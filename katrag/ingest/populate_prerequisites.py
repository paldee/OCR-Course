"""Populate prerequisite fields จากหน้าคำอธิบายรายวิชา.

วิธีที่ 4 (block-by-code + prereq zone): ตัดข้อความเป็นบล็อกตรงตำแหน่ง
"รหัส 8 หลัก + credit ในระยะ 200 ตัว" ซึ่ง = course header แต่**ยกเว้น**รหัสที่ตกอยู่ใน
"prereq zone" (ช่วงหลังคำว่า วิชาบังคับก่อน/PREREQUISITE) เพราะรหัสตรงนั้นคือรหัสของวิชา
ที่ถูกอ้างเป็นเงื่อนไข ไม่ใช่หัวรายวิชาใหม่

ปัญหาเดิม:
1. (v1) จับรหัสตัวสุดท้ายก่อน keyword → match ผิดตัวเมื่อรหัส prereq อยู่ก่อน keyword
2. (v2) ตัดบล็อกที่ credit → body กินรหัสวิชาถัดไป (ที่ยังไม่ถูก credit ตัดออก)
3. (v3) รหัส prereq ถูกนับเป็น course header เมื่อเอกสารวาง credit ไว้**ท้าย**บล็อก
   (รูปแบบของ DSBA/AIT ฉบับใหม่) ทำให้บล็อกถูกตัดคาที่ "วิชาบังคับก่อน :" พอดี
   เหลือ window ที่ไม่มีรหัสให้จับ → prerequisite_json = [] ทั้งหลักสูตร
   เช่น DSBA 2565: `06026212 ... วิชาบังคับก่อน : 06066300 ... PREREQUISITE : 06066300 ... 3(2-2-5)`
   รหัส 06066300 มี credit ของวิชาแม่อยู่ในระยะ 200 ตัว จึงถูกเข้าใจผิดว่าเป็นหัวรายวิชา
   (DSBA 2560 รอดเพราะวาง credit ไว้ก่อน prereq: `06026120 ... 3(2-2-5) ... วิชาบังคับก่อน : ...`)
"""

from __future__ import annotations

import bisect
import json
import re
import sqlite3
from pathlib import Path

_CODE_RE = re.compile(r"\b(\d{8})\b")
_CREDIT_RE = re.compile(r"\d\s*\(\d-\d-\d+\)")
_PREREQ_KW = re.compile(r"วิชาบังคับก่อน|PREREQUISITE", re.IGNORECASE)
_NONE_MARKERS = ["ไม่มี", "none", "-"]

# "ไม่มี"/"NONE" ที่ตามหลัง keyword ทันที = ประกาศว่าไม่มีวิชาบังคับก่อน
#
# ห้ามปิดท้ายด้วย `\b`: "ไม่มี" ลงท้ายด้วยสระ ี (U+0E35) ซึ่งเป็น combining mark
# ที่ Python ไม่นับเป็น word character จึงไม่เกิด word boundary กับช่องว่างที่ตามมา
# ทำให้ตัวตรวจไม่ทำงานกับคำไทยเลย (ส่วน "None" อังกฤษ match ได้ปกติ) — บั๊กนี้
# ปล่อยให้ zone ไหลไปกลืนรหัสวิชาถัดไป แล้วเก็บรหัสนั้นเป็น prerequisite ผิด ๆ
# ใช้ negative lookahead กันคำอังกฤษที่ยาวกว่าแทน เช่น "nonetheless"
_NONE_AFTER_KW = re.compile(r"\s*:?\s*(?:ไม่มี|none)(?![a-zA-Z])", re.IGNORECASE)

# ความยาวสูงสุดของ prereq zone (ตัวอักษร) — กันไม่ให้ zone กินรหัสของวิชาถัดไป
_ZONE_MAX_CHARS = 200


