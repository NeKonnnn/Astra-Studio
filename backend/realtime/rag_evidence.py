"""
Порог релевантности RAG и короткий ответ без вызова LLM, если в документах нет опоры.

Скоры из SVC-RAG: cosine similarity ≈ 1 - (embedding <=> query), обычно в [0, 1].
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from backend.rag_query.prompts import RAG_STRICT_NOT_FOUND_MESSAGE
from backend.settings.logging import get_logger

logger = get_logger(__name__)

RAG_NO_RELEVANT_CONTEXT_MESSAGE = RAG_STRICT_NOT_FOUND_MESSAGE


def _rag_env_flag(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def memory_rag_enabled() -> bool:
    """Глобальный выключатель Библиотеки: ```RAG_MEMORY_ENABLED=0```

    Выключает ИСТОЧНИК, а не кнопку в UI: тумблер у пользователя остаётся, но
    memory-rag в поиск не попадает ни в одном режиме чата. Нужен, когда
    Библиотека наполнена мусором или переиндексируется, а ронять остальной RAG
    нельзя
    """
    return _rag_env_flag("RAG_MEMORY_ENABLED", True)


def build_rag_id_to_filename(rows: Optional[List[Any]]) -> Dict[int, str]:
    """Сопоставление id документа в SVC-RAG с именем файла для подписей в промпте (без вывода числового id)."""
    out: Dict[int, str] = {}
    if not rows:
        return out
    for d in rows:
        if not isinstance(d, dict):
            continue
        raw_id = d.get("id")
        if raw_id is None:
            continue
        try:
            key = int(raw_id)
        except (TypeError, ValueError):
            continue
        name = d.get("filename")
        label = (str(name).strip() if name else "") or str(key)
        out[key] = label
    return out


def rag_document_label(doc_id: Optional[Any], id_to_name: Dict[int, str]) -> str:
    """Подпись источника фрагмента для LLM: имя файла, не внутренний document_id."""
    if doc_id is None:
        return "неизвестный документ"
    try:
        key = int(doc_id)
    except (TypeError, ValueError):
        return "неизвестный документ"
    name = id_to_name.get(key)
    if name and str(name).strip():
        return str(name).strip()
    return "документ без имени"


_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"})


def rag_fragment_source(title: Any) -> str:
    """Человекочитаемая подпись источника фрагмента по имени файла."""
    name = str(title or "").strip() or "без имени"
    ext = ""
    if "." in name:
        ext = "." + name.rsplit(".", 1)[-1].lower()
    if ext in _IMAGE_EXTS:
        return f"текст, распознанный на изображении «{name}»"
    return f"документ «{name}»"


def rag_scoped_filenames(
    id_to_name: Dict[int, str],
    only_ids: Optional[List[Any]] = None,
) -> List[str]:
    """Имена файлов хранилища; only_ids сужает перечень до документов сущности.

    Для Базы Знаний это обязательно: kb_list_documents отдаёт всю KB, а
    агент видит только свои kb_document_ids. Показать ему чужие файлы -
    утечка, пусть и одними именами.

    only_ids=None - без сужения (проект и библиотека и так свои).
    Пустой список - пустой перечень, а не "всё".
    """
    names = dict(id_to_name or {})
    if only_ids is not None:
        keep = set()
        for raw in only_ids or []:
            try:
                keep.add(int(raw))
            except (TypeError, ValueError):
                continue
        names = {k: v for k, v in names.items() if k in keep}
    out = [str(v).strip() for v in names.values() if str(v or "").strip()]
    return sorted(set(out))


# Основа имени короче — не признак: файл «1.pdf» совпал бы с любой цифрой
# в вопросе. Полное имя с расширением проверяется всегда.
_NAMED_DOC_MIN_STEM = 3


def documents_named_in_query(
    query: str,
    id_to_name: Dict[int, str],
    *,
    only_ids: Optional[List[Any]] = None,
    limit: int = 3,
) -> List[int]:
    """document_id тех документов, чьи имена названы в вопросе.

    Вопрос «что в файле image.png?» семантически про ИМЯ файла, а чанки
    хранят его СОДЕРЖИМОЕ. Вектор их не связывает, и поиск возвращает
    пусто - хотя документ проиндексирован. Находим его по имени, чтобы
    дотянуть чанки отдельным запросом.

    only_ids сужает перечень (для KB - документы агента: kb_list_documents
    отдаёт всю базу, и без сужения можно было бы вытащить чужой файл,
    просто назвав его). None - без сужения.

    limit держит разумным число дополнительных поисков.
    """
    q = (query or "").lower()
    if not q:
        return []

    allowed: Optional[set] = None
    if only_ids is not None:
        allowed = set()
        for raw in only_ids or []:
            try:
                allowed.add(int(raw))
            except (TypeError, ValueError):
                continue

    found: List[int] = []
    for raw_id, raw_name in (id_to_name or {}).items():
        try:
            doc_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if allowed is not None and doc_id not in allowed:
            continue
        name = str(raw_name or "").strip().lower()
        if not name:
            continue
        hit = name in q
        if not hit:
            stem = name.rsplit(".", 1)[0].strip()
            if stem != name and len(stem) >= _NAMED_DOC_MIN_STEM:
                hit = stem in q
        if hit:
            found.append(doc_id)
    found.sort()
    return found[: max(0, int(limit))]


def merge_extra_hits(primary: List[Any], extra: List[Any]) -> List[Any]:
    """Добавить недостающие фрагменты к основным, без дублей.

    Ключ - пара (документ, чанк). Порядок основных не меняется:
    дотянутое дописывается в хвост.
    """
    out = list(primary or [])
    seen = set()
    for hit in out:
        try:
            _, _, doc_id, chunk_idx = hit
        except (TypeError, ValueError):
            continue
        seen.add((doc_id, chunk_idx))
    for hit in extra or []:
        try:
            _, _, doc_id, chunk_idx = hit
        except (TypeError, ValueError):
            continue
        if (doc_id, chunk_idx) in seen:
            continue
        seen.add((doc_id, chunk_idx))
        out.append(hit)
    return out


def build_rag_inventory_block(
    sections: List[Tuple[str, List[str]]],
) -> str:
    """Перечень доступных файлов - в начало CONTEXT, всегда и целиком.
    Пустой результат - если выключено или показывать нечего.
    """
    try:
        from backend.rag_query.prompts import documents_inventory_enabled

        if not documents_inventory_enabled():
            return ""
    except Exception:
        logger.exception("Перечень файлов: не удалось прочитать настройку")

    lines: List[str] = []
    for label, names in sections or []:
        names = [n for n in (names or []) if n]
        if not names:
            continue
        lines.append(f"{label} ({len(names)}): " + ", ".join(names))
    if not lines:
        return ""
    return "ДОСТУПНЫЕ ФАЙЛЫ (полный перечень того, что загружено):\n" + "\n".join(lines)


class ActiveRagSources:
    """Какие RAG-источники активны для текущего запроса.

    Единое правило подмешивания (одинаковое для direct / multi-LLM / agent):

      * ``memory``    — библиотека памяти (кнопка «Библиотека»); только если тумблер включён;
      * ``agent_kb``  — документы выбранного агента; только если у агента включён
                        file_search и есть привязанные kb_document_ids;
      * ``project``   — RAG-документы проекта; только если чат открыт в проекте.

    Все активные источники используются вместе (аддитивно). Выключенные —
    НИКОГДА не подмешиваются. Это гарантирует, что, например, memory-only в
    обычном чате не приводит к утечке project/agent документов, а project-only
    не тянет memory/agent.
    """

    __slots__ = ("project", "agent_kb", "memory")

    def __init__(self, *, project: bool, agent_kb: bool, memory: bool) -> None:
        self.project = bool(project)
        self.agent_kb = bool(agent_kb)
        self.memory = bool(memory)

    @property
    def any(self) -> bool:
        return self.project or self.agent_kb or self.memory

    def as_dict(self) -> Dict[str, bool]:
        return {
            "project": self.project,
            "agent_kb": self.agent_kb,
            "memory": self.memory,
        }

    def store_list(self) -> List[str]:
        """Имена SVC-RAG store'ов, разрешённые к поиску (для agentic tool)."""
        out: List[str] = []
        if self.project:
            out.append("project")
        if self.agent_kb:
            out.append("kb")
        if self.memory:
            out.append("memory")
        return out


