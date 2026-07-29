"""Field_Extractor — สกัด 11 field ของรายวิชาพร้อม provenance (R8.1, R8.7, R8.10).

หน้าที่:
1. รับ TableCell records (จาก table_extractor) และ/หรือ text lines แล้วผลิต
   CourseRecord ที่มี field ครบ 11 field พร้อม per-field provenance
2. ตรวจค่า category / type ว่าอยู่ในชุดค่าปิดจากไฟล์ตั้งค่า (value_sets.toml)
3. เมื่อหาต้นทางไม่ได้หรือค่าขัดแย้งกัน → บันทึก field นั้นเป็นค่าว่าง +
   review_issue ชนิด ``field_unresolved`` แต่คง course record ไว้กับ field อื่นที่สำเร็จ

Requirements covered: 8.1, 8.7, 8.10
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from katrag.common.types import (
    BBox,
    Credits,
    ExtractionMethod,
    PrereqEmpty,
    PrereqNode,
    Provenance,
)
from katrag.config import ValueSets
from katrag.errors import ParseFailure, ReviewIssue
from katrag.ingest.fields.credits import parse_credits
from katrag.ingest.fields.prerequisite import parse_prerequisite
from katrag.ingest.table_extractor import TableCell


# ── CourseRecord ──────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class FieldValue:
    """ค่าของ field หนึ่งใน course record — อาจเป็นค่าจริงหรือค่าว่าง (unresolved)."""

    value: Any
    provenance: Provenance | None
    resolved: bool

    @staticmethod
    def resolved_field(value: Any, provenance: Provenance) -> "FieldValue":
        """สร้าง field ที่สกัดได้สำเร็จ."""
        return FieldValue(value=value, provenance=provenance, resolved=True)

    @staticmethod
    def unresolved_field() -> "FieldValue":
        """สร้าง field ที่หาต้นทางไม่ได้ (R8.10)."""
        return FieldValue(value=None, provenance=None, resolved=False)


@dataclass(frozen=True, slots=True)
class CourseRecord:
    """หนึ่ง course record ที่มี 11 field ตาม R8.1 พร้อม per-field provenance."""

    code: FieldValue
    name_th: FieldValue
    name_en: FieldValue
    credits: FieldValue
    year: FieldValue
    semester: FieldValue
    category: FieldValue
    type: FieldValue
    prerequisite: FieldValue
    flexible_year_semester: FieldValue
    note: FieldValue

    # Metadata ที่ไม่ใช่ field แต่ต้องระบุเพื่อบันทึก course
    document_id: str = ""
    page: int = 0

    @property
    def field_names(self) -> tuple[str, ...]:
        return (
            "code", "name_th", "name_en", "credits", "year",
            "semester", "category", "type", "prerequisite",
            "flexible_year_semester", "note",
        )

    def get_field(self, name: str) -> FieldValue:
        """คืน FieldValue ตามชื่อ field."""
        return getattr(self, name)

    @property
    def all_resolved(self) -> bool:
        return all(self.get_field(n).resolved for n in self.field_names)

    @property
    def unresolved_fields(self) -> tuple[str, ...]:
        return tuple(n for n in self.field_names if not self.get_field(n).resolved)

    @property
    def code_value(self) -> str:
        """คืนค่า code (หรือ '' ถ้า unresolved)."""
        return self.code.value if self.code.resolved else ""


# ── ExtractionInput ──────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RowInput:
    """Input หนึ่งแถว (row) ที่ประกอบจาก TableCell หรือ text line.

    field mapping ถูกจัดไว้ตาม column index ของตาราง:
    col 1=code, 2=name_th, 3=name_en, 4=credits, 5=category, 6=type,
    7=prerequisite, 8=flexible_year_semester, 9=note
    (year/semester มาจาก table context)
    """

    cells: tuple[TableCell, ...]
    document_id: str
    page: int
    year: int | None = None
    semester: int | None = None
    year_provenance: Provenance | None = None
    semester_provenance: Provenance | None = None


# ── column mapping (สำหรับตารางแผนการศึกษา) ───────────────────────────

# ลำดับคอลัมน์มาตรฐาน (from design / GT schema)
_COLUMN_MAP: dict[str, int] = {
    "code": 1,
    "name_th": 2,
    "name_en": 3,
    "credits": 4,
    "category": 5,
    "type": 6,
    "prerequisite": 7,
    "flexible_year_semester": 8,
    "note": 9,
}


# ── FieldExtractor ────────────────────────────────────────────────────


class FieldExtractor:
    """สกัด 11 field ของรายวิชาจาก TableCell records.

    ใช้ ValueSets จาก config เพื่อตรวจค่า category/type ว่าอยู่ในชุดค่าปิด
    """

    def __init__(self, value_sets: ValueSets) -> None:
        self._valid_categories: frozenset[str] = value_sets.course_category
        self._valid_types: frozenset[str] = value_sets.course_type
        self._category_synonym = value_sets.category_synonym

    def extract_from_row(self, row: RowInput) -> tuple[CourseRecord, list[ReviewIssue]]:
        """สกัด CourseRecord จากหนึ่งแถวของตารางแผนการศึกษา.

        Returns
        -------
        (CourseRecord, list[ReviewIssue])
            course record ที่อาจมี field บาง field เป็น unresolved +
            รายการ review_issue ที่ต้องบันทึก
        """
        issues: list[ReviewIssue] = []
        cells_by_col: dict[int, TableCell] = {c.col_index: c for c in row.cells}

        # ── helper: สร้าง Provenance จาก TableCell ─────────────────────
        def _prov(cell: TableCell) -> Provenance:
            return Provenance(
                document_id=row.document_id,
                page=cell.page,
                bbox=cell.bbox,
                span=(0, len(cell.text)),
                extraction_method="table_cell",
            )

        # ── helper: extract string field with length validation ────────
        def _string_field(
            col: int, min_len: int, max_len: int, field_name: str,
        ) -> FieldValue:
            cell = cells_by_col.get(col)
            if cell is None:
                issues.append(_field_unresolved_issue(
                    row.document_id, row.page,
                    self._code_from_cells(cells_by_col),
                    field_name, reason="source_not_found", values=(),
                ))
                return FieldValue.unresolved_field()
            text = cell.text.strip()
            if min_len > 0 and len(text) < min_len:
                issues.append(_field_unresolved_issue(
                    row.document_id, row.page,
                    self._code_from_cells(cells_by_col),
                    field_name, reason="value_too_short", values=(text,),
                ))
                return FieldValue.unresolved_field()
            if len(text) > max_len:
                text = text[:max_len]
            return FieldValue.resolved_field(text, _prov(cell))

        # ── code (string 1-20) ─────────────────────────────────────────
        code_fv = _string_field(_COLUMN_MAP["code"], 1, 20, "code")

        # ── name_th (string 0-255) ─────────────────────────────────────
        name_th_fv = _string_field(_COLUMN_MAP["name_th"], 0, 255, "name_th")

        # ── name_en (string 0-255) ─────────────────────────────────────
        name_en_fv = _string_field(_COLUMN_MAP["name_en"], 0, 255, "name_en")

        # ── credits (Credits structure via parse_credits) ──────────────
        credits_fv = self._extract_credits(cells_by_col, row, issues)

        # ── year (int 1-8 from table context) ──────────────────────────
        year_fv = self._extract_year(row, cells_by_col, issues)

        # ── semester (int 1-3 from table context) ──────────────────────
        semester_fv = self._extract_semester(row, cells_by_col, issues)

        # ── category (closed set from config) ──────────────────────────
        category_fv = self._extract_category(cells_by_col, row, issues)

        # ── type (closed set from config) ──────────────────────────────
        type_fv = self._extract_type(cells_by_col, row, issues)

        # ── prerequisite (PrereqNode via parse_prerequisite) ───────────
        prereq_fv = self._extract_prerequisite(cells_by_col, row, issues)

        # ── flexible_year_semester (bool) ──────────────────────────────
        flex_fv = self._extract_flexible(cells_by_col, row, issues)

        # ── note (string 0-500) ────────────────────────────────────────
        note_fv = _string_field(_COLUMN_MAP["note"], 0, 500, "note")

        record = CourseRecord(
            code=code_fv,
            name_th=name_th_fv,
            name_en=name_en_fv,
            credits=credits_fv,
            year=year_fv,
            semester=semester_fv,
            category=category_fv,
            type=type_fv,
            prerequisite=prereq_fv,
            flexible_year_semester=flex_fv,
            note=note_fv,
            document_id=row.document_id,
            page=row.page,
        )
        return record, issues

    # ── private extraction methods ────────────────────────────────────

    def _extract_credits(
        self,
        cells_by_col: dict[int, TableCell],
        row: RowInput,
        issues: list[ReviewIssue],
    ) -> FieldValue:
        col = _COLUMN_MAP["credits"]
        cell = cells_by_col.get(col)
        if cell is None:
            issues.append(_field_unresolved_issue(
                row.document_id, row.page,
                self._code_from_cells(cells_by_col),
                "credits", reason="source_not_found", values=(),
            ))
            return FieldValue.unresolved_field()

        raw_text = cell.text.strip()
        if not raw_text:
            # Empty credits cell — treat as unresolved
            issues.append(_field_unresolved_issue(
                row.document_id, row.page,
                self._code_from_cells(cells_by_col),
                "credits", reason="empty_value", values=(),
            ))
            return FieldValue.unresolved_field()

        result = parse_credits(raw_text)
        if isinstance(result, ParseFailure):
            # R8.3: บันทึกค่า credits เป็นค่าว่าง + review_issue credits_parse_error
            issues.append(ReviewIssue(
                kind="credits_parse_error",
                document_id=row.document_id,
                page=cell.page,
                detail={
                    "raw_text": result.raw_text,
                    "error_index": result.error_index,
                    "bbox": cell.bbox.as_tuple(),
                },
            ))
            return FieldValue.unresolved_field()

        prov = Provenance(
            document_id=row.document_id,
            page=cell.page,
            bbox=cell.bbox,
            span=(0, len(raw_text)),
            extraction_method="table_cell",
        )
        return FieldValue.resolved_field(result, prov)

    def _extract_year(
        self,
        row: RowInput,
        cells_by_col: dict[int, TableCell],
        issues: list[ReviewIssue],
    ) -> FieldValue:
        if row.year is not None and 1 <= row.year <= 8:
            prov = row.year_provenance
            if prov is None:
                # Derive provenance from document context
                prov = Provenance(
                    document_id=row.document_id,
                    page=row.page,
                    bbox=BBox(0, 0, 1, 1),
                    span=(0, 0),
                    extraction_method="derived",
                )
            return FieldValue.resolved_field(row.year, prov)

        # Year not provided from table context — unresolved
        issues.append(_field_unresolved_issue(
            row.document_id, row.page,
            self._code_from_cells(cells_by_col),
            "year", reason="source_not_found", values=(),
        ))
        return FieldValue.unresolved_field()

    def _extract_semester(
        self,
        row: RowInput,
        cells_by_col: dict[int, TableCell],
        issues: list[ReviewIssue],
    ) -> FieldValue:
        if row.semester is not None and 1 <= row.semester <= 3:
            prov = row.semester_provenance
            if prov is None:
                prov = Provenance(
                    document_id=row.document_id,
                    page=row.page,
                    bbox=BBox(0, 0, 1, 1),
                    span=(0, 0),
                    extraction_method="derived",
                )
            return FieldValue.resolved_field(row.semester, prov)

        issues.append(_field_unresolved_issue(
            row.document_id, row.page,
            self._code_from_cells(cells_by_col),
            "semester", reason="source_not_found", values=(),
        ))
        return FieldValue.unresolved_field()

    def _extract_category(
        self,
        cells_by_col: dict[int, TableCell],
        row: RowInput,
        issues: list[ReviewIssue],
    ) -> FieldValue:
        col = _COLUMN_MAP["category"]
        cell = cells_by_col.get(col)
        if cell is None:
            issues.append(_field_unresolved_issue(
                row.document_id, row.page,
                self._code_from_cells(cells_by_col),
                "category", reason="source_not_found", values=(),
            ))
            return FieldValue.unresolved_field()

        raw = cell.text.strip()
        if not raw:
            issues.append(_field_unresolved_issue(
                row.document_id, row.page,
                self._code_from_cells(cells_by_col),
                "category", reason="empty_value", values=(),
            ))
            return FieldValue.unresolved_field()

        # Apply synonym mapping before validation
        canonical = self._category_synonym.get(raw, raw)

        if canonical not in self._valid_categories:
            issues.append(_field_unresolved_issue(
                row.document_id, row.page,
                self._code_from_cells(cells_by_col),
                "category", reason="value_not_in_closed_set",
                values=(raw,),
            ))
            return FieldValue.unresolved_field()

        prov = Provenance(
            document_id=row.document_id,
            page=cell.page,
            bbox=cell.bbox,
            span=(0, len(cell.text)),
            extraction_method="table_cell",
        )
        return FieldValue.resolved_field(canonical, prov)

    def _extract_type(
        self,
        cells_by_col: dict[int, TableCell],
        row: RowInput,
        issues: list[ReviewIssue],
    ) -> FieldValue:
        col = _COLUMN_MAP["type"]
        cell = cells_by_col.get(col)
        if cell is None:
            issues.append(_field_unresolved_issue(
                row.document_id, row.page,
                self._code_from_cells(cells_by_col),
                "type", reason="source_not_found", values=(),
            ))
            return FieldValue.unresolved_field()

        raw = cell.text.strip()
        if not raw:
            issues.append(_field_unresolved_issue(
                row.document_id, row.page,
                self._code_from_cells(cells_by_col),
                "type", reason="empty_value", values=(),
            ))
            return FieldValue.unresolved_field()

        if raw not in self._valid_types:
            issues.append(_field_unresolved_issue(
                row.document_id, row.page,
                self._code_from_cells(cells_by_col),
                "type", reason="value_not_in_closed_set",
                values=(raw,),
            ))
            return FieldValue.unresolved_field()

        prov = Provenance(
            document_id=row.document_id,
            page=cell.page,
            bbox=cell.bbox,
            span=(0, len(cell.text)),
            extraction_method="table_cell",
        )
        return FieldValue.resolved_field(raw, prov)

    def _extract_prerequisite(
        self,
        cells_by_col: dict[int, TableCell],
        row: RowInput,
        issues: list[ReviewIssue],
    ) -> FieldValue:
        col = _COLUMN_MAP["prerequisite"]
        cell = cells_by_col.get(col)
        if cell is None:
            issues.append(_field_unresolved_issue(
                row.document_id, row.page,
                self._code_from_cells(cells_by_col),
                "prerequisite", reason="source_not_found", values=(),
            ))
            return FieldValue.unresolved_field()

        raw_text = cell.text.strip()
        result = parse_prerequisite(raw_text)

        if isinstance(result, ParseFailure):
            # R8.9: บันทึกค่า prerequisite เป็นค่าว่าง + review_issue
            issues.append(ReviewIssue(
                kind="prerequisite_parse_error",
                document_id=row.document_id,
                page=cell.page,
                detail={
                    "raw_text": result.raw_text,
                    "error_index": result.error_index,
                    "bbox": cell.bbox.as_tuple(),
                },
            ))
            return FieldValue.unresolved_field()

        prov = Provenance(
            document_id=row.document_id,
            page=cell.page,
            bbox=cell.bbox,
            span=(0, len(raw_text)),
            extraction_method="table_cell",
        )
        return FieldValue.resolved_field(result, prov)

    def _extract_flexible(
        self,
        cells_by_col: dict[int, TableCell],
        row: RowInput,
        issues: list[ReviewIssue],
    ) -> FieldValue:
        col = _COLUMN_MAP["flexible_year_semester"]
        cell = cells_by_col.get(col)
        if cell is None:
            issues.append(_field_unresolved_issue(
                row.document_id, row.page,
                self._code_from_cells(cells_by_col),
                "flexible_year_semester", reason="source_not_found", values=(),
            ))
            return FieldValue.unresolved_field()

        raw = cell.text.strip().lower()
        # Interpret as boolean: common values from GT
        if raw in ("true", "yes", "1", "ใช่", "จริง"):
            value = True
        elif raw in ("false", "no", "0", "ไม่", "ไม่ใช่", "เท็จ", ""):
            value = False
        else:
            # Cannot determine boolean value — treat as unresolved
            issues.append(_field_unresolved_issue(
                row.document_id, row.page,
                self._code_from_cells(cells_by_col),
                "flexible_year_semester",
                reason="cannot_interpret_as_boolean",
                values=(cell.text.strip(),),
            ))
            return FieldValue.unresolved_field()

        prov = Provenance(
            document_id=row.document_id,
            page=cell.page,
            bbox=cell.bbox,
            span=(0, len(cell.text)),
            extraction_method="table_cell",
        )
        return FieldValue.resolved_field(value, prov)

    @staticmethod
    def _code_from_cells(cells_by_col: dict[int, TableCell]) -> str:
        """พยายามดึง code จาก column 1 เพื่อใส่ใน review_issue."""
        cell = cells_by_col.get(_COLUMN_MAP["code"])
        if cell is not None:
            return cell.text.strip()
        return ""

    # ── batch extraction ──────────────────────────────────────────────

    def extract_from_rows(
        self, rows: Sequence[RowInput],
    ) -> tuple[list[CourseRecord], list[ReviewIssue]]:
        """สกัด CourseRecord จากหลายแถว.

        Returns
        -------
        (list[CourseRecord], list[ReviewIssue])
        """
        all_records: list[CourseRecord] = []
        all_issues: list[ReviewIssue] = []
        for row in rows:
            record, issues = self.extract_from_row(row)
            all_records.append(record)
            all_issues.extend(issues)
        return all_records, all_issues


# ── helper: สร้าง field_unresolved ReviewIssue (R8.10) ────────────────


def _field_unresolved_issue(
    document_id: str,
    page: int,
    course_code: str,
    field_name: str,
    *,
    reason: str,
    values: tuple[str, ...],
) -> ReviewIssue:
    """สร้าง review_issue ชนิด ``field_unresolved`` ตาม R8.10.

    detail ประกอบด้วย:
    - course_code: รหัสวิชา (ถ้ามี)
    - field: ชื่อ field ที่ unresolved
    - reason: เหตุผล
    - found_values: ค่าที่พบทั้งหมด (อาจว่างถ้าหาไม่เจอ)
    """
    return ReviewIssue(
        kind="field_unresolved",
        document_id=document_id,
        page=page if page >= 1 else None,
        detail={
            "course_code": course_code,
            "field": field_name,
            "reason": reason,
            "found_values": list(values),
        },
    )
