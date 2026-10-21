"""routes/rag: настройки RAG сущности, статус пересборки, фоновая перечанковка, полный реиндекс."""

import asyncio
from types import SimpleNamespace

import pytest

from backend.routes import rag as module
from backend.tests.api.conftest import ADMIN, ALICE

pytestmark = pytest.mark.unit


class FakeStore:
    """Хранилище настроек сущностей вместо БД."""

    def __init__(self):
        self.rows = {}
        self.saved, self.reset, self.memory = [], [], {}

    async def get(self, scope, entity_id):
        return dict(self.rows.get((scope, entity_id), {}))

    async def save(self, scope, entity_id, updates, user_id):
        merged = {**self.rows.get((scope, entity_id), {}), **updates}
        self.rows[(scope, entity_id)] = merged
        self.saved.append((scope, entity_id, dict(updates), user_id))
        return merged

    async def reset_entity(self, scope, entity_id):
        self.reset.append((scope, entity_id))
        self.rows.pop((scope, entity_id), None)
        return {}

    async def memory_strategy(self, user_id):
        return self.memory.get(user_id, "auto")

    async def save_memory_strategy(self, user_id, strategy):
        self.memory[user_id] = strategy
        return strategy


class FakeRagClient:
    def __init__(self):
        self.calls = []
        self.status = {"kb": {"reindexing": False}, "project": {"reindexing": False}, "memory": {"reindexing": False}}
        self.status_error = None
        self.kb_docs = [{"id": 1}, {"id": 2}]
        self.project_docs = []
        self.fail = set()

    async def get_reindex_status(self):
        if self.status_error:
            raise self.status_error
        return self.status

    async def kb_list_documents(self):
        return self.kb_docs

    async def project_rag_list_documents(self, project_id):
        if "project_list" in self.fail:
            raise RuntimeError("down")
        return self.project_docs

    async def _record(self, name, kwargs):
        self.calls.append((name, kwargs))
        if name in self.fail:
            raise RuntimeError(f"{name} failed")
        return {"status": "started"}

    async def kb_reindex(self, **kw):
        return await self._record("kb", kw)

    async def project_rag_reindex_all(self, **kw):
        return await self._record("projects", kw)

    async def memory_rag_reindex(self, **kw):
        return await self._record("memory", kw)


@pytest.fixture
def env(monkeypatch, services):
    store, rag = FakeStore(), FakeRagClient()
    permissions = {}
    bumped = []
    services(module, "rag")
    monkeypatch.setattr(module, "rag_client", rag)
    monkeypatch.setattr(module, "get_entity_rag_settings", store.get)
    monkeypatch.setattr(module, "save_entity_rag_settings", store.save)
    monkeypatch.setattr(module, "reset_entity_rag_settings", store.reset_entity)
    monkeypatch.setattr(module, "get_user_memory_strategy", store.memory_strategy)
    monkeypatch.setattr(module, "save_user_memory_strategy", store.save_memory_strategy)
    monkeypatch.setattr(module, "bump_rag_semantic_cache", lambda: bumped.append(True))

    async def permission(scope, entity_id, user_id, *, is_admin=False):
        return permissions.get((scope, entity_id), permissions.get("*"))

    monkeypatch.setattr(module, "_entity_permission", permission)
    monkeypatch.setattr(module, "_active_reindex", {})
    monkeypatch.setattr(module, "_cluster_reindex_active", False)
    monkeypatch.setattr(module, "_reindex_flags_started_at", 0.0)
    monkeypatch.delenv("RAG_RECHUNK_ON_SETTINGS_CHANGE", raising=False)
    import backend.realtime.rag_evidence as evidence

    monkeypatch.setattr(evidence, "memory_rag_enabled", lambda: True)
    return SimpleNamespace(store=store, rag=rag, permissions=permissions, bumped=bumped, mp=monkeypatch)


def client(make_client, user=ALICE):
    return make_client(module.router, user=user)