def resolve_active_rag_sources(
    *,
    project_id: Optional[Any],
    use_agent_scoped_kb: bool,
    agent_kb_doc_ids: Optional[List[Any]],
    use_memory_library_rag: bool,
    use_kb_rag: bool = False,
) -> ActiveRagSources:
    """Единая точка решения, какие источники RAG активны.

    Используется всеми режимами чата и agentic-tool'ом, чтобы правило было
    одинаковым и тестируемым.

    ВАЖНО про «Библиотеку»: в UI одна кнопка «Библиотека» ставит СРАЗУ два флага
    (``use_kb_rag`` и ``use_memory_library_rag``) — это единый тумблер «искать в
    моих загруженных документах». Документы, загруженные через «Открыть базу
    данных» в настройках RAG, лежат в memory-rag. Поэтому memory-поиск включаем
    по ЛЮБОМУ из этих флагов: иначе, если фронт по какой-то причине прислал
    только ``use_kb_rag`` (а он исторически был no-op для retrieval), библиотека
    не искалась вообще и чат отвечал «не нашёл», хотя документы загружены.
    """
    project = bool(project_id)
    agent_kb = (
        bool(use_agent_scoped_kb)
        and bool(agent_kb_doc_ids)
        and len(agent_kb_doc_ids) > 0
    )
    memory = bool(use_memory_library_rag) or bool(use_kb_rag)
    if memory and not memory_rag_enabled():
        logger.info(
            "[RAG-SOURCES] Библиотека выключена глобально (RAG_MEMORY_ENABLED=0) - "
            "тумблер пользователя игнорируем"
        )
        memory = False
    sources = ActiveRagSources(project=project, agent_kb=agent_kb, memory=memory)
    logger.debug(
        "[RAG-SOURCES] active=%s (project_id=%s, use_agent_scoped_kb=%s, agent_kb_docs=%s, "
        "use_memory_library_rag=%s, use_kb_rag=%s)",
        sources.as_dict(),
        project_id,
        use_agent_scoped_kb,
        len(agent_kb_doc_ids or []),
        use_memory_library_rag,
        use_kb_rag,
    )
    return sources


