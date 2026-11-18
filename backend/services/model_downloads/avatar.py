"""Аватары организаций Hugging Face для отображения моделей в UI."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, Optional, Tuple
from urllib.parse import quote

_CACHE: Dict[str, Tuple[float, Optional[str]]] = {}
_CACHE_TTL_SEC = 6 * 3600


def org_from_repo_id(repo_id: Optional[str]) -> Optional[str]:
    rid = (repo_id or "").strip()
    if "/" not in rid:
        return None
    org = rid.split("/", 1)[0].strip()
    return org or None


def fetch_org_avatar_url(repo_id: Optional[str], *, timeout: float = 8.0) -> Optional[str]:
    """URL аватара org/user владельца репо (как на сайте HF)."""
    org = org_from_repo_id(repo_id)
    if not org:
        return None
    now = time.time()
    cached = _CACHE.get(org)
    if cached and (now - cached[0]) < _CACHE_TTL_SEC:
        return cached[1]
    url = f"https://huggingface.co/api/organizations/{quote(org)}/avatar"
    avatar: Optional[str] = None
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "AstraChat/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        if isinstance(data, dict):
            avatar = (data.get("avatarUrl") or data.get("avatar_url") or "").strip() or None
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, TypeError, ValueError):
        # fallback: user avatar endpoint
        try:
            uurl = f"https://huggingface.co/api/users/{quote(org)}/avatar"
            req = urllib.request.Request(uurl, headers={"Accept": "application/json", "User-Agent": "AstraChat/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            if isinstance(data, dict):
                avatar = (data.get("avatarUrl") or data.get("avatar_url") or "").strip() or None
        except Exception:
            avatar = None
    _CACHE[org] = (now, avatar)
    return avatar


def avatar_sidecar_path_for_file(model_file: Path) -> Path:
    return model_file.with_name(model_file.name + ".astra-avatar.url")


def avatar_sidecar_path_for_dir(model_dir: Path) -> Path:
    return model_dir / ".astra-avatar.url"


def write_avatar_url_sidecar(local_path: Path, avatar_url: str) -> Optional[Path]:
    if not avatar_url:
        return None
    path = Path(local_path)
    try:
        if path.is_file() or path.suffix:
            dest = avatar_sidecar_path_for_file(path)
        else:
            dest = avatar_sidecar_path_for_dir(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(avatar_url.strip() + "\n", encoding="utf-8")
        return dest
    except OSError:
        return None


def read_avatar_url_near(path: Path) -> Optional[str]:
    candidates = []
    if path.is_file() or path.suffix:
        candidates.append(avatar_sidecar_path_for_file(path))
    if path.is_dir() or not path.suffix:
        candidates.append(avatar_sidecar_path_for_dir(path))
    if path.is_file():
        candidates.append(avatar_sidecar_path_for_dir(path.parent))
    for c in candidates:
        try:
            if c.is_file():
                url = c.read_text(encoding="utf-8").strip()
                if url.startswith("http"):
                    return url
        except OSError:
            continue
    return None
