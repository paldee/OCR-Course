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

# ── Category / Type headers ──
# category: "หมวดวิชาศึกษาทั่วไป", "หมวดวิชาเฉพาะ", "หมวดวิชาเลือกเสรี"
_CATEGORY_RE = re.compile(r"(หมวดวิชา(?:ศึกษาทั่วไป|เฉพาะ|เลือกเสรี|เสรี))")
# type: "วิชาบังคับ", "วิชาเลือก" — ต้องไม่ใช่ "วิชาเลือกเสรี" (เป็น category)
_TYPE_RE = re.compile(r"(วิชาบังคับ|วิชาเลือก)(?!เสรี)")

# normalize หมวดวิชาเสรี → หมวดวิชาเลือกเสรี (ตาม config/value_sets.toml)
_CATEGORY_NORM: dict[str, str] = {
    "หมวดวิชาศึกษาทั่วไป": "หมวดวิชาศึกษาทั่วไป",
    "หมวดวิชาเฉพาะ": "หมวดวิชาเฉพาะ",
    "หมวดวิชาเลือกเสรี": "หมวดวิชาเลือกเสรี",
    "หมวดวิชาเสรี": "หมวดวิชาเลือกเสรี",
}
# "วิชาบังคับ" → "บังคับ", "วิชาเลือก" → "เลือก" (ตาม value_sets.toml)
_TYPE_NORM: dict[str, str] = {
    "วิชาบังคับ": "บังคับ",
    "วิชาเลือก": "เลือก",
}


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


_CONT_TOTAL_RE = re.compile(r"(?<![ก-๙])รวม\s*\|?\s*\d{1,2}(?![\d.])")
_CONT_CODE_RE = re.compile(r"(?<!\d)(\d{8})(?!\d)")


def repair_continuation_pages(conn: sqlite3.Connection) -> int:
    """ตารางแผนที่ล้นไปหน้าถัดไปโดยไม่มีหัว 'ปีที่ X ภาคการศึกษาที่ Y' ใช้ปี/ภาคของหน้าก่อน.

    หน้าต่อตารางไม่มี marker จึงไม่ถูกจับคู่ตอน pre-pass และวิชาในหน้านั้นไปได้ปี/ภาค
    จากหน้าอื่นที่เอ่ยถึงรหัสเดียวกัน (เช่น หน้าสรุปที่มีคำว่า "ปีที่ 4 ภาคการศึกษาที่ 2")
    — พบใน IT 2565 หน้า 36 ที่เป็นตารางปี 3 ภาค 2 ต่อจากหน้า 35

    เงื่อนไขแคบโดยตั้งใจ: ต้องเป็นหน้าที่อยู่ถัดจากหน้าที่มี marker ทันที, มีแถว "รวม N"
    ของตารางแผน และมีรหัสวิชาอย่างน้อย 2 ตัว คืนจำนวนวิชาที่แก้
    """
    ys_re = _YEAR_SEM_RE
    fixed = 0
    for version_id, document_id in conn.execute("SELECT version_id, document_id FROM document").fetchall():
        prev_last: tuple[int, int] | None = None
        pages = conn.execute(
            "SELECT page_number, page_text FROM page WHERE document_id=? ORDER BY page_number",
            (document_id,),
        ).fetchall()
        for page_no, text in pages:
            ms = list(ys_re.finditer(text))
            if ms:
                prev_last = (int(ms[-1].group(1)), int(ms[-1].group(2)))
                continue
            codes = set(_CONT_CODE_RE.findall(text))
            if prev_last and _CONT_TOTAL_RE.search(text) and len(codes) >= 2:
                year, sem = prev_last
                for code in codes:
                    cur = conn.execute(
                        "UPDATE course SET year=?, semester=? "
                        "WHERE version_id=? AND code=? AND (year IS NOT ? OR semester IS NOT ?)",
                        (year, sem, version_id, code, year, sem),
                    )
                    if cur.rowcount:
                        fixed += cur.rowcount
                        conn.execute(
                            "UPDATE plan_slot SET year=?, semester=? WHERE version_id=? AND course_id IN "
                            "(SELECT course_id FROM course WHERE version_id=? AND code=?)",
                            (year, sem, version_id, version_id, code),
                        )
            prev_last = None
    return fixed