class TestGetSettings:
    async def test_defaults_without_an_entity_are_editable(self, make_client, env):
        data = (await client(make_client).get("/api/rag/settings")).json()
        assert data["scope"] == "project" and data["permission"] == "owner" and data["can_edit"] is True
        assert data["rag_memory_strategy"] == "auto" and data["strategy"]

    async def test_entity_settings_and_permission(self, make_client, env):
        env.store.rows[("agent", "7")] = {"rag_strategy": "vector", "rag_chunk_size": 777}
        env.permissions[("agent", "7")] = "viewer"
        data = (await client(make_client).get("/api/rag/settings", params={"scope": "agent", "agent_id": 7})).json()
        assert data["strategy"] == "vector" and data["permission"] == "viewer" and data["can_edit"] is False
        assert data["agent_id"] == 7 and data["project_id"] is None

    async def test_project_scope_echoes_the_project_id(self, make_client, env):
        env.permissions[("project", "p1")] = "owner"
        data = (await client(make_client).get("/api/rag/settings", params={"project_id": "p1"})).json()
        assert data["project_id"] == "p1" and data["agent_id"] is None and data["can_edit"] is True

    async def test_stranger_sees_settings_but_cannot_edit(self, make_client, env):
        data = (await client(make_client).get("/api/rag/settings", params={"project_id": "foreign"})).json()
        assert data["permission"] is None and data["can_edit"] is False


class TestUpdateSettings:
    async def put(self, make_client, body, user=ALICE):
        return await client(make_client, user).put("/api/rag/settings", json=body)

    async def test_values_are_validated_and_clamped(self, make_client, env):
        env.permissions["*"] = "owner"
        response = await self.put(make_client, {
            "scope": "agent", "agent_id": 3, "strategy": "reranking", "agentic_max_iterations": 99, "rag_chat_top_k": 0,
            "rag_chunk_size": 50, "rag_chunk_overlap": 9999, "rag_similarity_threshold": 7, "rag_rerank_top_n": 1000,
            "rag_system_prompt": "  промпт  ", "rag_chunking_strategy": " Fixed ", "agentic_rag_enabled": True,
            "rag_embedding_model_path": " local/bge ", "rag_reranker_model_path": "",
        })
        assert response.status_code == 200
        _, entity, updates, user = env.store.saved[0]
        assert entity == "3" and user == "alice"
        assert updates == {
            "rag_strategy": "hybrid", "agentic_rag_enabled": True, "agentic_max_iterations": 5, "rag_chat_top_k": 1,
            "rag_chunking_strategy": "fixed", "rag_chunk_size": 200, "rag_chunk_overlap": 2000, "rag_similarity_threshold": 1.0,
            "rag_rerank_top_n": 64, "rag_system_prompt": "промпт", "rag_embedding_model_path": "local/bge", "rag_reranker_model_path": "",
        }
        body = response.json()
        assert body["success"] and body["permission"] == "owner" and body["can_edit"] and body["agent_id"] == 3 and body["reindexed"] is False
        assert env.bumped == [True]

    async def test_boolean_toggles_and_standard_strategy_alias(self, make_client, env):
        env.permissions["*"] = "editor"
        await self.put(make_client, {"project_id": "p1", "strategy": "standard", "rag_query_fix_typos": True, "rag_multi_query_enabled": False, "rag_hyde_enabled": True, "rag_reranking_enabled": True, "rag_similarity_threshold": -1})
        updates = env.store.saved[0][2]
        assert updates["rag_strategy"] == "vector" and updates["rag_multi_query_enabled"] is False and updates["rag_similarity_threshold"] == 0.0
        assert env.bumped == []

    @pytest.mark.parametrize("body,fragment", [
        ({"project_id": "p", "strategy": "magic"}, "Недопустимая стратегия"),
        ({"project_id": "p", "rag_chunking_strategy": "weird"}, "чанкования"),
        ({"rag_memory_strategy": "magic"}, "Библиотеки"),
        ({}, "Нет полей"),
        ({"strategy": "vector"}, "project_id, agent_id или knowledge_base_id"),
    ])
    async def test_invalid_requests(self, make_client, env, body, fragment):
        env.permissions["*"] = "owner"
        response = await self.put(make_client, body)
        assert response.status_code == 400 and fragment in response.json()["detail"]

    async def test_viewers_cannot_change_settings(self, make_client, env):
        env.permissions["*"] = "viewer"
        response = await self.put(make_client, {"project_id": "p", "strategy": "vector"})
        assert response.status_code == 403 and env.store.saved == []

    async def test_memory_strategy_alone_is_saved_per_user(self, make_client, env):
        response = await self.put(make_client, {"rag_memory_strategy": " Hybrid "})
        assert response.json() == {"message": "Стратегия поиска по Библиотеке обновлена", "success": True, "rag_memory_strategy": "hybrid"}
        assert env.store.memory == {"alice": "hybrid"}

    async def test_anonymous_user_is_unauthorized(self, make_client, env):
        response = await self.put(make_client, {"project_id": "p", "strategy": "vector"}, user={"username": "", "user_id": ""})
        assert response.status_code == 401

    async def test_storage_failures_are_a_500(self, make_client, env):
        env.permissions["*"] = "owner"

        async def broken(*a):
            raise RuntimeError("db down")

        env.mp.setattr(module, "save_entity_rag_settings", broken)
        response = await self.put(make_client, {"project_id": "p", "strategy": "vector"})
        assert response.status_code == 500

    async def test_index_affecting_change_starts_a_background_rechunk(self, make_client, env):
        env.permissions["*"] = "owner"
        env.mp.setenv("RAG_RECHUNK_ON_SETTINGS_CHANGE", "true")
        started = []

        async def fake_rechunk(user_id, params, scope, **kw):
            started.append((user_id, params, scope, kw))

        env.mp.setattr(module, "_run_background_rechunk_for_user", fake_rechunk)
        response = await self.put(make_client, {"scope": "agent", "agent_id": 4, "rag_chunk_size": 500})
        await asyncio.sleep(0)
        assert response.json()["reindexed"] is True
        assert started[0][0] == "alice" and started[0][2] == "agent"
        assert started[0][3] == {
            "project_id": None,
            "agent_id": 4,
            "knowledge_base_id": None,
        }
        assert started[0][1]["chunk_size"] == 500

    async def test_unchanged_index_settings_do_not_rechunk(self, make_client, env):
        env.permissions["*"] = "owner"
        env.mp.setenv("RAG_RECHUNK_ON_SETTINGS_CHANGE", "true")
        env.store.rows[("project", "p")] = {"rag_chunk_size": 500}
        response = await self.put(make_client, {"project_id": "p", "rag_chunk_size": 500, "rag_chat_top_k": 5})
        assert response.json()["reindexed"] is False


