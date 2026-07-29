"""Thai_Glyph_Reorderer — จัดลำดับ glyph ไทยจาก geometry (design §4.5, R3.1-R3.4, R3.9).

ชั้นนี้ทำงานบน **glyph ที่มีพิกัด** ต่างจาก `katrag.common.normalize.canonical_mark_order`
ที่ทำงานบนสตริงล้วน เหตุผลที่ต้องใช้ geometry: ใน PDF ตระกูลนี้ combining mark ถูกวาง
เป็น glyph ความกว้างศูนย์ที่พิกัดของตัวเอง และลำดับใน content stream ไม่รับประกันว่า
mark จะตามหลัง base ของมัน (พบทั้งกรณี mark มาก่อน base และมี whitespace คั่นกลาง)

ลำดับการทำงาน

1. **map PUA → combining mark จริง** ต่อ glyph โดยคงพิกัดเดิม (คลังนี้ใช้ U+F700-U+F71A
   แทนสระ/วรรณยุกต์ ถ้าไม่แปลงจะมองไม่เห็นว่าเป็น mark และจัดลำดับไม่ได้)
2. **ผูก mark กับ base** ที่ใกล้ที่สุดในแนวนอน ภายใน baseline tolerance และหน้าต่างแนวนอน
   จากไฟล์ตั้งค่า เลือกตัวซ้ายเมื่อระยะเท่ากัน (R3.2)
3. **จัดลำดับภายใน cluster** base → below vowel → above vowel → tone → sign ด้วย stable sort
   ที่ tie-break ตาม `order` ของ input (R3.1)
4. **ลบ whitespace ที่คั่นระหว่างอักขระไทยกับ mark เท่านั้น** (R3.3, R3.4)
5. **mark ที่หา base ไม่ได้ต้องคงตำแหน่งเดิม** และรายงาน `thai_reorder_unresolved` (R3.9)

การตัดสินว่า glyph หนึ่งเป็น mark ใช้ **สองเงื่อนไขร่วมกัน** คือ mark class > 0 และ
ความกว้าง bbox ≤ เพดานที่ตั้งไว้ เพราะพบ glyph ความกว้างศูนย์ที่ไม่ใช่ mark ในคลังจริง
(`^` U+005E, `_` U+005F) และในทางกลับกัน mark ที่มีความกว้างจริงก็ยังต้องถูกจัดลำดับ
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from katrag.common.normalize import (
    THAI_PUA_TO_MARK,
    is_thai_char,
    mark_class,
    marker_space_violations,
)
from katrag.common.types import CharRecord, PageCharSet
from katrag.config import ThaiConfig
from katrag.errors import ReviewIssue


@dataclass(frozen=True, slots=True)
class ReorderedPage:
    """ผลของ Thai_Glyph_Reorderer ต่อหนึ่งหน้า."""

    document_id: str
    page: int
    width_pt: float
    height_pt: float
    chars: tuple[CharRecord, ...]
    unresolved_marks: int
    dropped_marker_spaces: int
    pua_mapped: int
    review_issues: tuple[ReviewIssue, ...]

    @property
    def text(self) -> str:
        return "".join(char.codepoint for char in self.chars)


def effective_font_size(char: CharRecord) -> float:
    """ขนาดที่ใช้คำนวณ tolerance = ค่ามากสุดระหว่างขนาดฟอนต์ที่ span รายงานกับความสูง bbox.

    เหตุผลที่ไม่เชื่อ `span["size"]` เพียงอย่างเดียว (วัดจากคลังจริง)

    * span บางตัวรายงาน `size = 0` ซึ่งทำให้ tolerance เป็นศูนย์และ mark หา base ไม่ได้เลย
    * `PH_D_IT2561_old.pdf` หน้า 9, 10, 14 มี span ที่รายงาน `size = 2.1` ขณะที่ bbox ของ
      glyph สูง 19.3 pt ทำให้ baseline tolerance เหลือ 0.41 pt และวรรณยุกต์ 5 ตัวหา base
      ไม่ได้ทั้งที่อยู่ติดกับ base ในสตรีม การใช้ค่ามากสุดทำให้ tolerance สะท้อนขนาด glyph จริง

    ค่ามากสุดไม่กระทบ glyph ปกติ เพราะ bbox ของ combining mark เตี้ยกว่าขนาดฟอนต์อยู่แล้ว
    """
    candidate = max(char.font_size, char.bbox.height)
    return candidate if candidate > 0.0 else 1.0


class ThaiGlyphReorderer:
    """จัดลำดับ glyph ไทยของหนึ่งหน้าแบบ deterministic."""

    def __init__(self, thai_config: ThaiConfig) -> None:
        self._cfg = thai_config

    # ── public API ───────────────────────────────────────────────────

    def reorder(self, char_set: PageCharSet) -> ReorderedPage:
        """คืนลำดับ glyph ใหม่ของหน้า โดยไม่เพิ่มและไม่ลบอักขระที่ไม่ใช่ whitespace."""
        mapped, pua_mapped = self._map_pua(char_set.chars)
        is_mark = tuple(self._is_mark(char) for char in mapped)

        attachment = self._attach_marks(mapped, is_mark)
        dropped = self._marker_space_indices(mapped, is_mark, attachment)
        chars = self._emit(mapped, is_mark, attachment, dropped)

        unresolved = sum(
            1 for index, marked in enumerate(is_mark) if marked and attachment[index] is None
        )
        issues: list[ReviewIssue] = []
        if unresolved:
            issues.append(
                ReviewIssue(
                    kind="thai_reorder_unresolved",
                    document_id=char_set.document_id,
                    page=char_set.page,
                    detail={
                        "reason": "no_base_within_window",
                        "unresolved_marks": unresolved,
                        "baseline_tolerance_ratio": self._cfg.baseline_tolerance_ratio,
                        "horizontal_window_ratio": self._cfg.horizontal_window_ratio,
                    },
                )
            )
        result = ReorderedPage(
            document_id=char_set.document_id,
            page=char_set.page,
            width_pt=char_set.width_pt,
            height_pt=char_set.height_pt,
            chars=chars,
            unresolved_marks=unresolved,
            dropped_marker_spaces=len(dropped),
            pua_mapped=pua_mapped,
            review_issues=tuple(issues),
        )
        violations = marker_space_violations(result.text)
        if violations:
            # ไม่ควรเกิด: pattern ต้องหมดหลังขั้นตอนที่ 4 — ถ้าเกิดต้องเห็น ไม่ใช่เงียบ
            issues.append(
                ReviewIssue(
                    kind="thai_reorder_unresolved",
                    document_id=char_set.document_id,
                    page=char_set.page,
                    detail={"reason": "marker_space_residue", "violations": violations},
                )
            )
            result = replace(result, review_issues=tuple(issues))
        return result

    # ── steps ────────────────────────────────────────────────────────

    def _map_pua(self, chars: tuple[CharRecord, ...]) -> tuple[list[CharRecord], int]:
        """แปลง PUA เป็น combining mark จริง คงพิกัด/ฟอนต์/ลำดับเดิมไว้ทั้งหมด."""
        out: list[CharRecord] = []
        count = 0
        for char in chars:
            replacement = THAI_PUA_TO_MARK.get(char.codepoint)
            if replacement is None:
                out.append(char)
                continue
            count += 1
            out.append(replace(char, codepoint=replacement))
        return out, count

    def _is_mark(self, char: CharRecord) -> bool:
        """mark ต้องผ่านทั้ง mark class และเพดานความกว้าง (ดู docstring ของโมดูล)."""
        if mark_class(char.codepoint) == 0:
            return False
        return char.bbox.width <= self._cfg.zero_width_max_points

    def _attach_marks(
        self, chars: list[CharRecord], is_mark: tuple[bool, ...]
    ) -> list[int | None]:
        """คืน index ของ base ที่แต่ละ mark ผูกกับ (None = หาไม่ได้).

        เกณฑ์: |base.baseline - mark.baseline| ≤ tolerance_ratio × font_size และ
        |base.center_x - mark.center_x| ≤ window_ratio × font_size
        เลือกระยะแนวนอนน้อยสุด เสมอกันเลือกตัวที่อยู่ซ้ายกว่า เสมออีกเลือก order น้อยกว่า
        """
        base_indices = [i for i, marked in enumerate(is_mark) if not marked and self._can_host(chars[i])]
        attachment: list[int | None] = [None] * len(chars)
        for index, marked in enumerate(is_mark):
            if not marked:
                continue
            mark = chars[index]
            size = effective_font_size(mark)
            baseline_tol = self._cfg.baseline_tolerance_ratio * size
            window = self._cfg.horizontal_window_ratio * size
            best: tuple[float, float, int] | None = None
            best_index: int | None = None
            for base_index in base_indices:
                base = chars[base_index]
                if abs(base.baseline - mark.baseline) > baseline_tol:
                    continue
                distance = abs(base.bbox.center_x - mark.bbox.center_x)
                if distance > window:
                    continue
                key = (distance, base.bbox.x0, base.order)
                if best is None or key < best:
                    best = key
                    best_index = base_index
            attachment[index] = best_index
        return attachment

    def _can_host(self, char: CharRecord) -> bool:
        """base ที่รับ mark ได้ต้องเป็นอักขระไทยที่ไม่ใช่ mark และไม่ใช่ whitespace (R3.2)."""
        return is_thai_char(char.codepoint) and mark_class(char.codepoint) == 0

    def _marker_space_indices(
        self,
        chars: list[CharRecord],
        is_mark: tuple[bool, ...],
        attachment: list[int | None],
    ) -> frozenset[int]:
        """index ของ whitespace ที่คั่นระหว่างอักขระไทยกับ mark ที่ตามมา — ต้องลบ (R3.3).

        ลบเฉพาะ run ของ whitespace ที่ **มีอักขระไทยอยู่ซ้าย และมี mark อยู่ขวา** เท่านั้น
        whitespace ตำแหน่งอื่นไม่ถูกแตะ (property test บังคับข้อนี้)

        `attachment` เข้ามาเพื่อความชัดเจนของสัญญา: mark ที่หา base ไม่ได้ก็ยังทำให้
        whitespace ที่คั่นถูกลบ เพราะ pattern ต้องห้ามใน R3.4 วัดจากข้อความล้วน
        """
        del attachment  # เจตนา: เกณฑ์ไม่ขึ้นกับผลการผูก
        dropped: set[int] = set()
        length = len(chars)
        index = 0
        while index < length:
            if not chars[index].codepoint.isspace():
                index += 1
                continue
            run_start = index
            while index < length and chars[index].codepoint.isspace():
                index += 1
            left_ok = run_start > 0 and is_thai_char(chars[run_start - 1].codepoint)
            right_ok = index < length and is_mark[index]
            if left_ok and right_ok:
                dropped.update(range(run_start, index))
        return frozenset(dropped)

    def _emit(
        self,
        chars: list[CharRecord],
        is_mark: tuple[bool, ...],
        attachment: list[int | None],
        dropped: frozenset[int],
    ) -> tuple[CharRecord, ...]:
        """ประกอบลำดับผลลัพธ์: base + mark ที่ผูกไว้ (เรียงตาม class) แล้วตามด้วยที่เหลือ.

        mark ที่ผูกได้จะถูกปล่อยที่ตำแหน่งของ base เท่านั้น (แม้ base อยู่หลังใน stream)
        mark ที่ผูกไม่ได้ถูกปล่อยที่ตำแหน่งเดิมของตัวเอง (R3.9)
        """
        attached: dict[int, list[int]] = {}
        for index, base_index in enumerate(attachment):
            if base_index is None or not is_mark[index]:
                continue
            attached.setdefault(base_index, []).append(index)

        out: list[CharRecord] = []
        for index, char in enumerate(chars):
            if index in dropped:
                continue
            if is_mark[index]:
                if attachment[index] is None:
                    out.append(char)
                continue
            out.append(char)
            mark_indices = attached.get(index)
            if not mark_indices:
                continue
            mark_indices.sort(key=lambda i: (mark_class(chars[i].codepoint), chars[i].order))
            out.extend(chars[i] for i in mark_indices)
        return tuple(out)
