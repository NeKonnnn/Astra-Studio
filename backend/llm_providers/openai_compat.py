"""
Базовый OpenAI-совместимый провайдер.

Используется как основа для vLLM, Ollama (OpenAI-mode), LiteLLM, OpenRouter,
OpenAI и любых custom-серверов, поддерживающих ``/v1/chat/completions``.
Реализует:

- ``chat`` через POST /v1/chat/completions;
- ``stream_chat`` через POST /v1/chat/completions со SSE;
- ``list_models`` через GET /v1/models;
- ``health`` через GET /v1/health с fallback на /v1/models (если health нет);
- ``ensure_model_loaded`` по умолчанию = проверка, что model_id есть
  в списке моделей провайдера (без сетевых побочных эффектов).

Подклассы могут переопределить любой метод (например, ``LlmSvcProvider``
добавляет POST /v1/models/load в ``ensure_model_loaded``).
"""

from __future__ import annotations

import asyncio
import html as _html_module
import json
import logging
import os
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple

import httpx

from backend.settings.cef_logger.cef_logger import (
    log_cef_int003_llm_request,
    log_cef_int006_llm_api_failure,
)

try:
    from backend.llm_providers.routing import is_thinking_requested
except Exception:  # pragma: no cover
    def is_thinking_requested(request_extra):
        if not request_extra:
            return False
        if 'enable_thinking' in request_extra:
            return bool(request_extra.get('enable_thinking'))
        ctk = request_extra.get('chat_template_kwargs')
        if isinstance(ctk, dict) and 'enable_thinking' in ctk:
            return bool(ctk.get('enable_thinking'))
        return False

from .presentation_continue import (
    _auto_continue_max,
    _count_html_slides,
    _finalize_presentation_message,
    _looks_like_presentation_html,
    _merge_presentation_continue,
    _open_presentation_for_continue,
    _presentation_looks_finished,
    _presentation_looks_truncated,
    _requested_slide_count,
    _stream_read_timeout_sec,
)
from .base import (
    LLMProvider,
    LLMProviderConfig,
    ChatResult,
    ModelInfo,
    ProviderCapabilities,
    ProviderHealth,
    StreamCallback,
    ToolCall,
)

logger = logging.getLogger(__name__)


def _safe_response_text(response: Any) -> str:
    """Тело ответа, даже если это недочитанный поток (иначе ResponseNotRead)."""
    try:
        return response.text or ""
    except Exception:
        return "<тело ответа недоступно: поток не прочитан>"


def _invoke_stream_callback(callback: Any, chunk: str, acc: str, stream_role: str = "content") -> bool:
    """Вызывает callback стрима; поддерживает 2- и 3-аргументные колбэки.

    ``False`` — прервать поток. Любое другое значение (включая ``None``) — продолжать.
    """
    try:
        result = callback(chunk, acc, stream_role)
    except TypeError:
        if stream_role == "heartbeat":
            return True
        result = callback(chunk, acc)
    return result is not False


# =============================================================================
# Очистка ответа LLM от артефактов chat template (перенесено из llm_client.py)
# =============================================================================


_CHAT_TEMPLATE_RE_START = [
    re.compile(r"<\|im_start\|>.*", re.DOTALL),
    re.compile(r"&lt;\|im_start\|&gt;.*", re.DOTALL),
    re.compile(r"&amp;lt;\|im_start\|&amp;gt;.*", re.DOTALL),
    re.compile(r"&amp;amp;lt;\|im_start\|&amp;amp;gt;.*", re.DOTALL),
    re.compile(r"&amp;amp;amp;lt;\|im_start\|&amp;amp;amp;gt;.*", re.DOTALL),
]
_CHAT_TEMPLATE_RE_END = [
    re.compile(r"<\|im_end\|>.*", re.DOTALL),
    re.compile(r"&lt;\|im_end\|&gt;.*", re.DOTALL),
    re.compile(r"&amp;lt;\|im_end\|&amp;gt;.*", re.DOTALL),
]


def clean_llm_response(text: str) -> str:
    """Убирает хвост ``<|im_start|>`` / ``<|im_end|>`` и HTML entities."""
    if not text:
        return text
    for rx in _CHAT_TEMPLATE_RE_START:
        text = rx.sub("", text)
    for rx in _CHAT_TEMPLATE_RE_END:
        text = rx.sub("", text)
    # Вложенный HTML escaping иногда встречается (после нескольких прогонов).
    for _ in range(3):
        new_text = _html_module.unescape(text)
        if new_text == text:
            break
        text = new_text
    return text.rstrip()


def _strip_think_tags(text: str) -> str:
    """Удаляет блоки <think>...</think> и незакрытые <think>... из текста.

    Используется в режиме быстрого ответа (thinking_requested=False), чтобы
    рассуждения модели не попадали в финальный ответ пользователю.
    """
    if not text or "<think>" not in text.lower():
        return text
    # Закрытые блоки
    text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE)
    # Незакрытый блок (модель не успела закрыть тег)
    text = re.sub(r"<think>[\s\S]*$", "", text, flags=re.IGNORECASE)
    return text.strip()


