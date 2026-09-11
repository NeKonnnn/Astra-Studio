# Настройки сервиса: URL RAG-MODELS, PostgreSQL, размерность эмбеддингов
import os
import yaml
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

_settings = None


def _env_str(name: str) -> Optional[str]:
    value = os.environ.get(name)
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _env_int(name: str) -> Optional[int]:
    value = _env_str(name)
    if value is None:
        return None
    return int(value)


def _docker_runtime() -> bool:
    de = os.environ.get("DOCKER_ENV")
    if de is not None:
        return str(de).lower() == "true"
    return os.path.exists("/.dockerenv")


def _pick_service_url(urls: dict, docker_key: str, port_key: str) -> str:
    if not isinstance(urls, dict):
        return ""
    if _docker_runtime():
        return (urls.get(docker_key) or "").strip().rstrip("/")
    return ((urls.get(port_key) or urls.get(docker_key)) or "").strip().rstrip("/")


_URLS_CORS_KEYS: Tuple[str, ...] = (
    "frontend_port",
    "frontend_port_ipv4",
    "frontend_port_2",
    "frontend_port_2_ipv4",
    "frontend_port_3",
    "frontend_port_3_ipv4",
    "backend_port",
    "backend_port_ipv4",
    "backend_port_2",
    "backend_port_2_ipv4",
    # memo_new_api naming
    "frontend_port_1",
    "frontend_port_1_ipv4",
    "backend_port_1",
    "backend_port_1_ipv4",
)


def _apply_urls_section(data: dict) -> dict:
    urls = data.get("urls")
    if not isinstance(urls, dict):
        return data
    out = dict(data)
    rmc = dict(out.get("rag_models_client") or {})
    if not str(rmc.get("base_url") or "").strip():
        bu = _pick_service_url(urls, "rag_models_service_docker", "rag_models_service_port")
        if bu:
            rmc["base_url"] = bu
    out["rag_models_client"] = rmc

    oc = dict(out.get("ocr") or {})
    if not str(oc.get("url") or "").strip():
        u = _pick_service_url(urls, "ocr_service_docker", "ocr_service_port")
        if u:
            oc["url"] = u
    out["ocr"] = oc

    llm = dict(out.get("llm_service") or {})
    if not str(llm.get("base_url") or "").strip():
        bu = _pick_service_url(urls, "llm_service_docker", "llm_service_port")
        if bu:
            llm["base_url"] = bu
    out["llm_service"] = llm

    be = dict(out.get("backend") or {})
    if not str(be.get("base_url") or "").strip():
        bu = _pick_service_url(urls, "backend_service_docker", "backend_service_port")
        if bu:
            be["base_url"] = bu
    out["backend"] = be

    cors = dict(out.get("cors") or {})
    ao = cors.get("allowed_origins")
    if not ao or ao == ["*"]:
        merged = [str(urls[k]).strip() for k in _URLS_CORS_KEYS if urls.get(k) and str(urls[k]).strip()]
        if merged:
            cors["allowed_origins"] = merged
    out["cors"] = cors
    out.pop("urls", None)
    return out


def _embedding_gateway_from_providers(data: dict) -> Tuple[str, str]:
    """base_url и api_key_env для эмбеддингов из rag_models.providers."""
    section = data.get("rag_models") or {}
    providers = section.get("providers") or []
    preferred = (
        os.environ.get("RAG_MODELS_PROVIDER") or section.get("provider") or "CORSUR"
    )
    preferred_ids = [str(preferred).strip(), "CORSUR", "PHOENIX"]
    seen: set[str] = set()
    for pid in preferred_ids:
        key = pid.lower()
        if not key or key in seen:
            continue
        seen.add(key)
        for entry in providers:
            if str(entry.get("id", "")).lower() == key:
                base = str(entry.get("base_url") or "").strip().rstrip("/")
                if base:
                    api_key_env = str(entry.get("api_key_env") or "").strip()
                    return base, api_key_env
    for entry in providers:
        base = str(entry.get("base_url") or "").strip().rstrip("/")
        if base:
            return base, str(entry.get("api_key_env") or "").strip()
    return "", ""


