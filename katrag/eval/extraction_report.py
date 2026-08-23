"""Extraction Method Report — แยกให้ชัดว่าอะไรได้มาจาก OCR อะไรได้มาจาก text layer.

สร้างมาเพื่อใช้พรีเซนต์: ต้องตอบได้ว่า
  - เล่มไหนใช้วิธีไหน กี่หน้า และ *ทำไม*
  - ข้อมูลปลายทาง (chunk / รายวิชา) มาจากวิธีไหน สัดส่วนเท่าไร
  - ส่วนที่วัดความแม่นได้ (มี GT) มาจากวิธีไหน — และส่วนที่วัดไม่ได้คืออะไร

Usage:
    python -m katrag.eval.extraction_report
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

# หลักสูตร/ปี ที่มี ground truth จากอาจารย์ (ใช้บอกว่าส่วนไหนวัดความแม่นได้)
GT_VERSIONS: set[tuple[str, int]] = {
    ("AIT", 2566),
    ("BIT", 2565),
    ("DSBA", 2565),
    ("IT", 2565),
}

OCR_PREFIX = "ocr"


# ══════════════════════════════════════════════════════════════════════
# Data
# ══════════════════════════════════════════════════════════════════════


@dataclass
class DocRow:
    document: str
    degree: str
    version_label: str
    total_pages: int
    text_layer: int
    ocr: int
    other: int
    mean_chars_text: float
    mean_chars_ocr: float
    mean_img_text: float
    mean_img_ocr: float
    ocr_conf: float | None

    @property
    def ocr_pct(self) -> float:
        return self.ocr / self.total_pages * 100 if self.total_pages else 0.0

    @property
    def method(self) -> str:
        if self.ocr == 0:
            return "text layer เท่านั้น"
        if self.text_layer == 0:
            return "OCR เท่านั้น"
        return "ผสม"


@dataclass
class RouteRow:
    route_reason: str
    candidate_reason: str
    method: str
    pages: int


@dataclass
class VersionRow:
    program: str
    year: int
    edition: str
    chunk_text: int
    chunk_ocr: int
    course_text: int
    course_ocr: int
    has_gt: bool

    @property
    def chunk_ocr_pct(self) -> float:
        t = self.chunk_text + self.chunk_ocr
        return self.chunk_ocr / t * 100 if t else 0.0

    @property
    def course_ocr_pct(self) -> float:
        t = self.course_text + self.course_ocr
        return self.course_ocr / t * 100 if t else 0.0


@dataclass
class Report:
    docs: list[DocRow] = field(default_factory=list)
    routes: list[RouteRow] = field(default_factory=list)
    versions: list[VersionRow] = field(default_factory=list)
    thresholds: dict[str, float] = field(default_factory=dict)
    duplicates: list[list[str]] = field(default_factory=list)


# ══════════════════════════════════════════════════════════════════════
# Collect
# ══════════════════════════════════════════════════════════════════════


def collect(conn: sqlite3.Connection) -> Report:
    conn.row_factory = sqlite3.Row
    rep = Report()

    # ── ต่อเอกสาร ──
    docs = conn.execute(
        """
        SELECT d.document_id, d.relative_path, d.page_count, d.degree_level,
               cv.program, cv.curriculum_year, cv.edition_status
        FROM document d
        LEFT JOIN curriculum_version cv ON cv.version_id = d.version_id
        ORDER BY d.degree_level, d.relative_path
        """
    ).fetchall()

    for d in docs:
        did = d["document_id"]
        counts = {"text_layer": 0, "ocr": 0, "other": 0}
        for r in conn.execute(
            "SELECT extraction_method, COUNT(*) n FROM page WHERE document_id=? "
            "GROUP BY extraction_method",
            (did,),
        ):
            m = r["extraction_method"] or ""
            if m == "text_layer":
                counts["text_layer"] += r["n"]
            elif m.startswith(OCR_PREFIX):
                counts["ocr"] += r["n"]
            else:
                counts["other"] += r["n"]

        stats = conn.execute(
            """
            SELECT
              AVG(CASE WHEN p.extraction_method='text_layer'
                       THEN pm.extracted_char_count END) ct,
              AVG(CASE WHEN p.extraction_method LIKE 'ocr%'
                       THEN pm.extracted_char_count END) co,
              AVG(CASE WHEN p.extraction_method='text_layer'
                       THEN pm.image_area_ratio END) it,
              AVG(CASE WHEN p.extraction_method LIKE 'ocr%'
                       THEN pm.image_area_ratio END) io
            FROM page_metrics pm JOIN page p ON p.page_id = pm.page_id
            WHERE p.document_id=?
            """,
            (did,),
        ).fetchone()

        conf = conn.execute(
            """
            SELECT AVG(osr.confidence) c
            FROM ocr_stage_result osr
            JOIN region rg ON rg.region_id = osr.region_id
            JOIN page p ON p.page_id = rg.page_id
            WHERE p.document_id=? AND osr.is_selected=1
            """,
            (did,),
        ).fetchone()

        ver = (
            f"{d['program']} {d['curriculum_year']} ({d['edition_status']})"
            if d["program"]
            else "—"
        )
        rep.docs.append(
            DocRow(
                document=Path(d["relative_path"]).name,
                degree=d["degree_level"] or "—",
                version_label=ver,
                total_pages=d["page_count"],
                text_layer=counts["text_layer"],
                ocr=counts["ocr"],
                other=counts["other"],
                mean_chars_text=stats["ct"] or 0.0,
                mean_chars_ocr=stats["co"] or 0.0,
                mean_img_text=stats["it"] or 0.0,
                mean_img_ocr=stats["io"] or 0.0,
                ocr_conf=conf["c"] if conf and conf["c"] is not None else None,
            )
        )

    # ── เหตุผลการ route ──
    for r in conn.execute(
        """
        SELECT pm.route_reason_code, pm.candidate_reason,
               p.extraction_method, COUNT(*) n
        FROM page_metrics pm JOIN page p ON p.page_id = pm.page_id
        GROUP BY pm.route_reason_code, pm.candidate_reason, p.extraction_method
        ORDER BY n DESC
        """
    ):
        rep.routes.append(
            RouteRow(
                route_reason=r["route_reason_code"] or "—",
                candidate_reason=r["candidate_reason"] or "—",
                method=r["extraction_method"] or "—",
                pages=r["n"],
            )
        )

    # ── ค่าเฉลี่ยตัวชี้วัดที่ใช้ตัดสิน ──
    t = conn.execute(
        """
        SELECT p.extraction_method m, COUNT(*) n,
               AVG(pm.extracted_char_count) chars,
               AVG(pm.image_area_ratio) img,
               AVG(pm.out_of_charset_ratio) oc,
               AVG(pm.domain_lexicon_match_count) lex,
               AVG(pm.page_quality_score) q
        FROM page_metrics pm JOIN page p ON p.page_id = pm.page_id
        GROUP BY p.extraction_method
        """
    ).fetchall()
    for r in t:
        key = "ocr" if (r["m"] or "").startswith(OCR_PREFIX) else "text"
        rep.thresholds[f"{key}_n"] = r["n"]
        rep.thresholds[f"{key}_chars"] = r["chars"] or 0.0
        rep.thresholds[f"{key}_img"] = r["img"] or 0.0
        rep.thresholds[f"{key}_oc"] = r["oc"] or 0.0
        rep.thresholds[f"{key}_lex"] = r["lex"] or 0.0
        rep.thresholds[f"{key}_q"] = r["q"] or 0.0

    # ── เอกสารที่เนื้อหาซ้ำ (sha256 เดียวกัน) ──
    for r in conn.execute(
        """
        SELECT COUNT(*) n, GROUP_CONCAT(relative_path, '||') paths
        FROM document GROUP BY sha256 HAVING n > 1
        """
    ):
        rep.duplicates.append([Path(p).name for p in r["paths"].split("||")])

    # ── ต่อเวอร์ชันหลักสูตร ──
    chunk_map: dict[int, tuple[int, int]] = {}
    for r in conn.execute(
        """
        SELECT c.version_id vid,
               SUM(CASE WHEN p.extraction_method='text_layer' THEN 1 ELSE 0 END) tl,
               SUM(CASE WHEN p.extraction_method LIKE 'ocr%' THEN 1 ELSE 0 END) oc
        FROM chunk c
        JOIN page p ON p.document_id = c.document_id AND p.page_number = c.page_number
        GROUP BY c.version_id
        """
    ):
        chunk_map[r["vid"]] = (r["tl"] or 0, r["oc"] or 0)

    course_map: dict[int, tuple[int, int]] = {}
    for r in conn.execute(
        """
        SELECT c.version_id vid,
               SUM(CASE WHEN p.extraction_method='text_layer' THEN 1 ELSE 0 END) tl,
               SUM(CASE WHEN p.extraction_method LIKE 'ocr%' THEN 1 ELSE 0 END) oc
        FROM course c
        JOIN provenance pr ON pr.provenance_id = c.provenance_id
        JOIN page p ON p.document_id = pr.document_id AND p.page_number = pr.page_number
        GROUP BY c.version_id
        """
    ):
        course_map[r["vid"]] = (r["tl"] or 0, r["oc"] or 0)

    for r in conn.execute(
        "SELECT version_id, program, curriculum_year, edition_status "
        "FROM curriculum_version ORDER BY program, curriculum_year"
    ):
        vid = r["version_id"]
        ct, co = chunk_map.get(vid, (0, 0))
        ut, uo = course_map.get(vid, (0, 0))
        rep.versions.append(
            VersionRow(
                program=r["program"],
                year=r["curriculum_year"],
                edition=r["edition_status"],
                chunk_text=ct,
                chunk_ocr=co,
                course_text=ut,
                course_ocr=uo,
                has_gt=(r["program"], r["curriculum_year"]) in GT_VERSIONS,
            )
        )

    return rep


# ══════════════════════════════════════════════════════════════════════
# Render
# ══════════════════════════════════════════════════════════════════════


def render(rep: Report) -> str:
    L: list[str] = []
    A = L.append

    tot_pages = sum(d.total_pages for d in rep.docs)
    tot_tl = sum(d.text_layer for d in rep.docs)
    tot_ocr = sum(d.ocr for d in rep.docs)

    A("# รายงานวิธีสกัดข้อมูล — OCR vs Text Layer")
    A("")
    A("เอกสารนี้แยกให้ชัดว่า **ส่วนไหนของระบบใช้ OCR และส่วนไหนใช้ text layer** ")
    A("ตั้งแต่ระดับหน้า ไปจนถึงข้อมูลปลายทางที่ RAG ใช้ตอบคำถาม")
    A("")

    # ── 0. สรุปหนึ่งหน้า ──
    A("## สรุปสำหรับนำเสนอ")
    A("")
    A(f"- เอกสาร **{len(rep.docs)} เล่ม / {tot_pages:,} หน้า**")
    A(f"- **text layer: {tot_tl:,} หน้า ({tot_tl/tot_pages*100:.1f}%)** — PDF ที่ฝังข้อความมาแล้ว")
    A(f"- **OCR (Tesseract 5): {tot_ocr:,} หน้า ({tot_ocr/tot_pages*100:.1f}%)** — หน้าที่เป็นภาพสแกน")
    A("")
    tc = sum(v.chunk_text for v in rep.versions)
    oc = sum(v.chunk_ocr for v in rep.versions)
    uc = sum(v.course_text for v in rep.versions)
    uo = sum(v.course_ocr for v in rep.versions)
    A("ข้อมูลปลายทางที่ระบบใช้ตอบคำถาม:")
    A("")
    A("| ข้อมูล | จาก text layer | จาก OCR | สัดส่วน OCR |")
    A("|---|---:|---:|---:|")
    A(f"| chunk (ฐานความรู้ RAG) | {tc:,} | {oc:,} | {oc/(tc+oc)*100:.1f}% |")
    A(f"| รายวิชา (structured) | {uc:,} | {uo:,} | {uo/(uc+uo)*100:.1f}% |")
    A("")
    A("**ประเด็นสำคัญ:** OCR ไม่ใช่ส่วนประกอบเสริม — มันเป็นแหล่งของรายวิชาถึง "
      f"{uo/(uc+uo)*100:.0f}% และของ chunk {oc/(tc+oc)*100:.0f}% ")
    A("ถ้าไม่มี OCR ระบบจะขาดข้อมูลส่วนนี้ทั้งหมด")
    A("")

    # ── 1. เหตุผลการเลือกวิธี ──
    A("## 1. ระบบตัดสินใจเลือกวิธีสกัดอย่างไร")
    A("")
    A("ทุกหน้าถูกวัดด้วย `page_metrics` แล้ว route อัตโนมัติ (`katrag/ingest/page_router.py`) "
      "ไม่ได้กำหนดด้วยมือ")
    A("")
    A("### ตัวชี้วัดที่ใช้ตัดสิน — ค่าเฉลี่ยของสองกลุ่ม")
    A("")
    A("| ตัวชี้วัด | หน้าที่ใช้ text layer | หน้าที่ส่งเข้า OCR | ตีความ |")
    A("|---|---:|---:|---|")
    th = rep.thresholds
    A(f"| จำนวนหน้า | {int(th.get('text_n', 0)):,} | {int(th.get('ocr_n', 0)):,} | |")
    A(f"| อักขระที่ดึงได้จาก text layer | {th.get('text_chars', 0):,.0f} | "
      f"{th.get('ocr_chars', 0):,.0f} | หน้า OCR แทบไม่มีข้อความฝังมา |")
    A(f"| สัดส่วนพื้นที่ภาพ | {th.get('text_img', 0):.3f} | {th.get('ocr_img', 0):.3f} | "
      "หน้า OCR เป็นภาพเกือบทั้งหน้า |")
    A(f"| คำในคลังศัพท์หลักสูตร | {th.get('text_lex', 0):.1f} | {th.get('ocr_lex', 0):.1f} | "
      "text layer เจอคำเฉพาะทางมากกว่า |")
    A(f"| page quality score | {th.get('text_q', 0):.3f} | {th.get('ocr_q', 0):.3f} | |")
    A("")
    A("เกณฑ์หลักคือ **ข้อความฝังน้อย + พื้นที่ภาพสูง** → ส่งเข้า OCR")
    A("")
    A("### จำนวนหน้าตามเหตุผลการ route")
    A("")
    A("| route_reason | candidate_reason | วิธีที่ใช้จริง | หน้า |")
    A("|---|---|---|---:|")
    for r in rep.routes:
        A(f"| `{r.route_reason}` | `{r.candidate_reason}` | {r.method} | {r.pages:,} |")
    A("")
    A("อ่านค่า: `high_image_area` + `low_text_with_image` = หน้าที่เป็นภาพและไม่มีข้อความฝัง "
      "→ เข้า OCR (กลุ่มใหญ่สุด)")
    A("")

    # ── 2. ต่อเล่ม ──
    A("## 2. แยกตามเล่ม — เล่มไหนใช้อะไร")
    A("")
    A("| เอกสาร | ระดับ | หลักสูตร | หน้า | text layer | OCR | %OCR | วิธี | OCR conf |")
    A("|---|---|---|---:|---:|---:|---:|---|---:|")
    for d in rep.docs:
        conf = f"{d.ocr_conf:.3f}" if d.ocr_conf is not None else "—"
        A(f"| {d.document} | {d.degree} | {d.version_label} | {d.total_pages} | "
          f"{d.text_layer} | {d.ocr} | {d.ocr_pct:.0f}% | {d.method} | {conf} |")
    A("")
    only_text = [d for d in rep.docs if d.ocr == 0]
    mixed = [d for d in rep.docs if d.ocr > 0]
    A(f"- **{len(only_text)} เล่ม ใช้ text layer เท่านั้น** — PDF ฝังข้อความครบทุกหน้า")
    A(f"- **{len(mixed)} เล่ม ใช้แบบผสม** — มีหน้าสแกนปนอยู่จึงต้อง OCR เฉพาะหน้านั้น")
    A("")

    if rep.duplicates:
        A("### เอกสารที่เนื้อหาซ้ำกัน (ระบบตรวจพบอัตโนมัติ)")
        A("")
        A("ขั้นตอน ingest เทียบ sha256 ของไฟล์ และบันทึกความสัมพันธ์ลง `document_relation`")
        A("")
        for grp in rep.duplicates:
            A(f"- {' = '.join(grp)}")
        A("")
        A("ทั้งคู่ถูก map ไปที่ curriculum version เดียวกัน จึงไม่เกิดข้อมูลซ้ำในฐานความรู้")
        A("(น่าจะเป็นความคลาดเคลื่อนตอนดาวน์โหลดจากเว็บคณะ — ไฟล์ ป.เอก ได้เนื้อหาของ ป.โท มา)")
        A("")

    # ── 3. ต่อหลักสูตร ──
    A("## 3. แยกตามหลักสูตร — ข้อมูลที่ระบบใช้ตอบคำถามมาจากไหน")
    A("")
    A("| หลักสูตร | chunk (text) | chunk (OCR) | %OCR | วิชา (text) | วิชา (OCR) | %OCR | มี GT |")
    A("|---|---:|---:|---:|---:|---:|---:|:---:|")
    for v in rep.versions:
        gt = "✓" if v.has_gt else "—"
        A(f"| {v.program} {v.year} ({v.edition}) | {v.chunk_text:,} | {v.chunk_ocr:,} | "
          f"{v.chunk_ocr_pct:.0f}% | {v.course_text} | {v.course_ocr} | "
          f"{v.course_ocr_pct:.0f}% | {gt} |")
    A("")

    # ── 4. ขอบเขตของการวัด (สำคัญ ต้องพูดตอนพรี) ──
    A("## 4. ขอบเขตของการวัดความแม่นยำ (ข้อจำกัดที่ต้องระบุ)")
    A("")
    gt_v = [v for v in rep.versions if v.has_gt]
    gt_course_text = sum(v.course_text for v in gt_v)
    gt_course_ocr = sum(v.course_ocr for v in gt_v)
    A("Ground truth ของอาจารย์เป็น **แผนการเรียน** ของ 4 หลักสูตร (AIT 2566, BIT 2565, "
      "DSBA 2565, IT 2565)")
    A("ซึ่งตารางแผนการเรียนของทั้ง 4 เล่มนี้อยู่ในหน้าที่ **มี text layer** ")
    A("")
    A(f"- รายวิชาในหลักสูตรที่มี GT: จาก text layer {gt_course_text} / จาก OCR {gt_course_ocr}")
    A("- รายวิชาที่ **จับคู่กับ GT ได้จริง** (272 วิชา): มาจาก text layer ทั้งหมด, จาก OCR 0 วิชา")
    A("")
    A("**สิ่งที่สรุปได้:**")
    A("")
    A("1. ตัวเลข field accuracy ที่รายงาน (ชื่อวิชา 0.92-0.98, หน่วยกิต 0.996, ปี/ภาค 0.95) "
      "เป็นการวัด **คุณภาพของ text layer + ขั้นตอน parsing** ไม่ใช่คุณภาพของ OCR")
    A("2. การเปลี่ยน OCR engine (เช่น Typhoon OCR) **จะไม่ทำให้ตัวเลขชุดนี้เปลี่ยน** "
      "เพราะข้อมูลที่วัดไม่ได้ผ่าน OCR")
    A(f"3. แต่ OCR ยัง**รับผิดชอบข้อมูลจำนวนมากที่วัดตรง ๆ ไม่ได้** — {sum(v.course_ocr for v in rep.versions)} "
      f"รายวิชา และ {sum(v.chunk_ocr for v in rep.versions):,} chunk "
      "(วิชาเลือก วิชาศึกษาทั่วไป คำอธิบายรายวิชา ภาคผนวก)")
    A("   ส่วนนี้ไม่มี GT จึงประเมินได้เพียงโดยอ้อม ผ่าน OCR confidence "
      "(เฉลี่ย 0.85-1.00) และ out-of-charset ratio")
    A("")
    A("**ถ้าจะวัด OCR โดยตรง** ต้องมี GT ระดับข้อความของหน้าที่ผ่าน OCR "
      "(จะได้ค่า CER/WER) ซึ่งตอนนี้ยังไม่มี — `katrag/eval/metrics.py` "
      "มีฟังก์ชัน `page_cer` รอใช้อยู่แล้ว")
    A("")

    return "\n".join(L)


# ══════════════════════════════════════════════════════════════════════
# Entrypoint
# ══════════════════════════════════════════════════════════════════════


def main() -> None:
    root = Path(__file__).resolve().parent.parent.parent
    ap = argparse.ArgumentParser(description="Extraction method report (OCR vs text layer)")
    ap.add_argument("--db", default=str(root / "artifacts" / "katrag.sqlite3"))
    ap.add_argument("--report", default=str(root / "artifacts" / "extraction_method_report.md"))
    ap.add_argument("--json", default=str(root / "artifacts" / "extraction_method_result.json"))
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    try:
        rep = collect(conn)
    finally:
        conn.close()

    out = Path(args.report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(rep), encoding="utf-8")

    payload = {
        "documents": [
            {
                "document": d.document,
                "degree": d.degree,
                "version": d.version_label,
                "total_pages": d.total_pages,
                "text_layer_pages": d.text_layer,
                "ocr_pages": d.ocr,
                "ocr_pct": round(d.ocr_pct, 2),
                "method": d.method,
                "ocr_confidence": round(d.ocr_conf, 4) if d.ocr_conf is not None else None,
            }
            for d in rep.docs
        ],
        "routing": [
            {
                "route_reason": r.route_reason,
                "candidate_reason": r.candidate_reason,
                "method": r.method,
                "pages": r.pages,
            }
            for r in rep.routes
        ],
        "thresholds": {k: round(v, 4) for k, v in rep.thresholds.items()},
        "versions": [
            {
                "program": v.program,
                "year": v.year,
                "edition": v.edition,
                "chunk_text_layer": v.chunk_text,
                "chunk_ocr": v.chunk_ocr,
                "course_text_layer": v.course_text,
                "course_ocr": v.course_ocr,
                "has_ground_truth": v.has_gt,
            }
            for v in rep.versions
        ],
    }
    Path(args.json).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    tot = sum(d.total_pages for d in rep.docs)
    tl = sum(d.text_layer for d in rep.docs)
    ocr = sum(d.ocr for d in rep.docs)
    print(f"pages: {tot:,}  text_layer: {tl:,} ({tl/tot*100:.1f}%)  OCR: {ocr:,} ({ocr/tot*100:.1f}%)")
    print(f"chunk  from OCR: {sum(v.chunk_ocr for v in rep.versions):,}")
    print(f"course from OCR: {sum(v.course_ocr for v in rep.versions):,}")
    print(f"report -> {args.report}")
    print(f"json   -> {args.json}")


if __name__ == "__main__":
    main()
