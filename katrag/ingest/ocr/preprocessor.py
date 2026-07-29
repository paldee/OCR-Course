"""`Preprocessor` — ปรับภาพก่อน OCR เฉพาะเมื่อเข้าเงื่อนไข (design §4.10, R5.7-R5.9).

เงื่อนไขเปิดใช้ (OR ข้อใดข้อหนึ่งเป็นจริง):
1. มุมเอียง > `skew_degrees_threshold` (1.0°)
2. DPI < `min_dpi` (300)
3. contrast score < `contrast_score_threshold` (0.30)

ถ้าไม่มีเงื่อนไขใดเป็นจริง → ส่งภาพต้นฉบับเข้า OCR โดยไม่ปรับ (R5.9)
ผลก่อน/หลังปรับถูกเปรียบเทียบคะแนน → เลือกค่าสูงกว่า เสมอกันเลือกก่อนปรับ (R5.8)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np

from katrag.common.types import BBox
from katrag.config import PreprocessConfig


@dataclass(frozen=True, slots=True)
class PreprocessOutcome:
    """ผลของการพิจารณา/ปรับภาพ."""

    applied_steps: tuple[str, ...]  # รายการว่างเมื่อไม่ปรับ (R5.7)
    image: np.ndarray  # ภาพที่ปรับแล้ว (หรือต้นฉบับถ้าไม่ปรับ)
    was_applied: bool  # True เมื่อปรับจริง


class Preprocessor:
    """ปรับภาพก่อน OCR เฉพาะเมื่อเข้าเงื่อนไข (R5.9)."""

    def __init__(self, config: PreprocessConfig) -> None:
        self._config = config

    def should_apply(self, image: np.ndarray, region: BBox) -> tuple[bool, Mapping[str, float]]:
        """ตรวจว่าภาพเข้าเงื่อนไขหรือไม่ — คืน (ต้องปรับ, metrics ที่วัดได้).

        metrics ใช้สำหรับ provenance/debug ไม่จำเป็นต้องแม่นยำ 100%
        """
        skew = _estimate_skew_degrees(image)
        dpi = _estimate_dpi(image, region)
        contrast = _compute_contrast_score(image)

        metrics: dict[str, float] = {
            "skew_degrees": skew,
            "estimated_dpi": dpi,
            "contrast_score": contrast,
        }

        apply = (
            skew > self._config.skew_degrees_threshold
            or dpi < self._config.min_dpi
            or contrast < self._config.contrast_score_threshold
        )
        return apply, metrics

    def apply(self, image: np.ndarray, region: BBox) -> PreprocessOutcome:
        """ปรับภาพถ้าเข้าเงื่อนไข — คืน PreprocessOutcome.

        ถ้าไม่เข้าเงื่อนไข คืนภาพต้นฉบับพร้อม applied_steps ว่าง
        """
        need, metrics = self.should_apply(image, region)
        if not need:
            return PreprocessOutcome(applied_steps=(), image=image, was_applied=False)

        steps: list[str] = []
        result = image.copy()

        # Step 1: deskew
        if metrics["skew_degrees"] > self._config.skew_degrees_threshold:
            result = _deskew(result, metrics["skew_degrees"])
            steps.append("deskew")

        # Step 2: upscale (DPI ต่ำ)
        if metrics["estimated_dpi"] < self._config.min_dpi:
            scale = self._config.min_dpi / max(1.0, metrics["estimated_dpi"])
            result = _upscale(result, scale)
            steps.append("upscale")

        # Step 3: contrast enhancement
        if metrics["contrast_score"] < self._config.contrast_score_threshold:
            result = _enhance_contrast(result)
            steps.append("contrast_enhance")

        return PreprocessOutcome(
            applied_steps=tuple(steps),
            image=result,
            was_applied=True,
        )


# ── internal helpers ──────────────────────────────────────────────────


def _estimate_skew_degrees(image: np.ndarray) -> float:
    """ประมาณมุมเอียงจาก Hough lines — คืน degrees (0 = ตรง)."""
    try:
        import cv2
    except ImportError:  # pragma: no cover
        return 0.0

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    edges = cv2.Canny(gray, 50, 150, apertureSize=3)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=100, minLineLength=50, maxLineGap=10)
    if lines is None or len(lines) == 0:
        return 0.0

    angles: list[float] = []
    for line in lines:
        x1, y1, x2, y2 = line[0]
        if x2 - x1 == 0:
            continue
        angle = np.degrees(np.arctan2(y2 - y1, x2 - x1))
        # เก็บเฉพาะมุมเล็ก ๆ (ใกล้แนวนอน) ที่น่าจะเป็นบรรทัดข้อความ
        if abs(angle) < 15.0:
            angles.append(angle)

    if not angles:
        return 0.0
    return float(np.median(angles))


def _estimate_dpi(image: np.ndarray, region: BBox) -> float:
    """ประมาณ DPI จากขนาด region vs pixel — heuristic ง่ายสุด.

    ใช้ A4 width (210mm ≈ 8.27 inch) เป็น reference เมื่อ region กว้างเต็มหน้า
    ถ้า region เล็กมากให้สมมติ 300 DPI (ไม่ trigger upscale)
    """
    region_width_px = region.x1 - region.x0
    # สมมติ A4 หน้ากว้าง 8.27 inch
    if region_width_px < 100:
        return 300.0  # too small to estimate
    # ถ้า region กว้างใกล้เคียง full page width ของภาพ
    image_width = image.shape[1] if image.ndim >= 2 else 1
    coverage = region_width_px / max(1, image_width)
    if coverage > 0.7:
        # region ครอบเกือบทั้งหน้า → ใช้ image width เป็นฐานคำนวณ
        return float(image_width) / 8.27
    return 300.0  # ไม่แน่ใจ → สมมติเพียงพอ


def _compute_contrast_score(image: np.ndarray) -> float:
    """คะแนน contrast 0.0-1.0 จาก standard deviation ของ grayscale."""
    try:
        import cv2
    except ImportError:  # pragma: no cover
        return 1.0

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    std = float(np.std(gray))
    # normalize: std=0 → 0.0, std>=80 → 1.0 (saturation)
    return min(1.0, std / 80.0)


def _deskew(image: np.ndarray, angle_degrees: float) -> np.ndarray:
    """หมุนภาพเพื่อแก้ skew."""
    try:
        import cv2
    except ImportError:  # pragma: no cover
        return image

    h, w = image.shape[:2]
    center = (w // 2, h // 2)
    matrix = cv2.getRotationMatrix2D(center, angle_degrees, 1.0)
    return cv2.warpAffine(image, matrix, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def _upscale(image: np.ndarray, scale: float) -> np.ndarray:
    """ขยายภาพตาม scale factor."""
    try:
        import cv2
    except ImportError:  # pragma: no cover
        return image

    if scale <= 1.0:
        return image
    h, w = image.shape[:2]
    new_w = max(1, int(w * scale))
    new_h = max(1, int(h * scale))
    return cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_CUBIC)


def _enhance_contrast(image: np.ndarray) -> np.ndarray:
    """CLAHE contrast enhancement."""
    try:
        import cv2
    except ImportError:  # pragma: no cover
        return image

    if image.ndim == 3:
        lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        lab[:, :, 0] = clahe.apply(lab[:, :, 0])
        return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
    else:
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        return clahe.apply(image)
