"""รหัสหลักสูตรในคำถาม — คำอังกฤษ it/bit ต้องไม่ถูกตีเป็นหลักสูตร IT/BIT."""

from katrag.query.program_codes import first_program_code, program_code_positions


def test_codes_found():
    assert first_program_code("หลักสูตร AIT: วิชานี้กี่หน่วยกิต") == "AIT"
    assert first_program_code("IT กับ DSBA ต่างกันไหม") == "DSBA"
    assert [c for _, c in program_code_positions("IT กับ DSBA")] == ["IT", "DSBA"]
    assert first_program_code("หลักสูตร dsba เรียนกี่ปี") == "DSBA"


def test_english_words_are_not_programs():
    assert first_program_code("which year is it in the plan?") is None
    assert first_program_code("a bit harder question") is None
    assert first_program_code("หลักสูตร AIT: which year is it in the plan?") == "AIT"
