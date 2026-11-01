"""Вызов плагина из чата (direct и agent prerun) без ожидания планировщика.

Если у выбранного агента подключён плагин и к сообщению приложен файл нужного
формата (.xlsx/.xlsm для cash-flow), backend сам читает оригинал из
INLINE_ATTACH_DIR/MinIO и отправляет его в сервис плагина. Текстовый дамп ячеек
в промпт при этом не кладётся.

Триггер по словам («аудит», «проверь»…) намеренно НЕ используется: пользователь
ожидает, что при агенте с плагином приложенный Excel всегда уходит в cash-flow.
"""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Any, Dict, List, Optional, Sequence

from backend.plugins.tools import PLUGIN_INPUT_EXTENSIONS
from backend.settings.logging import get_logger

logger = get_logger(__name__)

# Читаемые названия для сообщений пользователю.
PLUGIN_LABELS: Dict[str, str] = {
    "cash-flow": "Аудит денежного потока",
}

# Имя multipart-поля файла в /invoke каждого плагина.
PLUGIN_FILE_FIELD: Dict[str, str] = {
    "cash-flow": "model_file",
}

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Вердикт для промпта модели: сам артефакт уходит пользователю целиком.
PROMPT_VERDICT_MAX_CHARS = 60_000


@dataclass
class PluginDirectRun:
    """Что и куда отправляем."""

    plugin_id: str
    label: str
    file_name: str
    minio_object: str
    minio_bucket: str
    prompt: str


@dataclass
class PluginDirectOutcome:
    """Результат вызова: вердикт для промпта, артефакт для ответа."""

    ok: bool
    elapsed_sec: float
    verdict_markdown: str = ""
    artifact_markdown: str = ""
    result_status: str = ""
    error: str = ""
    bytes_sent: int = 0
    invoke_url: str = ""


def _attachment_debug_rows(attachments: Any) -> List[Dict[str, Any]]:
    """Краткий снимок вложений для логов (без содержимого файла)."""
    rows: List[Dict[str, Any]] = []
    if not isinstance(attachments, (list, tuple)):
        return rows
    for item in attachments:
        if not isinstance(item, dict):
            rows.append({"raw_type": type(item).__name__})
            continue
        name = str(item.get("name") or "").strip()
        obj = str(item.get("minio_object") or "").strip()
        bucket = str(item.get("minio_bucket") or "").strip()
        rows.append(
            {
                "name": name or None,
                "has_minio_object": bool(obj),
                "minio_object": obj or None,
                "minio_bucket": bucket or None,
                "contentType": item.get("contentType") or item.get("content_type"),
                "size": item.get("size"),
            }
        )
    return rows


def _attachment_candidates(attachments: Any) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    if not isinstance(attachments, (list, tuple)):
        return out
    for item in attachments:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        obj = str(item.get("minio_object") or "").strip()
        bucket = str(item.get("minio_bucket") or "").strip()
        if name and obj and bucket:
            out.append({"name": name, "minio_object": obj, "minio_bucket": bucket})
    return out


