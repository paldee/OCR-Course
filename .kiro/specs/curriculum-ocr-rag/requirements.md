# Requirements Document

## Introduction

เอกสารนี้กำหนดความต้องการของระบบ **curriculum-ocr-rag (KatRAG-lite)** ซึ่งเป็นระบบตอบคำถามหลักสูตรแบบ offline-first ทำงานบนเครื่องผู้ใช้ทั้งหมด ไม่ใช้ paid API และใช้เฉพาะ engine ที่เป็น open-source ใช้ฟรี (PyMuPDF, PaddleOCR PP-OCRv5, Tesseract 5, bge-m3, llama.cpp + Qwen3 4B GGUF Q4)

ขอบเขตข้อมูลคือเอกสารหลักสูตร PDF จำนวน 14 ไฟล์ รวม 3,689 หน้า ใน `project/Information_Technology_Course/` ซึ่งเป็น born-digital PDF ทั้งหมด (มี embedded subset fonts และ vector text จริง ไม่ใช่สแกน) ระบบจึงต้องดึงข้อมูลแบบ *text-first* และเรียก OCR เฉพาะหน้า/บริเวณที่วัดได้ว่าคุณภาพ text layer ไม่ผ่านเกณฑ์ (จากการวัดจริง หน้าที่มีข้อความน้อยกว่า 120 ตัวอักษรคือ 979 จาก 3,689 หน้า = 26.5% ซึ่งเป็น upper bound ของงาน OCR ทั้งโปรเจกต์)

ระบบแบ่งเป็นสองระบบย่อยที่แยกกันเด็ดขาด: (1) **KatOCR Cascade** เส้นทาง offline ingestion ที่ผลิตข้อมูลลง SQLite แบบ provenance-first และ (2) **KatRAG-lite query path** เส้นทางตอบคำถาม online ที่ไม่มี OCR อยู่ในเส้นทางเลย ประกอบด้วย hybrid retrieval → phrase boost → MaxSim rerank → bounded multi-hop evidence DAG พร้อม gain–cost early halt → deterministic reasoning → LLM ท้องถิ่นที่ถูกบังคับให้อ้างอิงเฉพาะ citation ID ที่ระบบสร้างให้ → citation validator

ความต้องการทั้งหมดในเอกสารนี้อยู่ภายใต้หลักการหลักสามข้อของการออกแบบ: ตัวเลขทุกตัวต้องวัดได้และแยกให้ชัดระหว่าง *measured fact* กับ *architectural estimate*; ความถูกต้องเชิงตรรกะ (prerequisite / หน่วยกิต / เกณฑ์สำเร็จการศึกษา) ต้องคำนวณด้วย deterministic code ไม่ใช่ LLM; และการปนข้ามเวอร์ชันหลักสูตรถือเป็น correctness bug ไม่ใช่ปัญหาคุณภาพคำตอบ

## Glossary

- **KatRAG_System**: ระบบรวมทั้งหมดของ curriculum-ocr-rag ประกอบด้วยระบบย่อยทุกตัวในคำศัพท์นี้
- **Ingestion_Manager**: ระบบย่อยที่ควบคุมลำดับงาน offline ingestion จาก PDF ถึงการบันทึกลง Provenance_Store
- **Document_Registry**: ระบบย่อยที่บันทึกตัวตนของเอกสาร (path, SHA-256, จำนวนหน้า, program, curriculum_year, edition_status) และ review_issue
- **Text_Extractor**: ระบบย่อยที่ดึง text layer และ per-character geometry จาก PDF ด้วย PyMuPDF rawdict
- **Thai_Glyph_Reorderer**: ระบบย่อยที่จัดลำดับ glyph ภาษาไทยใหม่จาก per-char bbox + font + baseline ตามกฎอักขรวิธีไทย
- **Line_Assembler**: ระบบย่อยที่ประกอบบรรทัดจาก glyph ด้วย baseline clustering แล้วเรียงตามแกน X และ Y
- **Page_Quality_Gate**: ระบบย่อยที่ให้คะแนนคุณภาพข้อความของหน้า และตัดสินว่าหน้านั้นเป็น OCR candidate หรือไม่
- **Ocr_Page_Router**: ระบบย่อยที่กำหนด compute path (Fast / Standard / Deep) ให้แต่ละหน้าที่เป็น OCR candidate
- **Ocr_Cascade**: ระบบย่อยที่เรียก OCR engine ตามลำดับขั้น (escalation) ภายใต้ Gain_Cost_Halter
- **Preprocessor**: ระบบย่อยที่ปรับภาพก่อน OCR แบบมีเงื่อนไขและตรวจสอบผลได้
- **Region_Adjudicator**: ระบบย่อยที่ทำ spatial voting ต่อ region จากผลของ OCR engine หลายตัวและเลือกผลลัพธ์สุดท้าย
- **Table_Extractor**: ระบบย่อยที่แปลงตารางแผนการศึกษาและตารางหน่วยกิตเป็นโครงสร้าง cell พร้อม bbox
- **Field_Extractor**: ระบบย่อยที่แปลงข้อความ/cell เป็น structured field ของหลักสูตร (course, plan slot, rule)
- **Credits_Parser**: ระบบย่อยที่แปลงสตริงหน่วยกิตรูปแบบ `3(3-0-6)` เป็น total / lecture / lab / self_study
- **Credits_Printer**: ระบบย่อยที่แปลงโครงสร้างหน่วยกิตกลับเป็นสตริงรูปแบบ `3(3-0-6)`
- **Prerequisite_Parser**: ระบบย่อยที่แปลงข้อความเงื่อนไขวิชาก่อนเป็นโครงสร้าง prerequisite expression
- **Prerequisite_Printer**: ระบบย่อยที่แปลง prerequisite expression กลับเป็นข้อความรูปแบบ canonical
- **Provenance_Store**: ฐานข้อมูล SQLite ที่เก็บทุก field พร้อม document_id, page, bbox, span และ extraction method
- **Version_Resolver**: ระบบย่อยที่ตัดสินว่าคำถามหนึ่งอ้างถึง curriculum version ใด (program + curriculum_year + edition_status)
- **Gt_Normalizer**: ระบบย่อยที่อ่าน teacher ground truth แบบ read-only แล้วสร้าง normalized ground truth ใน workspace ของโปรเจกต์
- **Gold_Set**: ชุดข้อมูลอ้างอิงที่โปรเจกต์สร้างเอง ครอบคลุมสิ่งที่ teacher ground truth วัดไม่ได้
- **Evaluation_Harness**: ระบบย่อยที่คำนวณ metric ทุกตัวและออก evaluation report
- **Lexical_Index**: ดัชนี SQLite FTS5 BM25 ของ chunk
- **Dense_Index**: ดัชนี embedding แบบ exact scan ที่สร้างจากโมเดล bge-m3
- **Hybrid_Retriever**: ระบบย่อยที่รวมผล Lexical_Index และ Dense_Index เป็นอันดับเดียว
- **Phrase_Booster**: ระบบย่อยที่เพิ่มน้ำหนักผลลัพธ์ตาม domain lexicon ของหลักสูตร
- **MaxSim_Reranker**: ระบบย่อยที่จัดอันดับใหม่ด้วย late-interaction MaxSim scoring
- **Evidence_Planner**: ระบบย่อยที่สร้าง bounded multi-hop evidence DAG
- **Gain_Cost_Halter**: ระบบย่อยตัดสินใจหยุดวนซ้ำโดยเทียบ gain กับ cost × tau พร้อม oscillation patience และ l_min floor
- **Curriculum_Reasoner**: ระบบย่อยที่คำนวณ prerequisite chain, การนับหน่วยกิต และเกณฑ์สำเร็จการศึกษาแบบ deterministic
- **Question_Router**: ระบบย่อยที่จำแนกคำถามเป็นระดับ L1–L4 และเลือกเส้นทางประมวลผล
- **Answer_Generator**: ระบบย่อยที่เรียก LLM ท้องถิ่น (llama.cpp + Qwen3 4B GGUF Q4) เพื่อเรียบเรียงคำตอบจาก evidence object
- **Citation_Validator**: ระบบย่อยที่ตรวจว่าทุกข้อความในคำตอบมี citation ID ที่ระบบออกให้รองรับ
- **Trace_Recorder**: ระบบย่อยที่บันทึก query_trace ของทุกคำขอ
- **Api_Service**: บริการ FastAPI ที่เปิด endpoint ของ KatRAG_System
- **Web_Ui**: ส่วนติดต่อผู้ใช้แบบเว็บที่เรียก Api_Service
- **OCR candidate**: หน้าที่ Page_Quality_Gate ตัดสินว่าคุณภาพ text layer ไม่ผ่านเกณฑ์
- **citation ID**: ตัวระบุหลักฐานที่ KatRAG_System ออกให้ ผูกกับ document_id + page + bbox + chunk hash
- **curriculum version**: คู่ค่า (program, curriculum_year, edition_status) โดย edition_status มีค่า `old` หรือ `current`
- **measured fact**: ตัวเลขที่ได้จากการวัดบนข้อมูลจริงของโปรเจกต์และมี artifact การวัดกำกับ
- **architectural estimate**: ตัวเลขที่ได้จากการประมาณเชิงสถาปัตยกรรมและยังไม่มี artifact การวัด
- **L1 question**: คำถามค้นค่าเดียวจากเอกสารเดียว เช่น หน่วยกิตของวิชาหนึ่ง
- **L2 question**: คำถามรวมค่า/กรองภายในหลักสูตรเดียว เช่น รายวิชาในปี 2 ภาค 1
- **L3 question**: คำถามที่ต้องเชื่อมโยงหลายหลักฐาน เช่น สายวิชาก่อนของวิชาหนึ่ง
- **L4 question**: คำถามเปรียบเทียบข้ามหลักสูตรหรือข้ามเวอร์ชัน เช่น ความต่างของเกณฑ์สำเร็จการศึกษาระหว่างสองฉบับ

## หมายเหตุสถานะของเกณฑ์ตัวเลข

เกณฑ์ตัวเลขในเอกสารนี้แบ่งเป็นสองสถานะตามหลักการ measured fact / architectural estimate

- **เกณฑ์ที่กำหนดไว้แล้วในเป้าหมาย G1–G5**: field macro-F1 ≥ 0.91, citation page precision ≥ 0.95, citation recall ≥ 0.91, unsupported-claim rate < 0.05
- **เกณฑ์ที่โปรเจกต์ตั้งเพิ่มและมีสถานะ `estimate` จนกว่าจะมีผลวัดบน Gold_Set**: page CER ≤ 0.05, table-cell F1 ≥ 0.90, Recall@10 ≥ 0.90, version-selection accuracy ≥ 0.98, resident memory ≤ 6 GB

ตัวเลขที่วัดจากไฟล์จริงแล้ว (measured fact) ได้แก่ 14 เอกสาร, 3,689 หน้า, หน้าที่มีข้อความน้อยกว่า 120 ตัวอักษร 979 หน้า (26.5%) และคู่เอกสารที่มี SHA-256 เท่ากัน

## Requirements

### Requirement 1: ตัวตนเอกสารและ dataset manifest

**User Story:** As a นักพัฒนาระบบ, I want ให้ทุกเอกสารมีตัวตนที่ตรวจสอบย้อนกลับได้, so that ทุกข้อมูลที่สกัดออกมาสามารถอ้างกลับไปยังไฟล์ต้นฉบับที่ระบุได้แน่นอน

#### Acceptance Criteria

1. WHEN Ingestion_Manager เริ่มประมวลผลไฟล์ PDF หนึ่งไฟล์, THE Document_Registry SHALL บันทึก document record หนึ่งรายการที่มี document_id ที่ไม่ซ้ำกับรายการอื่นในฐานข้อมูล, relative path เทียบกับ `project/Information_Technology_Course/`, SHA-256 เป็น hex ตัวพิมพ์เล็ก 64 อักขระ, ขนาดไฟล์เป็นจำนวนเต็มไบต์ และจำนวนหน้าเป็นจำนวนเต็มไม่น้อยกว่า 1
2. THE Document_Registry SHALL บันทึกเอกสารครบ 14 รายการ และผลรวมจำนวนหน้าของทุกรายการเท่ากับ 3,689 หน้า
3. IF จำนวน document record ที่บันทึกได้ไม่เท่ากับ 14 หรือผลรวมจำนวนหน้าไม่เท่ากับ 3,689, THEN THE Ingestion_Manager SHALL สร้าง review_issue ชนิด `dataset_scope_mismatch` ที่บันทึกทั้งค่าที่นับได้จริงและค่าที่คาดไว้ พร้อม relative path ของไฟล์ที่อ่านไม่สำเร็จทุกไฟล์ และจบงาน ingestion ด้วยสถานะไม่สำเร็จโดยคง document record ที่บันทึกไว้แล้ว
4. WHEN Document_Registry พบเอกสารตั้งแต่สองรายการที่มี SHA-256 เท่ากัน, THE Document_Registry SHALL สร้าง review_issue ชนิด `duplicate_content` ที่อ้างถึง document_id ทุกรายการในกลุ่ม, เลือก document_id ที่มี relative path เรียงลำดับตามรหัสอักขระน้อยที่สุดเป็น canonical document, ประมวลผลเนื้อหาเพียงครั้งเดียวจาก canonical document และผูกผลลัพธ์ที่สกัดได้กับ document_id ทุกรายการในกลุ่มนั้น
5. THE Document_Registry SHALL บันทึก review_issue ชนิด `duplicate_content` สำหรับคู่ `M_AITBA2569_current.pdf` และ `PH_D_AITBA2569_current.pdf`
6. WHEN Document_Registry กำหนดค่า program, curriculum_year, degree_level และ edition_status ของเอกสาร, THE Document_Registry SHALL กำหนด curriculum_year เป็นปีพุทธศักราชสี่หลัก, edition_status เป็น `old` หรือ `current`, degree_level เป็น `bachelor`, `master` หรือ `doctoral` และบันทึกแหล่งที่มาของแต่ละค่าเป็น document_id, เลขหน้า และ bbox ของข้อความที่ใช้ตัดสิน พร้อม provenance source เป็น `document_text`
7. IF Document_Registry ไม่พบข้อความภายในเอกสารที่ระบุค่า program, curriculum_year, degree_level หรือ edition_status ได้, THEN THE Document_Registry SHALL ใช้ค่าที่สื่อโดยชื่อไฟล์หรือชื่อโฟลเดอร์, บันทึก provenance source ของ field นั้นเป็น `filename` และสร้าง review_issue ชนิด `metadata_unresolved` ที่ระบุ document_id และชื่อ field ที่หาแหล่งที่มาในเอกสารไม่ได้
8. IF ค่า program, curriculum_year, degree_level หรือ edition_status ที่ตัดสินจากเนื้อหาเอกสารต่างจากค่าที่สื่อโดยชื่อไฟล์หรือชื่อโฟลเดอร์, THEN THE Document_Registry SHALL ใช้ค่าที่ตัดสินจากเนื้อหาเอกสาร และสร้าง review_issue ชนิด `metadata_conflict` ที่บันทึกชื่อ field, ค่าจากเนื้อหาเอกสารพร้อมเลขหน้าและ bbox และค่าจากชื่อไฟล์หรือชื่อโฟลเดอร์
9. WHEN Ingestion_Manager จบงาน ingestion, THE Ingestion_Manager SHALL ผลิตไฟล์ dataset manifest ที่มีหนึ่ง entry ต่อหนึ่ง document record ครบทุกรายการที่บันทึกไว้ โดยแต่ละ entry ระบุ document_id, relative path, SHA-256, จำนวนหน้า, curriculum version, degree_level และรายการ review_issue ที่อ้างถึงเอกสารนั้น ทุกค่าเท่ากับค่าที่บันทึกใน Provenance_Store, เรียง entry ตาม relative path และการผลิตซ้ำจากชุดไฟล์เดิม SHALL ให้เนื้อหา manifest เหมือนเดิมทุกครั้ง

