"""
JWT токены для аутентификации
"""

import os
import uuid
from datetime import datetime, timedelta
from http.cookies import SimpleCookie
from threading import RLock
from typing import Annotated, Any, Dict, List, Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError

from backend.settings.cef_logger.cef_logger import log_cef_event
from backend.settings.logging import get_logger

logger = get_logger(__name__)

security = HTTPBearer()
JWT_ACCESS_SECRET = os.getenv("JWT_SECRET")
JWT_REFRESH_SECRET = os.getenv("JWT_REFRESH_SECRET")
JWT_ALGORITHM = "HS256"
JWT_TYPE_ACCESS = "access"
JWT_TYPE_REFRESH = "refresh"


def _optional_env_str(name: str, fallback: str) -> str:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return fallback
    return str(raw).strip()


JWT_ISSUER = _optional_env_str("JWT_ISS", _optional_env_str("JWT_ISSUER", "AstraChat"))
JWT_AUDIENCE = _optional_env_str(
    "JWT_AUD", _optional_env_str("JWT_AUDIENCE", "astra-chat-api")
)


def _aud_claim_matches(expected: str, claim_value: Any) -> bool:
    """aud в токене (строка или список); отсутствие claim — legacy-токен."""
    if claim_value is None:
        return True
    if isinstance(claim_value, list):
        return expected in [str(v) for v in claim_value if v is not None]
    return str(claim_value) == expected


def _assert_standard_jwt_claims(payload: dict) -> None:
    """Проверить iss/aud, если они присутствуют в payload (без jose auto-validate)."""
    aud = payload.get("aud")
    if not _aud_claim_matches(JWT_AUDIENCE, aud):
        msg = f"Invalid audience (expected {JWT_AUDIENCE!r}, got {aud!r})"
        raise JWTError(msg)
    iss = payload.get("iss")
    if iss is not None and str(iss) != JWT_ISSUER:
        msg = f"Invalid issuer (expected {JWT_ISSUER!r}, got {iss!r})"
        raise JWTError(msg)


def _decode_jwt(
    token: str,
    secret: str,
    *,
    verify_exp: bool = True,
    verify_standard_claims: bool = True,
) -> dict:
    """Декодировать JWT: подпись + exp; iss/aud — вручную (legacy без iss/aud допускается)."""
    options: Dict[str, Any] = {"verify_aud": False, "verify_iss": False}
    if not verify_exp:
        options["verify_exp"] = False
    payload = jwt.decode(token, secret, algorithms=[JWT_ALGORITHM], options=options)
    if verify_standard_claims:
        _assert_standard_jwt_claims(payload)
    return payload


def _jwt_error_reason(exc: Exception) -> str:
    if isinstance(exc, ExpiredSignatureError):
        return "token expired (exp)"
    if isinstance(exc, JWTError):
        message = str(exc).strip()
        if message:
            return message
        return f"JWT error ({type(exc).__name__})"
    return f"JWT error ({type(exc).__name__})"


def _required_env_int(name: str) -> int:
    """Обязательная целочисленная переменная окружения (TTL в секундах)."""
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        msg = f"Missing required JWT env var: {name}"
        raise RuntimeError(msg)
    value = int(str(raw).strip())
    if value <= 0:
        msg = f"JWT env var {name} must be a positive integer (seconds), got {raw!r}"
        raise RuntimeError(msg)
    return value


JWT_ACCESS_TOKEN_EXPIRE_SECONDS = _required_env_int("JWT_ACCESS_TOKEN_EXPIRE_SECONDS")
JWT_REFRESH_TOKEN_EXPIRE_SECONDS = _required_env_int("JWT_REFRESH_TOKEN_EXPIRE_SECONDS")
AUTH_MAX_ACTIVE_SESSION = _required_env_int("AUTH_MAX_ACTIVE_SESSION")


def get_max_active_sessions() -> int:
    return AUTH_MAX_ACTIVE_SESSION


