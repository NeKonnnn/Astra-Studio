"""
Централизованный вызов LLM (OpenAI-совместимый /v1/chat/completions) для RAG

Используется двумя LLM-функциями RAG:
  • LLM-as-a-Judge      (app/services/llm_rag_judge.py)
  • иерархическая суммаризация (app/services/rag_service.py -> _llm_summarize)

Поведение запроса 1:1 с прежним (model/temperature/max_tokens/stream=false, без
авторизации) - добавлена только подробная трассировка (см. app.core.logging.llm_trace),
чтобы по логам было видно, КУДА и С ЧЕМ стучится RAG.
"""

from __future__ import annotations

import os
import time
from typing import Optional

import httpx

from app.core.config import get_settings
from app.core.http_verify import resolve_httpx_verify
from app.core.logging import (
    get_logger,
    log_llm_config,
    log_llm_failure,
    log_llm_request,
    log_llm_response,
    new_request_id,
)

logger = get_logger(__name__)

# Конфиг LLM логируем один раз (чтобы не дублировать на каждом вызове).
_config_logged = False


def _auth_present(cfg) -> bool:
    """
    Есть ли у RAG API-ключ для LLM.
    Сейчас RAG авторизацию НЕ шлёт — это хук на будущее: если в LLMServiceConfig
    появится api_key_env, мы прочитаем ключ из окружения.
    """
    api_key_env = getattr(cfg, "api_key_env", None)
    if api_key_env:
        return bool(os.environ.get(api_key_env))
    return False


async def chat(
    prompt: str,
    *,
    purpose: str,
    temperature: float,
    max_tokens: int,
    timeout: Optional[float] = None,
) -> str:
    """
    Один вызов chat/completions. Логирует запрос/ответ/ошибку.
    Возвращает content ответа. При сетевом/HTTP-сбое — пробрасывает исключение
    (вызывающий код решает, что делать: judge -> вернуть ошибку, summary -> fallback).
    """
    global _config_logged

    cfg = get_settings().backend
    base_url = (cfg.base_url or "").rstrip("/")
    url = f"{base_url}/api/internal/rag/llm" if base_url else "/api/internal/rag/llm"
    model = "(backend-selected)"
    req_timeout = timeout if timeout is not None else cfg.timeout
    auth = _auth_present(cfg)

    if not _config_logged:
        log_llm_config(
            logger,
            base_url=base_url,
            model=model,
            timeout=cfg.timeout,
            auth_present=auth,
        )
        _config_logged = True

    rid = new_request_id()
    payload = {
        "prompt": prompt,
        "purpose": purpose,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    try:
        from app.core.cef_logger.cef_audit_context import chat_model_peek

        model_path = chat_model_peek()
        if model_path:
            payload["model_path"] = model_path
            model = model_path
    except Exception:
        pass

    # Что именно отправляем (без самого текста промпта — он может быть огромным;
    # полный промпт — на DEBUG ниже).
    params = {
        "messages": 1,
        "prompt_chars": len(prompt or ""),
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    log_llm_request(
        logger,
        request_id=rid,
        purpose=purpose,
        url=url,
        model=model,
        timeout=req_timeout,
        params=params,
        auth_present=auth,
    )
    # Полный промпт — только на DEBUG (APP_LOG_LEVEL=DEBUG), чтобы при разборе
    # можно было увидеть, что именно ушло в модель.
    logger.debug("[LLM→] rid=%s purpose=%s prompt=%r", rid, purpose, prompt)

    start = time.monotonic()
    try:
        from app.core.cef_logger.cef_audit_context import cef_identity_headers

        async with httpx.AsyncClient(
            timeout=req_timeout, verify=resolve_httpx_verify()
        ) as client:
            resp = await client.post(url, headers=cef_identity_headers(), json=payload)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as e:
        latency = time.monotonic() - start
        body = ""
        try:
            body = (e.response.text or "")[:500]
        except Exception:
            body = ""
        log_llm_failure(
            logger,
            request_id=rid,
            purpose=purpose,
            url=url,
            latency_s=latency,
            status=e.response.status_code,
            error_type=type(e).__name__,
            reason=str(e),
            body=body,
        )
        raise
    except Exception as e:
        latency = time.monotonic() - start
        log_llm_failure(
            logger,
            request_id=rid,
            purpose=purpose,
            url=url,
            latency_s=latency,
            status=None,
            error_type=type(e).__name__,
            reason=str(e),
            body=None,
        )
        raise

    latency = time.monotonic() - start
    content = data.get("content", "") or ""
    finish = data.get("finish_reason")
    choices = [content] if content else []
    log_llm_response(
        logger,
        request_id=rid,
        purpose=purpose,
        status=200,
        latency_s=latency,
        content_chars=len(content),
        choices=len(choices),
        finish_reason=finish,
    )
    logger.debug("[LLM←] rid=%s purpose=%s content=%r", rid, purpose, content)
    return content
