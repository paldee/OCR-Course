# Requirements Document

## Introduction

เอกสารนี้กำหนดความต้องการของฟีเจอร์ **semantic-rag-katgpt** ซึ่งต่อยอดจากระบบ **KatRAG-lite** (spec `curriculum-ocr-rag`) ที่ทำงานบนคลังเอกสารหลักสูตร PDF จำนวน 14 ไฟล์ 3,689 หน้า 9,443 chunk ใน `artifacts/katrag.sqlite3` โดยแต่ละ chunk ผูกกับ `curriculum_version` (program ∈ {IT, DSBA, AIT, BIT, AITBA} + curriculum_year + edition_status ∈ {current, old}) และมี text layer ที่ผ่านการ normalize ภาษาไทยแล้ว

ปัญหาที่ต้องแก้คือ เส้นทางตอบคำถามที่ให้บริการจริง (`POST /ask` ใน `katrag/api/service.py`) ยังใช้ **การค้นแบบ lexical เท่านั้น** (`katrag/query/retriever.py`: tokenize + ตัด stopword + ขยาย synonym + LIKE substring แบบทนวรรณยุกต์ + filter program/ปี + heuristic scoring) ซึ่งจับคู่เฉพาะรูปคำที่ปรากฏบนผิวเอกสาร ไม่ใช่ความหมาย คำถามที่ใช้ถ้อยคำต่างจากเอกสารจึงพลาดผลลัพธ์ได้ ขณะเดียวกันมีองค์ประกอบที่เขียนและทดสอบไว้แล้วแต่ยัง **ไม่ถูกเปิดใช้งานในเส้นทางให้บริการ** ได้แก่ `BgeM3Embedder`, `DenseIndex`, `hybrid_retriever.retrieve`, `maxsim`, และ `phrase_boost` รวมทั้งตาราง `chunk_embedding` ที่ยังมี 0 แถว และไม่มีไฟล์น้ำหนักโมเดล bge-m3 ในเครื่อง

ฟีเจอร์นี้ครอบคลุมสามเสาหลัก:
- **เสา A — ค้นเชิงความหมายให้ใช้งานได้จริง**: จัดหาน้ำหนักโมเดล bge-m3 (ONNX) ให้พร้อมแบบ offline, สร้างและบันทึก chunk embedding ลง store, ต่อ Hybrid_Retriever (lexical + dense RRF) เข้าเส้นทาง `/ask`, คงการกรอง program/version, และทำงานได้ภายใต้ข้อจำกัด offline ด้วย latency ที่ยอมรับได้
- **เสา B — นำแนวคิด katgpt มาเปิดใช้พร้อมพิสูจน์ผล**: เปิดใช้ MaxSim_Reranker และ Phrase_Booster ต่อเมื่อมีหลักฐานเชิงตัวเลขว่าช่วยยกคุณภาพการค้น ไม่ใช่เปิดโดยไม่ผ่าน ablation
- **เสา C — ปรับ harness ของ Typhoon ให้มีประสิทธิภาพสูงสุด**: ยกคุณภาพความครบถ้วนและความสอดคล้อง (faithfulness) ของคำตอบภายใต้ LLM ที่ instruction-following อ่อน (typhoon-v2.5-30b-a3b-instruct, active ~3B) ด้วยการปรับ prompt/การประกอบ context/กลยุทธ์การสร้างคำตอบ/การยึดโยง citation และมี Evaluation_Harness ที่วัดคุณภาพคำตอบเทียบ Teacher_Ground_Truth เพื่อแยกให้ได้ว่าความผิดพลาดมาจากการค้น (RAG) หรือจากตัว LLM

หลักการที่บังคับตลอดทั้งเอกสาร: (1) Teacher_Ground_Truth ใน `data/teacher_gt/` ใช้เพื่อ **การวัดผลเท่านั้น** ห้ามเป็นแหล่งของคำตอบ; (2) ระบบต้องสกัดข้อมูลจากคลังเอกสารเอง ห้ามใช้แหล่งข้อมูลภายนอก; (3) ต้องคง lexical retriever เดิมไว้เป็นแขน lexical ของ Hybrid_Retriever; (4) คงหลัก offline (net_guard บล็อก socket ที่ไม่ใช่ loopback) กับทุกส่วน ยกเว้นการเรียก Typhoon LLM ที่มี waiver อยู่แล้ว การ embed ต้องรันในเครื่องและไม่ต้องใช้เครือข่ายขณะ query; (5) คำตอบเป็นภาษาไทย

