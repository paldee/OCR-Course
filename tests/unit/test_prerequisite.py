"""Unit tests for katrag.ingest.fields.prerequisite (R8.5, R8.6, R8.9)."""

from __future__ import annotations

import pytest

from katrag.common.types import (
    PrereqAnd,
    PrereqEmpty,
    PrereqLeaf,
    PrereqNode,
    PrereqOr,
)
from katrag.errors import ParseFailure
from katrag.ingest.fields.prerequisite import parse_prerequisite, print_prerequisite


# ── Empty / no prerequisite ───────────────────────────────────────────


class TestParsePrerequisiteEmpty:
    """R8.5: empty string / whitespace / 'ไม่มี' → PrereqEmpty."""

    def test_empty_string(self) -> None:
        assert parse_prerequisite("") == PrereqEmpty()

    def test_whitespace_only(self) -> None:
        assert parse_prerequisite("   ") == PrereqEmpty()

    def test_thai_keyword_no_prereq(self) -> None:
        assert parse_prerequisite("ไม่มี") == PrereqEmpty()

    def test_dash_keyword(self) -> None:
        assert parse_prerequisite("-") == PrereqEmpty()

    def test_keyword_with_surrounding_whitespace(self) -> None:
        assert parse_prerequisite("  ไม่มี  ") == PrereqEmpty()


# ── Single course code ────────────────────────────────────────────────


class TestParsePrerequisiteSingleCode:
    """R8.5: single course code → PrereqLeaf."""

    def test_basic_code(self) -> None:
        result = parse_prerequisite("06016101")
        assert result == PrereqLeaf(code="06016101")

    def test_code_with_surrounding_spaces(self) -> None:
        result = parse_prerequisite("  06016101  ")
        assert result == PrereqLeaf(code="06016101")

    def test_short_code(self) -> None:
        result = parse_prerequisite("A")
        assert result == PrereqLeaf(code="A")

    def test_max_length_code(self) -> None:
        code = "A" * 20
        result = parse_prerequisite(code)
        assert result == PrereqLeaf(code=code)


# ── AND expressions ───────────────────────────────────────────────────


class TestParsePrerequisiteAnd:
    """R8.5: 'และ' / 'and' → PrereqAnd."""

    def test_thai_and(self) -> None:
        result = parse_prerequisite("06016101 และ 06016102")
        assert result == PrereqAnd(
            children=(PrereqLeaf("06016101"), PrereqLeaf("06016102"))
        )

    def test_english_and(self) -> None:
        result = parse_prerequisite("06016101 and 06016102")
        assert result == PrereqAnd(
            children=(PrereqLeaf("06016101"), PrereqLeaf("06016102"))
        )

    def test_three_way_and(self) -> None:
        result = parse_prerequisite("A และ B และ C")
        assert isinstance(result, PrereqAnd)
        assert len(result.children) == 3


# ── OR expressions ────────────────────────────────────────────────────


class TestParsePrerequisiteOr:
    """R8.5: 'หรือ' / 'or' → PrereqOr."""

    def test_thai_or(self) -> None:
        result = parse_prerequisite("06016101 หรือ 06016102")
        assert result == PrereqOr(
            children=(PrereqLeaf("06016101"), PrereqLeaf("06016102"))
        )

    def test_english_or(self) -> None:
        result = parse_prerequisite("06016101 or 06016102")
        assert result == PrereqOr(
            children=(PrereqLeaf("06016101"), PrereqLeaf("06016102"))
        )


# ── Precedence ────────────────────────────────────────────────────────


class TestParsePrerequisitePrecedence:
    """AND binds tighter than OR: A or B and C → Or(A, And(B, C))."""

    def test_and_binds_tighter(self) -> None:
        result = parse_prerequisite("A หรือ B และ C")
        assert isinstance(result, PrereqOr)
        assert result.children[0] == PrereqLeaf("A")
        assert isinstance(result.children[1], PrereqAnd)
        assert result.children[1].children == (
            PrereqLeaf("B"),
            PrereqLeaf("C"),
        )

    def test_parens_override_precedence(self) -> None:
        result = parse_prerequisite("(A หรือ B) และ C")
        assert isinstance(result, PrereqAnd)
        assert isinstance(result.children[0], PrereqOr)
        assert result.children[1] == PrereqLeaf("C")


# ── Nested expressions ────────────────────────────────────────────────


class TestParsePrerequisiteNested:
    """R8.5: parenthesized nested up to 3 levels."""

    def test_nested_and_in_or(self) -> None:
        result = parse_prerequisite("(06016101 และ 06016102) หรือ 06016103")
        assert isinstance(result, PrereqOr)
        assert isinstance(result.children[0], PrereqAnd)
        assert result.children[1] == PrereqLeaf("06016103")

    def test_three_levels_ok(self) -> None:
        # Level 1: OR, Level 2: AND, Level 3: OR (via parens)
        result = parse_prerequisite("(A หรือ B) และ C หรือ D")
        assert not isinstance(result, ParseFailure)


