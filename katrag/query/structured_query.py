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

    # current edition — บาง program มีหลาย current (เช่น IT 2565/2566/2568)
    # เลือกเวอร์ชันที่มีข้อมูลแผนการเรียน (course ที่มี year) มากที่สุด
    # ถ้าเท่ากันเลือกปีล่าสุด
    rows = conn.execute(
        "SELECT cv.version_id, cv.program, cv.curriculum_year, cv.edition_status, "
        "COUNT(CASE WHEN co.year IS NOT NULL THEN 1 END) AS n_plan "
        "FROM curriculum_version cv LEFT JOIN course co ON co.version_id=cv.version_id "
        "WHERE cv.program=? AND cv.edition_status='current' "
        "GROUP BY cv.version_id ORDER BY n_plan DESC, cv.curriculum_year DESC",
        (program,),
    ).fetchall()
    if rows:
        best = rows[0]
        # ถ้าเวอร์ชันที่ดีที่สุดไม่มีแผนเลย ก็ยังคืนตัวล่าสุด (fallback)
        return best["version_id"], f"{best['program']} {best['curriculum_year']} ({best['edition_status']})"
    return None


try:
    from pythainlp.tokenize import word_tokenize as _thai_tok
    _HAS_TOK = True
except Exception:  # pragma: no cover
    _HAS_TOK = False

# คำทั่วไปที่ไม่ใช่ "หัวข้อวิชา"
_TOPIC_STOP = {
    "วิชา", "เรียน", "มี", "กี่", "อะไร", "บ้าง", "หลักสูตร", "ของ", "ที่",
    "เกี่ยวกับ", "เกี่ยวข้อง", "กับ", "รายวิชา", "ทั้งหมด", "ครับ", "คะ", "ค่ะ",
    "ปี", "เทอม", "ภาค", "หน่วยกิต", "ต้อง", "ลง", "ไหน", "ด้าน", "สาขา",
    # คำกว้างเกินไป — จับ synonym แทน (กัน false positive เช่น 'เขียน'→เขียนภาษาอังกฤษ)
    "เขียน", "การ", "และ",
}
# synonym: คำถาม → คำที่ปรากฏในชื่อวิชา
_TOPIC_SYNONYM = {
    "เขียนโปรแกรม": ["โปรแกรม", "programming"],
    "โปรแกรมมิ่ง": ["โปรแกรม", "programming"],
    "coding": ["โปรแกรม", "programming"],
    "programming": ["โปรแกรม", "programming"],
    "ฐานข้อมูล": ["ฐานข้อมูล", "database"],
    # ใช้ "ARTIFICIAL" ไม่ใช่ "INTELLIGENCE" เพราะ INTELLIGENCE ไปตรงกับ
    # "DIGITAL INTELLIGENCE QUOTIENT" ซึ่งไม่ใช่วิชา AI
    # (ชื่อในเอกสารสะกด INTELLIGIENCE ผิดบางที่ → ARTIFICIAL ครอบคลุมกว่า)
    "เอไอ": ["ปัญญาประดิษฐ์", "ARTIFICIAL"],
    "ปัญญาประดิษฐ์": ["ปัญญาประดิษฐ์", "ARTIFICIAL"],
    "เครือข่าย": ["เครือข่าย", "NETWORK"],
    "ความมั่นคง": ["ความมั่นคง", "SECURITY", "ไซเบอร์"],
    "คณิต": ["คณิต", "MATH", "แคลคูลัส", "CALCULUS"],
}


def _extract_topic_keywords(question: str, program: str | None) -> list[str]:
    """แยกคำหัวข้อวิชาจากคำถาม + ขยาย synonym."""
    q = question
    # ตัดส่วน 'หลักสูตร XXX:' ที่ prepend มา
    q = re.sub(r"หลักสูตร\s+[A-Z]+\s*:", "", q)
    tokens = _thai_tok(q, keep_whitespace=False) if _HAS_TOK else re.findall(r"[ก-๙]+|[A-Za-z]+", q)
    prog_up = (program or "").upper()
    kws: list[str] = []
    for t in tokens:
        t = t.strip()
        if not t or t in _TOPIC_STOP or t.upper() == prog_up or len(t) < 2:
            continue
        kws.append(t)
    # synonym expansion + ตรวจ compound ในคำถามดิบ
    expanded: list[str] = []
    ql = question.lower()
    for syn, reps in _TOPIC_SYNONYM.items():
        if syn in ql:
            expanded.extend(reps)
    for k in kws:
        if k.lower() in _TOPIC_SYNONYM:
            expanded.extend(_TOPIC_SYNONYM[k.lower()])
        else:
            expanded.append(k)
    # unique คงลำดับ
    return list(dict.fromkeys(expanded))


