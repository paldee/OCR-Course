"""KatRAG API Service — FastAPI application (R19.1, R19.2, R19.3, R19.8, R19.9).

Endpoints:
- POST /ask          — ส่งคำถาม, คืนคำตอบพร้อม citations
- GET  /documents    — รายการเอกสารพร้อมเวอร์ชัน (≤500 รายการ)
- GET  /pages/{id}   — หน้าเอกสารตาม citation ID พร้อม bbox
- GET  /traces/{id}  — query_trace ตาม request_id

R19.2: bind listener ที่ 127.0.0.1
R19.3: คืน 422 พร้อมรายชื่อ field ที่ผิดทุก field
R19.8: คืน 404 เมื่อ identifier ไม่มีอยู่
R19.9: ยุติคำขอที่เกิน 120 วินาทีพร้อมบันทึก trace
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
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
# Application factory
# ══════════════════════════════════════════════════════════════════════

# Default config values (used when config module is not available)
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_MAX_DOCUMENTS = 500
DEFAULT_REQUEST_TIMEOUT_SECONDS = 120.0
DEFAULT_MAX_QUESTION_CHARS = 2000


def create_app(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    max_documents: int = DEFAULT_MAX_DOCUMENTS,
    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    max_question_chars: int = DEFAULT_MAX_QUESTION_CHARS,
) -> FastAPI:
    """สร้าง FastAPI app instance พร้อม config.

    Args:
        host: bind address (R19.2 — loopback only)
        port: listen port
        max_documents: จำนวนเอกสารสูงสุดใน GET /documents response
        request_timeout_seconds: timeout ต่อ request (R19.9)
        max_question_chars: ความยาวคำถามสูงสุด (R19.3)
    """
    app = FastAPI(
        title="KatRAG API",
        version="0.1.0",
        description="Curriculum Q&A RAG API — loopback only",
    )

    # Store config in app state
    app.state.host = host
    app.state.port = port
    app.state.max_documents = max_documents
    app.state.request_timeout_seconds = request_timeout_seconds
    app.state.max_question_chars = max_question_chars

    # In-memory stores (replaced by real services when integrated)
    app.state.trace_store: dict[str, dict[str, Any]] = {}
    app.state.documents_store: list[dict[str, Any]] = []
    app.state.citations_store: dict[str, dict[str, Any]] = {}

    # ── Validation error handler (R19.3) ──────────────────────────────

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """คืน 422 พร้อมรายชื่อ field ที่ผิดทุก field (R19.3)."""
        errors: list[dict[str, Any]] = []
        for error in exc.errors():
            loc_parts: list[str] = []
            for part in error.get("loc", []):
                loc_parts.append(str(part))
            errors.append(
                {
                    "loc": loc_parts,
                    "msg": error.get("msg", ""),
                    "type": error.get("type", ""),
                }
            )
        return JSONResponse(status_code=422, content={"detail": errors})

    # ── Timeout middleware (R19.9) ────────────────────────────────────

    @app.middleware("http")
    async def timeout_middleware(request: Request, call_next: Any) -> Any:
        """ยุติคำขอที่เกิน request_timeout_seconds พร้อมบันทึก trace."""
        timeout = app.state.request_timeout_seconds
        start_time = time.time()

        try:
            response = await asyncio.wait_for(
                call_next(request), timeout=timeout
            )
            return response
        except asyncio.TimeoutError:
            elapsed = time.time() - start_time
            # บันทึก timeout trace
            request_id = str(uuid.uuid4())
            app.state.trace_store[request_id] = {
                "request_id": request_id,
                "question": "(timeout)",
                "question_level": "unknown",
                "versions_resolved": "",
                "evidence_nodes": 0,
                "citations_sent": 0,
                "citations_passed": 0,
                "citations_removed": 0,
                "unsupported_claims": 0,
                "answer_generation_time_seconds": 0.0,
                "ocr_invocations": 0,
                "preprocessor_invocations": 0,
                "adjudicator_invocations": 0,
                "total_time_seconds": elapsed,
                "halt_reason": "request_timeout",
                "cache_hit": False,
                "created_at_ns": time.time_ns(),
            }
            return JSONResponse(
                status_code=504,
                content={
                    "detail": "Request timeout exceeded",
                    "request_id": request_id,
                    "elapsed_seconds": round(elapsed, 3),
                },
            )

    # ── POST /ask (R19.1) ─────────────────────────────────────────────

    @app.post("/ask", response_model=AskResponse)
    async def ask(body: AskRequest) -> AskResponse:
        """ส่งคำถาม, คืนคำตอบพร้อม citations.

        - Validates question length 1-2000 chars (R19.3)
        - Returns answer with citations list
        - Records trace for each request
        """
        start_time = time.time()
        request_id = str(uuid.uuid4())

        # ── ตรวจ question length เพิ่มเติม (config-driven) ──
        question = body.question.strip()
        if len(question) < 1 or len(question) > app.state.max_question_chars:
            raise HTTPException(
                status_code=422,
                detail=[
                    {
                        "loc": ["body", "question"],
                        "msg": f"ความยาวคำถามต้องอยู่ในช่วง 1 ถึง {app.state.max_question_chars} อักขระ",
                        "type": "value_error",
                    }
                ],
            )

        # ── Real pipeline: Hybrid (lexical+dense) retriever → Typhoon LLM ──
        import sqlite3
        import pathlib

        db_path = pathlib.Path(__file__).resolve().parent.parent.parent / "artifacts" / "katrag.sqlite3"
        answer_text = ""
        citations: list[CitationItem] = []
        versions_resolved: list[str] = []

        try:
            conn = sqlite3.connect(str(db_path))

            # ── Structured answer path: query course/plan_slot ตรง ๆ (แม่นกว่า chunk) ──
            structured_context = ""
            try:
                from katrag.query.structured_query import (
                    try_structured_answer,
                    try_cross_version_diff,
                    detect_cross_version_intent,
                    try_plan_summary,
                    detect_plan_summary_intent,
                    try_prerequisite,
                    detect_prerequisite_intent,
                )
                # ลำดับ: prerequisite → cross-version diff → plan summary → รายวิชาปกติ
                if detect_prerequisite_intent(question):
                    sr = try_prerequisite(conn, question)
                    if not sr.matched:
                        sr = try_structured_answer(conn, question)
                elif detect_cross_version_intent(question):
                    sr = try_cross_version_diff(conn, question)
                elif detect_plan_summary_intent(question):
                    sr = try_plan_summary(conn, question)
                else:
                    sr = try_structured_answer(conn, question)
                if sr.matched:
                    structured_context = sr.context
                    if sr.version_label and sr.version_label not in versions_resolved:
                        versions_resolved.append(sr.version_label)
            except Exception:
                structured_context = ""

            # ลองใช้ hybrid search (lexical + dense RRF)
            try:
                from katrag.query.semantic_retriever import hybrid_search
                from katrag.index.dense_search import DenseSearchIndex

                # โหลด dense index (singleton pattern — แคชไว้ใน app.state)
                if not hasattr(app.state, "dense_index") or app.state.dense_index is None:
                    dense_idx = DenseSearchIndex(db_path)
                    loaded = dense_idx.load()
                    if loaded > 0:
                        app.state.dense_index = dense_idx
                    else:
                        app.state.dense_index = None

                if app.state.dense_index is not None:
                    raw_hits = hybrid_search(conn, app.state.dense_index, question, limit=10)
                    # แปลง HybridHit → format เดียวกับ RetrievedChunk
                    from types import SimpleNamespace
                    hits = [SimpleNamespace(
                        chunk_id=h.chunk_id, page_number=h.page_number,
                        heading=h.heading, text=h.text, program=h.program,
                        curriculum_year=h.curriculum_year, edition_status=h.edition_status,
                        score=h.fused_score
                    ) for h in raw_hits]
                else:
                    # Fallback: lexical only
                    from katrag.query.retriever import search as retrieve
                    hits = retrieve(conn, question, limit=8)
            except Exception:
                # Any error → fallback to lexical
                from katrag.query.retriever import search as retrieve
                hits = retrieve(conn, question, limit=8)

            conn.close()

            if not hits and not structured_context:
                answer_text = (
                    "ไม่พบข้อมูลที่เกี่ยวข้องกับคำถามนี้ในฐานข้อมูล\n\n"
                    "ลองระบุชื่อหลักสูตร (เช่น IT, DSBA, AIT, BIT) และปี พ.ศ. "
                    "หรือถามให้เจาะจงขึ้น เช่น 'หลักสูตร DSBA เรียนกี่หน่วยกิต'"
                )
            else:
                # สร้าง context — เริ่มด้วย structured data (ถ้ามี) ในฐานะหลักฐานหลัก
                context_parts = []
                if structured_context:
                    context_parts.append(f"[ข้อมูลจากฐานข้อมูลหลักสูตร — เชื่อถือได้ ครบถ้วน]:\n{structured_context}")
                for i, hit in enumerate(hits, 1):
                    heading = hit.heading or "ไม่มีหัวข้อ"
                    ver = f"{hit.program} {hit.curriculum_year}".strip()
                    snippet = hit.text[:500].strip()
                    context_parts.append(f"[{i}] ({heading} — {ver}, หน้า {hit.page_number}):\n{snippet}")
                    cite_id = f"cite-{i:03d}"
                    citations.append(CitationItem(
                        citation_id=cite_id,
                        document_id=str(hit.chunk_id),
                        page=hit.page_number,
                        heading=heading,
                    ))
                    # เก็บลง store เพื่อให้ GET /pages/{citation_id} ใช้งานได้
                    app.state.citations_store[cite_id] = {
                        "citation_id": cite_id,
                        "document_id": str(hit.chunk_id),
                        "page": hit.page_number,
                        "heading": heading,
                        "bbox": None,
                        "page_width": 0.0,
                        "page_height": 0.0,
                        "chunk_text": hit.text[:1000],
                    }
                    if hit.program and hit.curriculum_year:
                        ver_label = f"{hit.program} {hit.curriculum_year} ({hit.edition_status})"
                        if ver_label not in versions_resolved:
                            versions_resolved.append(ver_label)

                context = "\n\n".join(context_parts)

                # เรียก Typhoon LLM
                try:
                    from katrag.query.typhoon_llm import TyphoonLLM
                    from dotenv import load_dotenv
                    load_dotenv(pathlib.Path(__file__).resolve().parent.parent.parent / ".env")
                    llm = TyphoonLLM()

                    prompt = (
                        "คุณเป็นผู้ช่วยตอบคำถามเกี่ยวกับหลักสูตรของ KMITL "
                        "ใช้เฉพาะข้อมูลจากหลักฐานด้านล่างในการตอบ ตอบเป็นภาษาไทย ตรงประเด็นกับคำถาม\n"
                        "แนวทางการตอบ:\n"
                        "- ตอบเฉพาะสิ่งที่ถาม อย่าเพิ่มหมายเหตุหรือรายการที่ไม่ได้ถาม\n"
                        "- เมื่อระบุรายวิชา ให้ใส่ทั้งชื่อภาษาไทยและชื่อภาษาอังกฤษ (ในวงเล็บ) จำนวนหน่วยกิต และชั้นปี/ภาคที่เรียนถ้ามีในหลักฐาน\n"
                        "- ถ้าคำถามให้แจกแจงรายวิชา ให้ระบุครบทุกวิชาที่พบในหลักฐาน ไม่ซ้ำ\n"
                        "- ระบุหมายเลขหลักฐาน [n] ที่ใช้อ้างอิง\n"
                        "- ถ้าหลักฐานไม่มีข้อมูลเพียงพอ ให้บอกตามตรงว่าไม่พบข้อมูล\n\n"
                        f"== หลักฐาน ==\n{context}\n\n"
                        f"== คำถาม ==\n{question}\n\n"
                        "== คำตอบ ==\n"
                    )
                    answer_text = llm.generate(prompt, max_tokens=1024)

                    # Postprocess: dedup + backfill
                    from katrag.query.completeness import postprocess_answer
                    evidence_texts = [h.text for h in hits]
                    answer_text = postprocess_answer(answer_text, evidence_texts, question)
                except Exception as llm_exc:
                    # LLM ล้มเหลว → ตอบจาก chunks ตรง ๆ (fallback)
                    answer_text = (
                        f"(ระบบสรุปคำตอบด้วย LLM ไม่พร้อมใช้งาน: {type(llm_exc).__name__}: {llm_exc})\n\n"
                        f"ข้อมูลที่เกี่ยวข้องที่สุดจากฐานข้อมูล:\n\n{context}"
                    )
        except Exception as exc:
            answer_text = f"เกิดข้อผิดพลาด: {type(exc).__name__}: {exc}"

        elapsed = time.time() - start_time

        # ── บันทึก trace ──
        trace_data = {
            "request_id": request_id,
            "question": question,
            "question_level": "L1",
            "versions_resolved": "|".join(versions_resolved),
            "evidence_nodes": 0,
            "citations_sent": 0,
            "citations_passed": 0,
            "citations_removed": 0,
            "unsupported_claims": 0,
            "answer_generation_time_seconds": elapsed,
            "ocr_invocations": 0,
            "preprocessor_invocations": 0,
            "adjudicator_invocations": 0,
            "total_time_seconds": elapsed,
            "halt_reason": "no_new_evidence",
            "cache_hit": False,
            "created_at_ns": time.time_ns(),
        }
        app.state.trace_store[request_id] = trace_data

        return AskResponse(
            request_id=request_id,
            answer=answer_text,
            citations=citations,
            versions_resolved=versions_resolved,
            citations_removed=0,
            unsupported_claims=0,
            total_time_seconds=round(elapsed, 4),
        )

    # ── GET /documents (R19.1) ────────────────────────────────────────

    @app.get("/documents", response_model=DocumentsResponse)
    async def list_documents() -> DocumentsResponse:
        """รายการเอกสารพร้อมเวอร์ชัน (≤500 รายการ)."""
        docs = app.state.documents_store[: app.state.max_documents]
        items = [
            DocumentItem(
                document_id=d["document_id"],
                filename=d.get("filename", ""),
                page_count=d.get("page_count", 0),
                versions=d.get("versions", []),
            )
            for d in docs
        ]
        return DocumentsResponse(
            documents=items,
            total=len(app.state.documents_store),
        )

    # ── GET /pages/{citation_id} (R19.1, R19.8) ──────────────────────

    @app.get("/pages/{citation_id}", response_model=PageResponse)
    async def get_page(citation_id: str) -> PageResponse:
        """หน้าเอกสารตาม citation ID พร้อม bbox.

        Returns 404 เมื่อ citation_id ไม่มีอยู่ (R19.8).
        """
        citation_data = app.state.citations_store.get(citation_id)
        if citation_data is None:
            raise HTTPException(
                status_code=404,
                detail=f"citation_id '{citation_id}' ไม่พบในระบบ",
            )

        bbox_raw = citation_data.get("bbox")
        bbox = (
            BBoxItem(
                x0=bbox_raw["x0"],
                y0=bbox_raw["y0"],
                x1=bbox_raw["x1"],
                y1=bbox_raw["y1"],
            )
            if bbox_raw
            else None
        )

        return PageResponse(
            citation_id=citation_id,
            document_id=citation_data["document_id"],
            page=citation_data["page"],
            heading=citation_data.get("heading", ""),
            bbox=bbox,
            page_width=citation_data.get("page_width", 0.0),
            page_height=citation_data.get("page_height", 0.0),
            chunk_text=citation_data.get("chunk_text", ""),
        )

    # ── GET /traces/{request_id} (R19.1, R19.8) ──────────────────────

    @app.get("/traces/{request_id}", response_model=TraceResponse)
    async def get_trace(request_id: str) -> TraceResponse:
        """query_trace ตาม request_id.

        Returns 404 เมื่อ request_id ไม่มีอยู่ (R19.8).
        """
        trace_data = app.state.trace_store.get(request_id)
        if trace_data is None:
            raise HTTPException(
                status_code=404,
                detail=f"request_id '{request_id}' ไม่พบในระบบ",
            )

        return TraceResponse(**trace_data)

    # ── Static files: serve web/ directory at root ──────────────────
    import pathlib
    web_dir = pathlib.Path(__file__).resolve().parent.parent.parent / "web"
    if web_dir.is_dir():
        from fastapi.staticfiles import StaticFiles
        from fastapi.responses import FileResponse

        @app.get("/")
        async def serve_index():
            return FileResponse(web_dir / "index.html")

        app.mount("/", StaticFiles(directory=str(web_dir)), name="static")

    return app


# ══════════════════════════════════════════════════════════════════════
# Default app instance
# ══════════════════════════════════════════════════════════════════════

app = create_app()


# ══════════════════════════════════════════════════════════════════════
# Entrypoint — สำหรับ `python -m katrag.api.service`
# ══════════════════════════════════════════════════════════════════════


def main() -> None:  # pragma: no cover
    """Run API server with uvicorn — binds to 127.0.0.1 (R19.2)."""
    import uvicorn

    try:
        from katrag.config import load_config

        config = load_config()
        host = config.api.host
        port = config.api.port
    except Exception:
        host = DEFAULT_HOST
        port = DEFAULT_PORT

    uvicorn.run(
        "katrag.api.service:app",
        host=host,
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