## Glossary

- **KatRAG_System**: ระบบรวมของ KatRAG-lite ที่ฟีเจอร์นี้ต่อยอด
- **Serving_Pipeline**: เส้นทางประมวลผลคำถามใน `POST /ask` ตั้งแต่รับคำถามจนคืนคำตอบพร้อม citation
- **Api_Service**: บริการ FastAPI ที่เปิด endpoint ของ KatRAG_System (`katrag/api/service.py`)
- **Lexical_Retriever**: ตัวค้นแบบ lexical เดิม (`katrag/query/retriever.py`) ที่ใช้ tokenization + LIKE substring + heuristic scoring
- **Embedder**: องค์ประกอบสร้าง embedding ตาม protocol `Embedder` (`katrag/index/embedder.py`)
- **BgeM3_Embedder**: การ implement `Embedder` ด้วยโมเดล bge-m3 บน onnxruntime (CPU) มิติ embedding = 1024
- **Model_Provisioner**: องค์ประกอบที่จัดหาและตรวจสอบไฟล์น้ำหนักโมเดล bge-m3 (ONNX + tokenizer) ให้พร้อมใช้งานในเครื่อง
- **Model_Artifacts**: ชุดไฟล์ที่ BgeM3_Embedder ต้องใช้ ได้แก่ ไฟล์ `.onnx` และ `tokenizer.json`
- **Chunk_Embedding_Store**: ตาราง `chunk_embedding` ใน `artifacts/katrag.sqlite3` ที่เก็บ embedding vector ต่อ chunk
- **Dense_Index**: ดัชนี embedding แบบ exact full-scan cosine similarity (`katrag/index/dense.py`)
- **Hybrid_Retriever**: องค์ประกอบรวมผล Lexical_Retriever และ Dense_Index ด้วย Reciprocal Rank Fusion (`katrag/query/hybrid_retriever.py`)
- **MaxSim_Reranker**: องค์ประกอบจัดอันดับใหม่ด้วย late-interaction MaxSim scoring (`katrag/common/maxsim.py`)
- **Phrase_Booster**: องค์ประกอบเพิ่มน้ำหนักผลลัพธ์ตาม domain lexicon (`katrag/common/phrase_boost.py`, `config/domain_lexicon.toml`)
- **Answer_Generator**: องค์ประกอบประกอบ context และสร้างคำตอบจาก evidence โดยเรียก Typhoon_Llm
- **Typhoon_Llm**: backend LLM ผ่าน OpenTyphoon API รุ่น `typhoon-v2.5-30b-a3b-instruct` (`katrag/query/typhoon_llm.py`)
- **Citation_Grounding**: กลไกผูกทุกข้อความเชิงข้อเท็จจริงในคำตอบกับ citation ID ที่ระบบออกให้
- **Evaluation_Harness**: องค์ประกอบวัดผลและออก evaluation report (`katrag/eval/harness.py`, `katrag/eval/metrics.py`)
- **Teacher_Ground_Truth**: ชุดข้อมูลอ้างอิงของอาจารย์ใน `data/teacher_gt/` ใช้เพื่อการวัดผลเท่านั้น
- **Gold_Set**: ชุดข้อมูลอ้างอิงที่โปรเจกต์สร้างเองเพื่อครอบคลุมสิ่งที่ Teacher_Ground_Truth วัดไม่ได้
- **curriculum version**: คู่ค่า (program, curriculum_year, edition_status) โดย edition_status ∈ {`current`, `old`}
- **version filter**: การกรองผลการค้นให้เหลือเฉพาะ chunk ที่อยู่ใน curriculum version ที่ Version_Resolver เลือก
- **enumeration question**: คำถามที่คำตอบถูกต้องคือ "รายการครบชุด" ของหน่วยข้อมูล เช่น รายวิชาทั้งหมดที่มีคำว่า "โปรแกรม" ในชื่อ
- **answer completeness**: สัดส่วนของหน่วยข้อมูลที่คาดหวัง (จาก Gold_Set/Teacher_Ground_Truth) ที่ปรากฏในคำตอบจริง
- **faithfulness**: สัดส่วนของข้อความเชิงข้อเท็จจริงในคำตอบที่มีหลักฐานใน context รองรับ (ตรงข้ามกับ unsupported claim)
- **retrieval recall**: Recall@k ของ Serving_Pipeline เทียบหน้า evidence ที่ถูกต้องใน Gold_Set
- **ablation**: การเปรียบเทียบเมทริกคุณภาพระหว่างการเปิดและปิดองค์ประกอบหนึ่งบนข้อมูลชุดเดียวกัน
- **measured fact**: ตัวเลขที่ได้จากการวัดบนข้อมูลจริงและมี artifact การวัดกำกับ
- **architectural estimate**: ตัวเลขที่ประมาณเชิงสถาปัตยกรรมและยังไม่มี artifact การวัด
- **query-time**: ช่วงเวลาตั้งแต่ Api_Service รับคำถามจนคืนคำตอบ (ไม่รวมช่วง build index แบบ offline)

