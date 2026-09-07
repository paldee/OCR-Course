"""QA Evaluation — ยิงคำถาม 3 ระดับเข้า /ask แล้วสร้างรายงานให้คนตรวจ.

**Anti-leak guarantee**
    payload ที่ส่งไป /ask มีแค่ {"question", "program"} เท่านั้น
    ฟิลด์ ``expected`` ของ ground truth ไม่เคยถูกส่งเข้า API/LLM
    ฟังก์ชัน ``_build_payload`` มี assertion บังคับข้อนี้ไว้

การให้คะแนน 2 ชั้น:
    1. auto  — ตรวจว่าคำ/ตัวเลขใน ``check`` ปรากฏในคำตอบ และ ``forbid`` ไม่ปรากฏ
               (เป็น screening เร็ว ไม่ใช่คะแนนสุดท้าย)
    2. human — รายงานพิมพ์ช่อง [ ] ให้ผู้ตรวจกาเอง (คำตอบถูก / อ้างอิงถูก)
               ตามเกณฑ์ในใบเสนอโครงการ

Usage:
    python -m katrag.eval.qa_eval
    python -m katrag.eval.qa_eval --url http://127.0.0.1:8000/ask
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from katrag.eval.qa_questions import ALL_QUESTIONS, QAItem

# ══════════════════════════════════════════════════════════════════════
# Anti-leak: payload ต้องมีแค่ question + program
# ══════════════════════════════════════════════════════════════════════

_ALLOWED_PAYLOAD_KEYS = {"question", "program"}


def _build_payload(item: QAItem) -> dict[str, str]:
    """สร้าง payload ที่ส่งเข้า API — ห้ามมีเฉลยเด็ดขาด."""
    payload = {"question": item.question, "program": item.program}
    # hard guarantee
    assert set(payload) <= _ALLOWED_PAYLOAD_KEYS, "payload มีคีย์ที่ไม่อนุญาต"
    blob = json.dumps(payload, ensure_ascii=False)
    assert item.expected not in blob, "เฉลยรั่วเข้า payload!"
    assert item.reference not in blob, "reference รั่วเข้า payload!"
    return payload


# ══════════════════════════════════════════════════════════════════════
# Result
# ══════════════════════════════════════════════════════════════════════


@dataclass
class QAResult:
    item: QAItem
    answer: str = ""
    citations: int = 0
    versions: list[str] = field(default_factory=list)
    elapsed: float = 0.0
    error: str = ""
    check_hit: list[str] = field(default_factory=list)
    check_miss: list[str] = field(default_factory=list)
    forbid_hit: list[str] = field(default_factory=list)
    # ── citation metrics (เทียบกับ gold_set) ──
    cited_pages: list[tuple[str, int]] = field(default_factory=list)
    expected_pages: list[tuple[str, int]] = field(default_factory=list)
    cite_precision: float | None = None
    cite_recall: float | None = None
    answered_from: str = ""   # "structured" | "llm+evidence"

    @property
    def auto_pass(self) -> bool:
        """ผ่าน auto-screening: ได้ check ครบ และไม่ติด forbid."""
        if self.error:
            return False
        return not self.check_miss and not self.forbid_hit

    @property
    def check_ratio(self) -> float:
        total = len(self.item.check)
        if not total:
            return 1.0
        return len(self.check_hit) / total

    # ── การวัดสองมิติ (แยก "กลไกดึงข้อมูล" ออกจาก "คำตอบถูก") ──

    @property
    def retrieval_status(self) -> str:
        """มิติ 1 — กลไกดึงข้อมูลทำงานถูกไหม (แทน 'SQL รันผ่าน' ของ Text-to-SQL).

        ระบบเราไม่ได้ให้ LLM สร้าง SQL — structured path ใช้ SQL ที่เขียนตายตัว
        จึงรันผ่านเสมอ (100% ไม่มีความหมาย) มิติที่มีความหมายกับสถาปัตยกรรม RAG
        คือ "ดึงหน้าหลักฐานที่ถูกต้องมาได้ไหม"

        คืนค่า:
          "error"      — เรียก API ไม่สำเร็จ (กลไกล้มจริง)
          "structured" — ตอบจากตารางที่ validate แล้ว (ไม่ต้องมี citation)
          "hit"        — ดึงหน้าหลักฐานที่อยู่ใน gold_set มาได้อย่างน้อย 1 หน้า
          "miss"       — คืน citation แต่ไม่มีหน้าใดตรง gold_set
          "no_gold"    — ยังไม่มีหน้าหลักฐานใน gold_set ให้เทียบ (วัดไม่ได้)
        """
        if self.error:
            return "error"
        if self.answered_from == "structured":
            return "structured"
        if not self.expected_pages:
            return "no_gold"
        return "hit" if (self.cite_recall or 0.0) > 0 else "miss"

    @property
    def retrieval_ok(self) -> bool:
        """มิติ 1 ผ่าน: กลไกทำงานถูก (structured หรือ ดึงหน้าถูกอย่างน้อย 1)."""
        return self.retrieval_status in ("structured", "hit")

    @property
    def retrieval_measurable(self) -> bool:
        """มิติ 1 วัดได้ (ตัด no_gold ที่ยังไม่มีเฉลยหน้าออก)."""
        return self.retrieval_status in ("hit", "miss", "structured", "error")


# ══════════════════════════════════════════════════════════════════════
# Runner
# ══════════════════════════════════════════════════════════════════════


def ask(url: str, item: QAItem, timeout: float = 180.0) -> QAResult:
    result = QAResult(item=item)
    payload = _build_payload(item)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        result.answer = data.get("answer", "")
        cites = data.get("citations", [])
        result.citations = len(cites)
        result.cited_pages = [
            (str(c.get("document_id", "")), int(c.get("page", 0))) for c in cites
        ]
        result.versions = data.get("versions_resolved", [])
        result.elapsed = float(data.get("total_time_seconds", 0.0))
        # คำตอบที่มาจาก structured path จะไม่มี citation (ตอบจากตารางตรง ๆ)
        result.answered_from = "structured" if not cites else "llm+evidence"
    except Exception as exc:  # noqa: BLE001 — รายงานทุก error ไม่ให้ล้ม
        result.error = f"{type(exc).__name__}: {exc}"
        result.elapsed = time.time() - t0
        return result

    low = result.answer.lower()
    for token in item.check:
        (result.check_hit if token.lower() in low else result.check_miss).append(token)
    for token in item.forbid:
        if token.lower() in low:
            result.forbid_hit.append(token)
    return result


def load_gold_pages(db_path: Path) -> dict[str, list[tuple[str, int]]]:
    """โหลดหน้าหลักฐานที่ถูกต้องจาก gold_set (qid -> [(document_id, page)])."""
    import sqlite3

    if not db_path.is_file():
        return {}
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    out: dict[str, list[tuple[str, int]]] = {}
    try:
        rows = conn.execute(
            "SELECT payload_json, expected_citations_json FROM gold_set "
            "WHERE item_kind='question'"
        ).fetchall()
    except Exception:
        conn.close()
        return {}
    for r in rows:
        try:
            qid = json.loads(r["payload_json"]).get("qid", "")
            cites = json.loads(r["expected_citations_json"])
        except Exception:
            continue
        if qid:
            out[qid] = [
                (str(c.get("document_id", "")), int(c.get("page", 0))) for c in cites
            ]
    conn.close()
    return out


def run_all(url: str, gold: dict[str, list[tuple[str, int]]] | None = None) -> list[QAResult]:
    from katrag.eval.metrics import citation_page_precision, citation_page_recall

    gold = gold or {}
    results: list[QAResult] = []
    for item in ALL_QUESTIONS:
        r = ask(url, item)
        exp = gold.get(item.qid, [])
        r.expected_pages = exp
        # วัด citation เฉพาะข้อที่ระบบคืน citation มา และมีหน้าหลักฐานใน gold_set
        if exp and r.cited_pages:
            r.cite_precision = citation_page_precision(r.cited_pages, exp)
            r.cite_recall = citation_page_recall(r.cited_pages, exp)
        status = "PASS" if r.auto_pass else ("ERR " if r.error else "CHECK")
        cite = ""
        if r.cite_precision is not None:
            cite = f" cite_p={r.cite_precision:.2f}"
        print(
            f"  [{status}] {r.item.qid} ({r.item.level}) {r.elapsed:6.2f}s"
            f"{cite}  {r.item.question[:40]}"
        )
        results.append(r)
    return results


# ══════════════════════════════════════════════════════════════════════
# Report
# ══════════════════════════════════════════════════════════════════════


def build_report(results: list[QAResult]) -> str:
    L: list[str] = []
    A = L.append

    A("# รายงานผลทดสอบคำถาม 3 ระดับ — Jingjok-Thorius")
    A("")
    A("ระบบยิงคำถามเข้า `POST /ask` โดยส่งเฉพาะ `question` + `program`")
    A("**ไม่มีการส่งเฉลยหรือคำอ้างอิงเข้าไปในระบบ/prompt** (บังคับด้วย assertion ใน `qa_eval.py`)")
    A("")
    A("`auto` เป็นเพียงการคัดกรองเบื้องต้น (ตรวจว่าคำสำคัญปรากฏในคำตอบไหม)")
    A("คะแนนจริงตามใบเสนอโครงการต้องให้ **ผู้ตรวจกาช่อง** ว่าคำตอบถูก + อ้างอิงถูก")
    A("")

    levels = ["easy", "medium", "hard"]

    # ══════════════════════════════════════════════════════════════════
    # การวัดสองมิติ — แยก "กลไกดึงข้อมูล" ออกจาก "คำตอบถูก"
    # ══════════════════════════════════════════════════════════════════
    A("## การวัดผลสองมิติ")
    A("")
    A("แยกวัดสองด้านเพื่อวินิจฉัยได้ว่าพลาดตรงไหน — กลไกดึงข้อมูลผิด หรือ LLM ตอบผิด")
    A("")
    A("| มิติ | Text-to-SQL ทั่วไป | ระบบนี้ (RAG) วัดอะไรแทน |")
    A("|---|---|---|")
    A("| 1. กลไกทำงาน | SQL รันผ่าน | **Retrieval hit** — ดึงหน้าหลักฐานถูกไหม |")
    A("| 2. คำตอบถูก | ตรงเฉลย | **Answer accuracy** — auto-screen + ผู้ตรวจกา |")
    A("")
    A("> ระบบนี้ไม่ได้ให้ LLM สร้าง SQL — เส้นทาง structured ใช้ SQL ที่เขียนตายตัว "
      "จึงรันผ่านเสมอ (100% ไม่มีความหมาย) จึงวัด **retrieval hit** แทน ซึ่งสะท้อนว่า "
      "กลไกค้นหลักฐานทำงานถูกจริงหรือไม่")
    A("")

    # ── มิติ 1: retrieval ──
    n_hit = sum(1 for r in results if r.retrieval_status == "hit")
    n_struct_r = sum(1 for r in results if r.retrieval_status == "structured")
    n_miss = sum(1 for r in results if r.retrieval_status == "miss")
    n_nogold = sum(1 for r in results if r.retrieval_status == "no_gold")
    n_err_r = sum(1 for r in results if r.retrieval_status == "error")
    n_measurable = sum(1 for r in results if r.retrieval_measurable)
    n_ok = sum(1 for r in results if r.retrieval_ok)
    rate = f"{n_ok}/{n_measurable}" if n_measurable else "—"
    pct = f" ({n_ok / n_measurable * 100:.0f}%)" if n_measurable else ""
    A("### มิติ 1 — Retrieval hit rate")
    A("")
    A(f"**{rate}{pct}** ของข้อที่วัดได้ (กลไกดึงข้อมูลทำงานถูก)")
    A("")
    A("| สถานะ | จำนวน | ความหมาย |")
    A("|---|---:|---|")
    A(f"| hit | {n_hit} | ดึงหน้าหลักฐานที่อยู่ใน gold_set มาได้ ≥ 1 หน้า |")
    A(f"| structured | {n_struct_r} | ตอบจากตารางที่ validate แล้ว (ไม่ต้องมี citation) |")
    A(f"| miss | {n_miss} | คืน citation แต่ไม่มีหน้าใดตรง gold_set |")
    A(f"| no_gold | {n_nogold} | ยังไม่มีหน้าหลักฐานใน gold_set ให้เทียบ (ตัดออกจากตัวหาร) |")
    A(f"| error | {n_err_r} | เรียก API ไม่สำเร็จ |")
    A("")

    # ── มิติ 2: answer accuracy ──
    A("### มิติ 2 — Answer accuracy (auto-screening)")
    A("")
    npass_all = sum(1 for r in results if r.auto_pass)
    A(f"**{npass_all}/{len(results)}** ผ่าน auto-screening "
      "(มีคำสำคัญครบ + ไม่ติดคำต้องห้าม)")
    A("")
    A("> auto-screening เป็นการคัดกรองเบื้องต้นด้วย substring เท่านั้น "
      "คะแนน accuracy จริงต้องให้ผู้ตรวจกาช่องในตารางท้ายรายงาน "
      "(`คำตอบถูก` AND `อ้างอิงถูก`)")
    A("")

    # ── สรุป ──
    A("## สรุปผล auto-screening")
    A("")
    A("| ระดับ | จำนวนข้อ | auto pass | คำสำคัญที่พบ (เฉลี่ย) | เวลาเฉลี่ย |")
    A("|---|---:|---:|---:|---:|")
    for lv in levels:
        sub = [r for r in results if r.item.level == lv]
        if not sub:
            continue
        npass = sum(1 for r in sub if r.auto_pass)
        ratio = sum(r.check_ratio for r in sub) / len(sub)
        tavg = sum(r.elapsed for r in sub) / len(sub)
        A(f"| {lv} | {len(sub)} | {npass}/{len(sub)} | {ratio:.2f} | {tavg:.2f}s |")
    npass_all = sum(1 for r in results if r.auto_pass)
    nerr = sum(1 for r in results if r.error)
    A(f"| **รวม** | **{len(results)}** | **{npass_all}/{len(results)}** | | |")
    A("")
    if nerr:
        A(f"> มี {nerr} ข้อที่เรียก API ไม่สำเร็จ")
        A("")

    # ── แหล่งที่มาของคำตอบ (structured vs LLM+evidence) ──
    n_struct = sum(1 for r in results if r.answered_from == "structured")
    n_llm = sum(1 for r in results if r.answered_from == "llm+evidence")
    A("## คำตอบมาจากเส้นทางไหน")
    A("")
    A("| เส้นทาง | จำนวนข้อ | มี citation | ลักษณะ |")
    A("|---|---:|---|---|")
    A(f"| structured (ตอบจากตาราง course/plan ตรง ๆ) | {n_struct} | ไม่มี | "
      "ข้อมูลครบและ deterministic ไม่ผ่าน LLM |")
    A(f"| LLM + evidence (hybrid retrieval → Typhoon) | {n_llm} | มี | "
      "ต้องอ้างอิงหน้า/หัวข้อ |")
    A("")
    A("เส้นทาง structured ไม่คืน citation เพราะตอบจากฐานข้อมูลที่ผ่านการ validate แล้ว ")
    A("(ทุกแถวใน `course` มี `provenance_id` ชี้หน้าต้นทางอยู่ จึงตรวจย้อนกลับได้)")
    A("")

    # ── citation accuracy ──
    scored = [r for r in results if r.cite_precision is not None]
    A("## ความถูกต้องของการอ้างอิง (citation accuracy)")
    A("")
    if scored:
        mp = sum(r.cite_precision or 0 for r in scored) / len(scored)
        mr = sum(r.cite_recall or 0 for r in scored) / len(scored)
        A(f"วัดได้ **{len(scored)} จาก {len(results)} ข้อ** (เฉพาะข้อที่ระบบคืน citation "
          "และมีหน้าหลักฐานใน `gold_set`)")
        A("")
        A("| ตัวชี้วัด | ค่า | ความหมาย |")
        A("|---|---:|---|")
        A(f"| citation page precision | {mp:.3f} | หน้าที่อ้าง อยู่ในชุดหน้าหลักฐานจริงกี่ % |")
        A(f"| citation page recall | {mr:.3f} | หน้าหลักฐานจริง ถูกอ้างถึงกี่ % |")
        A("")
        A("| ข้อ | ระดับ | หน้าที่ระบบอ้าง | หน้าหลักฐาน (gold) | precision | recall | เพดาน recall |")
        A("|---|---|---:|---:|---:|---:|---:|")
        for r in scored:
            ceil = min(1.0, len(r.cited_pages) / len(r.expected_pages))
            A(f"| {r.item.qid} | {r.item.level} | {len(r.cited_pages)} | "
              f"{len(r.expected_pages)} | {r.cite_precision:.3f} | {r.cite_recall:.3f} | "
              f"{ceil:.3f} |")
        A("")
        A("**เพดาน recall** = จำนวน citation ที่ระบบคืน / จำนวนหน้าหลักฐาน — "
          "ถ้าหน้าหลักฐานมากกว่าจำนวน citation ที่คืนได้ recall จะไม่มีทางถึง 1.0")
        A("ระบบตั้งค่าคืน citation 10 รายการต่อคำถาม ดังนั้นควรอ่าน precision เป็นตัวหลัก")
        A("")
        A("`gold_set` สร้างจาก provenance ของข้อมูลจริงในฐาน (`katrag/eval/build_gold_set.py`) ")
        A("ไม่ได้ derive จากคำตอบของระบบ จึงไม่เป็นการตรวจตัวเอง")
        A("")
    else:
        A("ยังไม่มีข้อที่วัดได้ — ทุกข้อตอบผ่านเส้นทาง structured (ไม่คืน citation) ")
        A("หรือยังไม่มีหน้าหลักฐานใน `gold_set`")
        A("")
        A("รัน `python -m katrag.eval.build_gold_set` เพื่อสร้างหน้าหลักฐานก่อน")
        A("")

    # ── แบบฟอร์มให้ผู้ตรวจ ──
    A("## ตารางบันทึกผลสำหรับผู้ตรวจ")
    A("")
    A("| ข้อ | ระดับ | คำถาม | auto | คำตอบถูก | อ้างอิงถูก |")
    A("|---|---|---|---|---|---|")
    for r in results:
        auto = "PASS" if r.auto_pass else ("ERROR" if r.error else "ตรวจมือ")
        q = r.item.question.replace("|", "/")
        A(f"| {r.item.qid} | {r.item.level} | {q} | {auto} | [ ] | [ ] |")
    A("")
    A("สูตรคะแนน: `accuracy = จำนวนข้อที่ (คำตอบถูก AND อ้างอิงถูก) / จำนวนข้อทั้งหมด`")
    A("")

    # ── รายละเอียดทีละข้อ ──
    A("## รายละเอียดทีละข้อ")
    A("")
    for lv in levels:
        sub = [r for r in results if r.item.level == lv]
        if not sub:
            continue
        A(f"### ระดับ {lv}")
        A("")
        for r in sub:
            A(f"#### {r.item.qid} — {r.item.question}")
            A("")
            A(f"- หลักสูตรที่เลือก: `{r.item.program or '(ไม่ระบุ)'}`")
            A(f"- เวลา: {r.elapsed:.2f}s | citations: {r.citations} | "
              f"versions: {', '.join(r.versions) or '—'}")
            A(f"- retrieval (มิติ 1): `{r.retrieval_status}` | "
              f"auto-screen (มิติ 2): `{'PASS' if r.auto_pass else 'ตรวจมือ'}`")
            if r.item.check:
                A(f"- คำสำคัญที่ต้องมี: พบ {len(r.check_hit)}/{len(r.item.check)} "
                  f"{'(ขาด: ' + ', '.join(r.check_miss) + ')' if r.check_miss else ''}")
            if r.forbid_hit:
                A(f"- **พบคำที่ไม่ควรมี**: {', '.join(r.forbid_hit)}")
            A("")
            if r.error:
                A(f"> ERROR: {r.error}")
                A("")
            else:
                A("<details><summary>คำตอบของระบบ</summary>")
                A("")
                A("```")
                ans = r.answer if len(r.answer) <= 2500 else r.answer[:2500] + "\n...(ตัด)"
                A(ans)
                A("```")
                A("")
                A("</details>")
                A("")
            A(f"**เฉลย (สำหรับผู้ตรวจ):** {r.item.expected}")
            A("")
            A(f"**อ้างอิง:** {r.item.reference}")
            A("")
            A("---")
            A("")

    return "\n".join(L)


def main() -> None:
    root = Path(__file__).resolve().parent.parent.parent
    ap = argparse.ArgumentParser(description="QA evaluation (3 levels)")
    ap.add_argument("--url", default="http://127.0.0.1:8000/ask")
    ap.add_argument("--db", default=str(root / "artifacts" / "katrag.sqlite3"))
    ap.add_argument("--report", default=str(root / "artifacts" / "qa_eval_report.md"))
    ap.add_argument("--json", default=str(root / "artifacts" / "qa_eval_result.json"))
    args = ap.parse_args()

    gold = load_gold_pages(Path(args.db))
    print(f"ยิงคำถาม {len(ALL_QUESTIONS)} ข้อไปที่ {args.url}")
    print(f"หน้าหลักฐานจาก gold_set: {len(gold)} ข้อ")
    results = run_all(args.url, gold)

    report = build_report(results)
    out = Path(args.report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")

    payload = [
        {
            "qid": r.item.qid,
            "level": r.item.level,
            "program": r.item.program,
            "question": r.item.question,
            "answer": r.answer,
            "citations": r.citations,
            "elapsed": round(r.elapsed, 3),
            "auto_pass": r.auto_pass,
            "check_hit": r.check_hit,
            "check_miss": r.check_miss,
            "forbid_hit": r.forbid_hit,
            "answered_from": r.answered_from,
            "retrieval_status": r.retrieval_status,
            "retrieval_ok": r.retrieval_ok,
            "cited_pages": [{"document_id": d, "page": p} for d, p in r.cited_pages],
            "expected_pages": [{"document_id": d, "page": p} for d, p in r.expected_pages],
            "citation_precision": r.cite_precision,
            "citation_recall": r.cite_recall,
            "error": r.error,
        }
        for r in results
    ]
    Path(args.json).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    npass = sum(1 for r in results if r.auto_pass)
    n_measurable = sum(1 for r in results if r.retrieval_measurable)
    n_ok = sum(1 for r in results if r.retrieval_ok)
    print()
    print(f"มิติ 1 retrieval hit : {n_ok}/{n_measurable} (ข้อที่วัดได้)")
    print(f"มิติ 2 auto pass     : {npass}/{len(results)}")
    print(f"report -> {args.report}")
    print(f"json   -> {args.json}")


if __name__ == "__main__":
    main()
