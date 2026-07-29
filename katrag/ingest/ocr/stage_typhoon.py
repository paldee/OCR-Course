"""`TyphoonStage` — stage 2 ของ cascade, GPU-gated (design §4.9, R5.1, R5.1.1-5.1.3).

ข้อบังคับที่ชั้นนี้รักษา (ยืนยันจากการทดสอบจริง — ดู `docs/results/task_09_ocr_cascade.md`)

1. **GPU-gated** — สร้าง instance นี้เฉพาะเมื่อ `torch.cuda.is_available()` เป็นจริง
   (ผู้เรียกต้องตรวจก่อนสร้าง ไม่ใช่ตรวจในนี้ เพื่อให้ `Ocr_Cascade` ตัดสินใจข้าม stage ได้
   โดยไม่ต้อง import torch เมื่อไม่มี CUDA เลย — ดู R5.1.1)
2. **จำกัดการวนซ้ำข้อความ** ด้วย `max_new_tokens`, `repetition_penalty`,
   `no_repeat_ngram_size` จากไฟล์ตั้งค่า (R5.1.3) — ยืนยันแล้วว่าไม่มีการกำกับนี้ทำให้
   หน้าที่มีข้อความน้อยกิน 568 วินาทีต่อหน้าได้
3. **ตรวจ hallucination ชื่อสถาบัน** เทียบกับ `known_institution_name` (R5.1.2) — ยืนยัน
   แล้วว่าโมเดลนี้ทายชื่อสถาบันอื่นผิดบนหน้าที่มีโลโก้ ถ้าพบชื่อสถาบันอื่นที่ไม่ตรง (ตรวจแบบ
   heuristic: มีคำว่า "สถาบัน"/"มหาวิทยาลัย" ตามด้วยชื่อที่ไม่ใช่ค่าที่ประกาศไว้) ให้
   quality_score = 0.00 ทันที โดยไม่ทิ้งข้อความ (ยังเก็บ text ไว้เพื่อตรวจสอบย้อนหลังได้)
4. **prompt คงที่ตามที่ผู้เผยแพร่กำหนด** — โมเดลนี้ถูกฝึกให้ใช้กับ prompt เดียวเท่านั้น
   (ระบุไว้ใน model card) ห้ามเปลี่ยน
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image

from katrag.common.types import BBox
from katrag.config import TyphoonConfig
from katrag.errors import OcrEngineError
from katrag.ingest.ocr.stage import StageResult

STAGE_INDEX = 2
ENGINE_NAME = "typhoon_ocr1_5_2b"

#: prompt คงที่ตามที่ผู้เผยแพร่กำหนด (model card ของ scb10x/typhoon-ocr1.5-2b) — ห้ามเปลี่ยน
#: เพราะโมเดลถูกฝึกให้ใช้กับ prompt นี้เท่านั้น (ยืนยันจาก model card: "intended to be used
#: with a specific prompt only; it will not work with any other prompts")
PROMPT = """Extract all text from the image.

Instructions:
- Only return the clean Markdown.
- Do not include any explanation or extra text.
- You must include all information on the page.

Formatting Rules:
- Tables: Render tables using <table>...</table> in clean HTML format.
- Equations: Render equations using LaTeX syntax with inline ($...$) and block ($$...$$).
- Images/Charts/Diagrams: Wrap any clearly defined visual areas (e.g. charts, diagrams, pictures) in:

<figure>
Describe the image's main elements (people, objects, text), note any contextual clues (place, event, culture), mention visible text and its meaning, provide deeper analysis when relevant (especially for financial charts, graphs, or documents), comment on style or architecture if relevant, then give a concise overall summary. Describe in Thai.
</figure>

