"""In-memory менеджер задач загрузки моделей."""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.services.model_downloads.apply import apply_model
from backend.services.model_downloads.catalog import get_catalog_entry
from backend.services.model_downloads.hf_client import (
    DownloadCancelled,
    download_file,
    download_snapshot,
)
from backend.services.model_downloads.http_client import download_url
from backend.services.model_downloads.paths import (
    MODEL_TYPES,
    resolve_target_dir,
    resolve_target_file,
)
from backend.services.model_downloads.hf_ref import normalize_hf_ref
from backend.services.model_downloads.avatar import fetch_org_avatar_url, write_avatar_url_sidecar
from backend.services.model_downloads.provenance import write_provenance
from backend.services.model_downloads.token_store import get_hf_token
from backend.settings.logging import get_logger

logger = get_logger(__name__)

JobStatus = str  # queued|running|completed|failed|cancelled|completed_needs_restart


@dataclass
class DownloadJob:
    id: str
    status: JobStatus = "queued"
    model_type: str = ""
    display_name: str = ""
    catalog_id: Optional[str] = None
    progress_pct: float = 0.0
    bytes_done: int = 0
    bytes_total: int = 0
    speed_bps: float = 0.0
    eta_seconds: Optional[float] = None
    message: str = ""
    error: Optional[str] = None
    local_path: Optional[str] = None
    apply_result: Optional[Dict[str, Any]] = None
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    updated_at: float = field(default_factory=time.time)
    cancel_requested: bool = False

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d.pop("cancel_requested", None)
        return d


