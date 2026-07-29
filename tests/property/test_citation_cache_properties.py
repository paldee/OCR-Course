"""Property-based tests for citation validation and answer cache (R17.3, R17.4, R10.10, R4.10).

Properties:
1. Every citation ID in the validated answer matches the issued set (R17.3)
2. Cache hit only when key matches exactly — no threshold path (R10.10)
3. OCR invocation count on query path equals zero (R4.10)
"""

from __future__ import annotations

import re
import time

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from katrag.common.types import CitationId, CurriculumVersion
from katrag.query.answer_cache import AnswerCache, _normalize_question, _content_hash
from katrag.query.citation import CitationRegistry, EvidenceUnit
from katrag.query.citation_validator import CitationValidator
from katrag.query.trace import QueryTrace, TraceStore, create_query_trace


# ── Strategies ────────────────────────────────────────────────────────

# Citation ID pattern
_CITATION_ID_RE = re.compile(r"\[(cite-\d{3,})\]")

# Valid programs for versions
_PROGRAMS = st.sampled_from(["IT", "BIT", "DSBA", "AIT"])
_YEARS = st.sampled_from([2560, 2563, 2565, 2566, 2568])
_EDITIONS = st.sampled_from(["old", "current"])

_curriculum_version = st.builds(
    CurriculumVersion,
    program=_PROGRAMS,
    curriculum_year=_YEARS,
    edition_status=_EDITIONS,
)

# Non-empty text for chunks
_chunk_text = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    min_size=1,
    max_size=200,
)

# Questions
_question_text = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    min_size=1,
    max_size=500,
)


# ══════════════════════════════════════════════════════════════════════
# Property 1: Every citation ID in validated answer ∈ issued set (R17.3)
# ══════════════════════════════════════════════════════════════════════


@settings(max_examples=100)
@given(
    num_citations=st.integers(min_value=1, max_value=20),
    extra_fake_ids=st.lists(
        st.integers(min_value=100, max_value=999),
        min_size=0,
        max_size=5,
    ),
)
def test_validated_answer_citations_subset_of_issued(
    num_citations: int,
    extra_fake_ids: list[int],
) -> None:
    """Property: after validation, every citation ID in the answer belongs to the issued set.

    Strategy:
    - Create a registry with N citations
    - Build an answer mixing valid and fake citation IDs
    - Validate the answer
    - Assert: all citation IDs in validated output ∈ issued set
    """
    # Setup registry
    registry = CitationRegistry()
    for i in range(1, num_citations + 1):
        registry.issue(EvidenceUnit(
            chunk_id=f"chunk_{i:03d}",
            document_id=f"doc_{i:03d}",
            page=i,
            heading=f"heading_{i}",
            text=f"evidence text {i}",
        ))

    issued_set = {str(cid) for cid in registry.all_ids()}

    # Build answer with valid + fake citations
    parts: list[str] = []
    for i in range(1, num_citations + 1):
        parts.append(f"วิชา {i} มีหน่วยกิตที่ต้องเรียน [cite-{i:03d}]")
    for fake_id in extra_fake_ids:
        parts.append(f"ข้อมูลปลอม [cite-{fake_id:03d}]")

    answer = "\n".join(parts)

    # Validate
    validator = CitationValidator(registry)
    result = validator.validate(answer)

    # Property: every citation in validated answer ∈ issued set
    found_ids = _CITATION_ID_RE.findall(result.validated_answer)
    for cid in found_ids:
        assert cid in issued_set, (
            f"Citation '{cid}' in validated answer but not in issued set {issued_set}"
        )


# ══════════════════════════════════════════════════════════════════════
# Property 2: Cache hit only on exact key match (R10.10)
# ══════════════════════════════════════════════════════════════════════


