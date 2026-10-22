"""core.logging: setup (уровни, фильтр тихих проб, dictConfig), errors (run_guarded, guarded, logged_suppress), llm_trace."""

from __future__ import annotations

import logging
import logging.config
from types import SimpleNamespace

import pytest

from app.core.logging import errors, llm_trace, setup
from app.core import logging as core_logging

pytestmark = pytest.mark.unit

_TOUCHED = ("uvicorn", "uvicorn.error", "uvicorn.access", "app", "", *setup._NOISY_LOGGERS)


@pytest.fixture
def restore_logging(monkeypatch):
    """dictConfig меняет глобальные логгеры: запоминаем и возвращаем как было."""
    saved = {}
    for name in _TOUCHED:
        lg = logging.getLogger(name)
        saved[name] = (list(lg.handlers), lg.level, lg.propagate, list(lg.filters))
    monkeypatch.setattr(setup, "_configured", setup._configured)
    yield
    for name, (handlers, level, propagate, filters) in saved.items():
        lg = logging.getLogger(name)
        lg.handlers[:] = handlers
        lg.setLevel(level)
        lg.propagate = propagate
        lg.filters[:] = filters


@pytest.fixture
def no_log_env(monkeypatch):
    for key in ("APP_LOG_LEVEL", "SVC_RAG_LOG_LEVEL", "RAG_LOG_LEVEL", "LOG_LEVEL", "RAG_LOG_NOISY_LEVEL",
                "RAG_LOG_QUIET_PATHS", "RAG_LOG_QUIET_SUFFIXES"):
        monkeypatch.delenv(key, raising=False)


# ───────────────────────────── setup: тихие пробы ─────────────────────────────


class TestQuietPaths:
    def test_defaults_without_env(self, no_log_env):
        assert setup._quiet_paths() == setup._DEFAULT_QUIET_PATHS
        assert setup._quiet_suffixes() == setup._DEFAULT_QUIET_SUFFIXES

    def test_env_lists_are_split_and_stripped(self, monkeypatch):
        monkeypatch.setenv("RAG_LOG_QUIET_PATHS", " /a , /b ,, ")
        monkeypatch.setenv("RAG_LOG_QUIET_SUFFIXES", "/x")

        assert setup._quiet_paths() == ("/a", "/b")
        assert setup._quiet_suffixes() == ("/x",)

    def test_empty_env_disables_filter(self, monkeypatch):
        monkeypatch.setenv("RAG_LOG_QUIET_PATHS", "")
        monkeypatch.setenv("RAG_LOG_QUIET_SUFFIXES", "")

        assert setup.is_quiet_path("GET", "/v1/health") is False
        assert setup.is_quiet_path("GET", "/v1/kb/documents") is False

    @pytest.mark.parametrize("method,path,expected", [
        ("GET", "/v1/health", True),
        ("get", "/v1/kb/reindex/status", True),
        ("HEAD", "/readyz", True),
        ("GET", "/v1/kb/documents", True),
        ("GET", "/v1/kb/documents?agent=1", True),
        ("POST", "/v1/kb/documents", False),
        ("POST", "/v1/health", False),
        (None, "/v1/health", False),
        ("GET", "/v1/kb/search", False),
        ("GET", "", False),
        ("GET", None, False),
    ])
    def test_is_quiet_path(self, no_log_env, method, path, expected):
        assert setup.is_quiet_path(method, path) is expected

    def test_probe_names_match_as_substring_known_issue(self, no_log_env):
        # Низкая: "/live", "/ready", "/health" ищутся подстрокой, а не по границе сегмента, поэтому
        # успешный GET проекта с id, начинающимся на live/ready, пропадает из access-лога.
        assert setup.is_quiet_path("GET", "/v1/project-rag/projects/live-demo/documents/5/chunks") is True
        assert setup.is_quiet_path("GET", "/v1/project-rag/projects/ready-set/search") is True

    @pytest.mark.parametrize("status,expected", [
        (200, True), (204, True), (299, True), (199, False), (301, False), (404, False), (500, False),
        (None, False), ("abc", False), ("200", True),
    ])
    def test_is_quiet_access_only_for_success(self, no_log_env, status, expected):
        assert setup.is_quiet_access("GET", "/v1/health", status) is expected

    def test_is_quiet_access_other_paths_stay_visible(self, no_log_env):
        assert setup.is_quiet_access("GET", "/v1/kb/search", 200) is False


