"""
Тонкий async-клиент для SVC-RAG.
"""

import os
import uuid
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import urlparse

import httpx

from backend.settings.config import get_settings
from backend.settings.logging import get_logger
from backend.settings.logging.errors import logged_suppress
from backend.rag_query.llm_judge import judge_and_filter_hits
from backend.rag_query.stage_timer import StageTimer

logger = get_logger(__name__)


class RagReindexInProgress(RuntimeError):
    """SVC-RAG вернул 409: стор переиндексируется, поиск временно недоступен."""


class RagDuplicateDocument(RuntimeError):
    """SVC-RAG/проверка: документ с таким содержимым уже есть в scope."""

    def __init__(
        self,
        message: str,
        *,
        document_id: Optional[int] = None,
        filename: Optional[str] = None,
        detail: Any = None,
    ):
        super().__init__(message)
        self.document_id = document_id
        self.filename = filename
        self.detail = detail


def _is_duplicate_document_detail(detail: Any) -> bool:
    from backend.rag_query.document_duplicate import is_duplicate_detail

    return is_duplicate_detail(detail)


def _looks_like_rag_model_error(detail: Any) -> bool:
    """Пытаемся распознать embed/rerank ошибки по тексту ответа SVC-RAG.

    Backend не знает внутренние INT006 SVC-RAG напрямую, поэтому опираемся на
    response body (detail/text) от SVC-RAG.
    """
    try:
        if isinstance(detail, dict):
            parts = []
            for v in detail.values():
                if v is None:
                    continue
                parts.append(str(v))
            text = " ".join(parts)
        else:
            text = str(detail or "")
    except Exception:
        text = str(detail or "")
    low = text.lower()

    keywords = [
        "embed",
        "embedding",
        "embeddings",
        "эмбед",
        "эмбеддин",
        "rerank",
        "reranker",
        "реранк",
        "модель",
        "ошибка модели",
        "llm",
    ]
    return any(k in low for k in keywords)


def _normalize_rag_service_base(url: str) -> str:
    """Базовый origin SVC-RAG без хвоста /v1 (префикс API добавляется в _rag_request_url)."""
    u = (url or "").strip().rstrip("/")
    if u.endswith("/v1"):
        return u[:-3].rstrip("/")
    return u


def _rag_request_url(base_url: str, path: str) -> str:
    """SVC-RAG монтирует app.api под prefix=/v1 (см. SVC-RAG/app/main.py)."""
    b = (base_url or "").strip().rstrip("/")
    p = path if path.startswith("/") else f"/{path}"
    return f"{b}/v1{p}"


def _with_chunk_index_form_data(
    data: Dict[str, Any],
    *,
    chunk_size: Optional[int] = None,
    chunk_overlap: Optional[int] = None,
    chunking_strategy: Optional[str] = None,
) -> Dict[str, Any]:
    if chunk_size is not None:
        data["chunk_size"] = str(int(chunk_size))
    if chunk_overlap is not None:
        data["chunk_overlap"] = str(int(chunk_overlap))
    if chunking_strategy is not None and str(chunking_strategy).strip():
        data["chunking_strategy"] = str(chunking_strategy).strip().lower()
    return data


# Ниже этого косинуса выдача считается провальной и запускается переспрос
# (коммит 49). 0.3 - стартовое значение: ноль означал бы «только пустая
# выдача», а её почти не бывает из-за rescue top-N в SVC-RAG.
# Перекрывается переменной RAG_REWRITE_MIN_COSINE.
_REWRITE_MIN_COSINE_DEFAULT = "0.3"


def _svc_rag_search_timeout() -> httpx.Timeout:
    """Ожидание ответа на поиск.

    Дефолтные 60с общего клиента рассчитаны на быстрый эмбеддер. Тяжёлая модель
    на CPU тратит на вектор запроса 40-80с — запрос рвётся по ReadTimeout
    """
    try:
        # 240, а не 180: худший путь SVC-RAG - три попытки эмбеддинга вопроса
        # по ~61 с до 504 шлюза, ~183 с. 25.09 SVC-RAG нашёл фрагменты за
        # 183.1 с, а бэкенд бросил запрос на 180-й - ребёнок получил ноль.
        read_sec = float(os.getenv("SVC_RAG_SEARCH_READ_TIMEOUT", "240"))
    except ValueError:
        read_sec = 240.0
    read_sec = max(30.0, read_sec)
    return httpx.Timeout(30.0, read=read_sec)


def _svc_rag_document_index_timeout() -> httpx.Timeout:
    """Ожидание ответа POST /…/documents: парсинг + чанки + серия embed к rag-models."""
    try:
        read_sec = float(os.getenv("SVC_RAG_INDEX_READ_TIMEOUT", "900"))
    except ValueError:
        read_sec = 900.0
    read_sec = max(60.0, read_sec)
    return httpx.Timeout(120.0, read=read_sec)


def _rag_query_preview(q: str, max_len: int = 72) -> str:
    s = (q or "").replace("\n", " ").strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 3] + "..."


def _dedupe_jaccard_threshold() -> float:
    try:
        return float(os.getenv("RAG_DEDUP_JACCARD", "0.88"))
    except ValueError:
        return 0.88


def _log_backend_rag_strategy_banner(
    *,
    path: str,
    strategy: Optional[str],
    k: int,
    document_id: Optional[int],
    use_reranking: Optional[bool],
    hits: int,
    query_preview: str,
    prep_suffix: str,
    from_cache: bool,
) -> None:
    """Видно в `docker compose logs -f astrachat-backend` без svc-rag."""
    bar = "*" * 72
    logger.debug(bar)
    logger.debug(
        "[astrachat-backend RAG] Использована стратегия в запросе к SVC-RAG: %s",
        strategy or "(default)",
    )
    logger.debug(
        "[astrachat-backend RAG] endpoint=%s k=%s document_id=%s use_reranking=%s",
        path,
        k,
        document_id,
        use_reranking,
    )
    logger.debug(
        "[astrachat-backend RAG] хитов после ответа=%s %s",
        hits,
        "(из кэша)" if from_cache else "",
    )
    logger.debug("[astrachat-backend RAG] запрос: %s", query_preview)
    if prep_suffix:
        logger.debug("[astrachat-backend RAG] %s", prep_suffix.strip())
    logger.debug(
        "[astrachat-backend RAG] Реальный пайплайн (косинус / BM25 / реранк / graph) смотрите в логах контейнера svc-rag — блок из %s звёздочек «Использована стратегия поиска».",
        len(bar),
    )
    logger.debug(bar)


