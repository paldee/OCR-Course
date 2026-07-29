"""Unit tests for katrag.query.citation.

ทดสอบ R17.6, R19.1:
- issue ออก sequential citation IDs (cite-001, cite-002, ...)
- resolve แปลง citation ID กลับเป็น document_id, page, heading, chunk_id
- unknown ID คืน None
- registry เป็น request-scoped (แต่ละ instance อิสระกัน)
- all_ids คืน frozenset ครบทุก ID ที่ออกไป
- count นับจำนวน citation ที่ออกไป
"""

from __future__ import annotations

import pytest

from katrag.common.types import CitationId
from katrag.query.citation import CitationInfo, CitationRegistry, EvidenceUnit


# ── Fixtures / helpers ────────────────────────────────────────────────


def _make_evidence(
    chunk_id: str = "chunk_01",
    document_id: str = "doc_abc",
    page: int = 5,
    heading: str = "วิชาบังคับ",
    text: str = "เนื้อหาตัวอย่าง",
) -> EvidenceUnit:
    return EvidenceUnit(
        chunk_id=chunk_id,
        document_id=document_id,
        page=page,
        heading=heading,
        text=text,
    )


# ══════════════════════════════════════════════════════════════════════
# Tests: EvidenceUnit validation
# ══════════════════════════════════════════════════════════════════════


class TestEvidenceUnitValidation:
    def test_rejects_empty_chunk_id(self) -> None:
        with pytest.raises(ValueError, match="chunk_id"):
            EvidenceUnit(chunk_id="", document_id="d", page=1, heading="h", text="t")

    def test_rejects_empty_document_id(self) -> None:
        with pytest.raises(ValueError, match="document_id"):
            EvidenceUnit(chunk_id="c", document_id="", page=1, heading="h", text="t")

    def test_rejects_page_zero(self) -> None:
        with pytest.raises(ValueError, match="page"):
            EvidenceUnit(chunk_id="c", document_id="d", page=0, heading="h", text="t")

    def test_rejects_negative_page(self) -> None:
        with pytest.raises(ValueError, match="page"):
            EvidenceUnit(chunk_id="c", document_id="d", page=-1, heading="h", text="t")

    def test_accepts_valid_unit(self) -> None:
        unit = _make_evidence()
        assert unit.chunk_id == "chunk_01"
        assert unit.document_id == "doc_abc"
        assert unit.page == 5


# ══════════════════════════════════════════════════════════════════════
# Tests: CitationRegistry.issue — sequential IDs
# ══════════════════════════════════════════════════════════════════════


class TestIssueSequentialIds:
    """R17.6: citation ID ออกลำดับ sequential per request."""

    def test_first_id_is_cite_001(self) -> None:
        registry = CitationRegistry()
        cid = registry.issue(_make_evidence())
        assert str(cid) == "cite-001"

    def test_sequential_ids(self) -> None:
        registry = CitationRegistry()
        ids = [
            registry.issue(_make_evidence(chunk_id=f"c{i}"))
            for i in range(5)
        ]
        assert [str(c) for c in ids] == [
            "cite-001",
            "cite-002",
            "cite-003",
            "cite-004",
            "cite-005",
        ]

    def test_returns_citation_id_type(self) -> None:
        registry = CitationRegistry()
        cid = registry.issue(_make_evidence())
        assert isinstance(cid, CitationId)

    def test_many_ids_three_digit_format(self) -> None:
        """Verify zero-padding works up to at least 100+."""
        registry = CitationRegistry()
        for i in range(1, 101):
            cid = registry.issue(_make_evidence(chunk_id=f"c{i}"))
        assert str(cid) == "cite-100"


# ══════════════════════════════════════════════════════════════════════
# Tests: CitationRegistry.resolve — แปลง ID กลับ
# ══════════════════════════════════════════════════════════════════════


class TestResolve:
    """R19.1: resolve citation ID กลับเป็น document_id + page + heading + chunk_id."""

    def test_resolve_returns_correct_info(self) -> None:
        registry = CitationRegistry()
        unit = _make_evidence(
            chunk_id="chunk_42",
            document_id="doc_xyz",
            page=12,
            heading="หมวดวิชาเลือก",
        )
        cid = registry.issue(unit)
        info = registry.resolve(cid)

        assert info is not None
        assert info.document_id == "doc_xyz"
        assert info.page == 12
        assert info.heading == "หมวดวิชาเลือก"
        assert info.chunk_id == "chunk_42"

    def test_resolve_with_string_id(self) -> None:
        """resolve ยอมรับทั้ง CitationId object และ string."""
        registry = CitationRegistry()
        registry.issue(_make_evidence())
        info = registry.resolve("cite-001")

        assert info is not None
        assert info.document_id == "doc_abc"

    def test_resolve_multiple_distinct_units(self) -> None:
        """ออกหลาย citation → resolve แต่ละ ID ได้ข้อมูลถูกต้อง."""
        registry = CitationRegistry()
        unit_a = _make_evidence(chunk_id="ca", document_id="docA", page=1, heading="A")
        unit_b = _make_evidence(chunk_id="cb", document_id="docB", page=7, heading="B")

        cid_a = registry.issue(unit_a)
        cid_b = registry.issue(unit_b)

        info_a = registry.resolve(cid_a)
        info_b = registry.resolve(cid_b)

        assert info_a is not None
        assert info_a.document_id == "docA"
        assert info_a.page == 1
        assert info_a.heading == "A"
        assert info_a.chunk_id == "ca"

        assert info_b is not None
        assert info_b.document_id == "docB"
        assert info_b.page == 7
        assert info_b.heading == "B"
        assert info_b.chunk_id == "cb"

    def test_resolve_returns_citation_info_type(self) -> None:
        registry = CitationRegistry()
        registry.issue(_make_evidence())
        info = registry.resolve("cite-001")
        assert isinstance(info, CitationInfo)


