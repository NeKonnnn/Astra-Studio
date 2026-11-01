"""api_agents: маршруты, не покрытые test_agents_api — списки, закладки, оценки, дублирование, шаринг, ошибки."""

import pytest

from backend import api_agents as module
from backend.tests.api.conftest import ADMIN, ALICE

from ._agents_fake import FullAgentRepo, make_agent, share_row, stats_for
from ._fakes import in_group, role_caches, with_mapping  # noqa: F401

pytestmark = pytest.mark.unit

BASE = "/api/agents"
BOB = {**ALICE, "username": "bob", "user_id": "bob"}
NEW_AGENT = {"name": "Кредитный консультант", "system_prompt": "Ты полезный ассистент банка.", "is_public": False}


@pytest.fixture
def repo(monkeypatch, cef_events):
    fake = FullAgentRepo()
    monkeypatch.setattr(module, "get_agent_repository", lambda: fake)

    async def no_enrich(agents):
        return None

    monkeypatch.setattr(module, "_enrich_agents_for_response", no_enrich)
    fake.events = cef_events(module)
    return fake


def _as(make_client, user=ALICE):
    return make_client(module.router, user=user)


def _gets(repo):
    """Вызовы get_agents с аргументами (без служебных записей журнала)."""
    return [c for c in repo.calls if c[0] == "get_agents" and len(c) > 1]


def _codes(repo):
    return [code for code, _ in repo.events]


# ------------------------------------------------------------ справочники и создание


async def test_chain_config_requires_login_and_reports_limits(make_client):
    # BUG-BE-03 исправлен: ручка больше не публичная (без входа 401).
    assert (await make_client(module.router, user=None).get(BASE + "/chain-config")).status_code == 401
    response = await _as(make_client).get(BASE + "/chain-config")

    body = response.json()
    assert response.status_code == 200
    assert body["max_agents"] <= body["max_agents_cap"]
    assert body["max_subagents"] <= body["max_subagents_cap"]
    assert body["default_recursion_limit"] >= 1 and body["max_recursion_limit"] >= body["default_recursion_limit"]


async def test_tags_are_listed_or_empty_without_repository(make_client, repo, monkeypatch):
    assert (await _as(make_client).get(BASE + "/tags")).json() == {"tags": [{"id": 1, "name": "bank"}]}

    monkeypatch.setattr(module, "get_agent_repository", lambda: None)
    assert (await _as(make_client).get(BASE + "/tags")).json() == {"tags": []}


async def test_create_reports_500_when_repository_returns_no_id(make_client, repo):
    repo.next_ids = [None]

    response = await _as(make_client).post(BASE, json=NEW_AGENT)

    assert response.status_code == 500
    assert response.json()["detail"] == "Ошибка при создании агента"
    assert _codes(repo) == []


# ------------------------------------------------------------------- просмотр и чтение


async def test_view_counts_and_unknown_agent(make_client, repo):
    repo.add(make_agent(1, "alice", is_public=True))

    ok = await _as(make_client).post(BASE + "/1/view")
    missing = await _as(make_client).post(BASE + "/9/view")

    assert ok.json() == {"success": True, "message": "Просмотр учтён"}
    assert missing.status_code == 404


async def test_view_error_and_feature_gate(make_client, repo, with_mapping):
    repo.fail_on = {"increment_views"}
    assert (await _as(make_client).post(BASE + "/1/view")).status_code == 403  # alice без группы при включённом маппинге

    response = await _as(make_client, in_group("Viewers", ALICE)).post(BASE + "/1/view")
    assert response.status_code == 500 and response.json()["detail"] == "Внутренняя ошибка сервера"
    assert (await make_client(module.router, user=None).post(BASE + "/1/view")).status_code in (401, 403)


async def test_get_agent_without_access_is_403(make_client, repo):
    repo.add(make_agent(1, "alice", is_public=True))
    repo.deny_access = True

    response = await _as(make_client, BOB).get(BASE + "/1")

    assert response.status_code == 403 and "Нет доступа" in response.json()["detail"]
    assert ("increment_views",) not in repo.calls


