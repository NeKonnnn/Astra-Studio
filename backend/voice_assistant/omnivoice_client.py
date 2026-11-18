"""Клиент OmniVoice microservice + профили голоса пользователя."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import httpx

from backend.settings.logging import get_logger

logger = get_logger(__name__)

# Локально (Windows) uploads рядом с репо; в Docker — /app/uploads
_default_profiles = Path(__file__).resolve().parents[2] / "uploads" / "voice_profiles"
PROFILES_ROOT = Path(os.getenv("VOICE_PROFILES_DIR", str(_default_profiles)))


def omnivoice_base_url() -> str:
    return (os.getenv("SVC_OMNIVOICE_URL") or "http://omnivoice-service:8000").rstrip("/")


def profile_dir(user_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(user_id))[:64]
    return PROFILES_ROOT / safe


def profile_paths(user_id: str) -> Tuple[Path, Path]:
    d = profile_dir(user_id)
    return d / "ref.wav", d / "meta.json"


async def fetch_omnivoice_health() -> Dict[str, Any]:
    base = omnivoice_base_url()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{base}/v1/health")
            data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            status = data.get("status") or ("up" if r.is_success else "down")
            err = data.get("error")
            available = r.is_success and status in {"healthy", "loading", "starting"}
            hint = None
            if not available:
                hint = str(err) if err else "OmniVoice ответил с ошибкой"
            elif status in {"loading", "starting"}:
                hint = "Модель загружается…"
            return {
                "available": available,
                "ready": bool(data.get("model_loaded")),
                "status": status,
                "error": err,
                "license_note": data.get("license_note"),
                "hint": hint,
            }
    except Exception as e:
        logger.warning("[VA] omnivoice health: %s", e)
        return {
            "available": False,
            "ready": False,
            "status": "unreachable",
            "error": str(e),
            "hint": "Запустите: docker compose up -d omnivoice-service (сеть astrachat-network, порт 8014)",
        }


async def synthesize_with_clone(
    text: str,
    ref_wav_path: Path,
    *,
    ref_text: Optional[str] = None,
) -> bytes:
    if not ref_wav_path.is_file():
        raise FileNotFoundError(f"ref missing: {ref_wav_path}")
    base = omnivoice_base_url()
    data = {"text": text}
    if ref_text:
        data["ref_text"] = ref_text
    async with httpx.AsyncClient(timeout=120.0) as client:
        with open(ref_wav_path, "rb") as f:
            files = {"ref_audio": ("ref.wav", f, "audio/wav")}
            r = await client.post(f"{base}/v1/synthesize", data=data, files=files)
        if not r.is_success:
            raise RuntimeError(f"OmniVoice synthesize HTTP {r.status_code}: {r.text[:300]}")
        return r.content