def should_block_rag_send(
    status: Dict[str, Any],
    *,
    library_enabled: bool,
    project_has_documents: bool = False,
    agent_has_kb: bool = False,
) -> bool:
    """Зеркало фронтового shouldBlockRagSend — блок только затронутых RAG-источников."""
    if not status:
        return False
    if library_enabled and bool((status.get("memory") or {}).get("reindexing")):
        return True
    if project_has_documents and bool((status.get("project") or {}).get("reindexing")):
        return True
    if agent_has_kb and bool((status.get("kb") or {}).get("reindexing")):
        return True
    return False


async def rag_reindex_blocks_active_sources(
    sources: ActiveRagSources,
    rag_client: Any,
    *,
    project_id: Optional[Any] = None,
) -> bool:
    """Proactive guard: lock SVC-RAG до вызова search."""
    if not sources.any or not rag_client:
        return False
    try:
        status = await rag_client.get_reindex_status()
    except Exception:
        logger.warning("[RAG] proactive reindex status check failed", exc_info=True)
        return False
    if sources.memory and bool((status.get("memory") or {}).get("reindexing")):
        return True
    if sources.project and bool((status.get("project") or {}).get("reindexing")):
        if not project_id:
            return False
        try:
            docs = list(await rag_client.project_rag_list_documents(project_id) or [])
        except Exception:
            logger.warning(
                "[RAG] project_rag_list_documents failed during reindex guard",
                exc_info=True,
            )
            return False
        if docs:
            return True
    if sources.agent_kb and bool((status.get("kb") or {}).get("reindexing")):
        return True
    return False


def _entity_titles(active: Optional[list]) -> list:
    """«агента «Юрист»» / «проекта «Отчётность»» — по одной строке на сущность."""
    out: list = []
    for item in active or []:
        if not isinstance(item, dict):
            continue
        kind = "агента" if str(item.get("scope") or "") == "agent" else "проекта"
        name = str(item.get("name") or "").strip()
        eid = str(item.get("entity_id") or "").strip()
        title = f"{kind} «{name}»" if name else f"{kind} {eid}".strip()
        if title not in out:
            out.append(title)
    return out


