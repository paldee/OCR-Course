"""Pydantic schemas สำหรับ KatRAG API (R19.1, R19.3).

ทุก request/response ต้องผ่าน schema validation ก่อนถึง router/generator.
ข้อกำหนด:
- คำถาม 1-2000 อักขระ (api_max_question_chars จาก config)
- คืน 422 พร้อมรายชื่อ field ที่ผิดทุก field (R19.3)
- documents ≤500 รายการต่อ response
"""

from __future__ import annotations

from pydantic import BaseModel, Field


# ══════════════════════════════════════════════════════════════════════
# Request schemas
# ══════════════════════════════════════════════════════════════════════


class AskRequest(BaseModel):
    """POST /ask — คำถามจากผู้ใช้ (R19.1, R19.3)."""

    question: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="คำถามภาษาไทย/อังกฤษ ความยาว 1-2000 อักขระ",
    )


# ══════════════════════════════════════════════════════════════════════
# Response schemas
# ══════════════════════════════════════════════════════════════════════


class CitationItem(BaseModel):
    """รายการ citation หนึ่งรายการในคำตอบ."""

    citation_id: str = Field(..., description="citation ID เช่น cite-001")
    document_id: str = Field(..., description="ID เอกสารต้นทาง")
    page: int = Field(..., ge=1, description="หมายเลขหน้า (1-based)")
    heading: str = Field(..., description="หัวข้อของ chunk")


class AskResponse(BaseModel):
    """Response ของ POST /ask — คำตอบพร้อม citations (R19.1)."""

    request_id: str = Field(..., description="unique ID ของ request นี้")
    answer: str = Field(..., description="คำตอบที่สร้างจาก evidence")
    citations: list[CitationItem] = Field(
        default_factory=list, description="รายการ citations ที่ใช้อ้างอิง"
    )
    versions_resolved: list[str] = Field(
        default_factory=list, description="ชุด curriculum version ที่ resolve ได้"
    )
    citations_removed: int = Field(
        default=0, description="จำนวน claim units ที่ถูกลบ (unsupported)"
    )
    unsupported_claims: int = Field(
        default=0, description="จำนวน unsupported claims"
    )
    total_time_seconds: float = Field(
        default=0.0, ge=0, description="เวลารวมทั้ง request (วินาที)"
    )


class DocumentItem(BaseModel):
    """เอกสารหนึ่งรายการสำหรับ GET /documents."""

    document_id: str = Field(..., description="ID เอกสาร")
    filename: str = Field(..., description="ชื่อไฟล์")
    page_count: int = Field(..., ge=0, description="จำนวนหน้า")
    versions: list[str] = Field(
        default_factory=list, description="ชุด curriculum versions ที่เกี่ยวข้อง"
    )


class DocumentsResponse(BaseModel):
    """Response ของ GET /documents (R19.1) — ≤500 รายการ."""

    documents: list[DocumentItem] = Field(
        default_factory=list, description="รายการเอกสาร"
    )
    total: int = Field(default=0, ge=0, description="จำนวนเอกสารทั้งหมด")


class BBoxItem(BaseModel):
    """Bounding box หนึ่งรายการบนหน้าเอกสาร."""

    x0: float
    y0: float
    x1: float
    y1: float


class PageResponse(BaseModel):
    """Response ของ GET /pages/{citation_id} — หน้าเอกสารพร้อม bbox."""

    citation_id: str = Field(..., description="citation ID ที่ขอ")
    document_id: str = Field(..., description="ID เอกสาร")
    page: int = Field(..., ge=1, description="หมายเลขหน้า")
    heading: str = Field(..., description="หัวข้อ")
    bbox: BBoxItem | None = Field(
        default=None, description="bounding box ของ chunk บนหน้า"
    )
    page_width: float = Field(default=0.0, description="ความกว้างหน้า (pt)")
    page_height: float = Field(default=0.0, description="ความสูงหน้า (pt)")
    chunk_text: str = Field(default="", description="เนื้อหาข้อความของ chunk ที่อ้างอิง")


class TraceResponse(BaseModel):
    """Response ของ GET /traces/{request_id} — query trace."""

    request_id: str
    question: str
    question_level: str
    versions_resolved: str
    evidence_nodes: int
    citations_sent: int
    citations_passed: int
    citations_removed: int
    unsupported_claims: int
    answer_generation_time_seconds: float
    ocr_invocations: int
    preprocessor_invocations: int
    adjudicator_invocations: int
    total_time_seconds: float
    halt_reason: str
    cache_hit: bool
    created_at_ns: int


class ErrorDetail(BaseModel):
    """รายละเอียด error field หนึ่งรายการ (R19.3)."""

    loc: list[str] = Field(..., description="ตำแหน่ง field ที่ผิด")
    msg: str = Field(..., description="ข้อความอธิบายข้อผิดพลาด")
    type: str = Field(..., description="ชนิดข้อผิดพลาด")


class ValidationErrorResponse(BaseModel):
    """Response 422 — รายชื่อ field ที่ผิดทุก field (R19.3)."""

    detail: list[ErrorDetail] = Field(
        default_factory=list, description="รายการข้อผิดพลาดทุก field"
    )
