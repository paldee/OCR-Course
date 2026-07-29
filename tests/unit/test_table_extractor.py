"""Unit tests for katrag.ingest.table_extractor (R7.1–R7.6)."""

from __future__ import annotations

import pytest

from katrag.common.types import BBox, PageCharSet, TextLine
from katrag.ingest.table_extractor import (
    AssembledLine,
    ContextResolution,
    DetectedTable,
    TableCell,
    TableExtractor,
)


# ── Helpers ───────────────────────────────────────────────────────────


def _make_line(text: str, order: int = 0, y0: float = 0.0, y1: float = 12.0) -> TextLine:
    """สร้าง TextLine สำหรับ test."""
    return TextLine(
        text=text,
        bbox=BBox(0.0, y0, 500.0, y1),
        baseline=y0 + 10.0,
        order=order,
    )


def _make_page(document_id: str = "doc1", page: int = 1) -> PageCharSet:
    """สร้าง minimal PageCharSet สำหรับ test."""
    return PageCharSet(
        document_id=document_id,
        page=page,
        width_pt=595.0,
        height_pt=842.0,
        chars=(),
        image_count=0,
        image_area_ratio=0.0,
    )


# ── TableCell validation ──────────────────────────────────────────────


class TestTableCell:
    """R7.1: cell record ต้องมี row/col index เริ่มจาก 1."""

    def test_valid_cell(self) -> None:
        cell = TableCell(
            table_index=0,
            row_index=1,
            col_index=1,
            row_span=1,
            col_span=1,
            text="hello",
            bbox=BBox(0.0, 0.0, 100.0, 20.0),
            document_id="doc1",
            page=1,
        )
        assert cell.text == "hello"
        assert cell.row_index == 1
        assert cell.col_index == 1

    def test_empty_text_cell(self) -> None:
        """R7.2: เซลล์ว่างบันทึกเป็น text=''."""
        cell = TableCell(
            table_index=0,
            row_index=2,
            col_index=3,
            row_span=1,
            col_span=1,
            text="",
            bbox=BBox(0.0, 0.0, 50.0, 12.0),
            document_id="doc1",
            page=1,
        )
        assert cell.text == ""

    def test_row_index_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="row_index"):
            TableCell(
                table_index=0,
                row_index=0,
                col_index=1,
                row_span=1,
                col_span=1,
                text="x",
                bbox=BBox(0, 0, 1, 1),
                document_id="d",
                page=1,
            )

    def test_col_index_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="col_index"):
            TableCell(
                table_index=0,
                row_index=1,
                col_index=0,
                row_span=1,
                col_span=1,
                text="x",
                bbox=BBox(0, 0, 1, 1),
                document_id="d",
                page=1,
            )

    def test_row_span_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="row_span"):
            TableCell(
                table_index=0,
                row_index=1,
                col_index=1,
                row_span=0,
                col_span=1,
                text="x",
                bbox=BBox(0, 0, 1, 1),
                document_id="d",
                page=1,
            )

    def test_col_span_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="col_span"):
            TableCell(
                table_index=0,
                row_index=1,
                col_index=1,
                row_span=1,
                col_span=0,
                text="x",
                bbox=BBox(0, 0, 1, 1),
                document_id="d",
                page=1,
            )

    def test_page_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="page"):
            TableCell(
                table_index=0,
                row_index=1,
                col_index=1,
                row_span=1,
                col_span=1,
                text="x",
                bbox=BBox(0, 0, 1, 1),
                document_id="d",
                page=0,
            )

    def test_span_greater_than_one(self) -> None:
        """R7.6: span >= 1."""
        cell = TableCell(
            table_index=0,
            row_index=1,
            col_index=1,
            row_span=2,
            col_span=3,
            text="merged",
            bbox=BBox(0, 0, 300, 40),
            document_id="doc1",
            page=1,
        )
        assert cell.row_span == 2
        assert cell.col_span == 3


# ── DetectedTable validation ──────────────────────────────────────────