## หมายเหตุสถานะของเกณฑ์ตัวเลข

- เกณฑ์ที่สืบทอดจาก spec `curriculum-ocr-rag` และไฟล์ตั้งค่า `config/katrag.toml` (สถานะ `estimate` จนกว่าจะมีผลวัดบน Gold_Set ครบ `min_samples_for_measured` = 30): Recall@10 ≥ 0.90, citation page precision ≥ 0.95, citation page recall ≥ 0.91, unsupported-claim rate < 0.05, version-selection accuracy ≥ 0.98, dense search p95 latency ≤ 3.0 วินาที
- เกณฑ์ที่ฟีเจอร์นี้ตั้งเพิ่ม (สถานะ `estimate` จนกว่าจะมีผลวัด): answer completeness เฉลี่ยบน enumeration question ≥ 0.90, semantic-only recall gain, ablation non-regression margin
- ค่า measured fact ที่ทราบแล้ว: 14 เอกสาร, 3,689 หน้า, 9,443 chunk, `chunk_embedding` ปัจจุบัน 0 แถว, bge-m3 dim = 1024

## Requirements

### Requirement 1: การจัดหาน้ำหนักโมเดล bge-m3 แบบ offline

**User Story:** As a ผู้ดูแลระบบ, I want ให้ไฟล์น้ำหนักโมเดล bge-m3 พร้อมใช้งานในเครื่องและตรวจสอบความถูกต้องได้, so that การสร้าง embedding ทำงานได้โดยไม่ต้องพึ่งเครือข่ายตอน query

#### Acceptance Criteria

1. WHEN Model_Provisioner ทำงานจนสำเร็จ, THE Model_Provisioner SHALL วางไฟล์ `.onnx` และ `tokenizer.json` ของ bge-m3 ไว้ใน directory ที่ระบุในไฟล์ตั้งค่า และบันทึก SHA-256 เป็น hex ตัวพิมพ์เล็ก 64 อักขระของแต่ละไฟล์ลง manifest ของ Model_Artifacts
2. WHEN BgeM3_Embedder ถูกสร้างขึ้นจาก Model_Artifacts, THE BgeM3_Embedder SHALL รายงานมิติ embedding เท่ากับ 1024
3. IF ไฟล์ `.onnx` หรือ `tokenizer.json` ไม่มีอยู่ในเครื่อง ณ เวลาที่ต้องสร้าง BgeM3_Embedder, THEN THE Model_Provisioner SHALL คืน error ที่ระบุ path ที่คาดหวังและชื่อไฟล์ที่ขาด และ THE KatRAG_System SHALL ไม่เริ่มขั้นตอนสร้าง embedding
4. IF SHA-256 ของไฟล์ Model_Artifacts ที่พบไม่ตรงกับค่าที่บันทึกไว้ใน manifest, THEN THE Model_Provisioner SHALL คืน error ชนิด `model_artifact_mismatch` ที่ระบุชื่อไฟล์ ค่าที่คาดหวัง และค่าที่พบ และ SHALL ไม่ใช้ไฟล์นั้นสร้าง embedding
5. WHILE BgeM3_Embedder ประมวลผลการสร้าง embedding, THE BgeM3_Embedder SHALL ใช้เฉพาะ `CPUExecutionProvider` และ SHALL ไม่เปิด socket ที่ปลายทางไม่ใช่ loopback

