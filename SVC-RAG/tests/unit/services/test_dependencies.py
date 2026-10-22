"""app/dependencies: ленивые синглтоны, выбор провайдеров моделей, миграции размерности, автоперенарезка Библиотеки.

БД не поднимается: соединение, репозитории и функции схемы подменены двойниками.
"""

import asyncio

import pytest

from app import dependencies as deps
from app.clients.rag_models_client import RagModelsClient
from app.core.config import RagModelsProviderEntry, get_settings
from app.database import embedding_schema

pytestmark = pytest.mark.unit

STATE_NAMES = [
    "_pg", "_kb_doc_repo", "_kb_vector_repo", "_rag_client", "_kb_service", "_mem_doc_repo",
    "_mem_vector_repo", "_memory_rag_service", "_proj_doc_repo", "_proj_vector_repo",
    "_project_rag_service", "_graph_repo",
]


class _Tx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeConn:
    def __init__(self, row=None):
        self.row = row
        self.executed = []

    async def execute(self, sql, *args):
        self.executed.append((" ".join(sql.split()), args))

    async def fetchrow(self, sql, *args):
        return self.row

    def transaction(self):
        return _Tx()


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


class FakePg:
    def __init__(self, ok=True, row=None):
        self.ok = ok
        self.conn = FakeConn(row)
        self.connected = 0

    async def connect(self):
        self.connected += 1
        return self.ok

    async def acquire(self):
        return _Acquire(self.conn)


class FakeRepo:
    created = []

    def __init__(self, pg, embedding_dim=None):
        self.pg = pg
        self.embedding_dim = embedding_dim
        self.tables_created = 0

    async def create_tables(self):
        self.tables_created += 1


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    for name in STATE_NAMES:
        monkeypatch.setattr(deps, name, None)
    monkeypatch.setattr(deps, "_model_choice", {
        "embedding": {"provider": None, "model": None},
        "reranker": {"provider": None, "model": None},
    })
    monkeypatch.delenv("RAG_ALLOW_DESTRUCTIVE_MIGRATION", raising=False)
    monkeypatch.delenv("RAG_MEMORY_RECHUNK_ON_CHANGE", raising=False)


@pytest.fixture
def fake_pg(monkeypatch):
    pg = FakePg()
    monkeypatch.setattr(deps, "get_postgres_connection", lambda: pg)
    for cls in ("KbDocumentRepository", "KbVectorRepository", "MemoryRagDocumentRepository",
                "MemoryRagVectorRepository", "ProjectRagDocumentRepository",
                "ProjectRagVectorRepository", "GraphRepository"):
        monkeypatch.setattr(deps, cls, type(cls, (FakeRepo,), {}))
    return pg


@pytest.fixture
def providers(monkeypatch):
    section = get_settings().rag_models
    entries = [
        RagModelsProviderEntry(id="PHOENIX", base_url="http://phoenix.test", embedding_model="emb-1",
                               reranker_model="rr-1", api_key_env="PHX_KEY"),
        RagModelsProviderEntry(id="CORSUR", base_url="http://corsur.test"),
    ]
    monkeypatch.setattr(section, "providers", entries)
    monkeypatch.setattr(section, "provider", "native")
    return section


