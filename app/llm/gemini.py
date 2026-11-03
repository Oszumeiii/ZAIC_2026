"""Backend Gemini (chỉ để phát triển/so sánh; vòng nộp cuối phải offline)."""
from __future__ import annotations

import os
import time

from app.config import Config
from .base import GenerationError, LLMBackend, QuotaExhausted


def _is_daily_quota(e: Exception) -> bool:
    s = str(e)
    return "RESOURCE_EXHAUSTED" in s and ("PerDay" in s or "per day" in s.lower())


def _api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY")
    if key:
        return key
    try:                                            # Kaggle Secrets
        from kaggle_secrets import UserSecretsClient
        return UserSecretsClient().get_secret("GEMINI_API_KEY")
    except Exception as e:
        raise RuntimeError("Thiếu khóa: đặt biến môi trường GEMINI_API_KEY hoặc Kaggle Secret GEMINI_API_KEY") from e


class GeminiBackend(LLMBackend):
    supports_batch = False                          # gọi lần lượt để còn dừng sớm khi đã đồng thuận (tiết kiệm quota)

    def __init__(self, cfg: Config, api_key: str | None = None):
        from google import genai
        from google.genai import types
        self._types = types
        self._model, self._thinking = cfg.gemini_model, cfg.thinking
        self._client = genai.Client(api_key=api_key or _api_key())

    def tag(self) -> str:
        return f"{self._model}|{self._thinking}"

    def _one(self, system: str, prompt: str) -> str:
        last = None
        for i in range(4):                          # backoff khi bị rate limit / lỗi mạng
            try:
                r = self._client.models.generate_content(
                    model=self._model, contents=prompt,
                    config=self._types.GenerateContentConfig(
                        system_instruction=system,
                        thinking_config=self._types.ThinkingConfig(thinking_level=self._thinking)))
                return r.text or ""
            except Exception as e:
                if _is_daily_quota(e):
                    raise QuotaExhausted("Hết quota request/ngày của Gemini. Đổi model, bật billing, hoặc chờ reset. "
                                         + str(e)[:200]) from e
                last = e
                time.sleep(2 * 2 ** i)
        raise GenerationError(f"Gemini lỗi sau 4 lần thử: {last!r}"[:300])

    def generate(self, system: str, prompt: str, n: int = 1) -> list[str]:
        return [self._one(system, prompt) for _ in range(n)]
