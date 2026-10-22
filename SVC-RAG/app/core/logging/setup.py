"""
Централизованная настройка логирования SVC-RAG
Порт матрицы логов backend (backend.settings.logging) под SVC-RAG
"""

from __future__ import annotations
import logging
import logging.config
import os
import sys
from typing import Optional
from app.core.logging.matrix import DEFAULT_LEVEL, LEVEL_BY_NAME

# Корень иерархии логгеров сервиса (RAG-модули: logging.getLogger(__name__) -> "app.*").
APP_LOGGER_NAME = "app"
LOG_FORMAT = (
    "%(asctime)s,%(msecs)03d: [%(thread)d] [astra-chat-rag]"
    "[%(threadName)s][%(levelname)s][%(name)s:%(funcName)s(%(lineno)d)]: %(message)s"
)
LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")

# Библиотеки, у которых DEBUG означает «строка на каждый токен разбора».
# Вернуть подробность: RAG_LOG_NOISY_LEVEL=DEBUG.
_NOISY_LOGGERS = (
    "pdfminer",
    "pdfplumber",
    "PIL",
    "httpcore",
    "httpx",
    "urllib3",
    "asyncio",
    "multipart",
    "python_multipart",
)

# Пути, по которым в лог не идёт УСПЕШНЫЙ запрос: пробы k8s и опрос статуса
# бэкендом. Каждый прилетает раз в несколько секунд и вытесняет из лога всё
# остальное; полезной информации в «/health -> 200» нет.
# Скрывается ТОЛЬКО успешный ответ 2хх
# Отключить фильтр целиком: RAG_LOG_QUIET_PATHS="" (пустая строка).
_DEFAULT_QUIET_PATHS = (
    "/health",
    "/healthz",
    "/ready",
    "/readyz",
    "/live",
    "/livez",
    "/metrics",
    "/reindex/status",
)

# Пути, у которых шумит только СПИСОК
# Отключить: RAG_LOG_QUIET_SUFFIXES="" (пустая строка)
_DEFAULT_QUIET_SUFFIXES = ("/documents",)

_configured = False


def _quiet_paths() -> tuple:
    """Пути проб: ENV RAG_LOG_QUIET_PATHS через запятую, иначе дефолт."""
    raw = os.getenv("RAG_LOG_QUIET_PATHS")
    if raw is None:
        return _DEFAULT_QUIET_PATHS
    parts = tuple(p.strip() for p in raw.split(",") if p.strip())
    return parts


def _quiet_suffixes() -> tuple:
    """Окончания путей-списков: ENV RAG_LOG_QUIET_SUFFIXES через запятую."""
    raw = os.getenv("RAG_LOG_QUIET_SUFFIXES")
    if raw is None:
        return _DEFAULT_QUIET_SUFFIXES
    return tuple(p.strip() for p in raw.split(",") if p.strip())


def is_quiet_path(method: Optional[str], path: Optional[str]) -> bool:
    """Запрос похож на фоновый опрос: ЧТЕНИЕ по пути пробы или списка.

    Код ответа здесь не участвует - функция нужна и там, где он ещё
    неизвестен (строка «запрос начался» в middleware).

    Запись по тому же пути опросом не считается: для /documents это разница
    между «фронт опросил список» и «пользователь загрузил файл».
    """
    if not path:
        return False
    # Метод неизвестен - не рискуем и пишем строку: пустой method сюда попадает
    # только когда запись uvicorn.access разобралась не полностью.
    if str(method or "").upper() not in ("GET", "HEAD"):
        return False
    clean = str(path).split("?", 1)[0]
    if any(q in clean for q in _quiet_paths()):
        return True
    return any(clean.endswith(s) for s in _quiet_suffixes())


def is_quiet_access(
    method: Optional[str], path: Optional[str], status: Optional[int]
) -> bool:
    """Тот же опрос, но с УСПЕШНЫМ ответом - прячем только такие строки.

    Всё, что ответило не 2xx, остаётся в логе: ради этого его и читают.
    Нераспознанный код тоже остаётся - неизвестно значит не успех.
    """
    if status is None:
        return False
    try:
        code = int(status)
    except (TypeError, ValueError):
        return False
    if not 200 <= code < 300:
        return False
    return is_quiet_path(method, path)