class TestDetectedTable:
    """R7.1: ตารางต้องมี header >= 1, column >= 2."""

    def test_valid_table(self) -> None:
        cells = (
            TableCell(0, 1, 1, 1, 1, "A", BBox(0, 0, 50, 12), "doc1", 1),
            TableCell(0, 1, 2, 1, 1, "B", BBox(50, 0, 100, 12), "doc1", 1),
            TableCell(0, 2, 1, 1, 1, "1", BBox(0, 12, 50, 24), "doc1", 1),
            TableCell(0, 2, 2, 1, 1, "2", BBox(50, 12, 100, 24), "doc1", 1),
        )
        table = DetectedTable(
            table_index=0,
            header_rows=1,
            column_count=2,
            cells=cells,
            plan_year=None,
            plan_semester=None,
            context_provenance=None,
        )
        assert table.header_rows == 1
        assert table.column_count == 2
        assert len(table.cells) == 4

    def test_header_rows_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="header_rows"):
            DetectedTable(
                table_index=0,
                header_rows=0,
                column_count=2,
                cells=(),
                plan_year=None,
                plan_semester=None,
                context_provenance=None,
            )

    def test_column_count_one_raises(self) -> None:
        with pytest.raises(ValueError, match="column_count"):
            DetectedTable(
                table_index=0,
                header_rows=1,
                column_count=1,
                cells=(),
                plan_year=None,
                plan_semester=None,
                context_provenance=None,
            )

    def test_plan_year_out_of_range_raises(self) -> None:
        with pytest.raises(ValueError, match="plan_year"):
            DetectedTable(
                table_index=0,
                header_rows=1,
                column_count=2,
                cells=(),
                plan_year=9,
                plan_semester=1,
                context_provenance=None,
            )

    def test_plan_semester_out_of_range_raises(self) -> None:
        with pytest.raises(ValueError, match="plan_semester"):
            DetectedTable(
                table_index=0,
                header_rows=1,
                column_count=2,
                cells=(),
                plan_year=1,
                plan_semester=4,
                context_provenance=None,
            )


# ── TableExtractor.extract ────────────────────────────────────────────


class TestTableExtractorExtract:
    """R7.1: ตรวจจับตารางและผลิต cell record."""

    def test_simple_two_column_table(self) -> None:
        """ตารางง่าย 2 คอลัมน์ — header + 1 data row."""
        lines = [
            _make_line("รหัสวิชา\tชื่อวิชา", order=0, y0=0, y1=12),
            _make_line("CS101\tIntro to CS", order=1, y0=12, y1=24),
        ]
        page = _make_page()
        extractor = TableExtractor()
        tables = extractor.extract(page, lines, ocr=[])

        assert len(tables) == 1
        table = tables[0]
        assert table.column_count == 2
        assert table.header_rows == 1
        assert len(table.cells) >= 2  # at least header cells

    def test_no_table_in_plain_text(self) -> None:
        """ข้อความธรรมดาไม่มีตาราง."""
        lines = [
            _make_line("This is a paragraph of text.", order=0),
            _make_line("Another paragraph.", order=1),
        ]
        page = _make_page()
        extractor = TableExtractor()
        tables = extractor.extract(page, lines, ocr=[])

        assert len(tables) == 0

    def test_empty_cells_recorded(self) -> None:
        """R7.2: เซลล์ว่างต้องถูกบันทึกเป็น cell ที่ text=''."""
        lines = [
            _make_line("รหัสวิชา\tชื่อวิชา\tหน่วยกิต", order=0, y0=0, y1=12),
            _make_line("CS101\t\tIntro", order=1, y0=12, y1=24),
        ]
        page = _make_page()
        extractor = TableExtractor()
        tables = extractor.extract(page, lines, ocr=[])

        assert len(tables) == 1
        # Check that row 2 has cells for all 3 columns
        row2_cells = [c for c in tables[0].cells if c.row_index == 2]
        assert len(row2_cells) == 3
        # One of them should be empty text
        empty_cells = [c for c in row2_cells if c.text == ""]
        assert len(empty_cells) >= 1

    def test_cells_have_document_id_and_page(self) -> None:
        """R7.1: cell record ต้องมี document_id และเลขหน้า."""
        lines = [
            _make_line("รหัสวิชา\tชื่อวิชา", order=0, y0=0, y1=12),
            _make_line("CS101\tIntro", order=1, y0=12, y1=24),
        ]
        page = _make_page(document_id="mydoc", page=5)
        extractor = TableExtractor()
        tables = extractor.extract(page, lines, ocr=[])

        assert len(tables) == 1
        for cell in tables[0].cells:
            assert cell.document_id == "mydoc"
            assert cell.page == 5

    def test_cells_have_bbox(self) -> None:
        """R7.1: cell record ต้องมี bbox."""
        lines = [
            _make_line("รหัสวิชา\tชื่อวิชา", order=0, y0=0, y1=12),
            _make_line("CS101\tIntro", order=1, y0=12, y1=24),
        ]
        page = _make_page()
        extractor = TableExtractor()
        tables = extractor.extract(page, lines, ocr=[])

        for cell in tables[0].cells:
            assert cell.bbox.is_valid()

    def test_row_col_index_start_at_one(self) -> None:
        """R7.1: row/col index เริ่มที่ 1."""
        lines = [
            _make_line("รหัสวิชา\tชื่อวิชา", order=0, y0=0, y1=12),
            _make_line("CS101\tIntro", order=1, y0=12, y1=24),
        ]
        page = _make_page()
        extractor = TableExtractor()
        tables = extractor.extract(page, lines, ocr=[])

        all_rows = {c.row_index for c in tables[0].cells}
        all_cols = {c.col_index for c in tables[0].cells}
        assert min(all_rows) == 1
        assert min(all_cols) == 1