async def test_get_agent_repository_error(make_client, repo):
    repo.fail_on = {"get_agent"}

    assert (await _as(make_client).get(BASE + "/1")).status_code == 500


async def test_get_agent_for_admin_is_not_capped(make_client, repo, with_mapping):
    repo.add(make_agent(1, "alice", is_public=True, my_permission="owner"))

    body = (await _as(make_client, ADMIN).get(BASE + "/1")).json()

    assert body["my_permission"] == "owner"


# ------------------------------------------------------------------ список агентов


async def test_listing_forwards_filters_and_pages(make_client, repo):
    repo.list_result = ([make_agent(1, "alice", is_public=True)], 45)

    response = await _as(make_client).get(
        BASE,
        params={"search": "кредит", "tags": "1, 2,,3", "author_id": "bob", "min_rating": 3.5,
                "sort_by": "views", "sort_order": "asc", "page": 2, "limit": 20},
    )

    body = response.json()
    assert (body["total"], body["page"], body["pages"]) == (45, 2, 3)
    _, filters, user_id, _ = _gets(repo)[0]
    assert user_id == "alice"
    assert (filters.search_query, filters.tag_ids, filters.author_id, filters.min_rating) == ("кредит", [1, 2, 3], "bob", 3.5)
    assert (filters.sort_by, filters.sort_order, filters.limit, filters.offset) == ("views", "asc", 20, 20)


async def test_listing_rejects_malformed_tags(make_client, repo):
    response = await _as(make_client).get(BASE, params={"tags": "1,abc"})

    assert response.status_code == 400 and response.json()["detail"] == "Неверный формат тегов"
    assert ("get_agents",) not in repo.calls


async def test_listing_is_open_for_anonymous_users(make_client, repo):
    repo.list_result = ([make_agent(1, "alice", is_public=True)], 1)

    response = await make_client(module.router, user=None).get(BASE + "/")

    assert response.status_code == 200 and response.json()["agents"][0]["id"] == 1
    assert _gets(repo)[0][2] is None


async def test_listing_is_empty_for_base_role(make_client, repo, with_mapping):
    repo.list_result = ([make_agent(1, "alice", is_public=True)], 1)

    body = (await _as(make_client, in_group("Basic", ALICE)).get(BASE)).json()

    assert body == {"agents": [], "total": 0, "page": 1, "pages": 0}
    assert ("get_agents",) not in repo.calls


async def test_listing_caps_permissions_of_foreign_agents(make_client, repo, with_mapping):
    repo.list_result = (
        [make_agent(1, "alice", is_public=True), make_agent(2, "bob", is_public=True)],
        2,
    )

    body = (await _as(make_client, in_group("Viewers", ALICE)).get(BASE)).json()

    perms = {a["id"]: a["my_permission"] for a in body["agents"]}
    assert perms == {1: "owner", 2: "viewer"}


async def test_listing_repository_error(make_client, repo):
    repo.fail_on = {"get_agents"}

    response = await _as(make_client).get(BASE)

    assert response.status_code == 500 and response.json()["detail"] == "Внутренняя ошибка сервера"


# ------------------------------------------------------------------ обновление (ошибка)


async def test_update_repository_error_is_500(make_client, repo):
    repo.add(make_agent(1, "alice"))
    repo.fail_on = {"update_agent"}

    response = await _as(make_client).put(BASE + "/1", json={"name": "Новое имя"})

    assert response.status_code == 500 and _codes(repo) == []


# -------------------------------------------------------------- удаление и каскад


class FakeRag:
    def __init__(self):
        self.deleted = []
        self.fail_for = set()
        self.result = {"minio_object": "obj", "minio_bucket": "bkt"}

    async def kb_delete_document(self, doc_id):
        if doc_id in self.fail_for:
            raise RuntimeError("svc-rag down")
        self.deleted.append(doc_id)
        return self.result


