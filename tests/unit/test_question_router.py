"""Unit tests for katrag.query.question_router (R16.1–R16.7).

ทดสอบ:
- R16.1: จำแนก L1–L4 ภายใน 200ms พร้อมบันทึก level, confidence, rule_id, elapsed_ms
- R16.2: L1/L2 → structured path ไม่เรียก Evidence_Planner, เสร็จภายใน 1,000ms
- R16.3: L3/L4 → Evidence_Planner ไม่เกิน 2 curriculum version ต่อคำขอ
- R16.4: confidence < 0.50 → router_fallback ไป L3
- R16.6: ปฏิเสธคำถามยาว 0 หรือ > 500 อักขระ → question_input_invalid
- R16.7: route_escalated ไม่เกิน 1 ครั้งต่อคำขอเมื่อ L1/L2 คืนผลว่าง
"""

from __future__ import annotations

import time
from typing import Any, Sequence
from unittest.mock import MagicMock

import pytest

from katrag.common.types import CurriculumVersion, QuestionLevel
from katrag.config import QuestionRouterConfig
from katrag.errors import QuestionInputInvalidError
from katrag.query.question_router import (
    ClassificationResult,
    RouteDecision,
    classify_question,
    route_question,
)


# ── fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def router_config() -> QuestionRouterConfig:
    """ค่าตั้งค่า router ตัวอย่าง."""
    return QuestionRouterConfig(
        max_question_chars=500,
        api_max_question_chars=500,
        retriever_max_question_chars=1000,
        min_confidence=0.50,
        classification_budget_ms=200,
        structured_path_budget_ms=1000,
        max_route_escalations=1,
    )


@pytest.fixture
def sample_versions() -> list[CurriculumVersion]:
    """ชุด curriculum version ตัวอย่าง."""
    return [
        CurriculumVersion(program="IT", curriculum_year=2565, edition_status="current"),
        CurriculumVersion(program="IT", curriculum_year=2560, edition_status="old"),
        CurriculumVersion(program="BIT", curriculum_year=2565, edition_status="current"),
    ]


# ── R16.6: Input validation ───────────────────────────────────────────


class TestInputValidation:
    """R16.6: ปฏิเสธคำถามที่ยาว 0 หรือเกิน 500 อักขระ."""

    def test_empty_question_raises(self, router_config: QuestionRouterConfig) -> None:
        """คำถามว่าง → QuestionInputInvalidError."""
        with pytest.raises(QuestionInputInvalidError):
            route_question("", config=router_config)

    def test_whitespace_only_raises(self, router_config: QuestionRouterConfig) -> None:
        """คำถามที่มีแต่ whitespace → QuestionInputInvalidError."""
        with pytest.raises(QuestionInputInvalidError):
            route_question("   \t\n  ", config=router_config)

    def test_over_500_chars_raises(self, router_config: QuestionRouterConfig) -> None:
        """คำถามเกิน 500 อักขระ → QuestionInputInvalidError."""
        long_question = "ก" * 501
        with pytest.raises(QuestionInputInvalidError):
            route_question(long_question, config=router_config)

    def test_exactly_500_chars_ok(self, router_config: QuestionRouterConfig) -> None:
        """คำถาม 500 อักขระพอดี → ไม่ raise."""
        question = "ก" * 500
        result = route_question(question, config=router_config)
        assert result is not None

    def test_one_char_question_ok(self, router_config: QuestionRouterConfig) -> None:
        """คำถาม 1 อักขระ → ไม่ raise."""
        result = route_question("A", config=router_config)
        assert result is not None

    def test_error_contains_length_info(self, router_config: QuestionRouterConfig) -> None:
        """Error payload ต้องระบุ length, min_chars, max_chars."""
        with pytest.raises(QuestionInputInvalidError) as exc_info:
            route_question("", config=router_config)
        err = exc_info.value
        assert err.length == 0
        assert err.context["min_chars"] == 1
        assert err.context["max_chars"] == 500


# ── R16.1: Classification L1–L4 ──────────────────────────────────────


