"""Unit tests for katrag.query.pipeline — ขั้นตอนที่แยกออกจาก /ask handler.

เดิม logic เหล่านี้ฝังอยู่ในฟังก์ชัน `/ask` ยาว 417 บรรทัด ทดสอบแยกขั้นไม่ได้
"""

from __future__ import annotations

from katrag.query.pipeline import (
    CUTOFF_RATIO,
    MIN_EVIDENCE,
    EvidenceHit,
    StructuredOutcome,
    _apply_cutoff,
    _dedupe_by_page,
    build_context,
    citations_from_hits,
    is_reasoning_question,
    resolve_program,
    scope_question,
)


def _hit(
    chunk_id: int = 1,
    document_id: str = "docA",
    page: int = 1,
    score: float = 1.0,
    text: str = "เนื้อหา",
) -> EvidenceHit:
    return EvidenceHit(
        chunk_id=chunk_id,
        document_id=document_id,
        page_number=page,
        heading="หัวข้อ",
        text=text,
        program="IT",
        curriculum_year=2565,
        edition_status="current",
        score=score,
    )


# ── ขั้นที่ 1: เลือกหลักสูตร ───────────────────────────────────────────


class TestResolveProgram:
    """กฎสองชั้น: ชื่อในคำถามชนะค่าที่ผู้ใช้เลือก ไม่มีการเดา."""

    def test_uses_selected_when_question_has_no_program(self) -> None:
        program, source = resolve_program("มีวิชาอะไรบ้าง", "DSBA")
        assert (program, source) == ("DSBA", "selected")

    def test_question_overrides_selected(self) -> None:
        """คำถามข้ามหลักสูตรต้องทำได้โดยไม่ต้องเปลี่ยน dropdown."""
        program, source = resolve_program("หลักสูตร BIT มีกี่หน่วยกิต", "DSBA")
        assert (program, source) == ("BIT", "question")

    def test_selected_is_normalized_to_upper(self) -> None:
        program, _ = resolve_program("มีวิชาอะไรบ้าง", " it ")
        assert program == "IT"

    def test_no_silent_guessing(self) -> None:
        """คำถามที่ไม่มีชื่อหลักสูตรต้องไม่ถูกเดาเป็นหลักสูตรอื่น."""
        program, source = resolve_program("เรียนวิชา Database ปี 4 ได้ไหม", "AIT")
        assert (program, source) == ("AIT", "selected")


class TestScopeQuestion:
    def test_prepends_program_when_absent(self) -> None:
        assert scope_question("มีกี่วิชา", "IT").startswith("หลักสูตร IT:")

    def test_does_not_duplicate_existing_program(self) -> None:
        q = "หลักสูตร IT มีกี่วิชา"
        assert scope_question(q, "IT") == q

    def test_empty_program_leaves_question_unchanged(self) -> None:
        assert scope_question("มีกี่วิชา", "") == "มีกี่วิชา"


# ── ขั้นที่ 4: กรองหลักฐาน ─────────────────────────────────────────────


class TestDedupeByPage:
    def test_keeps_first_chunk_per_page(self) -> None:
        hits = [
            _hit(chunk_id=1, page=5, text="แรก"),
            _hit(chunk_id=2, page=5, text="ซ้ำหน้าเดียวกัน"),
            _hit(chunk_id=3, page=6),
        ]
        out = _dedupe_by_page(hits)
        assert [h.chunk_id for h in out] == [1, 3]

    def test_same_page_different_document_both_kept(self) -> None:
        hits = [_hit(document_id="docA", page=1), _hit(document_id="docB", page=1)]
        assert len(_dedupe_by_page(hits)) == 2


class TestApplyCutoff:
    def test_drops_hits_far_below_top_score(self) -> None:
        hits = [
            _hit(chunk_id=1, page=1, score=1.0),
            _hit(chunk_id=2, page=2, score=0.9),
            _hit(chunk_id=3, page=3, score=0.8),
            _hit(chunk_id=4, page=4, score=0.05),
        ]
        out = _apply_cutoff(hits)
        assert [h.chunk_id for h in out] == [1, 2, 3]
        assert all(h.score >= 1.0 * CUTOFF_RATIO for h in out)

    def test_keeps_minimum_evidence_when_cutoff_too_aggressive(self) -> None:
        """ตัดแรงเกินจนเหลือหลักฐานไม่พอ → คืน MIN_EVIDENCE ตัวแรกแทน."""
        hits = [
            _hit(chunk_id=1, page=1, score=1.0),
            _hit(chunk_id=2, page=2, score=0.01),
            _hit(chunk_id=3, page=3, score=0.01),
            _hit(chunk_id=4, page=4, score=0.01),
        ]
        out = _apply_cutoff(hits)
        assert len(out) == MIN_EVIDENCE

    def test_empty_input(self) -> None:
        assert _apply_cutoff([]) == []

    def test_all_zero_scores_returns_input(self) -> None:
        hits = [_hit(chunk_id=1, page=1, score=0.0), _hit(chunk_id=2, page=2, score=0.0)]
        assert len(_apply_cutoff(hits)) == 2


# ── ขั้นที่ 5-6: context และการเลือกโหมดตอบ ─────────────────────────────


class TestBuildContext:
    def test_structured_context_comes_first(self) -> None:
        structured = StructuredOutcome(context="ข้อมูลตาราง", intent="year_sem")
        ctx = build_context(structured, [_hit(text="ข้อมูล chunk")])
        assert ctx.index("ข้อมูลตาราง") < ctx.index("ข้อมูล chunk")

    def test_numbers_evidence_from_one(self) -> None:
        ctx = build_context(StructuredOutcome(), [_hit(page=7)])
        assert "[1]" in ctx and "หน้า 7" in ctx

    def test_no_structured_no_hits_gives_empty(self) -> None:
        assert build_context(StructuredOutcome(), []) == ""


class TestIsReasoningQuestion:
    def test_detects_can_i_question(self) -> None:
        assert is_reasoning_question("เรียนวิชา Database ปี 4 ได้ไหม")

    def test_detects_why_question(self) -> None:
        assert is_reasoning_question("ทำไมต้องเรียนวิชานี้")

    def test_plain_lookup_is_not_reasoning(self) -> None:
        assert not is_reasoning_question("ปี 1 เทอม 1 เรียนกี่วิชา")


# ── ขั้นที่ 7: citation ────────────────────────────────────────────────


class TestCitationsFromHits:
    def test_ids_are_sequential_and_padded(self) -> None:
        cites = citations_from_hits([_hit(page=1), _hit(page=2), _hit(page=3)])
        assert [c.citation_id for c in cites] == ["cite-001", "cite-002", "cite-003"]

    def test_carries_document_and_page(self) -> None:
        cites = citations_from_hits([_hit(document_id="docX", page=42)])
        assert cites[0].document_id == "docX"
        assert cites[0].page == 42
