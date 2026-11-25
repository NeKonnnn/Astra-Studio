from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Literal, Dict, Any, Union
from enum import Enum
import json
import logging
logger = logging.getLogger(__name__) 
class FunctionCall(BaseModel):
    name: str = Field(..., description="Название вызываемой функции")
    arguments: str = Field(..., description="Аргументы функции в формате JSON строки")
class ToolCall(BaseModel):
    id: str = Field(..., description="Уникальный идентификатор вызова инструмента")
    type: Literal["function"] = "function"
    function: FunctionCall
class FunctionDefinition(BaseModel):
    name: str = Field(..., description="Название функции")
    description: Optional[str] = Field(None, description="Описание функции")
    parameters: Optional[Dict[str, Any]] = Field(None, description="JSON Schema параметров функции")
class ToolDefinition(BaseModel):
    type: Literal["function"] = "function"
    function: FunctionDefinition
class MessageRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    FUNCTION = "function"
    TOOL = "tool"
# === Базовые классы для каждого типа сообщений ===
class SystemMessage(BaseModel):
    role: Literal[MessageRole.SYSTEM] = MessageRole.SYSTEM
    content: str
class UserMessage(BaseModel):
    role: Literal[MessageRole.USER] = MessageRole.USER
    content: Union[str, List[Dict[str, Any]]]
    @field_validator('content', mode='before')
    @classmethod
    def convert_content(cls, v: Any) -> Any:
        """Сохраняем список для мультимодальных сообщений (text + image_url); иначе — только текст."""
        if isinstance(v, list):
            has_image = any(
                isinstance(item, dict) and item.get("type") == "image_url"
                for item in v
            )
            if has_image:
                return v  # Передаём как есть в vision-модель
            text_parts = []
            for item in v:
                if isinstance(item, dict) and item.get("type") == "text" and "text" in item:
                    text_parts.append(item.get("text", ""))
            return "".join(text_parts)
        return v
class AssistantMessage(BaseModel):
    role: Literal[MessageRole.ASSISTANT] = MessageRole.ASSISTANT
    content: Optional[str] = None
    tool_calls: Optional[List[ToolCall]] = None
class ToolMessage(BaseModel):
    """Строгое определение для tool-сообщений"""
    role: Literal[MessageRole.TOOL] = MessageRole.TOOL
    content: str  # JSON-строка с оригинальной структурой MCP
    tool_call_id: str
    name: str
    @field_validator('content', mode='before')
    @classmethod
    def convert_to_json_string(cls, v: Any) -> str:
        """Конвертируем content в JSON-строку с сохранением структуры MCP"""
        import json
        # Если это уже строка, проверяем не JSON ли это
        if isinstance(v, str):
            try:
                # Если это валидный JSON, оставляем как есть
                json.loads(v)
                return v
            except json.JSONDecodeError:
                # Если это простая строка, оборачиваем в MCP структуру
                return json.dumps([{"type": "text", "text": v}], ensure_ascii=False)
        # Если это список (оригинальный MCP формат)
        elif isinstance(v, list):
            # Сериализуем весь список в JSON строку
            try:
                return json.dumps(v, ensure_ascii=False)
            except Exception as e:
                logger.error(f"Failed to serialize MCP content to JSON: {e}")
                # Fallback: пытаемся извлечь текст
                text_parts = []
                for item in v:
                    if isinstance(item, dict) and item.get("type") == "text" and "text" in item:
                        text_parts.append(str(item.get("text", "")))
                return json.dumps([{"type": "text", "text": "".join(text_parts)}], ensure_ascii=False)
        # Для других типов - преобразуем в текст и оборачиваем
        else:
            return json.dumps([{"type": "text", "text": str(v)}], ensure_ascii=False)
class FunctionMessage(BaseModel):
    role: Literal[MessageRole.FUNCTION] = MessageRole.FUNCTION
    content: str
    name: str
# === Union тип для аннотации ===
Message = Union[SystemMessage, UserMessage, AssistantMessage, ToolMessage, FunctionMessage]
class ChatCompletionRequest(BaseModel):
    model: str = "abstract-model"
    messages: List[Message]  # Используем Union тип
    temperature: Optional[float] = Field(0.7, ge=0, le=2)
    max_tokens: Optional[int] = Field(256, ge=1)
    frequency_penalty: Optional[float] = Field(0.0, ge=-2.0, le=2.0)
    presence_penalty: Optional[float] = Field(0.0, ge=-2.0, le=2.0)
    stream: Optional[bool] = False
    tools: Optional[List[ToolDefinition]] = None
    tool_choice: Optional[Union[str, Dict[str, Any]]] = None
    enable_thinking: Optional[bool] = Field(
        None,
        description="Включить режим рассуждений (Qwen/DeepSeek и др.), если поддерживает бэкенд llama.cpp/vLLM",
    )
    chat_template_kwargs: Optional[Dict[str, Any]] = Field(
        None,
        description="Доп. kwargs шаблона чата (vLLM/Qwen). enable_thinking здесь — fallback, если top-level не задан.",
    )
class ChatCompletionResponseChoice(BaseModel):
    index: int
    message: AssistantMessage  # В ответе всегда assistant!
    finish_reason: Optional[str] = None
class UsageInfo(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[ChatCompletionResponseChoice]
    usage: UsageInfo


# Алиасы для совместимости с llama_handler и другими потребителями
ChatResponse = ChatCompletionResponse
ChatChoice = ChatCompletionResponseChoice


class ChatCompletionChunkChoice(BaseModel):
    index: int
    delta: Dict[str, Any]
    finish_reason: Optional[str] = None
class ChatCompletionChunk(BaseModel):
    id: str
    object: str = "chat.completion.chunk"
    created: int
    model: str
    choices: List[ChatCompletionChunkChoice]
class HealthResponse(BaseModel):
    status: str
    model_loaded: bool
    model_name: Optional[str] = None
    loaded_models: Optional[List[str]] = None
class ModelsListResponse(BaseModel):
    data: List[Dict[str, Any]]
    object: str = "list"


class ModelInfoResponse(BaseModel):
    """Детальная информация о текущей загруженной модели (GET /models/current)."""
    model_name: Optional[str] = None
    model_path: Optional[str] = None
    is_loaded: bool = False
    context_size: Optional[int] = None
    gpu_layers: Optional[int] = None
    file_size: Optional[int] = None
    file_size_mb: Optional[float] = None
    file_exists: bool = False
    config_name: Optional[str] = None


class ModelLoadRequest(BaseModel):
    model: str

class ModelLoadResponse(BaseModel):
    success: bool
    message: str
    model_name: Optional[str] = None


class ModelPoolTrimResponse(BaseModel):
    success: bool
    message: str
    remaining_models: Optional[List[str]] = None

ChatRequest = ChatCompletionRequest