# ══════════════════════════════════════════════════════════════════════
# Tests: Unknown ID returns None
# ══════════════════════════════════════════════════════════════════════


class TestResolveUnknown:
    """R17.6: LLM can only use citation IDs that the system provides."""

    def test_unknown_id_returns_none(self) -> None:
        registry = CitationRegistry()
        registry.issue(_make_evidence())
        assert registry.resolve("cite-999") is None

    def test_empty_registry_returns_none(self) -> None:
        registry = CitationRegistry()
        assert registry.resolve("cite-001") is None

    def test_fabricated_id_returns_none(self) -> None:
        """ID ที่ LLM อาจสร้างเองแต่ระบบไม่ได้ออก → None."""
        registry = CitationRegistry()
        registry.issue(_make_evidence())
        assert registry.resolve("fake-id") is None
        assert registry.resolve("cite-abc") is None
        assert registry.resolve("") is None


# ══════════════════════════════════════════════════════════════════════
# Tests: Request-scoped — registry ไม่แชร์ข้ามคำขอ
# ══════════════════════════════════════════════════════════════════════


class TestRequestScoped:
    """R17.6: registry สร้างใหม่ทุก request — ไม่แชร์ข้ามคำขอ."""

    def test_separate_registries_independent(self) -> None:
        """สอง registry ออก ID ซ้ำกันได้ — ไม่มี shared state."""
        reg1 = CitationRegistry()
        reg2 = CitationRegistry()

        cid1 = reg1.issue(_make_evidence(chunk_id="c1", document_id="doc1"))
        cid2 = reg2.issue(_make_evidence(chunk_id="c2", document_id="doc2"))

        # ทั้งคู่ออก cite-001
        assert str(cid1) == "cite-001"
        assert str(cid2) == "cite-001"

        # resolve ในแต่ละ registry ได้ข้อมูลต่างกัน
        info1 = reg1.resolve(cid1)
        info2 = reg2.resolve(cid2)

        assert info1 is not None and info1.document_id == "doc1"
        assert info2 is not None and info2.document_id == "doc2"

    def test_one_registry_cannot_resolve_another(self) -> None:
        """registry ไม่สามารถ resolve ID ที่อีก registry ออก (ถ้า chunk ต่างกัน)."""
        reg1 = CitationRegistry()
        reg2 = CitationRegistry()

        reg1.issue(_make_evidence(chunk_id="c1"))
        # reg2 ไม่ได้ issue อะไร → resolve cite-001 คืน None
        assert reg2.resolve("cite-001") is None


# ══════════════════════════════════════════════════════════════════════
# Tests: all_ids returns complete set
# ══════════════════════════════════════════════════════════════════════


class TestAllIds:
    """R17.6: closed set per request — LLM can only use IDs the system provides."""

    def test_all_ids_empty_initially(self) -> None:
        registry = CitationRegistry()
        assert registry.all_ids() == frozenset()

    def test_all_ids_returns_frozenset(self) -> None:
        registry = CitationRegistry()
        registry.issue(_make_evidence())
        ids = registry.all_ids()
        assert isinstance(ids, frozenset)

    def test_all_ids_contains_all_issued(self) -> None:
        registry = CitationRegistry()
        issued = [
            registry.issue(_make_evidence(chunk_id=f"c{i}"))
            for i in range(4)
        ]
        all_ids = registry.all_ids()
        assert len(all_ids) == 4
        for cid in issued:
            assert cid in all_ids

    def test_all_ids_immutable(self) -> None:
        """frozenset ป้องกันการแก้ไขจากภายนอก."""
        registry = CitationRegistry()
        registry.issue(_make_evidence())
        ids = registry.all_ids()
        # frozenset ไม่มี method add/remove — ทดสอบ type
        assert isinstance(ids, frozenset)


# ══════════════════════════════════════════════════════════════════════
# Tests: count
# ══════════════════════════════════════════════════════════════════════


class TestCount:
    def test_count_zero_initially(self) -> None:
        registry = CitationRegistry()
        assert registry.count() == 0

    def test_count_increments(self) -> None:
        registry = CitationRegistry()
        for i in range(1, 6):
            registry.issue(_make_evidence(chunk_id=f"c{i}"))
            assert registry.count() == i