class TestGetDb:
    async def test_connects_once_and_prepares_all_tables(self, fake_pg):
        pg = await deps.get_db()
        again = await deps.get_db()
        assert pg is again is fake_pg and fake_pg.connected == 1
        repos = [deps._kb_doc_repo, deps._kb_vector_repo, deps._mem_doc_repo, deps._mem_vector_repo,
                 deps._proj_doc_repo, deps._proj_vector_repo, deps._graph_repo]
        assert all(r.tables_created == 1 for r in repos)
        dim = get_settings().postgresql.embedding_dim
        assert deps._kb_vector_repo.embedding_dim == dim == deps._proj_vector_repo.embedding_dim

    async def test_connection_failure_raises(self, fake_pg):
        fake_pg.ok = False
        with pytest.raises(RuntimeError, match="PostgreSQL"):
            await deps.get_db()

    @pytest.mark.xfail(strict=True, raises=AssertionError,
                       reason="BUG: после неудачного connect() _pg остаётся заполненным, и повторный get_db() "
                              "молча отдаёт неподключённое соединение вместо новой попытки/ошибки "
                              "(dependencies.py:get_db)")
    async def test_failed_connection_is_retried_not_cached(self, fake_pg):
        fake_pg.ok = False
        with pytest.raises(RuntimeError):
            await deps.get_db()
        try:
            await deps.get_db()
        except RuntimeError:
            raised = True
        else:
            raised = False
        assert raised, "повторный get_db() после сбоя подключения должен снова бросить RuntimeError"

    async def test_failed_connection_is_cached_known_issue(self, fake_pg):
        fake_pg.ok = False
        with pytest.raises(RuntimeError):
            await deps.get_db()
        # второй вызов «успешно» возвращает неподключённое соединение, репозитории не созданы
        assert await deps.get_db() is fake_pg
        assert deps._kb_doc_repo is None and fake_pg.connected == 1


class TestModelChoice:
    def test_native_default(self, providers):
        deps._init_model_choice_from_config()
        assert deps._model_choice["embedding"] == {"provider": "native", "model": None}
        assert deps._model_choice["reranker"] == {"provider": "native", "model": None}

    def test_configured_provider_models(self, providers):
        providers.provider = "PHOENIX"
        deps._init_model_choice_from_config()
        assert deps._model_choice["embedding"] == {"provider": "PHOENIX", "model": "emb-1"}
        assert deps._model_choice["reranker"] == {"provider": "PHOENIX", "model": "rr-1"}

    def test_provider_missing_in_list_has_no_models(self, providers):
        providers.provider = "GHOST"
        deps._init_model_choice_from_config()
        assert deps._model_choice["embedding"] == {"provider": "GHOST", "model": None}

    def test_existing_choice_is_not_overwritten(self, providers):
        deps._model_choice["embedding"] = {"provider": "CORSUR", "model": "x"}
        providers.provider = "PHOENIX"
        deps._init_model_choice_from_config()
        assert deps._model_choice["embedding"] == {"provider": "CORSUR", "model": "x"}
        assert deps._model_choice["reranker"]["provider"] == "PHOENIX"

    def test_get_model_choice_returns_copy(self, providers):
        choice = deps.get_model_choice()
        choice["embedding"]["provider"] = "changed"
        assert deps.get_model_choice()["embedding"]["provider"] == "native"


class TestSplitRagClient:
    async def test_delegates_each_part(self):
        calls = []

        class Part:
            def __init__(self, name):
                self.name = name

            async def embed(self, texts, model=None, kind=None):
                calls.append((self.name, "embed", texts, model, kind))
                return [[1.0]]

            async def embed_single(self, text, model=None, kind=None):
                calls.append((self.name, "embed_single", text, model, kind))
                return [2.0]

            async def rerank(self, query, passages, top_k=20, model=None):
                calls.append((self.name, "rerank", query, passages, top_k, model))
                return [(0, 1.0)]

            async def health(self):
                calls.append((self.name, "health"))
                return True

        split = deps.SplitRagClient(Part("emb"), Part("rr"))
        assert await split.embed(["a"], model="m", kind="query") == [[1.0]]
        assert await split.embed_single("a", model="m", kind="document") == [2.0]
        assert await split.rerank("q", ["p"], top_k=5, model="r") == [(0, 1.0)]
        assert await split.health() is True
        assert calls == [
            ("emb", "embed", ["a"], "m", "query"),
            ("emb", "embed_single", "a", "m", "document"),
            ("rr", "rerank", "q", ["p"], 5, "r"),
            ("emb", "health"),
        ]