class TestResetSettings:
    async def test_editor_resets_the_entity(self, make_client, env):
        env.permissions["*"] = "editor"
        response = await client(make_client).post("/api/rag/settings/reset", params={"scope": "agent", "agent_id": 9})
        body = response.json()
        assert body["success"] and body["scope"] == "agent" and body["can_edit"] is True
        assert env.store.reset == [("agent", "9")] and env.bumped == [True]

    async def test_entity_is_required(self, make_client, env):
        response = await client(make_client).post("/api/rag/settings/reset")
        assert response.status_code == 400

    async def test_viewer_is_rejected(self, make_client, env):
        env.permissions["*"] = "viewer"
        response = await client(make_client).post("/api/rag/settings/reset", params={"project_id": "p"})
        assert response.status_code == 403 and env.store.reset == []

    async def test_unexpected_error_is_a_500(self, make_client, env):
        env.permissions["*"] = "owner"

        async def broken(*a):
            raise RuntimeError("db down")

        env.mp.setattr(module, "reset_entity_rag_settings", broken)
        assert (await client(make_client).post("/api/rag/settings/reset", params={"project_id": "p"})).status_code == 500


class TestReindexStatus:
    async def test_without_rag_service_everything_is_quiet(self, make_client, env):
        env.mp.setattr(module, "rag_client", None)
        data = (await client(make_client).get("/api/rag/reindex-status")).json()
        assert data["any_reindexing"] is False and data["memory_rag_enabled"] is True and data["message"] == ""

    async def test_poll_failure_returns_the_empty_payload(self, make_client, env):
        env.rag.status_error = RuntimeError("down")
        assert (await client(make_client).get("/api/rag/reindex-status")).json()["any_reindexing"] is False

    async def test_registry_is_cleared_when_svc_rag_is_idle(self, make_client, env):
        module._active_reindex["agent:1"] = {"scope": "agent", "entity_id": "1", "name": "A"}
        module._cluster_reindex_active = True
        data = (await client(make_client).get("/api/rag/reindex-status")).json()
        assert data["any_reindexing"] is False and module._active_reindex == {} and module._cluster_reindex_active is False

    async def test_grace_period_keeps_the_flags_alive(self, make_client, env):
        module._mark_reindex_started()
        module._active_reindex["agent:1"] = {"scope": "agent", "entity_id": "1", "name": "База знаний"}
        data = (await client(make_client).get("/api/rag/reindex-status")).json()
        assert data["kb"]["reindexing"] is True and data["any_reindexing"] is True and "База знаний" in data["message"]
        assert data["active"][0]["entity_id"] == "1"

    async def test_cluster_run_is_shown_to_everybody(self, make_client, env):
        module._cluster_reindex_active = True
        module._mark_reindex_started()
        env.rag.status["kb"]["reindexing"] = True
        data = (await client(make_client).get("/api/rag/reindex-status")).json()
        assert data["memory"]["reindexing"] is True and data["kb"]["reindexing"] is True and data["project"]["reindexing"] is False

    async def test_agent_and_project_document_flags(self, make_client, env):
        env.rag.project_docs = [{"id": 1}]
        env.mp.setattr(module, "_resolve_agent_has_kb_documents", lambda agent_id, user: asyncio.sleep(0, result=agent_id == 5))
        data = (await client(make_client).get("/api/rag/reindex-status", params={"agent_id": 5, "project_id": " p1 "})).json()
        assert data["agent_has_kb"] is True and data["project_has_documents"] is True
        data = (await client(make_client).get("/api/rag/reindex-status", params={"agent_id": 6})).json()
        assert data["agent_has_kb"] is False

    async def test_project_document_lookup_failure_is_not_fatal(self, make_client, env):
        env.rag.fail.add("project_list")
        assert await module._resolve_project_has_rag_documents("p") is False
        assert await module._resolve_project_has_rag_documents("") is False

    async def test_agent_kb_resolution(self, env, monkeypatch):
        import backend.realtime.helpers as helpers

        profile = {"file_search_enabled": True, "kb_document_ids": [2, 99]}

        async def params(agent_id, user_id, user=None):
            return profile

        monkeypatch.setattr(helpers, "_resolve_agent_chat_params", params)
        assert await module._resolve_agent_has_kb_documents(1, {"user_id": "u"}) is True
        profile["kb_document_ids"] = [99]
        assert await module._resolve_agent_has_kb_documents(1, None) is False
        profile["kb_document_ids"] = []
        assert await module._resolve_agent_has_kb_documents(1, None) is False
        profile.update(file_search_enabled=False, kb_document_ids=[1])
        assert await module._resolve_agent_has_kb_documents(1, None) is False
        profile["file_search_enabled"] = True
        env.mp.setattr(module, "rag_client", None)
        assert await module._resolve_agent_has_kb_documents(1, None) is False

    async def test_agent_kb_listing_failure_assumes_documents_exist(self, env, monkeypatch):
        import backend.realtime.helpers as helpers

        async def params(agent_id, user_id, user=None):
            return {"file_search_enabled": True, "kb_document_ids": [1]}

        async def broken():
            raise RuntimeError("down")

        monkeypatch.setattr(helpers, "_resolve_agent_chat_params", params)
        env.rag.kb_list_documents = broken
        assert await module._resolve_agent_has_kb_documents(1, None) is True


