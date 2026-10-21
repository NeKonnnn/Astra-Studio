"""routes/share: публичные ссылки на сообщения (создание, просмотр, список, удаление)."""

from types import SimpleNamespace

import pytest

from backend.routes import share as module
from backend.tests.api.conftest import ALICE

pytestmark = pytest.mark.unit

BOB = {**ALICE, "username": "bob", "user_id": "bob"}
MESSAGES = [{"id": "m1", "role": "user", "content": "привет"}, {"id": "m2", "role": "assistant", "content": "здравствуйте"}]


class FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, key, direction):
        self._docs.sort(key=lambda d: d.get(key, ""), reverse=direction == -1)
        return self

    def __aiter__(self):
        self._iter = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return dict(next(self._iter))
        except StopIteration:
            raise StopAsyncIteration from None


class FakeCollection:
    def __init__(self):
        self.docs = []
        self.insert_result = SimpleNamespace(inserted_id="oid")
        self.deleted = 1

    async def insert_one(self, doc):
        self.docs.append(dict(doc))
        return self.insert_result

    def find(self, query):
        return FakeCursor([d for d in self.docs if all(d.get(k) == v for k, v in query.items())])

    async def find_one(self, query):
        for doc in self.docs:
            if all(doc.get(k) == v for k, v in query.items()):
                return dict(doc)
        return None

    async def delete_one(self, query):
        before = len(self.docs)
        self.docs = [d for d in self.docs if not all(d.get(k) == v for k, v in query.items())]
        return SimpleNamespace(deleted_count=min(self.deleted, before - len(self.docs)))


@pytest.fixture
def collection(monkeypatch):
    coll = FakeCollection()
    conn = SimpleNamespace(db=SimpleNamespace(shared_conversations=coll))
    monkeypatch.setattr(module, "get_mongodb_connection", lambda: conn)
    return coll


def _doc(share_id, created_by, created_at="2026-01-01T00:00:00"):
    return {"share_id": share_id, "messages": MESSAGES, "created_at": created_at, "created_by": created_by}


class TestCreate:
    async def test_creates_a_link_owned_by_the_caller(self, make_client, collection):
        response = await make_client(module.router).post("/api/share/create", json={"messages": MESSAGES})

        body = response.json()
        assert response.status_code == 200
        assert body["url"] == f"/share/{body['share_id']}"
        (saved,) = collection.docs
        assert saved["created_by"] == "alice"
        assert saved["share_id"] == body["share_id"]
        assert [m["content"] for m in saved["messages"]] == ["привет", "здравствуйте"]

    async def test_each_link_gets_a_unique_hard_to_guess_id(self, make_client, collection):
        client = make_client(module.router)

        first = (await client.post("/api/share/create", json={"messages": MESSAGES})).json()["share_id"]
        second = (await client.post("/api/share/create", json={"messages": MESSAGES})).json()["share_id"]

        assert first != second
        assert len(first) == 36

    async def test_rejects_an_empty_selection(self, make_client, collection):
        response = await make_client(module.router).post("/api/share/create", json={"messages": []})

        assert response.status_code == 400
        assert collection.docs == []

    @pytest.mark.parametrize("body", [{}, {"messages": "x"}, {"messages": [{"id": "1"}]}])
    async def test_validates_the_payload(self, make_client, collection, body):
        response = await make_client(module.router).post("/api/share/create", json=body)

        assert response.status_code == 422

    async def test_database_unavailable(self, make_client, monkeypatch):
        monkeypatch.setattr(module, "get_mongodb_connection", lambda: None)

        response = await make_client(module.router).post("/api/share/create", json={"messages": MESSAGES})

        assert response.status_code == 500
        assert response.json()["detail"] == "База данных недоступна"

    async def test_failed_insert(self, make_client, collection):
        collection.insert_result = SimpleNamespace(inserted_id=None)

        response = await make_client(module.router).post("/api/share/create", json={"messages": MESSAGES})

        assert response.status_code == 500

    async def test_unexpected_errors_do_not_leak_details(self, make_client, collection):
        async def boom(doc):
            raise RuntimeError("mongodb://admin:secret@host")

        collection.insert_one = boom

        response = await make_client(module.router).post("/api/share/create", json={"messages": MESSAGES})

        assert response.status_code == 500
        assert response.json()["detail"] == "Внутренняя ошибка сервера"
        assert "secret" not in response.text

    async def test_anonymous_visitors_can_create_links_without_an_owner(self, make_client, collection):
        response = await make_client(module.router, user=None).post("/api/share/create", json={"messages": MESSAGES})

        assert response.status_code == 200
        assert collection.docs[0]["created_by"] is None


