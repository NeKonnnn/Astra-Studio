"""services/user_rag_settings: RAG-настройки агента/проекта поверх дефолтов кластера, привязка к запросу, пути моделей."""

import asyncio
from types import SimpleNamespace

import pytest

from backend.services import user_rag_settings as rs

pytestmark = pytest.mark.unit

CLUSTER = {
    "rag_strategy": "hybrid", "agentic_rag_enabled": False, "agentic_max_iterations": 3, "rag_query_fix_typos": True, "rag_multi_query_enabled": False,
    "rag_hyde_enabled": False, "rag_chat_top_k": 8, "rag_chunking_strategy": "fixed", "rag_chunk_size": 800, "rag_chunk_overlap": 100,
    "rag_similarity_threshold": 0.25, "rag_reranking_enabled": False, "rag_rerank_top_n": 10, "rag_system_prompt": "Кластерный промпт",
    "rag_embedding_model_path": "", "rag_reranker_model_path": "",
}


class FakeEntityRepo:
    def __init__(self, rows=None):
        self.rows = dict(rows or {})
        self.calls = []

    async def get(self, scope, entity_id):
        return dict(self.rows[(scope, entity_id)]) if (scope, entity_id) in self.rows else None

    async def upsert(self, scope, entity_id, payload, updated_by):
        self.calls.append(("upsert", scope, entity_id, dict(payload), updated_by))
        self.rows[(scope, entity_id)] = dict(payload)

    async def delete(self, scope, entity_id):
        self.calls.append(("delete", scope, entity_id))
        self.rows.pop((scope, entity_id), None)


class FakeUserRepo:
    def __init__(self, row=None):
        self.row = row
        self.upserts = []

    async def get(self, user_id):
        return self.row

    async def upsert(self, user_id, rag_settings=None):
        self.upserts.append((user_id, rag_settings))
        self.row = {"rag_settings": rag_settings}


@pytest.fixture(autouse=True)
def storage(monkeypatch):
    state = SimpleNamespace(entities=FakeEntityRepo(), users=FakeUserRepo())
    monkeypatch.setattr(rs, "_defaults_from_app_state", lambda: dict(CLUSTER))
    monkeypatch.setattr(rs, "_get_entity_repo", lambda: state.entities)
    monkeypatch.setattr(rs, "_get_user_repo", lambda: state.users)
    return state


class TestNormalisation:
    @pytest.mark.parametrize(("raw", "expected"), [("agent", "agent"), (" PROJECT ", "project"), ("memory", "project"), ("", "project"), (None, "project")])
    def test_scope(self, raw, expected):
        assert rs.normalize_scope(raw) == expected

    @pytest.mark.parametrize(("raw", "expected"), [(5, "5"), (" abc ", "abc"), ("", None), (None, None), ("   ", None), (0, "0")])
    def test_entity_id(self, raw, expected):
        assert rs.normalize_entity_id(raw) == expected

    def test_defaults_snapshot_is_a_copy(self):
        snapshot = rs.default_rag_settings_snapshot()
        snapshot["rag_strategy"] = "changed"

        assert rs._defaults_from_app_state()["rag_strategy"] == "hybrid"

    def test_values_equality(self):
        assert rs._values_equal(1, 1.0) and rs._values_equal(True, 1) and rs._values_equal(" x ", "x") and rs._values_equal(None, None)
        assert not rs._values_equal(None, "") and not rs._values_equal(1, 2) and not rs._values_equal("a", "b")


class TestLegacyDefaults:
    def test_frozen_legacy_defaults_are_dropped_so_the_entity_follows_the_cluster_again(self):
        stored = {"rag_strategy": "auto", "rag_chat_top_k": 12, "rag_similarity_threshold": 0.0, "rag_system_prompt": "", "rag_hyde_enabled": True}

        assert rs._strip_legacy_default_overrides(stored) == {"rag_hyde_enabled": True}

    def test_index_affecting_keys_are_never_dropped(self):
        stored = {"rag_chunking_strategy": "hierarchical", "rag_chunk_size": 1000, "rag_chunk_overlap": 200, "rag_embedding_model_path": ""}

        assert rs._strip_legacy_default_overrides(stored) == stored

    @pytest.mark.parametrize("stored", [None, "x", [], 5])
    def test_garbage_rows(self, stored):
        assert rs._strip_legacy_default_overrides(stored) == {}

    def test_merge_prefers_stored_values_but_not_none(self):
        merged = rs._merge_with_defaults({"rag_chat_top_k": 20, "rag_hyde_enabled": None, "unknown": 1})

        assert merged["rag_chat_top_k"] == 20 and merged["rag_hyde_enabled"] is False and "unknown" not in merged

    def test_merge_without_a_row_is_the_cluster_defaults(self):
        assert rs._merge_with_defaults(None) == CLUSTER


