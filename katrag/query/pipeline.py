"""Answer pipeline — ขั้นตอนตอบคำถามทั้งหมด แยกออกจากชั้น HTTP.

ทำไมต้องมีไฟล์นี้
-----------------
เดิม logic ตอบคำถามทั้งหมดอยู่ใน `/ask` handler ของ `katrag/api/service.py`
เป็นฟังก์ชันเดียวยาว 417 บรรทัด ที่ทำทุกอย่างปนกัน: เลือกหลักสูตร → structured
intent chain → retrieval → adaptive cutoff → สร้าง prompt → เรียก LLM →
ประกอบ citation → บันทึก trace พร้อม inline import 26 จุดและ try/except ซ้อน
หลายชั้น ทำให้แก้จุดหนึ่งกระทบจุดอื่นโดยไม่รู้ตัว และเขียนเทสต์แยกขั้นไม่ได้

ไฟล์นี้แยกเป็นขั้นตอนที่มีขอบเขตชัดเจน แต่ละขั้นรับ input/คืน output ตรง ๆ
ไม่ผูกกับ FastAPI app state จึงเรียกจากเทสต์หรือ CLI ได้เหมือนกัน
`service.py` เหลือหน้าที่แค่ตรวจ request → เรียก `answer_question()` → คืน response

ลำดับขั้น
---------
1. `resolve_program`    เลือกหลักสูตร (ชื่อในคำถามชนะค่าที่ผู้ใช้เลือก)
2. `scope_question`     ผนวกชื่อหลักสูตรเข้าคำถามให้ขั้นถัดไปเห็นบริบทเดียวกัน
3. `run_structured`     ตอบจากตาราง course/plan_slot (แม่นกว่า chunk)
4. `retrieve_evidence`  hybrid retrieval + ตัดหน้าซ้ำ + adaptive cutoff
5. `build_context`      ประกอบหลักฐานเป็นข้อความสำหรับ LLM
6. `compose_answer`     คืน context ตรง ๆ (คำถามตายตัว) หรือให้ LLM เรียบเรียง
7. `resolve_citations`  ชี้หน้าต้นทางที่ตรงกับคำตอบ
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, Sequence

from katrag.query.completeness import postprocess_answer
from katrag.query.retriever import search as lexical_search
from katrag.query.semantic_retriever import hybrid_search
from katrag.query.structured_query import (
    detect_cross_version_intent,
    detect_plan_summary_intent,
    detect_prerequisite_intent,
    detect_program,
    detect_program_name_intent,
    detect_year,
    source_pages_for_codes,
    try_cross_version_diff,
    try_plan_summary,
    try_prerequisite,
    try_program_name,
    try_structured_answer,
)
from katrag.query.topic_semantic import (
    answer_topic,
    detect_program_code,
    is_topic_question,
)

# ══════════════════════════════════════════════════════════════════════
# ค่าคงที่ที่ปรับพฤติกรรมการตอบ
# ══════════════════════════════════════════════════════════════════════

#: คะแนน hybrid มักมี "cliff" ชัดเจนระหว่างหน้าที่เกี่ยวจริงกับหน้าอื่น
#: (เช่น 0.020 / 0.019 / 0.018 / 0.018 แล้วตกเป็น 0.008) การคืนครบ 10 หน้า
#: ทุกครั้งทำให้ citation precision ตกโดยไม่จำเป็น จึงเก็บเฉพาะหน้าที่
#: คะแนน >= อันดับหนึ่ง × ค่านี้
CUTOFF_RATIO = 0.55

#: ต้องเหลือหลักฐานอย่างน้อยเท่านี้ ไม่ว่า cutoff จะตัดแรงแค่ไหน
MIN_EVIDENCE = 3

#: intent ที่ structured path ตอบได้ครบแล้ว — คืน context ตรง ๆ ไม่ให้ LLM
#: reformat (กันวิชาเลือกตกหล่นและกันคำตอบถูกตัดกลาง)
DIRECT_INTENTS = frozenset({
    "year_sem", "all_courses", "plan_summary", "cross_version",
    "topic_courses", "topic_semantic", "prerequisite",
})

#: คำที่บ่งชี้ว่าเป็นคำถามเชิงวิเคราะห์ (ต้องให้ LLM ให้เหตุผล ไม่ใช่ list ข้อมูล)
REASONING_MARKERS = (
    "ได้ไหม", "ได้มั้ย", "ได้หรือไม่", "ได้รึเปล่า",
    "ควรไหม", "ควรมั้ย", "ดีไหม", "เหมาะไหม",
    "เป็นไปได้ไหม", "เป็นไปได้มั้ย", "ทำได้ไหม",
    "ลงได้ไหม", "เรียนได้ไหม", "สมัครได้ไหม",
    "จำเป็นไหม", "จำเป็นมั้ย", "ต้องไหม",
    "ทำไม", "เพราะอะไร", "เหตุผล",
    "แนะนำ", "ข้อดี", "ข้อเสีย", "เปรียบเทียบ",
)

#: max_tokens คุมเวลา generate ของโมเดล 30B โดยตรง — 3000 token ทำให้คำถาม
#: แผนเรียนใช้เวลา ~85 วินาที ค่าปัจจุบันพอสำหรับแผนทั้งปี
MAX_TOKENS_STRUCTURED = 1500
MAX_TOKENS_GENERAL = 700

NO_RESULT_ANSWER = (
    "ไม่พบข้อมูลที่เกี่ยวข้องกับคำถามนี้ในฐานข้อมูล\n\n"
    "ลองถามให้เจาะจงขึ้น เช่น ระบุชื่อวิชา ชั้นปี หรือภาคการศึกษา"
)


# ══════════════════════════════════════════════════════════════════════
# ชนิดข้อมูลระหว่างขั้น
# ══════════════════════════════════════════════════════════════════════


class LlmClient(Protocol):
    """สัญญาขั้นต่ำของ LLM backend ที่ pipeline ต้องใช้."""

    def generate(self, prompt: str, max_tokens: int = ...) -> str: ...


@dataclass(slots=True)
class EvidenceHit:
    """หลักฐานหนึ่งชิ้นจาก retrieval — รูปแบบเดียวไม่ว่ามาจาก hybrid หรือ lexical."""

    chunk_id: int
    document_id: str
    page_number: int
    heading: str
    text: str
    program: str
    curriculum_year: int
    edition_status: str
    score: float

    @property
    def version_label(self) -> str:
        if self.program and self.curriculum_year:
            return f"{self.program} {self.curriculum_year} ({self.edition_status})"
        return ""


@dataclass(slots=True)
class StructuredOutcome:
    """ผลจาก structured path (ตาราง course/plan_slot)."""

    context: str = ""
    intent: str = ""
    codes: list[str] = field(default_factory=list)
    version_id: int | None = None
    version_label: str = ""

    @property
    def matched(self) -> bool:
        return bool(self.context)


@dataclass(slots=True)
class CitationRef:
    """citation หนึ่งรายการที่ชี้กลับไปหน้าต้นทางได้."""

    citation_id: str
    document_id: str
    page: int
    heading: str
    chunk_text: str = ""


@dataclass(slots=True)
class AnswerResult:
    """ผลลัพธ์สุดท้ายที่ชั้น HTTP นำไปประกอบ response."""

    answer: str
    citations: list[CitationRef] = field(default_factory=list)
    versions_resolved: list[str] = field(default_factory=list)
    program: str = ""
    program_source: str = ""


# ══════════════════════════════════════════════════════════════════════
# ขั้นที่ 1-2: เลือกหลักสูตรและผนวกบริบท
# ══════════════════════════════════════════════════════════════════════


def resolve_program(question: str, selected: str) -> tuple[str, str]:
    """คืน (program, ที่มา) — ชื่อหลักสูตรในคำถามชนะค่าที่ผู้ใช้เลือก.

    กฎมีสองชั้นเท่านั้น ไม่มีการเดา:
      1. คำถามระบุชื่อหลักสูตรมาเอง → ใช้อันนั้น (รองรับคำถามข้ามหลักสูตร
         เช่น "เปรียบเทียบ DSBA กับ IT" โดยไม่ต้องเปลี่ยน dropdown)
      2. ไม่ระบุ → ใช้ค่าที่ผู้ใช้เลือก (schema บังคับว่าต้องมี)

    เดิมมีชั้นที่ 3-4 (เดาจาก prefix รหัสวิชา / ชื่อวิชาที่มีในหลักสูตรเดียว
    แล้ว fallback เป็นคณะ IT) ซึ่งเดาผิดเงียบ ๆ ได้ — ตัดออกเพราะตอนนี้
    ผู้ใช้ต้องเลือกหลักสูตรก่อนถามอยู่แล้ว
    """
    explicit = detect_program(question)
    if explicit:
        return explicit, "question"
    return selected.strip().upper(), "selected"


def scope_question(question: str, program: str) -> str:
    """ผนวกชื่อหลักสูตรเข้าคำถาม ถ้ายังไม่มี — ให้ทุกขั้นถัดไปเห็นบริบทเดียวกัน."""
    if program and program not in question.upper():
        return f"หลักสูตร {program}: {question}"
    return question


# ══════════════════════════════════════════════════════════════════════
# ขั้นที่ 3: structured path
# ══════════════════════════════════════════════════════════════════════


def run_structured(
    conn: sqlite3.Connection,
    question: str,
    *,
    course_index: object | None = None,
    llm: LlmClient | None = None,
) -> StructuredOutcome:
    """ตอบจากตาราง course/plan_slot — แม่นกว่าการค้น chunk.

    ลำดับ intent เรียงจากเฉพาะเจาะจงที่สุดไปกว้างที่สุด ตัวแรกที่ตรงชนะ
    """
    sr = _dispatch_intent(conn, question)
    outcome = StructuredOutcome()
    if sr.matched:
        outcome = StructuredOutcome(
            context=sr.context,
            intent=sr.intent,
            codes=list(sr.codes),
            version_id=sr.version_id,
            version_label=sr.version_label,
        )

    # คำถามหัวข้อวิชาที่ไม่ระบุชั้นปี → recall เชิงความหมาย (bge-m3)
    # แล้วจัดรูปคำตอบเอง เพื่อให้ครบชื่ออังกฤษ/หน่วยกิต/ชั้นปี ไม่ตกหล่น
    if not outcome.matched and detect_year(question) is None and course_index is not None:
        prog_code = detect_program_code(question)
        if is_topic_question(question, prog_code):
            tr = answer_topic(conn, course_index, question, prog_code, llm=llm)
            if tr.matched:
                outcome = StructuredOutcome(
                    context=tr.context,
                    intent=tr.intent,
                    codes=[h.code for h in tr.candidates],
                    version_id=tr.candidates[0].version_id if tr.candidates else None,
                    version_label=tr.version_label,
                )
    return outcome


def _dispatch_intent(conn: sqlite3.Connection, question: str):
    """เลือก structured handler ตาม intent ของคำถาม."""
    if detect_program_name_intent(question):
        sr = try_program_name(conn, question)
        return sr if sr.matched else try_structured_answer(conn, question)
    if detect_prerequisite_intent(question):
        sr = try_prerequisite(conn, question)
        return sr if sr.matched else try_structured_answer(conn, question)
    if detect_cross_version_intent(question):
        return try_cross_version_diff(conn, question)
    if detect_plan_summary_intent(question):
        return try_plan_summary(conn, question)
    return try_structured_answer(conn, question)


# ══════════════════════════════════════════════════════════════════════
# ขั้นที่ 4: retrieval
# ══════════════════════════════════════════════════════════════════════


def retrieve_evidence(
    conn: sqlite3.Connection,
    question: str,
    *,
    dense_index: object | None = None,
    limit: int = 10,
) -> list[EvidenceHit]:
    """ค้นหลักฐานจาก chunk แล้วกรองให้เหลือเฉพาะที่เกี่ยวจริง."""
    hits = _raw_search(conn, question, dense_index=dense_index, limit=limit)
    hits = _dedupe_by_page(hits)
    return _apply_cutoff(hits)


def _raw_search(
    conn: sqlite3.Connection,
    question: str,
    *,
    dense_index: object | None,
    limit: int,
) -> list[EvidenceHit]:
    """hybrid ถ้ามี dense index ไม่งั้น lexical — คืนรูปแบบเดียวกัน."""
    if dense_index is not None:
        raw = hybrid_search(conn, dense_index, question, limit=limit)
        return [
            EvidenceHit(
                chunk_id=h.chunk_id,
                document_id=h.document_id,
                page_number=h.page_number,
                heading=h.heading,
                text=h.text,
                program=h.program,
                curriculum_year=h.curriculum_year,
                edition_status=h.edition_status,
                score=h.fused_score,
            )
            for h in raw
        ]

    raw_lex = lexical_search(conn, question, limit=min(limit, 8))
    return [
        EvidenceHit(
            chunk_id=h.chunk_id,
            document_id=h.document_id,
            page_number=h.page_number,
            heading=h.heading,
            text=h.text,
            program=h.program,
            curriculum_year=h.curriculum_year,
            edition_status=h.edition_status,
            score=h.score,
        )
        for h in raw_lex
    ]


def _dedupe_by_page(hits: Sequence[EvidenceHit]) -> list[EvidenceHit]:
    """เก็บ chunk แรกของแต่ละหน้า — หลาย chunk ในหน้าเดียวกันกิน citation slot ซ้ำ
    โดยไม่เพิ่มข้อมูลใหม่ และทำให้ precision ตก."""
    seen: set[tuple[str, int]] = set()
    out: list[EvidenceHit] = []
    for h in hits:
        key = (h.document_id, h.page_number)
        if key in seen:
            continue
        seen.add(key)
        out.append(h)
    return out


def _apply_cutoff(hits: list[EvidenceHit]) -> list[EvidenceHit]:
    """ตัดหลักฐานที่คะแนนต่ำกว่าอันดับหนึ่งมาก แต่ต้องเหลือพอให้ LLM เรียบเรียง."""
    if not hits:
        return hits
    top = max(h.score or 0.0 for h in hits)
    if top <= 0:
        return hits
    kept = [h for h in hits if (h.score or 0.0) >= top * CUTOFF_RATIO]
    return kept if len(kept) >= MIN_EVIDENCE else hits[:MIN_EVIDENCE]


# ══════════════════════════════════════════════════════════════════════
# ขั้นที่ 5-6: ประกอบ context และสร้างคำตอบ
# ══════════════════════════════════════════════════════════════════════


def build_context(structured: StructuredOutcome, hits: Sequence[EvidenceHit]) -> str:
    """ประกอบหลักฐานเป็นข้อความเดียวสำหรับ LLM — structured มาก่อนเพราะเชื่อถือได้กว่า."""
    parts: list[str] = []
    if structured.matched:
        parts.append(
            "[ข้อมูลจากฐานข้อมูลหลักสูตร — เชื่อถือได้ ครบถ้วน]:\n" + structured.context
        )
    for i, hit in enumerate(hits, 1):
        heading = hit.heading or "ไม่มีหัวข้อ"
        ver = f"{hit.program} {hit.curriculum_year}".strip()
        parts.append(
            f"[{i}] ({heading} — {ver}, หน้า {hit.page_number}):\n{hit.text[:500].strip()}"
        )
    return "\n\n".join(parts)


def is_reasoning_question(question: str) -> bool:
    """คำถามเชิงวิเคราะห์ (ได้ไหม/ทำไม/แนะนำ) ต้องให้ LLM ให้เหตุผล ไม่ใช่ list ข้อมูล."""
    return any(m in question for m in REASONING_MARKERS)


def _reasoning_prompt(context: str, question: str) -> str:
    return (
        "คุณเป็นที่ปรึกษาหลักสูตรของ KMITL คณะเทคโนโลยีสารสนเทศ "
        "ตอบเป็นภาษาไทย กระชับ ให้เหตุผลประกอบคำตอบ\n\n"
        "แนวทางการตอบ:\n"
        "- ตอบว่า 'ได้' หรือ 'ไม่ได้' ก่อน แล้วอธิบายเหตุผล\n"
        "- วิเคราะห์จากข้อมูล: วิชาบังคับก่อน (prerequisite), ภาคการศึกษาที่เปิดสอน, "
        "แขนง/กลุ่มวิชาเลือก\n"
        "- ถ้าคำตอบแตกต่างตามแขนง/เงื่อนไข ให้แยกบอกแต่ละกรณี\n"
        "- อ้างอิงข้อมูลจากหลักฐาน [n]\n"
        "- ถ้าข้อมูลไม่พอสรุป ให้บอกตามตรงว่า 'ไม่สามารถยืนยันได้จากข้อมูลที่มี'\n\n"
        f"== หลักฐาน ==\n{context}\n\n"
        f"== คำถาม ==\n{question}\n\n"
        "== คำตอบ (ตอบว่าได้/ไม่ได้ก่อน แล้วอธิบาย) ==\n"
    )


def _factual_prompt(context: str, question: str) -> str:
    return (
        "คุณเป็นผู้ช่วยตอบคำถามเกี่ยวกับหลักสูตรของ KMITL "
        "ใช้เฉพาะข้อมูลจากหลักฐานด้านล่างในการตอบ ตอบเป็นภาษาไทย ตรงประเด็นกับคำถาม\n"
        "แนวทางการตอบ:\n"
        "- ตอบเฉพาะสิ่งที่ถาม อย่าเพิ่มหมายเหตุหรือรายการที่ไม่ได้ถาม\n"
        "- เมื่อระบุรายวิชา ให้ใส่ทั้งชื่อภาษาไทยและชื่อภาษาอังกฤษ (ในวงเล็บ) "
        "จำนวนหน่วยกิต และชั้นปี/ภาคที่เรียนถ้ามีในหลักฐาน\n"
        "- ถ้าคำถามให้แจกแจงรายวิชา ให้ระบุครบทุกวิชาที่พบในหลักฐาน ไม่ซ้ำ\n"
        "- ระบุหมายเลขหลักฐาน [n] ที่ใช้อ้างอิง\n"
        "- ถ้าหลักฐานไม่มีข้อมูลเพียงพอ ให้บอกตามตรงว่าไม่พบข้อมูล\n\n"
        f"== หลักฐาน ==\n{context}\n\n"
        f"== คำถาม ==\n{question}\n\n"
        "== คำตอบ ==\n"
    )


def compose_answer(
    conn: sqlite3.Connection,
    question: str,
    structured: StructuredOutcome,
    hits: Sequence[EvidenceHit],
    context: str,
    *,
    llm: LlmClient | None,
) -> str:
    """คืนคำตอบสุดท้าย — ตรงจากตารางถ้าตอบครบแล้ว ไม่งั้นให้ LLM เรียบเรียง."""
    reasoning = is_reasoning_question(question)

    # คำถามตายตัวที่ structured ตอบครบแล้ว → คืนตรง ๆ กันวิชาตกหล่น
    if structured.matched and structured.intent in DIRECT_INTENTS and not reasoning:
        return structured.context

    if llm is None:
        return _fallback_answer(context, "ไม่ได้ตั้งค่า LLM backend")

    prompt_context = context
    if reasoning and structured.matched and "prerequisite" not in structured.intent:
        prompt_context = _augment_with_prerequisite(conn, question, context)

    prompt = (
        _reasoning_prompt(prompt_context, question)
        if reasoning
        else _factual_prompt(prompt_context, question)
    )
    max_tokens = MAX_TOKENS_STRUCTURED if structured.matched else MAX_TOKENS_GENERAL

    try:
        answer = llm.generate(prompt, max_tokens=max_tokens)
    except Exception as exc:
        return _fallback_answer(context, f"{type(exc).__name__}: {exc}")

    return postprocess_answer(answer, [h.text for h in hits], question)


def _augment_with_prerequisite(
    conn: sqlite3.Connection, question: str, context: str
) -> str:
    """เสริมข้อมูลวิชาบังคับก่อนให้คำถามเชิงวิเคราะห์.

    คำถามอย่าง "ลงวิชา X ตอนปีสองได้ไหม" ไม่มีคำว่า "ต้องผ่าน" จึงไม่เข้า
    prerequisite intent แต่ยังต้องรู้ prerequisite เพื่อตอบให้ถูก
    """
    try:
        pr = try_prerequisite(conn, question, require_intent=False)
    except Exception:
        return context
    if pr.matched:
        return f"{context}\n\n[ข้อมูลวิชาบังคับก่อน]:\n{pr.context}"
    return context


def _fallback_answer(context: str, reason: str) -> str:
    """LLM ใช้ไม่ได้ → คืนหลักฐานดิบ ดีกว่าไม่ตอบอะไรเลย."""
    return (
        f"(ระบบสรุปคำตอบด้วย LLM ไม่พร้อมใช้งาน: {reason})\n\n"
        f"ข้อมูลที่เกี่ยวข้องที่สุดจากฐานข้อมูล:\n\n{context}"
    )


# ══════════════════════════════════════════════════════════════════════
# ขั้นที่ 7: citation
# ══════════════════════════════════════════════════════════════════════


def citations_from_hits(hits: Sequence[EvidenceHit]) -> list[CitationRef]:
    """citation จาก chunk ที่ retrieval ดึงมา."""
    return [
        CitationRef(
            citation_id=f"cite-{i:03d}",
            document_id=hit.document_id,
            page=hit.page_number,
            heading=hit.heading or "ไม่มีหัวข้อ",
            chunk_text=hit.text[:1000],
        )
        for i, hit in enumerate(hits, 1)
    ]


def citations_from_structured(
    conn: sqlite3.Connection, structured: StructuredOutcome, *, limit: int = 8
) -> list[CitationRef]:
    """citation ที่ชี้หน้าต้นทางของรายวิชาที่ใช้ตอบ.

    เมื่อคำตอบมาจาก structured path หน้าที่ retrieval ดึงมาอาจไม่ใช่หน้าที่
    ให้คำตอบ จึงต้องหาหน้าที่รหัสวิชาในคำตอบปรากฏจริงแทน
    """
    if not structured.codes or structured.version_id is None:
        return []
    try:
        pages = source_pages_for_codes(
            conn, structured.codes, structured.version_id, limit=limit
        )
    except Exception:
        return []
    return [
        CitationRef(
            citation_id=f"cite-{i:03d}",
            document_id=doc_id,
            page=page_no,
            heading=heading or "ตารางรายวิชา/แผนการศึกษา",
        )
        for i, (doc_id, page_no, heading) in enumerate(pages, 1)
    ]


def resolve_citations(
    conn: sqlite3.Connection,
    structured: StructuredOutcome,
    hits: Sequence[EvidenceHit],
) -> list[CitationRef]:
    """เลือกชุด citation ที่ตรงกับแหล่งของคำตอบมากที่สุด."""
    from_structured = citations_from_structured(conn, structured)
    return from_structured if from_structured else citations_from_hits(hits)


# ══════════════════════════════════════════════════════════════════════
# Orchestrator
# ══════════════════════════════════════════════════════════════════════


def answer_question(
    db_path: Path | str,
    question: str,
    selected_program: str,
    *,
    dense_index: object | None = None,
    course_index: object | None = None,
    llm: LlmClient | None = None,
) -> AnswerResult:
    """ตอบคำถามหนึ่งข้อจากต้นจนจบ.

    Args:
        db_path: ไฟล์ฐานข้อมูล provenance store
        question: คำถามดิบจากผู้ใช้
        selected_program: หลักสูตรที่ผู้ใช้เลือก (บังคับ — schema ตรวจแล้ว)
        dense_index: dense index ที่โหลดไว้ (None → ใช้ lexical เท่านั้น)
        course_index: course semantic index (None → ข้าม topic search)
        llm: LLM backend (None → คืนหลักฐานดิบ)
    """
    program, program_source = resolve_program(question, selected_program)
    scoped = scope_question(question, program)

    conn = sqlite3.connect(str(db_path))
    try:
        structured = run_structured(
            conn, scoped, course_index=course_index, llm=llm
        )
        hits = retrieve_evidence(conn, scoped, dense_index=dense_index)

        if not structured.matched and not hits:
            return AnswerResult(
                answer=NO_RESULT_ANSWER,
                program=program,
                program_source=program_source,
            )

        context = build_context(structured, hits)
        answer = compose_answer(
            conn, scoped, structured, hits, context, llm=llm
        )
        citations = resolve_citations(conn, structured, hits)
    finally:
        conn.close()

    return AnswerResult(
        answer=answer,
        citations=citations,
        versions_resolved=_collect_versions(structured, hits),
        program=program,
        program_source=program_source,
    )


def _collect_versions(
    structured: StructuredOutcome, hits: Sequence[EvidenceHit]
) -> list[str]:
    """รวมชื่อเวอร์ชันหลักสูตรที่ใช้ตอบ คงลำดับ ไม่ซ้ำ."""
    labels: list[str] = []
    if structured.version_label:
        labels.append(structured.version_label)
    for hit in hits:
        label = hit.version_label
        if label and label not in labels:
            labels.append(label)
    return labels