### Requirement 2: การสร้างและบันทึก chunk embedding ลง store

**User Story:** As a นักพัฒนาระบบ, I want ให้ทุก chunk มี embedding ที่บันทึกถาวรใน store, so that Dense_Index ค้นเชิงความหมายได้โดยไม่ต้อง encode คลังใหม่ทุกครั้งที่เริ่มระบบ

#### Acceptance Criteria

1. WHEN ขั้นตอนสร้างดัชนีทำงานบนคลังเอกสารปัจจุบัน, THE KatRAG_System SHALL สร้าง embedding ด้วย BgeM3_Embedder ให้ chunk และบันทึกลง Chunk_Embedding_Store โดยจำนวนแถวใน `chunk_embedding` ที่สำเร็จเท่ากับจำนวน chunk ที่ encode สำเร็จ
2. THE Chunk_Embedding_Store SHALL บันทึกแต่ละแถวด้วย chunk identifier (`content_sha256`) ที่อ้างกลับไปยัง chunk ต้นทางได้ และ embedding vector ที่มีมิติเท่ากับ 1024 ทุกแถว
3. IF การ encode chunk ใดล้มเหลว, THEN THE KatRAG_System SHALL ข้าม chunk นั้น ประมวลผล chunk ถัดไปต่อ และบันทึก review_issue ชนิด `index_build_incomplete` ที่ระบุจำนวนและ `content_sha256` ของ chunk ที่ล้มเหลว จำนวนที่พยายาม และจำนวนที่สำเร็จ
4. WHEN ขั้นตอนสร้างดัชนีถูกเรียกซ้ำบนคลังเอกสารที่เนื้อหาไม่เปลี่ยน, THE KatRAG_System SHALL ให้ embedding ของแต่ละ chunk เท่าเดิมทุกค่า (deterministic) และจำนวนแถวใน Chunk_Embedding_Store เท่าเดิม
5. WHEN Dense_Index ถูกโหลดตอนเริ่ม Serving_Pipeline, THE Dense_Index SHALL โหลด embedding จาก Chunk_Embedding_Store โดยไม่เรียก BgeM3_Embedder เพื่อ re-encode chunk ที่มี embedding อยู่แล้ว

### Requirement 3: การต่อ Hybrid_Retriever เข้าเส้นทางให้บริการ

**User Story:** As a ผู้ใช้ที่ถามคำถามหลักสูตร, I want ให้ระบบค้นทั้งเชิงคำและเชิงความหมายรวมกัน, so that คำถามที่ใช้ถ้อยคำต่างจากเอกสารยังได้ผลลัพธ์ที่เกี่ยวข้อง

#### Acceptance Criteria

1. WHEN Api_Service ประมวลผลคำถามหนึ่งใน `POST /ask`, THE Serving_Pipeline SHALL ค้นผลลัพธ์ด้วย Hybrid_Retriever ที่รวมผลจาก Lexical_Retriever และ Dense_Index ด้วย Reciprocal Rank Fusion แทนการใช้ Lexical_Retriever เพียงตัวเดียว
2. THE Serving_Pipeline SHALL คง Lexical_Retriever เดิมไว้เป็นแขน lexical ของ Hybrid_Retriever โดยไม่ลบหรือแทนที่พฤติกรรมการค้นแบบ lexical
3. WHEN Hybrid_Retriever รวมผลลัพธ์, THE Hybrid_Retriever SHALL ใช้ค่า `lexical_top_k`, `dense_top_k`, `fusion_output_max`, `fusion_lexical_weight`, `fusion_dense_weight` และ `fusion_rrf_k` จากส่วน `[retrieval]` ของไฟล์ตั้งค่า และ SHALL คืนผลลัพธ์ไม่เกิน `fusion_output_max` รายการ
4. WHERE Version_Resolver เลือก curriculum version ให้คำถามหนึ่ง, THE Hybrid_Retriever SHALL ใช้ version filter ก่อนการให้คะแนน โดยจำนวน chunk ในผลลัพธ์ที่อยู่นอก version set ที่เลือกเท่ากับศูนย์
5. IF คำถามว่างหลังตัด whitespace หรือมีความยาวเกิน `retriever_max_question_chars` (1,000) อักขระ, THEN THE Serving_Pipeline SHALL ปฏิเสธคำถามนั้นโดยไม่เรียกดัชนีใด และคืนสถานะที่ระบุเหตุผล `empty_query` หรือ `query_too_long`
6. IF Hybrid_Retriever ไม่พบ chunk ที่เข้าเกณฑ์, THEN THE Serving_Pipeline SHALL คืนคำตอบที่แจ้งว่าไม่พบข้อมูลที่เกี่ยวข้องเป็นภาษาไทย และบันทึก trace ของคำขอนั้น
7. WHEN Serving_Pipeline คืนคำตอบหนึ่งคำขอ, THE Serving_Pipeline SHALL บันทึกลง query_trace ว่าใช้เส้นทาง hybrid retrieval พร้อมจำนวนผลลัพธ์จากแขน lexical และแขน dense