class TestReindexRegistryHelpers:
    def test_mark_and_unmark(self, env):
        module._mark_entity_reindex("agent", " 4 ", active=True, name="A")
        module._mark_entity_reindex("project", "", active=True)
        assert module._active_reindex == {"agent:4": {"scope": "agent", "entity_id": "4", "name": "A"}}
        module._mark_entity_reindex("agent", "4", active=False)
        assert module._active_reindex == {}

    def test_keys(self, env):
        assert module._reindex_key("agent", "3") == "agent:3" and module._reindex_key(None, "") == "project"

    def test_grace_window(self, env):
        assert module._reindex_grace_active() is False
        module._mark_reindex_started()
        assert module._reindex_grace_active() is True

    async def test_entity_display_name(self, env, monkeypatch):
        import backend.database.init_db as init_db

        class Agents:
            async def get_agent(self, aid):
                return SimpleNamespace(name=" Юрист ") if aid == 1 else None

        monkeypatch.setattr(init_db, "get_agent_repository", lambda: Agents())
        assert await module._entity_display_name("agent", "1") == "Юрист"
        assert await module._entity_display_name("agent", "2") is None
        assert await module._entity_display_name("agent", "abc") is None
        assert await module._entity_display_name("agent", "") is None

        class Conn:
            async def fetchrow(self, sql, eid):
                return {"name": " Проект "} if eid == "p1" else None

        class Acquire:
            async def __aenter__(self):
                return Conn()

            async def __aexit__(self, *a):
                return False

        class Pool:
            async def acquire(self):
                return Acquire()

        monkeypatch.setattr(init_db, "get_project_repository", lambda: SimpleNamespace(db_connection=Pool()))
        assert await module._entity_display_name("project", "p1") == "Проект"
        assert await module._entity_display_name("project", "zzz") is None
        monkeypatch.setattr(init_db, "get_project_repository", lambda: None)
        assert await module._entity_display_name("project", "p1") is None

    def test_message_builder_delegates(self, env):
        assert "документов проекта" in module._build_reindex_status_message(memory_reindexing=False, project_reindexing=True, kb_reindexing=False)

    def test_environment_switches(self, env):
        assert module._rechunk_on_settings_change() is False
        env.mp.setenv("RAG_RECHUNK_ON_SETTINGS_CHANGE", "On")
        assert module._rechunk_on_settings_change() is True
        assert module._reindex_on_model_change() is True
        env.mp.setenv("RAG_REINDEX_ON_MODEL_CHANGE", "0")
        assert module._reindex_on_model_change() is False


