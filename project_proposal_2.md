# ใบเสนอโครงการ (Project Proposal)
## โปรเจกต์ที่ 2: OCR เล่มหลักสูตร เพื่อตอบคำถามเกี่ยวกับการเรียน

**วิชา** 06026240 Intelligent System Development  
**กลุ่ม/Section** ___

---

## 1. ภาพรวมโปรเจกต์ (Overview)

ทีมพัฒนาระบบ **KatRAG-lite** — ระบบ OCR เล่มหลักสูตร (มคอ.2) ของคณะเทคโนโลยีสารสนเทศ สจล. จำนวน 14 ไฟล์ (3,689 หน้า) ครอบคลุม 5 หลักสูตร (IT, DSBA, AIT, BIT, AITBA) แล้วนำเนื้อหาไปสร้างเป็นฐานความรู้ (RAG) ให้ LLM (Typhoon v2.5) ตอบคำถามนักศึกษาเกี่ยวกับโครงสร้างหลักสูตร วิชาบังคับ/เลือก เงื่อนไข prerequisite แผนการเรียน และให้คำแนะนำการลงทะเบียน พร้อมอ้างอิงหน้า/หัวข้อในเล่มหลักสูตร ระบบใช้ semantic search (bge-m3 embedding) ร่วมกับ structured query path เพื่อความแม่นยำสูง

---

## 2. ข้อมูลต้นฉบับ (เล่มหลักสูตร)

### ระดับปริญญาตรี (Bachelors Degree)

| ไฟล์ต้นฉบับ | จำนวนหน้า | ปีหลักสูตร/ฉบับ | ตำแหน่งเก็บไฟล์ (ใน repo) |
|---|---|---|---|
| AIT2566_current.pdf | 346 หน้า | AIT พ.ศ. 2566 (current) | `Information_Technology_Course/Bachelors_Degree/` |
| BIT2560_old.pdf | 242 หน้า | BIT พ.ศ. 2560 (old) | `Information_Technology_Course/Bachelors_Degree/` |
| BIT2565_current.pdf | 317 หน้า | BIT พ.ศ. 2565 (current) | `Information_Technology_Course/Bachelors_Degree/` |
| DSBA2560_old.pdf | 258 หน้า | DSBA พ.ศ. 2560 (old) | `Information_Technology_Course/Bachelors_Degree/` |
| DSBA2565_current.pdf | 403 หน้า | DSBA พ.ศ. 2565 (current) | `Information_Technology_Course/Bachelors_Degree/` |
| IT2560_old.pdf | 327 หน้า | IT พ.ศ. 2560 (old) | `Information_Technology_Course/Bachelors_Degree/` |
| IT2565_current.pdf | 429 หน้า | IT พ.ศ. 2565 (current) | `Information_Technology_Course/Bachelors_Degree/` |

### ระดับปริญญาเอก (Doctorals Degree)

| ไฟล์ต้นฉบับ | จำนวนหน้า | ปีหลักสูตร/ฉบับ | ตำแหน่งเก็บไฟล์ (ใน repo) |
|---|---|---|---|
| PH_D_AITBA2569_current.pdf | 252 หน้า | AITBA พ.ศ. 2569 (current) | `Information_Technology_Course/Doctorals_Degree/` |
| PH_D_IT2561_old.pdf | 156 หน้า | IT พ.ศ. 2561 (old) | `Information_Technology_Course/Doctorals_Degree/` |
| PH_D_IT2566_current.pdf | 199 หน้า | IT พ.ศ. 2566 (current) | `Information_Technology_Course/Doctorals_Degree/` |

### ระดับปริญญาโท (Masters Degree)

| ไฟล์ต้นฉบับ | จำนวนหน้า | ปีหลักสูตร/ฉบับ | ตำแหน่งเก็บไฟล์ (ใน repo) |
|---|---|---|---|
| M_AITBA2564_old.pdf | 147 หน้า | AITBA พ.ศ. 2564 (old) | `Information_Technology_Course/Masters_Degree/` |
| M_AITBA2569_current.pdf | 252 หน้า | AITBA พ.ศ. 2569 (current) | `Information_Technology_Course/Masters_Degree/` |
| M_IT2563_old.pdf | 182 หน้า | IT พ.ศ. 2563 (old) | `Information_Technology_Course/Masters_Degree/` |
| M_IT2568_current.pdf | 179 หน้า | IT พ.ศ. 2568 (current) | `Information_Technology_Course/Masters_Degree/` |

