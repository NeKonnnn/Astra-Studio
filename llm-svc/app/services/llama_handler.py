from llama_cpp import Llama
from typing import List, Dict, Any, Optional, Callable, AsyncGenerator, Union
from collections import OrderedDict
import inspect
import os
import json
import asyncio
import time
import logging

from app.models.schemas import ChatResponse, ChatChoice, Message, AssistantMessage, UsageInfo, SystemMessage, UserMessage
from app.utils import convert_to_dict_messages, format_messages_for_llama, estimate_tokens
from app.core.config import settings
from app.services.base_llm_handler import BaseLLMHandler
from app.utils.gguf_paths import resolve_gguf_path

logger = logging.getLogger(__name__)

# 27B Q8 (~30 ГБ) на CPU (gpu_layers=0) легко грузится дольше 10 мин.
MODEL_LOAD_TIMEOUT = int(os.environ.get("LLM_MODEL_LOAD_TIMEOUT", "1800"))
MAX_LOADED_MODELS = max(1, int(os.environ.get("LLM_MAX_LOADED_MODELS", "4")))
# Несколько независимых Llama на одну модель — параллельные стримы из разных чатов.
# Каждая реплика держит свой KV-кэш; веса GGUF обычно mmap-ятся с диска.
INFERENCE_REPLICAS = max(1, int(os.environ.get("LLM_INFERENCE_REPLICAS", "2")))


class _Slot:
    def __init__(self, llama: Llama, path: str):
        self.llama = llama
        self.path = path
        self.gen_lock = asyncio.Lock()


