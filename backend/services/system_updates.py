"""Проверка и применение обновлений AstraStudio (admin)."""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from backend.settings.logging import get_logger

logger = get_logger(__name__)

_DOCKER_SOCK = Path("/var/run/docker.sock")
_UPDATE_HELPER_IMAGE = os.getenv("ASTRA_UPDATE_HELPER_IMAGE", "docker:27-cli")
_job_lock = threading.Lock()
_job: Dict[str, Any] = {
    "running": False,
    "phase": "idle",
    "message": "",
    "ok": True,
    "started_at": None,
    "finished_at": None,
    "log_tail": "",
}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def updates_enabled() -> bool:
    return _env_bool("ASTRA_UPDATES_ENABLED", False)


def read_local_version() -> str:
    candidates = [
        Path(os.getenv("ASTRA_HOST_ROOT", "")) / "VERSION" if os.getenv("ASTRA_HOST_ROOT") else None,
        Path("/astra-host/VERSION"),
        Path(__file__).resolve().parents[2] / "VERSION",
        Path.cwd() / "VERSION",
    ]
    for p in candidates:
        if p and p.is_file():
            return p.read_text(encoding="utf-8").strip()
    return os.getenv("ASTRA_APP_VERSION", "unknown")


def host_root() -> Optional[Path]:
    raw = os.getenv("ASTRA_HOST_ROOT", "").strip()
    if raw:
        p = Path(raw)
        if p.is_dir():
            return p
    p2 = Path("/astra-host")
    return p2 if p2.is_dir() else None


def host_root_on_host() -> str:
    return (
        os.getenv("ASTRA_HOST_ROOT_ON_HOST", "").strip()
        or os.getenv("ASTRA_HOST_ROOT", "").strip()
        or ""
    )


def status_file() -> Optional[Path]:
    root = host_root()
    if not root:
        return None
    return root / "data" / "update-status.json"


def read_status_file() -> Optional[Dict[str, Any]]:
    path = status_file()
    if not path or not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def get_job_snapshot() -> Dict[str, Any]:
    with _job_lock:
        snap = dict(_job)
    file_status = read_status_file()
    if file_status:
        snap["file_status"] = file_status
        if snap.get("running") and file_status.get("phase"):
            snap["phase"] = file_status.get("phase") or snap["phase"]
            snap["message"] = file_status.get("message") or snap["message"]
            if file_status.get("phase") == "done":
                snap["running"] = False
                snap["ok"] = True
            elif file_status.get("phase") == "failed":
                snap["running"] = False
                snap["ok"] = False
    snap["enabled"] = updates_enabled()
    snap["local_version"] = read_local_version()
    snap["image_tag"] = os.getenv("ASTRA_VERSION", "latest")
    return snap


async def fetch_remote_version() -> Dict[str, Any]:
    repo = os.getenv("ASTRA_GITHUB_REPO", "NeKonnnn/Astra-Studio")
    ref = os.getenv("ASTRA_GITHUB_REF", "main")
    local = read_local_version()
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "AstraStudio-Updater",
    }
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    remote_version = None
    latest_release = None
    commit_sha = None
    html_url = f"https://github.com/{repo}"

    async with httpx.AsyncClient(timeout=20.0, headers=headers) as client:
        # Prefer VERSION file on default branch
        ver_url = f"https://raw.githubusercontent.com/{repo}/{ref}/VERSION"
        try:
            r = await client.get(ver_url)
            if r.status_code == 200 and r.text.strip():
                remote_version = r.text.strip()
        except Exception as exc:
            logger.warning("VERSION fetch failed: %s", exc)

        try:
            r = await client.get(f"https://api.github.com/repos/{repo}/releases/latest")
            if r.status_code == 200:
                data = r.json()
                latest_release = data.get("tag_name")
                html_url = data.get("html_url") or html_url
                if not remote_version and latest_release:
                    remote_version = str(latest_release).lstrip("v")
        except Exception as exc:
            logger.warning("releases/latest failed: %s", exc)

        try:
            r = await client.get(f"https://api.github.com/repos/{repo}/commits/{ref}")
            if r.status_code == 200:
                commit_sha = (r.json().get("sha") or "")[:12]
        except Exception as exc:
            logger.warning("commits fetch failed: %s", exc)

    update_available = bool(
        remote_version and local and remote_version != "unknown" and remote_version != local
    )
    return {
        "local_version": local,
        "remote_version": remote_version,
        "latest_release": latest_release,
        "commit_sha": commit_sha,
        "update_available": update_available,
        "repo": repo,
        "ref": ref,
        "html_url": html_url,
        "image_tag": os.getenv("ASTRA_VERSION", "latest"),
        "enabled": updates_enabled(),
        "can_apply": _can_apply(),
    }


