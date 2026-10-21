"""auth/session_timeout: публичная политика абсолютного таймаута сессии."""

from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def policy(monkeypatch):
    from backend.auth import session_timeout

    def _set(seconds):
        monkeypatch.setattr(
            session_timeout,
            "_get_session_timeout_config",
            lambda: SimpleNamespace(timeout_seconds=seconds),
        )
        return session_timeout.get_session_policy()

    return _set


def test_positive_timeout_is_enabled(policy):
    assert policy(1800) == {"timeout_seconds": 1800, "enabled": True}


def test_zero_disables_timeout(policy):
    assert policy(0) == {"timeout_seconds": 0, "enabled": False}


def test_negative_value_is_clamped_to_disabled(policy):
    assert policy(-10) == {"timeout_seconds": 0, "enabled": False}


def test_string_seconds_are_coerced(policy):
    assert policy("60") == {"timeout_seconds": 60, "enabled": True}


def test_settings_session_timeout_comes_from_env(monkeypatch):
    from backend.settings.config import SessionTimeoutConfig

    monkeypatch.setenv("AUTH_SESSION_TIMEOUT_SECONDS", "90")
    assert SessionTimeoutConfig().timeout_seconds == 90

    monkeypatch.delenv("AUTH_SESSION_TIMEOUT_SECONDS")
    with pytest.raises(ValueError, match="AUTH_SESSION_TIMEOUT_SECONDS"):
        SessionTimeoutConfig()
