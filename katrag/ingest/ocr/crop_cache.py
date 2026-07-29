"""Crop_Cache — cache ผล OCR ต่อภาพ crop เดิม (design §4.9, R5.11).

คีย์คือ `(crop_sha256, engine, preprocess_step_sequence)` ตามที่ design กำหนดไว้ตรง ๆ
— crop เดียวกันที่ผ่าน preprocessing ต่างขั้นตอนกัน หรือถูกส่งให้ engine ต่างตัวกัน
ต้องไม่ชน cache กัน (R5.11)

ข้อบังคับ

1. **ไม่เกิน 2,000 รายการต่อเอกสาร** — LRU evict รายการที่เก่าสุดเมื่อเต็ม
2. **ล้างทั้งหมดเมื่อเปลี่ยนเอกสาร** — `crop_sha256` คำนวณจากเนื้อหาภาพเท่านั้น ไม่รวม
   document_id จึงมีโอกาสชนกันข้ามเอกสารได้ในทางทฤษฎี (ภาพเหมือนกันพอดี) การล้าง cache
   ทุกครั้งที่เปลี่ยนเอกสารเป็นการป้องกันสองชั้นที่ปลอดภัยกว่า ตรงตามที่ design ระบุไว้
3. **hit ต้องคืนผลเหมือนเดิมทุกฟิลด์** — เก็บ `StageResult` ทั้งก้อน ไม่ใช่แค่ text
   แล้วคืน copy ที่มี `cache_hit=True` แทนของเดิม (ฟิลด์อื่นต้องเหมือนต้นฉบับทุกตัว)
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, replace

from katrag.common.hashing import sha256_hex, sha256_parts
from katrag.ingest.ocr.stage import StageResult

#: เพดานรายการต่อเอกสาร (R5.11)
MAX_ENTRIES_PER_DOCUMENT = 2000


@dataclass(frozen=True, slots=True)
class CropCacheKey:
    """คีย์ของ crop cache — `(crop_sha256, engine, preprocess_step_sequence)` (R5.11)."""

    crop_sha256: str
    engine: str
    preprocess_steps: tuple[str, ...]

    def as_tuple(self) -> tuple[str, str, tuple[str, ...]]:
        return (self.crop_sha256, self.engine, self.preprocess_steps)


class CropCache:
    """LRU cache ของผล OCR ต่อ crop — ขอบเขตต่อเอกสาร (ล้างเมื่อเปลี่ยนเอกสาร)."""

    def __init__(self, max_entries: int = MAX_ENTRIES_PER_DOCUMENT) -> None:
        if max_entries < 1:
            raise ValueError("max_entries ต้องไม่น้อยกว่า 1")
        self._max_entries = max_entries
        self._current_document_id: str | None = None
        self._entries: OrderedDict[tuple[str, str, tuple[str, ...]], StageResult] = OrderedDict()
        self._hits = 0
        self._misses = 0

    @property
    def max_entries(self) -> int:
        return self._max_entries

    @property
    def size(self) -> int:
        return len(self._entries)

    @property
    def hits(self) -> int:
        return self._hits

    @property
    def misses(self) -> int:
        return self._misses

    def set_document(self, document_id: str) -> None:
        """ล้าง cache ทั้งหมดเมื่อเปลี่ยนเอกสาร (R5.11) — เรียกก่อนประมวลผลเอกสารใหม่ทุกครั้ง."""
        if document_id != self._current_document_id:
            self._entries.clear()
            self._current_document_id = document_id

    @staticmethod
    def crop_sha256(crop_bytes: bytes) -> str:
        """sha256 ของเนื้อหาภาพ crop (ไม่ใช่ path หรือ metadata) — ใช้ป้องกันตำแหน่งเดิม
        แต่เนื้อหาเปลี่ยน (เช่น หลัง preprocessing) ไม่ถูกมองว่าเป็น crop เดิม."""
        return sha256_hex(crop_bytes)

    def make_key(
        self, crop_sha256: str, engine: str, preprocess_steps: tuple[str, ...]
    ) -> CropCacheKey:
        return CropCacheKey(crop_sha256=crop_sha256, engine=engine, preprocess_steps=preprocess_steps)

    def get(self, key: CropCacheKey) -> StageResult | None:
        """คืนผลจาก cache พร้อม `cache_hit=True` — ฟิลด์อื่นเหมือนต้นฉบับทุกตัว (R5.11)."""
        raw_key = key.as_tuple()
        cached = self._entries.get(raw_key)
        if cached is None:
            self._misses += 1
            return None
        self._hits += 1
        self._entries.move_to_end(raw_key)  # LRU: recency update ตอนอ่านด้วย
        return replace(cached, cache_hit=True)

    def put(self, key: CropCacheKey, result: StageResult) -> None:
        """บันทึกผลลง cache — evict รายการเก่าสุดถ้าเต็ม (LRU, R5.11)."""
        raw_key = key.as_tuple()
        if raw_key in self._entries:
            self._entries.move_to_end(raw_key)
        self._entries[raw_key] = result
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def cache_key_digest(
        self, crop_sha256: str, engine: str, preprocess_steps: tuple[str, ...]
    ) -> str:
        """digest เดียวของคีย์ทั้งสามส่วน — สะดวกสำหรับ log/debug ไม่ใช่ตัวคีย์จริงที่ใช้ lookup."""
        return sha256_parts((crop_sha256, engine, *preprocess_steps))
