"""Error taxonomy ของ KatRAG-lite (design §6).

หลักการ: ทุกความล้มเหลวต้องกลายเป็นข้อมูลที่ตรวจสอบได้ ไม่ใช่ silent fallback
error ทุกชนิดจึงพา payload ที่ระบุ "อะไรผิด ที่ไหน" ไปด้วยเสมอ
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class KatragError(Exception):
    """base ของ error ทุกชนิดในระบบ."""

    def __init__(self, message: str, **context: Any) -> None:
        super().__init__(message)
        self.message = message
        self.context: dict[str, Any] = dict(context)

    def __str__(self) -> str:  # pragma: no cover - รูปแบบข้อความอ่านง่าย
        if not self.context:
            return self.message
        detail = ", ".join(f"{k}={v!r}" for k, v in sorted(self.context.items()))
        return f"{self.message} ({detail})"


# ── config / preflight ────────────────────────────────────────────────


class ConfigError(KatragError):
    """ค่าตั้งค่าขาด, ชนิดผิด หรืออยู่นอกช่วงที่ requirements กำหนด."""


class PreflightError(KatragError):
    """artifact ขาด หรือ sha256 ไม่ตรงกับที่บันทึกไว้ (R20.8)."""


class OfflineViolationError(KatragError):
    """มีการพยายามเรียก address ที่ไม่ใช่ loopback (R20.9)."""


# ── dataset / ingestion ───────────────────────────────────────────────


class DatasetScopeError(KatragError):
    """จำนวนเอกสารหรือผลรวมหน้าไม่ตรงขอบเขตที่ประกาศไว้ (R1.3)."""


class DocumentUnreadableError(KatragError):
    """เปิดไฟล์เอกสารไม่ได้ — ต้องไปเอกสารถัดไป (R2.5)."""


class PageUnreadableError(KatragError):
    """อ่านหน้าไม่ได้ — ต้องไปหน้าถัดไปโดยไม่บันทึกข้อความบางส่วน (R2.4)."""


class MemoryLimitExceededError(KatragError):
    """resident memory เกินเพดาน — หยุดอย่างปลอดภัยและคงผลที่เสร็จแล้ว (R6.6)."""


class OcrEngineError(KatragError):
    """OCR engine คืน error หรือเกิน timeout ต่อ region (R5.6)."""


# ── parsing ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ParseFailure:
    """ผลการ parse ที่ไม่สำเร็จ พร้อม index อักขระแรกที่ไม่ตรงรูปแบบ (นับจาก 0)."""

    raw_text: str
    error_index: int
    reason: str

    def __post_init__(self) -> None:
        if self.error_index < 0:
            raise ValueError("error_index ต้องไม่ติดลบ")


class CreditsParseError(KatragError):
    """สตริงหน่วยกิตไม่ตรงรูปแบบ หรือค่าอยู่นอกช่วง 0-30 (R8.3)."""

    def __init__(self, failure: ParseFailure) -> None:
        super().__init__(
            "credits parse error",
            raw_text=failure.raw_text,
            error_index=failure.error_index,
            reason=failure.reason,
        )
        self.failure = failure


class PrerequisiteParseError(KatragError):
    """ข้อความวิชาก่อนไม่ตรงไวยากรณ์ หรือเกินขอบเขตที่กำหนด (R8.9)."""

    def __init__(self, failure: ParseFailure) -> None:
        super().__init__(
            "prerequisite parse error",
            raw_text=failure.raw_text,
            error_index=failure.error_index,
            reason=failure.reason,
        )
        self.failure = failure


# ── store ─────────────────────────────────────────────────────────────


class ProvenanceViolationError(KatragError):
    """แถวข้อมูลหลักสูตรอ้าง provenance ที่ไม่มีอยู่ หรือ provenance ไม่ครบฟิลด์ (R9.3).

    ต้องทำให้ transaction ถูกปฏิเสธทั้งก้อน โดยไม่คงแถวใดไว้
    """

    def __init__(self, table: str, field_name: str, provenance_attribute: str) -> None:
        super().__init__(
            "provenance ไม่ผ่านการตรวจ — ปฏิเสธทั้ง transaction",
            table=table,
            field=field_name,
            provenance_attribute=provenance_attribute,
        )
        self.table = table
        self.field_name = field_name
        self.provenance_attribute = provenance_attribute


class VersionStampMissingError(KatragError):
    """chunk หรือ field ที่จะเขียนไม่มี curriculum version ครบสามค่า (R10.2)."""

    def __init__(self, field_name: str, missing: tuple[str, ...]) -> None:
        super().__init__(
            "curriculum version ไม่ครบ — ปฏิเสธการเขียนแถวนั้น",
            field=field_name,
            missing=list(missing),
        )
        self.field_name = field_name
        self.missing = missing


class StoreAccessError(KatragError):
    """เปิด/เขียนไฟล์ SQLite ล้มเหลว หรือ integrity check ไม่ผ่าน (R9.8)."""


# ── query path ────────────────────────────────────────────────────────


class QuestionInputInvalidError(KatragError):
    """ความยาวคำถามอยู่นอกขอบเขตที่รองรับ (R13.10, R16.6, R19.3)."""

    def __init__(self, length: int, min_chars: int, max_chars: int) -> None:
        super().__init__(
            f"ความยาวคำถามต้องอยู่ในช่วง {min_chars} ถึง {max_chars} อักขระ",
            length=length,
            min_chars=min_chars,
            max_chars=max_chars,
        )
        self.length = length


class CrossVersionCitationError(KatragError):
    """คำตอบอ้าง chunk ที่อยู่นอกชุด curriculum version ของคำขอ (R10.8).

    ต้องปฏิเสธคำตอบทั้งฉบับ ไม่ใช่ตัดบางส่วน
    """

    def __init__(self, citation_ids: tuple[str, ...]) -> None:
        super().__init__(
            "พบการอ้างอิงข้ามเวอร์ชัน — ปฏิเสธคำตอบทั้งฉบับ",
            citation_ids=list(citation_ids),
        )
        self.citation_ids = citation_ids


class PrerequisiteCycleError(KatragError):
    """กราฟวิชาก่อนมี cycle (R15.2)."""

    def __init__(self, course_codes: tuple[str, ...]) -> None:
        super().__init__("พบ cycle ในกราฟวิชาก่อน", course_codes=list(course_codes))
        self.course_codes = course_codes


class AnswerGenerationError(KatragError):
    """โมเดลท้องถิ่นล้มเหลว หรือเกิน answer_time_budget (R17.9)."""


class IdentifierNotFoundError(KatragError):
    """citation ID / document_id / request_id ที่ขอไม่มีอยู่ (R19.8)."""

    def __init__(self, kind: str, value: str) -> None:
        super().__init__("ไม่พบ identifier ที่ระบุ", kind=kind, value=value)
        self.kind = kind
        self.value = value


class RequestTimeoutError(KatragError):
    """คำขอใช้เวลาเกินเพดานที่กำหนด (R19.9)."""


# ── evaluation ────────────────────────────────────────────────────────


class MetricScopeMismatchError(KatragError):
    """ขอบเขตหน้าของค่าที่ประเมินกับค่าอ้างอิงไม่ตรงกัน (R18.2).

    ต้องปฏิเสธ metric นั้นทั้งตัว แต่ยังคำนวณ metric อื่นในรันเดียวกันต่อ
    """

    def __init__(
        self,
        metric: str,
        evaluated: tuple[str, int],
        reference: tuple[str, int],
    ) -> None:
        super().__init__(
            "ขอบเขตหน้าไม่ตรงกัน — ปฏิเสธการคำนวณ metric นี้",
            metric=metric,
            evaluated_document_id=evaluated[0],
            evaluated_page=evaluated[1],
            reference_document_id=reference[0],
            reference_page=reference[1],
        )
        self.metric = metric


class MetricNotReproducibleError(KatragError):
    """รันซ้ำด้วยข้อมูลชุดเดิมได้ค่า metric ต่างจากครั้งก่อน (R18.9)."""

    def __init__(self, metric: str, previous: float, current: float) -> None:
        super().__init__(
            "ค่า metric ไม่คงที่เมื่อรันซ้ำ",
            metric=metric,
            previous=previous,
            current=current,
        )
        self.metric = metric


class GoldSetError(KatragError):
    """gold set ขาดองค์ประกอบที่ requirements บังคับ (R12)."""


# ── review issue (ไม่ใช่ exception แต่เป็นข้อมูลที่ต้องบันทึก) ──────────


@dataclass(frozen=True, slots=True)
class ReviewIssue:
    """รายการที่ต้องให้มนุษย์ตรวจ — บันทึกลงตาราง review_issue."""

    kind: str
    detail: dict[str, Any] = field(default_factory=dict)
    document_id: str | None = None
    page: int | None = None

    def __post_init__(self) -> None:
        if not self.kind:
            raise ValueError("review issue ต้องระบุ kind")
        if self.page is not None and self.page < 1:
            raise ValueError("page ต้องเป็นจำนวนเต็มตั้งแต่ 1")
