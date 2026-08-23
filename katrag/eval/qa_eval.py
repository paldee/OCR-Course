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
        result.citations = len(data.get("citations", []))
        result.versions = data.get("versions_resolved", [])
        result.elapsed = float(data.get("total_time_seconds", 0.0))
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


def run_all(url: str) -> list[QAResult]:
    results: list[QAResult] = []
    for item in ALL_QUESTIONS:
        r = ask(url, item)
        status = "PASS" if r.auto_pass else ("ERR " if r.error else "CHECK")
        print(f"  [{status}] {r.item.qid} ({r.item.level}) {r.elapsed:6.2f}s  {r.item.question[:45]}")
        results.append(r)
    return results


# ══════════════════════════════════════════════════════════════════════
# Report
# ══════════════════════════════════════════════════════════════════════


def build_report(results: list[QAResult]) -> str:
    L: list[str] = []
    A = L.append

    A("# รายงานผลทดสอบคำถาม 3 ระดับ — KatRAG-lite")
    A("")
    A("ระบบยิงคำถามเข้า `POST /ask` โดยส่งเฉพาะ `question` + `program`")
    A("**ไม่มีการส่งเฉลยหรือคำอ้างอิงเข้าไปในระบบ/prompt** (บังคับด้วย assertion ใน `qa_eval.py`)")
    A("")
    A("`auto` เป็นเพียงการคัดกรองเบื้องต้น (ตรวจว่าคำสำคัญปรากฏในคำตอบไหม)")
    A("คะแนนจริงตามใบเสนอโครงการต้องให้ **ผู้ตรวจกาช่อง** ว่าคำตอบถูก + อ้างอิงถูก")
    A("")

    # ── สรุป ──
    levels = ["easy", "medium", "hard"]
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
    ap.add_argument("--report", default=str(root / "artifacts" / "qa_eval_report.md"))
    ap.add_argument("--json", default=str(root / "artifacts" / "qa_eval_result.json"))
    args = ap.parse_args()

    print(f"ยิงคำถาม {len(ALL_QUESTIONS)} ข้อไปที่ {args.url}")
    results = run_all(args.url)

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
            "error": r.error,
        }
        for r in results
    ]
    Path(args.json).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    npass = sum(1 for r in results if r.auto_pass)
    print()
    print(f"auto pass {npass}/{len(results)}")
    print(f"report -> {args.report}")
    print(f"json   -> {args.json}")


if __name__ == "__main__":
    main()
