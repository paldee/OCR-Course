"""Unit tests for katrag.common.maxsim (R13.6, R13.7, R13.8).

ทดสอบ:
- MaxSim score คำนวณถูกต้อง (cosine similarity → max per query token → mean)
- Feature flag OFF → คืน input ตามเดิม
- Rerank เฉพาะ top rerank_depth, ส่วนที่เหลือคงลำดับเดิม
- ไม่เพิ่ม/ลบ chunk
- Edge cases: empty input, missing embeddings
"""

from __future__ import annotations

import numpy as np
import pytest

from katrag.common.maxsim import (
    MAXSIM_ENABLED_DEFAULT,
    MAXSIM_STATUS,
    RERANK_DEPTH_DEFAULT,
    maxsim_score,
    maxsim_score_packed,
    rerank_maxsim,
)


# ── feature flag defaults ─────────────────────────────────────────────


class TestFeatureFlag:
    """R13.8: MaxSim feature flag defaults."""

    def test_default_disabled(self) -> None:
        """Feature flag ต้องปิดเป็นค่าตั้งต้น."""
        assert MAXSIM_ENABLED_DEFAULT is False

    def test_status_pending_ablation(self) -> None:
        """Status ต้องเป็น pending_ablation."""
        assert MAXSIM_STATUS == "pending_ablation"

    def test_default_rerank_depth(self) -> None:
        """Default rerank depth ต้องเป็น 20."""
        assert RERANK_DEPTH_DEFAULT == 20


# ── maxsim_score ──────────────────────────────────────────────────────


class TestMaxSimScore:
    """R13.7: MaxSim scoring — late-interaction cosine similarity."""

    def test_identical_vectors_score_1(self) -> None:
        """Vector เหมือนกัน → cosine = 1.0 → MaxSim = 1.0."""
        v = np.array([[1.0, 0.0, 0.0]], dtype=np.float32)

        score = maxsim_score(v, v)

        assert abs(score - 1.0) < 1e-6

    def test_orthogonal_vectors_score_0(self) -> None:
        """Vector ตั้งฉาก → cosine = 0.0 → MaxSim = 0.0."""
        q = np.array([[1.0, 0.0]], dtype=np.float32)
        d = np.array([[0.0, 1.0]], dtype=np.float32)

        score = maxsim_score(q, d)

        assert abs(score) < 1e-6

    def test_multiple_query_tokens_averaged(self) -> None:
        """หลาย query tokens → ผลเฉลี่ยของ max per token."""
        # query: 2 tokens, doc: 1 token = [1, 0, 0]
        # token 1 = [1, 0, 0] → max sim = 1.0
        # token 2 = [0, 1, 0] → max sim = 0.0
        # mean = 0.5
        q = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32)
        d = np.array([[1.0, 0.0, 0.0]], dtype=np.float32)

        score = maxsim_score(q, d)

        assert abs(score - 0.5) < 1e-6

    def test_multiple_doc_tokens_max_selected(self) -> None:
        """หลาย doc tokens → เลือก max similarity ต่อ query token."""
        # query: 1 token = [1, 0]
        # doc: 2 tokens = [[0, 1], [1, 0]]
        # sim with token 1 = 0.0, sim with token 2 = 1.0
        # max = 1.0
        q = np.array([[1.0, 0.0]], dtype=np.float32)
        d = np.array([[0.0, 1.0], [1.0, 0.0]], dtype=np.float32)

        score = maxsim_score(q, d)

        assert abs(score - 1.0) < 1e-6

    def test_unnormalized_input_handled(self) -> None:
        """Input ที่ไม่ normalized → ฟังก์ชัน normalize ให้."""
        q = np.array([[3.0, 0.0]], dtype=np.float32)  # not unit length
        d = np.array([[0.0, 5.0]], dtype=np.float32)  # not unit length

        score = maxsim_score(q, d)

        # orthogonal → cos = 0
        assert abs(score) < 1e-6

    def test_empty_query_returns_0(self) -> None:
        """Query ว่าง → score = 0.0."""
        q = np.zeros((0, 3), dtype=np.float32)
        d = np.array([[1.0, 0.0, 0.0]], dtype=np.float32)

        score = maxsim_score(q, d)

        assert score == 0.0

    def test_empty_doc_returns_0(self) -> None:
        """Doc ว่าง → score = 0.0."""
        q = np.array([[1.0, 0.0, 0.0]], dtype=np.float32)
        d = np.zeros((0, 3), dtype=np.float32)

        score = maxsim_score(q, d)

        assert score == 0.0

    def test_dimension_mismatch_raises(self) -> None:
        """Embedding dim ไม่ตรง → raise ValueError."""
        q = np.array([[1.0, 0.0]], dtype=np.float32)
        d = np.array([[1.0, 0.0, 0.0]], dtype=np.float32)

        with pytest.raises(ValueError, match="embedding dim"):
            maxsim_score(q, d)

    def test_1d_input_raises(self) -> None:
        """Input ที่เป็น 1D → raise ValueError."""
        q = np.array([1.0, 0.0], dtype=np.float32)
        d = np.array([[1.0, 0.0]], dtype=np.float32)

        with pytest.raises(ValueError, match="2D"):
            maxsim_score(q, d)

    def test_known_score(self) -> None:
        """ทดสอบค่าที่คำนวณได้ล่วงหน้า."""
        # query: [[1, 1], [1, 0]]  (normalized: [[0.707, 0.707], [1, 0]])
        # doc: [[1, 0], [0, 1]]    (already normalized)
        # sim matrix:
        #   q[0]·d[0] = 0.707, q[0]·d[1] = 0.707 → max = 0.707
        #   q[1]·d[0] = 1.0,   q[1]·d[1] = 0.0   → max = 1.0
        # mean = (0.707 + 1.0) / 2 = 0.8535...
        q = np.array([[1.0, 1.0], [1.0, 0.0]], dtype=np.float32)
        d = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)

        score = maxsim_score(q, d)

        expected = (1.0 / np.sqrt(2) + 1.0) / 2.0
        assert abs(score - expected) < 1e-5


