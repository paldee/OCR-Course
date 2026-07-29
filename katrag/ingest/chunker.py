"""Chunker — heading-aware chunking พร้อม content sha256 และ curriculum version stamp (R9.6, R10.1).

หลักการ:
1. แบ่งข้อความจากหน้าเป็น chunk ตามขอบเขตหัวข้อ (heading boundary)
   หัวข้อตรวจจับจากรูปแบบที่พบในเอกสารหลักสูตร เช่น "หมวดวิชา...", "ปีที่ ...",
   เลขนำหน้าหัวข้อ (1.1, 2.3.1), "บทที่", "ภาคผนวก" เป็นต้น
2. ทุก chunk ต้องมี content_sha256 เป็น hex 64 อักขระ (R9.6)
3. ทุก chunk ต้องมี curriculum version ครบสามค่า: program, curriculum_year, edition_status (R10.1)
   chunk ที่ไม่มี curriculum version ครบจะถูกปฏิเสธจาก provenance_store
4. ขนาด chunk เป้าหมาย ~200-1000 ตัวอักษร หลีกเลี่ยง chunk เล็กเกินหรือใหญ่เกิน
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from katrag.common.hashing import sha256_text
from katrag.common.types import CurriculumVersion
from katrag.errors import VersionStampMissingError

# ── heading detection patterns ────────────────────────────────────────

# หมวดวิชา..., หมวดที่ ..., ส่วนที่ ...
_HEADING_CATEGORY = re.compile(
    r"^(?:หมวดวิชา|หมวดที่|ส่วนที่)\s*\S", re.UNICODE
)

# ปีที่ N / ปีการศึกษาที่ N / ภาคการศึกษาที่ N
_HEADING_YEAR_SEM = re.compile(
    r"^(?:ปีที่|ปีการศึกษาที่|ภาคการศึกษาที่|ภาคเรียนที่)\s*\d", re.UNICODE
)

# section numbers like "1.", "1.1", "2.3.1", etc.
# Must contain at least one dot OR be a single digit followed by text
# to avoid matching course codes (8-digit numbers like 06016481)
_HEADING_SECTION_NUM = re.compile(r"^(?:\d{1,2}\.\d+(?:\.\d+)*|\d{1,2}\.)\s+\S")

# บทที่ N, ภาคผนวก, ตารางที่, แผนการศึกษา
_HEADING_CHAPTER = re.compile(
    r"^(?:บทที่|ภาคผนวก|ตารางที่|แผนการศึกษา|โครงสร้างหลักสูตร|"
    r"คุณสมบัติ|เกณฑ์|ระบบ|อาจารย์|ข้อกำหนด|วัตถุประสงค์|ปรัชญา|"
    r"ความสำคัญ|วิสัยทัศน์|พันธกิจ)", re.UNICODE
)

# English section headings commonly found
_HEADING_ENGLISH = re.compile(
    r"^(?:Chapter|Section|Part|Table|Appendix|Program|Curriculum)\s", re.IGNORECASE
)

_HEADING_PATTERNS: tuple[re.Pattern[str], ...] = (
    _HEADING_CATEGORY,
    _HEADING_YEAR_SEM,
    _HEADING_SECTION_NUM,
    _HEADING_CHAPTER,
    _HEADING_ENGLISH,
)

# ── chunk size constraints ────────────────────────────────────────────

MIN_CHUNK_CHARS = 50
"""ต่ำกว่านี้จะพยายาม merge กับ chunk ถัดไปเพื่อไม่ให้เกิด chunk เล็กเกินไป."""

MAX_CHUNK_CHARS = 1200
"""สูงกว่านี้จะแบ่งที่ paragraph boundary หรือตำแหน่งคั่นใกล้เคียง."""

TARGET_CHUNK_CHARS = 600
"""ขนาดเป้าหมายสำหรับการตัดสินใจแบ่ง chunk ใหญ่."""


# ── data types ────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Chunk:
    """หนึ่ง chunk พร้อม metadata ครบถ้วน.

    - content_sha256: hex 64 อักขระ (R9.6)
    - program, curriculum_year, edition_status: curriculum version ครบสามค่า (R10.1)
    """

    content_text: str
    content_sha256: str
    document_id: str
    page_start: int
    page_end: int
    heading: str
    program: str
    curriculum_year: int
    edition_status: str

    def __post_init__(self) -> None:
        if len(self.content_sha256) != 64:
            raise ValueError("content_sha256 ต้องเป็น hex 64 อักขระ")
        if not self.document_id:
            raise ValueError("document_id ต้องไม่ว่าง")
        if self.page_start < 1:
            raise ValueError("page_start ต้องเป็นจำนวนเต็มตั้งแต่ 1")
        if self.page_end < self.page_start:
            raise ValueError("page_end ต้องไม่น้อยกว่า page_start")
        if not self.program:
            raise ValueError("program ต้องไม่ว่าง")

    @property
    def version(self) -> CurriculumVersion:
        """สร้าง CurriculumVersion จากค่าสามตัวที่ stamp ไว้."""
        return CurriculumVersion(
            program=self.program,
            curriculum_year=self.curriculum_year,
            edition_status=self.edition_status,  # type: ignore[arg-type]
        )

    @property
    def token_count(self) -> int:
        """ประมาณจำนวน token — ใช้จำนวนตัวอักษรเป็นตัวแทนเบื้องต้น."""
        return len(self.content_text)


# ── heading detection ─────────────────────────────────────────────────


def is_heading(line: str) -> bool:
    """ตรวจว่าบรรทัดนี้เป็นหัวข้อหรือไม่ ตามรูปแบบที่พบในเอกสารหลักสูตร."""
    stripped = line.strip()
    if not stripped:
        return False
    for pattern in _HEADING_PATTERNS:
        if pattern.match(stripped):
            return True
    return False


def extract_heading(line: str) -> str:
    """ดึงข้อความหัวข้อจากบรรทัด — ตัดให้สั้นพอเหมาะสำหรับเป็น label."""
    stripped = line.strip()
    # จำกัดความยาว heading ไม่เกิน 120 ตัวอักษร
    if len(stripped) > 120:
        return stripped[:120].rstrip()
    return stripped


# ── chunker ───────────────────────────────────────────────────────────


class Chunker:
    """สร้าง chunk แบบรู้หัวข้อ พร้อม content sha256 และ curriculum version stamp.

    การใช้งาน:
        chunker = Chunker(version)
        chunks = chunker.chunk_page(document_id, page_number, page_text)
        # หรือ chunk หลายหน้าพร้อมกัน
        chunks = chunker.chunk_pages(document_id, pages)
    """

    def __init__(self, version: CurriculumVersion) -> None:
        """สร้าง Chunker ที่ stamp curriculum version ให้ทุก chunk.

        Args:
            version: curriculum version ที่ต้องครบสามค่า (R10.1)

        Raises:
            VersionStampMissingError: ถ้า version ไม่ครบสามค่า
        """
        self._validate_version(version)
        self._version = version

    @property
    def version(self) -> CurriculumVersion:
        return self._version

    def chunk_page(
        self,
        document_id: str,
        page_number: int,
        page_text: str,
    ) -> tuple[Chunk, ...]:
        """แบ่ง text ของหน้าเดียวเป็น chunk.

        Args:
            document_id: รหัสเอกสาร (sha256)
            page_number: เลขหน้า (เริ่มจาก 1)
            page_text: ข้อความของหน้าที่ประกอบแล้ว (จาก Line_Assembler)

        Returns:
            tuple ของ Chunk ที่มี content_sha256 และ curriculum version ครบ
        """
        if not page_text.strip():
            return ()

        segments = self._split_by_headings(page_text)
        chunks = self._segments_to_chunks(segments, document_id, page_number, page_number)
        return tuple(chunks)

    def chunk_pages(
        self,
        document_id: str,
        pages: Sequence[tuple[int, str]],
    ) -> tuple[Chunk, ...]:
        """แบ่ง text จากหลายหน้าเป็น chunk.

        Args:
            document_id: รหัสเอกสาร
            pages: ลำดับของ (page_number, page_text) เรียงตามเลขหน้า

        Returns:
            tuple ของ Chunk ที่ครบ metadata
        """
        all_chunks: list[Chunk] = []
        for page_number, page_text in pages:
            all_chunks.extend(self.chunk_page(document_id, page_number, page_text))
        return tuple(all_chunks)

    # ── internal ──────────────────────────────────────────────────────

    def _split_by_headings(self, text: str) -> list[tuple[str, str]]:
        """แบ่งข้อความตาม heading boundary.

        Returns:
            list ของ (heading, content) pairs
            heading แรกอาจเป็นค่าว่างถ้าข้อความเริ่มโดยไม่มีหัวข้อ
        """
        lines = text.split("\n")
        segments: list[tuple[str, list[str]]] = []
        current_heading = ""
        current_lines: list[str] = []

        for line in lines:
            if is_heading(line):
                # บันทึก segment ก่อนหน้า (ถ้ามีเนื้อหา)
                if current_lines:
                    segments.append((current_heading, current_lines))
                current_heading = extract_heading(line)
                current_lines = [line]
            else:
                current_lines.append(line)

        # segment สุดท้าย
        if current_lines:
            segments.append((current_heading, current_lines))

        # รวม content ของแต่ละ segment
        return [(heading, "\n".join(content_lines)) for heading, content_lines in segments]

    def _segments_to_chunks(
        self,
        segments: list[tuple[str, str]],
        document_id: str,
        page_start: int,
        page_end: int,
    ) -> list[Chunk]:
        """แปลง segments เป็น chunks พร้อม merge/split ตามขนาด."""
        if not segments:
            return []

        chunks: list[Chunk] = []
        pending_heading = ""
        pending_text = ""

        for heading, content in segments:
            content_stripped = content.strip()
            if not content_stripped:
                continue

            # ถ้ามี pending content ให้รวมหรือปล่อยตามขนาด
            if pending_text:
                combined_len = len(pending_text) + len(content_stripped) + 1
                # ถ้ารวมแล้วยังไม่เกิน MAX → merge เมื่อ pending เล็กเกินไป
                if len(pending_text) < MIN_CHUNK_CHARS and combined_len <= MAX_CHUNK_CHARS:
                    # ใช้ heading ใหม่ถ้า pending ไม่มี heading
                    if not pending_heading and heading:
                        pending_heading = heading
                    pending_text = pending_text + "\n" + content_stripped
                    continue
                else:
                    # ปล่อย pending เป็น chunk (อาจต้อง split ถ้าใหญ่เกิน)
                    chunks.extend(
                        self._make_chunks(pending_heading, pending_text, document_id, page_start, page_end)
                    )
                    pending_heading = heading
                    pending_text = content_stripped
            else:
                pending_heading = heading
                pending_text = content_stripped

        # ปล่อย pending สุดท้าย
        if pending_text:
            chunks.extend(
                self._make_chunks(pending_heading, pending_text, document_id, page_start, page_end)
            )

        return chunks

    def _make_chunks(
        self,
        heading: str,
        text: str,
        document_id: str,
        page_start: int,
        page_end: int,
    ) -> list[Chunk]:
        """สร้าง chunk(s) จาก text — split ถ้าเกิน MAX_CHUNK_CHARS."""
        if len(text) <= MAX_CHUNK_CHARS:
            return [self._build_chunk(heading, text, document_id, page_start, page_end)]

        # Split text ที่ paragraph boundary หรือใกล้ TARGET_CHUNK_CHARS
        parts = self._split_large_text(text)
        return [
            self._build_chunk(heading, part, document_id, page_start, page_end)
            for part in parts
            if part.strip()
        ]

    def _split_large_text(self, text: str) -> list[str]:
        """แบ่ง text ใหญ่ที่ paragraph boundary ให้แต่ละส่วนไม่เกิน MAX_CHUNK_CHARS.

        ลำดับความสำคัญของจุดตัด:
        1. บรรทัดว่าง (paragraph break)
        2. ขึ้นบรรทัดใหม่ (line break)
        """
        parts: list[str] = []
        # ลอง split ที่ paragraph boundary ก่อน (บรรทัดว่าง)
        paragraphs = re.split(r"\n\s*\n", text)

        current = ""
        for para in paragraphs:
            para_stripped = para.strip()
            if not para_stripped:
                continue
            if not current:
                current = para_stripped
            elif len(current) + len(para_stripped) + 2 <= MAX_CHUNK_CHARS:
                current = current + "\n\n" + para_stripped
            else:
                # ปล่อย current (split ต่อถ้ายังใหญ่เกิน)
                parts.extend(self._split_by_lines(current))
                current = para_stripped

        if current:
            parts.extend(self._split_by_lines(current))

        return parts

    def _split_by_lines(self, text: str) -> list[str]:
        """Split text ตาม line boundary ถ้ายังเกิน MAX_CHUNK_CHARS."""
        if len(text) <= MAX_CHUNK_CHARS:
            return [text]

        lines = text.split("\n")
        parts: list[str] = []
        current = ""

        for line in lines:
            if not current:
                current = line
            elif len(current) + len(line) + 1 <= MAX_CHUNK_CHARS:
                current = current + "\n" + line
            else:
                if current.strip():
                    parts.append(current)
                current = line

        if current.strip():
            parts.append(current)

        # กรณีสุดท้าย: ถ้ายังมีส่วนที่เกิน MAX (เช่น บรรทัดเดียวที่ยาวมาก) ตัดตรง ๆ
        final: list[str] = []
        for part in parts:
            if len(part) <= MAX_CHUNK_CHARS:
                final.append(part)
            else:
                # hard split ทุก TARGET_CHUNK_CHARS ตัวอักษร
                for i in range(0, len(part), TARGET_CHUNK_CHARS):
                    segment = part[i:i + TARGET_CHUNK_CHARS]
                    if segment.strip():
                        final.append(segment)

        return final

    def _build_chunk(
        self,
        heading: str,
        text: str,
        document_id: str,
        page_start: int,
        page_end: int,
    ) -> Chunk:
        """สร้าง Chunk เดียว พร้อม content_sha256 และ curriculum version stamp."""
        content_sha256 = sha256_text(text)
        return Chunk(
            content_text=text,
            content_sha256=content_sha256,
            document_id=document_id,
            page_start=page_start,
            page_end=page_end,
            heading=heading,
            program=self._version.program,
            curriculum_year=self._version.curriculum_year,
            edition_status=self._version.edition_status,
        )

    @staticmethod
    def _validate_version(version: CurriculumVersion) -> None:
        """ตรวจว่า curriculum version ครบสามค่า (R10.1).

        Raises:
            VersionStampMissingError: ถ้าค่าใดค่าหนึ่งขาด
        """
        missing: list[str] = []
        if not version.program:
            missing.append("program")
        if not version.curriculum_year:
            missing.append("curriculum_year")
        if not version.edition_status:
            missing.append("edition_status")
        if missing:
            raise VersionStampMissingError(
                field_name="chunk",
                missing=tuple(missing),
            )
