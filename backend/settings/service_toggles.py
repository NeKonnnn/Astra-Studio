"""
service_toggles.py - ВРЕМЕННЫЕ фича-флаги микросервисов.
[FEATURE-FLAG] Весь модуль временный. Когда сервисы будем в проде
удалим этот файл и все строки с маркером # FEATURE-FLAG в проекте
Назначение: включать/выключать микросервисы булевым флагом
Если сервис выключен:
  - на старте один раз пишется «Микросервис X: в разработке»
  - попытка вызова сервиса через UI ведет к ServiceDisabledError -> 503 «в разработке»;
  - в сеть не лезет (ни коннекта, ни health, ни reconnect)
Источник значения (приоритет):
ENV _SERVICE_ENABLED > config.yml microservices_enabled. > default False
Флаг ГЛАВНЕЕ наличия url: даже если url прописан, при false сервис не работает
"""

import os
from typing import Dict, Tuple

from backend.settings import get_settings
from backend.settings.logging import get_logger
from backend.settings.logging.errors import logged_suppress

logger = get_logger(__name__)

_SERVICES: Dict[str, Tuple[str, str]] = {
    "stt": ("STT_SERVICE_ENABLED", "STT (распознавание речи)"),
    "tts": ("TTS_SERVICE_ENABLED", "TTS (синтез речи)"),
    "diarization": ("DIARIZATION_SERVICE_ENABLED", "Diarization (диаризация)"),
    "rag": ("RAG_SERVICE_ENABLED", "RAG (поиск по документам)"),
    "rag_models": ("RAG_MODELS_SERVICE_ENABLED", "RAG-models (эмбеддинги)"),
    "agents": ("AGENTS_SERVICE_ENABLED", "Пользовательские агенты (/api/agents)"),
    "minio": ("MINIO_SERVICE_ENABLED", "MinIO (хранилище файлов)"),
    "postgresql": ("POSTGRESQL_SERVICE_ENABLED", "PostgreSQL + pgvector"),
    "mcp": ("MCP_ENABLED", "MCP (инструменты)"),
}

_REQUIRES: Dict[str, Tuple[str, ...]] = {
    "agents": ("postgresql",),
    "rag": ("postgresql",),
}

_TRUE = {"1", "true", "yes", "on"}


def _env_bool(name: str):
    """True/False из ENV или None, если переменная не задана/пустая"""
    val = os.getenv(name)
    if val is None or not str(val).strip():
        return None
    return str(val).strip().lower() in _TRUE


def _own_enabled(key: str) -> bool:
    """Собственный флаг сервиса без учета зависимостей ENV/YAML"""
    if key == "mcp":
        try:
            mcp = getattr(get_settings(), "mcp", None)
            return bool(mcp is not None and getattr(mcp, "enabled", False))
        except Exception:
            logger.exception("Ошибка операции")
            return False
    env_name = _SERVICES.get(key, (f"{key.upper()}_SERVICE_ENABLED", key))[0]
    env_val = _env_bool(env_name)
    if env_val is not None:
        return env_val
    with logged_suppress(logger):
        ms = getattr(get_settings(), "microservices_enabled", None)
        if ms is not None:
            yaml_val = getattr(ms, key, None)
            if isinstance(yaml_val, bool):
                return yaml_val
    return False


def is_service_enabled(key: str) -> bool:
    """Включён ли сервис: собственный флаг И все зависимости (см. _REQUIRES)
    Источник флага (приоритет): ENV _SERVICE_ENABLED > config.yml
    microservices_enabled. > False
    """
    if not _own_enabled(key):
        return False
    for dep in _REQUIRES.get(key, ()):
        if not is_service_enabled(dep):
            return False
    return True


def display_name(key: str) -> str:
    return _SERVICES.get(key, (None, key))[1]


class ServiceDisabledError(Exception):
    """Микросервис выключен фича-флагом (в разработке) Ловится handler'ом в main.py -> 503"""

    def __init__(self, key: str):
        self.key = key
        self.display = display_name(key)
        super().__init__(f"Микросервис '{self.display}' в разработке")


def require_service(key: str) -> None:
    """Гейт: если сервис выключен - бросает ServiceDisabledError (503, без сетевых вызовов)"""
    if not is_service_enabled(key):
        raise ServiceDisabledError(key)


_logged_once = False


def log_disabled_services_once() -> None:
    """Один раз на старте логирует все выключенные сервисы. Зовётся из startup_event"""
    global _logged_once
    if _logged_once:
        return
    _logged_once = True
    for key, (env_name, disp) in _SERVICES.items():
        if is_service_enabled(key):
            continue
        if not _own_enabled(key):
            reason = f"{env_name}=false"
        else:
            # собственный флаг включён, но выключена зависимость
            off = [d for d in _REQUIRES.get(key, ()) if not is_service_enabled(d)]
            reason = "выключена зависимость: " + ", ".join(
                f"{_SERVICES[d][0]}=false" for d in off
            )
        logger.info(
            "[feature-flag] Микросервис «%s» — В РАЗРАБОТКЕ (%s). Подключение отключено",
            disp,
            reason,
        )
        if key == "rag":
            logger.info(
                "[feature-flag] RAG отключён — шаг поиска в чате будет пропускаться"
            )