class TestEntityStorage:
    async def test_no_entity_means_cluster_defaults(self, storage):
        assert await rs.get_entity_rag_settings("agent", None) == CLUSTER
        assert await rs.get_entity_rag_settings("agent", "  ") == CLUSTER

    async def test_missing_repository(self, storage, monkeypatch):
        monkeypatch.setattr(rs, "_get_entity_repo", lambda: None)

        assert await rs.get_entity_rag_settings("agent", 1) == CLUSTER

    async def test_reads_a_row_of_the_right_scope(self, storage):
        storage.entities.rows[("agent", "7")] = {"rag_chat_top_k": 30}
        storage.entities.rows[("project", "7")] = {"rag_chat_top_k": 5}

        assert (await rs.get_entity_rag_settings("agent", 7))["rag_chat_top_k"] == 30
        assert (await rs.get_entity_rag_settings("project", "7"))["rag_chat_top_k"] == 5
        assert (await rs.get_entity_rag_settings("weird", "7"))["rag_chat_top_k"] == 5  # неизвестный scope = project

    async def test_a_row_holding_only_legacy_defaults_is_removed(self, storage):
        storage.entities.rows[("agent", "7")] = {"rag_strategy": "auto", "rag_chat_top_k": 12}

        settings = await rs.get_entity_rag_settings("agent", 7)

        assert settings == CLUSTER and ("agent", "7") not in storage.entities.rows and ("delete", "agent", "7") in storage.entities.calls

    async def test_a_failing_cleanup_does_not_break_reading(self, storage):
        storage.entities.rows[("agent", "7")] = {"rag_strategy": "auto"}

        async def boom(scope, entity_id):
            raise RuntimeError("db down")

        storage.entities.delete = boom

        assert await rs.get_entity_rag_settings("agent", 7) == CLUSTER

    async def test_only_differences_from_the_cluster_are_stored(self, storage):
        result = await rs.save_entity_rag_settings("agent", 7, {"rag_chat_top_k": 20, "rag_strategy": "hybrid"}, updated_by="alice")

        assert storage.entities.calls[-1] == ("upsert", "agent", "7", {"rag_chat_top_k": 20}, "alice")
        assert result["rag_chat_top_k"] == 20 and result["rag_strategy"] == "hybrid"

    async def test_index_affecting_values_are_stored_even_when_equal_to_the_default(self, storage):
        await rs.save_entity_rag_settings("agent", 7, {"rag_chunk_size": 800, "rag_chunking_strategy": "fixed"})

        assert storage.entities.calls[-1][3] == {"rag_chunk_size": 800, "rag_chunking_strategy": "fixed"}

    async def test_updates_are_merged_with_the_stored_row(self, storage):
        await rs.save_entity_rag_settings("agent", 7, {"rag_chat_top_k": 20})
        await rs.save_entity_rag_settings("agent", 7, {"rag_rerank_top_n": 5})

        assert storage.entities.rows[("agent", "7")] == {"rag_chat_top_k": 20, "rag_rerank_top_n": 5}

    async def test_returning_to_the_defaults_removes_the_row(self, storage):
        await rs.save_entity_rag_settings("agent", 7, {"rag_chat_top_k": 20})

        await rs.save_entity_rag_settings("agent", 7, {"rag_chat_top_k": 8})

        assert ("agent", "7") not in storage.entities.rows

    async def test_unknown_keys_and_none_values_are_ignored(self, storage):
        await rs.save_entity_rag_settings("agent", 7, {"rag_chat_top_k": 20, "is_admin": True, "rag_hyde_enabled": None})

        assert storage.entities.rows[("agent", "7")] == {"rag_chat_top_k": 20}

    async def test_the_entity_id_is_required(self, storage):
        with pytest.raises(ValueError):
            await rs.save_entity_rag_settings("agent", "  ", {})
        with pytest.raises(ValueError):
            await rs.reset_entity_rag_settings("agent", None)

    async def test_missing_repository_returns_the_merge_without_saving(self, storage, monkeypatch):
        monkeypatch.setattr(rs, "_get_entity_repo", lambda: None)

        assert (await rs.save_entity_rag_settings("agent", 7, {"rag_chat_top_k": 3}))["rag_chat_top_k"] == 3

    async def test_a_string_setting_cannot_be_cleared_with_none_known_issue(self, storage):
        # None считается «не менять», поэтому очистить значение (вернуть дефолт) можно только записав дефолт явно.
        await rs.save_entity_rag_settings("agent", 7, {"rag_system_prompt": "Мой"})

        await rs.save_entity_rag_settings("agent", 7, {"rag_system_prompt": None})

        assert storage.entities.rows[("agent", "7")] == {"rag_system_prompt": "Мой"}

    async def test_reset(self, storage):
        storage.entities.rows[("agent", "7")] = {"rag_chat_top_k": 20}

        assert await rs.reset_entity_rag_settings("agent", 7) == CLUSTER and ("agent", "7") not in storage.entities.rows

    async def test_reset_without_a_repository(self, storage, monkeypatch):
        monkeypatch.setattr(rs, "_get_entity_repo", lambda: None)

        assert await rs.reset_entity_rag_settings("agent", 7) == CLUSTER


