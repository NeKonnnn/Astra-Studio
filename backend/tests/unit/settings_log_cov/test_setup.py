"""Тесты backend/settings/logging/setup.py: уровни, фильтры, настройка логгеров."""

import io
import logging
import logging.config
from logging.handlers import RotatingFileHandler
from types import SimpleNamespace

import pytest

from backend.settings.logging import setup

_MANAGED = (
    setup.BACKEND_LOGGER_NAME,
    *setup._UVICORN_LOGGERS,
    *setup._NOISY_LOGGERS,
    "engineio.server",
    "socketio.server",
    "backend.cov_test.child",
)


@pytest.fixture(autouse=True)
def restore_logging():
    """Снимок и восстановление конфигурации логгеров и флага _configured."""
    root = logging.getLogger()
    saved = {}
    for name in _MANAGED:
        lg = logging.getLogger(name)
        saved[name] = (list(lg.handlers), list(lg.filters), lg.level, lg.propagate, lg.disabled)
    root_state = (list(root.handlers), list(root.filters), root.level)
    configured = setup._configured
    yield
    for name, (handlers, filters, level, propagate, disabled) in saved.items():
        lg = logging.getLogger(name)
        lg.handlers[:] = handlers
        lg.filters[:] = filters
        lg.setLevel(level)
        lg.propagate = propagate
        lg.disabled = disabled
    root.handlers[:] = root_state[0]
    root.filters[:] = root_state[1]
    root.setLevel(root_state[2])
    setup._configured = configured


@pytest.fixture
def clean_env(monkeypatch):
    for var in (
        "APP_LOG_LEVEL",
        "astrachat_LOG_LEVEL",
        "ASTRACHAT_LOG_LEVEL",
        "LOG_LEVEL",
        "BACKEND_LOG_NOISY_LEVEL",
        "NOISY_DEBUG_LOGS_HIDE",
        "BACKEND_LOG_QUIET_PATHS",
        "BACKEND_LOG_QUIET_SUFFIXES",
        "BACKEND_LOG_FILE",
        "BACKEND_LOG_MAX_SIZE",
        "BACKEND_LOG_BACKUP_COUNT",
    ):
        monkeypatch.delenv(var, raising=False)


def _rec(name="x", level=logging.INFO, msg="m", args=None):
    return logging.LogRecord(name, level, __file__, 1, msg, args, None)


# ---------------- NoisyInfoToDebugFilter ----------------


def test_noisy_filter_passes_regular_logger(clean_env):
    assert setup.NoisyInfoToDebugFilter().filter(_rec("backend.app")) is True


def test_noisy_filter_non_info_record_untouched(clean_env):
    rec = _rec("httpx", logging.WARNING)
    assert setup.NoisyInfoToDebugFilter().filter(rec) is True
    assert rec.levelno == logging.WARNING


def test_noisy_filter_hides_info_when_flag_on_by_default(clean_env):
    assert setup.NoisyInfoToDebugFilter().filter(_rec("uvicorn.access")) is False


def test_noisy_filter_downgrades_info_to_debug_when_flag_off(clean_env, monkeypatch):
    monkeypatch.setenv("NOISY_DEBUG_LOGS_HIDE", "false")
    rec = _rec("httpx.client")  # подлогер тоже считается шумным
    assert setup.NoisyInfoToDebugFilter().filter(rec) is True
    assert (rec.levelno, rec.levelname) == (logging.DEBUG, "DEBUG")


# ---------------- quiet paths / suffixes ----------------


def test_quiet_paths_default_and_env_override(clean_env, monkeypatch):
    assert setup._quiet_paths() == setup._DEFAULT_QUIET_PATHS
    monkeypatch.setenv("BACKEND_LOG_QUIET_PATHS", " /a , ,/b ")
    assert setup._quiet_paths() == ("/a", "/b")
    monkeypatch.setenv("BACKEND_LOG_QUIET_PATHS", "")
    assert setup._quiet_paths() == ()