def _normalize_base_url(url: str) -> str:
    """Адрес для сравнения: без хвостового слеша и без дефолтного порта."""
    u = str(url or "").strip().rstrip("/").lower()
    for scheme, port in (("https://", ":443"), ("http://", ":80")):
        if u.startswith(scheme) and u.endswith(port):
            return u[: -len(port)]
    return u

def _provider_entry_by_base_url(data: dict, url: str) -> dict:
    """Запись из rag_models.providers, чей base_url совпадает с переданным."""
    target = _normalize_base_url(url)
    if not target:
        return {}
    for entry in (data.get("rag_models") or {}).get("providers") or []:
        if _normalize_base_url(str(entry.get("base_url") or "")) == target:
            return entry
    return {}

def _is_chat_only_llm_gateway(data: dict, url: str) -> bool:
    """Шлюз объявлен chat-only в rag_models.providers (```chat_only: true```).

    Раньше здесь была подстрока ```llm.corsur``` в имени хоста. Из-за неё ЛЮБОЙ
    адрес в этом домене молча уезжал на другого провайдера — включая новый
    шлюз, который эмбеддинги как раз обслуживает. Признак должен приходить
    из конфига, а не из имени хоста.
    """
    return bool(_provider_entry_by_base_url(data, url).get("chat_only"))


def _apply_rag_models_client_env(data: dict) -> dict:
    """env RAG_MODELS_CLIENT** / RAG_MODELS_SERVICE_URL перекрывают yml."""
    out = dict(data)
    rmc = dict(out.get("rag_models_client") or {})
    env_base = _env_str("RAG_MODELS_CLIENT_BASE_URL") or _env_str("RAG_MODELS_SERVICE_URL")
    env_key = _env_str("RAG_MODELS_CLIENT_API_KEY_ENV")
    if env_base:
        rmc["base_url"] = env_base.rstrip("/")
        print(
            f"[CFG-DEBUG] rag_models_client.base_url <- env {env_base!r}",
            flush=True,
        )
    if env_key is not None:
        rmc["api_key_env"] = env_key
        print(
            f"[CFG-DEBUG] rag_models_client.api_key_env <- env {env_key!r}",
            flush=True,
        )
    env_batch = _env_str("RAG_MODELS_CLIENT_EMBED_BATCH_SIZE")
    if env_batch is not None:
        try:
            rmc["embed_batch_size"] = max(1, int(env_batch))
            print(
                f"[CFG-DEBUG] rag_models_client.embed_batch_size <- env {env_batch!r}",
                flush=True,
            )
        except ValueError:
            pass
    env_conc = _env_str("RAG_MODELS_CLIENT_EMBED_CONCURRENCY")
    if env_conc is not None:
        try:
            rmc["embed_concurrency"] = max(1, int(env_conc))
            print(
                f"[CFG-DEBUG] rag_models_client.embed_concurrency <- env {env_conc!r}",
                flush=True,
            )
        except ValueError:
            pass
    out["rag_models_client"] = rmc
    return out


def _sanitize_rag_models_client_url(data: dict) -> dict:
    """Не направлять эмбеддинги на chat-шлюз помеченный chat-only (там 404 на /v1/embeddings)."""
    out = dict(data)
    rmc = dict(out.get("rag_models_client") or {})
    base = str(rmc.get("base_url") or "").strip().rstrip("/")
    # Совпадение с llm_service.base_url больше НЕ считаем ошибкой: единый шлюз
    # (litellm/bifrost) обслуживает и chat, и эмбеддинги с одного адреса.
    fixup = bool(base and _is_chat_only_llm_gateway(out, base))
    if fixup:
        emb_base, emb_key = _embedding_gateway_from_providers(out)
        if emb_base and emb_base != base:
            print(
                f"[CFG-DEBUG] rag_models_client.base_url {base!r} -> {emb_base!r} "
                "(chat-шлюз не обслуживает /v1/embeddings)",
                flush=True,
            )
            rmc["base_url"] = emb_base
            if not str(rmc.get("api_key_env") or "").strip() and emb_key:
                rmc["api_key_env"] = emb_key
    elif not str(rmc.get("api_key_env") or "").strip():
        _, emb_key = _embedding_gateway_from_providers(out)
        if emb_key:
            rmc["api_key_env"] = emb_key
    out["rag_models_client"] = rmc
    return out