@pytest.fixture
def rag(monkeypatch):
    import backend.routes.rag as rag_routes
    import backend.settings.rag_client as rag_client_module

    fake = FakeRag()
    fake.source_files = []
    monkeypatch.setattr(rag_client_module, "get_rag_client", lambda: fake)
    monkeypatch.setattr(rag_routes, "_delete_rag_source_file", lambda obj, bucket: fake.source_files.append((obj, bucket)))
    monkeypatch.setattr(rag_routes, "_require_storage_for_delete", lambda: None)
    return fake


async def test_delete_removes_orphan_kb_documents_and_keeps_shared_ones(make_client, repo, rag):
    repo.add(make_agent(1, "alice", config={"kb_document_ids": [10, "11", "x"]}))
    repo.orphans = [10]

    response = await _as(make_client).delete(BASE + "/1")

    assert response.json()["deleted_linked"] == 0 and response.status_code == 200
    assert rag.deleted == [10]
    assert rag.source_files == [("obj", "bkt")]
    assert _codes(repo) == ["AGT005"]


async def test_delete_is_blocked_when_storage_is_unavailable(make_client, repo, rag, monkeypatch):
    import backend.routes.rag as rag_routes
    from fastapi import HTTPException

    def unavailable():
        raise HTTPException(status_code=503, detail="PVC недоступен")

    monkeypatch.setattr(rag_routes, "_require_storage_for_delete", unavailable)
    repo.add(make_agent(1, "alice", config={"kb_document_ids": [10]}))

    response = await _as(make_client).delete(BASE + "/1")

    assert response.status_code == 503
    assert 1 in repo.agents and _codes(repo) == []


async def test_cascade_delete_removes_only_own_linked_agents(make_client, repo, rag):
    repo.add(make_agent(1, "alice", config={"agent_ids": [2, 3, 4], "subagents": {"agent_ids": [5]}}))
    repo.add(make_agent(2, "alice", config={"kb_document_ids": [20]}))
    repo.add(make_agent(3, "bob", is_public=True))  # чужой — не трогаем
    repo.add(make_agent(5, "ALICE", config={"agent_ids": [2, 1]}))  # регистр id не важен, ссылки-циклы
    repo.orphans = [20]

    response = await _as(make_client).delete(BASE + "/1", params={"cascade_linked": "true"})

    body = response.json()
    assert body["deleted_linked"] == 2 and "2 связанными" in body["message"]
    assert set(repo.agents) == {3}
    assert rag.deleted == [20]
    code, extra = repo.events[0]
    assert code == "AGT005" and extra["extra"]["cs3"] == "cascade_linked=2"


async def test_cascade_delete_survives_failure_of_a_linked_agent(make_client, repo, rag):
    repo.add(make_agent(1, "alice", config={"agent_ids": [2]}))
    repo.add(make_agent(2, "alice"))
    repo.delete_results = {2: False}

    response = await _as(make_client).delete(BASE + "/1", params={"cascade_linked": "true"})

    assert response.json()["deleted_linked"] == 0
    assert 1 not in repo.agents and 2 in repo.agents


async def test_delete_refused_by_repository_is_403(make_client, repo):
    repo.add(make_agent(1, "alice"))
    repo.delete_results = {1: False}

    assert (await _as(make_client).delete(BASE + "/1")).status_code == 403


async def test_delete_repository_error_is_500(make_client, repo):
    repo.add(make_agent(1, "alice"))
    repo.fail_on = {"delete_agent"}

    assert (await _as(make_client).delete(BASE + "/1")).status_code == 500


# ----------------------------------------------------------------- дублирование


@pytest.fixture
def entity_settings(monkeypatch):
    import backend.database.init_db as init_db

    class Store:
        def __init__(self):
            self.data = {("agent", "1"): {"top_k": 7}}
            self.upserts = []

        async def get(self, kind, key):
            return self.data.get((kind, key))

        async def upsert(self, kind, key, payload, updated_by):
            self.upserts.append((kind, key, payload, updated_by))

    store = Store()
    monkeypatch.setattr(init_db, "get_entity_settings_repository", lambda: store)
    return store