### สรุป

| | จำนวนไฟล์ | จำนวนหน้ารวม |
|---|---|---|
| ปริญญาตรี | 7 | 2,322 หน้า |
| ปริญญาเอก | 3 | 607 หน้า |
| ปริญญาโท | 4 | 760 หน้า |
| **รวมทั้งหมด** | **14 ไฟล์** | **3,689 หน้า** |

**แหล่งที่มา:** ดาวน์โหลดจากเว็บไซต์คณะเทคโนโลยีสารสนเทศ สจล. (มคอ.2 ฉบับเผยแพร่)  
**ตำแหน่งเก็บ:** `Information_Technology_Course/` ใน root ของโปรเจกต์ (GitHub repo)

### Ground Truth จากอาจารย์ (สำหรับวัดผล OCR + ระบบ)

ข้อมูล GT ที่อาจารย์จัดทำ (จาก GT_Template-2.xlsx) เป็นรายการวิชาระดับฟิลด์ (code, name_th, name_en, credits, year, semester, category, type) — **ไม่ได้ระบุหน้าในเล่ม** เพราะเป็นข้อมูลที่ key จาก spreadsheet โดยตรง

| ไฟล์ GT | หลักสูตร/แผน | จำนวนรายวิชา | ตำแหน่งเก็บไฟล์ |
|---|---|---|---|
| AIT_academic_plan.json | AIT 2566 | 58 วิชา | `data/teacher_gt/AIT/` |
| BIT_academic_plan_coop.json | BIT 2565 (สหกิจ) | 63 วิชา | `data/teacher_gt/BIT/` |
| BIT_academic_plan_no_coop.json | BIT 2565 (ไม่สหกิจ) | 63 วิชา | `data/teacher_gt/BIT/` |
| DSBA_academic_plan_coop.json | DSBA 2565 (สหกิจ) | 91 วิชา | `data/teacher_gt/DSBA/` |
| DSBA_academic_plan_no_coop.json | DSBA 2565 (ไม่สหกิจ) | 92 วิชา | `data/teacher_gt/DSBA/` |
| IT_academic_plan_coop.json | IT 2565 (สหกิจ) | 107 วิชา | `data/teacher_gt/IT/` |
| IT_academic_plan_no_coop.json | IT 2565 (ไม่สหกิจ) | 109 วิชา | `data/teacher_gt/IT/` |
| general_education_ground_truth.json | ทุกหลักสูตร (วิชาศึกษาทั่วไป) | 266 วิชา | `data/teacher_gt/` |
| rules_ground_truth.json | ทุกหลักสูตร (กฎ/เกณฑ์จบ) | — | `data/teacher_gt/` |

**รวม GT:** 10 ไฟล์ ครอบคลุม 4 หลักสูตร (AIT, BIT, DSBA, IT) + วิชาศึกษาทั่วไป + กฎเกณฑ์  
**ที่มา:** คัดลอกจาก Lab_Week3/ocr_system/data/ground_truth (อาจารย์จัดทำ) — ใช้เป็น read-only สำหรับวัดผลเท่านั้น ห้ามใช้เป็นแหล่งคำตอบ  
**หมายเหตุ:** GT ไม่ได้ระบุหน้าในเล่ม PDF (เป็นข้อมูลระดับรายวิชา ไม่ผูกกับหน้าเฉพาะ) การวัดจึงเทียบ field-by-field ระหว่าง OCR output กับ GT โดยตรง

---

## 3. แผนการทำ OCR

**โมเดล/บริการที่ใช้:**

1. **PDF Text Layer Extraction** (PyMuPDF/fitz) — ดึง text layer จาก PDF ที่เป็น digital-born (ส่วนใหญ่ ~2,714 หน้า)
2. **Tesseract 5 OCR** — สำหรับหน้าที่เป็นภาพ/สแกน (975 หน้า) ได้ผลปรับปรุง 950 หน้า
3. **Structured Table Parsing** — แกะตารางแผนการเรียน/รายวิชาจาก OCR output แล้ว populate ลงตาราง structured (course, plan_slot)

