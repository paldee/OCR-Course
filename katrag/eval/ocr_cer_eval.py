"""วัดคุณภาพ Tesseract โดยตรง (CER / WER / digit accuracy) — paired-page protocol.

ปัญหาที่โมดูลนี้แก้
-------------------
`ocr_eval.py` วัดฟิลด์รายวิชาเทียบ ground truth ของอาจารย์ได้ แต่รายวิชาที่จับคู่ GT
ได้ทั้ง 272 รายการมาจากหน้า **text layer** ทั้งหมด (GT คือตารางแผนการเรียน ซึ่งใน
ทั้ง 4 เล่มเป็นหน้าที่ PDF ฝังข้อความมาแล้ว) ตัวเลขชุดนั้นจึงวัด "คุณภาพ text layer
+ ขั้นตอน parsing" ไม่ได้วัด OCR เลย — ทำให้ตอบไม่ได้ว่า Tesseract อ่านผิดแค่ไหน
และคุ้มไหมที่จะเปลี่ยนไปใช้ engine อื่น (เช่น Typhoon OCR)

โปรโตคอลการวัด (paired-page)
----------------------------
1. เลือกหน้าที่ระบบ route ไปใช้ text layer และมีข้อความยาวพอ → ใช้ text layer
   เป็น **reference** (สำหรับ PDF ที่ฝังข้อความมา text layer คือสิ่งที่พิมพ์ไว้จริง
   จึงใกล้เคียง ground truth ระดับข้อความมากที่สุดที่หาได้โดยไม่ต้องให้คนพิมพ์ใหม่)
2. render หน้าเดียวกันเป็นภาพที่ DPI เดียวกับ pipeline จริง (300)
3. ส่งเข้า Tesseract ด้วย argument ชุดเดียวกับ `run_ocr.py` (`-l tha+eng --psm 6`)
4. เทียบผล OCR กับ reference → CER / WER / ความแม่นของรหัสวิชา 8 หลัก

ผลที่ได้คือ "ถ้าหน้านี้ไม่มี text layer แล้วต้องพึ่ง Tesseract จะเสียความถูกต้องไปเท่าไร"
ซึ่งเป็นตัวแทนที่ยุติธรรมของหน้า OCR จริง เพราะเป็นเล่มเดียวกัน ฟอนต์และเลย์เอาต์เดียวกัน

ข้อจำกัดที่ต้องระบุเวลารายงาน
----------------------------
- reference คือ text layer ไม่ใช่คนพิมพ์ ถ้า text layer เองเพี้ยน (embedded font
  ที่ map อักขระผิด) CER จะสูงเกินจริง จึงมีคอลัมน์ `ref out-of-charset` กำกับไว้
- `--psm 6` สมมติว่าหน้าเป็นบล็อกข้อความเดียว หน้าที่เป็นตารางหลายคอลัมน์ (มคอ.2 มีเยอะ:
  ตารางเปรียบเทียบหลักสูตร, ตาราง PLO mapping, ประวัติอาจารย์) Tesseract ไล่อ่านทีละคอลัมน์
  ขณะที่ text layer ไล่ทีละแถว → edit distance พุ่งสูงทั้งที่อักขระอ่านถูก
  จึงต้องมีตัววัดที่แยกสองสาเหตุนี้ออกจากกัน:
    * `CER` — ตัวเลขตรง ๆ รวมทั้งความผิดของการรู้จำและของลำดับ (upper bound)
    * `bag CER` — เทียบ multiset ของอักขระ ไม่สนลำดับเลย = ความผิดของ **การรู้จำ** เพียว ๆ
      (lower bound; ชดเชยกันเองได้บ้างเพราะอ่านผิดเป็นอักขระที่มีอยู่ที่อื่นก็จะหักกลบ)
  ถ้า `CER` สูงแต่ `bag CER` ต่ำ แปลว่าปัญหาคือลำดับการอ่าน ไม่ใช่ Tesseract อ่านตัวอักษรผิด
- ภาษาไทยไม่มีช่องว่างระหว่างคำ WER จึงมีความหมายจำกัด อ่าน CER เป็นตัวหลัก

Usage: python -m katrag.eval.ocr_cer_eval [--per-doc N] [--min-chars N]
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sqlite3
import subprocess
import tempfile
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF

from katrag.eval.metrics import _levenshtein_distance

# argument ชุดเดียวกับ katrag/ingest/run_ocr.py — ถ้าที่นั่นเปลี่ยน ต้องเปลี่ยนที่นี่ด้วย
# ไม่งั้นตัวเลข CER จะไม่ใช่ค่าของ pipeline จริง
TESSERACT_ARGS = ["-l", "tha+eng", "--psm", "6"]
RENDER_DPI = 300

_CODE_RE = re.compile(r"\b(\d{8})\b")
_WS_RE = re.compile(r"\s+")

# ชุดอักขระที่คาดหวังในเอกสารหลักสูตร (ไทย + ละติน + เลข + เครื่องหมายพื้นฐาน)
_EXPECTED_CHARS = re.compile(r"[\u0E00-\u0E7Fa-zA-Z0-9\s\.\,\-\(\)\[\]\/\:\;\'\"%&+*=<>_#\u2013\u2014\u201C\u201D\u2018\u2019]")


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text or "")


def _collapse_ws(text: str) -> str:
    """ยุบช่องว่างซ้อนเป็นช่องว่างเดียว — ความต่างของการขึ้นบรรทัดไม่ใช่ OCR error."""
    return _WS_RE.sub(" ", _nfc(text)).strip()


def _strip_ws(text: str) -> str:
    """ตัดช่องว่างทั้งหมด — ใช้กับภาษาไทยที่ช่องว่างไม่ใช่ขอบคำ."""
    return _WS_RE.sub("", _nfc(text))


def _bag_error(hyp: str, ref: str) -> tuple[int, int]:
    """(อักขระที่หายไป/ผิด, อักขระอ้างอิงทั้งหมด) โดยเทียบเป็น multiset ไม่สนลำดับ.

    ใช้แยก "Tesseract อ่านตัวอักษรผิด" ออกจาก "Tesseract เรียงลำดับต่างจาก text layer"
    ตัดช่องว่างทั้งหมดก่อนนับ เพราะช่องว่างไม่ใช่ขอบคำในภาษาไทย
    """
    r = Counter(_strip_ws(ref))
    h = Counter(_strip_ws(hyp))
    total = sum(r.values())
    matched = sum(min(n, h[ch]) for ch, n in r.items())
    return total - matched, total


def _line_match_error(hyp: str, ref: str) -> tuple[int, int]:
    """(edit distance รวมแบบค้นทั้งหน้า, อักขระอ้างอิงรวม) — ทน reading order.

    ทำไมต้องมีตัวนี้: `CER` ลงโทษการสลับลำดับบล็อกเต็ม ๆ ส่วน `bag CER` ปล่อยหลุด
    มากเกินไป (อ่าน "ก" ผิดเป็น "ข" ถูกหักกลบถ้าที่อื่นมี "ข" ขาดอยู่) ตัวนี้อยู่กลาง

    วิธี: เอาแต่ละบรรทัดของ reference ไป **ค้นหาช่วงที่ตรงที่สุดใน OCR output ทั้งหน้า**
    (approximate substring matching) แล้ววัด edit distance กับช่วงนั้น
    → บรรทัดจะถูกหาเจอไม่ว่า Tesseract จะวางไว้ตำแหน่งไหนของหน้า จึงไม่ถูกลงโทษ
      จากการที่ OCR ไล่อ่านคอลัมน์สลับกับ text layer
    → แต่ถ้าอักขระในบรรทัดถูกอ่านผิด ระยะทางในบรรทัดนั้นยังนับเต็ม จึงไม่หักกลบแบบ bag

    หมายเหตุ: ไม่บังคับจับคู่ 1:1 (ช่วงเดียวใน hypothesis อาจตรงกับหลายบรรทัดของ
    reference) เพราะเคยลองบังคับ 1:1 แล้วพบว่า Tesseract แบ่งบรรทัดไม่ตรงกับ text layer
    (รวมหลายเซลล์เป็นบรรทัดเดียว / ตัดบรรทัดเดียวเป็นหลายบรรทัด) การบังคับ 1:1 จึงทำให้
    บรรทัดที่เหลือถูกนับผิดเต็มทั้งที่ OCR อ่านถูก — ค่าที่ได้เคยสูงกว่า CER ธรรมดาด้วยซ้ำ
    ผลข้างเคียงที่ต้องยอมรับ: metric นี้เป็น recall-oriented (ไม่ลงโทษข้อความที่ OCR
    เพิ่มเกินมา) ส่วนนั้นให้ดู `CER` และ "รหัสวิชาที่ OCR สร้างเกิน" ประกอบ
    """
    from rapidfuzz import fuzz
    from rapidfuzz.distance import Levenshtein as _RFLev

    hyp_flat = _strip_ws(hyp)
    ref_lines = [ln for ln in (_strip_ws(x) for x in _nfc(ref).split("\n")) if ln]

    total = sum(len(ln) for ln in ref_lines)
    if not total:
        return 0, 0
    if not hyp_flat:
        return total, total

    err = 0
    for rl in ref_lines:
        al = fuzz.partial_ratio_alignment(rl, hyp_flat)
        if al is None:
            err += len(rl)
            continue
        window = hyp_flat[al.dest_start:al.dest_end]
        err += min(_RFLev.distance(rl, window), len(rl))
    return min(err, total), total


def _is_table_like(text: str) -> bool:
    """เดาว่าหน้านี้เป็นตารางหรือข้อความบรรยาย จากสัดส่วนบรรทัดสั้น.

    หน้าตารางในมคอ.2 ประกอบด้วยเซลล์สั้น ๆ จำนวนมาก ส่วนหน้าคำอธิบาย/ระเบียบ
    เป็นย่อหน้าเต็มบรรทัด เกณฑ์ 40 อักขระมาจากการดูความกว้างบรรทัดจริงในเล่มเหล่านี้
    """
    lines = [ln.strip() for ln in _nfc(text).split("\n") if ln.strip()]
    if len(lines) < 5:
        return False
    short = sum(1 for ln in lines if len(ln) < 40)
    return short / len(lines) >= 0.6


def _out_of_charset_ratio(text: str) -> float:
    t = _nfc(text)
    if not t:
        return 0.0
    ok = len(_EXPECTED_CHARS.findall(t))
    return 1.0 - ok / len(t)


@dataclass
class PagePair:
    document_id: str
    relative_path: str
    page_number: int
    reference: str
    hypothesis: str
    elapsed_ms: int

    # ── metric ต่อหน้า ──
    @property
    def cer(self) -> float:
        ref = _collapse_ws(self.reference)
        if not ref:
            return 0.0
        return _levenshtein_distance(_collapse_ws(self.hypothesis), ref) / len(ref)

    @property
    def cer_nows(self) -> float:
        ref = _strip_ws(self.reference)
        if not ref:
            return 0.0
        return _levenshtein_distance(_strip_ws(self.hypothesis), ref) / len(ref)

    @property
    def wer(self) -> float:
        ref_w = _collapse_ws(self.reference).split(" ")
        hyp_w = _collapse_ws(self.hypothesis).split(" ")
        if not ref_w:
            return 0.0
        # ใช้ Levenshtein ระดับ token โดย map แต่ละ token เป็นอักขระเดียว
        vocab: dict[str, str] = {}

        def enc(words: list[str]) -> str:
            out = []
            for w in words:
                if w not in vocab:
                    vocab[w] = chr(0xE000 + len(vocab))  # private use area
                out.append(vocab[w])
            return "".join(out)

        return _levenshtein_distance(enc(hyp_w), enc(ref_w)) / len(ref_w)

    @property
    def cer_bag(self) -> float:
        err, total = _bag_error(self.hypothesis, self.reference)
        return err / total if total else 0.0

    @property
    def cer_line(self) -> float:
        err, total = _line_match_error(self.hypothesis, self.reference)
        return err / total if total else 0.0

    @property
    def is_table_like(self) -> bool:
        return _is_table_like(self.reference)

    @property
    def code_stats(self) -> tuple[int, int, int]:
        """(รหัสใน reference, รหัสที่ OCR อ่านได้ตรง, รหัสที่ OCR สร้างเกินมา)."""
        ref_codes = set(_CODE_RE.findall(_nfc(self.reference)))
        hyp_codes = set(_CODE_RE.findall(_nfc(self.hypothesis)))
        return len(ref_codes), len(ref_codes & hyp_codes), len(hyp_codes - ref_codes)


@dataclass
class DocResult:
    document_id: str
    relative_path: str
    pages: list[PagePair] = field(default_factory=list)

    def _micro(self, strip: bool) -> float:
        norm = _strip_ws if strip else _collapse_ws
        dist = sum(_levenshtein_distance(norm(p.hypothesis), norm(p.reference)) for p in self.pages)
        total = sum(len(norm(p.reference)) for p in self.pages)
        return dist / total if total else 0.0

    @property
    def cer_micro(self) -> float:
        return self._micro(False)

    @property
    def cer_micro_nows(self) -> float:
        return self._micro(True)

    @property
    def cer_bag_micro(self) -> float:
        pairs = [_bag_error(p.hypothesis, p.reference) for p in self.pages]
        den = sum(t for _, t in pairs)
        return sum(e for e, _ in pairs) / den if den else 0.0

    @property
    def cer_line_micro(self) -> float:
        pairs = [_line_match_error(p.hypothesis, p.reference) for p in self.pages]
        den = sum(t for _, t in pairs)
        return sum(e for e, _ in pairs) / den if den else 0.0

    @property
    def wer_micro(self) -> float:
        # ถ่วงน้ำหนักด้วยจำนวนคำใน reference
        num = sum(p.wer * len(_collapse_ws(p.reference).split(" ")) for p in self.pages)
        den = sum(len(_collapse_ws(p.reference).split(" ")) for p in self.pages)
        return num / den if den else 0.0

    @property
    def code_totals(self) -> tuple[int, int, int]:
        ref = hit = extra = 0
        for p in self.pages:
            r, h, e = p.code_stats
            ref += r
            hit += h
            extra += e
        return ref, hit, extra

    @property
    def ref_out_of_charset(self) -> float:
        if not self.pages:
            return 0.0
        return sum(_out_of_charset_ratio(p.reference) for p in self.pages) / len(self.pages)

    @property
    def mean_ms(self) -> float:
        if not self.pages:
            return 0.0
        return sum(p.elapsed_ms for p in self.pages) / len(self.pages)


def _sample_pages(
    conn: sqlite3.Connection, per_doc: int, min_chars: int, seed: int
) -> dict[str, list[sqlite3.Row]]:
    """สุ่มหน้า text_layer ต่อเอกสารแบบ deterministic (seed คงที่ = รันซ้ำได้ผลเดิม)."""
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT p.page_id, p.document_id, p.page_number, p.page_text,
               d.relative_path
        FROM page p
        JOIN document d ON d.document_id = p.document_id
        WHERE p.extraction_method = 'text_layer'
          AND length(p.page_text) >= ?
        ORDER BY d.relative_path, p.page_number
        """,
        (min_chars,),
    ).fetchall()

    by_doc: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        by_doc.setdefault(r["relative_path"], []).append(r)

    rng = random.Random(seed)
    out: dict[str, list[sqlite3.Row]] = {}
    for rel, page_rows in sorted(by_doc.items()):
        picked = sorted(rng.sample(page_rows, min(per_doc, len(page_rows))),
                        key=lambda r: r["page_number"])
        out[rel] = picked
    return out


