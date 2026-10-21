"""api_agents: права на агентов, создание/правка/удаление/шаринг, хелперы конфигурации и клонирования."""

from types import SimpleNamespace

import pytest

from backend import api_agents as module
from backend.auth import agent_platform_roles as roles
from backend.database.postgresql.agent_models import AgentWithTags
from backend.tests.api.conftest import ALICE

pytestmark = pytest.mark.unit

BASE = "/api/agents"
BOB = {**ALICE, "username": "bob", "user_id": "bob"}
ADMIN = {**ALICE, "username": "root", "user_id": "root", "is_admin": True}
LONG_PROMPT = "Ты полезный ассистент банка."


def _agent(aid, author="alice", **kw):
    return AgentWithTags(
        id=aid,
        name=kw.pop("name", f"Agent {aid}"),
        system_prompt=kw.pop("system_prompt", LONG_PROMPT),
        author_id=author,
        author_name=author,
        my_permission=kw.pop("my_permission", "owner"),
        **kw,
    )


class FakeAgentRepo:
    def __init__(self):
        self.agents = {}
        self.calls = []
        self.foreign_docs = []
        self.share_result = (["bob"], [])
        self.update_ok = True
        self.delete_ok = True

    def add(self, agent):
        self.agents[agent.id] = agent
        return agent

    def _can_see(self, agent, uid, is_admin):
        return is_admin or agent.is_public or agent.author_id == uid

    async def get_agent(self, aid, user_id=None, is_admin=False):
        agent = self.agents.get(aid)
        if not agent:
            return None
        if user_id is None and not agent.is_public and not is_admin:
            return agent if False else None
        return agent

    async def user_can_access_agent(self, aid, user_id, is_admin=False):
        agent = self.agents.get(aid)
        return bool(agent and self._can_see(agent, user_id, is_admin))

    async def increment_views(self, aid):
        self.calls.append(("views", aid))
        return aid in self.agents

    async def increment_usage(self, aid):
        self.calls.append(("usage", aid))
        return aid in self.agents

    async def create_agent(self, agent_data, author_id, author_name):
        self.calls.append(("create", author_id, author_name, agent_data.is_public))
        aid = max(self.agents, default=0) + 1
        self.add(_agent(aid, author_id, name=agent_data.name, is_public=agent_data.is_public))
        return aid

    async def update_agent(self, agent_id, agent_data, author_id, is_admin=False):
        agent = self.agents.get(agent_id)
        if not self.update_ok or not agent or not (is_admin or agent.author_id == author_id):
            return False
        self.calls.append(("update", agent_id, author_id))
        for key, value in agent_data.model_dump(exclude_none=True).items():
            if hasattr(agent, key):
                setattr(agent, key, value)
        return True

    async def delete_agent(self, agent_id, author_id, is_admin=False):
        agent = self.agents.get(agent_id)
        if not self.delete_ok or not agent or not (is_admin or agent.author_id == author_id):
            return False
        del self.agents[agent_id]
        self.calls.append(("delete", agent_id, author_id))
        return True

    async def find_documents_claimed_by_other_agents(self, agent_id, ids):
        return [i for i in ids if i in self.foreign_docs]

    async def share_agent(self, agent_id, owner_id, usernames, permission, recipient_permissions):
        agent = self.agents.get(agent_id)
        if not agent or agent.author_id != owner_id:
            return [], usernames
        self.calls.append(("share", agent_id, owner_id, list(usernames), permission))
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
def repo(monkeypatch, cef_events):
    fake = FakeAgentRepo()
    monkeypatch.setattr(module, "get_agent_repository", lambda: fake)

    async def no_enrich(agents):
        return None

    monkeypatch.setattr(module, "_enrich_agents_for_response", no_enrich)
    fake.events = cef_events(module)
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


NEW_AGENT = {"name": "Кредитный консультант", "system_prompt": LONG_PROMPT, "is_public": False}


