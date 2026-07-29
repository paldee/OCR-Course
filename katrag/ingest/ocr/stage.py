"""`OcrStage` protocol และ `StageResult` — สัญญาร่วมของทุก stage ใน cascade (design §4.9).

ทุก stage adapter (`stage_tesseract.py`, `stage_typhoon.py`) implement protocol นี้
และคืนผลรูปแบบเดียวกันเสมอ ไม่ว่า engine ภายในจะต่างกันแค่ไหน — `Ocr_Cascade`,
`Gain_Cost_Halter` และ `Region_Adjudicator` (งาน 9.6, 9.5) จึงทำงานกับ `StageResult`
โดยไม่ต้องรู้ว่า engine ไหนสร้างผลนั้นขึ้นมา

การออกแบบนี้ทำให้ **สลับ/เพิ่ม/ถอด engine ทำได้โดยไม่แก้โค้ดปลายทาง**: ถ้าต้องเปลี่ยนไปใช้
PaddleOCR 3.x ในอนาคต เขียน `stage_paddle.py` ใหม่ที่ implement `OcrStage` เดิม แก้แค่
`config/katrag.toml` (`ocr.stage_order`) และ `config/value_sets.toml` + schema CHECK ของ
`ocr_stage_result.engine` เท่านั้น ไม่ต้องแก้ `cascade.py`, halter, adjudicator หรือ
Table_Extractor/Field_Extractor ที่อยู่ปลายทาง
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np

from katrag.common.types import BBox


class OcrStage(Protocol):
    """หนึ่ง stage ของ cascade — implement โดย `TesseractStage` และ `TyphoonStage`."""

    name: str  # "tesseract5" | "typhoon_ocr1_5_2b"

    def recognize(self, image: np.ndarray, region: BBox, timeout_s: float) -> "StageResult":
        """OCR ภาพหนึ่ง region แล้วคืนผลภายในเวลาที่กำหนด.

        ผู้ implement ต้องรับผิดชอบการ timeout เอง (ไม่ปล่อยให้ `Ocr_Cascade` บล็อกค้าง) —
        เกิน `timeout_s` ต้อง raise `OcrEngineError` แทนที่จะคืนผลบางส่วน (R5.6)
        """
        ...


@dataclass(frozen=True, slots=True)
class StageResult:
    """ผลของหนึ่ง stage ต่อหนึ่ง region (design §4.9).

    `boxes` เป็น bounding box ระดับคำ/บรรทัดที่ engine คืนมาเพิ่มเติมจาก `text` รวม
    (ใช้ต่อใน Table_Extractor เพื่อคง provenance ระดับพิกัด) — บาง stage (เช่น Typhoon
    ที่ไม่คืน bbox รายคำ) อาจคืน tuple ว่างได้ ผู้เรียกต้องรองรับกรณีนี้
    """

    engine: str
    stage_index: int  # 1 หรือ 2 ตาม CHECK ของ schema (ocr_stage_result.stage_index)
    text: str
    quality_score: float  # 0.00..1.00
    confidence: float  # 0.00..1.00
    elapsed_ms: int
    boxes: tuple[tuple[BBox, str, float], ...] = ()
    cache_hit: bool = False

    def __post_init__(self) -> None:
        if not 0.0 <= self.quality_score <= 1.0:
            raise ValueError("quality_score ต้องอยู่ในช่วง 0.0-1.0")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence ต้องอยู่ในช่วง 0.0-1.0")
        if self.stage_index not in (1, 2):
            raise ValueError("stage_index ต้องเป็น 1 หรือ 2")
        if self.elapsed_ms < 0:
            raise ValueError("elapsed_ms ต้องไม่ติดลบ")
