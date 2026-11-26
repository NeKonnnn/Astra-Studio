"""Модели внешних интеграций (file pickers / connectors)."""

from __future__ import annotations

import os
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator


def _truthy(raw: Optional[str]) -> Optional[bool]:
    if raw is None:
        return None
    return raw.strip().lower() in ("1", "true", "yes", "on")


class OAuthAppConfig(BaseModel):
    """OAuth-приложение провайдера (секреты не отдаём на фронт)."""

    enable: bool = False
    client_id: str = ""
    client_secret: str = ""
    api_key: str = ""
    redirect_uri: str = ""

    @classmethod
    def from_env(
        cls,
        data: Any,
        *,
        enable_env: str,
        client_id_envs: tuple[str, ...],
        client_secret_envs: tuple[str, ...] = (),
        api_key_envs: tuple[str, ...] = (),
        redirect_envs: tuple[str, ...] = (),
    ) -> dict:
        if not isinstance(data, dict):
            data = {}
        flag = _truthy(os.getenv(enable_env))
        if flag is not None:
            data["enable"] = flag
        for key in client_id_envs:
            val = os.getenv(key)
            if val is not None and val.strip():
                data["client_id"] = val.strip()
                break
        for key in client_secret_envs:
            val = os.getenv(key)
            if val is not None and val.strip():
                data["client_secret"] = val.strip()
                break
        for key in api_key_envs:
            val = os.getenv(key)
            if val is not None and val.strip():
                data["api_key"] = val.strip()
                break
        for key in redirect_envs:
            val = os.getenv(key)
            if val is not None:
                data["redirect_uri"] = val.strip()
                break
        return data

    def public_dict(self) -> dict:
        cid = str(self.client_id or "").strip()
        return {
            "client_id": cid,
            "api_key": str(self.api_key or "").strip(),
            "redirect_uri": str(self.redirect_uri or "").strip(),
            "configured": bool(cid),
        }


class IntegrationsConfig(BaseModel):
    """Все внешние интеграции чата."""

    yandex_disk: OAuthAppConfig = Field(default_factory=OAuthAppConfig)
    yandex_mail: OAuthAppConfig = Field(default_factory=OAuthAppConfig)
    yandex_calendar: OAuthAppConfig = Field(default_factory=OAuthAppConfig)
    yandex_meetings: OAuthAppConfig = Field(default_factory=OAuthAppConfig)
    google_drive: OAuthAppConfig = Field(default_factory=OAuthAppConfig)
    gmail: OAuthAppConfig = Field(default_factory=OAuthAppConfig)
    onedrive: OAuthAppConfig = Field(default_factory=OAuthAppConfig)
    outlook: OAuthAppConfig = Field(default_factory=OAuthAppConfig)
    notion: OAuthAppConfig = Field(default_factory=OAuthAppConfig)
    github: OAuthAppConfig = Field(default_factory=OAuthAppConfig)

    @model_validator(mode="before")
    @classmethod
    def load_all_from_env(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            data = {}

        def sub(key: str, **kwargs: Any) -> None:
            data[key] = OAuthAppConfig.from_env(data.get(key), **kwargs)

        yandex_ids = ("YANDEX_DISK_CLIENT_ID", "YANDEX_OAUTH_CLIENT_ID", "YANDEX_CLIENT_ID")
        yandex_redirect = ("YANDEX_DISK_REDIRECT_URI", "YANDEX_OAUTH_REDIRECT_URI")
        google_ids = ("GOOGLE_DRIVE_CLIENT_ID", "GOOGLE_CLIENT_ID", "GMAIL_CLIENT_ID")
        google_keys = ("GOOGLE_DRIVE_API_KEY", "GOOGLE_API_KEY")
        ms_ids = ("ONEDRIVE_CLIENT_ID", "MICROSOFT_CLIENT_ID", "OUTLOOK_CLIENT_ID")

        sub(
            "yandex_disk",
            enable_env="ENABLE_YANDEX_DISK_INTEGRATION",
            client_id_envs=yandex_ids,
            redirect_envs=yandex_redirect,
        )
        sub(
            "yandex_mail",
            enable_env="ENABLE_YANDEX_MAIL_INTEGRATION",
            client_id_envs=("YANDEX_MAIL_CLIENT_ID",) + yandex_ids,
            redirect_envs=("YANDEX_MAIL_REDIRECT_URI",) + yandex_redirect,
        )
        sub(
            "yandex_calendar",
            enable_env="ENABLE_YANDEX_CALENDAR_INTEGRATION",
            client_id_envs=("YANDEX_CALENDAR_CLIENT_ID",) + yandex_ids,
            redirect_envs=("YANDEX_CALENDAR_REDIRECT_URI",) + yandex_redirect,
        )
        sub(
            "yandex_meetings",
            enable_env="ENABLE_YANDEX_MEETINGS_INTEGRATION",
            client_id_envs=("YANDEX_MEETINGS_CLIENT_ID",) + yandex_ids,
            redirect_envs=("YANDEX_MEETINGS_REDIRECT_URI",) + yandex_redirect,
        )
        sub(
            "google_drive",
            enable_env="ENABLE_GOOGLE_DRIVE_INTEGRATION",
            client_id_envs=google_ids,
            api_key_envs=google_keys,
            redirect_envs=("GOOGLE_DRIVE_REDIRECT_URI", "GOOGLE_REDIRECT_URI"),
        )
        sub(
            "gmail",
            enable_env="ENABLE_GMAIL_INTEGRATION",
            client_id_envs=("GMAIL_CLIENT_ID",) + google_ids,
            redirect_envs=("GMAIL_REDIRECT_URI", "GOOGLE_REDIRECT_URI"),
        )
        sub(
            "onedrive",
            enable_env="ENABLE_ONEDRIVE_INTEGRATION",
            client_id_envs=ms_ids,
            redirect_envs=("ONEDRIVE_REDIRECT_URI", "MICROSOFT_REDIRECT_URI"),
        )
        sub(
            "outlook",
            enable_env="ENABLE_OUTLOOK_INTEGRATION",
            client_id_envs=("OUTLOOK_CLIENT_ID",) + ms_ids,
            redirect_envs=("OUTLOOK_REDIRECT_URI", "MICROSOFT_REDIRECT_URI"),
        )
        sub(
            "notion",
            enable_env="ENABLE_NOTION_INTEGRATION",
            client_id_envs=("NOTION_CLIENT_ID",),
            client_secret_envs=("NOTION_CLIENT_SECRET",),
            redirect_envs=("NOTION_REDIRECT_URI",),
        )
        sub(
            "github",
            enable_env="ENABLE_GITHUB_INTEGRATION",
            client_id_envs=("GITHUB_CLIENT_ID",),
            client_secret_envs=("GITHUB_CLIENT_SECRET",),
            redirect_envs=("GITHUB_REDIRECT_URI",),
        )
        return data
