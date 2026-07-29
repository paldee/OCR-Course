"""Property test ของ retrieval pipeline (task 16.3).

คุณสมบัติที่ทดสอบ:
1. Determinism: คำถามเดียวกันบนดัชนีเดิมได้ลำดับผลลัพธ์เดิมทุกครั้ง
2. Phrase boost membership invariant: apply_phrase_boost ไม่เพิ่ม/ลบ chunk —
   set ของ chunk_id ใน output == set ของ chunk_id ใน input
3. MaxSim rerank membership invariant: rerank_maxsim ไม่เพิ่ม/ลบ chunk —
   set ของ chunk_id ใน output == set ของ chunk_id ใน input;
   เปลี่ยนลำดับเฉพาะส่วนหัว (top rerank_depth)
4. Version isolation: เมื่อตั้ง version_filter แล้ว ไม่มี chunk นอก version set
   ปรากฏในผลลัพธ์

**Validates: Requirements 13.3, 13.5, 13.6, 10.5**
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from katrag.common.maxsim import rerank_maxsim
from katrag.common.phrase_boost import apply_phrase_boost
from katrag.common.types import CurriculumVersion
from katrag.index.dense import DenseHit
from katrag.index.lexical import LexicalHit
from katrag.query.hybrid_retriever import (
    HybridRetrievalResponse,
    RetrievalResult,
    retrieve,
)

PROPERTY_SETTINGS = settings(max_examples=200, deadline=None)

# ══════════════════════════════════════════════════════════════════════
# Strategies
# ══════════════════════════════════════════════════════════════════════

# ── chunk id: content_sha256 hex strings ──────────────────────────────

_chunk_id_st = st.text(
    alphabet="0123456789abcdef",
    min_size=8,
    max_size=64,
)

# ── score: float 0-1 ──────────────────────────────────────────────────

_score_st = st.floats(min_value=0.01, max_value=1.0, allow_nan=False)

# ── query text: non-empty, at most 500 chars (within valid limit) ─────

_query_st = st.text(
    alphabet=st.characters(categories=("L", "N", "P", "Z")),
    min_size=1,
    max_size=200,
).filter(lambda s: s.strip())

# ── domain lexicon terms ──────────────────────────────────────────────

_term_st = st.text(
    alphabet=st.characters(categories=("L", "N")),
    min_size=2,
    max_size=20,
)

_lexicon_category_st = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz_",
    min_size=3,
    max_size=15,
)

# ── scored_chunks: list of (chunk_id, score) with unique chunk_ids ────


@st.composite
def scored_chunks_st(
    draw: st.DrawFn, min_size: int = 1, max_size: int = 30
) -> list[tuple[str, float]]:
    """สุ่มรายการ (chunk_id, score) ที่มี chunk_id ไม่ซ้ำกัน."""
    n = draw(st.integers(min_value=min_size, max_value=max_size))
    ids = draw(
        st.lists(_chunk_id_st, min_size=n, max_size=n, unique=True)
    )
    scores = draw(
        st.lists(_score_st, min_size=n, max_size=n)
    )
    # เรียงจากคะแนนสูงไปต่ำ (เหมือนผลลัพธ์จริงจาก retriever)
    pairs = sorted(zip(ids, scores), key=lambda x: -x[1])
    return [(cid, sc) for cid, sc in pairs]


# ── chunk texts: dict chunk_id -> text ────────────────────────────────


@st.composite
def chunk_texts_st(
    draw: st.DrawFn, chunk_ids: list[str]
) -> dict[str, str]:
    """สุ่มข้อความของแต่ละ chunk_id."""
    texts: dict[str, str] = {}
    for cid in chunk_ids:
        texts[cid] = draw(
            st.text(
                alphabet=st.characters(categories=("L", "N", "Z")),
                min_size=5,
                max_size=100,
            )
        )
    return texts


# ── domain lexicon strategy ───────────────────────────────────────────


@st.composite
def domain_lexicon_st(draw: st.DrawFn) -> dict:
    """สุ่ม domain lexicon (terms + boost)."""
    n_categories = draw(st.integers(min_value=1, max_value=3))
    terms: dict[str, list[str]] = {}
    boost: dict[str, float] = {}
    for _ in range(n_categories):
        cat = draw(_lexicon_category_st)
        cat_terms = draw(st.lists(_term_st, min_size=1, max_size=5))
        terms[cat] = cat_terms
        boost[cat] = draw(st.floats(min_value=1.0, max_value=2.0, allow_nan=False))
    boost["max_total_multiplier"] = 3.0
    return {"terms": terms, "boost": boost}


# ── LexicalHit / DenseHit factory from chunk_ids ──────────────────────

_VALID_VERSION = CurriculumVersion(
    program="IT", curriculum_year=2565, edition_status="current"
)


def _make_lexical_hits(
    chunk_ids: list[str],
    version: CurriculumVersion = _VALID_VERSION,
) -> list[LexicalHit]:
    """สร้าง LexicalHit list จาก chunk_ids."""
    return [
        LexicalHit(
            chunk_id=cid,
            score=1.0 / (i + 1),
            heading=f"heading_{cid[:8]}",
            text_snippet=f"snippet_{cid[:8]}",
            program=version.program,
            curriculum_year=version.curriculum_year,
            edition_status=version.edition_status,
        )
        for i, cid in enumerate(chunk_ids)
    ]


def _make_dense_hits(
    chunk_ids: list[str],
    version: CurriculumVersion = _VALID_VERSION,
) -> list[DenseHit]:
    """สร้าง DenseHit list จาก chunk_ids."""
    return [
        DenseHit(
            chunk_id=cid,
            score=1.0 / (i + 1),
            heading=f"heading_{cid[:8]}",
            text_snippet=f"snippet_{cid[:8]}",
            program=version.program,
            curriculum_year=version.curriculum_year,
            edition_status=version.edition_status,
        )
        for i, cid in enumerate(chunk_ids)
    ]


# ══════════════════════════════════════════════════════════════════════
# Property 1: Determinism — same query + same index → same ordered results
# ══════════════════════════════════════════════════════════════════════


@given(
    query=_query_st,
    chunk_ids=st.lists(_chunk_id_st, min_size=1, max_size=20, unique=True),
)
@PROPERTY_SETTINGS
def test_retrieve_determinism(query: str, chunk_ids: list[str]) -> None:
    """คำถามเดียวกันบนดัชนีเดิมได้ลำดับผลลัพธ์เดิมทุกครั้ง.

    เรียก retrieve สองครั้งด้วย query เดียวกัน mock searcher เดียวกัน
    ต้องได้ผลลัพธ์เหมือนกันทั้งลำดับและค่า score.

    **Validates: Requirement 13.3**
    """
    lexical_hits = _make_lexical_hits(chunk_ids)
    dense_hits = _make_dense_hits(chunk_ids)

    def lexical_searcher(
        query_text: str,
        *,
        version_filter: CurriculumVersion | None = None,
        top_k: int = 100,
    ) -> list[LexicalHit]:
        return lexical_hits

    def dense_searcher(
        query_text: str,
        *,
        version_filter: CurriculumVersion | None = None,
        top_k: int = 100,
    ) -> list[DenseHit]:
        return dense_hits

    response_1 = retrieve(
        query,
        lexical_searcher=lexical_searcher,
        dense_searcher=dense_searcher,
    )
    response_2 = retrieve(
        query,
        lexical_searcher=lexical_searcher,
        dense_searcher=dense_searcher,
    )

    # ผลลัพธ์ต้องเหมือนกันทุกประการ (ลำดับ + คะแนน)
    assert len(response_1.results) == len(response_2.results), (
        f"result count differs: {len(response_1.results)} vs {len(response_2.results)}"
    )
    for i, (r1, r2) in enumerate(zip(response_1.results, response_2.results)):
        assert r1.chunk_id == r2.chunk_id, (
            f"chunk_id differs at position {i}: {r1.chunk_id} vs {r2.chunk_id}"
        )
        assert r1.fused_score == r2.fused_score, (
            f"fused_score differs at position {i}: {r1.fused_score} vs {r2.fused_score}"
        )


# ══════════════════════════════════════════════════════════════════════
# Property 2: Phrase boost membership invariant
# ══════════════════════════════════════════════════════════════════════


@given(
    data=st.data(),
    chunks=scored_chunks_st(min_size=1, max_size=25),
    lexicon=domain_lexicon_st(),
)
@PROPERTY_SETTINGS
def test_phrase_boost_preserves_membership(
    data: st.DataObject,
    chunks: list[tuple[str, float]],
    lexicon: dict,
) -> None:
    """apply_phrase_boost ไม่เพิ่มหรือลบ chunk ออกจากชุดผลลัพธ์.

    Set ของ chunk_id ใน output == set ของ chunk_id ใน input.

    **Validates: Requirement 13.5**
    """
    chunk_ids = [cid for cid, _ in chunks]
    chunk_texts = data.draw(chunk_texts_st(chunk_ids))

    result = apply_phrase_boost(
        scored_chunks=chunks,
        chunk_texts=chunk_texts,
        domain_lexicon=lexicon,
    )

    input_ids = {cid for cid, _ in chunks}
    output_ids = {cid for cid, _ in result}

    assert input_ids == output_ids, (
        f"membership changed!\n"
        f"  added: {output_ids - input_ids}\n"
        f"  removed: {input_ids - output_ids}"
    )
    assert len(result) == len(chunks), (
        f"length changed: input={len(chunks)}, output={len(result)}"
    )


# ══════════════════════════════════════════════════════════════════════
# Property 3: MaxSim rerank membership invariant
# ══════════════════════════════════════════════════════════════════════


@given(
    chunks=scored_chunks_st(min_size=1, max_size=30),
    rerank_depth=st.integers(min_value=1, max_value=40),
    embed_dim=st.integers(min_value=4, max_value=16),
    n_query_tokens=st.integers(min_value=1, max_value=8),
)
@PROPERTY_SETTINGS
def test_maxsim_rerank_preserves_membership(
    chunks: list[tuple[str, float]],
    rerank_depth: int,
    embed_dim: int,
    n_query_tokens: int,
) -> None:
    """rerank_maxsim ไม่เพิ่มหรือลบ chunk ออกจากชุดผลลัพธ์.

    Set ของ chunk_id ใน output == set ของ chunk_id ใน input.
    เปลี่ยนลำดับเฉพาะ top rerank_depth; ส่วนท้ายคงลำดับเดิม.

    **Validates: Requirement 13.6**
    """
    # สร้าง fake query tokens
    rng = np.random.default_rng(42)
    query_tokens = rng.standard_normal((n_query_tokens, embed_dim)).astype(np.float32)

    # สร้าง fake doc tokens สำหรับแต่ละ chunk
    doc_tokens_map: dict[str, np.ndarray] = {}
    for cid, _ in chunks:
        n_doc_tokens = rng.integers(1, 10)
        doc_tokens_map[cid] = rng.standard_normal(
            (n_doc_tokens, embed_dim)
        ).astype(np.float32)

    result = rerank_maxsim(
        scored_chunks=chunks,
        query_tokens=query_tokens,
        doc_tokens_map=doc_tokens_map,
        rerank_depth=rerank_depth,
        maxsim_enabled=True,
    )

    input_ids = {cid for cid, _ in chunks}
    output_ids = {cid for cid, _ in result}

    # membership ต้องเท่ากัน
    assert input_ids == output_ids, (
        f"membership changed!\n"
        f"  added: {output_ids - input_ids}\n"
        f"  removed: {input_ids - output_ids}"
    )
    assert len(result) == len(chunks), (
        f"length changed: input={len(chunks)}, output={len(result)}"
    )

    # ส่วนท้าย (หลัง rerank_depth) ต้องคงลำดับเดิม
    effective_depth = min(rerank_depth, len(chunks))
    tail_input = [cid for cid, _ in chunks[effective_depth:]]
    tail_output = [cid for cid, _ in result[effective_depth:]]
    assert tail_input == tail_output, (
        f"tail order changed (positions {effective_depth}+)!\n"
        f"  input_tail:  {tail_input}\n"
        f"  output_tail: {tail_output}"
    )


@given(chunks=scored_chunks_st(min_size=1, max_size=20))
@PROPERTY_SETTINGS
def test_maxsim_rerank_disabled_returns_same_order(
    chunks: list[tuple[str, float]],
) -> None:
    """เมื่อ maxsim_enabled=False ผลลัพธ์คงลำดับเดิมทุกประการ.

    **Validates: Requirement 13.6 (feature flag guard)**
    """
    rng = np.random.default_rng(99)
    query_tokens = rng.standard_normal((4, 8)).astype(np.float32)
    doc_tokens_map: dict[str, np.ndarray] = {
        cid: rng.standard_normal((3, 8)).astype(np.float32)
        for cid, _ in chunks
    }

    result = rerank_maxsim(
        scored_chunks=chunks,
        query_tokens=query_tokens,
        doc_tokens_map=doc_tokens_map,
        maxsim_enabled=False,
    )

    assert result == chunks, "maxsim_enabled=False must return input unchanged"


# ══════════════════════════════════════════════════════════════════════
# Property 4: Version isolation — zero chunks outside version set
# ══════════════════════════════════════════════════════════════════════


@given(
    query=_query_st,
    chunk_ids=st.lists(_chunk_id_st, min_size=1, max_size=15, unique=True),
)
@PROPERTY_SETTINGS
def test_version_isolation(query: str, chunk_ids: list[str]) -> None:
    """เมื่อตั้ง version_filter ไม่มี chunk นอก version set ในผลลัพธ์.

    จำนวน chunk นอกชุดเวอร์ชันที่ส่งต่อเท่ากับศูนย์.

    Mock searchers จะคืนเฉพาะ chunk ที่ตรงกับ version_filter
    (จำลองพฤติกรรมจริงของ index ที่กรอง version ก่อน scoring)

    **Validates: Requirement 10.5**
    """
    target_version = CurriculumVersion(
        program="IT", curriculum_year=2565, edition_status="current"
    )
    other_version = CurriculumVersion(
        program="CE", curriculum_year=2560, edition_status="old"
    )

    # แบ่ง chunk_ids: ครึ่งแรกเป็น target version, ครึ่งหลังเป็น other version
    mid = max(1, len(chunk_ids) // 2)
    target_ids = chunk_ids[:mid]
    other_ids = chunk_ids[mid:]

    # Mock searcher: เมื่อมี version_filter จะคืนเฉพาะ chunk ที่ตรง version
    def lexical_searcher(
        query_text: str,
        *,
        version_filter: CurriculumVersion | None = None,
        top_k: int = 100,
    ) -> list[LexicalHit]:
        if version_filter is not None:
            # คืนเฉพาะ chunk ที่ตรง version (จำลอง FTS5 WHERE clause)
            return _make_lexical_hits(target_ids, version=target_version)
        # ไม่กรอง → คืนทุก chunk
        return _make_lexical_hits(target_ids, target_version) + _make_lexical_hits(
            other_ids, other_version
        )

    def dense_searcher(
        query_text: str,
        *,
        version_filter: CurriculumVersion | None = None,
        top_k: int = 100,
    ) -> list[DenseHit]:
        if version_filter is not None:
            return _make_dense_hits(target_ids, version=target_version)
        return _make_dense_hits(target_ids, target_version) + _make_dense_hits(
            other_ids, other_version
        )

    response = retrieve(
        query,
        lexical_searcher=lexical_searcher,
        dense_searcher=dense_searcher,
        version_filter=target_version,
    )

    # ตรวจว่าทุก chunk_id ในผลลัพธ์อยู่ใน target_ids (อยู่ใน version set)
    result_ids = {r.chunk_id for r in response.results}
    target_id_set = set(target_ids)
    outside_version = result_ids - target_id_set

    assert len(outside_version) == 0, (
        f"พบ {len(outside_version)} chunk นอก version set: {outside_version}\n"
        f"target_ids: {target_id_set}\n"
        f"result_ids: {result_ids}"
    )
