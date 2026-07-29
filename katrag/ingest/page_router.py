"""Ocr_Page_Router — กำหนด compute path หนึ่งค่าต่อหน้า OCR candidate (design §4.8, R4.7-R4.8).

เป็น **total function** — ทุกหน้าที่เป็น OCR candidate ต้องได้ compute path ค่าเดียวเสมอ
ไม่มีเงื่อนไขใดที่ปล่อยให้ไม่มีค่า (property 17 บังคับ deterministic + total)

ลำดับการตรวจ **ต้องเป็น deep ก่อน fast** ตามที่ requirements ระบุไว้ตรง ๆ เพื่อไม่ให้
เงื่อนไขทับซ้อนกัน: ถ้า `deep_min_image_area_ratio <= fast_max_image_area_ratio` (ค่าที่ไม่
สมเหตุผลแต่เป็นไปได้ในทางทฤษฎี) ผลของหน้าที่ image_area_ratio อยู่ระหว่างสองค่านี้จะขึ้นกับ
ลำดับการตรวจ — `KatragConfig._validate` บังคับ `fast_max < deep_min` ไว้แล้วเพื่อกันกรณีนี้
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from katrag.common.types import ComputePath
from katrag.config import PageRouteConfig
from katrag.ingest.quality_gate import PageMetrics

#: รหัสเหตุผลที่เก็บลง store (design §4.8)
REASON_NO_TEXT = "no_text"
REASON_HIGH_IMAGE_AREA = "high_image_area"
REASON_LOW_IMAGE_AREA = "low_image_area"
REASON_DEFAULT_STANDARD = "default_standard"


@dataclass(frozen=True, slots=True)
class RouteDecision:
    """ผลของการกำหนด compute path หนึ่งหน้า (R4.8)."""

    compute_path: ComputePath
    reason_code: str
    metrics_used: Mapping[str, float]


class OcrPageRouter:
    """กำหนด compute path เดียวต่อหน้าจาก `PageMetrics`."""

    def __init__(self, config: PageRouteConfig) -> None:
        self._config = config

    def route(self, metrics: PageMetrics) -> RouteDecision:
        """`deep` เมื่อ char_count = 0 หรือ image_area_ratio >= เกณฑ์; `fast` เมื่อ
        image_area_ratio <= เกณฑ์; อื่น ๆ `standard` (R4.7).

        ตรวจ `deep` ก่อน `fast` ตามลำดับที่ requirements ระบุ
        """
        if metrics.extracted_char_count == 0:
            return RouteDecision(
                compute_path=ComputePath.DEEP,
                reason_code=REASON_NO_TEXT,
                metrics_used={"extracted_char_count": float(metrics.extracted_char_count)},
            )
        if metrics.image_area_ratio >= self._config.deep_min_image_area_ratio:
            return RouteDecision(
                compute_path=ComputePath.DEEP,
                reason_code=REASON_HIGH_IMAGE_AREA,
                metrics_used={
                    "image_area_ratio": metrics.image_area_ratio,
                    "deep_min_image_area_ratio": self._config.deep_min_image_area_ratio,
                },
            )
        if metrics.image_area_ratio <= self._config.fast_max_image_area_ratio:
            return RouteDecision(
                compute_path=ComputePath.FAST,
                reason_code=REASON_LOW_IMAGE_AREA,
                metrics_used={
                    "image_area_ratio": metrics.image_area_ratio,
                    "fast_max_image_area_ratio": self._config.fast_max_image_area_ratio,
                },
            )
        return RouteDecision(
            compute_path=ComputePath.STANDARD,
            reason_code=REASON_DEFAULT_STANDARD,
            metrics_used={"image_area_ratio": metrics.image_area_ratio},
        )