def _apply_postgres_env_overrides(data: dict) -> dict:
    """Приоритет подключения к PostgreSQL: env -> config.yml -> defaults."""
    out = dict(data)
    pg = dict(out.get("postgresql") or {})

    host = _env_str("POSTGRES_HOST")
    port = _env_int("POSTGRES_PORT")
    db = _env_str("POSTGRES_DB")
    user = _env_str("POSTGRES_USER")
    password = _env_str("POSTGRES_PASSWORD")
    embedding_dim = _env_int("RAG_EMBEDDING_DIM")

    if host is not None:
        pg["host"] = host
    if port is not None:
        pg["port"] = port
    if db is not None:
        pg["database"] = db
    if user is not None:
        pg["user"] = user
    if password is not None:
        pg["password"] = password
    if embedding_dim is not None:
        pg["embedding_dim"] = embedding_dim

    out["postgresql"] = pg
    return out


def _apply_backend_service_env(data: dict) -> dict:
    """env backend_service_port (или BACKEND_SERVICE_PORT) перекрывает urls.backend_service_port."""
    out = dict(data)
    url = (os.environ.get("backend_service_port") or os.environ.get("BACKEND_SERVICE_PORT") or "").strip()
    if url:
        be = dict(out.get("backend") or {})
        be["base_url"] = url.rstrip("/")
        out["backend"] = be
    return out

def _apply_rag_env_overrides(data: dict) -> dict:
    """Приоритет выбранных rag-настроек: env > config.yml > дефолт поля.
    По умолчанию pydantic читает env только как дефолт поля - ключ, прописанный
    в yml, перекрывает env. Для перечисленных здесь ключей env главнее yml:
    удобно крутить на поде без пересборки образа
    """
    overrides = {
        "RAG_MEMPROBE": ("memprobe", lambda v: str(v).strip().lower()),
        "RAG_HYBRID_MAX_CHUNKS_PER_DOCUMENT": ("hybrid_max_chunks_per_document", int),
        "RAG_VECTOR_MAX_CHUNKS_PER_DOCUMENT": ("vector_max_chunks_per_document", int),
        "RAG_BM25_RETAIN_SECONDS": ("bm25_retain_seconds", float),
        "RAG_BM25_STORE_ENABLED": (
            "bm25_store_enabled",
            lambda v: str(v).strip().lower() == "true",
        ),
        "RAG_BM25_MALLOC_TRIM": (
            "bm25_malloc_trim",
            lambda v: str(v).strip().lower() == "true",
        ),
    }
    out = dict(data)
    rag = dict(out.get("rag") or {})
    changed = False
    for env_name, (key, cast) in overrides.items():
        raw = (os.environ.get(env_name) or "").strip()
        if not raw:
            continue
        try:
            rag[key] = cast(raw)
            changed = True
            print(f"[CFG-DEBUG] rag.{key} <- env {env_name}={raw!r}", flush=True)
        except (TypeError, ValueError):
            print(
                f"[CFG-DEBUG] env {env_name}={raw!r} невалиден - игнорирую",
                flush=True,
            )
    if changed:
        out["rag"] = rag
    return out


def _apply_rag_models_provider_env(data: dict) -> dict:
    """env RAG_MODELS_PROVIDER главнее yml: переключение провайдера без пересборки.
    (pydantic сам по себе даёт приоритет yml над env - см. _apply_rag_env_overrides.)
    """
    out = dict(data)
    raw = (os.environ.get("RAG_MODELS_PROVIDER") or "").strip()
    if raw:
        section = dict(out.get("rag_models") or {})
        section["provider"] = raw
        out["rag_models"] = section
        print(
            f"[CFG-DEBUG] rag_models.provider <- env RAG_MODELS_PROVIDER={raw!r}",
            flush=True,
        )
    return out


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    docs_url: str = "/docs"
    redoc_url: str = "/redoc"


class CorsConfig(BaseModel):
    allowed_origins: List[str] = Field(default_factory=list)
    allow_credentials: bool = True
    allow_methods: List[str] = ["*"]
    allow_headers: List[str] = ["*"]


class AppConfig(BaseModel):
    title: str = "RAG Service"
    description: str = "Логика RAG: индексация документов, поиск по pgvector и BM25"
    version: str = "1.0.0"


