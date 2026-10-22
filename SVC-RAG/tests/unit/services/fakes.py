"""In-memory двойники репозиториев и клиента эмбеддингов для юнит-тестов сервисов RAG.

Сигнатуры методов повторяют ``app/database/{kb,project_rag,memory_rag}_repository.py``,
но без БД: данные лежат в словарях, поиск — по тематическим эмбеддингам и подстрокам.
Сервисы (KbService / ProjectRagService / MemoryRagService) работают через них целиком,
включая настоящий retrieval-пайплайн.
"""

from __future__ import annotations

import copy
import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.database.models import Document, DocumentVector

DIM = 8  # совпадает с postgresql.embedding_dim из tests/fixtures/config.test.yml
TOPICS = ["кошк", "собак", "птиц", "налог", "космос", "музык", "футбол"]


def vector_for(text: str) -> List[float]:
    low = (text or "").lower()
    vec = [float(low.count(t)) for t in TOPICS] + [0.01]
    norm = sum(x * x for x in vec) ** 0.5
    return [x / norm for x in vec]


def cosine(a: List[float], b: List[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


CATS = ("Кошки любят спать. Кошка мурлычет на окне, а кошки охотятся на мышей. " * 6).encode()
DOGS = ("Собаки верные друзья. Собака охраняет дом, а собаки любят гулять во дворе. " * 6).encode()
TAXES = ("Налог на прибыль уплачивается ежеквартально. Налоговая служба проверяет налог. " * 6).encode()
BIG = ("\n\n".join(f"Абзац {i}. " + "кошки " * 80 for i in range(12))).encode()


class FakeRagClient:
    """Детерминированные «эмбеддинги»; умеет падать и считать вызовы реранка."""

    def __init__(self):
        self.embed_calls: List[Tuple[List[str], Optional[str]]] = []
        self.fail: Optional[BaseException] = None
        self.rerank_calls: List[Any] = []
        self.rerank_result: Optional[Any] = None  # None — развернуть порядок; список — отдать как есть
        self.rerank_fail: Optional[BaseException] = None

    async def embed(self, texts, model=None, kind=None):
        if self.fail:
            raise self.fail
        self.embed_calls.append((list(texts), kind))
        return [vector_for(t) for t in texts]

    async def embed_single(self, text, model=None, kind=None):
        return vector_for(text)

    async def rerank(self, query, documents, **kw):
        self.rerank_calls.append((query, list(documents), kw))
        if self.rerank_fail:
            raise self.rerank_fail
        if self.rerank_result is not None:
            return self.rerank_result
        n = len(documents)
        return [(i, float(n - r)) for r, i in enumerate(reversed(range(n)))]


class FakeGraphRepo:
    """Графовый репозиторий: запоминает вызовы, умеет падать."""

    def __init__(self):
        self.rebuilt: List[Dict[str, Any]] = []
        self.deleted: List[Tuple[str, int]] = []
        self.fail_rebuild: Optional[BaseException] = None
        self.fail_delete: Optional[BaseException] = None
        self.neighbors: Dict[Tuple[int, int], float] = {}
        self.fail_expand: Optional[BaseException] = None
        self.expand_calls: List[Dict[str, Any]] = []

    async def rebuild_document_graph(self, store_type, document_id, chunks):
        if self.fail_rebuild:
            raise self.fail_rebuild
        self.rebuilt.append(
            {"store_type": store_type, "document_id": document_id, "chunks": list(chunks)}
        )

    async def delete_document_graph(self, store_type, document_id):
        if self.fail_delete:
            raise self.fail_delete
        self.deleted.append((store_type, document_id))

    async def expand_neighbors(self, **kw):
        self.expand_calls.append(kw)
        if self.fail_expand:
            raise self.fail_expand
        return dict(self.neighbors)


def _meta(doc: Document) -> Dict[str, Any]:
    return doc.metadata or {}


class _DocRepoBase:
    """Общая часть репозиториев документов: словарь Document по id."""

    def __init__(self):
        self.docs: Dict[int, Document] = {}
        self._next = 1
        self.create_returns_none = False
        self.fail_delete: Optional[BaseException] = None
        self.deleted: List[int] = []
        self.create_hook = None  # вызывается после вставки (для сценариев гонки)

    def _insert(self, doc: Document) -> Optional[int]:
        if self.create_returns_none:
            return None
        doc_id = self._next
        self._next += 1
        self.docs[doc_id] = doc.model_copy(update={"id": doc_id}, deep=True)
        if self.create_hook:
            self.create_hook(doc_id)
        return doc_id

    async def delete_document(self, document_id: int) -> bool:
        if self.fail_delete:
            raise self.fail_delete
        self.deleted.append(document_id)
        self.docs.pop(document_id, None)
        return True

    async def get_document(self, document_id: int):
        doc = self.docs.get(document_id)
        return doc.model_copy(deep=True) if doc else None

    def _by_name(self, name: str, limit: int) -> List[int]:
        needle = (name or "").strip().lower()
        if not needle:
            return []
        ids = [i for i, d in sorted(self.docs.items(), reverse=True) if needle in d.filename.lower()]
        return ids[:limit]

    @staticmethod
    def _dup(doc: Document, digest: str, name: str, size_s: Optional[str]) -> bool:
        meta = _meta(doc)
        known = str(meta.get("content_hash") or "").strip().lower()
        if digest and known == digest:
            return True
        return bool(
            name
            and size_s is not None
            and not known
            and doc.filename.lower() == name.lower()
            and str(meta.get("size", "")) == size_s
        )


class FakeKbDocRepo(_DocRepoBase):
    async def create_document(self, document: Document) -> Optional[int]:
        return self._insert(document)

    async def find_duplicate(
        self,
        *,
        content_hash=None,
        filename=None,
        size=None,
        agent_id=None,
        knowledge_base_id=None,
    ):
        digest = (content_hash or "").strip().lower()
        name = (filename or "").strip()
        size_s = str(int(size)) if size is not None else None
        if not digest and not (name and size_s is not None):
            return None
        for i, d in sorted(self.docs.items(), reverse=True):
            meta = _meta(d)
            if knowledge_base_id is not None:
                in_scope = meta.get("knowledge_base_id") == knowledge_base_id
            elif agent_id is not None:
                in_scope = meta.get("agent_id") == agent_id
            else:
                in_scope = meta.get("agent_id") is None and meta.get("knowledge_base_id") is None
            if in_scope and self._dup(d, digest, name, size_s):
                return {"id": i, "filename": d.filename}
        return None

    async def get_all_documents(self) -> List[Document]:
        return [d.model_copy(deep=True) for _, d in sorted(self.docs.items())]

    async def list_documents_brief(self) -> List[Dict[str, Any]]:
        out = []
        for i, d in sorted(self.docs.items(), reverse=True):
            item = {
                "id": i,
                "filename": d.filename,
                "created_at": d.created_at.isoformat(),
                "size": str(_meta(d).get("size", 0)),
                "file_type": _meta(d).get("file_type", ""),
            }
            if str(_meta(d).get("agent_id", "")).isdigit():
                item["metadata"] = {"agent_id": int(_meta(d)["agent_id"])}
            out.append(item)
        return out

    async def get_documents_by_owner(self, owner_user_id: str) -> List[Document]:
        oid = (owner_user_id or "").strip().lower()
        return [
            d.model_copy(deep=True)
            for _, d in sorted(self.docs.items())
            if str(_meta(d).get("owner_user_id", "")).strip().lower() == oid
        ]

    async def get_documents_by_ids(self, document_ids: List[int]) -> List[Document]:
        return [self.docs[i].model_copy(deep=True) for i in document_ids if i in self.docs]

    async def clone_document_row(self, source_id: int, *, metadata: Dict[str, Any]) -> Optional[int]:
        src = self.docs.get(source_id)
        if not src:
            return None
        now = datetime.now(timezone.utc)
        return self._insert(
            Document(filename=src.filename, content=src.content, metadata=metadata,
                     created_at=now, updated_at=now)
        )

    async def find_document_ids_by_filename(
        self, name_or_stem: str, limit: int = 10, document_ids: Optional[List[int]] = None
    ) -> List[int]:
        if document_ids is None:
            return self._by_name(name_or_stem, limit)
        allowed = {int(i) for i in document_ids}
        return [i for i in self._by_name(name_or_stem, len(self.docs) or 1) if i in allowed][:limit]


class FakeMemoryDocRepo(_DocRepoBase):
    async def create_document(self, document: Document) -> Optional[int]:
        return self._insert(document)

    async def find_duplicate(self, *, content_hash=None, filename=None, size=None):
        digest = (content_hash or "").strip().lower()
        name = (filename or "").strip()
        size_s = str(int(size)) if size is not None else None
        if not digest and not (name and size_s is not None):
            return None
        for i, d in sorted(self.docs.items(), reverse=True):
            if self._dup(d, digest, name, size_s):
                return {"id": i, "filename": d.filename}
        return None

    async def get_all_documents(self) -> List[Document]:
        return [d.model_copy(deep=True) for _, d in sorted(self.docs.items())]

    async def list_documents_brief(self) -> List[Dict[str, Any]]:
        return [
            {
                "id": i,
                "filename": d.filename,
                "created_at": d.created_at.isoformat(),
                "size": str(_meta(d).get("size", 0)),
                "file_type": _meta(d).get("file_type", ""),
            }
            for i, d in sorted(self.docs.items(), reverse=True)
        ]

    async def find_document_ids_by_filename(self, name_or_stem: str, limit: int = 10) -> List[int]:
        return self._by_name(name_or_stem, limit)


class FakeProjectDocRepo:
    """Документы проектов: dict-и, как у настоящего репозитория."""

    def __init__(self):
        self.rows: Dict[int, Dict[str, Any]] = {}
        self._next = 1
        self.create_returns_none = False
        self.fail_delete: Optional[BaseException] = None
        self.deleted: List[int] = []
        self.create_hook = None

    async def create_document(self, project_id: str, document: Document) -> Optional[int]:
        if self.create_returns_none:
            return None
        doc_id = self._next
        self._next += 1
        self.rows[doc_id] = {
            "id": doc_id,
            "project_id": project_id,
            "filename": document.filename,
            "content": document.content,
            "metadata": dict(document.metadata or {}),
            "created_at": document.created_at,
            "updated_at": document.updated_at,
        }
        if self.create_hook:
            self.create_hook(doc_id)
        return doc_id

    async def find_duplicate(self, *, project_id, content_hash=None, filename=None, size=None):
        digest = (content_hash or "").strip().lower()
        name = (filename or "").strip()
        size_s = str(int(size)) if size is not None else None
        if not digest and not (name and size_s is not None):
            return None
        for i, r in sorted(self.rows.items(), reverse=True):
            if r["project_id"] != project_id:
                continue
            meta = r["metadata"]
            known = str(meta.get("content_hash") or "").strip().lower()
            if (digest and known == digest) or (
                name and size_s is not None and not known
                and r["filename"].lower() == name.lower() and str(meta.get("size", "")) == size_s
            ):
                return {"id": i, "filename": r["filename"]}
        return None

    async def get_document(self, document_id: int) -> Optional[dict]:
        row = self.rows.get(document_id)
        return copy.deepcopy(row) if row else None

    async def get_all_project_ids(self) -> List[str]:
        return sorted({r["project_id"] for r in self.rows.values() if r["project_id"]})

    @staticmethod
    def _brief(r) -> dict:
        return {**copy.deepcopy(r), "content": None}

    async def get_documents_by_owner(self, owner_user_id: str) -> List[dict]:
        oid = (owner_user_id or "").strip().lower()
        if not oid:
            return []
        return [
            self._brief(r)
            for _, r in sorted(self.rows.items())
            if str(r["metadata"].get("owner_user_id", "")).strip().lower() == oid
        ]

    async def find_document_ids_by_filename(self, name_or_stem: str, project_id=None, limit: int = 10) -> List[int]:
        needle = (name_or_stem or "").strip().lower()
        if not needle:
            return []
        ids = [
            i for i, r in sorted(self.rows.items(), reverse=True)
            if needle in r["filename"].lower() and (project_id is None or r["project_id"] == project_id)
        ]
        return ids[:limit]

    async def delete_document(self, document_id: int) -> bool:
        if self.fail_delete:
            raise self.fail_delete
        self.deleted.append(document_id)
        self.rows.pop(document_id, None)
        return True

    async def delete_documents_by_project(self, project_id: str) -> int:
        ids = [i for i, r in self.rows.items() if r["project_id"] == project_id]
        for i in ids:
            del self.rows[i]
        return len(ids)

    async def list_documents_brief(self, project_id: str) -> List[dict]:
        return [self._brief(r) for _, r in sorted(self.rows.items(), reverse=True)
                if r["project_id"] == project_id]


class FakeVectorRepo:
    """Векторная таблица одной размерности: сигнатуры — надмножество трёх настоящих репозиториев.

    doc_repo нужен для фильтров (имя файла, даты) и для скоупа по project_id.
    """

    def __init__(self, embedding_dim: int = DIM, doc_repo: Any = None):
        self.embedding_dim = embedding_dim
        self.doc_repo = doc_repo
        self.rows: Dict[int, List[DocumentVector]] = {}
        self.fail_create: Optional[BaseException] = None
        self.fail_create_on_call: Optional[int] = None
        self.create_calls = 0
        self.fail_delete: Optional[BaseException] = None
        self.fail_clone: Optional[BaseException] = None
        self.store: Dict[str, Tuple[str, bytes, int]] = {}
        self.store_dropped: List[str] = []
        self.fail_store_drop: Optional[BaseException] = None
        self.tuple_single = False  # get_vector_by_document_and_chunk отдаёт (dv, score), как project

    # --- запись ---
    async def create_vectors_batch(self, vectors: List[DocumentVector]) -> int:
        self.create_calls += 1
        if self.fail_create:
            raise self.fail_create
        if self.fail_create_on_call is not None and self.create_calls == self.fail_create_on_call:
            raise RuntimeError("insert failed")
        for v in vectors:
            self.rows.setdefault(v.document_id, []).append(v.model_copy(deep=True))
        return len(vectors)

    async def delete_vectors_by_document(self, document_id: int) -> bool:
        if self.fail_delete:
            raise self.fail_delete
        self.rows.pop(document_id, None)
        return True

    async def clone_vectors_to_document(self, source_document_id: int, target_document_id: int) -> int:
        if self.fail_clone:
            raise self.fail_clone
        src = self.rows.get(source_document_id, [])
        self.rows[target_document_id] = [
            v.model_copy(update={"document_id": target_document_id}, deep=True) for v in src
        ]
        return len(src)

    # --- чтение ---
    def _doc(self, doc_id: int):
        repo = self.doc_repo
        if repo is None:
            return None
        if hasattr(repo, "rows"):
            return repo.rows.get(doc_id)
        return repo.docs.get(doc_id)

    def _allowed(self, v, *, project_id=None, document_id=None, document_ids=None, filters=None) -> bool:
        if document_ids:
            if v.document_id not in {int(x) for x in document_ids}:
                return False
        elif document_id is not None and v.document_id != document_id:
            return False
        doc = self._doc(v.document_id)
        if project_id is not None and (not doc or doc["project_id"] != project_id):
            return False
        if filters is not None and filters.active() and doc is not None:
            name = doc["filename"] if isinstance(doc, dict) else doc.filename
            created = doc["created_at"] if isinstance(doc, dict) else doc.created_at
            if filters.filename_contains and filters.filename_contains.lower() not in name.lower():
                return False
            if filters.date_from and created < filters.date_from:
                return False
            if filters.date_to and created > filters.date_to:
                return False
        return True

    def _all(self, **scope):
        for doc_id in sorted(self.rows):
            for v in self.rows[doc_id]:
                if self._allowed(v, **scope):
                    yield v

    async def similarity_search(self, query_embedding, limit=10, project_id=None, document_id=None,
                                document_ids=None, filters=None):
        scored = [
            (v, cosine(query_embedding, v.embedding or []))
            for v in self._all(project_id=project_id, document_id=document_id,
                               document_ids=document_ids, filters=filters)
        ]
        scored.sort(key=lambda p: p[1], reverse=True)
        return scored[:limit]

    async def keyword_search(self, query_text, limit=20, project_id=None, document_id=None,
                             document_ids=None, filters=None):
        words = [w for w in (query_text or "").lower().split() if len(w) > 3]
        out = []
        for v in self._all(project_id=project_id, document_id=document_id,
                           document_ids=document_ids, filters=filters):
            n = sum(1 for w in words if w[:5] in v.content.lower())
            if n:
                out.append((v, float(n)))
        out.sort(key=lambda p: p[1], reverse=True)
        return out[:limit]

    async def substring_search(self, tokens, limit=32, project_id=None, document_id=None, document_ids=None):
        out = []
        for v in self._all(project_id=project_id, document_id=document_id, document_ids=document_ids):
            n = sum(1 for t in tokens if t.lower() in v.content.lower())
            if n:
                out.append((v, float(n)))
        return out[:limit]

    async def get_vectors_by_document(self, document_id: int) -> List[DocumentVector]:
        return [
            v.model_copy(update={"embedding": None})
            for v in sorted(self.rows.get(document_id, []), key=lambda v: v.chunk_index)
        ]

    async def get_chunk_contents_by_indices(self, document_id: int, chunk_indices: List[int]) -> Dict[int, str]:
        wanted = {int(i) for i in chunk_indices or [] if i is not None and int(i) >= 0}
        return {v.chunk_index: v.content for v in self.rows.get(document_id, []) if v.chunk_index in wanted}

    async def get_vector_by_document_and_chunk(self, document_id: int, chunk_index: int):
        for v in self.rows.get(document_id, []):
            if v.chunk_index == chunk_index:
                return (v, 0.0) if self.tuple_single else v
        return None

    async def get_all_document_ids(self, project_id=None) -> List[int]:
        return sorted({v.document_id for v in self._all(project_id=project_id)})

    async def get_all_contents_for_bm25(self, document_ids=None, project_id=None):
        return [
            (v.document_id, v.chunk_index, v.content)
            for v in self._all(project_id=project_id, document_ids=document_ids)
        ]

    # --- хранилище BM25 ---
    def _fingerprint(self, **scope) -> str:
        h = hashlib.sha256()
        for v in self._all(**scope):
            h.update(f"{v.document_id}:{v.chunk_index}:{v.content}".encode())
        return h.hexdigest()

    async def bm25_store_load(self, cache_key, document_ids=None, project_id=None):
        fp = self._fingerprint(document_ids=document_ids, project_id=project_id)
        saved = self.store.get(cache_key)
        return fp, (saved[1] if saved and saved[0] == fp else None)

    async def bm25_store_save(self, cache_key, fingerprint, payload, chunk_count):
        self.store[cache_key] = (fingerprint, payload, chunk_count)

    async def bm25_store_drop(self, project_id: str) -> int:
        if self.fail_store_drop:
            raise self.fail_store_drop
        self.store_dropped.append(project_id)
        return 1
