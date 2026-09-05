from __future__ import annotations

import json
from urllib.error import URLError

import pytest

from formalsynapse.generator import GenerateError, Message, VLLMGenerator, _content_from_chat, ping


def test_content_from_chat() -> None:
    raw = json.dumps({"choices": [{"message": {"content": "property p; endproperty"}}]})
    assert "property p" in _content_from_chat(raw)


def test_ping_down() -> None:
    ok, detail = ping("http://127.0.0.1:9", timeout_s=0.2)
    assert ok is False
    assert detail


def test_default_model_is_codev_sva(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FSYN_LLM_MODEL", raising=False)
    monkeypatch.delenv("FSYN_LLM_BASE_URL", raising=False)
    gen = VLLMGenerator()
    assert "CodeV-SVA-14B" in gen.model
    assert "8000" in gen.base_url
    assert gen.guided is False
    assert isinstance(Message("user", "hi").content, str)


def test_generate_unreachable() -> None:
    gen = VLLMGenerator(base_url="http://127.0.0.1:9", timeout_s=0.2, guided=False)
    try:
        gen.generate([Message("user", "hi")])
    except GenerateError as exc:
        assert "unreachable" in str(exc).lower()
    except URLError:
        pass
    else:
        raise AssertionError("expected GenerateError")