### Requirement 2: การสกัดข้อความแบบ text-first

**User Story:** As a นักพัฒนาระบบ, I want ให้ระบบใช้ text layer ของ born-digital PDF เป็นแหล่งข้อมูลหลัก, so that ระบบได้ข้อความที่แม่นยำกว่าและใช้ทรัพยากรน้อยกว่าการ OCR ทุกหน้า

#### Acceptance Criteria

1. WHEN Ingestion_Manager ประมวลผลหน้าหนึ่ง, THE Text_Extractor SHALL ดึง per-character record ของทุก glyph ที่ปรากฏใน text layer ของหน้านั้น โดยแต่ละ record มี unicode codepoint, bbox เป็นค่า (x0, y0, x1, y1) ในระบบพิกัดของหน้านั้น, ชื่อ font, ขนาด font และค่า baseline ให้ครบทุกฟิลด์ และ SHALL ดึงเสร็จก่อนเรียก Thai_Glyph_Reorderer, Page_Quality_Gate หรือ Ocr_Cascade ของหน้านั้น
2. WHEN Text_Extractor ดึง per-character record ของหน้าหนึ่งเสร็จ, THE Text_Extractor SHALL บันทึก char_count และ image_count ของหน้านั้นเป็นจำนวนเต็มไม่ติดลบลงใน Provenance_Store และเมื่อประมวลผล dataset ครบทั้งชุด จำนวนหน้าที่มีทั้งสองค่าครบถ้วน SHALL เท่ากับ 3,689 หน้า
3. IF Page_Quality_Gate ไม่ได้ทำเครื่องหมายหน้าหนึ่งเป็น OCR candidate, THEN THE Ingestion_Manager SHALL ใช้ข้อความจาก Text_Extractor เป็นข้อความของหน้านั้น, SHALL บันทึก extraction_method เป็น `text_layer` และจำนวนการเรียก Ocr_Cascade สำหรับหน้านั้น SHALL เท่ากับศูนย์
4. IF PyMuPDF อ่านหน้าใดไม่ได้, THEN THE Text_Extractor SHALL บันทึก error record ที่มี document_id, เลขหน้า และข้อความ error ที่ระบุสาเหตุ, SHALL ไม่บันทึกข้อความบางส่วนของหน้านั้นเป็นผลลัพธ์ของหน้า และ SHALL ประมวลผลหน้าถัดไปต่อ
5. IF PyMuPDF เปิดไฟล์เอกสารหนึ่งไม่ได้, THEN THE Text_Extractor SHALL บันทึก error record ระดับเอกสารที่มี document_id และข้อความ error ที่ระบุสาเหตุ และ THE Ingestion_Manager SHALL ประมวลผลเอกสารถัดไปต่อโดยไม่ยุติการประมวลผล dataset ทั้งชุด
6. IF หน้าหนึ่งมี char_count เท่ากับศูนย์, THEN THE Text_Extractor SHALL บันทึกหน้านั้นด้วย char_count เท่ากับศูนย์โดยไม่ถือเป็น error record และ SHALL ส่งหน้านั้นให้ Page_Quality_Gate ตัดสินต่อ

### Requirement 3: การจัดลำดับ glyph ภาษาไทยด้วย geometry

**User Story:** As a ผู้ใช้ที่ค้นข้อมูลภาษาไทย, I want ให้ข้อความไทยที่สกัดได้มีสระและวรรณยุกต์อยู่ในลำดับที่ถูกต้อง, so that การค้นหาและการอ่านคำตอบไม่ผิดพลาดจากคำที่แตก

#### Acceptance Criteria

1. WHEN Thai_Glyph_Reorderer รับ per-character record (bbox, ชื่อ font, ขนาด font, baseline) ของหน้าหนึ่งจาก Text_Extractor, THE Thai_Glyph_Reorderer SHALL จัดลำดับ glyph ภายในแต่ละ cluster ตามลำดับ base consonant → below vowel (U+0E38 ถึง U+0E3A) → above vowel (U+0E31, U+0E34 ถึง U+0E37, U+0E47) → tone mark (U+0E48 ถึง U+0E4B) → sign อื่นในช่วง U+0E4C ถึง U+0E4E และเมื่อมี glyph มากกว่าหนึ่งตัวอยู่ในคลาสเดียวกัน SHALL เรียงตามลำดับที่ปรากฏใน input เป็น tie-break
2. WHEN glyph หนึ่งมีความกว้าง bbox ไม่เกิน 0.5 point และ codepoint อยู่ในช่วง U+0E30 ถึง U+0E4E, THE Thai_Glyph_Reorderer SHALL ผูก glyph นั้นกับ base consonant ที่มีระยะห่างแนวนอนระหว่างจุดกึ่งกลาง bbox น้อยที่สุด โดยพิจารณาเฉพาะ base consonant ที่มีผลต่างค่า baseline ไม่เกิน 20% ของขนาด font ของ glyph นั้น และอยู่ภายในระยะแนวนอนไม่เกิน 1.5 เท่าของขนาด font นั้น และ IF มีระยะห่างเท่ากันหลายตัว, THEN SHALL เลือกตัวที่อยู่ทางซ้าย
3. WHEN Thai_Glyph_Reorderer ประมวลผลข้อความของหน้าหนึ่ง, THE Thai_Glyph_Reorderer SHALL ลบอักขระ whitespace (U+0020, U+00A0, U+0009) ทุกตัวที่อยู่ระหว่างอักขระในช่วง U+0E00 ถึง U+0E7F กับ combining mark ในช่วง U+0E30 ถึง U+0E4E ที่ตามมา และ SHALL ไม่ลบ whitespace ในตำแหน่งอื่น
4. WHEN Thai_Glyph_Reorderer ประมวลผลข้อความของหน้าหนึ่งเสร็จ, THE Thai_Glyph_Reorderer SHALL ผลิตข้อความที่มีจำนวนตำแหน่งซึ่งตรงกับ pattern `[\u0e00-\u0e7f]\s+[\u0e30-\u0e4e]` เท่ากับศูนย์
5. WHEN Thai_Glyph_Reorderer ถูกเรียกด้วย input ชุดเดียวกัน 3 ครั้ง, THE Thai_Glyph_Reorderer SHALL คืนข้อความที่เท่ากันทุกอักขระทั้ง 3 ครั้ง (deterministic) และ SHALL คืนข้อความที่เท่ากันทุกอักขระกับผลลัพธ์ครั้งแรกเมื่อถูกเรียกซ้ำบนผลลัพธ์ของตัวเอง (idempotence)
6. WHEN Line_Assembler รับ glyph ของหน้าหนึ่งจาก Thai_Glyph_Reorderer, THE Line_Assembler SHALL จัดกลุ่ม glyph ที่มีผลต่างค่า baseline ไม่เกิน 30% ของขนาด font ที่ใหญ่ที่สุดในกลุ่มนั้นเป็นบรรทัดเดียวกัน, SHALL เรียง glyph ภายในบรรทัดตามพิกัด X จากน้อยไปมาก และ SHALL เรียงบรรทัดตามพิกัด Y จากบนลงล่างของหน้า โดยใช้ลำดับที่ปรากฏใน input เป็น tie-break เมื่อค่าพิกัดเท่ากัน
7. WHEN Line_Assembler ประกอบบรรทัดของหน้าหนึ่งเสร็จ, THE Line_Assembler SHALL ให้ multiset ของ codepoint ที่ไม่ใช่ whitespace ในข้อความผลลัพธ์เท่ากับ multiset ของ codepoint ที่ไม่ใช่ whitespace ที่ Thai_Glyph_Reorderer ส่งเข้ามาทุกตัว
8. THE Evaluation_Harness SHALL คำนวณ page CER ของทุกหน้าใน Gold_Set เป็นจำนวนการแทรก ลบ และแทนที่อักขระที่น้อยที่สุดระหว่างข้อความหลังการจัดลำดับกับข้อความอ้างอิงของหน้านั้น หารด้วยจำนวนอักขระของข้อความอ้างอิง โดยเทียบหลัง Unicode normalization form C และ SHALL รายงานค่าเฉลี่ยของ page CER ทุกหน้าพร้อมสถานะเกณฑ์ `estimate` โดยค่าเฉลี่ยต้องไม่มากกว่า 0.05
9. IF ไม่พบ base consonant ที่เข้าเงื่อนไขระยะ baseline และระยะแนวนอนสำหรับ combining mark หนึ่ง, THEN THE Thai_Glyph_Reorderer SHALL คงตำแหน่งเดิมของ glyph นั้นไว้โดยไม่ลบ glyph, และ SHALL บันทึก review_issue ชนิด `thai_reorder_unresolved` ที่ระบุ document_id, เลขหน้า, codepoint และ bbox ของ glyph นั้น
10. IF multiset ของ codepoint ที่ไม่ใช่ whitespace หลังประกอบบรรทัดไม่เท่ากับของ glyph ที่รับเข้ามา, THEN THE Line_Assembler SHALL บันทึก review_issue ชนิด `glyph_count_mismatch` ที่ระบุ document_id, เลขหน้า, จำนวน glyph ที่คาดหวัง และจำนวน glyph ที่พบ, และ SHALL คงข้อความของหน้านั้นไว้ใน Provenance_Store โดยไม่ยกเลิกผลของหน้าอื่น

### Requirement 4: การตัดสินคุณภาพหน้าและการเรียก OCR แบบมีเงื่อนไข

**User Story:** As a เจ้าของโปรเจกต์, I want ให้ OCR ถูกเรียกเฉพาะหน้าที่จำเป็น, so that งาน ingestion เสร็จในเวลาและทรัพยากรที่จำกัดของเครื่องเดียว

#### Acceptance Criteria

1. WHEN Text_Extractor ส่งผลของหน้าหนึ่งให้ Page_Quality_Gate, THE Page_Quality_Gate SHALL คำนวณ page_quality_score เป็นค่าทศนิยมในช่วง 0.00 ถึง 1.00 จากตัวชี้วัดสี่ตัว ได้แก่ extracted_char_count, out_of_charset_ratio (สัดส่วนอักขระที่อยู่นอกชุดอักขระไทย–ละติน–ตัวเลข–เครื่องหมายวรรคตอนที่ประกาศไว้), image_area_ratio (สัดส่วนพื้นที่ภาพต่อพื้นที่หน้า) และ domain_lexicon_match_count โดยใช้น้ำหนักที่ประกาศไว้ในไฟล์ตั้งค่า
2. THE Page_Quality_Gate SHALL บันทึกค่าตัวชี้วัดทั้งสี่ตัวและ page_quality_score ลง Provenance_Store ครบทุกหน้าของทั้ง 3,689 หน้า และ SHALL ให้ค่าเดิมทุกครั้งเมื่อ input ของหน้าเดิมไม่เปลี่ยน (deterministic)
3. IF extracted_char_count ของหน้าหนึ่งน้อยกว่า 120 ตัวอักษร และหน้านั้นมีภาพอย่างน้อย 1 ภาพ, THEN THE Page_Quality_Gate SHALL ทำเครื่องหมายหน้านั้นเป็น OCR candidate พร้อมเหตุผล `low_text_with_image`
4. IF extracted_char_count ของหน้าหนึ่งน้อยกว่า 120 ตัวอักษร และหน้านั้นไม่มีภาพ, THEN THE Page_Quality_Gate SHALL ไม่ทำเครื่องหมายหน้านั้นเป็น OCR candidate และ SHALL บันทึก review_issue ชนิด `low_content_page` ที่อ้าง document_id และเลขหน้า
5. THE Page_Quality_Gate SHALL ทำเครื่องหมายเป็น OCR candidate ไม่เกิน 979 หน้าจาก 3,689 หน้าของ dataset นี้ (ไม่เกิน 26.5%)
6. IF จำนวนหน้าที่ถูกทำเครื่องหมายเป็น OCR candidate สะสมถึง 979 หน้า, THEN THE Page_Quality_Gate SHALL ไม่ทำเครื่องหมายหน้าเพิ่มอีก และ SHALL บันทึก review_issue ชนิด `ocr_budget_exhausted` ที่ระบุจำนวนหน้าที่เหลือซึ่งเข้าเกณฑ์แต่ไม่ได้เข้าคิว OCR โดยคงผลลัพธ์ที่บันทึกไว้แล้วไม่ถูกลบ
7. WHEN หน้าหนึ่งถูกทำเครื่องหมายเป็น OCR candidate, THE Ocr_Page_Router SHALL กำหนด compute path หนึ่งค่าจาก `fast`, `standard` หรือ `deep` ตามกฎที่กำหนดค่าเดียวต่อหน้า คือ `fast` เมื่อ image_area_ratio ไม่เกิน 0.30, `deep` เมื่อ extracted_char_count เท่ากับ 0 หรือ image_area_ratio ไม่น้อยกว่า 0.60 และ `standard` ในกรณีที่เหลือ
8. WHEN Ocr_Page_Router กำหนด compute path ของหน้าหนึ่ง, THE Ocr_Page_Router SHALL บันทึก compute path, ค่าตัวชี้วัดที่ใช้ตัดสิน และรหัสเหตุผลการกำหนด ลง Provenance_Store
9. THE Ingestion_Manager SHALL บันทึกลง evaluation report ต่อทุกเอกสารและต่อทั้ง dataset: จำนวนหน้าที่เป็น OCR candidate, จำนวนหน้าที่เข้าสู่ OCR จริง, สัดส่วนต่อจำนวนหน้าทั้งหมด และจำนวนหน้าแยกตาม compute path
10. WHEN Api_Service ประมวลผลคำถามหนึ่งคำขอ, THE KatRAG_System SHALL ใช้เฉพาะข้อมูลที่บันทึกไว้แล้วใน Provenance_Store และ THE Trace_Recorder SHALL บันทึกจำนวนการเรียก Ocr_Cascade, Preprocessor และ Region_Adjudicator ของคำขอนั้นลง query_trace โดยทั้งสามค่าต้องเท่ากับศูนย์

