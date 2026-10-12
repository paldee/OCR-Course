"""แผนการศึกษาและหน่วยกิตของหลักสูตรปริญญาโท/เอก — อ่านจากหัวข้อ 3.1.1 / 3.1.2 ของเล่มโดยตรง.

เล่มบัณฑิตศึกษาเขียนรูปแบบเดียวกันทุกเล่ม:
    3.1.1 จำนวนหน่วยกิตรวมตลอดหลักสูตร | แผน ก แบบ ก 1 | 36 | หน่วยกิต | แผน ข | 36 | หน่วยกิต ...
    3.1.2 โครงสร้างหลักสูตร | แผน ก แบบ ก 1 | 36 | หน่วยกิต | ก. หมวดวิชาวิทยานิพนธ์ | 36 | หน่วยกิต ...
เดิมคำถามกลุ่มนี้ตกไปที่ LLM ซึ่งตอบไม่นิ่ง (เช่น X5 ลืมแผน ข แล้วไปตอบจำนวนรับนักศึกษา)
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from functools import lru_cache

_TH_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")
_GRAD_WORDS = ("ปริญญาโท", "ปริญญาเอก", "ป.โท", "ป.เอก", "มหาบัณฑิต", "ดุษฎีบัณฑิต", "บัณฑิตศึกษา",
               "วท.ม", "ปร.ด", "master", "ph.d", "phd", "doctoral")
_PLAN_ASK = ("แผน", "แบบ", "หน่วยกิต", "วิทยานิพนธ์", "ค้นคว้าอิสระ")
_PLAN_RE = re.compile(r"([^|]*?(?:แผน|แบบ)[^|]*?)\s*\|?\s*(\d{2,3})\s*\|?\s*หน่วยกิต")
_CAT_RE = re.compile(r"([ก-ฮ])\.\s*(หมวดวิชา[^|]*?)\s*\|\s*(\d{1,3}|-)\s*\|\s*หน่วยกิต")


@dataclass
class Plan:
    name: str
    credits: int
    categories: list[tuple[str, str]] = field(default_factory=list)  # (หมวด, หน่วยกิต หรือ "-")


@dataclass
class GradStructure:
    version_id: int
    program: str
    year: int
    level: str  # master | doctoral
    document_id: str
    page: int
    plans: list[Plan]


def _clean(s: str) -> str:
    s = s.replace("ส าเร็จ", "สำเร็จ").replace("จ านวน", "จำนวน")  # สระอำที่หายจากหน้า PDF บางเล่ม
    s = re.sub(r"[-]", "", s)  # สัญลักษณ์ช่องติ๊กหน้าชื่อแผน (อักขระ Private Use)
    return re.sub(r"\s+", " ", s).strip(" |")


def _norm_key(s: str) -> str:
    return re.sub(r"[\s()]", "", s.split("(")[0])


def parse_structure(text: str) -> list[Plan]:
    """อ่านรายการแผนจาก 3.1.1 และหมวดวิชาของแต่ละแผนจาก 3.1.2."""
    f = re.sub(r"\s*\n\s*", " | ", text.translate(_TH_DIGITS)).replace("จ านวน", "จำนวน")
    if "Curriculum structure and credits" in f:
        return _parse_structure_en(f)
    i = f.find("3.1.1")
    j = f.find("3.1.2", i + 1)
    if i < 0 or j < 0:
        return []
    head = f[i:j].replace("จำนวนหน่วยกิตรวมตลอดหลักสูตร", "")
    plans = [Plan(_clean(m.group(1)), int(m.group(2))) for m in _PLAN_RE.finditer(head)]
    if not plans:
        return []
    # 3.1.2: หัวแผน แล้วตามด้วยหมวดวิชาของแผนนั้น จนถึงหัวแผนถัดไป
    end = re.search(r"3\.1\.3|3\.2\s", f[j:])
    body = f[j:j + end.start()] if end else f[j:j + 3000]
    keys = [_norm_key(p.name) for p in plans]
    current: Plan | None = None
    for tok_m in re.finditer(r"[^|]+", body):
        tok = tok_m.group(0)
        k = _norm_key(_clean(tok))
        if k and k in keys:
            current = plans[keys.index(k)]
            continue
        if current is not None:
            m = re.match(r"\s*([ก-ฮ])\.\s*(หมวดวิชา.*)", tok)
            if m:
                rest = body[tok_m.end():tok_m.end() + 30]
                n = re.match(r"\s*\|\s*(\d{1,3}|-)\s*\|\s*หน่วยกิต", rest)
                if n:
                    current.categories.append((_clean(m.group(2)), n.group(1)))
    return plans


def _parse_structure_en(f: str) -> list[Plan]:
    """เล่มหลักสูตรนานาชาติ (ภาษาอังกฤษ) เช่น ปริญญาเอก IT 2566:
    "3.1 Curriculum structure and credits | Type 1.1 (Applicants with Master's Degree) | 57 credits |
     a. Thesis courses | 57 credits | b. Research Courses | Non Credits | ..."
    """
    i = f.find("Curriculum structure and credits")
    end = re.search(r"\b3\.2\s+Courses", f[i:])
    body = f[i:i + end.start()] if end else f[i:i + 2500]
    plans: list[Plan] = []
    for tok in re.split(r"\|", body):
        tok = tok.strip()
        m = re.match(r"((?:Type|Plan)\s*\d(?:\.\d)?\s*(?:\([^)]*\))?)\s*$", tok)
        if m:
            plans.append(Plan(m.group(1), 0))
            continue
        if not plans:
            continue
        cur = plans[-1]
        n = re.match(r"(\d{1,3})\s*credits?$", tok, re.IGNORECASE)
        if n and cur.credits == 0 and not cur.categories:
            cur.credits = int(n.group(1))
            continue
        c = re.match(r"[a-z]\.\s*(.+)", tok)
        if c:
            cur.categories.append((c.group(1).strip(), ""))
            continue
        if cur.categories and not cur.categories[-1][1]:
            v = re.match(r"(\d{1,3})\s*credits?$|Non\s*Credits?$", tok, re.IGNORECASE)
            if v:
                cur.categories[-1] = (cur.categories[-1][0], v.group(1) or "-")
    return [p for p in plans if p.credits]


@lru_cache(maxsize=64)
def _structure_cached(db_key: str, document_id: str) -> tuple[int, tuple[Plan, ...]]:
    con = sqlite3.connect(db_key)
    try:
        rows = con.execute(
            "SELECT page_number, page_text FROM page WHERE document_id=? AND page_number BETWEEN 5 AND 40 "
            "ORDER BY page_number", (document_id,)
        ).fetchall()
    finally:
        con.close()
    for idx, (pg, text) in enumerate(rows):
        t = (text or "").translate(_TH_DIGITS)
        if ("3.1.1" in t and "หน่วยกิต" in t) or "Curriculum structure and credits" in t:
            joined = (text or "") + "\n" + (rows[idx + 1][1] if idx + 1 < len(rows) else "")
            plans = parse_structure(joined)
            if plans:
                return pg, tuple(plans)
    return 0, ()


def graduate_structures(conn: sqlite3.Connection, version_ids: list[int]) -> list[GradStructure]:
    db_file = conn.execute("PRAGMA database_list").fetchone()[2]
    out = []
    for vid in version_ids:
        for doc_id, level, prog, yr in conn.execute(
            "SELECT d.document_id, d.degree_level, cv.program, cv.curriculum_year FROM document d "
            "JOIN curriculum_version cv ON cv.version_id = d.version_id "
            "WHERE d.version_id = ? AND d.degree_level IN ('master','doctoral') "
            "AND (d.canonical_document_id IS NULL OR d.canonical_document_id = d.document_id)",
            (vid,),
        ).fetchall():
            pg, plans = _structure_cached(db_file, doc_id)
            if plans:
                out.append(GradStructure(vid, prog, yr, level, doc_id, pg, list(plans)))
    return out


def detect_grad_plan_intent(question: str) -> bool:
    q = question.lower()
    grad = any(w in q for w in _GRAD_WORDS) or "AITBA" in question.upper()
    return grad and any(w in question for w in _PLAN_ASK)


def _wanted_plan(question: str, plans: list[Plan]) -> list[Plan]:
    q = question.translate(_TH_DIGITS)
    picks = []
    for p in plans:
        n = p.name
        if "วิชาชีพ" in q and "วิชาชีพ" in n or "วิชาการ" in q and "วิชาการ" in n:
            picks.append(p)
            continue
        for code in re.findall(r"\d\.\d", q):
            if code in n:
                picks.append(p)
        if re.search(r"แผน\s*ข", q) and re.search(r"แผน\s*ข", n):
            picks.append(p)
        for g in re.findall(r"ก\s*([12])", q):
            if re.search(rf"ก\s*{g}", n):
                picks.append(p)
    seen, out = set(), []
    for p in picks:
        if id(p) not in seen:
            seen.add(id(p))
            out.append(p)
    return out


def try_grad_plans(conn: sqlite3.Connection, question: str):
    from katrag.query.structured_query import StructuredResult, detect_program, detect_versions

    none = StructuredResult(False, "", "", "none")
    if not detect_grad_plan_intent(question):
        return none
    q = question.lower()
    level = "doctoral" if any(w in q for w in ("เอก", "ดุษฎี", "ph.d", "phd", "doctoral", "ปร.ด")) else "master"
    named = [v for v, _p, _y in detect_versions(conn, question)]
    if not named:
        program = detect_program(question)
        if not program:
            return none
        named = [
            r[0] for r in conn.execute(
                "SELECT DISTINCT cv.version_id FROM curriculum_version cv JOIN document d ON d.version_id = cv.version_id "
                "WHERE cv.program = ? AND cv.edition_status = 'current' AND d.degree_level = ?",
                (program, level),
            ).fetchall()
        ]
    structs = [s for s in graduate_structures(conn, named) if s.level == level] or graduate_structures(conn, named)
    if not structs:
        return none
    level_th = {"master": "ปริญญาโท", "doctoral": "ปริญญาเอก"}
    lines: list[str] = []
    pages: list[tuple[str, int, str]] = []
    # ถามแผนเฉพาะ → ขึ้นต้นด้วยคำตอบตรง ๆ ก่อนรายละเอียดทุกแผน
    for s in sorted(structs, key=lambda x: x.year):
        for p in _wanted_plan(question, s.plans):
            lines.append(f"{s.program} {s.year} ({level_th.get(s.level, s.level)}) {p.name}: ต้องเรียน {p.credits} หน่วยกิต")
    if lines:
        lines.append("")
    for s in sorted(structs, key=lambda x: x.year):
        lines.append(f"{s.program} {s.year} ({level_th.get(s.level, s.level)}) — จำนวนหน่วยกิตรวมตลอดหลักสูตร (หน้า {s.page}):")
        wanted = _wanted_plan(question, s.plans)
        for p in s.plans:
            mark = " ◀ แผนที่ถาม" if p in wanted else ""
            cats = ", ".join(f"{c} {v} หน่วยกิต" for c, v in p.categories if v != "-")
            lines.append(f"  - {p.name}: {p.credits} หน่วยกิต" + (f" ({cats})" if cats else "") + mark)
        pages.append((s.document_id, s.page, "จำนวนหน่วยกิตรวมและโครงสร้างหลักสูตร"))
    if len(structs) > 1:
        same = len({tuple(p.credits for p in s.plans) for s in structs}) == 1
        lines.append("")
        lines.append("หน่วยกิตรวมของทุกแผนเท่ากันทั้งสองฉบับ" if same else "หน่วยกิตรวมของแผนต่างกันระหว่างฉบับ")
        names = [" / ".join(p.name for p in s.plans) for s in sorted(structs, key=lambda x: x.year)]
        lines.append("ชื่อแผน: " + " → ".join(names))
    label = ", ".join(f"{s.program} {s.year}" for s in structs)
    return StructuredResult(True, "\n".join(lines), label, "grad_plans", version_id=structs[0].version_id, pages=pages)
