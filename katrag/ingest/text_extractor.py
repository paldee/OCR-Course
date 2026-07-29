"""Text_Extractor — ดึง per-character geometry จาก text layer (design §4.4, R2).

ข้อบังคับที่ชั้นนี้รักษา

1. **ดึงให้เสร็จก่อนขั้นถัดไป** per-character record ของหน้าต้องครบก่อนเรียก
   Thai_Glyph_Reorderer, Page_Quality_Gate หรือ Ocr_Cascade ของหน้านั้น (R2.1)
2. **ทุก glyph มีข้อมูลครบห้าฟิลด์** codepoint, bbox, font, ขนาด, baseline (R2.1)
3. **หน้าที่อ่านไม่ได้ต้องไม่บันทึกข้อความบางส่วน** และต้องไปหน้าถัดไป (R2.4)
4. **เอกสารที่เปิดไม่ได้ต้องไม่ยุติทั้งชุด** (R2.5)
5. **หน้าที่ไม่มีข้อความไม่ถือเป็น error** แต่ส่งต่อให้ Page_Quality_Gate ตัดสิน (R2.6)

หมายเหตุเรื่องพิกัด: PyMuPDF ใช้จุดกำเนิดมุมซ้ายบน y เพิ่มลงล่าง ค่า `baseline`
มาจาก `origin[1]` ของ char ซึ่งเป็นเส้นฐานของตัวอักษรจริง ไม่ใช่ขอบล่างของ bbox

ข้อเท็จจริงที่วัดจากคลังจริง (ใช้กำหนดสัญญาของชั้นถัดไป)

* bbox ของ glyph มีความสูงเป็นบวกเสมอ แต่ **ความกว้างเป็นศูนย์ได้** สำหรับ glyph ที่
  ไม่มี advance width ดังนั้น `BBox.is_valid()` (ซึ่งบังคับ x1 > x0 ตาม R9.2) ใช้กับ
  bbox ระดับ provenance/บรรทัดเท่านั้น **ห้าม** ใช้ตรวจ bbox ระดับ glyph
* glyph ความกว้างศูนย์ **ไม่ได้เป็น combining mark ทุกตัว** — พบ `^` (U+005E) และ
  `_` (U+005F) ความกว้างศูนย์ในฟอนต์ของคลังนี้ Thai_Glyph_Reorderer จึงต้องตรวจ
  ทั้งความกว้างและ mark class ห้ามตัดสินจากความกว้างเพียงอย่างเดียว
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

from katrag.common.normalize import unmapped_thai_pua
from katrag.common.types import BBox, CharRecord, PageCharSet
from katrag.errors import DocumentUnreadableError, PageUnreadableError, ReviewIssue


@dataclass(frozen=True, slots=True)
class PageFailure:
    """หน้าที่อ่านไม่ได้ — ผู้เรียกต้องเขียน error_record ระดับหน้าแล้วไปหน้าถัดไป (R2.4).

    ไม่มีข้อความบางส่วนอยู่ในชนิดนี้เลย เพื่อบังคับตามข้อห้าม "ห้ามบันทึกข้อความบางส่วน"
    """

    document_id: str
    page: int
    reason: str
    message: str


@dataclass(frozen=True, slots=True)
class PageExtraction:
    """ผลการดึง text layer ของหนึ่งหน้า พร้อมสัญญาณคุณภาพระดับดิบ."""

    char_set: PageCharSet
    fonts: frozenset[str]
    unmapped_pua: dict[str, int]
    review_issues: tuple[ReviewIssue, ...]
    plain_text: str = ""  # raw get_text() จาก PyMuPDF (สะอาด ไม่ผ่าน reorder)

    @property
    def document_id(self) -> str:
        return self.char_set.document_id

    @property
    def page(self) -> int:
        return self.char_set.page

    @property
    def char_count(self) -> int:
        return self.char_set.char_count

    @property
    def raw_text(self) -> str:
        """ข้อความตามลำดับที่ปรากฏใน stream (ยังไม่จัดลำดับใหม่).

        ใช้เป็น `raw_text` ของ span เพื่อเทียบ quote แบบดิบได้ (R17.3)
        """
        return "".join(char.codepoint for char in self.char_set.chars)


class TextExtractor:
    """ดึง per-character record ด้วย PyMuPDF rawdict."""

    def __init__(self) -> None:
        self._fitz: Any | None = None

    # ── document level ───────────────────────────────────────────────

    def open_document(self, path: str | Path) -> Any:
        """เปิดเอกสาร; ล้มเหลว -> DocumentUnreadableError ให้ผู้เรียกข้ามเอกสาร (R2.5)."""
        fitz = self._import_fitz()
        try:
            return fitz.open(path)
        except Exception as exc:
            raise DocumentUnreadableError(
                "เปิดเอกสารไม่สำเร็จ", path=str(path), reason=f"{type(exc).__name__}: {exc}"
            ) from exc

    def iter_pages(
        self, pdf: Any, document_id: str, *, page_numbers: Iterable[int] | None = None
    ) -> Iterator[PageExtraction]:
        """ไล่หน้าแบบ generator — ไม่สร้าง list ของทุกหน้า (R6.1, R6.4).

        ช่วงหน้ามาจาก `pdf.page_count` เท่านั้น ไม่มีการฮาร์ดโค้ดหน้า
        """
        numbers = page_numbers if page_numbers is not None else range(1, pdf.page_count + 1)
        for page_number in numbers:
            yield self.extract_page(pdf, document_id, page_number)

    def iter_pages_resilient(
        self, pdf: Any, document_id: str, *, page_numbers: Iterable[int] | None = None
    ) -> Iterator[PageExtraction | PageFailure]:
        """เหมือน `iter_pages` แต่หน้าที่พังกลายเป็น `PageFailure` แทนการยุติทั้งเอกสาร (R2.4).

        หน้าที่ `char_count = 0` ไม่ใช่ความล้มเหลว — ยัง yield เป็น `PageExtraction`
        เพื่อให้ Page_Quality_Gate ตัดสินว่าจะส่ง OCR หรือไม่ (R2.6)
        """
        numbers = page_numbers if page_numbers is not None else range(1, pdf.page_count + 1)
        for page_number in numbers:
            try:
                yield self.extract_page(pdf, document_id, page_number)
            except PageUnreadableError as exc:
                yield PageFailure(
                    document_id=document_id,
                    page=page_number,
                    reason=str(exc.context.get("reason", "page_unreadable")),
                    message=exc.message,
                )

    # ── page level ───────────────────────────────────────────────────

    def extract_page(self, pdf: Any, document_id: str, page_number: int) -> PageExtraction:
        """ดึง glyph ทั้งหมดของหนึ่งหน้า.

        Raises:
            PageUnreadableError: เมื่ออ่านหน้าไม่ได้ — ผู้เรียกต้องบันทึก error record
                และไปหน้าถัดไป โดยห้ามบันทึกข้อความบางส่วนของหน้านั้น (R2.4)
        """
        if page_number < 1 or page_number > pdf.page_count:
            raise PageUnreadableError(
                "เลขหน้าอยู่นอกช่วงของเอกสาร",
                document_id=document_id,
                page_number=page_number,
                page_count=pdf.page_count,
            )
        try:
            page = pdf[page_number - 1]
            raw = page.get_text("rawdict")
            # ดึง plain text ตรง ๆ จาก PyMuPDF (สะอาด ไม่ต้อง reorder)
            plain_text = page.get_text()
            width_pt = float(page.rect.width)
            height_pt = float(page.rect.height)
        except Exception as exc:
            raise PageUnreadableError(
                "อ่านหน้าไม่สำเร็จ",
                document_id=document_id,
                page_number=page_number,
                reason=f"{type(exc).__name__}: {exc}",
            ) from exc

        chars: list[CharRecord] = []
        fonts: set[str] = set()
        image_count = 0
        image_area = 0.0
        order = 0

        for block in raw.get("blocks", ()):
            block_type = block.get("type")
            if block_type == 1:  # image block
                image_count += 1
                bbox = block.get("bbox")
                if bbox and len(bbox) == 4:
                    image_area += abs((bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))
                continue
            for line in block.get("lines", ()):
                for span in line.get("spans", ()):
                    font_name = str(span.get("font", ""))
                    font_size = float(span.get("size", 0.0) or 0.0)
                    origin = span.get("origin") or (0.0, 0.0)
                    baseline = float(origin[1])
                    fonts.add(font_name)
                    for char in span.get("chars", ()):
                        codepoint = char.get("c")
                        if not isinstance(codepoint, str) or len(codepoint) != 1:
                            continue
                        box = char.get("bbox")
                        if not box or len(box) != 4:
                            continue
                        char_origin = char.get("origin") or origin
                        chars.append(
                            CharRecord(
                                codepoint=codepoint,
                                bbox=BBox(float(box[0]), float(box[1]), float(box[2]), float(box[3])),
                                font_name=font_name,
                                font_size=font_size,
                                baseline=float(char_origin[1]),
                                order=order,
                            )
                        )
                        order += 1

        page_area = width_pt * height_pt
        image_area_ratio = 0.0 if page_area <= 0 else min(1.0, image_area / page_area)

        char_set = PageCharSet(
            document_id=document_id,
            page=page_number,
            width_pt=width_pt,
            height_pt=height_pt,
            chars=tuple(chars),
            image_count=image_count,
            image_area_ratio=image_area_ratio,
        )

        raw_text = "".join(char.codepoint for char in chars)
        unmapped = unmapped_thai_pua(raw_text)
        issues: list[ReviewIssue] = []
        if unmapped:
            # PUA ที่ยังไม่มี mapping ต้องรายงาน ไม่แปลงแบบเดา
            issues.append(
                ReviewIssue(
                    kind="thai_reorder_unresolved",
                    document_id=document_id,
                    page=page_number,
                    detail={
                        "reason": "unmapped_thai_pua",
                        "codepoints": {f"U+{ord(ch):04X}": count for ch, count in sorted(unmapped.items())},
                    },
                )
            )

        return PageExtraction(
            char_set=char_set,
            fonts=frozenset(fonts),
            unmapped_pua=unmapped,
            review_issues=tuple(issues),
            plain_text=plain_text,
        )

    # ── internals ────────────────────────────────────────────────────

    def _import_fitz(self) -> Any:
        if self._fitz is None:
            import fitz  # นำเข้าเมื่อใช้จริง เพื่อให้ import package ไม่ผูกกับ PyMuPDF

            self._fitz = fitz
        return self._fitz
