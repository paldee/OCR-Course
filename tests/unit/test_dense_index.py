"""Unit tests for katrag.index.embedder and katrag.index.dense (R13.2, R13.4, R13.12).

ทดสอบ:
- Embedder protocol + StubEmbedder
- DenseIndex.build: จำนวน embedding = จำนวน chunk, มิติเท่ากันทุกตัว
- DenseIndex.search: exact full-scan cosine similarity (ไม่ใช้ ANN)
- p95 latency tracking เทียบงบ 3.0 วินาที
- R13.12: ข้าม chunk ที่ embed ล้มเหลว แล้วรายงาน index_build_incomplete
"""

from __future__ import annotations

from typing import Sequence
from unittest.mock import patch

import numpy as np
import pytest

from katrag.common.hashing import sha256_text
from katrag.common.types import CurriculumVersion
from katrag.index.dense import DenseHit, DenseIndex, LatencyTracker
from katrag.index.embedder import (
    DEFAULT_EMBEDDING_DIM,
    Embedder,
    StubEmbedder,
)
from katrag.ingest.chunker import Chunk


# ── fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def stub_embedder() -> StubEmbedder:
    """StubEmbedder ขนาดมาตรฐาน."""
    return StubEmbedder(dim=DEFAULT_EMBEDDING_DIM)


@pytest.fixture
def small_embedder() -> StubEmbedder:
    """StubEmbedder ขนาดเล็กสำหรับทดสอบเร็ว."""
    return StubEmbedder(dim=64)


@pytest.fixture
def version_it() -> CurriculumVersion:
    return CurriculumVersion(program="IT", curriculum_year=2565, edition_status="current")


@pytest.fixture
def version_dsba() -> CurriculumVersion:
    return CurriculumVersion(program="DSBA", curriculum_year=2566, edition_status="current")


def _make_chunk(
    text: str,
    document_id: str = "doc1",
    page: int = 1,
    heading: str = "",
    program: str = "IT",
    curriculum_year: int = 2565,
    edition_status: str = "current",
) -> Chunk:
    """สร้าง Chunk สำหรับทดสอบ."""
    return Chunk(
        content_text=text,
        content_sha256=sha256_text(text),
        document_id=document_id,
        page_start=page,
        page_end=page,
        heading=heading,
        program=program,
        curriculum_year=curriculum_year,
        edition_status=edition_status,
    )


# ── StubEmbedder tests ───────────────────────────────────────────────