def test_quiet_suffixes_default_and_env_override(clean_env, monkeypatch):
    assert setup._quiet_suffixes() == ("/documents",)
    monkeypatch.setenv("BACKEND_LOG_QUIET_SUFFIXES", "/x, /y")
    assert setup._quiet_suffixes() == ("/x", "/y")


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("GET", "/health", True),
        ("get", "/api/llm/status?x=1", True),
        ("HEAD", "/readyz", True),
        ("GET", "/api/chats/5/documents", True),
        ("POST", "/api/chats/5/documents", False),  # запись - не опрос
        ("POST", "/health", False),
        (None, "/health", False),  # метод неизвестен - пишем строку
        ("GET", "", False),
        ("GET", None, False),
        ("GET", "/api/other", False),
    ],
)
def test_is_quiet_path(clean_env, method, path, expected):
    assert setup.is_quiet_path(method, path) is expected


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (200, True),
        ("204", True),
        (299, True),
        (500, False),
        (404, False),
        (199, False),
        (300, False),
        (None, False),
        ("abc", False),
        (object(), False),
    ],
)
def test_is_quiet_access_only_for_2xx(clean_env, status, expected):
    assert setup.is_quiet_access("GET", "/health", status) is expected


def test_is_quiet_access_non_quiet_path_even_if_2xx(clean_env):
    assert setup.is_quiet_access("GET", "/api/other", 200) is False


# ---------------- _access_method_path_status ----------------


def test_access_method_path_status_parses_uvicorn_args():
    rec = _rec("uvicorn.access", args=("1.2.3.4:5", "GET", "/p?q=1", "1.1", 200))
    assert setup._access_method_path_status(rec) == ("GET", "/p", 200)


def test_access_method_path_status_bad_status_becomes_none():
    rec = _rec("uvicorn.access", args=("c", "GET", "/p", "1.1", "oops"))
    assert setup._access_method_path_status(rec) == ("GET", "/p", None)


def test_access_method_path_status_non_str_method():
    rec = _rec("uvicorn.access", args=("c", None, "/p", "1.1", 200))
    assert setup._access_method_path_status(rec) == (None, "/p", 200)


def test_access_method_path_status_unknown_formats():
    assert setup._access_method_path_status(_rec(args=("a", "b"))) == (None, None, None)
    assert setup._access_method_path_status(_rec(msg="x")) == (None, None, None)
    non_str_path = _rec(args=("c", "GET", 5, "1.1", 200))
    assert setup._access_method_path_status(non_str_path) == (None, None, None)


# ---------------- QuietProbeAccessFilter ----------------


def test_quiet_probe_filter_hides_successful_probe(clean_env):
    rec = _rec("uvicorn.access", args=("c", "GET", "/health", "1.1", 200))
    assert setup.QuietProbeAccessFilter().filter(rec) is False


def test_quiet_probe_filter_keeps_failed_probe(clean_env):
    rec = _rec("uvicorn.access", args=("c", "GET", "/health", "1.1", 503))
    assert setup.QuietProbeAccessFilter().filter(rec) is True


def test_quiet_probe_filter_keeps_warning_even_for_probe(clean_env):
    rec = _rec("uvicorn.access", logging.WARNING, args=("c", "GET", "/health", "1.1", 200))
    assert setup.QuietProbeAccessFilter().filter(rec) is True


def test_quiet_probe_filter_keeps_unparsed_record(clean_env):
    assert setup.QuietProbeAccessFilter().filter(_rec("uvicorn.access", msg="free text")) is True


# ---------------- UvicornAccessRedactionFilter ----------------

_R = setup.UvicornAccessRedactionFilter


def test_redact_query_without_query_unchanged():
    assert _R._redact_query("/path") == "/path"
    assert _R._redact_query("/path?") == "/path?"


def test_redact_query_masks_sensitive_keys_and_jwt_values():
    jwt = "aaa.bbb.ccc"
    out = _R._redact_query(f"/cb?code=abc&TOKEN=zzz&page=2&x={jwt}&empty=")
    assert "code=%3Credacted%3E" in out
    assert "TOKEN=%3Credacted%3E" in out
    assert "x=%3Credacted%3E" in out
    assert "page=2" in out
    assert "empty=" in out
    assert "abc" not in out and jwt not in out


def test_redact_query_parse_failure_masks_whole_query(monkeypatch):
    def boom(*a, **k):
        raise ValueError("x")

    monkeypatch.setattr(setup, "parse_qsl", boom)
    assert _R._redact_query("/p?secret=1") == "/p?<redacted>"


def test_sanitize_text_redacts_request_line():
    text = '1.1.1.1 - "GET /cb?access_token=s3cr3t HTTP/1.1" 200'
    out = _R()._sanitize_text(text)
    assert "s3cr3t" not in out
    assert "GET /cb?access_token=%3Credacted%3E HTTP/1.1" in out


