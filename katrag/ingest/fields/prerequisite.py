"""Prerequisite field parser and printer (R8.5, R8.6, R8.9).

รูปแบบเงื่อนไขวิชาก่อน:
- สตริงว่าง / ช่องว่างล้วน / "ไม่มี" → PrereqEmpty
- "06016101" → PrereqLeaf("06016101")
- "06016101 และ 06016102" → PrereqAnd(...)
- "06016101 หรือ 06016102" → PrereqOr(...)
- "(06016101 และ 06016102) หรือ 06016103" → ซ้อนกัน

ขอบเขต (R8.5):
- ข้อความ ≤ 500 อักขระ
- รหัสวิชา ≤ 20 รายการต่อ expression
- ระดับซ้อน and/or ≤ 3 ระดับ

หลักการ:
- Parser เป็น deterministic — input เดียวกันได้ผลเดียวกันเสมอ
- round-trip: parse(print(x)) == x สำหรับทุก x ที่ valid (R8.6)
- เมื่อ parse ไม่ผ่าน คืน ParseFailure พร้อม error_index (0-based)
  โดยไม่บันทึกค่าบางส่วน (R8.9)

ไวยากรณ์ (precedence: AND binds tighter than OR):
  expr        ::= or_expr
  or_expr     ::= and_expr (OR and_expr)*
  and_expr    ::= atom (AND atom)*
  atom        ::= '(' expr ')' | course_code
  course_code ::= [^ ()และหรือandor]+  (trimmed, non-empty, ≤ 20 chars)
  AND         ::= 'และ' | 'and'  (case-insensitive)
  OR          ::= 'หรือ' | 'or'  (case-insensitive)

Canonical form (print):
- AND: " และ "
- OR: " หรือ "
- วงเล็บใส่เฉพาะเมื่อ child operator มี precedence ต่ำกว่า parent
  (เช่น OR ภายใน AND ต้องครอบวงเล็บ)
"""

from __future__ import annotations

from katrag.common.types import (
    PrereqAnd,
    PrereqEmpty,
    PrereqLeaf,
    PrereqNode,
    PrereqOr,
    prereq_codes,
    prereq_depth,
)
from katrag.errors import ParseFailure

# ── constants ─────────────────────────────────────────────────────────

_MAX_INPUT_LENGTH: int = 500
_MAX_COURSE_CODES: int = 20
_MAX_NESTING_DEPTH: int = 3

# Thai keywords that indicate empty prerequisite
_EMPTY_KEYWORDS: frozenset[str] = frozenset({"ไม่มี", "-"})

# Operator tokens (Thai and English)
_AND_TOKENS: tuple[str, ...] = ("และ", "and")
_OR_TOKENS: tuple[str, ...] = ("หรือ", "or")


# ── Prerequisite_Parser (public API) ──────────────────────────────────


def parse_prerequisite(text: str) -> PrereqNode | ParseFailure:
    """Parse ข้อความเงื่อนไขวิชาก่อนเป็นโครงสร้าง PrereqNode.

    Parameters
    ----------
    text : str
        ข้อความเงื่อนไขวิชาก่อน เช่น ``"06016101 และ 06016102"``

    Returns
    -------
    PrereqNode
        เมื่อ parse สำเร็จ
    ParseFailure
        เมื่อ parse ไม่สำเร็จ พร้อม error_index (0-based)
    """
    # Length constraint
    if len(text) > _MAX_INPUT_LENGTH:
        return ParseFailure(
            raw_text=text,
            error_index=_MAX_INPUT_LENGTH,
            reason=f"input exceeds {_MAX_INPUT_LENGTH} characters",
        )

    # Empty / whitespace-only → PrereqEmpty
    stripped = text.strip()
    if not stripped:
        return PrereqEmpty()

    # Thai keyword for "none"
    if stripped in _EMPTY_KEYWORDS:
        return PrereqEmpty()

    # Parse
    parser = _Parser(text)
    result = parser.parse_expr()
    if isinstance(result, ParseFailure):
        return result

    # Ensure entire input was consumed
    parser.skip_whitespace()
    if parser.pos < len(text):
        return ParseFailure(
            raw_text=text,
            error_index=parser.pos,
            reason="unexpected characters after expression",
        )

    # Validate constraints
    codes = prereq_codes(result)
    if len(codes) > _MAX_COURSE_CODES:
        return ParseFailure(
            raw_text=text,
            error_index=0,
            reason=f"too many course codes ({len(codes)} > {_MAX_COURSE_CODES})",
        )

    depth = prereq_depth(result)
    if depth > _MAX_NESTING_DEPTH:
        return ParseFailure(
            raw_text=text,
            error_index=0,
            reason=f"nesting depth ({depth}) exceeds maximum ({_MAX_NESTING_DEPTH})",
        )

    return result


