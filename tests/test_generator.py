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


def test_thinking_off_by_default_and_sent_as_template_kwarg(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.request

    monkeypatch.delenv("FSYN_LLM_THINKING", raising=False)
    seen: list[dict[str, object]] = []

    class _Resp:
        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *a: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps({"choices": [{"message": {"content": "`ifdef FORMAL\n`endif"}}]}).encode()

    def fake_urlopen(req: urllib.request.Request, timeout: float = 0) -> _Resp:
        seen.append(json.loads(req.data))  # type: ignore[arg-type]
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    gen = VLLMGenerator(base_url="http://x/v1")
    assert gen.thinking is False
    gen.generate([Message("user", "hi")])
    assert seen[0]["chat_template_kwargs"] == {"enable_thinking": False}
    monkeypatch.setenv("FSYN_LLM_THINKING", "1")
    VLLMGenerator(base_url="http://x/v1").generate([Message("user", "hi")])
    assert "chat_template_kwargs" not in seen[1]


def test_generate_sva_n_runs_samples_concurrently_in_order() -> None:
    import threading
    import time

    from formalsynapse.generator import generate_sva_n

    lock = threading.Lock()
    active = {"now": 0, "peak": 0}

    class Slow:
        def generate(self, messages: list[Message]) -> str:
            with lock:
                active["now"] += 1
                active["peak"] = max(active["peak"], active["now"])
            time.sleep(0.05)
            with lock:
                active["now"] -= 1
            return "`ifdef FORMAL\na_x: assert property (@(posedge clk) a |-> b);\n`endif"

    blocks = generate_sva_n(Slow(), [Message("user", "hi")], 4)
    assert len(blocks) == 4
    assert active["peak"] > 1


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
