"""Populate ตาราง `person` — อาจารย์ประจำหลักสูตร (มคอ.2 หมวดที่ 5) จาก chunk.heading.

ทำไมต้องมีไฟล์นี้
------------------
ไม่มีตาราง person/faculty ในฐานเลย คำถามอาจารย์ทุกสาขาตกไปที่ LLM อ่าน chunk
ซึ่งตอบไม่ครบ/ผิดหมวด (ดู docs/removed_subsystems.md) แทนการรื้อ table_cell
extractor ที่ถูกลบไปแล้ว ใช้ประโยชน์จาก heading ที่สม่ำเสมอ
"N. คำนำหน้า ชื่อ สกุล" มาสกัดตรง ๆ

ขอบเขตที่ตรวจยืนยันแล้วก่อนเขียน (ต่างกันตามที่เล่มมีจริง — ไม่เดาให้ครบ)
-------------------------------------------------------------------------
AIT / AITBA / IT(เฉพาะ v13) มี sub-heading ชัด 3 บทบาท:
  1.1 อาจารย์ผู้รับผิดชอบหลักสูตร  (responsible)
  1.2 อาจารย์ประจำหลักสูตร        (regular)
  1.3 อาจารย์ผู้สอนที่เป็นอาจารย์ประจำ (teaching_regular)

DSBA / BIT ไม่มี sub-heading แบบนั้นเลย — ตรวจแล้วว่ารายชื่อ 5 คน (ผู้รับผิดชอบ
หลักสูตร) ปรากฏซ้ำ 3 รอบในหน้าต่างกันของเล่มเดียวกัน (ยืนยันว่าเป็นเนื้อหาเดียว
ซ้ำ ไม่ใช่คนละกลุ่ม) แต่ไม่มีรายชื่อ "อาจารย์ประจำหลักสูตร" แยกออกมาต่างหาก —
ภาคผนวก ค/ง/จ/ฉ ที่เจอคือประวัติอาจารย์รายบุคคลประกอบ ไม่ใช่ role อื่น
จึงเก็บแค่ role='responsible' สำหรับสองหลักสูตรนี้ ไม่เดาว่า role อื่นมีใคร

IT มี 3 curriculum_version ที่ edition_status='current' พร้อมกัน (v7=315วิชา,
v10=27, v13=75 — ปัญหาที่รู้ตัวแล้ว) แต่ role header มีอยู่ใน v13 เท่านั้น
ไม่ใช่ v7 ที่ course/rule table ใช้ (resolve ด้วยจำนวนวิชามากสุด) เพราะข้อมูล
อาจารย์ผูกกับ "เล่มที่มีข้อมูลนี้จริง" ไม่ใช่ "เล่มที่มีวิชามากสุด" จึง resolve
version ของ person แยกจาก resolve ของ course/rule

AITBA มี 2 document ที่ sha256 เหมือนกัน (ดูภาคผนวก 4 ใน removed_subsystems.md)
ทั้งสองมี heading ชุดเดียวกันซ้ำ ต้องกรองเหลือ document_id ที่เป็น
canonical_document_id ของตัวเองเท่านั้น ไม่งั้นได้ sequence_no ซ้ำ (UNIQUE ชน)

Usage:
    python -m katrag.ingest.populate_person
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

_NAME_RE = re.compile(r"^\s*(\d+)\.\s*((?:ศ|รศ|ผศ|อ|ดร)[.\s][^\n]{2,60})")

# sub-topic ที่ซ้ำในทุกอาจารย์ (ตามด้วย 1./2./3. เสมอ) — ไม่ใช่คนใหม่และไม่ใช่
# จุดจบ section ต้องข้ามได้โดยไม่ปิด current_role (ยืนยันจาก trace จริงของ AIT
# v1: '1. งานวิจัย' / '2. ตำราเรียน:' / '3. ภาระงานสอน' ปรากฏหลังชื่อทุกคน)
_PER_PERSON_SUBTOPIC_RE = re.compile(r"^\s*\d+\.\s*(งานวิจัย|ตำราเรียน|ภาระงานสอน)")

# sub-heading ที่แบ่ง 3 บทบาท (AIT/AITBA/IT-v13)
_ROLE_HEADERS: tuple[tuple[str, str], ...] = (
    ("1.1", "responsible"),
    ("1.2", "regular"),
    ("1.3", "teaching_regular"),
)

#: หลักสูตรที่มี role header ครบ 3 บทบาท + version_id เจาะจง (ไม่ใช้ resolve ปกติ
#: เพราะ IT ต้องชี้ v13 ไม่ใช่ v7 ที่ course/rule table ใช้)
_STRUCTURED_VERSIONS: dict[str, int] = {
    "AIT": 1,
    "AITBA": 8,
    "IT": 13,
}

#: หลักสูตรที่เล่มมีแค่รายชื่อ "อาจารย์ผู้รับผิดชอบหลักสูตร" (ไม่แยก role อื่น)
_RESPONSIBLE_ONLY_VERSIONS: dict[str, int] = {
    "DSBA": 5,
    "BIT": 3,
}


@dataclass(frozen=True)
class ParsedPerson:
    role: str
    sequence_no: int
    name_raw: str
    chunk_id: int
    provenance_id: int


def _document_filter_sql(conn: sqlite3.Connection, version_id: int) -> tuple[str, list]:
    """คืนเงื่อนไข SQL ที่กรองเฉพาะ canonical document (กัน AITBA duplicate).

    ถ้า version มีหลาย document ที่ sha256 เหมือนกัน (canonical_document_id
    ชี้ไปตัวเดียวกัน) จะเหลือ document_id ที่ canonical_document_id = ตัวเอง
    เท่านั้น ป้องกัน heading ชุดเดียวกันถูกอ่านซ้ำสองรอบ
    """
    rows = conn.execute(
        "SELECT document_id FROM document "
        "WHERE version_id=? AND document_id = canonical_document_id",
        (version_id,),
    ).fetchall()
    doc_ids = [r[0] for r in rows]
    if not doc_ids:
        return "", []
    placeholders = ",".join("?" * len(doc_ids))
    return f"AND document_id IN ({placeholders})", doc_ids


def _parse_structured(conn: sqlite3.Connection, version_id: int) -> list[ParsedPerson]:
    """parse 3 บทบาทจาก sub-heading 1.1/1.2/1.3 (AIT/AITBA/IT-v13)."""
    doc_filter, doc_params = _document_filter_sql(conn, version_id)
    rows = conn.execute(
        f"SELECT chunk_id, page_number, heading, provenance_id FROM chunk "
        f"WHERE version_id=? AND COALESCE(is_boilerplate,0)=0 AND heading != '' {doc_filter} "
        f"ORDER BY chunk_id",
        (version_id, *doc_params),
    ).fetchall()

    out: list[ParsedPerson] = []
    current_role: str | None = None
    seq_in_role = 0

    for r in rows:
        heading = r["heading"] or ""

        # เจอ sub-heading ใหม่ → สลับบทบาท
        matched_role = None
        for marker, role in _ROLE_HEADERS:
            if heading.startswith(marker):
                matched_role = role
                break
        if matched_role:
            current_role = matched_role
            seq_in_role = 0
            continue

        if current_role is None:
            continue

        m = _NAME_RE.match(heading)
        if not m:
            # sub-topic ที่แทรกอยู่ระหว่างคน (งานวิจัย/ตำราเรียน/ภาระงานสอน)
            # ไม่ใช่จุดจบ section — ข้ามไปโดยไม่ปิด current_role
            if _PER_PERSON_SUBTOPIC_RE.match(heading):
                continue
            # heading อื่นที่ไม่ใช่ชื่อคน ไม่ใช่ sub-topic และไม่ใช่ sub-heading
            # role ใหม่ = จบ section นี้แล้ว (กันไม่ให้เดินยาวไปเก็บชื่อที่อยู่
            # นอกหมวดนี้ต่อ — ยืนยันด้วยการนับจำนวนคนต่อเนื่องจริงก่อนเขียน
            # stop condition นี้)
            current_role = None
            continue
        seq_in_role += 1
        out.append(ParsedPerson(
            role=current_role,
            sequence_no=seq_in_role,
            name_raw=" ".join(m.group(2).split()),
            chunk_id=r["chunk_id"],
            provenance_id=r["provenance_id"],
        ))
    return out


def _parse_responsible_only(conn: sqlite3.Connection, version_id: int) -> list[ParsedPerson]:
    """parse แค่ role='responsible' จากกลุ่มชื่อแรกที่เจอ (DSBA/BIT).

    รายชื่อกลุ่มนี้ปรากฏซ้ำหลายรอบในเล่ม (ยืนยันแล้วว่าเนื้อหาเดียวกัน) ใช้
    กลุ่มแรกที่เจอเป็นตัวแทน แล้วยืนยันว่ารอบถัดไป (ถ้ามี) ตรงกันก่อนเชื่อ
    """
    doc_filter, doc_params = _document_filter_sql(conn, version_id)
    rows = conn.execute(
        f"SELECT chunk_id, page_number, heading, provenance_id FROM chunk "
        f"WHERE version_id=? AND COALESCE(is_boilerplate,0)=0 AND heading != '' {doc_filter} "
        f"ORDER BY chunk_id",
        (version_id, *doc_params),
    ).fetchall()

    groups: list[list[tuple]] = []
    current: list[tuple] = []
    prev_num = 0
    for r in rows:
        m = _NAME_RE.match(r["heading"] or "")
        if not m:
            continue
        num = int(m.group(1))
        if num == 1 and current:
            groups.append(current)
            current = []
        current.append((num, r["chunk_id"], r["provenance_id"], " ".join(m.group(2).split())))
        prev_num = num
    if current:
        groups.append(current)

    # กลุ่มแรกที่มีอย่างน้อย 3 ชื่อขึ้นไป (กันกลุ่มเล็ก ๆ ที่เป็นภาคผนวก CV เดี่ยว)
    first_real = next((g for g in groups if len(g) >= 3), None)
    if not first_real:
        return []

    # ยืนยันไขว้กับกลุ่มอื่นที่มีขนาดเท่ากัน — เทียบแบบ prefix (ไม่ใช่ exact)
    # เพราะ heading บางรอบถูกตัดสั้นกว่ารอบอื่น (ความยาวต่างกันได้ใน OCR/chunk
    # คนละรอบ) แต่ต้องเป็นคนเดิมตามลำดับเดิม ไม่ใช่คนละคน — ถ้า prefix ไม่ตรง
    # กันเลย (คนละคน) ถือว่าไม่มั่นใจพอ ไม่ insert เลือกเวอร์ชันชื่อที่ยาวที่สุด
    # ต่อตำแหน่งเป็นตัวแทน (สมบูรณ์กว่า)
    #
    # เทียบโดยตัดช่องว่างทั้งหมดออกก่อน (ไม่ใช่แค่ยุบวรรคซ้อน) เพราะ OCR
    # บางรอบมีวรรคหลังคำนำหน้า ("ดร. ชยานนท์") บางรอบไม่มี ("ดร.ชยานนท์")
    # ทั้งที่เป็นคนเดียวกัน — ยืนยันจากเคสจริงของ DSBA sequence_no=4
    def _norm(s: str) -> str:
        return re.sub(r"\s+", "", s)

    same_size = [g for g in groups if len(g) == len(first_real)]
    best_names = [x[3] for x in first_real]
    for g in same_size[1:3]:  # เทียบไม่เกิน 2 กลุ่มถัดไปพอ
        other_names = [x[3] for x in g]
        for i, (a, b) in enumerate(zip(best_names, other_names)):
            na, nb = _norm(a), _norm(b)
            shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
            if not longer.startswith(shorter):
                return []  # คนละคน = ไม่มั่นใจพอ ไม่ insert
            if len(b) > len(best_names[i]):
                best_names[i] = b

    return [
        ParsedPerson(role="responsible", sequence_no=i + 1, name_raw=name,
                     chunk_id=first_real[i][1], provenance_id=first_real[i][2])
        for i, name in enumerate(best_names)
    ]


def populate(db_path: Path | str) -> dict[str, int]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    inserted = 0
    programs_done: list[str] = []

    for program, version_id in _STRUCTURED_VERSIONS.items():
        people = _parse_structured(conn, version_id)
        for p in people:
            conn.execute(
                "INSERT OR REPLACE INTO person "
                "(version_id, role, sequence_no, name_raw, provenance_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (version_id, p.role, p.sequence_no, p.name_raw, p.provenance_id),
            )
            inserted += 1
        programs_done.append(f"{program}(v{version_id})={len(people)}")

    for program, version_id in _RESPONSIBLE_ONLY_VERSIONS.items():
        people = _parse_responsible_only(conn, version_id)
        for p in people:
            conn.execute(
                "INSERT OR REPLACE INTO person "
                "(version_id, role, sequence_no, name_raw, provenance_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (version_id, p.role, p.sequence_no, p.name_raw, p.provenance_id),
            )
            inserted += 1
        programs_done.append(f"{program}(v{version_id})={len(people)}")

    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM person").fetchone()[0]
    conn.close()
    return {"inserted": inserted, "total_in_db": total, "detail": ", ".join(programs_done)}


def main() -> None:
    db = Path(__file__).resolve().parent.parent.parent / "artifacts" / "katrag.sqlite3"
    print(f"Populating person: {db}")
    result = populate(db)
    print(f"Done! inserted: {result['inserted']}, total in DB: {result['total_in_db']}")
    print(f"  detail: {result['detail']}")


if __name__ == "__main__":
    main()
