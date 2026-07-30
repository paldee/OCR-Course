# Agent Handoff — KatRAG-lite Curriculum Q&A System (ฉบับเต็ม)

> เขียน: 2026-07-31
> โปรเจกต์: `d:\kmitl\kmitl_ISD\project`
> GitHub: https://github.com/paldee/OCR-Course.git (branch: main)
> วัตถุประสงค์: ระบบ RAG ตอบคำถามหลักสูตร KMITL จาก PDF 14 ไฟล์ (3,689 หน้า)

---

## 1. ภาพรวมระบบ

ระบบ KatRAG-lite ประกอบด้วย:
- **Ingestion pipeline**: อ่าน PDF → text extraction + PUA normalization → OCR (Tesseract5) → chunking → structured field extraction (course/plan_slot)
- **Retrieval pipeline**: Hybrid search (lexical + dense embedding RRF) → Typhoon LLM → คำตอบพร้อม citation
- **Serving**: FastAPI server (localhost:8000) + Web UI

---

## 2. สิ่งที่ทำเสร็จแล้ว ✅

### 2.1 Text Extraction + Normalization
- PyMuPDF `page.get_text()` ดึง text layer 3,689 หน้า
- **Thai PUA font fix**: เอกสารใช้ Private Use Area (U+F70x) แทนวรรณยุกต์ → สร้างตาราง mapping 13 ตัว → `normalize_text()` แปลงก่อนเก็บ
- Migration: แก้ทั้ง `page.page_text`, `chunk.text`, `chunk.heading` ใน DB ที่มีอยู่

### 2.2 OCR (ขั้นที่ 2)
- **975 หน้า** ที่เป็น OCR candidate (low text + image) ถูก OCR ด้วย Tesseract5
- ผลลัพธ์: 950/975 หน้า improved (97.4%), รวม 2.1M chars extracted
- เก็บใน `ocr_stage_result` (engine='tesseract5', quality_score, elapsed_ms)
- เก็บใน `region` (page-level crop, adjudication_json='{}')
- `page.page_text` updated เมื่อ OCR ได้มากกว่า text layer 1.5x
- **Typhoon-OCR-1.5-2B**: cached (4GB) แต่ RTX 2050 4GB VRAM ไม่พอ run เต็ม (offload ช้ามาก) → ใช้เป็น "deep path" ในอนาคต ยังไม่ได้รันจริง
- เวลาทั้งหมด: 63 นาที (CPU Tesseract)

### 2.3 Chunking + FTS5
- 11,587 chunks (หลัง OCR rebuild)
- FTS5 `chunk_fts` (unicode61 tokenizer, remove_diacritics 0): 11,587 entries
- Rebuild ทุกครั้งที่ content เปลี่ยน

### 2.4 Structured Field Extraction (ขั้นที่ 1)
- `katrag/ingest/populate_courses.py`: regex-based parser สกัดรหัสวิชา 8 หลัก + ชื่อไทย/EN + credits
- Pre-pass: map page→year/semester ข้ามturn chunks (carry forward)
- ผลลัพธ์: **1,611 courses**, **2,649 plan_slots** ครบทุก 13 curriculum versions
- Query ได้จริง: `SELECT course JOIN plan_slot JOIN curriculum_version WHERE program='DSBA' AND year=1`

### 2.5 Lexical Retriever
- `katrag/query/retriever.py`:
  - pythainlp word segmentation
  - Thai stopword removal (40+ คำ)
  - Synonym expansion ("เขียนโปรแกรม" → "โปรแกรม")
  - Tone-insensitive matching (ตัดวรรณยุกต์ก่อนเทียบ)
  - Program/year structured filter (detect DSBA/IT/AIT/BIT)
  - Heuristic scoring: OR match + bonus heading/year-level/course-table/code-count

### 2.6 Semantic Retrieval (ขั้นที่ 3)
- **Embedding model**: bge-m3 (dim=1024) รัน local ด้วย GPU (RTX 2050)
  - ไฟล์: `D:\hf_cache\hub\models--BAAI--bge-m3` (2.12 GB safetensors)
  - ต้อง: torch ≥2.6, transformers 4.57.6
- **chunk_embedding table**: 11,547/11,587 rows (99.7%)
  - 960 จาก Gemini API (dim=3072) + 10,587 จาก bge-m3 local (dim=1024)
  - ⚠️ **ปัญหา dim ไม่ตรง**: 960 rows มี dim=3072 (Gemini), 10,587 rows มี dim=1024 (bge-m3)
  - ⚠️ ควร rebuild 960 rows ด้วย bge-m3 ให้ dim uniform = 1024
