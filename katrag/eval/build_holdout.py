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


def _year_alts(y: int, s: int | None = None) -> list[list[str]]:
    """ปี/ภาค เขียนได้หลายแบบ (ตอบจากตาราง "ปีที่ 3 ภาคการศึกษาที่ 1" หรือ LLM "ปี 3 เทอม 1")."""
    out = [[f"ปีที่ {y}", f"ปี {y}"]]
    if s is not None:
        out.append([f"ภาคการศึกษาที่ {s}", f"เทอม {s}", f"ภาค {s}", f"ภาคเรียนที่ {s}"])
    return out


def build_round5(seed_offset: int = 4000, ids: tuple[str, str] = ("WH", "WC"), alt: bool = False) -> list[dict]:
    """ชุดที่ 5: ข้อยากและท้าทายล้วน (หลายขั้น, เงื่อนไข, เทียบข้ามแผน/หลักสูตร, สถานการณ์ตามระเบียบ).

    ค่าเฉลยคำนวณจากไฟล์เฉลยอาจารย์ (แผนการเรียนและกฎระเบียบ) ไม่ได้อ่านจากระบบ
    """
    rng = random.Random(SEED + seed_offset)

    def ph(a: str, b: str) -> str:  # สำนวนชุดที่สอง (รอบ 6) ใช้ b
        return b if alt else a
    seen = set()
    for name in ("eval_bank.json", "eval_holdout.json", "eval_holdout2.json", "eval_holdout3.json",
                 "eval_holdout4.json") + (("eval_holdout5.json",) if alt else ()):
        p = ROOT / "docs" / name
        if p.exists():
            seen |= {i["question"] for i in json.loads(p.read_text(encoding="utf-8"))["items"]}
    items: list[dict] = []

    def add(level, prog, question, must, expected, source):
        if question in seen:
            return
        seen.add(question)
        n = sum(1 for i in items if i["level"] == level) + 1
        items.append({"id": f"{ids[0] if level == 'ยาก' else ids[1]}{n}", "level": level, "program": prog,
                      "question": question, "must": must, "expected": expected, "source": source})

    plan_courses: dict[str, dict[str, dict]] = {}
    for prog, rel in PLANS:
        courses = json.loads((GT / rel).read_text(encoding="utf-8"))["courses"]
        src = f"teacher_gt/{rel}"
        named = [c for c in courses if _clean(c["name_th"]) and _codes(c["code"])
                 and (prog, _codes(c["code"])[0]) not in GT_KNOWN_ERRORS]
        by_code = {_codes(c["code"])[0]: c for c in named}
        plan_courses[prog] = by_code
        slotted = [c for c in named if _fixed_slot(c)]
        pre = {_codes(c["code"])[0]: [p for p in _codes(c.get("prerequisite")) if p in by_code] for c in named}

        # ยาก 1: วิชาบังคับก่อนสองชั้น (C <- B <- A)
        chains = [(c, b, a) for c in named for b in pre[_codes(c["code"])[0]] for a in pre.get(b, [])]
        for c, b, a in rng.sample(chains, min(1, len(chains))):
            add("ยาก", prog, ph(f"ถ้าจะเรียนวิชา{_clean(c['name_th'])} ต้องผ่านวิชาอะไรมาก่อนบ้าง นับรวมวิชาที่เป็นพื้นฐานของวิชานั้นด้วย",
               f"อยากเรียน{_clean(c['name_th'])} ต้องเรียนอะไรให้ผ่านก่อนทั้งหมด รวมถึงวิชาที่ต้องผ่านก่อนวิชาเหล่านั้นด้วย"),
                [[b], [a]], f"{b} และก่อนหน้านั้น {a}", src)
        # ยาก 2: ลงก่อนปีในแผนได้ไหม — ต้องบอกปีตามแผนและวิชาบังคับก่อน
        early = [c for c in slotted if _fixed_slot(c)[0] >= 2 and pre[_codes(c["code"])[0]]]
        for c in rng.sample(early, min(1, len(early))):
            y, s = _fixed_slot(c)
            p = pre[_codes(c["code"])[0]][0]
            add("ยาก", prog, ph(f"ขอลงวิชา{_clean(c['name_th'])}ตั้งแต่ปี {y - 1} ได้หรือเปล่า",
               f"ตอนอยู่ปี {y - 1} จะลงเรียน{_clean(c['name_th'])}เลยได้มั้ย"),
                [[p]] + _year_alts(y), f"ตามแผนเรียนปี {y} ภาค {s} ต้องผ่าน {p} ก่อน", src)
        # ยาก 3: ตกวิชาพื้นฐานแล้วกระทบวิชาไหน และวิชานั้นอยู่ปีไหน
        deps: dict[str, list[str]] = {}
        for code, ps in pre.items():
            for p in ps:
                if _fixed_slot(by_code[code]):
                    deps.setdefault(p, []).append(code)
        for p in rng.sample(sorted(deps), min(1, len(deps))):
            d = deps[p][0]
            y, s = _fixed_slot(by_code[d])
            add("ยาก", prog, ph(f"ถ้าสอบตกวิชา{_clean(by_code[p]['name_th'])} จะกระทบวิชาไหนบ้าง และวิชาเหล่านั้นเรียนปีไหน",
               f"ได้ F วิชา{_clean(by_code[p]['name_th'])} แล้ววิชาไหนจะลงต่อไม่ได้ วิชาพวกนั้นอยู่ปีไหน"),
                [[x] for x in deps[p]] + _year_alts(y), ", ".join(deps[p]) + f" ({d} ปี {y} ภาค {s})", src)
        # ยาก 4: สองวิชาเรียนปีเดียวกันไหม
        a, b = rng.sample(slotted, 2)
        (ya, sa), (yb, sb) = _fixed_slot(a), _fixed_slot(b)
        add("ยาก", prog, ph(f"วิชา{_clean(a['name_th'])}กับวิชา{_clean(b['name_th'])} เรียนปีเดียวกันไหม",
               f"{_clean(a['name_th'])} กับ {_clean(b['name_th'])} วิชาไหนเรียนก่อน"),
            _year_alts(ya) + _year_alts(yb),
            f"{_codes(a['code'])[0]} ปี {ya} ภาค {sa}, {_codes(b['code'])[0]} ปี {yb} ภาค {sb}", src)

    # ยาก 5: แผนสหกิจกับแผนปกติต่างกันตรงไหน (ภาคสุดท้ายที่ต่าง)
    def slots(cs):
        out: dict[tuple[int, int], set[str]] = {}
        for c in cs:
            if _fixed_slot(c) and _codes(c["code"]):
                out.setdefault(_fixed_slot(c), set()).add(_codes(c["code"])[0])
        return out

    for prog, rel in COOP_PLANS:
        normal_rel = dict(PLANS)[prog]
        sc = slots(json.loads((GT / rel).read_text(encoding="utf-8"))["courses"])
        sn = slots(json.loads((GT / normal_rel).read_text(encoding="utf-8"))["courses"])
        diff = [k for k in sorted(set(sc) | set(sn)) if sc.get(k, set()) != sn.get(k, set())]
        if diff:
            y, s = diff[-1]
            only = sorted(sc.get((y, s), set()) ^ sn.get((y, s), set()))[:4]
            add("ยาก", prog, ph(f"ปี {y} เทอม {s} ถ้าเลือกแผนสหกิจ จะเรียนต่างจากแผนปกติอย่างไร",
               f"เลือกทำสหกิจศึกษาแล้ว ปี {y} ภาค {s} เรียนไม่เหมือนแผนปกติตรงไหน"),
                [[x] for x in only], ", ".join(only), f"teacher_gt/{rel} เทียบ {normal_rel}")

    # ท้าทาย: สถานการณ์ตามเกณฑ์ (ค่าจาก rules_ground_truth.json)
    rules = json.loads((GT / "rules_ground_truth.json").read_text(encoding="utf-8"))["programs"]
    total = {p: next(v["value"] for c in cats if c["category"] == "เกณฑ์การสำเร็จการศึกษา"
                     for v in c["values"] if "หน่วยกิต" in v["label"]) for p, cats in rules.items()}
    rsrc = "teacher_gt/rules_ground_truth.json"
    for p in ("IT", "DSBA", "AIT"):
        t = int(total[p])
        add("ท้าทาย", p, ph(f"นักศึกษา {p} เก็บได้ {t - 6} หน่วยกิต เกรดเฉลี่ยสะสม 2.45 จบได้หรือยัง ถ้ายังต้องเก็บเพิ่มอีกกี่หน่วยกิต",
               f"ตอนนี้มี {t - 6} หน่วยกิต GPA 2.45 อยู่หลักสูตร {p} ขาดอีกเท่าไหร่ถึงจะจบ"),
            [[str(t)], ["6 หน่วยกิต", "อีก 6", "ขาด 6"]], f"ยังไม่จบ ต้องครบ {t} ขาด 6", rsrc)
        add("ท้าทาย", p, ph(f"ถ้าเกรดเฉลี่ยสะสม 1.95 แต่เรียนครบ {t} หน่วยกิตแล้ว จะจบการศึกษาได้ไหม",
               f"เรียนครบ {t} หน่วยกิตแล้วแต่ GPA 1.95 สำเร็จการศึกษาได้หรือเปล่า"),
            [["2.00"]], "ไม่ได้ ต้องได้ไม่ต่ำกว่า 2.00", rsrc)
    for p1, p2 in (("IT", "DSBA"), ("AIT", "IT"), ("DSBA", "AIT")):
        add("ท้าทาย", p1, ph(f"หลักสูตร {p1} กับ {p2} ต้องเรียนหน่วยกิตรวมต่างกันกี่หน่วยกิต",
               f"{p1} หรือ {p2} หลักสูตรไหนต้องเรียนหน่วยกิตมากกว่ากัน"),
            [[total[p1]], [total[p2]]], f"{p1} {total[p1]}, {p2} {total[p2]}", rsrc)
    for p, q, must, exp in ([
        ("AIT", "GPA ตอนจบ 3.55 ได้เกียรตินิยมไหม อันดับเท่าไร", [["อันดับ 1", "อันดับหนึ่ง"], ["3.50"]], "อันดับ 1 (>= 3.50)"),
        ("IT", "เกรดเฉลี่ย 3.40 จะได้เกียรตินิยมอันดับสองหรือเปล่า", [["อันดับ 2", "อันดับสอง"], ["3.25"]], "อันดับ 2 (>= 3.25)"),
        ("DSBA", "ภาคเรียนปกติลง 8 หน่วยกิตได้ไหม", [["9"]], "ไม่ได้ ขั้นต่ำ 9"),
        ("AIT", "ซัมเมอร์ลงได้มากสุดกี่หน่วยกิต ถ้าลงฝึกงานด้วยจะลงวิชาอื่นได้ไหม", [["9"]], "ไม่เกิน 9 ฝึกงานแล้วลงวิชาอื่นไม่ได้"),
    ] if alt else [
        ("IT", "จบด้วยเกรดเฉลี่ย 3.60 จะได้เกียรตินิยมอันดับไหน", [["อันดับ 1", "อันดับหนึ่ง"], ["3.50"]], "อันดับ 1 (>= 3.50)"),
        ("DSBA", "เกรดเฉลี่ยตอนจบ 3.30 ได้เกียรตินิยมไหม", [["อันดับ 2", "อันดับสอง"], ["3.25"]], "อันดับ 2 (>= 3.25)"),
        ("AIT", "เทอมหน้าอยากลง 25 หน่วยกิตในภาคปกติ ทำได้ไหม", [["22"]], "ไม่ได้ ภาคปกติสูงสุด 22"),
        ("IT", "ภาคปกติต้องลงทะเบียนอย่างน้อยกี่หน่วยกิต และมากสุดได้เท่าไร", [["9"], ["22"]], "9-22"),
    ]):
        add("ท้าทาย", p, q, must, exp, rsrc)

    # ท้าทาย: วิชาเดียวกัน ต่างหลักสูตร เรียนต่างปี/ภาค
    shared = [(code, p1, p2) for p1 in plan_courses for p2 in plan_courses if p1 < p2
              and {p1, p2} != {"AIT", "BIT"}
              for code in plan_courses[p1] if code in plan_courses[p2]
              and _fixed_slot(plan_courses[p1][code]) and _fixed_slot(plan_courses[p2][code])
              and _fixed_slot(plan_courses[p1][code]) != _fixed_slot(plan_courses[p2][code])]
    for code, p1, p2 in rng.sample(shared, min(3, len(shared))):
        c1, c2 = plan_courses[p1][code], plan_courses[p2][code]
        (y1, s1), (y2, s2) = _fixed_slot(c1), _fixed_slot(c2)
        add("ท้าทาย", p1, ph(f"วิชา{_clean(c1['name_th'])} ของหลักสูตร {p1} กับ {p2} เรียนปีและเทอมเดียวกันหรือไม่",
               f"{_clean(c1['name_th'])} ใน {p1} และ {p2} อยู่ปีไหนเทอมไหนบ้าง"),
            [[p1], [p2]] + _year_alts(y1) + _year_alts(y2),
            f"{p1} ปี {y1} ภาค {s1}, {p2} ปี {y2} ภาค {s2}", "teacher_gt (แผนของทั้งสองหลักสูตร)")
    return items


if __name__ == "__main__" and __import__("sys").argv[1:] == ["--round5"]:
    items5 = build_round5()
    out5 = ROOT / "docs" / "eval_holdout5.json"
    out5.write_text(json.dumps({"_meta": "ชุดที่ไม่เคยเห็น รอบ 5 (ข้อยากและท้าทายล้วน)", "items": items5},
                               ensure_ascii=False, indent=1), encoding="utf-8")
    from collections import Counter
    print(len(items5), "ข้อ", dict(Counter(i["level"] for i in items5)), "->", out5)


if __name__ == "__main__" and __import__("sys").argv[1:] == ["--round6"]:
    items6 = build_round5(5000, ("ZH", "ZC"), alt=True)
    out6 = ROOT / "docs" / "eval_holdout6.json"
    out6.write_text(json.dumps({"_meta": "ชุดที่ไม่เคยเห็น รอบ 6 (ข้อยากและท้าทาย seed ใหม่ สำนวนใหม่)", "items": items6},
                               ensure_ascii=False, indent=1), encoding="utf-8")
    from collections import Counter
    print(len(items6), "ข้อ", dict(Counter(i["level"] for i in items6)), "->", out6)
