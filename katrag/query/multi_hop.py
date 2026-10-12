"""คำถามหลายขั้นที่ตอบได้จากตาราง โดยคำนวณคำตอบเองไม่ผ่าน LLM.

- ลงวิชา X ตั้งแต่ปี N ได้ไหม → ปี/ภาคตามแผน + วิชาบังคับก่อน (และปีที่เรียน) → สรุปได้/ไม่ได้
- วิชา A กับวิชา B เรียนปีเดียวกันไหม → ปี/ภาคของทั้งสองวิชา
- วิชา X ของหลักสูตร P1 กับ P2 → ปี/ภาคของวิชาในแต่ละหลักสูตร
- ต้องผ่านอะไรมาก่อนบ้าง (นับรวมวิชาพื้นฐานของวิชานั้น) → วิชาบังคับก่อนไล่หลายชั้น
- เก็บได้ N หน่วยกิต เกรด G จบได้ไหม → เทียบหน่วยกิตรวมของหลักสูตรและเกรดขั้นต่ำ 2.00 (ข้อ 34)
- หลักสูตร P1 กับ P2 หน่วยกิตรวมต่างกันเท่าไร → หน่วยกิตรวมของฉบับปัจจุบันแต่ละหลักสูตร
- ปี Y ภาค S ตามแผนรวมกี่หน่วยกิต → บรรทัด 'รวม' ของภาคในตารางแผน (+ เทียบเพดาน 9–22 ถ้าถาม)
- ลงวิชา A พร้อมกับวิชา B ได้ไหม → ไม่ได้ ถ้าวิชาหนึ่งเป็นวิชาบังคับก่อนของอีกวิชา

ตัวตรวจแต่ละแบบแคบโดยตั้งใจ ถ้าไม่ตรงรูปแบบหรือหาวิชา/ค่าไม่ได้ คืน matched=False ให้ขั้นถัดไปตอบ
"""

from __future__ import annotations

import json
import re
import sqlite3

from katrag.query.structured_query import (
    StructuredResult, _course_scope_versions, _match_course_by_name, _versions_label,
    detect_program, detect_versions,
)
from katrag.query.program_codes import program_code_positions

_PREFIX_RE = re.compile(r"^\s*หลักสูตร\s+[A-Za-z]+\s*[:：]\s*")
_THAI_DIGIT = {"หนึ่ง": 1, "สอง": 2, "สาม": 3, "สี่": 4, "๑": 1, "๒": 2, "๓": 3, "๔": 4}
_YES_NO = ("ได้ไหม", "ได้มั้ย", "ได้หรือไม่", "ได้หรือเปล่า", "ได้รึเปล่า", "ได้ป่าว", "ได้เปล่า", "ได้ป่ะ", "ได้มั๊ย")
_NONE = StructuredResult(False, "", "", "none")


def _user_text(question: str) -> str:
    """ตัดคำนำ "หลักสูตร X:" ที่ pipeline เติมให้ (เป็นหลักสูตรที่เลือกในหน้าเว็บ ไม่ใช่ที่ผู้ใช้พิมพ์)."""
    return _PREFIX_RE.sub("", question)


def _programs_in(text: str) -> list[str]:
    return [c for _, c in program_code_positions(text)]


def _slot(r) -> str:
    if r["year"] and r["semester"]:
        return f"ปีที่ {r['year']} ภาคการศึกษาที่ {r['semester']}"
    return "ไม่ได้กำหนดปี/ภาคในแผนการศึกษา (วิชาเลือก)"


def _name(r) -> str:
    en = f" ({r['name_en']})" if r["name_en"] else ""
    return f"{r['code']} {r['name_th']}{en}"


def _course(conn: sqlite3.Connection, vid: int, code: str):
    return conn.execute(
        "SELECT code, name_th, name_en, credits_raw, year, semester, prerequisite_json, version_id "
        "FROM course WHERE version_id=? AND code=?", (vid, code),
    ).fetchone()


def _prereqs(conn: sqlite3.Connection, r) -> list:
    out = []
    for code in json.loads(r["prerequisite_json"] or "[]"):
        p = _course(conn, r["version_id"], code)
        if p is not None:
            out.append(p)
    return out


