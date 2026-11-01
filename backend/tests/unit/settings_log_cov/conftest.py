"""Общие фикстуры тестов CEF-логгера и логирования (зона B6)."""

import logging

import pytest


class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)

    @property
    def messages(self) -> list[str]:
        return [r.getMessage() for r in self.records]


@pytest.fixture
def capture_logger():
    """Фабрика: вешает перехватчик прямо на именованный логгер.

    caplog не подходит: configure_cef_logging() ставит у логгера "cef"
    propagate=False, и записи дочерних логгеров не доходят до root.
    Исходное состояние логгера восстанавливается после теста.
    """
    saved = []

    def _capture(name: str, level: int = logging.DEBUG) -> _ListHandler:
        lg = logging.getLogger(name)
        saved.append((lg, lg.level, lg.propagate, lg.disabled))
        handler = _ListHandler()
        lg.addHandler(handler)
        lg.setLevel(level)
        lg.propagate = False
        lg.disabled = False
        saved.append(handler)
        return handler

    yield _capture
    for item in reversed(saved):
        if isinstance(item, logging.Handler):
            for lg, *_ in [s for s in saved if isinstance(s, tuple)]:
                if item in lg.handlers:
                    lg.removeHandler(item)
        else:
            lg, level, propagate, disabled = item
            lg.setLevel(level)
            lg.propagate = propagate
            lg.disabled = disabled
