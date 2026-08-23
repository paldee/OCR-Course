"""Populate prerequisite fields จากหน้าคำอธิบายรายวิชา.

วิธีที่ 3 (block-by-code): ตัดข้อความเป็นบล็อกตรงตำแหน่ง "รหัส 8 หลัก + credit
ในระยะ 200 ตัว" ซึ่ง = course header แน่ ๆ (ต่างจากรหัส prereq ที่ไม่มี credit ตาม)
แต่ละบล็อก = หนึ่งรายวิชา → หา "วิชาบังคับก่อน" ภายในบล็อก → เก็บรหัส prereq

ปัญหาเดิม:
1. (v1) จับรหัสตัวสุดท้ายก่อน keyword → match ผิดตัวเมื่อรหัส prereq อยู่ก่อน keyword
2. (v2) ตัดบล็อกที่ credit → body กินรหัสวิชาถัดไป (ที่ยังไม่ถูก credit ตัดออก)
ทั้งสองวิธีล้มเหลวกับ IT เพราะหน้าคำอธิบายรายวิชาจัดหลายวิชาต่อ chunk
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

_CODE_RE = re.compile(r"\b(\d{8})\b")
_CREDIT_RE = re.compile(r"\d\s*\(\d-\d-\d+\)")
_PREREQ_KW = re.compile(r"วิชาบังคับก่อน|PREREQUISITE", re.IGNORECASE)
_NONE_MARKERS = ["ไม่มี", "none", "-"]


def populate(db_path: Path | str) -> dict[str, int]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    rows = conn.execute("""
        SELECT chunk_id, text, version_id FROM chunk
        WHERE text LIKE '%บังคับก่อน%' OR text LIKE '%PREREQUISITE%'
        ORDER BY version_id, page_number
    """).fetchall()

    updated = 0
    with_prereq = 0
    seen: set[tuple[str, int]] = set()

    for row in rows:
        text = row["text"]
        version_id = row["version_id"]

        # ── ตัดข้อความเป็นบล็อกตรงรหัส 8 หลักที่มี credit ตามหลัง ──
        code_positions = list(_CODE_RE.finditer(text))
        if not code_positions:
            continue

        # หา "course header positions" = รหัสที่มี credit ในระยะ 200 ตัว
        header_positions: list[tuple[int, str]] = []  # (start_pos, code)
        for cm in code_positions:
            lookahead = text[cm.start():cm.start() + 200]
            if _CREDIT_RE.search(lookahead):
                header_positions.append((cm.start(), cm.group(1)))

        if not header_positions:
            continue

        # สร้าง blocks
        for i, (hpos, code) in enumerate(header_positions):
            end_pos = header_positions[i + 1][0] if i + 1 < len(header_positions) else len(text)
            block = text[hpos:end_pos]

            key = (code, version_id)
            if key in seen:
                continue

            # มี keyword prerequisite ใน block?
            kw_m = _PREREQ_KW.search(block)
            if not kw_m:
                continue

            # หารหัส prereq ใน window 300 ตัวหลัง keyword
            prereq_window = block[kw_m.start():kw_m.start() + 300]
            prereq_codes = [c for c in _CODE_RE.findall(prereq_window) if c != code]
            prereq_codes = list(dict.fromkeys(prereq_codes))

            block_lower = block.lower()
            if not prereq_codes and any(mk in block_lower for mk in _NONE_MARKERS):
                raw = "ไม่มี"
            else:
                raw = " ".join(prereq_window.split())[:200] or "ไม่มี"

            cur = conn.execute(
                "UPDATE course SET prerequisite_json=?, prerequisite_raw=? "
                "WHERE code=? AND version_id=?",
                (json.dumps(prereq_codes, ensure_ascii=False), raw, code, version_id),
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
