"""auth/routes: вход через LDAP, блокировка, refresh, выход, профиль, аватар, проверка токена."""

from types import SimpleNamespace

import httpx
import pytest
import respx
from fastapi import HTTPException

from backend.auth import jwt_handler, login_lockout
from backend.auth import routes as module

pytestmark = pytest.mark.unit

ALICE_PROFILE = {
    "username": "alice",
    "user_id": "alice",
    "email": "alice@example.com",
    "full_name": "Alice A",
    "is_admin": False,
    "groups": ["staff"],
    "ldap_groups": ["staff"],
    "auth_method": "ldap",
}


@pytest.fixture
def ldap(monkeypatch):
    """Фейковый каталог LDAP + чистое состояние сессий/блокировок + перехват CEF."""
    jwt_handler._active_sessions_by_user.clear()
    jwt_handler._client_bindings.clear()
    jwt_handler.reset_consumed_refresh_tokens()
    login_lockout._states.clear()
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")  # тестовый клиент ходит по http

    state = SimpleNamespace(
        users={"alice": ("secret", dict(ALICE_PROFILE)), "bob": ("hunter2", {**ALICE_PROFILE, "username": "bob", "user_id": "bob"})},
        enabled=True,
        events=[],
        revoked_accounts=set(),
        photo=b"\xff\xd8photo",
        profile_override=None,
        validate_calls=[],
    )

    def authenticate(username, password):
        entry = state.users.get(username)
        return dict(entry[1]) if entry and entry[0] == password else None

    def validate(username, user_id=None):
        state.validate_calls.append(username)
        if username in state.revoked_accounts:
            raise HTTPException(status_code=401, detail="Учётная запись отключена")

    def fetch_profile(username):
        if state.profile_override is not None:
            return state.profile_override
        entry = state.users.get(username)
        return dict(entry[1]) if entry else None

    record = lambda code, **kw: state.events.append((code, kw))  # noqa: E731
    monkeypatch.setattr(module, "is_ldap_enabled", lambda: state.enabled)
    monkeypatch.setattr(module, "authenticate_ldap", authenticate)
    monkeypatch.setattr(module, "validate_ldap_user_account", validate)
    monkeypatch.setattr(module, "fetch_ldap_user_profile", fetch_profile)
    monkeypatch.setattr(module, "fetch_ldap_user_photo", lambda username, user_id=None: state.photo)
    monkeypatch.setattr(module, "log_cef_event", record)
    monkeypatch.setattr(jwt_handler, "log_cef_event", record)

    yield state

    jwt_handler._active_sessions_by_user.clear()
    jwt_handler._client_bindings.clear()
    jwt_handler.reset_consumed_refresh_tokens()
    login_lockout._states.clear()


@pytest.fixture
def client(make_client, ldap):
    return make_client(module.router, user=None)


async def _login(client, username="alice", password="secret"):
    return await client.post("/api/auth/login", json={"username": username, "password": password})


def _bearer(tokens, key="access_token"):
    return {"Authorization": f"Bearer {tokens[key]}"}


class TestPublicPolicies:
    async def test_lockout_policy(self, client):
        body = (await client.get("/api/auth/login-lockout-policy")).json()

        assert set(body) == {"enabled", "max_failed_attempts", "lockout_duration_seconds"}

    async def test_session_policy(self, client):
        body = (await client.get("/api/auth/session-policy")).json()

        assert body == {"timeout_seconds": 1800, "enabled": True}

    async def test_server_instance_is_stable(self, client):
        first = (await client.get("/api/auth/server-instance")).json()["instance_id"]
        second = (await client.get("/api/auth/server-instance")).json()["instance_id"]

        assert first == second and len(first) == 36


