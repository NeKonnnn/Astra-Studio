"""backend/app_state.py: импорт сервисов с деградацией, env-настройки RAG, settings.json."""

import importlib.util
import json
import os
import sys
import types
from pathlib import Path

import pytest

import backend.app_state as real_app_state

_STATE_PATH = Path(real_app_state.__file__)

_SERVICE_MODULES = {
    "backend.agent_llm_svc": {
        "ask_agent": object(),
        "get_model_info": lambda: None,
        "initialize_model": object(),
        "model_settings": object(),
        "reload_model_by_path": object(),
        "update_model_settings": object(),
    },
    "backend.context_prompts": {"context_prompt_manager": object()},
    "backend.database.memory_service": {
        n: object()
        for n in (
            "clear_dialog_history get_or_create_conversation_id get_recent_dialog_history "
            "load_dialog_history remove_last_user_message reset_conversation "
            "save_assistant_response save_dialog_entry set_current_conversation_id "
            "verify_conversation_owner"
        ).split()
    },
    "backend.transcription.voice": {
        n: object() for n in "check_stt_available recognize_speech recognize_speech_from_file speak_text".split()
    },
    "backend.database.minio": {"get_minio_client": lambda: "minio-client"},
    "backend.settings.rag_client": {
        "get_rag_client": lambda: types.SimpleNamespace(base_url="http://rag")
    },
    "backend.settings.rag_models_client": {
        "get_rag_models_client": lambda: types.SimpleNamespace(base_url="http://rag-models")
    },
    "backend.transcription.universal_transcriber": {
        "UniversalTranscriber": lambda engine: types.SimpleNamespace(engine=engine)
    },
    "backend.database.init_db": {
        n: object()
        for n in (
            "close_databases get_conversation_repository get_document_repository "
            "get_vector_repository init_databases"
        ).split()
    },
}


