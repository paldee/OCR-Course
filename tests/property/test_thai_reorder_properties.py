"""Property test ของการจัดลำดับข้อความไทย (tasks 6.3, R3.3-R3.5, R3.7, R3.10).

generator สร้างลำดับ glyph สังเคราะห์ที่เลียนพฤติกรรมที่วัดได้จากคลังจริง

* base เป็นพยัญชนะไทย มี bbox กว้างจริง
* combining mark มี bbox **กว้างศูนย์** วางทับ base (ยืนยันจากคลัง: mark ทุกตัวใน
  BIT2565 p269/270/273 มีความกว้าง 0.00 pt พอดี)
* มี glyph กว้างศูนย์ที่ **ไม่ใช่** mark ปนอยู่ (`^` U+005E, `_` U+005F พบจริงในคลัง)
  เพื่อกันการถอยกลับไปตัดสิน mark จากความกว้างเพียงอย่างเดียว
* แทรก whitespace ได้ทั้งตำแหน่งที่ต้องลบ (ก่อน mark) และตำแหน่งที่ต้องคงไว้
* สระที่กินที่ (เ แ โ ใ ไ ะ า ำ) ปนอยู่ เพื่อยืนยันว่าช่องว่างหน้าสระเหล่านี้ไม่ถูกลบ
"""

from __future__ import annotations

from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from katrag.common.normalize import (
    ABOVE_VOWELS,
    BELOW_VOWELS,
    OTHER_SIGNS,
    TONE_MARKS,
    codepoint_multiset,
    mark_class,
    marker_space_violations,
)
from katrag.common.types import BBox, CharRecord, PageCharSet
from katrag.config import ThaiConfig
from katrag.ingest.line_assembler import LineAssembler
from katrag.ingest.thai_reorder import ReorderedPage, ThaiGlyphReorderer

THAI_CONFIG = ThaiConfig(
    zero_width_max_points=0.5,
    baseline_tolerance_ratio=0.20,
    horizontal_window_ratio=1.50,
    line_baseline_tolerance_ratio=0.30,
)

CONSONANTS = "กขคงจฉชซญฎฏฐฑฒณดตถทธนบปผฝพฟภมยรลวศษสหอฮ"
SPACING_VOWELS = "\u0e30\u0e32\u0e33\u0e40\u0e41\u0e42\u0e43\u0e44"  # ะ า ำ เ แ โ ใ ไ
ZERO_WIDTH_NON_MARKS = "^_"
MARKS = sorted(BELOW_VOWELS | ABOVE_VOWELS | TONE_MARKS | OTHER_SIGNS)

FONT_SIZE = 16.0
GLYPH_WIDTH = 8.0
LINE_HEIGHT = 20.0
PAGE_WIDTH = 600.0
PAGE_HEIGHT = 800.0


@st.composite
def glyph_pages(draw: st.DrawFn) -> PageCharSet:
    """สร้างหน้าสังเคราะห์: 1-3 บรรทัด แต่ละบรรทัดมี 1-6 หน่วย."""
    line_count = draw(st.integers(min_value=1, max_value=3))
    chars: list[CharRecord] = []
    order = 0
    for line_index in range(line_count):
        baseline = 60.0 + line_index * LINE_HEIGHT * 3.0
        pen_x = 40.0
        unit_count = draw(st.integers(min_value=1, max_value=6))
        for _ in range(unit_count):
            kind = draw(st.sampled_from(["cluster", "spacing_vowel", "zero_width_other", "space"]))
            if kind == "space":
                chars.append(
                    CharRecord(
                        codepoint=" ",
                        bbox=BBox(pen_x, baseline - FONT_SIZE, pen_x + 4.0, baseline + 4.0),
                        font_name="THSarabunPSK",
                        font_size=FONT_SIZE,
                        baseline=baseline,
                        order=order,
                    )
                )
                order += 1
                pen_x += 4.0
                continue
            if kind == "zero_width_other":
                chars.append(
                    CharRecord(
                        codepoint=draw(st.sampled_from(ZERO_WIDTH_NON_MARKS)),
                        bbox=BBox(pen_x, baseline - FONT_SIZE, pen_x, baseline),
                        font_name="THSarabunPSK",
                        font_size=FONT_SIZE,
                        baseline=baseline,
                        order=order,
                    )
                )
                order += 1
                continue
            if kind == "spacing_vowel":
                codepoint = draw(st.sampled_from(SPACING_VOWELS))
            else:
                codepoint = draw(st.sampled_from(CONSONANTS))
            chars.append(
                CharRecord(
                    codepoint=codepoint,
                    bbox=BBox(pen_x, baseline - FONT_SIZE, pen_x + GLYPH_WIDTH, baseline + 4.0),
                    font_name="THSarabunPSK",
                    font_size=FONT_SIZE,
                    baseline=baseline,
                    order=order,
                )
            )
            order += 1
            base_x = pen_x
            pen_x += GLYPH_WIDTH
            if kind != "cluster":
                continue
            # ช่องว่างคั่นก่อน mark (ต้องถูกลบ) — จำลองข้อบกพร่องที่ R3.3 กล่าวถึง
            gap = draw(st.integers(min_value=0, max_value=2))
            for _ in range(gap):
                chars.append(
                    CharRecord(
                        codepoint=" ",
                        bbox=BBox(pen_x, baseline - FONT_SIZE, pen_x, baseline),
                        font_name="THSarabunPSK",
                        font_size=FONT_SIZE,
                        baseline=baseline,
                        order=order,
                    )
                )
                order += 1
            mark_count = draw(st.integers(min_value=0, max_value=3))
            chosen = draw(
                st.lists(st.sampled_from(MARKS), min_size=mark_count, max_size=mark_count, unique=True)
            )
            for mark in chosen:
                chars.append(
                    CharRecord(
                        codepoint=mark,
                        bbox=BBox(base_x + 1.0, baseline - FONT_SIZE - 4.0, base_x + 1.0, baseline - FONT_SIZE),
                        font_name="THSarabunPSK",
                        font_size=FONT_SIZE,
                        baseline=baseline,
                        order=order,
                    )
                )
                order += 1
    assume(chars)
    return PageCharSet(
        document_id="synthetic",
        page=1,
        width_pt=PAGE_WIDTH,
        height_pt=PAGE_HEIGHT,
        chars=tuple(chars),
        image_count=0,
        image_area_ratio=0.0,
    )


