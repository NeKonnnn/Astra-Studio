"""plugins/artifact_format, plugins/orchestrator_bridge, plugins/tools (хвост): блоки :::artifact и подключение tools плагинов."""

import json

import pytest

from backend.plugins import artifact_format as af
from backend.plugins import orchestrator_bridge as ob
from backend.plugins import tools as pt

pytestmark = pytest.mark.unit


class TestFormatMarkdownArtifact:
    def test_empty_body_gives_empty_string(self):
        assert af.format_markdown_artifact(body="  \n ") == "" and af.format_markdown_artifact(body=None) == ""

    def test_block_structure_and_normalization(self):
        out = af.format_markdown_artifact(body="a\r\nb", identifier="My Verdict #1!", title='Вердикт "X"')
        assert out == ':::artifact{identifier="my-verdict-1" type="text/markdown" title="Вердикт \'X\'"}\n````\na\nb\n````\n:::\n'

    def test_defaults_when_identifier_and_title_are_empty(self):
        out = af.format_markdown_artifact(body="x", identifier="!!!", title="")
        assert 'identifier="plugin-verdict"' in out and 'title="Вердикт"' in out

    def test_inner_triple_backtick_fences_survive(self):
        body = "```py\nx\n```"
        assert f"````\n{body}\n````" in af.format_markdown_artifact(body=body)

    def test_title_cannot_break_the_header_line(self):
        out = af.format_markdown_artifact(body="x", title="A\n}\nинъекция")
        assert out.splitlines()[0].endswith("}") and len(out.splitlines()) == 5

    def test_blank_title_after_cleanup_falls_back_to_default(self):
        out = af.format_markdown_artifact(body="x", title=" \n ")
        assert 'title="Вердикт"' in out


class TestVerdictMarkdown:
    def test_non_dict(self):
        assert af.verdict_markdown_from_plugin_result("x") is None

    def test_top_level_and_nested(self):
        assert af.verdict_markdown_from_plugin_result({"verdict_markdown": "  v "}) == "v"
        assert af.verdict_markdown_from_plugin_result({"result": {"verdict_markdown": " n "}}) == "n"

    def test_blank_or_missing(self):
        assert af.verdict_markdown_from_plugin_result({"verdict_markdown": "  ", "result": {"verdict_markdown": 5}}) is None
        assert af.verdict_markdown_from_plugin_result({"result": "x"}) is None


class TestFindingsFallback:
    def test_non_dict_and_non_dict_payload(self):
        assert af.findings_fallback_markdown(None) == ""
        assert af.findings_fallback_markdown({"result": [1]}).startswith("# Аудит денежного потока")

    def test_builds_report(self):
        result = {"result": {"status": "fail", "message": "есть ошибки", "deterministic_findings": [
            {"where": "B2", "type": "SIGN", "detail": "минус"}, "junk", {}, {"type": "X", "where": "C3"},
        ]}}
        md = af.findings_fallback_markdown(result)
        assert md.startswith("# Аудит денежного потока\n\n**Статус:** `fail`\n\nесть ошибки")
        assert "- **[SIGN]** B2 — минус" in md and "- **[?]**" in md and "- **[X]** C3" in md
        assert md.count("- **[") == 3

    def test_flat_payload_without_findings(self):
        assert af.findings_fallback_markdown({"message": "", "deterministic_findings": "no"}) == "# Аудит денежного потока\n\n**Статус:** `unknown`"

    def test_findings_are_capped_at_fifty(self):
        findings = [{"type": "T", "where": str(i), "detail": "d"} for i in range(80)]
        assert af.findings_fallback_markdown({"deterministic_findings": findings}).count("- **[T]**") == 50


class TestBuildCashFlowArtifact:
    def test_uses_verdict(self):
        out = af.build_cash_flow_artifact_from_invoke({"verdict_markdown": "# V"})
        assert 'identifier="cash-flow-audit-verdict"' in out and "# V" in out and "денежного потока" in out

    def test_falls_back_to_findings(self):
        assert "Статус" in af.build_cash_flow_artifact_from_invoke({"status": "ok"})

    def test_nothing_to_show(self):
        assert af.build_cash_flow_artifact_from_invoke(None) == ""


