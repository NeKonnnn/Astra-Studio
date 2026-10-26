import asyncio
import pytest
from unittest.mock import MagicMock

from app.services.llama_handler import LlamaHandler, _Slot
from app.models.schemas import UserMessage


@pytest.fixture
def llama_handler():
    return LlamaHandler()


@pytest.mark.asyncio
async def test_generate_response_not_loaded(llama_handler):
    with pytest.raises(ValueError, match="Model not loaded"):
        await llama_handler.generate_response([UserMessage(content="Hello")])


@pytest.mark.asyncio
async def test_cleanup(llama_handler):
    mock_llama = MagicMock()
    llama_handler._model_slots["test-model"] = [_Slot(mock_llama, "/tmp/test.gguf")]
    llama_handler._primary_model_id = "test-model"
    llama_handler.is_initialized = True

    await llama_handler.cleanup()

    assert not llama_handler._model_slots
    assert llama_handler.is_initialized is False


def test_is_loaded(llama_handler):
    assert llama_handler.is_loaded() is False

    llama_handler._model_slots["x"] = [_Slot(MagicMock(), "/p/x.gguf")]
    assert llama_handler.is_loaded() is True


@pytest.mark.asyncio
async def test_acquire_replica_allows_parallel_slots(llama_handler):
    a = _Slot(MagicMock(), "/p/m.gguf")
    b = _Slot(MagicMock(), "/p/m.gguf")
    llama_handler._model_slots["m"] = [a, b]
    llama_handler._primary_model_id = "m"

    first = await llama_handler._acquire_replica("m")
    second = await llama_handler._acquire_replica("m")
    assert first is not second
    assert first.gen_lock.locked()
    assert second.gen_lock.locked()
    first.gen_lock.release()
    second.gen_lock.release()


@pytest.mark.asyncio
async def test_acquire_replica_waits_when_all_busy(llama_handler):
    only = _Slot(MagicMock(), "/p/m.gguf")
    llama_handler._model_slots["m"] = [only]
    llama_handler._primary_model_id = "m"
    await only.gen_lock.acquire()

    async def release_later():
        await asyncio.sleep(0.05)
        only.gen_lock.release()

    releaser = asyncio.create_task(release_later())
    try:
        got = await asyncio.wait_for(llama_handler._acquire_replica("m"), timeout=2.0)
        assert got is only
    finally:
        await releaser
        if only.gen_lock.locked():
            only.gen_lock.release()
