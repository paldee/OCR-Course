"""Answer_Generator — สร้างคำตอบจาก evidence units ผ่าน local LLM (R17.1, R17.2, R17.9, R10.7, R15.5).

Design decisions:
- LLM interface เป็น protocol (pluggable) → stub ได้ในเทสต์
- เรียก Qwen3 4B GGUF Q4 ผ่าน llama.cpp locally เท่านั้น (R17.1)
- ข้อมูลไม่ส่งออกนอกเครื่อง (R17.1)
- Prompt ประกอบจาก evidence units ที่มี citation ID เท่านั้น (max 60 units, version-stamped) (R17.2)
- แยกคำตอบตาม version สำหรับ L4 cross-version comparison (R10.7)
- Numeric values มาจาก Curriculum_Reasoner เท่านั้น — LLM ห้ามคำนวณ (R15.5)
- Cancel เมื่อเกิน time budget หรือ model fail → ไม่คืน partial answer (R17.9)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from katrag.common.types import CitationId, CurriculumVersion
from katrag.errors import AnswerGenerationError
from katrag.query.citation import CitationRegistry, EvidenceUnit


# ══════════════════════════════════════════════════════════════════════
# LLM Protocol — pluggable interface for local model
# ══════════════════════════════════════════════════════════════════════


@runtime_checkable
class LLMProtocol(Protocol):
    """Protocol สำหรับ local LLM backend (R17.1).

    ผู้ implement ต้องเรียก model บน localhost เท่านั้น.
    """

    def generate(self, prompt: str, max_tokens: int = 2048) -> str:
        """ส่ง prompt ไปยัง local LLM และคืนคำตอบ.

        Raises:
            AnswerGenerationError: เมื่อ model fail หรือ timeout
        """
        ...


# ══════════════════════════════════════════════════════════════════════
# Stub LLM สำหรับ unit tests
# ══════════════════════════════════════════════════════════════════════


class StubLLM:
    """Stub LLM ที่คืนคำตอบที่กำหนดไว้ล่วงหน้า — สำหรับ testing."""

    def __init__(
        self,
        response: str = "This is a stub answer.",
        *,
        fail: bool = False,
        delay_seconds: float = 0.0,
    ) -> None:
        self._response = response
        self._fail = fail
        self._delay = delay_seconds
        self.call_count: int = 0
        self.last_prompt: str = ""

    def generate(self, prompt: str, max_tokens: int = 2048) -> str:
        """คืนคำตอบที่กำหนดไว้ หรือ raise error ถ้า fail=True."""
        self.call_count += 1
        self.last_prompt = prompt
        if self._delay > 0:
            time.sleep(self._delay)
        if self._fail:
            raise AnswerGenerationError("Stub LLM forced failure")
        return self._response


# ══════════════════════════════════════════════════════════════════════
# Data types
# ══════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class EvidenceWithCitation:
    """Evidence unit ที่ได้รับ citation ID แล้ว — ใช้สร้าง prompt."""

    unit: EvidenceUnit
    citation_id: CitationId
    version: CurriculumVersion


@dataclass(frozen=True, slots=True)
class ReasonerValue:
    """ค่าเชิงตัวเลขจาก Curriculum_Reasoner — LLM ต้องใช้ค่านี้ ห้ามคำนวณเอง (R15.5)."""

    label: str
    value: float | int | str
    citation_id: str


@dataclass(frozen=True, slots=True)
class GenerationResult:
    """ผลลัพธ์จาก AnswerGenerator.generate() — ไม่มี partial answer."""

    answer_text: str
    citations_sent: int
    generation_time_seconds: float
    versions_in_prompt: tuple[CurriculumVersion, ...]


# ══════════════════════════════════════════════════════════════════════
# Constants
# ══════════════════════════════════════════════════════════════════════

MAX_EVIDENCE_UNITS = 60


# ══════════════════════════════════════════════════════════════════════
# AnswerGenerator
# ══════════════════════════════════════════════════════════════════════


class AnswerGenerator:
    """สร้างคำตอบจาก evidence units ผ่าน local LLM.

    - รับเฉพาะ evidence ที่มี citation ID (max 60 units)
    - แยกข้อมูลตาม version สำหรับ L4 cross-version comparison
    - Numeric values จาก Curriculum_Reasoner เท่านั้น
    - Cancel ทั้งหมดเมื่อเกิน time budget หรือ model fail
    """

    __slots__ = ("_llm",)

    def __init__(self, llm: LLMProtocol) -> None:
        """สร้าง AnswerGenerator พร้อม LLM backend.

        Args:
            llm: LLM protocol implementation (ต้องเป็น local เท่านั้น)
        """
        if not isinstance(llm, LLMProtocol):
            raise TypeError("llm ต้อง implement LLMProtocol")
        self._llm = llm

    def generate(
        self,
        question: str,
        evidence_units: list[EvidenceWithCitation],
        reasoner_values: list[ReasonerValue] | None = None,
        time_budget: float = 30.0,
    ) -> GenerationResult:
        """สร้างคำตอบจาก evidence ที่มี citation ID.

        Args:
            question: คำถามจากผู้ใช้
            evidence_units: evidence ที่ผ่าน CitationRegistry แล้ว (max 60)
            reasoner_values: ค่าเชิงตัวเลขจาก Curriculum_Reasoner
            time_budget: เวลาสูงสุดที่อนุญาต (วินาที)

        Returns:
            GenerationResult ที่มีคำตอบสมบูรณ์

        Raises:
            AnswerGenerationError: เมื่อ model fail หรือเกิน time budget
        """
        start_time = time.monotonic()

        # ── Validate inputs ──
        if not question:
            raise AnswerGenerationError("question ต้องไม่ว่าง")

        if not evidence_units:
            raise AnswerGenerationError("ต้องมี evidence units อย่างน้อย 1 ชิ้น")

        # ── Enforce max 60 units (R17.2) ──
        if len(evidence_units) > MAX_EVIDENCE_UNITS:
            evidence_units = evidence_units[:MAX_EVIDENCE_UNITS]

        # ── Check time budget before calling LLM ──
        elapsed = time.monotonic() - start_time
        if elapsed >= time_budget:
            raise AnswerGenerationError(
                "time budget exceeded before LLM call",
                elapsed=elapsed,
                budget=time_budget,
            )

        # ── Compose prompt ──
        prompt = self._compose_prompt(
            question=question,
            evidence_units=evidence_units,
            reasoner_values=reasoner_values or [],
        )

        # ── Call LLM ──
        try:
            answer_text = self._llm.generate(prompt)
        except AnswerGenerationError:
            raise
        except Exception as exc:
            raise AnswerGenerationError(
                f"LLM generation failed: {exc}",
                original_error=str(exc),
            ) from exc

        # ── Check time budget after LLM call ──
        elapsed = time.monotonic() - start_time
        if elapsed >= time_budget:
            raise AnswerGenerationError(
                "time budget exceeded after LLM call",
                elapsed=elapsed,
                budget=time_budget,
            )

        # ── Validate answer is not empty/partial ──
        if not answer_text or not answer_text.strip():
            raise AnswerGenerationError("LLM returned empty answer — cancelling")

        # ── Collect versions in prompt ──
        versions_in_prompt = tuple(sorted(
            set(eu.version for eu in evidence_units),
            key=lambda v: v.key(),
        ))

        generation_time = time.monotonic() - start_time

        return GenerationResult(
            answer_text=answer_text.strip(),
            citations_sent=len(evidence_units),
            generation_time_seconds=generation_time,
            versions_in_prompt=versions_in_prompt,
        )

    def _compose_prompt(
        self,
        question: str,
        evidence_units: list[EvidenceWithCitation],
        reasoner_values: list[ReasonerValue],
    ) -> str:
        """Compose prompt สำหรับ LLM — แยกตาม version (R10.7).

        Prompt structure:
        1. System instruction (ห้ามคำนวณ numeric, ใช้เฉพาะ citation ที่ให้)
        2. Reasoner values (numeric facts)
        3. Evidence grouped by version
        4. Question
        """
        parts: list[str] = []

        # ── System instruction ──
        parts.append(
            "คุณเป็นผู้ช่วยตอบคำถามเกี่ยวกับหลักสูตร KMITL\n"
            "กฎ:\n"
            "1. อ้างอิงเฉพาะ citation ID ที่ระบบให้เท่านั้น (เช่น [cite-001])\n"
            "2. ห้ามสร้าง citation ID ใหม่เอง\n"
            "3. ห้ามคำนวณค่าตัวเลข (หน่วยกิต, จำนวนวิชา) ด้วยตัวเอง — ใช้ค่าจาก Reasoner เท่านั้น\n"
            "4. ถ้ามีหลายเวอร์ชัน ให้แยกคำตอบตามเวอร์ชัน\n"
            "5. ทุกข้อกล่าวอ้างต้องมี citation ID กำกับ\n"
        )

        # ── Reasoner values (R15.5) ──
        if reasoner_values:
            parts.append("\n== ค่าจาก Curriculum_Reasoner (ใช้ค่านี้เท่านั้น ห้ามคำนวณเอง) ==")
            for rv in reasoner_values:
                parts.append(f"- {rv.label}: {rv.value} [{rv.citation_id}]")

        # ── Evidence grouped by version (R10.7) ──
        version_groups: dict[str, list[EvidenceWithCitation]] = {}
        for eu in evidence_units:
            key = eu.version.label()
            if key not in version_groups:
                version_groups[key] = []
            version_groups[key].append(eu)

        for version_label in sorted(version_groups.keys()):
            group = version_groups[version_label]
            parts.append(f"\n== หลักฐานจาก: {version_label} ==")
            for eu in group:
                parts.append(
                    f"[{eu.citation_id}] ({eu.unit.heading}, หน้า {eu.unit.page}): "
                    f"{eu.unit.text}"
                )

        # ── Question ──
        parts.append(f"\n== คำถาม ==\n{question}")

        return "\n".join(parts)