### Requirement 5: OCR cascade พร้อม escalation แบบ gain–cost

**User Story:** As a นักพัฒนาระบบ, I want ให้การยกระดับ OCR หยุดเมื่อผลตอบแทนไม่คุ้มต้นทุน, so that ระบบไม่เสียเวลาประมวลผลหน้าที่ปรับปรุงต่อไม่ได้

#### Acceptance Criteria

1. THE Ocr_Cascade SHALL เรียก OCR engine ตามลำดับขั้นคงที่คือ stage 1 = Tesseract 5 และ stage 2 = Typhoon-OCR-1.5-2B โดยไม่เรียก engine อื่นใด และไม่เกิน 2 stage ต่อ region (หมายเหตุ: PaddleOCR PP-OCRv5 ที่ระบุไว้เดิมไม่รองรับภาษาไทยในเวอร์ชันที่ pin ไว้ ยืนยันจากการทดสอบจริงบนเครื่องเป้าหมาย จึงถอดออกจาก cascade และบันทึกเป็น known-limitation แทนการฝืนใช้)
1.1. WHERE เครื่องที่รันไม่มี GPU ที่รองรับ CUDA, THE Ocr_Cascade SHALL ข้าม stage 2 (Typhoon-OCR-1.5-2B) ทุก region และใช้ผลของ stage 1 (Tesseract 5) เป็นผลสุดท้ายเสมอ โดยไม่ถือเป็นข้อผิดพลาด (รองรับ R20.7 ที่บังคับให้ระบบผ่านเกณฑ์บน CPU-only ได้)
1.2. IF ข้อความที่ Typhoon-OCR-1.5-2B คืนมามีชื่อสถาบันการศึกษาที่ไม่ตรงกับ "สถาบันเทคโนโลยีพระจอมเกล้าเจ้าคุณทหารลาดกระบัง" ที่ประกาศไว้ในไฟล์ตั้งค่าว่าเป็นสถาบันเดียวที่ปรากฏในคลังเอกสารนี้, THEN THE Ocr_Cascade SHALL ลดคะแนนคุณภาพของผลลัพธ์ stage นั้นลงเหลือ 0.00 และบันทึก error record ที่ระบุว่าเป็น `hallucinated_institution_name` พร้อมข้อความที่พบ (ป้องกันการ hallucinate ชื่อสถาบันอื่นที่ยืนยันแล้วว่าเกิดขึ้นจริงกับโมเดลนี้)
1.3. THE Ocr_Cascade SHALL จำกัดจำนวน token ที่ Typhoon-OCR-1.5-2B สร้างต่อ region ไว้ไม่เกินค่าที่อ่านจากไฟล์ตั้งค่า และ SHALL ใช้ repetition penalty กับ no-repeat n-gram size ตามค่าที่อ่านจากไฟล์ตั้งค่า เพื่อจำกัดผลกระทบของการวนซ้ำข้อความที่ยืนยันแล้วว่าเกิดขึ้นจริงกับโมเดลนี้บนหน้าที่มีข้อความน้อย
2. WHEN Ocr_Cascade จบ stage หนึ่ง, THE Gain_Cost_Halter SHALL คำนวณ gain เป็นผลต่างของคะแนนคุณภาพ (สเกล 0.00–1.00) ระหว่าง stage ล่าสุดกับ stage ที่ดีที่สุดก่อนหน้า และ cost เป็นเวลาประมวลผลของ stage ล่าสุดหารด้วย per-page time budget แล้วคืนคำตัดสิน `halt` เมื่อ gain น้อยกว่า cost คูณ tau และจำนวน stage ที่ทำแล้วไม่น้อยกว่า l_min โดยค่าเริ่มต้น tau = 1.0 และ l_min = 1 อ่านจากไฟล์ตั้งค่า
3. WHEN คะแนนคุณภาพของหน้าเดียวกันเปลี่ยนทิศทาง (เพิ่มสลับลด) ติดต่อกันครบ 2 ครั้ง ซึ่งเท่ากับค่า oscillation patience เริ่มต้น, THE Gain_Cost_Halter SHALL คืนคำตัดสิน `halt` และบันทึกเหตุผล `oscillation` ลง Provenance_Store
4. IF ค่า gain หรือ cost เป็น NaN หรือ infinity, THEN THE Gain_Cost_Halter SHALL คืนคำตัดสิน `halt` ถือว่า gain เท่ากับ 0.00 และบันทึกเหตุผล `nan_guard` ลง Provenance_Store โดยคงผลลัพธ์ของ stage ที่ทำสำเร็จแล้วไว้
5. WHEN Gain_Cost_Halter คืนคำตัดสิน `halt` ของหน้าหนึ่ง, THE Ocr_Cascade SHALL ไม่เรียก OCR engine stage ถัดไปของหน้านั้น ใช้ผลลัพธ์ที่มีคะแนนคุณภาพสูงสุดจาก stage ที่ทำสำเร็จแล้วเป็นผลสุดท้าย และบันทึกจำนวน stage ที่ทำ, ค่า gain, ค่า cost และเหตุผลการหยุด ลง Provenance_Store
6. IF OCR engine ของ stage หนึ่งคืน error หรือใช้เวลาเกิน hard timeout ที่กำหนดต่อ engine ต่อ region (Tesseract 5 = 15 วินาที, Typhoon-OCR-1.5-2B = 300 วินาที อ่านจากไฟล์ตั้งค่า `[ocr.stage_timeout]`), THEN THE Ocr_Cascade SHALL ยกเลิก stage นั้น บันทึก error record ที่มี document_id, page, bbox และเหตุผลที่ระบุว่าเกิด engine timeout หรือ engine error ลง Provenance_Store แล้วใช้ผลลัพธ์คะแนนสูงสุดจาก stage ที่สำเร็จ หรือทำเครื่องหมาย region นั้นเป็น `ocr_failed` เมื่อไม่มี stage ใดสำเร็จ และประมวลผล region ถัดไปต่อ (หมายเหตุ: ค่าเดิม 10 วินาทีเท่ากันทุก engine ใช้ไม่ได้จริง — Typhoon วัดจริงเฉลี่ย 126 วินาที/region ทุก timeout จะ fire 100% ทำให้ stage 2 ไม่มีวันได้ผลลัพธ์ จึงแยกเป็น per-engine timeout ตามข้อมูล benchmark จริง)
6.1. THE Ocr_Cascade SHALL จำกัดเวลารวมของ stage 2 (Typhoon-OCR-1.5-2B) ต่อ ingestion run ไว้ไม่เกินค่าที่อ่านจากไฟล์ตั้งค่า (`ocr.escalation.max_typhoon_seconds_per_run`) เมื่อใช้ครบ SHALL ข้าม stage 2 ของทุก region ที่เหลือ บันทึกเหตุผล `budget_exhausted` และใช้ผลของ stage 1 เป็นผลสุดท้าย
6.2. IF Typhoon-OCR-1.5-2B คืน error หรือ timeout ติดต่อกันครบจำนวนครั้งที่อ่านจากไฟล์ตั้งค่า (`ocr.escalation.max_consecutive_typhoon_failures`), THEN THE Ocr_Cascade SHALL เปิด circuit breaker และข้าม stage 2 ทุก region ที่เหลือใน run เดียวกัน บันทึกเหตุผล `circuit_breaker`
6.3. IF ผลของ stage 1 มีคะแนนคุณภาพไม่น้อยกว่าค่าที่อ่านจากไฟล์ตั้งค่า (`ocr.escalation.min_stage1_quality_for_skip`), THEN THE Ocr_Cascade SHALL ข้าม stage 2 ของ region นั้นโดยไม่ถือเป็นข้อผิดพลาด บันทึกเหตุผล `quality_sufficient`
7. WHERE compute path ของหน้าเป็น `deep`, THE Preprocessor SHALL ปรับภาพก่อนส่งเข้า OCR engine และบันทึกชื่อขั้นตอนการปรับภาพทุกขั้นตามลำดับที่ใช้ลง Provenance_Store โดยบันทึกรายการว่างเมื่อไม่มีขั้นตอนใดถูกใช้
8. WHEN Preprocessor ปรับภาพของ region หนึ่ง, THE Ocr_Cascade SHALL เปรียบเทียบคะแนนคุณภาพ (สเกล 0.00–1.00) ของผลลัพธ์ก่อนและหลังการปรับภาพ และเลือกผลลัพธ์ที่มีคะแนนสูงกว่า โดยเลือกผลลัพธ์ก่อนการปรับภาพเมื่อคะแนนทั้งสองเท่ากัน
9. THE Preprocessor SHALL ปรับภาพเฉพาะเมื่ออย่างน้อยหนึ่งเงื่อนไขต่อไปนี้เป็นจริง: มุมเอียงที่วัดได้ของ region มากกว่า 1.0 องศา, ความละเอียดของภาพ region น้อยกว่า 300 DPI, หรือคะแนน contrast ของ region น้อยกว่า 0.30 บนสเกล 0.00–1.00 และ SHALL ส่งภาพต้นฉบับเข้า OCR engine โดยไม่ปรับภาพเมื่อไม่มีเงื่อนไขใดเป็นจริง
10. WHEN OCR engine มากกว่าหนึ่งตัวคืนผลของ region ที่มีค่า IoU ของ bbox ไม่น้อยกว่า 0.50, THE Region_Adjudicator SHALL เลือกผลลัพธ์ด้วย spatial voting โดยใช้คะแนนความเชื่อมั่นของ engine เป็นเกณฑ์หลัก เลือกผลจาก stage ที่ลำดับต้นกว่าเมื่อคะแนนความเชื่อมั่นต่างกันไม่เกิน 0.01 และบันทึกผลของทุก engine พร้อมผลที่เลือกลง Provenance_Store
11. WHEN Ocr_Cascade ได้รับ region ที่มี SHA-256 ของภาพ crop ตรงกับรายการที่ประมวลผลแล้วภายใน ingestion run เดียวกัน และใช้ engine stage กับลำดับขั้นตอนการปรับภาพเดียวกัน, THE Ocr_Cascade SHALL คืนผลลัพธ์จาก cache ที่เหมือนกับผลลัพธ์เดิมทุกฟิลด์แทนการเรียก OCR engine ซ้ำ โดยคงจำนวนรายการใน cache ไม่เกิน 2,000 รายการต่อเอกสาร

### Requirement 6: การใช้หน่วยความจำและการประมวลผลแบบสตรีม

**User Story:** As a ผู้ใช้ที่รันบนโน้ตบุ๊กเครื่องเดียว, I want ให้ ingestion ไม่กินหน่วยความจำจนเครื่องหยุดทำงาน, so that ประมวลผลเอกสาร 3,689 หน้าได้จนจบ

#### Acceptance Criteria

1. WHEN Ingestion_Manager ต้องการภาพของหน้าหนึ่งเพื่อส่งเข้า Ocr_Cascade, THE Ingestion_Manager SHALL แปลงเป็นภาพเฉพาะหน้านั้น และคงจำนวน page image ที่ถืออยู่ในหน่วยความจำพร้อมกันไม่เกิน 2 หน้าตลอดการประมวลผลทั้ง 3,689 หน้า
2. WHEN Ingestion_Manager ประมวลผลหน้าหนึ่งเสร็จ, THE Ingestion_Manager SHALL คืนหน่วยความจำของ page image และ intermediate tensor ของหน้านั้นก่อนเริ่มแปลงหน้าถัดไป
3. WHEN Ocr_Cascade เริ่มประมวลผลหน้าถัดไป, THE Ocr_Cascade SHALL นำ buffer สำหรับข้อมูลภาพและ tensor ที่ผู้เรียกเป็นเจ้าของและจัดสรรไว้แล้วกลับมาใช้ซ้ำ โดยค่า resident memory ที่วัดหลังประมวลผลหน้าใด ๆ ต้องไม่เกินค่าที่วัดหลังหน้าที่ 50 มากกว่า 5%
4. THE Ingestion_Manager SHALL ประมวลผลทุกหน้าของแต่ละเอกสารตั้งแต่หน้าที่ 1 ถึงจำนวนหน้าที่ Document_Registry บันทึกไว้ โดยรับช่วงหน้าจาก metadata ของเอกสารเท่านั้น และผลรวมจำนวนหน้าที่มี page record ใน Provenance_Store เท่ากับ 3,689 หน้า
5. WHEN Ingestion_Manager ประมวลผล dataset ทั้งชุด (14 เอกสาร 3,689 หน้า) จนจบ, THE Ingestion_Manager SHALL คง peak resident memory ของกระบวนการไว้ไม่เกิน 6 GB โดยวัดค่า resident memory ทุกครั้งที่ประมวลผลหน้าหนึ่งเสร็จ และบันทึกค่า peak ลง evaluation report พร้อมสถานะเกณฑ์ `estimate`
6. IF ค่า resident memory ที่วัดได้เกิน 6 GB, THEN THE Ingestion_Manager SHALL บันทึกผลของหน้าที่กำลังประมวลผลให้เสร็จแล้วหยุดการประมวลผล, สร้าง review_issue ชนิด `memory_limit_exceeded` ที่ระบุ document_id, เลขหน้า และค่าที่วัดได้, และคงผลลัพธ์ของหน้าที่เสร็จแล้วทั้งหมดไว้ใน Provenance_Store
7. WHEN Ingestion_Manager ประมวลผลหน้าหนึ่งเสร็จ, THE Provenance_Store SHALL บันทึกผลของหน้านั้นพร้อมสถานะ `page_complete` เป็นหน่วยเดียวแบบ atomic ก่อนที่ Ingestion_Manager จะเริ่มหน้าถัดไป
8. IF Ingestion_Manager เริ่มทำงานใหม่หลังถูกขัดจังหวะ (สัญญาณหยุดจากผู้ใช้, กระบวนการจบผิดปกติ, หรือการหยุดจากเกณฑ์หน่วยความจำ), THEN THE Ingestion_Manager SHALL ประมวลผลเฉพาะหน้าที่ไม่มี record สถานะ `page_complete` ใน Provenance_Store โดยจำนวนการเรียก Ocr_Cascade สำหรับหน้าที่มีสถานะ `page_complete` แล้วเท่ากับศูนย์

