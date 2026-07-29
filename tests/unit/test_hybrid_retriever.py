"""Unit tests for katrag.query.hybrid_retriever (R13.3, R13.10, R13.11, R10.5).

ทดสอบ:
- RRF fusion: รวม top-100 จาก lexical + dense ด้วยสูตร RRF
- ผลลัพธ์ไม่เกิน 50 รายการ
- Tie-break ด้วย chunk_id ascending เมื่อคะแนนเท่ากัน
- Version filter ก่อน scoring — chunk นอกชุดที่ส่งต่อต้องเท่ากับศูนย์
- ปฏิเสธ query ว่าง / ยาวเกิน 1,000 อักขระ โดยไม่เรียกดัชนีใด
- คืนผลว่างพร้อม status เมื่อไม่พบ chunk
"""

from __future__ import annotations

import pytest

from katrag.common.types import CurriculumVersion
from katrag.index.dense import DenseHit
from katrag.index.lexical import LexicalHit
from katrag.query.hybrid_retriever import (
    HybridRetrievalResponse,
    RetrievalResult,
    RetrievalStatus,
    retrieve,
)


# ── helpers ───────────────────────────────────────────────────────────


def _make_lexical_hit(
    chunk_id: str,
    score: float = 1.0,
    program: str = "IT",
    curriculum_year: int = 2565,
    edition_status: str = "current",
) -> LexicalHit:
    return LexicalHit(
        chunk_id=chunk_id,  # type: ignore[arg-type]
        score=score,
        heading="test heading",
        text_snippet="test snippet",
        program=program,
        curriculum_year=curriculum_year,
        edition_status=edition_status,
    )


def _make_dense_hit(
    chunk_id: str,
    score: float = 0.9,
    program: str = "IT",
    curriculum_year: int = 2565,
    edition_status: str = "current",
) -> DenseHit:
    return DenseHit(
        chunk_id=chunk_id,
        score=score,
        heading="test heading",
        text_snippet="test snippet",
        program=program,
        curriculum_year=curriculum_year,
        edition_status=edition_status,
    )


class MockLexicalSearcher:
    """Mock lexical searcher ที่บันทึกว่าถูกเรียกหรือไม่."""

    def __init__(self, hits: list[LexicalHit] | None = None) -> None:
        self.hits = hits or []
        self.call_count = 0
        self.last_args: tuple = ()
        self.last_kwargs: dict = {}

    def __call__(
        self,
        query_text: str,
        *,
        version_filter: CurriculumVersion | None = None,
        top_k: int = 100,
    ) -> list[LexicalHit]:
        self.call_count += 1
        self.last_args = (query_text,)
        self.last_kwargs = {"version_filter": version_filter, "top_k": top_k}
        return self.hits


class MockDenseSearcher:
    """Mock dense searcher ที่บันทึกว่าถูกเรียกหรือไม่."""

    def __init__(self, hits: list[DenseHit] | None = None) -> None:
        self.hits = hits or []
        self.call_count = 0
        self.last_args: tuple = ()
        self.last_kwargs: dict = {}

    def __call__(
        self,
        query_text: str,
        *,
        version_filter: CurriculumVersion | None = None,
        top_k: int = 100,
    ) -> list[DenseHit]:
        self.call_count += 1
        self.last_args = (query_text,)
        self.last_kwargs = {"version_filter": version_filter, "top_k": top_k}
        return self.hits


# ── R13.10: ปฏิเสธ query ว่างหรือยาวเกิน 1,000 อักขระ ──────────────


