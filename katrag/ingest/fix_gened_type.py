"""แก้ type ของวิชาศึกษาทั่วไป (gen-ed) นอกแผนการเรียน ที่ถูกติดป้าย 'บังคับ' ผิด.

ต้นเหตุ (populate_courses.py)
------------------------------
`type` ของวิชาบนหน้าที่ไม่ใช่หน้าแผนการเรียน (`is_plan_page=False`) มาจาก
`type_from_carry` — ค่า marker "วิชาบังคับ/วิชาเลือก" ตัวล่าสุดที่เจอ carry
ข้ามหน้ามา หน้า catalog วิชาศึกษาทั่วไป ("รายละเอียดหลักสูตร N มคอ.2") ไม่มี
marker แบบนี้อยู่ในหน้าตัวเองเลย (ยืนยันแล้วด้วยการค้น "วิชาบังคับ/วิชาเลือก"
ในทุก chunk ของหน้าตัวอย่าง = 0 chunk) ค่าที่ได้จึงเป็นเศษที่รั่วมาจากหมวด
ก่อนหน้าในเล่มเดียวกัน ซึ่งบังเอิญเป็น "บังคับ" บ่อยกว่า

ทำไมแก้ปลอดภัย (ขอบเขตที่ยืนยันแล้วก่อนเขียน UPDATE)
------------------------------------------------------
เงื่อนไข `category = 'หมวดวิชาศึกษาทั่วไป' AND year IS NULL AND type = 'บังคับ'`
ตรวจแล้วว่าตรงกับกลุ่มวิชา gen-ed นอกแผนทั้งหมดเป๊ะ 260/260 แถว (AIT 183 + IT 77
ในเวอร์ชัน current) ไม่มีวิชาไหนในกลุ่มนี้ที่ category เป็นอย่างอื่นเลย — field
`category` เป็นสัญญาณที่แม่นอยู่แล้ว (accuracy 0.921 ต่อ teacher GT วัดในรอบก่อน)
เพราะ regex `_CATEGORY_RE` จับ marker "หมวดวิชาศึกษาทั่วไป" ได้ตรงบนหน้าที่มี
header หมวดจริง จึงใช้ category เป็นตัวชี้ทาง override type แทนการแก้
carry-forward logic ที่ซับซ้อนกว่าและเสี่ยงกระทบวิชาในแผนที่ label ถูกอยู่แล้ว

ทำไมไม่แก้ carry-forward logic ตรง ๆ ใน populate_courses.py
-------------------------------------------------------------
รอบก่อนเคยลองเลิกใช้ carry-forward type ทั้งระบบแล้ว type accuracy ของ DSBA
ตกจาก 0.96 เหลือ 0.41 เพราะทิ้ง label ที่เคยถูกไปเป็น NULL จำนวนมาก การแก้
ที่ carry-forward logic ตรง ๆ เสี่ยงกระทบวิชาในแผนที่ label ถูกอยู่แล้ว
งานนี้จึงเลือกแก้เฉพาะจุดหลัง populate เสร็จ ด้วยเงื่อนไขที่แคบและยืนยันแล้ว
ว่าไม่ทับ record อื่น (`year IS NULL` กันไม่ให้แตะวิชาบังคับจริงในแผน)

Usage:
    python -m katrag.ingest.fix_gened_type
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

_TARGET_WHERE = (
    "category = 'หมวดวิชาศึกษาทั่วไป' AND year IS NULL AND type = 'บังคับ' "
    "AND version_id IN ("
    "  SELECT version_id FROM curriculum_version WHERE edition_status='current'"
    ")"
)


def fix(db_path: Path | str) -> dict[str, int]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    before = conn.execute(f"SELECT COUNT(*) FROM course WHERE {_TARGET_WHERE}").fetchone()[0]

    cur = conn.execute(f"UPDATE course SET type='เลือก' WHERE {_TARGET_WHERE}")
    updated = cur.rowcount

    after = conn.execute(f"SELECT COUNT(*) FROM course WHERE {_TARGET_WHERE}").fetchone()[0]

    conn.commit()
    conn.close()
    return {"matched_before": before, "updated": updated, "remaining": after}


def main() -> None:
    db = Path(__file__).resolve().parent.parent.parent / "artifacts" / "katrag.sqlite3"
    print(f"Fixing gen-ed type: {db}")
    result = fix(db)
    print(f"Done! matched: {result['matched_before']}, updated: {result['updated']}, "
          f"remaining (should be 0): {result['remaining']}")


if __name__ == "__main__":
    main()
