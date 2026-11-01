"""api_skills: то, что не покрыто test_skills_api — ошибки репозитория, файлы навыка, шаринг, закладки."""

from datetime import datetime
from types import SimpleNamespace

import pytest

from backend import api_skills as module
from backend.database.postgresql.skill_models import SkillFileOut, SkillListItem
from backend.tests.api.conftest import ADMIN, ALICE
from backend.tests.api.test_skills_api import FakeRepo, _skill

from ._fakes import ExplodingRepo, in_group, role_caches, with_mapping  # noqa: F401

pytestmark = pytest.mark.unit

BASE = "/api/skills"
BOB = {**ALICE, "username": "bob", "user_id": "bob"}
NOW = datetime(2026, 1, 1)


def _file(fid=1, skill_id=1, path="docs/a.md"):
    return SkillFileOut(id=fid, skill_id=skill_id, relative_path=path, created_at=NOW, updated_at=NOW)


class GapsRepo(FakeRepo):
    """FakeRepo + файлы навыка, список шарингов, снятие доступа."""

    def __init__(self):
        super().__init__()
        self.files = {}
        self.write_ok = True
        self.upsert_result = "row"
        self.unshare_ok = True
        self.shares = []

    async def can_write(self, sid, uid, is_admin=False):
        self.calls.append(("can_write", sid, uid))
        return self.write_ok

    async def list_skill_files(self, sid):
        return [_file(1, sid)]

    async def upsert_skill_file_text(self, sid, path, content, mime_type, is_executable):
        self.calls.append(("upsert", sid, path, content, mime_type, is_executable))
        return _file(5, sid, path) if self.upsert_result == "row" else None

    async def get_skill_file_content(self, sid, path):
        return self.files.get((sid, path))

    async def delete_skill_file(self, sid, fid):
        self.calls.append(("delete_file", sid, fid))
        return fid == 1

    async def list_skill_shares(self, sid, author_id):
        return self.shares

    async def unshare_skill(self, sid, uid, username):
        self.calls.append(("unshare", sid, uid, username))
        return self.unshare_ok


@pytest.fixture
def repo(monkeypatch):
    fake = GapsRepo()
    monkeypatch.setattr(module, "get_skill_repository", lambda: fake)

    async def no_enrich(items):
        return None

    monkeypatch.setattr(module, "enrich_items_author_full_names", no_enrich)
    return fake


@pytest.fixture
def broken(monkeypatch):
    monkeypatch.setattr(module, "get_skill_repository", lambda: ExplodingRepo())


def _as(make_client, user=ALICE):
    return make_client(module.router, user=user)


# ------------------------------------------------- ошибки хранилища → 500 с текстом


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/", None),
        ("GET", "/list", None),
        ("GET", "/export", None),
        ("POST", "/", {"name": "n", "content": "c"}),
        ("GET", "/my/bookmarks", None),
        ("GET", "/1", None),
        ("POST", "/1/rate", {"rating": 5}),
        ("POST", "/1/view", None),
        ("POST", "/1/use", None),
        ("POST", "/1/bookmark", None),
        ("DELETE", "/1/bookmark", None),
        ("PUT", "/1", {"name": "x"}),
        ("POST", "/1/toggle", None),
        ("DELETE", "/1", None),
        ("POST", "/1/share", {"usernames": ["bob"]}),
        ("GET", "/1/shares", None),
        ("DELETE", "/1/share/bob", None),
    ],
)
async def test_repository_failure_is_reported_as_500(make_client, broken, monkeypatch, method, path, body):
    async def prepare(usernames, permission):
        return SimpleNamespace(to_share=[], skipped=[])

    monkeypatch.setattr(module, "prepare_share_recipients", prepare)

    response = await _as(make_client).request(method, BASE + path, json=body)

    assert response.status_code == 500
    assert response.json()["detail"] == "Внутренняя ошибка сервера"  # BUG-BE-08: текст исключения скрыт


# --------------------------------------------------------------------- списки


async def test_admin_listing_walks_through_all_pages(make_client, repo):
    for i in range(1, 202):
        repo.add(_skill(i, "alice", slug=f"s{i}"))

    response = await _as(make_client, ADMIN).get(BASE + "/")

    assert response.status_code == 200 and len(response.json()) == 201
    admin_calls = [c for c in repo.calls if c[0] == "list"]
    assert [(c[3], c[2]) for c in admin_calls] == [(0, 200), (200, 200)]


