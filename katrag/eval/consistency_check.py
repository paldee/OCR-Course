"""Consistency check (CHK1-CHK7) — ตรวจว่าข้อมูลที่สกัดมา "สอดคล้องกันเอง" หรือไม่.

ทำไมต้องมีชั้นนี้
-----------------
การวัดผลที่มีอยู่เดิมต้องมีเฉลย: `eval/ocr_eval.py` เทียบกับ ground truth ของอาจารย์
ซึ่งมีแค่ 4 หลักสูตร ส่วน `store/integrity.py` ตรวจแค่ระดับ SQLite (foreign key,
PRAGMA integrity_check) ไม่ได้ดูเนื้อหา

กฎในไฟล์นี้ตรวจ **ความสอดคล้องภายในเอกสารเอง** จึงไม่ต้องมีเฉลย ใช้ได้กับทั้ง 13
เวอร์ชันหลักสูตร และรันซ้ำได้ทุกครั้งที่แก้ ingest — ทำหน้าที่เป็น regression guard
ที่ระบบยังขาด

หลักการของทุกกฎคือ "ข้อมูลชุดเดียวกันถูกเขียนไว้สองที่ในเอกสาร ต้องตรงกัน" หรือ
"ค่าต้องอยู่ในกรอบที่เป็นไปได้ตามธรรมชาติของหลักสูตร"

ระดับความรุนแรง
---------------
- `error`   ผิดแน่นอน ไม่มีทางเป็นข้อมูลที่ถูก (CHK3, CHK4, CHK5, CHK6)
- `warning` ต้องใช้คนดูประกอบ เพราะโครงสร้างหลักสูตรมีข้อยกเว้น (CHK1, CHK2, CHK7)

ทุกกฎรายงาน `checked` (จำนวนที่ตรวจได้จริง) เพื่อไม่ให้ตีความ "ผ่าน" เกินจริง
เมื่อข้อมูลที่ตรวจได้มีน้อย

Usage:
    python -m katrag.eval.consistency_check
    python -m katrag.eval.consistency_check --db path/to.sqlite3 --fail-on-error
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

# ── ค่าคงที่ของกฎ ─────────────────────────────────────────────────────

#: รหัสวิชาต้องเป็นเลข 8 หลักเท่านั้น (CHK3)
COURSE_CODE_RE = re.compile(r"^\d{8}$")

#: รหัส 8 หลักที่ปรากฏในข้อความ
CODE_IN_TEXT_RE = re.compile(r"\b(\d{8})\b")

#: หน่วยกิตรูปแบบ 3(2-2-5) ที่ตามหลังรหัสวิชาไม่เกิน 120 ตัวอักษร (CHK4)
CODE_CREDIT_RE = re.compile(r"\b(\d{8})\b[^\d]{0,120}?(\d)\s*\((\d)-(\d)-(\d+)\)")

#: คำที่บ่งชี้ว่าเป็นหน้าคำอธิบายรายวิชา (CHK2, CHK4)
DESC_KEYWORD_RE = re.compile(r"บังคับก่อน|PREREQUISITE", re.IGNORECASE)

#: ช่วงหน่วยกิตต่อภาคการศึกษาที่เป็นไปได้ (CHK7)
CREDITS_PER_TERM_MIN = 9
CREDITS_PER_TERM_MAX = 22

#: CHK7 ตรวจเฉพาะภาคที่มีวิชามากพอจะถือว่าข้อมูลครบ — ภาคที่มี 1-2 วิชา
#: มักเป็นภาคที่เนื้อหาจริงเป็นวิชาเลือก (placeholder `06026xxx` ไม่ถูกเก็บเป็น course)
#: จึงนับหน่วยกิตไม่ได้และไม่ควรรายงานเป็นปัญหา
CHK7_MIN_COURSES = 4

#: ความยาวข้อความหลังรหัสวิชาที่ถือว่าเป็น "คำอธิบายรายวิชา" แบบผ่อนปรน (CHK2)
CHK2_RELAXED_DESC_CHARS = 250

#: หน่วยกิตจบที่ประกาศในเล่ม — regex จับ "รวมไม่น้อยกว่า 132 หน่วยกิต" (CHK1)
DECLARED_TOTAL_RE = re.compile(r"(?:รวม|ไม่น้อยกว่า)[^\d]{0,20}(\d{3})\s*หน่วยกิต")

#: ช่วงหน่วยกิตจบที่เป็นไปได้ของปริญญาตรี — กันไม่ให้จับเลขในตารางอื่นมาเป็นเกณฑ์จบ
DECLARED_TOTAL_MIN = 120
DECLARED_TOTAL_MAX = 180


# ══════════════════════════════════════════════════════════════════════
# Data
# ══════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class Finding:
    """ปัญหาหนึ่งรายการที่กฎจับได้ — ต้องมีข้อมูลพอให้ตามไปดูเอกสารต้นทางได้.

    `severity` ว่างไว้ = ใช้ระดับของกฎ กฎบางข้อมีทั้งกรณีที่ผิดแน่และกรณีที่ต้องดู
    ประกอบ (เช่น CHK5: prerequisite อยู่ภาคหลังกว่า = ผิดแน่, อยู่ภาคเดียวกัน =
    อาจเป็น co-requisite ที่เล่มจัดไว้เอง) จึงต้องระบุเป็นรายการได้
    """

    rule: str
    version_label: str
    detail: str
    code: str = ""
    severity: str = ""

    def effective_severity(self, rule_severity: str) -> str:
        return self.severity or rule_severity

    def as_dict(self, rule_severity: str = "") -> dict[str, str]:
        return {
            "rule": self.rule,
            "version": self.version_label,
            "code": self.code,
            "severity": self.effective_severity(rule_severity),
            "detail": self.detail,
        }


@dataclass(slots=True)
class RuleResult:
    """ผลของกฎหนึ่งข้อ."""

    rule: str
    title: str
    catches: str
    severity: str
    checked: int = 0
    findings: list[Finding] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.findings

    @property
    def pass_rate(self) -> float:
        """สัดส่วนที่ผ่าน — 1.0 เมื่อไม่มีอะไรให้ตรวจ (ต้องอ่านคู่กับ checked)."""
        if self.checked <= 0:
            return 1.0
        return max(0.0, (self.checked - len(self.findings)) / self.checked)

    def count_by_severity(self, severity: str) -> int:
        """จำนวน finding ที่มีระดับตามที่ระบุ (นับระดับของ finding เป็นหลัก)."""
        return sum(
            1 for f in self.findings if f.effective_severity(self.severity) == severity
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "rule": self.rule,
            "title": self.title,
            "catches": self.catches,
            "severity": self.severity,
            "checked": self.checked,
            "finding_count": len(self.findings),
            "error_count": self.count_by_severity("error"),
            "warning_count": self.count_by_severity("warning"),
            "pass_rate": round(self.pass_rate, 4),
            "passed": self.passed,
            "findings": [f.as_dict(self.severity) for f in self.findings],
        }


@dataclass(slots=True)
class ConsistencyReport:
    """ผลรวมทุกกฎ."""

    results: list[RuleResult] = field(default_factory=list)
    course_total: int = 0
    version_total: int = 0

    @property
    def error_count(self) -> int:
        return sum(r.count_by_severity("error") for r in self.results)

    @property
    def warning_count(self) -> int:
        return sum(r.count_by_severity("warning") for r in self.results)

    @property
    def ok(self) -> bool:
        """ผ่านเมื่อไม่มี finding ระดับ error (warning ไม่ทำให้ตก)."""
        return self.error_count == 0

    def as_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "course_total": self.course_total,
            "version_total": self.version_total,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "rules": [r.as_dict() for r in self.results],
        }


# ══════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════


def _version_labels(conn: sqlite3.Connection) -> dict[int, str]:
    """version_id -> "IT/2565(current)" สำหรับใส่ในรายงาน."""
    rows = conn.execute(
        "SELECT version_id, program, curriculum_year, edition_status FROM curriculum_version"
    ).fetchall()
    return {
        r["version_id"]: f"{r['program']}/{r['curriculum_year']}({r['edition_status']})"
        for r in rows
    }


def _description_chunks(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """chunk ที่เป็นหน้าคำอธิบายรายวิชา (มีคำว่า วิชาบังคับก่อน / PREREQUISITE)."""
    return conn.execute(
        """
        SELECT version_id, text FROM chunk
        WHERE text LIKE '%บังคับก่อน%' OR text LIKE '%PREREQUISITE%'
           OR text LIKE '%Prerequisite%'
        """
    ).fetchall()


def _codes_in_description(conn: sqlite3.Connection) -> dict[int, set[str]]:
    """version_id -> เซตรหัสวิชาที่พบในหน้าคำอธิบายรายวิชา."""
    out: dict[int, set[str]] = {}
    for row in _description_chunks(conn):
        bucket = out.setdefault(row["version_id"], set())
        bucket.update(CODE_IN_TEXT_RE.findall(row["text"] or ""))
    return out


def _codes_with_long_text(conn: sqlite3.Connection) -> dict[int, set[str]]:
    """version_id -> รหัสวิชาที่มีข้อความบรรยายยาวตามหลัง (คำอธิบายแบบผ่อนปรน).

    ใช้กับ CHK2 เพื่อไม่รายงานวิชาศึกษาทั่วไปที่คำอธิบายอยู่ในภาคผนวกซึ่งไม่มี
    คำว่า "วิชาบังคับก่อน" กำกับ
    """
    out: dict[int, set[str]] = {}
    rows = conn.execute("SELECT version_id, text FROM chunk").fetchall()
    for row in rows:
        text = " ".join((row["text"] or "").split())
        bucket = out.setdefault(row["version_id"], set())
        for m in CODE_IN_TEXT_RE.finditer(text):
            if len(text) - m.end() >= CHK2_RELAXED_DESC_CHARS:
                bucket.add(m.group(1))
    return out


def _course_positions(conn: sqlite3.Connection) -> dict[tuple[int, str], tuple[int, int]]:
    """(version_id, code) -> (year, semester) เฉพาะวิชาที่รู้ตำแหน่งในแผน."""
    out: dict[tuple[int, str], tuple[int, int]] = {}
    rows = conn.execute(
        "SELECT version_id, code, year, semester FROM course "
        "WHERE year IS NOT NULL AND semester IS NOT NULL"
    ).fetchall()
    for r in rows:
        out[(r["version_id"], r["code"])] = (int(r["year"]), int(r["semester"]))
    return out


def _declared_total_credits(conn: sqlite3.Connection) -> dict[int, int]:
    """version_id -> หน่วยกิตจบที่เล่มประกาศไว้ (ค่าที่พบบ่อยที่สุด).

    เล่มพิมพ์เลขหน่วยกิตหลายที่ (โครงสร้างหลักสูตร, ตารางเปรียบเทียบ, สรุปหมวด)
    จึงใช้ค่าที่พบบ่อยที่สุดเป็นตัวแทน ไม่ใช่ค่าน้อยสุด — การใช้ค่าน้อยสุดเคยทำให้
    ได้ 105/120 ซึ่งเป็นโควตาหมวดย่อย ไม่ใช่เกณฑ์จบ
    """
    counters: dict[int, Counter[int]] = {}
    rows = conn.execute(
        "SELECT version_id, text FROM chunk WHERE text LIKE '%หน่วยกิต%'"
    ).fetchall()
    for row in rows:
        text = " ".join((row["text"] or "").split())
        for m in DECLARED_TOTAL_RE.finditer(text):
            total = int(m.group(1))
            if DECLARED_TOTAL_MIN <= total <= DECLARED_TOTAL_MAX:
                counters.setdefault(row["version_id"], Counter())[total] += 1
    return {
        vid: counter.most_common(1)[0][0]
        for vid, counter in counters.items()
        if counter
    }


def _parse_prereq_codes(raw: str | None) -> list[str]:
    """แปลง prerequisite_json เป็น list ของรหัส — คืน [] ถ้าว่างหรือ parse ไม่ได้."""
    if not raw or raw in ("", "[]", "null"):
        return []
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(value, list):
        return []
    return [str(c) for c in value if str(c).strip()]


# ══════════════════════════════════════════════════════════════════════
# CHK1 — หน่วยกิตรวมของแผน = หน่วยกิตที่ประกาศ
# ══════════════════════════════════════════════════════════════════════


def check_chk1(conn: sqlite3.Connection) -> RuleResult:
    """หน่วยกิตของวิชาบังคับต้องไม่เกินหน่วยกิตจบที่เล่มประกาศไว้.

    จับ: แผนตกหล่นไปทั้งภาคเรียน หรือมีวิชาจากหน้าอื่นปนเข้ามา

    ไม่ใช้เกณฑ์ "เท่ากันพอดี" เพราะผลรวมจากฐานข้อมูลนับได้แค่วิชาบังคับ —
    วิชาเลือกในเล่มเป็น placeholder (`06026xxx`) ที่ไม่มีรหัสจริงจึงไม่ถูกเก็บ
    เป็นรายวิชา ส่วนต่างที่เหลือคือโควตาวิชาเลือก ซึ่งรายงานไว้ให้คนอ่านตรวจ
    """
    result = RuleResult(
        rule="CHK1",
        title="หน่วยกิตรวมของแผน = หน่วยกิตที่ประกาศ",
        catches="แผนตกหล่นไปทั้งภาคเรียน หรือมีวิชาจากหน้าอื่นปนเข้ามา",
        severity="warning",
    )
    labels = _version_labels(conn)
    declared = _declared_total_credits(conn)

    required = conn.execute(
        "SELECT version_id, SUM(COALESCE(credits_total, 0)) AS tot, COUNT(*) AS n "
        "FROM course WHERE type = 'บังคับ' GROUP BY version_id"
    ).fetchall()

    for row in required:
        vid = row["version_id"]
        if vid not in declared:
            continue
        result.checked += 1
        total_required = int(row["tot"] or 0)
        total_declared = declared[vid]
        if total_required > total_declared:
            result.findings.append(
                Finding(
                    rule="CHK1",
                    version_label=labels.get(vid, str(vid)),
                    detail=(
                        f"หน่วยกิตวิชาบังคับรวม {total_required} > เกณฑ์จบที่ประกาศ "
                        f"{total_declared} หน่วยกิต ({row['n']} วิชาถูกจัดเป็นบังคับ) "
                        f"— เป็นไปไม่ได้ตามโครงสร้าง มักหมายถึงวิชาเลือกถูกจัดประเภทเป็นบังคับ"
                    ),
                )
            )
    return result


# ══════════════════════════════════════════════════════════════════════
# CHK2 — ทุกรหัสในแผน มีคำอธิบายรายวิชา
# ══════════════════════════════════════════════════════════════════════


def check_chk2(conn: sqlite3.Connection) -> RuleResult:
    """รหัสวิชาที่อยู่ในแผนการเรียน ต้องมีคำอธิบายรายวิชาในเล่มด้วย.

    จับ: รหัสผิดหลัก หรือหมวดคำอธิบายถูกสกัดมาไม่ครบ
    """
    result = RuleResult(
        rule="CHK2",
        title="ทุกรหัสในแผน มีคำอธิบายรายวิชา",
        catches="รหัสผิดหลัก หรือหมวดคำอธิบายไม่ครบ",
        severity="warning",
    )
    labels = _version_labels(conn)
    strict = _codes_in_description(conn)
    relaxed = _codes_with_long_text(conn)

    rows = conn.execute(
        "SELECT version_id, code, name_th FROM course WHERE year IS NOT NULL"
    ).fetchall()
    for r in rows:
        vid = r["version_id"]
        result.checked += 1
        code = r["code"]
        if code in strict.get(vid, set()) or code in relaxed.get(vid, set()):
            continue
        result.findings.append(
            Finding(
                rule="CHK2",
                version_label=labels.get(vid, str(vid)),
                code=code,
                detail=f"{code} {(r['name_th'] or '')[:34]} — ไม่พบคำอธิบายรายวิชาในเล่ม",
            )
        )
    return result


# ══════════════════════════════════════════════════════════════════════
# CHK3 — รหัสวิชาเป็นตัวเลข 8 หลัก
# ══════════════════════════════════════════════════════════════════════


def check_chk3(conn: sqlite3.Connection) -> RuleResult:
    """รหัสวิชาทุกตัวต้องเป็นเลข 8 หลักล้วน.

    จับ: OCR อ่านตัวอักษรปนเข้ามาในรหัส (เช่น O แทน 0, l แทน 1)
    """
    result = RuleResult(
        rule="CHK3",
        title="รหัสวิชาเป็นตัวเลข 8 หลัก",
        catches="OCR อ่านตัวอักษรปนเข้ามาในรหัส",
        severity="error",
    )
    labels = _version_labels(conn)
    rows = conn.execute("SELECT version_id, code, name_th FROM course").fetchall()
    for r in rows:
        result.checked += 1
        code = r["code"] or ""
        if COURSE_CODE_RE.match(code):
            continue
        result.findings.append(
            Finding(
                rule="CHK3",
                version_label=labels.get(r["version_id"], str(r["version_id"])),
                code=code,
                detail=f"รหัส {code!r} ไม่ใช่เลข 8 หลัก ({(r['name_th'] or '')[:30]})",
            )
        )
    return result


# ══════════════════════════════════════════════════════════════════════
# CHK4 — หน่วยกิตในแผน = หน่วยกิตในคำอธิบาย
# ══════════════════════════════════════════════════════════════════════


def check_chk4(conn: sqlite3.Connection) -> RuleResult:
    """หน่วยกิตในตารางแผน ต้องเท่ากับหน่วยกิตในหน้าคำอธิบายรายวิชา.

    จับ: ตัวเลขถูกสกัดผิดในแหล่งใดแหล่งหนึ่ง (เอกสารเขียนหน่วยกิตไว้สองที่)
    """
    result = RuleResult(
        rule="CHK4",
        title="หน่วยกิตในแผน = หน่วยกิตในคำอธิบาย",
        catches="ตัวเลขหน่วยกิตผิดในแหล่งใดแหล่งหนึ่ง",
        severity="error",
    )
    labels = _version_labels(conn)

    # หน่วยกิตที่พิมพ์ไว้ในหน้าคำอธิบายรายวิชา
    desc_credits: dict[tuple[int, str], int] = {}
    for row in _description_chunks(conn):
        text = " ".join((row["text"] or "").split())
        for m in CODE_CREDIT_RE.finditer(text):
            desc_credits.setdefault((row["version_id"], m.group(1)), int(m.group(2)))

    rows = conn.execute(
        "SELECT version_id, code, credits_total FROM course WHERE credits_total IS NOT NULL"
    ).fetchall()
    for r in rows:
        key = (r["version_id"], r["code"])
        if key not in desc_credits:
            continue
        result.checked += 1
        table_credits = int(r["credits_total"])
        doc_credits = desc_credits[key]
        if table_credits == doc_credits:
            continue
        result.findings.append(
            Finding(
                rule="CHK4",
                version_label=labels.get(r["version_id"], str(r["version_id"])),
                code=r["code"],
                detail=(
                    f"{r['code']}: ตารางแผน {table_credits} หน่วยกิต "
                    f"แต่คำอธิบายรายวิชา {doc_credits} หน่วยกิต"
                ),
            )
        )
    return result


# ══════════════════════════════════════════════════════════════════════
# CHK5 — วิชาบังคับก่อน อยู่ภาคที่มาก่อนจริง
# ══════════════════════════════════════════════════════════════════════


def check_chk5(conn: sqlite3.Connection) -> RuleResult:
    """วิชาบังคับก่อนต้องอยู่ภาคการศึกษาที่มาก่อนวิชาที่อ้างถึงมันเสมอ.

    จับ: สกัด prerequisite ผิดตัว, สกัดชั้นปี/ภาคผิด, หรือเล่มเขียนผิดเอง

    เป็นกฎที่ทรงพลังที่สุดในชุดนี้ เพราะเชื่อม 3 ฟิลด์ที่สกัดจากคนละหน้า
    (prerequisite จากหน้าคำอธิบาย, year/semester จากหน้าตารางแผน) เข้าด้วยกัน
    """
    result = RuleResult(
        rule="CHK5",
        title="วิชาบังคับก่อน อยู่ภาคที่มาก่อนจริง",
        catches="prerequisite ผิดตัว, ชั้นปี/ภาคผิด, หรือเล่มเขียนผิด",
        severity="error",
    )
    labels = _version_labels(conn)
    positions = _course_positions(conn)

    rows = conn.execute(
        "SELECT version_id, code, year, semester, prerequisite_json FROM course "
        "WHERE prerequisite_json IS NOT NULL AND year IS NOT NULL AND semester IS NOT NULL"
    ).fetchall()

    for r in rows:
        prereqs = _parse_prereq_codes(r["prerequisite_json"])
        if not prereqs:
            continue
        here = (int(r["year"]), int(r["semester"]))
        for pcode in prereqs:
            ppos = positions.get((r["version_id"], pcode))
            if ppos is None:
                continue  # ไม่รู้ตำแหน่งของวิชาบังคับก่อน — ตรวจไม่ได้
            result.checked += 1
            if ppos < here:
                continue
            # อยู่ภาคเดียวกัน = อาจเป็น co-requisite ที่เล่มจัดไว้เองจริง (ตรวจแล้วพบว่า
            # IT/2560 จัด 06016306 กับ 06016321 ในหน้าเดียวกัน) จึงเป็น warning
            # ส่วนอยู่ภาคหลังกว่า = เป็นไปไม่ได้ตามลำดับการเรียน จึงเป็น error
            same_term = ppos == here
            result.findings.append(
                Finding(
                    rule="CHK5",
                    version_label=labels.get(r["version_id"], str(r["version_id"])),
                    code=r["code"],
                    severity="warning" if same_term else "error",
                    detail=(
                        f"{r['code']} (ปี {here[0]} ภาค {here[1]}) ระบุว่าต้องผ่าน {pcode} "
                        f"แต่ {pcode} อยู่ปี {ppos[0]} ภาค {ppos[1]} — "
                        + (
                            "ภาคเดียวกัน (อาจเป็นวิชาเรียนร่วมภาคที่เล่มจัดไว้เอง)"
                            if same_term
                            else "ภาคหลังกว่า จึงเป็นไปไม่ได้"
                        )
                    ),
                )
            )
    return result


# ══════════════════════════════════════════════════════════════════════
# CHK6 — ไม่มีวิชาซ้ำในภาคเรียนเดียวกัน
# ══════════════════════════════════════════════════════════════════════


def check_chk6(conn: sqlite3.Connection) -> RuleResult:
    """รหัสวิชาเดียวกันต้องไม่ปรากฏซ้ำในภาคการศึกษาเดียวกัน.

    จับ: ตารางแผนถูกอ่านซ้ำสองรอบ (เช่น หน้าซ้ำ หรือ chunk ทับกัน)
    """
    result = RuleResult(
        rule="CHK6",
        title="ไม่มีวิชาซ้ำในภาคเรียนเดียวกัน",
        catches="ตารางแผนถูกอ่านซ้ำสองรอบ",
        severity="error",
    )
    labels = _version_labels(conn)

    total = conn.execute(
        "SELECT COUNT(*) AS n FROM course WHERE year IS NOT NULL AND semester IS NOT NULL"
    ).fetchone()
    result.checked = int(total["n"]) if total else 0

    rows = conn.execute(
        """
        SELECT version_id, code, year, semester, COUNT(*) AS n
        FROM course
        WHERE year IS NOT NULL AND semester IS NOT NULL
        GROUP BY version_id, code, year, semester
        HAVING n > 1
        """
    ).fetchall()
    for r in rows:
        result.findings.append(
            Finding(
                rule="CHK6",
                version_label=labels.get(r["version_id"], str(r["version_id"])),
                code=r["code"],
                detail=(
                    f"{r['code']} ปรากฏ {r['n']} ครั้งในปี {r['year']} ภาค {r['semester']}"
                ),
            )
        )
    return result


# ══════════════════════════════════════════════════════════════════════
# CHK7 — หน่วยกิตต่อภาคอยู่ระหว่าง 9-22
# ══════════════════════════════════════════════════════════════════════


def check_chk7(conn: sqlite3.Connection) -> RuleResult:
    """หน่วยกิตรวมต่อภาคการศึกษาต้องอยู่ในช่วงที่ลงทะเบียนได้จริง.

    จับ: บางวิชาตกหล่น หรือมีวิชาจากหน้าอื่นปนเข้ามา

    ตรวจเฉพาะภาคที่มีวิชาตั้งแต่ `CHK7_MIN_COURSES` ขึ้นไป เพราะภาคที่มีวิชาน้อย
    (ปี 3-4) เนื้อหาจริงเป็นวิชาเลือกที่เก็บเป็น placeholder ไม่มีหน่วยกิตในฐาน
    จึงนับรวมไม่ได้และไม่ใช่ความผิดพลาดของการสกัด
    """
    result = RuleResult(
        rule="CHK7",
        title=f"หน่วยกิตต่อภาคอยู่ระหว่าง {CREDITS_PER_TERM_MIN}-{CREDITS_PER_TERM_MAX}",
        catches="บางวิชาตกหล่น หรือมีวิชาจากหน้าอื่นปนเข้ามา",
        severity="warning",
    )
    labels = _version_labels(conn)

    rows = conn.execute(
        """
        SELECT version_id, year, semester,
               SUM(COALESCE(credits_total, 0)) AS tot,
               COUNT(*) AS n
        FROM course
        WHERE year IS NOT NULL AND semester IS NOT NULL
        GROUP BY version_id, year, semester
        ORDER BY version_id, year, semester
        """
    ).fetchall()

    for r in rows:
        if int(r["n"]) < CHK7_MIN_COURSES:
            continue
        result.checked += 1
        total = int(r["tot"] or 0)
        if CREDITS_PER_TERM_MIN <= total <= CREDITS_PER_TERM_MAX:
            continue
        direction = "น้อยกว่าเกณฑ์" if total < CREDITS_PER_TERM_MIN else "เกินเกณฑ์"
        result.findings.append(
            Finding(
                rule="CHK7",
                version_label=labels.get(r["version_id"], str(r["version_id"])),
                detail=(
                    f"ปี {r['year']} ภาค {r['semester']} รวม {total} หน่วยกิต "
                    f"({r['n']} วิชา) — {direction}"
                ),
            )
        )
    return result


# ══════════════════════════════════════════════════════════════════════
# Orchestrator
# ══════════════════════════════════════════════════════════════════════

CHECKS = (check_chk1, check_chk2, check_chk3, check_chk4, check_chk5, check_chk6, check_chk7)


def run_all(conn: sqlite3.Connection) -> ConsistencyReport:
    """รันทุกกฎแล้วรวมผล."""
    conn.row_factory = sqlite3.Row
    report = ConsistencyReport()
    report.results = [check(conn) for check in CHECKS]

    row = conn.execute("SELECT COUNT(*) AS n FROM course").fetchone()
    report.course_total = int(row["n"]) if row else 0
    row = conn.execute("SELECT COUNT(*) AS n FROM curriculum_version").fetchone()
    report.version_total = int(row["n"]) if row else 0
    return report


# ══════════════════════════════════════════════════════════════════════
# Render
# ══════════════════════════════════════════════════════════════════════

MAX_FINDINGS_IN_REPORT = 20


def render(report: ConsistencyReport) -> str:
    """สร้างรายงาน markdown."""
    lines: list[str] = [
        "# รายงานความสอดคล้องของข้อมูลที่สกัดมา (CHK1-CHK7)",
        "",
        "กฎชุดนี้ตรวจว่าข้อมูลที่สกัดจากเอกสาร **สอดคล้องกันเอง** หรือไม่ "
        "จึงไม่ต้องมีเฉลยจากอาจารย์ และใช้ได้กับทุกหลักสูตรในฐานข้อมูล",
        "",
        f"- รายวิชาทั้งหมด: **{report.course_total:,}** วิชา "
        f"จาก **{report.version_total}** เวอร์ชันหลักสูตร",
        f"- ผลรวม: **error {report.error_count} รายการ**, "
        f"warning {report.warning_count} รายการ",
        f"- สถานะ: **{'ผ่าน' if report.ok else 'ไม่ผ่าน'}** "
        f"(ตกเมื่อมี error, warning ต้องใช้คนอ่านประกอบ)",
        "",
        "## สรุปต่อกฎ",
        "",
        "| รหัส | กฎ | จับความผิดแบบไหน | ระดับ | ตรวจได้ | error | warning | ผ่าน |",
        "|---|---|---|---|---:|---:|---:|---:|",
    ]
    for r in report.results:
        lines.append(
            f"| {r.rule} | {r.title} | {r.catches} | {r.severity} | "
            f"{r.checked:,} | {r.count_by_severity('error'):,} | "
            f"{r.count_by_severity('warning'):,} | {r.pass_rate * 100:.1f}% |"
        )

    for r in report.results:
        lines += ["", f"## {r.rule} — {r.title}", ""]
        lines.append(f"- ระดับ: **{r.severity}**")
        lines.append(f"- จับ: {r.catches}")
        lines.append(f"- ตรวจได้ {r.checked:,} รายการ พบปัญหา {len(r.findings):,} รายการ")
        if r.passed:
            lines += ["", "ไม่พบปัญหา"]
            continue
        lines += ["", "| หลักสูตร | รายละเอียด |", "|---|---|"]
        for f in r.findings[:MAX_FINDINGS_IN_REPORT]:
            lines.append(f"| {f.version_label} | {f.detail} |")
        if len(r.findings) > MAX_FINDINGS_IN_REPORT:
            lines.append(
                f"| … | และอีก {len(r.findings) - MAX_FINDINGS_IN_REPORT} รายการ "
                f"(ดูทั้งหมดในไฟล์ json) |"
            )

    lines += [
        "",
        "---",
        "",
        "## วิธีอ่านผล",
        "",
        "- `error` = ผิดแน่นอน ไม่มีทางเป็นข้อมูลที่ถูกต้อง ควรแก้ที่ตัวสกัด",
        "- `warning` = ต้องดูประกอบ เพราะโครงสร้างหลักสูตรมีข้อยกเว้น "
        "(เช่น ภาคที่เนื้อหาเป็นวิชาเลือกซึ่งเก็บเป็น placeholder)",
        "- ต้องอ่าน **ตรวจได้** คู่กับ **ผ่าน** เสมอ — กฎที่ตรวจได้น้อย "
        "เปอร์เซ็นต์ผ่านสูงไม่ได้แปลว่าข้อมูลดี",
        "",
    ]
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════
# Entrypoint
# ══════════════════════════════════════════════════════════════════════


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parent.parent.parent
    ap = argparse.ArgumentParser(
        description="ตรวจความสอดคล้องของข้อมูลที่สกัดมา (CHK1-CHK7)"
    )
    ap.add_argument("--db", default=str(root / "artifacts" / "katrag.sqlite3"))
    ap.add_argument("--report", default=str(root / "artifacts" / "consistency_report.md"))
    ap.add_argument("--json", default=str(root / "artifacts" / "consistency_result.json"))
    ap.add_argument(
        "--fail-on-error",
        action="store_true",
        help="คืน exit code 1 เมื่อพบ finding ระดับ error (ใช้ใน CI)",
    )
    args = ap.parse_args(argv)

    conn = sqlite3.connect(args.db)
    try:
        report = run_all(conn)
    finally:
        conn.close()

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render(report), encoding="utf-8")
    Path(args.json).write_text(
        json.dumps(report.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"ตรวจ {report.course_total:,} วิชา จาก {report.version_total} เวอร์ชันหลักสูตร")
    for r in report.results:
        mark = "ผ่าน" if r.passed else f"พบ {len(r.findings)}"
        print(f"  {r.rule} [{r.severity:7}] ตรวจได้ {r.checked:5,} -> {mark}")
    print(f"error {report.error_count} / warning {report.warning_count}")
    print(f"report -> {args.report}")
    print(f"json   -> {args.json}")

    if args.fail_on_error and not report.ok:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
