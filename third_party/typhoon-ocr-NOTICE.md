# Third-party notice — Typhoon-OCR-1.5-2B

โปรเจกต์นี้ใช้ model weight `scb10x/typhoon-ocr1.5-2b` (Hugging Face) เป็น stage 2 ของ
`Ocr_Cascade` (design §4.9) โดยไม่มีการ training, fine-tune หรือแก้ไขค่า weight ใด ๆ
(R20.6) และใช้เฉพาะเมื่อเครื่องมี GPU ที่รองรับ CUDA (GPU-gated, R5.1.1, R20.7)

## แหล่งที่มา

| รายการ | ค่า |
|--------|-----|
| Model repository | https://huggingface.co/scb10x/typhoon-ocr1.5-2b |
| Base model | Qwen/Qwen3-VL-2B-Instruct |
| ผู้เผยแพร่ | SCB 10X (scb10x / typhoon-ai) |
| วันที่ดาวน์โหลด | 2026-07-28 |
| License | Apache-2.0 พร้อมเงื่อนไขเพิ่มเติม OpenTyphoon Terms and Conditions |
| ค่าใช้จ่าย | ไม่มี — เป็นการดาวน์โหลด weight ครั้งเดียวมารันเป็น local process ไม่มี API key/subscription |
| ที่จัดเก็บ weight ในเครื่องนี้ | `D:\hf_cache\hub\models--scb10x--typhoon-ocr1.5-2b\` (นอก workspace ของ `project/` เพราะไฟล์มีขนาด ~4 GB — บันทึก sha256 ใน `config/engines.toml`) |

## เงื่อนไขการใช้งานเพิ่มเติม (OpenTyphoon Terms and Conditions)

การใช้โมเดลนี้ต้องยอมรับเงื่อนไขที่ https://opentyphoon.ai/tac และรับทราบ privacy notice ที่
https://opentyphoon.ai/privacy ตามที่ผู้เผยแพร่ระบุไว้ในหน้า model card เงื่อนไขเหล่านี้เป็น
เงื่อนไขเพิ่มเติมจาก Apache-2.0 (ไม่ใช่ license คนละฉบับ) และไม่มีค่าใช้จ่ายหรือข้อจำกัดที่ขัดกับ
การใช้งานแบบ offline local inference ของโปรเจกต์นี้

## ข้อจำกัดที่พบจากการทดสอบจริง (บันทึกไว้เพื่อความโปร่งใส — ดู `docs/results/`)

* โมเดลนี้ **ถูกฝึกให้ใช้กับ prompt เดียวที่ผู้เผยแพร่กำหนดไว้เท่านั้น** ไม่ทำงานถูกต้องกับ
  prompt อื่น (ระบุไว้ใน model card)
* พบ **hallucination ชื่อสถาบันการศึกษา** บนหน้าที่มีโลโก้/ตราสัญลักษณ์ (ทายผิดเป็น
  "มหาวิทยาลัยศรีนครินทรวิโรฒ" ทั้งที่เอกสารทั้งหมดในคลังนี้เป็นของสถาบันเทคโนโลยี
  พระจอมเกล้าเจ้าคุณทหารลาดกระบัง) — `Ocr_Cascade` ตรวจสอบชื่อสถาบันกับค่าที่ประกาศไว้ใน
  `config/katrag.toml` (`ocr.typhoon.known_institution_name`) แล้วให้คะแนน 0.00 เมื่อไม่ตรง
* พบ **การวนซ้ำข้อความ (repetition loop)** บนหน้าที่มีข้อความน้อย/มีแต่โลโก้ ทำให้ใช้เวลา
  ประมวลผลนานผิดปกติ (วัดได้สูงสุด 568 วินาทีต่อหน้าในการทดสอบ) — จำกัดด้วย
  `max_new_tokens`, `repetition_penalty` และ `no_repeat_ngram_size` ตามค่าใน
  `config/katrag.toml`
* ความเร็วเฉลี่ยที่วัดได้จริงบน RTX 2050 (4bit NF4 quantization): **~126 วินาที/หน้า**
  ทำให้ไม่เหมาะให้เป็น stage เดียวสำหรับทุกหน้า OCR candidate (979 หน้า ≈ 34 ชั่วโมง)
  จึงใช้ร่วมกับ Tesseract 5 ผ่าน `Gain_Cost_Halter` แทน
