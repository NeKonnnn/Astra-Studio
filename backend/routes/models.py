"""
routes/models.py - управление моделями
"""

import asyncio
import os
import time
from typing import Annotated, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from backend.utils.http_errors import public_error_detail

from backend.app_state import (
    get_model_info,
    load_app_settings,
    model_settings,
    save_app_settings,
)
from backend.auth.jwt_handler import get_current_user
from backend.schemas import ModelLoadRequest, ModelLoadResponse, ModelSettings
from backend.services.user_llm_settings import (
    get_user_model_settings,
    get_user_selected_model_path,
    reset_user_model_settings,
    save_user_model_settings,
    save_user_selected_model_path,
)
from backend.settings import get_settings
from backend.settings.logging import get_logger
from backend.settings.logging.repeat import log_on_change
from backend.settings.logging.errors import logged_suppress

logger = get_logger(__name__)

router = APIRouter(prefix="/api/models", tags=["models"])

_models_available_cache_payload: Optional[dict] = None
_models_available_cache_ts: float = 0.0
_models_available_cache_lock = asyncio.Lock()


def _models_available_cache_seconds() -> int:
    return get_settings().model_health.models_available_cache_seconds


def _avatar_url_for_local_model(model_id: str) -> Optional[str]:
    """Читает sidecar аватара рядом с GGUF в models/llm (если модель качали через UI)."""
    try:
        from pathlib import Path

        from backend.services.model_downloads.avatar import read_avatar_url_near
        from backend.services.model_downloads.paths import model_roots

        llm = model_roots().get("llm")
        if not llm:
            return None
        name = Path(str(model_id or "").replace("\\", "/")).name
        if not name:
            return None
        candidates = []
        if name.lower().endswith(".gguf"):
            candidates.append(llm / name)
        else:
            candidates.append(llm / f"{name}.gguf")
            candidates.append(llm / name)
        for path in candidates:
            if path.is_file():
                url = read_avatar_url_near(path)
                if url:
                    return url
    except Exception:
        return None
    return None


def _is_chat_model(model_id: str) -> bool:
    """Модель годится для диалога — то есть не эмбеддер и не реранкер.

    Шлюз отдаёт свой каталог одним списком: у bifrost в ```/v1/models``` рядом
    с ```vllm/*``` лежат ```embed/FRIDA``` и ```rerank/*```. Провайдер чата
    (CORSUR, PHOENIX) получает их все, и без этого фильтра эмбеддер попадает
    в выбор модели для диалога.

    Тип определяем по тем же подсказкам имён, что и RAG-селектор, — там они
    расширяются переменными ```RAG_EMBEDDING_NAME_HINTS``` /
    ```RAG_RERANKER_NAME_HINTS```, и держать два списка нельзя.
    """
    try:
        from backend.routes.rag import _rag_model_kind
    except Exception:
        logger.exception("[MODELS] не удалось получить классификатор RAG-моделей")
        return True
    return _rag_model_kind(model_id, rag_only=False) is None


def _is_chat_provider(provider) -> bool:
    """Показывать ли провайдера в выборе модели чата.

    Решает ТОЛЬКО конфиг: ```extra.chat_visible: false``` у шлюза RAG-моделей.
    Никаких списков исключений в коде и никаких соглашений по именам id —
    состав ведёт эксплуатация там же, где заводит сам провайдер. Механика чтения
    та же, что у ```health_path``` и ```function_calling```.

    Провайдер при этом остаётся зарегистрированным: RAG-селектор опрашивает его
    как обычно, скрывается он только из списка моделей для диалога.
    """
    with logged_suppress(logger):
        extra = getattr(getattr(provider, "config", None), "extra", None) or {}
        if "chat_visible" in extra:
            return bool(extra.get("chat_visible"))
    return True


def _uid(current_user: dict) -> str:
    return str(
        current_user.get("user_id") or current_user.get("username") or ""
    ).strip()


def _model_display_name(path: str) -> str:
    raw = (path or "").strip().replace("\\", "/")
    if not raw:
        return "Unknown"
    tail = raw.split("/")[-1]
    if tail.lower().endswith(".gguf"):
        tail = tail[:-5]
    return tail or raw


