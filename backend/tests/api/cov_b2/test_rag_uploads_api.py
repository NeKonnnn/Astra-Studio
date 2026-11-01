"""routes/rag: загрузка документов в KB агента и в библиотеку памяти (права, дубликаты, PVC/MinIO, сбои индексации)."""

from types import SimpleNamespace

import httpx
import pytest

from backend.routes import rag as module
from backend.settings.rag_client import RagDuplicateDocument
from backend.tests.api.conftest import ALICE

pytestmark = pytest.mark.unit

KB = "/api/kb/documents"
MEMORY = "/api/memory-rag/documents"


class FakeRag:
    def __init__(self):
        self.calls = []
        self.duplicate = None
        self.upload_error = None
        self.result = {"document_id": 11, "chunks": 3}

    async def kb_check_duplicate(self, **kw):
        self.calls.append(("kb_dup", kw))
        return self.duplicate

    async def memory_rag_check_duplicate(self, **kw):
        self.calls.append(("memory_dup", kw))
        return self.duplicate

    async def kb_upload_document(self, **kw):
        self.calls.append(("kb_upload", kw))
        if self.upload_error:
            raise self.upload_error
        return self.result

    async def memory_rag_index_document(self, **kw):
        self.calls.append(("memory_upload", kw))
        if self.upload_error:
            raise self.upload_error
        return self.result

    def of(self, name):
        return [kw for n, kw in self.calls if n == name]


class FakeAgents:
    def __init__(self):
        self.permission = "owner"
        self.agent = SimpleNamespace(author_id=" OwnerUser ")
        self.permission_calls = []

    async def get_user_permission(self, agent_id, user_id, is_admin=False):
        self.permission_calls.append((agent_id, user_id, is_admin))
        return self.permission

    async def get_agent(self, agent_id, user_id=None, is_admin=False):
        return self.agent


@pytest.fixture
def env(monkeypatch, services):
    import backend.database.init_db as init_db
    import backend.services.user_rag_settings as urs

    rag, agents = FakeRag(), FakeAgents()
    state = SimpleNamespace(
        rag=rag,
        agents=agents,
        mp=monkeypatch,
        removed=[],
        bumped=[],
        pvc=False,
        pvc_saved=[],
        pvc_result="pvc/obj.pdf",
        rag_settings={"rag_embedding_model_path": "phoenix/emb", "rag_reranking_enabled": False},
        storage_ok=True,
    )
    services(module, "rag", "minio")
    monkeypatch.setattr(module, "rag_client", rag)
    monkeypatch.setattr(module, "minio_client", None)
    monkeypatch.setattr(module, "use_rag_pvc", lambda: state.pvc)
    monkeypatch.setattr(module, "_require_storage_for_delete", lambda: None)
    monkeypatch.setattr(module, "_delete_rag_source_file", lambda o, b: state.removed.append((o, b)))
    monkeypatch.setattr(module, "bump_rag_semantic_cache", lambda: state.bumped.append(True))
    monkeypatch.setattr(module, "get_rag_chunk_index_params", lambda: {"chunk_size": 111, "chunk_overlap": 11, "chunking_strategy": "hierarchical"})
    monkeypatch.setattr(module, "get_library_chunk_index_params", lambda: {"chunk_size": 999, "chunk_overlap": 99, "chunking_strategy": "universal"})
    monkeypatch.setattr(
        module,
        "chunk_params_from_rag_settings",
        lambda s: {"chunk_size": 500, "chunk_overlap": 50, "chunking_strategy": "fixed"},
    )
    monkeypatch.setattr(module, "settings", SimpleNamespace(minio=SimpleNamespace(memory_rag_bucket_name="mem-bucket")))

    async def entity_settings(scope, entity_id):
        return dict(state.rag_settings)

    monkeypatch.setattr(module, "get_entity_rag_settings", entity_settings)

    def save_pvc(content, fn, **kw):
        state.pvc_saved.append((fn, kw))
        return state.pvc_result

    monkeypatch.setattr(module, "save_rag_bytes_to_pvc", save_pvc)
    monkeypatch.setattr(
        urs,
        "embedding_fields_from_rag_settings",
        lambda s: {"embedding_model": "emb", "embedding_provider": "phoenix"} if s.get("rag_embedding_model_path") else {},
    )
    monkeypatch.setattr(
        urs,
        "reranker_fields_from_rag_settings",
        lambda s: {"reranker_model": "rr"} if s.get("rag_reranker_model_path") else {},
    )
    monkeypatch.setattr(init_db, "get_agent_repository", lambda: agents)
    return state


