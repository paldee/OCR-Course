"""Citation Registry — ออก citation ID ต่อ evidence unit และแปลงกลับ (R17.6, R19.1).

CitationRegistry สร้างใหม่ทุก request (closed set per request):
- issue(evidence_unit) → CitationId  ออก ID ลำดับถัดไป e.g. "cite-001"
- resolve(citation_id) → CitationInfo | None  แปลง ID กลับเป็นข้อมูลต้นทาง
- all_ids() → frozenset  ชุด citation ID ทั้งหมดที่ออกไปแล้ว
- count() → int  จำนวน citation ที่ออกไปแล้ว

LLM จะได้รับเฉพาะ citation ID ที่ระบบออกให้ — ห้ามสร้างเอง (R17.2, R17.3).
"""

from __future__ import annotations

from dataclasses import dataclass

from katrag.common.types import CitationId


# ── Data types ────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class EvidenceUnit:
    """หน่วยหลักฐานที่จะถูกออก citation ID — input ของ registry."""

    chunk_id: str
    document_id: str
    page: int
    heading: str
    text: str

    def __post_init__(self) -> None:
        if not self.chunk_id:
            raise ValueError("chunk_id ต้องไม่ว่าง")
        if not self.document_id:
            raise ValueError("document_id ต้องไม่ว่าง")
        if self.page < 1:
            raise ValueError("page ต้องเป็นจำนวนเต็มตั้งแต่ 1")


@dataclass(frozen=True, slots=True)
class CitationInfo:
    """ข้อมูลต้นทางที่ resolve กลับมาจาก citation ID (R19.1)."""

    document_id: str
    page: int
    heading: str
    chunk_id: str


# ── Citation Registry ─────────────────────────────────────────────────


class CitationRegistry:
    """Registry ที่ออก citation ID แบบ sequential per request.

    สร้างใหม่ทุก request — ไม่แชร์ข้ามคำขอ (R17.6).
    """

    __slots__ = ("_counter", "_id_to_info", "_issued_ids")

    def __init__(self) -> None:
        self._counter: int = 0
        self._id_to_info: dict[str, CitationInfo] = {}
        self._issued_ids: list[CitationId] = []

    def issue(self, evidence_unit: EvidenceUnit) -> CitationId:
        """ออก citation ID ถัดไปสำหรับ evidence unit ที่ให้มา.

        Returns:
            CitationId ที่มีรูปแบบ "cite-001", "cite-002", ...
        """
        self._counter += 1
        cite_value = f"cite-{self._counter:03d}"
        citation_id = CitationId(value=cite_value)

        info = CitationInfo(
            document_id=evidence_unit.document_id,
            page=evidence_unit.page,
            heading=evidence_unit.heading,
            chunk_id=evidence_unit.chunk_id,
        )

        self._id_to_info[cite_value] = info
        self._issued_ids.append(citation_id)

        return citation_id

    def resolve(self, citation_id: CitationId | str) -> CitationInfo | None:
        """แปลง citation ID กลับเป็นข้อมูลต้นทาง.

        Args:
            citation_id: CitationId object หรือ string value ของ citation ID

        Returns:
            CitationInfo ถ้าพบ, None ถ้าไม่พบ (ID ที่ระบบไม่ได้ออก)
        """
        key = str(citation_id) if isinstance(citation_id, CitationId) else citation_id
        return self._id_to_info.get(key)

    def all_ids(self) -> frozenset[CitationId]:
        """คืนชุด citation ID ทั้งหมดที่ออกไปแล้วใน request นี้."""
        return frozenset(self._issued_ids)

    def count(self) -> int:
        """จำนวน citation ID ที่ออกไปแล้ว."""
        return self._counter
