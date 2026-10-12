"""Regression harness — รันคำถามทั้งชุดในโปรเซสเดียว (ไม่ผ่าน HTTP) แล้วเทียบกับคำตอบที่ตรวจแล้ว.

ใช้กันระบบถอยหลังระหว่างพัฒนา: ทุกครั้งที่แก้โค้ด รันชุดนี้ก่อน commit

แหล่งคำถาม
  - ชุดหลัก 28 ข้อ  : TEST_ITEMS ใน run_live_qa_tests.py + คำตอบที่ตรวจแล้วใน docs/reviewed_run.json
                      + ผลตรวจใน docs/manual_review.json
  - ชุดเพิ่ม 44 ข้อ  : docs/extra_levels.json (ระดับ / W / tester)
  - ข้อท้าทาย 20 ข้อ : docs/challenge_extended.json
  - คลังคำถามใหม่    : docs/eval_bank.json (ตรวจอัตโนมัติด้วย must / must_not)

สถานะต่อข้อ
  SAME     คำตอบเหมือนคำตอบที่ตรวจแล้วทุกตัวอักษร → ใช้ผลตรวจเดิม
  PASS     ผ่านเงื่อนไข must / must_not ของข้อนั้น
  FAIL     ไม่ผ่านเงื่อนไข must / must_not
  CHANGED  คำตอบเปลี่ยนจากที่ตรวจไว้ และไม่มีเงื่อนไขอัตโนมัติ → ต้องให้คนอ่านใหม่
  ERROR    ระบบพังระหว่างตอบ

Usage:
    python -m katrag.eval.regression                 # ทุกข้อ
    python -m katrag.eval.regression --only E1,H2    # เฉพาะบางข้อ
    python -m katrag.eval.regression --sets bank     # เฉพาะคลังคำถามใหม่
"""

from __future__ import annotations

import argparse
import ast
import json
import statistics
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "artifacts" / "katrag.sqlite3"


def _core_items() -> list[dict]:
    """อ่าน TEST_ITEMS จาก run_live_qa_tests.py ด้วย ast (ไฟล์นั้นรัน argparse ตอน import)."""
    tree = ast.parse((ROOT / "run_live_qa_tests.py").read_text(encoding="utf-8"))
    items: list[dict] = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "TEST_ITEMS" for t in node.targets
        ):
            items = ast.literal_eval(node.value)
    run = {o["id"]: o["answer"] for o in _load(ROOT / "docs" / "reviewed_run.json")}
    reviews = _load(ROOT / "docs" / "manual_review.json")["reviews"]
    out = []
    for it in items:
        rv = reviews.get(it["id"], {})
        out.append({
            "id": it["id"], "set": "core", "program": it["program"], "question": it["question"],
            "expected": it.get("expected", ""), "ref_answer": run.get(it["id"]),
            "verdict": rv.get("verdict", ""),
        })
    return out


def _json_items(path: Path, set_name: str) -> list[dict]:
    out = []
    for it in _load(path)["items"]:
        out.append({
            "id": it["id"], "set": set_name if set_name != "extra" else (it.get("group") or "level"),
            "program": it["program"], "question": it["question"], "expected": it.get("expected", ""),
            "ref_answer": it.get("answer"), "verdict": it.get("verdict", ""),
            "must": it.get("must", []), "must_not": it.get("must_not", []),
        })
    return out


def _bank_items() -> list[dict]:
    path = ROOT / "docs" / "eval_bank.json"
    if not path.exists():
        return []
    out = []
    for it in _load(path)["items"]:
        out.append({
            "id": it["id"], "set": "bank", "program": it["program"], "question": it["question"],
            "expected": it.get("expected", ""), "ref_answer": it.get("answer"),
            "verdict": it.get("verdict", ""), "must": it.get("must", []),
            "must_not": it.get("must_not", []), "source": it.get("source", ""),
        })
    return out


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def all_items() -> list[dict]:
    items = (
        _core_items()
        + _json_items(ROOT / "docs" / "extra_levels.json", "extra")
        + _json_items(ROOT / "docs" / "challenge_extended.json", "xyz")
        + _bank_items()
    )
    # เงื่อนไขอัตโนมัติของข้อเดิมที่ผ่าน LLM (docs/eval_rules.json)
    rules_path = ROOT / "docs" / "eval_rules.json"
    if rules_path.exists():
        rules = _load(rules_path)["rules"]
        for it in items:
            r = rules.get(it["id"])
            if r:
                it["must"] = r.get("must", [])
                it["must_not"] = r.get("must_not", [])
    return items


class _CountingLLM:
    """ห่อ LLM เพื่อนับจำนวนครั้งที่ถูกเรียก (บอกว่าข้อนั้นผ่าน LLM หรือไม่)."""

    def __init__(self, inner):
        self.inner = inner
        self.calls = 0

    def generate(self, prompt, max_tokens=2048):
        self.calls += 1
        return self.inner.generate(prompt, max_tokens=max_tokens)


