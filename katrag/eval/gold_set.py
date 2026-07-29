"""Gold_Set — loader/validator ของ gold set ที่โปรเจกต์สร้างเอง (design §4.20).

Gold set ครอบคลุมสิ่งที่ teacher ground truth วัดไม่ได้:
- ข้อความอ้างอิงระดับหน้า (page CER)
- ตารางอ้างอิงระดับ cell (table-cell F1)
- ชุดคำถาม L1–L4 พร้อมคำตอบอ้างอิง, เวอร์ชันที่ถูกต้อง, หน้าหลักฐาน
- คำถามที่คำตอบต่างกันระหว่าง old/current (version-selection accuracy)
- ผู้จัดทำ/วันที่/วิธีตรวจทาน

ต้องครอบคลุมทั้ง 14 เอกสาร (รวมบัณฑิตศึกษาและฉบับเก่า)

Format: ไดเรกทอรี ``artifacts/gold_set/`` มีไฟล์ JSON ตาม schema ที่กำหนด

Requirements: 12.1, 12.2, 12.3, 12.4, 12.5, 12.6
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping, Sequence

from katrag.common.types import CurriculumVersion, QuestionLevel
from katrag.errors import GoldSetError

logger = logging.getLogger(__name__)

# ── constants ─────────────────────────────────────────────────────────

EXPECTED_DOCUMENT_COUNT = 14

VALID_QUESTION_LEVELS: frozenset[str] = frozenset(("L1", "L2", "L3", "L4"))

VALID_ITEM_KINDS: frozenset[str] = frozenset(("page_text", "table_cell", "question"))


# ── types ─────────────────────────────────────────────────────────────


class GoldItemKind(StrEnum):
    """ชนิดของรายการใน gold set (design §4.20)."""

    PAGE_TEXT = "page_text"
    TABLE_CELL = "table_cell"
    QUESTION = "question"


@dataclass(frozen=True, slots=True)
class GoldItem:
    """หนึ่งรายการใน gold set — immutable เพื่อ determinism."""

    gold_id: int
    kind: GoldItemKind
    document_id: str | None
    page: int | None
    version: CurriculumVersion | None
    question_level: QuestionLevel | None
    payload: Mapping[str, object]
    expected: Mapping[str, object]
    expected_citations: tuple[tuple[str, int], ...]
    author: str
    created_date: str
    review_method: str

    def __post_init__(self) -> None:
        if self.gold_id < 0:
            raise ValueError("gold_id ต้องไม่ติดลบ")
        if not self.author:
            raise ValueError("author ต้องไม่ว่าง (R12.6)")
        if not self.created_date:
            raise ValueError("created_date ต้องไม่ว่าง (R12.6)")
        if not self.review_method:
            raise ValueError("review_method ต้องไม่ว่าง (R12.6)")


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """ปัญหาที่พบระหว่าง validate — เก็บเป็น review_issue ได้."""

    kind: str
    message: str
    detail: Mapping[str, Any] = field(default_factory=dict)


# ── GoldSet class ─────────────────────────────────────────────────────


class GoldSet:
    """Loader/validator ของ gold set (design §4.20).

    Lifecycle:
        1. ``load(path)`` — อ่านไฟล์ JSON จากไดเรกทอรี gold set
        2. ``validate_coverage()`` — ตรวจว่าครอบคลุมทั้ง 14 เอกสาร
        3. ``validate_completeness()`` — ตรวจองค์ประกอบครบตาม R12.1–R12.6
    """

    def __init__(self) -> None:
        self._items: tuple[GoldItem, ...] = ()

    @property
    def items(self) -> tuple[GoldItem, ...]:
        return self._items

    # ── loading ───────────────────────────────────────────────────────

    def load(self, path: Path) -> tuple[GoldItem, ...]:
        """โหลด gold set จากไดเรกทอรี (หรือไฟล์ JSON เดียว).

        Args:
            path: เส้นทางไปยังไดเรกทอรี ``artifacts/gold_set/`` หรือไฟล์ JSON เดี่ยว

        Returns:
            tuple ของ GoldItem ที่โหลดได้ทั้งหมด

        Raises:
            GoldSetError: เมื่อ path ไม่มีอยู่, format ผิด, หรือขาดฟิลด์บังคับ
        """
        if not path.exists():
            raise GoldSetError(
                "ไม่พบไดเรกทอรี/ไฟล์ gold set",
                path=str(path),
            )

        raw_items: list[dict[str, Any]] = []

        if path.is_file():
            raw_items = self._load_json_file(path)
        elif path.is_dir():
            json_files = sorted(path.glob("*.json"))
            if not json_files:
                raise GoldSetError(
                    "ไดเรกทอรี gold set ไม่มีไฟล์ JSON",
                    path=str(path),
                )
            for json_file in json_files:
                raw_items.extend(self._load_json_file(json_file))
        else:
            raise GoldSetError(
                "path ของ gold set ต้องเป็นไดเรกทอรีหรือไฟล์ JSON",
                path=str(path),
            )

        items = tuple(self._parse_item(raw, idx) for idx, raw in enumerate(raw_items))
        self._items = items
        logger.info("โหลด gold set สำเร็จ: %d รายการ", len(items))
        return items

    # ── validation ────────────────────────────────────────────────────

    def validate_coverage(
        self, expected_document_ids: frozenset[str] | None = None
    ) -> tuple[ValidationIssue, ...]:
        """ตรวจว่า gold set ครอบคลุมทั้ง 14 เอกสาร (R12.1).

        Args:
            expected_document_ids: ชุด document_id ที่คาดว่าต้องมี
                ถ้าไม่ระบุจะตรวจเฉพาะจำนวน distinct document_ids ≥ 14

        Returns:
            tuple ของ ValidationIssue (ว่างเมื่อผ่านทั้งหมด)
        """
        issues: list[ValidationIssue] = []

        # รวบรวม document_ids ที่ปรากฏใน gold set
        covered_ids: set[str] = set()
        for item in self._items:
            if item.document_id:
                covered_ids.add(item.document_id)

        if expected_document_ids is not None:
            missing = expected_document_ids - covered_ids
            if missing:
                issues.append(ValidationIssue(
                    kind="gold_set_incomplete_coverage",
                    message=(
                        f"gold set ไม่ครอบคลุมเอกสาร {len(missing)} ไฟล์: "
                        f"{sorted(missing)}"
                    ),
                    detail={
                        "missing_document_ids": sorted(missing),
                        "covered_count": len(covered_ids),
                        "expected_count": len(expected_document_ids),
                    },
                ))
        else:
            if len(covered_ids) < EXPECTED_DOCUMENT_COUNT:
                issues.append(ValidationIssue(
                    kind="gold_set_incomplete_coverage",
                    message=(
                        f"gold set ครอบคลุมเพียง {len(covered_ids)} เอกสาร "
                        f"(ต้องครบ {EXPECTED_DOCUMENT_COUNT})"
                    ),
                    detail={
                        "covered_count": len(covered_ids),
                        "expected_count": EXPECTED_DOCUMENT_COUNT,
                        "covered_document_ids": sorted(covered_ids),
                    },
                ))

        return tuple(issues)

    def validate_completeness(self) -> tuple[ValidationIssue, ...]:
        """ตรวจองค์ประกอบครบตาม R12.1–R12.6.

        Checks:
            - R12.2: มีข้อความอ้างอิงระดับหน้า (page_text items)
            - R12.3: มีตารางอ้างอิงระดับ cell (table_cell items)
            - R12.4: มีคำถาม L1-L4 พร้อมคำตอบ, version, evidence pages
            - R12.5: มีคำถามที่คำตอบต่างกันระหว่าง old/current
            - R12.6: ทุกรายการมี author, created_date, review_method

        Returns:
            tuple ของ ValidationIssue (ว่างเมื่อผ่านทั้งหมด)
        """
        issues: list[ValidationIssue] = []

        # แยกรายการตาม kind
        page_text_items = [i for i in self._items if i.kind == GoldItemKind.PAGE_TEXT]
        table_cell_items = [i for i in self._items if i.kind == GoldItemKind.TABLE_CELL]
        question_items = [i for i in self._items if i.kind == GoldItemKind.QUESTION]

        # R12.2: ต้องมีข้อความอ้างอิงระดับหน้า
        if not page_text_items:
            issues.append(ValidationIssue(
                kind="gold_set_missing_page_text",
                message="gold set ไม่มีข้อความอ้างอิงระดับหน้า (R12.2)",
            ))

        # R12.3: ต้องมีตารางอ้างอิงระดับ cell
        if not table_cell_items:
            issues.append(ValidationIssue(
                kind="gold_set_missing_table_cell",
                message="gold set ไม่มีตารางอ้างอิงระดับ cell (R12.3)",
            ))

        # R12.4: ต้องมีคำถาม L1-L4 พร้อมคำตอบ, version, evidence pages
        question_levels_found: set[str] = set()
        questions_missing_version: list[int] = []
        questions_missing_citations: list[int] = []
        questions_missing_expected: list[int] = []

        for q in question_items:
            if q.question_level:
                question_levels_found.add(q.question_level)
            if q.version is None:
                questions_missing_version.append(q.gold_id)
            if not q.expected_citations:
                questions_missing_citations.append(q.gold_id)
            if not q.expected:
                questions_missing_expected.append(q.gold_id)

        missing_levels = VALID_QUESTION_LEVELS - question_levels_found
        if missing_levels:
            issues.append(ValidationIssue(
                kind="gold_set_missing_question_levels",
                message=(
                    f"gold set ขาดคำถามระดับ {sorted(missing_levels)} (R12.4)"
                ),
                detail={"missing_levels": sorted(missing_levels)},
            ))

        if questions_missing_version:
            issues.append(ValidationIssue(
                kind="gold_set_question_missing_version",
                message=(
                    f"คำถาม {len(questions_missing_version)} ข้อไม่มี curriculum version (R12.4)"
                ),
                detail={"gold_ids": questions_missing_version},
            ))

        if questions_missing_citations:
            issues.append(ValidationIssue(
                kind="gold_set_question_missing_citations",
                message=(
                    f"คำถาม {len(questions_missing_citations)} ข้อไม่มีหน้าหลักฐาน (R12.4)"
                ),
                detail={"gold_ids": questions_missing_citations},
            ))

        if questions_missing_expected:
            issues.append(ValidationIssue(
                kind="gold_set_question_missing_expected",
                message=(
                    f"คำถาม {len(questions_missing_expected)} ข้อไม่มีคำตอบอ้างอิง (R12.4)"
                ),
                detail={"gold_ids": questions_missing_expected},
            ))

        # R12.5: ต้องมีคำถามที่คำตอบต่างกันระหว่าง old/current
        has_version_diff_questions = any(
            q.payload.get("version_diff", False) for q in question_items
        )
        if not has_version_diff_questions:
            issues.append(ValidationIssue(
                kind="gold_set_missing_version_diff_questions",
                message=(
                    "gold set ไม่มีคำถามที่คำตอบต่างกันระหว่าง old/current (R12.5)"
                ),
            ))

        # R12.6: ทุกรายการต้องมี author, created_date, review_method
        # (ตรวจแล้วใน __post_init__ แต่เช็คซ้ำสำหรับกรณี edge)
        metadata_issues: list[int] = []
        for item in self._items:
            if not item.author or not item.created_date or not item.review_method:
                metadata_issues.append(item.gold_id)
        if metadata_issues:
            issues.append(ValidationIssue(
                kind="gold_set_missing_metadata",
                message=(
                    f"รายการ {len(metadata_issues)} ข้อไม่มี author/created_date/review_method (R12.6)"
                ),
                detail={"gold_ids": metadata_issues},
            ))

        return tuple(issues)

    def validate_all(
        self, expected_document_ids: frozenset[str] | None = None
    ) -> tuple[ValidationIssue, ...]:
        """รัน validate_coverage + validate_completeness รวมกัน."""
        return self.validate_coverage(expected_document_ids) + self.validate_completeness()

    # ── accessors ─────────────────────────────────────────────────────

    def page_text_items(self) -> tuple[GoldItem, ...]:
        """คืนเฉพาะรายการ page_text."""
        return tuple(i for i in self._items if i.kind == GoldItemKind.PAGE_TEXT)

    def table_cell_items(self) -> tuple[GoldItem, ...]:
        """คืนเฉพาะรายการ table_cell."""
        return tuple(i for i in self._items if i.kind == GoldItemKind.TABLE_CELL)

    def question_items(self) -> tuple[GoldItem, ...]:
        """คืนเฉพาะรายการ question."""
        return tuple(i for i in self._items if i.kind == GoldItemKind.QUESTION)

    def questions_by_level(self, level: QuestionLevel) -> tuple[GoldItem, ...]:
        """คืนคำถามตามระดับที่ระบุ."""
        return tuple(
            i for i in self._items
            if i.kind == GoldItemKind.QUESTION and i.question_level == level
        )

    def version_diff_questions(self) -> tuple[GoldItem, ...]:
        """คืนคำถามที่คำตอบต่างกันระหว่าง old/current (R12.5)."""
        return tuple(
            i for i in self._items
            if i.kind == GoldItemKind.QUESTION and i.payload.get("version_diff", False)
        )

    def document_ids(self) -> frozenset[str]:
        """คืน document_ids ทั้งหมดที่ปรากฏใน gold set."""
        return frozenset(
            i.document_id for i in self._items if i.document_id is not None
        )

    # ── internal ──────────────────────────────────────────────────────

    def _load_json_file(self, path: Path) -> list[dict[str, Any]]:
        """อ่านไฟล์ JSON หนึ่งไฟล์ คืนรายการ raw dict."""
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            raise GoldSetError(
                "อ่านไฟล์ gold set ไม่สำเร็จ",
                path=str(path),
                reason=str(exc),
            ) from exc

        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "items" in data:
            items = data["items"]
            if isinstance(items, list):
                return items
            raise GoldSetError(
                "field 'items' ใน gold set ต้องเป็น array",
                path=str(path),
            )
        raise GoldSetError(
            "ไฟล์ gold set ต้องเป็น JSON array หรือ object ที่มี key 'items'",
            path=str(path),
        )

    def _parse_item(self, raw: dict[str, Any], fallback_id: int) -> GoldItem:
        """แปลง raw dict เป็น GoldItem พร้อม validate ฟิลด์บังคับ."""
        if not isinstance(raw, dict):
            raise GoldSetError(
                "แต่ละรายการใน gold set ต้องเป็น JSON object",
                item_index=fallback_id,
            )

        # gold_id
        gold_id = raw.get("gold_id", fallback_id)
        if not isinstance(gold_id, int) or gold_id < 0:
            gold_id = fallback_id

        # kind (บังคับ)
        kind_raw = raw.get("item_kind") or raw.get("kind")
        if kind_raw not in VALID_ITEM_KINDS:
            raise GoldSetError(
                f"item_kind ต้องเป็นค่าหนึ่งใน {sorted(VALID_ITEM_KINDS)}",
                gold_id=gold_id,
                actual=kind_raw,
            )
        kind = GoldItemKind(kind_raw)

        # document_id
        document_id = raw.get("document_id")
        if document_id is not None and not isinstance(document_id, str):
            document_id = str(document_id)

        # page
        page = raw.get("page") or raw.get("page_number")
        if page is not None:
            if not isinstance(page, int) or page < 1:
                raise GoldSetError(
                    "page ต้องเป็นจำนวนเต็มตั้งแต่ 1",
                    gold_id=gold_id,
                    actual=page,
                )

        # curriculum version
        version: CurriculumVersion | None = None
        version_raw = raw.get("version") or raw.get("curriculum_version")
        if version_raw is not None and isinstance(version_raw, dict):
            try:
                version = CurriculumVersion(
                    program=str(version_raw.get("program", "")),
                    curriculum_year=int(version_raw.get("curriculum_year", 0)),
                    edition_status=version_raw.get("edition_status", "current"),
                )
            except (ValueError, TypeError) as exc:
                logger.warning(
                    "gold_id=%d: curriculum version ไม่ถูกต้อง: %s", gold_id, exc
                )
                version = None

        # question_level
        question_level: QuestionLevel | None = None
        level_raw = raw.get("question_level")
        if level_raw is not None:
            if level_raw in VALID_QUESTION_LEVELS:
                question_level = level_raw  # type: ignore[assignment]
            else:
                raise GoldSetError(
                    f"question_level ต้องเป็นค่าหนึ่งใน {sorted(VALID_QUESTION_LEVELS)}",
                    gold_id=gold_id,
                    actual=level_raw,
                )

        # payload (บังคับ)
        payload = raw.get("payload")
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            raise GoldSetError(
                "payload ต้องเป็น JSON object",
                gold_id=gold_id,
            )

        # expected (บังคับ)
        expected = raw.get("expected")
        if expected is None:
            expected = {}
        if not isinstance(expected, dict):
            raise GoldSetError(
                "expected ต้องเป็น JSON object",
                gold_id=gold_id,
            )

        # expected_citations — [[document_id, page], ...]
        citations_raw = raw.get("expected_citations", [])
        if not isinstance(citations_raw, list):
            raise GoldSetError(
                "expected_citations ต้องเป็น array ของ [document_id, page]",
                gold_id=gold_id,
            )
        expected_citations: list[tuple[str, int]] = []
        for citation in citations_raw:
            if (
                not isinstance(citation, (list, tuple))
                or len(citation) != 2
                or not isinstance(citation[0], str)
                or not isinstance(citation[1], int)
            ):
                raise GoldSetError(
                    "แต่ละ citation ต้องเป็น [document_id: str, page: int]",
                    gold_id=gold_id,
                    actual=citation,
                )
            expected_citations.append((citation[0], citation[1]))

        # metadata (R12.6 — บังคับ)
        author = raw.get("author", "")
        if not isinstance(author, str) or not author.strip():
            raise GoldSetError(
                "author ต้องไม่ว่าง (R12.6)",
                gold_id=gold_id,
            )
        author = author.strip()

        created_date = raw.get("created_date", "")
        if not isinstance(created_date, str) or not created_date.strip():
            raise GoldSetError(
                "created_date ต้องไม่ว่าง (R12.6)",
                gold_id=gold_id,
            )
        created_date = created_date.strip()

        review_method = raw.get("review_method", "")
        if not isinstance(review_method, str) or not review_method.strip():
            raise GoldSetError(
                "review_method ต้องไม่ว่าง (R12.6)",
                gold_id=gold_id,
            )
        review_method = review_method.strip()

        # validate kind-specific requirements
        self._validate_kind_specific(kind, gold_id, document_id, page, question_level, payload)

        return GoldItem(
            gold_id=gold_id,
            kind=kind,
            document_id=document_id,
            page=page,
            version=version,
            question_level=question_level,
            payload=payload,
            expected=expected,
            expected_citations=tuple(expected_citations),
            author=author,
            created_date=created_date,
            review_method=review_method,
        )

    def _validate_kind_specific(
        self,
        kind: GoldItemKind,
        gold_id: int,
        document_id: str | None,
        page: int | None,
        question_level: QuestionLevel | None,
        payload: Mapping[str, Any],
    ) -> None:
        """ตรวจฟิลด์เฉพาะตาม kind."""
        if kind == GoldItemKind.PAGE_TEXT:
            # ต้องมี document_id และ page (R12.2)
            if not document_id:
                raise GoldSetError(
                    "page_text ต้องมี document_id",
                    gold_id=gold_id,
                )
            if page is None:
                raise GoldSetError(
                    "page_text ต้องมี page",
                    gold_id=gold_id,
                )
            # payload ต้องมี reference_text
            if "reference_text" not in payload:
                raise GoldSetError(
                    "page_text payload ต้องมี 'reference_text'",
                    gold_id=gold_id,
                )

        elif kind == GoldItemKind.TABLE_CELL:
            # ต้องมี document_id และ page (R12.3)
            if not document_id:
                raise GoldSetError(
                    "table_cell ต้องมี document_id",
                    gold_id=gold_id,
                )
            if page is None:
                raise GoldSetError(
                    "table_cell ต้องมี page",
                    gold_id=gold_id,
                )

        elif kind == GoldItemKind.QUESTION:
            # ต้องมี question_level (R12.4)
            if question_level is None:
                raise GoldSetError(
                    "question ต้องมี question_level (L1-L4)",
                    gold_id=gold_id,
                )