class TestClients:
    def test_native_client(self, providers):
        deps._model_choice["embedding"] = {"provider": "native", "model": None}
        assert type(deps._client_for("embedding")) is RagModelsClient

    def test_unset_choice_means_native(self, providers):
        assert type(deps._client_for("reranker")) is RagModelsClient

    def test_unknown_provider_falls_back_to_native(self, providers):
        deps._model_choice["embedding"] = {"provider": "GHOST", "model": "m"}
        assert type(deps._client_for("embedding")) is RagModelsClient

    def test_external_provider_embedding_client(self, providers):
        deps._model_choice["embedding"] = {"provider": "PHOENIX", "model": "emb-9"}
        client = deps._client_for("embedding")
        assert type(client).__name__ == "OpenAICompatModelsClient"
        assert client.embedding_model == "emb-9" and client.reranker_model in (None, "")

    def test_external_provider_reranker_client(self, providers):
        deps._model_choice["reranker"] = {"provider": "phoenix", "model": "rr-9"}
        client = deps._client_for("reranker")
        assert type(client).__name__ == "OpenAICompatModelsClient"
        assert client.reranker_model == "rr-9" and client.embedding_model in (None, "")

    def test_provider_entry_lookup_is_delegated(self, providers):
        assert deps._provider_entry("phoenix").id == "PHOENIX"
        assert deps._provider_entry("native") is None

    def test_make_rag_client_both_native(self, providers):
        assert type(deps._make_rag_client()) is RagModelsClient

    def test_make_rag_client_split_when_any_external(self, providers):
        providers.provider = "PHOENIX"
        client = deps._make_rag_client()
        assert isinstance(client, deps.SplitRagClient)
        assert client.embed_client is not client.rerank_client

    def test_make_rag_client_split_for_mixed(self, providers):
        deps._model_choice["embedding"] = {"provider": "native", "model": None}
        deps._model_choice["reranker"] = {"provider": "PHOENIX", "model": "rr-1"}
        client = deps._make_rag_client()
        assert isinstance(client, deps.SplitRagClient)
        assert type(client.embed_client) is RagModelsClient


class TestServices:
    @pytest.mark.parametrize("getter, attr, cls_name", [
        ("get_kb_service", "_kb_service", "KbService"),
        ("get_memory_rag_service", "_memory_rag_service", "MemoryRagService"),
        ("get_project_rag_service", "_project_rag_service", "ProjectRagService"),
    ])
    async def test_lazy_singletons_share_db_and_client(self, fake_pg, providers, getter, attr, cls_name):
        svc = await getattr(deps, getter)()
        assert type(svc).__name__ == cls_name
        assert await getattr(deps, getter)() is svc is getattr(deps, attr)
        assert svc.rag_client is deps.get_current_rag_client()
        assert svc.graph_repo is deps._graph_repo and fake_pg.connected == 1

    async def test_services_share_one_rag_client(self, fake_pg, providers):
        kb = await deps.get_kb_service()
        mem = await deps.get_memory_rag_service()
        proj = await deps.get_project_rag_service()
        assert kb.rag_client is mem.rag_client is proj.rag_client

    async def test_client_created_by_first_service_is_reused_by_next(self, fake_pg, providers):
        proj = await deps.get_project_rag_service()
        mem = await deps.get_memory_rag_service()
        kb = await deps.get_kb_service()
        assert proj.rag_client is mem.rag_client is kb.rag_client

    def test_current_client_is_none_before_first_use(self):
        assert deps.get_current_rag_client() is None


