"""run_direct_or_chain: один агент / цепочка, на фейках realtime.handlers."""

import pytest

from backend.agents.chain import runner
from backend.realtime import handlers as rt


class FakeSio:
    def __init__(self):
        self.events = []

    async def emit(self, event, payload=None, room=None):
        self.events.append((event, payload, room))

    def named(self, name):
        return [p for e, p, _ in self.events if e == name]


@pytest.fixture
def env(monkeypatch):
    """Подменяет всё, что раннер берёт из realtime.handlers и соседей."""
    state = {
        "chain": [],
        "replies": [],
        "calls": [],
        "stopped_after": None,
        "saved": [],
        "save_error": None,
        "bound": [],
        "queries": [],
    }

    async def resolve(primary_id, profile, user_id, user=None):
        state["resolve_args"] = (primary_id, user_id, user)
        return state["chain"]

    async def handle_direct(sio, sid, data, message, *args, **kw):
        state["calls"].append({"data": data, "message": message, "args": args, "kw": kw})
        if not state["replies"]:
            return None
        return state["replies"].pop(0)

    async def enrich(profile, user_id):
        out = dict(profile)
        if profile.get("_eff"):
            out["effective_model_settings"] = profile["_eff"]
        return out

    async def step_query(profile, question, *, step_label):
        state["queries"].append((step_label, question))
        return f"поиск:{profile.get('name')}"

    async def save(visible, meta, **kw):
        if state["save_error"]:
            raise state["save_error"]
        state["saved"].append((visible, meta, kw))

    async def flush(cb):
        state["flushed"] = True

    monkeypatch.setattr(runner, "resolve_agent_chain", resolve)
    monkeypatch.setattr(runner, "chain_step_search_query", step_query)
    monkeypatch.setattr(rt, "_handle_direct", handle_direct)
    monkeypatch.setattr(rt, "enrich_agent_profile_with_user_settings", enrich)
    monkeypatch.setattr(rt, "bind_user_model_runtime", lambda ms: state["bound"].append(ms))
    monkeypatch.setattr(rt, "save_assistant_response", save)
    monkeypatch.setattr(rt, "_flush_ui_stream_cb", flush)
    monkeypatch.setattr(rt, "_generation_duration_sec", lambda t: 3)

    def stopped(sid):
        n = state["stopped_after"]
        return n is not None and len(state["calls"]) >= n

    monkeypatch.setattr(rt, "_generation_stopped", stopped)
    return state


async def _run(sio, **over):
    kw = dict(
        sio=sio,
        sid="sid1",
        data={"agent_id": 10},
        user_message="вопрос",
        streaming=True,
        conversation_id="conv",
        history=[],
        use_kb_rag=True,
        use_memory_library_rag=False,
        agent_profile={"agent_id": 10},
        sync_stream_cb=None,
        loop=None,
        current_user={"user_id": "u1"},
    )
    kw.update(over)
    return await runner.run_direct_or_chain(**kw)


class TestSingleAgent:
    async def test_single_profile_delegates(self, env):
        env["chain"] = [{"agent_id": 10}]
        sio = FakeSio()
        await _run(sio, rag_strategy="deep", inline_context="ctx")
        assert len(env["calls"]) == 1
        call = env["calls"][0]
        assert call["message"] == "вопрос"
        assert call["kw"]["rag_strategy"] == "deep"
        assert call["kw"]["inline_context"] == "ctx"
        assert sio.events == []
        assert env["resolve_args"][0] == 10 and env["resolve_args"][1] == "u1"

    async def test_empty_chain_delegates_and_ignores_non_dict_user(self, env):
        env["chain"] = []
        await _run(FakeSio(), current_user="oops", data="bad")
        assert len(env["calls"]) == 1
        assert env["resolve_args"] == (None, None, None)


def _chain():
    return [
        {"agent_id": 1, "name": "Первый", "kb_document_ids": [5], "file_search_enabled": True, "system_prompt": "p1"},
        {"agent_id": 2, "name": "  ", "system_prompt": "p2"},
        {"agent_id": 3, "name": "Итог", "system_prompt": "p3"},
    ]


