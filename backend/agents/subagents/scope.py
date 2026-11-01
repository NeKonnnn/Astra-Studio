"""Субагент: в каких файлах искать - указания его карточки и файлы из задания."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from backend.realtime.helpers import kb_search_agent_documents
from backend.settings.logging import get_logger

log = get_logger(__name__)


# --- В каких файлах искать: указания карточки и файлы, названные в вопросе --
#
# Поиск по базе выбирает фрагменты по смыслу среди ВСЕХ доступных файлов.
# Две ситуации это ломало:
#
# 1. Карточка говорит «смотри только в файл 123.docx». Это была просьба к
#    модели, а искал код по всем файлам: от 123.docx могло не прийти ни
#    одного фрагмента, от других - все двенадцать, и модель ими пользовалась.
#    Теперь такое указание ограничивает сам поиск - и общую базу главного,
#    и свою базу ребёнка.
# 2. В вопросе или задании назван файл («что в Проекте ДС № 14?»). Вопрос
#    про ИМЯ, а чанки хранят СОДЕРЖИМОЕ - вектор их не связывает. У главного
#    то же делает pull_named_kb_hits в _handle_direct / multi-LLM. Здесь —
#    тот же приём для субагента.
#
# Указание карточки - строка со словом «только / исключительно / лишь». Что
# в ней ищем:
#   - имя файла из базы (с расширением или без) - этот файл; несколько имён -
#     все они;
#   - имя файла с расширением, которого в базе нет, - «не найден»: просили
#     только его, искать нечего;
#   - после «только», до конца того же предложения, - аббревиатуры из имён
#     файлов (ВНД, СФКА, РСБУ, БГ …), сколько угодно: все файлы, где такая
#     стоит в имени отдельным словом («ВНД_Регламент НЭР.docx», «Методика
#     №ВНД-381-1.docx»).
# Имена можно дать и столбиком в следующих строках («Смотри только в эти
# файлы» и ниже пункты). Строка ниже считается, только если в ней настоящее
# имя файла из базы: правила, которые идут списком после «только»
# («- Используй только факты из документов» и следом другие пункты), так не
# превратятся в файлы. Столбик кончается на первой строке без имени файла:
# «- Не выдумывай» после «- Используй только факты из документов» его
# обрывает, и соседнее правило «- Суть бери из Заявка.docx» не запрёт
# агента в одном файле.
#
# Никакого особого синтаксиса: так и пишут - «смотри только про ВНД»,
# «отвечай только по файлам Проект ДС № 14.docx и Заявка на БГ.docx».
#
# Обычные слова строчными имени файла не задают: по одному слову не отличить
# указание от упоминания - «используй только утверждённые решения» заперло
# бы агента в файле «Проект решения». Аббревиатуры берутся только те, что
# есть в именах файлов этой базы, - «отвечай только в рублях РФ» ничего не
# ограничит.
_SCOPE_RESTRICT_RE = re.compile(
    r"(?<![\w])(только|исключительно|лишь)(?![\w])", re.IGNORECASE
)
# Расширение - любое: 2-5 латинских букв/цифр после точки, первая - буква
# (docx, xlsm, pdf, odt, msg, mp4 …). Без списка: база может содержать что
# угодно. С буквы - чтобы даты и номера («01.04.2026», «3.2.») не сошли за
# имена файлов.
_SCOPE_EXT = r"(?:[A-Za-z][A-Za-z0-9]{1,4})"
_SCOPE_FILE_LIKE_RE = re.compile(
    r"[^\s«»\"'`,;:()\[\]]+\." + _SCOPE_EXT + r"(?![\w])", re.IGNORECASE
)
# Аббревиатура: 2-8 заглавных букв отдельным словом («ВНД», «СФКА», «БГ»).
_SCOPE_ABBR_RE = re.compile(r"(?<![0-9A-Za-zА-Яа-яЁё])[A-ZА-ЯЁ]{2,8}(?![0-9A-Za-zА-Яа-яЁё])")
_SCOPE_WORD_SPLIT_RE = re.compile(r"[^0-9A-Za-zА-Яа-яЁё]+")
_SCOPE_RESTRICT_WORDS = {"ТОЛЬКО", "ИСКЛЮЧИТЕЛЬНО", "ЛИШЬ"}
_SCOPE_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*•—–]|\d{1,3}[.)])\s+")
# Перечень источников после «только» состоит из аббревиатур и кодов (ВНД,
# ВНД-381-1), связок и слов «файл / документ / материал / источник / данные»:
# «только ВНД», «только файлы БГ и ВНД», «только по документам БГ».
_SCOPE_LIST_WORDS = {
    "и", "или", "либо", "а", "также", "в", "во", "по", "про", "из", "на", "о",
    "об", "с", "со", "от", "эти", "этих", "этот", "эту", "это", "этом", "этим",
    "этими", "те", "тех", "том", "тем", "теми", "следующие", "следующих",
    "указанные", "указанных", "перечисленные", "перечисленных",
}
_SCOPE_LIST_NOUNS = ("файл", "документ", "материал", "источник", "данн", "сведени", "информаци")
_SCOPE_TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё]+(?:[-./№][0-9A-Za-zА-Яа-яЁё]+)*|\S")


def _scope_norm(value: str) -> str:
    return (value or "").lower().replace("ё", "е").strip()


def _scope_strip_ext(value: str) -> str:
    return re.sub(r"\." + _SCOPE_EXT + r"$", "", value, flags=re.IGNORECASE)


def _card_file_scope(
    card_prompt: str, id_to_name: Dict[int, str]
) -> Optional[Dict[str, Any]]:
    """Какими файлами карточка ограничивает поиск. None - ограничения нет.

    Возвращает {"ids", "missing", "reasons", "rule"}: какие документы
    оставить, какие указанные файлы не нашлись, по каким строкам промпта это
    решено и что сработало - «имя файла» и/или «аббревиатура».
    """
    names = {int(k): str(v) for k, v in (id_to_name or {}).items() if v}
    normed = {i: _scope_norm(n) for i, n in names.items()}
    # Слова имён заглавными: «ВНД_Регламент НЭР.docx» → {"ВНД", "РЕГЛАМЕНТ", "НЭР", "DOCX"}
    words = {
        i: {w.upper().replace("Ё", "Е") for w in _SCOPE_WORD_SPLIT_RE.split(n) if w}
        for i, n in names.items()
    }
    picked: List[int] = []
    missing: List[str] = []
    reasons: List[str] = []
    kinds: List[str] = []

    def _pick(ids):
        for i in ids:
            if i not in picked:
                picked.append(i)

    def _names_in(text: str):
        """(найденные файлы, имена с расширением, которых в базе нет)."""
        ntext = _scope_norm(text)
        # Имя целиком; без расширения - если не слишком короткое.
        ids = [
            i for i, n in normed.items()
            if n in ntext or (len(_scope_strip_ext(n)) >= 6 and _scope_strip_ext(n) in ntext)
        ]
        # Имя с расширением, взятое из текста куском без пробелов, - сверяем с
        # КОНЦОМ имён («НЭР.docx» из «ВНД_Регламент НЭР.docx»). Не совпало ни
        # с чем - такого файла в базе нет. Хвост уже найденного имени
        # пропускаем: «14.docx» из «Проект ДС № 14.docx» зацепил бы «Прочий
        # файл 14.docx».
        miss: List[str] = []
        for f in _SCOPE_FILE_LIKE_RE.findall(text):
            nf = _scope_norm(f)
            if any(normed[i].endswith(nf) for i in ids):
                continue
            tail = [
                i for i, n in normed.items()
                if n.endswith(nf) or _scope_strip_ext(n) == _scope_strip_ext(nf)
            ]
            if tail:
                ids += [i for i in tail if i not in ids]
            elif f not in miss:
                miss.append(f)
        return ids, miss

    def _abbr_after_restrict(line: str):
        """Аббревиатуры из перечня источников после «только».

        Только ПОСЛЕ «только» и только в ТОМ ЖЕ предложении: длинная строка
        «…ВНД… СФКА… Применяются ТОЛЬКО для сверки параметров…» иначе
        заперла бы агента в файлах, которые в ней просто упомянуты.
        И только если после «только» идёт именно ПЕРЕЧЕНЬ источников: «только
        ВНД», «только файлы БГ и ВНД», «только по документам БГ», «только
        РСХА, ПТА, БГ, ВНД». Перечень кончается концом предложения или знаком
        препинания перед обычным словом («только ВНД, остальное не смотри»).
        Обычное слово сразу за аббревиатурой - это не перечень, а фраза:
        строка правил раздела 11 «Есть только БГ без контрактов - Тип 1»
        заперла субагента в двух файлах про БГ (прогон 24.09), и он ответил
        «данных по клиенту нет».
        Это пересечение с именами файлов базы и только оно: слово капсом,
        которого нет ни в одном имени («ФАЙЛ», «РФ», «РСХА»), ничего не
        значит. Сказать «РСХА не найдена» не нужно - субагент видит свою
        инструкцию и перечень разрешённых файлов и заметит разницу сам.
        """
        out: List[int] = []
        for m in _SCOPE_RESTRICT_RE.finditer(line):
            # Конец предложения - . ! ? ; и точка перед пробелом/концом строки.
            # Двоеточие - нет: «только в документах: ВНД, СФКА» - одно указание.
            sentence = re.split(r"[!?;]|\.(?=\s|$)", line[m.end():], maxsplit=1)[0]
            listed: List[str] = []
            is_list, after_punct = True, False
            for tok in _SCOPE_TOKEN_RE.findall(sentence):
                if not re.search(r"[0-9A-Za-zА-Яа-яЁё]", tok):
                    after_punct = True
                    continue
                low = tok.lower()
                if not re.search(r"[a-zа-яё]", tok):
                    # Капсом, код или число: ВНД, ВНД-381-1, 44-ФЗ.
                    listed += _SCOPE_ABBR_RE.findall(tok)
                elif not (low in _SCOPE_LIST_WORDS or low.startswith(_SCOPE_LIST_NOUNS)):
                    # Обычное слово: после знака препинания перечень просто
                    # кончился, вплотную к нему - это не перечень.
                    is_list = after_punct and bool(listed)
                    break
                after_punct = False
            if not is_list:
                continue
            for a in listed:
                a = a.replace("Ё", "Е")
                if a in _SCOPE_RESTRICT_WORDS:
                    continue
                out += [i for i, ws in words.items() if a in ws and i not in out]
        return out

    lines = (card_prompt or "").splitlines()
    for idx, line in enumerate(lines):
        if not _SCOPE_RESTRICT_RE.search(line):
            continue
        name_ids, miss_here = _names_in(line)
        # Названные файлы вырезаем до поиска аббревиатур: «БГ» из «Заявка на
        # БГ 4 глаза.docx» - часть имени, а не указание «все файлы про БГ».
        rest = line
        for i in name_ids:
            for piece in (names[i], _scope_strip_ext(names[i])):
                rest = re.sub(re.escape(piece), " ", rest, flags=re.IGNORECASE)
        for f in miss_here:
            rest = rest.replace(f, " ")
        # «Не найдены» - только для имени с расширением («123.docx»): там
        # ошибиться нельзя. Для слов капсом - нет: в прогоне 24.09 «Смотри
        # ТОЛЬКО ФАЙЛ 1. Заявка.docx» дало ребёнку «не найдены в базе: ФАЙЛ».
        abbr_ids = _abbr_after_restrict(rest)
        # Имена столбиком в следующих строках.
        for nxt in lines[idx + 1:]:
            if not nxt.strip():
                break
            item_ids, item_miss = _names_in(nxt)
            if item_ids:
                name_ids += [i for i in item_ids if i not in name_ids]
                # Имя с расширением, которого нет, - только если строка и есть
                # это имя («- 555.docx»), а не правило, где оно упомянуто.
                bare = _SCOPE_LIST_ITEM_RE.sub("", nxt).strip().rstrip(",;.")
                miss_here += [f for f in item_miss if f == bare and f not in miss_here]
                continue
            bare = _SCOPE_LIST_ITEM_RE.sub("", nxt).strip().rstrip(",;.")
            if item_miss and item_miss == [bare]:
                miss_here += [f for f in item_miss if f not in miss_here]
                continue
            break
        if name_ids or miss_here or abbr_ids:
            reasons.append(line.strip()[:160])
            _pick(name_ids)
            _pick(abbr_ids)
            missing += [m for m in miss_here if m not in missing]
            if name_ids or miss_here:
                kinds.append("имя файла")
            if abbr_ids:
                kinds.append("аббревиатура")
    if not reasons:
        return None
    rule = " + ".join(dict.fromkeys(kinds))
    return {"ids": picked, "missing": missing, "reasons": reasons, "rule": rule}


async def _resolve_card_scope(
    child_profile: Dict[str, Any],
    shared_kb_ids: List[int],
    target_agent_id: int,
) -> Optional[List[int]]:
    """Файлы, которыми карточка ребёнка ограничивает поиск. None - без ограничений.

    Сопоставление идёт только среди файлов, доступных ребёнку: общая база
    главного плюс своя. Итог - в child_profile: "_scope_doc_ids" читает поиск
    по своей базе (_apply_child_kb_context), "_scope_info" - лог и подсказка
    ребёнку. Общую базу раннер фильтрует по возвращённому списку.
    """
    card = str(child_profile.get("system_prompt") or "")
    if not _SCOPE_RESTRICT_RE.search(card):
        return None
    available: List[int] = []
    for v in [*(shared_kb_ids or []), *(child_profile.get("kb_document_ids") or [])]:
        try:
            iv = int(v)
        except (TypeError, ValueError):
            continue
        if iv not in available:
            available.append(iv)
    if not available:
        return None
    try:
        from backend.app_state import rag_client
        from backend.realtime.rag_evidence import build_rag_id_to_filename

        if rag_client is None:
            return None
        all_names = build_rag_id_to_filename(list(await rag_client.kb_list_documents() or []))
    except Exception:
        # Без имён ограничить нечем: ищем как раньше, по всем файлам.
        log.exception(
            "subagent agent_id=%s: имена файлов для ограничения поиска не получены",
            target_agent_id,
        )
        return None
    names = {i: all_names[i] for i in available if i in all_names}
    scope = _card_file_scope(card, names)
    if scope is None:
        return None
    child_profile["_scope_doc_ids"] = list(scope["ids"])
    child_profile["_scope_info"] = {
        "files": [names[i] for i in scope["ids"]],
        "missing": list(scope["missing"]),
        "rule": scope["rule"],
    }
    log.info(
        "[subagent-scope] agent_id=%s «%s»: поиск ограничен карточкой (правило «%s») "
        "файлы=%s не_найдены=%s строка=%r",
        target_agent_id,
        child_profile.get("name") or f"agent_{target_agent_id}",
        scope["rule"],
        [names[i] for i in scope["ids"]],
        scope["missing"],
        scope["reasons"][0] if scope["reasons"] else "",
    )
    return list(scope["ids"])


def _card_scope_note(child_profile: Dict[str, Any]) -> str:
    """Подсказка ребёнку, что поиск ограничен его же инструкцией.

    Без неё модель, не найдя фрагментов, сказала бы «в базе нет» про всю
    базу - а искали только в указанных файлах.
    """
    scope = child_profile.get("_scope_info")
    if not scope:
        return ""
    files = scope.get("files") or []
    missing = scope.get("missing") or []
    if files:
        note = "Поиск по документам ограничен файлами из твоей инструкции: " + ", ".join(files) + "."
        if missing:
            note += " Не найдены в базе: " + ", ".join(missing) + "."
        return note
    return (
        "Файлы, указанные в твоей инструкции, в базе не найдены: "
        + ", ".join(missing or ["-"])
        + ". Поиск по документам не выполнялся: если вопрос требует этих файлов, так и скажи."
    )


async def _pull_named_documents(
    rag_client: Any,
    query: str,
    hits: List[Any],
    id_to_name: Dict[int, str],
    doc_ids: List[Any],
    k: int,
    child_profile: Optional[Dict[str, Any]],
    strategy: Optional[str] = None,
) -> List[Any]:
    """Файл назван в вопросе или задании - дотянуть его фрагменты отдельно.

    Тот же приём, что у главного в _handle_direct. Ищем только среди doc_ids
    (уже суженных карточкой): назвать чужой файл и так его получить нельзя.
    Не больше трёх файлов (limit в documents_named_in_query).
    """
    try:
        from backend.realtime.rag_evidence import (
            documents_named_in_query,
            merge_extra_hits,
        )

        named = documents_named_in_query(query, id_to_name, only_ids=list(doc_ids or []))
    except Exception:
        log.exception("subagent: не удалось найти файлы, названные в задании")
        return hits
    for doc_id in named:
        try:
            extra = await kb_search_agent_documents(
                rag_client, query, [doc_id], k=k, strategy=strategy
            )
        except Exception:
            log.exception("subagent: не удалось дотянуть файл %s по имени", doc_id)
            continue
        before = len(hits)
        hits = merge_extra_hits(hits, list(extra or []))
        if isinstance(child_profile, dict):
            child_profile.setdefault("_named_files", []).append(
                f"{id_to_name.get(int(doc_id)) or doc_id} (+{len(hits) - before})"
            )
    return hits