class TestEnsureEmbeddingDim:
    @pytest.fixture
    def schema(self, monkeypatch):
        calls = []

        async def fake_ensure(conn, base, dim, parent):
            calls.append((base, dim, parent))
            return f"{base}_{dim}"

        async def fake_migrate(conn, dim):
            calls.append(("migrate", dim))
            return {"embedding_dim": dim, "migrated": True, "cleared_rows": 7}

        monkeypatch.setattr(embedding_schema, "ensure_vector_table", fake_ensure)
        monkeypatch.setattr(embedding_schema, "migrate_vector_tables", fake_migrate)
        monkeypatch.setattr(get_settings().postgresql, "embedding_dim", get_settings().postgresql.embedding_dim)
        return calls

    async def test_additive_by_default(self, fake_pg, schema):
        await deps.get_db()
        result = await deps.ensure_embedding_dim("16")
        assert schema == [("kb_vectors", 16, "kb_documents"),
                          ("project_rag_vectors", 16, "project_rag_documents"),
                          ("memory_rag_vectors", 16, "memory_rag_documents")]
        assert result == {"embedding_dim": 16, "migrated": False, "cleared_rows": 0,
                          "tables": {"kb_vectors": "kb_vectors_16", "project_rag_vectors": "project_rag_vectors_16",
                                     "memory_rag_vectors": "memory_rag_vectors_16"}}
        assert get_settings().postgresql.embedding_dim == 16
        assert deps._kb_vector_repo.embedding_dim == 16 == deps._mem_vector_repo.embedding_dim

    async def test_destructive_migration_only_on_explicit_env(self, fake_pg, schema, monkeypatch):
        monkeypatch.setenv("RAG_ALLOW_DESTRUCTIVE_MIGRATION", "TRUE")
        await deps.get_db()
        result = await deps.ensure_embedding_dim(32)
        assert schema == [("migrate", 32)]
        assert result["migrated"] is True and result["cleared_rows"] == 7

    async def test_skips_repos_that_do_not_exist(self, fake_pg, schema):
        await deps.get_db()
        deps._mem_vector_repo = None
        await deps.ensure_embedding_dim(8)
        assert deps._kb_vector_repo.embedding_dim == 8

    @pytest.fixture(autouse=True)
    def _restore_dim(self):
        dim = get_settings().postgresql.embedding_dim
        yield
        get_settings().postgresql.embedding_dim = dim


class TestMemoryChunkConsistency:
    @pytest.fixture
    def reindex(self, monkeypatch):
        from app.api.endpoints import memory_rag

        calls = []

        async def fake_bg(svc, size, overlap, strategy, model, provider):
            calls.append((svc, size, overlap, strategy, model, provider))

        monkeypatch.setattr(memory_rag, "_memory_reindex_bg", fake_bg)
        return calls

    async def test_disabled_by_env(self, fake_pg, monkeypatch):
        monkeypatch.setenv("RAG_MEMORY_RECHUNK_ON_CHANGE", "off")
        await deps.ensure_memory_chunk_consistency()
        assert fake_pg.connected == 0

    async def test_first_start_records_settings(self, fake_pg, reindex):
        await deps.ensure_memory_chunk_consistency()
        sql = [s for s, _ in fake_pg.conn.executed]
        assert any("CREATE TABLE IF NOT EXISTS memory_chunk_state" in s for s in sql)
        assert any("ADD COLUMN IF NOT EXISTS embedding_key" in s for s in sql)
        insert = next(a for s, a in fake_pg.conn.executed if s.startswith("INSERT INTO memory_chunk_state"))
        assert insert[3] == ""  # кластерная модель
        await asyncio.sleep(0)
        assert reindex == []

    async def test_unchanged_settings_do_nothing(self, fake_pg, reindex):
        from app.services.chunker import normalize_chunking_strategy, resolve_chunk_params

        cs, co = resolve_chunk_params(None, None)
        fake_pg.conn.row = {"strategy": normalize_chunking_strategy(None), "chunk_size": cs,
                            "chunk_overlap": co, "embedding_key": None}
        await deps.ensure_memory_chunk_consistency()
        await asyncio.sleep(0)
        assert not any(s.startswith(("INSERT", "UPDATE")) for s, _ in fake_pg.conn.executed)
        assert reindex == []

    async def test_changed_chunking_starts_background_reindex(self, fake_pg, reindex, monkeypatch):
        monkeypatch.setenv("RAG_MEMORY_CHUNK_SIZE", "777")
        fake_pg.conn.row = {"strategy": "universal", "chunk_size": 500, "chunk_overlap": 50, "embedding_key": ""}
        await deps.ensure_memory_chunk_consistency()
        await asyncio.sleep(0)
        update = next(a for s, a in fake_pg.conn.executed if s.startswith("UPDATE memory_chunk_state"))
        assert update[1] == 777
        assert len(reindex) == 1
        svc, size, overlap, strategy, model, provider = reindex[0]
        assert type(svc).__name__ == "MemoryRagService" and (size, overlap, strategy) == (None, None, None)
        assert (model, provider) == (None, None)

    async def test_changed_embedding_model_is_passed_to_reindex(self, fake_pg, reindex, monkeypatch):
        from app.services.chunker import normalize_chunking_strategy, resolve_chunk_params

        cs, co = resolve_chunk_params(None, None)
        monkeypatch.setenv("RAG_MEMORY_EMBEDDING_MODEL", "phoenix/emb-2")
        fake_pg.conn.row = {"strategy": normalize_chunking_strategy(None), "chunk_size": cs,
                            "chunk_overlap": co, "embedding_key": ""}
        await deps.ensure_memory_chunk_consistency()
        await asyncio.sleep(0)
        update = next(a for s, a in fake_pg.conn.executed if s.startswith("UPDATE memory_chunk_state"))
        assert update[3] == "PHOENIX/emb-2"
        assert reindex[0][4:] == ("emb-2", "PHOENIX")