def build_reindex_status_message(
    *,
    memory_reindexing: bool,
    project_reindexing: bool,
    kb_reindexing: bool,
    active: Optional[list] = None,
) -> str:
    # Пересобирается конкретная сущность — называем её: плашка видна всем, кто с
    # ней работает, и «идёт перечанковка базы знаний агента» без имени в такой
    # ситуации не отвечает на вопрос «какого именно».
    titles = _entity_titles(active)
    if titles and not memory_reindexing:
        stores = (
            titles[0]
            if len(titles) == 1
            else ", ".join(titles[:-1]) + f" и {titles[-1]}"
        )
        return (
            f"Идёт пересборка {stores}. "
            "Поиск по ним временно недоступен — дождитесь завершения, иначе ответ может быть «Не знаю»."
        )
    parts: list[str] = []
    if memory_reindexing:
        parts.append("Библиотеки")
    if project_reindexing:
        parts.append("документов проекта")
    if kb_reindexing:
        parts.append("базы знаний агента")
    if not parts:
        return ""
    if len(parts) == 1:
        stores = parts[0]
    elif len(parts) == 2:
        stores = f"{parts[0]} и {parts[1]}"
    else:
        stores = f"{parts[0]}, {parts[1]} и {parts[2]}"
    return (
        f"Идёт перечанковка {stores}. "
        "Поиск по базе временно недоступен — дождитесь завершения, иначе ответ может быть «Не знаю»."
    )


def rag_guard_env(scope: Optional[str] = None) -> Tuple[float, bool]:
    """(min_similarity, block_on_no_evidence).

    ``scope`` — стор, чьи хиты фильтруем: "agent" или "project". Порог теперь
    у каждой сущности свой, и общий на оба стора обрезал бы выдачу не тем числом.

    ВАЖНО: по умолчанию backend НЕ отсекает по score (min_sim=0), потому что SVC-RAG уже
    применил ``min_vector_similarity`` + rescue top-N, и результат может быть в разных
    шкалах (чистый cosine / смесь cross-encoder+cosine / BM25 RRF). Любой дополнительный
    порог здесь снова обнуляет recall. Включать RAG_MIN_SIMILARITY>0 имеет смысл только
    когда вы умышленно отключили rescue и все шкалы приведены к [0, 1].

    RAG_BLOCK_ON_NO_EVIDENCE=1 (по умолчанию) — если после всех фильтров контекст действительно
    пуст, backend сам отвечает канонической фразой без вызова LLM. Чтобы ВСЕГДА звать LLM
    (даже без опоры на документы), поставьте RAG_BLOCK_ON_NO_EVIDENCE=0.
    """
    min_sim = None
    try:
        from backend.services.user_rag_settings import runtime_rag_similarity_threshold

        min_sim = float(runtime_rag_similarity_threshold(scope))
    except Exception:
        logger.exception("RAG similarity threshold runtime read")
        min_sim = None
    if min_sim is None:
        try:
            import backend.app_state as state

            min_sim = float(getattr(state, "rag_similarity_threshold", 0.0))
        except Exception:
            logger.exception("RAG similarity threshold state read")
            min_sim = None
    if min_sim is None:
        try:
            min_sim = float(os.getenv("RAG_MIN_SIMILARITY", "0"))
        except ValueError:
            min_sim = 0.0
    min_sim = max(0.0, min(min_sim, 1.0))
    block = os.getenv("RAG_BLOCK_ON_NO_EVIDENCE", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )
    return (min_sim, block)