def _reorderer() -> ThaiGlyphReorderer:
    return ThaiGlyphReorderer(THAI_CONFIG)


def _as_char_set(page: ReorderedPage) -> PageCharSet:
    """ห่อผลลัพธ์กลับเป็น input เพื่อทดสอบ idempotence."""
    return PageCharSet(
        document_id=page.document_id,
        page=page.page,
        width_pt=page.width_pt,
        height_pt=page.height_pt,
        chars=page.chars,
        image_count=0,
        image_area_ratio=0.0,
    )


PROPERTY_SETTINGS = settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


@given(glyph_pages())
@PROPERTY_SETTINGS
def test_reorder_is_deterministic(char_set: PageCharSet) -> None:
    """เรียกสามครั้งบน input เดียวกันต้องได้ผลเท่ากันทุกฟิลด์ (R3.5)."""
    reorderer = _reorderer()
    first = reorderer.reorder(char_set)
    assert reorderer.reorder(char_set) == first
    assert reorderer.reorder(char_set) == first


@given(glyph_pages())
@PROPERTY_SETTINGS
def test_reorder_is_idempotent(char_set: PageCharSet) -> None:
    """จัดลำดับซ้ำบนผลของตัวเองต้องไม่เปลี่ยนข้อความ (R3.5)."""
    reorderer = _reorderer()
    once = reorderer.reorder(char_set)
    twice = reorderer.reorder(_as_char_set(once))
    assert twice.text == once.text


@given(glyph_pages())
@PROPERTY_SETTINGS
def test_no_space_before_combining_mark(char_set: PageCharSet) -> None:
    """ผลลัพธ์ต้องไม่มี whitespace คั่นระหว่างอักขระไทยกับ combining mark (R3.4)."""
    result = _reorderer().reorder(char_set)
    assert marker_space_violations(result.text) == 0


@given(glyph_pages())
@PROPERTY_SETTINGS
def test_only_marker_spaces_are_removed(char_set: PageCharSet) -> None:
    """whitespace ที่ไม่ได้คั่นหน้า combining mark ต้องไม่ถูกลบ (R3.3).

    นับจำนวน whitespace ที่ "ควรถูกลบ" จาก input โดยตรง แล้วเทียบกับส่วนต่างจริง
    """
    result = _reorderer().reorder(char_set)
    source = char_set.chars
    expected_removed = 0
    index = 0
    while index < len(source):
        if not source[index].codepoint.isspace():
            index += 1
            continue
        run_start = index
        while index < len(source) and source[index].codepoint.isspace():
            index += 1
        left_thai = run_start > 0 and "\u0e00" <= source[run_start - 1].codepoint <= "\u0e7f"
        right_mark = index < len(source) and mark_class(source[index].codepoint) > 0
        if left_thai and right_mark:
            expected_removed += index - run_start
    source_spaces = sum(1 for char in source if char.codepoint.isspace())
    result_spaces = sum(1 for char in result.chars if char.codepoint.isspace())
    assert source_spaces - result_spaces == expected_removed
    assert result.dropped_marker_spaces == expected_removed