class TestKbDocumentCollection:
    def agents(self, monkeypatch, agents=None, single=None, error=None):
        import backend.database.init_db as init_db

        class Repo:
            async def get_agents(self, filters, user_id=None):
                if error:
                    raise error
                return agents or [], len(agents or [])

            async def get_agent(self, aid):
                return single

        monkeypatch.setattr(init_db, "get_agent_repository", lambda: Repo())

    async def test_owner_documents_are_merged_and_deduplicated(self, env, monkeypatch):
        self.agents(monkeypatch, agents=[
            SimpleNamespace(config={"kb_document_ids": [1, "2", "x", 1]}),
            SimpleNamespace(config='{"kb_document_ids": [2, 3]}'),
            SimpleNamespace(config="{broken"),
            SimpleNamespace(config=["list"]),
            SimpleNamespace(config=None),
        ])
        assert await module._collect_owner_agent_kb_document_ids(" Alice ") == [1, 2, 3]
        assert await module._collect_owner_agent_kb_document_ids("") == []

    async def test_owner_lookup_failure(self, env, monkeypatch):
        self.agents(monkeypatch, error=RuntimeError("db"))
        assert await module._collect_owner_agent_kb_document_ids("alice") == []

    async def test_single_agent_documents(self, env, monkeypatch):
        self.agents(monkeypatch, single=SimpleNamespace(config='{"kb_document_ids": [4, "5", "x"]}'))
        assert await module._collect_agent_kb_document_ids(1) == [4, 5]
        assert await module._collect_agent_kb_document_ids("bad") == [] and await module._collect_agent_kb_document_ids(0) == []
        self.agents(monkeypatch, single=None)
        assert await module._collect_agent_kb_document_ids(1) == []
        self.agents(monkeypatch, single=SimpleNamespace(config="[1]"))
        assert await module._collect_agent_kb_document_ids(1) == []