### Requirement 7: การสกัดตารางแผนการศึกษา

**User Story:** As a นักศึกษา, I want ให้ระบบอ่านตารางแผนการศึกษาได้ถูกต้องตามคอลัมน์, so that คำตอบเรื่องรายวิชาต่อปี/ภาคการศึกษาไม่สลับค่า

#### Acceptance Criteria

1. THE Table_Extractor SHALL ผลิต cell record หนึ่งรายการต่อหนึ่งเซลล์ของทุกตารางที่ตรวจพบ โดยตารางที่ตรวจพบคือโครงสร้างที่มี header row อย่างน้อย 1 แถว, คอลัมน์ตั้งแต่ 2 คอลัมน์ขึ้นไป และแถวข้อมูลตั้งแต่ 1 แถวขึ้นไป และ cell record แต่ละรายการ SHALL มี row index และ column index ที่เริ่มนับจาก 1, ข้อความของเซลล์, bbox ในระบบพิกัดของหน้านั้น, document_id และ document page
2. THE Table_Extractor SHALL บันทึกเซลล์ที่ไม่มีข้อความเป็น cell record ที่มีข้อความว่าง แทนการข้ามเซลล์นั้น เพื่อให้จำนวน cell record ต่อแถวเท่ากับจำนวนคอลัมน์ของ header
3. WHEN Table_Extractor ตรวจพบตารางแผนการศึกษา, THE Table_Extractor SHALL ระบุปีการศึกษาเป็นจำนวนเต็มในช่วง 1 ถึง 8 และภาคการศึกษาเป็นจำนวนเต็มในช่วง 1 ถึง 3 ที่ตารางนั้นสังกัด พร้อม document page และ bbox ของข้อความที่ใช้ระบุค่าทั้งสอง
4. IF Table_Extractor ไม่พบข้อความที่ระบุปีการศึกษาหรือภาคการศึกษาของตารางแผนการศึกษา หรือพบค่าที่ขัดแย้งกันมากกว่าหนึ่งค่า, THEN THE Table_Extractor SHALL คง cell record ทุกรายการของตารางนั้นไว้โดยไม่กำหนดค่าปีการศึกษาและภาคการศึกษา และ SHALL บันทึก review_issue ชนิด `table_context_unresolved` ที่อ้าง document page และค่าที่พบทั้งหมด
5. IF จำนวน cell ในแถวหนึ่งต่างจากจำนวนคอลัมน์ของ header เมื่อนับ column span แล้ว, THEN THE Table_Extractor SHALL บันทึก review_issue ชนิด `table_shape_mismatch` ที่อ้าง document page, เลขแถว, จำนวนคอลัมน์ที่คาดหวัง และจำนวน cell ที่พบ และ SHALL คง cell record ของแถวนั้นและของทุกแถวในตารางเดียวกันไว้ทั้งหมด
6. WHERE เซลล์หนึ่งครอบคลุมหลายแถวหรือหลายคอลัมน์, THE Table_Extractor SHALL บันทึก row span และ column span เป็นจำนวนเต็มตั้งแต่ 1 ขึ้นไปไว้กับ cell record ที่ตำแหน่ง row index และ column index น้อยที่สุดของช่วงที่ครอบคลุม และ SHALL ไม่สร้าง cell record ซ้ำสำหรับตำแหน่งอื่นภายในช่วงเดียวกัน
7. THE Evaluation_Harness SHALL รายงาน table-cell F1 เทียบกับตารางใน Gold_Set โดยถือว่าเซลล์หนึ่งตรงกันเมื่อ document page, row index และ column index เท่ากัน และข้อความหลัง normalize ช่องว่างซ้อนและลำดับ combining mark เท่ากันทุกอักขระ และ table-cell F1 ต้องไม่น้อยกว่า 0.90 (เกณฑ์สถานะ `estimate`)

### Requirement 8: การสกัด field ของหลักสูตรและ parser ของ field ประกอบ

**User Story:** As a ผู้ประเมินระบบ, I want ให้ทุก field ที่โจทย์กำหนดถูกสกัดเป็นโครงสร้างที่ตรวจสอบได้, so that วัดความแม่นยำต่อ field เป็นตัวเลขได้

#### Acceptance Criteria

1. WHEN Field_Extractor ประมวลผลข้อความหรือ cell record ของรายวิชาหนึ่ง, THE Field_Extractor SHALL ผลิต course record หนึ่งรายการที่มี field ต่อไปนี้ครบทั้ง 11 field: `code`, `name_th`, `name_en`, `credits`, `year`, `semester`, `category`, `type`, `prerequisite`, `flexible_year_semester`, `note` โดย `code` เป็นสตริงความยาว 1 ถึง 20 อักขระ, `name_th` และ `name_en` เป็นสตริงความยาว 0 ถึง 255 อักขระ, `year` เป็นจำนวนเต็มในช่วง 1 ถึง 8, `semester` เป็นจำนวนเต็มในช่วง 1 ถึง 3, `category` และ `type` เป็นค่าหนึ่งค่าจากชุดค่าที่ประกาศไว้ในไฟล์ตั้งค่า, `flexible_year_semester` เป็นค่าจริงหรือเท็จ, `note` เป็นสตริงความยาว 0 ถึง 500 อักขระ และ `credits` กับ `prerequisite` เป็นโครงสร้างตามเกณฑ์ข้อ 2 และข้อ 5
2. WHEN Field_Extractor ส่งสตริงหน่วยกิตให้ Credits_Parser, THE Credits_Parser SHALL แปลงสตริงรูปแบบ `total(lecture-lab-self_study)` เป็นโครงสร้างที่มี total, lecture, lab และ self_study เป็นจำนวนเต็มในช่วง 0 ถึง 30 ทุกค่า และ SHALL ให้โครงสร้างผลลัพธ์เท่ากันทุกฟิลด์ทุกครั้งเมื่อ input เป็นสตริงเดิม (deterministic)
3. IF สตริงหน่วยกิตไม่ตรงรูปแบบไวยากรณ์ตามเกณฑ์ข้อ 2 หรือมีค่าใดอยู่นอกช่วง 0 ถึง 30, THEN THE Credits_Parser SHALL คืน error ที่ระบุ index ของอักขระแรกที่ไม่ตรงรูปแบบโดยนับจาก 0 และ THE Field_Extractor SHALL บันทึกค่า `credits` เป็นค่าว่างพร้อมสตริงต้นฉบับ, SHALL ไม่บันทึกค่าตัวเลขบางส่วนของสตริงนั้น และ SHALL บันทึก review_issue ชนิด `credits_parse_error` ที่อ้าง document_id, เลขหน้า, bbox, สตริงต้นฉบับ และ index ที่ไม่ตรงรูปแบบ
4. WHERE โครงสร้างหน่วยกิตถูกสร้างจาก Credits_Parser ได้สำเร็จ, THE Credits_Printer SHALL แปลงโครงสร้างนั้นกลับเป็นสตริงรูปแบบ `total(lecture-lab-self_study)` และการ parse สตริงผลลัพธ์ซ้ำ SHALL ให้โครงสร้างที่มี total, lecture, lab และ self_study เท่ากับโครงสร้างเดิมทุกฟิลด์ (round-trip property)
5. WHEN Field_Extractor ส่งข้อความเงื่อนไขวิชาก่อนให้ Prerequisite_Parser, THE Prerequisite_Parser SHALL แปลงข้อความความยาวไม่เกิน 500 อักขระเป็นโครงสร้าง expression ที่รองรับรายการวิชาแบบ and, ทางเลือกแบบ or และเงื่อนไขว่าง โดยมีรหัสวิชาไม่เกิน 20 รายการต่อ expression และระดับการซ้อนของ and/or ไม่เกิน 3 ระดับ และ SHALL ผลิต expression ว่างเมื่อข้อความเป็นสตริงว่างหรือมีเฉพาะอักขระช่องว่าง
6. WHERE prerequisite expression ถูกสร้างจาก Prerequisite_Parser ได้สำเร็จ, THE Prerequisite_Printer SHALL แปลง expression กลับเป็นข้อความ canonical และการ parse ข้อความผลลัพธ์ซ้ำ SHALL ให้ expression ที่เท่ากันกับ expression เดิมทุก node และทุกลำดับ (round-trip property)
7. WHEN Field_Extractor บันทึกค่า field ใด, THE Provenance_Store SHALL เก็บ document_id, เลขหน้าเป็นจำนวนเต็มตั้งแต่ 1, bbox เป็นค่า (x0, y0, x1, y1) ในระบบพิกัดของหน้านั้น, span ของข้อความต้นทางเป็นคู่ index เริ่มและสิ้นสุดโดยนับจาก 0 และ extraction_method หนึ่งค่าจากชุดค่าที่ประกาศไว้ในไฟล์ตั้งค่า ครบทุกฟิลด์โดยไม่มีฟิลด์ใดว่าง
8. THE Evaluation_Harness SHALL รายงาน precision, recall และ F1 ต่อ field ทั้ง 11 field เทียบกับ Gold_Set โดยถือว่าค่าหนึ่งตรงกันเมื่อรหัสวิชาและชื่อ field เท่ากัน และค่าหลัง normalize ช่องว่างซ้อนและลำดับ combining mark เท่ากันทุกอักขระ, SHALL นับ field ที่ไม่ถูกบันทึกเป็น false negative และ SHALL รายงาน field macro-F1 เป็นค่าเฉลี่ยไม่ถ่วงน้ำหนักของ F1 ทั้ง 11 field โดย field macro-F1 ต้องไม่น้อยกว่า 0.91
9. IF ข้อความเงื่อนไขวิชาก่อนไม่ตรงไวยากรณ์ตามเกณฑ์ข้อ 5 หรือเกินขอบเขตความยาว จำนวนรหัสวิชา หรือระดับการซ้อนที่กำหนด, THEN THE Prerequisite_Parser SHALL คืน error ที่ระบุ index ของอักขระแรกที่ไม่ตรงรูปแบบโดยนับจาก 0 และ THE Field_Extractor SHALL บันทึกค่า `prerequisite` เป็นค่าว่างพร้อมข้อความต้นฉบับ และ SHALL บันทึก review_issue ชนิด `prerequisite_parse_error` ที่อ้าง document_id, เลขหน้า, bbox และข้อความต้นฉบับ
10. IF Field_Extractor ไม่พบข้อความต้นทางของ field ใดของรายวิชาหนึ่ง หรือพบค่าที่ขัดแย้งกันมากกว่าหนึ่งค่าสำหรับ field เดียวกัน, THEN THE Field_Extractor SHALL คง course record นั้นไว้พร้อม field ที่สกัดได้ทั้งหมด, SHALL บันทึก field นั้นเป็นค่าว่าง และ SHALL บันทึก review_issue ชนิด `field_unresolved` ที่อ้าง document_id, เลขหน้า, รหัสวิชา, ชื่อ field และค่าที่พบทั้งหมด

### Requirement 9: การจัดเก็บแบบ provenance-first

**User Story:** As a ผู้ตรวจสอบคำตอบ, I want ให้ทุกค่าที่ระบบตอบสืบกลับไปยังหน้าและตำแหน่งในเอกสารได้, so that ตรวจสอบความถูกต้องด้วยตาได้ทันที

#### Acceptance Criteria

1. THE Provenance_Store SHALL เก็บข้อมูลทั้งหมดใน SQLite ไฟล์เดียวที่อยู่ใน workspace ของโปรเจกต์ โดยไม่มี data store อื่นนอกไฟล์นั้น และ SHALL เปิดการบังคับ foreign key constraint ทุกครั้งที่เปิดการเชื่อมต่อ
2. THE Provenance_Store SHALL บังคับว่าทุกแถวในตารางข้อมูลหลักสูตรมี foreign key ไปยัง provenance record ที่มีค่าครบทุกฟิลด์ ได้แก่ document_id ที่มีอยู่ใน Document_Registry, page เป็นจำนวนเต็มตั้งแต่ 1 ถึงจำนวนหน้าของเอกสารนั้น, bbox เป็นค่า (x0, y0, x1, y1) ที่ x1 > x0 และ y1 > y0 และอยู่ภายในขอบเขตพิกัดของหน้านั้น และ extraction_method ที่ไม่เป็นค่าว่าง
3. IF การเขียนแถวข้อมูลหลักสูตรใดอ้างถึง provenance record ที่ไม่มีอยู่ หรือมีฟิลด์ตามเกณฑ์ในข้อ 2 ขาดหรือไม่ผ่านการตรวจ, THEN THE Provenance_Store SHALL ปฏิเสธการเขียนทั้ง transaction, SHALL ไม่คงแถวใดของ transaction นั้นไว้ในฐานข้อมูล และ SHALL คืน error ที่ระบุชื่อตาราง ชื่อ field และ provenance attribute ที่ขาดหรือไม่ผ่านการตรวจ
4. THE Provenance_Store SHALL ตอบคำถามระดับ L1 และ L2 ใน Gold_Set ได้ครบ 100% ของจำนวนข้อ ด้วย SQL statement เดียวหรือ join ที่นิยามไว้ล่วงหน้า โดยแต่ละ query คืนผลภายใน 1,000 มิลลิวินาทีบน dataset 14 เอกสาร
5. THE Provenance_Store SHALL มีตาราง `document_relation` สำหรับความสัมพันธ์ระหว่างเอกสาร โดยทุกแถวอ้างถึง document_id ที่มีอยู่ใน Document_Registry เท่านั้น และจำนวนเอกสารที่ ingest SHALL เท่ากับ 14 ไฟล์ในขอบเขต โดยไม่มี document record ของเอกสารนอกขอบเขต
6. THE Provenance_Store SHALL เก็บ content hash แบบ SHA-256 เป็นสตริงเลขฐานสิบหก 64 อักขระ ของทุก document, curriculum version, page และ chunk เพื่อใช้เป็นตัวตนของหลักฐาน และการคำนวณซ้ำจากเนื้อหาเดิม SHALL ให้ค่า hash เท่าเดิมทุกครั้ง
7. WHEN มีการขอ provenance ของค่า field หนึ่งที่ระบบตอบ, THE Provenance_Store SHALL คืน document_id, relative path, page, bbox และ chunk content hash ของหลักฐานนั้นด้วย query เดียว ภายใน 1,000 มิลลิวินาที
8. IF การเปิดหรือการเขียนไฟล์ SQLite ล้มเหลว หรือการตรวจ integrity ของฐานข้อมูลไม่ผ่าน, THEN THE Provenance_Store SHALL หยุดงานเขียนที่กำลังทำ, SHALL ไม่ commit ข้อมูลบางส่วนของงานนั้น และ SHALL คืน error ที่ระบุว่าเป็นความล้มเหลวในการเข้าถึงไฟล์ฐานข้อมูลหรือความล้มเหลวของ integrity check

