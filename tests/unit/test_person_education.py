"""วุฒิการศึกษาของอาจารย์จากตารางประวัติ — ต้องไม่แสดงเลขประจำตัวประชาชน."""

import sqlite3
from pathlib import Path

import pytest

from katrag.query.faculty import detect_person_education, try_person_education

DB = Path(__file__).resolve().parents[2] / "artifacts" / "katrag.sqlite3"
needs_db = pytest.mark.skipif(not DB.exists(), reason="database not extracted (run setup_db.py)")


def test_detects_name_and_question():
    assert detect_person_education("อาจารย์ศุภมิตร จิตตะยโศธร จบการศึกษาจากที่ไหน") == ("ศุภมิตร", "จิตตะยโศธร")
    assert detect_person_education("อาจารย์ประจำหลักสูตรมีใครบ้าง") is None


@needs_db
def test_full_degrees_and_no_national_id():
    con = sqlite3.connect(str(DB))
    r = try_person_education(con, "หลักสูตร AIT: อาจารย์ศุภมิตร จิตตะยโศธร จบการศึกษาจากที่ไหน")
    assert r.matched and "2523" in r.context and "Queensland" in r.context and "2525" in r.context
    assert "3100500712364" not in r.context


@needs_db
def test_points_to_fuller_book_when_selected_book_lists_less():
    con = sqlite3.connect(str(DB))
    r = try_person_education(con, "หลักสูตร BIT: อาจารย์ศุภมิตร จิตตะยโศธร จบการศึกษาจากที่ไหน")
    assert "BIT 2565" in r.context and "Queensland" in r.context
