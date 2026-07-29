"""Unit tests for katrag.index.lexical (R13.1, R13.12).

ทดสอบการสร้างดัชนี FTS5 BM25 จาก chunk:
- จำนวน entry เท่ากับจำนวน chunk
- ทุก entry อ้าง chunk_id กับ curriculum version ได้
- index_build_incomplete เมื่อบาง chunk ล้มเหลว
- search กับ version filter
"""

from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest

from katrag.common.hashing import sha256_text
from katrag.common.types import CurriculumVersion
from katrag.index.lexical import (
    LexicalHit,
    build_index,
    rebuild_index,
    search,
    _escape_fts5_query,
)
from katrag.ingest.chunker import Chunk
from katrag.store.integrity import apply_schema, connect


# ── fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def db_conn() -> sqlite3.Connection:
    """In-memory SQLite connection with full schema applied."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_schema(conn)
    return conn


@pytest.fixture
def version_it() -> CurriculumVersion:
    return CurriculumVersion(program="IT", curriculum_year=2565, edition_status="current")


@pytest.fixture
def version_dsba() -> CurriculumVersion:
    return CurriculumVersion(program="DSBA", curriculum_year=2566, edition_status="current")


def _seed_document_and_page(
    conn: sqlite3.Connection,
    document_id: str,
    version_id: int,
    pages: list[int] | None = None,
) -> None:
    """สร้าง document และ page ขั้นต่ำสำหรับ FK constraint."""
    if pages is None:
        pages = [1]

    conn.execute(
        """INSERT OR IGNORE INTO document
           (document_id, relative_path, sha256, size_bytes, page_count,
            degree_level, version_id, canonical_document_id, metadata_source_json, ingested_at)
           VALUES (?, ?, ?, 1000, ?, 'bachelor', ?, ?, '{}', '2024-01-01T00:00:00')""",
        (document_id, f"path/{document_id}.pdf", "a" * 64, len(pages), version_id, document_id),
    )

    for page_num in pages:
        conn.execute(
            """INSERT OR IGNORE INTO page
               (document_id, page_number, width_pt, height_pt, char_count,
                image_count, page_text, extraction_method, page_sha256, status)
               VALUES (?, ?, 595.0, 842.0, 500, 0, 'test', 'text_layer', ?, 'page_complete')""",
            (document_id, page_num, f"{page_num:064d}"),
        )


def _seed_version(conn: sqlite3.Connection, version: CurriculumVersion) -> int:
    """สร้าง curriculum_version entry — คืน version_id."""
    from katrag.common.hashing import sha256_parts

    version_sha = sha256_parts([version.program, version.curriculum_year, version.edition_status])
    cur = conn.execute(
        """INSERT OR IGNORE INTO curriculum_version
           (program, curriculum_year, edition_status, version_sha256)
           VALUES (?, ?, ?, ?)""",
        (version.program, version.curriculum_year, version.edition_status, version_sha),
    )
    if cur.lastrowid:
        return cur.lastrowid

    row = conn.execute(
        "SELECT version_id FROM curriculum_version WHERE program=? AND curriculum_year=? AND edition_status=?",
        (version.program, version.curriculum_year, version.edition_status),
    ).fetchone()
    return int(row["version_id"])


def _make_chunk(
    text: str,
    document_id: str = "doc1",
    page: int = 1,
    heading: str = "",
    program: str = "IT",
    curriculum_year: int = 2565,
    edition_status: str = "current",
) -> Chunk:
    """สร้าง Chunk สำหรับทดสอบ."""
    return Chunk(
        content_text=text,
        content_sha256=sha256_text(text),
        document_id=document_id,
        page_start=page,
        page_end=page,
        heading=heading,
        program=program,
        curriculum_year=curriculum_year,
        edition_status=edition_status,
    )


# ── build_index tests ─────────────────────────────────────────────────


class TestBuildIndex:
    """ทดสอบการสร้างดัชนี FTS5."""

    def test_empty_chunks(self, db_conn: sqlite3.Connection) -> None:
        """ไม่มี chunk → ไม่มี entry ไม่มี issue."""
        count, issues = build_index(db_conn, [])
        assert count == 0
        assert issues == []

    def test_single_chunk_indexed(
        self, db_conn: sqlite3.Connection, version_it: CurriculumVersion
    ) -> None:
        """chunk หนึ่งตัวถูก index สำเร็จ — จำนวน entry = 1."""
        vid = _seed_version(db_conn, version_it)
        _seed_document_and_page(db_conn, "doc1", vid)

        chunk = _make_chunk("หมวดวิชาศึกษาทั่วไป รายละเอียดของหลักสูตร")
        count, issues = build_index(db_conn, [chunk])

        assert count == 1
        assert issues == []

        # ยืนยัน FTS entry
        row = db_conn.execute("SELECT COUNT(*) AS n FROM chunk_fts").fetchone()
        assert row["n"] == 1

    def test_multiple_chunks_indexed(
        self, db_conn: sqlite3.Connection, version_it: CurriculumVersion
    ) -> None:
        """หลาย chunks ถูก index ครบ — จำนวน entry = จำนวน chunk."""
        vid = _seed_version(db_conn, version_it)
        _seed_document_and_page(db_conn, "doc1", vid, pages=[1, 2, 3])

        chunks = [
            _make_chunk("วิชาบังคับสำหรับสาขาวิทยาการคอมพิวเตอร์", page=1),
            _make_chunk("วิชาเลือกสำหรับสาขาเทคโนโลยีสารสนเทศ", page=2),
            _make_chunk("วิชาศึกษาทั่วไปกลุ่มภาษาอังกฤษ", page=3),
        ]
        count, issues = build_index(db_conn, chunks)

        assert count == 3
        assert issues == []

        # ยืนยัน FTS entry count
        row = db_conn.execute("SELECT COUNT(*) AS n FROM chunk_fts").fetchone()
        assert row["n"] == 3

    def test_chunk_references_version(
        self, db_conn: sqlite3.Connection, version_it: CurriculumVersion
    ) -> None:
        """ทุก entry อ้าง curriculum version ได้ผ่าน chunk → curriculum_version."""
        vid = _seed_version(db_conn, version_it)
        _seed_document_and_page(db_conn, "doc1", vid)

        chunk = _make_chunk("เนื้อหาทดสอบเวอร์ชัน")
        build_index(db_conn, [chunk])

        row = db_conn.execute(
            """SELECT cv.program, cv.curriculum_year, cv.edition_status
               FROM chunk c
               JOIN curriculum_version cv ON cv.version_id = c.version_id"""
        ).fetchone()

        assert row["program"] == "IT"
        assert row["curriculum_year"] == 2565
        assert row["edition_status"] == "current"

    def test_duplicate_chunk_not_double_indexed(
        self, db_conn: sqlite3.Connection, version_it: CurriculumVersion
    ) -> None:
        """chunk ซ้ำ (content_sha256 + version เดียวกัน) ไม่ถูก index ซ้ำ."""
        vid = _seed_version(db_conn, version_it)
        _seed_document_and_page(db_conn, "doc1", vid)

        chunk = _make_chunk("เนื้อหาเดิมซ้ำ")
        count1, _ = build_index(db_conn, [chunk])
        count2, _ = build_index(db_conn, [chunk])

        assert count1 == 1
        assert count2 == 0  # ไม่ index ซ้ำ

        row = db_conn.execute("SELECT COUNT(*) AS n FROM chunk_fts").fetchone()
        assert row["n"] == 1


class TestBuildIndexIncomplete:
    """R13.12: เมื่อสร้างดัชนีบาง chunk ไม่สำเร็จ."""

    def test_missing_document_reports_issue(
        self, db_conn: sqlite3.Connection, version_it: CurriculumVersion
    ) -> None:
        """chunk ที่อ้าง document ไม่มีในฐาน → ล้มเหลว + review issue."""
        _seed_version(db_conn, version_it)
        # ไม่สร้าง document "missing_doc"

        chunk = _make_chunk("เนื้อหา", document_id="missing_doc")
        count, issues = build_index(db_conn, [chunk])

        assert count == 0
        assert len(issues) == 1
        assert issues[0].kind == "index_build_incomplete"
        assert issues[0].detail["failed_count"] == 1

    def test_partial_success_preserves_good_chunks(
        self, db_conn: sqlite3.Connection, version_it: CurriculumVersion
    ) -> None:
        """บาง chunk สำเร็จ บาง chunk ล้มเหลว — คงดัชนีที่สำเร็จไว้."""
        vid = _seed_version(db_conn, version_it)
        _seed_document_and_page(db_conn, "doc1", vid)
        # ไม่สร้าง document "bad_doc"

        good_chunk = _make_chunk("วิชาที่ index ได้", document_id="doc1")
        bad_chunk = _make_chunk("วิชาที่ index ไม่ได้", document_id="bad_doc")

        count, issues = build_index(db_conn, [good_chunk, bad_chunk])

        assert count == 1  # good chunk สำเร็จ
        assert len(issues) == 1
        assert issues[0].kind == "index_build_incomplete"
        assert issues[0].detail["failed_count"] == 1
        assert issues[0].detail["indexed_count"] == 1

        # ยืนยันว่า good chunk ยังอยู่ในดัชนี
        row = db_conn.execute("SELECT COUNT(*) AS n FROM chunk_fts").fetchone()
        assert row["n"] == 1

    def test_all_fail_reports_full_issue(
        self, db_conn: sqlite3.Connection, version_it: CurriculumVersion
    ) -> None:
        """ทุก chunk ล้มเหลว — issue รายงานจำนวนที่ล้มทั้งหมด."""
        _seed_version(db_conn, version_it)

        chunks = [
            _make_chunk("chunk1", document_id="no_doc_1"),
            _make_chunk("chunk2", document_id="no_doc_2"),
        ]
        count, issues = build_index(db_conn, chunks)

        assert count == 0
        assert len(issues) == 1
        assert issues[0].detail["failed_count"] == 2
        assert issues[0].detail["total_attempted"] == 2


# ── search tests ──────────────────────────────────────────────────────


class TestSearch:
    """ทดสอบการค้นหา FTS5 BM25."""

    @pytest.fixture
    def indexed_db(
        self, db_conn: sqlite3.Connection, version_it: CurriculumVersion, version_dsba: CurriculumVersion
    ) -> sqlite3.Connection:
        """DB ที่มี chunks หลายตัวจากหลาย version."""
        vid_it = _seed_version(db_conn, version_it)
        vid_dsba = _seed_version(db_conn, version_dsba)

        _seed_document_and_page(db_conn, "doc_it", vid_it, pages=[1, 2])
        _seed_document_and_page(db_conn, "doc_dsba", vid_dsba, pages=[1])

        chunks = [
            _make_chunk(
                "คณิตศาสตร์วิศวกรรม วิชาบังคับสำหรับนักศึกษาชั้นปีที่หนึ่ง",
                document_id="doc_it", page=1,
                heading="หมวดวิชาบังคับ",
            ),
            _make_chunk(
                "ภาษาอังกฤษสำหรับวิทยาศาสตร์ วิชาศึกษาทั่วไป",
                document_id="doc_it", page=2,
                heading="หมวดวิชาศึกษาทั่วไป",
            ),
            _make_chunk(
                "วิทยาศาสตร์ข้อมูลเบื้องต้น หลักสูตร DSBA",
                document_id="doc_dsba", page=1,
                program="DSBA", curriculum_year=2566,
                heading="วิชาแกน",
            ),
        ]
        build_index(db_conn, chunks)
        return db_conn

    def test_empty_query_returns_empty(self, indexed_db: sqlite3.Connection) -> None:
        results = search(indexed_db, "")
        assert results == []

    def test_whitespace_query_returns_empty(self, indexed_db: sqlite3.Connection) -> None:
        results = search(indexed_db, "   ")
        assert results == []

    def test_basic_search_returns_results(self, indexed_db: sqlite3.Connection) -> None:
        """ค้นหาคำที่อยู่ใน chunk ได้ผลลัพธ์."""
        results = search(indexed_db, "คณิตศาสตร์")
        assert len(results) >= 1
        assert all(isinstance(r, LexicalHit) for r in results)

    def test_results_have_positive_scores(self, indexed_db: sqlite3.Connection) -> None:
        """คะแนน BM25 ต้องเป็นบวก."""
        results = search(indexed_db, "วิชา")
        assert all(r.score > 0 for r in results)

    def test_results_sorted_by_score_descending(self, indexed_db: sqlite3.Connection) -> None:
        """ผลลัพธ์เรียงตามคะแนนจากมากไปน้อย."""
        results = search(indexed_db, "วิชา")
        if len(results) >= 2:
            scores = [r.score for r in results]
            assert scores == sorted(scores, reverse=True)

    def test_version_filter_restricts_results(self, indexed_db: sqlite3.Connection) -> None:
        """R10.5: กรองเฉพาะ curriculum version ที่ระบุ."""
        version_it = CurriculumVersion(program="IT", curriculum_year=2565, edition_status="current")
        results = search(indexed_db, "วิทยาศาสตร์", version_filter=version_it)

        # ทุกผลลัพธ์ต้องเป็นของ IT 2565
        for r in results:
            assert r.program == "IT"
            assert r.curriculum_year == 2565

    def test_version_filter_excludes_other_versions(
        self, indexed_db: sqlite3.Connection
    ) -> None:
        """version filter ไม่รวมผลจาก version อื่น."""
        version_dsba = CurriculumVersion(program="DSBA", curriculum_year=2566, edition_status="current")
        results = search(indexed_db, "วิทยาศาสตร์", version_filter=version_dsba)

        for r in results:
            assert r.program == "DSBA"

    def test_top_k_limits_results(self, indexed_db: sqlite3.Connection) -> None:
        """top_k จำกัดจำนวนผลลัพธ์."""
        results = search(indexed_db, "วิชา", top_k=1)
        assert len(results) <= 1

    def test_no_match_returns_empty(self, indexed_db: sqlite3.Connection) -> None:
        """ค้นหาคำที่ไม่มีใน chunk ได้ผลว่าง."""
        results = search(indexed_db, "xyzzynonexistent")
        assert results == []

    def test_hit_has_chunk_id(self, indexed_db: sqlite3.Connection) -> None:
        """ผลลัพธ์ทุกรายการอ้าง chunk_id ได้."""
        results = search(indexed_db, "คณิตศาสตร์")
        for r in results:
            assert r.chunk_id > 0

    def test_hit_has_version_property(self, indexed_db: sqlite3.Connection) -> None:
        """ผลลัพธ์สร้าง CurriculumVersion ได้ถูกต้อง."""
        results = search(indexed_db, "คณิตศาสตร์")
        for r in results:
            v = r.version
            assert isinstance(v, CurriculumVersion)
            assert v.program in ("IT", "DSBA")


# ── escape tests ──────────────────────────────────────────────────────


class TestEscapeFts5Query:
    """ทดสอบ _escape_fts5_query."""

    def test_normal_text_preserved(self) -> None:
        result = _escape_fts5_query("คณิตศาสตร์")
        assert "คณิตศาสตร์" in result

    def test_special_chars_removed(self) -> None:
        result = _escape_fts5_query('test*"()')
        assert "*" not in result
        assert "(" not in result

    def test_empty_returns_empty(self) -> None:
        assert _escape_fts5_query("") == ""

    def test_only_special_returns_empty(self) -> None:
        assert _escape_fts5_query("***") == ""

    def test_fts_keywords_quoted(self) -> None:
        """OR, AND, NOT ถูก quote เพื่อไม่ให้เป็น operator."""
        result = _escape_fts5_query("OR AND NOT")
        # ทุก term ควรถูก quote
        assert '"OR"' in result or '"or"' in result.lower()


# ── rebuild tests ─────────────────────────────────────────────────────


class TestRebuildIndex:
    """ทดสอบ rebuild_index."""

    def test_rebuild_on_empty_db(self, db_conn: sqlite3.Connection) -> None:
        """rebuild บน DB ว่างไม่พัง."""
        rebuild_index(db_conn)  # ไม่ raise

    def test_rebuild_after_build(
        self, db_conn: sqlite3.Connection, version_it: CurriculumVersion
    ) -> None:
        """rebuild หลัง build ยัง search ได้."""
        vid = _seed_version(db_conn, version_it)
        _seed_document_and_page(db_conn, "doc1", vid)

        chunk = _make_chunk("เนื้อหาสำหรับ rebuild test")
        build_index(db_conn, [chunk])
        rebuild_index(db_conn)

        results = search(db_conn, "rebuild")
        assert len(results) >= 1
