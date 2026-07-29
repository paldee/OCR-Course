"""Gt_Normalizer — อ่าน teacher ground truth แบบ read-only แล้วสร้าง normalized output.

เปิดไฟล์ GT ด้วย ``open(path, "rb")`` เท่านั้น (R11.1) และเขียนผลลงใต้
``artifacts/gt_normalized/`` เท่านั้น ห้ามแก้ไขไฟล์ต้นฉบับ.

GT defects ที่จัดการ (design §2.3):
(a) กรองแถวคำแนะนำที่ code ขึ้นต้นด้วย "หมายเหตุ"
(b) แยกเซลล์รหัสทางเลือก เช่น "06016481\\nหรือ\\n06016482" เป็น alternative group
(c) coerce year/semester เป็น int
(d) แยก bucket ที่ปี/ภาคเป็น 0 → flexible
(e) map ชื่อหมวดที่เป็นคำพ้อง ผ่าน config/value_sets.toml [category_synonym]
(f) normalize prerequisite "ไม่มี" / null → empty set
(g) parse สตริงหน่วยกิต
(h) จับคู่แบบ multiset สำหรับรหัสที่ซ้ำภายในไฟล์เดียว

Requirements: 11.1, 11.9
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from katrag.ingest.fields.credits import parse_credits
from katrag.ingest.fields.prerequisite import parse_prerequisite, print_prerequisite
from katrag.errors import ParseFailure

logger = logging.getLogger(__name__)

# ── types ─────────────────────────────────────────────────────────────


@dataclass(slots=True)
class NormalizedCourse:
    """หนึ่งแถววิชาหลัง normalize."""

    code: str
    alternative_codes: list[str] = field(default_factory=list)
    name_th: str = ""
    name_en: str = ""
    credits_raw: str = ""
    credits_total: int | None = None
    credits_lecture: int | None = None
    credits_lab: int | None = None
    credits_self_study: int | None = None
    year: int = 0
    semester: int = 0
    category: str = ""
    type_: str = ""
    prerequisite_raw: str = ""
    prerequisite_codes: list[str] = field(default_factory=list)
    flexible_year_semester: str | None = None
    note: str = ""
    is_flexible: bool = False


@dataclass(slots=True)
class NormalizedGtFile:
    """ผลลัพธ์ของ GT file หนึ่งไฟล์หลัง normalize."""

    source_path: str
    program: str
    plan: str | None
    courses: list[NormalizedCourse] = field(default_factory=list)
    filtered_hint_rows: int = 0
    code_counts: dict[str, int] = field(default_factory=dict)


# ── constants ─────────────────────────────────────────────────────────

# Pattern to detect hint/template rows — code field starts with "หมายเหตุ"
_HINT_PREFIX = "หมายเหตุ"

# Pattern to split alternative codes: "06016481\nหรือ\n06016482" or "06016481 หรือ 06016482"
_ALT_CODE_SPLIT_RE = re.compile(r"\s*หรือ\s*")

# Pattern for valid course code (8 digits or with 'xxx' placeholder)
_CODE_SHAPE_RE = re.compile(r"^\d{8}$|^\d{5}xxx$")


# ── public API ────────────────────────────────────────────────────────


def normalize_gt_file(
    gt_path: Path,
    category_synonym: Mapping[str, str],
) -> NormalizedGtFile:
    """อ่านไฟล์ GT หนึ่งไฟล์ (read-only) แล้ว normalize ทุก defect.

    Parameters
    ----------
    gt_path : Path
        พาธไปยังไฟล์ GT (JSON)
    category_synonym : Mapping[str, str]
        ตาราง synonym ของ category จาก value_sets.toml [category_synonym]

    Returns
    -------
    NormalizedGtFile
        ผลลัพธ์หลัง normalize
    """
    # R11.1: เปิดแบบ read-only binary เท่านั้น
    with open(gt_path, "rb") as f:
        raw_data: dict[str, Any] = json.load(f)

    program: str = raw_data.get("program", "")
    plan: str | None = raw_data.get("plan")
    courses_raw: list[dict[str, Any]] = raw_data.get("courses", [])

    result = NormalizedGtFile(
        source_path=str(gt_path),
        program=program,
        plan=plan,
    )

    code_counter: Counter[str] = Counter()

    for row in courses_raw:
        code_raw = row.get("code", "")
        if code_raw is None:
            code_raw = ""

        # (a) กรองแถวคำแนะนำที่ code ขึ้นต้นด้วย "หมายเหตุ"
        if _is_hint_row(str(code_raw)):
            result.filtered_hint_rows += 1
            logger.debug("Filtered hint row: %s", str(code_raw)[:60])
            continue

        # (b) แยกเซลล์รหัสทางเลือก
        codes = _split_alternative_codes(str(code_raw))
        primary_code = codes[0] if codes else str(code_raw).strip()
        alternative_codes = codes[1:] if len(codes) > 1 else []

        # (c) coerce year/semester เป็น int
        year = _coerce_int(row.get("year", 0))
        semester = _coerce_int(row.get("semester", 0))

        # (d) แยก bucket ที่ปี/ภาคเป็น 0
        is_flexible = year == 0 or semester == 0

        # (e) map ชื่อหมวดที่เป็นคำพ้อง (R11.9)
        category_raw = row.get("category", "") or ""
        category = category_synonym.get(category_raw, category_raw)

        # (f) normalize prerequisite "ไม่มี" / null → empty
        prerequisite_raw = row.get("prerequisite") or ""
        prereq_codes = _normalize_prerequisite(str(prerequisite_raw))

        # (g) parse สตริงหน่วยกิต
        credits_raw = row.get("credits", "") or ""
        credits_parsed = _parse_credits_field(str(credits_raw))

        # flexible_year_semester field
        flex_ys = row.get("flexible_year_semester")
        if flex_ys is not None:
            flex_ys = str(flex_ys)

        # note
        note_raw = row.get("note")
        note = str(note_raw) if note_raw is not None else ""

        # type field
        type_raw = row.get("type", "") or ""

        # name fields
        name_th = row.get("name_th", "") or ""
        name_en = row.get("name_en", "") or ""

        course = NormalizedCourse(
            code=primary_code,
            alternative_codes=alternative_codes,
            name_th=str(name_th),
            name_en=str(name_en),
            credits_raw=str(credits_raw),
            credits_total=credits_parsed.get("total") if credits_parsed else None,
            credits_lecture=credits_parsed.get("lecture") if credits_parsed else None,
            credits_lab=credits_parsed.get("lab") if credits_parsed else None,
            credits_self_study=credits_parsed.get("self_study") if credits_parsed else None,
            year=year,
            semester=semester,
            category=category,
            type_=str(type_raw),
            prerequisite_raw=str(prerequisite_raw),
            prerequisite_codes=prereq_codes,
            flexible_year_semester=flex_ys,
            note=note,
            is_flexible=is_flexible,
        )
        result.courses.append(course)

        # (h) multiset: นับจำนวน code ที่ซ้ำภายในไฟล์
        code_counter[primary_code] += 1
        for alt in alternative_codes:
            code_counter[alt] += 1

    result.code_counts = dict(code_counter)
    return result


def normalize_all_gt_files(
    teacher_gt_dir: Path,
    output_dir: Path,
    category_synonym: Mapping[str, str],
) -> list[NormalizedGtFile]:
    """อ่าน GT ทุกไฟล์ใน teacher_gt_dir แล้วเขียน normalized JSON ลง output_dir.

    Parameters
    ----------
    teacher_gt_dir : Path
        ไดเรกทอรีที่เก็บไฟล์ GT ต้นฉบับ (read-only)
    output_dir : Path
        ไดเรกทอรี artifacts/gt_normalized/ ที่จะเขียนผลลง
    category_synonym : Mapping[str, str]
        ตาราง synonym ของ category จาก value_sets.toml

    Returns
    -------
    list[NormalizedGtFile]
        รายการผลลัพธ์ที่ normalize แล้ว
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    gt_files = _discover_gt_files(teacher_gt_dir)
    results: list[NormalizedGtFile] = []

    for gt_path in gt_files:
        logger.info("Normalizing GT: %s", gt_path.name)
        normalized = normalize_gt_file(gt_path, category_synonym)
        results.append(normalized)

        # เขียนผลลง output_dir โดยรักษาโครงสร้างย่อย
        relative = gt_path.relative_to(teacher_gt_dir)
        out_path = output_dir / relative
        out_path.parent.mkdir(parents=True, exist_ok=True)
        _write_normalized_json(normalized, out_path)

        logger.info(
            "  → %d courses, %d filtered hints, %d duplicate codes",
            len(normalized.courses),
            normalized.filtered_hint_rows,
            sum(1 for c in normalized.code_counts.values() if c > 1),
        )

    return results


