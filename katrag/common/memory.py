"""MemoryMonitor — วัด resident memory ของ process ปัจจุบัน (design §3.5, R6.3, R6.5, R6.6).

ใช้ `psutil` เพราะเป็นวิธีข้ามแพลตฟอร์มมาตรฐานในการอ่าน RSS บน Windows/Linux/macOS
โดยไม่ต้องพึ่ง `resource.getrusage` ซึ่งไม่มีบน Windows

Drift gate (R6.3) ต้อง**ตั้ง baseline ครั้งเดียว** หลังหน้าที่กำหนดไว้ (ค่าเริ่มต้นหน้าที่ 50)
แล้วเทียบทุกหน้าถัดไปกับ baseline นั้น — ไม่ใช่เทียบกับหน้าก่อนหน้าทีละหน้า เพราะ RSS
มีความผันผวนปกติจากการจัดสรร/คืนหน่วยความจำของ Python และ PyMuPDF interpreter เอง
"""

from __future__ import annotations

from dataclasses import dataclass

import psutil


@dataclass(slots=True)
class MemoryMonitor:
    """ติดตาม RSS ของ process ปัจจุบันตลอด ingestion run หนึ่งครั้ง."""

    baseline_page_index: int
    drift_tolerance: float
    limit_bytes: int
    _process: psutil.Process
    _baseline_bytes: int | None = None
    _peak_bytes: int = 0
    _pages_observed: int = 0

    @classmethod
    def create(cls, *, baseline_page_index: int, drift_tolerance: float, limit_bytes: int) -> "MemoryMonitor":
        return cls(
            baseline_page_index=baseline_page_index,
            drift_tolerance=drift_tolerance,
            limit_bytes=limit_bytes,
            _process=psutil.Process(),
        )

    @property
    def peak_bytes(self) -> int:
        return self._peak_bytes

    @property
    def baseline_bytes(self) -> int | None:
        return self._baseline_bytes

    def resident_bytes(self) -> int:
        """RSS ปัจจุบันของ process (ไบต์) — ไม่มี side effect ต่อ state ของ monitor."""
        return int(self._process.memory_info().rss)

    def observe_page(self) -> "MemoryObservation":
        """เรียกหลังประมวลผลหนึ่งหน้าเสร็จ (R6.5) — อัปเดต baseline/peak แล้วตัดสิน drift/limit.

        ต้องเรียก **ครั้งเดียวต่อหน้า** ตามลำดับหน้าจริง เพราะ `baseline_page_index`
        นับจากจำนวนครั้งที่เรียกเมธอดนี้ ไม่ใช่จากเลขหน้าของเอกสาร (ใช้ได้ข้ามหลายเอกสาร)
        """
        self._pages_observed += 1
        rss = self.resident_bytes()
        self._peak_bytes = max(self._peak_bytes, rss)

        if self._baseline_bytes is None and self._pages_observed >= self.baseline_page_index:
            self._baseline_bytes = rss

        drift_ratio: float | None = None
        drift_exceeded = False
        if self._baseline_bytes is not None and self._baseline_bytes > 0:
            drift_ratio = (rss - self._baseline_bytes) / self._baseline_bytes
            drift_exceeded = drift_ratio > self.drift_tolerance

        limit_exceeded = rss > self.limit_bytes

        return MemoryObservation(
            resident_bytes=rss,
            baseline_bytes=self._baseline_bytes,
            drift_ratio=drift_ratio,
            drift_exceeded=drift_exceeded,
            limit_exceeded=limit_exceeded,
            peak_bytes=self._peak_bytes,
        )


@dataclass(frozen=True, slots=True)
class MemoryObservation:
    """ผลการวัด RSS ของหนึ่งหน้า."""

    resident_bytes: int
    baseline_bytes: int | None
    drift_ratio: float | None
    drift_exceeded: bool
    limit_exceeded: bool
    peak_bytes: int