class LlamaHandler(BaseLLMHandler):
    _instance = None

    def __init__(self):
        super().__init__()
        # model_id → список реплик (у каждой свой gen_lock / Llama)
        self._model_slots: "OrderedDict[str, List[_Slot]]" = OrderedDict()
        self._primary_model_id: Optional[str] = None
        self._config_model_path = settings.model.path
        self._config_model_name = settings.model.name
        self.n_ctx = settings.model.ctx_size
        self.n_gpu_layers = settings.model.gpu_layers
        self.verbose = settings.model.verbose
        self._registry_lock = asyncio.Lock()
        self._model_switch_lock = asyncio.Lock()
        self.is_initialized = False

    def _replicas(self, model_id: str) -> List[_Slot]:
        return list(self._model_slots.get(model_id) or [])

    def _primary_slot(self, model_id: Optional[str] = None) -> Optional[_Slot]:
        mid = model_id or self._primary_model_id
        if mid and mid in self._model_slots and self._model_slots[mid]:
            return self._model_slots[mid][0]
        if self._model_slots:
            first_list = next(iter(self._model_slots.values()))
            if first_list:
                return first_list[0]
        return None

    async def _acquire_replica(self, model_id: str) -> _Slot:
        """Берёт свободную реплику модели; если все заняты — ждёт освобождения."""
        while True:
            replicas = self._replicas(model_id)
            if not replicas:
                raise ValueError(f"Model '{model_id}' is not loaded")
            free = [s for s in replicas if not s.gen_lock.locked()]
            if not free:
                await asyncio.sleep(0.02)
                continue
            # Берём первую свободную. Между проверкой и acquire возможен race —
            # тогда просто подождём этот lock (корректно, хоть и не оптимально).
            slot = free[0]
            await slot.gen_lock.acquire()
            return slot

    @property
    def model(self) -> Optional[Llama]:
        slot = self._primary_slot()
        return slot.llama if slot else None

    @property
    def model_path(self) -> Optional[str]:
        slot = self._primary_slot()
        if slot:
            return slot.path
        return self._config_model_path

    @property
    def model_name(self) -> str:
        if self._primary_model_id:
            return self._primary_model_id
        if self._model_slots:
            return next(iter(self._model_slots.keys()))
        return self._config_model_name

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @staticmethod
    def normalize_model_id(model_name: str) -> str:
        s = str(model_name).strip().replace("\\", "/")
        if not s:
            return ""
        if s.lower().endswith(".gguf"):
            s = s[:-5]
        # Абсолютный путь — только имя файла (legacy)
        if s.startswith("/") or (len(s) > 2 and s[1] == ":"):
            s = os.path.basename(s)
        return s

    def get_loaded_model_ids(self) -> List[str]:
        return list(self._model_slots.keys())

    def is_model_id_loaded(self, model_id: Optional[str]) -> bool:
        if not model_id or str(model_id).strip() in ("", "abstract-model"):
            return bool(self._model_slots)
        mid = self.normalize_model_id(model_id)
        if mid in self._model_slots:
            return True
        low = mid.lower()
        return any(k.lower() == low for k in self._model_slots.keys())

    def _resolve_slot_id(self, chat_model_id: Optional[str]) -> str:
        if not self._model_slots:
            raise ValueError("Model not loaded")
        raw = (chat_model_id or "").strip()
        if raw in ("", "abstract-model"):
            if self._primary_model_id and self._primary_model_id in self._model_slots:
                return self._primary_model_id
            return next(iter(self._model_slots.keys()))
        mid = self.normalize_model_id(raw)
        if mid in self._model_slots:
            return mid
        low = mid.lower()
        for k in self._model_slots.keys():
            if k.lower() == low:
                return k
        raise ValueError(
            f"Model '{chat_model_id}' is not loaded (loaded: {list(self._model_slots.keys())})"
        )

    def _touch_lru(self, slot_id: str) -> None:
        if slot_id in self._model_slots:
            self._model_slots.move_to_end(slot_id)

    async def _dispose_slot(self, slot: _Slot) -> None:
        try:
            if slot.llama is not None:
                del slot.llama
        except Exception as e:
            logger.warning(f"dispose_slot: {e}")
        slot.llama = None  # type: ignore

    def _build_llama_sync(self, model_path: str) -> Llama:
        return Llama(
            model_path=model_path,
            n_threads=7,
            n_threads_batch=7,
            n_ctx=self.n_ctx,
            n_gpu_layers=self.n_gpu_layers,
            use_mmap=True,
            verbose=True,
        )

    async def _instantiate_replicas(self, model_path: str, count: int) -> List[_Slot]:
        """Грузит до `count` независимых Llama; при OOM на N-й оставляет уже загруженные."""
        n = max(1, int(count))
        slots: List[_Slot] = []
        for i in range(n):
            try:
                llama = await self._instantiate_llama(model_path)
                slots.append(_Slot(llama, model_path))
            except Exception as e:
                if slots:
                    logger.warning(
                        "Replica %s/%s failed for %s (%s) — оставляем %s реплик(и)",
                        i + 1,
                        n,
                        model_path,
                        e,
                        len(slots),
                    )
                    break
                raise
        return slots

    async def _instantiate_llama(self, model_path: str) -> Llama:
        loop = asyncio.get_event_loop()
        load_task = loop.run_in_executor(None, lambda: self._build_llama_sync(model_path))
        return await asyncio.wait_for(load_task, timeout=MODEL_LOAD_TIMEOUT)

    def _get_llama_n_ctx(self, llama: Llama) -> int:
        n = getattr(llama, "n_ctx", None)
        if callable(n):
            try:
                n = n()
            except Exception:
                n = None
        if isinstance(n, int) and n > 0:
            return n
        return int(self.n_ctx)

    def _clamp_max_tokens_for_request(
        self,
        llama: Llama,
        messages: List[Message],
        max_tokens: int,
        slot_id: str,
    ) -> int:
        """
        llama.cpp падает, если prompt_tokens + max_tokens > n_ctx
        """
        n_ctx = self._get_llama_n_ctx(llama)
        formatted = format_messages_for_llama(convert_to_dict_messages(messages))
        try:
            prompt_tokens = len(llama.tokenize(formatted.encode("utf-8"), add_bos=True))
        except Exception as e:
            logger.debug("tokenize for max_tokens clamp failed, using estimate: %s", e)
            prompt_tokens = max(estimate_tokens(formatted), 1)
        reserved = 32
        room = n_ctx - prompt_tokens - reserved
        if room >= max_tokens:
            return max_tokens
        clamped = max(1, room)
        if clamped < max_tokens:
            logger.warning(
                "Clamping max_tokens %s -> %s (slot=%s n_ctx=%s ~prompt_tokens=%s)",
                max_tokens,
                clamped,
                slot_id,
                n_ctx,
                prompt_tokens,
            )
        return clamped

    def _count_prompt_tokens(self, llama: Llama, dict_msgs: List[Dict[str, Any]]) -> int:
        formatted = format_messages_for_llama(dict_msgs)
        try:
            return len(llama.tokenize(formatted.encode("utf-8"), add_bos=True))
        except Exception as e:
            logger.debug("tokenize failed: %s", e)
            return max(estimate_tokens(formatted), 1)

    def _dict_to_message(self, d: Dict[str, Any]) -> Message:
        role = str(d.get("role") or "user")
        content = d.get("content", "")
        if role == "system":
            return SystemMessage(content=str(content or ""))
        if role == "assistant":
            return AssistantMessage(content=str(content) if content is not None else None)
        return UserMessage(content=content if content is not None else "")

    def _fit_messages_to_context(
        self,
        llama: Llama,
        messages: List[Message],
        max_tokens: int,
        slot_id: str,
    ) -> List[Message]:
        """Обрезает историю/контент, чтобы prompt + max_tokens поместились в n_ctx."""
        n_ctx = self._get_llama_n_ctx(llama)
        reserved = 32
        min_gen = 64
        min_prompt = 512
        # Не резервировать под генерацию больше, чем позволяет окно (иначе budget промпта ~256 токенов)
        max_tokens = min(max_tokens, max(min_gen, n_ctx - min_prompt - reserved))
        budget = n_ctx - max_tokens - reserved

        dict_msgs = convert_to_dict_messages(messages)
        original_tokens = self._count_prompt_tokens(llama, dict_msgs)
        if original_tokens <= budget:
            return messages

        system = [m for m in dict_msgs if m.get("role") == "system"]
        rest = [m for m in dict_msgs if m.get("role") != "system"]

        while self._count_prompt_tokens(llama, system + rest) > budget and rest:
            if len(rest) > 1:
                rest.pop(0)
                continue
            m = dict(rest[0])
            content = m.get("content", "")
            if not isinstance(content, str):
                content = str(content or "")
            while content and self._count_prompt_tokens(
                llama, system + [{**m, "content": content}]
            ) > budget:
                if len(content) <= 200:
                    content = content[:100]
                    break
                content = content[: max(200, len(content) * 3 // 4)]
            m["content"] = content
            rest = [m]
            break

        trimmed = system + rest
        new_tokens = self._count_prompt_tokens(llama, trimmed)
        logger.warning(
            "Context trim slot=%s n_ctx=%s budget=%s tokens %s -> %s (messages %s -> %s)",
            slot_id,
            n_ctx,
            budget,
            original_tokens,
            new_tokens,
            len(dict_msgs),
            len(trimmed),
        )
        if new_tokens > budget:
            raise ValueError(
                f"Prompt too long for context window ({new_tokens} tokens, budget={budget}, n_ctx={n_ctx})"
            )
        return [self._dict_to_message(d) for d in trimmed]

    async def initialize(self):
        if self._model_slots:
            self.is_initialized = True
            return
        # Модель не грузим при старте — иначе долгий I/O и риск падения контейнера.
        # Список файлов: GET /v1/models, загрузка: POST /v1/models/load
        if not os.path.isfile(self._config_model_path):
            logger.warning(
                f"Default model file not found: {self._config_model_path}. "
                "Use POST /v1/models/load."
            )
        else:
            logger.info(
                f"LLM handler ready. Default model '{self._config_model_name}' — "
                "load via POST /v1/models/load or from chat."
            )
        self.is_initialized = False

    async def _try_create_completion(
        self,
        llama: Llama,
        messages: List[Message],
        temperature: float,
        max_tokens: int,
        stream: bool,
        enable_thinking: Optional[bool] = None,
    ) -> Union[Dict[str, Any], AsyncGenerator[Dict[str, Any], None]]:

        def create_completion(messages_formatter: Callable):
            formatted_messages = messages_formatter(messages)
            kwargs: Dict[str, Any] = {
                "messages": formatted_messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": stream,
            }
            if enable_thinking is None:
                return llama.create_chat_completion(**kwargs)

            # llama-cpp-python 0.3.x: create_chat_completion НЕ принимает enable_thinking
            # и не пробрасывает **kwargs. Но chat handler / Jinja-шаблон — да.
            # У Qwen3.5 в tokenizer.chat_template мышление только при
            # `enable_thinking is true` (не как у старого Qwen3).
            thinking_flag = bool(enable_thinking)
            try:
                params = inspect.signature(llama.create_chat_completion).parameters
                if "enable_thinking" in params or any(
                    p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
                ):
                    kwargs["enable_thinking"] = thinking_flag
                    return llama.create_chat_completion(**kwargs)
            except (TypeError, ValueError):
                pass

            handler = getattr(llama, "chat_handler", None)
            if handler is None:
                try:
                    from llama_cpp import llama_chat_format

                    chat_format = getattr(llama, "chat_format", None)
                    handlers = getattr(llama, "_chat_handlers", None) or {}
                    handler = handlers.get(chat_format) if chat_format else None
                    if handler is None and chat_format:
                        handler = llama_chat_format.get_chat_completion_handler(chat_format)
                except Exception as exc:
                    logger.debug("resolve chat_handler failed: %s", exc)
                    handler = None

            if handler is not None:
                logger.info(
                    "Passing enable_thinking=%r via chat_handler (template kwargs)",
                    thinking_flag,
                )
                return handler(llama=llama, enable_thinking=thinking_flag, **kwargs)

            logger.warning(
                "enable_thinking=%r ignored: create_chat_completion and chat_handler "
                "cannot receive this template kwarg",
                thinking_flag,
            )
            return llama.create_chat_completion(**kwargs)

        return await self._run_in_executor(lambda: create_completion(convert_to_dict_messages))

    async def _run_in_executor(self, func: Callable):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, func)

    def is_loaded(self) -> bool:
        return bool(self._model_slots)

    async def generate_response(
        self,
        messages: List[Message],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        stream: bool = False,
        chat_model_id: Optional[str] = None,
        enable_thinking: Optional[bool] = None,
    ) -> Union[ChatResponse, AsyncGenerator[str, None]]:
        if not self.is_loaded():
            raise ValueError("Model not loaded")
        slot_id = self._resolve_slot_id(chat_model_id)
        self._touch_lru(slot_id)
        # Предварительный clamp по primary-реплике (n_ctx одинаков у всех копий).
        probe = self._primary_slot(slot_id)
        if probe is None:
            raise ValueError("Model not loaded")
        temperature = temperature or settings.generation.default_temperature
        max_tokens = max_tokens or settings.generation.default_max_tokens
        reserved = 32
        min_gen = 64
        n_ctx = self._get_llama_n_ctx(probe.llama)
        prompt_tokens = self._count_prompt_tokens(
            probe.llama, convert_to_dict_messages(messages)
        )
        if prompt_tokens + max_tokens + reserved > n_ctx:
            max_tokens = max(min_gen, n_ctx - prompt_tokens - reserved)
            logger.warning(
                "Clamping max_tokens for prompt fit (slot=%s n_ctx=%s ~prompt_tokens=%s -> max_tokens=%s)",
                slot_id,
                n_ctx,
                prompt_tokens,
                max_tokens,
            )
        messages = self._fit_messages_to_context(probe.llama, messages, max_tokens, slot_id)
        max_tokens = self._clamp_max_tokens_for_request(
            probe.llama, messages, max_tokens, slot_id
        )
        start_time = time.time()

        # llama.cpp НЕ потокобезопасен на одном Llama: lock на реплику на весь стрим.
        # Несколько реплик (LLM_INFERENCE_REPLICAS) позволяют параллельные чаты.
        if stream:
            async def stream_generator():
                slot = await self._acquire_replica(slot_id)
                try:
                    stream_result = await self._try_create_completion(
                        slot.llama,
                        messages,
                        temperature,
                        max_tokens,
                        stream=True,
                        enable_thinking=enable_thinking,
                    )
                    logger.info(
                        "Stream [%s] started in %.2fs (replicas=%s)",
                        slot_id,
                        time.time() - start_time,
                        len(self._replicas(slot_id)),
                    )
                    try:
                        while True:
                            chunk = await self._run_in_executor(
                                lambda: next(stream_result, None)
                            )
                            if chunk is None:
                                break
                            yield f"data: {json.dumps(chunk)}\n\n"
                    except StopIteration:
                        pass
                    except ValueError as e:
                        logger.error("Stream error [%s]: %s", slot_id, e)
                        err_payload = {
                            "error": {
                                "message": str(e),
                                "type": "context_length_exceeded",
                            }
                        }
                        yield f"data: {json.dumps(err_payload)}\n\n"
                    finally:
                        yield "data: [DONE]\n\n"
                finally:
                    slot.gen_lock.release()

            return stream_generator()

        slot = await self._acquire_replica(slot_id)
        try:
            response = await self._try_create_completion(
                slot.llama,
                messages,
                temperature,
                max_tokens,
                stream=False,
                enable_thinking=enable_thinking,
            )
            logger.info(f"Response [{slot_id}] in {time.time() - start_time:.2f}s")
            return self._format_response(response, slot_id)
        finally:
            slot.gen_lock.release()

    def _format_response(self, raw_response: Dict[str, Any], response_model_id: str) -> ChatResponse:
        choice = raw_response["choices"][0]
        message = choice["message"]
        return ChatResponse(
            id=f"chatcmpl-{int(time.time())}",
            created=int(time.time()),
            model=response_model_id,
            choices=[
                ChatChoice(
                    index=0,
                    message=AssistantMessage(
                        role=message["role"],
                        content=message["content"],
                    ),
                    finish_reason=choice.get("finish_reason", "stop"),
                )
            ],
            usage=UsageInfo(
                prompt_tokens=raw_response.get("usage", {}).get("prompt_tokens", 0),
                completion_tokens=raw_response.get("usage", {}).get("completion_tokens", 0),
                total_tokens=raw_response.get("usage", {}).get("total_tokens", 0),
            ),
        )

    async def cleanup(self):
        async with self._registry_lock:
            groups = list(self._model_slots.values())
            self._model_slots.clear()
            self._primary_model_id = None
            self.is_initialized = False
        for group in groups:
            for s in group:
                await self._dispose_slot(s)
        logger.info("All model slots cleaned up")

    async def load_model(self, model_name: str) -> bool:
        return await self.load_model_by_name(model_name)

    async def _restore_default_after_failed_load(self, failed_id: str) -> None:
        dname = self.normalize_model_id(settings.model.name)
        dpath = settings.model.path
        if not os.path.isfile(dpath) or dname == failed_id:
            return
        async with self._registry_lock:
            if dname in self._model_slots:
                self._primary_model_id = dname
                return
        try:
            replicas = await self._instantiate_replicas(dpath, INFERENCE_REPLICAS)
            async with self._registry_lock:
                self._model_slots[dname] = replicas
                self._model_slots.move_to_end(dname)
                self._primary_model_id = dname
                self.is_initialized = True
            logger.info(
                "Restored default model %s after failed load of %s (replicas=%s)",
                dname,
                failed_id,
                len(replicas),
            )
        except Exception as e:
            logger.error(f"Failed to restore default model: {e}")

    async def load_model_by_name(self, model_name: str) -> bool:
        model_id = self.normalize_model_id(model_name)
        if not model_id:
            logger.error("load_model_by_name: пустое имя модели")
            return False
        model_path = resolve_gguf_path(model_id)
        if not model_path or not os.path.isfile(model_path):
            logger.error(f"Model file not found for id {model_id!r} (resolved={model_path!r})")
            return False

        async with self._model_switch_lock:
            async with self._registry_lock:
                if model_id in self._model_slots:
                    self._model_slots.move_to_end(model_id)
                    # Явный /load — делаем выбранную модель primary (не оставляем старый default).
                    self._primary_model_id = model_id
                    self.is_initialized = True
                    logger.info(f"Model {model_id} already in pool (LRU touch, set primary)")
                    return True

            victim_group: Optional[List[_Slot]] = None
            evicted_id: Optional[str] = None
            async with self._registry_lock:
                if len(self._model_slots) >= MAX_LOADED_MODELS:
                    evicted_id, victim_group = self._model_slots.popitem(last=False)
                    if self._primary_model_id == evicted_id:
                        self._primary_model_id = next(iter(self._model_slots.keys()), None)

        # Дальше — долгий I/O/CPU; без удержания _model_switch_lock, чтобы не блокировать
        # /v1/health, чат с уже загруженной моделью и второй параллельный /load

        if victim_group is not None:
            logger.info(f"LRU evict from pool: {evicted_id} (max={MAX_LOADED_MODELS})")
            for victim_slot in victim_group:
                await self._dispose_slot(victim_slot)

        try:
            new_replicas = await self._instantiate_replicas(model_path, INFERENCE_REPLICAS)
        except Exception as e:
            logger.error(f"Failed to load model {model_id}: {e}")
            async with self._model_switch_lock:
                await self._restore_default_after_failed_load(model_id)
            return False

        dispose_after: List[_Slot] = []
        async with self._model_switch_lock:
            async with self._registry_lock:
                if model_id in self._model_slots:
                    dispose_after.extend(new_replicas)
                    self._model_slots.move_to_end(model_id)
                    self._primary_model_id = model_id
                    self.is_initialized = True
                    logger.info(f"Model {model_id} уже в пуле (параллельная загрузка), отбрасываем дубликат")
                else:
                    while len(self._model_slots) >= MAX_LOADED_MODELS:
                        ev2, vic2 = self._model_slots.popitem(last=False)
                        dispose_after.extend(vic2)
                        if self._primary_model_id == ev2:
                            self._primary_model_id = next(iter(self._model_slots.keys()), None)
                    self._model_slots[model_id] = new_replicas
                    self._model_slots.move_to_end(model_id)
                    self._primary_model_id = model_id
                    self.is_initialized = True
                    logger.info(
                        "Pool load OK: %s (models=%s replicas=%s)",
                        model_id,
                        len(self._model_slots),
                        len(new_replicas),
                    )

        for s in dispose_after:
            await self._dispose_slot(s)
        return True

    async def trim_pool_to_config_default(self) -> bool:
        """
        Оставить в RAM только модель из config 
        Остальные GGUF выгружаются после выхода из multi-LLM на бэкенде
        """
        dname = self.normalize_model_id(settings.model.name)
        dpath = settings.model.path
        async with self._model_switch_lock:
            to_dispose: List[_Slot] = []
            async with self._registry_lock:
                keys_to_remove = [k for k in self._model_slots.keys() if k != dname]
                for k in keys_to_remove:
                    to_dispose.extend(self._model_slots.pop(k))
            for slot in to_dispose:
                await self._dispose_slot(slot)

            async with self._registry_lock:
                have_default = dname in self._model_slots

            if not have_default and os.path.isfile(dpath):
                try:
                    replicas = await self._instantiate_replicas(dpath, INFERENCE_REPLICAS)
                    async with self._registry_lock:
                        self._model_slots[dname] = replicas
                        self._primary_model_id = dname
                        self._model_slots.move_to_end(dname)
                        self.is_initialized = True
                    logger.info(
                        "trim_pool: загружена модель из конфига %s (replicas=%s)",
                        dname,
                        len(replicas),
                    )
                except Exception as e:
                    logger.error(f"trim_pool: не удалось загрузить дефолт {dname}: {e}")
                    async with self._registry_lock:
                        self._primary_model_id = next(iter(self._model_slots.keys()), None)
                        self.is_initialized = bool(self._model_slots)
                    return False
            else:
                async with self._registry_lock:
                    if dname in self._model_slots:
                        self._primary_model_id = dname
                        self._model_slots.move_to_end(dname)
                    else:
                        self._primary_model_id = next(iter(self._model_slots.keys()), None)
                    self.is_initialized = bool(self._model_slots)

        return True
