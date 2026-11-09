"""Optional real local Ollama integration test.

The normal suite mocks Ollama so it stays fast, deterministic and independent of
local model downloads. Enable this test explicitly after installing Ollama and
pulling the configured model:

    ollama pull qwen2.5:3b
    $env:OLLAMA_INTEGRATION = "1"
    pytest -q app/tests/test_ollama_integration.py

If Ollama is reachable but the configured model is missing, the test is skipped
with an actionable message instead of failing the whole suite.
"""

from __future__ import annotations

import os

import pytest

from app.services.llm_service import OllamaLLMService


@pytest.mark.skipif(
    os.getenv("OLLAMA_INTEGRATION") != "1",
    reason="Set OLLAMA_INTEGRATION=1 after installing Ollama and pulling the model",
)
def test_real_local_ollama_generates_answer_from_supplied_context():
    service = OllamaLLMService()
    health = service.check_health()
    if not health.get("model_available"):
        pytest.skip(
            "Ollama is not running or the configured model is not installed: "
            f"{health.get('detail')}"
        )

    result = service.generate_answer(
        "According to the source, how many annual leave days are provided?",
        (
            "SOURCE S1\n"
            "Document: Integration Policy\n"
            "Document ID: 00000000-0000-0000-0000-000000000001\n"
            "Chunk ID: 00000000-0000-0000-0000-000000000002\n"
            "Page: 1\n"
            "Content:\n"
            "Employees receive 18 days of annual leave each year.\n"
        ),
    )

    assert result.answer.strip()
    assert result.model
    assert "S1" in result.answer
