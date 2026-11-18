"""Хуки применения модели после успешной загрузки."""

from __future__ import annotations

from typing import Any, Dict, Optional

from backend.settings.logging import get_logger

logger = get_logger(__name__)


async def apply_model(
    *,
    apply_action: str,
    model_type: str,
    local_name: Optional[str] = None,
    stt_size: Optional[str] = None,
    rag_kind: Optional[str] = None,
) -> Dict[str, Any]:
    action = (apply_action or "none").strip().lower()
    if action in {"", "none"}:
        return {"applied": False, "action": "none", "message": "apply not requested"}

    try:
        if action == "load_llm":
            return await _apply_load_llm(local_name)
        if action == "select_rag":
            kind = rag_kind or ("embedding" if model_type == "rag_embed" else "reranker")
            return await _apply_select_rag(kind, local_name)
        if action == "reload_stt":
            return await _apply_reload_stt(stt_size or local_name)
        if action == "reload_tts":
            return await _proxy_reload("tts", "/v1/tts/reload")
        if action == "reload_diarization":
            return await _proxy_reload("diarization", "/v1/diarization/reload")
        if action == "reload_ocr":
            return await _proxy_reload("ocr", "/v1/ocr/reload")
        if action == "reload_omnivoice":
            return await _proxy_reload("omnivoice", "/v1/reload")
        return {"applied": False, "action": action, "message": f"unknown apply: {action}"}
    except Exception as exc:
        logger.exception("apply_model failed: %s", action)
        return {
            "applied": False,
            "action": action,
            "needs_restart": True,
            "error": str(exc),
            "message": "Скачивание завершено, но apply не удался — может потребоваться restart сервиса",
        }


async def _apply_load_llm(local_name: Optional[str]) -> Dict[str, Any]:
    from backend.llm_providers import get_registry

    name = (local_name or "").strip()
    if name.endswith(".gguf"):
        name = name[: -len(".gguf")]
    if not name:
        return {"applied": False, "action": "load_llm", "message": "local_name пуст"}

    # Предпочитаем local/<stem>
    model_path = f"local/{name}"
    registry = get_registry()
    provider, model_id = registry.resolve(model_path)
    ok = await provider.ensure_model_loaded(model_id)
    if ok:
        from backend.app_state import save_app_settings

        save_app_settings({"current_model_path": model_path})
    return {
        "applied": bool(ok),
        "action": "load_llm",
        "model_path": model_path,
        "message": "LLM загружена" if ok else "LLM не удалось загрузить",
    }


async def _apply_select_rag(kind: str, local_name: Optional[str]) -> Dict[str, Any]:
    from backend.settings.rag_models_client import get_rag_models_client

    name = (local_name or "").strip()
    if not name:
        return {"applied": False, "action": "select_rag", "message": "local_name пуст"}
    model_type = "embedding" if kind in {"embedding", "rag_embed", "embed"} else "reranker"
    model_path = f"local/{name}"
    client = get_rag_models_client()
    result = await client.select_model(model_type, model_path)
    return {
        "applied": True,
        "action": "select_rag",
        "model_type": model_type,
        "model_path": model_path,
        "result": result,
        "message": f"RAG {model_type} → {model_path}",
    }


async def _apply_reload_stt(stt_size: Optional[str]) -> Dict[str, Any]:
    from backend.llm_client import LLMClient

    client = LLMClient()
    size = (stt_size or "medium").strip()
    result = await client.reload_whisperx_models(model_size=size)
    return {
        "applied": True,
        "action": "reload_stt",
        "stt_size": size,
        "result": result,
        "message": f"STT reload ({size})",
    }


async def _proxy_reload(service_key: str, path: str) -> Dict[str, Any]:
    import os

    import httpx

    from backend.llm_client import LLMClient

    client = LLMClient()
    base_map = {
        "tts": getattr(client, "tts_url", None),
        "diarization": getattr(client, "diarization_url", None),
        "ocr": getattr(client, "ocr_url", None),
        "omnivoice": (os.getenv("SVC_OMNIVOICE_URL") or "").rstrip("/") or None,
    }
    base = base_map.get(service_key)
    if not base:
        return {
            "applied": False,
            "action": f"reload_{service_key}",
            "needs_restart": True,
            "message": f"URL сервиса {service_key} не найден",
        }

    async with httpx.AsyncClient(timeout=120.0) as http:
        resp = await http.post(f"{base}{path}")
        if resp.status_code >= 400:
            return {
                "applied": False,
                "action": f"reload_{service_key}",
                "needs_restart": True,
                "error": resp.text[:300],
                "message": f"reload {service_key} HTTP {resp.status_code}",
            }
        data = {}
        try:
            data = resp.json()
        except Exception:
            data = {"raw": resp.text[:200]}
        return {
            "applied": True,
            "action": f"reload_{service_key}",
            "result": data,
            "message": f"{service_key} reloaded",
        }