def build_multiset_index(
    normalized: NormalizedGtFile,
) -> dict[str, list[NormalizedCourse]]:
    """สร้าง multiset index จาก code → list of courses.

    ใช้สำหรับการจับคู่ที่รองรับรหัสซ้ำภายในไฟล์เดียว (defect h).
    """
    index: dict[str, list[NormalizedCourse]] = {}
    for course in normalized.courses:
        # Primary code
        index.setdefault(course.code, []).append(course)
        # Alternative codes ก็ต้องจับคู่ได้
        for alt in course.alternative_codes:
            index.setdefault(alt, []).append(course)
    return index


# ── internal helpers ──────────────────────────────────────────────────


def _is_hint_row(code: str) -> bool:
    """ตรวจว่าแถวนี้เป็นคำแนะนำจาก spreadsheet template หรือไม่."""
    stripped = code.strip()
    return stripped.startswith(_HINT_PREFIX)


def _split_alternative_codes(code_raw: str) -> list[str]:
    """แยกเซลล์รหัสทางเลือก เช่น "06016481\\nหรือ\\n06016482".

    คืน list ของรหัสที่ split ได้ หรือ list เดี่ยวถ้าไม่มี "หรือ".
    """
    # NFC normalize ก่อน split
    normalized = unicodedata.normalize("NFC", code_raw.strip())
    # Replace newlines with spaces for uniform splitting
    uniform = normalized.replace("\n", " ").replace("\r", " ")
    # Split by "หรือ"
    parts = _ALT_CODE_SPLIT_RE.split(uniform)
    # Trim each part
    codes = [p.strip() for p in parts if p.strip()]
    return codes if codes else [code_raw.strip()]


