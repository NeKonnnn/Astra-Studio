from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from app.models.schemas import ChatRequest
from app.services.base_llm_handler import BaseLLMHandler
from app.api.dependencies import get_llama_service, require_api_key
import logging

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/chat/completions")
async def chat_completion(
    request: ChatRequest,
    llama_service: BaseLLMHandler = Depends(get_llama_service),
    api_key: bool = Depends(require_api_key),
):
    """
    Эндпоинт совместимый с OpenAI API для обработки запросов чата.
    Поддерживает как обычные запросы, так и потоковую передачу.
    """
    logger.info(
        f"Chat request Model: {request.model}, "
        f"Messages: {len(request.messages)}, "
        f"Temperature: {request.temperature}, "
        f"Stream: {request.stream}"
    )
    if not llama_service.is_model_id_loaded(request.model):
        loaded = getattr(llama_service, "get_loaded_model_ids", lambda: [])()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Model '{request.model}' is not loaded. Loaded: {loaded}",
        )
    try:
        if request.stream:
            # Потоковый режим - возвращаем StreamingResponse
            response_generator = await llama_service.generate_response(
                messages=request.messages,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                stream=True,
                chat_model_id=request.model,
                enable_thinking=request.enable_thinking,
            )
            return StreamingResponse(response_generator, media_type="text/event-stream")
        else:
            # Обычный режим - возвращаем обычный ответ
            response = await llama_service.generate_response(
                messages=request.messages,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                stream=False,
                chat_model_id=request.model,
                enable_thinking=request.enable_thinking,
            )
            logger.info("Chat request: Response generated successfully")
            return response
    except Exception as e:
        logger.error(f"Chat request Error: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error generating response: {str(e)}"
        )
