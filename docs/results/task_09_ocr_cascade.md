# ผลงานที่ 9.1: ทดลอง PaddleOCR / Tesseract / Typhoon OCR 1.5 2B บนหน้าสแกนจริง

การทดสอบทั้งหมดรันจริงบนเครื่องเป้าหมาย (Windows, RTX 2050 4GB VRAM) ด้วยหน้า PDF จริงจากคลัง
14 เอกสาร ไม่ใช่ synthetic — ผลกระทบต่อ design/requirements ถูกแก้ไว้แล้วในเอกสาร spec

## 1. PaddleOCR — ตัดออกจาก cascade ทั้งหมด

`paddleocr==2.8.1` ที่ pin ไว้เดิมใน `pyproject.toml` **ไม่รองรับภาษาไทยเลย**:

```
PaddleOCR(lang='th')
AssertionError: param lang must in dict_keys(['ch', 'en', 'korean', 'japan',
'chinese_cht', 'ta', 'te', 'ka', 'latin', 'arabic', 'cyrillic', 'devanagari']),
but got th
```

สาเหตุ: โมเดล PP-OCRv5 ที่มีภาษาไทย (`th_PP-OCRv5_mobile_rec`) เปิดตัวใน **PaddleOCR 3.x**
(พฤษภาคม 2025) ซึ่งมาทีหลัง 2.8.1 และ PaddleOCR 3.x ดาวน์โหลด weight เองอัตโนมัติตอน
`PaddleOCR()` ครั้งแรก (ไม่มีโหมด local-only ที่ตรวจ sha256 ล่วงหน้าแบบที่ design ต้องการ)
ขัดกับ offline policy (R20.1, R20.9) จึงตัด PaddleOCR ออกจาก closed engine list ทั้งหมด

## 2. Tesseract 5 vs Typhoon-OCR-1.5-2B — เทียบบนหน้า OCR candidate จริง 8 หน้า

สุ่ม (seed=42) จาก 979 หน้าที่ `Page_Quality_Gate` ทำเครื่องหมายเป็น OCR candidate จริง
ครอบคลุม 5 เอกสาร และทั้ง compute path `standard`/`deep`

| ไฟล์ | หน้า | image_area_ratio | compute_path | Tesseract (s) | Typhoon (s) |
|------|------|-------------------|--------------|----------------|--------------|
| IT2565_current.pdf | 387 | 0.92 | deep | 3.00 | 140.38 |
| IT2560_old.pdf | 196 | 0.72 | deep | 4.30 | 170.76 |
| IT2560_old.pdf | 107 | 0.72 | deep | 0.95 | 32.24 |
| AIT2566_current.pdf | 181 | 0.92 | deep | 2.06 | 100.72 |
| M_AITBA2569_current.pdf | 97 | 0.57 | standard | 2.86 | 111.69 |
| M_IT2568_current.pdf | 103 | 1.00 | deep | 4.02 | 138.74 |
| M_IT2568_current.pdf | 81 | 1.00 | deep | 3.82 | 231.19 |
| IT2560_old.pdf | 272 | 0.72 | deep | 2.73 | 83.32 |

**สรุปความเร็ว**: Tesseract เฉลี่ย 3.0 s/หน้า, Typhoon เฉลี่ย 126.1 s/หน้า (**ช้ากว่า 42 เท่า**)

**ผลกระทบต่อทั้ง dataset (979 หน้า candidate)**:
- Tesseract ทุกหน้า: ~49 นาที
- Typhoon ทุกหน้า: **~34.3 ชั่วโมง** — เกินงบเวลาของโปรเจกต์มาก จึงใช้เป็น engine เดียวไม่ได้

**คุณภาพ (อ่านตรวจด้วยตาจากผลจริง)**: Typhoon แม่นกว่า Tesseract อย่างชัดเจนในทุกหน้าที่ทดสอบ
— สะกดคำผิดน้อยกว่ามาก, จัดโครงสร้างตาราง HTML ได้ถูกต้อง (รหัสวิชา/หน่วยกิต/ชื่อวิชาไทย-อังกฤษ)
ขณะที่ Tesseract ให้ข้อความไหลรวมไม่มีโครงสร้าง และอ่านตารางกรรมการ (ตัวเลขไทย) ผิดเพี้ยนสิ้นดี
ในบางหน้า (`IT2565_current.pdf` p387)

