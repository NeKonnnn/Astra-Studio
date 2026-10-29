"""streamable-http / list_tools: совместимость с MCP SDK."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from backend.mcp.client import McpClient
from backend.mcp.transports import streamable_http as sh


pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_connect_streamable_http_accepts_server_id(monkeypatch):
    """До фикса: TypeError unexpected keyword argument 'server_id' → UI «не удалось загрузить»."""
    captured = {}

    class FakeClient:
        async def connect_streams(self, streams_context, **kw):
            captured["streams"] = streams_context
            captured["kw"] = kw

    @asynccontextmanager
    async def fake_new(url, *, headers, timeout, verify_ssl):
        yield ("read", "write")

    monkeypatch.setattr(sh, "_uses_new_streamable_http_api", lambda: True)
    monkeypatch.setattr(sh, "_connect_new_streamable_http", fake_new)

    await sh.connect_streamable_http(
        FakeClient(),
        url="http://mcp-websearch:3000/mcp",
        headers={"X-Test": "1"},
        timeout=60.0,
        verify_ssl=True,
        server_id="websearch",
    )
    assert captured["kw"]["server_id"] == "websearch"
    assert captured["kw"]["mcp_url"] == "http://mcp-websearch:3000/mcp"
    assert captured["kw"]["init_timeout"] == 30.0


@pytest.mark.asyncio
async def test_list_tool_specs_accepts_input_schema_snake_case():
    """Новый MCP SDK отдаёт Tool.input_schema; старый код читал только inputSchema."""

    class FakeTool:
        name = "search"
        description = "web"
        input_schema = {"type": "object"}

    class FakeSession:
        async def list_tools(self):
            return SimpleNamespace(tools=[FakeTool()])

    client = McpClient()
    client.session = FakeSession()
    specs = await client.list_tool_specs()
    assert specs == [
        {"name": "search", "description": "web", "parameters": {"type": "object"}}
    ]


@pytest.mark.asyncio
async def test_list_tool_specs_still_reads_inputSchema_alias():
    class FakeTool:
        name = "search"
        description = "web"
        inputSchema = {"type": "object", "properties": {"q": {"type": "string"}}}

    class FakeSession:
        async def list_tools(self):
            return SimpleNamespace(tools=[FakeTool()])

    client = McpClient()
    client.session = FakeSession()
    specs = await client.list_tool_specs()
    assert specs[0]["parameters"]["properties"]["q"]["type"] == "string"


class _FakeCallResult:
    def __init__(self, *, content, is_error=None, isError=None):
        self.content = content
        if is_error is not None:
            self.is_error = is_error
        if isError is not None:
            self.isError = isError

    def model_dump(self, mode="python"):
        data = {"content": self.content}
        if hasattr(self, "is_error"):
            data["is_error"] = self.is_error
        if hasattr(self, "isError"):
            data["isError"] = self.isError
        return data


@pytest.mark.asyncio
async def test_call_tool_reads_is_error_snake_case():
    """До фикса: AttributeError 'CallToolResult' object has no attribute 'isError'."""

    class FakeSession:
        async def call_tool(self, name, args):
            return _FakeCallResult(content=[{"type": "text", "text": "ok"}], is_error=False)

    client = McpClient()
    client.session = FakeSession()
    assert await client.call_tool("search", {"q": "x"}) == [
        {"type": "text", "text": "ok"}
    ]


@pytest.mark.asyncio
async def test_call_tool_still_reads_isError_camel_case():
    class FakeSession:
        async def call_tool(self, name, args):
            return _FakeCallResult(content=[{"type": "text", "text": "ok"}], isError=False)

    client = McpClient()
    client.session = FakeSession()
    assert await client.call_tool("search", {}) == [{"type": "text", "text": "ok"}]


@pytest.mark.asyncio
async def test_call_tool_raises_on_is_error_true():
    class FakeSession:
        async def call_tool(self, name, args):
            return _FakeCallResult(content=[{"type": "text", "text": "boom"}], is_error=True)

    client = McpClient()
    client.session = FakeSession()
    with pytest.raises(RuntimeError, match="boom"):
        await client.call_tool("search", {})