# ── Round-trip ────────────────────────────────────────────────────────


class TestRoundTrip:
    """R8.6: parse(print(x)) == x for all valid x."""

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "06016101",
            "06016101 และ 06016102",
            "06016101 หรือ 06016102",
            "06016101 หรือ 06016102 และ 06016103",
            "(06016101 หรือ 06016102) และ 06016103",
        ],
    )
    def test_round_trip_from_canonical(self, text: str) -> None:
        parsed = parse_prerequisite(text)
        assert not isinstance(parsed, ParseFailure)
        printed = print_prerequisite(parsed)
        reparsed = parse_prerequisite(printed)
        assert parsed == reparsed

    def test_round_trip_complex(self) -> None:
        node = PrereqOr(
            children=(
                PrereqAnd(
                    children=(PrereqLeaf("06016101"), PrereqLeaf("06016102"))
                ),
                PrereqLeaf("06016103"),
            )
        )
        printed = print_prerequisite(node)
        reparsed = parse_prerequisite(printed)
        assert reparsed == node

    def test_print_preserves_structure(self) -> None:
        """print then parse gives same structure."""
        node = PrereqAnd(
            children=(
                PrereqOr(
                    children=(PrereqLeaf("A"), PrereqLeaf("B"))
                ),
                PrereqLeaf("C"),
            )
        )
        printed = print_prerequisite(node)
        assert "(" in printed  # OR inside AND needs parens
        reparsed = parse_prerequisite(printed)
        assert reparsed == node


# ── Determinism ───────────────────────────────────────────────────────


class TestDeterminism:
    """R8.5: same input → same output always."""

    def test_same_result(self) -> None:
        text = "06016101 และ 06016102 หรือ 06016103"
        first = parse_prerequisite(text)
        second = parse_prerequisite(text)
        assert first == second


# ── Error cases (R8.9) ────────────────────────────────────────────────


class TestParsePrerequisiteError:
    """R8.9: error with error_index of first mismatch character."""

    def test_input_too_long(self) -> None:
        result = parse_prerequisite("x" * 501)
        assert isinstance(result, ParseFailure)
        assert result.error_index == 500
        assert "500" in result.reason

    def test_unclosed_parenthesis(self) -> None:
        result = parse_prerequisite("(A และ B")
        assert isinstance(result, ParseFailure)
        assert ")" in result.reason

    def test_unexpected_close_paren(self) -> None:
        result = parse_prerequisite("A และ )")
        assert isinstance(result, ParseFailure)

    def test_too_many_course_codes(self) -> None:
        codes = " และ ".join(f"C{i:04d}" for i in range(21))
        result = parse_prerequisite(codes)
        assert isinstance(result, ParseFailure)
        assert "too many course codes" in result.reason

    def test_nesting_too_deep(self) -> None:
        # 4 levels of nesting
        result = parse_prerequisite("((((A และ B) หรือ C) และ D) หรือ E)")
        assert isinstance(result, ParseFailure)
        assert "nesting depth" in result.reason

    def test_trailing_operator(self) -> None:
        result = parse_prerequisite("A และ")
        assert isinstance(result, ParseFailure)

    def test_code_too_long(self) -> None:
        result = parse_prerequisite("A" * 21)
        assert isinstance(result, ParseFailure)
        assert "too long" in result.reason

    def test_empty_parens(self) -> None:
        result = parse_prerequisite("()")
        assert isinstance(result, ParseFailure)


# ── Printer ───────────────────────────────────────────────────────────


class TestPrintPrerequisite:
    """R8.6: canonical form."""

    def test_empty(self) -> None:
        assert print_prerequisite(PrereqEmpty()) == ""

    def test_leaf(self) -> None:
        assert print_prerequisite(PrereqLeaf("06016101")) == "06016101"

    def test_and(self) -> None:
        node = PrereqAnd(
            children=(PrereqLeaf("A"), PrereqLeaf("B"))
        )
        assert print_prerequisite(node) == "A และ B"

    def test_or(self) -> None:
        node = PrereqOr(
            children=(PrereqLeaf("A"), PrereqLeaf("B"))
        )
        assert print_prerequisite(node) == "A หรือ B"

    def test_or_inside_and_gets_parens(self) -> None:
        node = PrereqAnd(
            children=(
                PrereqOr(children=(PrereqLeaf("A"), PrereqLeaf("B"))),
                PrereqLeaf("C"),
            )
        )
        assert print_prerequisite(node) == "(A หรือ B) และ C"

    def test_and_inside_or_no_parens(self) -> None:
        node = PrereqOr(
            children=(
                PrereqAnd(children=(PrereqLeaf("A"), PrereqLeaf("B"))),
                PrereqLeaf("C"),
            )
        )
        assert print_prerequisite(node) == "A และ B หรือ C"