def _normalize_reasoning_payload(value: Any) -> str:
    """Нормализует reasoning payload к плоскому тексту."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: List[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                txt = item.get("text") or item.get("content") or item.get("reasoning")
                if isinstance(txt, str):
                    parts.append(txt)
        return "".join(parts)
    if isinstance(value, dict):
        txt = (
            value.get("text")
            or value.get("content")
            or value.get("reasoning")
            or value.get("reasoning_text")
            or value.get("thinking")
            or value.get("thought")
        )
        if isinstance(txt, str):
            return txt
        # Иногда reasoning лежит в массиве content-частей.
        nested_content = value.get("content")
        if isinstance(nested_content, list):
            return _normalize_reasoning_payload(nested_content)
        # Fallback: пробуем найти поле по "разумным" ключам вглубь.
        parts: List[str] = []
        for k, v in value.items():
            if any(token in str(k).lower() for token in ("reason", "think", "thought")):
                parts.append(_normalize_reasoning_payload(v))
        return "".join(parts)
    return str(value)


def _normalize_content_payload(value: Any) -> str:
    """Нормализует content payload к строке."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: List[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if item.get("type") in ("text", "output_text"):
                    txt = item.get("text") or item.get("content")
                    if isinstance(txt, str):
                        parts.append(txt)
                else:
                    txt = item.get("content") or item.get("text")
                    if isinstance(txt, str):
                        parts.append(txt)
        return "".join(parts)
    if isinstance(value, dict):
        txt = value.get("text") or value.get("content")
        return txt if isinstance(txt, str) else ""
    return str(value)


# =============================================================================
# OpenAICompatProvider
# =============================================================================


