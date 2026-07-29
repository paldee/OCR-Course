"""Unit tests for katrag.common.phrase_boost (R13.5).

ทดสอบ:
- ตัวคูณคำนวณถูกต้องเมื่อ chunk มีคำตรงกับ lexicon
- ไม่เพิ่ม/ลบ chunk — output ต้องมีจำนวนและ chunk_id เท่ากับ input
- chunk ที่ไม่มี text ไม่ถูก boost
- normalize ก่อนเทียบ (NFC + squeeze whitespace)
- max_total_multiplier cap
- multiple categories ให้ผลคูณ
"""

from __future__ import annotations

import pytest

from katrag.common.phrase_boost import (
    apply_phrase_boost,
    compute_boost_multiplier,
    _normalize_for_match,
)


# ── test data ─────────────────────────────────────────────────────────

SAMPLE_LEXICON = {
    "terms": {
        "credit_term": ["หน่วยกิต", "credit"],
        "requirement_term": ["วิชาบังคับ", "วิชาเลือก"],
        "program_term": ["เทคโนโลยีสารสนเทศ"],
    },
    "boost": {
        "credit_term": 1.20,
        "requirement_term": 1.25,
        "program_term": 1.30,
        "max_total_multiplier": 3.00,
    },
}


# ── normalization ─────────────────────────────────────────────────────


class TestNormalization:
    """การ normalize ก่อนเทียบ."""

    def test_nfc_normalization(self) -> None:
        """ข้อความ NFD ถูก normalize เป็น NFC."""
        # ก้ as NFD (ก + ้) vs NFC (ก้)
        nfd = "\u0e01\u0e49"  # ก + ้
        result = _normalize_for_match(nfd)
        assert result == "\u0e01\u0e49"  # stays the same for Thai

    def test_squeeze_whitespace(self) -> None:
        """Whitespace ที่ซ้อนกันถูกบีบเป็นช่องว่างเดียว."""
        text = "หน่วยกิต   สาม   (3)"
        result = _normalize_for_match(text)
        assert result == "หน่วยกิต สาม (3)"

    def test_strip_leading_trailing(self) -> None:
        """ช่องว่างหัวท้ายถูกตัด."""
        text = "   หน่วยกิต   "
        result = _normalize_for_match(text)
        assert result == "หน่วยกิต"


# ── compute_boost_multiplier ──────────────────────────────────────────


