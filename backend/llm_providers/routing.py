"""Маршрутизация LLM: ProviderRegistry vs legacy llm-svc (B-27)."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

import httpx

from backend.settings.logging import get_logger

logger = get_logger(__name__)


def should_use_llm_svc_direct(
    *, model_path: Optional[str] = None, images: Optional[List[Any]] = None
) -> bool:
    """
    True → ``ask_agent_llm_svc`` / ``generate_response`` (legacy llm-svc HTTP, vision).

    - Изображения: только llm-svc path (vision в generate_response).
    - ``llm-svc://…``: legacy URI — полная логика host/swap в llm_client.
    """
    if images:
        return True
    raw = str(model_path or "").strip().lower()
    if raw.startswith("llm-svc://"):
        return True
    return False


def registry_response_usable(response: Optional[str]) -> bool:
    if response is None:
        return False
    return bool(str(response).strip())


# Тексты, которые ask_agent и format_llm_http_error возвращают ВМЕСТО ответа
# модели — с точки зрения вызывающего кода это обычная строка, исключения нет.
# Для чата так и надо: пользователю показывают понятную причину. Но для служебных
# вызовов (суммаризация при индексации, HyDE, multi-query, исправление опечаток)
# такая строка — яд: 01.08 она попала и в поисковый запрос, и в индекс как
# содержимое чанков («Ошибка LLM (HTTP 422)» вместо summary документа).
_LLM_ERROR_MARKERS = (
    "ошибка llm (http",
    "не удалось получить ответ от модели",
    "ошибка при обращении к модели",
    "сервис llm недоступен",
    "модель не загружена на стороне провайдера",
    "запрос не помещается в контекстное окно модели",
)


def _env_flag(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw not in ("0", "false", "no", "off")


def service_thinking_disabled() -> bool:
    """Отключать ли мышление для служебных вызовов. LLM_SERVICE_DISABLE_THINKING=0 — не отключать."""
    return _env_flag("LLM_SERVICE_DISABLE_THINKING", True)


def service_min_max_tokens() -> int:
    """Нижняя граница max_tokens для служебных вызовов. LLM_SERVICE_MIN_MAX_TOKENS.

    Страховка на случай, если шлюз не понимает выключатель мышления: с
    reasoning-моделью 256 токенов уходят на рассуждение целиком, и ответа
    не остаётся.
    """
    raw = (os.getenv("LLM_SERVICE_MIN_MAX_TOKENS") or "").strip()
    try:
        return max(0, min(int(raw), 32000)) if raw else 1024
    except ValueError:
        return 1024


# Шлюз bifrost (CORSUR) пропускает до модели только поля OpenAI-схемы, а всё
# остальное молча отбрасывает — поэтому chat_template_kwargs до vLLM не доезжал
# и мышление не выключалось (02.08: «модель вернула только рассуждение»).
# Проброс включается заголовком; тот же приём уже работает для эмбеддингов и
# реранка в svc-rag (x-bf-passthrough-extra-params).
_PASSTHROUGH_HEADER = "x-bf-passthrough-extra-params"

# Ключи payload, ради которых нужен проброс. Заголовок шлём только когда они
# реально есть в теле: обычному чат-запросу лишний заголовок ни к чему.
# top_k / min_p / repetition_penalty — расширения vLLM, не поля OpenAI-схемы:
# без заголовка шлюз выбросит их так же, как выбрасывал chat_template_kwargs.
_EXTRA_PARAM_KEYS = ("chat_template_kwargs", "top_k", "min_p", "repetition_penalty")


def passthrough_extra_params_enabled() -> bool:
    """Слать ли заголовок проброса нестандартных полей.

    ``LLM_PASSTHROUGH_EXTRA_PARAMS=0`` — не слать (если шлюз отвечает на
    заголовок 4xx; тогда остаются /no_think и страховка по max_tokens).
    """
    return _env_flag("LLM_PASSTHROUGH_EXTRA_PARAMS", True)


def extra_params_headers(payload: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """Заголовки проброса для конкретного тела запроса (пусто, если не нужны)."""
    if not payload or not passthrough_extra_params_enabled():
        return {}
    if not any(key in payload for key in _EXTRA_PARAM_KEYS):
        return {}
    return {_PASSTHROUGH_HEADER: "true"}


# Поля выборки: ключ — имя настройки в llm_settings.json и в карточке агента,
# значение — имя поля в теле запроса. top_p / frequency_penalty /
# presence_penalty входят в OpenAI-схему, шлюз передаёт их сам. Остальные —
# расширения vLLM, их вывозит заголовок проброса (см. _EXTRA_PARAM_KEYS).
# repeat_penalty — имя из llama.cpp; тот же параметр у vLLM называется
# repetition_penalty, поэтому здесь переименование, а не опечатка.
_SAMPLING_FIELDS: Dict[str, str] = {
    "top_p": "top_p",
    "frequency_penalty": "frequency_penalty",
    "presence_penalty": "presence_penalty",
    "top_k": "top_k",
    "min_p": "min_p",
    "repeat_penalty": "repetition_penalty",
}


def sampling_enabled() -> bool:
    """Слать ли поля выборки вообще. ``LLM_SAMPLING_ENABLED=0`` — не слать.

    Аварийный рубильник: пер-агентные настройки и доставка полей до модели едут
    одним коммитом, и если шлюз или модель поведут себя неожиданно, выключать
    надо только вторую половину. Правка ConfigMap вместо отката кода, вместе с
    которым уехали бы и настройки, и фронт.
    """
    return _env_flag("LLM_SAMPLING_ENABLED", True)


def sampling_send_all() -> bool:
    """Слать ли поля выборки, равные дефолтам кластера.

    По умолчанию НЕТ. До сих пор эти значения не доезжали до модели вообще, и
    отправка их всех разом поменяла бы ответы у всех сразу: у vLLM свои дефолты
    (``top_k`` отключён, ``min_p`` 0, ``repetition_penalty`` 1), а в
    ``llm_settings.json`` лежат 40 / 0.05 / 1.05. Поэтому шлём только то, что
    человек осознанно изменил относительно дефолтов кластера.

    ``LLM_SAMPLING_SEND_ALL=1`` — слать всё, что показано в интерфейсе.
    """
    return _env_flag("LLM_SAMPLING_SEND_ALL", False)


def _same_number(left: Any, right: Any) -> bool:
    """Сравнение значения настройки с дефолтом (0.95 из JSON и из кода — одно и то же)."""
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left is right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return abs(float(left) - float(right)) < 1e-9
    return left == right


def sampling_request_extra() -> Dict[str, Any]:
    """Поля выборки из АКТИВНЫХ настроек генерации — итога «кластер → пользователь → агент».

    Активные настройки биндит обработчик чата (см.
    ``user_llm_settings.bind_user_model_runtime``), поэтому здесь достаточно их
    прочитать. Пустой словарь — значит слать нечего и заголовок проброса не
    понадобится.
    """
    if not sampling_enabled():
        return {}
    try:
        from backend.services.user_llm_settings import get_active_model_settings
        from backend.utils.llm_defaults import llm_settings_defaults

        active = get_active_model_settings() or {}
        defaults = llm_settings_defaults() or {}
    except Exception:
        logger.exception("sampling_request_extra: настройки генерации недоступны")
        return {}

    send_all = sampling_send_all()
    out: Dict[str, Any] = {}
    for key, field in _SAMPLING_FIELDS.items():
        value = active.get(key)
        if value is None:
            continue
        if not send_all and _same_number(value, defaults.get(key)):
            continue
        out[field] = value
    return out


def merge_sampling_request_extra(
    request_extra: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Добавить поля выборки к уже собранному request_extra, ничего в нём не затирая."""
    sampling = sampling_request_extra()
    if not sampling:
        return request_extra
    merged = dict(request_extra or {})
    for key, value in sampling.items():
        merged.setdefault(key, value)
    return merged


