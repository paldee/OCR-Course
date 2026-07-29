"""Version_Resolver — ตัดสินชุด curriculum version ที่คำถามอ้างถึง (R10.3, R10.4, R10.6).

ลำดับความสำคัญ:
1. พารามิเตอร์ที่ผู้ใช้ระบุชัดเจน (source = "request_parameter") — ชนะเสมอ
2. ค่าที่ตีความจากข้อความคำถาม (source = "question_text")
3. ค่าเริ่มต้น = ทุก version ที่มีอยู่ (source = "default_all")

ผลลัพธ์ deterministic: input เดียวกัน → output เดียวกัน ทุกครั้ง
เมื่อ resolve ได้ > 1 version → needs_clarification = True, ไม่เรียก Answer_Generator
เมื่อกรองแล้วไม่มี chunk → ตอบว่าไม่พบหลักฐาน ห้ามขยายไป version อื่น
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Mapping, Sequence

from katrag.common.types import CurriculumVersion

# ── known domain values ───────────────────────────────────────────────

KNOWN_PROGRAMS: frozenset[str] = frozenset({
    "IT", "BIT", "DSBA", "AIT",           # bachelor
    "M_IT", "M_AITBA",                    # master
    "PH_D_IT", "PH_D_AITBA",             # doctoral
})

KNOWN_YEARS: frozenset[int] = frozenset({
    2560, 2561, 2563, 2564, 2565, 2566, 2568, 2569,
})

KNOWN_EDITION_STATUSES: frozenset[str] = frozenset({"old", "current"})

# Alias mapping for text inference — Thai/English program name variants
_PROGRAM_ALIASES: dict[str, str] = {
    "it": "IT",
    "ไอที": "IT",
    "เทคโนโลยีสารสนเทศ": "IT",
    "bit": "BIT",
    "บิท": "BIT",
    "dsba": "DSBA",
    "ait": "AIT",
    "m_it": "M_IT",
    "m_aitba": "M_AITBA",
    "ph_d_it": "PH_D_IT",
    "ph_d_aitba": "PH_D_AITBA",
}

# Regex for year extraction (Buddhist era 4-digit years)
_YEAR_PATTERN = re.compile(r"\b(25(?:6[0-9]|7[0-9]))\b")

# Regex for program extraction — ordered longest first for greedy match
_PROGRAM_PATTERN = re.compile(
    r"\b("
    + "|".join(
        sorted(
            list(KNOWN_PROGRAMS) + list(_PROGRAM_ALIASES.keys()),
            key=len,
            reverse=True,
        )
    )
    + r")\b",
    re.IGNORECASE,
)

# Edition status keywords
_EDITION_KEYWORDS: dict[str, str] = {
    "หลักสูตรเก่า": "old",
    "เก่า": "old",
    "old": "old",
    "หลักสูตรปัจจุบัน": "current",
    "ปัจจุบัน": "current",
    "current": "current",
}


# ── result types ──────────────────────────────────────────────────────

VersionSource = Literal["request_parameter", "question_text", "default_all"]


@dataclass(frozen=True, slots=True)
class VersionResolution:
    """ผลลัพธ์ของ Version_Resolver.

    Attributes:
        versions: ชุด curriculum version ที่ตัดสินได้ (>= 1, sorted deterministically)
        source: แหล่งที่ใช้ตัดสิน
        evidence: รายละเอียดหลักฐานที่ใช้ตัดสิน (สำหรับ trace)
        needs_clarification: True เมื่อ |versions| > 1 → ต้องถามยืนยัน
        clarification_question: คำถามยืนยัน (None ถ้าไม่ต้องถาม)
    """

    versions: tuple[CurriculumVersion, ...]
    source: VersionSource
    evidence: Mapping[str, str]
    needs_clarification: bool
    clarification_question: str | None

    def __post_init__(self) -> None:
        if not self.versions:
            raise ValueError("versions ต้องมีอย่างน้อย 1 ค่า")
        if self.needs_clarification and self.clarification_question is None:
            raise ValueError("needs_clarification=True ต้องมี clarification_question")
        if not self.needs_clarification and self.clarification_question is not None:
            raise ValueError("needs_clarification=False ต้องไม่มี clarification_question")

    @property
    def resolved_versions(self) -> frozenset[CurriculumVersion]:
        """คืน frozenset สำหรับใช้เป็น filter key."""
        return frozenset(self.versions)

    @property
    def is_single(self) -> bool:
        """True เมื่อ resolve ได้เพียง version เดียว → พร้อมส่งต่อไป retriever."""
        return len(self.versions) == 1


@dataclass(frozen=True, slots=True)
class NoEvidenceResponse:
    """ผลลัพธ์เมื่อกรอง version แล้วไม่มี chunk เหลือ (R10.6).

    ไม่เรียก Answer_Generator, ไม่ขยายไป version อื่น.
    """

    message: str
    searched_versions: tuple[CurriculumVersion, ...]


# ── resolver ──────────────────────────────────────────────────────────


class VersionResolver:
    """ตัดสินชุด curriculum version สำหรับคำถามหนึ่ง.

    การทำงานเป็น deterministic — input เดียวกัน → output เดียวกัน
    ไม่มี randomness, ไม่มี side effect ที่ส่งผลต่อ output.
    """

    def __init__(
        self,
        available_versions: frozenset[CurriculumVersion],
    ) -> None:
        """สร้าง resolver พร้อมชุด version ทั้งหมดที่มีใน store.

        Args:
            available_versions: ชุด curriculum version ทั้งหมดที่มีข้อมูลอยู่ใน Provenance_Store
        """
        if not available_versions:
            raise ValueError("available_versions ต้องไม่ว่าง")
        # Sort deterministically for consistent default ordering
        self._available = tuple(
            sorted(available_versions, key=lambda v: v.key())
        )
        self._available_set = available_versions

    @property
    def available_versions(self) -> frozenset[CurriculumVersion]:
        """ชุด version ที่ resolver รู้จัก."""
        return self._available_set

    def resolve(
        self,
        question: str,
        requested: Sequence[CurriculumVersion] | None = None,
    ) -> VersionResolution:
        """ตัดสิน curriculum version สำหรับคำถาม.

        Priority (R10.3):
        1. requested (user parameter) — ชนะเสมอ
        2. text inference จากข้อความคำถาม
        3. default = ทุก available version

        Args:
            question: ข้อความคำถามจากผู้ใช้
            requested: curriculum version ที่ผู้ใช้ระบุเป็นพารามิเตอร์ (ถ้ามี)

        Returns:
            VersionResolution ที่มี versions >= 1 ค่า
        """
        # ── Priority 1: User parameter (R10.3 — ชนะเสมอ) ──
        if requested is not None and len(requested) > 0:
            # Filter to only versions that actually exist in the store
            valid_requested = tuple(
                v for v in requested if v in self._available_set
            )
            if valid_requested:
                versions = _sort_versions(valid_requested)
                evidence = {
                    "source": "request_parameter",
                    "detail": f"user specified {len(valid_requested)} version(s)",
                    "versions": ", ".join(v.label() for v in versions),
                }
                return self._make_resolution(
                    versions=versions,
                    source="request_parameter",
                    evidence=evidence,
                )

        # ── Priority 2: Text inference ──
        inferred = self._infer_from_text(question)
        if inferred:
            # Filter to only versions that exist in the store
            valid_inferred = tuple(
                v for v in inferred if v in self._available_set
            )
            if valid_inferred:
                versions = _sort_versions(valid_inferred)
                evidence = {
                    "source": "question_text",
                    "detail": f"inferred {len(valid_inferred)} version(s) from question",
                    "versions": ", ".join(v.label() for v in versions),
                    "question_excerpt": question[:100],
                }
                return self._make_resolution(
                    versions=versions,
                    source="question_text",
                    evidence=evidence,
                )

        # ── Priority 3: Default — all available versions ──
        evidence = {
            "source": "default_all",
            "detail": f"no version signal found, returning all {len(self._available)} versions",
        }
        return self._make_resolution(
            versions=self._available,
            source="default_all",
            evidence=evidence,
        )

    def _infer_from_text(self, question: str) -> list[CurriculumVersion]:
        """ตีความ curriculum version จากข้อความคำถาม.

        จับ program name, year, edition status จากข้อความ
        แล้วสร้าง combinations ที่เป็นไปได้.
        """
        programs = self._extract_programs(question)
        years = self._extract_years(question)
        edition = self._extract_edition_status(question)

        if not programs and not years and not edition:
            return []

        # Build candidate versions from extracted signals
        candidates: list[CurriculumVersion] = []

        if programs and years:
            # Both program and year specified — try all combinations with edition
            for prog in programs:
                for year in years:
                    if edition:
                        candidates.append(
                            CurriculumVersion(
                                program=prog,
                                curriculum_year=year,
                                edition_status=edition,
                            )
                        )
                    else:
                        # Try both edition statuses
                        for status in sorted(KNOWN_EDITION_STATUSES):
                            candidates.append(
                                CurriculumVersion(
                                    program=prog,
                                    curriculum_year=year,
                                    edition_status=status,
                                )
                            )
        elif programs:
            # Only program — match any version with that program
            for v in self._available:
                if v.program in programs:
                    if edition and v.edition_status != edition:
                        continue
                    candidates.append(v)
        elif years:
            # Only year — match any version with that year
            for v in self._available:
                if v.curriculum_year in years:
                    if edition and v.edition_status != edition:
                        continue
                    candidates.append(v)
        elif edition:
            # Only edition status — match all versions with that status
            for v in self._available:
                if v.edition_status == edition:
                    candidates.append(v)

        return candidates

    def _extract_programs(self, text: str) -> list[str]:
        """จับชื่อหลักสูตรจากข้อความ — deterministic ordering."""
        found: list[str] = []
        seen: set[str] = set()

        for match in _PROGRAM_PATTERN.finditer(text):
            raw = match.group(1)
            # Normalize via alias or uppercase match
            canonical = _PROGRAM_ALIASES.get(raw.lower())
            if canonical is None:
                canonical = raw.upper() if raw.upper() in KNOWN_PROGRAMS else None
            if canonical and canonical not in seen:
                seen.add(canonical)
                found.append(canonical)

        # Sort for determinism
        return sorted(found)

    def _extract_years(self, text: str) -> list[int]:
        """จับปีพุทธศักราชจากข้อความ — only known years."""
        found: set[int] = set()
        for match in _YEAR_PATTERN.finditer(text):
            year = int(match.group(1))
            if year in KNOWN_YEARS:
                found.add(year)
        return sorted(found)

    def _extract_edition_status(self, text: str) -> str | None:
        """จับสถานะหลักสูตร (old/current) จากข้อความ.

        ค้นจาก keyword ยาวที่สุดก่อน (greedy) เพื่อ determinism.
        """
        # Search longest keywords first for greedy matching
        for keyword in sorted(_EDITION_KEYWORDS.keys(), key=len, reverse=True):
            if keyword in text.lower():
                return _EDITION_KEYWORDS[keyword]
        return None

    def _make_resolution(
        self,
        versions: tuple[CurriculumVersion, ...],
        source: VersionSource,
        evidence: Mapping[str, str],
    ) -> VersionResolution:
        """สร้าง VersionResolution พร้อมคำถามยืนยันเมื่อจำเป็น (R10.4)."""
        needs_clarification = len(versions) > 1
        clarification_question: str | None = None

        if needs_clarification:
            options = "\n".join(
                f"  - {v.label()}" for v in versions
            )
            clarification_question = (
                "พบหลักสูตรที่เป็นไปได้หลายค่า กรุณาระบุหลักสูตรที่ต้องการ:\n"
                f"{options}"
            )

        return VersionResolution(
            versions=versions,
            source=source,
            evidence=evidence,
            needs_clarification=needs_clarification,
            clarification_question=clarification_question,
        )


# ── helper for no-evidence response (R10.6) ──────────────────────────


def make_no_evidence_response(
    versions: tuple[CurriculumVersion, ...] | frozenset[CurriculumVersion],
) -> NoEvidenceResponse:
    """สร้างข้อความ "ไม่พบหลักฐาน" เมื่อกรองแล้วไม่มี chunk (R10.6).

    ไม่ขยายไป version อื่น, ไม่เรียก Answer_Generator.
    """
    if isinstance(versions, frozenset):
        sorted_versions = tuple(sorted(versions, key=lambda v: v.key()))
    else:
        sorted_versions = versions

    version_labels = ", ".join(v.label() for v in sorted_versions)
    message = (
        f"ไม่พบหลักฐานในหลักสูตรที่ค้น ({version_labels}) "
        "กรุณาตรวจสอบว่าหลักสูตรและปีที่ระบุถูกต้อง"
    )

    return NoEvidenceResponse(
        message=message,
        searched_versions=sorted_versions,
    )


# ── utilities ─────────────────────────────────────────────────────────


def _sort_versions(
    versions: tuple[CurriculumVersion, ...] | list[CurriculumVersion],
) -> tuple[CurriculumVersion, ...]:
    """เรียง version ด้วย key() เพื่อ determinism."""
    # Deduplicate then sort
    unique = list(dict.fromkeys(versions))  # preserves first occurrence, dedupes
    return tuple(sorted(unique, key=lambda v: v.key()))
