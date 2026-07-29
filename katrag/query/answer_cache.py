"""Answer Cache — exact key match cache สำหรับคำตอบ (R10.10, R19.7).

Design:
- Cache key = normalized question + version set fingerprint + content hash ของ chunks
- Exact match เท่านั้น — ไม่มี approximate threshold path
- hit เมื่อ key ตรง 100%, miss เมื่อไม่ตรง
- ไม่มี TTL (cache อยู่ตลอด application lifetime)
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from katrag.common.types import CurriculumVersion, version_fingerprint


# ══════════════════════════════════════════════════════════════════════
# Data types
# ══════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class CacheEntry:
    """Entry ใน answer cache."""

    key: str
    answer_text: str
    citations_passed: int
    generation_time_seconds: float
    request_id: str


@dataclass(frozen=True, slots=True)
class CacheResult:
    """ผลลัพธ์จาก cache lookup."""

    hit: bool
    entry: CacheEntry | None = None


# ══════════════════════════════════════════════════════════════════════
# Answer Cache
# ══════════════════════════════════════════════════════════════════════


class AnswerCache:
    """Exact-match answer cache — no approximate threshold (R10.10).

    Cache key ประกอบจาก:
    1. Normalized question (lowercase, stripped, collapsed whitespace)
    2. Version set fingerprint (sorted, deterministic)
    3. Content hash ของ chunk texts (sha256)

    ถ้า key ไม่ตรง 100% → cache miss. ไม่มี fuzzy matching.
    """

    __slots__ = ("_store",)

    def __init__(self) -> None:
        self._store: dict[str, CacheEntry] = {}

    def lookup(
        self,
        question: str,
        versions: frozenset[CurriculumVersion],
        chunk_texts: list[str],
    ) -> CacheResult:
        """ค้นหาคำตอบใน cache — exact match only.

        Args:
            question: คำถามจากผู้ใช้
            versions: ชุด version ที่ resolve ได้
            chunk_texts: texts ของ chunks ที่ใช้ (ลำดับต้อง sorted)

        Returns:
            CacheResult(hit=True, entry=...) ถ้าพบ
            CacheResult(hit=False) ถ้าไม่พบ
        """
        key = self._build_key(question, versions, chunk_texts)
        entry = self._store.get(key)
        if entry is not None:
            return CacheResult(hit=True, entry=entry)
        return CacheResult(hit=False)

    def store(
        self,
        question: str,
        versions: frozenset[CurriculumVersion],
        chunk_texts: list[str],
        answer_text: str,
        citations_passed: int,
        generation_time_seconds: float,
        request_id: str,
    ) -> str:
        """บันทึกคำตอบลง cache.

        Args:
            question: คำถามจากผู้ใช้
            versions: ชุด version
            chunk_texts: texts ของ chunks
            answer_text: คำตอบที่ validated แล้ว
            citations_passed: จำนวน citations ที่ผ่าน
            generation_time_seconds: เวลาที่ใช้สร้างคำตอบ
            request_id: request ID ที่สร้างคำตอบนี้

        Returns:
            cache key ที่ใช้เก็บ
        """
        key = self._build_key(question, versions, chunk_texts)
        self._store[key] = CacheEntry(
            key=key,
            answer_text=answer_text,
            citations_passed=citations_passed,
            generation_time_seconds=generation_time_seconds,
            request_id=request_id,
        )
        return key

    def size(self) -> int:
        """จำนวน entries ใน cache."""
        return len(self._store)

    def clear(self) -> None:
        """ล้าง cache ทั้งหมด."""
        self._store.clear()

    def _build_key(
        self,
        question: str,
        versions: frozenset[CurriculumVersion],
        chunk_texts: list[str],
    ) -> str:
        """สร้าง cache key จาก 3 components — deterministic.

        Components:
        1. normalized question
        2. version fingerprint
        3. content hash (sha256 of sorted chunk texts)
        """
        norm_q = _normalize_question(question)
        v_fp = version_fingerprint(versions)
        content_hash = _content_hash(chunk_texts)
        return f"{norm_q}||{v_fp}||{content_hash}"

    def has_key(self, key: str) -> bool:
        """ตรวจว่า key นี้อยู่ใน cache หรือไม่."""
        return key in self._store


# ══════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════


def _normalize_question(question: str) -> str:
    """Normalize question สำหรับ cache key.

    - lowercase
    - strip leading/trailing whitespace
    - collapse multiple whitespace to single space
    - remove trailing punctuation (?,。)
    """
    q = question.strip().lower()
    q = re.sub(r"\s+", " ", q)
    q = q.rstrip("?。?.")
    return q


def _content_hash(chunk_texts: list[str]) -> str:
    """สร้าง sha256 hash จาก chunk texts (sorted for determinism).

    Sort texts ก่อน hash เพื่อให้ผลเหมือนกันไม่ว่าจะส่งมาลำดับใด.
    """
    sorted_texts = sorted(chunk_texts)
    combined = "\n".join(sorted_texts)
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()[:32]