#: ชื่ออังกฤษตัวพิมพ์ใหญ่ที่อาจขึ้นบรรทัดใหม่ (" | ") — ใช้เฉพาะตอนเติมชื่อที่หาย
#: (คนละตัวกับ _EN_NAME_RE ของตัวสกัดตารางด้านบน)
_EN_REPAIR_RE = re.compile(
    r"([A-Z][A-Z0-9&,'’/\-\.\(\) ]{4,}(?:\s*\|\s*[A-Z][A-Z0-9&,'’/\-\.\(\) ]{1,})*)"
)
_EN_REPAIR_STOP = ("PREREQUISITE", "NONE", "CO-REQUISITE")


def _english_after_code(text: str, code: str, name_th: str) -> str | None:
    """ชื่ออังกฤษที่ตามหลัง "รหัส ชื่อไทย [หน่วยกิต]" ในหน้าคำอธิบาย/ตารางรายวิชา."""
    for m in re.finditer(rf"(?<!\d){code}(?!\d)", text):
        win = text[m.end():m.end() + 260]
        th = (name_th or "")[:8]
        if th and th not in win[:120]:
            continue  # รหัสที่ถูกอ้าง (เช่นเป็น prerequisite) ไม่ใช่หัวรายวิชา
        cut = re.search(r"วิชาบังคับก่อน|PREREQUISITE", win)
        zone = win[:cut.start()] if cut else win
        for em in _EN_REPAIR_RE.finditer(zone):
            cand = re.sub(r"\s{2,}", " ", re.sub(r"\s*\|\s*", " ", em.group(1))).strip(" -,.")
            if len(cand) >= 6 and re.search(r"[A-Z]{2,}", cand) and not any(w in cand for w in _EN_REPAIR_STOP):
                return cand
    return None


def repair_english_names(conn: sqlite3.Connection) -> int:
    """เติมชื่ออังกฤษให้วิชาที่ name_en ว่าง จากข้อความในเล่มของเวอร์ชันเดียวกัน.

    ตัวสกัดตารางบางรูปแบบ (IT 2565, AIT 2566) เก็บชื่อไทยได้แต่ชื่ออังกฤษหาย ทำให้ถาม
    ชื่ออังกฤษหรือค้นวิชาด้วยชื่ออังกฤษไม่เจอ (เช่น 06016420 INFRASTRUCTURE SYSTEMS AND SERVICES)
    ใช้หน้า text layer ก่อน หน้า OCR รับเฉพาะชื่อที่ทุกคำเคยปรากฏในชื่อวิชาอังกฤษที่มีอยู่แล้ว
    (กันชื่อเพี้ยนจาก OCR เช่น "MODERN REPRENE") วนจนไม่มีชื่อใหม่ เพราะชื่อที่เติมได้จาก
    text layer เพิ่มคำศัพท์ให้รอบถัดไปตรวจหน้า OCR ได้มากขึ้น
    """
    total = 0
    for _ in range(5):
        n = _fill_english_names_once(conn)
        total += n
        if not n:
            break
    return total


def _fill_english_names_once(conn: sqlite3.Connection) -> int:
    vocab = {
        w for (n,) in conn.execute("SELECT name_en FROM course WHERE coalesce(trim(name_en),'') != ''")
        for w in re.findall(r"[A-Z]{2,}", (n or "").upper())
    }
    filled = 0
    for (vid,) in conn.execute("SELECT version_id FROM curriculum_version").fetchall():
        texts = {"text_layer": [], "ocr": []}
        for method, text in conn.execute(
            "SELECT p.extraction_method, p.page_text FROM page p JOIN document d ON d.document_id = p.document_id "
            "WHERE d.version_id = ? ORDER BY p.page_number",
            (vid,),
        ):
            texts["text_layer" if method == "text_layer" else "ocr"].append(text or "")
        text_blob, ocr_blob = "\n".join(texts["text_layer"]), "\n".join(texts["ocr"])
        empty = conn.execute(
            "SELECT code, name_th FROM course WHERE version_id = ? AND coalesce(trim(name_en),'') = ''", (vid,)
        ).fetchall()
        for code, name_th in empty:
            en = _english_after_code(text_blob, code, name_th)
            if not en:
                cand = _english_after_code(ocr_blob, code, name_th)
                words = re.findall(r"[A-Z]{2,}", cand or "")
                if cand and words and all(w in vocab for w in words):
                    en = cand
            if en:
                conn.execute("UPDATE course SET name_en = ? WHERE version_id = ? AND code = ?", (en, vid, code))
                filled += 1
    return filled


