"""
Централизованная настройка логирования backend.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from logging.handlers import RotatingFileHandler
from typing import Optional
from urllib.parse import parse_qsl, urlencode

from backend.settings.logging.matrix import DEFAULT_LEVEL, LEVEL_BY_NAME
from backend.utils.safe_paths import resolve_log_file_path, sanitize_log_value

BACKEND_LOGGER_NAME = "backend"
LOG_FORMAT = (
    "%(asctime)s,%(msecs)03d: [%(thread)d] [astra-chat-backend]"
    "[%(threadName)s][%(levelname)s][%(name)s:%(funcName)s(%(lineno)d)]: %(message)s"
)
LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"

_NOISY_LOGGERS = (
    "pymongo",
    "pymongo.topology",
    "pymongo.connection",
    "pymongo.serverSelection",
    "httpx",
    "httpcore",
    "urllib3",
    "socketio",
    "engineio",
    "socketio.server",
    "engineio.server",
    "asyncpg",
    "botocore",
    "boto3",
    "s3transfer",
    "urllib3.connectionpool",
    "minio",
    "PIL",
    "pdfminer",
    "multipart",
    "python_multipart",
    "asyncio",
    "watchfiles",
)

_NOISY_LEVEL_ENV = "BACKEND_LOG_NOISY_LEVEL"

_UVICORN_LOGGERS = (
    "uvicorn",
    "uvicorn.error",
    "uvicorn.access",
)

_NOISY_DEBUG_LOGS_HIDE_ENV = "NOISY_DEBUG_LOGS_HIDE"
_NOISY_INFO_LOGGERS = ("uvicorn.access", "uvicorn.error", "httpx", "httpcore")

_configured = False


class NoisyInfoToDebugFilter(logging.Filter):
    """Перевод шумных INFO логов в DEBUG с управлением через env-флаг."""

    def filter(self, record: logging.LogRecord) -> bool:
        name = str(record.name or "")
        is_noisy = any(
            name == logger_name or name.startswith(f"{logger_name}.")
            for logger_name in _NOISY_INFO_LOGGERS
        )
        if not is_noisy:
            return True
        if record.levelno != logging.INFO:
            return True
        if _hide_noisy_debug_logs():
            return False
        record.levelno = logging.DEBUG
        record.levelname = "DEBUG"
        return True


# Пути, по которым в лог не идёт УСПЕШНЫЙ запрос: пробы k8s и то, что фронт
# опрашивает по таймеру (баннер статуса LLM, плашка переиндексации, счётчик
# сессии). Каждый прилетает раз в несколько секунд с каждой открытой вкладки.
# Скрывается ТОЛЬКО успешный ответ 2хх
# Отключить фильтр целиком: BACKEND_LOG_QUIET_PATHS="" (пустая строка).
_DEFAULT_QUIET_PATHS = (
    # Пробы кластера
    "/health",
    "/healthz",
    "/ready",
    "/readyz",
    "/live",
    "/livez",
    "/metrics",
    # Что фронт опрашивает по таймеру.
    "/api/rag/reindex-status",
    "/api/llm/status",
    "/api/system/status",
    "/api/memory/status",
    "/api/mcp/status",
    "/api/agent/status",
    "/api/agent/agents/statuses",
    "/api/agent/langgraph/status",
    "/api/agent/mcp/status",
)

_QUIET_PATHS_ENV = "BACKEND_LOG_QUIET_PATHS"


def _quiet_paths() -> tuple:
    """Пути проб и поллинга: ENV BACKEND_LOG_QUIET_PATHS через запятую."""
    raw = os.getenv(_QUIET_PATHS_ENV)
    if raw is None:
        return _DEFAULT_QUIET_PATHS
    return tuple(p.strip() for p in raw.split(",") if p.strip())


# Пути, у которых шумит только СПИСОК
# Отключить: BACKEND_LOG_QUIET_SUFFIXES="" (пустая строка)
_DEFAULT_QUIET_SUFFIXES = ("/documents",)

_QUIET_SUFFIXES_ENV = "BACKEND_LOG_QUIET_SUFFIXES"


def _quiet_suffixes() -> tuple:
    """Окончания путей-списков: ENV BACKEND_LOG_QUIET_SUFFIXES через запятую."""
    raw = os.getenv(_QUIET_SUFFIXES_ENV)
    if raw is None:
        return _DEFAULT_QUIET_SUFFIXES
    return tuple(p.strip() for p in raw.split(",") if p.strip())


def is_quiet_path(method, path) -> bool:
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


def is_quiet_access(method, path, status) -> bool:
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


def _access_method_path_status(record: logging.LogRecord):
    """Метод, путь и код ответа из записи uvicorn.access.

    Формат записи: ''%s - "%s %s HTTP/%s" %d'' с args
    '(client, method, path, http_version, status)'. Разбираем args, а не
    готовую строку: так надёжнее и дешевле. Другой формат -> '(None, None, None)',
    и фильтр пропускает запись.
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