class TestClassification:
    """R16.1: จำแนก L1–L4 ภายใน 200ms พร้อมบันทึก level, confidence, rule_id."""

    def test_l1_single_code_credits(self, router_config: QuestionRouterConfig) -> None:
        """L1: รหัสวิชาเดียว + ค้นค่าเดียว (หน่วยกิต)."""
        result = classify_question("06016101 มีกี่หน่วยกิต", router_config)
        assert result.level == "L1"
        assert result.confidence >= 0.50

    def test_l1_single_code_name(self, router_config: QuestionRouterConfig) -> None:
        """L1: รหัสวิชาเดียว + ชื่อวิชา."""
        result = classify_question("06016102 ชื่อวิชาอะไร", router_config)
        assert result.level == "L1"

    def test_l2_year_semester(self, router_config: QuestionRouterConfig) -> None:
        """L2: aggregation — ปีที่ + ภาค."""
        result = classify_question("ปีที่ 2 ภาค 1 มีวิชาอะไรบ้าง", router_config)
        assert result.level == "L2"
        assert result.confidence >= 0.50

    def test_l2_total_courses(self, router_config: QuestionRouterConfig) -> None:
        """L2: aggregation — กี่วิชา."""
        result = classify_question("หลักสูตรมีกี่วิชาทั้งหมด", router_config)
        assert result.level == "L2"

    def test_l3_prerequisite(self, router_config: QuestionRouterConfig) -> None:
        """L3: relationship — วิชาก่อน."""
        result = classify_question("สายวิชาก่อนของ 06016481", router_config)
        assert result.level == "L3"
        assert result.confidence >= 0.50

    def test_l3_chain(self, router_config: QuestionRouterConfig) -> None:
        """L3: relationship — เรียนก่อน."""
        result = classify_question("ต้องเรียนก่อนวิชา 06016302 อะไรบ้าง", router_config)
        assert result.level == "L3"

    def test_l4_comparison_two_versions(self, router_config: QuestionRouterConfig) -> None:
        """L4: comparison + 2 version references."""
        result = classify_question(
            "เกณฑ์สำเร็จต่างกันอย่างไรระหว่าง IT 2560 กับ 2565", router_config
        )
        assert result.level == "L4"
        assert result.confidence >= 0.70

    def test_l4_comparison_single_version(self, router_config: QuestionRouterConfig) -> None:
        """L4: comparison keyword เพียงอย่างเดียว."""
        result = classify_question("เปรียบเทียบวิชาบังคับ", router_config)
        assert result.level == "L4"

    def test_classification_within_200ms(self, router_config: QuestionRouterConfig) -> None:
        """การจำแนกต้องเสร็จภายใน 200ms."""
        result = classify_question("06016101 มีกี่หน่วยกิต", router_config)
        assert result.elapsed_ms < 200.0

    def test_classification_records_rule_id(self, router_config: QuestionRouterConfig) -> None:
        """ต้องบันทึก rule_id ที่ใช้ตัดสิน."""
        result = classify_question("06016101 มีกี่หน่วยกิต", router_config)
        assert result.rule_id != ""
        assert isinstance(result.rule_id, str)

    def test_classification_records_elapsed_ms(self, router_config: QuestionRouterConfig) -> None:
        """ต้องบันทึก elapsed_ms."""
        result = classify_question("ปีที่ 2 มีวิชาอะไรบ้าง", router_config)
        assert result.elapsed_ms >= 0.0


# ── R16.4: Confidence fallback ────────────────────────────────────────


class TestConfidenceFallback:
    """R16.4: confidence < 0.50 → router_fallback ไป L3."""

    def test_low_confidence_fallback_to_l3(self, router_config: QuestionRouterConfig) -> None:
        """คำถามที่ไม่มี signal ชัดเจน → confidence < 0.50 → fallback L3."""
        # A generic question with no clear patterns
        result = route_question("สวัสดีครับ", config=router_config)
        assert result.level == "L3"
        assert result.fallback_applied is True
        assert "router_fallback" in result.rule_id

    def test_high_confidence_no_fallback(self, router_config: QuestionRouterConfig) -> None:
        """คำถามที่มี signal ชัดเจน → confidence >= 0.50 → ไม่ fallback."""
        result = route_question("06016101 มีกี่หน่วยกิต", config=router_config)
        assert result.confidence >= 0.50
        assert result.fallback_applied is False


# ── R16.2: Structured path for L1/L2 ─────────────────────────────────


class TestStructuredPath:
    """R16.2: L1/L2 → structured path ที่ไม่เรียก Evidence_Planner."""

    def test_l1_uses_structured_path(self, router_config: QuestionRouterConfig) -> None:
        """L1 → path = 'structured'."""
        result = route_question("06016101 มีกี่หน่วยกิต", config=router_config)
        assert result.path == "structured"

    def test_l2_uses_structured_path(self, router_config: QuestionRouterConfig) -> None:
        """L2 → path = 'structured'."""
        result = route_question("ปีที่ 2 ภาค 1 มีวิชาอะไรบ้าง", config=router_config)
        assert result.path == "structured"

    def test_structured_path_does_not_call_evidence_planner(
        self, router_config: QuestionRouterConfig
    ) -> None:
        """L1/L2 ต้องไม่เรียก Evidence_Planner."""
        evidence_mock = MagicMock()
        structured_mock = MagicMock(return_value=[{"credits": 3}])

        route_question(
            "06016101 มีกี่หน่วยกิต",
            config=router_config,
            structured_executor=structured_mock,
            evidence_executor=evidence_mock,
            versions=[CurriculumVersion("IT", 2565, "current")],
        )

        structured_mock.assert_called_once()
        evidence_mock.assert_not_called()

    def test_structured_path_within_1000ms(
        self, router_config: QuestionRouterConfig
    ) -> None:
        """Structured path ต้องเสร็จภายใน 1,000ms (ตอน route ทั้งหมด)."""
        result = route_question("06016101 มีกี่หน่วยกิต", config=router_config)
        assert result.elapsed_ms < 1000.0


