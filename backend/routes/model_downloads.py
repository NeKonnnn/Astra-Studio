"""API загрузки моделей из Hugging Face / HTTP."""

from __future__ import annotations

from typing import Annotated, Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.auth.jwt_handler import get_current_user
from backend.services.model_downloads.catalog import list_catalog_with_installed
from backend.services.model_downloads.hf_client import list_repo_files
from backend.services.model_downloads.job_manager import get_job_manager
from backend.services.model_downloads.paths import MODEL_TYPES, is_installed, model_roots
from backend.services.model_downloads.token_store import (
    get_hf_token,
    mask_token,
    set_hf_token,
)
from backend.settings.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/model-downloads", tags=["model-downloads"])


def _require_admin(current_user: dict) -> None:
    if not current_user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")


class StartDownloadRequest(BaseModel):
    catalog_id: Optional[str] = None
    type: Optional[str] = None
    repo_id: Optional[str] = None
    url: Optional[str] = None
    filename: Optional[str] = None
    revision: Optional[str] = None
    local_name: Optional[str] = None
    target_subdir: Optional[str] = None
    allow_patterns: Optional[List[str]] = None
    apply: Optional[bool] = True
    apply_action: Optional[str] = None
    stt_size: Optional[str] = None


class HfTokenRequest(BaseModel):
    token: Optional[str] = Field(default=None, description="HF token; пусто = удалить")


@router.get("/catalog")
async def get_catalog(current_user: Annotated[dict, Depends(get_current_user)]):
    _require_admin(current_user)
    return {"models": list_catalog_with_installed()}


@router.get("/installed")
async def get_installed(current_user: Annotated[dict, Depends(get_current_user)]):
    _require_admin(current_user)
    roots = {k: str(v) for k, v in model_roots().items()}
    catalog = list_catalog_with_installed()
    return {
        "roots": roots,
        "installed": [m for m in catalog if m.get("installed")],
        "all": catalog,
    }


@router.get("/hf-files")
async def get_hf_files(
    current_user: Annotated[dict, Depends(get_current_user)],
    repo_id: str,
    revision: Optional[str] = None,
):
    _require_admin(current_user)
    rid = (repo_id or "").strip()
    if not rid or "/" not in rid:
        raise HTTPException(status_code=400, detail="repo_id вида org/name")
    try:
        files = list_repo_files(rid, token=get_hf_token(), revision=revision)
    except Exception as exc:
        logger.exception("hf-files failed")
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"repo_id": rid, "files": files, "gguf": [f for f in files if f.lower().endswith(".gguf")]}


@router.get("/avatar")
async def get_model_avatar(
    current_user: Annotated[dict, Depends(get_current_user)],
    repo_id: str,
):
    """Аватар org/user владельца HF-репо (кэш на backend)."""
    _require_admin(current_user)
    from backend.services.model_downloads.avatar import fetch_org_avatar_url

    rid = (repo_id or "").strip()
    if not rid or "/" not in rid:
        raise HTTPException(status_code=400, detail="repo_id вида org/name")
    url = fetch_org_avatar_url(rid)
    return {"repo_id": rid, "avatar_url": url}


@router.get("/hf-token")
async def get_token_status(current_user: Annotated[dict, Depends(get_current_user)]):
    _require_admin(current_user)
    return mask_token(get_hf_token())


@router.put("/hf-token")
async def put_hf_token(
    body: HfTokenRequest,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    _require_admin(current_user)
    set_hf_token(body.token)
    return mask_token(get_hf_token())


@router.get("")
async def list_jobs(current_user: Annotated[dict, Depends(get_current_user)]):
    _require_admin(current_user)
    return {"jobs": get_job_manager().list_jobs()}


@router.get("/{job_id}")
async def get_job(
    job_id: str,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    _require_admin(current_user)
    job = get_job_manager().get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/{job_id}/cancel")
async def cancel_job(
    job_id: str,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    _require_admin(current_user)
    ok = get_job_manager().cancel(job_id)
    if not ok:
        raise HTTPException(status_code=400, detail="Нельзя отменить эту задачу")
    return {"ok": True, "job_id": job_id}


@router.post("/start")
async def start_download(
    body: StartDownloadRequest,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    _require_admin(current_user)
    mgr = get_job_manager()
    try:
        if body.catalog_id:
            job = mgr.start_from_catalog(body.catalog_id, apply=body.apply)
        else:
            if not body.type:
                raise ValueError("type обязателен без catalog_id")
            if body.type not in MODEL_TYPES:
                raise ValueError(f"Неизвестный type: {body.type}")
            apply_action = body.apply_action
            if body.apply is False:
                apply_action = "none"
            elif not apply_action:
                defaults = {
                    "llm_gguf": "load_llm",
                    "llm_hf": "load_llm",
                    "rag_embed": "select_rag",
                    "rag_rerank": "select_rag",
                    "stt": "reload_stt",
                    "tts": "reload_tts",
                    "diarization": "reload_diarization",
                    "ocr": "reload_ocr",
                    "omnivoice": "reload_omnivoice",
                    "comfyui": "none",
                }
                apply_action = defaults.get(body.type, "none")
            job = mgr.start_custom(
                model_type=body.type,
                repo_id=body.repo_id,
                url=body.url,
                filename=body.filename,
                revision=body.revision,
                local_name=body.local_name,
                target_subdir=body.target_subdir,
                allow_patterns=body.allow_patterns,
                apply_action=apply_action,
                stt_size=body.stt_size or body.local_name,
                display_name=body.local_name or body.repo_id or body.url,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("start download failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return job.to_dict()


@router.post("/apply/{catalog_id}")
async def apply_installed(
    catalog_id: str,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Применить уже установленную модель из каталога без повторной загрузки."""
    _require_admin(current_user)
    from backend.services.model_downloads.apply import apply_model
    from backend.services.model_downloads.catalog import get_catalog_entry

    entry = get_catalog_entry(catalog_id)
    if not entry:
        raise HTTPException(status_code=404, detail="catalog id not found")
    src = entry.get("source") or {}
    if not is_installed(str(entry["type"]), entry.get("local_name"), filename=src.get("filename")):
        raise HTTPException(status_code=400, detail="Модель ещё не установлена на диске")
    result = await apply_model(
        apply_action=str(entry.get("apply") or "none"),
        model_type=str(entry["type"]),
        local_name=entry.get("local_name"),
        stt_size=entry.get("stt_size"),
        rag_kind="embedding" if entry.get("type") == "rag_embed" else (
            "reranker" if entry.get("type") == "rag_rerank" else None
        ),
    )
    return result


@router.delete("/catalog/{catalog_id}")
async def delete_catalog_model(
    catalog_id: str,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Удалить веса модели с диска (файл/папка + sidecar provenance)."""
    _require_admin(current_user)
    from backend.services.model_downloads.delete import delete_installed_model

    try:
        return delete_installed_model(catalog_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("delete model failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
