"""Форматирование HTTP-плагинов в блоки :::artifact для чата."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional


def format_markdown_artifact(
    *,
    body: str,
    identifier: str = "plugin-verdict",
    title: str = "Вердикт",
) -> str:
    """Собирает блок :::artifact type=text/markdown для MessageRenderer."""
    text = (body or "").replace("\r\n", "\n").strip()
    if not text:
        return ""
    # 4 backticks, чтобы внутри вердикта спокойно жили ``` fences
    fence = "````"
    safe_id = (
        re.sub(r"[^a-zA-Z0-9_-]+", "-", identifier).strip("-").lower()
        or "plugin-verdict"
    )
    # перевод строки и скобки заголовка ломали бы строку атрибутов :::artifact{...}
    safe_title = re.sub(r"\s+", " ", title or "Вердикт").replace('"', "'")
    safe_title = safe_title.replace("{", "(").replace("}", ")").strip() or "Вердикт"
    return (
        f':::artifact{{identifier="{safe_id}" type="text/markdown" title="{safe_title}"}}\n'
        f"{fence}\n"
        f"{text}\n"
        f"{fence}\n"
        f":::\n"
    )


def verdict_markdown_from_plugin_result(result: Any) -> Optional[str]:
    """Достаёт verdict_markdown из ответа cash-flow /invoke."""
    if not isinstance(result, dict):
        return None
    md = result.get("verdict_markdown")
    if isinstance(md, str) and md.strip():
        return md.strip()
    # иногда обёртка { ok, result: {...} }
    nested = result.get("result")
    if isinstance(nested, dict):
        md2 = nested.get("verdict_markdown")
        if isinstance(md2, str) and md2.strip():
            return md2.strip()
    return None


def findings_fallback_markdown(result: Any) -> str:
    """Если verdict_markdown пуст — собираем markdown из deterministic_findings."""
    if not isinstance(result, dict):
        return ""
    payload = result.get("result") if isinstance(result.get("result"), dict) else result
    if not isinstance(payload, dict):
        return ""
    status = payload.get("status") or "unknown"
    message = payload.get("message") or ""
    findings = payload.get("deterministic_findings") or []
    lines = ["# Аудит денежного потока", "", f"**Статус:** `{status}`", ""]
    if message:
        lines.extend([str(message), ""])
    if isinstance(findings, list) and findings:
        lines.append("## Детерминированные находки")
        lines.append("")
        for f in findings[:50]:
            if not isinstance(f, dict):
                continue
            where = f.get("where") or ""
            ftype = f.get("type") or "?"
            detail = f.get("detail") or ""
            lines.append(f"- **[{ftype}]** {where} — {detail}".rstrip(" —"))
    return "\n".join(lines).strip()


def build_cash_flow_artifact_from_invoke(result: Any) -> str:
    """Готовый :::artifact из ответа сервиса cash-flow."""
    md = verdict_markdown_from_plugin_result(result)
    if not md:
        md = findings_fallback_markdown(result)
    if not md:
        return ""
    return format_markdown_artifact(
        body=md,
        identifier="cash-flow-audit-verdict",
        title="Вердикт аудита денежного потока",
    )


def extract_plugin_artifacts_from_tool_results(
    tool_results: List[Dict[str, Any]],
) -> str:
    """
    Ищет успешные вызовы audit_cash_flow_model и собирает markdown-артефакты.
    """
    blocks: List[str] = []
    for item in tool_results or []:
        if not isinstance(item, dict) or not item.get("success"):
            continue
        tool_name = str(item.get("tool") or "")
        if tool_name not in ("audit_cash_flow_model",):
            continue
        raw = item.get("output")
        parsed: Any = raw
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except Exception:
                # уже может быть готовый artifact-текст
                if ":::artifact" in raw:
                    blocks.append(raw.strip())
                continue
        if not isinstance(parsed, dict) or not parsed.get("ok"):
            continue
        # предпочитаем заранее собранный блок
        ready = parsed.get("artifact_markdown")
        if isinstance(ready, str) and ready.strip():
            blocks.append(ready.strip())
            continue
        block = build_cash_flow_artifact_from_invoke(parsed.get("result") or parsed)
        if block:
            blocks.append(block.strip())
    if not blocks:
        return ""
    return "\n\n".join(blocks)


def append_artifacts_to_answer(answer: Optional[str], artifacts_block: str) -> str:
    """Дописывает артефакты к ответу, если их ещё нет."""
    base = (answer or "").rstrip()
    block = (artifacts_block or "").strip()
    if not block:
        return base
    if ":::artifact" in base and "cash-flow-audit-verdict" in base:
        return base
    if not base:
        return block
    return f"{base}\n\n{block}"
