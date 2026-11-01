"""routes/project_rag: ветки ошибок (сервис недоступен, сбои зависимостей), хранилище исходников, удаление проекта."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.routes import project_rag as module
from backend.storage.rag_pvc import RagPvcUnavailable
from backend.tests.api.conftest import ALICE
from backend.tests.api.test_project_rag_api import UP, FakeRag, _client, _upload, env  # noqa: F401  (env — фикстура)

pytestmark = pytest.mark.unit


class TestDuplicateCheck:
    async def test_without_a_rag_client_nothing_is_checked(self, monkeypatch):
        monkeypatch.setattr(module, "rag_client", None)

        assert await module._raise_if_project_duplicate("p1", b"x", "a.pdf") is None

    async def test_checker_failure_does_not_block_the_upload(self, make_client, env):
        env.rag.duplicate = RuntimeError("svc-rag timeout")

        response = await _upload(_client(make_client))

        assert response.status_code == 200
        assert [c[0] for c in env.rag.calls] == ["dup", "upload"]

    async def test_duplicate_payload_is_forwarded_in_the_409(self, env):
        env.rag.duplicate = {"duplicate": True, "document_id": 3, "filename": "orig.pdf"}

        with pytest.raises(HTTPException) as exc:
            await module._raise_if_project_duplicate("p1", b"content", "new.pdf")

        assert exc.value.status_code == 409
        assert exc.value.detail["document_id"] == 3 and exc.value.detail["filename"] == "orig.pdf"
        (_, kw) = env.rag.calls[0]
        assert kw["project_id"] == "p1" and kw["size"] == 7 and len(kw["content_hash"]) == 64

    async def test_not_a_duplicate(self, env):
        env.rag.duplicate = {"duplicate": False}

        assert await module._raise_if_project_duplicate("p1", b"x", "a.pdf") is None


class TestStorageHelpers:
    def test_storage_check_passes_when_the_volume_is_available(self, monkeypatch):
        monkeypatch.setattr(module, "require_rag_pvc_available", lambda: None)

        assert module._require_storage_for_delete() is None

    def test_missing_volume_is_a_503_with_its_message(self, monkeypatch):
        def unavailable():
            raise RagPvcUnavailable("том /ragdb не смонтирован")

        monkeypatch.setattr(module, "require_rag_pvc_available", unavailable)

        with pytest.raises(HTTPException) as exc:
            module._require_storage_for_delete()

        assert exc.value.status_code == 503 and exc.value.detail == "Хранилище файлов недоступно"

    @pytest.mark.parametrize(("obj", "bucket"), [(None, "b"), ("o", None), ("", ""), (None, None)])
    def test_nothing_to_delete_without_object_or_bucket(self, monkeypatch, obj, bucket):
        monkeypatch.setattr(module, "delete_rag_pvc_file", lambda *a: pytest.fail("не должен вызываться"))
        monkeypatch.setattr(module, "minio_client", SimpleNamespace(delete_file=lambda *a, **k: pytest.fail("не должен вызываться")))

        assert module._delete_rag_source_file(obj, bucket) is None

    def test_pvc_source_is_removed_from_the_volume(self, monkeypatch):
        deleted = []
        monkeypatch.setattr(module, "is_rag_pvc_bucket", lambda bucket: bucket == "pvc-marker")
        monkeypatch.setattr(module, "delete_rag_pvc_file", lambda obj, bucket: deleted.append((obj, bucket)))
        monkeypatch.setattr(module, "minio_client", SimpleNamespace(delete_file=lambda *a, **k: pytest.fail("MinIO не должен вызываться")))

        module._delete_rag_source_file("proj_1.pdf", "pvc-marker")

        assert deleted == [("proj_1.pdf", "pvc-marker")]

    def test_minio_source_is_removed_from_its_bucket(self, monkeypatch):
        calls = []
        monkeypatch.setattr(module, "is_rag_pvc_bucket", lambda bucket: False)
        monkeypatch.setattr(module, "minio_client", SimpleNamespace(delete_file=lambda obj, bucket_name=None: calls.append((obj, bucket_name))))

        module._delete_rag_source_file("proj_1.pdf", "project-bucket")

        assert calls == [("proj_1.pdf", "project-bucket")]

    def test_minio_failure_is_swallowed(self, monkeypatch):
        def broken(obj, bucket_name=None):
            raise RuntimeError("minio down")

        monkeypatch.setattr(module, "is_rag_pvc_bucket", lambda bucket: False)
        monkeypatch.setattr(module, "minio_client", SimpleNamespace(delete_file=broken))

        assert module._delete_rag_source_file("o", "b") is None

    def test_without_minio_the_source_is_left_alone(self, monkeypatch):
        monkeypatch.setattr(module, "is_rag_pvc_bucket", lambda bucket: False)
        monkeypatch.setattr(module, "minio_client", None)

        assert module._delete_rag_source_file("o", "b") is None


class TestUploadFailures:
    async def test_http_error_from_the_indexer_is_forwarded_and_cleans_up(self, make_client, env):
        env.pvc = True
        env.rag.upload_error = HTTPException(status_code=413, detail="слишком большой")

        response = await _upload(_client(make_client))

        assert response.status_code == 413 and response.json()["detail"] == "слишком большой"
        assert env.removed == [("pvc/object.pdf", module.RAG_PVC_BUCKET_MARKER)]

    async def test_http_error_from_the_indexer_without_a_stored_file(self, make_client, env):
        env.rag.upload_error = HTTPException(status_code=413, detail="слишком большой")

        response = await _upload(_client(make_client))

        assert response.status_code == 413 and env.removed == []

    async def test_reranker_model_is_forwarded_when_reranking_is_enabled(self, make_client, env):
        env.rag_settings = {"embedding_model": "emb", "rag_reranking_enabled": True, "reranker_model": "rr"}

        response = await _upload(_client(make_client))

        assert response.status_code == 200
        (_, kw) = [c for c in env.rag.calls if c[0] == "upload"][0]
        assert kw["embedding_model"] == "emb"

    async def test_indexer_failure_without_a_stored_file_has_nothing_to_remove(self, make_client, env):
        env.rag.upload_error = RuntimeError("svc-rag crashed")

        response = await _upload(_client(make_client))

        assert response.status_code == 422 and env.removed == []
        assert "svc-rag crashed" not in response.text  # CWE-209

    async def test_unexpected_error_before_storage_is_a_500(self, make_client, env, monkeypatch):
        async def broken(scope, entity_id):
            raise RuntimeError("db down")

        monkeypatch.setattr(module, "get_entity_rag_settings", broken)

        response = await _upload(_client(make_client))

        assert response.status_code == 500 and env.saved_pvc == []

    async def test_user_without_an_id_is_refused(self, make_client, env):
        # BUG-BE-05 исправлен: без user_id проверка владельца проекта даёт 401, загрузки нет.
        client = _client(make_client, {"username": "svc", "user_id": ""})

        response = await _upload(client)

        assert response.status_code == 401
        assert not [c for c in env.rag.calls if c[0] == "upload"]

    async def test_file_is_stored_under_a_user_with_only_an_id(self, make_client, env, monkeypatch):
        env.pvc = True
        import backend.database.init_db as init_db

        class Projects:
            async def get_by_id(self, user_id, project_id):
                return {"id": project_id}

        monkeypatch.setattr(init_db, "get_project_repository", lambda: Projects())

        await _upload(_client(make_client, {"user_id": "bob"}))

        assert env.saved_pvc[0][1]["username"] == "bob"

    async def test_minio_source_is_used_for_cleanup_when_indexing_fails(self, make_client, env, monkeypatch):
        class Minio:
            def ensure_bucket(self, bucket):
                pass

            def generate_object_name(self, prefix="", extension=""):
                return f"{prefix}x{extension}"

            def upload_file(self, data, name, content_type=None, bucket_name=None):
                pass

        monkeypatch.setattr(module, "minio_client", Minio())
        env.rag.upload_error = RuntimeError("svc-rag crashed")

        await _upload(_client(make_client))

        assert env.removed == [("proj_p1_x.pdf", "proj-bucket")]


class TestListSearchDeleteFailures:
    def _anon(self, make_client):
        return _client(make_client)

    async def test_list_failure_is_a_500(self, make_client, env):
        async def broken(project_id):
            raise RuntimeError("svc-rag down")

        env.rag.project_rag_list_documents = broken

        response = await self._anon(make_client).get(UP)

        assert response.status_code == 500 and response.json()["detail"] == "Внутренняя ошибка сервера"

    async def test_delete_failure_is_a_500_and_keeps_the_file(self, make_client, env):
        async def broken(project_id, document_id):
            raise RuntimeError("svc-rag down")

        env.rag.project_rag_delete_document = broken

        response = await self._anon(make_client).delete(f"{UP}/7")

        assert response.status_code == 500 and env.removed == [] and env.bumped == []

    async def test_search_failure_is_a_500(self, make_client, env):
        async def broken(**kw):
            raise RuntimeError("svc-rag down")

        env.rag.project_rag_search = broken

        response = await self._anon(make_client).post("/api/project-rag/projects/p1/search", json={"query": "q"})

        assert response.status_code == 500

    async def test_search_without_service(self, make_client, env, monkeypatch):
        monkeypatch.setattr(module, "rag_client", None)

        response = await self._anon(make_client).post("/api/project-rag/projects/p1/search", json={"query": "q"})

        assert response.status_code == 503

    async def test_search_feature_flag_off(self, make_client, env, services):
        services(module)

        response = await self._anon(make_client).post("/api/project-rag/projects/p1/search", json={"query": "q"})

        assert response.status_code == 503

    async def test_delete_document_requires_the_storage_first(self, make_client, env, monkeypatch):
        def unavailable():
            raise HTTPException(status_code=503, detail="PVC недоступен")

        monkeypatch.setattr(module, "_require_storage_for_delete", unavailable)

        response = await self._anon(make_client).delete(f"{UP}/7")

        assert response.status_code == 503 and env.rag.calls == []

    async def test_non_numeric_document_id(self, make_client, env):
        assert (await self._anon(make_client).delete(f"{UP}/abc")).status_code == 422

    async def test_service_error_text_is_not_returned_to_the_client(self, make_client, env):
        async def broken(project_id):
            raise RuntimeError("postgres://svc:secret@10.0.0.5/rag refused")

        env.rag.project_rag_list_documents = broken

        response = await self._anon(make_client).get(UP)

        # BUG-BE-08 исправлен: текст исключения (адрес, креды) наружу не уходит.
        assert response.status_code == 500
        assert "10.0.0.5" not in response.json()["detail"]
        assert response.json()["detail"] == "Внутренняя ошибка сервера"


class TestDeleteProjectFailures:
    @pytest.fixture
    def project(self, monkeypatch, env):
        import backend.database.init_db as init_db
        import backend.database.memory_service as memory_service

        state = SimpleNamespace(memory_deleted=[], repo_calls=[], repo_error=None, deleted=True)

        async def delete_project_memory(project_id):
            state.memory_deleted.append(project_id)
            return 0

        class Repo:
            async def delete(self, user_id, project_id):
                state.repo_calls.append(("delete", user_id, project_id))
                if state.repo_error:
                    raise state.repo_error
                return state.deleted

            async def get_by_id(self, user_id, project_id):
                return {"id": project_id, "user_id": user_id}

            async def delete_by_id(self, project_id):
                state.repo_calls.append(("delete_by_id", project_id))
                return state.deleted

        monkeypatch.setattr(memory_service, "delete_project_memory", delete_project_memory)
        monkeypatch.setattr(init_db, "get_project_repository", lambda: Repo())
        monkeypatch.setattr(module, "is_service_enabled", lambda key: True)
        return state

    async def test_metadata_failure_is_reported_and_the_rest_is_done(self, make_client, env, project):
        project.repo_error = RuntimeError("pg down")

        body = (await _client(make_client).delete("/api/projects/p1")).json()

        assert body["ok"] is False and body["errors"] == ["PostgreSQL: ошибка удаления"] and "pg down" not in str(body)
        assert env.removed == [("o1", "b1")] and project.memory_deleted == ["p1"]

    async def test_project_without_metadata_is_still_ok(self, make_client, env, project):
        project.deleted = False

        body = (await _client(make_client).delete("/api/projects/p1")).json()

        assert body == {"ok": True, "project_id": "p1", "errors": []}

    async def test_disabled_rag_service_is_reported_but_other_data_is_removed(self, make_client, env, project, monkeypatch):
        monkeypatch.setattr(module, "is_service_enabled", lambda key: False)

        body = (await _client(make_client).delete("/api/projects/p1")).json()

        assert body["errors"] == ["RAG service недоступен"] and env.rag.calls == []
        assert project.memory_deleted == ["p1"] and project.repo_calls == [("delete", "alice", "p1")]

    async def test_response_without_storage_keys(self, make_client, env, project):
        env.rag.project_delete = {"deleted_count": 0}

        body = (await _client(make_client).delete("/api/projects/p1")).json()

        assert body["ok"] is True and env.removed == [] and env.bumped == [True]

    async def test_mongo_import_failure_is_reported(self, make_client, env, project, monkeypatch):
        import backend.database.memory_service as memory_service

        async def broken(project_id):
            raise ConnectionError("mongo down")

        monkeypatch.setattr(memory_service, "delete_project_memory", broken)

        body = (await _client(make_client).delete("/api/projects/p1")).json()

        assert body["errors"] == ["MongoDB: ошибка удаления"] and "mongo down" not in str(body)


class TestUploadOwnership:
    # BUG-BE-05 исправлен в банковском коде: чужой проект -> 403.
    async def test_stranger_must_not_upload_into_someone_elses_project(self, make_client, env, monkeypatch):
        import backend.database.init_db as init_db

        class Projects:
            async def get_by_id(self, user_id, project_id):
                return {"id": project_id} if user_id == "alice" else None

        monkeypatch.setattr(init_db, "get_project_repository", lambda: Projects())
        bob = _client(make_client, {**ALICE, "username": "bob", "user_id": "bob"})

        response = await _upload(bob, project="alices-project")

        assert response.status_code in (403, 404)
        assert not [c for c in env.rag.calls if c[0] == "upload"]