- **Dense search**: `katrag/index/dense_search.py` — in-memory full-scan cosine
- **Hybrid retriever**: `katrag/query/semantic_retriever.py`
  - RRF fusion: lexical_weight=0.5, dense_weight=0.5, rrf_k=60
  - Both-arm bonus: 1.3x เมื่อ chunk อยู่ทั้ง lexical + dense
  - Falls back to lexical-only ถ้า dense index ว่าง

### 2.7 LLM (Typhoon)
- `katrag/query/typhoon_llm.py`: OpenAI-compatible API
- model: `typhoon-v2.5-30b-a3b-instruct` (MoE, active ~3B params)
- endpoint: `https://api.opentyphoon.ai/v1`
- API key: ใน `.env` (`TYPHOON_API_KEY`)
- Prompt: สั่งให้ตอบไทย + ระบุทุกวิชาที่เกี่ยวข้อง + citation [n]

### 2.8 Web UI + API Server
- FastAPI: `katrag/api/service.py`
- Endpoints: POST /ask, GET /documents, GET /pages/{id}, GET /traces/{id}
- Web UI: `web/index.html`, `web/main.js`, `web/style.css`
- Citation viewer: แสดงข้อความ chunk จริง (ไม่ใช่ placeholder)
- Net guard: allow_external สำหรับ serve (Typhoon + Gemini API ออกเน็ตได้)

### 2.9 Specs
- `semantic-rag-katgpt`: requirements.md (11 requirements, EARS format) + design.md (สถาปัตยกรรมครบ)
- `curriculum-ocr-rag`: spec เดิมที่ออกแบบทั้งระบบ (79 tasks, implement ครบแล้ว)

### 2.10 Git
- ทุก commit push ไป GitHub แล้ว
- `.gitignore`: PDF (1.6GB), DB (25MB), .env, models/, __pycache__, temp scripts

---

## 3. สิ่งที่กำลังเป็นปัญหา / ข้อจำกัด

### 3.1 LLM (Typhoon) อ่อน
- **ตอบไม่ครบ**: ส่ง context ที่มีวิชา 5 ตัว → Typhoon ตอบแค่ 3
- **ซ้ำรายการ**: LLM repeat chunk เดิมหลายครั้ง (dedup อ่อน)
- **ตีความกว้างเกิน**: ถาม "เขียนโปรแกรม" → รวม MIS/DB ด้วย
- **แก้ได้ด้วย**: เปลี่ยน LLM (Gemini/GPT) หรือ completeness backfill (deterministic)

### 3.2 Embedding dim ไม่ uniform
- 960 rows เป็น dim=3072 (Gemini API) ส่วน 10,587 เป็น dim=1024 (bge-m3)
- Dense search ที่ load จะ error ถ้า dim ไม่ตรงกัน
- **แก้**: DELETE FROM chunk_embedding WHERE dim=3072; แล้วรัน bge-m3 rebuild 960 chunks ที่เหลือ (~10 วินาที)

### 3.3 Typhoon-OCR ยังไม่ได้ใช้จริง
- Model cached (4GB) แต่ VRAM ไม่พอ (4GB GPU = offload ช้ามาก)
- เป็น "deep path" สำหรับหน้าที่ Tesseract ผลไม่ดี
- **แก้**: ใช้เฉพาะหน้าที่ Tesseract quality_score ต่ำ (targeted, ไม่ batch ทั้งหมด)

### 3.4 Evaluation Harness ยังไม่ได้รันจริง
- Code มีอยู่ (`katrag/eval/harness.py`, `metrics.py`)
- Gold set อยู่ใน `data/teacher_gt/` (read-only สำหรับวัดผล)
- ยังไม่ได้สร้าง test questions + วัด Recall@k / answer completeness
- ยังไม่ได้รัน ablation (MaxSim on/off, phrase boost on/off, hybrid vs lexical)

### 3.5 katgpt Components ยัง flag OFF
- MaxSim reranker: code พร้อม (`katrag/common/maxsim.py`), flag `maxsim_enabled=False`
- Phrase boost: code พร้อม (`katrag/common/phrase_boost.py`), ยังไม่ wire
- TurboQuant: ยังไม่ port
- **ต้อง**: รัน ablation พิสูจน์ว่าช่วยจริง ก่อนเปิด

