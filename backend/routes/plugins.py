"""API каталога HTTP-плагинов и прокси invoke."""

from __future__ import annotations

import os
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from backend.auth.jwt_handler import get_current_user
from backend.settings.logging import get_logger
from backend.utils.http_errors import public_error_detail

logger = get_logger(__name__)

# Объявляем router сразу — чтобы циклические импорты не ломали
# `from backend.routes.plugins import router` (application.py).
router = APIRouter(tags=["plugins"])
__all__ = ["router"]


PLUGIN_MAX_UPLOAD_MB_ENV = "PLUGIN_INVOKE_MAX_UPLOAD_MB"
DEFAULT_PLUGIN_MAX_UPLOAD_MB = 20
_UPLOAD_READ_CHUNK = 1024 * 1024


def _max_upload_bytes() -> int:
    """Лимит размера одного загружаемого файла (env PLUGIN_INVOKE_MAX_UPLOAD_MB, по умолчанию 20 МБ)."""
    raw = (os.getenv(PLUGIN_MAX_UPLOAD_MB_ENV) or "").strip()
    try:
        mb = float(raw) if raw else float(DEFAULT_PLUGIN_MAX_UPLOAD_MB)
    except ValueError:
        logger.warning(
            "%s=%r не число — используется %s МБ",
            PLUGIN_MAX_UPLOAD_MB_ENV,
            raw,
            DEFAULT_PLUGIN_MAX_UPLOAD_MB,
        )
        mb = float(DEFAULT_PLUGIN_MAX_UPLOAD_MB)
    if mb <= 0:
        mb = float(DEFAULT_PLUGIN_MAX_UPLOAD_MB)
    return int(mb * 1024 * 1024)


def _platform():
    from backend.plugins.platform import get_plugins_platform

    return get_plugins_platform()


