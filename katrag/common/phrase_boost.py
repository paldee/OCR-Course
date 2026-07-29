"""Phrase boost — คูณคะแนน chunk ที่มีคำตรงกับ domain lexicon (R13.5).

อัลกอริทึมนี้เขียนใหม่เป็น Python จากแนวคิดใน `katgpt-rs`
(`crates/katgpt-pruners/src/phrase_boost.rs`) โดยใช้เฉพาะแนวคิด domain lexicon
boost; ไม่ใช้ cache ที่ไม่มีขอบเขตของต้นทาง — ดู `third_party/katgpt-rs-MIT-NOTICE.md`
ไม่มีการ import จาก `katgpt-rs/` (R20.4, R20.5)

MIT License — Copyright (c) 2026 Todsaporn Banjerdkit
See: third_party/katgpt-rs-MIT-NOTICE.md

ข้อบังคับ:
- ไม่เพิ่ม/ลบ chunk ออกจากชุดผลลัพธ์ — output มีจำนวนสมาชิกเท่ากับ input เสมอ
- เทียบคำหลัง normalize (NFC + squeeze whitespace) แล้วตรวจว่า term ปรากฏเป็น substring
- ตัวคูณต่อ chunk = ผลคูณของ boost ทุก category ที่ match (capped ที่ max_total_multiplier)
"""

from __future__ import annotations

import unicodedata
import re
from typing import Any, Mapping, Sequence

# ── normalization สำหรับ phrase matching ─────────────────────────────

_WHITESPACE_RUN_RE = re.compile(r"\s+")


def _normalize_for_match(text: str) -> str:
    """NFC + squeeze whitespace — ใช้สำหรับเทียบ phrase boost เท่านั้น."""
    nfc = unicodedata.normalize("NFC", text)
    return _WHITESPACE_RUN_RE.sub(" ", nfc).strip()


# ── core ──────────────────────────────────────────────────────────────


def compute_boost_multiplier(
    text: str,
    lexicon_terms: Mapping[str, Sequence[str]],
    boost_weights: Mapping[str, float],
    max_total_multiplier: float = 3.0,
) -> float:
    """คำนวณตัวคูณ phrase boost สำหรับ chunk หนึ่งอัน.

    Args:
        text: ข้อความของ chunk (ก่อน normalize — ฟังก์ชันจะ normalize เอง)
        lexicon_terms: dict ของ category -> list ของ term
                       เช่น {"credit_term": ["หน่วยกิต", "credit"]}
        boost_weights: dict ของ category -> ตัวคูณ เช่น {"credit_term": 1.20}
        max_total_multiplier: ขีดจำกัดสูงสุดของตัวคูณรวม (R13.5: 1.00-3.00)

    Returns:
        ตัวคูณรวม (>= 1.0, <= max_total_multiplier)
    """
    normalized_text = _normalize_for_match(text)
    multiplier = 1.0

    for category, terms in lexicon_terms.items():
        weight = boost_weights.get(category)
        if weight is None or weight <= 1.0:
            continue
        # เทียบว่ามี term ใด ๆ ใน category นี้ปรากฏเป็น substring
        for term in terms:
            normalized_term = _normalize_for_match(term)
            if not normalized_term:
                continue
            if normalized_term in normalized_text:
                multiplier *= weight
                break  # นับแค่ครั้งเดียวต่อ category

    return min(multiplier, max_total_multiplier)


def apply_phrase_boost(
    scored_chunks: Sequence[tuple[str, float]],
    chunk_texts: Mapping[str, str],
    domain_lexicon: Mapping[str, Any],
    phrase_boost_multiplier: float = 1.35,
) -> list[tuple[str, float]]:
    """คูณคะแนน chunk ที่มีคำตรงกับ domain lexicon ด้วยตัวคูณจากไฟล์ตั้งค่า.

    Args:
        scored_chunks: รายการ (chunk_id, score) — ลำดับเดิมคงไว้
        chunk_texts: dict ของ chunk_id -> ข้อความของ chunk
        domain_lexicon: ข้อมูลจาก config/domain_lexicon.toml
                        ต้องมี key "terms" (dict category->list[str])
                        และ "boost" (dict category->float + "max_total_multiplier")
        phrase_boost_multiplier: ตัวคูณ global จาก config (R13.5: 1.00-3.00, default 1.35)
                                 ใช้เป็น fallback เมื่อไม่มี per-category boost

    Returns:
        รายการ (chunk_id, score) ที่มีจำนวนเท่าเดิม ลำดับเดิม
        chunk ที่ match ได้คะแนนคูณตัวคูณ; chunk ที่ไม่ match คงเดิม

    ข้อบังคับ:
        - ห้ามเพิ่มหรือลบ chunk: len(output) == len(input) เสมอ
        - chunk_id ใน output ต้องเป็นชุดเดียวกับ input
    """
    lexicon_terms: Mapping[str, Sequence[str]] = domain_lexicon.get("terms", {})
    boost_section: Mapping[str, Any] = domain_lexicon.get("boost", {})

    # สร้าง boost weights: ใช้ per-category boost จาก [boost] section
    # ถ้าไม่มี per-category → ใช้ phrase_boost_multiplier เป็น uniform weight
    boost_weights: dict[str, float] = {}
    for category in lexicon_terms:
        weight = boost_section.get(category)
        if isinstance(weight, (int, float)) and weight >= 1.0:
            boost_weights[category] = float(weight)
        else:
            boost_weights[category] = phrase_boost_multiplier

    max_total = float(boost_section.get("max_total_multiplier", 3.0))

    result: list[tuple[str, float]] = []
    for chunk_id, score in scored_chunks:
        text = chunk_texts.get(chunk_id, "")
        if text:
            multiplier = compute_boost_multiplier(
                text, lexicon_terms, boost_weights, max_total
            )
            result.append((chunk_id, score * multiplier))
        else:
            result.append((chunk_id, score))

    return result
