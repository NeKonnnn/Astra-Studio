"""
API routes для SSO (Keycloak OIDC).
"""

from __future__ import annotations

from typing import Optional
from urllib.parse import quote, urlencode

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from backend.auth.jwt_handler import set_client_binding_cookie
from backend.auth.keycloak_auth import (
    AUTH_METHOD_KEYCLOAK,
    build_authorization_url,
    create_oauth_login_state,
    exchange_authorization_code,
    get_keycloak_settings,
    is_keycloak_enabled,
    pop_oauth_state,
    pop_sso_ticket,
    resolve_frontend_callback_url,
    resolve_keycloak_redirect_uri,
    store_sso_ticket,
)
from backend.auth.login_helpers import complete_application_login
from backend.auth.sso_logging import (
    KeycloakSsoError,
    log_keycloak_sso_failure,
    record_keycloak_sso_error,
)
from backend.settings.logging import get_logger
from backend.utils.safe_paths import sanitize_log_value

logger = get_logger(__name__)

router = APIRouter(prefix="/api/auth/sso", tags=["SSO Authentication"])


class SsoProvidersResponse(BaseModel):
    keycloak_enabled: bool
    keycloak_login_url: Optional[str] = None


class SsoExchangeRequest(BaseModel):
    ticket: str


def _secure_redirect(url: str) -> RedirectResponse:
    response = RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _redirect_sso_error(frontend_callback: str, detail: str) -> RedirectResponse:
    query = urlencode({"sso_error": detail})
    return _secure_redirect(f"{frontend_callback}?{query}")


@router.get("/providers", response_model=SsoProvidersResponse)
async def sso_providers(request: Request):
    """Публичная информация о доступных SSO-провайдерах."""
    enabled = is_keycloak_enabled()
    login_url = (
        f"{str(request.base_url).rstrip('/')}/api/auth/sso/keycloak/login"
        if enabled
        else None
    )
    return SsoProvidersResponse(keycloak_enabled=enabled, keycloak_login_url=login_url)


@router.get("/keycloak/login")
async def keycloak_login(request: Request):
    """Начать OIDC login: редирект на Keycloak."""
    if not is_keycloak_enabled():
        log_keycloak_sso_failure(
            request=request,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            log_message="Keycloak SSO: login rejected, provider disabled or misconfigured",
            cef_reason="Keycloak SSO disabled or misconfigured",
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Keycloak SSO отключён",
        )

    request_base_url = str(request.base_url).rstrip("/")
    settings = get_keycloak_settings()
    redirect_uri = resolve_keycloak_redirect_uri(request_base_url, settings)
    logger.info(
        "Keycloak SSO [login start]: issuer=%s client_id=%s redirect_uri=%s request_base=%s frontend_callback=%s",
        sanitize_log_value(str(settings.get("issuer") or "")),
        sanitize_log_value(str(settings.get("client_id") or "")),
        sanitize_log_value(redirect_uri),
        sanitize_log_value(request_base_url),
        sanitize_log_value(str(settings.get("frontend_callback_url") or "")),
    )

    try:
        oauth_state = create_oauth_login_state()
        authorization_url = await build_authorization_url(
            request_base_url=request_base_url,
            state=oauth_state["state"],
            code_challenge=oauth_state["code_challenge"],
        )
    except KeycloakSsoError as exc:
        record_keycloak_sso_error(request, exc)
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    logger.debug(
        "Keycloak SSO [login redirect]: отправка браузера на authorization_endpoint"
    )
    return _secure_redirect(authorization_url)