class TestAuthentication:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("GET", "/tags"), ("POST", ""), ("PUT", "/1"), ("DELETE", "/1"), ("POST", "/1/duplicate"), ("POST", "/1/rate"),
            ("POST", "/1/rollback"), ("POST", "/1/bookmark"), ("DELETE", "/1/bookmark"), ("GET", "/my/bookmarks"),
            ("GET", "/admin/all"), ("GET", "/my/agents"), ("GET", "/my/shared"), ("POST", "/1/share"), ("GET", "/1/shares"),
            ("DELETE", "/1/share/bob"),
        ],
    )
    async def test_protected_endpoints_require_login(self, make_client, repo, method, path):
        response = await make_client(module.router, user=None).request(method, BASE + path, json={} if method in ("POST", "PUT") else None)

        assert response.status_code in (401, 403)
        assert repo.calls == []


class TestFeatureGate:
    def test_feature_check_helper(self, with_mapping):
        module._require_agents_feature(_in_group("Viewers"))
        module._require_agents_feature(ADMIN)
        with pytest.raises(Exception) as exc:
            module._require_agents_feature(_in_group("Basic"))
        assert exc.value.status_code == 403

    def test_anonymous_is_allowed_only_without_role_mapping(self, with_mapping):
        with pytest.raises(Exception):
            module._require_agents_feature(None)

    def test_anonymous_is_allowed_when_mapping_is_off(self):
        module._require_agents_feature(None)

    async def test_unmatched_users_have_no_access_to_agent_endpoints(self, make_client, repo, with_mapping):
        client = _as(make_client, _in_group("Basic"))

        assert (await client.get(BASE + "/tags")).status_code == 403
        assert (await client.post(BASE, json=NEW_AGENT)).status_code == 403
        assert repo.calls == []


class TestCreate:
    async def test_author_comes_from_the_token(self, make_client, repo):
        response = await _as(make_client).post(BASE, json={**NEW_AGENT, "author_id": "bob"})

        assert response.status_code == 201
        assert response.json()["agent_id"] == 1
        assert repo.calls[-1][:3] == ("create", "alice", "alice")
        assert repo.agents[1].author_id == "alice"
        assert [c for c, _ in repo.events] == ["AGT001"]

    @pytest.mark.parametrize("body", [{}, {"name": "ab", "system_prompt": LONG_PROMPT}, {"name": "Нормальное", "system_prompt": "коротко"}])
    async def test_validates_payload(self, make_client, repo, body):
        assert (await _as(make_client).post(BASE, json=body)).status_code == 422

    async def test_viewers_cannot_create_and_it_is_audited(self, make_client, repo, with_mapping):
        response = await _as(make_client, _in_group("Viewers")).post(BASE, json=NEW_AGENT)

        assert response.status_code == 403
        assert repo.calls == [] and [c for c, _ in repo.events] == ["AGT006"]

    async def test_editors_can_only_create_private_agents(self, make_client, repo, with_mapping):
        await _as(make_client, _in_group("Editors")).post(BASE, json={**NEW_AGENT, "is_public": True})

        assert repo.calls[-1][3] is False

    async def test_owners_can_publish(self, make_client, repo, with_mapping):
        await _as(make_client, _in_group("Owners")).post(BASE, json={**NEW_AGENT, "is_public": True})

        assert repo.calls[-1][3] is True

    async def test_documents_of_other_agents_cannot_be_attached(self, make_client, repo):
        repo.foreign_docs = [5]

        response = await _as(make_client).post(BASE, json={**NEW_AGENT, "config": {"kb_document_ids": [5, "6"]}})

        assert response.status_code == 400 and "5" in response.json()["detail"]
        assert repo.calls == []

    async def test_repository_failure(self, make_client, repo, monkeypatch):
        async def fail(**kwargs):
            return None

        monkeypatch.setattr(repo, "create_agent", fail)

        assert (await _as(make_client).post(BASE, json=NEW_AGENT)).status_code == 500


