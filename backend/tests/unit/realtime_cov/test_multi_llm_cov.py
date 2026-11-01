"""realtime/handlers._handle_multi_llm: сравнение моделей (RAG, навыки, MCP, стрим, стоп, сохранение)."""

import asyncio
import sys
import types

import pytest

from backend.rag_query.prompts import RAG_STRICT_NOT_FOUND_MESSAGE
from backend.realtime import handlers as h
from backend.realtime.rag_evidence import RAG_SEARCH_FAILED_MESSAGE
from backend.settings.rag_client import RagReindexInProgress

pytestmark = pytest.mark.unit

USER = {"user_id": "alice", "username": "alice"}


class Env:
    """Управляющий объект: настройки фейков и журнал вызовов."""

    def __init__(self):
        self.models = ["model-a", "model-b"]
        self.answers = {}  # модель -> ответ/исключение/callable
        self.ask_calls = []
        self.saved = []
        self.saved_project = []
        self.save_error = None
        self.skills_result = ("", "", [], [], {})
        self.skills_error = None
        self.cpm = None
        self.feedback = ""
        self.replace_calls = []
        self.replace_with = None
        self.mcp_calls = []
        self.mcp_result = None
        self.mcp_error = None
        self.mcp_events = []
        self.stream_script = []
        self.after_ask = None


@pytest.fixture
def env(monkeypatch, rag):
    e = Env()
    import backend.database.memory_service as mem
    import backend.mcp.chat_integration as chat_integration
    import backend.services.skills as skills

    monkeypatch.setattr(h, "get_model_comparison_models", lambda: list(e.models))

    def ask(*args, **kwargs):
        e.ask_calls.append((args, kwargs))
        model = args[5]
        cb = args[4]
        for chunk, acc, role in e.stream_script:
            if cb is not None:
                cb(chunk, acc, role)
        if e.after_ask:
            e.after_ask()
        ans = e.answers.get(model, f"ответ {model}")
        if isinstance(ans, Exception):
            raise ans
        return ans

    monkeypatch.setattr(h, "ask_agent", ask)

    async def save_entry(*a, **kw):
        if e.save_error:
            raise e.save_error
        e.saved.append((a, kw))

    async def save_project(*a, **kw):
        if e.save_error:
            raise e.save_error
        e.saved_project.append((a, kw))

    async def save_assistant(*a, **kw):
        e.saved.append((a, kw))

    monkeypatch.setattr(h, "save_dialog_entry", save_entry)
    monkeypatch.setattr(h, "save_assistant_response", save_assistant)
    monkeypatch.setattr(mem, "save_dialog_entry_to_project", save_project)

    async def feedback(uid, conversation_id=None):
        return e.feedback

    async def get_cpm(uid):
        return e.cpm

    monkeypatch.setattr(h, "build_user_feedback_system_block", feedback)
    monkeypatch.setattr(h, "get_user_prompt_manager", get_cpm)
    monkeypatch.setattr(h, "context_prompt_manager", None)

    async def apply_skills(**kw):
        if e.skills_error:
            raise e.skills_error
        return e.skills_result

    monkeypatch.setattr(skills, "apply_skills_to_chat", apply_skills)

    async def replace(ctx, answer, replacement):
        e.replace_calls.append((ctx, answer, replacement))
        return e.replace_with if e.replace_with is not None else answer

    monkeypatch.setattr(h, "maybe_replace_ungrounded", replace)

    async def run_mcp(**kw):
        e.mcp_calls.append(kw)
        if e.mcp_error:
            raise e.mcp_error
        for ev in e.mcp_events:
            await kw["emit_event"](dict(ev))
        return e.mcp_result

    monkeypatch.setattr(chat_integration, "run_mcp_for_chat", run_mcp)
    monkeypatch.setenv("RAG_DOCUMENTS_INVENTORY", "0")
    monkeypatch.setenv("RAG_ANSWER_SUMMARY", "0")
    return e


class CollectSio:
    def __init__(self, fail_complete_times=0):
        self.emitted = []
        self.fail_complete_times = fail_complete_times
        self._failed = 0

    async def emit(self, name, payload=None, room=None, **kw):
        if name == "multi_llm_complete" and self._failed < self.fail_complete_times:
            self._failed += 1
            raise RuntimeError("socket closed")
        self.emitted.append((name, payload, room))

    def payloads(self, name):
        return [p for n, p, _ in self.emitted if n == name]

    def names(self):
        return [n for n, _, _ in self.emitted]