class TestChain:
    async def test_runs_every_step_and_emits_complete(self, env):
        env["chain"] = _chain()
        env["replies"] = [
            {"content": "A1", "reasoning": "r1", "document_search": {"hits": [{"file": "f"}]}},
            "A2",
            {"content": "A3"},
        ]
        sio = FakeSio()
        await _run(sio, project_id="p", generation_started_at=1.0, inline_context="ctx", inline_images=["i"])

        assert len(env["calls"]) == 3
        first, second, third = env["calls"]
        # первый шаг: исходные настройки UI, вложения, общий лимит
        assert first["args"][3] is True  # use_kb_rag первого шага
        assert first["kw"]["inline_context"] == "ctx"
        assert first["kw"]["inline_images"] == ["i"]
        assert second["args"][3] is False
        assert second["kw"]["inline_context"] == "" and second["kw"]["inline_images"] is None
        assert "Ответы агентов" in second["message"]
        assert "[[ОТВЕТ 1]]" in third["message"]
        assert all(c["kw"]["emit_complete"] is False and c["kw"]["save_response"] is False for c in env["calls"])
        assert first["kw"]["rag_query"] == "вопрос"
        # с project_id у каждого шага есть документы - свой поисковый запрос у всех
        assert first["kw"]["search_query"] == "поиск:Первый"
        assert second["kw"]["search_query"].startswith("поиск:")

        updates = sio.named("chat_agent_update")
        assert [u["index"] for u in updates] == [0, 1, 2]
        assert [u["is_last"] for u in updates] == [False, False, True]
        assert updates[1]["agent_name"] == "Агент"

        (done,) = sio.named("chat_complete")
        assert done["agent_id"] == 3 and done["agent_name"] == "Итог"
        assert [s["content"] for s in done["chain_steps"]] == ["A1", "A2", "A3"]
        assert done["chain_steps"][0]["reasoning"] == "r1"
        assert done["chain_steps"][0]["document_search"] == {"hits": [{"file": "f"}]}
        assert done["generation_duration_sec"] == 3
        assert done["response"].startswith("**▸ Первый**")
        assert env["flushed"] is True

        (saved,) = env["saved"]
        assert saved[0] == done["response"]
        assert saved[1]["chain_steps"] == done["chain_steps"]
        assert saved[2]["conversation_id"] == "conv" and saved[2]["user_id"] == "u1"
        assert saved[2]["project_id"] == "p" and saved[2]["regenerate"] is False

    async def test_step_trace_collects_previous_outputs(self, env):
        env["chain"] = _chain()
        env["replies"] = ["A1", "A2", "A3"]
        await _run(FakeSio())
        trace = env["calls"][2]["kw"]  # профиль шага передан позиционно
        profile = env["calls"][2]["args"][5]
        assert profile["_chain_trace"]["index"] == 3 and profile["_chain_trace"]["total"] == 3
        assert [p["output"] for p in profile["_chain_trace"]["prev"]] == ["A1", "A2"]
        assert [p["system_prompt"] for p in profile["_chain_trace"]["prev"]] == ["p1", "p2"]
        assert trace["suppress_ui_stream"] is False

    async def test_hide_sequential_outputs(self, env):
        chain = _chain()
        chain[0]["hide_sequential_outputs"] = True
        env["chain"] = chain
        env["replies"] = ["A1", "A2", "A3"]
        sio = FakeSio()
        await _run(sio)
        assert [c["kw"]["suppress_ui_stream"] for c in env["calls"]] == [True, True, False]
        assert all(c["kw"]["stream_prefix"] == "" for c in env["calls"])
        thinking = sio.named("chat_thinking")
        assert len(thinking) == 2 and thinking[0]["message"] == "Первый думает…"
        (done,) = sio.named("chat_complete")
        assert done["response"] == "A3" and done["hide_sequential_outputs"] is True

    async def test_marks_inserted_answers_are_expanded_and_folded(self, env):
        env["chain"] = _chain()[:2]
        env["replies"] = ["Ответ первого", "Итог:\n[[ОТВЕТ 1]]"]
        sio = FakeSio()
        await _run(sio)
        (done,) = sio.named("chat_complete")
        last = done["chain_steps"][-1]["content"]
        assert "Ответ первого" in last and "[[ОТВЕТ" not in last
        # вставлены все шаги - промежуточные скрыты, видно только итог
        assert done["hide_sequential_outputs"] is True
        assert done["response"] == last

    async def test_partially_inserted_steps_are_removed_from_message(self, env):
        env["chain"] = _chain()
        env["replies"] = ["один", "два", "Итог:\n[[ОТВЕТ 1]]"]
        sio = FakeSio()
        await _run(sio)
        (done,) = sio.named("chat_complete")
        assert [s["agent_name"] for s in done["chain_steps"]] == ["Агент", "Итог"]
        assert done["hide_sequential_outputs"] is False

    async def test_shared_rag_of_head_goes_to_steps_that_accept_it(self, env):
        chain = _chain()
        chain[0].update(shared_chain_rag=True, kb_document_ids=[7, 8])
        chain[1].update(use_parent_shared_rag=False, file_search_enabled=True, kb_document_ids=[99])
        env["chain"] = chain
        env["replies"] = ["a", "b", "c"]
        await _run(FakeSio(), use_kb_rag=False)
        kb = [(c["args"][8], c["args"][9]) for c in env["calls"]]
        assert kb[0] == (True, [7, 8])
        assert kb[1] == (True, [99])  # отказался от общего RAG - своя база
        assert kb[2] == (True, [7, 8])

    async def test_step_without_docs_skips_search_query(self, env):
        env["chain"] = _chain()
        env["replies"] = ["a", "b", "c"]
        await _run(FakeSio(), use_kb_rag=False)
        assert [q[0] for q in env["queries"]] == ["шаг 1/3 «Первый»"]
        assert env["calls"][1]["kw"]["search_query"] == "вопрос"

    async def test_memory_rag_gives_every_step_a_query(self, env):
        env["chain"] = _chain()
        env["replies"] = ["a", "b", "c"]
        await _run(FakeSio(), use_memory_library_rag=True)
        assert len(env["queries"]) == 3

    async def test_effective_model_settings_are_bound(self, env):
        chain = _chain()
        chain[1]["_eff"] = {"temperature": 0.2}
        env["chain"] = chain
        env["replies"] = ["a", "b", "c"]
        await _run(FakeSio())
        assert env["bound"] == [{"temperature": 0.2}]

    async def test_mentions_are_stripped_and_tag_ids_forwarded(self, env, monkeypatch):
        monkeypatch.setattr("backend.services.tag_mentions.collect_mention_tag_ids", lambda **kw: [4, 5])
        monkeypatch.setattr("backend.services.tag_mentions.strip_tag_mentions", lambda m: "чистый")
        env["chain"] = _chain()
        env["replies"] = ["a", "b", "c"]
        await _run(FakeSio(), user_message="<#4|x> вопрос", data={"agent_id": 1})
        assert env["calls"][0]["message"].startswith("чистый")
        assert env["calls"][0]["data"]["tag_ids"] == [4, 5]

    async def test_mention_failure_keeps_original_message(self, env, monkeypatch):
        def boom(**kw):
            raise RuntimeError("x")

        monkeypatch.setattr("backend.services.tag_mentions.collect_mention_tag_ids", boom)
        env["chain"] = _chain()[:2]
        env["replies"] = ["a", "b"]
        await _run(FakeSio())
        assert env["calls"][0]["message"].startswith("вопрос")

    async def test_non_dict_payload_is_tolerated(self, env):
        env["chain"] = _chain()[:2]
        env["replies"] = ["a", "b"]
        sio = FakeSio()
        await _run(sio, data=None)
        assert len(sio.named("chat_complete")) == 1