def try_topic_courses(conn: sqlite3.Connection, question: str) -> StructuredResult:
    """ตอบคำถาม 'วิชา<หัวข้อ> มีกี่วิชา/อะไรบ้าง' — ค้นชื่อวิชาในตาราง course.

    - ถ้าระบุ program → ค้นเฉพาะหลักสูตรนั้น
    - ถ้าไม่ระบุ (ทุกหลักสูตร) → ค้นทุก current version แล้วแยกตามหลักสูตร
    """
    conn.row_factory = sqlite3.Row
    if "วิชา" not in question:
        return StructuredResult(False, "", "", "none")

    program = detect_program(question)
    keywords = _extract_topic_keywords(question, program)
    if not keywords:
        return StructuredResult(False, "", "", "none")

    # version scope
    if program:
        vers = conn.execute(
            "SELECT version_id, program, curriculum_year FROM curriculum_version "
            "WHERE program=? AND edition_status='current'", (program,),
        ).fetchall()
    else:
        vers = conn.execute(
            "SELECT version_id, program, curriculum_year FROM curriculum_version "
            "WHERE edition_status='current'",
        ).fetchall()
    if not vers:
        return StructuredResult(False, "", "", "none")

    # เลือก version ที่มีข้อมูลมากสุดต่อ program (กัน IT ที่มีหลาย current)
    best_per_prog: dict[str, sqlite3.Row] = {}
    for v in vers:
        n = conn.execute("SELECT COUNT(*) FROM course WHERE version_id=?", (v["version_id"],)).fetchone()[0]
        cur = best_per_prog.get(v["program"])
        if cur is None or n > cur["_n"]:
            d = dict(v); d["_n"] = n
            best_per_prog[v["program"]] = d  # type: ignore

    kw_clause = " OR ".join(["name_th LIKE ? OR name_en LIKE ?" for _ in keywords])

    blocks: list[str] = []
    total_found = 0
    for prog, v in sorted(best_per_prog.items()):
        params: list = []
        for kw in keywords:
            params.extend([f"%{kw}%", f"%{kw}%"])
        params.append(v["version_id"])
        rows = conn.execute(
            f"SELECT code, name_th, name_en, credits_raw FROM course "
            f"WHERE ({kw_clause}) AND version_id=? ORDER BY code", params,
        ).fetchall()
        if not rows:
            continue
        total_found += len(rows)
        header = f"หลักสูตร {prog} {v['curriculum_year']} — พบ {len(rows)} วิชา:"
        lines = [header]
        for r in rows:
            en = f" ({r['name_en']})" if r["name_en"] else ""
            lines.append(f"  - {r['code']} {r['name_th']}{en} — {r['credits_raw']}")
        blocks.append("\n".join(lines))

    if not blocks:
        return StructuredResult(False, "", "", "none")

    topic = " / ".join(keywords[:3])
    ctx = f"วิชาที่เกี่ยวกับ '{topic}' (ค้นจากชื่อวิชาในหลักสูตร):\n\n" + "\n\n".join(blocks)
    label = program or "ทุกหลักสูตร"
    return StructuredResult(True, ctx, label, "topic_courses")


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
            from itertools import groupby
            lines = [f"รายวิชาของหลักสูตร {version_label} ปีที่ {year_level} "
                     "(จัดกลุ่มตามภาคการศึกษา):"]
            for sem_no, group in groupby(rows, key=lambda r: r["semester"]):
                courses = list(group)
                sem_credits = sum(_parse_credit(cc["credits_raw"]) for cc in courses)
                lines.append(f"\n▶ ภาคการศึกษาที่ {sem_no} — วิชาบังคับ ({len(courses)} วิชา {sem_credits} หน่วยกิต):")
                for cc in courses:
                    en = f" ({cc['name_en']})" if cc["name_en"] else ""
                    lines.append(f"  - {cc['code']} {cc['name_th']}{en} — {cc['credits_raw']}")
                # วิชาเลือกในเทอมนี้ (จากตารางแผน)
                electives = extract_elective_slots(conn, version_id, year_level, sem_no)
                if electives:
                    lines.append(f"  วิชาเลือกเฉพาะแขนง (เลือก 1 แขนง แล้วลงวิชาในแขนงนั้น) กลุ่มที่มี:")
                    for e in electives:
                        lines.append(f"    • {e}")
            total = sum(_parse_credit(r["credits_raw"]) for r in rows)
            lines.append(f"\nรวมปีที่ {year_level} (วิชาบังคับ): {len(rows)} วิชา {total} หน่วยกิต (ยังไม่รวมวิชาเลือก)")
            return StructuredResult(True, "\n".join(lines), version_label, "year_sem")

    # ── กรณี: ถามรายวิชา "ทั้งหมด" ของหลักสูตร (ต้องระบุชัดว่าเอาทั้งหมด) ──
    # ถ้าถามเจาะจงหัวข้อ (เช่น "วิชาเขียนโปรแกรม") ไม่เข้า branch นี้ → ให้ hybrid ค้นแทน
    wants_all = any(w in question for w in [
        "ทั้งหมด", "ทุกวิชา", "มีวิชาอะไรบ้าง", "รายวิชาทั้งหมด", "วิชาทั้งหมด", "โครงสร้างหลักสูตร"
    ])
    if wants_all:
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

    # คำถามเจาะจงหัวข้อ (ไม่ระบุปี ไม่เอาทั้งหมด) → ปล่อยให้ hybrid retrieval จัดการ
    return StructuredResult(False, "", "", "none")


