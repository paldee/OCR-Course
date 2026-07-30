"""Dense search — exact full-scan cosine similarity ใช้ embedding จาก chunk_embedding table.

โหลด vectors จาก DB ตอน startup → full-scan cosine ตอน query
ไม่ต้อง encode ทุก chunk ซ้ำ เพราะ embedding ถูก persist แล้ว
"""

from __future__ import annotations

import sqlite3
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np

from katrag.index.gemini_embedder import GeminiEmbedder


@dataclass
class DenseSearchHit:
    chunk_id: int
    score: float
    text: str
    heading: str
    page_number: int
    program: str
    curriculum_year: int
    edition_status: str


class DenseSearchIndex:
    """In-memory dense index loaded from chunk_embedding table."""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = str(db_path)
        self._embeddings: np.ndarray | None = None
        self._chunk_ids: list[int] = []
        self._chunk_meta: dict[int, dict] = {}
        self._dim: int = 0
        self._embedder: GeminiEmbedder | None = None

    @property
    def size(self) -> int:
        return len(self._chunk_ids)

    @property
    def dim(self) -> int:
        return self._dim

    def load(self) -> int:
        """โหลด embeddings จาก DB → memory. Return จำนวน vectors loaded."""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row

        rows = conn.execute("""
            SELECT ce.chunk_id, ce.dim, ce.vector,
                   c.text, c.heading, c.page_number,
                   cv.program, cv.curriculum_year, cv.edition_status
            FROM chunk_embedding ce
            JOIN chunk c ON c.chunk_id = ce.chunk_id
            JOIN curriculum_version cv ON cv.version_id = c.version_id
        """).fetchall()
        conn.close()

        if not rows:
            return 0

        self._dim = rows[0]["dim"]
        vectors: list[np.ndarray] = []
        self._chunk_ids = []
        self._chunk_meta = {}

        for row in rows:
            chunk_id = row["chunk_id"]
            vec = np.frombuffer(row["vector"], dtype=np.float32)
            if len(vec) != self._dim:
                continue
            vectors.append(vec)
            self._chunk_ids.append(chunk_id)
            self._chunk_meta[chunk_id] = {
                "text": row["text"],
                "heading": row["heading"],
                "page_number": row["page_number"],
                "program": row["program"],
                "curriculum_year": row["curriculum_year"],
                "edition_status": row["edition_status"],
            }

        self._embeddings = np.stack(vectors, axis=0) if vectors else np.empty((0, self._dim))
        return len(self._chunk_ids)

    def search(
        self,
        query_text: str,
        *,
        version_filter: tuple[str, int] | None = None,  # (program, year) or None
        top_k: int = 50,
    ) -> list[DenseSearchHit]:
        """ค้น cosine similarity แบบ exact full-scan."""
        if self._embeddings is None or self._embeddings.shape[0] == 0:
            return []

        # Embed query
        if self._embedder is None:
            from dotenv import load_dotenv
            load_dotenv()
            self._embedder = GeminiEmbedder()

        query_vec = self._embedder.encode([query_text])[0]  # already L2 normalized

        # Full scan cosine similarity (vectors already normalized)
        scores = self._embeddings @ query_vec  # (n,)

        # Version filter
        if version_filter:
            prog, year = version_filter
            mask = np.array([
                self._chunk_meta[cid]["program"] == prog and
                self._chunk_meta[cid]["curriculum_year"] == year
                for cid in self._chunk_ids
            ], dtype=bool)
            scores = np.where(mask, scores, -np.inf)

        # Top-k
        if top_k >= len(scores):
            top_indices = np.argsort(scores)[::-1]
        else:
            top_indices = np.argpartition(scores, -top_k)[-top_k:]
            top_indices = top_indices[np.argsort(scores[top_indices])[::-1]]

        results: list[DenseSearchHit] = []
        for idx in top_indices[:top_k]:
            s = float(scores[idx])
            if s == -np.inf:
                break
            cid = self._chunk_ids[idx]
            meta = self._chunk_meta[cid]
            results.append(DenseSearchHit(
                chunk_id=cid,
                score=s,
                text=meta["text"],
                heading=meta["heading"],
                page_number=meta["page_number"],
                program=meta["program"],
                curriculum_year=meta["curriculum_year"],
                edition_status=meta["edition_status"],
            ))

        return results
