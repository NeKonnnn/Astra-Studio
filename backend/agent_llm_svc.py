"""
AstraChat Agent с поддержкой llm-svc
Модифицированная версия agent.py для работы через llm-svc API
"""

import asyncio
import json
import os

from backend.config import get_path
from backend.context_prompts import context_prompt_manager
from backend.llm_client import (
    ask_agent_llm_svc,
    get_llm_service,
    resolve_llm_host_and_model_for_svc,
    resolve_llm_svc_model_id_for_request,
)
from backend.settings.logging import (
    get_logger,
    reconfigure_backend_handler_streams_utf8,
)
from backend.utils.encoding_fix import fix_windows_encoding
from backend.utils.llm_defaults import (
    is_settings_key,
    llm_settings_defaults,
    llm_settings_max_values,
)
from backend.utils.safe_paths import (
    llm_settings_file_fallbacks,
    llm_settings_seed_paths,
    resolve_llm_settings_file_path,
)
from backend.services.user_llm_settings import get_active_model_settings

logger = get_logger(__name__)

fix_windows_encoding()
reconfigure_backend_handler_streams_utf8()

MODEL_PATH = get_path("model_path")


class ModelSettings:

    def __init__(self):
        self.settings_file = str(resolve_llm_settings_file_path())
        # Заводские значения — из seed-файла llm_settings.json, не из хардкода.
        # Отсюда же берёт reset_to_defaults, поэтому «сброс до заводских» в UI
        # тоже идёт по файлу.
        self.default_settings = llm_settings_defaults()
        self.settings = self.default_settings.copy()
        self.load_settings()

    def _settings_load_candidates(self) -> list[str]:
        candidates: list[str] = []
        for path in [self.settings_file, *[str(p) for p in llm_settings_seed_paths()]]:
            abs_path = os.path.abspath(path)
            if abs_path not in candidates:
                candidates.append(abs_path)
        return candidates

    def load_settings(self):
        """
        Загрузка настроек: сначала writable-файл, иначе seed из образа.
        """
        loaded = False
        for path in self._settings_load_candidates():
            if not os.path.isfile(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    loaded_settings = json.load(f)
                if isinstance(loaded_settings, dict):
                    # Только известные ключи: служебные (_max_values, _comment)
                    # настройками не являются и в writable-файл попадать не должны.
                    self.settings.update(
                        {
                            k: v
                            for k, v in loaded_settings.items()
                            if is_settings_key(k) and k in self.default_settings
                        }
                    )
                    loaded = True
                if os.path.abspath(path) == os.path.abspath(self.settings_file):
                    break
            except Exception:
                logger.exception("Ошибка загрузки настроек модели из %s", path)
        if loaded:
            logger.info("Настройки модели загружены (файл: %s)", self.settings_file)
        else:
            logger.info(
                "Файл настроек модели не найден, используются значения по умолчанию"
            )

    def save_settings(self) -> bool:
        """Сохранение настроек в writable-путь с fallback."""
        last_error: Exception | None = None
        for target in llm_settings_file_fallbacks(self.settings_file):
            target_str = str(target)
            try:
                parent = os.path.dirname(target_str) or "."
                os.makedirs(parent, exist_ok=True)
                with open(target_str, "w", encoding="utf-8") as f:
                    json.dump(self.settings, f, indent=2, ensure_ascii=False)
                if target_str != self.settings_file:
                    logger.warning(
                        "llm_settings.json: переключение на fallback путь %s (primary=%s)",
                        target_str,
                        self.settings_file,
                    )
                    self.settings_file = target_str
                logger.info("Настройки модели сохранены в %s", self.settings_file)
                return True
            except OSError as exc:
                last_error = exc
                logger.warning(
                    "Не удалось записать llm_settings в %s: %s", target_str, exc
                )
            except Exception as exc:
                last_error = exc
                logger.exception(
                    "Ошибка при сохранении настроек модели в %s", target_str
                )
        if last_error is not None:
            logger.error(
                "Не удалось сохранить llm_settings ни в один путь: %s", last_error
            )
        return False

    def get(self, key, default=None):
        """Получение значения настройки"""
        return self.settings.get(key, default)

    def set(self, key, value, *, persist: bool = True):
        """Установка значения настройки"""
        if key in self.settings:
            self.settings[key] = value
            if persist:
                return self.save_settings()
            return True
        return False

    def update_many(self, updates: dict, *, persist: bool = True) -> bool:
        changed = False
        for key, value in updates.items():
            if key in self.settings:
                self.settings[key] = value
                changed = True
        if not changed:
            return False
        if persist:
            return self.save_settings()
        return True

    def reset_to_defaults(self):
        """Сброс настроек к рекомендуемым значениям по умолчанию"""
        self.settings = self.default_settings.copy()
        self.save_settings()
        logger.info("Настройки сброшены к рекомендуемым значениям")

    def get_recommended_settings(self):
        """Получение рекомендуемых настроек (без применения)"""
        return self.default_settings.copy()

    def get_max_values(self):
        """Максимумы для UI - блок _max_values в llm_settings.json.
        Гарантированно не ниже дефолтов.
        """
        return llm_settings_max_values()

    def get_all(self):
        """Получение всех настроек"""
        return self.settings.copy()


model_settings = ModelSettings()
MODEL_CONTEXT_SIZE = model_settings.get("context_size")
DEFAULT_OUTPUT_TOKENS = model_settings.get("output_tokens")
VERBOSE_OUTPUT = model_settings.get("verbose")
USE_LLM_SVC = True


def initialize_model():
    """Инициализация модели (теперь через llm-svc)"""
    if USE_LLM_SVC:
        logger.debug("Инициализация через llm-svc...")
        try:
            import asyncio

            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(asyncio.run, get_llm_service())
                    future.result()
            else:
                loop.run_until_complete(get_llm_service())
            logger.info("llm-svc сервис инициализирован успешно")
            return True
        except Exception:
            logger.exception("Ошибка инициализации llm-svc")
            return False
    else:
        logger.debug("Используется оригинальная инициализация модели")
        return True


def update_model_settings(new_settings):
    """Обновление настроек модели"""
    global model_settings, MODEL_CONTEXT_SIZE, DEFAULT_OUTPUT_TOKENS, VERBOSE_OUTPUT
    ok = model_settings.update_many(new_settings)
    MODEL_CONTEXT_SIZE = model_settings.get("context_size")
    DEFAULT_OUTPUT_TOKENS = model_settings.get("output_tokens")
    VERBOSE_OUTPUT = model_settings.get("verbose")
    if ok:
        logger.info("Настройки модели обновлены")
    else:
        logger.warning(
            "Не удалось сохранить настройки модели (ключи: %s)",
            list(new_settings.keys()),
        )
    return ok


_selected_model_name = None


def reload_model_by_path(model_path):
    """Перезагрузка модели с новым файлом модели (через llm-svc)"""
    global _selected_model_name
    if USE_LLM_SVC:
        try:

            async def _ensure_for_provider_path():
                from backend.llm_providers import get_registry

                registry = await get_registry()
                provider, model_id = registry.resolve(model_path)
                raw_path = str(model_path or "").strip()
                if not model_id:
                    if raw_path and registry.contains(raw_path):
                        provider = registry.get(raw_path)
                        candidates = await provider.list_models()
                        if not candidates:
                            return False
                        model_id = str(candidates[0].model_id or "").strip()
                        if not model_id:
                            return False
                    else:
                        return False
                ok = await provider.ensure_model_loaded(model_id)
                if ok:
                    global _selected_model_name
                    _selected_model_name = f"{provider.id}/{model_id}"
                return ok

            if (
                isinstance(model_path, str)
                and model_path.strip()
                and (not model_path.startswith("llm-svc://"))
            ):
                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                if loop.is_running():
                    import concurrent.futures

                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        future = executor.submit(
                            asyncio.run, _ensure_for_provider_path()
                        )
                        return future.result()
                return loop.run_until_complete(_ensure_for_provider_path())
        except Exception:
            logger.exception("provider-path reload fallback to llm-svc logic")
        if os.path.isdir(model_path):
            logger.warning(
                f"Передан путь к директории вместо файла модели: {model_path}. Пропускаем загрузку."
            )
            return False
        if model_path.startswith("llm-svc://"):
            model_name = model_path.replace("llm-svc://", "").strip()
            if not model_name:
                logger.warning("llm-svc: пустое имя модели в пути")
                return False
            _selected_model_name = model_name
            try:

                async def _load_on_llm_svc():
                    service = await get_llm_service()
                    hid, mid = resolve_llm_host_and_model_for_svc(
                        model_path,
                        service.model_name,
                        service.client.llm_hosts,
                        service.client.default_llm_host,
                    )
                    if not mid:
                        logger.warning(
                            "llm-svc: не удалось определить id модели из пути"
                        )
                        return False
                    ok = await service.client.load_model_if_needed(mid, host_id=hid)
                    if ok:
                        service.model_name = mid
                        logger.info(
                            f"[llm-svc] Обновлён model_name в бэкенде: {mid!r} (host={hid!r})"
                        )
                    return ok

                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                if loop.is_running():
                    import concurrent.futures

                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        future = executor.submit(asyncio.run, _load_on_llm_svc())
                        return future.result()
                else:
                    return loop.run_until_complete(_load_on_llm_svc())
            except Exception:
                logger.exception("Ошибка переключения модели в llm-svc")
                return False
        if os.path.exists(model_path) and model_path.endswith(".gguf"):
            logger.warning(
                f"Передан путь к локальному файлу модели {model_path}, но используется llm-svc. Модель должна быть доступна через llm-svc."
            )
            return True
        resolved_id = resolve_llm_svc_model_id_for_request(model_path, "")
        if resolved_id:
            try:

                async def _load_resolved():
                    service = await get_llm_service()
                    hid, mid = resolve_llm_host_and_model_for_svc(
                        model_path,
                        service.model_name,
                        service.client.llm_hosts,
                        service.client.default_llm_host,
                    )
                    use_id = mid or resolved_id
                    ok = await service.client.load_model_if_needed(use_id, host_id=hid)
                    if ok:
                        service.model_name = use_id
                        global _selected_model_name
                        _selected_model_name = use_id
                        logger.debug(
                            f"[llm-svc] Multi-LLM: модель готова к вызову {resolved_id!r} (путь {model_path!r})"
                        )
                    return ok

                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                if loop.is_running():
                    import concurrent.futures

                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        future = executor.submit(asyncio.run, _load_resolved())
                        return future.result()
                return loop.run_until_complete(_load_resolved())
            except Exception:
                logger.exception("Ошибка load_model для %r", resolved_id)
                return False
        logger.info(f"Перезагрузка модели через llm-svc: {model_path}")
        logger.info(
            "Для смены модели в llm-svc обновите конфигурацию и перезапустите сервис"
        )
        return True
    else:
        logger.debug("Используется оригинальная перезагрузка модели")
        return True


def get_model_info():
    """Получение информации о текущей модели (через llm-svc)"""
    global _selected_model_name
    if USE_LLM_SVC:
        try:
            import asyncio

            async def _get_model_info_async():
                """Вспомогательная асинхронная функция для получения информации о модели"""
                service = await get_llm_service()
                ref = (
                    f"llm-svc://{_selected_model_name.strip()}"
                    if _selected_model_name
                    else None
                )
                hid, _ = resolve_llm_host_and_model_for_svc(
                    ref,
                    service.model_name,
                    service.client.llm_hosts,
                    service.client.default_llm_host,
                )
                health = await service.client.health_check(host_id=hid)
                return (service, health)

            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            if loop.is_running():
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(asyncio.run, _get_model_info_async())
                    service, health = future.result()
            else:
                service, health = loop.run_until_complete(_get_model_info_async())
            if health and health.get("status") == "healthy":
                if _selected_model_name:
                    model_name = _selected_model_name
                    model_path = f"llm-svc://{_selected_model_name}"
                else:
                    model_name = health.get("model_name") or getattr(
                        service, "model_name", "Unknown"
                    )
                    model_path = ""
                return {
                    "loaded": health.get("model_loaded", True),
                    "name": model_name if model_path else "Модель не загружена",
                    "metadata": {
                        "general.name": model_name,
                        "general.architecture": "LLM-SVC",
                        "general.size_label": "Unknown",
                    },
                    "path": model_path,
                    "n_ctx": MODEL_CONTEXT_SIZE,
                    "n_gpu_layers": 0,
                }
            else:
                return {"loaded": False, "error": "llm-svc недоступен", "path": ""}
        except Exception as e:
            logger.exception("Ошибка получения информации о модели")
            return {"loaded": False, "error": str(e), "path": ""}
    else:
        return {
            "loaded": True,
            "metadata": {"general.name": "Local Model"},
            "path": MODEL_PATH,
        }


def prepare_prompt(
    text, system_prompt=None, history=None, model_path=None, custom_prompt_id=None
):
    """Подготовка промпта в правильном формате с поддержкой истории диалога и контекстных промптов"""
    if system_prompt is None:
        if model_path:
            system_prompt = context_prompt_manager.get_effective_prompt(
                model_path, custom_prompt_id
            )
        else:
            system_prompt = context_prompt_manager.get_global_prompt()
    prompt_parts = []
    if system_prompt and system_prompt.strip():
        prompt_parts.append(f"<|im_start|>system\n{system_prompt}\n<|im_end|>")
    if history:
        for entry in history:
            role = entry.get("role", "user")
            content = entry.get("content", "")
            if role == "user":
                prompt_parts.append(f"<|im_start|>user\n{content}\n<|im_end|>")
            elif role == "assistant":
                prompt_parts.append(f"<|im_start|>assistant\n{content}\n<|im_end|>")
    prompt_parts.append(f"<|im_start|>user\n{text.strip()}\n<|im_end|>")
    prompt_parts.append("<|im_start|>assistant\n")
    return "".join(prompt_parts)


def ask_agent(
    prompt,
    history=None,
    max_tokens=None,
    streaming=False,
    stream_callback=None,
    model_path=None,
    custom_prompt_id=None,
    images=None,
    system_prompt=None,
    temperature=None,
    enable_thinking=None,
    service_call=False,
):
    """
    Единая точка LLM для legacy callers.

    ```service_call=True``` — ответ читает код, а не человек (исправление опечаток,
    multi-query, HyDE, суммаризация при индексации, judge). Для таких вызовов
    выключается мышление: reasoning-модель иначе тратит весь бюджет токенов на
    рассуждение, оно вырезается, и вызывающий получает пустую строку.

    - CORSUR / Phoenix / llm-svc в ``llm_providers`` → ProviderRegistry
    - ``llm-svc://…``, vision (images) → ``ask_agent_llm_svc`` / ``generate_response``
    - MCP agent loop всегда идёт через registry отдельно (не через эту функцию)
    """
    import httpx

    from backend.llm_providers.routing import (
        format_llm_http_error,
        registry_response_usable,
        should_use_llm_svc_direct,
    )

    eff_max_tokens = (
        max_tokens or get_active_model_settings().get("output_tokens") or 1024
    )
    eff_temperature = float(
        temperature
        if temperature is not None
        else get_active_model_settings().get("temperature") or 0.7
    )
    if system_prompt is None:
        try:
            ctx_prompt = (
                context_prompt_manager.get_effective_prompt(model_path)
                if model_path
                else context_prompt_manager.get_global_prompt()
            )
            ctx_prompt = (ctx_prompt or "").strip()
            system_prompt = ctx_prompt or None
        except Exception:
            logger.exception("ask_agent: не удалось получить контекстные инструкции")
    use_llm_svc_direct = should_use_llm_svc_direct(model_path=model_path, images=images)
    if not use_llm_svc_direct:
        try:
            from backend.mcp.orchestrator_bridge import sync_chat_via_registry

            registry_response = sync_chat_via_registry(
                prompt,
                history=history,
                model_path=model_path,
                streaming=streaming,
                stream_callback=stream_callback,
                max_tokens=eff_max_tokens,
                temperature=eff_temperature,
                system_prompt=system_prompt,
                enable_thinking=bool(enable_thinking),
                service_call=bool(service_call),
            )
            if registry_response_usable(registry_response):
                logger.debug("ask_agent: ProviderRegistry model_path=%s", model_path)
                return registry_response
            return (
                "Не удалось получить ответ от модели. Проверьте доступность провайдера."
            )
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "ask_agent: ProviderRegistry HTTP %s", exc.response.status_code
            )
            return format_llm_http_error(exc)
        except Exception:
            logger.exception("ask_agent: ProviderRegistry failed")
            return "Ошибка при обращении к модели."
    if USE_LLM_SVC:
        logger.debug(
            "ask_agent: llm-svc path model_path=%s images=%s", model_path, bool(images)
        )
        if max_tokens is None:
            max_tokens = get_active_model_settings().get("output_tokens")
        if temperature is None:
            temperature = float(get_active_model_settings().get("temperature") or 0.7)
        try:
            response = ask_agent_llm_svc(
                prompt=prompt,
                history=history,
                max_tokens=max_tokens,
                streaming=streaming,
                stream_callback=stream_callback,
                model_path=model_path,
                custom_prompt_id=custom_prompt_id,
                images=images,
                system_prompt=system_prompt,
                temperature=temperature,
                enable_thinking=enable_thinking,
            )
            if response is None:
                logger.warning("Генерация была отменена пользователем")
                return None
            return response
        except asyncio.CancelledError:
            logger.warning("Генерация была отменена (asyncio.CancelledError)")
            return None
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 503:
                return "Сервис модели занят или перезагружается. Повторите запрос через несколько секунд."
            return format_llm_http_error(exc)
        except Exception as e:
            logger.exception("Ошибка генерации через llm-svc")
            return "Извините, произошла ошибка при генерации ответа. Повторите запрос позже."
    else:
        logger.warning("llm-svc недоступен, используется fallback режим")
        return "llm-svc недоступен. Пожалуйста, запустите llm-svc сервис."


logger.debug("Модуль agent_llm_svc импортирован (инициализация отложена)")