class TestSetProvider:
    @pytest.fixture
    def fake_client(self, monkeypatch):
        class Embed:
            def __init__(self, dim=None, error=None):
                self.dim, self.error = dim, error

            async def probe_dim(self):
                if self.error:
                    raise self.error
                return self.dim

        class Client:
            def __init__(self, embed):
                self.embed_client = embed

        holder = {"client": Client(Embed(1024))}
        monkeypatch.setattr(deps, "_make_rag_client", lambda: holder["client"])
        holder["Client"], holder["Embed"] = Client, Embed
        return holder

    async def test_rejects_unknown_type(self, providers):
        with pytest.raises(ValueError, match="embedding или reranker"):
            await deps.set_rag_models_provider("llm", "native")

    async def test_rejects_unknown_provider(self, providers):
        with pytest.raises(ValueError, match="Неизвестный провайдер 'ghost'"):
            await deps.set_rag_models_provider("embedding", "ghost")

    async def test_switch_to_native_replaces_client_in_existing_services(self, fake_pg, providers):
        kb = await deps.get_kb_service()
        old = kb.rag_client
        result = await deps.set_rag_models_provider(" Reranker ", "NATIVE", "  ")
        assert result == {"model_type": "reranker", "provider": "native", "model": None, "services_updated": 1}
        assert deps.get_current_rag_client() is not old and kb.rag_client is deps.get_current_rag_client()
        assert deps.get_model_choice()["reranker"] == {"provider": "native", "model": None}

    async def test_switch_embedding_probes_dimension(self, providers, fake_client):
        result = await deps.set_rag_models_provider("embedding", "phoenix", " emb-7 ")
        assert result["provider"] == "PHOENIX" and result["model"] == "emb-7"
        assert result["embedding_dim"] == 1024 and result["services_updated"] == 0

    async def test_probe_failure_is_reported_not_raised(self, providers, fake_client):
        fake_client["client"] = fake_client["Client"](fake_client["Embed"](error=RuntimeError("no route")))
        result = await deps.set_rag_models_provider("embedding", "PHOENIX", "emb-7")
        assert result["embedding_dim"] is None and result["probe_error"] == "no route"

    async def test_client_without_probe_is_fine(self, providers, fake_client):
        fake_client["client"] = object()
        result = await deps.set_rag_models_provider("embedding", "CORSUR")
        assert "embedding_dim" not in result

    async def test_reranker_switch_does_not_probe(self, providers, fake_client):
        result = await deps.set_rag_models_provider("reranker", "PHOENIX", "rr-2")
        assert "embedding_dim" not in result and result["model"] == "rr-2"