def test_redaction_filter_ignores_other_loggers():
    rec = _rec("backend.x", msg="GET /a?token=1 HTTP/1.1")
    assert _R().filter(rec) is True
    assert rec.msg == "GET /a?token=1 HTTP/1.1"


def test_redaction_filter_redacts_tuple_args():
    rec = _rec("uvicorn.access", args=("c", "GET", "/cb?state=abc&ok=1", "1.1", 200))
    assert _R().filter(rec) is True
    assert "abc" not in rec.args[2]
    assert "ok=1" in rec.args[2]
    assert rec.args[4] == 200  # нестроковые аргументы не трогаются


def test_redaction_filter_redacts_msg_when_no_tuple_args():
    rec = _rec("uvicorn.access", msg='"GET /cb?code=zz HTTP/1.1" 200')
    rec.args = None
    assert _R().filter(rec) is True
    assert "zz" not in rec.msg


def test_redaction_filter_non_str_msg_without_args_is_noop():
    rec = _rec("uvicorn.access", msg="x")
    rec.args = None
    rec.msg = 123
    assert _R().filter(rec) is True
    assert rec.msg == 123


def test_redaction_filter_swallows_internal_errors(monkeypatch):
    monkeypatch.setattr(_R, "_sanitize_text", lambda self, t: 1 / 0)
    rec = _rec("uvicorn.access", args=("a",))
    assert _R().filter(rec) is True


# ---------------- SocketPacketLogFilter ----------------

_PKT_MSG = "%s: Sending packet %s data %s"


def _engineio(text, name="engineio.server"):
    return _rec(name, logging.INFO, _PKT_MSG, ("sid", "MESSAGE", text))


def test_socket_filter_no_args_passes():
    assert setup.SocketPacketLogFilter().filter(_rec("engineio.server", msg="x")) is True
    rec = _rec("engineio.server")
    rec.args = ()
    assert setup.SocketPacketLogFilter().filter(rec) is True


def test_socket_filter_drops_stream_events_of_socketio_server():
    f = setup.SocketPacketLogFilter()
    drop = _rec("socketio.server", msg="emitting event %s", args=("chat_chunk",))
    keep = _rec("socketio.server", msg="emitting event %s", args=("chat_complete",))
    assert f.filter(drop) is False
    assert f.filter(keep) is True


def test_socket_filter_other_logger_or_arg_count_passes():
    f = setup.SocketPacketLogFilter()
    assert f.filter(_rec("other", args=("a",))) is True
    assert f.filter(_rec("engineio.server", msg=_PKT_MSG, args=("a", "b"))) is True


def test_socket_filter_other_engineio_message_passes():
    rec = _rec("engineio.server", msg="другое %s %s %s", args=("a", "b", "c"))
    assert setup.SocketPacketLogFilter().filter(rec) is True


def test_socket_filter_drops_chunk_packets():
    f = setup.SocketPacketLogFilter()
    assert f.filter(_engineio('2["chat_chunk",{"a":1}]')) is False
    assert f.filter(_engineio('2/ns,17["multi_llm_chunk",{}]')) is False


def test_socket_filter_short_packet_passes_unchanged():
    rec = _engineio('2["chat_complete",{}]')
    assert setup.SocketPacketLogFilter().filter(rec) is True
    assert rec.args[2] == '2["chat_complete",{}]'


def test_socket_filter_truncates_long_packet_to_event_and_size():
    text = '2["chat_complete",{"t":"' + "я" * 400 + '"}]'
    rec = _engineio(text)
    assert setup.SocketPacketLogFilter().filter(rec) is True
    assert rec.args == ("sid", "MESSAGE", "chat_complete", len(text))
    assert "симв." in rec.msg
    assert "я" * 50 not in rec.getMessage()


def test_socket_filter_long_non_event_packet_uses_data_label_and_non_str_arg():
    class Big:
        def __str__(self):
            return "x" * 500

    rec = _rec("engineio.server", msg=_PKT_MSG, args=("sid", "MESSAGE", Big()))
    assert setup.SocketPacketLogFilter().filter(rec) is True
    assert rec.args[2:] == ("data", 500)


def test_socket_filter_swallows_errors():
    class Evil:
        def __str__(self):
            raise RuntimeError("no")

    rec = _rec("engineio.server", msg=_PKT_MSG, args=("sid", "M", Evil()))
    assert setup.SocketPacketLogFilter().filter(rec) is True


