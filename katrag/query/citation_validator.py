"""Citation_Validator — ตรวจสอบความถูกต้องของ citation ในคำตอบ (R17.3, R17.4, R17.5, R10.8, R15.6).

Design:
- Split answer → claim units (sentence/bullet boundaries)
- ตรวจทุก citation ID ว่าตรงกับ issued set (R17.3)
- ลบ claim units ที่อ้าง unknown ID — เก็บ valid ones, คืน removal count (R17.4)
- Mark factual claims ที่ไม่มี citation ว่า "unsupported_claim" (R17.5)
- Reject คำตอบทั้งฉบับถ้า citation ข้ามเวอร์ชัน + บันทึก trace (R10.8)
- Replace numeric values ด้วย reasoner output เมื่อคำตอบไม่ตรง (R15.6)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Sequence

from katrag.common.types import CitationId, CurriculumVersion
from katrag.errors import CrossVersionCitationError
from katrag.query.answer_generator import ReasonerValue
from katrag.query.citation import CitationRegistry


# ══════════════════════════════════════════════════════════════════════
# Data types
# ══════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class ClaimUnit:
    """หนึ่งหน่วยข้อกล่าวอ้างในคำตอบ."""

    text: str
    citation_ids: tuple[str, ...]
    is_unsupported: bool = False

    @property
    def has_citations(self) -> bool:
        return len(self.citation_ids) > 0


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """ผลลัพธ์จาก Citation_Validator.validate()."""

    validated_answer: str
    claim_units: tuple[ClaimUnit, ...]
    citations_passed: int
    citations_removed: int
    unsupported_claims: int
    numeric_replacements: int
    cross_version_violation: bool
    cross_version_ids: tuple[str, ...] = ()


# ══════════════════════════════════════════════════════════════════════
# Constants
# ══════════════════════════════════════════════════════════════════════

# Pattern to match citation IDs in text like [cite-001] or [cite-042]
_CITATION_PATTERN = re.compile(r"\[cite-\d{3,}\]")

# Pattern to extract just the ID value
_CITATION_ID_PATTERN = re.compile(r"\[(cite-\d{3,})\]")

# Sentence/bullet splitting pattern
_CLAIM_SPLIT_PATTERN = re.compile(
    r"(?<=[.!?。])\s+|(?=^[\-\*•]\s)", re.MULTILINE
)


# ══════════════════════════════════════════════════════════════════════
# Citation Validator
# ══════════════════════════════════════════════════════════════════════


class CitationValidator:
    """ตรวจสอบ citation ในคำตอบ LLM.

    ผู้ใช้สร้าง validator ต่อ request พร้อม registry ของ request นั้น.
    """

    __slots__ = ("_registry", "_version_map")

    def __init__(
        self,
        registry: CitationRegistry,
        version_map: dict[str, CurriculumVersion] | None = None,
    ) -> None:
        """สร้าง validator พร้อม citation registry ของ request.

        Args:
            registry: CitationRegistry ของ request ปัจจุบัน (issued set)
            version_map: mapping citation_id_value → CurriculumVersion
                         สำหรับตรวจ cross-version violation (R10.8)
        """
        self._registry = registry
        self._version_map = version_map or {}

    def validate(
        self,
        answer_text: str,
        reasoner_values: list[ReasonerValue] | None = None,
        allowed_versions: frozenset[CurriculumVersion] | None = None,
    ) -> ValidationResult:
        """ตรวจสอบ citation ทั้งหมดในคำตอบ.

        Args:
            answer_text: คำตอบดิบจาก LLM
            reasoner_values: ค่าเชิงตัวเลขจาก Reasoner สำหรับตรวจ numeric (R15.6)
            allowed_versions: ชุด version ที่อนุญาต (สำหรับ cross-version check)

        Returns:
            ValidationResult พร้อม validated answer

        Raises:
            CrossVersionCitationError: เมื่อ citation ข้ามเวอร์ชัน (R10.8)
        """
        # ── Step 1: Split into claim units ──
        raw_claims = self._split_into_claims(answer_text)

        # ── Step 2: Check cross-version violations (R10.8) ──
        if allowed_versions and self._version_map:
            cross_ids = self._check_cross_version(answer_text, allowed_versions)
            if cross_ids:
                raise CrossVersionCitationError(tuple(cross_ids))

        # ── Step 3: Validate each claim unit ──
        issued_set = {str(cid) for cid in self._registry.all_ids()}
        valid_claims: list[ClaimUnit] = []
        removed_count = 0
        unsupported_count = 0

        for raw_claim in raw_claims:
            if not raw_claim.strip():
                continue

            # Extract citation IDs from this claim
            found_ids = _CITATION_ID_PATTERN.findall(raw_claim)

            if not found_ids:
                # No citations → mark as unsupported claim (R17.5)
                # Only if it looks like a factual claim (not just a header/question)
                if self._is_factual_claim(raw_claim):
                    valid_claims.append(ClaimUnit(
                        text=raw_claim.strip(),
                        citation_ids=(),
                        is_unsupported=True,
                    ))
                    unsupported_count += 1
                else:
                    # Not a factual claim (header, question echo, etc.) — keep as-is
                    valid_claims.append(ClaimUnit(
                        text=raw_claim.strip(),
                        citation_ids=(),
                        is_unsupported=False,
                    ))
                continue

            # Check if all citation IDs are in the issued set (R17.3)
            valid_ids = [cid for cid in found_ids if cid in issued_set]
            invalid_ids = [cid for cid in found_ids if cid not in issued_set]

            if invalid_ids and not valid_ids:
                # All citations unknown → remove entire claim (R17.4)
                removed_count += 1
                continue
            elif invalid_ids:
                # Some citations unknown → remove the unknown ones from text
                cleaned_text = raw_claim
                for inv_id in invalid_ids:
                    cleaned_text = cleaned_text.replace(f"[{inv_id}]", "")
                removed_count += 1  # Count as one removal event
                valid_claims.append(ClaimUnit(
                    text=cleaned_text.strip(),
                    citation_ids=tuple(valid_ids),
                    is_unsupported=False,
                ))
            else:
                # All citations valid
                valid_claims.append(ClaimUnit(
                    text=raw_claim.strip(),
                    citation_ids=tuple(valid_ids),
                    is_unsupported=False,
                ))

        # ── Step 4: Numeric replacement (R15.6) ──
        numeric_replacements = 0
        if reasoner_values:
            valid_claims, numeric_replacements = self._replace_numerics(
                valid_claims, reasoner_values
            )

        # ── Step 5: Reconstruct validated answer ──
        validated_answer = "\n".join(c.text for c in valid_claims if c.text)
        citations_passed = sum(len(c.citation_ids) for c in valid_claims)

        return ValidationResult(
            validated_answer=validated_answer,
            claim_units=tuple(valid_claims),
            citations_passed=citations_passed,
            citations_removed=removed_count,
            unsupported_claims=unsupported_count,
            numeric_replacements=numeric_replacements,
            cross_version_violation=False,
        )

    def _split_into_claims(self, text: str) -> list[str]:
        """Split answer into claim units by sentence/bullet boundaries."""
        # First split by newlines (bullet points, paragraphs)
        lines = text.split("\n")
        claims: list[str] = []

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # If it's a bullet point, keep as single claim
            if line.startswith(("-", "*", "•", "–")):
                claims.append(line)
                continue

            # Split by sentence boundaries (period, question mark, exclamation)
            sentences = re.split(r"(?<=[.!?。])\s+", line)
            for sent in sentences:
                sent = sent.strip()
                if sent:
                    claims.append(sent)

        return claims

    def _is_factual_claim(self, text: str) -> bool:
        """ตัดสินว่า text เป็น factual claim ที่ต้องมี citation หรือไม่.

        Non-factual: headers (==), questions, short connectors, section dividers.
        """
        stripped = text.strip()

        # Headers / section markers
        if stripped.startswith("==") or stripped.startswith("#"):
            return False

        # Very short text (connectors like "ดังนั้น", "นอกจากนี้")
        if len(stripped) < 10:
            return False

        # Question echo
        if stripped.endswith("?") or stripped.startswith("คำถาม"):
            return False

        # Version labels (just stating which version)
        if stripped.startswith("หลักสูตร") and len(stripped) < 50:
            return False

        return True

    def _check_cross_version(
        self,
        text: str,
        allowed_versions: frozenset[CurriculumVersion],
    ) -> list[str]:
        """ตรวจหา citation ที่อ้าง version นอกชุดที่อนุญาต (R10.8)."""
        found_ids = _CITATION_ID_PATTERN.findall(text)
        violations: list[str] = []

        for cite_id in found_ids:
            if cite_id in self._version_map:
                version = self._version_map[cite_id]
                if version not in allowed_versions:
                    violations.append(cite_id)

        return violations

    def _replace_numerics(
        self,
        claims: list[ClaimUnit],
        reasoner_values: list[ReasonerValue],
    ) -> tuple[list[ClaimUnit], int]:
        """Replace numeric values ที่ไม่ตรงกับ reasoner output (R15.6).

        Only replaces when a numeric value in the claim is WRONG (differs from
        reasoner value). If the correct value is already present, no replacement.

        Returns:
            (updated claims, replacement count)
        """
        replacements = 0
        updated_claims: list[ClaimUnit] = []

        for claim in claims:
            new_text = claim.text
            for rv in reasoner_values:
                # ตรวจว่า claim กล่าวถึง label ของ reasoner value
                if rv.label.lower() not in new_text.lower():
                    continue

                expected = str(rv.value)

                # หา numeric values ใน claim ที่อยู่ใกล้ label
                numbers_in_text = re.findall(r"\d+(?:\.\d+)?", new_text)

                # ถ้า expected value อยู่ใน claim อยู่แล้ว → ไม่ต้อง replace
                if expected in numbers_in_text:
                    continue

                # มี numeric ที่ไม่ตรงกับ reasoner → replace ตัวที่อยู่ใกล้ label ที่สุด
                for num in numbers_in_text:
                    if num != expected and self._is_same_context(new_text, num, rv.label):
                        new_text = new_text.replace(num, expected, 1)
                        replacements += 1
                        break

            updated_claims.append(ClaimUnit(
                text=new_text,
                citation_ids=claim.citation_ids,
                is_unsupported=claim.is_unsupported,
            ))

        return updated_claims, replacements

    def _is_same_context(self, text: str, number: str, label: str) -> bool:
        """ตรวจว่า number อยู่ในบริบทเดียวกับ label (proximity check)."""
        label_pos = text.lower().find(label.lower())
        num_pos = text.find(number)
        if label_pos < 0 or num_pos < 0:
            return False
        # Within 100 characters of each other
        return abs(label_pos - num_pos) < 100
