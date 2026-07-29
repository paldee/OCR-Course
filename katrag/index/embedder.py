"""Embedder — สร้าง embedding ด้วย bge-m3 (onnxruntime CPU) หรือ stub สำหรับทดสอบ (R13.2).

Architecture:
- Embedder protocol กำหนด interface: encode(texts) → np.ndarray shape (n, dim)
- BgeM3Embedder ใช้ onnxruntime ถ้ามี model file ที่ระบุ
- StubEmbedder สร้าง deterministic random vectors สำหรับทดสอบ

หลักการ:
- จำนวน embedding ต้องเท่ากับจำนวน text ที่ส่งเข้ามาเสมอ
- มิติของ embedding ต้องเท่ากันทุกตัว (1024 สำหรับ bge-m3)
- ถ้า embedding chunk ใดล้มเหลว → ข้ามแล้วรายงาน (R13.12 จัดการที่ DenseIndex)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol, Sequence, runtime_checkable

import numpy as np

logger = logging.getLogger(__name__)

# bge-m3 มาตรฐาน output dimension = 1024
DEFAULT_EMBEDDING_DIM = 1024


# ── Protocol ──────────────────────────────────────────────────────────


@runtime_checkable
class Embedder(Protocol):
    """Protocol สำหรับ embedding model — ใช้ได้กับ bge-m3 หรือ model อื่น."""

    @property
    def dim(self) -> int:
        """มิติของ embedding vector."""
        ...

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        """Encode batch ของข้อความเป็น embedding vectors.

        Args:
            texts: ลำดับข้อความที่ต้องการ encode

        Returns:
            np.ndarray shape (len(texts), self.dim) dtype float32
            จำนวนแถวต้องเท่ากับจำนวน text เสมอ
        """
        ...


# ── BgeM3Embedder (onnxruntime) ───────────────────────────────────────


class BgeM3Embedder:
    """Embedding ด้วย bge-m3 ONNX model บน CPU (R13.2).

    ใช้ onnxruntime InferenceSession สำหรับ forward pass
    และ tokenizers (HuggingFace) สำหรับ tokenization.

    ถ้า model file หรือ dependency ไม่พร้อม จะ raise ImportError/FileNotFoundError
    ให้ caller จัดการ fallback เอง.
    """

    def __init__(
        self,
        model_path: str | Path,
        tokenizer_path: str | Path | None = None,
        max_length: int = 512,
    ) -> None:
        """สร้าง BgeM3Embedder.

        Args:
            model_path: path ไปยัง .onnx model file
            tokenizer_path: path ไปยัง tokenizer directory (ถ้า None ใช้ directory เดียวกับ model)
            max_length: ความยาวสูงสุดของ token sequence
        """
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self._model_path = Path(model_path)
        if not self._model_path.is_file():
            raise FileNotFoundError(f"ไม่พบ ONNX model: {self._model_path}")

        # โหลด tokenizer
        tok_path = Path(tokenizer_path) if tokenizer_path else self._model_path.parent
        tokenizer_file = tok_path / "tokenizer.json"
        if not tokenizer_file.is_file():
            raise FileNotFoundError(f"ไม่พบ tokenizer.json: {tokenizer_file}")

        self._tokenizer = Tokenizer.from_file(str(tokenizer_file))
        self._tokenizer.enable_truncation(max_length=max_length)
        self._tokenizer.enable_padding(length=max_length)
        self._max_length = max_length

        # สร้าง ONNX session (CPU only)
        sess_options = ort.SessionOptions()
        sess_options.inter_op_num_threads = 1
        sess_options.intra_op_num_threads = 4
        self._session = ort.InferenceSession(
            str(self._model_path),
            sess_options=sess_options,
            providers=["CPUExecutionProvider"],
        )

        # ตรวจ output dimension จาก model metadata
        output_shape = self._session.get_outputs()[0].shape
        # shape อาจเป็น [None, dim] หรือ [batch, dim]
        self._dim: int = int(output_shape[-1]) if len(output_shape) >= 2 else DEFAULT_EMBEDDING_DIM

        logger.info(
            "BgeM3Embedder loaded: model=%s, dim=%d, max_length=%d",
            self._model_path.name,
            self._dim,
            self._max_length,
        )

    @property
    def dim(self) -> int:
        return self._dim

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        """Encode texts ด้วย ONNX model.

        Returns:
            np.ndarray shape (len(texts), self.dim) dtype float32
        """
        if not texts:
            return np.empty((0, self._dim), dtype=np.float32)

        # Tokenize batch
        encodings = self._tokenizer.encode_batch(list(texts))
        input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)

        # ONNX inference — input names อาจแตกต่างตาม model export
        input_names = [inp.name for inp in self._session.get_inputs()]
        feeds: dict[str, np.ndarray] = {}
        if "input_ids" in input_names:
            feeds["input_ids"] = input_ids
        if "attention_mask" in input_names:
            feeds["attention_mask"] = attention_mask
        # bge-m3 บาง export มี token_type_ids
        if "token_type_ids" in input_names:
            feeds["token_type_ids"] = np.zeros_like(input_ids)

        outputs = self._session.run(None, feeds)

        # output[0] อาจเป็น last_hidden_state (batch, seq, dim) หรือ pooled (batch, dim)
        raw_output = outputs[0]
        if raw_output.ndim == 3:
            # Mean pooling with attention mask
            mask_expanded = attention_mask[:, :, np.newaxis].astype(np.float32)
            sum_embeddings = np.sum(raw_output * mask_expanded, axis=1)
            sum_mask = np.clip(mask_expanded.sum(axis=1), a_min=1e-9, a_max=None)
            embeddings = sum_embeddings / sum_mask
        else:
            embeddings = raw_output

        # L2 normalize
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms = np.clip(norms, a_min=1e-12, a_max=None)
        embeddings = embeddings / norms

        return embeddings.astype(np.float32)


# ── StubEmbedder (deterministic สำหรับทดสอบ) ──────────────────────────


class StubEmbedder:
    """Deterministic stub embedder สำหรับ unit test.

    สร้าง embedding จาก hash ของ text เพื่อให้:
    - ผลซ้ำได้ (deterministic)
    - text เดียวกันได้ vector เดียวกัน
    - text ต่างกันได้ vector ต่างกัน (ส่วนใหญ่)
    - ทุก vector มี L2 norm = 1
    """

    def __init__(self, dim: int = DEFAULT_EMBEDDING_DIM) -> None:
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        """สร้าง deterministic normalized embedding จาก text content.

        Returns:
            np.ndarray shape (len(texts), self.dim) dtype float32
        """
        if not texts:
            return np.empty((0, self._dim), dtype=np.float32)

        embeddings = np.zeros((len(texts), self._dim), dtype=np.float32)
        for i, text in enumerate(texts):
            # ใช้ hash ของ text เป็น seed สำหรับ deterministic random
            seed = hash(text) % (2**31)
            rng = np.random.default_rng(seed)
            vec = rng.standard_normal(self._dim).astype(np.float32)
            # L2 normalize
            norm = np.linalg.norm(vec)
            if norm > 1e-12:
                vec = vec / norm
            embeddings[i] = vec

        return embeddings
