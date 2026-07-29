"""Packed MaxSim reranker — late-interaction cosine similarity (R13.6, R13.7, R13.8).

อัลกอริทึมนี้เขียนใหม่เป็น Python (NumPy batched matmul) จากแนวคิดใน `katgpt-rs`
(`crates/katgpt-types/src/simd/maxsim.rs`: `maxsim_score`, `maxsim_score_packed`)
— ดู `third_party/katgpt-rs-MIT-NOTICE.md`
ไม่มีการ import จาก `katgpt-rs/` (R20.4, R20.5)

MIT License — Copyright (c) 2026 Todsaporn Banjerdkit
See: third_party/katgpt-rs-MIT-NOTICE.md

Feature flag (R13.8):
- maxsim_enabled: default OFF
- maxsim_status: "pending_ablation" — ต้องผ่าน ablation test บนข้อมูลจริงก่อนเปิด

Rerank behavior (R13.6):
- จัดอันดับใหม่เฉพาะอันดับ 1 ถึง `rerank_depth` (default 20)
- อันดับที่ต่ำกว่า `rerank_depth` คงลำดับเดิมไว้ต่อท้าย
"""

from __future__ import annotations

from typing import Sequence

import numpy as np
from numpy.typing import NDArray

# ── feature flag ──────────────────────────────────────────────────────

MAXSIM_ENABLED_DEFAULT: bool = False
MAXSIM_STATUS: str = "pending_ablation"
RERANK_DEPTH_DEFAULT: int = 20


# ── core scoring ──────────────────────────────────────────────────────


def maxsim_score(
    query_tokens: NDArray[np.floating],
    doc_tokens: NDArray[np.floating],
) -> float:
    """คำนวณ MaxSim score ระหว่าง query กับ document หนึ่งอัน.

    MaxSim = mean over query tokens of (max cosine similarity to any doc token)

    Args:
        query_tokens: shape (num_query_tokens, embedding_dim) — L2-normalized
        doc_tokens: shape (num_doc_tokens, embedding_dim) — L2-normalized

    Returns:
        MaxSim score (float) — ค่าเฉลี่ยของ max cosine similarity ต่อ query token

    หมายเหตุ: input ต้อง L2-normalized มาแล้ว (cosine sim = dot product)
    ถ้าไม่ normalized ฟังก์ชันจะ normalize ให้ (safe แต่ช้ากว่า)
    """
    q = np.asarray(query_tokens, dtype=np.float32)
    d = np.asarray(doc_tokens, dtype=np.float32)

    if q.ndim != 2 or d.ndim != 2:
        raise ValueError(
            f"query_tokens ต้องเป็น 2D (got {q.ndim}D), "
            f"doc_tokens ต้องเป็น 2D (got {d.ndim}D)"
        )

    if q.shape[0] == 0 or d.shape[0] == 0:
        return 0.0

    if q.shape[1] != d.shape[1]:
        raise ValueError(
            f"embedding dim ไม่ตรง: query {q.shape[1]} vs doc {d.shape[1]}"
        )

    # L2-normalize (safe ถ้า input normalized แล้วจะไม่เปลี่ยนค่า)
    q_norms = np.linalg.norm(q, axis=1, keepdims=True)
    q_norms = np.where(q_norms == 0, 1.0, q_norms)
    q_normalized = q / q_norms

    d_norms = np.linalg.norm(d, axis=1, keepdims=True)
    d_norms = np.where(d_norms == 0, 1.0, d_norms)
    d_normalized = d / d_norms

    # cosine similarity matrix: (num_query_tokens, num_doc_tokens)
    sim_matrix = q_normalized @ d_normalized.T

    # MaxSim: สำหรับแต่ละ query token หาค่า max similarity กับ doc tokens ทั้งหมด
    max_sims = np.max(sim_matrix, axis=1)  # shape: (num_query_tokens,)

    # ค่าเฉลี่ยของ max similarities
    return float(np.mean(max_sims))


def maxsim_score_packed(
    query_tokens: NDArray[np.floating],
    doc_tokens_list: Sequence[NDArray[np.floating]],
) -> list[float]:
    """คำนวณ MaxSim score สำหรับหลาย document พร้อมกัน (packed batch).

    Args:
        query_tokens: shape (num_query_tokens, embedding_dim)
        doc_tokens_list: รายการของ doc token arrays แต่ละอัน shape (num_doc_tokens_i, dim)

    Returns:
        รายการ MaxSim score ต่อ document (เท่ากับจำนวน doc_tokens_list)
    """
    return [maxsim_score(query_tokens, doc_tokens) for doc_tokens in doc_tokens_list]


# ── reranker ──────────────────────────────────────────────────────────


def rerank_maxsim(
    scored_chunks: Sequence[tuple[str, float]],
    query_tokens: NDArray[np.floating],
    doc_tokens_map: dict[str, NDArray[np.floating]],
    *,
    rerank_depth: int = RERANK_DEPTH_DEFAULT,
    maxsim_enabled: bool = MAXSIM_ENABLED_DEFAULT,
) -> list[tuple[str, float]]:
    """จัดอันดับใหม่ด้วย MaxSim เฉพาะ top rerank_depth รายการ.

    Args:
        scored_chunks: รายการ (chunk_id, score) เรียงตามคะแนนจากสูงไปต่ำแล้ว
        query_tokens: embedding tokens ของ query — shape (num_tokens, dim)
        doc_tokens_map: dict chunk_id -> embedding tokens ของ document
        rerank_depth: จำนวนอันดับที่จะ rerank (R13.6: 20-40, default 20)
        maxsim_enabled: feature flag (R13.8: default OFF)

    Returns:
        รายการ (chunk_id, score) ที่จัดอันดับใหม่
        - ถ้า maxsim_enabled=False → คืน input ตามเดิมโดยไม่เปลี่ยน
        - ถ้า maxsim_enabled=True →
          * อันดับ 1-rerank_depth: เรียงใหม่ด้วย MaxSim score
          * อันดับ rerank_depth+1 เป็นต้นไป: คงลำดับเดิม ต่อท้าย

    ข้อบังคับ:
        - ห้ามเพิ่มหรือลบ chunk: len(output) == len(input) เสมอ
    """
    # Feature flag check (R13.8): ถ้าปิดอยู่ คืน input ตามเดิม
    if not maxsim_enabled:
        return list(scored_chunks)

    chunks_list = list(scored_chunks)
    total = len(chunks_list)

    if total == 0:
        return []

    # แบ่งส่วนที่ rerank กับส่วนที่คงลำดับเดิม
    depth = min(rerank_depth, total)
    top_chunks = chunks_list[:depth]
    tail_chunks = chunks_list[depth:]

    # คำนวณ MaxSim score สำหรับ top chunks
    reranked: list[tuple[str, float]] = []
    for chunk_id, _original_score in top_chunks:
        doc_tokens = doc_tokens_map.get(chunk_id)
        if doc_tokens is not None and doc_tokens.shape[0] > 0:
            ms_score = maxsim_score(query_tokens, doc_tokens)
        else:
            # ถ้าไม่มี embedding → ใช้ score 0.0 (ตกไปท้ายของ top group)
            ms_score = 0.0
        reranked.append((chunk_id, ms_score))

    # เรียงใหม่ตาม MaxSim score (descending) — tie-break ด้วย chunk_id ascending
    reranked.sort(key=lambda item: (-item[1], item[0]))

    # ต่อท้ายด้วยส่วนที่คงลำดับเดิม
    return reranked + tail_chunks
