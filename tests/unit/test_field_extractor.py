"""Unit tests for katrag.ingest.fields.extractor (R8.1, R8.7, R8.10)."""

from __future__ import annotations

import pytest

from katrag.common.types import BBox, Credits, PrereqEmpty, PrereqLeaf, Provenance
from katrag.config import ValueSets
from katrag.errors import ReviewIssue
from katrag.ingest.fields.extractor import (
    CourseRecord,
    FieldExtractor,
    FieldValue,
    RowInput,
    _field_unresolved_issue,
)
from katrag.ingest.table_extractor import TableCell


# ── test fixtures ─────────────────────────────────────────────────────


@pytest.fixture()
def value_sets() -> ValueSets:
    """Minimal ValueSets for testing field extractor."""
    return ValueSets(
        course_category=frozenset(["หมวดวิชาศึกษาทั่วไป", "หมวดวิชาเฉพาะ", "หมวดวิชาเลือกเสรี"]),
        course_type=frozenset(["บังคับ", "เลือก", "สหกิจศึกษา", "โครงงาน", "ฝึกงาน"]),
        extraction_method=frozenset(["text_layer", "table_cell", "derived"]),
        provenance_source=frozenset(["document_text"]),
        edition_status=frozenset(["old", "current"]),
        degree_level=frozenset(["bachelor"]),
        compute_path=frozenset(["fast", "standard", "deep"]),
        question_level=frozenset(["L1", "L2", "L3", "L4"]),
        metric_status=frozenset(["measured", "estimate"]),
        reference_source=frozenset(["teacher_ground_truth"]),
        page_status=frozenset(["page_pending", "page_complete"]),
        review_issue_kind=frozenset(["field_unresolved", "credits_parse_error", "prerequisite_parse_error"]),
        halt_reason=frozenset(["oscillation"]),
        category_synonym={"หมวดวิชาเสรี": "หมวดวิชาเลือกเสรี"},
    )


@pytest.fixture()
def extractor(value_sets: ValueSets) -> FieldExtractor:
    return FieldExtractor(value_sets)


def _make_cell(col: int, text: str, *, row: int = 2, page: int = 10) -> TableCell:
    """Helper to create TableCell with minimal valid data."""
    return TableCell(
        table_index=1,
        row_index=row,
        col_index=col,
        row_span=1,
        col_span=1,
        text=text,
        bbox=BBox(x0=10.0, y0=20.0, x1=100.0, y1=40.0),
        document_id="doc_test",
        page=page,
    )


def _full_row(
    *,
    code: str = "06016101",
    name_th: str = "โครงสร้างข้อมูล",
    name_en: str = "Data Structures",
    credits: str = "3(3-0-6)",
    category: str = "หมวดวิชาเฉพาะ",
    type_val: str = "บังคับ",
    prerequisite: str = "ไม่มี",
    flexible: str = "false",
    note: str = "",
    year: int | None = 2,
    semester: int | None = 1,
) -> RowInput:
    """Create a complete RowInput with all 9 columns populated."""
    cells = (
        _make_cell(1, code),
        _make_cell(2, name_th),
        _make_cell(3, name_en),
        _make_cell(4, credits),
        _make_cell(5, category),
        _make_cell(6, type_val),
        _make_cell(7, prerequisite),
        _make_cell(8, flexible),
        _make_cell(9, note),
    )
    year_prov = Provenance(
        document_id="doc_test", page=10,
        bbox=BBox(0, 0, 50, 20), span=(0, 5),
        extraction_method="table_cell",
    )
    sem_prov = Provenance(
        document_id="doc_test", page=10,
        bbox=BBox(50, 0, 100, 20), span=(0, 3),
        extraction_method="table_cell",
    )
    return RowInput(
        cells=cells,
        document_id="doc_test",
        page=10,
        year=year,
        semester=semester,
        year_provenance=year_prov if year else None,
        semester_provenance=sem_prov if semester else None,
    )


# ── R8.1: CourseRecord has 11 fields ──────────────────────────────────