# ── TableExtractor.resolve_plan_context ───────────────────────────────


class TestResolvePlanContext:
    """R7.3, R7.4: ระบุปี/ภาคการศึกษาของตาราง."""

    def _make_table(self) -> DetectedTable:
        cells = (
            TableCell(0, 1, 1, 1, 1, "A", BBox(0, 0, 50, 12), "doc1", 1),
            TableCell(0, 1, 2, 1, 1, "B", BBox(50, 0, 100, 12), "doc1", 1),
        )
        return DetectedTable(
            table_index=0,
            header_rows=1,
            column_count=2,
            cells=cells,
            plan_year=None,
            plan_semester=None,
            context_provenance=None,
        )

    def test_resolve_year_and_semester(self) -> None:
        """R7.3: ระบุปี + ภาคการศึกษาได้."""
        lines = [
            _make_line("ชั้นปีที่ 2 ภาคการศึกษาที่ 1", order=0),
        ]
        table = self._make_table()
        extractor = TableExtractor()
        result = extractor.resolve_plan_context(table, lines)

        assert result.resolved is True
        assert result.plan_year == 2
        assert result.plan_semester == 1
        assert result.provenance is not None

    def test_unresolved_no_year(self) -> None:
        """R7.4: ไม่พบ year → table_context_unresolved."""
        lines = [
            _make_line("ภาคการศึกษาที่ 1", order=0),
        ]
        table = self._make_table()
        extractor = TableExtractor()
        result = extractor.resolve_plan_context(table, lines)

        assert result.resolved is False
        assert result.plan_year is None
        assert result.review_issue is not None
        assert result.review_issue.kind == "table_context_unresolved"

    def test_unresolved_no_semester(self) -> None:
        """R7.4: ไม่พบ semester → table_context_unresolved."""
        lines = [
            _make_line("ชั้นปีที่ 3", order=0),
        ]
        table = self._make_table()
        extractor = TableExtractor()
        result = extractor.resolve_plan_context(table, lines)

        assert result.resolved is False
        assert result.plan_semester is None
        assert result.review_issue is not None
        assert result.review_issue.kind == "table_context_unresolved"

    def test_unresolved_conflicting_years(self) -> None:
        """R7.4: พบหลายค่า year ที่ขัดแย้ง."""
        lines = [
            _make_line("ชั้นปีที่ 2", order=0),
            _make_line("ชั้นปีที่ 3", order=1),
            _make_line("ภาคการศึกษาที่ 1", order=2),
        ]
        table = self._make_table()
        extractor = TableExtractor()
        result = extractor.resolve_plan_context(table, lines)

        assert result.resolved is False
        assert result.review_issue is not None
        assert result.review_issue.kind == "table_context_unresolved"

    def test_resolved_keeps_cells(self) -> None:
        """R7.4: เมื่อ unresolved ยังคง cell record ทั้งตาราง."""
        lines = [_make_line("no context here", order=0)]
        table = self._make_table()
        extractor = TableExtractor()
        result = extractor.resolve_plan_context(table, lines)

        # table cells are unchanged regardless of context resolution
        assert len(table.cells) == 2
        assert result.resolved is False


# ── TableExtractor.validate_shape ─────────────────────────────────────