def _client(make_client, user=ALICE):
    return make_client(module.router, user=user)


def _kb(client, content=b"%PDF-data", name="a.pdf", **form):
    return client.post(KB, files={"file": (name, content, "application/pdf")}, data={k: str(v) for k, v in form.items()})


def _memory(client, content=b"%PDF-data", name="a.pdf"):
    return client.post(MEMORY, files={"file": (name, content, "application/pdf")})


class MinioStub:
    def __init__(self, fail_on=None):
        self.fail_on = fail_on
        self.events = []

    def ensure_bucket(self, bucket):
        self.events.append(("bucket", bucket))
        if self.fail_on == "bucket":
            raise RuntimeError("minio down")

    def generate_object_name(self, prefix="", extension=""):
        return f"{prefix}x{extension}"

    def upload_file(self, data, name, content_type=None, bucket_name=None):
        self.events.append(("upload", name, bucket_name, content_type))
        if self.fail_on == "upload":
            raise RuntimeError("upload failed")


# ---------- KB: проверки ----------


class TestKbUploadGuards:
    async def test_requires_login(self, make_client, env):
        assert (await _kb(_client(make_client, None))).status_code in (401, 403)
        assert env.rag.calls == []

    async def test_feature_flag_off(self, make_client, env, services):
        services(module)

        assert (await _kb(_client(make_client))).status_code == 503

    async def test_service_unavailable(self, make_client, env):
        env.mp.setattr(module, "rag_client", None)

        assert (await _kb(_client(make_client))).status_code == 503

    async def test_empty_file(self, make_client, env):
        response = await _kb(_client(make_client), content=b"")

        assert response.status_code == 400 and response.json()["detail"] == "Файл пустой"

    async def test_unreadable_upload_is_a_500(self, make_client, env, monkeypatch):
        from starlette.datastructures import UploadFile

        async def broken(self, size=-1):
            raise OSError("stream closed")

        monkeypatch.setattr(UploadFile, "read", broken)

        response = await _kb(_client(make_client))

        assert response.status_code == 500 and response.json()["detail"] == "Внутренняя ошибка сервера"

    async def test_duplicate_is_rejected_before_anything_is_stored(self, make_client, env):
        env.rag.duplicate = {"duplicate": True, "document_id": 3, "filename": "orig.pdf"}
        env.pvc = True

        response = await _kb(_client(make_client), agent_id=5)

        assert response.status_code == 409 and response.json()["detail"]["document_id"] == 3
        assert env.pvc_saved == [] and env.rag.of("kb_upload") == []
        assert env.rag.of("kb_dup")[0]["agent_id"] == 5


# ---------- KB: агент и права ----------