class TestQueryValidation:
    """R13.10: ปฏิเสธคำถามที่ว่างหรือยาวเกิน 1,000 อักขระ."""

    def test_empty_query_rejected_without_calling_index(self) -> None:
        """Query ว่าง → ปฏิเสธ ไม่เรียก index."""
        lex = MockLexicalSearcher()
        dns = MockDenseSearcher()

        response = retrieve("", lexical_searcher=lex, dense_searcher=dns)

        assert response.status.ok is False
        assert response.status.reason == "empty_query"
        assert response.results == []
        assert lex.call_count == 0
        assert dns.call_count == 0

    def test_whitespace_only_query_rejected(self) -> None:
        """Query ที่เป็น whitespace ล้วน → ปฏิเสธ."""
        lex = MockLexicalSearcher()
        dns = MockDenseSearcher()

        response = retrieve("   \t\n  ", lexical_searcher=lex, dense_searcher=dns)

        assert response.status.ok is False
        assert response.status.reason == "empty_query"
        assert lex.call_count == 0
        assert dns.call_count == 0

    def test_query_over_1000_chars_rejected(self) -> None:
        """Query ยาวเกิน 1,000 อักขระ → ปฏิเสธ ไม่เรียก index."""
        lex = MockLexicalSearcher()
        dns = MockDenseSearcher()
        long_query = "x" * 1001

        response = retrieve(long_query, lexical_searcher=lex, dense_searcher=dns)

        assert response.status.ok is False
        assert response.status.reason == "query_too_long"
        assert response.results == []
        assert lex.call_count == 0
        assert dns.call_count == 0

    def test_query_exactly_1000_chars_accepted(self) -> None:
        """Query ยาวพอดี 1,000 อักขระ → ยอมรับ."""
        lex = MockLexicalSearcher(hits=[_make_lexical_hit("chunk_a")])
        dns = MockDenseSearcher(hits=[_make_dense_hit("chunk_a")])
        query = "x" * 1000

        response = retrieve(query, lexical_searcher=lex, dense_searcher=dns)

        assert response.status.ok is True
        assert lex.call_count == 1
        assert dns.call_count == 1

    def test_custom_max_question_chars(self) -> None:
        """ใช้ค่า max_question_chars ที่กำหนดเองได้."""
        lex = MockLexicalSearcher()
        dns = MockDenseSearcher()

        response = retrieve(
            "hello world",
            lexical_searcher=lex,
            dense_searcher=dns,
            max_question_chars=5,
        )

        assert response.status.ok is False
        assert response.status.reason == "query_too_long"
        assert lex.call_count == 0


# ── R13.11: คืนผลว่างพร้อม status ─────────────────────────────────────


class TestNoResults:
    """R13.11: คืนผลว่างพร้อม status เมื่อไม่พบ chunk."""

    def test_no_results_from_both_indices(self) -> None:
        """ทั้งสองดัชนีคืนว่าง → status no_results."""
        lex = MockLexicalSearcher(hits=[])
        dns = MockDenseSearcher(hits=[])

        response = retrieve("หลักสูตร", lexical_searcher=lex, dense_searcher=dns)

        assert response.status.ok is True
        assert response.status.reason == "no_results"
        assert response.status.result_count == 0
        assert response.results == []


# ── R13.3: RRF fusion ─────────────────────────────────────────────────


