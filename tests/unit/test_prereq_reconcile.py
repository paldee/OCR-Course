"""ตัวอ่านวิชาบังคับก่อนรายหน้า — ข้อความตัวอย่างคัดจากเล่มจริง."""

from katrag.ingest.populate_prerequisites import scan_page_prerequisites


def test_none_is_not_borrowed_from_next_course():
    # DSBA 2560 หน้า 179: 06026106 เขียนว่า "ไม่มี" ส่วน 06026104 เป็นของ 06026107
    text = (
        "06026106  | แนวคิดระบบฐานข้อมูล  | 3(2-2-5)  |   | DATABASE SYSTEM CONCEPTS  |   |   | "
        "วิชาบังคับก่อน  :   | ไม่มี  |   | PREREQUISITE :    | NONE  | แนะนําฐานข้อมูล ... "
        "06026107 โครงสร้างข้อมูลและอัลกอริทึม  | 3(3-0-6)  |   | DATA  STRUCTURES  AND  ALGORITHMS  |   | "
        "วิชาบังคับก่อน  :   | 06026104  การสร้างโปรแกรมคอมพิวเตอร์  | PREREQUISITE :    | 06026104  COMPUTER PROGRAMMING"
    )
    got = scan_page_prerequisites(text)
    assert got["06026106"] == []
    assert got["06026107"] == ["06026104"]


def test_prerequisite_after_name_when_credits_come_last():
    # DSBA 2565 หน้า 324: หน่วยกิตอยู่ท้ายบล็อก
    text = (
        "06026213  | ระบบข้อมูลมหัต  | BIG DATA SYSTEMS  | วิชาบังคับก่อน :  06066300 แนวคิดระบบฐานข้อมูล   | "
        "PREREQUISITE : 06066300 DATABASE SYSTEM  | CONCEPTS  | 3(2-2-5)  | การติดตั้ง"
    )
    assert scan_page_prerequisites(text) == {"06026213": ["06066300"]}


def test_code_cited_by_previous_course_is_not_taken_as_head():
    text = (
        "06016419 โครงสร้างพื้นฐานเครือข่ายการสื่อสาร | วิชาบังคับก่อน : | 06016413 ระบบเครือข่ายเบื้องต้น | "
        "PREREQUISITE : 06016413 INTRODUCTION TO NETWORK SYSTEMS | มาตรฐาน ... "
        "06016420 ระบบโครงสร้างพื้นฐานและการบริการ | 3(2-2-5) | วิชาบังคับก่อน : | 06016413 ระบบเครือข่ายเบื้องต้น | PREREQUISITE"
    )
    got = scan_page_prerequisites(text)
    assert got["06016419"] == ["06016413"]
    assert got["06016420"] == ["06016413"]
    assert "06016413" not in got
