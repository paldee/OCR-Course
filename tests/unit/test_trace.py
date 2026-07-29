"""Unit tests for katrag.query.trace.

ทดสอบ R19.6, R19.7, R4.10, R17.10, R10.10:
- QueryTrace: all fields NOT NULL
- TraceStore: record + replay (same trace on same request_id)
- OCR/preprocessor/adjudicator invocations = 0 on query path
- Citation counts recorded
- Duplicate request_id rejected
"""

from __future__ import annotations

import time

import pytest

from katrag.query.trace import QueryTrace, TraceStore, create_query_trace


# ── Fixtures / helpers ────────────────────────────────────────────────


def _valid_trace(request_id: str = "req-001") -> QueryTrace:
    return QueryTrace(
        request_id=request_id,
        question="วิชาบังคับมีอะไรบ้าง?",
        question_level="L1",
        versions_resolved="IT:2566:current",
        evidence_nodes=10,
        citations_sent=8,
        citations_passed=7,
        citations_removed=1,
        unsupported_claims=2,
        answer_generation_time_seconds=1.5,
        ocr_invocations=0,
        preprocessor_invocations=0,
        adjudicator_invocations=0,
        total_time_seconds=3.0,
        halt_reason="no_new_evidence",
        cache_hit=False,
        created_at_ns=time.time_ns(),
    )


# ══════════════════════════════════════════════════════════════════════
# Tests: QueryTrace — NOT NULL enforcement
# ══════════════════════════════════════════════════════════════════════


class TestQueryTraceValidation:
    def test_valid_trace_creates_successfully(self) -> None:
        trace = _valid_trace()
        assert trace.request_id == "req-001"
        assert trace.ocr_invocations == 0

    def test_rejects_empty_request_id(self) -> None:
        with pytest.raises(ValueError, match="request_id"):
            QueryTrace(
                request_id="",
                question="q",
                question_level="L1",
                versions_resolved="v",
                evidence_nodes=0,
                citations_sent=0,
                citations_passed=0,
                citations_removed=0,
                unsupported_claims=0,
                answer_generation_time_seconds=0.0,
                ocr_invocations=0,
                preprocessor_invocations=0,
                adjudicator_invocations=0,
                total_time_seconds=0.0,
                halt_reason="done",
                cache_hit=False,
                created_at_ns=time.time_ns(),
            )

    def test_rejects_empty_question(self) -> None:
        with pytest.raises(ValueError, match="question"):
            QueryTrace(
                request_id="r1",
                question="",
                question_level="L1",
                versions_resolved="v",
                evidence_nodes=0,
                citations_sent=0,
                citations_passed=0,
                citations_removed=0,
                unsupported_claims=0,
                answer_generation_time_seconds=0.0,
                ocr_invocations=0,
                preprocessor_invocations=0,
                adjudicator_invocations=0,
                total_time_seconds=0.0,
                halt_reason="done",
                cache_hit=False,
                created_at_ns=time.time_ns(),
            )

    def test_rejects_empty_halt_reason(self) -> None:
        with pytest.raises(ValueError, match="halt_reason"):
            QueryTrace(
                request_id="r1",
                question="q",
                question_level="L1",
                versions_resolved="v",
                evidence_nodes=0,
                citations_sent=0,
                citations_passed=0,
                citations_removed=0,
                unsupported_claims=0,
                answer_generation_time_seconds=0.0,
                ocr_invocations=0,
                preprocessor_invocations=0,
                adjudicator_invocations=0,
                total_time_seconds=0.0,
                halt_reason="",
                cache_hit=False,
                created_at_ns=time.time_ns(),
            )

    def test_rejects_negative_evidence_nodes(self) -> None:
        with pytest.raises(ValueError, match="evidence_nodes"):
            QueryTrace(
                request_id="r1",
                question="q",
                question_level="L1",
                versions_resolved="v",
                evidence_nodes=-1,
                citations_sent=0,
                citations_passed=0,
                citations_removed=0,
                unsupported_claims=0,
                answer_generation_time_seconds=0.0,
                ocr_invocations=0,
                preprocessor_invocations=0,
                adjudicator_invocations=0,
                total_time_seconds=0.0,
                halt_reason="done",
                cache_hit=False,
                created_at_ns=time.time_ns(),
            )

    def test_rejects_zero_created_at_ns(self) -> None:
        with pytest.raises(ValueError, match="created_at_ns"):
            QueryTrace(
                request_id="r1",
                question="q",
                question_level="L1",
                versions_resolved="v",
                evidence_nodes=0,
                citations_sent=0,
                citations_passed=0,
                citations_removed=0,
                unsupported_claims=0,
                answer_generation_time_seconds=0.0,
                ocr_invocations=0,
                preprocessor_invocations=0,
                adjudicator_invocations=0,
                total_time_seconds=0.0,
                halt_reason="done",
                cache_hit=False,
                created_at_ns=0,
            )

    def test_to_dict_contains_all_fields(self) -> None:
        trace = _valid_trace()
        d = trace.to_dict()
        assert d["request_id"] == "req-001"
        assert d["ocr_invocations"] == 0
        assert d["preprocessor_invocations"] == 0
        assert d["adjudicator_invocations"] == 0
        assert "citations_sent" in d
        assert "citations_passed" in d


