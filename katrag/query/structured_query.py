"""Structured Query Path — ตอบคำถามรายวิชา/แผนเรียนจากตาราง course/plan_slot ตรง ๆ.

แม่นกว่า chunk retrieval สำหรับคำถามประเภท:
- "ปี X เทอม Y เรียนอะไร" → query course by year/semester
- "มีกี่วิชา / กี่หน่วยกิต" → aggregate
- คืน context ที่มี code + name_th + name_en + credits + year/semester ครบ
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass


PROGRAM_CODES = ["AITBA", "DSBA", "AIT", "BIT", "IT"]

_THAI_NUM = {"หนึ่ง": 1, "สอง": 2, "สาม": 3, "สี่": 4}


@dataclass
class StructuredResult:
    matched: bool
    context: str  # evidence text สำหรับส่ง LLM
    version_label: str
    intent: str  # "year_sem" | "all_courses" | "none"


def detect_program(question: str) -> str | None:
    upper = question.upper()
    for code in PROGRAM_CODES:
        if re.search(rf"(?<![A-Z]){re.escape(code)}(?![A-Z])", upper):
            return code
    kw_map = {
        "วิทยาการข้อมูล": "DSBA", "วิเคราะห์เชิงธุรกิจ": "DSBA",
        "ปัญญาประดิษฐ์": "AIT", "เทคโนโลยีสารสนเทศ": "IT",
    }
    for kw, prog in kw_map.items():
        if kw in question:
            return prog
    return None


def detect_year(question: str) -> int | None:
    """ตรวจชั้นปี (1-4) จาก 'ปีหนึ่ง/ปีที่ 2/ปี 3'."""
    for word, n in _THAI_NUM.items():
        if f"ปี{word}" in question or f"ปีที่{word}" in question:
            return n
    m = re.search(r"ปี(?:ที่)?\s*([1-4])", question)
    if m:
        return int(m.group(1))
    return None


def detect_semester(question: str) -> int | None:
    """ตรวจภาคเรียน (1-3) จาก 'เทอม 1/ภาคต้น/ภาคปลาย/ภาคการศึกษาที่ 2'."""
    if "ภาคต้น" in question or "เทอมต้น" in question or "เทอมแรก" in question:
        return 1
    if "ภาคปลาย" in question or "เทอมปลาย" in question:
        return 2
    m = re.search(r"(?:เทอม|ภาค(?:การศึกษา)?(?:ที่)?)\s*([1-3])", question)
    if m:
        return int(m.group(1))
    return None


def _resolve_version_id(conn: sqlite3.Connection, program: str, year_be: int | None) -> tuple[int, str] | None:
    conn.row_factory = sqlite3.Row
    if year_be:
        row = conn.execute(
            "SELECT version_id, program, curriculum_year, edition_status FROM curriculum_version WHERE program=? AND curriculum_year=?",
            (program, year_be),
        ).fetchone()
        if row:
            return row["version_id"], f"{row['program']} {row['curriculum_year']} ({row['edition_status']})"
    # default: current edition
    row = conn.execute(
        "SELECT version_id, program, curriculum_year, edition_status FROM curriculum_version WHERE program=? AND edition_status='current' ORDER BY curriculum_year DESC LIMIT 1",
        (program,),
    ).fetchone()
    if row:
        return row["version_id"], f"{row['program']} {row['curriculum_year']} ({row['edition_status']})"
    return None


def try_structured_answer(conn: sqlite3.Connection, question: str) -> StructuredResult:
    """ลองตอบจากตาราง structured. คืน matched=False ถ้าไม่เข้าเงื่อนไข."""
    conn.row_factory = sqlite3.Row

    program = detect_program(question)
    if not program:
        return StructuredResult(False, "", "", "none")

    year_level = detect_year(question)
    semester = detect_semester(question)

    # ตรวจปี พ.ศ. (สำหรับ version)
    be_match = re.search(r"\b(25\d\d)\b", question)
    year_be = int(be_match.group(1)) if be_match else None

    resolved = _resolve_version_id(conn, program, year_be)
    if not resolved:
        return StructuredResult(False, "", "", "none")
    version_id, version_label = resolved

    # คำถามเกี่ยวกับรายวิชา/แผนเรียนหรือไม่
    course_intent = any(w in question for w in ["เรียน", "วิชา", "รายวิชา", "แผน", "หน่วยกิต", "บังคับ", "เลือก"])
    if not course_intent:
        return StructuredResult(False, "", "", "none")

    # ── กรณี: ระบุปี+เทอม → query course ตามชั้นปี/ภาค ──
    if year_level is not None:
        params: list = [version_id, year_level]
        sem_clause = ""
        if semester is not None:
            sem_clause = " AND semester=?"
            params.append(semester)
        rows = conn.execute(
            f"SELECT code, name_th, name_en, credits_raw, year, semester "
            f"FROM course WHERE version_id=? AND year=?{sem_clause} ORDER BY semester, code",
            params,
        ).fetchall()

        if rows:
            lines = [f"รายวิชาของหลักสูตร {version_label} "
                     f"ปีที่ {year_level}" + (f" ภาคการศึกษาที่ {semester}" if semester else "") + ":"]
            for r in rows:
                en = f" ({r['name_en']})" if r["name_en"] else ""
                sem_info = f" [ปีที่ {r['year']} ภาคการศึกษาที่ {r['semester']}]"
                lines.append(f"- {r['code']} {r['name_th']}{en} — {r['credits_raw']}{sem_info}")
            total = sum(_parse_credit(r["credits_raw"]) for r in rows)
            lines.append(f"รวม {len(rows)} วิชา {total} หน่วยกิต")
            return StructuredResult(True, "\n".join(lines), version_label, "year_sem")

    # ── กรณี: ถามรายวิชาทั้งหมดของหลักสูตร ──
    rows = conn.execute(
        "SELECT code, name_th, name_en, credits_raw, year, semester "
        "FROM course WHERE version_id=? ORDER BY year, semester, code LIMIT 200",
        (version_id,),
    ).fetchall()
    if rows:
        lines = [f"รายวิชาของหลักสูตร {version_label} (ทั้งหมด {len(rows)} วิชา):"]
        for r in rows:
            en = f" ({r['name_en']})" if r["name_en"] else ""
            ys = ""
            if r["year"] and r["semester"]:
                ys = f" [ปีที่ {r['year']} ภาคการศึกษาที่ {r['semester']}]"
            lines.append(f"- {r['code']} {r['name_th']}{en} — {r['credits_raw']}{ys}")
        return StructuredResult(True, "\n".join(lines), version_label, "all_courses")

    return StructuredResult(False, "", "", "none")


def _parse_credit(credits_raw: str) -> int:
    m = re.match(r"(\d+)", credits_raw or "")
    return int(m.group(1)) if m else 0