def _access_record(args, level=logging.INFO):
    return logging.LogRecord("uvicorn.access", level, __file__, 1, '%s - "%s %s HTTP/%s" %d', args, None)


class TestAccessFilter:
    def test_extracts_method_path_status(self):
        rec = _access_record(("1.2.3.4:5", "GET", "/v1/health?x=1", "1.1", 200))

        assert setup._access_method_path_status(rec) == ("GET", "/v1/health", 200)

    def test_bad_status_becomes_none(self):
        rec = _access_record(("c", "GET", "/p", "1.1", "oops"))

        assert setup._access_method_path_status(rec) == ("GET", "/p", None)

    def test_non_string_method_becomes_none(self):
        rec = _access_record(("c", 5, "/p", "1.1", 200))

        assert setup._access_method_path_status(rec) == (None, "/p", 200)

    @pytest.mark.parametrize("args", [(), ("a", "b"), {"x": 1}, ("c", "GET", 123, "1.1", 200)])
    def test_unparsable_args_give_nones(self, args):
        rec = logging.LogRecord("x", logging.INFO, __file__, 1, "msg", None, None)
        rec.args = args

        assert setup._access_method_path_status(rec) == (None, None, None)

    def test_filter_hides_successful_probe(self, no_log_env):
        flt = setup.QuietProbeAccessFilter()

        assert flt.filter(_access_record(("c", "GET", "/v1/health", "1.1", 200))) is False

    def test_filter_keeps_failed_probe_other_paths_and_warnings(self, no_log_env):
        flt = setup.QuietProbeAccessFilter()

        assert flt.filter(_access_record(("c", "GET", "/v1/health", "1.1", 503))) is True
        assert flt.filter(_access_record(("c", "POST", "/v1/kb/search", "1.1", 200))) is True
        assert flt.filter(_access_record(("c", "GET", "/v1/health", "1.1", 200), level=logging.WARNING)) is True
        assert flt.filter(_access_record(("garbage",))) is True


# ───────────────────────────── setup: уровни и конфиг ─────────────────────────────


class TestLevels:
    @pytest.fixture(autouse=True)
    def env(self, no_log_env):
        pass

    def test_settings_logging_failure_gives_none(self, monkeypatch):
        import app.core.config as cfg

        def boom():
            raise RuntimeError("нет конфига")

        monkeypatch.setattr(cfg, "get_settings", boom)

        assert setup._settings_logging() is None

    def test_settings_logging_returns_section(self, monkeypatch):
        import app.core.config as cfg

        section = SimpleNamespace(level="debug")
        monkeypatch.setattr(cfg, "get_settings", lambda: SimpleNamespace(logging=section))

        assert setup._settings_logging() is section

    def test_env_priority_order(self, monkeypatch):
        monkeypatch.setattr(setup, "_settings_logging", lambda: SimpleNamespace(level="error"))
        monkeypatch.setenv("LOG_LEVEL", "warning")
        monkeypatch.setenv("RAG_LOG_LEVEL", "debug")
        assert setup._level_name() == "DEBUG"

        monkeypatch.setenv("APP_LOG_LEVEL", " critical ")
        assert setup._level_name() == "CRITICAL"

    def test_falls_back_to_yaml_then_default(self, monkeypatch):
        monkeypatch.setattr(setup, "_settings_logging", lambda: SimpleNamespace(level="warning"))
        assert setup._level_name() == "WARNING"

        monkeypatch.setattr(setup, "_settings_logging", lambda: None)
        assert setup._level_name() == "INFO"

    def test_noisy_level(self, monkeypatch):
        monkeypatch.setattr(setup, "_settings_logging", lambda: SimpleNamespace(noisy_level="error"))
        assert setup._noisy_level_name() == "ERROR"

        monkeypatch.setenv("RAG_LOG_NOISY_LEVEL", "debug")
        assert setup._noisy_level_name() == "DEBUG"

        monkeypatch.delenv("RAG_LOG_NOISY_LEVEL")
        monkeypatch.setattr(setup, "_settings_logging", lambda: None)
        assert setup._noisy_level_name() == "WARNING"

    def test_resolve_level_maps_name_and_defaults_for_unknown(self, monkeypatch):
        monkeypatch.setenv("APP_LOG_LEVEL", "debug")
        assert setup._resolve_level() == logging.DEBUG

        monkeypatch.setenv("APP_LOG_LEVEL", "громко")
        assert setup._resolve_level() == logging.INFO


