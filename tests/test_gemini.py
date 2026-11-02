from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.llm import gemini


def test_create_gemini_llm_loads_dotenv_and_uses_configured_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = Mock()
    load_dotenv = Mock()
    create_client = Mock(return_value=client)
    monkeypatch.setattr(gemini, "load_dotenv", load_dotenv)
    monkeypatch.setattr(gemini.genai, "Client", create_client)
    monkeypatch.setenv("GEMINI_APIKEY", "test-api-key")

    llm = gemini.create_gemini_llm()

    load_dotenv.assert_called_once_with(dotenv_path=gemini.ENV_FILE)
    create_client.assert_called_once_with(api_key="test-api-key")
    assert llm.client is client
    assert llm.model == "gemini-3.5-flash-lite"


def test_create_gemini_llm_rejects_missing_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gemini, "load_dotenv", Mock())
    monkeypatch.delenv("GEMINI_APIKEY", raising=False)

    with pytest.raises(RuntimeError, match="GEMINI_APIKEY"):
        gemini.create_gemini_llm()


def test_generate_uses_configured_model_and_returns_text() -> None:
    client = Mock()
    client.models.generate_content.return_value = SimpleNamespace(text="hello")
    llm = gemini.GeminiLLM(client=client, model="test-model")

    result = llm.generate("say hello")

    client.models.generate_content.assert_called_once_with(
        model="test-model",
        contents="say hello",
    )
    assert result == "hello"


def test_generate_rejects_empty_prompt() -> None:
    llm = gemini.GeminiLLM(client=Mock())

    with pytest.raises(ValueError, match="Prompt must not be empty"):
        llm.generate("   ")


def test_generate_reports_empty_model_response() -> None:
    client = Mock()
    client.models.generate_content.return_value = SimpleNamespace(text=None)
    llm = gemini.GeminiLLM(client=client)

    with pytest.raises(RuntimeError, match="no text content"):
        llm.generate("prompt")