class TestComputeBoostMultiplier:
    """คำนวณตัวคูณ phrase boost."""

    def test_no_match_returns_1(self) -> None:
        """ไม่มี term ใดตรง → multiplier = 1.0."""
        text = "ข้อความที่ไม่เกี่ยวข้อง"
        terms = {"credit_term": ["หน่วยกิต"]}
        weights = {"credit_term": 1.20}

        result = compute_boost_multiplier(text, terms, weights)

        assert result == 1.0

    def test_single_category_match(self) -> None:
        """มี term ตรงหนึ่ง category → คูณด้วย weight ของ category นั้น."""
        text = "รายวิชานี้มี 3 หน่วยกิต"
        terms = {"credit_term": ["หน่วยกิต"]}
        weights = {"credit_term": 1.20}

        result = compute_boost_multiplier(text, terms, weights)

        assert abs(result - 1.20) < 1e-10

    def test_multiple_category_match(self) -> None:
        """มี term ตรงหลาย category → ผลคูณ weights."""
        text = "วิชาบังคับ 3 หน่วยกิต"
        terms = {
            "credit_term": ["หน่วยกิต"],
            "requirement_term": ["วิชาบังคับ"],
        }
        weights = {"credit_term": 1.20, "requirement_term": 1.25}

        result = compute_boost_multiplier(text, terms, weights)

        expected = 1.20 * 1.25
        assert abs(result - expected) < 1e-10

    def test_multiple_terms_same_category_count_once(self) -> None:
        """category เดียวกันมีหลาย term ตรง → นับแค่ครั้งเดียว."""
        text = "หน่วยกิต credit credits"
        terms = {"credit_term": ["หน่วยกิต", "credit", "credits"]}
        weights = {"credit_term": 1.20}

        result = compute_boost_multiplier(text, terms, weights)

        # นับแค่ครั้งเดียว ไม่ว่าจะมีกี่ term ตรง
        assert abs(result - 1.20) < 1e-10

    def test_max_total_multiplier_cap(self) -> None:
        """ผลคูณไม่เกิน max_total_multiplier."""
        text = "วิชาบังคับ หน่วยกิต เทคโนโลยีสารสนเทศ"
        terms = {
            "credit_term": ["หน่วยกิต"],
            "requirement_term": ["วิชาบังคับ"],
            "program_term": ["เทคโนโลยีสารสนเทศ"],
        }
        # 1.5 * 1.5 * 1.5 = 3.375 > 2.0
        weights = {"credit_term": 1.50, "requirement_term": 1.50, "program_term": 1.50}
        max_cap = 2.0

        result = compute_boost_multiplier(text, terms, weights, max_total_multiplier=max_cap)

        assert result == 2.0

    def test_weight_below_1_ignored(self) -> None:
        """weight <= 1.0 ไม่ถูกนำมาคูณ."""
        text = "หน่วยกิต"
        terms = {"credit_term": ["หน่วยกิต"]}
        weights = {"credit_term": 0.9}

        result = compute_boost_multiplier(text, terms, weights)

        assert result == 1.0

    def test_empty_text(self) -> None:
        """ข้อความว่าง → multiplier = 1.0."""
        text = ""
        terms = {"credit_term": ["หน่วยกิต"]}
        weights = {"credit_term": 1.20}

        result = compute_boost_multiplier(text, terms, weights)

        assert result == 1.0

    def test_substring_match(self) -> None:
        """เทียบแบบ substring — ไม่ต้องตรงทั้งคำ."""
        text = "จำนวนหน่วยกิตรวม"
        terms = {"credit_term": ["หน่วยกิต"]}
        weights = {"credit_term": 1.20}

        result = compute_boost_multiplier(text, terms, weights)

        assert abs(result - 1.20) < 1e-10

    def test_whitespace_normalized_before_match(self) -> None:
        """ช่องว่างซ้อนใน text ถูก normalize ก่อนเทียบ."""
        text = "หน่วย   กิต"  # ช่องว่างซ้อน
        # term "หน่วย กิต" จะไม่ตรง เพราะ text normalized เป็น "หน่วย กิต"
        # แต่ถ้า term คือ "หน่วย กิต" → ต้องตรง
        terms = {"credit_term": ["หน่วย กิต"]}
        weights = {"credit_term": 1.20}

        result = compute_boost_multiplier(text, terms, weights)

        assert abs(result - 1.20) < 1e-10


# ── apply_phrase_boost ────────────────────────────────────────────────