class TestStubEmbedder:
    """ทดสอบ StubEmbedder."""

    def test_protocol_compliance(self, stub_embedder: StubEmbedder) -> None:
        """StubEmbedder ต้อง implement Embedder protocol."""
        assert isinstance(stub_embedder, Embedder)

    def test_dim_property(self, stub_embedder: StubEmbedder) -> None:
        """dim ต้องตรงกับที่กำหนด."""
        assert stub_embedder.dim == DEFAULT_EMBEDDING_DIM

    def test_custom_dim(self) -> None:
        """สร้างด้วย dim ที่กำหนดเองได้."""
        emb = StubEmbedder(dim=256)
        assert emb.dim == 256

    def test_encode_empty(self, stub_embedder: StubEmbedder) -> None:
        """encode ลิสต์ว่าง → array shape (0, dim)."""
        result = stub_embedder.encode([])
        assert result.shape == (0, DEFAULT_EMBEDDING_DIM)
        assert result.dtype == np.float32

    def test_encode_single(self, stub_embedder: StubEmbedder) -> None:
        """encode text เดียว → shape (1, dim)."""
        result = stub_embedder.encode(["hello"])
        assert result.shape == (1, DEFAULT_EMBEDDING_DIM)
        assert result.dtype == np.float32

    def test_encode_batch(self, stub_embedder: StubEmbedder) -> None:
        """encode หลาย texts → shape (n, dim)."""
        texts = ["text1", "text2", "text3"]
        result = stub_embedder.encode(texts)
        assert result.shape == (3, DEFAULT_EMBEDDING_DIM)

    def test_encode_count_equals_input(self, stub_embedder: StubEmbedder) -> None:
        """จำนวน embedding ต้องเท่ากับจำนวน text."""
        texts = [f"chunk_{i}" for i in range(10)]
        result = stub_embedder.encode(texts)
        assert result.shape[0] == len(texts)

    def test_encode_dim_uniform(self, stub_embedder: StubEmbedder) -> None:
        """มิติของทุก embedding ต้องเท่ากัน."""
        texts = ["a", "bb", "ccc", "dddd"]
        result = stub_embedder.encode(texts)
        # ทุกแถวมีมิติเดียวกัน
        assert result.shape[1] == DEFAULT_EMBEDDING_DIM
        for i in range(len(texts)):
            assert result[i].shape == (DEFAULT_EMBEDDING_DIM,)

    def test_encode_deterministic(self, stub_embedder: StubEmbedder) -> None:
        """text เดียวกันได้ embedding เดียวกัน (deterministic)."""
        result1 = stub_embedder.encode(["same text"])
        result2 = stub_embedder.encode(["same text"])
        np.testing.assert_array_equal(result1, result2)

    def test_encode_different_texts_different_embeddings(
        self, stub_embedder: StubEmbedder
    ) -> None:
        """text ต่างกันได้ embedding ต่างกัน."""
        result = stub_embedder.encode(["text_a", "text_b"])
        # ไม่ควรเท่ากัน
        assert not np.allclose(result[0], result[1])

    def test_encode_normalized(self, stub_embedder: StubEmbedder) -> None:
        """ทุก embedding ต้องมี L2 norm ≈ 1."""
        result = stub_embedder.encode(["test1", "test2", "test3"])
        norms = np.linalg.norm(result, axis=1)
        np.testing.assert_allclose(norms, 1.0, atol=1e-5)


# ── DenseIndex.build tests ────────────────────────────────────────────


class TestDenseIndexBuild:
    """ทดสอบ DenseIndex.build."""

    def test_build_empty(self, small_embedder: StubEmbedder) -> None:
        """ไม่มี chunk → size = 0, ไม่มี issue."""
        idx = DenseIndex()
        count, issues = idx.build([], small_embedder)
        assert count == 0
        assert issues == []
        assert idx.size == 0

    def test_build_single_chunk(self, small_embedder: StubEmbedder) -> None:
        """chunk หนึ่งตัว → index size = 1."""
        idx = DenseIndex()
        chunk = _make_chunk("วิชาคณิตศาสตร์วิศวกรรม")
        count, issues = idx.build([chunk], small_embedder)

        assert count == 1
        assert issues == []
        assert idx.size == 1
        assert idx.dim == small_embedder.dim

    def test_build_multiple_chunks(self, small_embedder: StubEmbedder) -> None:
        """หลาย chunks → จำนวน embedding = จำนวน chunk."""
        idx = DenseIndex()
        chunks = [
            _make_chunk("วิชาบังคับ"),
            _make_chunk("วิชาเลือก"),
            _make_chunk("วิชาศึกษาทั่วไป"),
            _make_chunk("วิชาเฉพาะสาขา"),
            _make_chunk("วิชาฝึกงาน"),
        ]
        count, issues = idx.build(chunks, small_embedder)

        assert count == 5
        assert count == len(chunks)
        assert issues == []
        assert idx.size == 5

    def test_build_embedding_count_equals_chunk_count(
        self, small_embedder: StubEmbedder
    ) -> None:
        """R13.2: จำนวน embedding ต้องเท่ากับจำนวน chunk."""
        idx = DenseIndex()
        n = 20
        chunks = [_make_chunk(f"chunk content number {i}") for i in range(n)]
        count, issues = idx.build(chunks, small_embedder)

        assert count == n
        assert idx.size == n
        # ตรวจ internal embedding matrix shape
        assert idx._embeddings is not None
        assert idx._embeddings.shape == (n, small_embedder.dim)

    def test_build_embedding_dim_uniform(self, small_embedder: StubEmbedder) -> None:
        """มิติของ embedding ต้องเท่ากันทุกตัว."""
        idx = DenseIndex()
        chunks = [_make_chunk(f"text {i}" * (i + 1)) for i in range(10)]
        count, _ = idx.build(chunks, small_embedder)

        assert idx._embeddings is not None
        # ทุก row ต้องมีมิติเท่ากับ embedder.dim
        assert idx._embeddings.shape[1] == small_embedder.dim
        assert idx.dim == small_embedder.dim


