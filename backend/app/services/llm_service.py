"""Local Ollama generation client.

The service sends only the already-authorized context produced by the context
builder. It never logs prompts, context text or generated text.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from functools import lru_cache

import httpx

from app.core.exceptions import ServiceUnavailableError
from app.core.llm_config import OllamaConfig, get_ollama_config
from app.services.prompt_builder import Prompt, build_prompt

logger = logging.getLogger(__name__)


class LLMServiceError(ServiceUnavailableError):
    default_detail = "The local language model is unavailable"


class LLMTimeoutError(LLMServiceError):
    default_detail = "The local language model timed out"


class LLMModelNotFoundError(LLMServiceError):
    default_detail = "The configured local language model is not installed"


class LLMMalformedResponseError(LLMServiceError):
    default_detail = "The local language model returned an invalid response"


@dataclass(frozen=True, slots=True)
class LLMResponse:
    answer: str
    model: str


class OllamaLLMService:
    def __init__(
        self,
        config: OllamaConfig | None = None,
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.config = config or get_ollama_config()
        self._http_client = http_client
        self._model_validated = False
        self._validated_at = 0.0

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        if self._http_client is not None:
            return self._http_client.request(method, path, **kwargs)
        with httpx.Client(
            base_url=self.config.base_url,
            timeout=self.config.timeout_seconds,
        ) as client:
            return client.request(method, path, **kwargs)

    def _model_names(self) -> list[str]:
        response = self._request(
            "GET",
            "/api/tags",
            timeout=self.config.health_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        models = payload.get("models", []) if isinstance(payload, dict) else []
        names: list[str] = []
        if isinstance(models, list):
            for item in models:
                if isinstance(item, dict) and isinstance(item.get("name"), str):
                    names.append(item["name"])
        return names

    def _validate_model(self) -> None:
        now = time.monotonic()
        if (
            self._model_validated
            and now - self._validated_at < self.config.model_cache_seconds
        ):
            return

        names = self._model_names()
        configured = self.config.model
        aliases = {configured}
        if ":" not in configured:
            aliases.add(f"{configured}:latest")
        if not any(name in aliases for name in names):
            raise LLMModelNotFoundError()

        self._model_validated = True
        self._validated_at = now

    def check_health(self) -> dict[str, object]:
        """Return safe operational status; never include prompts or credentials."""
        try:
            names = self._model_names()
        except Exception:
            logger.warning(
                "Ollama health check failed model=%s", self.config.model
            )
            return {
                "status": "unavailable",
                "model": self.config.model,
                "model_available": False,
                "detail": "Ollama is not reachable",
            }

        available = self.config.model in names
        if not available and ":" not in self.config.model:
            available = f"{self.config.model}:latest" in names
        return {
            "status": "ok" if available else "model_missing",
            "model": self.config.model,
            "model_available": available,
            "detail": None if available else "Configured model is not installed",
        }

    def generate_answer(self, question: str, authorized_context: str) -> LLMResponse:
        prompt = build_prompt(question, authorized_context)
        return self.generate_messages(prompt)

    def generate_messages(self, prompt: Prompt) -> LLMResponse:
        try:
            self._validate_model()
        except LLMServiceError:
            raise
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError() from exc
        except httpx.HTTPError as exc:
            raise LLMServiceError() from exc
        except Exception as exc:
            raise LLMServiceError() from exc

        payload = {
            "model": self.config.model,
            "messages": list(prompt.messages),
            "stream": False,
            "options": {
                "temperature": self.config.temperature,
                "num_predict": self.config.max_output_tokens,
                "num_ctx": self.config.context_window,
            },
        }

        try:
            response = self._request("POST", "/api/chat", json=payload)
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError() from exc
        except httpx.HTTPError as exc:
            raise LLMServiceError() from exc

        if response.status_code != 200:
            raise LLMServiceError()

        try:
            data = response.json()
        except ValueError as exc:
            raise LLMMalformedResponseError() from exc

        if not isinstance(data, dict):
            raise LLMMalformedResponseError()
        message = data.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise LLMMalformedResponseError()

        return LLMResponse(answer=content.strip(), model=self.config.model)


@lru_cache(maxsize=1)
def get_llm_service() -> OllamaLLMService:
    return OllamaLLMService(get_ollama_config())
