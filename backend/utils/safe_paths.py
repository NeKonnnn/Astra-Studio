"""
Валидация путей к файлам (CWE-73) и санитизация значений для журналов (CWE-117).
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Optional, Union

_UNSAFE_PATH_PARTS = frozenset({".", ".."})
_LOG_UNSAFE_CHARS = re.compile(r"[\r\n\x00]")


def default_app_root() -> Path:
    # В контейнере код лежит в /app/backend; одного существования /app мало
    # (на хосте Windows часто есть чужой F:\app).
    container_root = Path("/app")
    if container_root.is_dir() and (container_root / "backend").is_dir():
        return container_root
    return Path(__file__).resolve().parent.parent.parent


def sanitize_log_value(value: object) -> str:
    """Убирает переводы строк и NUL из значений, попадающих в журнал (CWE-117)."""
    return _LOG_UNSAFE_CHARS.sub(" ", str(value))


def resolve_path_under_base(
    raw_path: Union[str, Path],
    base_dir: Optional[Union[str, Path]] = None,
    *,
    create_parent: bool = False,
) -> Optional[Path]:
    """
    Разрешает путь относительно base_dir и отклоняет выход за его пределы.
    Компоненты ``.`` и ``..`` в исходном пути запрещены.
    """
    text = str(raw_path).strip()
    if not text:
        return None

    base = (
        Path(base_dir).resolve()
        if base_dir is not None
        else default_app_root().resolve()
    )
    candidate = Path(text).expanduser()

    if any(part in _UNSAFE_PATH_PARTS for part in candidate.parts):
        return None

    try:
        resolved = (
            candidate.resolve(strict=False)
            if candidate.is_absolute()
            else (base / candidate).resolve(strict=False)
        )
    except (OSError, RuntimeError):
        return None

    try:
        resolved.relative_to(base)
    except ValueError:
        return None

    if create_parent:
        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None

    return resolved


def _resolve_dir_from_env(env_name: str, default: Path, base: Path) -> Path:
    env_value = os.getenv(env_name)
    if env_value:
        resolved = resolve_path_under_base(env_value, base, create_parent=True)
        if resolved is not None:
            return resolved
    default.mkdir(parents=True, exist_ok=True)
    return default


def is_writable_path(path: Path) -> bool:
    """Проверяет, можно ли создать/записать файл по указанному пути."""
    directory = path.parent
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        if not directory.is_dir():
            return False
    return os.access(directory, os.W_OK)


def default_logs_dir() -> Path:
    base = default_app_root()
    return _resolve_dir_from_env("BACKEND_LOG_DIR", base / "logs", base)


def _prefer_docker_memory(base: Path) -> Path:
    """/app/memory только если корень приложения реально /app (Docker), не в тестах."""
    docker_root = Path("/app")
    try:
        if docker_root.is_dir() and base.resolve() == docker_root.resolve():
            return Path("/app/memory")
    except OSError:
        pass
    return base / "memory"


def default_context_prompts_dir() -> Path:
    base = default_app_root()
    preferred = _prefer_docker_memory(base)
    return _resolve_dir_from_env("CONTEXT_PROMPTS_DIR", preferred, base)


def resolve_log_file_path(raw_path: Optional[str]) -> Optional[Path]:
    if not raw_path:
        return None
    logs_dir = default_logs_dir()
    resolved = resolve_path_under_base(raw_path, logs_dir.parent, create_parent=True)
    if resolved is None:
        return None
    try:
        resolved.relative_to(logs_dir.parent)
    except ValueError:
        return None
    if not is_writable_path(resolved):
        return None
    return resolved


def resolve_context_prompts_file_path(raw_path: Optional[str]) -> Optional[Path]:
    if not raw_path:
        return None
    bases = (
        default_context_prompts_dir().parent,
        Path(tempfile.gettempdir()) / "astrachat",
    )
    for base in bases:
        resolved = resolve_path_under_base(raw_path, base, create_parent=True)
        if resolved is not None:
            return resolved
    return None


def resolve_context_prompts_file_path_primary() -> Path:
    """Writable-путь для context_prompts.json (Docker: /app/memory)."""
    env_path = os.getenv("CONTEXT_PROMPTS_FILE", "").strip()
    if env_path:
        resolved = resolve_context_prompts_file_path(env_path)
        if resolved is not None:
            return resolved
    return default_context_prompts_dir() / "context_prompts.json"


def context_prompts_seed_paths() -> list[Path]:
    """Read-only шаблоны и существующие файлы для первичной загрузки."""
    backend_dir = Path(__file__).resolve().parent.parent
    root_dir = backend_dir.parent
    seeds: list[Path] = [
        resolve_context_prompts_file_path_primary(),
        default_context_prompts_dir() / "context_prompts.json",
        root_dir / "context_prompts.json",
        Path("/app/context_prompts.json"),
        Path("/app/memory/context_prompts.json"),
        Path(tempfile.gettempdir()) / "astrachat" / "context_prompts.json",
    ]
    unique: list[Path] = []
    for path in seeds:
        rp = path.resolve()
        if rp not in unique:
            unique.append(rp)
    return unique


def context_prompts_file_fallbacks(primary_path: Union[str, Path]) -> list[Path]:
    primary = Path(primary_path).resolve()
    fallbacks: list[Path] = [primary]
    for path in (
        default_context_prompts_dir() / "context_prompts.json",
        Path(tempfile.gettempdir()) / "astrachat" / "context_prompts.json",
        Path(os.getcwd()) / "memory" / "context_prompts.json",
    ):
        rp = path.resolve()
        if rp not in fallbacks:
            fallbacks.append(rp)
    return fallbacks


def default_llm_settings_dir() -> Path:
    """Writable-каталог для llm_settings.json (Docker: /app/memory)."""
    base = default_app_root()
    preferred = _prefer_docker_memory(base)
    return _resolve_dir_from_env("LLM_SETTINGS_DIR", preferred, base)


def resolve_llm_settings_file_path() -> Path:
    """
    Путь для записи llm_settings.json.
    Порядок: LLM_SETTINGS_PATH -> LLM_SETTINGS_DIR/llm_settings.json -> /app/memory -> cwd/memory.
    """
    env_path = os.getenv("LLM_SETTINGS_PATH", "").strip()
    if env_path:
        resolved = resolve_path_under_base(
            env_path, default_app_root(), create_parent=True
        )
        if resolved is not None:
            return resolved
    return default_llm_settings_dir() / "llm_settings.json"


def llm_settings_seed_paths() -> list[Path]:
    """Read-only шаблоны из образа (для первичной загрузки, если writable-файла ещё нет)."""
    backend_dir = Path(__file__).resolve().parent.parent
    seeds: list[Path] = [
        backend_dir / "llm_settings.json",
        default_app_root() / "llm_settings.json",
        Path("/app/llm_settings.json"),
    ]
    unique: list[Path] = []
    for p in seeds:
        rp = p.resolve()
        if rp not in unique:
            unique.append(rp)
    return unique


def llm_settings_file_fallbacks(primary_path: Union[str, Path]) -> list[Path]:
    primary = Path(primary_path).resolve()
    fallbacks: list[Path] = [primary]
    for p in (
        default_llm_settings_dir() / "llm_settings.json",
        Path(tempfile.gettempdir()) / "astrachat" / "llm_settings.json",
        Path(os.getcwd()) / "memory" / "llm_settings.json",
    ):
        rp = p.resolve()
        if rp not in fallbacks:
            fallbacks.append(rp)
    return fallbacks


def resolve_config_file_path(raw_path: Optional[str]) -> Optional[Path]:
    if not raw_path:
        return None
    resolved = resolve_path_under_base(raw_path, default_app_root())
    if resolved is None or not resolved.is_file():
        return None
    if resolved.suffix.lower() not in {".yml", ".yaml"}:
        return None
    return resolved
