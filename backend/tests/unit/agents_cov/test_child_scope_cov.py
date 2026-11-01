"""child_context и scope: ветки сбоев и пустых результатов."""

import sys
from types import SimpleNamespace

import pytest

from backend.agents.subagents import child_context as cc
from backend.agents.subagents import scope

pytestmark = pytest.mark.unit

NAMES = {2: "Заявка на БГ.docx", 3: "ВНД_Регламент НЭР.docx"}


@pytest.fixture
def rag(monkeypatch):
    state = SimpleNamespace(hits=[], searches=[])
    client = SimpleNamespace()

    async def list_docs():
        return [{"id": k, "filename": v} for k, v in NAMES.items()]

    client.kb_list_documents = list_docs

    async def search(client_, prompt, ids, k=None, strategy=None):
        state.searches.append(ids)
        return list(state.hits)

    async def pull(client_, prompt, hits, id_to_name, ids, top_k, profile, strategy=None):
        return hits

    monkeypatch.setattr(cc, "kb_search_agent_documents", search)
    monkeypatch.setattr(cc, "_pull_named_documents", pull)
    monkeypatch.setattr("backend.app_state.rag_client", client, raising=False)
    monkeypatch.setattr("backend.app_state.get_rag_chat_top_k", lambda scope_name: 5, raising=False)
    return state


class TestSharedRagImportFailure:
    async def test_missing_evidence_module_gives_empty_context(self, rag, monkeypatch):
        monkeypatch.setitem(sys.modules, "backend.realtime.rag_evidence", None)

        assert await cc._build_shared_rag_context("q", [2]) == ("", None)


class TestChildKbNothingToAdd:
    async def test_no_inventory_and_no_hits_keep_prompts(self, rag, monkeypatch):
        monkeypatch.setattr("backend.realtime.rag_evidence.build_rag_inventory_block", lambda sections: "")
        profile = {"file_search_enabled": True, "kb_document_ids": [2]}

        prompt, system, trace = await cc._apply_child_kb_context(profile, "вопрос", "КАРТОЧКА", 5)

        assert (prompt, system) == ("вопрос", "КАРТОЧКА")
        assert rag.searches == [[2]]  # поиск был, но добавить нечего


class TestSkillsAndArtifactsFailures:
    @pytest.fixture
    def skills(self, monkeypatch):
        async def apply(**kw):
            return ("СИС", "стрип", [], [], {})

        monkeypatch.setattr("backend.services.skills.apply_skills_to_chat", apply)
        monkeypatch.setattr("backend.tools.tool_context.get_tool_context", lambda: {})
        monkeypatch.setattr("backend.tools.tool_context.set_tool_context", lambda c: None)
        monkeypatch.setattr("backend.prompts.artifacts.maybe_artifacts_prompt_for_agent", lambda p: "")

    async def test_tag_mention_stripping_error_is_ignored(self, skills, monkeypatch):
        def boom(text):
            raise RuntimeError("strip")

        monkeypatch.setattr("backend.services.tag_mentions.strip_tag_mentions", boom)

        prompt, system, tools = await cc._apply_child_skills_and_artifacts({}, "вопрос #skill", "С", None, 2)

        assert system == "СИС" and tools == []
        assert prompt  # текст сохранён после strip_skill_mentions

    async def test_artifacts_failure_keeps_system_prompt(self, skills, monkeypatch):
        def boom(profile):
            raise RuntimeError("artifacts")

        monkeypatch.setattr("backend.prompts.artifacts.maybe_artifacts_prompt_for_agent", boom)

        _, system, _ = await cc._apply_child_skills_and_artifacts({}, "p", "С", None, 2)

        assert system == "СИС"


class TestScopeEdgeCases:
    def test_restriction_word_inside_the_list_is_not_a_source(self):
        # «ТОЛЬКО» капсом внутри перечня после «только» не должно считаться аббревиатурой источника
        result = scope._card_file_scope("Смотри только ТОЛЬКО БГ", {1: "Заявка на БГ.docx", 2: "ВНД.docx"})

        assert result is not None and result["ids"] == [1]

    async def test_restriction_without_matching_files_gives_no_scope(self, rag):
        profile = {"system_prompt": "Отвечай только в рублях РФ", "kb_document_ids": [3]}

        assert await scope._resolve_card_scope(profile, [], 7) is None
        assert "_scope_doc_ids" not in profile
