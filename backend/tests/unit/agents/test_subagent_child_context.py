"""Контекст субагента перед вызовом модели: общий RAG, своя база, навыки, плагин."""

from types import SimpleNamespace

import pytest

from backend.agents.shared_rag import SHARED_RAG_KEY
from backend.agents.subagents import child_context as cc
from backend.services.plugins_direct import PluginDirectOutcome, PluginDirectRun

ROWS = [
    {"id": 1, "filename": "a.pdf"},
    {"id": 2, "filename": "b.pdf"},
    {"id": 3, "filename": "secret.pdf"},
]


class FakeRag:
    def __init__(self, rows=ROWS, boom_list=False):
        self.rows, self.boom_list = rows, boom_list

    async def kb_list_documents(self):
        if self.boom_list:
            raise RuntimeError("svc-rag down")
        return self.rows


@pytest.fixture
def rag(monkeypatch):
    """Поиск возвращает hits из state['hits']; rag_client подменён."""
    state = {"hits": [("текст про выручку", 0.9, 1, 0)], "searches": [], "boom_search": False}
    client = FakeRag()
    state["client"] = client

    async def search(client_, prompt, ids, k=None, strategy=None):
        state["searches"].append((prompt, list(ids), k, strategy))
        if state["boom_search"]:
            raise RuntimeError("timeout")
        return list(state["hits"])

    async def pull(client_, prompt, hits, id_to_name, ids, top_k, profile, strategy=None):
        return hits

    monkeypatch.setattr(cc, "kb_search_agent_documents", search)
    monkeypatch.setattr(cc, "_pull_named_documents", pull)
    monkeypatch.setattr("backend.app_state.rag_client", client, raising=False)
    monkeypatch.setattr("backend.app_state.get_rag_chat_top_k", lambda scope: 5, raising=False)
    return state


class TestSharedRagContext:
    async def test_no_ids(self, rag):
        assert await cc._build_shared_rag_context("q", []) == ("", None)

    async def test_no_client(self, rag, monkeypatch):
        monkeypatch.setattr("backend.app_state.rag_client", None, raising=False)
        assert await cc._build_shared_rag_context("q", [1]) == ("", None)

    async def test_fragments_inventory_and_trace(self, rag):
        ctx, trace = await cc._build_shared_rag_context("выручка", [1, 2])
        assert "<shared_knowledge>" in ctx and "</shared_knowledge>" in ctx
        assert "текст про выручку" in ctx and "a.pdf" in ctx
        # перечень только из общей базы, чужой файл не светится
        assert "secret.pdf" not in ctx
        assert trace["hits"] and trace["hits"][0]["file"] == "a.pdf"
        assert rag["searches"][0][0] == "выручка" and rag["searches"][0][2] == 5

    async def test_search_failure_still_gives_inventory(self, rag):
        rag["boom_search"] = True
        ctx, trace = await cc._build_shared_rag_context("q", [1])
        assert "a.pdf" in ctx and "текст про выручку" not in ctx and trace is None

    async def test_document_list_failure_gives_empty_context_without_names(self, rag):
        rag["client"].boom_list = True
        ctx, trace = await cc._build_shared_rag_context("q", [1])
        assert "текст про выручку" in ctx
        assert "ДОСТУПНЫЕ ФАЙЛЫ" not in ctx

    async def test_nothing_found_and_no_names(self, rag):
        rag["hits"] = []
        rag["client"].rows = []
        assert await cc._build_shared_rag_context("q", [1]) == ("", None)

    async def test_min_similarity_filter_failure_is_survived(self, rag, monkeypatch):
        monkeypatch.setattr(
            "backend.realtime.rag_evidence.rag_guard_env",
            lambda scope=None: (_ for _ in ()).throw(RuntimeError("x")),
        )
        ctx, _ = await cc._build_shared_rag_context("q", [1])
        assert "текст про выручку" in ctx


