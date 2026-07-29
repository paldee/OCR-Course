"""Unit tests for katrag.eval.gold_set (R12.1–R12.6)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from katrag.common.types import CurriculumVersion
from katrag.errors import GoldSetError
from katrag.eval.gold_set import (
    EXPECTED_DOCUMENT_COUNT,
    GoldItem,
    GoldItemKind,
    GoldSet,
    ValidationIssue,
)


# ── Fixtures ──────────────────────────────────────────────────────────


def _make_page_text_item(doc_id: str, page: int, gold_id: int = 0) -> dict:
    return {
        "gold_id": gold_id,
        "item_kind": "page_text",
        "document_id": doc_id,
        "page": page,
        "payload": {"reference_text": "ข้อความตัวอย่าง"},
        "expected": {"text": "ข้อความตัวอย่าง"},
        "expected_citations": [[doc_id, page]],
        "author": "tester",
        "created_date": "2025-01-15",
        "review_method": "manual_review",
    }


def _make_table_cell_item(doc_id: str, page: int, gold_id: int = 0) -> dict:
    return {
        "gold_id": gold_id,
        "item_kind": "table_cell",
        "document_id": doc_id,
        "page": page,
        "payload": {"row": 1, "col": 1, "text": "06016101"},
        "expected": {"text": "06016101"},
        "expected_citations": [[doc_id, page]],
        "author": "tester",
        "created_date": "2025-01-15",
        "review_method": "manual_review",
    }


def _make_question_item(
    doc_id: str,
    level: str,
    gold_id: int = 0,
    version_diff: bool = False,
) -> dict:
    return {
        "gold_id": gold_id,
        "item_kind": "question",
        "document_id": doc_id,
        "page": 1,
        "question_level": level,
        "version": {
            "program": "IT",
            "curriculum_year": 2565,
            "edition_status": "current",
        },
        "payload": {
            "question": "คำถามตัวอย่าง",
            "version_diff": version_diff,
        },
        "expected": {"answer": "คำตอบตัวอย่าง"},
        "expected_citations": [[doc_id, 1]],
        "author": "tester",
        "created_date": "2025-01-15",
        "review_method": "manual_review",
    }


DOCUMENT_IDS = [
    "AIT2566_current",
    "BIT2560_old",
    "BIT2565_current",
    "DSBA2560_old",
    "DSBA2565_current",
    "IT2560_old",
    "IT2565_current",
    "PH_D_AITBA2569_current",
    "PH_D_IT2561_old",
    "PH_D_IT2566_current",
    "M_AITBA2564_old",
    "M_AITBA2569_current",
    "M_IT2563_old",
    "M_IT2568_current",
]


def _make_complete_gold_set() -> list[dict]:
    """สร้าง gold set ที่ครบตาม R12.1–R12.6."""
    items: list[dict] = []
    gid = 0

    # page_text สำหรับทุกเอกสาร (R12.1, R12.2)
    for doc_id in DOCUMENT_IDS:
        items.append(_make_page_text_item(doc_id, 1, gold_id=gid))
        gid += 1

    # table_cell สำหรับบางเอกสาร (R12.3)
    for doc_id in DOCUMENT_IDS[:3]:
        items.append(_make_table_cell_item(doc_id, 5, gold_id=gid))
        gid += 1

    # questions L1-L4 (R12.4)
    for level in ("L1", "L2", "L3", "L4"):
        items.append(_make_question_item(DOCUMENT_IDS[0], level, gold_id=gid))
        gid += 1

    # version diff question (R12.5)
    items.append(_make_question_item(
        DOCUMENT_IDS[0], "L4", gold_id=gid, version_diff=True
    ))
    gid += 1

    return items


def _write_gold_set(tmp_path: Path, items: list[dict]) -> Path:
    """เขียน gold set ลง temp directory."""
    gold_dir = tmp_path / "gold_set"
    gold_dir.mkdir()
    file_path = gold_dir / "gold_items.json"
    file_path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    return gold_dir


# ── Load tests ────────────────────────────────────────────────────────


class TestGoldSetLoad:
    """ทดสอบการโหลด gold set จากไฟล์ JSON."""

    def test_load_from_directory(self, tmp_path: Path) -> None:
        items_data = _make_complete_gold_set()
        gold_dir = _write_gold_set(tmp_path, items_data)

        gs = GoldSet()
        items = gs.load(gold_dir)

        assert len(items) == len(items_data)
        assert all(isinstance(i, GoldItem) for i in items)

    def test_load_from_single_file(self, tmp_path: Path) -> None:
        items_data = [_make_page_text_item("doc1", 1, gold_id=0)]
        file_path = tmp_path / "gold.json"
        file_path.write_text(json.dumps(items_data, ensure_ascii=False), encoding="utf-8")

        gs = GoldSet()
        items = gs.load(file_path)

        assert len(items) == 1
        assert items[0].kind == GoldItemKind.PAGE_TEXT

    def test_load_object_with_items_key(self, tmp_path: Path) -> None:
        items_data = [_make_page_text_item("doc1", 1, gold_id=0)]
        file_path = tmp_path / "gold.json"
        file_path.write_text(
            json.dumps({"items": items_data}, ensure_ascii=False), encoding="utf-8"
        )

        gs = GoldSet()
        items = gs.load(file_path)
        assert len(items) == 1

    def test_load_nonexistent_path_raises(self, tmp_path: Path) -> None:
        gs = GoldSet()
        with pytest.raises(GoldSetError, match="ไม่พบ"):
            gs.load(tmp_path / "nonexistent")

    def test_load_empty_directory_raises(self, tmp_path: Path) -> None:
        gold_dir = tmp_path / "empty"
        gold_dir.mkdir()

        gs = GoldSet()
        with pytest.raises(GoldSetError, match="ไม่มีไฟล์ JSON"):
            gs.load(gold_dir)

    def test_load_invalid_json_raises(self, tmp_path: Path) -> None:
        gold_dir = tmp_path / "gold_set"
        gold_dir.mkdir()
        (gold_dir / "bad.json").write_text("not json", encoding="utf-8")

        gs = GoldSet()
        with pytest.raises(GoldSetError, match="อ่านไฟล์ gold set ไม่สำเร็จ"):
            gs.load(gold_dir)


# ── Parse item tests ──────────────────────────────────────────────────


class TestGoldSetParseItem:
    """ทดสอบการ parse แต่ละรายการ."""

    def test_parse_page_text(self, tmp_path: Path) -> None:
        items_data = [_make_page_text_item("doc1", 5, gold_id=42)]
        gold_dir = _write_gold_set(tmp_path, items_data)

        gs = GoldSet()
        items = gs.load(gold_dir)

        assert items[0].gold_id == 42
        assert items[0].kind == GoldItemKind.PAGE_TEXT
        assert items[0].document_id == "doc1"
        assert items[0].page == 5
        assert items[0].author == "tester"
        assert items[0].created_date == "2025-01-15"
        assert items[0].review_method == "manual_review"

    def test_parse_question_with_version(self, tmp_path: Path) -> None:
        items_data = [_make_question_item("doc1", "L2", gold_id=7)]
        gold_dir = _write_gold_set(tmp_path, items_data)

        gs = GoldSet()
        items = gs.load(gold_dir)

        assert items[0].question_level == "L2"
        assert items[0].version is not None
        assert items[0].version.program == "IT"
        assert items[0].version.curriculum_year == 2565
        assert items[0].version.edition_status == "current"

    def test_missing_author_raises(self, tmp_path: Path) -> None:
        item = _make_page_text_item("doc1", 1)
        item["author"] = ""
        gold_dir = _write_gold_set(tmp_path, [item])

        gs = GoldSet()
        with pytest.raises(GoldSetError, match="author"):
            gs.load(gold_dir)

    def test_missing_created_date_raises(self, tmp_path: Path) -> None:
        item = _make_page_text_item("doc1", 1)
        item["created_date"] = ""
        gold_dir = _write_gold_set(tmp_path, [item])

        gs = GoldSet()
        with pytest.raises(GoldSetError, match="created_date"):
            gs.load(gold_dir)

    def test_missing_review_method_raises(self, tmp_path: Path) -> None:
        item = _make_page_text_item("doc1", 1)
        item["review_method"] = ""
        gold_dir = _write_gold_set(tmp_path, [item])

        gs = GoldSet()
        with pytest.raises(GoldSetError, match="review_method"):
            gs.load(gold_dir)

    def test_invalid_item_kind_raises(self, tmp_path: Path) -> None:
        item = _make_page_text_item("doc1", 1)
        item["item_kind"] = "unknown"
        gold_dir = _write_gold_set(tmp_path, [item])

        gs = GoldSet()
        with pytest.raises(GoldSetError, match="item_kind"):
            gs.load(gold_dir)

    def test_page_text_missing_document_id_raises(self, tmp_path: Path) -> None:
        item = _make_page_text_item("doc1", 1)
        item["document_id"] = None
        gold_dir = _write_gold_set(tmp_path, [item])

        gs = GoldSet()
        with pytest.raises(GoldSetError, match="document_id"):
            gs.load(gold_dir)

    def test_page_text_missing_reference_text_raises(self, tmp_path: Path) -> None:
        item = _make_page_text_item("doc1", 1)
        item["payload"] = {}
        gold_dir = _write_gold_set(tmp_path, [item])

        gs = GoldSet()
        with pytest.raises(GoldSetError, match="reference_text"):
            gs.load(gold_dir)

    def test_question_missing_level_raises(self, tmp_path: Path) -> None:
        item = _make_question_item("doc1", "L1")
        del item["question_level"]
        gold_dir = _write_gold_set(tmp_path, [item])

        gs = GoldSet()
        with pytest.raises(GoldSetError, match="question_level"):
            gs.load(gold_dir)

    def test_invalid_question_level_raises(self, tmp_path: Path) -> None:
        item = _make_question_item("doc1", "L1")
        item["question_level"] = "L5"
        gold_dir = _write_gold_set(tmp_path, [item])

        gs = GoldSet()
        with pytest.raises(GoldSetError, match="question_level"):
            gs.load(gold_dir)


# ── Coverage validation tests ─────────────────────────────────────────


class TestGoldSetValidateCoverage:
    """R12.1: gold set ต้องครอบคลุมเอกสารทั้ง 14 ไฟล์."""

    def test_complete_coverage_passes(self, tmp_path: Path) -> None:
        items_data = _make_complete_gold_set()
        gold_dir = _write_gold_set(tmp_path, items_data)

        gs = GoldSet()
        gs.load(gold_dir)
        issues = gs.validate_coverage()

        assert len(issues) == 0

    def test_incomplete_coverage_reports_issue(self, tmp_path: Path) -> None:
        # Only 3 documents
        items_data = [
            _make_page_text_item("doc1", 1, gold_id=0),
            _make_page_text_item("doc2", 1, gold_id=1),
            _make_page_text_item("doc3", 1, gold_id=2),
        ]
        gold_dir = _write_gold_set(tmp_path, items_data)

        gs = GoldSet()
        gs.load(gold_dir)
        issues = gs.validate_coverage()

        assert len(issues) == 1
        assert issues[0].kind == "gold_set_incomplete_coverage"

    def test_coverage_with_expected_ids(self, tmp_path: Path) -> None:
        items_data = [_make_page_text_item("doc_A", 1, gold_id=0)]
        gold_dir = _write_gold_set(tmp_path, items_data)

        expected_ids = frozenset({"doc_A", "doc_B", "doc_C"})

        gs = GoldSet()
        gs.load(gold_dir)
        issues = gs.validate_coverage(expected_document_ids=expected_ids)

        assert len(issues) == 1
        assert "doc_B" in str(issues[0].detail.get("missing_document_ids"))
        assert "doc_C" in str(issues[0].detail.get("missing_document_ids"))


# ── Completeness validation tests ────────────────────────────────────


class TestGoldSetValidateCompleteness:
    """R12.2–R12.6: gold set ต้องมีองค์ประกอบครบ."""

    def test_complete_gold_set_passes(self, tmp_path: Path) -> None:
        items_data = _make_complete_gold_set()
        gold_dir = _write_gold_set(tmp_path, items_data)

        gs = GoldSet()
        gs.load(gold_dir)
        issues = gs.validate_completeness()

        assert len(issues) == 0

    def test_missing_page_text(self, tmp_path: Path) -> None:
        # Only questions, no page_text
        items_data = [_make_question_item("doc1", "L1", gold_id=0, version_diff=True)]
        gold_dir = _write_gold_set(tmp_path, items_data)

        gs = GoldSet()
        gs.load(gold_dir)
        issues = gs.validate_completeness()

        kinds = [i.kind for i in issues]
        assert "gold_set_missing_page_text" in kinds

    def test_missing_table_cell(self, tmp_path: Path) -> None:
        # Only page_text, no table_cell
        items_data = [
            _make_page_text_item("doc1", 1, gold_id=0),
            _make_question_item("doc1", "L1", gold_id=1, version_diff=True),
            _make_question_item("doc1", "L2", gold_id=2),
            _make_question_item("doc1", "L3", gold_id=3),
            _make_question_item("doc1", "L4", gold_id=4),
        ]
        gold_dir = _write_gold_set(tmp_path, items_data)

        gs = GoldSet()
        gs.load(gold_dir)
        issues = gs.validate_completeness()

        kinds = [i.kind for i in issues]
        assert "gold_set_missing_table_cell" in kinds

    def test_missing_question_levels(self, tmp_path: Path) -> None:
        # Only L1, missing L2-L4
        items_data = [
            _make_page_text_item("doc1", 1, gold_id=0),
            _make_table_cell_item("doc1", 1, gold_id=1),
            _make_question_item("doc1", "L1", gold_id=2, version_diff=True),
        ]
        gold_dir = _write_gold_set(tmp_path, items_data)

        gs = GoldSet()
        gs.load(gold_dir)
        issues = gs.validate_completeness()

        kinds = [i.kind for i in issues]
        assert "gold_set_missing_question_levels" in kinds

    def test_missing_version_diff_questions(self, tmp_path: Path) -> None:
        # All questions present but no version_diff
        items_data = [
            _make_page_text_item("doc1", 1, gold_id=0),
            _make_table_cell_item("doc1", 1, gold_id=1),
            _make_question_item("doc1", "L1", gold_id=2),
            _make_question_item("doc1", "L2", gold_id=3),
            _make_question_item("doc1", "L3", gold_id=4),
            _make_question_item("doc1", "L4", gold_id=5),
        ]
        gold_dir = _write_gold_set(tmp_path, items_data)

        gs = GoldSet()
        gs.load(gold_dir)
        issues = gs.validate_completeness()

        kinds = [i.kind for i in issues]
        assert "gold_set_missing_version_diff_questions" in kinds


# ── Accessor tests ────────────────────────────────────────────────────


class TestGoldSetAccessors:
    """ทดสอบ helper methods."""

    def test_page_text_items(self, tmp_path: Path) -> None:
        items_data = _make_complete_gold_set()
        gold_dir = _write_gold_set(tmp_path, items_data)

        gs = GoldSet()
        gs.load(gold_dir)

        page_texts = gs.page_text_items()
        assert len(page_texts) == EXPECTED_DOCUMENT_COUNT
        assert all(i.kind == GoldItemKind.PAGE_TEXT for i in page_texts)

    def test_questions_by_level(self, tmp_path: Path) -> None:
        items_data = _make_complete_gold_set()
        gold_dir = _write_gold_set(tmp_path, items_data)

        gs = GoldSet()
        gs.load(gold_dir)

        l1_questions = gs.questions_by_level("L1")
        assert len(l1_questions) >= 1
        assert all(i.question_level == "L1" for i in l1_questions)

    def test_version_diff_questions(self, tmp_path: Path) -> None:
        items_data = _make_complete_gold_set()
        gold_dir = _write_gold_set(tmp_path, items_data)

        gs = GoldSet()
        gs.load(gold_dir)

        diff_questions = gs.version_diff_questions()
        assert len(diff_questions) >= 1

    def test_document_ids(self, tmp_path: Path) -> None:
        items_data = _make_complete_gold_set()
        gold_dir = _write_gold_set(tmp_path, items_data)

        gs = GoldSet()
        gs.load(gold_dir)

        doc_ids = gs.document_ids()
        assert len(doc_ids) == EXPECTED_DOCUMENT_COUNT