def _graph(repo):
    repo.add(make_agent(1, "alice", name="Корень", is_public=True, config={"agent_ids": [2], "kb_document_ids": []}, tools=["t"]))
    repo.add(
        make_agent(
            2, "bob", name="Субагент", is_public=False,
            config={"subagents": {"agent_ids": [1, 7], "agent_names": {"1": "Корень", "7": "?", "x": "bad"}}},
        )
    )


async def test_duplicate_clones_the_whole_graph_and_remaps_links(make_client, repo, entity_settings):
    _graph(repo)

    response = await _as(make_client).post(BASE + "/1/duplicate")

    body = response.json()
    assert response.status_code == 200
    assert body["agent_id"] == 3 and body["cloned_agents"] == 2 and body["cloned_agent_ids"] == [3, 4]
    root_clone, sub_clone = repo.agents[3], repo.agents[4]
    assert root_clone.author_id == "alice" and root_clone.is_public is False
    assert root_clone.name == "Корень (копия)" and root_clone.tools == ["t"]
    assert root_clone.config["agent_ids"] == [4] and root_clone.config["cloned_from"] == 1
    assert sub_clone.config["subagents"]["agent_ids"] == [3]  # 7 без клона отброшен
    assert sub_clone.config["subagents"]["agent_names"] == {"3": "Корень"}
    assert sub_clone.config["cloned_from"] == 2
    assert repo.agents[1].config["agent_ids"] == [2]  # оригиналы не тронуты
    assert entity_settings.upserts == [("agent", "3", {"top_k": 7}, "alice")]
    assert _codes(repo) == ["AGT003"]


async def test_duplicate_of_unknown_or_forbidden_agent(make_client, repo, entity_settings):
    assert (await _as(make_client).post(BASE + "/9/duplicate")).status_code == 404

    repo.add(make_agent(1, "bob", is_public=True))
    repo.deny_access = True
    assert (await _as(make_client).post(BASE + "/1/duplicate")).status_code == 403


async def test_duplicate_skips_nodes_it_cannot_copy(make_client, repo, entity_settings):
    _graph(repo)
    repo.next_ids = [3, None]  # корень склонирован, субагенту id не выдан

    body = (await _as(make_client).post(BASE + "/1/duplicate")).json()

    assert body["cloned_agent_ids"] == [3]
    assert repo.agents[3].config["agent_ids"] == []  # ссылка на несклонированного отброшена


async def test_duplicate_fails_when_root_cannot_be_copied(make_client, repo, entity_settings):
    _graph(repo)
    repo.create_raises_for = {3}
    repo.next_ids = [3]

    response = await _as(make_client).post(BASE + "/1/duplicate")

    assert response.status_code == 500 and response.json()["detail"] == "Ошибка при дублировании агента"
    assert _codes(repo) == []


async def test_duplicate_fails_when_root_is_unreadable_without_acl(make_client, repo, entity_settings):
    _graph(repo)
    repo.hidden_for_none = {1}

    response = await _as(make_client).post(BASE + "/1/duplicate")

    assert response.status_code == 500


async def test_duplicate_does_not_update_when_clone_reuses_a_source_id(make_client, repo, entity_settings):
    """Клон корня получил id чужого узла графа: второй проход не должен его переписывать."""
    _graph(repo)
    repo.next_ids = [2, 3]  # клон агента 1 -> id 2 (занят оригиналом), клон агента 2 -> id 3

    body = (await _as(make_client).post(BASE + "/1/duplicate")).json()

    assert body["cloned_agent_ids"] == [2, 3]
    updates = [c[1] for c in repo.calls if c[0] == "update"]
    # id 2: только запись при создании клона; второй проход (remap) для него пропущен
    assert updates.count(2) == 1
    assert updates.count(3) == 2