class TestDictConfig:
    def test_structure(self, monkeypatch, no_log_env):
        monkeypatch.setattr(setup, "_settings_logging", lambda: None)
        monkeypatch.setenv("RAG_LOG_NOISY_LEVEL", "error")
        monkeypatch.setenv("APP_LOG_LEVEL", "debug")

        cfg = setup.get_uvicorn_log_config()

        assert cfg["version"] == 1 and cfg["disable_existing_loggers"] is False
        assert cfg["formatters"]["svc_rag"]["format"] == setup.LOG_FORMAT
        assert cfg["loggers"]["app"] == {"handlers": ["svc_rag_stdout"], "level": "DEBUG", "propagate": False}
        assert cfg["loggers"]["uvicorn.access"]["filters"] == ["quiet_probe_access"]
        assert cfg["loggers"]["httpx"] == {"level": "ERROR", "propagate": True}
        assert set(setup._NOISY_LOGGERS) <= set(cfg["loggers"])
        assert cfg["root"]["level"] == "DEBUG"

    def test_config_is_applicable_by_dictconfig(self, restore_logging, monkeypatch, no_log_env):
        monkeypatch.setattr(setup, "_settings_logging", lambda: None)

        logging.config.dictConfig(setup.get_uvicorn_log_config())

        assert logging.getLogger("uvicorn.access").filters
        assert isinstance(logging.getLogger("uvicorn.access").filters[0], setup.QuietProbeAccessFilter)


class TestConfigureAndGetLogger:
    def test_configure_is_idempotent_unless_forced(self, restore_logging, monkeypatch, no_log_env):
        monkeypatch.setattr(setup, "_settings_logging", lambda: None)
        calls = []
        monkeypatch.setattr(setup.logging.config, "dictConfig", lambda cfg: calls.append(cfg))
        monkeypatch.setattr(setup, "_configured", False)

        setup.configure_logging()
        setup.configure_logging()
        assert len(calls) == 1 and setup._configured is True

        setup.configure_logging(force=True)
        assert len(calls) == 2

    def test_get_logger_configures_lazily(self, restore_logging, monkeypatch):
        calls = []
        monkeypatch.setattr(setup, "_configured", False)
        monkeypatch.setattr(setup, "configure_logging", lambda **kw: calls.append(1))

        setup.get_logger("x")

        assert calls == [1]

    @pytest.mark.parametrize("name,expected", [
        (None, "app"), ("", "app"), ("app", "app"), ("App", "app"), (" app ", "app"),
        ("app.services.x", "app.services.x"), ("services.x", "app.services.x"), (".mod", "app.mod"),
        ("other", "app.other"),
    ])
    def test_get_logger_names(self, monkeypatch, name, expected):
        monkeypatch.setattr(setup, "_configured", True)

        assert setup.get_logger(name).name == expected

    def test_ensure_stdout_utf8_reconfigures_and_ignores_failures(self, monkeypatch):
        calls = []

        class Good:
            def reconfigure(self, **kw):
                calls.append(kw)

        class Bad:
            def reconfigure(self, **kw):
                raise ValueError("нельзя")

        monkeypatch.setattr(setup.sys, "stdout", Good())
        monkeypatch.setattr(setup.sys, "stderr", Bad())

        setup._ensure_stdout_utf8()

        assert calls == [{"encoding": "utf-8"}]

    def test_ensure_stdout_utf8_skips_streams_without_reconfigure(self, monkeypatch):
        monkeypatch.setattr(setup.sys, "stdout", object())
        monkeypatch.setattr(setup.sys, "stderr", object())

        setup._ensure_stdout_utf8()

    def test_package_reexports(self):
        assert core_logging.get_logger is setup.get_logger
        assert core_logging.run_guarded is errors.run_guarded
        assert core_logging.log_llm_failure is llm_trace.log_llm_failure
        assert set(core_logging.LEVEL_BY_NAME) == set(core_logging.LOG_MATRIX)


