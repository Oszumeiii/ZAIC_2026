from dataclasses import dataclass, field
from pathlib import Path
import os
import time

from dotenv import load_dotenv
from google import genai
from google.genai import types

GEMINI_MODEL = "gemini-3.5-flash-lite"
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"



# Init Gemini client with API key from environment variable
@dataclass(frozen=True, slots=True)
class GeminiLLM:
    client: genai.Client
    model: str = GEMINI_MODEL
    min_request_interval_seconds: float = 0.0
    _last_request_at: float = field(default=0.0, init=False, repr=False, compare=False)

    def generate(
        self,
        prompt: str,
        *,
        system_instruction: str | None = None,
        response_json_schema: dict[str, object] | None = None,
    ) -> str:
        if not prompt.strip():
            raise ValueError("Prompt must not be empty.")
        if self.min_request_interval_seconds < 0:
            raise ValueError("Request interval must not be negative.")

        now = time.monotonic()
        wait_seconds = self.min_request_interval_seconds - (
            now - self._last_request_at
        )
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        object.__setattr__(self, "_last_request_at", time.monotonic())

        config: types.GenerateContentConfig | None = None
        if system_instruction is not None or response_json_schema is not None:
            config = types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type=(
                    "application/json" if response_json_schema is not None else None
                ),
                response_json_schema=response_json_schema,
                temperature=0,
            )

        if config is None:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
            )
        else:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=config,
            )
        if response.text is None:
            raise RuntimeError("Gemini returned no text content.")
        return response.text

    @classmethod
    def from_env(
        cls, *, min_request_interval_seconds: float = 0.0
    ) -> "GeminiLLM":
        load_dotenv(dotenv_path=ENV_FILE)
        api_key = os.getenv("GEMINI_APIKEY")
        if not api_key:
            raise RuntimeError(
                f"GEMINI_APIKEY is missing or empty. Set it in {ENV_FILE}."
            )

        return cls(
            client=genai.Client(api_key=api_key),
            min_request_interval_seconds=min_request_interval_seconds,
        )
