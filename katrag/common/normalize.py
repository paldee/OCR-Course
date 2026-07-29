"""Text normalization ที่ใช้ร่วมกันทั้ง ingestion, retrieval และ evaluation.

ข้อบังคับสำคัญ: ingestion และ query path ต้องใช้ normalizer ตัวเดียวกัน
มิฉะนั้น quote matching และ cache key จะไม่ตรงกัน (R10.10, R17.3, R18.1)

ชั้นของการ normalize (แยกหน้าที่ชัดเจน ห้ามรวมกัน):
1. `normalize_nfc`          — Unicode NFC เท่านั้น
2. `squeeze_whitespace`      — บีบช่องว่างซ้อนและตัดหัวท้าย
3. `canonical_mark_order`    — จัดลำดับ combining mark ไทยภายในพยางค์
4. `strip_marker_spaces`     — ลบ whitespace ที่คั่นระหว่างอักขระไทยกับ combining mark
5. `normalize_for_compare`   — ประกอบทั้งสี่ชั้นสำหรับการเทียบค่า

`canonical_mark_order` ทำงานบนสตริงล้วน (ไม่มี geometry) จึงเป็นการ normalize
"หลังได้ข้อความแล้ว" ส่วนการจัดลำดับที่ใช้ bbox/baseline อยู่ใน
`katrag/ingest/thai_reorder.py` ซึ่งเป็นงานคนละชั้นกัน
"""

from __future__ import annotations

import re
import unicodedata

# ── ช่วงอักขระไทยตาม requirements R3.1-R3.4 ──────────────────────────
THAI_BLOCK_START = "\u0e00"
THAI_BLOCK_END = "\u0e7f"

BELOW_VOWELS = frozenset("\u0e38\u0e39\u0e3a")  # ุ ู ฺ
ABOVE_VOWELS = frozenset("\u0e31\u0e34\u0e35\u0e36\u0e37\u0e47")  # ั ิ ี ึ ื ็
TONE_MARKS = frozenset("\u0e48\u0e49\u0e4a\u0e4b")  # ่ ้ ๊ ๋
OTHER_SIGNS = frozenset("\u0e4c\u0e4d\u0e4e")  # ์ ํ ๎
COMBINING_MARKS = BELOW_VOWELS | ABOVE_VOWELS | TONE_MARKS | OTHER_SIGNS

#: ลำดับ canonical ภายในหนึ่งพยางค์ (R3.1)
#: base -> below vowel -> above vowel -> tone -> other sign
_MARK_CLASS_ORDER: dict[str, int] = {}
for _ch in BELOW_VOWELS:
    _MARK_CLASS_ORDER[_ch] = 1
for _ch in ABOVE_VOWELS:
    _MARK_CLASS_ORDER[_ch] = 2
for _ch in TONE_MARKS:
    _MARK_CLASS_ORDER[_ch] = 3
for _ch in OTHER_SIGNS:
    _MARK_CLASS_ORDER[_ch] = 4

#: whitespace ที่ requirements ระบุให้ลบเมื่อคั่นหน้า combining mark (R3.3)
MARKER_WHITESPACE = "\u0020\u00a0\u0009"