## 3. ข้อบกพร่องของ Typhoon ที่ยืนยันแล้วว่าเกิดขึ้นจริง

### 3.1 Hallucination ชื่อสถาบัน (3 จาก 8 หน้าที่ทดสอบ)

ทุกหน้าที่มีโลโก้/ตราสัญลักษณ์ Typhoon ทายชื่อสถาบันผิดเป็น **"มหาวิทยาลัยศรีนครินทรวิโรฒ"**
ทั้งที่เอกสารทั้ง 14 ไฟล์ในคลังนี้เป็นของ **สถาบันเทคโนโลยีพระจอมเกล้าเจ้าคุณทหารลาดกระบัง (KMITL)**
เท่านั้น ยืนยันจากการทดสอบหน้าปก (นอก candidate sample) ที่เห็นชื่อผิดนี้ซ้ำหลายสิบครั้งในผลลัพธ์เดียว

แก้โดยเพิ่มการตรวจชื่อสถาบันใน `Ocr_Cascade` เทียบกับค่าที่ประกาศไว้ล่วงหน้าใน
`config/katrag.toml` (`ocr.typhoon.known_institution_name`) — ไม่ตรง → คะแนน 0.00 +
`error_record: hallucinated_institution_name` (R5.1.2)

### 3.2 การวนซ้ำข้อความ (repetition loop)

หน้าปกที่มีข้อความน้อย (ทดสอบนอก candidate sample เพื่อยืนยันช่วงกรณีเลว) ทำให้ Typhoon สร้าง
ข้อความชื่อสถาบันผิดซ้ำ ๆ จนชน `max_new_tokens = 4000` (ค่าทดสอบเดิม) ใช้เวลา **568 วินาที**
สำหรับหนึ่งหน้า — ถ้าเกิดกับหน้า candidate จริงจะทำให้เวลารวมของทั้ง cascade พังได้

แก้โดยตั้งค่าเริ่มต้น `max_new_tokens = 2000`, `repetition_penalty = 1.3`,
`no_repeat_ngram_size = 6` ใน `config/katrag.toml` (`ocr.typhoon.*`) — ทดสอบกับ 8 หน้า candidate
จริงหลังตั้งค่านี้แล้วไม่พบการวนซ้ำเลย (เวลาสูงสุดคือ 231 วินาที ไม่ใช่ 500+) (R5.1.3)

### 3.3 ข้อจำกัดด้านสภาพแวดล้อม

* **VRAM ไม่พอสำหรับ BF16 เต็ม** — GPU มี 4,096 MiB, โมเดล BF16 ต้องการมากกว่า 3,884 MiB ที่ว่าง
  ทำให้ `device_map="auto"` ยก layer บางส่วนไป CPU (`Some parameters are on the meta device`)
  แก้โดยใช้ **4-bit NF4 quantization** (`bitsandbytes`) ซึ่งใช้ VRAM เพียง ~1.5 GB และรันเต็มบน
  GPU ได้ (`device_map={"": 0}`)
* **`torchvision` ขาดหายระหว่างทาง** — พบว่า torch ที่ติดตั้งไว้ก่อนหน้าเสียหาย (`torch/__init__.py`
  หาย, มีไดเรกทอรี `~orch` ค้างจากการ uninstall ที่ขัดจังหวะ) ต้องลบไฟล์ค้างและ reinstall
  `torch==2.5.1+cu121` และ `torchvision==0.20.1+cu121` ใหม่ทั้งคู่
* **HuggingFace cache ต้อง redirect ไป D: drive** — C: มีที่เหลือ ~22-35 GB ไม่พอสำหรับ weight
  ขนาด ~4 GB ของ Typhoon รวมกับ dependency อื่น ตั้ง `HF_HOME=D:\hf_cache` ก่อนดาวน์โหลด
* **`hf_transfer` จำเป็นสำหรับดาวน์โหลดให้เสร็จ** — การดาวน์โหลดแบบปกติผ่าน `huggingface_hub`
  ค้างที่ 0% นานเกิน 2 นาที ต้องติดตั้ง `hf_transfer` และตั้ง `HF_HUB_ENABLE_HF_TRANSFER=1`

