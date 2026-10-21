"""routes/project_rag: RAG файлов проекта (загрузка, список, поиск, удаление) и удаление проекта целиком."""

from types import SimpleNamespace

import pytest

from backend.routes import project_rag as module
from backend.tests.api.conftest import ALICE

pytestmark = pytest.mark.unit

UP = "/api/project-rag/projects/p1/documents"


class FakeRag:
    def __init__(self):
        self.calls = []
        self.duplicate = None
        self.upload_result = {"document_id": 11, "chunks": 3}
        self.upload_error = None
        self.docs = [{"id": 1, "filename": "a.pdf"}]
        self.delete_result = {"ok": True, "minio_object": "obj", "minio_bucket": "bucket"}
        self.search_results = [("текст", 0.9, 1, 0)]
        self.project_delete = {"minio_keys": [{"minio_object": "o1", "minio_bucket": "b1"}], "deleted_count": 1}
        self.project_delete_error = None

    async def project_rag_check_duplicate(self, **kw):
        self.calls.append(("dup", kw))
        if isinstance(self.duplicate, Exception):
            raise self.duplicate
        return self.duplicate

    async def project_rag_upload_document(self, **kw):
        self.calls.append(("upload", kw))
        if self.upload_error:
            raise self.upload_error
        return self.upload_result

    async def project_rag_list_documents(self, project_id):
        self.calls.append(("list", project_id))
        return self.docs

    async def project_rag_delete_document(self, project_id, document_id):
        self.calls.append(("delete", project_id, document_id))
        return self.delete_result

    async def project_rag_search(self, **kw):
        self.calls.append(("search", kw))
        return self.search_results

    async def project_rag_delete_project(self, project_id):
        self.calls.append(("delete_project", project_id))
        if self.project_delete_error:
            raise self.project_delete_error
        return self.project_delete


@pytest.fixture
def env(monkeypatch, services):
    rag = FakeRag()
    state = SimpleNamespace(rag=rag, removed=[], bumped=[], pvc=False, saved_pvc=[], rag_settings={"embedding_model": "emb", "rag_reranking_enabled": False})
    services(module, "rag", "minio")
    monkeypatch.setattr(module, "rag_client", rag)
    monkeypatch.setattr(module, "minio_client", None)
    monkeypatch.setattr(module, "use_rag_pvc", lambda: state.pvc)
    monkeypatch.setattr(module, "_require_storage_for_delete", lambda: None)
    monkeypatch.setattr(module, "_delete_rag_source_file", lambda obj, bucket: state.removed.append((obj, bucket)))
    monkeypatch.setattr(module, "bump_rag_semantic_cache", lambda: state.bumped.append(True))
    monkeypatch.setattr(module, "settings", SimpleNamespace(minio=SimpleNamespace(project_rag_bucket_name="proj-bucket")))
    monkeypatch.setattr(module, "get_rag_chunk_index_params", lambda: {"chunk_size": 111, "chunk_overlap": 11})
    monkeypatch.setattr(module, "chunk_params_from_rag_settings", lambda s: {"chunk_size": 500, "chunk_overlap": 50})

    async def entity_settings(scope, entity_id):
        return state.rag_settings

    monkeypatch.setattr(module, "get_entity_rag_settings", entity_settings)

    def save_pvc(content, fn, **kw):
        state.saved_pvc.append((fn, kw))
        return "pvc/object.pdf"

    monkeypatch.setattr(module, "save_rag_bytes_to_pvc", save_pvc)

    import backend.database.init_db as init_db
    import backend.services.user_rag_settings as urs

    class _OwnerRepo:
        async def get_by_id(self, user_id, project_id):
            # В API-тестах alice владеет любым проектом; остальные — нет.
            if user_id == "alice":
                return {"id": project_id, "user_id": "alice"}
            return None

        async def delete(self, user_id, project_id):
            return True

        async def delete_by_id(self, project_id):
            return True

    monkeypatch.setattr(init_db, "get_project_repository", lambda: _OwnerRepo())
    monkeypatch.setattr(urs, "embedding_fields_from_rag_settings", lambda s: {"embedding_model": s.get("embedding_model")} if s.get("embedding_model") else {})
    monkeypatch.setattr(urs, "reranker_fields_from_rag_settings", lambda s: {"reranker_model": s.get("reranker_model")} if s.get("reranker_model") else {})
    return state


def _client(make_client, user=ALICE):
    return make_client(module.router, user=user)


def _upload(client, content=b"%PDF-data", name="a.pdf", project="p1"):
    return client.post(f"/api/project-rag/projects/{project}/documents", files={"file": (name, content, "application/pdf")})