def _scope(conn: sqlite3.Connection, question: str) -> tuple[str | None, list[int]]:
    program = detect_program(question)
    if not program:
        return None, []
    return program, _course_scope_versions(conn, question, program)


# ── 1. ลงวิชา X ตั้งแต่ปี N ได้ไหม ─────────────────────────────────────

_TARGET_YEAR_RE = re.compile(r"(?:ตั้งแต่|ตอน|ใน|ช่วง|ระหว่าง)?\s*ปี\s*(?:ที่)?\s*([1-4]|หนึ่ง|สอง|สาม|สี่|[๑-๔])")


def try_can_take(conn: sqlite3.Connection, question: str) -> StructuredResult:
    text = _user_text(question)
    if not any(k in text for k in _YES_NO) or not re.search(r"ลง|เรียน", text):
        return _NONE
    m = _TARGET_YEAR_RE.search(text)
    if not m:
        return _NONE
    n = _THAI_DIGIT.get(m.group(1)) or int(m.group(1))
    program, vids = _scope(conn, question)
    rows = _match_course_by_name(conn, text[:m.start()] + " " + text[m.end():], vids) if vids else []
    if not rows:
        return _NONE
    c = rows[0]
    pre = _prereqs(conn, c)
    lines = [f"วิชา {_name(c)} — {c['credits_raw']} หน่วยกิต",
             f"ตามแผนการศึกษา: {_slot(c)}"]
    if pre:
        lines.append("วิชาบังคับก่อน (ต้องสอบผ่านก่อนจึงจะลงได้):")
        lines += [f"  - {_name(p)} — {_slot(p)}" for p in pre]
    else:
        lines.append("วิชาบังคับก่อน: ไม่มี")
    blocking = [p for p in pre if p["year"] and p["year"] >= n]
    if blocking:
        b = blocking[0]
        verdict = (f"ไม่ได้ — ต้องสอบผ่าน {b['code']} {b['name_th']} ก่อน ซึ่งตามแผนเรียนใน{_slot(b)} "
                   f"จึงยังไม่ผ่านวิชานี้ก่อนเริ่มปี {n}")
    elif c["year"] and c["year"] <= n:
        verdict = f"ได้ — ตามแผนวิชานี้อยู่ปีที่ {c['year']} อยู่แล้ว" + (" (ต้องผ่านวิชาบังคับก่อนข้างต้นก่อน)" if pre else "")
    elif c["year"]:
        verdict = (f"ไม่ตรงแผน — แผนการศึกษากำหนดไว้ปีที่ {c['year']} "
                   + ("ลงก่อนแผนได้เฉพาะเมื่อสอบผ่านวิชาบังคับก่อนข้างต้นแล้วและภาคนั้นเปิดสอนรายวิชานี้" if pre
                      else "วิชานี้ไม่มีวิชาบังคับก่อน แต่ต้องดูว่าภาคนั้นเปิดสอนรายวิชานี้"))
    else:
        verdict = "วิชานี้ไม่ได้กำหนดปีในแผน (วิชาเลือก) ลงได้เมื่อผ่านวิชาบังคับก่อนครบ" if pre else \
            "วิชานี้ไม่ได้กำหนดปีในแผน (วิชาเลือก) และไม่มีวิชาบังคับก่อน"
    lines.insert(0, f"คำตอบ: {verdict}")
    return StructuredResult(True, "\n".join(lines), _versions_label(conn, [c["version_id"]]), "can_take",
                            codes=[c["code"]] + [p["code"] for p in pre], version_id=c["version_id"])


# ── 2. วิชา A กับวิชา B เรียนปี/ภาคเดียวกันไหม ───────────────────────────

_SAME_TIME_RE = re.compile(
    r"ปีเดียวกัน|เทอมเดียวกัน|ภาคเดียวกัน|พร้อมกัน|ปีไหน|เทอมไหน|ภาคไหน|(?:วิชา|อัน|ตัว)ไหน\S{0,8}ก่อน|ก่อนหรือหลัง"
)


_CONCURRENT_RE = re.compile(r"พร้อมกัน|เทอมเดียวกัน|ภาคเดียวกัน|ควบคู่|ในคราวเดียว")


