"""Unit tests for katrag.query.version_resolver (R10.3, R10.4, R10.6).

ทดสอบ:
- R10.3: พารามิเตอร์ผู้ใช้ชนะค่าที่ตีความจากข้อความ, ผลลัพธ์ deterministic
- R10.4: คืนคำถามยืนยันเมื่อ resolve ได้ > 1 version, ไม่เรียก Answer_Generator
- R10.6: ตอบ "ไม่พบหลักฐาน" เมื่อกรองแล้วไม่มี chunk, ไม่ขยายไป version อื่น
- Determinism: input เดียวกัน → output เดียวกัน ทุกครั้ง
- Trace evidence: บันทึกแหล่งที่ใช้ตัดสินลง evidence
"""

from __future__ import annotations

import pytest

from katrag.common.types import CurriculumVersion
from katrag.query.version_resolver import (
    NoEvidenceResponse,
    VersionResolution,
    VersionResolver,
    make_no_evidence_response,
)


# ── fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def versions_pool() -> frozenset[CurriculumVersion]:
    """ชุด version ตัวอย่างที่จำลอง Provenance_Store."""
    return frozenset({
        CurriculumVersion(program="IT", curriculum_year=2565, edition_status="current"),
        CurriculumVersion(program="IT", curriculum_year=2560, edition_status="old"),
        CurriculumVersion(program="BIT", curriculum_year=2565, edition_status="current"),
        CurriculumVersion(program="DSBA", curriculum_year=2566, edition_status="current"),
        CurriculumVersion(program="AIT", curriculum_year=2564, edition_status="current"),
        CurriculumVersion(program="M_IT", curriculum_year=2563, edition_status="current"),
        CurriculumVersion(program="PH_D_IT", curriculum_year=2565, edition_status="current"),
    })


@pytest.fixture
def resolver(versions_pool: frozenset[CurriculumVersion]) -> VersionResolver:
    """สร้าง resolver พร้อม pool ตัวอย่าง."""
    return VersionResolver(available_versions=versions_pool)


# ── R10.3: user parameter wins ────────────────────────────────────────


class TestUserParameterPriority:
    """R10.3: พารามิเตอร์ผู้ใช้ชนะค่าที่ตีความจากข้อความเมื่อขัดกัน."""

    def test_user_parameter_overrides_text_inference(
        self, resolver: VersionResolver
    ) -> None:
        """ข้อความมี 'IT 2565' แต่ user ระบุ BIT 2565 → ใช้ BIT 2565."""
        user_version = CurriculumVersion(
            program="BIT", curriculum_year=2565, edition_status="current"
        )
        result = resolver.resolve(
            question="หลักสูตร IT 2565 มีวิชาอะไรบ้าง",
            requested=[user_version],
        )

        assert len(result.versions) == 1
        assert result.versions[0] == user_version
        assert result.source == "request_parameter"
        assert not result.needs_clarification
        assert result.clarification_question is None

    def test_user_parameter_single_version(
        self, resolver: VersionResolver
    ) -> None:
        """User ระบุ version เดียว → ใช้เลย, source = request_parameter."""
        version = CurriculumVersion(
            program="IT", curriculum_year=2565, edition_status="current"
        )
        result = resolver.resolve(
            question="วิชาบังคับมีอะไรบ้าง",
            requested=[version],
        )

        assert result.versions == (version,)
        assert result.source == "request_parameter"
        assert result.is_single

    def test_user_parameter_multiple_versions(
        self, resolver: VersionResolver
    ) -> None:
        """User ระบุหลาย version → ใช้ทั้งหมดแต่ needs_clarification."""
        v1 = CurriculumVersion(program="IT", curriculum_year=2565, edition_status="current")
        v2 = CurriculumVersion(program="BIT", curriculum_year=2565, edition_status="current")

        result = resolver.resolve(
            question="เทียบวิชาบังคับ",
            requested=[v1, v2],
        )

        assert len(result.versions) == 2
        assert result.source == "request_parameter"
        assert result.needs_clarification is True
        assert result.clarification_question is not None

    def test_user_parameter_invalid_version_filtered(
        self, resolver: VersionResolver
    ) -> None:
        """User ระบุ version ที่ไม่อยู่ใน store → กรองออก, fallback ไป text inference."""
        invalid_version = CurriculumVersion(
            program="ZZZZ", curriculum_year=2599, edition_status="current"
        )
        # Question mentions IT 2565
        result = resolver.resolve(
            question="หลักสูตร IT 2565",
            requested=[invalid_version],
        )

        # Should fall through to text inference since invalid version is filtered
        assert result.source == "question_text"

    def test_user_parameter_empty_list_uses_text_inference(
        self, resolver: VersionResolver
    ) -> None:
        """User ส่ง list ว่าง → ใช้ text inference."""
        result = resolver.resolve(
            question="หลักสูตร IT 2565 มีวิชาอะไร",
            requested=[],
        )

        assert result.source == "question_text"

    def test_user_parameter_none_uses_text_inference(
        self, resolver: VersionResolver
    ) -> None:
        """requested=None → ใช้ text inference."""
        result = resolver.resolve(
            question="หลักสูตร IT 2565 มีวิชาอะไร",
            requested=None,
        )

        assert result.source == "question_text"


