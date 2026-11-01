"""sso_logging: KeycloakSsoError, суффикс SEC003, запись ошибок SSO в журнал и CEF."""

import logging

import pytest

from backend.auth import sso_logging as sl

pytestmark = pytest.mark.unit


@pytest.fixture
def cef(monkeypatch):
    events = []
    monkeypatch.setattr(sl, "log_cef_event", lambda code, **k: events.append((code, k)))
    return events


class TestKeycloakSsoError:
    def test_fields_are_sanitized_and_defaulted(self):
        err = sl.KeycloakSsoError(
            401, "Отказ", log_message="msg %s", log_args=("a",), cef_reason="причина\nвторая",
            username="ev\nil", error_detail="деталь\r\n2",
        )

        assert str(err) == "Отказ" and err.status_code == 401 and err.detail == "Отказ"
        assert "\n" not in err.cef_reason and "\n" not in err.username and "\r" not in err.error_detail
        assert err.log_args == ("a",) and err.log_exception is False

    def test_defaults(self):
        err = sl.KeycloakSsoError(500, "Детали", log_message="m")

        assert err.cef_reason == "Детали" and err.username == "anonymous" and err.error_detail is None

    def test_blank_username_becomes_anonymous(self):
        assert sl.KeycloakSsoError(500, "d", log_message="m", username="").username == "anonymous"


class TestReasonSuffix:
    def test_contains_issuer(self, monkeypatch):
        monkeypatch.setattr(
            "backend.auth.keycloak_auth.get_keycloak_settings", lambda: {"issuer": "https://sso/realms/x"}
        )

        assert sl.keycloak_reason_suffix() == " (Keycloak issuer https://sso/realms/x)"

    def test_missing_issuer_uses_dash(self, monkeypatch):
        monkeypatch.setattr("backend.auth.keycloak_auth.get_keycloak_settings", lambda: {})

        assert sl.keycloak_reason_suffix() == " (Keycloak issuer -)"

    def test_settings_error_gives_empty_suffix(self, monkeypatch):
        def boom():
            raise ValueError("нет настроек")

        monkeypatch.setattr("backend.auth.keycloak_auth.get_keycloak_settings", boom)

        assert sl.keycloak_reason_suffix() == ""


class TestLogFailure:
    def test_warning_detail_and_cef(self, cef, caplog, monkeypatch):
        monkeypatch.setattr(sl, "keycloak_reason_suffix", lambda: " (S)")
        request = object()
        with caplog.at_level(logging.WARNING):
            sl.log_keycloak_sso_failure(
                request=request, status_code=401, log_message="отказ user=%s", log_args=("ivan",),
                cef_reason="Причина", username="ivan\n", error_detail="подробно",
            )

        code, kwargs = cef[0]
        assert code == "SEC003" and len(cef) == 1
        assert kwargs["request"] is request and kwargs["status_code"] == 401
        assert kwargs["extra"] == {"reason": "Причина (S)", "auth_method": "keycloak"}
        assert "\n" not in kwargs["current_user"]["username"]
        assert any("отказ" in r.getMessage() for r in caplog.records)
        assert any("подробно" in r.getMessage() for r in caplog.records)

    def test_exception_branch_uses_logger_exception(self, cef, monkeypatch):
        calls = []
        monkeypatch.setattr(sl.logger, "exception", lambda *a: calls.append(("exc", a)))
        monkeypatch.setattr(sl.logger, "warning", lambda *a: calls.append(("warn", a)))
        monkeypatch.setattr(sl, "keycloak_reason_suffix", lambda: "")

        sl.log_keycloak_sso_failure(
            status_code=503, log_message="сбой %s", log_args=(1,), cef_reason="r", log_exception=True,
            error_detail="d",
        )

        assert calls[0] == ("exc", ("сбой %s", 1))
        assert calls[1][0] == "warn" and calls[1][1][1] == "d"
        assert cef[0][1]["current_user"] == {"username": "anonymous"}

    def test_without_detail_only_one_warning(self, cef, monkeypatch):
        calls = []
        monkeypatch.setattr(sl.logger, "warning", lambda *a: calls.append(a))
        monkeypatch.setattr(sl, "keycloak_reason_suffix", lambda: "")

        sl.log_keycloak_sso_failure(status_code=401, log_message="m", cef_reason="r")

        assert calls == [("m",)]


def test_record_error_forwards_all_fields(monkeypatch):
    got = {}
    monkeypatch.setattr(sl, "log_keycloak_sso_failure", lambda **k: got.update(k))
    err = sl.KeycloakSsoError(
        403, "нет роли", log_message="m %s", log_args=("u",), cef_reason="роль", username="ivan",
        log_exception=True, error_detail="det",
    )
    request = object()

    sl.record_keycloak_sso_error(request, err)

    assert got == {
        "request": request, "status_code": 403, "log_message": "m %s", "log_args": ("u",),
        "cef_reason": "роль", "username": "ivan", "log_exception": True, "error_detail": "det",
    }