**เหตุผล:** ใช้ text layer ก่อนเพราะแม่นยำ 100% สำหรับ digital PDF ส่วน Tesseract ใช้เฉพาะหน้าที่ไม่มี text layer (ภาพสแกน) เพื่อลดเวลาประมวลผลและเพิ่มความแม่นยำ

---

## 4. ออกแบบฐานข้อมูล / RAG

| ชื่อตาราง/Collection | ฟิลด์หลัก | Chunking Strategy | Embedding Model / Vector DB |
|---|---|---|---|
| `curriculum_version` | version_id PK, program, curriculum_year, edition_status | - | - |
| `course` | course_id PK, version_id FK, code, name_th, name_en, credits_raw, year, semester, prerequisite_json | - | - |
| `chunk` | chunk_id PK, version_id FK, page_number, heading, text | แบ่งตามหัวข้อ/หน้า (~300-800 คำ/chunk) | - |
| `chunk_embedding` | chunk_id FK, dim, vector | - | bge-m3 (dim 1024), SQLite BLOB |
| `course_embedding` | course_id FK, dim, vector | 1 row ต่อ 1 วิชา (ชื่อไทย+อังกฤษ) | bge-m3 (dim 1024), SQLite BLOB |

**Retrieval Strategy:**
- **Hybrid Search** (lexical BM25 + dense cosine RRF) สำหรับคำถามทั่วไป
- **Structured Query Path** สำหรับคำถามรายวิชา/แผนเรียน (query ตรงจากตาราง course/plan_slot)
- **Semantic Course Search** (bge-m3 course embedding) สำหรับคำถาม "วิชาเกี่ยวกับ X มีอะไรบ้าง"
- **Vector DB:** SQLite3 + in-memory numpy (ไม่ต้อง external service)

---

## 5. คำถาม / คำแนะนำ / ค่าที่จะ Evaluate

### ระดับง่าย (Easy) — ข้อเท็จจริงตรง ๆ

| # | คำถาม | คำตอบที่ถูกต้อง | อ้างอิง (หน้า/หัวข้อ) |
|---|---|---|---|
| 1 | หลักสูตร DSBA 2565 ต้องเรียนครบกี่หน่วยกิตจึงจะสำเร็จการศึกษา? | 126 หน่วยกิต | หน้า 7 หัวข้อ "โครงสร้างหลักสูตร" |
| 2 | วิชา 06026212 (Data Warehousing) เปิดสอนปีที่เท่าไร เทอมอะไร? | ปีที่ 3 ภาคการศึกษาที่ 1 | หน้า 15 ตารางแผนการเรียน |
| 3 | หลักสูตร IT 2565 มีกี่แขนงวิชา? | 4 แขนง (Network & Security, Software Engineering, Multimedia, Data Science) | หน้า 10 หัวข้อ "แขนงวิชา" |
| 4 | วิชา CALCULUS 1 ของ DSBA มีกี่หน่วยกิต? | 3 หน่วยกิต (3-0-6) | หน้า 12 ตารางรายวิชา |
| 5 | DSBA ปี 1 เทอม 1 เรียนกี่วิชา? | 7 วิชา (18 หน่วยกิต) | หน้า 14 แผนการศึกษา |
| 6 | วิชา Calculus 2 ต้องผ่านวิชาใดก่อน? | Calculus 1 (06026200) | หน้า 12 หัวข้อ prerequisite |
| 7 | หลักสูตร AIT 2566 ชื่อเต็มภาษาอังกฤษว่าอะไร? | Bachelor of Science in Artificial Intelligence Technology | หน้า 1 ปกเล่ม |

### ระดับกลาง (Medium) — เชื่อมโยงข้อมูล 2 จุดขึ้นไป

