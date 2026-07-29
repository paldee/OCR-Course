"""Unit tests สำหรับ katrag.api.schemas — Pydantic models validation (R19.3)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from katrag.api.schemas import (
    AskRequest,
    AskResponse,
    BBoxItem,
    CitationItem,
    DocumentItem,
    DocumentsResponse,
    ErrorDetail,
    PageResponse,
    TraceResponse,
    ValidationErrorResponse,
)


# ══════════════════════════════════════════════════════════════════════
# AskRequest validation
# ══════════════════════════════════════════════════════════════════════


class TestAskRequest:
    """ทดสอบ AskRequest validation (R19.3 — 1-2000 อักขระ)."""

    def test_valid_question_minimal(self) -> None:
        """คำถาม 1 อักขระ — ขอบล่างที่ยอมรับ."""
        req = AskRequest(question="a")
        assert req.question == "a"

    def test_valid_question_max_length(self) -> None:
        """คำถาม 2000 อักขระ — ขอบบนที่ยอมรับ."""
        req = AskRequest(question="x" * 2000)
        assert len(req.question) == 2000

    def test_valid_question_thai(self) -> None:
        """คำถามภาษาไทยปกติ."""
        req = AskRequest(question="วิชา 09064100 มีกี่หน่วยกิต?")
        assert "หน่วยกิต" in req.question

    def test_empty_question_rejected(self) -> None:
        """คำถามว่าง — ปฏิเสธ (R19.3)."""
        with pytest.raises(ValidationError) as exc_info:
            AskRequest(question="")
        errors = exc_info.value.errors()
        assert len(errors) >= 1
        # ต้องมี field 'question' ในข้อผิดพลาด
        assert any("question" in str(e.get("loc", "")) for e in errors)

    def test_question_too_long_rejected(self) -> None:
        """คำถามเกิน 2000 อักขระ — ปฏิเสธ (R19.3)."""
        with pytest.raises(ValidationError) as exc_info:
            AskRequest(question="y" * 2001)
        errors = exc_info.value.errors()
        assert len(errors) >= 1

    def test_missing_question_field(self) -> None:
        """ไม่ส่ง field question — ปฏิเสธ."""
        with pytest.raises(ValidationError):
            AskRequest()  # type: ignore[call-arg]


# ══════════════════════════════════════════════════════════════════════
# AskResponse
# ══════════════════════════════════════════════════════════════════════


class TestAskResponse:
    """ทดสอบ AskResponse schema."""

    def test_minimal_response(self) -> None:
        resp = AskResponse(
            request_id="abc-123",
            answer="คำตอบทดสอบ",
        )
        assert resp.request_id == "abc-123"
        assert resp.citations == []
        assert resp.citations_removed == 0

    def test_full_response(self) -> None:
        resp = AskResponse(
            request_id="req-001",
            answer="วิชานี้มี 3 หน่วยกิต",
            citations=[
                CitationItem(
                    citation_id="cite-001",
                    document_id="doc-1",
                    page=5,
                    heading="หน่วยกิต",
                )
            ],
            versions_resolved=["IT 2565 (current)"],
            citations_removed=1,
            unsupported_claims=0,
            total_time_seconds=1.23,
        )
        assert len(resp.citations) == 1
        assert resp.citations[0].citation_id == "cite-001"
        assert resp.total_time_seconds == 1.23


# ══════════════════════════════════════════════════════════════════════
# DocumentsResponse
# ══════════════════════════════════════════════════════════════════════


class TestDocumentsResponse:
    """ทดสอบ DocumentsResponse schema."""

    def test_empty_list(self) -> None:
        resp = DocumentsResponse(documents=[], total=0)
        assert resp.documents == []
        assert resp.total == 0

    def test_with_documents(self) -> None:
        resp = DocumentsResponse(
            documents=[
                DocumentItem(
                    document_id="doc-1",
                    filename="IT2565.pdf",
                    page_count=200,
                    versions=["IT 2565 (current)"],
                )
            ],
            total=1,
        )
        assert len(resp.documents) == 1
        assert resp.documents[0].filename == "IT2565.pdf"


# ══════════════════════════════════════════════════════════════════════
# PageResponse
# ══════════════════════════════════════════════════════════════════════


class TestPageResponse:
    """ทดสอบ PageResponse schema."""

    def test_with_bbox(self) -> None:
        resp = PageResponse(
            citation_id="cite-001",
            document_id="doc-1",
            page=5,
            heading="หน่วยกิต",
            bbox=BBoxItem(x0=10.0, y0=20.0, x1=300.0, y1=50.0),
            page_width=612.0,
            page_height=792.0,
        )
        assert resp.bbox is not None
        assert resp.bbox.x0 == 10.0

    def test_without_bbox(self) -> None:
        resp = PageResponse(
            citation_id="cite-002",
            document_id="doc-2",
            page=1,
            heading="test",
        )
        assert resp.bbox is None


# ══════════════════════════════════════════════════════════════════════
# TraceResponse
# ══════════════════════════════════════════════════════════════════════


class TestTraceResponse:
    """ทดสอบ TraceResponse schema."""

    def test_valid_trace(self) -> None:
        resp = TraceResponse(
            request_id="req-001",
            question="test",
            question_level="L1",
            versions_resolved="IT:2565:current",
            evidence_nodes=5,
            citations_sent=3,
            citations_passed=3,
            citations_removed=0,
            unsupported_claims=0,
            answer_generation_time_seconds=1.5,
            ocr_invocations=0,
            preprocessor_invocations=0,
            adjudicator_invocations=0,
            total_time_seconds=2.0,
            halt_reason="no_new_evidence",
            cache_hit=False,
            created_at_ns=1700000000000000000,
        )
        assert resp.question_level == "L1"
        assert resp.ocr_invocations == 0


# ══════════════════════════════════════════════════════════════════════
# ValidationErrorResponse
# ══════════════════════════════════════════════════════════════════════


class TestValidationErrorResponse:
    """ทดสอบ ValidationErrorResponse schema (R19.3)."""

    def test_with_errors(self) -> None:
        resp = ValidationErrorResponse(
            detail=[
                ErrorDetail(
                    loc=["body", "question"],
                    msg="String should have at least 1 character",
                    type="string_too_short",
                )
            ]
        )
        assert len(resp.detail) == 1
        assert resp.detail[0].loc == ["body", "question"]

    def test_multiple_errors(self) -> None:
        """รายชื่อ field ที่ผิดทุก field (R19.3)."""
        resp = ValidationErrorResponse(
            detail=[
                ErrorDetail(loc=["body", "field1"], msg="error 1", type="type_1"),
                ErrorDetail(loc=["body", "field2"], msg="error 2", type="type_2"),
            ]
        )
        assert len(resp.detail) == 2
