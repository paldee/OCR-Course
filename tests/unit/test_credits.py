"""Unit tests for katrag.ingest.fields.credits (R8.2, R8.3, R8.4)."""

from __future__ import annotations

import pytest

from katrag.common.types import Credits
from katrag.errors import ParseFailure
from katrag.ingest.fields.credits import parse_credits, print_credits


# ── Happy path ────────────────────────────────────────────────────────


class TestParseCreditsValid:
    """R8.2: parse สตริงรูปแบบ total(lecture-lab-self_study) เป็นโครงสร้าง."""

    def test_basic(self) -> None:
        result = parse_credits("3(3-0-6)")
        assert result == Credits(total=3, lecture=3, lab=0, self_study=6)

    def test_with_lab(self) -> None:
        result = parse_credits("6(3-3-12)")
        assert result == Credits(total=6, lecture=3, lab=3, self_study=12)

    def test_all_zeros(self) -> None:
        result = parse_credits("0(0-0-0)")
        assert result == Credits(total=0, lecture=0, lab=0, self_study=0)

    def test_max_values(self) -> None:
        result = parse_credits("30(30-30-30)")
        assert result == Credits(total=30, lecture=30, lab=30, self_study=30)

    def test_two_digit_self_study(self) -> None:
        result = parse_credits("4(2-2-10)")
        assert result == Credits(total=4, lecture=2, lab=2, self_study=10)

    def test_deterministic(self) -> None:
        """R8.2: same input -> same output (deterministic)."""
        raw = "3(3-0-6)"
        first = parse_credits(raw)
        second = parse_credits(raw)
        assert first == second


# ── Round-trip ────────────────────────────────────────────────────────


class TestRoundTrip:
    """R8.4: parse(print(c)) == c and print(parse(s)) == s."""

    def test_print_then_parse(self) -> None:
        c = Credits(total=3, lecture=3, lab=0, self_study=6)
        assert parse_credits(print_credits(c)) == c

    def test_parse_then_print(self) -> None:
        raw = "6(3-3-12)"
        result = parse_credits(raw)
        assert isinstance(result, Credits)
        assert print_credits(result) == raw

    @pytest.mark.parametrize(
        "raw",
        [
            "0(0-0-0)",
            "1(1-0-0)",
            "3(3-0-6)",
            "6(3-3-12)",
            "30(30-30-30)",
            "12(6-3-18)",
        ],
    )
    def test_round_trip_parametrized(self, raw: str) -> None:
        result = parse_credits(raw)
        assert isinstance(result, Credits)
        assert print_credits(result) == raw
        assert parse_credits(print_credits(result)) == result


# ── Error cases ───────────────────────────────────────────────────────


class TestParseCreditsError:
    """R8.3: error with error_index of first mismatch character."""

    def test_empty_string(self) -> None:
        result = parse_credits("")
        assert isinstance(result, ParseFailure)
        assert result.error_index == 0
        assert result.raw_text == ""

    def test_no_parenthesis(self) -> None:
        result = parse_credits("3")
        assert isinstance(result, ParseFailure)
        assert result.error_index == 1  # after '3', expect '('

    def test_missing_close_paren(self) -> None:
        result = parse_credits("3(3-0-6")
        assert isinstance(result, ParseFailure)
        assert result.error_index == 7  # expect ')' at end

    def test_extra_chars_after(self) -> None:
        result = parse_credits("3(3-0-6)x")
        assert isinstance(result, ParseFailure)
        assert result.error_index == 8  # extra char at pos 8

    def test_spaces_not_allowed(self) -> None:
        result = parse_credits("3 (3-0-6)")
        assert isinstance(result, ParseFailure)
        assert result.error_index == 1  # space where '(' expected

    def test_value_out_of_range_total(self) -> None:
        result = parse_credits("31(3-0-6)")
        assert isinstance(result, ParseFailure)
        assert result.error_index == 0  # total starts at 0
        assert "total" in result.reason

    def test_value_out_of_range_lecture(self) -> None:
        result = parse_credits("3(31-0-6)")
        assert isinstance(result, ParseFailure)
        assert result.error_index == 2  # lecture starts after '('
        assert "lecture" in result.reason

    def test_value_out_of_range_lab(self) -> None:
        result = parse_credits("3(3-31-6)")
        assert isinstance(result, ParseFailure)
        assert result.error_index == 4  # lab starts after '3-'
        assert "lab" in result.reason

    def test_value_out_of_range_self_study(self) -> None:
        result = parse_credits("3(3-0-31)")
        assert isinstance(result, ParseFailure)
        assert result.error_index == 6  # self_study starts after '3-0-'
        assert "self_study" in result.reason

    def test_non_numeric(self) -> None:
        result = parse_credits("abc")
        assert isinstance(result, ParseFailure)
        assert result.error_index == 0

    def test_wrong_separator(self) -> None:
        result = parse_credits("3(3,0,6)")
        assert isinstance(result, ParseFailure)
        # After '3(' and '3', expect '-' but got ','
        assert result.error_index == 3


# ── Credits_Printer ───────────────────────────────────────────────────


class TestPrintCredits:
    """R8.4: Credits_Printer produces canonical format."""

    def test_basic_format(self) -> None:
        c = Credits(total=3, lecture=3, lab=0, self_study=6)
        assert print_credits(c) == "3(3-0-6)"

    def test_two_digit_values(self) -> None:
        c = Credits(total=12, lecture=6, lab=3, self_study=18)
        assert print_credits(c) == "12(6-3-18)"

    def test_all_zeros(self) -> None:
        c = Credits(total=0, lecture=0, lab=0, self_study=0)
        assert print_credits(c) == "0(0-0-0)"