async def test_duplicate_ignores_clone_that_disappeared(make_client, repo, entity_settings):
    _graph(repo)
    repo.hidden_ids = {3}  # get_agent(new_id, owner) клона 3 ничего не находит

    response = await _as(make_client).post(BASE + "/1/duplicate")

    assert response.status_code == 200
    assert repo.agents[3].config["agent_ids"] == [2]  # remap пропущен, остались старые ссылки


async def test_duplicate_repository_error_is_500(make_client, repo):
    repo.fail_on = {"get_agent"}

    assert (await _as(make_client).post(BASE + "/1/duplicate")).status_code == 500


# ----------------------------------------------------- оценки, использование, статистика


async def test_rating_range_and_outcomes(make_client, repo):
    assert (await _as(make_client).post(BASE + "/1/rate", json={"rating": 0})).status_code == 400
    assert (await _as(make_client).post(BASE + "/1/rate", json={"rating": 6})).status_code == 400
    assert not [c for c in repo.calls if c[0] == "rate"]

    ok = await _as(make_client).post(BASE + "/1/rate", json={"rating": 5})
    assert ok.json() == {"success": True, "message": "Оценка сохранена"}
    assert ("rate", 1, "alice", 5) in repo.calls

    repo.rate_ok = False
    assert (await _as(make_client).post(BASE + "/1/rate", json={"rating": 5})).status_code == 404

    repo.fail_on = {"rate_agent"}
    assert (await _as(make_client).post(BASE + "/1/rate", json={"rating": 5})).status_code == 500


async def test_use_is_counted_and_audited(make_client, repo):
    repo.add(make_agent(1, "alice", name="Мой агент", is_public=True))

    response = await _as(make_client).post(BASE + "/1/use")

    assert response.json() == {"success": True, "message": "Использование учтено"}
    code, extra = repo.events[0]
    assert code == "INT004" and extra["extra"]["cs1"] == "Мой агент"


async def test_use_of_agent_that_vanished_is_audited_by_id(make_client, repo):
    repo.add(make_agent(1, "bob", is_public=False))  # alice его не видит -> get_agent вернёт None

    await _as(make_client).post(BASE + "/1/use")

    assert repo.events[0][1]["extra"]["cs1"] == "agent-1"


async def test_use_unknown_agent_and_errors(make_client, repo, with_mapping):
    assert (await _as(make_client, in_group("Viewers", ALICE)).post(BASE + "/9/use")).status_code == 404
    assert (await _as(make_client).post(BASE + "/1/use")).status_code == 403  # без роли при включённом маппинге

    repo.fail_on = {"increment_usage"}
    assert (await _as(make_client, in_group("Viewers", ALICE)).post(BASE + "/1/use")).status_code == 500


async def test_stats(make_client, repo):
    repo.stats = stats_for(1)
    ok = await _as(make_client).get(BASE + "/1/stats")
    assert ok.json()["average_rating"] == 4.5 and ok.json()["rating_distribution"] == {"5": 1, "4": 1}

    repo.stats = None
    assert (await _as(make_client).get(BASE + "/1/stats")).status_code == 404

    repo.fail_on = {"get_agent_stats"}
    assert (await _as(make_client).get(BASE + "/1/stats")).status_code == 500


# ----------------------------------------------------------------------- rollback


async def test_rollback_refused_by_repository_and_errors(make_client, repo):
    repo.add(make_agent(1, "alice"))
    repo.update_ok = False
    assert (await _as(make_client).post(BASE + "/1/rollback", json={"version": 2})).status_code == 403

    repo.update_ok = True
    repo.fail_on = {"update_agent"}
    assert (await _as(make_client).post(BASE + "/1/rollback", json={"version": 2})).status_code == 500


# ---------------------------------------------------------------------- закладки


