"""
API routes для аутентификации
"""

import ipaddress
import os
import uuid
from typing import Annotated, Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

from backend.auth.jwt_handler import (
    _jwt_auth_error,
    JWT_TYPE_REFRESH,
    clear_client_binding_cookie,
    consume_refresh_jti,
    create_access_token,
    create_client_binding,
    create_refresh_token,
    create_session_id,
    decode_refresh_token,
    get_client_binding_id_from_request,
    get_current_user,
    get_server_instance_id,
    jwt_token_byte_length,
    register_user_session,
    revoke_client_binding,
    revoke_user_session,
    set_client_binding_cookie,
)
from backend.auth.ldap_auth import (
    authenticate_ldap,
    fetch_ldap_user_photo,
    fetch_ldap_user_profile,
    is_ldap_enabled,
    validate_ldap_user_account,
)
from backend.auth.mock_users import authenticate_mock
from backend.auth.login_lockout import (
    assert_login_not_locked,
    build_cef_sec003_extra,
    build_login_failed_detail,
    get_lockout_policy,
    record_failed_login,
    record_successful_login,
)
from backend.auth.session_timeout import get_session_policy
from backend.settings.cef_logger.cef_logger import (
    domain_from_ldap_base_dn,
    login_audit_cef_extra,
    ldap_reason_suffix,
    log_cef_event,
)
from backend.settings.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/auth", tags=["Authentication"])


class LoginRequest(BaseModel):
    username: str
    password: str


class SessionPolicyPayload(BaseModel):
    timeout_seconds: int
    enabled: bool


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105
    user: dict
    session_policy: Optional[SessionPolicyPayload] = None
    server_instance_id: str


class ServerInstanceResponse(BaseModel):
    instance_id: str


class RefreshRequest(BaseModel):
    refresh_token: str


class UserResponse(BaseModel):
    username: str
    email: Optional[str]
    full_name: Optional[str]
    is_active: bool
    is_admin: bool
    avatar_url: Optional[str] = None
    # Платформенные флаги (LDAP / Keycloak) — иначе /me обрезает их
    # и UI снова показывает конструктор базовому пользователю.
    platform_role: Optional[str] = None
    can_use_agents: Optional[bool] = None
    can_use_mcp: Optional[bool] = None
    can_create_agents: Optional[bool] = None
    can_publish_to_library: Optional[bool] = None
    features: Optional[dict] = None


def _ensure_ldap_user_active(current_user: dict) -> None:
    """Проверить учётную запись в LDAP; при 401 отозвать текущую сессию."""
    try:
        validate_ldap_user_account(
            current_user.get("username") or "", current_user.get("user_id")
        )
    except HTTPException as he:
        if he.status_code == status.HTTP_401_UNAUTHORIZED:
            revoke_user_session(
                current_user.get("user_id") or current_user.get("username") or "",
                current_user.get("session_id"),
            )
        raise


def _ensure_user_account_active(current_user: dict) -> None:
    """Проверить учётную запись: LDAP — через AD; Keycloak/mock — без повторной LDAP-проверки."""
    auth_method = current_user.get("auth_method") or "ldap"
    if auth_method in ("keycloak", "mock"):
        return
    _ensure_ldap_user_active(current_user)


@router.get("/login-lockout-policy")
async def login_lockout_policy():
    """Публичные параметры блокировки входа (для страницы авторизации)."""
    return get_lockout_policy()


@router.get("/session-policy")
async def session_policy():
    """Параметры абсолютного таймаута сессии с момента входа (для frontend)."""
    return get_session_policy()


@router.get("/server-instance", response_model=ServerInstanceResponse)
async def server_instance():
    """Идентификатор текущего процесса backend (меняется при рестарте)."""
    return {"instance_id": get_server_instance_id()}


