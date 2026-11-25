import pytest
from fastapi import status


def test_health_check(test_client):
    """Тест эндпоинта health check."""
    response = test_client.get("/v1/health")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["status"] == "healthy"
    assert response.json()["model_loaded"] is True


def test_list_models(test_client):
    """Тест эндпоинта list models."""
    response = test_client.get("/v1/models")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["data"][0]["id"] == "test-model"


def test_chat_completion_without_api_key_auth_enabled(test_client, auth_enabled_config):
    """Тест эндпоинта chat/completions без API ключа при включенной аутентификации."""

    request_data = {
        "model": "test-model",
        "stream": "false",
        "messages": [
            {"role": "user", "content": "Hello, how are you?"}
        ],
        "temperature": 0.7,
        "max_tokens": 100
    }

    response = test_client.post("/v1/chat/completions", json=request_data)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["detail"] == "API key is missing"


def test_chat_completion_with_invalid_api_key(test_client, auth_enabled_config):
    """Тест эндпоинта chat/completions с неверным API ключом."""
    
    request_data = {
        "model": "test-model",
        "stream": "false",
        "messages": [
            {"role": "user", "content": "Hello, how are you?"}
        ],
        "temperature": 0.7,
        "max_tokens": 100
    }

    response = test_client.post("/v1/chat/completions",
                                json=request_data,
                                headers={"X-API-Key": "invalid-key"})

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["detail"] == "Invalid API key"


def test_chat_completion_with_valid_api_key(test_client, auth_enabled_config):
    """Тест эндпоинта chat/completions с правильным API ключом."""
    
    request_data = {
        "model": "test-model",
        "stream": "false",
        "messages": [
            {"role": "user", "content": "Hello, how are you?"}
        ],
        "temperature": 0.7,
        "max_tokens": 100
    }

    response = test_client.post("/v1/chat/completions",
                                json=request_data,
                                headers={"X-API-Key": "test-api-key"})

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["model"] == "test-model"
    assert response.json()["choices"][0]["message"]["content"] == "Test response from mock handler"


def test_chat_completion_without_api_key_auth_disabled(test_client):
    """Тест эндпоинта chat/completions без API ключа при выключенной аутентификации."""
    
    request_data = {
        "model": "test-model",
        "stream": "false",
        "messages": [
            {"role": "user", "content": "Hello, how are you?"}
        ],
        "temperature": 0.7,
        "max_tokens": 100
    }

    response = test_client.post("/v1/chat/completions", json=request_data)

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["model"] == "test-model"
    assert response.json()["choices"][0]["message"]["content"] == "Test response from mock handler"


def test_chat_completion_without_api_key_empty_key_config(test_client, auth_enabled_no_key_config):
    """Тест эндпоинта chat/completions без API ключа при пустом значении ключа в конфигурации."""
    
    request_data = {
        "model": "test-model",
        "stream": "false",
        "messages": [
            {"role": "user", "content": "Hello, how are you?"}
        ],
        "temperature": 0.7,
        "max_tokens": 100
    }

    response = test_client.post("/v1/chat/completions", json=request_data)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["detail"] == "API key is not configured on server"
