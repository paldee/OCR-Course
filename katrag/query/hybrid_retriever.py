"""Hybrid retriever — Reciprocal Rank Fusion ของ lexical + dense (R13.3, R13.10, R13.11, R10.5).

รวม top-100 จาก lexical (FTS5 BM25) กับ top-100 จาก dense (cosine similarity)
ด้วย RRF score แล้วคืนไม่เกิน 50 ผลลัพธ์ที่ดีที่สุด

สูตร RRF:
  score(d) = (lexical_weight / (rrf_k + rank_lexical(d)))
           + (dense_weight / (rrf_k + rank_dense(d)))

- version filter ถูกใช้ก่อน scoring — chunk นอก version set ต้องมีจำนวนเป็นศูนย์ (R10.5)
- query ว่าง หรือยาวเกิน 1,000 อักขระ ถูกปฏิเสธทันที ไม่เรียกดัชนีใด (R13.10)
- เมื่อไม่พบ chunk คืนผลว่างพร้อม status (R13.11)
- tie-break: chunk_id (content_sha256) ascending
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from katrag.common.types import CurriculumVersion
from katrag.config import RetrievalConfig
from katrag.index.dense import DenseHit
from katrag.index.lexical import LexicalHit


# ── result type ───────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """ผลลัพธ์หนึ่งรายการจาก hybrid retrieval."""

    chunk_id: str
    fused_score: float
    lexical_rank: int | None  # None ถ้าไม่อยู่ใน lexical top-k
    dense_rank: int | None  # None ถ้าไม่อยู่ใน dense top-k


@dataclass(frozen=True, slots=True)
class RetrievalStatus:
    """สถานะของการ retrieve — ใช้ร่วมกับ results เพื่อบอกสาเหตุเมื่อผลว่าง."""

    ok: bool
    reason: str  # "success", "empty_query", "query_too_long", "no_results"
    result_count: int


@dataclass(frozen=True, slots=True)
class HybridRetrievalResponse:
    """คำตอบจาก hybrid retriever รวม results และ status."""

    results: list[RetrievalResult]
    status: RetrievalStatus


# ── index protocols ───────────────────────────────────────────────────


class LexicalSearcher(Protocol):
    """Interface สำหรับ lexical index searcher."""

    def __call__(
        self,
        query_text: str,
        *,
        version_filter: CurriculumVersion | None = None,
        top_k: int = 100,
    ) -> list[LexicalHit]: ...


class DenseSearcher(Protocol):
    """Interface สำหรับ dense index searcher."""

    def __call__(
        self,
        query_text: str,
        *,
        version_filter: CurriculumVersion | None = None,
        top_k: int = 100,
    ) -> list[DenseHit]: ...


# ── retriever ─────────────────────────────────────────────────────────


# Default config values (match retrieval section in katrag.toml)
_DEFAULT_LEXICAL_TOP_K = 100
_DEFAULT_DENSE_TOP_K = 100
_DEFAULT_FUSION_OUTPUT_MAX = 50
_DEFAULT_FUSION_LEXICAL_WEIGHT = 0.5
_DEFAULT_FUSION_DENSE_WEIGHT = 0.5
_DEFAULT_FUSION_RRF_K = 60
_DEFAULT_MAX_QUESTION_CHARS = 1000


def retrieve(
    query_text: str,
    *,
    lexical_searcher: LexicalSearcher,
    dense_searcher: DenseSearcher,
    version_filter: CurriculumVersion | None = None,
    config: RetrievalConfig | None = None,
    max_question_chars: int = _DEFAULT_MAX_QUESTION_CHARS,
) -> HybridRetrievalResponse:
    """Hybrid retrieval ด้วย Reciprocal Rank Fusion.

    Args:
        query_text: ข้อความค้นหา
        lexical_searcher: callable ที่ค้น lexical index
        dense_searcher: callable ที่ค้น dense index
        version_filter: กรอง curriculum version (R10.5) — applied BEFORE scoring
        config: RetrievalConfig จากไฟล์ตั้งค่า (ถ้า None ใช้ค่า default)
        max_question_chars: จำนวนอักขระสูงสุดที่รับได้ (default 1000, R13.10)

    Returns:
        HybridRetrievalResponse ที่มีผลลัพธ์ไม่เกิน 50 รายการ พร้อม status
    """
    # ── R13.10: ปฏิเสธ query ที่ว่างหรือยาวเกิน ──
    if not query_text or not query_text.strip():
        return HybridRetrievalResponse(
            results=[],
            status=RetrievalStatus(ok=False, reason="empty_query", result_count=0),
        )

    if len(query_text) > max_question_chars:
        return HybridRetrievalResponse(
            results=[],
            status=RetrievalStatus(ok=False, reason="query_too_long", result_count=0),
        )

    # ── ดึงค่าจาก config ──
    if config is not None:
        lexical_top_k = config.lexical_top_k
        dense_top_k = config.dense_top_k
        fusion_output_max = config.fusion_output_max
        lexical_weight = config.fusion_lexical_weight
        dense_weight = config.fusion_dense_weight
        rrf_k = config.fusion_rrf_k
    else:
        lexical_top_k = _DEFAULT_LEXICAL_TOP_K
        dense_top_k = _DEFAULT_DENSE_TOP_K
        fusion_output_max = _DEFAULT_FUSION_OUTPUT_MAX
        lexical_weight = _DEFAULT_FUSION_LEXICAL_WEIGHT
        dense_weight = _DEFAULT_FUSION_DENSE_WEIGHT
        rrf_k = _DEFAULT_FUSION_RRF_K

    # ── ค้นทั้งสองดัชนี — version_filter ถูกส่งลงไปเพื่อกรอง BEFORE scoring (R10.5) ──
    lexical_hits = lexical_searcher(
        query_text, version_filter=version_filter, top_k=lexical_top_k
    )
    dense_hits = dense_searcher(
        query_text, version_filter=version_filter, top_k=dense_top_k
    )

    # ── สร้าง rank map (1-indexed: rank 1 = อันดับแรก) ──
    # chunk_id จาก LexicalHit อาจเป็น int (chunk_id ในฐานข้อมูล)
    # แต่ใน hybrid retriever เราใช้ content_sha256 string เป็น key
    # ทั้ง LexicalHit.chunk_id (int/str) จะถูกแปลงเป็น str ให้เป็น key เดียว

    lexical_rank_map: dict[str, int] = {}
    for rank, hit in enumerate(lexical_hits, start=1):
        cid = str(hit.chunk_id)
        if cid not in lexical_rank_map:
            lexical_rank_map[cid] = rank

    dense_rank_map: dict[str, int] = {}
    for rank, hit in enumerate(dense_hits, start=1):
        cid = str(hit.chunk_id)
        if cid not in dense_rank_map:
            dense_rank_map[cid] = rank

    # ── รวม chunk_id จากทั้งสองดัชนี ──
    all_chunk_ids = set(lexical_rank_map.keys()) | set(dense_rank_map.keys())

    if not all_chunk_ids:
        # R13.11: คืนผลว่างพร้อม status
        return HybridRetrievalResponse(
            results=[],
            status=RetrievalStatus(ok=True, reason="no_results", result_count=0),
        )

    # ── คำนวณ RRF score ──
    scored: list[tuple[str, float, int | None, int | None]] = []
    for chunk_id in all_chunk_ids:
        lex_rank = lexical_rank_map.get(chunk_id)
        dns_rank = dense_rank_map.get(chunk_id)

        score = 0.0
        if lex_rank is not None:
            score += lexical_weight / (rrf_k + lex_rank)
        if dns_rank is not None:
            score += dense_weight / (rrf_k + dns_rank)

        scored.append((chunk_id, score, lex_rank, dns_rank))

    # ── เรียงตาม score จากมากไปน้อย, tie-break ด้วย chunk_id ascending ──
    scored.sort(key=lambda item: (-item[1], item[0]))

    # ── ตัดผลลัพธ์ไม่เกิน fusion_output_max (50) ──
    top_results = scored[:fusion_output_max]

    results = [
        RetrievalResult(
            chunk_id=chunk_id,
            fused_score=fused_score,
            lexical_rank=lex_rank,
            dense_rank=dns_rank,
        )
        for chunk_id, fused_score, lex_rank, dns_rank in top_results
    ]

    return HybridRetrievalResponse(
        results=results,
        status=RetrievalStatus(ok=True, reason="success", result_count=len(results)),
    )
