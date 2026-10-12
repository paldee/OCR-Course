"""คำถามหลายขั้นที่คำนวณคำตอบจากตาราง — เส้นทางที่เลือกและข้อสรุปต้องตรงกับแผน/เกณฑ์ในเล่ม."""

import sqlite3
from pathlib import Path

import pytest

from katrag.query.multi_hop import try_multi_hop

DB = Path(__file__).resolve().parents[2] / "artifacts" / "katrag.sqlite3"
pytestmark = pytest.mark.skipif(not DB.exists(), reason="database not extracted (run setup_db.py)")


@pytest.fixture(scope="module")
def conn():
    c = sqlite3.connect(DB)
    yield c
    c.close()


@pytest.mark.parametrize("q,intent,must", [
    # วิชาบังคับก่อนเรียนปี 2 ภาค 1 จึงลงการสร้างคลังข้อมูลตอนปี 2 ไม่ได้ (ตรงกับเฉลยข้อ H3)
    ("หลักสูตร DSBA: ตอนอยู่ปี 2 จะลงเรียนการสร้างคลังข้อมูลเลยได้มั้ย", "can_take", ["ไม่ได้", "06066300"]),
    ("หลักสูตร AIT: พื้นฐานการเรียนรู้เชิงลึก ต้องผ่านอะไรมาก่อนบ้าง นับรวมวิชาพื้นฐานของวิชานั้นด้วย",
     "prereq_chain", ["06046401", "06046400"]),
    ("หลักสูตร IT: การบริหารโครงการเทคโนโลยีสารสนเทศ กับ โรงเรียนสร้างเสน่ห์ วิชาไหนเรียนก่อน",
     "course_compare", ["ปีที่ 1", "ปีที่ 3"]),
    ("หลักสูตร DSBA: คณิตศาสตร์ไม่ต่อเนื่อง ใน AIT และ DSBA อยู่ปีไหนเทอมไหนบ้าง",
     "cross_program_course", ["AIT", "DSBA", "ปีที่ 1", "ปีที่ 2"]),
    ("หลักสูตร IT: ตอนนี้มี 123 หน่วยกิต GPA 2.45 ขาดอีกเท่าไหร่ถึงจะจบ", "grad_check", ["129", "ขาดอีก 6"]),
    ("หลักสูตร DSBA: เรียนครบ 132 หน่วยกิตแล้วแต่ GPA 1.95 สำเร็จการศึกษาได้หรือเปล่า",
     "grad_check", ["ยังจบไม่ได้", "2.00"]),
    ("หลักสูตร IT: IT หรือ DSBA หลักสูตรไหนต้องเรียนหน่วยกิตมากกว่ากัน", "program_compare", ["129", "132", "ต่างกัน 3"]),
])
def test_multi_hop_answers(conn, q, intent, must):
    sr = try_multi_hop(conn, q)
    assert sr.matched and sr.intent == intent
    for m in must:
        assert m in sr.context, m


@pytest.mark.parametrize("q", [
    "หลักสูตร DSBA: ปี 3 เทอม 1 เรียนวิชาอะไร",
    "หลักสูตร IT: จบด้วยเกรดเฉลี่ย 3.60 จะได้เกียรตินิยมอันดับไหน",
    # ระบุหน่วยกิตแยกหมวด → ตัวตรวจแผนจบ (grad_audit) เป็นผู้ตอบ
    "หลักสูตร DSBA: เก็บศึกษาทั่วไป 30 หน่วยกิต เฉพาะบังคับ 78 หน่วยกิต รวม 123 หน่วยกิต จบได้ไหม",
    "หลักสูตร DSBA: วิชาที่มีในหลักสูตรเก่า แต่ไม่มีในหลักสูตรใหม่ มีอะไรบ้าง",
])
def test_leaves_other_questions(conn, q):
    assert not try_multi_hop(conn, q).matched