@router.post("/login", response_model=TokenResponse)
async def login(credentials: LoginRequest, request: Request, response: Response):
    """
    Логин: LDAP (если включён) либо локальные mock-учётки (admin/user/test),
    когда LDAP_ENABLED=false.
    """
    username = credentials.username
    logger.debug("auth login start user=%s", username)
    try:
        assert_login_not_locked(username)
    except HTTPException as he:
        if he.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
            log_cef_event(
                "SEC003",
                request=request,
                current_user={"username": username},
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                extra=build_cef_sec003_extra(username, account_locked=True),
            )
        raise
    ldap_on = is_ldap_enabled()
    logger.debug("auth login ldap_enabled=%s", ldap_on)
    auth_method = "ldap"
    if ldap_on:
        user = authenticate_ldap(username, credentials.password)
        logger.debug("auth login after LDAP user_resolved=%s", bool(user))
    else:
        auth_method = "mock"
        user = authenticate_mock(username, credentials.password)
        logger.debug(
            "auth login LDAP disabled; mock user_resolved=%s",
            bool(user),
        )
    if not user:
        record_failed_login(username)
        logger.info("auth login rejecting invalid credentials user=%s", username)
        log_cef_event(
            "SEC003",
            request=request,
            current_user={"username": username},
            status_code=status.HTTP_401_UNAUTHORIZED,
            extra={
                **build_cef_sec003_extra(username, account_locked=False),
                "auth_method": auth_method,
                **(
                    {"reason": f"LDAP disabled{ldap_reason_suffix()}; mock miss"}
                    if not ldap_on
                    else {}
                ),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=build_login_failed_detail(username),
        )
    user = {**user, "auth_method": auth_method}
    record_successful_login(username)
    sub = username
    session_id = create_session_id()
    session_info = register_user_session(
        sub, session_id, username=username, request=request
    )
    logger.debug("auth login issuing token sub=%s method=%s", sub, auth_method)
    try:
        access_token = create_access_token(
            username=sub, session_id=session_id, user_profile=user
        )
        refresh_token = create_refresh_token(
            username=sub, session_id=session_id, user_profile=user
        )
    except Exception as e:
        logger.exception("auth login JWT token create failed user=%s", username)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка выдачи токена",
        ) from e
    logger.debug(
        "JWT: login sub=%s access=%s байт refresh=%s байт",
        sub,
        jwt_token_byte_length(access_token),
        jwt_token_byte_length(refresh_token),
    )
    logger.info("auth login success sub=%s method=%s", sub, auth_method)
    _login_cef_extra = login_audit_cef_extra(user, auth_method)
    log_cef_event(
        "SEC001",
        request=request,
        current_user=user,
        status_code=status.HTTP_200_OK,
        extra=_login_cef_extra,
    )
    if session_info.get("first_seen"):
        _dntdom = domain_from_ldap_base_dn(os.getenv("LDAP_USER_SEARCH_BASE", "")) or (
            user.get("email", "").split("@", 1)[-1] if user.get("email") else None
        )
        log_cef_event(
            "USR001",
            request=request,
            current_user=user,
            status_code=status.HTTP_200_OK,
            extra={
                "duser": user.get("username"),
                "dntdom": _dntdom,
                **_login_cef_extra,
            },
        )
    policy = get_session_policy()
    binding_id = create_client_binding(sub, session_id)
    set_client_binding_cookie(response, binding_id)
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user": user,
        "session_policy": SessionPolicyPayload(
            timeout_seconds=policy["timeout_seconds"], enabled=policy["enabled"]
        ),
        "server_instance_id": get_server_instance_id(),
    }


@router.post("/refresh", response_model=TokenResponse)
async def refresh_tokens(request: Request, response: Response, payload: RefreshRequest):
    """Обновить access токен по refresh токену."""
    try:
        token_data = decode_refresh_token(payload.refresh_token, request=request)
    except HTTPException as he:
        log_cef_event(
            "INT006",
            request=request,
            current_user={"username": "anonymous"},
            status_code=status.HTTP_401_UNAUTHORIZED,
            extra={
                "methodName": "POST /api/auth/refresh",
                "serviceName": "AstraChat-Auth",
                "requestUuid": uuid.uuid4().hex,
                "codeStatus": str(he.status_code),
                "textStatus": str(he.detail or "Invalid refresh token")[:512],
            },
        )
        raise
    sub = token_data["username"]
    session_id = token_data.get("session_id")
    auth_method = token_data.get("auth_method") or "ldap"
    logger.debug("auth refresh start sub=%s method=%s", sub, auth_method)
    _ensure_user_account_active(
        {
            "username": sub,
            "user_id": sub,
            "session_id": session_id,
            "auth_method": auth_method,
        }
    )
    # Ротация: каждый refresh-токен обменивается один раз (с коротким окном для
    # параллельных вкладок), иначе украденный токен жил бы до конца сессии.
    if not consume_refresh_jti(
        token_data.get("refresh_jti"), float(token_data.get("refresh_exp") or 0)
    ):
        raise _jwt_auth_error(
            "Refresh токен уже использован",
            reason="refresh token reuse detected",
            token_type=JWT_TYPE_REFRESH,
            username=sub,
            request=request,
        )
    if auth_method == "keycloak":
        user_profile = token_data
    else:
        user_profile = fetch_ldap_user_profile(sub) or token_data
    new_access_token = create_access_token(
        username=sub, session_id=session_id, user_profile=user_profile
    )
    new_refresh_token = create_refresh_token(
        username=sub, session_id=session_id, user_profile=user_profile
    )
    logger.debug(
        "JWT: refresh sub=%s access=%s байт refresh=%s байт",
        sub,
        jwt_token_byte_length(new_access_token),
        jwt_token_byte_length(new_refresh_token),
    )
    logger.debug("auth refresh success sub=%s", sub)
    from backend.auth.agent_platform_roles import enrich_user_feature_flags

    public_user = enrich_user_feature_flags(
        {
            "user_id": sub,
            "username": sub,
            "email": user_profile.get("email"),
            "full_name": user_profile.get("full_name"),
            "is_active": True,
            "is_admin": bool(user_profile.get("is_admin")),
            "groups": user_profile.get("groups") or [],
            "ldap_groups": user_profile.get("ldap_groups") or [],
            "avatar_url": user_profile.get("avatar_url"),
        }
    )
    return {
        "access_token": new_access_token,
        "refresh_token": new_refresh_token,
        "token_type": "bearer",
        "user": public_user,
        "server_instance_id": get_server_instance_id(),
    }


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Выход из системы"""
    revoke_client_binding(get_client_binding_id_from_request(request))
    revoke_user_session(current_user["user_id"], current_user.get("session_id"))
    clear_client_binding_cookie(response)
    log_cef_event(
        "SEC002",
        request=request,
        current_user=current_user,
        status_code=status.HTTP_200_OK,
    )
    return {
        "message": "Успешный выход из системы",
        "username": current_user["username"],
    }


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Получить информацию о текущем пользователе"""
    return current_user