async def _read_upload_limited(upload: UploadFile, field: str) -> bytes:
    """Читает файл кусками и обрывает чтение при превышении лимита (HTTP 413)."""
    limit = _max_upload_bytes()
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(_UPLOAD_READ_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise HTTPException(
                status_code=413,
                detail=f"{field}: файл слишком большой (максимум {limit // (1024 * 1024)} МБ)",
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.get("/api/plugins")
async def list_plugins(
    current_user: Annotated[dict, Depends(get_current_user)],
    include_health: bool = False,
):
    """Каталог плагинов для галереи и конструктора агентов."""
    _ = current_user
    platform = _platform()
    catalog_ids = [p.id for p in platform.list_plugins()]
    logger.info(
        "[plugins.list] user=%s enabled=%s include_health=%s catalog=%s",
        (current_user or {}).get("username") or (current_user or {}).get("sub") or "?",
        platform.enabled,
        include_health,
        catalog_ids,
    )
    if not platform.enabled:
        logger.warning(
            "[plugins.list] platform disabled (plugins.enabled=false or PLUGINS_ENABLED=false); "
            "returning empty catalog (configured ids=%s)",
            catalog_ids,
        )
        return {"success": True, "enabled": False, "plugins": [], "catalog_ids": catalog_ids}
    plugins = platform.list_plugins_public()
    if include_health:
        enriched = []
        for p in plugins:
            if not p.enabled:
                logger.info("[plugins.list] skip health for disabled id=%s", p.id)
                enriched.append(p)
                continue
            health = await platform.health(p.id)
            logger.info(
                "[plugins.list] health id=%s ok=%s error=%s",
                p.id,
                health.ok,
                health.error,
            )
            cfg = platform.get_plugin(p.id)
            if not cfg:
                enriched.append(p)
                continue
            enriched.append(
                platform.to_public(
                    cfg,
                    healthy=health.ok,
                    health_detail=health.detail if health.ok else {"error": health.error},
                )
            )
        plugins = enriched
    logger.info(
        "[plugins.list] response count=%s ids=%s",
        len(plugins),
        [p.id for p in plugins],
    )
    return {
        "success": True,
        "enabled": True,
        "plugins": [p.model_dump() for p in plugins],
        "catalog_ids": catalog_ids,
    }


@router.get("/api/plugins/{plugin_id}")
async def get_plugin(
    plugin_id: str,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    _ = current_user
    platform = _platform()
    plugin = platform.get_plugin(plugin_id)
    if not plugin:
        raise HTTPException(status_code=404, detail="Plugin not found")
    health = await platform.health(plugin_id)
    return {
        "success": True,
        "plugin": platform.to_public(
            plugin,
            healthy=health.ok,
            health_detail=health.detail if health.ok else {"error": health.error},
        ).model_dump(),
    }


@router.get("/api/plugins/{plugin_id}/health")
async def plugin_health(
    plugin_id: str,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    _ = current_user
    platform = _platform()
    if not platform.get_plugin(plugin_id):
        raise HTTPException(status_code=404, detail="Plugin not found")
    result = await platform.health(plugin_id)
    return {"success": True, "health": result.model_dump()}


@router.post("/api/plugins/{plugin_id}/invoke")
async def invoke_plugin(
    plugin_id: str,
    current_user: Annotated[dict, Depends(get_current_user)],
    model_file: UploadFile = File(
        ..., description="Основной файл для плагина (Excel для cash-flow)"
    ),
    quality_file: Optional[UploadFile] = File(None),
    prompt: Optional[str] = Form(None),
    request_id: Optional[str] = Form(None),
):
    """Проксирует multipart-вызов к микросервису плагина (cash-flow /audit и т.п.)."""
    from backend.plugins.platform import PluginInvokeTimeout

    _ = current_user
    platform = _platform()
    plugin = platform.get_plugin(plugin_id)
    if not plugin:
        raise HTTPException(status_code=404, detail="Plugin not found")
    if not plugin.enabled:
        raise HTTPException(status_code=503, detail="Plugin disabled")

    model_bytes = await _read_upload_limited(model_file, "model_file")
    if not model_bytes:
        raise HTTPException(status_code=400, detail="model_file is empty")

    files = [
        (
            plugin.invoke_file_field or "model_file",
            (
                model_file.filename or "model.xlsx",
                model_bytes,
                model_file.content_type
                or "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
        )
    ]
    if quality_file is not None:
        q_bytes = await _read_upload_limited(quality_file, "quality_file")
        if q_bytes:
            files.append(
                (
                    plugin.invoke_optional_file_field or "quality_file",
                    (
                        quality_file.filename or "quality.xlsx",
                        q_bytes,
                        quality_file.content_type
                        or "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    ),
                )
            )

    form = {}
    if prompt:
        form[plugin.invoke_prompt_field or "prompt"] = prompt
    if request_id:
        form["request_id"] = request_id

    try:
        result = await platform.invoke_multipart(
            plugin_id, files=files, form=form or None
        )
    except HTTPException:
        raise
    except KeyError:
        raise HTTPException(status_code=404, detail="Plugin not found") from None
    except PluginInvokeTimeout as e:
        logger.warning("Plugin invoke timeout id=%s: %s", plugin_id, e)
        raise HTTPException(status_code=504, detail=str(e)) from e
    except Exception as e:
        logger.exception("Plugin invoke failed id=%s", plugin_id)
        # Текст исключения (хосты, порты) наружу не отдаём (CWE-209) — он в серверном логе.
        raise HTTPException(
            status_code=502,
            detail=public_error_detail(
                e, f"Ошибка при вызове плагина '{plugin_id}'"
            ),
        ) from e

    markdown = ""
    if plugin_id == "cash-flow":
        from backend.plugins.artifact_format import (
            findings_fallback_markdown,
            verdict_markdown_from_plugin_result,
        )

        markdown = (
            verdict_markdown_from_plugin_result(result)
            or findings_fallback_markdown(result)
            or ""
        )

    logger.info(
        "Plugin invoke ok id=%s status=%s markdown_len=%s",
        plugin_id,
        result.get("status") if isinstance(result, dict) else None,
        len(markdown),
    )

    return {
        "success": True,
        "plugin_id": plugin_id,
        "result": result,
        "markdown": markdown,
    }

