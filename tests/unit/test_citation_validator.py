"""Unit tests for katrag.query.citation_validator.

ทดสอบ R17.3, R17.4, R17.5, R10.8, R15.6:
- Split answer into claim units
- Valid citation IDs pass through
- Unknown citation IDs → claim removed
- Factual claims without citation → unsupported_claim
- Cross-version violation → reject entire answer
- Numeric replacement from reasoner
"""

from __future__ import annotations

import pytest

from katrag.common.types import CitationId, CurriculumVersion
from katrag.errors import CrossVersionCitationError
from katrag.query.answer_generator import ReasonerValue
from katrag.query.citation import CitationRegistry, EvidenceUnit
from katrag.query.citation_validator import (
    CitationValidator,
    ClaimUnit,
    ValidationResult,
)


# ── Fixtures / helpers ────────────────────────────────────────────────


def _version(program: str = "IT", year: int = 2566) -> CurriculumVersion:
    return CurriculumVersion(program=program, curriculum_year=year, edition_status="current")


def _setup_registry(count: int = 3) -> CitationRegistry:
    """Create a registry with `count` issued citation IDs."""
    reg = CitationRegistry()
    for i in range(1, count + 1):
        reg.issue(EvidenceUnit(
            chunk_id=f"chunk_{i:03d}",
            document_id=f"doc_{i:03d}",
            page=i,
            heading=f"heading_{i}",
            text=f"text_{i}",
        ))
    return reg


# ══════════════════════════════════════════════════════════════════════
# Tests: Basic claim splitting
# ══════════════════════════════════════════════════════════════════════


class TestClaimSplitting:
    def test_splits_by_newline(self) -> None:
        reg = _setup_registry(2)
        validator = CitationValidator(reg)
        answer = "วิชา A มี 3 หน่วยกิต [cite-001]\nวิชา B มี 4 หน่วยกิต [cite-002]"
        result = validator.validate(answer)
        assert len(result.claim_units) == 2

    def test_splits_by_sentence(self) -> None:
        reg = _setup_registry(2)
        validator = CitationValidator(reg)
        answer = "วิชา A มี 3 หน่วยกิต [cite-001]. วิชา B มี 4 หน่วยกิต [cite-002]."
        result = validator.validate(answer)
        assert len(result.claim_units) == 2

    def test_splits_bullet_points(self) -> None:
        reg = _setup_registry(2)
        validator = CitationValidator(reg)
        answer = "- วิชา A [cite-001]\n- วิชา B [cite-002]"
        result = validator.validate(answer)
        assert len(result.claim_units) == 2


# ══════════════════════════════════════════════════════════════════════
# Tests: Valid citations pass through (R17.3)
# ══════════════════════════════════════════════════════════════════════


class TestValidCitations:
    def test_valid_citations_preserved(self) -> None:
        reg = _setup_registry(3)
        validator = CitationValidator(reg)
        answer = "วิชาบังคับคือ CS101 มี 3 หน่วยกิต [cite-001]"
        result = validator.validate(answer)
        assert result.citations_passed == 1
        assert result.citations_removed == 0

    def test_multiple_valid_citations_in_one_claim(self) -> None:
        reg = _setup_registry(3)
        validator = CitationValidator(reg)
        answer = "วิชาบังคับ CS101 [cite-001] และ CS102 [cite-002] รวม 6 หน่วยกิต"
        result = validator.validate(answer)
        assert result.citations_passed == 2

    def test_all_valid_citations_no_removal(self) -> None:
        reg = _setup_registry(3)
        validator = CitationValidator(reg)
        answer = (
            "วิชา A [cite-001]\n"
            "วิชา B [cite-002]\n"
            "วิชา C [cite-003]"
        )
        result = validator.validate(answer)
        assert result.citations_removed == 0
        assert result.citations_passed == 3


# ══════════════════════════════════════════════════════════════════════
# Tests: Unknown citation IDs → remove claim (R17.4)
# ══════════════════════════════════════════════════════════════════════


class TestUnknownCitations:
    def test_claim_with_unknown_id_removed(self) -> None:
        reg = _setup_registry(2)
        validator = CitationValidator(reg)
        answer = "วิชา X [cite-999] ไม่มีในระบบ"
        result = validator.validate(answer)
        assert result.citations_removed == 1
        # The claim should be removed from output
        assert "cite-999" not in result.validated_answer

    def test_claim_with_mixed_valid_and_invalid(self) -> None:
        """Claim with both valid and invalid → keep valid, remove invalid."""
        reg = _setup_registry(2)
        validator = CitationValidator(reg)
        answer = "วิชา A [cite-001] และ B [cite-999] อยู่ในหลักสูตร"
        result = validator.validate(answer)
        assert result.citations_removed == 1
        assert "cite-001" in result.validated_answer
        assert "cite-999" not in result.validated_answer

    def test_removal_count_increments(self) -> None:
        reg = _setup_registry(1)
        validator = CitationValidator(reg)
        answer = "ข้อมูล A [cite-888]\nข้อมูล B [cite-999]"
        result = validator.validate(answer)
        assert result.citations_removed == 2