class DownloadJobManager:
    def __init__(self) -> None:
        self._jobs: Dict[str, DownloadJob] = {}
        self._lock = threading.Lock()
        self._max_jobs = 50

    def list_jobs(self) -> List[Dict[str, Any]]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
            return [j.to_dict() for j in jobs[:40]]

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.to_dict() if job else None

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False
            if job.status in {"completed", "failed", "cancelled", "completed_needs_restart"}:
                return False
            job.cancel_requested = True
            job.message = "Отмена запрошена…"
            job.updated_at = time.time()
            return True

    def start_from_catalog(self, catalog_id: str, apply: Optional[bool] = True) -> DownloadJob:
        entry = get_catalog_entry(catalog_id)
        if not entry:
            raise ValueError(f"Каталог: id не найден: {catalog_id}")
        src = entry.get("source") or {}
        return self.start_custom(
            model_type=str(entry["type"]),
            repo_id=src.get("repo_id"),
            url=src.get("url"),
            filename=src.get("filename"),
            revision=src.get("revision"),
            local_name=entry.get("local_name"),
            target_subdir=entry.get("target_subdir"),
            allow_patterns=src.get("allow_patterns"),
            apply_action=(entry.get("apply") if apply is not False else "none"),
            display_name=entry.get("display_name"),
            catalog_id=entry.get("id"),
            stt_size=entry.get("stt_size"),
            gated=bool(entry.get("gated")),
        )

    def start_custom(
        self,
        *,
        model_type: str,
        repo_id: Optional[str] = None,
        url: Optional[str] = None,
        filename: Optional[str] = None,
        revision: Optional[str] = None,
        local_name: Optional[str] = None,
        target_subdir: Optional[str] = None,
        allow_patterns: Optional[List[str]] = None,
        apply_action: Optional[str] = "none",
        display_name: Optional[str] = None,
        catalog_id: Optional[str] = None,
        stt_size: Optional[str] = None,
        gated: bool = False,
    ) -> DownloadJob:
        if model_type not in MODEL_TYPES:
            raise ValueError(f"Неизвестный type: {model_type}")
        norm = normalize_hf_ref(repo_id=repo_id, filename=filename, url=url)
        repo_id = norm.get("repo_id")
        filename = norm.get("filename") or filename
        url = norm.get("url")
        if not repo_id and not url:
            raise ValueError("Нужен repo_id или url")
        if gated and not get_hf_token():
            raise ValueError("Модель gated — укажите HF_TOKEN в настройках загрузки")
        if model_type in {"llm_gguf", "comfyui"} and repo_id and not filename and not url:
            raise ValueError(
                "Для GGUF/ComfyUI укажите файл, например: "
                "hf://org/repo/model.gguf или org/repo/model.gguf"
            )
        if not local_name and filename:
            local_name = filename.split("/")[-1]

        job = DownloadJob(
            id=str(uuid.uuid4()),
            status="queued",
            model_type=model_type,
            display_name=display_name or local_name or repo_id or url or model_type,
            catalog_id=catalog_id,
            message="В очереди",
        )
        with self._lock:
            self._jobs[job.id] = job
            self._trim_locked()

        spec = {
            "model_type": model_type,
            "repo_id": repo_id,
            "url": url,
            "filename": filename,
            "revision": revision,
            "local_name": local_name,
            "target_subdir": target_subdir,
            "allow_patterns": allow_patterns,
            "apply_action": apply_action or "none",
            "stt_size": stt_size,
        }
        threading.Thread(
            target=self._run_job_thread,
            args=(job.id, spec),
            name=f"model-dl-{job.id[:8]}",
            daemon=True,
        ).start()
        return job

    def _trim_locked(self) -> None:
        if len(self._jobs) <= self._max_jobs:
            return
        finished = [
            j
            for j in self._jobs.values()
            if j.status in {"completed", "failed", "cancelled", "completed_needs_restart"}
        ]
        finished.sort(key=lambda j: j.updated_at)
        for j in finished[: max(0, len(self._jobs) - self._max_jobs)]:
            self._jobs.pop(j.id, None)

    def _update(self, job_id: str, **kwargs: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            for k, v in kwargs.items():
                setattr(job, k, v)
            job.updated_at = time.time()

    def _is_cancelled(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            return bool(job and job.cancel_requested)

    def _run_job_thread(self, job_id: str, spec: Dict[str, Any]) -> None:
        try:
            asyncio.run(self._run_job(job_id, spec))
        except Exception as exc:
            logger.exception("download job crashed %s", job_id)
            self._update(job_id, status="failed", error=str(exc), message="Ошибка")

    async def _run_job(self, job_id: str, spec: Dict[str, Any]) -> None:
        started = time.time()
        self._update(
            job_id,
            status="running",
            message="Загрузка…",
            progress_pct=0.0,
            started_at=started,
            speed_bps=0.0,
            eta_seconds=None,
        )

        def progress(pct: float, done: int, total: int, msg: str) -> None:
            elapsed = max(time.time() - started, 1e-3)
            speed = float(done) / elapsed if done > 0 else 0.0
            eta = None
            if speed > 0 and total > done:
                eta = float(total - done) / speed
            self._update(
                job_id,
                progress_pct=round(float(pct), 2),
                bytes_done=int(done),
                bytes_total=int(total),
                speed_bps=round(speed, 1),
                eta_seconds=round(eta, 1) if eta is not None else None,
                message=msg or "Загрузка…",
            )

        cancel = lambda: self._is_cancelled(job_id)  # noqa: E731
        model_type = spec["model_type"]
        local_name = spec.get("local_name")
        filename = spec.get("filename")
        token = get_hf_token()
        local_path: Optional[Path] = None

        try:
            if spec.get("url"):
                dest = resolve_target_file(
                    model_type,
                    filename or "model.pt",
                    local_name=local_name,
                    target_subdir=spec.get("target_subdir"),
                )
                local_path = download_url(
                    url=str(spec["url"]),
                    dest_path=dest,
                    progress=progress,
                    is_cancelled=cancel,
                )
            elif model_type == "llm_gguf" or (
                model_type == "comfyui" and filename
            ):
                fname = filename or (local_name if local_name and "." in str(local_name) else None)
                if not fname:
                    raise ValueError("Для llm_gguf/comfyui нужен filename")
                dest = resolve_target_file(
                    model_type,
                    fname,
                    local_name=local_name,
                    target_subdir=spec.get("target_subdir"),
                )
                local_path = download_file(
                    repo_id=str(spec["repo_id"]),
                    filename=str(fname),
                    dest_path=dest,
                    token=token,
                    revision=spec.get("revision"),
                    progress=progress,
                    is_cancelled=cancel,
                )
            else:
                # snapshot into target dir
                dest_dir = resolve_target_dir(
                    model_type,
                    local_name=local_name,
                    target_subdir=spec.get("target_subdir"),
                )
                # STT: download into whisperx_models root (HF cache layout)
                if model_type == "stt":
                    dest_dir = resolve_target_dir("stt")
                if model_type == "diarization":
                    dest_dir = resolve_target_dir("diarization")
                if model_type == "ocr":
                    dest_dir = resolve_target_dir("ocr")
                if model_type == "omnivoice":
                    dest_dir = resolve_target_dir("omnivoice")

                local_path = download_snapshot(
                    repo_id=str(spec["repo_id"]),
                    dest_dir=dest_dir,
                    token=token,
                    revision=spec.get("revision"),
                    allow_patterns=spec.get("allow_patterns"),
                    progress=progress,
                    is_cancelled=cancel,
                    use_hf_cache_layout=(model_type == "stt"),
                )

            if cancel():
                raise DownloadCancelled()

            if local_path:
                with self._lock:
                    job = self._jobs.get(job_id)
                    dn = job.display_name if job else None
                    cid = job.catalog_id if job else None
                avatar_url = None
                if spec.get("repo_id"):
                    try:
                        avatar_url = fetch_org_avatar_url(str(spec["repo_id"]))
                    except Exception:
                        avatar_url = None
                write_provenance(
                    Path(local_path),
                    {
                        "model_type": model_type,
                        "repo_id": spec.get("repo_id"),
                        "url": spec.get("url"),
                        "filename": filename or spec.get("filename"),
                        "revision": spec.get("revision"),
                        "local_name": local_name,
                        "target_subdir": spec.get("target_subdir"),
                        "allow_patterns": spec.get("allow_patterns"),
                        "apply_action": spec.get("apply_action") or "none",
                        "stt_size": spec.get("stt_size"),
                        "display_name": dn,
                        "catalog_id": cid,
                        "avatar_url": avatar_url,
                    },
                )
                if avatar_url:
                    write_avatar_url_sidecar(Path(local_path), avatar_url)

            self._update(
                job_id,
                progress_pct=100.0,
                local_path=str(local_path) if local_path else None,
                message="Применение…",
            )

            apply_result = await apply_model(
                apply_action=str(spec.get("apply_action") or "none"),
                model_type=model_type,
                local_name=local_name,
                stt_size=spec.get("stt_size"),
                rag_kind="embedding" if model_type == "rag_embed" else (
                    "reranker" if model_type == "rag_rerank" else None
                ),
            )
            needs_restart = bool(apply_result.get("needs_restart"))
            status: JobStatus = (
                "completed_needs_restart" if needs_restart else "completed"
            )
            self._update(
                job_id,
                status=status,
                apply_result=apply_result,
                message=apply_result.get("message") or "Готово",
                progress_pct=100.0,
            )
        except DownloadCancelled:
            self._update(job_id, status="cancelled", message="Отменено", error="cancelled")
        except Exception as exc:
            logger.exception("download job failed %s", job_id)
            self._update(job_id, status="failed", error=str(exc), message="Ошибка загрузки")


_manager: Optional[DownloadJobManager] = None
_manager_lock = threading.Lock()


def get_job_manager() -> DownloadJobManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = DownloadJobManager()
        return _manager