def _parse_credit(credits_raw: str) -> int:
    m = re.match(r"(\d+)", credits_raw or "")
    return int(m.group(1)) if m else 0


_ELECTIVE_SLOT_RE = re.compile(r"(?:\d{4}xxx\s*)?(วิชาเลือก[ก-๙\s]*?\d?)\s*\n?\s*(ELECTIVE[A-Z\s]*\d?)?", re.IGNORECASE)


def extract_elective_slots(conn: sqlite3.Connection, version_id: int, year: int, semester: int) -> list[str]:
    """ดึงช่องวิชาเลือก (elective slot) จากตารางแผนการศึกษาของปี/เทอมนั้น.

    แผนมักเขียน 'xxxxxxx วิชาเลือกกลุ่ม... ELECTIVE IN ...' = ต้องเลือกลง 1 วิชา
    """
    conn.row_factory = sqlite3.Row
    header = f"ปีที่ {year} ภาคการศึกษาที่ {semester}"
    row = conn.execute(
        "SELECT text FROM chunk WHERE version_id=? AND text LIKE ? ORDER BY page_number LIMIT 1",
        (version_id, f"%{header}%"),
    ).fetchone()
    if not row:
        return []
    text = row["text"]
    start = text.find(header)
    # ตัดถึง header เทอมถัดไป (ถ้ามี) เพื่อจำกัดขอบเขต
    nxt = re.search(r"ปีที่ \d ภาคการศึกษาที่ \d", text[start + len(header):])
    segment = text[start: start + len(header) + (nxt.start() if nxt else 800)]

    slots: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(r"วิชาเลือก[ก-๙\s]{2,60}?\d?(?=\s|\n|$)", segment):
        name = " ".join(m.group(0).split())
        # ตัดชื่อยาวเกินที่รวมข้อความอื่น
        name = name.split("หน่วยกิต")[0].strip()
        # ตัดเลข trailing (เช่น "วิชาเลือกกลุ่มวิทยาการข้อมูล 3" → ตัด " 3")
        name = re.sub(r"\s+\d+$", "", name)
        if 10 < len(name) < 60 and name not in seen:
            seen.add(name)
            slots.append(name)
    return slots