def _prereq_zones(text: str) -> list[tuple[int, int]]:
    """หาช่วงข้อความที่รหัส 8 หลักภายในเป็น "รหัสที่ถูกอ้างเป็นเงื่อนไข" ไม่ใช่หัวรายวิชา.

    zone เริ่มหลัง keyword และจบที่ credit pattern ตัวแรก (= credit ของวิชาแม่
    ซึ่งอยู่ท้ายบล็อกในเอกสารฉบับใหม่) หรือที่ _ZONE_MAX_CHARS แล้วแต่อะไรมาก่อน
    """
    zones: list[tuple[int, int]] = []
    for kw in _PREREQ_KW.finditer(text):
        start = kw.end()

        # ประกาศ "ไม่มี/NONE" ทันทีหลัง keyword → zone จบตรงนั้น
        # (ถ้าปล่อยให้ zone ยาวต่อ จะไหลไปกลบรหัสของวิชาถัดไป ซึ่งทำให้วิชาถัดไป
        #  ไม่ถูกนับเป็นหัวรายวิชา และรหัสนั้นถูกเก็บเป็น prereq ผิด ๆ
        #  พบในรูปแบบของ AIT/BIT ที่วาง credit ไว้ก่อน keyword จึงไม่มี credit มาหยุด zone)
        none_m = _NONE_AFTER_KW.match(text, start)
        if none_m:
            zones.append((start, none_m.end()))
            continue

        end = min(start + _ZONE_MAX_CHARS, len(text))
        cred = _CREDIT_RE.search(text, start, end)
        if cred:
            end = cred.start()
        if end > start:
            zones.append((start, end))

    # merge ช่วงที่ทับกัน (keyword ไทย/อังกฤษ ของวิชาเดียวกันอยู่ติดกัน)
    merged: list[tuple[int, int]] = []
    for start, end in sorted(zones):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def populate(db_path: Path | str) -> dict[str, int]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    # ── รวม text ทุก chunk ในแต่ละ version เป็นข้อความยาว ──
    # (ข้ามปัญหา chunk boundary ที่ตัดกลางบล็อกคำอธิบายรายวิชา)
    rows = conn.execute("""
        SELECT text, version_id FROM chunk
        WHERE text LIKE '%บังคับก่อน%' OR text LIKE '%PREREQUISITE%'
        ORDER BY version_id, page_number, chunk_id
    """).fetchall()

    # group by version_id — merge text
    version_texts: dict[int, str] = {}
    for row in rows:
        vid = row["version_id"]
        version_texts[vid] = version_texts.get(vid, "") + "\n" + (row["text"] or "")

    updated = 0
    with_prereq = 0
    seen: set[tuple[str, int]] = set()

    for version_id, text in version_texts.items():

        # ── ตัดข้อความเป็นบล็อกตรงรหัส 8 หลักที่มี credit ตามหลัง ──
        code_positions = list(_CODE_RE.finditer(text))
        if not code_positions:
            continue

        # ช่วงที่รหัสภายในเป็นรหัส prerequisite ไม่ใช่หัวรายวิชา
        zones = _prereq_zones(text)
        zone_starts = [s for s, _ in zones]

        def in_prereq_zone(pos: int) -> bool:
            i = bisect.bisect_right(zone_starts, pos) - 1
            return i >= 0 and pos < zones[i][1]

        # หา "course header positions" = รหัสที่มี credit ในระยะ 200 ตัว
        # และไม่อยู่ใน prereq zone
        #
        # รูปแบบที่วาง credit ไว้ท้ายบล็อก (AIT/DSBA ฉบับใหม่) ถ้าวิชามีวิชาบังคับก่อน
        # 2 ตัว ข้อความไทย + อังกฤษของ prereq ยาวจน credit เลยระยะ 200 ตัวไป
        # (เช่น 06046406 พื้นฐานการเรียนรู้เชิงลึก) จึงยอมให้ไกลถึง 500 ตัว
        # เฉพาะเมื่อคำว่า วิชาบังคับก่อน/PREREQUISITE ตามหลังรหัสภายใน 200 ตัว
        header_positions: list[tuple[int, str]] = []  # (start_pos, code)
        for cm in code_positions:
            if in_prereq_zone(cm.start()):
                continue
            lookahead = text[cm.start():cm.start() + 200]
            if _CREDIT_RE.search(lookahead):
                header_positions.append((cm.start(), cm.group(1)))
                continue
            if _PREREQ_KW.search(lookahead) and _CREDIT_RE.search(
                text, cm.start(), cm.start() + 500
            ):
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
    fixed = reconcile_with_page_scan(conn)
    conn.close()
    return {"updated": updated, "with_prereq": with_prereq, "reconciled": fixed}


# ══════════════════════════════════════════════════════════════════════
# ตรวจซ้ำจากข้อความรายหน้า
# ══════════════════════════════════════════════════════════════════════

_KW_TH = re.compile(r"วิชาบังคับก่อน\s*:?")
_ZONE_STOP = re.compile(r"PREREQUISITE|\d\s*\(\d+-\d+-\d+\)|CO-?REQUISITE|วิชาบังคับร่วม", re.IGNORECASE)
_NONE_START = re.compile(r"^\s*(?:ไม่มี|none|-)", re.IGNORECASE)


