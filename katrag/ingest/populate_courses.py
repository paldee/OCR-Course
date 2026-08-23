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

# ชื่อภาษาอังกฤษ (ตัวพิมพ์ใหญ่) — อนุญาตเลขลำดับท้ายชื่อ เช่น "CALCULUS 1"
# แต่จะไม่ดูดเลขหน่วยกิตเข้ามา เพราะเราตัด block ที่ตำแหน่ง credits ก่อนแยกชื่อแล้ว
_EN_NAME_RE = re.compile(r"([A-Z][A-Z\s\-&,()]+(?:[A-Z)]|\d))")

# ชื่อไทย — ต้องรวมตัวเลขด้วย ("แคลคูลัส 1") ไม่งั้นเลขลำดับวิชาหาย
_TH_NAME_RE = re.compile(r"([ก-๙][ก-๙\s\d\-/().]*)")

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

        # ── แยก "ส่วนชื่อวิชา" ออกมาก่อน = ข้อความระหว่างรหัสวิชากับ credits ──
        # สำคัญ: ตัดที่ตำแหน่ง credits เพื่อไม่ให้เลขหน่วยกิตปนเข้าไปในชื่อ
        # เช่น "06026202 พีชคณิตเชิงเส้น LINEAR ALGEBRA 3 (3-0-6)"
        #      → name_segment = "พีชคณิตเชิงเส้น LINEAR ALGEBRA"
        code_pos = block.find(code)
        seg_start = code_pos + len(code)
        seg_end = credits_match.start()
        if seg_end <= seg_start:
            # credits อยู่ก่อนชื่อ (layout แปลก) → ใช้ทุกอย่างหลังรหัส
            name_segment = block[seg_start:].strip()
        else:
            name_segment = block[seg_start:seg_end].strip()

        # ค้นชื่ออังกฤษจากส่วนชื่อเท่านั้น
        en_matches = _EN_NAME_RE.findall(name_segment)
        name_en = ""
        for m in en_matches:
            candidate = " ".join(m.split())  # squeeze whitespace
            if len(candidate) > 5 and candidate.upper() != code:
                name_en = candidate
                break

        # ค้นชื่อไทย = ข้อความไทย (รวมเลขลำดับ) ที่นำหน้าในส่วนชื่อ
        name_th = ""
        thai_match = _TH_NAME_RE.match(name_segment)
        if thai_match:
            name_th = " ".join(thai_match.group(1).split())
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

    # ── Pre-pass: จับคู่ "รหัสวิชา → (ปี, ภาค)" ตามตำแหน่งในข้อความของหน้า ──
    # แผนการศึกษาบางหน้ามี header ปี/ภาค มากกว่าหนึ่งค่า (เช่น ปี 3 เทอม 1 และ
    # ปี 3 เทอม 2 อยู่หน้าเดียวกัน — พบ 52 หน้าในฐานข้อมูลนี้) ถ้าใช้ marker
    # ตัวสุดท้ายของหน้าเป็นค่าเดียวของทั้งหน้า วิชาในเทอมแรกจะได้ label ผิด
    # จึงต้องจับวิชาแต่ละตัวกับ marker "ตัวที่อยู่ก่อนมันใกล้สุด" ในข้อความ
    #
    # code_year_sem : (version_id, page, code) -> (year, semester)
    # page_year_sem : (version_id, page) -> (year, semester)  ใช้เป็น fallback
    #                 เฉพาะหน้าที่มี marker ค่าเดียว
    code_year_sem: dict[tuple[int, int, str], tuple[int, int]] = {}
    page_year_sem: dict[tuple[int, int], tuple[int, int]] = {}

    page_chunks = conn.execute("""
        SELECT chunk_id, text, page_number, version_id FROM chunk
        ORDER BY version_id, page_number, chunk_id
    """).fetchall()

    # รวม text ของ chunk ทั้งหมดในหน้าเดียวกัน (เรียงตาม chunk_id = ลำดับในหน้า)
    page_text_map: dict[tuple[int, int], str] = {}
    for ch in page_chunks:
        key = (ch["version_id"], ch["page_number"])
        page_text_map[key] = page_text_map.get(key, "") + "\n" + (ch["text"] or "")

    for (vid, pg), ptext in page_text_map.items():
        markers = [
            (m.start(), int(m.group(1)), int(m.group(2)))
            for m in _YEAR_SEM_RE.finditer(ptext)
        ]
        if not markers:
            continue

        distinct = {(y, s) for _, y, s in markers}
        if len(distinct) == 1:
            page_year_sem[(vid, pg)] = next(iter(distinct))

        # จับวิชากับ marker ที่อยู่ก่อนมันใกล้สุด
        for cm in _CODE_RE.finditer(ptext):
            pos = cm.start()
            chosen: tuple[int, int] | None = None
            for mpos, yr, sem in markers:
                if mpos < pos:
                    chosen = (yr, sem)
                else:
                    break
            if chosen is not None:
                code_year_sem.setdefault((vid, pg, cm.group(1)), chosen)

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

        # ใช้ year/sem เฉพาะจาก marker บนหน้านั้นเอง (ไม่ carry-forward)
        ys = page_year_sem.get((version_id, page_number))
        cy = ys[0] if ys else None
        cs = ys[1] if ys else None

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
            # ลำดับความน่าเชื่อถือของ ปี/ภาค:
            #   1. map ที่จับคู่ตามตำแหน่งในหน้า (แม่นสุด รองรับหน้าที่มีหลายเทอม)
            #   2. marker ที่พบใน chunk ตอน parse
            #   3. marker เดียวของหน้า (fallback)
            pos_ys = code_year_sem.get((version_id, page_number, course.code))
            if pos_ys:
                final_year, final_sem = pos_ys
            else:
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
