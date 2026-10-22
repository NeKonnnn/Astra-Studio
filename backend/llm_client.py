"""
LLM Client для взаимодействия с микросервисами AI (LLM, STT, TTS, OCR, Diarization)
"""

import asyncio
import contextvars
import html as html_module
import io
import json
import os
import re
import uuid
import base64
from PIL import Image
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx

from backend.settings import get_settings
from backend.settings.cef_logger.cef_logger import (
    log_cef_int003_llm_request,
    log_cef_int006_llm_api_failure,
)
from backend.settings.logging import get_logger
from backend.settings.service_toggles import require_service

logger = get_logger(__name__)

# Тексты для пользователя при сбое вызова LLM: подробности исключения только в логе,
# наружу (в «ответ» модели) их не отдаём.
LLM_GENERIC_ERROR_TEXT = "Извините, произошла ошибка при обращении к модели. Повторите запрос позже."
LLM_STREAM_ERROR_TEXT = "Ошибка потока: соединение с LLM прервано"


def _invoke_stream_callback_safe(
    cb: Callable[..., Any], chunk: str, acc: str, stream_role: str = "content"
) -> bool:
    try:
        result = cb(chunk, acc, stream_role)
    except TypeError:
        if stream_role == "heartbeat":
            # 2-arg колбэки не умеют heartbeat — не шлём пустой chunk в UI.
            return True
        result = cb(chunk, acc)
    return result is not False


def resolve_llm_svc_model_id_for_request(
    model_path: Optional[str], fallback_model_name: str
) -> str:
    """
    Получает <id> для JSON model в /v1/chat/completions и для POST /v1/models/load.
    - llm-svc://<id> (и опечатка 1lm-svc://) → <id>
    - models/<name> или путь к .gguf → имя файла без .gguf (как в GET /v1/models)
    - иначе → fallback (имя загруженной в llm-svc модели из health)
    """
    if not model_path or not str(model_path).strip():
        return fallback_model_name
    s = re.sub("\\s+", "", str(model_path).strip())
    low = s.lower()
    if low.startswith("1lm-svc://"):
        s = "llm-svc://" + s[10:]
        low = s.lower()
    if low.startswith("llm-svc://"):
        return s[10:]
    norm = s.replace("\\", "/")
    base = os.path.basename(norm.rstrip("/"))
    if base and base not in (".", ".."):
        if base.lower().endswith(".gguf"):
            base = base[:-5]
        looks_like_filesystem_path = (
            low.startswith("models/")
            or "/models/" in low
            or low.endswith(".gguf")
            or ("/" in norm)
        )
        if looks_like_filesystem_path:
            return base
    return fallback_model_name


def same_llm_svc_model_id(loaded: str, requested: str) -> bool:
    """
    Совпадение «та же модель» для llm-svc: в /v1/health часто короткое имя из конфига,
    а в агенте - полное имя файла без .gguf
    """
    if not loaded or not requested:
        return False
    a, b = (loaded.strip().lower(), requested.strip().lower())
    if a == b:
        return True
    if a.startswith(b + "-") or b.startswith(a + "-"):
        return True
    return False


def pool_contains_model(
    health: Optional[Dict[str, Any]], model_id: Optional[str]
) -> bool:
    """
    True, если model_id уже в RAMе llm-svc
    """
    if not health or not model_id or (not str(model_id).strip()):
        return False
    mid = str(model_id).strip()
    loaded = health.get("loaded_models")
    if isinstance(loaded, list) and len(loaded) > 0:
        for lid in loaded:
            if lid and same_llm_svc_model_id(str(lid), mid):
                return True
        return False
    if health.get("model_loaded") and health.get("model_name"):
        return same_llm_svc_model_id(str(health["model_name"]), mid)
    return False


def infer_llm_host_for_openai_model_id(
    model_id: str, llm_hosts: Dict[str, str], default_host_id: str
) -> Tuple[str, str]:
    """
    Выбор инстанса llm-svc по id модели, когда нет полного пути llm-svc://<host_id>/...
    (полный путь разбирается в resolve_llm_host_and_model_for_svc)
    """
    mid = (model_id or "").strip()
    if not mid:
        return (default_host_id, mid)
    return (default_host_id, mid)


def resolve_llm_host_and_model_for_svc(
    model_ref: Optional[str],
    fallback_model: str,
    llm_hosts: Dict[str, str],
    default_host_id: Optional[str],
) -> Tuple[str, str]:
    """
    Маршрутизация на инстанс llm-svc (или совместимый OpenAI API)
    Returns:
        (host_id, model_id) — model_id только для поля JSON model (без host/)
    Пути:
        - llm-svc://<host_id>/<model_id> при известном host_id в llm_hosts
        - llm-svc://<model_id> — хост по умолчанию
        - иначе — как resolve_llm_svc_model_id_for_request, хост по умолчанию
    """
    if not llm_hosts:
        fb = (fallback_model or "").strip() or "qwen-coder-30b"
        mid = resolve_llm_svc_model_id_for_request(model_ref, fb)
        return ("default", mid)
    keys = list(llm_hosts.keys())
    default_h = default_host_id if default_host_id in llm_hosts else keys[0]
    fb = (fallback_model or "").strip() or "qwen-coder-30b"
    if not model_ref or not str(model_ref).strip():
        return infer_llm_host_for_openai_model_id(fb, llm_hosts, default_h)
    s = re.sub("\\s+", "", str(model_ref).strip())
    low = s.lower()
    if low.startswith("1lm-svc://"):
        s = "llm-svc://" + s[10:]
        low = s.lower()
    if low.startswith("llm-svc://"):
        rest = s[10:].strip()
        if not rest:
            return infer_llm_host_for_openai_model_id(fb, llm_hosts, default_h)
        if "/" in rest:
            hid, tail = rest.split("/", 1)
            tail = (tail or "").strip()
            if hid in llm_hosts and tail:
                return (hid, tail)
            return infer_llm_host_for_openai_model_id(rest, llm_hosts, default_h)
        return infer_llm_host_for_openai_model_id(rest, llm_hosts, default_h)
    if low in ("llm-svc", "llm-svc://"):
        return infer_llm_host_for_openai_model_id(fb, llm_hosts, default_h)
    mid = resolve_llm_svc_model_id_for_request(model_ref, fb)
    return infer_llm_host_for_openai_model_id(mid, llm_hosts, default_h)


# Компилируется один раз
_CHAT_TEMPLATE_RE = re.compile(r"<\|im_(?:start|end)\|>.*", re.DOTALL)


def _clean_llm_response(text: str) -> str:
    """Очистка ответа LLM от артефактов chat template (im_start/im_end теги, HTML entities)."""
    if not text:
        return text
    for _ in range(3):
        new_text = html_module.unescape(text)
        if new_text == text:
            break
        text = new_text
    return _CHAT_TEMPLATE_RE.sub("", text).rstrip()