class TestKbUploadAgentRights:
    @pytest.mark.parametrize("permission", ["viewer", None, ""])
    async def test_only_owner_and_editor_may_upload(self, make_client, env, permission):
        env.agents.permission = permission

        response = await _kb(_client(make_client), agent_id=5)

        assert response.status_code == 403 and "Недостаточно прав" in response.json()["detail"]
        assert env.rag.calls == []

    async def test_editor_uploads_into_the_owners_name(self, make_client, env):
        env.agents.permission = "editor"

        response = await _kb(_client(make_client), agent_id=5)

        assert response.json() == {"document_id": 11, "chunks": 3}
        (kw,) = env.rag.of("kb_upload")
        assert (kw["owner_user_id"], kw["agent_id"], kw["uploaded_by"]) == ("owneruser", 5, "alice")
        assert env.agents.permission_calls == [(5, "alice", False)]
        assert env.bumped == [True]

    async def test_admin_flag_is_forwarded(self, make_client, env):
        from backend.tests.api.conftest import ADMIN

        await _kb(_client(make_client, ADMIN), agent_id=5)

        assert env.agents.permission_calls == [(5, "root", True)]

    async def test_missing_agent(self, make_client, env):
        env.agents.agent = None

        response = await _kb(_client(make_client), agent_id=5)

        assert response.status_code == 404 and response.json()["detail"] == "Агент не найден"

    async def test_agent_without_an_author_belongs_to_the_uploader(self, make_client, env):
        env.agents.agent = SimpleNamespace(author_id=None)

        await _kb(_client(make_client), agent_id=5)

        assert env.rag.of("kb_upload")[0]["owner_user_id"] == "alice"

    async def test_embedding_model_must_be_chosen_for_the_agent(self, make_client, env):
        env.rag_settings = {}

        response = await _kb(_client(make_client), agent_id=5)

        assert response.status_code == 400 and "эмбеддингов" in response.json()["detail"]
        assert env.rag.of("kb_upload") == []

    async def test_reranker_is_required_while_reranking_is_on(self, make_client, env):
        env.rag_settings = {"rag_embedding_model_path": "phoenix/emb", "rag_reranking_enabled": True}

        response = await _kb(_client(make_client), agent_id=5)

        assert response.status_code == 400 and "реранкера" in response.json()["detail"]

    async def test_reranker_chosen_lets_the_upload_through(self, make_client, env):
        env.rag_settings = {"rag_embedding_model_path": "phoenix/emb", "rag_reranking_enabled": True, "rag_reranker_model_path": "phoenix/rr"}

        assert (await _kb(_client(make_client), agent_id=5)).status_code == 200

    async def test_agent_settings_drive_chunking_and_embedding(self, make_client, env):
        await _kb(_client(make_client), agent_id=5)

        (kw,) = env.rag.of("kb_upload")
        assert (kw["chunk_size"], kw["chunk_overlap"], kw["chunking_strategy"]) == (500, 50, "fixed")
        assert (kw["embedding_model"], kw["embedding_provider"]) == ("emb", "phoenix")
        assert kw["file_bytes"] == b"%PDF-data" and kw["filename"] == "a.pdf" and kw["minio_object"] is None


# ---------- KB: стратегия нарезки без агента ----------


class TestKbChunking:
    async def test_without_an_agent_cluster_params_are_used(self, make_client, env):
        env.rag_settings = {}

        await _kb(_client(make_client), chunking_strategy="fixed")

        (kw,) = env.rag.of("kb_upload")
        assert (kw["chunk_size"], kw["chunking_strategy"]) == (111, "fixed")
        assert "embedding_model" not in kw and kw["agent_id"] is None and kw["owner_user_id"] == "alice"

    @pytest.mark.parametrize("strategy", ["universal", " Markdown "])
    async def test_known_strategies_are_accepted(self, make_client, env, strategy):
        env.rag_settings = {}

        await _kb(_client(make_client), chunking_strategy=strategy)

        assert env.rag.of("kb_upload")[0]["chunking_strategy"] == strategy.strip().lower()

    async def test_unknown_strategy_is_ignored(self, make_client, env):
        env.rag_settings = {}

        await _kb(_client(make_client), chunking_strategy="weird")

        assert env.rag.of("kb_upload")[0]["chunking_strategy"] == "hierarchical"

    async def test_no_strategy_without_an_agent_uses_the_library_params(self, make_client, env):
        env.rag_settings = {}

        await _kb(_client(make_client))

        (kw,) = env.rag.of("kb_upload")
        assert (kw["chunk_size"], kw["chunking_strategy"]) == (999, "universal")

    async def test_no_strategy_with_an_agent_keeps_the_agent_params(self, make_client, env):
        await _kb(_client(make_client), agent_id=5)

        assert env.rag.of("kb_upload")[0]["chunking_strategy"] == "fixed"

    async def test_form_strategy_overrides_the_agent_setting(self, make_client, env):
        await _kb(_client(make_client), agent_id=5, chunking_strategy="semantic")

        assert env.rag.of("kb_upload")[0]["chunking_strategy"] == "semantic"


# ---------- KB: хранилище и сбои индексации ----------


