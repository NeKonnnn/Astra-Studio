"""
SSO (Keycloak): безопасное логирование и CEF SEC003 для неудачного входа.
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

from backend.settings.cef_logger.cef_logger import log_cef_event
from backend.settings.logging import get_logger
from backend.utils.safe_paths import sanitize_log_value

logger = get_logger(__name__)


class KeycloakSsoError(Exception):
    """Ошибка Keycloak SSO: HTTP-ответ + поля для audit/log без утечки секретов."""

    def __init__(
        self,
        status_code: int,
        detail: str,
        *,
        log_message: str,
        log_args: Tuple[Any, ...] = (),
        cef_reason: Optional[str] = None,
        username: str = "anonymous",
        log_exception: bool = False,
        error_detail: Optional[str] = None,
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.log_message = log_message
        self.log_args = log_args
        self.cef_reason = sanitize_log_value(cef_reason or detail)
        self.username = sanitize_log_value(username) or "anonymous"
        self.log_exception = log_exception
        self.error_detail = sanitize_log_value(error_detail) if error_detail else None


def keycloak_reason_suffix() -> str:
    """Суффикс SEC003 для Keycloak SSO (issuer из KEYCLOAK_ISSUER)."""
    try:
        from backend.auth.keycloak_auth import get_keycloak_settings

        issuer = get_keycloak_settings().get("issuer") or "-"
        return f" (Keycloak issuer {sanitize_log_value(issuer)})"
    except (ImportError, AttributeError, ValueError, TypeError):
        return ""


def log_keycloak_sso_failure(
    *,
    request: Any = None,
    status_code: int,
    log_message: str,
    log_args: Tuple[Any, ...] = (),
    cef_reason: str,
    username: str = "anonymous",
    log_exception: bool = False,
    error_detail: Optional[str] = None,
) -> None:
    """Обычный log (warning/exception) + CEF SEC003."""
    safe_username = sanitize_log_value(username) or "anonymous"
    if log_exception:
        logger.exception(log_message, *log_args)
    else:
        logger.warning(log_message, *log_args)
    if error_detail:
        logger.warning(
            "Keycloak SSO error detail: %s", sanitize_log_value(error_detail)
        )
    reason = f"{sanitize_log_value(cef_reason)}{keycloak_reason_suffix()}"
    log_cef_event(
        "SEC003",
        request=request,
        current_user={"username": safe_username},
        status_code=status_code,
        extra={"reason": reason, "auth_method": "keycloak"},
    )


def record_keycloak_sso_error(request: Any, exc: KeycloakSsoError) -> None:
    """Записать KeycloakSsoError в журнал и CEF."""
    log_keycloak_sso_failure(
        request=request,
        status_code=exc.status_code,
        log_message=exc.log_message,
        log_args=exc.log_args,
        cef_reason=exc.cef_reason,
        username=exc.username,
        log_exception=exc.log_exception,
        error_detail=exc.error_detail,
    )
