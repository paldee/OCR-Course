"""เทสต์ของ consistency check (CHK1-CHK7).

แต่ละกฎมีสองเทสต์เป็นอย่างน้อย: กรณีที่ข้อมูลถูก (ต้องผ่าน) และกรณีที่ข้อมูลผิด
(ต้องจับได้) เพื่อยืนยันว่ากฎไม่ใช่แค่ "ผ่านตลอด"

ใช้ sqlite in-memory ที่สร้าง schema ย่อเฉพาะตารางที่กฎอ่าน — ไม่แตะฐานข้อมูลจริง
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from katrag.eval.consistency_check import (
    CHK7_MIN_COURSES,
    ConsistencyReport,
    check_chk1,
    check_chk2,
    check_chk3,
    check_chk4,
    check_chk5,
    check_chk6,
    check_chk7,
    render,
    run_all,
)

# ══════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════

_SCHEMA = """
CREATE TABLE curriculum_version (
    version_id INTEGER PRIMARY KEY,
    program TEXT NOT NULL,
    curriculum_year INTEGER NOT NULL,
    edition_status TEXT NOT NULL
);
CREATE TABLE course (
    course_id INTEGER PRIMARY KEY,
    version_id INTEGER NOT NULL,
    code TEXT NOT NULL,
    name_th TEXT,
    name_en TEXT,
    credits_total INTEGER,
    year INTEGER,
    semester INTEGER,
    category TEXT,
    type TEXT,
    prerequisite_json TEXT
);
CREATE TABLE chunk (
    chunk_id INTEGER PRIMARY KEY,
    version_id INTEGER NOT NULL,
    page_number INTEGER,
    text TEXT
);
"""


@pytest.fixture()
def conn() -> sqlite3.Connection:
    """ฐานข้อมูลเปล่าที่มีเวอร์ชันหลักสูตรเดียว (version_id=1)."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    c.execute(
        "INSERT INTO curriculum_version VALUES (1, 'DSBA', 2565, 'current')"
    )
    return c


def _add_course(
    conn: sqlite3.Connection,
    code: str,
    *,
    credits: int | None = 3,
    year: int | None = 1,
    semester: int | None = 1,
    ctype: str = "บังคับ",
    prereq: list[str] | None = None,
    name: str = "วิชาทดสอบ",
    version_id: int = 1,
) -> None:
    conn.execute(
        "INSERT INTO course (version_id, code, name_th, credits_total, year, semester,"
        " category, type, prerequisite_json) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            version_id,
            code,
            name,
            credits,
            year,
            semester,
            "หมวดวิชาเฉพาะ",
            ctype,
            json.dumps(prereq or [], ensure_ascii=False),
        ),
    )


def _add_chunk(conn: sqlite3.Connection, text: str, *, version_id: int = 1) -> None:
    conn.execute(
        "INSERT INTO chunk (version_id, page_number, text) VALUES (?, 1, ?)",
        (version_id, text),
    )


def _describe(code: str, credits: int = 3, *, prereq_text: str = "ไม่มี") -> str:
    """ข้อความเลียนหน้าคำอธิบายรายวิชาในเล่มจริง."""
    return (
        f"{code} วิชาทดสอบ {credits}(3-0-6) TEST COURSE "
        f"วิชาบังคับก่อน : {prereq_text} PREREQUISITE : NONE "
        + "คำอธิบายรายวิชาอย่างละเอียด " * 20
    )


# ══════════════════════════════════════════════════════════════════════
# CHK3 — รหัสวิชาเป็นตัวเลข 8 หลัก
# ══════════════════════════════════════════════════════════════════════


class TestChk3:
    def test_passes_when_all_codes_are_8_digits(self, conn: sqlite3.Connection) -> None:
        _add_course(conn, "06026200")
        _add_course(conn, "06026201")
        result = check_chk3(conn)
        assert result.passed
        assert result.checked == 2

    @pytest.mark.parametrize(
        "bad_code",
        ["0602620O", "6026200", "060262001", "06026-00", "O6O26200", ""],
    )
    def test_catches_non_numeric_or_wrong_length(
        self, conn: sqlite3.Connection, bad_code: str
    ) -> None:
        _add_course(conn, bad_code)
        result = check_chk3(conn)
        assert not result.passed
        assert result.findings[0].code == bad_code

    def test_severity_is_error(self, conn: sqlite3.Connection) -> None:
        assert check_chk3(conn).severity == "error"


# ══════════════════════════════════════════════════════════════════════
# CHK6 — ไม่มีวิชาซ้ำในภาคเรียนเดียวกัน
# ══════════════════════════════════════════════════════════════════════


