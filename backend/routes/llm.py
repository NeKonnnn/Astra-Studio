"""
Статус подключения к LLM-провайдерам — для UI без знания URL на фронте.
После перехода на ProviderRegistry эндпоинт показывает агрегированный статус
ВСЕХ зарегистрированных провайдеров (llm-svc / vLLM / Ollama / OpenAI / ...).
"""

import asyncio
import os
import time

from fastapi import APIRouter, Depends

from backend.auth.jwt_handler import get_current_user
from backend.llm_providers import get_registry
from backend.settings.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(
    prefix="/api/llm", tags=["llm"], dependencies=[Depends(get_current_user)]
)
_STATUS_LAST_TS = 0.0
_STATUS_COUNT = 0
_LLM_STATUS_CACHE_SECONDS = max(1, int(os.getenv("LLM_STATUS_CACHE_SECONDS", "60")))
_llm_status_cache_payload = None
_llm_status_cache_ts = 0.0
_llm_status_cache_lock = asyncio.Lock()


def _trace_llm_status_request() -> None:
    global _STATUS_LAST_TS, _STATUS_COUNT
    now = time.monotonic()
    delta_s = (now - _STATUS_LAST_TS) if _STATUS_LAST_TS > 0 else None
    _STATUS_LAST_TS = now
    _STATUS_COUNT += 1
    logger.debug(
        "[LLM_TRAFFIC] inbound path=/api/llm/status count=%s delta_s=%s",
        _STATUS_COUNT,
        f"{delta_s:.2f}" if delta_s is not None else "first",
    )


@router.get("/status")
async def llm_connection_status():
    """
    Состояние всех настроенных LLM-провайдеров. Фронт использует только этот
    эндпоинт (и ``/api/models``), не URL конкретного сервиса.

    Формат ответа (важно для фронта):

    ::

        {
          "connected": bool,           # хотя бы один провайдер healthy
          "default_provider_id": str,  # id провайдера по умолчанию
          "providers": [
            {
              "id": str,
              "kind": "llm-svc" | "vllm" | "ollama" | ...,
              "base_url": str,
              "healthy": bool,
              "loaded_models": [str, ...],
              "capabilities": {...},
              "api_key_set": bool,
              "error": str | None,
            },
            ...
          ],

          # LEGACY-поля — для старых фронтов, выпиливаются в шаге 6:
          "use_llm_svc": bool,
          "hosts": [{"id": str, "healthy": bool, "error": str|None}, ...],
          "default_host_id": str,
          "message": str | None,
        }
    """
    global _llm_status_cache_payload, _llm_status_cache_ts
    _trace_llm_status_request()
    now = time.monotonic()
    if (
        _llm_status_cache_payload is not None
        and (now - _llm_status_cache_ts) < _LLM_STATUS_CACHE_SECONDS
    ):
        return _llm_status_cache_payload

    async with _llm_status_cache_lock:
        now = time.monotonic()
        if (
            _llm_status_cache_payload is not None
            and (now - _llm_status_cache_ts) < _LLM_STATUS_CACHE_SECONDS
        ):
            return _llm_status_cache_payload
        try:
            registry = await get_registry()
        except Exception as e:
            logger.exception("llm status: registry error")
            return {
                "connected": False,
                "default_provider_id": None,
                "providers": [],
                "use_llm_svc": False,
                "hosts": [],
                "default_host_id": None,
                "message": f"Ошибка инициализации LLM-провайдеров: {e}",
            }
        providers_out = []
        hosts_out = []
        any_healthy = False
        has_llmsvc = False
        for provider in registry.all():
            try:
                health = await provider.health()
            except Exception as e:
                logger.exception("health() error")
                health_healthy, err, loaded = (False, str(e), [])
            else:
                health_healthy = health.healthy
                err = health.error
                loaded = list(health.loaded_models)
            if health_healthy:
                any_healthy = True
            if provider.kind == "llm-svc":
                has_llmsvc = True
            providers_out.append(
                {
                    "id": provider.id,
                    "kind": provider.kind,
                    "base_url": provider.base_url,
                    "healthy": health_healthy,
                    "loaded_models": loaded,
                    "capabilities": {
                        "hot_swap": provider.capabilities.hot_swap,
                        "multi_loaded": provider.capabilities.multi_loaded,
                        "streaming": provider.capabilities.streaming,
                        "vision": provider.capabilities.vision,
                    },
                    **provider.secret_status(),
                    "error": None if health_healthy else err or "unhealthy",
                }
            )
            hosts_out.append(
                {
                    "id": provider.id,
                    "healthy": health_healthy,
                    "error": None if health_healthy else err or "unhealthy",
                }
            )
        message = None
        if not any_healthy:
            message = "Подключиться к LLM не удалось. Проверьте, что сервис(ы) моделей запущены и настройки провайдеров (backend/config/config.yml / ENV-переменные для API-ключей) указаны верно."
        response = {
            "connected": any_healthy,
            "default_provider_id": registry.default_id,
            "providers": providers_out,
            "use_llm_svc": has_llmsvc,
            "default_host_id": registry.default_id,
            "hosts": hosts_out,
            "message": message,
        }
        _llm_status_cache_payload = response
        _llm_status_cache_ts = time.monotonic()
        return response