def _prereq_closure(conn: sqlite3.Connection, r, depth: int = 0) -> set[str]:
    """รหัสวิชาบังคับก่อนทุกชั้นของวิชา r."""
    out: set[str] = set()
    if depth > 5:
        return out
    for p in _prereqs(conn, r):
        if p["code"] not in out:
            out.add(p["code"])
            out |= _prereq_closure(conn, p, depth + 1)
    return out


def try_two_courses(conn: sqlite3.Connection, question: str) -> StructuredResult:
    text = _user_text(question)
    if not _SAME_TIME_RE.search(text) or "กับ" not in text or len(_programs_in(text)) >= 2:
        return _NONE
    program, vids = _scope(conn, question)
    if not vids:
        return _NONE
    left, right = text.split("กับ", 1)
    a = _match_course_by_name(conn, left, vids)
    b = _match_course_by_name(conn, right, vids)
    if not a or not b or a[0]["code"] == b[0]["code"]:
        return _NONE
    a, b = a[0], b[0]
    lines = [f"- {_name(a)} — {_slot(a)}", f"- {_name(b)} — {_slot(b)}"]
    # วิชาหนึ่งเป็นวิชาบังคับก่อนของอีกวิชา → ลงพร้อมกันในภาคเดียวไม่ได้
    first, then = (a, b) if a["code"] in _prereq_closure(conn, b) else (b, a) if b["code"] in _prereq_closure(conn, a) else (None, None)
    if first is not None:
        lines.append(f"- {then['code']} มีวิชาบังคับก่อนเป็น {first['code']} (ต้องสอบผ่าน {first['code']} ก่อนจึงลง {then['code']} ได้)")
    if first is not None and _CONCURRENT_RE.search(text):
        verdict = (f"ไม่ได้ — ต้องสอบผ่าน {first['code']} {first['name_th']} ก่อน "
                   f"จึงจะลง {then['code']} {then['name_th']} ได้ จึงลงพร้อมกันในภาคเดียวกันไม่ได้")
    elif a["year"] and b["year"]:
        if (a["year"], a["semester"]) == (b["year"], b["semester"]):
            verdict = f"เรียนปีและภาคเดียวกัน (ปีที่ {a['year']} ภาคการศึกษาที่ {a['semester']})"
        elif a["year"] == b["year"]:
            verdict = f"เรียนปีเดียวกัน (ปีที่ {a['year']}) แต่คนละภาค"
        else:
            first = a if (a["year"], a["semester"]) < (b["year"], b["semester"]) else b
            verdict = f"เรียนคนละปี — {first['name_th']} เรียนก่อน"
    else:
        verdict = "วิชาใดวิชาหนึ่งไม่ได้กำหนดปี/ภาคในแผน (วิชาเลือก) จึงเทียบปีไม่ได้"
    lines.insert(0, f"คำตอบ: {verdict}")
    return StructuredResult(True, "\n".join(lines), _versions_label(conn, [a["version_id"]]), "course_compare",
                            codes=[a["code"], b["code"]], version_id=a["version_id"])


# ── 3. วิชา X ของหลักสูตร P1 กับ P2 ─────────────────────────────────────

_WHEN_RE = re.compile(r"ปี|เทอม|ภาค|เรียนตอนไหน|เหมือนกัน|ต่างกัน|เดียวกัน")


def try_course_across_programs(conn: sqlite3.Connection, question: str) -> StructuredResult:
    text = _user_text(question)
    progs = list(dict.fromkeys(_programs_in(text)))
    if len(progs) < 2 or not _WHEN_RE.search(text) or "หน่วยกิตรวม" in text:
        return _NONE
    found = []
    for p in progs:
        vids = _course_scope_versions(conn, f"หลักสูตร {p}: {text}", p)
        rows = _match_course_by_name(conn, text, vids)
        if rows:
            found.append((p, rows[0]))
    if len(found) < 2:
        return _NONE
    lines = []
    for p, r in found:
        lines.append(f"- หลักสูตร {_versions_label(conn, [r['version_id']])}: {_name(r)} — {r['credits_raw']} | {_slot(r)}")
    slots = {(r["year"], r["semester"]) for _, r in found}
    if any(r["year"] is None for _, r in found):
        verdict = "บางหลักสูตรไม่ได้กำหนดปี/ภาคของวิชานี้ในแผน (วิชาเลือก)"
    elif len(slots) == 1:
        verdict = "เรียนปีและภาคเดียวกันในทุกหลักสูตรที่ถาม"
    else:
        verdict = "เรียนต่างปี/ภาคกัน — " + ", ".join(f"{p} ปีที่ {r['year']} ภาค {r['semester']}" for p, r in found)
    lines.insert(0, f"คำตอบ: {verdict}")
    return StructuredResult(True, "\n".join(lines), " / ".join(p for p, _ in found), "cross_program_course",
                            codes=[found[0][1]["code"]], version_id=found[0][1]["version_id"])


