"""OCR Batch Pipeline — รัน Tesseract OCR กับทุกหน้า OCR candidate.

Strategy:
- Fast path: Tesseract5 (tha+eng, --psm 6) — เร็ว ไม่ต้อง GPU
- เก็บผล OCR ลง ocr_stage_result + update page.page_text ถ้า OCR ดีกว่าเดิม
- บันทึก quality stats สำหรับ evaluation

Usage: python -m katrag.ingest.run_ocr [--limit N]
"""

from __future__ import annotations

import os
import subprocess
import sqlite3
import time
import tempfile
from pathlib import Path

import fitz  # PyMuPDF


def run_ocr_batch(
    db_path: Path,
    pdf_base: Path,
    *,
    limit: int | None = None,
    dpi: int = 300,
) -> dict[str, int]:
    """รัน OCR กับทุกหน้าที่เป็น candidate.

    Returns:
        dict of counts: pages_processed, ocr_chars_total, pages_improved
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    # ดึงหน้า OCR candidate
    limit_sql = f"LIMIT {limit}" if limit else ""
    rows = conn.execute(f"""
        SELECT p.page_id, p.document_id, p.page_number, p.char_count,
               d.relative_path
        FROM page p
        JOIN page_metrics m ON m.page_id = p.page_id
        JOIN document d ON d.document_id = p.document_id
        WHERE m.is_ocr_candidate = 1
        ORDER BY d.relative_path, p.page_number
        {limit_sql}
    """).fetchall()

    print(f"OCR candidates: {len(rows)} pages")

    pages_processed = 0
    ocr_chars_total = 0
    pages_improved = 0
    current_doc_path: str | None = None
    current_pdf = None

    for row in rows:
        rel_path = row["relative_path"]
        page_num = row["page_number"]
        page_id = row["page_id"]
        existing_chars = row["char_count"]

        # เปิด PDF (reuse ถ้าเอกสารเดียวกัน)
        if rel_path != current_doc_path:
            if current_pdf:
                current_pdf.close()
            full_path = pdf_base / rel_path
            current_pdf = fitz.open(str(full_path))
            current_doc_path = rel_path

        page = current_pdf[page_num - 1]

        # Render page เป็นภาพ
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name
        pix = page.get_pixmap(dpi=dpi)
        pix.save(tmp_path)

        # Tesseract OCR
        t0_page = time.time()
        try:
            result = subprocess.run(
                ["tesseract", tmp_path, "stdout", "-l", "tha+eng", "--psm", "6"],
                capture_output=True, text=True, encoding="utf-8", timeout=30,
            )
            ocr_text = result.stdout.strip()
        except (subprocess.TimeoutExpired, Exception) as e:
            ocr_text = ""
            print(f"  WARN: Tesseract failed on {rel_path} p{page_num}: {e}")
        elapsed_ocr = time.time() - t0_page

        os.unlink(tmp_path)

        ocr_chars = len(ocr_text)
        ocr_chars_total += ocr_chars

        # เก็บผล OCR ลง ocr_stage_result (ถ้ามี region)
        # ตอนนี้เก็บเป็น page-level result ชั่วคราว (region=whole page)
        # สร้าง region ถ้ายังไม่มี
        region_row = conn.execute(
            "SELECT region_id FROM region WHERE page_id=? LIMIT 1", (page_id,)
        ).fetchone()

        if not region_row:
            from katrag.common.hashing import sha256_text
            crop_sha = sha256_text(f"{row['document_id']}_{page_num}_full")
            conn.execute(
                """INSERT INTO region (page_id, x0, y0, x1, y1, crop_sha256, status, adjudication_json)
                   VALUES (?, 0.0, 0.0, ?, ?, ?, 'ok', '{}')""",
                (page_id, float(page.rect.width), float(page.rect.height), crop_sha),
            )
            region_row = conn.execute(
                "SELECT region_id FROM region WHERE page_id=? LIMIT 1", (page_id,)
            ).fetchone()

        region_id = region_row["region_id"]

        # Insert OCR result
        conn.execute("""
            INSERT OR REPLACE INTO ocr_stage_result
            (region_id, stage_index, engine, text, quality_score,
             confidence, elapsed_ms, preprocess_steps_json, cache_hit, is_selected)
            VALUES (?, 1, 'tesseract5', ?, ?, ?, ?, '[]', 0, 1)
        """, (region_id, ocr_text, min(1.0, ocr_chars / 500.0),
              min(1.0, ocr_chars / 500.0), int(elapsed_ocr * 1000)))

        # Update page_text ถ้า OCR ได้ข้อความมากกว่าเดิม
        if ocr_chars > existing_chars * 1.5 and ocr_chars > 50:
            conn.execute(
                "UPDATE page SET page_text=?, extraction_method='ocr_tesseract5' WHERE page_id=?",
                (ocr_text, page_id),
            )
            pages_improved += 1

        pages_processed += 1
        if pages_processed % 50 == 0:
            conn.commit()
            print(f"  Progress: {pages_processed}/{len(rows)} pages, {pages_improved} improved")

    if current_pdf:
        current_pdf.close()

    conn.commit()
    conn.close()

    return {
        "pages_processed": pages_processed,
        "ocr_chars_total": ocr_chars_total,
        "pages_improved": pages_improved,
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Limit number of pages to OCR")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent.parent
    db = project_root / "artifacts" / "katrag.sqlite3"
    pdf_base = project_root / "Information_Technology_Course"

    print(f"OCR Pipeline: DB={db}")
    print(f"PDF base: {pdf_base}")
    t0 = time.time()
    result = run_ocr_batch(db, pdf_base, limit=args.limit)
    elapsed = time.time() - t0

    print(f"\nDone in {elapsed:.0f}s!")
    print(f"  Pages processed: {result['pages_processed']}")
    print(f"  Total OCR chars: {result['ocr_chars_total']}")
    print(f"  Pages improved (text replaced): {result['pages_improved']}")
    print(f"  Avg chars/page: {result['ocr_chars_total'] / max(result['pages_processed'], 1):.0f}")
