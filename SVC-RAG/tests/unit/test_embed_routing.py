"""services/embed_routing: выбор клиента и модели эмбеддинга, размерность по пробе, кэши процесса, репозитории по dim, реранк-клиенты."""

import asyncio
from types import SimpleNamespace

import pytest

from app.services import embed_routing as er

pytestmark = pytest.mark.unit


def entry(pid="PHOENIX", **kw):
    base = dict(
        id=pid, base_url=f"http://{pid.lower()}.test", api_key_env="KEY", embed_path="/v1/embeddings", rerank_path="/v1/rerank", timeout=30.0,
        embedding_model="", reranker_model="", chat_only=False, embed_payload_style="openai", rerank_payload_style="openai", extra_headers={"x-bf": "1"},
    )
    base.update(kw)
    return SimpleNamespace(**base)


class FakeModelsClient:
    """Замена OpenAICompatModelsClient: без сети, каталог и размерности задаются снаружи."""

    created = []
    catalogs = {}
    dims = {}
    embed_errors = {}
    embed_calls = []
    catalog_error = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        FakeModelsClient.created.append(kwargs)

    async def list_models(self):
        if FakeModelsClient.catalog_error:
            raise FakeModelsClient.catalog_error
        return list(FakeModelsClient.catalogs.get(self.kwargs["provider_id"], []))

    async def embed(self, texts, model=None, kind=None):
        FakeModelsClient.embed_calls.append((self.kwargs["provider_id"], model, kind))
        if model in FakeModelsClient.embed_errors:
            raise FakeModelsClient.embed_errors[model]
        dim = FakeModelsClient.dims.get(model, 0)
        return [[0.1] * dim for _ in texts] if dim else [[]]


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    for cache in (er._clients, er._dims, er._dim_locks, er._repos, er._auto_models, er._auto_locks, er._catalogs, er._rerank_clients):
        cache.clear()
    FakeModelsClient.created, FakeModelsClient.catalogs, FakeModelsClient.dims = [], {}, {}
    FakeModelsClient.embed_errors, FakeModelsClient.embed_calls, FakeModelsClient.catalog_error = {}, [], None
    monkeypatch.delenv("RAG_EMBED_AUTOPICK", raising=False)

    state = SimpleNamespace(providers=[entry("PHOENIX"), entry("CORSUR", chat_only=True), entry("GATE", embedding_model="embed/FRIDA", reranker_model="rr-1")], dim=1024)
    settings = SimpleNamespace(
        rag_models=SimpleNamespace(providers=state.providers),
        rag_models_client=SimpleNamespace(embed_batch_size=16, embed_concurrency=4),
        postgresql=SimpleNamespace(embedding_dim=state.dim),
    )
    import app.clients.openai_models_compat_client as compat
    import app.core.config as config

    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr(compat, "OpenAICompatModelsClient", FakeModelsClient)
    yield state
    for cache in (er._clients, er._dims, er._dim_locks, er._repos, er._auto_models, er._auto_locks, er._catalogs, er._rerank_clients):
        cache.clear()


class DefaultClient:
    """Кластерный клиент сервиса; embed_client — нативный эмбеддер."""

    def __init__(self, dim=1024, with_native=True):
        self.dim = dim
        self.embed_client = SimpleNamespace(embed=self._embed, tag="native") if with_native else None
        self.calls = []

    async def _embed(self, texts, model=None, kind=None):
        self.calls.append((model, kind))
        return [[0.0] * self.dim for _ in texts]

    async def embed(self, texts, model=None, kind=None):
        return await self._embed(texts, model, kind)


class TestNames:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [("embed/FRIDA", True), ("BAAI/bge-m3", True), ("intfloat/e5-large", True), ("gte-base", True), ("LaBSE", True), ("all-MiniLM-L6", True),
         ("bge-reranker-v2", False), ("ms-marco-MiniLM", False), ("cross-encoder/x", False), ("gpt-4o", False), ("", False), (None, False)],
    )
    def test_embedding_detection(self, name, expected):
        assert er._looks_like_embedding(name) is expected

    @pytest.mark.parametrize(("raw", "expected"), [(None, True), ("1", True), ("", True), ("0", False), ("false", False), (" NO ", False), ("yes", True)])
    def test_autopick_flag(self, monkeypatch, raw, expected):
        monkeypatch.delenv("RAG_EMBED_AUTOPICK", raising=False)
        if raw is not None:
            monkeypatch.setenv("RAG_EMBED_AUTOPICK", raw)

        assert er._autopick_enabled() is expected

    def test_normalization(self):
        assert er._norm("  PHOENIX ", "  m ") == ("PHOENIX", "m") and er._norm(None, "  ") == ("", None) and er._key("", None) == ("", "")

    def test_provider_lookup_is_case_insensitive(self):
        assert er._provider_entry("phoenix").id == "PHOENIX" and er._canonical_provider(" corsur ") == "CORSUR"
        assert er._provider_entry("native") is None and er._provider_entry("") is None and er._provider_entry("ghost") is None
        assert er._canonical_provider("ghost") == "ghost"