def filter_rag_hits_by_score(
    hits: Optional[List[Tuple[str, float, Optional[int], Optional[int]]]],
    min_score: float,
) -> List[Tuple[str, float, Optional[int], Optional[int]]]:
    if not hits:
        return []
    if min_score <= 0.0:
        return list(hits)
    out: List[Tuple[str, float, Optional[int], Optional[int]]] = []
    raw_scores: List[float] = []
    for h in hits:
        try:
            s = float(h[1])
            raw_scores.append(s)
            if s >= min_score:
                out.append(h)
        except (TypeError, ValueError):
            continue
    if not out and raw_scores:
        mx = max(raw_scores)
        if mx < min_score and mx < 0.0:
            logger.debug(
                "[RAG] Порог RAG_MIN_SIMILARITY=%s не применён: шкала похожа на реранк (max score=%.4f < 0). Оставляем хиты как отдал SVC-RAG; при необходимости задайте RAG_RERANK_MIN_SCORE в svc-rag.",
                min_score,
                mx,
            )
            return list(hits)
        if 0.0 <= mx < min_score:
            try:
                rescue = int(os.getenv("RAG_RESCUE_LOW_SCORE_TOP", "10"))
            except ValueError:
                rescue = 10
            rescue = max(4, min(rescue, 24))
            ranked = sorted(
                [h for h in hits if len(h) > 1], key=lambda h: float(h[1]), reverse=True
            )
            if ranked:
                logger.debug(
                    "[RAG] max score=%.4f < RAG_MIN_SIMILARITY=%s — спасение recall: топ-%s чанков.",
                    mx,
                    min_score,
                    rescue,
                )
                return ranked[:rescue]
        logger.debug(
            "[RAG] Все %s хитов отсечены порогом RAG_MIN_SIMILARITY=%s (макс. score до фильтра: %.4f). Уменьшите RAG_MIN_SIMILARITY в окружении, если ответы есть в документах, но контекст пуст.",
            len(hits),
            min_score,
            mx,
        )
    return out


def _format_single_fragment(
    row: Tuple[str, float, Optional[int], Optional[int]],
    *,
    number: int,
    id_to_name: Dict[int, str],
    include_chunk_meta: bool,
) -> Optional[str]:
    try:
        content, score, doc_id, chunk_idx = row
    except (TypeError, ValueError):
        return None
    title = rag_document_label(doc_id, id_to_name)
    source = rag_fragment_source(title)
    if include_chunk_meta:
        try:
            from backend.rag_query.relevance import (
                relevance_percent_or_none,
                score_to_relevance_percent,
            )

            # По сырому cosine, если он доехал из SVC-RAG: score здесь может
            # быть в шкале стратегии (RRF / логит реранкера), и процент по нему
            # ввёл бы модель в заблуждение.
            sc_pct = relevance_percent_or_none(row)
            if sc_pct is None:
                sc_pct = score_to_relevance_percent(score)
        except Exception:
            sc_pct = 1
        return f"Фрагмент {number} ({source}, чанк {chunk_idx}, релевантность: {sc_pct}%):\n{content}\n"
    return f"Фрагмент {number} ({source}): {content}\n"


def hits_to_document_search_trace(
    *,
    query: str,
    hits: Optional[List[Tuple[str, float, Optional[int], Optional[int]]]],
    id_to_name: Dict[int, str],
    store: str = "kb",
    strategy: str = "auto",
) -> Optional[Dict[str, Any]]:
    """Собрать document_search для UI из сырых RAG-хитов (как в handlers)."""
    if not hits:
        return None
    try:
        from backend.rag_query.relevance import hits_to_relevance_percents
    except Exception:
        return None

    pending: List[Tuple[Any, str]] = []
    files_used: set = set()
    for hit in hits:
        try:
            content, _score, doc_id, chunk_idx = hit
        except (TypeError, ValueError):
            continue
        if doc_id is None:
            continue
        try:
            fn = id_to_name.get(int(doc_id))
        except (TypeError, ValueError):
            fn = None
        if not fn:
            fn = f"doc*{doc_id}"
        files_used.add(fn)
        pending.append((hit, fn))
    if not pending:
        return None
    pcts = hits_to_relevance_percents([row[0] for row in pending])
    hits_out: List[Dict[str, Any]] = []
    for ((content, _score, doc_id, chunk_idx), fn), pct in zip(pending, pcts):
        hits_out.append(
            {
                "file": fn,
                "anchor": f"chunk@{chunk_idx}({fn})",
                "relevance": int(pct) if pct is not None else 1,
                "content": (content or "")[:12000],
                "chunkIndex": chunk_idx,
                "documentId": doc_id,
                "store": store,
            }
        )
    return {
        "query": query or "",
        "strategy": strategy,
        "sourceFiles": sorted(files_used),
        "hits": hits_out,
    }


