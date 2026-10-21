"""
Общая логика завершения входа (выдача JWT).

Используется SSO-потоком Keycloak. LDAP /login не затрагивается.
"""

from __future__ import annotations

import os
from typing import Any, Dict

from fastapi import Request, Response, status

from backend.auth.jwt_handler import (
    create_access_token,
    create_client_binding,
    create_refresh_token,
    create_session_id,
    get_server_instance_id,
    jwt_token_byte_length,
    register_user_session,
    set_client_binding_cookie,
)
from backend.auth.session_timeout import get_session_policy
from backend.auth.sso_logging import KeycloakSsoError
from backend.settings.cef_logger.cef_logger import (
    domain_from_ldap_base_dn,
    log_cef_event,
    login_audit_cef_extra,
)
from backend.settings.logging import get_logger
from backend.utils.safe_paths import sanitize_log_value

logger = get_logger(__name__)


def complete_application_login(
    user: Dict[str, Any],
    request: Request,
    response: Response,
    *,
    auth_method: str,
) -> Dict[str, Any]:
    username = str(user.get("username") or user.get("user_id") or "").strip()
    if not username:
        raise KeycloakSsoError(
            500,
            "Профиль пользователя без username",
            log_message="Keycloak SSO: user profile missing username after token exchange",
            cef_reason="SSO user profile missing username",
        )

    user_with_method = {**user, "auth_method": auth_method}
    session_id = create_session_id()
    session_info = register_user_session(
        username, session_id, username=username, request=request
    )

    try:
        access_token = create_access_token(
            username=username, session_id=session_id, user_profile=user_with_method
        )
        refresh_token = create_refresh_token(
            username=username, session_id=session_id, user_profile=user_with_method
        )
    except Exception as exc:
        raise KeycloakSsoError(
            500,
            "Ошибка выдачи токена",
            log_message="Keycloak SSO: JWT token create failed user=%s method=%s",
            log_args=(sanitize_log_value(username), sanitize_log_value(auth_method)),
            cef_reason="SSO JWT token issuance failed",
            username=username,
            log_exception=True,
        ) from exc

    logger.debug(
        "JWT: login sub=%s method=%s access=%s байт refresh=%s байт",
        sanitize_log_value(username),
        sanitize_log_value(auth_method),
        jwt_token_byte_length(access_token),
        jwt_token_byte_length(refresh_token),
    )

    extra = login_audit_cef_extra(user_with_method, auth_method)
    extra["duser"] = user.get("username")
    extra["dmail"] = user.get("email") or "-"
    log_cef_event(
        "SEC001",
        request=request,
        current_user=user_with_method,
        status_code=status.HTTP_200_OK,
        extra=extra,
    )

    if session_info.get("first_seen"):
        _dntdom = domain_from_ldap_base_dn(os.getenv("LDAP_USER_SEARCH_BASE", "")) or (
            user.get("email", "").split("@", 1)[-1] if user.get("email") else None
        )
        log_cef_event(
            "USR001",
            request=request,
            current_user=user_with_method,
            status_code=status.HTTP_200_OK,
            extra={"duser": user.get("username"), "dntdom": _dntdom, **extra},
        )

    policy = get_session_policy()
    binding_id = create_client_binding(username, session_id)
    set_client_binding_cookie(response, binding_id)

    public_user = {
        key: value for key, value in user_with_method.items() if key != "keycloak_sub"
    }
    from backend.auth.agent_platform_roles import enrich_user_feature_flags

    enrich_user_feature_flags(public_user)
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user": public_user,
        "session_policy": {
            "timeout_seconds": policy["timeout_seconds"],
            "enabled": policy["enabled"],
        },
        "server_instance_id": get_server_instance_id(),
        "_binding_id": binding_id,
    }
