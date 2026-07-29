"""Query Trace — บันทึก trace ต่อ request (R19.6, R19.7, R4.10, R17.10, R10.10).

Design:
- query_trace หนึ่งรายการต่อ request_id — ทุกฟิลด์ NOT NULL
- บันทึก OCR/preprocessor/adjudicator invocation counts (ต้องเป็น 0 บน query path)
- บันทึก citation counts (sent/passed), removal count, unsupported claims
- บันทึก answer generation time
- replay: คืน trace เดิมเมื่อให้ request_id เดียวกัน
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


# ══════════════════════════════════════════════════════════════════════
# Data types
# ══════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class QueryTrace:
    """Trace record สำหรับหนึ่ง request — ทุกฟิลด์ NOT NULL (R19.6).

    Attributes:
        request_id: unique ID ต่อ request
        question: คำถามดิบ
        question_level: ระดับคำถาม (L1-L4)
        versions_resolved: ชุด version ที่ resolve ได้
        evidence_nodes: จำนวน evidence nodes ใน graph
        citations_sent: จำนวน citation ID ที่ส่งให้ LLM
        citations_passed: จำนวน citation ID ที่ผ่าน validation
        citations_removed: จำนวน claim units ที่ถูกลบ
        unsupported_claims: จำนวน claim ที่ไม่มี citation
        answer_generation_time_seconds: เวลาที่ใช้สร้างคำตอบ
        ocr_invocations: จำนวนครั้งที่เรียก OCR (ต้องเป็น 0 บน query path)
        preprocessor_invocations: จำนวนครั้งที่เรียก preprocessor (ต้องเป็น 0)
        adjudicator_invocations: จำนวนครั้งที่เรียก adjudicator (ต้องเป็น 0)
        total_time_seconds: เวลารวมทั้ง request
        halt_reason: เหตุผลที่หยุด evidence expansion
        cache_hit: ว่า hit answer cache หรือไม่
        created_at_ns: timestamp (nanoseconds) ที่สร้าง trace
    """

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

    def __post_init__(self) -> None:
        # ── NOT NULL enforcement ──
        if not self.request_id:
            raise ValueError("request_id ต้องไม่ว่าง")
        if not self.question:
            raise ValueError("question ต้องไม่ว่าง")
        if not self.question_level:
            raise ValueError("question_level ต้องไม่ว่าง")
        if not self.versions_resolved:
            raise ValueError("versions_resolved ต้องไม่ว่าง")
        if self.evidence_nodes < 0:
            raise ValueError("evidence_nodes ต้องไม่ติดลบ")
        if self.citations_sent < 0:
            raise ValueError("citations_sent ต้องไม่ติดลบ")
        if self.citations_passed < 0:
            raise ValueError("citations_passed ต้องไม่ติดลบ")
        if self.citations_removed < 0:
            raise ValueError("citations_removed ต้องไม่ติดลบ")
        if self.unsupported_claims < 0:
            raise ValueError("unsupported_claims ต้องไม่ติดลบ")
        if self.answer_generation_time_seconds < 0:
            raise ValueError("answer_generation_time_seconds ต้องไม่ติดลบ")
        if self.total_time_seconds < 0:
            raise ValueError("total_time_seconds ต้องไม่ติดลบ")
        if not self.halt_reason:
            raise ValueError("halt_reason ต้องไม่ว่าง")
        if self.created_at_ns <= 0:
            raise ValueError("created_at_ns ต้องมากกว่า 0")

    def to_dict(self) -> dict[str, Any]:
        """แปลง trace เป็น dict สำหรับ serialization."""
        return {
            "request_id": self.request_id,
            "question": self.question,
            "question_level": self.question_level,
            "versions_resolved": self.versions_resolved,
            "evidence_nodes": self.evidence_nodes,
            "citations_sent": self.citations_sent,
            "citations_passed": self.citations_passed,
            "citations_removed": self.citations_removed,
            "unsupported_claims": self.unsupported_claims,
            "answer_generation_time_seconds": self.answer_generation_time_seconds,
            "ocr_invocations": self.ocr_invocations,
            "preprocessor_invocations": self.preprocessor_invocations,
            "adjudicator_invocations": self.adjudicator_invocations,
            "total_time_seconds": self.total_time_seconds,
            "halt_reason": self.halt_reason,
            "cache_hit": self.cache_hit,
            "created_at_ns": self.created_at_ns,
        }


# ══════════════════════════════════════════════════════════════════════
# Trace Store — in-memory store (one per application lifetime)
# ══════════════════════════════════════════════════════════════════════


class TraceStore:
    """In-memory trace store — บันทึกและ replay query traces.

    - record(trace) → บันทึก trace ใหม่ (ปฏิเสธ duplicate request_id)
    - get(request_id) → คืน trace เดิม (replay)
    - count() → จำนวน traces ที่บันทึกไว้

    Design: replay คืน trace เดิมเมื่อให้ request_id เดียวกัน (R19.7).
    """

    __slots__ = ("_traces",)

    def __init__(self) -> None:
        self._traces: dict[str, QueryTrace] = {}

    def record(self, trace: QueryTrace) -> None:
        """บันทึก trace ใหม่.

        Args:
            trace: QueryTrace ที่จะบันทึก

        Raises:
            ValueError: ถ้า request_id ซ้ำ
        """
        if trace.request_id in self._traces:
            raise ValueError(
                f"duplicate request_id: {trace.request_id} — trace แต่ละ request บันทึกได้ครั้งเดียว"
            )
        self._traces[trace.request_id] = trace

    def get(self, request_id: str) -> QueryTrace | None:
        """คืน trace ที่บันทึกไว้ (replay) — None ถ้าไม่พบ.

        Returns same trace on replay (R19.7).
        """
        return self._traces.get(request_id)

    def count(self) -> int:
        """จำนวน traces ที่บันทึกไว้."""
        return len(self._traces)

    def all_traces(self) -> list[QueryTrace]:
        """คืน traces ทั้งหมด (เรียงตาม created_at_ns)."""
        return sorted(self._traces.values(), key=lambda t: t.created_at_ns)


# ══════════════════════════════════════════════════════════════════════
# Helper — สร้าง trace จากข้อมูลที่ collect ได้ระหว่าง request
# ══════════════════════════════════════════════════════════════════════


def create_query_trace(
    request_id: str,
    question: str,
    question_level: str,
    versions_resolved: str,
    evidence_nodes: int,
    citations_sent: int,
    citations_passed: int,
    citations_removed: int,
    unsupported_claims: int,
    answer_generation_time_seconds: float,
    total_time_seconds: float,
    halt_reason: str,
    cache_hit: bool = False,
) -> QueryTrace:
    """สร้าง QueryTrace พร้อม enforce ว่า OCR/preprocessor/adjudicator = 0 บน query path.

    R4.10: OCR invocation count on query path must equal zero.
    """
    return QueryTrace(
        request_id=request_id,
        question=question,
        question_level=question_level,
        versions_resolved=versions_resolved,
        evidence_nodes=evidence_nodes,
        citations_sent=citations_sent,
        citations_passed=citations_passed,
        citations_removed=citations_removed,
        unsupported_claims=unsupported_claims,
        answer_generation_time_seconds=answer_generation_time_seconds,
        # R4.10: query path must NOT invoke OCR/preprocessor/adjudicator
        ocr_invocations=0,
        preprocessor_invocations=0,
        adjudicator_invocations=0,
        total_time_seconds=total_time_seconds,
        halt_reason=halt_reason,
        cache_hit=cache_hit,
        created_at_ns=time.time_ns(),
    )