def _cache_key(rel: str, page_number: int) -> str:
    return f"{rel}#{page_number}#dpi{RENDER_DPI}#{'_'.join(TESSERACT_ARGS)}"


def _load_cache(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _ocr_page(pdf_path: Path, page_number: int) -> tuple[str, int]:
    """render หน้า → Tesseract → (text, elapsed_ms) ใช้เส้นทางเดียวกับ run_ocr.py."""
    doc = fitz.open(str(pdf_path))
    try:
        page = doc[page_number - 1]
        pix = page.get_pixmap(dpi=RENDER_DPI)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name
        pix.save(tmp_path)
    finally:
        doc.close()

    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            ["tesseract", tmp_path, "stdout", *TESSERACT_ARGS],
            capture_output=True, text=True, encoding="utf-8", timeout=120,
        )
        text = proc.stdout or ""
    except Exception:
        text = ""
    elapsed = int((time.perf_counter() - t0) * 1000)
    Path(tmp_path).unlink(missing_ok=True)
    return text, elapsed


def evaluate(
    db_path: Path,
    pdf_base: Path,
    *,
    per_doc: int = 3,
    min_chars: int = 800,
    seed: int = 20260823,
    cache_path: Path | None = None,
) -> list[DocResult]:
    conn = sqlite3.connect(str(db_path))
    sample = _sample_pages(conn, per_doc, min_chars, seed)

    # cache ผล OCR ต่อ (ไฟล์, หน้า, dpi, argument) — การรัน Tesseract ใหม่ทั้งชุด
    # ใช้เวลาหลายนาที ทำให้ปรับสูตร metric แล้ววัดซ้ำได้ช้าเกินจะทำงานจริง
    cache_path = cache_path or (db_path.parent / "ocr_cer_cache.json")
    cache = _load_cache(cache_path)
    cache_dirty = False

    results: list[DocResult] = []
    total = sum(len(v) for v in sample.values())
    done = 0
    for rel, rows in sample.items():
        pdf_path = pdf_base / rel
        if not pdf_path.exists():
            print(f"  SKIP (ไม่พบไฟล์): {rel}")
            continue
        dr = DocResult(document_id=rows[0]["document_id"], relative_path=rel)
        for r in rows:
            key = _cache_key(rel, r["page_number"])
            cached = cache.get(key)
            if cached is not None:
                hyp, ms, tag = cached["text"], cached["elapsed_ms"], "cache"
            else:
                hyp, ms = _ocr_page(pdf_path, r["page_number"])
                cache[key] = {"text": hyp, "elapsed_ms": ms}
                cache_dirty = True
                tag = "ocr"
            dr.pages.append(PagePair(
                document_id=r["document_id"],
                relative_path=rel,
                page_number=r["page_number"],
                reference=r["page_text"] or "",
                hypothesis=hyp,
                elapsed_ms=ms,
            ))
            done += 1
            print(f"  [{done}/{total}] {rel} p{r['page_number']} "
                  f"CER={dr.pages[-1].cer:.3f} ({ms} ms, {tag})")
        results.append(dr)

    conn.close()
    if cache_dirty:
        cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return results


