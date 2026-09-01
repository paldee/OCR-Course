# ระบบย่อยที่ถอดออกจากโค้ดหลัก

เอกสารนี้บันทึกโค้ด 7,691 บรรทัด (30 ไฟล์) ที่ถอดออกจาก `katrag/` ในการทำความสะอาด
repo เพื่อให้เหลือเฉพาะเส้นทางที่ระบบใช้งานจริง

**เหตุผลที่ต้องบันทึกไว้:** โค้ดเหล่านี้ไม่ใช่ของเสีย — เป็นระบบที่ออกแบบและ
implement ตามสเปกไว้ครบ (มีเทสต์ผ่าน 458 ข้อ) แต่ **เส้นทางที่ให้บริการจริงไม่เรียกใช้**
เพราะเลือกวิธีที่เรียบง่ายกว่าและวัดผลได้จริงแทน ข้อมูลในเอกสารนี้จำเป็นสำหรับการนำเสนอ
เพราะอธิบายได้ว่าตัดสินใจอะไรไปบ้างและเพราะอะไร

**วิธีเรียกโค้ดคืน:** ทุกไฟล์ยังอยู่ใน git history — `git show <commit>^:<path>`
หรือ `git log --diff-filter=D --name-only` เพื่อหา commit ที่ลบ

---

## 1. OCR Cascade — ระบบ OCR สองชั้น (1,224 บรรทัด)

| ไฟล์ | บรรทัด | หน้าที่ |
|---|---:|---|
| `ingest/ocr/cascade.py` | 328 | เรียก stage ตามลำดับ + per-engine timeout + circuit breaker |
| `ingest/ocr/stage_typhoon.py` | 269 | Typhoon-OCR-1.5-2B (VLM, GPU 4-bit) |
| `ingest/ocr/preprocessor.py` | 200 | deskew / upscale / contrast ก่อนส่ง OCR |
| `ingest/ocr/stage_tesseract.py` | 157 | Tesseract 5 ผ่าน pytesseract + bbox ระดับคำ |
| `ingest/ocr/crop_cache.py` | 110 | cache ภาพ crop ต่อ region |
| `ingest/ocr/adjudicator.py` | 95 | ตัดสินผลเมื่อสอง stage ไม่ตรงกัน (IoU) |
| `ingest/ocr/stage.py` | 65 | protocol ร่วมของทุก stage |

**สิ่งที่ออกแบบไว้:** cascade 2 ชั้น (Tesseract → Typhoon) ที่ escalate เฉพาะ region
ที่ชั้นแรกคุณภาพต่ำ ตัดสินด้วย gain/cost halter มี budget รวมต่อ run และ circuit breaker

**สิ่งที่ใช้จริง:** `ingest/run_ocr.py` เรียก Tesseract ผ่าน subprocess ตรง ๆ ทั้งหน้า
(`-l tha+eng --psm 6`, 300 DPI) ไม่มี escalation

**เหตุผล — วัดได้จริง** (รายละเอียดใน [`docs/results/task_09_ocr_cascade.md`](results/task_09_ocr_cascade.md)):

- Typhoon ใช้เวลา p95 ~231 วินาที/region บน GPU 4-bit เทียบกับ Tesseract ~2.5 วินาที/หน้า
  → 950 หน้าที่ต้อง OCR = 33–60 ชั่วโมง เทียบกับ ~40 นาที
- Typhoon **แต่งข้อมูลขึ้นเอง** (hallucinate ชื่อสถาบัน) จึงต้องเพิ่ม guard
  เทียบชื่อสถาบันที่ประกาศไว้ล่วงหน้า ไม่ตรง → คะแนน 0.00
- Typhoon วนซ้ำไม่จบ ต้องตั้ง `repetition_penalty` / `no_repeat_ngram_size` คุม
- ไม่มี GPU → ต้องข้าม stage นี้เสมอ (ไม่ถือเป็น error)

**ข้อสรุปที่ยืนยันภายหลัง:** `katrag/eval/ocr_cer_eval.py` (ที่ยังอยู่) วัดพบว่าการรู้จำ
อักขระของ Tesseract อยู่ที่ **line-match CER 0.045** ใกล้เกณฑ์ 0.05 — error ส่วนใหญ่มาจาก
**ลำดับการอ่านหน้าตาราง** ไม่ใช่การอ่านตัวอักษรผิด แปลว่า cascade ไปแก้ไม่ตรงจุดตั้งแต่แรก
สิ่งที่ควรทำคือ layout analysis ซึ่งใช้ Tesseract เดิมได้

