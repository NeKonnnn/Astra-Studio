"""routes/rag: KB и memory-RAG документы, права на сущности, классификация моделей, проба эмбеддера."""

from types import SimpleNamespace

import pytest

from backend.routes import rag as module
from backend.tests.api.conftest import ALICE

pytestmark = pytest.mark.unit


class FakeRag:
    def __init__(self):
        self.docs = [{"id": 1, "filename": "a.pdf"}, {"id": 2, "filename": "b.pdf"}]
        self.calls = []
        self.delete_result = {"ok": True, "minio_object": "obj", "minio_bucket": "bucket"}
        self.fail = None
        self.duplicate = None

    async def kb_list_documents(self):
        self.calls.append(("kb_list",))
        if self.fail:
            raise self.fail
        return self.docs

    async def memory_rag_list_documents(self):
        self.calls.append(("memory_list",))
        return self.docs

    async def kb_delete_document(self, document_id):
        self.calls.append(("kb_delete", document_id))
        if self.fail:
            raise self.fail
        return self.delete_result

    async def memory_rag_delete_document(self, document_id):
        self.calls.append(("memory_delete", document_id))
        return self.delete_result

    async def kb_check_duplicate(self, **kwargs):
        self.calls.append(("kb_dup", kwargs))
        if isinstance(self.duplicate, Exception):
            raise self.duplicate
        return self.duplicate

    async def memory_rag_check_duplicate(self, **kwargs):
        self.calls.append(("memory_dup", kwargs))
        return self.duplicate


@pytest.fixture
def rag(monkeypatch, services):
    fake = FakeRag()
    removed, bumped = [], []
    services(module, "rag")
    monkeypatch.setattr(module, "rag_client", fake)
    monkeypatch.setattr(module, "_require_storage_for_delete", lambda: None)
    monkeypatch.setattr(module, "_delete_rag_source_file", lambda obj, bucket: removed.append((obj, bucket)))
    monkeypatch.setattr(module, "bump_rag_semantic_cache", lambda: bumped.append(True))
    fake.removed, fake.bumped = removed, bumped
    return fake


def _client(make_client, user=ALICE):
    return make_client(module.router, user=user)


class TestKnowledgeBaseList:
    async def test_lists_documents(self, make_client, rag):
        response = await _client(make_client).get("/api/kb/documents")

        assert response.json() == {"documents": rag.docs}

    async def test_service_unavailable(self, make_client, rag, monkeypatch):
        monkeypatch.setattr(module, "rag_client", None)

        assert (await _client(make_client).get("/api/kb/documents")).status_code == 503

    async def test_feature_flag_off(self, make_client, rag, services):
        services(module)

        assert (await _client(make_client).get("/api/kb/documents")).status_code == 503

    async def test_failure_does_not_leak_the_message(self, make_client, rag):
        rag.fail = RuntimeError("svc-rag at http://10.0.0.5:8011 refused")

        response = await _client(make_client).get("/api/kb/documents")

        # CWE-209: текст исключения (хосты, порты) не должен попадать в ответ.
        assert response.status_code == 500 and "10.0.0.5" not in response.text

    async def test_anyone_sees_every_users_documents_known_issue(self, make_client, rag):
        rag.docs = [{"id": 7, "filename": "salary-2026.xlsx", "owner": "ceo"}]

        response = await _client(make_client).get("/api/kb/documents")

        assert response.json()["documents"][0]["filename"] == "salary-2026.xlsx"