## 4. การเปลี่ยนแปลง spec (บันทึกไว้เพื่อความโปร่งใส)

| ไฟล์ | การเปลี่ยนแปลง |
|------|----------------|
| `requirements.md` | R5.1 เปลี่ยน stage list เป็น Tesseract 5 → Typhoon-OCR-1.5-2B; เพิ่ม R5.1.1 (GPU-gated skip), R5.1.2 (hallucination guard), R5.1.3 (generation limits); R20.2 เปลี่ยน engine closed list; R20.7 เพิ่มข้อยกเว้น CUDA เฉพาะ stage นี้ |
| `design.md` | §4.9 อธิบายการเปลี่ยนแปลงและเหตุผล; flowchart, phase gate table, engine list, memory estimate, risk table ปรับให้ตรง |
| `config/katrag.toml` | `ocr.stage_order` เปลี่ยนเป็น `["tesseract5", "typhoon_ocr1_5_2b"]`; เพิ่ม `[ocr.typhoon]` section |
| `config/engines.toml` | ถอด `paddle_ppocrv5`, เพิ่ม `typhoon_ocr1_5_2b` พร้อม `gpu_required = true` |
| `config/value_sets.toml` | `extraction_method` เปลี่ยน `ocr_paddle` → `ocr_typhoon` |
| `katrag/store/schema.sql` | `ocr_stage_result.engine` CHECK เปลี่ยนเป็น `('tesseract5','typhoon_ocr1_5_2b')` |
| `katrag/config.py` | เพิ่ม `TyphoonConfig` dataclass และ loader |
| `pyproject.toml` | ถอด `paddleocr`/`paddlepaddle`, เพิ่ม `torch`, `torchvision`, `transformers`, `accelerate`, `bitsandbytes`, `qwen-vl-utils` พร้อม license |
| `third_party/typhoon-ocr-NOTICE.md` | ไฟล์ใหม่ — บันทึก license, แหล่งที่มา, ข้อจำกัดที่พบจริง |

## 5. สถาปัตยกรรม hybrid ที่จะ implement ในงาน 9.2–9.7

Tesseract เป็น stage 1 เสมอ (เร็ว, ครอบคลุมทุกหน้า candidate ภายใน ~49 นาที) แล้วให้
`Gain_Cost_Halter` ที่มีอยู่แล้ว (งานที่ 2.3) ตัดสินว่าหน้าไหนคุ้มส่งต่อ Typhoon (stage 2, แม่น
กว่ามากแต่ช้า) — ไม่ใช่ส่งทุกหน้า ถ้าไม่มี CUDA cascade จบด้วย Tesseract stage เดียวเสมอ ไม่ถือ
เป็น error (ผ่านเกณฑ์ CPU-only ตาม R20.7 ได้จริง เพียงคุณภาพต่ำกว่า)

## 6. สิ่งที่ยังไม่ทำ (รองาน 9.2–9.7)

* preflight check ที่ตรวจ CUDA availability และ weight sha256 จริง
* stage adapter (`stage_tesseract.py`, `stage_typhoon.py`) แบบ interface เดียวกัน
* crop cache, preprocessor, region adjudicator
* `cascade.py` ที่ประกอบทุกอย่างเข้ากับ `GainCostHalter` จริง (ตอนนี้ทดสอบ Tesseract/Typhoon
  แยกกันตรง ๆ ยังไม่ผ่าน cascade)
* property test ของ halter ในบริบท OCR escalation (9.7 เดิมคือ 9.8)


---

# ผลงานที่ 9.2: OCR dependency และ preflight check

## 1. สิ่งที่สร้าง

| ไฟล์ | หน้าที่ |
| --- | --- |
| `katrag/ingest/ocr/preflight.py` | `run_preflight()`, `HashCache`, `cuda_available()`, `tesseract_status()`, `PreflightReport`, `EngineCheck`, `WeightCheck` |
| `tests/property/test_preflight_properties.py` | 2 property (hash cache correctness/invalidation) + 2 เคสเจาะจง (GPU-gated skip, sha256 mismatch) |

## 2. บั๊กจริงที่พบและแก้

### 2.1 sha256 ของไฟล์ weight ขนาดใหญ่กินเวลาเกือบเต็มเพดาน 10 วินาที