def test_install_socket_packet_filter_is_idempotent():
    for name in ("engineio.server", "socketio.server"):
        logging.getLogger(name).filters[:] = []
    setup.install_socket_packet_filter()
    setup.install_socket_packet_filter()
    for name in ("engineio.server", "socketio.server"):
        found = [
            f for f in logging.getLogger(name).filters if isinstance(f, setup.SocketPacketLogFilter)
        ]
        assert len(found) == 1


# ---------------- env helpers / уровни ----------------


@pytest.mark.parametrize(
    ("raw", "default", "expected"),
    [
        (None, True, True),
        (None, False, False),
        ("1", False, True),
        (" Yes ", False, True),
        ("on", False, True),
        ("true", False, True),
        ("0", True, False),
        ("no", True, False),
        ("", True, False),
    ],
)
def test_env_bool(monkeypatch, raw, default, expected):
    if raw is None:
        monkeypatch.delenv("X_FLAG", raising=False)
    else:
        monkeypatch.setenv("X_FLAG", raw)
    assert setup._env_bool("X_FLAG", default) is expected


def test_hide_noisy_flag_defaults_to_true(clean_env, monkeypatch):
    assert setup._hide_noisy_debug_logs() is True
    monkeypatch.setenv("NOISY_DEBUG_LOGS_HIDE", "0")
    assert setup._hide_noisy_debug_logs() is False


def test_debug_gate_does_not_replace_debug_with_info():
    assert setup._apply_debug_gate_to_level(logging.DEBUG) == logging.DEBUG
    assert setup._apply_debug_gate_to_level_name("debug") == "DEBUG"
    assert setup._apply_debug_gate_to_level_name("") == "INFO"
    assert setup._apply_debug_gate_to_level_name(None) == "INFO"


def test_level_name_env_priority(clean_env, monkeypatch):
    assert setup._level_name() == "INFO"
    monkeypatch.setenv("LOG_LEVEL", "error")
    assert setup._level_name() == "ERROR"
    monkeypatch.setenv("ASTRACHAT_LOG_LEVEL", "warning")
    assert setup._level_name() == "WARNING"
    monkeypatch.setenv("astrachat_LOG_LEVEL", "debug")
    assert setup._level_name() == "DEBUG"
    monkeypatch.setenv("APP_LOG_LEVEL", "critical")
    assert setup._level_name() == "CRITICAL"


def test_resolve_level_unknown_falls_back_to_default(clean_env, monkeypatch):
    assert setup._resolve_level() == logging.INFO
    monkeypatch.setenv("APP_LOG_LEVEL", "debug")
    assert setup._resolve_level() == logging.DEBUG
    monkeypatch.setenv("APP_LOG_LEVEL", "ПУСТО")
    assert setup._resolve_level() == setup.DEFAULT_LEVEL


def test_noisy_level_name_priorities(clean_env, monkeypatch):
    assert setup._noisy_level_name() == "WARNING"  # флаг hide включён по умолчанию
    monkeypatch.setenv("NOISY_DEBUG_LOGS_HIDE", "false")
    monkeypatch.setenv("APP_LOG_LEVEL", "debug")
    assert setup._noisy_level_name() == "DEBUG"  # как уровень наших логгеров
    monkeypatch.setenv("BACKEND_LOG_NOISY_LEVEL", " error ")
    assert setup._noisy_level_name() == "ERROR"  # явная переменная главнее


# ---------------- UTF-8 стримы ----------------


def test_ensure_utf8_stream_reconfigures_when_supported():
    calls = []
    stream = SimpleNamespace(reconfigure=lambda **kw: calls.append(kw))
    setup._ensure_utf8_stream(stream)
    assert calls == [{"encoding": "utf-8"}]


def test_ensure_utf8_stream_ignores_streams_without_reconfigure():
    setup._ensure_utf8_stream(io.StringIO())  # не падает


def test_reconfigure_backend_handler_streams_utf8():
    calls = []
    lg = logging.getLogger(setup.BACKEND_LOGGER_NAME)
    with_stream = SimpleNamespace(
        stream=SimpleNamespace(reconfigure=lambda **kw: calls.append(kw))
    )
    no_stream = logging.NullHandler()
    lg.handlers[:] = [with_stream, no_stream]
    setup.reconfigure_backend_handler_streams_utf8()
    assert calls == [{"encoding": "utf-8"}]


# ---------------- _attach_backend_handler ----------------


