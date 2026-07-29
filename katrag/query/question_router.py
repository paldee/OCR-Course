"""Question Router — จำแนกระดับคำถาม L1–L4 และเลือกเส้นทางประมวลผล (R16.1–R16.7).

ลำดับการทำงาน:
1. ตรวจความยาวคำถาม — ปฏิเสธเมื่อ 0 หรือ > 500 อักขระ (R16.6)
2. จำแนก L1–L4 ด้วย rule-based pattern matching ภายใน 200 มิลลิวินาที (R16.1)
3. ถ้า confidence < 0.50 → fallback ไป L3 (R16.4)
4. L1/L2 → structured path (predefined SQL) ภายใน 1,000 มิลลิวินาที (R16.2)
5. L3/L4 → Evidence_Planner ไม่เกิน 2 curriculum version ต่อคำขอ (R16.3)
6. route_escalated ไม่เกิน 1 ครั้งต่อคำขอเมื่อ L1/L2 คืนผลว่าง (R16.7)

Classification approach: Rule-based keyword/pattern matching (ไม่ใช่ ML) เพื่อความเร็ว:
- L1: single course code + single field query (credits, prerequisite, name)
- L2: aggregation keywords ("ปีที่", "ภาค", "ทั้งหมด", "กี่วิชา")
- L3: relationship keywords ("วิชาก่อน", "ก่อนหน้า", "ต่อจาก")
- L4: comparison keywords ("เปรียบเทียบ", "ต่างกัน", "เหมือนกัน") + multiple version references
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Protocol, Sequence

from katrag.common.types import CurriculumVersion, QuestionLevel
from katrag.config import QuestionRouterConfig
from katrag.errors import QuestionInputInvalidError


# ── result types ──────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ClassificationResult:
    """ผลการจำแนกระดับคำถาม."""

    level: QuestionLevel
    confidence: float
    rule_id: str
    elapsed_ms: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence ต้องอยู่ในช่วง 0.0-1.0")
        if self.elapsed_ms < 0.0:
            raise ValueError("elapsed_ms ต้องไม่ติดลบ")


@dataclass(frozen=True, slots=True)
class RouteDecision:
    """ผลลัพธ์การเลือกเส้นทางของ router."""

    level: QuestionLevel
    confidence: float
    rule_id: str
    path: Literal["structured", "evidence_planner"]
    elapsed_ms: float
    fallback_applied: bool = False
    escalated: bool = False
    escalation_count: int = 0


# ── protocols for dependency injection ────────────────────────────────


class StructuredPathExecutor(Protocol):
    """Interface สำหรับ structured path (predefined SQL) — L1/L2."""

    def __call__(
        self,
        question: str,
        level: QuestionLevel,
    ) -> list[dict[str, Any]]: ...


class EvidencePlannerExecutor(Protocol):
    """Interface สำหรับ Evidence_Planner — L3/L4."""

    def __call__(
        self,
        question: str,
        level: QuestionLevel,
        versions: Sequence[CurriculumVersion],
    ) -> list[dict[str, Any]]: ...


# ── classification rules ──────────────────────────────────────────────

# Course code pattern: 8 หลักตัวเลข หรือ ตัวอักษร+ตัวเลข
_COURSE_CODE_PATTERN = re.compile(r"\b\d{8}\b|\b[A-Za-z]{2,4}\d{4,6}\b")

# L1 field keywords: ค้นค่าเดียว (credits, name, prerequisite of a single course)
_L1_FIELD_KEYWORDS = (
    "หน่วยกิต", "กี่หน่วยกิต", "ชื่อวิชา", "ชื่อภาษาอังกฤษ",
    "ชื่อภาษาไทย", "คำอธิบายรายวิชา", "ประเภทวิชา",
)

# L2 aggregation keywords (ไม่รวม "กี่หน่วยกิต" ซึ่งอยู่ใน L1 เมื่อมีรหัสวิชาเดียว)
_L2_KEYWORDS = (
    "ปีที่", "ภาคที่", "ภาค", "ทั้งหมด", "กี่วิชา", "รวม",
    "เทอม", "ภาคเรียน", "วิชาอะไรบ้าง", "รายวิชาทั้งหมด",
    "วิชาบังคับ", "วิชาเลือก", "หมวดวิชา",
)

# L3 relationship keywords
_L3_KEYWORDS = (
    "วิชาก่อน", "ก่อนหน้า", "ต่อจาก", "prerequisite", "สายวิชา",
    "ต่อเนื่อง", "ลงก่อน", "เรียนก่อน", "วิชาต่อ",
    "พื้นฐาน", "เงื่อนไข",
)

# L4 comparison keywords
_L4_COMPARISON_KEYWORDS = (
    "เปรียบเทียบ", "ต่างกัน", "เหมือนกัน", "ต่างจาก", "เทียบ",
    "เปลี่ยนแปลง", "ปรับปรุง", "แตกต่าง", "versus", "vs",
)

# Version reference pattern (Buddhist era years)
_VERSION_YEAR_PATTERN = re.compile(r"\b25(?:6[0-9]|7[0-9])\b")


# ── classifier ────────────────────────────────────────────────────────


def classify_question(
    question: str,
    config: QuestionRouterConfig,
) -> ClassificationResult:
    """จำแนกระดับคำถาม L1–L4 ด้วย rule-based pattern matching.

    ต้องเสร็จภายใน classification_budget_ms (200ms).

    Args:
        question: ข้อความคำถาม (ผ่านการตรวจความยาวแล้ว)
        config: QuestionRouterConfig จากไฟล์ตั้งค่า

    Returns:
        ClassificationResult พร้อม level, confidence, rule_id, elapsed_ms
    """
    t0 = time.perf_counter()

    level: QuestionLevel
    confidence: float
    rule_id: str

    # Normalize question for matching
    q_lower = question.lower().strip()

    # Count course codes
    course_codes = _COURSE_CODE_PATTERN.findall(question)
    num_course_codes = len(course_codes)

    # Count version year references
    version_years = _VERSION_YEAR_PATTERN.findall(question)
    num_version_refs = len(set(version_years))

    # Check keyword matches
    has_l4_comparison = any(kw in q_lower for kw in _L4_COMPARISON_KEYWORDS)
    has_l3_relationship = any(kw in q_lower for kw in _L3_KEYWORDS)
    has_l2_aggregation = any(kw in q_lower for kw in _L2_KEYWORDS)
    has_l1_field = any(kw in q_lower for kw in _L1_FIELD_KEYWORDS)

    # ── L4: comparison + multiple version references ──
    if has_l4_comparison and num_version_refs >= 2:
        level = "L4"
        confidence = 0.90
        rule_id = "R4_comparison_multi_version"
    elif has_l4_comparison:
        # Comparison keyword but only 1 or 0 version refs — still likely L4 but lower confidence
        level = "L4"
        confidence = 0.70
        rule_id = "R4_comparison_implicit_version"

    # ── L3: relationship keywords ──
    elif has_l3_relationship:
        if num_course_codes >= 1:
            level = "L3"
            confidence = 0.85
            rule_id = "R3_relationship_with_code"
        else:
            level = "L3"
            confidence = 0.70
            rule_id = "R3_relationship_general"

    # ── L1: single course code + single field query ──
    elif num_course_codes == 1 and has_l1_field and not has_l2_aggregation:
        level = "L1"
        confidence = 0.90
        rule_id = "R1_single_code_single_field"

    # ── L2: aggregation/listing patterns ──
    elif has_l2_aggregation:
        if num_course_codes == 0:
            level = "L2"
            confidence = 0.85
            rule_id = "R2_aggregation_no_code"
        else:
            level = "L2"
            confidence = 0.75
            rule_id = "R2_aggregation_with_codes"

    # ── L1: single course code alone (likely lookup) ──
    elif num_course_codes == 1:
        level = "L1"
        confidence = 0.65
        rule_id = "R1_single_code_implicit_field"

    # ── Fallback: cannot determine clearly ──
    else:
        level = "L2"
        confidence = 0.40
        rule_id = "R_default_uncertain"

    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    return ClassificationResult(
        level=level,
        confidence=confidence,
        rule_id=rule_id,
        elapsed_ms=elapsed_ms,
    )


# ── router ────────────────────────────────────────────────────────────


def route_question(
    question: str,
    *,
    config: QuestionRouterConfig,
    structured_executor: StructuredPathExecutor | None = None,
    evidence_executor: EvidencePlannerExecutor | None = None,
    versions: Sequence[CurriculumVersion] | None = None,
) -> RouteDecision:
    """จำแนกและเลือกเส้นทางสำหรับคำถาม.

    ขั้นตอน:
    1. ตรวจความยาวคำถาม → question_input_invalid (R16.6)
    2. จำแนก L1–L4 ภายใน 200ms (R16.1)
    3. confidence < 0.50 → fallback to L3 (R16.4)
    4. L1/L2 → structured path ภายใน 1,000ms (R16.2)
       - ถ้าผลว่าง → route_escalated ไป L3 ไม่เกิน 1 ครั้ง (R16.7)
    5. L3/L4 → Evidence_Planner ไม่เกิน 2 versions (R16.3)

    Args:
        question: ข้อความคำถามจากผู้ใช้
        config: QuestionRouterConfig จากไฟล์ตั้งค่า
        structured_executor: callable สำหรับ structured path (L1/L2)
        evidence_executor: callable สำหรับ Evidence_Planner (L3/L4)
        versions: curriculum versions สำหรับ Evidence_Planner (จำกัดไม่เกิน 2)

    Returns:
        RouteDecision พร้อมข้อมูลเส้นทางและเวลา

    Raises:
        QuestionInputInvalidError: เมื่อความยาวคำถาม = 0 หรือ > max_question_chars
    """
    t_start = time.perf_counter()

    # ── R16.6: ตรวจความยาวคำถาม ──
    question_stripped = question.strip() if question else ""
    question_len = len(question_stripped)

    if question_len == 0 or question_len > config.max_question_chars:
        raise QuestionInputInvalidError(
            length=question_len,
            min_chars=1,
            max_chars=config.max_question_chars,
        )

    # ── R16.1: จำแนกระดับ ──
    classification = classify_question(question_stripped, config)

    level = classification.level
    confidence = classification.confidence
    rule_id = classification.rule_id
    fallback_applied = False

    # ── R16.4: confidence < min_confidence → fallback to L3 ──
    if confidence < config.min_confidence:
        level = "L3"
        rule_id = f"router_fallback({classification.rule_id})"
        fallback_applied = True

    # ── R16.1: ตรวจว่าการจำแนกเสร็จภายใน budget ──
    classification_elapsed_ms = (time.perf_counter() - t_start) * 1000.0
    if classification_elapsed_ms > config.classification_budget_ms:
        # เกินงบ → fallback to L3
        level = "L3"
        rule_id = f"router_fallback(timeout:{classification.rule_id})"
        fallback_applied = True

    # ── เลือกเส้นทาง ──
    escalated = False
    escalation_count = 0

    if level in ("L1", "L2"):
        # ── R16.2: structured path ภายใน 1,000ms ──
        path: Literal["structured", "evidence_planner"] = "structured"

        if structured_executor is not None:
            t_structured_start = time.perf_counter()
            results = structured_executor(question_stripped, level)
            structured_elapsed_ms = (time.perf_counter() - t_structured_start) * 1000.0

            # ── R16.7: route_escalated เมื่อ L1/L2 คืนผลว่าง ──
            if not results and escalation_count < config.max_route_escalations:
                level = "L3"
                path = "evidence_planner"
                escalated = True
                escalation_count += 1
                rule_id = f"route_escalated({rule_id})"

                # เรียก Evidence_Planner หลัง escalation
                if evidence_executor is not None and versions is not None:
                    # R16.3: จำกัดไม่เกิน 2 versions
                    limited_versions = list(versions[:2])
                    evidence_executor(question_stripped, level, limited_versions)

    else:
        # ── R16.3: L3/L4 → Evidence_Planner ──
        path = "evidence_planner"

        if evidence_executor is not None and versions is not None:
            # R16.3: จำกัดไม่เกิน 2 curriculum version ต่อคำขอ
            limited_versions = list(versions[:2])
            evidence_executor(question_stripped, level, limited_versions)

    total_elapsed_ms = (time.perf_counter() - t_start) * 1000.0

    return RouteDecision(
        level=level,
        confidence=confidence,
        rule_id=rule_id,
        path=path,
        elapsed_ms=total_elapsed_ms,
        fallback_applied=fallback_applied,
        escalated=escalated,
        escalation_count=escalation_count,
    )
