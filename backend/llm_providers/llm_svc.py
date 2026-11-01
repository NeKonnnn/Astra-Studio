"""
Провайдер для нашего кастомного llm-svc.

Отличия от базового OpenAI-совместимого провайдера:

- умеет горячо переключать модель (``POST /v1/models/load``);
- поддерживает очистку пула (``POST /v1/models/unload-excess``);
- имеет health с полями ``loaded_models``/``model_loaded``/``model_name``;
- для multi-LLM не требует глобальных локов: hot_swap сериализуется
  внутренним ``asyncio.Lock`` одного инстанса.
"""

from __future__ import annotations

import asyncio
from typing import List, Optional

import httpx

from backend.settings.logging import get_logger

from .base import LLMProviderConfig, ModelInfo, ProviderCapabilities
from .model_health import model_health_filter_enabled
from .openai_compat import OpenAICompatProvider

logger = get_logger(__name__)


def same_llm_svc_model_id(loaded: Optional[str], requested: Optional[str]) -> bool:
    """
    В ``/v1/health`` llm-svc часто кладёт короткое имя из конфига, а мы
    отправляем полное имя файла без ``.gguf``. Считаем их «той же моделью»,
    если одно — префикс другого до дефиса.
    """
    if not loaded or not requested:
        return False
    a, b = (loaded.strip().lower(), requested.strip().lower())
    if a == b:
        return True
    if a.startswith(b + "-") or b.startswith(a + "-"):
        return True
    return False


def pool_contains_model(loaded_models: List[str], model_id: Optional[str]) -> bool:
    """True, если ``model_id`` уже в RAM-пуле этого llm-svc инстанса."""
    if not model_id or not str(model_id).strip():
        return False
    mid = str(model_id).strip()
    for lid in loaded_models:
        if lid and same_llm_svc_model_id(str(lid), mid):
            return True
    return False


class LlmSvcProvider(OpenAICompatProvider):
    """llm-svc — наш кастомный OpenAI-compat сервер с пулом и swap-API."""

    HEALTH_PATH: str = "/v1/health"
    HEALTH_FALLBACK_TO_MODELS: bool = False
    _capabilities = ProviderCapabilities(
        hot_swap=True,
        multi_loaded=True,
        native_chat_api=True,
        streaming=True,
        vision=True,
    )

    def __init__(self, config: LLMProviderConfig) -> None:
        super().__init__(config)
        self._switch_lock: asyncio.Lock = asyncio.Lock()

    async def _load_timeout(self) -> httpx.Timeout:
        return httpx.Timeout(1200.0, connect=10.0, read=1200.0, write=30.0)

    async def _post_load_model(self, model_name: str) -> bool:
        """POST /v1/models/load — реально переключает веса."""
        try:
            async with httpx.AsyncClient(timeout=await self._load_timeout()) as client:
                response = await client.post(
                    f"{self.base_url}/v1/models/load",
                    headers=self._headers(),
                    json={"model": model_name},
                )
                if not response.is_success:
                    logger.error(
                        "[%s] /v1/models/load failed: %s %s",
                        self.id,
                        response.status_code,
                        response.text[:500],
                    )
                    return False
                data = response.json()
                if data.get("success"):
                    logger.info("[%s] загружена модель: %r", self.id, model_name)
                    return True
                logger.warning(
                    "[%s] load_model returned success=False: %s", self.id, data
                )
                return False
        except Exception:
            logger.exception("[] /v1/models/load error")
            return False

    async def ensure_model_loaded(self, model_id: str) -> bool:
        """
        Если модель уже в пуле — no-op. Иначе POST /v1/models/load под
        ``_switch_lock``, чтобы параллельные multi-LLM слоты не ломали
        состояние друг другу.
        """
        mid = (model_id or "").strip()
        if not mid:
            return False
        health = await self.health()
        if not health.healthy:
            logger.warning(
                "[%s] ensure_model_loaded: провайдер нездоров (%s)",
                self.id,
                health.error,
            )
        if pool_contains_model(health.loaded_models, mid):
            return True
        async with self._switch_lock:
            health2 = await self.health()
            if pool_contains_model(health2.loaded_models, mid):
                return True
            logger.info(
                "[%s] swap → %r (было: %s)", self.id, mid, health2.loaded_models
            )
            return await self._post_load_model(mid)

    async def _filter_models_by_health(
        self, models: List[ModelInfo]
    ) -> List[ModelInfo]:
        """Каталог UI = все GGUF с диска (/v1/models), не только RAM-пул.

        Раньше оставляли только ``loaded_models`` из /v1/health — в селекторе
        оставались 1–2 модели, хотя hot_swap/ensure_model_loaded как раз
        подгружают выбранную. Проверяем лишь живость провайдера.
        """
        if not models or not model_health_filter_enabled(self._config):
            return models
        try:
            health = await self.health()
            if not health.healthy:
                logger.warning(
                    "[%s] llm-svc unhealthy (%s) — список без фильтра",
                    self.id,
                    health.error,
                )
            return models
        except Exception:
            logger.exception("[%s] llm-svc health filter failed", self.id)
            return models

    async def unload_excess(self) -> bool:
        """Оставить в llm-svc только default-модель из конфига сервиса."""
        t = httpx.Timeout(1200.0, connect=10.0, read=1200.0, write=60.0)
        try:
            async with httpx.AsyncClient(timeout=t) as client:
                response = await client.post(
                    f"{self.base_url}/v1/models/unload-excess", headers=self._headers()
                )
                if not response.is_success:
                    logger.error(
                        "[%s] /v1/models/unload-excess failed: %s %s",
                        self.id,
                        response.status_code,
                        response.text[:500],
                    )
                    return False
                data = response.json()
                return bool(data.get("success", True))
        except Exception:
            logger.exception("[] /v1/models/unload-excess error")
            return False