| # | คำถาม | คำตอบที่ถูกต้อง | อ้างอิง (หน้า/หัวข้อ) |
|---|---|---|---|
| 1 | วิชา Data Warehousing ต้องผ่านวิชาใดก่อน และวิชานั้นเรียนปีไหน? | ต้องผ่าน 06066300 แนวคิดระบบฐานข้อมูล (ปี 2 เทอม 1) | หน้า 15, 18 (แผนเรียน + prerequisite) |
| 2 | หมวดวิชาเฉพาะเลือกของ DSBA เก็บกี่หน่วยกิต และต้องเรียนตอนไหน? | ไม่น้อยกว่า 6 หน่วยกิต เริ่มลงได้ตั้งแต่ปี 3 | หน้า 7-8 (โครงสร้าง) + หน้า 15-16 (แผนเรียน) |
| 3 | DSBA มีวิชาที่เกี่ยวกับการเขียนโปรแกรมกี่วิชา รวมกี่หน่วยกิต? | 4 วิชา รวม 12 หน่วยกิต (PSCP, Computer Programming, Data Analytics & Programming, Web Programming) | หน้า 12-15 (ตารางรายวิชา) |
| 4 | วิชาที่มีในหลักสูตร DSBA 2560 แต่ไม่มีในหลักสูตร 2565 มีอะไรบ้าง? | (รายการวิชาที่ถูกตัดออก เช่น วิชาภาษาอังกฤษเฉพาะทาง) | หน้า 12 (2560) vs หน้า 12 (2565) |
| 5 | DSBA มีวิชาเกี่ยวกับคณิตศาสตร์กี่ตัว? | 6 วิชา 18 หน่วยกิต (Calculus 1/2, Linear Algebra, Discrete Math, Probability & Statistics, Bayesian Statistics) | หน้า 12-16 |
| 6 | IT มีวิชาเกี่ยวกับเครือข่ายกี่วิชา? | 7 วิชา (Network Infrastructure, IoT, Wireless Network, Network Design, Network Performance, Intro to Networks, Troubleshooting) | หน้า 18-25 |

### ระดับยาก (Hard) — ตีความ/สังเคราะห์/ให้คำแนะนำ

| # | คำถาม | คำตอบที่ถูกต้อง | อ้างอิง (หน้า/หัวข้อ) |
|---|---|---|---|
| 1 | หากต้องการจบ DSBA ภายใน 3.5 ปี ควรวางแผนอย่างไร? | ต้องดึงวิชาปี 4 เทอม 2 + วิชาเลือก มาลงในเทอมก่อนหน้า เฉลี่ยเพิ่มเทอมละ 3-6 หน่วยกิต ตรวจ prerequisite | หน้า 14-16 แผนการศึกษา |
| 2 | เรียนวิชา Database ปี 4 ได้ไหม? | ไม่ได้ เพราะ Database System Concepts เป็นวิชาบังคับปี 2 เทอม 1 ไม่ใช่วิชาเลือกที่เปิดให้ลงปี 4 | หน้า 15 แผนเรียน + หน้า 18 prerequisite |
| 3 | ลงวิชา Data Warehouse ตอนปี 2 ได้ไหม? | ไม่ได้ เพราะต้องผ่าน Database System Concepts (ปี 2 เทอม 1) ก่อน และ Data Warehouse อยู่ปี 3 เทอม 1 ตามแผน | หน้า 15, 18 |
| 4 | นักศึกษา DSBA ปี 3 ต้องเลือกแขนงไหน และแต่ละแขนงมีวิชาอะไรบ้าง? | ต้องเลือก 1 แขนง จาก 3 กลุ่ม: วิทยาการข้อมูล / การวิเคราะห์เชิงสถิติ / วิศวกรรมข้อมูล (มีวิชาให้เลือกกลุ่มละ ~8-14 วิชา) | หน้า 16-20 หัวข้อวิชาเลือก |
| 5 | เปรียบเทียบหลักสูตร DSBA 2560 กับ 2565 ต่างกันอย่างไร? | หลักสูตร 2565 เพิ่มวิชา ML/DL/Big Data และตัดวิชาภาษาอังกฤษเฉพาะทางออก โครงสร้างเปลี่ยนจาก 4 ปี 8 เทอม เป็นเน้น data science มากขึ้น | หน้า 7-20 (ทั้ง 2 เวอร์ชัน) |
| 6 | ถ้าจะจบใน 3.5 ปี ตรวจว่าแผนเรียนครบเงื่อนไขจบหรือไม่? | ต้องตรวจ: หน่วยกิตรวมครบ 126, วิชาบังคับครบ, วิชาเลือกแขนงครบ 6 หน่วยกิต, วิชาเลือกเสรีครบ, วิชา prerequisite ผ่านก่อนลงวิชาถัดไป | หน้า 7 (เกณฑ์จบ) + หน้า 14-16 (แผน) |

