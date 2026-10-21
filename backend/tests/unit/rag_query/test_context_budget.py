"""rag_query/context_budget: лимиты CONTEXT для RAG по сторам."""

import pytest

from backend.rag_query import context_budget as cb

pytestmark = pytest.mark.unit

ALL_ENV = (
    "RAG_CONTEXT_MAX_TOKENS",
    "RAG_CONTEXT_PROJECT_MAX_TOKENS",
    "RAG_CONTEXT_KNOWLEDGE_BASE_MAX_TOKENS",
    "RAG_CONTEXT_AGENT_MAX_TOKENS",
    "RAG_CONTEXT_KB_MAX_TOKENS",
    "RAG_CONTEXT_MEMORY_MAX_TOKENS",
    "RAG_CHARS_PER_TOKEN",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ALL_ENV:
        monkeypatch.delenv(name, raising=False)


class TestStoreKey:
    @pytest.mark.parametrize(
        ("label", "expected"),
        [
            ("project", "project"),
            ("project (direct)", "project"),
            ("  KB (direct)  ", "kb"),
            ("(memory)", "memory"),
            ("agent", "agent"),
            ("unknown", None),
            ("", None),
            (None, None),
        ],
    )
    def test_key(self, label, expected):
        assert cb.store_key(label) == expected


class TestMaxTokens:
    def test_default(self):
        assert cb.rag_context_max_tokens() == 256_000

    def test_global_override(self, monkeypatch):
        monkeypatch.setenv("RAG_CONTEXT_MAX_TOKENS", "50000")

        assert cb.rag_context_max_tokens() == 50_000
        assert cb.rag_context_max_tokens("project") == 50_000

    def test_store_override_beats_global(self, monkeypatch):
        monkeypatch.setenv("RAG_CONTEXT_MAX_TOKENS", "50000")
        monkeypatch.setenv("RAG_CONTEXT_PROJECT_MAX_TOKENS", "8000")

        assert cb.rag_context_max_tokens("project (direct)") == 8000
        assert cb.rag_context_max_tokens("memory") == 50_000

    def test_kb_accepts_agent_synonym(self, monkeypatch):
        monkeypatch.setenv("RAG_CONTEXT_AGENT_MAX_TOKENS", "12000")

        assert cb.rag_context_max_tokens("kb") == 12_000
        assert cb.rag_context_max_tokens("agent") == 12_000

    def test_kb_specific_beats_synonym(self, monkeypatch):
        monkeypatch.setenv("RAG_CONTEXT_KNOWLEDGE_BASE_MAX_TOKENS", "3000")
        monkeypatch.setenv("RAG_CONTEXT_AGENT_MAX_TOKENS", "12000")

        assert cb.rag_context_max_tokens("kb") == 3000

    def test_agent_accepts_kb_alias(self, monkeypatch):
        monkeypatch.setenv("RAG_CONTEXT_KB_MAX_TOKENS", "7000")

        assert cb.rag_context_max_tokens("agent") == 7000

    @pytest.mark.parametrize("raw", ["", "  ", "abc", "1.5"])
    def test_garbage_falls_back_to_default(self, monkeypatch, raw):
        monkeypatch.setenv("RAG_CONTEXT_MAX_TOKENS", raw)

        assert cb.rag_context_max_tokens() == 256_000

    def test_garbage_store_value_falls_back_to_global(self, monkeypatch):
        monkeypatch.setenv("RAG_CONTEXT_MAX_TOKENS", "20000")
        monkeypatch.setenv("RAG_CONTEXT_MEMORY_MAX_TOKENS", "oops")

        assert cb.rag_context_max_tokens("memory") == 20_000

    @pytest.mark.parametrize(("raw", "expected"), [("5", 1000), ("999999999", 2_000_000), ("-1", 1000)])
    def test_values_are_clamped(self, monkeypatch, raw, expected):
        monkeypatch.setenv("RAG_CONTEXT_MAX_TOKENS", raw)

        assert cb.rag_context_max_tokens() == expected


class TestChars:
    def test_default_chars_per_token(self):
        assert cb.rag_chars_per_token() == 4.0

    def test_override_and_clamp(self, monkeypatch):
        monkeypatch.setenv("RAG_CHARS_PER_TOKEN", "2.5")
        assert cb.rag_chars_per_token() == 2.5
        monkeypatch.setenv("RAG_CHARS_PER_TOKEN", "100")
        assert cb.rag_chars_per_token() == 16.0
        monkeypatch.setenv("RAG_CHARS_PER_TOKEN", "0")
        assert cb.rag_chars_per_token() == 1.0
        monkeypatch.setenv("RAG_CHARS_PER_TOKEN", "x")
        assert cb.rag_chars_per_token() == 4.0

    def test_max_chars_is_tokens_times_ratio(self, monkeypatch):
        monkeypatch.setenv("RAG_CONTEXT_MAX_TOKENS", "10000")
        monkeypatch.setenv("RAG_CHARS_PER_TOKEN", "3")

        assert cb.rag_context_max_chars() == 30_000

    def test_default_max_chars(self):
        assert cb.rag_context_max_chars("kb (direct)") == 1_024_000


def test_budget_overview(monkeypatch):
    monkeypatch.setenv("RAG_CONTEXT_PROJECT_MAX_TOKENS", "9000")

    overview = cb.budget_overview()

    assert overview["chars_per_token"] == 4.0
    assert overview["default_tokens"] == 256_000
    assert overview["by_store_tokens"] == {
        "project": 9000,
        "kb": 256_000,
        "memory": 256_000,
    }