def test_attach_backend_handler_adds_stdout_handler_with_filters(clean_env):
    lg = logging.getLogger("uvicorn.access")
    lg.handlers.clear()
    fmt = logging.Formatter("%(message)s")
    setup._attach_backend_handler(
        lg, fmt, logging.DEBUG, add_noisy_filter=True, add_uvicorn_access_redaction_filter=True
    )
    assert lg.level == logging.DEBUG and lg.propagate is False
    (handler,) = lg.handlers
    assert handler.formatter is fmt and handler.level == logging.DEBUG
    kinds = {type(f) for f in handler.filters}
    assert kinds == {
        setup.NoisyInfoToDebugFilter,
        setup.UvicornAccessRedactionFilter,
        setup.QuietProbeAccessFilter,
    }


def test_attach_backend_handler_no_duplicate_without_clear(clean_env):
    lg = logging.getLogger("uvicorn")
    lg.handlers.clear()
    fmt = logging.Formatter("%(message)s")
    setup._attach_backend_handler(lg, fmt, logging.INFO)
    setup._attach_backend_handler(lg, fmt, logging.WARNING)
    assert len(lg.handlers) == 1
    assert lg.level == logging.WARNING
    assert lg.handlers[0].filters == []
    first = lg.handlers[0]
    setup._attach_backend_handler(lg, fmt, logging.INFO, clear_handlers=True)
    assert len(lg.handlers) == 1 and lg.handlers[0] is not first


def test_configure_uvicorn_logging_sets_loggers(clean_env, monkeypatch):
    monkeypatch.setenv("APP_LOG_LEVEL", "warning")
    for name in setup._UVICORN_LOGGERS:
        logging.getLogger(name).handlers.clear()
    setup.configure_uvicorn_logging(force=True)
    for name in setup._UVICORN_LOGGERS:
        lg = logging.getLogger(name)
        assert lg.level == logging.WARNING
        assert len(lg.handlers) == 1
        has_redaction = any(
            isinstance(f, setup.UvicornAccessRedactionFilter) for f in lg.handlers[0].filters
        )
        assert has_redaction is (name == "uvicorn.access")


# ---------------- get_uvicorn_log_config ----------------


def test_get_uvicorn_log_config_structure(clean_env, monkeypatch):
    monkeypatch.setenv("APP_LOG_LEVEL", "debug")
    monkeypatch.setenv("BACKEND_LOG_NOISY_LEVEL", "error")
    cfg = setup.get_uvicorn_log_config()
    assert cfg["version"] == 1 and cfg["disable_existing_loggers"] is False
    assert cfg["formatters"]["backend"]["format"] == setup.LOG_FORMAT
    assert cfg["root"]["level"] == "DEBUG"
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", setup.BACKEND_LOGGER_NAME):
        assert cfg["loggers"][name]["level"] == "DEBUG"
        assert cfg["loggers"][name]["propagate"] is False
    for noisy in setup._NOISY_LOGGERS:
        assert cfg["loggers"][noisy] == {"level": "ERROR", "propagate": True}
    # фабрики фильтров должны разрешаться по указанным путям
    for spec in cfg["filters"].values():
        mod, _, cls = spec["()"].rpartition(".")
        assert hasattr(__import__(mod, fromlist=[cls]), cls)


def test_get_uvicorn_log_config_is_accepted_by_dictconfig(clean_env):
    # состояние логгеров вернёт фикстура restore_logging
    logging.config.dictConfig(setup.get_uvicorn_log_config())
    assert any(
        isinstance(f, setup.QuietProbeAccessFilter)
        for h in logging.getLogger("uvicorn.access").handlers
        for f in h.filters
    )


# ---------------- configure_logging ----------------


def test_configure_logging_is_noop_when_already_configured(clean_env):
    setup._configured = True
    lg = logging.getLogger(setup.BACKEND_LOGGER_NAME)
    marker = logging.NullHandler()
    lg.handlers[:] = [marker]
    setup.configure_logging()
    assert lg.handlers == [marker]


