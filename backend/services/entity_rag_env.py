"""
Дефолты РАГ для проектов и агентов — из env / ConfigMap backend.

Memory RAG (Библиотека) — отдельные RAG_MEMORY_* (см. memory_rag_env).

Переменные (Kubernetes ConfigMap astra-chat-backend):
  RAG_ENTITY_EMBEDDING_MODEL        — corsur/embed/FRIDA
  RAG_ENTITY_RERANKER_MODEL         — corsur/rerank/bge-rerank-vs-minicpm-layerwise
  RAG_ENTITY_STRATEGY               — hybrid
  RAG_ENTITY_CHUNKING_STRATEGY      — fixed
  RAG_ENTITY_CHUNK_SIZE             — 4000
  RAG_ENTITY_CHUNK_OVERLAP          — 100
  RAG_ENTITY_CHAT_TOP_K             — 12
  RAG_ENTITY_SIMILARITY_THRESHOLD   — 0
  RAG_ENTITY_AGENTIC_RAG_ENABLED    — true
  RAG_ENTITY_AGENTIC_MAX_ITERATIONS — 2
  RAG_ENTITY_QUERY_FIX_TYPOS        — false
  RAG_ENTITY_MULTI_QUERY_ENABLED    — false
  RAG_ENTITY_HYDE_ENABLED           — false
  RAG_ENTITY_RERANKING_ENABLED      — false (CPU MiniCPM часто > timeout)
  RAG_ENTITY_RERANK_TOP_N           — 12
  RAG_ENTITY_SYSTEM_PROMPT            — (пусто)
"""

from __future__ import annotations

import os

from backend.settings.logging import get_logger

logger = get_logger(__name__)

_VALID_STRATEGIES = frozenset(
    {"auto", "hierarchical", "hybrid", "vector", "lexical", "raw_cosine", "graph"}
)
_VALID_CHUNKING = frozenset(
    {"hierarchical", "fixed", "markdown", "separators", "semantic", "universal"}
)


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    logger.warning("%s=%r не bool — используем default=%s", name, os.getenv(name), default)
    return default


def _env_int(name: str, default: int, *, lo: int, hi: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return max(lo, min(default, hi))
    try:
        return max(lo, min(int(raw), hi))
    except ValueError:
        logger.warning("%s=%r не число — default=%s", name, raw, default)
        return max(lo, min(default, hi))


def _env_float(name: str, default: float, *, lo: float, hi: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return max(lo, min(default, hi))
    try:
        return max(lo, min(float(raw), hi))
    except ValueError:
        logger.warning("%s=%r не число — default=%s", name, raw, default)
        return max(lo, min(default, hi))


def get_entity_embedding_model_path() -> str:
    return (os.getenv("RAG_ENTITY_EMBEDDING_MODEL") or "").strip()


def get_entity_reranker_model_path() -> str:
    return (os.getenv("RAG_ENTITY_RERANKER_MODEL") or "").strip()


def get_entity_rag_strategy() -> str:
    raw = (os.getenv("RAG_ENTITY_STRATEGY") or "hybrid").strip().lower()
    if raw in _VALID_STRATEGIES:
        return raw
    logger.warning("RAG_ENTITY_STRATEGY=%r неизвестна — hybrid", raw)
    return "hybrid"


def get_entity_chunking_strategy() -> str:
    raw = (os.getenv("RAG_ENTITY_CHUNKING_STRATEGY") or "fixed").strip().lower()
    if raw in _VALID_CHUNKING:
        return raw
    logger.warning("RAG_ENTITY_CHUNKING_STRATEGY=%r неизвестна — fixed", raw)
    return "fixed"


def get_entity_chunk_size() -> int:
    return _env_int("RAG_ENTITY_CHUNK_SIZE", 4000, lo=200, hi=8000)


def get_entity_chunk_overlap() -> int:
    return _env_int("RAG_ENTITY_CHUNK_OVERLAP", 100, lo=0, hi=2000)


def get_entity_chat_top_k() -> int:
    return _env_int("RAG_ENTITY_CHAT_TOP_K", 12, lo=1, hi=64)


def get_entity_similarity_threshold() -> float:
    return _env_float("RAG_ENTITY_SIMILARITY_THRESHOLD", 0.0, lo=0.0, hi=1.0)


def get_entity_agentic_rag_enabled() -> bool:
    return _env_bool("RAG_ENTITY_AGENTIC_RAG_ENABLED", True)


def get_entity_agentic_max_iterations() -> int:
    return _env_int("RAG_ENTITY_AGENTIC_MAX_ITERATIONS", 2, lo=1, hi=8)


def get_entity_query_fix_typos() -> bool:
    return _env_bool("RAG_ENTITY_QUERY_FIX_TYPOS", False)


def get_entity_multi_query_enabled() -> bool:
    return _env_bool("RAG_ENTITY_MULTI_QUERY_ENABLED", False)


def get_entity_hyde_enabled() -> bool:
    return _env_bool("RAG_ENTITY_HYDE_ENABLED", False)


def get_entity_reranking_enabled() -> bool:
    # False по умолчанию: MiniCPM на CPU часто >2 мин и срывает
    # SVC_RAG_SEARCH_READ_TIMEOUT — чат получает «документов нет» при живом корпусе.
    return _env_bool("RAG_ENTITY_RERANKING_ENABLED", False)


def get_entity_rerank_top_n() -> int:
    return _env_int("RAG_ENTITY_RERANK_TOP_N", 12, lo=1, hi=64)


def get_entity_system_prompt() -> str:
    return (os.getenv("RAG_ENTITY_SYSTEM_PROMPT") or "").strip()