class TestDenseIndexBuildIncomplete:
    """R13.12: ข้าม chunk ที่ embed ล้มเหลว."""

    def test_failed_embed_reports_issue(self, small_embedder: StubEmbedder) -> None:
        """chunk ที่ embed ล้มเหลว → ReviewIssue kind=index_build_incomplete."""

        class FailOnSecondEmbedder:
            """Embedder ที่ล้มเหลวบาง chunk."""

            dim = 64

            def __init__(self) -> None:
                self._call_count = 0

            def encode(self, texts: Sequence[str]) -> np.ndarray:
                self._call_count += 1
                if self._call_count == 2:
                    raise RuntimeError("simulated embedding failure")
                rng = np.random.default_rng(42)
                result = rng.standard_normal((len(texts), self.dim)).astype(np.float32)
                norms = np.linalg.norm(result, axis=1, keepdims=True)
                return result / np.clip(norms, 1e-12, None)

        idx = DenseIndex()
        chunks = [
            _make_chunk("chunk ที่ 1 สำเร็จ"),
            _make_chunk("chunk ที่ 2 จะล้มเหลว"),
            _make_chunk("chunk ที่ 3 สำเร็จ"),
        ]

        embedder = FailOnSecondEmbedder()
        count, issues = idx.build(chunks, embedder)

        assert count == 2  # 2 สำเร็จ
        assert idx.size == 2
        assert len(issues) == 1
        assert issues[0].kind == "index_build_incomplete"
        assert issues[0].detail["failed_count"] == 1
        assert issues[0].detail["indexed_count"] == 2
        assert issues[0].detail["total_attempted"] == 3
        assert issues[0].detail["index_type"] == "dense"

    def test_all_fail_reports_issue(self) -> None:
        """ทุก chunk ล้มเหลว → index ว่าง + issue."""

        class AlwaysFailEmbedder:
            dim = 64

            def encode(self, texts: Sequence[str]) -> np.ndarray:
                raise RuntimeError("always fail")

        idx = DenseIndex()
        chunks = [_make_chunk("fail1"), _make_chunk("fail2")]
        count, issues = idx.build(chunks, AlwaysFailEmbedder())

        assert count == 0
        assert idx.size == 0
        assert len(issues) == 1
        assert issues[0].detail["failed_count"] == 2


# ── DenseIndex.search tests ───────────────────────────────────────────


