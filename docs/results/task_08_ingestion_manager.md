# ผลงานที่ 8: Ingestion_Manager แบบ streaming และ resume

ข้อมูลดิบเครื่องอ่านได้: `artifacts/ingestion_manager_report.json` (รันทั้งคลัง 14 เอกสาร / 3,689 หน้าจริงผ่าน `katrag.sqlite3`)

## 1. สิ่งที่สร้าง

| ไฟล์ | หน้าที่ |
| --- | --- |
| `katrag/common/memory.py` | `MemoryMonitor` — วัด RSS ผ่าน `psutil` (เพิ่ม dependency ใหม่, BSD-3-Clause, pin `7.0.0`), ตั้ง baseline หลังหน้าที่กำหนด แล้วตัดสิน drift/limit ทุกหน้า |
| `katrag/ingest/manager.py` | `IngestionManager` — ประสาน Text_Extractor → Thai_Glyph_Reorderer → Line_Assembler → Page_Quality_Gate → Ocr_Page_Router ต่อหนึ่งหน้า, เขียนผลแบบ atomic ต่อหน้า, resume, replicate ผลของเอกสารซ้ำ, ผลิต manifest |
| `tests/property/test_ingestion_manager_properties.py` | 2 property (bounded page slot, ceiling violation ต้อง raise) + 2 integration test (resume ไม่ extract ซ้ำ, resume หลังขัดจังหวะจำลอง) |

## 2. ผลการรันทั้งคลังจริง (14 เอกสาร / 3,689 หน้า)

| ตัวชี้วัด | ค่า |
| --- | --- |
| สถานะ | `success` |
| หน้าที่เสร็จ | 3,689 / 3,689 |
| OCR candidates ทั้ง dataset | **979** (ตรงเพดาน R4.5 พอดี — รวมหน้าของเอกสารซ้ำแล้ว) |
| compute path: fast / standard / deep | 1,150 / 1,321 / 1,218 |
| peak resident memory | 485.7 MB (เพดาน 6 GB — ต่ำกว่ามาก เพราะยังไม่ rasterize ภาพจริง จนกว่าจะถึงงานที่ 9) |
| peak page slot ที่ใช้พร้อมกัน | 1 (เพดาน 2) |
| เวลาที่ใช้ (extract+reorder+assemble+score+route ครบทุกหน้า) | 296.7 s → 12.43 หน้า/วินาที |
| รันซ้ำแบบ resume (ทุกหน้า complete แล้ว) | 0.15 s สำหรับทั้ง 3,689 หน้า |
| review issue ที่เกิด | 20 รายการ |

manifest ที่ผลิตใหม่จากการรันจริงตรงกับที่บันทึกไว้ก่อนหน้า: 14 เอกสาร / 3,689 หน้า / 13 canonical document

## 3. บั๊กที่พบและแก้ระหว่างงานนี้

### โควตา OCR candidate นับผิดขอบเขตเมื่อมีเอกสารซ้ำ (R4.5)