# ── 4. วิชาบังคับก่อนไล่หลายชั้น ─────────────────────────────────────────

_CHAIN_RE = re.compile(r"นับรวม|ทั้งหมด|ทุกวิชา|ทางอ้อม|ทั้งทางตรง|หลายชั้น|ไล่|ต่อเนื่อง|พื้นฐานของวิชานั้น|ทั้งสาย|ทั้งชุด")


def try_prereq_chain(conn: sqlite3.Connection, question: str) -> StructuredResult:
    text = _user_text(question)
    if not (re.search(r"ผ่าน|เรียน|ลง", text) and "ก่อน" in text and _CHAIN_RE.search(text)):
        return _NONE
    if any(k in text for k in ("สอบตก", "ตกวิชา", "กระทบ", "ไม่ผ่าน")):
        return _NONE
    program, vids = _scope(conn, question)
    rows = _match_course_by_name(conn, text, vids) if vids else []
    if not rows:
        return _NONE
    base = rows[0]
    lines = [f"วิชา {_name(base)} — {_slot(base)}", "วิชาที่ต้องสอบผ่านก่อน (↳ = วิชาที่ต้องผ่านก่อนวิชาด้านบนอีกชั้น):"]
    codes, seen = [base["code"]], {base["code"]}

    def walk(r, depth: int) -> None:
        for p in _prereqs(conn, r):
            if p["code"] in seen or depth > 5:
                continue
            seen.add(p["code"])
            codes.append(p["code"])
            mark = "•" if depth == 1 else "↳"
            lines.append(f"{'  ' * depth}{mark} {_name(p)} — {p['credits_raw']} | {_slot(p)}")
            walk(p, depth + 1)

    walk(base, 1)
    if len(codes) == 1:
        lines[-1] = "ไม่มีวิชาบังคับก่อน"
    else:
        lines.append(f"รวมต้องผ่านก่อน {len(codes) - 1} วิชา")
    return StructuredResult(True, "\n".join(lines), _versions_label(conn, [base["version_id"]]), "prereq_chain",
                            codes=codes, version_id=base["version_id"])


# ── 5. เก็บได้ N หน่วยกิต เกรด G จบได้ไหม ────────────────────────────────

_CREDITS_HAVE_RE = re.compile(r"(?:เก็บ|เรียน|สะสม|ได้|ครบ|มี|ผ่าน)[^\d]{0,12}?(\d{2,3})\s*หน่วยกิต")
_GPA_RE = re.compile(r"(?:เกรด|GPA|gpa|เกรดเฉลี่ย|เกรดเฉลี่ยสะสม|ค่าระดับคะแนนเฉลี่ย)[^\d]{0,12}?([0-4]\.\d{1,2})")
_CATEGORY_WORDS = ("ศึกษาทั่วไป", "วิชาเฉพาะ", "เลือกเสรี", "เฉพาะบังคับ", "เฉพาะเลือก")


def _total_credits(conn: sqlite3.Connection, vid: int):
    return conn.execute(
        "SELECT r.value_numeric, p.page_number, p.document_id FROM rule r "
        "JOIN provenance p ON p.provenance_id = r.provenance_id "
        "WHERE r.version_id=? AND r.rule_kind='graduation' AND r.attribute='min_total_credits'", (vid,),
    ).fetchone()


