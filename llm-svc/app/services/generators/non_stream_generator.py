import time
import uuid
from typing import List, Optional, Callable, Dict, Any
from app.models.schemas import (
    Message, ToolDefinition, ChatCompletionResponse,
    UsageInfo, MessageRole, ChatCompletionResponseChoice, AssistantMessage
)
from .base_generator import BaseResponseGenerator
from app.services.generators.tool_call_processor import ToolCallProcessor # Добавлен импорт
import logging

logger = logging.getLogger(__name__)

class NonStreamResponseGenerator(BaseResponseGenerator):
    """Генератор не-потоковых ответов"""

    def __init__(self, model_name: str, completion_caller: Callable):
            super().__init__(model_name, completion_caller)
            self.tool_processor = ToolCallProcessor()

    async def generate(
            self,
            messages: List[Message],
            temperature: float,
            max_tokens: int,
            frequency_penalty: float,
            presence_penalty: float,
            tools: Optional[List[ToolDefinition]] = None,
            session_id: str = None
    ) -> ChatCompletionResponse:
        """Генерация не-потокового ответа"""
        logger.info("Starting non-stream generation")
        start_time = time.time()
        response_id = f"chatcmpl-{uuid.uuid4().hex}"
        try:
            # Подготавливаем параметры
            params = self._prepare_generation_params(
                messages, temperature, max_tokens, frequency_penalty, presence_penalty, tools
            )
            logger.info(f"Calling model completion for session {session_id}")
            # Убран ** так как models_service передает словарь параметров
            response = await self._completion_caller(session_id, params)
            logger.info(f"Model response received, processing...")
            # Обрабатываем ответ
            processed_response = self._process_response(response, tools, response_id)
            processing_time = time.time() - start_time
            logger.info(f"Non-stream response generated in {processing_time:.2f}s")
            return processed_response
        except Exception as e:
            logger.error(f"Non-stream generation error: {str(e)}", exc_info=True)
            return self._create_error_response(response_id, str(e))

    def _process_response(
            self,
            response: dict,
            tools: Optional[List[ToolDefinition]],
            response_id: str
    ) -> ChatCompletionResponse:
        """Обрабатывает raw response от модели"""
        if not response or 'choices' not in response or not response['choices']:
            return self._create_error_response(response_id, "Invalid response from model")
        choices_data = response.get('choices', [{}])[0]
        message_data = choices_data.get('message', {})
        finish_reason = choices_data.get('finish_reason', 'stop')
        content = message_data.get('content', '')
        tool_calls = message_data.get('tool_calls')
        # Обрабатываем tool calls только если они переданы и контент содержит теги
        if self._should_use_tools(tools) and content and '<tool_call>' in content:
            cleaned_content, extracted_tool_calls = self.tool_processor.extract_tool_calls(content)
            if extracted_tool_calls:
                content = cleaned_content.strip() if cleaned_content else None
                tool_calls = extracted_tool_calls
                finish_reason = "tool_calls"
                logger.info(f"Extracted {len(extracted_tool_calls)} tool calls from text")
        # Создаем сообщение ассистента
        assistant_message = AssistantMessage(
            role=MessageRole.ASSISTANT,
            content=content if content else None,
            tool_calls=tool_calls if tool_calls else None
        )
        return ChatCompletionResponse(
            id=response.get('id', response_id),
            object="chat.completion",
            created=response.get('created', int(time.time())),
            model=self.model_name,
            choices=[
                ChatCompletionResponseChoice(
                    index=0,
                    message=assistant_message,
                    finish_reason=finish_reason
                )],
            usage=UsageInfo(
                prompt_tokens=response.get('usage', {}).get('prompt_tokens', 0),
                completion_tokens=response.get('usage', {}).get('completion_tokens', 0),
                total_tokens=response.get('usage', {}).get('total_tokens', 0)
            )
        )

    def _create_error_response(self, response_id: str, error_message: str) -> ChatCompletionResponse:
        """Создает ответ с ошибкой"""
        return ChatCompletionResponse(
            id=response_id,
            object="chat.completion",
            created=int(time.time()),
            model=self.model_name,
            choices=[
                ChatCompletionResponseChoice(
                    index=0,
                    message=AssistantMessage(
                        role=MessageRole.ASSISTANT,
                        content=f"Error: {error_message}"
                    ),
                    finish_reason="stop"
                )],
            usage=UsageInfo(prompt_tokens=0, completion_tokens=0, total_tokens=0)
        )