class TestApplyPhraseBoost:
    """ทดสอบ apply_phrase_boost แบบ end-to-end."""

    def test_output_length_equals_input(self) -> None:
        """จำนวน chunk ใน output ต้องเท่ากับ input."""
        scored = [("c1", 0.5), ("c2", 0.3), ("c3", 0.8)]
        texts = {"c1": "หน่วยกิต", "c2": "ข้อความอื่น", "c3": "วิชาบังคับ"}

        result = apply_phrase_boost(scored, texts, SAMPLE_LEXICON)

        assert len(result) == len(scored)

    def test_output_chunk_ids_unchanged(self) -> None:
        """ชุด chunk_id ใน output ต้องเหมือน input ทุกประการ."""
        scored = [("c1", 0.5), ("c2", 0.3), ("c3", 0.8)]
        texts = {"c1": "หน่วยกิต", "c2": "ข้อความอื่น", "c3": "วิชาบังคับ"}

        result = apply_phrase_boost(scored, texts, SAMPLE_LEXICON)

        input_ids = [cid for cid, _ in scored]
        output_ids = [cid for cid, _ in result]
        assert output_ids == input_ids

    def test_order_preserved(self) -> None:
        """ลำดับ chunk ใน output ต้องเหมือน input."""
        scored = [("c1", 0.5), ("c2", 0.3), ("c3", 0.8)]
        texts = {"c1": "test", "c2": "test", "c3": "test"}

        result = apply_phrase_boost(scored, texts, SAMPLE_LEXICON)

        assert [cid for cid, _ in result] == ["c1", "c2", "c3"]

    def test_matching_chunk_boosted(self) -> None:
        """chunk ที่มี term ตรง → คะแนนถูกคูณ."""
        scored = [("c1", 1.0)]
        texts = {"c1": "รายวิชานี้มี 3 หน่วยกิต"}

        result = apply_phrase_boost(scored, texts, SAMPLE_LEXICON)

        # credit_term boost = 1.20
        assert result[0][0] == "c1"
        assert abs(result[0][1] - 1.20) < 1e-10

    def test_non_matching_chunk_unchanged(self) -> None:
        """chunk ที่ไม่มี term ตรง → คะแนนคงเดิม."""
        scored = [("c1", 0.75)]
        texts = {"c1": "ข้อความที่ไม่เกี่ยวข้องกับ lexicon ใดๆ"}

        result = apply_phrase_boost(scored, texts, SAMPLE_LEXICON)

        assert result[0][0] == "c1"
        assert abs(result[0][1] - 0.75) < 1e-10

    def test_missing_text_not_boosted(self) -> None:
        """chunk ที่ไม่มี text ใน chunk_texts → คะแนนคงเดิม."""
        scored = [("c_missing", 0.6)]
        texts: dict[str, str] = {}  # ไม่มี text สำหรับ c_missing

        result = apply_phrase_boost(scored, texts, SAMPLE_LEXICON)

        assert result[0][1] == 0.6

    def test_empty_input_returns_empty(self) -> None:
        """input ว่าง → output ว่าง."""
        result = apply_phrase_boost([], {}, SAMPLE_LEXICON)
        assert result == []

    def test_empty_lexicon_no_boost(self) -> None:
        """lexicon ว่าง → ไม่มีการ boost."""
        scored = [("c1", 0.5)]
        texts = {"c1": "หน่วยกิต"}
        empty_lexicon: dict[str, object] = {"terms": {}, "boost": {}}

        result = apply_phrase_boost(scored, texts, empty_lexicon)

        assert result[0][1] == 0.5

    def test_fallback_to_phrase_boost_multiplier(self) -> None:
        """ถ้า [boost] ไม่มี weight สำหรับ category → ใช้ phrase_boost_multiplier."""
        scored = [("c1", 1.0)]
        texts = {"c1": "หน่วยกิต"}
        # lexicon มี terms แต่ boost section ไม่มี weight สำหรับ credit_term
        lexicon = {"terms": {"credit_term": ["หน่วยกิต"]}, "boost": {}}

        result = apply_phrase_boost(
            scored, texts, lexicon, phrase_boost_multiplier=1.35
        )

        assert abs(result[0][1] - 1.35) < 1e-10

    def test_real_lexicon_structure(self) -> None:
        """ทดสอบกับโครงสร้างจริงของ domain_lexicon.toml."""
        scored = [
            ("c1", 0.8),  # มี "หน่วยกิต" + "วิชาบังคับ"
            ("c2", 0.6),  # ไม่มีอะไรตรง
        ]
        texts = {
            "c1": "วิชาบังคับ 3 หน่วยกิต (3-0-6)",
            "c2": "บทที่ 1 แนะนำรายวิชา",
        }

        result = apply_phrase_boost(scored, texts, SAMPLE_LEXICON)

        # c1: credit_term(1.20) * requirement_term(1.25) = 1.50
        assert abs(result[0][1] - 0.8 * 1.20 * 1.25) < 1e-10
        # c2: no boost
        assert abs(result[1][1] - 0.6) < 1e-10
