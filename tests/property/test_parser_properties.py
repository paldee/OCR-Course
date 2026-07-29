"""Property test ของ Credits_Parser / Prerequisite_Parser (task 11.3).

คุณสมบัติที่ทดสอบ:
1. Credits round-trip: parse(print(x)) == x สำหรับทุก Credits ที่ valid
2. Prerequisite round-trip: parse(print(e)) == e สำหรับทุก PrereqNode ที่ valid
3. Parser ปฏิเสธแบบไม่ทิ้งค่าบางส่วน: เมื่อ parser คืน error ผลลัพธ์เป็น
   ParseFailure ที่มี raw_text ตรงกับ input และไม่มี partial value ถูกบันทึก

**Validates: Requirements 8.3, 8.4, 8.6, 8.9**
"""

from __future__ import annotations

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from katrag.common.types import (
    Credits,
    PrereqAnd,
    PrereqEmpty,
    PrereqLeaf,
    PrereqNode,
    PrereqOr,
)
from katrag.errors import ParseFailure
from katrag.ingest.fields.credits import parse_credits, print_credits
from katrag.ingest.fields.prerequisite import parse_prerequisite, print_prerequisite

PROPERTY_SETTINGS = settings(max_examples=200, deadline=None)

# ══════════════════════════════════════════════════════════════════════
# Strategies
# ══════════════════════════════════════════════════════════════════════

# ── Credits strategy ──────────────────────────────────────────────────

_int_0_30 = st.integers(min_value=0, max_value=30)


@st.composite
def credits_st(draw: st.DrawFn) -> Credits:
    """สุ่ม Credits ที่ valid — ทุกค่า 0-30."""
    return Credits(
        total=draw(_int_0_30),
        lecture=draw(_int_0_30),
        lab=draw(_int_0_30),
        self_study=draw(_int_0_30),
    )


# ── Prerequisite strategy ─────────────────────────────────────────────

# รหัสวิชา: ตัวเลข 8 หลัก (pattern ปกติของ KMITL)
_course_code_st = st.from_regex(r"[0-9]{8}", fullmatch=True)


@st.composite
def prereq_node_st(
    draw: st.DrawFn, max_depth: int = 3, parent_op: str | None = None
) -> PrereqNode:
    """สุ่ม PrereqNode tree ในรูปแบบ canonical:
    - leaves มีรหัสวิชา valid
    - รวม ≤ 20 codes
    - depth ≤ 3
    - ไม่ซ้อน operator เดียวกัน (canonical form: AND ภายใน AND จะถูก flatten)
    """
    if max_depth <= 0:
        # Base case: leaf only
        return PrereqLeaf(code=draw(_course_code_st))

    # เลือก kind โดยห้ามซ้อน operator เดียวกันกับ parent
    choices = ["empty", "leaf", "and", "or"]
    if parent_op == "and":
        choices = ["empty", "leaf", "or"]
    elif parent_op == "or":
        choices = ["empty", "leaf", "and"]

    kind = draw(st.sampled_from(choices))

    if kind == "empty":
        return PrereqEmpty()
    elif kind == "leaf":
        return PrereqLeaf(code=draw(_course_code_st))
    elif kind == "and":
        # 2-5 children, depth - 1; children must not be AND (canonical)
        n_children = draw(st.integers(min_value=2, max_value=5))
        children = tuple(
            draw(prereq_node_st(max_depth=max_depth - 1, parent_op="and"))
            for _ in range(n_children)
        )
        # Filter out empties from and/or children (invalid structure)
        children = tuple(c for c in children if not isinstance(c, PrereqEmpty))
        if len(children) < 2:
            return PrereqLeaf(code=draw(_course_code_st))
        return PrereqAnd(children=children)
    else:  # or
        n_children = draw(st.integers(min_value=2, max_value=5))
        children = tuple(
            draw(prereq_node_st(max_depth=max_depth - 1, parent_op="or"))
            for _ in range(n_children)
        )
        children = tuple(c for c in children if not isinstance(c, PrereqEmpty))
        if len(children) < 2:
            return PrereqLeaf(code=draw(_course_code_st))
        return PrereqOr(children=children)


def _count_codes(node: PrereqNode) -> int:
    """นับจำนวนรหัสวิชาใน tree."""
    if isinstance(node, PrereqEmpty):
        return 0
    if isinstance(node, PrereqLeaf):
        return 1
    if isinstance(node, (PrereqAnd, PrereqOr)):
        return sum(_count_codes(c) for c in node.children)
    return 0


def _tree_depth(node: PrereqNode) -> int:
    """วัดความลึกของ tree."""
    if isinstance(node, (PrereqAnd, PrereqOr)):
        if not node.children:
            return 1
        return 1 + max(_tree_depth(c) for c in node.children)
    return 0


# ── Invalid strings strategy ──────────────────────────────────────────

