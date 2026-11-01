"""LangChain-инструменты для HTTP-плагинов (cash-flow и др.)."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from langchain_core.tools import tool

from backend.settings.logging import get_logger
from backend.tools.tool_context import get_tool_context

logger = get_logger(__name__)

_XLSX_EXT = (".xlsx", ".xlsm", ".xltx", ".xltm")


def _run_async(coro):
    import asyncio
    import concurrent.futures

    try:
        asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor() as executor:
            return executor.submit(asyncio.run, coro).result()
    except RuntimeError:
        return asyncio.run(coro)


def _load_attachment_bytes(object_name: str, bucket: str) -> bytes:
    """Читает вложение из pod INLINE_ATTACH_DIR или MinIO."""
    import os
    from pathlib import Path

    import backend.app_state as state

    # Совпадает с backend.routes.documents.INLINE_ATTACH_POD_TMP_BUCKET
    if bucket == "__pod_tmp__":
        base = (os.getenv("INLINE_ATTACH_DIR") or "").strip()
        if not base:
            raise RuntimeError("INLINE_ATTACH_DIR не задан")
        root = Path(base).resolve()
        candidate = (root / object_name).resolve()
        if not candidate.is_relative_to(root) or not candidate.is_file():
            raise FileNotFoundError(f"Attachment not found: {object_name}")
        return candidate.read_bytes()
    client = getattr(state, "minio_client", None)
    if not client:
        raise RuntimeError("MinIO недоступен")
    return client.download_file(object_name, bucket_name=bucket)


def load_attachment_bytes(object_name: str, bucket: str) -> bytes:
    """Публичная обёртка: вложение нужно и прямому режиму чата, не только инструменту."""
    return _load_attachment_bytes(object_name, bucket)


def _find_excel_in_context() -> Optional[Dict[str, str]]:
    ctx = get_tool_context() or {}
    attachments = ctx.get("inline_attachments") or ctx.get("attachments") or []
    if not isinstance(attachments, list):
        return None
    for item in attachments:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        low = name.lower()
        if not any(low.endswith(ext) for ext in _XLSX_EXT):
            continue
        obj = item.get("minio_object")
        bucket = item.get("minio_bucket")
        if obj and bucket:
            return {"name": name, "minio_object": str(obj), "minio_bucket": str(bucket)}
    return None


_LLM_FINDINGS_LIMIT = 30
_LLM_FINDING_DETAIL_CHARS = 300


def _normalize_prompt_arg(value: Any) -> str:
    """Планировщик кладёт в input то строку, то JSON — достаём текст фокуса аудита."""
    if value is None:
        return ""
    if isinstance(value, dict):
        for key in ("prompt", "input", "query", "text"):
            nested = value.get(key)
            if isinstance(nested, str) and nested.strip():
                return nested.strip()
        return ""
    text = str(value).strip()
    if text.startswith("{") and text.endswith("}"):
        try:
            return _normalize_prompt_arg(json.loads(text))
        except ValueError:
            return text
    return text


def _compact_result_for_llm(result: Any) -> Dict[str, Any]:
    """Ответ сервиса без вердикта: он уже уходит в artifact_markdown."""
    if not isinstance(result, dict):
        return {"raw": str(result)[:2000]}
    payload = result.get("result") if isinstance(result.get("result"), dict) else result
    findings = payload.get("deterministic_findings")
    findings = findings if isinstance(findings, list) else []
    trimmed = []
    for item in findings[:_LLM_FINDINGS_LIMIT]:
        if not isinstance(item, dict):
            continue
        trimmed.append(
            {
                "where": str(item.get("where") or "")[:200],
                "type": str(item.get("type") or "")[:100],
                "n": str(item.get("n") or "")[:100],
                "detail": str(item.get("detail") or "")[:_LLM_FINDING_DETAIL_CHARS],
            }
        )
    compact: Dict[str, Any] = {
        "status": payload.get("status"),
        "request_id": payload.get("request_id"),
        "file": payload.get("file"),
        "steps": payload.get("steps") if isinstance(payload.get("steps"), dict) else {},
        "findings_total": len(findings),
        "deterministic_findings": trimmed,
        "verdict_in_artifact": bool(payload.get("verdict_markdown")),
    }
    message = payload.get("message")
    if message:
        compact["message"] = str(message)[:1000]
    if len(findings) > len(trimmed):
        compact["findings_note"] = (
            f"Показаны первые {len(trimmed)} из {len(findings)} находок; полный список — в артефакте."
        )
    return compact


@tool
def audit_cash_flow_model(
    prompt: str = "", minio_object: str = "", minio_bucket: str = "", filename: str = ""
) -> str:
    """
    Запускает аудит проектно-финансовой Excel-модели через плагин cash-flow
    (DSCR, CFADS, NPV, IRR, ковенанты). Передайте minio_object/minio_bucket
    вложения или прикрепите .xlsx/.xlsm к сообщению.

    Args:
        prompt: Дополнительный фокус аналитика (например, «проверь DSCR»).
        minio_object: Имя объекта вложения (если не из контекста).
        minio_bucket: Бакет вложения.
        filename: Оригинальное имя файла (опционально).
    """
    try:
        prompt = _normalize_prompt_arg(prompt)
        minio_object = str(minio_object or "").strip()
        minio_bucket = str(minio_bucket or "").strip()
        filename = str(filename or "").strip()
        plugin_ids = (get_tool_context() or {}).get("__plugin_ids__") or []
        if plugin_ids and "cash-flow" not in [str(x) for x in plugin_ids]:
            return json.dumps(
                {"ok": False, "error": "Плагин cash-flow не подключён к агенту"},
                ensure_ascii=False,
            )

        ref = None
        if minio_object and minio_bucket:
            ref = {
                "name": filename or minio_object,
                "minio_object": minio_object,
                "minio_bucket": minio_bucket,
            }
        else:
            ref = _find_excel_in_context()
        if not ref:
            return json.dumps(
                {
                    "ok": False,
                    "error": (
                        "Не найден Excel-файл. Прикрепите .xlsx/.xlsm к сообщению "
                        "или укажите minio_object и minio_bucket."
                    ),
                },
                ensure_ascii=False,
            )

        data = _load_attachment_bytes(ref["minio_object"], ref["minio_bucket"])
        name = ref.get("name") or "model.xlsx"
        if not any(name.lower().endswith(ext) for ext in _XLSX_EXT):
            name = f"{name}.xlsx"

        from backend.plugins.platform import get_plugins_platform

        platform = get_plugins_platform()

        async def _invoke():
            return await platform.invoke_multipart(
                "cash-flow",
                files=[
                    (
                        "model_file",
                        (
                            name,
                            data,
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        ),
                    )
                ],
                form={"prompt": prompt} if prompt else None,
            )

        result = _run_async(_invoke())
        from backend.plugins.artifact_format import build_cash_flow_artifact_from_invoke

        artifact_md = build_cash_flow_artifact_from_invoke(result)
        # Вердикт уходит в промпт агрегатора один раз — только внутри artifact_markdown.
        # Раньше тот же текст дублировался в result.verdict_markdown и verdict_markdown,
        # и на большой модели агрегатор упирался в контекст шлюза.
        payload: Dict[str, Any] = {
            "ok": True,
            "result": _compact_result_for_llm(result),
        }
        if artifact_md:
            payload["artifact_markdown"] = artifact_md
        return json.dumps(payload, ensure_ascii=False, default=str)
    except Exception as e:
        logger.exception("audit_cash_flow_model failed")
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)


PLUGIN_TOOLS: Dict[str, Any] = {
    "cash-flow": audit_cash_flow_model,
}

# Расширения вложений, которые плагин читает сам (байты из INLINE_ATTACH_DIR/MinIO).
# Текст таких файлов не нужно вставлять в промпт: дамп Excel-модели бесполезен
# для LLM и переполняет контекст (шлюз отвечает HTTP 400).
PLUGIN_INPUT_EXTENSIONS: Dict[str, tuple] = {
    "cash-flow": _XLSX_EXT,
}


def plugin_owned_attachment_names(
    plugin_ids: Optional[List[str]],
    attachment_names: Optional[List[str]],
) -> List[str]:
    """Имена вложений, которые прочитает сам плагин (а не LLM из промпта)."""
    ids = [str(x).strip() for x in (plugin_ids or []) if str(x).strip()]
    exts: List[str] = []
    for pid in ids:
        exts.extend(PLUGIN_INPUT_EXTENSIONS.get(pid) or ())
    if not exts:
        return []
    owned = []
    for name in attachment_names or []:
        low = str(name or "").strip().lower()
        if low and any(low.endswith(ext) for ext in exts):
            owned.append(str(name))
    return owned


def plugin_attachment_prompt_note(
    plugin_ids: Optional[List[str]],
    owned_names: Optional[List[str]],
) -> str:
    """Заметка вместо вырезанного содержимого файла: чем его читать."""
    names = [str(n) for n in (owned_names or []) if str(n).strip()]
    if not names:
        return ""
    tool_names = []
    for pid in [str(x).strip() for x in (plugin_ids or []) if str(x).strip()]:
        tool_fn = PLUGIN_TOOLS.get(pid)
        tool_name = getattr(tool_fn, "name", None)
        if tool_name and tool_name not in tool_names:
            tool_names.append(str(tool_name))
    files = ", ".join(f"«{n}»" for n in names)
    hint = (
        f" Чтобы получить его содержимое и результат разбора, вызови инструмент: {', '.join(tool_names)}."
        if tool_names
        else ""
    )
    return (
        f"[Прикреплённый файл {files} обрабатывается плагином и не вставлен в промпт целиком.]"
        f"{hint}"
    )


PLUGIN_SYSTEM_HINTS: Dict[str, str] = {
    "cash-flow": (
        "Доступен плагин «Аудит денежного потока» (cash-flow). "
        "Для аудита Excel-модели используй инструмент audit_cash_flow_model: "
        "пользователь должен прикрепить .xlsx/.xlsm к сообщению. "
        "В ответе инструмента будет artifact_markdown — блок :::artifact с вердиктом. "
        "Кратко прокомментируй итог пользователю; сам блок артефакта система добавит в сообщение "
        "(не дублируй его целиком, если он уже будет вставлен)."
    ),
}


PLUGIN_PLANNER_HINTS: Dict[str, str] = {
    "cash-flow": (
        "- audit_cash_flow_model — аудит проектно-финансовой Excel-модели "
        "(DSCR, CFADS, NPV, IRR, ковенанты). Если к сообщению приложен .xlsx/.xlsm и пользователь "
        "просит аудит, проверку или анализ модели — ОБЯЗАТЕЛЬНО включи шаг "
        '{"tool": "audit_cash_flow_model", "input": "<на что обратить внимание, можно пусто>"}. '
        "Инструмент сам читает приложенный файл: путь, имя файла и его содержимое в input не нужны."
    ),
}


def build_plugins_planner_hint(plugin_ids: Optional[List[str]] = None) -> str:
    """Блок для промпта планировщика: как вызывать инструменты подключённых плагинов."""
    ids = [str(x).strip() for x in (plugin_ids or []) if str(x).strip()]
    parts = [PLUGIN_PLANNER_HINTS[pid] for pid in ids if pid in PLUGIN_PLANNER_HINTS]
    if not parts:
        return ""
    return "ПЛАГИНЫ, ПОДКЛЮЧЁННЫЕ К ЭТОМУ ЗАПРОСУ:\n" + "\n".join(parts) + "\n"


def get_plugin_tools(plugin_ids: Optional[List[str]] = None) -> List[Any]:
    ids = [str(x).strip() for x in (plugin_ids or []) if str(x).strip()]
    tools = []
    for pid in ids:
        tool_fn = PLUGIN_TOOLS.get(pid)
        if tool_fn is not None:
            tools.append(tool_fn)
    return tools


def build_plugins_system_append(plugin_ids: Optional[List[str]] = None) -> str:
    ids = [str(x).strip() for x in (plugin_ids or []) if str(x).strip()]
    parts = [PLUGIN_SYSTEM_HINTS[pid] for pid in ids if pid in PLUGIN_SYSTEM_HINTS]
    if not parts:
        return ""
    return "\n\n".join(parts)
