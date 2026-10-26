import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient
import os


@pytest.fixture(scope="session", autouse=True)
def test_environment():
    """Настройка тестового окружения."""
    # Устанавливаем тестовую конфигурацию ДО импорта любых модулей приложения
    os.environ["CONFIG_PATH"] = "config/config.test.yml"

    # Сбрасываем настройки перед началом тестов
    from app.core.config import reset_settings
    settings = reset_settings()

    yield settings

    # Очистка после тестов
    if "CONFIG_PATH" in os.environ:
        del os.environ["CONFIG_PATH"]


@pytest.fixture(scope="session")
def mock_llama_instance():
    """Общий мок handler для API-тестов (не патчит класс LlamaHandler)."""
    instance = MagicMock()
    instance.is_loaded.return_value = True
    instance.model_name = "test-model"
    instance.initialized = True
    instance.model_path = "/tmp/test.gguf"

    async def generate_response(*args, **kwargs):
        return {
            "id": "test-id",
            "created": 1234567890,
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "Test response from mock handler",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "total_tokens": 30,
            },
        }

    instance.generate_response = AsyncMock(side_effect=generate_response)
    instance.initialize = AsyncMock()
    instance.cleanup = AsyncMock()
    instance.is_model_id_loaded = MagicMock(return_value=True)
    instance.get_loaded_model_ids = MagicMock(return_value=["test-model"])
    instance.load_model = AsyncMock(return_value=True)
    instance.load_model_by_name = AsyncMock(return_value=True)
    return instance


@pytest.fixture(scope="session")
def test_app(mock_llama_instance):
    """Создание тестового приложения с переопределенными зависимостями."""
    from app.main import create_application
    from app.api.dependencies import get_llama_service, get_llm_handler_without_loaded_gate
    from app.llm_dependencies import get_llm_handler

    app = create_application()

    async def _override_handler():
        return mock_llama_instance

    app.dependency_overrides[get_llm_handler] = _override_handler
    app.dependency_overrides[get_llama_service] = _override_handler
    app.dependency_overrides[get_llm_handler_without_loaded_gate] = _override_handler
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def test_client(test_app):
    """Создание тестового клиента."""
    with TestClient(test_app) as client:
        yield client


@pytest.fixture
def auth_enabled_config():
    """Временное включение аутентификации для тестов."""
    from app.core.config import get_settings

    settings = get_settings()

    # Сохраняем оригинальные значения
    original_enabled = settings.security.enabled
    original_api_key = settings.security.api_key

    # Включаем аутентификацию и устанавливаем тестовый ключ
    settings.security.enabled = True
    settings.security.api_key = "test-api-key"

    yield

    # Восстанавливаем оригинальные значения
    settings.security.enabled = original_enabled
    settings.security.api_key = original_api_key


@pytest.fixture
def auth_enabled_no_key_config():
    """Временное включение аутентификации для тестов."""
    from app.core.config import get_settings

    settings = get_settings()

    # Сохраняем оригинальные значения
    original_enabled = settings.security.enabled
    original_api_key = settings.security.api_key

    # Включаем аутентификацию и сбрасываем ключ
    settings.security.enabled = True
    settings.security.api_key = None

    yield

    # Восстанавливаем оригинальные значения
    settings.security.enabled = original_enabled
    settings.security.api_key = original_api_key
