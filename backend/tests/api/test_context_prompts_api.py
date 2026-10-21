"""routes/context_prompts: персональные контекстные промпты пользователя."""

import pytest

from backend.routes import context_prompts as module
from backend.tests.api.conftest import ADMIN, ALICE
from backend.utils.http_errors import GENERIC_INTERNAL_ERROR_DETAIL, public_error_detail

pytestmark = pytest.mark.unit

BASE = "/api/context-prompts"


def test_public_error_detail_is_generic_and_ignores_the_exception():
    assert public_error_detail(RuntimeError("secret-token")) == GENERIC_INTERNAL_ERROR_DETAIL
    assert public_error_detail() == GENERIC_INTERNAL_ERROR_DETAIL
    assert public_error_detail(ValueError("x"), default="Не удалось") == "Не удалось"


class FakeStore:
    """In-memory замена сервисов user_llm_settings, запоминает, чьим именем к ним ходили."""

    def __init__(self):
        self.calls = []
        self.data = {"global_prompt": "глобальный", "custom_prompts": {"p1": {"prompt": "x"}}}
        self.fail_with = None
        self.delete_result = True

    def _maybe_fail(self):
        if self.fail_with:
            raise self.fail_with

    async def get_user_context_prompts(self, uid):
        self.calls.append(("get", uid))
        self._maybe_fail()
        return self.data

    async def set_user_global_prompt(self, uid, prompt):
        self.calls.append(("set_global", uid, prompt))
        self._maybe_fail()

    async def set_user_model_prompt(self, uid, model_path, prompt):
        self.calls.append(("set_model", uid, model_path, prompt))
        self._maybe_fail()

    async def set_user_custom_prompt(self, uid, prompt_id, prompt, description):
        self.calls.append(("set_custom", uid, prompt_id, prompt, description))
        self._maybe_fail()

    async def delete_user_custom_prompt(self, uid, prompt_id):
        self.calls.append(("delete", uid, prompt_id))
        self._maybe_fail()
        return self.delete_result

    async def get_user_prompt_manager(self, uid):
        self.calls.append(("manager", uid))
        self._maybe_fail()
        store = self

        class Manager:
            def get_model_prompt(self, model_path):
                return f"prompt-for-{model_path}"

            def get_effective_prompt(self, model_path, custom_prompt_id):
                return f"{model_path}|{custom_prompt_id}"

            def enrich_models_with_prompts(self, models):
                return [{**m, "has_prompt": True} for m in models]

        return Manager()


@pytest.fixture
def store(monkeypatch):
    fake = FakeStore()
    for name in (
        "get_user_context_prompts",
        "set_user_global_prompt",
        "set_user_model_prompt",
        "set_user_custom_prompt",
        "delete_user_custom_prompt",
        "get_user_prompt_manager",
    ):
        monkeypatch.setattr(module, name, getattr(fake, name))
    return fake


@pytest.fixture
def client(make_client, store, cef_events):
    cef_events(module)
    return make_client(module.router)


class TestAuthentication:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("GET", "/global"),
            ("PUT", "/global"),
            ("GET", "/models"),
            ("GET", "/model/openai/gpt"),
            ("PUT", "/model/openai/gpt"),
            ("GET", "/custom"),
            ("POST", "/custom"),
            ("DELETE", "/custom/p1"),
            ("GET", "/effective/openai/gpt"),
        ],
    )
    async def test_every_endpoint_rejects_anonymous_requests(self, make_client, store, method, path):
        anonymous = make_client(module.router, user=None)

        response = await anonymous.request(method, BASE + path, json={} if method in ("PUT", "POST") else None)

        assert response.status_code in (401, 403)
        assert store.calls == []

    async def test_invalid_bearer_token_is_rejected(self, make_client, store):
        anonymous = make_client(module.router, user=None)

        response = await anonymous.get(BASE + "/global", headers={"Authorization": "Bearer not-a-jwt"})

        assert response.status_code == 401
        assert store.calls == []


class TestGlobalPrompt:
    async def test_get(self, client, store):
        response = await client.get(BASE + "/global")

        assert response.status_code == 200
        assert response.json()["prompt"] == "глобальный"
        assert response.json()["success"] is True
        assert store.calls == [("get", "alice")]

    async def test_get_with_no_saved_prompt(self, client, store):
        store.data = {}

        assert (await client.get(BASE + "/global")).json()["prompt"] == ""

    async def test_update_saves_for_the_caller_and_audits(self, client, store, cef_events):
        events = cef_events(module)

        response = await client.put(BASE + "/global", json={"prompt": "новый"})

        assert response.status_code == 200
        assert store.calls == [("set_global", "alice", "новый")]
        assert [code for code, _ in events] == ["SEC005"]
        assert events[0][1]["current_user"]["username"] == "alice"

    async def test_update_without_a_prompt_saves_an_empty_one(self, client, store):
        await client.put(BASE + "/global", json={})

        assert store.calls == [("set_global", "alice", "")]

    async def test_users_are_isolated_by_their_ids(self, make_client, store):
        bob = make_client(module.router, user={**ALICE, "username": "bob", "user_id": "bob"})

        await bob.get(BASE + "/global")

        assert store.calls == [("get", "bob")]

    async def test_falls_back_to_the_username_when_there_is_no_user_id(self, make_client, store):
        client = make_client(module.router, user={"username": "carol"})

        await client.get(BASE + "/global")

        assert store.calls == [("get", "carol")]