### Requirement 10: การแยกเวอร์ชันหลักสูตร

**User Story:** As a นักศึกษาที่อยู่ในหลักสูตรฉบับหนึ่ง, I want ให้คำตอบมาจากหลักสูตรฉบับของตนเท่านั้น, so that ไม่ได้ข้อมูลของฉบับอื่นที่ใช้กับตนไม่ได้

#### Acceptance Criteria

1. THE Provenance_Store SHALL ผูกทุก chunk และทุก field เข้ากับ curriculum version เพียงหนึ่งค่า โดยค่า program, curriculum_year และ edition_status ครบทั้งสามค่าและไม่เป็น null
2. IF การเขียน chunk หรือ field ใดไม่มี curriculum version ครบทั้งสามค่า, THEN THE Provenance_Store SHALL ปฏิเสธการเขียนแถวนั้นทั้งแถว, SHALL ไม่บันทึกค่าบางส่วน และ SHALL คืน error ที่ระบุชื่อ field และค่าที่ขาด
3. WHEN Api_Service รับคำถาม, THE Version_Resolver SHALL คืนชุด curriculum version ที่คำถามอ้างถึงซึ่งมีสมาชิกตั้งแต่ 1 ค่าถึงจำนวน curriculum version ทั้งหมดที่มีใน Provenance_Store, SHALL ใช้ค่าจากพารามิเตอร์ที่ผู้ใช้ระบุก่อนค่าที่ตีความจากข้อความคำถามเมื่อทั้งสองแหล่งขัดกัน, SHALL ให้ผลเท่าเดิมทุกครั้งเมื่อคำถามและพารามิเตอร์เดิมไม่เปลี่ยน (deterministic) และ THE Trace_Recorder SHALL บันทึกชุดที่ตัดสินได้พร้อมแหล่งที่ใช้ตัดสินลง query_trace
4. IF ชุด curriculum version ที่ Version_Resolver ตัดสินได้มีสมาชิกมากกว่า 1 ค่า, THEN THE Api_Service SHALL คืนคำถามยืนยันที่ระบุ curriculum version ที่เป็นไปได้ทุกค่าโดยแต่ละรายการแสดง program, curriculum_year และ edition_status, SHALL ไม่เรียก Answer_Generator, SHALL ไม่คืนเนื้อหาคำตอบของคำถามนั้น และ SHALL คงข้อความคำถามเดิมไว้เพื่อประมวลผลต่อเมื่อผู้ใช้เลือกค่าเดียว
5. WHILE คำขอหนึ่งถูกจำกัดอยู่ที่ curriculum version ชุดหนึ่ง, THE Hybrid_Retriever SHALL คืนเฉพาะ chunk ที่สังกัด curriculum version ในชุดนั้น และจำนวน chunk ที่ส่งต่อให้ Phrase_Booster, MaxSim_Reranker และ Evidence_Planner ซึ่งสังกัด curriculum version นอกชุดนั้น SHALL เท่ากับศูนย์
6. IF หลังการกรองตาม curriculum version ไม่มี chunk ใดเหลืออยู่, THEN THE Api_Service SHALL คืนคำตอบที่ระบุว่าไม่พบหลักฐานใน curriculum version ที่ค้นพร้อมระบุค่า curriculum version นั้น, SHALL ไม่ขยายการค้นไป curriculum version อื่น และ SHALL ไม่เรียก Answer_Generator ให้เรียบเรียงคำตอบจากหลักฐานนอกชุดนั้น
7. WHERE คำถามเป็น L4 question ที่ขอเปรียบเทียบข้ามเวอร์ชันหรือข้ามหลักสูตร, THE Answer_Generator SHALL แสดงคำตอบเป็นส่วนแยกหนึ่งส่วนต่อหนึ่ง curriculum version โดยแต่ละส่วนกำกับ program, curriculum_year และ edition_status และอ้างเฉพาะ citation ID ของ chunk ที่สังกัด curriculum version ของส่วนนั้น
8. IF คำตอบหนึ่งมี citation ID ที่อ้าง chunk ซึ่งไม่สังกัด curriculum version ในชุดของคำขอนั้น, THEN THE Citation_Validator SHALL ปฏิเสธคำตอบนั้นทั้งฉบับ, SHALL ไม่ส่งคำตอบนั้นให้ผู้ใช้, SHALL คืน error ที่ระบุว่าเกิดการอ้างข้ามเวอร์ชันพร้อม citation ID ที่ผิด และ THE Trace_Recorder SHALL บันทึกเหตุการณ์นั้นลง query_trace
9. THE Evaluation_Harness SHALL รายงาน version-selection accuracy เป็นสัดส่วนของคำถามที่ชุด curriculum version ที่ Version_Resolver คืนเท่ากับชุดที่คาดหมายทุกค่า ต่อจำนวนคำถามใน Gold_Set ที่มีชุด curriculum version ที่คาดหมายกำกับไว้ และค่านั้นต้องไม่น้อยกว่า 0.98 (เกณฑ์สถานะ `estimate`)
10. THE KatRAG_System SHALL คืนคำตอบจาก cache ได้เฉพาะเมื่อ cache key ตรงกันทุกค่า คือข้อความคำถามที่ normalize แล้วตรงกันทุกอักขระ, ชุด curriculum version ตรงกันทุกค่า และ content hash ของ chunk ที่ใช้ตรงกัน และ SHALL ไม่ใช้ค่าความคล้ายแบบประมาณหรือเกณฑ์ threshold ใดเป็นเงื่อนไขการคืนคำตอบจาก cache

### Requirement 11: ground truth ของอาจารย์แบบไม่แก้ต้นฉบับ

**User Story:** As a เจ้าของโปรเจกต์, I want ให้ระบบใช้ ground truth ของอาจารย์ได้โดยไม่แก้ไฟล์ต้นฉบับ, so that การประเมินยังอ้างอิงข้อมูลที่ส่งมอบมาได้ตรงตามเดิม

#### Acceptance Criteria

1. THE Gt_Normalizer SHALL เปิดไฟล์ ground truth ของอาจารย์ด้วยโหมดอ่านอย่างเดียว, เขียน normalized ground truth และรายงานทุกไฟล์ลงไดเรกทอรีของโปรเจกต์เท่านั้น ไม่เขียนหรือลบไฟล์ใดในไดเรกทอรีต้นฉบับ และ SHA-256 ของไฟล์ ground truth ต้นฉบับทุกไฟล์เมื่อจบงานต้องเท่ากับค่าที่บันทึกไว้ก่อนเริ่มงานทุกไฟล์
2. THE Gt_Normalizer SHALL ผูกไฟล์ ground truth ของแต่ละโปรแกรมในกลุ่ม IT, BIT, DSBA และ AIT เข้ากับ curriculum version หนึ่งค่าที่มี program ตรงกันและ edition_status เท่ากับ `current` ก่อนนำไปใช้วัดผล
3. IF ไฟล์ ground truth หนึ่งไฟล์ผูกกับ curriculum version ที่มี edition_status เท่ากับ `current` ได้ 0 ค่า หรือได้มากกว่า 1 ค่า, THEN THE Gt_Normalizer SHALL คัดทุกแถวของไฟล์นั้นออกจากชุดวัดผล, บันทึกเหตุผลการคัดออกที่ระบุชื่อไฟล์, program และจำนวน curriculum version ที่จับคู่ได้ และรายงานจำนวนแถวที่คัดออกด้วยเหตุผลนี้แยกต่อไฟล์
4. WHEN Gt_Normalizer พบแถวที่ค่า `code` หลังตัดช่องว่างหัวท้ายและช่องว่างภายในแล้วไม่ใช่เลขอารบิก 8 หลัก, THE Gt_Normalizer SHALL คัดแถวนั้นออกจากชุดวัดผล และบันทึกเหตุผลการคัดออกที่ระบุชื่อไฟล์ ground truth, เลขแถวในไฟล์, ค่า `code` ดั้งเดิม และเหตุผลว่ารูปแบบรหัสวิชาไม่ถูกต้อง
5. WHEN Gt_Normalizer พบเซลล์รหัสวิชาที่มีทางเลือกหลายค่าคั่นด้วยคำว่า `หรือ` โดยมีหรือไม่มีช่องว่างล้อมรอบ, THE Gt_Normalizer SHALL แยกเป็น alternative group ที่มีสมาชิกตั้งแต่ 2 ถึง 10 รหัส โดยนับเฉพาะสมาชิกที่ผ่านรูปแบบเลขอารบิก 8 หลัก และถือว่าตรงกันเมื่อรหัสใดรหัสหนึ่งในกลุ่มตรงกัน
6. WHEN Gt_Normalizer พบค่า `year` หรือ `semester` เป็นสตริงที่หลังตัดช่องว่างหัวท้ายแล้วประกอบด้วยเลขอารบิกเท่านั้น, THE Gt_Normalizer SHALL แปลงค่าเป็นจำนวนเต็ม โดยยอมรับ `year` ในช่วง 0 ถึง 8 และ `semester` ในช่วง 0 ถึง 3
7. IF ค่า `year` หรือ `semester` ของแถวหนึ่งแปลงเป็นจำนวนเต็มไม่ได้ หรืออยู่นอกช่วงที่ยอมรับ, THEN THE Gt_Normalizer SHALL คัดแถวนั้นออกจากการให้คะแนน slot ปีและภาคการศึกษา, คงแถวนั้นไว้ในการให้คะแนนระดับรายวิชา และบันทึกเหตุผลการคัดออกที่ระบุชื่อไฟล์, เลขแถว, ชื่อ field และค่าดั้งเดิม
8. WHEN แถว ground truth มี `year` เท่ากับ 0 หรือ `semester` เท่ากับ 0, THE Gt_Normalizer SHALL คัดแถวนั้นออกจากการให้คะแนน slot ปีและภาคการศึกษา และคงไว้ในการให้คะแนนระดับรายวิชา
9. THE Gt_Normalizer SHALL ตัดช่องว่างหัวท้ายและย่อช่องว่างซ้อนภายในค่าหมวดวิชาให้เหลือหนึ่งช่องว่างก่อนเทียบกับ synonym map ที่ประกาศไว้ในโปรเจกต์, แปลงค่า `หมวดวิชาเสรี` และ `หมวดวิชาเลือกเสรี` เป็นค่าเดียวกัน และคงค่าที่ไม่มีใน synonym map ไว้ตามเดิมพร้อมนับจำนวนค่าที่ไม่พบใน map แยกต่อไฟล์
10. THE Gt_Normalizer SHALL แปลงค่า prerequisite ที่เป็น `ไม่มี`, `-`, สตริงว่าง, สตริงที่มีเฉพาะช่องว่าง และค่า null เป็นเซตว่าง
11. WHEN Evaluation_Harness จับคู่รายวิชาระหว่าง ground truth และผลลัพธ์ของระบบ, THE Evaluation_Harness SHALL จับคู่แบบ multiset เพื่อรองรับรหัสวิชาที่ปรากฏหลาย slot ในไฟล์เดียว
12. IF รหัสวิชาใน ground truth ไม่ปรากฏในเอกสาร PDF ฉบับที่ผูกไว้, THEN THE Evaluation_Harness SHALL บันทึก discrepancy record ที่ระบุรหัสวิชา, ชื่อไฟล์ ground truth, เลขแถว, document_id, curriculum version และสถานะรอการตัดสินโดยมนุษย์, กันรายการนั้นออกจากตัวเลข metric หลัก, คง discrepancy record ทุกรายการไว้ในผลลัพธ์ และรายงานจำนวนรายการที่กันออกแยกต่อไฟล์
13. THE Gt_Normalizer SHALL รายงานจำนวนแถวก่อน normalize, จำนวนแถวหลัง normalize, จำนวนแถวที่คัดออกแยกตามเหตุผลการคัดออก ต่อไฟล์ ground truth ทุกไฟล์ โดยจำนวนแถวหลัง normalize บวกจำนวนแถวที่คัดออกทั้งหมดต้องเท่ากับจำนวนแถวก่อน normalize ของไฟล์นั้น และการประมวลผลซ้ำจากไฟล์ต้นฉบับชุดเดิมต้องให้ตัวเลขรายงานเท่ากันทุกครั้ง

### Requirement 12: gold set ของโปรเจกต์

**User Story:** As a ผู้ประเมินระบบ, I want ให้มีชุดอ้างอิงที่ครอบคลุมสิ่งที่ ground truth ของอาจารย์วัดไม่ได้, so that วัด CER, citation, เวอร์ชัน และคำถาม QA ได้จริง

#### Acceptance Criteria

1. THE Gold_Set SHALL ครอบคลุมเอกสารทั้ง 14 ไฟล์ รวมหลักสูตรบัณฑิตศึกษาและฉบับเก่า
2. THE Gold_Set SHALL มีข้อความอ้างอิงระดับหน้าที่ถอดด้วยมือ พร้อม document_id และเลขหน้า สำหรับใช้วัด page CER
3. THE Gold_Set SHALL มีตารางอ้างอิงระดับ cell สำหรับใช้วัด table-cell F1
4. THE Gold_Set SHALL มีชุดคำถามที่ครอบคลุมทั้งสี่ระดับ L1, L2, L3 และ L4 โดยแต่ละคำถามมีคำตอบอ้างอิง, curriculum version ที่ถูกต้อง และรายการหน้าที่เป็นหลักฐานที่ถูกต้อง
5. THE Gold_Set SHALL มีคำถามที่คำตอบต่างกันระหว่างฉบับ old และ current ของโปรแกรมเดียวกัน สำหรับใช้วัด version-selection accuracy
6. THE Gold_Set SHALL บันทึกผู้จัดทำ, วันที่จัดทำ และวิธีการตรวจทานของทุกรายการ
7. THE Evaluation_Harness SHALL ใช้ Gold_Set เป็นแหล่งอ้างอิงสำหรับ page CER, table-cell F1, citation precision, citation recall, Recall@k, version-selection accuracy และความถูกต้องของคำตอบ

