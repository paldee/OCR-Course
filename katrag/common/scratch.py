"""Caller-owned reusable buffers — คุมหน่วยความจำของ ingestion (R6.1-R6.3).

บทเรียนจาก Lab_Week3: `pdf2image.convert_from_path` materialize ทุกหน้าเป็น list
ทำให้ RAM โตตามจำนวนหน้า การออกแบบใหม่จึงบังคับสองข้อ

1. **จำนวน page image ที่ถืออยู่พร้อมกันมีเพดานคงที่** (ค่าตั้งต้น 2 slot)
   โดย `page_slot()` เป็น context manager ที่คืน slot ใน `finally` เสมอ
2. **buffer ถูกใช้ซ้ำ** ไม่จัดสรรใหม่ต่อหน้า ฟังก์ชันหนักทุกตัวรับ `out`
   จากผู้เรียก (รูปแบบ `*_into`) จึงไม่มี allocation ใน hot path

แนวคิด caller-owned scratch buffer ปรับมาจาก `katgpt-rs`
(`PkmScratch`, `matvec_into`, `encode_vector_into`) — ดู
`third_party/katgpt-rs-MIT-NOTICE.md` และไม่มีการ import จาก `katgpt-rs/`
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator

import numpy as np

_BYTES_PER_RGB_PIXEL = 3


@dataclass(slots=True)
class PageSlot:
    """หนึ่ง slot ที่ถือ buffer ของหนึ่งหน้า และถูกใช้ซ้ำข้ามหน้า."""

    index: int
    capacity_bytes: int
    _buffer: bytearray = field(repr=False)
    _in_use: bool = False
    _current_shape: tuple[int, int, int] | None = None

    @classmethod
    def allocate(cls, index: int, capacity_bytes: int) -> "PageSlot":
        if capacity_bytes <= 0:
            raise ValueError("capacity_bytes ต้องมากกว่า 0")
        return cls(index=index, capacity_bytes=capacity_bytes, _buffer=bytearray(capacity_bytes))

    @property
    def in_use(self) -> bool:
        return self._in_use

    @property
    def shape(self) -> tuple[int, int, int] | None:
        return self._current_shape

    def view(self, height: int, width: int, channels: int = _BYTES_PER_RGB_PIXEL) -> np.ndarray:
        """คืน numpy view บน buffer เดิม โดยไม่ copy และไม่จัดสรรใหม่.

        Raises:
            ValueError: เมื่อขนาดที่ขอเกิน capacity ของ slot
        """
        needed = height * width * channels
        if needed <= 0:
            raise ValueError("ขนาดภาพต้องมากกว่า 0")
        if needed > self.capacity_bytes:
            raise ValueError(
                f"ขนาดภาพ {needed} ไบต์เกิน capacity ของ slot ({self.capacity_bytes} ไบต์)"
            )
        self._current_shape = (height, width, channels)
        return np.frombuffer(memoryview(self._buffer)[:needed], dtype=np.uint8).reshape(
            height, width, channels
        )

    def write(self, data: bytes, height: int, width: int, channels: int = _BYTES_PER_RGB_PIXEL) -> np.ndarray:
        """คัดลอก raster เข้า buffer ที่มีอยู่ แล้วคืน view (ไม่จัดสรรใหม่)."""
        needed = height * width * channels
        if len(data) != needed:
            raise ValueError(f"ขนาดข้อมูล {len(data)} ไม่ตรงกับ {needed} ที่คาดไว้")
        if needed > self.capacity_bytes:
            raise ValueError("ข้อมูลเกิน capacity ของ slot")
        self._buffer[:needed] = data
        return self.view(height, width, channels)

    def release(self) -> None:
        """คืน slot ให้ pool โดยไม่คืนหน่วยความจำ (คง buffer ไว้ใช้ซ้ำ)."""
        self._in_use = False
        self._current_shape = None


class PageBufferPool:
    """pool ขนาดคงที่ของ PageSlot — เพดานเดียวที่คุม resident page image (R6.1).

    Args:
        max_slots: จำนวน slot สูงสุด (จาก `memory.max_resident_page_images`)
        slot_capacity_bytes: ขนาด buffer ต่อ slot คำนวณจาก DPI สูงสุดและขนาดหน้าใหญ่สุด
    """

    __slots__ = ("_slots", "_lock", "_max_slots", "_peak_in_use")

    def __init__(self, max_slots: int, slot_capacity_bytes: int) -> None:
        if max_slots < 1:
            raise ValueError("max_slots ต้องไม่น้อยกว่า 1")
        self._max_slots = max_slots
        self._slots = [PageSlot.allocate(i, slot_capacity_bytes) for i in range(max_slots)]
        self._lock = threading.Lock()
        self._peak_in_use = 0

    @classmethod
    def for_pages(
        cls,
        max_slots: int,
        *,
        max_dpi: int = 300,
        max_page_width_pt: float = 612.0,
        max_page_height_pt: float = 1008.0,
    ) -> "PageBufferPool":
        """สร้าง pool ที่ขนาด slot คำนวณจาก DPI และขนาดหน้าที่ใหญ่สุดที่รองรับ.

        A4 ที่ 300 DPI ประมาณ 25 MB/หน้า ดังนั้น 2 slot ประมาณ 50 MB
        ซึ่งห่างจากเพดาน 6 GB มาก (design §3.5)
        """
        scale = max_dpi / 72.0
        width_px = int(max_page_width_pt * scale) + 1
        height_px = int(max_page_height_pt * scale) + 1
        capacity = width_px * height_px * _BYTES_PER_RGB_PIXEL
        return cls(max_slots=max_slots, slot_capacity_bytes=capacity)

    @property
    def max_slots(self) -> int:
        return self._max_slots

    @property
    def in_use_count(self) -> int:
        with self._lock:
            return sum(1 for slot in self._slots if slot.in_use)

    @property
    def peak_in_use(self) -> int:
        """จำนวน slot สูงสุดที่ถูกใช้พร้อมกัน — ต้องไม่เกิน max_slots (property test)."""
        return self._peak_in_use

    @property
    def total_capacity_bytes(self) -> int:
        return sum(slot.capacity_bytes for slot in self._slots)

    @contextmanager
    def page_slot(self) -> Iterator[PageSlot]:
        """ยืม slot หนึ่งอัน และคืนใน `finally` เสมอ (R6.2).

        Raises:
            RuntimeError: เมื่อไม่มี slot ว่าง ซึ่งหมายความว่าผู้เรียกละเมิดเพดาน
                          ที่ตั้งไว้ — ถือเป็น bug ไม่ใช่สภาวะปกติ
        """
        slot = self._acquire()
        try:
            yield slot
        finally:
            with self._lock:
                slot.release()

    def _acquire(self) -> PageSlot:
        with self._lock:
            for slot in self._slots:
                if not slot.in_use:
                    slot._in_use = True
                    in_use = sum(1 for item in self._slots if item.in_use)
                    if in_use > self._peak_in_use:
                        self._peak_in_use = in_use
                    return slot
        raise RuntimeError(
            f"ไม่มี page slot ว่าง (เพดาน {self._max_slots} slot) — "
            "ผู้เรียกถือหน้าไว้พร้อมกันเกินเพดานที่ตั้งไว้"
        )


@dataclass(slots=True)
class VectorScratch:
    """buffer ที่ใช้ซ้ำสำหรับงานเวกเตอร์ (dense scan, MaxSim rerank).

    ผู้เรียกเป็นเจ้าของ buffer และส่งเข้าไปเป็น `out` ทุกครั้ง
    """

    scores: np.ndarray
    indices: np.ndarray

    @classmethod
    def allocate(cls, capacity: int) -> "VectorScratch":
        if capacity <= 0:
            raise ValueError("capacity ต้องมากกว่า 0")
        return cls(
            scores=np.zeros(capacity, dtype=np.float32),
            indices=np.zeros(capacity, dtype=np.int64),
        )

    @property
    def capacity(self) -> int:
        return int(self.scores.shape[0])

    def score_view(self, size: int) -> np.ndarray:
        """คืน view ขนาด `size` บน buffer เดิม — ไม่จัดสรรใหม่."""
        if size < 0 or size > self.capacity:
            raise ValueError(f"size {size} เกิน capacity {self.capacity}")
        return self.scores[:size]

    def reset(self) -> None:
        self.scores.fill(0.0)
        self.indices.fill(0)
