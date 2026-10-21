"""auth/login_lockout: блокировка входа после серии неудачных попыток."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from freezegun import freeze_time

pytestmark = pytest.mark.unit


@pytest.fixture
def lockout(monkeypatch):
    from backend.auth import login_lockout

    login_lockout._states.clear()
    monkeypatch.setattr(login_lockout, "_config_logged", True)
    cfg = SimpleNamespace(enabled=True, max_failed_attempts=3, lockout_duration_seconds=120)
    monkeypatch.setattr(login_lockout, "_get_lockout_config", lambda: cfg)
    login_lockout.cfg = cfg
    yield login_lockout
    login_lockout._states.clear()


def _fail(lockout, username="alice", times=1):
    for _ in range(times):
        lockout.record_failed_login(username)


class TestFormatDuration:
    @pytest.mark.parametrize(
        ("seconds", "expected"),
        [
            (0, "0 сек."),
            (45, "45 сек."),
            (60, "1 мин."),
            (900, "15 мин."),
            (930, "15 мин. 30 сек."),
            (-5, "0 сек."),
        ],
    )
    def test_format(self, lockout, seconds, expected):
        assert lockout.format_duration_ru(seconds) == expected


class TestEnabledFlag:
    def test_enabled_with_positive_limits(self, lockout):
        assert lockout._is_enabled() is True

    @pytest.mark.parametrize(
        "override",
        [
            {"enabled": False},
            {"max_failed_attempts": 0},
            {"lockout_duration_seconds": 0},
        ],
    )
    def test_disabled_when_any_limit_off(self, lockout, override):
        for key, value in override.items():
            setattr(lockout.cfg, key, value)

        assert lockout._is_enabled() is False

    def test_disabled_policy_never_locks(self, lockout):
        lockout.cfg.enabled = False

        _fail(lockout, times=50)
        lockout.assert_login_not_locked("alice")

        assert lockout._states == {}


class TestLockFlow:
    @freeze_time("2026-01-01 12:00:00")
    def test_locks_after_max_failed_attempts(self, lockout):
        _fail(lockout, times=2)
        lockout.assert_login_not_locked("alice")

        _fail(lockout)

        with pytest.raises(HTTPException) as exc:
            lockout.assert_login_not_locked("alice")
        assert exc.value.status_code == 429
        assert exc.value.headers["Retry-After"] == "120"
        assert exc.value.detail["retry_after_seconds"] == 120
        assert exc.value.detail["remaining_attempts"] == 0

    def test_username_is_case_and_space_insensitive(self, lockout):
        lockout.record_failed_login("Alice")
        lockout.record_failed_login(" ALICE ")
        lockout.record_failed_login("alice")

        with pytest.raises(HTTPException):
            lockout.assert_login_not_locked("aLiCe")

    def test_other_users_are_not_affected(self, lockout):
        _fail(lockout, "alice", 3)

        lockout.assert_login_not_locked("bob")

    def test_blank_username_is_ignored(self, lockout):
        _fail(lockout, "  ", 10)
        lockout.assert_login_not_locked("")

        assert lockout._states == {}

    def test_failures_during_lock_do_not_extend_it(self, lockout):
        with freeze_time("2026-01-01 12:00:00") as clock:
            _fail(lockout, times=3)
            clock.tick(60)
            _fail(lockout, times=5)

            with pytest.raises(HTTPException) as exc:
                lockout.assert_login_not_locked("alice")

        assert exc.value.detail["retry_after_seconds"] == 60

    def test_lock_expires_and_counter_resets(self, lockout):
        with freeze_time("2026-01-01 12:00:00") as clock:
            _fail(lockout, times=3)
            clock.tick(121)

            lockout.assert_login_not_locked("alice")
            info = lockout.get_attempt_info("alice")

        assert info["remaining_attempts"] == 3

    def test_success_clears_counter(self, lockout):
        _fail(lockout, times=2)

        lockout.record_successful_login("Alice")

        assert lockout.get_attempt_info("alice")["remaining_attempts"] == 3

    def test_success_with_blank_username_is_noop(self, lockout):
        lockout.record_successful_login("")


class TestInfoAndDetails:
    def test_attempt_info_counts_down(self, lockout):
        assert lockout.get_attempt_info("alice")["remaining_attempts"] == 3
        _fail(lockout)
        assert lockout.get_attempt_info("alice")["remaining_attempts"] == 2

    def test_attempt_info_for_blank_user(self, lockout):
        assert lockout.get_attempt_info("")["remaining_attempts"] == 3

    def test_attempt_info_when_disabled(self, lockout):
        lockout.cfg.enabled = False

        info = lockout.get_attempt_info("alice")

        assert info["enabled"] is False
        assert info["remaining_attempts"] is None

    def test_policy_exposes_limits(self, lockout):
        assert lockout.get_lockout_policy() == {
            "enabled": True,
            "max_failed_attempts": 3,
            "lockout_duration_seconds": 120,
        }

    def test_failed_detail_reports_remaining_attempts(self, lockout):
        detail = lockout.build_login_failed_detail("bob")
        assert detail["message"] == "Неверное имя пользователя или пароль"
        assert detail["remaining_attempts"] == 3

        _fail(lockout, "carol", 2)
        detail = lockout.build_login_failed_detail("carol")
        assert detail["remaining_attempts"] == 1

    def test_failed_detail_announces_lock_on_zero_remaining(self, lockout):
        lockout.cfg.max_failed_attempts = 1
        _fail(lockout, "dave")
        lockout._states["dave"].locked_until = None  # лимит исчерпан, блока ещё нет

        detail = lockout.build_login_failed_detail("dave")

        assert "заблокирует вход на 2 мин." in detail["message"]
        assert detail["remaining_attempts"] == 0

    def test_lockout_detail_falls_back_to_full_duration(self, lockout):
        detail = lockout.build_lockout_detail("nobody")

        assert detail["retry_after_seconds"] == 120
        assert "2 мин." in detail["message"]


class TestCefExtra:
    @pytest.fixture(autouse=True)
    def _no_ldap_suffix(self, monkeypatch):
        from backend.settings.cef_logger import cef_logger

        monkeypatch.setattr(cef_logger, "ldap_reason_suffix", lambda: "")

    def test_invalid_credentials_reports_remaining(self, lockout):
        _fail(lockout)

        extra = lockout.build_cef_sec003_extra("alice", account_locked=False)

        assert extra["cn1"] == 2
        assert extra["cn2"] == 120
        assert "remaining attempts 2/3" in extra["reason"]

    def test_locked_account_reports_retry(self, lockout):
        with freeze_time("2026-01-01 12:00:00"):
            _fail(lockout, times=3)

            extra = lockout.build_cef_sec003_extra("alice", account_locked=True)

        assert extra["cn1"] == 0
        assert extra["cn2"] == 120
        assert extra["reason"].startswith("Account temporarily locked: retry after 120s")

    def test_disabled_policy_has_plain_reasons(self, lockout):
        lockout.cfg.enabled = False

        locked = lockout.build_cef_sec003_extra("a", account_locked=True)
        invalid = lockout.build_cef_sec003_extra("a", account_locked=False)

        assert locked == {
            "reason": "Account temporarily locked",
            "auth_method": "ldap",
        }
        assert invalid["reason"] == "Invalid username/password"


class TestConfigModel:
    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        for name in (
            "AUTH_LOGIN_LOCKOUT_ENABLED",
            "AUTH_LOGIN_LOCKOUT_MAX_FAILED_ATTEMPTS",
            "AUTH_LOGIN_LOCKOUT_DURATION_SECONDS",
        ):
            monkeypatch.delenv(name, raising=False)

    def test_defaults(self):
        from backend.settings.config import LoginLockoutConfig

        cfg = LoginLockoutConfig()

        assert (cfg.enabled, cfg.max_failed_attempts, cfg.lockout_duration_seconds) == (
            True,
            5,
            900,
        )

    def test_minutes_converted_to_seconds(self):
        from backend.settings.config import LoginLockoutConfig

        cfg = LoginLockoutConfig(lockout_duration_minutes=10)

        assert cfg.lockout_duration_seconds == 600

    def test_explicit_seconds_win_over_minutes(self):
        from backend.settings.config import LoginLockoutConfig

        cfg = LoginLockoutConfig(lockout_duration_seconds=30, lockout_duration_minutes=10)

        assert cfg.lockout_duration_seconds == 30

    def test_env_overrides_yaml(self, monkeypatch):
        from backend.settings.config import LoginLockoutConfig

        monkeypatch.setenv("AUTH_LOGIN_LOCKOUT_ENABLED", "false")
        monkeypatch.setenv("AUTH_LOGIN_LOCKOUT_MAX_FAILED_ATTEMPTS", "7")
        monkeypatch.setenv("AUTH_LOGIN_LOCKOUT_DURATION_SECONDS", "45")

        cfg = LoginLockoutConfig(enabled=True, max_failed_attempts=1)

        assert (cfg.enabled, cfg.max_failed_attempts, cfg.lockout_duration_seconds) == (
            False,
            7,
            45,
        )
