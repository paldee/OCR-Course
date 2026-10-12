"""ชุดที่ระบบไม่เคยเห็น รอบ 10 เป็นต้นไป → docs/eval_holdout{N}.json.

แต่ละแบบคำถามมี 8 สำนวน รอบที่ N ใช้สำนวนที่ (N - 10) mod 8 และสุ่มวิชา/ตัวเลขด้วย seed ของรอบ
จึงได้คำถามที่ไม่ซ้ำรอบก่อน — ใช้วัดว่าระบบตอบได้ตั้งแต่รอบแรกโดยไม่ต้องปรับเพิ่ม

ค่าเฉลยคำนวณจากไฟล์เฉลยอาจารย์ (แผนการเรียน กฎหลักสูตร) ส่วนข้อบังคับที่เฉลยไม่มี
(ภาคทัณฑ์ พ้นสภาพ ภาคฤดูร้อน ข้อยกเว้นปีสุดท้าย) ใช้ภาคผนวกในเล่ม AIT 2566 หน้า 82-95 ที่ตรวจกับหน้าในเล่มแล้ว
ภาคที่เฉลยรวมหน่วยกิตเกิน 22 (ใส่วิชาทุกกลุ่มที่ให้เลือก) ไม่ใช้เป็นค่าเฉลยหน่วยกิตรวม

Usage:
    python -m katrag.eval.build_rounds 10            # ยาก/ท้าทาย
    python -m katrag.eval.build_rounds 14 --all      # ครบ 4 ระดับ
"""

from __future__ import annotations

import argparse
import json
import random
import re
from collections import Counter
from pathlib import Path

from katrag.eval.build_bank import (
    COOP_PLANS, GT, GT_KNOWN_ERRORS, PLANS, _clean, _codes, _credit_alts, _fixed_slot, gt_credit,
)

ROOT = Path(__file__).resolve().parents[2]
SEED = 20261013
RULES_SRC = "teacher_gt/rules_ground_truth.json"
#: วิชาบังคับก่อนที่เฉลยอาจารย์ไม่ตรงกับเล่ม (หลักสูตร, วิชา, วิชาบังคับก่อนในเฉลย): ที่มาในเล่ม
PREREQ_GT_ERRORS = {
    ("IT", "06016432", "06016413"): "เล่ม IT 2565 หน้า 337 วิชาบังคับก่อนคือ 06066304",
}
REG_SRC = "ข้อบังคับ ป.ตรี (AIT 2566 ภาคผนวก หน้า 82-95)"


def _year_alts(y: int, s: int | None = None) -> list[list[str]]:
    out = [[f"ปีที่ {y}", f"ปี {y}"]]
    if s is not None:
        out.append([f"ภาคการศึกษาที่ {s}", f"เทอม {s}", f"ภาค {s}", f"ภาคเรียนที่ {s}"])
    return out


def _cred(c: dict) -> int:
    m = re.match(r"\s*(\d+)", str(c.get("credits") or ""))
    return int(m.group(1)) if m else 0


class Plan:
    def __init__(self, prog: str, rel: str):
        self.prog = prog
        self.src = f"teacher_gt/{rel}"
        courses = json.loads((GT / rel).read_text(encoding="utf-8"))["courses"]
        self.named = [c for c in courses if _clean(c["name_th"]) and _codes(c["code"])
                      and (prog, _codes(c["code"])[0]) not in GT_KNOWN_ERRORS]
        self.by_code = {self.code(c): c for c in self.named}
        self.slotted = [c for c in self.named if _fixed_slot(c)]
        self.pre = {self.code(c): [p for p in _codes(c.get("prerequisite")) if p in self.by_code
                                   and (prog, self.code(c), p) not in PREREQ_GT_ERRORS] for c in self.named}
        self.deps: dict[str, list[str]] = {}
        for code, ps in self.pre.items():
            for p in ps:
                self.deps.setdefault(p, []).append(code)
        self.totals: dict[tuple[int, int], int] = {}
        for c in courses:
            if _fixed_slot(c) and gt_credit(prog, c) and (prog, (_codes(c["code"]) or [""])[0]) not in GT_KNOWN_ERRORS:
                self.totals[_fixed_slot(c)] = self.totals.get(_fixed_slot(c), 0) + gt_credit(prog, c)
        self.bad_years = {y for (y, _), v in self.totals.items() if v > 22}

    @staticmethod
    def code(c: dict) -> str:
        return _codes(c["code"])[0]

    def name(self, c: dict) -> str:
        return _clean(c["name_th"])

    def closure(self, code: str, depth: int = 0) -> set[str]:
        out: set[str] = set()
        for p in self.pre.get(code, []):
            if depth < 5 and p not in out:
                out |= {p} | self.closure(p, depth + 1)
        return out