async def test_bookmark_add_and_remove(make_client, repo):
    assert (await _as(make_client).post(BASE + "/1/bookmark")).json()["message"] == "Добавлено в закладки"
    assert (await _as(make_client).delete(BASE + "/1/bookmark")).json()["message"] == "Удалено из закладок"

    repo.bookmark_ok = False
    assert (await _as(make_client).post(BASE + "/1/bookmark")).status_code == 404
    assert (await _as(make_client).delete(BASE + "/1/bookmark")).status_code == 404

    repo.fail_on = {"add_bookmark", "remove_bookmark"}
    assert (await _as(make_client).post(BASE + "/1/bookmark")).status_code == 500
    assert (await _as(make_client).delete(BASE + "/1/bookmark")).status_code == 500


async def test_my_bookmarks(make_client, repo, with_mapping):
    empty = await _as(make_client, in_group("Viewers", ALICE)).get(BASE + "/my/bookmarks")
    assert empty.json() == {"agents": [], "total": 0, "page": 1, "pages": 0}

    repo.add(make_agent(1, "alice", is_public=True))
    repo.add(make_agent(2, "bob", is_public=True))
    repo.bookmarks = [1, 2, 3]  # 3 уже удалён
    body = (await _as(make_client, in_group("Viewers", ALICE)).get(BASE + "/my/bookmarks", params={"limit": 2, "page": 1})).json()

    assert [(a["id"], a["my_permission"]) for a in body["agents"]] == [(1, "owner"), (2, "viewer")]
    assert body["total"] == 3 and body["pages"] == 2
    assert ("bookmarks", "alice", 2, 0) in repo.calls


async def test_my_bookmarks_error(make_client, repo):
    repo.fail_on = {"get_user_bookmarks"}

    assert (await _as(make_client).get(BASE + "/my/bookmarks")).status_code == 500


# ------------------------------------------------------- admin/all, my/agents, my/shared


async def test_admin_listing_requires_admin_and_marks_owner(make_client, repo):
    assert (await _as(make_client).get(BASE + "/admin/all")).status_code == 403

    repo.list_result = ([make_agent(1, "bob", my_permission="viewer")], 0)
    body = (await _as(make_client, ADMIN).get(BASE + "/admin/all", params={"search": "x", "page": 2, "limit": 5})).json()

    assert body["agents"][0]["my_permission"] == "owner" and body["pages"] == 0
    _, filters, user_id, is_admin = _gets(repo)[0]
    assert (filters.search_query, filters.offset, filters.limit, is_admin, user_id) == ("x", 5, 5, True, "root")

    repo.list_result = ([], 11)
    assert (await _as(make_client, ADMIN).get(BASE + "/admin/all", params={"limit": 5})).json()["pages"] == 3

    repo.fail_on = {"get_agents"}
    assert (await _as(make_client, ADMIN).get(BASE + "/admin/all")).status_code == 500


async def test_my_agents(make_client, repo, with_mapping):
    repo.list_result = ([make_agent(1, "alice"), make_agent(2, "bob", is_public=True)], 21)

    body = (await _as(make_client, in_group("Viewers", {**ALICE, "user_id": " Alice "})).get(BASE + "/my/agents")).json()

    assert body["pages"] == 2 and body["total"] == 21
    _, filters, _, _ = _gets(repo)[0]
    assert filters.author_id == "alice" and filters.author_only is True and filters.sort_by == "date"

    repo.fail_on = {"get_agents"}
    assert (await _as(make_client, in_group("Viewers", ALICE)).get(BASE + "/my/agents")).status_code == 500


async def test_my_agents_keep_author_permission_under_platform_roles(make_client, repo, with_mapping):
    repo.list_result = ([make_agent(1, "alice"), make_agent(2, "bob", is_public=True)], 2)

    body = (await _as(make_client, in_group("Viewers", ALICE)).get(BASE + "/my/agents")).json()

    assert {a["id"]: a["my_permission"] for a in body["agents"]} == {1: "owner", 2: "viewer"}