class TestLogin:
    async def test_success_returns_tokens_profile_and_binding_cookie(self, client, ldap):
        response = await _login(client)

        body = response.json()
        assert response.status_code == 200
        assert body["token_type"] == "bearer"
        assert body["user"]["username"] == "alice"
        assert body["session_policy"] == {"timeout_seconds": 1800, "enabled": True}
        assert body["server_instance_id"] == jwt_handler.get_server_instance_id()
        cookie = response.headers["set-cookie"]
        assert jwt_handler.CLIENT_BINDING_COOKIE in cookie
        assert "HttpOnly" in cookie and "samesite=lax" in cookie.lower()

    async def test_issued_tokens_are_valid_and_scoped(self, client):
        body = (await _login(client)).json()

        access = jwt_handler._decode_jwt(body["access_token"], jwt_handler.JWT_ACCESS_SECRET)
        refresh = jwt_handler._decode_jwt(body["refresh_token"], jwt_handler.JWT_REFRESH_SECRET)
        assert (access["token_type"], refresh["token_type"]) == ("access", "refresh")
        assert access["sub"] == refresh["sub"] == "alice"
        assert access["sid"] == refresh["sid"]
        assert access["grp"] == ["staff"]

    async def test_success_is_audited(self, client, ldap):
        await _login(client)

        codes = [c for c, _ in ldap.events]
        assert "SEC001" in codes and "USR001" in codes

    async def test_when_ldap_disabled_mock_admin_can_login(self, client, ldap):
        ldap.enabled = False

        response = await _login(client, "admin", "admin123")
        body = response.json()

        assert response.status_code == 200
        assert body["user"]["username"] == "admin"
        assert body["user"]["is_admin"] is True
        assert body["user"]["auth_method"] == "mock"
        access = jwt_handler._decode_jwt(body["access_token"], jwt_handler.JWT_ACCESS_SECRET)
        assert access.get("am") == "mock"

    async def test_wrong_password_is_401_with_remaining_attempts(self, client, ldap):
        response = await _login(client, password="wrong")

        detail = response.json()["detail"]
        assert response.status_code == 401
        assert detail["message"] == "Неверное имя пользователя или пароль"
        assert detail["remaining_attempts"] == 4
        assert [c for c, _ in ldap.events] == ["SEC003"]

    async def test_unknown_user_looks_exactly_like_a_wrong_password(self, client):
        wrong = (await _login(client, "alice", "wrong")).json()["detail"]
        unknown = (await _login(client, "nobody", "whatever")).json()["detail"]

        assert wrong["message"] == unknown["message"]

    async def test_account_is_locked_after_repeated_failures_even_for_the_right_password(self, client, ldap):
        for _ in range(5):
            await _login(client, password="wrong")

        response = await _login(client, password="secret")

        assert response.status_code == 429
        assert response.headers["retry-after"]
        assert response.json()["detail"]["retry_after_seconds"] > 0
        assert ldap.events[-1][0] == "SEC003" and ldap.events[-1][1]["status_code"] == 429

    async def test_lockout_is_per_account(self, client):
        for _ in range(5):
            await _login(client, "alice", "wrong")

        assert (await _login(client, "bob", "hunter2")).status_code == 200

    async def test_success_resets_the_failure_counter(self, client):
        for _ in range(3):
            await _login(client, password="wrong")
        await _login(client)

        response = await _login(client, password="wrong")

        assert response.json()["detail"]["remaining_attempts"] == 4

    async def test_ldap_disabled_falls_back_to_mock_and_rejects_unknown(self, client, ldap):
        """Без LDAP нет 503: пробуем mock; чужой логин → 401."""
        ldap.enabled = False

        response = await _login(client)  # alice/secret — не mock-учётка

        assert response.status_code == 401
        assert [c for c, _ in ldap.events] == ["SEC003"]

    @pytest.mark.parametrize("body", [{}, {"username": "alice"}, {"password": "x"}, {"username": 1, "password": []}])
    async def test_validates_the_payload(self, client, body):
        assert (await client.post("/api/auth/login", json=body)).status_code == 422

    async def test_session_limit_revokes_the_oldest_login(self, client):
        first = (await _login(client)).json()
        for _ in range(3):
            await _login(client)

        sid = jwt_handler._decode_jwt(first["access_token"], jwt_handler.JWT_ACCESS_SECRET)["sid"]
        assert sid not in jwt_handler._active_sessions_by_user["alice"]