async def call(env, sio=None, **over):
    sio = sio or CollectSio()
    h._begin_generation("s")
    args = dict(
        sio=sio, sid="s", data=over.pop("data", {"message_id": "m1"}), user_message=over.pop("user_message", "вопрос"),
        streaming=over.pop("streaming", False), conversation_id=over.pop("conversation_id", "c1"),
        use_kb_rag=False, use_memory_library_rag=False, loop=asyncio.get_running_loop(),
    )
    args.update(over)
    await h._handle_multi_llm(**args)
    await asyncio.sleep(0)
    return sio


class TestModelSelection:
    async def test_no_models(self, env):
        env.models = []
        sio = await call(env)
        assert sio.emitted == [("chat_error", {"error": "Модели не выбраны"}, "s")]

    async def test_subset_filters_to_allowed(self, env):
        sio = await call(env, models_subset=["model-b", "ghost"])
        assert [a[0][5] for a in env.ask_calls] == ["model-b"]
        assert sio.payloads("multi_llm_start")[0]["models"] == ["model-b"]

    async def test_subset_without_overlap(self, env):
        sio = await call(env, models_subset=["ghost"])
        assert sio.emitted[0][1]["error"] == "Указанная модель не входит в список multi-LLM"
        assert env.ask_calls == []


class TestPlainFlow:
    async def test_each_model_answers_and_result_is_saved(self, env):
        sio = await call(env, generation_started_at=None, current_user=USER)
        completes = {p["model"]: p for p in sio.payloads("multi_llm_complete")}
        assert set(completes) == {"model-a", "model-b"}
        assert completes["model-a"]["response"] == "ответ model-a" and completes["model-a"]["error"] is False
        assert completes["model-a"]["index"] == 0 and completes["model-b"]["total"] == 2
        assert all(room == "s" for _, _, room in sio.emitted)
        (args, kwargs), = env.saved
        assert args[0] == "assistant" and args[1] == "model-a:\nответ model-a\n\nmodel-b:\nответ model-b"
        meta = args[2]
        assert [s["model"] for s in meta["multi_llm_responses"]] == ["model-a", "model-b"]
        assert meta["generation_duration_sec"] == 1 and args[4] == "c1" and kwargs == {"user_id": "alice"}

    async def test_ask_agent_receives_parameters(self, env):
        env.models = ["m"]
        await call(env, streaming=False, inline_images=["img"], enable_thinking=True,
                   data={"temperature": 0.2, "max_tokens": 100})
        (args, kwargs), = env.ask_calls
        assert args[0] == "вопрос" and args[1] == [] and args[2] == 4096  # floor токенов
        assert args[3] is False and args[4] is None and args[5] == "m"
        assert kwargs["images"] == ["img"] and kwargs["temperature"] == 0.2 and kwargs["enable_thinking"] is True

    async def test_default_temperature_from_agent_profile(self, env):
        env.models = ["m"]
        await call(env, agent_profile={"temperature": 0.3, "max_tokens": 9000})
        (args, kwargs), = env.ask_calls
        assert kwargs["temperature"] == 0.3 and args[2] == 9000

    async def test_non_string_answer_becomes_empty(self, env):
        env.models = ["m"]
        env.answers["m"] = None
        sio = await call(env)
        assert sio.payloads("multi_llm_complete")[0]["response"] == ""
        assert env.saved[0][0][1] == "m:\n[пустой ответ]"

    async def test_model_error_is_reported_and_saved_as_error_slot(self, env):
        env.answers["model-a"] = RuntimeError("OOM")
        sio = await call(env)
        done = {p["model"]: p for p in sio.payloads("multi_llm_complete")}
        assert done["model-a"]["error"] is True and done["model-a"]["response"] == "Ошибка: Внутренняя ошибка сервера"
        assert done["model-b"]["error"] is False
        slots = env.saved[0][0][2]["multi_llm_responses"]
        assert [s["error"] for s in slots] == [True, False]

    async def test_streaming_chunks_and_roles(self, env):
        env.models = ["m"]
        env.stream_script = [("к", "к", "content"), ("", "", "heartbeat"), ("м", "м", "reasoning"),
                             ("ус", "кус", "content")]
        sio = await call(env, streaming=True)
        chunks = sio.payloads("multi_llm_chunk")
        assert [(c["chunk"], c["accumulated"]) for c in chunks] == [("к", "к"), ("ус", "кус")]
        assert env.ask_calls[0][0][4] is not None  # callback передан

    async def test_stream_callback_stops_when_generation_stopped(self, env):
        env.models = ["m"]
        results = []

        def script():
            h._stopped_upto["s"] = h._generation_seq["s"]

        env.after_ask = None
        # callback вызывается в потоке без контекста генерации: проверяем через ask-фейк в контексте
        orig = h.ask_agent

        def ask(*args, **kwargs):
            cb = args[4]
            script()
            results.append(cb("x", "x", "content"))
            results.append(cb("", "", "heartbeat"))
            return "ответ"

        h.ask_agent = ask
        try:
            sio = await call(env, streaming=True)
        finally:
            h.ask_agent = orig
        assert results == [False, False] and sio.payloads("multi_llm_chunk") == []

    async def test_stopped_generation_does_not_emit_complete_but_saves(self, env):
        env.models = ["m"]
        env.after_ask = lambda: h._stopped_upto.__setitem__("s", h._generation_seq["s"])
        sio = await call(env)
        assert sio.payloads("multi_llm_complete") == []
        assert env.saved[0][0][1] == "m:\nответ m"

    async def test_project_conversation_is_saved_into_project(self, env):
        env.models = ["m"]
        await call(env, project_id="p1", current_user=USER)
        (args, kwargs), = env.saved_project
        assert args[:3] == ("assistant", "m:\nответ m", "p1") and args[3] == "c1"
        assert kwargs["user_id"] == "alice" and env.saved == []

    async def test_no_conversation_means_no_save(self, env):
        await call(env, conversation_id=None)
        assert env.saved == [] and env.saved_project == []

    @pytest.mark.parametrize("error", [RuntimeError("mongo"), ValueError("boom")])
    async def test_save_errors_are_swallowed(self, env, error):
        env.save_error = error
        sio = await call(env)
        assert len(sio.payloads("multi_llm_complete")) == 2

    async def test_emit_failure_reports_error_slot(self, env):
        env.models = ["m"]
        sio = CollectSio(fail_complete_times=2)
        await call(env, sio=sio)
        done = sio.payloads("multi_llm_complete")
        assert len(done) == 1 and done[0]["error"] is True and "socket closed" in done[0]["response"]
        assert done[0]["model"] == "m" and done[0]["index"] == 0
        slot = env.saved[0][0][2]["multi_llm_responses"][0]
        assert slot["error"] is True and slot["content"] == ""
        assert env.saved[0][0][1] == "m:\n[пустой ответ]"

    async def test_slot_alternatives_are_serialized(self, env, monkeypatch):
        env.models = ["m"]
        real = asyncio

        class Proxy:
            def __getattr__(self, name):
                return getattr(real, name)

            async def gather(self, *aws, **kw):
                res = await real.gather(*aws, **kw)
                for r in res:
                    if isinstance(r, dict):
                        r["alternative_responses"] = ["a1", 2]
                        r["current_response_index"] = 1
                return res

        monkeypatch.setattr(h, "asyncio", Proxy())
        await call(env)
        slot = env.saved[0][0][2]["multi_llm_responses"][0]
        assert slot["alternative_responses"] == ["a1", "2"] and slot["current_response_index"] == 1