class TestRead:
    async def test_anonymous_can_read_a_public_agent(self, make_client, repo):
        repo.add(_agent(1, "alice", is_public=True))

        response = await make_client(module.router, user=None).get(BASE + "/1")

        assert response.status_code == 200 and response.json()["name"] == "Agent 1"
        assert ("views", 1) in repo.calls

    async def test_private_agent_is_not_readable_by_others(self, make_client, repo):
        repo.add(_agent(1, "alice", is_public=False, system_prompt="Секретный промпт агента"))

        for user in (BOB, None):
            response = await make_client(module.router, user=user).get(BASE + "/1")
            assert response.status_code in (403, 404)
            assert "Секретный" not in response.text

    async def test_author_reads_the_private_agent(self, make_client, repo):
        repo.add(_agent(1, "alice", is_public=False))

        assert (await _as(make_client).get(BASE + "/1")).status_code == 200

    async def test_unknown_agent(self, make_client, repo):
        assert (await _as(make_client).get(BASE + "/99")).status_code == 404

    async def test_platform_role_caps_the_permission_on_foreign_agents(self, make_client, repo, with_mapping):
        repo.add(_agent(1, "carol", is_public=True, my_permission="owner"))

        body = (await _as(make_client, _in_group("Viewers")).get(BASE + "/1")).json()

        assert body["my_permission"] == "viewer"

    async def test_authors_keep_full_permission_on_their_own_cards(self, make_client, repo, with_mapping):
        repo.add(_agent(1, "alice", is_public=True, my_permission="owner"))

        assert (await _as(make_client, _in_group("Viewers")).get(BASE + "/1")).json()["my_permission"] == "owner"


class TestUpdate:
    async def test_author_updates_and_it_is_audited(self, make_client, repo):
        repo.add(_agent(1, "alice"))

        response = await _as(make_client).put(BASE + "/1", json={"name": "Переименованный"})

        assert response.json()["success"] is True and repo.agents[1].name == "Переименованный"
        assert [c for c, _ in repo.events] == ["AGT002"]

    async def test_non_author_is_refused_without_changes(self, make_client, repo):
        repo.add(_agent(1, "alice", is_public=True))

        response = await _as(make_client, BOB).put(BASE + "/1", json={"name": "Взломанный"})

        assert response.status_code == 403 and repo.agents[1].name == "Agent 1"
        assert repo.events == []

    async def test_admin_may_update_any_agent(self, make_client, repo):
        repo.add(_agent(1, "alice"))

        assert (await _as(make_client, ADMIN).put(BASE + "/1", json={"name": "Админская"})).status_code == 200

    async def test_unknown_agent_is_refused(self, make_client, repo):
        assert (await _as(make_client).put(BASE + "/9", json={"name": "Несуществующий"})).status_code == 403

    async def test_editors_cannot_publish(self, make_client, repo, with_mapping):
        repo.add(_agent(1, "editor", is_public=False))
        editor = _in_group("Editors", {**ALICE, "username": "editor", "user_id": "editor"})

        response = await _as(make_client, editor).put(BASE + "/1", json={"is_public": True})

        assert response.status_code == 403 and repo.agents[1].is_public is False

    async def test_owners_publish(self, make_client, repo, with_mapping):
        repo.add(_agent(1, "alice", is_public=False))

        assert (await _as(make_client, _in_group("Owners")).put(BASE + "/1", json={"is_public": True})).status_code == 200

    async def test_viewer_cannot_edit_foreign_agents(self, make_client, repo, with_mapping):
        repo.add(_agent(1, "carol", is_public=True))

        assert (await _as(make_client, _in_group("Viewers")).put(BASE + "/1", json={"name": "Чужой агент"})).status_code == 403

    async def test_documents_of_other_agents_cannot_be_attached(self, make_client, repo):
        repo.add(_agent(1, "alice"))
        repo.foreign_docs = [7]

        response = await _as(make_client).put(BASE + "/1", json={"config": {"kb_document_ids": [7]}})

        assert response.status_code == 400 and "update" not in [c[0] for c in repo.calls]


class TestDelete:
    async def test_author_deletes(self, make_client, repo):
        repo.add(_agent(1, "alice"))

        response = await _as(make_client).delete(BASE + "/1")

        assert response.status_code == 200 and 1 not in repo.agents
        assert [c for c, _ in repo.events] == ["AGT005"]

    async def test_non_author_cannot_delete(self, make_client, repo):
        repo.add(_agent(1, "alice", is_public=True))

        response = await _as(make_client, BOB).delete(BASE + "/1")

        assert response.status_code == 403 and 1 in repo.agents and repo.events == []

    async def test_admin_deletes_any_agent(self, make_client, repo):
        repo.add(_agent(1, "alice"))

        assert (await _as(make_client, ADMIN).delete(BASE + "/1")).status_code == 200

    async def test_editor_role_cannot_delete_foreign_agents(self, make_client, repo, with_mapping):
        repo.add(_agent(1, "carol", is_public=True))

        assert (await _as(make_client, _in_group("Editors")).delete(BASE + "/1")).status_code == 403

    async def test_missing_agent(self, make_client, repo):
        assert (await _as(make_client).delete(BASE + "/9")).status_code == 403