def _can_apply() -> bool:
    if not updates_enabled():
        return False
    root = host_root()
    if not root:
        return False
    script = root / "scripts" / "update.sh"
    if not script.is_file():
        return False
    # Host mode (backend not in docker / script runnable locally)
    if not _env_bool("DOCKER_ENV", True):
        return True
    # In-container: need docker.sock + host path for helper
    return _DOCKER_SOCK.exists() and bool(host_root_on_host())


def _set_job(**kwargs: Any) -> None:
    with _job_lock:
        _job.update(kwargs)


async def _docker_api(method: str, path: str, json_body: Any = None) -> httpx.Response:
    transport = httpx.AsyncHTTPTransport(uds=str(_DOCKER_SOCK))
    async with httpx.AsyncClient(
        transport=transport, base_url="http://localhost", timeout=None
    ) as client:
        return await client.request(method, path, json=json_body)


async def _apply_via_docker_helper() -> None:
    host_path = host_root_on_host()
    if not host_path:
        raise RuntimeError("ASTRA_HOST_ROOT_ON_HOST is not set")

    env = [
        f"ASTRA_VERSION={os.getenv('ASTRA_VERSION', 'latest')}",
        f"ASTRA_LLM_VARIANT={os.getenv('ASTRA_LLM_VARIANT', 'cuda')}",
        f"ASTRA_PROFILE={os.getenv('ASTRA_PROFILE', 'full')}",
        f"ASTRA_REPO_REF={os.getenv('ASTRA_GITHUB_REF', 'main')}",
    ]
    # Ensure compose plugin available in helper
    cmd = [
        "sh",
        "-c",
        "apk add --no-cache git docker-cli-compose >/dev/null 2>&1 || true; "
        f"sh '{host_path}/scripts/update.sh'",
    ]
    create_body = {
        "Image": _UPDATE_HELPER_IMAGE,
        "Cmd": cmd,
        "Env": env,
        "WorkingDir": host_path,
        "HostConfig": {
            "Binds": [
                f"{host_path}:{host_path}",
                "/var/run/docker.sock:/var/run/docker.sock",
            ],
            "AutoRemove": True,
        },
    }

    _set_job(phase="helper", message="Pulling update helper image")
    pull = await _docker_api(
        "POST",
        f"/images/create?fromImage={_UPDATE_HELPER_IMAGE.split(':')[0]}&tag={_UPDATE_HELPER_IMAGE.split(':')[-1]}",
    )
    # pull endpoint streams; accept 200
    if pull.status_code not in (200, 201):
        logger.warning("helper image pull status=%s body=%s", pull.status_code, pull.text[:300])

    _set_job(phase="create", message="Starting update helper container")
    created = await _docker_api("POST", "/containers/create?name=astra-updater", json_body=create_body)
    if created.status_code == 409:
        # remove stale
        await _docker_api("DELETE", "/containers/astra-updater?force=true")
        created = await _docker_api("POST", "/containers/create?name=astra-updater", json_body=create_body)
    if created.status_code not in (200, 201):
        raise RuntimeError(f"Cannot create updater container: {created.status_code} {created.text[:400]}")

    cid = created.json().get("Id")
    started = await _docker_api("POST", f"/containers/{cid}/start")
    if started.status_code not in (204, 200):
        raise RuntimeError(f"Cannot start updater: {started.status_code} {started.text[:400]}")

    _set_job(phase="running", message="Update is running (services may restart)")
    # Wait until container exits (AutoRemove may make inspect 404)
    for _ in range(360):  # ~30 min
        await asyncio.sleep(5)
        file_status = read_status_file()
        if file_status and file_status.get("phase") in {"done", "failed"}:
            ok = file_status.get("phase") == "done"
            _set_job(
                running=False,
                ok=ok,
                phase=file_status.get("phase"),
                message=file_status.get("message") or "",
                finished_at=time.time(),
            )
            return
        try:
            insp = await _docker_api("GET", f"/containers/{cid}/json")
            if insp.status_code == 200:
                state = insp.json().get("State") or {}
                if not state.get("Running"):
                    code = state.get("ExitCode", 1)
                    _set_job(
                        running=False,
                        ok=(code == 0),
                        phase="done" if code == 0 else "failed",
                        message=f"Updater exited with code {code}",
                        finished_at=time.time(),
                    )
                    return
            elif insp.status_code == 404:
                # AutoRemove finished
                file_status = read_status_file() or {}
                ok = file_status.get("phase") == "done"
                _set_job(
                    running=False,
                    ok=ok if file_status else True,
                    phase=file_status.get("phase") or "done",
                    message=file_status.get("message") or "Updater finished",
                    finished_at=time.time(),
                )
                return
        except Exception as exc:
            logger.warning("wait updater: %s", exc)

    _set_job(
        running=False,
        ok=False,
        phase="failed",
        message="Update timed out",
        finished_at=time.time(),
    )