**ยังอยู่ในโค้ดหลัก:** `ingest/ocr/preflight.py` (ตรวจว่า engine/weight พร้อม) และ
`ingest/page_router.py` (ตัดสินว่าหน้าไหนต้อง OCR) — สองตัวนี้ใช้งานจริง

---

## 2. Field Extractor — สกัดฟิลด์รายวิชาแบบมี provenance ต่อฟิลด์ (1,208 บรรทัด)

| ไฟล์ | บรรทัด | หน้าที่ |
|---|---:|---|
| `ingest/fields/extractor.py` | 584 | สกัด 11 ฟิลด์ต่อรายวิชา + provenance ต่อฟิลด์ + review issue |
| `ingest/fields/prerequisite.py` | 396 | parse เงื่อนไขวิชาบังคับก่อน (and/or, ข้อความอิสระ) |
| `ingest/fields/credits.py` | 207 | parse หน่วยกิตรูป `3(2-2-5)` + ตรวจความสอดคล้อง |
| `ingest/fields/__init__.py` | 21 | — |

**สิ่งที่ใช้จริง:** `ingest/populate_courses.py` (regex + จับคู่ marker ปี/ภาคตามตำแหน่ง
ในหน้า + majority vote) และ `ingest/populate_prerequisites.py` (ตัดบล็อกตรงรหัสวิชาที่มี
หน่วยกิตตามหลัง)

**เหตุผล:** เส้นทางที่ใช้จริงเขียนขึ้นระหว่างแก้บั๊กที่ evaluation ตรวจเจอ แล้ววัดผล
เทียบ ground truth ของอาจารย์ได้ตรง ๆ (ชื่อไทย 0.919, ชื่ออังกฤษ 0.926, หน่วยกิต 0.996,
ชั้นปี 0.979, ภาค 1.000, หมวดวิชา 0.934, ประเภทวิชา 0.912) การมีสองเส้นทางที่ทำงานเดียวกัน
ทำให้แก้จุดหนึ่งแล้วไม่รู้ว่าอีกจุดเปลี่ยนตามหรือไม่

**สิ่งที่เสียไปจริง:** `course_field_provenance` (provenance แยกต่อฟิลด์) ไม่ถูกเติม
ตารางยังอยู่ใน schema แต่ว่างเปล่า — ปัจจุบัน provenance เก็บระดับรายวิชา (หน้าที่พบวิชานั้น)
ซึ่งพอสำหรับ citation แต่ตอบไม่ได้ว่า "ชื่ออังกฤษของวิชานี้มาจากพิกัดใดในหน้า"

---

## 3. Table Extractor — สกัดตารางเป็นเซลล์ (529 บรรทัด)

`ingest/table_extractor.py` — ตรวจหาโครงตาราง แปลงเป็น `table_cell`
(row/col/span + bbox + `plan_year`/`plan_semester`)

**สิ่งที่ใช้จริง:** อ่านข้อความทั้งหน้าแล้วใช้ตำแหน่งของ marker "ปีที่ N ภาคการศึกษาที่ N"
เทียบกับตำแหน่งรหัสวิชา (ใน `populate_courses.py`)

**สิ่งที่เสียไปจริง:** ตาราง `table_cell` ว่างเปล่า และ metric `table-cell F1` ใน
`eval/metrics.py` ไม่มีข้อมูลป้อน — **ถ้าจะพูดถึง metric นี้ตอนนำเสนอ ต้องระบุว่าไม่ได้วัด**

---

## 4. RAG Subsystem ที่ออกแบบไว้แต่ไม่ได้ต่อสาย (2,894 บรรทัด)

| ไฟล์ | บรรทัด | หน้าที่ที่ออกแบบไว้ |
|---|---:|---|
| `query/evidence_planner.py` | 439 | วางแผนเก็บหลักฐานหลาย hop + halter + budget |
| `query/reasoner.py` | 424 | กราฟ prerequisite + ตรวจ cycle + อนุมานเงื่อนไข |
| `query/version_resolver.py` | 395 | เลือกชุด curriculum version จากคำถาม (R10.x) |
| `query/question_router.py` | 340 | จัดระดับคำถาม L1–L4 + เลือกเส้นทางประมวลผล |
| `query/citation_validator.py` | 329 | ตรวจทุกข้อความเชิงข้อเท็จจริงว่ามี citation รองรับ ลบที่ไม่มี |
| `query/answer_generator.py` | 285 | ประกอบ context + สร้างคำตอบ + ผูก citation |
| `query/trace.py` | 211 | เขียน `query_trace` ลงฐานข้อมูล |
| `query/answer_cache.py` | 179 | cache คำตอบตาม normalized question |
| `query/query_parser.py` | 144 | แปลคำถามเป็น structured intent ด้วย LLM |
| `query/citation.py` | 107 | ชนิดข้อมูล citation + ตัวออก ID |
| `query/gemini_llm.py` | 41 | LLM backend Gemini (เลิกใช้ เปลี่ยนไป Typhoon) |