# ── maxsim_score_packed ───────────────────────────────────────────────


class TestMaxSimScorePacked:
    """Packed batch scoring."""

    def test_packed_matches_individual(self) -> None:
        """Packed result ต้องตรงกับการเรียก maxsim_score ทีละอัน."""
        q = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
        d1 = np.array([[1.0, 0.0]], dtype=np.float32)
        d2 = np.array([[0.0, 1.0]], dtype=np.float32)

        packed_scores = maxsim_score_packed(q, [d1, d2])
        individual_scores = [maxsim_score(q, d1), maxsim_score(q, d2)]

        assert len(packed_scores) == 2
        for p, i in zip(packed_scores, individual_scores):
            assert abs(p - i) < 1e-6

    def test_empty_list(self) -> None:
        """รายการ doc ว่าง → ผลว่าง."""
        q = np.array([[1.0, 0.0]], dtype=np.float32)

        result = maxsim_score_packed(q, [])

        assert result == []


# ── rerank_maxsim ─────────────────────────────────────────────────────


class TestRerankMaxSim:
    """R13.6: MaxSim reranker."""

    def test_disabled_returns_input_unchanged(self) -> None:
        """Feature flag OFF → คืน input ตามเดิม."""
        scored = [("c1", 0.9), ("c2", 0.8), ("c3", 0.7)]
        q = np.array([[1.0, 0.0]], dtype=np.float32)
        doc_map = {
            "c1": np.array([[0.0, 1.0]], dtype=np.float32),
            "c2": np.array([[1.0, 0.0]], dtype=np.float32),
            "c3": np.array([[0.5, 0.5]], dtype=np.float32),
        }

        result = rerank_maxsim(scored, q, doc_map, maxsim_enabled=False)

        assert result == scored

    def test_enabled_reranks_top_items(self) -> None:
        """Feature flag ON → rerank top items by MaxSim."""
        # c1: doc=[0,1] → sim with q=[1,0] = 0.0
        # c2: doc=[1,0] → sim with q=[1,0] = 1.0
        # c3: doc=[0.707,0.707] → sim ≈ 0.707
        scored = [("c1", 0.9), ("c2", 0.8), ("c3", 0.7)]
        q = np.array([[1.0, 0.0]], dtype=np.float32)
        doc_map = {
            "c1": np.array([[0.0, 1.0]], dtype=np.float32),
            "c2": np.array([[1.0, 0.0]], dtype=np.float32),
            "c3": np.array([[1.0, 1.0]], dtype=np.float32),  # normalized → [0.707, 0.707]
        }

        result = rerank_maxsim(
            scored, q, doc_map, rerank_depth=3, maxsim_enabled=True
        )

        # Expected order by MaxSim: c2 (1.0), c3 (~0.707), c1 (0.0)
        assert result[0][0] == "c2"
        assert result[1][0] == "c3"
        assert result[2][0] == "c1"

    def test_output_length_equals_input(self) -> None:
        """ห้ามเพิ่มหรือลบ chunk."""
        scored = [("c1", 0.9), ("c2", 0.8), ("c3", 0.7), ("c4", 0.6)]
        q = np.array([[1.0, 0.0]], dtype=np.float32)
        doc_map = {
            "c1": np.array([[1.0, 0.0]], dtype=np.float32),
            "c2": np.array([[0.0, 1.0]], dtype=np.float32),
            "c3": np.array([[1.0, 1.0]], dtype=np.float32),
            "c4": np.array([[0.5, 0.0]], dtype=np.float32),
        }

        result = rerank_maxsim(
            scored, q, doc_map, rerank_depth=2, maxsim_enabled=True
        )

        assert len(result) == len(scored)

    def test_tail_keeps_original_order(self) -> None:
        """อันดับที่เกิน rerank_depth คงลำดับเดิม."""
        scored = [("c1", 0.9), ("c2", 0.8), ("c3", 0.7), ("c4", 0.6), ("c5", 0.5)]
        q = np.array([[1.0, 0.0]], dtype=np.float32)
        doc_map = {
            "c1": np.array([[0.0, 1.0]], dtype=np.float32),  # low MaxSim
            "c2": np.array([[1.0, 0.0]], dtype=np.float32),  # high MaxSim
        }

        # rerank_depth=2 → only c1, c2 get reranked
        result = rerank_maxsim(
            scored, q, doc_map, rerank_depth=2, maxsim_enabled=True
        )

        # Top 2 reranked: c2 (score 1.0) before c1 (score 0.0)
        assert result[0][0] == "c2"
        assert result[1][0] == "c1"
        # Tail unchanged: c3, c4, c5 in original order with original scores
        assert result[2] == ("c3", 0.7)
        assert result[3] == ("c4", 0.6)
        assert result[4] == ("c5", 0.5)

    def test_chunk_ids_preserved(self) -> None:
        """ชุด chunk_id ใน output ต้องเหมือน input ทุกประการ."""
        scored = [("a", 0.5), ("b", 0.4), ("c", 0.3)]
        q = np.array([[1.0, 0.0]], dtype=np.float32)
        doc_map = {
            "a": np.array([[1.0, 0.0]], dtype=np.float32),
            "b": np.array([[0.0, 1.0]], dtype=np.float32),
            "c": np.array([[1.0, 1.0]], dtype=np.float32),
        }

        result = rerank_maxsim(
            scored, q, doc_map, rerank_depth=3, maxsim_enabled=True
        )

        input_ids = sorted(cid for cid, _ in scored)
        output_ids = sorted(cid for cid, _ in result)
        assert output_ids == input_ids

    def test_empty_input(self) -> None:
        """Input ว่าง → output ว่าง."""
        q = np.array([[1.0, 0.0]], dtype=np.float32)

        result = rerank_maxsim([], q, {}, maxsim_enabled=True)

        assert result == []

    def test_missing_embedding_gets_zero_score(self) -> None:
        """chunk ที่ไม่มี embedding → MaxSim = 0.0."""
        scored = [("c1", 0.9), ("c2", 0.8)]
        q = np.array([[1.0, 0.0]], dtype=np.float32)
        # c1 ไม่มี embedding, c2 มี
        doc_map = {
            "c2": np.array([[1.0, 0.0]], dtype=np.float32),
        }

        result = rerank_maxsim(
            scored, q, doc_map, rerank_depth=2, maxsim_enabled=True
        )

        # c2 ได้ MaxSim = 1.0, c1 ได้ 0.0 → c2 ก่อน c1
        assert result[0][0] == "c2"
        assert result[1][0] == "c1"

    def test_rerank_depth_larger_than_input(self) -> None:
        """rerank_depth > จำนวน chunk → rerank ทั้งหมด ไม่มี tail."""
        scored = [("c1", 0.5), ("c2", 0.9)]
        q = np.array([[1.0, 0.0]], dtype=np.float32)
        doc_map = {
            "c1": np.array([[1.0, 0.0]], dtype=np.float32),
            "c2": np.array([[0.0, 1.0]], dtype=np.float32),
        }

        result = rerank_maxsim(
            scored, q, doc_map, rerank_depth=100, maxsim_enabled=True
        )

        # c1 ได้ MaxSim 1.0, c2 ได้ 0.0
        assert result[0][0] == "c1"
        assert result[1][0] == "c2"
        assert len(result) == 2

    def test_tie_break_by_chunk_id(self) -> None:
        """MaxSim score เท่ากัน → เรียงตาม chunk_id ascending."""
        scored = [("z_chunk", 0.9), ("a_chunk", 0.8)]
        q = np.array([[1.0, 0.0]], dtype=np.float32)
        # ทั้งคู่ได้ MaxSim เท่ากัน
        doc_map = {
            "z_chunk": np.array([[1.0, 0.0]], dtype=np.float32),
            "a_chunk": np.array([[1.0, 0.0]], dtype=np.float32),
        }

        result = rerank_maxsim(
            scored, q, doc_map, rerank_depth=2, maxsim_enabled=True
        )

        # ทั้งคู่ score 1.0 → tie-break by chunk_id → "a_chunk" < "z_chunk"
        assert result[0][0] == "a_chunk"
        assert result[1][0] == "z_chunk"

    def test_disabled_does_not_compute(self) -> None:
        """Feature flag OFF → ไม่ต้องมี doc_tokens_map ก็ได้ (ไม่เข้าถึง)."""
        scored = [("c1", 0.9), ("c2", 0.8)]
        q = np.array([[1.0, 0.0]], dtype=np.float32)

        # Pass empty map — should work because flag is OFF
        result = rerank_maxsim(scored, q, {}, maxsim_enabled=False)

        assert result == [("c1", 0.9), ("c2", 0.8)]