def _strip_think_tags(text: str) -> str:
    """Удаляет блоки <think>...</think> и незакрытые <think>... из текста.

    Также вырезает «сиротский» закрывающий тег без открывающего
    (Qwen3.5 / GLM в режиме enable_thinking=False часто пишут reasoning…</think>ответ).

    Используется в режиме быстрого ответа (thinking_requested=False), чтобы
    рассуждения модели не попадали в финальный ответ пользователю.
    """
    if not text:
        return text
    lower = text.lower()
    open_l = "<think>".lower()
    close_l = "</think>".lower()
    if open_l not in lower and close_l not in lower:
        return text
    if open_l in lower:
        text = re.sub(
            re.escape("<think>") + r"[\s\S]*?" + re.escape("</think>"),
            "",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(
            re.escape("<think>") + r"[\s\S]*$",
            "",
            text,
            flags=re.IGNORECASE,
        )
    # Orphan closer: всё до первого </think> включительно — рассуждение.
    if close_l in text.lower():
        text = re.sub(
            r"^[\s\S]*?" + re.escape("</think>"),
            "",
            text,
            count=1,
            flags=re.IGNORECASE,
        )
    return text.strip()


def _has_think_markers(text: str) -> bool:
    """True, если в тексте есть открывающий или закрывающий think-тег."""
    if not text:
        return False
    lower = text.lower()
    return "<think>".lower() in lower or "</think>".lower() in lower



def _normalize_reasoning_payload(value: Any) -> str:
    """Нормализует reasoning payload из OpenAI-compat ответов к строке."""
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
        nested_content = value.get("content")
        if isinstance(nested_content, list):
            return _normalize_reasoning_payload(nested_content)
        parts: List[str] = []
        for k, v in value.items():
            if any(
                (token in str(k).lower() for token in ("reason", "think", "thought"))
            ):
                parts.append(_normalize_reasoning_payload(v))
        return "".join(parts)
    return str(value)


def _normalize_content_payload(value: Any) -> str:
    """Нормализует content payload из OpenAI-compat ответов к строке."""
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


def _build_ssl_verify():
    """
    Определяет параметр verify для httpx из переменных окружения.
    Приоритет: TLS_CERT_PATH (путь к CA bundle) > LLM_VERIFY_SSL > True (дефолт).
    Читается напрямую из os.environ, без зависимости от settings/config.yml.
    """
    tls_cert = os.environ.get("TLS_CERT_PATH", "").strip()
    if tls_cert:
        if os.path.isfile(tls_cert):
            logger.debug(f"[SSL] CA bundle из TLS_CERT_PATH: {tls_cert}")
            return tls_cert
        else:
            logger.warning(
                f"[SSL] TLS_CERT_PATH задан, но файл не найден: {tls_cert}. Используется verify=True"
            )
            return True
    verify_str = os.environ.get("LLM_VERIFY_SSL", "").strip().lower()
    if verify_str in ("false", "0", "no"):
        logger.warning("[SSL] Проверка SSL отключена (LLM_VERIFY_SSL=false)")
        return False
    return True


class LLMClient:
    """Клиент для взаимодействия с распределенными API сервисов"""

    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None):
        settings = get_settings()
        if base_url:
            self.llm_url = base_url.rstrip("/")
        else:
            self.llm_url = settings.get_llm_service_url().rstrip("/")

        def _optional_url(docker_key: str, port_key: str):
            try:
                return settings.microservice_http_base(docker_key, port_key)
            except Exception:
                logger.exception("Optional service url not set /")
                return None

        # ВРЕМЕННО: OCR через мультимодальную LLM, пока нет отдельного ocr-service.
        # Переменные берутся из ConfigMap.
        self.ocr_vision_model_path = os.getenv("OCR_VISION_MODEL_PATH")
        if self.ocr_vision_model_path:
            logger.debug(
                f"LLMClient: OCR vision fallback configured model_path={self.ocr_vision_model_path}"
            )

        self.stt_url = _optional_url("stt_service_docker", "stt_service_port")
        self.tts_url = _optional_url("tts_service_docker", "tts_service_port")
        self.ocr_url = _optional_url("ocr_service_docker", "ocr_service_port")
        self.diarization_url = _optional_url(
            "diarization_service_docker", "diarization_service_port"
        )
        if "1lm-svc" in self.llm_url or "11m-svc" in self.llm_url:
            logger.error(f"ОБНАРУЖЕНА ОПЕЧАТКА В URL: {self.llm_url}. Исправляем.")
            self.llm_url = self.llm_url.replace("1lm-svc", "llm-svc").replace(
                "11m-svc", "llm-svc"
            )
        self._verify = _build_ssl_verify()
        self.llm_hosts: Dict[str, str] = {}
        try:
            cfg_hosts = getattr(settings.llm_service, "hosts", None) or []
            for entry in cfg_hosts:
                if entry is None:
                    continue
                if hasattr(entry, "id") and hasattr(entry, "base_url"):
                    hid, bu = (entry.id, entry.base_url)
                elif isinstance(entry, dict):
                    hid, bu = (entry.get("id"), entry.get("base_url"))
                else:
                    continue
                if hid and bu:
                    self.llm_hosts[str(hid)] = str(bu).rstrip("/")
        except Exception:
            logger.exception("LLM hosts из конфига не разобраны")
        if not self.llm_hosts:
            self.llm_hosts = {"default": self.llm_url}
        dh = getattr(settings.llm_service, "default_host_id", None)
        self.default_llm_host: str = (
            dh if dh and dh in self.llm_hosts else next(iter(self.llm_hosts))
        )
        self.llm_url = self.llm_hosts[self.default_llm_host]
        logger.debug("LLMClient инициализирован. Маршруты:")
        logger.debug(
            f"  LLM: {self.llm_url} (hosts={list(self.llm_hosts.keys())}, default_host={self.default_llm_host}) | STT: {self.stt_url} | TTS: {self.tts_url}"
        )
        logger.debug(f"  SSL verify: {self._verify}")
        cfg_api_key = getattr(settings.llm_service, "api_key", None) or os.getenv(
            "LLM_API_KEY"
        )
        self.api_key = api_key or cfg_api_key
        if self.api_key:
            logger.debug("LLMClient: API key configured for upstream requests")
        try:
            self.timeout = settings.llm_service.timeout
        except Exception:
            logger.exception("Ошибка операции")
            self.timeout = 120.0
        try:
            providers = getattr(settings, "llm_providers", None) or []
            for p in providers:
                if not isinstance(p, dict):
                    continue
                if not bool(p.get("enabled", True)):
                    continue
                pid = str(p.get("id") or "").strip()
                burl = str(p.get("base_url") or "").strip().rstrip("/")
                kind = str(p.get("kind") or "").strip().lower()
                if (
                    pid
                    and burl
                    and (
                        kind
                        in {"openai-compat", "llm-svc", "vllm", "ollama", "litellm"}
                    )
                ):
                    self.llm_hosts.setdefault(pid, burl)
        except Exception:
            logger.exception("LLM providers из конфига не разобраны")

    @property
    def ocr_available(self) -> bool:
        """True, если есть хоть какой-то способ распознать текст с изображения:
        либо настоящий ocr-service, либо временный vision-LLM fallback."""
        return bool(self.ocr_url) or bool(self.ocr_vision_model_path)

    def _get_headers(self) -> Dict[str, str]:
        """Получение заголовков"""
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        # X-User-ID
        from backend.settings.cef_logger.cef_audit_context import (
            model_identity_headers,
        )

        headers.update(model_identity_headers())
        return headers

    def _url_for_llm_host(self, host_id: Optional[str] = None) -> str:
        hid = (
            host_id if host_id and host_id in self.llm_hosts else self.default_llm_host
        )
        return self.llm_hosts.get(hid) or self.llm_url

    def _parse_json_or_log(
        self, response: httpx.Response, *, op_name: str, base_url: str
    ) -> Tuple[Optional[Any], Optional[Dict[str, Any]]]:
        try:
            return (response.json(), None)
        except ValueError:
            content_type = response.headers.get("content-type", "")
            body_preview = (response.text or "")[:300]
            diag = {
                "status": "unhealthy",
                "error": "invalid_json_response",
                "http_status": response.status_code,
                "content_type": content_type,
                "body_preview": body_preview,
            }
            logger.exception(
                "%s: невалидный JSON от LLM (%s) status=%s content-type=%s body=%r",
                op_name,
                base_url,
                response.status_code,
                content_type,
                body_preview,
            )
            return (None, diag)

    async def health_check(self, host_id: Optional[str] = None) -> Dict[str, Any]:
        """Проверка состояния LLM на указанном хосте (или default_llm_host)."""
        try:
            from backend.llm_providers import get_registry

            registry = await get_registry()
            pid = host_id if host_id and registry.contains(host_id) else None
            provider = registry.get(pid)
            health = await provider.health()
            return {
                "status": "healthy" if health.healthy else "unhealthy",
                "provider_id": provider.id,
                "provider_kind": provider.kind,
                "loaded_models": health.loaded_models,
                "error": health.error,
                "raw": health.raw,
            }
        except Exception:
            logger.exception("ProviderRegistry health_check fallback to legacy HTTP")
        base = self._url_for_llm_host(host_id)
        try:
            async with httpx.AsyncClient(verify=self._verify, timeout=10.0) as client:
                for path in ("/v1/health", "/health", "/v1/models"):
                    response = await client.get(
                        f"{base}{path}", headers=self._get_headers()
                    )
                    if not response.is_success:
                        continue
                    payload, parse_error = self._parse_json_or_log(
                        response, op_name=f"health_check:{path}", base_url=base
                    )
                    if parse_error:
                        continue
                    if path == "/v1/models":
                        if isinstance(payload, dict) and isinstance(
                            payload.get("data"), list
                        ):
                            return {
                                "status": "healthy",
                                "source": path,
                                "models_count": len(payload.get("data", [])),
                            }
                        continue
                    if isinstance(payload, dict):
                        if "status" not in payload:
                            payload["status"] = "healthy"
                        payload.setdefault("source", path)
                        return payload
                    return {"status": "healthy", "source": path, "data": payload}
                return {"status": "unhealthy", "error": "no_valid_health_endpoint"}
        except Exception as e:
            logger.exception("Ошибка здоровья LLM ()")
            return {"status": "unhealthy", "error": str(e)}

    async def get_models(self, host_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Получение списка моделей с инстанса host_id."""
        try:
            from backend.llm_providers import get_registry

            registry = await get_registry()
            pid = host_id if host_id and registry.contains(host_id) else None
            provider = registry.get(pid)
            models = await provider.list_models()
            data = []
            for m in models:
                data.append(
                    {
                        "id": m.model_id,
                        "provider_id": m.provider_id,
                        "path": m.path,
                        "display_name": m.display_name or m.model_id,
                        "context_size": m.context_size,
                        "extra": m.extra or {},
                    }
                )
            return data
        except Exception:
            logger.exception("ProviderRegistry get_models fallback to legacy HTTP")
        base = self._url_for_llm_host(host_id)
        try:
            async with httpx.AsyncClient(verify=self._verify, timeout=10.0) as client:
                response = await client.get(
                    f"{base}/v1/models", headers=self._get_headers()
                )
                response.raise_for_status()
                data, parse_error = self._parse_json_or_log(
                    response, op_name="get_models", base_url=base
                )
                if parse_error:
                    return []
                if not isinstance(data, dict):
                    logger.error(
                        "get_models: неожиданный формат ответа (%s): %r",
                        base,
                        type(data).__name__,
                    )
                    return []
                return data.get("data", [])
        except Exception:
            logger.exception("Ошибка получения моделей ()")
            return []

    async def load_model(self, model_name: str, host_id: Optional[str] = None) -> bool:
        """Запросить LLM-сервис загрузит
        ь/переключить модель по имени (для llama.cpp backend)."""
        if not model_name or not model_name.strip():
            logger.warning("load_model: пустое имя модели")
            return False
        model_name = model_name.strip()
        try:
            from backend.llm_providers import get_registry

            registry = await get_registry()
            pid = host_id if host_id and registry.contains(host_id) else None
            provider = registry.get(pid)
            ok = await provider.ensure_model_loaded(model_name)
            logger.debug(
                "[load_model][provider] provider=%s kind=%s model=%r result=%s",
                provider.id,
                provider.kind,
                model_name,
                ok,
            )
            return bool(ok)
        except Exception:
            logger.exception("ProviderRegistry load_model fallback to legacy HTTP")
        base = self._url_for_llm_host(host_id)
        load_timeout = httpx.Timeout(1200.0, connect=10.0, read=1200.0, write=30.0)
        try:
            async with httpx.AsyncClient(
                verify=self._verify, timeout=load_timeout
            ) as client:
                response = await client.post(
                    f"{base}/v1/models/load",
                    headers=self._get_headers(),
                    json={"model": model_name},
                )
                if not response.is_success:
                    logger.error(
                        f"llm-svc load_model failed ({base}): {response.status_code} {response.text}"
                    )
                    return False
                data = response.json()
                if data.get("success"):
                    logger.info(
                        f"llm-svc ({base}) переключился на модель: {model_name}"
                    )
                    return True
                logger.warning(f"llm-svc load_model returned success=False: {data}")
                return False
        except Exception:
            logger.exception("Ошибка вызова llm-svc load_model ()")
            return False

    async def load_model_if_needed(
        self, model_name: str, host_id: Optional[str] = None
    ) -> bool:
        """POST /v1/models/load только если модели ещё нет в пуле на этом хосте."""
        if not model_name or not model_name.strip():
            return False
        model_name = model_name.strip()
        health = await self.health_check(host_id=host_id)
        if pool_contains_model(health, model_name):
            logger.debug(
                f"llm-svc: модель {model_name!r} уже в пуле ({host_id or self.default_llm_host}), пропуск load_model"
            )
            return True
        return await self.load_model(model_name, host_id=host_id)

    async def unload_excess_llm_models(self, host_id: Optional[str] = None) -> bool:
        """Оставить в llm-svc только модель из конфига сервиса. host_id=None — все хосты."""
        t = httpx.Timeout(1200.0, connect=10.0, read=1200.0, write=60.0)
        targets = [host_id] if host_id else list(self.llm_hosts.keys())
        ok_all = True
        for hid in targets:
            base = self._url_for_llm_host(hid)
            try:
                async with httpx.AsyncClient(verify=self._verify, timeout=t) as client:
                    response = await client.post(
                        f"{base}/v1/models/unload-excess", headers=self._get_headers()
                    )
                    if not response.is_success:
                        logger.error(
                            f"llm-svc unload-excess failed ({base}): {response.status_code} {response.text}"
                        )
                        ok_all = False
                        continue
                    data = response.json()
                    ok_all = ok_all and bool(data.get("success", True))
            except Exception:
                logger.exception("Ошибка вызова llm-svc unload-excess ()")
                ok_all = False
        return ok_all

    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        model: str = "qwen-coder-30b",
        temperature: float = 0.7,
        max_tokens: int = 1024,
        stream: bool = False,
        host_id: Optional[str] = None,
        request_extra: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Генерация ответа LLM"""
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        if request_extra:
            for k, v in request_extra.items():
                if v is not None:
                    payload[k] = v
        base = self._url_for_llm_host(host_id)
        from backend.llm_providers.routing import is_thinking_requested

        logger.debug(
            "[LLMClient] POST %s/v1/chat/completions host=%r model=%r stream=%r enable_thinking=%r",
            base,
            host_id,
            model,
            stream,
            is_thinking_requested(request_extra),
        )
        try:
            request_timeout = httpx.Timeout(
                self.timeout, connect=10.0, read=self.timeout, write=10.0
            )
            async with httpx.AsyncClient(
                verify=self._verify, timeout=request_timeout
            ) as client:
                response = await client.post(
                    f"{base}/v1/chat/completions",
                    headers=self._get_headers(),
                    json=payload,
                )
                response.raise_for_status()
                return response.json()
        except Exception:
            logger.exception("Ошибка чата LLM")
            raise

    async def get_transcription_health(self) -> Dict[str, Any]:
        """Проверка состояния STT (WhisperX)"""
        try:
            async with httpx.AsyncClient(verify=self._verify, timeout=5.0) as client:
                response = await client.get(f"{self.stt_url}/v1/whisperx/health")
                if response.status_code == 200:
                    return response.json()
                return {"status": "unhealthy", "code": response.status_code}
        except Exception as e:
            logger.exception("Ошибка операции")
            return {"status": "unhealthy", "error": str(e)}

    async def get_tts_health(self) -> Dict[str, Any]:
        """Проверка состояния TTS сервиса"""
        try:
            async with httpx.AsyncClient(verify=self._verify, timeout=5.0) as client:
                response = await client.get(f"{self.tts_url}/v1/health")
                if response.status_code == 200:
                    return response.json()
                return {"status": "unhealthy", "code": response.status_code}
        except Exception as e:
            logger.exception("Ошибка операции")
            return {"status": "unhealthy", "error": str(e)}

    async def transcribe_audio_whisperx(
        self,
        audio_file: bytes,
        filename: str = "audio.wav",
        language: str = "auto",
        compute_type: str = "float16",
        batch_size: int = 16,
    ) -> Dict[str, Any]:
        """Транскрибация аудио файла через WhisperX."""
        require_service("stt")
        try:
            ext = os.path.splitext(filename)[1].lower()
            content_type_map = {
                ".wav": "audio/wav",
                ".webm": "audio/webm",
                ".ogg": "audio/ogg",
                ".mp3": "audio/mpeg",
                ".m4a": "audio/mp4",
                ".flac": "audio/flac",
            }
            content_type = content_type_map.get(ext, "audio/wav")
            files = {"file": (filename, io.BytesIO(audio_file), content_type)}
            data = {
                "language": language,
                "compute_type": compute_type,
                "batch_size": batch_size,
            }
            whisperx_timeout = httpx.Timeout(
                18000.0, connect=10.0, read=18000.0, write=60.0
            )
            async with httpx.AsyncClient(
                verify=self._verify, timeout=whisperx_timeout
            ) as client:
                response = await client.post(
                    f"{self.stt_url}/v1/whisperx/transcribe",
                    files=files,
                    data=data,
                    headers={"Accept": "application/json"},
                )
                if response.status_code == 400:
                    try:
                        body = response.json()
                        err = body.get("error") or body.get("detail") or response.text
                        logger.warning(f"WhisperX вернул 400 для {filename}: {err}")
                        return {"success": False, "text": "", "error": str(err)}
                    except Exception:
                        logger.exception("WhisperX вернул 400 для")
                        return {
                            "success": False,
                            "text": "",
                            "error": response.text[:300],
                        }
                response.raise_for_status()
                return response.json()
        except Exception:
            logger.exception("Ошибка транскрибации WhisperX")
            raise

    async def reload_whisperx_models(self) -> Dict[str, Any]:
        """Принудительная перезагрузка моделей WhisperX"""
        try:
            async with httpx.AsyncClient(verify=self._verify, timeout=60.0) as client:
                response = await client.post(
                    f"{self.stt_url}/v1/whisperx/reload",
                    headers={"Accept": "application/json"},
                )
                response.raise_for_status()
                return response.json()
        except Exception:
            logger.exception("Ошибка перезагрузки WhisperX")
            raise

    async def synthesize_speech(
        self,
        text: str,
        language: str = "auto",
        speaker: str = "baya",
        sample_rate: int = 48000,
        speech_rate: float = 1.0,
    ) -> bytes:
        """Синтез речи Silero"""
        require_service("tts")
        try:
            data = {
                "text": text,
                "language": language,
                "speaker": speaker,
                "sample_rate": sample_rate,
                "speech_rate": speech_rate,
            }
            async with httpx.AsyncClient(verify=self._verify, timeout=300.0) as client:
                response = await client.post(
                    f"{self.tts_url}/v1/synthesize",
                    data=data,
                    headers={"Accept": "audio/wav"},
                )
                response.raise_for_status()
                return response.content
        except Exception:
            logger.exception("Ошибка синтеза речи")
            raise

    async def diarize_audio(
        self,
        audio_file: bytes,
        filename: str = "audio.wav",
        min_speakers: int = 1,
        max_speakers: int = 10,
        min_duration: float = 0.5,
    ) -> Dict[str, Any]:
        """Диаризация Pyannote"""
        require_service("diarization")
        try:
            files = {"file": (filename, io.BytesIO(audio_file), "audio/wav")}
            data = {
                "min_speakers": min_speakers,
                "max_speakers": max_speakers,
                "min_duration": min_duration,
            }
            diarize_timeout = httpx.Timeout(
                18000.0, connect=10.0, read=18000.0, write=60.0
            )
            async with httpx.AsyncClient(
                verify=self._verify, timeout=diarize_timeout
            ) as client:
                response = await client.post(
                    f"{self.diarization_url}/v1/diarize",
                    files=files,
                    data=data,
                    headers={"Accept": "application/json"},
                )
                response.raise_for_status()
                return response.json()
        except Exception:
            logger.exception("Ошибка диаризации")
            raise

    async def transcribe_with_diarization(
        self,
        audio_file: bytes,
        filename: str = "audio.wav",
        language: str = "auto",
        min_speakers: int = 1,
        max_speakers: int = 10,
        min_duration: float = 0.5,
        engine: str = "whisperx",
    ) -> Dict[str, Any]:
        """
        Комбинированная транскрибация с диаризацией.
        Шаг 1: диаризация (diarization-service) → сегменты по спикерам.
        Шаг 2: транскрипция (stt-service) → текст с временными метками.
        Шаг 3: сопоставление по времени → segments[{speaker, text, start, end}].
        Возвращает {success, text, segments, speakers_count}.
        """
        require_service("diarization")
        try:
            logger.debug(
                f"[transcribe_with_diarization] Шаг 1: диаризация... engine={engine}"
            )
            diarization_result = await self.diarize_audio(
                audio_file,
                filename=filename,
                min_speakers=min_speakers,
                max_speakers=max_speakers,
                min_duration=min_duration,
            )
            diar_segments = diarization_result.get("segments", [])
            if not diar_segments:
                logger.warning(
                    "[transcribe_with_diarization] Диаризация не нашла сегментов"
                )
                return {
                    "success": True,
                    "text": "",
                    "segments": [],
                    "speakers_count": 0,
                }
            logger.debug(
                f"[transcribe_with_diarization] Шаг 2: транскрипция (WhisperX, engine={engine})..."
            )
            transcription_result = await self.transcribe_audio_whisperx(
                audio_file, filename=filename, language=language
            )
            full_text = (
                transcription_result.get("text", "")
                if isinstance(transcription_result, dict)
                else str(transcription_result)
            )
            stt_segments = (
                transcription_result.get("segments", [])
                if isinstance(transcription_result, dict)
                else []
            )
            has_timestamps = len(stt_segments) > 0 and all(
                (
                    isinstance(s.get("start"), (int, float))
                    and isinstance(s.get("end"), (int, float))
                    for s in stt_segments
                )
            )
            if has_timestamps:
                diar_sorted = sorted(diar_segments, key=lambda s: s.get("start", 0))
                all_words = []
                for stt in stt_segments:
                    words = stt.get("words") or []
                    for w in words:
                        if (
                            isinstance(w, dict)
                            and "start" in w
                            and ("end" in w)
                            and w.get("word")
                        ):
                            all_words.append(w)
                if all_words:
                    raw_parts = []
                    for w in all_words:
                        w_start = float(w.get("start", 0))
                        w_end = float(w.get("end", 0))
                        word_text = (w.get("word") or "").strip()
                        if not word_text:
                            continue
                        best_speaker = "SPEAKER_0"
                        best_overlap = 0.0
                        for d in diar_sorted:
                            d_start = float(d.get("start", 0))
                            d_end = float(d.get("end", 0))
                            overlap = max(
                                0.0, min(w_end, d_end) - max(w_start, d_start)
                            )
                            if overlap > best_overlap:
                                best_overlap = overlap
                                best_speaker = d.get("speaker", "SPEAKER_0")
                        raw_parts.append((w_start, w_end, best_speaker, word_text))
                    result_segments = []
                    for w_start, w_end, speaker, word_text in raw_parts:
                        if (
                            result_segments
                            and result_segments[-1]["speaker"] == speaker
                        ):
                            result_segments[-1]["text"] += " " + word_text
                            result_segments[-1]["end"] = w_end
                        else:
                            result_segments.append(
                                {
                                    "start": w_start,
                                    "end": w_end,
                                    "speaker": speaker,
                                    "text": word_text,
                                }
                            )
                    logger.debug(
                        f"[transcribe_with_diarization] Word-level сопоставление: {len(result_segments)} сегментов"
                    )
                else:
                    raw_parts = []
                    for stt in stt_segments:
                        t_start = float(stt.get("start", 0))
                        t_end = float(stt.get("end", 0))
                        text = (stt.get("text") or "").strip()
                        if not text:
                            continue
                        overlapping = sorted(
                            [
                                (
                                    float(d.get("start", 0)),
                                    float(d.get("end", 0)),
                                    d.get("speaker", "SPEAKER_0"),
                                    max(
                                        0.0,
                                        min(t_end, float(d.get("end", 0)))
                                        - max(t_start, float(d.get("start", 0))),
                                    ),
                                )
                                for d in diar_sorted
                                if max(
                                    0.0,
                                    min(t_end, float(d.get("end", 0)))
                                    - max(t_start, float(d.get("start", 0))),
                                )
                                > 0
                            ],
                            key=lambda x: x[0],
                        )
                        if not overlapping:
                            nearest = min(
                                diar_sorted,
                                key=lambda d: abs(float(d.get("start", 0)) - t_start),
                            )
                            raw_parts.append(
                                (
                                    t_start,
                                    t_end,
                                    nearest.get("speaker", "SPEAKER_0"),
                                    text,
                                )
                            )
                            continue
                        if len(overlapping) == 1:
                            raw_parts.append((t_start, t_end, overlapping[0][2], text))
                        else:
                            total_overlap = sum((o for _, _, _, o in overlapping))
                            words_list = text.split()
                            word_idx = 0
                            for i, (d_start, d_end, speaker, overlap) in enumerate(
                                overlapping
                            ):
                                if i == len(overlapping) - 1:
                                    chunk = words_list[word_idx:]
                                else:
                                    count = max(
                                        1,
                                        round(
                                            len(words_list) * overlap / total_overlap
                                        ),
                                    )
                                    chunk = words_list[word_idx : word_idx + count]
                                    word_idx += len(chunk)
                                chunk_text = " ".join(chunk).strip()
                                if chunk_text:
                                    raw_parts.append(
                                        (d_start, d_end, speaker, chunk_text)
                                    )
                    result_segments = []
                    for p_start, p_end, speaker, text in sorted(
                        raw_parts, key=lambda x: x[0]
                    ):
                        if (
                            result_segments
                            and result_segments[-1]["speaker"] == speaker
                        ):
                            result_segments[-1]["text"] += " " + text
                            result_segments[-1]["end"] = p_end
                        else:
                            result_segments.append(
                                {
                                    "start": p_start,
                                    "end": p_end,
                                    "speaker": speaker,
                                    "text": text,
                                }
                            )
                    logger.debug(
                        f"[transcribe_with_diarization] Segment+split+merge: {len(result_segments)} сегментов"
                    )
            else:
                total_duration = sum(
                    (
                        s.get("duration", s.get("end", 0) - s.get("start", 0))
                        for s in diar_segments
                    )
                )
                words = full_text.split() if full_text else []
                total_words = len(words)
                result_segments = []
                word_idx = 0
                for seg in diar_segments:
                    seg_duration = seg.get(
                        "duration", seg.get("end", 0) - seg.get("start", 0)
                    )
                    word_count = (
                        max(1, round(total_words * seg_duration / total_duration))
                        if total_duration > 0
                        else total_words
                    )
                    seg_words = words[word_idx : word_idx + word_count]
                    word_idx += word_count
                    result_segments.append(
                        {
                            "start": seg.get("start", 0),
                            "end": seg.get("end", 0),
                            "speaker": seg.get("speaker", "SPEAKER_0"),
                            "text": " ".join(seg_words),
                        }
                    )
                if word_idx < total_words and result_segments:
                    result_segments[-1]["text"] += " " + " ".join(words[word_idx:])
                logger.debug(
                    f"[transcribe_with_diarization] Fallback по длительности: {len(diar_segments)} сегментов"
                )
            return {
                "success": True,
                "text": full_text,
                "segments": result_segments,
                "speakers_count": diarization_result.get("speakers_count", 0),
            }
        except Exception:
            logger.exception("[transcribe_with_diarization] Ошибка")
            raise

    async def recognize_text_from_image(
        self, image_file: bytes, filename: str = "image.jpg", languages: str = "ru,en"
    ) -> Dict[str, Any]:
        """Распознавание текста с изображения через ocr-service (Surya). Сейчас временный fallback на мультимодальную LLM."""
        if self.ocr_vision_model_path:
            return await self.recognize_text_from_image_via_llm(image_file, filename)
        raise RuntimeError("OCR не настроен: не настроен OCR_VISION_MODEL_PATH")
        # Временно убираем логику с отдельным эндпоинтом.
        # try:
        #     mime = "image/jpeg"
        #     if filename.lower().endswith(".png"):
        #         mime = "image/png"
        #     files = {"file": (filename, io.BytesIO(image_file), mime)}
        #     data = {"languages": languages}
        #     async with httpx.AsyncClient(verify=self._verify, timeout=300.0) as client:
        #         response = await client.post(
        #             f"{self.ocr_url}/v1/ocr", files=files, data=data, headers={"Accept": "application/json"}
        #         )
        #         response.raise_for_status()
        #         return response.json()
        # except Exception:
        #     logger.exception("Ошибка OCR")
        #     raise

    async def recognize_text_from_image_via_llm(
        self, image_file: bytes, filename: str = "image.png"
    ) -> Dict[str, Any]:
        """ВРЕМЕННОЕ решение: OCR через мультимодальную LLM вместо отдельного ocr-service."""
        if not self.ocr_vision_model_path:
            raise RuntimeError(
                "OCR vision fallback не настроен: OCR_VISION_MODEL_PATH не заданы"
            )
        image_file = _resize_for_vision(image_file)
        b64 = base64.b64encode(image_file).decode("ascii")
        prompt = (
            "Распознай весь текст на изображении. Верни ТОЛЬКО распознанный текст "
            "в формате Markdown, сохраняя структуру документа (заголовки, таблицы, "
            "списки, абзацы). Не добавляй никаких комментариев от себя."
        )
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                    },
                ],
            }
        ]
        try:
            from backend.llm_providers import get_registry

            registry = await get_registry()
            provider_ref, resolved_model = registry.resolve(self.ocr_vision_model_path)
            eff_model = resolved_model or self.ocr_vision_model_path
            logger.debug(
                "[OCR-vision] INPUT model_path=%r RESOLVED provider_id=%r kind=%r resolved_model=%r",
                self.ocr_vision_model_path,
                provider_ref.id,
                provider_ref.kind,
                eff_model,
            )
            text = await provider_ref.chat(
                messages=messages,
                model=eff_model,
                temperature=0.0,
                max_tokens=4096,
            )
            text = text if isinstance(text, str) else str(text or "")
            logger.debug("[OCR-vision] file=%s text_len=%s", filename, len(text))

            return {"text": text.strip()}
        except Exception:
            logger.exception("Ошибка OCR через мультимодальную LLM (vision fallback)")
            raise