def scan_page_prerequisites(text: str) -> dict[str, list[str] | None]:
    """อ่านวิชาบังคับก่อนจากข้อความหนึ่งหน้าโดยยึดคำว่า "วิชาบังคับก่อน" (ฝั่งภาษาไทย).

    วิชาแม่ = รหัสตัวสุดท้ายก่อน keyword (ตัดส่วนที่อยู่หลัง PREREQUISITE ของวิชาก่อนหน้าออก
    เพื่อไม่ให้รหัสที่ถูกอ้างของวิชาก่อนกลายเป็นวิชาแม่) ส่วนรหัสที่ถูกอ้าง = รหัสระหว่าง
    keyword กับ PREREQUISITE / หน่วยกิตตัวถัดไป
    คืน {รหัสวิชา: [รหัสที่ต้องผ่าน]} โดย [] = เล่มเขียนว่าไม่มี
    """
    out: dict[str, list[str] | None] = {}
    for m in _KW_TH.finditer(text):
        before = text[max(0, m.start() - 260):m.start()]
        cut = before.rfind("PREREQUISITE")
        if cut >= 0:
            before = before[cut + len("PREREQUISITE"):]
        heads = _CODE_RE.findall(before)
        if not heads:
            continue
        head = heads[-1]
        after = text[m.end():m.end() + 160]
        if _NONE_START.match(after):
            out.setdefault(head, [])
            continue
        stop = _ZONE_STOP.search(after)
        zone = after[:stop.start()] if stop else after
        codes = [c for c in dict.fromkeys(_CODE_RE.findall(zone)) if c != head]
        if codes or head not in out:
            out[head] = codes
    return out


def reconcile_with_page_scan(conn: sqlite3.Connection) -> int:
    """แก้ prerequisite ที่ตัวสกัดแบบบล็อกพลาด โดยเทียบกับการอ่านรายหน้า.

    ตัวสกัดแบบบล็อกรวมข้อความทั้งเล่มแล้วตัดตามหัวรายวิชา บางรูปแบบหน้าทำให้วิชาบังคับก่อน
    ไปผูกกับวิชาข้างเคียง (เช่น DSBA 2560: เล่มเขียน 06026106 "ไม่มี" แต่ตาราง course ได้
    06026104 ซึ่งเป็นของ 06026107) หรือหายไปเลย (DSBA 2565 06026213, IT 2565 06016420)
    แก้เฉพาะกรณีที่ขัดกันชัดเจน: ตารางว่างแต่เล่มมีรหัส / ตารางมีรหัสแต่เล่มเขียนว่าไม่มี
    ถ้าทั้งสองฝั่งมีรหัสแต่ต่างกัน (เช่นเล่มเขียนรหัสไทยกับอังกฤษไม่ตรงกัน) คงค่าเดิมไว้
    """
    fixed = 0
    versions = [r[0] for r in conn.execute("SELECT version_id FROM curriculum_version")]
    for vid in versions:
        scanned: dict[str, list[str]] = {}
        for (text,) in conn.execute(
            "SELECT p.page_text FROM page p JOIN document d ON d.document_id = p.document_id "
            "WHERE d.version_id = ? ORDER BY p.page_number",
            (vid,),
        ):
            for code, pre in scan_page_prerequisites(text or "").items():
                if pre or code not in scanned:
                    scanned[code] = pre
        for code, pre in scanned.items():
            row = conn.execute(
                "SELECT prerequisite_json FROM course WHERE code = ? AND version_id = ?", (code, vid)
            ).fetchone()
            if row is None:
                continue
            have = json.loads(row[0] or "[]")
            if (not have and pre) or (have and not pre):
                conn.execute(
                    "UPDATE course SET prerequisite_json = ?, prerequisite_raw = ? "
                    "WHERE code = ? AND version_id = ?",
                    (json.dumps(pre, ensure_ascii=False),
                     ("วิชาบังคับก่อน : " + " ".join(pre)) if pre else "ไม่มี", code, vid),
                )
                fixed += 1
    conn.commit()
    return fixed


if __name__ == "__main__":
    db = Path(__file__).resolve().parent.parent.parent / "artifacts" / "katrag.sqlite3"
    print(f"Populating prerequisites: {db}")
    result = populate(db)
    print(f"Done! Updated: {result['updated']} courses, "
          f"with prerequisite: {result['with_prereq']}")
