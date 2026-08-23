"""สร้าง gold_set สำหรับวัด citation accuracy — หาหน้าที่ควรถูกอ้างอิงจากฐานข้อมูล.

แนวคิด: คำถามแต่ละข้อมี "หน้าที่เป็นหลักฐานถูกต้อง" ซึ่งหาได้แบบ deterministic
จาก provenance ของข้อมูลที่ใช้ตอบ ไม่ต้องนั่ง key มือ

  - คำถามรายวิชาตามชั้นปี → หน้าที่มีตารางแผนการเรียนของปีนั้น
  - คำถาม prerequisite     → หน้าที่มีคำอธิบายรายวิชานั้น
  - คำถามหัวข้อวิชา        → หน้าที่มีรายวิชาที่เกี่ยวข้อง

ผลถูกเขียนลงตาราง gold_set (item_kind='question') พร้อม expected_citations_json
ซึ่ง qa_eval จะนำไปเทียบกับ citation ที่ระบบคืนมา

**สำคัญ:** สคริปต์นี้ derive หน้าจาก provenance ของ *ข้อมูลจริง* ไม่ได้ derive
จากคำตอบของระบบ จึงไม่เกิด circularity (ระบบไม่ได้ตรวจตัวเอง)

Usage:
    python -m katrag.eval.build_gold_set
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from katrag.eval.qa_questions import ALL_QUESTIONS, QAItem
from katrag.query.structured_query import (
    detect_program,
    detect_semester,
    detect_year,
)

# schema ของ gold_set บังคับ question_level ∈ L1..L4
# map ระดับความยากในชุดคำถามไปยังรหัสที่ schema ยอมรับ
_LEVEL_CODE = {"easy": "L1", "medium": "L2", "hard": "L3"}


def _resolve_version(conn: sqlite3.Connection, program: str) -> int | None:
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT cv.version_id, COUNT(c.course_id) n
        FROM curriculum_version cv
        LEFT JOIN course c ON c.version_id = cv.version_id
        WHERE cv.program=? AND cv.edition_status='current'
        GROUP BY cv.version_id ORDER BY n DESC, cv.curriculum_year DESC LIMIT 1
        """,
        (program,),
    ).fetchone()
    return row["version_id"] if row else None


def _pages_for_year(conn: sqlite3.Connection, version_id: int, year: int,
                    semester: int | None) -> list[tuple[str, int]]:
    """หน้าที่มีรายวิชาของชั้นปี/ภาคนั้น (จาก provenance ของ course)."""
    sql = (
        "SELECT DISTINCT pr.document_id, pr.page_number "
        "FROM course c JOIN provenance pr ON pr.provenance_id = c.provenance_id "
        "WHERE c.version_id=? AND c.year=?"
    )
    params: list = [version_id, year]
    if semester is not None:
        sql += " AND c.semester=?"
        params.append(semester)
    return [(r["document_id"], r["page_number"]) for r in conn.execute(sql, params)]


def _pages_for_codes(conn: sqlite3.Connection, version_id: int,
                     codes: list[str]) -> list[tuple[str, int]]:
    if not codes:
        return []
    ph = ",".join("?" * len(codes))
    return [
        (r["document_id"], r["page_number"])
        for r in conn.execute(
            f"SELECT DISTINCT pr.document_id, pr.page_number "
            f"FROM course c JOIN provenance pr ON pr.provenance_id = c.provenance_id "
            f"WHERE c.version_id=? AND c.code IN ({ph})",
            [version_id, *codes],
        )
    ]


def _pages_for_course_name(conn: sqlite3.Connection, version_id: int,
                           keyword: str) -> list[tuple[str, int]]:
    """ทุกหน้าที่ *รหัสวิชา* ของวิชาที่ชื่อตรงคำสำคัญ ปรากฏอยู่.

    ทำสองขั้น:
      1. หารหัสวิชาที่ชื่อ (ไทย/อังกฤษ) ตรงกับคำสำคัญ
      2. หาทุกหน้าที่มีรหัสนั้นใน chunk

    ไม่ใช้ provenance ของ course โดยตรง เพราะ populate_courses บันทึกเฉพาะ
    ครั้งแรกที่เจอวิชา (dedup ด้วยรหัส) แต่ในเล่มจริงวิชาหนึ่งปรากฏหลายหน้า
    (ตารางแผนการเรียน + ตารางรายวิชา + คำอธิบายรายวิชา) ซึ่งทุกหน้าเป็นหลักฐานที่ถูก
    ถ้าใช้ provenance หน้าเดียว gold จะแคบเกินจริงและลงโทษระบบอย่างไม่เป็นธรรม
    """
    codes = [
        r["code"]
        for r in conn.execute(
            "SELECT DISTINCT code FROM course "
            "WHERE version_id=? AND (name_th LIKE ? OR name_en LIKE ?)",
            (version_id, f"%{keyword}%", f"%{keyword}%"),
        )
    ]
    if not codes:
        return []

    pages: set[tuple[str, int]] = set()
    for code in codes[:12]:  # กันคำกว้างที่ตรงหลายสิบวิชา
        for r in conn.execute(
            "SELECT DISTINCT document_id, page_number FROM chunk "
            "WHERE version_id=? AND text LIKE ? "
            "AND COALESCE(is_boilerplate, 0) = 0",
            (version_id, f"%{code}%"),
        ):
            pages.add((r["document_id"], r["page_number"]))
    return sorted(pages)


