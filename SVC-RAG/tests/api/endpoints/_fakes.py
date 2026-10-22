"""Фейки и хелперы API-тестов (не фикстуры, поэтому не в conftest)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI

from app.services.hit_types import RagHit


class FakeTrace:
    """Минимальная замена RetrievalTrace: API вызывает только to_dict()."""

    def to_dict(self) -> Dict[str, Any]:
        return {"store": "fake", "steps": ["vector"]}


class FakeStoreService:
    """Универсальный фейк kb/memory/project-сервиса: пишет вызовы, отдаёт заданные ответы."""

    def __init__(self) -> None:
        self.calls: List[tuple] = []
        self.index_result: Dict[str, Any] = {
            "ok": True,
            "document_id": 7,
            "filename": "a.txt",
            "chunks_count": 3,
        }
        self.duplicate_result: Dict[str, Any] = {"duplicate": False}
        self.documents: List[Dict[str, Any]] = []
        self.delete_result: Dict[str, Any] = {"ok": True}
        self.search_hits: List[Any] = []
        self.chunks_result: Optional[List[tuple]] = None
        self.delete_project_result: Dict[str, Any] = {"ok": True, "deleted": 2}
        self.reindex_error: Optional[BaseException] = None
        self.reindex_result: Dict[str, Any] = {"ok": True}
        self.vector_repo = None
        self.doc_repo = None
        self.rag_client = None

    # --- общий интерфейс --------------------------------------------------
    async def index_document(self, data, filename, **kw):
        self.calls.append(("index_document", data, filename, kw))
        return self.index_result

    async def check_duplicate(self, **kw):
        self.calls.append(("check_duplicate", kw))
        return self.duplicate_result

    async def list_documents(self, *args):
        self.calls.append(("list_documents", args))
        return self.documents

    async def delete_document(self, document_id, project_id=None):
        self.calls.append(("delete_document", document_id, project_id) if project_id is not None
                          else ("delete_document", document_id))
        return self.delete_result

    async def delete_by_project(self, project_id):
        self.calls.append(("delete_by_project", project_id))
        return self.delete_project_result

    async def get_document_chunks(self, project_id, document_id, **kw):
        self.calls.append(("get_document_chunks", project_id, document_id, kw))
        return self.chunks_result

    async def search(self, **kw):
        self.calls.append(("search", kw))
        return self.search_hits, FakeTrace()

    async def reindex_all(self, *args, **kw):
        self.calls.append(("reindex_all", args, kw))
        if self.reindex_error:
            raise self.reindex_error
        return self.reindex_result

    async def reindex_all_projects(self, **kw):
        self.calls.append(("reindex_all_projects", kw))
        return self.reindex_result

    def last(self, name: str) -> tuple:
        return [c for c in self.calls if c[0] == name][-1]


def make_client(application: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application, raise_app_exceptions=False),
        base_url="http://rag",
    )


def hit(content="текст", score=0.5, doc=1, idx=0, cosine=None) -> RagHit:
    return RagHit(content, score, doc, idx, cosine)