class TestDenseIndexSearch:
    """ทดสอบ search (exact full-scan, ไม่ใช้ ANN)."""

    @pytest.fixture
    def indexed(self, small_embedder: StubEmbedder) -> tuple[DenseIndex, StubEmbedder]:
        """Index ที่มี chunks หลายตัวพร้อม search."""
        idx = DenseIndex()
        chunks = [
            _make_chunk("คณิตศาสตร์วิศวกรรม วิชาบังคับ", heading="หมวดวิชาบังคับ"),
            _make_chunk("ภาษาอังกฤษเชิงวิชาการ", heading="หมวดวิชาศึกษาทั่วไป"),
            _make_chunk("วิทยาศาสตร์ข้อมูลเบื้องต้น", heading="วิชาแกน",
                        program="DSBA", curriculum_year=2566),
            _make_chunk("โครงสร้างข้อมูลและอัลกอริทึม", heading="หมวดวิชาบังคับ"),
            _make_chunk("ฐานข้อมูลเบื้องต้น", heading="หมวดวิชาบังคับ"),
        ]
        idx.build(chunks, small_embedder)
        return idx, small_embedder

    def test_empty_query_returns_empty(
        self, indexed: tuple[DenseIndex, StubEmbedder]
    ) -> None:
        idx, emb = indexed
        results = idx.search("", emb)
        assert results == []

    def test_whitespace_query_returns_empty(
        self, indexed: tuple[DenseIndex, StubEmbedder]
    ) -> None:
        idx, emb = indexed
        results = idx.search("   ", emb)
        assert results == []

    def test_search_returns_results(
        self, indexed: tuple[DenseIndex, StubEmbedder]
    ) -> None:
        """ค้นหาได้ผลลัพธ์ (อย่างน้อย 1 รายการ)."""
        idx, emb = indexed
        results = idx.search("คณิตศาสตร์", emb)
        assert len(results) > 0
        assert all(isinstance(r, DenseHit) for r in results)

    def test_search_results_sorted_descending(
        self, indexed: tuple[DenseIndex, StubEmbedder]
    ) -> None:
        """ผลลัพธ์เรียงตามคะแนนจากมากไปน้อย."""
        idx, emb = indexed
        results = idx.search("วิชา", emb)
        if len(results) >= 2:
            scores = [r.score for r in results]
            assert scores == sorted(scores, reverse=True)

    def test_search_top_k_limits_results(
        self, indexed: tuple[DenseIndex, StubEmbedder]
    ) -> None:
        """top_k จำกัดจำนวนผลลัพธ์."""
        idx, emb = indexed
        results = idx.search("วิชา", emb, top_k=2)
        assert len(results) <= 2

    def test_search_version_filter(
        self, indexed: tuple[DenseIndex, StubEmbedder]
    ) -> None:
        """R10.5: version_filter กรองเฉพาะ curriculum version ที่ระบุ."""
        idx, emb = indexed
        version_it = CurriculumVersion(program="IT", curriculum_year=2565, edition_status="current")
        results = idx.search("วิชา", emb, version_filter=version_it)

        for r in results:
            assert r.program == "IT"
            assert r.curriculum_year == 2565
            assert r.edition_status == "current"

    def test_search_version_filter_excludes_others(
        self, indexed: tuple[DenseIndex, StubEmbedder]
    ) -> None:
        """version_filter กรอง version อื่นออก."""
        idx, emb = indexed
        version_dsba = CurriculumVersion(program="DSBA", curriculum_year=2566, edition_status="current")
        results = idx.search("วิชา", emb, version_filter=version_dsba)

        for r in results:
            assert r.program == "DSBA"

    def test_search_on_empty_index(self, small_embedder: StubEmbedder) -> None:
        """search บน index ว่าง → ผลลัพธ์ว่าง."""
        idx = DenseIndex()
        idx.build([], small_embedder)
        results = idx.search("anything", small_embedder)
        assert results == []

    def test_search_returns_all_when_top_k_exceeds_size(
        self, indexed: tuple[DenseIndex, StubEmbedder]
    ) -> None:
        """ถ้า top_k > จำนวน chunks → คืนทุก chunk."""
        idx, emb = indexed
        results = idx.search("วิชา", emb, top_k=1000)
        assert len(results) == idx.size

    def test_hit_has_version_property(
        self, indexed: tuple[DenseIndex, StubEmbedder]
    ) -> None:
        """DenseHit.version สร้าง CurriculumVersion ได้ถูกต้อง."""
        idx, emb = indexed
        results = idx.search("คณิตศาสตร์", emb)
        for r in results:
            v = r.version
            assert isinstance(v, CurriculumVersion)

    def test_hit_has_text_snippet(
        self, indexed: tuple[DenseIndex, StubEmbedder]
    ) -> None:
        """DenseHit มี text_snippet ไม่ว่าง."""
        idx, emb = indexed
        results = idx.search("คณิตศาสตร์", emb)
        for r in results:
            assert len(r.text_snippet) > 0

    def test_scores_are_cosine_similarity_range(
        self, indexed: tuple[DenseIndex, StubEmbedder]
    ) -> None:
        """คะแนน cosine similarity อยู่ในช่วง [-1, 1]."""
        idx, emb = indexed
        results = idx.search("test", emb)
        for r in results:
            assert -1.0 <= r.score <= 1.0 + 1e-6  # tolerance for float

    def test_no_ann_full_scan(self, small_embedder: StubEmbedder) -> None:
        """R13.4: ยืนยันว่า search เป็น full scan — ทุก chunk ถูกพิจารณา.

        ทดสอบโดยตรวจว่าผลลัพธ์สามารถคืน chunk ที่มีคะแนนต่ำสุดได้
        (ANN อาจพลาด chunk ที่ score ต่ำแต่ full scan ไม่พลาด)
        """
        idx = DenseIndex()
        chunks = [_make_chunk(f"unique content {i}") for i in range(50)]
        idx.build(chunks, small_embedder)

        # ค้นด้วย top_k เท่ากับ corpus size → ต้องคืนครบทุก chunk
        results = idx.search("query text", small_embedder, top_k=50)
        assert len(results) == 50