class TestKnowledgeBaseDelete:
    def _agent_repo(self, monkeypatch, foreign=None, error=None):
        import backend.database.init_db as init_db

        calls = []
        unbound = []

        class Repo:
            async def find_documents_claimed_by_other_agents(self, agent_id, ids):
                calls.append((agent_id, ids))
                if error:
                    raise error
                return foreign or []

            async def remove_kb_document_ids(self, agent_id, ids):
                unbound.append((agent_id, list(ids)))
                return True

        monkeypatch.setattr(init_db, "get_agent_repository", lambda: Repo())
        return calls, unbound

    async def test_deletes_the_record_the_source_file_and_resets_the_cache(self, make_client, rag, monkeypatch):
        calls, unbound = self._agent_repo(monkeypatch)

        response = await _client(make_client).delete("/api/kb/documents/5", params={"agent_id": 3})

        assert response.json() == {"ok": True, "document_id": 5, "unbound_only": False}
        assert rag.calls == [("kb_delete", 5)]
        assert rag.removed == [("obj", "bucket")] and rag.bumped == [True]
        assert calls == [(3, [5])]
        assert unbound == [(3, [5])]

    async def test_document_still_used_by_another_agent_is_unbound_only(
        self, make_client, rag, monkeypatch
    ):
        import backend.database.init_db as init_db

        removed = []

        class Repo:
            async def find_documents_claimed_by_other_agents(self, agent_id, ids):
                return [5]

            async def remove_kb_document_ids(self, agent_id, ids):
                removed.append((agent_id, ids))
                return True

        monkeypatch.setattr(init_db, "get_agent_repository", lambda: Repo())

        response = await _client(make_client).delete(
            "/api/kb/documents/5", params={"agent_id": 3}
        )

        assert response.status_code == 200
        assert response.json() == {
            "ok": True,
            "document_id": 5,
            "unbound_only": True,
        }
        assert rag.calls == [] and rag.removed == []
        assert removed == [(3, [5])]

    async def test_missing_document(self, make_client, rag, monkeypatch):
        self._agent_repo(monkeypatch)
        rag.delete_result = {"ok": False}

        response = await _client(make_client).delete("/api/kb/documents/5")

        assert response.status_code == 404 and rag.removed == []

    async def test_reference_check_errors_do_not_block_the_delete(self, make_client, rag, monkeypatch):
        self._agent_repo(monkeypatch, error=RuntimeError("db down"))

        response = await _client(make_client).delete("/api/kb/documents/5")

        assert response.status_code == 200

    async def test_storage_must_be_available_before_touching_svc_rag(self, make_client, rag, monkeypatch):
        from fastapi import HTTPException

        def unavailable():
            raise HTTPException(status_code=503, detail="PVC недоступен")

        monkeypatch.setattr(module, "_require_storage_for_delete", unavailable)

        response = await _client(make_client).delete("/api/kb/documents/5")

        assert response.status_code == 503 and rag.calls == []

    async def test_non_numeric_id(self, make_client, rag):
        assert (await _client(make_client).delete("/api/kb/documents/abc")).status_code == 422

    async def test_service_unavailable(self, make_client, rag, monkeypatch):
        monkeypatch.setattr(module, "rag_client", None)

        assert (await _client(make_client).delete("/api/kb/documents/5")).status_code == 503

    async def test_anonymous_visitor_must_not_delete_documents(self, make_client, rag, monkeypatch):
        self._agent_repo(monkeypatch)

        response = await _client(make_client, None).delete("/api/kb/documents/5")

        assert response.status_code in (401, 403)
        assert rag.calls == []


class TestMemoryRag:
    async def test_list(self, make_client, rag):
        assert (await _client(make_client).get("/api/memory-rag/documents")).json() == {"documents": rag.docs}

    async def test_delete(self, make_client, rag):
        response = await _client(make_client).delete("/api/memory-rag/documents/9")

        assert response.json() == {"ok": True, "document_id": 9}
        assert rag.removed == [("obj", "bucket")] and rag.bumped == [True]

    async def test_delete_missing(self, make_client, rag):
        rag.delete_result = {"ok": False}

        assert (await _client(make_client).delete("/api/memory-rag/documents/9")).status_code == 404

    async def test_service_unavailable(self, make_client, rag, monkeypatch):
        monkeypatch.setattr(module, "rag_client", None)

        assert (await _client(make_client).get("/api/memory-rag/documents")).status_code == 503
        assert (await _client(make_client).delete("/api/memory-rag/documents/9")).status_code == 503

    async def test_anonymous_visitor_must_not_delete_documents(self, make_client, rag):
        response = await _client(make_client, None).delete("/api/memory-rag/documents/9")

        assert response.status_code in (401, 403)
        assert rag.calls == []