class LoggingConfig(BaseModel):
    level: str = "INFO"
    format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    noisy_level: str = "WARNING"


class AuditConfig(BaseModel):
    """CEF-аудит. Пишется отдельным каналом (логгер cef, свой обработчик,
    propagate=False), поэтому уровень логирования на него не влияет."""

    # Аудировать ли фоновый поллинг интерфейса: успешные GET по
    # /reindex/status и спискам документов. Один опрос плашки переиндексации -
    # пять запросов в SVC-RAG, то есть пять событий OBJ001 каждые несколько
    # секунд с каждой открытой вкладки.
    #
    # true (по умолчанию) - аудируем, но не каждое обращение, а не чаще
    # cef_polling_interval_seconds. false - не аудируем такие чтения вовсе.
    # Изменения (POST/DELETE), ошибки и отказы доступа аудируются ВСЕГДА,
    # независимо от обоих полей. ENV перекрывает: CEF_AUDIT_POLLING.
    cef_polling: bool = True

    # Окно троттлинга, секунд. Одно событие на (пользователь, метод, путь) в
    # окно; в нём проставляется cn1 = сколько обращений оно представляет.
    # 0 - без троттлинга, событие на каждое обращение (поведение до правки).
    # Пользователь входит в ключ намеренно: аудит должен показывать, КТО
    # обращался, иначе событие приписывалось бы тому, кто попал первым.
    # ENV перекрывает: CEF_AUDIT_POLLING_INTERVAL.
    cef_polling_interval_seconds: int = 120


class RagModelsClientConfig(BaseModel):
    base_url: str = Field(...)
    timeout: float = Field(...)
    # Сколько чанков отправлять за один POST /v1/embeddings (меньше → меньше пик RAM)
    embed_batch_size: int = int(os.environ.get("RAG_MODELS_CLIENT_EMBED_BATCH_SIZE", "24"))
    # Сколько POST /embeddings одновременно при индексации (1 = последовательно).
    # K8s: RAG_MODELS_CLIENT_EMBED_CONCURRENCY
    embed_concurrency: int = int(os.environ.get("RAG_MODELS_CLIENT_EMBED_CONCURRENCY", "4"))
    # OpenAI-совместимый шлюз (llm.corsur): тот же ключ, что у backend LLM
    api_key_env: str = Field(default="")
    embedding_model: str = Field(default="")
    reranker_model: str = Field(default="")


class RagModelsProviderEntry(BaseModel):
    """Внешний OpenAI-совместимый провайдер эмбеддингов/реранка (Phoenix, свой vLLM)."""

    id: str
    kind: str = "openai-compat"
    base_url: str = ""
    api_key_env: str = ""
    timeout: float = 300.0
    embed_path: str = "/v1/embeddings"
    rerank_path: str = "/v1/rerank"
    # true - шлюз обслуживает только chat/completions и 404-ит на эмбеддингах.
    # Тогда эмбеддинги уводятся на другого провайдера.
    # ВНИМАНИЕ: имя поля должно совпадать с ключом в yml и с чтением в
    # _is_chat_only_llm_gateway (:150). Раньше здесь было ```cant_only``` —
    # опечатка, из-за неё ```chat_only: true``` молча отбрасывался pydantic,
    # и CORSUR можно было выбрать эмбеддером, получая 404 на /v1/embeddings.
    chat_only: bool = False
    # Формат тела запроса эмбеддингов:
    #   "openai"              - {"model", "input"} на embed_path (по умолчанию);
    #   "bifrost-passthrough" - {"model", "messages", "texts", "input", "kind"}
    #                           на /v1/chat/completions. Нужен, когда на шлюзе
    #                           опубликован только chat-маршрут, а эмбеддер за ним
    #                           достаётся через x-bf-passthrough-extra-params.
    embed_payload_style: str = "openai"
    # Формат тела запроса реранков:
    #   "openai"              - {"model", "query", "documents", "top_n"} на rerank_path (по умолчанию);
    #   "bifrost-passthrough" - {"model", "query", "documents", "top_n"} на /v1/rerank. Нужен, когда на шлюзе
    #                           опубликован только rerank-маршрут, а реранкер за ним
    #                           достаётся через x-bf-passthrough-extra-params.
    rerank_payload_style: str = "openai"
    # Доп. заголовки провайдера, например x-bf-passthrough-extra-params: "true".
    extra_headers: Dict[str, str] = Field(default_factory=dict)
    # id моделей НЕ хардкодим в yml - приходят из выбора в UI (POST /v1/schema/models-provider).
    # Значения здесь - только стартовый дефолт до первого выбора.
    embedding_model: str = ""
    reranker_model: str = ""