### Requirement 4: หลัก offline และ latency ของการค้นเชิงความหมายตอน query

**User Story:** As a เจ้าของโปรเจกต์, I want ให้การค้นเชิงความหมายทำงานในเครื่องและตอบภายในเวลาที่ยอมรับได้, so that ระบบยังเคารพหลัก offline และให้ประสบการณ์ใช้งานที่รวดเร็ว

#### Acceptance Criteria

1. WHILE Serving_Pipeline ประมวลผลคำถาม, THE BgeM3_Embedder SHALL encode คำถามในเครื่องด้วย onnxruntime CPU โดยไม่เปิด socket ที่ปลายทางไม่ใช่ loopback
2. THE Dense_Index SHALL ค้นด้วย exact full-scan cosine similarity โดยไม่ใช้ ANN/FAISS/HNSW และ SHALL วัด p95 latency ของการค้นเทียบงบ `dense_p95_latency_budget_seconds` (3.0 วินาที) จากไฟล์ตั้งค่า
3. IF p95 latency ของ Dense_Index เกิน `dense_p95_latency_budget_seconds`, THEN THE Dense_Index SHALL บันทึกคำเตือนที่ระบุค่า p95 ที่วัดได้และงบที่กำหนด โดยยังคืนผลลัพธ์การค้นให้ Serving_Pipeline
4. WHEN net_guard ทำงานในโหมด serve, THE KatRAG_System SHALL อนุญาตการเชื่อมต่อภายนอกเฉพาะการเรียก Typhoon_Llm ภายใต้ waiver ที่มีอยู่ และ SHALL บล็อกการเชื่อมต่อภายนอกอื่นทั้งหมดรวมถึงระหว่างการ embed คำถาม

### Requirement 5: การพิสูจน์ผล MaxSim_Reranker ก่อนเปิดใช้งาน

**User Story:** As a ผู้ประเมินระบบ, I want ให้ MaxSim reranker ถูกเปิดใช้งานต่อเมื่อพิสูจน์บนข้อมูลจริงแล้วว่าช่วยยกคุณภาพ, so that ระบบไม่เพิ่มความซับซ้อนและต้นทุนโดยไม่มีประโยชน์ที่วัดได้

#### Acceptance Criteria