- Page Numbers: Wrap page numbers in <page_number>...</page_number> (e.g., <page_number>14</page_number>).
- Checkboxes: Use ☐ for unchecked and ☑ for checked boxes."""

#: ตรวจการ mention สถาบัน/มหาวิทยาลัย — ใช้หา candidate ชื่อสถาบันในข้อความเพื่อเทียบกับ
#: known_institution_name (R5.1.2) จับ "สถาบัน"/"มหาวิทยาลัย" ตามด้วยอักขระไทย**ที่ติดกัน
#: ไม่มีช่องว่างคั่น**เท่านั้น (ชื่อสถาบันในคลังนี้เขียนเป็นคำเดียวต่อเนื่องไม่มีช่องว่าง เช่น
#: "สถาบันเทคโนโลยีพระจอมเกล้าเจ้าคุณทหารลาดกระบัง") ไม่ข้ามช่องว่างไปจับข้อความประโยคถัดไป
#: มิฉะนั้นจะ false positive ทันทีที่เจอ "สถาบันฯ" (คำย่อ) ตามด้วยประโยคอื่นที่ไม่เกี่ยวข้อง
#: (พบจริงตอนทดสอบ: จับ "สถาบันฯ เพื่อการจัดการเรียนที่ตรงตามสมรรถนะ..." เป็นชื่อสถาบันผิด ๆ)
_INSTITUTION_MENTION_RE = re.compile(r"(?:สถาบัน|มหาวิทยาลัย)[\u0e01-\u0e4c]+")

#: คำย่อ/ต่อท้ายที่ไม่ถือเป็นส่วนของชื่อสถาบัน — ตัดออกก่อนเทียบ (เช่น "ฯ" หลัง "สถาบัน")
_ABBREVIATION_SUFFIX = "ฯ"


class HallucinatedInstitutionNameError(OcrEngineError):
    """ข้อความคืนชื่อสถาบันที่ไม่ตรงกับค่าที่ประกาศไว้ (R5.1.2) — ไม่ raise แต่ใช้เป็น marker.

    เก็บไว้เป็น exception class เพื่อความสอดคล้อง แต่ `recognize()` ไม่ raise ตัวนี้
    (ตาม R5.1.2 ต้อง "ลดคะแนนคุณภาพลงเหลือ 0.00" ไม่ใช่ "ยกเลิก stage") ผู้เรียกที่ต้องการ
    ตรวจสอบใช้ `detect_hallucinated_institution()` แทน
    """


def detect_hallucinated_institution(text: str, known_institution_name: str) -> str | None:
    """คืนชื่อสถาบันที่พบในข้อความถ้าไม่ตรงกับ `known_institution_name`, มิฉะนั้นคืน None.

    ใช้ heuristic แบบ regex เพราะไม่มี NER ภาษาไทยที่รันแบบ offline อยู่ในระบบนี้แล้ว —
    เพียงพอสำหรับตรวจกรณีที่ยืนยันแล้วว่าเกิดขึ้นจริง (โมเดลทาย "มหาวิทยาลัยศรีนครินทรวิโรฒ"
    ผิดซ้ำ ๆ) ไม่จำเป็นต้องแม่นยำ 100% เพราะเป็น safety net ชั้นที่สอง ไม่ใช่ตัวตัดสินหลัก
    """
    known_normalized = known_institution_name.strip()
    for match in _INSTITUTION_MENTION_RE.finditer(text):
        mention = match.group(0).strip().rstrip(_ABBREVIATION_SUFFIX)
        # คำย่อ/self-reference สั้น ๆ เช่น "สถาบัน" หรือ "สถาบันฯ" (ไม่มีชื่อเฉพาะต่อท้าย)
        # ไม่ถือเป็นการระบุชื่อสถาบันอื่น — ปล่อยผ่าน ไม่ตัดสินว่า hallucinate
        if mention in ("สถาบัน", "มหาวิทยาลัย", ""):
            continue
        if known_normalized and known_normalized.startswith(mention[: min(len(mention), 10)]):
            continue
        if mention in known_normalized or known_normalized in mention:
            continue
        return mention
    return None


@dataclass(slots=True)
class TyphoonStage:
    """OCR ด้วย Typhoon-OCR-1.5-2B (Qwen3-VL 2B fine-tune) — ต้องมี CUDA (R5.1.1)."""

    config: TyphoonConfig
    name: str = ENGINE_NAME
    _model: Any = None
    _processor: Any = None

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        import torch
        from transformers import AutoModelForImageTextToText, AutoProcessor, BitsAndBytesConfig

        if not torch.cuda.is_available():
            raise OcrEngineError(
                "TyphoonStage ต้องมี CUDA — ผู้เรียกต้องตรวจ cuda_available() ก่อนสร้าง instance นี้",
                engine=ENGINE_NAME,
            )
        quant_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True,
        )
        self._model = AutoModelForImageTextToText.from_pretrained(
            self.config.model_id, quantization_config=quant_config, device_map={"": 0}
        )
        self._processor = AutoProcessor.from_pretrained(self.config.model_id)

    def recognize(self, image: np.ndarray, region: BBox, timeout_s: float) -> StageResult:
        """OCR ทั้งภาพของ region ด้วย Typhoon — คืน quality_score = 0.00 เมื่อพบ hallucination.

        หมายเหตุ: Typhoon ไม่คืน bbox ระดับคำ (`boxes` เป็น tuple ว่างเสมอ) ต่างจาก Tesseract
        ที่ให้ bbox รายคำ — Table_Extractor/Field_Extractor ต้องรองรับทั้งสองกรณี

        timeout enforcement: ใช้ cooperative `StoppingCriteria` ที่ตรวจ wall-clock deadline
        ทุก ~16 tokens — ถ้าเลยเวลาจะ signal หยุด generation ทำให้ model.generate() คืนผล
        บางส่วนหรือ raise exception; หลังจบเราตรวจ elapsed อีกครั้ง ถ้าเกินจริง → OcrEngineError
        """
        import torch
        from transformers import StoppingCriteria, StoppingCriteriaList

        self._ensure_loaded()
        crop = _crop_region(image, region)
        pil_image = _resize_if_needed(Image.fromarray(crop), self.config.image_max_dimension_px)

        messages = [
            {
                "role": "user",
                "content": [{"type": "image", "image": pil_image}, {"type": "text", "text": PROMPT}],
            }
        ]
        inputs = self._processor.apply_chat_template(
            messages, tokenize=True, add_generation_prompt=True, return_dict=True, return_tensors="pt"
        )
        inputs = inputs.to(self._model.device)

        generate_kwargs: dict[str, Any] = {
            "max_new_tokens": self.config.max_new_tokens,
            "do_sample": False,
            "repetition_penalty": self.config.repetition_penalty,
        }
        if self.config.no_repeat_ngram_size > 0:
            # 0 หมายถึงปิดการบล็อก n-gram ซ้ำ (ยืนยันจากการทดสอบ: การบล็อกทำให้รหัสวิชา/
            # หน่วยกิตที่มีเลขซ้ำกันจริงในภาพถูกบิดเบือน) — ไม่ส่ง key นี้เข้า generate() เลย
            # เมื่อปิด เพราะ transformers ตีความ 0 ไม่แน่นอนเท่ากับการไม่ส่ง argument
            generate_kwargs["no_repeat_ngram_size"] = self.config.no_repeat_ngram_size

        # ─── cooperative deadline via StoppingCriteria ───
        start = time.perf_counter()
        deadline = start + timeout_s
        _timed_out = False

        class _DeadlineStopper(StoppingCriteria):
            """ตรวจ wall-clock ทุก check (HF เรียกทุก ~16 tokens) — cooperative stop."""

            def __call__(self, input_ids: Any, scores: Any, **kwargs: Any) -> bool:
                nonlocal _timed_out
                if time.perf_counter() >= deadline:
                    _timed_out = True
                    return True
                return False

        generate_kwargs["stopping_criteria"] = StoppingCriteriaList([_DeadlineStopper()])

        try:
            with torch.no_grad():
                generated_ids = self._model.generate(**inputs, **generate_kwargs)
        except Exception as exc:  # pragma: no cover - CUDA OOM/driver error ไม่ควรพัง cascade
            raise OcrEngineError(
                "Typhoon generate() ล้มเหลว", engine=ENGINE_NAME, reason=f"{type(exc).__name__}: {exc}"
            ) from exc
        elapsed_ms = int((time.perf_counter() - start) * 1000)

        # ตรวจ deadline อีกครั้งหลัง generate เสร็จ — อาจเลยเพราะ cooperative check ไม่ทัน
        if _timed_out or (time.perf_counter() - start) > timeout_s:
            raise OcrEngineError(
                "Typhoon เกิน timeout ที่กำหนด (cooperative deadline)",
                engine=ENGINE_NAME,
                timeout_s=timeout_s,
                elapsed_s=round(time.perf_counter() - start, 1),
            )

        trimmed = [out[len(inp) :] for inp, out in zip(inputs.input_ids, generated_ids)]
        text = self._processor.batch_decode(
            trimmed, skip_special_tokens=True, clean_up_tokenization_spaces=False
        )[0]

        hallucinated = detect_hallucinated_institution(text, self.config.known_institution_name)
        quality_score = 0.0 if hallucinated is not None else _estimate_quality(text)

        return StageResult(
            engine=ENGINE_NAME,
            stage_index=STAGE_INDEX,
            text=text,
            quality_score=quality_score,
            confidence=quality_score,
            elapsed_ms=elapsed_ms,
            boxes=(),  # Typhoon ไม่คืน bbox ระดับคำ
            cache_hit=False,
        )


def _crop_region(image: np.ndarray, region: BBox) -> np.ndarray:
    height, width = image.shape[:2]
    x0 = max(0, int(round(region.x0)))
    y0 = max(0, int(round(region.y0)))
    x1 = min(width, int(round(region.x1)))
    y1 = min(height, int(round(region.y1)))
    if x1 <= x0 or y1 <= y0:
        raise OcrEngineError(
            "region หลัง clamp ไม่มีพื้นที่เหลือให้ครอบภาพ",
            engine=ENGINE_NAME,
            region=region.as_tuple(),
            image_shape=(height, width),
        )
    return image[y0:y1, x0:x1]


def _resize_if_needed(img: Image.Image, max_size: int) -> Image.Image:
    """ย่อภาพถ้าด้านใดด้านหนึ่งเกิน `max_size` px — โมเดลถูกฝึกด้วยขนาดภาพคงที่ (model card)."""
    width, height = img.size
    if width <= max_size and height <= max_size:
        return img
    if width >= height:
        scale = max_size / float(width)
        new_size = (max_size, max(1, int(height * scale)))
    else:
        scale = max_size / float(height)
        new_size = (max(1, int(width * scale)), max_size)
    return img.resize(new_size, Image.Resampling.LANCZOS)


def _estimate_quality(text: str) -> float:
    """คะแนนคุณภาพเบื้องต้นเมื่อไม่พบ hallucination — Typhoon ไม่รายงาน confidence เอง.

    ใช้สัดส่วนอักขระที่ไม่ใช่ whitespace/replacement character เป็นสัญญาณเบื้องต้น
    (ข้อความว่างหรือมีแต่ placeholder ถือว่าคุณภาพต่ำ) — ยังไม่ใช่ metric ที่สมบูรณ์ แต่
    เพียงพอให้ Gain_Cost_Halter เปรียบเทียบ "ดีขึ้นหรือแย่ลง" ระหว่าง stage ได้
    """
    stripped = text.strip()
    if not stripped:
        return 0.0
    replacement_count = stripped.count("\ufffd")
    clean_ratio = 1.0 - (replacement_count / len(stripped))
    return max(0.0, min(1.0, clean_ratio))