class TestRag:
    async def test_reindex_status_blocks_before_search(self, env, rag):
        rag.status = {"memory": {"reindexing": True}}
        sio = await call(env, use_memory_library_rag=True, current_user=USER)
        assert sio.names()[0] == "chat_info"
        done = sio.payloads("multi_llm_complete")
        assert [p["response"] for p in done] == [h.REINDEX_WAIT_MESSAGE] * 2
        assert env.ask_calls == []
        (args, kwargs), = env.saved
        assert kwargs["conversation_id"] == "c1" and args[0].startswith("model-a:\n")
        assert args[0].count(h.REINDEX_WAIT_MESSAGE) == 2
        assert rag.called("memory_rag_search") == []

    @pytest.mark.parametrize("method,kwargs", [
        ("project_rag_search", {"project_id": "p1"}),
        ("kb_list_documents", {"use_agent_scoped_kb": True, "agent_kb_doc_ids": [1]}),
        ("memory_rag_list_documents", {"use_memory_library_rag": True}),
    ])
    async def test_reindex_during_search_aborts(self, env, rag, method, kwargs):
        rag.errors[method] = RagReindexInProgress("reindex")
        sio = await call(env, **kwargs)
        assert sio.names()[0] == "chat_info" and env.ask_calls == []
        assert [p["response"] for p in sio.payloads("multi_llm_complete")] == [h.REINDEX_WAIT_MESSAGE] * 2

    async def test_project_context_and_named_document(self, env, rag):
        env.models = ["m"]
        rag.project_docs = [{"id": 5, "filename": "Смета.xlsx"}, {"id": 6, "filename": "other.txt"}]
        rag.project_hits = [("общий фрагмент", 0.9, 6, 0)]
        rag.by_document[("project", 5)] = [("из сметы", 0.8, 5, 1)]
        await call(env, project_id="p1", user_message="что в Смета.xlsx?", rag_strategy="hybrid")
        (args, kwargs), = env.ask_calls
        assert "Документы проекта (RAG):" in args[0]
        assert "общий фрагмент" in args[0] and "из сметы" in args[0]
        assert args[0].endswith("Вопрос: что в Смета.xlsx?")
        assert RAG_STRICT_NOT_FOUND_MESSAGE in kwargs["system_prompt"]
        searches = rag.called("project_rag_search")
        assert len(searches) == 2 and searches[1][2]["document_id"] == 5
        assert searches[0][2]["strategy"] == "hybrid"
        assert env.replace_calls[0][2] == RAG_STRICT_NOT_FOUND_MESSAGE

    async def test_project_search_failure_is_reported_as_failure_not_as_not_found(self, env, rag):
        # Сбой svc-rag при пустом контексте: пользователь видит сообщение о сбое поиска,
        # а не «не нашёл в базе документов».
        env.models = ["m"]
        rag.project_docs = [{"id": 1, "filename": "a.txt"}]
        rag.errors["project_rag_search"] = RuntimeError("svc-rag down")
        sio = await call(env, project_id="p1")
        assert env.ask_calls == []
        assert sio.payloads("multi_llm_complete")[0]["response"] == RAG_SEARCH_FAILED_MESSAGE

    async def test_project_search_failure_without_blocking_calls_llm(self, env, rag, monkeypatch):
        monkeypatch.setenv("RAG_BLOCK_ON_NO_EVIDENCE", "0")
        env.models = ["m"]
        rag.project_docs = [{"id": 1, "filename": "a.txt"}]
        rag.errors["project_rag_search"] = RuntimeError("svc-rag down")
        await call(env, project_id="p1")
        assert len(env.ask_calls) == 1 and env.ask_calls[0][0][0] == "вопрос"

    async def test_agent_kb_context_and_named_document(self, env, rag):
        env.models = ["m"]
        rag.kb_docs = [{"id": 1, "filename": "Регламент.pdf"}, {"id": 2, "filename": "чужой.pdf"}]
        rag.kb_hits = [("текст регламента", 0.7, 1, 0)]
        rag.by_document[("kb", 1)] = [("дотянутый кусок", 0.6, 1, 3)]
        await call(env, use_agent_scoped_kb=True, agent_kb_doc_ids=[1], user_message="Регламент.pdf: сроки?")
        text = env.ask_calls[0][0][0]
        assert text.startswith("База Знаний (документы агента):") and "дотянутый кусок" in text
        assert "чужой" not in text
        assert len(rag.called("kb_search")) == 2

    async def test_agent_kb_error_is_swallowed(self, env, rag):
        env.models = ["m"]
        rag.errors["kb_list_documents"] = RuntimeError("down")
        await call(env, use_agent_scoped_kb=True, agent_kb_doc_ids=[1])
        assert env.ask_calls[0][0][0] == "вопрос"

    async def test_memory_context_and_named_document(self, env, rag):
        env.models = ["m"]
        rag.memory_docs = [{"id": 9, "filename": "Инструкция.docx"}]
        rag.memory_hits = [("про настройку", 0.9, 9, 0)]
        rag.by_document[("memory", 9)] = [("из инструкции", 0.5, 9, 4)]
        await call(env, use_memory_library_rag=True, user_message="найди в Инструкция.docx")
        text = env.ask_calls[0][0][0]
        assert "Документы из настроек (библиотека памяти):" in text and "из инструкции" in text

    async def test_memory_error_is_swallowed(self, env, rag):
        env.models = ["m"]
        rag.errors["memory_rag_search"] = RuntimeError("down")
        await call(env, use_memory_library_rag=True)
        assert env.ask_calls[0][0][0] == "вопрос"

    async def test_all_sources_are_combined_with_inline_context(self, env, rag):
        env.models = ["m"]
        rag.project_docs = [{"id": 1, "filename": "p.txt"}]
        rag.project_hits = [("проект", 0.9, 1, 0)]
        rag.kb_docs = [{"id": 2, "filename": "k.txt"}]
        rag.kb_hits = [("база", 0.9, 2, 0)]
        rag.memory_docs = [{"id": 3, "filename": "m.txt"}]
        rag.memory_hits = [("память", 0.9, 3, 0)]
        await call(env, project_id="p", use_agent_scoped_kb=True, agent_kb_doc_ids=[2],
                   use_memory_library_rag=True, inline_context="вложенный текст")
        text = env.ask_calls[0][0][0]
        assert text.startswith("[Прикреплённый документ]\nвложенный текст\n\nДокументы из настроек")
        assert text.index("Документы из настроек") < text.index("База Знаний") < text.index("Документы проекта")

    async def test_inline_context_alone(self, env):
        env.models = ["m"]
        await call(env, inline_context="только файл")
        assert env.ask_calls[0][0][0] == "[Прикреплённый документ]\nтолько файл\n\n[Вопрос пользователя]\nвопрос"

    async def test_inventory_block_is_added_without_hits(self, env, rag, monkeypatch):
        monkeypatch.setenv("RAG_DOCUMENTS_INVENTORY", "1")
        env.models = ["m"]
        rag.project_docs = [{"id": 1, "filename": "список.txt"}]
        await call(env, project_id="p1")
        text = env.ask_calls[0][0][0]
        assert text.startswith("ДОСТУПНЫЕ ФАЙЛЫ") and "список.txt" in text
        assert RAG_STRICT_NOT_FOUND_MESSAGE in env.ask_calls[0][1]["system_prompt"]

    async def test_canned_answer_when_corpus_exists_but_no_context(self, env, rag):
        rag.project_docs = [{"id": 1, "filename": "a.txt"}]
        sio = await call(env, project_id="p1", generation_started_at=None, current_user=USER)
        assert env.ask_calls == []
        done = sio.payloads("multi_llm_complete")
        assert [p["response"] for p in done] == [RAG_STRICT_NOT_FOUND_MESSAGE] * 2
        assert sio.payloads("multi_llm_start")[0]["total_models"] == 2
        (args, kwargs), = env.saved_project
        assert args[2] == "p1" and args[1].count(RAG_STRICT_NOT_FOUND_MESSAGE) == 2

    async def test_canned_answer_saved_to_dialog_without_project(self, env, rag):
        rag.memory_docs = [{"id": 1, "filename": "a.txt"}]
        await call(env, use_memory_library_rag=True)
        assert env.ask_calls == [] and len(env.saved) == 1 and env.saved[0][0][4] == "c1"

    async def test_canned_save_error_is_swallowed(self, env, rag):
        rag.memory_docs = [{"id": 1, "filename": "a.txt"}]
        env.save_error = RuntimeError("mongo")
        sio = await call(env, use_memory_library_rag=True)
        assert len(sio.payloads("multi_llm_complete")) == 2

    async def test_canned_without_conversation_is_not_saved(self, env, rag):
        rag.memory_docs = [{"id": 1, "filename": "a.txt"}]
        await call(env, use_memory_library_rag=True, conversation_id=None)
        assert env.saved == []

    async def test_ungrounded_answer_is_replaced(self, env, rag):
        env.models = ["m"]
        rag.memory_hits = [("факт", 0.9, 1, 0)]
        env.replace_with = "заменено"
        sio = await call(env, use_memory_library_rag=True)
        assert sio.payloads("multi_llm_complete")[0]["response"] == "заменено"