class RagModelsProvidersConfig(BaseModel):
    """Выбор источника моделей RAG. native = svc-rag-models (как было всегда)."""

    provider: str = "native"
    providers: List[RagModelsProviderEntry] = Field(default_factory=list)


class PostgreSQLConfig(BaseModel):
    host: str = Field(...)
    port: int = Field(...)
    database: str = Field(...)
    user: str = Field(...)
    password: str = Field(...)
    embedding_dim: int = Field(...)


class MinioConfig(BaseModel):
    endpoint: str = Field(...)
    port: int = Field(...)
    access_key: str = Field(...)
    secret_key: str = Field(...)
    use_ssl: bool = Field(...)
    bucket_name: str = Field(...)
    documents_bucket_name: str = Field(...)


class OcrConfig(BaseModel):
    url: str = Field(...)
    timeout: float = Field(...)


class RagServiceConfig(BaseModel):
    enabled: bool = True
    # Гибридный поиск: вектор + BM25
    use_hybrid_search: bool = os.environ.get("RAG_USE_HYBRID_SEARCH", "true").lower() == "true"
    hybrid_bm25_weight: float = float(os.environ.get("RAG_HYBRID_BM25_WEIGHT", "0.3"))
    # Диверсификация применяется только после weighted RRF и не использует
    # cosine-пороги (RRF и cosine имеют разные шкалы).
    hybrid_diversify_results: bool = os.environ.get("RAG_HYBRID_DIVERSIFY_RESULTS", "true").lower() == "true"
    # 0 = выключить лимит (не вытеснять чанки одного большого документа).
    # Раньше дефолт 2 резал большие DOCX до 2 фрагментов → ложные «Не знаю».
    hybrid_max_chunks_per_document: int = int(os.environ.get("RAG_HYBRID_MAX_CHUNKS_PER_DOCUMENT", "0"))

    # Диверсификация чистого vector-поиска: максимум N чанков одного документа
    # в выдаче (0 = выключить и вернуть сырой top-k).
    vector_max_chunks_per_document: int = int(
        os.environ.get("RAG_VECTOR_MAX_CHUNKS_PER_DOCUMENT", "0")
    )

    # Хранить готовые BM25-индексы в Postgres и отпускать их из памяти
    # false - прежнее поведение: собирать из текста и держать в памяти.
    # ENV перекрывает: RAG_BM25_STORE_ENABLED
    bm25_store_enabled: bool = (
        os.environ.get("RAG_BM25_STORE_ENABLED", "true").lower() == "true"
    )

    # Подробный учёт памяти в лог DEBUG: кто сколько занял и сколько вернул.
    # ENV перекрывает: RAG_MEMPROBE
    memprobe: str = os.environ.get("RAG_MEMPROBE", "on").strip().lower()

    # Сколько секунд держать индекс после последнего поиска по нему.
    # ENV перекрывает: RAG_BM25_RETAIN_SECONDS
    bm25_retain_seconds: float = float(
        os.environ.get("RAG_BM25_RETAIN_SECONDS", "2")
    )

    # Возвращать ли ядру память отпущенного индекса сразу (glibc malloc_trim).
    # ENV перекрывает: RAG_BM25_MALLOC_TRIM
    bm25_malloc_trim: bool = (
        os.environ.get("RAG_BM25_MALLOC_TRIM", "true").lower() == "true"
    )

    # Реранкинг через SVC-RAG-MODELS
    use_reranking: bool = os.environ.get("RAG_USE_RERANKING", "false").lower() == "true"
    rerank_top_k: int = int(os.environ.get("RAG_RERANK_TOP_K", "20"))
    # Порог по финальному скору после реранка: 0.7*логит(CrossEncoder) + 0.3*cosine; не шкала 0..1.
    # 0 = не отсекать; положительные значения часто выкидывают все чанки — подбирайте с осторожностью.
    rerank_min_score: float = float(os.environ.get("RAG_RERANK_MIN_SCORE", "0"))
    sentence_window: int = int(os.environ.get("RAG_SENTENCE_WINDOW", "0"))
    chunk_size: int = 1000
    chunk_overlap: int = 200
    # Минимальный косинусный скор чанка после pgvector — отсекает явно нерелевантные результаты до реранка.
    # 0 = не фильтровать; типичные мультиязычные эмбеддинги дают релевантные чанки на 0.12–0.25,
    # поэтому порог выше 0.10 массово «съедает» правильные ответы. При всех чанках ниже порога
    # включится rescue top-N (см. filter_by_min_vector_similarity).
    min_vector_similarity: float = float(os.environ.get("RAG_MIN_VECTOR_SIMILARITY", "0.05"))
    # Минимальная длина чанка для low_signal-фильтра; 0 = не фильтровать.
    min_chunk_length: int = int(os.environ.get("RAG_MIN_CHUNK_LENGTH", "40"))

    # Иерархическое индексирование
    use_hierarchical_indexing: bool = os.environ.get("RAG_USE_HIERARCHICAL", "true").lower() == "true"
    hierarchical_threshold: int = int(os.environ.get("RAG_HIERARCHICAL_THRESHOLD", "10000"))
    hierarchical_chunk_size: int = int(os.environ.get("RAG_HIERARCHICAL_CHUNK_SIZE", "1500"))
    hierarchical_chunk_overlap: int = int(os.environ.get("RAG_HIERARCHICAL_CHUNK_OVERLAP", "200"))
    intermediate_summary_chunks: int = int(os.environ.get("RAG_INTERMEDIATE_SUMMARY_CHUNKS", "8"))
    create_full_summary_via_llm: bool = os.environ.get("RAG_CREATE_FULL_SUMMARY_VIA_LLM", "false").lower() == "true"
    enable_graph_rag: bool = os.environ.get("RAG_ENABLE_GRAPH", "true").lower() == "true"
    # Разрешить LLM-as-a-Judge в POST /search (доп. вызов llm-service; только при eval_llm_judge=true в теле)
    eval_llm_judge_allowed: bool = os.environ.get("RAG_EVAL_LLM_JUDGE_ALLOWED", "false").lower() == "true"

    # ANN-индекс pgvector: hnsw (дефолт) | ivfflat. Свап через K8s env без смены dim/стратегий.
    # RAG_VECTOR_INDEX_METHOD=hnsw|ivfflat
    vector_index_method: str = (
        os.environ.get("RAG_VECTOR_INDEX_METHOD", "hnsw") or "hnsw"
    ).strip().lower()
    # IVFFlat: число списков при CREATE INDEX; probes — на каждый поиск (SET ivfflat.probes).
    vector_ivfflat_lists: int = int(os.environ.get("RAG_VECTOR_IVFFLAT_LISTS", "100"))
    vector_ivfflat_probes: int = int(os.environ.get("RAG_VECTOR_IVFFLAT_PROBES", "10"))
    # HNSW: параметры построения и поиска.
    vector_hnsw_m: int = int(os.environ.get("RAG_VECTOR_HNSW_M", "16"))
    vector_hnsw_ef_construction: int = int(
        os.environ.get("RAG_VECTOR_HNSW_EF_CONSTRUCTION", "64")
    )
    vector_hnsw_ef_search: int = int(os.environ.get("RAG_VECTOR_HNSW_EF_SEARCH", "40"))


