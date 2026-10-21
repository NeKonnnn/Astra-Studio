"""routes/projects: CRUD пользовательских проектов, изоляция по владельцу."""

import pytest

from backend.routes import projects as module
from backend.tests.api.conftest import ALICE

pytestmark = pytest.mark.unit

BOB = {**ALICE, "username": "bob", "user_id": "bob"}


class FakeProjectRepo:
    """In-memory репозиторий: проекты изолированы по (user_id, project_id), как в реальном."""

    def __init__(self):
        self.rows = {}
        self.next_id = 1
        self.fail_create = False
        self.fail_update = False

    async def list_by_user(self, user_id):
        return [p for (uid, _), p in self.rows.items() if uid == user_id]

    async def create(self, user_id, body):
        if self.fail_create:
            return None
        pid = body.id or f"p{self.next_id}"
        self.next_id += 1
        project = {"id": pid, "name": body.name, "instructions": body.instructions, "memory": body.memory, "icon": body.icon}
        self.rows[(user_id, pid)] = project
        return project

    async def get_by_id(self, user_id, project_id):
        return self.rows.get((user_id, project_id))

    async def update(self, user_id, project_id, body):
        if self.fail_update or (user_id, project_id) not in self.rows:
            return None
        patch = body.model_dump(exclude_none=True)
        self.rows[(user_id, project_id)].update({k: v for k, v in patch.items() if k in ("name", "instructions", "memory", "icon")})
        return self.rows[(user_id, project_id)]


@pytest.fixture
def repo(monkeypatch):
    fake = FakeProjectRepo()
    monkeypatch.setattr(module, "get_project_repository", lambda: fake)
    return fake


@pytest.fixture
def client(make_client, repo):
    return make_client(module.router)


class TestAuthentication:
    @pytest.mark.parametrize(
        ("method", "path"),
        [("GET", "/api/projects"), ("POST", "/api/projects"), ("GET", "/api/projects/p1"), ("PUT", "/api/projects/p1")],
    )
    async def test_requires_login(self, make_client, repo, method, path):
        response = await make_client(module.router, user=None).request(
            method, path, json={"name": "x"} if method in ("POST", "PUT") else None
        )

        assert response.status_code in (401, 403)
        assert repo.rows == {}


class TestCreateAndList:
    async def test_create_returns_201_and_the_project(self, client, repo):
        response = await client.post("/api/projects", json={"name": "Отчёты", "instructions": "кратко", "memory": "default"})

        assert response.status_code == 201
        body = response.json()
        assert body["success"] is True
        assert body["project"]["name"] == "Отчёты"
        assert ("alice", body["project"]["id"]) in repo.rows

    async def test_aliases_for_icon_fields_are_accepted(self, client):
        response = await client.post("/api/projects", json={"name": "P", "iconType": "emoji", "iconColor": "#fff"})

        assert response.status_code == 201

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"name": ""},
            {"name": "x" * 256},
            {"name": "ok", "instructions": "y" * 10001},
            {"name": "ok", "memory": "nonsense"},
            {"name": "ok", "id": "i" * 65},
        ],
    )
    async def test_invalid_payload_is_rejected(self, client, repo, body):
        response = await client.post("/api/projects", json=body)

        assert response.status_code == 422
        assert repo.rows == {}

    async def test_repository_failure(self, client, repo):
        repo.fail_create = True

        response = await client.post("/api/projects", json={"name": "P"})

        assert response.status_code == 500

    async def test_list_shows_only_own_projects(self, make_client, repo):
        await make_client(module.router).post("/api/projects", json={"name": "Алисин"})
        await make_client(module.router, user=BOB).post("/api/projects", json={"name": "Бобов"})

        alice = (await make_client(module.router).get("/api/projects")).json()
        bob = (await make_client(module.router, user=BOB).get("/api/projects")).json()

        assert [p["name"] for p in alice["projects"]] == ["Алисин"] and alice["count"] == 1
        assert [p["name"] for p in bob["projects"]] == ["Бобов"] and bob["count"] == 1


class TestGetAndUpdate:
    async def test_get_own_project(self, client):
        created = (await client.post("/api/projects", json={"name": "P"})).json()["project"]

        response = await client.get(f"/api/projects/{created['id']}")

        assert response.json()["project"]["name"] == "P"

    async def test_foreign_project_looks_like_it_does_not_exist(self, make_client, repo):
        created = (await make_client(module.router).post("/api/projects", json={"name": "Секрет"})).json()["project"]

        response = await make_client(module.router, user=BOB).get(f"/api/projects/{created['id']}")

        assert response.status_code == 404
        assert "Секрет" not in response.text

    async def test_missing_project(self, client):
        assert (await client.get("/api/projects/ghost")).status_code == 404

    async def test_update_changes_only_sent_fields(self, client):
        created = (await client.post("/api/projects", json={"name": "Старое", "instructions": "i"})).json()["project"]

        response = await client.put(f"/api/projects/{created['id']}", json={"name": "Новое"})

        project = response.json()["project"]
        assert (project["name"], project["instructions"]) == ("Новое", "i")

    async def test_update_of_a_foreign_project_does_not_touch_it(self, make_client, repo):
        created = (await make_client(module.router).post("/api/projects", json={"name": "Алисин"})).json()["project"]

        await make_client(module.router, user=BOB).put(f"/api/projects/{created['id']}", json={"name": "Взломан"})

        assert repo.rows[("alice", created["id"])]["name"] == "Алисин"

    async def test_update_of_an_unknown_id_creates_the_project_for_the_caller(self, client, repo):
        response = await client.put("/api/projects/client-chosen-id", json={"name": "Из UI"})

        assert response.status_code == 200
        assert response.json()["project"]["id"] == "client-chosen-id"
        assert repo.rows[("alice", "client-chosen-id")]["name"] == "Из UI"

    async def test_upsert_uses_defaults_for_missing_fields(self, client, repo):
        await client.put("/api/projects/new-one", json={})

        row = repo.rows[("alice", "new-one")]
        assert (row["name"], row["instructions"], row["memory"]) == ("Проект", "", "default")

    async def test_failed_upsert(self, client, repo):
        repo.fail_create = True

        assert (await client.put("/api/projects/new-one", json={"name": "x"})).status_code == 404

    async def test_failed_update_of_an_existing_project(self, client, repo):
        created = (await client.post("/api/projects", json={"name": "P"})).json()["project"]
        repo.fail_update = True

        assert (await client.put(f"/api/projects/{created['id']}", json={"name": "Q"})).status_code == 404

    async def test_update_validates_the_payload(self, client):
        response = await client.put("/api/projects/p1", json={"name": ""})

        assert response.status_code == 422

    async def test_user_without_an_id_is_treated_as_an_empty_owner(self, make_client, repo):
        client = make_client(module.router, user={"username": "ghost"})

        await client.post("/api/projects", json={"name": "P"})

        assert list(repo.rows)[0][0] == ""
