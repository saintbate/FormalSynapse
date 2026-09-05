"""OpenAI-compatible client for vLLM (Outlines/XGrammar guided decoding on the server).

Default endpoint is the local vLLM server from ``scripts/run_vllm.sh``. Any OpenAI-compatible
host works (OpenRouter, a remote GPU box) via ``FSYN_LLM_BASE_URL`` / ``FSYN_LLM_API_KEY``.
The formal grade still comes only from ``sby``.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urljoin

from formalsynapse.sva_grammar import SVA_EBNF, SVA_REGEX, ExtractError, extract_sva


class GenerateError(RuntimeError):
    """LLM endpoint refused the request or returned unusable text."""


@dataclass(frozen=True)
class Message:
    role: str
    content: str


class Generator(Protocol):
    """Anything that turns a chat into raw model text."""

    def generate(self, messages: list[Message]) -> str: ...


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value if value else default


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


@dataclass
class VLLMGenerator:
    """Chat-completions client with optional ``guided_grammar`` (vLLM + outlines/xgrammar)."""

    base_url: str = field(default_factory=lambda: _env("FSYN_LLM_BASE_URL", "http://localhost:8000/v1"))
    model: str = field(default_factory=lambda: _env("FSYN_LLM_MODEL", "Qwen/Qwen2.5-Coder-7B-Instruct"))
    api_key: str = field(default_factory=lambda: _env("FSYN_LLM_API_KEY", "EMPTY"))
    temperature: float = 0.2
    max_tokens: int = field(default_factory=lambda: _env_int("FSYN_LLM_MAX_TOKENS", 1024))
    timeout_s: float = 180.0
    guided: bool = True
    guided_backend: str = "grammar"  # grammar | regex | off

    def generate(self, messages: list[Message]) -> str:
        url = urljoin(self.base_url.rstrip("/") + "/", "chat/completions")
        payload: dict[str, object] = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if self.guided and self.guided_backend == "grammar":
            payload["guided_grammar"] = SVA_EBNF
        elif self.guided and self.guided_backend == "regex":
            payload["guided_regex"] = SVA_REGEX
        body = json.dumps(payload).encode()
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        if "openrouter.ai" in self.base_url:
            headers["HTTP-Referer"] = "https://github.com/formalsynapse/opensva-rl"
            headers["X-Title"] = "FormalSynapse"
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers=headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                raw = resp.read().decode()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:800]
            if exc.code in {400, 422} and "context length" in detail.lower() and self.max_tokens > 512:
                smaller = VLLMGenerator(
                    base_url=self.base_url,
                    model=self.model,
                    api_key=self.api_key,
                    temperature=self.temperature,
                    max_tokens=max(256, self.max_tokens // 2),
                    timeout_s=self.timeout_s,
                    guided=self.guided,
                    guided_backend=self.guided_backend,
                )
                return smaller.generate(messages)
            if self.guided and exc.code in {400, 422}:
                return self._retry_unguided(messages, detail)
            raise GenerateError(f"LLM HTTP {exc.code} at {url}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise GenerateError(f"LLM unreachable at {self.base_url}: {exc}") from exc
        return _content_from_chat(raw)

    def _retry_unguided(self, messages: list[Message], detail: str) -> str:
        """Some servers reject guided_grammar; fall back to unconstrained decode once."""
        unguided = VLLMGenerator(
            base_url=self.base_url,
            model=self.model,
            api_key=self.api_key,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            timeout_s=self.timeout_s,
            guided=False,
            guided_backend="off",
        )
        try:
            return unguided.generate(messages)
        except GenerateError as exc:
            raise GenerateError(f"guided decode rejected ({detail[:160]}); fallback also failed: {exc}") from exc


def _content_from_chat(raw: str) -> str:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GenerateError(f"LLM returned non-JSON: {raw[:200]!r}") from exc
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise GenerateError(f"LLM JSON missing choices[0].message.content: {raw[:300]}") from exc
    if not isinstance(content, str) or not content.strip():
        raise GenerateError("LLM returned empty content")
    return content


def ping(base_url: str, timeout_s: float = 5.0, api_key: str | None = None) -> tuple[bool, str]:
    """Probe ``GET {base}/models`` (vLLM / OpenAI compatible)."""
    url = urljoin(base_url.rstrip("/") + "/", "models")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    req = urllib.request.Request(url, method="GET", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            data = json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return False, str(exc)
    models = data.get("data") if isinstance(data, dict) else None
    if isinstance(models, list) and models:
        ids = [m.get("id", "?") for m in models if isinstance(m, dict)]
        return True, ", ".join(str(i) for i in ids[:6])
    return True, "ok"


def generate_sva(generator: Generator, messages: list[Message]) -> str:
    """Generate and extract a single SVA block."""
    raw = generator.generate(messages)
    try:
        return extract_sva(raw)
    except ExtractError as exc:
        raise GenerateError(f"{exc}; raw starts {raw[:160]!r}") from exc
