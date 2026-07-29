"""Lexical index — FTS5 BM25 ค้นหาข้อความจาก chunk (R13.1, R13.12).

สร้างดัชนี FTS5 จากทุก chunk โดย:
- จำนวน entry ในดัชนีเท่ากับจำนวน chunk ที่สร้างสำเร็จ
- ทุก entry อ้าง chunk_id กับ curriculum version ได้
- รองรับการกรอง version ก่อน scoring (R10.5)
- ถ้า index chunk ใดไม่สำเร็จ ให้ข้ามแล้วรายงาน index_build_incomplete (R13.12)
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from typing import Sequence

from katrag.common.types import CurriculumVersion
from katrag.errors import ReviewIssue
from katrag.ingest.chunker import Chunk
from katrag.store.integrity import connect, apply_schema

logger = logging.getLogger(__name__)

# ── result type ───────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class LexicalHit:
    """ผลลัพธ์หนึ่งรายการจากการค้น FTS5 พร้อมคะแนน BM25."""

    chunk_id: int
    score: float
    heading: str
    text_snippet: str
    program: str
    curriculum_year: int
    edition_status: str

    @property
    def version(self) -> CurriculumVersion:
        return CurriculumVersion(
            program=self.program,
            curriculum_year=self.curriculum_year,
            edition_status=self.edition_status,  # type: ignore[arg-type]
        )


# ── index builder ─────────────────────────────────────────────────────


def build_index(
    conn: sqlite3.Connection,
    chunks: Sequence[Chunk],
) -> tuple[int, list[ReviewIssue]]:
    """สร้างดัชนี FTS5 จาก chunks ลงในฐานข้อมูลที่เปิดไว้แล้ว.

    ขั้นตอน:
    1. ตรวจว่า schema มี chunk table + chunk_fts virtual table แล้ว
    2. เขียน chunk ลงตาราง chunk (ข้ามถ้ามี content_sha256+version_id เดิมแล้ว)
    3. เพิ่ม row เข้า chunk_fts ผ่าน INSERT INTO chunk_fts(rowid, text, heading)

    Args:
        conn: SQLite connection ที่เปิดจาก store.integrity.connect()
        chunks: ลำดับ Chunk ที่จะสร้างดัชนี

    Returns:
        (จำนวน entry ที่สร้างสำเร็จ, รายการ ReviewIssue ถ้ามี chunk ที่ล้มเหลว)
    """
    issues: list[ReviewIssue] = []
    indexed_count = 0
    failed_chunks: list[str] = []

    for chunk in chunks:
        try:
            chunk_id = _insert_chunk(conn, chunk)
            if chunk_id is not None:
                _insert_fts(conn, chunk_id, chunk)
                indexed_count += 1
        except (sqlite3.Error, ValueError) as exc:
            logger.warning(
                "index chunk failed: sha256=%s, error=%s",
                chunk.content_sha256,
                str(exc),
            )
            failed_chunks.append(chunk.content_sha256)

    if failed_chunks:
        issues.append(
            ReviewIssue(
                kind="index_build_incomplete",
                detail={
                    "failed_count": len(failed_chunks),
                    "failed_sha256": failed_chunks[:20],  # cap detail size
                    "total_attempted": len(chunks),
                    "indexed_count": indexed_count,
                },
            )
        )

    return indexed_count, issues


def rebuild_index(conn: sqlite3.Connection) -> None:
    """สร้าง FTS5 index ใหม่จาก chunk table ทั้งหมด (rebuild command).

    ใช้เมื่อต้อง sync content table กับ FTS virtual table ใหม่ทั้งหมด
    """
    conn.execute("INSERT INTO chunk_fts(chunk_fts) VALUES('rebuild')")


# ── search ────────────────────────────────────────────────────────────


def search(
    conn: sqlite3.Connection,
    query_text: str,
    *,
    version_filter: CurriculumVersion | None = None,
    top_k: int = 100,
) -> list[LexicalHit]:
    """ค้น FTS5 ด้วย BM25 ranking พร้อม version filter.

    Args:
        conn: SQLite connection (ควรเปิดแบบ read_only สำหรับ query path)
        query_text: ข้อความค้นหา
        version_filter: กรองเฉพาะ curriculum version (R10.5) ถ้า None ไม่กรอง
        top_k: จำนวนผลลัพธ์สูงสุด (ค่าเริ่มต้น 100 จาก retrieval.lexical_top_k)

    Returns:
        รายการ LexicalHit เรียงตามคะแนน BM25 จากมากไปน้อย
    """
    if not query_text.strip():
        return []

    # Escape FTS5 special characters in query to prevent syntax errors
    safe_query = _escape_fts5_query(query_text)
    if not safe_query:
        return []

    if version_filter is not None:
        sql = """
            SELECT c.chunk_id,
                   bm25(chunk_fts) AS score,
                   c.heading,
                   c.text,
                   cv.program,
                   cv.curriculum_year,
                   cv.edition_status
              FROM chunk_fts
              JOIN chunk c ON c.chunk_id = chunk_fts.rowid
              JOIN curriculum_version cv ON cv.version_id = c.version_id
             WHERE chunk_fts MATCH ?
               AND cv.program = ?
               AND cv.curriculum_year = ?
               AND cv.edition_status = ?
             ORDER BY score
             LIMIT ?
        """
        params: tuple = (
            safe_query,
            version_filter.program,
            version_filter.curriculum_year,
            version_filter.edition_status,
            top_k,
        )
    else:
        sql = """
            SELECT c.chunk_id,
                   bm25(chunk_fts) AS score,
                   c.heading,
                   c.text,
                   cv.program,
                   cv.curriculum_year,
                   cv.edition_status
              FROM chunk_fts
              JOIN chunk c ON c.chunk_id = chunk_fts.rowid
              JOIN curriculum_version cv ON cv.version_id = c.version_id
             WHERE chunk_fts MATCH ?
             ORDER BY score
             LIMIT ?
        """
        params = (safe_query, top_k)

    rows = conn.execute(sql, params).fetchall()

    results: list[LexicalHit] = []
    for row in rows:
        results.append(
            LexicalHit(
                chunk_id=row["chunk_id"],
                score=-row["score"],  # bm25() returns negative; negate for descending rank
                heading=row["heading"],
                text_snippet=row["text"][:500],
                program=row["program"],
                curriculum_year=row["curriculum_year"],
                edition_status=row["edition_status"],
            )
        )

    return results


# ── helpers ───────────────────────────────────────────────────────────


def _get_or_create_version_id(conn: sqlite3.Connection, chunk: Chunk) -> int:
    """หา version_id จาก curriculum_version หรือสร้างใหม่."""
    from katrag.common.hashing import sha256_parts

    row = conn.execute(
        """SELECT version_id FROM curriculum_version
           WHERE program = ? AND curriculum_year = ? AND edition_status = ?""",
        (chunk.program, chunk.curriculum_year, chunk.edition_status),
    ).fetchone()

    if row is not None:
        return int(row["version_id"])

    # สร้าง version ใหม่
    version_sha = sha256_parts([chunk.program, chunk.curriculum_year, chunk.edition_status])
    cur = conn.execute(
        """INSERT INTO curriculum_version (program, curriculum_year, edition_status, version_sha256)
           VALUES (?, ?, ?, ?)""",
        (chunk.program, chunk.curriculum_year, chunk.edition_status, version_sha),
    )
    return cur.lastrowid  # type: ignore[return-value]


def _insert_chunk(conn: sqlite3.Connection, chunk: Chunk) -> int | None:
    """แทรก chunk ลงตาราง chunk — คืน chunk_id หรือ None ถ้ามีอยู่แล้ว.

    ตาราง chunk มี UNIQUE(content_sha256, version_id) ดังนั้น
    ถ้ามี chunk เนื้อหาเดียวกันใน version เดียวกันอยู่แล้ว จะข้าม
    """
    version_id = _get_or_create_version_id(conn, chunk)

    # ตรวจว่ามีอยู่แล้วหรือยัง
    existing = conn.execute(
        "SELECT chunk_id FROM chunk WHERE content_sha256 = ? AND version_id = ?",
        (chunk.content_sha256, version_id),
    ).fetchone()

    if existing is not None:
        # มีอยู่แล้ว — ตรวจว่า FTS entry มีหรือยัง
        fts_exists = conn.execute(
            "SELECT rowid FROM chunk_fts WHERE rowid = ?",
            (existing["chunk_id"],),
        ).fetchone()
        if fts_exists is None:
            # chunk มีแต่ FTS ไม่มี — เพิ่ม FTS แล้วนับเป็นสำเร็จ
            return int(existing["chunk_id"])
        return None  # ทั้ง chunk และ FTS มีอยู่แล้ว

    # ต้องมี document + provenance — สร้าง minimal provenance สำหรับ index
    # ตรวจว่า document มีอยู่หรือยัง
    doc_exists = conn.execute(
        "SELECT 1 FROM document WHERE document_id = ?",
        (chunk.document_id,),
    ).fetchone()

    if doc_exists is None:
        # ถ้า document ยังไม่มี ไม่สามารถสร้าง chunk ได้ (FK constraint)
        raise ValueError(f"document_id '{chunk.document_id}' ไม่มีในฐานข้อมูล")

    # สร้าง provenance entry สำหรับ chunk (simplified — ใช้ full page bbox)
    page_row = conn.execute(
        "SELECT width_pt, height_pt FROM page WHERE document_id = ? AND page_number = ?",
        (chunk.document_id, chunk.page_start),
    ).fetchone()

    if page_row is None:
        raise ValueError(
            f"page ({chunk.document_id}, {chunk.page_start}) ไม่มีในฐานข้อมูล"
        )

    prov_cur = conn.execute(
        """INSERT INTO provenance
           (document_id, page_number, x0, y0, x1, y1, span_start, span_end,
            extraction_method, provenance_source)
           VALUES (?, ?, 0, 0, ?, ?, 0, ?, 'text_layer', 'document_text')""",
        (
            chunk.document_id,
            chunk.page_start,
            float(page_row["width_pt"]),
            float(page_row["height_pt"]),
            len(chunk.content_text),
        ),
    )
    provenance_id = prov_cur.lastrowid

    cur = conn.execute(
        """INSERT INTO chunk
           (document_id, page_number, version_id, heading, text,
            token_count, content_sha256, provenance_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            chunk.document_id,
            chunk.page_start,
            version_id,
            chunk.heading,
            chunk.content_text,
            chunk.token_count,
            chunk.content_sha256,
            provenance_id,
        ),
    )
    return cur.lastrowid  # type: ignore[return-value]