class TestChk6:
    def test_passes_when_no_duplicates(self, conn: sqlite3.Connection) -> None:
        _add_course(conn, "06026200", year=1, semester=1)
        _add_course(conn, "06026201", year=1, semester=2)
        assert check_chk6(conn).passed

    def test_catches_same_code_twice_in_one_term(self, conn: sqlite3.Connection) -> None:
        _add_course(conn, "06026200", year=1, semester=1)
        _add_course(conn, "06026200", year=1, semester=1)
        result = check_chk6(conn)
        assert not result.passed
        assert "2 ครั้ง" in result.findings[0].detail

    def test_same_code_in_different_terms_is_allowed(
        self, conn: sqlite3.Connection
    ) -> None:
        """วิชาเดียวกันปรากฏได้ในสองภาค (เช่น เล่มแสดงทั้งแผนสหกิจและแผนปกติ)."""
        _add_course(conn, "06026200", year=1, semester=1)
        _add_course(conn, "06026200", year=2, semester=1)
        assert check_chk6(conn).passed


# ══════════════════════════════════════════════════════════════════════
# CHK5 — วิชาบังคับก่อน อยู่ภาคที่มาก่อนจริง
# ══════════════════════════════════════════════════════════════════════


class TestChk5:
    def test_passes_when_prerequisite_comes_earlier(
        self, conn: sqlite3.Connection
    ) -> None:
        _add_course(conn, "06026200", year=1, semester=1)
        _add_course(conn, "06026201", year=1, semester=2, prereq=["06026200"])
        result = check_chk5(conn)
        assert result.passed
        assert result.checked == 1

    def test_catches_prerequisite_in_later_term(self, conn: sqlite3.Connection) -> None:
        _add_course(conn, "06026200", year=2, semester=2)
        _add_course(conn, "06026201", year=1, semester=1, prereq=["06026200"])
        result = check_chk5(conn)
        assert not result.passed
        assert "ภาคหลังกว่า" in result.findings[0].detail

    def test_later_term_is_error_severity(self, conn: sqlite3.Connection) -> None:
        """prerequisite อยู่ภาคหลังกว่า เป็นไปไม่ได้ ต้องเป็น error."""
        _add_course(conn, "06026200", year=2, semester=2)
        _add_course(conn, "06026201", year=1, semester=1, prereq=["06026200"])
        result = check_chk5(conn)
        assert result.count_by_severity("error") == 1
        assert result.count_by_severity("warning") == 0

    def test_catches_prerequisite_in_same_term(self, conn: sqlite3.Connection) -> None:
        _add_course(conn, "06026200", year=1, semester=1)
        _add_course(conn, "06026201", year=1, semester=1, prereq=["06026200"])
        result = check_chk5(conn)
        assert not result.passed
        assert "ภาคเดียวกัน" in result.findings[0].detail

    def test_same_term_is_warning_not_error(self, conn: sqlite3.Connection) -> None:
        """ภาคเดียวกันอาจเป็นวิชาเรียนร่วมภาคที่เล่มจัดไว้เอง — ต้องไม่ทำให้ผลรวมตก.

        พบจริงใน IT/2560 ที่จัด 06016306 กับ 06016321 ไว้ปี 2 ภาค 2 เหมือนกัน
        """
        _add_course(conn, "06026200", year=1, semester=1)
        _add_course(conn, "06026201", year=1, semester=1, prereq=["06026200"])
        result = check_chk5(conn)
        assert result.count_by_severity("warning") == 1
        assert result.count_by_severity("error") == 0
        assert run_all(conn).ok is True

    def test_earlier_year_later_semester_still_passes(
        self, conn: sqlite3.Connection
    ) -> None:
        """ปี 1 ภาค 2 มาก่อน ปี 2 ภาค 1 — ต้องเทียบเป็น tuple ไม่ใช่เทียบภาคอย่างเดียว."""
        _add_course(conn, "06026200", year=1, semester=2)
        _add_course(conn, "06026201", year=2, semester=1, prereq=["06026200"])
        assert check_chk5(conn).passed

    def test_skips_when_prerequisite_position_unknown(
        self, conn: sqlite3.Connection
    ) -> None:
        """วิชาบังคับก่อนที่ไม่รู้ชั้นปี ตรวจไม่ได้ ต้องไม่นับเป็นทั้งผ่านและไม่ผ่าน."""
        _add_course(conn, "06026201", year=1, semester=1, prereq=["09999999"])
        result = check_chk5(conn)
        assert result.passed
        assert result.checked == 0

    def test_handles_malformed_prerequisite_json(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT INTO course (version_id, code, year, semester, prerequisite_json)"
            " VALUES (1, '06026201', 1, 1, 'not-json')"
        )
        result = check_chk5(conn)
        assert result.passed
        assert result.checked == 0