#: แปลง Private Use Area ของฟอนต์ไทยเก่าเป็น combining mark จริง
#:
#: text layer ของเอกสารชุดนี้ใช้ codepoint ในย่าน U+F700-U+F71A แทนสระและ
#: วรรณยุกต์ (เป็น glyph variant ของฟอนต์ตระกูล AngsanaNew/CordiaNew/THSarabun)
#: ถ้าไม่แปลง ข้อความจะขาดสระ/วรรณยุกต์ทั้งหมด และการค้นหาจะพลาดทุกคำ
#:
#: mapping ทุกบรรทัดพิสูจน์จากคลังเอกสารจริง โดยสร้างพจนานุกรมจากคำที่ไม่มี PUA
#: แล้วเลือก mark ที่ทำให้ได้คำที่มีอยู่จริงมากที่สุด (hit rate กำกับไว้ท้ายบรรทัด)
THAI_PUA_TO_MARK: dict[str, str] = {
    "\uf701": "\u0e34",  # ิ  เปด -> เปิด            (hit 73.1%, 59 ครั้ง)
    "\uf702": "\u0e35",  # ี  ปที่ -> ปีที่             (hit 89.1%, 319 ครั้ง)
    "\uf703": "\u0e36",  # ึ  ฝกงาน -> ฝึกงาน        (hit 30.0%, 21 ครั้ง)
    "\uf705": "\u0e48",  # ่  ใฝรู้ -> ใฝ่รู้            (18 ครั้ง, ยืนยันจากคำตัวอย่างทุกคำ)
    "\uf706": "\u0e49",  # ้  ไฟฟา -> ไฟฟ้า          (hit 88.6%, 92 ครั้ง)
    "\uf709": "\u0e4c",  # ์  ทัศนศิลป -> ทัศนศิลป์     (6 ครั้ง, ยืนยันจาก ไปป์ไลน์ ด้วย)
    "\uf70a": "\u0e48",  # ่  หนวยกิต -> หน่วยกิต     (hit 69.4%, 1,983 ครั้ง)
    "\uf70b": "\u0e49",  # ้  ดวยตนเอง -> ด้วยตนเอง  (hit 57.9%, 3,059 ครั้ง)
    "\uf70c": "\u0e47",  # ็  เวบ -> เว็บ / อออบเจกต -> อ็อบเจกต์ (14 ครั้ง, ยืนยัน 4 คำ)
    "\uf70e": "\u0e4c",  # ์  สัปดาห -> สัปดาห์        (hit 83.2%, 2,154 ครั้ง)
    "\uf710": "\u0e31",  # ั  ปญหา -> ปัญหา          (hit 32.0%, 161 ครั้ง)
    "\uf712": "\u0e47",  # ็  เปน -> เป็น             (hit 48.7%, 260 ครั้ง)
    "\uf713": "\u0e48",  # ่  เว็บฝัง -> เว็บฝั่ง         (10 ครั้ง, ยืนยันจากคำตัวอย่างทุกคำ)
}

#: ย่าน PUA ของฟอนต์ไทยที่ต้องแปลง — ตัวที่ไม่มีใน mapping ต้องถูกรายงาน ไม่ใช่เดา
THAI_PUA_START = "\uf700"
THAI_PUA_END = "\uf71a"

#: character class ของ combining mark ไทยจริง (ตรงกับ `COMBINING_MARKS`)
#:
#: **หมายเหตุการแก้ไขจาก requirements R3.4** ข้อกำหนดเขียน pattern ต้องห้ามเป็น
#: `[\u0e00-\u0e7f]\s+[\u0e30-\u0e4e]` แต่ช่วง U+0E30-U+0E4E รวม **สระที่กินที่**
#: ซึ่งตามหลังช่องว่างได้อย่างถูกต้อง: ะ (0E30) า (0E32) ำ (0E33) เ (0E40) แ (0E41)
#: โ (0E42) ใ (0E43) ไ (0E44) ๅ (0E45) ๆ (0E46)
#:
#: วัดจากคลังจริง: ใช้ช่วงตามตัวอักษรของข้อกำหนดแล้วพบ "การละเมิด" 197 จาก 608 หน้า
#: ซึ่งทุกรายการเป็นข้อความไทยที่ถูกต้อง เช่น "คงอยู่ และเปลี่ยนชื่อ", "อัฉริยะ และ",
#: "รายวิชา เทคโนโลยี", "ตัดออก เปลี่ยนแปลง" ถ้าลบช่องว่างเหล่านั้นจะทำให้คำติดกันผิด
#: จึงจำกัดขอบเขตไว้ที่ combining mark เท่านั้น ซึ่งเป็นเจตนาของ R3.3/R3.4
THAI_COMBINING_CLASS = "\u0e31\u0e34-\u0e3a\u0e47-\u0e4e"

#: pattern ที่ผลลัพธ์สุดท้ายต้องไม่มีเหลืออยู่ (R3.4 ตามขอบเขตที่แก้ไขข้างต้น)
THAI_SPACE_MARK_RE = re.compile(rf"[\u0e00-\u0e7f]\s+[{THAI_COMBINING_CLASS}]")

