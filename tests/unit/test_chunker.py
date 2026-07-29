"""Unit tests for katrag.ingest.chunker (R9.6, R10.1)."""

from __future__ import annotations

import pytest

from katrag.common.hashing import is_sha256_hex, sha256_text
from katrag.common.types import CurriculumVersion
from katrag.errors import VersionStampMissingError
from katrag.ingest.chunker import (
    MAX_CHUNK_CHARS,
    MIN_CHUNK_CHARS,
    Chunk,
    Chunker,
    extract_heading,
    is_heading,
)


# ── Heading detection ─────────────────────────────────────────────────


class TestIsHeading:
    """ตรวจจับหัวข้อจากรูปแบบที่พบในเอกสารหลักสูตร."""

    def test_category_heading(self) -> None:
        assert is_heading("หมวดวิชาศึกษาทั่วไป")

    def test_category_prefix(self) -> None:
        assert is_heading("หมวดที่ 1 วิชาบังคับ")

    def test_year_heading(self) -> None:
        assert is_heading("ปีที่ 1 ภาคการศึกษาที่ 1")

    def test_semester_heading(self) -> None:
        assert is_heading("ภาคการศึกษาที่ 2")

    def test_section_number(self) -> None:
        assert is_heading("1.1 โครงสร้างหลักสูตร")

    def test_nested_section(self) -> None:
        assert is_heading("2.3.1 วิชาบังคับ")

    def test_chapter(self) -> None:
        assert is_heading("บทที่ 1")

    def test_appendix(self) -> None:
        assert is_heading("ภาคผนวก ก")

    def test_english_section(self) -> None:
        assert is_heading("Chapter 1 Introduction")

    def test_criteria_heading(self) -> None:
        assert is_heading("เกณฑ์การสำเร็จการศึกษา")

    def test_not_heading_empty(self) -> None:
        assert not is_heading("")
        assert not is_heading("   ")

    def test_not_heading_regular_text(self) -> None:
        assert not is_heading("รหัสวิชา 06016123 คณิตศาสตร์")

    def test_not_heading_course_line(self) -> None:
        assert not is_heading("06016481 วิชาเลือก 3(3-0-6)")


class TestExtractHeading:
    """ดึงข้อความหัวข้อที่เหมาะกับเป็น label."""

    def test_normal(self) -> None:
        assert extract_heading("หมวดวิชาศึกษาทั่วไป") == "หมวดวิชาศึกษาทั่วไป"

    def test_strips_whitespace(self) -> None:
        assert extract_heading("  1.1 หัวข้อ  ") == "1.1 หัวข้อ"

    def test_truncates_long(self) -> None:
        long_text = "A" * 200
        result = extract_heading(long_text)
        assert len(result) <= 120


# ── Chunker creation ──────────────────────────────────────────────────


class TestChunkerCreation:
    """R10.1: curriculum version ต้องครบสามค่า."""

    def test_valid_version(self) -> None:
        version = CurriculumVersion(program="IT", curriculum_year=2565, edition_status="current")
        chunker = Chunker(version)
        assert chunker.version == version

    def test_version_old(self) -> None:
        version = CurriculumVersion(program="BIT", curriculum_year=2560, edition_status="old")
        chunker = Chunker(version)
        assert chunker.version.edition_status == "old"


# ── Chunk output ──────────────────────────────────────────────────────


class TestChunkPage:
    """ทดสอบการสร้าง chunk จากหน้าเดียว."""

    @pytest.fixture
    def chunker(self) -> Chunker:
        version = CurriculumVersion(program="IT", curriculum_year=2565, edition_status="current")
        return Chunker(version)

    def test_empty_text_returns_no_chunks(self, chunker: Chunker) -> None:
        assert chunker.chunk_page("doc1", 1, "") == ()
        assert chunker.chunk_page("doc1", 1, "   ") == ()

    def test_single_paragraph(self, chunker: Chunker) -> None:
        text = "ข้อความปกติที่ไม่มีหัวข้อ เป็นเนื้อหาของหน้า"
        chunks = chunker.chunk_page("doc1", 5, text)
        assert len(chunks) == 1
        assert chunks[0].content_text == text
        assert chunks[0].page_start == 5
        assert chunks[0].page_end == 5

    def test_content_sha256_is_valid(self, chunker: Chunker) -> None:
        """R9.6: content sha256 เป็น hex 64 อักขระ."""
        chunks = chunker.chunk_page("doc1", 1, "ทดสอบ content hash")
        assert len(chunks) == 1
        assert is_sha256_hex(chunks[0].content_sha256)

    def test_content_sha256_matches_text(self, chunker: Chunker) -> None:
        """R9.6: sha256 คำนวณจากเนื้อหาจริง."""
        text = "ข้อความสำหรับทดสอบ"
        chunks = chunker.chunk_page("doc1", 1, text)
        expected_hash = sha256_text(text)
        assert chunks[0].content_sha256 == expected_hash

    def test_content_sha256_deterministic(self, chunker: Chunker) -> None:
        """R9.6: คำนวณซ้ำได้ค่า hash เท่าเดิมทุกครั้ง."""
        text = "deterministic test ข้อความเดิม"
        c1 = chunker.chunk_page("d", 1, text)
        c2 = chunker.chunk_page("d", 1, text)
        assert c1[0].content_sha256 == c2[0].content_sha256

    def test_curriculum_version_stamped(self, chunker: Chunker) -> None:
        """R10.1: ทุก chunk ต้องมี curriculum version ครบสามค่า."""
        text = "เนื้อหาที่ต้องมี version stamp"
        chunks = chunker.chunk_page("doc1", 1, text)
        for chunk in chunks:
            assert chunk.program == "IT"
            assert chunk.curriculum_year == 2565
            assert chunk.edition_status == "current"

    def test_version_property(self, chunker: Chunker) -> None:
        """Chunk.version สร้าง CurriculumVersion ได้ถูกต้อง."""
        chunks = chunker.chunk_page("doc1", 1, "test")
        assert chunks[0].version == chunker.version

    def test_heading_detected(self, chunker: Chunker) -> None:
        """chunk ที่เริ่มต้นด้วยหัวข้อต้องมี heading."""
        text = "หมวดวิชาศึกษาทั่วไป\nเนื้อหาของหมวดวิชา"
        chunks = chunker.chunk_page("doc1", 1, text)
        assert chunks[0].heading == "หมวดวิชาศึกษาทั่วไป"

    def test_multiple_headings_split(self, chunker: Chunker) -> None:
        """ข้อความที่มีหลายหัวข้อถูกแบ่งเป็นหลาย chunk."""
        text = (
            "หมวดวิชาศึกษาทั่วไป\n"
            + "เนื้อหา " * 40 + "\n\n"
            + "หมวดวิชาเฉพาะ\n"
            + "เนื้อหาเฉพาะ " * 40
        )
        chunks = chunker.chunk_page("doc1", 1, text)
        assert len(chunks) >= 2
        # ทุก chunk ต้องมี version stamp
        for chunk in chunks:
            assert chunk.program == "IT"
            assert is_sha256_hex(chunk.content_sha256)


