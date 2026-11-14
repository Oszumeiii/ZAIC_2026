import json
from unittest.mock import Mock

import pytest

from app.config import Config
from app.llm.base import GenerationError
from app.llm.local_service import LocalLLMService


def make_service(response):
    backend = Mock()
    backend.prompt_tokens.return_value = 1
    backend.generate_with_options.return_value = [response]
    config = Config(max_model_len=4096, max_new_tokens=128)
    return LocalLLMService(backend, config), backend


def test_local_rewrite_preserves_protected_terms_and_numbers():
    original = "sl SKU-001 cua ABC ngay 04/05/2026 la bn?"
    expected = "Số lượng SKU-001 của ABC ngày 04/05/2026 là bao nhiêu?"
    service, backend = make_service(json.dumps({"question": expected}, ensure_ascii=False))

    assert service.rewrite_question(original, protected_terms=["SKU-001", "ABC"]) == expected

    prompt = json.loads(backend.generate_with_options.call_args.args[1])
    assert prompt == {
        "question": original,
        "protected_terms": ["ABC", "SKU-001"],
    }


@pytest.mark.parametrize(
    ("original", "rewritten"),
    [
        ("ngay 04/05/2026", "Ngày 05/04/2026"),
        ("gia 2tr", "Giá 2000000"),
        ("giam 10%", "Giảm 10"),
    ],
)
def test_local_rewrite_rejects_changed_numeric_literals(original, rewritten):
    service, _ = make_service(json.dumps({"question": rewritten}, ensure_ascii=False))

    with pytest.raises(GenerationError, match="numbers/dates"):
        service.rewrite_question(original)


def test_local_rewrite_propagates_generation_errors():
    service, backend = make_service("unused")
    backend.generate_with_options.side_effect = GenerationError("model unavailable")

    with pytest.raises(GenerationError, match="model unavailable"):
        service.rewrite_question("hnay doanh thu bn?")


def test_local_rewrite_rejects_empty_input_without_generating():
    service, backend = make_service("unused")

    with pytest.raises(ValueError):
        service.rewrite_question("  ")

    backend.generate_with_options.assert_not_called()


@pytest.mark.parametrize("protected_terms", ["SKU-1", [""], [None]])
def test_local_rewrite_rejects_invalid_protected_terms_without_generating(protected_terms):
    service, backend = make_service("unused")

    with pytest.raises(ValueError):
        service.rewrite_question("Doanh thu SKU-1?", protected_terms=protected_terms)

    backend.generate_with_options.assert_not_called()
