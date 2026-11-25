import re
import json
import logging
import time
from typing import List, Tuple, Optional
from app.models.schemas import ToolCall, FunctionCall
logger = logging.getLogger(__name__)
class ToolCallProcessor:
    """Сервис для обработки и парсинга tool calls из текста модели"""
    @staticmethod
    def extract_tool_calls(text: str) -> Tuple[str, List[ToolCall]]:
        """
        Извлекает tool calls из текста ответа модели.
        Возвращает (очищенный_текст, список_tool_calls)
        """
        tool_calls = []
        cleaned_text = text
        # Ищем все вхождения <tool_call>...</tool_call>
        pattern = r'<tool_call>\s*(.*?)\s*</tool_call>'
        matches = re.findall(pattern, text, re.DOTALL)
        for i, match in enumerate(matches):
            try:
                tool_data = json.loads(match)
                tool_call = ToolCall(
                    id=f"call_{i}_{int(time.time())}",
                    type="function",
                    function=FunctionCall(
                        name=tool_data.get("name", ""),
                        arguments=json.dumps(tool_data.get("arguments", {}))
                    )
                )
                tool_calls.append(tool_call)
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"Failed to parse tool call: {e}, content: {match}")
                continue
        # Удаляем tool calls из основного текста
        cleaned_text = re.sub(pattern, '', text, flags=re.DOTALL).strip()
        return cleaned_text, tool_calls
    @staticmethod
    def should_use_tools(tools: Optional[List]) -> bool:
        """Определяет, нужно ли использовать инструменты"""
        return tools is not None and len(tools) > 0
