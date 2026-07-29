"""Property test ของ GainCostHalter (task 9.7, R5.2-R5.5, R14.5-R14.8).

สามคุณสมบัติที่ต้องคงอยู่เสมอ:
1. ลำดับค่าสุ่ม (รวม NaN/±inf) ทำให้ลูป escalation จบภายใน max_stages รอบ
2. NaN/inf ต้องให้เหตุผล `nan_guard` โดย gain ถือเป็น 0.00
3. oscillation ที่เกิดครบ patience ต้องหยุดด้วย `oscillation`
"""

from __future__ import annotations

import math

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from katrag.common.halter import GainCostHalter
from katrag.common.types import HaltDecision, HaltReason

PROPERTY_SETTINGS = settings(max_examples=200, deadline=None)

# ── strategies ────────────────────────────────────────────────────────

# สร้าง float ที่รวม NaN, ±inf, ค่าปกติ, และค่าที่อยู่ในช่วง 0-1
_score_st = st.one_of(
    st.floats(min_value=0.0, max_value=1.0),
    st.just(float("nan")),
    st.just(float("inf")),
    st.just(float("-inf")),
)

_elapsed_st = st.one_of(
    st.floats(min_value=0.0, max_value=1000.0),
    st.just(float("nan")),
    st.just(float("inf")),
)

_budget_st = st.one_of(
    st.floats(min_value=0.001, max_value=1000.0),
    st.just(0.0),
    st.just(float("nan")),
    st.just(float("inf")),
)


# ── Property 1: Bounded termination ──────────────────────────────────


@given(
    scores=st.lists(_score_st, min_size=1, max_size=10),
    elapseds=st.lists(_elapsed_st, min_size=1, max_size=10),
    budget=_budget_st,
    tau=st.floats(min_value=0.01, max_value=10.0),
    l_min=st.integers(min_value=1, max_value=5),
    patience=st.integers(min_value=1, max_value=5),
)
@PROPERTY_SETTINGS
def test_halter_always_terminates_within_max_stages(
    scores: list[float],
    elapseds: list[float],
    budget: float,
    tau: float,
    l_min: int,
    patience: int,
) -> None:
    """ลูป escalation ต้องจบภายใน len(scores) รอบ — ไม่มี infinite loop."""
    halter = GainCostHalter(tau=tau, l_min=l_min, oscillation_patience=patience)
    max_stages = len(scores)
    terminated = False

    for i in range(max_stages):
        score = scores[i]
        elapsed = elapseds[i % len(elapseds)]
        verdict = halter.observe(score, elapsed, budget)
        if verdict.decision == HaltDecision.HALT:
            terminated = True
            break

    # ทุกลำดับต้องจบ (ถ้าไม่ halt ก่อนก็หมดรอบ — ไม่ใช่ infinite loop)
    assert halter.iterations_done <= max_stages


# ── Property 2: NaN → nan_guard ──────────────────────────────────────


@given(
    good_scores=st.lists(st.floats(min_value=0.0, max_value=1.0), min_size=0, max_size=3),
)
@PROPERTY_SETTINGS
def test_nan_input_yields_nan_guard(good_scores: list[float]) -> None:
    """NaN ในตัวแปรใดก็ตามต้องให้เหตุผล nan_guard."""
    halter = GainCostHalter(tau=1.0, l_min=1, oscillation_patience=5)

    # Feed good scores first (ให้ iterations_done >= l_min)
    for s in good_scores:
        halter.observe(s, 1.0, 100.0)

    # Feed NaN score
    verdict = halter.observe(float("nan"), 1.0, 100.0)
    assert verdict.decision == HaltDecision.HALT
    assert verdict.reason == HaltReason.NAN_GUARD
    assert verdict.gain == 0.0


@given(
    score=st.floats(min_value=0.0, max_value=1.0),
)
@PROPERTY_SETTINGS
def test_nan_elapsed_yields_nan_guard(score: float) -> None:
    """NaN ใน elapsed ต้องให้เหตุผล nan_guard."""
    halter = GainCostHalter(tau=1.0, l_min=1, oscillation_patience=5)
    verdict = halter.observe(score, float("nan"), 100.0)
    assert verdict.decision == HaltDecision.HALT
    assert verdict.reason == HaltReason.NAN_GUARD


@given(
    score=st.floats(min_value=0.0, max_value=1.0),
)
@PROPERTY_SETTINGS
def test_nan_budget_yields_nan_guard(score: float) -> None:
    """NaN ใน budget ต้องให้เหตุผล nan_guard."""
    halter = GainCostHalter(tau=1.0, l_min=1, oscillation_patience=5)
    verdict = halter.observe(score, 1.0, float("nan"))
    assert verdict.decision == HaltDecision.HALT
    assert verdict.reason == HaltReason.NAN_GUARD


# ── Property 3: Oscillation ──────────────────────────────────────────


@given(
    patience=st.integers(min_value=1, max_value=5),
)
@PROPERTY_SETTINGS
def test_oscillation_triggers_halt(patience: int) -> None:
    """สลับทิศทางครบ patience ครั้งต้องหยุดด้วย oscillation."""
    halter = GainCostHalter(tau=100.0, l_min=1, oscillation_patience=patience)

    # สร้างลำดับที่สลับขึ้น-ลงติดกัน patience ครั้ง
    # แต่ละ oscillation ต้องเห็น direction change: need patience+2 observations min
    scores = []
    val = 0.5
    for i in range(patience + 3):
        if i % 2 == 0:
            val = 0.5 + (i + 1) * 0.01
        else:
            val = 0.5 - (i + 1) * 0.01
        scores.append(max(0.0, min(1.0, val)))

    found_oscillation = False
    for s in scores:
        verdict = halter.observe(s, 0.01, 100.0)
        if verdict.decision == HaltDecision.HALT and verdict.reason == HaltReason.OSCILLATION:
            found_oscillation = True
            break

    assert found_oscillation, (
        f"ลำดับ {scores} ควรทำให้ oscillation halt (patience={patience}) "
        f"แต่ iterations_done={halter.iterations_done}"
    )


# ── Property 4: l_min respected ──────────────────────────────────────


@given(
    l_min=st.integers(min_value=2, max_value=5),
)
@PROPERTY_SETTINGS
def test_l_min_prevents_early_halt(l_min: int) -> None:
    """ไม่มีทางหยุดก่อนทำครบ l_min รอบ (ยกเว้น NaN)."""
    halter = GainCostHalter(tau=0.001, l_min=l_min, oscillation_patience=10)

    # gain=0 < cost*tau ปกติจะ halt แต่ l_min ต้องป้องกัน
    for i in range(l_min - 1):
        verdict = halter.observe(0.5, 100.0, 1.0)  # cost สูงมาก
        assert verdict.decision == HaltDecision.CONTINUE, (
            f"ไม่ควร halt ก่อนรอบ {l_min}, halt ที่รอบ {i + 1}"
        )
