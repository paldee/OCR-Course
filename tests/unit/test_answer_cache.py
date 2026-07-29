"""Unit tests for katrag.query.answer_cache.

ทดสอบ R10.10, R19.7:
- Exact key match only (no approximate threshold)
- Cache hit when question + versions + chunks match exactly
- Cache miss on any difference
- Normalized question (lowercase, stripped, collapsed whitespace)
- Content hash from sorted chunk texts
"""

from __future__ import annotations

import pytest

from katrag.common.types import CurriculumVersion
from katrag.query.answer_cache import (
    AnswerCache,
    CacheEntry,
    CacheResult,
    _content_hash,
    _normalize_question,
)


# ── Fixtures / helpers ────────────────────────────────────────────────


def _version(program: str = "IT", year: int = 2566) -> CurriculumVersion:
    return CurriculumVersion(program=program, curriculum_year=year, edition_status="current")


def _versions(*args: tuple[str, int]) -> frozenset[CurriculumVersion]:
    return frozenset(
        CurriculumVersion(program=p, curriculum_year=y, edition_status="current")
        for p, y in args
    )


# ══════════════════════════════════════════════════════════════════════
# Tests: Normalize question
# ══════════════════════════════════════════════════════════════════════


class TestNormalizeQuestion:
    def test_lowercase(self) -> None:
        assert _normalize_question("What IS IT?") == "what is it"

    def test_strip_whitespace(self) -> None:
        assert _normalize_question("  hello  ") == "hello"

    def test_collapse_whitespace(self) -> None:
        assert _normalize_question("a   b   c") == "a b c"

    def test_strip_trailing_punctuation(self) -> None:
        assert _normalize_question("question?") == "question"
        assert _normalize_question("question。") == "question"

    def test_combined_normalization(self) -> None:
        assert _normalize_question("  What  IS  This? ") == "what is this"


# ══════════════════════════════════════════════════════════════════════
# Tests: Content hash
# ══════════════════════════════════════════════════════════════════════


class TestContentHash:
    def test_same_texts_same_hash(self) -> None:
        h1 = _content_hash(["a", "b", "c"])
        h2 = _content_hash(["a", "b", "c"])
        assert h1 == h2

    def test_order_independent(self) -> None:
        """Sorted before hashing → order doesn't matter."""
        h1 = _content_hash(["a", "b", "c"])
        h2 = _content_hash(["c", "a", "b"])
        assert h1 == h2

    def test_different_texts_different_hash(self) -> None:
        h1 = _content_hash(["a", "b"])
        h2 = _content_hash(["a", "c"])
        assert h1 != h2

    def test_returns_32_char_hex(self) -> None:
        h = _content_hash(["test"])
        assert len(h) == 32
        assert all(c in "0123456789abcdef" for c in h)


# ══════════════════════════════════════════════════════════════════════
# Tests: AnswerCache — exact match (R10.10)
# ══════════════════════════════════════════════════════════════════════


