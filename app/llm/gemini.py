"""Backend Gemini (chỉ để phát triển/so sánh; vòng nộp cuối phải offline)."""
from __future__ import annotations

import os
import time
import json
import re
from collections import Counter
from pathlib import Path
from typing import Sequence

from dotenv import load_dotenv

from app.config import Config
from .base import GenerationError, LLMBackend, QuotaExhausted
from .prompts import QUESTION_REWRITE_SYSTEM_PROMPT

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def _is_daily_quota(e: Exception) -> bool:
    s = str(e)
    return "RESOURCE_EXHAUSTED" in s and ("PerDay" in s or "per day" in s.lower())


def _api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GEMINI_APIKEY")
    if not key:
        load_dotenv(dotenv_path=ENV_FILE)
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GEMINI_APIKEY")
    if key:
        return key
    try:                                            # Kaggle Secrets
        from kaggle_secrets import UserSecretsClient
        return UserSecretsClient().get_secret("GEMINI_API_KEY")
    except Exception as e:
        raise RuntimeError("Thiếu khóa: đặt GEMINI_API_KEY (hoặc GEMINI_APIKEY) trong môi trường/.env, "
                           "hoặc Kaggle Secret GEMINI_API_KEY") from e


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

    def rewrite_question(self, question: str, *, protected_terms: Sequence[str] = ()) -> str:
        """Chuẩn hóa tiếng Việt qua API, không trả lời câu hỏi hoặc quy đổi thời gian.

        protected_terms: tên riêng/mã cần giữ nguyên chính tả nếu xuất hiện trong câu.
        Trả câu đã chuẩn hóa; phản hồi sai định dạng hoặc đổi số/thuật ngữ được bảo vệ
        gây GenerationError. Lỗi API/quota dùng cùng cơ chế với generate().
        """
        if not isinstance(question, str):
            raise TypeError("question phải là chuỗi")
        if not question.strip():
            raise ValueError("question không được rỗng")
        if isinstance(protected_terms, str) or any(
            not isinstance(term, str) or not term.strip() for term in protected_terms
        ):
            raise ValueError("protected_terms phải là danh sách các chuỗi không rỗng")
        terms = sorted({term for term in protected_terms if term in question})
        prompt = json.dumps({"question": question, "protected_terms": terms}, ensure_ascii=False)
        responses = self.generate(QUESTION_REWRITE_SYSTEM_PROMPT, prompt, n=1)
        try:
            result = json.loads(responses[0])
        except (IndexError, TypeError, json.JSONDecodeError) as e:
            raise GenerationError("Gemini rewrite không trả JSON hợp lệ") from e
        if (not isinstance(result, dict) or set(result) != {"question"}
                or not isinstance(result["question"], str) or not result["question"].strip()):
            raise GenerationError("Gemini rewrite phải trả một trường question không rỗng")
        rewritten = result["question"].strip()
        # Giữ nguyên chữ số, định dạng ngày/giờ, số thập phân và phần trăm.
        number_pattern = r"\d+(?:[.,:/-]\d+)*%?"
        if Counter(re.findall(number_pattern, question)) != Counter(re.findall(number_pattern, rewritten)):
            raise GenerationError("Gemini rewrite đã thay đổi số liệu hoặc mốc ngày/giờ")
        if any(question.count(term) != rewritten.count(term) for term in terms):
            raise GenerationError("Gemini rewrite đã thay đổi tên/mã cần giữ nguyên")
        return rewritten