class TestKbStorageAndFailures:
    async def test_source_is_kept_in_the_pvc(self, make_client, env):
        env.pvc = True

        await _kb(_client(make_client), agent_id=5)

        (fn, kw) = env.pvc_saved[0]
        assert fn == "a.pdf" and kw["scope"] == "agent" and kw["username"] == "alice" and kw["prefix"] == "kb_"
        upload = env.rag.of("kb_upload")[0]
        assert upload["minio_object"] == "pvc/obj.pdf" and upload["minio_bucket"] == module.RAG_PVC_BUCKET_MARKER

    async def test_pvc_failure_is_a_500(self, make_client, env):
        env.pvc = True
        env.pvc_result = None

        response = await _kb(_client(make_client), agent_id=5)

        assert response.status_code == 500 and "PVC" in response.json()["detail"]
        assert env.rag.of("kb_upload") == []

    async def test_indexing_failure_removes_the_source(self, make_client, env):
        env.pvc = True
        env.rag.upload_error = RuntimeError("svc-rag crashed")

        response = await _kb(_client(make_client), agent_id=5)

        assert response.status_code == 500 and response.json()["detail"] == "Внутренняя ошибка сервера"
        assert env.removed == [("pvc/obj.pdf", module.RAG_PVC_BUCKET_MARKER)] and env.bumped == []

    async def test_indexing_timeout_keeps_the_source_and_is_a_504(self, make_client, env):
        env.pvc = True
        env.rag.upload_error = httpx.ReadTimeout("slow")

        response = await _kb(_client(make_client), agent_id=5)

        assert response.status_code == 504 and "индексация продолжается" in response.json()["detail"]
        assert env.removed == []

    async def test_duplicate_found_by_the_indexer_is_a_409(self, make_client, env):
        env.pvc = True
        env.rag.upload_error = RagDuplicateDocument("уже есть", document_id=4, filename="x.pdf")

        response = await _kb(_client(make_client), agent_id=5)

        assert response.status_code == 409
        assert response.json()["detail"]["document_id"] == 4 and response.json()["detail"]["filename"] == "x.pdf"
        assert env.removed

    async def test_duplicate_without_a_filename_uses_the_upload_name(self, make_client, env):
        env.rag.upload_error = RagDuplicateDocument("уже есть", document_id=4)

        response = await _kb(_client(make_client), agent_id=5)

        assert response.status_code == 409 and response.json()["detail"]["filename"] == "a.pdf"


# ---------- Memory: проверки ----------


class TestMemoryUploadGuards:
    async def test_requires_login(self, make_client, env):
        assert (await _memory(_client(make_client, None))).status_code in (401, 403)

    async def test_feature_flag_off(self, make_client, env, services):
        services(module)

        assert (await _memory(_client(make_client))).status_code == 503

    async def test_minio_flag_matters_only_without_the_pvc(self, make_client, env, services):
        services(module, "rag")
        assert (await _memory(_client(make_client))).status_code == 503
        env.pvc = True
        assert (await _memory(_client(make_client))).status_code == 200

    async def test_service_unavailable(self, make_client, env):
        env.mp.setattr(module, "rag_client", None)

        assert (await _memory(_client(make_client))).status_code == 503

    async def test_volume_must_be_available(self, make_client, env):
        from fastapi import HTTPException

        def unavailable():
            raise HTTPException(status_code=503, detail="PVC недоступен")

        env.mp.setattr(module, "_require_storage_for_delete", unavailable)

        response = await _memory(_client(make_client))

        assert response.status_code == 503 and env.rag.calls == []

    async def test_empty_file(self, make_client, env):
        assert (await _memory(_client(make_client), content=b"")).status_code == 400

    async def test_unreadable_upload_is_a_500(self, make_client, env, monkeypatch):
        from starlette.datastructures import UploadFile

        async def broken(self, size=-1):
            raise OSError("stream closed")

        monkeypatch.setattr(UploadFile, "read", broken)

        response = await _memory(_client(make_client))

        assert response.status_code == 500 and response.json()["detail"] == "Внутренняя ошибка сервера"

    async def test_duplicate_is_rejected_before_storing(self, make_client, env):
        env.rag.duplicate = {"duplicate": True, "document_id": 2, "filename": "orig.pdf"}
        env.pvc = True

        response = await _memory(_client(make_client))

        assert response.status_code == 409 and env.pvc_saved == [] and env.rag.of("memory_upload") == []