_MARKER_SPACE_RE = re.compile(
    rf"([{THAI_BLOCK_START}-{THAI_BLOCK_END}])[{MARKER_WHITESPACE}]+([{THAI_COMBINING_CLASS}])"
)
_WHITESPACE_RUN_RE = re.compile(r"\s+")


def is_thai_char(ch: str) -> bool:
    """คืน True เมื่ออักขระอยู่ในบล็อกไทย U+0E00-U+0E7F."""
    return THAI_BLOCK_START <= ch <= THAI_BLOCK_END


def is_combining_mark(ch: str) -> bool:
    """คืน True เมื่อเป็นสระ/วรรณยุกต์/เครื่องหมายที่ต้องเกาะพยัญชนะ.

    หมายเหตุสำคัญ: `unicodedata.combining()` คืน 0 สำหรับ mark ไทยหลายตัว
    จึงตรวจด้วยชุดอักขระที่ประกาศไว้ ไม่พึ่ง Unicode combining class
    """
    return ch in COMBINING_MARKS


def is_thai_base(ch: str) -> bool:
    """คืน True เมื่อเป็นอักขระไทยที่ทำหน้าที่เป็น base (ไม่ใช่ combining mark)."""
    return is_thai_char(ch) and not is_combining_mark(ch)


def mark_class(ch: str) -> int:
    """คืนลำดับชั้นของ combining mark (1-4); 0 หมายถึงไม่ใช่ mark."""
    return _MARK_CLASS_ORDER.get(ch, 0)


def normalize_nfc(text: str) -> str:
    """Unicode normalization form C (R18.1)."""
    return unicodedata.normalize("NFC", text)


def map_thai_pua(text: str) -> str:
    """แปลง PUA ของฟอนต์ไทยเก่าเป็น combining mark จริง.

    ต้องทำเป็นขั้นแรกสุดของ pipeline ก่อน NFC และก่อนจัดลำดับ mark
    เพราะ PUA ไม่ใช่ combining mark ในสายตา Unicode จึงไม่ถูกจัดลำดับให้ถูกต้อง
    """
    if not text:
        return text
    out: list[str] = []
    for ch in text:
        out.append(THAI_PUA_TO_MARK.get(ch, ch))
    return "".join(out)


def unmapped_thai_pua(text: str) -> dict[str, int]:
    """คืนจำนวน PUA ในย่านฟอนต์ไทยที่ยังไม่มี mapping — ต้องรายงาน ไม่ใช่ทิ้งเงียบ.

    ใช้สร้าง review issue เมื่อพบ codepoint ที่ยังไม่เคยเห็นในคลัง
    """
    counts: dict[str, int] = {}
    for ch in text:
        if THAI_PUA_START <= ch <= THAI_PUA_END and ch not in THAI_PUA_TO_MARK:
            counts[ch] = counts.get(ch, 0) + 1
    return counts


def strip_marker_spaces(text: str) -> str:
    """ลบ whitespace ที่อยู่ระหว่างอักขระไทยกับ combining mark ที่ตามมา (R3.3).

    ลบเฉพาะตำแหน่งนี้เท่านั้น whitespace ตำแหน่งอื่นไม่ถูกแตะ
    ทำซ้ำจนไม่มีการเปลี่ยนแปลง เพราะการลบครั้งหนึ่งอาจทำให้เกิดคู่ใหม่ที่ติดกัน
    """
    previous = text
    while True:
        current = _MARKER_SPACE_RE.sub(r"\1\2", previous)
        if current == previous:
            return current
        previous = current


def squeeze_whitespace(text: str) -> str:
    """บีบ whitespace ที่ซ้อนกันเป็นช่องว่างเดียว และตัดหัวท้าย (R7.7, R8.8, R18.1)."""
    return _WHITESPACE_RUN_RE.sub(" ", text).strip()


