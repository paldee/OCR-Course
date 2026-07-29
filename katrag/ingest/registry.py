"""Document_Registry — ตัวตนเอกสารและ metadata ของหลักสูตร (design §4.3, R1).

หลักการที่ชั้นนี้บังคับ

1. **identity คือ sha256 ของเนื้อหา** ไม่ใช่ชื่อไฟล์ — เพราะพบแล้วว่ามีสองไฟล์
   ที่ hash เท่ากันแต่วางในโฟลเดอร์ระดับปริญญาต่างกัน (R1.4, R1.5)
2. **metadata ตัดสินจากเนื้อหาเอกสารก่อน** ชื่อไฟล์/โฟลเดอร์เป็นเพียง hint
   ที่บันทึกไว้ และเมื่อขัดกันต้องใช้ค่าจากเนื้อหาแล้วสร้าง review issue (R1.6-R1.8)
3. **ขอบเขต dataset ต้องตรง** 14 เอกสาร 3,689 หน้า มิฉะนั้นจบงานด้วยสถานะ
   ไม่สำเร็จโดยคงสิ่งที่บันทึกไว้แล้ว (R1.2, R1.3)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from katrag.common.hashing import sha256_file, sha256_text
from katrag.common.normalize import match_key, normalize_for_compare
from katrag.common.types import BBox, CurriculumVersion, DegreeLevel, EditionStatus
from katrag.config import KatragConfig
from katrag.errors import DatasetScopeError, ReviewIssue
from katrag.store.provenance_store import DocumentRow, ProvenanceStore

#: จำนวนหน้าแรกที่ใช้ค้นหา metadata ของหลักสูตร (หน้าปกและหน้าข้อมูลหลักสูตร)
METADATA_SCAN_PAGES = 8

#: field ของ metadata ที่ต้องตัดสินให้ได้ทุกเอกสาร
METADATA_FIELDS: tuple[str, ...] = ("program", "curriculum_year", "degree_level", "edition_status")

#: ข้อความในเอกสารที่บ่งชี้ระดับปริญญา (R1.6) — ตรวจจากเนื้อหา ไม่ใช่ชื่อโฟลเดอร์
DEGREE_PATTERNS: tuple[tuple[str, DegreeLevel], ...] = (
    ("ปรัชญาดุษฎีบัณฑิต", "doctoral"),
    ("ดุษฎีบัณฑิต", "doctoral"),
    ("doctor of philosophy", "doctoral"),
    ("วิทยาศาสตรมหาบัณฑิต", "master"),
    ("มหาบัณฑิต", "master"),
    ("master of science", "master"),
    ("วิทยาศาสตรบัณฑิต", "bachelor"),
    ("bachelor of science", "bachelor"),
)

#: ชื่อสาขาในเอกสาร -> รหัส program (R1.6)
PROGRAM_PATTERNS: tuple[tuple[str, str], ...] = (
    ("ปัญญาประดิษฐ์เพื่อการวิเคราะห์เชิงธุรกิจ", "AITBA"),
    ("เทคโนโลยีปัญญาประดิษฐ์", "AIT"),
    ("วิทยาการข้อมูลและการวิเคราะห์เชิงธุรกิจ", "DSBA"),
    ("เทคโนโลยีสารสนเทศทางธุรกิจ", "BIT"),
    ("เทคโนโลยีสารสนเทศ", "IT"),
    ("information technology", "IT"),
)

#: hint จากชื่อไฟล์ (ใช้เมื่อเนื้อหาไม่บอก และใช้ตรวจความขัดแย้ง)
_FILENAME_PROGRAM_PREFIXES: tuple[tuple[str, str, DegreeLevel], ...] = (
    ("PH_D_AITBA", "AITBA", "doctoral"),
    ("PH_D_IT", "IT", "doctoral"),
    ("M_AITBA", "AITBA", "master"),
    ("M_IT", "IT", "master"),
    ("AIT", "AIT", "bachelor"),
    ("BIT", "BIT", "bachelor"),
    ("DSBA", "DSBA", "bachelor"),
    ("IT", "IT", "bachelor"),
)

_FOLDER_DEGREE: Mapping[str, DegreeLevel] = {
    "Bachelors_Degree": "bachelor",
    "Masters_Degree": "master",
    "Doctorals_Degree": "doctoral",
}

_YEAR_RE = re.compile(r"(25[0-9]{2}|26[0-9]{2})")
_FILENAME_RE = re.compile(r"^(?P<prefix>[A-Za-z_]+?)(?P<year>25\d{2}|26\d{2})_(?P<edition>old|current)$")


@dataclass(frozen=True, slots=True)
class MetadataValue:
    """ค่าหนึ่งค่าพร้อมที่มา (R1.6, R1.7)."""

    value: Any
    source: str  # 'document_text' | 'filename'
    page: int | None = None
    bbox: BBox | None = None
    evidence_text: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "source": self.source,
            "page": self.page,
            "bbox": list(self.bbox.as_tuple()) if self.bbox else None,
            "evidence_text": self.evidence_text[:120],
        }


@dataclass(frozen=True, slots=True)
class DocumentCandidate:
    """เอกสารหนึ่งไฟล์ที่สแกนพบ พร้อมค่าที่ตัดสินได้แล้ว."""

    document_id: str
    path: Path
    relative_path: str
    sha256: str
    size_bytes: int
    page_count: int
    folder_hint: str
    filename_stem: str
    metadata: Mapping[str, MetadataValue]
    canonical_document_id: str
    duplicate_group: tuple[str, ...] = ()

    @property
    def program(self) -> str:
        return str(self.metadata["program"].value)

    @property
    def curriculum_year(self) -> int:
        return int(self.metadata["curriculum_year"].value)

    @property
    def degree_level(self) -> DegreeLevel:
        return self.metadata["degree_level"].value  # type: ignore[return-value]

    @property
    def edition_status(self) -> EditionStatus:
        return self.metadata["edition_status"].value  # type: ignore[return-value]

    def version(self) -> CurriculumVersion:
        return CurriculumVersion(
            program=self.program,
            curriculum_year=self.curriculum_year,
            edition_status=self.edition_status,
        )

    def metadata_source_json(self) -> dict[str, Any]:
        return {name: value.to_json() for name, value in sorted(self.metadata.items())}


@dataclass(slots=True)
class RegistrationResult:
    """ผลการลงทะเบียนทั้ง dataset."""

    documents: list[DocumentCandidate] = field(default_factory=list)
    review_issues: list[ReviewIssue] = field(default_factory=list)
    unreadable_paths: list[str] = field(default_factory=list)
    scope_ok: bool = True

    @property
    def document_count(self) -> int:
        return len(self.documents)

    @property
    def page_total(self) -> int:
        return sum(doc.page_count for doc in self.documents)


class DocumentRegistry:
    """สแกนคลังเอกสาร ตัดสิน metadata และลงทะเบียนลง Provenance_Store."""

    def __init__(self, config: KatragConfig, store: ProvenanceStore) -> None:
        self._config = config
        self._store = store

    # ── public API ───────────────────────────────────────────────────

    def scan(self, corpus_root: Path | None = None) -> RegistrationResult:
        """สแกนไฟล์ PDF ทั้งหมด คำนวณ hash และตัดสิน metadata (ยังไม่เขียน store)."""
        root = corpus_root or self._config.dataset_root
        result = RegistrationResult()
        if not root.is_dir():
            raise DatasetScopeError("ไม่พบไดเรกทอรีคลังเอกสาร", path=str(root))

        candidates: list[DocumentCandidate] = []
        for pdf_path in sorted(root.rglob("*.pdf"), key=lambda p: p.relative_to(root).as_posix()):
            relative = pdf_path.relative_to(root).as_posix()
            try:
                candidate = self._build_candidate(pdf_path, relative, result)
            except Exception as exc:  # เอกสารเปิดไม่ได้ -> ข้ามไปเอกสารถัดไป (R2.5)
                result.unreadable_paths.append(relative)
                self._store.record_error(
                    scope="document",
                    error_kind="pdf_document_unreadable",
                    message=f"{type(exc).__name__}: {exc}",
                )
                continue
            candidates.append(candidate)

        candidates = self._resolve_duplicates(candidates, result)
        result.documents = candidates
        self._check_scope(result)
        return result

    def register(self, result: RegistrationResult) -> None:
        """เขียน document record, curriculum_version, ความสัมพันธ์ และ review issue.

        ลำดับสำคัญ: document ต้องมีก่อน review issue และ document_relation
        เพราะทั้งสองตารางมี foreign key ชี้ไป document (R9.1 บังคับ FK จริง)
        """
        for doc in result.documents:
            version = doc.version()
            version_id = self._store.upsert_version(
                version, sha256_text("|".join(str(part) for part in version.key()))
            )
            self._store.insert_document(
                DocumentRow(
                    document_id=doc.document_id,
                    relative_path=doc.relative_path,
                    sha256=doc.sha256,
                    size_bytes=doc.size_bytes,
                    page_count=doc.page_count,
                    degree_level=doc.degree_level,
                    version_id=version_id,
                    canonical_document_id=doc.canonical_document_id,
                    metadata_source=doc.metadata_source_json(),
                )
            )

        # ความสัมพันธ์ duplicate ต้องเขียนหลังจากทุก document มีอยู่แล้ว (FK)
        for doc in result.documents:
            for other_id in doc.duplicate_group:
                if other_id == doc.document_id:
                    continue
                self._store.insert_document_relation(
                    doc.document_id,
                    other_id,
                    "duplicate_content",
                    "sha256 ของเนื้อหาเท่ากัน",
                )

        # review issue เขียนท้ายสุด เพราะบางรายการอ้าง document_id (FK)
        for issue in result.review_issues:
            self._store.record_review_issue(issue)

    # ── candidate construction ───────────────────────────────────────

    def _build_candidate(
        self, pdf_path: Path, relative: str, result: RegistrationResult
    ) -> DocumentCandidate:
        import fitz  # นำเข้าเฉพาะเมื่อใช้ เพื่อให้ import package ไม่ต้องพึ่ง PyMuPDF

        digest = sha256_file(pdf_path)
        size_bytes = pdf_path.stat().st_size
        with fitz.open(pdf_path) as pdf:
            page_count = pdf.page_count
            if page_count < 1:
                raise ValueError("เอกสารไม่มีหน้า")
            head_text = self._read_head_text(pdf, page_count)

        folder_hint = Path(relative).parent.as_posix()
        stem = pdf_path.stem
        # document_id ผูกกับ "ไฟล์" (relative path) ไม่ใช่ "เนื้อหา"
        # เพราะสองไฟล์อาจมี sha256 เท่ากันได้ (พบจริงในคลังนี้) ส่วน identity
        # ของเนื้อหาอยู่ในคอลัมน์ sha256 และความสัมพันธ์ duplicate_content
        document_id = sha256_text(relative)[:16]
        metadata = self._resolve_metadata(
            head_text=head_text,
            folder_hint=folder_hint,
            stem=stem,
            relative=relative,
            document_id=document_id,
            result=result,
        )
        return DocumentCandidate(
            document_id=document_id,
            path=pdf_path,
            relative_path=relative,
            sha256=digest,
            size_bytes=size_bytes,
            page_count=page_count,
            folder_hint=folder_hint,
            filename_stem=stem,
            metadata=metadata,
            canonical_document_id=document_id,
        )

    @staticmethod
    def _read_head_text(pdf: Any, page_count: int) -> list[tuple[int, str, BBox]]:
        """อ่านข้อความของหน้าแรก ๆ พร้อม bbox ของบล็อก เพื่อใช้เป็นหลักฐาน metadata."""
        out: list[tuple[int, str, BBox]] = []
        limit = min(METADATA_SCAN_PAGES, page_count)
        for index in range(limit):
            page = pdf[index]
            for block in page.get_text("blocks"):
                x0, y0, x1, y1, text = block[0], block[1], block[2], block[3], block[4]
                if not isinstance(text, str) or not text.strip():
                    continue
                if x1 <= x0 or y1 <= y0:
                    continue
                out.append((index + 1, text, BBox(x0, y0, x1, y1)))
        return out

    # ── metadata resolution ──────────────────────────────────────────

    def _resolve_metadata(
        self,
        *,
        head_text: Sequence[tuple[int, str, BBox]],
        folder_hint: str,
        stem: str,
        relative: str,
        document_id: str,
        result: RegistrationResult,
    ) -> dict[str, MetadataValue]:
        filename_hint = self._filename_hint(stem, folder_hint)
        resolved: dict[str, MetadataValue] = {}

        from_text = {
            "program": self._find_program(head_text),
            "degree_level": self._find_degree(head_text),
            "curriculum_year": self._find_year(head_text),
        }

        for name in ("program", "degree_level", "curriculum_year"):
            found = from_text[name]
            hint_value = filename_hint.get(name)
            if found is not None:
                resolved[name] = found
                if hint_value is not None and hint_value != found.value:
                    # เนื้อหาชนะ hint แต่ต้องบันทึกความขัดแย้ง (R1.8)
                    result.review_issues.append(
                        ReviewIssue(
                            kind="metadata_conflict",
                            document_id=document_id,
                            page=found.page,
                            detail={
                                "field": name,
                                "from_document_text": found.value,
                                "from_filename": hint_value,
                                "relative_path": relative,
                                "page": found.page,
                                "bbox": list(found.bbox.as_tuple()) if found.bbox else None,
                            },
                        )
                    )
            elif hint_value is not None:
                resolved[name] = MetadataValue(value=hint_value, source="filename")
                result.review_issues.append(
                    ReviewIssue(
                        kind="metadata_unresolved",
                        document_id=document_id,
                        detail={"field": name, "relative_path": relative, "fallback": hint_value},
                    )
                )
            else:
                raise ValueError(f"ตัดสิน metadata '{name}' ไม่ได้จากทั้งเนื้อหาและชื่อไฟล์")

        # edition_status มาจากชื่อไฟล์เท่านั้นตามรูปแบบที่คลังนี้ใช้ (old/current)
        edition = filename_hint.get("edition_status")
        if edition is None:
            raise ValueError("ตัดสิน edition_status ไม่ได้")
        resolved["edition_status"] = MetadataValue(value=edition, source="filename")
        result.review_issues.append(
            ReviewIssue(
                kind="metadata_unresolved",
                document_id=document_id,
                detail={
                    "field": "edition_status",
                    "relative_path": relative,
                    "fallback": edition,
                    "note": "คลังนี้ระบุฉบับด้วยชื่อไฟล์ ไม่มีข้อความในเล่มที่ระบุตรง ๆ",
                },
            )
        )
        return resolved

    @staticmethod
    def _filename_hint(stem: str, folder_hint: str) -> dict[str, Any]:
        """ดึง hint จากชื่อไฟล์และโฟลเดอร์ (บันทึกไว้ แต่ไม่มีน้ำหนักเหนือเนื้อหา)."""
        hint: dict[str, Any] = {}
        match = _FILENAME_RE.match(stem)
        if match:
            hint["curriculum_year"] = int(match.group("year"))
            hint["edition_status"] = match.group("edition")
            prefix = match.group("prefix").rstrip("_")
            for candidate_prefix, program, degree in _FILENAME_PROGRAM_PREFIXES:
                if prefix.upper() == candidate_prefix:
                    hint["program"] = program
                    hint["degree_level"] = degree
                    break
        folder_degree = _FOLDER_DEGREE.get(Path(folder_hint).name)
        if folder_degree is not None:
            hint.setdefault("degree_level", folder_degree)
        return hint

    @staticmethod
    def _strip_faculty_mentions(text: str) -> str:
        """ตัดชื่อ "คณะ..." ออกก่อนหาชื่อสาขา.

        จำเป็นเพราะหน้าปกของทุกเล่มมีชื่อคณะ "คณะเทคโนโลยีสารสนเทศ" ซึ่งมีคำ
        เดียวกับชื่อสาขา IT ถ้าไม่ตัดออก เอกสารของ DSBA/BIT/AIT จะถูกตัดสิน
        เป็น IT ทั้งหมดจากชื่อคณะ (พบจริงตอนรันกับเอกสารชุดนี้)
        """
        cleaned = text
        for needle, _ in PROGRAM_PATTERNS:
            faculty = match_key(f"คณะ{needle}")
            cleaned = cleaned.replace(faculty, " ")
        return cleaned

    @classmethod
    def _find_program(cls, head_text: Iterable[tuple[int, str, BBox]]) -> MetadataValue | None:
        """หาชื่อสาขาจากเนื้อหา โดยให้บล็อกที่มีบริบท "หลักสูตร/สาขาวิชา" มาก่อน.

        การจับคู่ใช้ `match_key` ซึ่งลบ combining mark ทั้งสองฝ่าย เพราะ text layer
        ของบางหน้าไม่มีสระ/วรรณยุกต์ (ดู `strip_combining_marks`)

        สองรอบ: รอบแรกดูเฉพาะบล็อกที่มีบริบทของหลักสูตร รอบสองจึงยอมรับบล็อกอื่น
        ทำให้ชื่อคณะหรือชื่อสถาบันไม่ชนะชื่อสาขาที่แท้จริง
        """
        blocks = list(head_text)
        context_keys = (match_key("หลักสูตร"), match_key("สาขาวิชา"))
        for context_required in (True, False):
            for page, text, bbox in blocks:
                key = match_key(text)
                if context_required and not any(marker in key for marker in context_keys):
                    continue
                haystack = cls._strip_faculty_mentions(key)
                for needle, program in PROGRAM_PATTERNS:
                    if match_key(needle) in haystack:
                        return MetadataValue(
                            value=program,
                            source="document_text",
                            page=page,
                            bbox=bbox,
                            evidence_text=text.strip(),
                        )
        return None

    @staticmethod
    def _find_degree(head_text: Iterable[tuple[int, str, BBox]]) -> MetadataValue | None:
        for page, text, bbox in head_text:
            haystack = match_key(text)
            for needle, degree in DEGREE_PATTERNS:
                if match_key(needle) in haystack:
                    return MetadataValue(
                        value=degree,
                        source="document_text",
                        page=page,
                        bbox=bbox,
                        evidence_text=text.strip(),
                    )
        return None

    @staticmethod
    def _find_year(head_text: Iterable[tuple[int, str, BBox]]) -> MetadataValue | None:
        for page, text, bbox in head_text:
            normalized = normalize_for_compare(text)
            if "หลักสูตร" not in normalized and "พ.ศ." not in normalized:
                continue
            match = _YEAR_RE.search(normalized)
            if match:
                return MetadataValue(
                    value=int(match.group(1)),
                    source="document_text",
                    page=page,
                    bbox=bbox,
                    evidence_text=text.strip(),
                )
        return None

    # ── duplicates and scope ─────────────────────────────────────────

    @staticmethod
    def _resolve_duplicates(
        candidates: list[DocumentCandidate], result: RegistrationResult
    ) -> list[DocumentCandidate]:
        """จัดกลุ่มเอกสารที่ sha256 เท่ากัน และเลือก canonical จาก relative path น้อยสุด (R1.4)."""
        groups: dict[str, list[DocumentCandidate]] = {}
        for candidate in candidates:
            groups.setdefault(candidate.sha256, []).append(candidate)

        out: list[DocumentCandidate] = []
        for digest, group in groups.items():
            if len(group) == 1:
                out.append(group[0])
                continue
            ordered = sorted(group, key=lambda c: c.relative_path)
            canonical = ordered[0]
            member_ids = tuple(item.document_id for item in ordered)
            result.review_issues.append(
                ReviewIssue(
                    kind="duplicate_content",
                    document_id=canonical.document_id,
                    detail={
                        "sha256": digest,
                        "canonical_document_id": canonical.document_id,
                        "canonical_relative_path": canonical.relative_path,
                        "members": [
                            {
                                "document_id": item.document_id,
                                "relative_path": item.relative_path,
                                "degree_level_resolved": item.degree_level,
                            }
                            for item in ordered
                        ],
                        "note": "เนื้อหาเหมือนกันแต่วางในโฟลเดอร์ต่างกัน ประมวลผลเนื้อหาครั้งเดียวจาก canonical",
                    },
                )
            )
            for item in ordered:
                out.append(
                    DocumentCandidate(
                        document_id=item.document_id,
                        path=item.path,
                        relative_path=item.relative_path,
                        sha256=item.sha256,
                        size_bytes=item.size_bytes,
                        page_count=item.page_count,
                        folder_hint=item.folder_hint,
                        filename_stem=item.filename_stem,
                        metadata=item.metadata,
                        canonical_document_id=canonical.document_id,
                        duplicate_group=member_ids,
                    )
                )
        out.sort(key=lambda c: c.relative_path)
        return out

    def _check_scope(self, result: RegistrationResult) -> None:
        """ตรวจ 14 เอกสาร / 3,689 หน้า (R1.2, R1.3)."""
        expected_docs = self._config.dataset.expected_document_count
        expected_pages = self._config.dataset.expected_page_total
        actual_docs = result.document_count
        actual_pages = result.page_total
        if actual_docs == expected_docs and actual_pages == expected_pages:
            result.scope_ok = True
            return
        result.scope_ok = False
        result.review_issues.append(
            ReviewIssue(
                kind="dataset_scope_mismatch",
                detail={
                    "expected_document_count": expected_docs,
                    "actual_document_count": actual_docs,
                    "expected_page_total": expected_pages,
                    "actual_page_total": actual_pages,
                    "unreadable_paths": list(result.unreadable_paths),
                },
            )
        )

    # ── document identity for canonical processing ───────────────────

    @staticmethod
    def canonical_documents(documents: Iterable[DocumentCandidate]) -> list[DocumentCandidate]:
        """คืนเฉพาะ canonical document — เนื้อหาถูกประมวลผลครั้งเดียวต่อกลุ่ม (R1.4)."""
        return [doc for doc in documents if doc.document_id == doc.canonical_document_id]