def _log_jwt_configuration() -> None:
    access_from_env = bool(JWT_ACCESS_SECRET)
    refresh_from_env = bool(JWT_REFRESH_SECRET)
    logger.info(
        "JWT config: access_secret_source=%s refresh_secret_source=%s algorithm=%s access_ttl_sec=%s refresh_ttl_sec=%s max_active_sessions=%s jwt_iss=%s jwt_aud=%s",
        "env" if access_from_env else "missing",
        "env" if refresh_from_env else "missing",
        JWT_ALGORITHM,
        JWT_ACCESS_TOKEN_EXPIRE_SECONDS,
        JWT_REFRESH_TOKEN_EXPIRE_SECONDS,
        AUTH_MAX_ACTIVE_SESSION,
        JWT_ISSUER,
        JWT_AUDIENCE,
    )
    if not access_from_env or not refresh_from_env:
        missing_vars = []
        if not access_from_env:
            missing_vars.append("JWT_SECRET")
        if not refresh_from_env:
            missing_vars.append("JWT_REFRESH_SECRET")
        logger.error(
            "JWT config: отсутствуют обязательные переменные: %s",
            ", ".join(missing_vars),
        )
        msg = f"Missing required JWT env vars: {', '.join(missing_vars)}"
        raise RuntimeError(msg)


_log_jwt_configuration()
_active_sessions_lock = RLock()
_active_sessions_by_user: dict[str, List[str]] = {}
SERVER_INSTANCE_ID = str(uuid.uuid4())
CLIENT_BINDING_COOKIE = "astra_client_binding"
_binding_lock = RLock()
_client_bindings: dict[str, Dict[str, str]] = {}

# Refresh-токен одноразовый: jti -> (время первого использования, exp токена).
# Состояние в памяти — как и _active_sessions_by_user: рестарт пода сбрасывает
# и сессии, и этот реестр. Небольшое «окно повторного использования»
# (по умолчанию 10 с) терпит параллельные refresh из двух вкладок / повторный
# mount в React StrictMode; позже старый токен отклоняется.
_consumed_refresh_lock = RLock()
_consumed_refresh_jtis: dict[str, tuple[float, float]] = {}


def _refresh_reuse_grace_seconds() -> float:
    raw = os.getenv("JWT_REFRESH_REUSE_GRACE_SECONDS", "10").strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 10.0


def consume_refresh_jti(jti: Optional[str], exp_ts: float, *, now: Optional[float] = None) -> bool:
    """Помечает refresh-токен использованным.

    True — токен можно обменять (первый раз или в окне повторного использования),
    False — токен уже был обменян раньше. Токены без ``jti`` (выданные до
    введения ротации) пропускаются без учёта.
    """
    if not jti:
        return True
    import time

    current = time.time() if now is None else now
    grace = _refresh_reuse_grace_seconds()
    with _consumed_refresh_lock:
        for used_jti, (_, used_exp) in list(_consumed_refresh_jtis.items()):
            if used_exp and used_exp < current:
                del _consumed_refresh_jtis[used_jti]
        first_used = _consumed_refresh_jtis.get(jti)
        if first_used is None:
            _consumed_refresh_jtis[jti] = (current, exp_ts)
            return True
        return (current - first_used[0]) < grace


def reset_consumed_refresh_tokens() -> None:
    """Сбросить реестр использованных refresh-токенов (тесты)."""
    with _consumed_refresh_lock:
        _consumed_refresh_jtis.clear()