**สิ่งที่ใช้จริง:** `query/pipeline.py` — structured intent dispatch → hybrid retrieval
(lexical + dense RRF) → adaptive cutoff → Typhoon prompt → citation จากหน้าต้นทางของรหัสวิชา

**ข้อที่ต้องระบุตอนนำเสนอ (สำคัญ):** เพราะ `citation_validator.py` และ `trace.py`
ไม่ได้ต่อสาย ตัวเลขสองช่องนี้ใน response ของ `POST /ask` **คืนค่า 0 ตลอด** ไม่ใช่ค่าที่วัดได้:

- `citations_removed` = 0
- `unsupported_claims` = 0

metric `unsupported-claim rate` ใน `eval/metrics.py` จึงไม่มีข้อมูลป้อนเช่นกัน
ส่วน `query_trace` ที่บันทึกจริงเก็บใน memory (`app.state.trace_store`) ไม่ได้ลงตาราง

ตัวเลข citation ที่**วัดได้จริง**คือ citation page precision 0.632 / recall 0.580
จาก `eval/qa_eval.py` + `eval/build_gold_set.py` (ทั้งสองยังอยู่ในโค้ดหลัก)

---

## 5. katgpt-rs ports (468 บรรทัด)

| ไฟล์ | บรรทัด | ที่มา (MIT) |
|---|---:|---|
| `common/halter.py` | 176 | `crates/katgpt-core/src/gain_cost_halt.rs` |
| `common/maxsim.py` | 167 | `crates/katgpt-types/src/simd/maxsim.rs` |
| `common/phrase_boost.py` | 125 | `crates/katgpt-pruners/src/phrase_boost.rs` |

เขียนใหม่เป็น Python จากแนวคิดใน `katgpt-rs` (ไม่ import ข้าม repo ตาม R20.4/R20.5)
ทั้งสามตัวอยู่หลัง feature flag ที่**ปิดอยู่ตลอด** รอทำ ablation ซึ่งไม่ได้ทำ

- `maxsim` — late-interaction rerank ต้องใช้ per-token (multi-vector) embedding
  แต่ระบบเก็บเวกเตอร์เดียวต่อ chunk จึงเปิดใช้ไม่ได้จริงโดยไม่ rebuild index
- `phrase_boost` — domain lexicon boost ซ้อนกับ lexical retriever ที่มี synonym expansion แล้ว
- `halter` — ออกแบบให้ใช้กับ OCR escalation และ evidence hop ซึ่งทั้งสองระบบไม่ได้ต่อสาย

ดู `third_party/katgpt-rs-MIT-NOTICE.md` สำหรับ attribution ฉบับเต็ม (เก็บไว้เป็นบันทึก)

---

## 6. โมดูลที่ถูกแทนด้วยตัวใหม่ (1,368 บรรทัด)

| ไฟล์ | บรรทัด | ถูกแทนด้วย |
|---|---:|---|
| `eval/gold_set.py` | 590 | `eval/build_gold_set.py` (derive หน้าหลักฐานจาก provenance จริง) |
| `eval/gt_normalizer.py` | 438 | `eval/ocr_eval.py` อ่าน GT ด้วย `open(path,"rb")` read-only ตรง ๆ |
| `store/queries.py` | 238 | แต่ละสคริปต์เขียน SQL ที่ต้องใช้เอง |
| `index/build_embeddings.py` | 102 | `index/build_course_embeddings.py` + `index/backfill_chunk_embeddings.py` |

---

## 7. Config section ที่ไม่มีโค้ดอ่านแล้ว (349 บรรทัดใน `config.py`)

`katrag/config.py` 893 → **544 บรรทัด** และ `config/katrag.toml` 138 → **72 บรรทัด**

