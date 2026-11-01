"""Роутинг embedding-модели: (provider, model) -> клиент, размерность, таблица.

Зачем этот модуль. У каждой размерности своя таблица векторов, а модель
у каждого пользователя своя. Значит на КАЖДУЮ операцию индексации/поиска надо знать
три вещи:

* каким клиентом считать — нативным svc-rag-models или внешним OpenAI-совместимым;
* какое имя модели передать этому клиенту;
* какая размерность у результата — от неё зависит таблица векторов.

Размерность НЕ берётся из конфига и не хардкодится: её даёт сам вектор. Для новой
пары (provider, model) делается один пробный embed, дальше работает кэш процесса.
Так гард по dim не зависит от того, объявляет ли модель размерность в дескрипторе.

Обратная совместимость: если ни provider, ни model не заданы (кластерный путь),
возвращается ТОТ ЖЕ клиент и ТА ЖЕ таблица
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

NATIVE = "native"

# Кэши процесса. Ключ модели — (provider, model), где provider="" означает
# «кластерный клиент по умолчанию» (тот, что уже лежит в сервисе).
_clients: Dict[Tuple[str, str], Any] = {}
_dims: Dict[Tuple[str, str], int] = {}
_dim_locks: Dict[Tuple[str, str], asyncio.Lock] = {}
_repos: Dict[Tuple[str, int], Any] = {}
_repo_lock = asyncio.Lock()

_PROBE_TEXT = "проверка размерности"

# Автовыбор модели по умолчанию: provider_id -> имя модели.
# Нужен, чтобы у провайдера была рабочая модель до первого выбора в UI,
# и при этом её имя нигде не было записано руками: каталоги шлюзов меняются,
# а неверное имя даёт 500 в момент индексации, а не при старте.
_auto_models: Dict[str, str] = {}
_auto_locks: Dict[str, asyncio.Lock] = {}
# provider_id -> список id моделей из GET /v1/models. Нужен и для автовыбора,
# и для приведения имени модели к каталожному регистру.
_catalogs: Dict[str, List[str]] = {}

# Те же подсказки, что в backend/routes/rag.py::_rag_model_kind — чтобы каталог
# в UI и автовыбор на сервере считали моделью одно и то же.
_RERANK_NAME_HINTS = ("rerank", "cross-encoder", "crossencoder", "marco")
_EMBED_NAME_HINTS = ("embed", "bge", "frida", "e5-", "gte-", "labse", "minilm")

def _looks_like_embedding(model_id: str) -> bool:
    n = (model_id or "").lower()
    if any(h in n for h in _RERANK_NAME_HINTS):
        return False
    return any(h in n for h in _EMBED_NAME_HINTS)

def _autopick_enabled() -> bool:
    import os

    return str(os.getenv("RAG_EMBED_AUTOPICK", "1")).strip().lower() not in {
        "0",
        "false",
        "no",
    }

async def _autopick_embedding_model(entry) -> Optional[str]:
    """Первая модель из каталога провайдера, которая реально считает вектор.

    Порядок — как отдал шлюз. Каталог может объявлять нерабочие алиасы
    (проверено 31.07: PHOENIX объявляет ```Qwen/Qwen3-Embedding-0.6B```, который
    отвечает 500), поэтому мало отфильтровать по имени — нужен пробный вектор.
    """
    from app.clients.openai_models_compat_client import OpenAICompatModelsClient
    from app.core.config import get_settings

    client = OpenAICompatModelsClient(
        provider_id=entry.id,
        base_url=entry.base_url,
        api_key_env=entry.api_key_env or None,
        embed_path=entry.embed_path,
        rerank_path=entry.rerank_path,
        timeout=min(float(entry.timeout or 60.0), 60.0),
        embed_batch_size=get_settings().rag_models_client.embed_batch_size,
        embed_concurrency=get_settings().rag_models_client.embed_concurrency,
        embed_payload_style=getattr(entry, "embed_payload_style", "openai"),
        rerank_payload_style=getattr(entry, "rerank_payload_style", "openai"),
        extra_headers=dict(getattr(entry, "extra_headers", None) or {}),
    )
    catalog = await _provider_catalog(entry)
    candidates = [m for m in catalog if _looks_like_embedding(m)]
    if not candidates:
        logger.warning(
            "[EMBED-ROUTE] %s: в каталоге (%s моделей) нет похожих на embedding — "
            "автовыбор невозможен, выберите модель в UI",
            entry.id,
            len(catalog),
        )
        return None

    rejected: List[str] = []
    for model_id in candidates:
        try:
            vectors = await client.embed([_PROBE_TEXT], model=model_id, kind="query")
        except Exception as e:
            rejected.append(f"{model_id}: {type(e).__name__}")
            continue
        dim = len(vectors[0]) if vectors and vectors[0] else 0
        if dim < 1:
            rejected.append(f"{model_id}: пустой вектор")
            continue
        logger.info(
            "[EMBED-ROUTE] %s: модель по умолчанию выбрана из каталога — %s (dim=%s). "
            "Отклонены: %s",
            entry.id,
            model_id,
            dim,
            rejected or "нет",
        )
        note_dim(entry.id, model_id, dim)
        return model_id

    logger.error(
        "[EMBED-ROUTE] %s: ни одна модель каталога не ответила. Проверено: %s",
        entry.id,
        rejected,
    )
    return None

async def _provider_catalog(entry) -> List[str]:
    """Каталог моделей провайдера. Один запрос на процесс, дальше кэш."""
    cached = _catalogs.get(entry.id)
    if cached is not None:
        return cached

    from app.clients.openai_models_compat_client import OpenAICompatModelsClient
    from app.core.config import get_settings

    client = OpenAICompatModelsClient(
        provider_id=entry.id,
        base_url=entry.base_url,
        api_key_env=entry.api_key_env or None,
        embed_path=entry.embed_path,
        rerank_path=entry.rerank_path,
        timeout=min(float(entry.timeout or 60.0), 60.0),
        embed_batch_size=get_settings().rag_models_client.embed_batch_size,
        embed_concurrency=get_settings().rag_models_client.embed_concurrency,
        embed_payload_style=getattr(entry, "embed_payload_style", "openai"),
        rerank_payload_style=getattr(entry, "rerank_payload_style", "openai"),
        extra_headers=dict(getattr(entry, "extra_headers", None) or {}),
    )
    ids = await client.list_models()
    _catalogs[entry.id] = ids
    return ids

async def _canonical_model(provider_id: str, model: str) -> str:
    """Написание модели ровно как в каталоге провайдера.

    Зачем. Bifrost различает регистр: на ```embed/frida``` он отвечает
    ```no keys found that support model: frida```, хотя в каталоге лежит
    ```embed/FRIDA```. Понижение регистра приходило из backend
    (```rag_client.py```, исправлено), но у пользователей, успевших сохранить
    выбор, строчное имя уже лежит в настройках — правка backend их не чинит.
    Поэтому имя приводится к каталожному здесь, на последнем рубеже.

    Если каталог недоступен — возвращаем как есть: гадать не будем.
    """
    entry = _provider_entry(provider_id)
    if entry is None or not model:
        return model
    try:
        ids = await _provider_catalog(entry)
    except Exception as e:
        logger.warning(
            "[EMBED-ROUTE] %s: каталог недоступен (%s), имя модели %r оставлено как есть",
            provider_id,
            type(e).__name__,
            model,
        )
        return model
    if not ids or model in ids:
        return model
    exact = {m.lower(): m for m in ids}.get(model.lower())
    if exact and exact != model:
        logger.warning(
            "[EMBED-ROUTE] %s: имя модели %r приведено к каталожному %r "
            "(шлюз различает регистр). Проверьте сохранённый выбор пользователя.",
            provider_id,
            model,
            exact,
        )
        return exact
    return model

async def _default_model_for(provider_id: str) -> Optional[str]:
    """Модель провайдера по умолчанию: из конфига, иначе автовыбором.

    Результат автовыбора кэшируется на процесс: повторно шлюз не опрашиваем.
    """
    entry = _provider_entry(provider_id)
    if entry is None:
        return None
    configured = str(getattr(entry, "embedding_model", "") or "").strip()
    if configured:
        return configured
    # chat_only уходит на кластерный эмбеддер — имя модели ему не нужно.
    if bool(getattr(entry, "chat_only", False)) or not _autopick_enabled():
        return None

    cached = _auto_models.get(entry.id)
    if cached:
        return cached

    lock = _auto_locks.get(entry.id)
    if lock is None:
        lock = asyncio.Lock()
        _auto_locks[entry.id] = lock
    async with lock:
        cached = _auto_models.get(entry.id)
        if cached:
            return cached
        try:
            picked = await _autopick_embedding_model(entry)
        except Exception:
            logger.exception("[EMBED-ROUTE] %s: автовыбор модели не удался", entry.id)
            return None
        if picked:
            _auto_models[entry.id] = picked
        return picked

def _norm(provider: Optional[str], model: Optional[str]) -> Tuple[str, Optional[str]]:
    p = (provider or "").strip()
    m = (model or "").strip() or None
    return p, m

def _key(provider: str, model: Optional[str]) -> Tuple[str, str]:
    return (provider or "", model or "")

@dataclass(frozen=True)
class EmbedProfile:
    """Чем и во что считать эмбеддинги для одной операции.

    provider: "" — кластерный клиент сервиса, "native" — svc-rag-models,
              иначе id провайдера из rag_models.providers (PHOENIX и т.п.).
    model:    имя модели для этого клиента (None — модель по умолчанию).
    dim:      размерность векторов; по ней выбирается таблица.
    """

    provider: str
    model: Optional[str]
    dim: int
    client: Any

    @property
    def label(self) -> str:
        return f"{self.provider or 'cluster'}/{self.model or 'default'}(dim={self.dim})"

    async def embed(
        self, texts: List[str], kind: Optional[str] = None
    ) -> List[List[float]]:
        """Посчитать эмбеддинги этим профилем.

        kind="document" для чанков, "query" для запроса — включает префиксы
        асимметричных моделей (FRIDA), см. фазу B1.
        """
        vectors = await self.client.embed(texts, model=self.model, kind=kind)
        if vectors and vectors[0]:
            got = len(vectors[0])
            if self.dim and got != self.dim:
                # Записать их в выбранную таблицу нельзя — размерность колонки другая.
                # Кэш чиним, чтобы повтор ушёл в правильную таблицу, и падаем громко.
                _dims[_key(self.provider, self.model)] = got
                raise RuntimeError(
                    f"[EMBED-ROUTE] {self.label}: модель вернула dim={got}. "
                    "Профиль пересчитан — повторите операцию."
                )
            note_dim(self.provider, self.model, got)
        return vectors

def note_dim(provider: Optional[str], model: Optional[str], dim: int) -> None:
    """Запомнить фактическую размерность пары (provider, model)."""
    p, m = _norm(provider, model)
    d = int(dim or 0)
    if d < 1:
        return
    key = _key(p, m)
    if _dims.get(key) != d:
        _dims[key] = d
        logger.debug("[EMBED-ROUTE] provider=%s model=%s dim=%s", p or "cluster", m, d)

def _provider_entry(provider_id: str):
    from app.core.config import get_settings

    pid = (provider_id or "").strip()
    if not pid or pid.lower() == NATIVE:
        return None
    for entry in getattr(get_settings().rag_models, "providers", None) or []:
        if str(entry.id or "").lower() == pid.lower():
            return entry
    return None

def _canonical_provider(provider: str) -> str:
    """Канонический id провайдера (PHOENIX/CORSUR), без учёта регистра входа."""
    entry = _provider_entry(provider)
    return entry.id if entry is not None else (provider or "").strip()

def _native_client(default_client: Any) -> Any:
    """Кластерный/локальный клиент эмбеддинга (svc-rag-models)."""
    embed_default = getattr(default_client, "embed_client", None)
    if embed_default is not None:
        _clients.setdefault(_key(NATIVE, None), embed_default)
        return embed_default

    from app.clients.rag_models_client import RagModelsClient

    key = _key(NATIVE, None)
    client = _clients.get(key)
    if client is None:
        client = RagModelsClient()
        _clients[key] = client
    return client

def _client_for(provider: str, model: Optional[str], default_client: Any) -> Any:
    """Клиент эмбеддинга для провайдера. Кэшируется — httpx-клиент внутри дешёвый."""
    if not provider or provider.lower() == NATIVE:
        # Кластерный embed (PHOENIX и т.п.) важнее rag_models_client из yml:
        # иначе local/native уходит на llm.corsur, где нет /v1/embeddings.
        return _native_client(default_client)

    key = _key(provider, model)
    client = _clients.get(key)
    if client is not None:
        return client

    entry = _provider_entry(provider)
    if entry is None:
        from app.core.config import get_settings

        known = [e.id
            for e in getattr(get_settings().rag_models, "providers", None) or []
        ]
        raise ValueError(
            f"[EMBED-ROUTE] провайдер '{provider}' не найден в rag_models.providers. "
            f"Доступны: native, {', '.join(known) or '-'}"
        )

    # Шлюз объявлен chat-only: /v1/embeddings там нет, запрос вернул бы 404.
    # Уводим на кластерный эмбеддер вместо того, чтобы ронять поиск и индексацию.
    if bool(getattr(entry, "chat_only", False)):
        logger.warning(
            "[EMBED-ROUTE] провайдер %s помечен chat_only (%s) — эмбеддинги туда "
            "не отправляем, используем кластерный. Уберите его из выбора моделей "
            "эмбеддинга в UI либо снимите chat_only, если шлюз научился /v1/embeddings.",
            entry.id,
            entry.base_url,
        )
        client = _native_client(default_client)
        _clients[key] = client
        return client

    from app.clients.openai_models_compat_client import OpenAICompatModelsClient
    from app.core.config import get_settings

    provider = entry.id
    client = OpenAICompatModelsClient(
        provider_id=entry.id,
        base_url=entry.base_url,
        api_key_env=entry.api_key_env or None,
        embedding_model=model or entry.embedding_model or None,
        reranker_model=entry.reranker_model or None,
        embed_path=entry.embed_path,
        rerank_path=entry.rerank_path,
        timeout=entry.timeout,
        embed_batch_size=get_settings().rag_models_client.embed_batch_size,
        embed_concurrency=get_settings().rag_models_client.embed_concurrency,
        embed_payload_style=getattr(entry, "embed_payload_style", "openai"),
        rerank_payload_style=getattr(entry, "rerank_payload_style", "openai"),
        extra_headers=dict(getattr(entry, "extra_headers", None) or {}),
    )
    _clients[key] = client
    logger.info(
        "[EMBED-ROUTE] клиент создан: provider=%s model=%s base_url=%s style=%s",
        provider,
        model,
        entry.base_url,
        getattr(entry, "embed_payload_style", "openai"),
    )
    return client

async def resolve_dim(client: Any, provider: str, model: Optional[str]) -> int:
    """Размерность пары (provider, model). Первый раз — пробный embed, дальше кэш."""
    key = _key(provider or NATIVE, model)
    dim = _dims.get(key)
    if dim:
        return dim

    lock = _dim_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _dim_locks[key] = lock

    async with lock:
        dim = _dims.get(key)
        if dim:
            return dim
        vectors = await client.embed([_PROBE_TEXT], model=model, kind="query")
        dim = len(vectors[0]) if vectors and vectors[0] else 0
        if dim < 1:
            raise RuntimeError(
                f"[EMBED-ROUTE] provider={provider or NATIVE} model={model}: "
                "пробный embed вернул пустой вектор — размерность неизвестна"
            )
        _dims[key] = dim
        logger.info(
            "[EMBED-ROUTE] проба: provider=%s model=%s dim=%s",
            provider or NATIVE,
            model,
            dim,
        )
        return dim

async def resolve_profile(
    default_client: Any,
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> EmbedProfile:
    """Профиль эмбеддинга для операции.

    Без provider и model — кластерный путь: тот же клиент и та же размерность,
    что были до фазы B3 (никакой пробы, никаких новых таблиц).
    """
    p, m = _norm(provider, model)
    if not p and not m:
        from app.core.config import get_settings

        return EmbedProfile(
            provider="",
            model=None,
            dim=int(get_settings().postgresql.embedding_dim or 0),
            client=default_client,
        )
    if p and p.lower() != NATIVE:
        p = _canonical_provider(p)
        if not m:
            # Пользователь выбрал провайдера, но не модель (или выбор ещё не делали).
            # Раньше это давало «embedding_model не задан» уже на индексации.
            m = await _default_model_for(p)
        else:
            m = await _canonical_model(p, m)
    client = _client_for(p, m, default_client)
    dim = await resolve_dim(client, p, m)
    return EmbedProfile(provider=p or NATIVE, model=m, dim=dim, client=client)

async def vector_repo_for(root_repo: Any, dim: int) -> Any:
    """Репозиторий, адресующий таблицу этой размерности (фаза B2b).

    Экземпляры кэшируются по (класс репозитория, dim). При первом обращении
    create_tables добавляет таблицу нужной размерности — операция добавляющая,
    существующие данные не трогает.
    """
    d = int(dim or 0)
    root_dim = int(getattr(root_repo, "embedding_dim", 0) or 0)
    if d < 1 or d == root_dim:
        return root_repo

    key = (type(root_repo).__name__, d)
    repo = _repos.get(key)
    if repo is not None:
        return repo

    async with _repo_lock:
        repo = _repos.get(key)
        if repo is not None:
            return repo
        repo = type(root_repo)(root_repo.db, embedding_dim=d)
        await repo.create_tables()
        _repos[key] = repo
        logger.info(
            "[EMBED-ROUTE] репозиторий %s для dim=%s готов", type(root_repo).__name__, d
        )
        return repo

def _store_label(repo: Any) -> str:
    """project / kb / memory - по классу репозитория, без правки вызывающих"""
    name = type(repo).__name__.lower()
    for key in ("project", "kb", "memory"):
        if key in name:
            return key
    return name or "store"

async def resolve_for(
    default_client: Any,
    root_repo: Any,
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> Tuple[EmbedProfile, Any]:
    """Одной строкой: профиль эмбеддинга + репозиторий нужной таблицы."""
    profile = await resolve_profile(default_client, provider, model)
    repo = await vector_repo_for(root_repo, profile.dim)
    logger.debug(
        "[EMBED-ROUTE] %s: запрошено provider=%s model=%s -> выбрано provider=%s model=%s dim=%s таблица=%s",
        _store_label(root_repo),
        provider or "-",
        model or "-",
        profile.provider or "cluster",
        profile.model or "default",
        profile.dim,
        type(repo).__name__,
    )
    return profile, repo


async def profile_for_dim(
    default_client: Any,
    dim: int,
    *,
    prefer_provider: Optional[str] = None,
) -> Optional[EmbedProfile]:
    """Профиль эмбеддинга под известную dim.

    Сначала смотрим кэш проб. Если пусто (после рестарта ещё не зондировали
    модель, которой индексировали) — пробуем провайдеров из rag_models.providers
    и их embedding_model: одна удачная проба с нужной dim и достаточно.
    """
    want = int(dim or 0)
    if want < 1:
        return None
    prefer = (prefer_provider or "").strip().lower()
    matches: List[Tuple[str, str]] = [
        (p, m) for (p, m), d in _dims.items() if int(d) == want
    ]
    if prefer:
        preferred = [(p, m) for p, m in matches if (p or "").lower() == prefer]
        if preferred:
            matches = preferred + [x for x in matches if x not in preferred]

    candidates: List[Tuple[Optional[str], Optional[str]]] = [
        (p or None, m or None) for p, m in matches
    ]
    if not candidates:
        try:
            from app.core.config import get_settings

            section = getattr(get_settings(), "rag_models", None)
            providers = list(getattr(section, "providers", None) or [])
            if prefer:
                providers = sorted(
                    providers,
                    key=lambda e: 0 if (getattr(e, "id", "") or "").lower() == prefer else 1,
                )
            for entry in providers:
                pid = (getattr(entry, "id", None) or "").strip()
                mid = (getattr(entry, "embedding_model", None) or "").strip() or None
                if pid:
                    candidates.append((pid, mid))
        except Exception:
            logger.debug(
                "[EMBED-ROUTE] profile_for_dim(%s): каталог провайдеров недоступен",
                want,
                exc_info=True,
            )

    seen: set = set()
    for p, m in candidates:
        key = ((p or "").lower(), (m or "").lower())
        if key in seen:
            continue
        seen.add(key)
        try:
            prof = await resolve_profile(default_client, p, m)
        except Exception:
            logger.debug(
                "[EMBED-ROUTE] profile_for_dim(%s): skip %s/%s",
                want,
                p,
                m,
                exc_info=True,
            )
            continue
        if int(getattr(prof, "dim", 0) or 0) == want:
            return prof
    return None

# Клиенты реранка. Отдельный кэш от эмбеддинга: у провайдера это разные модели,
# и пользователь волен взять эмбеддер у одного, а реранкер у другого.
_rerank_clients: Dict[Tuple[str, str], Any] = {}

def rerank_client_for(
    default_client: Any,
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> Any:
    """Клиент реранка для пары (provider, model).

    Хранилища у реранка нет, поэтому здесь только выбор клиента — ни размерности,
    ни таблицы, ни пробного вызова. Имя модели передаётся в сам вызов rerank(),
    так что одного клиента на провайдера достаточно.

    Без provider — кластерный клиент, как было до фазы E.
    """
    p, m = _norm(provider, model)
    logger.debug(
        "[RERANK-ROUTE] запрошено provider=%s model=%s -> %s",
        provider or "-",
        model or "-",
        "кластерный клиент" if not p else f"{p}/{m or 'default'}",
    )
    if not p:
        return default_client
    if p.lower() == NATIVE:
        from app.clients.rag_models_client import RagModelsClient

        key = _key(NATIVE, None)
        client = _clients.get(key)
        if client is None:
            client = RagModelsClient()
            _clients[key] = client
        return client

    p = _canonical_provider(p)
    key = _key(p, m)
    client = _rerank_clients.get(key)
    if client is not None:
        return client
    entry = _provider_entry(p)
    if entry is None:
        from app.core.config import get_settings

        known = [
            e.id
            for e in getattr(get_settings().rag_models, "providers", None) or []
        ]
        raise ValueError(
            f"[RERANK-ROUTE] провайдер '{p}' не найден в rag_models.providers. "
            f"Доступны: native, {', '.join(known) or '-'}"
        )

    from app.clients.openai_models_compat_client import OpenAICompatModelsClient
    from app.core.config import get_settings

    p = entry.id
    client = OpenAICompatModelsClient(
        provider_id=entry.id,
        base_url=entry.base_url,
        api_key_env=entry.api_key_env or None,
        reranker_model=m or entry.reranker_model or None,
        embed_path=entry.embed_path,
        rerank_path=entry.rerank_path,
        timeout=entry.timeout,
        embed_batch_size=get_settings().rag_models_client.embed_batch_size,
        embed_concurrency=get_settings().rag_models_client.embed_concurrency,
        rerank_payload_style=getattr(entry, "rerank_payload_style", "openai"),
        # Без extra_headers тоннель не работает: шлюзу нужен
        # x-bf-passthrough-extra-params, иначе 422/404.
        extra_headers=dict(getattr(entry, "extra_headers", None) or {}),
    )
    _rerank_clients[key] = client
    logger.info(
        "[RERANK-ROUTE] клиент создан: provider=%s model=%s path=%s стиль=%s", 
        p, 
        m, 
        entry.rerank_path, 
        getattr(entry, "rerank_payload_style", "openai"),
    )
    return client

def routing_overview() -> Dict[str, Any]:
    """Диагностика для /v1/health: что процесс уже знает о моделях."""
    return {
        "models": [
            {"provider": p or "cluster", "model": m or None, "dim": d}
            for (p, m), d in sorted(_dims.items())
        ],
        "clients": [f"{p or 'cluster'}/{m or 'default'}" for (p, m) in _clients],
        "rerank_clients": [
            f"{p or 'cluster'}/{m or 'default'}" for (p, m) in _rerank_clients
        ],
        "repos": [{"repo": name, "dim": d} for (name, d) in sorted(_repos)],
        # Что автовыбор нашёл в каталогах — видно в /v1/health, без чтения логов.
        "auto_models": dict(sorted(_auto_models.items())),
    }