async def test_user_listing_is_a_single_page(make_client, repo):
    repo.add(_skill(1, "alice"))

    response = await _as(make_client).get(BASE + "/")

    assert [s["id"] for s in response.json()] == [1]
    assert len([c for c in repo.calls if c[0] == "list"]) == 1


async def test_bookmarks_listing_skips_skills_that_disappeared(make_client, repo):
    repo.add(_skill(1, "alice", is_public=True))
    repo.bookmarks["alice"] = {1, 2}  # навык 2 удалён

    response = await _as(make_client).get(BASE + "/my/bookmarks")

    body = response.json()
    assert [s["id"] for s in body["items"]] == [1]
    assert body["total"] == 2 and "content" not in body["items"][0]


async def test_bookmarks_listing_empty(make_client, repo):
    body = (await _as(make_client).get(BASE + "/my/bookmarks")).json()

    assert body == {"items": [], "total": 0, "page": 1, "pages": 0}


async def test_get_skill_applies_platform_role_cap(make_client, repo, with_mapping):
    repo.add(_skill(1, "alice", is_public=True))

    mine = await _as(make_client, in_group("Viewers", ALICE)).get(BASE + "/1")
    foreign = await _as(make_client, in_group("Viewers", BOB)).get(BASE + "/1")

    assert mine.json()["my_permission"] == "owner"  # автор своей карточки
    assert foreign.json()["my_permission"] == "viewer"
    assert foreign.json()["write_access"] is False


# ------------------------------------------------------------- update/toggle/delete


async def test_update_value_error_is_400(make_client, repo):
    repo.add(_skill(1, "alice"))

    async def boom(*a, **k):
        raise ValueError("slug занят")

    repo.update_skill = boom

    response = await _as(make_client).put(BASE + "/1", json={"name": "x"})

    assert response.status_code == 400 and response.json()["detail"] == "slug занят"


async def test_update_refused_by_repository_is_403(make_client, repo):
    repo.add(_skill(1, "alice"))

    async def refuse(*a, **k):
        return None

    repo.update_skill = refuse

    response = await _as(make_client).put(BASE + "/1", json={"name": "x"})

    assert response.status_code == 403


async def test_toggle_unknown_skill_is_403(make_client, repo):
    assert (await _as(make_client).post(BASE + "/99/toggle")).status_code == 403


async def test_delete_unknown_skill_is_404_and_repository_refusal_is_403(make_client, repo):
    assert (await _as(make_client).delete(BASE + "/99")).status_code == 404

    repo.add(_skill(1, "alice"))

    async def refuse(*a, **k):
        return False

    repo.delete_skill = refuse
    assert (await _as(make_client).delete(BASE + "/1")).status_code == 403


async def test_rate_when_repository_refuses_is_404(make_client, repo):
    repo.add(_skill(1, "alice", is_public=True))

    async def refuse(**k):
        return False

    repo.rate_skill = refuse

    assert (await _as(make_client).post(BASE + "/1/rate", json={"rating": 3})).status_code == 404


@pytest.mark.parametrize("action", ["view", "use", "bookmark"])
async def test_counters_report_404_when_repository_returns_false(make_client, repo, action):
    repo.add(_skill(1, "alice", is_public=True))
    method = {"view": "increment_views", "use": "increment_usage", "bookmark": "add_bookmark"}[action]

    async def refuse(*a, **k):
        return False

    setattr(repo, method, refuse)

    assert (await _as(make_client).post(f"{BASE}/1/{action}")).status_code == 404


# ----------------------------------------------------------------------- шаринг


