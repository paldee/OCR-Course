"""Evaluation_Harness — คำนวณ metric ทุกตัวและออก evaluation report (R18).

Responsibilities:
- จับคู่ค่าด้วยกุญแจ (document_id, page) หรือ (document_id, page, field_name)
  หลัง NFC + ตัด whitespace หัวท้าย (R18.1)
- ปฏิเสธ metric ที่ขอบเขตหน้าไม่ตรง โดยคืน error แต่คำนวณ metric อื่นต่อ (R18.2)
- ผลิต evaluation report (R18.3): ค่า metric ทศนิยม 4 ตำแหน่ง, จำนวนตัวอย่าง,
  ชนิดแหล่งอ้างอิง, commit id, timestamp
- กฎสถานะ measured/estimate (R18.4, R18.5, R18.8):
  - measured เมื่อ samples >= min_samples_for_measured (30)
  - estimate เมื่อ samples < 30; ห้ามระบุ pass; บันทึก metric_sample_insufficient
- ตรวจ reproducibility (R18.7, R18.9): รันซ้ำต้องได้ค่าเท่าเดิม

Requirements: 18.1, 18.2, 18.3, 18.4, 18.5, 18.6, 18.7, 18.8, 18.9
"""

from __future__ import annotations

import json
import logging
import subprocess
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from katrag.config import EvaluationConfig
from katrag.errors import MetricNotReproducibleError, MetricScopeMismatchError

logger = logging.getLogger(__name__)


# ── Constants ─────────────────────────────────────────────────────────

_DECIMAL_PLACES = 4

_REFERENCE_SOURCES = frozenset(("teacher_ground_truth", "gold_set"))


# ── Helper: NFC + strip whitespace (R18.1) ────────────────────────────


def _normalize_key_text(text: str) -> str:
    """NFC normalize แล้วตัด whitespace หัวท้าย — ใช้สำหรับเปรียบเทียบ key."""
    return unicodedata.normalize("NFC", text).strip()


# ── Key-matching types ────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PageKey:
    """กุญแจระดับหน้า: (document_id, page) — R18.1."""

    document_id: str
    page: int


@dataclass(frozen=True, slots=True)
class FieldKey:
    """กุญแจระดับ field: (document_id, page, field_name) — R18.1."""

    document_id: str
    page: int
    field_name: str


# ── Data containers ───────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class MetricResult:
    """ผลลัพธ์ของ metric หนึ่งตัวใน evaluation report (R18.3, R18.4)."""

    name: str
    value: float
    samples: int
    status: str  # "measured" | "estimate"
    threshold: float | None
    reference_source: str
    pass_fail: str | None  # "pass" | "fail" | None (when estimate)
    condition_to_measured: str | None  # R18.5: เงื่อนไขเปลี่ยนสถานะ

    def to_dict(self) -> dict[str, Any]:
        """แปลงเป็น dict สำหรับ JSON report."""
        d: dict[str, Any] = {
            "name": self.name,
            "value": round(self.value, _DECIMAL_PLACES),
            "samples": self.samples,
            "status": self.status,
            "reference_source": self.reference_source,
        }
        if self.threshold is not None:
            d["threshold"] = self.threshold
        if self.pass_fail is not None:
            d["pass_fail"] = self.pass_fail
        if self.condition_to_measured is not None:
            d["condition_to_measured"] = self.condition_to_measured
        return d


@dataclass(frozen=True, slots=True)
class ScopeMismatchError:
    """Error เมื่อขอบเขตหน้าไม่ตรงกัน (R18.2)."""

    metric: str
    evaluated_document_id: str
    evaluated_page: int
    reference_document_id: str
    reference_page: int
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "evaluated": {
                "document_id": self.evaluated_document_id,
                "page": self.evaluated_page,
            },
            "reference": {
                "document_id": self.reference_document_id,
                "page": self.reference_page,
            },
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class ReviewIssue:
    """review_issue ที่ harness ต้องบันทึก (R18.8)."""

    kind: str
    metric: str
    samples: int
    detail: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "metric": self.metric,
            "samples": self.samples,
            **self.detail,
        }


