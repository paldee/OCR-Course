"""แถวตารางแผนที่ตัวอ่านหลักอ่านไม่ได้ — ข้อความตัวอย่างคัดจากเล่มจริง."""

from katrag.ingest.populate_courses import _lenient_plan_rows, _names_split_by_or


def test_or_pair_with_names_after_second_code():
    # AIT 2566 หน้า 22: "06046443 หรือ 06046444 ชื่อA หรือ ชื่อB"
    text = (
        "06046443 | หรือ | 06046444 | สหกิจศึกษาทางเทคโนโลยีปัญญาประดิษฐ์ | COOPERATIVE EDUCATION IN ARTIFICIAL | "
        "INTELLIGIENCE TECHNOLOGY หรือ | สหกิจศึกษาต่างประเทศทางเทคโนโลยีปัญญาประดิษฐ์ | "
        "OVERSEA COOPERATIVE EDUCATION IN ARTIFICIAL | INTELLIGIENCE TECHNOLOGY | 6 (0-45-0) | 6 (0-45-0)"
    )
    rows = {r.code: r for r in _lenient_plan_rows(text, 1, 22, "doc", set())}
    assert rows["06046443"].name_th == "สหกิจศึกษาทางเทคโนโลยีปัญญาประดิษฐ์"
    assert rows["06046444"].name_th == "สหกิจศึกษาต่างประเทศทางเทคโนโลยีปัญญาประดิษฐ์"
    assert rows["06046444"].name_en.startswith("OVERSEA")
    assert rows["06046443"].credits_lab == 45  # ค่าเกิน 30 ถูกจัดการตอนบันทึก ไม่ใช่ตอนอ่าน


def test_second_course_borrows_credits_of_or_partner():
    # DSBA 2565 หน้า 36: 06026260 ไม่มีหน่วยกิตของตัวเอง
    text = (
        "06026259 | หรือ | สหกิจศึกษาทางวิทยาการข้อมูลและการวิเคราะห์เชิงธุรกิจ | "
        "COOPERATIVE EDUCATION IN DATA SCIENCE AND | BUSINESS ANALYTICS | 6 (0-35-0) | "
        "06026260 สหกิจศึกษาต่างประเทศทางวิทยาการข้อมูลและการ | วิเคราะห์เชิงธุรกิจ | "
        "OVERSEA COOPERATIVE EDUCATION IN DATA | SCIENCE AND BUSINESS ANALYTICS | รวม | 6"
    )
    rows = {r.code: r for r in _lenient_plan_rows(text, 1, 36, "doc", set())}
    assert rows["06026259"].credits_total == 6
    assert rows["06026260"].credits_total == 6
    assert rows["06026260"].name_en == "OVERSEA COOPERATIVE EDUCATION IN DATA SCIENCE AND BUSINESS ANALYTICS"


def test_track_heading_is_not_part_of_name():
    assert _names_split_by_or("| แขนงวิชาวิศวกรรมซอฟต์แวร์ | สหกิจศึกษาทางด้านวิศวกรรมซอฟต์แวร์ | COOPERATIVE |") == [
        ("สหกิจศึกษาทางด้านวิศวกรรมซอฟต์แวร์", "COOPERATIVE")
    ]