การรันทั้งคลังครั้งแรกได้ **980** candidates แทน 979 ที่ R4.5 บังคับไว้ ("ไม่เกิน 979 หน้า
จาก 3,689 หน้า") สาเหตุ: ออกแบบเริ่มต้นให้ตัวนับ `candidates_so_far` นับเฉพาะหน้าของ
canonical document แล้ว **คัดลอก** ค่า `is_ocr_candidate` ของ canonical ไปยังหน้าของ
เอกสารซ้ำ (`PH_D_AITBA2569` / `M_AITBA2569` มี sha256 เท่ากัน) ตรง ๆ โดยไม่ตรวจโควตาใหม่
ทำให้หน้าซ้ำเพิ่มเข้ามาเป็นหน้าที่ 980 โดยไม่มีใครเห็นว่าเกิน

แก้โดยให้เอกสารซ้ำ**นำค่า `PageMetrics` ที่คำนวณไว้แล้วจาก canonical มาใช้ตรง ๆ**
(ไม่เรียก Text_Extractor/Page_Quality_Gate.score() ซ้ำ — ยังคงหลักการ "ประมวลผลเนื้อหา
ครั้งเดียว" ตาม R1.4/R1.5) แต่**ตัดสิน OCR candidacy ใหม่**ด้วย `PageQualityGate.mark()`
โดยใช้ `candidates_so_far` ที่นับจากทั้งฐานข้อมูล ณ ขณะนั้น (`SELECT COUNT(*) FROM
page_metrics WHERE is_ocr_candidate = 1` โดยไม่กรอง document) ยืนยันด้วย probe ที่ตั้ง
budget เทียมไว้ที่ 10 หน้าและมีเอกสารซ้ำคู่หนึ่ง (692 หน้ารวม): ยอดรวม candidate ทั้งสอง
เอกสารหยุดที่ 10 พอดี ไม่ใช่ 20

### resume ไม่ตรวจ scope ซ้ำหลังลงทะเบียนครั้งแรก (R1.2, R1.3)

โค้ดร่างแรกตรวจขอบเขต 14 เอกสาร/3,689 หน้าเฉพาะตอนที่ `document_count() == 0` (ครั้งแรก
ที่ลงทะเบียน) ถ้า scope ผิดตั้งแต่ครั้งแรก การเรียก `run()` ซ้ำในครั้งต่อ ๆ ไปจะข้ามการ
ตรวจนี้ไปเลยและเดินหน้าประมวลผลหน้าต่อทั้งที่ยังไม่ครบขอบเขต ขัดกับ R1.3 ที่บังคับให้จบงาน
ด้วยสถานะไม่สำเร็จ แก้โดยย้ายการตรวจ scope ไปอยู่นอกเงื่อนไข `document_count() == 0` —
ตรวจทุกครั้งที่ `run()` ถูกเรียก โดยอ่านจำนวนเอกสาร/หน้าจริงจากฐานข้อมูลสด

## 4. การตรวจว่าไม่ถอยหลัง

* property test ยืนยัน `PageBufferPool` ไม่ปล่อยให้ยืม slot เกินเพดานภายใต้ลำดับ
  ยืม/คืนแบบสุ่มความยาวถึง 200 ก้าว (200 ตัวอย่าง) และ raise `RuntimeError` เมื่อผู้เรียก
  ละเมิดเพดานตรง ๆ
* integration test ยืนยันด้วยการ patch `extract_page` แล้วรัน `IngestionManager.run()`
  ซ้ำบนเอกสารที่ complete แล้วทั้งหมด (346 หน้า) — จำนวนครั้งที่เรียก `extract_page` = 0
* integration test จำลองการขัดจังหวะกลางเอกสาร (drain generator แค่ 10 หน้าแล้วหยุด)
  จากนั้นสร้าง `IngestionManager` ใหม่ (จำลอง process ใหม่) แล้วรันต่อ — ได้ผลครบ 346 หน้า
* รันทั้งชุดทดสอบ 26 รายการผ่านหมดหลังแก้บั๊กทั้งสองจุด
* รันทั้งคลังจริงซ้ำสองครั้ง (ก่อน/หลังแก้บั๊กโควตา) ยืนยันว่า pages_completed, manifest,
  peak memory, peak slot ไม่เปลี่ยนแปลง มีเพียง `ocr_candidate_pages` ที่ถูกต้องขึ้น
  (980 → 979)

## 5. ขอบเขตที่ยังไม่ทำในงานนี้ (รอ Ocr_Cascade งานที่ 9)

* `ocr_invoked_pages` คงที่ที่ 0 เสมอ เพราะ Ocr_Cascade ยังไม่เชื่อมต่อ — หน้าที่ทำ
  เครื่องหมาย `is_ocr_candidate = 1` ยังไม่ถูกส่ง OCR จริง เป็นไปตามลำดับ phase ที่ design
  §12 กำหนด (ห้ามข้าม gate ของ Phase 2 ไป Phase 3)
* `process_page`/`process_document` ยืม `PageSlot` จาก pool ตาม pseudocode ของ design §3.5
  ไว้ล่วงหน้า แม้ยังไม่ rasterize ภาพจริง เพื่อให้ invariant ≤ 2 slot ถูกทดสอบได้ตั้งแต่ตอนนี้
  และไม่ต้องแก้โครงตอนต่อ Ocr_Cascade