def _norm(s: str | None) -> str:
    return " ".join((s or "").split())


def check(item: dict, answer: str) -> tuple[str, list[str]]:
    """คืน (สถานะ, เหตุผล) ตามกติกาในหัวไฟล์."""
    problems = []
    a = _norm(answer).lower()
    for m in item.get("must", []):
        alts = m if isinstance(m, list) else [m]
        if not any(_norm(x).lower() in a for x in alts):
            problems.append(f"ขาด: {' | '.join(alts)}")
    for m in item.get("must_not", []):
        if _norm(m).lower() in a:
            problems.append(f"มีคำต้องห้าม: {m}")
    has_rules = bool(item.get("must") or item.get("must_not"))
    if item.get("ref_answer") is not None and _norm(answer) == _norm(item["ref_answer"]):
        return ("FAIL" if problems else "SAME"), problems
    if has_rules:
        return ("FAIL" if problems else "PASS"), problems
    return "CHANGED", problems


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", help="รายการรหัสข้อ คั่นด้วย comma")
    ap.add_argument("--sets", help="เลือกชุด เช่น core,level,heldout,tester,xyz,bank")
    ap.add_argument("--no-llm", action="store_true", help="ไม่เรียก LLM (ข้อที่ต้องใช้ LLM จะได้หลักฐานดิบ)")
    ap.add_argument("--out", default=str(ROOT / "artifacts" / "regression_last.json"))
    args = ap.parse_args()

    items = all_items()
    if args.only:
        want = {x.strip() for x in args.only.split(",")}
        items = [i for i in items if i["id"] in want]
    if args.sets:
        sets = {x.strip() for x in args.sets.split(",")}
        items = [i for i in items if i["set"] in sets]

    from dotenv import load_dotenv

    from katrag.index.dense_search import DenseSearchIndex
    from katrag.query import pipeline
    from katrag.query.course_semantic import CourseSemanticIndex

    load_dotenv(ROOT / ".env")
    t0 = time.time()
    dense = DenseSearchIndex(DB)
    dense = dense if dense.load() > 0 else None
    course = CourseSemanticIndex(DB)
    course = course if course.load() > 0 else None
    llm = None
    if not args.no_llm:
        from katrag.query.typhoon_llm import TyphoonLLM

        llm = _CountingLLM(TyphoonLLM())
    from katrag.index import bge_encoder

    bge_encoder.encode_one("warmup")
    print(f"โหลดดัชนีและโมเดล {time.time() - t0:.1f} วินาที, {len(items)} ข้อ")

    # จับ intent ที่ structured path เลือก
    seen: dict[str, str] = {}
    orig_run_structured = pipeline.run_structured

    def traced(conn, question, **kw):
        out = orig_run_structured(conn, question, **kw)
        seen["intent"] = out.intent if out.matched else ""
        return out

    pipeline.run_structured = traced

    results = []
    for it in items:
        seen.clear()
        calls_before = llm.calls if llm else 0
        t = time.time()
        try:
            res = pipeline.answer_question(
                DB, it["question"], it["program"], dense_index=dense, course_index=course, llm=llm
            )
            answer, err = res.answer, ""
            pages = [c.page for c in res.citations]
        except Exception as exc:  # noqa: BLE001 — harness ต้องรายงานแทนการล้ม
            answer, err, pages = "", f"{type(exc).__name__}: {exc}", []
        dt = time.time() - t
        llm_calls = (llm.calls - calls_before) if llm else 0
        status, problems = ("ERROR", [err]) if err else check(it, answer)
        results.append({
            **{k: it.get(k) for k in ("id", "set", "program", "question", "expected", "verdict")},
            "status": status, "problems": problems, "latency": round(dt, 3),
            "intent": seen.get("intent", ""), "llm_calls": llm_calls, "pages": pages,
            "answer": answer, "ref_answer": it.get("ref_answer"),
        })
        mark = {"SAME": ".", "PASS": "+", "FAIL": "F", "CHANGED": "~", "ERROR": "E"}[status]
        print(f"{mark} {it['id']:<5} {dt:5.2f}s {seen.get('intent', '') or ('llm' if llm_calls else '-'):<16} {it['question'][:50]}")

    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    by = {}
    for r in results:
        by[r["status"]] = by.get(r["status"], 0) + 1
    lat = [r["latency"] for r in results]
    print("\nสรุป:", by)
    if lat:
        print(f"เวลา: เฉลี่ย {statistics.mean(lat):.2f} มัธยฐาน {statistics.median(lat):.2f} "
              f"สูงสุด {max(lat):.2f} · เกิน 5 วินาที {sum(x > 5 for x in lat)} ข้อ")
    for r in results:
        if r["status"] in ("FAIL", "CHANGED", "ERROR"):
            print(f"\n[{r['status']}] {r['id']} ({r['verdict'] or 'ใหม่'}) {r['question']}")
            for p in r["problems"]:
                print("   ", p)
    print(f"\nผลเต็ม -> {args.out}")


if __name__ == "__main__":
    main()