class TestCourseRecordHas11Fields:
    """R8.1: CourseRecord ต้องมี 11 field ครบ."""

    def test_full_extraction_produces_11_fields(self, extractor: FieldExtractor) -> None:
        row = _full_row()
        record, issues = extractor.extract_from_row(row)
        assert len(record.field_names) == 11
        assert record.all_resolved
        assert issues == []

    def test_field_names_match_spec(self, extractor: FieldExtractor) -> None:
        row = _full_row()
        record, _ = extractor.extract_from_row(row)
        expected = (
            "code", "name_th", "name_en", "credits", "year",
            "semester", "category", "type", "prerequisite",
            "flexible_year_semester", "note",
        )
        assert record.field_names == expected


# ── R8.1: field type and range validation ─────────────────────────────


class TestFieldTypes:
    """R8.1: ตรวจชนิดและช่วงค่าของแต่ละ field."""

    def test_code_is_string_1_to_20(self, extractor: FieldExtractor) -> None:
        row = _full_row(code="06016101")
        record, _ = extractor.extract_from_row(row)
        assert record.code.resolved
        assert isinstance(record.code.value, str)
        assert 1 <= len(record.code.value) <= 20

    def test_name_th_is_string_0_to_255(self, extractor: FieldExtractor) -> None:
        row = _full_row(name_th="โครงสร้างข้อมูล")
        record, _ = extractor.extract_from_row(row)
        assert record.name_th.resolved
        assert isinstance(record.name_th.value, str)
        assert 0 <= len(record.name_th.value) <= 255

    def test_year_is_int_1_to_8(self, extractor: FieldExtractor) -> None:
        row = _full_row(year=4)
        record, _ = extractor.extract_from_row(row)
        assert record.year.resolved
        assert isinstance(record.year.value, int)
        assert 1 <= record.year.value <= 8

    def test_semester_is_int_1_to_3(self, extractor: FieldExtractor) -> None:
        row = _full_row(semester=2)
        record, _ = extractor.extract_from_row(row)
        assert record.semester.resolved
        assert isinstance(record.semester.value, int)
        assert 1 <= record.semester.value <= 3

    def test_category_from_closed_set(self, extractor: FieldExtractor) -> None:
        row = _full_row(category="หมวดวิชาศึกษาทั่วไป")
        record, _ = extractor.extract_from_row(row)
        assert record.category.resolved
        assert record.category.value == "หมวดวิชาศึกษาทั่วไป"

    def test_type_from_closed_set(self, extractor: FieldExtractor) -> None:
        row = _full_row(type_val="เลือก")
        record, _ = extractor.extract_from_row(row)
        assert record.type.resolved
        assert record.type.value == "เลือก"

    def test_credits_is_credits_structure(self, extractor: FieldExtractor) -> None:
        row = _full_row(credits="3(3-0-6)")
        record, _ = extractor.extract_from_row(row)
        assert record.credits.resolved
        assert isinstance(record.credits.value, Credits)
        assert record.credits.value == Credits(total=3, lecture=3, lab=0, self_study=6)

    def test_prerequisite_is_prereq_node(self, extractor: FieldExtractor) -> None:
        row = _full_row(prerequisite="06016101")
        record, _ = extractor.extract_from_row(row)
        assert record.prerequisite.resolved
        assert isinstance(record.prerequisite.value, PrereqLeaf)
        assert record.prerequisite.value.code == "06016101"

    def test_prerequisite_empty_keyword(self, extractor: FieldExtractor) -> None:
        row = _full_row(prerequisite="ไม่มี")
        record, _ = extractor.extract_from_row(row)
        assert record.prerequisite.resolved
        assert isinstance(record.prerequisite.value, PrereqEmpty)

    def test_flexible_year_semester_is_bool(self, extractor: FieldExtractor) -> None:
        row = _full_row(flexible="true")
        record, _ = extractor.extract_from_row(row)
        assert record.flexible_year_semester.resolved
        assert record.flexible_year_semester.value is True

    def test_note_is_string_0_to_500(self, extractor: FieldExtractor) -> None:
        row = _full_row(note="หมายเหตุ")
        record, _ = extractor.extract_from_row(row)
        assert record.note.resolved
        assert isinstance(record.note.value, str)
        assert 0 <= len(record.note.value) <= 500


