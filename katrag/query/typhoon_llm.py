"""Typhoon LLM backend — ใช้ OpenTyphoon API (OpenAI-compatible).

SCB10x Typhoon — LLM ภาษาไทยคุณภาพสูง
Endpoint: https://api.opentyphoon.ai/v1
"""

from __future__ import annotations

import os

from openai import OpenAI

from katrag.errors import AnswerGenerationError


class TyphoonLLM:
    """Typhoon API backend via OpenAI-compatible client."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "typhoon-v2.5-30b-a3b-instruct",
        base_url: str = "https://api.opentyphoon.ai/v1",
    ) -> None:
        key = api_key or os.environ.get("TYPHOON_API_KEY", "")
        if not key:
            raise AnswerGenerationError("TYPHOON_API_KEY not set")
        self._client = OpenAI(api_key=key, base_url=base_url)
        self._model = model

    def generate(self, prompt: str, max_tokens: int = 2048) -> str:
        """ส่ง prompt ไป Typhoon แล้วคืนคำตอบ."""
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": "คุณเป็นผู้ช่วยตอบคำถามเกี่ยวกับหลักสูตร KMITL ตอบเป็นภาษาไทย กระชับ ตรงประเด็น"},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=max_tokens,
                temperature=0.1,
            )
            content = response.choices[0].message.content
            return content if content else "(Typhoon returned empty)"
        except Exception as exc:
            raise AnswerGenerationError(f"Typhoon API error: {exc}") from exc
