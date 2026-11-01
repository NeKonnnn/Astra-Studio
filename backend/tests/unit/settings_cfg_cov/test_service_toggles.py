"""settings/service_toggles: фича-флаги микросервисов (ENV > YAML > False) и зависимости."""

import logging
from types import SimpleNamespace

import pytest

from backend.settings import service_toggles as st

pytestmark = pytest.mark.unit

_ALL_ENVS = [env for env, _ in st._SERVICES.values()]


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """Чистим ENV флагов и сбрасываем флаг «уже залогировано»."""
    for name in _ALL_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("CUSTOM_SERVICE_ENABLED", raising=False)
    monkeypatch.setattr(st, "_logged_once", False)


def _use_settings(monkeypatch, **flags):
    ms = SimpleNamespace(**flags)
    monkeypatch.setattr(
        st, "get_settings", lambda: SimpleNamespace(microservices_enabled=ms, mcp=None)
    )


class TestEnvBool:
    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_missing_or_blank_is_none(self, monkeypatch, value):
        if value is None:
            monkeypatch.delenv("X_FLAG", raising=False)
        else:
            monkeypatch.setenv("X_FLAG", value)

        assert st._env_bool("X_FLAG") is None

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("1", True),
            ("TRUE", True),
            (" yes ", True),
            ("On", True),
            ("0", False),
            ("off", False),
            ("nope", False),
        ],
    )
    def test_parses_truthy_values(self, monkeypatch, value, expected):
        monkeypatch.setenv("X_FLAG", value)

        assert st._env_bool("X_FLAG") is expected


class TestOwnEnabled:
    def test_mcp_reads_settings_mcp_enabled(self, monkeypatch):
        monkeypatch.setattr(
            st, "get_settings", lambda: SimpleNamespace(mcp=SimpleNamespace(enabled=True))
        )

        assert st._own_enabled("mcp") is True

    def test_mcp_without_block_is_disabled(self, monkeypatch):
        monkeypatch.setattr(st, "get_settings", lambda: SimpleNamespace(mcp=None))

        assert st._own_enabled("mcp") is False

    def test_mcp_settings_failure_is_swallowed(self, monkeypatch):
        def boom():
            raise RuntimeError("settings broken")

        monkeypatch.setattr(st, "get_settings", boom)

        assert st._own_enabled("mcp") is False

    def test_env_wins_over_yaml(self, monkeypatch):
        _use_settings(monkeypatch, stt=True)
        monkeypatch.setenv("STT_SERVICE_ENABLED", "false")

        assert st._own_enabled("stt") is False

    def test_yaml_value_used_without_env(self, monkeypatch):
        _use_settings(monkeypatch, tts=True)

        assert st._own_enabled("tts") is True

    def test_non_bool_yaml_value_means_disabled(self, monkeypatch):
        _use_settings(monkeypatch, tts="yes")

        assert st._own_enabled("tts") is False

    def test_missing_yaml_block_means_disabled(self, monkeypatch):
        monkeypatch.setattr(
            st, "get_settings", lambda: SimpleNamespace(microservices_enabled=None)
        )

        assert st._own_enabled("stt") is False

    def test_settings_error_means_disabled(self, monkeypatch):
        def boom():
            raise RuntimeError("no settings")

        monkeypatch.setattr(st, "get_settings", boom)

        assert st._own_enabled("stt") is False

    def test_unknown_key_uses_derived_env_name(self, monkeypatch):
        _use_settings(monkeypatch)
        monkeypatch.setenv("CUSTOM_SERVICE_ENABLED", "1")

        assert st._own_enabled("custom") is True


class TestIsServiceEnabled:
    def test_disabled_own_flag(self, monkeypatch):
        _use_settings(monkeypatch)

        assert st.is_service_enabled("stt") is False

    def test_dependency_off_disables_service(self, monkeypatch):
        _use_settings(monkeypatch, rag=True, postgresql=False)

        assert st.is_service_enabled("rag") is False

    def test_dependency_on_enables_service(self, monkeypatch):
        _use_settings(monkeypatch, rag=True, postgresql=True)

        assert st.is_service_enabled("rag") is True


class TestDisplayAndErrors:
    def test_display_name_known_and_unknown(self):
        assert st.display_name("stt") == "STT (распознавание речи)"
        assert st.display_name("zzz") == "zzz"

    def test_service_disabled_error_carries_key_and_message(self):
        err = st.ServiceDisabledError("tts")

        assert err.key == "tts"
        assert err.display == "TTS (синтез речи)"
        assert "TTS (синтез речи)" in str(err)
        assert "в разработке" in str(err)

    def test_require_service_raises_when_disabled(self, monkeypatch):
        _use_settings(monkeypatch)

        with pytest.raises(st.ServiceDisabledError) as exc:
            st.require_service("stt")

        assert exc.value.key == "stt"

    def test_require_service_passes_when_enabled(self, monkeypatch):
        _use_settings(monkeypatch, stt=True)

        assert st.require_service("stt") is None


class TestLogDisabledServicesOnce:
    def test_logs_reason_for_each_disabled_service(self, monkeypatch):
        _use_settings(monkeypatch, stt=True, rag=True, postgresql=False, agents=True)
        messages = []
        monkeypatch.setattr(
            st.logger,
            "info",
            lambda fmt, *args, **kw: messages.append(fmt % args if args else fmt),
        )

        st.log_disabled_services_once()

        text = "\n".join(messages)
        assert "TTS_SERVICE_ENABLED=false" in text
        assert "выключена зависимость: POSTGRESQL_SERVICE_ENABLED=false" in text
        assert "STT_SERVICE_ENABLED=false" not in text
        assert "RAG отключён" in text

    def test_second_call_is_noop(self, monkeypatch):
        _use_settings(monkeypatch)
        st.log_disabled_services_once()
        messages = []
        monkeypatch.setattr(st.logger, "info", lambda *a, **k: messages.append(a))

        st.log_disabled_services_once()

        assert messages == []
