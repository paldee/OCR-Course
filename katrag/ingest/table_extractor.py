"""Table_Extractor — ตรวจจับตารางและผลิต cell record (design §4.13, R7.1–R7.6).

หน้าที่หลัก:
1. ตรวจจับตาราง: header ≥ 1 แถว, คอลัมน์ ≥ 2, แถวข้อมูล ≥ 1 (R7.1)
2. ผลิต cell record ทุกเซลล์ รวมเซลล์ว่าง (R7.2)
3. ระบุปีการศึกษา (1–8) / ภาคการศึกษา (1–3) ของตารางแผนการศึกษา (R7.3)
4. table_context_unresolved เมื่อระบุ year/semester ไม่ได้ (R7.4)
5. table_shape_mismatch เมื่อ cell ต่อแถวไม่ตรง header (R7.5)
6. span บันทึกที่ (min row, min col) ไม่สร้างซ้ำในช่วงเดียวกัน (R7.6)
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Sequence

from katrag.common.types import BBox, PageCharSet, Provenance, TextLine
from katrag.errors import ReviewIssue
from katrag.ingest.ocr.cascade import RegionOutcome

# ── Type alias ────────────────────────────────────────────────────────
# Design uses "AssembledLine" which maps to TextLine in our codebase
AssembledLine = TextLine


# ── Data classes (R7.1, R7.6) ─────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TableCell:
    """หนึ่ง cell ในตาราง — ทุกฟิลด์ไม่เปลี่ยนหลังสร้าง."""

    table_index: int
    row_index: int          # เริ่มที่ 1
    col_index: int          # เริ่มที่ 1
    row_span: int           # >= 1
    col_span: int           # >= 1
    text: str               # "" ได้ (R7.2)
    bbox: BBox
    document_id: str
    page: int

    def __post_init__(self) -> None:
        if self.row_index < 1:
            raise ValueError("row_index ต้องเริ่มที่ 1")
        if self.col_index < 1:
            raise ValueError("col_index ต้องเริ่มที่ 1")
        if self.row_span < 1:
            raise ValueError("row_span ต้อง >= 1")
        if self.col_span < 1:
            raise ValueError("col_span ต้อง >= 1")
        if self.page < 1:
            raise ValueError("page ต้อง >= 1")


@dataclass(frozen=True, slots=True)
class DetectedTable:
    """ตารางที่ตรวจพบหนึ่งตาราง."""

    table_index: int
    header_rows: int                     # >= 1
    column_count: int                    # >= 2
    cells: tuple[TableCell, ...]
    plan_year: int | None                # 1..8
    plan_semester: int | None            # 1..3
    context_provenance: Provenance | None

    def __post_init__(self) -> None:
        if self.header_rows < 1:
            raise ValueError("header_rows ต้อง >= 1")
        if self.column_count < 2:
            raise ValueError("column_count ต้อง >= 2")
        if self.plan_year is not None and not (1 <= self.plan_year <= 8):
            raise ValueError("plan_year ต้องอยู่ในช่วง 1-8")
        if self.plan_semester is not None and not (1 <= self.plan_semester <= 3):
            raise ValueError("plan_semester ต้องอยู่ในช่วง 1-3")


@dataclass(frozen=True, slots=True)
class ContextResolution:
    """ผลการระบุ year/semester ของตาราง."""

    resolved: bool
    plan_year: int | None
    plan_semester: int | None
    provenance: Provenance | None
    conflicting_values: tuple[str, ...] = ()
    review_issue: ReviewIssue | None = None


# ── Internal helpers ──────────────────────────────────────────────────

# Thai year/semester patterns found in curriculum PDFs
_YEAR_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ชั้นปีที่\s*(\d)"),
    re.compile(r"ปีที่\s*(\d)"),
    re.compile(r"ปีการศึกษาที่\s*(\d)"),
    re.compile(r"Year\s*(\d)", re.IGNORECASE),
)

_SEMESTER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ภาคการศึกษาที่\s*(\d)"),
    re.compile(r"ภาคเรียนที่\s*(\d)"),
    re.compile(r"ภาคที่\s*(\d)"),
    re.compile(r"Semester\s*(\d)", re.IGNORECASE),
)

# Pattern to detect table header separators (lines with multiple columns)
_COLUMN_SEP_PATTERN = re.compile(r"\t|  {2,}|\|")


def _find_year_in_lines(
    lines: Sequence[AssembledLine],
) -> list[tuple[int, BBox, int]]:
    """หาปีการศึกษาจากบรรทัด — คืน [(year_value, bbox, line_order), ...]."""
    results: list[tuple[int, BBox, int]] = []
    for line in lines:
        for pattern in _YEAR_PATTERNS:
            match = pattern.search(line.text)
            if match:
                year_val = int(match.group(1))
                if 1 <= year_val <= 8:
                    results.append((year_val, line.bbox, line.order))
    return results


def _find_semester_in_lines(
    lines: Sequence[AssembledLine],
) -> list[tuple[int, BBox, int]]:
    """หาภาคการศึกษาจากบรรทัด — คืน [(semester_value, bbox, line_order), ...]."""
    results: list[tuple[int, BBox, int]] = []
    for line in lines:
        for pattern in _SEMESTER_PATTERNS:
            match = pattern.search(line.text)
            if match:
                sem_val = int(match.group(1))
                if 1 <= sem_val <= 3:
                    results.append((sem_val, line.bbox, line.order))
    return results


def _split_columns(text: str) -> list[str]:
    """แยกข้อความเป็นคอลัมน์ด้วย tab หรือ multiple spaces."""
    parts = _COLUMN_SEP_PATTERN.split(text)
    return [p.strip() for p in parts]


def _is_header_like(columns: list[str]) -> bool:
    """ตรวจว่าบรรทัดนี้ดูเหมือน header ของตาราง."""
    if len(columns) < 2:
        return False
    # Header มักมีคำสำคัญภาษาไทยหรืออังกฤษที่เป็นชื่อคอลัมน์
    header_keywords = {
        "รหัสวิชา", "ชื่อวิชา", "หน่วยกิต", "code", "course",
        "credit", "credits", "รหัส", "ชื่อ", "ภาคเรียน", "ปี",
        "หมวดวิชา", "ประเภท", "วิชาบังคับก่อน", "หมายเหตุ",
        "ลำดับ", "no.", "no", "#", "รวม", "total",
    }
    text_lower = " ".join(columns).lower()
    return any(kw in text_lower for kw in header_keywords)


def _merge_bbox(boxes: Sequence[BBox]) -> BBox:
    """รวม bbox หลายกรอบเป็นกรอบเดียวที่ครอบทั้งหมด."""
    if not boxes:
        return BBox(0.0, 0.0, 1.0, 1.0)
    x0 = min(b.x0 for b in boxes)
    y0 = min(b.y0 for b in boxes)
    x1 = max(b.x1 for b in boxes)
    y1 = max(b.y1 for b in boxes)
    return BBox(x0, y0, x1, y1)


def _compute_column_positions(
    header_columns: list[str], header_bbox: BBox
) -> list[tuple[float, float]]:
    """คำนวณตำแหน่ง x โดยประมาณของแต่ละคอลัมน์จาก header."""
    n = len(header_columns)
    if n == 0:
        return []
    col_width = header_bbox.width / n
    positions: list[tuple[float, float]] = []
    for i in range(n):
        x0 = header_bbox.x0 + i * col_width
        x1 = x0 + col_width
        positions.append((x0, x1))
    return positions


# ── TableExtractor ────────────────────────────────────────────────────


class TableExtractor:
    """ตรวจจับตารางจาก text lines และ OCR output แล้วผลิต DetectedTable.

    Algorithm outline:
    1. สแกน lines หา header row (≥ 2 คอลัมน์ที่ดูเหมือนชื่อฟิลด์)
    2. รวบรวม data rows ที่ตามหลัง header จนเจอบรรทัดว่างหรือหัวข้อใหม่
    3. สร้าง TableCell สำหรับทุก cell รวมเซลล์ว่าง
    4. ตรวจ shape mismatch (R7.5)
    5. ตรวจ span (R7.6)
    """

    def extract(
        self,
        page: PageCharSet,
        lines: Sequence[AssembledLine],
        ocr: Sequence[RegionOutcome],
    ) -> tuple[DetectedTable, ...]:
        """ตรวจจับตารางทั้งหมดในหน้าหนึ่ง.

        Returns:
            tuple ของ DetectedTable ที่ตรวจพบ (อาจเป็น () ถ้าไม่มีตาราง)
        """
        tables: list[DetectedTable] = []
        table_regions = self._detect_table_regions(lines)

        for table_idx, region in enumerate(table_regions):
            header_lines = region["header_lines"]
            data_lines = region["data_lines"]

            if not header_lines or not data_lines:
                continue

            # Determine column count from header
            header_columns = _split_columns(header_lines[0].text)
            column_count = len(header_columns)
            if column_count < 2:
                continue

            header_rows = len(header_lines)
            all_lines_in_table = list(header_lines) + list(data_lines)

            # Build cells
            cells = self._build_cells(
                table_index=table_idx,
                header_lines=header_lines,
                data_lines=data_lines,
                column_count=column_count,
                document_id=page.document_id,
                page_num=page.page,
            )

            if not cells:
                continue

            table = DetectedTable(
                table_index=table_idx,
                header_rows=header_rows,
                column_count=column_count,
                cells=tuple(cells),
                plan_year=None,
                plan_semester=None,
                context_provenance=None,
            )
            tables.append(table)

        return tuple(tables)

    def resolve_plan_context(
        self,
        table: DetectedTable,
        lines: Sequence[AssembledLine],
    ) -> ContextResolution:
        """ระบุปี/ภาคการศึกษาของตารางแผนการศึกษา (R7.3, R7.4).

        Returns:
            ContextResolution ที่ resolved=True เมื่อพบค่าเดียวที่ไม่ขัดแย้ง
            หรือ resolved=False พร้อม review_issue เมื่อระบุไม่ได้
        """
        year_hits = _find_year_in_lines(lines)
        semester_hits = _find_semester_in_lines(lines)

        # Collect unique year values
        year_values = list({y for y, _, _ in year_hits})
        semester_values = list({s for s, _, _ in semester_hits})

        # Case: ระบุไม่ได้ — ไม่พบหรือขัดแย้ง
        if len(year_values) != 1 or len(semester_values) != 1:
            conflicting: list[str] = []
            if len(year_values) == 0:
                conflicting.append("year: not found")
            elif len(year_values) > 1:
                conflicting.append(f"year: {year_values}")
            if len(semester_values) == 0:
                conflicting.append("semester: not found")
            elif len(semester_values) > 1:
                conflicting.append(f"semester: {semester_values}")

            issue = ReviewIssue(
                kind="table_context_unresolved",
                document_id=table.cells[0].document_id if table.cells else None,
                page=table.cells[0].page if table.cells else None,
                detail={
                    "table_index": table.table_index,
                    "year_values_found": year_values,
                    "semester_values_found": semester_values,
                },
            )
            return ContextResolution(
                resolved=False,
                plan_year=None,
                plan_semester=None,
                provenance=None,
                conflicting_values=tuple(conflicting),
                review_issue=issue,
            )

        # Single valid year and semester
        plan_year = year_values[0]
        plan_semester = semester_values[0]

        # Find the provenance from the matching lines
        year_bbox = year_hits[0][1]
        sem_bbox = semester_hits[0][1]
        combined_bbox = _merge_bbox([year_bbox, sem_bbox])
        page_num = table.cells[0].page if table.cells else 1
        doc_id = table.cells[0].document_id if table.cells else ""

        provenance = Provenance(
            document_id=doc_id,
            page=page_num,
            bbox=combined_bbox,
            span=(0, 0),
            extraction_method="text_layer",
        )

        return ContextResolution(
            resolved=True,
            plan_year=plan_year,
            plan_semester=plan_semester,
            provenance=provenance,
        )

    def validate_shape(
        self, table: DetectedTable
    ) -> list[ReviewIssue]:
        """ตรวจ shape mismatch: จำนวน cell ต่อแถวต้องตรง column_count (R7.5).

        Returns:
            list ของ ReviewIssue ที่ kind='table_shape_mismatch'
            (เซลล์ทั้งหมดยังคงอยู่ในตาราง ไม่ถูกลบ)
        """
        issues: list[ReviewIssue] = []
        # Group cells by row
        row_cells: dict[int, list[TableCell]] = defaultdict(list)
        for cell in table.cells:
            row_cells[cell.row_index].append(cell)

        for row_idx in sorted(row_cells.keys()):
            cells_in_row = row_cells[row_idx]
            # Calculate effective column count considering col_span
            effective_cols = sum(cell.col_span for cell in cells_in_row)
            if effective_cols != table.column_count:
                doc_id = cells_in_row[0].document_id if cells_in_row else None
                page_num = cells_in_row[0].page if cells_in_row else None
                issues.append(
                    ReviewIssue(
                        kind="table_shape_mismatch",
                        document_id=doc_id,
                        page=page_num,
                        detail={
                            "table_index": table.table_index,
                            "row_index": row_idx,
                            "expected_columns": table.column_count,
                            "actual_effective_columns": effective_cols,
                        },
                    )
                )
        return issues

    # ── Internal methods ──────────────────────────────────────────────

    def _detect_table_regions(
        self, lines: Sequence[AssembledLine]
    ) -> list[dict[str, list[AssembledLine]]]:
        """ตรวจหา table regions จาก lines.

        คืน list ของ dict ที่มี header_lines และ data_lines
        """
        regions: list[dict[str, list[AssembledLine]]] = []
        i = 0
        n = len(lines)

        while i < n:
            line = lines[i]
            columns = _split_columns(line.text)

            if len(columns) >= 2 and _is_header_like(columns):
                # Found potential header
                header_lines: list[AssembledLine] = [line]
                i += 1

                # Check for multi-row header (next line also looks like header)
                while i < n:
                    next_cols = _split_columns(lines[i].text)
                    if len(next_cols) >= 2 and _is_header_like(next_cols):
                        header_lines.append(lines[i])
                        i += 1
                    else:
                        break

                # Collect data rows
                data_lines: list[AssembledLine] = []
                while i < n:
                    data_cols = _split_columns(lines[i].text)
                    # Stop at empty line or new section header
                    if not lines[i].text.strip():
                        i += 1
                        break
                    if len(data_cols) < 2:
                        # Single column line might be end of table
                        # unless it's a merged cell
                        break
                    data_lines.append(lines[i])
                    i += 1

                if data_lines:
                    regions.append({
                        "header_lines": header_lines,
                        "data_lines": data_lines,
                    })
            else:
                i += 1

        return regions

    def _build_cells(
        self,
        *,
        table_index: int,
        header_lines: list[AssembledLine],
        data_lines: list[AssembledLine],
        column_count: int,
        document_id: str,
        page_num: int,
    ) -> list[TableCell]:
        """สร้าง TableCell จาก header + data lines.

        เซลล์ว่างถูกบันทึกเป็น cell record ที่ text="" (R7.2)
        Span บันทึกที่ตำแหน่ง (min row, min col) ไม่สร้างซ้ำ (R7.6)
        """
        cells: list[TableCell] = []
        # Track occupied positions for span detection
        occupied: set[tuple[int, int]] = set()

        all_lines = list(header_lines) + list(data_lines)

        for line_idx, line in enumerate(all_lines):
            row_index = line_idx + 1  # 1-based
            columns = _split_columns(line.text)

            # Pad or truncate columns to match expected column_count
            # (R7.2: empty cells are recorded with empty text)
            while len(columns) < column_count:
                columns.append("")

            col_positions = _compute_column_positions(
                columns[:column_count], line.bbox
            )

            col_idx = 1  # 1-based column index
            for c_i in range(column_count):
                # Skip if this position is occupied by a span
                if (row_index, col_idx) in occupied:
                    col_idx += 1
                    continue

                cell_text = columns[c_i] if c_i < len(columns) else ""

                # Compute approximate bbox for this cell
                if col_positions and c_i < len(col_positions):
                    cx0, cx1 = col_positions[c_i]
                    cell_bbox = BBox(cx0, line.bbox.y0, cx1, line.bbox.y1)
                else:
                    cell_bbox = line.bbox

                # Detect row_span and col_span
                row_span, col_span = self._detect_span(
                    cell_text=cell_text,
                    row_index=row_index,
                    col_index=col_idx,
                    all_lines=all_lines,
                    column_count=column_count,
                )

                # Mark occupied positions for span
                for r_off in range(row_span):
                    for c_off in range(col_span):
                        occupied.add((row_index + r_off, col_idx + c_off))

                cell = TableCell(
                    table_index=table_index,
                    row_index=row_index,
                    col_index=col_idx,
                    row_span=row_span,
                    col_span=col_span,
                    text=cell_text,
                    bbox=cell_bbox,
                    document_id=document_id,
                    page=page_num,
                )
                cells.append(cell)
                col_idx += col_span

        return cells

    def _detect_span(
        self,
        *,
        cell_text: str,
        row_index: int,
        col_index: int,
        all_lines: list[AssembledLine],
        column_count: int,
    ) -> tuple[int, int]:
        """ตรวจ row span / column span ของ cell.

        Default: row_span=1, col_span=1
        ตรวจจาก merge indicators ในข้อความ (เช่น cell ที่กินหลายคอลัมน์)
        """
        # Basic implementation: detect spans from text patterns
        # In practice, PDFs may use merged cells indicated by empty adjacent cells
        # For now, default to 1x1 — refined detection would need geometric analysis
        row_span = 1
        col_span = 1
        return row_span, col_span