# ── R8.1: category/type from closed set in config ─────────────────────


class TestClosedSetValidation:
    """category/type ต้องอยู่ในชุดค่าปิดจากไฟล์ตั้งค่า."""

    def test_invalid_category_becomes_unresolved(self, extractor: FieldExtractor) -> None:
        row = _full_row(category="หมวดไม่รู้จัก")
        record, issues = extractor.extract_from_row(row)
        assert not record.category.resolved
        assert any(i.kind == "field_unresolved" and i.detail["field"] == "category" for i in issues)

    def test_invalid_type_becomes_unresolved(self, extractor: FieldExtractor) -> None:
        row = _full_row(type_val="ไม่รู้จัก")
        record, issues = extractor.extract_from_row(row)
        assert not record.type.resolved
        assert any(i.kind == "field_unresolved" and i.detail["field"] == "type" for i in issues)

    def test_synonym_mapping_for_category(self, extractor: FieldExtractor) -> None:
        """R11.9: หมวดวิชาเสรี -> หมวดวิชาเลือกเสรี."""
        row = _full_row(category="หมวดวิชาเสรี")
        record, issues = extractor.extract_from_row(row)
        assert record.category.resolved
        assert record.category.value == "หมวดวิชาเลือกเสรี"
        assert issues == []


# ── R8.7: per-field provenance ────────────────────────────────────────


class TestProvenance:
    """R8.7: ทุก field ต้องมี provenance (document_id, page, bbox, span, extraction_method)."""

    def test_every_resolved_field_has_provenance(self, extractor: FieldExtractor) -> None:
        row = _full_row()
        record, _ = extractor.extract_from_row(row)
        for name in record.field_names:
            fv = record.get_field(name)
            assert fv.resolved, f"field {name} should be resolved"
            assert fv.provenance is not None, f"field {name} missing provenance"
            prov = fv.provenance
            assert prov.document_id, f"field {name} provenance missing document_id"
            assert prov.page >= 1, f"field {name} provenance page < 1"
            assert prov.bbox.is_valid(), f"field {name} provenance has invalid bbox"
            assert prov.extraction_method, f"field {name} provenance missing extraction_method"

    def test_provenance_has_correct_document_id(self, extractor: FieldExtractor) -> None:
        row = _full_row()
        record, _ = extractor.extract_from_row(row)
        for name in record.field_names:
            fv = record.get_field(name)
            assert fv.provenance is not None
            assert fv.provenance.document_id == "doc_test"

    def test_provenance_extraction_method_is_table_cell_or_derived(
        self, extractor: FieldExtractor,
    ) -> None:
        row = _full_row()
        record, _ = extractor.extract_from_row(row)
        valid_methods = {"table_cell", "derived"}
        for name in record.field_names:
            fv = record.get_field(name)
            assert fv.provenance is not None
            assert fv.provenance.extraction_method in valid_methods


# ── R8.10: field_unresolved behavior ─────────────────────────────────


