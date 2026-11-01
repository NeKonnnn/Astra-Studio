"""Фикстуры API-тестов: приложение из create_application() + httpx поверх ASGI (без сети)."""

import httpx
import pytest

from app.main import create_application


@pytest.fixture
def app():
    return create_application()


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://svc") as c:
        yield c


@pytest.fixture
def disk_models(make_model, fake_ml):
    """На диске: FRIDA (с промптами), Giga (без), cross-encoder реранкер. Фейки ML включены."""
    make_model("FRIDA", config={"model_type": "t5"})
    make_model("Giga-Embeddings-instruct", config={"model_type": "gigarembed"})
    make_model("ms-marco-MiniLM-L6-v2", config={"architectures": ["BertForSequenceClassification"]})
    return fake_ml