def pick_plugin_run(
    plugin_ids: Optional[Sequence[str]],
    attachments: Any,
    user_message: str,
    *,
    chat_mode: str = "chat",
) -> Optional[PluginDirectRun]:
    """Решает, надо ли отдать вложение плагину. Всегда пишет причину в лог."""
    ids = [str(x).strip() for x in (plugin_ids or []) if str(x).strip()]
    att_rows = _attachment_debug_rows(attachments)
    msg_preview = str(user_message or "").replace("\n", " ").strip()[:120]

    logger.info(
        "[plugin-dispatch] mode=%s decide: plugin_ids=%s attachments=%s user_msg=%r",
        chat_mode,
        ids,
        att_rows,
        msg_preview,
    )

    if not ids:
        logger.info(
            "[plugin-dispatch] mode=%s SKIP: у агента нет активных plugin_ids "
            "(проверьте plugins_enabled и plugin_ids в карточке агента)",
            chat_mode,
        )
        return None

    if not isinstance(attachments, (list, tuple)) or not attachments:
        logger.info(
            "[plugin-dispatch] mode=%s SKIP: в chat_message нет inline_attachments "
            "(фронт не передал метаданные attach)",
            chat_mode,
        )
        return None

    candidates = _attachment_candidates(attachments)
    if not candidates:
        logger.warning(
            "[plugin-dispatch] mode=%s SKIP: вложения есть (%s шт.), но без "
            "minio_object/minio_bucket — оригинал файла на диске/MinIO не сохранён. "
            "Проверьте INLINE_ATTACH_DIR в поде backend и ответ POST /api/documents/attach "
            "(attachments_saved / warning). details=%s",
            chat_mode,
            len(att_rows),
            att_rows,
        )
        return None

    text = str(user_message or "")
    for plugin_id in ids:
        exts = PLUGIN_INPUT_EXTENSIONS.get(plugin_id)
        field = PLUGIN_FILE_FIELD.get(plugin_id)
        if not exts or not field:
            logger.info(
                "[plugin-dispatch] mode=%s SKIP plugin=%s: нет карты расширений/поля файла",
                chat_mode,
                plugin_id,
            )
            continue
        match = next(
            (c for c in candidates if c["name"].lower().endswith(tuple(exts))),
            None,
        )
        if not match:
            logger.info(
                "[plugin-dispatch] mode=%s SKIP plugin=%s: среди вложений нет файла %s "
                "(есть: %s)",
                chat_mode,
                plugin_id,
                exts,
                [c["name"] for c in candidates],
            )
            continue

        run = PluginDirectRun(
            plugin_id=plugin_id,
            label=PLUGIN_LABELS.get(plugin_id, plugin_id),
            file_name=match["name"],
            minio_object=match["minio_object"],
            minio_bucket=match["minio_bucket"],
            prompt=text.strip()[:2000],
        )
        logger.info(
            "[plugin-dispatch] mode=%s WILL_SEND plugin=%s file=%r bucket=%s object=%s "
            "field=%s prompt_chars=%s",
            chat_mode,
            run.plugin_id,
            run.file_name,
            run.minio_bucket,
            run.minio_object,
            field,
            len(run.prompt),
        )
        return run

    logger.info(
        "[plugin-dispatch] mode=%s SKIP: ни один из plugin_ids=%s не подошёл к вложениям",
        chat_mode,
        ids,
    )
    return None


async def run_plugin_direct(
    run: PluginDirectRun, *, chat_mode: str = "chat"
) -> PluginDirectOutcome:
    """Читает файл и отправляет его в сервис плагина; пишет полный след в лог."""
    from backend.plugins.artifact_format import (
        build_cash_flow_artifact_from_invoke,
        findings_fallback_markdown,
        verdict_markdown_from_plugin_result,
    )
    from backend.plugins.platform import get_plugins_platform
    from backend.plugins.tools import load_attachment_bytes

    started = monotonic()
    logger.info(
        "[plugin-dispatch] mode=%s LOAD_START plugin=%s file=%r bucket=%s object=%s",
        chat_mode,
        run.plugin_id,
        run.file_name,
        run.minio_bucket,
        run.minio_object,
    )
    try:
        payload = load_attachment_bytes(run.minio_object, run.minio_bucket)
    except Exception as exc:
        logger.exception(
            "[plugin-dispatch] mode=%s LOAD_FAIL plugin=%s object=%s err=%s",
            chat_mode,
            run.plugin_id,
            run.minio_object,
            exc,
        )
        return PluginDirectOutcome(
            ok=False,
            elapsed_sec=monotonic() - started,
            error=f"не удалось прочитать вложение «{run.file_name}»: {exc}",
        )

    bytes_sent = len(payload or b"")
    logger.info(
        "[plugin-dispatch] mode=%s LOAD_OK plugin=%s file=%r bytes=%s",
        chat_mode,
        run.plugin_id,
        run.file_name,
        bytes_sent,
    )

    field = PLUGIN_FILE_FIELD.get(run.plugin_id) or "file"
    invoke_url = ""
    try:
        platform = get_plugins_platform()
        plugin = platform._registry.require_plugin(run.plugin_id)
        base = platform.resolve_base_url(plugin)
        path = str(plugin.invoke_path or "/audit").strip() or "/audit"
        if not path.startswith("/"):
            path = f"/{path}"
        invoke_url = f"{base}{path}"
        logger.info(
            "[plugin-dispatch] mode=%s INVOKE_START plugin=%s url=%s field=%s "
            "filename=%r bytes=%s timeout=%.0fs",
            chat_mode,
            run.plugin_id,
            invoke_url,
            field,
            run.file_name,
            bytes_sent,
            float(plugin.timeout_seconds or 600),
        )
        result = await platform.invoke_multipart(
            run.plugin_id,
            files=[(field, (run.file_name, payload, _XLSX_MIME))],
            form={"prompt": run.prompt} if run.prompt else None,
        )
    except Exception as exc:
        logger.exception(
            "[plugin-dispatch] mode=%s INVOKE_FAIL plugin=%s url=%s bytes=%s err=%s",
            chat_mode,
            run.plugin_id,
            invoke_url or "?",
            bytes_sent,
            exc,
        )
        return PluginDirectOutcome(
            ok=False,
            elapsed_sec=monotonic() - started,
            error=str(exc) or exc.__class__.__name__,
            bytes_sent=bytes_sent,
            invoke_url=invoke_url,
        )

    if result:
        verdict = verdict_markdown_from_plugin_result(
            result
        ) or findings_fallback_markdown(result)
        artifact = build_cash_flow_artifact_from_invoke(result)
    else:
        # Пустой ответ (в том числе {}) - не успех: иначе fallback построит
        # заголовок со статусом unknown, и пользователь увидит «результат».
        verdict = artifact = ""
    status = ""
    if isinstance(result, dict):
        nested = (
            result.get("result") if isinstance(result.get("result"), dict) else result
        )
        if isinstance(nested, dict):
            status = str(nested.get("status") or "")
    elapsed = monotonic() - started
    logger.info(
        "[plugin-dispatch] mode=%s INVOKE_OK plugin=%s url=%s bytes=%s elapsed=%.1fs "
        "status=%s verdict_chars=%s artifact_chars=%s",
        chat_mode,
        run.plugin_id,
        invoke_url or "?",
        bytes_sent,
        elapsed,
        status or "?",
        len(verdict or ""),
        len(artifact or ""),
    )
    if not verdict and not artifact:
        return PluginDirectOutcome(
            ok=False,
            elapsed_sec=elapsed,
            result_status=status,
            error="сервис плагина вернул пустой результат",
            bytes_sent=bytes_sent,
            invoke_url=invoke_url,
        )
    return PluginDirectOutcome(
        ok=True,
        elapsed_sec=elapsed,
        verdict_markdown=verdict or "",
        artifact_markdown=artifact or "",
        result_status=status,
        bytes_sent=bytes_sent,
        invoke_url=invoke_url,
    )