class TestFullSession:
    async def test_login_me_refresh_logout_cycle(self, client):
        tokens = (await _login(client)).json()

        me = await client.get("/api/auth/me", headers=_bearer(tokens))
        assert me.status_code == 200 and me.json()["username"] == "alice"

        refreshed = await client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
        assert refreshed.status_code == 200
        new_tokens = refreshed.json()
        assert (await client.get("/api/auth/me", headers=_bearer(new_tokens))).status_code == 200

        logout = await client.post("/api/auth/logout", headers=_bearer(new_tokens))
        assert logout.status_code == 200 and logout.json()["username"] == "alice"

        assert (await client.get("/api/auth/me", headers=_bearer(new_tokens))).status_code == 401
        assert (await client.get("/api/auth/me", headers=_bearer(tokens))).status_code == 401

    async def test_logout_kills_the_refresh_token_too(self, client):
        tokens = (await _login(client)).json()
        await client.post("/api/auth/logout", headers=_bearer(tokens))

        response = await client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})

        assert response.status_code == 401

    async def test_logout_is_audited_and_clears_the_cookie(self, client, ldap):
        tokens = (await _login(client)).json()

        response = await client.post("/api/auth/logout", headers=_bearer(tokens))

        assert "SEC002" in [c for c, _ in ldap.events]
        assert jwt_handler.CLIENT_BINDING_COOKIE in response.headers["set-cookie"]

    async def test_token_does_not_work_without_the_browser_binding_cookie(self, make_client, ldap):
        first = make_client(module.router, user=None)
        tokens = (await _login(first)).json()
        other_browser = make_client(module.router, user=None)

        response = await other_browser.get("/api/auth/me", headers=_bearer(tokens))

        assert response.status_code == 401

    async def test_stolen_token_with_another_users_cookie_is_rejected(self, make_client, ldap):
        alice_browser = make_client(module.router, user=None)
        bob_browser = make_client(module.router, user=None)
        alice_tokens = (await _login(alice_browser, "alice", "secret")).json()
        await _login(bob_browser, "bob", "hunter2")

        response = await bob_browser.get("/api/auth/me", headers=_bearer(alice_tokens))

        assert response.status_code == 401

    async def test_logout_in_one_browser_does_not_end_the_other_session(self, make_client, ldap):
        one, two = make_client(module.router, user=None), make_client(module.router, user=None)
        first = (await _login(one)).json()
        second = (await _login(two)).json()

        await one.post("/api/auth/logout", headers=_bearer(first))

        assert (await two.get("/api/auth/me", headers=_bearer(second))).status_code == 200


