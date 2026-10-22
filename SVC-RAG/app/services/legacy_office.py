"""Старые бинарные форматы Office: .doc и .ppt.

Python-библиотек для них нет, разбор - внешними утилитами из образа:
antiword (Word 97-2003) и пакет catdoc (catdoc - Word, в т.ч. Word 6/95;
catppt - PowerPoint 97-2003). Файл пишется во временный, текст берётся из
stdout утилиты.

Файл со старым расширением часто внутри другого формата: .doc, сохранённый
как RTF или как .docx, .xls/.ppt - как .xlsx/.pptx. Такие файлы
real_office_ext() отправляет в парсер настоящего формата.
"""

import asyncio
import logging
import os
import re
import shutil
import tempfile
import time
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

ZIP_MAGIC = b"PK\x03\x04"
RTF_MAGIC = b"{\\rtf"

# Старое расширение -> расширение формата, которым файл оказался внутри.
_ZIP_EXT = {".doc": ".docx", ".xls": ".xlsx", ".ppt": ".pptx"}

_TIMEOUT_S = 120
# Кодировка 8-битных документов (Word 6/95, текст без Юникода): catdoc сам
# по умолчанию берёт cp1252, и кириллица превращается в мусор.
_SOURCE_CHARSET = "cp1251"

_DOC_TOOLS = (
    ("antiword", "-m", "UTF-8.txt", "-w", "0"),
    ("catdoc", "-d", "utf-8", "-s", _SOURCE_CHARSET, "-w"),
)
_PPT_TOOLS = (("catppt", "-d", "utf-8", "-s", _SOURCE_CHARSET),)


def real_office_ext(ext: str, data: bytes) -> str:
    """Расширение по содержимому, если старое расширение не соответствует файлу."""
    head = data[:8]
    if ext in _ZIP_EXT and head.startswith(ZIP_MAGIC):
        return _ZIP_EXT[ext]
    if ext == ".doc" and head.startswith(RTF_MAGIC):
        return ".rtf"
    return ext


def _tidy(text: str) -> str:
    """antiword выравнивает таблицы пробелами до ширины колонки - сжимаем."""
    lines = [re.sub(r"[ \t]{2,}", " ", ln).rstrip() for ln in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


async def _run_tool(cmd: Sequence[str], path: str) -> Tuple[int, str, str]:
    """(код выхода, stdout, stderr без пути временного файла)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=_TIMEOUT_S)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"{cmd[0]}: не уложился в {_TIMEOUT_S} с") from None
    except asyncio.CancelledError:
        # BUG-RAG-20: при отмене запроса убиваем antiword/catdoc/catppt, иначе процесс утекает.
        proc.kill()
        await proc.wait()
        raise
    msg = err.decode("utf-8", errors="replace").replace(path, "файл").strip()
    return proc.returncode, out.decode("utf-8", errors="replace"), msg


async def _extract(
    data: bytes, suffix: str, tools: Sequence[Sequence[str]], name: str = ""
) -> str:
    """Первая утилита, давшая текст. Ошибки всех утилит - в исключении.

    Каждый запуск - строка в лог: код, сколько символов вывела, stderr,
    время. По ней видно, почему файл «пустой»: утилита упала, ничего не
    вывела или вывела одни пробелы.
    """
    label = name or f"файл{suffix}"
    fd, path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        errors: List[str] = []
        empty: Optional[str] = None
        for cmd in tools:
            if not shutil.which(cmd[0]):
                logger.warning(
                    "[legacy] %s «%s»: утилиты нет в PATH (%s)",
                    cmd[0],
                    label,
                    os.environ.get("PATH", ""),
                )
                errors.append(f"{cmd[0]} не установлен")
                continue
            started = time.perf_counter()
            try:
                code, raw, stderr = await _run_tool(cmd, path)
            except Exception as e:
                logger.warning("[legacy] %s «%s»: %s", cmd[0], label, e)
                errors.append(str(e))
                continue
            text = _tidy(raw) if code == 0 else ""
            logger.info(
                "[legacy] %s «%s» (%s байт): код %s, вывод %s симв., после "
                "чистки %s, %s мс, stderr: «%s»",
                " ".join(cmd),
                label,
                len(data),
                code,
                len(raw),
                len(text),
                int((time.perf_counter() - started) * 1000),
                stderr[:500] or "-",
            )
            if code != 0:
                errors.append(
                    f"{cmd[0]}: код {code}: {stderr[:300] or 'без сообщения'}"
                )
                continue
            if text:
                return text
            empty = cmd[0]
        if empty:
            # Утилита отработала, но текста нет - пустой документ, а не ошибка.
            logger.warning(
                "[legacy] «%s»: утилиты отработали без ошибок, но текста нет (%s)",
                label,
                "; ".join(errors) or f"последняя - {empty}",
            )
            return ""
        raise RuntimeError("; ".join(errors))
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


async def extract_doc_text(data: bytes, name: str = "") -> str:
    """Word 97-2003 (.doc): antiword, при ошибке или пустом выводе - catdoc."""
    return await _extract(data, ".doc", _DOC_TOOLS, name)


async def extract_ppt_text(data: bytes, name: str = "") -> str:
    """PowerPoint 97-2003 (.ppt) через catppt - запасной путь, основной -
    свой разбор ppt_binary (catppt не видит текст надписей, таблиц и групп
    в файлах из PowerPoint 2007+)."""
    return await _extract(data, ".ppt", _PPT_TOOLS, name)