async def test_shared_with_me(make_client, repo, with_mapping):
    repo.shared_result = ([make_agent(2, "bob", is_public=True, my_permission="editor")], 0)

    body = (await _as(make_client, in_group("Viewers", ALICE)).get(BASE + "/my/shared", params={"page": 3, "limit": 5})).json()

    assert body["agents"][0]["my_permission"] == "viewer" and body["pages"] == 0
    assert ("shared", "alice", 5, 10) in repo.calls

    repo.shared_result = ([], 12)
    assert (await _as(make_client, in_group("Viewers", ALICE)).get(BASE + "/my/shared", params={"limit": 5})).json()["pages"] == 3

    repo.fail_on = {"get_shared_with_me"}
    assert (await _as(make_client, in_group("Viewers", ALICE)).get(BASE + "/my/shared")).status_code == 500


# ----------------------------------------------------------------------- шаринг


@pytest.fixture
def recipients(monkeypatch):
    from types import SimpleNamespace

    async def prepare(usernames, permission):
        return SimpleNamespace(to_share=[(u.lower(), permission) for u in usernames], skipped=[])

    def respond(prepared, added, skipped, resource_label=""):
        return {"success": True, "added": list(added), "skipped": list(skipped), "message": "msg"}

    monkeypatch.setattr(module, "prepare_share_recipients", prepare)
    monkeypatch.setattr(module, "build_share_api_response", respond)


async def test_share_nothing_added_is_403_and_errors_are_500(make_client, repo, recipients):
    repo.add(make_agent(1, "alice"))
    repo.share_result = ([], ["nobody"])

    response = await _as(make_client).post(BASE + "/1/share", json={"usernames": ["nobody"]})
    assert response.status_code == 403 and response.json()["detail"] == "msg"
    assert _codes(repo) == []

    repo.fail_on = {"share_agent"}
    assert (await _as(make_client).post(BASE + "/1/share", json={"usernames": ["bob"]})).status_code == 500


async def test_shares_listing_resolves_names(make_client, repo, monkeypatch):
    repo.add(make_agent(1, "alice"))
    repo.shares = [share_row("bob", "EDITOR "), share_row("carol", "x")]

    async def names(ids):
        assert ids == ["alice", "bob", "carol"]
        return {"bob": "Боб"}

    monkeypatch.setattr(module, "resolve_full_names", names)

    body = (await _as(make_client, {**ALICE, "name": "Алиса"}).get(BASE + "/1/shares")).json()

    assert body["owner"] == {"user_id": "alice", "full_name": "Алиса", "permission": "owner"}
    assert body["shares"] == [
        {"user_id": "bob", "full_name": "Боб", "permission": "editor"},
        {"user_id": "carol", "full_name": None, "permission": "viewer"},
    ]


async def test_shares_listing_permissions_and_errors(make_client, repo, monkeypatch):
    repo.add(make_agent(1, "alice", is_public=True))

    async def names(ids):
        return {}

    monkeypatch.setattr(module, "resolve_full_names", names)

    assert (await _as(make_client).get(BASE + "/9/shares")).status_code == 404
    forbidden = await _as(make_client, BOB).get(BASE + "/1/shares")
    assert forbidden.status_code == 403 and "Только автор" in forbidden.json()["detail"]

    admin_body = (await _as(make_client, {**ADMIN, "full_name": "Админ"}).get(BASE + "/1/shares")).json()
    assert admin_body["owner"]["full_name"] is None  # ФИО админа не подставляется в чужую карточку

    repo.fail_on = {"list_agent_shares"}
    assert (await _as(make_client).get(BASE + "/1/shares")).status_code == 500


async def test_unshare(make_client, repo):
    repo.add(make_agent(1, "alice", name="Мой агент"))

    response = await _as(make_client).delete(BASE + "/1/share/bob")

    assert response.json() == {"success": True, "message": "Доступ для bob снят"}
    code, extra = repo.events[0]
    assert code == "SEC004" and extra["extra"]["cs3"] == "revoked" and extra["extra"]["duser"] == "bob"
    assert ("unshare", 1, "alice", "bob") in repo.calls