@given(glyph_pages())
@PROPERTY_SETTINGS
def test_non_whitespace_multiset_is_preserved_by_reorder(char_set: PageCharSet) -> None:
    """multiset ของ codepoint ที่ไม่ใช่ whitespace ต้องคงเดิมหลังจัดลำดับ (R3.7)."""
    result = _reorderer().reorder(char_set)
    source_text = "".join(char.codepoint for char in char_set.chars)
    assert codepoint_multiset(result.text) == codepoint_multiset(source_text)


@given(glyph_pages())
@PROPERTY_SETTINGS
def test_non_whitespace_multiset_is_preserved_by_assembly(char_set: PageCharSet) -> None:
    """multiset ต้องคงเดิมหลังประกอบบรรทัด และไม่มี glyph_count_mismatch (R3.7, R3.10)."""
    reordered = _reorderer().reorder(char_set)
    assembled = LineAssembler(THAI_CONFIG).assemble(reordered)
    assert assembled.multiset_preserved
    assert codepoint_multiset(assembled.text) == codepoint_multiset(reordered.text)
    assert not [issue for issue in assembled.review_issues if issue.kind == "glyph_count_mismatch"]


@given(glyph_pages())
@PROPERTY_SETTINGS
def test_marks_follow_their_base_in_canonical_class_order(char_set: PageCharSet) -> None:
    """ภายในแต่ละ cluster ลำดับ mark ต้องไม่ถอยหลังตามชั้น (R3.1)."""
    result = _reorderer().reorder(char_set)
    previous_class = 0
    for char in result.chars:
        current = mark_class(char.codepoint)
        if current == 0:
            previous_class = 0
            continue
        assert current >= previous_class
        previous_class = current


@given(glyph_pages())
@PROPERTY_SETTINGS
def test_assembly_is_deterministic(char_set: PageCharSet) -> None:
    """ประกอบบรรทัดซ้ำต้องได้บรรทัดชุดเดิมทุกฟิลด์ (R3.5, R3.6)."""
    reordered = _reorderer().reorder(char_set)
    assembler = LineAssembler(THAI_CONFIG)
    first = assembler.assemble(reordered)
    assert assembler.assemble(reordered) == first
    assert [line.order for line in first.lines] == list(range(len(first.lines)))


# ── เคสเจาะจงที่ generator ไม่แตะ (unresolved + tie-break) ──────────────


def _char(codepoint: str, x0: float, baseline: float, order: int, *, zero_width: bool = False) -> CharRecord:
    width = 0.0 if zero_width else GLYPH_WIDTH
    return CharRecord(
        codepoint=codepoint,
        bbox=BBox(x0, baseline - FONT_SIZE, x0 + width, baseline),
        font_name="THSarabunPSK",
        font_size=FONT_SIZE,
        baseline=baseline,
        order=order,
    )


def _page(*chars: CharRecord) -> PageCharSet:
    return PageCharSet(
        document_id="synthetic",
        page=1,
        width_pt=PAGE_WIDTH,
        height_pt=PAGE_HEIGHT,
        chars=chars,
        image_count=0,
        image_area_ratio=0.0,
    )


def test_unresolved_mark_keeps_position_and_reports_issue() -> None:
    """mark ที่ไม่มี base ในหน้าต่างต้องคงตำแหน่งเดิมและถูกรายงาน (R3.9)."""
    # base อยู่ baseline 100 แต่ mark อยู่ baseline 400 -> เกิน tolerance 20% x 16pt = 3.2pt
    page = _page(
        _char("ก", 40.0, 100.0, 0),
        _char("\u0e48", 41.0, 400.0, 1, zero_width=True),
        _char("ข", 48.0, 100.0, 2),
    )
    result = ThaiGlyphReorderer(THAI_CONFIG).reorder(page)
    assert result.unresolved_marks == 1
    assert result.text == "ก\u0e48ข"  # ลำดับเดิมคงอยู่ ไม่ถูกย้ายไปเกาะ base ใด
    kinds = [issue.kind for issue in result.review_issues]
    assert "thai_reorder_unresolved" in kinds
    detail = result.review_issues[0].detail
    assert detail["reason"] == "no_base_within_window"
    assert detail["unresolved_marks"] == 1


def test_mark_outside_horizontal_window_is_unresolved() -> None:
    """mark ที่ห่างเกิน 1.5 เท่าของ font size ในแนวนอนต้องหา base ไม่ได้ (R3.2)."""
    # หน้าต่าง = 1.5 x 16 = 24pt วัดจากจุดกลาง; base center=44, mark center=100 -> 56pt
    page = _page(
        _char("ก", 40.0, 100.0, 0),
        _char("\u0e48", 100.0, 100.0, 1, zero_width=True),
    )
    result = ThaiGlyphReorderer(THAI_CONFIG).reorder(page)
    assert result.unresolved_marks == 1