class OpenAICompatProvider(LLMProvider):
    """Провайдер для любого OpenAI-совместимого REST."""

    #: Путь health-эндпоинта. Подклассы могут переопределить (у OpenAI нет
    #: health; у vLLM — ``/health`` без тела).
    HEALTH_PATH: str = "/v1/health"

    #: Если True — ``health()`` падает на ``list_models`` при 404/405 на
    #: HEALTH_PATH (полезно для OpenAI.com, у которого health просто нет).
    HEALTH_FALLBACK_TO_MODELS: bool = True

    #: Поля ``request_extra``, которые этот REST не понимает и на которые
    #: отвечает ошибкой. Пусто для OpenAI-совместимых шлюзов (vLLM, llm-svc,
    #: LiteLLM, Ollama): они читают ``enable_thinking`` /
    #: ``chat_template_kwargs`` и должны получать их как раньше.
    UNSUPPORTED_REQUEST_EXTRA_KEYS: frozenset = frozenset()

    _capabilities = ProviderCapabilities(
        hot_swap=False,
        multi_loaded=True,
        native_chat_api=True,
        streaming=True,
        vision=True,
        function_calling=True,
        prompt_json_fc=True,
        langgraph_agent=True,
    )

    def _apply_request_extra(
        self, payload: Dict[str, Any], request_extra: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Переносит ``request_extra`` в payload, отбрасывая непонятные этому REST поля.

        Провайдер сам решает, какие дополнительные поля запроса он понимает:
        отправитель (``thinking_request_extra`` и пр.) знает про режим мышления,
        но не про то, какой эндпоинт по ту сторону.
        """
        if not request_extra:
            return payload
        dropped = []
        for key, value in request_extra.items():
            if value is None:
                continue
            if key in self.UNSUPPORTED_REQUEST_EXTRA_KEYS:
                dropped.append(key)
                continue
            payload[key] = value
        if dropped:
            logger.debug(
                "[%s] поля запроса не поддерживаются этим провайдером, отброшены: %s",
                self.id,
                sorted(dropped),
            )
        return payload

    def __init__(self, config: LLMProviderConfig) -> None:
        super().__init__(config)
        self._timeout_read = float(config.timeout)
        extra = config.extra or {}
        if "function_calling" in extra:
            fc = bool(extra.get("function_calling"))
            self._capabilities = ProviderCapabilities(
                hot_swap=self._capabilities.hot_swap,
                multi_loaded=self._capabilities.multi_loaded,
                native_chat_api=self._capabilities.native_chat_api,
                streaming=self._capabilities.streaming,
                vision=self._capabilities.vision,
                function_calling=fc,
                prompt_json_fc=bool(extra.get("prompt_json_fc", True)),
                langgraph_agent=bool(extra.get("langgraph_agent", True)),
            )

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    # ---- HTTP helpers -----------------------------------------------------

    def _headers(self, *, accept_sse: bool = False) -> Dict[str, str]:
        headers: Dict[str, str] = {"Content-Type": "application/json", "Accept": "application/json"}
        if accept_sse:
            headers["Accept"] = "text/event-stream"
        api_key = self.get_api_key()
        if api_key:
            # OpenAI-style. Для llm-svc заголовок игнорируется.
            headers["Authorization"] = f"Bearer {api_key}"
            # На всякий случай дублируем — некоторые custom-серверы ждут X-API-Key.
            headers["X-API-Key"] = api_key
        return headers

    def _auth_diag(self, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Безопасная диагностика авторизации (без вывода секретов)."""
        hdrs = headers or self._headers()
        return {
            "api_key_env": self._config.api_key_env,
            "api_key_set": bool(self.get_api_key()),
            "auth_header": "Authorization: Bearer *" if "Authorization" in hdrs else None,
            "x_api_key_header": "X-API-Key: *" if "X-API-Key" in hdrs else None,
        }

    def _safe_auth_diag(self, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """
        Защитный вызов auth-диагностики.
        Нужен на случай старых рантаймов/оберток, где _auth_diag не принимает headers.
        """
        try:
            return self._auth_diag(headers)
        except TypeError:
            try:
                return self._auth_diag()  # type: ignore[call-arg]
            except Exception:
                return {"api_key_env": self._config.api_key_env, "api_key_set": bool(self.get_api_key())}
        except Exception:
            return {"api_key_env": self._config.api_key_env, "api_key_set": bool(self.get_api_key())}

    def _http_error_details(self, e: Exception) -> Dict[str, Any]:
        """
        Нормализованный payload ошибки HTTP для логов.
        Даёт максимально полезную причину: status code + кусок response body.
        """
        if isinstance(e, httpx.HTTPStatusError):
            response = e.response
            body = ""
            try:
                body = (response.text or "")[:500]
            except Exception:
                body = ""
            return {
                "error_type": type(e).__name__,
                "status_code": response.status_code,
                "reason": body or str(e),
            }
        return {
            "error_type": type(e).__name__,
            "status_code": None,
            "reason": str(e),
        }

    def _short_timeout(self) -> httpx.Timeout:
        return httpx.Timeout(10.0, connect=5.0, read=10.0, write=5.0)

    def _request_timeout(self, seconds: Optional[float] = None) -> httpx.Timeout:
        t = float(seconds) if seconds is not None else self._timeout_read
        return httpx.Timeout(t, connect=10.0, read=t, write=10.0)

    def _http_verify(self) -> Any:
        """
        TLS verify для httpx.
        Приоритет: TLS_CERT_PATH -> SSL_CERT_FILE -> REQUESTS_CA_BUNDLE -> True.
        """
        for env_name in ("TLS_CERT_PATH", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
            cert_path = str(os.getenv(env_name, "") or "").strip()
            if cert_path:
                return cert_path
        return True

    # ---- health -----------------------------------------------------------

    async def health(self) -> ProviderHealth:
        url = f"{self.base_url}{self.HEALTH_PATH}"
        headers = self._headers()
        try:
            async with httpx.AsyncClient(timeout=self._short_timeout(), verify=self._http_verify()) as client:
                response = await client.get(
                    url,
                    headers=headers,
                )
                if response.status_code == 200:
                    try:
                        payload = response.json()
                    except Exception:
                        payload = {}
                    if isinstance(payload, dict):
                        return self._interpret_health_payload(payload)
                    return ProviderHealth(healthy=True, raw={"value": payload})
                if response.status_code in (404, 405) and self.HEALTH_FALLBACK_TO_MODELS:
                    return await self._health_via_models(client)
                return ProviderHealth(
                    healthy=False,
                    error=f"HTTP {response.status_code}: {response.text[:200]}",
                )
        except Exception as e:
            details = self._http_error_details(e)
            logger.warning(
                "[LLM-PROVIDER][health] id=%s url=%s status=%s type=%s reason=%r auth=%s",
                self.id, url, details.get("status_code"), details.get("error_type"),
                details.get("reason"), self._safe_auth_diag(headers),
            )
            return ProviderHealth(healthy=False, error=str(e))

    def _interpret_health_payload(self, payload: Dict[str, Any]) -> ProviderHealth:
        """Из произвольного health-JSON извлекаем список загруженных моделей."""
        loaded: List[str] = []
        lm = payload.get("loaded_models")
        if isinstance(lm, list):
            loaded = [str(x) for x in lm if x]
        elif payload.get("model_loaded") and payload.get("model_name"):
            loaded = [str(payload["model_name"])]
        status = str(payload.get("status", "")).lower()
        healthy = (not status) or status in ("ok", "healthy", "up", "ready")
        return ProviderHealth(healthy=healthy, loaded_models=loaded, raw=payload)

    async def _health_via_models(self, client: httpx.AsyncClient) -> ProviderHealth:
        try:
            response = await client.get(f"{self.base_url}/v1/models", headers=self._headers())
            if response.status_code == 200:
                return ProviderHealth(healthy=True, raw={"fallback": "models"})
            return ProviderHealth(healthy=False, error=f"/v1/models HTTP {response.status_code}")
        except Exception as e:
            return ProviderHealth(healthy=False, error=f"/v1/models: {e}")

    # ---- list models ------------------------------------------------------

    async def list_models(self) -> List[ModelInfo]:
        static = (self._config.static_model or "").strip()
        configured = [str(m).strip() for m in (self._config.models or []) if str(m or "").strip()]
        url = f"{self.base_url}/v1/models"
        headers = self._headers()
        try:
            async with httpx.AsyncClient(timeout=self._short_timeout(), verify=self._http_verify()) as client:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                data = response.json()
            items: List[ModelInfo] = []
            for row in data.get("data", []) or []:
                if not isinstance(row, dict):
                    continue
                mid = str(row.get("id") or "").strip()
                if not mid:
                    continue
                items.append(
                    ModelInfo(
                        provider_id=self.id,
                        model_id=mid,
                        display_name=str(row.get("display_name") or row.get("name") or mid),
                        extra={k: v for k, v in row.items() if k not in {"id"}},
                    )
                )
            existing_ids = {m.model_id for m in items}
            # Ручной список моделей из конфига добавляем как fallback/override:
            # это позволяет стабильно показывать модели даже при неполном /v1/models.
            for mid in configured:
                if mid in existing_ids:
                    continue
                items.append(
                    ModelInfo(
                        provider_id=self.id,
                        model_id=mid,
                        display_name=mid,
                        extra={"synthetic": True, "reason": "configured_model"},
                    )
                )
            if not items and static:
                # Сервер вернул пустой список, но в конфиге есть static_model.
                items.append(
                    ModelInfo(
                        provider_id=self.id,
                        model_id=static,
                        display_name=static,
                        extra={"synthetic": True, "reason": "empty_list_with_static_model"},
                    )
                )
            return items
        except Exception as e:
            details = self._http_error_details(e)
            logger.warning(
                "[LLM-PROVIDER][list_models] id=%s url=%s status=%s type=%s reason=%r auth=%s",
                self.id, url, details.get("status_code"), details.get("error_type"),
                details.get("reason"), self._safe_auth_diag(headers),
            )
            if static:
                logger.warning(
                    "Provider %s /v1/models failed (%s); fallback на static_model=%r",
                    self.id, e, static,
                )
                fallback_items = [
                    ModelInfo(
                        provider_id=self.id, model_id=static, display_name=static,
                        extra={"synthetic": True, "reason": f"fallback:{type(e).__name__}"},
                    )
                ]
                existing_ids = {m.model_id for m in fallback_items}
                for mid in configured:
                    if mid in existing_ids:
                        continue
                    fallback_items.append(
                        ModelInfo(
                            provider_id=self.id,
                            model_id=mid,
                            display_name=mid,
                            extra={"synthetic": True, "reason": "configured_model"},
                        )
                    )
                return fallback_items
            if configured:
                return [
                    ModelInfo(
                        provider_id=self.id,
                        model_id=mid,
                        display_name=mid,
                        extra={"synthetic": True, "reason": "configured_model"},
                    )
                    for mid in configured
                ]
            logger.error("Provider %s /v1/models error: %s", self.id, e)
            return []

    # ---- ensure model loaded ---------------------------------------------

    async def ensure_model_loaded(self, model_id: str) -> bool:
        """
        Базовая реализация: проверяем, что model_id есть в list_models() или
        совпадает со static_model. Никаких сетевых побочных эффектов.
        Подклассы (LlmSvcProvider) переопределяют это.
        """
        mid = (model_id or "").strip()
        if not mid:
            return False
        static = (self._config.static_model or "").strip()
        if static and mid == static:
            return True
        try:
            models = await self.list_models()
        except Exception as e:
            logger.warning("ensure_model_loaded(%s): list_models error: %s", mid, e)
            return bool(static and mid == static)
        for m in models:
            if m.model_id == mid:
                return True
        logger.warning(
            "Provider %s: модель %r отсутствует в /v1/models. "
            "Она должна быть запущена на стороне сервера (для vLLM/OpenAI свап невозможен).",
            self.id, mid,
        )
        return False

    # ---- chat / stream_chat ----------------------------------------------

    def _parse_chat_response(self, data: dict, *, cef_rid: str) -> ChatResult:
        choices = data.get("choices") or []
        if not choices:
            logger.error("[%s] chat: нет choices в ответе: %s", self.id, data)
            log_cef_int006_llm_api_failure(
                request_uuid=cef_rid,
                code_status="FORMAT",
                text_status=str(data)[:512],
                service_name=f"openai-compat-{self.id}",
                status_code=200,
            )
            return ChatResult(content="Ошибка генерации ответа")
        msg = choices[0].get("message") or {}
        tool_calls_raw = msg.get("tool_calls") or []
        tool_calls: List[ToolCall] = []
        for tc in tool_calls_raw:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") or {}
            name = str(fn.get("name") or "")
            if not name:
                continue
            args_raw = fn.get("arguments") or "{}"
            try:
                args = json.loads(args_raw) if isinstance(args_raw, str) else dict(args_raw or {})
            except json.JSONDecodeError:
                args = {"raw": args_raw}
            tool_calls.append(
                ToolCall(
                    id=str(tc.get("id") or uuid.uuid4().hex),
                    name=name,
                    arguments=args if isinstance(args, dict) else {},
                )
            )
        content = _normalize_content_payload(msg.get("content"))
        reasoning = _normalize_reasoning_payload(
            msg.get("reasoning_content") or msg.get("reasoning")
        ).strip()
        cleaned = clean_llm_response(content)
        if reasoning and "<think>" not in cleaned:
            cleaned = f"<think>{reasoning}</think>\n\n{cleaned}" if reasoning else cleaned
        return ChatResult(content=cleaned, tool_calls=tool_calls, raw_message=msg)

    async def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        *,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Any] = None,
        request_extra: Optional[Dict[str, Any]] = None,
    ) -> ChatResult:
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice if tool_choice is not None else "auto"
        self._apply_request_extra(payload, request_extra)
        cef_rid = uuid.uuid4().hex
        log_cef_int003_llm_request(
            base_url=self.base_url,
            provider_id=self.id,
            model=model,
            request_uuid=cef_rid,
        )
        async with httpx.AsyncClient(timeout=self._request_timeout(), verify=self._http_verify()) as client:
            try:
                response = await client.post(
                    f"{self.base_url}/v1/chat/completions",
                    headers=self._headers(),
                    json=payload,
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as he:
                log_cef_int006_llm_api_failure(
                    request_uuid=cef_rid,
                    code_status=str(he.response.status_code),
                    text_status=(he.response.text or "")[:512],
                    service_name=f"openai-compat-{self.id}",
                    status_code=he.response.status_code,
                )
                raise
            except Exception as e:
                log_cef_int006_llm_api_failure(
                    request_uuid=cef_rid,
                    code_status="EXCEPTION",
                    text_status=str(e)[:512],
                    service_name=f"openai-compat-{self.id}",
                    status_code=None,
                )
                raise
            data = response.json()
        return self._parse_chat_response(data, cef_rid=cef_rid)

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        *,
        request_extra: Optional[Dict[str, Any]] = None,
    ) -> str:
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        self._apply_request_extra(payload, request_extra)
        logger.info(
            "[%s] chat flags: enable_thinking=%r payload_keys=%s",
            self.id,
            payload.get("enable_thinking"),
            sorted(list(payload.keys())),
        )
        logger.info("[%s] POST /v1/chat/completions model=%r", self.id, model)
        result = await self.chat_completion(
            messages,
            model,
            temperature=temperature,
            max_tokens=max_tokens,
            request_extra=request_extra,
        )
        thinking_requested = is_thinking_requested(request_extra)
        cleaned = result.content
        if not thinking_requested and "<think>" in cleaned.lower():
            return _strip_think_tags(cleaned)
        return cleaned

    async def stream_chat(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        callback: StreamCallback,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        *,
        request_extra: Optional[Dict[str, Any]] = None,
    ) -> str:
        # Auto-continue:
        # 1) finish_reason=length — упёрлись в max_tokens одного ответа
        # 2) презентация: модель часто ставит stop, не дописав N слайдов —
        #    дожимаем, пока class="slide" не достигнет запрошенного числа
        # Лимит продолжений защищает от бесконечного цикла.
        max_continuations = _auto_continue_max()
        requested_slides = _requested_slide_count(messages)
        if requested_slides and requested_slides > 8:
            # На длинных презах 5 продолжений часто мало (тяжёлый HTML).
            max_continuations = max(max_continuations, min(requested_slides, 20))
        base_messages = list(messages)
        thinking_requested = is_thinking_requested(request_extra)

        accumulated = ""
        reasoning_accumulated = ""
        _final_finish_reason: Optional[str] = None

        for _cont_idx in range(max_continuations + 1):
            if _cont_idx == 0:
                pass_messages = base_messages
            else:
                have = _count_html_slides(accumulated)
                is_pres = _looks_like_presentation_html(accumulated)
                # Важно: открываем документ В accumulated до стрима, иначе модель
                # допишет </body></html> / новый fence УЖЕ ПОСЛЕ закрытого ``` → хвост в UI.
                if is_pres:
                    opened = _open_presentation_for_continue(accumulated)
                    if opened != accumulated:
                        accumulated = opened
                        if (
                            _invoke_stream_callback(callback, "", accumulated, "content")
                            is False
                        ):
                            break
                if requested_slides and have < requested_slides and is_pres:
                    continue_hint = (
                        f"В HTML сейчас {have} слайдов с class=\"slide\", нужно ровно {requested_slides}. "
                        f"Допиши ТОЛЬКО следующие элементы <… class=\"slide\"> с номерами {have + 1}–{requested_slides} "
                        "сразу после последнего слайда.\n"
                        "СТРОГО ЗАПРЕЩЕНО: новый блок ``` / ```html, слово html отдельной строкой, "
                        "<!DOCTYPE, <html>, <head>, <body>, пояснения, повтор слайдов.\n"
                        f"Когда будет ровно {requested_slides} слайдов — закрой </body></html> и fence ```."
                    )
                elif is_pres and _presentation_looks_truncated(accumulated):
                    continue_hint = (
                        f"В HTML сейчас {have} слайдов с class=\"slide\", но ответ оборван "
                        "(незакрытый комментарий, mid-tag или незакрытые <div> в последнем слайде).\n"
                        "Допиши ТОЛЬКО недостающие <div class=\"slide\"> сразу после последнего готового слайда, "
                        "затем финальный слайд и закрой </body></html> и fence ```.\n"
                        "СТРОГО ЗАПРЕЩЕНО: новый блок ``` / ```html, слово html отдельной строкой, "
                        "<!DOCTYPE, <html>, <head>, <body>, пояснения, повтор слайдов."
                    )
                else:
                    continue_hint = (
                        "Продолжи ровно с того места, где ответ оборвался. "
                        "Не повторяй уже написанное, не добавляй пояснений — только продолжение. "
                        "Не открывай новый блок ``` / ```html — допиши текущий HTML."
                    )
                pass_messages = base_messages + [
                    {"role": "assistant", "content": accumulated},
                    {"role": "user", "content": continue_hint},
                ]

            _acc_snapshot = accumulated
            _reason_snapshot = reasoning_accumulated
            finish_reason, pass_accumulated, pass_reasoning = await self._stream_chat_once(
                pass_messages,
                model,
                callback,
                temperature=temperature,
                max_tokens=max_tokens,
                request_extra=request_extra,
                _shared=lambda: (_acc_snapshot, _reason_snapshot),
            )
            fr = str(finish_reason or "").lower()

            # Ошибка потока в ПРОДОЛЖЕНИИ: не теряем уже готовые слайды.
            if fr == "error" or (
                isinstance(pass_accumulated, str)
                and pass_accumulated.startswith("Ошибка потока")
            ):
                if _cont_idx > 0 and _acc_snapshot.strip():
                    logger.debug(
                        "[%s] auto-continue: ошибка в pass #%s, оставляем накопленное (%s симв.)",
                        self.id,
                        _cont_idx,
                        len(_acc_snapshot),
                    )
                    accumulated = _acc_snapshot
                    reasoning_accumulated = _reason_snapshot
                    _final_finish_reason = "stop"
                    break
                accumulated = pass_accumulated
                reasoning_accumulated = pass_reasoning
                _final_finish_reason = finish_reason
                break

            accumulated = pass_accumulated
            reasoning_accumulated = pass_reasoning

            # Continue-pass: модель часто начинает новый ```html / <!DOCTYPE> —
            # вырезаем reopen из дельты, иначе UI «троится» и в чат утекает «html».
            if _cont_idx > 0:
                merged = _merge_presentation_continue(_acc_snapshot, accumulated)
                if merged != accumulated:
                    accumulated = merged
                    if (
                        _invoke_stream_callback(callback, "", accumulated, "content")
                        is False
                    ):
                        break

            # Детект обрыва на СЫРОМ тексте. Finalize (закрытый fence) только
            # в конце всех pass'ов — иначе ```html попадает внутрь слайдов.
            trunc_before_finalize = _presentation_looks_truncated(accumulated)
            _final_finish_reason = finish_reason

            # Пользовательский стоп — не крутим continue.
            if fr == "stop_user":
                break

            have_slides = _count_html_slides(accumulated)
            need_more_slides = bool(
                requested_slides
                and have_slides < requested_slides
                and (_looks_like_presentation_html(accumulated) or have_slides > 0)
            )
            need_fix_truncation = bool(
                trunc_before_finalize
                and have_slides > 0
                and not _presentation_looks_finished(accumulated)
            )

            # length после финального слайда без явного N — не дожимать слайды.
            if (
                fr == "length"
                and not need_more_slides
                and not need_fix_truncation
                and not requested_slides
                and _presentation_looks_finished(accumulated)
            ):
                break

            if fr == "length" or need_more_slides or need_fix_truncation:
                if _cont_idx >= max_continuations:
                    logger.debug(
                        "[%s] auto-continue: лимит продолжений (%s), стоп "
                        "(finish=%r slides=%s/%s trunc=%s накоплено=%s)",
                        self.id,
                        max_continuations,
                        fr,
                        have_slides,
                        requested_slides,
                        need_fix_truncation,
                        len(accumulated),
                    )
                    break
                logger.debug(
                    "[%s] auto-continue #%s: finish=%r slides=%s/%s trunc=%s, дожимаем (накоплено %s симв.)",
                    self.id,
                    _cont_idx + 1,
                    fr,
                    have_slides,
                    requested_slides,
                    need_fix_truncation,
                    len(accumulated),
                )
                continue

            break

        # Единая финальная склейка: один ```html, chrome (или fallback), без fence-шума.
        if _looks_like_presentation_html(accumulated):
            finalized = _finalize_presentation_message(accumulated)
            if finalized != accumulated:
                accumulated = finalized
                _invoke_stream_callback(callback, "", accumulated, "content")

        logger.debug(
            "[%s] ИТОГ СТРИМА (all passes): finish_reason=%r slides=%s/%s накоплено=%s симв.",
            self.id,
            _final_finish_reason,
            _count_html_slides(accumulated),
            requested_slides,
            len(accumulated),
        )
        cleaned = clean_llm_response(accumulated)
        if thinking_requested:
            return cleaned
        if "<think>" in cleaned.lower():
            return _strip_think_tags(cleaned)
        if reasoning_accumulated.strip() and "<think>" not in cleaned:
            return f"<think>{reasoning_accumulated.strip()}</think>\n\n{cleaned}"
        return cleaned

    async def _stream_chat_once(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        callback: StreamCallback,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        *,
        request_extra: Optional[Dict[str, Any]] = None,
        _shared=None,
    ):
        """Один проход стрима. Возвращает (finish_reason, accumulated, reasoning).

        accumulated/reasoning инициализируются значениями из _shared() (для
        бесшовного продолжения при auto-continue): callback получает суммарный
        текст всех проходов, а не только текущего.
        """
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        self._apply_request_extra(payload, request_extra)
        thinking_requested = is_thinking_requested(request_extra)
        logger.debug(
            "[%s] stream enable_thinking=%r model=%r url=%s/v1/chat/completions",
            self.id,
            thinking_requested,
            model,
            self.base_url,
        )
        headers = self._headers(accept_sse=True)
        read_s = _stream_read_timeout_sec(self._timeout_read)
        stream_timeout = httpx.Timeout(read_s, connect=10.0, read=read_s, write=10.0)
        # Продолжаем накопление предыдущих проходов (auto-continue), чтобы callback
        # получал суммарный текст, а UI не «моргал» на границе продолжений.
        _seed_acc, _seed_reason = _shared() if _shared else ("", "")
        accumulated = _seed_acc or ""
        reasoning_accumulated = _seed_reason or ""
        logged_delta_shape = False
        # Диагностика обрыва: у цикла ниже пять выходов, и четыре из них
        # сегодня не оставляют в логах ничего. Пишем, какой сработал.
        _t0 = time.monotonic()
        _chunks = 0
        _end_reason = "iterator_end"
        _finish_reason_seen = None
        logger.debug(
            "[%s] POST /v1/chat/completions stream=True model=%r read_timeout=%ss",
            self.id,
            model,
            read_s,
        )
        cef_rid = uuid.uuid4().hex
        log_cef_int003_llm_request(base_url=self.base_url, provider_id=self.id, model=model, request_uuid=cef_rid)
        try:
            async with httpx.AsyncClient(timeout=stream_timeout, verify=self._http_verify()) as client:
                async with client.stream(
                    "POST", f"{self.base_url}/v1/chat/completions", headers=headers, json=payload
                ) as response:
                    if response.status_code >= 400:
                        # Тело потокового ответа не прочитано, и .text в обработчике
                        # ошибки бросил бы ResponseNotRead — причина отказа шлюза
                        # (в т.ч. guardrail) терялась вместе с ней. Читаем здесь,
                        # пока поток открыт.
                        try:
                            await response.aread()
                        except Exception:
                            logger.debug(
                                "[%s] тело ошибки потока прочитать не удалось", self.id
                            )
                    response.raise_for_status()
                    line_iter = response.aiter_lines().__aiter__()
                    while True:
                        # Idle-wait по 1с: стоп пользователя не ждёт следующего токена
                        # и не упирается в длинный read-timeout.
                        try:
                            line = await asyncio.wait_for(line_iter.__anext__(), timeout=1.0)
                        except StopAsyncIteration:
                            break
                        except asyncio.TimeoutError:
                            if (
                                _invoke_stream_callback(
                                    callback, "", accumulated, "heartbeat"
                                )
                                is False
                            ):
                                logger.debug(
                                    "[%s] поток прерван callback'ом (heartbeat/stop)",
                                    self.id,
                                )
                                _end_reason = "callback_stop"
                                return ("stop_user", accumulated, reasoning_accumulated)
                            continue
                        if not line or not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            _end_reason = "done"
                            break
                        try:
                            data = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        choices = data.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        if not logged_delta_shape:
                            logged_delta_shape = True
                            logger.debug(
                                "[%s] first delta keys=%s has_reasoning_fields=%s has_content_fields=%s",
                                self.id,
                                sorted(list(delta.keys())),
                                any(
                                    (
                                        k in delta
                                        for k in (
                                            "reasoning_content",
                                            "reasoning",
                                            "reasoning_text",
                                            "thinking",
                                            "thought",
                                        )
                                    )
                                ),
                                any((k in delta for k in ("content", "text", "output_text", "message"))),
                            )
                        reasoning_source = delta.get("reasoning_content")
                        if reasoning_source is None:
                            reasoning_source = delta.get("reasoning")
                        if reasoning_source is None:
                            reasoning_source = delta.get("reasoning_text")
                        if reasoning_source is None:
                            reasoning_source = delta.get("thinking")
                        if reasoning_source is None:
                            reasoning_source = delta.get("thought")
                        reasoning_chunk = _normalize_reasoning_payload(reasoning_source)
                        if thinking_requested and reasoning_chunk:
                            reasoning_accumulated += reasoning_chunk
                            if (
                                _invoke_stream_callback(callback, reasoning_chunk, reasoning_accumulated, "reasoning")
                                is False
                            ):
                                logger.debug("[%s] поток прерван callback'ом (reasoning)", self.id)
                                return ("stop_user", accumulated, reasoning_accumulated)
                        chunk = _normalize_content_payload(
                            delta.get("content")
                            or delta.get("text")
                            or delta.get("output_text")
                            or delta.get("message")
                        )
                        if chunk:
                            accumulated += chunk
                            _chunks += 1
                            if "<|im_start|>" in accumulated or "<|im_end|>" in accumulated:
                                logger.debug(
                                    "[%s] СТРИМ ОБОРВАН callback'ом: чанков=%s накоплено=%s симв. за %.1f c",
                                    self.id,
                                    _chunks,
                                    len(accumulated),
                                    time.monotonic() - _t0,
                                ) 
                                _end_reason = "chat_template_tag"
                                return ("stop", accumulated, reasoning_accumulated)
                            # Отдаём контентный чанк наружу — это и есть стрим на фронт.
                            # callback получает суммарный accumulated (в т.ч. хвост
                            # предыдущих проходов auto-continue), поэтому UI видит
                            # непрерывный текст. Отказ callback'а прерывает поток.
                            if (
                                _invoke_stream_callback(callback, chunk, accumulated, "content")
                                is False
                            ):
                                logger.debug("[%s] поток прерван callback'ом (content)", self.id)
                                _end_reason = "callback_stop"
                                return ("stop_user", accumulated, reasoning_accumulated)
                        # finish_reason (в т.ч. length = лимит токенов) — конец стрима.
                        # Часть провайдеров не присылает [DONE] после этого и httpx ждёт
                        # read-timeout минутами, а UI остаётся в «генерации».
                        finish_reason = choices[0].get("finish_reason") or choices[0].get("stop_reason")
                        if finish_reason:
                            _finish_reason_seen = str(finish_reason)
                            _end_reason = "finish_reason"
                            logger.debug(
                                "[%s] stream finish_reason=%r model=%r (накоплено %s симв.)",
                                self.id,
                                finish_reason,
                                model,
                                len(accumulated),
                            )
                            break
        except httpx.HTTPStatusError as e:
            stream_body = _safe_response_text(e.response)
            logger.error("[%s] stream HTTP %s: %s", self.id, e.response.status_code, e)
            log_cef_int006_llm_api_failure(
                request_uuid=cef_rid,
                code_status=str(e.response.status_code),
                text_status=stream_body[:512],
                service_name=f"openai-compat-{self.id}",
                status_code=e.response.status_code,
            )
            if e.response.status_code == 503:
                detail = ""
                try:
                    detail = str((e.response.json() or {}).get("detail", ""))
                except Exception:
                    logger.debug("[%s] 503: тело не разобрано как JSON", self.id)
                    detail = stream_body[:500]
                low = detail.lower()
                if "not loaded" in low or "не загруж" in low:
                    return (
                        "stop",
                        "Модель не загружена в LLM-бэкенде (503). Проверьте, что модель активна на стороне провайдера.",
                        "",
                    )
                return ("stop", "Сервис LLM недоступен (503). Повторите запрос через несколько секунд.", "")
            return ("stop", f"Ошибка потока: {e}", "")
        except Exception as e:
            logger.exception("[%s] stream error", self.id)
            log_cef_int006_llm_api_failure(
                request_uuid=cef_rid,
                code_status="EXCEPTION",
                text_status=str(e)[:512],
                service_name=f"openai-compat-{self.id}",
                status_code=None,
            )
            return ("stop", f"Ошибка потока: {e}", "")
        cleaned = clean_llm_response(accumulated)
        # Итог одной строкой. Ключевое - сравнить накоплено и после_очистки:
        # если второе заметно меньше, текст режет наша постобработка, а не
        # модель (незакрытый <think> вырезает всё до конца текста).
        logger.debug(
            "[%s] ИТОГ ПРОХОДА СТРИМА: конец=%s finish_reason=%r чанков=%s "
            "накоплено=%s симв. после_очистки=%s симв. think=%s "
            "reasoning=%s симв. max_tokens=%s за %.1f c",
            self.id,
            _end_reason,
            _finish_reason_seen,
            _chunks,
            len(accumulated),
            len(cleaned),
            "<think>" in cleaned.lower(),
            len(reasoning_accumulated),
            max_tokens,
            time.monotonic() - _t0,
        )
        # Постобработка (<think>, очистка) и решение о продолжении — в stream_chat.
        return (_finish_reason_seen, accumulated, reasoning_accumulated)