class TestSystemPrompt:
    async def test_prompt_manager_receives_agent_and_project_prompts(self, env):
        env.models = ["m"]
        seen = []

        class Cpm:
            def resolve_chat_system_prompt(self, model, agent_system_prompt="", project_instructions=""):
                seen.append((model, agent_system_prompt, project_instructions))
                return "из менеджера"

        env.cpm = Cpm()
        env.feedback = "ФИДБЕК"
        await call(env, agent_profile={"system_prompt": "Я юрист"}, project_instructions="инструкции проекта")
        assert seen == [("m", "Я юрист", "инструкции проекта")]
        sp = env.ask_calls[0][1]["system_prompt"]
        assert "из менеджера" in sp and "ФИДБЕК" in sp

    @pytest.mark.parametrize("instr,agent,expected", [
        (" инструкции ", "агент", "инструкции"), ("", " агент ", "агент"), ("  ", "", None),
    ])
    async def test_fallback_without_prompt_manager(self, env, instr, agent, expected):
        env.models = ["m"]
        await call(env, agent_profile={"system_prompt": agent}, project_instructions=instr)
        sp = env.ask_calls[0][1]["system_prompt"]
        assert sp == expected

    async def test_plugins_and_artifacts_blocks_are_appended(self, env):
        env.models = ["m"]
        await call(env, agent_profile={"system_prompt": "База", "plugins_enabled": True, "plugin_ids": ["cash-flow"],
                                       "artifacts_enabled": True})
        sp = env.ask_calls[0][1]["system_prompt"]
        assert sp.startswith("База") and "cash-flow" in sp and ":::artifact" in sp

    async def test_prompt_injection_failures_are_swallowed(self, env, monkeypatch):
        import backend.plugins.tools as ptools
        import backend.prompts.artifacts as art

        def boom(*a, **k):
            raise RuntimeError("x")

        monkeypatch.setattr(ptools, "build_plugins_system_append", boom)
        monkeypatch.setattr(art, "maybe_artifacts_prompt_for_agent", boom)
        env.models = ["m"]
        await call(env, agent_profile={"system_prompt": "База"})
        assert env.ask_calls[0][1]["system_prompt"] == "База"

    async def test_skill_block_is_appended(self, env):
        env.models = ["m"]
        env.skills_result = ("\n[SKILL] правила навыка", "", [], [], {})
        await call(env, agent_profile={"system_prompt": "База"})
        assert env.ask_calls[0][1]["system_prompt"] == "База\n\n[SKILL] правила навыка"


