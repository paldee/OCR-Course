"""รายชื่ออาจารย์ผู้รับผิดชอบ/อาจารย์ประจำหลักสูตร อ่านจากหัวข้อ 3.2.1 / 3.2.2 ในเล่ม มคอ.2.

ตาราง `person` มีข้อมูลไม่ครบ (DSBA/BIT มีแต่ผู้รับผิดชอบ, IT ปริญญาตรีไม่มีเลย) ทั้งที่เล่มมีหัวข้อ
"3.2.2 อาจารย์ประจำหลักสูตร" ครบ รายชื่อในเล่มเรียงเป็น "1. รศ.ดร.ชื่อ นามสกุล" ตามด้วยเลขประจำตัว
ประชาชนและประวัติ — เก็บเฉพาะลำดับและชื่อ ไม่เก็บเลขประจำตัว
"""

from __future__ import annotations

import re
import sqlite3
from functools import lru_cache

_SECTION = {
    "responsible": re.compile(r"3\.\d\.1\s*อาจารย์ผู้รับผิดชอบหลักสูตร"),
    "regular": re.compile(r"3\.\d\.2\s*อาจารย์ประจำหลักสูตร"),
    "teaching": re.compile(r"3\.\d\.3\s*อาจารย์ผู้สอน"),
}
_SECTION_END = re.compile(r"3\.\d\.[3-9]\s*อาจารย์|3\.\d\.[3-9]\s|อาจารย์พิเศษ|หมวดที่\s*4|4\.\s*องค์ประกอบ")
_TITLE = r"(?:ศาสตราจารย์|รองศาสตราจารย์|ผู้ช่วยศาสตราจารย์|ศ\.|รศ\.|ผศ\.|อ\.|ดร\.|Asst\.|Assoc\.|Prof\.|Dr\.)"
_ENTRY = re.compile(rf"(?:^|\n|\|)\s*(\d{{1,2}})\.\s*({_TITLE}[^\n|()\d]{{2,60}})(?:\s*\|?\s*\n?\s*([ก-๙]{{2,30}})(?=\s*(?:\n|\||\()))?")


@lru_cache(maxsize=32)
def _cached(db_file: str, version_id: int) -> tuple[tuple[str, tuple[tuple[int, str, int], ...]], ...]:
    conn = sqlite3.connect(db_file)
    try:
        return tuple((k, tuple(v)) for k, v in _parse(conn, version_id).items())
    finally:
        conn.close()


def faculty_lists(conn: sqlite3.Connection, version_id: int) -> dict[str, list[tuple[int, str, int]]]:
    """{'responsible'|'regular': [(ลำดับ, ชื่อ, หน้า), ...]} จากเล่มของเวอร์ชันนี้."""
    row = conn.execute("PRAGMA database_list").fetchone()
    if row and row[2]:
        return {k: list(v) for k, v in _cached(row[2], version_id)}
    return _parse(conn, version_id)


def _parse(conn: sqlite3.Connection, version_id: int) -> dict[str, list[tuple[int, str, int]]]:
    doc = conn.execute(
        "SELECT document_id FROM document WHERE version_id=? ORDER BY document_id LIMIT 1", (version_id,)
    ).fetchone()
    if not doc:
        return {}
    pages = conn.execute(
        "SELECT page_number, page_text FROM page WHERE document_id=? AND page_number BETWEEN 20 AND 80 ORDER BY page_number",
        (doc[0],),
    ).fetchall()
    out: dict[str, list[tuple[int, str, int]]] = {}
    for role, start_re in _SECTION.items():
        names: list[tuple[int, str, int]] = []
        active = False
        for page_no, text in pages:
            seg = text
            if not active:
                m = start_re.search(text)
                if not m:
                    continue
                active = True
                seg = text[m.end():]
            end = _SECTION_END.search(seg)
            if end:
                seg = seg[: end.start()]
            for m in _ENTRY.finditer(seg):
                n = int(m.group(1))
                if names and n != names[-1][0] + 1:
                    continue
                if not names and n != 1:
                    continue
                name = re.sub(r"\s+", " ", m.group(2)).strip()
                if m.group(3) and not re.search(r"สาขา|วิชา", m.group(3)):
                    # ชื่อที่ขึ้นบรรทัดใหม่กลางนามสกุล (นามสกุลไทยไม่มีเว้นวรรค) — ต่อกันโดยไม่เว้น
                    name = f"{name}{m.group(3)}"
                names.append((n, name, page_no))
            if end:
                break
        if names:
            out[role] = names
    return out


# ══════════════════════════════════════════════════════════════════════
# วุฒิการศึกษาของอาจารย์ (จากตารางประวัติอาจารย์ในหัวข้อ 3.2)
# ══════════════════════════════════════════════════════════════════════

