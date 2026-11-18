"""Нормализация пользовательского ввода HF (hf://, hf download, URL)."""

from __future__ import annotations

import re
from typing import Dict, Optional
from urllib.parse import urlparse


def normalize_hf_ref(
    *,
    repo_id: Optional[str] = None,
    filename: Optional[str] = None,
    url: Optional[str] = None,
) -> Dict[str, Optional[str]]:
    """
    Возвращает {repo_id, filename, url}.

    Примеры:
      hf://unsloth/Qwen3.8-27B-GGUF/Qwen3.8-27B-UD-IQ4_XS.gguf
      hf download hf://unsloth/.../file.gguf
      unsloth/Qwen3.8-27B-GGUF/file.gguf
      https://huggingface.co/unsloth/.../resolve/main/file.gguf
    """
    out_repo = (repo_id or "").strip() or None
    out_file = (filename or "").strip() or None
    out_url = (url or "").strip() or None

    raw = out_repo or out_url
    if not raw:
        return {"repo_id": out_repo, "filename": out_file, "url": out_url}

    s = raw.strip().strip("\"'")
    s = re.sub(r"^(?:huggingface-cli|hf)\s+download\s+", "", s, flags=re.I).strip()

    if re.match(r"^https?://", s, flags=re.I):
        try:
            u = urlparse(s)
            parts = [p for p in u.path.split("/") if p]
            if "huggingface.co" in (u.hostname or "") and len(parts) >= 2:
                out_repo = f"{parts[0]}/{parts[1]}"
                for key in ("resolve", "blob"):
                    if key in parts:
                        i = parts.index(key)
                        if len(parts) > i + 2:
                            out_file = out_file or "/".join(parts[i + 2 :])
                        break
                else:
                    if len(parts) > 2:
                        out_file = out_file or "/".join(parts[2:])
                out_url = None
                return {"repo_id": out_repo, "filename": out_file, "url": out_url}
        except Exception:
            pass
        return {"repo_id": None, "filename": out_file, "url": s}

    s = re.sub(r"^hf://", "", s, flags=re.I)
    s = re.sub(r"^hf:", "", s, flags=re.I)
    s = re.sub(r"^(?:hf\.co|huggingface\.co)/", "", s, flags=re.I).strip()

    # org/repo file.gguf
    if " " in s and "/" in s.split(" ", 1)[0]:
        left, right = s.split(None, 1)
        segs = [p for p in left.split("/") if p]
        if len(segs) >= 2:
            out_repo = f"{segs[0]}/{segs[1]}"
            out_file = out_file or right.strip()
            return {"repo_id": out_repo, "filename": out_file, "url": None}

    # org/repo:quant
    if ":" in s:
        left, right = s.split(":", 1)
        segs = [p for p in left.split("/") if p]
        if len(segs) >= 2:
            out_repo = f"{segs[0]}/{segs[1]}"
            if "." in right:
                out_file = out_file or right
            return {"repo_id": out_repo, "filename": out_file, "url": None}

    segs = [p for p in s.split("/") if p]
    if len(segs) >= 2:
        out_repo = f"{segs[0]}/{segs[1]}"
        if len(segs) > 2:
            out_file = out_file or "/".join(segs[2:])
        return {"repo_id": out_repo, "filename": out_file, "url": None}

    return {"repo_id": out_repo, "filename": out_file, "url": out_url}
