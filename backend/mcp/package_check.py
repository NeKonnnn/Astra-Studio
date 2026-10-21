"""Проверка полноты MCP-пакета при старте."""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path
from typing import List

from backend.settings.logging import get_logger

log = get_logger(__name__)

REQUIRED_MODULES = (
    "agent_loop",
    "chat_integration",
    "client",
    "connection",
    "context_headers",
    "events",
    "platform",
    "prompt_fc_handler",
    "registry",
    "result_parser",
    "resolvers",
    "sdk",
    "session_manager",
    "session_pool",
    "tool_adapter",
    "tool_calling",
    "types",
)


def verify_mcp_package() -> List[str]:
    """Возвращает список отсутствующих модулей backend.mcp.*."""
    missing: List[str] = []
    for name in REQUIRED_MODULES:
        try:
            importlib.import_module(f"backend.mcp.{name}")
        except ModuleNotFoundError:
            missing.append(name)
    return missing


def verify_prompt_fc_handler() -> List[str]:
    """Предупреждения о устаревшем prompt_fc_handler в образе."""
    issues: List[str] = []
    try:
        from backend.mcp.prompt_fc_handler import run_prompt_json_fc

        params = inspect.signature(run_prompt_json_fc).parameters
        if "event_callback" not in params:
            issues.append(
                "prompt_fc_handler.run_prompt_json_fc без event_callback (устаревший деплой)"
            )
    except Exception as exc:
        log.exception("prompt_fc_handler")
        issues.append(f"prompt_fc_handler: {exc}")
    return issues


def log_mcp_package_status() -> None:
    missing = verify_mcp_package()
    stale = verify_prompt_fc_handler()
    if missing:
        root = Path(__file__).resolve().parent
        log.error(
            "MCP package incomplete — agent loop will fail. Missing modules: %s. Ensure entire folder %s is copied into the backend image.",
            ", ".join(missing),
            root,
        )
    elif stale:
        log.warning(
            "MCP package check OK (%s modules), но есть устаревшие модули: %s",
            len(REQUIRED_MODULES),
            "; ".join(stale),
        )
    else:
        log.info("MCP package check OK (%s modules)", len(REQUIRED_MODULES))
