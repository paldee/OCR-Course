"""OCR Evaluation — วัดคุณภาพ OCR/สกัดข้อมูล เทียบกับ teacher ground truth.

วัด 3 ระดับ:
  1. PAGE LEVEL     — วิธีสกัด (text_layer / OCR), คุณภาพหน้า, out-of-charset ratio
                      ต่อเอกสาร (ตอบคำถาม "เล่มไหนใช้วิธีอะไร ดีแค่ไหน")
  2. FIELD LEVEL    — เทียบฟิลด์รายวิชาที่สกัดได้ กับ GT ทีละฟิลด์
                      (code / name_th / name_en / credits / year / semester)
  3. CATEGORY LEVEL — recall ต่อหมวดวิชา (วิชาบังคับ / เลือก / ศึกษาทั่วไป ฯลฯ)

GT เป็น read-only (R11.1) — เปิดด้วย open(path, "rb") เท่านั้น

Usage:
    python -m katrag.eval.ocr_eval
    python -m katrag.eval.ocr_eval --report artifacts/ocr_eval_report.md
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ══════════════════════════════════════════════════════════════════════
# Normalization (ให้เทียบแบบยุติธรรม — ตัดช่องว่าง/ตัวพิมพ์/วรรณยุกต์ซ้ำ)
# ══════════════════════════════════════════════════════════════════════


def norm_text(s: str | None) -> str:
    """NFC + ตัดช่องว่างซ้ำ + lower — สำหรับเทียบชื่อวิชา."""
    if not s:
        return ""
    s = unicodedata.normalize("NFC", str(s))
    s = " ".join(s.split())
    return s.lower()


def norm_credits(s: str | None) -> str:
    """normalize หน่วยกิต: '3(2-2-5)' / '3 (2-2-5)' → '3(2-2-5)'."""
    if not s:
        return ""
    s = unicodedata.normalize("NFC", str(s))
    return "".join(s.split())


def norm_code(s: str | None) -> str:
    """normalize รหัสวิชา — ตัดช่องว่าง."""
    if not s:
        return ""
    return "".join(str(s).split())


# ══════════════════════════════════════════════════════════════════════
# Data containers
# ══════════════════════════════════════════════════════════════════════


@dataclass
class PageLevelResult:
    """ผลวัดระดับหน้า ต่อเอกสารหนึ่งเล่ม."""

    document: str
    total_pages: int
    text_layer_pages: int
    ocr_pages: int
    mean_quality: float
    mean_out_of_charset: float
    ocr_mean_quality: float | None
    ocr_mean_confidence: float | None

    @property
    def ocr_ratio(self) -> float:
        return self.ocr_pages / self.total_pages if self.total_pages else 0.0

    @property
    def method_label(self) -> str:
        if self.ocr_pages == 0:
            return "text_layer เท่านั้น"
        if self.text_layer_pages == 0:
            return "OCR (Tesseract 5) เท่านั้น"
        return "ผสม (text_layer + Tesseract 5)"


@dataclass
class FieldStat:
    """สถิติต่อฟิลด์หนึ่งฟิลด์."""

    name: str
    matched: int = 0
    compared: int = 0
    mismatches: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        return self.matched / self.compared if self.compared else 0.0


@dataclass
class ProgramResult:
    """ผลวัดต่อหลักสูตร."""

    program: str
    gt_file: str
    version_label: str
    gt_total: int
    extracted_total: int
    matched_codes: int
    gt_only_codes: list[str] = field(default_factory=list)
    extracted_only_codes: list[str] = field(default_factory=list)
    fields: dict[str, FieldStat] = field(default_factory=dict)
    category_recall: dict[str, tuple[int, int]] = field(default_factory=dict)

    @property
    def code_recall(self) -> float:
        return self.matched_codes / self.gt_total if self.gt_total else 0.0

    @property
    def code_precision(self) -> float:
        return self.matched_codes / self.extracted_total if self.extracted_total else 0.0

    @property
    def code_f1(self) -> float:
        p, r = self.code_precision, self.code_recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    @property
    def field_macro_accuracy(self) -> float:
        vals = [f.accuracy for f in self.fields.values() if f.compared]
        return sum(vals) / len(vals) if vals else 0.0


# ══════════════════════════════════════════════════════════════════════
# 1. PAGE LEVEL
# ══════════════════════════════════════════════════════════════════════


def evaluate_page_level(conn: sqlite3.Connection) -> list[PageLevelResult]:
    """วัดระดับหน้า ต่อเอกสาร — วิธีสกัด + คุณภาพ."""
    conn.row_factory = sqlite3.Row
    docs = conn.execute(
        "SELECT document_id, relative_path, page_count FROM document ORDER BY relative_path"
    ).fetchall()

    results: list[PageLevelResult] = []
    for d in docs:
        did = d["document_id"]
        name = Path(d["relative_path"]).name

        method_rows = conn.execute(
            "SELECT extraction_method, COUNT(*) n FROM page "
            "WHERE document_id=? GROUP BY extraction_method",
            (did,),
        ).fetchall()
        text_layer = sum(r["n"] for r in method_rows if r["extraction_method"] == "text_layer")
        ocr_pages = sum(
            r["n"] for r in method_rows
            if r["extraction_method"] and r["extraction_method"].startswith("ocr")
        )

        qrow = conn.execute(
            "SELECT AVG(pm.page_quality_score) q, AVG(pm.out_of_charset_ratio) oc "
            "FROM page_metrics pm JOIN page p ON p.page_id = pm.page_id "
            "WHERE p.document_id=?",
            (did,),
        ).fetchone()

        orow = conn.execute(
            "SELECT AVG(osr.quality_score) q, AVG(osr.confidence) c "
            "FROM ocr_stage_result osr "
            "JOIN region rg ON rg.region_id = osr.region_id "
            "JOIN page p ON p.page_id = rg.page_id "
            "WHERE p.document_id=? AND osr.is_selected=1",
            (did,),
        ).fetchone()

        results.append(
            PageLevelResult(
                document=name,
                total_pages=d["page_count"],
                text_layer_pages=text_layer,
                ocr_pages=ocr_pages,
                mean_quality=qrow["q"] or 0.0,
                mean_out_of_charset=qrow["oc"] or 0.0,
                ocr_mean_quality=orow["q"] if orow and orow["q"] is not None else None,
                ocr_mean_confidence=orow["c"] if orow and orow["c"] is not None else None,
            )
        )
    return results


# ══════════════════════════════════════════════════════════════════════
# 2 + 3. FIELD LEVEL + CATEGORY LEVEL
# ══════════════════════════════════════════════════════════════════════

# GT file → (program, ปีหลักสูตรที่ควรเทียบ)
GT_TARGETS: list[tuple[str, str, int]] = [
    ("AIT/AIT_academic_plan.json", "AIT", 2566),
    ("BIT/BIT_academic_plan_no_coop.json", "BIT", 2565),
    ("DSBA/DSBA_academic_plan_no_coop.json", "DSBA", 2565),
    ("IT/IT_academic_plan_no_coop.json", "IT", 2565),
]

FIELD_SPECS: list[tuple[str, str]] = [
    ("name_th", "ชื่อวิชา (ไทย)"),
    ("name_en", "ชื่อวิชา (อังกฤษ)"),
    ("credits", "หน่วยกิต"),
    ("year", "ชั้นปี"),
    ("semester", "ภาคการศึกษา"),
]


def load_gt(gt_path: Path) -> dict[str, Any]:
    """อ่าน GT แบบ read-only (R11.1)."""
    with open(gt_path, "rb") as f:
        return json.load(f)


def _resolve_version(conn: sqlite3.Connection, program: str, year: int) -> tuple[int, str] | None:
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT version_id, program, curriculum_year, edition_status "
        "FROM curriculum_version WHERE program=? AND curriculum_year=?",
        (program, year),
    ).fetchone()
    if not row:
        return None
    return row["version_id"], f"{row['program']} {row['curriculum_year']} ({row['edition_status']})"


def evaluate_program(
    conn: sqlite3.Connection, gt_root: Path, gt_rel: str, program: str, year: int
) -> ProgramResult | None:
    """วัด field-level + category-level ของหลักสูตรหนึ่ง."""
    conn.row_factory = sqlite3.Row
    gt_path = gt_root / gt_rel
    if not gt_path.is_file():
        return None

    resolved = _resolve_version(conn, program, year)
    if not resolved:
        return None
    version_id, version_label = resolved

    gt = load_gt(gt_path)
    gt_courses_raw = gt.get("courses", [])

    # ── index GT ตามรหัสวิชา (ข้ามแถวคำแนะนำ/รหัส placeholder) ──
    gt_index: dict[str, dict] = {}
    gt_categories: dict[str, list[str]] = defaultdict(list)
    for c in gt_courses_raw:
        code = norm_code(c.get("code"))
        if not code or code.startswith("หมายเหตุ"):
            continue
        # รหัส placeholder เช่น 06026xxx — ไม่ใช่วิชาจริง ข้าม
        if "x" in code.lower():
            continue
        if code not in gt_index:
            gt_index[code] = c
        gt_categories[str(c.get("category") or "(ไม่ระบุหมวด)")].append(code)

    # ── index ข้อมูลที่ระบบสกัดได้ ──
    ext_rows = conn.execute(
        "SELECT code, name_th, name_en, credits_raw, year, semester "
        "FROM course WHERE version_id=?",
        (version_id,),
    ).fetchall()
    ext_index: dict[str, sqlite3.Row] = {}
    for r in ext_rows:
        code = norm_code(r["code"])
        if code and code not in ext_index:
            ext_index[code] = r

    gt_codes = set(gt_index)
    ext_codes = set(ext_index)
    matched = gt_codes & ext_codes

    result = ProgramResult(
        program=program,
        gt_file=gt_rel,
        version_label=version_label,
        gt_total=len(gt_codes),
        extracted_total=len(ext_codes),
        matched_codes=len(matched),
        gt_only_codes=sorted(gt_codes - ext_codes),
        extracted_only_codes=sorted(ext_codes - gt_codes),
    )

    # ── FIELD LEVEL: เทียบเฉพาะรหัสที่จับคู่ได้ ──
    for fname, _label in FIELD_SPECS:
        result.fields[fname] = FieldStat(name=fname)

    for code in sorted(matched):
        g = gt_index[code]
        e = ext_index[code]

        pairs = [
            ("name_th", norm_text(g.get("name_th")), norm_text(e["name_th"])),
            ("name_en", norm_text(g.get("name_en")), norm_text(e["name_en"])),
            ("credits", norm_credits(g.get("credits")), norm_credits(e["credits_raw"])),
        ]
        # year/semester: GT ใช้ 0 แทน "ยืดหยุ่น" — เทียบเฉพาะเมื่อ GT ระบุจริง
        gy, gs = g.get("year"), g.get("semester")
        try:
            gy_i = int(gy) if gy not in (None, "") else 0
            gs_i = int(gs) if gs not in (None, "") else 0
        except (TypeError, ValueError):
            gy_i = gs_i = 0
        if gy_i:
            pairs.append(("year", str(gy_i), str(e["year"] or "")))
        if gs_i:
            pairs.append(("semester", str(gs_i), str(e["semester"] or "")))

        for fname, gval, eval_ in pairs:
            stat = result.fields[fname]
            # ข้ามถ้า GT ไม่มีข้อมูลฟิลด์นั้น (ไม่นับเป็นความผิดของระบบ)
            if gval == "":
                continue
            stat.compared += 1
            if gval == eval_:
                stat.matched += 1
            elif len(stat.mismatches) < 8:
                stat.mismatches.append((code, gval, eval_))

    # ── CATEGORY LEVEL: recall ต่อหมวด ──
    for cat, codes in gt_categories.items():
        uniq = {c for c in codes if c in gt_codes}
        found = len(uniq & ext_codes)
        result.category_recall[cat] = (found, len(uniq))

    return result


# ══════════════════════════════════════════════════════════════════════
# Report
# ══════════════════════════════════════════════════════════════════════


def build_report(
    page_results: list[PageLevelResult], prog_results: list[ProgramResult]
) -> str:
    L: list[str] = []
    A = L.append

    A("# รายงานการประเมิน OCR — KatRAG-lite")
    A("")
    A("ประเมิน 3 ระดับ: **page level** (วิธีสกัด/คุณภาพหน้า), **field level** ")
    A("(เทียบฟิลด์รายวิชากับ ground truth ของอาจารย์), **category level** (recall ต่อหมวดวิชา)")
    A("")
    A("Ground truth เปิดแบบ read-only เท่านั้น ไม่มีการเขียนกลับหรือใช้เป็นแหล่งคำตอบ")
    A("")

    # ── สรุปผู้บริหาร ──
    tot_pages = sum(p.total_pages for p in page_results)
    tot_ocr = sum(p.ocr_pages for p in page_results)
    tot_text = sum(p.text_layer_pages for p in page_results)
    A("## สรุปภาพรวม")
    A("")
    A(f"- เอกสารทั้งหมด **{len(page_results)} เล่ม / {tot_pages:,} หน้า**")
    A(f"- สกัดด้วย text layer **{tot_text:,} หน้า** ({tot_text/tot_pages*100:.1f}%)")
    A(f"- สกัดด้วย OCR Tesseract 5 **{tot_ocr:,} หน้า** ({tot_ocr/tot_pages*100:.1f}%)")
    if prog_results:
        mean_code_f1 = sum(r.code_f1 for r in prog_results) / len(prog_results)
        mean_field = sum(r.field_macro_accuracy for r in prog_results) / len(prog_results)
        A(f"- ค่าเฉลี่ย **course-code F1 = {mean_code_f1:.3f}**, "
          f"**field macro-accuracy = {mean_field:.3f}** (เทียบ GT {len(prog_results)} หลักสูตร)")
    A("")

    # ── 1. PAGE LEVEL ──
    A("## 1. Page level — เล่มไหนใช้วิธีอะไร คุณภาพเท่าไร")
    A("")
    A("| เอกสาร | หน้า | text_layer | OCR | วิธีที่ใช้ | page quality | out-of-charset | OCR conf |")
    A("|---|---:|---:|---:|---|---:|---:|---:|")
    for p in page_results:
        occ = f"{p.ocr_mean_confidence:.3f}" if p.ocr_mean_confidence is not None else "—"
        A(f"| {p.document} | {p.total_pages} | {p.text_layer_pages} | {p.ocr_pages} | "
          f"{p.method_label} | {p.mean_quality:.3f} | {p.mean_out_of_charset:.4f} | {occ} |")
    A("")
    A("**อ่านค่า:**")
    A("")
    A("- `page quality` — คะแนนคุณภาพหน้า (0-1) จาก char count, สัดส่วนภาพ, คำในคลังศัพท์เฉพาะทาง")
    A("- `out-of-charset` — สัดส่วนอักขระที่อยู่นอกชุดอักขระที่คาดหวัง (ยิ่งต่ำยิ่งดี; สูง = OCR เพี้ยน)")
    A("- `OCR conf` — ค่าความมั่นใจของ Tesseract เฉพาะหน้าที่ผ่าน OCR (— = ไม่มีหน้าที่ต้อง OCR)")
    A("")
    A("**เหตุผลการเลือกวิธี:** ระบบ route แต่ละหน้าอัตโนมัติ (`page_router`) — หน้าที่มี text layer ")
    A("คุณภาพดีใช้ text layer ตรง ๆ (แม่นกว่าและเร็วกว่า) หน้าที่เป็นภาพสแกน/text layer เสียจึงส่งเข้า OCR")
    A("")

    if not prog_results:
        A("> ไม่พบผลระดับฟิลด์ (ไม่มี GT ที่จับคู่ได้)")
        return "\n".join(L)

    # ── 2. FIELD LEVEL ──
    A("## 2. Field level — เทียบฟิลด์รายวิชากับ ground truth")
    A("")
    A("### 2.1 ความครบถ้วนของรหัสวิชา (course-code coverage)")
    A("")
    A("| หลักสูตร | เวอร์ชันที่เทียบ | GT | ระบบสกัดได้ | จับคู่ได้ | Recall | Precision | F1 |")
    A("|---|---|---:|---:|---:|---:|---:|---:|")
    for r in prog_results:
        A(f"| {r.program} | {r.version_label} | {r.gt_total} | {r.extracted_total} | "
          f"{r.matched_codes} | {r.code_recall:.3f} | {r.code_precision:.3f} | {r.code_f1:.3f} |")
    A("")
    A("- **Recall** = วิชาใน GT ที่ระบบสกัดเจอ / วิชาใน GT ทั้งหมด (วัดว่า OCR ตกวิชาไปไหม)")
    A("- **Precision** = วิชาที่สกัดได้ตรงกับ GT / วิชาที่สกัดได้ทั้งหมด "
      "(ต่ำได้เพราะระบบสกัดวิชาเลือกทั้งเล่ม ซึ่ง GT แผนการเรียนไม่ได้ครอบคลุมทุกตัว)")
    A("")

    A("### 2.2 ความถูกต้องต่อฟิลด์ (เฉพาะวิชาที่จับคู่รหัสได้)")
    A("")
    header = "| หลักสูตร | " + " | ".join(lbl for _, lbl in FIELD_SPECS) + " | macro |"
    sep = "|---|" + "---:|" * (len(FIELD_SPECS) + 1)
    A(header)
    A(sep)
    for r in prog_results:
        cells = []
        for fname, _ in FIELD_SPECS:
            st = r.fields.get(fname)
            cells.append(f"{st.accuracy:.3f} ({st.matched}/{st.compared})" if st and st.compared else "—")
        A(f"| {r.program} | " + " | ".join(cells) + f" | **{r.field_macro_accuracy:.3f}** |")
    A("")

    # aggregate per field
    A("### 2.3 รวมทุกหลักสูตร ต่อฟิลด์")
    A("")
    A("| ฟิลด์ | ตรง | เทียบทั้งหมด | Accuracy |")
    A("|---|---:|---:|---:|")
    for fname, label in FIELD_SPECS:
        m = sum(r.fields[fname].matched for r in prog_results if fname in r.fields)
        c = sum(r.fields[fname].compared for r in prog_results if fname in r.fields)
        acc = f"{m/c:.3f}" if c else "—"
        A(f"| {label} | {m} | {c} | {acc} |")
    A("")

    # ── 3. CATEGORY LEVEL ──
    A("## 3. Category level — recall ต่อหมวดวิชา")
    A("")
    for r in prog_results:
        A(f"### {r.program} ({r.version_label})")
        A("")
        A("| หมวดวิชา | พบ / GT | Recall |")
        A("|---|---:|---:|")
        for cat, (found, total) in sorted(
            r.category_recall.items(), key=lambda kv: -kv[1][1]
        ):
            rec = found / total if total else 0.0
            A(f"| {cat} | {found} / {total} | {rec:.3f} |")
        A("")

    # ── 4. ตัวอย่างที่ไม่ตรง ──
    A("## 4. ตัวอย่างข้อมูลที่ยังไม่ตรง (สำหรับปรับปรุง)")
    A("")
    any_mismatch = False
    for r in prog_results:
        rows: list[str] = []
        for fname, label in FIELD_SPECS:
            st = r.fields.get(fname)
            if not st or not st.mismatches:
                continue
            for code, gval, eval_ in st.mismatches[:4]:
                g_s = gval if len(gval) <= 60 else gval[:57] + "..."
                e_s = (eval_ or "(ว่าง)") if len(eval_ or "") <= 60 else eval_[:57] + "..."
                rows.append(f"| {code} | {label} | {g_s} | {e_s} |")
        if rows:
            any_mismatch = True
            A(f"### {r.program}")
            A("")
            A("| รหัสวิชา | ฟิลด์ | GT | ระบบสกัดได้ |")
            A("|---|---|---|---|")
            L.extend(rows)
            A("")
    if not any_mismatch:
        A("ไม่พบความไม่ตรงในฟิลด์ที่เทียบได้")
        A("")

    # ── 5. วิชาที่ GT มีแต่ระบบไม่มี ──
    A("## 5. วิชาที่ GT มีแต่ระบบสกัดไม่ได้ (missing)")
    A("")
    for r in prog_results:
        if not r.gt_only_codes:
            A(f"- **{r.program}**: ไม่มีวิชาตกหล่น")
            continue
        preview = ", ".join(r.gt_only_codes[:15])
        more = f" (และอีก {len(r.gt_only_codes)-15} รหัส)" if len(r.gt_only_codes) > 15 else ""
        A(f"- **{r.program}**: ตกหล่น {len(r.gt_only_codes)} รหัส — {preview}{more}")
    A("")

    return "\n".join(L)


# ══════════════════════════════════════════════════════════════════════
# Entrypoint
# ══════════════════════════════════════════════════════════════════════


def run(db_path: Path, gt_root: Path) -> tuple[list[PageLevelResult], list[ProgramResult]]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        page_results = evaluate_page_level(conn)
        prog_results: list[ProgramResult] = []
        for gt_rel, program, year in GT_TARGETS:
            r = evaluate_program(conn, gt_root, gt_rel, program, year)
            if r:
                prog_results.append(r)
        return page_results, prog_results
    finally:
        conn.close()


def main() -> None:
    root = Path(__file__).resolve().parent.parent.parent
    ap = argparse.ArgumentParser(description="OCR evaluation vs teacher ground truth")
    ap.add_argument("--db", default=str(root / "artifacts" / "katrag.sqlite3"))
    ap.add_argument("--gt", default=str(root / "data" / "teacher_gt"))
    ap.add_argument("--report", default=str(root / "artifacts" / "ocr_eval_report.md"))
    ap.add_argument("--json", default=str(root / "artifacts" / "ocr_eval_result.json"))
    args = ap.parse_args()

    page_results, prog_results = run(Path(args.db), Path(args.gt))
    report = build_report(page_results, prog_results)

    out = Path(args.report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")

    payload = {
        "page_level": [
            {
                "document": p.document,
                "total_pages": p.total_pages,
                "text_layer_pages": p.text_layer_pages,
                "ocr_pages": p.ocr_pages,
                "method": p.method_label,
                "mean_page_quality": round(p.mean_quality, 4),
                "mean_out_of_charset": round(p.mean_out_of_charset, 5),
                "ocr_mean_confidence": (
                    round(p.ocr_mean_confidence, 4) if p.ocr_mean_confidence is not None else None
                ),
            }
            for p in page_results
        ],
        "field_level": [
            {
                "program": r.program,
                "version": r.version_label,
                "gt_total": r.gt_total,
                "extracted_total": r.extracted_total,
                "matched_codes": r.matched_codes,
                "code_recall": round(r.code_recall, 4),
                "code_precision": round(r.code_precision, 4),
                "code_f1": round(r.code_f1, 4),
                "field_macro_accuracy": round(r.field_macro_accuracy, 4),
                "fields": {
                    k: {"matched": v.matched, "compared": v.compared,
                        "accuracy": round(v.accuracy, 4)}
                    for k, v in r.fields.items()
                },
                "category_recall": {
                    k: {"found": v[0], "total": v[1],
                        "recall": round(v[0] / v[1], 4) if v[1] else 0.0}
                    for k, v in r.category_recall.items()
                },
                "missing_codes": r.gt_only_codes,
            }
            for r in prog_results
        ],
    }
    Path(args.json).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"report -> {args.report}")
    print(f"json   -> {args.json}")
    for r in prog_results:
        print(
            f"  {r.program:6s} code_F1={r.code_f1:.3f} "
            f"recall={r.code_recall:.3f} field_macro={r.field_macro_accuracy:.3f}"
        )


if __name__ == "__main__":
    main()
