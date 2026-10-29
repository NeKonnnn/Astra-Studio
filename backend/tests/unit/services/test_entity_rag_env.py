"""entity_rag_env: дефолты entity-RAG из env (реранкер выключен на CPU)."""

import pytest

from backend.services import entity_rag_env as env


pytestmark = pytest.mark.unit


class TestEntityRerankingDefault:
    def test_default_is_false_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("RAG_ENTITY_RERANKING_ENABLED", raising=False)
        assert env.get_entity_reranking_enabled() is False

    @pytest.mark.parametrize("raw", ["1", "true", "YES", "on"])
    def test_explicit_true(self, monkeypatch, raw):
        monkeypatch.setenv("RAG_ENTITY_RERANKING_ENABLED", raw)
        assert env.get_entity_reranking_enabled() is True

    @pytest.mark.parametrize("raw", ["0", "false", "OFF", "no"])
    def test_explicit_false(self, monkeypatch, raw):
        monkeypatch.setenv("RAG_ENTITY_RERANKING_ENABLED", raw)
        assert env.get_entity_reranking_enabled() is False
