"""Completeness Backfill + Dedup — deterministic post-processing ของคำตอบ LLM.

1. Dedup: ลบรายวิชาที่ซ้ำ (ตรวจจากรหัสวิชา 8 หลัก)
2. Backfill: เติมวิชาที่อยู่ใน evidence แต่ LLM ไม่ได้ระบุ
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class CourseUnit:
    code: str
    name: str
    credits: str
    year_semester: str  # เช่น "ปีที่ 1 ภาคการศึกษาที่ 1" หรือ ""


# regex: รหัสวิชา 8 หลัก
_CODE_RE = re.compile(r"\b(\d{8})\b")
# regex: credits pattern 3(3-0-6)
_CREDITS_RE = re.compile(r"(\d)\s*\((\d)-(\d)-(\d+)\)")
# regex: ปีที่ X ภาคการศึกษาที่ Y
_YEAR_SEM_RE = re.compile(r"ปีที่\s*(\d)\s*ภาคการศึกษาที่\s*(\d)")


def extract_courses_from_text(text: str) -> list[CourseUnit]:
    """สกัดรายวิชาจาก text (evidence chunk) ด้วย regex."""
    courses: list[CourseUnit] = []
    lines = text.split("\n")

    # หา year/sem context
    current_ys = ""
    for line in lines:
        ys_match = _YEAR_SEM_RE.search(line)
        if ys_match:
            current_ys = f"ปีที่ {ys_match.group(1)} ภาคการศึกษาที่ {ys_match.group(2)}"

        code_match = _CODE_RE.search(line)
        if not code_match:
            continue

        code = code_match.group(1)
        # ชื่อวิชา: ข้อความไทยหลังรหัส
        after_code = line[code_match.end():].strip()
        name_match = re.match(r"([ก-๙\s\-/().]+)", after_code)
        name = name_match.group(1).strip() if name_match else ""

        # credits
        credits_match = _CREDITS_RE.search(line)
        credits = credits_match.group(0) if credits_match else ""

        if name and len(name) > 3:
            courses.append(CourseUnit(
                code=code, name=name, credits=credits, year_semester=current_ys
            ))

    return courses


def dedup_answer(answer: str) -> str:
    """ลบรายวิชาที่ซ้ำ (รหัสเดียวกัน) ออกจากคำตอบ."""
    lines = answer.split("\n")
    seen_codes: set[str] = set()
    result_lines: list[str] = []
    skip_until_next = False

    for line in lines:
        codes = _CODE_RE.findall(line)
        if codes:
            code = codes[0]
            if code in seen_codes:
                skip_until_next = True
                continue
            seen_codes.add(code)
            skip_until_next = False
        elif skip_until_next and line.strip().startswith(("-", "*", " ")):
            continue
        else:
            skip_until_next = False

        result_lines.append(line)

    return "\n".join(result_lines)


def backfill_courses(answer: str, evidence_texts: list[str], question: str = "") -> str:
    """เติมวิชาที่อยู่ใน evidence แต่ LLM ไม่ได้ระบุ.

    เติมเฉพาะวิชาที่ชื่อมี keyword จากคำถาม (ถ้ามี) เพื่อไม่เติมวิชาที่ไม่เกี่ยว
    """
    # 1. รหัสที่ LLM ระบุแล้ว
    answer_codes = set(_CODE_RE.findall(answer))

    # 2. สกัดจาก evidence
    all_evidence_courses: dict[str, CourseUnit] = {}
    for text in evidence_texts:
        for course in extract_courses_from_text(text):
            if course.code not in all_evidence_courses:
                all_evidence_courses[course.code] = course

    # 3. หา keywords จากคำถาม (ตัด stopword พื้นฐาน)
    question_keywords: list[str] = []
    if question:
        import re as _re
        # ดึงคำไทย/อังกฤษที่ยาว ≥ 3 ตัว
        tokens = _re.findall(r"[ก-๙]{3,}|[A-Za-z]{3,}", question.lower())
        stopwords = {"มี", "วิชา", "ที่", "เกี่ยวข้อง", "กับ", "การ", "กี่", "ของ", "ใน"}
        question_keywords = [t for t in tokens if t not in stopwords]

    # 4. หาที่ขาด + filter ด้วย keyword (ถ้ามี)
    missing = []
    for code, c in all_evidence_courses.items():
        if code in answer_codes:
            continue
        if question_keywords:
            name_lower = c.name.lower()
            if not any(kw in name_lower for kw in question_keywords):
                continue
        missing.append(c)

    if not missing:
        return answer

    # เติมท้ายคำตอบ
    backfill_lines = ["\n\nวิชาเพิ่มเติมที่พบในหลักฐาน:"]
    for c in missing:
        line = f"- **{c.code} {c.name}**"
        if c.credits:
            line += f" — {c.credits}"
        if c.year_semester:
            line += f" ({c.year_semester})"
        backfill_lines.append(line)

    return answer + "\n".join(backfill_lines)


def postprocess_answer(answer: str, evidence_texts: list[str], question: str = "") -> str:
    """Main entry: dedup only (backfill disabled — LLM interpretation issue, not retrieval)."""
    result = dedup_answer(answer)
    # backfill disabled: LLM ตีความ "เขียนโปรแกรม" กว้างเกิน ทำให้เติมวิชาไม่ตรง
    # จะเปิดเมื่อมี enumeration detection + keyword filtering ที่แม่นกว่า
    # result = backfill_courses(result, evidence_texts, question)
    return result