class RagClient:
    """
    Тонкий async‑клиент для SVC-RAG.
    Не содержит логики поиска - только HTTP‑вызовы.
    """

    def __init__(self, base_url: Optional[str] = None, timeout: float = 60.0):
        settings = get_settings()
        if base_url:
            self.base_url = _normalize_rag_service_base(base_url)
        else:
            self.base_url = _normalize_rag_service_base(
                settings.microservice_http_base(
                    "rag_service_docker", "rag_service_port"
                )
            )
        self.timeout = timeout

    def _cef_extra(self, method: str, path: str, request_uuid: str) -> Dict[str, Any]:
        """Поля CEF extension для исходящего вызова к SVC-RAG (INT005 / INT006)."""
        parsed = urlparse(self.base_url)
        dhost = parsed.hostname or self.base_url
        dpt: int
        if parsed.port:
            dpt = int(parsed.port)
        elif (parsed.scheme or "").lower() == "https":
            dpt = 443
        else:
            dpt = 80
        return {
            "_deviceDirection": 1,
            "dhost": dhost,
            "dpt": dpt,
            "methodName": f"{method} /v1{path}",
            "serviceName": "SVC-RAG",
            "requestUuid": request_uuid,
        }

    def _cef_identity_headers(self) -> Dict[str, str]:
        """Прокинуть инициатора в SVC-RAG для сквозного CEF (suser/suid)."""
        headers: Dict[str, str] = {}
        try:
            from backend.settings.cef_logger.cef_audit_context import cef_audit_peek

            _req, _user, _ = cef_audit_peek()
            if not _user:
                return headers
            suser = str(_user.get("username") or _user.get("user_id") or "").strip()
            suid = str(_user.get("user_id") or "").strip()
            sntdom = str(_user.get("sntdom") or _user.get("domain") or "").strip()
            if suser:
                headers["X-CEF-Suser"] = suser
            if suid:
                headers["X-CEF-Suid"] = suid
            if sntdom:
                headers["X-CEF-Sntdom"] = sntdom
        except Exception:
            pass
        return headers

    def _svc_rag_api_key_headers(self) -> Dict[str, str]:
        """Общий секрет backend↔svc-rag (если задан SVC_RAG_API_KEY / RAG_API_KEY)."""
        import os

        key = (
            os.getenv("SVC_RAG_API_KEY") or os.getenv("RAG_API_KEY") or ""
        ).strip()
        return {"X-API-Key": key} if key else {}

    async def _request(
        self,
        method: str,
        path: str,
        json: Optional[Dict[str, Any]] = None,
        files: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        http_timeout: Optional[Union[float, httpx.Timeout]] = None,
    ) -> Any:
        url = _rag_request_url(self.base_url, path)
        client_timeout = self.timeout if http_timeout is None else http_timeout
        _cef_rid = uuid.uuid4().hex
        # Поллинг статусов переиндексации (*/reindex/status) порождает очень много CEF
        # и обычно не нужен для аудита. Пропускаем такие события целиком.
        _cef_skip = path.rstrip("/").endswith("/reindex/status") or path.rstrip(
            "/"
        ) in ("/health",)
        _headers = {**self._cef_identity_headers(), **self._svc_rag_api_key_headers()}
        # Модель текущего чата - чтобы LLM-вызовы, которые SVC-RAG сделает по
        # ходу этого запроса (judge, суммаризация), вернулись в неё же.
        # Пусто - SVC-RAG модель не пришлёт, backend возьмёт текущую.
        with logged_suppress(logger):
            from backend.settings.cef_logger.cef_audit_context import chat_model_peek

            _model_path = chat_model_peek()
            if _model_path:
                _headers["X-Astra-Model"] = _model_path
        try:
            async with httpx.AsyncClient(timeout=client_timeout) as client:
                resp = await client.request(
                    method=method,
                    url=url,
                    json=json,
                    files=files,
                    data=data,
                    params=params,
                    headers=_headers or None,
                )
                resp.raise_for_status()
                result = resp.json()
            if not _cef_skip:
                with logged_suppress(logger):
                    from backend.settings.cef_logger.cef_audit_context import (
                        cef_audit_peek,
                    )
                    from backend.settings.cef_logger.cef_logger import log_cef_event
                    from backend.settings.cef_logger.poll_audit import (
                        is_polling_read,
                        poll_audit_decision,
                        poll_count_extra,
                    )

                    _req, _user, _ = cef_audit_peek()
                    # Плашка переиндексации даёт ЧЕТЫРЕ исходящих вызова на
                    # каждый свой тик (три /reindex/status + список документов),
                    # и каждый порождал INT005. Прореживаем: одно событие в
                    # окно на (пользователь, путь), в нём cn1 - сколько
                    # обращений оно представляет. Ошибки и любые методы кроме
                    # GET/HEAD идут мимо этой ветки и аудируются как раньше.
                    _emit, _poll_count = True, 0
                    if is_polling_read(method, path, 200):
                        _emit, _poll_count = poll_audit_decision(_user, method, path)
                    if _emit:
                        log_cef_event(
                            "INT005",
                            request=_req,
                            current_user=_user,
                            status_code=200,
                            extra={
                                **self._cef_extra(method, path, _cef_rid),
                                **poll_count_extra(_poll_count),
                            },
                        )
            return result
        except httpx.HTTPStatusError as e:
            detail = None
            try:
                detail = e.response.json()
            except Exception:
                # logger.exception("Ошибка операции")
                detail = e.response.text
            if not _cef_skip:
                with logged_suppress(logger):
                    from backend.settings.cef_logger.cef_audit_context import (
                        cef_audit_peek,
                    )
                    from backend.settings.cef_logger.cef_logger import log_cef_event

                    _req, _user, _ = cef_audit_peek()
                    _ex = self._cef_extra(method, path, _cef_rid)
                    _ex["codeStatus"] = str(e.response.status_code)
                    _ex["textStatus"] = str(detail or "")[:512]
                    event_id = (
                        "INT007" if _looks_like_rag_model_error(detail) else "INT006"
                    )
                    log_cef_event(
                        event_id,
                        request=_req,
                        current_user=_user,
                        status_code=e.response.status_code,
                        extra=_ex,
                    )
            msg = f"SVC-RAG {method} {url} failed: {e.response.status_code} {detail}"
            if e.response.status_code == 409:
                parsed = detail
                if isinstance(detail, dict) and "detail" in detail:
                    parsed = detail.get("detail")
                if _is_duplicate_document_detail(parsed):
                    from backend.rag_query.document_duplicate import DUPLICATE_MESSAGE

                    message = DUPLICATE_MESSAGE
                    doc_id = None
                    fname = None
                    if isinstance(parsed, dict):
                        message = str(parsed.get("message") or message)
                        try:
                            doc_id = (
                                int(parsed["document_id"])
                                if parsed.get("document_id") is not None
                                else None
                            )
                        except (TypeError, ValueError):
                            doc_id = None
                        fname = parsed.get("filename")
                    elif isinstance(parsed, str) and parsed.strip():
                        message = parsed.strip()
                    raise RagDuplicateDocument(
                        message,
                        document_id=doc_id,
                        filename=fname,
                        detail=parsed,
                    ) from e
                raise RagReindexInProgress(msg) from e
            raise RuntimeError(msg) from e
        except Exception as e:
            logger.exception("Ошибка операции")
            if not _cef_skip:
                with logged_suppress(logger):
                    from backend.settings.cef_logger.cef_audit_context import (
                        cef_audit_peek,
                    )
                    from backend.settings.cef_logger.cef_logger import log_cef_event

                    _req, _user, _ = cef_audit_peek()
                    _ex = self._cef_extra(method, path, _cef_rid)
                    _ex["codeStatus"] = "EXCEPTION"
                    _ex["textStatus"] = str(e)[:512]
                    event_id = "INT006"
                    if isinstance(e, httpx.ConnectError):
                        _ex["codeStatus"] = "CONNECT_ERROR"
                    elif isinstance(e, httpx.NetworkError):
                        _ex["codeStatus"] = "NETWORK_ERROR"
                    elif isinstance(e, httpx.ReadTimeout):
                        _ex["codeStatus"] = "READ_TIMEOUT"
                    elif isinstance(e, httpx.TimeoutException):
                        _ex["codeStatus"] = "TIMEOUT"
                    log_cef_event(
                        event_id,
                        request=_req,
                        current_user=_user,
                        status_code=None,
                        extra=_ex,
                    )
            msg = f"SVC-RAG {method} {url} error: {e}"
            raise RuntimeError(msg) from e

    @staticmethod
    def _parse_hits(resp: Any) -> List[Tuple[str, float, Optional[int], Optional[int]]]:
        """Хиты из ответа SVC-RAG.

        `cosine` — сырая косинусная близость чанка, необязательное поле:
        старый SVC-RAG его не присылает, лексическая стратегия не считает.
        Едет отдельно от `score`, потому что тот в шкале стратегии
        (RRF / логит реранкера), и процент релевантности по нему считать нельзя.
        """
        from backend.rag_query.hit_types import RagHit

        hits = resp.get("hits", []) if isinstance(resp, dict) else []
        out: List[Tuple[str, float, Optional[int], Optional[int]]] = []
        for h in hits:
            raw_cosine = h.get("cosine")
            try:
                cosine = None if raw_cosine is None else float(raw_cosine)
            except (TypeError, ValueError):
                cosine = None
            out.append(
                RagHit(
                    h.get("content", ""),
                    float(h.get("score", 0.0)),
                    h.get("document_id"),
                    h.get("chunk_index"),
                    cosine,
                )
            )
        return out

    async def _merge_variant_searches(
        self, path: str, base_body: Dict[str, Any], variants: List[str], k: int
    ) -> List[Tuple[str, float, Optional[int], Optional[int]]]:
        merged: Dict[
            Tuple[Optional[int], Optional[int]],
            Tuple[str, float, Optional[int], Optional[int]],
        ] = {}
        order_q: List[str] = []
        for q in [base_body["query"]] + list(variants):
            t = (q or "").strip()
            if t and t not in order_q:
                order_q.append(t)
        vq = base_body.get("vector_query")
        for idx, qtext in enumerate(order_q):
            body = {**base_body, "query": qtext}
            if idx > 0 or not vq:
                body.pop("vector_query", None)
            resp = await self._request(
                "POST", path, json=body, http_timeout=_svc_rag_search_timeout()
            )
            for tup in self._parse_hits(resp):
                key = (tup[2], tup[3])
                prev = merged.get(key)
                if prev is None or float(tup[1]) > float(prev[1]):
                    merged[key] = tup
        out = sorted(merged.values(), key=lambda x: float(x[1]), reverse=True)
        return out[:k]

    async def _search_with_pipeline(
        self,
        path: str,
        query: str,
        k: int,
        *,
        log_tag: str,
        document_id: Optional[int] = None,
        document_ids: Optional[List[int]] = None,
        use_reranking: Optional[bool] = None,
        strategy: Optional[str] = None,
        project_id: Optional[str] = None,
        settings_source: str = "user",
        settings_override: Optional[Dict[str, Any]] = None,
        _retry_done: bool = False,
    ) -> List[Tuple[str, float, Optional[int], Optional[int]]]:
        from backend.rag_query.pipeline import process_user_query
        from backend.rag_query.postprocess import dedupe_rag_hits
        from backend.rag_query.semantic_cache import (
            cache_get,
            cache_set,
            make_cache_key,
            semantic_cache_enabled,
        )
        from backend.services.memory_rag_env import get_memory_rag_retrieval_settings
        from backend.services.user_rag_settings import (
            embedding_fields_from_rag_settings,
            get_runtime_rag_settings,
            reranker_fields_from_rag_settings,
            runtime_embedding_fields,
            runtime_reranker_fields,
        )

        # Библиотека вне пер-юзерности: её модели задаются в ENV
        # (RAG_MEMORY_EMBEDDING_MODEL / RAG_MEMORY_RERANKER_MODEL). Если они
        # не заданы — пустые поля, и svc-rag берёт кластерную модель, как раньше.
        #
        # settings_override — явный снимок настроек сущности (per-KB search):
        # тогда embedding/rerank/strategy/top_k берутся из него, а не из
        # ContextVar runtime (у разных БЗ разные модели).
        override = settings_override if isinstance(settings_override, dict) else None
        src = (settings_source or "user").strip().lower()
        if override is not None and src not in ("memory",):
            src = "kb"
        is_memory = src == "memory"
        # Проекты, агенты и БЗ настраиваются раздельно: модель, top_k, порог и
        # реранк берём из скоупа того стора, по которому идёт поиск. "user" —
        # как раньше, то есть скоуп по умолчанию.
        scope = src if src in ("project", "agent", "kb") else None
        if is_memory:
            from backend.services.memory_rag_env import (
                get_memory_embedding_fields,
                get_memory_reranker_fields,
            )

            emb_fields = get_memory_embedding_fields()
            rr_fields = get_memory_reranker_fields()
        elif override is not None:
            emb_fields = embedding_fields_from_rag_settings(override)
            rr_fields = reranker_fields_from_rag_settings(override)
        else:
            emb_fields = runtime_embedding_fields(scope)
            rr_fields = runtime_reranker_fields(scope)

        # Настройка стора - источник истины
        _st_in = str(strategy or "").strip().lower()
        if is_memory or scope in ("project", "agent", "kb") or _st_in in ("", "auto"):
            from backend.services.user_rag_settings import (
                runtime_memory_strategy,
                runtime_rag_strategy,
            )

            if override is not None and not is_memory:
                strategy = str(override.get("rag_strategy") or "auto")
            else:
                strategy = (
                    runtime_memory_strategy()
                    if is_memory
                    else runtime_rag_strategy(scope)
                )
            if str(strategy or "").strip().lower() != _st_in:
                logger.debug(
                    "[%s] стратегия из настроек %s: %r вместо %r",
                    log_tag or path,
                    scope or ("memory" if is_memory else "по умолчанию"),
                    strategy,
                    _st_in or None,
                )

        st = (strategy or "").strip().lower()
        raw_mode = st == "raw_cosine"
        timer = StageTimer(
            "BACKEND-SEARCH",
            store=log_tag or path,
            strategy=st or "default",
            k=k,
        )
        if raw_mode:
            body: Dict[str, Any] = {"query": query, "k": k, "strategy": "raw_cosine"}
            if document_id is not None:
                body["document_id"] = document_id
            if document_ids:
                body["document_ids"] = [int(x) for x in document_ids]
            body.update(emb_fields)
            with timer.stage("svc_rag_http"):
                resp = await self._request(
                    "POST", path, json=body, http_timeout=_svc_rag_search_timeout()
                )
            hits = self._parse_hits(resp)
            _log_backend_rag_strategy_banner(
                path=path,
                strategy=body.get("strategy"),
                k=k,
                document_id=document_id,
                use_reranking=False,
                hits=len(hits),
                query_preview=_rag_query_preview(query),
                prep_suffix="препроцесс backend: OFF (raw_cosine)",
                from_cache=False,
            )
            timer.meta["hits"] = len(hits)
            timer.log(logger)
            return hits
        if is_memory:
            user_rag = get_memory_rag_retrieval_settings()
        elif override is not None:
            user_rag = override
        else:
            user_rag = get_runtime_rag_settings(scope)
        _fix = bool(user_rag.get("rag_query_fix_typos", False))
        _multi = bool(user_rag.get("rag_multi_query_enabled", False))
        _hyde = bool(user_rag.get("rag_hyde_enabled", False))
        with timer.stage("preprocess"):
            pq = await process_user_query(
                query, fix_typos=_fix, multi_query=_multi, hyde=_hyde
            )
        body: Dict[str, Any] = {"query": pq.query_for_search, "k": k}
        body.update(emb_fields)
        body.update(rr_fields)
        if document_id is not None:
            body["document_id"] = document_id
        if document_ids:
            body["document_ids"] = [int(x) for x in document_ids]
        _rr_enabled = bool(user_rag.get("rag_reranking_enabled", False))
        effective_reranking = (
            bool(use_reranking) if use_reranking is not None else _rr_enabled
        )
        if strategy and str(strategy).strip().lower() == "lexical":
            effective_reranking = False
        try:
            rerank_top_n = int(user_rag.get("rag_rerank_top_n") or 0)
        except (TypeError, ValueError):
            rerank_top_n = 0
        rerank_top_n = max(0, min(rerank_top_n, 64))
        body["use_reranking"] = effective_reranking
        logger.debug(
            "[RAG-SEARCH] mode=%s источник=%s emb=%s/%s rr=%s/%s strategy=%s k=%s "
            "reranking=%s rerank_top_n=%s fix_typos=%s multi_query=%s hyde=%s "
            "document_id=%s project_id=%s",
            path,
            src,
            emb_fields.get("embedding_provider") or "cluster",
            emb_fields.get("embedding_model") or "default",
            rr_fields.get("reranker_provider") or "cluster",
            rr_fields.get("reranker_model") or "default",
            strategy,
            k,
            effective_reranking,
            rerank_top_n,
            _fix,
            _multi,
            _hyde,
            document_id,
            project_id,
        )
        if strategy is not None:
            body["strategy"] = strategy
        if pq.vector_query:
            body["vector_query"] = pq.vector_query
        if pq.filters:
            body["filters"] = pq.filters
        cache_key = make_cache_key(
            path,
            pq.normalized,
            k,
            f"{strategy}|topn={rerank_top_n if effective_reranking else 0}"
            f"|docs={','.join(str(int(x)) for x in sorted(document_ids or []))}"
            f"|emb={emb_fields.get('embedding_provider') or '-'}"
            f"/{emb_fields.get('embedding_model') or '-'}"
            f"|rr={rr_fields.get('reranker_provider') or '-'}"
            f"/{rr_fields.get('reranker_model') or '-'}",
            document_id,
            effective_reranking,
            pq.filters,
            project_id,
            rag_fix_typos=_fix,
            rag_multi_query=_multi,
            rag_hyde=_hyde,
        )
        if semantic_cache_enabled():
            cached = cache_get(cache_key)
            if cached is not None:
                hits_cached = dedupe_rag_hits(
                    cached, jaccard_threshold=_dedupe_jaccard_threshold()
                )
                _log_backend_rag_strategy_banner(
                    path=path,
                    strategy=body.get("strategy"),
                    k=k,
                    document_id=document_id,
                    use_reranking=use_reranking,
                    hits=len(hits_cached),
                    query_preview=_rag_query_preview(pq.query_for_search),
                    prep_suffix="",
                    from_cache=True,
                )
                timer.mark("cache_hit", 0.0)
                timer.meta["hits"] = len(hits_cached)
                timer.meta["cache"] = "hit"
                timer.log(logger)
                return hits_cached
        with timer.stage("svc_rag_http"):
            if pq.multi_variants:
                hits = await self._merge_variant_searches(
                    path, body, pq.multi_variants, k
                )
            else:
                resp = await self._request(
                    "POST", path, json=body, http_timeout=_svc_rag_search_timeout()
                )
                hits = self._parse_hits(resp)
        hits = dedupe_rag_hits(hits, jaccard_threshold=_dedupe_jaccard_threshold())
        if effective_reranking:
            if rerank_top_n > 0:
                hits = hits[: max(1, min(rerank_top_n, k))]
        with timer.stage("llm_judge"):
            hits = await judge_and_filter_hits(pq.query_for_search, hits)
        # Поиск отработал и показывать нечего - пробуем переписать вопрос по
        # истории и поискать ещё раз. Вызов модели тратится ТОЛЬКО здесь, на
        # реальном промахе, а не на каждом коротком вопросе.
        #
        # Порог берём тот же, по которому отбирает filter_rag_hits_by_score в
        # handlers: не осталось ни одного хита - пользователю всё равно
        # нечего показать. Порог не задан - критерий вырождается в «пусто».
        if not _retry_done:
            # Порог для переспроса - своей переменной и в шкале КОСИНУСА.
            # rag_similarity_threshold сюда не годится по двум причинам: его
            # же применяет filter_rag_hits_by_score в handlers (выставив его
            # тут, порезали бы выдачу пользователю), и применяет он его к
            # score - а тот в шкале стратегии (RRF ~0.016, логит реранкера),
            # с косинусным порогом несопоставим. См. докстринг RagHit.
            try:
                _thr = float(
                    os.getenv("RAG_REWRITE_MIN_COSINE", _REWRITE_MIN_COSINE_DEFAULT)
                    or 0.0
                )
            except (TypeError, ValueError):
                _thr = 0.0
            _cosines = [
                c
                for c in (getattr(h, "cosine", None) for h in hits)
                if isinstance(c, (int, float))
            ]
            # Пусто - промах всегда. Иначе смотрим лучший косинус, и только
            # если порог задан: ноль значит «фильтр выключен», а не «нулевой
            # косинус» - такой фильтр не отсёк бы ничего.
            _best_cos = max(_cosines) if _cosines else None
            _weak = not hits or (
                _thr > 0 and _best_cos is not None and _best_cos < _thr
            )
            if _weak:
                # Косинус в логе - чтобы подобрать порог по живым данным.
                logger.debug(
                    "[%s] выдача слабая: хитов=%s лучший cosine=%s порог=%.2f",
                    log_tag,
                    len(hits),
                    "нет" if _best_cos is None else "%.3f" % _best_cos,
                    _thr,
                )
                from backend.rag_query.pipeline import rewrite_query_with_context

                with timer.stage("query_rewrite"):
                    _rewritten = await rewrite_query_with_context(query)
                if _rewritten:
                    _retry_hits = await self._search_with_pipeline(
                        path,
                        _rewritten,
                        k,
                        log_tag=log_tag,
                        document_id=document_id,
                        document_ids=document_ids,
                        use_reranking=use_reranking,
                        strategy=strategy,
                        project_id=project_id,
                        settings_source=settings_source,
                        settings_override=override,
                        _retry_done=True,
                    )
                    if _retry_hits:
                        from backend.realtime.rag_evidence import merge_extra_hits

                        _before = len(hits)
                        hits = merge_extra_hits(hits, _retry_hits)
                        logger.debug(
                            "[%s] переспрос по переписанному вопросу: +%s фрагментов",
                            log_tag,
                            len(hits) - _before,
                        )
        if semantic_cache_enabled():
            cache_set(cache_key, hits)
        prep_bits: List[str] = []
        if pq.multi_variants:
            prep_bits.append("multi-query")
        if pq.vector_query:
            prep_bits.append("HyDE(vector_query)")
        prep_s = f"препроцесс backend: {', '.join(prep_bits)}" if prep_bits else ""
        _log_backend_rag_strategy_banner(
            path=path,
            strategy=body.get("strategy"),
            k=k,
            document_id=document_id,
            use_reranking=use_reranking,
            hits=len(hits),
            query_preview=_rag_query_preview(pq.query_for_search),
            prep_suffix=prep_s,
            from_cache=False,
        )
        timer.meta["hits"] = len(hits)
        timer.log(logger)
        return hits

    async def health(self) -> Dict[str, Any]:
        return await self._request("GET", "/health")

    async def ensure_embedding_dim(self, embedding_dim: int) -> Dict[str, Any]:
        """Привести колонки vector(*) в Postgres к размерности текущей модели."""
        return await self._request(
            "POST",
            "/schema/embedding-dim",
            json={"embedding_dim": int(embedding_dim)},
        )

    async def set_models_provider(
        self,
        model_type: str,
        provider: str,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Переключить источник моделей RAG в svc-rag."""
        return await self._request(
            "POST",
            "/schema/models-provider",
            json={"model_type": model_type, "provider": provider, "model": model},
        )

    async def upload_document(
        self,
        file_bytes: bytes,
        filename: str,
        minio_object: Optional[str] = None,
        minio_bucket: Optional[str] = None,
        original_path: Optional[str] = None,
        chunk_size: Optional[int] = None,
        chunk_overlap: Optional[int] = None,
    ) -> Dict[str, Any]:
        files = {"file": (filename, file_bytes, "application/octet-stream")}
        data: Dict[str, Any] = {}
        if minio_object:
            data["minio_object"] = minio_object
        if minio_bucket:
            data["minio_bucket"] = minio_bucket
        if original_path:
            data["original_path"] = original_path
        data = _with_chunk_index_form_data(
            data, chunk_size=chunk_size, chunk_overlap=chunk_overlap
        )
        return await self._request(
            "POST",
            "/documents",
            files=files,
            data=data,
            http_timeout=_svc_rag_document_index_timeout(),
        )

    async def list_documents(self) -> List[Dict[str, Any]]:
        resp = await self._request("GET", "/documents")
        return resp

    async def delete_document_by_id(self, document_id: int) -> Dict[str, Any]:
        return await self._request("DELETE", f"/documents/{document_id}")

    async def delete_document_by_filename(self, filename: str) -> Dict[str, Any]:
        return await self._request("DELETE", f"/documents/by-filename/{filename}")

    async def get_document_start_chunks(
        self, document_id: int, max_chunks: int = 2
    ) -> List[Tuple[str, float, Optional[int], Optional[int]]]:
        """Legacy: /v1/documents/... в текущем SVC-RAG не смонтирован. Для project — project_rag_get_document_start_chunks."""
        try:
            resp = await self._request(
                "GET",
                f"/documents/{document_id}/chunks",
                params={"start": 0, "limit": max_chunks},
            )
        except Exception:
            logger.warning(
                "get_document_start_chunks: /documents/%s/chunks недоступен (legacy). "
                "Для project RAG используйте project_rag_get_document_start_chunks.",
                document_id,
            )
            return []
        chunks = resp.get("chunks", []) if isinstance(resp, dict) else []
        return [
            (c.get("content", ""), 1.0, c.get("document_id"), c.get("chunk_index"))
            for c in chunks
        ]

    async def project_rag_get_document_start_chunks(
        self, project_id: str, document_id: int, max_chunks: int = 2
    ) -> List[Tuple[str, float, Optional[int], Optional[int]]]:
        """Начало документа из project_rag (для запросов про оглавление/структуру)."""
        try:
            resp = await self._request(
                "GET",
                f"/project-rag/projects/{project_id}/documents/{document_id}/chunks",
                params={"start": 0, "limit": max_chunks},
            )
        except Exception:
            logger.warning(
                "project_rag_get_document_start_chunks: project=%s doc=%s — чанки недоступны",
                project_id,
                document_id,
            )
            return []
        chunks = resp.get("chunks", []) if isinstance(resp, dict) else []
        return [
            (c.get("content", ""), 1.0, c.get("document_id"), c.get("chunk_index"))
            for c in chunks
        ]

    async def search(
        self,
        query: str,
        k: int = 10,
        strategy: Optional[str] = None,
        document_id: Optional[int] = None,
        use_reranking: Optional[bool] = None,
    ) -> List[Tuple[str, float, Optional[int], Optional[int]]]:
        return await self._search_with_pipeline(
            "/search",
            query,
            k,
            log_tag="/search",
            document_id=document_id,
            use_reranking=use_reranking,
            strategy=strategy,
            project_id=None,
        )

    async def get_confidence_report(self) -> Dict[str, Any]:
        return await self._request("GET", "/documents/report/confidence")

    async def get_image_minio_info(self, filename: str) -> Optional[Dict[str, Any]]:
        resp = await self._request("GET", f"/documents/minio-info/{filename}")
        if resp is None:
            return None
        return resp

    async def kb_upload_document(
        self,
        file_bytes: bytes,
        filename: str,
        chunk_size: Optional[int] = None,
        chunk_overlap: Optional[int] = None,
        chunking_strategy: Optional[str] = None,
        minio_object: Optional[str] = None,
        minio_bucket: Optional[str] = None,
        owner_user_id: Optional[str] = None,
        agent_id: Optional[int] = None,
        knowledge_base_id: Optional[int] = None,
        uploaded_by: Optional[str] = None,
        relative_path: Optional[str] = None,
        embedding_model: Optional[str] = None,
        embedding_provider: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Загрузить документ в постоянную Базу Знаний."""
        files = {"file": (filename, file_bytes, "application/octet-stream")}
        data: Dict[str, Any] = {}
        if minio_object:
            data["minio_object"] = minio_object
        if minio_bucket:
            data["minio_bucket"] = minio_bucket
        if owner_user_id:
            data["owner_user_id"] = str(owner_user_id).strip().lower()
        if uploaded_by:
            data["uploaded_by"] = str(uploaded_by).strip().lower()
        if agent_id is not None:
            data["agent_id"] = str(int(agent_id))
        if knowledge_base_id is not None:
            data["knowledge_base_id"] = str(int(knowledge_base_id))
        if relative_path:
            data["relative_path"] = str(relative_path).replace("\\", "/").strip()
        # Документ индексируется моделью владельца, а не того, кто заливает.
        if embedding_model:
            # Регистр НЕ понижаем: шлюз чувствителен к нему (embed/FRIDA != embed/frida)
            data["embedding_model"] = str(embedding_model).strip()
        if embedding_provider:
            data["embedding_provider"] = str(embedding_provider).strip()
        data = _with_chunk_index_form_data(
            data,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            chunking_strategy=chunking_strategy,
        )
        return await self._request(
            "POST",
            "/kb/documents",
            files=files,
            data=data,
            http_timeout=_svc_rag_document_index_timeout(),
        )

    async def kb_check_duplicate(
        self,
        *,
        content_hash: str,
        filename: Optional[str] = None,
        size: Optional[int] = None,
        agent_id: Optional[int] = None,
        knowledge_base_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {"content_hash": str(content_hash).strip().lower()}
        if filename:
            body["filename"] = filename
        if size is not None:
            body["size"] = int(size)
        if agent_id is not None:
            body["agent_id"] = int(agent_id)
        if knowledge_base_id is not None:
            body["knowledge_base_id"] = int(knowledge_base_id)
        resp = await self._request("POST", "/kb/documents/duplicate-check", json=body)
        return resp if isinstance(resp, dict) else {"duplicate": False}

    async def kb_list_documents(self) -> List[Dict[str, Any]]:
        """Список документов в Базе Знаний."""
        resp = await self._request("GET", "/kb/documents")
        return resp if isinstance(resp, list) else []

    async def kb_delete_document(self, document_id: int) -> Dict[str, Any]:
        """Удалить документ из Базы Знаний."""
        return await self._request("DELETE", f"/kb/documents/{document_id}")

    async def kb_get_document(self, document_id: int) -> Optional[Dict[str, Any]]:
        """Метаданные одного KB-документа."""
        resp = await self._request("GET", f"/kb/documents/{document_id}")
        return resp if isinstance(resp, dict) else None

    async def kb_clone_document(
        self,
        document_id: int,
        *,
        agent_id: Optional[int] = None,
        knowledge_base_id: Optional[int] = None,
        owner_user_id: Optional[str] = None,
        uploaded_by: Optional[str] = None,
        minio_object: Optional[str] = None,
        minio_bucket: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Клон KB-документа с векторами (агент или база знаний)."""
        body: Dict[str, Any] = {}
        if agent_id is not None:
            body["agent_id"] = int(agent_id)
        if knowledge_base_id is not None:
            body["knowledge_base_id"] = int(knowledge_base_id)
        if owner_user_id:
            body["owner_user_id"] = str(owner_user_id).strip().lower()
        if uploaded_by:
            body["uploaded_by"] = str(uploaded_by).strip().lower()
        if minio_object is not None:
            body["minio_object"] = minio_object
        if minio_bucket is not None:
            body["minio_bucket"] = minio_bucket
        resp = await self._request(
            "POST", f"/kb/documents/{document_id}/clone", json=body
        )
        return resp if isinstance(resp, dict) else {"ok": False}

    async def kb_search(
        self,
        query: str,
        k: int = 8,
        document_id: Optional[int] = None,
        use_reranking: Optional[bool] = None,
        strategy: Optional[str] = None,
        document_ids: Optional[List[int]] = None,
        settings_override: Optional[Dict[str, Any]] = None,
        settings_source: Optional[str] = None,
    ) -> List[Tuple[str, float, Optional[int], Optional[int]]]:
        """Поиск по Базе Знаний.

        Возвращает список (content, score, document_id, chunk_index).

        ``settings_override`` — снимок настроек конкретной БЗ (per-KB search).
        Без него: runtime ``kb`` если привязан, иначе legacy ``agent``.
        """
        from backend.services.user_rag_settings import runtime_kb_search_scope

        src = (settings_source or "").strip().lower() or runtime_kb_search_scope()
        if isinstance(settings_override, dict) and settings_override:
            src = "kb"
        return await self._search_with_pipeline(
            "/kb/search",
            query,
            k,
            log_tag="/kb/search",
            document_id=document_id,
            document_ids=document_ids,
            use_reranking=use_reranking,
            strategy=strategy,
            project_id=None,
            settings_source=src,
            settings_override=settings_override,
        )

    async def kb_reindex(
        self,
        chunk_size: Optional[int] = None,
        chunk_overlap: Optional[int] = None,
        chunking_strategy: Optional[str] = None,
        owner_user_id: Optional[str] = None,
        document_ids: Optional[list] = None,
        embedding_model: Optional[str] = None,
        embedding_provider: Optional[str] = None,
        agent_id: Optional[int] = None,
        knowledge_base_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Перечанкировать Базу Знаний (опционально только документы владельца).

        ```agent_id``` / ```knowledge_base_id``` — ключ очереди в svc-rag: у двух
        сущностей одного владельца пересборки идут независимо.
        """
        body: Dict[str, Any] = {}
        if chunk_size is not None:
            body["chunk_size"] = int(chunk_size)
        if chunk_overlap is not None:
            body["chunk_overlap"] = int(chunk_overlap)
        if chunking_strategy is not None:
            body["chunking_strategy"] = str(chunking_strategy)
        # Модель приходит от вызывающего — из настроек агента. Библиотека тут ни
        # при чём: её RAG_MEMORY_EMBEDDING_MODEL уводила вектора агентов в чужую
        # таблицу размерности, пока поиск по агенту смотрел в свою.
        # Не передана — svc-rag берёт кластерную, как у проектов.
        if owner_user_id:
            body["owner_user_id"] = str(owner_user_id).strip().lower()
        if agent_id is not None:
            body["agent_id"] = int(agent_id)
        if knowledge_base_id is not None:
            body["knowledge_base_id"] = int(knowledge_base_id)
        if document_ids:
            body["document_ids"] = [int(x) for x in document_ids if x is not None]
        if embedding_model:
            # Регистр НЕ понижаем: шлюз чувствителен к нему (embed/FRIDA != embed/frida)
            body["embedding_model"] = str(embedding_model).strip()
        if embedding_provider:
            body["embedding_provider"] = str(embedding_provider).strip()
        return await self._request(
            "POST",
            "/kb/reindex",
            json=body,
            http_timeout=_svc_rag_document_index_timeout(),
        )

    async def memory_rag_reindex(
        self,
        chunk_size: Optional[int] = None,
        chunk_overlap: Optional[int] = None,
        chunking_strategy: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Переиндексировать всю Библиотеку (вектора из сохранённого текста)."""
        body: Dict[str, Any] = {}
        if chunk_size is not None:
            body["chunk_size"] = int(chunk_size)
        if chunk_overlap is not None:
            body["chunk_overlap"] = int(chunk_overlap)
        if chunking_strategy is not None:
            body["chunking_strategy"] = str(chunking_strategy)
        return await self._request(
            "POST",
            "/memory-rag/reindex",
            json=body,
            http_timeout=_svc_rag_document_index_timeout(),
        )

    async def memory_rag_index_document(
        self,
        file_bytes: bytes,
        filename: str,
        minio_object: Optional[str] = None,
        minio_bucket: Optional[str] = None,
        chunk_size: Optional[int] = None,
        chunk_overlap: Optional[int] = None,
        chunking_strategy: Optional[str] = None,
    ) -> Dict[str, Any]:
        files = {"file": (filename, file_bytes, "application/octet-stream")}
        data: Dict[str, Any] = {}
        if minio_object:
            data["minio_object"] = minio_object
        if minio_bucket:
            data["minio_bucket"] = minio_bucket
        data = _with_chunk_index_form_data(
            data,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            chunking_strategy=chunking_strategy,
        )
        from backend.services.memory_rag_env import get_memory_embedding_fields

        data.update(get_memory_embedding_fields())
        return await self._request(
            "POST",
            "/memory-rag/documents",
            files=files,
            data=data,
            http_timeout=_svc_rag_document_index_timeout(),
        )

    async def memory_rag_check_duplicate(
        self,
        *,
        content_hash: str,
        filename: Optional[str] = None,
        size: Optional[int] = None,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {"content_hash": str(content_hash).strip().lower()}
        if filename:
            body["filename"] = filename
        if size is not None:
            body["size"] = int(size)
        resp = await self._request(
            "POST", "/memory-rag/documents/duplicate-check", json=body
        )
        return resp if isinstance(resp, dict) else {"duplicate": False}

    async def memory_rag_list_documents(self) -> List[Dict[str, Any]]:
        resp = await self._request("GET", "/memory-rag/documents")
        return resp if isinstance(resp, list) else []

    async def memory_rag_delete_document(self, document_id: int) -> Dict[str, Any]:
        return await self._request("DELETE", f"/memory-rag/documents/{document_id}")

    async def memory_rag_search(
        self,
        query: str,
        k: Optional[int] = None,
        document_id: Optional[int] = None,
        use_reranking: Optional[bool] = None,
        strategy: Optional[str] = None,
    ) -> List[Tuple[str, float, Optional[int], Optional[int]]]:
        """Поиск по Memory/Библиотеке.

        Retrieval (top_k, rerank, hyde, …) — из env RAG_MEMORY_*.
        strategy — из UI/чата (единственная Memory-настройка из UI).
        """
        from backend.services.memory_rag_env import (
            filter_hits_by_memory_similarity,
            get_memory_rag_chat_top_k,
        )

        eff_k = int(k) if k is not None else get_memory_rag_chat_top_k()
        hits = await self._search_with_pipeline(
            "/memory-rag/search",
            query,
            eff_k,
            log_tag="/memory-rag/search",
            document_id=document_id,
            use_reranking=use_reranking,
            strategy=strategy,
            project_id=None,
            settings_source="memory",
        )
        return filter_hits_by_memory_similarity(hits)

    async def project_rag_upload_document(
        self,
        file_bytes: bytes,
        filename: str,
        project_id: str,
        minio_object: Optional[str] = None,
        minio_bucket: Optional[str] = None,
        chunk_size: Optional[int] = None,
        chunk_overlap: Optional[int] = None,
        chunking_strategy: Optional[str] = None,
        owner_user_id: Optional[str] = None,
        uploaded_by: Optional[str] = None,
        embedding_model: Optional[str] = None,
        embedding_provider: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Загрузить документ в RAG-хранилище проекта."""
        files = {"file": (filename, file_bytes, "application/octet-stream")}
        data: Dict[str, Any] = {}
        if minio_object:
            data["minio_object"] = minio_object
        if minio_bucket:
            data["minio_bucket"] = minio_bucket
        if owner_user_id:
            data["owner_user_id"] = str(owner_user_id).strip().lower()
        if uploaded_by:
            data["uploaded_by"] = str(uploaded_by).strip().lower()
        if embedding_model:
            # Регистр НЕ понижаем: шлюз чувствителен к нему (embed/FRIDA != embed/frida)
            data["embedding_model"] = str(embedding_model).strip()
        if embedding_provider:
            data["embedding_provider"] = str(embedding_provider).strip()
        data = _with_chunk_index_form_data(
            data,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            chunking_strategy=chunking_strategy,
        )
        return await self._request(
            "POST",
            f"/project-rag/projects/{project_id}/documents",
            files=files,
            data=data,
            http_timeout=_svc_rag_document_index_timeout(),
        )

    async def project_rag_check_duplicate(
        self,
        *,
        project_id: str,
        content_hash: str,
        filename: Optional[str] = None,
        size: Optional[int] = None,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {"content_hash": str(content_hash).strip().lower()}
        if filename:
            body["filename"] = filename
        if size is not None:
            body["size"] = int(size)
        resp = await self._request(
            "POST",
            f"/project-rag/projects/{project_id}/documents/duplicate-check",
            json=body,
        )
        return resp if isinstance(resp, dict) else {"duplicate": False}

    async def project_rag_list_documents(self, project_id: str) -> List[Dict[str, Any]]:
        """Список документов проекта."""
        resp = await self._request(
            "GET", f"/project-rag/projects/{project_id}/documents"
        )
        return resp if isinstance(resp, list) else []

    async def project_rag_delete_document(
        self, project_id: str, document_id: int
    ) -> Dict[str, Any]:
        """Удалить один документ из RAG проекта."""
        return await self._request(
            "DELETE", f"/project-rag/projects/{project_id}/documents/{document_id}"
        )

    async def project_rag_delete_project(self, project_id: str) -> Dict[str, Any]:
        """Удалить все RAG-данные проекта (при удалении проекта)."""
        return await self._request("DELETE", f"/project-rag/projects/{project_id}")

    async def project_rag_search(
        self,
        query: str,
        project_id: str,
        k: int = 8,
        document_id: Optional[int] = None,
        use_reranking: Optional[bool] = None,
        strategy: Optional[str] = None,
    ) -> List[Tuple[str, float, Optional[int], Optional[int]]]:
        """Поиск по RAG-документам проекта."""
        path = f"/project-rag/projects/{project_id}/search"
        return await self._search_with_pipeline(
            path,
            query,
            k,
            log_tag=f"project-rag/{project_id}/search",
            document_id=document_id,
            use_reranking=use_reranking,
            strategy=strategy,
            project_id=project_id,
            settings_source="project",
        )

    async def project_rag_reindex_all(
        self,
        chunk_size: Optional[int] = None,
        chunk_overlap: Optional[int] = None,
        chunking_strategy: Optional[str] = None,
        owner_user_id: Optional[str] = None,
        project_id: Optional[str] = None,
        embedding_model: Optional[str] = None,
        embedding_provider: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Перечанкировать проекты (один project_id или все проекты пользователя)."""
        body: Dict[str, Any] = {}
        if chunk_size is not None:
            body["chunk_size"] = int(chunk_size)
        if chunk_overlap is not None:
            body["chunk_overlap"] = int(chunk_overlap)
        if chunking_strategy is not None:
            body["chunking_strategy"] = str(chunking_strategy)
        if owner_user_id:
            body["owner_user_id"] = str(owner_user_id).strip().lower()
        if project_id:
            body["project_id"] = str(project_id).strip()
        if embedding_model:
            # Регистр НЕ понижаем: шлюз чувствителен к нему (embed/FRIDA != embed/frida)
            body["embedding_model"] = str(embedding_model).strip()
        if embedding_provider:
            body["embedding_provider"] = str(embedding_provider).strip()
        return await self._request(
            "POST",
            "/project-rag/reindex",
            json=body,
            http_timeout=_svc_rag_document_index_timeout(),
        )

    async def get_reindex_status(self) -> Dict[str, Any]:
        """Статус фоновой перечанковки по трём RAG-сторам (lock в SVC-RAG)."""
        import asyncio

        kb_resp, memory_resp, project_resp = await asyncio.gather(
            self._request("GET", "/kb/reindex/status"),
            self._request("GET", "/memory-rag/reindex/status"),
            self._request("GET", "/project-rag/reindex/status"),
            return_exceptions=True,
        )

        def _reindexing(resp: Any) -> bool:
            if isinstance(resp, Exception):
                return False
            if isinstance(resp, dict):
                return bool(resp.get("reindexing"))
            return False

        memory_flag = _reindexing(memory_resp)
        project_flag = _reindexing(project_resp)
        kb_flag = _reindexing(kb_resp)
        return {
            "memory": {"reindexing": memory_flag},
            "project": {"reindexing": project_flag},
            "kb": {"reindexing": kb_flag},
            "any_reindexing": memory_flag or project_flag or kb_flag,
        }


_rag_client_singleton: Optional[RagClient] = None


def get_rag_client() -> RagClient:
    global _rag_client_singleton
    if _rag_client_singleton is None:
        _rag_client_singleton = RagClient()
    return _rag_client_singleton


def rag_model_path_to_provider(model_path: str):
    """path -> (provider_id, model_id).

    Разбор пути один на весь бэкенд — 'embedding_fields_from_path': префикс
    это id провайдера в 'rag_models.providers' у SVC-RAG, сравнение там
    регистронезависимое. Раньше здесь лежал свой список из трёх известных
    префиксов, и путь нового шлюза ('corsur_rag/embed/FRIDA') сверялся
    как 'native': reconcile после рестарта svc-rag видел расхождение и
    молча пушил кластерную модель поверх выбора пользователя.
    """
    from backend.services.user_rag_settings import embedding_fields_from_path

    fields = embedding_fields_from_path(model_path)
    provider = str(fields.get("embedding_provider") or "native")
    model = fields.get("embedding_model")
    if provider == "native":
        return "native", None
    return provider, model


async def reconcile_rag_models_provider(
    client,
    health,
    embedding_path: str,
    reranker_path: str,
) -> None:
    """Самолечение после рестарта svc-rag.

    Сверяет активный провайдер в svc-rag (поля /v1/health) с выбором
    пользователя (settings.json) и пушит расхождение. Так persist живёт
    только в backend - без таблиц в Postgres и файлов в svc-rag.
    """
    if not isinstance(health, dict) or "embedding_provider" not in health:
        return  # svc-rag ещё без переключалки - сверять нечего
    expected = {
        "embedding": rag_model_path_to_provider(embedding_path),
        "reranker": rag_model_path_to_provider(reranker_path),
    }
    actual = {
        "embedding": (
            str(health.get("embedding_provider") or "native"),
            health.get("embedding_model") or None,
        ),
        "reranker": (
            str(health.get("reranker_provider") or "native"),
            health.get("reranker_model") or None,
        ),
    }
    for model_type in ("embedding", "reranker"):
        if expected[model_type] == actual[model_type]:
            continue
        provider, model = expected[model_type]
        logger.warning(
            "[RAG-RECONCILE] svc-rag %s: активен %s, ожидается %s - пушим выбор",
            model_type,
            actual[model_type],
            expected[model_type],
        )
        result = await client.set_models_provider(model_type, provider, model)
        if model_type == "embedding":
            emb_dim = result.get("embedding_dim") if isinstance(result, dict) else None
            if emb_dim:
                # Идемпотентно: при совпадении dim данные не трогаются.
                # Заодно чинит стартовый рассинхрон settings.postgresql.embedding_dim
                # в svc-rag (конфиг мог отстать от фактической колонки БД).
                schema = await client.ensure_embedding_dim(int(emb_dim))
                # Если миграция всё-таки прошла — вектора очищены. Без реиндекса
                # корпус остался бы пустым после рестарта svc-rag (тихая потеря).
                if isinstance(schema, dict) and schema.get("migrated"):
                    logger.warning(
                        "[RAG-RECONCILE] миграция dim=%s очистила вектора — запускаю восстановление",
                        emb_dim,
                    )
                    with logged_suppress(logger):
                        import asyncio

                        from backend.routes.rag import (
                            _reindex_on_model_change,
                            _run_background_reindex_after_model_change,
                        )

                        if _reindex_on_model_change():
                            asyncio.create_task(
                                _run_background_reindex_after_model_change()
                            )
