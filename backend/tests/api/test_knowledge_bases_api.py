"""api_knowledge_bases: CRUD БЗ, права, шаринг, duplicate, ensure-*-library."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend import api_knowledge_bases as module
from backend.auth import agent_platform_roles as roles
from backend.database.postgresql.knowledge_base_models import (
    KnowledgeBaseListItem,
    KnowledgeBaseOut,
    KnowledgeBaseShare,
)
from backend.tests.api.conftest import ALICE

pytestmark = pytest.mark.unit

BASE = "/api/knowledge-bases"
BOB = {**ALICE, "username": "bob", "user_id": "bob"}
ADMIN = {**ALICE, "username": "root", "user_id": "root", "is_admin": True}
_NOW = datetime(2024, 1, 1)


def _kb(kid, author="alice", **kw):
    return KnowledgeBaseOut(
        id=kid,
        name=kw.pop("name", f"KB {kid}"),
        description=kw.pop("description", None),
        meta=kw.pop("meta", {}),
        is_public=kw.pop("is_public", False),
        document_count=kw.pop("document_count", 0),
        author_id=author,
        author_name=author,
        created_at=kw.pop("created_at", _NOW),
        updated_at=kw.pop("updated_at", _NOW),
        my_permission=kw.pop("my_permission", "owner"),
        write_access=kw.pop("write_access", True),
        **kw,
    )


class FakeRepo:
    """Репозиторий с видимостью «автор / публичный / шаринг»."""

    def __init__(self):
        self.items = {}
        self.calls = []
        self.bookmarks = {}
        self.shares = {}  # kb_id -> {user_id: permission}
        self.share_result = (["bob"], [])

    def add(self, item):
        self.items[item.id] = item
        return item

    def _visible(self, item, uid, is_admin):
        if is_admin or item.author_id == uid or item.is_public:
            return True
        return uid in self.shares.get(item.id, {})

    def _writable(self, item, uid, is_admin):
        if is_admin or item.author_id == uid:
            return True
        return self.shares.get(item.id, {}).get(uid) == "editor"

    async def list_items(self, uid, filters, is_admin=False):
        self.calls.append(
            ("list", uid, filters.limit, filters.offset, filters.search_query, filters.view_option)
        )
        visible = [i for i in self.items.values() if self._visible(i, uid, is_admin)]
        page = visible[filters.offset : filters.offset + filters.limit]
        return [
            KnowledgeBaseListItem(**i.model_dump(exclude={"write_access"}))
            for i in page
        ], len(visible)

    async def create(self, data, author_id, author_name):
        self.calls.append(("create", author_id, author_name, data.is_public, data.name))
        kid = max(self.items, default=0) + 1
        return self.add(
            _kb(
                kid,
                author_id,
                name=data.name,
                description=data.description,
                meta=dict(data.meta or {}),
                is_public=data.is_public,
            )
        )

    async def get_by_id(self, kid, uid, is_admin=False):
        item = self.items.get(kid)
        if not item or not self._visible(item, uid, is_admin):
            return None
        out = item.model_copy(deep=True)
        if item.author_id == uid or is_admin:
            out.my_permission = "owner"
            out.write_access = True
        elif uid in self.shares.get(kid, {}):
            out.my_permission = self.shares[kid][uid]
            out.write_access = out.my_permission == "editor"
            out.is_shared_with_me = True
        else:
            out.my_permission = "viewer"
            out.write_access = False
        return out

    async def update(self, kid, data, uid, is_admin=False):
        item = self.items.get(kid)
        if not item or not self._writable(item, uid, is_admin):
            return None
        self.calls.append(("update", kid, uid))
        for key, value in data.model_dump(exclude_none=True).items():
            setattr(item, key, value)
        return item

    async def delete(self, kid, uid, is_admin=False):
        item = self.items.get(kid)
        if not item or not self._writable(item, uid, is_admin):
            return False
        del self.items[kid]
        self.calls.append(("delete", kid, uid))
        return True

    async def can_write(self, kid, uid, is_admin=False):
        item = self.items.get(kid)
        return bool(item and self._writable(item, uid, is_admin))

    async def can_read(self, kid, uid, is_admin=False):
        item = self.items.get(kid)
        return bool(item and self._visible(item, uid, is_admin))

    async def get_user_permission(self, kid, uid, is_admin=False):
        item = await self.get_by_id(kid, uid, is_admin=is_admin)
        return getattr(item, "my_permission", None) if item else None

    async def share(self, kb_id, owner_id, usernames, permission, recipient_permissions):
        item = self.items.get(kb_id)
        if not item or item.author_id != owner_id:
            return [], list(usernames)
        self.calls.append(("share", kb_id, owner_id, list(usernames), permission))
        for uid in usernames:
            self.shares.setdefault(kb_id, {})[uid] = (
                recipient_permissions.get(uid) if recipient_permissions else permission
            ) or permission
        return self.share_result

    async def list_shares(self, kb_id):
        return [
            KnowledgeBaseShare(
                knowledge_base_id=kb_id,
                owner_id=self.items[kb_id].author_id,
                shared_with_user_id=uid,
                permission=perm,
            )
            for uid, perm in self.shares.get(kb_id, {}).items()
        ]

    async def remove_share(self, kb_id, shared_with_user_id):
        bucket = self.shares.get(kb_id, {})
        if shared_with_user_id not in bucket:
            return False
        del bucket[shared_with_user_id]
        return True

    async def rate_knowledge_base(self, kb_id, user_id, rating):
        self.calls.append(("rate", kb_id, user_id, rating))
        return kb_id in self.items

    async def increment_views(self, kid):
        self.calls.append(("views", kid))
        return kid in self.items

    async def increment_usage(self, kid):
        self.calls.append(("usage", kid))
        return kid in self.items

    async def add_bookmark(self, kid, uid):
        self.bookmarks.setdefault(uid, set()).add(kid)
        return kid in self.items

    async def remove_bookmark(self, kid, uid):
        marks = self.bookmarks.get(uid, set())
        if kid not in marks:
            return False
        marks.discard(kid)
        return True

    async def get_user_bookmarks(self, uid, limit, offset):
        ids = sorted(self.bookmarks.get(uid, set()))
        page_ids = ids[offset : offset + limit]
        items = [self.items[i] for i in page_ids if i in self.items]
        return [
            KnowledgeBaseListItem(**i.model_dump(exclude={"write_access"}))
            for i in items
        ], len(ids)

    async def set_document_count(self, kb_id, count):
        if kb_id in self.items:
            self.items[kb_id].document_count = int(count)

    async def bump_document_count(self, kb_id, delta=1):
        if kb_id in self.items:
            self.items[kb_id].document_count += int(delta)


@pytest.fixture(autouse=True)
def _role_caches(monkeypatch):
    for name in (
        "AGENT_PLATFORM_ROLE_MAPPING_ENABLED",
        "AGENT_ROLE_OWNER_GROUPS",
        "AGENT_ROLE_EDITOR_GROUPS",
        "AGENT_ROLE_VIEWER_GROUPS",
        "AGENT_ROLE_BASE_USER_GROUPS",
    ):
        monkeypatch.delenv(name, raising=False)
    roles.is_agent_platform_role_mapping_enabled.cache_clear()
    roles._allowed_sets.cache_clear()
    yield
    roles.is_agent_platform_role_mapping_enabled.cache_clear()
    roles._allowed_sets.cache_clear()


@pytest.fixture
def repo(monkeypatch):
    fake = FakeRepo()
    monkeypatch.setattr(module, "get_knowledge_base_repository", lambda: fake)

    async def no_enrich(items):
        return None

    monkeypatch.setattr(module, "enrich_items_author_full_names", no_enrich)
    return fake


@pytest.fixture
def with_mapping(monkeypatch):
    monkeypatch.setenv("AGENT_ROLE_OWNER_GROUPS", "Owners")
    monkeypatch.setenv("AGENT_ROLE_EDITOR_GROUPS", "Editors")
    monkeypatch.setenv("AGENT_ROLE_VIEWER_GROUPS", "Viewers")
    monkeypatch.setenv("AGENT_ROLE_BASE_USER_GROUPS", "Basic")
    roles.is_agent_platform_role_mapping_enabled.cache_clear()
    roles._allowed_sets.cache_clear()


def _as(make_client, user=ALICE):
    return make_client(module.router, user=user)


def _in_group(group, base=ALICE):
    return {**base, "groups": [group], "ldap_groups": []}


class TestAuthentication:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("GET", "/"),
            ("GET", "/list"),
            ("POST", "/"),
            ("GET", "/1"),
            ("PUT", "/1"),
            ("DELETE", "/1"),
            ("POST", "/1/share"),
            ("GET", "/1/shares"),
            ("POST", "/1/duplicate"),
            ("POST", "/ensure-personal-library"),
        ],
    )
    async def test_everything_requires_login(self, make_client, repo, method, path):
        response = await make_client(module.router, user=None).request(
            method, BASE + path, json={} if method in ("POST", "PUT") else None
        )
        assert response.status_code in (401, 403)
        assert repo.calls == []


class TestListing:
    async def test_user_sees_own_and_public(self, make_client, repo):
        repo.add(_kb(1, "alice"))
        repo.add(_kb(2, "bob", is_public=True))
        repo.add(_kb(3, "bob"))

        body = (await _as(make_client).get(BASE + "/")).json()

        assert sorted(x["id"] for x in body) == [1, 2]

    async def test_paginated_listing(self, make_client, repo):
        for i in range(1, 6):
            repo.add(_kb(i, "alice"))

        body = (
            await _as(make_client).get(
                BASE + "/list",
                params={"page": 2, "limit": 2, "query": "x", "view_option": "created"},
            )
        ).json()

        assert (body["page"], body["total"], body["pages"], len(body["items"])) == (
            2,
            5,
            3,
            2,
        )
        assert repo.calls[-1] == ("list", "alice", 2, 2, "x", "created")

    async def test_admin_sees_all(self, make_client, repo):
        for i in range(1, 4):
            repo.add(_kb(i, "someone"))
        assert len((await _as(make_client, ADMIN).get(BASE + "/")).json()) == 3


class TestCrud:
    async def test_create_author_from_token(self, make_client, repo):
        response = await _as(make_client).post(
            BASE + "/", json={"name": "Новая", "description": "d"}
        )
        assert response.status_code == 201
        assert response.json()["author_id"] == "alice"
        assert repo.calls[-1][:3] == ("create", "alice", "alice")

    async def test_get_update_delete_own(self, make_client, repo, monkeypatch):
        repo.add(_kb(1, "alice", name="A"))

        got = await _as(make_client).get(BASE + "/1")
        assert got.status_code == 200 and got.json()["name"] == "A"

        updated = await _as(make_client).put(BASE + "/1", json={"name": "B"})
        assert updated.status_code == 200 and updated.json()["name"] == "B"

        async def no_cleanup(_kid):
            return 0

        monkeypatch.setattr(module, "_cleanup_knowledge_base_documents", no_cleanup)
        deleted = await _as(make_client).delete(BASE + "/1")
        assert deleted.status_code == 200 and deleted.json()["success"] is True
        assert 1 not in repo.items

    async def test_foreign_private_hidden(self, make_client, repo):
        repo.add(_kb(1, "alice"))
        assert (await _as(make_client, BOB).get(BASE + "/1")).status_code == 404
        assert (await _as(make_client, BOB).put(BASE + "/1", json={"name": "x"})).status_code == 404
        assert (await _as(make_client, BOB).delete(BASE + "/1")).status_code == 404

    async def test_viewers_cannot_create(self, make_client, repo, with_mapping):
        response = await _as(make_client, _in_group("Viewers")).post(
            BASE + "/", json={"name": "N"}
        )
        assert response.status_code == 403 and repo.calls == []

    async def test_editors_create_private_only(self, make_client, repo, with_mapping):
        response = await _as(make_client, _in_group("Editors")).post(
            BASE + "/", json={"name": "N", "is_public": True}
        )
        assert response.status_code == 201
        assert repo.calls[-1][3] is False


class TestShare:
    async def test_author_shares(self, make_client, repo, monkeypatch):
        repo.add(_kb(1, "alice"))

        async def prepare(usernames, permission):
            return SimpleNamespace(
                to_share=[("bob", "viewer")],
                skipped=[],
            )

        monkeypatch.setattr(module, "prepare_share_recipients", prepare)
        monkeypatch.setattr(
            module,
            "build_share_api_response",
            lambda *a, **k: {"success": True, "shared_with": ["bob"], "message": "ok"},
        )

        response = await _as(make_client).post(
            BASE + "/1/share", json={"usernames": ["bob"], "permission": "viewer"}
        )
        assert response.status_code == 200
        assert repo.calls[-1][0] == "share"
        assert "bob" in repo.shares[1]

    async def test_shares_list_and_unshare(self, make_client, repo):
        repo.add(_kb(1, "alice"))
        repo.shares[1] = {"bob": "viewer"}

        body = (await _as(make_client).get(BASE + "/1/shares")).json()
        assert body["owner"]["user_id"] == "alice"
        assert body["shares"][0]["user_id"] == "bob"

        ok = await _as(make_client).delete(BASE + "/1/share/bob")
        assert ok.status_code == 200 and ok.json()["success"] is True
        assert "bob" not in repo.shares[1]

    async def test_shared_user_can_read(self, make_client, repo):
        repo.add(_kb(1, "alice"))
        repo.shares[1] = {"bob": "viewer"}
        assert (await _as(make_client, BOB).get(BASE + "/1")).status_code == 200


class TestDuplicate:
    async def test_duplicate_clones_card(self, make_client, repo, monkeypatch):
        repo.add(_kb(1, "alice", name="Исходная", description="d", meta={"a": 1}))

        async def no_docs(**kwargs):
            return []

        async def no_settings(*args):
            return None

        monkeypatch.setattr(module, "_clone_kb_documents_into", no_docs)
        monkeypatch.setattr(module, "_clone_kb_rag_settings", no_settings)

        response = await _as(make_client).post(BASE + "/1/duplicate")
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["knowledge_base_id"] == 2
        assert body["cloned_documents"] == 0
        assert repo.items[2].name.endswith("(копия)")
        assert repo.items[2].meta.get("cloned_from") == 1
        assert repo.items[2].is_public is False

    async def test_duplicate_requires_create_rights(
        self, make_client, repo, with_mapping
    ):
        repo.add(_kb(1, "alice", is_public=True))
        response = await _as(make_client, _in_group("Viewers")).post(BASE + "/1/duplicate")
        assert response.status_code == 403


class TestEnsureLibraries:
    async def test_ensure_personal(self, make_client, repo, monkeypatch):
        item = _kb(9, "alice", name="Библиотека", meta={"source": "memory_library"})

        async def ensure(uid, author_name=None):
            return item

        monkeypatch.setattr(
            "backend.services.legacy_rag_to_kb.ensure_personal_library_kb", ensure
        )
        response = await _as(make_client).post(BASE + "/ensure-personal-library")
        assert response.status_code == 200
        assert response.json()["id"] == 9

    async def test_ensure_project(self, make_client, repo, monkeypatch):
        item = _kb(10, "alice", meta={"source": "project_library", "project_id": "p1"})

        async def ensure(uid, project_id, project_name=None, author_name=None):
            assert project_id == "p1"
            return item

        monkeypatch.setattr(
            "backend.services.legacy_rag_to_kb.ensure_project_library_kb", ensure
        )
        response = await _as(make_client).post(
            BASE + "/ensure-project-library", params={"project_id": "p1"}
        )
        assert response.status_code == 200
        assert response.json()["id"] == 10

    async def test_ensure_agent(self, make_client, repo, monkeypatch):
        item = _kb(11, "alice", meta={"source": "agent_library", "agent_id": 5})

        async def ensure(uid, agent_id, agent_name=None, author_name=None):
            assert agent_id == 5
            return item

        monkeypatch.setattr(
            "backend.services.legacy_rag_to_kb.ensure_agent_library_kb", ensure
        )
        response = await _as(make_client).post(
            BASE + "/ensure-agent-library", params={"agent_id": 5}
        )
        assert response.status_code == 200
        assert response.json()["id"] == 11

    async def test_ensure_personal_failure(self, make_client, repo, monkeypatch):
        async def ensure(*a, **k):
            return None

        monkeypatch.setattr(
            "backend.services.legacy_rag_to_kb.ensure_personal_library_kb", ensure
        )
        assert (
            await _as(make_client).post(BASE + "/ensure-personal-library")
        ).status_code == 400


class TestPermissionsExtras:
    async def test_rate_view_use_bookmark(self, make_client, repo):
        repo.add(_kb(1, "alice"))
        client = _as(make_client)

        assert (await client.post(BASE + "/1/rate", json={"rating": 5})).status_code == 200
        assert (await client.post(BASE + "/1/view")).status_code == 200
        assert (await client.post(BASE + "/1/use")).status_code == 200
        assert (await client.post(BASE + "/1/bookmark")).status_code == 200
        assert (await client.delete(BASE + "/1/bookmark")).status_code == 200
        assert ("rate", 1, "alice", 5) in repo.calls
        assert ("views", 1) in repo.calls
        assert ("usage", 1) in repo.calls
