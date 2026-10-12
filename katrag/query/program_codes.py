"""หารหัสหลักสูตร (AITBA/DSBA/AIT/BIT/IT) ที่ผู้ใช้พิมพ์ในคำถาม.

IT และ BIT ต้องพิมพ์ตัวใหญ่ เพราะ "it" / "bit" เป็นคำภาษาอังกฤษปกติ
("which year is it in the plan?" เดิมถูกตีเป็นหลักสูตร IT แทนหลักสูตรที่เลือก)
รหัสอื่นไม่ซ้ำกับคำอังกฤษ จึงรับทั้งตัวเล็กและตัวใหญ่
"""

from __future__ import annotations

import re

PROGRAM_CODES = ["AITBA", "DSBA", "AIT", "BIT", "IT"]
_CASE_SENSITIVE = {"IT", "BIT"}
_CODE_RE = {
    code: re.compile(rf"(?<![A-Za-z]){code}(?![A-Za-z])", 0 if code in _CASE_SENSITIVE else re.IGNORECASE)
    for code in PROGRAM_CODES
}


def program_code_positions(text: str) -> list[tuple[int, str]]:
    """(ตำแหน่ง, รหัส) ทุกจุดที่พบ เรียงตามตำแหน่ง."""
    return sorted((m.start(), code) for code, rx in _CODE_RE.items() for m in rx.finditer(text))


def has_program_code(text: str, code: str) -> bool:
    rx = _CODE_RE.get(code.upper())
    return bool(rx and rx.search(text))


def first_program_code(text: str) -> str | None:
    """รหัสแรกตามลำดับ PROGRAM_CODES (รหัสยาวก่อน) — แบบเดียวกับตัวตรวจเดิม."""
    for code in PROGRAM_CODES:
        if _CODE_RE[code].search(text):
            return code
    return None
