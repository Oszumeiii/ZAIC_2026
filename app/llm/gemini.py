import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from google import genai

GEMINI_MODEL = "gemini-3.5-flash-lite"
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


@dataclass(frozen=True, slots=True)
class GeminiLLM:
    client: genai.Client
    model: str = GEMINI_MODEL

    def generate(self, prompt: str) -> str:
        if not prompt.strip():
            raise ValueError("Prompt must not be empty.")

        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
        )
        if response.text is None:
            raise RuntimeError("Gemini returned no text content.")
        return response.text


def create_gemini_llm() -> GeminiLLM:
    load_dotenv(dotenv_path=ENV_FILE)
    api_key = os.getenv("GEMINI_APIKEY")
    if not api_key:
        raise RuntimeError(
            f"GEMINI_APIKEY is missing or empty. Set it in {ENV_FILE}."
        )

    return GeminiLLM(client=genai.Client(api_key=api_key))