class TestProfileAndDims:
    def test_note_dim(self):
        er.note_dim("PHOENIX", "m", 768)
        er.note_dim(None, None, 1024)
        er.note_dim("x", "y", 0)
        er.note_dim("x", "y", None)

        assert er._dims == {("PHOENIX", "m"): 768, ("", ""): 1024}

    def test_label(self):
        assert er.EmbedProfile("", None, 1024, None).label == "cluster/default(dim=1024)"
        assert er.EmbedProfile("PHOENIX", "frida", 1536, None).label == "PHOENIX/frida(dim=1536)"

    async def test_embed_passes_model_and_kind_and_remembers_the_dim(self):
        client = FakeModelsClient(provider_id="p")
        FakeModelsClient.dims = {"m": 8}
        profile = er.EmbedProfile("p", "m", 8, client)

        vectors = await profile.embed(["a", "b"], kind="document")

        assert len(vectors) == 2 and FakeModelsClient.embed_calls[-1] == ("p", "m", "document") and er._dims[("p", "m")] == 8

    async def test_dimension_drift_fixes_the_cache_and_fails_loudly(self):
        client = FakeModelsClient(provider_id="p")
        FakeModelsClient.dims = {"m": 8}
        profile = er.EmbedProfile("p", "m", 4, client)  # профиль считал, что dim=4

        with pytest.raises(RuntimeError, match="dim=8"):
            await profile.embed(["a"])

        assert er._dims[("p", "m")] == 8  # повтор уйдёт в правильную таблицу

    async def test_unknown_expected_dim_is_not_checked(self):
        FakeModelsClient.dims = {"m": 8}

        await er.EmbedProfile("p", "m", 0, FakeModelsClient(provider_id="p")).embed(["a"])

    async def test_empty_vectors_do_not_touch_the_cache(self):
        FakeModelsClient.dims = {}

        assert await er.EmbedProfile("p", "m", 4, FakeModelsClient(provider_id="p")).embed(["a"]) == [[]] and er._dims == {}


class TestResolveDim:
    async def test_probe_once_then_cache(self):
        client = FakeModelsClient(provider_id="PHOENIX")
        FakeModelsClient.dims = {"m": 8}

        assert await er.resolve_dim(client, "PHOENIX", "m") == 8
        assert await er.resolve_dim(client, "PHOENIX", "m") == 8
        assert len(FakeModelsClient.embed_calls) == 1 and FakeModelsClient.embed_calls[0][2] == "query"

    async def test_parallel_probes_share_one_call(self):
        client = FakeModelsClient(provider_id="PHOENIX")
        FakeModelsClient.dims = {"m": 8}

        results = await asyncio.gather(*[er.resolve_dim(client, "PHOENIX", "m") for _ in range(5)])

        assert results == [8] * 5 and len(FakeModelsClient.embed_calls) == 1

    async def test_empty_probe_is_an_error_and_is_not_cached(self):
        client = FakeModelsClient(provider_id="PHOENIX")

        with pytest.raises(RuntimeError, match="пустой вектор"):
            await er.resolve_dim(client, "PHOENIX", "m")

        assert er._dims == {}

    async def test_empty_provider_is_stored_as_native(self):
        FakeModelsClient.dims = {None: 6}

        assert await er.resolve_dim(FakeModelsClient(provider_id="x"), "", None) == 6 and ("native", "") in er._dims


