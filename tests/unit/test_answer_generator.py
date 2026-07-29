"""Unit tests for katrag.query.answer_generator.

ทดสอบ R17.1, R17.2, R17.9, R10.7, R15.5:
- LLM protocol + StubLLM
- Max 60 evidence units enforced
- Time budget cancellation (no partial answer)
- Version-separated prompt composition
- Reasoner values in prompt
- Empty/failed LLM → AnswerGenerationError
"""

from __future__ import annotations

import pytest

from katrag.common.types import CitationId, CurriculumVersion
from katrag.errors import AnswerGenerationError
from katrag.query.answer_generator import (
    AnswerGenerator,
    EvidenceWithCitation,
    GenerationResult,
    LLMProtocol,
    MAX_EVIDENCE_UNITS,
    ReasonerValue,
    StubLLM,
)
from katrag.query.citation import EvidenceUnit


# ── Fixtures / helpers ────────────────────────────────────────────────


def _version(program: str = "IT", year: int = 2566) -> CurriculumVersion:
    return CurriculumVersion(program=program, curriculum_year=year, edition_status="current")


def _evidence(
    idx: int = 1,
    version: CurriculumVersion | None = None,
) -> EvidenceWithCitation:
    v = version or _version()
    unit = EvidenceUnit(
        chunk_id=f"chunk_{idx:03d}",
        document_id=f"doc_{idx:03d}",
        page=idx,
        heading=f"หัวข้อ {idx}",
        text=f"เนื้อหาตัวอย่าง {idx}",
    )
    return EvidenceWithCitation(
        unit=unit,
        citation_id=CitationId(value=f"cite-{idx:03d}"),
        version=v,
    )


# ══════════════════════════════════════════════════════════════════════
# Tests: StubLLM
# ══════════════════════════════════════════════════════════════════════


class TestStubLLM:
    def test_returns_predetermined_response(self) -> None:
        llm = StubLLM(response="Hello world")
        assert llm.generate("test") == "Hello world"

    def test_tracks_call_count(self) -> None:
        llm = StubLLM()
        llm.generate("a")
        llm.generate("b")
        assert llm.call_count == 2

    def test_tracks_last_prompt(self) -> None:
        llm = StubLLM()
        llm.generate("my prompt")
        assert llm.last_prompt == "my prompt"

    def test_fail_mode_raises(self) -> None:
        llm = StubLLM(fail=True)
        with pytest.raises(AnswerGenerationError):
            llm.generate("test")

    def test_implements_protocol(self) -> None:
        llm = StubLLM()
        assert isinstance(llm, LLMProtocol)


# ══════════════════════════════════════════════════════════════════════
# Tests: AnswerGenerator.generate — basic behavior
# ══════════════════════════════════════════════════════════════════════


class TestAnswerGeneratorBasic:
    def test_generates_answer_successfully(self) -> None:
        llm = StubLLM(response="คำตอบจาก LLM [cite-001]")
        gen = AnswerGenerator(llm)
        result = gen.generate(
            question="วิชาบังคับมีอะไรบ้าง?",
            evidence_units=[_evidence(1)],
        )
        assert isinstance(result, GenerationResult)
        assert result.answer_text == "คำตอบจาก LLM [cite-001]"
        assert result.citations_sent == 1

    def test_citations_sent_matches_evidence_count(self) -> None:
        llm = StubLLM(response="answer")
        gen = AnswerGenerator(llm)
        units = [_evidence(i) for i in range(1, 6)]
        result = gen.generate(question="q?", evidence_units=units)
        assert result.citations_sent == 5

    def test_versions_in_prompt_collected(self) -> None:
        llm = StubLLM(response="answer")
        gen = AnswerGenerator(llm)
        v1 = _version("IT", 2566)
        v2 = _version("DSBA", 2565)
        units = [_evidence(1, v1), _evidence(2, v2)]
        result = gen.generate(question="q?", evidence_units=units)
        assert len(result.versions_in_prompt) == 2

    def test_generation_time_recorded(self) -> None:
        llm = StubLLM(response="answer")
        gen = AnswerGenerator(llm)
        result = gen.generate(question="q?", evidence_units=[_evidence(1)])
        assert result.generation_time_seconds >= 0


# ══════════════════════════════════════════════════════════════════════
# Tests: Max 60 evidence units (R17.2)
# ══════════════════════════════════════════════════════════════════════