_NO_THINK_TAG = "/no_think"


def service_no_think_tag_enabled() -> bool:
    """Дописывать ли ``/no_think`` в промпт служебных вызовов.

    ``LLM_SERVICE_NO_THINK_TAG=0`` — не дописывать (если модель не Qwen3 и тег
    попадает в ответ как обычный текст).
    """
    return _env_flag("LLM_SERVICE_NO_THINK_TAG", True)


def apply_no_think_tag(
    messages: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Мягкий выключатель мышления Qwen3: тег в тексте последнего user-сообщения.

    В отличие от ``chat_template_kwargs`` тег едет в ``messages`` — поле, которое
    шлюз обязан передать дословно. Это единственный способ, не зависящий от
    того, пробрасывает ли шлюз нестандартные параметры.

    Возвращает НОВЫЙ список: вызывающий код может держать исходные сообщения.
    """
    if not messages:
        return messages
    out = list(messages)
    for idx in range(len(out) - 1, -1, -1):
        entry = out[idx]
        if not isinstance(entry, dict) or entry.get("role") != "user":
            continue
        content = entry.get("content")
        if not isinstance(content, str):
            # Мультимодальный content (список частей) не трогаем: у служебных
            # вызовов его не бывает, а гадать про формат частей незачем.
            return out
        if _NO_THINK_TAG in content:
            return out
        out[idx] = {**entry, "content": f"{content}\n\n{_NO_THINK_TAG}"}
        return out
    return out


def thinking_request_extra(enable_thinking: bool) -> Dict[str, Any]:
    """Поля запроса для включения/выключения мышления у reasoning-моделей.

    UI «Быстрый» → False, «Мышление» → True. Способ через
    ``chat_template_kwargs`` документирован vLLM для Qwen3.
    Верхнеуровневый ``enable_thinking`` НЕ шлём: нестандартное поле некоторые
    шлюзы отвергают с 422.
    """
    return {"chat_template_kwargs": {"enable_thinking": bool(enable_thinking)}}


def is_thinking_requested(request_extra: Optional[Dict[str, Any]]) -> bool:
    """True, если в request_extra явно включено мышление."""
    if not request_extra:
        return False
    if "enable_thinking" in request_extra:
        return bool(request_extra.get("enable_thinking"))
    ctk = request_extra.get("chat_template_kwargs")
    if isinstance(ctk, dict) and "enable_thinking" in ctk:
        return bool(ctk.get("enable_thinking"))
    return False


def service_call_request_extra() -> Dict[str, Any]:
    """Поля запроса, выключающие мышление у reasoning-моделей.

    Служебным вызовам (исправление опечаток, multi-query, HyDE, суммаризация
    при индексации, judge) рассуждение не нужно: их ответ читает код, а не
    человек. Хуже того, оно вредит — модель тратит бюджет токенов на
    рассуждение, до ответа не доходит, и вызывающий получает пустую строку
    (01.08: так подменился поисковый запрос).

    Если шлюз не понимает и это — ``LLM_SERVICE_DISABLE_THINKING=0``,
    и останется только страховка по ``max_tokens``.
    """
    if not service_thinking_disabled():
        return {}
    return thinking_request_extra(False)


def is_llm_error_text(text: Optional[str]) -> bool:
    """Строка — это сообщение об ошибке LLM, а не ответ модели.

    Служебные вызовы обязаны проверять результат этой функцией и вести себя как
    при сбое: пропускать шаг, а не подставлять текст ошибки в запрос или индекс.
    """
    s = str(text or "").strip().lower()
    if not s:
        return False
    return any(marker in s[:200] for marker in _LLM_ERROR_MARKERS)


def format_llm_http_error(exc: httpx.HTTPStatusError) -> str:
    """Человекочитаемая ошибка LLM API (контекст, 503, пр.)."""
    status = exc.response.status_code
    body = ""
    detail = ""
    try:
        body = (exc.response.text or "")[:800]
        payload = exc.response.json()
        if isinstance(payload, dict):
            detail = str(
                payload.get("detail")
                or payload.get("message")
                or payload.get("error")
                or ""
            )
    except Exception:
        detail = body[:300]

    haystack = f"{detail} {body}".lower()
    if status == 400 and any(
        token in haystack
        for token in (
            "context",
            "token",
            "length",
            "maximum",
            "max_model",
            "prompt is too long",
            "too long",
        )
    ):
        return (
            "Запрос не помещается в контекстное окно модели: сумма входного текста и max_tokens "
            "(output_tokens) превышает лимит. Уменьшите файл, сократите max_tokens или историю чата."
            + (f" ({detail[:240]})" if detail else "")
        )
    if status == 503:
        if "not loaded" in haystack or "не загруж" in haystack:
            return "Модель не загружена на стороне провайдера (503). Проверьте доступность модели."
        return "Сервис LLM недоступен (503). Повторите запрос через несколько секунд."
    if detail:
        return f"Ошибка LLM (HTTP {status}): {detail[:400]}"
    try:
        return f"Ошибка LLM (HTTP {status}): {json.dumps(exc.response.json(), ensure_ascii=False)[:400]}"
    except Exception:
        return f"Ошибка LLM (HTTP {status}): {body[:400] or exc}"


def build_chat_messages(
    prompt: str,
    *,
    history: Optional[List[Dict[str, Any]]] = None,
    system_prompt: Optional[str] = None,
) -> List[Dict[str, Any]]:
    messages: List[Dict[str, Any]] = []
    if system_prompt and str(system_prompt).strip():
        messages.append({"role": "system", "content": str(system_prompt).strip()})
    for entry in history or []:
        role = str(entry.get("role") or "").strip()
        content = entry.get("content")
        if role in ("user", "assistant", "system") and content is not None:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": prompt})
    return messages


async def describe_llm_routes() -> dict:
    """Диагностика: какие провайдеры в registry (для /api/system/status)."""
    try:
        from backend.llm_providers import get_registry

        registry = await get_registry()
        providers = []
        for p in registry.all():
            providers.append(
                {
                    "id": p.id,
                    "kind": p.kind,
                    "enabled": p.enabled,
                    "function_calling": p.supports_native_function_calling(),
                    "mcp_mode": p.mcp_tool_calling_mode(),
                }
            )
        return {
            "default_provider": registry.default_id,
            "providers": providers,
            "legacy_llm_svc_direct": "llm-svc:// URIs and vision (images) use ask_agent_llm_svc",
            "mcp_agent_loop": "always ProviderRegistry",
        }
    except Exception as exc:
        logger.exception("Ошибка операции")
        return {"error": str(exc)}
