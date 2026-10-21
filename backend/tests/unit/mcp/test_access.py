"""mcp/access: ACL серверов и фильтрация инструментов (B-35, B-36)."""

import pytest

from backend.mcp import access
from backend.mcp.types import McpCallContext, McpToolInfo
from backend.settings.config import McpServerConfig

pytestmark = pytest.mark.unit


def _server(**kwargs) -> McpServerConfig:
    return McpServerConfig(id=kwargs.pop("id", "srv"), **kwargs)


def _ctx(**kwargs) -> McpCallContext:
    return McpCallContext(
        user_id=kwargs.pop("user_id", "u1"),
        username=kwargs.pop("username", "alice"),
        **kwargs,
    )


def _tool(name: str, server_id: str = "srv") -> McpToolInfo:
    return McpToolInfo(
        server_id=server_id,
        name=name,
        qualified_name=f"{server_id}__{name}",
        description="",
        parameters={},
    )


class TestServerAccess:
    def test_disabled_server_is_closed_even_for_admin(self):
        assert not access.has_mcp_server_access(
            _server(enabled=False), _ctx(is_admin=True)
        )

    def test_server_without_grants_is_open(self):
        assert access.has_mcp_server_access(_server(), _ctx())

    def test_empty_grants_list_is_open(self):
        assert access.has_mcp_server_access(_server(access_grants=[]), _ctx())

    def test_admin_bypasses_grants(self):
        assert access.has_mcp_server_access(
            _server(access_grants=["finance"]), _ctx(is_admin=True)
        )

    def test_matching_group_grants_access(self):
        assert access.has_mcp_server_access(
            _server(access_grants=["finance"]), _ctx(groups=["finance", "other"])
        )

    def test_ldap_groups_count_too(self):
        assert access.has_mcp_server_access(
            _server(access_grants=["finance"]), _ctx(ldap_groups=["finance"])
        )

    def test_non_matching_group_is_denied(self):
        assert not access.has_mcp_server_access(
            _server(access_grants=["finance"]), _ctx(groups=["hr"])
        )

    def test_user_without_groups_is_denied_when_grants_exist(self):
        assert not access.has_mcp_server_access(_server(access_grants=["finance"]), _ctx())

    def test_group_match_is_case_sensitive(self):
        assert not access.has_mcp_server_access(
            _server(access_grants=["Finance"]), _ctx(groups=["finance"])
        )

    def test_falsy_group_entries_are_ignored(self):
        assert not access.has_mcp_server_access(
            _server(access_grants=[""]), _ctx(groups=["", None])
        )

    def test_is_server_allowed_is_alias(self):
        server, ctx = _server(access_grants=["x"]), _ctx(groups=["x"])

        assert access.is_server_allowed(server, ctx) is True

    def test_extra_header_values_count_as_groups(self):
        # Значения extra_headers НЕ являются группами (иначе обход ACL).
        ctx = _ctx(extra_headers={"X-Anything": "finance"})

        assert access._user_groups(ctx) == set()
        assert not access.has_mcp_server_access(
            _server(access_grants=["finance"]), ctx
        )


class TestFilterTools:
    TOOLS = [_tool("read"), _tool("write"), _tool("delete")]

    def test_no_filters_returns_everything(self):
        result = access.filter_tools(self.TOOLS, server=_server(), context=_ctx())

        assert [t.name for t in result] == ["read", "write", "delete"]

    def test_no_access_returns_nothing(self):
        result = access.filter_tools(
            self.TOOLS, server=_server(access_grants=["x"]), context=_ctx()
        )

        assert result == []

    def test_native_whitelist(self):
        result = access.filter_tools(
            self.TOOLS, server=_server(enabled_tools=["read"]), context=_ctx()
        )

        assert [t.name for t in result] == ["read"]

    def test_qualified_whitelist(self):
        result = access.filter_tools(
            self.TOOLS,
            server=_server(function_name_filter_list=["srv__write"]),
            context=_ctx(),
        )

        assert [t.name for t in result] == ["write"]

    def test_user_selection_narrows_further(self):
        result = access.filter_tools(
            self.TOOLS,
            server=_server(enabled_tools=["read", "write"]),
            context=_ctx(),
            enabled_tool_names={"srv__write"},
        )

        assert [t.name for t in result] == ["write"]

    def test_empty_user_selection_disables_all(self):
        result = access.filter_tools(
            self.TOOLS, server=_server(), context=_ctx(), enabled_tool_names=set()
        )

        assert result == []

    def test_works_with_generators(self):
        result = access.filter_tools(
            (t for t in self.TOOLS), server=_server(), context=_ctx()
        )

        assert len(result) == 3