class TestView:
    async def test_public_link_is_readable_without_login(self, make_client, collection):
        collection.docs.append(_doc("abc", "alice"))

        response = await make_client(module.router, user=None).get("/api/share/abc")

        assert response.status_code == 200
        assert response.json()["share_id"] == "abc"
        assert len(response.json()["messages"]) == 2

    async def test_internal_mongo_id_is_not_exposed(self, make_client, collection):
        collection.docs.append({**_doc("abc", "alice"), "_id": "mongo-oid"})

        response = await make_client(module.router, user=None).get("/api/share/abc")

        assert "_id" not in response.json()

    async def test_unknown_link(self, make_client, collection):
        response = await make_client(module.router, user=None).get("/api/share/nope")

        assert response.status_code == 404

    async def test_database_unavailable(self, make_client, monkeypatch):
        monkeypatch.setattr(module, "get_mongodb_connection", lambda: SimpleNamespace(db=None))

        response = await make_client(module.router, user=None).get("/api/share/abc")

        assert response.status_code == 500


class TestMyShares:
    async def test_lists_only_own_links_newest_first(self, make_client, collection):
        collection.docs += [
            _doc("old", "alice", "2026-01-01T00:00:00"),
            _doc("new", "alice", "2026-03-01T00:00:00"),
            _doc("bobs", "bob", "2026-02-01T00:00:00"),
        ]

        response = await make_client(module.router).get("/api/share/my-shares")

        assert [s["share_id"] for s in response.json()] == ["new", "old"]

    async def test_anonymous_gets_an_empty_list(self, make_client, collection):
        collection.docs.append(_doc("abc", "alice"))

        response = await make_client(module.router, user=None).get("/api/share/my-shares")

        assert response.json() == []

    async def test_user_without_an_id_gets_an_empty_list(self, make_client, collection):
        collection.docs.append(_doc("abc", None))

        response = await make_client(module.router, user={"username": "x"}).get("/api/share/my-shares")

        assert response.json() == []

    async def test_route_is_not_shadowed_by_the_dynamic_id_route(self, make_client, collection):
        response = await make_client(module.router).get("/api/share/my-shares")

        assert response.status_code == 200
        assert isinstance(response.json(), list)


class TestDelete:
    async def test_owner_can_delete(self, make_client, collection):
        collection.docs.append(_doc("abc", "alice"))

        response = await make_client(module.router).delete("/api/share/abc")

        assert response.status_code == 200
        assert collection.docs == []

    async def test_another_user_cannot_delete(self, make_client, collection):
        collection.docs.append(_doc("abc", "alice"))

        response = await make_client(module.router, user=BOB).delete("/api/share/abc")

        assert response.status_code == 403
        assert len(collection.docs) == 1

    async def test_unknown_link(self, make_client, collection):
        response = await make_client(module.router).delete("/api/share/ghost")

        assert response.status_code == 404

    async def test_delete_that_removes_nothing_is_reported(self, make_client, collection):
        collection.docs.append(_doc("abc", "alice"))
        collection.deleted = 0

        response = await make_client(module.router).delete("/api/share/abc")

        assert response.status_code == 500

    async def test_anonymous_visitor_must_not_delete_someone_elses_link(self, make_client, collection):
        collection.docs.append(_doc("abc", "alice"))

        response = await make_client(module.router, user=None).delete("/api/share/abc")

        assert response.status_code in (401, 403)
        assert len(collection.docs) == 1
