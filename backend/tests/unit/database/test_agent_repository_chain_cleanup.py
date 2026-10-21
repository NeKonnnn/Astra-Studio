"""AgentRepository: чистка цепочек/субагентов, когда JSONB приходит строкой (пул без JSON-кодека)."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.database.postgresql.agent_repository import AgentRepository, _config_as_dict

pytestmark = pytest.mark.unit


class _Acquired:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


def _repo_with_rows(rows):
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=rows)
    conn.execute = AsyncMock()
    db = MagicMock()
    db.acquire = AsyncMock(return_value=_Acquired(conn))
    return AgentRepository(db), conn


@pytest.mark.parametrize(
    "raw, expected",
    [
        ({"a": 1}, {"a": 1}),
        ('{"a": 1}', {"a": 1}),
        (b'{"a": 1}', {"a": 1}),
        ("", {}),
        (None, {}),
        ("not json", {}),
        ("[1, 2]", {}),
    ],
)
def test_config_as_dict(raw, expected):
    assert _config_as_dict(raw) == expected


def test_chain_cleanup_works_with_string_jsonb():
    cfg = json.dumps({"agent_ids": [7, 8, "junk"], "keep": True})
    repo, conn = _repo_with_rows([{"id": 1, "config": cfg}])

    asyncio.run(repo.remove_agent_from_chains(7))

    conn.execute.assert_awaited_once()
    payload = json.loads(conn.execute.await_args.args[1])
    assert payload == {"agent_ids": [8], "keep": True}
    assert conn.execute.await_args.args[2] == 1


def test_chain_cleanup_works_with_dict_jsonb():
    repo, conn = _repo_with_rows([{"id": 1, "config": {"agent_ids": [7, 8]}}])

    asyncio.run(repo.remove_agent_from_chains(7))

    assert json.loads(conn.execute.await_args.args[1]) == {"agent_ids": [8]}


def test_chain_cleanup_skips_cards_that_do_not_reference_the_agent():
    repo, conn = _repo_with_rows([{"id": 1, "config": json.dumps({"agent_ids": [8, 9]})}])

    asyncio.run(repo.remove_agent_from_chains(7))

    conn.execute.assert_not_awaited()


def test_subagents_cleanup_works_with_string_jsonb():
    cfg = json.dumps({"subagents": {"agent_ids": [3, 7], "mode": "x"}})
    repo, conn = _repo_with_rows([{"id": 2, "config": cfg}])

    asyncio.run(repo.remove_agent_from_subagents(7))

    payload = json.loads(conn.execute.await_args.args[1])
    assert payload == {"subagents": {"agent_ids": [3], "mode": "x"}}
    assert conn.execute.await_args.args[2] == 2
