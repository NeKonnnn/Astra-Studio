"""routes/voice_assistant.py — parse / ask / live stream / TTS status / OmniVoice profile."""

import asyncio
import json
from pathlib import Path
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response, StreamingResponse

import backend.app_state as state
from backend.auth.jwt_handler import get_current_user
from backend.settings.logging import get_logger
from backend.utils.http_errors import public_error_detail
from backend.voice_assistant.live import ASK_SYSTEM, fetch_tts_status, iter_ask_sse
from backend.voice_assistant.omnivoice_client import (
    fetch_omnivoice_health,
    profile_dir,
    profile_paths,
    synthesize_with_clone,
)
from backend.voice_assistant.parser import parse_voice_intent
from backend.voice_assistant.schemas import (
    AskRequest,
    AskResponse,
    ParseIntentRequest,
    ParseIntentResponse,
)

logger = get_logger(__name__)

router = APIRouter(tags=["voice-assistant"], dependencies=[Depends(get_current_user)])


@router.get("/api/voice-assistant/tts-status")
async def voice_assistant_tts_status(_user: Annotated[dict, Depends(get_current_user)]):
    """Доступность Silero + список голосов для UI голосового помощника."""
    return await fetch_tts_status()


@router.post("/api/voice-assistant/parse", response_model=ParseIntentResponse)
async def parse_voice_assistant_intent(
    body: ParseIntentRequest,
    _user: Annotated[dict, Depends(get_current_user)],
):
    try:
        ask_agent = getattr(state, "ask_agent", None)
        get_model = getattr(state, "get_current_model_path", None)
        model_path = get_model() if callable(get_model) else None
        result = parse_voice_intent(body.text, ask_agent=ask_agent, model_path=model_path)
        logger.info("[VA] parse text=%r intent=%s", body.text[:200], result.get("intent"))
        return ParseIntentResponse(**result)
    except Exception as e:
        logger.exception("voice-assistant parse error")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.post("/api/voice-assistant/ask", response_model=AskResponse)
async def ask_voice_assistant(
    body: AskRequest,
    _user: Annotated[dict, Depends(get_current_user)],
):
    """Короткий ответ на свободный вопрос (batch, совместимость)."""
    ask_agent = getattr(state, "ask_agent", None)
    if not ask_agent:
        raise HTTPException(status_code=503, detail="AI сервис недоступен")
    get_model = getattr(state, "get_current_model_path", None)
    model_path = get_model() if callable(get_model) else None
    try:
        loop = asyncio.get_event_loop()
        raw = await loop.run_in_executor(
            None,
            lambda: ask_agent(
                body.text.strip(),
                history=[],
                streaming=False,
                model_path=model_path,
                system_prompt=ASK_SYSTEM,
            ),
        )
        answer = (raw if isinstance(raw, str) else str(raw)).strip()
        answer = answer.replace("**", "").replace("__", "").replace("`", "")
        if len(answer) > 800:
            answer = answer[:800].rsplit(" ", 1)[0] + "…"
        logger.info("[VA] ask text=%r answer=%r", body.text[:160], answer[:160])
        if not answer:
            answer = "Не удалось сформировать ответ."
        return AskResponse(answer=answer)
    except Exception as e:
        logger.exception("voice-assistant ask error")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.post("/api/voice-assistant/ask/stream")
async def ask_voice_assistant_stream(
    body: AskRequest,
    request: Request,
    _user: Annotated[dict, Depends(get_current_user)],
):
    """SSE: token-by-token ответ для live-озвучки + barge-in на клиенте."""
    ask_agent = getattr(state, "ask_agent", None)
    if not ask_agent:
        raise HTTPException(status_code=503, detail="AI сервис недоступен")
    get_model = getattr(state, "get_current_model_path", None)
    model_path = get_model() if callable(get_model) else None
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Пустой текст")

    async def event_gen():
        try:
            async for chunk in iter_ask_sse(text, ask_agent=ask_agent, model_path=model_path):
                if await request.is_disconnected():
                    logger.info("[VA] ask stream client disconnected")
                    break
                yield chunk
        except Exception as e:
            logger.exception("[VA] ask stream generator error")
            import json as _json

            yield f"data: {_json.dumps({'type': 'error', 'error': public_error_detail(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _user_id(user: dict) -> str:
    return str(user.get("user_id") or user.get("sub") or user.get("username") or "anonymous")


