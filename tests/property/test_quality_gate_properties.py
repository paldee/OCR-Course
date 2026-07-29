"""Property test ของ Page_Quality_Gate และ Ocr_Page_Router (design Property 17, R4.2, R4.7).

Property 17: `page_quality_score` และ compute path ต้อง deterministic ต่อ input เดิม
และ `Ocr_Page_Router.route()` ต้องเป็น total function (มีค่าเดียวเสมอ ไม่มีกรณีตกหลุด)
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from katrag.common.types import ComputePath, PageCharSet
from katrag.config import PageQualityConfig, PageRouteConfig
from katrag.ingest.page_router import OcrPageRouter
from katrag.ingest.quality_gate import DeclaredCharset, DomainLexicon, PageQualityGate

CHARSET = DeclaredCharset(
    thai_range=("\u0e00", "\u0e7f"),
    latin_ranges=(("A", "Z"), ("a", "z")),
    digit_range=("0", "9"),
    punctuation=frozenset(" \t\n.,;:!?()[]{}"),
)
LEXICON = DomainLexicon(terms=("หน่วยกิต", "รหัสวิชา", "หลักสูตร"), patterns=())

PAGE_QUALITY_CONFIG = PageQualityConfig(
    weight_extracted_char_count=0.45,
    weight_out_of_charset_ratio=0.20,
    weight_image_area_ratio=0.20,
    weight_domain_lexicon_match_count=0.15,
    low_text_char_threshold=120,
    ocr_candidate_budget_pages=979,
    char_count_reference=1200,
    lexicon_match_reference=12,
)
PAGE_ROUTE_CONFIG = PageRouteConfig(fast_max_image_area_ratio=0.30, deep_min_image_area_ratio=0.60)

GATE = PageQualityGate(PAGE_QUALITY_CONFIG, LEXICON, CHARSET)
ROUTER = OcrPageRouter(PAGE_ROUTE_CONFIG)


def _page(image_area_ratio: float, image_count: int) -> PageCharSet:
    return PageCharSet(
        document_id="synthetic",
        page=1,
        width_pt=600.0,
        height_pt=800.0,
        chars=(),
        image_count=image_count,
        image_area_ratio=image_area_ratio,
    )


text_strategy = st.text(
    alphabet=st.sampled_from(
        "abcXYZ0123456789 \tกขคงจฉรลวหน่วยกิตรหัสวิชาหลักสูตร่้๊๋์็ึ.,;:!?"
    ),
    min_size=0,
    max_size=400,
)
image_ratio_strategy = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
image_count_strategy = st.integers(min_value=0, max_value=5)


@given(text_strategy, image_ratio_strategy, image_count_strategy)
@settings(max_examples=300, deadline=None)
def test_score_is_deterministic(text: str, image_area_ratio: float, image_count: int) -> None:
    """input เดิมต้องให้ PageMetrics เดิมทุกครั้ง (Property 17, R4.2)."""
    page = _page(image_area_ratio, image_count)
    first = GATE.score(page, text)
    assert GATE.score(page, text) == first
    assert GATE.score(page, text) == first


@given(text_strategy, image_ratio_strategy, image_count_strategy)
@settings(max_examples=300, deadline=None)
def test_score_within_bounds(text: str, image_area_ratio: float, image_count: int) -> None:
    """page_quality_score ต้องอยู่ในช่วง 0.00-1.00 เสมอ (R4.1)."""
    page = _page(image_area_ratio, image_count)
    metrics = GATE.score(page, text)
    assert 0.0 <= metrics.page_quality_score <= 1.0
    assert 0.0 <= metrics.out_of_charset_ratio <= 1.0


@given(text_strategy, image_ratio_strategy, image_count_strategy)
@settings(max_examples=300, deadline=None)
def test_router_is_total_and_deterministic(text: str, image_area_ratio: float, image_count: int) -> None:
    """Ocr_Page_Router ต้องให้ compute path ที่ถูกต้องเสมอค่าเดียวและ deterministic (R4.7)."""
    page = _page(image_area_ratio, image_count)
    metrics = GATE.score(page, text)
    first = ROUTER.route(metrics)
    assert isinstance(first.compute_path, ComputePath)
    assert ROUTER.route(metrics) == first

    if metrics.extracted_char_count == 0:
        assert first.compute_path is ComputePath.DEEP
        assert first.reason_code == "no_text"
    elif metrics.image_area_ratio >= PAGE_ROUTE_CONFIG.deep_min_image_area_ratio:
        assert first.compute_path is ComputePath.DEEP
        assert first.reason_code == "high_image_area"
    elif metrics.image_area_ratio <= PAGE_ROUTE_CONFIG.fast_max_image_area_ratio:
        assert first.compute_path is ComputePath.FAST
        assert first.reason_code == "low_image_area"
    else:
        assert first.compute_path is ComputePath.STANDARD
        assert first.reason_code == "default_standard"


@given(
    st.integers(min_value=0, max_value=300),
    st.integers(min_value=0, max_value=3),
    st.integers(min_value=0, max_value=1200),
)
@settings(max_examples=300, deadline=None)
def test_mark_is_consistent_with_threshold_and_budget(
    char_len: int, image_count: int, candidates_so_far: int
) -> None:
    """กฎ candidacy ต้องตรงตามเกณฑ์ char_count/image/budget เสมอ (R4.3-R4.6)."""
    text = "ก" * char_len
    page = _page(0.5 if image_count > 0 else 0.0, image_count)
    metrics = GATE.score(page, text)
    decision = GATE.mark(metrics, image_count, candidates_so_far)

    threshold = PAGE_QUALITY_CONFIG.low_text_char_threshold
    budget = PAGE_QUALITY_CONFIG.ocr_candidate_budget_pages

    if metrics.extracted_char_count >= threshold:
        assert not decision.is_ocr_candidate
        assert decision.candidates_so_far_after == candidates_so_far
        assert not decision.review_issues
    elif image_count < 1:
        assert not decision.is_ocr_candidate
        assert decision.candidates_so_far_after == candidates_so_far
        assert [i.kind for i in decision.review_issues] == ["low_content_page"]
    elif candidates_so_far >= budget:
        assert not decision.is_ocr_candidate
        assert decision.candidates_so_far_after == candidates_so_far
        assert [i.kind for i in decision.review_issues] == ["ocr_budget_exhausted"]
    else:
        assert decision.is_ocr_candidate
        assert decision.candidate_reason == "low_text_with_image"
        assert decision.candidates_so_far_after == candidates_so_far + 1
        assert not decision.review_issues


def test_budget_never_exceeded_across_sequence() -> None:
    """จำลองลำดับหน้าที่ทุกหน้าเข้าเกณฑ์ candidate — สะสมต้องไม่เกิน budget เด็ดขาด (R4.5)."""
    budget = PAGE_QUALITY_CONFIG.ocr_candidate_budget_pages
    candidates_so_far = 0
    marked = 0
    exhausted_issues = 0
    page = _page(0.5, 1)
    metrics = GATE.score(page, "")  # extracted_char_count = 0 < threshold, มีภาพ -> candidate เสมอ
    for _ in range(budget + 50):
        decision = GATE.mark(metrics, 1, candidates_so_far)
        candidates_so_far = decision.candidates_so_far_after
        if decision.is_ocr_candidate:
            marked += 1
        if decision.review_issues and decision.review_issues[0].kind == "ocr_budget_exhausted":
            exhausted_issues += 1
    assert marked == budget
    assert exhausted_issues == 50
    assert candidates_so_far == budget