### Requirement 13: การค้นคืนแบบ hybrid

**User Story:** As a ผู้ใช้, I want ให้ระบบค้นหลักฐานที่เกี่ยวข้องได้ทั้งจากคำตรงและความหมาย, so that ได้คำตอบแม้ถามด้วยถ้อยคำที่ต่างจากในเอกสาร

#### Acceptance Criteria

1. THE Lexical_Index SHALL สร้างดัชนี FTS5 BM25 จากข้อความ chunk ทุก chunk ใน Provenance_Store โดยจำนวน entry ในดัชนีเท่ากับจำนวน chunk record ใน Provenance_Store และทุก entry อ้าง chunk id และ curriculum version ของ chunk นั้นได้
2. THE Dense_Index SHALL สร้าง embedding ของทุก chunk ด้วยโมเดล bge-m3 ที่ทำงานบนเครื่องผู้ใช้ โดยจำนวน embedding เท่ากับจำนวน chunk record ใน Provenance_Store และ embedding ทุกตัวมีจำนวนมิติเท่ากัน
3. WHEN Hybrid_Retriever ได้รับคำถามที่ผ่านการตรวจความยาว, THE Hybrid_Retriever SHALL รวมอันดับ 100 อันดับแรกจาก Lexical_Index กับ 100 อันดับแรกจาก Dense_Index ด้วยสูตรรวมคะแนนและค่าพารามิเตอร์ที่อ่านจากไฟล์ตั้งค่า และคืน chunk ไม่เกิน 50 รายการเรียงคะแนนจากมากไปน้อย โดยเมื่อคะแนนเท่ากันให้เรียงตาม chunk id จากน้อยไปมาก เพื่อให้คำถามเดียวกันบนดัชนีชุดเดิมได้ลำดับผลลัพธ์เดิมทุกครั้ง
4. THE Dense_Index SHALL คำนวณความคล้ายด้วยการสแกนแบบครบทุก chunk โดยไม่ใช้ approximate nearest neighbor index และเวลาค้นต่อหนึ่งคำถามต้องไม่เกิน 3.0 วินาทีที่ percentile 95 บนคลังข้อมูล 14 เอกสาร 3,689 หน้า (เกณฑ์สถานะ `estimate`)
5. THE Phrase_Booster SHALL คูณคะแนนรวมของ chunk ที่มีคำตรงกับรายการใน domain lexicon ได้แก่ รหัสวิชา, ชื่อวิชา, หน่วยกิต, วิชาบังคับ, วิชาก่อน, ปี, ภาคการศึกษา และเวอร์ชันหลักสูตร ด้วยตัวคูณที่อ่านจากไฟล์ตั้งค่าในช่วง 1.00 ถึง 3.00 โดยเทียบคำแบบตรงทุกอักขระหลัง normalize ช่องว่างซ้อนและลำดับ combining mark และ SHALL ไม่เพิ่มหรือลบ chunk ออกจากชุดผลลัพธ์ที่ Hybrid_Retriever ส่งเข้ามา
6. THE MaxSim_Reranker SHALL จัดอันดับใหม่เฉพาะ chunk อันดับ 1 ถึงอันดับ rerank_depth จาก Hybrid_Retriever โดย rerank_depth เป็นจำนวนเต็มในช่วง 20 ถึง 40 ที่อ่านจากไฟล์ตั้งค่า ค่าตั้งต้นเท่ากับ 20 และ SHALL คงลำดับเดิมของ chunk ที่อยู่ต่ำกว่าอันดับ rerank_depth ไว้ทั้งหมดต่อท้ายผลที่จัดอันดับใหม่
7. WHERE ผลการทดสอบ ablation บน Gold_Set แสดงว่า Recall@10 เมื่อเปิด MaxSim_Reranker สูงกว่าเมื่อปิดไม่น้อยกว่า 0.01 บนชุดคำถามเดียวกัน, THE KatRAG_System SHALL เปิดใช้ MaxSim_Reranker เป็นค่าตั้งต้น และบันทึกค่า Recall@10 ทั้งสองกรณีพร้อมวันที่ทดสอบ
8. IF ผลการทดสอบ ablation บน Gold_Set ยังไม่มี, THEN THE KatRAG_System SHALL ปิด MaxSim_Reranker เป็นค่าตั้งต้น และบันทึกสถานะเป็น `pending_ablation`
9. THE Evaluation_Harness SHALL รายงาน Recall@k ที่ค่า k เท่ากับ 5, 10 และ 20 บนชุดคำถามใน Gold_Set โดยนับว่า chunk หนึ่งเป็น hit เมื่อ document_id และเลขหน้าของ chunk นั้นอยู่ในรายการหน้าที่เป็นหลักฐานที่ถูกต้องของคำถามนั้น และ Recall@10 ต้องไม่น้อยกว่า 0.90 (เกณฑ์สถานะ `estimate`)
10. IF ข้อความคำถามที่ Hybrid_Retriever ได้รับว่างเปล่าหลังตัดช่องว่างหัวท้าย หรือยาวเกิน 1,000 อักขระ, THEN THE Hybrid_Retriever SHALL ปฏิเสธคำขอ คืน error ที่ระบุว่าความยาวคำถามต้องอยู่ในช่วง 1 ถึง 1,000 อักขระ และไม่เรียก Lexical_Index หรือ Dense_Index
11. IF ไม่มี chunk ใดตรงกับคำถามภายใต้ curriculum version ที่คำขอถูกจำกัดไว้, THEN THE Hybrid_Retriever SHALL คืนรายการผลลัพธ์ว่างพร้อมสถานะที่ระบุว่าไม่พบหลักฐาน โดยไม่คืน error และคงข้อมูลในดัชนีไว้ไม่เปลี่ยนแปลง
12. IF การสร้างดัชนี lexical หรือ embedding ของ chunk ใดไม่สำเร็จ, THEN THE Ingestion_Manager SHALL บันทึก review_issue ชนิด `index_build_incomplete` ที่ระบุ chunk id, ชนิดดัชนีที่ล้มเหลว และเหตุผลที่ล้มเหลว ทำเครื่องหมายดัชนีนั้นว่าไม่สมบูรณ์ และคงดัชนีของ chunk ที่สำเร็จแล้วไว้ทั้งหมด

### Requirement 14: การวางแผนหลักฐานหลายฮอปแบบมีขอบเขต

**User Story:** As a ผู้ใช้ที่ถามคำถามซับซ้อน, I want ให้ระบบตามหาหลักฐานต่อเนื่องได้แต่ไม่วนไม่จบ, so that ได้คำตอบครบภายในเวลาที่ยอมรับได้

#### Acceptance Criteria

1. THE Evidence_Planner SHALL สร้าง evidence graph ที่เป็น directed acyclic graph โดยมี node ไม่เกิน 60 node ต่อคำขอ และทุก node SHALL อ้างถึง chunk หรือ field ที่มี provenance ครบทั้ง document_id, เลขหน้า และ span หรือ bbox
2. IF chunk หรือ field ที่จะเพิ่มเป็น node ขาดค่า document_id, เลขหน้า หรือ span/bbox, THEN THE Evidence_Planner SHALL ไม่เพิ่ม node นั้นและ SHALL บันทึกเหตุผล `missing_provenance` ลง query_trace โดยคง node ที่เพิ่มไว้แล้วในกราฟ
3. THE Evidence_Planner SHALL จำกัดจำนวน hop ต่อคำขอไว้ไม่เกินค่า max_hops ที่อ่านจากไฟล์ตั้งค่า โดยค่าเริ่มต้นเท่ากับ 3 และรับค่าได้ในช่วง 1 ถึง 5 และ SHALL เพิ่ม node ไม่เกิน 10 node ต่อ hop
4. WHEN จำนวน hop ที่ทำแล้วเท่ากับ max_hops, THE Evidence_Planner SHALL หยุดค้นหลักฐานเพิ่ม คืน evidence graph ที่สร้างได้แล้ว และบันทึกเหตุผล `max_hops_reached` ลง query_trace
5. WHEN Evidence_Planner จบ hop หนึ่ง, THE Gain_Cost_Halter SHALL คำนวณ gain เป็นผลต่างของ evidence coverage score (สเกล 0.00–1.00) ระหว่าง hop ล่าสุดกับค่าที่ดีที่สุดก่อนหน้า และ cost เป็นเวลาประมวลผลของ hop ล่าสุดหารด้วย per-query evidence time budget แล้วคืนคำตัดสินหนึ่งค่าจาก `halt` หรือ `continue` โดยคืน `halt` เมื่อ gain น้อยกว่า cost คูณ tau และจำนวน hop ที่ทำแล้วไม่น้อยกว่า l_min โดยค่าเริ่มต้น tau = 1.0, l_min = 1 และ per-query evidence time budget = 10 วินาที อ่านจากไฟล์ตั้งค่า
6. WHEN Gain_Cost_Halter คืนคำตัดสิน `halt`, THE Evidence_Planner SHALL ไม่เริ่ม hop ถัดไปและ SHALL คืน evidence graph ที่สร้างได้แล้วพร้อมเหตุผลการหยุด
7. IF hop หนึ่งไม่เพิ่ม node ใหม่เลย, THEN THE Gain_Cost_Halter SHALL คืนคำตัดสิน `halt` และ THE Evidence_Planner SHALL บันทึกเหตุผล `no_new_evidence` ลง query_trace
8. IF ค่า gain หรือ cost เป็น NaN หรือ infinity, THEN THE Gain_Cost_Halter SHALL คืนคำตัดสิน `halt` ถือว่า gain เท่ากับ 0.00 และ THE Evidence_Planner SHALL บันทึกเหตุผล `nan_guard` ลง query_trace โดยคง evidence graph ที่สร้างได้แล้วไว้
9. IF เวลาสะสมของการวางแผนหลักฐานในคำขอหนึ่งถึง per-query evidence time budget, THEN THE Evidence_Planner SHALL ยุติการค้นหลักฐาน คืน evidence graph ที่สร้างได้แล้ว และบันทึกเหตุผล `time_budget_exceeded` ลง query_trace
10. IF Evidence_Planner กำลังจะเพิ่ม edge ที่ทำให้เกิด cycle, THEN THE Evidence_Planner SHALL ปฏิเสธ edge นั้น คงกราฟเดิมไว้ไม่เปลี่ยนแปลง และบันทึกเหตุผล `cycle_rejected` พร้อม node ต้นทางและ node ปลายทางของ edge นั้นลง query_trace
11. THE Evidence_Planner SHALL คืน evidence graph ที่ทุก node สังกัด curriculum version ที่ Version_Resolver กำหนดไว้สำหรับคำขอนั้น และ SHALL ไม่เพิ่ม node ที่สังกัด curriculum version อื่น โดยบันทึกเหตุผล `version_filtered` พร้อมจำนวน node ที่ถูกกรองออกลง query_trace
12. WHEN Evidence_Planner จบ hop หนึ่ง, THE Trace_Recorder SHALL บันทึกลง query_trace ของคำขอนั้น: ลำดับที่ของ hop, คำค้นที่ใช้, รายการ node_id ที่เพิ่ม, ค่า gain, ค่า cost, คำตัดสินของ Gain_Cost_Halter, เหตุผลการหยุด และเวลาประมวลผลของ hop เป็นมิลลิวินาที

### Requirement 15: การให้เหตุผลหลักสูตรแบบ deterministic

**User Story:** As a นักศึกษา, I want ให้คำตอบเรื่องวิชาก่อน หน่วยกิต และเกณฑ์สำเร็จการศึกษาคำนวณด้วยกฎที่ตรวจสอบได้, so that ผลลัพธ์ไม่เปลี่ยนไปตามการเรียบเรียงภาษาของโมเดล

#### Acceptance Criteria

1. THE Curriculum_Reasoner SHALL คำนวณสายวิชาก่อนของรายวิชาหนึ่งจากกราฟ prerequisite ใน Provenance_Store ด้วยอัลกอริทึมที่ให้ผลเดิมทุกครั้งที่ input เดิม
2. IF กราฟ prerequisite มี cycle, THEN THE Curriculum_Reasoner SHALL คืน error ที่ระบุรายวิชาใน cycle และ Ingestion_Manager SHALL บันทึก review_issue ชนิด `prerequisite_cycle`
3. THE Curriculum_Reasoner SHALL คำนวณผลรวมหน่วยกิตต่อหมวดวิชาและต่อหลักสูตรจากค่า total ของ Credits_Parser
4. WHEN ผู้ใช้ถามเกณฑ์สำเร็จการศึกษาของหลักสูตรหนึ่ง, THE Curriculum_Reasoner SHALL ประเมินเกณฑ์จากกฎที่บันทึกไว้ใน Provenance_Store พร้อมคืน citation ID ของทุกกฎที่ใช้
5. THE Answer_Generator SHALL ไม่คำนวณค่าตัวเลขหน่วยกิต ผลการประเมินเกณฑ์ หรือความสัมพันธ์วิชาก่อนขึ้นใหม่ และ SHALL ใช้ค่าที่ Curriculum_Reasoner ส่งมาเท่านั้น
6. IF ค่าตัวเลขในคำตอบที่ Answer_Generator ผลิตต่างจากค่าที่ Curriculum_Reasoner ส่งมา, THEN THE Citation_Validator SHALL ทำเครื่องหมายคำตอบนั้นเป็น unsupported และ Api_Service SHALL คืนค่าที่ Curriculum_Reasoner คำนวณไว้แทน

### Requirement 16: การจำแนกระดับคำถามและการเลือกเส้นทาง

**User Story:** As a ผู้ใช้, I want ให้ระบบเลือกวิธีตอบตามความซับซ้อนของคำถาม, so that คำถามง่ายได้คำตอบเร็วและคำถามยากได้คำตอบครบ

#### Acceptance Criteria