# ══════════════════════════════════════════════════════════════════════
# Tests: TraceStore — record and replay (R19.7)
# ══════════════════════════════════════════════════════════════════════


class TestTraceStore:
    def test_record_and_get(self) -> None:
        store = TraceStore()
        trace = _valid_trace("req-001")
        store.record(trace)
        retrieved = store.get("req-001")
        assert retrieved is trace

    def test_replay_returns_same_trace(self) -> None:
        """R19.7: replay returns the same trace on same request_id."""
        store = TraceStore()
        trace = _valid_trace("req-123")
        store.record(trace)
        first = store.get("req-123")
        second = store.get("req-123")
        assert first is second
        assert first is trace

    def test_get_unknown_returns_none(self) -> None:
        store = TraceStore()
        assert store.get("nonexistent") is None

    def test_duplicate_request_id_raises(self) -> None:
        store = TraceStore()
        store.record(_valid_trace("req-001"))
        with pytest.raises(ValueError, match="duplicate"):
            store.record(_valid_trace("req-001"))

    def test_count(self) -> None:
        store = TraceStore()
        assert store.count() == 0
        store.record(_valid_trace("r1"))
        assert store.count() == 1
        store.record(_valid_trace("r2"))
        assert store.count() == 2

    def test_all_traces_sorted_by_time(self) -> None:
        store = TraceStore()
        t1 = QueryTrace(
            request_id="r1", question="q1", question_level="L1",
            versions_resolved="v", evidence_nodes=0, citations_sent=0,
            citations_passed=0, citations_removed=0, unsupported_claims=0,
            answer_generation_time_seconds=0.0, ocr_invocations=0,
            preprocessor_invocations=0, adjudicator_invocations=0,
            total_time_seconds=0.0, halt_reason="done", cache_hit=False,
            created_at_ns=1000,
        )
        t2 = QueryTrace(
            request_id="r2", question="q2", question_level="L2",
            versions_resolved="v", evidence_nodes=0, citations_sent=0,
            citations_passed=0, citations_removed=0, unsupported_claims=0,
            answer_generation_time_seconds=0.0, ocr_invocations=0,
            preprocessor_invocations=0, adjudicator_invocations=0,
            total_time_seconds=0.0, halt_reason="done", cache_hit=False,
            created_at_ns=2000,
        )
        store.record(t2)
        store.record(t1)
        all_t = store.all_traces()
        assert all_t[0].request_id == "r1"
        assert all_t[1].request_id == "r2"


# ══════════════════════════════════════════════════════════════════════
# Tests: create_query_trace helper — enforces OCR = 0 (R4.10)
# ══════════════════════════════════════════════════════════════════════


class TestCreateQueryTrace:
    def test_ocr_invocations_always_zero(self) -> None:
        """R4.10: OCR invocation count on query path must equal zero."""
        trace = create_query_trace(
            request_id="req-abc",
            question="q?",
            question_level="L1",
            versions_resolved="IT:2566:current",
            evidence_nodes=5,
            citations_sent=4,
            citations_passed=3,
            citations_removed=1,
            unsupported_claims=0,
            answer_generation_time_seconds=2.0,
            total_time_seconds=5.0,
            halt_reason="no_new_evidence",
        )
        assert trace.ocr_invocations == 0
        assert trace.preprocessor_invocations == 0
        assert trace.adjudicator_invocations == 0

    def test_records_citation_counts(self) -> None:
        trace = create_query_trace(
            request_id="req-xyz",
            question="q?",
            question_level="L2",
            versions_resolved="DSBA:2565:current",
            evidence_nodes=10,
            citations_sent=8,
            citations_passed=6,
            citations_removed=2,
            unsupported_claims=1,
            answer_generation_time_seconds=1.5,
            total_time_seconds=4.0,
            halt_reason="max_hops_reached",
        )
        assert trace.citations_sent == 8
        assert trace.citations_passed == 6
        assert trace.citations_removed == 2
        assert trace.unsupported_claims == 1

    def test_records_answer_generation_time(self) -> None:
        trace = create_query_trace(
            request_id="req-t",
            question="q?",
            question_level="L1",
            versions_resolved="v",
            evidence_nodes=0,
            citations_sent=0,
            citations_passed=0,
            citations_removed=0,
            unsupported_claims=0,
            answer_generation_time_seconds=3.14,
            total_time_seconds=5.0,
            halt_reason="done",
        )
        assert trace.answer_generation_time_seconds == 3.14
