# Клиент моделей RAG к OpenAI-совместимому провайдеру (Phoenix / LiteLLM, свой vLLM).
# Контракт совпадает с RagModelsClient: embed, embed_single, rerank, health.
import asyncio
import contextlib
import contextvars
import hashlib
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.core.http_verify import resolve_httpx_verify
from app.core.logging import get_logger

logger = get_logger(__name__)

# Повторы на стороне клиента. Зачем они нужны именно здесь: шлюз отвечает
# 500 не только когда «нельзя», но и когда у него самого что-то отвалилось —
# на Phoenix это ```Guardrail service unavailable, pre_call blocked``` (сервис
# гардрейла лёг, litellm падает закрыто) и ```Cannot connect to host ...```
# (недоступен апстрим vLLM). Оба состояния мигающие: 31.07 в 17:30 тот же
# эндпоинт и та же модель отвечали 200, в 18:01 — 500.
# Без повтора одна такая осечка роняет индексацию всего документа: батч
# эмбеддингов не досчитан → вектора не сохранены → загрузка отдаёт ошибку.
_RETRY_ATTEMPTS = max(1, int(os.getenv("RAG_EMBED_RETRY_ATTEMPTS", "3") or 3))
_RETRY_BASE_DELAY = float(os.getenv("RAG_EMBED_RETRY_BASE_DELAY", "1.0") or 1.0)
# 4xx не повторяем: это «запрос неверный» — повтор даст тот же ответ.
# 429 исключение: это «слишком часто», ровно то, что лечится ожиданием.
_RETRIABLE_STATUS = (429, 500, 502, 503, 504)
# Таймаут — особый случай среди повторяемых. Шлюз не «мигнул», он не дождался
# ответа модели: тот же батч на том же CPU не успеет и в следующий раз, а каждая
# попытка стоит полного таймаута шлюза. Поэтому по таймауту делаем ОДНУ попытку и
# отдаём управление дроблению батча (см. embed_parallel._with_timeout_split).
# K8s: RAG_EMBED_TIMEOUT_RETRY_ATTEMPTS — сколько попыток тратить на таймаут.
_TIMEOUT_RETRY_ATTEMPTS = max(
    1, int(os.getenv("RAG_EMBED_TIMEOUT_RETRY_ATTEMPTS", "1") or 1)
)

# Реранкер, не успевший за таймаут шлюза, не успеет и на следующем поиске:
# Поэтому после таймаута реранк  пропускается RAG_RERANK_PAUSE_SEC
# секунд: поиск идёт сразу в векторном порядке
# тем же путём, что и при сбое реранкера. Первый поиск после паузы
# снова пробует реранкер; ответил - пауза снята. 0 - без паузы.
_RERANK_PAUSE_SEC = max(0.0, float(os.getenv("RAG_RERANK_PAUSE_SEC", "600") or 0))
# Модель реранка → monotonic-время, до которого она на паузе.
_RERANK_PAUSED_UNTIL: Dict[str, float] = {}
_RETRIABLE_EXC = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.ReadError,
    httpx.RemoteProtocolError,
)

# ---- 1а: распознавание отказа по политике (пропустите если уже есть) ----

# 5xx, которые повторять бессмысленно: шлюз не «упал», а осознанно отказал.
# На PHOENIX guardrail отвечает 500 и для «сервис гардрейла недоступен» (это
# лечится повтором), и для «Content blocked by guardrail (pre_call): custom
# policy» — то есть текст документа отклонён политикой. Второе повторять —
# терять секунды на документ и молотить шлюз ради того же ответа.
_NON_RETRIABLE_BODY_MARKERS = (
    "content blocked by guardrail",
    "blocked by content policy",
    "content_filter",
)


def _is_permanent_refusal(resp: httpx.Response) -> bool:
    body = (resp.text or "").lower()
    return any(marker in body for marker in _NON_RETRIABLE_BODY_MARKERS)


# ---- 1б: разбор заблокированного батча ----

# Шлюз отбивает батч целиком и не говорит, что в нём не понравилось, а батч —
# это два десятка чанков одного документа. Без раскладки по фрагментам видно
# только «документ не проиндексирован», и согласовать текст с владельцами
# политики невозможно. Поэтому при отказе по политике батч переспрашивается
# по одному — ТОЛЬКО на этом пути, он и так уже отклонён.
_ISOLATE_BLOCKED = str(
    os.getenv("RAG_EMBED_GUARDRAIL_ISOLATE", "1")
).strip().lower() not in ("0", "false", "no")
# Больше этого не разбираем: диагностика не должна сама становиться нагрузкой.
_ISOLATE_MAX_BATCH = max(
    1, int(os.getenv("RAG_EMBED_GUARDRAIL_ISOLATE_MAX_BATCH", "32") or 32)
)
# Сколько символов отклонённого фрагмента писать в лог:
#   0  — не писать текст вовсе, только номер и длину (содержимое ВНД в логах
#        нужно не всем);
#  -1  — писать фрагмент целиком. Нужно, когда текст отдают владельцам политики:
#        им важно видеть ровно то, на что сработало правило;
#   N  — обрезать до N символов.
# По умолчанию -1: заблокированный чанк нужно видеть целиком, иначе согласовать
# текст с владельцами политики невозможно — обрезка обычно и съедает то место,
# на которое сработало правило. Ставьте 0, если содержимое ВНД в логах нежелательно.
try:
    _BLOCKED_PREVIEW_CHARS = int(os.getenv("RAG_EMBED_BLOCKED_PREVIEW_CHARS", "-1"))
except ValueError:
    _BLOCKED_PREVIEW_CHARS = -1


def _preview(text: str) -> str:
    if _BLOCKED_PREVIEW_CHARS == 0:
        return "<текст скрыт: RAG_EMBED_BLOCKED_PREVIEW_CHARS=0>"
    s = str(text or "")
    if _BLOCKED_PREVIEW_CHARS < 0:
        # Целиком и без потерь. json.dumps сохраняет переносы строк и кавычки,
        # запись остаётся одной строкой лога (иначе многострочный чанк порвёт
        # разбор логов) и копируется в тикет ровно тем, чем был отправлен.
        return json.dumps(s, ensure_ascii=False)
    s = " ".join(s.split())
    return s if len(s) <= _BLOCKED_PREVIEW_CHARS else f"{s[:_BLOCKED_PREVIEW_CHARS]}…"