def _norm_name(name: str) -> str:
    """normalize ชื่อวิชาสำหรับเทียบข้ามเวอร์ชัน (ตัด whitespace/วรรณยุกต์ที่ต่างเล็กน้อย)."""
    n = re.sub(r"\s+", "", name or "")
    # ตัดเลขลำดับท้าย เช่น "แคลคูลัส 1" -> "แคลคูลัส1" (คงไว้)
    return n.lower()


def detect_prerequisite_intent(question: str) -> bool:
    """ตรวจว่าเป็นคำถามวิชาบังคับก่อน (prerequisite)."""
    return any(w in question.lower() for w in ["บังคับก่อน", "ต้องผ่าน", "ต้องเรียนก่อน", "ลงก่อน", "prerequisite", "ก่อนถึงจะลง", "เรียนก่อน"])


def try_prerequisite(conn: sqlite3.Connection, question: str) -> StructuredResult:
    """ตอบว่าวิชาที่ถามต้องผ่านวิชาใดก่อน — จาก course.prerequisite_json."""
    import json
    conn.row_factory = sqlite3.Row
    if not detect_prerequisite_intent(question):
        return StructuredResult(False, "", "", "none")

    program = detect_program(question)

    # ตัด 'หลักสูตร XXX:' ที่ prepend มา ไม่ให้กลายเป็น keyword ชื่อวิชา
    q = re.sub(r"หลักสูตร\s+[A-Za-z]+\s*[:：]", " ", question)
    if program:
        q = re.sub(rf"(?i)\b{re.escape(program)}\b", " ", q)

    # ดึง keyword ชื่อวิชาจากคำถาม (คำไทยยาว ≥ 4 + อังกฤษ ≥ 4)
    stop = {"ต้องผ่าน", "วิชา", "บังคับก่อน", "ต้องเรียน", "ก่อนถึงจะลง", "อะไร", "ใดบ้าง", "หลักสูตร"}
    # คำที่บ่งชี้ว่าเป็น "ส่วนคำถาม" ไม่ใช่ชื่อวิชา (tokenizer ไทยรวมเป็นก้อนยาว)
    q_markers = ("ต้อง", "ก่อน", "อะไร", "ใดบ้าง", "ได้บ้าง", "หรือไม่", "จะลง")
    tokens = re.findall(r"[ก-๙]{4,}|[A-Za-z]{4,}", q)
    keywords = [
        t for t in tokens
        if t.lower() not in {s.lower() for s in stop}
        and not any(m in t for m in q_markers)
    ]
    if not keywords:
        return StructuredResult(False, "", "", "none")

    # หา version scope
    version_ids: list[int] = []
    if program:
        rows = conn.execute(
            "SELECT version_id FROM curriculum_version WHERE program=? AND edition_status='current'",
            (program,),
        ).fetchall()
        version_ids = [r[0] for r in rows]

    # สร้างเงื่อนไข OR ของ keyword — params ต้องเรียงตามลำดับ placeholder ใน SQL
    kw_clauses = " OR ".join(["name_th LIKE ? OR name_en LIKE ?" for _ in keywords])
    params: list = []
    for kw in keywords:  # keyword placeholders มาก่อนใน WHERE (...)
        params.extend([f"%{kw}%", f"%{kw}%"])

    where_scope = ""
    if version_ids:  # version placeholders มาหลัง
        ph = ",".join("?" for _ in version_ids)
        where_scope = f" AND version_id IN ({ph})"
        params.extend(version_ids)

    rows = conn.execute(
        f"SELECT code, name_th, name_en, credits_raw, year, semester, "
        f"prerequisite_json, prerequisite_raw, version_id "
        f"FROM course WHERE ({kw_clauses}){where_scope} ORDER BY (prerequisite_json != '[]') DESC LIMIT 10",
        params,
    ).fetchall()

    if not rows:
        return StructuredResult(False, "", "", "none")

    # ── Relevance ranking: เรียงตามจำนวน keyword ที่ match ──
    # วิชาที่ชื่อตรงกับ keyword มากที่สุด = น่าจะเป็นวิชาที่ถูกถามถึง
    def _stem_match(kw: str, word: str) -> bool:
        """Match ถ้า keyword กับคำในชื่อวิชามี prefix ร่วมกัน ≥ 5 ตัว (กัน warehouse/warehousing)."""
        kl = kw.lower()
        wl = word.lower()
        if kl == wl:
            return True
        # ดู prefix ร่วม
        minlen = min(len(kl), len(wl))
        if minlen < 4:
            return kl in wl or wl in kl
        shared = 0
        for i in range(minlen):
            if kl[i] == wl[i]:
                shared += 1
            else:
                break
        return shared >= min(5, minlen)

    def _kw_score(r) -> int:
        blob = f"{r['name_th']} {r['name_en']}".lower()
        words = re.findall(r"[ก-๙]+|[a-z]+", blob)
        score = 0
        for kw in keywords:
            if any(_stem_match(kw, w) for w in words):
                score += 1
        return score

    rows = sorted(rows, key=lambda r: (-_kw_score(r), -(1 if json.loads(r["prerequisite_json"] or "[]") else 0)))

    # ถ้าตัวอันดับ 1 match keyword มากกว่าตัวที่ 2 ชัดเจน → ตอบแค่ตัวเดียว
    # (เช่น "data warehouse" → DATA WAREHOUSING match 2 คำ, PROJECT IN DATA SCIENCE match 1 คำ)
    if len(rows) > 1 and _kw_score(rows[0]) > _kw_score(rows[1]):
        rows = [rows[0]]
    else:
        # ถ้า score เท่ากัน → เอาเฉพาะที่มี prereq + limit 3
        rows_with_prereq = [r for r in rows if json.loads(r["prerequisite_json"] or "[]")]
        if rows_with_prereq:
            rows = rows_with_prereq[:3]
        else:
            rows = rows[:3]

    def _plan(r) -> str:
        if r["year"] and r["semester"]:
            return f"ปีที่ {r['year']} ภาคการศึกษาที่ {r['semester']}"
        if r["year"]:
            return f"ปีที่ {r['year']}"
        return "ไม่ระบุชั้นปี (วิชาเลือก)"

    # ── ตรวจว่าถามเรื่อง "ลงได้ไหมถ้ายังไม่ผ่าน" ──
    ask_can_register = any(w in question for w in ["ลงได้ไหม", "ลงทะเบียนได้ไหม", "ลงได้หรือไม่", "ลงได้มั้ย"])

    lines = []
    for r in rows:
        prereqs = json.loads(r["prerequisite_json"] or "[]")
        en = f" ({r['name_en']})" if r["name_en"] else ""
        head = f"วิชา {r['code']} {r['name_th']}{en} — {r['credits_raw']} | {_plan(r)}"
        if prereqs:
            lines.append(head)
            lines.append("  ต้องผ่านวิชาบังคับก่อน:")
            for pc in prereqs:
                pr = conn.execute(
                    "SELECT name_th, name_en, credits_raw, year, semester FROM course "
                    "WHERE code=? AND version_id=? LIMIT 1",
                    (pc, r["version_id"]),
                ).fetchone()
                if pr is None:
                    # prereq อาจเป็นวิชาแกนที่อยู่ในเวอร์ชันอื่น → หาแบบไม่ผูกเวอร์ชัน
                    pr = conn.execute(
                        "SELECT name_th, name_en, credits_raw, year, semester FROM course "
                        "WHERE code=? LIMIT 1", (pc,),
                    ).fetchone()
                if pr:
                    pen = f" ({pr['name_en']})" if pr["name_en"] else ""
                    lines.append(
                        f"    • {pc} {pr['name_th']}{pen} — {pr['credits_raw']} | {_plan(pr)}"
                    )
                else:
                    lines.append(f"    • {pc} (ไม่พบชื่อวิชาในฐานข้อมูล)")
        else:
            lines.append(head + "\n  ไม่มีวิชาบังคับก่อน (PREREQUISITE: None)")

    if ask_can_register:
        lines.append("")
        has_prereq = any(json.loads(r["prerequisite_json"] or "[]") for r in rows)
        if has_prereq:
            lines.append("คำตอบ: ไม่ได้ — ถ้ายังไม่ผ่านวิชาบังคับก่อน (prerequisite) จะลงทะเบียนวิชานี้ไม่ได้")
            lines.append("ต้องเรียนวิชาบังคับก่อนให้ผ่านก่อนจึงจะลงทะเบียนได้")
        else:
            lines.append("คำตอบ: ได้ — วิชานี้ไม่มีวิชาบังคับก่อน สามารถลงทะเบียนได้เลย")

    return StructuredResult(True, "\n".join(lines), "", "prerequisite")