# ───────────────────────────── errors ─────────────────────────────


@pytest.fixture
def log(caplog):
    caplog.set_level(logging.DEBUG)
    lg = logging.getLogger("tests.errors")
    lg.propagate = True
    return lg


class TestRunGuarded:
    def test_returns_value_without_logging(self, log, caplog):
        assert errors.run_guarded(log, lambda: 42, message="m") == 42
        assert caplog.records == []

    def test_exception_returns_default_and_logs_traceback(self, log, caplog):
        def boom():
            raise ValueError("плохо")

        assert errors.run_guarded(log, boom, message="не вышло", default="d") == "d"
        rec = caplog.records[-1]
        assert rec.levelno == logging.ERROR and rec.getMessage() == "не вышло" and rec.exc_info

    def test_reraise(self, log):
        def boom():
            raise KeyError("k")

        with pytest.raises(KeyError):
            errors.run_guarded(log, boom, message="m", reraise=True)

    @pytest.mark.parametrize("level,expected", [
        ("exception", logging.ERROR), ("error", logging.ERROR), ("warning", logging.WARNING),
        ("debug", logging.DEBUG), ("странный", logging.ERROR),
    ])
    def test_levels(self, log, caplog, level, expected):
        def boom():
            raise ValueError("x")

        errors.run_guarded(log, boom, message="сообщение", level=level)

        rec = caplog.records[-1]
        assert rec.levelno == expected and "сообщение" in rec.getMessage() and rec.exc_info

    def test_base_exceptions_are_not_caught(self, log):
        def stop():
            raise KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            errors.run_guarded(log, stop, message="m")


class TestRunGuardedAsync:
    async def test_success(self, log):
        async def ok():
            return "v"

        assert await errors.run_guarded_async(log, ok, message="m") == "v"

    async def test_failure_returns_default_and_logs(self, log, caplog):
        async def boom():
            raise RuntimeError("async плохо")

        assert await errors.run_guarded_async(log, boom, message="упало", default=0, level="warning") == 0
        assert caplog.records[-1].levelno == logging.WARNING

    async def test_reraise(self, log):
        async def boom():
            raise RuntimeError("x")

        with pytest.raises(RuntimeError):
            await errors.run_guarded_async(log, boom, message="m", reraise=True)


class TestLoggedSuppress:
    def test_suppresses_exception_and_logs_debug(self, log, caplog):
        with errors.logged_suppress(log, "подавлено тестом"):
            raise ValueError("x")

        rec = caplog.records[-1]
        assert rec.levelno == logging.DEBUG and rec.getMessage() == "подавлено тестом" and rec.exc_info[0] is ValueError

    def test_no_exception_no_log(self, log, caplog):
        with errors.logged_suppress(log):
            pass

        assert caplog.records == []

    def test_default_message(self, log, caplog):
        with errors.logged_suppress(log):
            raise ValueError

        assert caplog.records[-1].getMessage() == "Подавлено исключение"

    def test_base_exception_passes_through(self, log):
        with pytest.raises(KeyboardInterrupt):
            with errors.logged_suppress(log):
                raise KeyboardInterrupt


class TestGuardedDecorator:
    def test_passes_through_result_and_keeps_metadata(self):
        @errors.guarded()
        def add(a, b=1):
            """doc"""
            return a + b

        assert add(2, b=3) == 5 and add.__name__ == "add" and add.__doc__ == "doc"

    def test_failure_returns_default_with_qualname_message(self, caplog):
        caplog.set_level(logging.DEBUG)

        @errors.guarded(default=-1)
        def broken():
            raise ValueError("x")

        assert broken() == -1
        assert any("Ошибка в" in r.getMessage() and "broken" in r.getMessage() for r in caplog.records)

    def test_custom_message_and_reraise(self, caplog):
        caplog.set_level(logging.DEBUG)

        @errors.guarded("своё сообщение", reraise=True, level="warning")
        def broken():
            raise ValueError("x")

        with pytest.raises(ValueError):
            broken()
        assert caplog.records[-1].getMessage() == "своё сообщение" and caplog.records[-1].levelno == logging.WARNING


# ───────────────────────────── llm_trace ─────────────────────────────


@pytest.fixture
def lg(caplog):
    caplog.set_level(logging.DEBUG)
    logger = logging.getLogger("tests.llm")
    logger.propagate = True
    return logger