1. WHEN Api_Service รับคำถามที่มีความยาวหลังตัดช่องว่างหัวท้ายตั้งแต่ 1 ถึง 500 อักขระ, THE Question_Router SHALL จำแนกคำถามเป็นระดับหนึ่งค่าจาก L1, L2, L3 หรือ L4 ให้เสร็จภายใน 200 มิลลิวินาที และ SHALL บันทึกลง query_trace ของคำขอนั้น: ระดับที่จำแนกได้, ค่า confidence ในสเกล 0.00 ถึง 1.00, รหัสกฎหรือชุดคุณลักษณะที่ใช้ตัดสิน, เส้นทางที่เลือก และเวลาที่ใช้จำแนกเป็นมิลลิวินาที
2. WHEN Question_Router จำแนกคำถามเป็น L1 หรือ L2, THE Question_Router SHALL เลือกเส้นทางที่ตอบจาก structured field ใน Provenance_Store เท่านั้น, SHALL ไม่เรียก Evidence_Planner และ SHALL ให้เส้นทางนั้นคืนผลภายใน 1,000 มิลลิวินาทีบน dataset 14 เอกสาร
3. WHEN Question_Router จำแนกคำถามเป็น L3 หรือ L4, THE Question_Router SHALL เรียก Evidence_Planner หนึ่งครั้งต่อ curriculum version ที่ Version_Resolver กำหนดไว้สำหรับคำขอนั้น โดยใช้ค่า max_hops จากไฟล์ตั้งค่า และ SHALL เรียกไม่เกิน 2 curriculum version ต่อคำขอ
4. IF ค่า confidence ของการจำแนกน้อยกว่า 0.50 หรือการจำแนกใช้เวลาเกิน 200 มิลลิวินาที, THEN THE Question_Router SHALL เลือกเส้นทาง L3 เป็นค่าตั้งต้น, SHALL ดำเนินการตอบคำถามต่อโดยไม่ยกเลิกคำขอ และ SHALL บันทึกเหตุผล `router_fallback` พร้อมค่า confidence และเวลาที่ใช้จำแนกลง query_trace
5. THE Evaluation_Harness SHALL รายงานต่อระดับ L1, L2, L3 และ L4 บนชุดคำถามใน Gold_Set: จำนวนคำถาม, จำนวนคำตอบที่ตรงกับคำตอบอ้างอิง, answer accuracy เป็นสัดส่วนทศนิยม 2 ตำแหน่ง และ routing accuracy เป็นสัดส่วนของคำถามที่ระดับที่จำแนกได้ตรงกับระดับอ้างอิงใน Gold_Set พร้อมสถานะเกณฑ์ `estimate`
6. IF คำถามที่รับเข้ามามีความยาวหลังตัดช่องว่างหัวท้ายเป็น 0 อักขระ หรือมากกว่า 500 อักขระ, THEN THE Api_Service SHALL คืน error ที่ระบุขอบเขตความยาวที่รองรับ, THE Question_Router SHALL ไม่จำแนกระดับและไม่เรียก Evidence_Planner และ THE Trace_Recorder SHALL บันทึกเหตุผล `question_input_invalid` พร้อมความยาวที่รับมาลง query_trace
7. IF เส้นทาง L1 หรือ L2 คืนผลลัพธ์ว่างเพราะไม่พบ structured field ที่ตรงเงื่อนไข, THEN THE Question_Router SHALL เปลี่ยนไปใช้เส้นทาง L3 ได้ไม่เกิน 1 ครั้งต่อคำขอ, SHALL คงระดับที่จำแนกครั้งแรกไว้ใน query_trace และ SHALL บันทึกเหตุผล `route_escalated` พร้อมระดับต้นทางและระดับปลายทาง

### Requirement 17: การสร้างคำตอบด้วยโมเดลท้องถิ่นและการตรวจการอ้างอิง

**User Story:** As a ผู้ใช้, I want ให้ทุกประโยคของคำตอบมีการอ้างอิงหน้าเอกสารที่ตรวจสอบได้, so that เชื่อถือคำตอบได้และตรวจย้อนกลับเองได้

#### Acceptance Criteria

1. WHEN Answer_Generator ได้รับ evidence object ที่มี node ตั้งแต่ 1 node, THE Answer_Generator SHALL เรียกโมเดล Qwen3 4B GGUF Q4 ผ่าน llama.cpp ที่ทำงานบนเครื่องผู้ใช้, SHALL ไม่ส่งข้อความคำถามหรือเนื้อหา evidence ไปยังปลายทางนอกเครื่องผู้ใช้ และ SHALL คืนคำตอบภายใน answer_time_budget ที่อ่านจากไฟล์ตั้งค่า โดยค่าเริ่มต้นเท่ากับ 60 วินาที และรับค่าได้ในช่วง 10 ถึง 180 วินาที
2. WHEN Answer_Generator ประกอบ prompt ของคำขอหนึ่ง, THE Answer_Generator SHALL ใส่เฉพาะ evidence unit ที่มี citation ID ที่ KatRAG_System ออกให้แล้ว จำนวนไม่เกิน 60 รายการต่อคำขอ โดยแต่ละรายการกำกับ citation ID, ข้อความหลักฐาน และ curriculum version ครบทุกค่า และ SHALL ไม่ใส่ chunk หรือ field ที่ยังไม่มี citation ID ลงใน prompt
3. WHEN Answer_Generator คืนคำตอบ, THE Citation_Validator SHALL แยกคำตอบเป็นหน่วยข้อความ (claim unit) โดยหนึ่งหน่วยคือข้อความที่คั่นด้วยเครื่องหมายจบประโยคหรือหนึ่งรายการในรายการหัวข้อย่อย และ SHALL ตรวจทุก citation ID ที่ปรากฏในแต่ละหน่วยข้อความว่าตรงทุกอักขระกับ citation ID รายการใดรายการหนึ่งที่ส่งเข้า prompt ของคำขอนั้น
4. IF หน่วยข้อความใดอ้าง citation ID ที่ไม่ตรงกับรายการที่ส่งเข้า prompt, THEN THE Citation_Validator SHALL ลบหน่วยข้อความนั้นออกจากคำตอบ, SHALL คงหน่วยข้อความที่ผ่านการตรวจไว้ทั้งหมดโดยไม่แก้ไขข้อความ และ THE Api_Service SHALL คืนจำนวนหน่วยข้อความที่ถูกลบเป็นจำนวนเต็มตั้งแต่ 0 พร้อมผลลัพธ์
5. IF หน่วยข้อความใดเป็นข้อความเชิงข้อเท็จจริง คือมีตัวเลข รหัสวิชา ชื่อวิชา ชื่อหลักสูตร curriculum version หรือค่าของ field หลักสูตรใด และไม่มี citation ID ที่ผ่านการตรวจกำกับอยู่ในหน่วยเดียวกัน, THEN THE Citation_Validator SHALL ทำเครื่องหมายหน่วยข้อความนั้นเป็น unsupported claim และ THE Api_Service SHALL คืนเครื่องหมาย unsupported claim ของหน่วยข้อความนั้นพร้อมจำนวน unsupported claim ทั้งหมดในผลลัพธ์
6. WHEN Citation_Validator ตรวจคำตอบเสร็จ, THE Api_Service SHALL คืนคำตอบพร้อมรายการ citation ที่แปลงจาก citation ID แต่ละรายการเป็น document_id, เลขหน้าเป็นจำนวนเต็มตั้งแต่ 1 และหัวข้อของ chunk นั้น ครบทุกฟิลด์โดยไม่มีฟิลด์ใดว่าง และ SHALL คืนผลลัพธ์ภายใน 1,000 มิลลิวินาทีนับจากเวลาที่การตรวจเสร็จ
7. THE Evaluation_Harness SHALL คำนวณ citation page precision เป็นจำนวน citation ในคำตอบที่คู่ (document_id, page) ตรงกับ citation ที่คาดหมายของคำถามนั้นใน Gold_Set หารด้วยจำนวน citation ทั้งหมดที่คำตอบอ้าง, citation recall เป็นจำนวน citation ที่คาดหมายซึ่งปรากฏในคำตอบ หารด้วยจำนวน citation ที่คาดหมายทั้งหมด และ unsupported-claim rate เป็นจำนวนหน่วยข้อความที่ถูกทำเครื่องหมาย unsupported claim หารด้วยจำนวนหน่วยข้อความเชิงข้อเท็จจริงทั้งหมดของคำถามทุกข้อใน Gold_Set และ SHALL รายงานค่า citation page precision ไม่น้อยกว่า 0.95, citation recall ไม่น้อยกว่า 0.91 และ unsupported-claim rate น้อยกว่า 0.05
8. IF Evidence_Planner คืน evidence graph ที่ไม่มี node ใด, THEN THE Api_Service SHALL คืนคำตอบที่ระบุว่าไม่พบหลักฐานในเอกสารที่กำหนดพร้อมรายการ citation ว่าง, SHALL ไม่เรียก Answer_Generator และ SHALL ไม่คืน error
9. IF Answer_Generator ใช้เวลาเกิน answer_time_budget หรือคืน error, THEN THE Api_Service SHALL ยกเลิกการสร้างคำตอบของคำขอนั้น, SHALL ไม่คืนคำตอบบางส่วนที่ยังไม่ผ่าน Citation_Validator และ SHALL คืน error ที่ระบุว่าการสร้างคำตอบไม่สำเร็จเพราะเกินเวลาที่กำหนดหรือเพราะโมเดลท้องถิ่นล้มเหลว
10. WHEN Citation_Validator ตรวจคำตอบของคำขอหนึ่งเสร็จ, THE Trace_Recorder SHALL บันทึกลง query_trace ของคำขอนั้น: จำนวน citation ID ที่ส่งเข้า prompt, จำนวน citation ID ที่ผ่านการตรวจ, จำนวนหน่วยข้อความที่ถูกลบ, จำนวน unsupported claim และเวลาสร้างคำตอบเป็นมิลลิวินาที

### Requirement 18: การวัดผลและรายงานผล

**User Story:** As a ผู้ประเมินโปรเจกต์, I want ให้ตัวเลขผลการวัดทุกตัวมีวิธีคำนวณและสถานะที่ชัดเจน, so that แยกได้ว่าอะไรวัดแล้วและอะไรยังเป็นการประมาณ

#### Acceptance Criteria

1. WHEN Evaluation_Harness คำนวณ metric หนึ่งตัว, THE Evaluation_Harness SHALL จับคู่ค่าที่ประเมินกับค่าอ้างอิงด้วยกุญแจ (document_id, page) สำหรับ metric ระดับหน้า หรือ (document_id, page, field name) สำหรับ metric ระดับ field เท่านั้น, SHALL เทียบข้อความหลัง Unicode normalization form C และตัด whitespace หัวท้าย, และ SHALL ไม่นับคู่ที่มี document_id ต่างกันเป็นคู่ที่จับคู่ได้
2. IF ข้อความที่ประเมินและข้อความอ้างอิงมาจากขอบเขตหน้าที่ไม่ตรงกัน, THEN THE Evaluation_Harness SHALL ปฏิเสธการคำนวณ metric นั้น, SHALL ไม่รายงานค่าบางส่วนของ metric นั้น, SHALL คืน error ที่ระบุ document_id และเลขหน้าของทั้งสองฝ่ายที่ไม่ตรงกัน และ SHALL คำนวณ metric ตัวอื่นในการรันเดียวกันต่อโดยไม่ยกเลิกผลที่คำนวณเสร็จแล้ว
3. WHEN Evaluation_Harness คำนวณ metric ของการรันหนึ่งเสร็จ, THE Evaluation_Harness SHALL ผลิต evaluation report ที่ระบุครบทุก metric ได้แก่ ค่า metric เป็นทศนิยม 4 ตำแหน่ง, จำนวนตัวอย่างที่ใช้เป็นจำนวนเต็มไม่ติดลบ, ชนิดแหล่งอ้างอิงหนึ่งค่าจาก `teacher_ground_truth` หรือ `gold_set`, commit identifier ของโค้ดที่ใช้วัด และ timestamp ของการรัน
4. THE Evaluation_Harness SHALL ระบุสถานะของทุกตัวเลขในรายงานเป็น `measured` หรือ `estimate` โดย `measured` ใช้เฉพาะตัวเลขที่คำนวณจากข้อมูลจริงของโปรเจกต์ในการรันนั้นและมีจำนวนตัวอย่างไม่น้อยกว่า 30 ตัวอย่าง ส่วนกรณีอื่นทุกกรณี SHALL เป็น `estimate`
5. WHERE ตัวเลขใดมีสถานะ `estimate`, THE Evaluation_Harness SHALL ระบุเงื่อนไขที่ต้องทำเพื่อเปลี่ยนสถานะเป็น `measured` โดยระบุชื่อ metric, จำนวนตัวอย่างที่มีอยู่ และจำนวนตัวอย่างที่ยังขาดจากเกณฑ์ขั้นต่ำ 30 ตัวอย่าง
6. THE Evaluation_Harness SHALL รายงานทุก metric ต่อไปนี้พร้อมค่า, จำนวนตัวอย่าง และสถานะ: precision, recall และ F1 ต่อ field; field macro-F1 (เกณฑ์ ≥ 0.91); page CER (เกณฑ์ ≤ 0.05); table-cell F1 (เกณฑ์ ≥ 0.90); Recall@10 (เกณฑ์ ≥ 0.90); citation page precision (เกณฑ์ ≥ 0.95); citation recall (เกณฑ์ ≥ 0.91); unsupported-claim rate (เกณฑ์ < 0.05); version-selection accuracy (เกณฑ์ ≥ 0.98) และ SHALL ระบุผลเทียบเกณฑ์ของแต่ละ metric ที่มีเกณฑ์เป็น `pass` หรือ `fail`
7. WHEN Evaluation_Harness รันซ้ำด้วย Provenance_Store และ Gold_Set ชุดเดิม, THE Evaluation_Harness SHALL ให้ค่า metric ทุกตัวเท่ากันทุกหลักที่รายงาน (ทศนิยม 4 ตำแหน่ง) และให้เนื้อหา evaluation report เหมือนเดิมทุก field ยกเว้น timestamp ของการรันและระยะเวลาการรัน
8. IF จำนวนตัวอย่างที่จับคู่ได้ของ metric ใดน้อยกว่า 30 ตัวอย่าง, THEN THE Evaluation_Harness SHALL กำหนดสถานะของ metric นั้นเป็น `estimate`, SHALL ไม่ระบุผลเทียบเกณฑ์เป็น `pass` และ SHALL บันทึก review_issue ชนิด `metric_sample_insufficient` ที่ระบุชื่อ metric และจำนวนตัวอย่างที่จับคู่ได้
9. IF การรันซ้ำด้วย Provenance_Store และ Gold_Set ชุดเดิมให้ค่า metric ใดต่างจากการรันก่อนหน้า, THEN THE Evaluation_Harness SHALL คืน error ที่ระบุชื่อ metric และค่าทั้งสองครั้งที่ต่างกัน, SHALL กำหนดสถานะของ metric นั้นเป็น `estimate` และ SHALL คงรายงานของการรันก่อนหน้าไว้โดยไม่ลบ

### Requirement 19: บริการ API และส่วนติดต่อผู้ใช้

