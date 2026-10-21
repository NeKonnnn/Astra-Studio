"""
RAG-настройки агентов и проектов.

Настройки принадлежат СУЩНОСТИ, а не пользователю: у каждого агента и каждого
проекта свой набор, он один на всех, кому агента расшарили, и переживает смену
владельца. Хранилище — таблица ``entity_rag_settings`` (см.
``database/postgresql/entity_settings_repository.py``). Глобальный settings.json
/ app_state остаются seed-дефолтами для сущностей, которых не настраивали.

Библиотека (memory) сюда не входит: её модель, чанкинг, пороги и препроцесс —
только из env, см. ``memory_rag_env``. Дефолты project/agent — ``RAG_ENTITY_*``
в env, см. ``entity_rag_env`` (модели, стратегия, чанкинг, K, порог и т.д.).

На время обработки чата снимки активных сущностей прокидываются через ContextVar
(см. ``bind_user_rag_runtime``).
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from copy import deepcopy
from typing import Any, Dict, Optional, Tuple

from backend.settings.logging import get_logger

logger = get_logger(__name__)

# Все настройки — пер-сущностные. Исключение одно: rag_memory_strategy, это
# стратегия поиска по Библиотеке, она общая и живёт в user_llm_settings.
RAG_SETTING_KEYS: Tuple[str, ...] = (
    "rag_strategy",
    "agentic_rag_enabled",
    "agentic_max_iterations",
    "rag_query_fix_typos",
    "rag_multi_query_enabled",
    "rag_hyde_enabled",
    "rag_chat_top_k",
    "rag_chunking_strategy",
    "rag_chunk_size",
    "rag_chunk_overlap",
    "rag_similarity_threshold",
    "rag_reranking_enabled",
    "rag_rerank_top_n",
    "rag_system_prompt",
    "rag_embedding_model_path",
    "rag_reranker_model_path",
)

# Совместимость с прежним именем: на него ссылался внешний код.
_RAG_SETTING_KEYS = RAG_SETTING_KEYS

# Поля, изменение которых требует пересборки индекса. Отдельный список, потому
# что top_k или порог similarity менять можно сколько угодно — они на нарезку и
# вектора не влияют.
INDEX_AFFECTING_KEYS: Tuple[str, ...] = (
    "rag_chunking_strategy",
    "rag_chunk_size",
    "rag_chunk_overlap",
    "rag_embedding_model_path",
)

SCOPES: Tuple[str, ...] = ("project", "agent", "kb")
DEFAULT_SCOPE = "project"

# Ключ настроек Библиотеки в строке пользователя.
MEMORY_STRATEGY_KEY = "rag_memory_strategy"

# Снимки настроек сущностей текущего запроса: {"agent": {...}, "project": {...}}.
_runtime_scopes: ContextVar[Optional[Dict[str, Dict[str, Any]]]] = ContextVar(
    "rag_runtime_scopes", default=None
)
# Стратегия поиска по Библиотеке текущего пользователя.
_runtime_memory_strategy: ContextVar[Optional[str]] = ContextVar(
    "rag_runtime_memory_strategy", default=None
)


def normalize_scope(raw: Optional[str]) -> str:
    s = (raw or "").strip().lower()
    return s if s in SCOPES else DEFAULT_SCOPE


def normalize_entity_id(raw: Any) -> Optional[str]:
    s = str(raw if raw is not None else "").strip()
    return s or None


def _defaults_from_app_state() -> Dict[str, Any]:
    """Дефолты кластера: settings.json / ConfigMap через app_state."""
    from backend.services.entity_rag_env import (
        get_entity_agentic_max_iterations,
        get_entity_agentic_rag_enabled,
        get_entity_chat_top_k,
        get_entity_chunk_overlap,
        get_entity_chunk_size,
        get_entity_chunking_strategy,
        get_entity_embedding_model_path,
        get_entity_hyde_enabled,
        get_entity_multi_query_enabled,
        get_entity_query_fix_typos,
        get_entity_rag_strategy,
        get_entity_rerank_top_n,
        get_entity_reranker_model_path,
        get_entity_reranking_enabled,
        get_entity_similarity_threshold,
        get_entity_system_prompt,
    )

    try:
        from backend import app_state as state

        emb_state = str(getattr(state, "rag_embedding_model_path", "") or "")
        rer_state = str(getattr(state, "rag_reranker_model_path", "") or "")
        return {
            "rag_strategy": get_entity_rag_strategy(),
            "agentic_rag_enabled": get_entity_agentic_rag_enabled(),
            "agentic_max_iterations": get_entity_agentic_max_iterations(),
            "rag_query_fix_typos": get_entity_query_fix_typos(),
            "rag_multi_query_enabled": get_entity_multi_query_enabled(),
            "rag_hyde_enabled": get_entity_hyde_enabled(),
            "rag_chat_top_k": get_entity_chat_top_k(),
            "rag_chunking_strategy": get_entity_chunking_strategy(),
            "rag_chunk_size": get_entity_chunk_size(),
            "rag_chunk_overlap": get_entity_chunk_overlap(),
            "rag_similarity_threshold": get_entity_similarity_threshold(),
            "rag_reranking_enabled": get_entity_reranking_enabled(),
            "rag_rerank_top_n": get_entity_rerank_top_n(),
            "rag_system_prompt": get_entity_system_prompt(),
            "rag_embedding_model_path": emb_state or get_entity_embedding_model_path(),
            "rag_reranker_model_path": rer_state or get_entity_reranker_model_path(),
        }
    except Exception:
        logger.exception("user_rag_settings: defaults from app_state failed")
        return {
            "rag_strategy": get_entity_rag_strategy(),
            "agentic_rag_enabled": get_entity_agentic_rag_enabled(),
            "agentic_max_iterations": get_entity_agentic_max_iterations(),
            "rag_query_fix_typos": get_entity_query_fix_typos(),
            "rag_multi_query_enabled": get_entity_multi_query_enabled(),
            "rag_hyde_enabled": get_entity_hyde_enabled(),
            "rag_chat_top_k": get_entity_chat_top_k(),
            "rag_chunking_strategy": get_entity_chunking_strategy(),
            "rag_chunk_size": get_entity_chunk_size(),
            "rag_chunk_overlap": get_entity_chunk_overlap(),
            "rag_similarity_threshold": get_entity_similarity_threshold(),
            "rag_reranking_enabled": get_entity_reranking_enabled(),
            "rag_rerank_top_n": get_entity_rerank_top_n(),
            "rag_system_prompt": get_entity_system_prompt(),
            "rag_embedding_model_path": get_entity_embedding_model_path(),
            "rag_reranker_model_path": get_entity_reranker_model_path(),
        }


def default_rag_settings_snapshot() -> Dict[str, Any]:
    return deepcopy(_defaults_from_app_state())


# Старые дефолты кластера (до RAG_ENTITY_*). Если они «заморожены» в Postgres
# у сущности, не считаем это осознанным выбором — снова следуем за ConfigMap.
_LEGACY_CLUSTER_ENTITY_DEFAULTS: Dict[str, Any] = {
    "rag_strategy": "auto",
    "rag_chunking_strategy": "hierarchical",
    "rag_chunk_size": 1000,
    "rag_chunk_overlap": 200,
    "rag_chat_top_k": 12,
    "rag_similarity_threshold": 0.0,
    "rag_reranking_enabled": True,
    "rag_rerank_top_n": 12,
    "agentic_rag_enabled": True,
    "agentic_max_iterations": 2,
    "rag_query_fix_typos": False,
    "rag_multi_query_enabled": False,
    "rag_hyde_enabled": False,
    "rag_system_prompt": "",
    "rag_embedding_model_path": "",
    "rag_reranker_model_path": "",
}


def _values_equal(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is b
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) == bool(b)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return float(a) == float(b)
    return str(a).strip() == str(b).strip()


def _strip_legacy_default_overrides(stored: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Убрать «замороженные» старые дефолты кластера из строки сущности.

    INDEX_AFFECTING_KEYS не трогаем: save() пишет их всегда (даже если значение
    совпало с дефолтом), и осознанный выбор вроде hierarchical при ConfigMap=fixed
    иначе снова схлопывался бы в fixed — UI откатывался, перечанковка не стартовала.
    """
    if not isinstance(stored, dict):
        return {}
    out = dict(stored)
    for key, legacy_val in _LEGACY_CLUSTER_ENTITY_DEFAULTS.items():
        if key in INDEX_AFFECTING_KEYS:
            continue
        if key in out and _values_equal(out[key], legacy_val):
            del out[key]
    return out


