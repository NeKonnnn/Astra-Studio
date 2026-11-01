"""Тесты backend/settings/logging/errors.py и repeat.py (защитные обёртки и «лог при изменении»)."""

import logging

import pytest

from backend.settings.logging import errors, guarded, logged_suppress, repeat
from backend.settings.logging import run_guarded, run_guarded_async


@pytest.fixture
def log():
    """Отдельный логгер с перехватом записей (без влияния на конфигурацию backend)."""
    logger = logging.getLogger("test_errors_cov")
    records: list[logging.LogRecord] = []

    class _H(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _H(level=logging.DEBUG)
    old_level, old_prop = logger.level, logger.propagate
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    logger.records = records
    yield logger
    logger.removeHandler(handler)
    logger.setLevel(old_level)
    logger.propagate = old_prop


# ---------------- _log_handled ----------------


@pytest.mark.parametrize(
    ("level", "expected_level"),
    [
        ("exception", logging.ERROR),
        ("error", logging.ERROR),
        ("warning", logging.WARNING),
        ("debug", logging.DEBUG),
        ("неизвестный", logging.ERROR),
    ],
)
def test_log_handled_levels(log, level, expected_level):
    try:
        raise ValueError("boom")
    except ValueError:
        errors._log_handled(log, "сообщение", level)
    assert len(log.records) == 1
    rec = log.records[0]
    assert rec.levelno == expected_level
    assert rec.getMessage() == "сообщение"
    assert rec.exc_info is not None


# ---------------- run_guarded ----------------


def test_run_guarded_success_returns_value(log):
    assert run_guarded(log, lambda: 42, message="m") == 42
    assert log.records == []


def test_run_guarded_returns_default_on_error_and_logs(log):
    def fail():
        raise RuntimeError("x")

    assert run_guarded(log, fail, message="упало", default="dflt") == "dflt"
    assert [r.getMessage() for r in log.records] == ["упало"]


def test_run_guarded_reraise(log):
    def fail():
        raise KeyError("k")

    with pytest.raises(KeyError):
        run_guarded(log, fail, message="m", reraise=True, level="warning")
    assert log.records[0].levelno == logging.WARNING


def test_run_guarded_does_not_catch_base_exception(log):
    def stop():
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_guarded(log, stop, message="m")


# ---------------- run_guarded_async ----------------


async def test_run_guarded_async_success(log):
    async def ok():
        return "v"

    assert await run_guarded_async(log, ok, message="m") == "v"


async def test_run_guarded_async_default_on_error(log):
    async def fail():
        raise ValueError("a")

    result = await run_guarded_async(log, fail, message="асинхронно", default=7)
    assert result == 7
    assert log.records[0].getMessage() == "асинхронно"


async def test_run_guarded_async_reraise(log):
    async def fail():
        raise ValueError("a")

    with pytest.raises(ValueError):
        await run_guarded_async(log, fail, message="m", reraise=True, level="debug")
    assert log.records[0].levelno == logging.DEBUG


# ---------------- logged_suppress ----------------


def test_logged_suppress_swallows_exception_and_logs_debug(log):
    with logged_suppress(log, "тихо"):
        raise OSError("disk")
    rec = log.records[0]
    assert rec.levelno == logging.DEBUG
    assert rec.getMessage() == "тихо"
    assert rec.exc_info[0] is OSError


def test_logged_suppress_no_error_logs_nothing(log):
    with logged_suppress(log) as ctx:
        assert ctx.message == "Подавлено исключение"
    assert log.records == []


def test_logged_suppress_does_not_swallow_base_exception(log):
    with pytest.raises(KeyboardInterrupt):
        with logged_suppress(log):
            raise KeyboardInterrupt
    assert log.records == []


# ---------------- guarded ----------------


def test_guarded_returns_result_and_keeps_metadata():
    @guarded("msg")
    def add(a, b):
        """doc"""
        return a + b

    assert add(1, 2) == 3
    assert add.__name__ == "add"
    assert add.__doc__ == "doc"


def test_guarded_default_message_contains_qualname(log, monkeypatch):
    monkeypatch.setattr("backend.settings.logging.get_logger", lambda name=None: log)

    @guarded(default=-1)
    def broken():
        raise ValueError("bad")

    assert broken() == -1
    # сообщение по умолчанию строится из qualname функции
    msg = log.records[0].getMessage()
    assert msg.startswith("Ошибка в ")
    assert "broken" in msg


def test_guarded_reraise(log, monkeypatch):
    monkeypatch.setattr("backend.settings.logging.get_logger", lambda name=None: log)

    @guarded("m", reraise=True, level="warning")
    def broken():
        raise ValueError("bad")

    with pytest.raises(ValueError):
        broken()
    assert log.records[0].levelno == logging.WARNING


# ---------------- repeat ----------------


@pytest.fixture(autouse=False)
def clean_repeat_state():
    repeat.reset_log_state()
    yield
    repeat.reset_log_state()


def test_log_on_change_first_call_logs_at_level_then_debug_on_repeat(log, clean_repeat_state):
    assert repeat.log_on_change(log, "k", ("a",), "msg %s", 1) is True
    assert repeat.log_on_change(log, "k", ("a",), "msg %s", 1) is False
    levels = [r.levelno for r in log.records]
    assert levels == [logging.INFO, logging.DEBUG]
    assert log.records[0].getMessage() == "msg 1"


def test_log_on_change_state_change_logs_again_with_custom_level(log, clean_repeat_state):
    repeat.log_on_change(log, "k", 1, "m", level=logging.WARNING)
    assert repeat.log_on_change(log, "k", 2, "m", level=logging.WARNING) is True
    assert [r.levelno for r in log.records] == [logging.WARNING, logging.WARNING]


def test_log_on_change_none_state_differs_from_missing(log, clean_repeat_state):
    # None - валидное состояние: первый вызов с None всё равно «изменение»
    assert repeat.log_on_change(log, "k", None, "m") is True
    assert repeat.log_on_change(log, "k", None, "m") is False


def test_reset_log_state_single_key_and_all(log, clean_repeat_state):
    repeat.log_on_change(log, "a", 1, "m")
    repeat.log_on_change(log, "b", 1, "m")
    repeat.reset_log_state("a")
    assert repeat.log_on_change(log, "a", 1, "m") is True
    assert repeat.log_on_change(log, "b", 1, "m") is False
    repeat.reset_log_state("нет такого")  # не падает
    repeat.reset_log_state()
    assert repeat.log_on_change(log, "b", 1, "m") is True


def test_missing_sentinel_semantics():
    m = repeat._Missing()
    assert m == repeat._MISSING
    assert m != object()
    assert hash(m) == hash(repeat._MISSING)
    assert repr(m) == "<не записывалось>"