def merge_document_search_traces(
    *traces: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Склеить несколько document_search одного субагента в одну карточку."""
    merged_hits: List[Dict[str, Any]] = []
    files: set = set()
    query = ""
    strategy = "auto"
    for trace in traces:
        if not isinstance(trace, dict):
            continue
        if not query:
            query = str(trace.get("query") or "")
        if trace.get("strategy"):
            strategy = str(trace.get("strategy") or strategy)
        for fn in trace.get("sourceFiles") or []:
            if fn:
                files.add(str(fn))
        for hit in trace.get("hits") or []:
            if isinstance(hit, dict):
                merged_hits.append(hit)
                if hit.get("file"):
                    files.add(str(hit["file"]))
    if not merged_hits and not files:
        return None
    return {
        "query": query,
        "strategy": strategy,
        "sourceFiles": sorted(files),
        "hits": merged_hits,
    }


def format_rag_fragments(
    hits: Optional[List[Tuple[str, float, Optional[int], Optional[int]]]],
    id_to_name: Dict[int, str],
    *,
    max_chars: int,
    store_label: str,
    include_chunk_meta: bool = False,
    # По умолчанию False: номер чанка и релевантность - служебная разметка, модель
    # копирует ее в ответ пользователю. Значения остаются в логах и рассировке.
    truncate_marker: str = "\n... [обрезано]\n",
) -> Tuple[List[str], Dict[str, int]]:
    """Единая формализация RAG-хитов в текстовые фрагменты с бюджетом длины.

    Два фундаментальных свойства:

    1. **Round-robin по документам**. Раньше обход был линейный: брали чанки
       в порядке SVC-RAG, и как только бюджет кончался — break. Если первые
       несколько документов были крупными (по 6 жирных чанков), остальные
       документы ВООБЩЕ не попадали в промпт — даже если были семантически
       самыми релевантными. Пользователь видел это как «SVC-RAG нашёл 3
       документа, но LLM ответил будто видел только один».

       Теперь: сначала по одному чанку от каждого document_id (в порядке
       первого появления в hits) — это гарантирует coverage всех документов.
       Затем второй проход берёт следующие чанки документов по порядку, пока
       не кончится бюджет. Итоговая нумерация фрагментов сохраняет исходный
       порядок hits (важно для подписей `Фрагмент N`).

    2. **Явный лог согласования метрик** SVC-RAG ↔ LLM-промпт:

           [RAG/fragments] store=memory: получено=17, попало_в_промпт_целиком=12,
           документов_в_промпт=3/3, последний_обрезан=1, отброшено=4, длина=9873

    Возвращает:
      * parts — готовые строки для `"\\n".join(parts)`;
      * metrics — dict для верхнего лога и, при желании, для ответа API.
    """
    hits = hits or []
    metrics: Dict[str, int] = {
        "received": len(hits),
        "used_full": 0,
        "truncated_last": 0,
        "dropped": 0,
        "total_chars": 0,
        "documents_input": 0,
        "documents_in_prompt": 0,
    }
    if not hits:
        return ([], metrics)
    doc_order: List[Optional[int]] = []
    doc_seen: set = set()
    for row in hits:
        try:
            _, _, d_id, _ = row
        except (TypeError, ValueError):
            continue
        if d_id not in doc_seen:
            doc_seen.add(d_id)
            doc_order.append(d_id)
    metrics["documents_input"] = len(doc_order)
    indexed: List[
        Tuple[int, Optional[int], Tuple[str, float, Optional[int], Optional[int]]]
    ] = []
    for i, row in enumerate(hits):
        try:
            _, _, d_id, _ = row
        except (TypeError, ValueError):
            continue
        indexed.append((i + 1, d_id, row))
    by_doc: Dict[Optional[int], List[int]] = {}
    for num, d_id, _row in indexed:
        by_doc.setdefault(d_id, []).append(num - 1)
    selected_entries: List[Optional[str]] = [None] * len(indexed)
    selected_doc_ids: set = set()
    total = 0
    truncated_last = False
    reserve_for_marker = len(truncate_marker)

    def _try_add(idx: int) -> bool:
        nonlocal total
        if selected_entries[idx] is not None:
            return True
        num, d_id, row = indexed[idx]
        frag = _format_single_fragment(
            row,
            number=num,
            id_to_name=id_to_name,
            include_chunk_meta=include_chunk_meta,
        )
        if frag is None:
            return False
        if total + len(frag) + reserve_for_marker > max_chars:
            return False
        selected_entries[idx] = frag
        selected_doc_ids.add(d_id)
        total += len(frag)
        return True

    for d_id in doc_order:
        idxs = by_doc.get(d_id) or []
        for idx in idxs:
            if _try_add(idx):
                break
    for idx, (num, d_id, row) in enumerate(indexed):
        if selected_entries[idx] is not None:
            continue
        if _try_add(idx):
            continue
        if total + reserve_for_marker < max_chars:
            frag = _format_single_fragment(
                row,
                number=num,
                id_to_name=id_to_name,
                include_chunk_meta=include_chunk_meta,
            )
            if frag is not None:
                tail = max(0, max_chars - total - reserve_for_marker)
                selected_entries[idx] = frag[:tail] + truncate_marker
                selected_doc_ids.add(d_id)
                total += len(selected_entries[idx])
                truncated_last = True
        break
    parts: List[str] = [p for p in selected_entries if p is not None]
    used_count = len(parts)
    metrics["used_full"] = used_count - (1 if truncated_last else 0)
    metrics["truncated_last"] = 1 if truncated_last else 0
    metrics["dropped"] = max(0, len(indexed) - used_count)
    metrics["total_chars"] = total
    metrics["documents_in_prompt"] = len(selected_doc_ids)
    logger.debug(
        "[RAG/fragments] store=%s: получено=%d, попало_в_промпт_целиком=%d, документов_в_промпт=%d/%d, последний_обрезан=%d, отброшено_после_лимита=%d, длина=%d/%d",
        store_label,
        metrics["received"],
        metrics["used_full"],
        metrics["documents_in_prompt"],
        metrics["documents_input"],
        metrics["truncated_last"],
        metrics["dropped"],
        metrics["total_chars"],
        max_chars,
    )
    _log_selected_chunks(
        store_label=store_label,
        indexed=indexed,
        selected_entries=selected_entries,
        id_to_name=id_to_name,
        truncated_last=truncated_last,
    )
    return (parts, metrics)


def _log_selected_chunks(
    *,
    store_label: str,
    indexed: List[
        Tuple[int, Optional[int], Tuple[str, float, Optional[int], Optional[int]]]
    ],
    selected_entries: List[Optional[str]],
    id_to_name: Dict[int, str],
    truncated_last: bool,
) -> None:
    """Поимённо: какие чанки ушли в промпт, а какие не поместились.

    Счётчиков ```[RAG/fragments]``` не хватает, чтобы разобрать конкретный ответ:
    они говорят «попало 12 из 17», но не какие именно. Здесь видно каждый
    кандидат, его релевантность и попал ли он в CONTEXT — то есть на что
    модель реально могла опираться.
    """
    if not logger.isEnabledFor(10) or not indexed:  # DEBUG
        return
    last_selected = max(
        (i for i, entry in enumerate(selected_entries) if entry is not None),
        default=-1,
    )
    lines = []
    for idx, (num, d_id, row) in enumerate(indexed):
        try:
            content, score, _doc_id, chunk_idx = row
        except (TypeError, ValueError):
            continue
        used = selected_entries[idx] is not None
        mark = "✓" if used else "✗"
        if used and truncated_last and idx == last_selected:
            mark = "▲"  # попал, но обрезан по бюджету
        title = id_to_name.get(d_id, f"doc_{d_id}") if d_id is not None else "?"
        preview = " ".join(str(content or "").split())[:80]
        # score — в шкале отработавшей стратегии, cosine — абсолютная близость.
        # Рядом видно и то, и другое: иначе по логу не понять, был ли чанк
        # действительно похож на запрос или просто оказался высоко по рангу.
        cos = getattr(row, "cosine", None)
        cos_text = "cos=н/д  " if cos is None else f"cos={float(cos):.4f}"
        lines.append(
            f"{mark} фрагмент {num:>2}  score={float(score or 0):.4f}"
            f"{cos_text}чанк {chunk_idx}  «{title}»  {preview}…"
        )
    logger.debug(
        "[RAG/выбор] store=%s: что ушло в CONTEXT (✓ целиком, ▲ обрезан, ✗ не поместился):\n%s",
        store_label,
        "\n".join(lines),
    )


async def fetch_rag_store_counts(
    rag_client: Any,
    *,
    project_id: Optional[str],
    use_kb_rag: bool,
    use_agent_scoped_kb: bool,
    agent_kb_doc_ids: Optional[List[Any]],
    use_memory_library_rag: bool,
) -> Dict[str, int]:
    """Число документов по хранилищам (для решения, ожидались ли ответы из корпуса)."""
    out: Dict[str, int] = {"project": 0, "kb": 0, "memory": 0, "agent_kb": 0}
    if not rag_client:
        return out
    if project_id:
        try:
            docs = await rag_client.project_rag_list_documents(project_id)
            out["project"] = len(docs) if isinstance(docs, list) else 0
        except Exception:
            logger.exception("project_rag_list_documents")
    kb_list: List[dict] = []
    if use_agent_scoped_kb:
        try:
            kb_list = await rag_client.kb_list_documents()
            if not isinstance(kb_list, list):
                kb_list = []
            out["kb"] = len(kb_list)
        except Exception:
            logger.exception("kb_list_documents")
    if use_agent_scoped_kb and agent_kb_doc_ids and kb_list:
        want = {
            int(x) for x in agent_kb_doc_ids if str(x).isdigit() or isinstance(x, int)
        }
        n = 0
        for d in kb_list:
            try:
                if int(d.get("id", -1)) in want:
                    n += 1
            except (TypeError, ValueError):
                continue
        out["agent_kb"] = n
    if use_memory_library_rag:
        try:
            docs = await rag_client.memory_rag_list_documents()
            out["memory"] = len(docs) if isinstance(docs, list) else 0
        except Exception:
            logger.exception("memory_rag_list_documents")
    return out


async def maybe_rag_no_evidence_message(
    rag_client: Any,
    *,
    block_when_no_evidence: bool,
    context_added: bool,
    project_id: Optional[str],
    use_kb_rag: bool,
    use_memory_library_rag: bool,
    use_agent_scoped_kb: bool,
    agent_kb_doc_ids: Optional[List[Any]],
    has_history: bool = False,
) -> Optional[str]:
    """
    Если включён блок и в промпт не попал ни один фрагмент, но при этом был непустой
    корпус в задействованных хранилищах (project / kb / memory) — возвращает готовый текст
    ответа без LLM. Global store больше не учитывается.

    'has_history' - в диалоге уже есть предыдущие сообщения. Тогда короткий
    ответ без LLM не даём: пользователь мог спросить про то, что обсуждали выше, а
    гард отвечает «не нашёл в базе документов», ни разу не заглянув в переписку.
    Решение отдаём модели — у неё на руках и история, и правила ответа, включая
    требование не выдумывать конкретику.
    """
    if not block_when_no_evidence or context_added or (not rag_client):
        return None
    if has_history:
        logger.info(
            "[RAG-NO-EVIDENCE] Контекст пуст, но в диалоге есть история — "
            "отвечает модель, а не гард"
        )
        return None
    counts = await fetch_rag_store_counts(
        rag_client,
        project_id=project_id,
        use_kb_rag=use_kb_rag,
        use_agent_scoped_kb=use_agent_scoped_kb,
        agent_kb_doc_ids=agent_kb_doc_ids,
        use_memory_library_rag=use_memory_library_rag,
    )
    doc_backed = (
        bool(project_id and counts["project"] > 0)
        or (use_agent_scoped_kb and counts.get("agent_kb", 0) > 0)
        or (use_memory_library_rag and counts["memory"] > 0)
    )
    if not doc_backed:
        return None
    logger.info(
        "[RAG-NO-EVIDENCE] Блок ответа без опоры: контекст пуст при непустом корпусе. "
        "counts=%s project_id=%s use_kb_rag=%s use_agent_scoped_kb=%s "
        "use_memory_library_rag=%s",
        counts,
        project_id,
        use_kb_rag,
        use_agent_scoped_kb,
        use_memory_library_rag,
    )
    return RAG_NO_RELEVANT_CONTEXT_MESSAGE
