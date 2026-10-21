"""api_skills: CRUD навыков, права автора и платформенных ролей, публикация, шаринг, закладки."""

from types import SimpleNamespace

import pytest

from backend import api_skills as module
from backend.auth import agent_platform_roles as roles
from backend.database.postgresql.skill_models import SkillListItem, SkillOut
from backend.tests.api.conftest import ALICE

pytestmark = pytest.mark.unit

BASE = "/api/skills"
BOB = {**ALICE, "username": "bob", "user_id": "bob"}
ADMIN = {**ALICE, "username": "root", "user_id": "root", "is_admin": True}


def _skill(sid, author="alice", **kw):
    return SkillOut(
        id=sid,
        slug=kw.pop("slug", f"skill-{sid}"),
        name=kw.pop("name", f"Skill {sid}"),
        content=kw.pop("content", "тело"),
        author_id=author,
        author_name=author,
        my_permission=kw.pop("my_permission", "owner"),
        **kw,
    )


class FakeRepo:
    """Репозиторий с видимостью «автор или публичный» и правами записи только у автора/админа."""

    def __init__(self):
        self.skills = {}
        self.calls = []
        self.bookmarks = {}
        self.share_result = (["bob"], [])

    def add(self, skill):
        self.skills[skill.id] = skill
        return skill

    def _visible(self, skill, uid, is_admin):
        return is_admin or skill.author_id == uid or skill.is_public

    def _writable(self, skill, uid, is_admin):
        return is_admin or skill.author_id == uid

    async def list_skills(self, uid, filters, is_admin=False):
        self.calls.append(("list", uid, filters.limit, filters.offset, filters.search_query, filters.view_option))
        visible = [s for s in self.skills.values() if self._visible(s, uid, is_admin)]
        page = visible[filters.offset : filters.offset + filters.limit]
        return [SkillListItem(**s.model_dump(exclude={"content", "write_access"})) for s in page], len(visible)

    async def get_accessible_skills(self, uid, is_admin=False):
        return [s for s in self.skills.values() if self._visible(s, uid, is_admin)]

    async def create_skill(self, data, author_id, author_name):
        self.calls.append(("create", author_id, author_name, data.is_public))
        if any(s.slug == (data.slug or data.name) for s in self.skills.values()):
            return None
        sid = max(self.skills, default=0) + 1
        return self.add(_skill(sid, author_id, slug=data.slug or data.name.lower(), name=data.name, content=data.content, is_public=data.is_public))

    async def get_skill_by_id(self, sid, uid, is_admin=False):
        skill = self.skills.get(sid)
        return skill if skill and self._visible(skill, uid, is_admin) else None

    async def get_skill_by_slug(self, slug, uid, is_admin=False):
        for skill in self.skills.values():
            if skill.slug == slug and self._visible(skill, uid, is_admin):
                return skill
        return None

    async def increment_views(self, sid):
        self.calls.append(("views", sid))
        return sid in self.skills

    async def increment_usage(self, sid):
        self.calls.append(("usage", sid))
        return sid in self.skills

    async def can_read(self, sid, uid, is_admin=False):
        skill = self.skills.get(sid)
        return bool(skill and self._visible(skill, uid, is_admin))

    async def rate_skill(self, skill_id, user_id, rating):
        self.calls.append(("rate", skill_id, user_id, rating))
        return skill_id in self.skills

    async def add_bookmark(self, sid, uid):
        self.bookmarks.setdefault(uid, set()).add(sid)
        return sid in self.skills

    async def remove_bookmark(self, sid, uid):
        marks = self.bookmarks.get(uid, set())
        if sid not in marks:
            return False
        marks.discard(sid)
        return True

    async def get_user_bookmarks(self, uid, limit, offset):
        ids = sorted(self.bookmarks.get(uid, set()))
        return ids[offset : offset + limit], len(ids)

    async def update_skill(self, sid, data, uid, is_admin=False):
        skill = self.skills.get(sid)
        if not skill or not self._writable(skill, uid, is_admin):
            return None
        self.calls.append(("update", sid, uid))
        for key, value in data.model_dump(exclude_none=True).items():
            setattr(skill, key, value)
        return skill

    async def toggle_skill(self, sid, uid, is_admin=False):
        skill = self.skills.get(sid)
        if not skill or not self._writable(skill, uid, is_admin):
            return None
        skill.is_active = not skill.is_active
        return skill

    async def delete_skill(self, sid, uid, is_admin=False):
        skill = self.skills.get(sid)
        if not skill or not self._writable(skill, uid, is_admin):
            return False
        del self.skills[sid]
        self.calls.append(("delete", sid, uid))
        return True

    async def share_skill(self, skill_id, owner_id, usernames, permission, recipient_permissions):
        # Как в настоящем репозитории: делиться может только автор карточки.
        skill = self.skills.get(skill_id)
        if not skill or skill.author_id != owner_id:
            return [], usernames
        self.calls.append(("share", skill_id, owner_id, list(usernames), permission))
        return self.share_result