def _insert_fts(conn: sqlite3.Connection, chunk_id: int, chunk: Chunk) -> None:
    """แทรก entry ลง chunk_fts virtual table."""
    conn.execute(
        "INSERT INTO chunk_fts(rowid, text, heading) VALUES (?, ?, ?)",
        (chunk_id, chunk.content_text, chunk.heading),
    )


def _escape_fts5_query(query: str) -> str:
    """Escape ข้อความค้นหาให้ปลอดภัยกับ FTS5 MATCH syntax.

    แปลงเป็นคำค้นหาแบบ terms (แยกด้วย space) โดยถอดอักขระพิเศษ FTS5 ออก
    """
    # ถอดอักขระพิเศษของ FTS5: *, ", (, ), ^, {, }, :, +, -, ~
    cleaned = ""
    for ch in query:
        if ch in '*"()^{}:+-~\\':
            cleaned += " "
        else:
            cleaned += ch

    # แยกเป็น terms แล้วรวมด้วย space (implicit AND ใน FTS5)
    terms = cleaned.split()
    if not terms:
        return ""

    # Quote แต่ละ term เพื่อป้องกัน FTS5 keyword collision (OR, AND, NOT, NEAR)
    safe_terms: list[str] = []
    for term in terms:
        if term.upper() in ("OR", "AND", "NOT", "NEAR"):
            safe_terms.append(f'"{term}"')
        else:
            safe_terms.append(f'"{term}"')

    return " ".join(safe_terms)