# ══════════════════════════════════════════════════════════════════════
# Tests: Unsupported claims (R17.5)
# ══════════════════════════════════════════════════════════════════════


class TestUnsupportedClaims:
    def test_factual_claim_without_citation_marked(self) -> None:
        reg = _setup_registry(1)
        validator = CitationValidator(reg)
        answer = "หลักสูตร IT 2566 มีวิชาบังคับทั้งหมด 15 วิชาที่ต้องเรียน"
        result = validator.validate(answer)
        assert result.unsupported_claims >= 1
        # Check that claim is marked
        unsupported = [c for c in result.claim_units if c.is_unsupported]
        assert len(unsupported) >= 1

    def test_header_not_marked_as_unsupported(self) -> None:
        """Headers / section markers should NOT be marked as unsupported."""
        reg = _setup_registry(1)
        validator = CitationValidator(reg)
        answer = "== หลักสูตร IT =="
        result = validator.validate(answer)
        assert result.unsupported_claims == 0

    def test_short_text_not_marked(self) -> None:
        """Very short text (< 10 chars) not marked as unsupported."""
        reg = _setup_registry(1)
        validator = CitationValidator(reg)
        answer = "ดังนั้น"
        result = validator.validate(answer)
        assert result.unsupported_claims == 0

    def test_mix_of_supported_and_unsupported(self) -> None:
        reg = _setup_registry(2)
        validator = CitationValidator(reg)
        answer = (
            "วิชา CS101 มี 3 หน่วยกิต [cite-001]\n"
            "วิชา CS102 มี 4 หน่วยกิตที่นักศึกษาต้องเรียนให้ครบ"
        )
        result = validator.validate(answer)
        assert result.citations_passed == 1
        assert result.unsupported_claims == 1


# ══════════════════════════════════════════════════════════════════════
# Tests: Cross-version violation (R10.8)
# ══════════════════════════════════════════════════════════════════════


class TestCrossVersionViolation:
    def test_cross_version_raises_error(self) -> None:
        """Citation referencing version outside allowed set → reject entire answer."""
        reg = _setup_registry(2)
        v_allowed = _version("IT", 2566)
        v_other = _version("DSBA", 2565)

        # cite-001 belongs to allowed version, cite-002 belongs to other
        version_map = {
            "cite-001": v_allowed,
            "cite-002": v_other,
        }

        validator = CitationValidator(reg, version_map=version_map)
        answer = "วิชา A [cite-001] และ B [cite-002] ข้ามหลักสูตร"

        with pytest.raises(CrossVersionCitationError):
            validator.validate(
                answer,
                allowed_versions=frozenset({v_allowed}),
            )

    def test_no_violation_when_all_same_version(self) -> None:
        reg = _setup_registry(2)
        v = _version("IT", 2566)
        version_map = {"cite-001": v, "cite-002": v}

        validator = CitationValidator(reg, version_map=version_map)
        answer = "วิชา A [cite-001] และ B [cite-002]"

        result = validator.validate(
            answer,
            allowed_versions=frozenset({v}),
        )
        assert result.cross_version_violation is False


# ══════════════════════════════════════════════════════════════════════
# Tests: Numeric replacement (R15.6)
# ══════════════════════════════════════════════════════════════════════


class TestNumericReplacement:
    def test_replaces_wrong_numeric(self) -> None:
        reg = _setup_registry(1)
        validator = CitationValidator(reg)
        rv = ReasonerValue(label="total_credits", value=135, citation_id="cite-001")
        answer = "total_credits ของหลักสูตรคือ 140 หน่วยกิต [cite-001]"
        result = validator.validate(answer, reasoner_values=[rv])
        assert "135" in result.validated_answer
        assert result.numeric_replacements >= 1

    def test_no_replacement_when_correct(self) -> None:
        reg = _setup_registry(1)
        validator = CitationValidator(reg)
        rv = ReasonerValue(label="total_credits", value=135, citation_id="cite-001")
        answer = "total_credits ของหลักสูตรคือ 135 หน่วยกิต [cite-001]"
        result = validator.validate(answer, reasoner_values=[rv])
        assert "135" in result.validated_answer
        assert result.numeric_replacements == 0


# ══════════════════════════════════════════════════════════════════════
# Tests: ValidationResult structure
# ══════════════════════════════════════════════════════════════════════


class TestValidationResult:
    def test_result_has_all_fields(self) -> None:
        reg = _setup_registry(1)
        validator = CitationValidator(reg)
        result = validator.validate("วิชา A [cite-001]")
        assert isinstance(result, ValidationResult)
        assert isinstance(result.validated_answer, str)
        assert isinstance(result.claim_units, tuple)
        assert isinstance(result.citations_passed, int)
        assert isinstance(result.citations_removed, int)
        assert isinstance(result.unsupported_claims, int)
        assert isinstance(result.numeric_replacements, int)
        assert isinstance(result.cross_version_violation, bool)

    def test_empty_answer_returns_empty_result(self) -> None:
        reg = _setup_registry(1)
        validator = CitationValidator(reg)
        result = validator.validate("")
        assert result.validated_answer == ""
        assert result.citations_passed == 0