@router.get("/api/voice-assistant/omnivoice/status")
async def omnivoice_status(_user: Annotated[dict, Depends(get_current_user)]):
    return await fetch_omnivoice_health()


@router.get("/api/voice-assistant/voice-profile")
async def get_voice_profile(user: Annotated[dict, Depends(get_current_user)]):
    uid = _user_id(user)
    d = profile_dir(uid)
    meta_path = d / "meta.json"
    refs = list(d.glob("ref.*")) if d.is_dir() else []
    if not refs:
        return {"exists": False}
    meta = {}
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
    ref_path = refs[0]
    return {
        "exists": True,
        "bytes": ref_path.stat().st_size,
        "ref_text": meta.get("ref_text"),
        "created_at": meta.get("created_at"),
        "engine": "omnivoice",
    }


@router.post("/api/voice-assistant/voice-profile")
async def upload_voice_profile(
    user: Annotated[dict, Depends(get_current_user)],
    file: Annotated[UploadFile, File(...)],
    ref_text: Annotated[Optional[str], Form()] = None,
):
    """Сохранить образец голоса (3–10с) для OmniVoice. Только свой голос, с согласием."""
    uid = _user_id(user)
    raw = await file.read()
    if len(raw) < 2000:
        raise HTTPException(status_code=400, detail="Слишком короткая запись (нужно ~3–10 секунд)")
    if len(raw) > 8_000_000:
        raise HTTPException(status_code=400, detail="Файл слишком большой")

    d = profile_dir(uid)
    d.mkdir(parents=True, exist_ok=True)
    ref_path, meta_path = profile_paths(uid)
    # Сохраняем как есть (webm/wav); OmniVoice + ffmpeg справятся
    ext = ".wav"
    name = (file.filename or "").lower()
    if name.endswith(".webm"):
        ext = ".webm"
    elif name.endswith(".ogg"):
        ext = ".ogg"
    ref_path = d / f"ref{ext}"
    # Чистим старые
    for p in d.glob("ref.*"):
        try:
            p.unlink()
        except OSError:
            pass
    ref_path.write_bytes(raw)
    # Симлинк/копия как ref.wav путь для клиента omnivoice — пишем meta с реальным именем
    meta = {
        "ref_file": ref_path.name,
        "ref_text": (ref_text or "").strip() or None,
        "created_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        "consent": True,
        "content_type": file.content_type,
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    # Для synthesize_with_clone ожидаем Path — обновим helper чтобы читать meta
    logger.info("[VA] voice profile saved user=%s file=%s bytes=%d", uid, ref_path.name, len(raw))
    return {"ok": True, "exists": True, "bytes": len(raw), "ref_text": meta.get("ref_text")}


@router.delete("/api/voice-assistant/voice-profile")
async def delete_voice_profile(user: Annotated[dict, Depends(get_current_user)]):
    uid = _user_id(user)
    d = profile_dir(uid)
    if d.is_dir():
        for p in d.iterdir():
            try:
                p.unlink()
            except OSError:
                pass
    return {"ok": True, "exists": False}


@router.post("/api/voice-assistant/omnivoice/synthesize")
async def omnivoice_synthesize(
    user: Annotated[dict, Depends(get_current_user)],
    text: Annotated[str, Form(...)],
):
    """Озвучить текст клонированным голосом пользователя."""
    uid = _user_id(user)
    d = profile_dir(uid)
    meta_path = d / "meta.json"
    if not meta_path.is_file():
        raise HTTPException(status_code=404, detail="Профиль голоса не записан")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        meta = {}
    ref_name = meta.get("ref_file") or "ref.wav"
    ref_path = d / ref_name
    if not ref_path.is_file():
        # fallback
        candidates = list(d.glob("ref.*"))
        if not candidates:
            raise HTTPException(status_code=404, detail="Файл образца не найден")
        ref_path = candidates[0]
    try:
        wav = await synthesize_with_clone(
            text.strip(),
            ref_path,
            ref_text=meta.get("ref_text"),
        )
        return Response(content=wav, media_type="audio/wav", headers={"X-TTS-Engine": "omnivoice"})
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        logger.exception("[VA] omnivoice synthesize")
        raise HTTPException(status_code=503, detail=public_error_detail(e)) from e