---

## 4. DB Schema สำคัญ (artifacts/katrag.sqlite3)

| ตาราง | rows | หมายเหตุ |
|-------|------|---------|
| document | 14 | PDF 14 ไฟล์ |
| curriculum_version | 13 | IT/DSBA/AIT/BIT/AITBA × years |
| page | 3,689 | ทุกหน้า |
| page_metrics | 3,689 | quality score, is_ocr_candidate |
| chunk | 11,587 | text chunks (หลัง OCR rebuild) |
| chunk_embedding | 11,547 | ⚠️ dim mixed (960×3072 + 10587×1024) |
| chunk_fts | 11,587 | FTS5 full-text index |
| course | 1,611 | structured courses |
| plan_slot | 2,649 | ปี/เทอม/วิชา mapping |
| region | 950 | OCR regions (page-level) |
| ocr_stage_result | 975 | Tesseract5 OCR results |
| provenance | ~20K | ที่มาของทุก field |
| review_issue | 22 | issues found during processing |
| table_cell | 0 | ⚠️ ยังไม่ populate (Table_Extractor ไม่ได้ wire) |
| rule | 0 | ⚠️ ยังไม่ populate |
| gold_set | 0 | ⚠️ ยังไม่สร้าง evaluation data |
| query_trace | 0 | ถูก store in-memory ไม่ persist |

---

## 5. ไฟล์สำคัญ

### Source Code
| ไฟล์ | บทบาท |
|------|--------|
| `katrag/api/service.py` | FastAPI `/ask` — hybrid retriever + Typhoon |
| `katrag/query/retriever.py` | Lexical retriever (lexical arm) |
| `katrag/query/semantic_retriever.py` | Hybrid search (lexical + dense RRF) |
| `katrag/query/typhoon_llm.py` | Typhoon LLM backend |
| `katrag/index/gemini_embedder.py` | Gemini Embedding API (ถูก rate limit) |
| `katrag/index/build_embeddings.py` | Batch embed via Gemini (ใช้ไม่ได้แล้ว — rate limited) |
| `katrag/index/dense_search.py` | In-memory dense index (cosine full-scan) |
| `katrag/ingest/run_ocr.py` | OCR batch pipeline (Tesseract5) |
| `katrag/ingest/populate_courses.py` | Regex-based course/plan_slot extraction |
| `katrag/ingest/manager.py` | Ingestion orchestrator |
| `katrag/ingest/text_extractor.py` | PDF text layer extraction |
| `katrag/common/normalize.py` | Thai PUA mapping + text normalization |
| `katrag/common/maxsim.py` | MaxSim reranker (from katgpt-rs, flag OFF) |
| `katrag/common/phrase_boost.py` | Phrase boost (from katgpt-rs, not wired) |
| `katrag/common/net_guard.py` | Offline enforcement (allow_external for serve) |
| `katrag/cli/__main__.py` | CLI: preflight, ingest, index, serve, evaluate |
| `katrag/eval/harness.py` | Evaluation harness (not yet run) |

### Config
| ไฟล์ | หมายเหตุ |
|------|---------|
| `.env` | GEMINI_API_KEY + TYPHOON_API_KEY |
| `config/katrag.toml` | Main config |
| `config/domain_lexicon.toml` | Phrase boost terms |
| `config/value_sets.toml` | Valid categories/types for field extraction |

### Data
| Path | หมายเหตุ |
|------|---------|
| `Information_Technology_Course/` | PDF 14 ไฟล์ (1.6 GB, gitignored) |
| `data/teacher_gt/` | Ground truth สำหรับวัดผล ONLY |
| `artifacts/katrag.sqlite3` | SQLite DB (25 MB, gitignored, regenerable) |

---

## 6. Environment

| Component | Version/Detail |
|-----------|---------------|
| Python | 3.11 |
| PyTorch | 2.6.0+cu124 |
| Transformers | 4.57.6 |
| torchvision | 0.21.0+cu124 |
| Tesseract | 5.5.0 (Thai lang ✓) |
| GPU | NVIDIA RTX 2050, 4GB VRAM |
| HF_HOME | D:\hf_cache |
| bge-m3 | cached (2.12 GB safetensors) |
| Typhoon-OCR-1.5-2B | cached (4 GB) |
| OS | Windows |

---

## 7. คำสั่งที่ใช้บ่อย