async def test_shares_listing_for_author(make_client, repo, monkeypatch):
    repo.add(_skill(1, "alice"))
    repo.shares = [
        SimpleNamespace(shared_with_user_id="bob", permission="EDITOR"),
        SimpleNamespace(shared_with_user_id="carol", permission="whatever"),
    ]

    async def names(ids):
        return {"bob": "Боб Б."}

    monkeypatch.setattr(module, "resolve_full_names", names)

    body = (await _as(make_client, {**ALICE, "full_name": "Алиса А."}).get(BASE + "/1/shares")).json()

    assert body["owner"] == {"user_id": "alice", "full_name": "Алиса А.", "permission": "owner"}
    assert body["shares"] == [
        {"user_id": "bob", "full_name": "Боб Б.", "permission": "editor"},
        {"user_id": "carol", "full_name": None, "permission": "viewer"},
    ]


async def test_shares_listing_for_admin_does_not_borrow_own_name(make_client, repo, monkeypatch):
    repo.add(_skill(1, "alice"))

    async def names(ids):
        return {}

    monkeypatch.setattr(module, "resolve_full_names", names)

    body = (await _as(make_client, {**ADMIN, "full_name": "Админ"}).get(BASE + "/1/shares")).json()

    assert body["owner"]["user_id"] == "alice" and body["owner"]["full_name"] is None


async def test_shares_listing_forbidden_for_non_author(make_client, repo):
    repo.add(_skill(1, "alice", is_public=True))

    assert (await _as(make_client, BOB).get(BASE + "/1/shares")).status_code == 403
    assert (await _as(make_client).get(BASE + "/9/shares")).status_code == 404


async def test_unshare(make_client, repo):
    repo.add(_skill(1, "alice"))
    response = await _as(make_client).delete(BASE + "/1/share/bob")

    assert response.json() == {"success": True, "message": "Доступ для bob снят"}
    assert ("unshare", 1, "alice", "bob") in repo.calls


async def test_unshare_refused_by_repository(make_client, repo):
    repo.add(_skill(1, "alice"))
    repo.unshare_ok = False

    assert (await _as(make_client).delete(BASE + "/1/share/bob")).status_code == 403


async def test_unshare_without_platform_role_is_forbidden(make_client, repo, with_mapping):
    repo.add(_skill(1, "carol", is_public=True))  # чужой навык: автору platform-роль не нужна
    response = await _as(make_client, in_group("Nobody", ALICE)).delete(BASE + "/1/share/bob")

    assert response.status_code == 403 and "unshare" not in [c[0] for c in repo.calls]


# ------------------------------------------------------------------- файлы навыка


async def test_list_files(make_client, repo):
    repo.add(_skill(1, "alice"))

    body = (await _as(make_client).get(BASE + "/1/files")).json()

    assert [f["relative_path"] for f in body] == ["docs/a.md"]
    assert (await _as(make_client).get(BASE + "/9/files")).status_code == 404


async def test_upsert_file(make_client, repo):
    repo.add(_skill(1, "alice"))

    response = await _as(make_client).post(
        BASE + "/1/files", json={"relative_path": "a/b.py", "content": "print(1)", "is_executable": True}
    )

    assert response.status_code == 201 and response.json()["relative_path"] == "a/b.py"
    assert repo.calls[-1] == ("upsert", 1, "a/b.py", "print(1)", "text/plain", True)


async def test_upsert_file_without_write_access(make_client, repo):
    repo.add(_skill(1, "alice"))
    repo.write_ok = False

    response = await _as(make_client).post(BASE + "/1/files", json={"relative_path": "a", "content": "x"})

    assert response.status_code == 403 and not [c for c in repo.calls if c[0] == "upsert"]


async def test_upsert_file_for_viewer_platform_role(make_client, repo, with_mapping):
    repo.add(_skill(1, "alice"))

    response = await _as(make_client, in_group("Viewers", ALICE)).post(
        BASE + "/1/files", json={"relative_path": "a", "content": "x"}
    )

    assert response.status_code == 403 and response.json()["detail"] == "Недостаточно прав"


async def test_upsert_file_by_editor_role_is_allowed(make_client, repo, with_mapping):
    repo.add(_skill(1, "alice"))

    response = await _as(make_client, in_group("Editors", ALICE)).post(
        BASE + "/1/files", json={"relative_path": "a", "content": "x", "mime_type": None}
    )

    assert response.status_code == 201
    assert repo.calls[-1][4] == "text/plain"  # пустой mime_type → text/plain