class TestClients:
    def test_native_prefers_the_default_clients_embedder(self):
        default = DefaultClient()

        assert er._client_for("", None, default) is default.embed_client
        assert er._client_for("NATIVE", "m", default) is default.embed_client

    def test_native_falls_back_to_the_models_client(self, monkeypatch):
        import app.clients.rag_models_client as rmc

        created = []

        class FakeRag:
            def __init__(self):
                created.append(self)

        monkeypatch.setattr(rmc, "RagModelsClient", FakeRag)
        default = DefaultClient(with_native=False)

        first = er._client_for("native", None, default)

        assert er._client_for("native", None, default) is first and len(created) == 1

    def test_unknown_provider_lists_the_known_ones(self):
        with pytest.raises(ValueError) as exc:
            er._client_for("GHOST", "m", DefaultClient())

        assert "GHOST" in str(exc.value) and "PHOENIX" in str(exc.value) and "native" in str(exc.value)

    def test_gateway_client_gets_the_provider_settings(self):
        client = er._client_for("PHOENIX", "frida", DefaultClient())

        kwargs = FakeModelsClient.created[-1]
        assert client.kwargs is kwargs and kwargs["provider_id"] == "PHOENIX" and kwargs["embedding_model"] == "frida"
        assert kwargs["embed_batch_size"] == 16 and kwargs["embed_concurrency"] == 4 and kwargs["extra_headers"] == {"x-bf": "1"} and kwargs["base_url"] == "http://phoenix.test"

    def test_clients_are_cached_per_provider_and_model(self):
        a = er._client_for("PHOENIX", "m1", DefaultClient())
        b = er._client_for("PHOENIX", "m1", DefaultClient())
        c = er._client_for("PHOENIX", "m2", DefaultClient())

        assert a is b and a is not c and len(FakeModelsClient.created) == 2

    def test_chat_only_gateways_use_the_cluster_embedder(self):
        default = DefaultClient()

        assert er._client_for("CORSUR", "m", default) is default.embed_client and FakeModelsClient.created == []

    def test_model_falls_back_to_the_providers_default(self):
        er._client_for("GATE", None, DefaultClient())

        assert FakeModelsClient.created[-1]["embedding_model"] == "embed/FRIDA"


class TestCatalog:
    async def test_catalog_is_requested_once(self):
        FakeModelsClient.catalogs = {"PHOENIX": ["embed/FRIDA", "gpt-4o"]}
        e = er._provider_entry("PHOENIX")

        assert await er._provider_catalog(e) == ["embed/FRIDA", "gpt-4o"] and await er._provider_catalog(e) == ["embed/FRIDA", "gpt-4o"]
        assert len(FakeModelsClient.created) == 1

    async def test_model_names_are_snapped_to_the_catalog_spelling(self):
        FakeModelsClient.catalogs = {"PHOENIX": ["embed/FRIDA"]}

        assert await er._canonical_model("PHOENIX", "embed/frida") == "embed/FRIDA"
        assert await er._canonical_model("PHOENIX", "embed/FRIDA") == "embed/FRIDA"
        assert await er._canonical_model("PHOENIX", "unknown") == "unknown"

    async def test_unavailable_catalog_keeps_the_name(self):
        FakeModelsClient.catalog_error = RuntimeError("down")

        assert await er._canonical_model("PHOENIX", "embed/frida") == "embed/frida"

    async def test_empty_catalog_or_unknown_provider(self):
        assert await er._canonical_model("PHOENIX", "m") == "m" and await er._canonical_model("GHOST", "m") == "m" and await er._canonical_model("PHOENIX", "") == ""