class TestMaxEvidenceUnits:
    def test_truncates_to_60_units(self) -> None:
        llm = StubLLM(response="answer")
        gen = AnswerGenerator(llm)
        units = [_evidence(i) for i in range(1, 80)]
        result = gen.generate(question="q?", evidence_units=units)
        assert result.citations_sent == MAX_EVIDENCE_UNITS

    def test_allows_exactly_60_units(self) -> None:
        llm = StubLLM(response="answer")
        gen = AnswerGenerator(llm)
        units = [_evidence(i) for i in range(1, 61)]
        result = gen.generate(question="q?", evidence_units=units)
        assert result.citations_sent == 60


# ══════════════════════════════════════════════════════════════════════
# Tests: Error handling — no partial answers (R17.9)
# ══════════════════════════════════════════════════════════════════════


class TestErrorHandling:
    def test_empty_question_raises(self) -> None:
        gen = AnswerGenerator(StubLLM())
        with pytest.raises(AnswerGenerationError, match="question"):
            gen.generate(question="", evidence_units=[_evidence(1)])

    def test_empty_evidence_raises(self) -> None:
        gen = AnswerGenerator(StubLLM())
        with pytest.raises(AnswerGenerationError, match="evidence"):
            gen.generate(question="q?", evidence_units=[])

    def test_llm_failure_raises(self) -> None:
        gen = AnswerGenerator(StubLLM(fail=True))
        with pytest.raises(AnswerGenerationError):
            gen.generate(question="q?", evidence_units=[_evidence(1)])

    def test_empty_llm_response_raises(self) -> None:
        gen = AnswerGenerator(StubLLM(response=""))
        with pytest.raises(AnswerGenerationError, match="empty"):
            gen.generate(question="q?", evidence_units=[_evidence(1)])

    def test_whitespace_only_response_raises(self) -> None:
        gen = AnswerGenerator(StubLLM(response="   \n  "))
        with pytest.raises(AnswerGenerationError, match="empty"):
            gen.generate(question="q?", evidence_units=[_evidence(1)])

    def test_time_budget_exceeded_raises(self) -> None:
        """Time budget exceeded after LLM call → cancel, no partial answer."""
        gen = AnswerGenerator(StubLLM(response="answer", delay_seconds=0.2))
        with pytest.raises(AnswerGenerationError, match="time budget"):
            gen.generate(
                question="q?",
                evidence_units=[_evidence(1)],
                time_budget=0.05,
            )


# ══════════════════════════════════════════════════════════════════════
# Tests: Prompt composition — version separation (R10.7)
# ══════════════════════════════════════════════════════════════════════


class TestPromptComposition:
    def test_prompt_contains_citation_ids(self) -> None:
        llm = StubLLM(response="answer")
        gen = AnswerGenerator(llm)
        gen.generate(question="q?", evidence_units=[_evidence(1)])
        assert "[cite-001]" in llm.last_prompt

    def test_prompt_separates_versions(self) -> None:
        """L4 cross-version comparison → evidence grouped by version."""
        llm = StubLLM(response="answer")
        gen = AnswerGenerator(llm)
        v1 = _version("IT", 2566)
        v2 = _version("DSBA", 2565)
        gen.generate(
            question="q?",
            evidence_units=[_evidence(1, v1), _evidence(2, v2)],
        )
        assert "IT 2566" in llm.last_prompt
        assert "DSBA 2565" in llm.last_prompt

    def test_prompt_contains_system_instruction(self) -> None:
        llm = StubLLM(response="answer")
        gen = AnswerGenerator(llm)
        gen.generate(question="q?", evidence_units=[_evidence(1)])
        assert "ห้ามคำนวณ" in llm.last_prompt

    def test_prompt_contains_reasoner_values(self) -> None:
        """R15.5: numeric values from Curriculum_Reasoner in prompt."""
        llm = StubLLM(response="answer")
        gen = AnswerGenerator(llm)
        rv = ReasonerValue(label="total_credits", value=135, citation_id="cite-001")
        gen.generate(
            question="q?",
            evidence_units=[_evidence(1)],
            reasoner_values=[rv],
        )
        assert "total_credits" in llm.last_prompt
        assert "135" in llm.last_prompt

    def test_prompt_contains_question(self) -> None:
        llm = StubLLM(response="answer")
        gen = AnswerGenerator(llm)
        gen.generate(question="วิชาบังคับมีกี่วิชา?", evidence_units=[_evidence(1)])
        assert "วิชาบังคับมีกี่วิชา?" in llm.last_prompt


# ══════════════════════════════════════════════════════════════════════
# Tests: LLMProtocol type checking
# ══════════════════════════════════════════════════════════════════════


class TestProtocol:
    def test_rejects_non_protocol(self) -> None:
        with pytest.raises(TypeError, match="LLMProtocol"):
            AnswerGenerator("not_a_llm")  # type: ignore[arg-type]