def _apply_via_local_script() -> None:
    root = host_root()
    if not root:
        raise RuntimeError("ASTRA_HOST_ROOT missing")
    script = root / "scripts" / "update.sh"
    env = os.environ.copy()
    env.setdefault("ASTRA_VERSION", os.getenv("ASTRA_VERSION", "latest"))
    env.setdefault("ASTRA_LLM_VARIANT", os.getenv("ASTRA_LLM_VARIANT", "cuda"))
    env.setdefault("ASTRA_PROFILE", os.getenv("ASTRA_PROFILE", "full"))
    import subprocess

    _set_job(phase="running", message="Running scripts/update.sh on host")
    proc = subprocess.run(
        ["sh", str(script)],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    tail = ((proc.stdout or "") + "\n" + (proc.stderr or ""))[-4000:]
    file_status = read_status_file() or {}
    ok = proc.returncode == 0 and file_status.get("phase") != "failed"
    _set_job(
        running=False,
        ok=ok,
        phase=file_status.get("phase") or ("done" if ok else "failed"),
        message=file_status.get("message") or (f"exit={proc.returncode}"),
        log_tail=tail,
        finished_at=time.time(),
    )


def start_apply() -> Dict[str, Any]:
    if not updates_enabled():
        raise RuntimeError("Updates are disabled (ASTRA_UPDATES_ENABLED=false)")
    if not _can_apply():
        raise RuntimeError(
            "Cannot apply from UI: need ASTRA_HOST_ROOT (+ docker.sock in container) "
            "or run: sh scripts/update.sh"
        )
    with _job_lock:
        if _job.get("running"):
            return dict(_job)
        _job.update(
            {
                "running": True,
                "phase": "starting",
                "message": "Starting update",
                "ok": True,
                "started_at": time.time(),
                "finished_at": None,
                "log_tail": "",
            }
        )

    def _runner() -> None:
        try:
            if _DOCKER_SOCK.exists() and _env_bool("DOCKER_ENV", True) and host_root_on_host():
                asyncio.run(_apply_via_docker_helper())
            else:
                _apply_via_local_script()
        except Exception as exc:
            logger.exception("update failed")
            _set_job(
                running=False,
                ok=False,
                phase="failed",
                message=str(exc),
                finished_at=time.time(),
            )

    threading.Thread(target=_runner, name="astra-updater", daemon=True).start()
    return get_job_snapshot()