@router.get("/keycloak/callback")
async def keycloak_callback(
    request: Request,
    response: Response,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
):
    """OIDC callback от Keycloak: обмен code → JWT приложения, редирект на frontend."""
    request_base_url = str(request.base_url).rstrip("/")
    try:
        frontend_callback = resolve_frontend_callback_url()
    except KeycloakSsoError as exc:
        record_keycloak_sso_error(request, exc)
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    logger.info(
        "Keycloak SSO [callback start]: request_base=%s frontend_callback=%s has_code=%s has_state=%s",
        sanitize_log_value(request_base_url),
        sanitize_log_value(frontend_callback),
        bool(code),
        bool(state),
    )

    if error:
        safe_error = sanitize_log_value(error)
        logger.warning("Keycloak SSO callback provider error=%s", safe_error)
        log_keycloak_sso_failure(
            request=request,
            status_code=status.HTTP_401_UNAUTHORIZED,
            log_message="Keycloak SSO callback provider error=%s",
            log_args=(safe_error,),
            cef_reason=f"Keycloak provider error: {safe_error}",
        )
        user_message = sanitize_log_value(error_description or error)
        return _redirect_sso_error(frontend_callback, user_message)

    if not code or not state:
        log_keycloak_sso_failure(
            request=request,
            status_code=status.HTTP_400_BAD_REQUEST,
            log_message="Keycloak SSO callback missing code or state",
            cef_reason="Keycloak callback missing code or state",
        )
        return _redirect_sso_error(
            frontend_callback, "Отсутствует code или state в ответе Keycloak"
        )

    oauth_record = pop_oauth_state(state)
    if not oauth_record:
        log_keycloak_sso_failure(
            request=request,
            status_code=status.HTTP_401_UNAUTHORIZED,
            log_message="Keycloak SSO callback invalid or expired OAuth state",
            cef_reason="Keycloak SSO session expired or invalid state",
        )
        return _redirect_sso_error(
            frontend_callback, "Сессия SSO истекла или недействительна"
        )

    try:
        user_profile = await exchange_authorization_code(
            code=code,
            code_verifier=str(oauth_record.get("code_verifier") or ""),
            request_base_url=request_base_url,
        )
        login_payload = complete_application_login(
            user_profile,
            request,
            response,
            auth_method=AUTH_METHOD_KEYCLOAK,
        )
        ticket = store_sso_ticket(login_payload)
    except KeycloakSsoError as exc:
        record_keycloak_sso_error(request, exc)
        return _redirect_sso_error(frontend_callback, exc.detail)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else "Ошибка входа через SSO"
        log_keycloak_sso_failure(
            request=request,
            status_code=exc.status_code,
            log_message="Keycloak SSO callback HTTP error status=%s",
            log_args=(exc.status_code,),
            cef_reason=sanitize_log_value(detail),
        )
        return _redirect_sso_error(frontend_callback, detail)
    except Exception as exc:  # noqa: BLE001
        log_keycloak_sso_failure(
            request=request,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            log_message="Keycloak SSO callback unexpected error error=%s",
            log_args=(sanitize_log_value(type(exc).__name__),),
            cef_reason="Keycloak SSO internal error",
            log_exception=True,
        )
        return _redirect_sso_error(frontend_callback, "Внутренняя ошибка SSO")

    # Короткий opaque handoff в fragment (#) — не в access-логах; JWT в process store.
    # (Раньше сюда клали 5KB+ Fernet-JWT — Location/#ticket обрезался прокси.)
    frontend_redirect = f"{frontend_callback}#ticket={quote(ticket, safe='')}"
    logger.debug(
        "Keycloak SSO [callback ok]: redirect frontend=%s user=%s handoff_len=%s",
        sanitize_log_value(frontend_callback),
        sanitize_log_value(str(user_profile.get("username") or "")),
        len(ticket),
    )
    return _secure_redirect(frontend_redirect)


@router.post("/keycloak/exchange")
async def keycloak_exchange(
    payload: SsoExchangeRequest, request: Request, response: Response
):
    """Обмен одноразового ticket (после callback) на JWT приложения."""
    ticket = (payload.ticket or "").strip()
    if not ticket:
        log_keycloak_sso_failure(
            request=request,
            status_code=status.HTTP_400_BAD_REQUEST,
            log_message="Keycloak SSO exchange rejected: empty ticket",
            cef_reason="Keycloak SSO exchange missing ticket",
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="ticket обязателен"
        )

    login_payload = pop_sso_ticket(ticket)
    if not login_payload:
        log_keycloak_sso_failure(
            request=request,
            status_code=status.HTTP_401_UNAUTHORIZED,
            log_message="Keycloak SSO exchange rejected: invalid or expired ticket",
            cef_reason="Keycloak SSO ticket invalid or expired",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ticket недействителен или истёк",
        )

    binding_id = login_payload.pop("_binding_id", None)
    if binding_id:
        set_client_binding_cookie(response, str(binding_id))

    logger.debug("Keycloak SSO: ticket успешно обменян на JWT")
    return login_payload
