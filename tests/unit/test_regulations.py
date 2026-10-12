"""ข้อบังคับปริญญาตรี — การแยกคำถาม และตัวเลขต้องตรงกับข้อความในเล่ม (หน้าที่ OCR อ่านได้)."""

import re
import sqlite3
from pathlib import Path

import pytest

from katrag.query.regulations import TOPICS, detect_regulation_topic

DB = Path(__file__).resolve().parents[2] / "artifacts" / "katrag.sqlite3"
needs_db = pytest.mark.skipif(not DB.exists(), reason="database not extracted (run setup_db.py)")
_TH = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")


@pytest.mark.parametrize("q,topic", [
    ("ภาคปกติลงทะเบียนได้มากที่สุดกี่หน่วยกิต", "registration"),
    ("ต้องได้เกรดเฉลี่ยสะสมอย่างน้อยเท่าไรจึงจะสำเร็จการศึกษา", "graduation"),
    ("เกียรตินิยมอันดับ 2 ต้องได้เกรดเท่าไร", "honors"),
    ("เกรดต่ำกว่าเท่าไรถึงโดนภาคทัณฑ์", "probation"),
    ("B+ ได้กี่แต้ม", "grading"),
    ("หลักสูตร IT: ภาคฤดูร้อนลงได้กี่หน่วยกิต", "registration"),
])
def test_detects_regulation_questions(q, topic):
    assert detect_regulation_topic(q) == topic


@pytest.mark.parametrize("q", [
    "ปี 1 เทอม 1 ต้องลงทะเบียนกี่หน่วยกิต",       # ถามตามแผน
    "ต้องเรียนกี่หน่วยกิตถึงจะจบ",                # หน่วยกิตรวมของหลักสูตร (ตาราง rule)
    "อาจารย์ศุภมิตรจบเกียรตินิยมไหม",              # ประวัติอาจารย์
    "ปริญญาโท IT 2568 เกียรตินิยมได้ไหม",           # ข้อบังคับนี้เป็นของปริญญาตรี
    "ภาคไหนลงหน่วยกิตมากที่สุด",                  # หน่วยกิตต่อภาคตามแผน
])
def test_leaves_other_questions_alone(q):
    assert detect_regulation_topic(q) is None


@needs_db
def test_numbers_match_book_pages_where_ocr_is_legible():
    con = sqlite3.connect(str(DB))

    def page(n):
        return con.execute(
            "SELECT p.page_text FROM page p JOIN document d ON d.document_id = p.document_id "
            "JOIN curriculum_version cv ON cv.version_id = d.version_id "
            "WHERE cv.program='AIT' AND cv.curriculum_year=2566 AND d.degree_level='bachelor' AND p.page_number=?",
            (n,),
        ).fetchone()[0].translate(_TH)

    assert re.search(r"ไม่เกิน\s*22\s*หน่วยกิต", page(TOPICS["registration"].pages[0]))
    honors = page(89)
    assert "3.50" in honors and "3.25" in honors
    assert "ภาคทัณฑ์" in page(TOPICS["probation"].pages[0])