# ── R10.3: text inference ─────────────────────────────────────────────


class TestTextInference:
    """R10.3: ตีความ version จากข้อความคำถาม."""

    def test_infer_program_and_year(self, resolver: VersionResolver) -> None:
        """ข้อความมีทั้ง program และ year → จับได้."""
        result = resolver.resolve(
            question="หลักสูตร IT ปี 2565 มีวิชาอะไร",
        )

        assert result.source == "question_text"
        # Should match IT 2565 current (exists in pool)
        assert any(
            v.program == "IT" and v.curriculum_year == 2565
            for v in result.versions
        )

    def test_infer_program_only(self, resolver: VersionResolver) -> None:
        """ข้อความมีแค่ program → จับได้ทุก version ของ program นั้น."""
        result = resolver.resolve(question="หลักสูตร IT มีกี่วิชา")

        assert result.source == "question_text"
        # Should match all IT versions in pool
        for v in result.versions:
            assert v.program == "IT"

    def test_infer_year_only(self, resolver: VersionResolver) -> None:
        """ข้อความมีแค่ year → จับได้ทุก version ของปีนั้น."""
        result = resolver.resolve(question="หลักสูตรปี 2565 ทั้งหมด")

        assert result.source == "question_text"
        for v in result.versions:
            assert v.curriculum_year == 2565

    def test_infer_with_edition_status(self, resolver: VersionResolver) -> None:
        """ข้อความระบุ edition เป็น 'หลักสูตรเก่า' → filter ด้วย old."""
        result = resolver.resolve(
            question="หลักสูตรเก่า IT 2560 มีวิชาอะไร",
        )

        assert result.source == "question_text"
        assert len(result.versions) >= 1
        for v in result.versions:
            assert v.edition_status == "old"

    def test_no_signal_returns_default_all(
        self, resolver: VersionResolver
    ) -> None:
        """ข้อความไม่มี signal ใด → คืนทุก version, source = default_all."""
        result = resolver.resolve(question="มีวิชาอะไรบ้างคะ")

        assert result.source == "default_all"
        assert len(result.versions) == len(resolver.available_versions)

    def test_infer_thai_program_alias(self, resolver: VersionResolver) -> None:
        """จับชื่อหลักสูตรภาษาไทย เช่น 'เทคโนโลยีสารสนเทศ' → IT."""
        result = resolver.resolve(
            question="เทคโนโลยีสารสนเทศ 2565 มีวิชาอะไร",
        )

        assert result.source == "question_text"
        assert any(v.program == "IT" for v in result.versions)

    def test_infer_year_not_in_known_set_ignored(
        self, resolver: VersionResolver
    ) -> None:
        """ปีที่ไม่อยู่ใน known set จะถูกเพิกเฉย."""
        result = resolver.resolve(question="หลักสูตรปี 2570")

        # 2570 is not in KNOWN_YEARS, so no year signal
        # but no program signal either → default_all
        assert result.source == "default_all"


# ── R10.3: determinism ────────────────────────────────────────────────