1. WHILE ค่า `maxsim_enabled` ในไฟล์ตั้งค่าเป็น `false`, THE Serving_Pipeline SHALL คืนผลการจัดอันดับของ Hybrid_Retriever โดยไม่เปลี่ยนลำดับด้วย MaxSim_Reranker
2. WHEN Evaluation_Harness รัน ablation ของ MaxSim_Reranker, THE Evaluation_Harness SHALL คำนวณ Recall@10 บน Gold_Set ทั้งกรณีเปิดและปิด MaxSim_Reranker บนชุดคำถามและ curriculum version ชุดเดียวกัน และรายงานค่าทั้งสองพร้อมจำนวนตัวอย่าง
3. WHEN MaxSim_Reranker ทำงานบนผลลัพธ์ที่จัดอันดับแล้ว, THE MaxSim_Reranker SHALL จัดอันดับใหม่เฉพาะรายการอันดับ 1 ถึง `rerank_depth` จากไฟล์ตั้งค่า คงรายการที่อันดับต่ำกว่า `rerank_depth` ไว้ตามลำดับเดิมต่อท้าย และให้จำนวนผลลัพธ์เท่ากับ input ทุกครั้ง
4. WHERE ผลวัดแสดงว่า Recall@10 เมื่อเปิด MaxSim_Reranker มากกว่าเมื่อปิดอย่างน้อยตาม margin ที่กำหนดในไฟล์ตั้งค่าและไม่ต่ำกว่าเมื่อปิดในทุกระดับ k ที่รายงาน (k ∈ {5, 10, 20}), THE Evaluation_Harness SHALL บันทึกผล ablation เป็น `pass` และเปลี่ยนสถานะ `maxsim_status` เป็น `ablation_passed`
5. IF ผลวัดไม่ผ่านเงื่อนไขในข้อ 4 หรือจำนวนตัวอย่างน้อยกว่า `min_samples_for_measured` (30), THEN THE Evaluation_Harness SHALL คงค่า `maxsim_enabled` เป็น `false` และ SHALL ไม่รายงานผล ablation เป็น `pass`

### Requirement 6: การพิสูจน์ผล Phrase_Booster จาก domain lexicon

**User Story:** As a ผู้ประเมินระบบ, I want ให้ phrase boost จาก domain lexicon มีหลักฐานว่ายกคุณภาพการค้น, so that การปรับน้ำหนักด้วยคำเฉพาะสาขาไม่ทำให้ผลลัพธ์แย่ลง

#### Acceptance Criteria

1. WHEN Phrase_Booster ปรับคะแนนผลลัพธ์การค้น, THE Phrase_Booster SHALL เทียบคำหลัง NFC และยุบ whitespace ซ้อนแล้วคูณคะแนนของ chunk ที่พบ term จาก `config/domain_lexicon.toml` ด้วยตัวคูณที่ไม่เกิน `max_total_multiplier` และ SHALL คงจำนวน chunk ในผลลัพธ์เท่ากับ input
2. THE Phrase_Booster SHALL ใช้ตัวคูณ `phrase_boost_multiplier` จากส่วน `[retrieval]` ของไฟล์ตั้งค่าเป็นค่า fallback เมื่อไม่มีตัวคูณเฉพาะ category และ SHALL ให้ตัวคูณต่อ chunk อยู่ในช่วง 1.00 ถึง `max_total_multiplier`
3. WHEN Evaluation_Harness รัน ablation ของ Phrase_Booster, THE Evaluation_Harness SHALL คำนวณ Recall@10 บน Gold_Set ทั้งกรณีเปิดและปิด Phrase_Booster บนชุดคำถามชุดเดียวกัน และรายงานค่าทั้งสองพร้อมจำนวนตัวอย่าง
4. IF Recall@10 เมื่อเปิด Phrase_Booster น้อยกว่าเมื่อปิด, THEN THE Evaluation_Harness SHALL บันทึกผล ablation เป็น `fail` และ THE Serving_Pipeline SHALL ไม่เปิดใช้ Phrase_Booster ในเส้นทางให้บริการ

### Requirement 7: การประกอบ context และกลยุทธ์สร้างคำตอบให้ครบถ้วน

**User Story:** As a นักศึกษาที่ถามรายการวิชา, I want ให้คำตอบระบุรายการที่เกี่ยวข้องครบทุกรายการที่พบในหลักฐาน, so that ไม่พลาดวิชาที่มีอยู่จริงเพราะ LLM ตอบไม่ครบ

#### Acceptance Criteria

