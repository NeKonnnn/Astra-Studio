"""ensure_project_library_kb / _attach_kb_to_project — без NameError ProjectUpdate."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.services import legacy_rag_to_kb as mod

pytestmark = pytest.mark.unit


class _FakeProjectRepo:
    def __init__(self, proj: Dict[str, Any]):
        self.proj = proj
        self.updates: List[Any] = []

    async def get_by_id(self, user_id: str, project_id: str) -> Optional[Dict[str, Any]]:
        return self.proj

    async def update(self, user_id: str, project_id: str, data: Any) -> Dict[str, Any]:
        self.updates.append(data)
        payload = data.model_dump(by_alias=True, exclude_none=True)
        self.proj = {**self.proj, **payload}
        return self.proj


class _FakeKb:
    def __init__(self, kb_id: int, meta: Optional[Dict[str, Any]] = None):
        self.id = kb_id
        self.meta = meta or {
            "source": mod.META_SOURCE_PROJECT,
            mod.META_PROJECT_ID: "p1",
        }


@pytest.mark.asyncio
async def test_attach_kb_to_project_imports_project_update(monkeypatch):
    """Регрессия: NameError ProjectUpdate валил upload документов (Failed to fetch)."""
    prepo = _FakeProjectRepo({"id": "p1", "name": "Demo", "knowledgeBaseIds": []})

    await mod._attach_kb_to_project(prepo, "admin", "p1", prepo.proj, 42)

    assert len(prepo.updates) == 1
    upd = prepo.updates[0]
    assert upd.knowledge_bases_enabled is True
    assert upd.knowledge_base_ids == [42]


@pytest.mark.asyncio
async def test_attach_kb_to_project_dedupes_existing_id():
    prepo = _FakeProjectRepo(
        {"id": "p1", "name": "Demo", "knowledge_base_ids": [7, "42"]}
    )
    await mod._attach_kb_to_project(prepo, "admin", "p1", prepo.proj, 42)
    assert prepo.updates[0].knowledge_base_ids == [7, 42]


@pytest.mark.asyncio
async def test_ensure_project_library_kb_attaches_found_kb(monkeypatch):
    prepo = _FakeProjectRepo({"id": "p1", "name": "Demo", "knowledgeBaseIds": []})
    found = _FakeKb(99)

    monkeypatch.setattr(mod, "find_kb_by_meta_source", AsyncMock(return_value=found))

    kb_repo = MagicMock()
    kb_repo.get_by_id = AsyncMock(return_value=None)
    kb_repo.create = AsyncMock(side_effect=AssertionError("create must not run"))

    monkeypatch.setattr(
        "backend.database.init_db.get_project_repository", lambda: prepo
    )
    monkeypatch.setattr(
        "backend.database.init_db.get_knowledge_base_repository", lambda: kb_repo
    )

    item = await mod.ensure_project_library_kb("admin", "p1", project_name="Demo")
    assert item is found
    assert prepo.updates and prepo.updates[0].knowledge_base_ids == [99]