def _cookie_secure_flag() -> bool:
    return os.getenv("AUTH_COOKIE_SECURE", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def get_client_binding_id_from_request(request: Any) -> Optional[str]:
    if request is None:
        return None
    try:
        value = request.cookies.get(CLIENT_BINDING_COOKIE)
        return str(value).strip() if value else None
    except Exception:
        logger.exception("Ошибка операции")
        return None


def get_client_binding_id_from_cookie_header(cookie_header: str) -> Optional[str]:
    if not cookie_header:
        return None
    try:
        jar = SimpleCookie()
        jar.load(cookie_header)
        morsel = jar.get(CLIENT_BINDING_COOKIE)
        if not morsel:
            return None
        value = str(morsel.value or "").strip()
        return value or None
    except Exception:
        logger.exception("Ошибка операции")
        return None


def create_client_binding(user_id: str, session_id: str) -> str:
    """Создать привязку браузера к sid (при login). Старые binding пользователя снимаются."""
    binding_id = str(uuid.uuid4())
    with _binding_lock:
        stale = [
            bid
            for bid, rec in _client_bindings.items()
            if rec.get("user_id") == user_id
        ]
        for bid in stale:
            _client_bindings.pop(bid, None)
        _client_bindings[binding_id] = {"user_id": user_id, "sid": session_id}
    return binding_id


def revoke_client_binding(binding_id: Optional[str]) -> None:
    if not binding_id:
        return
    with _binding_lock:
        _client_bindings.pop(binding_id, None)


def revoke_client_bindings_for_session(user_id: str, session_id: str) -> None:
    with _binding_lock:
        stale = [
            bid
            for bid, rec in _client_bindings.items()
            if rec.get("user_id") == user_id and rec.get("sid") == session_id
        ]
        for bid in stale:
            _client_bindings.pop(bid, None)


def revoke_all_client_bindings_for_user(user_id: str) -> None:
    with _binding_lock:
        stale = [
            bid
            for bid, rec in _client_bindings.items()
            if rec.get("user_id") == user_id
        ]
        for bid in stale:
            _client_bindings.pop(bid, None)


def set_client_binding_cookie(response: Any, binding_id: str) -> None:
    """Установить HttpOnly cookie привязки (только вместе с JWT того же login)."""
    response.set_cookie(
        key=CLIENT_BINDING_COOKIE,
        value=binding_id,
        max_age=JWT_REFRESH_TOKEN_EXPIRE_SECONDS,
        httponly=True,
        secure=_cookie_secure_flag(),
        samesite="lax",
        path="/",
    )


def clear_client_binding_cookie(response: Any) -> None:
    response.delete_cookie(
        key=CLIENT_BINDING_COOKIE,
        path="/",
        httponly=True,
        secure=_cookie_secure_flag(),
        samesite="lax",
    )


def _resolve_client_binding_id(
    *, request: Any = None, client_binding_id: Optional[str] = None
) -> Optional[str]:
    if client_binding_id:
        return client_binding_id
    return get_client_binding_id_from_request(request)


def _assert_client_binding(
    binding_id: Optional[str],
    user_id: str,
    session_id: Optional[str],
    *,
    request: Any = None,
    token_type: str = JWT_TYPE_ACCESS,
    username: Optional[str] = None,
    token: Optional[str] = None,
    allow_restore: bool = False,
) -> None:
    """JWT допустим только вместе с cookie привязки того же login/sid."""
    if not binding_id:
        logger.warning(
            "JWT client binding: отсутствует cookie user_id=%s sid=%s",
            user_id,
            session_id,
        )
        msg = "Токен не привязан к сессии браузера"
        raise _jwt_auth_error(
            msg,
            reason="missing client binding cookie",
            token_type=token_type,
            username=username or user_id,
            user_id=user_id,
            request=request,
            token=token,
        )
    with _binding_lock:
        record = _client_bindings.get(binding_id)
        if (
            record
            and record.get("user_id") == user_id
            and record.get("sid") == session_id
        ):
            return
        if record:
            logger.warning(
                "JWT client binding: несовпадение user_id=%s sid=%s binding=%s",
                user_id,
                session_id,
                binding_id,
            )
            msg = "Токен не соответствует сессии браузера"
            raise _jwt_auth_error(
                msg,
                reason="client binding mismatch (token from another user or browser)",
                token_type=token_type,
                username=username or user_id,
                user_id=user_id,
                request=request,
                token=token,
            )
        if allow_restore:
            _client_bindings[binding_id] = {"user_id": user_id, "sid": session_id}
            logger.info(
                "JWT client binding: восстановлена in-memory привязка user_id=%s sid=%s (вероятно рестарт пода)",
                user_id,
                session_id,
            )
            return
    logger.warning(
        "JWT client binding: несовпадение user_id=%s sid=%s binding=%s",
        user_id,
        session_id,
        binding_id,
    )
    msg = "Токен не соответствует сессии браузера"
    raise _jwt_auth_error(
        msg,
        reason="client binding mismatch (token from another user or browser)",
        token_type=token_type,
        username=username or user_id,
        user_id=user_id,
        request=request,
        token=token,
    )


def _subject_from_payload(payload: dict) -> Optional[str]:
    """Субъект JWT: claim ``sub`` (в старых токенах мог быть дублирующий ``user_id``)."""
    sub = payload.get("sub")
    if sub:
        return str(sub)
    legacy = payload.get("user_id")
    return str(legacy) if legacy else None


def jwt_token_byte_length(token: Any) -> int:
    """Размер JWT в байтах (без логирования самого токена)."""
    if isinstance(token, bytes):
        return len(token)
    if isinstance(token, str):
        return len(token.encode("utf-8"))
    return len(str(token).encode("utf-8"))


def _parse_bearer_token(request: Request) -> Optional[str]:
    """Bearer-токен из заголовка Authorization (без проверки)."""
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth:
        return None
    parts = auth.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def _session_is_active(user_id: str, session_id: Optional[str]) -> bool:
    if not session_id:
        return False
    with _active_sessions_lock:
        sessions = _active_sessions_by_user.get(user_id)
        return bool(sessions and session_id in sessions)


def try_user_from_request(request: Request) -> Optional[dict]:
    """Тихое извлечение пользователя из access JWT для CEF (без SEC007 и HTTPException)."""
    token = _parse_bearer_token(request)
    if not token:
        return None
    try:
        payload = _decode_jwt(token, JWT_ACCESS_SECRET)
        if payload.get("token_type") != JWT_TYPE_ACCESS:
            return None
        username = _subject_from_payload(payload)
        if not username:
            return None
        session_id = payload.get("sid")
        if not _session_is_active(username, session_id):
            return None
        return {
            "username": username,
            "user_id": username,
            "session_id": session_id,
            "email": None,
            "full_name": None,
            "is_active": True,
            "is_admin": False,
        }
    except (JWTError, ExpiredSignatureError):
        return None


def _peek_jwt_sub(token: str, token_type: str) -> Optional[str]:
    """Извлечь sub из JWT для CEF (без проверки exp)."""
    secret = JWT_REFRESH_SECRET if token_type == JWT_TYPE_REFRESH else JWT_ACCESS_SECRET
    if not secret:
        return None
    try:
        payload = _decode_jwt(
            token, secret, verify_exp=False, verify_standard_claims=False
        )
        return _subject_from_payload(payload)
    except Exception:
        logger.exception("Ошибка операции")
        return None


def _log_jwt_validation_failure(
    *,
    reason: str,
    token_type: str = JWT_TYPE_ACCESS,
    username: Optional[str] = None,
    user_id: Optional[str] = None,
    request: Any = None,
) -> None:
    """CEF SEC007: неуспешная проверка JWT (п. 31.14)."""
    effective_username = username or "anonymous"
    effective_user_id = user_id or username or "anonymous"
    log_cef_event(
        "SEC007",
        request=request,
        current_user={"username": effective_username, "user_id": effective_user_id},
        status_code=status.HTTP_401_UNAUTHORIZED,
        extra={"reason": reason, "cs1": token_type, "cs1Label": "TokenType"},
    )


def _jwt_auth_error(
    detail: str,
    *,
    reason: str,
    token_type: str = JWT_TYPE_ACCESS,
    token: Optional[str] = None,
    username: Optional[str] = None,
    user_id: Optional[str] = None,
    request: Any = None,
) -> HTTPException:
    if username is None and user_id is None and token:
        username = _peek_jwt_sub(token, token_type)
        user_id = username
    _log_jwt_validation_failure(
        reason=reason,
        token_type=token_type,
        username=username,
        user_id=user_id or username,
        request=request,
    )
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


def get_server_instance_id() -> str:
    return SERVER_INSTANCE_ID


def create_session_id() -> str:
    """Создать новый идентификатор сессии."""
    return str(uuid.uuid4())


def register_user_session(
    user_id: str, session_id: str, username: Optional[str] = None, request: Any = None
) -> Dict[str, Any]:
    """Зарегистрировать session_id среди активных сессий пользователя (до AUTH_MAX_ACTIVE_SESSION).

    При превышении лимита отзываются самые старые sid (SEC006).
    Returns:
        dict with keys:
        - invalidated_count: int
        - first_seen: bool
    """
    invalidated_count = 0
    first_seen = False
    with _active_sessions_lock:
        sessions = _active_sessions_by_user.get(user_id)
        if sessions is None:
            first_seen = True
            sessions = []
            _active_sessions_by_user[user_id] = sessions
        if session_id in sessions:
            sessions.remove(session_id)
        sessions.append(session_id)
        revoked_sids: List[str] = []
        while len(sessions) > AUTH_MAX_ACTIVE_SESSION:
            revoked_sids.append(sessions.pop(0))
            invalidated_count += 1
        if not sessions:
            _active_sessions_by_user.pop(user_id, None)
    for old_sid in revoked_sids:
        revoke_client_bindings_for_session(user_id, old_sid)
    if invalidated_count:
        log_cef_event(
            "SEC006",
            request=request,
            current_user={"username": username or user_id, "user_id": user_id},
            status_code=200,
            extra={"cn1": invalidated_count, "cn1Label": "InvalidatedSessionCount"},
        )
    return {"invalidated_count": invalidated_count, "first_seen": first_seen}


def revoke_user_session(user_id: str, session_id: Optional[str] = None) -> None:
    """Отозвать сессию пользователя.

    Если session_id передан — удаляется только он.
    Если не передан — удаляются все активные сессии пользователя.
    """
    with _active_sessions_lock:
        sessions = _active_sessions_by_user.get(user_id)
        if not sessions:
            return
        if session_id:
            if session_id not in sessions:
                return
            sessions.remove(session_id)
            if not sessions:
                _active_sessions_by_user.pop(user_id, None)
            revoke_client_bindings_for_session(user_id, session_id)
            return
        _active_sessions_by_user.pop(user_id, None)
    revoke_all_client_bindings_for_user(user_id)


def _assert_active_session(
    user_id: str,
    session_id: Optional[str],
    *,
    request: Any = None,
    token_type: str = JWT_TYPE_ACCESS,
    username: Optional[str] = None,
) -> None:
    """Проверить, что session_id — текущая активная сессия пользователя."""
    if not session_id:
        logger.warning("JWT session validate: missing sid user_id=%s", user_id)
        msg = "Сессия завершена"
        raise _jwt_auth_error(
            msg,
            reason="missing session id (sid)",
            token_type=token_type,
            username=username or user_id,
            user_id=user_id,
            request=request,
        )
    with _active_sessions_lock:
        sessions = _active_sessions_by_user.get(user_id)
        if sessions and session_id in sessions:
            return
        if not sessions:
            logger.warning(
                "JWT session validate: сессия отсутствует (logout или рестарт pod) user_id=%s sid=%s",
                user_id,
                session_id,
            )
            msg = "Сессия завершена: сервер перезапущен"
            raise _jwt_auth_error(
                msg,
                reason="session not found in memory (logout or pod restart)",
                token_type=token_type,
                username=username or user_id,
                user_id=user_id,
                request=request,
            )
        logger.warning(
            "JWT session validate: session revoked user_id=%s sid=%s active_count=%s max=%s",
            user_id,
            session_id,
            len(sessions),
            AUTH_MAX_ACTIVE_SESSION,
        )
        detail = (
            "Сессия завершена: выполнен вход с другого устройства/окна"
            if AUTH_MAX_ACTIVE_SESSION <= 1
            else f"Сессия завершена: превышен лимит активных сессий ({AUTH_MAX_ACTIVE_SESSION})"
        )
        raise _jwt_auth_error(
            detail,
            reason="session superseded (login elsewhere or max active sessions exceeded)",
            token_type=token_type,
            username=username or user_id,
            user_id=user_id,
            request=request,
        )


def _apply_standard_jwt_claims(to_encode: dict) -> None:
    """RFC 7519: iss, aud, iat (sub/exp задаются отдельно; alg — в header JWS)."""
    now = datetime.utcnow()
    to_encode["iss"] = JWT_ISSUER
    to_encode["aud"] = JWT_AUDIENCE
    to_encode["iat"] = now


_JWT_GROUPS_MAX = 64


def _compact_groups_for_jwt(user_profile: dict) -> List[str]:
    """
    grp в JWT ограничен по числу элементов. Полный memberOf у LDAP-админов
    часто >64: обрезание «как есть» выкидывало CN ролей AGENT_ROLE_* /
    LDAP_ADMIN_GROUPS → platform_role=base, пустые галерея/конструктор.

    Кладём сначала CN админских и платформенных групп, затем остальные CN
    (компактнее DN), потом хвост.
    """
    from backend.auth.agent_platform_roles import (
        _allowed_sets,
        _normalize_group_identifier,
    )
    from backend.auth.ldap_auth import _extract_group_cn, _split_groups

    raw_items: List[str] = []
    for raw in list(user_profile.get("groups") or []) + list(
        user_profile.get("ldap_groups") or []
    ):
        item = str(raw).strip()
        if item:
            raw_items.append(item)
    if not raw_items:
        return []

    admin_keys: set[str] = set()
    for env_name in ("LDAP_ADMIN_GROUPS", "KEYCLOAK_ADMIN_GROUPS"):
        for ag in _split_groups(os.getenv(env_name, "")):
            admin_keys.update(_normalize_group_identifier(ag))
            cn = _extract_group_cn(ag)
            if cn:
                admin_keys.add(cn.casefold())

    try:
        owner_s, editor_s, viewer_s, base_s = _allowed_sets()
        role_keys = owner_s | editor_s | viewer_s | base_s
    except Exception:
        role_keys = set()

    priority: List[str] = []
    rest: List[str] = []
    seen: set[str] = set()

    for item in raw_items:
        cn = _extract_group_cn(item)
        # В JWT предпочитаем CN: короче и совпадает с Keycloak / AGENT_ROLE_*.
        store = cn if (cn and ("," in item or item.casefold().startswith("cn="))) else item
        key = store.casefold()
        if key in seen:
            continue
        seen.add(key)

        norms = _normalize_group_identifier(item) | _normalize_group_identifier(store)
        if (admin_keys and (norms & admin_keys)) or (role_keys and (norms & role_keys)):
            priority.append(store)
        else:
            rest.append(store)

    return (priority + rest)[:_JWT_GROUPS_MAX]


def _apply_user_profile_claims(to_encode: dict, user_profile: Optional[dict]) -> None:
    """LDAP/SSO profile → compact JWT claims (grp = CN групп; am = способ входа; azp = OIDC client)."""
    if not user_profile:
        return
    auth_method = user_profile.get("auth_method")
    if auth_method:
        to_encode["am"] = str(auth_method)
    azp = user_profile.get("azp")
    if azp:
        to_encode["azp"] = str(azp)
    if user_profile.get("is_admin"):
        to_encode["adm"] = True
    merged_groups = _compact_groups_for_jwt(user_profile)
    if merged_groups:
        to_encode["grp"] = merged_groups
    email = user_profile.get("email")
    if email:
        to_encode["email"] = str(email)
    full_name = user_profile.get("full_name")
    if full_name:
        to_encode["name"] = str(full_name)
    avatar_url = user_profile.get("avatar_url")
    if avatar_url:
        av = str(avatar_url).strip()
        if av.startswith(("http://", "https://")):
            to_encode["pic"] = av[:512]
        elif av == "/api/auth/me/avatar" or user_profile.get("has_avatar"):
            to_encode["hav"] = True


def _user_dict_from_payload(payload: dict) -> dict:
    groups = payload.get("grp") or []
    if not isinstance(groups, list):
        groups = [groups] if groups else []
    sub = _subject_from_payload(payload) or ""
    user: Dict[str, Any] = {
        "username": sub,
        "user_id": sub,
        "session_id": payload.get("sid"),
        "email": payload.get("email"),
        "full_name": payload.get("name"),
        "is_active": True,
        "is_admin": bool(payload.get("adm")),
        "groups": groups,
        "ldap_groups": list(groups),
        "auth_method": payload.get("am") or "ldap",
    }
    azp = payload.get("azp")
    if azp:
        user["azp"] = str(azp)
    pic = payload.get("pic")
    if pic:
        user["avatar_url"] = str(pic)
    elif payload.get("hav"):
        user["avatar_url"] = "/api/auth/me/avatar"
    return user


def create_access_token(
    username: str,
    session_id: Optional[str] = None,
    *,
    user_profile: Optional[dict] = None,
) -> str:
    """Создать JWT access (субъект только в claim ``sub``)."""
    expire = datetime.utcnow() + timedelta(seconds=JWT_ACCESS_TOKEN_EXPIRE_SECONDS)
    sid = session_id or create_session_id()
    to_encode = {
        "sub": username,
        "sid": sid,
        "token_type": JWT_TYPE_ACCESS,
        "exp": expire,
    }
    _apply_standard_jwt_claims(to_encode)
    _apply_user_profile_claims(to_encode, user_profile)
    token = jwt.encode(to_encode, JWT_ACCESS_SECRET, algorithm=JWT_ALGORITHM)
    logger.debug(
        "JWT: выдан access-токен sub=%s размер=%s байт",
        username,
        jwt_token_byte_length(token),
    )
    return token


def create_refresh_token(
    username: str,
    session_id: Optional[str] = None,
    *,
    user_profile: Optional[dict] = None,
) -> str:
    """Создать JWT refresh (субъект только в claim ``sub``)."""
    expire = datetime.utcnow() + timedelta(seconds=JWT_REFRESH_TOKEN_EXPIRE_SECONDS)
    sid = session_id or create_session_id()
    to_encode = {
        "sub": username,
        "sid": sid,
        "token_type": JWT_TYPE_REFRESH,
        "exp": expire,
        "jti": uuid.uuid4().hex,
    }
    _apply_standard_jwt_claims(to_encode)
    _apply_user_profile_claims(to_encode, user_profile)
    token = jwt.encode(to_encode, JWT_REFRESH_SECRET, algorithm=JWT_ALGORITHM)
    logger.debug(
        "JWT: выдан refresh-токен sub=%s размер=%s байт",
        username,
        jwt_token_byte_length(token),
    )
    return token


def decode_token_signature_only(
    token: str, *, request: Any = None, client_binding_id: Optional[str] = None
) -> dict:
    """Декодировать JWT и вернуть данные пользователя БЕЗ проверки активной сессии.

    Используется при WebSocket-подключении: JWT может быть валиден,
    но сессия потеряна в памяти (рестарт пода). Позволить соединиться,
    проверку сессии делаем при обработке каждого сообщения.
    """
    try:
        payload = _decode_jwt(token, JWT_ACCESS_SECRET)
        token_type: str = payload.get("token_type")
        sub: Optional[str] = _subject_from_payload(payload)
        session_id: str = payload.get("sid")
        if token_type != JWT_TYPE_ACCESS:
            logger.warning(
                "JWT ws-connect validate: неверный token_type=%s", token_type
            )
            msg = "Неверный тип токена"
            raise _jwt_auth_error(
                msg,
                reason=f"invalid token_type={token_type!r}",
                token_type=JWT_TYPE_ACCESS,
                token=token,
                username=sub,
                request=request,
            )
        if not sub:
            logger.warning("JWT ws-connect validate: отсутствует sub claim")
            msg = "Неверный токен"
            raise _jwt_auth_error(
                msg,
                reason="missing sub claim",
                token_type=JWT_TYPE_ACCESS,
                token=token,
                request=request,
            )
        _assert_client_binding(
            _resolve_client_binding_id(
                request=request, client_binding_id=client_binding_id
            ),
            sub,
            session_id,
            request=request,
            token_type=JWT_TYPE_ACCESS,
            username=sub,
            token=token,
            allow_restore=True,
        )
        logger.debug(
            "JWT ws-connect validate (signature-only): успех sub=%s sid=%s",
            sub,
            session_id,
        )
        return {**_user_dict_from_payload(payload)}
    except HTTPException:
        raise
    except ExpiredSignatureError as exc:
        logger.warning("JWT ws-connect validate: токен просрочен (exp)")
        msg = "Не удалось валидировать токен"
        raise _jwt_auth_error(
            msg,
            reason="token expired (exp)",
            token_type=JWT_TYPE_ACCESS,
            token=token,
            request=request,
        ) from exc
    except JWTError as exc:
        logger.warning(
            "JWT ws-connect validate: ошибка JWT (%s): %s", type(exc).__name__, exc
        )
        msg = "Не удалось валидировать токен"
        raise _jwt_auth_error(
            msg,
            reason=_jwt_error_reason(exc),
            token_type=JWT_TYPE_ACCESS,
            token=token,
            request=request,
        ) from exc


def decode_token(
    token: str, *, request: Any = None, client_binding_id: Optional[str] = None
) -> dict:
    """Декодировать JWT токен и вернуть данные пользователя"""
    try:
        payload = _decode_jwt(token, JWT_ACCESS_SECRET)
        token_type: str = payload.get("token_type")
        sub: Optional[str] = _subject_from_payload(payload)
        session_id: str = payload.get("sid")
        if token_type != JWT_TYPE_ACCESS:
            logger.warning("JWT access validate: неверный token_type=%s", token_type)
            msg = "Неверный тип токена"
            raise _jwt_auth_error(
                msg,
                reason=f"invalid token_type={token_type!r}",
                token_type=JWT_TYPE_ACCESS,
                token=token,
                username=sub,
                request=request,
            )
        if not sub:
            logger.warning("JWT access validate: отсутствует sub claim")
            msg = "Неверный токен"
            raise _jwt_auth_error(
                msg,
                reason="missing sub claim",
                token_type=JWT_TYPE_ACCESS,
                token=token,
                request=request,
            )
        _assert_client_binding(
            _resolve_client_binding_id(
                request=request, client_binding_id=client_binding_id
            ),
            sub,
            session_id,
            request=request,
            token_type=JWT_TYPE_ACCESS,
            username=sub,
            token=token,
        )
        _assert_active_session(
            sub, session_id, request=request, token_type=JWT_TYPE_ACCESS, username=sub
        )
        logger.debug("JWT access validate: успех sub=%s", sub)
        return _user_dict_from_payload(payload)
    except HTTPException:
        raise
    except ExpiredSignatureError as exc:
        logger.warning("JWT access validate: токен просрочен (exp)")
        msg = "Не удалось валидировать токен"
        raise _jwt_auth_error(
            msg,
            reason="token expired (exp)",
            token_type=JWT_TYPE_ACCESS,
            token=token,
            request=request,
        ) from exc
    except JWTError as exc:
        logger.warning(
            "JWT access validate: ошибка JWT (%s): %s", type(exc).__name__, exc
        )
        msg = "Не удалось валидировать токен"
        raise _jwt_auth_error(
            msg,
            reason=_jwt_error_reason(exc),
            token_type=JWT_TYPE_ACCESS,
            token=token,
            request=request,
        ) from exc


def decode_refresh_token(token: str, *, request: Any = None) -> dict:
    """Декодировать refresh JWT токен и вернуть данные пользователя."""
    try:
        payload = _decode_jwt(token, JWT_REFRESH_SECRET)
        token_type: str = payload.get("token_type")
        sub: Optional[str] = _subject_from_payload(payload)
        session_id: str = payload.get("sid")
        if token_type != JWT_TYPE_REFRESH:
            logger.warning("JWT refresh validate: неверный token_type=%s", token_type)
            msg = "Неверный тип токена"
            raise _jwt_auth_error(
                msg,
                reason=f"invalid token_type={token_type!r}",
                token_type=JWT_TYPE_REFRESH,
                token=token,
                username=sub,
                request=request,
            )
        if not sub:
            logger.warning("JWT refresh validate: отсутствует sub claim")
            msg = "Неверный refresh токен"
            raise _jwt_auth_error(
                msg,
                reason="missing sub claim",
                token_type=JWT_TYPE_REFRESH,
                token=token,
                request=request,
            )
        _assert_client_binding(
            _resolve_client_binding_id(request=request),
            sub,
            session_id,
            request=request,
            token_type=JWT_TYPE_REFRESH,
            username=sub,
            token=token,
        )
        _assert_active_session(
            sub, session_id, request=request, token_type=JWT_TYPE_REFRESH, username=sub
        )
        logger.debug("JWT refresh validate: успех sub=%s", sub)
        refresh_user = _user_dict_from_payload(payload)
        refresh_user["refresh_jti"] = payload.get("jti")
        refresh_user["refresh_exp"] = float(payload.get("exp") or 0)
        return refresh_user
    except HTTPException:
        raise
    except ExpiredSignatureError as exc:
        logger.warning("JWT refresh validate: токен просрочен (exp)")
        msg = "Не удалось валидировать refresh токен"
        raise _jwt_auth_error(
            msg,
            reason="token expired (exp)",
            token_type=JWT_TYPE_REFRESH,
            token=token,
            request=request,
        ) from exc
    except JWTError as exc:
        logger.warning(
            "JWT refresh validate: ошибка JWT (%s): %s", type(exc).__name__, exc
        )
        msg = "Не удалось валидировать refresh токен"
        raise _jwt_auth_error(
            msg,
            reason=_jwt_error_reason(exc),
            token_type=JWT_TYPE_REFRESH,
            token=token,
            request=request,
        ) from exc


async def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
) -> dict:
    """Получить текущего пользователя из токена (обязательная авторизация)"""
    token = credentials.credentials
    logger.debug("JWT auth: проверка access токена для обязательной авторизации")
    user_data = decode_token(token, request=request)
    from backend.auth.agent_platform_roles import enrich_user_feature_flags

    return enrich_user_feature_flags(
        {
            "username": user_data["username"],
            "user_id": user_data["user_id"],
            "session_id": user_data.get("session_id"),
            "email": user_data.get("email"),
            "full_name": user_data.get("full_name"),
            "is_active": True,
            "is_admin": bool(user_data.get("is_admin")),
            "groups": list(user_data.get("groups") or []),
            "ldap_groups": list(user_data.get("ldap_groups") or []),
            "auth_method": user_data.get("auth_method") or "ldap",
            "avatar_url": user_data.get("avatar_url"),
        }
    )


