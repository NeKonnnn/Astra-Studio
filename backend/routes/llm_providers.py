"""
REST API для работы со списком LLM-провайдеров.

Ручка **read-only**: редактирование provider-ов и API-ключей через UI
не предусмотрено. Ключи читаются только из ENV; интерфейс показывает,
какую ENV-переменную выставить, установлена ли она, и preview её значения
(без раскрытия полного секрета).

Эндпоинты:

- ``GET /api/llm-providers`` — список всех провайдеров с их capabilities,
  health, статусом API-ключа и hint-ом по ENV-переменной.
- ``GET /api/llm-providers/{provider_id}`` — detail по одному провайдеру.
- ``GET /api/llm-providers/{provider_id}/models`` — модели конкретного
  провайдера (для UI, чтобы не тянуть объединённый /api/models).
"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from backend.utils.http_errors import public_error_detail

from backend.auth.jwt_handler import get_current_user
from backend.llm_providers import (
    default_env_name_for_provider,
    get_registry,
    join_model_path,
)
from backend.llm_providers.registry import build_registry_debug_snapshot
from backend.routes.models import _is_chat_provider
from backend.settings.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(
    prefix="/api/llm-providers",
    tags=["llm-providers"],
    dependencies=[Depends(get_current_user)],
)


def _hint_for_provider(provider_id: str, provider_kind: str) -> Dict[str, Any]:
    """
    Возвращает hint для UI: какую ENV-переменную выставить, в PowerShell и
    bash формате, требуется ли ключ вообще.
    """
    env_name = default_env_name_for_provider(provider_id)
    required = provider_kind in {"openai", "openrouter", "anthropic"}
    return {
        "env_name": env_name,
        "required": required,
        "powershell": f'$env:{env_name}="<ваш-ключ>"',
        "bash": f'export {env_name}="<ваш-ключ>"',
    }


async def _serialize_provider(provider, *, include_health: bool) -> Dict[str, Any]:
    describe = provider.describe()
    secret_hint = _hint_for_provider(provider.id, provider.kind)
    payload: Dict[str, Any] = {**describe, "secret_hint": secret_hint}
    if include_health:
        try:
            health = await provider.health()
            payload["health"] = {
                "healthy": health.healthy,
                "error": health.error,
                "loaded_models": list(health.loaded_models),
            }
        except Exception as e:
            logger.exception("Ошибка операции")
            payload["health"] = {"healthy": False, "error": public_error_detail(e), "loaded_models": []}
    return payload


@router.get("")
async def list_providers(include_health: bool = False, include_disabled: bool = False):
    """Полный список провайдеров + их health, capabilities, secret-status."""
    try:
        registry = await get_registry()
    except Exception as e:
        logger.exception("list_providers: registry error")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e
    providers_out: List[Dict[str, Any]] = []
    for provider in registry.all(include_disabled=include_disabled):
        # Шлюзы RAG-моделей (chat_visible: false) в этот список не попадают:
        if not _is_chat_provider(provider):
            continue
        providers_out.append(
            await _serialize_provider(provider, include_health=include_health)
        )
    return {"default_provider_id": registry.default_id, "providers": providers_out}


@router.get("/debug")
async def get_providers_debug_snapshot():
    """
    Техническая диагностика источников конфигурации провайдеров (без секретов).
    Нужна для расследования случаев, когда в UI виден не тот набор provider-ов.
    """
    try:
        return build_registry_debug_snapshot()
    except Exception as e:
        logger.exception("llm-providers debug error")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.get("/{provider_id}")
async def get_provider(provider_id: str, include_health: bool = False):
    """Detail по одному провайдеру."""
    try:
        registry = await get_registry()
    except Exception as e:
        logger.exception("Ошибка операции")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e
    if not registry.contains(provider_id):
        raise HTTPException(
            status_code=404, detail=f"Provider {provider_id!r} не найден"
        )
    provider = registry.get(provider_id)
    return await _serialize_provider(provider, include_health=include_health)


@router.get("/{provider_id}/models")
async def get_provider_models(provider_id: str):
    """Список моделей конкретного провайдера (для селектора в UI)."""
    try:
        registry = await get_registry()
    except Exception as e:
        logger.exception("Ошибка операции")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e
    if not registry.contains(provider_id):
        raise HTTPException(
            status_code=404, detail=f"Provider {provider_id!r} не найден"
        )
    provider = registry.get(provider_id)
    try:
        models = await provider.list_models()
    except Exception as e:
        logger.exception("list_models() error")
        raise HTTPException(status_code=502, detail=public_error_detail(e)) from e
    return {
        "provider_id": provider.id,
        "provider_kind": provider.kind,
        "models": [
            {
                "id": m.model_id,
                "path": join_model_path(provider.id, m.model_id),
                "provider_id": provider.id,
                "display_name": m.display_name or m.model_id,
                "extra": m.extra,
            }
            for m in models
        ],
    }