async def test_upsert_file_requires_content(make_client, repo):
    repo.add(_skill(1, "alice"))

    response = await _as(make_client).post(BASE + "/1/files", json={"relative_path": "a"})

    assert response.status_code == 400 and response.json()["detail"] == "Укажите content"


async def test_upsert_file_rejected_by_repository(make_client, repo):
    repo.add(_skill(1, "alice"))
    repo.upsert_result = None

    response = await _as(make_client).post(BASE + "/1/files", json={"relative_path": "SKILL.md", "content": "x"})

    assert response.status_code == 400 and "SKILL.md" in response.json()["detail"]


async def test_file_content(make_client, repo):
    repo.add(_skill(1, "alice"))
    repo.files[(1, "a.md")] = "текст"

    ok = await _as(make_client).get(BASE + "/1/files/content", params={"path": "a.md"})
    no_file = await _as(make_client).get(BASE + "/1/files/content", params={"path": "zzz"})
    no_skill = await _as(make_client).get(BASE + "/9/files/content", params={"path": "a.md"})

    assert ok.json() == {"relative_path": "a.md", "content": "текст"}
    assert (no_file.status_code, no_skill.status_code) == (404, 404)


async def test_delete_file(make_client, repo):
    repo.add(_skill(1, "alice"))

    ok = await _as(make_client).delete(BASE + "/1/files/1")
    missing = await _as(make_client).delete(BASE + "/1/files/2")

    assert ok.json() == {"success": True} and missing.status_code == 404


async def test_delete_file_permissions(make_client, repo, with_mapping):
    repo.add(_skill(1, "alice"))
    repo.write_ok = False
    assert (await _as(make_client, in_group("Owners", ALICE)).delete(BASE + "/1/files/1")).status_code == 403

    repo.write_ok = True
    viewer = await _as(make_client, in_group("Viewers", ALICE)).delete(BASE + "/1/files/1")
    assert viewer.status_code == 403
    assert not [c for c in repo.calls if c[0] == "delete_file"]


# --------------------------------------------------------------------- хелперы


def test_helpers_without_mapping_do_not_restrict(role_caches):
    item = SkillListItem(**_skill(1, "alice", my_permission="editor").model_dump(exclude={"content", "write_access"}))
    module._apply_platform_role_to_skills([item], {**ALICE})
    assert item.my_permission == "editor"
    module._require_platform_roles({**ALICE}, "owner", detail="нельзя")  # маппинг выключен → без ошибки
    assert module._platform_role({**ALICE}) is None


def test_admin_requires_no_platform_role(with_mapping):
    module._require_platform_roles(ADMIN, "owner", detail="нельзя")
    assert module._platform_role(in_group("Owners", ALICE)) == "owner"


def test_admin_bulk_helper_only_syncs_write_access(with_mapping):
    item = SkillListItem(**_skill(1, "bob", my_permission="editor").model_dump(exclude={"content", "write_access"}))
    module._apply_platform_role_to_skills([item], ADMIN)
    assert item.my_permission == "editor" and item.write_access is True


def test_apply_platform_role_for_author_keeps_permission(with_mapping):
    item = _skill(1, "alice", my_permission="owner")
    module._apply_platform_role_to_skill(item, "viewer", user=ALICE)
    assert item.my_permission == "owner" and item.write_access is True


# ------------------------------------------------ оставшиеся ветки (роли разрешают)


async def test_rate_unreadable_skill_is_hidden(make_client, repo):
    repo.add(_skill(1, "alice"))  # приватный навык другого пользователя

    response = await _as(make_client, BOB).post(BASE + "/1/rate", json={"rating": 4})

    assert response.status_code == 404
    assert not [c for c in repo.calls if c[0] == "rate"]


async def test_editor_role_may_toggle_unshare_and_delete_files(make_client, repo, with_mapping):
    repo.add(_skill(1, "alice"))
    editor = in_group("Editors", ALICE)

    toggled = await _as(make_client, editor).post(BASE + "/1/toggle")
    unshared = await _as(make_client, editor).delete(BASE + "/1/share/bob")
    file_deleted = await _as(make_client, editor).delete(BASE + "/1/files/1")

    assert toggled.status_code == 200 and toggled.json()["is_active"] is False
    assert unshared.status_code == 200
    assert file_deleted.json() == {"success": True}
