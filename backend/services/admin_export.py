"""Выгрузка admin-сводок в Excel / CSV / JSON."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from backend.settings.logging import get_logger

logger = get_logger(__name__)

Sheet = Tuple[str, Sequence[str], Sequence[Sequence[Any]]]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _sheet_rows(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> List[List[Any]]:
    return [list(headers), *[list(r) for r in rows]]


def _write_sheet(wb, title: str, headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> None:
    from openpyxl.utils import get_column_letter

    safe = (title or "Sheet")[:31]
    ws = wb.create_sheet(title=safe)
    for r_idx, row in enumerate(_sheet_rows(headers, rows), start=1):
        for c_idx, val in enumerate(row, start=1):
            ws.cell(row=r_idx, column=c_idx, value="" if val is None else val)
    for c_idx, header in enumerate(headers, start=1):
        width = len(str(header))
        for row in rows:
            if c_idx - 1 < len(row):
                width = max(width, len(str(row[c_idx - 1] if row[c_idx - 1] is not None else "")))
        ws.column_dimensions[get_column_letter(c_idx)].width = max(10, min(48, width + 2))


def _rows_as_dicts(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for row in rows:
        item: Dict[str, Any] = {}
        for i, key in enumerate(headers):
            item[key] = row[i] if i < len(row) else None
        out.append(item)
    return out


async def collect_admin_export_sheets(
    days: int = 14,
    entity_type: Optional[str] = None,
) -> List[Sheet]:
    """Собрать листы выгрузки (общая логика для xlsx/csv/json)."""
    from backend.database.init_db import get_admin_activity_repository
    from backend.database.postgresql.admin_activity_repository import ENTITY_TYPES
    from backend.services.admin_activity import (
        build_admin_activity,
        build_admin_auth_stats,
        build_admin_usage_stats,
    )

    days = max(1, min(int(days or 14), 90))
    since = _utc_now() - timedelta(days=days)
    activity = await build_admin_activity(days=days)
    auth = await build_admin_auth_stats(days=days, limit=500)
    usage_all = await build_admin_usage_stats(days=days, entity_type=None, limit=200)

    et = (entity_type or "").strip().lower() or None
    if et and et not in ENTITY_TYPES:
        et = None
    usage_focused = (
        await build_admin_usage_stats(days=days, entity_type=et, limit=200)
        if et
        else usage_all
    )

    repo = get_admin_activity_repository()
    auth_events = await repo.list_auth_events(since, limit=10000)
    usage_events = await repo.list_usage_events(since, entity_type=et, limit=10000)

    summary = activity.get("summary") or {}
    usage_7d = summary.get("usage_7d") or {}
    series = activity.get("series") or []
    auth_summary = auth.get("summary") or {}

    sheets: List[Sheet] = [
        (
            "Сводка",
            ["metric", "value"],
            [
                ("generated_at", activity.get("generated_at")),
                ("days", days),
                ("entity_type_filter", et or ""),
                ("active_users_24h", summary.get("active_users_24h")),
                ("active_users_7d", summary.get("active_users_7d")),
                ("logins_24h", summary.get("logins_24h")),
                ("logouts_24h", summary.get("logouts_24h")),
                ("logins_7d", summary.get("logins_7d")),
                ("logouts_7d", summary.get("logouts_7d")),
                ("unique_logins_7d", summary.get("unique_logins_7d")),
                ("conversations_touched_24h", summary.get("conversations_touched_24h")),
                ("agents_created_7d", summary.get("agents_created_7d")),
                ("skills_created_7d", summary.get("skills_created_7d")),
                ("usage_agent_7d", usage_7d.get("agent")),
                ("usage_skill_7d", usage_7d.get("skill")),
                ("usage_mcp_7d", usage_7d.get("mcp")),
                ("usage_knowledge_base_7d", usage_7d.get("knowledge_base")),
                ("usage_plugin_7d", usage_7d.get("plugin")),
            ],
        ),
        (
            "Активность по дням",
            [
                "date",
                "active_users",
                "logins",
                "logouts",
                "conversation_updates",
                "agents_created",
                "skills_created",
                "usage_total",
            ],
            [
                (
                    r.get("date"),
                    r.get("active_users"),
                    r.get("logins"),
                    r.get("logouts"),
                    r.get("conversation_updates"),
                    r.get("agents_created"),
                    r.get("skills_created"),
                    r.get("usage_total"),
                )
                for r in series
            ],
        ),
        (
            "Сессии сводка",
            ["metric", "value"],
            [(k, v) for k, v in auth_summary.items()],
        ),
        (
            "Сессии по дням",
            ["date", "logins", "logouts"],
            [
                (r.get("date"), r.get("logins"), r.get("logouts"))
                for r in (auth.get("series") or [])
            ],
        ),
        (
            "Сессии по пользователям",
            ["user_id", "logins", "logouts", "last_login_at", "last_logout_at"],
            [
                (
                    r.get("user_id"),
                    r.get("logins"),
                    r.get("logouts"),
                    r.get("last_login_at"),
                    r.get("last_logout_at"),
                )
                for r in (auth.get("by_user") or [])
            ],
        ),
        (
            "События login-logout",
            ["logged_at", "user_id", "event_type"],
            [
                (r.get("logged_at"), r.get("user_id"), r.get("event_type"))
                for r in auth_events
            ],
        ),
        (
            "Usage 7д по типам",
            ["entity_type", "uses_7d"],
            [
                (k, v)
                for k, v in sorted((usage_all.get("summary_7d") or {}).items())
            ],
        ),
        (
            "Usage топ",
            ["entity_type", "entity_id", "entity_name", "uses", "unique_users"],
            [
                (
                    r.get("entity_type"),
                    r.get("entity_id"),
                    r.get("entity_name"),
                    r.get("uses"),
                    r.get("unique_users"),
                )
                for r in (usage_focused.get("top") or [])
            ],
        ),
        (
            "Usage кто-чем",
            ["user_id", "entity_type", "entity_id", "entity_name", "uses", "last_used_at"],
            [
                (
                    r.get("user_id"),
                    r.get("entity_type"),
                    r.get("entity_id"),
                    r.get("entity_name"),
                    r.get("uses"),
                    r.get("last_used_at"),
                )
                for r in (usage_focused.get("by_user") or [])
            ],
        ),
        (
            "Usage события",
            ["occurred_at", "entity_type", "entity_id", "entity_name", "user_id", "action"],
            [
                (
                    r.get("occurred_at"),
                    r.get("entity_type"),
                    r.get("entity_id"),
                    r.get("entity_name"),
                    r.get("user_id"),
                    r.get("action"),
                )
                for r in usage_events
            ],
        ),
    ]
    return sheets


async def build_admin_export_xlsx(
    days: int = 14,
    entity_type: Optional[str] = None,
) -> bytes:
    from openpyxl import Workbook

    sheets = await collect_admin_export_sheets(days=days, entity_type=entity_type)
    wb = Workbook()
    default = wb.active
    wb.remove(default)
    for title, headers, rows in sheets:
        _write_sheet(wb, title, headers, rows)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def build_admin_export_csv(
    days: int = 14,
    entity_type: Optional[str] = None,
) -> bytes:
    """Один CSV: секции через `# SheetName` + таблица."""
    sheets = await collect_admin_export_sheets(days=days, entity_type=entity_type)
    buf = io.StringIO()
    writer = csv.writer(buf)
    for idx, (title, headers, rows) in enumerate(sheets):
        if idx:
            writer.writerow([])
        writer.writerow([f"# {title}"])
        writer.writerow(list(headers))
        for row in rows:
            writer.writerow(["" if v is None else v for v in row])
    # BOM для Excel
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


async def build_admin_export_json(
    days: int = 14,
    entity_type: Optional[str] = None,
) -> bytes:
    sheets = await collect_admin_export_sheets(days=days, entity_type=entity_type)
    payload = {
        "generated_at": _utc_now().isoformat(),
        "days": max(1, min(int(days or 14), 90)),
        "entity_type": (entity_type or "").strip().lower() or None,
        "sheets": [
            {
                "name": title,
                "headers": list(headers),
                "rows": _rows_as_dicts(headers, rows),
            }
            for title, headers, rows in sheets
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


async def build_admin_export(
    fmt: str = "xlsx",
    days: int = 14,
    entity_type: Optional[str] = None,
) -> tuple[bytes, str, str]:
    """Вернуть (payload, filename, media_type)."""
    key = (fmt or "xlsx").strip().lower()
    days_n = max(1, min(int(days or 14), 90))
    if key in ("xlsx", "excel", "xls"):
        return (
            await build_admin_export_xlsx(days=days_n, entity_type=entity_type),
            f"admin_export_{days_n}d.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    if key == "csv":
        return (
            await build_admin_export_csv(days=days_n, entity_type=entity_type),
            f"admin_export_{days_n}d.csv",
            "text/csv; charset=utf-8",
        )
    if key == "json":
        return (
            await build_admin_export_json(days=days_n, entity_type=entity_type),
            f"admin_export_{days_n}d.json",
            "application/json; charset=utf-8",
        )
    raise ValueError(f"Unsupported export format: {fmt}")