class TestAnswerCacheExactMatch:
    def test_store_and_lookup_hit(self) -> None:
        cache = AnswerCache()
        versions = _versions(("IT", 2566))
        chunks = ["chunk1 text", "chunk2 text"]

        cache.store(
            question="วิชาบังคับมีอะไร?",
            versions=versions,
            chunk_texts=chunks,
            answer_text="คำตอบ",
            citations_passed=3,
            generation_time_seconds=1.5,
            request_id="req-1",
        )

        result = cache.lookup(
            question="วิชาบังคับมีอะไร?",
            versions=versions,
            chunk_texts=chunks,
        )
        assert result.hit is True
        assert result.entry is not None
        assert result.entry.answer_text == "คำตอบ"

    def test_miss_on_different_question(self) -> None:
        cache = AnswerCache()
        versions = _versions(("IT", 2566))
        chunks = ["text"]

        cache.store(
            question="q1?",
            versions=versions,
            chunk_texts=chunks,
            answer_text="a1",
            citations_passed=1,
            generation_time_seconds=1.0,
            request_id="r1",
        )

        result = cache.lookup(question="q2?", versions=versions, chunk_texts=chunks)
        assert result.hit is False

    def test_miss_on_different_versions(self) -> None:
        cache = AnswerCache()
        chunks = ["text"]

        cache.store(
            question="q?",
            versions=_versions(("IT", 2566)),
            chunk_texts=chunks,
            answer_text="a",
            citations_passed=1,
            generation_time_seconds=1.0,
            request_id="r1",
        )

        result = cache.lookup(
            question="q?",
            versions=_versions(("DSBA", 2565)),
            chunk_texts=chunks,
        )
        assert result.hit is False

    def test_miss_on_different_chunks(self) -> None:
        cache = AnswerCache()
        versions = _versions(("IT", 2566))

        cache.store(
            question="q?",
            versions=versions,
            chunk_texts=["chunk_a"],
            answer_text="a",
            citations_passed=1,
            generation_time_seconds=1.0,
            request_id="r1",
        )

        result = cache.lookup(
            question="q?",
            versions=versions,
            chunk_texts=["chunk_b"],
        )
        assert result.hit is False

    def test_hit_with_normalized_question(self) -> None:
        """Same question after normalization → cache hit."""
        cache = AnswerCache()
        versions = _versions(("IT", 2566))
        chunks = ["text"]

        cache.store(
            question="  What IS This? ",
            versions=versions,
            chunk_texts=chunks,
            answer_text="ans",
            citations_passed=1,
            generation_time_seconds=1.0,
            request_id="r1",
        )

        # Same after normalization
        result = cache.lookup(
            question="what is this",
            versions=versions,
            chunk_texts=chunks,
        )
        assert result.hit is True

    def test_hit_with_reordered_chunks(self) -> None:
        """Chunks in different order → same hash → cache hit."""
        cache = AnswerCache()
        versions = _versions(("IT", 2566))

        cache.store(
            question="q?",
            versions=versions,
            chunk_texts=["b", "a", "c"],
            answer_text="ans",
            citations_passed=1,
            generation_time_seconds=1.0,
            request_id="r1",
        )

        result = cache.lookup(
            question="q?",
            versions=versions,
            chunk_texts=["c", "b", "a"],
        )
        assert result.hit is True


# ══════════════════════════════════════════════════════════════════════
# Tests: No approximate threshold path
# ══════════════════════════════════════════════════════════════════════


class TestNoApproximateThreshold:
    def test_near_miss_question_no_hit(self) -> None:
        """Similar but not identical question → miss (no fuzzy matching)."""
        cache = AnswerCache()
        versions = _versions(("IT", 2566))
        chunks = ["text"]

        cache.store(
            question="วิชาบังคับมีอะไรบ้าง",
            versions=versions,
            chunk_texts=chunks,
            answer_text="ans",
            citations_passed=1,
            generation_time_seconds=1.0,
            request_id="r1",
        )

        # Similar but different
        result = cache.lookup(
            question="วิชาบังคับมีอะไร",
            versions=versions,
            chunk_texts=chunks,
        )
        assert result.hit is False

    def test_one_extra_chunk_causes_miss(self) -> None:
        """Adding one extra chunk → different hash → miss."""
        cache = AnswerCache()
        versions = _versions(("IT", 2566))

        cache.store(
            question="q?",
            versions=versions,
            chunk_texts=["a", "b"],
            answer_text="ans",
            citations_passed=1,
            generation_time_seconds=1.0,
            request_id="r1",
        )

        result = cache.lookup(
            question="q?",
            versions=versions,
            chunk_texts=["a", "b", "c"],
        )
        assert result.hit is False


# ══════════════════════════════════════════════════════════════════════
# Tests: Cache operations
# ══════════════════════════════════════════════════════════════════════


class TestCacheOperations:
    def test_size(self) -> None:
        cache = AnswerCache()
        assert cache.size() == 0
        cache.store("q", _versions(("IT", 2566)), ["t"], "a", 1, 1.0, "r1")
        assert cache.size() == 1

    def test_clear(self) -> None:
        cache = AnswerCache()
        cache.store("q", _versions(("IT", 2566)), ["t"], "a", 1, 1.0, "r1")
        cache.clear()
        assert cache.size() == 0

    def test_cache_entry_fields(self) -> None:
        cache = AnswerCache()
        versions = _versions(("IT", 2566))
        cache.store("q?", versions, ["t"], "answer", 5, 2.5, "req-42")
        result = cache.lookup("q?", versions, ["t"])
        assert result.entry is not None
        assert result.entry.answer_text == "answer"
        assert result.entry.citations_passed == 5
        assert result.entry.generation_time_seconds == 2.5
        assert result.entry.request_id == "req-42"