def try_grad_check(conn: sqlite3.Connection, question: str) -> StructuredResult:
    text = _user_text(question)
    if not re.search(r"จบ|สำเร็จการศึกษา", text) or "เกียรตินิยม" in text or any(k in text for k in _CATEGORY_WORDS):
        return _NONE
    cm, gm = _CREDITS_HAVE_RE.search(text), _GPA_RE.search(text)
    if not cm and not gm:
        return _NONE
    program, vids = _scope(conn, question)
    if not vids:
        return _NONE
    vid = vids[0]
    total = _total_credits(conn, vid)
    if total is None:
        return _NONE
    need = int(total[0])
    lines, ok, pages = [], True, [(total[2], total[1], "โครงสร้างหลักสูตร")]
    label = _versions_label(conn, [vid])
    lines.append(f"หลักสูตร {label} ต้องเรียนครบไม่น้อยกว่า {need} หน่วยกิต (หน้า {total[1]})")
    if cm:
        have = int(cm.group(1))
        if have >= need:
            lines.append(f"- หน่วยกิต: เก็บได้ {have} หน่วยกิต → ครบเกณฑ์ {need} หน่วยกิต")
        else:
            ok = False
            lines.append(f"- หน่วยกิต: เก็บได้ {have} หน่วยกิต → ยังขาดอีก {need - have} หน่วยกิต (ต้องครบ {need})")
    if gm:
        g = float(gm.group(1))
        if g >= 2.0:
            lines.append(f"- เกรดเฉลี่ยสะสม {g:.2f} → ผ่านเกณฑ์ขั้นต่ำ 2.00 (ข้อ 34)")
        else:
            ok = False
            lines.append(f"- เกรดเฉลี่ยสะสม {g:.2f} → ต่ำกว่าเกณฑ์ขั้นต่ำ 2.00 (ข้อ 34) จึงยังจบไม่ได้"
                         " และเกรดเฉลี่ยสะสมต่ำกว่า 2.00 ต้องถูกภาคทัณฑ์ (ข้อ 31)")
    lines.append("- เงื่อนไขอื่นตามข้อ 34: สอบผ่านทุกรายวิชาตามโครงสร้างหลักสูตร และผ่านการสอบภาษาอังกฤษ (English Exit exam)")
    verdict = "จบได้ตามเกณฑ์หน่วยกิตและเกรด (ถ้าผ่านเงื่อนไขอื่นครบ)" if ok else "ยังจบไม่ได้"
    lines.insert(0, f"คำตอบ: {verdict}")
    doc = conn.execute(
        "SELECT d.document_id FROM document d JOIN curriculum_version cv ON cv.version_id = d.version_id "
        "WHERE cv.program = 'AIT' AND cv.curriculum_year = 2566 AND d.degree_level = 'bachelor'"
    ).fetchone()
    if doc:
        pages.append((doc[0], 88, "ข้อบังคับการศึกษาระดับปริญญาตรี ข้อ 34"))
    return StructuredResult(True, "\n".join(lines), label, "grad_check", version_id=vid, pages=pages)


# ── 6. หลักสูตร P1 กับ P2 หน่วยกิตรวมต่างกันเท่าไร ─────────────────────────

def try_program_total_compare(conn: sqlite3.Connection, question: str) -> StructuredResult:
    text = _user_text(question)
    progs = list(dict.fromkeys(_programs_in(text)))
    if len(progs) < 2 or "หน่วยกิต" not in text or len(detect_versions(conn, text)) >= 2:
        return _NONE
    if not re.search(r"รวม|ทั้งหมด|ตลอดหลักสูตร|ต่างกัน|มากกว่า|น้อยกว่า|เท่าไร|กี่", text):
        return _NONE
    facts, pages = [], []
    for p in progs:
        vids = _course_scope_versions(conn, f"หลักสูตร {p}: {text}", p)
        row = _total_credits(conn, vids[0]) if vids else None
        if row is None:
            return _NONE
        facts.append((_versions_label(conn, [vids[0]]), int(row[0]), row[1]))
        pages.append((row[2], row[1], "โครงสร้างหลักสูตร"))
    lines = [f"- {lab}: ไม่น้อยกว่า {n} หน่วยกิต (หน้า {pg})" for lab, n, pg in facts]
    hi = max(facts, key=lambda f: f[1])
    lo = min(facts, key=lambda f: f[1])
    verdict = ("หน่วยกิตรวมเท่ากัน" if hi[1] == lo[1]
               else f"ต่างกัน {hi[1] - lo[1]} หน่วยกิต ({hi[0]} มากกว่า {lo[0]})")
    lines.insert(0, f"คำตอบ: {verdict}")
    return StructuredResult(True, "\n".join(lines), " / ".join(f[0] for f in facts), "program_compare", pages=pages)