class TestExtractPluginArtifacts:
    def item(self, output, tool="audit_cash_flow_model", success=True):
        return {"tool": tool, "success": success, "output": output}

    def test_filters_items(self):
        items = [
            "junk", self.item("{}", success=False), self.item("{}", tool="other"),
            self.item(json.dumps({"ok": False})), self.item(json.dumps(["x"])),
        ]
        assert af.extract_plugin_artifacts_from_tool_results(items) == ""
        assert af.extract_plugin_artifacts_from_tool_results(None) == ""

    def test_ready_artifact_block_is_preferred(self):
        out = af.extract_plugin_artifacts_from_tool_results([self.item(json.dumps({"ok": True, "artifact_markdown": " :::artifact\nA\n::: "}))])
        assert out == ":::artifact\nA\n:::"

    def test_builds_from_result(self):
        out = af.extract_plugin_artifacts_from_tool_results([self.item({"ok": True, "result": {"verdict_markdown": "# V"}})])
        assert "# V" in out and out.startswith(":::artifact")

    def test_empty_result_still_yields_a_status_only_report(self):
        out = af.extract_plugin_artifacts_from_tool_results([self.item({"ok": True, "result": {}})])
        assert out.startswith(":::artifact") and "`unknown`" in out

    def test_raw_text_with_artifact_is_kept(self):
        assert af.extract_plugin_artifacts_from_tool_results([self.item(" :::artifact x ")]) == ":::artifact x"

    def test_raw_text_without_artifact_is_ignored(self):
        assert af.extract_plugin_artifacts_from_tool_results([self.item("просто текст")]) == ""

    def test_several_blocks_are_joined(self):
        a = self.item({"ok": True, "artifact_markdown": "A"})
        b = self.item({"ok": True, "artifact_markdown": "B"})
        assert af.extract_plugin_artifacts_from_tool_results([a, b]) == "A\n\nB"


class TestAppendArtifacts:
    def test_empty_block_keeps_answer(self):
        assert af.append_artifacts_to_answer("ответ  ", "  ") == "ответ"
        assert af.append_artifacts_to_answer(None, "") == ""

    def test_appends_block(self):
        assert af.append_artifacts_to_answer("ответ", "BLOCK") == "ответ\n\nBLOCK"
        assert af.append_artifacts_to_answer(None, "BLOCK") == "BLOCK"

    def test_does_not_duplicate_existing_verdict(self):
        base = ':::artifact{identifier="cash-flow-audit-verdict"}\nx\n:::'
        assert af.append_artifacts_to_answer(base, "BLOCK") == base


class TestCompactResult:
    def test_non_dict_is_stringified_and_cut(self):
        assert pt._compact_result_for_llm("x" * 3000) == {"raw": "x" * 2000}
        assert pt._compact_result_for_llm(None) == {"raw": "None"}

    def test_non_dict_findings_are_skipped_but_counted(self):
        out = pt._compact_result_for_llm({"result": {"status": "ok", "deterministic_findings": ["junk", {"where": "B2", "type": "T"}]}})
        assert out["findings_total"] == 2 and out["deterministic_findings"] == [{"where": "B2", "type": "T", "n": "", "detail": ""}]
        assert out["findings_note"].startswith("Показаны первые 1 из 2")


class TestResolveAgentPluginIds:
    @pytest.mark.parametrize(
        "profile,expected",
        [
            (None, []),
            ("x", []),
            ({}, []),
            ({"plugin_ids": ["a", " ", "b "]}, ["a", "b"]),
            ({"plugin_ids": ["a"], "plugins_enabled": False}, []),
            ({"plugin_ids": ["a"], "plugins_enabled": True}, ["a"]),
            ({"plugin_ids": "a"}, []),
            ({"plugins_enabled": True}, []),
        ],
    )
    def test_resolve(self, profile, expected):
        assert ob.resolve_agent_plugin_ids(profile) == expected


class FakeTool:
    def __init__(self, name):
        self.name = name