class LLMServiceConfig(BaseModel):
    base_url: str = Field(...)
    timeout: float = Field(...)
    default_model: str = Field(...)


class BackendServiceConfig(BaseModel):
    base_url: str = ""
    timeout: float = 120.0


class Settings(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    cors: CorsConfig = Field(default_factory=CorsConfig)
    app: AppConfig = Field(default_factory=AppConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    audit: AuditConfig = Field(default_factory=AuditConfig)

    # Эти секции содержат обязательные адреса/учётные данные. Не создаём их
    # пустыми при импорте модуля: значения сначала загружаются из config.yml/env,
    # затем Pydantic валидирует готовый словарь в ``from_yaml``.
    rag_models_client: RagModelsClientConfig
    rag_models: RagModelsProvidersConfig = Field(
        default_factory=RagModelsProvidersConfig
    )
    postgresql: PostgreSQLConfig
    minio: MinioConfig
    ocr: OcrConfig
    llm_service: LLMServiceConfig

    rag: RagServiceConfig = Field(default_factory=RagServiceConfig)
    backend: BackendServiceConfig = Field(default_factory=BackendServiceConfig)

    @classmethod
    def from_yaml(cls, config_path: Optional[str] = None):
        if not config_path or config_path == "":
            for path in [
                "config/config.yml",
                "../config/config.yml",
                Path(__file__).resolve().parent.parent.parent / "config" / "config.yml",
            ]:
                p = str(path) if hasattr(path, "resolve") else path
                if os.path.exists(p):
                    config_path = p
                    break
            else:
                raise ValueError(
                    "Файл config.yml не найден: настройки должны приходить из env Kubernetes или config.yml"
                )
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            data = _apply_urls_section(data)
            data = _apply_rag_models_client_env(data)
            data = _sanitize_rag_models_client_url(data)
            data = _apply_postgres_env_overrides(data)
            data = _apply_backend_service_env(data)
            data = _apply_rag_env_overrides(data)
            data = _apply_rag_models_provider_env(data)
            return cls(**data)
        except Exception as e:
            raise ValueError(f"Ошибка загрузки конфига {config_path}: {e}")


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings.from_yaml(os.environ.get("CONFIG_PATH", ""))
    return _settings


def get_settings_diagnostics(settings_obj: Optional[Settings] = None) -> Dict[str, Any]:
    """Безопасная сводка фактически загруженной конфигурации для startup-логов.

    Функция намеренно не возвращает пароли, access/secret keys и другие секреты.
    Необязательный аргумент сохраняет совместимость с вызовами как
    ``get_settings_diagnostics()``, так и ``get_settings_diagnostics(settings)``.
    """
    cfg = settings_obj or get_settings()
    configured_path = (os.environ.get("CONFIG_PATH") or "").strip()
    return {
        "config_source": configured_path or "auto-discovered config.yml",
        "docker_runtime": _docker_runtime(),
        "server": {
            "host": cfg.server.host,
            "port": cfg.server.port,
        },
        "rag_models": {
            "base_url": cfg.rag_models_client.base_url,
            "api_key_env": cfg.rag_models_client.api_key_env or None,
            "timeout": cfg.rag_models_client.timeout,
            "embed_batch_size": cfg.rag_models_client.embed_batch_size,
            "embed_concurrency": cfg.rag_models_client.embed_concurrency,
            "provider": cfg.rag_models.provider,
            "providers_configured": [p.id for p in cfg.rag_models.providers],
        },
        "postgresql": {
            "host": cfg.postgresql.host,
            "port": cfg.postgresql.port,
            "database": cfg.postgresql.database,
            "user": cfg.postgresql.user,
            "embedding_dim": cfg.postgresql.embedding_dim,
        },
        "ocr": {
            "url": cfg.ocr.url,
            "timeout": cfg.ocr.timeout,
        },
        "llm_service": {
            "base_url": cfg.llm_service.base_url,
            "timeout": cfg.llm_service.timeout,
            "default_model": cfg.llm_service.default_model,
        },
        "backend": {
            "base_url": cfg.backend.base_url,
            "timeout": cfg.backend.timeout,
        },
        "rag": {
            "enabled": cfg.rag.enabled,
            "use_hybrid_search": cfg.rag.use_hybrid_search,
            "enable_graph_rag": cfg.rag.enable_graph_rag,
            "vector_index_method": cfg.rag.vector_index_method,
            "vector_ivfflat_lists": cfg.rag.vector_ivfflat_lists,
            "vector_ivfflat_probes": cfg.rag.vector_ivfflat_probes,
            "vector_hnsw_m": cfg.rag.vector_hnsw_m,
            "vector_hnsw_ef_construction": cfg.rag.vector_hnsw_ef_construction,
            "vector_hnsw_ef_search": cfg.rag.vector_hnsw_ef_search,
        },
    }


settings = get_settings()
