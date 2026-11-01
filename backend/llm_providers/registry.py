"""
Реестр LLM-провайдеров.

Читает конфиг из ``settings.llm_providers`` (новая секция) и/или
производит автомиграцию из legacy ``settings.llm_service.hosts``
(каждый host превращается в провайдера kind="llm-svc").

Использование::

    registry = await get_registry()
    provider = registry.get("local-llmsvc")
    provider_for_path = registry.resolve("local-llmsvc/qwen-coder-30b")  # -> (provider, "qwen-coder-30b")
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from backend.settings import get_settings
from backend.settings.logging import get_logger
from backend.settings.logging.errors import logged_suppress

from .anthropic import AnthropicProvider
from .base import LLMProvider, LLMProviderConfig, ModelInfo, split_model_path
from .litellm import LiteLLMProvider
from .llm_svc import LlmSvcProvider
from .ollama import OllamaProvider
from .openai_compat import OpenAICompatProvider
from .openai_native import OpenAIProvider, OpenRouterProvider
from .vllm import VLLMProvider

logger = get_logger(__name__)


def _normalize_provider_item(raw_item: Any) -> Any:
    """
    Нормализует dict-запись провайдера к формату LLMProviderConfig.

    Backward compatibility:
    - api_key_env (новое поле)
    - llm_key (legacy)
    - llm_key_<provider_id> (legacy, например llm_key_phoenix)
    """
    if not isinstance(raw_item, dict):
        return raw_item
    item = dict(raw_item)
    models_raw = item.get("models")
    if isinstance(models_raw, dict):
        names_raw = models_raw.get("name")
        if isinstance(names_raw, list):
            item["models"] = [str(x).strip() for x in names_raw if str(x or "").strip()]
        elif isinstance(names_raw, str) and names_raw.strip():
            item["models"] = [names_raw.strip()]
        else:
            item["models"] = []
    elif isinstance(models_raw, list):
        item["models"] = [str(x).strip() for x in models_raw if str(x or "").strip()]
    elif isinstance(models_raw, str) and models_raw.strip():
        item["models"] = [models_raw.strip()]
    if str(item.get("api_key_env") or "").strip():
        return item
    legacy_plain = str(item.get("llm_key") or "").strip()
    if legacy_plain:
        item["api_key_env"] = legacy_plain
        return item
    provider_id = str(item.get("id") or "").strip().lower()
    if provider_id:
        preferred_key = f"llm_key_{provider_id}"
        preferred_val = str(item.get(preferred_key) or "").strip()
        if preferred_val:
            item["api_key_env"] = preferred_val
            return item
    for key, value in item.items():
        if not re.fullmatch("llm_key(?:_[a-zA-Z0-9_-]+)?", str(key)):
            continue
        vv = str(value or "").strip()
        if vv:
            item["api_key_env"] = vv
            return item
    return item


def _build_provider(config: LLMProviderConfig) -> LLMProvider:
    """Инстанцирует провайдера по ``kind``."""
    kind = config.kind
    if kind == "llm-svc":
        return LlmSvcProvider(config)
    if kind == "vllm":
        return VLLMProvider(config)
    if kind == "ollama":
        return OllamaProvider(config)
    if kind == "litellm":
        return LiteLLMProvider(config)
    if kind == "openai":
        return OpenAIProvider(config)
    if kind == "openrouter":
        return OpenRouterProvider(config)
    if kind == "anthropic":
        return AnthropicProvider(config)
    if kind == "openai-compat":
        return OpenAICompatProvider(config)
    logger.warning("kind=%r неизвестен, используется OpenAICompatProvider", kind)
    return OpenAICompatProvider(config)


def _parse_bool_env(value: str, default: bool = True) -> bool:
    v = (value or "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")


def _parse_float_env(value: str, default: float) -> float:
    try:
        return float((value or "").strip())
    except Exception:
        logger.exception("Ошибка операции")
        return default


def _parse_provider_configs_from_prefixed_env() -> List[LLMProviderConfig]:
    """
    Формат:
      LLM_PROVIDER_<ID>_KIND=openai-compat
      LLM_PROVIDER_<ID>_BASE_URL=https://...
      LLM_PROVIDER_<ID>_ENABLED=true
      LLM_PROVIDER_<ID>_TIMEOUT=300
      LLM_PROVIDER_<ID>_API_KEY_ENV=LLM_API_KEY
      LLM_PROVIDER_<ID>_STATIC_MODEL=...
    """
    grouped: Dict[str, Dict[str, str]] = {}
    prefix = "LLM_PROVIDER_"
    supported_fields = (
        "KIND",
        "BASE_URL",
        "ENABLED",
        "TIMEOUT",
        "API_KEY_ENV",
        "STATIC_MODEL",
    )
    for key, raw_value in os.environ.items():
        if not key.startswith(prefix):
            continue
        rest = key[len(prefix) :]
        matched_field = None
        provider_id = None
        for field_name in supported_fields:
            suffix = f"_{field_name}"
            if rest.endswith(suffix):
                provider_id = rest[: -len(suffix)]
                matched_field = field_name
                break
        provider_id = (provider_id or "").strip()
        if not provider_id or not matched_field:
            continue
        grouped.setdefault(provider_id, {})[matched_field] = raw_value
    configs: List[LLMProviderConfig] = []
    for provider_id, fields in grouped.items():
        kind = (fields.get("KIND") or "").strip()
        base_url = (fields.get("BASE_URL") or "").strip()
        if not kind:
            logger.warning("LLM_PROVIDER_%s_* пропущен: не задан KIND", provider_id)
            continue
        try:
            cfg = LLMProviderConfig(
                id=provider_id,
                kind=kind,
                base_url=base_url,
                enabled=_parse_bool_env(fields.get("ENABLED", "true"), default=True),
                timeout=_parse_float_env(fields.get("TIMEOUT", "120"), default=120.0),
                api_key_env=(fields.get("API_KEY_ENV") or "").strip() or None,
                static_model=(fields.get("STATIC_MODEL") or "").strip() or None,
            )
            configs.append(cfg)
        except Exception:
            logger.exception("Некорректная запись LLM_PROVIDER__*")
    return configs


class ProviderRegistry:
    """Контейнер всех провайдеров с методами lookup/resolve."""

    def __init__(
        self,
        providers: Dict[str, LLMProvider],
        default_provider_id: str,
        configured_models_by_provider: Optional[Dict[str, List[str]]] = None,
    ) -> None:
        self._providers = providers
        self._default_id = default_provider_id
        self._configured_models_by_provider = configured_models_by_provider or {}
        self._provider_ids_by_model_name: Dict[str, List[str]] = {}
        for pid, model_names in self._configured_models_by_provider.items():
            if pid not in self._providers:
                continue
            for model_name in model_names:
                mid = str(model_name or "").strip()
                if not mid:
                    continue
                self._provider_ids_by_model_name.setdefault(mid, [])
                if pid not in self._provider_ids_by_model_name[mid]:
                    self._provider_ids_by_model_name[mid].append(pid)

    @property
    def default_id(self) -> str:
        return self._default_id

    def get(self, provider_id: Optional[str]) -> LLMProvider:
        """
        Возвращает провайдера по id. Если id не указан или не найден —
        возвращает default-провайдер.
        """
        pid = (provider_id or "").strip()
        if pid and pid in self._providers:
            return self._providers[pid]
        if not pid:
            return self._providers[self._default_id]
        logger.warning(
            "Провайдер %r не найден, используется default %r", pid, self._default_id
        )
        return self._providers[self._default_id]

    def contains(self, provider_id: Optional[str]) -> bool:
        pid = (provider_id or "").strip()
        return bool(pid) and pid in self._providers

    def all(self, *, include_disabled: bool = False) -> List[LLMProvider]:
        if include_disabled:
            return list(self._providers.values())
        return [p for p in self._providers.values() if p.enabled]

    def ids(self, *, include_disabled: bool = False) -> List[str]:
        return [p.id for p in self.all(include_disabled=include_disabled)]

    def resolve(self, path: Optional[str]) -> Tuple[LLMProvider, str]:
        """
        ``<provider_id>/<model_id>`` → (provider, model_id).

        Legacy ``llm-svc://host/model`` тоже поддерживаем — ``host`` считаем
        id провайдера (auto-migration оставляет те же id). ``llm-svc://model``
        без host → default-провайдер.

        Плоский ``model_id`` без ``/`` → default-провайдер.

        Пустой path → (default_provider, "") — вызывающая сторона сама решит,
        что делать с пустой моделью (обычно: использовать default-модель
        провайдера).
        """
        head, tail = split_model_path(path or "")
        raw = str(path or "").strip()
        if head and self.contains(head):
            return (self._providers[head], tail)
        if not head and raw:
            matched_provider_ids = self._provider_ids_by_model_name.get(raw, [])
            if len(matched_provider_ids) == 1:
                pid = matched_provider_ids[0]
                logger.debug(
                    "resolve(%r): уникальное совпадение по конфигу models -> provider=%r",
                    path,
                    pid,
                )
                return (self._providers[pid], raw)
            if len(matched_provider_ids) > 1:
                logger.warning(
                    "resolve(%r): модель неоднозначна в конфиге models (%s), используется default %r",
                    path,
                    matched_provider_ids,
                    self._default_id,
                )
        if head and (not self.contains(head)):
            logger.warning(
                "resolve(%r): провайдер %r не зарегистрирован, используется default %r",
                path,
                head,
                self._default_id,
            )
            combined = f"{head}/{tail}" if tail else head
            return (self._providers[self._default_id], combined)
        return (self._providers[self._default_id], tail)

    async def initialize(self) -> None:
        """Инициализация всех провайдеров (health probe, sync model_name и т.д.)."""
        for p in self._providers.values():
            try:
                await p.initialize()
            except Exception:
                logger.exception("Provider  initialize() error")

    async def close(self) -> None:
        for p in self._providers.values():
            try:
                await p.close()
            except Exception:
                logger.exception("Provider  close() error")

    async def list_all_models(self) -> List[ModelInfo]:
        """Объединённый список моделей всех enabled-провайдеров."""
        results: List[ModelInfo] = []
        for p in self.all():
            try:
                models = await p.list_models()
                results.extend(models)
            except Exception:
                logger.exception("list_models() error")
        return results


def _parse_configs_from_settings(
    settings: Any,
) -> Tuple[List[LLMProviderConfig], Optional[str]]:
    """
    Достаёт список конфигов провайдеров из объекта Settings.

    Приоритет:
      1. ``LLM_PROVIDER_<ID>_*`` из ENV.
      2. ``LLM_PROVIDERS_JSON`` из ENV.
      3. ``settings.llm_providers`` — новая секция, как есть.
      4. ``settings.llm_service.hosts`` — автомиграция: каждый host становится
         llm-svc-провайдером с id = host.id, base_url = host.base_url.
      5. Единственный URL ``settings.llm_service.base_url`` → один llm-svc
         с id="local-llmsvc".

    Возвращает (configs, default_provider_id_or_None).
    """
    configs: List[LLMProviderConfig] = []
    default_id: Optional[str] = None
    env_default = (os.getenv("DEFAULT_LLM_PROVIDER", "") or "").strip()
    if env_default:
        default_id = env_default
    prefixed_env_configs = _parse_provider_configs_from_prefixed_env()
    if prefixed_env_configs:
        configs.extend(prefixed_env_configs)
    providers_json = (os.getenv("LLM_PROVIDERS_JSON", "") or "").strip()
    if not configs and providers_json:
        try:
            raw_items = json.loads(providers_json)
            if not isinstance(raw_items, list):
                msg = "LLM_PROVIDERS_JSON должен быть JSON-массивом"
                raise TypeError(msg)
            for item in raw_items:
                try:
                    if isinstance(item, LLMProviderConfig):
                        configs.append(item)
                    elif isinstance(item, dict):
                        configs.append(
                            LLMProviderConfig(**_normalize_provider_item(item))
                        )
                    elif hasattr(item, "model_dump"):
                        configs.append(
                            LLMProviderConfig(
                                **_normalize_provider_item(item.model_dump())
                            )
                        )
                    else:
                        msg = f"Неподдерживаемый тип записи: {type(item)!r}"
                        raise ValueError(msg)
                except Exception:
                    logger.exception("Некорректная запись LLM_PROVIDERS_JSON:  ()")
        except Exception:
            logger.exception("Ошибка парсинга LLM_PROVIDERS_JSON: %s")
    new_section = getattr(settings, "llm_providers", None)
    if new_section:
        items = list(new_section) if isinstance(new_section, list) else []
        for item in items:
            try:
                if isinstance(item, LLMProviderConfig):
                    configs.append(item)
                elif isinstance(item, dict):
                    configs.append(LLMProviderConfig(**_normalize_provider_item(item)))
                elif hasattr(item, "model_dump"):
                    configs.append(
                        LLMProviderConfig(**_normalize_provider_item(item.model_dump()))
                    )
            except Exception:
                logger.exception("Некорректная запись llm_providers:  ()")
        default_id = default_id or (
            getattr(settings, "default_llm_provider", None) or None
        )
    if not configs:
        llm_service = getattr(settings, "llm_service", None)
        if llm_service is not None:
            hosts = getattr(llm_service, "hosts", None) or []
            default_host_id = (
                getattr(llm_service, "default_host_id", None) or ""
            ).strip() or None
            timeout = float(getattr(llm_service, "timeout", 120.0) or 120.0)
            for h in hosts:
                hid, burl = _host_entry_to_tuple(h)
                if not hid or not burl:
                    continue
                try:
                    configs.append(
                        LLMProviderConfig(
                            id=hid,
                            kind="llm-svc",
                            base_url=burl,
                            timeout=timeout,
                            enabled=True,
                        )
                    )
                except Exception:
                    logger.exception("Автомиграция host=%r base_url=%r")
            if default_host_id and default_host_id in {c.id for c in configs}:
                default_id = default_host_id
            elif configs:
                default_id = configs[0].id
            if not configs:
                burl = (getattr(llm_service, "base_url", "") or "").strip()
                if burl:
                    try:
                        configs.append(
                            LLMProviderConfig(
                                id="local-llmsvc",
                                kind="llm-svc",
                                base_url=burl,
                                timeout=timeout,
                                enabled=True,
                            )
                        )
                        default_id = "local-llmsvc"
                    except Exception:
                        logger.exception("Автомиграция single base_url=%r")
    seen: Dict[str, LLMProviderConfig] = {}
    for c in configs:
        if c.id in seen:
            logger.warning("Дубликат provider id=%r, оставляем первый", c.id)
            continue
        seen[c.id] = c
    configs = list(seen.values())
    if configs and (not default_id or default_id not in seen):
        default_id = configs[0].id
    return (configs, default_id)


def _host_entry_to_tuple(entry: Any) -> Tuple[Optional[str], Optional[str]]:
    if entry is None:
        return (None, None)
    if hasattr(entry, "id") and hasattr(entry, "base_url"):
        return (
            getattr(entry, "id", None) or None,
            getattr(entry, "base_url", None) or None,
        )
    if isinstance(entry, dict):
        return (entry.get("id"), entry.get("base_url"))
    return (None, None)


_registry: Optional[ProviderRegistry] = None
_registry_lock: asyncio.Lock = asyncio.Lock()


async def get_registry() -> ProviderRegistry:
    """
    Ленивая инициализация реестра. Повторные вызовы возвращают тот же
    экземпляр. Thread-safe в пределах одного event loop.
    """
    global _registry
    if _registry is not None:
        return _registry
    async with _registry_lock:
        if _registry is not None:
            return _registry
        _registry = await _build_from_settings()
    return _registry


async def _build_from_settings() -> ProviderRegistry:
    settings = get_settings()
    configs, default_id = _parse_configs_from_settings(settings)
    if not configs:
        msg = (
            "Нет настроенных LLM-провайдеров. Заполните секцию llm_providers: "
            "в backend/config/config.yml или microservices.llm.hosts."
        )
        raise RuntimeError(msg)
    providers: Dict[str, LLMProvider] = {}
    for c in configs:
        try:
            providers[c.id] = _build_provider(c)
            logger.info(
                "Provider registered: id=%s kind=%s base_url=%s enabled=%s",
                c.id,
                c.kind,
                c.base_url,
                c.enabled,
            )
        except Exception:
            logger.exception("Provider  build error")
    if not providers:
        msg = "Ни один провайдер не инициализирован."
        raise RuntimeError(msg)
    if not default_id or default_id not in providers:
        default_id = next(iter(providers.keys()))
    configured_models_by_provider = {
        c.id: [str(m).strip() for m in c.models or [] if str(m or "").strip()]
        for c in configs
    }
    registry = ProviderRegistry(
        providers,
        default_id,
        configured_models_by_provider=configured_models_by_provider,
    )
    await registry.initialize()
    logger.info(
        "ProviderRegistry готов: %d провайдеров, default=%s", len(providers), default_id
    )
    return registry


def get_registry_sync_or_none() -> Optional[ProviderRegistry]:
    """Без инициализации: если реестра нет — возвращает None (для diag-эндпоинтов)."""
    return _registry


async def reload_registry() -> ProviderRegistry:
    """Переинициализация (для hot-reload конфига, сейчас не используется)."""
    global _registry
    async with _registry_lock:
        old = _registry
        if old is not None:
            with logged_suppress(logger):
                await old.close()
        _registry = None
    return await get_registry()


def build_registry_debug_snapshot() -> Dict[str, Any]:
    """
    Диагностика источников конфигурации провайдеров.

    Не содержит секретов (только имена ENV и агрегированные признаки).
    """
    settings = get_settings()
    prefixed_cfgs = _parse_provider_configs_from_prefixed_env()
    prefixed_ids = [c.id for c in prefixed_cfgs]
    providers_json = (os.getenv("LLM_PROVIDERS_JSON", "") or "").strip()
    env_default = (os.getenv("DEFAULT_LLM_PROVIDER", "") or "").strip()
    raw_yaml = getattr(settings, "llm_providers", None)
    yaml_ids: List[str] = []
    if isinstance(raw_yaml, list):
        for item in raw_yaml:
            try:
                if isinstance(item, dict):
                    pid = str(item.get("id") or "").strip()
                else:
                    pid = str(getattr(item, "id", "") or "").strip()
                if pid:
                    yaml_ids.append(pid)
            except Exception:
                logger.exception("Ошибка операции")
                continue
    parsed_cfgs, parsed_default_id = _parse_configs_from_settings(settings)
    parsed_items = [
        {
            "id": c.id,
            "kind": c.kind,
            "enabled": c.enabled,
            "base_url": c.base_url,
            "api_key_env": c.api_key_env,
        }
        for c in parsed_cfgs
    ]
    existing_registry = get_registry_sync_or_none()
    runtime_registry = None
    if existing_registry is not None:
        runtime_registry = {
            "default_provider_id": existing_registry.default_id,
            "provider_ids": existing_registry.ids(include_disabled=True),
        }
    env_provider_keys = sorted(
        [k for k in os.environ.keys() if k.startswith("LLM_PROVIDER_")]
    )
    return {
        "env": {
            "default_llm_provider": env_default or None,
            "has_llm_providers_json": bool(providers_json),
            "prefixed_provider_ids": prefixed_ids,
            "prefixed_keys": env_provider_keys,
        },
        "yaml": {
            "default_llm_provider": getattr(settings, "default_llm_provider", None),
            "provider_ids": yaml_ids,
            "providers_count": len(yaml_ids),
        },
        "parsed": {"default_provider_id": parsed_default_id, "providers": parsed_items},
        "runtime_registry": runtime_registry,
    }