class TestRefresh:
    async def test_new_tokens_are_issued_later_than_the_old_ones(self, client):
        import asyncio

        tokens = (await _login(client)).json()
        await asyncio.sleep(1.05)
        new = (await client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})).json()

        old_claims = jwt_handler._decode_jwt(tokens["access_token"], jwt_handler.JWT_ACCESS_SECRET, verify_exp=False)
        new_claims = jwt_handler._decode_jwt(new["access_token"], jwt_handler.JWT_ACCESS_SECRET, verify_exp=False)
        assert new["access_token"] != tokens["access_token"]
        assert new_claims["exp"] > old_claims["exp"]

    async def test_garbage_token(self, client, ldap):
        response = await client.post("/api/auth/refresh", json={"refresh_token": "junk"})

        codes = [c for c, _ in ldap.events]
        assert response.status_code == 401
        assert "INT006" in codes and "SEC007" in codes

    async def test_access_token_cannot_be_used_to_refresh(self, client):
        tokens = (await _login(client)).json()

        response = await client.post("/api/auth/refresh", json={"refresh_token": tokens["access_token"]})

        assert response.status_code == 401

    async def test_disabled_account_ends_the_session(self, client, ldap):
        tokens = (await _login(client)).json()
        ldap.revoked_accounts.add("alice")

        response = await client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})

        assert response.status_code == 401
        assert (await client.get("/api/auth/me", headers=_bearer(tokens))).status_code == 401

    async def test_profile_comes_from_ldap_and_reflects_group_changes(self, client, ldap):
        tokens = (await _login(client)).json()
        ldap.profile_override = {**ALICE_PROFILE, "is_admin": True, "groups": ["staff", "admins"], "ldap_groups": ["staff", "admins"]}

        new = (await client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})).json()

        assert new["user"]["is_admin"] is True
        claims = jwt_handler._decode_jwt(new["access_token"], jwt_handler.JWT_ACCESS_SECRET)
        assert claims["adm"] is True and "admins" in claims["grp"]

    async def test_falls_back_to_the_token_profile_when_ldap_has_no_entry(self, client, ldap):
        tokens = (await _login(client)).json()
        ldap.users.clear()
        ldap.profile_override = None

        response = await client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})

        assert response.status_code == 200
        assert response.json()["user"]["username"] == "alice"

    async def test_session_id_is_preserved(self, client):
        tokens = (await _login(client)).json()

        new = (await client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})).json()

        sid = lambda t: jwt_handler._decode_jwt(t, jwt_handler.JWT_ACCESS_SECRET)["sid"]  # noqa: E731
        assert sid(tokens["access_token"]) == sid(new["access_token"])

    async def test_old_refresh_token_is_rejected_after_rotation(self, client, monkeypatch):
        # Refresh-токен одноразовый: после обмена (и по истечении окна повторного использования)
        # украденный токен уже не работает.
        monkeypatch.setenv("JWT_REFRESH_REUSE_GRACE_SECONDS", "0")
        tokens = (await _login(client)).json()
        first = await client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
        assert first.status_code == 200

        again = await client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})

        assert again.status_code == 401

    async def test_rotated_refresh_token_works_and_is_itself_one_time(self, client, monkeypatch):
        monkeypatch.setenv("JWT_REFRESH_REUSE_GRACE_SECONDS", "0")
        tokens = (await _login(client)).json()
        rotated = (await client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})).json()
        assert rotated["refresh_token"] != tokens["refresh_token"]

        second = await client.post("/api/auth/refresh", json={"refresh_token": rotated["refresh_token"]})
        replay = await client.post("/api/auth/refresh", json={"refresh_token": rotated["refresh_token"]})

        assert second.status_code == 200 and replay.status_code == 401

    async def test_parallel_refresh_within_the_grace_window_is_tolerated(self, client):
        # Две вкладки / повторный mount обновляют токен одновременно: второй запрос не должен разлогинить.
        tokens = (await _login(client)).json()

        first = await client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
        second = await client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})

        assert first.status_code == 200 and second.status_code == 200

    async def test_requires_the_browser_binding(self, make_client, ldap):
        tokens = (await _login(make_client(module.router, user=None))).json()

        response = await make_client(module.router, user=None).post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})

        assert response.status_code == 401


