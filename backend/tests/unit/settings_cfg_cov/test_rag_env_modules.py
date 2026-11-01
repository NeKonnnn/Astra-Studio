"""services/entity_rag_env и services/memory_rag_env: RAG-дефолты из ENV (проекты, агенты, Библиотека)."""

import os

import pytest

from backend.services import entity_rag_env as ee
from backend.services import memory_rag_env as me

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in list(os.environ):
        if name.startswith(("RAG_ENTITY_", "RAG_MEMORY_")) or name in (
            "RAG_CHUNK_SIZE",
            "RAG_CHUNK_OVERLAP",
            "RAG_CHAT_TOP_K",
            "RAG_USE_RERANKING",
            "RAG_RERANK_TOP_N",
            "RAG_MIN_SIMILARITY",
        ):
            monkeypatch.delenv(name, raising=False)


class TestEntityEnvPrimitives:
    @pytest.mark.parametrize(
        ("raw", "default", "expected"),
        [(None, True, True), ("", False, False), ("YES", False, True), (" on ", False, True), ("0", True, False), ("off", True, False), ("maybe", True, True), ("maybe", False, False)],
    )
    def test_env_bool(self, monkeypatch, raw, default, expected):
        if raw is None:
            monkeypatch.delenv("X_FLAG", raising=False)
        else:
            monkeypatch.setenv("X_FLAG", raw)

        assert ee._env_bool("X_FLAG", default) is expected

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [(None, 12), ("", 12), ("5", 5), ("500", 64), ("-3", 1), ("abc", 12)],
    )
    def test_env_int_is_clamped(self, monkeypatch, raw, expected):
        if raw is None:
            monkeypatch.delenv("X_NUM", raising=False)
        else:
            monkeypatch.setenv("X_NUM", raw)

        assert ee._env_int("X_NUM", 12, lo=1, hi=64) == expected

    def test_env_int_default_is_clamped_too(self):
        assert ee._env_int("X_UNSET", 500, lo=1, hi=64) == 64

    @pytest.mark.parametrize(("raw", "expected"), [(None, 0.2), ("0.5", 0.5), ("7", 1.0), ("-1", 0.0), ("x", 0.2)])
    def test_env_float_is_clamped(self, monkeypatch, raw, expected):
        if raw is None:
            monkeypatch.delenv("X_FLOAT", raising=False)
        else:
            monkeypatch.setenv("X_FLOAT", raw)

        assert ee._env_float("X_FLOAT", 0.2, lo=0.0, hi=1.0) == expected


