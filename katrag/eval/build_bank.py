"""สร้างคลังคำถามทดสอบจากเฉลยของอาจารย์ (data/teacher_gt) → docs/eval_bank.json.

เฉลยมาจากไฟล์ของอาจารย์ ไม่ได้มาจากฐานข้อมูลของระบบ จึงใช้จับข้อผิดพลาดของการสกัดได้จริง
ทุกข้อมีเงื่อนไข must (ต้องมีครบทุกกลุ่ม ในกลุ่มเดียวกันมีตัวใดตัวหนึ่งก็พอ)
สุ่มด้วย seed คงที่ รันซ้ำได้คลังเดิม

Usage:
    python -m katrag.eval.build_bank            # สร้าง docs/eval_bank.json ใหม่
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GT = ROOT / "data" / "teacher_gt"
SEED = 20261012
PER_TEMPLATE = 6

#: จุดที่เฉลยอาจารย์ไม่ตรงกับเล่ม (ตรวจกับหน้าในเล่มแล้ว) — ไม่ใช้เป็นเงื่อนไขของคลังคำถาม
#: (โปรแกรม, รหัส): เหตุผล
GT_KNOWN_ERRORS = {
    ("DSBA", "06016401"): "ไม่อยู่ในตารางแผนปี 1 ภาค 1 ของ DSBA 2565 (หน้า 23 และ 30)",
    ("AIT", "06016401"): "ไม่อยู่ในตารางแผนปี 1 ภาค 1 ของ AIT 2566 (หน้า 23)",
}

#: หน่วยกิตที่เฉลยอาจารย์เว้นว่างไว้ (หลักสูตร, รหัส): (หน่วยกิตตามเล่ม, ที่มา)
GT_CREDIT_FIXES = {
    ("BIT", "06036116"): (3, "เล่ม BIT 2565 หน้า 29: 06036116 การตลาดเชิงดิจิทัล 3(2-2-5) ภาคนี้รวม 12"),
}


def gt_credit(prog: str, c: dict) -> int:
    """หน่วยกิตของแถวในเฉลย (0 = ไม่มี) — เติมค่าที่เฉลยเว้นว่างจาก GT_CREDIT_FIXES."""
    m = re.match(r"\s*(\d+)", str(c.get("credits") or ""))
    if m:
        return int(m.group(1))
    codes = _codes(c.get("code"))
    return GT_CREDIT_FIXES.get((prog, codes[0]), (0, ""))[0] if codes else 0


PLANS = [
    ("AIT", "AIT/AIT_academic_plan.json"),
    ("BIT", "BIT/BIT_academic_plan_no_coop.json"),
    ("DSBA", "DSBA/DSBA_academic_plan_no_coop.json"),
    ("IT", "IT/IT_academic_plan_no_coop.json"),
]


def _clean(s) -> str:
    return " ".join(str(s or "").split())


def _credit_alts(c: str) -> list[str]:
    c = _clean(c).replace(" ", "")
    m = re.match(r"(\d+)\((.*)\)", c)
    return [c, f"{m.group(1)} ({m.group(2)})"] if m else [c]


def _codes(text) -> list[str]:
    return re.findall(r"\d{8}", str(text or ""))


def _fixed_slot(c: dict) -> tuple[int, int] | None:
    try:
        y, s = int(c["year"]), int(c["semester"])
    except (TypeError, ValueError):
        return None
    return (y, s) if 1 <= y <= 4 and s in (1, 2) else None


def build() -> list[dict]:
    rng = random.Random(SEED)
    items: list[dict] = []

    def add(prog, kind, question, must, expected, source):
        items.append({
            "id": f"G-{prog}-{kind}-{sum(1 for i in items if i['id'].startswith(f'G-{prog}-{kind}-')) + 1}",
            "program": prog, "question": question, "must": must,
            "expected": expected, "source": source,
        })

    for prog, rel in PLANS:
        courses = json.loads((GT / rel).read_text(encoding="utf-8"))["courses"]
        src = f"teacher_gt/{rel}"
        named = [c for c in courses if _clean(c["name_th"]) and _codes(c["code"])]
        slotted = [c for c in named if _fixed_slot(c)]

        for c in rng.sample(named, min(PER_TEMPLATE, len(named))):
            add(prog, "credit", f"วิชา{_clean(c['name_th'])}มีกี่หน่วยกิต",
                [_credit_alts(c["credits"])], _clean(c["credits"]), src)
        for c in rng.sample(slotted, min(PER_TEMPLATE, len(slotted))):
            y, s = _fixed_slot(c)
            add(prog, "yearsem", f"วิชา{_clean(c['name_th'])}เรียนปีไหน เทอมไหน",
                [[f"ปีที่ {y}"], [f"ภาคการศึกษาที่ {s}"]], f"ปี {y} ภาค {s}", src)
        for c in rng.sample(slotted, min(PER_TEMPLATE, len(slotted))):
            y, s = _fixed_slot(c)
            add(prog, "en_yearsem", f"{_clean(c['name_en']).title()} เรียนตอนไหน",
                [[f"ปีที่ {y}"], [f"ภาคการศึกษาที่ {s}"]], f"ปี {y} ภาค {s}", src)
        for c in rng.sample(named, min(PER_TEMPLATE, len(named))):
            en = _clean(c["name_en"])
            if en:
                add(prog, "en_name", f"วิชา{_clean(c['name_th'])} ชื่อภาษาอังกฤษว่าอะไร",
                    [[en[:25]]], en, src)
        for c in rng.sample(named, min(PER_TEMPLATE, len(named))):
            add(prog, "by_code", f"รหัสวิชา {_codes(c['code'])[0]} คือวิชาอะไร",
                [[_clean(c["name_th"]), _clean(c["name_en"])]], _clean(c["name_th"]), src)
        with_pre = [c for c in named if _codes(c.get("prerequisite"))]
        for c in rng.sample(with_pre, min(PER_TEMPLATE, len(with_pre))):
            pre = _codes(c["prerequisite"])
            add(prog, "prereq", f"วิชา{_clean(c['name_th'])}ต้องผ่านวิชาอะไรก่อน",
                [[p] for p in pre], _clean(c["prerequisite"]), src)
        # รายวิชาบังคับต่อภาค (แผนปกติ)
        slots: dict[tuple[int, int], list[str]] = {}
        for c in slotted:
            if c.get("type") == "บังคับ" and (prog, _codes(c["code"])[0]) not in GT_KNOWN_ERRORS:
                slots.setdefault(_fixed_slot(c), []).append(_codes(c["code"])[0])
        for (y, s) in sorted(slots)[:8]:
            add(prog, "semester", f"ปี {y} เทอม {s} ต้องเรียนวิชาบังคับอะไรบ้าง",
                [[code] for code in slots[(y, s)]], ", ".join(slots[(y, s)]), src)

    # กฎหลักสูตร
    rules = json.loads((GT / "rules_ground_truth.json").read_text(encoding="utf-8"))["programs"]
    for prog, cats in rules.items():
        vals = {(c["category"], v["label"]): v["value"] for c in cats for v in c["values"]}
        src = "teacher_gt/rules_ground_truth.json"

        def num(label_cat, label):
            v = vals.get((label_cat, label))
            m = re.search(r"\d+(?:\.\d+)?", str(v or ""))
            return m.group(0) if m else None

        g = num("เกณฑ์การสำเร็จการศึกษา", "หน่วยกิตขั้นต่ำที่ต้องเรียนจบ")
        if g:
            add(prog, "rule", "ต้องเรียนกี่หน่วยกิตถึงจะจบ", [[g]], g, src)
            add(prog, "rule", "หลักสูตรนี้มีหน่วยกิตรวมตลอดหลักสูตรเท่าไร", [[g]], g, src)
        if num("เกณฑ์การสำเร็จการศึกษา", "GPA สะสมขั้นต่ำ"):
            add(prog, "rule", "ต้องได้เกรดเฉลี่ยสะสมอย่างน้อยเท่าไรจึงจะสำเร็จการศึกษา", [["2.00", "2.0"]], "2.00", src)
        for label, q in (("GPA อันดับ 1", "เกียรตินิยมอันดับ 1 ต้องได้เกรดเฉลี่ยเท่าไร"),
                         ("GPA อันดับ 2", "เกียรตินิยมอันดับ 2 ต้องได้เกรดเฉลี่ยเท่าไร")):
            v = num("เกณฑ์เกียรตินิยม", label)
            if v:
                add(prog, "rule", q, [[v]], v, src)
        for label, q in (("หน่วยกิตขั้นต่ำ/ภาคปกติ", "ภาคปกติต้องลงทะเบียนอย่างน้อยกี่หน่วยกิต"),
                         ("หน่วยกิตสูงสุด/ภาคปกติ", "ภาคปกติลงทะเบียนได้มากที่สุดกี่หน่วยกิต")):
            v = num("เกณฑ์การลงทะเบียน", label)
            if v:
                add(prog, "rule", q, [[v]], v, src)

    items += _build_v2(add)

    # วิชาศึกษาทั่วไป (ใช้ร่วมทุกหลักสูตร)
    gen = json.loads((GT / "general_education_ground_truth.json").read_text(encoding="utf-8"))["courses"]
    gen = [c for c in gen if _clean(c["name_th"]) and _codes(c["code"])]
    for i, c in enumerate(rng.sample(gen, 10)):
        prog = PLANS[i % 4][0]
        add(prog, "gened", f"วิชา{_clean(c['name_th'])}มีกี่หน่วยกิต",
            [_credit_alts(c["credits"])], _clean(c["credits"]),
            "teacher_gt/general_education_ground_truth.json")
    return items


COOP_PLANS = [
    ("BIT", "BIT/BIT_academic_plan_coop.json"),
    ("DSBA", "DSBA/DSBA_academic_plan_coop.json"),
    ("IT", "IT/IT_academic_plan_coop.json"),
]


def _build_v2(add) -> list[dict]:
    """ชุดที่ 2: สำนวน/รูปแบบคำถามที่หลากหลายขึ้น — ใช้ RNG แยก ข้อชุดแรกจึงไม่เปลี่ยน."""
    rng = random.Random(SEED + 1)
    for prog, rel in PLANS:
        courses = json.loads((GT / rel).read_text(encoding="utf-8"))["courses"]
        src = f"teacher_gt/{rel}"
        named = [c for c in courses if _clean(c["name_th"]) and _codes(c["code"])
                 and (prog, _codes(c["code"])[0]) not in GT_KNOWN_ERRORS]
        slotted = [c for c in named if _fixed_slot(c)]
        by_code = {_codes(c["code"])[0]: c for c in named}

        for c in rng.sample(named, min(4, len(named))):
            en = _clean(c["name_en"]).title()
            if en:
                add(prog, "en_credit", f"{en} กี่หน่วยกิต", [_credit_alts(c["credits"])], _clean(c["credits"]), src)
        for c in rng.sample(named, min(4, len(named))):
            add(prog, "credit_para", f"หน่วยกิตของวิชา{_clean(c['name_th'])}เท่าไหร่",
                [_credit_alts(c["credits"])], _clean(c["credits"]), src)
        for c in rng.sample(slotted, min(4, len(slotted))):
            y, s = _fixed_slot(c)
            add(prog, "code_ys", f"วิชา {_codes(c['code'])[0]} เรียนปีไหนเทอมไหน",
                [[f"ปีที่ {y}"], [f"ภาคการศึกษาที่ {s}"]], f"ปี {y} ภาค {s}", src)
        long_names = [c for c in slotted if len(_clean(c["name_th"])) >= 14]
        for c in rng.sample(long_names, min(3, len(long_names))):
            name = _clean(c["name_th"])
            cut = rng.randrange(4, len(name) - 3)
            typo = name[:cut] + name[cut + 1:]  # พิมพ์ตกหนึ่งตัว
            y, s = _fixed_slot(c)
            add(prog, "typo_ys", f"วิชา{typo}เรียนปีไหนเทอมไหน",
                [[f"ปีที่ {y}"], [f"ภาคการศึกษาที่ {s}"]], f"{name}: ปี {y} ภาค {s}", src)
        # ไม่ผ่านวิชา X แล้วจะลงวิชาไหนไม่ได้ (prerequisite ทิศกลับ ภายในหลักสูตรเดียวกัน)
        deps: dict[str, list[str]] = {}
        for c in named:
            for p in _codes(c.get("prerequisite")):
                deps.setdefault(p, []).append(_codes(c["code"])[0])
        for p in rng.sample(sorted(k for k in deps if k in by_code), min(3, len(deps))):
            add(prog, "dependents", f"ถ้าสอบไม่ผ่านวิชา{_clean(by_code[p]['name_th'])} จะลงวิชาไหนต่อไม่ได้",
                [[d] for d in deps[p]], ", ".join(deps[p]), src)

    for prog, rel in COOP_PLANS:
        courses = json.loads((GT / rel).read_text(encoding="utf-8"))["courses"]
        coop = [c for c in courses if "สหกิจ" in _clean(c["name_th"]) and _fixed_slot(c)]
        for c in coop[:1]:
            y, s = _fixed_slot(c)
            add(prog, "coop", "แผนสหกิจศึกษาต้องไปสหกิจปีไหน เทอมไหน และกี่หน่วยกิต",
                [[f"ปีที่ {y}"], [f"ภาคการศึกษาที่ {s}"], [_clean(c["credits"]).split("(")[0]]],
                f"ปี {y} ภาค {s} {_clean(c['credits'])}", f"teacher_gt/{rel}")

    # ข้อบังคับปริญญาตรี (ค่าจากเล่ม AIT 2566 ภาคผนวก — ไม่ได้มาจากเฉลยอาจารย์)
    reg = "มคอ.2 AIT 2566 ภาคผนวก ข้อบังคับปริญญาตรี"
    for prog, q, must in [
        ("IT", "ลงเรียนได้สูงสุดเทอมละกี่หน่วยกิต", [["22"]]),
        ("DSBA", "ภาคฤดูร้อนลงทะเบียนได้กี่หน่วยกิต", [["9"]]),
        ("BIT", "ปีสุดท้ายลงทะเบียนเกิน 22 หน่วยกิตได้ไหม ได้สูงสุดเท่าไร", [["27"]]),
        ("AIT", "เกรดเฉลี่ยต่ำกว่าเท่าไหร่ถึงโดนรีไทร์", [["1.00"]]),
        ("IT", "เกรดเฉลี่ยสะสมต่ำกว่า 2.00 จะเป็นอย่างไร", [["ภาคทัณฑ์"]]),
        ("DSBA", "เกียรตินิยมอันดับสองต้องได้เกรดเท่าไหร่", [["3.25"]]),
        ("BIT", "ถ้าเคยได้ F จะได้เกียรตินิยมไหม", [["F"]]),
        ("AIT", "เกรด C+ ได้กี่แต้ม", [["2.50"]]),
        ("IT", "ถ้าทุจริตในการสอบจะโดนอะไร", [["พักการเรียน"]]),
    ]:
        add(prog, "reg2", q, must, " ".join(m[0] for m in must), reg)
    return []


def main() -> None:
    items = build()
    out = ROOT / "docs" / "eval_bank.json"
    out.write_text(json.dumps({
        "_meta": "คลังคำถามจากเฉลยอาจารย์ (data/teacher_gt) สร้างด้วย python -m katrag.eval.build_bank",
        "items": items,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(items)} ข้อ -> {out}")


if __name__ == "__main__":
    main()