_invalid_credits_st = st.one_of(
    # ขาด parenthesis
    st.from_regex(r"[0-9]{1,2}-[0-9]{1,2}-[0-9]{1,2}", fullmatch=True),
    # มีตัวอักษรแทรก
    st.from_regex(r"[0-9]{1,2}[a-z]\([0-9]{1,2}-[0-9]{1,2}-[0-9]{1,2}\)", fullmatch=True),
    # ค่านอกช่วง (31-99)
    st.from_regex(r"[3-9][1-9]\([0-9]{1,2}-[0-9]{1,2}-[0-9]{1,2}\)", fullmatch=True),
    # สตริงว่าง
    st.just(""),
    # มี whitespace แทรก
    st.from_regex(r"[0-9]{1,2} \([0-9]{1,2}-[0-9]{1,2}-[0-9]{1,2}\)", fullmatch=True),
    # ขาด dash
    st.from_regex(r"[0-9]{1,2}\([0-9]{1,2}[0-9]{1,2}-[0-9]{1,2}\)", fullmatch=True),
)

_invalid_prereq_st = st.one_of(
    # unmatched parenthesis
    st.from_regex(r"\([0-9]{8} และ [0-9]{8}", fullmatch=True),
    # ข้อความยาวเกิน 500 ตัวอักษร
    st.just("06016101 และ " * 50 + "06016101"),
    # วงเล็บปิดโดดๆ
    st.from_regex(r"\) [0-9]{8}", fullmatch=True),
    # operator ซ้อนไม่มี operand
    st.just("และ"),
    st.just("หรือ"),
)


# ══════════════════════════════════════════════════════════════════════
# Property 4: Credits round-trip
# ══════════════════════════════════════════════════════════════════════


@given(cred=credits_st())
@PROPERTY_SETTINGS
def test_credits_round_trip(cred: Credits) -> None:
    """parse(print(x)) == x สำหรับทุก Credits ที่ valid.

    **Validates: Requirements 8.4**
    """
    printed = print_credits(cred)
    result = parse_credits(printed)

    assert not isinstance(result, ParseFailure), (
        f"print_credits produced unparseable string: {printed!r}, "
        f"error: {result.reason}"
    )
    assert result == cred, (
        f"round-trip mismatch: original={cred}, "
        f"printed={printed!r}, re-parsed={result}"
    )


# ══════════════════════════════════════════════════════════════════════
# Property 5: Prerequisite round-trip
# ══════════════════════════════════════════════════════════════════════


@given(node=prereq_node_st())
@PROPERTY_SETTINGS
def test_prerequisite_round_trip(node: PrereqNode) -> None:
    """parse(print(e)) == e สำหรับทุก PrereqNode ที่ valid.

    **Validates: Requirements 8.6**
    """
    # Ensure constraints: ≤ 20 codes, depth ≤ 3
    assume(_count_codes(node) <= 20)
    assume(_tree_depth(node) <= 3)

    printed = print_prerequisite(node)
    result = parse_prerequisite(printed)

    assert not isinstance(result, ParseFailure), (
        f"print_prerequisite produced unparseable string: {printed!r}, "
        f"error at index {result.error_index}: {result.reason}"
    )
    assert result == node, (
        f"round-trip mismatch:\n"
        f"  original: {node}\n"
        f"  printed:  {printed!r}\n"
        f"  re-parsed: {result}"
    )


# ══════════════════════════════════════════════════════════════════════
# Property 6: Parser ปฏิเสธแบบไม่ทิ้งค่าบางส่วน
# ══════════════════════════════════════════════════════════════════════


@given(raw=_invalid_credits_st)
@PROPERTY_SETTINGS
def test_credits_parse_failure_returns_no_partial(raw: str) -> None:
    """เมื่อ Credits_Parser คืน error ผลลัพธ์เป็น ParseFailure
    ที่มี raw_text == input และไม่มีค่าตัวเลขบางส่วนถูกบันทึก.

    **Validates: Requirements 8.3**
    """
    result = parse_credits(raw)

    # ต้องเป็น ParseFailure (ไม่ใช่ Credits ที่ valid)
    assert isinstance(result, ParseFailure), (
        f"expected ParseFailure for invalid input {raw!r}, got {result}"
    )

    # raw_text ต้องเป็นสตริงต้นฉบับ — ไม่มีการ trim หรือแก้ไข
    assert result.raw_text == raw, (
        f"raw_text mismatch: expected {raw!r}, got {result.raw_text!r}"
    )

    # error_index ต้องไม่ติดลบ
    assert result.error_index >= 0, (
        f"error_index must be >= 0, got {result.error_index}"
    )

    # reason ต้องไม่ว่าง
    assert result.reason, "ParseFailure.reason must not be empty"


@given(raw=_invalid_prereq_st)
@PROPERTY_SETTINGS
def test_prerequisite_parse_failure_returns_no_partial(raw: str) -> None:
    """เมื่อ Prerequisite_Parser คืน error ผลลัพธ์เป็น ParseFailure
    ที่มี raw_text == input และไม่มีรหัสวิชาบางส่วนถูกบันทึก.

    **Validates: Requirements 8.9**
    """
    result = parse_prerequisite(raw)

    # ต้องเป็น ParseFailure (ไม่ใช่ PrereqNode ที่ valid)
    assert isinstance(result, ParseFailure), (
        f"expected ParseFailure for invalid input {raw!r}, got {result}"
    )

    # raw_text ต้องเป็นสตริงต้นฉบับ
    assert result.raw_text == raw, (
        f"raw_text mismatch: expected {raw!r}, got {result.raw_text!r}"
    )

    # error_index ต้องไม่ติดลบ
    assert result.error_index >= 0, (
        f"error_index must be >= 0, got {result.error_index}"
    )

    # reason ต้องไม่ว่าง
    assert result.reason, "ParseFailure.reason must not be empty"