class TestUpload:
    async def test_requires_login(self, make_client, env):
        assert (await _upload(_client(make_client, None))).status_code in (401, 403)
        assert env.rag.calls == []

    async def test_indexes_the_file_with_the_project_settings(self, make_client, env):
        response = await _upload(_client(make_client))

        assert response.json() == {"document_id": 11, "chunks": 3}
        (_, kw) = [c for c in env.rag.calls if c[0] == "upload"][0]
        assert (kw["project_id"], kw["filename"], kw["owner_user_id"]) == ("p1", "a.pdf", "alice")
        assert (kw["chunk_size"], kw["chunk_overlap"], kw["embedding_model"]) == (500, 50, "emb")
        assert kw["file_bytes"] == b"%PDF-data"
        assert env.bumped == [True]

    async def test_duplicate_is_checked_before_anything_is_stored(self, make_client, env):
        env.rag.duplicate = {"duplicate": True, "document_id": 3, "filename": "orig.pdf"}
        env.pvc = True

        response = await _upload(_client(make_client))

        assert response.status_code == 409
        assert env.saved_pvc == [] and not [c for c in env.rag.calls if c[0] == "upload"]

    async def test_empty_file(self, make_client, env):
        assert (await _upload(_client(make_client), content=b"")).status_code == 400

    async def test_embedding_model_must_be_chosen_first(self, make_client, env):
        env.rag_settings = {}

        response = await _upload(_client(make_client))

        assert response.status_code == 400 and "эмбеддингов" in response.json()["detail"]
        assert env.saved_pvc == []

    async def test_reranker_is_required_only_when_reranking_is_enabled(self, make_client, env):
        env.rag_settings = {"embedding_model": "emb", "rag_reranking_enabled": True}

        response = await _upload(_client(make_client))

        assert response.status_code == 400 and "реранкера" in response.json()["detail"]

    async def test_pvc_storage_is_used_when_enabled(self, make_client, env):
        env.pvc = True

        await _upload(_client(make_client))

        (fn, kw) = env.saved_pvc[0]
        assert kw["scope"] == "project" and kw["username"] == "alice" and kw["prefix"] == "proj_p1_"
        upload_kw = [c for c in env.rag.calls if c[0] == "upload"][0][1]
        assert upload_kw["minio_object"] == "pvc/object.pdf"
        assert upload_kw["minio_bucket"] == module.RAG_PVC_BUCKET_MARKER

    async def test_pvc_save_failure(self, make_client, env, monkeypatch):
        env.pvc = True
        monkeypatch.setattr(module, "save_rag_bytes_to_pvc", lambda *a, **k: None)

        assert (await _upload(_client(make_client))).status_code == 500

    async def test_minio_storage_is_used_otherwise(self, make_client, env, monkeypatch):
        uploads = []

        class Minio:
            def ensure_bucket(self, bucket):
                uploads.append(("bucket", bucket))

            def generate_object_name(self, prefix="", extension=""):
                return f"{prefix}x{extension}"

            def upload_file(self, data, name, content_type=None, bucket_name=None):
                uploads.append((name, bucket_name))

        monkeypatch.setattr(module, "minio_client", Minio())

        await _upload(_client(make_client))

        assert uploads == [("bucket", "proj-bucket"), ("proj_p1_x.pdf", "proj-bucket")]

    async def test_minio_failure(self, make_client, env, monkeypatch):
        class Minio:
            def ensure_bucket(self, bucket):
                raise RuntimeError("minio down")

        monkeypatch.setattr(module, "minio_client", Minio())

        assert (await _upload(_client(make_client))).status_code == 500

    async def test_indexing_failure_removes_the_stored_file(self, make_client, env):
        env.pvc = True
        env.rag.upload_error = RuntimeError("svc-rag crashed")

        response = await _upload(_client(make_client))

        assert response.status_code == 422
        assert env.removed == [("pvc/object.pdf", module.RAG_PVC_BUCKET_MARKER)]

    async def test_duplicate_found_by_the_indexer_is_a_409_and_cleans_up(self, make_client, env):
        from backend.settings.rag_client import RagDuplicateDocument

        env.pvc = True
        env.rag.upload_error = RagDuplicateDocument("уже есть", document_id=4, filename="x.pdf") if "document_id" in RagDuplicateDocument.__init__.__code__.co_varnames else RagDuplicateDocument("уже есть")

        response = await _upload(_client(make_client))

        assert response.status_code == 409
        assert env.removed

    async def test_service_unavailable(self, make_client, env, monkeypatch):
        monkeypatch.setattr(module, "rag_client", None)

        assert (await _upload(_client(make_client))).status_code == 503

    async def test_feature_flag_off(self, make_client, env, services):
        services(module)

        assert (await _upload(_client(make_client))).status_code == 503