class QuietProbeAccessFilter(logging.Filter):
    """Прячет из 'uvicorn.access' успешные обращения к пробам и поллингу.

    uvicorn пишет строку на КАЖДЫЙ HTTP-запрос. Здесь это liveness/readiness
    кластера плюс таймеры фронта: баннер статуса LLM опрашивает
    '/api/llm/status', плашка переиндексации - '/api/rag/reindex-status',
    и так с каждой открытой вкладки. В сумме лог состоит из них.

    Работает независимо от 'NOISY_DEBUG_LOGS_HIDE': тот убирает access-лог
    целиком, а этот оставляет обычные запросы видимыми и убирает только пробы.

    Что НЕ прячем:

    * все чтоответило не 2xx - упавшая проба должна быть видна сразу;
    * записи уровня WARNING и выше;
    * все остальные пути.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.WARNING:
            return True
        return not is_quiet_access(*_access_method_path_status(record))


class UvicornAccessRedactionFilter(logging.Filter):
    """Маскирует чувствительные query-параметры в uvicorn.access."""

    _REQUEST_LINE_RE = re.compile(
        r"\b(GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)\s+(\S+)\s+(HTTP/\d(?:\.\d+)?)"
    )
    _JWTISH_RE = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)?$")
    _SENSITIVE_QUERY_KEYS = {
        "state",
        "code",
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "assertion",
    }

    @staticmethod
    def _redact_query(value: str) -> str:
        if "?" not in value:
            return value
        path, _, query = value.partition("?")
        if not query:
            return value
        try:
            pairs = parse_qsl(query, keep_blank_values=True)
            redacted_pairs = []
            for key, raw_val in pairs:
                key_l = str(key).strip().lower()
                if key_l in UvicornAccessRedactionFilter._SENSITIVE_QUERY_KEYS:
                    redacted_pairs.append((key, "<redacted>"))
                elif UvicornAccessRedactionFilter._JWTISH_RE.match(raw_val or ""):
                    redacted_pairs.append((key, "<redacted>"))
                else:
                    redacted_pairs.append((key, raw_val))
            return f"{path}?{urlencode(redacted_pairs)}"
        except Exception:
            return f"{path}?<redacted>"

    def _sanitize_text(self, text: str) -> str:
        def _replace(match: re.Match[str]) -> str:
            method = match.group(1)
            target = match.group(2)
            http_version = match.group(3)
            return f"{method} {self._redact_query(target)} {http_version}"

        return self._REQUEST_LINE_RE.sub(_replace, text)

    def filter(self, record: logging.LogRecord) -> bool:
        if str(record.name or "") != "uvicorn.access":
            return True
        try:
            if isinstance(record.args, tuple):
                redacted_args = []
                for arg in record.args:
                    if isinstance(arg, str):
                        redacted_args.append(
                            self._sanitize_text(self._redact_query(arg))
                        )
                    else:
                        redacted_args.append(arg)
                record.args = tuple(redacted_args)
            elif isinstance(record.msg, str):
                record.msg = self._sanitize_text(self._redact_query(record.msg))
        except Exception:
            return True
        return True


# Пакеты socket.io. engineio.server пишет каждый отправленный и полученный
# пакет целиком, а chat_chunk несёт весь накопленный ответ: при стриминге
# каждый кусок повторял в логе ответ заново, экранами \u-кодов.
# Куски стрима не пишем совсем, остальные большие пакеты - событие и размер.
_SOCKET_STREAM_EVENTS = ("chat_chunk", "multi_llm_chunk")
_SOCKET_PACKET_MAX_CHARS = 300
# «2["chat_complete",{...}]», с неймспейсом и id: «2/ns,17["event",...]».
_SOCKET_EVENT_RE = re.compile(r'^\d+(?:/[^,\[]*,)?\d*\["([^"]{1,80})"')

class SocketPacketLogFilter(logging.Filter):
    """engineio.server / socketio.server: без содержимого пакетов в логе."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            args = record.args
            if not isinstance(args, tuple) or not args:
                return True
            if record.name == "socketio.server":
                # «emitting event "chat_chunk" to …» - строка на каждый кусок.
                return args[0] not in _SOCKET_STREAM_EVENTS
            if record.name != "engineio.server" or len(args) != 3:
                return True
            if "packet %s data %s" not in str(record.msg):
                return True
            text = args[2] if isinstance(args[2], str) else str(args[2])
            m = _SOCKET_EVENT_RE.match(text)
            event = m.group(1) if m else ""
            if event in _SOCKET_STREAM_EVENTS:
                return False
            if len(text) > _SOCKET_PACKET_MAX_CHARS:
                record.msg = str(record.msg).replace("data %s", "%s (%s симв.)")
                record.args = (args[0], args[1], event or "data", len(text))
        except Exception:
            return True
        return True