def build(round_no: int, all_levels: bool = False) -> list[dict]:
    rng = random.Random(SEED + 1000 * round_no)
    v = (round_no - 10) % 8  # สำนวนที่ v ของทุกแบบคำถาม (8 สำนวน)
    seen: set[str] = set()
    for p in (ROOT / "docs").glob("eval_*.json"):
        if p.name != f"eval_holdout{round_no}.json":
            try:
                seen |= {i["question"] for i in json.loads(p.read_text(encoding="utf-8"))["items"]}
            except (KeyError, json.JSONDecodeError):
                pass
    items: list[dict] = []
    prefix = {"ง่าย": "E", "ปานกลาง": "M", "ยาก": "H", "ท้าทาย": "C"}

    def add(level, prog, question, must, expected, source):
        question = re.sub(r"\s+", " ", question).strip()
        if question in seen:
            return
        seen.add(question)
        n = sum(1 for i in items if i["level"] == level) + 1
        items.append({"id": f"N{round_no}{prefix[level]}{n}", "level": level, "program": prog,
                      "question": question, "must": must, "expected": expected, "source": source})

    def pick(phrases: list[str]) -> str:
        return phrases[v % len(phrases)]

    plans = [Plan(p, rel) for p, rel in PLANS]
    rules = json.loads((GT / "rules_ground_truth.json").read_text(encoding="utf-8"))["programs"]
    total = {p: int(next(v_["value"] for c in cats if c["category"] == "เกณฑ์การสำเร็จการศึกษา"
                         for v_ in c["values"] if "หน่วยกิต" in v_["label"])) for p, cats in rules.items()}

    # ── ยาก (ต่อหลักสูตร) ────────────────────────────────────────────────
    hard_types = ["chain", "early", "fail", "concurrent", "two_year", "pre_slot", "sem_total", "sem_cmp",
                  "year_total", "credit_sum", "late"]
    for pl in plans:
        P = pl.prog
        for t in rng.sample(hard_types, 6):
            if t == "chain":
                deep = [c for c in pl.named if len(pl.closure(pl.code(c))) >= 2]
                if deep:
                    c = rng.choice(deep)
                    cl = sorted(pl.closure(pl.code(c)))
                    add("ยาก", P, pick([
                        f"ถ้าอยากเรียน {pl.name(c)} ต้องผ่านวิชาไหนมาก่อนบ้าง รวมถึงวิชาพื้นฐานของวิชาพวกนั้นด้วย",
                        f"{pl.name(c)} ต้องมีพื้นฐานจากวิชาอะไรบ้าง ไล่ย้อนไปทุกชั้น",
                        f"ขอรายชื่อวิชาที่ต้องผ่านก่อนลง {pl.name(c)} ทั้งทางตรงและทางอ้อม",
                        f"ก่อนจะถึง {pl.name(c)} ต้องเก็บวิชาอะไรให้ผ่านบ้าง นับทุกทอด", f"เรียน {pl.name(c)} ได้ต้องผ่านอะไรมาบ้าง นับวิชาก่อนหน้าของวิชาก่อนหน้าด้วย", f"ไล่ให้หน่อยว่า {pl.name(c)} ต้องผ่านวิชาอะไรก่อน และวิชาพวกนั้นต้องผ่านอะไรอีก", f"วิชาที่ต้องผ่านทั้งหมดก่อนเรียน {pl.name(c)} มีกี่วิชา อะไรบ้าง", f"{pl.name(c)} มี prerequisite ทั้งหมดกี่ชั้น มีวิชาอะไรบ้าง"]),
                        [[x] for x in cl], ", ".join(cl), pl.src)
            elif t == "early":
                # วิชาบังคับก่อนอยู่ปีเดียวกับปีที่ขอลงหรือหลังกว่า → ลงไม่ได้ (หลักเดียวกับเฉลยข้อ H3)
                cand = [(c, p) for c in pl.slotted if _fixed_slot(c)[0] >= 2 for p in pl.pre[pl.code(c)]
                        if _fixed_slot(pl.by_code[p]) and _fixed_slot(pl.by_code[p])[0] >= _fixed_slot(c)[0] - 1]
                if cand:
                    c, p = rng.choice(cand)
                    y = _fixed_slot(c)[0] - 1
                    add("ยาก", P, pick([
                        f"อยู่ปี {y} ลง {pl.name(c)} ล่วงหน้าเลยได้ไหม",
                        f"ปี {y} จะชิงลงวิชา {pl.name(c)} ก่อนได้หรือเปล่า",
                        f"{pl.name(c)} ลงตอนปี {y} ได้ไหม",
                        f"ถ้าอยากเรียน {pl.name(c)} ตั้งแต่ปี {y} ทำได้มั้ย", f"เด็กปี {y} ลงวิชา {pl.name(c)} ได้เลยไหม", f"ปี {y} อยากลง {pl.name(c)} ข้ามไปก่อนเลยได้หรือไม่", f"สามารถลงทะเบียน {pl.name(c)} ตอนปี {y} ได้หรือเปล่า", f"{pl.name(c)} ปี {y} ลงได้ยัง"]),
                        [["ไม่ได้"], [p]], f"ไม่ได้ ต้องผ่าน {p} ก่อน", pl.src)
            elif t == "fail":
                cand = [p for p in pl.deps if p in pl.by_code]
                if cand:
                    p = rng.choice(cand)
                    add("ยาก", P, pick([
                        f"สอบตก {pl.name(pl.by_code[p])} แล้วจะลงวิชาไหนต่อไม่ได้บ้าง",
                        f"ถ้า {pl.name(pl.by_code[p])} ไม่ผ่าน จะติดวิชาอะไรบ้าง",
                        f"ติด F วิชา {pl.name(pl.by_code[p])} กระทบวิชาอะไร",
                        f"ไม่ผ่านวิชา {pl.name(pl.by_code[p])} จะเรียนวิชาไหนไม่ได้", f"{pl.name(pl.by_code[p])} ไม่ผ่านจะมีผลกับวิชาอะไรบ้าง", f"ถ้าได้ F ใน {pl.name(pl.by_code[p])} จะลงวิชาไหนไม่ได้", f"วิชาไหนบ้างที่ต้องผ่าน {pl.name(pl.by_code[p])} ก่อน", f"{pl.name(pl.by_code[p])} เป็นวิชาบังคับก่อนของวิชาอะไร"]),
                        [[x] for x in pl.deps[p]], ", ".join(pl.deps[p]), pl.src)
            elif t == "concurrent":
                pairs = [(pl.by_code[p], c) for c in pl.named for p in pl.pre[pl.code(c)]]
                if pairs:
                    a, b = rng.choice(pairs)
                    add("ยาก", P, pick([
                        f"ลง {pl.name(a)} กับ {pl.name(b)} ในเทอมเดียวกันได้ไหม",
                        f"จะเรียน {pl.name(a)} ควบคู่กับ {pl.name(b)} ภาคเดียวกันได้หรือเปล่า",
                        f"{pl.name(a)} กับ {pl.name(b)} ลงพร้อมกันได้มั้ย",
                        f"ขอลง {pl.name(b)} พร้อมกับ {pl.name(a)} ในภาคเดียวกันได้ไหม", f"{pl.name(a)} กับ {pl.name(b)} ลงเทอมเดียวกันได้หรือไม่", f"เทอมนี้จะลงทั้ง {pl.name(a)} และ {pl.name(b)} เลยได้ไหม", f"ลง {pl.name(b)} คู่กับ {pl.name(a)} ในเทอมเดียวได้มั้ย", f"อยากลง {pl.name(a)} และ {pl.name(b)} พร้อมกันในภาคนี้ ได้หรือเปล่า"]),
                        [["ไม่ได้"], [pl.code(a)]], f"ไม่ได้ ต้องผ่าน {pl.code(a)} ก่อน", pl.src)
            elif t == "two_year":
                a, b = rng.sample(pl.slotted, 2)
                (ya, sa), (yb, sb) = _fixed_slot(a), _fixed_slot(b)
                add("ยาก", P, pick([
                    f"{pl.name(a)} กับ {pl.name(b)} อันไหนเรียนก่อน",
                    f"{pl.name(a)} เรียนก่อนหรือหลัง {pl.name(b)}",
                    f"วิชา {pl.name(a)} กับวิชา {pl.name(b)} อยู่ปีเดียวกันหรือเปล่า",
                    f"{pl.name(a)} และ {pl.name(b)} ตามแผนอยู่ปีไหนบ้าง", f"{pl.name(a)} กับ {pl.name(b)} เรียนปีเดียวกันหรือคนละปี", f"ระหว่าง {pl.name(a)} กับ {pl.name(b)} วิชาไหนอยู่ปีหลังกว่า", f"{pl.name(a)} เรียนปีไหน แล้ว {pl.name(b)} เรียนปีไหน", f"{pl.name(a)} อยู่ก่อน {pl.name(b)} ใช่ไหม"]),
                    _year_alts(ya) + _year_alts(yb), f"{pl.code(a)} ปี {ya} ภาค {sa}, {pl.code(b)} ปี {yb} ภาค {sb}", pl.src)
            elif t == "pre_slot":
                cand = [(c, p) for c in pl.slotted for p in pl.pre[pl.code(c)] if _fixed_slot(pl.by_code[p])]
                if cand:
                    c, p = rng.choice(cand)
                    py, ps = _fixed_slot(pl.by_code[p])
                    add("ยาก", P, pick([
                        f"วิชาบังคับก่อนของ {pl.name(c)} เรียนตอนปีไหน",
                        f"{pl.name(c)} ต้องผ่านวิชาอะไรก่อน แล้ววิชานั้นอยู่ปีไหน",
                        f"ก่อนลง {pl.name(c)} ต้องผ่านวิชาอะไร วิชานั้นเปิดปีไหนเทอมไหน",
                        f"prerequisite ของ {pl.name(c)} คือวิชาอะไร อยู่ปีไหน", f"{pl.name(c)} มีวิชาบังคับก่อนอะไร และวิชานั้นเรียนเทอมไหน", f"ต้องเรียนอะไรก่อน {pl.name(c)} วิชานั้นอยู่ปีกี่", f"วิชาที่ต้องผ่านก่อนลง {pl.name(c)} อยู่ในแผนปีไหน", f"{pl.name(c)} ต้องผ่านวิชาไหนมาก่อน วิชานั้นลงตอนปีไหน"]),
                        [[p]] + _year_alts(py), f"{p} ปี {py} ภาค {ps}", pl.src)
            elif t == "sem_total":
                keys = [k for k in pl.totals if k[0] not in pl.bad_years]
                y, s = rng.choice(sorted(keys))
                add("ยาก", P, pick([
                    f"ปี {y} เทอม {s} ต้องลงทั้งหมดกี่หน่วยกิต",
                    f"ปี {y} ภาค {s} ตามแผนเรียนรวมกี่หน่วยกิต",
                    f"เทอม {s} ของปี {y} หน่วยกิตรวมเท่าไร",
                    f"ลงตามแผนปี {y} เทอม {s} ได้กี่หน่วยกิต", f"ปี {y} ภาคเรียนที่ {s} ลงกี่หน่วยกิต", f"หน่วยกิตรวมของปี {y} เทอม {s}", f"เทอม {s} ปี {y} ต้องลงทะเบียนรวมเท่าไหร่", f"ปีที่ {y} ภาคการศึกษาที่ {s} รวมกี่หน่วยกิต"]),
                    [[str(pl.totals[(y, s)])]], f"{pl.totals[(y, s)]} หน่วยกิต", pl.src)
            elif t == "sem_cmp":
                ys = [y for y in range(1, 5) if y not in pl.bad_years and (y, 1) in pl.totals and (y, 2) in pl.totals
                      and pl.totals[(y, 1)] != pl.totals[(y, 2)]]
                if ys:
                    y = rng.choice(ys)
                    a, b = pl.totals[(y, 1)], pl.totals[(y, 2)]
                    add("ยาก", P, pick([
                        f"ปี {y} เทอมไหนหน่วยกิตมากกว่ากัน",
                        f"ปี {y} ภาค 1 หรือภาค 2 ที่ต้องลงเยอะกว่า",
                        f"ปี {y} เทอมแรกกับเทอมสองหนักต่างกันกี่หน่วยกิต",
                        f"ระหว่างเทอม 1 กับเทอม 2 ของปี {y} เทอมไหนเบากว่า", f"ปี {y} ภาคไหนลงหน่วยกิตน้อยกว่า", f"ปี {y} เทอม 1 กับ 2 อันไหนหนักกว่ากัน", f"ปี {y} สองเทอมหน่วยกิตต่างกันเท่าไร", f"ปี {y} ภาคต้นหรือภาคปลายที่เรียนเยอะกว่า"]),
                        [[str(a)], [str(b)]], f"ภาค 1 {a}, ภาค 2 {b}", pl.src)
            elif t == "year_total":
                ys = [y for y in range(1, 5) if y not in pl.bad_years and (y, 1) in pl.totals and (y, 2) in pl.totals]
                if ys:
                    y = rng.choice(ys)
                    add("ยาก", P, pick([
                        f"ปี {y} ทั้งปีลงกี่หน่วยกิต",
                        f"รวมหน่วยกิตของปี {y} ทั้งสองเทอมได้เท่าไร",
                        f"ปีที่ {y} ต้องเรียนรวมทั้งปีกี่หน่วยกิต",
                        f"ทั้งปี {y} ตามแผนกี่หน่วยกิต", f"ปี {y} รวมทั้งปีกี่หน่วยกิต", f"ตลอดปีที่ {y} ลงทั้งหมดเท่าไร", f"ปี {y} ทั้งสองภาครวมกันกี่หน่วยกิต", f"หน่วยกิตทั้งปีของปี {y}"]),
                        [[str(pl.totals[(y, 1)] + pl.totals[(y, 2)])]], f"{pl.totals[(y, 1)]} + {pl.totals[(y, 2)]}", pl.src)
            elif t == "credit_sum":
                cs = [c for c in pl.slotted if _cred(c)]
                odd = [c for c in cs if _cred(c) != 3]
                a = rng.choice(odd or cs)
                b = rng.choice([c for c in cs if c is not a])
                add("ยาก", P, pick([
                    f"{pl.name(a)} กับ {pl.name(b)} รวมกันกี่หน่วยกิต",
                    f"ลง {pl.name(a)} และ {pl.name(b)} ได้หน่วยกิตรวมเท่าไร",
                    f"{pl.name(a)} บวก {pl.name(b)} กี่หน่วยกิต",
                    f"ถ้าเรียน {pl.name(a)} พร้อม {pl.name(b)} จะได้หน่วยกิตรวมกันกี่หน่วยกิต", f"{pl.name(a)} และ {pl.name(b)} รวมเป็นกี่หน่วยกิต", f"ลงสองวิชา {pl.name(a)} กับ {pl.name(b)} ได้กี่หน่วยกิต", f"หน่วยกิตของ {pl.name(a)} รวมกับ {pl.name(b)} เท่ากับเท่าไร", f"{pl.name(a)} + {pl.name(b)} กี่หน่วยกิต"]),
                    [[str(_cred(a) + _cred(b))]], f"{_cred(a)} + {_cred(b)}", pl.src)
            elif t == "late":
                cand = [c for c in pl.slotted if _fixed_slot(c)[0] <= 3 and pl.pre[pl.code(c)]]
                if cand:
                    c = rng.choice(cand)
                    y = _fixed_slot(c)[0]
                    add("ยาก", P, pick([
                        f"{pl.name(c)} ไปลงตอนปี {y + 1} ได้ไหม",
                        f"เลื่อน {pl.name(c)} ไปเรียนปี {y + 1} ได้หรือเปล่า",
                        f"ถ้าลง {pl.name(c)} ช้ากว่าแผนเป็นปี {y + 1} ได้มั้ย",
                        f"ปี {y + 1} ค่อยลง {pl.name(c)} ได้ไหม", f"ลง {pl.name(c)} ตอนปี {y + 1} แทนปี {y} ได้ไหม", f"ปี {y + 1} เพิ่งลง {pl.name(c)} ยังทันไหม", f"ถ้าปี {y} ไม่ได้ลง {pl.name(c)} ไปลงปี {y + 1} ได้หรือเปล่า", f"{pl.name(c)} ลงปี {y + 1} ได้มั้ย"]),
                        [[pl.pre[pl.code(c)][0]]] + _year_alts(y), f"ได้ แผนกำหนดปี {y} ต้องผ่าน {pl.pre[pl.code(c)][0]}", pl.src)

    # ── ท้าทาย ────────────────────────────────────────────────────────────
    for pl in rng.sample(plans, 2):  # เรียนครบถึงปี N แล้วสะสม/เหลือ
        if sum(pl.totals.values()) != total[pl.prog] or pl.bad_years:
            continue
        y = rng.choice([1, 2, 3])
        done = sum(x for (yy, _), x in pl.totals.items() if yy <= y)
        q, must = [
            (f"เรียนจบปี {y} ตามแผนแล้วมีหน่วยกิตสะสมกี่หน่วยกิต", [[str(done)]]),
            (f"ผ่านปี {y} ตามแผนครบ จะเหลืออีกกี่หน่วยกิตถึงจบ", [[str(total[pl.prog] - done)]]),
            (f"เรียนครบถึงปี {y} ได้กี่หน่วยกิตแล้ว ขาดอีกเท่าไร", [[str(done)], [str(total[pl.prog] - done)]]),
            (f"ถ้าเรียนตามแผนจนจบปี {y} ยังขาดอีกกี่หน่วยกิตถึงจะจบ", [[str(total[pl.prog] - done)]]),
            (f"ถ้าเรียนตามแผนครบปี {y} จะได้หน่วยกิตเท่าไร", [[str(done)]]),
            (f"จบปี {y} ตามแผน เหลือหน่วยกิตที่ต้องเรียนอีกเท่าไร", [[str(total[pl.prog] - done)]]),
            (f"หน่วยกิตสะสมหลังเรียนครบปี {y} และที่เหลือถึงจบ", [[str(done)], [str(total[pl.prog] - done)]]),
            (f"เรียนครบ {y} ปีตามแผน ยังขาดกี่หน่วยกิต", [[str(total[pl.prog] - done)]]),
        ][v]
        add("ท้าทาย", pl.prog, q, must, f"สะสม {done} เหลือ {total[pl.prog] - done}", pl.src)

    shared = [(code, a, b) for a in plans for b in plans if a.prog < b.prog and {a.prog, b.prog} != {"AIT", "BIT"}
              for code in a.by_code if code in b.by_code and _fixed_slot(a.by_code[code]) and _fixed_slot(b.by_code[code])]
    for code, a, b in rng.sample(shared, min(2, len(shared))):
        (y1, s1), (y2, s2) = _fixed_slot(a.by_code[code]), _fixed_slot(b.by_code[code])
        nm = a.name(a.by_code[code])
        add("ท้าทาย", a.prog, pick([
            f"{nm} ใน {a.prog} กับ {b.prog} เรียนปีเดียวกันไหม",
            f"{nm} ของ {a.prog} และ {b.prog} อยู่เทอมไหน",
            f"วิชา {nm} ถ้าอยู่ {a.prog} กับอยู่ {b.prog} เรียนตอนไหนต่างกันไหม",
            f"{a.prog} กับ {b.prog} เรียน {nm} ปีไหนบ้าง", f"{nm} เรียนปีไหนใน {a.prog} และใน {b.prog}", f"ถ้าอยู่ {a.prog} กับ {b.prog} จะเรียน {nm} ตอนไหน", f"{nm} ของ {a.prog} กับ {b.prog} อยู่ภาคเดียวกันไหม", f"เทียบปีที่เรียน {nm} ระหว่าง {a.prog} กับ {b.prog}"]),
            [[a.prog], [b.prog]] + _year_alts(y1) + _year_alts(y2),
            f"{a.prog} ปี {y1} ภาค {s1}, {b.prog} ปี {y2} ภาค {s2}", "teacher_gt (แผนของทั้งสองหลักสูตร)")

    for P in rng.sample(list(total), 2):  # เก็บได้ N หน่วยกิต เกรด G
        c = total[P] - rng.choice([0, 3, 5, 9])
        g = rng.choice([1.85, 2.00, 2.35, 3.05])
        must = [[str(total[P])]] + ([[str(total[P] - c)]] if c < total[P] else []) + ([["2.00"]] if g < 2 else [])
        add("ท้าทาย", P, pick([
            f"มี {c} หน่วยกิต เกรด {g:.2f} จบได้ไหม",
            f"สะสม {c} หน่วยกิต GPA {g:.2f} พอจบหรือยัง",
            f"ได้ {c} หน่วยกิตแล้ว เกรดเฉลี่ย {g:.2f} จะสำเร็จการศึกษาได้หรือไม่",
            f"ตอนนี้ {c} หน่วยกิต เกรดเฉลี่ยสะสม {g:.2f} ขาดอะไรอีกไหมถึงจะจบ", f"เก็บได้ {c} หน่วยกิต เกรดเฉลี่ย {g:.2f} จบหรือยัง", f"{c} หน่วยกิต GPA {g:.2f} พอจะจบไหม", f"หน่วยกิตตอนนี้ {c} เกรด {g:.2f} ยังขาดอะไร", f"ถ้ามี {c} หน่วยกิตกับเกรด {g:.2f} สำเร็จการศึกษาได้ไหม"]),
            must, f"ต้องครบ {total[P]} และ GPA >= 2.00", RULES_SRC)

    g = rng.choice([3.30, 3.45, 3.60, 3.80])
    th = "3.75" if g >= 3.75 else "3.50" if g >= 3.5 else "3.25"
    add("ท้าทาย", rng.choice(list(total)), pick([
        f"จบเกรด {g:.2f} ได้เกียรตินิยมไหม", f"เกรดเฉลี่ยสะสม {g:.2f} ได้เกียรตินิยมอันดับอะไร",
        f"GPA {g:.2f} ตอนจบ จะได้เกียรตินิยมหรือเปล่า", f"จบด้วย {g:.2f} ได้อันดับ 1 หรืออันดับ 2", f"เกรด {g:.2f} ตอนจบได้เกียรตินิยมอันดับไหน", f"GPAX {g:.2f} ได้อันดับเกียรตินิยมหรือเปล่า", f"จบเกรด {g:.2f} จะได้เกียรตินิยมอันดับสองไหม", f"ได้ {g:.2f} ตอนจบ ติดเกียรตินิยมไหม"]),
        [[th]], f"เกณฑ์ {th}", RULES_SRC)

    g = rng.choice([0.85, 1.40, 1.75, 1.95])
    add("ท้าทาย", rng.choice(list(total)), pick([
        f"เกรดเฉลี่ยสะสม {g:.2f} จะโดนอะไร", f"GPA {g:.2f} โดนรีไทร์ไหม",
        f"เทอมนี้ได้เกรดเฉลี่ย {g:.2f} ต้องกังวลอะไรไหม", f"ถ้าเกรดเฉลี่ยสะสม {g:.2f} ยังเรียนต่อได้หรือเปล่า", f"เกรดเฉลี่ย {g:.2f} จะเป็นอะไรไหม", f"GPA {g:.2f} จะโดนภาคทัณฑ์ไหม", f"ได้เกรดเฉลี่ยสะสม {g:.2f} ยังอยู่ต่อได้ไหม", f"เกรด {g:.2f} โดนไล่ออกไหม"]),
        [["พ้นสภาพ"], ["1.00"]] if g < 1 else [["ภาคทัณฑ์"], ["2.00"]],
        "พ้นสภาพ (< 1.00)" if g < 1 else "ภาคทัณฑ์ (< 2.00)", REG_SRC)

    n = rng.choice([7, 8, 23, 24, 25])
    add("ท้าทาย", rng.choice(list(total)), pick([
        f"ภาคปกติลง {n} หน่วยกิตได้ไหม", f"เทอมหน้าจะลง {n} หน่วยกิต ผิดเกณฑ์ไหม",
        f"ลงทะเบียน {n} หน่วยกิตในเทอมปกติได้หรือเปล่า", f"ภาคเรียนปกติขอลง {n} หน่วยกิตได้มั้ย", f"ลง {n} หน่วยกิตในภาคปกติได้หรือเปล่า", f"เทอมปกติลง {n} หน่วยกิตผิดระเบียบไหม", f"จะลงเรียน {n} หน่วยกิตเทอมนี้ได้ไหม", f"ภาคปกติ {n} หน่วยกิตลงได้ไหม"]),
        [["22"]] if n > 22 else [["9"]], "ภาคปกติ 9-22", REG_SRC)

    n = rng.choice([6, 9, 12])
    add("ท้าทาย", rng.choice(list(total)), pick([
        f"ซัมเมอร์ลง {n} หน่วยกิตได้ไหม", f"ภาคฤดูร้อนจะลง {n} หน่วยกิตเกินไหม",
        f"summer นี้อยากลง {n} หน่วยกิต ทำได้หรือเปล่า", f"ภาคเรียนพิเศษลงได้ {n} หน่วยกิตไหม", f"ภาคฤดูร้อนลง {n} หน่วยกิตได้หรือเปล่า", f"เทอมซัมเมอร์ลงได้ {n} หน่วยกิตไหม", f"ช่วงซัมเมอร์จะลง {n} หน่วยกิต ได้ไหม", f"ลง {n} หน่วยกิตในภาคฤดูร้อนได้มั้ย"]),
        [["9"]], "ภาคฤดูร้อนไม่เกิน 9", REG_SRC)

    n = rng.choice([23, 25, 26])
    add("ท้าทาย", rng.choice(list(total)), pick([
        f"ปีสุดท้ายเหลือ {n} หน่วยกิต ลงเทอมเดียวจบได้ไหม", f"เทอมสุดท้ายต้องลง {n} หน่วยกิตถึงจะจบ ลงได้หรือเปล่า",
        f"ใกล้จบแล้วเหลือ {n} หน่วยกิต ขอลงทีเดียวได้ไหม", f"ปีสุดท้ายจะลง {n} หน่วยกิตในเทอมเดียวได้มั้ย", f"ปีสุดท้ายขอลง {n} หน่วยกิตได้ไหม", f"เทอมสุดท้ายก่อนจบลง {n} หน่วยกิตได้หรือเปล่า", f"ปีสี่เทอมสุดท้ายเหลือ {n} หน่วยกิต ลงทั้งหมดได้ไหม", f"จะจบแล้วเหลือ {n} หน่วยกิต ลงในเทอมเดียวได้มั้ย"]),
        [["27"]], "ได้ ไม่เกิน 27 (ข้อยกเว้นปีสุดท้าย)", REG_SRC)

    p1, p2 = rng.sample(list(total), 2)
    add("ท้าทาย", p1, pick([
        f"{p1} กับ {p2} ต้องเรียนกี่หน่วยกิต ต่างกันเท่าไร", f"หลักสูตร {p1} หรือ {p2} ที่เรียนหน่วยกิตน้อยกว่า",
        f"{p1} เรียนมากกว่าหรือน้อยกว่า {p2} กี่หน่วยกิต", f"หน่วยกิตรวม {p1} เทียบกับ {p2}", f"{p1} เรียนกี่หน่วยกิต {p2} เรียนกี่หน่วยกิต", f"ระหว่าง {p1} กับ {p2} หลักสูตรไหนหน่วยกิตเยอะกว่า", f"{p1} ต่างจาก {p2} กี่หน่วยกิต", f"เทียบหน่วยกิตจบของ {p1} กับ {p2}"]),
        [[str(total[p1])], [str(total[p2])]], f"{p1} {total[p1]}, {p2} {total[p2]}", RULES_SRC)

    if all_levels:
        _add_easy_medium(add, pick, rng, plans, total)
    return items