def detect_plan_summary_intent(question: str) -> bool:
    """ตรวจว่าเป็นคำถามภาพรวมแผนการเรียน/จบเร็ว."""
    return any(w in question for w in ["แผนการเรียน", "แผนการศึกษา", "3.5 ปี", "3.5ปี", "จบเร็ว", "จบไว", "แต่ละเทอม", "ทุกเทอม", "โครงสร้างหลักสูตร"])


def _detect_early_grad(question: str) -> bool:
    """ตรวจว่าถาม 'จบ 3.5 ปี / จบเร็ว'."""
    return any(w in question for w in ["3.5 ปี", "3.5ปี", "จบเร็ว", "จบไว", "จบใน 3.5", "สามปีครึ่ง", "3 ปีครึ่ง"])


def try_plan_summary(conn: sqlite3.Connection, question: str) -> StructuredResult:
    """คืนสรุปแผนการเรียนต่อชั้นปี/ภาค (จำนวนวิชา+หน่วยกิต+รายวิชา)."""
    conn.row_factory = sqlite3.Row
    program = detect_program(question)
    if not program or not detect_plan_summary_intent(question):
        return StructuredResult(False, "", "", "none")

    be_match = re.search(r"\b(25\d\d)\b", question)
    year_be = int(be_match.group(1)) if be_match else None
    resolved = _resolve_version_id(conn, program, year_be)
    if not resolved:
        return StructuredResult(False, "", "", "none")
    version_id, version_label = resolved

    rows = conn.execute(
        "SELECT year, semester, code, name_th, name_en, credits_raw, credits_total "
        "FROM course WHERE version_id=? AND year IS NOT NULL AND semester IS NOT NULL "
        "ORDER BY year, semester, code",
        (version_id,),
    ).fetchall()
    if not rows:
        return StructuredResult(False, "", "", "none")

    # ── ถ้าถาม "จบ 3.5 ปี" → ให้คำแนะนำเฉพาะ ──
    if _detect_early_grad(question):
        return _format_early_grad(conn, version_id, version_label, rows)

    # ── แผนปกติ ──
    return _format_full_plan(conn, version_id, version_label, rows)