def install_socket_packet_filter() -> None:
    """Фильтр пакетов - на логгеры библиотеки (dictConfig uvicorn его не снимает)."""
    for name in ("engineio.server", "socketio.server"):
        logger = logging.getLogger(name)
        if not any(isinstance(f, SocketPacketLogFilter) for f in logger.filters):
            logger.addFilter(SocketPacketLogFilter())


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _hide_noisy_debug_logs() -> bool:
    """Приглушать ли шумные чужие логгеры (auth/http/access) — env-флаг."""
    return _env_bool(_NOISY_DEBUG_LOGS_HIDE_ENV, True)


def _apply_debug_gate_to_level(level: int) -> int:
    """Уровень НАШИХ логгеров. Запрошенный DEBUG больше не подменяется на INFO.

    Раньше при 'NOISY_DEBUG_LOGS_HIDE=true' (значение по умолчанию) любой
    запрошенный DEBUG молча превращался в INFO — то есть 'APP_LOG_LEVEL=DEBUG'
    не работал, и отладку backend нельзя было включить в принципе. Смысл флага
    в другом: приглушить ЧУЖИЕ болтливые логгеры, а это теперь делает
    '_noisy_level_name()' вместе с '_NOISY_LOGGERS' — точечно и не задевая
    'backend.*'.
    """
    return level


def _apply_debug_gate_to_level_name(level_name: str) -> str:
    return str(level_name or "INFO").upper()


def _noisy_level_name() -> str:
    """Уровень чужих болтливых логгеров: ENV -> WARNING.

    'BACKEND_LOG_NOISY_LEVEL' задаёт его напрямую. Если переменной нет,
    работает старый флаг 'NOISY_DEBUG_LOGS_HIDE': включён (по умолчанию) —
    WARNING, выключен — уровень наших логгеров, то есть «как раньше, всё вместе».
    """
    raw = (os.getenv(_NOISY_LEVEL_ENV) or "").strip()
    if raw:
        return raw.upper()
    if _hide_noisy_debug_logs():
        return "WARNING"
    return _level_name()