---

## 6. แผนการประเมินคำตอบ

### 6.0 การแยก OCR กับ Text Layer (ต้องระบุให้ชัดว่าอะไรใช้อะไร)

**สคริปต์:** `python -m katrag.eval.extraction_report` → รายงาน `artifacts/extraction_method_report.md`

ทุกหน้าถูก route อัตโนมัติด้วย `katrag/ingest/page_router.py` โดยดูตัวชี้วัดของหน้า ไม่ได้เลือกด้วยมือ

| ตัวชี้วัด | หน้าที่ใช้ text layer | หน้าที่ส่งเข้า OCR |
|---|---:|---:|
| จำนวนหน้า | 2,739 (74.2%) | 950 (25.8%) |
| อักขระที่ PDF ฝังมา | 1,264 | 94 |
| สัดส่วนพื้นที่ภาพ | 0.340 | 0.788 |
| คำในคลังศัพท์หลักสูตร | 10.2 | 3.0 |
| page quality score | 0.724 | 0.315 |

เกณฑ์ตัดสิน: **ข้อความฝังน้อย + พื้นที่ภาพสูง → ส่งเข้า OCR**

**ข้อมูลปลายทางมาจากวิธีไหน:**

| ข้อมูล | จาก text layer | จาก OCR | สัดส่วน OCR |
|---|---:|---:|---:|
| chunk (ฐานความรู้ RAG) | 8,493 | 3,094 | 26.7% |
| รายวิชา (structured) | 819 | 600 | 42.3% |

**แยกตามเล่ม:** 6 เล่มใช้ text layer เท่านั้น (BIT ทั้ง 2 ฉบับ, DSBA ทั้ง 2 ฉบับ, PH_D_IT2566, M_AITBA2564) / 8 เล่มใช้แบบผสม โดย AIT2566 มีสัดส่วน OCR สูงสุด 65%

**ข้อจำกัดของการวัดที่ต้องระบุตอนนำเสนอ:**

GT ของอาจารย์คือตารางแผนการเรียนของ 4 หลักสูตร ซึ่งอยู่ในหน้าที่มี text layer ทั้งหมด ดังนั้นรายวิชา 272 ตัวที่จับคู่กับ GT ได้ **มาจาก text layer 272 / จาก OCR 0**

- ตัวเลข field accuracy ในหัวข้อ 6.1 วัด **คุณภาพ text layer + ขั้นตอน parsing** ไม่ได้วัดคุณภาพ OCR
- การเปลี่ยน OCR engine (เช่น Typhoon OCR) จะไม่ทำให้ตัวเลขชุดนั้นเปลี่ยน
- แต่ OCR ยังรับผิดชอบรายวิชา 600 ตัว และ chunk 3,094 ชิ้น (วิชาเลือก วิชาศึกษาทั่วไป คำอธิบายรายวิชา) ซึ่ง GT ไม่ครอบคลุม จึงประเมินได้เพียงโดยอ้อมผ่าน OCR confidence (0.85–1.00) และ out-of-charset ratio (0.0004–0.0311)
- ถ้าจะวัด OCR โดยตรงต้องมี GT ระดับข้อความของหน้าที่ผ่าน OCR เพื่อคำนวณ CER/WER (`katrag/eval/metrics.py` มี `page_cer` รออยู่แล้ว)

**ข้อค้นพบเพิ่มเติม:** ระบบตรวจพบว่า `PH_D_AITBA2569_current.pdf` และ `M_AITBA2569_current.pdf` มี sha256 เดียวกัน (ไฟล์ ป.เอก ได้เนื้อหาของ ป.โท มา) บันทึกไว้ใน `document_relation` และ map ไป version เดียวกัน จึงไม่เกิดข้อมูลซ้ำในฐานความรู้