class TestChainInterruptions:
    async def test_stop_before_a_step_emits_generation_stopped(self, env):
        env["chain"] = _chain()
        env["replies"] = ["a", "b", "c"]
        env["stopped_after"] = 0
        sio = FakeSio()
        await _run(sio)
        assert env["calls"] == []
        assert sio.named("generation_stopped") == [{"message": "Генерация остановлена"}]
        assert sio.named("chat_complete") == [] and env["saved"] == []

    async def test_step_returning_none_aborts_silently(self, env):
        env["chain"] = _chain()
        env["replies"] = ["a"]  # второй вызов вернёт None
        sio = FakeSio()
        await _run(sio)
        assert len(env["calls"]) == 2
        assert sio.named("chat_complete") == [] and env["saved"] == []

    async def test_stop_during_step_aborts_without_complete(self, env):
        env["chain"] = _chain()[:2]
        env["replies"] = ["a", "b"]
        env["stopped_after"] = 2  # стоп выставлен, пока выполнялся 2-й шаг
        sio = FakeSio()
        await _run(sio)
        assert sio.named("chat_complete") == []

    async def test_save_failure_is_swallowed(self, env):
        env["chain"] = _chain()[:2]
        env["replies"] = ["a", "b"]
        env["save_error"] = RuntimeError("db down")
        sio = FakeSio()
        await _run(sio)
        assert len(sio.named("chat_complete")) == 1