# ══════════════════════════════════════════════════════════════════════
# CHK4 — หน่วยกิตในแผน = หน่วยกิตในคำอธิบาย
# ══════════════════════════════════════════════════════════════════════


class TestChk4:
    def test_passes_when_credits_match(self, conn: sqlite3.Connection) -> None:
        _add_course(conn, "06026200", credits=3)
        _add_chunk(conn, _describe("06026200", 3))
        result = check_chk4(conn)
        assert result.passed
        assert result.checked == 1

    def test_catches_credit_mismatch(self, conn: sqlite3.Connection) -> None:
        _add_course(conn, "06026200", credits=3)
        _add_chunk(conn, _describe("06026200", 4))
        result = check_chk4(conn)
        assert not result.passed
        assert "ตารางแผน 3" in result.findings[0].detail

    def test_skips_course_without_description(self, conn: sqlite3.Connection) -> None:
        _add_course(conn, "06026200", credits=3)
        result = check_chk4(conn)
        assert result.checked == 0


# ══════════════════════════════════════════════════════════════════════
# CHK2 — ทุกรหัสในแผน มีคำอธิบายรายวิชา
# ══════════════════════════════════════════════════════════════════════


class TestChk2:
    def test_passes_when_description_exists(self, conn: sqlite3.Connection) -> None:
        _add_course(conn, "06026200")
        _add_chunk(conn, _describe("06026200"))
        assert check_chk2(conn).passed

    def test_catches_missing_description(self, conn: sqlite3.Connection) -> None:
        _add_course(conn, "06026200")
        _add_chunk(conn, "ตารางแผนการเรียน 06026200 วิชาทดสอบ 3(3-0-6)")
        result = check_chk2(conn)
        assert not result.passed
        assert result.findings[0].code == "06026200"

    def test_accepts_long_description_without_prerequisite_keyword(
        self, conn: sqlite3.Connection
    ) -> None:
        """วิชาศึกษาทั่วไปที่คำอธิบายไม่มีคำว่า 'วิชาบังคับก่อน' ต้องไม่ถูกรายงาน."""
        _add_course(conn, "90641002")
        _add_chunk(conn, "90641002 ความฉลาดทางดิจิทัล " + "เนื้อหารายวิชา " * 40)
        assert check_chk2(conn).passed

    def test_ignores_course_without_plan_position(
        self, conn: sqlite3.Connection
    ) -> None:
        """วิชาที่ไม่อยู่ในแผน (year เป็น NULL) ไม่ใช่ขอบเขตของกฎนี้."""
        _add_course(conn, "06026999", year=None, semester=None)
        result = check_chk2(conn)
        assert result.checked == 0


# ══════════════════════════════════════════════════════════════════════
# CHK7 — หน่วยกิตต่อภาคอยู่ระหว่าง 9-22
# ══════════════════════════════════════════════════════════════════════


class TestChk7:
    def _fill_term(
        self, conn: sqlite3.Connection, count: int, credits: int, *, year: int = 1
    ) -> None:
        for i in range(count):
            _add_course(conn, f"0602620{i}", credits=credits, year=year, semester=1)

    def test_passes_for_normal_term_load(self, conn: sqlite3.Connection) -> None:
        self._fill_term(conn, CHK7_MIN_COURSES + 1, 3)  # 5 วิชา 15 หน่วยกิต
        result = check_chk7(conn)
        assert result.passed
        assert result.checked == 1

    def test_catches_overloaded_term(self, conn: sqlite3.Connection) -> None:
        self._fill_term(conn, 9, 3)  # 27 หน่วยกิต
        result = check_chk7(conn)
        assert not result.passed
        assert "เกินเกณฑ์" in result.findings[0].detail

    def test_catches_underloaded_term(self, conn: sqlite3.Connection) -> None:
        self._fill_term(conn, CHK7_MIN_COURSES, 1)  # 4 หน่วยกิต
        result = check_chk7(conn)
        assert not result.passed
        assert "น้อยกว่าเกณฑ์" in result.findings[0].detail

    def test_skips_terms_with_too_few_courses(self, conn: sqlite3.Connection) -> None:
        """ภาคที่มีวิชาน้อยมักเป็นภาคที่เนื้อหาจริงเป็นวิชาเลือก — ต้องไม่รายงาน."""
        self._fill_term(conn, CHK7_MIN_COURSES - 1, 3)  # 3 วิชา 9 หน่วยกิต
        result = check_chk7(conn)
        assert result.checked == 0
        assert result.passed


# ══════════════════════════════════════════════════════════════════════
# CHK1 — หน่วยกิตรวมของแผน = หน่วยกิตที่ประกาศ
# ══════════════════════════════════════════════════════════════════════