def _is_safe_avatar_url(url: str) -> bool:
    """Запрет SSRF: только http(s), хост не из частных/link-local диапазонов.

    Редиректы не следуем (см. get_user_avatar) — иначе IdP мог бы увести на
    169.254.169.254. Literal IP в URL проверяем сразу; DNS-резолв не делаем,
    чтобы юнит-тесты не ходили в сеть.
    """
    try:
        parsed = urlparse(str(url).strip())
    except Exception:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    host = parsed.hostname
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


@router.get("/me/avatar")
async def get_user_avatar(current_user: Annotated[dict, Depends(get_current_user)]):
    """Аватар текущего пользователя: LDAP/AD photo или прокси picture из Keycloak."""
    auth_method = current_user.get("auth_method") or "ldap"
    if auth_method == "keycloak":
        avatar_url = current_user.get("avatar_url")
        if not avatar_url or not str(avatar_url).startswith(("http://", "https://")):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Аватар не найден"
            )
        if not _is_safe_avatar_url(str(avatar_url)):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Аватар не найден"
            )
        try:
            # Без follow_redirects: иначе внешний URL может увести на metadata IP.
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
                upstream = await client.get(str(avatar_url))
                upstream.raise_for_status()
                content_type = upstream.headers.get("content-type") or "image/jpeg"
                return Response(
                    content=upstream.content,
                    media_type=content_type,
                    headers={"Cache-Control": "private, max-age=3600"},
                )
        except httpx.HTTPError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Аватар не найден"
            ) from None

    photo = fetch_ldap_user_photo(
        current_user.get("username") or "",
        current_user.get("user_id"),
    )
    if not photo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Аватар не найден"
        )
    return Response(
        content=photo,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.put("/me", response_model=UserResponse)
async def update_user_info(
    current_user: Annotated[dict, Depends(get_current_user)],
    email: Optional[str] = None,
    full_name: Optional[str] = None,
):
    """Обновить информацию пользователя"""
    if email:
        current_user["email"] = email
    if full_name:
        current_user["full_name"] = full_name
    return current_user


@router.delete("/me")
async def delete_user_account(
    request: Request, current_user: Annotated[dict, Depends(get_current_user)]
):
    """Удаление учетной записи пользователя (аудит-событие, без физического удаления профиля в LDAP)."""
    revoke_user_session(current_user["user_id"], current_user.get("session_id"))
    log_cef_event(
        "USR002",
        request=request,
        current_user=current_user,
        status_code=status.HTTP_200_OK,
        extra={
            "duser": current_user.get("username"),
            "dntdom": domain_from_ldap_base_dn(os.getenv("LDAP_USER_SEARCH_BASE", ""))
            or (
                current_user.get("email", "").split("@", 1)[-1]
                if current_user.get("email")
                else None
            ),
            "dmail": current_user.get("email") or "-",
        },
    )
    return {
        "message": "Учетная запись помечена как удаленная",
        "username": current_user["username"],
    }


@router.get("/verify")
async def verify_token(current_user: Annotated[dict, Depends(get_current_user)]):
    """Проверить валидность токена и статус учётной записи (LDAP или SSO)."""
    _ensure_user_account_active(current_user)
    return {
        "valid": True,
        "username": current_user["username"],
        "server_instance_id": get_server_instance_id(),
    }