class TestDeterminism:
    """R10.3: ผลลัพธ์ต้อง deterministic — input เดียวกัน → output เดียวกัน."""

    def test_same_input_same_output(self, resolver: VersionResolver) -> None:
        """เรียก 10 ครั้งด้วย input เดิม → ผลเหมือนกันทุกครั้ง."""
        question = "หลักสูตร IT 2565 มีวิชาอะไร"
        first = resolver.resolve(question)

        for _ in range(10):
            result = resolver.resolve(question)
            assert result.versions == first.versions
            assert result.source == first.source
            assert result.needs_clarification == first.needs_clarification

    def test_deterministic_with_user_parameter(
        self, resolver: VersionResolver
    ) -> None:
        """User parameter → ผล deterministic."""
        versions = [
            CurriculumVersion(program="IT", curriculum_year=2565, edition_status="current"),
            CurriculumVersion(program="BIT", curriculum_year=2565, edition_status="current"),
        ]
        first = resolver.resolve("test", requested=versions)

        for _ in range(10):
            result = resolver.resolve("test", requested=versions)
            assert result.versions == first.versions

    def test_version_ordering_is_sorted(self, resolver: VersionResolver) -> None:
        """ลำดับ version ในผลลัพธ์ต้อง sorted ด้วย key()."""
        result = resolver.resolve(question="หลักสูตร 2565")

        keys = [v.key() for v in result.versions]
        assert keys == sorted(keys)


# ── R10.3: trace evidence ─────────────────────────────────────────────


class TestTraceEvidence:
    """R10.3: บันทึกแหล่งที่ใช้ตัดสินลง trace (evidence field)."""

    def test_evidence_has_source_field(self, resolver: VersionResolver) -> None:
        """Evidence mapping ต้องมี 'source' key."""
        result = resolver.resolve(question="IT 2565")

        assert "source" in result.evidence
        assert result.evidence["source"] == result.source

    def test_evidence_has_detail(self, resolver: VersionResolver) -> None:
        """Evidence mapping ต้องมี 'detail' key."""
        result = resolver.resolve(question="IT 2565")

        assert "detail" in result.evidence
        assert isinstance(result.evidence["detail"], str)
        assert len(result.evidence["detail"]) > 0

    def test_evidence_request_parameter_source(
        self, resolver: VersionResolver
    ) -> None:
        """Source request_parameter → evidence บอกว่ามาจาก user."""
        version = CurriculumVersion(
            program="IT", curriculum_year=2565, edition_status="current"
        )
        result = resolver.resolve("test", requested=[version])

        assert result.evidence["source"] == "request_parameter"

    def test_evidence_text_inference_includes_excerpt(
        self, resolver: VersionResolver
    ) -> None:
        """Source question_text → evidence มี question_excerpt."""
        result = resolver.resolve(question="หลักสูตร IT 2565")

        assert result.evidence["source"] == "question_text"
        assert "question_excerpt" in result.evidence

    def test_evidence_default_all_explains(
        self, resolver: VersionResolver
    ) -> None:
        """Source default_all → evidence อธิบายว่าไม่พบ signal."""
        result = resolver.resolve(question="สวัสดี")

        assert result.evidence["source"] == "default_all"
        assert "no version signal" in result.evidence["detail"]


# ── R10.4: clarification question ────────────────────────────────────


class TestClarification:
    """R10.4: คืนคำถามยืนยันเมื่อ resolve ได้มากกว่า 1 version."""

    def test_multiple_versions_needs_clarification(
        self, resolver: VersionResolver
    ) -> None:
        """Resolve ได้ > 1 → needs_clarification = True."""
        # IT has 2 versions in pool (2565 current, 2560 old)
        result = resolver.resolve(question="หลักสูตร IT มีวิชาอะไร")

        assert len(result.versions) > 1
        assert result.needs_clarification is True

    def test_clarification_question_lists_all_versions(
        self, resolver: VersionResolver
    ) -> None:
        """คำถามยืนยันต้องระบุทุก version ที่เป็นไปได้."""
        result = resolver.resolve(question="หลักสูตร IT มีวิชาอะไร")

        assert result.clarification_question is not None
        for v in result.versions:
            # Each version's label should appear in the question
            assert v.label() in result.clarification_question

    def test_clarification_question_contains_program_year_edition(
        self, resolver: VersionResolver
    ) -> None:
        """คำถามยืนยันต้องแสดง program, curriculum_year, edition_status."""
        result = resolver.resolve(question="หลักสูตร IT มีวิชาอะไร")

        assert result.clarification_question is not None
        # Should contain program names and years
        for v in result.versions:
            assert v.program in result.clarification_question
            assert str(v.curriculum_year) in result.clarification_question
            assert v.edition_status in result.clarification_question

    def test_single_version_no_clarification(
        self, resolver: VersionResolver
    ) -> None:
        """Resolve ได้ 1 version → needs_clarification = False."""
        version = CurriculumVersion(
            program="IT", curriculum_year=2565, edition_status="current"
        )
        result = resolver.resolve("test", requested=[version])

        assert result.needs_clarification is False
        assert result.clarification_question is None

    def test_clarification_preserves_original_question(
        self, resolver: VersionResolver
    ) -> None:
        """คำถามยืนยันไม่ทำลายข้อความคำถามเดิม (R10.4 — คงไว้)."""
        question = "หลักสูตร IT มีวิชาอะไรบ้าง"
        result = resolver.resolve(question=question)

        # The resolver returns clarification but the question text is unchanged
        # (caller responsible for preserving it — we verify resolver doesn't modify)
        assert result.needs_clarification is True
        # The clarification question is a NEW string, not replacing the original
        assert result.clarification_question != question