# ---- 1в: максимально подробный разбор отказа ----
#
# Логи — единственная диагностика: curl'а в контуре нет, тело ответа шлюза
# больше нигде не оседает, а батч к моменту разбора уже потерян. Поэтому пишем
# всё, что вообще доступно: код, маркер, разобранные поля ответа, заголовки
# трассировки, тело целиком и сам отклонённый текст.

# Заголовки, по которым владельцы шлюза находят запрос у себя.
_DIAG_HEADERS = (
    "x-request-id",
    "x-correlation-id",
    "x-trace-id",
    "x-litellm-call-id",
    "x-litellm-model-id",
    "x-litellm-version",
    "retry-after",
    "date",
    "server",
)

# Ключи, в которых шлюзы прячут причину. Обходим рекурсивно: у Phoenix причина
# лежит в error.message, у части сборок litellm — во вложенном объекте гардрейла.
_REASON_KEYS = frozenset(
    (
        "message",
        "code",
        "type",
        "param",
        "reason",
        "detail",
        "details",
        "guardrail",
        "guardrail_name",
        "guardrails",
        "policy",
        "policy_name",
        "rule",
        "rule_name",
        "violation",
        "violations",
        "category",
        "categories",
        "flagged",
        "matched",
        "entities",
        "score",
        "scores",
        "blocked_by",
        "status",
        "error_code",
        "failure_reason",
    )
)


def _walk_reason_fields(
    node: Any,
    prefix: str = "",
    out: Optional[Dict[str, Any]] = None,
    depth: int = 0,
) -> Dict[str, Any]:
    """Все скалярные поля ответа, похожие на причину блокировки, с их путями."""
    if out is None:
        out = {}
    if depth > 6 or len(out) > 60:
        return out
    if isinstance(node, dict):
        for k, v in node.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            if str(k).lower() in _REASON_KEYS and (
                not isinstance(v, (dict, list))
                # Списки скаляров тоже нужны: в них лежат сработавшие категории
                # и найденные сущности («entities»: [«PERSON», «ORG»]).
                or (
                    isinstance(v, list)
                    and v
                    and not any(isinstance(x, (dict, list)) for x in v)
                )
            ):
                out[path] = v
            _walk_reason_fields(v, path, out, depth + 1)
    elif isinstance(node, list):
        for i, v in enumerate(node[:20]):
            _walk_reason_fields(v, f"{prefix}[{i}]", out, depth + 1)
    return out


def _guardrail_report(resp: httpx.Response) -> str:
    """Код, сработавший маркер, разобранные поля, заголовки и тело целиком."""
    lines = [f"HTTP {resp.status_code} {resp.reason_phrase or ''}".strip()]
    body = resp.text or ""
    low = body.lower()
    matched = [m for m in _NON_RETRIABLE_BODY_MARKERS if m in low]
    lines.append(f"сработавший маркер: {matched if matched else '— (маркеров нет)'}")
    parsed: Any = None
    with contextlib.suppress(Exception):
        parsed = json.loads(body)
    if parsed is not None:
        fields = _walk_reason_fields(parsed)
        if fields:
            lines.append("разобранные поля ответа:")
            for k, v in fields.items():
                lines.append(f"      {k} = {json.dumps(v, ensure_ascii=False)}")
        else:
            lines.append("разобранные поля ответа: — (знакомых ключей нет)")
    else:
        lines.append("тело не JSON — ниже как есть")
    hdrs = {k: v for k, v in resp.headers.items() if k.lower() in _DIAG_HEADERS}
    if hdrs:
        lines.append(f"заголовки: {json.dumps(hdrs, ensure_ascii=False)}")
    lines.append(f"тело ответа целиком: {json.dumps(body, ensure_ascii=False)}")
    return "\n    ".join(lines)


def _text_report(text: str) -> str:
    """Сам отклонённый текст плюс всё, чем его можно опознать в базе."""
    s = str(text or "")
    digest = hashlib.sha1(s.encode("utf-8", "replace")).hexdigest()
    return (
        f"длина={len(s)} символов, строк={s.count(chr(10)) + 1}, sha1={digest}\n"
        f"    текст: {_preview(s)}"
    )


# Кто индексируется прямо сейчас. Клиент про документы ничего не знает — он
# видит только список строк, — поэтому идентификацию передаёт вызывающий.
# Без неё в логе виден отклонённый чанк, но не видно, из какого он файла.
_embed_ctx: contextvars.ContextVar = contextvars.ContextVar(
    "embed_log_ctx", default=None
)


def set_embed_log_context(**fields: Any) -> None:
    """Пометить текущую операцию (стор, документ, файл) для логов гардрейла.

    Сбрасывать не нужно: пометка живёт в контексте задачи, а задача — это один
    HTTP-запрос. Внутри перечанковки всего стора каждый следующий документ
    переписывает пометку своей, так что она всегда про текущий.
    """
    _embed_ctx.set({k: v for k, v in fields.items() if v is not None})


def get_embed_log_context() -> Optional[Dict[str, Any]]:
    """Вернуть текущий контекст операции для логов (store/operation/документ/файл).

    Используется для обогащения CEF-меток при сохранении/удалении чанков.
    """
    ctx = _embed_ctx.get()
    return dict(ctx) if ctx else None


def _ctx_line() -> str:
    ctx = _embed_ctx.get()
    return json.dumps(ctx, ensure_ascii=False) if ctx else "— (не задан вызывающим)"