---

### 6.1 การวัดผล OCR / การสกัดข้อมูล

**สคริปต์:** `python -m katrag.eval.ocr_eval` → รายงาน `artifacts/ocr_eval_report.md`

วัด **3 ระดับ** โดยเทียบกับ Ground Truth ของอาจารย์ (เปิดแบบ read-only เท่านั้น)

#### ระดับที่ 1 — Page level (วิธีสกัดและคุณภาพหน้า)

| ตัวชี้วัด | ความหมาย |
|---|---|
| extraction method | หน้านั้นใช้ text layer หรือ OCR Tesseract 5 |
| page quality score | คะแนนคุณภาพหน้า (0-1) จาก char count, สัดส่วนภาพ, คำในคลังศัพท์เฉพาะทาง |
| out-of-charset ratio | สัดส่วนอักขระนอกชุดที่คาดหวัง (สูง = OCR เพี้ยน) |
| OCR confidence | ค่าความมั่นใจของ Tesseract เฉพาะหน้าที่ผ่าน OCR |

**ผลจริง:** 14 เล่ม / 3,689 หน้า — text layer 2,739 หน้า (74.2%), OCR 950 หน้า (25.8%)  
OCR confidence เฉลี่ย 0.85–1.00 | out-of-charset 0.0004–0.0311

#### ระดับที่ 2 — Field level (เทียบฟิลด์รายวิชากับ GT)

**ผลจริง (course-code coverage):**

| หลักสูตร | GT | ระบบสกัดได้ | จับคู่ได้ | Recall | Precision | F1 |
|---|---:|---:|---:|---:|---:|---:|
| AIT 2566 | 50 | 366 | 46 | 0.920 | 0.126 | 0.221 |
| BIT 2565 | 56 | 55 | 55 | 0.982 | 1.000 | 0.991 |
| DSBA 2565 | 80 | 75 | 75 | 0.938 | 1.000 | 0.968 |
| IT 2565 | 99 | 315 | 96 | 0.970 | 0.305 | 0.464 |

Recall สูงทุกหลักสูตร (0.92–0.98) = OCR ไม่ตกวิชา  
Precision ต่ำใน AIT/IT เพราะระบบสกัดวิชาเลือกทั้งเล่ม (366/315 วิชา) ขณะที่ GT ครอบคลุมเฉพาะแผนการเรียน (50/99 วิชา) — ไม่ใช่ error ของ OCR

**ผลจริง (ความถูกต้องต่อฟิลด์ รวมทุกหลักสูตร):**

| ฟิลด์ | ตรงเป๊ะ | เกือบตรง | เทียบทั้งหมด | Accuracy | Accuracy (ผ่อนปรน) |
|---|---:|---:|---:|---:|---:|
| ชื่อวิชา (ไทย) | 250 | 16 | 272 | 0.919 | 0.978 |
| ชื่อวิชา (อังกฤษ) | 252 | 7 | 272 | 0.926 | 0.952 |
| หน่วยกิต | 270 | 0 | 271 | 0.996 | 0.996 |
| ชั้นปี | 137 | 2 | 145 | 0.945 | 0.959 |
| ภาคการศึกษา | 139 | 0 | 145 | 0.959 | 0.959 |

`Accuracy (ผ่อนปรน)` นับกรณีต่างกัน ≤ 2 ตัวอักษรเป็นถูก เพราะตรวจพบว่า GT ของอาจารย์มีคำพิมพ์ผิด (เช่น "อัลกอรึทึม", "การตลาดเบื้อต้น", "โรงเรียนสร้างเสน่าห์") ซึ่งระบบอ่านถูกแต่ถูกนับว่าผิด

Field macro-accuracy ต่อหลักสูตร: AIT 0.991 | DSBA 0.979 | BIT 0.953 | IT 0.895

#### ระดับที่ 3 — Category level (recall ต่อหมวดวิชา)

เช่น DSBA 2565: หมวดวิชาเฉพาะ, หมวดวิชาศึกษาทั่วไป, หมวดวิชาเลือกเสรี — วัดว่าแต่ละหมวดสกัดได้ครบกี่ %

