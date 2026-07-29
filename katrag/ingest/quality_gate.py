"""Page_Quality_Gate — ตัดสินคุณภาพหน้าและทำเครื่องหมาย OCR candidate (design §4.7, R4.1-R4.6).

หน้าที่ของชั้นนี้เกิดขึ้นหลัง Text_Extractor + Thai_Glyph_Reorderer + Line_Assembler
เสร็จเรียบร้อยแล้วเท่านั้น (บังคับลำดับตาม R2.1) — รับ `PageCharSet` (มี image_area_ratio
คำนวณไว้แล้วจาก Text_Extractor) และข้อความที่ประกอบบรรทัดแล้วเป็น input

ตัวชี้วัดสี่ตัวที่ประกอบเป็น `page_quality_score` (R4.1)

1. `extracted_char_count`      — จำนวนอักขระที่ดึงได้ (มากกว่า = คุณภาพดีกว่า)
2. `out_of_charset_ratio`      — สัดส่วนอักขระนอกชุดที่ประกาศไว้ (มากกว่า = แย่กว่า)
3. `image_area_ratio`          — สัดส่วนพื้นที่ภาพต่อหน้า (มากกว่า = แย่กว่า)
4. `domain_lexicon_match_count`— จำนวนคำที่ตรง lexicon (มากกว่า = ดีกว่า)

การ saturate ตัวชี้วัดที่ไม่มีเพดานตามธรรมชาติ (char_count, lexicon_match_count) ใช้ค่า
`char_count_reference` / `lexicon_match_reference` จากไฟล์ตั้งค่าเป็นตัวหาร แล้ว clamp ที่ 1.0
เพื่อให้ผลรวมทั้งสี่พจน์อยู่ในช่วง 0.00-1.00 เสมอ (น้ำหนักทั้งสี่ถูกตรวจแล้วว่ารวมได้ 1.0
ที่ `KatragConfig._validate`)

กฎ OCR candidate (R4.3-R4.6) เป็น **stateful ต่อ dataset** (ต้องรู้ว่าทำเครื่องหมายไปแล้ว
กี่หน้า) จึงแยกเป็น method `mark()` ที่รับ `candidates_so_far` แทนการนับภายในตัวเอง —
ทำให้ `score()` เป็น deterministic ต่อ input เดียวเสมอ (Property 17) และให้
Ingestion_Manager เป็นผู้ถือตัวนับสะสมของทั้ง dataset (ไม่ใช่ต่อเอกสาร)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping

from katrag.common.types import PageCharSet
from katrag.config import PageQualityConfig
from katrag.errors import ReviewIssue

#: เหตุผลของ candidate ที่เก็บลง store (design §4.7)
REASON_LOW_TEXT_WITH_IMAGE = "low_text_with_image"

#: เหตุผลของ review issue เมื่อไม่เป็น candidate ทั้งที่ข้อความน้อย (R4.4)
ISSUE_LOW_CONTENT_PAGE = "low_content_page"

#: เหตุผลของ review issue เมื่อโควตาหมด (R4.6)
ISSUE_OCR_BUDGET_EXHAUSTED = "ocr_budget_exhausted"


@dataclass(frozen=True, slots=True)
class DeclaredCharset:
    """ชุดอักขระที่ประกาศไว้สำหรับคำนวณ `out_of_charset_ratio` (มาจาก `domain_lexicon.toml`).

    สร้างจาก `[charset]` ของ `domain_lexicon.toml` — เก็บเป็น predicate แทน set ตัวอักษร
    เพราะช่วงยูนิโค้ดของภาษาไทยกว้างเกินกว่าจะแจงเป็นสมาชิกทีละตัวอย่างมีประสิทธิภาพ
    """

    thai_range: tuple[str, str]
    latin_ranges: tuple[tuple[str, str], ...]
    digit_range: tuple[str, str]
    punctuation: frozenset[str]

    def contains(self, ch: str) -> bool:
        if ch.isspace():
            return True
        if self.thai_range[0] <= ch <= self.thai_range[1]:
            return True
        if self.digit_range[0] <= ch <= self.digit_range[1]:
            return True
        for lo, hi in self.latin_ranges:
            if lo <= ch <= hi:
                return True
        return ch in self.punctuation

    @classmethod
    def from_config(cls, domain_lexicon: Mapping[str, object]) -> "DeclaredCharset":
        charset = domain_lexicon.get("charset")
        if not isinstance(charset, Mapping):
            raise ValueError("domain_lexicon.toml ต้องมีหัวข้อ [charset]")
        thai = charset["thai_range"]
        latin = charset["latin_ranges"]
        digit = charset["digit_range"]
        punctuation = charset["punctuation"]
        if not (isinstance(thai, list) and len(thai) == 2):
            raise ValueError("charset.thai_range ต้องมีสองค่า")
        if not (isinstance(digit, list) and len(digit) == 2):
            raise ValueError("charset.digit_range ต้องมีสองค่า")
        if not isinstance(latin, list):
            raise ValueError("charset.latin_ranges ต้องเป็นรายการของช่วง")
        return cls(
            thai_range=(str(thai[0]), str(thai[1])),
            latin_ranges=tuple((str(pair[0]), str(pair[1])) for pair in latin),
            digit_range=(str(digit[0]), str(digit[1])),
            punctuation=frozenset(str(punctuation)),
        )


@dataclass(frozen=True, slots=True)
class DomainLexicon:
    """คำศัพท์เฉพาะทางสำหรับนับ `domain_lexicon_match_count` (จาก `[terms]` และ `[patterns]`).

    การเทียบเป็นการนับจำนวนครั้งที่คำ/pattern ปรากฏในข้อความของหน้า (ไม่ deduplicate)
    เพื่อให้หน้าที่มีเนื้อหาสาระเข้มข้นได้คะแนนสูงกว่าหน้าที่มีคำเดียวซ้ำผ่าน ๆ
    """

    terms: tuple[str, ...]
    patterns: tuple[re.Pattern[str], ...]

    def match_count(self, text: str) -> int:
        count = 0
        for term in self.terms:
            count += text.count(term)
        for pattern in self.patterns:
            count += len(pattern.findall(text))
        return count

    @classmethod
    def from_config(cls, domain_lexicon: Mapping[str, object]) -> "DomainLexicon":
        terms_section = domain_lexicon.get("terms", {})
        patterns_section = domain_lexicon.get("patterns", {})
        terms: list[str] = []
        if isinstance(terms_section, Mapping):
            for values in terms_section.values():
                if isinstance(values, list):
                    terms.extend(str(v) for v in values)
        patterns: list[re.Pattern[str]] = []
        if isinstance(patterns_section, Mapping):
            for raw in patterns_section.values():
                patterns.append(re.compile(str(raw)))
        return cls(terms=tuple(terms), patterns=tuple(patterns))


@dataclass(frozen=True, slots=True)
class PageMetrics:
    """ตัวชี้วัดสี่ตัวและคะแนนของ Page_Quality_Gate ต่อหนึ่งหน้า (design §4.7).

    ไม่มีฟิลด์ candidacy อยู่ในนี้โดยเจตนา — candidacy ขึ้นกับตัวนับสะสมของทั้ง dataset
    จึงแยกไปอยู่ใน `GateDecision` ที่คืนจาก `mark()` เท่านั้น
    """

    document_id: str
    page: int
    extracted_char_count: int
    out_of_charset_ratio: float
    image_area_ratio: float
    domain_lexicon_match_count: int
    page_quality_score: float
    weights: Mapping[str, float]


@dataclass(frozen=True, slots=True)
class GateDecision:
    """ผลของ `mark()` — แยกจาก `PageMetrics` เพราะขึ้นกับตัวนับสะสมของทั้ง dataset."""

    is_ocr_candidate: bool
    candidate_reason: str | None
    candidates_so_far_after: int
    review_issues: tuple[ReviewIssue, ...]


class PageQualityGate:
    """คำนวณ `page_quality_score` และตัดสิน OCR candidacy ของหนึ่งหน้า."""

    def __init__(
        self,
        config: PageQualityConfig,
        lexicon: DomainLexicon,
        declared_charset: DeclaredCharset,
    ) -> None:
        self._config = config
        self._lexicon = lexicon
        self._charset = declared_charset

    def score(self, page: PageCharSet, text: str) -> PageMetrics:
        """คำนวณตัวชี้วัดสี่ตัวและ `page_quality_score` (R4.1, R4.2).

        `text` ต้องเป็นข้อความที่ผ่าน Line_Assembler แล้ว (ไม่ใช่ raw stream) เพราะ
        out_of_charset_ratio และ domain_lexicon_match_count ต้องนับจากข้อความที่จัดลำดับ
        ถูกต้องแล้ว `image_area_ratio` มาจาก `page.image_area_ratio` ตรง ๆ (คำนวณไว้แล้ว
        ใน Text_Extractor) ไม่คำนวณซ้ำที่นี่ — deterministic ต่อ input เดียวเสมอ
        """
        char_count = len(text)
        non_space = [ch for ch in text if not ch.isspace()]
        if non_space:
            out_of_charset = sum(1 for ch in non_space if not self._charset.contains(ch)) / len(
                non_space
            )
        else:
            out_of_charset = 0.0
        lexicon_matches = self._lexicon.match_count(text)

        cfg = self._config
        w_char = (
            min(char_count / cfg.char_count_reference, 1.0) if cfg.char_count_reference > 0 else 0.0
        )
        w_lexicon = (
            min(lexicon_matches / cfg.lexicon_match_reference, 1.0)
            if cfg.lexicon_match_reference > 0
            else 0.0
        )

        score = (
            cfg.weight_extracted_char_count * w_char
            + cfg.weight_out_of_charset_ratio * (1.0 - out_of_charset)
            + cfg.weight_image_area_ratio * (1.0 - page.image_area_ratio)
            + cfg.weight_domain_lexicon_match_count * w_lexicon
        )
        score = max(0.0, min(1.0, score))

        return PageMetrics(
            document_id=page.document_id,
            page=page.page,
            extracted_char_count=char_count,
            out_of_charset_ratio=out_of_charset,
            image_area_ratio=page.image_area_ratio,
            domain_lexicon_match_count=lexicon_matches,
            page_quality_score=score,
            weights={
                "extracted_char_count": w_char,
                "out_of_charset_ratio": out_of_charset,
                "image_area_ratio": page.image_area_ratio,
                "domain_lexicon_match_count": w_lexicon,
            },
        )

    def mark(self, metrics: PageMetrics, image_count: int, candidates_so_far: int) -> GateDecision:
        """ตัดสิน OCR candidacy — **ต้องเรียกครั้งเดียวต่อหน้า** ตามลำดับหน้าจริง (R4.3-R4.6).

        `candidates_so_far` คือจำนวน candidate ที่ทำเครื่องหมายไปแล้ว **ก่อน** หน้านี้
        ผู้เรียก (Ingestion_Manager) เป็นผู้สะสมค่านี้ต่อทั้ง dataset ไม่ใช่ต่อเอกสาร
        มิฉะนั้นโควตา 979 หน้าจะถูกตรวจผิดขอบเขต
        """
        threshold = self._config.low_text_char_threshold
        budget = self._config.ocr_candidate_budget_pages
        low_text = metrics.extracted_char_count < threshold

        if not low_text:
            return GateDecision(
                is_ocr_candidate=False,
                candidate_reason=None,
                candidates_so_far_after=candidates_so_far,
                review_issues=(),
            )

        if image_count < 1:
            # ข้อความน้อยแต่ไม่มีภาพ — ไม่มีอะไรให้ OCR ทำ (R4.4)
            issue = ReviewIssue(
                kind=ISSUE_LOW_CONTENT_PAGE,
                document_id=metrics.document_id,
                page=metrics.page,
                detail={
                    "extracted_char_count": metrics.extracted_char_count,
                    "threshold": threshold,
                },
            )
            return GateDecision(
                is_ocr_candidate=False,
                candidate_reason=None,
                candidates_so_far_after=candidates_so_far,
                review_issues=(issue,),
            )

        if candidates_so_far >= budget:
            # โควตาหมดแล้ว — หน้านี้เข้าเกณฑ์แต่ไม่ได้เข้าคิว (R4.6)
            issue = ReviewIssue(
                kind=ISSUE_OCR_BUDGET_EXHAUSTED,
                document_id=metrics.document_id,
                page=metrics.page,
                detail={"budget": budget, "candidates_so_far": candidates_so_far},
            )
            return GateDecision(
                is_ocr_candidate=False,
                candidate_reason=None,
                candidates_so_far_after=candidates_so_far,
                review_issues=(issue,),
            )

        return GateDecision(
            is_ocr_candidate=True,
            candidate_reason=REASON_LOW_TEXT_WITH_IMAGE,
            candidates_so_far_after=candidates_so_far + 1,
            review_issues=(),
        )