@router.get("/current")
async def get_current_model(
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Выбранная модель пользователя (БД) + что сейчас в RAM на сервере."""
    uid = _uid(current_user)
    preferred = ""
    try:
        preferred = await get_user_selected_model_path(uid)
    except Exception:
        logger.exception("get_current_model: selected_model_path failed")

    loaded: dict = {}
    if get_model_info:
        try:
            info = get_model_info()
            if isinstance(info, dict) and info.get("path"):
                loaded = dict(info)
                save_app_settings(
                    {
                        "current_model_path": info["path"],
                        "current_model_name": info.get("name", "Unknown"),
                        "current_model_status": info.get("status", "loaded"),
                    }
                )
        except Exception:
            logger.exception("get_model_info error")
    if not loaded.get("path"):
        with logged_suppress(logger):
            s = load_app_settings()
            p = s.get("current_model_path")
            if p and (os.path.exists(p) or str(p).startswith("llm-svc") or "/" in str(p)):
                loaded = {
                    "name": s.get("current_model_name", _model_display_name(str(p))),
                    "path": p,
                    "status": s.get("current_model_status") or "loaded_from_settings",
                }

    # Для UI path = персональный выбор; server_loaded_path = фактический пул.
    path = preferred or str(loaded.get("path") or "")
    if not path:
        return {"name": "Модель не загружена", "path": "", "status": "not_loaded"}

    status = str(loaded.get("status") or "selected")
    loaded_path = str(loaded.get("path") or "")
    if preferred and loaded_path and preferred != loaded_path:
        status = "selected_not_loaded"

    return {
        "name": _model_display_name(path),
        "path": path,
        "status": status,
        "server_loaded_path": loaded_path or None,
        "selected_model_path": preferred or None,
    }


@router.get("")
@router.get("/")
async def list_models(
    current_user: Annotated[dict, Depends(get_current_user)],
):
    return await get_available_models(current_user)


@router.get("/available")
async def get_available_models(
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """
    Список моделей, доступных пользователю, из ВСЕХ зарегистрированных
    LLM-провайдеров. Формат path — новый: ``<provider_id>/<model_id>``.
    Поле ``provider`` содержит описание провайдера (kind, capabilities).
    """
    global _models_available_cache_payload, _models_available_cache_ts
    now = time.monotonic()
    cache_seconds = _models_available_cache_seconds()
    if (
        _models_available_cache_payload is not None
        and (now - _models_available_cache_ts) < cache_seconds
    ):
        if _models_available_cache_payload.get("models"):
            return _models_available_cache_payload

    async with _models_available_cache_lock:
        now = time.monotonic()
        if (
            _models_available_cache_payload is not None
            and (now - _models_available_cache_ts) < cache_seconds
        ):
            if _models_available_cache_payload.get("models"):
                return _models_available_cache_payload
        try:
            from backend.llm_providers import get_registry

            registry = await get_registry()
            default_id = registry.default_id

            async def _rows_for_provider(provider) -> Tuple[List[dict], Optional[str]]:
                try:
                    models = await provider.list_models()
                except Exception:
                    logger.exception("provider list_models error: %s", provider.id)
                    return ([], f"{provider.id}: list_models error")
                is_default = provider.id == default_id
                rows: List[dict] = []
                not_for_chat = [
                    m.model_id for m in models if not _is_chat_model(m.model_id)
                ]
                if not_for_chat:
                    # Каталог пересобирается раз в MODELS_AVAILABLE_CACHE_SECONDS
                    # (по умолчанию 60 с), а список эмбеддеров у провайдера не
                    # меняется — на INFO пишем только первый раз и при изменении.
                    log_on_change(
                        logger,
                        f"models.not_for_chat.{provider.id}",
                        tuple(not_for_chat),
                        "[MODELS] %s: в выбор модели чата не берём эмбеддеры и реранкеры: %s",
                        provider.id,
                        not_for_chat,
                    )
                for m in models:
                    if not _is_chat_model(m.model_id):
                        continue
                    extra = dict(m.extra or {})
                    avatar_url = extra.pop("avatar_url", None) or _avatar_url_for_local_model(
                        m.model_id
                    )
                    rows.append(
                        {
                            "name": m.model_id,
                            "display_name": m.display_name or m.model_id,
                            "path": m.path,
                            "provider_id": provider.id,
                            "provider_kind": provider.kind,
                            "provider_default": is_default,
                            "capabilities": {
                                "hot_swap": provider.capabilities.hot_swap,
                                "multi_loaded": provider.capabilities.multi_loaded,
                                "streaming": provider.capabilities.streaming,
                                "vision": provider.capabilities.vision,
                            },
                            "context_size": m.context_size,
                            "size": extra.pop("size", 0),
                            "size_mb": extra.pop("size_mb", 0),
                            "object": extra.pop("object", "model"),
                            "owned_by": extra.pop("owned_by", provider.id),
                            "llm_host_id": provider.id,
                            "avatar_url": avatar_url,
                            "extra": extra,
                        }
                    )
                return (rows, None)

            chat_providers = [p for p in registry.all() if _is_chat_provider(p)]
            skipped = [p.id for p in registry.all() if p not in chat_providers]
            if skipped:
                log_on_change(
                    logger,
                    "models.chat_invisible_providers",
                    tuple(skipped),
                    "[MODELS] в список моделей чата не берём провайдеров "
                    "с chat_visible: false — %s",
                    skipped,
                )
            pairs = await asyncio.gather(
                *(_rows_for_provider(p) for p in chat_providers)
            )
            result_models: List[dict] = []
            warnings: List[str] = []
            for rows, warn in pairs:
                result_models.extend(rows)
                if warn:
                    warnings.append(warn)
            response = {"models": result_models}
            if warnings:
                response["warning"] = "; ".join(warnings)

            # Пустой каталог при недоступном llm-svc не должен вытеснять
            # последний успешный снимок (иначе UI мигает «Нет моделей»).
            if result_models:
                _models_available_cache_payload = response
                _models_available_cache_ts = time.monotonic()
            elif (
                _models_available_cache_payload is not None
                and _models_available_cache_payload.get("models")
            ):
                return _models_available_cache_payload
            return response
        except Exception as e:
            logger.exception("get_available_models error")
            raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.post("/load", response_model=ModelLoadResponse)
async def load_model(
    request: ModelLoadRequest,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Загрузка весов модели.

    Ответ (и снятие UI-спиннера) — только после реального ensure_model_loaded.
    Без sync/to_thread/asyncio.run: иначе ответ может «залипнуть» после успеха в логах.
    """
    model_path = (request.model_path or "").strip()
    if not model_path:
        return ModelLoadResponse(message="model_path пуст", success=False)
    if os.path.isdir(model_path):
        return ModelLoadResponse(
            message=f"Передан путь к директории: {model_path}", success=False
        )
    try:
        from backend.llm_providers import get_registry

        registry = await get_registry()
        provider, model_id = registry.resolve(model_path)
        if not model_id:
            # Пришёл только id провайдера — берём первую доступную модель.
            if registry.contains(model_path):
                provider = registry.get(model_path)
                candidates = await provider.list_models()
                model_id = str(
                    (candidates[0].model_id if candidates else "") or ""
                ).strip()
            if not model_id:
                return ModelLoadResponse(
                    message="Не удалось определить model_id", success=False
                )

        ok = await provider.ensure_model_loaded(model_id)
        if not ok:
            return ModelLoadResponse(
                message="Не удалось загрузить модель", success=False
            )

        selected = f"{provider.id}/{model_id}"
        try:
            from backend import agent_llm_svc

            agent_llm_svc._selected_model_name = selected
        except Exception:
            pass

        save_app_settings(
            {
                "current_model_path": selected,
                "current_model_name": model_id,
                "current_model_status": "loaded",
            }
        )
        # Per-user выбор в PostgreSQL (разные пользователи — разные модели).
        try:
            await save_user_selected_model_path(_uid(current_user), selected)
        except Exception:
            logger.exception(
                "POST /api/models/load: не удалось сохранить selected_model_path user=%s",
                _uid(current_user),
            )
        logger.info("POST /api/models/load OK: %s user=%s", selected, _uid(current_user))
        return ModelLoadResponse(message="Модель успешно загружена", success=True)
    except Exception as e:
        logger.exception("POST /api/models/load error")
        return ModelLoadResponse(message=f"Ошибка: {public_error_detail(e)}", success=False)


@router.get("/settings")
async def get_model_settings(current_user: Annotated[dict, Depends(get_current_user)]):
    try:
        return await get_user_model_settings(_uid(current_user))
    except Exception:
        logger.exception("get_user_model_settings error")
        return await get_user_model_settings(None)


@router.put("/settings")
async def update_model_settings_api(
    settings_data: ModelSettings,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    try:
        saved = await save_user_model_settings(_uid(current_user), settings_data.dict())
        return {"message": "Настройки обновлены", "success": True, "settings": saved}
    except Exception as e:
        logger.exception("Ошибка операции")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.post("/settings/reset")
async def reset_model_settings(
    current_user: Annotated[dict, Depends(get_current_user)],
):
    try:
        settings = await reset_user_model_settings(_uid(current_user))
        return {"message": "Настройки сброшены", "success": True, "settings": settings}
    except Exception as e:
        logger.exception("Ошибка операции")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.get("/settings/recommended")
async def get_recommended_settings(
    current_user: Annotated[dict, Depends(get_current_user)],
):
    if not model_settings:
        raise HTTPException(status_code=503, detail="AI agent не доступен")
    try:
        return {
            "recommended": model_settings.get_recommended_settings(),
            "max_values": model_settings.get_max_values(),
        }
    except Exception as e:
        logger.exception("Ошибка операции")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e