class TestBackgroundRechunk:
    @pytest.fixture
    def prep(self, env, monkeypatch):
        import backend.services.user_rag_settings as urs

        async def embedding_fields(scope, entity_id):
            return {"embedding_model": "bge"}

        async def owner_docs(owner):
            return [1, 2]

        async def agent_docs(aid):
            return [9]

        async def name(scope, eid):
            return "Имя"

        monkeypatch.setattr(urs, "embedding_fields_for_entity", embedding_fields)
        monkeypatch.setattr(module, "_collect_owner_agent_kb_document_ids", owner_docs)
        monkeypatch.setattr(module, "_collect_agent_kb_document_ids", agent_docs)
        monkeypatch.setattr(module, "_entity_display_name", name)
        return env

    PARAMS = {"chunk_size": 500, "chunk_overlap": 50, "chunking_strategy": "fixed"}

    async def test_agent_rechunk_targets_only_that_agent(self, prep):
        await module._run_background_rechunk_for_user("Alice", self.PARAMS, "agent", agent_id=3)
        (name, kw), = prep.rag.calls
        assert name == "kb" and kw["owner_user_id"] is None and kw["agent_id"] == 3 and kw["document_ids"] == [9]
        assert kw["embedding_model"] == "bge" and kw["chunk_size"] == 500
        assert module._active_reindex["agent:3"]["name"] == "Имя" and module._reindex_grace_active()

    async def test_project_rechunk(self, prep):
        prep.rag.project_docs = [{"id": 1}]
        await module._run_background_rechunk_for_user("alice", self.PARAMS, "project", project_id="p1")
        (name, kw), = prep.rag.calls
        assert name == "projects" and kw["project_id"] == "p1" and kw["owner_user_id"] is None

    async def test_both_stores_for_the_owner(self, prep):
        await module._run_background_rechunk_for_user("alice", self.PARAMS, None)
        assert [c[0] for c in prep.rag.calls] == ["kb", "projects"]
        assert prep.rag.calls[0][1]["owner_user_id"] == "alice" and prep.rag.calls[1][1]["owner_user_id"] == "alice"

    async def test_empty_stores_are_skipped_without_a_banner(self, prep):
        prep.rag.project_docs = []
        prep.mp.setattr(module, "_collect_agent_kb_document_ids", lambda aid: asyncio.sleep(0, result=[]))
        await module._run_background_rechunk_for_user("alice", self.PARAMS, "agent", agent_id=3)
        await module._run_background_rechunk_for_user("alice", self.PARAMS, "project", project_id="p1")
        assert prep.rag.calls == [] and module._active_reindex == {}

    async def test_service_errors_do_not_escape(self, prep):
        prep.rag.fail.update({"kb", "projects"})
        prep.rag.project_docs = [{"id": 1}]
        await module._run_background_rechunk_for_user("alice", self.PARAMS, None, project_id="p1")
        assert [c[0] for c in prep.rag.calls] == ["kb", "projects"]

    async def test_no_client_or_user_means_nothing_to_do(self, prep):
        await module._run_background_rechunk_for_user("", self.PARAMS, "agent")
        prep.mp.setattr(module, "rag_client", None)
        await module._run_background_rechunk_for_user("alice", self.PARAMS, "agent")
        assert prep.rag.calls == []

    async def test_reindex_after_model_change_delegates(self, prep):
        await module._run_background_reindex_for_user("alice", self.PARAMS, "agent", agent_id=3)
        assert prep.rag.calls[0][0] == "kb"


class TestClusterReindex:
    async def test_after_model_change_runs_all_three_stores(self, env):
        env.mp.setattr(module, "get_rag_chunk_index_params", lambda: {"chunk_size": 800, "chunk_overlap": 80, "chunking_strategy": "fixed"})
        env.rag.fail.add("projects")
        await module._run_background_reindex_after_model_change()
        assert [c[0] for c in env.rag.calls] == ["kb", "projects", "memory"]
        assert env.rag.calls[0][1]["chunk_size"] == 800 and module._cluster_reindex_active is True

    async def test_without_client_nothing_happens(self, env):
        env.mp.setattr(module, "rag_client", None)
        await module._run_background_reindex_after_model_change()
        await module._run_global_reindex({}, {}, True)
        assert module._cluster_reindex_active is False

    async def test_global_reindex_with_and_without_memory(self, env):
        agent = {"chunk_size": 1, "chunk_overlap": 2, "chunking_strategy": "a"}
        project = {"chunk_size": 3, "chunk_overlap": 4, "chunking_strategy": "p"}
        await module._run_global_reindex(agent, project, False)
        assert [(c[0], c[1]["chunk_size"]) for c in env.rag.calls] == [("kb", 1), ("projects", 3)]
        env.rag.calls.clear()
        env.rag.fail.update({"kb", "memory"})
        await module._run_global_reindex(agent, project, True)
        assert [c[0] for c in env.rag.calls] == ["kb", "projects", "memory"]

    async def test_endpoint_starts_the_background_run(self, make_client, env):
        started = []

        async def fake(agent, project, include_memory):
            started.append(include_memory)

        env.mp.setattr(module, "_run_global_reindex", fake)
        response = await client(make_client).post("/api/rag/reindex-all", params={"include_memory": "true"})
        await asyncio.sleep(0)
        assert response.json() == {"ok": True, "status": "started", "scope": "kb+projects+memory"} and started == [True]

    async def test_endpoint_requires_service_and_user(self, make_client, env):
        env.mp.setattr(module, "rag_client", None)
        assert (await client(make_client).post("/api/rag/reindex-all")).status_code == 503
        env.mp.setattr(module, "rag_client", env.rag)
        response = await client(make_client, user={"username": "", "user_id": ""}).post("/api/rag/reindex-all")
        assert response.status_code == 401

    async def test_ordinary_user_can_trigger_a_cluster_wide_reindex_known_issue(self, make_client, env):
        """Известная проблема: эндпоинт доступен любому авторизованному пользователю, не только администратору,
        хотя пересобирает KB и проекты ВСЕХ пользователей (нагрузка на svc-rag и недоступность поиска для всех)."""
        env.mp.setattr(module, "_run_global_reindex", lambda *a: asyncio.sleep(0))
        response = await client(make_client, ALICE).post("/api/rag/reindex-all")
        assert response.status_code == 200
        assert ALICE["is_admin"] is False and ADMIN["is_admin"] is True


