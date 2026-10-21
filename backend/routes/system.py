"""
routes/system.py - health-check, системный статус, socket-test
"""

import asyncio
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends

from backend.app_state import (
    ask_agent, save_dialog_entry, get_recent_dialog_history, clear_dialog_history,
    speak_text, recognize_speech, recognize_speech_from_file, check_stt_available,
    rag_client, model_settings, update_model_settings, reload_model_by_path,
    get_model_info, initialize_model, minio_client, UniversalTranscriber,
    settings,
)
import backend.app_state as state
from backend.auth.jwt_handler import get_current_user
from backend.settings.logging import get_logger
from backend.settings.logging.errors import logged_suppress
from backend.utils.http_errors import public_error_detail

router = APIRouter(tags=["system"])
logger = get_logger(__name__)

_urls = settings.urls


@router.get("/")
async def root():
    return {"message": "astrachat Web API", "status": "active", "version": "1.0.0"}


@router.get("/socket-test")
async def socket_test(
    current_user: Annotated[dict, Depends(get_current_user)],
):
    return {
        "socketio_status": "active",
        "endpoint": "/socket.io/",
        "cors_origins": [
            getattr(_urls, "frontend_port", None),
            getattr(_urls, "frontend_port_ipv4", None),
            getattr(_urls, "frontend_port_2", None),
            getattr(_urls, "frontend_port_3", None),
        ],
        "ping_timeout": 300,
        "ping_interval": 15,
    }


@router.get("/health")
async def health_check():
    try:
        model_info = get_model_info() if get_model_info else {"loaded": False}
        stt_status = check_stt_available() if check_stt_available else False

        rag_available = False
        if rag_client:
            with logged_suppress(logger):
                health = await rag_client.health()
                rag_available = bool(health)
                from backend.settings.rag_client import reconcile_rag_models_provider

                await reconcile_rag_models_provider(
                    rag_client,
                    health,
                    str(getattr(state, "rag_embedding_model_path", "") or ""),
                    str(getattr(state, "rag_reranker_model_path", "") or ""),
                )

        mcp_status = {"enabled": False, "initialized": False}
        try:
            from backend.mcp.platform import get_mcp_platform
            from backend.mcp.types import McpCallContext

            platform = get_mcp_platform()
            mcp_status = {
                "enabled": platform.enabled,
                "initialized": platform.initialized,
            }
            if platform.initialized and platform.enabled:
                agg = await platform.get_aggregate_status(
                    McpCallContext(user_id="system", username="system", is_admin=True)
                )
                mcp_status.update(
                    {
                        "servers_total": agg.servers_total,
                        "servers_connected": agg.servers_connected,
                        "tools_total": agg.tools_total,
                        "pool": platform.get_pool_metrics(),
                    }
                )
        except Exception:
            pass

        return {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "services": {
                "llm_model": model_info.get("loaded", False),
                "stt_whisperx": stt_status,
                "rag_service": rag_available,
                "transcriber": UniversalTranscriber is not None,
                "mcp": mcp_status,
            },
        }
    except Exception as e:
        logger.exception("Ошибка операции")
        return {
            "status": "unhealthy",
            "error": public_error_detail(e),
            "timestamp": datetime.now().isoformat(),
        }


@router.get("/api/system/status")
async def get_system_status(
    current_user: Annotated[dict, Depends(get_current_user)],
):
    module_status = {
        "ai_agent": {
            "available": ask_agent is not None,
            "functions": {
                "ask_agent": ask_agent is not None,
                "model_settings": model_settings is not None,
                "update_model_settings": update_model_settings is not None,
                "reload_model_by_path": reload_model_by_path is not None,
                "get_model_info": get_model_info is not None,
                "initialize_model": initialize_model is not None,
            },
        },
        "memory": {
            "available": save_dialog_entry is not None,
            "functions": {
                "save_dialog_entry": save_dialog_entry is not None,
                "load_dialog_history": get_recent_dialog_history is not None,
                "clear_dialog_history": clear_dialog_history is not None,
            },
        },
        "voice": {
            "available": speak_text is not None and recognize_speech_from_file is not None,
            "functions": {
                "speak_text": speak_text is not None,
                "recognize_speech": recognize_speech is not None,
                "recognize_speech_from_file": recognize_speech_from_file is not None,
                "check_stt_available": check_stt_available is not None,
            },
        },
        "transcription": {
            "available": state.transcriber is not None,
            "functions": {"universal_transcriber": UniversalTranscriber is not None},
        },
        "rag_service": {"available": rag_client is not None},
    }
    try:
        from backend.mcp.platform import get_mcp_platform

        platform = get_mcp_platform()
        module_status["mcp"] = {
            "available": True,
            "enabled": platform.enabled,
            "initialized": platform.initialized,
        }
        try:
            from backend.llm_providers.routing import describe_llm_routes

            module_status["llm_routing"] = await describe_llm_routes()
        except Exception:
            pass
    except Exception:
        module_status["mcp"] = {"available": False}

    services_health = {}
    try:
        from backend.llm_client import get_llm_service
        import httpx

        service = await get_llm_service()

        async def check_diar():
            try:
                async with httpx.AsyncClient(timeout=2.0) as c:
                    r = await c.get(f"{service.client.diarization_url}/health")
                    return r.json() if r.status_code == 200 else {"status": "error"}
            except Exception:
                return {"status": "unreachable"}

        tasks = [
            service.client.health_check(),
            service.client.get_transcription_health(),
            service.client.get_tts_health(),
            service.client.get_ocr_health(),
            check_diar(),
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        keys = ["llm", "stt", "tts", "ocr", "diarization"]
        services_health = {k: (results[i] if not isinstance(results[i], Exception) else {"status": "error"})
                           for i, k in enumerate(keys)}
    except Exception as e:
        services_health = {"error": str(e)}

    return {"modules": module_status, "microservices": services_health, "timestamp": datetime.now().isoformat()}