# ── R16.3: Evidence_Planner for L3/L4 ────────────────────────────────


class TestEvidencePlanner:
    """R16.3: L3/L4 → Evidence_Planner ไม่เกิน 2 curriculum version ต่อคำขอ."""

    def test_l3_uses_evidence_planner(self, router_config: QuestionRouterConfig) -> None:
        """L3 → path = 'evidence_planner'."""
        result = route_question("สายวิชาก่อนของ 06016481", config=router_config)
        assert result.path == "evidence_planner"

    def test_l4_uses_evidence_planner(self, router_config: QuestionRouterConfig) -> None:
        """L4 → path = 'evidence_planner'."""
        result = route_question(
            "เกณฑ์สำเร็จต่างกันอย่างไรระหว่าง IT 2560 กับ 2565",
            config=router_config,
        )
        assert result.path == "evidence_planner"

    def test_evidence_planner_max_2_versions(
        self,
        router_config: QuestionRouterConfig,
        sample_versions: list[CurriculumVersion],
    ) -> None:
        """Evidence_Planner ต้องรับไม่เกิน 2 versions."""
        evidence_mock = MagicMock()

        route_question(
            "สายวิชาก่อนของ 06016481",
            config=router_config,
            evidence_executor=evidence_mock,
            versions=sample_versions,  # 3 versions
        )

        evidence_mock.assert_called_once()
        call_args = evidence_mock.call_args
        versions_passed = call_args[0][2]  # third positional arg
        assert len(versions_passed) <= 2

    def test_evidence_planner_called_with_level(
        self,
        router_config: QuestionRouterConfig,
        sample_versions: list[CurriculumVersion],
    ) -> None:
        """Evidence_Planner ต้องรับ level ที่จำแนกได้."""
        evidence_mock = MagicMock()

        route_question(
            "สายวิชาก่อนของ 06016481",
            config=router_config,
            evidence_executor=evidence_mock,
            versions=sample_versions[:2],
        )

        evidence_mock.assert_called_once()
        call_args = evidence_mock.call_args
        assert call_args[0][1] == "L3"


# ── R16.7: Route escalation ───────────────────────────────────────────


class TestRouteEscalation:
    """R16.7: route_escalated ไม่เกิน 1 ครั้งต่อคำขอเมื่อ L1/L2 คืนผลว่าง."""

    def test_escalation_when_structured_empty(
        self,
        router_config: QuestionRouterConfig,
        sample_versions: list[CurriculumVersion],
    ) -> None:
        """L1/L2 คืนผลว่าง → escalate to L3."""
        structured_mock = MagicMock(return_value=[])  # empty result
        evidence_mock = MagicMock()

        result = route_question(
            "06016101 มีกี่หน่วยกิต",
            config=router_config,
            structured_executor=structured_mock,
            evidence_executor=evidence_mock,
            versions=sample_versions,
        )

        assert result.escalated is True
        assert result.level == "L3"
        assert result.path == "evidence_planner"
        assert "route_escalated" in result.rule_id

    def test_no_escalation_when_structured_has_results(
        self, router_config: QuestionRouterConfig
    ) -> None:
        """L1/L2 คืนผลไม่ว่าง → ไม่ escalate."""
        structured_mock = MagicMock(return_value=[{"credits": 3}])

        result = route_question(
            "06016101 มีกี่หน่วยกิต",
            config=router_config,
            structured_executor=structured_mock,
        )

        assert result.escalated is False
        assert result.level == "L1"
        assert result.path == "structured"

    def test_escalation_max_once(
        self,
        router_config: QuestionRouterConfig,
        sample_versions: list[CurriculumVersion],
    ) -> None:
        """Escalation ต้องไม่เกิน 1 ครั้งต่อคำขอ."""
        structured_mock = MagicMock(return_value=[])

        result = route_question(
            "06016101 มีกี่หน่วยกิต",
            config=router_config,
            structured_executor=structured_mock,
            versions=sample_versions,
        )

        assert result.escalation_count <= 1

    def test_escalation_calls_evidence_planner(
        self,
        router_config: QuestionRouterConfig,
        sample_versions: list[CurriculumVersion],
    ) -> None:
        """หลัง escalation → เรียก Evidence_Planner."""
        structured_mock = MagicMock(return_value=[])
        evidence_mock = MagicMock()

        route_question(
            "06016101 มีกี่หน่วยกิต",
            config=router_config,
            structured_executor=structured_mock,
            evidence_executor=evidence_mock,
            versions=sample_versions,
        )

        evidence_mock.assert_called_once()