class TestEntityGetters:
    def test_defaults(self):
        assert ee.get_entity_embedding_model_path() == ""
        assert ee.get_entity_reranker_model_path() == ""
        assert ee.get_entity_rag_strategy() == "hybrid"
        assert ee.get_entity_chunking_strategy() == "fixed"
        assert ee.get_entity_chunk_size() == 4000
        assert ee.get_entity_chunk_overlap() == 100
        assert ee.get_entity_chat_top_k() == 12
        assert ee.get_entity_similarity_threshold() == 0.0
        assert ee.get_entity_agentic_rag_enabled() is True
        assert ee.get_entity_agentic_max_iterations() == 2
        assert ee.get_entity_query_fix_typos() is False
        assert ee.get_entity_multi_query_enabled() is False
        assert ee.get_entity_hyde_enabled() is False
        assert ee.get_entity_reranking_enabled() is True
        assert ee.get_entity_rerank_top_n() == 12
        assert ee.get_entity_system_prompt() == ""

    def test_values_from_env(self, monkeypatch):
        monkeypatch.setenv("RAG_ENTITY_EMBEDDING_MODEL", " corsur/embed/FRIDA ")
        monkeypatch.setenv("RAG_ENTITY_RERANKER_MODEL", "corsur/rerank/x")
        monkeypatch.setenv("RAG_ENTITY_STRATEGY", " Vector ")
        monkeypatch.setenv("RAG_ENTITY_CHUNKING_STRATEGY", "MARKDOWN")
        monkeypatch.setenv("RAG_ENTITY_CHUNK_SIZE", "100")
        monkeypatch.setenv("RAG_ENTITY_CHUNK_OVERLAP", "9999")
        monkeypatch.setenv("RAG_ENTITY_CHAT_TOP_K", "30")
        monkeypatch.setenv("RAG_ENTITY_SIMILARITY_THRESHOLD", "0.35")
        monkeypatch.setenv("RAG_ENTITY_AGENTIC_RAG_ENABLED", "false")
        monkeypatch.setenv("RAG_ENTITY_AGENTIC_MAX_ITERATIONS", "99")
        monkeypatch.setenv("RAG_ENTITY_QUERY_FIX_TYPOS", "1")
        monkeypatch.setenv("RAG_ENTITY_MULTI_QUERY_ENABLED", "true")
        monkeypatch.setenv("RAG_ENTITY_HYDE_ENABLED", "on")
        monkeypatch.setenv("RAG_ENTITY_RERANKING_ENABLED", "no")
        monkeypatch.setenv("RAG_ENTITY_RERANK_TOP_N", "0")
        monkeypatch.setenv("RAG_ENTITY_SYSTEM_PROMPT", "  Отвечай кратко  ")

        assert ee.get_entity_embedding_model_path() == "corsur/embed/FRIDA"
        assert ee.get_entity_reranker_model_path() == "corsur/rerank/x"
        assert ee.get_entity_rag_strategy() == "vector"
        assert ee.get_entity_chunking_strategy() == "markdown"
        assert ee.get_entity_chunk_size() == 200  # нижняя граница
        assert ee.get_entity_chunk_overlap() == 2000  # верхняя граница
        assert ee.get_entity_chat_top_k() == 30
        assert ee.get_entity_similarity_threshold() == 0.35
        assert ee.get_entity_agentic_rag_enabled() is False
        assert ee.get_entity_agentic_max_iterations() == 8
        assert ee.get_entity_query_fix_typos() is True
        assert ee.get_entity_multi_query_enabled() is True
        assert ee.get_entity_hyde_enabled() is True
        assert ee.get_entity_reranking_enabled() is False
        assert ee.get_entity_rerank_top_n() == 1
        assert ee.get_entity_system_prompt() == "Отвечай кратко"

    def test_unknown_strategies_fall_back(self, monkeypatch):
        monkeypatch.setenv("RAG_ENTITY_STRATEGY", "telepathy")
        monkeypatch.setenv("RAG_ENTITY_CHUNKING_STRATEGY", "magic")

        assert ee.get_entity_rag_strategy() == "hybrid"
        assert ee.get_entity_chunking_strategy() == "fixed"


class TestMemoryPrimitives:
    def test_env_bool_int_float(self, monkeypatch):
        monkeypatch.setenv("M_B", "TRUE")
        monkeypatch.setenv("M_B2", "weird")
        monkeypatch.setenv("M_B3", "off")
        monkeypatch.setenv("M_I", "900")
        monkeypatch.setenv("M_I2", "x")
        monkeypatch.setenv("M_F", "3")
        monkeypatch.setenv("M_F2", "x")

        assert me._env_bool("M_B", False) is True
        assert me._env_bool("M_B2", True) is True
        assert me._env_bool("M_B3", True) is False
        assert me._env_bool("M_MISSING", False) is False
        assert me._env_int("M_I", 5, lo=1, hi=64) == 64
        assert me._env_int("M_I2", 5, lo=1, hi=64) == 5
        assert me._env_int("M_MISSING", 500, lo=1, hi=64) == 64
        assert me._env_float("M_F", 0.1, lo=0.0, hi=1.0) == 1.0
        assert me._env_float("M_F2", 0.1, lo=0.0, hi=1.0) == 0.1
        assert me._env_float("M_MISSING", 5.0, lo=0.0, hi=1.0) == 1.0


