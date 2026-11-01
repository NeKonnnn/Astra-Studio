"""Общие фикстуры SVC-RAG-MODELS.

Сеть, GPU и реальные веса не нужны: torch / sentence_transformers / transformers
подменяются фейками (см. tests/fakes.py), модели - пустые каталоги в tmp_path.
"""

import logging
import os
from collections import OrderedDict
from pathlib import Path

import pytest

# Config читается при импорте app.* (module-level settings), поэтому - здесь,
# до первого импорта приложения. ENV, влияющие на дефолты, чистим заранее.
for _name in (
    "RAG_MODELS_DIR",
    "RAG_EMBEDDING_MODEL",
    "RAG_RERANKER_MODEL",
    "RAG_MODELS_DEVICE",
    "RAG_MODELS_OFFLINE",
    "RAG_MODELS_EMBED_BATCH_SIZE",
):
    os.environ.pop(_name, None)

_TEST_CONFIG = Path(__file__).resolve().parent / "fixtures" / "config.test.yml"
os.environ["CONFIG_PATH"] = str(_TEST_CONFIG)
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from tests.fakes import FakeML  # noqa: E402

# ENV, которые читает сервис и которые не должны протекать из окружения разработчика
_SERVICE_ENV = (
    [f"RAG_EMBEDDING_MODEL{i}" for i in range(2, 21)]
    + [f"RAG_RERANKER_MODEL{i}" for i in range(2, 21)]
    + [
        "RAG_MODELS_API_KEY",
        "RAG_MODELS_API_KEY_ENV",
        "RAG_MODELS_LOG_QUIET_PATHS",
        "RAG_RERANKER_CUTOFF_LAYERS",
        "RAG_RERANKER_BATCH_SIZE",
        "RAG_RERANKER_MAX_LENGTH",
        "AUDIT_CEF_ENABLED",
        "AUDIT_SYSLOG_TARGET",
        "AUDIT_SYSLOG_PORT",
        "AUDIT_SYSLOG_FACILITY",
        "CEF_DEVICE_VERSION",
        "CEF_DVCHOST",
        "CEF_DEVICE_PROCESS",
        "CEF_DEVICE_VENDOR",
        "CEF_DEVICE_PRODUCT",
        "CEF_DPT",
        "DOMAIN_SERVER",
        "LDAP_USER_SEARCH_BASE",
        "MY_POD_NAME",
        "POD_NAME",
        "HOSTNAME",
        "HF_HOME",
    ]
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    for name in _SERVICE_ENV:
        monkeypatch.delenv(name, raising=False)
    # Сервис fail-closed (BUG-RM-01): для тестов без ключа - явное разрешение
    monkeypatch.setenv("RAG_MODELS_ALLOW_INSECURE_NO_AUTH", "1")
    # Кэш HF пишем только в tmp_path; monkeypatch вернёт исходные значения
    cache = tmp_path / "hf-cache"
    monkeypatch.setenv("TRANSFORMERS_CACHE", str(cache))
    for name in (
        "HF_HUB_CACHE",
        "SENTENCE_TRANSFORMERS_HOME",
        "HF_HUB_OFFLINE",
        "TRANSFORMERS_OFFLINE",
        "HF_DATASETS_OFFLINE",
    ):
        monkeypatch.setenv(name, os.environ.get(name, "1" if "OFFLINE" in name else str(cache)))


@pytest.fixture
def models_dir(tmp_path):
    d = tmp_path / "models"
    d.mkdir()
    return d


@pytest.fixture(autouse=True)
def rm_settings(monkeypatch, models_dir):
    """Дефолтные настройки rag_models на каждый тест; прежние значения возвращаются."""
    from app.core.config import settings

    rm = settings.rag_models
    values = {
        "enabled": True,
        "models_dir": str(models_dir),
        "embedding_model": "FRIDA",
        "embedding_model2": "Giga-Embeddings-instruct",
        "embedding_models": [],
        "reranker_model": "ms-marco-MiniLM-L6-v2",
        "reranker_model2": None,
        "reranker_models": [],
        "embedding_model_default": "paraphrase-multilingual-MiniLM-L12-v2",
        "reranker_model_default": "ms-marco-MiniLM-L-6-v2",
        "offline": True,
        "device": "cpu",
        "embedding_dim": 384,
        "embed_batch_size": 16,
        "max_loaded_embedding": 2,
        "max_loaded_reranker": 2,
        "query_prompt_name": "search_query",
        "document_prompt_name": "search_document",
    }
    for key, val in values.items():
        monkeypatch.setattr(rm, key, val)
    return rm


@pytest.fixture(autouse=True)
def handler_state(monkeypatch):
    """Свежее состояние кэша моделей и замков handler'а."""
    import asyncio

    from app.dependencies import rag_models_handler as h

    monkeypatch.setattr(h, "_MODEL_CACHE", OrderedDict())
    monkeypatch.setattr(h, "_KEY_LOCKS", {})
    monkeypatch.setattr(h, "_REGISTRY_LOCK", asyncio.Lock())
    monkeypatch.setattr(h, "_last_rag_models_error", None)
    return h


@pytest.fixture
def fake_ml(monkeypatch):
    """Фейковые torch / sentence_transformers / transformers в sys.modules."""
    return FakeML().install(monkeypatch)


def make_model_dir(models_dir, name, *, config=None, files=()):
    """Создаёт каталог «модели»; config - dict для config.json."""
    import json

    d = Path(models_dir) / name
    d.mkdir(parents=True, exist_ok=True)
    if config is not None:
        (d / "config.json").write_text(json.dumps(config), encoding="utf-8")
    for f in files:
        (d / f).write_text("x", encoding="utf-8")
    return d


@pytest.fixture
def make_model(models_dir):
    def _make(name, **kw):
        return make_model_dir(models_dir, name, **kw)

    return _make


@pytest.fixture
def cef_lines():
    """Собирает строки логгера 'cef' (у него propagate=False, caplog их не видит)."""
    lines = []

    class _H(logging.Handler):
        def emit(self, record):
            lines.append(record.getMessage())

    handler = _H(level=logging.DEBUG)
    cef = logging.getLogger("cef")
    old_level = cef.level
    cef.setLevel(logging.INFO)
    cef.addHandler(handler)
    yield lines
    cef.removeHandler(handler)
    cef.setLevel(old_level)