class TestProfileEndpoints:
    async def test_endpoints_require_login(self, client):
        for method, path in [("GET", "/me"), ("GET", "/me/avatar"), ("PUT", "/me"), ("DELETE", "/me"), ("GET", "/verify"), ("POST", "/logout")]:
            response = await client.request(method, "/api/auth" + path)
            assert response.status_code in (401, 403), (method, path)

    async def test_me(self, client):
        tokens = (await _login(client)).json()

        body = (await client.get("/api/auth/me", headers=_bearer(tokens))).json()

        assert (body["username"], body["email"], body["full_name"], body["is_active"], body["is_admin"]) == (
            "alice",
            "alice@example.com",
            "Alice A",
            True,
            False,
        )

    async def test_verify_checks_the_account_in_the_directory(self, client, ldap):
        tokens = (await _login(client)).json()

        ok = await client.get("/api/auth/verify", headers=_bearer(tokens))
        ldap.revoked_accounts.add("alice")
        gone = await client.get("/api/auth/verify", headers=_bearer(tokens))

        assert ok.json()["valid"] is True and ldap.validate_calls
        assert gone.status_code == 401
        assert (await client.get("/api/auth/me", headers=_bearer(tokens))).status_code == 401

    async def test_update_profile_is_not_persisted_known_issue(self, client):
        tokens = (await _login(client)).json()

        response = await client.put("/api/auth/me", params={"email": "new@example.com", "full_name": "Новое Имя"}, headers=_bearer(tokens))
        again = await client.get("/api/auth/me", headers=_bearer(tokens))

        assert response.json()["email"] == "new@example.com"
        assert again.json()["email"] == "alice@example.com"

    async def test_delete_account_only_ends_the_session_and_writes_an_audit_event_known_issue(self, client, ldap):
        tokens = (await _login(client)).json()

        response = await client.delete("/api/auth/me", headers=_bearer(tokens))

        assert response.json()["message"] == "Учетная запись помечена как удаленная"
        assert "USR002" in [c for c, _ in ldap.events]
        assert ldap.users.get("alice") is not None
        assert (await client.get("/api/auth/me", headers=_bearer(tokens))).status_code == 401


class TestAvatar:
    async def test_ldap_photo(self, client, ldap):
        tokens = (await _login(client)).json()

        response = await client.get("/api/auth/me/avatar", headers=_bearer(tokens))

        assert response.status_code == 200
        assert response.content == ldap.photo
        assert response.headers["content-type"] == "image/jpeg"
        assert "private" in response.headers["cache-control"]

    async def test_no_photo(self, client, ldap):
        tokens = (await _login(client)).json()
        ldap.photo = None

        assert (await client.get("/api/auth/me/avatar", headers=_bearer(tokens))).status_code == 404

    @staticmethod
    def _keycloak_user(make_client, avatar_url):
        return make_client(module.router, user={"username": "kc", "user_id": "kc", "auth_method": "keycloak", "avatar_url": avatar_url})

    @respx.mock
    async def test_keycloak_avatar_is_proxied(self, make_client):
        respx.get("https://idp.example.com/pic.png").mock(return_value=httpx.Response(200, content=b"PNG", headers={"content-type": "image/png"}))

        response = await self._keycloak_user(make_client, "https://idp.example.com/pic.png").get("/api/auth/me/avatar")

        assert response.status_code == 200 and response.content == b"PNG"
        assert response.headers["content-type"] == "image/png"

    @pytest.mark.parametrize("avatar_url", [None, "", "/api/auth/me/avatar", "file:///etc/passwd", "ftp://host/x.png", "javascript:alert(1)"])
    async def test_keycloak_avatar_requires_an_http_url(self, make_client, avatar_url):
        response = await self._keycloak_user(make_client, avatar_url).get("/api/auth/me/avatar")

        assert response.status_code == 404

    @respx.mock
    async def test_keycloak_upstream_failure_is_a_404(self, make_client):
        respx.get("https://idp.example.com/pic.png").mock(return_value=httpx.Response(500))

        response = await self._keycloak_user(make_client, "https://idp.example.com/pic.png").get("/api/auth/me/avatar")

        assert response.status_code == 404

    @respx.mock
    async def test_keycloak_avatar_does_not_follow_redirects_to_internal_addresses(self, make_client):
        metadata = respx.get("http://169.254.169.254/latest/meta-data/").mock(
            return_value=httpx.Response(200, content=b"iam-role-credentials", headers={"content-type": "text/plain"})
        )
        redirect = respx.get("https://idp.example.com/pic").mock(
            return_value=httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})
        )

        response = await self._keycloak_user(make_client, "https://idp.example.com/pic").get("/api/auth/me/avatar")

        assert redirect.called
        assert not metadata.called
        assert response.status_code == 404

    async def test_keycloak_avatar_rejects_literal_metadata_ip(self, make_client):
        response = await self._keycloak_user(
            make_client, "http://169.254.169.254/latest/meta-data/"
        ).get("/api/auth/me/avatar")

        assert response.status_code == 404