วัดจริง: sha256 ของ `model.safetensors` ของ Typhoon (4 GB) ใช้เวลา **5 วินาทีเดียว** บน SSD
ถ้าคำนวณใหม่ทุกครั้งที่ preflight รัน (และในเฟสถัดไปต้องรวม bge-m3 ~2.3 GB และ Qwen3 4B GGUF
~2.6 GB ด้วย) จะเกิน `fail_fast_seconds = 10.0` แน่นอน

แก้ด้วย `HashCache` ที่ cache sha256 ตาม `(path, size, mtime_ns)` เก็บไว้ที่
`artifacts/preflight_hash_cache.json` — คำนวณใหม่เฉพาะไฟล์ที่ถูกแก้ไขจริง วัดผล:
- รันครั้งแรก (cache เย็น): 6.58-8.02 วินาที (ผ่านเพดานแต่ใกล้)
- รันครั้งที่สอง (cache อุ่น): **0.00-0.05 วินาที**

### 2.2 `engines.toml` ประกาศ path ของ Tesseract traineddata ผิด

ไฟล์เดิมประกาศ `weight_files = [{path = "models/tessdata/tha.traineddata", ...}]` ซึ่งเป็น
path สัมพัทธ์ใต้ `project/` แต่ Tesseract เป็น **system binary ที่ติดตั้งแยกจาก Python venv**
traineddata อยู่ข้าง binary ของระบบ (`C:\Program Files\Tesseract-OCR\tessdata\`) ไม่ใช่ใต้
`project/models/` แบบ engine อื่น ทำให้ preflight รายงาน `tesseract5` เป็น "ขาด" ทั้งที่ยืนยัน
แล้วว่า Tesseract 5.5.0 พร้อม `tha`+`eng` ติดตั้งอยู่จริงบนเครื่อง

แก้โดยเปลี่ยน `tesseract5.weight_files = []` ใน `engines.toml` (มีหมายเหตุอธิบายเหตุผล) และให้
`run_preflight()` สร้าง `WeightCheck` สังเคราะห์จากผล `tesseract_status()` ซึ่งค้นหา
`tessdata/*.traineddata` ข้าง binary จริงแทน — ไม่ต้องเรียก subprocess `tesseract --list-langs`
ที่อาจค้าง เพราะอ่านจากไฟล์ตรง ๆ

## 3. ผลการรัน preflight จริงบนเครื่องเป้าหมาย

| engine | สถานะ | หมายเหตุ |
| --- | --- | --- |
| `pymupdf` | ok | ไม่มี weight file (library ล้วน) |
| `tesseract5` | ok | พบ binary + `tha`, `eng` traineddata จากการติดตั้งระบบ |
| `typhoon_ocr1_5_2b` | ok | sha256 ตรงกับที่บันทึกไว้ (`fa5c1c15...`) |
| `bge_m3` | missing (คาดไว้) | ยังไม่ติดตั้ง — รอ Phase 6 (retrieval) |
| `llama_cpp_qwen3_4b` | missing (คาดไว้) | ยังไม่ติดตั้ง — รอ Phase 8 (answer generation) |

`cuda_available() == True` (RTX 2050 ตรวจพบจริง) ยืนยันด้วย monkeypatch ว่าเมื่อไม่มี CUDA
`typhoon_ocr1_5_2b` จะถูกข้าม (`skipped_reason = "no_cuda"`) และ **ไม่ทำให้ `EngineCheck.ok`
เป็น False** ตรงตาม R5.1.1/R20.7

## 4. การตรวจว่าไม่ถอยหลัง

* property test ยืนยัน `HashCache` ให้ sha256 ตรงกับการคำนวณตรงทั้ง cache hit/miss (30 ตัวอย่าง)
  และคำนวณใหม่ถูกต้องเมื่อไฟล์ถูกแก้ไข (บังคับ mtime ให้เปลี่ยนแน่นอน)
* เคสเจาะจงยืนยัน sha256 ไม่ตรง → รายงานเป็น `sha256_mismatch` + อยู่ใน `missing_artifacts`
  + `EngineCheck.ok = False` + `PreflightReport.ok = False`
* รันทั้งชุดทดสอบ 30 รายการผ่านหมด (เพิ่มจาก 26 เป็น 30 ด้วย property test ใหม่)
