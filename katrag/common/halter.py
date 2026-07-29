"""Gain_Cost_Halter — เกณฑ์หยุดแบบ gain/cost ที่ใช้ร่วมกันสองที่.

ใช้ที่ 1: OCR escalation ระหว่าง stage (R5.2-R5.5)
ใช้ที่ 2: evidence hops ของ Evidence_Planner (R14.5-R14.8)

อัลกอริทึมนี้เขียนใหม่เป็น Python จากแนวคิดใน `katgpt-rs`
(`crates/katgpt-core/src/gain_cost_halt.rs`) โดยเปลี่ยนสัญญาณจาก hidden-state
เป็นคะแนนคุณภาพ OCR และ evidence coverage — ดู `third_party/katgpt-rs-MIT-NOTICE.md`
ไม่มีการ import จาก `katgpt-rs/` (R20.4, R20.5)

กฎการตัดสิน (ลำดับสำคัญ):
1. `iterations_done < l_min`  -> CONTINUE เสมอ (ไม่อนุญาตให้หยุดก่อนพื้น)
2. gain หรือ cost เป็น NaN/inf -> HALT เหตุผล `nan_guard` โดยถือ gain = 0.00
3. ทิศทางคะแนนสลับครบ patience -> HALT เหตุผล `oscillation`
4. `gain < cost * tau`         -> HALT เหตุผล `gain_below_cost`
5. อื่น ๆ                      -> CONTINUE
"""

from __future__ import annotations

import math

from katrag.common.types import HaltDecision, HaltReason, HaltVerdict


class GainCostHalter:
    """ตัวตัดสินหยุด/ไปต่อ ที่มี state ต่อหนึ่งลำดับงาน (หนึ่งหน้า หรือหนึ่งคำขอ).

    ผู้เรียกต้องสร้างใหม่หรือเรียก `reset()` เมื่อเริ่มลำดับงานใหม่
    """

    __slots__ = (
        "_tau",
        "_l_min",
        "_oscillation_patience",
        "_best_score",
        "_last_score",
        "_last_direction",
        "_oscillation_count",
        "_iterations_done",
    )

    def __init__(
        self,
        tau: float = 1.0,
        l_min: int = 1,
        oscillation_patience: int = 2,
    ) -> None:
        if tau <= 0.0:
            raise ValueError("tau ต้องมากกว่า 0")
        if l_min < 1:
            raise ValueError("l_min ต้องไม่น้อยกว่า 1")
        if oscillation_patience < 1:
            raise ValueError("oscillation_patience ต้องไม่น้อยกว่า 1")
        self._tau = tau
        self._l_min = l_min
        self._oscillation_patience = oscillation_patience
        self._best_score = 0.0
        self._last_score: float | None = None
        self._last_direction = 0
        self._oscillation_count = 0
        self._iterations_done = 0

    # ── properties ───────────────────────────────────────────────────

    @property
    def iterations_done(self) -> int:
        return self._iterations_done

    @property
    def best_score(self) -> float:
        return self._best_score

    # ── core ─────────────────────────────────────────────────────────

    def observe(self, score: float, elapsed_s: float, budget_s: float) -> HaltVerdict:
        """บันทึกผลของรอบล่าสุดและตัดสินว่าจะทำรอบถัดไปหรือไม่.

        Args:
            score: คะแนนคุณภาพของรอบล่าสุดในสเกล 0.00-1.00
                   (OCR: คะแนนคุณภาพของ region / evidence: coverage score)
            elapsed_s: เวลาที่รอบล่าสุดใช้ (วินาที)
            budget_s: งบเวลาต่อหน่วยงาน (วินาที) — ต้องมากกว่า 0

        Returns:
            HaltVerdict ที่บอกคำตัดสิน เหตุผล ค่า gain/cost และจำนวนรอบที่ทำแล้ว

        ค่า NaN หรือ infinity ในทุก argument ถือเป็นสัญญาณเสีย: ถือ gain = 0.00
        แล้วหยุดด้วยเหตุผล `nan_guard` โดยผลของรอบที่สำเร็จยังคงอยู่ (R5.4, R14.8)
        """
        self._iterations_done += 1

        if not _is_finite(score) or not _is_finite(elapsed_s) or not _is_finite(budget_s):
            return self._verdict(HaltDecision.HALT, HaltReason.NAN_GUARD, 0.0, 0.0)

        gain = score - self._best_score
        cost = _safe_cost(elapsed_s, budget_s)

        if not _is_finite(gain) or not _is_finite(cost):
            return self._verdict(HaltDecision.HALT, HaltReason.NAN_GUARD, 0.0, 0.0)

        self._track_direction(score)
        if score > self._best_score:
            self._best_score = score
        self._last_score = score

        # พื้นล่าง: ห้ามหยุดก่อนทำครบ l_min รอบ
        if self._iterations_done < self._l_min:
            return self._verdict(HaltDecision.CONTINUE, None, gain, cost)

        if self._oscillation_count >= self._oscillation_patience:
            return self._verdict(HaltDecision.HALT, HaltReason.OSCILLATION, gain, cost)

        if gain < cost * self._tau:
            return self._verdict(HaltDecision.HALT, HaltReason.GAIN_BELOW_COST, gain, cost)

        return self._verdict(HaltDecision.CONTINUE, None, gain, cost)

    def halt_now(self, reason: HaltReason) -> HaltVerdict:
        """สร้างคำตัดสินหยุดจากเหตุผลภายนอก.

        ใช้กับกรณีที่ผู้เรียกตรวจพบเอง เช่น `max_hops_reached`,
        `no_new_evidence` หรือ `time_budget_exceeded` (R14.4, R14.7, R14.9)
        """
        return self._verdict(HaltDecision.HALT, reason, 0.0, 0.0)

    def reset(self) -> None:
        """ล้าง state สำหรับเริ่มลำดับงานใหม่ (หน้าถัดไป หรือคำขอถัดไป)."""
        self._best_score = 0.0
        self._last_score = None
        self._last_direction = 0
        self._oscillation_count = 0
        self._iterations_done = 0

    # ── internals ────────────────────────────────────────────────────

    def _track_direction(self, score: float) -> None:
        """นับจำนวนครั้งที่ทิศทางคะแนนสลับ (เพิ่มสลับลด) (R5.3)."""
        if self._last_score is None:
            return
        delta = score - self._last_score
        direction = 0 if delta == 0.0 else (1 if delta > 0.0 else -1)
        if direction == 0:
            return
        if self._last_direction != 0 and direction != self._last_direction:
            self._oscillation_count += 1
        self._last_direction = direction

    def _verdict(
        self,
        decision: HaltDecision,
        reason: HaltReason | None,
        gain: float,
        cost: float,
    ) -> HaltVerdict:
        return HaltVerdict(
            decision=decision,
            reason=reason,
            gain=gain,
            cost=cost,
            iterations_done=self._iterations_done,
        )


def _is_finite(value: float) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value)


def _safe_cost(elapsed_s: float, budget_s: float) -> float:
    """cost = elapsed / budget โดยกันการหารศูนย์.

    budget <= 0 ถือว่าไม่มีงบเหลือ จึงให้ cost สูงสุด (บังคับให้หยุด)
    """
    if budget_s <= 0.0:
        return math.inf if elapsed_s > 0.0 else 0.0
    return elapsed_s / budget_s