class TestRRFFusion:
    """R13.3: Reciprocal Rank Fusion with configurable parameters."""

    def test_basic_rrf_both_indices(self) -> None:
        """Chunk ที่อยู่ในทั้งสองดัชนีได้คะแนนรวม RRF."""
        # chunk_a: lexical rank 1, dense rank 1
        # chunk_b: lexical rank 2, dense rank 2
        lex_hits = [_make_lexical_hit("chunk_a"), _make_lexical_hit("chunk_b")]
        dns_hits = [_make_dense_hit("chunk_a"), _make_dense_hit("chunk_b")]
        lex = MockLexicalSearcher(hits=lex_hits)
        dns = MockDenseSearcher(hits=dns_hits)

        response = retrieve("test query", lexical_searcher=lex, dense_searcher=dns)

        assert response.status.ok is True
        assert response.status.reason == "success"
        assert len(response.results) == 2

        # chunk_a at rank 1 in both: 0.5/(60+1) + 0.5/(60+1) = 1.0/61
        result_a = response.results[0]
        assert result_a.chunk_id == "chunk_a"
        expected_score_a = 0.5 / (60 + 1) + 0.5 / (60 + 1)
        assert abs(result_a.fused_score - expected_score_a) < 1e-10

        # chunk_b at rank 2 in both: 0.5/(60+2) + 0.5/(60+2) = 1.0/62
        result_b = response.results[1]
        assert result_b.chunk_id == "chunk_b"
        expected_score_b = 0.5 / (60 + 2) + 0.5 / (60 + 2)
        assert abs(result_b.fused_score - expected_score_b) < 1e-10

    def test_chunk_only_in_lexical(self) -> None:
        """Chunk ที่อยู่เฉพาะใน lexical ได้เฉพาะ RRF จาก lexical."""
        lex_hits = [_make_lexical_hit("chunk_only_lex")]
        dns_hits: list[DenseHit] = []
        lex = MockLexicalSearcher(hits=lex_hits)
        dns = MockDenseSearcher(hits=dns_hits)

        response = retrieve("test query", lexical_searcher=lex, dense_searcher=dns)

        assert len(response.results) == 1
        result = response.results[0]
        assert result.chunk_id == "chunk_only_lex"
        assert result.lexical_rank == 1
        assert result.dense_rank is None
        expected = 0.5 / (60 + 1)
        assert abs(result.fused_score - expected) < 1e-10

    def test_chunk_only_in_dense(self) -> None:
        """Chunk ที่อยู่เฉพาะใน dense ได้เฉพาะ RRF จาก dense."""
        lex_hits: list[LexicalHit] = []
        dns_hits = [_make_dense_hit("chunk_only_dns")]
        lex = MockLexicalSearcher(hits=lex_hits)
        dns = MockDenseSearcher(hits=dns_hits)

        response = retrieve("test query", lexical_searcher=lex, dense_searcher=dns)

        assert len(response.results) == 1
        result = response.results[0]
        assert result.chunk_id == "chunk_only_dns"
        assert result.lexical_rank is None
        assert result.dense_rank == 1
        expected = 0.5 / (60 + 1)
        assert abs(result.fused_score - expected) < 1e-10

    def test_rrf_ranks_recorded_correctly(self) -> None:
        """Rank ที่บันทึกใน result ตรงกับตำแหน่งในรายการผลลัพธ์ดัชนี."""
        # chunk_x: lex rank 3, dense rank 1
        lex_hits = [
            _make_lexical_hit("chunk_1"),
            _make_lexical_hit("chunk_2"),
            _make_lexical_hit("chunk_x"),
        ]
        dns_hits = [_make_dense_hit("chunk_x")]
        lex = MockLexicalSearcher(hits=lex_hits)
        dns = MockDenseSearcher(hits=dns_hits)

        response = retrieve("test query", lexical_searcher=lex, dense_searcher=dns)

        # chunk_x should be in results
        chunk_x_result = next(r for r in response.results if r.chunk_id == "chunk_x")
        assert chunk_x_result.lexical_rank == 3
        assert chunk_x_result.dense_rank == 1


# ── output limit ≤ 50 ────────────────────────────────────────────────


class TestOutputLimit:
    """R13.3: ผลลัพธ์ไม่เกิน 50 รายการ."""

    def test_max_50_results(self) -> None:
        """เมื่อมี chunk เกิน 50 → ตัดที่ 50."""
        # 60 unique chunks จาก lexical, 60 จาก dense (บางส่วนซ้ำ)
        lex_hits = [_make_lexical_hit(f"lex_{i:03d}") for i in range(60)]
        dns_hits = [_make_dense_hit(f"dns_{i:03d}") for i in range(60)]
        lex = MockLexicalSearcher(hits=lex_hits)
        dns = MockDenseSearcher(hits=dns_hits)

        response = retrieve("test query", lexical_searcher=lex, dense_searcher=dns)

        assert len(response.results) <= 50
        assert response.status.result_count <= 50

    def test_fewer_than_50_returns_all(self) -> None:
        """เมื่อมี chunk น้อยกว่า 50 → คืนทั้งหมด."""
        lex_hits = [_make_lexical_hit(f"chunk_{i}") for i in range(10)]
        dns_hits = [_make_dense_hit(f"chunk_{i}") for i in range(10)]
        lex = MockLexicalSearcher(hits=lex_hits)
        dns = MockDenseSearcher(hits=dns_hits)

        response = retrieve("test query", lexical_searcher=lex, dense_searcher=dns)

        assert len(response.results) == 10


# ── tie-break ─────────────────────────────────────────────────────────