# ── R10.6: no evidence response ──────────────────────────────────────


class TestNoEvidenceResponse:
    """R10.6: ตอบว่าไม่พบหลักฐานเมื่อกรองแล้วไม่มี chunk เหลือ."""

    def test_no_evidence_message_contains_version(self) -> None:
        """ข้อความ no_evidence ต้องระบุ version ที่ค้น."""
        version = CurriculumVersion(
            program="IT", curriculum_year=2565, edition_status="current"
        )
        response = make_no_evidence_response((version,))

        assert "IT" in response.message
        assert "2565" in response.message
        assert "current" in response.message

    def test_no_evidence_contains_not_found_message(self) -> None:
        """ข้อความ no_evidence ต้องบอกว่าไม่พบ."""
        version = CurriculumVersion(
            program="IT", curriculum_year=2565, edition_status="current"
        )
        response = make_no_evidence_response((version,))

        assert "ไม่พบหลักฐาน" in response.message

    def test_no_evidence_preserves_searched_versions(self) -> None:
        """searched_versions ต้องตรงกับ input."""
        v1 = CurriculumVersion(program="IT", curriculum_year=2565, edition_status="current")
        v2 = CurriculumVersion(program="BIT", curriculum_year=2565, edition_status="current")

        response = make_no_evidence_response((v1, v2))

        assert v1 in response.searched_versions
        assert v2 in response.searched_versions

    def test_no_evidence_does_not_expand_to_other_versions(self) -> None:
        """ห้ามขยายไป version อื่น — searched_versions = เฉพาะที่ค้น."""
        version = CurriculumVersion(
            program="IT", curriculum_year=2565, edition_status="current"
        )
        response = make_no_evidence_response((version,))

        # Only the searched version is in the response
        assert len(response.searched_versions) == 1
        assert response.searched_versions[0] == version

    def test_no_evidence_from_frozenset(self) -> None:
        """รับ frozenset ได้ — ผลลัพธ์ sorted deterministically."""
        v1 = CurriculumVersion(program="IT", curriculum_year=2565, edition_status="current")
        v2 = CurriculumVersion(program="BIT", curriculum_year=2565, edition_status="current")

        response = make_no_evidence_response(frozenset({v1, v2}))

        # Should be sorted by key()
        assert response.searched_versions == tuple(
            sorted([v1, v2], key=lambda v: v.key())
        )

    def test_no_evidence_multiple_versions_message(self) -> None:
        """หลาย version → ข้อความระบุทุก version."""
        v1 = CurriculumVersion(program="IT", curriculum_year=2565, edition_status="current")
        v2 = CurriculumVersion(program="BIT", curriculum_year=2565, edition_status="current")

        response = make_no_evidence_response((v1, v2))

        assert "IT" in response.message
        assert "BIT" in response.message


# ── VersionResolution validation ──────────────────────────────────────