def test_equidistant_mark_attaches_to_left_base() -> None:
    """ระยะเท่ากันต้องเลือก base ที่อยู่ซ้าย (R3.2)."""
    # base ซ้าย center=44, base ขวา center=56, mark center=50 -> ห่างเท่ากัน 6pt
    page = _page(
        _char("ก", 40.0, 100.0, 0),
        _char("ข", 52.0, 100.0, 1),
        _char("\u0e48", 50.0, 100.0, 2, zero_width=True),
    )
    result = ThaiGlyphReorderer(THAI_CONFIG).reorder(page)
    assert result.unresolved_marks == 0
    assert result.text == "ก\u0e48ข"


def test_pua_mark_is_mapped_and_ordered() -> None:
    """PUA ของฟอนต์ไทยต้องถูกแปลงเป็น mark จริงและจัดลำดับตามชั้น (R3.1)."""
    # ลำดับ input: base, tone(U+F70A -> U+0E48), above vowel(U+F702 -> U+0E35)
    page = _page(
        _char("ป", 40.0, 100.0, 0),
        _char("\uf70a", 41.0, 100.0, 1, zero_width=True),
        _char("\uf702", 41.0, 100.0, 2, zero_width=True),
    )
    result = ThaiGlyphReorderer(THAI_CONFIG).reorder(page)
    assert result.pua_mapped == 2
    assert result.text == "ป\u0e35\u0e48"  # above vowel มาก่อน tone


def test_zero_width_non_mark_is_not_treated_as_mark() -> None:
    """glyph กว้างศูนย์ที่ไม่ใช่ mark ต้องไม่ถูกย้าย (พบ `^`/`_` จริงในคลัง)."""
    page = _page(
        _char("ก", 40.0, 100.0, 0),
        _char("^", 41.0, 100.0, 1, zero_width=True),
        _char("\u0e48", 41.0, 100.0, 2, zero_width=True),
    )
    result = ThaiGlyphReorderer(THAI_CONFIG).reorder(page)
    assert result.text == "ก\u0e48^"
    assert result.unresolved_marks == 0


def test_spacing_vowel_after_space_is_kept() -> None:
    """ช่องว่างหน้าสระที่กินที่ต้องไม่ถูกลบ (แก้ขอบเขต pattern ของ R3.4)."""
    page = _page(
        _char("ก", 40.0, 100.0, 0),
        _char(" ", 48.0, 100.0, 1),
        _char("\u0e41", 56.0, 100.0, 2),  # แ
        _char("ล", 64.0, 100.0, 3),
    )
    result = ThaiGlyphReorderer(THAI_CONFIG).reorder(page)
    assert result.text == "ก \u0e41ล"
    assert result.dropped_marker_spaces == 0


def test_marker_space_before_mark_is_removed() -> None:
    """ช่องว่างที่คั่นระหว่างอักขระไทยกับ combining mark ต้องถูกลบ (R3.3)."""
    page = _page(
        _char("ก", 40.0, 100.0, 0),
        _char(" ", 48.0, 100.0, 1),
        _char("\u0e48", 41.0, 100.0, 2, zero_width=True),
    )
    result = ThaiGlyphReorderer(THAI_CONFIG).reorder(page)
    assert result.text == "ก\u0e48"
    assert result.dropped_marker_spaces == 1
    assert marker_space_violations(result.text) == 0


def test_lines_are_grouped_by_baseline_and_ordered_top_down() -> None:
    """บรรทัดต้องจัดกลุ่มตาม baseline และเรียงจากบนลงล่าง (R3.6)."""
    page = _page(
        _char("ข", 60.0, 100.0, 0),
        _char("ก", 40.0, 100.0, 1),
        _char("ค", 40.0, 160.0, 2),
    )
    reordered = ThaiGlyphReorderer(THAI_CONFIG).reorder(page)
    assembled = LineAssembler(THAI_CONFIG).assemble(reordered)
    assert [line.text for line in assembled.lines] == ["กข", "ค"]
    assert assembled.text == "กข\nค"


def test_cluster_stays_intact_when_mark_x_precedes_base_x() -> None:
    """cluster ต้องไม่ถูกแยกแม้ x0 ของ mark น้อยกว่า x0 ของ base (เหตุผลของการเรียงเป็น cluster)."""
    page = _page(
        _char("ก", 40.0, 100.0, 0),
        _char("\u0e48", 38.0, 100.0, 1, zero_width=True),
        _char("ข", 48.0, 100.0, 2),
    )
    reordered = ThaiGlyphReorderer(THAI_CONFIG).reorder(page)
    assembled = LineAssembler(THAI_CONFIG).assemble(reordered)
    assert assembled.text == "ก\u0e48ข"