# ── 7. ปี Y ภาค S ตามแผนรวมกี่หน่วยกิต (และเกินเพดานภาคปกติไหม) ─────────────

_SEM_ASK_RE = re.compile(r"รวม|ทั้งเทอม|ทั้งภาค|ลง(?:ทะเบียน)?(?:เรียน)?\s*กี่|กี่หน่วยกิต")
_CEILING_RE = re.compile(r"เพดาน|เกิน|สูงสุด|มากสุด|หนักไป|ไหวไหม")


def try_semester_total(conn: sqlite3.Connection, question: str) -> StructuredResult:
    from katrag.query.plan_variants import _flags as plan_flags, plan_blocks, variant_totals
    from katrag.query.structured_query import detect_semester, detect_year, semester_totals

    text = _user_text(question)
    y, s = detect_year(text), detect_semester(text)
    if not (y and s) or "หน่วยกิต" not in text or not _SEM_ASK_RE.search(text) or "วิชา" in text:
        return _NONE
    if len(re.findall(r"ปี\s*(?:ที่)?\s*[1-4]", text)) > 1:
        return _NONE
    coop, normal, _diff = plan_flags(text)
    if coop or normal or "อะไร" in text:
        return _NONE  # ระบุแผน/ถามรายวิชาด้วย → ตัวตอบแผนปกติ-สหกิจแสดงรายวิชาของภาคพร้อมยอดรวม
    program, vids = _scope(conn, question)
    if not vids:
        return _NONE
    vid = vids[0]
    blocks = variant_totals(plan_blocks(conn, vid), "default")
    if (y, s) in blocks and blocks[(y, s)].total is not None:
        b = blocks[(y, s)]
        total, page, doc_id = b.total, b.pages[0] if b.pages else b.page, b.document_id
    else:
        st = semester_totals(conn, vid).get((y, s))
        if not st:
            return _NONE
        total, page = st
        row = conn.execute("SELECT document_id FROM document WHERE version_id=? AND degree_level='bachelor'",
                           (vid,)).fetchone()
        doc_id = row[0] if row else ""
    label = _versions_label(conn, [vid])
    plan = "แผนปกติ"
    lines = [f"คำตอบ: ปีที่ {y} ภาคการศึกษาที่ {s} ตาม{plan}ของ {label} ลงรวม {total} หน่วยกิต",
             f"- จากบรรทัด 'รวม' ของภาคนี้ในตารางแผนการศึกษา หน้า {page} "
             "(รวมวิชาเลือก/ศึกษาทั่วไปที่แผนกำหนดไว้แล้ว ไม่ใช่เฉพาะวิชาบังคับ)"]
    pages = [(doc_id, page, "ตารางแผนการศึกษา")] if doc_id else []
    if _CEILING_RE.search(text):
        within = 9 <= total <= 22
        lines.append(f"- ข้อบังคับ: ภาคการศึกษาปกติลงทะเบียนได้ 9–22 หน่วยกิต → {total} หน่วยกิต "
                     + ("อยู่ในเกณฑ์ ไม่เกินเพดาน 22" if within else
                        "เกินเพดาน 22" if total > 22 else "ต่ำกว่าขั้นต่ำ 9 (ตามแผนกำหนดไว้แล้ว)"))
        doc = conn.execute(
            "SELECT d.document_id FROM document d JOIN curriculum_version cv ON cv.version_id = d.version_id "
            "WHERE cv.program = 'AIT' AND cv.curriculum_year = 2566 AND d.degree_level = 'bachelor'"
        ).fetchone()
        if doc:
            pages.append((doc[0], 82, "ข้อบังคับการศึกษาระดับปริญญาตรี (การลงทะเบียน)"))
    return StructuredResult(True, "\n".join(lines), label, "semester_total", version_id=vid, pages=pages)


def try_multi_hop(conn: sqlite3.Connection, question: str) -> StructuredResult:
    conn.row_factory = sqlite3.Row
    for fn in (try_semester_total, try_grad_check, try_program_total_compare, try_course_across_programs,
               try_two_courses, try_prereq_chain, try_can_take):
        sr = fn(conn, question)
        if sr.matched:
            return sr
    return _NONE
