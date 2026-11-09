"""Typed configuration for the local Ollama runtime.

The application settings remain the single source of environment truth. This
module only exposes a small immutable view so the LLM client can be constructed
and unit-tested without reaching into the global Settings object.
"""

from dataclasses import dataclass

from app.core.config import get_settings


@dataclass(frozen=True, slots=True)
class OllamaConfig:
    base_url: str
    model: str
    temperature: float
    max_output_tokens: int
    timeout_seconds: float
    context_window: int
    health_timeout_seconds: float
    model_cache_seconds: float

    @property
    def model_label(self) -> str:
        return self.model


def get_ollama_config() -> OllamaConfig:
    settings = get_settings()
    return OllamaConfig(
        base_url=settings.ollama_base_url.rstrip("/"),
        model=settings.ollama_model,
        temperature=settings.ollama_temperature,
        max_output_tokens=settings.ollama_max_output_tokens,
        timeout_seconds=settings.ollama_timeout_seconds,
        context_window=settings.ollama_context_window,
        health_timeout_seconds=settings.ollama_health_timeout_seconds,
        model_cache_seconds=settings.ollama_model_cache_seconds,
    )