def test_configure_logging_force_builds_stdout_and_root_handlers(clean_env, monkeypatch):
    monkeypatch.setenv("APP_LOG_LEVEL", "debug")
    monkeypatch.setenv("NOISY_DEBUG_LOGS_HIDE", "true")
    setup.configure_logging(force=True)
    backend = logging.getLogger(setup.BACKEND_LOGGER_NAME)
    assert backend.level == logging.DEBUG and backend.propagate is False
    assert len(backend.handlers) == 1
    assert backend.handlers[0].level == logging.DEBUG
    for noisy in setup._NOISY_LOGGERS:
        assert logging.getLogger(noisy).level == logging.WARNING
    root = logging.getLogger()
    assert root.level == logging.WARNING
    marked = [h for h in root.handlers if getattr(h, "_astra_backend_root", False)]
    assert len(marked) == 1
    assert any(isinstance(f, setup.UvicornAccessRedactionFilter) for f in marked[0].filters)
    assert setup._configured is True
    # повторный принудительный вызов не плодит handler'ы
    setup.configure_logging(force=True)
    assert len([h for h in root.handlers if getattr(h, "_astra_backend_root", False)]) == 1
    assert len(backend.handlers) == 1


def test_configure_logging_non_force_keeps_existing_root_handler(clean_env):
    setup.configure_logging(force=True)
    setup._configured = False
    root = logging.getLogger()
    before = [h for h in root.handlers if getattr(h, "_astra_backend_root", False)]
    setup.configure_logging()
    after = [h for h in root.handlers if getattr(h, "_astra_backend_root", False)]
    assert before == after and len(after) == 1


def test_configure_logging_writes_to_rotating_file(clean_env, monkeypatch, tmp_path):
    log_file = tmp_path / "app.log"
    monkeypatch.setenv("BACKEND_LOG_FILE", "app.log")
    monkeypatch.setenv("BACKEND_LOG_MAX_SIZE", "2048")
    monkeypatch.setenv("BACKEND_LOG_BACKUP_COUNT", "2")
    monkeypatch.setattr(setup, "resolve_log_file_path", lambda raw: log_file)
    setup.configure_logging(force=True)
    backend = logging.getLogger(setup.BACKEND_LOGGER_NAME)
    file_handlers = [h for h in backend.handlers if isinstance(h, RotatingFileHandler)]
    assert len(file_handlers) == 1
    assert (file_handlers[0].maxBytes, file_handlers[0].backupCount) == (2048, 2)
    backend.info("запись в файл")
    file_handlers[0].flush()
    file_handlers[0].close()
    assert "запись в файл" in log_file.read_text(encoding="utf-8")


def test_configure_logging_rejected_log_file_warns_and_uses_stdout(
    clean_env, monkeypatch, capsys
):
    monkeypatch.setenv("BACKEND_LOG_FILE", "evil\nINJECTED")
    monkeypatch.setattr(setup, "resolve_log_file_path", lambda raw: None)
    setup.configure_logging(force=True)
    backend = logging.getLogger(setup.BACKEND_LOGGER_NAME)
    assert len(backend.handlers) == 1  # только stdout
    out = capsys.readouterr().out
    assert "BACKEND_LOG_FILE недоступен или отклонён" in out
    # перевод строки из значения env не должен порождать отдельную строку лога
    assert "evil INJECTED" in out
    assert "\nINJECTED" not in out


def test_configure_logging_file_open_error_warns(clean_env, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("BACKEND_LOG_FILE", "x.log")
    monkeypatch.setattr(setup, "resolve_log_file_path", lambda raw: tmp_path / "x.log")

    def boom(*a, **k):
        raise OSError("нет доступа")

    monkeypatch.setattr(setup, "RotatingFileHandler", boom)
    setup.configure_logging(force=True)
    backend = logging.getLogger(setup.BACKEND_LOGGER_NAME)
    assert len(backend.handlers) == 1
    out = capsys.readouterr().out
    assert "Не удалось открыть BACKEND_LOG_FILE" in out
    assert "нет доступа" in out


# ---------------- get_logger ----------------


def test_get_logger_configures_on_first_use(clean_env, monkeypatch):
    calls = []
    monkeypatch.setattr(setup, "configure_logging", lambda **kw: calls.append(kw))
    monkeypatch.setattr(setup, "_configured", False)
    setup.get_logger("x")
    assert calls == [{}]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (None, "backend"),
        ("", "backend"),
        ("Backend", "backend"),
        (" backend ", "backend"),
        ("backend.cov_test.child", "backend.cov_test.child"),
        ("backendx", "backendx"),
        ("my.module", "backend.my.module"),
        (".rel", "backend.rel"),
    ],
)
def test_get_logger_name_normalisation(monkeypatch, name, expected):
    monkeypatch.setattr(setup, "_configured", True)
    assert setup.get_logger(name).name == expected