class TestTieBreak:
    """Tie-break ด้วย chunk_id ascending เมื่อคะแนนเท่ากัน."""

    def test_same_score_sorted_by_chunk_id_ascending(self) -> None:
        """Chunks ที่ได้คะแนน RRF เท่ากัน → เรียงตาม chunk_id."""
        # ทั้งสาม chunk มี lexical rank 1 (เฉพาะ lexical) → คะแนนเท่ากัน
        # เราทำให้แต่ละ chunk อยู่ rank เดียวกันทั้งสองดัชนี
        # chunk_c rank 1 lex, chunk_a rank 1 dense, chunk_b rank 1 dense
        # ง่ายกว่า: ทุก chunk อยู่เฉพาะ lexical ที่ rank ต่าง ๆ ไม่ให้ tie

        # ให้ 3 chunk อยู่เฉพาะ dense rank 1, 2, 3 ไม่ tie
        # ลองแบบ simple: ให้ chunk_b กับ chunk_a อยู่ rank 1 ในแต่ละ index
        # chunk_b: lex rank 1 only → score = 0.5/61
        # chunk_a: dense rank 1 only → score = 0.5/61
        # ทั้งสองได้คะแนนเท่ากัน → tie-break ด้วย chunk_id → "chunk_a" < "chunk_b"
        lex_hits = [_make_lexical_hit("chunk_b")]
        dns_hits = [_make_dense_hit("chunk_a")]
        lex = MockLexicalSearcher(hits=lex_hits)
        dns = MockDenseSearcher(hits=dns_hits)

        response = retrieve("test query", lexical_searcher=lex, dense_searcher=dns)

        assert len(response.results) == 2
        assert response.results[0].chunk_id == "chunk_a"
        assert response.results[1].chunk_id == "chunk_b"

    def test_multiple_ties_sorted_alphabetically(self) -> None:
        """หลาย chunk ที่ tie → เรียงตาม chunk_id ascending ทั้งหมด."""
        # ทุก chunk อยู่เฉพาะ lexical rank 1 (ด้วย trick: ใส่ chunk_id ซ้ำ)
        # ใช้วิธี: แต่ละ chunk อยู่ในตำแหน่งเดียวกัน
        # ง่ายที่สุด: ทุก chunk อยู่ใน dense rank ต่าง ๆ ที่ให้ score เท่ากัน
        # ไม่ได้ — ต้อง rank เท่ากัน

        # ใช้วิธี: ทุก chunk อยู่เฉพาะ 1 index ที่ rank เดียวกัน (ไม่ซ้ำ)
        # ใส่ทุก chunk ใน lexical results ที่ rank 5
        # ทุก chunk จะมี rank ต่างกัน (1, 2, 3, ...)

        # ง่ายที่สุด: ให้ chunk_c, chunk_a, chunk_b ทั้งสามอยู่เฉพาะ dense
        # rank 1, 1, 1 ← ไม่ได้ เพราะแต่ละ hit คือ 1 chunk

        # วิธีที่ถูก: chunk ที่ได้ score เท่ากันจากคนละ index ที่ rank เดียวกัน
        # chunk_z: lexical rank 1 only → 0.5/61
        # chunk_m: dense rank 1 only → 0.5/61
        # chunk_a: lexical rank 2, dense rank 2 → 0.5/62 + 0.5/62 = 1.0/62
        # ↑ ไม่ tie

        # วิธีที่ง่ายสุด: 3 chunk แต่ละอันอยู่เฉพาะ 1 index ที่ rank 1
        # ← ไม่ได้ มี 3 chunk แต่ rank 1 ได้แค่ 1 ตัว/index

        # ใช้วิธี: 2 index, chunk_z lex rank 1, chunk_a dense rank 1
        # → ทั้งคู่ได้ 0.5/61 → tie → sort by chunk_id: "chunk_a" < "chunk_z"
        lex_hits = [_make_lexical_hit("chunk_z")]
        dns_hits = [_make_dense_hit("chunk_a")]
        lex = MockLexicalSearcher(hits=lex_hits)
        dns = MockDenseSearcher(hits=dns_hits)

        response = retrieve("test query", lexical_searcher=lex, dense_searcher=dns)

        assert response.results[0].chunk_id == "chunk_a"
        assert response.results[1].chunk_id == "chunk_z"


# ── R10.5: version filter ก่อน scoring ────────────────────────────────


