from fastapi import Depends, HTTPException, status
from app.services.base_llm_handler import BaseLLMHandler
from app.llm_dependencies import get_llm_handler
from app.core.security import verify_api_key

async def get_llama_service(
    llm_handler: BaseLLMHandler = Depends(get_llm_handler),
) -> BaseLLMHandler:
    """Зависимость для получения сервиса LLM."""
    # Не ждём _model_switch_lock
    if not llm_handler.is_loaded():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LLM model is not loaded",
        )
    return llm_handler


async def get_llm_handler_without_loaded_gate(
    llm_handler: BaseLLMHandler = Depends(get_llm_handler),
) -> BaseLLMHandler:
    """
    Доступ к handler без требования is_loaded().
    Не ждём _model_switch_lock: иначе GET /v1/health и прочие запросы висят на всё время
    чтения GGUF в другом запросе, а бэкенд шлёт health часто
    Согласованность пула обеспечивает _registry_lock внутри LlamaHandler
    """
    return llm_handler


async def require_api_key(api_key_verified: bool = Depends(verify_api_key)):
    """Зависимость для проверки API ключа."""
    return api_key_verified