async def test_unshare_of_unknown_agent_is_404_and_not_audited(make_client, repo):
    # Изменено поведение: сначала проверяется существование/доступ к карточке, аудит не пишется.
    response = await _as(make_client).delete(BASE + "/9/share/bob")

    assert response.status_code == 404
    assert repo.events == [] and ("unshare", 9, "alice", "bob") not in repo.calls


async def test_unshare_failures(make_client, repo, with_mapping):
    repo.add(make_agent(1, "alice"))  # свой агент: автору platform-роль не нужна
    repo.add(make_agent(2, "carol", is_public=True, my_permission="viewer"))  # чужой, виден alice
    # Не автор и без platform-owner → 403 (BUG-BE-56: проверка прав идёт через _require_platform_roles).
    assert (await _as(make_client, in_group("Nobody", ALICE)).delete(BASE + "/2/share/bob")).status_code == 403
    assert (await _as(make_client, in_group("Viewers", ALICE)).delete(BASE + "/2/share/bob")).status_code == 403

    viewer = _as(make_client, in_group("Viewers", ALICE))
    repo.unshare_ok = False
    assert (await viewer.delete(BASE + "/1/share/bob")).status_code == 403
    assert _codes(repo) == []

    repo.fail_on = {"unshare_agent"}
    assert (await viewer.delete(BASE + "/1/share/bob")).status_code == 500


# ------------------------------------------- хвосты: ветки без маппинга и пробрасывание HTTPException


async def test_cascade_delete_tolerates_linked_agent_that_vanished(make_client, repo, rag):
    repo.add(make_agent(1, "alice", config={"agent_ids": [2]}))
    repo.add(make_agent(2, "alice"))
    repo.vanish_after = {2: 1}  # при сборе графа есть, при проверке KB уже нет

    response = await _as(make_client).delete(BASE + "/1", params={"cascade_linked": "true"})

    assert response.status_code == 200 and 1 not in repo.agents


async def test_bookmarks_and_lists_work_without_platform_mapping(make_client, repo):
    repo.add(make_agent(1, "bob", is_public=True, my_permission="editor"))
    repo.bookmarks = [1]
    repo.list_result = ([make_agent(1, "bob", is_public=True, my_permission="editor")], 1)
    repo.shared_result = ([make_agent(1, "bob", is_public=True, my_permission="editor")], 1)

    for path in ("/my/bookmarks", "/my/agents", "/my/shared"):
        body = (await _as(make_client).get(BASE + path)).json()
        assert [a["my_permission"] for a in body["agents"]] == ["editor"], path  # без маппинга права не сжимаются


@pytest.mark.parametrize(
    ("path", "method_name", "user"),
    [
        ("/my/bookmarks", "get_user_bookmarks", ALICE),
        ("/my/agents", "get_agents", ALICE),
        ("/my/shared", "get_shared_with_me", ALICE),
        ("/admin/all", "get_agents", ADMIN),
    ],
)
async def test_http_errors_of_repository_are_passed_through(make_client, repo, monkeypatch, path, method_name, user):
    from fastapi import HTTPException

    async def teapot(*a, **k):
        raise HTTPException(status_code=418, detail="я чайник")

    monkeypatch.setattr(repo, method_name, teapot)

    response = await _as(make_client, user).get(BASE + path)

    assert response.status_code == 418 and response.json()["detail"] == "я чайник"


async def test_admin_without_platform_group_may_unshare_without_mapping(make_client, repo):
    """BUG-BE-56 исправлен: администратор снимает доступ (раньше получал 403)."""
    repo.add(make_agent(1, "alice"))

    response = await _as(make_client, ADMIN).delete(BASE + "/1/share/bob")

    assert response.status_code == 200


async def test_admin_may_unshare_agents(make_client, repo, with_mapping):
    repo.add(make_agent(1, "alice"))

    response = await _as(make_client, ADMIN).delete(BASE + "/1/share/bob")

    assert response.status_code == 200