@pytest.fixture(autouse=True)
def _role_caches(monkeypatch):
    for name in ("AGENT_PLATFORM_ROLE_MAPPING_ENABLED", "AGENT_ROLE_OWNER_GROUPS", "AGENT_ROLE_EDITOR_GROUPS", "AGENT_ROLE_VIEWER_GROUPS", "AGENT_ROLE_BASE_USER_GROUPS"):
        monkeypatch.delenv(name, raising=False)
    roles.is_agent_platform_role_mapping_enabled.cache_clear()
    roles._allowed_sets.cache_clear()
    yield
    roles.is_agent_platform_role_mapping_enabled.cache_clear()
    roles._allowed_sets.cache_clear()


@pytest.fixture
def repo(monkeypatch):
    fake = FakeRepo()
    monkeypatch.setattr(module, "get_skill_repository", lambda: fake)

    async def no_enrich(items):
        return None

    monkeypatch.setattr(module, "enrich_items_author_full_names", no_enrich)
    return fake


@pytest.fixture
def with_mapping(monkeypatch):
    """Включает платформенные роли: owner=Owners, editor=Editors, viewer=Viewers, base=Basic."""
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
            ("GET", "/"), ("GET", "/list"), ("GET", "/export"), ("POST", "/"), ("GET", "/1"), ("PUT", "/1"),
            ("DELETE", "/1"), ("POST", "/1/toggle"), ("POST", "/1/share"), ("GET", "/1/shares"), ("POST", "/1/rate"),
            ("POST", "/1/view"), ("POST", "/1/use"), ("POST", "/1/bookmark"), ("DELETE", "/1/bookmark"), ("GET", "/my/bookmarks"),
        ],
    )
    async def test_everything_requires_login(self, make_client, repo, method, path):
        response = await make_client(module.router, user=None).request(method, BASE + path, json={} if method in ("POST", "PUT") else None)

        assert response.status_code in (401, 403)
        assert repo.calls == []


class TestListing:
    async def test_user_sees_own_and_public_skills(self, make_client, repo):
        repo.add(_skill(1, "alice"))
        repo.add(_skill(2, "bob", is_public=True))
        repo.add(_skill(3, "bob"))

        body = (await _as(make_client).get(BASE + "/")).json()

        assert sorted(s["id"] for s in body) == [1, 2]

    async def test_paginated_listing_forwards_filters(self, make_client, repo):
        for i in range(1, 6):
            repo.add(_skill(i, "alice"))

        body = (await _as(make_client).get(BASE + "/list", params={"page": 2, "limit": 2, "query": "x", "view_option": "created"})).json()

        assert (body["page"], body["total"], body["pages"], len(body["items"])) == (2, 5, 3, 2)
        assert repo.calls[-1] == ("list", "alice", 2, 2, "x", "created")

    async def test_empty_listing(self, make_client, repo):
        assert (await _as(make_client).get(BASE + "/list")).json() == {"items": [], "total": 0, "page": 1, "pages": 0}

    async def test_admin_sees_every_skill(self, make_client, repo):
        for i in range(1, 4):
            repo.add(_skill(i, "someone"))

        assert len((await _as(make_client, ADMIN).get(BASE + "/")).json()) == 3

    @pytest.mark.parametrize("params", [{"page": 0}, {"limit": 0}, {"limit": 1001}])
    async def test_validates_pagination(self, make_client, repo, params):
        assert (await _as(make_client).get(BASE + "/list", params=params)).status_code == 422

    async def test_export_includes_content_for_accessible_skills_only(self, make_client, repo):
        repo.add(_skill(1, "alice", content="мой текст"))
        repo.add(_skill(2, "bob", content="чужой секрет"))

        body = (await _as(make_client).get(BASE + "/export")).json()

        assert [s["content"] for s in body] == ["мой текст"]