def _coerce_int(value: Any) -> int:
    """Coerce year/semester ที่อาจเป็น str หรือ int ให้เป็น int.

    คืน 0 ถ้า coerce ไม่สำเร็จ.
    """
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            return int(stripped)
        # Try to parse as int
        try:
            return int(stripped)
        except (ValueError, TypeError):
            return 0
    try:
        return int(value)
    except (ValueError, TypeError):
        return 0


def _normalize_prerequisite(prereq_raw: str) -> list[str]:
    """Normalize prerequisite field.

    "ไม่มี", null, empty → empty list
    Otherwise, parse and extract course codes.
    """
    stripped = prereq_raw.strip()
    if not stripped or stripped == "ไม่มี" or stripped == "-" or stripped == "null":
        return []

    result = parse_prerequisite(stripped)
    if isinstance(result, ParseFailure):
        # Can't parse — return raw text as single item for manual review
        logger.debug("Prerequisite parse failed: %s", stripped)
        return [stripped]

    # Extract codes from parsed tree
    from katrag.common.types import prereq_codes as extract_codes

    codes = extract_codes(result)
    return list(codes)


def _parse_credits_field(credits_raw: str) -> dict[str, int] | None:
    """Parse สตริงหน่วยกิต ถ้าเป็นรูปแบบ standard.

    สำหรับ credits ที่มี "หรือ" (alternative credits) ให้ parse เฉพาะตัวแรก.
    """
    stripped = credits_raw.strip()
    if not stripped:
        return None

    # ถ้ามี "หรือ" → ลองแค่ตัวแรก
    if "หรือ" in stripped:
        parts = stripped.replace("\n", " ").split("หรือ")
        first = parts[0].strip()
        if first:
            stripped = first
        else:
            return None

    result = parse_credits(stripped)
    if isinstance(result, ParseFailure):
        logger.debug("Credits parse failed for '%s': %s", stripped, result.reason)
        return None

    return {
        "total": result.total,
        "lecture": result.lecture,
        "lab": result.lab,
        "self_study": result.self_study,
    }


def _discover_gt_files(teacher_gt_dir: Path) -> list[Path]:
    """ค้นหาไฟล์ JSON ทั้งหมดใน teacher_gt_dir (recursive).

    ข้าม example_ground_truth.json (template เปล่า) และ rules_ground_truth.json
    (ไม่ใช่ course data).
    """
    skip_names = {"example_ground_truth.json"}
    gt_files: list[Path] = []

    if not teacher_gt_dir.is_dir():
        logger.warning("Teacher GT directory not found: %s", teacher_gt_dir)
        return []

    for path in sorted(teacher_gt_dir.rglob("*.json")):
        if path.name in skip_names:
            continue
        gt_files.append(path)

    return gt_files


def _write_normalized_json(normalized: NormalizedGtFile, out_path: Path) -> None:
    """เขียน normalized GT เป็น JSON."""
    output: dict[str, Any] = {
        "source_path": normalized.source_path,
        "program": normalized.program,
        "plan": normalized.plan,
        "filtered_hint_rows": normalized.filtered_hint_rows,
        "total_courses": len(normalized.courses),
        "duplicate_codes": {
            code: count
            for code, count in sorted(normalized.code_counts.items())
            if count > 1
        },
        "courses": [_course_to_dict(c) for c in normalized.courses],
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)


def _course_to_dict(course: NormalizedCourse) -> dict[str, Any]:
    """แปลง NormalizedCourse เป็น dict สำหรับ JSON output."""
    return {
        "code": course.code,
        "alternative_codes": course.alternative_codes,
        "name_th": course.name_th,
        "name_en": course.name_en,
        "credits_raw": course.credits_raw,
        "credits": {
            "total": course.credits_total,
            "lecture": course.credits_lecture,
            "lab": course.credits_lab,
            "self_study": course.credits_self_study,
        }
        if course.credits_total is not None
        else None,
        "year": course.year,
        "semester": course.semester,
        "category": course.category,
        "type": course.type_,
        "prerequisite_raw": course.prerequisite_raw,
        "prerequisite_codes": course.prerequisite_codes,
        "flexible_year_semester": course.flexible_year_semester,
        "note": course.note,
        "is_flexible": course.is_flexible,
    }
