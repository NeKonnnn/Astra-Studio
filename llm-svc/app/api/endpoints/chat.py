from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from app.models.schemas import ChatRequest
from app.services.base_llm_handler import BaseLLMHandler
from app.api.dependencies import get_llama_service, require_api_key
import logging

logger = logging.getLogger(__name__)
router = APIRouter()


def _resolve_enable_thinking(request: ChatRequest):
    """Top-level enable_thinking, иначе из chat_template_kwargs (GPB/vLLM-стиль)."""
    if request.enable_thinking is not None:
        return request.enable_thinking
    ctk = request.chat_template_kwargs
    if isinstance(ctk, dict) and "enable_thinking" in ctk:
        return bool(ctk.get("enable_thinking"))
    return None


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
    enable_thinking = _resolve_enable_thinking(request)
    logger.info(f"Chat request Model: {request.model}, "
                f"Messages: {len(request.messages)}, "
                f"Temperature: {request.temperature}, "
                f"Stream: {request.stream}, "
                f"enable_thinking: {enable_thinking}")
    if not llama_service.is_model_id_loaded(request.model):
        loaded = getattr(llama_service, "get_loaded_model_ids", lambda: [])()
        logger.info(
            "Model %r not in pool %s — loading on demand",
            request.model,
            loaded,
        )
        load_ok = await llama_service.load_model(request.model)
        if not load_ok or not llama_service.is_model_id_loaded(request.model):
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
                enable_thinking=enable_thinking,
            )
            return StreamingResponse(
                response_generator,
                media_type="text/event-stream"
            )
        else:
            # Обычный режим - возвращаем обычный ответ
            response = await llama_service.generate_response(
                messages=request.messages,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                stream=False,
                chat_model_id=request.model,
                enable_thinking=enable_thinking,
            )
            logger.info("Chat request: Response generated successfully")
            return response
    except Exception as e:
        logger.error(f"Chat request Error: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error generating response: {str(e)}"
        )

