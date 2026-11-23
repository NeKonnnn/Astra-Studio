import pytest
from unittest.mock import MagicMock

from app.services.llama_handler import LlamaHandler, _Slot
from app.models.schemas import Message


@pytest.fixture
def llama_handler():
    return LlamaHandler()


@pytest.mark.asyncio
async def test_generate_response_not_loaded(llama_handler):
    with pytest.raises(ValueError, match="Model not loaded"):
        await llama_handler.generate_response([Message(role="user", content="Hello")])


@pytest.mark.asyncio
async def test_cleanup(llama_handler):
    mock_llama = MagicMock()
    llama_handler._model_slots["test-model"] = _Slot(mock_llama, "/tmp/test.gguf")
    llama_handler._primary_model_id = "test-model"
    llama_handler.is_initialized = True

    await llama_handler.cleanup()

    assert not llama_handler._model_slots
    assert llama_handler.is_initialized is False


def test_is_loaded(llama_handler):
    assert llama_handler.is_loaded() is False

    llama_handler._model_slots["x"] = _Slot(MagicMock(), "/p/x.gguf")
    assert llama_handler.is_loaded() is True
