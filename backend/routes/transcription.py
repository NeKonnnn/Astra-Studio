"""
routes/transcription.py - транскрибация файлов и YouTube
"""

import asyncio
import concurrent.futures
import os
import tempfile
from datetime import datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

import backend.app_state as state
from backend.app_state import minio_client
from backend.auth.jwt_handler import get_current_user
from backend.schemas import YouTubeTranscribeRequest
from backend.settings.logging import get_logger
from backend.settings.logging.errors import logged_suppress
from backend.settings.service_toggles import require_service
from backend.utils.http_errors import public_error_detail
from backend.utils.url_safety import UnsafeUrlError, validate_public_http_url

logger = get_logger(__name__)

# transcription_id -> владелец задачи (user_id/username): остановить может только он или админ
_JOB_OWNERS: dict[str, str] = {}
_TRANSCRIPTION_FAILED_DETAIL = "Не удалось выполнить транскрибацию"


def _user_key(current_user: dict) -> str:
    return str(
        (current_user or {}).get("user_id")
        or (current_user or {}).get("username")
        or ""
    ).strip()

router = APIRouter(
    prefix="/api/transcribe",
    tags=["transcription"],
    dependencies=[Depends(get_current_user)],
)


@router.post("/upload")
async def transcribe_file(
    file: Annotated[UploadFile, File(...)],
    current_user: Annotated[dict, Depends(get_current_user)],
    request_id: Annotated[Optional[str], Form()] = None,
):
    require_service("diarization")  # FEATURE-FLAG
    require_service("stt")  # FEATURE-FLAG
    import uuid

    tid = request_id or str(uuid.uuid4())
    if not state.transcriber:
        raise HTTPException(status_code=503, detail="Transcriber не доступен")
    owner = _user_key(current_user)
    existing_owner = _JOB_OWNERS.get(tid)
    if existing_owner and existing_owner != owner:
        raise HTTPException(status_code=409, detail="transcription_id уже используется")
    _JOB_OWNERS[tid] = owner
    state.stop_transcription_flags[tid] = False
    temp_dir = tempfile.gettempdir()
    file_path = None
    file_object_name = None
    try:
        content = await file.read()
        if minio_client:
            try:
                ext = os.path.splitext(file.filename)[1] if file.filename else ""
                file_object_name = minio_client.generate_object_name(
                    prefix="media_", extension=ext
                )
                minio_client.upload_file(
                    content, file_object_name, content_type="application/octet-stream"
                )
                file_path = minio_client.get_file_path(file_object_name)
            except Exception:
                logger.exception("MinIO")
                file_path = os.path.join(
                    temp_dir, f"media_{datetime.now().timestamp()}_{file.filename}"
                )
                with open(file_path, "wb") as f:
                    f.write(content)
        else:
            file_path = os.path.join(
                temp_dir, f"media_{datetime.now().timestamp()}_{file.filename}"
            )
            with open(file_path, "wb") as f:
                f.write(content)
        if state.stop_transcription_flags.get(tid, False):
            raise HTTPException(status_code=499, detail="Транскрибация остановлена")
        loop = asyncio.get_event_loop()

        def _transcribe():
            if state.stop_transcription_flags.get(tid, False):
                return (False, "Транскрибация была остановлена")
            try:
                if hasattr(state.transcriber, "transcribe_with_diarization"):
                    return state.transcriber.transcribe_with_diarization(file_path)
                return state.transcriber.transcribe_audio_file(file_path)
            except Exception:
                logger.exception("Ошибка операции")
                return (False, _TRANSCRIPTION_FAILED_DETAIL)

        with concurrent.futures.ThreadPoolExecutor() as ex:
            success, result = await loop.run_in_executor(ex, _transcribe)
        state.stop_transcription_flags.pop(tid, None)
        if success:
            return {
                "transcription": result,
                "filename": file.filename,
                "success": True,
                "timestamp": datetime.now().isoformat(),
                "diarization": True,
                "transcription_id": tid,
            }
        else:
            if "остановлена" in str(result).lower():
                raise HTTPException(status_code=499, detail=result)
            raise HTTPException(status_code=400, detail=result)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка операции")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e
    finally:
        if _JOB_OWNERS.get(tid) == owner:
            _JOB_OWNERS.pop(tid, None)
        with logged_suppress(logger):
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
            if minio_client and file_object_name:
                with logged_suppress(logger):
                    minio_client.delete_file(file_object_name)


