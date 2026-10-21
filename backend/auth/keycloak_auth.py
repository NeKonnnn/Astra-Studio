"""
Keycloak OIDC (SSO) аутентификация.

Работает параллельно с LDAP: LDAP-код не изменяется.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlencode

import httpx
import jwt

from backend.auth.jwt_handler import JWT_ALGORITHM, JWT_REFRESH_SECRET

from backend.auth.sso_logging import KeycloakSsoError
from backend.settings.logging import get_logger
from backend.utils.safe_paths import sanitize_log_value

logger = get_logger(__name__)

AUTH_METHOD_KEYCLOAK = "keycloak"

_OAUTH_STATE_TTL_SECONDS = 600
_SSO_TICKET_TTL_SECONDS = 120

_sso_ticket_lock = threading.Lock()
# opaque handoff-code -> {exp, payload, used_at|None}. Payload НЕ кладётся в URL:
# зашифрованный JWT с access+refresh раздувался до 5KB+ и обрезался в Location/прокси.
_sso_handoff_store: Dict[str, Dict[str, Any]] = {}
_oauth_state_lock = threading.Lock()
_consumed_oauth_states: Dict[str, float] = {}

_oidc_metadata_cache: Dict[str, Any] = {"fetched_at": 0.0, "data": None}
_OIDC_METADATA_TTL_SECONDS = 300


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _split_csv(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return [item.strip() for item in str(raw).split(",") if item.strip()]


def _normalize_role_name(value: str) -> str:
    return str(value or "").strip().casefold()


def get_keycloak_settings() -> Dict[str, Any]:
    issuer = (os.getenv("KEYCLOAK_ISSUER") or "").strip().rstrip("/")
    client_id = (os.getenv("KEYCLOAK_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("KEYCLOAK_CLIENT_SECRET") or "").strip() or None
    redirect_uri = (os.getenv("KEYCLOAK_REDIRECT_URI") or "").strip() or None
    frontend_callback_url = (
        os.getenv("KEYCLOAK_FRONTEND_CALLBACK_URL") or ""
    ).strip() or None
    return {
        "enabled": _env_bool("KEYCLOAK_ENABLED", False),
        "issuer": issuer,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "frontend_callback_url": frontend_callback_url,
        "admin_groups": [
            _normalize_role_name(g)
            for g in _split_csv(os.getenv("KEYCLOAK_ADMIN_GROUPS", ""))
        ],
        "required_roles": [
            _normalize_role_name(r)
            for r in _split_csv(os.getenv("KEYCLOAK_REQUIRED_ROLES", ""))
        ],
        "groups_claim": (os.getenv("KEYCLOAK_GROUPS_CLAIM") or "groups").strip()
        or "groups",
        "ldap_groups_claim": (os.getenv("KEYCLOAK_LDAP_GROUPS_CLAIM") or "ldap").strip()
        or "ldap",
        "username_claim": (
            os.getenv("KEYCLOAK_USERNAME_CLAIM") or "preferred_username"
        ).strip()
        or "preferred_username",
    }


def is_keycloak_enabled() -> bool:
    settings = get_keycloak_settings()
    if not settings["enabled"]:
        return False
    required = ("issuer", "client_id")
    missing = [key for key in required if not settings.get(key)]
    if missing:
        logger.warning(
            "Keycloak SSO: включён, но не хватает настроек: %s",
            sanitize_log_value(", ".join(missing)),
        )
        return False
    return True


def _generate_pkce_pair() -> tuple[str, str]:
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return code_verifier, code_challenge


def _sso_hmac_secret() -> bytes:
    for name in ("JWT_SECRET", "JWT_REFRESH_SECRET"):
        raw = os.getenv(name, "").strip()
        if raw:
            return raw.encode("utf-8")
    raise KeycloakSsoError(
        500,
        "JWT_SECRET не настроен для SSO",
        log_message="Keycloak SSO: JWT_SECRET/JWT_REFRESH_SECRET missing for signed OAuth state",
        cef_reason="SSO signing secret missing",
    )


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padded = value + "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _pack_signed_oauth_state(code_verifier: str) -> str:
    payload = json.dumps(
        {"exp": int(time.time()) + _OAUTH_STATE_TTL_SECONDS, "cv": code_verifier},
        separators=(",", ":"),
    )
    payload_b64 = _b64url_encode(payload.encode("utf-8"))
    signature = hmac.new(
        _sso_hmac_secret(), payload_b64.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{payload_b64}.{_b64url_encode(signature)}"


def create_oauth_login_state() -> Dict[str, str]:
    """Создать state + PKCE verifier для начала OIDC login."""
    if not is_keycloak_enabled():
        raise KeycloakSsoError(
            503,
            "Keycloak SSO отключён",
            log_message="Keycloak SSO: login rejected, provider disabled or misconfigured",
            cef_reason="Keycloak SSO disabled or misconfigured",
        )
    code_verifier, code_challenge = _generate_pkce_pair()
    state = _pack_signed_oauth_state(code_verifier)
    logger.debug(
        "Keycloak SSO: создан подписанный OAuth state для PKCE (stateless, pod-safe)"
    )
    return {
        "state": state,
        "code_challenge": code_challenge,
        "code_verifier": code_verifier,
    }


def _consume_oauth_state(sig_b64: str, exp_ts: float) -> bool:
    """Помечает OAuth state использованным. False — если он уже был предъявлен.

    Как и для SSO ticket, хранилище process-local: одноразовость гарантирована в
    пределах пода; записи удаляются после истечения TTL state.
    """
    now = time.time()
    with _oauth_state_lock:
        for used_sig, used_exp in list(_consumed_oauth_states.items()):
            if used_exp < now:
                del _consumed_oauth_states[used_sig]
        if sig_b64 in _consumed_oauth_states:
            return False
        _consumed_oauth_states[sig_b64] = exp_ts
        return True


def pop_oauth_state(state: str) -> Optional[Dict[str, Any]]:
    """Извлечь PKCE verifier из подписанного state (одноразово, без server-side хранения verifier).

    Повторное предъявление того же state (replay) отклоняется."""
    if not state or "." not in state:
        logger.warning("Keycloak SSO: OAuth state malformed")
        return None
    payload_b64, sig_b64 = state.split(".", 1)
    try:
        expected_sig = hmac.new(
            _sso_hmac_secret(), payload_b64.encode("ascii"), hashlib.sha256
        ).digest()
        actual_sig = _b64url_decode(sig_b64)
        if not hmac.compare_digest(expected_sig, actual_sig):
            logger.warning("Keycloak SSO: OAuth state invalid signature")
            return None
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
        if int(payload.get("exp") or 0) < time.time():
            logger.warning("Keycloak SSO: OAuth state expired")
            return None
        code_verifier = str(payload.get("cv") or "").strip()
        if not code_verifier:
            logger.warning("Keycloak SSO: OAuth state missing code_verifier")
            return None
        if not _consume_oauth_state(sig_b64, float(payload.get("exp") or 0)):
            logger.warning("Keycloak SSO: OAuth state already used (replay)")
            return None
        return {"code_verifier": code_verifier}
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
        logger.warning(
            "Keycloak SSO: OAuth state parse failed error=%s", _format_error(exc)
        )
        return None


def _sso_ticket_reuse_grace_seconds() -> float:
    raw = os.getenv("SSO_TICKET_REUSE_GRACE_SECONDS", "10").strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 10.0


def _prune_sso_handoffs(now: Optional[float] = None) -> None:
    current = time.time() if now is None else now
    grace = _sso_ticket_reuse_grace_seconds()
    stale_after = _SSO_TICKET_TTL_SECONDS + grace + 1.0
    for code, entry in list(_sso_handoff_store.items()):
        exp = float(entry.get("exp") or 0)
        used_at = entry.get("used_at")
        if exp + stale_after < current:
            del _sso_handoff_store[code]
            continue
        if used_at is not None and float(used_at) + grace < current and exp < current:
            del _sso_handoff_store[code]


def reset_sso_handoff_store() -> None:
    """Сброс handoff-store (тесты)."""
    with _sso_ticket_lock:
        _sso_handoff_store.clear()


def store_sso_ticket(payload: Dict[str, Any]) -> str:
    """Короткий opaque handoff-code; JWT access/refresh лежат в process-local store.

    Раньше в URL уходил Fernet+JWT на 5KB+ — Location/#ticket обрезался прокси и
    фронт не вызывал exchange. Code ~43 символа безопасен для fragment.
    Хранилище process-local (как denylist jti): нужен sticky session на SSO или
    один реплика для callback+exchange. Ticket одноразовый + grace для StrictMode.
    """
    if not JWT_REFRESH_SECRET:
        raise KeycloakSsoError(
            500,
            "JWT_REFRESH_SECRET не настроен для SSO",
            log_message="Keycloak SSO: JWT_REFRESH_SECRET missing for SSO ticket",
            cef_reason="SSO ticket signing secret missing",
        )
    code = secrets.token_urlsafe(32)
    exp = time.time() + _SSO_TICKET_TTL_SECONDS
    with _sso_ticket_lock:
        _prune_sso_handoffs()
        _sso_handoff_store[code] = {
            "exp": exp,
            "payload": dict(payload),
            "used_at": None,
        }
    logger.debug(
        "Keycloak SSO: выдан handoff-code для обмена JWT size=%s байт (opaque, store-backed)",
        len(code),
    )
    return code


def pop_sso_ticket(ticket: str) -> Optional[Dict[str, Any]]:
    """Забрать payload по opaque handoff-code. Одноразовый; в окне grace — повтор ок."""
    if not ticket or not JWT_REFRESH_SECRET:
        return None
    code = str(ticket).strip()
    if not code or "." in code:
        # Старые JWT-ticket / мусор с точками больше не принимаем.
        if "." in code:
            logger.warning("Keycloak SSO: legacy JWT SSO ticket rejected (use opaque handoff)")
        return None
    now = time.time()
    grace = _sso_ticket_reuse_grace_seconds()
    with _sso_ticket_lock:
        _prune_sso_handoffs(now)
        entry = _sso_handoff_store.get(code)
        if not entry:
            logger.warning("Keycloak SSO: SSO handoff unknown or already purged")
            return None
        if float(entry.get("exp") or 0) < now and entry.get("used_at") is None:
            del _sso_handoff_store[code]
            logger.warning("Keycloak SSO: SSO handoff expired")
            return None
        payload = entry.get("payload")
        if not isinstance(payload, dict):
            del _sso_handoff_store[code]
            logger.warning("Keycloak SSO: SSO handoff missing payload")
            return None
        used_at = entry.get("used_at")
        if used_at is None:
            entry["used_at"] = now
            return dict(payload)
        if (now - float(used_at)) < grace:
            return dict(payload)
        logger.warning("Keycloak SSO: SSO handoff already used (replay rejected)")
        return None


def _issuer_url(settings: Dict[str, Any]) -> str:
    return str(settings["issuer"]).rstrip("/")


def _format_error(
    exc: BaseException, *, http_response: Optional[httpx.Response] = None
) -> str:
    """Тип и текст исключения для логов (без stack trace)."""
    type_name = type(exc).__name__
    message = str(exc).strip()
    parts = [type_name]
    if message and message != type_name:
        parts.append(message)
    if http_response is not None:
        try:
            body = (http_response.text or "").strip()
        except (AttributeError, UnicodeDecodeError, ValueError):
            body = ""
        if body:
            parts.append(f"response_body={body[:512]}")
    return sanitize_log_value(" | ".join(parts))


def _http_verify() -> Any:
    """
    TLS verify для httpx при запросах к Keycloak.
    Приоритет: TLS_CERT_PATH -> LDAP_CA_CERT_PATH -> SSL_CERT_FILE -> REQUESTS_CA_BUNDLE -> True.
    """
    for env_name in (
        "TLS_CERT_PATH",
        "LDAP_CA_CERT_PATH",
        "SSL_CERT_FILE",
        "REQUESTS_CA_BUNDLE",
    ):
        cert_path = str(os.getenv(env_name, "") or "").strip()
        if cert_path and os.path.isfile(cert_path):
            logger.debug(
                "Keycloak SSO: TLS verify bundle %s=%s",
                env_name,
                sanitize_log_value(cert_path),
            )
            return cert_path
    logger.debug(
        "Keycloak SSO: TLS verify bundle=system default (CA env paths not found)"
    )
    return True


def _log_sso_outbound(step: str, method: str, url: str, **fields: Any) -> None:
    """INFO: куда backend ходит наружу в рамках SSO (без секретов и одноразовых параметров)."""
    extras = " ".join(
        f"{name}={sanitize_log_value(value)}"
        for name, value in fields.items()
        if value is not None and value != ""
    )
    if extras:
        logger.debug(
            "Keycloak SSO [%s]: %s %s (%s)",
            step,
            method,
            sanitize_log_value(url),
            extras,
        )
    else:
        logger.debug(
            "Keycloak SSO [%s]: %s %s",
            step,
            method,
            sanitize_log_value(url),
        )


async def _fetch_oidc_metadata(settings: Dict[str, Any]) -> Dict[str, Any]:
    global _oidc_metadata_cache
    now = time.time()
    cached = _oidc_metadata_cache.get("data")
    fetched_at = float(_oidc_metadata_cache.get("fetched_at") or 0)
    if cached and now - fetched_at < _OIDC_METADATA_TTL_SECONDS:
        logger.debug(
            "Keycloak SSO [discovery]: используется кэш OIDC metadata issuer=%s",
            sanitize_log_value(_issuer_url(settings)),
        )
        return cached

    discovery_url = f"{_issuer_url(settings)}/.well-known/openid-configuration"
    _log_sso_outbound(
        "discovery",
        "GET",
        discovery_url,
        issuer=_issuer_url(settings),
    )
    try:
        async with httpx.AsyncClient(timeout=15.0, verify=_http_verify()) as client:
            response = await client.get(discovery_url)
            response.raise_for_status()
            metadata = response.json()
    except httpx.HTTPStatusError as exc:
        error_detail = _format_error(exc, http_response=exc.response)
        logger.warning(
            "Keycloak SSO: OIDC discovery HTTP error status=%s url=%s error=%s",
            exc.response.status_code,
            sanitize_log_value(discovery_url),
            error_detail,
        )
        raise KeycloakSsoError(
            503,
            "Keycloak SSO временно недоступен",
            log_message="Keycloak SSO: OIDC discovery HTTP error status=%s url=%s",
            log_args=(exc.response.status_code, sanitize_log_value(discovery_url)),
            cef_reason="Keycloak OIDC discovery failed",
            log_exception=False,
            error_detail=error_detail,
        ) from exc
    except Exception as exc:
        error_detail = _format_error(exc)
        logger.warning(
            "Keycloak SSO: OIDC discovery failed url=%s error=%s",
            sanitize_log_value(discovery_url),
            error_detail,
        )
        raise KeycloakSsoError(
            503,
            "Keycloak SSO временно недоступен",
            log_message="Keycloak SSO: OIDC discovery failed url=%s",
            log_args=(sanitize_log_value(discovery_url),),
            cef_reason="Keycloak OIDC discovery failed",
            log_exception=False,
            error_detail=error_detail,
        ) from exc

    _oidc_metadata_cache = {"fetched_at": now, "data": metadata}
    logger.info(
        "Keycloak SSO [discovery ok]: issuer=%s authorization_endpoint=%s token_endpoint=%s userinfo_endpoint=%s",
        sanitize_log_value(_issuer_url(settings)),
        sanitize_log_value(str(metadata.get("authorization_endpoint") or "")),
        sanitize_log_value(str(metadata.get("token_endpoint") or "")),
        sanitize_log_value(str(metadata.get("userinfo_endpoint") or "")),
    )
    return metadata


def resolve_keycloak_redirect_uri(
    request_base_url: str, settings: Optional[Dict[str, Any]] = None
) -> str:
    settings = settings or get_keycloak_settings()
    if settings.get("redirect_uri"):
        return str(settings["redirect_uri"])
    base = (request_base_url or "").strip().rstrip("/")
    return f"{base}/api/auth/sso/keycloak/callback"


def resolve_frontend_callback_url(settings: Optional[Dict[str, Any]] = None) -> str:
    settings = settings or get_keycloak_settings()
    if settings.get("frontend_callback_url"):
        return str(settings["frontend_callback_url"])
    for env_name in ("KEYCLOAK_FRONTEND_URL", "FRONTEND_URL", "FRONTEND_PORT"):
        raw = (os.getenv(env_name) or "").strip()
        if raw:
            return f"{raw.rstrip('/')}/login/sso-callback"
    try:
        from backend.settings.config import get_settings

        frontend_url = get_settings().urls.frontend_port
        if frontend_url:
            return f"{str(frontend_url).rstrip('/')}/login/sso-callback"
    except (ImportError, AttributeError, ValueError, TypeError) as exc:
        logger.warning(
            "Keycloak SSO: не удалось определить frontend callback URL error=%s",
            sanitize_log_value(type(exc).__name__),
        )
    raise KeycloakSsoError(
        500,
        "Не настроен KEYCLOAK_FRONTEND_CALLBACK_URL или FRONTEND_URL",
        log_message="Keycloak SSO: frontend callback URL is not configured",
        cef_reason="Keycloak SSO frontend callback URL is not configured",
    )


async def build_authorization_url(
    *, request_base_url: str, state: str, code_challenge: str
) -> str:
    settings = get_keycloak_settings()
    metadata = await _fetch_oidc_metadata(settings)
    authorization_endpoint = metadata.get("authorization_endpoint")
    if not authorization_endpoint:
        raise KeycloakSsoError(
            503,
            "Keycloak SSO: authorization_endpoint не найден",
            log_message="Keycloak SSO: authorization_endpoint missing in OIDC metadata",
            cef_reason="Keycloak authorization_endpoint missing",
        )
    redirect_uri = resolve_keycloak_redirect_uri(request_base_url, settings)
    query = urlencode(
        {
            "response_type": "code",
            "client_id": settings["client_id"],
            "redirect_uri": redirect_uri,
            "scope": "openid profile email",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
    )
    authorization_url = f"{authorization_endpoint}?{query}"
    _log_sso_outbound(
        "authorize redirect",
        "BROWSER",
        authorization_endpoint,
        client_id=settings["client_id"],
        redirect_uri=redirect_uri,
        scope="openid profile email",
        request_base_url=request_base_url,
    )
    return authorization_url


def _decode_jwt_payload_unverified(token: str) -> Dict[str, Any]:
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    payload_part = parts[1]
    padded = payload_part + "=" * ((4 - len(payload_part) % 4) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
        return {}


def _merge_claims(primary: Dict[str, Any], secondary: Dict[str, Any]) -> Dict[str, Any]:
    """Объединить claims: роли из access_token дополняют id_token; userinfo — profile-поля."""
    if not primary:
        return dict(secondary)
    if not secondary:
        return dict(primary)
    merged = dict(primary)
    for key in ("realm_access", "resource_access", "groups"):
        if key in secondary and key not in merged:
            merged[key] = secondary[key]
    groups_claim = get_keycloak_settings().get("groups_claim") or "groups"
    if groups_claim not in merged and groups_claim in secondary:
        merged[groups_claim] = secondary[groups_claim]
    ldap_claim = get_keycloak_settings().get("ldap_groups_claim") or "ldap"
    if ldap_claim not in merged and ldap_claim in secondary:
        merged[ldap_claim] = secondary[ldap_claim]
    for key in (
        "picture",
        "name",
        "email",
        "preferred_username",
        "given_name",
        "family_name",
    ):
        if key in secondary and secondary.get(key) and not merged.get(key):
            merged[key] = secondary[key]
    return merged


async def _fetch_userinfo_claims(
    access_token: str, metadata: Dict[str, Any]
) -> Dict[str, Any]:
    """Дополнить профиль из userinfo (picture часто только там, не в id_token)."""
    userinfo_endpoint = metadata.get("userinfo_endpoint")
    if not userinfo_endpoint or not access_token:
        return {}
    _log_sso_outbound("userinfo", "GET", str(userinfo_endpoint))
    try:
        async with httpx.AsyncClient(timeout=15.0, verify=_http_verify()) as client:
            userinfo_response = await client.get(
                userinfo_endpoint,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            userinfo_response.raise_for_status()
            userinfo = userinfo_response.json()
            return userinfo if isinstance(userinfo, dict) else {}
    except httpx.HTTPStatusError as exc:
        error_detail = _format_error(exc, http_response=exc.response)
        logger.warning(
            "Keycloak SSO: userinfo HTTP error status=%s error=%s",
            exc.response.status_code,
            error_detail,
        )
    except httpx.HTTPError as exc:
        error_detail = _format_error(exc)
        logger.warning("Keycloak SSO: userinfo transport error error=%s", error_detail)
    return {}


def _collect_roles_from_claims(
    claims: Dict[str, Any], settings: Dict[str, Any]
) -> Set[str]:
    roles: Set[str] = set()
    groups_claim = settings.get("groups_claim") or "groups"
    groups = claims.get(groups_claim)
    if isinstance(groups, list):
        roles.update(_normalize_role_name(str(item)) for item in groups if item)
    elif isinstance(groups, str) and groups.strip():
        roles.add(_normalize_role_name(groups))

    realm_access = claims.get("realm_access")
    if isinstance(realm_access, dict):
        realm_roles = realm_access.get("roles")
        if isinstance(realm_roles, list):
            roles.update(
                _normalize_role_name(str(item)) for item in realm_roles if item
            )

    resource_access = claims.get("resource_access")
    if isinstance(resource_access, dict):
        client_roles = resource_access.get(settings["client_id"])
        if isinstance(client_roles, dict):
            client_role_list = client_roles.get("roles")
            if isinstance(client_role_list, list):
                roles.update(
                    _normalize_role_name(str(item)) for item in client_role_list if item
                )

    # CN AD-групп из Keycloak mapper (claim ``ldap``) — те же имена, что CN в LDAP memberOf.
    ldap_claim = settings.get("ldap_groups_claim") or "ldap"
    ldap_groups = claims.get(ldap_claim)
    if isinstance(ldap_groups, list):
        roles.update(_normalize_role_name(str(item)) for item in ldap_groups if item)
    elif isinstance(ldap_groups, str) and ldap_groups.strip():
        roles.add(_normalize_role_name(ldap_groups))

    return roles


def _validate_required_roles(
    roles: Set[str], settings: Dict[str, Any], username: str
) -> None:
    required = settings.get("required_roles") or []
    if not required:
        return
    if roles.intersection(set(required)):
        return
    logger.info(
        "Keycloak SSO: пользователь %s без требуемых ролей required=%s",
        sanitize_log_value(username),
        sanitize_log_value(",".join(required)),
    )
    raise KeycloakSsoError(
        403,
        "У вас нет необходимой роли для взаимодействия с AstraChat. Обратитесь к администратору проекта",
        log_message="Keycloak SSO: user=%s missing required roles",
        log_args=(sanitize_log_value(username),),
        cef_reason="Missing required Keycloak role",
        username=username,
    )


def build_user_profile_from_claims(
    claims: Dict[str, Any], settings: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    settings = settings or get_keycloak_settings()
    username_claim = settings.get("username_claim") or "preferred_username"
    username = (
        str(claims.get(username_claim) or "").strip()
        or str(claims.get("preferred_username") or "").strip()
        or str(claims.get("email") or "").split("@", 1)[0].strip()
        or str(claims.get("sub") or "").strip()
    )
    if not username:
        raise KeycloakSsoError(
            401,
            "Keycloak не вернул имя пользователя",
            log_message="Keycloak SSO: username claim missing in token",
            cef_reason="Keycloak token missing username",
        )

    roles = _collect_roles_from_claims(claims, settings)
    _validate_required_roles(roles, settings, username)

    admin_groups = set(settings.get("admin_groups") or [])
    is_admin = bool(roles.intersection(admin_groups)) if admin_groups else False
    group_names = sorted({role for role in roles if role})

    email = claims.get("email")
    full_name = claims.get("name") or claims.get("given_name")
    picture = claims.get("picture")

    azp_raw = claims.get("azp") or claims.get("client_id")
    azp = str(azp_raw).strip() if azp_raw else ""
    if not azp and settings.get("client_id"):
        azp = str(settings["client_id"]).strip()

    logger.debug(
        "Keycloak SSO: профиль user=%s is_admin=%s groups=%s azp=%s",
        sanitize_log_value(username),
        is_admin,
        len(group_names),
        sanitize_log_value(azp or "-"),
    )

    profile: Dict[str, Any] = {
        "user_id": username,
        "username": username,
        "email": str(email).strip() if email else None,
        "full_name": str(full_name).strip() if full_name else None,
        "is_active": True,
        "is_admin": is_admin,
        "groups": group_names,
        "ldap_groups": list(group_names),
        "auth_method": AUTH_METHOD_KEYCLOAK,
    }
    if picture and str(picture).strip():
        profile["avatar_url"] = str(picture).strip()
    if azp:
        profile["azp"] = azp
    return profile


async def exchange_authorization_code(
    *,
    code: str,
    code_verifier: str,
    request_base_url: str,
) -> Dict[str, Any]:
    settings = get_keycloak_settings()
    metadata = await _fetch_oidc_metadata(settings)
    token_endpoint = metadata.get("token_endpoint")
    if not token_endpoint:
        raise KeycloakSsoError(
            503,
            "Keycloak SSO: token_endpoint не найден",
            log_message="Keycloak SSO: token_endpoint missing in OIDC metadata",
            cef_reason="Keycloak token_endpoint missing",
        )

    redirect_uri = resolve_keycloak_redirect_uri(request_base_url, settings)
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": settings["client_id"],
        "code_verifier": code_verifier,
    }
    if settings.get("client_secret"):
        data["client_secret"] = settings["client_secret"]

    _log_sso_outbound(
        "token exchange",
        "POST",
        str(token_endpoint),
        client_id=settings["client_id"],
        redirect_uri=redirect_uri,
        request_base_url=request_base_url,
        client_secret="set" if settings.get("client_secret") else "not set",
    )

    try:
        async with httpx.AsyncClient(timeout=20.0, verify=_http_verify()) as client:
            response = await client.post(token_endpoint, data=data)
            if response.status_code >= 400:
                try:
                    body = (response.text or "").strip()[:512]
                except (AttributeError, UnicodeDecodeError, ValueError):
                    body = ""
                error_detail = sanitize_log_value(
                    f"HTTPStatusError | status={response.status_code}"
                    + (f" | response_body={body}" if body else "")
                )
                logger.warning(
                    "Keycloak SSO: token exchange rejected status=%s client_id=%s error=%s",
                    response.status_code,
                    sanitize_log_value(settings["client_id"]),
                    error_detail,
                )
                raise KeycloakSsoError(
                    401,
                    "Keycloak отклонил авторизацию",
                    log_message="Keycloak SSO: token exchange rejected status=%s client_id=%s",
                    log_args=(
                        response.status_code,
                        sanitize_log_value(settings["client_id"]),
                    ),
                    cef_reason="Keycloak token exchange rejected",
                    error_detail=error_detail,
                )
            token_payload = response.json()
            logger.debug(
                "Keycloak SSO [token exchange ok]: token_endpoint=%s status=%s",
                sanitize_log_value(str(token_endpoint)),
                response.status_code,
            )
    except KeycloakSsoError:
        raise
    except httpx.HTTPError as exc:
        error_detail = _format_error(exc)
        logger.warning(
            "Keycloak SSO: token exchange transport error error=%s",
            error_detail,
        )
        raise KeycloakSsoError(
            503,
            "Keycloak SSO временно недоступен",
            log_message="Keycloak SSO: token exchange transport error",
            cef_reason="Keycloak token exchange transport error",
            error_detail=error_detail,
        ) from exc
    except Exception as exc:
        error_detail = _format_error(exc)
        logger.warning(
            "Keycloak SSO: token exchange unexpected error error=%s",
            error_detail,
        )
        raise KeycloakSsoError(
            503,
            "Keycloak SSO временно недоступен",
            log_message="Keycloak SSO: token exchange unexpected error",
            cef_reason="Keycloak token exchange failed",
            log_exception=True,
            error_detail=error_detail,
        ) from exc

    id_token = str(token_payload.get("id_token") or "")
    access_token = str(token_payload.get("access_token") or "")
    id_claims = _decode_jwt_payload_unverified(id_token) if id_token else {}
    access_claims = _decode_jwt_payload_unverified(access_token) if access_token else {}
    claims = _merge_claims(id_claims, access_claims)

    if access_token:
        userinfo_claims = await _fetch_userinfo_claims(access_token, metadata)
        if userinfo_claims:
            claims = _merge_claims(claims, userinfo_claims)

    if not claims:
        raise KeycloakSsoError(
            401,
            "Keycloak не вернул данные пользователя",
            log_message="Keycloak SSO: no user claims in token response",
            cef_reason="Keycloak token response missing user claims",
        )

    user_profile = build_user_profile_from_claims(claims, settings)
    user_profile["keycloak_sub"] = claims.get("sub")
    return user_profile
