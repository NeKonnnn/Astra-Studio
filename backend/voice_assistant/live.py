"""Live-пайплайн голосового помощника: streaming ask + TTS status."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, AsyncIterator, Dict, Optional

import httpx

from backend.settings.logging import get_logger

logger = get_logger(__name__)

ASK_SYSTEM = (
    "Ты голосовой помощник AstraChat. Отвечай кратко вслух: 1–3 предложения, "
    "без markdown, без списков и emoji. Язык ответа — русский."
)

SILERO_VOICES_RU = ["baya", "xenia", "kseniya", "aidar", "eugene"]


def _tts_base_url() -> Optional[str]:
    url = (os.getenv("SVC_TTS_URL") or "").strip().rstrip("/")
    return url or None


async def fetch_tts_status() -> Dict[str, Any]:
    """Статус Silero TTS + список голосов (для UI голосового помощника)."""
    base = _tts_base_url()
    if not base:
        # fallback через llm client, если env не задан
        try:
            from backend.llm_client import get_llm_service

            service = await get_llm_service()
            base = getattr(getattr(service, "client", None), "tts_url", None)
        except Exception:
            logger.exception("[VA] tts status: cannot resolve TTS URL")
            base = None

    if not base:
        return {
            "available": False,
            "status": "unavailable",
            "engine": "silero",
            "error": "SVC_TTS_URL не задан",
            "voices": {"ru": SILERO_VOICES_RU},
            "blocked": True,
            "hint": "TTS-сервис не сконфигурирован. Будет использован голос браузера.",
        }

    health: Dict[str, Any] = {}
    voices: Dict[str, Any] = {"ru": SILERO_VOICES_RU}
    error: Optional[str] = None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            # Канонический health в SVC-SPEECH-SYNT: /v1/tts/health
            for path in ("/v1/tts/health", "/v1/health"):
                try:
                    r = await client.get(f"{base}{path}")
                    if r.status_code < 500:
                        health = r.json() if r.headers.get("content-type", "").startswith("application/json") else {
                            "status": "ok" if r.is_success else "unhealthy",
                            "http_status": r.status_code,
                        }
                        if r.is_success:
                            break
                except Exception:
                    continue
            try:
                rv = await client.get(f"{base}/v1/tts/voices")
                if rv.is_success:
                    data = rv.json()
                    if isinstance(data.get("voices"), dict):
                        voices = data["voices"]
            except Exception:
                logger.debug("[VA] tts voices fetch failed", exc_info=True)
    except Exception as e:
        error = str(e)
        logger.warning("[VA] tts status error: %s", e)

    status = str(health.get("status") or ("unhealthy" if error else "unknown"))
    available = status in {"healthy", "ok", "up"} and not error
    blocked = not available

    omnivoice: Dict[str, Any] = {"available": False, "ready": False, "status": "unknown"}
    try:
        from backend.voice_assistant.omnivoice_client import fetch_omnivoice_health

        omnivoice = await fetch_omnivoice_health()
    except Exception:
        logger.debug("[VA] omnivoice status skipped", exc_info=True)

    return {
        "available": available,
        "blocked": blocked,
        "status": status if not error else "unreachable",
        "engine": "silero",
        "health": health,
        "voices": voices,
        "error": error,
        "hint": (
            None
            if available
            else "Silero недоступен или заблокирован. Голосовой помощник будет использовать speechSynthesis браузера."
        ),
        "omnivoice": omnivoice,
    }


async def iter_ask_sse(
    text: str,
    *,
    ask_agent,
    model_path: Optional[str] = None,
) -> AsyncIterator[str]:
    """SSE-события: token / done / error. stream_callback → очередь."""
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    stop = {"flag": False}

    def _cb(chunk: str, acc: str, role: str = "content") -> bool:
        if stop["flag"]:
            return False
        if role in {"reasoning", "heartbeat"}:
            return True
        if not chunk:
            return True
        loop.call_soon_threadsafe(queue.put_nowait, ("token", chunk, acc))
        return True

    def _run() -> None:
        try:
            ask_agent(
                text.strip(),
                history=[],
                streaming=True,
                stream_callback=_cb,
                model_path=model_path,
                system_prompt=ASK_SYSTEM,
                max_tokens=256,
                temperature=0.6,
            )
            loop.call_soon_threadsafe(queue.put_nowait, ("done", "", ""))
        except Exception as e:
            logger.exception("[VA] ask stream failed")
            loop.call_soon_threadsafe(queue.put_nowait, ("error", str(e), ""))

    task = loop.run_in_executor(None, _run)
    try:
        while True:
            kind, chunk, acc = await queue.get()
            if kind == "token":
                payload = {"type": "token", "chunk": chunk, "text": acc}
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            elif kind == "done":
                yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
                break
            elif kind == "error":
                yield f"data: {json.dumps({'type': 'error', 'error': chunk}, ensure_ascii=False)}\n\n"
                break
    finally:
        stop["flag"] = True
        # Не блокируем disconnect на долгий LLM — колбэк сам прекратит стрим
        if not task.done():
            task.add_done_callback(lambda _f: None)