@router.post("/stop")
async def stop_transcription(
    request: dict,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Останавливает задачу: только её владелец или администратор."""
    tid = request.get("transcription_id")
    if not tid or not isinstance(tid, str):
        raise HTTPException(status_code=400, detail="transcription_id обязателен")
    owner = _JOB_OWNERS.get(tid)
    is_admin = bool((current_user or {}).get("is_admin"))
    if owner and owner != _user_key(current_user) and not is_admin:
        raise HTTPException(status_code=403, detail="Нет доступа к этой задаче")
    state.stop_transcription_flags[tid] = True
    return {
        "success": True,
        "message": "Команда остановки отправлена",
        "transcription_id": tid,
    }


@router.post("/upload/diarization")
async def transcribe_with_diarization(file: Annotated[UploadFile, File(...)]):
    require_service("diarization")  # FEATURE-FLAG
    require_service("stt")  # FEATURE-FLAG
    if not state.transcriber:
        raise HTTPException(status_code=503, detail="Transcriber не доступен")
    temp_dir = tempfile.gettempdir()
    file_path = None
    file_object_name = None
    try:
        content = await file.read()
        if minio_client:
            try:
                ext = os.path.splitext(file.filename)[1] if file.filename else ""
                file_object_name = minio_client.generate_object_name(
                    prefix="media_diarization_", extension=ext
                )
                minio_client.upload_file(
                    content, file_object_name, content_type="application/octet-stream"
                )
                file_path = minio_client.get_file_path(file_object_name)
            except Exception:
                logger.exception("MinIO")
                file_path = os.path.join(
                    temp_dir, f"media_diar_{datetime.now().timestamp()}_{file.filename}"
                )
                with open(file_path, "wb") as f:
                    f.write(content)
        else:
            file_path = os.path.join(
                temp_dir, f"media_diar_{datetime.now().timestamp()}_{file.filename}"
            )
            with open(file_path, "wb") as f:
                f.write(content)
        loop = asyncio.get_event_loop()

        def _tr():
            if hasattr(state.transcriber, "transcribe_with_diarization"):
                return state.transcriber.transcribe_with_diarization(file_path)
            return state.transcriber.transcribe_audio_file(file_path)

        with concurrent.futures.ThreadPoolExecutor() as ex:
            success, result = await loop.run_in_executor(ex, _tr)
        if success:
            return {
                "transcription": result,
                "filename": file.filename,
                "success": True,
                "diarization": True,
            }
        raise HTTPException(status_code=400, detail=result)
    except HTTPException:
        raise
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


@router.post("/youtube")
async def transcribe_youtube(request: YouTubeTranscribeRequest):
    require_service("diarization")  # FEATURE-FLAG
    require_service("stt")  # FEATURE-FLAG
    if not state.transcriber:
        raise HTTPException(status_code=503, detail="Transcriber не доступен")
    try:
        loop = asyncio.get_event_loop()
        # SSRF: только http(s) и публичные адреса (DNS-резолв блокирующий — в executor)
        try:
            safe_url = await loop.run_in_executor(
                None, validate_public_http_url, request.url
            )
        except UnsafeUrlError as exc:
            logger.warning("YouTube URL отклонён: %s", exc)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        with concurrent.futures.ThreadPoolExecutor() as ex:
            success, result = await loop.run_in_executor(
                ex, state.transcriber.transcribe_youtube, safe_url
            )
        if success:
            return {
                "transcription": result,
                "url": request.url,
                "success": True,
                "timestamp": datetime.now().isoformat(),
                "diarization": True,
            }
        raise HTTPException(status_code=400, detail=result)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка операции")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e