def canonical_mark_order(text: str) -> str:
    """จัดลำดับ combining mark ภายในแต่ละพยางค์ให้เป็น canonical order (R3.1).

    การจัดเรียงเป็น stable sort ตามชั้นของ mark โดยคง relative order ของ mark
    ที่อยู่ชั้นเดียวกันไว้ (tie-break ตามลำดับที่ปรากฏใน input)

    ไม่เพิ่ม ไม่ลบ และไม่แทนอักขระใด — multiset ของ codepoint คงเดิมเสมอ
    """
    if not text:
        return text

    out: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        ch = text[index]
        out.append(ch)
        index += 1
        if is_combining_mark(ch):
            # mark ที่ไม่มี base นำหน้า — คงไว้ตามเดิม ไม่ย้าย
            continue
        run_start = index
        while index < length and is_combining_mark(text[index]):
            index += 1
        if index > run_start:
            marks = list(text[run_start:index])
            marks.sort(key=lambda m: _MARK_CLASS_ORDER.get(m, 0))
            out.extend(marks)
    return "".join(out)


def normalize_text(text: str) -> str:
    """normalize สำหรับเก็บเป็น `normalized_text` ของ span.

    ลำดับ: แปลง PUA -> NFC -> ลบ whitespace หน้า mark -> จัดลำดับ mark
    ไม่บีบ whitespace ตำแหน่งอื่น เพื่อคงความสัมพันธ์กับตำแหน่งอักขระต้นทาง
    """
    return canonical_mark_order(strip_marker_spaces(normalize_nfc(map_thai_pua(text))))


def normalize_for_compare(text: str) -> str:
    """normalize สำหรับการเทียบค่าใน metric, quote matching และ cache key.

    ลำดับ: NFC -> ลบ whitespace หน้า mark -> จัดลำดับ mark -> บีบ whitespace
    (R7.7, R8.8, R10.10, R17.3, R18.1)
    """
    return squeeze_whitespace(normalize_text(text))


def strip_combining_marks(text: str) -> str:
    """ลบสระและวรรณยุกต์ไทยทั้งหมด — ใช้เฉพาะการ "จับคู่" ไม่ใช่การเก็บข้อมูล.

    จำเป็นเพราะ text layer ของเอกสารชุดนี้บางหน้าไม่มี combining mark เลย
    (พบจริง: หน้าปกของ DSBA2565 ให้ข้อความ "วิทยาการขอมูล" แทน "วิทยาการข้อมูล")
    การเทียบแบบไม่สนใจ mark ทำให้จับคู่ชื่อสาขา/ระดับปริญญาได้แม้ text layer เสีย

    ห้ามใช้ผลของฟังก์ชันนี้เป็นข้อความที่เก็บลง store หรือแสดงต่อผู้ใช้
    """
    return "".join(ch for ch in text if ch not in COMBINING_MARKS)


def match_key(text: str) -> str:
    """คีย์สำหรับจับคู่ข้อความไทยแบบทนต่อความบกพร่องของ text layer.

    ลำดับ: normalize เพื่อเทียบ -> ลบ mark -> ลบ whitespace ทั้งหมด -> ตัวพิมพ์เล็ก

    การลบ whitespace จำเป็นเพราะชื่อสาขาบนหน้าปกถูกตัดข้ามบรรทัด ทำให้มีช่องว่าง
    แทรกกลางชื่อ (พบจริง: "วิทยาการข้อมูลและการวิเคราะห" + "เชิงธุรกิจ" แยกบรรทัด)
    ถ้าไม่ลบ การจับคู่ชื่อสาขาจะพลาดและไปแมตช์ประโยคอื่นที่กล่าวถึงสาขาอื่นแทน

    ใช้กับการ "จับคู่" เท่านั้น ห้ามใช้เป็นข้อความที่เก็บลง store หรือแสดงต่อผู้ใช้
    """
    stripped = strip_combining_marks(normalize_for_compare(text))
    return _WHITESPACE_RUN_RE.sub("", stripped).lower()


def marker_space_violations(text: str) -> int:
    """จำนวนตำแหน่งที่ยังตรง pattern ต้องห้าม — ต้องเป็นศูนย์ในผลลัพธ์สุดท้าย (R3.4)."""
    return len(THAI_SPACE_MARK_RE.findall(text))


def codepoint_multiset(text: str) -> dict[str, int]:
    """multiset ของ codepoint ที่ไม่ใช่ whitespace — ใช้ตรวจว่า glyph ไม่หาย (R3.7)."""
    counts: dict[str, int] = {}
    for ch in text:
        if ch.isspace():
            continue
        counts[ch] = counts.get(ch, 0) + 1
    return counts
