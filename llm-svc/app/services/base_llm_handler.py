from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Union, AsyncGenerator
from app.models.schemas import ChatResponse, Message

logger = None

class BaseLLMHandler(ABC):
    """Абстрактный базовый класс для всех LLM handlers."""
    
    def __init__(self):
        self.is_initialized = False
    
    @abstractmethod
    async def initialize(self):
        """Асинхронная инициализация модели."""
        pass
    
    @abstractmethod
    def is_loaded(self) -> bool:
        """Проверка, загружена ли модель."""
        pass

    def is_model_id_loaded(self, model_id: Optional[str]) -> bool:
        """Для llama.cpp-пула: проверка конкретного id; vLLM — достаточно is_loaded()."""
        return self.is_loaded()
    
    @abstractmethod
    async def generate_response(
        self,
        messages: List[Message],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        stream: bool = False,
        chat_model_id: Optional[str] = None,
        enable_thinking: Optional[bool] = None,
    ) -> Union[ChatResponse, AsyncGenerator[str, None]]:
        """Универсальный метод для генерации ответа."""
        pass
    
    @abstractmethod
    async def cleanup(self):
        """Очистка ресурсов модели."""
        pass