class TestSkills:
    async def test_lazy_skills_and_allowed_tools_update_tool_ids(self, env, monkeypatch):
        import backend.tools.tool_context as tc

        saved_ctx = {}
        monkeypatch.setattr(tc, "get_tool_context", lambda: {"old": 1})
        monkeypatch.setattr(tc, "set_tool_context", lambda ctx: saved_ctx.update(ctx))
        env.models = ["m"]
        env.skills_result = ("", "", ["skill-1"], ["tool-x", "tool-a"], {"manual": ["my-pptx"]})
        data = {"tool_ids": ["tool-a"], "message_id": "m1"}
        await call(env, data=data, current_user=USER)
        assert saved_ctx == {"old": 1, "__skill_ids__": ["skill-1"], "current_user": USER}
        assert data["tool_ids"] == ["tool-a", "tool-x"]
        # Презентационный навык: токенов не меньше жёсткого минимума
        assert env.mcp_calls[0]["max_tokens"] >= h._PRESENTATION_MAX_TOKENS_FLOOR

    async def test_only_allowed_tools_without_lazy_ids(self, env, monkeypatch):
        import backend.tools.tool_context as tc

        saved_ctx = {}
        monkeypatch.setattr(tc, "get_tool_context", lambda: None)
        monkeypatch.setattr(tc, "set_tool_context", lambda ctx: saved_ctx.update(ctx))
        env.models = ["m"]
        env.skills_result = ("", "", [], ["tool-x"], {})
        data = {"mcp_tool_ids": ["t0"]}
        await call(env, data=data, current_user=USER)
        assert saved_ctx == {"current_user": USER} and data["tool_ids"] == ["t0", "tool-x"]

    async def test_tag_mention_failure_is_ignored(self, env, monkeypatch):
        import backend.services.tag_mentions as tm

        def boom(text):
            raise RuntimeError("x")

        monkeypatch.setattr(tm, "strip_tag_mentions", boom)
        env.models = ["m"]
        await call(env, user_message="  $skill вопрос")
        assert env.ask_calls[0][0][0].strip().endswith("вопрос")

    async def test_skills_failure_is_swallowed(self, env):
        env.models = ["m"]
        env.skills_error = RuntimeError("db down")
        await call(env)
        assert len(env.ask_calls) == 1


