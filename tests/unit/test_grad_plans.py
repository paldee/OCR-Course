"""แผนการศึกษาปริญญาโท/เอก — อ่านจากข้อความหัวข้อ 3.1.1/3.1.2 (ตัวอย่างคัดจากเล่มจริง)."""

from katrag.query.grad_plans import detect_grad_plan_intent, parse_structure


def test_thai_structure_with_checkbox_symbols():
    # ปริญญาโท AITBA 2564 หน้า 16 (ชื่อแผนใน 3.1.1 มีสัญลักษณ์ช่องติ๊กนำหน้า)
    text = (
        "3.1.1 จำนวนหน่วยกิตรวมตลอดหลักสูตร |  แผน ก แบบ ก 1 | 36 | หน่วยกิต |  แผน ข | 36 | หน่วยกิต | "
        "3.1.2 โครงสร้างหลักสูตร | แผน ก แบบ ก 1 | 36 | หน่วยกิต | ก. หมวดวิชาวิทยานิพนธ์ | 36 | หน่วยกิต | "
        "แผน ข | 36 | หน่วยกิต | ก. หมวดวิชาการค้นคว้าอิสระ | 6 | หน่วยกิต | ข. หมวดวิชาบังคับ | 12 | หน่วยกิต"
    )
    plans = parse_structure(text)
    assert [(p.name, p.credits) for p in plans] == [("แผน ก แบบ ก 1", 36), ("แผน ข", 36)]
    assert plans[1].categories == [("หมวดวิชาการค้นคว้าอิสระ", "6"), ("หมวดวิชาบังคับ", "12")]


def test_english_structure_of_international_program():
    # ปริญญาเอก IT 2566 หน้า 25
    text = (
        "3.1 Curriculum structure and credits | Type 1.1  (Applicants with Master's Degree) | 57 credits | "
        "a. Thesis courses | 57  credits | b. Research Courses | Non Credits | "
        "Type 2.1 (Applicants with Master's Degree) | 60 credits | a. Thesis courses | 48  credits | "
        "b. Elective courses | 12  Credits | 3.2 Courses | Thesis courses 57 credits"
    )
    plans = parse_structure(text)
    assert [p.credits for p in plans] == [57, 60]
    assert plans[0].categories == [("Thesis courses", "57"), ("Research Courses", "-")]


def test_intent_needs_graduate_words():
    assert detect_grad_plan_intent("ปริญญาโท IT 2568 แผนวิชาชีพ ต้องเรียนกี่หน่วยกิต")
    assert detect_grad_plan_intent("หลักสูตร AITBA: แผน 1.2 ต้องทำวิทยานิพนธ์กี่หน่วยกิต")
    assert not detect_grad_plan_intent("หลักสูตร IT: ปี 1 เทอม 1 ต้องเรียนกี่หน่วยกิต")