**ข้อมูล GT ที่ใช้วัด:**
- `data/teacher_gt/AIT/AIT_academic_plan.json` (58 วิชา)
- `data/teacher_gt/BIT/BIT_academic_plan_no_coop.json` (63 วิชา)
- `data/teacher_gt/DSBA/DSBA_academic_plan_no_coop.json` (92 วิชา)
- `data/teacher_gt/IT/IT_academic_plan_no_coop.json` (109 วิชา)
- `data/teacher_gt/general_education_ground_truth.json` (266 วิชา)
- `data/teacher_gt/rules_ground_truth.json` (กฎ/เงื่อนไขจบการศึกษา)

**บั๊กที่ evaluation ตรวจเจอและแก้แล้ว:** regex สกัดชื่อวิชาทำเลขลำดับหาย ("แคลคูลัส 1" → "แคลคูลัส") และดูดเลขหน่วยกิตเข้าชื่ออังกฤษ ("LINEAR ALGEBRA 3") — หลังแก้ ชื่ออังกฤษดีขึ้นจาก 0.213 → 0.926

### 6.2 การวัดผลคำตอบ (QA Accuracy)

| ช่วงความแม่นยำ (ตอบถูก + อ้างอิงถูก) | สัดส่วนคะแนนที่ได้ | หมายเหตุ |
|---|---|---|
| มากกว่า 91% | 100% | เป้าหมายที่ทีมตั้งไว้ |
| 80% – 90% | 80% | ผ่านเกณฑ์ |
| น้อยกว่า 80% | 60% | ควรปรับปรุง RAG/prompt เพิ่มเติม |

**สคริปต์:** `python -m katrag.eval.qa_eval` → รายงาน `artifacts/qa_eval_report.md`

**วิธีคำนวณ accuracy และแผนการทดสอบ:**

- ทดสอบด้วยชุดคำถาม 19 ข้อ (Easy 7 + Medium 6 + Hard 6) ที่เก็บใน `katrag/eval/qa_questions.py`
- ให้คะแนน 2 ชั้น:
  1. **auto-screening** — ตรวจอัตโนมัติว่าคำสำคัญปรากฏในคำตอบไหม (คัดกรองเบื้องต้น)
  2. **human review** — ผู้ตรวจกาช่อง "คำตอบถูก" + "อ้างอิงถูก" ตามเกณฑ์ในใบเสนอโครงการ
- Accuracy = จำนวนข้อที่ (คำตอบถูก AND อ้างอิงถูก) / จำนวนข้อทั้งหมด × 100%
- ผู้ตรวจ: สมาชิกในทีมเทียบกับเอกสาร มคอ.2 ต้นฉบับ

**การป้องกันการรั่วของเฉลย (anti-leak):**  
`qa_eval.py` ส่งเข้า API เฉพาะ `{question, program}` เท่านั้น และมี `assert` บังคับว่า
ฟิลด์ `expected` / `reference` ต้องไม่ปรากฏใน payload — เฉลยจึงไม่เคยเข้าถึง prompt หรือ LLM

**ผล auto-screening ล่าสุด:**

| ระดับ | จำนวนข้อ | auto pass | เวลาเฉลี่ย |
|---|---:|---:|---:|
| easy | 7 | 7/7 | 3.5s |
| medium | 6 | 6/6 | 0.2s |
| hard | 6 | 6/6 | 1.7s |
| **รวม** | **19** | **19/19** | |

(ตัวเลขนี้เป็นการคัดกรองว่าคำตอบมีข้อมูลที่ควรมี ยังต้องให้ผู้ตรวจยืนยันความถูกต้องของเนื้อหาและการอ้างอิง)

### 6.3 ความถูกต้องของการอ้างอิง (citation accuracy)

**สคริปต์:** `python -m katrag.eval.build_gold_set` แล้ว `python -m katrag.eval.qa_eval`

`gold_set` เก็บ "หน้าที่ควรถูกอ้างอิง" ของแต่ละคำถาม โดย derive จาก **provenance ของข้อมูลจริงในฐาน** (หน้าที่มีรายวิชาของชั้นปีนั้น / หน้าที่มีคำสำคัญของคำถาม) ไม่ได้ derive จากคำตอบของระบบ จึงไม่เป็นการตรวจตัวเอง