class FakeOrchestrator:
    def __init__(self, names=()):
        self.tools = [FakeTool(n) for n in names]
        self.tools_by_name = {t.name: t for t in self.tools}
        self.tool_status = {t.name: True for t in self.tools}


class BareOrchestrator:
    """Оркестратор без tools_by_name/tool_status."""

    def __init__(self):
        self.tools = []


@pytest.fixture
def plugin_tools(monkeypatch):
    state = type("S", (), {"calls": [], "tools": [FakeTool("audit_cash_flow_model")]})()

    def fake_get(ids):
        state.calls.append(list(ids))
        return list(state.tools) if ids else []

    monkeypatch.setattr(pt, "get_plugin_tools", fake_get)
    return state


class TestAttachPluginTools:
    async def test_attaches_tools_for_plugin_ids(self, plugin_tools):
        orch = FakeOrchestrator(["builtin"])
        n = await ob.attach_plugin_tools_to_orchestrator(orch, {"__plugin_ids__": ["cash-flow"]})
        assert n == 1 and plugin_tools.calls == [["cash-flow"]]
        assert [t.name for t in orch.tools] == ["builtin", "audit_cash_flow_model"]
        assert orch.tools_by_name["audit_cash_flow_model"] and orch.tool_status["audit_cash_flow_model"] is True
        assert orch._dynamic_plugin_tool_names == {"audit_cash_flow_model"}

    async def test_ids_come_from_agent_profile_when_not_given(self, plugin_tools):
        orch = FakeOrchestrator()
        await ob.attach_plugin_tools_to_orchestrator(orch, {"agent_profile": {"plugin_ids": ["cash-flow"]}})
        assert plugin_tools.calls == [["cash-flow"]]

    async def test_non_list_ids_and_bad_profile(self, plugin_tools):
        orch = FakeOrchestrator()
        assert await ob.attach_plugin_tools_to_orchestrator(orch, {"__plugin_ids__": "cash-flow", "agent_profile": "x"}) == 0
        assert plugin_tools.calls == [[]]

    async def test_prerun_plugins_are_not_attached(self, plugin_tools):
        orch = FakeOrchestrator()
        n = await ob.attach_plugin_tools_to_orchestrator(orch, {"__plugin_ids__": ["cash-flow", "other"], "__plugins_prerun__": "cash-flow"})
        assert plugin_tools.calls == [["other"]] and n == 1

    async def test_all_plugins_prerun_means_no_tools(self, plugin_tools):
        orch = FakeOrchestrator()
        n = await ob.attach_plugin_tools_to_orchestrator(orch, {"__plugin_ids__": ["cash-flow"], "__plugins_prerun__": ["cash-flow"]})
        assert n == 0 and orch.tools == []

    async def test_previous_request_tools_are_removed(self, plugin_tools):
        orch = FakeOrchestrator(["builtin", "audit_cash_flow_model"])
        orch._dynamic_plugin_tool_names = {"audit_cash_flow_model"}
        n = await ob.attach_plugin_tools_to_orchestrator(orch, {})
        assert n == 0 and [t.name for t in orch.tools] == ["builtin"]
        assert "audit_cash_flow_model" not in orch.tools_by_name and "audit_cash_flow_model" not in orch.tool_status
        assert orch._dynamic_plugin_tool_names == set()

    async def test_orchestrator_without_registries(self, plugin_tools):
        orch = BareOrchestrator()
        orch._dynamic_plugin_tool_names = {"stale"}
        assert await ob.attach_plugin_tools_to_orchestrator(orch, {"__plugin_ids__": ["cash-flow"]}) == 1
        assert [t.name for t in orch.tools] == ["audit_cash_flow_model"]
        assert await ob.attach_plugin_tools_to_orchestrator(orch, {}) == 0 and orch.tools == []

    async def test_tool_without_name_uses_its_string_form(self, plugin_tools):
        plugin_tools.tools = ["raw-tool"]
        orch = FakeOrchestrator()
        await ob.attach_plugin_tools_to_orchestrator(orch, {"__plugin_ids__": ["cash-flow"]})
        assert orch.tools_by_name == {"raw-tool": "raw-tool"}