# ---------- Memory: хранилище и сбои ----------


class TestMemoryStorageAndFailures:
    async def test_pvc_source(self, make_client, env):
        env.pvc = True

        response = await _memory(_client(make_client))

        assert response.json() == {"document_id": 11, "chunks": 3} and env.bumped == [True]
        (fn, kw) = env.pvc_saved[0]
        assert kw["scope"] == "memory" and kw["username"] == "alice" and kw["prefix"] == "memrag*"
        upload = env.rag.of("memory_upload")[0]
        assert upload["minio_object"] == "pvc/obj.pdf" and upload["minio_bucket"] == module.RAG_PVC_BUCKET_MARKER
        assert (upload["chunk_size"], upload["chunking_strategy"]) == (999, "universal")

    async def test_pvc_failure_is_a_500(self, make_client, env):
        env.pvc = True
        env.pvc_result = None

        response = await _memory(_client(make_client))

        assert response.status_code == 500 and "PVC" in response.json()["detail"]

    async def test_minio_source(self, make_client, env):
        minio = MinioStub()
        env.mp.setattr(module, "minio_client", minio)

        await _memory(_client(make_client), name="doc.docx")

        assert minio.events == [("bucket", "mem-bucket"), ("upload", "memrag*x.docx", "mem-bucket", "application/pdf")]
        upload = env.rag.of("memory_upload")[0]
        assert upload["minio_object"] == "memrag*x.docx" and upload["minio_bucket"] == "mem-bucket"

    async def test_file_without_extension_gets_bin(self, make_client, env):
        minio = MinioStub()
        env.mp.setattr(module, "minio_client", minio)

        await _memory(_client(make_client), name="README")

        assert minio.events[1][1] == "memrag*x.bin"

    @pytest.mark.parametrize("fail_on", ["bucket", "upload"])
    async def test_minio_failure_is_a_500(self, make_client, env, fail_on):
        env.mp.setattr(module, "minio_client", MinioStub(fail_on=fail_on))

        response = await _memory(_client(make_client))

        assert response.status_code == 500 and response.json()["detail"] == "Ошибка загрузки файла в хранилище"
        assert env.rag.of("memory_upload") == []

    async def test_without_minio_the_file_is_indexed_without_a_source(self, make_client, env):
        await _memory(_client(make_client))

        upload = env.rag.of("memory_upload")[0]
        assert upload["minio_object"] is None and upload["minio_bucket"] is None

    async def test_indexing_failure_is_a_502_and_removes_the_source(self, make_client, env):
        env.pvc = True
        env.rag.upload_error = RuntimeError("svc-rag crashed")

        response = await _memory(_client(make_client))

        assert response.status_code == 502 and response.json()["detail"] == "Ошибка при обращении к внешнему сервису"
        assert env.removed == [("pvc/obj.pdf", module.RAG_PVC_BUCKET_MARKER)] and env.bumped == []

    async def test_indexing_timeout_keeps_the_source(self, make_client, env):
        env.pvc = True
        env.rag.upload_error = httpx.ReadTimeout("slow")

        response = await _memory(_client(make_client))

        assert response.status_code == 504 and env.removed == []

    async def test_duplicate_from_the_indexer_is_a_409_and_cleans_up(self, make_client, env):
        env.pvc = True
        env.rag.upload_error = RagDuplicateDocument("уже есть", document_id=8, filename="x.pdf")

        response = await _memory(_client(make_client))

        assert response.status_code == 409 and response.json()["detail"]["document_id"] == 8
        assert env.removed == [("pvc/obj.pdf", module.RAG_PVC_BUCKET_MARKER)]

    async def test_duplicate_without_a_filename_uses_the_upload_name(self, make_client, env):
        env.rag.upload_error = RagDuplicateDocument("уже есть")

        response = await _memory(_client(make_client), name="mine.pdf")

        assert response.status_code == 409 and response.json()["detail"]["filename"] == "mine.pdf"