class TestDefaultModel:
    async def test_configured_model_wins(self):
        assert await er._default_model_for("GATE") == "embed/FRIDA" and FakeModelsClient.created == []

    async def test_unknown_provider(self):
        assert await er._default_model_for("GHOST") is None

    async def test_chat_only_or_disabled_autopick_has_no_default(self, monkeypatch):
        assert await er._default_model_for("CORSUR") is None
        monkeypatch.setenv("RAG_EMBED_AUTOPICK", "0")
        assert await er._default_model_for("PHOENIX") is None

    async def test_autopick_takes_the_first_working_embedding_model(self):
        FakeModelsClient.catalogs = {"PHOENIX": ["gpt-4o", "bge-reranker", "Qwen/Qwen3-Embedding-0.6B", "BAAI/bge-m3", "embed/FRIDA"]}
        FakeModelsClient.embed_errors = {"Qwen/Qwen3-Embedding-0.6B": RuntimeError("500")}
        FakeModelsClient.dims = {"BAAI/bge-m3": 1024, "embed/FRIDA": 1536}

        assert await er._default_model_for("PHOENIX") == "BAAI/bge-m3"
        assert er._auto_models == {"PHOENIX": "BAAI/bge-m3"} and er._dims[("PHOENIX", "BAAI/bge-m3")] == 1024

    async def test_the_choice_is_cached_for_the_process(self):
        FakeModelsClient.catalogs = {"PHOENIX": ["BAAI/bge-m3"]}
        FakeModelsClient.dims = {"BAAI/bge-m3": 1024}

        await er._default_model_for("PHOENIX")
        FakeModelsClient.embed_calls.clear()
        await er._default_model_for("PHOENIX")

        assert FakeModelsClient.embed_calls == []

    async def test_parallel_requests_pick_once(self):
        FakeModelsClient.catalogs = {"PHOENIX": ["BAAI/bge-m3"]}
        FakeModelsClient.dims = {"BAAI/bge-m3": 1024}

        await asyncio.gather(*[er._default_model_for("PHOENIX") for _ in range(4)])

        assert len(FakeModelsClient.embed_calls) == 1

    async def test_nothing_suitable(self):
        FakeModelsClient.catalogs = {"PHOENIX": ["gpt-4o"]}
        assert await er._default_model_for("PHOENIX") is None

        FakeModelsClient.catalogs = {"PHOENIX": ["embed/x", "bge-y"]}
        FakeModelsClient.embed_errors = {"embed/x": RuntimeError("500")}  # у bge-y пустой вектор
        er._catalogs.clear()
        assert await er._default_model_for("PHOENIX") is None and er._auto_models == {}

    async def test_catalog_failure_does_not_raise(self):
        FakeModelsClient.catalog_error = RuntimeError("down")

        assert await er._default_model_for("PHOENIX") is None


class TestResolveProfile:
    async def test_cluster_path_uses_the_configured_dimension_and_never_probes(self):
        default = DefaultClient()

        profile = await er.resolve_profile(default)

        assert (profile.provider, profile.model, profile.dim, profile.client) == ("", None, 1024, default) and FakeModelsClient.embed_calls == []

    async def test_native_with_a_model(self):
        default = DefaultClient(dim=768)

        profile = await er.resolve_profile(default, "native", "local-model")

        assert profile.provider == "native" and profile.model == "local-model" and profile.dim == 768 and profile.client is default.embed_client

    async def test_gateway_model_is_canonicalised_and_probed(self):
        FakeModelsClient.catalogs = {"PHOENIX": ["embed/FRIDA"]}
        FakeModelsClient.dims = {"embed/FRIDA": 1536}

        profile = await er.resolve_profile(DefaultClient(), "phoenix", "embed/frida")

        assert (profile.provider, profile.model, profile.dim) == ("PHOENIX", "embed/FRIDA", 1536)

    async def test_provider_without_a_model_gets_the_default_one(self):
        FakeModelsClient.dims = {"embed/FRIDA": 1536}

        profile = await er.resolve_profile(DefaultClient(), "GATE", None)

        assert (profile.provider, profile.model, profile.dim) == ("GATE", "embed/FRIDA", 1536)

    async def test_model_only_request_goes_to_the_cluster_client(self):
        default = DefaultClient(dim=512)

        profile = await er.resolve_profile(default, None, "some-model")

        assert profile.provider == "native" and profile.model == "some-model" and profile.dim == 512

    async def test_unknown_provider_fails_with_the_list_of_known_ones(self):
        with pytest.raises(ValueError, match="PHOENIX"):
            await er.resolve_profile(DefaultClient(), "GHOST", "m")


class FakeRepo:
    created = []

    def __init__(self, db, embedding_dim=1024):
        self.db, self.embedding_dim, self.tables_created = db, embedding_dim, 0
        FakeRepo.created.append(self)

    async def create_tables(self):
        await asyncio.sleep(0)
        self.tables_created += 1


class KbRepo(FakeRepo):
    pass


class ProjectRepo(FakeRepo):
    pass


class MemoryVectorStore(FakeRepo):
    pass