def _single_plan_blocks(conn: sqlite3.Connection, version_id: int) -> list[tuple]:
    """บล็อกตารางแผนของหลักสูตรที่มีแผนเดียว: แบ่งข้อความหน้า 15-60 ตามหัว "ปีที่ X ภาคการศึกษาที่ Y"."""
    doc = conn.execute(
        "SELECT document_id FROM document WHERE version_id=? AND degree_level='bachelor'", (version_id,)
    ).fetchone()
    if not doc:
        return []
    out = []
    for page_no, text in conn.execute(
        "SELECT page_number, page_text FROM page WHERE document_id=? AND page_number BETWEEN 15 AND 60 "
        "ORDER BY page_number", (doc[0],)
    ):
        heads = list(_YEAR_SEM_RE.finditer(text or ""))
        for i, m in enumerate(heads):
            end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
            out.append(("default", int(m.group(1)), int(m.group(2)), page_no, doc[0], text[m.end():end]))
    return out


def _lenient_plan_rows(text: str, version_id: int, page: int, document_id: str, skip: set[str]) -> list[ParsedCourse]:
    """แถวตารางแผนที่ตัวอ่านหลักอ่านไม่ได้ — "รหัส | หรือ | ชื่อ" และคู่ "หรือ" ที่ใช้หน่วยกิตร่วมกัน.

    เช่น DSBA 2565 แผนสหกิจ: "06026259 | หรือ | สหกิจศึกษา... | COOPERATIVE ... | 6 (0-35-0) |
    06026260 สหกิจศึกษาต่างประเทศ... | OVERSEA COOPERATIVE ..." (06026260 ไม่มีหน่วยกิตของตัวเอง)
    """
    flat = re.sub(r"\s*\n\s*", " | ", text)
    codes = [(m.group(1), m.start(), m.end()) for m in re.finditer(r"(?<!\d)(\d{8})(?!\d)", flat)]
    credit_re = re.compile(r"(\d{1,2})\s*\((\d{1,2})-(\d{1,2})-(\d{1,2})\)")  # สหกิจ 6 (0-35-0)
    out: list[ParsedCourse] = []
    last_credit = None
    pending: str | None = None  # รหัสแรกของคู่ "A หรือ B ชื่อA หรือ ชื่อB"
    for i, (code, s, e) in enumerate(codes):
        seg_end = codes[i + 1][1] if i + 1 < len(codes) else len(flat)
        seg = re.split(r"\|\s*รวม\s*\|", flat[e:seg_end])[0]
        cm = credit_re.search(seg)
        if cm:
            last_credit = cm
        body = seg[:cm.start()] if cm else seg
        names = _names_split_by_or(body)
        if not names:
            # รหัสที่ตามด้วย "หรือ" ทันที: ชื่อของมันอยู่หลังรหัสถัดไป
            pending = code if "หรือ" in body else None
            continue
        targets = [pending, code] if pending and len(names) >= 2 else [code]
        pending = None
        # คู่ "หรือ": ไม่มีหน่วยกิตของตัวเอง ใช้ของวิชาก่อนหน้าที่อยู่ติดกันใน "หรือ" เดียวกัน
        credit = cm or (last_credit if "หรือ" in flat[max(0, s - 400):s] else None)
        if not credit:
            continue
        for target, (th, en) in zip(targets, names):
            if target in skip:
                continue
            out.append(ParsedCourse(
                code=target, name_th=th, name_en=en,
                credits_total=int(credit.group(1)), credits_lecture=int(credit.group(2)),
                credits_lab=int(credit.group(3)), credits_self_study=int(credit.group(4)),
                version_id=version_id, page_number=page, document_id=document_id,
            ))
    return out