class TestUpstreamTimeouts:
    def test_timeout_detection_follows_the_cause_chain(self):
        import httpx

        inner = httpx.ReadTimeout("slow")
        outer = RuntimeError("wrapper")
        outer.__cause__ = inner
        assert module._is_upstream_httpx_timeout(outer) is True
        assert module._is_upstream_httpx_timeout(RuntimeError("x")) is False

    def test_cause_cycles_do_not_loop_forever(self):
        a, b = RuntimeError("a"), RuntimeError("b")
        a.__cause__, b.__cause__ = b, a
        assert module._is_upstream_httpx_timeout(a) is False

    def test_source_file_is_kept_on_timeout_and_removed_otherwise(self, env):
        import httpx

        removed = []
        env.mp.setattr(module, "_delete_rag_source_file", lambda obj, bucket: removed.append((obj, bucket)))
        assert module._cleanup_rag_source_after_failure(httpx.ReadTimeout("t"), "obj", "b") is True and removed == []
        assert module._cleanup_rag_source_after_failure(RuntimeError("x"), "obj", "b") is False and removed == [("obj", "b")]
        assert module._cleanup_rag_source_after_failure(RuntimeError("x"), None, None) is False and len(removed) == 1

    def test_cleanup_errors_are_suppressed(self, env):
        def broken(obj, bucket):
            raise OSError("disk")

        env.mp.setattr(module, "_delete_rag_source_file", broken)
        assert module._cleanup_rag_source_after_failure(RuntimeError("x"), "obj", "b") is False


class TestLocalModelValidation:
    async def test_unknown_model_is_rejected(self, env):
        env.mp.setattr(module, "rag_models_client", SimpleNamespace(list_models=lambda t: asyncio.sleep(0, result={"models": {"embedding": [{"path": "local/a"}]}})))
        await module._validate_local_model_path("embedding", "local/a")
        with pytest.raises(Exception) as err:
            await module._validate_local_model_path("embedding", "local/zzz")
        assert err.value.status_code == 400

    async def test_memory_guard_allows_only_listed_embedders(self, env):
        env.mp.setattr(module, "rag_models_client", SimpleNamespace(list_models=lambda t: asyncio.sleep(0, result={"models": {"embedding": [{"path": "local/Big"}, {"path": "local/small"}], "reranker": [{"path": "local/rr"}]}})))
        env.mp.setenv("RAG_EMBEDDING_MODELS_ALLOWED", "small; other")
        assert module._allowed_embedding_models() == ["small", "other"]
        await module._validate_local_model_path("embedding", "local/small")
        with pytest.raises(Exception) as err:
            await module._validate_local_model_path("embedding", "local/Big")
        assert "не помещается" in err.value.detail
        await module._validate_local_model_path("reranker", "local/rr")

    def test_overlay_uses_saved_models_over_the_cluster_defaults(self):
        data = {"cluster_default": {"embedding": {"path": "c/e"}, "reranker": {"path": "c/r"}}, "current": {"embedding": {"path": "live"}}}
        out = module._overlay_user_model_current(data, {"rag_embedding_model_path": "phoenix/emb-1"})
        assert out["current"]["embedding"]["name"] == "emb-1" and out["current"]["embedding"]["source"] == "phoenix"
        assert out["current"]["reranker"] == {"path": "c/r"} and out["cluster_default"]["embedding"] == {"path": "c/e"}
        assert module._overlay_user_model_current({"current": {"x": 1}}, {})["cluster_default"] == {"x": 1}
        assert module._overlay_user_model_current(None, {})["current"] == {}
        assert module._model_row_from_path("", "embedding")["source"] == "local"