class TestSharing:
    @pytest.fixture(autouse=True)
    def _recipients(self, monkeypatch):
        async def prepare(usernames, permission):
            return SimpleNamespace(to_share=[(u.lower(), permission) for u in usernames], skipped=[])

        def respond(prepared, added, skipped, resource_label=""):
            return {"success": True, "added": list(added), "skipped": list(skipped)}

        monkeypatch.setattr(module, "prepare_share_recipients", prepare)
        monkeypatch.setattr(module, "build_share_api_response", respond)

    async def test_author_shares_and_each_recipient_is_audited(self, make_client, repo):
        repo.add(_agent(1, "alice"))
        repo.share_result = (["bob", "carol"], [])

        response = await _as(make_client).post(BASE + "/1/share", json={"usernames": ["Bob", "Carol"], "permission": "editor"})

        assert response.json()["added"] == ["bob", "carol"]
        assert [(c, kw["extra"]["duser"], kw["extra"]["cs3"]) for c, kw in repo.events] == [
            ("SEC004", "bob", "agent_editor"),
            ("SEC004", "carol", "agent_editor"),
        ]

    async def test_viewer_share_is_audited_as_viewer(self, make_client, repo):
        repo.add(_agent(1, "alice"))

        await _as(make_client).post(BASE + "/1/share", json={"usernames": ["bob"]})

        assert repo.events[0][1]["extra"]["cs3"] == "agent_viewer"

    async def test_other_users_cannot_share_the_agent(self, make_client, repo):
        repo.add(_agent(1, "alice", is_public=True))

        response = await _as(make_client, BOB).post(BASE + "/1/share", json={"usernames": ["carol"]})

        assert response.status_code == 403
        assert not [c for c in repo.calls if c[0] == "share"] and repo.events == []

    async def test_non_owner_platform_role_cannot_share_foreign_agents(self, make_client, repo, with_mapping):
        repo.add(_agent(1, "carol", is_public=True))

        assert (await _as(make_client, _in_group("Editors")).post(BASE + "/1/share", json={"usernames": ["bob"]})).status_code == 403

    async def test_unknown_agent_and_empty_recipients(self, make_client, repo):
        assert (await _as(make_client).post(BASE + "/9/share", json={"usernames": ["bob"]})).status_code == 404
        repo.add(_agent(1, "alice"))
        assert (await _as(make_client).post(BASE + "/1/share", json={"usernames": []})).status_code == 422


class TestRollback:
    async def test_rollback_only_touches_the_agent_and_reports_success_known_issue(self, make_client, repo):
        # См. TESTING_KNOWN_BUGS.md: ревизий нет, «rollback» вызывает update с пустым
        # AgentUpdate и отвечает «выполнен к версии N» — ничего не откатывается.
        repo.add(_agent(1, "alice", name="Текущее имя"))

        response = await _as(make_client).post(BASE + "/1/rollback", json={"version": 3})

        assert response.status_code == 200 and "версии 3" in response.json()["message"]
        assert repo.agents[1].name == "Текущее имя"
        assert [c for c, _ in repo.events] == ["AGT004"]

    async def test_foreign_agent(self, make_client, repo):
        repo.add(_agent(1, "alice", is_public=True))

        assert (await _as(make_client, BOB).post(BASE + "/1/rollback", json={"version": 1})).status_code == 403

    async def test_unknown_agent(self, make_client, repo):
        assert (await _as(make_client).post(BASE + "/9/rollback", json={"version": 1})).status_code == 404