class TestRepositories:
    @pytest.fixture(autouse=True)
    def _reset(self):
        FakeRepo.created = []

    async def test_root_dimension_returns_the_root_repo(self):
        root = KbRepo("db", embedding_dim=1024)

        assert await er.vector_repo_for(root, 1024) is root and await er.vector_repo_for(root, 0) is root and await er.vector_repo_for(root, None) is root

    async def test_new_dimension_creates_tables_once_and_is_cached(self):
        root = KbRepo("db", embedding_dim=1024)

        a = await er.vector_repo_for(root, 768)
        b = await er.vector_repo_for(root, 768)

        assert a is b and a is not root and a.embedding_dim == 768 and a.db == "db" and a.tables_created == 1

    async def test_parallel_requests_create_one_repo(self):
        root = KbRepo("db", embedding_dim=1024)

        repos = await asyncio.gather(*[er.vector_repo_for(root, 768) for _ in range(5)])

        assert len({id(r) for r in repos}) == 1 and repos[0].tables_created == 1

    async def test_repos_are_separated_by_class(self):
        kb = await er.vector_repo_for(KbRepo("db", 1024), 768)
        project = await er.vector_repo_for(ProjectRepo("db", 1024), 768)

        assert kb is not project and type(kb) is KbRepo and type(project) is ProjectRepo

    async def test_resolve_for_returns_profile_and_repo(self):
        default = DefaultClient(dim=768)
        root = KbRepo("db", embedding_dim=1024)

        profile, repo = await er.resolve_for(default, root, "native", "m")

        assert profile.dim == 768 and repo.embedding_dim == 768 and repo is not root

    def test_store_labels(self):
        assert [er._store_label(r) for r in (KbRepo("d"), ProjectRepo("d"), MemoryVectorStore("d"))] == ["kb", "project", "memory"]
        assert er._store_label(FakeRepo("d")) == "fakerepo"


class TestRerankClients:
    def test_cluster_client_by_default(self):
        default = object()

        assert er.rerank_client_for(default) is default and er.rerank_client_for(default, None, "m") is default

    def test_native_reranker(self, monkeypatch):
        import app.clients.rag_models_client as rmc

        created = []
        monkeypatch.setattr(rmc, "RagModelsClient", lambda: created.append(1) or SimpleNamespace(tag="rag-models"))

        a = er.rerank_client_for(object(), "native")
        b = er.rerank_client_for(object(), "NATIVE")

        assert a is b and len(created) == 1

    def test_gateway_reranker_settings_and_cache(self):
        a = er.rerank_client_for(object(), "gate", "rr-2")
        b = er.rerank_client_for(object(), "GATE", "rr-2")

        kwargs = FakeModelsClient.created[-1]
        assert a is b and len(FakeModelsClient.created) == 1 and kwargs["reranker_model"] == "rr-2" and kwargs["provider_id"] == "GATE" and kwargs["extra_headers"] == {"x-bf": "1"}

    def test_default_reranker_model_comes_from_the_provider(self):
        er.rerank_client_for(object(), "GATE")

        assert FakeModelsClient.created[-1]["reranker_model"] == "rr-1"

    def test_embedding_and_rerank_clients_are_cached_separately(self):
        er._client_for("PHOENIX", "m", DefaultClient())
        er.rerank_client_for(object(), "PHOENIX", "m")

        assert len(FakeModelsClient.created) == 2

    def test_unknown_provider(self):
        with pytest.raises(ValueError, match="RERANK-ROUTE"):
            er.rerank_client_for(object(), "GHOST")


class TestOverview:
    async def test_diagnostics(self):
        er.note_dim("PHOENIX", "m", 768)
        er.note_dim(None, None, 1024)
        er._client_for("PHOENIX", "m", DefaultClient())
        er.rerank_client_for(object(), "PHOENIX", "r")
        er._auto_models["PHOENIX"] = "m"
        await er.vector_repo_for(KbRepo("db", 1024), 768)

        overview = er.routing_overview()

        assert {"provider": "PHOENIX", "model": "m", "dim": 768} in overview["models"] and {"provider": "cluster", "model": None, "dim": 1024} in overview["models"]
        assert "PHOENIX/m" in overview["clients"] and "PHOENIX/r" in overview["rerank_clients"] and overview["repos"] == [{"repo": "KbRepo", "dim": 768}]
        assert overview["auto_models"] == {"PHOENIX": "m"}

    def test_empty(self):
        assert er.routing_overview() == {"models": [], "clients": [], "rerank_clients": [], "repos": [], "auto_models": {}}
