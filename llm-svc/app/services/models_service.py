import asyncio
import uuid
import os
from typing import List, Optional, AsyncGenerator
from app.models.schemas import Message, ToolDefinition, ChatCompletionResponse
from app.core.config import settings
from app.exceptions import ServiceUnavailableError
from .model_pool import ModelPool
from .generators.non_stream_generator import NonStreamResponseGenerator
from .generators.stream_generator import StreamResponseGenerator
import logging
logger = logging.getLogger(__name__)

MODELS_DIR = "/app/models/llm"

class LlamaService:
    """Фасад для работы с моделями LLM"""
    _instance: Optional['LlamaService'] = None
    _lock = asyncio.Lock()
    def __init__(self):
        self.model_pool = ModelPool(pool_size=settings.model.pool_size)
        self.model_name = settings.model.name
        self._initialized = False
        # Инициализация генераторов с правильными completion caller'ами
        self.non_stream_generator = NonStreamResponseGenerator(
            self.model_name, self._create_completion
        )
        self.stream_generator = StreamResponseGenerator(
            self.model_name, self._create_completion_stream
        )
    @classmethod
    async def get_instance(cls) -> 'LlamaService':
        """Получение экземпляра сервиса (синглтон)"""
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance
    async def initialize(self) -> None:
        """Инициализация сервиса"""
        if self._initialized:
            return
        try:
            await self.model_pool.initialize()
            self._initialized = True
            logger.info("LlamaService initialized successfully")
        except Exception as e:
            logger.error(f"LlamaService initialization failed: {e}")
            self._initialized = False
            raise ServiceUnavailableError(f"Service initialization failed: {str(e)}")
    async def cleanup(self) -> None:
        """Очистка ресурсов"""
        if self._initialized:
            await self.model_pool.cleanup()
            self._initialized = False
            logger.info("LlamaService cleaned up")
    async def _create_completion(self, session_id: str, kwargs) -> dict:
        """Создание non-stream completion"""
        context = await self.model_pool.acquire()
        try:
            return await context.generate(**kwargs)
        finally:
            await self.model_pool.release(context)
    async def _create_completion_stream(self, session_id: str, kwargs: dict = None) -> AsyncGenerator[dict, None]:
        """Создание stream completion с неблокирующей итерацией."""
        if kwargs is None:
            kwargs = {}
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue()
        context = await self.model_pool.acquire()
        def producer():
            """
            Запускается в отдельном потоке, итерируется по блокирующему
            генератору и кладет результаты в очередь.
            """
            try:
                # Этот вызов блокирующий, но он выполняется в потоке executor'а
                kwargs['stream'] = True
                result_iterator = context._model.create_chat_completion(**kwargs)
                for chunk in result_iterator:
                    # Помещение в очередь из другого потока должно быть потокобезопасным
                    loop.call_soon_threadsafe(queue.put_nowait, chunk)
            except Exception as e:
                logger.error(f"Error in stream producer thread: {e}", exc_info=True)
                # Отправляем ошибку в очередь, чтобы генератор мог ее обработать
                loop.call_soon_threadsafe(queue.put_nowait, e)
            finally:
                # Сигнал о завершении
                loop.call_soon_threadsafe(queue.put_nowait, None)
        # Запускаем producer в отдельном потоке, чтобы не блокировать event loop
        loop.run_in_executor(None, producer)
        try:
            while True:
                # Асинхронно ждем следующий элемент из очереди
                item = await queue.get()
                # Завершаем, если получили сигнал
                if item is None:
                    break
                # Если из потока пришла ошибка, возбуждаем ее в главном цикле
                if isinstance(item, Exception):
                    raise item
                yield item
        finally:
            # Гарантированно возвращаем контекст в пул
            await self.model_pool.release(context)
    async def generate_response_non_stream(
            self,
            messages: List[Message],
            temperature: float,
            max_tokens: int,
            frequency_penalty: float,
            presence_penalty: float,
            tools: Optional[List[ToolDefinition]] = None,
            session_id: str = None
    ) -> ChatCompletionResponse:
        """Не-потоковая генерация ответа"""
        if session_id is None:
            session_id = f"non_stream_{uuid.uuid4().hex}"
        try:
            return await self.non_stream_generator.generate(
                messages, temperature, max_tokens, frequency_penalty, presence_penalty, tools, session_id
            )
        except ServiceUnavailableError:
            raise
        except Exception as e:
            logger.error(f"Non-stream generation error: {str(e)}")
            raise ServiceUnavailableError(f"Generation error: {str(e)}")
    async def generate_response_stream(
            self,
            messages: List[Message],
            temperature: float,
            max_tokens: int,
            frequency_penalty: float,
            presence_penalty: float,
            tools: Optional[List[ToolDefinition]] = None,
            session_id: str = None
    ) -> AsyncGenerator[str, None]:
        """Потоковая генерация ответа"""
        if session_id is None:
            session_id = f"stream_{uuid.uuid4().hex}"
        async for chunk in self.stream_generator.generate(
                messages, temperature, max_tokens, frequency_penalty, presence_penalty, tools, session_id
        ):
            yield chunk
    @property
    def is_loaded(self) -> bool:
        """Проверка загрузки модели"""
        return self._initialized and self.model_pool.total_count > 0
    @property
    def is_ready(self) -> bool:
        """Проверка готовности сервиса"""
        return self._initialized and self.model_pool.is_ready
    @property
    def is_available(self) -> bool:
        """Проверка, есть ли свободные инстансы для обработки запросов"""
        return self._initialized and self.model_pool.available_count > 0

    @property
    def model_path(self) -> str:
        """Путь к текущей модели (из конфига)."""
        return settings.model.path

    @property
    def n_ctx(self) -> int:
        """Размер контекста из конфига."""
        return settings.model.ctx_size

    @property
    def n_gpu_layers(self) -> int:
        """Количество GPU-слоёв из конфига."""
        return settings.model.gpu_layers

    async def load_model(self, model_name_or_path: str) -> bool:
        """
        Переключение на другую модель по имени (файл без .gguf) или по полному пути.
        Обновляет конфиг, делает cleanup и повторную инициализацию пула.
        """
        name = (model_name_or_path or "").strip()
        if not name:
            logger.error("load_model: пустое имя модели")
            return False
        if name.lower().endswith(".gguf"):
            name = name[:-5]
        name = os.path.basename(name).strip()
        if not name:
            return False
        if os.path.isabs(model_name_or_path.strip()) and os.path.exists(model_name_or_path.strip()):
            model_path = model_name_or_path.strip()
            display_name = os.path.splitext(os.path.basename(model_path))[0]
        else:
            model_path = os.path.join(MODELS_DIR, f"{name}.gguf")
            display_name = name
        if not os.path.exists(model_path):
            logger.error(f"Model file not found: {model_path}")
            return False
        if self._initialized and self.model_name == display_name:
            logger.info(f"Model {display_name} already loaded")
            return True
        prev_path = settings.model.path
        prev_name = settings.model.name
        try:
            settings.model.path = model_path
            settings.model.name = display_name
            await self.cleanup()
            await self.initialize()
            self.model_name = display_name
            self.non_stream_generator = NonStreamResponseGenerator(
                self.model_name, self._create_completion
            )
            self.stream_generator = StreamResponseGenerator(
                self.model_name, self._create_completion_stream
            )
            logger.info(f"Switched to model: {display_name}")
            return True
        except Exception as e:
            logger.error(f"Failed to load model {display_name}: {e}")
            settings.model.path = prev_path
            settings.model.name = prev_name
            try:
                await self.initialize()
                self.model_name = settings.model.name
                logger.info(f"Restored previous model: {self.model_name}")
            except Exception as restore_e:
                logger.error(f"Failed to restore previous model: {restore_e}")
            return False
