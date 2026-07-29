"""Content addressing — sha256 hex ตัวพิมพ์เล็ก 64 อักขระ (R1.1, R9.6).

ทุก identity ในระบบ (document, curriculum version, page, chunk, crop, cache key)
ใช้ฟังก์ชันในไฟล์นี้เท่านั้น เพื่อให้การคำนวณซ้ำจากเนื้อหาเดิมได้ค่าเท่าเดิมทุกครั้ง
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

SHA256_HEX_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_READ_CHUNK_BYTES = 1024 * 1024  # 1 MiB — ไฟล์ PDF ใหญ่ถึง 245 MB จึงต้องอ่านเป็นช่วง


def is_sha256_hex(value: str) -> bool:
    """คืน True เมื่อ value เป็น sha256 hex ตัวพิมพ์เล็ก 64 อักขระ."""
    return bool(SHA256_HEX_PATTERN.match(value))


def sha256_hex(data: bytes) -> str:
    """sha256 ของ bytes → hex ตัวพิมพ์เล็ก 64 อักขระ."""
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    """sha256 ของข้อความ โดย encode เป็น UTF-8 เสมอ (ผลไม่ขึ้นกับ locale)."""
    return sha256_hex(text.encode("utf-8"))


def sha256_file(path: str | Path) -> str:
    """sha256 ของไฟล์ อ่านแบบ streaming เพื่อไม่ให้หน่วยความจำโตตามขนาดไฟล์ (R6)."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            block = handle.read(_READ_CHUNK_BYTES)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def sha256_parts(parts: Iterable[Any]) -> str:
    """sha256 ของลำดับค่า โดยคั่นด้วย separator ที่ไม่ปรากฏในข้อมูล.

    ใช้สำหรับ cache key ที่ต้องรวมหลายองค์ประกอบ เช่น crop cache และ answer cache
    (R5.11, R10.10) การใส่ separator ป้องกันการชนกันของ ("ab", "c") กับ ("a", "bc")
    """
    digest = hashlib.sha256()
    for part in parts:
        digest.update(_canonical_bytes(part))
        digest.update(b"\x1f")  # unit separator
    return digest.hexdigest()


def canonical_json(value: Any) -> str:
    """JSON ที่ deterministic: เรียงคีย์ ไม่ escape อักขระไทย ไม่มีช่องว่างเกิน.

    ใช้กับ dataset manifest และ evaluation report ที่ต้องผลิตซ้ำได้เนื้อหาเดิม
    (R1.9, R18.7)
    """
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_mapping(mapping: Mapping[str, Any]) -> str:
    """sha256 ของ mapping ผ่าน canonical JSON — ลำดับคีย์ไม่มีผลต่อค่า hash."""
    return sha256_text(canonical_json(mapping))


def _canonical_bytes(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, bool):
        return b"true" if value else b"false"
    if isinstance(value, int):
        return str(value).encode("utf-8")
    if isinstance(value, float):
        # repr ของ float ใน Python 3 เป็น round-trip ได้ จึง deterministic
        return repr(value).encode("utf-8")
    if isinstance(value, str):
        return value.encode("utf-8")
    if value is None:
        return b"null"
    if isinstance(value, Mapping):
        return canonical_json(dict(value)).encode("utf-8")
    if isinstance(value, (list, tuple)):
        return canonical_json(list(value)).encode("utf-8")
    raise TypeError(f"ไม่รองรับชนิด {type(value).__name__} ใน cache key")