def prompt_block_for_outcome(run: PluginDirectRun, outcome: PluginDirectOutcome) -> str:
    """Блок для промпта модели вместо дампа ячеек."""
    if not outcome.ok:
        return (
            f"[Плагин «{run.label}» не смог обработать файл «{run.file_name}»: {outcome.error}. "
            "Сообщи об этом пользователю и не проси прикладывать файл заново — он уже приложен.]"
        )
    verdict = outcome.verdict_markdown.strip()
    if len(verdict) > PROMPT_VERDICT_MAX_CHARS:
        verdict = (
            verdict[:PROMPT_VERDICT_MAX_CHARS]
            + "\n\n[…вердикт обрезан для промпта, пользователь видит его полностью в артефакте]"
        )
    return (
        f"[Плагин «{run.label}» уже выполнил разбор приложенного файла «{run.file_name}» "
        f"({outcome.elapsed_sec:.0f} с). Ниже его вердикт. Кратко прокомментируй итог и главные "
        "риски; полный вердикт система приложит к сообщению отдельным блоком, не дублируй его.]\n"
        f"{verdict}"
    )


def system_note_prerun(run: PluginDirectRun) -> str:
    """Подсказка для случая, когда плагин уже выполнен системой."""
    return (
        f"Плагин «{run.label}» уже запущен системой для приложенного файла "
        f"«{run.file_name}», его вердикт есть в сообщении пользователя. Не вызывай этот "
        "инструмент повторно и не проси прикрепить файл — он приложен."
    )


def system_note_no_tools(plugin_ids: Optional[Sequence[str]]) -> str:
    """Подсказка для режима без инструментов: как вообще запускается плагин."""
    if not [str(x).strip() for x in (plugin_ids or []) if str(x).strip()]:
        return ""
    return (
        "В этом режиме вызов инструментов недоступен. Плагин запускается автоматически, "
        "когда пользователь прикладывает файл нужного формата (.xlsx/.xlsm) к сообщению "
        "при подключённом плагине cash-flow. Если файла нет — отвечай сам и попроси "
        "приложить Excel-модель."
    )


__all__ = [
    "PLUGIN_FILE_FIELD",
    "PLUGIN_LABELS",
    "PluginDirectOutcome",
    "PluginDirectRun",
    "pick_plugin_run",
    "prompt_block_for_outcome",
    "run_plugin_direct",
    "system_note_no_tools",
    "system_note_prerun",
]