1. WHEN Answer_Generator ประกอบ context จากผลการค้น, THE Answer_Generator SHALL รวมหน่วยหลักฐานไม่เกิน `max_evidence_units` จากไฟล์ตั้งค่า โดยแต่ละหน่วยระบุหมายเลขอ้างอิง หัวข้อ curriculum version และเลขหน้า
2. WHEN คำถามหนึ่งเป็น enumeration question, THE Answer_Generator SHALL สั่งให้ Typhoon_Llm ระบุทุกหน่วยข้อมูลที่เกี่ยวข้องที่พบในหลักฐาน พร้อมรหัส/ชื่อ/หน่วยกิต/ชั้นปี-ภาค เท่าที่หลักฐานมี
3. WHERE จำนวนหน่วยข้อมูลที่เกี่ยวข้องในหลักฐานของ enumeration question มากกว่าจำนวนที่ Typhoon_Llm ระบุในคำตอบ, THE Answer_Generator SHALL เติมหน่วยข้อมูลที่ขาดจากหลักฐานด้วยตรรกะ deterministic เพื่อให้คำตอบครอบคลุมทุกหน่วยข้อมูลที่พบในหลักฐาน
4. THE Evaluation_Harness SHALL คำนวณ answer completeness เฉลี่ยบน enumeration question ใน Gold_Set เป็นสัดส่วนของหน่วยข้อมูลที่คาดหวังที่ปรากฏในคำตอบ และค่าเฉลี่ยต้องไม่น้อยกว่า 0.90 (สถานะเกณฑ์ `estimate`)
5. IF Typhoon_Llm คืนคำตอบว่างหรือเกิด error, THEN THE Answer_Generator SHALL คืนคำตอบ fallback ที่ประกอบจากหลักฐานที่ค้นได้โดยตรงเป็นภาษาไทย และบันทึกเหตุผลความล้มเหลวลง trace
6. THE Answer_Generator SHALL ยุติการสร้างคำตอบและคืนผลภายใน `answer_time_budget_seconds` จากไฟล์ตั้งค่า

### Requirement 8: การยึดโยง citation กับหลักฐาน

**User Story:** As a ผู้ใช้ที่ต้องการตรวจสอบคำตอบ, I want ให้ทุกข้อความเชิงข้อเท็จจริงในคำตอบอ้าง citation ที่ตรวจย้อนกลับได้, so that สามารถเปิดหน้าเอกสารต้นทางเพื่อยืนยันได้

#### Acceptance Criteria

1. WHEN Answer_Generator สร้างคำตอบ, THE Citation_Grounding SHALL ออก citation ID ให้ทุกหน่วยหลักฐานที่ส่งเข้า context โดย citation ID แต่ละตัวผูกกับ document identifier และเลขหน้าของหลักฐานนั้น
2. WHEN Api_Service คืนคำตอบใน `POST /ask`, THE Api_Service SHALL ให้ทุก citation ID ที่ปรากฏในคำตอบสามารถเรียกดูได้ผ่าน `GET /pages/{citation_id}` และ SHALL คืน 404 เมื่อ citation ID ไม่มีอยู่
3. IF ข้อความเชิงข้อเท็จจริงในคำตอบไม่มี citation ID ที่ระบบออกให้รองรับ, THEN THE Citation_Grounding SHALL ทำเครื่องหมายข้อความนั้นเป็น unsupported claim และบันทึกจำนวน unsupported claim ลง trace
4. THE Evaluation_Harness SHALL คำนวณ citation page precision, citation page recall และ unsupported-claim rate บน Gold_Set โดยเกณฑ์คือ citation page precision ≥ 0.95, citation page recall ≥ 0.91 และ unsupported-claim rate < 0.05 (สถานะเกณฑ์ `estimate`)

### Requirement 9: Evaluation_Harness สำหรับวัดคุณภาพและแยกสาเหตุความผิดพลาด

**User Story:** As a ผู้ประเมินระบบ, I want ให้มี harness ที่วัดคุณภาพการค้นและคุณภาพคำตอบแยกกันได้, so that บอกได้อย่างเป็นวัตถุวิสัยว่าความผิดพลาดมาจากการค้น (RAG) หรือจาก LLM

#### Acceptance Criteria