@settings(max_examples=100)
@given(
    question=_question_text,
    version=_curriculum_version,
    chunks=st.lists(_chunk_text, min_size=1, max_size=10),
    alt_question=_question_text,
    alt_chunks=st.lists(_chunk_text, min_size=1, max_size=10),
)
def test_cache_hit_only_on_exact_match(
    question: str,
    version: CurriculumVersion,
    chunks: list[str],
    alt_question: str,
    alt_chunks: list[str],
) -> None:
    """Property: cache returns hit ONLY when normalized key matches exactly.

    If the normalized question, version fingerprint, or content hash differs,
    the cache must miss. No approximate threshold path exists.
    """
    assume(question.strip())  # non-empty after strip
    assume(alt_question.strip())

    versions = frozenset({version})
    cache = AnswerCache()

    # Store original
    cache.store(
        question=question,
        versions=versions,
        chunk_texts=chunks,
        answer_text="original answer",
        citations_passed=1,
        generation_time_seconds=1.0,
        request_id="req-1",
    )

    # Same key → must hit
    result_same = cache.lookup(question=question, versions=versions, chunk_texts=chunks)
    assert result_same.hit is True, "Exact same inputs must produce cache hit"

    # Different question (after normalization) → must miss
    norm_orig = _normalize_question(question)
    norm_alt = _normalize_question(alt_question)
    if norm_orig != norm_alt:
        result_diff_q = cache.lookup(
            question=alt_question, versions=versions, chunk_texts=chunks,
        )
        assert result_diff_q.hit is False, (
            "Different normalized question must produce cache miss"
        )

    # Different chunks → must miss (if hash differs)
    hash_orig = _content_hash(chunks)
    hash_alt = _content_hash(alt_chunks)
    if hash_orig != hash_alt:
        result_diff_c = cache.lookup(
            question=question, versions=versions, chunk_texts=alt_chunks,
        )
        assert result_diff_c.hit is False, (
            "Different chunk content hash must produce cache miss"
        )


# ══════════════════════════════════════════════════════════════════════
# Property 3: OCR invocation count on query path equals zero (R4.10)
# ══════════════════════════════════════════════════════════════════════


@settings(max_examples=100)
@given(
    request_id=st.text(
        alphabet=st.characters(whitelist_categories=("L", "N")),
        min_size=1,
        max_size=30,
    ),
    question=_question_text,
    evidence_nodes=st.integers(min_value=0, max_value=60),
    citations_sent=st.integers(min_value=0, max_value=60),
    citations_passed=st.integers(min_value=0, max_value=60),
    citations_removed=st.integers(min_value=0, max_value=60),
    unsupported=st.integers(min_value=0, max_value=60),
    gen_time=st.floats(min_value=0.0, max_value=100.0),
    total_time=st.floats(min_value=0.0, max_value=200.0),
)
def test_query_path_ocr_invocations_zero(
    request_id: str,
    question: str,
    evidence_nodes: int,
    citations_sent: int,
    citations_passed: int,
    citations_removed: int,
    unsupported: int,
    gen_time: float,
    total_time: float,
) -> None:
    """Property: create_query_trace always produces OCR/preprocessor/adjudicator = 0.

    No matter what other values are provided, the query path must never
    invoke OCR, preprocessor, or adjudicator (R4.10).
    """
    assume(request_id.strip())
    assume(question.strip())

    trace = create_query_trace(
        request_id=request_id,
        question=question,
        question_level="L1",
        versions_resolved="IT:2566:current",
        evidence_nodes=evidence_nodes,
        citations_sent=citations_sent,
        citations_passed=citations_passed,
        citations_removed=citations_removed,
        unsupported_claims=unsupported,
        answer_generation_time_seconds=gen_time,
        total_time_seconds=total_time,
        halt_reason="completed",
    )

    assert trace.ocr_invocations == 0, (
        f"OCR invocations must be 0 on query path, got {trace.ocr_invocations}"
    )
    assert trace.preprocessor_invocations == 0, (
        f"Preprocessor invocations must be 0 on query path, got {trace.preprocessor_invocations}"
    )
    assert trace.adjudicator_invocations == 0, (
        f"Adjudicator invocations must be 0 on query path, got {trace.adjudicator_invocations}"
    )
