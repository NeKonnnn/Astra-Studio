"""
Утилита для исправления проблем с кодировкой в Windows
"""

import os
import subprocess
import sys

from backend.settings.logging import (
    get_logger,
    reconfigure_backend_handler_streams_utf8,
)

logger = get_logger(__name__)


def fix_windows_encoding():
    """Исправляет проблемы с кодировкой в Windows"""
    if sys.platform != "win32":
        return True

    try:
        import shutil

        _chcp = shutil.which("chcp") or os.path.join(
            os.environ.get("SystemRoot", r"C:\Windows"), "System32", "chcp.exe"
        )
        subprocess.run([_chcp, "65001"], check=False, capture_output=True)  # noqa: S603

        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8")

        reconfigure_backend_handler_streams_utf8()

        logger.debug("Кодировка Windows настроена на UTF-8")
        return True
    except Exception:
        logger.exception("Ошибка настройки кодировки")
        return False


def safe_print(text: str):
    """Безопасный вывод текста с правильной кодировкой"""
    try:
        logger.info("%s", text)
    except UnicodeEncodeError:
        logger.info("%s", text.encode("ascii", "ignore").decode("ascii"))


def safe_log(log, level: str, message: str):
    """Безопасное логирование с правильной кодировкой"""
    try:
        log_fn = getattr(log, level.lower(), log.info)
        log_fn(message)
    except UnicodeEncodeError:
        safe_message = message.encode("ascii", "ignore").decode("ascii")
        log.info(safe_message)
