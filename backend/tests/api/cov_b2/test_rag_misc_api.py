"""routes/rag: хранилище исходников, права на сущности, защитные ветки настроек, пересборка, ошибки списков KB/Memory."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.routes import rag as module
from backend.schemas import RAGSettings
from backend.storage.rag_pvc import RagPvcUnavailable
from backend.tests.api.conftest import ALICE
from backend.tests.api.cov_b2.test_documents_extract import RecLogger

pytestmark = pytest.mark.unit


class Store:
    def __init__(self):
        self.rows = {}
        self.saved = []

    async def get(self, scope, entity_id):
        return dict(self.rows.get((scope, entity_id), {}))

    async def save(self, scope, entity_id, updates, user_id):
        merged = {**self.rows.get((scope, entity_id), {}), **updates}
        self.rows[(scope, entity_id)] = merged
        self.saved.append((scope, entity_id, dict(updates), user_id))
        return merged


class RagStub:
    """Клиент svc-rag для проверки ветвей пересборки и списков."""

    def __init__(self):
        self.calls = []
        self.fail = set()
        self.docs = [{"id": 1}]

    async def _record(self, name, kw):
        self.calls.append((name, kw))
        if name in self.fail:
            raise RuntimeError(f"{name} failed")
        return {"status": "started"}

    async def kb_reindex(self, **kw):
        return await self._record("kb", kw)

    async def project_rag_reindex_all(self, **kw):
        return await self._record("projects", kw)

    async def memory_rag_reindex(self, **kw):
        return await self._record("memory", kw)

    async def kb_list_documents(self):
        if "kb_list" in self.fail:
            raise RuntimeError("svc-rag at 10.0.0.5 refused")
        return self.docs

    async def memory_rag_list_documents(self):
        raise RuntimeError("memory list down")

    async def kb_delete_document(self, document_id):
        if "kb_delete" in self.fail:
            raise RuntimeError("kb delete down")
        return self.docs

    async def memory_rag_delete_document(self, document_id):
        raise RuntimeError("memory delete down")

    async def memory_rag_check_duplicate(self, **kw):
        raise RuntimeError("check down")


@pytest.fixture
def env(monkeypatch, services):
    store, rag = Store(), RagStub()
    services(module, "rag")
    monkeypatch.setattr(module, "rag_client", rag)
    monkeypatch.setattr(module, "get_entity_rag_settings", store.get)
    monkeypatch.setattr(module, "save_entity_rag_settings", store.save)
    monkeypatch.setattr(module, "bump_rag_semantic_cache", lambda: None)
    monkeypatch.setattr(module, "_active_reindex", {})
    monkeypatch.setattr(module, "_cluster_reindex_active", False)

    async def permission(scope, entity_id, user_id, *, is_admin=False):
        return "owner"

    monkeypatch.setattr(module, "_entity_permission", permission)
    monkeypatch.delenv("RAG_RECHUNK_ON_SETTINGS_CHANGE", raising=False)
    return SimpleNamespace(store=store, rag=rag, mp=monkeypatch)


def _client(make_client, user=ALICE):
    return make_client(module.router, user=user)


# ---------- хранилище исходников ----------


class TestSourceStorage:
    def test_storage_check_passes(self, monkeypatch):
        monkeypatch.setattr(module, "require_rag_pvc_available", lambda: None)

        assert module._require_storage_for_delete() is None

    def test_missing_volume_is_a_503(self, monkeypatch):
        def unavailable():
            raise RagPvcUnavailable("том не смонтирован")

        monkeypatch.setattr(module, "require_rag_pvc_available", unavailable)

        with pytest.raises(HTTPException) as exc:
            module._require_storage_for_delete()

        assert exc.value.status_code == 503 and exc.value.detail == "Хранилище файлов недоступно"

    @pytest.mark.parametrize(("obj", "bucket"), [(None, "b"), ("o", None), ("", "")])
    def test_nothing_to_delete(self, monkeypatch, obj, bucket):
        monkeypatch.setattr(module, "delete_rag_pvc_file", lambda *a: pytest.fail("не должен вызываться"))
        monkeypatch.setattr(module, "minio_client", SimpleNamespace(delete_file=lambda *a, **k: pytest.fail("не должен вызываться")))

        assert module._delete_rag_source_file(obj, bucket) is None

    def test_pvc_source(self, monkeypatch):
        deleted = []
        monkeypatch.setattr(module, "is_rag_pvc_bucket", lambda b: b == "pvc")
        monkeypatch.setattr(module, "delete_rag_pvc_file", lambda o, b: deleted.append((o, b)))
        monkeypatch.setattr(module, "minio_client", None)

        module._delete_rag_source_file("kb_1.pdf", "pvc")

        assert deleted == [("kb_1.pdf", "pvc")]

    def test_minio_source_and_failure(self, monkeypatch):
        calls = []

        def delete_file(obj, bucket_name=None):
            calls.append((obj, bucket_name))
            if obj == "bad":
                raise RuntimeError("minio down")

        monkeypatch.setattr(module, "is_rag_pvc_bucket", lambda b: False)
        monkeypatch.setattr(module, "minio_client", SimpleNamespace(delete_file=delete_file))

        module._delete_rag_source_file("good", "docs")
        module._delete_rag_source_file("bad", "docs")

        assert calls == [("good", "docs"), ("bad", "docs")]

    def test_without_minio_nothing_happens(self, monkeypatch):
        monkeypatch.setattr(module, "is_rag_pvc_bucket", lambda b: False)
        monkeypatch.setattr(module, "minio_client", None)

        assert module._delete_rag_source_file("o", "b") is None


class TestMiscHelpers:
    async def test_memory_duplicate_check_failure_does_not_block(self, env):
        assert await module._raise_if_memory_duplicate(b"x", "a.pdf") is None

    def test_cluster_defaults_response(self):
        from backend.services.user_rag_settings import default_rag_settings_snapshot, settings_response_dict

        assert module._rag_settings_response_dict() == settings_response_dict(default_rag_settings_snapshot())

    def test_overlay_applies_the_saved_reranker(self):
        out = module._overlay_user_model_current({"current": {"embedding": {"path": "c/e"}}}, {"rag_reranker_model_path": "phoenix/rr"})

        assert out["current"]["embedding"] == {"path": "c/e"}
        assert out["current"]["reranker"] == {"path": "phoenix/rr", "name": "rr", "display_name": "rr", "source": "phoenix", "kind": "reranker"}


# ---------- права на проект ----------


class TestProjectPermission:
    async def test_missing_repository(self, monkeypatch):
        import backend.database.init_db as init_db

        monkeypatch.setattr(init_db, "get_project_repository", lambda: None)

        assert await module._entity_permission("project", "p1", "alice") is None

    async def test_repository_failure_denies_access(self, monkeypatch):
        import backend.database.init_db as init_db

        class Repo:
            async def get_by_id(self, uid, pid):
                raise RuntimeError("db down")

        monkeypatch.setattr(init_db, "get_project_repository", lambda: Repo())

        assert await module._entity_permission("project", "p1", "alice") is None


# ---------- сброс настроек без пользователя ----------


class TestResetUnauthorized:
    async def test_user_without_an_id(self, make_client, env):
        response = await _client(make_client, {"username": "x", "user_id": ""}).post("/api/rag/settings/reset", params={"project_id": "p"})

        assert response.status_code == 401 and response.json()["detail"] == "Требуется авторизация"


# ---------- update_rag_settings ----------


class TestUpdateBranches:
    async def put(self, make_client, body):
        return await _client(make_client).put("/api/rag/settings", json=body)

    async def test_lexical_strategy_is_kept(self, make_client, env):
        response = await self.put(make_client, {"project_id": "p", "strategy": "lexical"})

        assert response.status_code == 200 and env.store.saved[0][2] == {"rag_strategy": "lexical"}

    async def test_blank_memory_strategy_is_ignored(self, make_client, env):
        response = await self.put(make_client, {"rag_memory_strategy": "   "})

        assert response.status_code == 400 and response.json()["detail"] == "Нет полей для обновления"

    async def test_blank_memory_strategy_next_to_entity_fields(self, make_client, env):
        response = await self.put(make_client, {"project_id": "p", "rag_memory_strategy": " ", "rag_chat_top_k": 5})

        assert response.status_code == 200 and env.store.saved[0][2] == {"rag_chat_top_k": 5}

    @pytest.mark.parametrize(
        ("field", "fragment"),
        [
            ("rag_chat_top_k", "rag_chat_top_k: ожидается целое число 1–64"),
            ("rag_chunk_size", "rag_chunk_size: ожидается целое число 200–8000"),
            ("rag_chunk_overlap", "rag_chunk_overlap: ожидается целое число 0–2000"),
            ("rag_similarity_threshold", "rag_similarity_threshold: ожидается число 0..1"),
            ("rag_rerank_top_n", "rag_rerank_top_n: ожидается целое число 1–64"),
        ],
    )
    async def test_non_numeric_values_are_a_400_when_validation_is_bypassed(self, env, field, fragment):
        # Pydantic отсекает такие значения ещё на входе (422); защитная ветка проверяется прямым вызовом.
        settings = RAGSettings.model_construct(project_id="p", **{field: "abc"})

        with pytest.raises(HTTPException) as exc:
            await module.update_rag_settings(settings, {"user_id": "alice"})

        assert exc.value.status_code == 400 and exc.value.detail == fragment
        assert env.store.saved == []

    @pytest.mark.parametrize("field", ["rag_chat_top_k", "rag_chunk_size", "rag_similarity_threshold"])
    async def test_non_numeric_values_are_rejected_by_the_schema(self, make_client, env, field):
        response = await self.put(make_client, {"project_id": "p", field: "abc"})

        assert response.status_code == 422 and env.store.saved == []

    async def test_nan_threshold_is_clamped_to_zero(self, make_client, env):
        response = await _client(make_client).put(
            "/api/rag/settings", content='{"project_id": "p", "rag_similarity_threshold": NaN}', headers={"content-type": "application/json"}
        )

        assert response.status_code == 200 and env.store.saved[0][2] == {"rag_similarity_threshold": 0.0}


# ---------- пересборка ----------


class TestRechunkEdgeCases:
    PARAMS = {"chunk_size": 500, "chunk_overlap": 50, "chunking_strategy": "fixed"}

    @pytest.fixture
    def prep(self, env):
        import backend.services.user_rag_settings as urs

        async def embedding_fields(scope, entity_id):
            return {}

        async def owner_docs(owner):
            return [1]

        async def agent_docs(aid):
            return [9]

        async def name(scope, eid):
            return None

        env.mp.setattr(urs, "embedding_fields_for_entity", embedding_fields)
        env.mp.setattr(module, "_collect_owner_agent_kb_document_ids", owner_docs)
        env.mp.setattr(module, "_collect_agent_kb_document_ids", agent_docs)
        env.mp.setattr(module, "_entity_display_name", name)
        return env

    async def test_garbage_agent_id_never_reaches_svc_rag(self, prep):
        # Документы агента не подменяются, а сам запрос не строится (int("x") падает) — ошибка логируется, не пробрасывается.
        await module._run_background_rechunk_for_user("alice", self.PARAMS, "agent", agent_id="x")

        assert prep.rag.calls == []

    async def test_non_positive_agent_id_keeps_the_owner_document_list(self, prep):
        await module._run_background_rechunk_for_user("alice", self.PARAMS, "agent", agent_id=0)

        (name, kw) = prep.rag.calls[0]
        assert name == "kb" and kw["document_ids"] == [1] and kw["agent_id"] == 0

    async def test_flag_is_removed_when_queueing_itself_fails(self, prep):
        class Logger(RecLogger):
            def info(self, msg, *args, **kwargs):
                if "задачи поставлены" in msg:
                    raise RuntimeError("log sink broke")
                self.records.append(("info", msg, args))

        prep.mp.setattr(module, "logger", Logger())

        with pytest.raises(RuntimeError, match="log sink broke"):
            await module._run_background_rechunk_for_user("alice", self.PARAMS, "agent", agent_id=3)

        assert module._active_reindex == {}

    async def test_cluster_run_falls_back_when_memory_params_are_unavailable(self, prep):
        import backend.services.memory_rag_env as memory_env

        def broken():
            raise RuntimeError("no env")

        prep.mp.setattr(memory_env, "get_memory_chunk_index_params", broken)
        prep.mp.setattr(module, "get_rag_chunk_index_params", lambda: {"chunk_size": 700, "chunk_overlap": 70, "chunking_strategy": "fixed"})

        await module._run_background_reindex_after_model_change()

        memory = [kw for name, kw in prep.rag.calls if name == "memory"][0]
        assert memory == {"chunk_size": 700, "chunk_overlap": 70, "chunking_strategy": "universal"}

    async def test_global_run_reports_failures_per_store(self, prep):
        agent = {"chunk_size": 1, "chunk_overlap": 2, "chunking_strategy": "a"}
        prep.rag.fail.add("projects")

        await module._run_global_reindex(agent, agent, False)

        assert [c[0] for c in prep.rag.calls] == ["kb", "projects"]
        assert module._cluster_reindex_active is True

    async def test_global_run_memory_uses_env_params_or_the_fallback(self, prep):
        import backend.services.memory_rag_env as memory_env

        agent = {"chunk_size": 11, "chunk_overlap": 22, "chunking_strategy": "a"}
        prep.mp.setattr(memory_env, "get_memory_chunk_index_params", lambda: {"chunk_size": 5, "chunk_overlap": 6, "chunking_strategy": "universal"})
        await module._run_global_reindex(agent, agent, True)

        def broken():
            raise RuntimeError("no env")

        prep.mp.setattr(memory_env, "get_memory_chunk_index_params", broken)
        await module._run_global_reindex(agent, agent, True)

        memory_calls = [kw for name, kw in prep.rag.calls if name == "memory"]
        assert memory_calls == [
            {"chunk_size": 5, "chunk_overlap": 6, "chunking_strategy": "universal"},
            {"chunk_size": 11, "chunk_overlap": 22, "chunking_strategy": "universal"},
        ]


class TestAgentDocumentCollection:
    def _repo(self, monkeypatch, repo):
        import backend.database.init_db as init_db

        monkeypatch.setattr(init_db, "get_agent_repository", lambda: repo)

    async def test_missing_repository(self, monkeypatch):
        self._repo(monkeypatch, None)

        assert await module._collect_agent_kb_document_ids(3) == []

    async def test_dict_config_is_used_as_is(self, monkeypatch):
        class Repo:
            async def get_agent(self, aid):
                return SimpleNamespace(config={"kb_document_ids": ["1", 2, None, "x"]})

        self._repo(monkeypatch, Repo())

        assert await module._collect_agent_kb_document_ids(3) == [1, 2]

    async def test_broken_json_config(self, monkeypatch):
        class Repo:
            async def get_agent(self, aid):
                return SimpleNamespace(config="{broken")

        self._repo(monkeypatch, Repo())

        assert await module._collect_agent_kb_document_ids(3) == []

    async def test_repository_failure(self, monkeypatch):
        class Repo:
            async def get_agent(self, aid):
                raise RuntimeError("db down")

        self._repo(monkeypatch, Repo())

        assert await module._collect_agent_kb_document_ids(3) == []


# ---------- списки и удаление: ошибки зависимостей ----------


class TestKbAndMemoryFailures:
    async def test_kb_list_failure_is_a_500(self, make_client, env):
        env.rag.fail.add("kb_list")

        response = await _client(make_client).get("/api/kb/documents")

        assert response.status_code == 500

    async def test_memory_list_failure_is_a_500(self, make_client, env):
        response = await _client(make_client).get("/api/memory-rag/documents")

        assert response.status_code == 500 and response.json()["detail"] == "Внутренняя ошибка сервера"

    async def test_memory_delete_failure_is_a_500(self, make_client, env):
        response = await _client(make_client).delete("/api/memory-rag/documents/3")

        assert response.status_code == 500 and response.json()["detail"] == "Внутренняя ошибка сервера"

    @pytest.fixture
    def kb_delete(self, env):
        removed = []
        env.mp.setattr(module, "_require_storage_for_delete", lambda: None)
        env.mp.setattr(module, "_delete_rag_source_file", lambda o, b: removed.append((o, b)))
        env.removed = removed
        return env

    async def test_kb_delete_without_an_agent_repository(self, make_client, kb_delete, monkeypatch):
        import backend.database.init_db as init_db

        monkeypatch.setattr(init_db, "get_agent_repository", lambda: None)
        kb_delete.rag.docs = {"ok": True, "minio_object": "o", "minio_bucket": "b"}

        response = await _client(make_client).delete("/api/kb/documents/4")

        assert response.json() == {"ok": True, "document_id": 4} and kb_delete.removed == [("o", "b")]

    async def test_kb_delete_with_a_non_dict_answer_keeps_the_source(self, make_client, kb_delete, monkeypatch):
        import backend.database.init_db as init_db

        monkeypatch.setattr(init_db, "get_agent_repository", lambda: None)
        kb_delete.rag.docs = None

        response = await _client(make_client).delete("/api/kb/documents/4")

        assert response.status_code == 200 and kb_delete.removed == []

    async def test_kb_delete_failure_is_a_500(self, make_client, kb_delete, monkeypatch):
        import backend.database.init_db as init_db

        monkeypatch.setattr(init_db, "get_agent_repository", lambda: None)
        kb_delete.rag.fail.add("kb_delete")

        response = await _client(make_client).delete("/api/kb/documents/4")

        assert response.status_code == 500 and response.json()["detail"] == "Внутренняя ошибка сервера"