class TestProjectDocumentsAuth:
    def _anon(self, make_client):
        return _client(make_client, None)

    async def test_list(self, make_client, env):
        response = await _client(make_client).get(UP)

        assert response.json() == {"documents": env.rag.docs}

    async def test_search_formats_hits_and_forwards_options(self, make_client, env):
        response = await _client(make_client).post(
            "/api/project-rag/projects/p1/search", json={"query": "кредиты", "k": 3, "document_id": 5, "use_reranking": False, "strategy": "hybrid"}
        )

        assert response.json() == {"hits": [{"content": "текст", "score": 0.9, "document_id": 1, "chunk_index": 0}]}
        (_, kw) = env.rag.calls[0]
        assert kw == {"query": "кредиты", "project_id": "p1", "k": 3, "document_id": 5, "use_reranking": False, "strategy": "hybrid"}

    async def test_search_defaults(self, make_client, env):
        await _client(make_client).post("/api/project-rag/projects/p1/search", json={})

        assert env.rag.calls[0][1]["k"] == 8 and env.rag.calls[0][1]["query"] == ""

    async def test_delete_document(self, make_client, env):
        response = await _client(make_client).delete(f"{UP}/7")

        assert response.json() == {"ok": True, "document_id": 7}
        assert env.removed == [("obj", "bucket")] and env.bumped == [True]

    async def test_delete_missing_document(self, make_client, env):
        env.rag.delete_result = {"ok": False}

        assert (await _client(make_client).delete(f"{UP}/7")).status_code == 404

    async def test_service_unavailable(self, make_client, env, monkeypatch):
        monkeypatch.setattr(module, "rag_client", None)

        assert (await _client(make_client).get(UP)).status_code == 503
        assert (await _client(make_client).delete(f"{UP}/7")).status_code == 503

    async def test_anonymous_visitor_must_not_list_project_files(self, make_client, env):
        assert (await self._anon(make_client).get(UP)).status_code in (401, 403)

    async def test_anonymous_visitor_must_not_delete_project_files(self, make_client, env):
        response = await self._anon(make_client).delete(f"{UP}/7")

        assert response.status_code in (401, 403)
        assert env.removed == []

    async def test_anonymous_visitor_must_not_search_project_files(self, make_client, env):
        response = await self._anon(make_client).post("/api/project-rag/projects/p1/search", json={"query": "зарплаты"})

        assert response.status_code in (401, 403)


class TestDeleteProject:
    @pytest.fixture
    def project(self, monkeypatch, env):
        import backend.database.init_db as init_db
        import backend.database.memory_service as memory_service

        state = SimpleNamespace(memory_deleted=[], repo_calls=[], memory_error=None, repo_error=None)

        async def delete_project_memory(project_id):
            state.memory_deleted.append(project_id)
            if state.memory_error:
                raise state.memory_error
            return 4

        class Repo:
            async def get_by_id(self, user_id, project_id):
                state.repo_calls.append(("get_by_id", user_id, project_id))
                if user_id == "alice":
                    return {"id": project_id, "user_id": "alice"}
                return None

            async def delete(self, user_id, project_id):
                state.repo_calls.append(("delete", user_id, project_id))
                if state.repo_error:
                    raise state.repo_error
                return True

            async def delete_by_id(self, project_id):
                state.repo_calls.append(("delete_by_id", project_id))
                return True

        monkeypatch.setattr(memory_service, "delete_project_memory", delete_project_memory)
        monkeypatch.setattr(init_db, "get_project_repository", lambda: Repo())
        monkeypatch.setattr(module, "is_service_enabled", lambda key: True)
        return state

    async def test_owner_deletes_files_conversations_and_metadata(self, make_client, env, project):
        response = await _client(make_client).delete("/api/projects/p1")

        assert response.json() == {"ok": True, "project_id": "p1", "errors": []}
        assert env.removed == [("o1", "b1")] and env.bumped == [True]
        assert project.memory_deleted == ["p1"]
        assert ("delete", "alice", "p1") in project.repo_calls

    async def test_errors_in_one_step_do_not_stop_the_others(self, make_client, env, project):
        env.rag.project_delete_error = RuntimeError("svc-rag down")
        project.memory_error = RuntimeError("mongo down")

        body = (await _client(make_client).delete("/api/projects/p1")).json()

        assert body["ok"] is False
        assert [e.split(":")[0] for e in body["errors"]] == ["RAG", "MongoDB"]
        assert ("delete", "alice", "p1") in project.repo_calls

    async def test_rag_service_unavailable_is_reported(self, make_client, env, project, monkeypatch):
        monkeypatch.setattr(module, "rag_client", None)

        body = (await _client(make_client).delete("/api/projects/p1")).json()

        assert body["errors"] == ["RAG service недоступен"]

    async def test_storage_must_be_available_first(self, make_client, env, project, monkeypatch):
        from fastapi import HTTPException

        def unavailable():
            raise HTTPException(status_code=503, detail="PVC недоступен")

        monkeypatch.setattr(module, "_require_storage_for_delete", unavailable)

        response = await _client(make_client).delete("/api/projects/p1")

        assert response.status_code == 503
        assert ("delete_project", "p1") not in env.rag.calls

    async def test_anonymous_visitor_must_not_delete_projects(self, make_client, env, project):
        response = await _client(make_client, None).delete("/api/projects/victim-project")

        assert response.status_code in (401, 403)
        assert project.memory_deleted == []

    async def test_another_user_must_not_wipe_someone_elses_project(self, make_client, env, project):
        bob = _client(make_client, {**ALICE, "username": "bob", "user_id": "bob"})

        response = await bob.delete("/api/projects/alices-project")

        assert response.status_code in (401, 403)
        assert project.memory_deleted == []
        assert ("delete_project", "alices-project") not in env.rag.calls