def _format_full_plan(
    conn: sqlite3.Connection, version_id: int, version_label: str, rows: list
) -> StructuredResult:
    """แผนเรียนปกติทุกเทอม."""

    lines = [f"แผนการศึกษาของหลักสูตร {version_label} (จำแนกตามชั้นปี/ภาคการศึกษา):"]
    from itertools import groupby
    total_all = 0
    for (yr, sem), group in groupby(rows, key=lambda r: (r["year"], r["semester"])):
        courses = list(group)
        sem_credits = sum(cc["credits_total"] or 0 for cc in courses)
        total_all += sem_credits
        lines.append(f"\nปีที่ {yr} ภาคการศึกษาที่ {sem} ({len(courses)} วิชา, {sem_credits} หน่วยกิต):")
        for cc in courses:
            en = f" ({cc['name_en']})" if cc["name_en"] else ""
            lines.append(f"  - {cc['code']} {cc['name_th']}{en} — {cc['credits_raw']}")
        # วิชาเลือกแขนง/เฉพาะด้าน ที่ต้องลงในเทอมนี้
        elective_slots = extract_elective_slots(conn, version_id, yr, sem)
        if elective_slots:
            lines.append(f"  + วิชาเลือกเฉพาะแขนง (เลือก 1 แขนง แล้วลงวิชาในแขนงนั้น) กลุ่มที่มี:")
            for slot in elective_slots:
                lines.append(f"    • {slot}")

    # วิชาเลือก/เสรี ที่ไม่ผูกเทอม
    elective_count = conn.execute(
        "SELECT COUNT(*) FROM course WHERE version_id=? AND year IS NULL", (version_id,)
    ).fetchone()[0]
    lines.append(f"\nหมายเหตุ: มีวิชาเลือก/เลือกเสรีอีก {elective_count} วิชา ที่ไม่ผูกภาคเรียนตายตัว (เลือกลงได้ตามเงื่อนไข)")
    lines.append(f"หน่วยกิตในแผนบังคับตามเทอม: {total_all} หน่วยกิต")

    return StructuredResult(True, "\n".join(lines), version_label, "plan_summary")


