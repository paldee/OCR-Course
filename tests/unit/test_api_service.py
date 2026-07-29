"""Unit tests สำหรับ katrag.api.service — endpoint tests (R19.1-R19.9)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from katrag.api.service import create_app


@pytest.fixture
def app():
    """สร้าง app instance สำหรับทดสอบ."""
    return create_app(
        host="127.0.0.1",
        port=8000,
        max_documents=500,
        request_timeout_seconds=120.0,
        max_question_chars=2000,
    )


@pytest.fixture
def client(app):
    """TestClient สำหรับทดสอบ endpoints."""
    return TestClient(app)


# ══════════════════════════════════════════════════════════════════════
# POST /ask
# ══════════════════════════════════════════════════════════════════════


class TestAskEndpoint:
    """ทดสอบ POST /ask (R19.1)."""

    def test_valid_question(self, client: TestClient) -> None:
        """คำถามปกติ — 200 พร้อม request_id."""
        resp = client.post("/ask", json={"question": "วิชา IT มีอะไรบ้าง?"})
        assert resp.status_code == 200
        data = resp.json()
        assert "request_id" in data
        assert "answer" in data
        assert isinstance(data["citations"], list)
        assert data["total_time_seconds"] >= 0

    def test_minimal_question(self, client: TestClient) -> None:
        """คำถาม 1 อักขระ — ขอบล่าง."""
        resp = client.post("/ask", json={"question": "a"})
        assert resp.status_code == 200

    def test_max_length_question(self, client: TestClient) -> None:
        """คำถาม 2000 อักขระ — ขอบบน."""
        resp = client.post("/ask", json={"question": "x" * 2000})
        assert resp.status_code == 200

    def test_empty_question_returns_422(self, client: TestClient) -> None:
        """คำถามว่าง — 422 (R19.3)."""
        resp = client.post("/ask", json={"question": ""})
        assert resp.status_code == 422
        data = resp.json()
        assert "detail" in data
        assert len(data["detail"]) >= 1

    def test_too_long_question_returns_422(self, client: TestClient) -> None:
        """คำถามเกิน 2000 อักขระ — 422 (R19.3)."""
        resp = client.post("/ask", json={"question": "z" * 2001})
        assert resp.status_code == 422
        data = resp.json()
        assert "detail" in data

    def test_missing_question_field_returns_422(self, client: TestClient) -> None:
        """ไม่ส่ง field question — 422 (R19.3)."""
        resp = client.post("/ask", json={})
        assert resp.status_code == 422
        data = resp.json()
        assert "detail" in data

    def test_invalid_json_returns_422(self, client: TestClient) -> None:
        """ส่ง body ที่ไม่ใช่ JSON — 422."""
        resp = client.post(
            "/ask",
            content="not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 422

    def test_question_whitespace_only_returns_422(self, client: TestClient) -> None:
        """คำถามที่มีแต่ช่องว่าง — ผ่าน schema (min_length=1) แต่ strip แล้วว่าง → 422."""
        resp = client.post("/ask", json={"question": "   "})
        # After strip, length < 1 → custom 422
        assert resp.status_code == 422

    def test_response_has_versions_resolved(self, client: TestClient) -> None:
        """Response ต้องมี versions_resolved field."""
        resp = client.post("/ask", json={"question": "test question"})
        data = resp.json()
        assert "versions_resolved" in data
        assert isinstance(data["versions_resolved"], list)

    def test_response_records_trace(self, app, client: TestClient) -> None:
        """POST /ask ต้องบันทึก trace ที่ดึงได้ภายหลัง."""
        resp = client.post("/ask", json={"question": "trace test"})
        request_id = resp.json()["request_id"]

        trace_resp = client.get(f"/traces/{request_id}")
        assert trace_resp.status_code == 200
        assert trace_resp.json()["request_id"] == request_id


# ══════════════════════════════════════════════════════════════════════
# GET /documents
# ══════════════════════════════════════════════════════════════════════


class TestDocumentsEndpoint:
    """ทดสอบ GET /documents (R19.1)."""

    def test_empty_store(self, client: TestClient) -> None:
        """ยังไม่มีเอกสาร — คืน list ว่าง."""
        resp = client.get("/documents")
        assert resp.status_code == 200
        data = resp.json()
        assert data["documents"] == []
        assert data["total"] == 0

    def test_with_documents(self, app, client: TestClient) -> None:
        """มีเอกสาร — คืนรายการ."""
        app.state.documents_store = [
            {
                "document_id": "doc-1",
                "filename": "IT2565.pdf",
                "page_count": 200,
                "versions": ["IT 2565 (current)"],
            },
            {
                "document_id": "doc-2",
                "filename": "BIT2563.pdf",
                "page_count": 150,
                "versions": ["BIT 2563 (old)"],
            },
        ]
        resp = client.get("/documents")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["documents"]) == 2
        assert data["total"] == 2

    def test_max_documents_limit(self, app, client: TestClient) -> None:
        """จำกัด ≤500 รายการต่อ response."""
        app.state.documents_store = [
            {"document_id": f"doc-{i}", "filename": f"f{i}.pdf", "page_count": 10, "versions": []}
            for i in range(600)
        ]
        resp = client.get("/documents")
        data = resp.json()
        assert len(data["documents"]) <= 500
        assert data["total"] == 600


# ══════════════════════════════════════════════════════════════════════
# GET /pages/{citation_id}
# ══════════════════════════════════════════════════════════════════════


class TestPagesEndpoint:
    """ทดสอบ GET /pages/{citation_id} (R19.1, R19.8)."""

    def test_not_found_returns_404(self, client: TestClient) -> None:
        """citation_id ที่ไม่มีอยู่ — 404 (R19.8)."""
        resp = client.get("/pages/cite-999")
        assert resp.status_code == 404

    def test_existing_citation(self, app, client: TestClient) -> None:
        """citation_id ที่มีอยู่ — 200 พร้อม bbox."""
        app.state.citations_store["cite-001"] = {
            "document_id": "doc-1",
            "page": 5,
            "heading": "หน่วยกิต",
            "bbox": {"x0": 10.0, "y0": 20.0, "x1": 300.0, "y1": 50.0},
            "page_width": 612.0,
            "page_height": 792.0,
        }
        resp = client.get("/pages/cite-001")
        assert resp.status_code == 200
        data = resp.json()
        assert data["citation_id"] == "cite-001"
        assert data["document_id"] == "doc-1"
        assert data["page"] == 5
        assert data["bbox"]["x0"] == 10.0
        assert data["bbox"]["x1"] == 300.0

    def test_citation_without_bbox(self, app, client: TestClient) -> None:
        """citation ที่ไม่มี bbox — bbox = null."""
        app.state.citations_store["cite-002"] = {
            "document_id": "doc-2",
            "page": 1,
            "heading": "test",
            "bbox": None,
            "page_width": 612.0,
            "page_height": 792.0,
        }
        resp = client.get("/pages/cite-002")
        assert resp.status_code == 200
        assert resp.json()["bbox"] is None


# ══════════════════════════════════════════════════════════════════════
# GET /traces/{request_id}
# ══════════════════════════════════════════════════════════════════════


class TestTracesEndpoint:
    """ทดสอบ GET /traces/{request_id} (R19.1, R19.8)."""

    def test_not_found_returns_404(self, client: TestClient) -> None:
        """request_id ที่ไม่มีอยู่ — 404 (R19.8)."""
        resp = client.get("/traces/nonexistent-id")
        assert resp.status_code == 404

    def test_existing_trace(self, app, client: TestClient) -> None:
        """request_id ที่มีอยู่ — 200."""
        import time

        app.state.trace_store["req-123"] = {
            "request_id": "req-123",
            "question": "test question",
            "question_level": "L1",
            "versions_resolved": "IT:2565:current",
            "evidence_nodes": 5,
            "citations_sent": 3,
            "citations_passed": 3,
            "citations_removed": 0,
            "unsupported_claims": 0,
            "answer_generation_time_seconds": 1.5,
            "ocr_invocations": 0,
            "preprocessor_invocations": 0,
            "adjudicator_invocations": 0,
            "total_time_seconds": 2.0,
            "halt_reason": "no_new_evidence",
            "cache_hit": False,
            "created_at_ns": time.time_ns(),
        }
        resp = client.get("/traces/req-123")
        assert resp.status_code == 200
        data = resp.json()
        assert data["request_id"] == "req-123"
        assert data["question"] == "test question"
        assert data["ocr_invocations"] == 0


# ══════════════════════════════════════════════════════════════════════
# Validation error format (R19.3)
# ══════════════════════════════════════════════════════════════════════


class TestValidationErrorFormat:
    """ทดสอบว่า 422 response มีรายชื่อ field ที่ผิดครบ (R19.3)."""

    def test_422_contains_all_error_fields(self, client: TestClient) -> None:
        """ต้องมี loc, msg, type สำหรับทุก field ที่ผิด."""
        resp = client.post("/ask", json={"question": ""})
        assert resp.status_code == 422
        data = resp.json()
        for error in data["detail"]:
            assert "loc" in error
            assert "msg" in error
            assert "type" in error

    def test_422_does_not_call_pipeline(self, app, client: TestClient) -> None:
        """เมื่อ validation ไม่ผ่าน ต้องไม่เรียก router/generator (R19.3)."""
        initial_trace_count = len(app.state.trace_store)
        client.post("/ask", json={"question": ""})
        # Trace store ต้องไม่เพิ่ม (ไม่ได้เข้า pipeline)
        assert len(app.state.trace_store) == initial_trace_count


# ══════════════════════════════════════════════════════════════════════
# Host binding (R19.2)
# ══════════════════════════════════════════════════════════════════════


class TestHostBinding:
    """ทดสอบว่า app ตั้งค่า host เป็น 127.0.0.1 (R19.2)."""

    def test_default_host_is_loopback(self, app) -> None:
        assert app.state.host == "127.0.0.1"

    def test_default_port(self, app) -> None:
        assert app.state.port == 8000