class TestDuplicateChecks:
    async def test_kb_duplicate_is_rejected_with_409(self, rag):
        from fastapi import HTTPException

        rag.duplicate = {"duplicate": True, "document_id": 3, "filename": "orig.pdf"}

        with pytest.raises(HTTPException) as exc:
            await module._raise_if_kb_duplicate(b"content", "new.pdf", agent_id=4)

        assert exc.value.status_code == 409
        assert exc.value.detail["document_id"] == 3 and exc.value.detail["filename"] == "orig.pdf"
        (name, kwargs) = rag.calls[0]
        assert kwargs["agent_id"] == 4 and kwargs["size"] == 7 and len(kwargs["content_hash"]) == 64

    async def test_unique_files_pass(self, rag):
        rag.duplicate = {"duplicate": False}

        assert await module._raise_if_kb_duplicate(b"x", "a.pdf") is None
        assert await module._raise_if_memory_duplicate(b"x", "a.pdf") is None

    async def test_checker_failures_do_not_block_uploads(self, rag):
        rag.duplicate = RuntimeError("svc-rag timeout")

        assert await module._raise_if_kb_duplicate(b"x", "a.pdf") is None

    async def test_without_a_rag_client_nothing_is_checked(self, monkeypatch):
        monkeypatch.setattr(module, "rag_client", None)

        assert await module._raise_if_kb_duplicate(b"x", "a.pdf") is None
        assert await module._raise_if_memory_duplicate(b"x", "a.pdf") is None

    async def test_memory_duplicate_is_rejected(self, rag):
        from fastapi import HTTPException

        rag.duplicate = {"duplicate": True, "document_id": 1}

        with pytest.raises(HTTPException) as exc:
            await module._raise_if_memory_duplicate(b"x", "a.pdf")

        assert exc.value.status_code == 409 and exc.value.detail["filename"] == "a.pdf"


class TestEntityPermissions:
    @pytest.fixture
    def repos(self, monkeypatch):
        import backend.database.init_db as init_db

        state = SimpleNamespace(agent_permission="viewer", project=None, agent_repo=True, project_repo=True, error=None)

        class AgentRepo:
            async def get_user_permission(self, agent_id, uid, is_admin=False):
                if state.error:
                    raise state.error
                return "owner" if is_admin else state.agent_permission

        class ProjectRepo:
            async def get_by_id(self, uid, pid):
                return state.project

        monkeypatch.setattr(init_db, "get_agent_repository", lambda: AgentRepo() if state.agent_repo else None)
        monkeypatch.setattr(init_db, "get_project_repository", lambda: ProjectRepo() if state.project_repo else None)
        return state

    @pytest.mark.parametrize("permission", ["owner", "editor", " Editor ", "OWNER"])
    def test_owner_and_editor_may_edit(self, permission):
        assert module._can_edit(permission) is True

    @pytest.mark.parametrize("permission", [None, "", "viewer", "reader", "admin"])
    def test_others_may_not(self, permission):
        assert module._can_edit(permission) is False

    async def test_agent_role_comes_from_the_repository(self, repos):
        repos.agent_permission = "editor"

        assert await module._entity_permission("agent", "7", "Alice") == "editor"

    async def test_admin_flag_is_forwarded(self, repos):
        assert await module._entity_permission("agent", "7", "alice", is_admin=True) == "owner"

    @pytest.mark.parametrize(("entity_id", "user_id"), [("", "alice"), ("7", ""), (None, "alice")])
    async def test_missing_identifiers_give_no_role(self, repos, entity_id, user_id):
        assert await module._entity_permission("agent", entity_id, user_id) is None

    async def test_garbage_agent_id_and_missing_repository(self, repos):
        assert await module._entity_permission("agent", "not-a-number", "alice") is None
        repos.agent_repo = False
        assert await module._entity_permission("agent", "7", "alice") is None

    async def test_repository_errors_deny_access(self, repos):
        repos.error = RuntimeError("db down")

        assert await module._entity_permission("agent", "7", "alice") is None

    async def test_project_is_owned_only_by_its_creator(self, repos):
        repos.project = {"id": "p1"}
        assert await module._entity_permission("project", "p1", "alice") == "owner"
        repos.project = None
        assert await module._entity_permission("project", "p1", "alice") is None

    async def test_require_editor_raises_403_for_viewers_and_strangers(self, repos):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as viewer:
            await module._require_entity_editor("agent", "7", "alice")
        repos.project = None
        with pytest.raises(HTTPException) as stranger:
            await module._require_entity_editor("project", "p1", "alice")

        assert viewer.value.status_code == 403 and "агента" in viewer.value.detail
        assert stranger.value.status_code == 403 and "проекта" in stranger.value.detail

    async def test_require_editor_returns_the_role(self, repos):
        repos.agent_permission = "editor"

        assert await module._require_entity_editor("agent", "7", "alice") == "editor"