def _format_early_grad(
    conn: sqlite3.Connection, version_id: int, version_label: str, rows: list
) -> StructuredResult:
    """คำแนะนำจบ 3.5 ปี: ดึงวิชาปี 4 เทอม 2 มาลงก่อน."""
    from itertools import groupby

    # แยกวิชาปี 4 เทอม 2 — คือส่วนที่ต้อง "เลื่อนขึ้น" ไปลงเทอมก่อนหน้า
    last_sem = [r for r in rows if r["year"] == 4 and r["semester"] == 2]
    other = [r for r in rows if not (r["year"] == 4 and r["semester"] == 2)]

    # วิชาเลือก/เสรี ที่ไม่ผูกเทอม
    elective_rows = conn.execute(
        "SELECT code, name_th, name_en, credits_raw, credits_total "
        "FROM course WHERE version_id=? AND year IS NULL", (version_id,)
    ).fetchall()

    last_credits = sum((r["credits_total"] or 0) for r in last_sem)
    elective_credits_total = sum((r["credits_total"] or 0) for r in elective_rows)

    lines = [
        f"แนวทางจบหลักสูตร {version_label} ภายใน 3.5 ปี",
        "",
        "หลักการ: จบปกติใช้ 4 ปี (8 ภาค) การจบ 3.5 ปี = จบในสิ้นปี 4 เทอม 1",
        "วิธี: ดึงวิชาที่ปกติอยู่ปี 4 ภาคการศึกษาที่ 2 มาลงล่วงหน้าในเทอมก่อนหน้า",
        "(ต้องตรวจสอบ prerequisite ว่าวิชานั้นเปิดลงล่วงหน้าได้)",
        "",
    ]

    if last_sem:
        lines.append(f"■ วิชาในปี 4 ภาคการศึกษาที่ 2 (ปกติ) ที่ต้องดึงมาลงก่อน ({len(last_sem)} วิชา, {last_credits} หน่วยกิต):")
        for r in last_sem:
            en = f" ({r['name_en']})" if r["name_en"] else ""
            lines.append(f"  - {r['code']} {r['name_th']}{en} — {r['credits_raw']}")
    else:
        lines.append("■ ไม่พบวิชาบังคับในปี 4 ภาคการศึกษาที่ 2 ในแผน (อาจเป็นวิชาเลือกทั้งหมด)")

    if elective_rows:
        lines.append(f"\n■ วิชาเลือก/เลือกเสรี ที่ต้องลงให้ครบด้วย ({len(elective_rows)} วิชา, {elective_credits_total} หน่วยกิต):")
        lines.append("  (วิชาเหล่านี้ไม่ผูกเทอมตายตัว ลงได้ตั้งแต่เทอมที่เปิดให้ลง)")

    lines.append("")
    lines.append("■ แผนบังคับทุกเทอม (ไม่รวมปี 4 เทอม 2):")
    total_other = 0
    for (yr, sem), group in groupby(other, key=lambda r: (r["year"], r["semester"])):
        courses = list(group)
        sem_credits = sum(cc["credits_total"] or 0 for cc in courses)
        total_other += sem_credits
        lines.append(f"\n  ปีที่ {yr} ภาคการศึกษาที่ {sem} ({len(courses)} วิชา, {sem_credits} หน่วยกิต):")
        for cc in courses:
            en = f" ({cc['name_en']})" if cc["name_en"] else ""
            lines.append(f"    - {cc['code']} {cc['name_th']}{en} — {cc['credits_raw']}")
        # วิชาเลือกแขนง/เฉพาะด้าน ที่ต้องลงในเทอมนี้ (จากแผนในเอกสาร)
        elective_slots = extract_elective_slots(conn, version_id, yr, sem)
        if elective_slots:
            lines.append(f"    + วิชาเลือกเฉพาะแขนง (เลือก 1 แขนง แล้วลงวิชาในแขนงนั้น) กลุ่มที่มี:")
            for slot in elective_slots:
                lines.append(f"      • {slot}")

    extra = last_credits + elective_credits_total
    lines.append("")
    lines.append(f"สรุป: ต้องดึงวิชาปี 4 เทอม 2 ({last_credits} หน่วยกิต) + วิชาเลือก/เลือกเสรี ({elective_credits_total} หน่วยกิต)")
    lines.append(f"รวม {extra} หน่วยกิต มากระจายลงในเทอมก่อนหน้า")
    lines.append("แนะนำเฉลี่ยเพิ่มเทอมละ 3-6 หน่วยกิต เพื่อไม่ให้หนักเกินไป")
    lines.append("และต้องตรวจ prerequisite ว่าวิชาที่จะดึงขึ้นมาลงก่อนได้จริง")

    return StructuredResult(True, "\n".join(lines), version_label, "plan_summary")


