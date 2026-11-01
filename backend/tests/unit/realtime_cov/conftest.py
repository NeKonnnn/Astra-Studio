"""Общие фейки для тестов realtime/handlers (покрытие B1)."""

import types

import pytest

from backend.realtime import handlers as h


class FakeSio:
    """Замена socketio.AsyncServer: пишет emit, хранит сессии, регистрирует @sio.event."""

    def __init__(self):
        self.handlers = {}
        self.emitted = []
        self.sessions = {}
        self.disconnected = []
        self.rooms = []

    def event(self, fn):
        self.handlers[fn.__name__] = fn
        return fn

    async def emit(self, name, payload=None, room=None, **kwargs):
        self.emitted.append((name, payload, room))

    async def save_session(self, sid, session):
        self.sessions[sid] = session

    async def get_session(self, sid):
        if sid not in self.sessions:
            raise KeyError(sid)
        return self.sessions[sid]

    async def disconnect(self, sid):
        self.disconnected.append(sid)

    async def enter_room(self, sid, room):
        self.rooms.append((sid, room))

    def names(self):
        return [e[0] for e in self.emitted]

    def payloads(self, name):
        return [p for n, p, _ in self.emitted if n == name]


class FakeRag:
    """Фейковый rag_client: настраиваемые ответы, журнал вызовов, исключения по имени метода."""

    def __init__(self):
        self.calls = []
        self.status = {}
        self.project_docs = []
        self.kb_docs = []
        self.memory_docs = []
        self.project_hits = []
        self.kb_hits = []
        self.memory_hits = []
        self.start_chunks = []
        self.errors = {}
        # Хиты, зависящие от document_id (дотягивание файла, названного в вопросе).
        self.by_document = {}

    def _maybe_raise(self, name):
        err = self.errors.get(name)
        if err is not None:
            raise err

    async def get_reindex_status(self):
        self.calls.append(("get_reindex_status", (), {}))
        self._maybe_raise("get_reindex_status")
        return self.status

    async def project_rag_list_documents(self, project_id):
        self.calls.append(("project_rag_list_documents", (project_id,), {}))
        self._maybe_raise("project_rag_list_documents")
        return self.project_docs

    async def project_rag_search(self, query, **kw):
        self.calls.append(("project_rag_search", (query,), kw))
        self._maybe_raise("project_rag_search")
        if kw.get("document_id") is not None and ("project", kw["document_id"]) in self.by_document:
            return self.by_document[("project", kw["document_id"])]
        return self.project_hits

    async def project_rag_get_document_start_chunks(self, project_id, doc_id, max_chunks=2):
        self.calls.append(("project_rag_get_document_start_chunks", (project_id, doc_id), {"max_chunks": max_chunks}))
        self._maybe_raise("project_rag_get_document_start_chunks")
        return self.start_chunks

    async def kb_list_documents(self):
        self.calls.append(("kb_list_documents", (), {}))
        self._maybe_raise("kb_list_documents")
        return self.kb_docs

    async def kb_search(self, query, **kw):
        self.calls.append(("kb_search", (query,), kw))
        self._maybe_raise("kb_search")
        ids = kw.get("document_ids") or []
        if len(ids) == 1 and ("kb", ids[0]) in self.by_document:
            return self.by_document[("kb", ids[0])]
        return self.kb_hits

    async def memory_rag_list_documents(self):
        self.calls.append(("memory_rag_list_documents", (), {}))
        self._maybe_raise("memory_rag_list_documents")
        return self.memory_docs

    async def memory_rag_search(self, query, **kw):
        self.calls.append(("memory_rag_search", (query,), kw))
        self._maybe_raise("memory_rag_search")
        if kw.get("document_id") is not None and ("memory", kw["document_id"]) in self.by_document:
            return self.by_document[("memory", kw["document_id"])]
        return self.memory_hits

    def called(self, name):
        return [c for c in self.calls if c[0] == name]


@pytest.fixture
def sio():
    fake = FakeSio()
    h.register_handlers(fake)
    return fake


@pytest.fixture(autouse=True)
def _clean_handler_state():
    """Глобальное состояние handlers между тестами."""
    h._generation_seq.clear()
    h._stopped_upto.clear()
    h._user_rooms.clear()
    h.stop_generation_flags.clear()
    h.stop_transcription_flags.clear()
    h._my_generation.set(0)
    h._stream_conversation_id.set(None)
    h._stream_request_id.set(None)
    yield
    h._my_generation.set(0)
    h._stream_conversation_id.set(None)
    h._stream_request_id.set(None)


class Recorder:
    """Фиксирует вызовы подменённых зависимостей."""

    def __init__(self):
        self.calls = {}

    def add(self, name, *args, **kwargs):
        self.calls.setdefault(name, []).append((args, kwargs))

    def get(self, name):
        return self.calls.get(name, [])


@pytest.fixture
def rec():
    return Recorder()


@pytest.fixture
def rag(monkeypatch):
    """Подставляет FakeRag как h.rag_client."""
    fake = FakeRag()
    monkeypatch.setattr(h, "rag_client", fake)
    return fake


def ns(**kw):
    return types.SimpleNamespace(**kw)