class TestGetSkill:
    async def test_by_id_and_by_slug_and_view_is_counted(self, make_client, repo):
        repo.add(_skill(7, "alice", slug="my-skill"))

        by_id = await _as(make_client).get(BASE + "/7")
        by_slug = await _as(make_client).get(BASE + "/my-skill")

        assert by_id.json()["id"] == by_slug.json()["id"] == 7
        assert repo.calls.count(("views", 7)) == 2

    async def test_private_skill_of_someone_else_is_hidden(self, make_client, repo):
        repo.add(_skill(7, "alice", content="секрет"))

        response = await _as(make_client, BOB).get(BASE + "/7")

        assert response.status_code == 404 and "секрет" not in response.text

    async def test_public_skill_is_readable(self, make_client, repo):
        repo.add(_skill(7, "alice", is_public=True))

        assert (await _as(make_client, BOB).get(BASE + "/7")).status_code == 200

    async def test_unknown_skill(self, make_client, repo):
        assert (await _as(make_client).get(BASE + "/999")).status_code == 404
        assert (await _as(make_client).get(BASE + "/no-such-slug")).status_code == 404

    async def test_id_route_alias(self, make_client, repo):
        repo.add(_skill(7, "alice"))

        assert (await _as(make_client).get(BASE + "/id/7")).status_code == 200


class TestCreate:
    async def test_author_comes_from_the_token_not_from_the_body(self, make_client, repo):
        response = await _as(make_client).post(BASE + "/", json={"name": "Новый", "content": "текст", "author_id": "bob"})

        assert response.status_code == 201
        assert response.json()["author_id"] == "alice"
        assert repo.calls[-1][:3] == ("create", "alice", "alice")

    async def test_conflict(self, make_client, repo):
        repo.add(_skill(1, "alice", slug="dup"))

        response = await _as(make_client).post(BASE + "/create", json={"name": "Dup", "slug": "dup", "content": "x"})

        assert response.status_code == 400

    @pytest.mark.parametrize("body", [{}, {"name": ""}, {"name": "x"}, {"name": "x", "content": ""}, {"name": "y" * 256, "content": "c"}])
    async def test_validates_payload(self, make_client, repo, body):
        assert (await _as(make_client).post(BASE + "/", json=body)).status_code == 422

    async def test_viewers_cannot_create(self, make_client, repo, with_mapping):
        response = await _as(make_client, _in_group("Viewers")).post(BASE + "/", json={"name": "N", "content": "c"})

        assert response.status_code == 403 and repo.calls == []

    async def test_editors_can_create_but_only_private_skills(self, make_client, repo, with_mapping):
        response = await _as(make_client, _in_group("Editors")).post(BASE + "/", json={"name": "N", "content": "c", "is_public": True})

        assert response.status_code == 201
        assert repo.calls[-1][3] is False

    async def test_owners_can_publish_on_creation(self, make_client, repo, with_mapping):
        await _as(make_client, _in_group("Owners")).post(BASE + "/", json={"name": "N", "content": "c", "is_public": True})

        assert repo.calls[-1][3] is True


class TestUpdate:
    async def test_author_updates_own_skill(self, make_client, repo):
        repo.add(_skill(1, "alice"))

        response = await _as(make_client).put(BASE + "/1", json={"name": "Новое имя"})

        assert response.status_code == 200 and response.json()["name"] == "Новое имя"

    async def test_foreign_private_skill_looks_missing(self, make_client, repo):
        repo.add(_skill(1, "alice"))

        assert (await _as(make_client, BOB).put(BASE + "/1", json={"name": "Взлом"})).status_code == 404
        assert repo.skills[1].name == "Skill 1"

    async def test_foreign_public_skill_cannot_be_changed_by_a_regular_user(self, make_client, repo):
        repo.add(_skill(1, "alice", is_public=True))

        response = await _as(make_client, BOB).put(BASE + "/1", json={"name": "Взлом"})

        assert response.status_code == 403 and repo.skills[1].name == "Skill 1"

    async def test_admin_can_change_any_skill(self, make_client, repo):
        repo.add(_skill(1, "alice"))

        assert (await _as(make_client, ADMIN).put(BASE + "/1", json={"name": "Админ"})).status_code == 200

    async def test_unknown_skill(self, make_client, repo):
        assert (await _as(make_client).put(BASE + "/9", json={"name": "x"})).status_code == 404

    async def test_empty_name_is_rejected(self, make_client, repo):
        repo.add(_skill(1, "alice"))

        assert (await _as(make_client).put(BASE + "/1", json={"name": ""})).status_code == 422

    async def test_editors_cannot_publish(self, make_client, repo, with_mapping):
        repo.add(_skill(1, "editor-user", is_public=False))
        editor = _in_group("Editors", {**ALICE, "username": "editor-user", "user_id": "editor-user"})

        response = await _as(make_client, editor).put(BASE + "/1", json={"is_public": True})

        assert response.status_code == 403 and repo.skills[1].is_public is False

    async def test_owners_can_publish(self, make_client, repo, with_mapping):
        repo.add(_skill(1, "alice"))

        response = await _as(make_client, _in_group("Owners")).put(BASE + "/1", json={"is_public": True})

        assert response.status_code == 200 and repo.skills[1].is_public is True

    async def test_viewer_author_may_still_edit_their_own_card(self, make_client, repo, with_mapping):
        repo.add(_skill(1, "alice"))

        assert (await _as(make_client, _in_group("Viewers")).put(BASE + "/1", json={"name": "Моё"})).status_code == 200

    async def test_viewer_cannot_edit_someone_elses_card_even_if_it_is_readable(self, make_client, repo, with_mapping):
        repo.add(_skill(1, "carol", is_public=True))

        assert (await _as(make_client, _in_group("Viewers")).put(BASE + "/1", json={"name": "Чужое"})).status_code == 403