| section ใน `katrag.toml` | dataclass | เหตุผลที่ลบ |
|---|---|---|
| `[halt]` | `HaltConfig` | ผูกกับ `common/halter.py` |
| `[ocr]`, `[ocr.stage_timeout]`, `[ocr.escalation]`, `[ocr.typhoon]` | `OcrConfig`, `StageTimeoutConfig`, `EscalationConfig`, `TyphoonConfig` | ผูกกับ OCR cascade; `run_ocr.py` เรียก Tesseract ตรง ๆ ไม่อ่าน `stage_order` |
| `[preprocess]` | `PreprocessConfig` | ผูกกับ `ocr/preprocessor.py` |
| `[evidence]` | `EvidenceConfig` | ผูกกับ `query/evidence_planner.py` |
| `[answer]` | `AnswerConfig` | ผูกกับ `query/answer_generator.py` |
| `[router.question]` | `QuestionRouterConfig` | ผูกกับ `query/question_router.py` |
| 5 key ใน `[retrieval]` | — | `dense_p95_latency_budget_seconds`, `phrase_boost_multiplier`, `rerank_depth`, `maxsim_enabled`, `maxsim_status` — สามตัวหลังผูกกับ ports ที่ถอดออก (หัวข้อ 5) |

**ข้อค้นพบที่ควรพูดตอนนำเสนอ:** knob สองตัวที่ `POST /ask` ใช้จริง คือเพดานความยาวคำถาม
และ request timeout **เคยอ่านค่าจาก signature default ของ `create_app()` ไม่ใช่จาก
`katrag.toml`** ค่าใน `[answer].request_timeout_seconds` (120) และ
`[router.question].api_max_question_chars` (2000) จึงไม่มีผลต่อระบบที่รันอยู่

รอบนี้ย้ายทั้งสองเข้า `[api]` และต่อสายผ่าน `_app_kwargs_from_config()` ให้มีผลจริง
โดยตั้งค่าเท่ากับ default เดิม (120.0 / 2000) เพื่อไม่ให้พฤติกรรมเปลี่ยน

```
[api]
request_timeout_seconds = 120.0      # R19.9  — ต่อสายแล้ว
max_question_chars = 2000            # R19.3  — ต่อสายแล้ว
```

`[evaluation].table_cell_f1_threshold` **คงไว้** เพราะยังอยู่ในรายงานผลประเมิน แต่ค่านี้
เทียบกับตัวเลขที่วัดไม่ได้ (ดูหัวข้อ 3) — ตอนนำเสนอต้องระบุว่าไม่ได้วัด

---

## ผลต่อชุดทดสอบ

ลบไฟล์เทสต์ 21 ไฟล์ = **458 เทสต์** ที่ทดสอบระบบย่อยข้างต้น

เทสต์ที่เหลือครอบเฉพาะเส้นทางที่ใช้งานจริง (ingest text layer, chunker, provenance store,
retrieval, API, pipeline, metrics) ซึ่งตรงกับที่ระบบทำจริงมากกว่า

**ถ้าจะอ้างจำนวนเทสต์ตอนนำเสนอ** ให้ระบุว่าเป็นเทสต์ของเส้นทางที่ให้บริการจริง
ไม่ใช่ทั้งระบบที่เคยออกแบบไว้

---

## ภาคผนวก: บั๊กที่พบจากการตรวจคำถาม 19 ข้อ แต่ยังไม่ได้แก้

### 1. chunker ตัดคำตอบขาดกลางประโยค (กระทบ E3)

คำถาม "หลักสูตร IT 2565 มีกี่แขนงวิชา?" ตอบไม่ได้ แม้ข้อมูลอยู่ในฐานครบ
เอกสารหน้า 372 เขียนว่า "ให้ผ่าน **1 แขนงวิชา จาก 3 แขนงวิชา** ดังนี้" แล้วไล่รายการ 1–3

`chunker.py` ตัดข้อความช่วงนี้ออกเป็น 3 chunk:

| chunk | เนื้อหา |
|---|---|
| 5026 | `...นักศึกษาต้องเลือกเรียนรายวิชาเชี่ยวชาญเฉพาะให้ผ่าน 1` ← ตัดคาที่นี่ |
| 5027 | `1. แขนงวิศวกรรมซอฟต์แวร์ (Software Engineering) 2. แขนงเทคโนโลยีเครือข่ายและระบบ` |
| 5028 | `3. แขนงการพัฒนาสื่อประสมและเกม (Multimedia and Game Development) ...` |

วลี "จาก 3 แขนงวิชา" หายไปกับรอยตัด ทำให้ไม่มี chunk ใดตอบคำถาม "กี่แขนง" ได้ในตัวเอง

### 2. hybrid fusion ทิ้ง chunk ที่ lexical จัดอันดับ 1 (กระทบ E3)

