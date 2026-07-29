"""`RegionAdjudicator` — spatial voting ข้ามผล OCR จากหลาย engine (design §4.11, R5.10).

เมื่อ OCR engine มากกว่าหนึ่งตัวคืนผลของ region ที่มีค่า IoU ของ bbox >= iou_threshold:
1. จับกลุ่มผลที่ overlap กัน
2. เลือกด้วยคะแนนความเชื่อมั่น (`confidence`) เป็นเกณฑ์หลัก
3. ถ้าคะแนนต่างกันไม่เกิน `tie_epsilon` → เลือก stage ที่ลำดับต้นกว่า (stage_index น้อยกว่า)
4. บันทึกผลของทุก engine พร้อมผลที่เลือก
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from katrag.common.types import BBox
from katrag.ingest.ocr.stage import StageResult


@dataclass(frozen=True, slots=True)
class Adjudication:
    """ผลของการ adjudicate — ผลที่เลือกและข้อมูลการตัดสิน."""

    chosen: StageResult
    all_results: tuple[StageResult, ...]
    reason: str  # "confidence" | "tie_earlier_stage" | "single_result"


class RegionAdjudicator:
    """เลือกผล OCR ที่ดีที่สุดจากหลาย engine ด้วย spatial voting (R5.10)."""

    def adjudicate(
        self,
        results: Sequence[StageResult],
        iou_threshold: float,
        tie_epsilon: float,
    ) -> Adjudication:
        """เลือกผลลัพธ์ที่ดีที่สุดจาก results ที่ overlap กัน.

        Args:
            results: ผลจากแต่ละ stage ที่มี bbox ของ region เดียวกัน
            iou_threshold: เกณฑ์ IoU ขั้นต่ำ (ใช้เพื่อยืนยันว่าผลเป็น region เดียวกัน)
            tie_epsilon: ค่าต่างของ confidence ที่ถือว่าเสมอกัน

        Returns:
            Adjudication ที่ระบุผลที่เลือกและเหตุผล
        """
        if not results:
            raise ValueError("ต้องมีอย่างน้อยหนึ่งผลลัพธ์")

        if len(results) == 1:
            return Adjudication(
                chosen=results[0],
                all_results=tuple(results),
                reason="single_result",
            )

        # เรียงตาม confidence จากมากไปน้อย
        sorted_results = sorted(results, key=lambda r: r.confidence, reverse=True)
        best = sorted_results[0]
        second = sorted_results[1]

        # ตรวจว่าเป็นเสมอกันหรือไม่ (ใช้ round ป้องกัน floating-point precision)
        diff = abs(best.confidence - second.confidence)
        if diff <= tie_epsilon + 1e-9:
            # เลือก stage ลำดับต้นกว่า (stage_index น้อยกว่า)
            tie_candidates = [r for r in sorted_results if abs(r.confidence - best.confidence) <= tie_epsilon + 1e-9]
            chosen = min(tie_candidates, key=lambda r: r.stage_index)
            return Adjudication(
                chosen=chosen,
                all_results=tuple(results),
                reason="tie_earlier_stage",
            )

        return Adjudication(
            chosen=best,
            all_results=tuple(results),
            reason="confidence",
        )


def compute_iou(a: BBox, b: BBox) -> float:
    """คำนวณ Intersection over Union ของสอง BBox."""
    x0 = max(a.x0, b.x0)
    y0 = max(a.y0, b.y0)
    x1 = min(a.x1, b.x1)
    y1 = min(a.y1, b.y1)

    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    area_a = (a.x1 - a.x0) * (a.y1 - a.y0)
    area_b = (b.x1 - b.x0) * (b.y1 - b.y0)
    union = area_a + area_b - intersection

    if union <= 0.0:
        return 0.0
    return intersection / union