def texts(caplog, level=None):
    return [r.getMessage() for r in caplog.records if level is None or r.levelno == level]


class TestLlmTrace:
    def test_request_id_is_short_unique_hex(self):
        a, b = llm_trace.new_request_id(), llm_trace.new_request_id()

        assert len(a) == 8 and int(a, 16) >= 0 and a != b

    def test_config_logs_info_with_auth_flag(self, lg, caplog):
        llm_trace.log_llm_config(lg, base_url="http://llm", model="m", timeout=30, auth_present=True)

        msgs = texts(caplog)
        assert msgs == ["[LLM cfg] base_url='http://llm' model='m' timeout=30s auth=YES"]

    def test_config_warns_on_empty_base_url(self, lg, caplog):
        llm_trace.log_llm_config(lg, base_url="", model="m", timeout=1, auth_present=False)

        assert "auth=NO" in texts(caplog, logging.INFO)[0]
        assert "ПУСТОЙ" in texts(caplog, logging.WARNING)[0]

    def test_request_debug_line(self, lg, caplog):
        llm_trace.log_llm_request(lg, request_id="r1", purpose="rerank", url="http://llm/v1/chat/completions",
                                  model="m", timeout=5, params={"t": 0}, auth_present=True)

        assert texts(caplog, logging.ERROR) == []
        line = texts(caplog, logging.DEBUG)[0]
        assert "rid=r1" in line and "purpose=rerank" in line and "POST http://llm/v1/chat/completions" in line
        assert "auth=YES" in line and "params={'t': 0}" in line

    @pytest.mark.parametrize("url", ["", "/v1/chat/completions"])
    def test_request_with_empty_host_is_config_error(self, lg, caplog, url):
        llm_trace.log_llm_request(lg, request_id="r2", purpose="p", url=url, model="m", timeout=5,
                                  params={}, auth_present=False)

        assert "ОШИБКА КОНФИГА" in texts(caplog, logging.ERROR)[0]
        assert "auth=NO" in texts(caplog, logging.DEBUG)[0]
        if not url:
            assert "<EMPTY base_url>" in texts(caplog, logging.DEBUG)[0]

    def test_response_ok(self, lg, caplog):
        llm_trace.log_llm_response(lg, request_id="r", purpose="p", status=200, latency_s=0.1234,
                                   content_chars=10, choices=1, finish_reason="stop")

        assert texts(caplog, logging.WARNING) == []
        assert "status=200" in texts(caplog)[0] and "latency=0.123s" in texts(caplog)[0] and "finish=stop" in texts(caplog)[0]

    def test_response_empty_content_warns(self, lg, caplog):
        llm_trace.log_llm_response(lg, request_id="r", purpose="p", status=200, latency_s=1, content_chars=0, choices=0)

        assert "ПУСТОЙ content" in texts(caplog, logging.WARNING)[0]

    def test_failure_truncates_body(self, lg, caplog):
        llm_trace.log_llm_failure(lg, request_id="r", purpose="p", url="u", latency_s=2, status=500,
                                  error_type="HTTPStatusError", reason="boom", body="x" * 2000)

        line = texts(caplog, logging.ERROR)[0]
        assert "status=500" in line and "error=HTTPStatusError" in line
        assert "x" * llm_trace._MAX_BODY_PREVIEW in line and "x" * (llm_trace._MAX_BODY_PREVIEW + 1) not in line
        assert len(texts(caplog, logging.ERROR)) == 1

    @pytest.mark.parametrize("status", [401, 403])
    def test_failure_with_auth_status_adds_hint(self, lg, caplog, status):
        llm_trace.log_llm_failure(lg, request_id="r", purpose="p", url="u", latency_s=0, status=status)

        errs = texts(caplog, logging.ERROR)
        assert len(errs) == 2 and "ОТКАЗ АВТОРИЗАЦИИ" in errs[1] and str(status) in errs[1]

    def test_failure_without_status_or_body(self, lg, caplog):
        llm_trace.log_llm_failure(lg, request_id="r", purpose="p", url="u", latency_s=0, error_type="Timeout")

        line = texts(caplog, logging.ERROR)[0]
        assert "status=None" in line and "body=''" in line
