from app.core.config import settings
from app.services.base_llm_handler import BaseLLMHandler


async def get_llama_service() -> BaseLLMHandler:
    """Получение экземпляра сервиса LLM в зависимости от backend."""
    backend = settings.model.backend.lower()
    if backend == "vllm":
        from app.llm_dependencies import get_llm_handler
        
        return await get_llm_handler()
    else:
        from app.services.models_service import LlamaService
        return await LlamaService.get_instance()


async def cleanup_llama_service() -> None:
    """Очистка сервиса LLM."""
    backend = settings.model.backend.lower()
    if backend == "vllm":
        from app.llm_dependencies import cleanup_llm_handler
        await cleanup_llm_handler()
    else:
        from app.services.models_service import LlamaService
        service = await LlamaService.get_instance()
        await service.cleanup()
