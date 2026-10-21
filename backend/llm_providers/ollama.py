"""
Провайдер Ollama.

Ollama имеет собственный нативный API (``/api/chat``), но также
OpenAI-совместимый endpoint под ``/v1/chat/completions``. Работаем через
OpenAI-compat, т.к. это позволяет разделять код со всеми остальными
провайдерами; нативный ``/api/tags`` используем только для ``list_models``
(он точнее и включает размер модели, модификацию и т.д.).

Ollama может держать несколько моделей в RAM одновременно (до лимита
``num_parallel``) и загружать модель по первому запросу автоматически.
Поэтому ``ensure_model_loaded`` просто проверяет наличие модели в
``/api/tags`` — никаких ручных swap-запросов не требуется.
"""

from __future__ import annotations

from typing import List

import httpx

from backend.settings.logging import get_logger

from .base import ModelInfo, ProviderCapabilities, ProviderHealth
from .openai_compat import OpenAICompatProvider

logger = get_logger(__name__)


class OllamaProvider(OpenAICompatProvider):
    """Ollama через OpenAI-compat endpoint + /api/tags для list_models."""

    HEALTH_PATH: str = "/api/version"
    HEALTH_FALLBACK_TO_MODELS: bool = True
    _capabilities = ProviderCapabilities(
        hot_swap=False,
        multi_loaded=True,
        native_chat_api=True,
        streaming=True,
        vision=True,
    )

    async def health(self) -> ProviderHealth:
        """``GET /api/version`` → ``{"version": "..."}``."""
        try:
            async with httpx.AsyncClient(timeout=self._short_timeout()) as client:
                response = await client.get(
                    f"{self.base_url}{self.HEALTH_PATH}", headers=self._headers()
                )
                if response.status_code == 200:
                    payload = self._decode_health_json(
                        response, url=f"{self.base_url}{self.HEALTH_PATH}"
                    )
                    if payload is None:
                        if self.HEALTH_FALLBACK_TO_MODELS:
                            return await self._health_via_models(client)
                        return ProviderHealth(
                            healthy=False,
                            error=f"health returned non-JSON: {(response.text or '')[:200]}",
                        )
                    return ProviderHealth(healthy=True, raw=payload)
                if (
                    response.status_code in (404, 405)
                    and self.HEALTH_FALLBACK_TO_MODELS
                ):
                    return await self._health_via_models(client)
                return ProviderHealth(
                    healthy=False,
                    error=f"HTTP {response.status_code}: {response.text[:200]}",
                )
        except (httpx.HTTPError, OSError) as e:
            logger.warning("[%s] health check failed: %s", self.id, e)
            return ProviderHealth(healthy=False, error=str(e))

    async def list_models(self) -> List[ModelInfo]:
        """``GET /api/tags`` — нативный список моделей Ollama."""
        try:
            async with httpx.AsyncClient(timeout=self._short_timeout()) as client:
                response = await client.get(
                    f"{self.base_url}/api/tags", headers=self._headers()
                )
                if response.status_code != 200:
                    logger.warning(
                        "[%s:ollama] /api/tags HTTP %s, fallback на OpenAI /v1/models",
                        self.id,
                        response.status_code,
                    )
                    return await super().list_models()
                data = response.json() or {}
            items: List[ModelInfo] = []
            for row in data.get("models", []) or []:
                if not isinstance(row, dict):
                    continue
                name = str(row.get("name") or row.get("model") or "").strip()
                if not name:
                    continue
                details = row.get("details") or {}
                items.append(
                    ModelInfo(
                        provider_id=self.id,
                        model_id=name,
                        display_name=name,
                        context_size=None,
                        extra={
                            "size": row.get("size"),
                            "modified_at": row.get("modified_at"),
                            "quantization": details.get("quantization_level"),
                            "family": details.get("family"),
                            "parameter_size": details.get("parameter_size"),
                        },
                    )
                )
            return items
        except Exception:
            logger.exception("[:ollama] list_models error")
            return await super().list_models()

    async def ensure_model_loaded(self, model_id: str) -> bool:
        """
        Ollama автоматически подгружает модель при первом запросе. Достаточно
        убедиться, что модель установлена (``/api/tags``).
        """
        mid = (model_id or "").strip()
        if not mid:
            return False
        try:
            models = await self.list_models()
        except Exception:
            logger.exception("[:ollama] list_models error")
            return False
        for m in models:
            if m.model_id == mid:
                return True
        logger.warning(
            "[%s:ollama] модель %r не установлена. Выполните `ollama pull %s` на сервере.",
            self.id,
            mid,
            mid,
        )
        return False
