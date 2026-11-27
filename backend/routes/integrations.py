"""
Публичный конфиг внешних интеграций + OAuth code exchange (Notion/GitHub).

Фронт читает client_id / feature flags и сам делает OAuth/picker.
Для Notion/GitHub нужен client_secret — обмен code→token на backend.
"""

from __future__ import annotations

from typing import Annotated, Any, Dict, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.app_state import settings
from backend.auth.jwt_handler import get_current_user, get_optional_user
from backend.settings.integrations_models import OAuthAppConfig
from backend.settings.logging import get_logger

router = APIRouter(tags=["integrations"])
logger = get_logger(__name__)

_PROVIDER_ATTR = {
    "yandex_disk": "yandex_disk",
    "yandex-disk": "yandex_disk",
    "yandex_mail": "yandex_mail",
    "yandex-mail": "yandex_mail",
    "yandex_calendar": "yandex_calendar",
    "yandex-calendar": "yandex_calendar",
    "yandex_meetings": "yandex_meetings",
    "yandex-meetings": "yandex_meetings",
    "google_drive": "google_drive",
    "google-drive": "google_drive",
    "gmail": "gmail",
    "onedrive": "onedrive",
    "outlook": "outlook",
    "notion": "notion",
    "github": "github",
}


def _get_provider(name: str) -> OAuthAppConfig:
    attr = _PROVIDER_ATTR.get(str(name or "").strip().lower())
    if not attr:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {name}")
    return getattr(settings.integrations, attr)


def build_integrations_public_config() -> Dict[str, Any]:
    """Сериализация публичных полей (без client_secret)."""
    integ = settings.integrations
    features: Dict[str, bool] = {}
    body: Dict[str, Any] = {"features": features}

    mapping = {
        "yandex_disk": ("enable_yandex_disk_integration", integ.yandex_disk),
        "yandex_mail": ("enable_yandex_mail_integration", integ.yandex_mail),
        "yandex_calendar": ("enable_yandex_calendar_integration", integ.yandex_calendar),
        "yandex_meetings": ("enable_yandex_meetings_integration", integ.yandex_meetings),
        "google_drive": ("enable_google_drive_integration", integ.google_drive),
        "gmail": ("enable_gmail_integration", integ.gmail),
        "onedrive": ("enable_onedrive_integration", integ.onedrive),
        "outlook": ("enable_outlook_integration", integ.outlook),
        "notion": ("enable_notion_integration", integ.notion),
        "github": ("enable_github_integration", integ.github),
    }
    for key, (feat, cfg) in mapping.items():
        features[feat] = bool(cfg.enable)
        body[key] = cfg.public_dict()
    return body


@router.get("/api/config/integrations")
async def get_integrations_config(
    _user: Annotated[Optional[dict], Depends(get_optional_user)],
):
    return build_integrations_public_config()


class OAuthExchangeRequest(BaseModel):
    provider: str
    code: str
    redirect_uri: str = ""


@router.post("/api/integrations/oauth/exchange")
async def exchange_oauth_code(
    body: OAuthExchangeRequest,
    _user: Annotated[dict, Depends(get_current_user)],
):
    """Обмен authorization code → access_token (Notion, GitHub)."""
    provider = body.provider.strip().lower().replace("-", "_")
    cfg = _get_provider(provider)
    if not cfg.enable:
        raise HTTPException(status_code=400, detail="Integration disabled")
    if not cfg.client_id or not cfg.client_secret:
        raise HTTPException(status_code=400, detail="client_id/secret not configured")

    redirect_uri = (body.redirect_uri or cfg.redirect_uri or "").strip()
    if provider == "github":
        token_url = "https://github.com/login/oauth/access_token"
        payload = {
            "client_id": cfg.client_id,
            "client_secret": cfg.client_secret,
            "code": body.code,
            "redirect_uri": redirect_uri,
        }
        headers = {"Accept": "application/json"}
    elif provider == "notion":
        token_url = "https://api.notion.com/v1/oauth/token"
        payload = {
            "grant_type": "authorization_code",
            "code": body.code,
            "redirect_uri": redirect_uri,
        }
        headers = {"Accept": "application/json"}
        auth = (cfg.client_id, cfg.client_secret)
    else:
        raise HTTPException(status_code=400, detail="Provider does not use code exchange")

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            if provider == "notion":
                resp = await client.post(
                    token_url, json=payload, headers=headers, auth=auth
                )
            else:
                resp = await client.post(token_url, data=payload, headers=headers)
            data = resp.json() if resp.content else {}
            if resp.status_code >= 400:
                logger.warning("OAuth exchange failed provider=%s status=%s", provider, resp.status_code)
                raise HTTPException(
                    status_code=400,
                    detail=data.get("error_description")
                    or data.get("error")
                    or f"OAuth exchange failed ({resp.status_code})",
                )
            token = data.get("access_token")
            if not token:
                raise HTTPException(status_code=400, detail="No access_token in response")
            return {
                "access_token": token,
                "token_type": data.get("token_type") or "bearer",
                "scope": data.get("scope"),
                "workspace_name": data.get("workspace_name"),
            }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("OAuth exchange error")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


class ProxyRequest(BaseModel):
    method: str = "GET"
    url: str
    token: str = Field(..., min_length=1)
    token_type: str = "Bearer"
    json_body: Optional[Dict[str, Any]] = None


@router.post("/api/integrations/proxy")
async def integrations_proxy(
    body: ProxyRequest,
    _user: Annotated[dict, Depends(get_current_user)],
):
    """
    CORS-прокси для API провайдеров (Яндекс Почта/Календарь и т.п.).
    Токен передаёт клиент; backend только форвардит.
    """
    url = str(body.url or "").strip()
    allowed_prefixes = (
        "https://cloud-api.yandex.net/",
        "https://api.passport.yandex.net/",
        "https://caldav.yandex.ru/",
        "https://api.calendar.yandex.net/",
        "https://mail.yandex.ru/",
        "https://cloud-api.yandex.net",
        "https://login.yandex.ru/",
        "https://api.github.com/",
        "https://api.notion.com/",
        "https://gmail.googleapis.com/",
        "https://www.googleapis.com/",
        "https://graph.microsoft.com/",
    )
    if not any(url.startswith(p) for p in allowed_prefixes):
        raise HTTPException(status_code=400, detail="URL host not allowed")

    method = body.method.upper()
    if method not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        raise HTTPException(status_code=400, detail="Method not allowed")

    auth_type = (body.token_type or "Bearer").strip()
    if auth_type.lower() == "oauth":
        auth_header = f"OAuth {body.token}"
    else:
        auth_header = f"{auth_type} {body.token}"

    headers = {
        "Authorization": auth_header,
        "Accept": "application/json",
    }
    if "api.notion.com" in url:
        headers["Notion-Version"] = "2022-06-28"

    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            resp = await client.request(
                method, url, headers=headers, json=body.json_body
            )
            content_type = resp.headers.get("content-type", "")
            if "application/json" in content_type:
                try:
                    payload: Any = resp.json()
                except Exception:
                    payload = {"raw": resp.text}
            else:
                payload = {"raw": resp.text[:200_000], "content_type": content_type}
            return {
                "status": resp.status_code,
                "ok": resp.is_success,
                "data": payload,
            }
    except Exception as exc:
        logger.exception("integrations proxy error")
        raise HTTPException(status_code=502, detail=str(exc)) from exc