class TestConfigHelpers:
    def test_kb_document_ids_from_a_dict_or_json_string(self):
        assert module._extract_kb_document_ids(SimpleNamespace(config={"kb_document_ids": [1, "2", "x", None]})) == [1, 2]
        assert module._extract_kb_document_ids(SimpleNamespace(config='{"kb_document_ids": [3]}')) == [3]
        assert module._extract_kb_document_ids(SimpleNamespace(config="not json")) == []
        assert module._extract_kb_document_ids(SimpleNamespace(config=None)) == []
        assert module._extract_kb_document_ids(SimpleNamespace(config=[1, 2])) == []
        assert module._extract_kb_document_ids(None) == []

    @pytest.mark.parametrize(("raw", "expected"), [([1, "2", "x", None, 3.0], [1, 2, 3]), (None, []), ("12", []), ([], [])])
    def test_int_id_parsing(self, raw, expected):
        assert module._parse_int_ids(raw) == expected

    def test_linked_agents_are_deduplicated_in_order(self):
        config = {"agent_ids": [3, "2", 3, 0, -1], "subagents": {"agent_ids": [2, 5]}}

        assert module._extract_linked_agent_ids(config) == [3, 2, 5]

    @pytest.mark.parametrize("config", [None, [], "x", {}, {"subagents": "no"}])
    def test_linked_agents_of_garbage_configs(self, config):
        assert module._extract_linked_agent_ids(config) == []

    def test_clone_config_is_a_deep_copy(self):
        source = {"a": {"b": [1, 2]}, "kb_document_ids": [1]}

        clone = module._clone_agent_config(source)
        clone["a"]["b"].append(3)
        clone["kb_document_ids"].clear()

        assert source == {"a": {"b": [1, 2]}, "kb_document_ids": [1]}

    def test_clone_config_from_json_and_garbage(self):
        assert module._clone_agent_config('{"x": 1}') == {"x": 1}
        assert module._clone_agent_config("{broken") == {}
        assert module._clone_agent_config(None) == {}
        assert module._clone_agent_config([1]) == {}

    def test_clone_config_stringifies_unserializable_values(self):
        from datetime import datetime

        assert isinstance(module._clone_agent_config({"when": datetime(2026, 1, 1)})["when"], str)

    def test_references_are_remapped_to_the_clones(self):
        config = {
            "agent_ids": [1, 2, 9],
            "subagents": {"agent_ids": [2, 3], "agent_names": {"2": "Два", "3": "Три", "x": "bad"}},
        }

        result = module._remap_config_agent_refs(config, {1: 11, 2: 12, 3: 13})

        assert result["agent_ids"] == [11, 12]  # 9 без клона — выкидываем
        assert result["subagents"]["agent_ids"] == [12, 13]
        assert result["subagents"]["agent_names"] == {"12": "Два", "13": "Три"}

    def test_remap_without_mapping_leaves_the_config(self):
        config = {"agent_ids": [1]}

        assert module._remap_config_agent_refs(config, {}) is config
        assert module._remap_config_agent_refs("x", {1: 2}) == "x"

    def test_remap_removes_duplicates_created_by_the_mapping(self):
        assert module._remap_config_agent_refs({"agent_ids": [1, 2]}, {1: 5, 2: 5})["agent_ids"] == [5]


class TestPermissionHelpers:
    def test_author_check(self):
        agent = _agent(1, "Alice")

        assert module._is_agent_author(agent, {"user_id": "alice"}) is True
        assert module._is_agent_author(agent, {"user_id": "bob"}) is False
        assert module._is_agent_author(agent, {"user_id": "bob", "is_admin": True}) is True
        assert module._is_agent_author(None, {"user_id": "alice"}) is False
        assert module._is_agent_author(agent, None) is False

    def test_cap_is_skipped_for_authors_admins_and_without_mapping(self, with_mapping):
        mine = _agent(1, "alice", my_permission="owner")
        module._apply_platform_permission_cap(mine, _in_group("Viewers"), "viewer")
        assert mine.my_permission == "owner"

        theirs = _agent(2, "carol", my_permission="owner")
        module._apply_platform_permission_cap(theirs, ADMIN, "viewer")
        assert theirs.my_permission == "owner"
        module._apply_platform_permission_cap(theirs, _in_group("Viewers"), "viewer")
        assert theirs.my_permission == "viewer"

    def test_cap_does_nothing_without_a_platform_role(self, with_mapping):
        agent = _agent(1, "carol", my_permission="owner")

        module._apply_platform_permission_cap(agent, _in_group("Viewers"), None)

        assert agent.my_permission == "owner"