@pytest.fixture
def load_state(monkeypatch, tmp_path):
    """Фабрика: свежая копия app_state c поддельными сервисами и settings.json в tmp_path.

    ``load_state(broken=[...], env={...}, settings=None|dict)``: ``broken`` — имена модулей,
    импорт которых должен упасть.
    """
    counter = {"n": 0}

    def _load(broken=(), env=None, settings=None, raw_settings=None):
        settings_path = tmp_path / "settings.json"
        if raw_settings is not None:
            settings_path.write_text(raw_settings, encoding="utf-8")
        elif settings is not None:
            settings_path.write_text(json.dumps(settings), encoding="utf-8")
        monkeypatch.setenv("APP_SETTINGS_PATH", str(settings_path))
        for key in (
            "RAG_MIN_SIMILARITY RAG_RERANK_TOP_N RAG_CHAT_TOP_K RAG_CHUNK_SIZE RAG_CHUNK_OVERLAP "
            "RAG_QUERY_FIX_TYPOS RAG_MULTI_QUERY_ENABLED RAG_HYDE_ENABLED RAG_USE_RERANKING"
        ).split():
            monkeypatch.delenv(key, raising=False)
        for key, value in (env or {}).items():
            monkeypatch.setenv(key, value)
        for name, attrs in _SERVICE_MODULES.items():
            if name in broken:
                monkeypatch.setitem(sys.modules, name, None)
            else:
                monkeypatch.setitem(sys.modules, name, types.SimpleNamespace(**attrs))
        counter["n"] += 1
        spec = importlib.util.spec_from_file_location(f"backend._app_state_copy{counter['n']}", _STATE_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    return _load


# ---------------------------------------------------------------- импорт сервисов


def test_all_services_available(load_state):
    st = load_state()
    assert st.ask_agent is not None and st.context_prompt_manager is not None
    assert st.minio_client == "minio-client"
    assert st.rag_client.base_url == "http://rag"
    assert st.rag_models_client.base_url == "http://rag-models"
    assert st.transcriber.engine == "whisperx"
    assert st.database_available is True


def test_agent_import_failure_nulls_llm_helpers(load_state):
    st = load_state(broken=["backend.agent_llm_svc"])
    for name in (
        "ask_agent", "model_settings", "update_model_settings", "reload_model_by_path",
        "get_model_info", "initialize_model", "context_prompt_manager",
    ):
        assert getattr(st, name) is None


def test_memory_import_failure(load_state):
    st = load_state(broken=["backend.database.memory_service"])
    for name in (
        "save_dialog_entry", "save_assistant_response", "load_dialog_history",
        "clear_dialog_history", "get_recent_dialog_history", "reset_conversation",
        "get_or_create_conversation_id", "remove_last_user_message",
        "set_current_conversation_id", "verify_conversation_owner",
    ):
        assert getattr(st, name) is None


def test_voice_import_failure(load_state):
    st = load_state(broken=["backend.transcription.voice"])
    assert st.speak_text is None and st.recognize_speech is None
    assert st.recognize_speech_from_file is None and st.check_stt_available is None


def test_minio_import_failure(load_state):
    assert load_state(broken=["backend.database.minio"]).minio_client is None


def test_rag_clients_failure(load_state):
    st = load_state(broken=["backend.settings.rag_client", "backend.settings.rag_models_client"])
    assert st.rag_client is None and st.rag_models_client is None


def test_transcriber_failure(load_state):
    st = load_state(broken=["backend.transcription.universal_transcriber"])
    assert st.UniversalTranscriber is None and st.transcriber is None


def test_database_failure_marks_unavailable(load_state):
    st = load_state(broken=["backend.database.init_db"])
    assert st.database_available is False
    assert st.init_databases is None and st.close_databases is None
    assert st.get_conversation_repository is None
    assert st.get_document_repository is None and st.get_vector_repository is None


# ------------------------------------------------------------------- env-настройки


@pytest.mark.parametrize(
    ("value", "default", "expected"),
    [
        ("", False, False),
        ("", True, True),
        ("0", True, False),
        ("false", True, False),
        ("No", True, False),
        ("OFF", True, False),
        ("1", False, True),
        ("yes", False, True),
    ],
)
def test_env_rag_pipeline_bool(load_state, monkeypatch, value, default, expected):
    st = load_state()
    monkeypatch.setenv("SOME_FLAG", value)
    assert st._env_rag_pipeline_bool("SOME_FLAG", default) is expected


def test_env_defaults(load_state):
    st = load_state()
    assert st.rag_reranking_enabled is True
    assert st.rag_query_fix_typos is False
    assert st.rag_chunk_size == 1000 and st.rag_chunk_overlap == 200
    assert st.rag_similarity_threshold == 0.0
    assert st.rag_rerank_top_n == 12 and st.rag_chat_top_k == 12


def test_invalid_env_numbers_fall_back(load_state):
    st = load_state(
        env={"RAG_MIN_SIMILARITY": "abc", "RAG_RERANK_TOP_N": "x", "RAG_CHAT_TOP_K": "y"}
    )
    assert st.rag_similarity_threshold == 0.0
    assert st.rag_rerank_top_n == 12
    assert st.rag_chat_top_k == 12


def test_env_numbers_are_clamped(load_state):
    st = load_state(
        env={
            "RAG_MIN_SIMILARITY": "7",
            "RAG_RERANK_TOP_N": "1000",
            "RAG_CHAT_TOP_K": "-5",
            "RAG_CHUNK_SIZE": "10",
            "RAG_CHUNK_OVERLAP": "50",
        }
    )
    assert st.rag_similarity_threshold == 1.0
    assert st.rag_rerank_top_n == 64
    assert st.rag_chat_top_k == 1
    assert st.rag_chunk_size == 200
    assert st.rag_chunk_overlap == 50


# --------------------------------------------------------------- параметры чанкинга


def test_get_library_chunk_index_params_delegates(load_state, monkeypatch):
    st = load_state()
    fake = types.SimpleNamespace(get_memory_chunk_index_params=lambda: {"chunk_size": 5})
    monkeypatch.setitem(sys.modules, "backend.services.memory_rag_env", fake)
    assert st.get_library_chunk_index_params() == {"chunk_size": 5}


def test_get_mcp_platform_service(load_state, monkeypatch):
    st = load_state()
    monkeypatch.setitem(sys.modules, "backend.mcp.platform", types.SimpleNamespace(get_mcp_platform=lambda: "platform"))
    assert st.get_mcp_platform_service() == "platform"


def test_rag_chunk_index_params_normal(load_state):
    st = load_state()
    st.rag_chunk_size, st.rag_chunk_overlap, st.rag_chunking_strategy = 500, 100, " Markdown "
    assert st.get_rag_chunk_index_params() == {
        "chunk_size": 500, "chunk_overlap": 100, "chunking_strategy": "markdown",
    }


def test_rag_chunk_index_params_invalid_values(load_state):
    st = load_state()
    st.rag_chunk_size, st.rag_chunk_overlap, st.rag_chunking_strategy = "bad", None, "weird"
    assert st.get_rag_chunk_index_params() == {
        "chunk_size": 1000, "chunk_overlap": 200, "chunking_strategy": "hierarchical",
    }


def test_rag_chunk_index_params_clamps_and_empty_strategy(load_state):
    st = load_state()
    st.rag_chunk_size, st.rag_chunk_overlap, st.rag_chunking_strategy = 99999, 99999, ""
    assert st.get_rag_chunk_index_params() == {
        "chunk_size": 8000, "chunk_overlap": 2000, "chunking_strategy": "hierarchical",
    }


def test_get_rag_chat_top_k_prefers_runtime_value(load_state, monkeypatch):
    st = load_state()
    seen = []
    monkeypatch.setitem(
        sys.modules, "backend.services.user_rag_settings",
        types.SimpleNamespace(runtime_rag_top_k=lambda scope: seen.append(scope) or 33),
    )
    assert st.get_rag_chat_top_k("agent") == 33
    assert seen == ["agent"]


def test_get_rag_chat_top_k_falls_back_to_global(load_state, monkeypatch):
    st = load_state()
    monkeypatch.setitem(sys.modules, "backend.services.user_rag_settings", None)
    st.rag_chat_top_k = 100
    assert st.get_rag_chat_top_k() == 64
    st.rag_chat_top_k = "oops"
    assert st.get_rag_chat_top_k("project") == 12
    st.rag_chat_top_k = 0
    assert st.get_rag_chat_top_k() == 1


# ----------------------------------------------------------- сравнение моделей


def test_model_comparison_models_are_normalized_and_stored(load_state):
    st = load_state()
    assert st.set_model_comparison_models([" a ", "b", "a", "", None, 5]) == ["a", "b", "5"]
    assert st.get_model_comparison_models() == ["a", "b", "5"]
    copy = st.get_model_comparison_models()
    copy.append("zzz")
    assert st.get_model_comparison_models() == ["a", "b", "5"]
    assert st.set_model_comparison_models(None) == []
    assert st.get_model_comparison_models() == []


# ------------------------------------------------------------ путь settings.json


def _fake_fs(monkeypatch, st, *, exists=(), writable=(), dirs=()):
    """Подмена файловых проверок по абсолютным путям (нормализованным abspath)."""
    exists = {os.path.abspath(p) for p in exists}
    writable = {os.path.abspath(p) for p in writable}
    dirs = {os.path.abspath(p) for p in dirs}
    monkeypatch.setattr(st.os.path, "exists", lambda p: os.path.abspath(p) in exists)
    monkeypatch.setattr(st.os.path, "isdir", lambda p: os.path.abspath(p) in dirs)
    monkeypatch.setattr(st.os, "access", lambda p, mode: os.path.abspath(p) in writable)


def test_resolve_settings_file_uses_env_path_when_writable(load_state, monkeypatch, tmp_path):
    st = load_state()
    target = tmp_path / "env.json"
    monkeypatch.setenv("APP_SETTINGS_PATH", str(target))
    _fake_fs(monkeypatch, st, exists=[target], writable=[target])
    assert st._resolve_settings_file() == os.path.abspath(target)


def test_resolve_settings_file_skips_readonly_and_picks_writable_dir(load_state, monkeypatch, tmp_path):
    st = load_state()
    ro = tmp_path / "ro.json"
    monkeypatch.setenv("APP_SETTINGS_PATH", str(ro))
    monkeypatch.setattr(st, "_THIS_DIR", str(tmp_path / "sub"))
    # ro существует, но не пишется; /app/settings.json недоступен;
    # <THIS_DIR>/settings.json не существует, каталог пишется.
    _fake_fs(monkeypatch, st, exists=[ro], writable=[tmp_path / "sub"], dirs=[tmp_path / "sub"])
    assert st._resolve_settings_file() == os.path.abspath(tmp_path / "sub" / "settings.json")


def test_resolve_settings_file_last_resort_is_cwd(load_state, monkeypatch, tmp_path):
    st = load_state()
    monkeypatch.delenv("APP_SETTINGS_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    _fake_fs(monkeypatch, st)
    assert st._resolve_settings_file() == os.path.abspath(tmp_path / "settings.json")


def test_settings_file_fallbacks_are_unique(load_state, monkeypatch, tmp_path):
    st = load_state()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(st.tempfile, "gettempdir", lambda: str(tmp_path / "tmp"))
    primary = str(tmp_path / "settings.json")
    result = st._settings_file_fallbacks(primary)
    assert result == [
        os.path.abspath(primary),
        os.path.abspath(tmp_path / "tmp" / "astrachat-settings.json"),
    ]
    other = st._settings_file_fallbacks(str(tmp_path / "x" / "s.json"))
    assert len(other) == 3 and len(set(other)) == 3


# ------------------------------------------------------------ load_app_settings


def test_load_settings_missing_file_returns_current_values(load_state):
    st = load_state()
    result = st.load_app_settings()
    assert result["transcription_engine"] == "whisperx"
    assert result["rag_strategy"] == "auto"
    assert result["current_model_path"] is None


def test_load_settings_corrupt_json_returns_current_values(load_state):
    st = load_state(raw_settings="{not json")
    assert st.load_app_settings()["rag_strategy"] == "auto"


def test_load_settings_full_file(load_state):
    data = {
        "transcription_engine": "vosk",
        "transcription_language": "en",
        "memory_max_messages": 7,
        "memory_include_system_prompts": False,
        "memory_clear_on_restart": True,
        "rag_strategy": "hybrid",
        "agentic_rag_enabled": 0,
        "agentic_max_iterations": 99,
        "rag_query_fix_typos": 1,
        "rag_multi_query_enabled": 1,
        "rag_hyde_enabled": 1,
        "rag_chat_top_k": 500,
        "rag_chunking_strategy": " Fixed ",
        "rag_chunk_overlap": 5000,
        "rag_chunk_size": 5,
        "rag_similarity_threshold": 3,
        "rag_reranking_enabled": 0,
        "rag_rerank_top_n": 0,
        "rag_system_prompt": "  prompt  ",
        "rag_embedding_model_path": " emb ",
        "rag_reranker_model_path": None,
    }
    st = load_state(settings=data)
    assert st.load_app_settings()["rag_strategy"] == "hybrid"
    assert st.current_transcription_engine == "whisperx"  # vosk заменён
    assert st.current_transcription_language == "en"
    assert st.memory_max_messages == 7
    assert st.memory_include_system_prompts is False
    assert st.memory_clear_on_restart is True
    assert st.agentic_rag_enabled is False
    assert st.agentic_max_iterations == 5
    assert (st.rag_query_fix_typos, st.rag_multi_query_enabled, st.rag_hyde_enabled) == (True, True, True)
    assert st.rag_chat_top_k == 64
    assert st.rag_chunking_strategy == "fixed"
    assert st.rag_chunk_overlap == 2000
    assert st.rag_chunk_size == 200
    assert st.rag_similarity_threshold == 1.0
    assert st.rag_reranking_enabled is False
    assert st.rag_rerank_top_n == 1
    assert st.rag_system_prompt == "prompt"
    assert st.rag_embedding_model_path == "emb"
    assert st.rag_reranker_model_path == ""


def test_load_settings_invalid_numbers_keep_previous_values(load_state):
    st = load_state(
        settings={
            "agentic_max_iterations": "many",
            "rag_chat_top_k": "x",
            "rag_chunk_overlap": "x",
            "rag_chunk_size": "x",
            "rag_similarity_threshold": "x",
            "rag_rerank_top_n": "x",
            "rag_chunking_strategy": "unknown-strategy",
        }
    )
    assert st.agentic_max_iterations == 2
    assert st.rag_chat_top_k == 12
    assert st.rag_chunk_overlap == 200
    assert st.rag_chunk_size == 1000
    assert st.rag_similarity_threshold == 0.0
    assert st.rag_rerank_top_n == 12
    assert st.rag_chunking_strategy == "hierarchical"


@pytest.mark.parametrize(("legacy", "modern"), [("reranking", "hybrid"), ("standard", "vector")])
def test_load_settings_migrates_legacy_rag_strategy(load_state, legacy, modern):
    st = load_state(settings={"rag_strategy": legacy})
    assert st.current_rag_strategy == modern
    with open(st.SETTINGS_FILE, encoding="utf-8") as f:
        assert json.load(f)["rag_strategy"] == modern


# ------------------------------------------------------------ save_app_settings


def test_save_settings_merges_into_existing_file(load_state):
    st = load_state(settings={"a": 1, "b": 2})
    assert st.save_app_settings({"b": 3, "c": "я"}) is True
    with open(st.SETTINGS_FILE, encoding="utf-8") as f:
        assert json.load(f) == {"a": 1, "b": 3, "c": "я"}


def test_save_settings_creates_missing_file_and_dirs(load_state, tmp_path):
    st = load_state()
    st.SETTINGS_FILE = str(tmp_path / "deep" / "dir" / "s.json")
    assert st.save_app_settings({"k": 1}) is True
    assert json.loads(Path(st.SETTINGS_FILE).read_text(encoding="utf-8")) == {"k": 1}


def test_save_settings_switches_to_fallback_path(load_state, monkeypatch, tmp_path):
    st = load_state()
    blocker = tmp_path / "blocker"
    blocker.write_text("файл вместо каталога", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(st.tempfile, "gettempdir", lambda: str(tmp_path / "tmp"))
    st.SETTINGS_FILE = str(blocker / "settings.json")  # makedirs/open на таком пути падает
    assert st.save_app_settings({"x": 1}) is True
    expected = os.path.abspath(tmp_path / "tmp" / "astrachat-settings.json")
    assert st.SETTINGS_FILE == expected
    assert json.loads(Path(expected).read_text(encoding="utf-8")) == {"x": 1}


def test_save_settings_all_paths_fail_returns_false(load_state, monkeypatch):
    st = load_state()

    def _boom(*a, **k):
        raise PermissionError("read-only")

    monkeypatch.setattr(st, "open", _boom, raising=False)
    assert st.save_app_settings({"x": 1}) is False


def test_save_settings_corrupt_existing_file_returns_false(load_state):
    st = load_state(raw_settings="oops")
    assert st.save_app_settings({"x": 1}) is False


# --------------------------------------------------------- get_current_model_path


def _model_state(load_state, provider="", model="", info=None, raises=False):
    st = load_state()
    st.settings = types.SimpleNamespace(
        default_llm_provider=provider, llm_service=types.SimpleNamespace(default_model=model)
    )

    def _info():
        if raises:
            raise RuntimeError("boom")
        return info

    st.get_model_info = _info
    return st


def test_current_model_path_plain(load_state):
    assert _model_state(load_state, info={"path": "/models/a.gguf"}).get_current_model_path() == "/models/a.gguf"


@pytest.mark.parametrize("alias", ["llm-svc", "LLM-SVC://", "local", "default"])
def test_current_model_path_alias_resolves_to_default_model(load_state, alias):
    st = _model_state(load_state, "ollama", "llama3", info={"path": alias})
    assert st.get_current_model_path() == "ollama/llama3"


def test_current_model_path_alias_without_default_is_none(load_state):
    assert _model_state(load_state, info={"path": "local"}).get_current_model_path() is None


def test_current_model_path_empty_path_is_none(load_state):
    assert _model_state(load_state, info={"path": "  "}).get_current_model_path() is None


def test_current_model_path_from_saved_settings(load_state):
    st = _model_state(load_state, info=None)
    st.load_app_settings = lambda: {"current_model_path": "saved/model"}
    assert st.get_current_model_path() == "saved/model"


def test_current_model_path_without_model_info_function(load_state):
    st = _model_state(load_state)
    st.get_model_info = None
    st.load_app_settings = lambda: {}
    assert st.get_current_model_path() is None


def test_current_model_path_error_is_swallowed(load_state):
    assert _model_state(load_state, raises=True).get_current_model_path() is None
