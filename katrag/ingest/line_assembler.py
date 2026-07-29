"""Line_Assembler — ประกอบ glyph ที่จัดลำดับแล้วเป็นบรรทัด (design §4.6, R3.6, R3.7, R3.10).

หลักการที่ต่างจากการ "sort ตาม X ตรง ๆ"

combining mark เป็น glyph ความกว้างศูนย์ที่วางอยู่เหนือหรือใต้ base ค่า `x0` ของมัน
มักน้อยกว่า `x0` ของ base เล็กน้อย ถ้าเรียง glyph ตาม X แบบแบน mark จะหลุดไปอยู่หน้า base
และทำลายลำดับที่ Thai_Glyph_Reorderer เพิ่งจัดไว้ จึงเรียงเป็น **cluster** (base + mark
ที่ตามมาติดกัน) แล้วเรียง cluster ตาม X ของ base เท่านั้น

การจัดกลุ่มบรรทัดใช้ baseline tolerance เป็นสัดส่วนของ **font size ที่ใหญ่สุดในกลุ่ม**
(อัปเดตแบบ running max) เพราะบรรทัดหัวเรื่องที่ปนตัวเล็กต้องยอมให้ระยะห่างมากกว่าบรรทัด
เนื้อความปกติ ตัว tie-break ทุกจุดใช้ `order` ของ input เพื่อให้ผลลัพธ์ deterministic

R3.7 บังคับให้ multiset ของ codepoint ที่ไม่ใช่ whitespace เท่ากับ input — เมื่อไม่เท่า
ต้องบันทึก `glyph_count_mismatch` แต่ **ยังคงข้อความของหน้าไว้** ไม่ทิ้งหน้า (R3.10)
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from katrag.common.normalize import codepoint_multiset, mark_class
from katrag.common.types import BBox, CharRecord, TextLine
from katrag.config import ThaiConfig
from katrag.errors import ReviewIssue
from katrag.ingest.thai_reorder import ReorderedPage, effective_font_size


@dataclass(frozen=True, slots=True)
class AssembledPage:
    """ผลของ Line_Assembler ต่อหนึ่งหน้า."""

    document_id: str
    page: int
    lines: tuple[TextLine, ...]
    multiset_preserved: bool
    review_issues: tuple[ReviewIssue, ...]

    @property
    def text(self) -> str:
        """ข้อความของหน้า — บรรทัดคั่นด้วย `\\n` เดียว."""
        return "\n".join(line.text for line in self.lines)


@dataclass(frozen=True, slots=True)
class _Cluster:
    """base หนึ่งตัวพร้อม combining mark ที่เกาะอยู่ — หน่วยที่ห้ามแยกจากกัน."""

    chars: tuple[CharRecord, ...]

    @property
    def anchor(self) -> CharRecord:
        return self.chars[0]

    @property
    def baseline(self) -> float:
        return self.anchor.baseline

    @property
    def x0(self) -> float:
        return self.anchor.bbox.x0

    @property
    def order(self) -> int:
        return self.anchor.order

    @property
    def font_size(self) -> float:
        return max(effective_font_size(char) for char in self.chars)


class LineAssembler:
    """ประกอบบรรทัดจาก glyph ที่ผ่าน Thai_Glyph_Reorderer แล้ว."""

    def __init__(self, thai_config: ThaiConfig) -> None:
        self._cfg = thai_config

    def assemble(self, page: ReorderedPage) -> AssembledPage:
        clusters = self._clusters(page.chars)
        groups = self._group_by_baseline(clusters)
        # เลข order ต้องต่อเนื่องจาก 0 ในบรรทัดที่ **เหลืออยู่** จึงนับหลังกรองแล้ว
        # (กลุ่มที่มีแต่ whitespace ถูกทิ้ง ถ้านับก่อนกรองจะเกิดช่องว่างในลำดับ)
        built = [self._build_line(group) for group in groups]
        lines = tuple(
            replace(line, order=index)
            for index, line in enumerate(line for line in built if line is not None)
        )

        source = codepoint_multiset(page.text)
        assembled = codepoint_multiset("\n".join(line.text for line in lines))
        preserved = source == assembled
        issues: list[ReviewIssue] = []
        if not preserved:
            issues.append(
                ReviewIssue(
                    kind="glyph_count_mismatch",
                    document_id=page.document_id,
                    page=page.page,
                    detail={
                        "source_glyphs": sum(source.values()),
                        "assembled_glyphs": sum(assembled.values()),
                        "missing": self._diff(source, assembled),
                        "extra": self._diff(assembled, source),
                    },
                )
            )
        return AssembledPage(
            document_id=page.document_id,
            page=page.page,
            lines=lines,
            multiset_preserved=preserved,
            review_issues=tuple(issues),
        )

    # ── steps ────────────────────────────────────────────────────────

    def _clusters(self, chars: tuple[CharRecord, ...]) -> list[_Cluster]:
        """จับ base กับ mark ที่ตามหลังติดกันเป็นก้อนเดียว.

        mark ที่ไม่มี base นำหน้า (กรณี unresolved) กลายเป็น cluster เดี่ยวของตัวเอง
        เพื่อไม่ให้หายไป
        """
        clusters: list[_Cluster] = []
        current: list[CharRecord] = []
        for char in chars:
            if mark_class(char.codepoint) > 0 and current:
                current.append(char)
                continue
            if current:
                clusters.append(_Cluster(tuple(current)))
            current = [char]
        if current:
            clusters.append(_Cluster(tuple(current)))
        return clusters

    def _group_by_baseline(self, clusters: list[_Cluster]) -> list[list[_Cluster]]:
        """จัดกลุ่ม cluster เป็นบรรทัดตาม baseline (R3.6).

        เรียงตาม (baseline, x0, order) ก่อน แล้วไล่เปิดบรรทัดใหม่เมื่อ baseline ห่างจาก
        baseline อ้างอิงของบรรทัดเกิน tolerance × font size ที่ใหญ่สุดในบรรทัดนั้น
        """
        ordered = sorted(clusters, key=lambda c: (c.baseline, c.x0, c.order))
        groups: list[list[_Cluster]] = []
        reference: float = 0.0
        max_size: float = 0.0
        for cluster in ordered:
            if not groups:
                groups.append([cluster])
                reference = cluster.baseline
                max_size = cluster.font_size
                continue
            tolerance = self._cfg.line_baseline_tolerance_ratio * max(max_size, cluster.font_size)
            if abs(cluster.baseline - reference) <= tolerance:
                groups[-1].append(cluster)
                max_size = max(max_size, cluster.font_size)
                continue
            groups.append([cluster])
            reference = cluster.baseline
            max_size = cluster.font_size
        return groups

    def _build_line(self, group: list[_Cluster]) -> TextLine | None:
        """สร้าง TextLine จาก cluster ในบรรทัดเดียว — เรียงตาม X แล้ว tie-break ด้วย order.

        `order` ของ TextLine ถูกกำหนดภายหลังใน `assemble` หลังกรองบรรทัดว่างแล้ว
        """
        ordered = sorted(group, key=lambda c: (c.x0, c.order))
        chars = [char for cluster in ordered for char in cluster.chars]
        text = "".join(char.codepoint for char in chars)
        if not text.strip():
            # บรรทัดที่มีแต่ whitespace ไม่ถูกเก็บ (multiset นับเฉพาะ non-whitespace)
            return None
        x0 = min(char.bbox.x0 for char in chars)
        y0 = min(char.bbox.y0 for char in chars)
        x1 = max(char.bbox.x1 for char in chars)
        y1 = max(char.bbox.y1 for char in chars)
        anchor = max(ordered, key=lambda c: (c.font_size, -c.order))
        return TextLine(
            text=text,
            bbox=BBox(x0, y0, max(x1, x0), max(y1, y0)),
            baseline=anchor.baseline,
            order=0,
        )

    @staticmethod
    def _diff(left: dict[str, int], right: dict[str, int]) -> dict[str, int]:
        """codepoint ที่ `left` มีมากกว่า `right` — รูปแบบ U+XXXX เพื่อให้อ่าน log ได้."""
        out: dict[str, int] = {}
        for codepoint, count in left.items():
            delta = count - right.get(codepoint, 0)
            if delta > 0:
                out[f"U+{ord(codepoint):04X}"] = delta
        return out