class TestMemoryStrategy:
    async def test_defaults(self, storage, monkeypatch):
        assert await rs.get_user_memory_strategy(None) == "auto" and await rs.get_user_memory_strategy("  ") == "auto"
        assert await rs.get_user_memory_strategy("alice") == "hybrid"  # дефолт кластера
        monkeypatch.setattr(rs, "_get_user_repo", lambda: None)
        assert await rs.get_user_memory_strategy("alice") == "auto"

    async def test_save_and_read_back_lowercased(self, storage):
        assert await rs.save_user_memory_strategy("alice", " VECTOR ") == "vector"

        assert await rs.get_user_memory_strategy("alice") == "vector"
        assert storage.users.upserts[-1] == ("alice", {"rag_memory_strategy": "vector"})

    async def test_blank_strategy_becomes_auto_and_other_settings_survive(self, storage):
        storage.users.row = {"rag_settings": {"other": 1}}

        assert await rs.save_user_memory_strategy("alice", "  ") == "auto"
        assert storage.users.upserts[-1][1] == {"other": 1, "rag_memory_strategy": "auto"}

    async def test_errors(self, storage, monkeypatch):
        with pytest.raises(ValueError):
            await rs.save_user_memory_strategy("  ", "x")
        monkeypatch.setattr(rs, "_get_user_repo", lambda: None)
        with pytest.raises(RuntimeError):
            await rs.save_user_memory_strategy("alice", "x")


