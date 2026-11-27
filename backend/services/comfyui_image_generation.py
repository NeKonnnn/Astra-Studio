"""
Клиент HTTP API ComfyUI для постановки workflow в очередь и получения PNG/JPEG.

Отдельный процесс ComfyUI (--listen), бэкенд только дергает /prompt и /history.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import json
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from backend.settings.logging import get_logger

logger = get_logger(__name__)


class ComfyImageGenError(Exception):
    pass


def _format_comfy_execution_error(messages: Any) -> str:
    raw = str(messages)
    if "no kernel image is available for execution on the device" in raw:
        return (
            "GPU несовместим с PyTorch в контейнере ComfyUI (часто RTX 50xx / sm_120). "
            "Пересоберите comfyui: docker compose build comfyui && docker compose up -d comfyui "
            "(нужен образ PyTorch 2.7 + CUDA 12.8). Временно: COMFYUI_FORCE_CPU=1 в docker-compose."
        )
    if len(raw) > 1200:
        return raw[:1200] + "…"
    return raw


_NO_CHECKPOINTS_HINT = (
    "В ComfyUI нет checkpoint-моделей (список пуст). "
    "Скачайте .safetensors в models/comfyui/checkpoints/ на хосте "
    "(в контейнере: /app/ComfyUI/models/checkpoints/). "
    "Рекомендуется Flux.1 Schnell FP8: Comfy-Org/flux1-schnell → flux1-schnell-fp8.safetensors (~17 ГБ). "
    "Альтернатива: SD1.5 v1-5-pruned-emaonly.safetensors + workflow sd15_txt2img_api.json."
)


async def upload_image_to_comfyui(
    comfyui_base_url: str,
    image_bytes: bytes,
    filename: str,
    *,
    subfolder: str = "",
    image_type: str = "input",
) -> str:
    """Загружает изображение в ComfyUI /upload/image, возвращает имя для LoadImage."""
    base = comfyui_base_url.rstrip("/")
    safe_name = (filename or "reference.png").replace("\\", "/").split("/")[-1] or "reference.png"
    timeout = httpx.Timeout(60.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        files = {"image": (safe_name, image_bytes)}
        data: Dict[str, str] = {"type": image_type}
        if subfolder:
            data["subfolder"] = subfolder
        r = await client.post(f"{base}/upload/image", files=files, data=data)
        if r.status_code >= 400:
            raise ComfyImageGenError(f"ComfyUI /upload/image: {r.status_code} {r.text[:500]}")
        body = r.json()
    if not isinstance(body, dict):
        raise ComfyImageGenError("ComfyUI /upload/image: неожиданный ответ")
    name = str(body.get("name") or safe_name)
    sub = str(body.get("subfolder") or "")
    if sub:
        return f"{sub}/{name}"
    return name


async def fetch_comfyui_checkpoint_names(comfyui_base_url: str) -> List[str]:
    """Список имён checkpoint из ComfyUI object_info (CheckpointLoaderSimple)."""
    base = comfyui_base_url.rstrip("/")
    timeout = httpx.Timeout(12.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.get(f"{base}/object_info/CheckpointLoaderSimple")
        r.raise_for_status()
        data = r.json()
    node = data.get("CheckpointLoaderSimple") if isinstance(data, dict) else None
    if not isinstance(node, dict):
        return []
    required = (node.get("input") or {}).get("required") or {}
    ckpt_spec = required.get("ckpt_name")
    if not isinstance(ckpt_spec, (list, tuple)) or not ckpt_spec:
        return []
    names = ckpt_spec[0]
    if not isinstance(names, list):
        return []
    return [str(n) for n in names if n]


def apply_checkpoint_to_workflow(
    workflow: Dict[str, Any],
    checkpoint_names: List[str],
    *,
    preferred: str = "",
    strict_preferred: bool = False,
) -> str:
    """
    Подставляет ckpt_name во все CheckpointLoaderSimple.
    Возвращает выбранное имя файла.
    """
    if not checkpoint_names:
        raise ComfyImageGenError(_NO_CHECKPOINTS_HINT)

    pref = (preferred or "").strip()
    if not pref:
        for node in workflow.values():
            if isinstance(node, dict) and node.get("class_type") == "CheckpointLoaderSimple":
                inputs = node.get("inputs") or {}
                pref = str(inputs.get("ckpt_name") or "").strip()
                if pref:
                    break

    if pref and pref in checkpoint_names:
        chosen = pref
    elif pref:
        chosen = next(
            (n for n in checkpoint_names if n == pref or n.endswith(f"/{pref}") or n.endswith(pref)),
            None,
        )
        if not chosen:
            hint = (
                f"Checkpoint «{pref}» не найден в ComfyUI. "
                f"Доступно: {', '.join(checkpoint_names[:8])}. "
                "Положите .safetensors в models/comfyui/checkpoints/ и перезапустите ComfyUI."
            )
            if strict_preferred:
                raise ComfyImageGenError(hint)
            chosen = checkpoint_names[0]
            logger.warning("%s Используем %r.", hint, chosen)
        elif chosen != pref:
            if strict_preferred:
                raise ComfyImageGenError(
                    f"Checkpoint «{pref}» не найден в ComfyUI (ближайший: {chosen}). "
                    f"Доступно: {', '.join(checkpoint_names[:8])}"
                )
            logger.warning(
                "Checkpoint %r недоступен в ComfyUI, используем %r (доступно: %s)",
                pref,
                chosen,
                ", ".join(checkpoint_names[:5]),
            )
    else:
        chosen = checkpoint_names[0]
        logger.info("checkpoint_name не задан, используем первый из ComfyUI: %r", chosen)

    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        if node.get("class_type") != "CheckpointLoaderSimple":
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            node["inputs"] = {"ckpt_name": chosen}
        else:
            inputs["ckpt_name"] = chosen

    return chosen


def resolve_workflow_file(workflow_path: str, project_root: Path) -> Path:
    p = Path(workflow_path)
    if p.is_absolute():
        return p
    return (project_root / p).resolve()


def load_workflow_template(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise ComfyImageGenError(f"Файл workflow не найден: {path}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ComfyImageGenError(f"Невалидный JSON workflow: {e}") from e
    if not isinstance(data, dict):
        raise ComfyImageGenError("Workflow должен быть JSON-объектом (формат API ComfyUI)")
    return data


def inject_workflow_inputs(
    workflow: Dict[str, Any],
    node_map: Dict[str, Tuple[str, str]],
    payload: Dict[str, Any],
) -> None:
    """
    node_map: logical_key -> (node_id_str, input_field_name)
    payload: logical_key -> value (пропускаем None и отсутствующие в node_map)
    """
    for key, raw_val in payload.items():
        if raw_val is None:
            continue
        ref = node_map.get(key)
        if not ref:
            continue
        node_id, field = ref
        node = workflow.get(str(node_id))
        if not isinstance(node, dict):
            raise ComfyImageGenError(f'В workflow нет ноды "{node_id}" (ключ "{key}")')
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            raise ComfyImageGenError(f'Нода "{node_id}" без объекта inputs')
        inputs[field] = raw_val


_VIDEO_EXTENSIONS = (".mp4", ".webm", ".mov", ".avi", ".mkv", ".gif")


def _media_spec(filename: str, subfolder: str, media_type: str) -> Dict[str, str]:
    return {
        "filename": filename,
        "subfolder": subfolder,
        "type": media_type,
    }


def _collect_output_images(history_entry: Dict[str, Any]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    outputs = history_entry.get("outputs") or {}
    if not isinstance(outputs, dict):
        return out
    for _nid, nod in outputs.items():
        if not isinstance(nod, dict):
            continue
        for im in nod.get("images") or []:
            if isinstance(im, dict) and im.get("filename"):
                fn = str(im["filename"])
                if fn.lower().endswith(_VIDEO_EXTENSIONS):
                    continue
                out.append(
                    _media_spec(
                        fn,
                        str(im.get("subfolder") or ""),
                        str(im.get("type") or "output"),
                    )
                )
    return out


def _collect_output_videos(history_entry: Dict[str, Any]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    outputs = history_entry.get("outputs") or {}
    if not isinstance(outputs, dict):
        return out
    for _nid, nod in outputs.items():
        if not isinstance(nod, dict):
            continue
        for key in ("videos", "gifs"):
            for item in nod.get(key) or []:
                if isinstance(item, dict) and item.get("filename"):
                    out.append(
                        _media_spec(
                            str(item["filename"]),
                            str(item.get("subfolder") or ""),
                            str(item.get("type") or "output"),
                        )
                    )
        for im in nod.get("images") or []:
            if isinstance(im, dict) and im.get("filename"):
                fn = str(im["filename"])
                if fn.lower().endswith(_VIDEO_EXTENSIONS):
                    out.append(
                        _media_spec(
                            fn,
                            str(im.get("subfolder") or ""),
                            str(im.get("type") or "output"),
                        )
                    )
    return out


async def _fetch_history_prompt(
    client: httpx.AsyncClient, base: str, prompt_id: str
) -> Optional[Dict[str, Any]]:
    url = f"{base}/history/{prompt_id}"
    r = await client.get(url)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict):
        return None
    # Ответ вида { prompt_id: [ { status, outputs, ... }, ... ] }
    block = data.get(prompt_id)
    if isinstance(block, list) and block:
        return block[-1]
    # Реже — сразу один объект
    if isinstance(block, dict):
        return block
    return None


def _mime_for_output_filename(filename: str) -> str:
    fn = filename.lower()
    if fn.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if fn.endswith(".webp"):
        return "image/webp"
    if fn.endswith(".gif"):
        return "image/gif"
    if fn.endswith(".webm"):
        return "video/webm"
    if fn.endswith((".mp4", ".m4v")):
        return "video/mp4"
    if fn.endswith(".mov"):
        return "video/quicktime"
    if fn.endswith(".avi"):
        return "video/x-msvideo"
    if fn.endswith(".mkv"):
        return "video/x-matroska"
    return "image/png"


async def _download_image_bytes(
    client: httpx.AsyncClient, base: str, spec: Dict[str, str]
) -> bytes:
    params = {
        "filename": spec["filename"],
        "type": spec.get("type") or "output",
        "subfolder": spec.get("subfolder") or "",
    }
    r = await client.get(f"{base}/view", params=params)
    r.raise_for_status()
    return r.content


async def generate_images_via_comfyui(
    *,
    comfyui_base_url: str,
    workflow: Dict[str, Any],
    timeout_sec: float,
    poll_interval_sec: float,
) -> List[Tuple[bytes, str]]:
    """
    Возвращает список (bytes, mime) для каждого выходного изображения из workflow.
    """
    base = comfyui_base_url.rstrip("/")
    client_id = str(uuid.uuid4())
    wf = copy.deepcopy(workflow)

    limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
    timeout = httpx.Timeout(timeout_sec, connect=30.0)

    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        post = await client.post(
            f"{base}/prompt",
            json={"prompt": wf, "client_id": client_id},
        )
        if post.status_code >= 400:
            try:
                detail = post.json()
            except Exception:
                detail = post.text
            raise ComfyImageGenError(f"ComfyUI /prompt: {post.status_code} {detail}")

        body = post.json()
        prompt_id = body.get("prompt_id")
        if not prompt_id:
            raise ComfyImageGenError(f"ComfyUI не вернул prompt_id: {body}")
        node_errors = body.get("node_errors") or {}
        if node_errors:
            raise ComfyImageGenError(f"Ошибки нод ComfyUI: {node_errors}")

        waited = 0.0
        history_entry: Optional[Dict[str, Any]] = None
        while waited < timeout_sec:
            await asyncio.sleep(poll_interval_sec)
            waited += poll_interval_sec
            history_entry = await _fetch_history_prompt(client, base, str(prompt_id))
            if not history_entry:
                continue
            status = history_entry.get("status")
            if status and status.get("completed") is False and status.get("status_str") == "error":
                messages = status.get("messages") or []
                raise ComfyImageGenError(
                    f"ComfyUI execution error: {_format_comfy_execution_error(messages)}"
                )
            imgs = _collect_output_images(history_entry)
            if imgs:
                results: List[Tuple[bytes, str]] = []
                for spec in imgs:
                    raw = await _download_image_bytes(client, base, spec)
                    results.append((raw, _mime_for_output_filename(spec["filename"])))
                return results

        raise ComfyImageGenError(
            f"Таймаут ожидания результата ComfyUI ({timeout_sec}s), prompt_id={prompt_id}"
        )


async def generate_videos_via_comfyui(
    *,
    comfyui_base_url: str,
    workflow: Dict[str, Any],
    timeout_sec: float,
    poll_interval_sec: float,
) -> List[Tuple[bytes, str]]:
    """Возвращает список (bytes, mime) для каждого выходного видео из workflow."""
    base = comfyui_base_url.rstrip("/")
    client_id = str(uuid.uuid4())
    wf = copy.deepcopy(workflow)

    limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
    timeout = httpx.Timeout(timeout_sec, connect=30.0)

    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        post = await client.post(
            f"{base}/prompt",
            json={"prompt": wf, "client_id": client_id},
        )
        if post.status_code >= 400:
            try:
                detail = post.json()
            except Exception:
                detail = post.text
            raise ComfyImageGenError(f"ComfyUI /prompt: {post.status_code} {detail}")

        body = post.json()
        prompt_id = body.get("prompt_id")
        if not prompt_id:
            raise ComfyImageGenError(f"ComfyUI не вернул prompt_id: {body}")
        node_errors = body.get("node_errors") or {}
        if node_errors:
            raise ComfyImageGenError(f"Ошибки нод ComfyUI: {node_errors}")

        waited = 0.0
        while waited < timeout_sec:
            await asyncio.sleep(poll_interval_sec)
            waited += poll_interval_sec
            history_entry = await _fetch_history_prompt(client, base, str(prompt_id))
            if not history_entry:
                continue
            status = history_entry.get("status")
            if status and status.get("completed") is False and status.get("status_str") == "error":
                messages = status.get("messages") or []
                raise ComfyImageGenError(
                    f"ComfyUI execution error: {_format_comfy_execution_error(messages)}"
                )
            videos = _collect_output_videos(history_entry)
            if videos:
                results: List[Tuple[bytes, str]] = []
                for spec in videos:
                    raw = await _download_image_bytes(client, base, spec)
                    results.append((raw, _mime_for_output_filename(spec["filename"])))
                return results

        raise ComfyImageGenError(
            f"Таймаут ожидания видео ComfyUI ({timeout_sec}s), prompt_id={prompt_id}"
        )


def bytes_to_data_uri(data: bytes, mime: str) -> str:
    b64 = base64.standard_b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"