async def get_optional_user(
    request: Request,
    credentials: Annotated[
        Optional[HTTPAuthorizationCredentials], Depends(HTTPBearer(auto_error=False))
    ],
) -> Optional[dict]:
    """Получить текущего пользователя из токена (опциональная авторизация)"""
    if not credentials:
        logger.debug("JWT auth: опциональная авторизация без токена")
        return None
    try:
        token = credentials.credentials
        logger.debug("JWT auth: проверка access токена для опциональной авторизации")
        user_data = decode_token(token, request=request)
        from backend.auth.agent_platform_roles import enrich_user_feature_flags

        return enrich_user_feature_flags(
            {
                "username": user_data["username"],
                "user_id": user_data["user_id"],
                "session_id": user_data.get("session_id"),
                "email": user_data.get("email"),
                "full_name": user_data.get("full_name"),
                "is_active": True,
                "is_admin": bool(user_data.get("is_admin")),
                "groups": list(user_data.get("groups") or []),
                "ldap_groups": list(user_data.get("ldap_groups") or []),
                "auth_method": user_data.get("auth_method") or "ldap",
                "avatar_url": user_data.get("avatar_url"),
            }
        )
    except HTTPException:
        logger.debug(
            "JWT auth: опциональная авторизация отклонена из-за невалидного токена"
        )
        return None