class OpenAICompatModelsClient:
    """Эмбеддинги и реранк через OpenAI-совместимый HTTP API.

    Параметры приходят снаружи (из фабрики), конфиг класс не читает —
    так один и тот же клиент обслуживает и Phoenix, и будущий свой vLLM.
    """

    def __init__(
        self,
        *,
        provider_id: str,
        base_url: str,
        api_key_env: Optional[str] = None,
        embedding_model: Optional[str] = None,
        reranker_model: Optional[str] = None,
        embed_path: str = "/v1/embeddings",
        rerank_path: str = "/v1/rerank",
        timeout: float = 300.0,
        embed_batch_size: int = 24,
        embed_concurrency: int = 4,
        embed_payload_style: str = "openai",
        rerank_payload_style: str = "openai",
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> None:
        self.provider_id = provider_id
        self.base_url = (base_url or "").rstrip("/")
        self.api_key_env = api_key_env
        self.embedding_model = embedding_model
        self.reranker_model = reranker_model
        self.embed_path = (
            embed_path if str(embed_path or "").startswith("/") else f"/{embed_path}"
        )
        self.rerank_path = (
            rerank_path if str(rerank_path or "").startswith("/") else f"/{rerank_path}"
        )
        self.timeout = float(timeout)
        self.embed_batch_size = max(1, int(embed_batch_size or 24))
        self.embed_concurrency = max(1, int(embed_concurrency or 1))
        self.embed_payload_style = str(embed_payload_style or "openai").strip().lower()
        self.rerank_payload_style = (
            str(rerank_payload_style or "openai").strip().lower()
        )
        self.extra_headers = dict(extra_headers or {})
        self._logged_dim = False
        self._logged_dim_note = False
        # Потолки, которые клиент ставит себе сам, когда шлюз не дожидается
        # модели (см. _note_batch_shrink). None = потолка нет, работаем на
        # значениях из конфига. Живут на экземпляре клиента: при смене модели в
        # UI клиент пересоздаётся и потолки сбрасываются.
        self._batch_cap: Optional[int] = None
        self._conc_cap: Optional[int] = None
        # Сколько вызовов embed подряд прошло без единого таймаута — счётчик
        # обратного хода (см. _note_embed_ok). _shrink_events — сколько батчей
        # пришлось дробить с последнего восстановления.
        self._clean_runs = 0
        self._shrink_events = 0
        self._shrunk_this_run = False

    # ---------- инфраструктура ----------

    def get_api_key(self) -> str:
        if not self.api_key_env:
            return ""
        return str(os.getenv(self.api_key_env, "") or "").strip()

    def _headers(self) -> Dict[str, str]:
        # Как в backend/llm_providers/openai_compat.py: LiteLLM принимает оба варианта.
        headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        api_key = self.get_api_key()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
            headers["X-API-Key"] = api_key
        # X-User-ID
        from app.core.cef_logger.cef_audit_context import model_identity_headers

        headers.update(model_identity_headers())
        # Заголовки провайдера из конфига (для Bifrost — x-bf-passthrough-extra-params).
        headers.update(self.extra_headers)
        return headers

    def _embed_payload(
        self, batch: List[str], model: str, kind: Optional[str]
    ) -> Dict[str, Any]:
        """Тело запроса эмбеддингов в стиле, который понимает провайдер.

        ```openai``` — стандартное {"model", "input"}.

        ```bifrost-passthrough``` — эмбеддинги через единственный опубликованный
        POST-маршрут шлюза (/v1/chat/completions). Проверено 31.07 на CORSUR:
        без заголовка x-bf-passthrough-extra-params приходит 422, с ним —
        200 и вектор dim=1536 (FRIDA). ```messages``` обязателен, иначе шлюз не
        примет запрос как chat-completions, хотя содержимое ему не нужно.

        Тексты идут ТОЛЬКО в ```texts```. Сначала я слал заодно и ```input``` тем же
        батчем — «пусть сервис возьмёт то поле, которое читает». Оказалось
        нельзя: 31.07 20:10 на боевой индексации это дало 400 Bad Request.
        Из трёх вариантов, ответивших 200 в прогоне 19:36, ни в одном не было
        обоих полей с непустым содержимым одновременно. Взят тот, что проверен:
        ```texts``` + ```kind```, без ```input```.

        ```kind``` нужен не для галочки: за шлюзом FRIDA, модель асимметричная,
        и от него зависят префиксы search_query/search_document.
        """
        if self.embed_payload_style == "bifrost-passthrough":
            return {
                "model": model,
                "messages": [{"role": "user", "content": "Send request"}],
                "texts": list(batch),
                "kind": kind or "document",
            }
        return {"model": model, "input": batch}

    def _client(self, timeout: Optional[float] = None) -> httpx.AsyncClient:
        t = float(timeout if timeout is not None else self.timeout)
        return httpx.AsyncClient(
            timeout=httpx.Timeout(t, connect=10.0, read=t, write=10.0),
            verify=resolve_httpx_verify(),
        )

    def _log_http_error(self, what: str, err: Exception) -> None:
        """Подробный лог ошибки. В банке нет curl — это единственная диагностика."""
        if isinstance(err, httpx.HTTPStatusError):
            body = (err.response.text or "")[:500]
            logger.error(
                "[%s] %s: HTTP %s, тело=%s (api_key_env=%s, ключ_задан=%s)",
                self.provider_id,
                what,
                err.response.status_code,
                body,
                self.api_key_env,
                bool(self.get_api_key()),
            )
            return
        logger.error(
            "[%s] %s: %s: %s (base_url=%s). Расшифровка: "
            "'missing protocol' → base_url без схемы; "
            "'Name or service not known' → неверный хост/namespace; "
            "'Connection refused' → порт; "
            "'SSL'/'certificate' → CA-сертификат (TLS_CERT_PATH).",
            self.provider_id,
            what,
            type(err).__name__,
            err,
            self.base_url,
        )

    async def _post_with_retry(
        self,
        client: httpx.AsyncClient,
        url: str,
        payload: Dict[str, Any],
        what: str,
        splittable: bool = False,
    ) -> httpx.Response:
        """POST с повтором на мигающих отказах шлюза.

        Возвращает успешный ответ либо поднимает последнюю ошибку — контракт
        вызывающего кода не меняется, он по-прежнему ловит исключение.

        splittable — у вызывающего есть чем ответить на таймаут кроме повтора
        (батч из нескольких фрагментов, который он раздробит). Только тогда имеет
        смысл экономить на повторах: они всё равно упрутся в тот же таймаут.
        Для батча из одного текста — запрос поиска — дробить нечего, и повтор
        остаётся единственным лекарством от мигнувшего шлюза: там прежние три.
        """
        from app.clients.embed_parallel import is_timeout_error

        last_exc: Optional[Exception] = None
        # Лимит попыток плавающий: таймаут снижает его до _TIMEOUT_RETRY_ATTEMPTS,
        # чтобы не ждать шлюз по кругу, а поскорее уйти на дробление батча.
        limit = _RETRY_ATTEMPTS
        for attempt in range(1, _RETRY_ATTEMPTS + 1):
            try:
                resp = await client.post(url, headers=self._headers(), json=payload)
                resp.raise_for_status()
                if attempt > 1:
                    logger.info(
                        "[%s] %s: успех с попытки %s из %s",
                        self.provider_id,
                        what,
                        attempt,
                        limit,
                    )
                return resp
            except httpx.HTTPStatusError as e:
                last_exc = e
                if e.response.status_code not in _RETRIABLE_STATUS:
                    raise
                if _is_permanent_refusal(e.response):
                    logger.warning(
                        "[%s] %s: отказ по политике, повторять бессмысленно - %s",
                        self.provider_id,
                        what,
                        (e.response.text or "")[:200],
                    )
                    raise
                if splittable and is_timeout_error(e):
                    limit = min(limit, _TIMEOUT_RETRY_ATTEMPTS)
            except _RETRIABLE_EXC as e:
                last_exc = e
                if splittable and is_timeout_error(e):
                    limit = min(limit, _TIMEOUT_RETRY_ATTEMPTS)

            if attempt >= limit:
                break
            delay = _RETRY_BASE_DELAY * (2 ** (attempt - 1))
            logger.warning(
                "[%s] %s: попытка %s из %s не удалась (%s), повтор через %.1f с",
                self.provider_id,
                what,
                attempt,
                limit,
                self._retry_reason(last_exc),
                delay,
            )
            await asyncio.sleep(delay)

        assert last_exc is not None
        raise last_exc

    @staticmethod
    def _timeout_reason(err: Exception) -> str:
        """Короткая причина таймаута для пользователя.

        Тело ответа сюда не кладём: у openresty это HTML-страница «504 Gateway
        Time-out» на несколько строк, и раньше она целиком доезжала до UI, ничего
        не объясняя. Полный ответ уже записан _log_http_error, а исходное
        исключение остаётся в цепочке (raise ... from e).
        """
        if isinstance(err, httpx.HTTPStatusError):
            return f"HTTP {err.response.status_code} от шлюза"
        return f"{type(err).__name__} на нашей стороне"

    @staticmethod
    def _retry_reason(err: Optional[Exception]) -> str:
        if isinstance(err, httpx.HTTPStatusError):
            return f"HTTP {err.response.status_code}: {(err.response.text or '')[:120]}"
        return f"{type(err).__name__}: {err}"

    def _readable(self, what: str, err: Exception) -> Exception:
        """Ошибка с телом ответа внутри.

        Голый httpx.HTTPStatusError говорит только «Client error '400'» — этот
        текст доезжает до пользователя через ```Модель эмбеддинга: ...``` и не
        объясняет ничего. Тело ответа шлюза объясняет всё, поэтому вносим его
        в сообщение. Логирование при этом остаётся на _log_http_error, который
        вызывается ДО и умеет разбирать исходный тип исключения.
        """
        if isinstance(err, httpx.HTTPStatusError):
            body = (err.response.text or "").strip().replace("\n", " ")[:300]
            return RuntimeError(
                f"[{self.provider_id}] {what}: HTTP {err.response.status_code}: {body}"
            )
        return err

    # ---------- discovery ----------

    async def list_models(self) -> List[str]:
        """Список id моделей провайдера. Пишем в лог — это и есть discovery без curl."""
        url = f"{self.base_url}/v1/models"
        try:
            async with self._client(timeout=30.0) as client:
                resp = await client.get(url, headers=self._headers())
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            self._log_http_error("GET /v1/models", e)
            return []
        items = data.get("data") or []
        ids = [
            str(item.get("id"))
            for item in items
            if isinstance(item, dict) and item.get("id")
        ]
        logger.info("[%s] models: %s", self.provider_id, ids)
        return ids

    # ---------- эмбеддинги ----------

    def _check_dim(self, dim: int) -> None:
        """Раньше здесь была жёсткая ошибка при несовпадении с кластерной dim.

        С фазы B2 у каждой размерности своя таблица векторов, поэтому чужая
        размерность — норма, а не авария: вектора просто лягут в свою таблицу.
        Ошибку оставлять нельзя — она блокировала бы пер-юзерные модели.
        """
        from app.core.config import get_settings

        db_dim = int(get_settings().postgresql.embedding_dim or 0)
        if db_dim and dim != db_dim and not self._logged_dim_note:
            logger.info(
                "[%s] dim=%s отличается от кластерной (%s) — вектора идут "
                "в таблицу своей размерности",
                self.provider_id,
                dim,
                db_dim,
            )
            self._logged_dim_note = True

    @staticmethod
    def _is_vector(value: Any) -> bool:
        """Список чисел достаточной длины, чтобы быть эмбеддингом."""
        return (
            isinstance(value, list)
            and len(value) >= 8
            and all(
                isinstance(x, (int, float)) and not isinstance(x, bool)
                for x in value[:8]
            )
        )

    @classmethod
    def _collect_vectors(cls, value: Any, depth: int = 0) -> List[List[float]]:
        """Первый найденный набор векторов, где бы он ни лежал.

        Нужно для bifrost-passthrough: шлюз заворачивает ответ эмбеддера в
        оболочку chat.completion, и вектора оказываются не в ```data```, а глубже
        (31.07: ключи верхнего уровня — choices, extra_fields, usage и т.п.).
        Гадать про точное место не хочется — оно зависит от версии шлюза,
        поэтому ищем структурно. Порядок обхода: сначала знакомые ключи,
        потом всё остальное.
        """
        if depth > 6:
            return []
        if isinstance(value, list):
            if not value:
                return []
            if cls._is_vector(value):
                return [list(value)]
            vectors = [list(x) for x in value if cls._is_vector(x)]
            if vectors:
                return vectors
            dicts = [x for x in value if isinstance(x, dict)]
            if dicts and any("embedding" in d for d in dicts):
                ordered = sorted(dicts, key=lambda it: int(it.get("index") or 0))
                found = [list(it.get("embedding") or []) for it in ordered]
                if any(found):
                    return found

            per_item = [cls._collect_vectors(item, depth + 1) for item in value]
            # Каждый элемент несёт ровно по вектору (например choices[i].message
            # .embedding) — собираем все, иначе вернули бы один и потеряли батч.
            if len(per_item) > 1 and all(len(v) == 1 for v in per_item):
                if len(dicts) == len(value) and any("index" in d for d in dicts):

                    def _pos(i: int) -> int:
                        # Именно "is None", а не "or": index=0 ложен для or,
                        # и нулевой элемент уехал бы в конец.
                        raw = value[i].get("index")
                        return int(raw) if raw is not None else i

                    order = sorted(range(len(value)), key=_pos)
                    return [per_item[i][0] for i in order]
                return [v[0] for v in per_item]
            for got in per_item:
                if got:
                    return got
            return []
        if isinstance(value, dict):
            for key in ("data", "embeddings", "embedding"):
                if key in value:
                    got = cls._collect_vectors(value[key], depth + 1)
                    if got:
                        return got
            for item in value.values():
                got = cls._collect_vectors(item, depth + 1)
                if got:
                    return got
        return []

    @staticmethod
    def _describe(value: Any, depth: int = 0) -> Any:
        """Структура ответа без данных — для лога, когда формат неожиданный."""
        if depth > 4:
            return "..."
        if isinstance(value, dict):
            return {
                k: OpenAICompatModelsClient._describe(v, depth + 1)
                for k, v in value.items()
            }
        if isinstance(value, list):
            if not value:
                return "список[0]"
            if OpenAICompatModelsClient._is_vector(value):
                return f"ВЕКТОР[{len(value)}]"
            return [
                f"список[{len(value)}]",
                OpenAICompatModelsClient._describe(value[0], depth + 1),
            ]
        if isinstance(value, str):
            return f"строка({len(value)})"
        return type(value).__name__

    # ---------- адаптация к медленной модели ----------

    def _effective_batch_size(self) -> int:
        """Размер батча с учётом потолка, выставленного после таймаутов."""
        if self._batch_cap is None:
            return self.embed_batch_size
        return max(1, min(self.embed_batch_size, self._batch_cap))

    def _effective_concurrency(self) -> int:
        """Параллелизм с учётом потолка, выставленного после таймаутов."""
        if self._conc_cap is None:
            return self.embed_concurrency
        return max(1, min(self.embed_concurrency, self._conc_cap))

    def _note_embed_ok(self, used_full_batch: bool) -> None:
        """Вызов embed завершился без таймаутов — понемногу возвращаем прежний режим.

        Сначала растёт батч, и только когда он дорос до конфигурационного —
        возвращается параллелизм. Порядок такой, потому что параллелизм влияет
        на время одного запроса сильнее: поднимать его нужно последним.

        used_full_batch — прогон и вправду отправил батч текущего размера.
        Запрос поиска (один текст) успевает всегда, и считать его
        доказательством того, что шлюз переварит батч из 24, нельзя: иначе
        поисковый трафик вернул бы прежний режим, не проверив его ничем.
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
                "[%s] embed: %s прогонов подряд без таймаутов — поднимаю батч до %s",
                self.provider_id,
                ADAPT_RECOVER_AFTER,
                self._effective_batch_size(),
            )
            return
        self._batch_cap = None
        self._shrink_events = 0
        if self._conc_cap is not None:
            self._conc_cap = None
            logger.info(
                "[%s] embed: %s прогонов подряд без таймаутов — возвращаю "
                "параллельные запросы (%s)",
                self.provider_id,
                ADAPT_RECOVER_AFTER,
                self.embed_concurrency,
            )

    def _note_batch_shrink(self, failed_size: int) -> None:
        """Батч размера failed_size не уложился в таймаут — запоминаем это.

        Два вывода из одного факта, оба снижают время ОДНОГО POST, не отключая
        ничего: батч вдвое меньше и не более одного запроса за раз. Второе важнее
        первого — N одновременных запросов к модели на CPU делят одни ядра, и
        каждый идёт примерно в N раз дольше, то есть параллелизм сам и загоняет
        запросы за таймаут шлюза. Общая пропускная способность при этом не
        падает: работа та же, просто выстроена в очередь.
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
                "[%s] embed: после таймаута снижаю размер батча до %s "
                "(из конфига %s). K8s: RAG_MODELS_CLIENT_EMBED_BATCH_SIZE",
                self.provider_id,
                self._effective_batch_size(),
                self.embed_batch_size,
            )
        # Параллелизм — последнее, что трогаем, и только когда уменьшать уже нечего
        if (
            self._batch_cap is not None
            and self._batch_cap <= CONC_DROP_BATCH_FLOOR
            and self._effective_concurrency() > 1
        ):
            self._conc_cap = max(1, self._effective_concurrency() // 2)
            logger.warning(
                "[%s] embed: батч уже %s, а таймауты продолжаются (%s дроблений) — "
                "снижаю параллелизм до %s (из конфига %s): столько запросов к одному "
                "CPU не укладываются в таймаут шлюза. "
                "K8s: RAG_MODELS_CLIENT_EMBED_CONCURRENCY",
                self.provider_id,
                self._effective_batch_size(),
                self._shrink_events,
                self._conc_cap,
                self.embed_concurrency,
            )

    @classmethod
    def _parse_embeddings(
        cls, data: Dict[str, Any], expected: int
    ) -> List[List[float]]:
        """Разбор ответа /v1/embeddings.

        Порядок data[] по спеке НЕ гарантирован — сортируем по index, иначе
        векторы молча разъедутся с текстами.
        """
        items = data.get("data")
        if isinstance(items, list) and items:
            if len(items) != expected:
                raise ValueError(
                    f"Число эмбеддингов ({len(items)}) не совпадает с размером батча ({expected})"
                )
            ordered = sorted(items, key=lambda it: int(it.get("index") or 0))
            return [list(it.get("embedding") or []) for it in ordered]

        # Нативная форма svc-rag-models: {"embeddings": [[...], [...]]}.
        # Через bifrost-passthrough ответ может прийти в ней, а не в OpenAI-формате.
        vectors = data.get("embeddings")
        if isinstance(vectors, list):
            if len(vectors) != expected:
                raise ValueError(
                    f"Число эмбеддингов ({len(vectors)}) не совпадает с размером батча ({expected})"
                )
            return [list(v or []) for v in vectors]

        # Знакомых ключей нет — ищем вектора структурно, где бы они ни лежали.
        vectors = cls._collect_vectors(data)
        if vectors:
            if len(vectors) != expected:
                raise ValueError(
                    f"Число эмбеддингов ({len(vectors)}) не совпадает с размером "
                    f"батча ({expected}). Структура ответа: "
                    f"{json.dumps(cls._describe(data), ensure_ascii=False)[:400]}"
                )
            logger.debug(
                "[EMBED-PARSE] вектора найдены не в 'data'/'embeddings'. Структура: %s",
                json.dumps(cls._describe(data), ensure_ascii=False)[:400],
            )
            return vectors

        raise ValueError(
            "В ответе нет вектора. Структура: "
            f"{json.dumps(cls._describe(data), ensure_ascii=False)[:400]}"
        )

    async def embed(
        self,
        texts: List[str],
        model: Optional[str] = None,
        kind: Optional[str] = None,
    ) -> List[List[float]]:
        """Эмбеддинги для списка текстов. Один текст — один вектор.

        model — модель этого запроса (None = модель клиента): один клиент
        обслуживает разных пользователей одного провайдера.
        kind — "query"/"document". У OpenAI-совместимого API префиксов нет,
        поле принимается ради единого контракта с RagModelsClient.

        Батчи POST идут параллельно (см. embed_concurrency /
        RAG_MODELS_CLIENT_EMBED_CONCURRENCY), порядок векторов = порядок texts.
        """
        if not texts:
            return []
        use_model = (model or self.embedding_model or "").strip()
        if not use_model:
            raise ValueError(
                f"[{self.provider_id}] embedding_model не задан — выберите модель в UI"
            )
        url = f"{self.base_url}{self.embed_path}"
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
                provider=str(self.provider_id or "openai-compat"),
                method_name=f"POST {self.embed_path}",
            )
        except Exception:
            pass

        async with self._client() as client:

            async def _one_batch(start: int, batch: List[str]) -> List[List[float]]:
                try:
                    resp = await self._post_with_retry(
                        client,
                        url,
                        self._embed_payload(batch, use_model, kind),
                        f"POST {self.embed_path}",
                        splittable=len(batch) > 1,
                    )
                    data = resp.json()
                except Exception as e:
                    self._log_http_error(f"POST {self.embed_path}", e)
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
                            provider=str(self.provider_id or "openai-compat"),
                            method_name=f"POST {self.embed_path}",
                            status_code=sc,
                            text_status=str(e),
                        )
                    except Exception:
                        pass
                    if isinstance(e, httpx.HTTPStatusError) and _is_permanent_refusal(
                        e.response
                    ):
                        logger.error(
                            "[%s] GUARDRAIL: батч %s–%s отклонён политикой шлюза\n"
                            "  что индексировалось: %s\n"
                            "  запрос: POST %s (модель=%s, kind=%s, фрагментов=%s, "
                            "символов в батче=%s)\n"
                            "  ответ шлюза:\n    %s",
                            self.provider_id,
                            start + 1,
                            start + len(batch),
                            _ctx_line(),
                            url,
                            use_model,
                            kind,
                            len(batch),
                            sum(len(t or "") for t in batch),
                            _guardrail_report(e.response),
                        )
                        await self._report_blocked_texts(
                            client, url, batch, use_model, kind, start
                        )
                    if is_timeout_error(e):
                        # Отдельный тип, чтобы верхний уровень раздробил батч, а не
                        # повторял его целиком. Текст короткий: HTML-страница 504 от
                        # openresty доезжала до пользователя целиком и ничего не
                        # объясняла.
                        raise EmbedBatchTimeout(
                            f"[{self.provider_id}] POST {self.embed_path}: "
                            f"шлюз не дождался модели эмбеддингов "
                            f"({self._timeout_reason(e)}, фрагментов={len(batch)}, "
                            f"символов={sum(len(t or '') for t in batch)})"
                        ) from e
                    raise self._readable(f"POST {self.embed_path}", e) from e
                part = self._parse_embeddings(data, len(batch))
                if part and part[0]:
                    self._check_dim(len(part[0]))
                return part
            
            self._shrink_this_run = False
            planned_batch = self._effective_batch_size()
            all_embeddings = await embed_texts_in_batches(
                texts,
                batch_size=planned_batch,
                concurrency=self._effective_concurrency(),
                embed_batch=_one_batch,
                log_prefix=f"[{self.provider_id}] embed",
                on_batch_shrink=self._note_batch_shrink,
            )
            if not self._shrink_this_run:
                self._note_embed_ok(len(texts) >= planned_batch)

        if all_embeddings and not self._logged_dim:
            logger.debug(
                "[%s] embed dim=%s (model=%s concurrency=%s batch=%s)",
                self.provider_id,
                len(all_embeddings[0]),
                use_model,
                self._effective_concurrency(),
                self._effective_batch_size(),
            )
            self._logged_dim = True
        return all_embeddings

    async def _report_blocked_texts(
        self,
        client: httpx.AsyncClient,
        url: str,
        batch: List[str],
        model: str,
        kind: Optional[str],
        offset: int,
    ) -> None:
        """Какие именно фрагменты батча отклонены политикой шлюза

        Вызывается только после отказа по политике: батч уже потерян, поэтому
        лишней нагрузки нет - мы переспрашиваем ровно те тексты, которые всё
        равно не проиндексировались. Исключений не поднимает: это диагностика,
        она не должна подменять исходную ошибку
        """
        first, last = offset + 1, offset + len(batch)
        if not _ISOLATE_BLOCKED or len(batch) > _ISOLATE_MAX_BATCH:
            # Молча не уходим: иначе в логе останется отказ по батчу без единого
            # слова о том, почему не видно конкретных фрагментов.
            logger.warning(
                "[%s] GUARDRAIL: разбор батча %s–%s по фрагментам ПРОПУЩЕН "
                "(RAG_EMBED_GUARDRAIL_ISOLATE=%s, размер батча %s при лимите "
                "RAG_EMBED_GUARDRAIL_ISOLATE_MAX_BATCH=%s). Какой именно текст "
                "отклонён — не установлено",
                self.provider_id,
                first,
                last,
                "1" if _ISOLATE_BLOCKED else "0",
                len(batch),
                _ISOLATE_MAX_BATCH,
            )
            return
        blocked: List[int] = []
        for i, text in enumerate(batch):
            try:
                resp = await client.post(
                    url,
                    headers=self._headers(),
                    json=self._embed_payload([text], model, kind),
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                if not _is_permanent_refusal(e.response):
                    continue
                blocked.append(offset + i + 1)
                logger.error(
                    "[%s] GUARDRAIL: отклонён фрагмент #%s (%s-й в батче %s–%s)\n"
                    "  что индексировалось: %s\n"
                    "  запрос: POST %s (модель=%s, kind=%s)\n"
                    "  фрагмент: %s\n"
                    "  ответ шлюза:\n    %s",
                    self.provider_id,
                    offset + i + 1,
                    i + 1,
                    first,
                    last,
                    _ctx_line(),
                    url,
                    model,
                    kind,
                    _text_report(text),
                    _guardrail_report(e.response),
                )
            except Exception:
                # Сетевая осечка на разборе - не наш случай, молча пропускаем.
                continue
        if blocked:
            logger.error(
                "[%s] GUARDRAIL ИТОГ: в батче %s–%s отклонено %s из %s "
                "(номера: %s), прошло %s. Политика сработала на конкретных "
                "текстах — они выведены выше целиком. Что индексировалось: %s",
                self.provider_id,
                first,
                last,
                len(blocked),
                len(batch),
                blocked,
                len(batch) - len(blocked),
                _ctx_line(),
            )
        else:
            logger.warning(
                "[%s] guardrail отклонил батч %s–%s целиком, но по одному все %s "
                "фрагментов прошли. Значит, дело не в конкретном тексте, а в объёме "
                "запроса или сочетании фрагментов - попробуйте уменьшить "
                "rag_models_client.embed_batch_size",
                self.provider_id,
                first,
                last,
                len(batch),
            )

    async def embed_single(
        self,
        text: str,
        model: Optional[str] = None,
        kind: Optional[str] = None,
    ) -> List[float]:
        """Один текст — один вектор."""
        vectors = await self.embed([text], model=model, kind=kind)
        return vectors[0] if vectors else []

    async def probe_dim(self) -> int:
        """Размерность embedding-модели одним пробным вызовом (БЕЗ dim-гарда).

        Используется эндпоинтом переключения: узнать dim ДО решения о миграции.
        """
        if not self.embedding_model:
            raise ValueError(
                f"[{self.provider_id}] embedding_model не задан - probe_dim невозможен"
            )
        url = f"{self.base_url}{self.embed_path}"
        try:
            async with self._client(timeout=60.0) as client:
                resp = await self._post_with_retry(
                    client,
                    url,
                    self._embed_payload(["ping"], self.embedding_model, "query"),
                    f"POST {self.embed_path} (probe)",
                )
                data = resp.json()
        except Exception as e:
            self._log_http_error(f"POST {self.embed_path} (probe)", e)
            raise self._readable(f"POST {self.embed_path} (probe)", e) from e
        vectors = self._parse_embeddings(data, 1)
        dim = len(vectors[0]) if vectors and vectors[0] else 0
        logger.info(
            "[%s] probe dim=%s (model=%s)", self.provider_id, dim, self.embedding_model
        )
        return dim

    # ---------- реранк ----------

    async def rerank(
        self,
        query: str,
        passages: List[str],
        top_k: int = 20,
        model: Optional[str] = None,
    ) -> List[Tuple[int, float]]:
        """Реранк пассажей. Возвращает пары (индекс в passages, скор).

        При ошибке — подробный лог и re-raise, как у нативного RagModelsClient.
        НЕ возвращать пустой список: вызывающий код (rerank_helpers) ловит
        исключение и откатывается на векторный порядок — это и есть graceful
        fallback, а [] неотличим от «реранкер счёл всё нерелевантным».
        """
        if not passages:
            return []
        use_model = (model or self.reranker_model or "").strip()
        if not use_model:
            raise ValueError(
                f"[{self.provider_id}] reranker_model не задан — выберите модель в UI "
                "(вызывающий код продолжит без реранка)"
            )
        paused_for = _RERANK_PAUSED_UNTIL.get(use_model, 0.0) - time.monotonic()
        if paused_for > 0:
            # Исключение, а не []: вызывающий код откатится на векторный порядок
            # и пометит реранк как неприменённый (см. докстринг выше).
            raise RuntimeError(
                f"[{self.provider_id}] реранкер {use_model} на паузе после таймаута "
                f"шлюза, ещё {paused_for:.0f} с - поиск без реранка"
            )
        url = f"{self.base_url}{self.rerank_path}"
        payload = self._rerank_payload(query, passages, use_model, top_k)
        try:
            from app.core.cef_logger import log_cef_int003_model_request

            log_cef_int003_model_request(
                base_url=self.base_url,
                model=use_model,
                provider=str(self.provider_id or "openai-compat"),
                method_name=f"POST {self.rerank_path}",
            )
        except Exception:
            pass
        try:
            async with self._client() as client:
                # splittable=True: на таймаут у вызывающего есть ответ кроме
                # повтора - векторный порядок. Две лишние попытки стоили ~120 с
                # и упирались в тот же таймаут шлюза.
                resp = await self._post_with_retry(
                    client,
                    url,
                    payload,
                    f"POST {self.rerank_path}",
                    splittable=True,
                )
                data = resp.json()
            if _RERANK_PAUSED_UNTIL.pop(use_model, None) is not None:
                logger.info("[%s] реранкер %s снова отвечает - пауза снята", self.provider_id, use_model)
        except Exception as e:
            self._log_http_error(f"POST {self.rerank_path}", e)
            from app.clients.embed_parallel import is_timeout_error

            if _RERANK_PAUSE_SEC > 0 and is_timeout_error(e):
                _RERANK_PAUSED_UNTIL[use_model] = time.monotonic() + _RERANK_PAUSE_SEC
                logger.warning(
                    "[%s] реранкер %s не ответил за таймаут шлюза - поиск идёт без "
                    "реранка %.0f с (RAG_RERANK_PAUSE_SEC), потом попробуем снова",
                    self.provider_id,
                    use_model,
                    _RERANK_PAUSE_SEC,
                )
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
                    provider=str(self.provider_id or "openai-compat"),
                    method_name=f"POST {self.rerank_path}",
                    status_code=sc,
                    text_status=str(e),
                )
            except Exception:
                pass
            raise self._readable(f"POST {self.rerank_path}", e) from e

        pairs = self._parse_rerank(data, len(passages))
        if not pairs:
            # Пустой ответ неотличим от «всё нерелевантно», поэтому это ошибка:
            # вызывающий код откатится на векторный порядок и пометит
            # реранк как НЕприменённый.
            raise ValueError(
                f"[{self.provider_id}] реранк не разобран. Структура ответа: "
                f"{json.dumps(self._describe(data), ensure_ascii=False)[:400]}"
            )
        logger.debug(
            "[%s] rerank: ok, вернулось %s из %s (model=%s, стиле=%s)",
            self.provider_id,
            len(pairs),
            len(passages),
            use_model,
            self.rerank_payload_style,
        )
        return pairs

    def _rerank_payload(
        self, query: str, passages: List[str], model: str, top_k: int
    ) -> Dict[str, Any]:
        """Тело запроса реранка в стиле провайдера.

        ```openai``` — стандартное cohere-подобное {"query", "documents", "top_n"}.

        ```bifrost-passthrough``` — через единственный опубликованный POST-маршрут
        шлюза (/v1/chat/completions). Поля взяты из рабочего скрипта коллег:
        ```query```/```passages```/```top_k``` строкой плюс обязательный ```messages``` —
        без него шлюз не примет запрос как chat-completions. Проверено 31.07:
        так реранк-модель отвечает 200, тогда как /v1/rerank даёт 404.
        """
        if self.rerank_payload_style == "bifrost-passthrough":
            return {
                "model": model,
                "query": query,
                "passages": list(passages),
                "messages": [{"role": "user", "content": "Send request"}],
                "top_k": str(min(int(top_k), len(passages))),
            }
        return {
            "model": model,
            "query": query,
            "documents": list(passages),
            "top_n": min(int(top_k), len(passages)),
        }

    @classmethod
    def _parse_rerank(cls, data: Any, expected: int) -> List[Tuple[int, float]]:
        """Пары (индекс пассажа, скор) из ответа любой известной формы.

        Форму ответа тоннеля мы вживую не видели — знаем только, что это
        оболочка chat.completion с ```choices: null```. Поэтому разбор идёт по
        нескольким вариантам, а не по одному ключу:

        1. cohere-подобный ```results: [{index, relevance_score}]```;
        2. список скоров, выровненный с пассажами: ```scores: [0.9, 0.1, ...]```;
        3. то же, но завёрнутое глубже (```extra_fields.raw_response``` и т.п.);
        4. JSON строкой внутри ```choices[].message.content```.

        Ничего не подошло — пустой список; вызывающий код поднимет ошибку
        с распечаткой структуры, и по одному прогону станет ясно, что добавить.
        """
        found = cls._rerank_from_obj(data, expected)
        if found:
            return found
        # Вариант 4: модель вернула JSON текстом.
        for choice in (data.get("choices") or []) if isinstance(data, dict) else []:
            if not isinstance(choice, dict):
                continue
            content = ((choice.get("message") or {}) or {}).get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            try:
                inner = json.loads(content[content.find("{") : content.rfind("}") + 1])
            except Exception:
                continue
            found = cls._rerank_from_obj(inner, expected)
            if found:
                return found
        return []

    @classmethod
    def _rerank_from_obj(
        cls, value: Any, expected: int, depth: int = 0
    ) -> List[Tuple[int, float]]:
        if depth > 6 or not isinstance(value, (dict, list)):
            return []
        if isinstance(value, dict):
            items = value.get("results")
            if isinstance(items, list) and items:
                pairs = [
                    (
                        int(it["index"]),
                        float(it.get("relevance_score") or it.get("score") or 0.0),
                    )
                    for it in items
                    if isinstance(it, dict) and it.get("index") is not None
                ]
                if pairs:
                    return pairs
            for key in ("scores", "relevance_scores", "rerank_scores"):
                scores = value.get(key)
                if isinstance(scores, list) and len(scores) == expected:
                    if all(
                        isinstance(x, (int, float)) and not isinstance(x, bool)
                        for x in scores
                    ):
                        ordered = sorted(
                            enumerate(float(x) for x in scores),
                            key=lambda p: p[1],
                            reverse=True,
                        )
                        return ordered
            for item in value.values():
                got = cls._rerank_from_obj(item, expected, depth + 1)
                if got:
                    return got
            return []
        for item in value:
            got = cls._rerank_from_obj(item, expected, depth + 1)
            if got:
                return got
        return []

    # ---------- health ----------

    async def health(self) -> bool:
        """Доступность провайдера.

        Бьём в /v1/models, а не в health/liveliness: liveliness отвечает 200 и
        без ключа, а нам надо знать, что сеть + CA + авторизация в порядке.
        """
        try:
            async with self._client(timeout=5.0) as client:
                resp = await client.get(
                    f"{self.base_url}/v1/models", headers=self._headers()
                )
                return resp.status_code == 200
        except Exception as e:
            logger.warning("[%s] health check failed: %s", self.provider_id, e)
            return False
