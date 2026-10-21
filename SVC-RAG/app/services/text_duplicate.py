"""Дубликат по тексту: тот же документ под другим именем и в других байтах.

Побайтный отпечаток (document_duplicate.file_content_hash) ловит только точную
копию файла. Тот же текст, сохранённый заново, проходит: txt в другой
кодировке или с переносом в конце, пересохранённый docx (внутри zip с датами).

Здесь сравнивается извлечённый текст.
"""

from __future__ import annotations

import hashlib
import unicodedata
from typing import Awaitable, Callable, Dict, Iterable, Optional, Tuple

from app.core.logging import get_logger

logger = get_logger(__name__)

# Невидимые символы, которыми различаются «одинаковые» тексты: BOM в начале
# txt из Блокнота, пробелы нулевой ширины, мягкий перенос.
_INVISIBLE = dict.fromkeys(map(ord, "\ufeff\u200b\u200c\u200d\u2060\u00ad"))

# (хранилище, id документа) -> отпечаток текста. Строка в 64 символа на
# документ; после перезапуска досчитывается заново при первой проверке.
_fingerprints: Dict[Tuple[str, int], str] = {}
_MAX_CACHED = 100_000


def text_fingerprint(text: Optional[str]) -> str:
    """SHA-256 текста без различий в пробелах, переносах и невидимых символах.

    Регистр не трогаем: разные по регистру тексты считаем разными документами.
    Пустой текст - пустая строка (с пустыми ничего не сравниваем).
    """
    norm = _normalize(text)
    if not norm:
        return ""
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def _normalize(text: Optional[str]) -> str:
    norm = unicodedata.normalize("NFKC", text or "").translate(_INVISIBLE)
    return "".join(norm.split())


async def find_text_duplicate(
    *,
    store: str,
    text: str,
    candidates: Iterable[Tuple[int, str]],
    load_text: Callable[[int], Awaitable[Optional[str]]],
    older_than: Optional[int] = None,
    upload_name: str = "",
) -> Optional[Dict[str, object]]:
    """Документ области с тем же текстом: {"id", "filename"} или None.

    candidates - (id, имя) документов той же области, что и загружаемый
    (проект, база агента, библиотека). Текст документа достаётся через
    load_text только один раз за жизнь процесса - дальше берётся отпечаток.

    older_than - id только что вставленного документа: сравниваем лишь с
    документами раньше него. Две одновременные загрузки одного текста обе
    проходят проверку до вставки; после вставки более поздняя (больший id)
    находит раннюю и уступает, а ранняя не видит позднюю и остаётся.

    upload_name - имя загружаемого файла для строки в логе, когда совпадений
    нет: сколько документов сравнили и их отпечатки. Текст в лог не пишется.
    """
    fingerprint = text_fingerprint(text)
    if not fingerprint:
        return None
    if len(_fingerprints) > _MAX_CACHED:
        _fingerprints.clear()
    seen = []
    for doc_id, filename in candidates:
        if older_than is not None and int(doc_id) >= int(older_than):
            continue
        key = (store, int(doc_id))
        known = _fingerprints.get(key)
        if known is None:
            try:
                known = text_fingerprint(await load_text(int(doc_id)))
            except Exception as e:
                logger.warning(
                    "[DUPLICATE %s] текст документа id=%s не прочитан: %s",
                    store,
                    doc_id,
                    e,
                )
                continue
            _fingerprints[key] = known
        if known and known == fingerprint:
            return {"id": int(doc_id), "filename": filename}
        seen.append(f"{doc_id}:{(known or '-')[:8]}")
    if older_than is None:
        # Почему не дубль: нет документов в области или другой отпечаток
        # (длина - символов без пробелов, сравнить с соседом по логу).
        logger.info(
            "[DUPLICATE %s] '%s': по тексту совпадений нет - отпечаток %s "
            "(%s симв. без пробелов), документов области %s: %s",
            store,
            upload_name or "?",
            fingerprint[:8],
            len(_normalize(text)),
            len(seen),
            ", ".join(seen[:20]) or "-",
        )
    return None


def remember_text(store: str, document_id: Optional[int], text: str) -> None:
    """Запомнить отпечаток только что загруженного документа - без чтения из базы."""
    if document_id is not None:
        _fingerprints[(store, int(document_id))] = text_fingerprint(text)


def text_duplicate_index_result(
    store: str, filename: str, found: Dict[str, object]
) -> Dict[str, object]:
    """Ответ индексации «дубликат» - тот же, что у побайтной проверки, с именем."""
    from app.services.document_duplicate import duplicate_index_result

    same_name = str(found.get("filename") or "")
    logger.info(
        "[DUPLICATE %s] '%s': тот же текст, что у '%s' (id=%s) - не загружаю",
        store,
        filename,
        same_name,
        found.get("id"),
    )
    return duplicate_index_result(
        document_id=found.get("id"),
        filename=same_name or filename,
        message=f"Упс, такой документ уже подгружен в базу - под именем «{same_name}»",
    )