class TestToggleAndDelete:
    async def test_toggle(self, make_client, repo):
        repo.add(_skill(1, "alice", is_active=True))

        assert (await _as(make_client).post(BASE + "/1/toggle")).json()["is_active"] is False

    async def test_toggle_by_non_author_is_forbidden(self, make_client, repo):
        repo.add(_skill(1, "alice", is_public=True))

        response = await _as(make_client, BOB).post(BASE + "/1/toggle")

        assert response.status_code == 403 and repo.skills[1].is_active is True

    async def test_viewer_role_cannot_toggle(self, make_client, repo, with_mapping):
        repo.add(_skill(1, "alice"))

        assert (await _as(make_client, _in_group("Viewers")).post(BASE + "/1/toggle")).status_code == 403

    async def test_author_deletes(self, make_client, repo):
        repo.add(_skill(1, "alice"))

        response = await _as(make_client).delete(BASE + "/1")

        assert response.json() == {"success": True, "message": "Skill удалён"} and 1 not in repo.skills

    async def test_foreign_private_skill_cannot_be_deleted(self, make_client, repo):
        repo.add(_skill(1, "alice"))

        assert (await _as(make_client, BOB).delete(BASE + "/1")).status_code == 404 and 1 in repo.skills

    async def test_foreign_public_skill_cannot_be_deleted(self, make_client, repo):
        repo.add(_skill(1, "alice", is_public=True))

        assert (await _as(make_client, BOB).delete(BASE + "/1")).status_code == 403 and 1 in repo.skills

    async def test_editor_role_cannot_delete_someone_elses_skill(self, make_client, repo, with_mapping):
        repo.add(_skill(1, "alice", is_public=True))

        assert (await _as(make_client, _in_group("Editors", BOB)).delete(BASE + "/1")).status_code == 403

    async def test_admin_deletes_any_skill(self, make_client, repo):
        repo.add(_skill(1, "alice"))

        assert (await _as(make_client, ADMIN).delete(BASE + "/1")).status_code == 200

    async def test_alias_routes(self, make_client, repo):
        repo.add(_skill(1, "alice"))
        repo.add(_skill(2, "alice"))

        assert (await _as(make_client).post(BASE + "/id/1/toggle")).status_code == 200
        assert (await _as(make_client).delete(BASE + "/id/2/delete")).status_code == 200


class TestSocialActions:
    @pytest.mark.parametrize("action", ["view", "use", "bookmark"])
    async def test_actions_are_hidden_for_unreadable_skills(self, make_client, repo, action):
        repo.add(_skill(1, "alice"))

        response = await _as(make_client, BOB).post(f"{BASE}/1/{action}")

        assert response.status_code == 404
        assert ("views", 1) not in repo.calls and ("usage", 1) not in repo.calls

    async def test_view_use_and_rate_on_a_public_skill(self, make_client, repo):
        repo.add(_skill(1, "alice", is_public=True))
        client = _as(make_client, BOB)

        assert (await client.post(BASE + "/1/view")).status_code == 200
        assert (await client.post(BASE + "/1/use")).status_code == 200
        assert (await client.post(BASE + "/1/rate", json={"rating": 5})).status_code == 200
        assert ("rate", 1, "bob", 5) in repo.calls

    @pytest.mark.parametrize("rating", [0, 6, "x"])
    async def test_rating_range(self, make_client, repo, rating):
        repo.add(_skill(1, "alice"))

        assert (await _as(make_client).post(BASE + "/1/rate", json={"rating": rating})).status_code == 422

    async def test_bookmarks(self, make_client, repo):
        repo.add(_skill(1, "alice"))
        client = _as(make_client)

        assert (await client.post(BASE + "/1/bookmark")).status_code == 200
        listing = (await client.get(BASE + "/my/bookmarks")).json()
        assert [i["id"] for i in listing["items"]] == [1] and "content" not in listing["items"][0]
        assert (await client.delete(BASE + "/1/bookmark")).status_code == 200
        assert (await client.get(BASE + "/my/bookmarks")).json()["items"] == []

    async def test_removing_a_missing_bookmark(self, make_client, repo):
        assert (await _as(make_client).delete(BASE + "/1/bookmark")).status_code == 404

    async def test_bookmarks_are_per_user(self, make_client, repo):
        repo.add(_skill(1, "alice", is_public=True))
        await _as(make_client).post(BASE + "/1/bookmark")

        assert (await _as(make_client, BOB).get(BASE + "/my/bookmarks")).json()["items"] == []


