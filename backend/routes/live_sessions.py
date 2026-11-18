"""REST API «Живых сессий» — dual-track запись + стенограмма + бриф."""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from backend.auth.jwt_handler import get_current_user
from backend.database.postgresql.live_session_models import (
    LiveCollectionCreate,
    LiveCollectionUpdate,
    LiveSessionBriefUpdate,
    LiveSessionCreate,
    LiveSessionSpeakerRename,
    LiveSessionUpdate,
)
from backend.services import live_sessions as svc
from backend.database import init_db
from backend.settings.logging import get_logger
from backend.settings.service_toggles import require_service
from backend.utils.http_errors import public_error_detail

logger = get_logger(__name__)

router = APIRouter(
    prefix="/api/live-sessions",
    tags=["live-sessions"],
    dependencies=[Depends(get_current_user)],
)


def _user_id(user: dict) -> str:
    return str(
        (user or {}).get("user_id")
        or (user or {}).get("username")
        or ""
    ).strip()


def _repo():
    repo = getattr(init_db, "live_session_repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Хранилище сессий недоступно")
    return repo


@router.post("")
async def create_live_session(
    body: LiveSessionCreate,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    require_service("stt")
    try:
        return await svc.create_session(_user_id(current_user), body)
    except Exception as e:
        logger.exception("create live session failed")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.get("")
async def list_live_sessions(
    current_user: Annotated[dict, Depends(get_current_user)],
    trashed: bool = False,
    collection_id: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 50,
    before: Optional[str] = None,
):
    repo = _repo()
    uid = _user_id(current_user)
    if q and q.strip():
        items = await repo.search_sessions(uid, q.strip(), limit=limit)
    else:
        items = await repo.list_sessions(
            uid,
            trashed=trashed,
            collection_id=collection_id,
            limit=limit,
            before=before,
        )
    return {"items": items, "count": len(items)}


@router.get("/prefs")
async def get_live_prefs(current_user: Annotated[dict, Depends(get_current_user)]):
    return await _repo().get_prefs(_user_id(current_user))


@router.put("/prefs")
async def put_live_prefs(
    body: dict,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    return await _repo().set_prefs(_user_id(current_user), body or {})


@router.get("/collections")
async def list_collections(current_user: Annotated[dict, Depends(get_current_user)]):
    return {"items": await _repo().list_collections(_user_id(current_user))}


@router.post("/collections")
async def create_collection(
    body: LiveCollectionCreate,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    return await _repo().create_collection(_user_id(current_user), body)


@router.patch("/collections/{collection_id}")
async def update_collection(
    collection_id: str,
    body: LiveCollectionUpdate,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    item = await _repo().update_collection(
        _user_id(current_user), collection_id, body
    )
    if not item:
        raise HTTPException(status_code=404, detail="Коллекция не найдена")
    return item


@router.delete("/collections/{collection_id}")
async def delete_collection(
    collection_id: str,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    ok = await _repo().delete_collection(_user_id(current_user), collection_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Коллекция не найдена")
    return {"success": True}


@router.get("/{session_id}")
async def get_live_session(
    session_id: str,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    item = await _repo().get_session(_user_id(current_user), session_id, include_trashed=True)
    if not item:
        raise HTTPException(status_code=404, detail="Сессия не найдена")
    return item


@router.patch("/{session_id}")
async def update_live_session(
    session_id: str,
    body: LiveSessionUpdate,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    item = await _repo().update_session(_user_id(current_user), session_id, body)
    if not item:
        raise HTTPException(status_code=404, detail="Сессия не найдена")
    return item


@router.post("/{session_id}/start")
async def start_live_session(
    session_id: str,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    require_service("stt")
    item = await svc.start_session(_user_id(current_user), session_id)
    if not item:
        raise HTTPException(status_code=404, detail="Сессия не найдена")
    return item


@router.post("/{session_id}/pause")
async def pause_live_session(
    session_id: str,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    item = await svc.pause_session(_user_id(current_user), session_id)
    if not item:
        raise HTTPException(status_code=404, detail="Сессия не найдена")
    return item


@router.post("/{session_id}/resume")
async def resume_live_session(
    session_id: str,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    item = await svc.resume_session(_user_id(current_user), session_id)
    if not item:
        raise HTTPException(status_code=404, detail="Сессия не найдена")
    return item


@router.post("/{session_id}/chunks")
async def upload_live_chunk(
    session_id: str,
    current_user: Annotated[dict, Depends(get_current_user)],
    file: Annotated[UploadFile, File(...)],
    track: Annotated[str, Form()] = "local",
    offset_ms: Annotated[int, Form()] = 0,
    language: Annotated[Optional[str], Form()] = None,
    jargon: Annotated[Optional[str], Form()] = None,
):
    require_service("stt")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Пустой аудиофрагмент")
    jargon_list = None
    if jargon is not None:
        jargon_list = [x.strip() for x in jargon.replace(";", ",").split(",") if x.strip()]
    try:
        result = await svc.ingest_audio_chunk(
            _user_id(current_user),
            session_id,
            track=track if track in ("local", "remote", "mixed") else "local",
            offset_ms=int(offset_ms or 0),
            audio_bytes=data,
            filename=file.filename or "chunk.wav",
            language=language or "auto",
            jargon=jargon_list,
        )
        return {"success": True, **result}
    except LookupError:
        raise HTTPException(status_code=404, detail="Сессия не найдена") from None
    except RuntimeError as e:
        code = str(e)
        if code == "session_not_recording":
            raise HTTPException(status_code=409, detail="Сессия не в режиме записи") from e
        raise HTTPException(status_code=503, detail=public_error_detail(e)) from e
    except Exception as e:
        logger.exception("chunk ingest failed")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.post("/{session_id}/finalize")
async def finalize_live_session(
    session_id: str,
    current_user: Annotated[dict, Depends(get_current_user)],
    local_audio: Annotated[Optional[UploadFile], File()] = None,
    remote_audio: Annotated[Optional[UploadFile], File()] = None,
    mixed_audio: Annotated[Optional[UploadFile], File()] = None,
    duration_ms: Annotated[int, Form()] = 0,
    generate_brief: Annotated[bool, Form()] = True,
):
    require_service("stt")
    # Диаризация желательна на финализации, но не блокируем стоп, если сервис выключен.

    async def _read(up: Optional[UploadFile]):
        if not up:
            return None
        data = await up.read()
        if not data:
            return None
        return data, up.content_type or "application/octet-stream"

    try:
        result = await svc.finalize_session(
            _user_id(current_user),
            session_id,
            local_audio=await _read(local_audio),
            remote_audio=await _read(remote_audio),
            mixed_audio=await _read(mixed_audio),
            duration_ms=duration_ms,
            generate_brief=generate_brief,
        )
        return result
    except LookupError:
        raise HTTPException(status_code=404, detail="Сессия не найдена") from None
    except Exception as e:
        logger.exception("finalize failed")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.post("/{session_id}/speakers")
async def rename_live_speakers(
    session_id: str,
    body: LiveSessionSpeakerRename,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    item = await svc.rename_speakers(
        _user_id(current_user), session_id, body.mapping or {}
    )
    if not item:
        raise HTTPException(status_code=404, detail="Сессия не найдена")
    return item


@router.post("/{session_id}/brief")
async def update_brief(
    session_id: str,
    body: LiveSessionBriefUpdate,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    uid = _user_id(current_user)
    repo = _repo()
    if body.user_notes is not None or (body.brief_markdown is not None and not body.regenerate):
        from backend.database.postgresql.live_session_models import LiveSessionUpdate

        item = await repo.update_session(
            uid,
            session_id,
            LiveSessionUpdate(
                user_notes=body.user_notes,
                # brief only via regenerate or explicit field below
            ),
        )
        if body.brief_markdown is not None and not body.regenerate:
            item = await repo.set_status(
                uid,
                session_id,
                (item or {}).get("status") or "ready",
                extra={"brief_markdown": body.brief_markdown, "brief_status": "ready"},
            )
        if not body.regenerate:
            if not item:
                raise HTTPException(status_code=404, detail="Сессия не найдена")
            return item
    try:
        return await svc.regenerate_brief(
            uid, session_id, instruction=body.instruction
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="Сессия не найдена") from None
    except Exception as e:
        logger.exception("brief update failed")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.post("/{session_id}/trash")
async def trash_session(
    session_id: str,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    ok = await _repo().soft_delete(_user_id(current_user), session_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Сессия не найдена")
    return {"success": True}


@router.post("/{session_id}/restore")
async def restore_session(
    session_id: str,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    ok = await _repo().restore(_user_id(current_user), session_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Сессия не найдена")
    return {"success": True}


@router.delete("/{session_id}")
async def delete_session(
    session_id: str,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    item = await _repo().hard_delete(_user_id(current_user), session_id)
    if not item:
        raise HTTPException(status_code=404, detail="Сессия не найдена")
    # best-effort cleanup MinIO
    try:
        from backend.app_state import minio_client

        if minio_client:
            for key in (
                item.get("local_audio_key"),
                item.get("remote_audio_key"),
                item.get("mixed_audio_key"),
            ):
                if key:
                    try:
                        minio_client.delete_file(key)
                    except Exception:
                        logger.exception("minio delete failed for %s", key)
    except Exception:
        logger.exception("minio cleanup failed")
    return {"success": True}
