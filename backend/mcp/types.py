"""Типы и модели данных MCP-платформы."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class McpTransport(str, Enum):
    STREAMABLE_HTTP = "streamable-http"
    STDIO = "stdio"


@dataclass
class McpCallContext:
    """Контекст вызова MCP для одного chat/request."""

    user_id: str
    username: str
    email: Optional[str] = None
    chat_id: Optional[str] = None
    message_id: Optional[str] = None
    is_admin: bool = False
    groups: List[str] = field(default_factory=list)
    ldap_groups: List[str] = field(default_factory=list)
    extra_headers: Dict[str, str] = field(default_factory=dict)


@dataclass
class McpToolInfo:
    """Нормализованное описание MCP tool."""

    server_id: str
    name: str
    qualified_name: str
    description: str
    parameters: Dict[str, Any]
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class McpServerStatus:
    server_id: str
    display_name: str
    enabled: bool
    transport: str
    connected: bool
    tools: int = 0
    latency_ms: Optional[float] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class McpVerifyResult:
    server_id: str
    success: bool
    tools_count: int = 0
    latency_ms: Optional[float] = None
    error: Optional[str] = None
    tools: List[str] = field(default_factory=list)


@dataclass
class McpAggregateStatus:
    initialized: bool
    enabled: bool
    servers_total: int
    servers_connected: int
    tools_total: int
    servers: List[McpServerStatus] = field(default_factory=list)
    message: Optional[str] = None


@dataclass
class AgentLoopResult:
    content: str
    tool_calls_executed: int = 0
    mode: str = ""
    iterations: int = 0
    attachments: List[Dict[str, str]] = field(default_factory=list)
    # Сообщения, которые цикл добавил к входным (вызовы инструментов и их 
    # результаты), - чтобы повтор хода продолжил с них, а не с начала.
    new_messages: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class ParsedMcpResult:
    text: str
    images: List[Dict[str, Any]] = field(default_factory=list)
    audio: List[Dict[str, Any]] = field(default_factory=list)
    resources: List[Dict[str, Any]] = field(default_factory=list)
    raw: Any = None