@dataclass(slots=True)
class EvaluationReport:
    """evaluation report ที่ออกโดย Evaluation_Harness (R18.3)."""

    metrics: list[MetricResult] = field(default_factory=list)
    errors: list[ScopeMismatchError] = field(default_factory=list)
    review_issues: list[ReviewIssue] = field(default_factory=list)
    commit_id: str = ""
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "commit_id": self.commit_id,
            "timestamp": self.timestamp,
            "metrics": [m.to_dict() for m in self.metrics],
            "errors": [e.to_dict() for e in self.errors],
            "review_issues": [r.to_dict() for r in self.review_issues],
        }


# ── Git commit helper ─────────────────────────────────────────────────


def _get_commit_id() -> str:
    """ดึง git commit hash ปัจจุบัน; คืนสตริงว่างเมื่อไม่อยู่ใน git repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        pass
    return ""


# ── Key-matching functions (R18.1) ────────────────────────────────────


def match_page_level(
    system_pages: Mapping[PageKey, str],
    reference_pages: Mapping[PageKey, str],
) -> tuple[list[tuple[str, str]], list[ScopeMismatchError]]:
    """จับคู่ค่าระดับหน้าด้วย (document_id, page) หลัง NFC + strip.

    Returns:
        (matched_pairs, errors): คู่ที่จับได้ และ error ที่ขอบเขตไม่ตรง
    """
    matched: list[tuple[str, str]] = []
    errors: list[ScopeMismatchError] = []

    # Group reference pages by document_id
    ref_by_doc: dict[str, set[int]] = {}
    for key in reference_pages:
        ref_by_doc.setdefault(key.document_id, set()).add(key.page)

    sys_by_doc: dict[str, set[int]] = {}
    for key in system_pages:
        sys_by_doc.setdefault(key.document_id, set()).add(key.page)

    # Match only by exact key (document_id, page)
    for key, sys_text in system_pages.items():
        if key in reference_pages:
            ref_text = reference_pages[key]
            # Both texts are NFC normalized + stripped (R18.1)
            matched.append((
                _normalize_key_text(sys_text),
                _normalize_key_text(ref_text),
            ))

    return matched, errors


def match_field_level(
    system_fields: Mapping[FieldKey, str],
    reference_fields: Mapping[FieldKey, str],
) -> tuple[list[tuple[str, str]], list[ScopeMismatchError]]:
    """จับคู่ค่าระดับ field ด้วย (document_id, page, field_name) หลัง NFC + strip.

    Returns:
        (matched_pairs, errors): คู่ที่จับได้ และ error ที่ขอบเขตไม่ตรง
    """
    matched: list[tuple[str, str]] = []
    errors: list[ScopeMismatchError] = []

    for key, sys_text in system_fields.items():
        if key in reference_fields:
            ref_text = reference_fields[key]
            matched.append((
                _normalize_key_text(sys_text),
                _normalize_key_text(ref_text),
            ))

    return matched, errors


def validate_page_scope(
    metric_name: str,
    system_pages: set[PageKey],
    reference_pages: set[PageKey],
) -> list[ScopeMismatchError]:
    """ตรวจขอบเขตหน้า (R18.2): ถ้า document_id ตรงแต่ชุดหน้าไม่ตรง ให้ปฏิเสธ.

    Returns:
        list ของ ScopeMismatchError สำหรับ document ที่ขอบเขตหน้าไม่ตรง
    """
    errors: list[ScopeMismatchError] = []

    # Group pages by document_id
    sys_by_doc: dict[str, set[int]] = {}
    for key in system_pages:
        sys_by_doc.setdefault(key.document_id, set()).add(key.page)

    ref_by_doc: dict[str, set[int]] = {}
    for key in reference_pages:
        ref_by_doc.setdefault(key.document_id, set()).add(key.page)

    # Check overlapping documents
    for doc_id in sys_by_doc.keys() & ref_by_doc.keys():
        sys_page_set = sys_by_doc[doc_id]
        ref_page_set = ref_by_doc[doc_id]
        # Pages in system but not in reference (scope mismatch)
        sys_only = sys_page_set - ref_page_set
        ref_only = ref_page_set - sys_page_set
        if sys_only or ref_only:
            # Report one error per mismatched page pair
            for page in sorted(sys_only):
                errors.append(ScopeMismatchError(
                    metric=metric_name,
                    evaluated_document_id=doc_id,
                    evaluated_page=page,
                    reference_document_id=doc_id,
                    reference_page=min(ref_page_set) if ref_page_set else -1,
                    message=(
                        f"ขอบเขตหน้าไม่ตรง: system มีหน้า {page} ของ {doc_id} "
                        f"แต่ reference ไม่มี"
                    ),
                ))
            for page in sorted(ref_only):
                errors.append(ScopeMismatchError(
                    metric=metric_name,
                    evaluated_document_id=doc_id,
                    evaluated_page=min(sys_page_set) if sys_page_set else -1,
                    reference_document_id=doc_id,
                    reference_page=page,
                    message=(
                        f"ขอบเขตหน้าไม่ตรง: reference มีหน้า {page} ของ {doc_id} "
                        f"แต่ system ไม่มี"
                    ),
                ))

    return errors


# ── Status rules (R18.4, R18.5, R18.8) ───────────────────────────────


def _determine_status(
    samples: int,
    min_samples: int,
) -> str:
    """กำหนดสถานะ measured/estimate ตาม R18.4.

    - "measured": samples >= min_samples (30 from config)
    - "estimate": samples < min_samples
    """
    if samples >= min_samples:
        return "measured"
    return "estimate"


def _determine_pass_fail(
    value: float,
    threshold: float | None,
    status: str,
    metric_name: str,
) -> str | None:
    """กำหนด pass/fail ตาม R18.6/R18.8.

    - ห้ามระบุ pass เมื่อ status == "estimate" (R18.8)
    - ไม่ระบุ pass/fail เมื่อไม่มี threshold
    """
    if threshold is None:
        return None
    if status == "estimate":
        # R18.8: SHALL ไม่ระบุผลเทียบเกณฑ์เป็น "pass"
        return None

    # Determine comparison direction based on metric
    # page_cer and unsupported_claim_rate: lower is better (<=, <)
    lower_is_better = metric_name in ("page_cer", "unsupported_claim_rate")

    if lower_is_better:
        if metric_name == "unsupported_claim_rate":
            return "pass" if value < threshold else "fail"
        return "pass" if value <= threshold else "fail"
    else:
        return "pass" if value >= threshold else "fail"


def _condition_to_measured(
    metric_name: str,
    samples: int,
    min_samples: int,
    status: str,
) -> str | None:
    """ระบุเงื่อนไขเปลี่ยนสถานะเป็น measured (R18.5).

    คืน None เมื่อสถานะเป็น measured อยู่แล้ว.
    """
    if status == "measured":
        return None
    deficit = min_samples - samples
    return (
        f"ต้องเพิ่มตัวอย่างของ {metric_name} อีก {deficit} ตัวอย่าง "
        f"(มี {samples}, ต้องครบ {min_samples})"
    )


def _build_metric_result(
    name: str,
    value: float,
    samples: int,
    threshold: float | None,
    reference_source: str,
    min_samples: int,
) -> tuple[MetricResult, ReviewIssue | None]:
    """สร้าง MetricResult พร้อม review_issue ถ้า samples ไม่พอ."""
    status = _determine_status(samples, min_samples)
    pass_fail = _determine_pass_fail(value, threshold, status, name)
    condition = _condition_to_measured(name, samples, min_samples, status)

    result = MetricResult(
        name=name,
        value=round(value, _DECIMAL_PLACES),
        samples=samples,
        status=status,
        threshold=threshold,
        reference_source=reference_source,
        pass_fail=pass_fail,
        condition_to_measured=condition,
    )

    # R18.8: บันทึก metric_sample_insufficient เมื่อ samples < min_samples
    issue: ReviewIssue | None = None
    if status == "estimate":
        issue = ReviewIssue(
            kind="metric_sample_insufficient",
            metric=name,
            samples=samples,
            detail={"min_required": min_samples, "deficit": min_samples - samples},
        )

    return result, issue


# ── Reproducibility check (R18.7, R18.9) ─────────────────────────────


def check_reproducibility(
    current_report: EvaluationReport,
    previous_report_path: Path,
) -> list[MetricNotReproducibleError]:
    """ตรวจว่ารันซ้ำได้ค่าเท่าเดิม (R18.7, R18.9).

    เปรียบเทียบ metric values ระหว่าง current report กับ report ก่อนหน้า.
    ค่า metric ต้องเท่ากันทุกหลักที่รายงาน (ทศนิยม 4 ตำแหน่ง).
    timestamp และ runtime อนุญาตให้ต่างกันได้ (R18.7).

    Returns:
        list ของ MetricNotReproducibleError สำหรับ metric ที่ค่าต่างกัน
    """
    errors: list[MetricNotReproducibleError] = []

    if not previous_report_path.exists():
        return errors

    try:
        with previous_report_path.open("r", encoding="utf-8") as f:
            previous_data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("อ่าน report ก่อนหน้าไม่สำเร็จ: %s", exc)
        return errors

    # Build lookup of previous metric values
    prev_metrics: dict[str, float] = {}
    for m in previous_data.get("metrics", []):
        if isinstance(m, dict) and "name" in m and "value" in m:
            prev_metrics[m["name"]] = m["value"]

    # Compare current metrics with previous
    for metric in current_report.metrics:
        if metric.name in prev_metrics:
            prev_value = round(prev_metrics[metric.name], _DECIMAL_PLACES)
            curr_value = round(metric.value, _DECIMAL_PLACES)
            if prev_value != curr_value:
                errors.append(MetricNotReproducibleError(
                    metric=metric.name,
                    previous=prev_value,
                    current=curr_value,
                ))

    return errors


# ── Main Evaluation Harness ───────────────────────────────────────────


@dataclass(slots=True)
class MetricInput:
    """Input data for computing a single metric."""

    name: str
    value: float
    samples: int
    threshold: float | None
    reference_source: str


class EvaluationHarness:
    """Evaluation_Harness: คำนวณ metric ทุกตัวและออก evaluation report (R18).

    Lifecycle:
        1. สร้าง instance ด้วย EvaluationConfig
        2. เรียก compute_metric() หรือ compute_all() เพื่อคำนวณ
        3. เรียก produce_report() เพื่อสร้าง evaluation report
        4. เรียก save_report() เพื่อเขียน JSON
    """

    def __init__(self, config: EvaluationConfig) -> None:
        self._config = config
        self._min_samples = config.min_samples_for_measured

    @property
    def min_samples_for_measured(self) -> int:
        return self._min_samples


    def compute_metric(
        self,
        name: str,
        value: float,
        samples: int,
        threshold: float | None,
        reference_source: str,
    ) -> tuple[MetricResult, ReviewIssue | None]:
        """คำนวณ metric หนึ่งตัวพร้อมกำหนดสถานะ (R18.3, R18.4, R18.8).

        Args:
            name: ชื่อ metric
            value: ค่า metric ที่คำนวณได้
            samples: จำนวนตัวอย่างที่ใช้
            threshold: เกณฑ์ยอมรับ (None ถ้าไม่มี)
            reference_source: แหล่งอ้างอิง ("teacher_ground_truth" | "gold_set")

        Returns:
            (MetricResult, ReviewIssue or None)
        """
        return _build_metric_result(
            name=name,
            value=value,
            samples=samples,
            threshold=threshold,
            reference_source=reference_source,
            min_samples=self._min_samples,
        )


    def compute_all(
        self,
        inputs: Sequence[MetricInput],
    ) -> tuple[list[MetricResult], list[ReviewIssue]]:
        """คำนวณ metric หลายตัวพร้อมกัน.

        Args:
            inputs: ลำดับ MetricInput ที่ต้องการคำนวณ

        Returns:
            (results, review_issues)
        """
        results: list[MetricResult] = []
        issues: list[ReviewIssue] = []
        for inp in inputs:
            result, issue = self.compute_metric(
                name=inp.name,
                value=inp.value,
                samples=inp.samples,
                threshold=inp.threshold,
                reference_source=inp.reference_source,
            )
            results.append(result)
            if issue is not None:
                issues.append(issue)
        return results, issues


    def produce_report(
        self,
        metric_inputs: Sequence[MetricInput],
        scope_errors: Sequence[ScopeMismatchError] | None = None,
    ) -> EvaluationReport:
        """ผลิต evaluation report ที่สมบูรณ์ (R18.3).

        รวม:
        - metric ทุกตัวพร้อมค่า, samples, status, threshold
        - commit_id (R18.3)
        - timestamp ISO 8601 (R18.3)
        - scope mismatch errors (R18.2)
        - review_issues (R18.8)
        """
        results, issues = self.compute_all(metric_inputs)
        report = EvaluationReport(
            metrics=results,
            errors=list(scope_errors) if scope_errors else [],
            review_issues=issues,
            commit_id=_get_commit_id(),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        return report


    def save_report(
        self,
        report: EvaluationReport,
        output_path: Path,
    ) -> Path:
        """เขียน evaluation report เป็น JSON (R18.3).

        ค่า metric ปัดทศนิยม 4 ตำแหน่ง.
        ใช้ ensure_ascii=False เพื่อรองรับข้อความไทย.

        Returns:
            Path ของไฟล์ที่เขียน
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        data = report.to_dict()
        with output_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info("เขียน evaluation report: %s", output_path)
        return output_path


    def run_with_reproducibility_check(
        self,
        metric_inputs: Sequence[MetricInput],
        scope_errors: Sequence[ScopeMismatchError] | None = None,
        output_path: Path | None = None,
    ) -> tuple[EvaluationReport, list[MetricNotReproducibleError]]:
        """รัน evaluation พร้อมตรวจ reproducibility (R18.7, R18.9).

        ถ้ามี report ก่อนหน้าอยู่แล้ว จะเปรียบเทียบค่า:
        - ถ้าค่าต่างกัน → คืน error + คง report เดิมไว้ (R18.9)
        - ถ้าค่าเท่ากัน → เขียน report ใหม่ (timestamp อัปเดต)

        Args:
            metric_inputs: ข้อมูล metric ที่จะคำนวณ
            scope_errors: errors จากการตรวจขอบเขตหน้า
            output_path: path ที่จะเขียน report (ถ้า None จะไม่เขียน)

        Returns:
            (report, reproducibility_errors)
        """
        report = self.produce_report(metric_inputs, scope_errors)

        repro_errors: list[MetricNotReproducibleError] = []
        if output_path is not None and output_path.exists():
            repro_errors = check_reproducibility(report, output_path)

        if repro_errors:
            # R18.9: คืน error + คง report เดิมไว้ ไม่เขียนทับ
            for err in repro_errors:
                logger.error(
                    "Reproducibility error: %s — previous=%.4f, current=%.4f",
                    err.metric,
                    err.context.get("previous", 0.0),
                    err.context.get("current", 0.0),
                )
            # Mark affected metrics as estimate
            for i, m in enumerate(report.metrics):
                for err in repro_errors:
                    if m.name == err.metric:
                        report.metrics[i] = MetricResult(
                            name=m.name,
                            value=m.value,
                            samples=m.samples,
                            status="estimate",
                            threshold=m.threshold,
                            reference_source=m.reference_source,
                            pass_fail=None,
                            condition_to_measured=m.condition_to_measured,
                        )
            return report, repro_errors

        # No reproducibility issues — save new report
        if output_path is not None:
            self.save_report(report, output_path)

        return report, repro_errors


    def evaluate_page_cer(
        self,
        system_pages: Mapping[PageKey, str],
        reference_pages: Mapping[PageKey, str],
        reference_source: str = "gold_set",
    ) -> tuple[MetricResult | None, list[ScopeMismatchError], ReviewIssue | None]:
        """คำนวณ page CER metric พร้อมตรวจขอบเขต (R3.8, R18.1, R18.2).

        Returns:
            (result, scope_errors, review_issue)
            result is None when scope mismatch prevents computation.
        """
        from katrag.eval.metrics import mean_page_cer

        # Validate page scope (R18.2)
        scope_errors = validate_page_scope(
            "page_cer",
            set(system_pages.keys()),
            set(reference_pages.keys()),
        )
        if scope_errors:
            return None, scope_errors, None

        # Match and compute (R18.1)
        matched, _ = match_page_level(system_pages, reference_pages)
        if not matched:
            result, issue = _build_metric_result(
                "page_cer", 0.0, 0,
                self._config.page_cer_threshold,
                reference_source, self._min_samples,
            )
            return result, [], issue

        value = mean_page_cer(matched)
        result, issue = _build_metric_result(
            "page_cer", value, len(matched),
            self._config.page_cer_threshold,
            reference_source, self._min_samples,
        )
        return result, [], issue


    def evaluate_field_macro_f1(
        self,
        system_fields: Mapping[FieldKey, str],
        reference_fields: Mapping[FieldKey, str],
        reference_source: str = "teacher_ground_truth",
    ) -> tuple[MetricResult | None, list[ScopeMismatchError], ReviewIssue | None]:
        """คำนวณ field macro-F1 metric (R8.8, R18.1).

        Returns:
            (result, scope_errors, review_issue)
        """
        from katrag.eval.metrics import field_precision_recall_f1, field_macro_f1

        # Match by (document_id, page, field_name)
        matched, _ = match_field_level(system_fields, reference_fields)
        if not matched:
            result, issue = _build_metric_result(
                "field_macro_f1", 0.0, 0,
                self._config.field_macro_f1_threshold,
                reference_source, self._min_samples,
            )
            return result, [], issue

        # Group matched pairs by field_name for per-field F1
        field_groups: dict[str, list[tuple[str, str]]] = {}
        for key, sys_text in system_fields.items():
            if key in reference_fields:
                ref_text = reference_fields[key]
                field_groups.setdefault(key.field_name, []).append((
                    _normalize_key_text(sys_text),
                    _normalize_key_text(ref_text),
                ))

        per_field_results = []
        for _field_name, pairs in field_groups.items():
            sys_vals = [p[0] for p in pairs]
            ref_vals = [p[1] for p in pairs]
            prf = field_precision_recall_f1(sys_vals, ref_vals)
            per_field_results.append(prf)

        macro_f1 = field_macro_f1(per_field_results)
        total_samples = len(matched)
        result, issue = _build_metric_result(
            "field_macro_f1", macro_f1, total_samples,
            self._config.field_macro_f1_threshold,
            reference_source, self._min_samples,
        )
        return result, [], issue


    def get_thresholds(self) -> dict[str, float | None]:
        """คืน mapping ของชื่อ metric กับ threshold (R18.6)."""
        return {
            "page_cer": self._config.page_cer_threshold,
            "table_cell_f1": self._config.table_cell_f1_threshold,
            "field_macro_f1": self._config.field_macro_f1_threshold,
            "recall_at_10": self._config.recall_at_10_threshold,
            "citation_page_precision": self._config.citation_precision_threshold,
            "citation_page_recall": self._config.citation_recall_threshold,
            "unsupported_claim_rate": self._config.unsupported_claim_rate_threshold,
            "version_selection_accuracy": self._config.version_selection_accuracy_threshold,
        }