class TestMcp:
    async def test_mcp_result_replaces_plain_call(self, env):
        env.models = ["m"]
        env.mcp_result = types.SimpleNamespace(content="итог mcp", mode="tools", tool_calls_executed=2)
        env.mcp_events = [{"type": "tool_call", "name": "t"}]
        sio = await call(env, data={"tool_ids": ["t1"], "message_id": "m1"}, current_user=USER, streaming=True,
                         agent_profile={"temperature": 0.4})
        assert env.ask_calls == []
        kw = env.mcp_calls[0]
        assert kw["tool_ids"] == ["t1"] and kw["model_path"] == "m" and kw["temperature"] == 0.4
        assert kw["message_id"] == "m1" and kw["history"] == [] and kw["chat_id"] == "c1"
        chunk = sio.payloads("multi_llm_chunk")[0]
        assert chunk["chunk"] == "итог mcp" and chunk["model"] == "m"
        event = sio.payloads("chat_mcp_event")[0]
        assert event["model"] == "m" and event["type"] == "tool_call"
        done = sio.payloads("multi_llm_complete")[0]
        assert done["response"] == "итог mcp" and done["mcp_mode"] == "tools" and done["mcp_tool_calls"] == 2
        assert sio.payloads("multi_llm_start")[0]["mcp_enabled"] is True

    async def test_mcp_answer_is_checked_for_grounding(self, env, rag):
        env.models = ["m"]
        rag.memory_hits = [("факт", 0.9, 1, 0)]
        env.mcp_result = types.SimpleNamespace(content="ответ", mode="tools", tool_calls_executed=0)
        env.replace_with = "проверено"
        sio = await call(env, data={"tool_ids": ["t1"]}, current_user=USER, use_memory_library_rag=True)
        assert sio.payloads("multi_llm_complete")[0]["response"] == "проверено"

    async def test_empty_mcp_content_without_streaming(self, env):
        env.models = ["m"]
        env.mcp_result = types.SimpleNamespace(content=None, mode="x", tool_calls_executed=0)
        sio = await call(env, data={"tool_ids": ["t1"]}, current_user=USER, streaming=True)
        assert sio.payloads("multi_llm_chunk") == []
        assert sio.payloads("multi_llm_complete")[0]["response"] == ""

    async def test_subagents_enable_mcp_without_tool_ids(self, env):
        env.models = ["m"]
        env.mcp_result = types.SimpleNamespace(content="от субагента", mode="sub", tool_calls_executed=1)
        await call(env, current_user=USER, agent_profile={"subagents": {"enabled": True}})
        assert len(env.mcp_calls) == 1 and env.ask_calls == []

    async def test_mcp_none_falls_back_to_plain_llm(self, env):
        env.models = ["m"]
        env.mcp_result = None
        sio = await call(env, data={"tool_ids": ["t1"]}, current_user=USER)
        assert len(env.mcp_calls) == 1 and len(env.ask_calls) == 1
        assert sio.payloads("multi_llm_complete")[0]["response"] == "ответ m"

    async def test_mcp_error_falls_back_to_plain_llm(self, env):
        env.models = ["m"]
        env.mcp_error = RuntimeError("mcp down")
        sio = await call(env, data={"tool_ids": ["t1"]}, current_user=USER)
        assert len(env.ask_calls) == 1 and sio.payloads("multi_llm_complete")[0]["error"] is False

    async def test_images_disable_mcp(self, env):
        env.models = ["m"]
        await call(env, data={"tool_ids": ["t1"]}, current_user=USER, inline_images=["i"])
        assert env.mcp_calls == [] and len(env.ask_calls) == 1

    async def test_no_user_disables_mcp(self, env):
        env.models = ["m"]
        await call(env, data={"tool_ids": ["t1"]}, current_user=None)
        assert env.mcp_calls == []