class TestChildKb:
    def _profile(self, **kw):
        base = {"file_search_enabled": True, "kb_document_ids": [1, 2]}
        base.update(kw)
        return base

    @pytest.mark.parametrize(
        "profile",
        [
            {},
            {"file_search_enabled": False, "kb_document_ids": [1]},
            {"file_search_enabled": True, "kb_document_ids": []},
            {"file_search_enabled": True, "kb_document_ids": "1"},
        ],
    )
    async def test_disabled_keeps_everything(self, rag, profile):
        assert await cc._apply_child_kb_context(profile, "p", "s", 2) == ("p", "s", None)
        assert rag["searches"] == []

    async def test_prompt_gets_inventory_and_fragments(self, rag):
        prompt, system, trace = await cc._apply_child_kb_context(self._profile(), "вопрос", "КАРТОЧКА", 2)
        assert prompt.endswith("\n\nвопрос")
        assert "База Знаний (постоянные документы):" in prompt and "текст про выручку" in prompt
        assert "ДОСТУПНЫЕ ФАЙЛЫ" in prompt and "secret.pdf" not in prompt
        assert system.startswith("КАРТОЧКА")  # правила CONTEXT дописаны после карточки
        assert len(system) > len("КАРТОЧКА")
        assert trace["hits"]
        assert rag["searches"][0][1] == [1, 2] and rag["searches"][0][3] == "auto"

    async def test_no_hits_still_lists_files_but_keeps_system(self, rag):
        rag["hits"] = []
        prompt, system, trace = await cc._apply_child_kb_context(self._profile(), "q", "S", 2)
        assert prompt.startswith("ДОСТУПНЫЕ ФАЙЛЫ") and prompt.endswith("\n\nq")
        assert trace is None
        assert system != "S"

    async def test_no_client(self, rag, monkeypatch):
        monkeypatch.setattr("backend.app_state.rag_client", None, raising=False)
        assert await cc._apply_child_kb_context(self._profile(), "p", "s", 2) == ("p", "s", None)

    async def test_search_error_is_swallowed(self, rag):
        rag["boom_search"] = True
        assert await cc._apply_child_kb_context(self._profile(), "p", "s", 2) == ("p", "s", None)

    async def test_already_shared_docs_are_not_searched_twice(self, rag):
        profile = self._profile(**{SHARED_RAG_KEY: [1]})
        await cc._apply_child_kb_context(profile, "p", "s", 2)
        assert rag["searches"][0][1] == [2]

    async def test_everything_already_shared(self, rag):
        profile = self._profile(**{SHARED_RAG_KEY: ["1", "2"]})
        assert await cc._apply_child_kb_context(profile, "p", "s", 2) == ("p", "s", None)
        assert rag["searches"] == []

    async def test_card_scope_limits_documents(self, rag):
        profile = self._profile(_scope_doc_ids=[2, 7])
        await cc._apply_child_kb_context(profile, "p", "s", 2)
        assert rag["searches"][0][1] == [2]

    async def test_empty_scope_means_nothing_to_search(self, rag):
        profile = self._profile(_scope_doc_ids=[])
        assert await cc._apply_child_kb_context(profile, "p", "s", 2) == ("p", "s", None)
        assert rag["searches"] == []

    async def test_scope_skips_non_numeric_ids(self, rag):
        profile = self._profile(kb_document_ids=["x", 2], _scope_doc_ids=[2])
        await cc._apply_child_kb_context(profile, "p", "s", 2)
        assert rag["searches"][0][1] == [2]


class TestSkillsAndArtifacts:
    @pytest.fixture
    def skills(self, monkeypatch):
        state = {"ctx": {}, "set": [], "result": ("СИС+НАВЫК", "стрип", ["s1"], ["tool_a", " "], {}),
                 "artifacts": "", "boom": False}

        async def apply(**kw):
            if state["boom"]:
                raise RuntimeError("skills down")
            state["apply_kw"] = kw
            return state["result"]

        monkeypatch.setattr("backend.services.skills.apply_skills_to_chat", apply)
        monkeypatch.setattr("backend.tools.tool_context.get_tool_context", lambda: state["ctx"])
        monkeypatch.setattr("backend.tools.tool_context.set_tool_context", lambda c: state["set"].append(c))
        monkeypatch.setattr(
            "backend.prompts.artifacts.maybe_artifacts_prompt_for_agent", lambda p: state["artifacts"]
        )
        return state

    async def test_skills_extend_system_prompt_and_tools(self, skills):
        prompt, system, tools = await cc._apply_child_skills_and_artifacts(
            {"agent_id": 2}, "Запрос #skill", "СИС", {"user_id": "u"}, 2
        )
        assert system == "СИС+НАВЫК" and tools == ["tool_a"]
        assert skills["apply_kw"]["agent_profile"] == {"agent_id": 2}
        assert skills["apply_kw"]["current_user"] == {"user_id": "u"}

    async def test_lazy_skill_ids_extend_tool_context(self, skills):
        skills["ctx"] = {"__skill_ids__": ["old", "s1"]}
        await cc._apply_child_skills_and_artifacts({}, "p", "s", {"user_id": "u"}, 2)
        (ctx,) = skills["set"]
        assert ctx["__skill_ids__"] == ["old", "s1"] and ctx["current_user"] == {"user_id": "u"}

    async def test_skills_failure_leaves_prompts(self, skills):
        skills["boom"] = True
        prompt, system, tools = await cc._apply_child_skills_and_artifacts({}, "p", "СИС", None, 2)
        assert (prompt, system, tools) == ("p", "СИС", [])

    async def test_artifacts_block_is_appended(self, skills):
        skills["result"] = (None, "", [], [], {})
        skills["artifacts"] = "АРТЕФАКТЫ"
        _, system, _ = await cc._apply_child_skills_and_artifacts({}, "p", "СИС", None, 2)
        assert system == "АРТЕФАКТЫ" or system.endswith("АРТЕФАКТЫ")