FTS5 (`bm25`) จัดหน้า 372 เป็น **อันดับ 1** (score −13.020) สำหรับคำค้น "แขนงวิชา"
แต่ citation ที่ `POST /ask` คืนกลับมาเป็นหน้า 369, 378, 7, 376, 371 — **ไม่มีหน้า 372 เลย**
แปลว่า RRF + adaptive cutoff (ratio 0.55) กลบผลของ lexical ทิ้ง

### 2.1 การประเมินเชิงลึก + ทางเลือกการแก้ (E3 และ M2 รากเดียวกัน)

ตรวจเพิ่มเติมพบว่า **ต้นเหตุไม่ใช่ chunker เสียหาย** แต่เป็น retrieval ดึงหน้าไม่ครบ
ชุดที่ต้องใช้ แล้ว fusion ตัดหน้าที่สำคัญทิ้ง คำถามสองข้อที่ตอบไม่ได้มาจากอาการเดียวกัน:

- **E3** ("IT 2565 มีกี่แขนง") — คำตอบกระจายใน chunk 5027-5028 (หน้า 372) ซึ่งเป็น
  chunk เล็ก ๆ และ **ไม่มี chunk ใดมีคำว่า "3 แขนง" อยู่ในตัวเอง** LLM ต้องนับเอง
  แต่ได้ chunk มาไม่ครบทั้งชุด
- **M2** ("วิชาเฉพาะเลือก DSBA กี่หน่วยกิต เรียนตอนไหน") — เลข "12 หน่วยกิต วิชาชีพเลือก"
  อยู่หน้า 15 หน้าเดียว, ข้อมูลปีอยู่ในหน้าแผนเรียน (27-29), และ `plan_slot` ของ DSBA 2565
  มีแค่ 34 แถว (วิชาเลือกใช้ placeholder `06026xxx` ไม่มี `course_id` จริง จึงไม่เข้า
  plan_slot) — structured path จึงตอบเรื่องปีไม่ได้ และ retrieval ดึงมาแต่หน้าโครงสร้าง
  ที่มีเลข 6 (คนละหมวด)

**ทางเลือกการแก้ (เรียงจากเบา→หนัก):**

| ทางเลือก | ทำอะไร | ต้นทุน | ครอบคลุม |
|---|---|---|---|
| A. neighbor-chunk expansion | เมื่อ retrieval เจอหน้าใด ดึง chunk อื่นในหน้าเดียวกันมาต่อใน context | เบา — แก้แค่ `retrieve_evidence`/`build_context` ไม่แตะ index | E3 (chunk 5027-5031 อยู่หน้าเดียวกัน) |
| B. ยก lexical top-1 ให้รอด cutoff | การันตีว่าหน้าที่ bm25 จัดอันดับ 1 ไม่ถูก fusion ตัดทิ้ง | เบา — แก้ `_apply_cutoff`/fusion weight | E3, และเคสที่ lexical แม่นกว่า dense |
| C. re-chunk + re-embed ทั้งชุด | ปรับ chunker ไม่ตัดกลางรายการ/ประโยค แล้ว rebuild | **หนัก — 11,587 chunk + 11,587 embedding + 1,419 course embedding** | ครอบคลุมสุด แต่เสี่ยง regression ทั้งระบบ |

**ข้อเสนอ:** ทำ A+B ก่อน (เบา, ไม่แตะ index, ทดสอบง่าย) แล้ววัดผลกับ E3/M2 ถ้ายังไม่พอ
ค่อยพิจารณา C ส่วน M2 เรื่อง "ปีที่เรียน" อาจต้องเสริม structured ให้ผูก plan_slot กับ
วิชา placeholder ด้วย ซึ่งเป็นงานแยกต่างหาก

**ยังไม่ลงมือ** เพราะกระทบ retrieval หลักที่ตอนนี้ตอบ 15/19 ข้อได้ถูก ต้องมี regression
guard ก่อน (รอการตัดสินใจของเจ้าของโปรเจกต์)

### 3. prerequisite false positive ที่เหลือ (~3 จาก 102 วิชา)

`populate_prerequisites.py` ยังจับรหัสวิชาถัดไปมาเป็น prereq ใน 3 กรณี
(`AIT/2566 90642058`, `BIT/2565 06036129`, `IT/2566 06018686`) ทั้งหมดเป็นวิชาศึกษาทั่วไป
ที่เอกสารเขียน "วิชาบังคับก่อน : ไม่มี" แต่รูปแบบหน้ามี OCR noise คั่นจนตัวตรวจ none-marker
ไม่ match — ไม่กระทบคำถามทั้ง 19 ข้อ
