"""
Подробная трассировка обращений RAG к LLM (chat/completions).
Зачем: в контуре банка нет доступа к curl — единственный способ убедиться, что
LLM-функционал работает, это читать логи. Поэтому каждый вызов LLM логируется так,
чтобы по логам было видно:
  • КУДА стучимся   — полный URL (base_url + /v1/chat/completions);
  • С ЧЕМ           — модель, таймаут, размеры payload, наличие авторизации;
  • СКОЛЬКО          — latency;
  • ЧТО вернулось    — HTTP-статус, длина ответа, finish_reason, тело при ошибке.
Формат строк единый и грепабельный: префиксы [LLM→] / [LLM←] / [LLM✗] / [LLM cfg].
"""

from __future__ import annotations
import logging
import uuid
from typing import Any, Dict, Optional

# Сколько символов тела ответа показывать при ошибке (не льём мегабайты в лог).
_MAX_BODY_PREVIEW = 500


def new_request_id() -> str:
    """Короткий id для связки строк [LLM→]/[LLM←]/[LLM✗] одного вызова."""
    return uuid.uuid4().hex[:8]


def log_llm_config(
    logger: logging.Logger,
    *,
    base_url: str,
    model: str,
    timeout: float,
    auth_present: bool,
) -> None:
    """Один раз при первом вызове: какой LLM-эндпоинт RAG считает целевым."""
    logger.info(
        "[LLM cfg] base_url=%r model=%r timeout=%ss auth=%s",
        base_url,
        model,
        timeout,
        "YES" if auth_present else "NO",
    )
    if not base_url:
        logger.warning(
            "[LLM cfg] base_url ПУСТОЙ — запросы уйдут в '/v1/chat/completions' без хоста. "
            "Проверьте config.yml: llm_service.base_url или блок urls (llm_service_)."
        )


def log_llm_request(
    logger: logging.Logger,
    *,
    request_id: str,
    purpose: str,
    url: str,
    model: str,
    timeout: float,
    params: Dict[str, Any],
    auth_present: bool,
) -> None:
    """Строка ПЕРЕД отправкой запроса: куда и с чем стучимся."""
    if not url or url.startswith("/"):
        logger.error(
            "[LLM→] rid=%s purpose=%s ОШИБКА КОНФИГА: base_url пуст, запрос некуда отправить (url=%r)",
            request_id,
            purpose,
            url,
        )
    logger.debug(
        "[LLM→] rid=%s purpose=%s POST %s model=%r timeout=%ss auth=%s params=%s",
        request_id,
        purpose,
        url or "<EMPTY base_url>/v1/chat/completions",
        model,
        timeout,
        "YES" if auth_present else "NO",
        params,
    )


def log_llm_response(
    logger: logging.Logger,
    *,
    request_id: str,
    purpose: str,
    status: int,
    latency_s: float,
    content_chars: int,
    choices: int,
    finish_reason: Optional[str] = None,
) -> None:
    """Строка ПОСЛЕ успешного ответа: статус, тайминг, что пришло."""
    logger.debug(
        "[LLM←] rid=%s purpose=%s status=%s latency=%.3fs content_chars=%s choices=%s finish=%s",
        request_id,
        purpose,
        status,
        latency_s,
        content_chars,
        choices,
        finish_reason,
    )
    if content_chars == 0:
        logger.warning(
            "[LLM←] rid=%s purpose=%s модель вернула ПУСТОЙ content (choices=%s) — проверьте модель/лимиты",
            request_id,
            purpose,
            choices,
        )


def log_llm_failure(
    logger: logging.Logger,
    *,
    request_id: str,
    purpose: str,
    url: str,
    latency_s: float,
    status: Optional[int] = None,
    error_type: Optional[str] = None,
    reason: Optional[str] = None,
    body: Optional[str] = None,
) -> None:
    """Строка при сбое: статус (если HTTP), тип ошибки, кусок тела ответа."""
    body_preview = (body or "")[:_MAX_BODY_PREVIEW]
    logger.error(
        "[LLM✗] rid=%s purpose=%s url=%s status=%s latency=%.3fs error=%s reason=%r body=%r",
        request_id,
        purpose,
        url,
        status,
        latency_s,
        error_type,
        reason,
        body_preview,
    )
    if status in (401, 403):
        logger.error(
            "[LLM✗] rid=%s purpose=%s ПОХОЖЕ НА ОТКАЗ АВТОРИЗАЦИИ (%s) — эндпоинту нужен API-ключ, "
            "а RAG его сейчас не шлёт (auth не реализован).",
            request_id,
            purpose,
            status,
        )
