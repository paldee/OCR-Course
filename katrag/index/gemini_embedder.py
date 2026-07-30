"""Gemini Embedding backend — ใช้ Google Generative AI Embedding API.

model: gemini-embedding-001 (dim=3072, free tier 1500 RPM)
ใช้สำหรับ semantic retrieval (dense arm ของ Hybrid Retriever)
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from typing import Sequence

import numpy as np


class GeminiEmbedder:
    """Gemini Embedding API embedder — online, high quality, free tier."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "models/gemini-embedding-001",
        batch_size: int = 20,
        requests_per_minute: int = 1400,
    ) -> None:
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        if not self._api_key:
            raise ValueError("GEMINI_API_KEY not set")
        self._model = model
        self._batch_size = batch_size
        self._min_interval = 60.0 / requests_per_minute
        self._last_request_time = 0.0
        self._dim: int | None = None

    @property
    def dim(self) -> int:
        if self._dim is None:
            # probe
            result = self._embed_single("test")
            self._dim = len(result)
        return self._dim

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        """Encode texts → np.ndarray (n, dim)."""
        if not texts:
            return np.empty((0, self.dim), dtype=np.float32)

        all_embeddings: list[list[float]] = []
        for i in range(0, len(texts), self._batch_size):
            batch = texts[i:i + self._batch_size]
            embeddings = self._embed_batch(batch)
            all_embeddings.extend(embeddings)

        arr = np.array(all_embeddings, dtype=np.float32)
        # L2 normalize
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms = np.clip(norms, 1e-12, None)
        arr = arr / norms
        return arr

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed batch ผ่าน batchEmbedContents API."""
        # Rate limiting
        elapsed = time.time() - self._last_request_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)

        url = f"https://generativelanguage.googleapis.com/v1beta/{self._model}:batchEmbedContents?key={self._api_key}"
        requests_body = [
            {"content": {"parts": [{"text": t[:2048]}]}, "model": self._model}
            for t in texts
        ]
        data = json.dumps({"requests": requests_body}).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})

        for attempt in range(3):
            try:
                self._last_request_time = time.time()
                resp = urllib.request.urlopen(req, timeout=30)
                result = json.loads(resp.read().decode())
                embeddings = [e["values"] for e in result.get("embeddings", [])]
                if self._dim is None and embeddings:
                    self._dim = len(embeddings[0])
                return embeddings
            except Exception as e:
                if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                    wait = 2 ** attempt * 5
                    print(f"    Rate limited, waiting {wait}s...")
                    time.sleep(wait)
                else:
                    raise
        raise RuntimeError("Failed after 3 retries")

    def _embed_single(self, text: str) -> list[float]:
        """Single text embedding."""
        url = f"https://generativelanguage.googleapis.com/v1beta/{self._model}:embedContent?key={self._api_key}"
        data = json.dumps({"content": {"parts": [{"text": text}]}}).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req, timeout=15)
        result = json.loads(resp.read().decode())
        return result["embedding"]["values"]