class TestChk1:
    def test_passes_when_required_credits_fit_declared_total(
        self, conn: sqlite3.Connection
    ) -> None:
        _add_chunk(conn, "โครงสร้างหลักสูตร รวมไม่น้อยกว่า 132 หน่วยกิต")
        for i in range(5):
            _add_course(conn, f"0602620{i}", credits=3)
        result = check_chk1(conn)
        assert result.passed
        assert result.checked == 1

    def test_catches_required_credits_exceeding_declared_total(
        self, conn: sqlite3.Connection
    ) -> None:
        _add_chunk(conn, "โครงสร้างหลักสูตร รวมไม่น้อยกว่า 132 หน่วยกิต")
        for i in range(50):
            _add_course(conn, f"060262{i:02d}", credits=3)  # 150 หน่วยกิต
        result = check_chk1(conn)
        assert not result.passed
        assert "เป็นไปไม่ได้ตามโครงสร้าง" in result.findings[0].detail

    def test_uses_most_common_declared_total_not_smallest(
        self, conn: sqlite3.Connection
    ) -> None:
        """เล่มพิมพ์เลขหน่วยกิตหลายที่ — ต้องเลือกค่าที่พบบ่อยสุด ไม่ใช่ค่าน้อยสุด.

        ถ้าเลือกค่าน้อยสุด (120) วิชาบังคับ 129 หน่วยกิตจะถูกรายงานผิด ๆ
        """
        for _ in range(3):
            _add_chunk(conn, "รวมไม่น้อยกว่า 132 หน่วยกิต")
        _add_chunk(conn, "หมวดวิชาเฉพาะ รวม 120 หน่วยกิต")
        for i in range(43):
            _add_course(conn, f"060262{i:02d}", credits=3)  # 129 หน่วยกิต
        assert check_chk1(conn).passed

    def test_skips_version_without_declared_total(
        self, conn: sqlite3.Connection
    ) -> None:
        _add_course(conn, "06026200", credits=3)
        result = check_chk1(conn)
        assert result.checked == 0


# ══════════════════════════════════════════════════════════════════════
# Report / orchestrator
# ══════════════════════════════════════════════════════════════════════


class TestRunAll:
    def test_runs_every_rule_once(self, conn: sqlite3.Connection) -> None:
        _add_course(conn, "06026200")
        report = run_all(conn)
        assert [r.rule for r in report.results] == [
            "CHK1", "CHK2", "CHK3", "CHK4", "CHK5", "CHK6", "CHK7",
        ]
        assert report.course_total == 1
        assert report.version_total == 1

    def test_ok_is_false_only_for_error_severity(
        self, conn: sqlite3.Connection
    ) -> None:
        """warning ต้องไม่ทำให้ผลรวมตก — error ต้องทำให้ตก."""
        # วิชาที่อยู่ในแผนแต่ไม่มีคำอธิบาย → CHK2 warning เท่านั้น
        _add_course(conn, "06026200")
        report = run_all(conn)
        assert report.warning_count > 0
        assert report.error_count == 0
        assert report.ok is True

        # รหัสผิดรูป → CHK3 error
        _add_course(conn, "BADCODE1")
        report = run_all(conn)
        assert report.error_count > 0
        assert report.ok is False

    def test_render_includes_every_rule_and_status(
        self, conn: sqlite3.Connection
    ) -> None:
        _add_course(conn, "06026200")
        text = render(run_all(conn))
        for rule in ("CHK1", "CHK2", "CHK3", "CHK4", "CHK5", "CHK6", "CHK7"):
            assert rule in text
        assert "วิธีอ่านผล" in text

    def test_report_json_is_serializable(self, conn: sqlite3.Connection) -> None:
        _add_course(conn, "BADCODE1")
        payload = run_all(conn).as_dict()
        # ต้อง dump ได้โดยไม่ error (ใช้เขียนไฟล์ json)
        text = json.dumps(payload, ensure_ascii=False)
        assert "CHK3" in text
        assert payload["ok"] is False


class TestPassRate:
    def test_pass_rate_is_one_when_nothing_to_check(
        self, conn: sqlite3.Connection
    ) -> None:
        result = check_chk5(conn)
        assert result.checked == 0
        assert result.pass_rate == 1.0

    def test_pass_rate_reflects_findings(self, conn: sqlite3.Connection) -> None:
        _add_course(conn, "06026200")
        _add_course(conn, "BADCODE1")
        result = check_chk3(conn)
        assert result.checked == 2
        assert result.pass_rate == 0.5


class TestEmptyDatabase:
    def test_all_rules_pass_on_empty_database(self, conn: sqlite3.Connection) -> None:
        """ฐานข้อมูลเปล่าต้องไม่ทำให้กฎ crash และต้องรายงาน checked = 0."""
        report = run_all(conn)
        assert report.ok
        assert all(r.checked == 0 for r in report.results)
        assert isinstance(report, ConsistencyReport)
