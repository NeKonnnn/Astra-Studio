"""services/user_llm_settings и user_rag_settings: недоступные репозитории, app_state, граничные значения."""

import sys
from types import SimpleNamespace

import pytest

from backend.services import user_llm_settings as uls
from backend.services import user_rag_settings as urs

pytestmark = pytest.mark.unit


class TestDefaultModelSettings:
    @staticmethod
    def with_model_settings(monkeypatch, value):
        import backend.app_state as state

        monkeypatch.setattr(state, "model_settings", value, raising=False)

    def test_default_settings_attribute_is_deep_copied(self, monkeypatch):
        original = {"temperature": 0.1, "nested": {"a": 1}}
        self.with_model_settings(monkeypatch, SimpleNamespace(default_settings=original))

        copy = uls._default_model_settings()
        copy["nested"]["a"] = 2

        assert copy["temperature"] == 0.1
        assert original["nested"]["a"] == 1

    def test_get_all_is_used_when_there_is_no_default_settings(self, monkeypatch):
        self.with_model_settings(monkeypatch, SimpleNamespace(get_all=lambda: {"top_k": 5}))

        assert uls._default_model_settings() == {"top_k": 5}

    def test_falls_back_to_seed_defaults_when_state_is_unusable(self, monkeypatch):
        self.with_model_settings(monkeypatch, SimpleNamespace())
        monkeypatch.setattr(uls, "llm_settings_defaults", lambda: {"temperature": 0.9})

        assert uls._default_model_settings() == {"temperature": 0.9}

    def test_falls_back_to_seed_defaults_when_state_import_fails(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "backend.app_state", None)
        monkeypatch.setattr(uls, "llm_settings_defaults", lambda: {"temperature": 0.8})

        assert uls._default_model_settings() == {"temperature": 0.8}

    def test_active_settings_without_override_are_the_defaults(self, monkeypatch):
        monkeypatch.setattr(uls, "_default_model_settings", lambda: {"temperature": 0.5, "top_k": 40})

        assert uls.get_active_model_settings() == {"temperature": 0.5, "top_k": 40}


class TestUserSettingsRepo:
    def test_repository_is_returned(self, monkeypatch):
        sentinel = object()
        monkeypatch.setattr("backend.database.init_db.get_user_settings_repository", lambda: sentinel)

        assert uls._get_repo() is sentinel

    def test_failure_gives_none(self, monkeypatch):
        def boom():
            msg = "no db"
            raise RuntimeError(msg)

        monkeypatch.setattr("backend.database.init_db.get_user_settings_repository", boom)

        assert uls._get_repo() is None


class TestRagSettingsRepos:
    def test_entity_repo(self, monkeypatch):
        sentinel = object()
        monkeypatch.setattr("backend.database.init_db.get_entity_settings_repository", lambda: sentinel)
        assert urs._get_entity_repo() is sentinel

        def boom():
            msg = "no db"
            raise RuntimeError(msg)

        monkeypatch.setattr("backend.database.init_db.get_entity_settings_repository", boom)
        assert urs._get_entity_repo() is None

    def test_user_repo(self, monkeypatch):
        sentinel = object()
        monkeypatch.setattr("backend.database.init_db.get_user_settings_repository", lambda: sentinel)
        assert urs._get_user_repo() is sentinel

        def boom():
            msg = "no db"
            raise RuntimeError(msg)

        monkeypatch.setattr("backend.database.init_db.get_user_settings_repository", boom)
        assert urs._get_user_repo() is None


class TestDefaultsFromAppState:
    def test_unreadable_state_falls_back_to_env_defaults(self, monkeypatch):
        import backend.app_state as state

        class Unprintable:
            def __str__(self):
                msg = "cannot stringify"
                raise RuntimeError(msg)

        monkeypatch.setattr(state, "rag_embedding_model_path", Unprintable(), raising=False)
        monkeypatch.delenv("RAG_ENTITY_EMBEDDING_MODEL", raising=False)
        monkeypatch.delenv("RAG_ENTITY_STRATEGY", raising=False)

        defaults = urs._defaults_from_app_state()

        assert defaults["rag_strategy"] == "hybrid"
        assert defaults["rag_embedding_model_path"] == ""
        assert set(urs.RAG_SETTING_KEYS) <= set(defaults)

    def test_state_paths_win_over_env(self, monkeypatch):
        import backend.app_state as state

        monkeypatch.setattr(state, "rag_embedding_model_path", "corsur/embed/state", raising=False)
        monkeypatch.setattr(state, "rag_reranker_model_path", "", raising=False)
        monkeypatch.setenv("RAG_ENTITY_EMBEDDING_MODEL", "corsur/embed/env")
        monkeypatch.setenv("RAG_ENTITY_RERANKER_MODEL", "corsur/rerank/env")

        defaults = urs._defaults_from_app_state()

        assert defaults["rag_embedding_model_path"] == "corsur/embed/state"
        assert defaults["rag_reranker_model_path"] == "corsur/rerank/env"


class TestChunkParams:
    def test_garbage_values_use_defaults(self):
        out = urs.chunk_params_from_rag_settings(
            {"rag_chunk_size": "many", "rag_chunk_overlap": "some", "rag_chunking_strategy": "magic"}
        )

        assert out == {"chunk_size": 1000, "chunk_overlap": 200, "chunking_strategy": "hierarchical"}

    def test_values_are_clamped(self):
        out = urs.chunk_params_from_rag_settings(
            {"rag_chunk_size": 10, "rag_chunk_overlap": 99999, "rag_chunking_strategy": " Markdown "}
        )

        assert out == {"chunk_size": 200, "chunk_overlap": 199, "chunking_strategy": "markdown"}