class TestChunkPages:
    """ทดสอบ chunk_pages (หลายหน้า)."""

    @pytest.fixture
    def chunker(self) -> Chunker:
        version = CurriculumVersion(program="DSBA", curriculum_year=2565, edition_status="current")
        return Chunker(version)

    def test_multiple_pages(self, chunker: Chunker) -> None:
        pages = [
            (1, "หมวดวิชาบังคับ\nรายวิชาบังคับทั้งหมด"),
            (2, "หมวดวิชาเลือก\nรายวิชาเลือกทั้งหมด"),
        ]
        chunks = chunker.chunk_pages("doc1", pages)
        assert len(chunks) >= 2
        # ทุก chunk มี version
        for chunk in chunks:
            assert chunk.program == "DSBA"
            assert chunk.curriculum_year == 2565
            assert is_sha256_hex(chunk.content_sha256)

    def test_empty_pages_skipped(self, chunker: Chunker) -> None:
        pages = [(1, ""), (2, "มีเนื้อหา"), (3, "   ")]
        chunks = chunker.chunk_pages("doc1", pages)
        assert len(chunks) == 1
        assert chunks[0].page_start == 2


class TestChunkSplitting:
    """ทดสอบการแบ่ง chunk ขนาดใหญ่."""

    @pytest.fixture
    def chunker(self) -> Chunker:
        version = CurriculumVersion(program="AIT", curriculum_year=2566, edition_status="current")
        return Chunker(version)

    def test_large_text_split(self, chunker: Chunker) -> None:
        """ข้อความที่เกิน MAX_CHUNK_CHARS ถูกแบ่ง."""
        large_text = "เนื้อหายาว " * 250  # ~2750 chars
        chunks = chunker.chunk_page("doc1", 1, large_text)
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk.content_text) <= MAX_CHUNK_CHARS + 50
            assert is_sha256_hex(chunk.content_sha256)
            assert chunk.program == "AIT"

    def test_medium_text_stays_single(self, chunker: Chunker) -> None:
        """ข้อความที่ไม่เกิน MAX_CHUNK_CHARS ไม่ถูกแบ่ง."""
        medium_text = "ข้อความปกติ " * 50  # ~600 chars
        chunks = chunker.chunk_page("doc1", 1, medium_text)
        assert len(chunks) == 1


class TestChunkDataclass:
    """ทดสอบ Chunk dataclass validation."""

    def test_invalid_sha256_length(self) -> None:
        with pytest.raises(ValueError, match="content_sha256"):
            Chunk(
                content_text="test",
                content_sha256="abc",
                document_id="doc1",
                page_start=1,
                page_end=1,
                heading="",
                program="IT",
                curriculum_year=2565,
                edition_status="current",
            )

    def test_empty_document_id(self) -> None:
        with pytest.raises(ValueError, match="document_id"):
            Chunk(
                content_text="test",
                content_sha256="a" * 64,
                document_id="",
                page_start=1,
                page_end=1,
                heading="",
                program="IT",
                curriculum_year=2565,
                edition_status="current",
            )

    def test_invalid_page_start(self) -> None:
        with pytest.raises(ValueError, match="page_start"):
            Chunk(
                content_text="test",
                content_sha256="a" * 64,
                document_id="doc1",
                page_start=0,
                page_end=1,
                heading="",
                program="IT",
                curriculum_year=2565,
                edition_status="current",
            )

    def test_page_end_before_start(self) -> None:
        with pytest.raises(ValueError, match="page_end"):
            Chunk(
                content_text="test",
                content_sha256="a" * 64,
                document_id="doc1",
                page_start=5,
                page_end=3,
                heading="",
                program="IT",
                curriculum_year=2565,
                edition_status="current",
            )

    def test_empty_program(self) -> None:
        with pytest.raises(ValueError, match="program"):
            Chunk(
                content_text="test",
                content_sha256="a" * 64,
                document_id="doc1",
                page_start=1,
                page_end=1,
                heading="",
                program="",
                curriculum_year=2565,
                edition_status="current",
            )
