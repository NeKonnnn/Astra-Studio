"""pull_named_kb_hits: дотягивание чанков, когда в вопросе назван файл."""

from __future__ import annotations

import pytest

from backend.realtime import helpers as h


@pytest.mark.asyncio
async def test_pull_named_kb_hits_merges_extra(monkeypatch):
    names = {10: "AstraChat_audit_plan.md", 11: "other.txt"}
    calls = []

    async def fake_scoped(*args, **kwargs):
        only = kwargs.get("only_document_ids") or []
        calls.append(only)
        if only == [10]:
            return [("chunk about audit", 0.9, 10, 0)]
        return []

    monkeypatch.setattr(h, "kb_search_scoped_documents", fake_scoped)
    out = await h.pull_named_kb_hits(
        object(),
        "что в файле AstraChat_audit_plan.md?",
        [],
        names,
        only_ids=[10, 11],
        knowledge_base_ids=[1],
        k=4,
    )
    assert len(out) == 1
    assert out[0][2] == 10
    assert calls and calls[0] == [10]


@pytest.mark.asyncio
async def test_pull_named_kb_hits_no_name_noop(monkeypatch):
    async def boom(*_a, **_k):
        raise AssertionError("search must not run")

    monkeypatch.setattr(h, "kb_search_scoped_documents", boom)
    base = [("x", 0.5, 1, 0)]
    out = await h.pull_named_kb_hits(
        object(), "привет", base, {1: "a.md"}, only_ids=[1]
    )
    assert out == base