# ── RouteDecision structure ───────────────────────────────────────────


class TestRouteDecisionStructure:
    """RouteDecision ต้องมีข้อมูลครบ: level, confidence, rule_id, path, elapsed_ms."""

    def test_route_decision_has_all_fields(
        self, router_config: QuestionRouterConfig
    ) -> None:
        """RouteDecision ต้องมีทุก field."""
        result = route_question("06016101 มีกี่หน่วยกิต", config=router_config)

        assert result.level in ("L1", "L2", "L3", "L4")
        assert 0.0 <= result.confidence <= 1.0
        assert isinstance(result.rule_id, str) and result.rule_id
        assert result.path in ("structured", "evidence_planner")
        assert result.elapsed_ms >= 0.0
        assert isinstance(result.fallback_applied, bool)
        assert isinstance(result.escalated, bool)
        assert isinstance(result.escalation_count, int)

    def test_route_decision_elapsed_ms_nonnegative(
        self, router_config: QuestionRouterConfig
    ) -> None:
        """elapsed_ms ต้องไม่ติดลบ."""
        result = route_question("ปีที่ 2 มีวิชาอะไรบ้าง", config=router_config)
        assert result.elapsed_ms >= 0.0


# ── ClassificationResult validation ──────────────────────────────────


class TestClassificationResultValidation:
    """ClassificationResult dataclass validation."""

    def test_confidence_out_of_range_raises(self) -> None:
        """confidence นอกช่วง 0-1 → ValueError."""
        with pytest.raises(ValueError, match="confidence"):
            ClassificationResult(level="L1", confidence=1.5, rule_id="test", elapsed_ms=0.1)

    def test_negative_confidence_raises(self) -> None:
        """confidence ติดลบ → ValueError."""
        with pytest.raises(ValueError, match="confidence"):
            ClassificationResult(level="L1", confidence=-0.1, rule_id="test", elapsed_ms=0.1)

    def test_negative_elapsed_raises(self) -> None:
        """elapsed_ms ติดลบ → ValueError."""
        with pytest.raises(ValueError, match="elapsed_ms"):
            ClassificationResult(level="L1", confidence=0.5, rule_id="test", elapsed_ms=-1.0)

    def test_valid_classification_result(self) -> None:
        """ClassificationResult ที่ถูกต้อง."""
        result = ClassificationResult(
            level="L2", confidence=0.85, rule_id="R2_aggregation_no_code", elapsed_ms=0.5
        )
        assert result.level == "L2"
        assert result.confidence == 0.85


# ── Edge cases ────────────────────────────────────────────────────────


class TestEdgeCases:
    """Edge cases for question router."""

    def test_no_executors_provided(self, router_config: QuestionRouterConfig) -> None:
        """ไม่มี executor → ยังคืน RouteDecision ได้ (ใช้จำแนกอย่างเดียว)."""
        result = route_question("06016101 มีกี่หน่วยกิต", config=router_config)
        assert result is not None
        assert result.level in ("L1", "L2", "L3", "L4")

    def test_versions_none_for_evidence_planner(
        self, router_config: QuestionRouterConfig
    ) -> None:
        """versions = None → ไม่เรียก Evidence_Planner."""
        evidence_mock = MagicMock()

        route_question(
            "สายวิชาก่อนของ 06016481",
            config=router_config,
            evidence_executor=evidence_mock,
            versions=None,
        )

        evidence_mock.assert_not_called()

    def test_boundary_500_chars_accepted(
        self, router_config: QuestionRouterConfig
    ) -> None:
        """คำถาม 500 อักขระพอดี → accepted."""
        question = "ว" * 500
        result = route_question(question, config=router_config)
        assert result is not None

    def test_boundary_501_chars_rejected(
        self, router_config: QuestionRouterConfig
    ) -> None:
        """คำถาม 501 อักขระ → rejected."""
        question = "ว" * 501
        with pytest.raises(QuestionInputInvalidError):
            route_question(question, config=router_config)