class QuietProbeAccessFilter(logging.Filter):
    """Прячет из 'uvicorn.access' успешные обращения к пробам и статусам.

    uvicorn пишет строку на КАЖДЫЙ HTTP-запрос. В кластере это liveness/readiness
    каждые несколько секунд плюс постоянный опрос '/reindex/status' бэкендом -
    в сумме лог состоит из них, а индексация и поиск в нём теряются.

    Что НЕ прячем:
    * все что ответило не 2xx - упавшая проба должна быть видна сразу;
    * записи уровня WARNING и выше;
    * все остальные пути.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.WARNING:
            return True
        return not is_quiet_access(*_access_method_path_status(record))


def _access_method_path_status(record: logging.LogRecord):
    """Метод, путь и код ответа из записи uvicorn.access.

    Формат записи: ''%s - "%s %s HTTP/%s" %d'' с args
    '(client, method, path, http_version, status)'. Разбираем args, а не
    готовую строку: так надёжнее и дешевле. Если формат другой - возвращаем
    '(None, None, None)', и фильтр пропускает запись.
    """
    args = record.args
    if isinstance(args, tuple) and len(args) >= 5:
        method, path, status = args[1], args[2], args[4]
        try:
            status_int = int(status)
        except (TypeError, ValueError):
            status_int = None
        if isinstance(path, str):
            method_str = method if isinstance(method, str) else None
            return (method_str, path.split("?", 1)[0], status_int)
    return (None, None, None)


def _settings_logging():
    """Секция 'logging' из config.yml. Ошибка чтения - None."""
    try:
        from app.core.config import get_settings

        return getattr(get_settings(), "logging", None)
    except Exception:
        return None


def _level_from(env_names: tuple, yml_field: str, default: str) -> str:
    """ENV -> config.yml -> дефолт. Первый непустой источник побеждает."""
    for name in env_names:
        value = (os.getenv(name) or "").strip()
        if value:
            return value.upper()
    value = str(getattr(_settings_logging(), yml_field, "") or "").strip()
    return (value or default).upper()


def _level_name() -> str:
    """Уровень наших логгеров.

    Порядок: APP_LOG_LEVEL -> SVC_RAG_LOG_LEVEL -> RAG_LOG_LEVEL ->
    LOG_LEVEL -> logging.level из config.yml -> INFO
    """
    return _level_from(
        ("APP_LOG_LEVEL", "SVC_RAG_LOG_LEVEL", "RAG_LOG_LEVEL", "LOG_LEVEL"),
        "level",
        "INFO",
    )


def _noisy_level_name() -> str:
    """Уровень чужих болтливых логгеров: RAG_LOG_NOISY_LEVEL, затем
    'logging.noisy_level' из config.yml, затем WARNING."""
    return _level_from(("RAG_LOG_NOISY_LEVEL",), "noisy_level", "WARNING")


def _resolve_level() -> int:
    return LEVEL_BY_NAME.get(_level_name(), DEFAULT_LEVEL)


def get_uvicorn_log_config() -> dict:
    """
    dictConfig для единого формата логов.
    Тот же по смыслу конфиг, что backend отдаёт в uvicorn.run(log_config=...),
    но в RAG применяется через logging.config.dictConfig(...) при импорте main.py
    Покрывает: uvicorn / uvicorn.error / uvicorn.access (свои хендлеры),
    "app" (наши логгеры) и root (httpx и всё прочее, что пропагейтит в корень)
    """
    level_name = _level_name()
    noisy_level = _noisy_level_name()
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "svc_rag": {
                "format": LOG_FORMAT,
                "datefmt": LOG_DATEFMT,
            },
        },
        "filters": {
            "quiet_probe_access": {
                "()": "app.core.logging.setup.QuietProbeAccessFilter",
            },
        },
        "handlers": {
            "svc_rag_stdout": {
                "class": "logging.StreamHandler",
                "formatter": "svc_rag",
                "stream": "ext://sys.stdout",
            },
        },
        "loggers": {
            "uvicorn": {
                "handlers": ["svc_rag_stdout"],
                "level": level_name,
                "propagate": False,
            },
            "uvicorn.error": {
                "handlers": ["svc_rag_stdout"],
                "level": level_name,
                "propagate": False,
            },
            "uvicorn.access": {
                "handlers": ["svc_rag_stdout"],
                "level": level_name,
                "propagate": False,
                # Пробы и опрос статуса - мимо access-лога, пока отвечают успешно.
                "filters": ["quiet_probe_access"],
            },
            APP_LOGGER_NAME: {
                "handlers": ["svc_rag_stdout"],
                "level": level_name,
                "propagate": False,
            },
            **{
                name: {"level": noisy_level, "propagate": True}
                for name in _NOISY_LOGGERS
            },
        },
        "root": {"handlers": ["svc_rag_stdout"], "level": level_name},
    }


def _ensure_stdout_utf8() -> None:
    """stdout → UTF-8, чтобы кириллица и символы [LLM→]/[LLM✗] не падали с UnicodeEncodeError"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass


def configure_logging(*, force: bool = False) -> None:
    """
    Инициализирует логирование SVC-RAG в едином формате.
    Идемпотентно: повторные вызовы (в т.ч. ленивые из get_logger) — no-op, пока не force.
    """
    global _configured
    if _configured and not force:
        return
    _ensure_stdout_utf8()
    logging.config.dictConfig(get_uvicorn_log_config())
    _configured = True


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """
    Возвращает логгер в иерархии app.*.
    Использование: logger = get_logger(__name__)
    """
    if not _configured:
        configure_logging()
    if not name:
        return logging.getLogger(APP_LOGGER_NAME)
    normalized = name.strip()
    if normalized in ("App", "app"):
        normalized = APP_LOGGER_NAME
    elif not normalized.startswith(f"{APP_LOGGER_NAME}."):
        normalized = f"{APP_LOGGER_NAME}.{normalized.lstrip('.')}"
    return logging.getLogger(normalized)
