"""`OcrCascade` — orchestrate stage 1 → stage 2 พร้อม escalation budget/timeout (design §4.9).

รับผิดชอบ:
1. เรียก stage ตามลำดับคงที่ (Tesseract → Typhoon) พร้อม per-engine hard timeout
2. selective escalation — ไม่เรียก stage 2 เมื่อ stage 1 คุณภาพดีพอ / budget หมด / circuit-breaker เปิด
3. จัดการ engine error/timeout → บันทึก error_record + ใช้ผลที่สำเร็จ หรือ mark `ocr_failed`
4. crop cache hit → คืนผลเดิมไม่เรียก engine ซ้ำ
5. ข้าม stage 2 โดยอัตโนมัติเมื่อไม่มี CUDA (R5.1.1)
6. เรียก GainCostHalter หลังจบแต่ละ stage — halt → เลือกผลดีที่สุด (R5.2-R5.5)
7. Preprocessor + before/after comparison (R5.7-R5.9) [optional injection]
8. RegionAdjudicator สำหรับ spatial voting (R5.10) [optional injection]
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

from katrag.common.halter import GainCostHalter
from katrag.common.types import BBox, HaltDecision
from katrag.config import EscalationConfig, OcrConfig, StageTimeoutConfig
from katrag.errors import OcrEngineError
from katrag.ingest.ocr.adjudicator import RegionAdjudicator
from katrag.ingest.ocr.crop_cache import CropCache, CropCacheKey
from katrag.ingest.ocr.preprocessor import Preprocessor, PreprocessOutcome
from katrag.ingest.ocr.stage import OcrStage, StageResult

logger = logging.getLogger(__name__)


# ── Result types ──────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ErrorRecord:
    """บันทึกข้อผิดพลาดของ stage หนึ่งต่อ region หนึ่ง (R5.6)."""

    engine: str
    reason: str  # "engine_timeout" | "engine_error" | "hallucinated_institution_name" | "budget_exhausted"
    detail: str
    elapsed_ms: int


@dataclass(frozen=True, slots=True)
class RegionOutcome:
    """ผลรวมของ cascade ต่อ region หนึ่ง."""

    best_result: StageResult | None  # None เมื่อ mark ocr_failed
    stage_results: tuple[StageResult, ...]
    error_records: tuple[ErrorRecord, ...]
    stages_executed: int
    halted: bool
    halt_reason: str | None  # "gain_cost" | "gain_below_cost" | "oscillation" | "nan_guard" | "budget_exhausted" | "circuit_breaker" | "quality_sufficient" | None
    ocr_failed: bool
    preprocess_steps: tuple[str, ...] = ()
    gain: float = 0.0
    cost: float = 0.0


# ── Escalation Gate (pre-stage 2 decision) ────────────────────────────


@dataclass(slots=True)
class EscalationTracker:
    """ติดตาม budget/circuit-breaker ของ Typhoon ตลอด ingestion run."""

    config: EscalationConfig
    _elapsed_typhoon_seconds: float = 0.0
    _consecutive_failures: int = 0
    _circuit_open: bool = False

    @property
    def circuit_open(self) -> bool:
        return self._circuit_open

    @property
    def budget_remaining(self) -> float:
        return max(0.0, self.config.max_typhoon_seconds_per_run - self._elapsed_typhoon_seconds)

    def should_escalate(self, stage1_quality: float) -> tuple[bool, str | None]:
        """ตัดสินใจว่าจะเรียก Typhoon หรือไม่ — คืน (escalate?, skip_reason)."""
        if self._circuit_open:
            return False, "circuit_breaker"
        if self._elapsed_typhoon_seconds >= self.config.max_typhoon_seconds_per_run:
            return False, "budget_exhausted"
        if stage1_quality >= self.config.min_stage1_quality_for_skip:
            return False, "quality_sufficient"
        return True, None

    def record_success(self, elapsed_seconds: float) -> None:
        self._elapsed_typhoon_seconds += elapsed_seconds
        self._consecutive_failures = 0

    def record_failure(self, elapsed_seconds: float) -> None:
        self._elapsed_typhoon_seconds += elapsed_seconds
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.config.max_consecutive_typhoon_failures:
            self._circuit_open = True
            logger.warning(
                "Typhoon circuit breaker เปิด: ล้มเหลวติดต่อกัน %d ครั้ง",
                self._consecutive_failures,
            )


# ── Main Cascade ──────────────────────────────────────────────────────


class OcrCascade:
    """Orchestrate OCR stages ตามลำดับคงที่พร้อม escalation control + halter."""

    def __init__(
        self,
        stages: Sequence[OcrStage],
        config: OcrConfig,
        cache: CropCache,
        *,
        has_cuda: bool = False,
        preprocessor: Preprocessor | None = None,
        adjudicator: RegionAdjudicator | None = None,
    ) -> None:
        if not stages:
            raise ValueError("ต้องมีอย่างน้อย 1 stage")
        self._stages = list(stages)
        self._config = config
        self._cache = cache
        self._has_cuda = has_cuda
        self._escalation = EscalationTracker(config=config.escalation)
        self._preprocessor = preprocessor
        self._adjudicator = adjudicator

    @property
    def escalation(self) -> EscalationTracker:
        return self._escalation

    def _make_halter(self) -> GainCostHalter:
        """สร้าง halter ใหม่สำหรับแต่ละ region (reset state ทุกครั้ง)."""
        # อ่าน tau/l_min/oscillation_patience จาก config ต้น (ผ่าน parent config)
        # แต่เนื่องจาก OcrConfig ไม่ hold HaltConfig โดยตรง เราใช้ค่าเริ่มต้นตรง ๆ
        # cascade ที่ระดับ production จะได้รับ HaltConfig จาก KatragConfig
        return GainCostHalter(tau=1.0, l_min=1, oscillation_patience=2)

    def run_region(
        self,
        image: np.ndarray,
        region: BBox,
        *,
        preprocess_steps: tuple[str, ...] = (),
        crop_sha256: str | None = None,
        halter: GainCostHalter | None = None,
    ) -> RegionOutcome:
        """รัน cascade สำหรับ region เดียว — คืน RegionOutcome.

        Args:
            halter: GainCostHalter instance — ถ้าไม่ส่งจะสร้างใหม่ด้วยค่าเริ่มต้น
        """
        results: list[StageResult] = []
        errors: list[ErrorRecord] = []
        halt_reason: str | None = None
        last_gain = 0.0
        last_cost = 0.0

        _halter = halter or self._make_halter()

        # ── Preprocessing (R5.7-R5.9) ──
        actual_preprocess_steps = preprocess_steps
        processed_image = image
        if self._preprocessor is not None and not preprocess_steps:
            outcome = self._preprocessor.apply(image, region)
            actual_preprocess_steps = outcome.applied_steps
            processed_image = outcome.image

        for stage in self._stages:
            engine_name = stage.name

            # ── GPU gate: ข้าม Typhoon ถ้าไม่มี CUDA (R5.1.1) ──
            if engine_name == "typhoon_ocr1_5_2b" and not self._has_cuda:
                logger.debug("ข้าม %s: ไม่มี CUDA", engine_name)
                continue

            # ── cache hit ──
            if crop_sha256 is not None:
                cache_key = self._cache.make_key(crop_sha256, engine_name, actual_preprocess_steps)
                cached = self._cache.get(cache_key)
                if cached is not None:
                    results.append(cached)
                    # halter observe for cached results too (คะแนนเดิม, elapsed=0)
                    verdict = _halter.observe(cached.quality_score, 0.0, self._config.per_page_time_budget_seconds)
                    last_gain, last_cost = verdict.gain, verdict.cost
                    if verdict.decision == HaltDecision.HALT:
                        halt_reason = verdict.reason.value if verdict.reason else "gain_below_cost"
                        break
                    continue
            else:
                cache_key = None

            # ── selective escalation (stage 2 only) ──
            if engine_name == "typhoon_ocr1_5_2b" and results:
                best_so_far = max(results, key=lambda r: r.quality_score)
                should, skip_reason = self._escalation.should_escalate(best_so_far.quality_score)
                if not should:
                    halt_reason = skip_reason
                    break

            # ── run stage with timeout ──
            timeout_s = self._config.timeout_for(engine_name)
            start = time.perf_counter()
            try:
                result = stage.recognize(processed_image, region, timeout_s)
            except OcrEngineError as exc:
                elapsed_ms = int((time.perf_counter() - start) * 1000)
                reason = "engine_timeout" if "timeout" in str(exc).lower() else "engine_error"
                errors.append(
                    ErrorRecord(
                        engine=engine_name,
                        reason=reason,
                        detail=str(exc),
                        elapsed_ms=elapsed_ms,
                    )
                )
                if engine_name == "typhoon_ocr1_5_2b":
                    self._escalation.record_failure((time.perf_counter() - start))
                continue
            except Exception as exc:  # pragma: no cover — unexpected
                elapsed_ms = int((time.perf_counter() - start) * 1000)
                errors.append(
                    ErrorRecord(
                        engine=engine_name,
                        reason="engine_error",
                        detail=f"{type(exc).__name__}: {exc}",
                        elapsed_ms=elapsed_ms,
                    )
                )
                if engine_name == "typhoon_ocr1_5_2b":
                    self._escalation.record_failure((time.perf_counter() - start))
                continue

            # ── success ──
            results.append(result)
            if cache_key is not None:
                self._cache.put(cache_key, result)
            if engine_name == "typhoon_ocr1_5_2b":
                self._escalation.record_success(result.elapsed_ms / 1000.0)

            # ── halter observe (R5.2-R5.5) ──
            verdict = _halter.observe(
                result.quality_score,
                result.elapsed_ms / 1000.0,
                self._config.per_page_time_budget_seconds,
            )
            last_gain, last_cost = verdict.gain, verdict.cost
            if verdict.decision == HaltDecision.HALT:
                halt_reason = verdict.reason.value if verdict.reason else "gain_below_cost"
                break

        # ── Preprocessing comparison (R5.8): เลือกก่อนปรับถ้าคะแนนเท่า ──
        # (ทำถ้า preprocessor ถูกใช้และมีผล — ยังไม่ implement เปรียบเทียบ before/after
        #  เพราะต้องรัน cascade ซ้ำ 2 ครั้ง; สำหรับ v1 ใช้ preprocessed image ตรง ๆ)

        # ── adjudication (R5.10) ──
        if self._adjudicator is not None and len(results) > 1:
            adj = self._adjudicator.adjudicate(
                results, self._config.adjudicate_iou_threshold, self._config.confidence_tie_epsilon
            )
            best = adj.chosen
        elif results:
            best = max(results, key=lambda r: r.quality_score)
        else:
            best = None

        # ── choose best result ──
        if best is None:
            return RegionOutcome(
                best_result=None,
                stage_results=tuple(results),
                error_records=tuple(errors),
                stages_executed=len(results) + len(errors),
                halted=halt_reason is not None,
                halt_reason=halt_reason,
                ocr_failed=True,
                preprocess_steps=actual_preprocess_steps,
                gain=last_gain,
                cost=last_cost,
            )

        return RegionOutcome(
            best_result=best,
            stage_results=tuple(results),
            error_records=tuple(errors),
            stages_executed=len(results) + len(errors),
            halted=halt_reason is not None,
            halt_reason=halt_reason,
            ocr_failed=False,
            preprocess_steps=actual_preprocess_steps,
            gain=last_gain,
            cost=last_cost,
        )

    def run_page(
        self,
        page_image: np.ndarray,
        regions: Sequence[BBox],
        *,
        preprocess_steps: tuple[str, ...] = (),
        crop_sha256s: Sequence[str | None] | None = None,
        halt_config: tuple[float, int, int] | None = None,
    ) -> tuple[RegionOutcome, ...]:
        """รัน cascade สำหรับทุก region ในหน้า — ไม่หยุดเมื่อ region หนึ่งล้มเหลว.

        Args:
            halt_config: (tau, l_min, oscillation_patience) — ถ้าไม่ส่งใช้ค่าเริ่มต้น
        """
        sha256s = crop_sha256s or [None] * len(regions)
        outcomes: list[RegionOutcome] = []
        tau, l_min, osc = halt_config or (1.0, 1, 2)
        for region, sha in zip(regions, sha256s):
            halter = GainCostHalter(tau=tau, l_min=l_min, oscillation_patience=osc)
            outcome = self.run_region(
                page_image, region,
                preprocess_steps=preprocess_steps,
                crop_sha256=sha,
                halter=halter,
            )
            outcomes.append(outcome)
        return tuple(outcomes)