_EDU_ASK = ("จบการศึกษา", "จบจาก", "จบที่", "จบอะไร", "วุฒิ", "ประวัติการศึกษา", "เรียนจบ", "จบปริญญา", "สำเร็จการศึกษาจาก")
_EDU_STOP = re.compile(r"\|\s*(?:\d\.\s*)?(?:งานวิจัย|ผลงานทางวิชาการ|ตำรา|ภาระงานสอน|ประสบการณ์)|\|\s*\d{1,2}\.\s*" + _TITLE)
_NAME_IN_Q = re.compile(r"(?:อาจารย์|อ\.|ดร\.|ผศ\.|รศ\.|ศ\.)\s*(?:ดร\.)?\s*([ก-๙]{2,25})(?:\s+([ก-๙]{2,30}))?")


def detect_person_education(question: str) -> tuple[str, str] | None:
    """(ชื่อ, นามสกุล) ของอาจารย์ที่ถามวุฒิการศึกษา หรือ None."""
    if not any(k in question for k in _EDU_ASK):
        return None
    m = _NAME_IN_Q.search(question)
    if not m:
        return None
    first, last = m.group(1), (m.group(2) or "")
    for suffix in ("จบการศึกษา", "จบ", "มีวุฒิ", "วุฒิ", "เรียน"):
        if last.startswith(suffix):
            last = ""
        first = first.split(suffix)[0] if suffix in first and len(first.split(suffix)[0]) >= 2 else first
    return first, last


def _degrees_in(text: str, first: str) -> list[str]:
    flat = re.sub(r"\s*\n\s*", " | ", text)
    out: list[str] = []
    for m in re.finditer(re.escape(first), flat):
        head = flat[max(0, m.start() - 40):m.start()]
        if not re.search(_TITLE, head):
            continue  # ชื่อในย่อหน้าอื่น (เช่นรายชื่อผลงาน) ไม่ใช่หัวประวัติ
        block = flat[m.end():m.end() + 900]
        stop = _EDU_STOP.search(block)
        block = block[:stop.start()] if stop else block
        if " - " not in f" {block}" and "| -" not in block:
            continue
        items = [re.sub(r"\s*\|\s*", " ", x).strip(" |,") for x in re.split(r"\|\s*-\s+", "|" + block.split("| -", 1)[-1] if "| -" in block else block)]
        degrees = [re.sub(r"\(\d{13}\)|\d{13}", "", x).strip() for x in items if re.search(r"\d{4}", x)]
        if degrees:
            out = degrees
            break
    return out


def try_person_education(conn: sqlite3.Connection, question: str):
    """วุฒิการศึกษาของอาจารย์ตามตารางประวัติในเล่มของหลักสูตรที่เลือก."""
    from katrag.query.structured_query import StructuredResult, _course_scope_versions, _versions_label, detect_program

    none = StructuredResult(False, "", "", "none")
    who = detect_person_education(question)
    program = detect_program(question)
    if not who or not program:
        return none
    first, last = who
    version_ids = _course_scope_versions(conn, question, program)

    def search(vids):
        for vid in vids:
            for doc_id, pg, text in conn.execute(
                "SELECT d.document_id, p.page_number, p.page_text FROM page p JOIN document d ON d.document_id = p.document_id "
                "WHERE d.version_id = ? AND p.page_text LIKE ? ORDER BY p.page_number",
                (vid, f"%{first}%"),
            ):
                deg = _degrees_in(text or "", first)
                if deg:
                    return vid, doc_id, pg, deg
        return None

    own = search(version_ids)
    others_ids = [
        r[0] for r in conn.execute(
            "SELECT DISTINCT cv.version_id FROM curriculum_version cv JOIN document d ON d.version_id = cv.version_id "
            "WHERE cv.edition_status = 'current'"
        ) if r[0] not in version_ids
    ]
    other = None
    for vid in others_ids:
        hit = search([vid])
        if hit and (not own or len(hit[3]) > len(own[3])):
            other = hit if not other or len(hit[3]) > len(other[3]) else other
    if not own and not other:
        return none
    lines: list[str] = []
    pages: list[tuple[str, int, str]] = []
    name = f"{first} {last}".strip()
    if own:
        vid, doc_id, pg, deg = own
        lines.append(f"ประวัติการศึกษาของอาจารย์{name} ตามเล่ม {_versions_label(conn, [vid])} (หน้า {pg}):")
        lines += [f"  - {d}" for d in deg]
        pages.append((doc_id, pg, "ประวัติอาจารย์"))
    if other:
        vid, doc_id, pg, deg = other
        lines.append(("เล่ม" if not own else "เล่มอื่นระบุไว้ครบกว่า — เล่ม") + f" {_versions_label(conn, [vid])} (หน้า {pg}):")
        lines += [f"  - {d}" for d in deg]
        pages.append((doc_id, pg, "ประวัติอาจารย์"))
    return StructuredResult(True, "\n".join(lines), name, "person_education", pages=pages)