def _names_split_by_or(body: str) -> list[tuple[str, str]]:
    """แยก (ชื่อไทย, ชื่ออังกฤษ) ของแถว — ตัดเป็นหลายชื่อตรงคำว่า "หรือ" ข้ามหัวข้อแขนง/กลุ่ม."""
    groups: list[tuple[list[str], list[str]]] = [([], [])]
    for raw in body.split("|"):
        tok = raw.strip()
        if not tok:
            continue
        trailing_or = tok.endswith(" หรือ")
        tok = tok[: -len(" หรือ")].strip() if trailing_or else tok
        if tok == "หรือ":
            if groups[-1][0]:
                groups.append(([], []))
            continue
        if re.match(r"(แขนงวิชา|กลุ่มวิชา)", tok):
            continue
        if re.match(r"[ก-๙]", tok):
            groups[-1][0].append(tok)
        elif re.match(r"[A-Za-z]", tok):
            groups[-1][1].append(tok)
        if trailing_or and groups[-1][0]:
            groups.append(([], []))
    return [(" ".join(th), " ".join(en)) for th, en in groups if th]


def repair_missing_plan_courses(conn: sqlite3.Connection) -> int:
    """เติมวิชาที่อยู่ในตารางแผนการศึกษาแต่ไม่มีในตาราง course.

    ตัวสกัดหลักอ่านจาก chunk แถวตารางที่ถูกตัดคร่อม chunk (รหัสอยู่ชิ้นหนึ่ง หน่วยกิตอยู่อีกชิ้น)
    จึงหายไป เช่น IT 2565 06066102 (ปี 3 ภาค 1), AIT 2566 90641008 (ปี 1 ภาค 1)
    ขั้นนี้อ่านบล็อกตารางแผนจากข้อความทั้งหน้า (plan_variants) แล้วเติมเฉพาะรหัสที่ยังไม่มี
    วิชาที่พบเฉพาะในแผนสหกิจไม่ใส่ปี/ภาค เพราะคำตอบ "ปี X ภาค Y เรียนอะไร" อ่านปี/ภาคจาก
    ตาราง course และหมายถึงแผนปกติ (ปี/ภาคของแผนสหกิจตอบจาก plan_variants อยู่แล้ว)
    """
    from katrag.query.plan_variants import _parse_blocks

    inserted = 0
    for (vid,) in conn.execute("SELECT version_id FROM curriculum_version").fetchall():
        blocks = [(b.variant, b.year, b.semester, b.page, b.document_id, b.text) for b in _parse_blocks(conn, vid)]
        if not blocks:
            blocks = _single_plan_blocks(conn, vid)  # หลักสูตรแผนเดียว (AIT) ไม่มีหัว แผนปกติ/แผนสหกิจ
        found: dict[str, tuple[ParsedCourse, str, int, int]] = {}
        for variant, year, sem, page, doc_id, text in blocks:
            parsed = parse_courses_from_text(text, vid, page, doc_id, current_year=year, current_semester=sem)
            parsed += _lenient_plan_rows(text, vid, page, doc_id, {c.code for c in parsed})
            for c in parsed:
                prev = found.get(c.code)
                # แผนปกติชนะแผนสหกิจ เมื่อวิชาเดียวกันอยู่ทั้งสองแผน และในแผนเดียวกันเลือกแถวที่ชื่อสั้นสุด
                # (บางหน้าวางคู่ "หรือ" โดยไม่มีคำว่า หรือ คั่นชื่อ ทำให้ชื่อสองวิชาต่อกัน)
                if prev is None or (prev[1] != "default" and variant == "default"):
                    found[c.code] = (c, variant, year, sem)
        # ชื่อจากแถวรายการวิชาแบบตรงตัว "รหัส | ชื่อไทย | ชื่ออังกฤษ | หน่วยกิต" ถ้ามี
        # (ตารางแผนบางหน้าวางคู่ "หรือ" โดยไม่มีคำคั่นชื่อ ทำให้ชื่อสองวิชาต่อกัน)
        blob = " | ".join(
            re.sub(r"\s*\n\s*", " | ", t or "")
            for (t,) in conn.execute(
                "SELECT p.page_text FROM page p JOIN document d ON d.document_id = p.document_id "
                "WHERE d.version_id = ? ORDER BY p.page_number", (vid,)
            )
        )
        for code, (c, variant, year, sem) in found.items():
            if conn.execute("SELECT 1 FROM course WHERE version_id=? AND code=?", (vid, code)).fetchone():
                continue
            direct = re.search(
                rf"(?<!\d){code}(?!\d)\s*\|\s*([ก-๙][^|]*?)\s*\|\s*([A-Z][^|]*?(?:\|\s*[A-Z][^|]*?)?)\s*\|\s*\d{{1,2}}\s*\(",
                blob,
            )
            if direct:
                c.name_th = direct.group(1).strip()
                c.name_en = re.sub(r"\s*\|\s*", " ", direct.group(2)).strip()
            prov = conn.execute(
                """INSERT INTO provenance (document_id, page_number, x0, y0, x1, y1,
                   span_start, span_end, extraction_method, provenance_source)
                   VALUES (?, ?, 0.0, 0.0, 595.0, 842.0, 0, ?, 'text_layer', 'document_text')""",
                (c.document_id, c.page_number, len(c.name_th)),
            ).lastrowid
            y, s = (year, sem) if variant == "default" else (None, None)
            credits_raw = f"{c.credits_total}({c.credits_lecture}-{c.credits_lab}-{c.credits_self_study})"
            # schema จำกัดค่าย่อย 0-30 แต่บางวิชาในเล่มเกิน (สหกิจ 6(0-35-0), 0(0-0-45))
            # เก็บค่าจริงไว้ใน credits_raw และเว้นช่องที่เกินเป็น NULL

            def fit(v):
                return v if v is not None and 0 <= v <= 30 else None

            course_id = conn.execute(
                """INSERT INTO course (version_id, code, name_th, name_en,
                   credits_total, credits_lecture, credits_lab, credits_self_study,
                   credits_raw, year, semester, category, type,
                   prerequisite_json, prerequisite_raw, flexible_year_semester, note, provenance_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, '[]', '', 0, '', ?)""",
                (vid, code, c.name_th, c.name_en, fit(c.credits_total), fit(c.credits_lecture),
                 fit(c.credits_lab), fit(c.credits_self_study), credits_raw, y, s, prov),
            ).lastrowid
            conn.execute(
                """INSERT OR IGNORE INTO plan_slot (version_id, course_id, year, semester, plan_variant, provenance_id)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (vid, course_id, year, sem, variant, prov),
            )
            inserted += 1
    return inserted


_FACULTY_GROUP_RE = re.compile(
    r"(?<!\d)(\d{8})\s*\|\s*กลุ่มวิชาที่กำหนดโดยคณะ\*?\s*\|\s*([ก-๙][^|]*?)\s*\|\s*([A-Z][^|]*?)\s*\|"
)


def repair_faculty_group_names(conn: sqlite3.Connection) -> int:
    """ชื่อวิชาในแถว "รหัส | กลุ่มวิชาที่กำหนดโดยคณะ* | ชื่อไทย | ชื่ออังกฤษ" ของตารางแผน.

    รูปแบบนี้ทำให้ตัวสกัดหลักเอาชื่อกลุ่มเป็นชื่อวิชา หรือเลื่อนชื่อไปผูกกับรหัสข้างเคียง
    (BIT 2565: 96643021 คือ ผู้ประกอบการสมัยใหม่ แต่ตารางได้ชื่อ กฎหมายสำหรับคนรุ่นใหม่ ซึ่งเป็นของ 96642033)
    """
    fixed = 0
    for (vid,) in conn.execute("SELECT version_id FROM curriculum_version").fetchall():
        names: dict[str, tuple[str, str]] = {}
        for (text,) in conn.execute(
            "SELECT p.page_text FROM page p JOIN document d ON d.document_id = p.document_id WHERE d.version_id = ?",
            (vid,),
        ):
            flat = re.sub(r"\s*\n\s*", " | ", text or "")
            for m in _FACULTY_GROUP_RE.finditer(flat):
                names[m.group(1)] = (m.group(2).strip(), m.group(3).strip())
        for code, (th, en) in names.items():
            cur = conn.execute(
                "UPDATE course SET name_th = ?, name_en = ?, note = 'กลุ่มวิชาที่กำหนดโดยคณะ' "
                "WHERE version_id = ? AND code = ? AND (name_th IS NOT ? OR name_en IS NOT ?)",
                (th, en, vid, code, th, en),
            )
            fixed += cur.rowcount
    return fixed


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

    # ── Pre-pass (2): จับคู่ "รหัสวิชา → (category, type)" ──
    # สแกนหน้าที่มี header หมวดวิชา/วิชาบังคับ/วิชาเลือก แล้วจับคู่กับรหัสวิชา
    # ที่ตามหลัง — carry-forward ข้ามหน้าภายใน version เดียวกัน เพราะ PDF
    # จัดหมวด: header ปรากฏหน้าแรกแล้ว list รายวิชาต่อเนื่องไปอีกหลายหน้า
    code_cat_type: dict[tuple[int, str], tuple[str | None, str | None]] = {}

    pages_by_version: dict[int, list[tuple[int, str]]] = {}
    for (vid, pg), ptext in page_text_map.items():
        pages_by_version.setdefault(vid, []).append((pg, ptext))
    for vid in pages_by_version:
        pages_by_version[vid].sort(key=lambda x: x[0])

    # แยกการเก็บ category กับ type เพราะมีลำดับความน่าเชื่อถือต่างกัน:
    #   category: carry-forward ทำงานดี (หมวดวิชาเป็นบล็อกใหญ่ต่อเนื่องหลายหน้า)
    #   type: carry-forward ทำ "วิชาเลือก" จากหน้าโครงสร้างรั่วไปทับวิชาบังคับ
    #         ในหน้าแผนการเรียน จึงต้องใช้สัญญาณจากหน้าแผนเป็นหลัก
    #
    # หน้าแผนการเรียน (มี year/sem marker) ประกอบด้วยวิชาบังคับเป็นหลัก
    # และวิชาเลือกจะมี header "วิชาเลือก" ชัดเจนบนหน้าเดียวกัน — ดังนั้น
    # บนหน้าแผน ถ้าไม่มี type marker ก่อนรหัสวิชา → default "บังคับ"
    #
    # เก็บ type แยกเป็น 2 ระดับความมั่นใจ:
    #   type_from_plan  : มาจากหน้าแผน (แม่นสุด — ทับได้)
    #   type_from_carry : มาจาก carry-forward หน้าโครงสร้าง (ใช้เป็น fallback)
    cat_map: dict[tuple[int, str], str] = {}
    type_from_plan: dict[tuple[int, str], str] = {}
    type_from_carry: dict[tuple[int, str], str] = {}

    for vid, vpages in pages_by_version.items():
        carry_cat: str | None = None
        carry_type: str | None = None

        for pg, ptext in vpages:
            cat_markers = [
                (m.start(), _CATEGORY_NORM.get(m.group(1), m.group(1)))
                for m in _CATEGORY_RE.finditer(ptext)
            ]
            type_markers = [
                (m.start(), _TYPE_NORM.get(m.group(1), m.group(1)))
                for m in _TYPE_RE.finditer(ptext)
            ]
            codes_on_page = _CODE_RE.findall(ptext)
            is_list_page = len(codes_on_page) >= 3
            is_plan_page = bool(_YEAR_SEM_RE.search(ptext))

            if cat_markers:
                carry_cat = cat_markers[-1][1]
            if type_markers:
                carry_type = type_markers[-1][1]

            if not is_list_page:
                continue

            for cm in _CODE_RE.finditer(ptext):
                pos = cm.start()
                code = cm.group(1)
                key = (vid, code)

                # ── category ── (carry-forward ปกติ)
                page_cat: str | None = None
                for mpos, cval in cat_markers:
                    if mpos < pos:
                        page_cat = cval
                    else:
                        break
                cval = page_cat or carry_cat
                if cval and key not in cat_map:
                    cat_map[key] = cval

                # ── type marker ก่อน pos บนหน้านี้ ──
                page_type: str | None = None
                for mpos, tval in type_markers:
                    if mpos < pos:
                        page_type = tval
                    else:
                        break

                if is_plan_page:
                    # หน้าแผน: type จาก marker บนหน้า ไม่งั้น default "บังคับ"
                    resolved = page_type or "บังคับ"
                    # หน้าแผนแม่นสุด — เก็บค่าแรกที่เจอจากหน้าแผน
                    if key not in type_from_plan:
                        type_from_plan[key] = resolved
                else:
                    # หน้าโครงสร้าง: ใช้ marker บนหน้า/ carry เป็น fallback
                    resolved = page_type or carry_type
                    if resolved and key not in type_from_carry:
                        type_from_carry[key] = resolved

    # รวม: category + type (plan ชนะ carry)
    code_cat_type: dict[tuple[int, str], tuple[str | None, str | None]] = {}
    all_keys = set(cat_map) | set(type_from_plan) | set(type_from_carry)
    for key in all_keys:
        cat_val = cat_map.get(key)
        type_val = type_from_plan.get(key) or type_from_carry.get(key)
        code_cat_type[key] = (cat_val, type_val)

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

                    # category / type จาก pre-pass map
                    ct = code_cat_type.get((version_id, course.code))
                    cat_val = ct[0] if ct else None
                    type_val = ct[1] if ct else None

                    cur = conn.execute(
                        """INSERT INTO course (version_id, code, name_th, name_en,
                           credits_total, credits_lecture, credits_lab, credits_self_study,
                           credits_raw, year, semester, category, type,
                           prerequisite_json, prerequisite_raw,
                           flexible_year_semester, note, provenance_id)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', '', 0, '', ?)""",
                        (course.version_id, course.code, course.name_th, course.name_en,
                         course.credits_total, course.credits_lecture, course.credits_lab,
                         course.credits_self_study, credits_raw,
                         final_year, final_sem, cat_val, type_val, prov_id),
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

            # ── Insert plan_slot ถ้ามี year/semester ──
            # schema บังคับ provenance_id NOT NULL — ถ้าไม่ส่งมา INSERT OR IGNORE
            # จะข้ามเงียบทุกแถว (เคยทำให้ตารางว่างทั้งที่รายงานว่าใส่ 2,149 แถว)
            # จึงใช้ provenance ของ course แถวนั้น และนับจาก rowcount จริง
            # ใช้ค่าเดียวกับที่ลง course (จับ marker ตามตำแหน่งในหน้า)
            # ห้ามใช้ current_year_per_version ซึ่งเป็น carry-forward ข้ามหน้า —
            # วัดกับ GT แล้วพบว่ามันให้ y4s2 กับวิชาที่จริง ๆ อยู่ปี 1-2
            # (marker ตัวสุดท้ายของเล่มรั่วมาใส่วิชาที่ไม่มี marker บนหน้าตัวเอง)
            course_id = seen_codes.get(key)
            yr = final_year
            sem = final_sem

            if course_id and yr and sem:
                prov_row = conn.execute(
                    "SELECT provenance_id FROM course WHERE course_id=?", (course_id,)
                ).fetchone()
                prov_for_slot = prov_row["provenance_id"] if prov_row else None
                if prov_for_slot is not None:
                    cur_slot = conn.execute(
                        """INSERT OR IGNORE INTO plan_slot
                           (version_id, course_id, year, semester, plan_variant,
                            provenance_id)
                           VALUES (?, ?, ?, ?, 'default', ?)""",
                        (course.version_id, course_id, yr, sem, prov_for_slot),
                    )
                    plan_slots_inserted += cur_slot.rowcount or 0

    # ── Pass สุดท้าย: เติม year/semester ที่ยังว่าง จาก pre-pass map ──
    # วิชาบางตัวถูก insert จากหน้าคำอธิบายรายวิชา (ไม่มี marker) แล้วหน้าตารางแผน
    # ที่มี marker ไม่ถูก parse ซ้ำ เพราะตารางแผนบางแบบไม่มีหน่วยกิตรูป 3(3-0-6)
    # ให้ _CREDITS_RE จับได้ → วิชานั้นจึงไม่เคยได้ชั้นปี
    #
    # code_year_sem จาก pre-pass ครอบทุกหน้าที่มี "รหัสวิชา + marker" อยู่แล้ว
    # จึงใช้เติมได้ตรง ๆ ถ้าหลายหน้าให้ค่าไม่ตรงกัน เลือกค่าที่พบบ่อยที่สุด
    from collections import Counter

    votes: dict[tuple[int, str], Counter[tuple[int, int]]] = {}
    for (vid_k, _pg, code_k), ys_val in code_year_sem.items():
        votes.setdefault((vid_k, code_k), Counter())[ys_val] += 1

    backfilled = 0
    for (vid_k, code_k), counter in votes.items():
        (best_y, best_s), _n = counter.most_common(1)[0]
        cur_bf = conn.execute(
            "UPDATE course SET year=?, semester=? "
            "WHERE version_id=? AND code=? AND year IS NULL",
            (best_y, best_s, vid_k, code_k),
        )
        backfilled += cur_bf.rowcount or 0

        # plan_slot ต้องมาจาก "ตารางแผนการศึกษา" ซึ่งคือหน้าที่มี marker ปี/ภาค
        # ไม่ใช่จาก parse_courses_from_text (ตารางแผนหลายเล่มไม่มีหน่วยกิตรูป
        # 3(3-0-6) ให้ _CREDITS_RE จับ วิชาในแผนจึงหลุดไปเกือบทั้งหมด —
        # เคยเหลือแค่ 329 แถวจากที่ควรมีราว 1.5 พัน)
        row_bf = conn.execute(
            "SELECT course_id, provenance_id FROM course "
            "WHERE version_id=? AND code=?",
            (vid_k, code_k),
        ).fetchone()
        if row_bf and row_bf["provenance_id"] is not None:
            cur_slot = conn.execute(
                """INSERT OR IGNORE INTO plan_slot
                   (version_id, course_id, year, semester, plan_variant, provenance_id)
                   VALUES (?, ?, ?, ?, 'default', ?)""",
                (vid_k, row_bf["course_id"], best_y, best_s, row_bf["provenance_id"]),
            )
            plan_slots_inserted += cur_slot.rowcount or 0

    repaired = repair_continuation_pages(conn)
    missing_added = repair_missing_plan_courses(conn)
    faculty_fixed = repair_faculty_group_names(conn)
    en_filled = repair_english_names(conn)

    conn.commit()
    conn.close()

    return {
        "continuation_repaired": repaired,
        "missing_plan_courses_added": missing_added,
        "faculty_group_names_fixed": faculty_fixed,
        "english_names_filled": en_filled,
        "courses_inserted": courses_inserted,
        "plan_slots_inserted": plan_slots_inserted,
        "pages_scanned": len(rows),
        "year_backfilled": backfilled,
        "with_category": sum(1 for (_, c) in code_cat_type.items() if c[0]),
        "with_type": sum(1 for (_, c) in code_cat_type.items() if c[1]),
    }


if __name__ == "__main__":
    db = Path(__file__).resolve().parent.parent.parent / "artifacts" / "katrag.sqlite3"
    print(f"Populating courses from: {db}")
    result = populate(db)
    print(f"Done! Courses: {result['courses_inserted']}, "
          f"Plan slots: {result['plan_slots_inserted']}, "
          f"Pages scanned: {result['pages_scanned']}, "
          f"Year backfilled: {result['year_backfilled']}, "
          f"With category: {result['with_category']}, "
          f"With type: {result['with_type']}")