def render_report(results: list[DocResult]) -> str:
    all_pages = [p for d in results for p in d.pages]
    if not all_pages:
        return "ไม่มีหน้าที่วัดได้\n"

    def micro_of(pages: list[PagePair], strip: bool) -> float:
        norm = _strip_ws if strip else _collapse_ws
        dist = sum(_levenshtein_distance(norm(p.hypothesis), norm(p.reference)) for p in pages)
        den = sum(len(norm(p.reference)) for p in pages)
        return dist / den if den else 0.0

    def bag_micro(pages: list[PagePair]) -> float:
        pairs = [_bag_error(p.hypothesis, p.reference) for p in pages]
        den = sum(t for _, t in pairs)
        return sum(e for e, _ in pairs) / den if den else 0.0

    def line_micro(pages: list[PagePair]) -> float:
        pairs = [_line_match_error(p.hypothesis, p.reference) for p in pages]
        den = sum(t for _, t in pairs)
        return sum(e for e, _ in pairs) / den if den else 0.0

    cer_micro = micro_of(all_pages, False)
    cer_micro_nows = micro_of(all_pages, True)
    cer_bag = bag_micro(all_pages)
    cer_line = line_micro(all_pages)
    cer_macro = sum(p.cer for p in all_pages) / len(all_pages)
    wer_num = sum(p.wer * len(_collapse_ws(p.reference).split(" ")) for p in all_pages)
    wer_den = sum(len(_collapse_ws(p.reference).split(" ")) for p in all_pages)
    wer_micro = wer_num / wer_den if wer_den else 0.0

    ref_codes = hit_codes = extra_codes = 0
    for p in all_pages:
        r, h, e = p.code_stats
        ref_codes += r
        hit_codes += h
        extra_codes += e
    code_acc = hit_codes / ref_codes if ref_codes else 0.0

    L: list[str] = []
    L.append("# รายงาน CER — วัดคุณภาพ Tesseract 5 โดยตรง\n")
    L.append("ตัวเลขในรายงานนี้ตอบคำถามที่ `ocr_eval_report.md` ตอบไม่ได้: "
             "**Tesseract อ่านผิดแค่ไหน**\n")
    L.append("## วิธีวัด (paired-page protocol)\n")
    L.append("ground truth ระดับข้อความที่คนพิมพ์เองไม่มีในโปรเจกต์นี้ "
             "จึงใช้หน้าที่ PDF ฝังข้อความมาแล้ว (`text_layer`) เป็น reference "
             "แล้วส่งภาพของ *หน้าเดียวกัน* เข้า Tesseract เพื่อเทียบกัน\n")
    L.append("1. สุ่มหน้า `extraction_method = 'text_layer'` "
             f"ที่มีข้อความยาวพอ — ต่อเล่มไม่เกิน {max(len(d.pages) for d in results)} หน้า "
             "(seed คงที่ รันซ้ำได้ผลเดิม)\n")
    L.append(f"2. render หน้าเป็นภาพที่ **{RENDER_DPI} DPI** — ค่าเดียวกับ `run_ocr.py`\n")
    L.append(f"3. เรียก Tesseract ด้วย `{' '.join(TESSERACT_ARGS)}` — argument ชุดเดียวกับ pipeline จริง\n")
    L.append("4. เทียบผลกับ reference → CER / WER / ความแม่นของรหัสวิชา 8 หลัก\n")
    L.append(f"\n**ขนาดชุดวัด:** {len(results)} เล่ม / {len(all_pages)} หน้า / "
             f"{sum(len(_collapse_ws(p.reference)) for p in all_pages):,} อักขระอ้างอิง\n")

    L.append("\n## สรุปภาพรวม\n")
    L.append("| ตัวชี้วัด | ค่า | ความหมาย |")
    L.append("|---|---:|---|")
    L.append(f"| CER (micro) | {cer_micro:.3f} | เทียบข้อความทั้งหน้าตามลำดับ (นับช่องว่าง) |")
    L.append(f"| CER ไม่นับช่องว่าง | {cer_micro_nows:.3f} | **ขอบบน** — รวมความผิดของลำดับการอ่านไว้ด้วย |")
    L.append(f"| **line-match CER** | **{cer_line:.3f}** | ค้นแต่ละบรรทัดในหน้าทั้งหน้า — **ตัวเลขที่ควรอ้างเป็นคุณภาพการรู้จำ** |")
    L.append(f"| bag CER (micro) | {cer_bag:.3f} | ไม่สนลำดับเลย — **ขอบล่าง** (อ่านผิดหักกลบกันได้) |")
    L.append(f"| CER (macro) | {cer_macro:.3f} | เฉลี่ยต่อหน้า (ทุกหน้าน้ำหนักเท่ากัน) |")
    L.append(f"| WER (micro) | {wer_micro:.3f} | ระดับคำ — ภาษาไทยไม่มีช่องว่างระหว่างคำ อ่านประกอบเท่านั้น |")
    L.append(f"| รหัสวิชา 8 หลัก อ่านตรง | {code_acc:.3f} | {hit_codes}/{ref_codes} รหัส (เทียบแบบ set ไม่สนลำดับ) |")
    L.append("")
    L.append("สามค่ากลาง (`CER ไม่นับช่องว่าง`, `line-match CER`, `bag CER`) ใช้ตัวหารเดียวกัน "
             "คือจำนวนอักขระอ้างอิงหลังตัดช่องว่าง จึงเทียบกันตรง ๆ ได้ "
             "ส่วน `CER (micro)` มีตัวหารใหญ่กว่าเพราะนับช่องว่างด้วย")
    L.append(f"| รหัสวิชาที่ OCR สร้างเกิน | {extra_codes} | เลข 8 หลักที่ไม่มีใน reference (false positive) |")
    L.append(f"| เวลา OCR ต่อหน้า | {sum(p.elapsed_ms for p in all_pages) / len(all_pages):.0f} ms | เฉลี่ย |")

    # ── แยกหน้าตาราง vs หน้าข้อความบรรยาย ──
    tbl = [p for p in all_pages if p.is_table_like]
    prose = [p for p in all_pages if not p.is_table_like]

    L.append("\n## แยกตามชนิดหน้า — ต้นตอของ error อยู่ที่ไหน\n")
    L.append("| ชนิดหน้า | จำนวนหน้า | CER ไม่นับช่องว่าง | line-match CER | bag CER | ส่วนที่มาจากลำดับ |")
    L.append("|---|---:|---:|---:|---:|---:|")
    for label, group in (("ตาราง (เซลล์สั้นเป็นส่วนใหญ่)", tbl), ("ข้อความบรรยาย/ย่อหน้า", prose)):
        if not group:
            continue
        c = micro_of(group, True)
        ln = line_micro(group)
        b = bag_micro(group)
        L.append(f"| {label} | {len(group)} | {c:.3f} | {ln:.3f} | {b:.3f} | {c - ln:+.3f} |")
    L.append("\n`ส่วนที่มาจากลำดับ` = `CER ไม่นับช่องว่าง` − `line-match CER` "
             "ยิ่งมาก = ปัญหาของหน้ากลุ่มนั้นมาจาก **ลำดับการอ่าน** มากกว่าการรู้จำอักขระ\n")

    L.append("\n## แยกต่อเล่ม\n")
    L.append("| เอกสาร | หน้าที่วัด | CER ไม่นับช่องว่าง | line-match CER | bag CER | รหัสวิชาตรง | ref out-of-charset | ms/หน้า |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for d in sorted(results, key=lambda x: -x.cer_line_micro):
        rc, hc, _ = d.code_totals
        code_txt = f"{hc}/{rc}" if rc else "—"
        L.append(f"| {Path(d.relative_path).name} | {len(d.pages)} | {d.cer_micro_nows:.3f} | "
                 f"{d.cer_line_micro:.3f} | {d.cer_bag_micro:.3f} | {code_txt} | "
                 f"{d.ref_out_of_charset:.4f} | {d.mean_ms:.0f} |")
    L.append("\nเรียงตาม `line-match CER` — เล่มบนสุดคือเล่มที่ Tesseract อ่านอักขระผิดมากที่สุดจริง ๆ\n")

    L.append("\n## หน้าที่ line-match CER สูงสุด 10 อันดับ\n")
    L.append("| เอกสาร | หน้า | ชนิดหน้า | CER ไม่นับช่องว่าง | line-match CER | bag CER | อักขระอ้างอิง |")
    L.append("|---|---:|---|---:|---:|---:|---:|")
    for p in sorted(all_pages, key=lambda x: -x.cer_line)[:10]:
        kind = "ตาราง" if p.is_table_like else "บรรยาย"
        L.append(f"| {Path(p.relative_path).name} | {p.page_number} | {kind} | {p.cer_nows:.3f} | "
                 f"{p.cer_line:.3f} | {p.cer_bag:.3f} | {len(_strip_ws(p.reference)):,} |")

    L.append("\n## อ่านผลอย่างไร\n")
    thr = 0.05
    L.append(f"เกณฑ์ที่ตั้งไว้ในสเปกคือ CER ≤ {thr:.2f} ผลที่วัดได้อยู่ระหว่าง "
             f"**{cer_bag:.3f}** ถึง **{cer_micro_nows:.3f}** ขึ้นกับว่านับความผิดของ "
             "ลำดับการอ่านเข้าไปด้วยหรือไม่\n")
    L.append("\nตัวเลขสามค่านี้ต้องอ่านด้วยกัน ไม่ใช่เลือกค่าใดค่าเดียว (ทั้งสามใช้ตัวหารเดียวกัน):\n")
    L.append(f"- **CER ไม่นับช่องว่าง = {cer_micro_nows:.3f}** — ผลกระทบรวมที่ downstream เจอถ้าอ่านทั้งหน้าเรียงตามลำดับ")
    L.append(f"- **line-match CER = {cer_line:.3f}** — คุณภาพการรู้จำอักขระของ Tesseract หลังตัดปัญหาลำดับออก")
    L.append(f"- **bag CER = {cer_bag:.3f}** — ขอบล่าง (การอ่านผิดหักกลบกันเองได้)\n")

    gap = cer_micro_nows - cer_line
    L.append("\n### ข้อสรุปหลัก\n")
    if gap > cer_line:
        L.append(f"**error ส่วนใหญ่ไม่ได้มาจาก Tesseract อ่านตัวอักษรผิด แต่มาจากลำดับการอ่าน** — "
                 f"ส่วนต่างระหว่าง CER ไม่นับช่องว่าง กับ line-match CER เท่ากับ {gap:.3f} "
                 f"ซึ่งมากกว่าตัว line-match CER ({cer_line:.3f}) เอง\n")
        L.append("\nสาเหตุ: มคอ.2 มีหน้าตารางเยอะ (ตารางเปรียบเทียบหลักสูตรฉบับปรับปรุง, "
                 "ตาราง PLO mapping, ประวัติอาจารย์) `--psm 6` มองหน้าเป็นบล็อกข้อความเดียว "
                 "Tesseract จึงไล่อ่านทีละคอลัมน์ ขณะที่ text layer ไล่ทีละแถว "
                 "ข้อความเดียวกันแต่เรียงต่างกันจึงถูกนับเป็น error เต็ม ๆ\n")
        L.append("\n**ผลต่อการตัดสินใจเรื่อง OCR engine:** "
                 f"การรู้จำอักขระของ Tesseract อยู่ที่ {cer_line:.3f} "
                 f"({'เกิน' if cer_line > thr else 'อยู่ใน'}เกณฑ์ {thr:.2f}) "
                 "การเปลี่ยนไป Typhoon OCR จะช่วยได้มากหรือน้อยขึ้นกับว่ามันจัดการ layout "
                 "ตารางได้ดีกว่าหรือไม่ (Typhoon เป็น VLM คืนผลเป็น markdown จึงมีโอกาสรักษาโครงตาราง "
                 "ได้ดีกว่า) แต่ถ้าจะแก้ให้ตรงจุดที่สุด สิ่งที่ควรทำก่อนคือ **layout analysis** — "
                 "ตรวจหาคอลัมน์/เซลล์แล้วส่ง OCR ทีละบล็อก ซึ่งใช้ Tesseract เดิมได้เลย\n")
    else:
        L.append(f"**error ส่วนใหญ่มาจากการรู้จำอักขระผิดจริง** (line-match CER = {cer_line:.3f} "
                 f"เทียบกับส่วนที่มาจากลำดับ {gap:.3f}) การทดลอง OCR engine ที่แม่นกว่าสำหรับภาษาไทย "
                 "(เช่น Typhoon OCR) จึงเป็นทางที่ให้ผลตอบแทนสูง — โครงสร้าง cascade ใน "
                 "`katrag/ingest/ocr/` รองรับการเสียบ stage 2 อยู่แล้ว\n")

    L.append(f"\n### สิ่งที่ยืนยันได้จากตัวเลขอื่น\n")
    L.append(f"- รหัสวิชา 8 หลักอ่านตรง **{code_acc:.3f}** ({hit_codes}/{ref_codes}) "
             f"และสร้างเกินมา {extra_codes} รหัส — ค่านี้ทนต่อการสลับลำดับ และเป็นตัวที่ผูกกับ "
             "downstream ที่สุด เพราะ pipeline ใช้รหัสวิชาเป็นกุญแจสร้างตาราง `course`\n")
    L.append("- `ref out-of-charset` สูงในเล่มไหน แปลว่า text layer ของเล่มนั้นเองก็เพี้ยน "
             "ค่า CER ของเล่มนั้นจึงสูงเกินจริง (โทษ OCR ไม่ได้เต็มร้อย)\n")

    L.append(f"\n### ข้อจำกัดที่ต้องพูดตอนนำเสนอ\n")
    L.append("- reference คือ text layer ไม่ใช่คนพิมพ์ ตัวเลขชุดนี้จึงเป็น **proxy** ของ CER จริง "
             "ไม่ใช่ CER ต่อ ground truth มนุษย์\n")
    L.append(f"- ชุดวัดมี {len(all_pages)} หน้าจาก {len(results)} เล่ม (สุ่มด้วย seed คงที่) "
             "ไม่ใช่ทั้ง 3,689 หน้า\n")
    L.append("- เทียบกับ `ocr_eval_report.md`: ที่นั่น field accuracy วัดจากหน้า text layer "
             "ทั้งหมด (จากหน้า OCR = 0) จึงไม่สะท้อนคุณภาพ OCR — สองรายงานวัดคนละอย่าง "
             "ต้องอ่านคู่กัน\n")

    return "\n".join(L) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-doc", type=int, default=3, help="จำนวนหน้าที่สุ่มต่อเล่ม")
    parser.add_argument("--min-chars", type=int, default=800, help="ความยาวข้อความต่ำสุดของหน้าที่เลือก")
    parser.add_argument("--seed", type=int, default=20260823)
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent.parent
    db = root / "artifacts" / "katrag.sqlite3"
    pdf_base = root / "Information_Technology_Course"

    results = evaluate(db, pdf_base, per_doc=args.per_doc,
                       min_chars=args.min_chars, seed=args.seed)

    report_path = root / "artifacts" / "ocr_cer_report.md"
    report_path.write_text(render_report(results), encoding="utf-8")

    payload = {
        "protocol": "paired-page (text_layer as reference vs Tesseract on rendered image)",
        "tesseract_args": TESSERACT_ARGS,
        "render_dpi": RENDER_DPI,
        "documents": [
            {
                "relative_path": d.relative_path,
                "pages_measured": len(d.pages),
                "cer_micro": round(d.cer_micro, 4),
                "cer_line_match_micro": round(d.cer_line_micro, 4),
                "cer_bag_micro": round(d.cer_bag_micro, 4),
                "cer_micro_no_whitespace": round(d.cer_micro_nows, 4),
                "wer_micro": round(d.wer_micro, 4),
                "code_ref": d.code_totals[0],
                "code_hit": d.code_totals[1],
                "code_extra": d.code_totals[2],
                "ref_out_of_charset": round(d.ref_out_of_charset, 5),
                "mean_ms_per_page": round(d.mean_ms, 1),
                "pages": [
                    {"page_number": p.page_number, "cer": round(p.cer, 4),
                     "cer_line_match": round(p.cer_line, 4),
                     "cer_bag": round(p.cer_bag, 4),
                     "cer_no_whitespace": round(p.cer_nows, 4),
                     "table_like": p.is_table_like}
                    for p in d.pages
                ],
            }
            for d in results
        ],
    }
    json_path = root / "artifacts" / "ocr_cer_result.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"report -> {report_path}")
    print(f"json   -> {json_path}")


if __name__ == "__main__":
    main()
