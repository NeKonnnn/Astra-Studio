# -*- coding: utf-8 -*-
"""Тесты backend.auth.login_lockout: счётчик неудач, блокировка 429, сброс, тела ответов."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.auth import login_lockout as ll  # noqa: E402


def _cfg(enabled=True, max_failed=3, duration=60):
    return SimpleNamespace(
        enabled=enabled, max_failed_attempts=max_failed, lockout_duration_seconds=duration
    )


@pytest.fixture(autouse=True)
def lockout_cfg(monkeypatch):
    """Чистое состояние и управляемая конфигурация (меняется через holder.cfg)."""
    ll._states.clear()
    holder = SimpleNamespace(cfg=_cfg())
    monkeypatch.setattr(ll, "_get_lockout_config", lambda: holder.cfg)
    yield holder
    ll._states.clear()


def _fail(username: str, times: int) -> None:
    for _ in range(times):
        ll.record_failed_login(username)


def _expire_lock(username: str) -> None:
    ll._states[ll._normalize_username(username)].locked_until = datetime.utcnow() - timedelta(seconds=1)


class TestFormatDurationRu:
    @pytest.mark.parametrize(
        "seconds, expected",
        [
            (0, "0 сек."),
            (5, "5 сек."),
            (60, "1 мин."),
            (90, "1 мин. 30 сек."),
            (900, "15 мин."),
            (930, "15 мин. 30 сек."),
            (-10, "0 сек."),
            ("45", "45 сек."),
        ],
    )
    def test_format(self, seconds, expected):
        assert ll.format_duration_ru(seconds) == expected


class TestIsEnabled:
    @pytest.mark.parametrize(
        "cfg, expected",
        [
            (_cfg(), True),
            (_cfg(enabled=False), False),
            (_cfg(max_failed=0), False),
            (_cfg(duration=0), False),
            (_cfg(max_failed=-1), False),
        ],
    )
    def test_enabled(self, lockout_cfg, cfg, expected):
        lockout_cfg.cfg = cfg
        assert ll._is_enabled() is expected


class TestLockoutFlow:
    def test_not_locked_below_limit(self):
        _fail("alice", 2)
        ll.assert_login_not_locked("alice")  # не бросает

    def test_locked_after_limit(self):
        _fail("alice", 3)
        with pytest.raises(HTTPException) as exc:
            ll.assert_login_not_locked("alice")
        assert exc.value.status_code == 429
        assert int(exc.value.headers["Retry-After"]) <= 60
        assert exc.value.detail["retry_after_seconds"] > 0
        assert exc.value.detail["remaining_attempts"] == 0

    def test_username_is_case_and_space_insensitive(self):
        _fail("  Alice ", 3)
        with pytest.raises(HTTPException):
            ll.assert_login_not_locked("ALICE")

    def test_users_are_independent(self):
        _fail("alice", 3)
        ll.assert_login_not_locked("bob")

    def test_failures_while_locked_do_not_extend_counter(self):
        _fail("alice", 3)
        before = ll._states["alice"].locked_until
        _fail("alice", 5)
        assert ll._states["alice"].failed_attempts == 3
        assert ll._states["alice"].locked_until == before

    def test_unlock_after_expiry(self):
        _fail("alice", 3)
        _expire_lock("alice")
        ll.assert_login_not_locked("alice")  # блокировка истекла
        assert ll._states["alice"].failed_attempts == 0
        assert ll._states["alice"].locked_until is None

    def test_counter_restarts_after_expiry(self):
        _fail("alice", 3)
        _expire_lock("alice")
        ll.record_failed_login("alice")
        assert ll._states["alice"].failed_attempts == 1

    def test_successful_login_resets_counter(self):
        _fail("alice", 2)
        ll.record_successful_login("alice")
        assert "alice" not in ll._states
        _fail("alice", 2)
        ll.assert_login_not_locked("alice")

    def test_successful_login_clears_lock(self):
        _fail("alice", 3)
        ll.record_successful_login("alice")
        ll.assert_login_not_locked("alice")

    @pytest.mark.parametrize("name", ["", "   ", None])
    def test_blank_username_ignored(self, name):
        ll.record_failed_login(name)
        ll.assert_login_not_locked(name)
        ll.record_successful_login(name)
        assert ll._states == {}

    def test_disabled_never_locks(self, lockout_cfg):
        lockout_cfg.cfg = _cfg(enabled=False)
        _fail("alice", 50)
        ll.assert_login_not_locked("alice")
        assert ll._states == {}


class TestPolicyAndAttemptInfo:
    def test_policy(self):
        assert ll.get_lockout_policy() == {
            "enabled": True,
            "max_failed_attempts": 3,
            "lockout_duration_seconds": 60,
        }

    def test_policy_disabled(self, lockout_cfg):
        lockout_cfg.cfg = _cfg(enabled=False)
        assert ll.get_lockout_policy()["enabled"] is False

    def test_remaining_attempts_decrease(self):
        assert ll.get_attempt_info("alice")["remaining_attempts"] == 3
        ll.record_failed_login("alice")
        assert ll.get_attempt_info("alice")["remaining_attempts"] == 2
        _fail("alice", 5)
        assert ll.get_attempt_info("alice")["remaining_attempts"] == 0

    def test_attempt_info_disabled(self, lockout_cfg):
        lockout_cfg.cfg = _cfg(enabled=False)
        assert ll.get_attempt_info("alice")["remaining_attempts"] is None

    def test_attempt_info_blank_user(self):
        assert ll.get_attempt_info("")["remaining_attempts"] == 3


class TestBuildDetails:
    def test_login_failed_detail_plain(self):
        detail = ll.build_login_failed_detail("alice")
        assert detail["message"] == "Неверное имя пользователя или пароль"
        assert detail["remaining_attempts"] == 3
        assert detail["max_failed_attempts"] == 3

    def test_login_failed_detail_warns_before_lock(self):
        # remaining_attempts == 0 достигается, когда счётчик дошёл до лимита без блокировки.
        ll._get_state("alice").failed_attempts = 3
        detail = ll.build_login_failed_detail("alice")
        assert "заблокирует вход на 1 мин." in detail["message"]

    def test_lockout_detail_uses_remaining_time(self):
        _fail("alice", 3)
        detail = ll.build_lockout_detail("alice")
        assert 0 < detail["retry_after_seconds"] <= 60
        assert "Слишком много неудачных попыток" in detail["message"]

    def test_lockout_detail_falls_back_to_policy_duration(self):
        detail = ll.build_lockout_detail("never-locked")
        assert detail["retry_after_seconds"] == 60

    def test_lockout_detail_blank_user(self):
        assert ll.build_lockout_detail("")["retry_after_seconds"] == 60


class TestCefExtra:
    @pytest.fixture(autouse=True)
    def _suffix(self, monkeypatch):
        import backend.settings.cef_logger.cef_logger as cef

        monkeypatch.setattr(cef, "ldap_reason_suffix", lambda: "")

    def test_disabled_invalid(self, lockout_cfg):
        lockout_cfg.cfg = _cfg(enabled=False)
        assert ll.build_cef_sec003_extra("alice", account_locked=False) == {
            "reason": "Invalid username/password"
        }

    def test_disabled_locked(self, lockout_cfg):
        lockout_cfg.cfg = _cfg(enabled=False)
        assert ll.build_cef_sec003_extra("alice", account_locked=True) == {
            "reason": "Account temporarily locked"
        }

    def test_invalid_with_remaining(self):
        ll.record_failed_login("alice")
        extra = ll.build_cef_sec003_extra("alice", account_locked=False)
        assert extra["cn1"] == 2
        assert extra["cn1Label"] == "RemainingLoginAttempts"
        assert extra["cn2"] == 60
        assert "remaining attempts 2/3" in extra["reason"]

    def test_locked(self):
        _fail("alice", 3)
        extra = ll.build_cef_sec003_extra("alice", account_locked=True)
        assert extra["cn1"] == 0
        assert 0 < extra["cn2"] <= 60
        assert extra["cn2Label"] == "LockoutDurationSeconds"
        assert "Account temporarily locked: retry after" in extra["reason"]