class TestValidateShape:
    """R7.5: table_shape_mismatch."""

    def test_no_mismatch(self) -> None:
        """ตารางปกติไม่มี mismatch."""
        cells = (
            TableCell(0, 1, 1, 1, 1, "A", BBox(0, 0, 50, 12), "doc1", 1),
            TableCell(0, 1, 2, 1, 1, "B", BBox(50, 0, 100, 12), "doc1", 1),
            TableCell(0, 2, 1, 1, 1, "1", BBox(0, 12, 50, 24), "doc1", 1),
            TableCell(0, 2, 2, 1, 1, "2", BBox(50, 12, 100, 24), "doc1", 1),
        )
        table = DetectedTable(
            table_index=0, header_rows=1, column_count=2,
            cells=cells, plan_year=None, plan_semester=None,
            context_provenance=None,
        )
        extractor = TableExtractor()
        issues = extractor.validate_shape(table)
        assert issues == []

    def test_mismatch_detected(self) -> None:
        """R7.5: แถวที่มี cell ไม่ตรง column_count → issue."""
        cells = (
            TableCell(0, 1, 1, 1, 1, "A", BBox(0, 0, 50, 12), "doc1", 1),
            TableCell(0, 1, 2, 1, 1, "B", BBox(50, 0, 100, 12), "doc1", 1),
            # Row 2 has 3 cells but column_count is 2
            TableCell(0, 2, 1, 1, 1, "1", BBox(0, 12, 33, 24), "doc1", 1),
            TableCell(0, 2, 2, 1, 1, "2", BBox(33, 12, 66, 24), "doc1", 1),
            TableCell(0, 2, 3, 1, 1, "3", BBox(66, 12, 100, 24), "doc1", 1),
        )
        table = DetectedTable(
            table_index=0, header_rows=1, column_count=2,
            cells=cells, plan_year=None, plan_semester=None,
            context_provenance=None,
        )
        extractor = TableExtractor()
        issues = extractor.validate_shape(table)

        assert len(issues) == 1
        assert issues[0].kind == "table_shape_mismatch"
        assert issues[0].detail["row_index"] == 2
        assert issues[0].detail["expected_columns"] == 2
        assert issues[0].detail["actual_effective_columns"] == 3

    def test_mismatch_keeps_all_cells(self) -> None:
        """R7.5: shape mismatch คง cell record ทั้งตาราง."""
        cells = (
            TableCell(0, 1, 1, 1, 1, "A", BBox(0, 0, 50, 12), "doc1", 1),
            TableCell(0, 1, 2, 1, 1, "B", BBox(50, 0, 100, 12), "doc1", 1),
            TableCell(0, 2, 1, 1, 1, "1", BBox(0, 12, 50, 24), "doc1", 1),
        )
        table = DetectedTable(
            table_index=0, header_rows=1, column_count=2,
            cells=cells, plan_year=None, plan_semester=None,
            context_provenance=None,
        )
        extractor = TableExtractor()
        issues = extractor.validate_shape(table)

        # Issues reported but cells remain
        assert len(issues) >= 1
        assert len(table.cells) == 3  # All cells preserved

    def test_col_span_counted(self) -> None:
        """R7.5: col_span นับรวมในจำนวน effective columns."""
        cells = (
            TableCell(0, 1, 1, 1, 1, "A", BBox(0, 0, 50, 12), "doc1", 1),
            TableCell(0, 1, 2, 1, 1, "B", BBox(50, 0, 100, 12), "doc1", 1),
            # Row 2: one cell with col_span=2 → effective = 2 = column_count OK
            TableCell(0, 2, 1, 1, 2, "merged", BBox(0, 12, 100, 24), "doc1", 1),
        )
        table = DetectedTable(
            table_index=0, header_rows=1, column_count=2,
            cells=cells, plan_year=None, plan_semester=None,
            context_provenance=None,
        )
        extractor = TableExtractor()
        issues = extractor.validate_shape(table)
        assert issues == []


# ── Span recording (R7.6) ─────────────────────────────────────────────


class TestSpanRecording:
    """R7.6: span บันทึกที่ตำแหน่ง (min row, min col) ไม่สร้างซ้ำ."""

    def test_span_at_min_position(self) -> None:
        """Cell ที่ span อยู่ที่ row/col index น้อยสุด."""
        cell = TableCell(
            table_index=0,
            row_index=1,
            col_index=1,
            row_span=2,
            col_span=2,
            text="merged",
            bbox=BBox(0, 0, 100, 40),
            document_id="doc1",
            page=1,
        )
        assert cell.row_index == 1
        assert cell.col_index == 1
        assert cell.row_span == 2
        assert cell.col_span == 2

    def test_no_duplicate_cells_in_span_range(self) -> None:
        """R7.6: ไม่สร้าง cell ซ้ำสำหรับตำแหน่งในช่วง span."""
        # A 2x2 table where cell (1,1) spans 2 rows
        # Expected: only 3 cells total, not 4
        cells = (
            TableCell(0, 1, 1, 2, 1, "merged", BBox(0, 0, 50, 24), "doc1", 1),
            TableCell(0, 1, 2, 1, 1, "B", BBox(50, 0, 100, 12), "doc1", 1),
            TableCell(0, 2, 2, 1, 1, "D", BBox(50, 12, 100, 24), "doc1", 1),
        )
        # Verify no cell at (2, 1) since it's covered by the span
        positions = {(c.row_index, c.col_index) for c in cells}
        assert (2, 1) not in positions
