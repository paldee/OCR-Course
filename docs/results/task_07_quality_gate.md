# ผลงานที่ 7: Page_Quality_Gate และ Ocr_Page_Router

ข้อมูลดิบเครื่องอ่านได้: `artifacts/quality_gate_report.json` (รันทั้งคลัง 14 เอกสาร / 3,689 หน้า)

## 1. สิ่งที่สร้าง

| ไฟล์ | หน้าที่ |
| --- | --- |
| `katrag/ingest/quality_gate.py` | `DeclaredCharset`, `DomainLexicon` (จาก `domain_lexicon.toml`), `PageQualityGate.score()` คำนวณ 4 ตัวชี้วัด + `page_quality_score`, `PageQualityGate.mark()` ตัดสิน OCR candidacy แบบมีสถานะสะสม |
| `katrag/ingest/page_router.py` | `OcrPageRouter.route()` — total function กำหนด compute path เดียวต่อหน้า |
| `tests/property/test_quality_gate_properties.py` | 4 property (hypothesis, 300 examples) + 2 เคสเจาะจง (โควตา, ลำดับ) |

การออกแบบที่ตั้งใจ: `score()` **ไม่มีสถานะ** และ deterministic ล้วน (ตรงตาม Property 17)
ส่วนกฎ candidacy/โควตาที่เป็น stateful ต่อทั้ง dataset แยกไปอยู่ใน `mark()` ที่รับ
`candidates_so_far` จากผู้เรียก (`Ingestion_Manager` ในงานที่ 8) — ไม่นับภายในตัวเอง
เพื่อไม่ให้ปนสถานะเข้ากับฟังก์ชันคำนวณคะแนน

## 2. ผลการรันทั้งคลัง (3,689 หน้า)

| ตัวชี้วัด | ค่า |
| --- | --- |
| OCR candidates ที่ทำเครื่องหมาย | **979 / 979** (ตรงเพดานพอดี) |
| `low_content_page` (ข้อความน้อย ไม่มีภาพ) | 2 หน้า |
| `ocr_budget_exhausted` | 1 หน้า (หน้าที่เข้าเกณฑ์แต่โควตาเต็มแล้ว) |
| compute path: fast / standard / deep | 1,150 / 1,321 / 1,218 |
| compute path เฉพาะหน้าที่เป็น candidate: fast / standard / deep | 4 / 231 / 744 |
| page_quality_score ช่วงที่พบจริง | 0.2506 – 1.0000 |
| ปริมาณงาน | 317.2 s → 11.63 หน้า/วินาที (รวม extract+reorder+assemble+score+route) |

ตัวเลข 979 ตรงกับเพดานที่ requirements ประกาศไว้ล่วงหน้า (R4.5) เพราะ dataset มีหน้า
ที่ `char_count < 120` และมีภาพจำนวนมากกว่า 979 หน้าเล็กน้อย (มี 2 หน้าที่เข้าเกณฑ์ข้อความ
น้อยแต่ไม่มีภาพ จึงไม่ถูกนับเป็น candidate และมี 1 หน้าที่โควตาเต็มพอดีตัดออก)

compute path `fast` ของหน้า candidate มีเพียง 4 หน้า (จาก 979) เพราะเงื่อนไข candidate
ต้องมีภาพอยู่แล้ว และภาพที่ทำให้ข้อความน้อยมักครอบพื้นที่มาก — ส่วนใหญ่จึงตกที่ `deep`
(744 หน้า) ซึ่งสอดคล้องกับข้อเท็จจริงที่วัดไว้ก่อนหน้า (745 หน้าภาพเต็มหน้า)

## 3. การตรวจว่าไม่ถอยหลัง

* property test 6 รายการผ่านทั้งหมด (4 hypothesis property × 300 examples + 2 เคสเจาะจง)
* ทดสอบเขียนผลลง `page_metrics` ผ่าน `ProvenanceStore` จริง (FK ครบ: version → document →
  page → page_metrics) ยืนยันว่า `PageMetrics`/`GateDecision`/`RouteDecision` แปลงเป็น
  `PageMetricsRow` ได้ตรง schema โดยไม่ต้องแก้ store
* `test_budget_never_exceeded_across_sequence` จำลอง 1,029 หน้าที่เข้าเกณฑ์ candidate
  ทุกหน้า ยืนยันว่าสะสมหยุดที่ 979 พอดีและ `ocr_budget_exhausted` ยิง 50 ครั้งตามจำนวน
  ส่วนเกิน

## 4. ข้อควรระวังสำหรับงานถัดไป (Ingestion_Manager, งานที่ 8)

* **ตัวนับ `candidates_so_far` ต้องสะสมข้ามเอกสาร** ไม่ใช่รีเซ็ตต่อไฟล์ PDF มิฉะนั้น
  ตัวเลข 979 จะไม่ตรง (ยืนยันจากการสำรวจว่าโควตาเต็มพอดีที่หน้าสุดท้ายของคลังทั้งหมด)
* ลำดับการประมวลผลเอกสารต้อง deterministic (เรียงตาม `relative_path` เหมือน
  `Document_Registry`) เพราะหน้าไหนตกเป็น `ocr_budget_exhausted` ขึ้นกับลำดับที่ประมวลผล
* `image_area_ratio` ที่ใช้ต้องมาจาก `PageCharSet.image_area_ratio` ตรง ๆ (คำนวณไว้แล้ว
  ใน Text_Extractor) ห้ามคำนวณซ้ำใน `Page_Quality_Gate`
