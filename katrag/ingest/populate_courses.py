"""Populate course + plan_slot tables จาก text layer ที่สกัดแล้ว.

Strategy: scan chunks ที่มีรหัสวิชา 8 หลัก + ชื่อไทย/อังกฤษ + credits
แล้ว insert ลง course/plan_slot พร้อม provenance

Tables populated:
- course (code, name_th, name_en, credits_total, credits_lecture, credits_lab, credits_self_study, version_id)
- plan_slot (version_id, course_id, year, semester, plan_variant)
- table_cell (page_id, table_index, row_index, col_index, text, bbox) — simplified

Usage: python -m katrag.ingest.populate_courses
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ParsedCourse:
    code: str
    name_th: str
    name_en: str
    credits_total: int
    credits_lecture: int
    credits_lab: int
    credits_self_study: int
    version_id: int
    page_number: int
    document_id: str
    year: int | None = None
    semester: int | None = None


# ── Regex patterns ──

# รหัสวิชา 8 หลัก
_CODE_RE = re.compile(r"\b(\d{8})\b")

# หน่วยกิต รูปแบบ 3(3-0-6) หรือ 3 (3-0-6) หรือ 3(2-2-5)
_CREDITS_RE = re.compile(r"(\d)\s*\((\d)-(\d)-(\d+)\)")

# ชื่อภาษาอังกฤษ (ตัวพิมพ์ใหญ่ ≥3 คำ)
_EN_NAME_RE = re.compile(r"([A-Z][A-Z\s\-&,()]+(?:[A-Z)]|\d))")

# ปี/เทอม: "ปีที่ 1 ภาคการศึกษาที่ 1"
_YEAR_SEM_RE = re.compile(r"ปีที่\s*(\d)\s*ภาคการศึกษาที่\s*(\d)")


def parse_courses_from_text(
    text: str,
    version_id: int,
    page_number: int,
    document_id: str,
    current_year: int | None = None,
    current_semester: int | None = None,
) -> list[ParsedCourse]:
    """Parse course records จาก text ของหนึ่งหน้า/chunk."""
    courses: list[ParsedCourse] = []

    # ตรวจจับ ปี/เทอม จากข้อความก่อน
    year_sem_match = _YEAR_SEM_RE.search(text)
    if year_sem_match:
        current_year = int(year_sem_match.group(1))
        current_semester = int(year_sem_match.group(2))

    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        code_match = _CODE_RE.search(line)
        if not code_match:
            # ตรวจ ปี/ภาค ในบรรทัดนี้
            ys = _YEAR_SEM_RE.search(line)
            if ys:
                current_year = int(ys.group(1))
                current_semester = int(ys.group(2))
            i += 1
            continue

        code = code_match.group(1)

        # รวบรวมข้อความหลายบรรทัดจนเจอ credits หรือรหัสถัดไป
        block_lines = [line]
        j = i + 1
        while j < len(lines) and j < i + 6:
            next_line = lines[j].strip()
            if not next_line:
                j += 1
                continue
            if _CODE_RE.match(next_line) and next_line != line:
                break
            block_lines.append(next_line)
            j += 1

        block = " ".join(block_lines)

        # ค้น credits
        credits_match = _CREDITS_RE.search(block)
        if not credits_match:
            i = j
            continue

        total = int(credits_match.group(1))
        lecture = int(credits_match.group(2))
        lab = int(credits_match.group(3))
        self_study = int(credits_match.group(4))

        # ค้นชื่ออังกฤษ
        en_matches = _EN_NAME_RE.findall(block)
        name_en = ""
        for m in en_matches:
            candidate = m.strip()
            if len(candidate) > 5 and candidate.upper() != code:
                name_en = candidate
                break

        # ค้นชื่อไทย: ข้อความระหว่าง code กับชื่อ EN หรือ credits
        code_pos = block.find(code)
        after_code = block[code_pos + 8:].strip()
        # ชื่อไทย = ข้อความไทยก่อน EN name หรือ credits
        name_th = ""
        thai_match = re.match(r"([ก-๙\s\-/().]+)", after_code)
        if thai_match:
            name_th = thai_match.group(1).strip()
            # ตัดคำสั้นเกินไปออก
            if len(name_th) < 3:
                name_th = ""

        if not name_th and not name_en:
            i = j
            continue

        courses.append(ParsedCourse(
            code=code,
            name_th=name_th[:255] if name_th else name_en[:255],
            name_en=name_en[:255] if name_en else "",
            credits_total=total,
            credits_lecture=lecture,
            credits_lab=lab,
            credits_self_study=self_study,
            version_id=version_id,
            page_number=page_number,
            document_id=document_id,
            year=current_year,
            semester=current_semester,
        ))

        i = j

    return courses


def populate(db_path: Path | str) -> dict[str, int]:
    """สแกนทุก chunk แล้ว populate course + plan_slot tables.

    Returns dict of counts: courses_inserted, plan_slots_inserted, pages_scanned.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    # ลบข้อมูลเก่า (idempotent)
    conn.execute("DELETE FROM plan_slot")
    conn.execute("DELETE FROM course_field_provenance")
    conn.execute("DELETE FROM course")

    # ── Pre-pass: สร้าง map page_number → (year, semester) ต่อ version ──
    # สแกนทุก chunk เพื่อหา "ปีที่ X ภาคการศึกษาที่ Y"
    page_year_sem: dict[tuple[int, int], tuple[int, int]] = {}  # (version_id, page) -> (year, sem)
    all_chunks = conn.execute("""
        SELECT text, page_number, version_id FROM chunk ORDER BY version_id, page_number
    """).fetchall()
    last_ys: dict[int, tuple[int, int]] = {}  # version_id -> last seen (year, sem)
    for ch in all_chunks:
        text = ch["text"] or ""
        vid = ch["version_id"]
        pg = ch["page_number"]
        for m in _YEAR_SEM_RE.finditer(text):
            yr, sem = int(m.group(1)), int(m.group(2))
            last_ys[vid] = (yr, sem)
            page_year_sem[(vid, pg)] = (yr, sem)
        # ถ้าหน้านี้ยังไม่มี → ใช้ค่าล่าสุด (carry forward)
        if (vid, pg) not in page_year_sem and vid in last_ys:
            page_year_sem[(vid, pg)] = last_ys[vid]

    # ดึง chunks ที่มีรหัสวิชา
    rows = conn.execute("""
        SELECT c.chunk_id, c.text, c.page_number, c.document_id, c.version_id
        FROM chunk c
        WHERE c.text GLOB '*[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]*'
        ORDER BY c.version_id, c.page_number
    """).fetchall()

    seen_codes: dict[tuple[str, int], int] = {}  # (code, version_id) -> course_id
    courses_inserted = 0
    plan_slots_inserted = 0
    current_year_per_version: dict[int, int | None] = {}
    current_sem_per_version: dict[int, int | None] = {}

    for row in rows:
        text = row["text"]
        version_id = row["version_id"]
        page_number = row["page_number"]
        document_id = row["document_id"]

        # ใช้ year/sem จาก pre-pass map
        ys = page_year_sem.get((version_id, page_number))
        cy = ys[0] if ys else current_year_per_version.get(version_id)
        cs = ys[1] if ys else current_sem_per_version.get(version_id)

        parsed = parse_courses_from_text(
            text,
            version_id,
            page_number,
            document_id,
            current_year=cy,
            current_semester=cs,
        )

        for course in parsed:
            # อัพเดท current year/sem
            if course.year:
                current_year_per_version[version_id] = course.year
            if course.semester:
                current_sem_per_version[version_id] = course.semester

            key = (course.code, course.version_id)
            # ใช้ year/sem จาก course หรือ fallback จาก pre-pass map
            final_year = course.year or cy
            final_sem = course.semester or cs

            if key not in seen_codes:
                # Insert course
                try:
                    # สร้าง provenance record สำหรับ course นี้
                    prov_cur = conn.execute(
                        """INSERT INTO provenance (document_id, page_number, x0, y0, x1, y1,
                           span_start, span_end, extraction_method, provenance_source)
                           VALUES (?, ?, 0.0, 0.0, 595.0, 842.0, 0, ?, 'text_layer', 'document_text')""",
                        (course.document_id, course.page_number, len(course.name_th)),
                    )
                    prov_id = prov_cur.lastrowid

                    credits_raw = f"{course.credits_total}({course.credits_lecture}-{course.credits_lab}-{course.credits_self_study})"
                    cur = conn.execute(
                        """INSERT INTO course (version_id, code, name_th, name_en,
                           credits_total, credits_lecture, credits_lab, credits_self_study,
                           credits_raw, year, semester, category, type,
                           prerequisite_json, prerequisite_raw,
                           flexible_year_semester, note, provenance_id)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, '[]', '', 0, '', ?)""",
                        (course.version_id, course.code, course.name_th, course.name_en,
                         course.credits_total, course.credits_lecture, course.credits_lab,
                         course.credits_self_study, credits_raw,
                         final_year, final_sem, prov_id),
                    )
                    seen_codes[key] = cur.lastrowid
                    courses_inserted += 1
                except sqlite3.IntegrityError:
                    # duplicate — skip
                    continue
            else:
                # Course exists — update year/sem ถ้าเดิมเป็น NULL
                if final_year and final_sem:
                    course_id = seen_codes[key]
                    conn.execute(
                        "UPDATE course SET year=?, semester=? WHERE course_id=? AND year IS NULL",
                        (final_year, final_sem, course_id),
                    )

            # Insert plan_slot ถ้ามี year/semester
            course_id = seen_codes.get(key)
            yr = course.year or current_year_per_version.get(version_id)
            sem = course.semester or current_sem_per_version.get(version_id)

            if course_id and yr and sem:
                try:
                    conn.execute(
                        """INSERT OR IGNORE INTO plan_slot
                           (version_id, course_id, year, semester, plan_variant)
                           VALUES (?, ?, ?, ?, 'default')""",
                        (course.version_id, course_id, yr, sem),
                    )
                    plan_slots_inserted += 1
                except sqlite3.IntegrityError:
                    pass

    conn.commit()
    conn.close()

    return {
        "courses_inserted": courses_inserted,
        "plan_slots_inserted": plan_slots_inserted,
        "pages_scanned": len(rows),
    }


if __name__ == "__main__":
    db = Path(__file__).resolve().parent.parent.parent / "artifacts" / "katrag.sqlite3"
    print(f"Populating courses from: {db}")
    result = populate(db)
    print(f"Done! Courses: {result['courses_inserted']}, "
          f"Plan slots: {result['plan_slots_inserted']}, "
          f"Pages scanned: {result['pages_scanned']}")