**User Story:** As a ผู้ใช้ทั่วไป, I want ให้ถามคำถามผ่านหน้าเว็บและเห็นหลักฐานประกอบ, so that ใช้งานได้โดยไม่ต้องเขียนโค้ด

#### Acceptance Criteria

1. THE Api_Service SHALL เปิด endpoint ครบทั้งสี่รายการ คือ ส่งคำถาม, ดึงรายการเอกสารพร้อม curriculum version ที่แสดง program, curriculum_year และ edition_status ครบทุกค่า, ดึงหน้าเอกสารตาม citation ID พร้อม bbox ของหลักฐาน และดึง query_trace ของคำขอหนึ่งตาม request_id โดย endpoint ส่งคำถาม SHALL รับข้อความคำถามยาว 1 ถึง 2,000 อักขระ และ endpoint ดึงรายการเอกสาร SHALL คืนไม่เกิน 500 รายการต่อคำขอ
2. THE Api_Service SHALL ผูก listener ไว้ที่ loopback address (127.0.0.1) เป็นค่าตั้งต้น และจำนวน connection ที่ Api_Service รับจาก address นอก loopback interface ในค่าตั้งต้น SHALL เท่ากับศูนย์
3. IF Api_Service รับคำขอที่พารามิเตอร์ไม่ตรง schema, ข้อความคำถามว่างหลังตัดช่องว่างหัวท้าย หรือข้อความคำถามยาวเกิน 2,000 อักขระ, THEN THE Api_Service SHALL คืนสถานะ 422 พร้อมรายชื่อ field ที่ไม่ถูกต้องทุก field และเหตุผลของแต่ละ field, SHALL ไม่เรียก Question_Router และ SHALL ไม่เรียก Answer_Generator
4. WHEN Api_Service คืนคำตอบของคำขอหนึ่ง, THE Web_Ui SHALL แสดงในหน้าผลลัพธ์เดียวกันครบทุกรายการต่อไปนี้: ข้อความคำตอบ, รายการ citation ทุกรายการที่แต่ละรายการมีชื่อเอกสาร เลขหน้า และ citation ID, curriculum version ที่ใช้ตอบซึ่งระบุ program, curriculum_year และ edition_status และสถานะการตรวจของ Citation_Validator หนึ่งค่าจาก `validated`, `unsupported` หรือ `rejected` พร้อมจำนวนข้อความที่ถูกลบหรือถูกทำเครื่องหมาย unsupported
5. WHEN ผู้ใช้เลือก citation หนึ่งใน Web_Ui, THE Web_Ui SHALL แสดงภาพหน้าเอกสารต้นทางที่ document_id และเลขหน้าตรงกับ citation ID นั้น, SHALL ทำเครื่องหมายบริเวณ bbox ของหลักฐานบนภาพหน้านั้น และ SHALL แสดงผลภายใน 3 วินาทีนับจากการเลือก
6. WHEN Api_Service ประมวลผลคำขอหนึ่งจนจบ, THE Trace_Recorder SHALL บันทึก query_trace หนึ่งรายการที่ผูกกับ request_id เดียวและมีทุก field ต่อไปนี้ไม่เป็น null: ระดับคำถามหนึ่งค่าจาก L1 ถึง L4, ชุด curriculum version ที่เลือก, คำค้นทุกครั้งเรียงตามลำดับการเรียก, chunk ที่ค้นได้ทุก chunk พร้อมคะแนน, การตัดสิน halt พร้อมเหตุผลการหยุด, evidence node ทุก node และผลการตรวจของ Citation_Validator
7. WHEN Api_Service รับคำขอ query_trace ด้วย request_id ที่มีอยู่, THE Api_Service SHALL คืน query_trace ที่มีค่าทุก field เหมือนเดิมทุกครั้งที่เรียกซ้ำด้วย request_id เดียวกัน
8. IF คำขอระบุ citation ID, document_id หรือ request_id ที่ไม่มีอยู่ใน Provenance_Store, THEN THE Api_Service SHALL คืน error ที่ระบุชนิดและค่าของ identifier ที่ไม่พบ, SHALL ไม่คืนเนื้อหาเอกสารหรือ query_trace ใด และ SHALL คงข้อมูลใน Provenance_Store ไม่เปลี่ยนแปลง
9. IF การประมวลผลคำถามหนึ่งคำขอใช้เวลาเกิน 120 วินาที, THEN THE Api_Service SHALL ยุติคำขอนั้น, SHALL คืน error ที่ระบุว่าเกินเวลาที่กำหนดพร้อม request_id, SHALL ไม่คืนคำตอบบางส่วน และ THE Trace_Recorder SHALL บันทึก query_trace ของคำขอนั้นพร้อมเหตุผลการยุติ
10. WHILE Api_Service ยังไม่คืนผลของคำถามที่ส่งจาก Web_Ui, THE Web_Ui SHALL แสดงตัวบ่งชี้สถานะกำลังประมวลผลของคำขอนั้นและ SHALL ไม่ส่งคำถามเดิมซ้ำ

### Requirement 20: ข้อจำกัดด้านการทำงานแบบออฟไลน์และสิทธิ์ใช้งาน

**User Story:** As a เจ้าของโปรเจกต์, I want ให้ระบบทำงานได้โดยไม่พึ่งบริการที่มีค่าใช้จ่ายและไม่ละเมิดขอบเขตของ repo อื่น, so that ส่งงานได้ตามข้อกำหนดของวิชา

#### Acceptance Criteria

1. WHILE network adapter ของเครื่องถูกปิดทั้งหมด และ model artifact กับ dependency ถูกติดตั้งครบตาม preflight check, THE KatRAG_System SHALL ทำงานสำเร็จทุกฟังก์ชัน (ingestion, index build, query ระดับ L1–L4, Api_Service, Web_Ui) โดยไม่มี error และมีจำนวน outbound request ไปยัง address ที่ไม่ใช่ loopback เท่ากับ 0
2. THE KatRAG_System SHALL ใช้เฉพาะ engine และโมเดลจากรายการปิดนี้เท่านั้น ได้แก่ PyMuPDF, Tesseract 5, Typhoon-OCR-1.5-2B (เมื่อมี GPU รองรับ CUDA), bge-m3 และ llama.cpp + Qwen3 4B GGUF Q4 โดยทุกรายการมี license แบบ open-source ที่ใช้ได้ฟรี ไม่มีรายการใดต้องซื้อ license หรือ subscription และ dependency manifest ระบุชื่อ license ของทุกรายการ (Typhoon-OCR-1.5-2B ใช้ license Apache-2.0 พร้อมเงื่อนไขเพิ่มเติมของ OpenTyphoon Terms and Conditions ซึ่งไม่มีค่าใช้จ่าย; PaddleOCR ที่ระบุไว้เดิมถอดออกเพราะเวอร์ชันที่รองรับภาษาไทย (3.x) ยังไม่ผ่านการทดสอบว่าทำงานแบบ offline ได้โดยไม่ดาวน์โหลด weight ระหว่างใช้งานจริง)
3. THE KatRAG_System SHALL ประมวลผล OCR ทุกหน้าและ LLM inference ทุกครั้งด้วย engine ที่รันเป็น local process บนเครื่องเดียวกัน โดยไม่เรียก endpoint ที่ต้องมี API key, บัญชีผู้ใช้ หรือมีค่าใช้จ่ายต่อการเรียกใช้
4. THE KatRAG_System SHALL เข้าถึงไดเรกทอรี `katgpt-rs/` แบบ read-only เท่านั้น กล่าวคือ จำนวน import ของโมดูลหรือ crate ภายใต้ `katgpt-rs/` ในซอร์สโค้ดทั้งหมดของ `project/` เท่ากับ 0 และจำนวนการสร้าง แก้ไข หรือลบไฟล์ใน `katgpt-rs/` เท่ากับ 0
5. WHERE โค้ดจาก `katgpt-rs/` ถูกนำมาใช้ซ้ำ, THE KatRAG_System SHALL เก็บสำเนาโค้ดนั้นไว้ใต้ `project/` พร้อม MIT notice ที่ระบุชื่อ repo ต้นทาง, commit หรือวันที่ที่คัดลอก, ผู้ถือ copyright และข้อความ MIT license ครบทั้งฉบับ
6. THE KatRAG_System SHALL ใช้ model weight ของ OCR และ LLM ตามที่ผู้เผยแพร่ปล่อยออกมาโดยไม่มีขั้นตอน training, fine-tune หรือการแก้ไขค่า weight และค่า SHA-256 ของไฟล์ weight ที่ใช้งานตรงกับค่าที่บันทึกไว้ตอนติดตั้ง
7. THE KatRAG_System SHALL ผ่านเกณฑ์ยอมรับทุกข้อในเอกสารนี้บนสภาพแวดล้อมอ้างอิง Windows 10 หรือใหม่กว่า (64-bit), Python 3.11.x แบบ CPU-only โดยไม่ต้องใช้ CUDA หรือ GPU backend เฉพาะของ Apple (MPS/CoreML) ยกเว้น Typhoon-OCR-1.5-2B (stage 2 ของ Ocr_Cascade) ซึ่งเป็น GPU-gated: WHERE ไม่มี GPU ที่รองรับ CUDA, THE Ocr_Cascade SHALL ข้าม stage นี้เสมอ (ตาม R5.1.1) และ WHERE มี GPU ที่รองรับ CUDA, THE KatRAG_System SHALL อนุญาตให้ใช้ CUDA เฉพาะกับ stage นี้เท่านั้น โดยส่วนอื่นของระบบทั้งหมดยังคง CPU-only ตามเดิม
8. IF preflight check ตอนเริ่มระบบพบว่า model artifact หรือ dependency ที่จำเป็นขาดไป หรือค่า SHA-256 ไม่ตรงกับที่บันทึกไว้, THEN THE KatRAG_System SHALL หยุดการเริ่มทำงานภายใน 10 วินาที, แสดง error ที่ระบุรายชื่อ artifact ที่ขาดหรือไม่ตรง, และไม่พยายามดาวน์โหลดไฟล์ใดจากเครือข่าย
9. IF มีการพยายามเรียก network endpoint ที่ไม่ใช่ loopback ระหว่าง ingestion หรือ query, THEN THE KatRAG_System SHALL ปฏิเสธการเรียกนั้น, คืน error ที่ระบุว่าละเมิดข้อจำกัด offline, และรักษาข้อมูลใน Provenance_Store ไว้ไม่เปลี่ยนแปลง

### Requirement 21: สิ่งส่งมอบและการสาธิต

**User Story:** As a ผู้ตรวจงาน, I want ให้มีเอกสารและการสาธิตที่รันได้จริง, so that ตรวจรับงานได้ครบตามเกณฑ์

#### Acceptance Criteria

1. THE KatRAG_System SHALL มีไฟล์ README ที่มีสี่ส่วนครบถ้วน ได้แก่ ขั้นตอนติดตั้ง dependency และโมเดลบน Python 3.11, คำสั่งรัน ingestion, คำสั่งรัน evaluation และคำสั่งเปิด Api_Service โดยแต่ละส่วนระบุคำสั่งที่คัดลอกไปรันได้โดยไม่ต้องแก้ไข พร้อมค่าที่คาดหมายของ artifact ที่คำสั่งนั้นผลิต
2. THE KatRAG_System SHALL มีเอกสาร ER diagram ที่จำนวนตารางในแผนภาพเท่ากับจำนวนตารางที่มีอยู่จริงใน Provenance_Store, ชื่อตารางและชื่อ field ตรงกับ schema ที่ใช้งานทุกรายการ และแสดงทุกความสัมพันธ์แบบ foreign key พร้อม cardinality ของแต่ละความสัมพันธ์
3. THE KatRAG_System SHALL มี dataset manifest, evaluation report และสไลด์นำเสนอ อยู่ในไดเรกทอรีของโปรเจกต์ โดย dataset manifest มี entry ครบ 14 เอกสารในขอบเขต และค่า metric ทุกตัวใน evaluation report ผลิตจาก Provenance_Store ชุดเดียวกับ dataset manifest นั้น
4. THE KatRAG_System SHALL มีสคริปต์สาธิตที่เมื่อเรียกด้วยคำสั่งเดียวโดยไม่ต้องป้อนข้อมูลระหว่างทาง SHALL ทำงานครบทุกขั้นตั้งแต่อ่านไฟล์ PDF, ingestion, การค้นคืน จนถึงคำตอบพร้อม citation และ SHALL ทำงานจนจบภายใน 30 นาที พร้อมคืนสถานะสำเร็จ
5. WHEN สคริปต์สาธิตทำงานจนจบ, THE KatRAG_System SHALL แสดงคำถามตัวอย่างไม่น้อยกว่าหนึ่งข้อต่อระดับครบทั้งสี่ระดับ L1, L2, L3 และ L4 โดยแต่ละข้อแสดงระดับคำถามที่จำแนกได้, คำตอบ และ citation ไม่น้อยกว่าหนึ่งรายการที่ระบุชื่อเอกสารและเลขหน้า และทุกคำตอบ SHALL ผ่านการตรวจของ Citation_Validator
6. IF สคริปต์สาธิตหยุดทำงานก่อนจบครบทุกขั้น หรือทำงานเกิน 30 นาที, THEN THE KatRAG_System SHALL คืนสถานะไม่สำเร็จพร้อม error ที่ระบุชื่อขั้นตอนที่ล้มเหลวและสาเหตุ, SHALL คงผลลัพธ์ของขั้นตอนที่เสร็จแล้วไว้ใน Provenance_Store และ SHALL ไม่แสดงคำตอบของคำถามตัวอย่างที่ยังตรวจ citation ไม่ผ่าน
7. IF สิ่งส่งมอบรายการใดจากทั้งห้ารายการ ได้แก่ README, ER diagram, dataset manifest, evaluation report และสไลด์นำเสนอ ไม่มีอยู่ในไดเรกทอรีของโปรเจกต์เมื่อเริ่มสคริปต์สาธิต, THEN THE KatRAG_System SHALL คืนสถานะไม่สำเร็จพร้อม error ที่ระบุรายการสิ่งส่งมอบที่ขาดทุกรายการ ก่อนเริ่มประมวลผลขั้นถัดไป
8. WHEN สคริปต์สาธิตถูกรันซ้ำครั้งที่สองบนเครื่องเดียวกันด้วย dataset และ Gold_Set ชุดเดิม, THE KatRAG_System SHALL คืนระดับคำถาม, ชุด curriculum version และชุด citation ID ของทุกคำถามตัวอย่างเท่ากับผลของการรันครั้งแรกทุกค่า