# ── Prerequisite_Printer (public API) ─────────────────────────────────


def print_prerequisite(node: PrereqNode) -> str:
    """แปลง PrereqNode กลับเป็นข้อความ canonical form.

    round-trip property: parse_prerequisite(print_prerequisite(x)) == x
    สำหรับทุก x ที่ valid (R8.6)
    """
    if isinstance(node, PrereqEmpty):
        return ""
    if isinstance(node, PrereqLeaf):
        return node.code
    if isinstance(node, PrereqAnd):
        parts: list[str] = []
        for child in node.children:
            # OR inside AND needs parentheses
            if isinstance(child, PrereqOr):
                parts.append(f"({print_prerequisite(child)})")
            else:
                parts.append(print_prerequisite(child))
        return " และ ".join(parts)
    if isinstance(node, PrereqOr):
        parts = []
        for child in node.children:
            # AND inside OR does NOT need parentheses (AND binds tighter)
            parts.append(print_prerequisite(child))
        return " หรือ ".join(parts)
    raise TypeError(f"unknown PrereqNode type: {type(node)}")  # pragma: no cover


# ── Internal recursive-descent parser ─────────────────────────────────


class _Parser:
    """Recursive-descent parser for prerequisite expressions.

    Grammar (precedence: AND > OR):
      expr      ::= or_expr
      or_expr   ::= and_expr (OR and_expr)*
      and_expr  ::= atom (AND atom)*
      atom      ::= '(' expr ')' | course_code
    """

    __slots__ = ("text", "pos", "length")

    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0
        self.length = len(text)

    def skip_whitespace(self) -> None:
        while self.pos < self.length and self.text[self.pos] == " ":
            self.pos += 1

    def peek_operator(self) -> str | None:
        """Look ahead for an operator token without consuming it.

        Returns 'and', 'or', or None.
        """
        saved_pos = self.pos
        self.skip_whitespace()

        for token in _OR_TOKENS:
            if self._matches_operator(token):
                self.pos = saved_pos
                return "or"

        for token in _AND_TOKENS:
            if self._matches_operator(token):
                self.pos = saved_pos
                return "and"

        self.pos = saved_pos
        return None

    def consume_operator(self, kind: str) -> bool:
        """Try to consume an operator of the given kind ('and' or 'or').

        Returns True if consumed.
        """
        self.skip_whitespace()
        tokens = _AND_TOKENS if kind == "and" else _OR_TOKENS
        for token in tokens:
            if self._matches_operator(token):
                self.pos += len(token)
                return True
        return False

    def _matches_operator(self, token: str) -> bool:
        """Check if the text at current position matches token as a word boundary."""
        end = self.pos + len(token)
        if end > self.length:
            return False
        segment = self.text[self.pos : end]
        # Case-insensitive comparison for English operators
        if segment.lower() != token.lower():
            return False
        # Ensure it's not part of a larger word (for English tokens)
        if token.isascii():
            if end < self.length and self.text[end].isalnum():
                return False
        return True

    def parse_expr(self) -> PrereqNode | ParseFailure:
        """Parse top-level expression (or_expr)."""
        return self._parse_or_expr()

    def _parse_or_expr(self) -> PrereqNode | ParseFailure:
        """or_expr ::= and_expr (OR and_expr)*"""
        left = self._parse_and_expr()
        if isinstance(left, ParseFailure):
            return left

        children: list[PrereqNode] = [left]

        while True:
            op = self.peek_operator()
            if op != "or":
                break
            self.consume_operator("or")
            self.skip_whitespace()
            right = self._parse_and_expr()
            if isinstance(right, ParseFailure):
                return right
            children.append(right)

        if len(children) == 1:
            return children[0]
        return PrereqOr(children=tuple(children))

    def _parse_and_expr(self) -> PrereqNode | ParseFailure:
        """and_expr ::= atom (AND atom)*"""
        left = self._parse_atom()
        if isinstance(left, ParseFailure):
            return left

        children: list[PrereqNode] = [left]

        while True:
            op = self.peek_operator()
            if op != "and":
                break
            self.consume_operator("and")
            self.skip_whitespace()
            right = self._parse_atom()
            if isinstance(right, ParseFailure):
                return right
            children.append(right)

        if len(children) == 1:
            return children[0]
        return PrereqAnd(children=tuple(children))

    def _parse_atom(self) -> PrereqNode | ParseFailure:
        """atom ::= '(' expr ')' | course_code"""
        self.skip_whitespace()

        if self.pos >= self.length:
            return ParseFailure(
                raw_text=self.text,
                error_index=self.pos,
                reason="unexpected end of input",
            )

        # Parenthesized sub-expression
        if self.text[self.pos] == "(":
            self.pos += 1  # consume '('
            self.skip_whitespace()
            inner = self.parse_expr()
            if isinstance(inner, ParseFailure):
                return inner
            self.skip_whitespace()
            if self.pos >= self.length or self.text[self.pos] != ")":
                return ParseFailure(
                    raw_text=self.text,
                    error_index=self.pos,
                    reason="expected closing parenthesis ')'",
                )
            self.pos += 1  # consume ')'
            return inner

        # Closing paren without opening → error
        if self.text[self.pos] == ")":
            return ParseFailure(
                raw_text=self.text,
                error_index=self.pos,
                reason="unexpected closing parenthesis ')'",
            )

        # Course code
        return self._parse_course_code()

    def _parse_course_code(self) -> PrereqNode | ParseFailure:
        """Parse a course code token (non-whitespace, non-paren, non-operator)."""
        self.skip_whitespace()
        start = self.pos

        if self.pos >= self.length:
            return ParseFailure(
                raw_text=self.text,
                error_index=self.pos,
                reason="expected course code",
            )

        # Collect characters that are not whitespace, parentheses, or operators
        while self.pos < self.length:
            ch = self.text[self.pos]
            # Stop at space, parens
            if ch in (" ", "(", ")"):
                break
            # Check if current position starts an operator keyword
            if self._at_operator_boundary():
                break
            self.pos += 1

        code = self.text[start : self.pos]
        if not code:
            return ParseFailure(
                raw_text=self.text,
                error_index=start,
                reason="expected course code",
            )

        # Validate code length (1-20 chars per R8.1)
        if len(code) > 20:
            return ParseFailure(
                raw_text=self.text,
                error_index=start,
                reason=f"course code too long ({len(code)} > 20 characters)",
            )

        return PrereqLeaf(code=code)

    def _at_operator_boundary(self) -> bool:
        """Check if current position is at the start of an operator keyword
        preceded by whitespace context (used during code scanning).

        We only break if the operator would be valid in context — meaning
        the preceding char was space or start, and the char after is space/paren/end.
        """
        # Operators in prerequisite text are always surrounded by spaces
        # So during code scanning, we only need to check if we're at
        # a known Thai operator start that's not part of the code
        for token in _AND_TOKENS + _OR_TOKENS:
            end = self.pos + len(token)
            if end > self.length:
                continue
            segment = self.text[self.pos : end]
            if segment.lower() != token.lower():
                continue
            # For Thai operators: they're never part of a course code
            # (course codes are alphanumeric), so if segment matches
            # and is followed by space/paren/end, it's an operator
            if not token.isascii():
                # Thai token: check that it's followed by space, paren, or end
                if end >= self.length or self.text[end] in (" ", "(", ")"):
                    return True
            else:
                # English token: check word boundary after
                if end >= self.length or not self.text[end].isalnum():
                    return True
        return False