def _level_name() -> str:
    """Уровень наших логгеров: APP_LOG_LEVEL / astrachat_LOG_LEVEL /
    ASTRACHAT_LOG_LEVEL / LOG_LEVEL, затем INFO."""
    return (
        os.getenv("APP_LOG_LEVEL")
        or os.getenv("astrachat_LOG_LEVEL")
        or os.getenv("ASTRACHAT_LOG_LEVEL")
        or os.getenv("LOG_LEVEL")
        or "INFO"
    ).upper()


def _resolve_level() -> int:
    return LEVEL_BY_NAME.get(_level_name(), DEFAULT_LEVEL)


def _ensure_utf8_stream(stream) -> None:
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")


def reconfigure_backend_handler_streams_utf8() -> None:
    """Переключает stdout/stderr handlers backend-логгера на UTF-8 (Windows)."""
    for handler in logging.getLogger(BACKEND_LOGGER_NAME).handlers:
        if hasattr(handler, "stream"):
            _ensure_utf8_stream(handler.stream)


def _attach_backend_handler(
    logger: logging.Logger,
    formatter: logging.Formatter,
    level: int,
    *,
    clear_handlers: bool = False,
    add_noisy_filter: bool = False,
    add_uvicorn_access_redaction_filter: bool = False,
) -> None:
    if clear_handlers:
        logger.handlers.clear()
    logger.setLevel(level)
    logger.propagate = False
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(formatter)
        handler.setLevel(level)
        if add_noisy_filter:
            handler.addFilter(NoisyInfoToDebugFilter())
        if add_uvicorn_access_redaction_filter:
            handler.addFilter(UvicornAccessRedactionFilter())
            # Пробы и поллинг фронта - мимо лога, пока отвечают успешно
            handler.addFilter(QuietProbeAccessFilter())
        _ensure_utf8_stream(handler.stream)
        logger.addHandler(handler)


def configure_uvicorn_logging(*, force: bool = False) -> None:
    """Переводит uvicorn / uvicorn.access на формат backend."""
    level = _apply_debug_gate_to_level(_resolve_level())
    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATEFMT)
    for name in _UVICORN_LOGGERS:
        logger_level = level
        _attach_backend_handler(
            logging.getLogger(name),
            formatter,
            logger_level,
            clear_handlers=force,
            add_noisy_filter=True,
            add_uvicorn_access_redaction_filter=name == "uvicorn.access",
        )


def get_uvicorn_log_config() -> dict:
    """dictConfig для uvicorn.run(log_config=...)."""
    level_name = _apply_debug_gate_to_level_name(_level_name())
    noisy_level = _noisy_level_name()
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "backend": {
                "format": LOG_FORMAT,
                "datefmt": LOG_DATEFMT,
            },
        },
        "filters": {
            "noisy_info_to_debug": {
                "()": "backend.settings.logging.setup.NoisyInfoToDebugFilter",
            },
            "uvicorn_access_redaction": {
                "()": "backend.settings.logging.setup.UvicornAccessRedactionFilter",
            },
            "quiet_probe_access": {
                "()": "backend.settings.logging.setup.QuietProbeAccessFilter",
            },
        },
        "handlers": {
            "backend_stdout": {
                "class": "logging.StreamHandler",
                "formatter": "backend",
                "stream": "ext://sys.stdout",
                "filters": [
                    "noisy_info_to_debug",
                    "uvicorn_access_redaction",
                    "quiet_probe_access",
                ],
            },
        },
        "loggers": {
            "uvicorn": {
                "handlers": ["backend_stdout"],
                "level": level_name,
                "propagate": False,
            },
            "uvicorn.error": {
                "handlers": ["backend_stdout"],
                "level": level_name,
                "propagate": False,
            },
            "uvicorn.access": {
                "handlers": ["backend_stdout"],
                "level": level_name,
                "propagate": False,
            },
            BACKEND_LOGGER_NAME: {
                "handlers": ["backend_stdout"],
                "level": level_name,
                "propagate": False,
            },
            # Чужие болтливые логгеры - своим уровнем, независимо от нашего.
            **{
                name: {"level": noisy_level, "propagate": True}
                for name in _NOISY_LOGGERS
            },
        },
        "root": {"handlers": ["backend_stdout"], "level": level_name},
    }


