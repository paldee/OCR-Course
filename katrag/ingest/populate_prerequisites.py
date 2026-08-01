"""Populate prerequisite fields จากหน้าคำอธิบายรายวิชา.

Pattern ในเอกสาร:
    06026201 แคลคูลัส 2  CALCULUS 2
    วิชาบังคับก่อน :  06026200 แคลคูลัส 1
    PREREQUISITE : 06026200 CALCULUS 1

สกัดรหัสวิชาบังคับก่อน (8 หลัก) → update course.prerequisite_json + prerequisite_raw
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

_CODE_RE = re.compile(r"\b(\d{8})\b")
# บรรทัด "วิชาบังคับก่อน : ..." จนถึง newline หรือ "PREREQUISITE"
_PREREQ_TH_RE = re.compile(r"วิชาบังคับก่อน\s*:?\s*(.*?)(?:\n|PREREQUISITE|$)", re.DOTALL)
_PREREQ_EN_RE = re.compile(r"PREREQUISITE\s*:?\s*(.*?)(?:\n|$)", re.IGNORECASE | re.DOTALL)

_NONE_MARKERS = ["ไม่มี", "none", "-"]


# credit pattern ที่ปิดท้าย metadata ของวิชา เช่น 3(3-0-6)
_CREDIT_RE = re.compile(r"\d\s*\(\d-\d-\d+\)")


def populate(db_path: Path | str) -> dict[str, int]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    rows = conn.execute("""
        SELECT chunk_id, text, version_id FROM chunk
        WHERE text LIKE '%บังคับก่อน%'
        ORDER BY version_id, page_number
    """).fetchall()

    updated = 0
    with_prereq = 0
    seen: set[tuple[str, int]] = set()

    for row in rows:
        text = row["text"]
        version_id = row["version_id"]

        # วนที่ตำแหน่งของ "วิชาบังคับก่อน" แต่ละครั้ง
        for m in re.finditer(r"วิชาบังคับก่อน", text):
            prereq_pos = m.start()

            # course code = รหัส 8 หลักตัวสุดท้ายก่อน "วิชาบังคับก่อน"
            codes_before = list(_CODE_RE.finditer(text[:prereq_pos]))
            if not codes_before:
                continue
            course_code = codes_before[-1].group(1)

            key = (course_code, version_id)
            if key in seen:
                continue

            # prereq window = ตั้งแต่ "วิชาบังคับก่อน" ถึง credit pattern แรก (หรือ +200 ตัว)
            window = text[prereq_pos:prereq_pos + 250]
            credit_m = _CREDIT_RE.search(window)
            if credit_m:
                window = window[:credit_m.start()]

            # หารหัส prereq ในหน้าต่าง (ไม่นับรหัสตัวเอง)
            prereq_codes = [c for c in _CODE_RE.findall(window) if c != course_code]
            # unique คงลำดับ
            prereq_codes = list(dict.fromkeys(prereq_codes))

            window_lower = window.lower()
            if not prereq_codes and any(mk in window_lower for mk in _NONE_MARKERS):
                raw = "ไม่มี"
            else:
                raw = " ".join(window.split())[:200] or "ไม่มี"

            cur = conn.execute(
                "UPDATE course SET prerequisite_json=?, prerequisite_raw=? "
                "WHERE code=? AND version_id=?",
                (json.dumps(prereq_codes, ensure_ascii=False), raw, course_code, version_id),
            )
            if cur.rowcount > 0:
                seen.add(key)
                updated += 1
                if prereq_codes:
                    with_prereq += 1

    conn.commit()
    conn.close()
    return {"updated": updated, "with_prereq": with_prereq}


if __name__ == "__main__":
    db = Path(__file__).resolve().parent.parent.parent / "artifacts" / "katrag.sqlite3"
    print(f"Populating prerequisites: {db}")
    result = populate(db)
    print(f"Done! Updated: {result['updated']} courses, "
          f"with prerequisite: {result['with_prereq']}")