def _add_easy_medium(add, pick, rng, plans, total) -> None:
    """ง่าย/ปานกลาง: ค้นข้อมูลขั้นเดียว (หน่วยกิต ปี/ภาค ชื่ออังกฤษ วิชาบังคับก่อน รายวิชาในภาค หน่วยกิตรวม)."""
    for pl in plans:
        P = pl.prog
        for c in rng.sample(pl.slotted, 2):
            add("ง่าย", P, pick([f"วิชา {pl.name(c)} กี่หน่วยกิต", f"{pl.name(c)} มีกี่หน่วยกิต",
                                 f"หน่วยกิตของ {pl.name(c)}", f"{pl.name(c)} ได้กี่หน่วยกิตคะ", f"วิชา {pl.name(c)} หน่วยกิตเท่าไร", f"{pl.name(c)} กี่หน่วยกิตครับ", f"ขอจำนวนหน่วยกิตวิชา {pl.name(c)}", f"{pl.name(c)} นับกี่หน่วยกิต"]),
                [_credit_alts(c["credits"])], _clean(c["credits"]), pl.src)
        c = rng.choice(pl.slotted)
        y, s = _fixed_slot(c)
        add("ง่าย", P, pick([f"{pl.name(c)} เรียนปีไหน", f"วิชา {pl.name(c)} อยู่ปีไหนเทอมไหน",
                             f"{pl.name(c)} ต้องเรียนตอนปีอะไร", f"เรียน {pl.name(c)} ตอนไหน", f"{pl.name(c)} อยู่ปีไหนในแผน", f"วิชา {pl.name(c)} ลงปีอะไร", f"{pl.name(c)} เปิดสอนปีไหน", f"แผนการเรียนให้ลง {pl.name(c)} ปีไหน"]),
            _year_alts(y), f"ปี {y} ภาค {s}", pl.src)
        c = rng.choice([c for c in pl.slotted if _clean(c["name_en"])])
        en = _clean(c["name_en"]).upper()
        add("ง่าย", P, pick([f"{pl.name(c)} ชื่อภาษาอังกฤษว่าอะไร", f"ชื่ออังกฤษของวิชา {pl.name(c)}",
                             f"วิชา {pl.name(c)} ภาษาอังกฤษเรียกว่าอะไร", f"{pl.name(c)} ภาษาอังกฤษคือ", f"{pl.name(c)} ภาษาอังกฤษว่าอะไร", f"English name ของ {pl.name(c)}", f"วิชา {pl.name(c)} ชื่อภาษาอังกฤษ", f"{pl.name(c)} in English"]),
            [[en, en.title()]], en, pl.src)
        c = rng.choice(pl.slotted)
        add("ง่าย", P, pick([f"รหัสวิชา {pl.name(c)} คืออะไร", f"{pl.name(c)} รหัสวิชาอะไร",
                             f"ขอรหัสของวิชา {pl.name(c)}", f"วิชา {pl.name(c)} ใช้รหัสอะไร", f"{pl.name(c)} รหัสอะไร", f"รหัสวิชาของ {pl.name(c)}", f"วิชา {pl.name(c)} มีรหัสวิชาว่าอะไร", f"{pl.name(c)} course code"]),
            [[pl.code(c)]], pl.code(c), pl.src)
        c = rng.choice([c for c in pl.slotted if pl.pre[pl.code(c)]])
        add("ปานกลาง", P, pick([f"{pl.name(c)} มีวิชาบังคับก่อนไหม", f"ต้องผ่านวิชาอะไรก่อนลง {pl.name(c)}",
                                f"วิชาบังคับก่อนของ {pl.name(c)} คืออะไร", f"ลง {pl.name(c)} ต้องผ่านอะไรมาก่อน", f"{pl.name(c)} ต้องเรียนอะไรก่อน", f"ก่อนลง {pl.name(c)} ต้องผ่านวิชาไหน", f"{pl.name(c)} มี prerequisite อะไร", f"วิชาบังคับก่อนของวิชา {pl.name(c)}"]),
            [[p] for p in pl.pre[pl.code(c)]], ", ".join(pl.pre[pl.code(c)]), pl.src)
        c = rng.choice(pl.slotted)
        en = _clean(c["name_en"]).title()
        if en:
            add("ปานกลาง", P, pick([f"{en} กี่หน่วยกิต", f"วิชา {en} เรียนปีไหน",
                                    f"{en} ภาษาไทยชื่อวิชาอะไร", f"{en} รหัสวิชาอะไร", f"{en} มีกี่หน่วยกิต", f"{en} อยู่ปีไหน", f"{en} ชื่อไทยคืออะไร", f"รหัสวิชาของ {en}"]),
                [[_clean(c["credits"]).replace(" ", "")]] if pick([1, 2, 3, 4] * 2) == 1 else
                _year_alts(_fixed_slot(c)[0]) if pick([1, 2, 3, 4] * 2) == 2 else
                [[pl.name(c)]] if pick([1, 2, 3, 4] * 2) == 3 else [[pl.code(c)]],
                f"{pl.code(c)} {pl.name(c)}", pl.src)
        req = {}
        for c in pl.slotted:
            if c.get("type") == "บังคับ":
                req.setdefault(_fixed_slot(c), []).append(pl.code(c))
        y, s = rng.choice(sorted(req))
        add("ปานกลาง", P, pick([f"ปี {y} เทอม {s} เรียนวิชาบังคับอะไรบ้าง", f"วิชาบังคับปี {y} ภาค {s} มีอะไร",
                                f"ปี {y} เทอม {s} ต้องลงวิชาบังคับไหนบ้าง", f"ภาค {s} ปี {y} มีวิชาบังคับอะไรบ้าง", f"ปี {y} ภาค {s} วิชาบังคับมีอะไรบ้าง", f"วิชาบังคับของปี {y} เทอม {s}", f"ปี {y} เทอม {s} บังคับเรียนวิชาอะไร", f"ขอรายชื่อวิชาบังคับปี {y} ภาค {s}"]),
            [[x] for x in req[(y, s)]], ", ".join(req[(y, s)]), pl.src)
    for P in rng.sample(list(total), 2):
        add("ปานกลาง", P, pick([f"หลักสูตร {P} ต้องเรียนทั้งหมดกี่หน่วยกิตถึงจะจบ", f"{P} หน่วยกิตรวมตลอดหลักสูตรเท่าไร",
                                f"จบ {P} ต้องเก็บกี่หน่วยกิต", f"หลักสูตร {P} เรียนครบกี่หน่วยกิต", f"{P} ต้องเก็บหน่วยกิตทั้งหมดเท่าไร", f"หน่วยกิตขั้นต่ำที่ต้องเรียนของ {P}", f"เรียน {P} ให้จบต้องมีกี่หน่วยกิต", f"{P} จบที่กี่หน่วยกิต"]),
            [[str(total[P])]], str(total[P]), RULES_SRC)
    add("ปานกลาง", rng.choice(list(total)), pick(["ภาคปกติลงได้สูงสุดกี่หน่วยกิต", "เทอมปกติต้องลงขั้นต่ำกี่หน่วยกิต",
                                                  "ลงทะเบียนภาคปกติได้กี่หน่วยกิต", "ภาคปกติลงน้อยสุดและมากสุดกี่หน่วยกิต",
                                                  "ภาคปกติลงได้มากสุดกี่หน่วยกิต", "ภาคปกติต้องลงอย่างน้อยกี่หน่วยกิต",
                                                  "เทอมปกติลงได้ไม่เกินกี่หน่วยกิต", "ภาคปกติลงต่ำสุดได้เท่าไร"]),
        [["22"]] if pick([1, 2, 3, 4, 1, 2, 1, 2]) in (1, 3) else [["9"]], "9-22", REG_SRC)
    add("ปานกลาง", rng.choice(list(total)), pick(["เกียรตินิยมอันดับ 1 ต้องได้เกรดเท่าไร", "เกียรตินิยมอันดับ 2 ใช้เกรดเฉลี่ยเท่าไร",
                                                  "เกรดขั้นต่ำของเกียรตินิยมอันดับหนึ่ง", "อันดับสองต้องได้ GPA เท่าไหร่",
                                                  "เกียรตินิยมอันดับหนึ่งใช้เกรดขั้นต่ำเท่าไร", "เกรดเท่าไหร่ถึงได้เกียรตินิยมอันดับสอง",
                                                  "ได้อันดับ 1 ต้อง GPAX เท่าไร", "เกณฑ์เกียรตินิยมอันดับ 2"]),
        [["3.50"]] if pick([1, 2, 3, 4, 1, 2, 1, 2]) in (1, 3) else [["3.25"]], "3.50 / 3.25", RULES_SRC)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("round", type=int)
    ap.add_argument("--all", action="store_true", help="ครบ 4 ระดับ (ค่าปกติ: ยาก/ท้าทาย)")
    args = ap.parse_args()
    items = build(args.round, args.all)
    out = ROOT / "docs" / f"eval_holdout{args.round}.json"
    kind = "ครบ 4 ระดับ" if args.all else "ยาก/ท้าทาย"
    out.write_text(json.dumps({"_meta": f"ชุดที่ไม่เคยเห็น รอบ {args.round} ({kind}) สร้างด้วย build_rounds.py",
                               "items": [dict(i, _round_kind=kind) for i in items]},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(len(items), "ข้อ", dict(Counter(i["level"] for i in items)), "->", out)


if __name__ == "__main__":
    main()
