# Клиент эмбеддингов/реранка к OpenAI-совместимому шлюзу (LiteLLM / PHOENIX).
# Контракт: POST /v1/embeddings, POST /v1/rerank, GET /v1/models.
import asyncio
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.core.config import get_settings
from app.core.http_verify import resolve_httpx_verify

logger = logging.getLogger(__name__)

# Короткий retry на «мигнувший» шлюз: 429/502/503 и обрыв соединения. 504 и
# ReadTimeout НЕ повторяем — это сигнал дробить батч (EmbedBatchTimeout).
_RETRY_STATUS_CODES = frozenset({429, 502, 503})
_RETRY_EXCEPTIONS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.RemoteProtocolError,
)
_MAX_ATTEMPTS = 3
_RETRY_BASE_DELAY_SECONDS = 0.5


class RagModelsClient:
    """Эмбеддинги и реранкер через OpenAI-совместимый API шлюза моделей."""

    _last_ensured_dim: Optional[int] = None

    def __init__(self, base_url: Optional[str] = None, timeout: Optional[float] = None):
        cfg = get_settings().rag_models_client
        self.base_url = (base_url or cfg.base_url).rstrip("/")
        self.timeout = timeout if timeout is not None else cfg.timeout
        self.embed_batch_size = max(1, int(getattr(cfg, "embed_batch_size", 24) or 24))
        self.embed_concurrency = max(1, int(getattr(cfg, "embed_concurrency", 4) or 4))
        # Потолки, которые клиент ставит себе сам после таймаута (см.
        # _note_batch_shrink). None = работаем на значениях из конфига.
        self._batch_cap: Optional[int] = None
        self._conc_cap: Optional[int] = None
        self._clean_runs = 0
        self._shrink_events = 0
        self._shrunk_this_run = False
        self.api_key_env = str(getattr(cfg, "api_key_env", "") or "").strip()
        self.embedding_model = (
            str(getattr(cfg, "embedding_model", "") or "").strip() or None
        )
        self.reranker_model = (
            str(getattr(cfg, "reranker_model", "") or "").strip() or None
        )
        logger.debug(
            "[rag-models] клиент: base_url=%s api_key_env=%s batch=%s concurrency=%s",
            self.base_url,
            self.api_key_env or "(не задан)",
            self.embed_batch_size,
            self.embed_concurrency,
        )

    def _api_key(self) -> str:
        if not self.api_key_env:
            return ""
        return str(os.getenv(self.api_key_env, "") or "").strip()

    def _cef_identity_headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {"X-CEF-Caller-Service": "SVC-RAG"}
        try:
            from app.core.cef_logger.cef_audit_context import cef_audit_peek

            _req, _user = cef_audit_peek()
            if _user:
                suser = str(_user.get("username") or "").strip()
                suid = str(_user.get("user_id") or "").strip()
                sntdom = str(_user.get("sntdom") or "").strip()
                if suser:
                    headers["X-CEF-Suser"] = suser
                if suid:
                    headers["X-CEF-Suid"] = suid
                if sntdom:
                    headers["X-CEF-Sntdom"] = sntdom
        except Exception:
            pass
        return headers

    def _headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        api_key = self._api_key()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
            headers["X-API-Key"] = api_key
        # X-User-ID
        from app.core.cef_logger.cef_audit_context import model_identity_headers

        headers.update(model_identity_headers())
        # X-CEF-*: наш сквозной аудит, к шлюзу отношения не имеет. Имена с
        # X-User-ID не пересекаются, так что порядок этих двух строк роли не
        # играет - он выбран ради одинаковой формы с
        # openai_models_compat_client.
        headers.update(self._cef_identity_headers())
        return headers

    def _client(self, timeout: Optional[float] = None) -> httpx.AsyncClient:
        t = float(timeout if timeout is not None else self.timeout)
        return httpx.AsyncClient(
            timeout=httpx.Timeout(t, connect=10.0, read=t, write=10.0),
            verify=resolve_httpx_verify(),
        )

    async def _post_json(
        self, client: httpx.AsyncClient, url: str, payload: Dict[str, Any]
    ) -> httpx.Response:
        """POST с повтором на транзиентные ошибки (до ``_MAX_ATTEMPTS`` попыток)."""
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                resp = await client.post(url, headers=self._headers(), json=payload)
            except _RETRY_EXCEPTIONS as e:
                if attempt >= _MAX_ATTEMPTS:
                    raise
                logger.warning(
                    "[rag-models] %s: %s, повтор %s/%s",
                    url,
                    type(e).__name__,
                    attempt,
                    _MAX_ATTEMPTS - 1,
                )
            else:
                if (
                    resp.status_code not in _RETRY_STATUS_CODES
                    or attempt >= _MAX_ATTEMPTS
                ):
                    resp.raise_for_status()
                    return resp
                logger.warning(
                    "[rag-models] %s: HTTP %s, повтор %s/%s",
                    url,
                    resp.status_code,
                    attempt,
                    _MAX_ATTEMPTS - 1,
                )
            await asyncio.sleep(_RETRY_BASE_DELAY_SECONDS * attempt)
        raise RuntimeError("unreachable")  # pragma: no cover

    def _log_http_error(self, what: str, err: Exception) -> None:
        if isinstance(err, httpx.HTTPStatusError):
            body = (err.response.text or "")[:500]
            logger.error(
                "[rag-models] %s: HTTP %s url=%s тело=%s (api_key_env=%s, ключ_задан=%s). "
                "404 на /v1/embeddings → на шлюзе нет embedding-роута/модели; "
                "401/403 → LLM_API_KEY.",
                what,
                err.response.status_code,
                err.request.url,
                body,
                self.api_key_env,
                bool(self._api_key()),
            )
            return
        logger.error(
            "[rag-models] %s: %s: %s (base_url=%s)",
            what,
            type(err).__name__,
            err,
            self.base_url,
        )

    async def _ensure_db_dim(self, dim: int, *, allow_migrate: bool = True) -> None:
        """Привести pgvector к размерности модели — ТОЛЬКО для кластерной модели.

        allow_migrate=False (когда модель названа явно) миграцию НЕ запускает:
        migrate_vector_tables делает TRUNCATE всех таблиц векторов, то есть стёр бы
        корпус всех пользователей из-за одного запроса чужой моделью.
        """
        if dim < 1:
            return
        if RagModelsClient._last_ensured_dim == dim:
            return
        if not allow_migrate:
            logger.warning(
                "embed: получен вектор dim=%s при явно указанной модели — "
                "миграцию схемы НЕ запускаем (защита от TRUNCATE чужих векторов)",
                dim,
            )
            return
        from app.dependencies import ensure_embedding_dim

        await ensure_embedding_dim(dim)
        RagModelsClient._last_ensured_dim = dim

    @staticmethod
    def _parse_embeddings(data: Dict[str, Any], expected: int) -> List[List[float]]:
        """Разбор ответа /v1/embeddings (порядок data[] по спеке не гарантирован)."""
        items = data.get("data") or []
        if len(items) != expected:
            raise ValueError(
                f"Число эмбеддингов ({len(items)}) не совпадает с размером батча ({expected})"
            )
        ordered = sorted(items, key=lambda it: int(it.get("index") or 0))
        return [list(it.get("embedding") or []) for it in ordered]

    def _effective_batch_size(self) -> int:
        if self._batch_cap is None:
            return self.embed_batch_size
        return max(1, min(self.embed_batch_size, self._batch_cap))

    def _effective_concurrency(self) -> int:
        if self._conc_cap is None:
            return self.embed_concurrency
        return max(1, min(self.embed_concurrency, self._conc_cap))

    def _note_embed_ok(self, used_full_batch: bool) -> None:
        """Вызов embed прошёл без таймаутов — понемногу возвращаем прежний режим.

        Иначе потолок жил бы до перезапуска пода: модель могли вернуть на GPU,
        а клиент так и работал бы в щадящем режиме. Сначала растёт батч, потом
        возвращается параллелизм — он влияет на время одного запроса сильнее.

        used_full_batch — прогон и вправду отправил батч текущего размера.
        Запрос поиска (один текст) успевает всегда и доказательством не считается.
        """
        from app.clients.embed_parallel import ADAPT_RECOVER_AFTER

        if self._batch_cap is None and self._conc_cap is None:
            return
        if not ADAPT_RECOVER_AFTER or not used_full_batch:
            return
        self._clean_runs += 1
        if self._clean_runs < ADAPT_RECOVER_AFTER:
            return
        self._clean_runs = 0
        if self._batch_cap is not None and self._batch_cap < self.embed_batch_size:
            self._batch_cap = min(self.embed_batch_size, self._batch_cap * 2)
            if self._batch_cap >= self.embed_batch_size:
                self._batch_cap = None
            logger.info(
                "[rag-models] embed: %s прогонов подряд без таймаутов — "
                "поднимаю батч до %s",
                ADAPT_RECOVER_AFTER,
                self._effective_batch_size(),
            )
            return
        self._batch_cap = None
        self._shrink_events = 0
        if self._conc_cap is not None:
            self._conc_cap = None
            logger.info(
                "[rag-models] embed: %s прогонов подряд без таймаутов — "
                "возвращаю параллельные запросы (%s)",
                ADAPT_RECOVER_AFTER,
                self.embed_concurrency,
            )

    def _note_batch_shrink(self, failed_size: int) -> None:
        """Батч размера failed_size не уложился в таймаут — снижаем нагрузку.

        Тот же приём, что в OpenAICompatModelsClient: меньший батч и очередь
        вместо параллельных запросов. На CPU одновременные POST делят одни ядра,
        поэтому каждый идёт во столько же раз дольше — параллелизм сам загоняет
        запросы в таймаут, а суммарная работа от очереди не растёт.
        """
        from app.clients.embed_parallel import (
            ADAPT_ON_TIMEOUT,
            CONC_DROP_BATCH_FLOOR,
        )

        self._shrunk_this_run = True
        self._clean_runs = 0
        if not ADAPT_ON_TIMEOUT:
            return
        self._shrink_events += 1
        new_batch = max(1, int(failed_size) // 2)
        if self._batch_cap is None or new_batch < self._batch_cap:
            self._batch_cap = new_batch
            logger.warning(
                "[rag-models] embed: после таймаута снижаю размер батча до %s "
                "(из конфига %s). K8s: RAG_MODELS_CLIENT_EMBED_BATCH_SIZE",
                self._effective_batch_size(),
                self.embed_batch_size,
            )
        # Параллелизм — последнее, что трогаем, и только когда уменьшать уже
        # нечего: сначала батч, и лишь если таймауты продолжаются с батчем в пол.
        if (
            self._batch_cap is not None
            and self._batch_cap <= CONC_DROP_BATCH_FLOOR
            and self._effective_concurrency() > 1
        ):
            self._conc_cap = max(1, self._effective_concurrency() // 2)
            logger.warning(
                "[rag-models] embed: батч уже %s, а таймауты продолжаются "
                "(%s дроблений) — снижаю параллелизм до %s (из конфига %s). "
                "K8s: RAG_MODELS_CLIENT_EMBED_CONCURRENCY",
                self._effective_batch_size(),
                self._shrink_events,
                self._conc_cap,
                self.embed_concurrency,
            )

    async def embed(
        self,
        texts: List[str],
        model: Optional[str] = None,
        kind: Optional[str] = None,
    ) -> List[List[float]]:
        """Эмбеддинги: POST {base}/v1/embeddings {"model", "input"}.

        Батчи идут параллельно (embed_concurrency /
        RAG_MODELS_CLIENT_EMBED_CONCURRENCY).
        """
        if not texts:
            return []
        use_model = (model or self.embedding_model or "").strip()
        if not use_model:
            raise ValueError(
                "embedding model не задан: укажите model в запросе или "
                "rag_models_client.embedding_model в конфиге шлюза"
            )
        url = f"{self.base_url}/v1/embeddings"
        from app.clients.embed_parallel import (
            EmbedBatchTimeout,
            embed_texts_in_batches,
            is_timeout_error,
        )

        try:
            from app.core.cef_logger import log_cef_int003_model_request

            log_cef_int003_model_request(
                base_url=self.base_url,
                model=use_model,
                provider="rag-models",
                method_name="POST /v1/embeddings",
            )
        except Exception:
            pass

        async with self._client() as client:

            async def _one_batch(start: int, batch: List[str]) -> List[List[float]]:
                payload: Dict[str, Any] = {"model": use_model, "input": batch}
                if kind:
                    payload["kind"] = kind
                try:
                    resp = await self._post_json(client, url, payload)
                    part = self._parse_embeddings(resp.json(), len(batch))
                except Exception as e:
                    self._log_http_error("POST /v1/embeddings", e)
                    try:
                        from app.core.cef_logger import log_cef_int006_outbound_failure
                        import httpx as _httpx

                        sc = (
                            e.response.status_code
                            if isinstance(e, _httpx.HTTPStatusError)
                            else None
                        )
                        log_cef_int006_outbound_failure(
                            base_url=self.base_url,
                            model=use_model,
                            provider="rag-models",
                            method_name="POST /v1/embeddings",
                            status_code=sc,
                            text_status=str(e),
                        )
                    except Exception:
                        pass
                    if is_timeout_error(e):
                        # Отдельный тип — сигнал верхнему уровню дробить батч,
                        # а не повторять его целиком.
                        raise EmbedBatchTimeout(
                            f"POST /v1/embeddings: модель эмбеддингов не ответила "
                            f"за таймаут (фрагментов={len(batch)}, "
                            f"символов={sum(len(t or '') for t in batch)}): {e}"
                        ) from e
                    raise
                if part and part[0]:
                    await self._ensure_db_dim(
                        len(part[0]), allow_migrate=model is None
                    )
                return part

            self._shrunk_this_run = False
            planned_batch = self._effective_batch_size()
            vectors = await embed_texts_in_batches(
                texts,
                batch_size=planned_batch,
                concurrency=self._effective_concurrency(),
                embed_batch=_one_batch,
                log_prefix="RAG embed",
                on_batch_shrink=self._note_batch_shrink,
            )
            if not self._shrunk_this_run:
                self._note_embed_ok(len(texts) >= planned_batch)
            return vectors

    async def embed_single(
        self,
        text: str,
        model: Optional[str] = None,
        kind: Optional[str] = None,
    ) -> List[float]:
        """Один текст — один вектор."""
        vectors = await self.embed([text], model=model, kind=kind)
        return vectors[0] if vectors else []

    async def rerank(
        self,
        query: str,
        passages: List[str],
        top_k: int = 20,
        model: Optional[str] = None,
    ) -> List[Tuple[int, float]]:
        """Реранк: POST /v1/rerank (documents + top_n)."""
        if not passages:
            return []
        use_model = (model or self.reranker_model or "").strip()
        url = f"{self.base_url}/v1/rerank"
        payload: Dict[str, Any] = {
            "query": query,
            "documents": passages,
            "top_n": min(int(top_k), len(passages)),
        }
        if use_model:
            payload["model"] = use_model
        try:
            from app.core.cef_logger import log_cef_int003_model_request

            log_cef_int003_model_request(
                base_url=self.base_url,
                model=use_model or "-",
                provider="rag-models",
                method_name="POST /v1/rerank",
            )
        except Exception:
            pass
        try:
            async with self._client() as client:
                resp = await self._post_json(client, url, payload)
                data = resp.json()
        except Exception as e:
            self._log_http_error("POST /v1/rerank", e)
            try:
                from app.core.cef_logger import log_cef_int006_outbound_failure
                import httpx as _httpx

                sc = (
                    e.response.status_code
                    if isinstance(e, _httpx.HTTPStatusError)
                    else None
                )
                log_cef_int006_outbound_failure(
                    base_url=self.base_url,
                    model=use_model or "-",
                    provider="rag-models",
                    method_name="POST /v1/rerank",
                    status_code=sc,
                    text_status=str(e),
                )
            except Exception:
                pass
            raise

        if not isinstance(data, dict):
            raise ValueError(
                f"POST /v1/rerank: неожиданный формат ответа ({type(data).__name__})"
            )
        results = data.get("results")
        if isinstance(results, list):
            pairs: List[Tuple[int, float]] = []
            for item in results:
                if not isinstance(item, dict) or item.get("index") is None:
                    continue
                pairs.append(
                    (int(item["index"]), float(item.get("relevance_score") or 0.0))
                )
            return pairs

        indices = data.get("indices")
        scores = data.get("scores")
        if isinstance(indices, list) and isinstance(scores, list):
            return list(zip(indices, scores))
        # Непонятный ответ нельзя выдавать за «все пассажи нерелевантны» ([]):
        # вызывающий код сочтёт реранк применённым. Пусть упадёт явно.
        raise ValueError(
            "POST /v1/rerank: в ответе нет ни 'results', ни 'indices'/'scores'"
        )

    async def health(self) -> bool:
        """GET /v1/models — стандартная проверка OpenAI-шлюза."""
        try:
            async with self._client(timeout=5.0) as client:
                r = await client.get(
                    f"{self.base_url}/v1/models", headers=self._headers()
                )
                return r.status_code == 200
        except Exception as e:
            logger.warning("RAG models health check failed: %s", e)
            return False
