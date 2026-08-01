"""Query Parser — ใช้ LLM แปลคำถามธรรมชาติเป็น structured intent + parameters.

ทำให้ระบบ flexible ต่อการเปลี่ยนคำ (ไม่พึ่ง regex จับคำแบบตายตัว)
LLM สกัด JSON → deterministic DB query ให้ผลแม่นและครบ

Fallback เป็น regex heuristics ถ้า LLM ล้มเหลว/JSON ไม่ถูก
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field


VALID_INTENTS = {
    "course_by_year",   # รายวิชาตามชั้นปี/ภาค เช่น "ปี 1 เรียนอะไร"
    "topic_courses",    # วิชาเกี่ยวกับหัวข้อ เช่น "วิชาเขียนโปรแกรม/AI/ฐานข้อมูล"
    "prerequisite",     # วิชาบังคับก่อน เช่น "X ต้องผ่านวิชาใด"
    "cross_version",    # เทียบหลักสูตรเก่า-ใหม่
    "plan_summary",     # แผนการเรียนทั้งหมด/จบเร็ว
    "all_courses",      # รายวิชาทั้งหมดของหลักสูตร
    "general",          # อื่น ๆ → hybrid retrieval
}

PROGRAMS = ["AITBA", "DSBA", "AIT", "BIT", "IT"]

_THAI_NUM = {"หนึ่ง": 1, "สอง": 2, "สาม": 3, "สี่": 4}


@dataclass
class ParsedQuery:
    intent: str = "general"
    program: str | None = None
    year: int | None = None
    semester: int | None = None
    topic: str | None = None
    source: str = "regex"  # "llm" | "regex"


_PARSE_PROMPT = """คุณเป็นตัวแยกวิเคราะห์คำถามเกี่ยวกับหลักสูตรมหาวิทยาลัย ตอบเป็น JSON เท่านั้น ห้ามมีข้อความอื่น

แยกคำถามเป็น:
- intent: หนึ่งใน ["course_by_year","topic_courses","prerequisite","cross_version","plan_summary","all_courses","general"]
- program: รหัสหลักสูตร ["DSBA","IT","AIT","BIT","AITBA"] หรือ null
- year: ชั้นปี 1-4 หรือ null
- semester: ภาคเรียน 1-3 หรือ null
- topic: หัวข้อวิชาที่ถาม (คำสั้น ๆ ภาษาไทย เช่น "โปรแกรม","ฐานข้อมูล","ปัญญาประดิษฐ์") หรือ null

นิยาม intent:
- course_by_year: ถามว่าปี/เทอมใดเรียนวิชาอะไร
- topic_courses: ถามวิชาที่เกี่ยวกับหัวข้อหนึ่ง (เช่น เขียนโปรแกรม, AI, เครือข่าย) — ต้องมี topic
- prerequisite: ถามวิชาบังคับก่อน/ต้องผ่านวิชาใดก่อน
- cross_version: เทียบหลักสูตรเก่ากับใหม่
- plan_summary: ถามแผนการเรียนทั้งหลักสูตร หรือ จบเร็ว/3.5 ปี
- all_courses: ถามรายวิชาทั้งหมดของหลักสูตร
- general: อื่น ๆ

ตัวอย่าง:
คำถาม: "DSBA ปีหนึ่งเรียนอะไร" → {"intent":"course_by_year","program":"DSBA","year":1,"semester":null,"topic":null}
คำถาม: "มีวิชาเขียนโปรแกรมกี่หน่วยกิต" → {"intent":"topic_courses","program":null,"year":null,"semester":null,"topic":"โปรแกรม"}
คำถาม: "data warehouse ต้องผ่านวิชาใดก่อน" → {"intent":"prerequisite","program":null,"year":null,"semester":null,"topic":"คลังข้อมูล"}
คำถาม: "IT วิชาเอไอมีอะไรบ้าง" → {"intent":"topic_courses","program":"IT","year":null,"semester":null,"topic":"ปัญญาประดิษฐ์"}

คำถาม: "%s"
JSON:"""


def _regex_parse(question: str) -> ParsedQuery:
    """Fallback: heuristic parse."""
    p = ParsedQuery(source="regex")
    up = question.upper()
    for code in PROGRAMS:
        if re.search(rf"(?<![A-Z]){re.escape(code)}(?![A-Z])", up):
            p.program = code
            break
    # year
    for w, n in _THAI_NUM.items():
        if f"ปี{w}" in question or f"ปีที่{w}" in question:
            p.year = n
    m = re.search(r"ปี(?:ที่)?\s*([1-4])", question)
    if m:
        p.year = int(m.group(1))
    # semester
    if "ภาคต้น" in question or "เทอมต้น" in question:
        p.semester = 1
    elif "ภาคปลาย" in question or "เทอมปลาย" in question:
        p.semester = 2
    # intent heuristics
    ql = question.lower()
    if any(w in ql for w in ["ต้องผ่าน", "บังคับก่อน", "prerequisite", "เรียนก่อน"]):
        p.intent = "prerequisite"
    elif any(w in ql for w in ["เก่า", "ใหม่", "เปรียบเทียบ", "ต่างกัน"]):
        p.intent = "cross_version"
    elif any(w in ql for w in ["แผนการเรียน", "แผนการศึกษา", "3.5", "จบเร็ว"]):
        p.intent = "plan_summary"
    elif p.year is not None:
        p.intent = "course_by_year"
    elif any(w in question for w in ["ทั้งหมด", "ทุกวิชา"]):
        p.intent = "all_courses"
    elif "วิชา" in question:
        p.intent = "topic_courses"
    return p


def parse_query(question: str, llm=None) -> ParsedQuery:
    """แปลคำถาม → ParsedQuery. ใช้ LLM ถ้ามี, ไม่งั้น regex."""
    if llm is None:
        return _regex_parse(question)

    try:
        raw = llm.generate(_PARSE_PROMPT % question.replace('"', "'"), max_tokens=200)
        # ดึง JSON block
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return _regex_parse(question)
        data = json.loads(m.group(0))
        intent = data.get("intent", "general")
        if intent not in VALID_INTENTS:
            intent = "general"
        program = data.get("program")
        if program and program.upper() in PROGRAMS:
            program = program.upper()
        else:
            program = None
        year = data.get("year")
        year = int(year) if isinstance(year, (int, float)) and 1 <= year <= 4 else None
        sem = data.get("semester")
        sem = int(sem) if isinstance(sem, (int, float)) and 1 <= sem <= 3 else None
        topic = data.get("topic")
        topic = topic.strip() if isinstance(topic, str) and topic.strip() and topic.lower() != "null" else None

        p = ParsedQuery(intent=intent, program=program, year=year, semester=sem, topic=topic, source="llm")
        # เติมช่องว่างด้วย regex (LLM อาจพลาด program/year)
        rp = _regex_parse(question)
        if p.program is None:
            p.program = rp.program
        if p.year is None:
            p.year = rp.year
        if p.semester is None:
            p.semester = rp.semester
        return p
    except Exception:
        return _regex_parse(question)
