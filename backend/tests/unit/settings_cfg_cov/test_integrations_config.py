"""IntegrationsConfig ENV overrides."""

from types import SimpleNamespace

import pytest

from backend.settings import config as cfg
from backend.settings.integrations_models import IntegrationsConfig, OAuthAppConfig

pytestmark = pytest.mark.unit


class TestIntegrationsConfigEnv:
    def test_defaults(self, monkeypatch):
        for key in list(__import__("os").environ):
            if "INTEGRATION" in key or key.startswith(
                ("YANDEX_", "GOOGLE_", "GMAIL_", "MICROSOFT_", "ONEDRIVE_", "OUTLOOK_", "NOTION_", "GITHUB_")
            ):
                monkeypatch.delenv(key, raising=False)

        c = IntegrationsConfig()
        assert c.yandex_disk.enable is False
        assert c.google_drive.enable is False

    def test_all_flags_true(self, monkeypatch):
        monkeypatch.setenv("ENABLE_YANDEX_DISK_INTEGRATION", "true")
        monkeypatch.setenv("ENABLE_YANDEX_MAIL_INTEGRATION", "1")
        monkeypatch.setenv("ENABLE_GOOGLE_DRIVE_INTEGRATION", "yes")
        monkeypatch.setenv("ENABLE_GMAIL_INTEGRATION", "on")
        monkeypatch.setenv("ENABLE_ONEDRIVE_INTEGRATION", "true")
        monkeypatch.setenv("ENABLE_OUTLOOK_INTEGRATION", "true")
        monkeypatch.setenv("ENABLE_NOTION_INTEGRATION", "true")
        monkeypatch.setenv("ENABLE_GITHUB_INTEGRATION", "true")
        monkeypatch.setenv("YANDEX_OAUTH_CLIENT_ID", "ya-1")
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "g-1")
        monkeypatch.setenv("GOOGLE_API_KEY", "key-1")
        monkeypatch.setenv("MICROSOFT_CLIENT_ID", "ms-1")
        monkeypatch.setenv("NOTION_CLIENT_ID", "n-1")
        monkeypatch.setenv("GITHUB_CLIENT_ID", "gh-1")

        c = IntegrationsConfig()
        assert c.yandex_disk.enable is True
        assert c.yandex_disk.client_id == "ya-1"
        assert c.yandex_mail.client_id == "ya-1"
        assert c.google_drive.client_id == "g-1"
        assert c.google_drive.api_key == "key-1"
        assert c.gmail.client_id == "g-1"
        assert c.onedrive.client_id == "ms-1"
        assert c.outlook.client_id == "ms-1"
        assert c.notion.client_id == "n-1"
        assert c.github.client_id == "gh-1"


class TestIntegrationsPublicBuilder:
    def test_features_and_configured(self, monkeypatch):
        monkeypatch.delenv("ENABLE_YANDEX_DISK_INTEGRATION", raising=False)
        monkeypatch.delenv("YANDEX_DISK_CLIENT_ID", raising=False)
        monkeypatch.delenv("YANDEX_OAUTH_CLIENT_ID", raising=False)

        from backend.routes import integrations as integrations_routes

        integrations_routes.settings = SimpleNamespace(
            integrations=IntegrationsConfig(
                yandex_disk=OAuthAppConfig(enable=True, client_id=""),
                google_drive=OAuthAppConfig(enable=True, client_id="g", api_key="k"),
            )
        )
        # Force model without env re-load: construct leaf configs directly
        integ = IntegrationsConfig.model_construct(
            yandex_disk=OAuthAppConfig.model_construct(enable=True, client_id="", api_key="", client_secret="", redirect_uri=""),
            yandex_mail=OAuthAppConfig.model_construct(enable=True, client_id="ya", api_key="", client_secret="", redirect_uri=""),
            yandex_calendar=OAuthAppConfig.model_construct(enable=False, client_id="", api_key="", client_secret="", redirect_uri=""),
            yandex_meetings=OAuthAppConfig.model_construct(enable=False, client_id="", api_key="", client_secret="", redirect_uri=""),
            google_drive=OAuthAppConfig.model_construct(enable=True, client_id="g", api_key="k", client_secret="", redirect_uri=""),
            gmail=OAuthAppConfig.model_construct(enable=True, client_id="g", api_key="", client_secret="", redirect_uri=""),
            onedrive=OAuthAppConfig.model_construct(enable=True, client_id="ms", api_key="", client_secret="", redirect_uri=""),
            outlook=OAuthAppConfig.model_construct(enable=True, client_id="ms", api_key="", client_secret="", redirect_uri=""),
            notion=OAuthAppConfig.model_construct(enable=True, client_id="n", api_key="", client_secret="s", redirect_uri=""),
            github=OAuthAppConfig.model_construct(enable=True, client_id="gh", api_key="", client_secret="s", redirect_uri=""),
        )
        integrations_routes.settings = SimpleNamespace(integrations=integ)
        body = integrations_routes.build_integrations_public_config()
        assert body["features"]["enable_yandex_disk_integration"] is True
        assert body["yandex_disk"]["configured"] is False
        assert body["google_drive"]["configured"] is True
        assert body["google_drive"]["api_key"] == "k"
        assert body["github"]["configured"] is True
        assert "client_secret" not in body["github"]