def detect_cross_version_intent(question: str) -> bool:
    """ตรวจว่าเป็นคำถามเทียบหลักสูตรเก่า-ใหม่."""
    has_compare = any(w in question for w in ["เก่า", "ใหม่", "เปรียบเทียบ", "ต่างกัน", "แตกต่าง", "หายไป", "เพิ่มเข้ามา", "ตัดออก"])
    return has_compare


def try_cross_version_diff(conn: sqlite3.Connection, question: str) -> StructuredResult:
    """เทียบรายวิชาระหว่างหลักสูตรเก่ากับใหม่ (เทียบด้วยชื่อวิชา)."""
    conn.row_factory = sqlite3.Row
    program = detect_program(question)
    if not program or not detect_cross_version_intent(question):
        return StructuredResult(False, "", "", "none")

    # ดึง version เก่า + ใหม่ ของ program
    vers = conn.execute(
        "SELECT version_id, curriculum_year, edition_status FROM curriculum_version WHERE program=? ORDER BY curriculum_year",
        (program,),
    ).fetchall()
    old_v = next((v for v in vers if v["edition_status"] == "old"), None)
    new_v = next((v for v in vers if v["edition_status"] == "current"), None)
    if not old_v or not new_v:
        return StructuredResult(False, "", "", "none")

    def _courses(vid):
        rows = conn.execute("SELECT code, name_th, name_en FROM course WHERE version_id=?", (vid,)).fetchall()
        return {_norm_name(r["name_th"]): (r["code"], r["name_th"], r["name_en"]) for r in rows}

    old_courses = _courses(old_v["version_id"])
    new_courses = _courses(new_v["version_id"])

    only_old = [v for k, v in old_courses.items() if k not in new_courses]
    only_new = [v for k, v in new_courses.items() if k not in old_courses]

    label = f"{program} {old_v['curriculum_year']} (เก่า) vs {new_v['curriculum_year']} (ใหม่)"
    lines = [f"เปรียบเทียบหลักสูตร {label} (เทียบด้วยชื่อวิชา):"]
    lines.append(f"\nวิชาที่มีในหลักสูตรเก่า ({old_v['curriculum_year']}) แต่ไม่มีในหลักสูตรใหม่ ({new_v['curriculum_year']}) — {len(only_old)} วิชา:")
    for code, th, en in sorted(only_old, key=lambda x: x[1])[:40]:
        en_s = f" ({en})" if en else ""
        lines.append(f"- {th}{en_s} [รหัสเดิม {code}]")
    lines.append(f"\nวิชาที่เพิ่มเข้ามาในหลักสูตรใหม่ ({new_v['curriculum_year']}) — {len(only_new)} วิชา:")
    for code, th, en in sorted(only_new, key=lambda x: x[1])[:40]:
        en_s = f" ({en})" if en else ""
        lines.append(f"- {th}{en_s} [รหัส {code}]")

    return StructuredResult(True, "\n".join(lines), label, "cross_version")