def configure_logging(*, force: bool = False) -> None:
    """Инициализирует логирование backend (stdout + опционально файл)."""
    global _configured
    if _configured and not force:
        return

    level = _apply_debug_gate_to_level(_resolve_level())
    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATEFMT)

    backend_logger = logging.getLogger(BACKEND_LOGGER_NAME)
    backend_logger.setLevel(level)
    backend_logger.propagate = False

    if force:
        backend_logger.handlers.clear()

    if not backend_logger.handlers:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        # Фильтрация ведётся на уровне логгеров; handler оставляем на DEBUG для точечных override.
        console_handler.setLevel(logging.DEBUG)
        _ensure_utf8_stream(console_handler.stream)
        backend_logger.addHandler(console_handler)

        raw_log_file = os.getenv("BACKEND_LOG_FILE")
        safe_log_file = resolve_log_file_path(raw_log_file)
        if raw_log_file and safe_log_file is None:
            backend_logger.warning(
                "BACKEND_LOG_FILE недоступен или отклонён: %s — используется только stdout",
                sanitize_log_value(raw_log_file),
            )
        if safe_log_file is not None:
            try:
                file_handler = RotatingFileHandler(
                    str(safe_log_file),
                    maxBytes=int(os.getenv("BACKEND_LOG_MAX_SIZE", "10485760")),
                    backupCount=int(os.getenv("BACKEND_LOG_BACKUP_COUNT", "5")),
                    encoding="utf-8",
                )
                file_handler.setFormatter(formatter)
                file_handler.setLevel(logging.DEBUG)
                backend_logger.addHandler(file_handler)
            except OSError as exc:
                backend_logger.warning(
                    "Не удалось открыть BACKEND_LOG_FILE (%s): %s — используется только stdout",
                    sanitize_log_value(safe_log_file),
                    exc,
                )

    configure_uvicorn_logging(force=force)

    noisy_level = LEVEL_BY_NAME.get(_noisy_level_name(), logging.WARNING)
    for noisy in _NOISY_LOGGERS:
        logging.getLogger(noisy).setLevel(noisy_level)
    install_socket_packet_filter()

    root_logger = logging.root
    root_logger.setLevel(noisy_level)
    if force:
        for handler in list(root_logger.handlers):
            if getattr(handler, "_astra_backend_root", False):
                root_logger.removeHandler(handler)
    if not any(getattr(h, "_astra_backend_root", False) for h in root_logger.handlers):
        root_handler = logging.StreamHandler(sys.stdout)
        root_handler.setFormatter(formatter)
        root_handler.setLevel(logging.DEBUG)
        root_handler.addFilter(UvicornAccessRedactionFilter())
        root_handler._astra_backend_root = True
        _ensure_utf8_stream(root_handler.stream)
        root_logger.addHandler(root_handler)

    for handler in logging.root.handlers:
        if hasattr(handler, "stream"):
            _ensure_utf8_stream(handler.stream)

    _configured = True


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """
    Возвращает логгер в иерархии backend.*.

    Использование: logger = get_logger(__name__)
    """
    if not _configured:
        configure_logging()

    if not name:
        return logging.getLogger(BACKEND_LOGGER_NAME)

    normalized = name.strip()
    if normalized in ("Backend", "backend"):
        normalized = BACKEND_LOGGER_NAME
    elif not normalized.startswith(f"{BACKEND_LOGGER_NAME}."):
        if normalized.startswith("backend"):
            pass
        else:
            normalized = f"{BACKEND_LOGGER_NAME}.{normalized.lstrip('.')}"

    return logging.getLogger(normalized)
