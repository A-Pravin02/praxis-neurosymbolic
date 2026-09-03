"""Network LLM providers: OpenAI-compatible, Anthropic, and Ollama.

These receive prompts that app/neuro/prompts.py has already rendered, so the
provider layer stays a thin transport. Failures raise ProviderError; the
orchestrator degrades to the deterministic provider rather than inventing text.
"""
from __future__ import annotations

import json
from collections.abc import Iterator

import httpx

from app.config import settings
from app.core.errors import ProviderError
from app.neuro.base import BaseProvider, Completion, CompletionRequest, Role, Task

_TASK_MAX_TOKENS = {
    Task.INTENT: 400,
    Task.TITLE: 40,
    Task.CLAIM_SPLIT: 800,
}


def _limits(request: CompletionRequest) -> tuple[float, int]:
    temperature = (
        request.temperature if request.temperature is not None else settings.llm_temperature
    )
    if request.task in (Task.INTENT, Task.CLAIM_SPLIT):
        temperature = 0.0
    max_tokens = request.max_tokens or _TASK_MAX_TOKENS.get(
        request.task, settings.llm_max_tokens
    )
    return temperature, max_tokens


class OpenAICompatibleProvider(BaseProvider):
    """Works with the OpenAI API and any server exposing /v1/chat/completions."""

    name = "openai"

    def __init__(self, model: str | None = None, api_key: str | None = None,
                 base_url: str | None = None) -> None:
        self.model = model or settings.llm_model
        self._api_key = api_key if api_key is not None else settings.llm_api_key
        self._base_url = (base_url or settings.llm_base_url or "https://api.openai.com/v1").rstrip("/")

    def _payload(self, request: CompletionRequest, stream: bool) -> dict:
        temperature, max_tokens = _limits(request)
        messages = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages += [{"role": m.role.value, "content": m.content} for m in request.messages]
        payload = {
            "model": self.model, "messages": messages,
            "temperature": temperature, "max_tokens": max_tokens, "stream": stream,
        }
        if request.task is Task.INTENT:
            payload["response_format"] = {"type": "json_object"}
        return payload

    def complete(self, request: CompletionRequest) -> Completion:
        try:
            with httpx.Client(timeout=settings.llm_timeout_seconds) as client:
                response = client.post(
                    f"{self._base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=self._payload(request, stream=False),
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise ProviderError("The language model provider is unavailable.") from exc
        try:
            text = data["choices"][0]["message"]["content"] or ""
            usage = data.get("usage", {})
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("Malformed provider response.") from exc
        return Completion(
            text=text, model=data.get("model", self.model), provider=self.name,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
        )

    def stream(self, request: CompletionRequest) -> Iterator[str]:
        try:
            with httpx.Client(timeout=settings.llm_timeout_seconds) as client:
                with client.stream(
                    "POST", f"{self._base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=self._payload(request, stream=True),
                ) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        chunk = line[5:].strip()
                        if chunk == "[DONE]":
                            return
                        try:
                            delta = json.loads(chunk)["choices"][0]["delta"].get("content")
                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue
                        if delta:
                            yield delta
        except httpx.HTTPError as exc:
            raise ProviderError("The language model provider is unavailable.") from exc


class AnthropicProvider(BaseProvider):
    name = "anthropic"

    def __init__(self, model: str | None = None, api_key: str | None = None,
                 base_url: str | None = None) -> None:
        self.model = model or settings.llm_model
        self._api_key = api_key if api_key is not None else settings.llm_api_key
        self._base_url = (base_url or settings.llm_base_url or "https://api.anthropic.com").rstrip("/")

    def _payload(self, request: CompletionRequest, stream: bool) -> dict:
        temperature, max_tokens = _limits(request)
        messages = [
            {"role": ("assistant" if m.role is Role.ASSISTANT else "user"),
             "content": m.content}
            for m in request.messages if m.role is not Role.SYSTEM
        ]
        payload = {
            "model": self.model, "messages": messages or [{"role": "user", "content": "."}],
            "max_tokens": max_tokens, "temperature": temperature, "stream": stream,
        }
        if request.system:
            payload["system"] = request.system
        return payload

    def _headers(self) -> dict:
        return {
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    def complete(self, request: CompletionRequest) -> Completion:
        try:
            with httpx.Client(timeout=settings.llm_timeout_seconds) as client:
                response = client.post(f"{self._base_url}/v1/messages",
                                       headers=self._headers(),
                                       json=self._payload(request, stream=False))
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise ProviderError("The language model provider is unavailable.") from exc
        try:
            text = "".join(
                block.get("text", "") for block in data.get("content", [])
                if block.get("type") == "text"
            )
            usage = data.get("usage", {})
        except (AttributeError, TypeError) as exc:
            raise ProviderError("Malformed provider response.") from exc
        return Completion(
            text=text, model=data.get("model", self.model), provider=self.name,
            prompt_tokens=usage.get("input_tokens", 0),
            completion_tokens=usage.get("output_tokens", 0),
        )

    def stream(self, request: CompletionRequest) -> Iterator[str]:
        try:
            with httpx.Client(timeout=settings.llm_timeout_seconds) as client:
                with client.stream("POST", f"{self._base_url}/v1/messages",
                                   headers=self._headers(),
                                   json=self._payload(request, stream=True)) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        try:
                            event = json.loads(line[5:].strip())
                        except json.JSONDecodeError:
                            continue
                        if event.get("type") == "content_block_delta":
                            piece = event.get("delta", {}).get("text")
                            if piece:
                                yield piece
        except httpx.HTTPError as exc:
            raise ProviderError("The language model provider is unavailable.") from exc


class OllamaProvider(BaseProvider):
    """Local models served by Ollama."""

    name = "ollama"

    def __init__(self, model: str | None = None, base_url: str | None = None) -> None:
        self.model = model or settings.llm_model
        self._base_url = (base_url or settings.llm_base_url or "http://localhost:11434").rstrip("/")

    def _payload(self, request: CompletionRequest, stream: bool) -> dict:
        temperature, max_tokens = _limits(request)
        messages = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages += [{"role": m.role.value, "content": m.content} for m in request.messages]
        payload = {
            "model": self.model, "messages": messages, "stream": stream,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        if request.task is Task.INTENT:
            payload["format"] = "json"
        return payload

    def complete(self, request: CompletionRequest) -> Completion:
        try:
            with httpx.Client(timeout=settings.llm_timeout_seconds) as client:
                response = client.post(f"{self._base_url}/api/chat",
                                       json=self._payload(request, stream=False))
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise ProviderError("The local model server is unavailable.") from exc
        return Completion(
            text=data.get("message", {}).get("content", ""),
            model=data.get("model", self.model), provider=self.name,
            prompt_tokens=data.get("prompt_eval_count", 0),
            completion_tokens=data.get("eval_count", 0),
        )

    def stream(self, request: CompletionRequest) -> Iterator[str]:
        try:
            with httpx.Client(timeout=settings.llm_timeout_seconds) as client:
                with client.stream("POST", f"{self._base_url}/api/chat",
                                   json=self._payload(request, stream=True)) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if not line.strip():
                            continue
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        piece = event.get("message", {}).get("content")
                        if piece:
                            yield piece
                        if event.get("done"):
                            return
        except httpx.HTTPError as exc:
            raise ProviderError("The local model server is unavailable.") from exc
