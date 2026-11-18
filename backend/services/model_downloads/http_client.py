"""Стриминговая HTTP-загрузка файла (Silero и прямые URL)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from backend.services.model_downloads.hf_client import (
    CancelCb,
    DownloadCancelled,
    ProgressCb,
    _stream_to_file,
)


def download_url(
    *,
    url: str,
    dest_path: Path,
    progress: Optional[ProgressCb] = None,
    is_cancelled: Optional[CancelCb] = None,
    timeout: float = 3600.0,
) -> Path:
    return _stream_to_file(
        url=url,
        dest_path=dest_path,
        progress=progress,
        is_cancelled=is_cancelled,
        progress_label=dest_path.name,
        timeout=timeout,
    )
