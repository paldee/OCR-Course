"""`TesseractStage` — stage 1 ของ cascade (design §4.9, R5.1).

Tesseract 5 เป็น system binary (ไม่ใช่ pip package) จึงเรียกผ่าน `pytesseract` ที่ทำหน้าที่
เพียง wrap การเรียก subprocess ตัวจริงไม่มี weight/model แยกให้ตรวจ sha256 (ดู preflight.py
ที่ตรวจ traineddata ข้าง binary แทน)

คะแนนคุณภาพ (`StageResult.quality_score`) มาจาก **ค่าความเชื่อมั่นเฉลี่ยที่ Tesseract
รายงานเอง** ต่อคำ (`image_to_data` คอลัมน์ `conf`) แปลงจากสเกล 0-100 เป็น 0.00-1.00 —
เลือกใช้ค่านี้เพราะเป็นสัญญาณคุณภาพเดียวที่ engine ให้มาโดยไม่ต้องมี ground truth มาเทียบ
(สอดคล้องกับหลักการที่ Gain_Cost_Halter ใช้เปรียบเทียบคุณภาพระหว่าง stage แบบไม่มี label)
คำที่ Tesseract ให้ `conf = -1` (แถวที่ไม่ใช่คำจริง เช่นช่องว่างระหว่างบรรทัด) ถูกตัดออก
ก่อนเฉลี่ย ถ้าไม่มีคำที่ conf ≥ 0 เลย ถือว่า quality_score = 0.00

**ข้อบกพร่องที่พบและแก้จากการทดสอบจริง:** `image_to_data` แบ่ง "word" ของภาษาไทยเป็นกลุ่ม
อักขระย่อยระดับ glyph cluster ไม่ใช่คำจริง เพราะภาษาไทยไม่มีช่องว่างระหว่างคำให้ Tesseract
ใช้แบ่งขอบคำ (ตัวอย่างจริงที่พบ: `"116 ร า ย ล ะ เอ ี ย ด ห ล ั ก ส ู ต ร"` แทนที่จะเป็น
`"116 รายละเอียดหลักสูตร"`) ถ้าต่อ token จาก `image_to_data` ด้วยช่องว่างจะได้ข้อความที่มี
ช่องว่างแทรกกลางคำทุกตัว จึงใช้ `image_to_string` (ให้ผลระดับบรรทัด ไม่มีปัญหานี้) เป็นค่า
`text` หลัก และใช้ `image_to_data` เฉพาะสำหรับดึง bbox ระดับ token ไปประกอบ `boxes`
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np
from PIL import Image

from katrag.common.types import BBox
from katrag.errors import OcrEngineError
from katrag.ingest.ocr.stage import StageResult

STAGE_INDEX = 1
ENGINE_NAME = "tesseract5"


@dataclass(slots=True)
class TesseractStage:
    """OCR ด้วย Tesseract 5 ผ่าน `pytesseract` (R5.1)."""

    name: str = ENGINE_NAME
    lang: str = "tha+eng"
    tesseract_cmd: str | None = None

    def __post_init__(self) -> None:
        import pytesseract  # นำเข้าเฉพาะเมื่อใช้จริง — ให้ import package ไม่ผูกกับ pytesseract

        if self.tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = self.tesseract_cmd

    def recognize(self, image: np.ndarray, region: BBox, timeout_s: float) -> StageResult:
        """ครอบภาพตาม `region` แล้วส่งให้ Tesseract — timeout เกิน `timeout_s` -> OcrEngineError (R5.6).

        ใช้ absolute deadline ร่วมกันสำหรับ image_to_string + image_to_data เพื่อให้
        wall-clock รวมไม่เกิน timeout_s (ก่อนหน้านี้ส่ง timeout_s ให้แต่ละ call แยกกัน
        ทำให้กรณีเลวร้ายอาจใช้ถึง 2×timeout_s)
        """
        import pytesseract
        from pytesseract import TesseractError

        crop = _crop_region(image, region)
        pil_image = Image.fromarray(crop)

        start = time.perf_counter()
        deadline = start + timeout_s

        def _remaining() -> float:
            left = deadline - time.perf_counter()
            if left <= 0:
                raise OcrEngineError(
                    "Tesseract เกิน deadline ที่กำหนด", engine=ENGINE_NAME, timeout_s=timeout_s
                )
            return left

        try:
            # `image_to_string` ใช้แยกจาก `image_to_data` โดยเจตนา: Tesseract แบ่ง "word"
            # ของภาษาไทยเป็นกลุ่มอักขระย่อย (glyph cluster) ไม่ใช่คำจริง เพราะภาษาไทยไม่มี
            # ช่องว่างระหว่างคำ ถ้าต่อ token จาก image_to_data ด้วยช่องว่างจะได้ข้อความที่มี
            # ช่องว่างแทรกกลางคำทุกตัว (ยืนยันจากการทดสอบจริง) `image_to_string` ให้ผลลัพธ์
            # ระดับบรรทัด/หน้าที่ไม่มีปัญหานี้ ใช้เป็นค่า `text` หลัก ส่วน `image_to_data`
            # ใช้เฉพาะสำหรับดึง bbox ระดับ token ไปประกอบ `boxes` เท่านั้น
            text_raw = pytesseract.image_to_string(
                pil_image, lang=self.lang, timeout=_remaining()
            )
            data = pytesseract.image_to_data(
                pil_image,
                lang=self.lang,
                timeout=_remaining(),
                output_type=pytesseract.Output.DICT,
            )
        except RuntimeError as exc:
            # pytesseract คืน RuntimeError เมื่อ timeout (ไม่ใช่ TimeoutError) ตาม library นี้
            raise OcrEngineError(
                "Tesseract เกิน timeout ที่กำหนด", engine=ENGINE_NAME, timeout_s=timeout_s
            ) from exc
        except TesseractError as exc:
            raise OcrEngineError(
                "Tesseract คืน error", engine=ENGINE_NAME, reason=str(exc)
            ) from exc
        elapsed_ms = int((time.perf_counter() - start) * 1000)

        confidences: list[float] = []
        boxes: list[tuple[BBox, str, float]] = []
        for i, raw_text in enumerate(data.get("text", [])):
            token = raw_text.strip()
            if not token:
                continue
            conf_raw = data["conf"][i]
            try:
                conf = float(conf_raw)
            except (TypeError, ValueError):
                continue
            if conf < 0:
                continue
            confidences.append(conf)
            box = BBox(
                x0=region.x0 + float(data["left"][i]),
                y0=region.y0 + float(data["top"][i]),
                x1=region.x0 + float(data["left"][i]) + float(data["width"][i]),
                y1=region.y0 + float(data["top"][i]) + float(data["height"][i]),
            )
            boxes.append((box, token, conf / 100.0))

        quality_score = (sum(confidences) / len(confidences) / 100.0) if confidences else 0.0
        quality_score = max(0.0, min(1.0, quality_score))

        return StageResult(
            engine=ENGINE_NAME,
            stage_index=STAGE_INDEX,
            text=text_raw.strip(),
            quality_score=quality_score,
            confidence=quality_score,
            elapsed_ms=elapsed_ms,
            boxes=tuple(boxes),
            cache_hit=False,
        )


def _crop_region(image: np.ndarray, region: BBox) -> np.ndarray:
    """ครอบภาพตามพิกัดของ `region` — พิกัดถูก clamp ให้อยู่ในขอบเขตของภาพเสมอ.

    ป้องกัน region ที่คำนวณคลาดเคลื่อนเล็กน้อยจากขั้นก่อนหน้าไม่ให้ทำให้ slice ล้มเหลว
    """
    height, width = image.shape[:2]
    x0 = max(0, int(round(region.x0)))
    y0 = max(0, int(round(region.y0)))
    x1 = min(width, int(round(region.x1)))
    y1 = min(height, int(round(region.y1)))
    if x1 <= x0 or y1 <= y0:
        raise OcrEngineError(
            "region หลัง clamp ไม่มีพื้นที่เหลือให้ครอบภาพ",
            engine=ENGINE_NAME,
            region=region.as_tuple(),
            image_shape=(height, width),
        )
    return image[y0:y1, x0:x1]
