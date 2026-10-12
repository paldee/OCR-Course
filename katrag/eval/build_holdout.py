"""ชุดทดสอบใหม่ที่ระบบไม่เคยใช้ปรับแก้ (held-out) → docs/eval_holdout.json.

สร้างหลังจากปรับระบบเสร็จแล้ว เพื่อวัดว่าระบบนิ่งจริง: สุ่มด้วย seed ใหม่จากเฉลยอาจารย์
(ตัดคำถามที่ซ้ำกับคลังเดิมออก) และข้อท้าทายที่เขียนเพิ่มโดยค่าเฉลยตรวจกับหน้าในเล่ม
จัดระดับ ง่าย / ปานกลาง / ยาก / ท้าทาย ตามรูปแบบชุดทดสอบเดิม

Usage:
    python -m katrag.eval.build_holdout
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from katrag.eval.build_bank import (
    COOP_PLANS, GT, GT_KNOWN_ERRORS, PLANS, _clean, _codes, _credit_alts, _fixed_slot,
)

ROOT = Path(__file__).resolve().parents[2]
SEED = 20261013


def build() -> list[dict]:
    rng = random.Random(SEED)
    seen = {i["question"] for i in json.loads((ROOT / "docs" / "eval_bank.json").read_text(encoding="utf-8"))["items"]}
    items: list[dict] = []

    def add(level, prog, question, must, expected, source):
        if question in seen:
            return
        seen.add(question)
        n = sum(1 for i in items if i["level"] == level) + 1
        code = {"ง่าย": "HE", "ปานกลาง": "HM", "ยาก": "HH", "ท้าทาย": "HC"}[level]
        items.append({"id": f"{code}{n}", "level": level, "program": prog, "question": question,
                      "must": must, "expected": expected, "source": source})

    for prog, rel in PLANS:
        courses = json.loads((GT / rel).read_text(encoding="utf-8"))["courses"]
        src = f"teacher_gt/{rel}"
        named = [c for c in courses if _clean(c["name_th"]) and _codes(c["code"])
                 and (prog, _codes(c["code"])[0]) not in GT_KNOWN_ERRORS]
        slotted = [c for c in named if _fixed_slot(c)]
        by_code = {_codes(c["code"])[0]: c for c in named}
        # ง่าย: ข้อเท็จจริงของวิชาเดียว
        for c in rng.sample(named, 3):
            add("ง่าย", prog, f"วิชา{_clean(c['name_th'])}กี่หน่วยกิต", [_credit_alts(c["credits"])], _clean(c["credits"]), src)
        for c in rng.sample(slotted, 2):
            y, s = _fixed_slot(c)
            add("ง่าย", prog, f"{_clean(c['name_th'])} อยู่ปีไหน ภาคไหน",
                [[f"ปีที่ {y}"], [f"ภาคการศึกษาที่ {s}"]], f"ปี {y} ภาค {s}", src)
        # ปานกลาง: ชื่ออังกฤษ / วิชาบังคับก่อน
        for c in rng.sample(slotted, 2):
            en = _clean(c["name_en"]).lower()
            y, s = _fixed_slot(c)
            if en:
                add("ปานกลาง", prog, f"{en} ต้องเรียนตอนปีไหน",
                    [[f"ปีที่ {y}"], [f"ภาคการศึกษาที่ {s}"]], f"ปี {y} ภาค {s}", src)
        with_pre = [c for c in slotted if _codes(c.get("prerequisite"))]
        for c in rng.sample(with_pre, min(2, len(with_pre))):
            add("ปานกลาง", prog, f"ก่อนจะลงวิชา{_clean(c['name_th'])} ต้องเรียนวิชาอะไรมาก่อน",
                [[p] for p in _codes(c["prerequisite"])], _clean(c["prerequisite"]), src)
        # ยาก: วิชาบังคับก่อน + ปี/ภาคของวิชานั้น (สองจุด)
        chain = [c for c in with_pre if all(p in by_code and _fixed_slot(by_code[p]) for p in _codes(c["prerequisite"]))]
        for c in rng.sample(chain, min(2, len(chain))):
            p = _codes(c["prerequisite"])[0]
            py, ps = _fixed_slot(by_code[p])
            add("ยาก", prog, f"วิชา{_clean(c['name_th'])}ต้องผ่านวิชาอะไรก่อน และวิชานั้นเรียนปีไหนเทอมไหน",
                [[p], [f"ปีที่ {py}"], [f"ภาคการศึกษาที่ {ps}"]], f"{p} ปี {py} ภาค {ps}", src)

    for prog, rel in COOP_PLANS:
        courses = json.loads((GT / rel).read_text(encoding="utf-8"))["courses"]
        slot = {}
        for c in courses:
            if _fixed_slot(c) and c.get("type") == "บังคับ" and _codes(c["code"]):
                slot.setdefault(_fixed_slot(c), []).append(_codes(c["code"])[0])
        y, s = max(slot)
        add("ยาก", prog, f"แผนสหกิจศึกษา ปี {y} เทอม {s} เรียนวิชาอะไร",
            [[x] for x in slot[(y, s)]], ", ".join(slot[(y, s)]), f"teacher_gt/{rel}")

    # ท้าทาย: ข้ามเวอร์ชัน/ข้ามหลักสูตร/บัณฑิตศึกษา/ระเบียบ — ค่าเฉลยตรวจกับหน้าในเล่ม
    for prog, q, must, exp, src in [
        ("IT", "IT 2560 กับ IT 2565 หน่วยกิตรวมตลอดหลักสูตรต่างกันกี่หน่วยกิต", [["130"], ["129"]],
         "130 กับ 129 ต่างกัน 1", "IT2560 หน้า 18 / IT2565 หน้า 6"),
        ("AIT", "AIT 2566 กับ DSBA 2565 หลักสูตรไหนต้องเรียนหน่วยกิตรวมมากกว่า", [["120"], ["132"]],
         "AIT 120, DSBA 132", "rule table (หน้าโครงสร้างหลักสูตร)"),
        ("IT", "ปริญญาเอก IT 2566 แบบ 1.2 ต้องเรียนกี่หน่วยกิต", [["72"]], "72", "PhD IT 2566 หน้า 25"),
        ("AITBA", "ปริญญาโท AITBA 2564 แผน ข ต้องทำการค้นคว้าอิสระกี่หน่วยกิต", [["6"]], "6", "AITBA 2564 หน้า 16"),
        ("IT", "ปริญญาเอก IT 2561 กับ 2566 แบบ 1.1 ต้องเรียนหน่วยกิตต่างกันไหม", [["60"], ["57"]],
         "2561: 60, 2566: 57", "PhD IT 2561 หน้า 16 / 2566 หน้า 25"),
        ("DSBA", "นักศึกษาปีสุดท้ายลงทะเบียนได้มากที่สุดกี่หน่วยกิต", [["27"]], "27", "AIT 2566 ภาคผนวก หน้า 82"),
        ("BIT", "เกรด D+ มีค่าเท่าไหร่", [["1.50"]], "1.50", "AIT 2566 ภาคผนวก หน้า 85"),
        ("IT", "ทุจริตในการสอบสองครั้งจะเป็นอย่างไร", [["พ้นสภาพ"]], "พ้นสภาพ (ข้อ 43.7)", "AIT 2566 ภาคผนวก หน้า 94"),
        ("DSBA", "อาจารย์ภัทรชัย ลลิตโรจน์วงศ์ จบปริญญาเอกจากที่ไหน", [["Illinois"]],
         "Ph.D. Illinois Institute of Technology, 2542", "DSBA 2565 หน้า 51"),
    ]:
        add("ท้าทาย", prog, q, must, exp, src)
    return items


def main() -> None:
    items = build()
    out = ROOT / "docs" / "eval_holdout.json"
    out.write_text(json.dumps({"_meta": __doc__.splitlines()[0], "items": items}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    from collections import Counter
    print(len(items), "ข้อ", dict(Counter(i["level"] for i in items)), "->", out)


if __name__ == "__main__" and not __import__("sys").argv[1:]:
    main()


def build_round2() -> list[dict]:
    """ชุดที่ 2 (ตรวจครั้งสุดท้าย): seed ใหม่และสำนวนที่ไม่เคยใช้ปรับระบบ."""
    rng = random.Random(SEED + 1000)
    seen = {i["question"] for i in json.loads((ROOT / "docs" / "eval_bank.json").read_text(encoding="utf-8"))["items"]}
    seen |= {i["question"] for i in json.loads((ROOT / "docs" / "eval_holdout.json").read_text(encoding="utf-8"))["items"]}
    items: list[dict] = []

    def add(level, prog, question, must, expected, source):
        if question in seen:
            return
        seen.add(question)
        n = sum(1 for i in items if i["level"] == level) + 1
        code = {"ง่าย": "FE", "ปานกลาง": "FM", "ยาก": "FH", "ท้าทาย": "FC"}[level]
        items.append({"id": f"{code}{n}", "level": level, "program": prog, "question": question,
                      "must": must, "expected": expected, "source": source})

    for prog, rel in PLANS:
        courses = json.loads((GT / rel).read_text(encoding="utf-8"))["courses"]
        src = f"teacher_gt/{rel}"
        named = [c for c in courses if _clean(c["name_th"]) and _codes(c["code"])
                 and (prog, _codes(c["code"])[0]) not in GT_KNOWN_ERRORS]
        slotted = [c for c in named if _fixed_slot(c)]
        by_code = {_codes(c["code"])[0]: c for c in named}
        for c in rng.sample(named, 3):
            add("ง่าย", prog, f"{_clean(c['name_th'])} ได้กี่หน่วยกิต", [_credit_alts(c["credits"])], _clean(c["credits"]), src)
        for c in rng.sample(slotted, 2):
            y, s = _fixed_slot(c)
            add("ง่าย", prog, f"วิชา{_clean(c['name_th'])} เปิดสอนปีอะไร เทอมอะไร",
                [[f"ปีที่ {y}"], [f"ภาคการศึกษาที่ {s}"]], f"ปี {y} ภาค {s}", src)
        with_pre = [c for c in named if _codes(c.get("prerequisite"))]
        for c in rng.sample(with_pre, min(2, len(with_pre))):
            add("ปานกลาง", prog, f"วิชา{_clean(c['name_th'])}มีวิชาบังคับก่อนไหม",
                [[p] for p in _codes(c["prerequisite"])], _clean(c["prerequisite"]), src)
        for c in rng.sample(with_pre, min(1, len(with_pre))):
            add("ปานกลาง", prog, f"ลงวิชา{_clean(c['name_th'])}ต้องผ่านตัวไหน",
                [[p] for p in _codes(c["prerequisite"])], _clean(c["prerequisite"]), src)
        slots: dict = {}
        for c in slotted:
            if c.get("type") == "บังคับ":
                slots.setdefault(_fixed_slot(c), []).append(_codes(c["code"])[0])
        for (y, s) in rng.sample(sorted(slots), 2):
            add("ยาก", prog, f"ปี {y} ภาค {s} มีวิชาบังคับอะไรบ้าง", [[x] for x in slots[(y, s)]],
                ", ".join(slots[(y, s)]), src)
        deps: dict = {}
        for c in named:
            for p in _codes(c.get("prerequisite")):
                deps.setdefault(p, []).append(_codes(c["code"])[0])
        cand = sorted(k for k in deps if k in by_code)
        for p in rng.sample(cand, min(1, len(cand))):
            add("ยาก", prog, f"วิชา{_clean(by_code[p]['name_th'])}เป็นวิชาบังคับก่อนของวิชาอะไรบ้าง",
                [[d] for d in deps[p]], ", ".join(deps[p]), src)
    for prog, q, must, exp, src in [
        ("DSBA", "ถ้าเกรดเฉลี่ยต่ำกว่า 2.00 จะเกิดอะไรขึ้น", [["ภาคทัณฑ์"]], "ถูกภาคทัณฑ์ (ข้อ 31)", "AIT 2566 ภาคผนวก หน้า 87"),
        ("AIT", "ต้องได้เกรดเฉลี่ยเท่าไรถึงได้เกียรตินิยมอันดับหนึ่ง", [["3.50"]], "3.50", "AIT 2566 ภาคผนวก หน้า 89"),
        ("IT", "ปริญญาโท IT 2563 มีแผนการศึกษาแบบไหนบ้าง", [["แผน ก"], ["แผน ข"]], "แผน ก แบบ ก1, ก2 และแผน ข", "M.IT 2563 หน้า 16"),
        ("AITBA", "ปริญญาโท AITBA 2569 แผน 1.2 ต้องทำวิทยานิพนธ์กี่หน่วยกิต", [["18"]], "18", "AITBA 2569 หน้า 15"),
        ("BIT", "BIT 2560 กับ BIT 2565 หน่วยกิตรวมต่างกันไหม", [["126"]], "เท่ากันที่ 126", "rule table"),
        ("IT", "อาจารย์ศุภมิตร จิตตะยโศธร มีวุฒิปริญญาโทจากที่ไหน", [["2525"]], "สถาบันเทคโนโลยีแห่งเอเชีย (AIT), 2525", "IT 2565 หน้า 56"),
    ]:
        add("ท้าทาย", prog, q, must, exp, src)
    return items


if __name__ == "__main__" and __import__("sys").argv[1:] == ["--round2"]:
    items2 = build_round2()
    out2 = ROOT / "docs" / "eval_holdout2.json"
    out2.write_text(json.dumps({"_meta": "ชุดตรวจครั้งสุดท้าย (seed และสำนวนใหม่)", "items": items2},
                               ensure_ascii=False, indent=1), encoding="utf-8")
    from collections import Counter
    print(len(items2), "ข้อ", dict(Counter(i["level"] for i in items2)), "->", out2)


#: วิชาศึกษาทั่วไปที่หน่วยกิตในเฉลยอาจารย์ไม่ตรงกับเล่ม (ตรวจกับภาพหน้าเล่มแล้ว)
GENED_BOOK_DIFF = {
    # รหัส 90642065 ในเฉลยไม่ปรากฏในเล่มใดเลย; เล่ม AIT 2566 หน้า 190 มีวิชาชื่อเดียวกันรหัส 90642176
    "90642065": ("3(2-2-5)", "รหัส 90642065 ในเฉลยไม่มีในเล่มใด เล่ม AIT 2566 หน้า 190 มีรักบี้ฟุตบอล 90642176 = 3 (2-2-5)"),
}


def build_round3(seed_offset: int = 2000, prefix: str = "U", earlier: tuple = ()) -> list[dict]:
    """ชุดที่ 3 (และ 4): seed ใหม่และสำนวนที่ยังไม่เคยใช้ (สลับลำดับปี/เทอม, ภาษาอังกฤษทั้งประโยค, ศึกษาทั่วไปชุดใหม่)."""
    rng = random.Random(SEED + seed_offset)
    seen = set()
    for name in ("eval_bank.json", "eval_holdout.json", "eval_holdout2.json") + earlier:
        seen |= {i["question"] for i in json.loads((ROOT / "docs" / name).read_text(encoding="utf-8"))["items"]}
    items: list[dict] = []

    def add(level, prog, question, must, expected, source):
        if question in seen:
            return
        seen.add(question)
        n = sum(1 for i in items if i["level"] == level) + 1
        code = prefix + {"ง่าย": "E", "ปานกลาง": "M", "ยาก": "H", "ท้าทาย": "C"}[level]
        items.append({"id": f"{code}{n}", "level": level, "program": prog, "question": question,
                      "must": must, "expected": expected, "source": source})

    for prog, rel in PLANS:
        courses = json.loads((GT / rel).read_text(encoding="utf-8"))["courses"]
        src = f"teacher_gt/{rel}"
        named = [c for c in courses if _clean(c["name_th"]) and _codes(c["code"])
                 and (prog, _codes(c["code"])[0]) not in GT_KNOWN_ERRORS]
        slotted = [c for c in named if _fixed_slot(c)]
        for c in rng.sample(named, 2):
            add("ง่าย", prog, f"อยากทราบหน่วยกิตของวิชา{_clean(c['name_th'])}", [_credit_alts(c["credits"])],
                _clean(c["credits"]), src)
        for c in rng.sample(slotted, 2):
            y, s = _fixed_slot(c)
            add("ง่าย", prog, f"{_clean(c['name_th'])} เรียนปีอะไร",
                [[f"ปีที่ {y}"]], f"ปี {y} ภาค {s}", src)
        for c in rng.sample([c for c in named if _clean(c["name_en"])], 2):
            add("ปานกลาง", prog, f"How many credits is {_clean(c['name_en']).title()}?", [_credit_alts(c["credits"])],
                _clean(c["credits"]), src)
        for c in rng.sample(slotted, 1):
            y, s = _fixed_slot(c)
            add("ปานกลาง", prog, f"Which year and semester is {_clean(c['name_en']).title()} taught?",
                [[f"ปีที่ {y}"], [f"ภาคการศึกษาที่ {s}"]], f"ปี {y} ภาค {s}", src)
        with_pre = [c for c in named if _codes(c.get("prerequisite"))]
        for c in rng.sample(with_pre, min(2, len(with_pre))):
            add("ปานกลาง", prog, f"{_clean(c['name_th'])} มี prerequisite อะไร",
                [[p] for p in _codes(c["prerequisite"])], _clean(c["prerequisite"]), src)
        slots: dict = {}
        for c in slotted:
            if c.get("type") == "บังคับ":
                slots.setdefault(_fixed_slot(c), []).append(_codes(c["code"])[0])
        for (y, s) in rng.sample(sorted(slots), 2):
            add("ยาก", prog, f"เทอม {s} ปี {y} ต้องลงวิชาบังคับอะไรบ้าง", [[x] for x in slots[(y, s)]],
                ", ".join(slots[(y, s)]), src)

    gen = json.loads((GT / "general_education_ground_truth.json").read_text(encoding="utf-8"))["courses"]
    gen = [c for c in gen if _clean(c["name_th"]) and _codes(c["code"])]
    for i, c in enumerate(rng.sample(gen, 8)):
        code = _codes(c["code"])[0]
        credits, src = _clean(c["credits"]), "teacher_gt/general_education_ground_truth.json"
        if code in GENED_BOOK_DIFF:  # เฉลยไม่ตรงกับเล่ม — ใช้ค่าตามเล่มและบันทึกเหตุผล
            credits, note = GENED_BOOK_DIFF[code]
            src = f"{note} (เฉลยอาจารย์เขียน {_clean(c['credits'])})"
        add("ง่าย", PLANS[i % 4][0], f"วิชาศึกษาทั่วไป {_clean(c['name_th'])} กี่หน่วยกิต", [_credit_alts(credits)],
            credits, src)

    for prog, q, must, exp, src in [
        ("IT", "ลงทะเบียนต่ำสุดเทอมละกี่หน่วยกิต", [["9"]], "9", "AIT 2566 ภาคผนวก หน้า 82"),
        ("DSBA", "GPA เท่าไหร่ถึงจะจบได้", [["2.00"]], "2.00", "AIT 2566 ภาคผนวก หน้า 88"),
        ("BIT", "อยากได้เกียรตินิยมอันดับ 1 ต้องได้เกรดเฉลี่ยอย่างน้อยเท่าไหร่", [["3.50"]], "3.50", "AIT 2566 ภาคผนวก หน้า 89"),
        ("AIT", "เกรด B ได้กี่แต้ม", [["3.00"]], "3.00", "AIT 2566 ภาคผนวก หน้า 85"),
        ("IT", "ปริญญาเอก IT 2566 แบบ 2.1 ต้องเรียนวิชาเลือกกี่หน่วยกิต", [["12"]], "12", "PhD IT 2566 หน้า 25"),
        ("IT", "ปริญญาโท IT 2568 แผน 2 แบบวิชาชีพ ต้องทำการค้นคว้าอิสระกี่หน่วยกิต", [["6"]], "6", "M.IT 2568 หน้า 12"),
        ("DSBA", "DSBA 2560 กับ 2565 หน่วยกิตรวมตลอดหลักสูตรต่างกันกี่หน่วยกิต", [["126"], ["132"]], "126 กับ 132", "DSBA2560 หน้า 17 / DSBA2565 หน้า 15"),
        ("AIT", "อาจารย์ศุภมิตร จิตตะยโศธร จบปริญญาเอกที่ไหน ปีอะไร", [["Queensland"], ["2531"]], "The University of Queensland, 2531", "AIT 2566 หน้า 42"),
    ]:
        add("ท้าทาย", prog, q, must, exp, src)
    return items


if __name__ == "__main__" and __import__("sys").argv[1:] == ["--round3"]:
    items3 = build_round3()
    out3 = ROOT / "docs" / "eval_holdout3.json"
    out3.write_text(json.dumps({"_meta": "ชุดที่ไม่เคยเห็น รอบ 3 (seed และสำนวนใหม่)", "items": items3},
                               ensure_ascii=False, indent=1), encoding="utf-8")
    from collections import Counter
    print(len(items3), "ข้อ", dict(Counter(i["level"] for i in items3)), "->", out3)


if __name__ == "__main__" and __import__("sys").argv[1:] == ["--round4"]:
    items4 = [i for i in build_round3(3000, "V", ("eval_holdout3.json",)) if not i["id"].startswith("VC")]
    out4 = ROOT / "docs" / "eval_holdout4.json"
    out4.write_text(json.dumps({"_meta": "ชุดที่ไม่เคยเห็น รอบ 4 (seed ใหม่, วิชาสุ่มใหม่ทั้งหมด)", "items": items4},
                               ensure_ascii=False, indent=1), encoding="utf-8")
    from collections import Counter
    print(len(items4), "ข้อ", dict(Counter(i["level"] for i in items4)), "->", out4)