1. WHEN Evaluation_Harness รันบนชุดคำถาม, THE Evaluation_Harness SHALL คำนวณและรายงานทั้ง retrieval recall (Recall@k, k ∈ {5, 10, 20}) และ answer completeness ของคำถามแต่ละข้อในรายงานเดียวกัน
2. WHERE retrieval recall ของคำถามหนึ่งไม่น้อยกว่าเกณฑ์ Recall@10 แต่ answer completeness ของคำถามนั้นน้อยกว่าเกณฑ์ answer completeness, THE Evaluation_Harness SHALL จัดสาเหตุความผิดพลาดของคำถามนั้นเป็น `llm_limited`
3. WHERE retrieval recall ของคำถามหนึ่งน้อยกว่าเกณฑ์ Recall@10, THE Evaluation_Harness SHALL จัดสาเหตุความผิดพลาดของคำถามนั้นเป็น `retrieval_limited`
4. WHEN Evaluation_Harness ออก evaluation report, THE Evaluation_Harness SHALL บันทึกค่า metric แต่ละตัวเป็นทศนิยม 4 ตำแหน่ง จำนวนตัวอย่าง สถานะ (`measured` เมื่อจำนวนตัวอย่าง ≥ `min_samples_for_measured` มิฉะนั้น `estimate`) commit id และ timestamp
5. IF จำนวนตัวอย่างของ metric ใดน้อยกว่า `min_samples_for_measured` (30), THEN THE Evaluation_Harness SHALL กำหนดสถานะ metric นั้นเป็น `estimate`, SHALL ไม่รายงานผลเทียบเกณฑ์เป็น `pass` และ SHALL บันทึก review_issue ชนิด `metric_sample_insufficient`
6. WHEN Evaluation_Harness ถูกรันซ้ำบน input เดิม, THE Evaluation_Harness SHALL ให้ค่า metric ทุกตัวเท่าเดิมทุกหลักที่รายงาน (ทศนิยม 4 ตำแหน่ง) โดยอนุญาตให้ timestamp และเวลาประมวลผลต่างกันได้

### Requirement 10: การใช้ Teacher_Ground_Truth เพื่อการวัดผลเท่านั้น

**User Story:** As a ผู้ประเมินระบบ, I want ให้ ground truth ของอาจารย์ถูกใช้เฉพาะการวัดผล, so that ผลการประเมินสะท้อนความสามารถจริงของระบบไม่ใช่การคัดลอกเฉลย

#### Acceptance Criteria

1. WHILE Serving_Pipeline ประมวลผลคำถาม, THE Serving_Pipeline SHALL ไม่อ่านไฟล์ใน `data/teacher_gt/` เป็นแหล่งข้อมูลของคำตอบ
2. THE Serving_Pipeline SHALL สร้างคำตอบจากเฉพาะข้อมูลที่สกัดจากคลังเอกสารและบันทึกไว้ใน store เท่านั้น
3. WHERE Evaluation_Harness ต้องการค่าอ้างอิง, THE Evaluation_Harness SHALL อ่าน Teacher_Ground_Truth และ Gold_Set แบบ read-only เพื่อการเปรียบเทียบเท่านั้น
4. IF องค์ประกอบในเส้นทางให้บริการพยายามอ่าน `data/teacher_gt/` ระหว่างสร้างคำตอบ, THEN THE KatRAG_System SHALL ถือเป็นข้อผิดพลาดและบันทึก review_issue ชนิด `ground_truth_leak`

### Requirement 11: การไม่ถดถอยเทียบเส้นทาง lexical เดิม

**User Story:** As a เจ้าของโปรเจกต์, I want ให้การเปลี่ยนไปใช้ hybrid retrieval ไม่ทำให้คุณภาพแย่ลงกว่าเดิม, so that การเปิดใช้การค้นเชิงความหมายเป็นการปรับปรุงที่พิสูจน์ได้

#### Acceptance Criteria

1. WHEN Evaluation_Harness เปรียบเทียบเส้นทางการค้น, THE Evaluation_Harness SHALL คำนวณ Recall@10 บน Gold_Set ชุดเดียวกันทั้งเส้นทาง lexical-only เดิมและเส้นทาง Hybrid_Retriever และรายงานค่าทั้งสองพร้อมจำนวนตัวอย่าง
2. WHERE จำนวนตัวอย่างไม่น้อยกว่า `min_samples_for_measured` (30), THE Evaluation_Harness SHALL รายงานว่า Recall@10 ของเส้นทาง Hybrid_Retriever ไม่น้อยกว่าของเส้นทาง lexical-only เดิม
3. IF Recall@10 ของเส้นทาง Hybrid_Retriever น้อยกว่าของเส้นทาง lexical-only เดิม, THEN THE Evaluation_Harness SHALL บันทึก review_issue ชนิด `hybrid_regression` ที่ระบุค่าทั้งสองและจำนวนตัวอย่าง