**ผลจริง (วัดได้ 15 จาก 19 ข้อ):**

| ตัวชี้วัด | ค่า | ความหมาย |
|---|---:|---|
| citation page precision | **0.574** | หน้าที่อ้าง อยู่ในชุดหน้าหลักฐานจริงกี่ % |
| citation page recall | **0.541** | หน้าหลักฐานจริง ถูกอ้างถึงกี่ % |

จำกัดหน้าหลักฐานที่ 10 หน้าต่อคำถาม เพื่อให้เพดาน recall ตีความได้ (แสดงคอลัมน์เพดานในรายงาน)

**การปรับปรุงที่ทำเพื่อยกตัวเลขนี้** (จากค่าเริ่มต้น 0.300 → 0.574):

1. **ตัด boilerplate chunk** — หัว/ท้ายกระดาษที่ซ้ำทุกหน้า (เช่น "มคอ. 2 วท.บ (วิทยาการข้อมูล...) คณะเทคโนโลยีสารสนเทศ") มี 1,546 chunk (13.3%) มีคำสำคัญของหลักสูตรครบแต่ไม่มีสาระ ทำให้ retrieval ดึงขึ้นมาติดอันดับ → ตรวจจับด้วยลายเซ็นข้อความที่ซ้ำ ≥ 5 หน้าในเล่มเดียวกัน (`katrag/ingest/mark_boilerplate.py`) แล้วกันออกจาก retrieval
2. **ตัดหลักฐานซ้ำหน้าเดียวกัน** — หลาย chunk ในหน้าเดียวกันเคยกิน citation slot ซ้ำซ้อน
3. **adaptive cutoff ตามคะแนน** — คะแนน hybrid มี cliff ชัดเจน (เช่น 0.020/0.019/0.018/0.018 แล้วตกเป็น 0.008) จึงเก็บเฉพาะหน้าที่คะแนน ≥ อันดับหนึ่ง × 0.55 แทนการคืน 10 หน้าเสมอ
4. **citation ต้องตรงกับข้อมูลที่ใช้ตอบ** — เมื่อคำตอบมาจาก structured path (ตาราง `course`) citation เดิมมาจาก chunk ที่ retrieval ดึงมาซึ่งไม่ใช่หน้าที่ให้คำตอบ แก้เป็นชี้หน้าที่รหัสวิชาในคำตอบปรากฏจริง เรียงตามจำนวนรหัสที่พบในหน้านั้น (`source_pages_for_codes`) — ข้อนี้ยกค่าจาก 0.349 → 0.574

**บั๊กที่พบตอนสร้างการวัดนี้:** ฟิลด์ `citations[].document_id` เคยเก็บ `chunk_id` ไม่ใช่ document_id จริง ทำให้ตรวจย้อนกลับไปหน้าใน PDF ไม่ได้และวัด citation ไม่ได้เลย (0.000 ทุกข้อ) — แก้โดยให้ `retriever.RetrievedChunk` และ `semantic_retriever.HybridHit` ส่ง `document_id` มาด้วย

---

## 7. แผนแอปพลิเคชันและเอกสารประกอบ



**Checklist:**
- ☑ Push code ขึ้น GitHub: https://github.com/paldee/OCR-Course.git
- ☑ มี README
- ☑ แนบ Dataset (PDF ต้นฉบับ + SQLite database)
- ☐ มี Presentation/Slide
- ☐ ส่งลิงก์ใน Discord

---

## สรุปเกณฑ์การให้คะแนน

| องค์ประกอบ | คะแนนเต็ม | สัมพันธ์กับหัวข้อ |
|---|---|---|
| 1. ออกแบบฐานข้อมูล / RAG | 20 | หัวข้อ 4 |
| 2. การอ่านภาพ: OCR / Keypoint | 20 | หัวข้อ 3 |
| 3. ความแม่นยำ / คุณภาพผลลัพธ์ | 20 | หัวข้อ 6 |
| 4. LLM / Recommend | 30 | หัวข้อ 5 และ 6 |
| 5. แอปใช้งานได้จริง + เอกสาร | 10 | หัวข้อ 7 |