class TestFieldUnresolved:
    """R8.10: เมื่อหาต้นทางไม่ได้ → คง record + field ว่าง + review_issue."""

    def test_missing_column_produces_unresolved(self, extractor: FieldExtractor) -> None:
        """ถ้าไม่มี cell สำหรับ column → field unresolved."""
        # Create row with only code column (missing all others)
        cells = (_make_cell(1, "06016101"),)
        row = RowInput(
            cells=cells, document_id="doc_test", page=10,
            year=2, semester=1,
            year_provenance=Provenance(
                document_id="doc_test", page=10,
                bbox=BBox(0, 0, 50, 20), span=(0, 5),
                extraction_method="table_cell",
            ),
            semester_provenance=Provenance(
                document_id="doc_test", page=10,
                bbox=BBox(50, 0, 100, 20), span=(0, 3),
                extraction_method="table_cell",
            ),
        )
        record, issues = extractor.extract_from_row(row)
        # code should resolve, others (name_th, name_en, etc.) should be unresolved
        assert record.code.resolved
        assert not record.name_th.resolved
        assert not record.credits.resolved
        # Record still exists (R8.10: คง course record)
        assert record.document_id == "doc_test"

    def test_unresolved_field_has_review_issue(self, extractor: FieldExtractor) -> None:
        """field_unresolved ต้องมี review_issue ชนิด field_unresolved."""
        cells = (_make_cell(1, "06016101"),)
        row = RowInput(
            cells=cells, document_id="doc_test", page=10,
            year=None, semester=None,
        )
        _, issues = extractor.extract_from_row(row)
        unresolved_issues = [i for i in issues if i.kind == "field_unresolved"]
        # Should have issues for: name_th, name_en, credits, year, semester,
        # category, type, prerequisite, flexible_year_semester, note
        assert len(unresolved_issues) >= 8

    def test_conflicting_year_becomes_unresolved(self, extractor: FieldExtractor) -> None:
        """year ที่อยู่นอกช่วง 1-8 → unresolved."""
        row = _full_row(year=0)
        record, issues = extractor.extract_from_row(row)
        assert not record.year.resolved
        assert any(
            i.kind == "field_unresolved" and i.detail["field"] == "year"
            for i in issues
        )

    def test_record_kept_with_other_fields_resolved(self, extractor: FieldExtractor) -> None:
        """R8.10: คง course record ไว้กับ field ที่สกัดได้ทั้งหมด."""
        row = _full_row(credits="invalid!!!")
        record, issues = extractor.extract_from_row(row)
        # credits should be unresolved
        assert not record.credits.resolved
        # But code, name_th etc. should still be resolved
        assert record.code.resolved
        assert record.name_th.resolved
        assert record.name_en.resolved

    def test_review_issue_contains_course_code(self, extractor: FieldExtractor) -> None:
        row = _full_row(category="ไม่ถูกต้อง")
        _, issues = extractor.extract_from_row(row)
        cat_issue = next(
            i for i in issues
            if i.kind == "field_unresolved" and i.detail["field"] == "category"
        )
        assert cat_issue.detail["course_code"] == "06016101"

    def test_review_issue_contains_found_values(self, extractor: FieldExtractor) -> None:
        row = _full_row(type_val="ไม่มีในชุด")
        _, issues = extractor.extract_from_row(row)
        type_issue = next(
            i for i in issues
            if i.kind == "field_unresolved" and i.detail["field"] == "type"
        )
        assert "ไม่มีในชุด" in type_issue.detail["found_values"]


# ── batch extraction ──────────────────────────────────────────────────


class TestBatchExtraction:
    def test_extract_multiple_rows(self, extractor: FieldExtractor) -> None:
        rows = [_full_row(code="06016101"), _full_row(code="06016102")]
        records, issues = extractor.extract_from_rows(rows)
        assert len(records) == 2
        assert records[0].code.value == "06016101"
        assert records[1].code.value == "06016102"
        assert issues == []


# ── edge cases ────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_note_is_valid(self, extractor: FieldExtractor) -> None:
        row = _full_row(note="")
        record, issues = extractor.extract_from_row(row)
        assert record.note.resolved
        assert record.note.value == ""
        assert issues == []

    def test_empty_name_en_is_valid(self, extractor: FieldExtractor) -> None:
        row = _full_row(name_en="")
        record, _ = extractor.extract_from_row(row)
        assert record.name_en.resolved
        assert record.name_en.value == ""

    def test_flexible_false_from_empty_string(self, extractor: FieldExtractor) -> None:
        row = _full_row(flexible="")
        record, _ = extractor.extract_from_row(row)
        assert record.flexible_year_semester.resolved
        assert record.flexible_year_semester.value is False

    def test_credits_parse_error_creates_specific_issue(self, extractor: FieldExtractor) -> None:
        row = _full_row(credits="abc")
        record, issues = extractor.extract_from_row(row)
        assert not record.credits.resolved
        credit_issues = [i for i in issues if i.kind == "credits_parse_error"]
        assert len(credit_issues) == 1
        assert credit_issues[0].detail["raw_text"] == "abc"