class TestModelPrompts:
    async def test_model_path_with_slashes_is_passed_whole(self, client, store):
        response = await client.get(BASE + "/model/openrouter/anthropic/claude")

        assert response.json()["model_path"] == "openrouter/anthropic/claude"
        assert response.json()["prompt"] == "prompt-for-openrouter/anthropic/claude"

    async def test_update_model_prompt(self, client, store):
        response = await client.put(BASE + "/model/vllm/qwen", json={"prompt": "кратко"})

        assert response.status_code == 200
        assert store.calls == [("set_model", "alice", "vllm/qwen", "кратко")]

    async def test_effective_prompt_with_custom_id(self, client):
        response = await client.get(BASE + "/effective/vllm/qwen", params={"custom_prompt_id": "p1"})

        assert response.json()["prompt"] == "vllm/qwen|p1"
        assert response.json()["custom_prompt_id"] == "p1"

    async def test_effective_prompt_without_custom_id(self, client):
        response = await client.get(BASE + "/effective/vllm/qwen")

        assert response.json()["prompt"] == "vllm/qwen|None"

    async def test_models_listing_is_enriched(self, client, monkeypatch):
        from backend.routes import models as models_module

        async def fake_models():
            return {"models": [{"path": "a/b"}]}

        monkeypatch.setattr(models_module, "get_available_models", fake_models)

        response = await client.get(BASE + "/models")

        assert response.json()["models"] == [{"path": "a/b", "has_prompt": True}]


class TestCustomPrompts:
    async def test_list(self, client):
        response = await client.get(BASE + "/custom")

        assert response.json()["prompts"] == {"p1": {"prompt": "x"}}

    async def test_list_defaults_to_an_empty_mapping(self, client, store):
        store.data = {"custom_prompts": None}

        assert (await client.get(BASE + "/custom")).json()["prompts"] == {}

    async def test_create_trims_id_and_prompt(self, client, store):
        response = await client.post(
            BASE + "/custom", json={"id": "  fin  ", "prompt": "  отвечай как финансист ", "description": "d"}
        )

        assert response.status_code == 200
        assert store.calls == [("set_custom", "alice", "fin", "отвечай как финансист", "d")]

    @pytest.mark.parametrize(
        "body",
        [{}, {"id": "a"}, {"prompt": "p"}, {"id": "  ", "prompt": "p"}, {"id": "a", "prompt": "   "}],
    )
    async def test_create_requires_id_and_prompt(self, client, store, body):
        response = await client.post(BASE + "/custom", json=body)

        assert response.status_code == 400
        assert store.calls == []

    async def test_create_with_non_string_fields_is_a_server_error_not_a_crash(self, client, store):
        response = await client.post(BASE + "/custom", json={"id": 5, "prompt": "p"})

        assert response.status_code == 500
        assert store.calls == []

    async def test_delete(self, client, store):
        response = await client.delete(BASE + "/custom/p1")

        assert response.status_code == 200
        assert store.calls == [("delete", "alice", "p1")]

    async def test_delete_missing_prompt_is_404(self, client, store):
        store.delete_result = False

        response = await client.delete(BASE + "/custom/ghost")

        assert response.status_code == 404
        assert response.json()["detail"] == "Промпт не найден"


class TestErrors:
    @pytest.mark.parametrize(
        ("method", "path", "body"),
        [
            ("GET", "/global", None),
            ("PUT", "/global", {"prompt": "x"}),
            ("GET", "/custom", None),
            ("GET", "/model/a/b", None),
            ("PUT", "/model/a/b", {"prompt": "x"}),
            ("GET", "/effective/a/b", None),
            ("DELETE", "/custom/p1", None),
        ],
    )
    async def test_service_failures_become_500(self, client, store, method, path, body):
        store.fail_with = RuntimeError("db down")

        response = await client.request(method, BASE + path, json=body)

        assert response.status_code == 500

    async def test_internal_error_text_is_not_returned_to_the_client(self, client, store):
        # CWE-209: текст исключения (хосты, креды) не должен попадать в ответ.
        store.fail_with = RuntimeError("connection to postgres://admin:hunter2@db:5432 refused")

        response = await client.get(BASE + "/global")

        assert response.status_code == 500
        assert "hunter2" not in response.text
        assert response.json()["detail"] == GENERIC_INTERNAL_ERROR_DETAIL

    async def test_admin_flag_does_not_change_user_scoping(self, make_client, store):
        admin = make_client(module.router, user=ADMIN)

        await admin.get(BASE + "/global")

        assert store.calls == [("get", "root")]