class TestSharing:
    @pytest.fixture(autouse=True)
    def _recipients(self, monkeypatch):
        async def prepare(usernames, permission):
            return SimpleNamespace(to_share=[(u.lower(), permission) for u in usernames], skipped=[])

        def respond(prepared, added, skipped, resource_label=""):
            return {"success": True, "added": list(added), "skipped": list(skipped)}

        monkeypatch.setattr(module, "prepare_share_recipients", prepare)
        monkeypatch.setattr(module, "build_share_api_response", respond)

    async def test_author_shares(self, make_client, repo):
        repo.add(_skill(1, "alice"))

        response = await _as(make_client).post(BASE + "/1/share", json={"usernames": ["Bob"], "permission": "editor"})

        assert response.json()["added"] == ["bob"]
        assert repo.calls[-1] == ("share", 1, "alice", ["bob"], "editor")

    async def test_other_users_cannot_share_the_skill(self, make_client, repo):
        repo.add(_skill(1, "alice", is_public=True))

        response = await _as(make_client, BOB).post(BASE + "/1/share", json={"usernames": ["carol"]})

        assert response.status_code == 403 and not [c for c in repo.calls if c[0] == "share"]

    async def test_non_owner_platform_role_cannot_share_foreign_skills(self, make_client, repo, with_mapping):
        repo.add(_skill(1, "alice", is_public=True))

        response = await _as(make_client, _in_group("Editors", BOB)).post(BASE + "/1/share", json={"usernames": ["carol"]})

        assert response.status_code == 403

    async def test_nothing_added_is_a_403(self, make_client, repo):
        repo.add(_skill(1, "alice"))
        repo.share_result = ([], ["nobody"])

        assert (await _as(make_client).post(BASE + "/1/share", json={"usernames": ["nobody"]})).status_code == 403

    async def test_requires_recipients(self, make_client, repo):
        repo.add(_skill(1, "alice"))

        assert (await _as(make_client).post(BASE + "/1/share", json={"usernames": []})).status_code == 422

    async def test_unknown_skill(self, make_client, repo):
        assert (await _as(make_client).post(BASE + "/9/share", json={"usernames": ["bob"]})).status_code == 404


class TestHelpers:
    def test_author_check_is_case_insensitive_and_admin_always_passes(self):
        skill = _skill(1, "Alice")

        assert module._is_skill_author(skill, {"user_id": "alice"}) is True
        assert module._is_skill_author(skill, {"user_id": "bob"}) is False
        assert module._is_skill_author(skill, {"user_id": "bob", "is_admin": True}) is True
        assert module._is_skill_author(None, {"user_id": "alice"}) is False
        assert module._is_skill_author(skill, None) is False

    def test_write_access_flag_follows_permission(self):
        for perm, expected in [("owner", True), ("editor", True), ("viewer", False), (None, False)]:
            item = _skill(1, my_permission=perm)
            module._sync_skill_write_access(item)
            assert item.write_access is expected

    def test_platform_role_caps_permissions_of_foreign_skills_only(self, with_mapping):
        mine, theirs = _skill(1, "alice", my_permission="owner"), _skill(2, "carol", my_permission="owner")

        module._apply_platform_role_to_skills([mine, theirs], _in_group("Viewers"))

        assert mine.my_permission == "owner" and mine.write_access is True
        assert theirs.my_permission == "viewer" and theirs.write_access is False

    def test_unmatched_users_lose_all_permissions_on_foreign_skills(self, with_mapping):
        item = _skill(1, "carol", my_permission="editor")

        module._apply_platform_role_to_skills([item], _in_group("Basic"))

        assert item.my_permission is None and item.write_access is False

    def test_admin_keeps_everything(self, with_mapping):
        item = _skill(1, "carol", my_permission="owner")

        module._apply_platform_role_to_skills([item], {**ADMIN, "groups": ["Basic"]})

        assert item.my_permission == "owner" and item.write_access is True