class TestVersionResolutionValidation:
    """VersionResolution dataclass validation."""

    def test_empty_versions_raises(self) -> None:
        """versions ว่าง → ValueError."""
        with pytest.raises(ValueError, match="ต้องมีอย่างน้อย 1"):
            VersionResolution(
                versions=(),
                source="default_all",
                evidence={"source": "default_all", "detail": "test"},
                needs_clarification=False,
                clarification_question=None,
            )

    def test_needs_clarification_without_question_raises(self) -> None:
        """needs_clarification=True + clarification_question=None → ValueError."""
        v = CurriculumVersion(program="IT", curriculum_year=2565, edition_status="current")
        with pytest.raises(ValueError, match="ต้องมี clarification_question"):
            VersionResolution(
                versions=(v,),
                source="default_all",
                evidence={"source": "default_all", "detail": "test"},
                needs_clarification=True,
                clarification_question=None,
            )

    def test_no_clarification_with_question_raises(self) -> None:
        """needs_clarification=False + clarification_question != None → ValueError."""
        v = CurriculumVersion(program="IT", curriculum_year=2565, edition_status="current")
        with pytest.raises(ValueError, match="ต้องไม่มี clarification_question"):
            VersionResolution(
                versions=(v,),
                source="default_all",
                evidence={"source": "default_all", "detail": "test"},
                needs_clarification=False,
                clarification_question="unnecessary question",
            )

    def test_resolved_versions_returns_frozenset(self) -> None:
        """resolved_versions property returns frozenset."""
        v = CurriculumVersion(program="IT", curriculum_year=2565, edition_status="current")
        resolution = VersionResolution(
            versions=(v,),
            source="request_parameter",
            evidence={"source": "request_parameter", "detail": "test"},
            needs_clarification=False,
            clarification_question=None,
        )

        assert resolution.resolved_versions == frozenset({v})
        assert isinstance(resolution.resolved_versions, frozenset)


# ── VersionResolver initialization ───────────────────────────────────


class TestResolverInit:
    """VersionResolver constructor validation."""

    def test_empty_available_versions_raises(self) -> None:
        """available_versions ว่าง → ValueError."""
        with pytest.raises(ValueError, match="ต้องไม่ว่าง"):
            VersionResolver(available_versions=frozenset())

    def test_available_versions_property(
        self, resolver: VersionResolver, versions_pool: frozenset[CurriculumVersion]
    ) -> None:
        """available_versions property returns the frozenset."""
        assert resolver.available_versions == versions_pool


# ── edge cases ────────────────────────────────────────────────────────


class TestEdgeCases:
    """Edge cases for the resolver."""

    def test_duplicate_user_versions_deduplicated(
        self, resolver: VersionResolver
    ) -> None:
        """User ส่ง version ซ้ำ → deduplicate."""
        v = CurriculumVersion(program="IT", curriculum_year=2565, edition_status="current")
        result = resolver.resolve("test", requested=[v, v, v])

        assert len(result.versions) == 1
        assert result.versions[0] == v
        assert not result.needs_clarification

    def test_mixed_valid_invalid_user_versions(
        self, resolver: VersionResolver
    ) -> None:
        """User ส่งทั้ง valid และ invalid → เก็บเฉพาะ valid."""
        valid = CurriculumVersion(program="IT", curriculum_year=2565, edition_status="current")
        invalid = CurriculumVersion(program="ZZZZ", curriculum_year=2599, edition_status="current")

        result = resolver.resolve("test", requested=[valid, invalid])

        assert len(result.versions) == 1
        assert result.versions[0] == valid
        assert result.source == "request_parameter"

    def test_text_with_multiple_programs(
        self, resolver: VersionResolver
    ) -> None:
        """ข้อความมีหลาย program → ได้หลาย version."""
        result = resolver.resolve(
            question="เทียบ IT กับ BIT ปี 2565"
        )

        assert result.source == "question_text"
        programs = {v.program for v in result.versions}
        assert "IT" in programs
        assert "BIT" in programs

    def test_text_with_multiple_years(
        self, resolver: VersionResolver
    ) -> None:
        """ข้อความมีหลายปี → ได้หลาย version."""
        result = resolver.resolve(
            question="หลักสูตร IT 2560 กับ 2565 ต่างกันอย่างไร"
        )

        assert result.source == "question_text"
        years = {v.curriculum_year for v in result.versions}
        assert 2560 in years
        assert 2565 in years

    def test_case_insensitive_program_matching(
        self, resolver: VersionResolver
    ) -> None:
        """Program name matching ต้อง case-insensitive."""
        result = resolver.resolve(question="หลักสูตร it 2565")

        assert result.source == "question_text"
        assert any(v.program == "IT" for v in result.versions)

    def test_resolver_with_single_available_version(self) -> None:
        """Store มี version เดียว → default_all ก็คืนค่าเดียว ไม่ต้อง clarify."""
        single = frozenset({
            CurriculumVersion(program="IT", curriculum_year=2565, edition_status="current")
        })
        r = VersionResolver(available_versions=single)

        result = r.resolve(question="มีวิชาอะไรบ้าง")

        assert len(result.versions) == 1
        assert not result.needs_clarification