class LLMService:
    """Сервис высокого уровня для работы с AI"""

    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None):
        self.client = LLMClient(base_url, api_key)
        self._model_switch_lock = asyncio.Lock()
        settings = get_settings()
        if settings and hasattr(settings, "llm_service"):
            llm_svc_config = settings.llm_service
            self.model_name = llm_svc_config.default_model
            self.fallback_model = llm_svc_config.fallback_model
            self.auto_select = llm_svc_config.auto_select
        else:
            self.model_name = "qwen-coder-30b"
            self.fallback_model = None
            self.auto_select = False

    async def initialize(self) -> bool:
        """Инициализация связи с сервисом LLM (первый доступный хост из конфига)."""
        try:
            for hid in self.client.llm_hosts:
                health = await self.client.health_check(host_id=hid)
                if health.get("status") != "healthy":
                    continue
                logger.debug(f"Связь с микросервисом LLM установлена (host={hid!r})")
                if health.get("model_loaded") and health.get("model_name"):
                    self.model_name = health["model_name"]
                    logger.debug(
                        f"Текущая загруженная модель в llm-svc: {self.model_name}"
                    )
                else:
                    models = await self.client.get_models(host_id=hid)
                    if models:
                        self.model_name = models[0]["id"]
                        logger.debug(
                            f"Модель не загружена в llm-svc, первая из списка: {self.model_name}"
                        )
                return True
            logger.error("Микросервис LLM недоступен ни на одном из настроенных хостов")
            return False
        except Exception:
            logger.exception("Ошибка инициализации LLMService")
            return False

    async def _sync_loaded_model_name_from_health(self, host_id: Optional[str] = None):
        """Подтягивает self.model_name из GET /v1/health выбранного хоста."""
        try:
            hid = host_id or self.client.default_llm_host
            health = await self.client.health_check(host_id=hid)
            if health.get("status") != "healthy":
                return (False, health)
            if health.get("model_loaded") and health.get("model_name"):
                actual = health["model_name"]
                if self.model_name != actual:
                    logger.info(
                        f"[LLMService] Синхронизация model_name с llm-svc: {self.model_name!r} → {actual!r}"
                    )
                self.model_name = actual
                return (True, health)
            return (False, health)
        except Exception:
            logger.exception("[LLMService] Пропуск синхронизации model_name по health")
            return (False, {})

    def prepare_messages(
        self,
        prompt: str,
        history: Optional[List[Dict[str, str]]] = None,
        system_prompt: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """Подготовка сообщений для OpenAI API формата"""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        elif history and len(history) > 0:
            system_prompt_with_history = "Ты - полезный AI ассистент. У тебя есть доступ к полной истории диалога с пользователем. Важные возможности:\n- Ты МОЖЕШЬ обращаться к предыдущим сообщениям в диалоге\n- Ты МОЖЕШЬ подсчитать количество токенов и сообщений в истории\n- Ты ВИДИШЬ все предыдущие сообщения в этом диалоге\n- Ты МОЖЕШЬ ссылаться на информацию из предыдущих сообщений\nКогда пользователь спрашивает о предыдущих сообщениях или токенах - используй доступную историю для ответа."
            messages.append({"role": "system", "content": system_prompt_with_history})
        MAX_CONTEXT_TOKENS = 250000
        if history:
            system_tokens = sum((len(m.get("content", "")) // 3 for m in messages))
            prompt_tokens = len(prompt) // 3
            used_tokens = system_tokens + prompt_tokens + 1024
            trimmed_history = []
            for entry in reversed(history):
                role = entry.get("role", "user")
                content = entry.get("content", "")
                if role not in ["user", "assistant", "system"]:
                    continue
                # Обычные реплики режем мягко; сообщения с вложением — только
                # по общему бюджету токенов (иначе follow-up теряет файл).
                has_attach = "[Прикреплённый документ]" in content
                max_chars = 100_000 if has_attach else 8000
                if len(content) > max_chars:
                    content = content[:max_chars] + "... [обрезано]"
                entry_tokens = len(content) // 3
                if used_tokens + entry_tokens > MAX_CONTEXT_TOKENS:
                    logger.warning(
                        f"История обрезана: {len(trimmed_history)} из {len(history)} сообщений вместилось"
                    )
                    break
                trimmed_history.append({"role": role, "content": content})
                used_tokens += entry_tokens
            for entry in reversed(trimmed_history):
                messages.append(entry)
        messages.append({"role": "user", "content": prompt})
        return messages

    @staticmethod
    def _thinking_request_extra(
        enable_thinking: Optional[bool],
    ) -> Optional[Dict[str, Any]]:
        if enable_thinking is None:
            return None
        from backend.llm_providers.routing import thinking_request_extra

        return thinking_request_extra(bool(enable_thinking))

    async def generate_response(
        self,
        prompt: str,
        history: Optional[List[Dict[str, str]]] = None,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        streaming: bool = False,
        stream_callback: Optional[Callable[..., bool]] = None,
        images: Optional[List[str]] = None,
        model_path: Optional[str] = None,
        enable_thinking: Optional[bool] = None,
    ) -> str:
        """Генерация ответа через распределенную систему"""
        cef_llm_rid = uuid.uuid4().hex
        try:
            if history:
                logger.debug(
                    f"История диалога: {len(history)} сообщений передается в LLM"
                )
            messages = self.prepare_messages(prompt, history, system_prompt)
            req_extra = self._thinking_request_extra(enable_thinking)
            # Поля выборки (top_p, top_k, penalties) доезжают до модели только
            # отсюда: payload и для потока, и без него собирается из req_extra.
            # llm-svc лишние поля игнорирует (extra="ignore" у его схемы),
            # шлюзу они уходят вместе с заголовком проброса.
            from backend.llm_providers.routing import merge_sampling_request_extra

            req_extra = merge_sampling_request_extra(req_extra)
            logger.debug(
                "[generate_response] thinking flag: enable_thinking=%r req_extra=%s",
                enable_thinking,
                req_extra,
            )
            if images:
                logger.debug(f"Добавление {len(images)} изображений к запросу")

                image_urls = []
                for image_path in images:
                    if not image_path:
                        continue
                    if str(image_path).startswith("data:"):
                        image_urls.append(str(image_path))
                        continue
                    if not os.path.exists(image_path):
                        continue
                    try:
                        with open(image_path, "rb") as f:
                            data = f.read()
                        b64 = base64.b64encode(data).decode("ascii")
                        ext = os.path.splitext(image_path)[1].lower()
                        mime = (
                            "image/png"
                            if ext == ".png"
                            else (
                                "image/jpeg"
                                if ext in (".jpg", ".jpeg")
                                else "image/png"
                            )
                        )
                        image_urls.append(f"data:{mime};base64,{b64}")
                    except Exception:
                        logger.exception("Не удалось прочитать изображение")
                if image_urls:
                    for msg in reversed(messages):
                        if msg.get("role") == "user":
                            content = msg.get("content", "")
                            msg["content"] = [{"type": "text", "text": content}]
                            for url in image_urls:
                                msg["content"].append(
                                    {"type": "image_url", "image_url": {"url": url}}
                                )
                            break
            if not str(model_path or "").strip():
                await self._sync_loaded_model_name_from_health()
            hid, model_to_use = resolve_llm_host_and_model_for_svc(
                model_path,
                self.model_name,
                self.client.llm_hosts,
                self.client.default_llm_host,
            )
            allow_hot_swap_load = True
            try:
                from backend.llm_providers import get_registry

                registry = await get_registry()
                provider_ref, resolved_model = registry.resolve(model_path)
                if resolved_model:
                    model_to_use = resolved_model
                invalid_model_tokens = {"", "llm-svc", "llm-svc://", "local", "default"}
                if (model_to_use or "").strip().lower() in invalid_model_tokens:
                    model_to_use = ""
                if not provider_ref.capabilities.hot_swap:
                    allow_hot_swap_load = False
                    if model_to_use:
                        self.model_name = model_to_use
                if provider_ref.kind != "llm-svc":
                    eff_model = model_to_use
                    if not eff_model:
                        try:
                            mlist = await provider_ref.list_models()
                            if mlist:
                                eff_model = mlist[0].model_id
                                logger.info(
                                    "[generate_response] model_path пустой → автовыбор %r у провайдера %r",
                                    eff_model,
                                    provider_ref.id,
                                )
                        except Exception:
                            logger.exception(
                                "[generate_response] auto-select first model failed"
                            )
                    if eff_model:
                        logger.info(
                            "[generate_response] DELEGATE provider_id=%r kind=%r model=%r streaming=%r enable_thinking=%r req_extra=%r",
                            provider_ref.id,
                            provider_ref.kind,
                            eff_model,
                            streaming,
                            enable_thinking,
                            req_extra,
                        )
                        # После rebuild пул пуст: без ensure chat/completions даёт 503.
                        # Не «пробуем chat» при False — иначе UI видит выбранную модель и 503.
                        try:
                            loaded = await provider_ref.ensure_model_loaded(eff_model)
                            if not loaded:
                                logger.warning(
                                    "[generate_response] ensure_model_loaded=False provider=%r model=%r",
                                    provider_ref.id,
                                    eff_model,
                                )
                                return (
                                    f"Модель «{eff_model}» не загружена в LLM-бэкенде. "
                                    "Дождитесь загрузки весов или выберите модель в селекторе ещё раз."
                                )
                        except Exception:
                            logger.exception(
                                "[generate_response] ensure_model_loaded failed provider=%r model=%r",
                                provider_ref.id,
                                eff_model,
                            )
                            return (
                                f"Не удалось загрузить модель «{eff_model}» в LLM-бэкенде. "
                                "Повторите выбор модели в селекторе."
                            )
                        if streaming and stream_callback:
                            return await provider_ref.stream_chat(
                                messages=messages,
                                model=eff_model,
                                callback=stream_callback,
                                temperature=temperature,
                                max_tokens=max_tokens,
                                request_extra=req_extra,
                            )
                        else:
                            return await provider_ref.chat(
                                messages=messages,
                                model=eff_model,
                                temperature=temperature,
                                max_tokens=max_tokens,
                                request_extra=req_extra,
                            )
                    logger.warning(
                        "[generate_response] provider %r: не удалось определить модель, fallback на LLMClient",
                        provider_ref.id,
                    )
            except Exception:
                logger.exception("[generate_response] provider resolve skipped")
            health = await self.client.health_check(host_id=hid)
            llm_ready = health.get("status") == "healthy"
            if str(model_path or "").strip():
                logger.debug(
                    f"[generate_response] model_path={model_path!r} → host={hid!r} model={model_to_use!r} (llm-svc RAM ok: {llm_ready}, кэш имени: {self.model_name!r})"
                )
            in_pool = pool_contains_model(health, model_to_use)
            if model_to_use and in_pool:
                self.model_name = model_to_use
            elif allow_hot_swap_load and model_to_use:
                # Важно: не опираться на кэш self.model_name — после rebuild пул пуст,
                # а имя в кэше/settings может совпадать → раньше пропускали load → 500.
                async with self._model_switch_lock:
                    health2 = await self.client.health_check(host_id=hid)
                    if pool_contains_model(health2, model_to_use):
                        self.model_name = model_to_use
                    else:
                        logger.info(
                            f"[generate_response] Загрузка/переключение llm-svc на модель: {model_to_use!r} host={hid!r} (pool={health2.get('loaded_models')!r})"
                        )
                        ok = await self.client.load_model_if_needed(
                            model_to_use, host_id=hid
                        )
                        if ok:
                            self.model_name = model_to_use
                            logger.debug(
                                f"[generate_response] llm-svc модель активна: {self.model_name!r}"
                            )
                        else:
                            logger.warning(
                                f"[generate_response] Не удалось загрузить llm-svc {model_to_use!r}; кэш имени: {self.model_name!r}"
                            )
                            # Не подставляем устаревший кэш: лучше явная ошибка, чем 500 от пустого пула.
                            raise RuntimeError(
                                f"Модель {model_to_use!r} не загружена в llm-svc после пересборки. "
                                "Дождитесь готовности сервиса моделей или выберите модель вручную."
                            )
            if streaming and stream_callback:
                log_cef_int003_llm_request(
                    base_url=self.client._url_for_llm_host(hid),
                    provider_id=str(hid),
                    model=model_to_use,
                    request_uuid=cef_llm_rid,
                )
                return await self._stream_generation(
                    messages,
                    temperature,
                    max_tokens,
                    stream_callback,
                    model_to_use,
                    host_id=hid,
                    request_extra=req_extra,
                    cef_correlation_id=cef_llm_rid,
                    cef_service_name=f"llm-svc-{hid}",
                )
            else:
                logger.debug(
                    f"[generate_response] Запрос к LLM микросервису (host={hid!r})..."
                )
                log_cef_int003_llm_request(
                    base_url=self.client._url_for_llm_host(hid),
                    provider_id=str(hid),
                    model=model_to_use,
                    request_uuid=cef_llm_rid,
                )
                try:
                    response = await self.client.chat_completion(
                        messages=messages,
                        model=model_to_use,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        stream=False,
                        host_id=hid,
                        request_extra=req_extra,
                    )
                except httpx.HTTPStatusError as he:
                    log_cef_int006_llm_api_failure(
                        request_uuid=cef_llm_rid,
                        code_status=he.response.status_code,
                        text_status=(he.response.text or "")[:512],
                        service_name=f"llm-svc-{hid}",
                        status_code=he.response.status_code,
                    )
                    raise
                if "choices" in response and len(response["choices"]) > 0:
                    message = response["choices"][0].get("message") or {}
                    logger.debug(
                        "[generate_response] non-stream message keys=%s has_reasoning_content=%s has_reasoning=%s",
                        sorted(list(message.keys())),
                        "reasoning_content" in message,
                        "reasoning" in message,
                    )
                    content = _clean_llm_response(
                        _normalize_content_payload(message.get("content"))
                    )
                    reasoning = _normalize_reasoning_payload(
                        message.get("reasoning_content") or message.get("reasoning")
                    ).strip()
                    from backend.llm_providers.routing import is_thinking_requested

                    thinking_requested = is_thinking_requested(req_extra)
                    if thinking_requested and reasoning and ("<think>" not in content):
                        content = f"<think>{reasoning}</think>\n\n{content}"
                    elif not thinking_requested and _has_think_markers(content):
                        content = _strip_think_tags(content)
                    logger.debug(
                        f"[generate_response] Ответ получен ({len(content)} симв.)"
                    )
                    return content
                else:
                    logger.error(f"[generate_response] Ошибка формата: {response}")
                    log_cef_int006_llm_api_failure(
                        request_uuid=cef_llm_rid,
                        code_status="FORMAT",
                        text_status=str(response)[:512],
                        service_name=f"llm-svc-{hid}",
                        status_code=200,
                    )
                    return "Ошибка генерации ответа"
        except httpx.HTTPStatusError:
            raise
        except Exception as e:
            logger.exception("Ошибка generate_response")
            log_cef_int006_llm_api_failure(
                request_uuid=cef_llm_rid,
                code_status="EXCEPTION",
                text_status=str(e)[:512],
                service_name="llm-svc",
            )
            return LLM_GENERIC_ERROR_TEXT

    async def _stream_generation(
        self,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
        stream_callback: Callable[..., bool],
        model_name: Optional[str] = None,
        host_id: Optional[str] = None,
        request_extra: Optional[Dict[str, Any]] = None,
        cef_correlation_id: Optional[str] = None,
        cef_service_name: Optional[str] = None,
    ) -> str:
        """Потоковая генерация с парсингом SSE"""
        accumulated_text = ""
        reasoning_accumulated = ""
        logged_delta_shape = False
        try:
            base = self.client._url_for_llm_host(host_id)
            logger.debug(
                f"[_stream_generation] Старт потока... host={host_id or self.client.default_llm_host!r}"
            )
            payload: Dict[str, Any] = {
                "model": model_name or self.model_name,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": True,
            }
            if request_extra:
                for k, v in request_extra.items():
                    if v is not None:
                        payload[k] = v
            from backend.llm_providers.routing import is_thinking_requested

            thinking_requested = is_thinking_requested(request_extra)
            logger.debug(
                "[_stream_generation] POST /v1/chat/completions host=%r model=%r enable_thinking=%r",
                host_id,
                payload.get("model"),
                thinking_requested,
            )
            headers = {**self.client._get_headers(), "Accept": "text/event-stream"}
            # Раньше 300с idle read обрывал длинные ответы. Берём timeout клиента
            # (часто 3600) с override LLM_STREAM_READ_TIMEOUT.
            try:
                from backend.llm_providers.openai_compat import _stream_read_timeout_sec

                stream_read_timeout = _stream_read_timeout_sec(
                    float(getattr(self.client, "timeout", 3600) or 3600)
                )
            except Exception:
                stream_read_timeout = float(
                    getattr(self.client, "timeout", 3600) or 3600
                )
            request_timeout = httpx.Timeout(
                stream_read_timeout, connect=10.0, read=stream_read_timeout, write=10.0
            )
            async with httpx.AsyncClient(
                verify=self.client._verify, timeout=request_timeout
            ) as client:
                async with client.stream(
                    "POST", f"{base}/v1/chat/completions", headers=headers, json=payload
                ) as response:
                    try:
                        response.raise_for_status()
                    except httpx.HTTPStatusError as he:
                        if cef_correlation_id:
                            r = he.response
                            _ts = (
                                getattr(r, "reason_phrase", None) or str(r.status_code)
                            )[:512]
                            log_cef_int006_llm_api_failure(
                                request_uuid=cef_correlation_id,
                                code_status=r.status_code,
                                text_status=_ts,
                                service_name=cef_service_name or "llm-svc",
                                status_code=r.status_code,
                            )
                        raise
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            data_str = line[6:]
                            if data_str.strip() == "[DONE]":
                                break
                            try:
                                data = json.loads(data_str)
                                if "choices" in data and len(data["choices"]) > 0:
                                    delta = data["choices"][0].get("delta", {})
                                    if not logged_delta_shape:
                                        logged_delta_shape = True
                                        logger.debug(
                                            "[_stream_generation] first delta keys=%s has_reasoning_fields=%s has_content_fields=%s",
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
                                            any(
                                                (
                                                    k in delta
                                                    for k in (
                                                        "content",
                                                        "text",
                                                        "output_text",
                                                        "message",
                                                    )
                                                )
                                            ),
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
                                    reasoning_chunk = _normalize_reasoning_payload(
                                        reasoning_source
                                    )
                                    if thinking_requested and reasoning_chunk:
                                        reasoning_accumulated += reasoning_chunk
                                        if (
                                            _invoke_stream_callback_safe(
                                                stream_callback,
                                                reasoning_chunk,
                                                reasoning_accumulated,
                                                "reasoning",
                                            )
                                            is False
                                        ):
                                            logger.debug(
                                                "[_stream_generation] Прервано колбэком (reasoning)"
                                            )
                                            return (
                                                _clean_llm_response(accumulated_text)
                                                if accumulated_text
                                                else ""
                                            )
                                    chunk = _normalize_content_payload(
                                        delta.get("content")
                                        or delta.get("text")
                                        or delta.get("output_text")
                                        or delta.get("message")
                                    )
                                    if chunk:
                                        accumulated_text += chunk
                                        if (
                                            "<|im_start|>" in accumulated_text
                                            or "<|im_end|>" in accumulated_text
                                        ):
                                            logger.debug(
                                                "[_stream_generation] Обнаружен im_start/im_end тег, обрезаем"
                                            )
                                            break
                                        cb_accumulated = accumulated_text
                                        if (
                                            not thinking_requested
                                            and _has_think_markers(accumulated_text)
                                        ):
                                            cb_accumulated = _strip_think_tags(
                                                accumulated_text
                                            )
                                        if (
                                            _invoke_stream_callback_safe(
                                                stream_callback,
                                                chunk,
                                                cb_accumulated,
                                                "content",
                                            )
                                            is False
                                        ):
                                            logger.debug(
                                                "[_stream_generation] Прервано колбэком"
                                            )
                                            return (
                                                _clean_llm_response(accumulated_text)
                                                if accumulated_text
                                                else ""
                                            )
                                    # Конец стрима по finish_reason (length = лимит токенов и т.п.).
                                    # Без этого при отсутствии [DONE] зависаем на read-timeout,
                                    # а фронт держит кнопку «Остановить» часами.
                                    choice0 = data["choices"][0]
                                    finish_reason = choice0.get(
                                        "finish_reason"
                                    ) or choice0.get("stop_reason")
                                    if finish_reason:
                                        _fr = str(finish_reason).lower()
                                        if _fr not in (
                                            "stop",
                                            "tool_calls",
                                            "function_call",
                                        ):
                                            # length = упёрлись в max_tokens: самая частая
                                            # причина обрыва презентаций на середине. Логируем
                                            # как WARNING, чтобы было видно в проде.
                                            _has_slide = (
                                                'class="slide"' in accumulated_text
                                                or "class='slide'" in accumulated_text
                                                or "content-zone" in accumulated_text
                                            )
                                            _log = (
                                                logger.warning
                                                if _fr == "length"
                                                else logger.info
                                            )
                                            _log(
                                                "[_stream_generation] ОБРЫВ стрима: finish_reason=%r "
                                                "накоплено=%s симв., запрошено max_tokens=%s"
                                                "%s",
                                                finish_reason,
                                                len(accumulated_text),
                                                max_tokens,
                                                (
                                                    " | похоже на GPB-презентацию "
                                                    "(есть .slide/content-zone) — вероятен обрыв колоды!"
                                                    if _has_slide
                                                    else ""
                                                ),
                                            )
                                        else:
                                            logger.debug(
                                                "[_stream_generation] finish_reason=%r (накоплено %s симв.)",
                                                finish_reason,
                                                len(accumulated_text),
                                            )
                                        break
                            except json.JSONDecodeError:
                                continue
            cleaned = _clean_llm_response(accumulated_text)
            if thinking_requested:
                return cleaned
            if _has_think_markers(cleaned):
                return _strip_think_tags(cleaned)
            if reasoning_accumulated.strip() and "<think>" not in cleaned:
                return f"<think>{reasoning_accumulated.strip()}</think>\n\n{cleaned}"
            return cleaned
        except httpx.HTTPStatusError:
            raise
        except Exception as e:
            logger.exception("Ошибка _stream_generation")
            import traceback

            logger.exception(traceback.format_exc())
            if cef_correlation_id:
                log_cef_int006_llm_api_failure(
                    request_uuid=cef_correlation_id,
                    code_status="STREAM",
                    text_status=str(e)[:512],
                    service_name=cef_service_name or "llm-svc",
                )
            return LLM_STREAM_ERROR_TEXT

    async def synthesize_speech(self, *args, **kwargs):
        return await self.client.synthesize_speech(*args, **kwargs)

    async def transcribe_audio_whisperx(self, *args, **kwargs):
        return await self.client.transcribe_audio_whisperx(*args, **kwargs)

    async def diarize_audio(self, *args, **kwargs):
        return await self.client.diarize_audio(*args, **kwargs)

    async def transcribe_with_diarization(self, *args, **kwargs):
        return await self.client.transcribe_with_diarization(*args, **kwargs)

    async def recognize_text_from_image(self, *args, **kwargs):
        return await self.client.recognize_text_from_image(*args, **kwargs)

    async def get_audio_services_health(self) -> Dict[str, Any]:
        """Сборное здоровье аудио-сервисов"""
        try:
            v_h = await self.client.get_transcription_health()
            t_h = await self.client.get_tts_health()
            return {
                "transcription": v_h,
                "tts": t_h,
                "overall": (
                    "healthy"
                    if v_h.get("status") == "healthy" and t_h.get("status") == "healthy"
                    else "unhealthy"
                ),
            }
        except Exception:
            logger.exception("Ошибка операции")
            return {"overall": "unhealthy"}


llm_service = None


async def get_llm_service() -> LLMService:
    global llm_service
    if llm_service is None:
        llm_service = LLMService()
        await llm_service.initialize()
    return llm_service


def ask_agent_llm_svc(
    prompt: str,
    history: Optional[List[Dict[str, str]]] = None,
    max_tokens: Optional[int] = None,
    streaming: bool = False,
    stream_callback: Optional[Callable[..., bool]] = None,
    model_path: Optional[str] = None,
    custom_prompt_id: Optional[str] = None,
    images: Optional[List[str]] = None,
    system_prompt: Optional[str] = None,
    temperature: Optional[float] = None,
    enable_thinking: Optional[bool] = None,
) -> str:
    """Синхронная обертка с защитой event loop"""
    logger.debug(
        f"[ask_agent_llm_svc] Called with prompt len: {len(prompt)}, streaming: {streaming}"
    )

    async def _async_generate():
        logger.debug("[ask_agent_llm_svc] _async_generate started")
        try:
            service = await get_llm_service()
            logger.debug("[ask_agent_llm_svc] Service obtained")
            result = await service.generate_response(
                prompt=prompt,
                history=history,
                max_tokens=max_tokens or 1024,
                streaming=streaming,
                stream_callback=stream_callback,
                images=images,
                model_path=model_path,
                system_prompt=system_prompt,
                enable_thinking=enable_thinking,
                temperature=temperature if temperature is not None else 0.7,
            )
            logger.debug("[ask_agent_llm_svc] generate_response completed")
            return result
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 503:
                logger.warning(
                    "[ask_agent_llm_svc] LLM service busy or reinitializing (503)"
                )
                return "Сервис модели занят или перезагружается. Повторите запрос через несколько секунд."
            logger.exception("[ask_agent_llm_svc] HTTP error")
            raise
        except Exception:
            logger.exception("[ask_agent_llm_svc] Error in _async_generate")
            raise

    try:
        asyncio.get_running_loop()
        import concurrent.futures

        _cef_ctx = contextvars.copy_context()

        def _run_async_generate_in_ctx():
            return asyncio.run(_async_generate())

        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(_cef_ctx.run, _run_async_generate_in_ctx)
            try:
                return future.result(timeout=120)
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 503:
                    return "Сервис модели занят или перезагружается. Повторите запрос через несколько секунд."
                raise
            except Exception:
                logger.exception("[ask_agent_llm_svc] Error in executor")
                return "Ошибка при обращении к модели."
    except RuntimeError:
        return asyncio.run(_async_generate())


def _wrap_sync(coro):
    """Универсальный синхронный запуск"""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as ex:
                return ex.submit(asyncio.run, coro).result()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


def synthesize_speech_llm_svc(text: str, **kwargs) -> bytes:

    async def _call():
        s = await get_llm_service()
        return await s.synthesize_speech(text, **kwargs)

    return _wrap_sync(_call())


def transcribe_audio_whisperx_llm_svc(audio_file: bytes, **kwargs) -> str:

    async def _call():
        s = await get_llm_service()
        r = await s.transcribe_audio_whisperx(audio_file, **kwargs)
        return r.get("text", "") if isinstance(r, dict) else ""

    return _wrap_sync(_call())


def diarize_audio_llm_svc(audio_file: bytes, **kwargs) -> Dict[str, Any]:

    async def _call():
        s = await get_llm_service()
        return await s.diarize_audio(audio_file, **kwargs)

    return _wrap_sync(_call())


def transcribe_with_diarization_llm_svc(audio_file: bytes, **kwargs) -> Dict[str, Any]:

    async def _call():
        s = await get_llm_service()
        return await s.transcribe_with_diarization(audio_file, **kwargs)

    return _wrap_sync(_call())


def recognize_text_from_image_llm_svc(image_file: bytes, **kwargs) -> Dict[str, Any]:

    async def _call():
        s = await get_llm_service()
        return await s.recognize_text_from_image(image_file, **kwargs)

    return _wrap_sync(_call())


def _resize_for_vision(image_bytes: bytes, max_side: int = 2048) -> bytes:
    """Уменьшает картинку до оптимального размера для её отправки в OCR"""
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    w, h = img.size
    scale = min(1.0, max_side / max(w, h))
    if scale < 1.0:
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", optimize=True, quality=85)
    return buf.getvalue()
