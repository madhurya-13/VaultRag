"""Ollama client behavior with mocked HTTP and no real model requirement."""

import httpx
import pytest

from app.core.llm_config import OllamaConfig
from app.services.llm_service import (
    LLMMalformedResponseError,
    LLMModelNotFoundError,
    LLMTimeoutError,
    OllamaLLMService,
)


def _config(model: str = "qwen2.5:3b") -> OllamaConfig:
    return OllamaConfig(
        base_url="http://ollama.test",
        model=model,
        temperature=0.1,
        max_output_tokens=128,
        timeout_seconds=5,
        context_window=2048,
        health_timeout_seconds=1,
        model_cache_seconds=0,
    )


def _service(handler, model: str = "qwen2.5:3b") -> OllamaLLMService:
    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://ollama.test",
    )
    return OllamaLLMService(_config(model), http_client=client)


def test_generate_answer_sends_separate_system_and_user_messages():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen2.5:3b"}]})
        captured["payload"] = request.read().decode()
        return httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": "Answer [S1]."}},
        )

    service = _service(handler)
    result = service.generate_answer("leave policy?", "SOURCE S1\nContent:\n18 days")

    assert result.answer == "Answer [S1]."
    assert result.model == "qwen2.5:3b"
    assert '"role":"system"' in captured["payload"]
    assert '"role":"user"' in captured["payload"]
    assert "SOURCE S1" in captured["payload"]


def test_missing_model_is_a_controlled_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "llama3.2:3b"}]})

    service = _service(handler)

    with pytest.raises(LLMModelNotFoundError):
        service.generate_answer("question", "context")


def test_timeout_is_a_controlled_error():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen2.5:3b"}]})
        raise httpx.ReadTimeout("timed out", request=request)

    service = _service(handler)

    with pytest.raises(LLMTimeoutError):
        service.generate_answer("question", "context")


def test_malformed_response_is_rejected():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen2.5:3b"}]})
        return httpx.Response(200, json={"message": {}})

    service = _service(handler)

    with pytest.raises(LLMMalformedResponseError):
        service.generate_answer("question", "context")


def test_health_reports_configured_model_availability():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "qwen2.5:3b"}]})

    health = _service(handler).check_health()
    assert health["status"] == "ok"
    assert health["model_available"] is True
    assert health["model"] == "qwen2.5:3b"