class TestMemoryFields:
    def test_embedding_and_reranker_fields_follow_the_path_format(self, monkeypatch):
        from backend.services import user_rag_settings as urs

        monkeypatch.setattr(urs, "embedding_fields_from_path", lambda path: {"emb": path})
        monkeypatch.setattr(urs, "reranker_fields_from_path", lambda path: {"rr": path})
        monkeypatch.setenv("RAG_MEMORY_EMBEDDING_MODEL", "corsur/embed/FRIDA")

        assert me.get_memory_embedding_fields() == {"emb": "corsur/embed/FRIDA"}
        assert me.get_memory_reranker_fields() == {"rr": None}

    def test_chunk_params_defaults_and_fallback_chain(self, monkeypatch):
        assert me.get_memory_chunk_index_params() == {
            "chunk_size": 1000,
            "chunk_overlap": 200,
            "chunking_strategy": "universal",
        }

        monkeypatch.setenv("RAG_CHUNK_SIZE", "1500")
        monkeypatch.setenv("RAG_CHUNK_OVERLAP", "50")
        assert me.get_memory_chunk_index_params()["chunk_size"] == 1500
        monkeypatch.setenv("RAG_MEMORY_CHUNK_SIZE", "300")
        monkeypatch.setenv("RAG_MEMORY_CHUNK_OVERLAP", "10")
        monkeypatch.setenv("RAG_MEMORY_CHUNKING_STRATEGY", "Markdown")
        assert me.get_memory_chunk_index_params() == {
            "chunk_size": 300,
            "chunk_overlap": 10,
            "chunking_strategy": "markdown",
        }

    def test_unknown_chunking_strategy_becomes_universal(self, monkeypatch):
        monkeypatch.setenv("RAG_MEMORY_CHUNKING_STRATEGY", "nonsense")

        assert me.get_memory_chunk_index_params()["chunking_strategy"] == "universal"

    def test_chat_top_k_fallback_chain(self, monkeypatch):
        assert me.get_memory_rag_chat_top_k() == 12
        monkeypatch.setenv("RAG_CHAT_TOP_K", "20")
        assert me.get_memory_rag_chat_top_k() == 20
        monkeypatch.setenv("RAG_MEMORY_CHAT_TOP_K", "5")
        assert me.get_memory_rag_chat_top_k() == 5

    def test_retrieval_settings_defaults_and_overrides(self, monkeypatch):
        assert me.get_memory_rag_retrieval_settings() == {
            "rag_query_fix_typos": False,
            "rag_multi_query_enabled": False,
            "rag_hyde_enabled": False,
            "rag_reranking_enabled": True,
            "rag_rerank_top_n": 12,
            "rag_similarity_threshold": 0.0,
            "rag_chat_top_k": 12,
            "rag_system_prompt": "",
        }

        monkeypatch.setenv("RAG_USE_RERANKING", "false")
        monkeypatch.setenv("RAG_RERANK_TOP_N", "7")
        monkeypatch.setenv("RAG_MEMORY_HYDE_ENABLED", "yes")
        monkeypatch.setenv("RAG_MEMORY_SYSTEM_PROMPT", " Только по CONTEXT ")
        monkeypatch.setenv("RAG_MIN_SIMILARITY", "0.2")
        settings = me.get_memory_rag_retrieval_settings()

        assert settings["rag_reranking_enabled"] is False
        assert settings["rag_rerank_top_n"] == 7
        assert settings["rag_hyde_enabled"] is True
        assert settings["rag_system_prompt"] == "Только по CONTEXT"
        assert settings["rag_similarity_threshold"] == 0.2

        monkeypatch.setenv("RAG_MEMORY_SIMILARITY_THRESHOLD", "0.6")
        assert me.get_memory_similarity_threshold() == 0.6


class TestMemoryHitFilter:
    HITS = [("a", 0.9, 1, 0), ("b", 0.4, 2, 0), ("c", "bad", 3, 0), ("d",), ("e", None, 5, 0)]

    def test_empty_input(self):
        assert me.filter_hits_by_memory_similarity(None) == []
        assert me.filter_hits_by_memory_similarity([]) == []

    def test_zero_threshold_keeps_everything(self):
        assert me.filter_hits_by_memory_similarity(self.HITS) == self.HITS

    def test_threshold_drops_weak_and_malformed_hits(self, monkeypatch):
        monkeypatch.setenv("RAG_MEMORY_SIMILARITY_THRESHOLD", "0.5")

        assert me.filter_hits_by_memory_similarity(self.HITS) == [("a", 0.9, 1, 0)]