```bash
# เริ่ม server (hybrid search + Typhoon LLM)
python -m katrag.cli serve

# Build embeddings ด้วย bge-m3 local GPU
# (ต้องสร้าง script ใหม่ — ดู section 8)
python _build_embed_local.py

# Re-populate courses
python -m katrag.ingest.populate_courses

# รัน OCR (เสร็จแล้ว ไม่ต้องรันอีก)
python -m katrag.ingest.run_ocr

# ดู embedding progress
python -c "import sqlite3; c=sqlite3.connect('artifacts/katrag.sqlite3'); print(c.execute('SELECT COUNT(*) FROM chunk_embedding').fetchone()[0])"

# Rebuild FTS5
python -c "import sqlite3; c=sqlite3.connect('artifacts/katrag.sqlite3'); c.execute(\"INSERT INTO chunk_fts(chunk_fts) VALUES('rebuild')\"); c.commit()"
```

---

## 8. สิ่งที่ต้องทำต่อ (เรียงลำดับ)

### 8.1 แก้ embedding dim ให้ uniform (5 นาที)
```python
# ลบ Gemini embeddings (dim=3072) แล้ว rebuild ด้วย bge-m3
import sqlite3
conn = sqlite3.connect("artifacts/katrag.sqlite3")
conn.execute("DELETE FROM chunk_embedding WHERE dim = 3072")
conn.commit()
# จากนั้นรัน bge-m3 embed script (~10 วินาทีสำหรับ 960 chunks)
```

### 8.2 Typhoon harness: completeness backfill (ตาม spec R7.3)
- ตรวจจับ enumeration question
- สกัดหน่วยข้อมูลจาก evidence (regex course code + credits)
- เปรียบเทียบกับสิ่งที่ LLM ตอบ → เติมหน่วยที่ขาด
- ลบ duplicates (dedup)

### 8.3 Evaluation harness (ตาม spec R9)
- สร้าง gold_set จาก teacher_gt (30+ questions)
- วัด Recall@k (k=5,10,20) + answer completeness
- แยกสาเหตุ: retrieval_limited vs llm_limited
- รัน ablation: MaxSim on/off, phrase boost on/off, hybrid vs lexical

### 8.4 katgpt activation (ตาม spec R5, R6)
- Ablation MaxSim → ถ้า Recall@10 ดีขึ้น → เปิด
- Ablation phrase boost → ถ้าไม่ถดถอย → เปิด
- (Optional) Port TurboQuant สำหรับบีบ embedding

### 8.5 (Optional) เปลี่ยน LLM
- Gemini API (ถ้า quota reset) หรือ GPT-4o mini → instruction-following ดีกว่า Typhoon มาก
- ลดปัญหา "ตอบไม่ครบ/ซ้ำ" โดยไม่ต้องแก้ code

---

## 9. หลักการบังคับ

1. **Teacher GT** (`data/teacher_gt/`) = วัดผลเท่านั้น ห้ามเป็นแหล่งคำตอบ
2. **katgpt-rs** (`d:\kmitl\kmitl_ISD\katgpt-rs`) = read-only ห้าม import/แก้ไข
3. **Net guard**: ทุก pipeline (ingest/index/evaluate) = offline เข้ม; serve = allow Typhoon+Gemini
4. **คำตอบ**: ภาษาไทย
5. **ข้อมูล**: สกัดจาก PDF เท่านั้น ห้ามแหล่งภายนอก

---

## 10. Background Processes

ณ เวลา handoff:
- Server อาจยังรัน (terminal 24, port 8000) — stop ก่อน restart ถ้าต้องการ
- ไม่มี batch process ค้าง

---

## 11. Known Issues / Technical Debt

| Issue | Impact | Fix |
|-------|--------|-----|
| chunk_embedding dim mixed (3072 vs 1024) | Dense search อาจ error | DELETE WHERE dim=3072 + rebuild |
| Typhoon LLM ตอบซ้ำ/ไม่ครบ | คุณภาพคำตอบ | Backfill + dedup หรือเปลี่ยน LLM |
| table_cell = 0 rows | Schema ไม่ populate เต็ม | Wire Table_Extractor หรือ regex |
| query_trace in-memory only | ไม่ persist | Write to DB |
| gold_set = 0 rows | ยังวัดผลไม่ได้ | สร้างจาก teacher_gt |
| Typhoon-OCR ยังไม่ใช้จริง | ขาด deep OCR path | รัน targeted (หน้า quality ต่ำ) |