class TestChildPlugin:
    RUN = PluginDirectRun(
        plugin_id="cashflow", label="Денежный поток", file_name="f.xlsx",
        minio_object="o", minio_bucket="b", prompt="",
    )

    @pytest.fixture
    def plugin(self, monkeypatch):
        state = {
            "ids": ["cashflow"], "run": self.RUN,
            "outcome": PluginDirectOutcome(ok=True, elapsed_sec=1.0, verdict_markdown="ВЕРДИКТ", artifact_markdown="АРТ"),
            "events": [], "boom_run": False,
        }

        monkeypatch.setattr("backend.plugins.orchestrator_bridge.resolve_agent_plugin_ids", lambda p: state["ids"])
        monkeypatch.setattr("backend.services.plugins_direct.pick_plugin_run", lambda ids, att, prompt, chat_mode="chat": state["run"])

        async def run_direct(run, chat_mode="chat"):
            if state["boom_run"]:
                raise RuntimeError("plugin down")
            return state["outcome"]

        async def start(emit, **kw):
            state["events"].append(("start", kw))

        async def end(emit, **kw):
            state["events"].append(("end", kw))

        monkeypatch.setattr("backend.services.plugins_direct.run_plugin_direct", run_direct)
        monkeypatch.setattr("backend.mcp.events.emit_mcp_tool_start", start)
        monkeypatch.setattr("backend.mcp.events.emit_mcp_tool_end", end)
        return state

    async def test_no_plugins(self, plugin):
        plugin["ids"] = []
        assert await cc._run_child_plugin({}, "p", "s", None, 2, None) == ("p", "s", "")

    async def test_ids_lookup_failure(self, plugin, monkeypatch):
        def boom(p):
            raise RuntimeError("x")

        monkeypatch.setattr("backend.plugins.orchestrator_bridge.resolve_agent_plugin_ids", boom)
        assert await cc._run_child_plugin({}, "p", "s", None, 2, None) == ("p", "s", "")

    async def test_no_attachment_adds_hint_only(self, plugin):
        plugin["run"] = None
        prompt, system, art = await cc._run_child_plugin({}, "p", "СИС", None, 2, None)
        assert prompt == "p" and art == "" and system.startswith("СИС\n\n")
        assert "подходящего файла" in system
        assert plugin["events"] == []

    async def test_success_returns_verdict_note_and_artifact(self, plugin):
        prompt, system, art = await cc._run_child_plugin({}, "p", "СИС", ["att"], 2, "emit")
        assert "ВЕРДИКТ" in prompt and prompt.endswith("\n\np")
        assert "уже запущен системой" in system and art == "АРТ"
        kinds = [k for k, _ in plugin["events"]]
        assert kinds == ["start", "end"]
        end = plugin["events"][1][1]
        assert end["success"] is True and end["tool"] == "plugin:cashflow"
        assert end["result_preview"] == "ВЕРДИКТ" and end["arguments"]["agent_id"] == 2

    async def test_failed_plugin_has_no_artifact(self, plugin):
        plugin["outcome"] = PluginDirectOutcome(ok=False, elapsed_sec=1.0, error="сломался", artifact_markdown="АРТ")
        prompt, system, art = await cc._run_child_plugin({}, "p", "", ["att"], 2, None)
        assert "не смог обработать" in prompt and "сломался" in prompt
        assert art == ""
        end = plugin["events"][1][1]
        assert end["success"] is False and end["error"] == "сломался" and end["result_preview"] is None

    async def test_plugin_exception_keeps_prompts(self, plugin):
        plugin["boom_run"] = True
        assert await cc._run_child_plugin({}, "p", "s", ["att"], 2, None) == ("p", "s", "")


class TestPluginArtifact:
    def test_empty_artifact_keeps_text(self):
        assert cc._with_child_plugin_artifact("т", "") == "т"

    def test_artifact_is_appended(self, monkeypatch):
        monkeypatch.setattr(
            "backend.plugins.artifact_format.append_artifacts_to_answer", lambda t, a: f"{t}+{a}"
        )
        assert cc._with_child_plugin_artifact("т", "А") == "т+А"

    def test_failure_keeps_text(self, monkeypatch):
        def boom(t, a):
            raise RuntimeError("x")

        monkeypatch.setattr("backend.plugins.artifact_format.append_artifacts_to_answer", boom)
        assert cc._with_child_plugin_artifact("т", "А") == "т"
