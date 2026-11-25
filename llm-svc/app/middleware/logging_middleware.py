from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.background import BackgroundTask
import uuid
import time
import json
import logging
from typing import AsyncGenerator, List, Optional
logger = logging.getLogger(__name__)
class LoggingMiddleware(BaseHTTPMiddleware):
    """Упрощенное middleware для логирования запросов и ответов"""
    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())
        start_time = time.time()
        # Логирование входящего запроса
        await self._log_request(request, request_id)
        # Обработка запроса
        response = await call_next(request)
        process_time = time.time() - start_time
        # Унифицированное логирование ответа
        return await self._log_response(request_id, response, process_time)
    async def _log_request(self, request: Request, request_id: str):
        """Логирование входящего запроса"""
        logger.info(f"Request {request_id}: {request.method} {request.url}")
        # Логирование тела запроса
        if request.method in ["POST", "PUT", "PATCH"]:
            await self._log_request_body(request, request_id)
    async def _log_request_body(self, request: Request, request_id: str):
        """Логирование тела запроса"""
        try:
            body = await request.body()
            if body:
                try:
                    # Пытаемся распарсить как JSON для красивого вывода
                    body_json = json.loads(body.decode())
                    pretty_body = json.dumps(body_json, ensure_ascii=False, indent=2)
                    logger.info(f"Request {request_id}: Body:\n{pretty_body}")
                except (json.JSONDecodeError, UnicodeDecodeError):
                    # Если не JSON или ошибка декодирования - логируем как текст
                    decoded_body = body.decode(errors='replace')
                    logger.info(f"Request {request_id}: Body: {decoded_body}")
                # Восстанавливаем тело запроса
                request._body = body
        except Exception as e:
            logger.error(f"Request {request_id}: Error reading request body: {str(e)}")
    async def _log_response(self, request_id: str, response: Response, process_time: float):
        """Унифицированное логирование ответа"""
        is_streaming = self._is_streaming_response(response)
        if is_streaming:
            return await self._handle_streaming_response(request_id, response, process_time)
        else:
            return await self._handle_standard_response(request_id, response, process_time)
    def _is_streaming_response(self, response: Response) -> bool:
        """Проверка, является ли ответ потоковым"""
        content_type = response.headers.get('content-type', '').lower()
        return (
            isinstance(response, StreamingResponse) or 
            'stream' in content_type or
            'text/event-stream' in content_type or
            'application/json-seq' in content_type
        )
    async def _handle_streaming_response(self, request_id: str, response: StreamingResponse, process_time: float):
        """Упрощенная обработка потокового ответа с логированием полного текста"""
        # Сохраняем оригинальный итератор
        original_iterator = response.body_iterator
        # Собираем только текстовое содержимое для логирования
        content_parts: List[str] = []
        async def logging_iterator() -> AsyncGenerator[bytes, None]:
            """Итератор, который собирает содержимое для логирования, но не блокирует поток"""
            async for chunk in original_iterator:
                yield chunk  # Сразу отдаем клиенту
                try:
                    # Декодируем чанк и извлекаем содержимое
                    chunk_text = chunk.decode('utf-8', errors='ignore')
                    # Для SSE формата (data: {...})
                    if chunk_text.startswith('data:'):
                        try:
                            # Извлекаем JSON из data: {...}
                            json_str = chunk_text[5:].strip()
                            if json_str.startswith('{') and json_str.endswith('}'):
                                chunk_data = json.loads(json_str)
                                # Извлекаем content из delta, если есть
                                if (chunk_data.get('choices') and 
                                    isinstance(chunk_data['choices'], list) and 
                                    len(chunk_data['choices']) > 0 and
                                    'delta' in chunk_data['choices'][0] and
                                    'content' in chunk_data['choices'][0]['delta']):
                                    content = chunk_data['choices'][0]['delta']['content']
                                    if content:
                                        content_parts.append(content)
                        except (json.JSONDecodeError, KeyError, TypeError):
                            pass
                    # Для обычного текстового потока
                    elif chunk_text.strip():
                        content_parts.append(chunk_text.strip())
                except Exception as e:
                    logger.debug(f"Request {request_id}: Error processing stream chunk: {str(e)}")
        # Создаем новый ответ с нашим итератором
        new_response = StreamingResponse(
            logging_iterator(),
            status_code=response.status_code,
            headers=response.headers,
            media_type=response.media_type,
            background=response.background
        )
        # Фоновая задача для логирования после завершения потока
        async def log_completion():
            full_content = ''.join(content_parts)
            if full_content:
                # Логируем полный текст без обрезания!
                logger.info(f"Request {request_id}: Stream response content: {full_content}")
            logger.info(f"Request {request_id}: Streaming completed. Total time: {process_time:.2f}s")
        # Добавляем фоновую задачу
        self._add_background_task(new_response, log_completion)
        # Логируем начало потоковой передачи
        logger.info(f"Request {request_id}: Streaming response started. Status: {response.status_code}")
        return new_response
    async def _handle_standard_response(self, request_id: str, response: Response, process_time: float):
        """Обработка стандартного ответа"""
        try:
            # Читаем всё тело ответа
            response_body = b""
            async for chunk in response.body_iterator:
                response_body += chunk
            # Логируем тело ответа
            await self._log_response_body(request_id, response_body, response.status_code)
            # Восстанавливаем итератор
            async def new_body_iterator():
                yield response_body
            response.body_iterator = new_body_iterator()
        except Exception as e:
            logger.error(f"Request {request_id}: Error reading response body: {str(e)}")
        logger.info(f"Request {request_id}: Response status: {response.status_code}")
        logger.info(f"Request {request_id}: Process time: {process_time:.2f}s")
        return response
    async def _log_response_body(self, request_id: str, response_body: bytes, status_code: int):
        """Логирование тела ответа с поддержкой полного текста"""
        if not response_body:
            logger.info(f"Request {request_id}: Response body: [Empty]")
            return
        try:
            decoded_body = response_body.decode('utf-8', errors='replace')
            # Проверяем, является ли это JSON
            try:
                json_obj = json.loads(decoded_body)
                # Для ошибок или обычных JSON ответов
                if status_code >= 400 or not self._contains_stream_data(json_obj):
                    pretty_json = json.dumps(json_obj, indent=2, ensure_ascii=False)
                    logger.info(f"Request {request_id}: Response body(JSON):\n{pretty_json}")
                else:
                    # Это успешный JSON, но не потоковый - логируем как текст
                    logger.info(f"Request {request_id}: Response body: {decoded_body}")
            except (json.JSONDecodeError, TypeError):
                # Обычный текст
                logger.info(f"Request {request_id}: Response body: {decoded_body}")
        except UnicodeDecodeError:
            logger.info(f"Request {request_id}: Response body: [Binary data - {len(response_body)} bytes]")
    def _contains_stream_data(self, data: any) -> bool:
        """Проверка, содержит ли JSON данные о потоковой передаче"""
        if isinstance(data, dict):
            # Проверяем наличие полей, характерных для чанков потоковых ответов
            return ('choices' in data and 
                    isinstance(data['choices'], list) and 
                    len(data['choices']) > 0 and
                    'delta' in data['choices'][0])
        return False
    def _add_background_task(self, response: Response, task_func: callable):
        """Унифицированное добавление фоновой задачи к ответу"""
        async def wrapper():
            await task_func()
        if response.background:
            original_background = response.background
            async def combined_task():
                await original_background()
                await wrapper()
            response.background = BackgroundTask(combined_task)
        else:
            response.background = BackgroundTask(wrapper)


