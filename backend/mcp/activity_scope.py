"""Контекст UI-активности: цепочка / субагент / вложенные tools.

Через contextvars все mcp_tool_start/end автоматически получают
agent_id, chain_step_index, parent_call_id, depth, activity_kind.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any, Dict, Iterator, Optional

from backend.agents.subagents.settings import NATIVE_SERVER_ID

_SCOPE: ContextVar[Optional["ActivityScope"]] = ContextVar(
    "mcp_activity_scope", default=None
)


@dataclass(frozen=True)
class ActivityScope:
    agent_id: Optional[int] = None
    agent_name: Optional[str] = None
    chain_step_index: Optional[int] = None
    chain_step_total: Optional[int] = None
    parent_call_id: Optional[str] = None
    depth: int = 0
    activity_kind: Optional[str] = None
    llm_iteration: Optional[int] = None

    def merge(self, **kwargs: Any) -> "ActivityScope":
        # Явный None для parent_call_id / agent_id — сброс, если ключ передан.
        data = {
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "chain_step_index": self.chain_step_index,
            "chain_step_total": self.chain_step_total,
            "parent_call_id": self.parent_call_id,
            "depth": self.depth,
            "activity_kind": self.activity_kind,
            "llm_iteration": self.llm_iteration,
        }
        for key, value in kwargs.items():
            if key in data:
                data[key] = value
        return ActivityScope(**data)

    def as_payload(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        if self.agent_id is not None:
            try:
                out["agent_id"] = int(self.agent_id)
            except (TypeError, ValueError):
                pass
        if self.agent_name:
            out["agent_name"] = str(self.agent_name)
        if self.chain_step_index is not None:
            out["chain_step_index"] = int(self.chain_step_index)
        if self.chain_step_total is not None:
            out["chain_step_total"] = int(self.chain_step_total)
        if self.parent_call_id:
            out["parent_call_id"] = str(self.parent_call_id)
        if self.depth:
            out["depth"] = int(self.depth)
        if self.activity_kind:
            out["activity_kind"] = str(self.activity_kind)
        if self.llm_iteration is not None:
            out["llm_iteration"] = int(self.llm_iteration)
        return out


def current_scope() -> Optional[ActivityScope]:
    return _SCOPE.get()


def merge_scope_into_payload(
    payload: Dict[str, Any],
    scope: Optional[ActivityScope] = None,
) -> Dict[str, Any]:
    """Добавляет поля scope, не перезаписывая уже заданные в payload."""
    sc = scope if scope is not None else current_scope()
    if not sc:
        return payload
    for key, value in sc.as_payload().items():
        if key not in payload or payload.get(key) in (None, ""):
            payload[key] = value
    return payload


@contextmanager
def activity_scope(**kwargs: Any) -> Iterator[ActivityScope]:
    prev = current_scope()
    merged = prev.merge(**kwargs) if prev is not None else ActivityScope(**kwargs)
    token: Token = _SCOPE.set(merged)
    try:
        yield merged
    finally:
        _SCOPE.reset(token)


async def emit_scoped_activity(
    callback,
    *,
    tool: str,
    activity_kind: str,
    arguments: Optional[Dict[str, Any]] = None,
    success: bool = True,
    result: Optional[str] = None,
    result_preview: Optional[str] = None,
    error: Optional[str] = None,
    document_search: Optional[Dict[str, Any]] = None,
    duration_ms: int = 0,
    server_id: str = NATIVE_SERVER_ID,
    qualified_name: Optional[str] = None,
) -> Optional[str]:
    """Синтетическая карточка активности (skill / KB / artifacts / plugin)."""
    if not callback:
        return None
    from backend.mcp.events import emit_mcp_tool_end, emit_mcp_tool_start

    call_id = uuid.uuid4().hex
    qn = qualified_name or tool
    with activity_scope(activity_kind=activity_kind):
        await emit_mcp_tool_start(
            callback,
            server_id=server_id,
            tool=tool,
            qualified_name=qn,
            call_id=call_id,
            arguments=arguments,
        )
        await emit_mcp_tool_end(
            callback,
            server_id=server_id,
            tool=tool,
            qualified_name=qn,
            success=success,
            duration_ms=duration_ms,
            error=error,
            result_preview=result_preview,
            call_id=call_id,
            arguments=arguments,
            result=result,
            document_search=document_search,
        )
    return call_id