def _merge_with_defaults(stored: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    merged = _defaults_from_app_state()
    cleaned = _strip_legacy_default_overrides(stored)
    if cleaned:
        for key in RAG_SETTING_KEYS:
            if key in cleaned and cleaned[key] is not None:
                merged[key] = cleaned[key]
    return merged


def _get_entity_repo():
    try:
        from backend.database.init_db import get_entity_settings_repository

        return get_entity_settings_repository()
    except Exception:
        logger.debug("entity settings repository недоступен", exc_info=True)
        return None


def _get_user_repo():
    try:
        from backend.database.init_db import get_user_settings_repository

        return get_user_settings_repository()
    except Exception:
        logger.debug("user settings repository недоступен", exc_info=True)
        return None


# --- настройки сущности -----------------------------------------------------


async def get_entity_rag_settings(
    scope: Optional[str], entity_id: Optional[Any]
) -> Dict[str, Any]:
    """Полный снимок настроек агента или проекта.

    Записи нет — чистые дефолты кластера. Не настройки того, кто спрашивает:
    иначе у читателя расшаренного агента поиск шёл бы по его собственной модели,
    мимо корпуса агента.
    """
    ek = normalize_entity_id(entity_id)
    if not ek:
        return _defaults_from_app_state()
    repo = _get_entity_repo()
    if repo is None:
        return _defaults_from_app_state()
    sc_norm = normalize_scope(scope)
    stored_raw = await repo.get(sc_norm, ek)
    if isinstance(stored_raw, dict) and stored_raw:
        cleaned = _strip_legacy_default_overrides(stored_raw)
        if not cleaned:
            try:
                await repo.delete(sc_norm, ek)
            except Exception:
                logger.debug(
                    "entity_rag_settings: не удалось удалить legacy-строку %s/%s",
                    sc_norm,
                    ek,
                    exc_info=True,
                )
            stored_raw = None
        else:
            stored_raw = cleaned
    return _merge_with_defaults(stored_raw)


async def save_entity_rag_settings(
    scope: Optional[str],
    entity_id: Any,
    updates: Dict[str, Any],
    updated_by: Optional[str] = None,
) -> Dict[str, Any]:
    """Частичное обновление настроек сущности. Возвращает полный снимок.

    Пишем только то, что реально отличается от дефолтов кластера: сущность без
    расхождений строки не заводит и продолжает следовать за ConfigMap.
    """
    ek = normalize_entity_id(entity_id)
    if not ek:
        raise ValueError("entity_id обязателен")
    sc = normalize_scope(scope)
    repo = _get_entity_repo()
    if repo is None:
        logger.warning(
            "entity_rag_settings: репозиторий недоступен — сохранение пропущено"
        )
        return _merge_with_defaults(updates)

    stored = _strip_legacy_default_overrides(dict(await repo.get(sc, ek) or {}))
    for key in RAG_SETTING_KEYS:
        if key in updates and updates[key] is not None:
            stored[key] = updates[key]

    defaults = _defaults_from_app_state()
    # Индексные ключи храним ВСЕГДА, даже когда значение совпало с дефолтным
    # кластера. Иначе выбор такого значения неотличим от "нетрогал"
    payload = {
        k: v
        for k, v in stored.items()
        if k in RAG_SETTING_KEYS and (k in INDEX_AFFECTING_KEYS or v != defaults.get(k))
    }
    if payload:
        await repo.upsert(sc, ek, payload, updated_by)
    else:
        # Всё вернулось к дефолтам — строка не нужна, пусть сущность снова
        # следует за ConfigMap.
        await repo.delete(sc, ek)
    return _merge_with_defaults(payload)


async def reset_entity_rag_settings(
    scope: Optional[str], entity_id: Any
) -> Dict[str, Any]:
    """Сброс настроек сущности к дефолтам кластера."""
    ek = normalize_entity_id(entity_id)
    if not ek:
        raise ValueError("entity_id обязателен")
    repo = _get_entity_repo()
    if repo is not None:
        await repo.delete(normalize_scope(scope), ek)
    return _defaults_from_app_state()


# --- стратегия Библиотеки (единственная настройка на пользователе) ----------


async def get_user_memory_strategy(user_id: Optional[str]) -> str:
    uid = (user_id or "").strip()
    if not uid:
        return "auto"
    repo = _get_user_repo()
    if repo is None:
        return "auto"
    row = await repo.get(uid)
    stored = (row or {}).get("rag_settings")
    if isinstance(stored, dict):
        value = str(stored.get(MEMORY_STRATEGY_KEY) or "").strip().lower()
        if value:
            return value
    return str(_defaults_from_app_state().get("rag_strategy") or "auto")


async def save_user_memory_strategy(user_id: str, strategy: str) -> str:
    uid = (user_id or "").strip()
    if not uid:
        raise ValueError("user_id обязателен")
    repo = _get_user_repo()
    if repo is None:
        raise RuntimeError("Хранилище пользовательских настроек недоступно")
    row = await repo.get(uid)
    stored = dict((row or {}).get("rag_settings") or {})
    stored[MEMORY_STRATEGY_KEY] = str(strategy or "auto").strip().lower() or "auto"
    await repo.upsert(uid, rag_settings=stored)
    return stored[MEMORY_STRATEGY_KEY]


# --- привязка к запросу чата ------------------------------------------------


def bind_user_rag_runtime(
    scopes: Optional[Dict[str, Dict[str, Any]]] = None,
    memory_strategy: Optional[str] = None,
) -> Token:
    """Привязать снимки настроек активных сущностей к текущему async-контексту.

    ``scopes`` — {"agent": {...}, "project": {...}}; сущность не выбрана — ключа
    нет, и её скоуп читается как дефолты кластера.
    """
    payload: Dict[str, Dict[str, Any]] = {}
    if isinstance(scopes, dict):
        for name, snapshot in scopes.items():
            key = normalize_scope(name)
            if isinstance(snapshot, dict) and snapshot:
                payload[key] = dict(snapshot)
    _runtime_memory_strategy.set(
        str(memory_strategy).strip().lower() if memory_strategy else None
    )
    return _runtime_scopes.set(payload or None)


def reset_user_rag_runtime(token: Token) -> None:
    _runtime_scopes.reset(token)
    _runtime_memory_strategy.set(None)


def runtime_has_scope(scope: Optional[str] = None) -> bool:
    """Есть ли в текущем запросе снимок настроек для скоупа (не пустой словарь)."""
    current = _runtime_scopes.get()
    if not isinstance(current, dict) or not current:
        return False
    snapshot = current.get(normalize_scope(scope))
    return isinstance(snapshot, dict) and bool(snapshot)


def runtime_kb_search_scope() -> str:
    """Скоуп настроек для поиска по документам БЗ / KB чата.

    Настройки entity-БЗ хранятся в embedding-пространстве ``kb``. Если в
    runtime активен скоуп ``kb`` — ищем по ним; иначе legacy-поведение агента
    (settings_source=agent).
    """
    return "kb" if runtime_has_scope("kb") else "agent"


def get_runtime_rag_settings(scope: Optional[str] = None) -> Dict[str, Any]:
    """Настройки стора для текущего запроса. Нет привязки — дефолты кластера."""
    current = _runtime_scopes.get()
    if isinstance(current, dict) and current:
        snapshot = current.get(normalize_scope(scope))
        if isinstance(snapshot, dict) and snapshot:
            return snapshot
    return _defaults_from_app_state()


# --- производные значения для пайплайна поиска ------------------------------


def runtime_rag_top_k(scope: Optional[str] = None) -> int:
    try:
        v = int(get_runtime_rag_settings(scope).get("rag_chat_top_k") or 12)
    except (TypeError, ValueError):
        v = 12
    return max(1, min(v, 64))


def runtime_rag_strategy(scope: Optional[str] = None) -> str:
    return str(get_runtime_rag_settings(scope).get("rag_strategy") or "auto")


def runtime_memory_strategy() -> str:
    """Стратегия поиска по Библиотеке: настройка пользователя, не сущности."""
    value = _runtime_memory_strategy.get()
    if value:
        return value
    return str(_defaults_from_app_state().get("rag_strategy") or "auto")


def runtime_primary_scope() -> str:
    """Чья настройка управляет поведением, общим для всего запроса.

    Agentic-цикл и число его итераций относятся не к отдельному стору, а к
    обработке вопроса целиком, — а настройки теперь у каждой сущности свои.
    Правило: командует та, с которой человек разговаривает: агент, если он
    выбран, иначе проект. Ни одной не привязано — дефолты кластера.
    """
    current = _runtime_scopes.get()
    if isinstance(current, dict) and current.get("agent"):
        return "agent"
    return DEFAULT_SCOPE


def runtime_agentic_rag_enabled(scope: Optional[str] = None) -> bool:
    sc = scope or runtime_primary_scope()
    return bool(get_runtime_rag_settings(sc).get("agentic_rag_enabled", True))


def runtime_agentic_max_iterations(scope: Optional[str] = None) -> int:
    sc = scope or runtime_primary_scope()
    try:
        v = int(get_runtime_rag_settings(sc).get("agentic_max_iterations") or 2)
    except (TypeError, ValueError):
        v = 2
    return max(1, min(v, 5))


def runtime_rag_similarity_threshold(scope: Optional[str] = None) -> float:
    """Порог similarity: agent/project — настройки сущности, memory — env."""
    if scope and str(scope).strip().lower() == "memory":
        from backend.services.memory_rag_env import get_memory_similarity_threshold

        return get_memory_similarity_threshold()
    try:
        v = float(
            get_runtime_rag_settings(scope).get("rag_similarity_threshold") or 0.0
        )
    except (TypeError, ValueError):
        v = 0.0
    return max(0.0, min(v, 1.0))


def merged_rag_system_prompt(scopes) -> str:
    """Системный промпт для набора сторов, давших чанки.

    project/agent — промпт сущности; memory — ``RAG_MEMORY_SYSTEM_PROMPT`` из
    env. Несколько разных промптов — склейка без дублей.
    """
    names = [str(s or "").strip().lower() for s in (scopes or []) if s]
    if not names:
        return ""
    seen: list = []
    for name in names:
        if name == "memory":
            from backend.services.memory_rag_env import get_memory_system_prompt

            p = get_memory_system_prompt().strip()
        else:
            p = str(
                get_runtime_rag_settings(normalize_scope(name)).get("rag_system_prompt")
                or ""
            ).strip()
        if p and p not in seen:
            seen.append(p)
    return "\n\n".join(seen)


def runtime_rag_system_prompt(scopes=None) -> str:
    """Промпт текущего запроса. ``scopes`` — сторы, реально давшие чанки."""
    if scopes:
        return merged_rag_system_prompt(scopes)
    return ""


# --- параметры нарезки и моделей --------------------------------------------


def chunk_params_from_rag_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    try:
        size = int(settings.get("rag_chunk_size") or 1000)
    except (TypeError, ValueError):
        size = 1000
    try:
        overlap = int(settings.get("rag_chunk_overlap") or 200)
    except (TypeError, ValueError):
        overlap = 200
    strategy = (
        str(settings.get("rag_chunking_strategy") or "hierarchical").strip().lower()
    )
    if strategy not in {
        "hierarchical",
        "fixed",
        "markdown",
        "separators",
        "semantic",
        "universal",
    }:
        strategy = "hierarchical"
    return {
        "chunk_size": max(200, min(size, 8000)),
        "chunk_overlap": max(0, min(overlap, 2000)),
        "chunking_strategy": strategy,
    }


def settings_response_dict(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Формат ответа /api/rag/settings."""
    from backend.services.entity_rag_env import (
        get_entity_agentic_max_iterations,
        get_entity_agentic_rag_enabled,
        get_entity_chat_top_k,
        get_entity_chunk_overlap,
        get_entity_chunk_size,
        get_entity_chunking_strategy,
        get_entity_hyde_enabled,
        get_entity_multi_query_enabled,
        get_entity_query_fix_typos,
        get_entity_rag_strategy,
        get_entity_rerank_top_n,
        get_entity_reranking_enabled,
        get_entity_similarity_threshold,
        get_entity_system_prompt,
    )

    strategy = str(settings.get("rag_strategy") or get_entity_rag_strategy())
    chunking = str(
        settings.get("rag_chunking_strategy") or get_entity_chunking_strategy()
    )
    chunk_size = int(settings.get("rag_chunk_size") or get_entity_chunk_size())
    chunk_overlap = int(settings.get("rag_chunk_overlap") or get_entity_chunk_overlap())
    return {
        "strategy": strategy,
        "applied_method": strategy,
        "method_description": {
            "auto": "Автоматический выбор стратегии.",
            "hierarchical": "Иерархический поиск по суммаризациям.",
            "hybrid": "Гибридный поиск: вектор + BM25 (weighted RRF по рангам).",
            "vector": "Чистый поиск по cosine distance без изменения порядка результатов.",
            "lexical": "Лексический поиск BM25 (без семантического расширения).",
            "raw_cosine": "Сырой cosine-поиск (без постобработки).",
            "graph": "Графовый RAG: расширение по связям между чанками.",
        }.get(strategy, ""),
        "agentic_rag_enabled": bool(
            settings.get("agentic_rag_enabled", get_entity_agentic_rag_enabled())
        ),
        "agentic_max_iterations": int(
            settings.get("agentic_max_iterations")
            or get_entity_agentic_max_iterations()
        ),
        "rag_query_fix_typos": bool(
            settings.get("rag_query_fix_typos", get_entity_query_fix_typos())
        ),
        "rag_multi_query_enabled": bool(
            settings.get("rag_multi_query_enabled", get_entity_multi_query_enabled())
        ),
        "rag_hyde_enabled": bool(
            settings.get("rag_hyde_enabled", get_entity_hyde_enabled())
        ),
        "rag_chat_top_k": int(
            settings.get("rag_chat_top_k") or get_entity_chat_top_k()
        ),
        "rag_chunking_strategy": chunking,
        "rag_chunk_size": chunk_size,
        "rag_chunk_overlap": chunk_overlap,
        "rag_similarity_threshold": float(
            settings.get("rag_similarity_threshold")
            if settings.get("rag_similarity_threshold") is not None
            else get_entity_similarity_threshold()
        ),
        "rag_reranking_enabled": bool(
            settings.get("rag_reranking_enabled", get_entity_reranking_enabled())
        ),
        "rag_rerank_top_n": int(
            settings.get("rag_rerank_top_n") or get_entity_rerank_top_n()
        ),
        "rag_system_prompt": str(
            settings.get("rag_system_prompt") or get_entity_system_prompt()
        ),
        "rag_embedding_model_path": str(settings.get("rag_embedding_model_path") or ""),
        "rag_reranker_model_path": str(settings.get("rag_reranker_model_path") or ""),
    }


def index_params_changed(before: Dict[str, Any], after: Dict[str, Any]) -> bool:
    """Нужна ли пересборка индекса после правки настроек."""
    return any(before.get(k) != after.get(k) for k in INDEX_AFFECTING_KEYS)


def embedding_fields_from_path(model_path: Optional[str]) -> Dict[str, Any]:
    """Путь модели из настроек → поля запроса к svc-rag.

    Префикс уходит провайдером как есть — это id из эrag_models.providers
    у SVC-RAG, там сравнение регистронезависимое:

    'corsur_rag/embed/FRIDA'      → CORSUR_RAG, модель 'embed/FRIDA'
    'phoenix_embeddings/<id>'     → phoenix_embeddings
    'corsur/<id>'                 → corsur (старый путь; запись CORSUR
                                    должна остаться в конфиге SVC-RAG)
    'local/FRIDA' | 'native/…'    → native
    ''                            → {} — кластерная модель svc-rag
    """
    p = (model_path or "").strip()
    if not p:
        return {}
    if "/" not in p:
        return {"embedding_model": p, "embedding_provider": "native"}
    prefix, model = (part.strip() for part in p.split("/", 1))
    if not model:
        return {}
    # Префикс — это id провайдера в конфиге SVC-RAG (rag_models.providers), там
    # сравнение регистронезависимое. Раньше здесь стоял разбор трёх известных
    # префиксов с переменными окружения на каждый; новый шлюз требовал правки
    # кода. Теперь имя провайдера одно и то же по обе стороны и берётся как есть.
    provider = "native" if prefix.lower() in ("local", "native") else prefix
    return {"embedding_model": model, "embedding_provider": provider}


def embedding_fields_from_rag_settings(
    settings: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    return embedding_fields_from_path((settings or {}).get("rag_embedding_model_path"))


def runtime_embedding_fields(scope: Optional[str] = None) -> Dict[str, Any]:
    """Модель стора для ТЕКУЩЕГО запроса чата/поиска."""
    return embedding_fields_from_rag_settings(get_runtime_rag_settings(scope))


def reranker_fields_from_path(model_path: Optional[str]) -> Dict[str, Any]:
    """Путь реранкера → поля запроса к svc-rag.

    Разбор пути тот же, что у эмбеддера, но ключи другие: реранкер выбирается
    независимо (можно эмбеддить нативно, а реранкать в Phoenix).
    """
    fields = embedding_fields_from_path(model_path)
    if not fields:
        return {}
    return {
        "reranker_model": fields["embedding_model"],
        "reranker_provider": fields["embedding_provider"],
    }


def reranker_fields_from_rag_settings(
    settings: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    return reranker_fields_from_path((settings or {}).get("rag_reranker_model_path"))


def runtime_reranker_fields(scope: Optional[str] = None) -> Dict[str, Any]:
    """Реранкер стора для текущего запроса."""
    return reranker_fields_from_rag_settings(get_runtime_rag_settings(scope))


async def embedding_fields_for_entity(
    scope: Optional[str], entity_id: Optional[Any]
) -> Dict[str, Any]:
    """Модель сущности — для фона и загрузок, где ContextVar пуст или чужой."""
    if not normalize_entity_id(entity_id):
        return {}
    return embedding_fields_from_rag_settings(
        await get_entity_rag_settings(scope, entity_id)
    )