class TestEntityHelpers:
    @pytest.mark.parametrize(
        ("scope", "project_id", "agent_id", "expected"),
        [
            ("project", " p1 ", None, "p1"),
            ("project", "", None, None),
            ("agent", None, 7, "7"),
            ("agent", None, "8", "8"),
            ("agent", None, 0, None),
            ("agent", None, -3, None),
            ("agent", None, "x", None),
            ("agent", None, None, None),
        ],
    )
    def test_entity_id(self, scope, project_id, agent_id, expected):
        assert module._entity_id_from_params(scope, project_id, agent_id) == expected

    def test_label(self):
        assert module._entity_label("agent", " Юрист ", "7") == "«Юрист»"
        assert module._entity_label("agent", "", "7") == "7"
        assert module._entity_label("agent", None, None) == "?"

    def test_keys_and_users_are_normalized(self):
        assert module._norm_uid("  Alice ") == "alice" and module._norm_uid(None) == ""
        assert module._reindex_key("agent", " 7 ") == "agent:7"
        assert module._reindex_key("agent", "") == "agent"

    def test_upload_username_falls_back(self):
        assert module._rag_upload_username({"username": "a", "user_id": "b"}) == "a"
        assert module._rag_upload_username({"user_id": "b"}) == "b"
        assert module._rag_upload_username({}) == "anonymous"


class TestModelClassification:
    @pytest.mark.parametrize("model_id", ["bge-reranker-v2", "rerank/ms-marco", "cross-encoder/x"])
    def test_rerankers(self, model_id):
        assert module._rag_model_kind(model_id, rag_only=False) == "reranker"

    @pytest.mark.parametrize("model_id", ["embed/FRIDA", "bge-m3-embedding", "text-embedding-3-small", "multilingual-e5-large"])
    def test_embedders_are_recognized_by_name(self, model_id):
        assert module._rag_model_kind(model_id, rag_only=False) in ("embedding", None)

    def test_chat_models_are_not_rag_models_unless_the_gateway_is_rag_only(self):
        assert module._rag_model_kind("vllm/qwen3-8b", rag_only=False) is None
        assert module._rag_model_kind("vllm/qwen3-8b", rag_only=True) == "embedding"
        assert module._rag_model_kind("FRIDA", rag_only=True) == "embedding"

    def test_name_hints_are_extended_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("X_HINTS", "Foo; bar ,baz")

        hints = module._name_hints("X_HINTS", ("default",))

        assert hints == ("default", "foo", "bar", "baz")

    def test_name_hints_do_not_duplicate_defaults(self, monkeypatch):
        monkeypatch.setenv("X_HINTS", "default,new")

        assert module._name_hints("X_HINTS", ("default",)) == ("default", "new")


class TestEmbeddingProbeParsing:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ([0.1] * 8, 8),
            ([0.1] * 1024, 1024),
            ([0.1] * 7, 0),
            ({"data": [{"embedding": [0.5] * 16}]}, 16),
            ({"choices": [{"message": {"content": [1.0] * 12}}]}, 12),
            ([["x"], [0.1] * 9], 9),
            ({"a": "text"}, 0),
            ([], 0),
            (None, 0),
            ("string", 0),
        ],
    )
    def test_first_vector_len(self, value, expected):
        assert module._first_vector_len(value) == expected

    def test_deeply_nested_values_are_not_followed_forever(self):
        value = [0.1] * 8
        for _ in range(10):
            value = [value]

        assert module._first_vector_len(value) == 0

    @staticmethod
    def _response(status, payload=None, text="", bad_json=False):
        def json():
            if bad_json:
                raise ValueError("not json")
            return payload

        return SimpleNamespace(status_code=status, json=json, text=text)

    def test_vector_means_ok(self):
        status, dim, _ = module._classify_probe_response(self._response(200, {"data": [{"embedding": [0.1] * 32}]}))

        assert (status, dim) == (module._PROBE_OK, 32)

    def test_chat_answer_without_a_vector_is_bad(self):
        status, _, reason = module._classify_probe_response(self._response(200, {"choices": [{"message": {"content": "hi"}}]}))

        assert status == module._PROBE_BAD and "не эмбеддер" in reason

    def test_non_json_200_is_bad(self):
        assert module._classify_probe_response(self._response(200, bad_json=True))[0] == module._PROBE_BAD

    @pytest.mark.parametrize("code", [400, 401, 403, 422])
    def test_explicit_rejection_hides_the_model(self, code):
        status, _, reason = module._classify_probe_response(self._response(code, text="Invalid model name"))

        assert status == module._PROBE_BAD and reason.startswith(f"HTTP {code}")

    @pytest.mark.parametrize("code", [404, 405, 500, 502, 503, 504])
    def test_gateway_trouble_keeps_the_model_visible(self, code):
        assert module._classify_probe_response(self._response(code))[0] == module._PROBE_UNKNOWN

    def test_long_error_bodies_are_truncated(self):
        _, _, reason = module._classify_probe_response(self._response(400, text="x" * 1000))

        assert len(reason) < 200
