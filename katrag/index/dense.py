"""Dense index — exact full-scan cosine similarity retrieval (R13.2, R13.4, R13.12).

สร้าง dense embedding index จาก chunk และค้นหาด้วย exact cosine similarity scan
โดยไม่ใช้ ANN/FAISS/HNSW เพราะ corpus เล็ก (R13.4).

หลักการ:
- build() สร้าง embedding ให้ทุก chunk — จำนวน embedding = จำนวน chunk ที่สำเร็จ
- search() encode query แล้ว scan ครบทุก chunk ด้วย cosine similarity
- วัด p95 latency เทียบงบ retrieval.dense_p95_latency_budget_seconds = 3.0 วินาที
- ถ้า embedding chunk ใดล้มเหลว → ข้ามแล้วรายงาน index_build_incomplete (R13.12)
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

from katrag.common.types import CurriculumVersion
from katrag.errors import ReviewIssue
from katrag.index.embedder import Embedder
from katrag.ingest.chunker import Chunk

logger = logging.getLogger(__name__)

# ── result type ───────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DenseHit:
    """ผลลัพธ์หนึ่งรายการจาก dense retrieval."""

    chunk_id: str
    score: float
    heading: str
    text_snippet: str
    program: str
    curriculum_year: int
    edition_status: str

    @property
    def version(self) -> CurriculumVersion:
        return CurriculumVersion(
            program=self.program,
            curriculum_year=self.curriculum_year,
            edition_status=self.edition_status,  # type: ignore[arg-type]
        )


# ── latency tracker ──────────────────────────────────────────────────


@dataclass
class LatencyTracker:
    """ติดตาม search latency สำหรับคำนวณ p95.

    ใช้ sliding window ขนาดจำกัดเพื่อไม่ให้ memory โต unbounded.
    """

    window_size: int = 100
    _latencies: deque[float] = field(default_factory=deque)

    def record(self, elapsed_seconds: float) -> None:
        """บันทึก latency ของ search หนึ่งครั้ง."""
        self._latencies.append(elapsed_seconds)
        if len(self._latencies) > self.window_size:
            self._latencies.popleft()

    @property
    def p95(self) -> float | None:
        """คำนวณ p95 latency — คืน None ถ้ายังไม่มีข้อมูลพอ."""
        if not self._latencies:
            return None
        sorted_latencies = sorted(self._latencies)
        idx = int(len(sorted_latencies) * 0.95)
        idx = min(idx, len(sorted_latencies) - 1)
        return sorted_latencies[idx]

    @property
    def count(self) -> int:
        return len(self._latencies)

    def is_within_budget(self, budget_seconds: float) -> bool:
        """ตรวจว่า p95 อยู่ในงบหรือไม่ — True ถ้ายังไม่มีข้อมูลพอ."""
        p95_val = self.p95
        if p95_val is None:
            return True
        return p95_val <= budget_seconds


# ── DenseIndex ────────────────────────────────────────────────────────


class DenseIndex:
    """Dense embedding index ที่ใช้ exact full-scan cosine similarity (R13.4).

    ไม่ใช้ ANN/FAISS/HNSW — scan ครบทุก chunk ทุกครั้ง
    เหมาะกับ corpus ขนาดเล็ก (หลักสูตรไม่กี่พัน chunk).
    """

    def __init__(self, p95_budget_seconds: float = 3.0) -> None:
        """สร้าง DenseIndex.

        Args:
            p95_budget_seconds: งบ p95 latency สำหรับ search (R13.4)
                ค่าเริ่มต้น 3.0 วินาที (retrieval.dense_p95_latency_budget_seconds)
        """
        self._embeddings: np.ndarray | None = None  # shape (n_chunks, dim)
        self._chunks: list[Chunk] = []
        self._chunk_ids: list[str] = []  # content_sha256 ใช้เป็น ID
        self._p95_budget = p95_budget_seconds
        self._latency_tracker = LatencyTracker()
        self._dim: int = 0

    @property
    def size(self) -> int:
        """จำนวน chunks ที่ index สำเร็จ."""
        return len(self._chunks)

    @property
    def dim(self) -> int:
        """มิติของ embedding vectors."""
        return self._dim

    @property
    def latency_tracker(self) -> LatencyTracker:
        """Access latency tracker สำหรับตรวจสอบ p95."""
        return self._latency_tracker

    def build(
        self,
        chunks: Sequence[Chunk],
        embedder: Embedder,
        *,
        batch_size: int = 32,
    ) -> tuple[int, list[ReviewIssue]]:
        """สร้าง embedding index จาก chunks (R13.2, R13.12).

        ขั้นตอน:
        1. Encode ทุก chunk เป็น batch
        2. ถ้า chunk ใดล้มเหลว → ข้ามแล้วบันทึก
        3. จำนวน embedding สุดท้ายต้อง = จำนวน chunk ที่สำเร็จ

        Args:
            chunks: ลำดับ Chunk ที่ต้องการ index
            embedder: Embedder instance สำหรับสร้าง embedding
            batch_size: ขนาด batch สำหรับ encode

        Returns:
            (จำนวนที่ index สำเร็จ, รายการ ReviewIssue ถ้ามี chunk ล้มเหลว)
        """
        if not chunks:
            self._embeddings = np.empty((0, embedder.dim), dtype=np.float32)
            self._chunks = []
            self._chunk_ids = []
            self._dim = embedder.dim
            return 0, []

        self._dim = embedder.dim
        successful_chunks: list[Chunk] = []
        successful_embeddings: list[np.ndarray] = []
        failed_chunks: list[str] = []

        # Process in batches
        for batch_start in range(0, len(chunks), batch_size):
            batch_end = min(batch_start + batch_size, len(chunks))
            batch_chunks = chunks[batch_start:batch_end]

            # Encode ทีละ chunk ใน batch เพื่อ isolate failures (R13.12)
            for chunk in batch_chunks:
                try:
                    embedding = embedder.encode([chunk.content_text])
                    if embedding.shape != (1, self._dim):
                        raise ValueError(
                            f"embedding shape ไม่ถูกต้อง: expected (1, {self._dim}), "
                            f"got {embedding.shape}"
                        )
                    successful_chunks.append(chunk)
                    successful_embeddings.append(embedding[0])
                except Exception as exc:
                    logger.warning(
                        "embed chunk failed: sha256=%s, error=%s",
                        chunk.content_sha256,
                        str(exc),
                    )
                    failed_chunks.append(chunk.content_sha256)

        # สร้าง matrix จาก successful embeddings
        if successful_embeddings:
            self._embeddings = np.stack(successful_embeddings, axis=0).astype(np.float32)
        else:
            self._embeddings = np.empty((0, self._dim), dtype=np.float32)

        self._chunks = successful_chunks
        self._chunk_ids = [c.content_sha256 for c in successful_chunks]

        # สร้าง ReviewIssue ถ้ามี failure
        issues: list[ReviewIssue] = []
        if failed_chunks:
            issues.append(
                ReviewIssue(
                    kind="index_build_incomplete",
                    detail={
                        "index_type": "dense",
                        "failed_count": len(failed_chunks),
                        "failed_sha256": failed_chunks[:20],
                        "total_attempted": len(chunks),
                        "indexed_count": len(successful_chunks),
                    },
                )
            )

        logger.info(
            "DenseIndex built: %d/%d chunks indexed, dim=%d",
            len(successful_chunks),
            len(chunks),
            self._dim,
        )

        return len(successful_chunks), issues

    def search(
        self,
        query_text: str,
        embedder: Embedder,
        *,
        version_filter: CurriculumVersion | None = None,
        top_k: int = 100,
    ) -> list[DenseHit]:
        """ค้นหาด้วย exact cosine similarity scan (R13.4).

        ขั้นตอน:
        1. Encode query เป็น embedding
        2. คำนวณ cosine similarity กับทุก chunk (full scan, ไม่ใช้ ANN)
        3. กรอง version ถ้าระบุ
        4. เลือก top_k ผลลัพธ์คะแนนสูงสุด
        5. บันทึก latency สำหรับ p95 tracking

        Args:
            query_text: ข้อความค้นหา
            embedder: Embedder instance (ควรเป็นตัวเดียวกับที่ใช้ build)
            version_filter: กรองเฉพาะ curriculum version (R10.5)
            top_k: จำนวนผลลัพธ์สูงสุด (default 100 จาก retrieval.dense_top_k)

        Returns:
            รายการ DenseHit เรียงตามคะแนน cosine similarity จากมากไปน้อย
        """
        if not query_text.strip():
            return []

        if self._embeddings is None or self._embeddings.shape[0] == 0:
            return []

        start_time = time.perf_counter()

        # 1. Encode query
        query_embedding = embedder.encode([query_text])  # shape (1, dim)
        query_vec = query_embedding[0]  # shape (dim,)

        # 2. Exact cosine similarity scan — embeddings ถูก L2 normalize แล้ว
        #    cosine_sim = dot product เมื่อทั้ง query และ doc ถูก normalize
        # Normalize query vector
        query_norm = np.linalg.norm(query_vec)
        if query_norm > 1e-12:
            query_vec = query_vec / query_norm

        # Full scan: dot product กับทุก chunk
        scores = self._embeddings @ query_vec  # shape (n_chunks,)

        # 3. กรอง version ถ้าระบุ
        if version_filter is not None:
            mask = np.array(
                [
                    (c.program == version_filter.program
                     and c.curriculum_year == version_filter.curriculum_year
                     and c.edition_status == version_filter.edition_status)
                    for c in self._chunks
                ],
                dtype=bool,
            )
            # ตั้งคะแนน chunk ที่ไม่ตรง version เป็น -inf
            scores = np.where(mask, scores, -np.inf)

        # 4. เลือก top_k
        if top_k >= len(scores):
            top_indices = np.argsort(scores)[::-1]
        else:
            # ใช้ argpartition สำหรับ efficiency (แต่ corpus เล็กจึงไม่จำเป็นมาก)
            top_indices = np.argpartition(scores, -top_k)[-top_k:]
            # เรียงตามคะแนนจากมากไปน้อย
            top_indices = top_indices[np.argsort(scores[top_indices])[::-1]]

        # 5. สร้างผลลัพธ์
        results: list[DenseHit] = []
        for idx in top_indices:
            score_val = float(scores[idx])
            if score_val == -np.inf:
                break  # ข้ามที่ถูก filter ออก
            chunk = self._chunks[idx]
            results.append(
                DenseHit(
                    chunk_id=chunk.content_sha256,
                    score=score_val,
                    heading=chunk.heading,
                    text_snippet=chunk.content_text[:500],
                    program=chunk.program,
                    curriculum_year=chunk.curriculum_year,
                    edition_status=chunk.edition_status,
                )
            )

        # บันทึก latency
        elapsed = time.perf_counter() - start_time
        self._latency_tracker.record(elapsed)

        if not self._latency_tracker.is_within_budget(self._p95_budget):
            logger.warning(
                "dense search p95 latency %.3fs exceeds budget %.3fs",
                self._latency_tracker.p95,
                self._p95_budget,
            )

        return results
