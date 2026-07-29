"""Credits field parser and printer (R8.2, R8.3, R8.4).

รูปแบบหน่วยกิต: ``total(lecture-lab-self_study)``
ตัวอย่าง: ``3(3-0-6)``, ``6(3-3-12)``

หลักการ:
- Parser เป็น deterministic — input เดียวกันได้ผลเดียวกันเสมอ
- round-trip: parse(print(c)) == c  และ  print(parse(s)) == s
- เมื่อ parse ไม่ผ่าน คืน ParseFailure พร้อม error_index (0-based)
  โดยไม่บันทึกค่าตัวเลขบางส่วน
"""

from __future__ import annotations

import re

from katrag.common.types import Credits
from katrag.errors import ParseFailure

# ── constants ─────────────────────────────────────────────────────────

_MIN_VALUE: int = 0
_MAX_VALUE: int = 30

# Strict regex: one or more digits, then '(', digits, '-', digits, '-', digits, ')'
# No extra whitespace or characters allowed.
_CREDITS_PATTERN: re.Pattern[str] = re.compile(
    r"^(\d+)\((\d+)-(\d+)-(\d+)\)$"
)


# ── Credits_Parser ────────────────────────────────────────────────────


def parse_credits(raw: str) -> Credits | ParseFailure:
    """Parse สตริงหน่วยกิตเป็นโครงสร้าง Credits.

    Parameters
    ----------
    raw : str
        สตริงรูปแบบ ``total(lecture-lab-self_study)``
        เช่น ``"3(3-0-6)"``

    Returns
    -------
    Credits
        เมื่อ parse สำเร็จ
    ParseFailure
        เมื่อ parse ไม่สำเร็จ พร้อม error_index ที่ระบุ
        ตำแหน่งอักขระแรกที่ไม่ตรงรูปแบบ (นับจาก 0)
    """
    if not raw:
        return ParseFailure(raw_text=raw, error_index=0, reason="empty string")

    # Attempt match against expected pattern
    match = _CREDITS_PATTERN.match(raw)
    if match is None:
        error_index = _find_error_index(raw)
        return ParseFailure(
            raw_text=raw,
            error_index=error_index,
            reason="format mismatch",
        )

    # Extract integer values
    total_str, lecture_str, lab_str, self_study_str = (
        match.group(1),
        match.group(2),
        match.group(3),
        match.group(4),
    )

    # Validate range 0-30 for each value
    values: list[tuple[str, str, int]] = []
    for name, raw_val in (
        ("total", total_str),
        ("lecture", lecture_str),
        ("lab", lab_str),
        ("self_study", self_study_str),
    ):
        val = int(raw_val)
        if val < _MIN_VALUE or val > _MAX_VALUE:
            # Find the position of this value in the raw string
            idx = _find_value_start(raw, name, raw_val)
            return ParseFailure(
                raw_text=raw,
                error_index=idx,
                reason=f"{name} value {val} out of range 0-30",
            )
        values.append((name, raw_val, val))

    total = values[0][2]
    lecture = values[1][2]
    lab = values[2][2]
    self_study = values[3][2]

    return Credits(total=total, lecture=lecture, lab=lab, self_study=self_study)


# ── Credits_Printer ───────────────────────────────────────────────────


def print_credits(credits: Credits) -> str:
    """แปลงโครงสร้าง Credits กลับเป็นสตริงรูปแบบ ``total(lecture-lab-self_study)``.

    round-trip property: parse_credits(print_credits(c)) == c สำหรับทุก c ที่ valid
    """
    return (
        f"{credits.total}"
        f"({credits.lecture}-{credits.lab}-{credits.self_study})"
    )


# ── internal helpers ──────────────────────────────────────────────────


def _find_error_index(raw: str) -> int:
    """หา index ของอักขระแรกที่ทำให้ไม่ตรงรูปแบบ.

    ลำดับที่คาด: digits '(' digits '-' digits '-' digits ')'
    สแกนจากซ้ายไปขวาจนเจอตำแหน่งที่ผิด
    """
    pos = 0
    length = len(raw)

    # Phase 1: expect one or more digits (total)
    if pos >= length or not raw[pos].isdigit():
        return pos
    while pos < length and raw[pos].isdigit():
        pos += 1

    # Phase 2: expect '('
    if pos >= length or raw[pos] != "(":
        return pos
    pos += 1

    # Phase 3: expect one or more digits (lecture)
    if pos >= length or not raw[pos].isdigit():
        return pos
    while pos < length and raw[pos].isdigit():
        pos += 1

    # Phase 4: expect '-'
    if pos >= length or raw[pos] != "-":
        return pos
    pos += 1

    # Phase 5: expect one or more digits (lab)
    if pos >= length or not raw[pos].isdigit():
        return pos
    while pos < length and raw[pos].isdigit():
        pos += 1

    # Phase 6: expect '-'
    if pos >= length or raw[pos] != "-":
        return pos
    pos += 1

    # Phase 7: expect one or more digits (self_study)
    if pos >= length or not raw[pos].isdigit():
        return pos
    while pos < length and raw[pos].isdigit():
        pos += 1

    # Phase 8: expect ')'
    if pos >= length or raw[pos] != ")":
        return pos
    pos += 1

    # Phase 9: expect end of string
    if pos < length:
        return pos

    # If we reach here, the string should have matched the regex.
    # This path should be unreachable but handle gracefully.
    return 0  # pragma: no cover


def _find_value_start(raw: str, name: str, raw_val: str) -> int:
    """หาตำแหน่งเริ่มต้นของค่าที่อยู่นอกช่วงในสตริงต้นฉบับ."""
    if name == "total":
        return 0
    if name == "lecture":
        # Right after the '('
        open_paren = raw.index("(")
        return open_paren + 1
    if name == "lab":
        # After 'total(' + lecture digits + '-'
        open_paren = raw.index("(")
        pos = open_paren + 1
        while pos < len(raw) and raw[pos].isdigit():
            pos += 1
        # skip the '-'
        return pos + 1
    # self_study: after second '-'
    open_paren = raw.index("(")
    pos = open_paren + 1
    # skip lecture
    while pos < len(raw) and raw[pos].isdigit():
        pos += 1
    # skip first '-'
    pos += 1
    # skip lab
    while pos < len(raw) and raw[pos].isdigit():
        pos += 1
    # skip second '-'
    return pos + 1