# ── LatencyTracker tests ──────────────────────────────────────────────


class TestLatencyTracker:
    """ทดสอบ LatencyTracker สำหรับ p95 budget."""

    def test_empty_tracker_p95_none(self) -> None:
        tracker = LatencyTracker()
        assert tracker.p95 is None
        assert tracker.count == 0

    def test_single_record(self) -> None:
        tracker = LatencyTracker()
        tracker.record(1.5)
        assert tracker.p95 == 1.5
        assert tracker.count == 1

    def test_p95_calculation(self) -> None:
        """p95 ของ 20 ค่า 0.1-2.0 → ค่า p95 ≈ ค่าที่ index 95%."""
        tracker = LatencyTracker()
        for i in range(20):
            tracker.record(0.1 * (i + 1))

        p95 = tracker.p95
        assert p95 is not None
        # p95 ของ [0.1, 0.2, ..., 2.0] อยู่ที่ index 19 (95% ของ 20 = 19)
        assert p95 >= 1.8

    def test_within_budget_true(self) -> None:
        """ถ้า latency ทุกตัวอยู่ในงบ → is_within_budget = True."""
        tracker = LatencyTracker()
        for _ in range(10):
            tracker.record(0.5)
        assert tracker.is_within_budget(3.0) is True

    def test_within_budget_false(self) -> None:
        """ถ้า latency เกินงบ → is_within_budget = False."""
        tracker = LatencyTracker()
        for _ in range(20):
            tracker.record(5.0)  # เกิน 3.0
        assert tracker.is_within_budget(3.0) is False

    def test_within_budget_empty(self) -> None:
        """ไม่มีข้อมูล → ถือว่าอยู่ในงบ."""
        tracker = LatencyTracker()
        assert tracker.is_within_budget(3.0) is True

    def test_window_size_respected(self) -> None:
        """เกิน window_size → ค่าเก่าถูกลบ."""
        tracker = LatencyTracker(window_size=5)
        for i in range(10):
            tracker.record(float(i))
        assert tracker.count == 5

    def test_search_records_latency(self, small_embedder: StubEmbedder) -> None:
        """search() ต้องบันทึก latency ใน tracker."""
        idx = DenseIndex(p95_budget_seconds=3.0)
        chunks = [_make_chunk(f"chunk {i}") for i in range(5)]
        idx.build(chunks, small_embedder)

        assert idx.latency_tracker.count == 0
        idx.search("query", small_embedder)
        assert idx.latency_tracker.count == 1

    def test_p95_within_budget_small_corpus(self, small_embedder: StubEmbedder) -> None:
        """R13.4: corpus เล็ก → p95 latency ต้องอยู่ในงบ 3.0 วินาที."""
        idx = DenseIndex(p95_budget_seconds=3.0)
        # corpus ~100 chunks ควร search ได้เร็วมาก
        chunks = [_make_chunk(f"content number {i} for testing") for i in range(100)]
        idx.build(chunks, small_embedder)

        # ทำ search หลายครั้งเพื่อเก็บ latency
        for i in range(20):
            idx.search(f"query {i}", small_embedder)

        assert idx.latency_tracker.is_within_budget(3.0)
        p95 = idx.latency_tracker.p95
        assert p95 is not None
        assert p95 < 3.0
