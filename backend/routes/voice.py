"""
routes/voice.py - синтез речи, распознавание, настройки транскрибации
"""

import asyncio
import os
import re
import shutil
import tempfile
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

import backend.app_state as state
from backend.app_state import (
    minio_client,
    recognize_speech_from_file,
    save_app_settings,
    speak_text,
)
from backend.auth.jwt_handler import get_current_user
from backend.schemas import TranscriptionSettings, VoiceSettings, VoiceSynthesizeRequest
from backend.settings.logging import get_logger
from backend.settings.logging.errors import logged_suppress
from backend.settings.service_toggles import require_service
from backend.utils.http_errors import public_error_detail

logger = get_logger(__name__)

# Единственный реализованный движок — whisperx (legacy-значение vosk мапится на него)
ALLOWED_TRANSCRIPTION_ENGINES = frozenset({"whisperx"})
_TRANSCRIPTION_ENGINE_ALIASES = {"vosk": "whisperx"}
_LANGUAGE_RE = re.compile(r"[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})?")

router = APIRouter(tags=["voice"], dependencies=[Depends(get_current_user)])


@router.post("/api/voice/synthesize")
async def synthesize_speech(request: VoiceSynthesizeRequest):
    require_service("tts")
    if not speak_text:
        raise HTTPException(status_code=503, detail="Модуль синтеза речи недоступен.")
    temp_dir = tempfile.gettempdir()
    audio_file = os.path.join(temp_dir, f"speech_{datetime.now().timestamp()}.wav")
    try:
        loop = asyncio.get_event_loop()
        success = await loop.run_in_executor(
            None,
            lambda: speak_text(
                text=request.text,
                speaker=request.voice_speaker,
                voice_id=request.voice_id,
                speech_rate=request.speech_rate,
                save_to_file=audio_file,
            ),
        )
        if success and os.path.exists(audio_file):
            if minio_client:
                try:
                    with open(audio_file, "rb") as f:
                        minio_client.upload_file(
                            f.read(),
                            minio_client.generate_object_name(
                                prefix="speech_", extension=".wav"
                            ),
                            content_type="audio/wav",
                        )
                except Exception:
                    logger.exception("MinIO upload failed")
            temp_copy = os.path.join(
                temp_dir, f"speech_copy_{datetime.now().timestamp()}.wav"
            )
            shutil.copy2(audio_file, temp_copy)

            async def _cleanup():
                with logged_suppress(logger):
                    if os.path.exists(temp_copy):
                        os.remove(temp_copy)

            return FileResponse(
                temp_copy,
                media_type="audio/wav",
                filename="speech.wav",
                background=_cleanup,
            )
        raise HTTPException(status_code=500, detail="Не удалось создать аудиофайл")
    except Exception as e:
        logger.exception("Ошибка операции")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e
    finally:
        with logged_suppress(logger):
            if os.path.exists(audio_file):
                os.remove(audio_file)


@router.post("/api/voice/recognize")
async def recognize_speech_api(audio_file: Annotated[UploadFile, File(...)]):
    require_service("stt")
    if not recognize_speech_from_file:
        return {
            "text": "",
            "success": False,
            "error": "Модуль распознавания речи недоступен.",
        }
    temp_dir = tempfile.gettempdir()
    file_path = None
    file_object_name = None
    try:
        content = await audio_file.read()
        if minio_client:
            try:
                file_object_name = minio_client.generate_object_name(
                    prefix="audio_", extension=".wav"
                )
                minio_client.upload_file(
                    content, file_object_name, content_type="audio/wav"
                )
                file_path = minio_client.get_file_path(file_object_name)
            except Exception:
                logger.exception("MinIO")
                file_path = os.path.join(
                    temp_dir, f"audio_{datetime.now().timestamp()}.wav"
                )
                with open(file_path, "wb") as f:
                    f.write(content)
        else:
            file_path = os.path.join(
                temp_dir, f"audio_{datetime.now().timestamp()}.wav"
            )
            with open(file_path, "wb") as f:
                f.write(content)
        text = recognize_speech_from_file(file_path)
        return {"text": text, "success": True, "timestamp": datetime.now().isoformat()}
    except Exception as e:
        logger.exception("Ошибка операции")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e
    finally:
        with logged_suppress(logger):
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
            if minio_client and file_object_name:
                with logged_suppress(logger):
                    minio_client.delete_file(file_object_name)


@router.get("/api/voice/settings")
async def get_voice_settings():
    return {"voice_id": "ru", "speech_rate": 1.0, "voice_speaker": "baya"}


@router.put("/api/voice/settings")
async def update_voice_settings(settings_data: VoiceSettings):
    """Серверного хранилища настроек голоса нет: значения только эхом возвращаются клиенту.

    GET /api/voice/settings всегда отдаёт значения по умолчанию, поэтому ответ
    явно помечен ``persisted: False`` — клиент должен хранить выбор у себя.
    """
    return {
        "message": "Настройки приняты, но не сохраняются на сервере (хранятся на клиенте)",
        "success": True,
        "persisted": False,
        "settings": settings_data.model_dump(),
    }


@router.get("/api/transcription/settings")
async def get_transcription_settings():
    return {
        "engine": state.current_transcription_engine,
        "language": state.current_transcription_language,
        "auto_detect": True,
    }


@router.put("/api/transcription/settings")
async def update_transcription_settings(settings_data: TranscriptionSettings):
    # Валидация до try: иначе 400 превратится в 500 общим обработчиком
    eng = None
    if settings_data.engine:
        eng = _TRANSCRIPTION_ENGINE_ALIASES.get(
            settings_data.engine.strip().lower(), settings_data.engine.strip().lower()
        )
        if eng not in ALLOWED_TRANSCRIPTION_ENGINES:
            raise HTTPException(
                status_code=400, detail="Неподдерживаемый движок транскрибации"
            )
    language = None
    if settings_data.language:
        language = settings_data.language.strip()
        if not _LANGUAGE_RE.fullmatch(language):
            raise HTTPException(status_code=400, detail="Некорректный код языка")
    try:
        if eng:
            state.current_transcription_engine = eng
            if state.transcriber and hasattr(state.transcriber, "switch_engine"):
                state.transcriber.switch_engine(state.current_transcription_engine)
        if language:
            state.current_transcription_language = language
            if state.transcriber and hasattr(state.transcriber, "set_language"):
                state.transcriber.set_language(state.current_transcription_language)
        save_app_settings(
            {
                "transcription_engine": state.current_transcription_engine,
                "transcription_language": state.current_transcription_language,
            }
        )
        return {"message": "Настройки обновлены", "success": True}
    except Exception as e:
        logger.exception("Ошибка операции")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e