def _pages_for_keyword(conn: sqlite3.Connection, version_id: int,
                       keyword: str, limit: int = 20) -> list[tuple[str, int]]:
    """หน้าที่มีคำสำคัญปรากฏใน chunk (fallback เมื่อคำนั้นไม่ใช่ชื่อวิชา).

    ข้าม chunk ที่เป็นหัว/ท้ายกระดาษซ้ำทุกหน้า (boilerplate) เพราะมีชื่อหลักสูตร
    ครบทุกหน้าแต่ไม่ใช่หลักฐานของคำตอบ
    """
    return [
        (r["document_id"], r["page_number"])
        for r in conn.execute(
            "SELECT DISTINCT document_id, page_number FROM chunk "
            "WHERE version_id=? AND text LIKE ? "
            "AND COALESCE(is_boilerplate, 0) = 0 LIMIT ?",
            (version_id, f"%{keyword}%", limit),
        )
    ]


# จำนวนหน้าหลักฐานสูงสุดต่อคำถาม
# ต้องจำกัด เพราะ recall มีเพดาน = จำนวน citation ที่ระบบคืน (10) / |gold|
# ถ้า gold กว้างเกินไป recall จะต่ำโดยอัตโนมัติและตีความไม่ได้
MAX_GOLD_PAGES = 10


def expected_pages(conn: sqlite3.Connection, item: QAItem) -> list[tuple[str, int]]:
    """หาหน้าที่ควรถูกอ้างอิงสำหรับคำถามหนึ่งข้อ.

    จัดลำดับความสำคัญของหน้า แล้วตัดเหลือ MAX_GOLD_PAGES หน้าที่ตรงที่สุด:
      1. หน้าที่มีรายวิชาของชั้นปี/ภาคที่ถาม (ถ้าคำถามระบุ) — ตรงที่สุด
      2. หน้าที่มีคำสำคัญของคำถาม เรียงตามจำนวนคำสำคัญที่พบในหน้านั้น
    """
    conn.row_factory = sqlite3.Row
    program = item.program or detect_program(item.question)
    if not program:
        return []
    version_id = _resolve_version(conn, program)
    if version_id is None:
        return []

    # ── ชั้น 1: หน้าแผนการเรียนของชั้นปี/ภาคที่ถาม ──
    primary: list[tuple[str, int]] = []
    year = detect_year(item.question)
    sem = detect_semester(item.question)
    if year is not None:
        primary = _pages_for_year(conn, version_id, year, sem)

    # ── ชั้น 2: หน้าต้นทางของรายวิชาที่ชื่อตรงคำสำคัญ (แม่นกว่าค้นทุก chunk) ──
    tokens = [t for t in item.check if len(t) >= 4 and not t.isdigit()]
    score: dict[tuple[str, int], int] = {}
    for token in tokens:
        pages_by_course = _pages_for_course_name(conn, version_id, token)
        if pages_by_course:
            for pg in pages_by_course:
                # ให้น้ำหนักสูงกว่า เพราะผูกกับรายวิชาจริง
                score[pg] = score.get(pg, 0) + 2
        else:
            # คำที่ไม่ใช่ชื่อวิชา (เช่น "แขนง", "หน่วยกิต") → ค้นใน chunk
            for pg in _pages_for_keyword(conn, version_id, token, limit=20):
                score[pg] = score.get(pg, 0) + 1

    ranked_secondary = sorted(score.items(), key=lambda kv: (-kv[1], kv[0]))

    out: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for pg in primary:
        if pg not in seen:
            seen.add(pg)
            out.append(pg)
        if len(out) >= MAX_GOLD_PAGES:
            return sorted(out)
    for pg, _n in ranked_secondary:
        if pg not in seen:
            seen.add(pg)
            out.append(pg)
        if len(out) >= MAX_GOLD_PAGES:
            break
    return sorted(out)


def build(db_path: Path, author: str = "auto-derived") -> dict[str, int]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    conn.execute("DELETE FROM gold_set WHERE item_kind='question'")

    now = datetime.now(timezone.utc).isoformat()
    written = 0
    empty = 0

    for item in ALL_QUESTIONS:
        pages = expected_pages(conn, item)
        if not pages:
            empty += 1
        program = item.program or detect_program(item.question) or ""
        version_id = _resolve_version(conn, program) if program else None
        doc_id = pages[0][0] if pages else None
        page_no = pages[0][1] if pages else None

        conn.execute(
            """
            INSERT INTO gold_set
              (item_kind, document_id, page_number, version_id, question_level,
               payload_json, expected_json, expected_citations_json,
               author, created_date, review_method)
            VALUES ('question', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                doc_id,
                page_no,
                version_id,
                _LEVEL_CODE.get(item.level, "L1"),
                json.dumps(
                    {"qid": item.qid, "question": item.question, "program": item.program},
                    ensure_ascii=False,
                ),
                json.dumps(
                    {"expected": item.expected, "reference": item.reference},
                    ensure_ascii=False,
                ),
                json.dumps(
                    [{"document_id": d, "page": p} for d, p in pages],
                    ensure_ascii=False,
                ),
                author,
                now,
                "derived_from_provenance",
            ),
        )
        written += 1

    conn.commit()
    n = conn.execute(
        "SELECT COUNT(*) FROM gold_set WHERE item_kind='question'"
    ).fetchone()[0]
    conn.close()
    return {"written": written, "no_pages": empty, "total_in_db": n}


def main() -> None:
    root = Path(__file__).resolve().parent.parent.parent
    ap = argparse.ArgumentParser(description="Build gold_set for citation evaluation")
    ap.add_argument("--db", default=str(root / "artifacts" / "katrag.sqlite3"))
    args = ap.parse_args()
    stats = build(Path(args.db))
    print(f"gold_set rows written : {stats['written']}")
    print(f"  ไม่พบหน้าหลักฐาน    : {stats['no_pages']}")
    print(f"  รวมใน DB            : {stats['total_in_db']}")


if __name__ == "__main__":
    main()