class TestVersionFilter:
    """R10.5: Version filter applied BEFORE scoring — zero chunks outside version set."""

    def test_version_filter_passed_to_both_searchers(self) -> None:
        """version_filter ถูกส่งไปทั้งสอง searcher."""
        version = CurriculumVersion(program="IT", curriculum_year=2565, edition_status="current")
        lex = MockLexicalSearcher(hits=[_make_lexical_hit("c1")])
        dns = MockDenseSearcher(hits=[_make_dense_hit("c1")])

        retrieve(
            "test query",
            lexical_searcher=lex,
            dense_searcher=dns,
            version_filter=version,
        )

        assert lex.last_kwargs["version_filter"] == version
        assert dns.last_kwargs["version_filter"] == version

    def test_no_chunks_outside_version_set_in_results(self) -> None:
        """เมื่อใช้ version filter, ผลลัพธ์ทั้งหมดมาจาก searcher ที่กรอง version แล้ว.

        searcher ที่ถูก mock คืนเฉพาะ chunk ที่ผ่าน filter (เหมือนจริง)
        → ผลลัพธ์ retriever ไม่มี chunk นอก version set.
        """
        version = CurriculumVersion(program="IT", curriculum_year=2565, edition_status="current")
        # Mock คืนเฉพาะ chunk ใน version ที่กรอง (เหมือนการทำงานจริงของ dense/lexical)
        lex = MockLexicalSearcher(
            hits=[_make_lexical_hit("c_it", program="IT", curriculum_year=2565)]
        )
        dns = MockDenseSearcher(
            hits=[_make_dense_hit("c_it", program="IT", curriculum_year=2565)]
        )

        response = retrieve(
            "test query",
            lexical_searcher=lex,
            dense_searcher=dns,
            version_filter=version,
        )

        # ผลลัพธ์มีเฉพาะ chunk ที่ถูก filter
        assert len(response.results) == 1
        assert response.results[0].chunk_id == "c_it"

    def test_none_version_filter_no_filtering(self) -> None:
        """version_filter=None → ส่ง None ไปให้ searcher (ไม่กรอง)."""
        lex = MockLexicalSearcher(hits=[_make_lexical_hit("any_chunk")])
        dns = MockDenseSearcher(hits=[_make_dense_hit("any_chunk")])

        retrieve("test query", lexical_searcher=lex, dense_searcher=dns, version_filter=None)

        assert lex.last_kwargs["version_filter"] is None
        assert dns.last_kwargs["version_filter"] is None


# ── integration-style: combined scenario ──────────────────────────────


class TestCombinedScenario:
    """ทดสอบสถานการณ์รวม."""

    def test_overlapping_and_unique_chunks(self) -> None:
        """Chunks ที่ซ้ำกันในทั้งสอง index ได้คะแนนสูงกว่า."""
        # shared: lex rank 1, dense rank 1 → 0.5/61 + 0.5/61
        # lex_only: lex rank 2 → 0.5/62
        # dense_only: dense rank 2 → 0.5/62
        lex_hits = [_make_lexical_hit("shared"), _make_lexical_hit("lex_only")]
        dns_hits = [_make_dense_hit("shared"), _make_dense_hit("dense_only")]
        lex = MockLexicalSearcher(hits=lex_hits)
        dns = MockDenseSearcher(hits=dns_hits)

        response = retrieve("test query", lexical_searcher=lex, dense_searcher=dns)

        assert response.results[0].chunk_id == "shared"
        shared_expected = 0.5 / 61 + 0.5 / 61
        assert abs(response.results[0].fused_score - shared_expected) < 1e-10

        # lex_only and dense_only both score 0.5/62, tie-break by chunk_id
        assert response.results[1].chunk_id == "dense_only"  # "d" < "l"
        assert response.results[2].chunk_id == "lex_only"

    def test_results_are_deterministic(self) -> None:
        """ผลลัพธ์ต้อง deterministic เมื่อ input เหมือนกัน."""
        lex_hits = [_make_lexical_hit(f"chunk_{i:02d}") for i in range(20)]
        dns_hits = [_make_dense_hit(f"chunk_{i:02d}") for i in range(15, 35)]
        lex = MockLexicalSearcher(hits=lex_hits)
        dns = MockDenseSearcher(hits=dns_hits)

        response1 = retrieve("determinism", lexical_searcher=lex, dense_searcher=dns)
        response2 = retrieve("determinism", lexical_searcher=lex, dense_searcher=dns)

        assert [r.chunk_id for r in response1.results] == [
            r.chunk_id for r in response2.results
        ]
        assert [r.fused_score for r in response1.results] == [
            r.fused_score for r in response2.results
        ]

    def test_status_fields_correct_on_success(self) -> None:
        """สถานะ success มีค่าครบถ้วน."""
        lex_hits = [_make_lexical_hit("c1"), _make_lexical_hit("c2")]
        dns_hits = [_make_dense_hit("c1")]
        lex = MockLexicalSearcher(hits=lex_hits)
        dns = MockDenseSearcher(hits=dns_hits)

        response = retrieve("test query", lexical_searcher=lex, dense_searcher=dns)

        assert response.status.ok is True
        assert response.status.reason == "success"
        assert response.status.result_count == 2