class TestRuntimeBinding:
    def test_without_a_binding_the_cluster_defaults_apply(self):
        assert rs.get_runtime_rag_settings("agent") == CLUSTER and rs.runtime_rag_top_k() == 8 and rs.runtime_rag_strategy() == "hybrid"

    def test_bound_snapshots_are_per_scope_and_released_on_reset(self):
        token = rs.bind_user_rag_runtime({"agent": {"rag_chat_top_k": 30, "rag_strategy": "vector"}, "project": {"rag_chat_top_k": 5}, "": {}}, memory_strategy=" LEXICAL ")
        try:
            assert rs.runtime_rag_top_k("agent") == 30 and rs.runtime_rag_top_k("project") == 5
            assert rs.runtime_rag_strategy("agent") == "vector" and rs.runtime_memory_strategy() == "lexical"
            assert rs.runtime_primary_scope() == "agent"
        finally:
            rs.reset_user_rag_runtime(token)

        assert rs.runtime_rag_top_k("agent") == 8 and rs.runtime_memory_strategy() == "hybrid" and rs.runtime_primary_scope() == "project"

    def test_the_snapshot_is_copied(self):
        source = {"rag_chat_top_k": 30}
        token = rs.bind_user_rag_runtime({"agent": source})
        source["rag_chat_top_k"] = 1
        try:
            assert rs.runtime_rag_top_k("agent") == 30
        finally:
            rs.reset_user_rag_runtime(token)

    async def test_bindings_do_not_leak_between_requests(self):
        async def request(top_k):
            token = rs.bind_user_rag_runtime({"agent": {"rag_chat_top_k": top_k}})
            try:
                await asyncio.sleep(0.01)
                return rs.runtime_rag_top_k("agent")
            finally:
                rs.reset_user_rag_runtime(token)

        assert await asyncio.gather(request(10), request(20), request(30)) == [10, 20, 30]

    @pytest.mark.parametrize(("raw", "expected"), [(0, 12), (-5, 1), (500, 64), ("x", 12), (None, 12), ("20", 20)])
    def test_top_k_is_clamped(self, raw, expected):
        token = rs.bind_user_rag_runtime({"agent": {"rag_chat_top_k": raw}})
        try:
            assert rs.runtime_rag_top_k("agent") == expected
        finally:
            rs.reset_user_rag_runtime(token)

    @pytest.mark.parametrize(("raw", "expected"), [(0, 2), (9, 5), (-3, 1), ("x", 2), (3, 3)])
    def test_agentic_iterations_are_clamped(self, raw, expected):
        token = rs.bind_user_rag_runtime({"agent": {"agentic_max_iterations": raw}})
        try:
            assert rs.runtime_agentic_max_iterations("agent") == expected
        finally:
            rs.reset_user_rag_runtime(token)

    def test_agentic_flag_follows_the_primary_scope(self):
        assert rs.runtime_agentic_rag_enabled() is False  # дефолт кластера
        token = rs.bind_user_rag_runtime({"agent": {"agentic_rag_enabled": True}, "project": {"agentic_rag_enabled": False}})
        try:
            assert rs.runtime_agentic_rag_enabled() is True and rs.runtime_agentic_rag_enabled("project") is False
        finally:
            rs.reset_user_rag_runtime(token)

    @pytest.mark.parametrize(("raw", "expected"), [(0.5, 0.5), (3, 1.0), (-1, 0.0), ("x", 0.0), (None, 0.0)])
    def test_similarity_threshold_is_clamped(self, raw, expected):
        token = rs.bind_user_rag_runtime({"agent": {"rag_similarity_threshold": raw}})
        try:
            assert rs.runtime_rag_similarity_threshold("agent") == expected
        finally:
            rs.reset_user_rag_runtime(token)

    def test_memory_threshold_comes_from_the_environment(self, monkeypatch):
        import backend.services.memory_rag_env as env

        monkeypatch.setattr(env, "get_memory_similarity_threshold", lambda: 0.42)

        assert rs.runtime_rag_similarity_threshold(" Memory ") == 0.42

    def test_the_system_prompts_of_all_contributing_stores_are_merged_without_duplicates(self, monkeypatch):
        import backend.services.memory_rag_env as env

        monkeypatch.setattr(env, "get_memory_system_prompt", lambda: "  Промпт библиотеки  ")
        token = rs.bind_user_rag_runtime({"agent": {"rag_system_prompt": "Промпт агента"}, "project": {"rag_system_prompt": "Промпт агента"}})
        try:
            assert rs.merged_rag_system_prompt(["agent", "project", "memory", None, ""]) == "Промпт агента\n\nПромпт библиотеки"
            assert rs.runtime_rag_system_prompt(["agent"]) == "Промпт агента"
        finally:
            rs.reset_user_rag_runtime(token)

        assert rs.runtime_rag_system_prompt() == "" and rs.merged_rag_system_prompt([]) == "" and rs.merged_rag_system_prompt(None) == ""


