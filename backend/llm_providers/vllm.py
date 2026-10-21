"""
Провайдер vLLM.

vLLM — OpenAI-совместимый inference-сервер, но одна инстанция обслуживает
**одну** модель (задаётся аргументом ``--model`` при старте процесса).
Горячей смены модели нет. Поэтому:

- ``ensure_model_loaded`` → ``True`` только если ``model_id`` совпадает с
  тем, что реально запущено (определяется через ``/v1/models`` и/или
  ``static_model`` из конфига);
- ``health`` — ``GET /health`` (без тела) вместо ``/v1/health``;
- ``list_models`` возвращает ровно одну модель (как отвечает сам vLLM).
"""

from __future__ import annotations

from typing import List

import httpx

from backend.settings.logging import get_logger

from .base import ProviderCapabilities, ProviderHealth
from .openai_compat import OpenAICompatProvider

logger = get_logger(__name__)


class VLLMProvider(OpenAICompatProvider):
    """vLLM: OpenAI-compat, но без swap — одна модель на инстанцию."""

    HEALTH_PATH: str = "/health"
    HEALTH_FALLBACK_TO_MODELS: bool = True
    _capabilities = ProviderCapabilities(
        hot_swap=False,
        multi_loaded=False,
        native_chat_api=True,
        streaming=True,
        vision=True,
    )

    async def health(self) -> ProviderHealth:
        """vLLM /health возвращает 200 без JSON → маппим в healthy=True."""
        try:
            async with httpx.AsyncClient(timeout=self._short_timeout()) as client:
                response = await client.get(
                    f"{self.base_url}{self.HEALTH_PATH}", headers=self._headers()
                )
                if response.status_code == 200:
                    loaded = await self._loaded_model_ids(client)
                    return ProviderHealth(healthy=True, loaded_models=loaded)
                if (
                    response.status_code in (404, 405)
                    and self.HEALTH_FALLBACK_TO_MODELS
                ):
                    return await self._health_via_models(client)
                return ProviderHealth(
                    healthy=False,
                    error=f"HTTP {response.status_code}: {response.text[:200]}",
                )
        except Exception as e:
            logger.exception("Ошибка операции")
            return ProviderHealth(healthy=False, error=str(e))

    async def _loaded_model_ids(self, client: httpx.AsyncClient) -> List[str]:
        """Список моделей, реально обслуживаемых этой vLLM-инстанцией."""
        try:
            response = await client.get(
                f"{self.base_url}/v1/models", headers=self._headers()
            )
            if response.status_code != 200:
                return self._static_fallback()
            data = response.json()
            ids = [
                str(row.get("id") or "").strip()
                for row in data.get("data") or []
                if isinstance(row, dict) and row.get("id")
            ]
            ids = [i for i in ids if i]
            if not ids and self._config.static_model:
                return [self._config.static_model]
            return ids
        except Exception:
            logger.exception("Ошибка операции")
            return self._static_fallback()

    def _static_fallback(self) -> List[str]:
        s = (self._config.static_model or "").strip()
        return [s] if s else []

    async def ensure_model_loaded(self, model_id: str) -> bool:
        mid = (model_id or "").strip()
        if not mid:
            return False
        static = (self._config.static_model or "").strip()
        if static and mid == static:
            return True
        try:
            models = await self.list_models()
        except Exception:
            logger.exception("[:vLLM] list_models error: ; падаем на static_model=%r")
            return bool(static and mid == static)
        for m in models:
            if m.model_id == mid:
                return True
        logger.warning(
            "[%s:vLLM] модель %r не обслуживается этой инстанцией (static_model=%r). vLLM не умеет hot-swap — перезапустите процесс с --model %s.",
            self.id,
            mid,
            static,
            mid,
        )
        return False
