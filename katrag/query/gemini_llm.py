"""Gemini LLM backend — ใช้ Google Gemini API แทน local Qwen3.

ข้อดี: คุณภาพคำตอบดีกว่ามาก, เร็ว, รองรับภาษาไทยดี
ข้อเสีย: ต้องมีอินเทอร์เน็ต + API key
"""

from __future__ import annotations

import os

import google.generativeai as genai

from katrag.errors import AnswerGenerationError
from katrag.query.answer_generator import LLMProtocol


class GeminiLLM:
    """Gemini API backend — implements LLMProtocol."""

    def __init__(self, api_key: str | None = None, model: str = "gemini-1.5-flash") -> None:
        key = api_key or os.environ.get("GEMINI_API_KEY", "")
        if not key:
            raise AnswerGenerationError("GEMINI_API_KEY not set")
        genai.configure(api_key=key)
        self._model = genai.GenerativeModel(model)

    def generate(self, prompt: str, max_tokens: int = 2048) -> str:
        """ส่ง prompt ไป Gemini แล้วคืนคำตอบ."""
        try:
            response = self._model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    max_output_tokens=max_tokens,
                    temperature=0.1,
                ),
            )
            if response.text:
                return response.text
            return "(Gemini returned empty response)"
        except Exception as exc:
            raise AnswerGenerationError(f"Gemini API error: {exc}") from exc