class TestChunkAndResponse:
    def test_chunk_parameters_are_clamped(self):
        assert rs.chunk_params_from_rag_settings({"rag_chunk_size": 50, "rag_chunk_overlap": 5000, "rag_chunking_strategy": " Markdown "}) == {
            "chunk_size": 200, "chunk_overlap": 2000, "chunking_strategy": "markdown",
        }
        assert rs.chunk_params_from_rag_settings({"rag_chunk_size": 99999})["chunk_size"] == 8000

    def test_chunk_parameter_defaults_for_garbage(self):
        assert rs.chunk_params_from_rag_settings({"rag_chunk_size": "много", "rag_chunk_overlap": None, "rag_chunking_strategy": "wild"}) == {
            "chunk_size": 1000, "chunk_overlap": 200, "chunking_strategy": "hierarchical",
        }
        assert rs.chunk_params_from_rag_settings({}) == {"chunk_size": 1000, "chunk_overlap": 200, "chunking_strategy": "hierarchical"}

    def test_the_overlap_may_exceed_the_chunk_size_known_issue(self):
        # Нет проверки overlap < size: size=200 при overlap=2000 даёт перекрытие в 10 раз больше чанка.
        params = rs.chunk_params_from_rag_settings({"rag_chunk_size": 200, "rag_chunk_overlap": 2000})

        assert params["chunk_overlap"] > params["chunk_size"]

    def test_response_for_a_full_snapshot(self):
        response = rs.settings_response_dict(dict(CLUSTER, rag_strategy="graph", rag_embedding_model_path="x/y"))

        assert response["strategy"] == response["applied_method"] == "graph" and "Графовый" in response["method_description"]
        assert response["rag_chat_top_k"] == 8 and response["rag_similarity_threshold"] == 0.25 and response["rag_embedding_model_path"] == "x/y"
        assert response["agentic_rag_enabled"] is False and response["rag_query_fix_typos"] is True

    def test_response_for_an_empty_snapshot_uses_the_environment_defaults(self):
        response = rs.settings_response_dict({})

        assert set(response) >= {"strategy", "rag_chat_top_k", "rag_chunk_size", "rag_system_prompt", "rag_reranker_model_path"}
        assert response["rag_embedding_model_path"] == "" and isinstance(response["rag_chat_top_k"], int)

    def test_unknown_strategies_have_no_description(self):
        assert rs.settings_response_dict(dict(CLUSTER, rag_strategy="exotic"))["method_description"] == ""

    def test_zero_threshold_is_kept_as_zero(self):
        assert rs.settings_response_dict(dict(CLUSTER, rag_similarity_threshold=0))["rag_similarity_threshold"] == 0.0

    def test_index_change_detection(self):
        before = dict(CLUSTER)

        assert rs.index_params_changed(before, dict(before, rag_chunk_size=900)) is True
        assert rs.index_params_changed(before, dict(before, rag_embedding_model_path="x/y")) is True
        assert rs.index_params_changed(before, dict(before, rag_chat_top_k=99, rag_similarity_threshold=0.9)) is False
        assert rs.index_params_changed({}, {}) is False


class TestModelPaths:
    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("", {}), (None, {}), ("   ", {}),
            ("FRIDA", {"embedding_model": "FRIDA", "embedding_provider": "native"}),
            ("local/FRIDA", {"embedding_model": "FRIDA", "embedding_provider": "native"}),
            ("NATIVE/bge", {"embedding_model": "bge", "embedding_provider": "native"}),
            ("corsur_rag/embed/FRIDA", {"embedding_model": "embed/FRIDA", "embedding_provider": "corsur_rag"}),
            (" phoenix_embeddings / m1 ", {"embedding_model": "m1", "embedding_provider": "phoenix_embeddings"}),
            ("provider/", {}),
            ("/model", {"embedding_model": "model", "embedding_provider": ""}),
        ],
    )
    def test_embedding_paths(self, path, expected):
        assert rs.embedding_fields_from_path(path) == expected

    def test_reranker_paths_use_their_own_keys(self):
        assert rs.reranker_fields_from_path("PHOENIX/rr-1") == {"reranker_model": "rr-1", "reranker_provider": "PHOENIX"}
        assert rs.reranker_fields_from_path("") == {} and rs.reranker_fields_from_path("local/x") == {"reranker_model": "x", "reranker_provider": "native"}

    def test_fields_from_settings(self):
        assert rs.embedding_fields_from_rag_settings({"rag_embedding_model_path": "a/b"}) == {"embedding_model": "b", "embedding_provider": "a"}
        assert rs.embedding_fields_from_rag_settings(None) == {} and rs.reranker_fields_from_rag_settings({}) == {}

    def test_runtime_fields(self):
        token = rs.bind_user_rag_runtime({"agent": {"rag_embedding_model_path": "p/m", "rag_reranker_model_path": "q/r"}})
        try:
            assert rs.runtime_embedding_fields("agent") == {"embedding_model": "m", "embedding_provider": "p"}
            assert rs.runtime_reranker_fields("agent") == {"reranker_model": "r", "reranker_provider": "q"}
            assert rs.runtime_embedding_fields("project") == {}
        finally:
            rs.reset_user_rag_runtime(token)

    async def test_fields_for_a_stored_entity(self, storage):
        storage.entities.rows[("agent", "7")] = {"rag_embedding_model_path": "p/m"}

        assert await rs.embedding_fields_for_entity("agent", 7) == {"embedding_model": "m", "embedding_provider": "p"}
        assert await rs.embedding_fields_for_entity("agent", None) == {}
        assert await rs.embedding_fields_for_entity("agent", 8) == {}
