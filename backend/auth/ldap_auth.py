"""
LDAP аутентификация (Active Directory) через ldap3.
"""

import os
import re
import ssl
from typing import Any, Dict, FrozenSet, List, Optional, Set
from urllib.parse import urlparse

from fastapi import HTTPException, status

try:
    from ldap3 import SIMPLE, SUBTREE, Connection, Server, Tls
    from ldap3.core.exceptions import LDAPBindError, LDAPException
    from ldap3.utils.conv import escape_filter_chars

    LDAP_AVAILABLE = True
except ImportError:
    LDAP_AVAILABLE = False

from backend.settings.logging import get_logger

logger = get_logger(__name__)
_AD_ACCOUNT_DISABLED = 2


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _split_groups(raw: str) -> List[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _extract_group_cn(group_dn: str) -> str:
    """CN=Group Name,OU=... → Group Name (для ACL access_grants)."""
    raw = str(group_dn or "").strip()
    if not raw:
        return ""
    upper = raw.upper()
    if upper.startswith("CN="):
        return raw[3:].split(",", 1)[0].strip()
    return raw


def _member_of_matches_admin_groups(
    member_of: List[str], admin_groups: List[str]
) -> bool:
    """
    is_admin по LDAP_ADMIN_GROUPS: CN, полный DN или ``cn=name,`` в memberOf.

    Раньше пустой LDAP_ADMIN_GROUPS (helm: ldap.groups ≠ admin_groups) оставлял
    LDAP-админа без is_admin — UI/API как у base, тогда как SSO (KEYCLOAK_ADMIN_GROUPS)
    работал.
    """
    if not admin_groups or not member_of:
        return False
    admin_keys: Set[str] = set()
    for raw_admin in admin_groups:
        ag = str(raw_admin or "").strip()
        if not ag:
            continue
        admin_keys.add(ag.casefold())
        admin_keys.add(_normalize_ldap_dn(ag))
        cn = _extract_group_cn(ag)
        if cn:
            admin_keys.add(cn.casefold())
    if not admin_keys:
        return False
    for group in member_of:
        raw = str(group or "").strip()
        if not raw:
            continue
        candidates = {raw.casefold(), _normalize_ldap_dn(raw)}
        cn = _extract_group_cn(raw)
        if cn:
            candidates.add(cn.casefold())
        if candidates & admin_keys:
            return True
        gl = raw.casefold()
        for key in admin_keys:
            if f"cn={key}," in gl:
                return True
    return False


def _normalize_ldap_dn(dn: str) -> str:
    """Нормализация DN для сравнения (регистр, пробелы вокруг запятых)."""
    parts = [part.strip() for part in str(dn or "").split(",") if part.strip()]
    return ",".join(parts).casefold()


def extract_member_of_dns_from_search_filter(search_filter: str) -> FrozenSet[str]:
    """Извлечь DN групп из memberOf=… в LDAP_SEARCH_FILTER (ConfigMap)."""
    if not search_filter:
        return frozenset()
    dns: Set[str] = set()
    for match in re.finditer("(?i)memberOf=([^)]+)", str(search_filter)):
        dn = match.group(1).strip()
        if dn:
            dns.add(_normalize_ldap_dn(dn))
    return frozenset(dns)


def _filter_member_of_by_search_filter(
    member_of_values: List[str], search_filter: Optional[str]
) -> List[str]:
    """Оставить только группы, перечисленные в memberOf LDAP_SEARCH_FILTER."""
    allowed = extract_member_of_dns_from_search_filter(search_filter or "")
    if not allowed:
        return []
    filtered: List[str] = []
    for group_dn in member_of_values:
        raw = str(group_dn or "").strip()
        if raw and _normalize_ldap_dn(raw) in allowed:
            filtered.append(raw)
    return filtered


def _dn_to_label(dn: str) -> str:
    raw = str(dn or "").strip()
    if not raw:
        return ""
    return _extract_group_cn(raw)


def _build_identity_search_filter(settings: Dict[str, Any], username: str) -> str:
    escaped = escape_filter_chars(username)
    username_attr = (
        str(settings.get("username_attr") or "sAMAccountName").strip()
        or "sAMAccountName"
    )
    # Диагностический фильтр без ограничений по memberOf.
    return (
        f"(|({username_attr}={escaped})(sAMAccountName={escaped})"
        f"(userPrincipalName={escaped})(cn={escaped}))"
    )


def _log_missing_required_roles(
    username: str, settings: Dict[str, Any], attrs: Optional[Dict[str, Any]]
) -> None:
    required_dns = sorted(
        extract_member_of_dns_from_search_filter(settings.get("search_filter") or "")
    )
    if not required_dns:
        return

    if not attrs:
        logger.warning(
            "LDAP auth: user=%s не прошёл фильтр ролей; required_groups=%s; user_groups=не удалось получить",
            username,
            [str(_dn_to_label(dn) or dn) for dn in required_dns],
        )
        return

    user_dns_raw = [
        str(group) for group in attrs.get("memberOf") or [] if str(group).strip()
    ]
    user_dns_normalized = {_normalize_ldap_dn(group) for group in user_dns_raw}
    missing_dns = [dn for dn in required_dns if dn not in user_dns_normalized]

    logger.warning(
        "LDAP auth: user=%s не прошёл фильтр ролей; user_groups=%s; required_groups=%s; missing_groups=%s",
        username,
        [str(_dn_to_label(dn) or dn) for dn in user_dns_raw],
        [str(_dn_to_label(dn) or dn) for dn in required_dns],
        [str(_dn_to_label(dn) or dn) for dn in missing_dns],
    )


def _diagnose_role_mismatch_with_service_bind(
    server, settings: Dict[str, Any], username: str, attributes: List[str]
) -> None:
    required_dns = extract_member_of_dns_from_search_filter(
        settings.get("search_filter") or ""
    )
    if not required_dns:
        return

    try:
        identity_filter = _build_identity_search_filter(settings, username)
        attrs, _user_dn = _service_bind_and_search(
            server, settings, username, identity_filter, attributes
        )
        _log_missing_required_roles(username, settings, attrs)
    except Exception as exc:
        logger.warning(
            "LDAP auth: не удалось собрать диагностику ролей user=%s reason=%s",
            username,
            str(exc),
        )


def _diagnose_role_mismatch_with_user_bind(
    conn: Connection, settings: Dict[str, Any], username: str, attributes: List[str]
) -> None:
    required_dns = extract_member_of_dns_from_search_filter(
        settings.get("search_filter") or ""
    )
    if not required_dns:
        return

    try:
        identity_filter = _build_identity_search_filter(settings, username)
        found = conn.search(
            search_base=settings["base_dn"],
            search_filter=identity_filter,
            search_scope=SUBTREE,
            attributes=attributes,
        )
        if not found or not conn.entries:
            _log_missing_required_roles(username, settings, None)
            return
        entry = conn.entries[0]
        attrs = entry.entry_attributes_as_dict
        _log_missing_required_roles(username, settings, attrs)
    except Exception as exc:
        logger.warning(
            "LDAP auth: не удалось собрать диагностику ролей user=%s reason=%s",
            username,
            str(exc),
        )


def _build_user_profile_from_attrs(
    attrs: Dict[str, Any], settings: Dict[str, Any], *, fallback_username: str
) -> Dict[str, Any]:
    """Собрать профиль пользователя LDAP с группами из LDAP_SEARCH_FILTER."""
    ldap_username = _extract_first(attrs, settings["id_attr"]) or fallback_username
    full_name = (
        _extract_first(attrs, settings["full_name_attr"])
        or _extract_first(attrs, "displayName")
        or _extract_first(attrs, "cn")
    )
    email = (
        _extract_first(attrs, settings["email_attr"])
        or _extract_first(attrs, "mail")
        or _extract_first(attrs, "userPrincipalName")
    )
    all_groups = [str(group) for group in attrs.get("memberOf") or []]
    # Группы из LDAP_SEARCH_FILTER — для login/ACL; полный memberOf — для
    # AGENT_ROLE_* (иначе base/owner/editor теряются и UI расходится с Keycloak).
    filter_groups = _filter_member_of_by_search_filter(
        all_groups, settings.get("search_filter")
    )
    group_names = [
        _extract_group_cn(g) for g in filter_groups if _extract_group_cn(g)
    ]
    is_admin = _member_of_matches_admin_groups(
        all_groups, settings.get("admin_groups") or []
    )
    logger.info(
        "LDAP profile: user=%s ldap_username=%s corsur_groups=%s/%s is_admin=%s",
        fallback_username,
        ldap_username,
        len(filter_groups),
        len(all_groups),
        is_admin,
    )
    profile: Dict[str, Any] = {
        "user_id": ldap_username,
        "username": ldap_username,
        "email": email,
        "full_name": full_name,
        "is_active": True,
        "is_admin": is_admin,
        "groups": group_names,
        "ldap_groups": all_groups,
    }
    if _extract_ldap_photo_bytes(attrs):
        profile["has_avatar"] = True
        profile["avatar_url"] = "/api/auth/me/avatar"
    return profile


def _domain_from_base_dn(base_dn: str) -> str:
    parts = []
    for chunk in (base_dn or "").split(","):
        piece = chunk.strip()
        if piece.lower().startswith("dc="):
            parts.append(piece[3:])
    return ".".join(parts)


def _build_filter(filter_template: str, username: str) -> str:
    escaped = escape_filter_chars(username)
    return (
        str(filter_template)
        .replace("{{username}}", escaped)
        .replace("{username}", escaped)
    )


def _get_ldap_settings() -> Dict[str, Any]:
    return {
        "enabled": _env_bool("LDAP_ENABLED", False),
        "url": os.getenv("LDAP_URL"),
        "base_dn": os.getenv("LDAP_USER_SEARCH_BASE"),
        "search_filter": os.getenv("LDAP_SEARCH_FILTER"),
        "ca_cert_path": os.getenv("LDAP_CA_CERT_PATH"),
        "bind_dn": os.getenv("LDAP_BIND_DN"),
        "bind_credentials": os.getenv("LDAP_BIND_CREDENTIALS"),
        "id_attr": os.getenv("LDAP_ID"),
        "email_attr": os.getenv("LDAP_EMAIL"),
        "full_name_attr": os.getenv("LDAP_FULL_NAME"),
        "username_attr": os.getenv("LDAP_USERNAME"),
        "login_uses_username": _env_bool("LDAP_LOGIN_USES_USERNAME", True),
        "admin_groups": _split_groups(os.getenv("LDAP_ADMIN_GROUPS", "")),
    }


def _build_server(ldap_url: str, timeout: int, ca_cert_path: Optional[str]):
    parsed = urlparse(ldap_url)
    scheme = parsed.scheme.lower() if parsed.scheme else "ldap"
    host = parsed.hostname or ldap_url
    port = parsed.port or (636 if scheme == "ldaps" else 389)
    use_ssl = scheme == "ldaps"
    tls = None
    if use_ssl:
        if ca_cert_path and os.path.exists(ca_cert_path):
            tls = Tls(validate=ssl.CERT_REQUIRED, ca_certs_file=ca_cert_path)
        elif _env_bool("LDAP_TLS_INSECURE", False):
            # Только по явному флагу: проверка сертификата LDAPS отключена
            logger.warning(
                "LDAPS: проверка сертификата ОТКЛЮЧЕНА (LDAP_TLS_INSECURE=1). "
                "Небезопасно, используйте только для отладки."
            )
            tls = Tls(validate=ssl.CERT_NONE)
        else:
            if ca_cert_path:
                logger.warning(
                    "LDAPS: CA-файл не найден (%s), используются системные CA "
                    "с проверкой сертификата",
                    ca_cert_path,
                )
            # Fail-closed: проверяем сертификат по системному хранилищу CA
            tls = Tls(validate=ssl.CERT_REQUIRED)
    logger.debug(
        "LDAP сервер инициализирован: host=%s port=%s use_ssl=%s ca_cert_configured=%s",
        host,
        port,
        use_ssl,
        bool(ca_cert_path),
    )
    return Server(
        host=host,
        port=port,
        use_ssl=use_ssl,
        connect_timeout=timeout,
        tls=tls,
        get_info=None,
    )


def _try_user_bind(
    server, username: str, password: str, settings: Dict[str, Any]
) -> Optional[Connection]:
    candidates: List[str] = []
    if settings["login_uses_username"]:
        candidates.append(username)
    domain = _domain_from_base_dn(settings["base_dn"])
    if domain and "@" not in username:
        candidates.append(f"{username}@{domain}")
    seen = set()
    logger.debug(
        "LDAP bind: подготовлены кандидаты count=%s user=%s", len(candidates), username
    )
    for bind_user in candidates:
        if bind_user in seen:
            continue
        seen.add(bind_user)
        logger.debug("LDAP bind: попытка user=%s bind_identity=%s", username, bind_user)
        try:
            conn = Connection(
                server,
                user=bind_user,
                password=password,
                authentication=SIMPLE,
                receive_timeout=10,
                auto_bind=True,
            )
            logger.debug(
                "LDAP bind: успех user=%s bind_identity=%s", username, bind_user
            )
            return conn
        except LDAPBindError as exc:
            logger.warning(
                "LDAP bind: отклонено user=%s bind_identity=%s reason=%s",
                username,
                bind_user,
                str(exc),
            )
            continue
        except LDAPException as exc:
            logger.warning(
                "LDAP bind: LDAP-исключение user=%s bind_identity=%s reason=%s",
                username,
                bind_user,
                str(exc),
            )
            continue
    logger.info("LDAP bind: все кандидаты исчерпаны user=%s", username)
    return None


def _service_bind_and_search(
    server,
    settings: Dict[str, Any],
    username: str,
    search_filter: str,
    attributes: List[str],
):
    conn = Connection(
        server,
        user=settings["bind_dn"],
        password=settings["bind_credentials"],
        authentication=SIMPLE,
        receive_timeout=10,
        auto_bind=True,
    )
    logger.debug("LDAP service bind: успех bind_dn=%s", settings["bind_dn"])
    found = conn.search(
        search_base=settings["base_dn"],
        search_filter=search_filter,
        search_scope=SUBTREE,
        attributes=attributes,
    )
    if not found or not conn.entries:
        logger.info("LDAP search: записи не найдены user=%s", username)
        conn.unbind()
        return (None, None)
    entry = conn.entries[0]
    attrs = entry.entry_attributes_as_dict
    entry_dn = str(entry.entry_dn)
    conn.unbind()
    logger.debug(
        "LDAP search: успех user=%s attrs_present=%s",
        username,
        ",".join(sorted(attrs.keys())),
    )
    return (attrs, entry_dn)


def _verify_user_password_by_dn(
    server, username: str, user_dn: str, password: str
) -> bool:
    logger.debug(
        "LDAP bind: проверка пароля пользователя через DN user=%s user_dn=%s",
        username,
        user_dn,
    )
    try:
        conn = Connection(
            server,
            user=user_dn,
            password=password,
            authentication=SIMPLE,
            receive_timeout=10,
            auto_bind=True,
        )
        conn.unbind()
        logger.debug("LDAP bind: пароль пользователя подтвержден user=%s", username)
        return True
    except LDAPBindError as exc:
        logger.warning(
            "LDAP bind: неверный пароль пользователя user=%s reason=%s",
            username,
            str(exc),
        )
        return False
    except LDAPException as exc:
        logger.warning(
            "LDAP bind: ошибка при проверке пароля user=%s reason=%s",
            username,
            str(exc),
        )
        return False


def _extract_first(attrs: Dict[str, Any], attr_name: str) -> Optional[str]:
    if not attr_name or attr_name not in attrs:
        return None
    value = attrs.get(attr_name)
    if isinstance(value, list):
        if not value:
            return None
        value = value[0]
    if value is None:
        return None
    return str(value)


def _ldap_photo_attribute_names() -> List[str]:
    raw = os.getenv("LDAP_PHOTO_ATTRS", "thumbnailPhoto,jpegPhoto")
    names = [item.strip() for item in str(raw).split(",") if item.strip()]
    return names or ["thumbnailPhoto", "jpegPhoto"]


def _extract_binary_first(attrs: Dict[str, Any], attr_name: str) -> Optional[bytes]:
    if not attr_name or attr_name not in attrs:
        return None
    value = attrs.get(attr_name)
    if isinstance(value, list):
        if not value:
            return None
        value = value[0]
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray, memoryview)):
        data = bytes(value)
        return data if data else None
    return None


def _extract_ldap_photo_bytes(attrs: Dict[str, Any]) -> Optional[bytes]:
    for attr_name in _ldap_photo_attribute_names():
        photo = _extract_binary_first(attrs, attr_name)
        if photo:
            return photo
    return None


def _ldap_lookup_attributes(settings: Dict[str, Any]) -> List[str]:
    return list(
        {
            settings["id_attr"],
            settings["email_attr"],
            settings["full_name_attr"],
            settings["username_attr"],
            "mail",
            "displayName",
            "cn",
            "memberOf",
            "userPrincipalName",
            "sAMAccountName",
            "userAccountControl",
            "lockoutTime",
            *_ldap_photo_attribute_names(),
        }
    )


def _parse_int_attr(attrs: Dict[str, Any], attr_name: str) -> Optional[int]:
    raw = _extract_first(attrs, attr_name)
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        try:
            return int(float(raw))
        except (TypeError, ValueError):
            return None


def _is_account_disabled(attrs: Dict[str, Any]) -> bool:
    uac = _parse_int_attr(attrs, "userAccountControl")
    if uac is None:
        return False
    return bool(uac & _AD_ACCOUNT_DISABLED)


def _is_account_locked(attrs: Dict[str, Any]) -> bool:
    lockout = _parse_int_attr(attrs, "lockoutTime")
    return lockout is not None and lockout > 0


def _ldap_account_status_error(attrs: Dict[str, Any]) -> Optional[str]:
    if _is_account_disabled(attrs):
        return "Учётная запись отключена в LDAP"
    if _is_account_locked(attrs):
        return "Учётная запись заблокирована в LDAP"
    return None


def _service_bind_lookup_user(
    username: str, settings: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """Найти пользователя в LDAP через service bind (без пароля пользователя)."""
    settings = settings or _get_ldap_settings()
    if not settings.get("bind_dn") or not settings.get("bind_credentials"):
        return None
    required_keys = ("url", "base_dn", "search_filter")
    missing = [key for key in required_keys if not settings.get(key)]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"LDAP конфигурация неполная. Отсутствуют: {', '.join(missing)}",
        )
    server = _build_server(settings["url"], 10, settings["ca_cert_path"])
    search_filter = _build_filter(settings["search_filter"], username)
    attributes = _ldap_lookup_attributes(settings)
    attrs, _user_dn = _service_bind_and_search(
        server, settings, username, search_filter, attributes
    )
    return attrs


def validate_ldap_user_account(username: str, user_id: Optional[str] = None) -> None:
    """Проверить, что учётная запись существует и активна в LDAP/AD.

    Raises:
        HTTPException 401 — учётная запись не найдена, отключена или заблокирована.
        HTTPException 503 — LDAP временно недоступен (сессию не завершаем).
    """
    if not is_ldap_enabled():
        return
    settings = _get_ldap_settings()
    if not settings.get("bind_dn") or not settings.get("bind_credentials"):
        logger.debug(
            "LDAP validate: пропуск, service bind не настроен user=%s user_id=%s",
            username,
            user_id,
        )
        return
    lookup_names: List[str] = []
    for name in (username, user_id):
        normalized = (name or "").strip()
        if normalized and normalized not in lookup_names:
            lookup_names.append(normalized)
    for lookup_name in lookup_names:
        try:
            attrs = _service_bind_lookup_user(lookup_name, settings)
        except HTTPException:
            raise
        except LDAPBindError as exc:
            logger.warning(
                "LDAP validate: service bind отклонён user=%s reason=%s",
                lookup_name,
                str(exc),
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="LDAP сервер недоступен",
            ) from exc
        except LDAPException as exc:
            logger.warning(
                "LDAP validate: LDAP-исключение user=%s reason=%s",
                lookup_name,
                str(exc),
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="LDAP сервер недоступен",
            ) from exc
        if not attrs:
            continue
        status_error = _ldap_account_status_error(attrs)
        if status_error:
            logger.debug(
                "LDAP validate: отклонено user=%s lookup=%s reason=%s",
                username,
                lookup_name,
                status_error,
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail=status_error
            )
        return
    logger.info(
        "LDAP validate: учётная запись не найдена user=%s user_id=%s", username, user_id
    )
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Учётная запись не найдена в LDAP",
    )


def fetch_ldap_user_photo(
    username: str, user_id: Optional[str] = None
) -> Optional[bytes]:
    """Фото пользователя из LDAP/AD (thumbnailPhoto / jpegPhoto)."""
    if not LDAP_AVAILABLE or not is_ldap_enabled():
        return None
    settings = _get_ldap_settings()
    if not settings.get("bind_dn") or not settings.get("bind_credentials"):
        return None
    lookup_names: List[str] = []
    for name in (username, user_id):
        normalized = (name or "").strip()
        if normalized and normalized not in lookup_names:
            lookup_names.append(normalized)
    for lookup_name in lookup_names:
        try:
            attrs = _service_bind_lookup_user(lookup_name, settings)
        except HTTPException:
            raise
        except (LDAPBindError, LDAPException):
            return None
        if not attrs:
            continue
        photo = _extract_ldap_photo_bytes(attrs)
        if photo:
            return photo
    return None


def fetch_ldap_user_profile(
    username: str, user_id: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Профиль пользователя из LDAP (service bind), с группами из LDAP_SEARCH_FILTER."""
    if not LDAP_AVAILABLE or not is_ldap_enabled():
        return None
    settings = _get_ldap_settings()
    if not settings.get("bind_dn") or not settings.get("bind_credentials"):
        return None
    lookup_names: List[str] = []
    for name in (username, user_id):
        normalized = (name or "").strip()
        if normalized and normalized not in lookup_names:
            lookup_names.append(normalized)
    for lookup_name in lookup_names:
        try:
            attrs = _service_bind_lookup_user(lookup_name, settings)
        except HTTPException:
            raise
        except (LDAPBindError, LDAPException):
            return None
        if not attrs:
            continue
        if _ldap_account_status_error(attrs):
            return None
        return _build_user_profile_from_attrs(
            attrs, settings, fallback_username=lookup_name
        )
    return None


def authenticate_ldap(username: str, password: str) -> Optional[Dict]:
    """Аутентификация пользователя через LDAP/AD."""
    settings = _get_ldap_settings()
    if not settings["enabled"]:
        logger.info("LDAP auth: пропуск, LDAP отключен user=%s", username)
        return None
    if not password:
        logger.info("LDAP auth: пустой пароль user=%s", username)
        return None
    if not LDAP_AVAILABLE:
        logger.exception("LDAP auth: недоступно, модуль ldap3 не установлен user=%s")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LDAP аутентификация недоступна: модуль ldap3 не установлен",
        )
    try:
        required_keys = (
            "url",
            "base_dn",
            "search_filter",
            "id_attr",
            "email_attr",
            "full_name_attr",
            "username_attr",
        )
        missing = [key for key in required_keys if not settings.get(key)]
        if missing:
            logger.error(
                "LDAP config: неполная конфигурация missing=%s", ",".join(missing)
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"LDAP конфигурация неполная. Отсутствуют: {', '.join(missing)}",
            )
        if bool(settings.get("bind_dn")) != bool(settings.get("bind_credentials")):
            logger.error(
                "LDAP config: bind_dn и bind_credentials должны быть заданы вместе"
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="LDAP конфигурация неполная. Укажите одновременно LDAP_BIND_DN и LDAP_BIND_CREDENTIALS",
            )
        logger.debug(
            "LDAP auth: старт user=%s base_dn=%s", username, settings["base_dn"]
        )
        server = _build_server(settings["url"], 10, settings["ca_cert_path"])
        search_filter = _build_filter(settings["search_filter"], username)
        attributes = _ldap_lookup_attributes(settings)
        logger.debug(
            "LDAP search: старт user=%s base_dn=%s attrs_count=%s",
            username,
            settings["base_dn"],
            len(attributes),
        )
        logger.debug("LDAP search: фильтр user=%s filter=%s", username, search_filter)
        if settings.get("bind_dn") and settings.get("bind_credentials"):
            logger.debug(
                "LDAP auth: используется service bind для поиска user=%s", username
            )
            attrs, user_dn = _service_bind_and_search(
                server, settings, username, search_filter, attributes
            )
            if not attrs or not user_dn:
                _diagnose_role_mismatch_with_service_bind(
                    server, settings, username, attributes
                )
                return None
            if not _verify_user_password_by_dn(server, username, user_dn, password):
                logger.info(
                    "LDAP auth: неуспешно на этапе проверки пароля user=%s", username
                )
                return None
        else:
            conn = _try_user_bind(server, username, password, settings)
            if conn is None:
                logger.info("LDAP auth: неуспешно на этапе bind user=%s", username)
                return None
            found = conn.search(
                search_base=settings["base_dn"],
                search_filter=search_filter,
                search_scope=SUBTREE,
                attributes=attributes,
            )
            if not found or not conn.entries:
                logger.info("LDAP search: записи не найдены user=%s", username)
                _diagnose_role_mismatch_with_user_bind(
                    conn, settings, username, attributes
                )
                conn.unbind()
                return None
            entry = conn.entries[0]
            attrs = entry.entry_attributes_as_dict
            conn.unbind()
            logger.debug(
                "LDAP search: успех user=%s attrs_present=%s",
                username,
                ",".join(sorted(attrs.keys())),
            )
        status_error = _ldap_account_status_error(attrs)
        if status_error:
            logger.info(
                "LDAP auth: отклонено user=%s reason=%s", username, status_error
            )
            return None
        return _build_user_profile_from_attrs(
            attrs, settings, fallback_username=username
        )
    except LDAPBindError as exc:
        logger.warning("LDAP auth: ошибка bind user=%s reason=%s", username, str(exc))
        return None
    except LDAPException as exc:
        logger.exception("LDAP auth: LDAP-исключение user=%s", username)
        if os.getenv("DEBUG", "false").lower() == "true":
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"LDAP ошибка: {str(exc)}",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LDAP сервер недоступен или вернул ошибку",
        ) from exc
    except Exception as exc:
        logger.exception("LDAP auth: непредвиденная ошибка user=%s", username)
        if os.getenv("DEBUG", "false").lower() == "true":
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Ошибка LDAP аутентификации: {str(exc)}",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка аутентификации",
        ) from exc


def _extract_member_of_dns_raw(search_filter: str) -> List[str]:
    """Оригинальные DN групп из LDAP_SEARCH_FILTER (для LDAP-фильтра поиска)."""
    if not search_filter:
        return []
    dns: List[str] = []
    for match in re.finditer("(?i)memberOf=([^)]+)", str(search_filter)):
        dn = match.group(1).strip()
        if dn:
            dns.append(dn)
    return dns


def _build_directory_scope_filter(settings: Dict[str, Any]) -> str:
    """Ограничения LDAP_SEARCH_FILTER (группы + тип объекта) без username."""
    member_dns = _extract_member_of_dns_raw(settings.get("search_filter") or "")
    parts: List[str] = ["(objectCategory=person)", "(objectClass=user)"]
    if member_dns:
        group_clause = (
            f"(memberOf={member_dns[0]})"
            if len(member_dns) == 1
            else f"(|{''.join(f'(memberOf={dn})' for dn in member_dns)})"
        )
        parts.append(group_clause)
    return f"(&{''.join(parts)})"


def _ldap_directory_search_attributes(settings: Dict[str, Any]) -> List[str]:
    """Минимальный набор атрибутов для автодополнения (без memberOf / фото)."""
    return list(
        {
            settings["id_attr"],
            settings["email_attr"],
            settings["full_name_attr"],
            settings["username_attr"],
            "mail",
            "displayName",
            "cn",
            "sAMAccountName",
            "userPrincipalName",
            "userAccountControl",
        }
    )


def _ldap_profile_lookup_attributes(settings: Dict[str, Any]) -> List[str]:
    """Атрибуты для пакетного резолва ФИО по логинам."""
    return list(
        {
            settings["id_attr"],
            settings["email_attr"],
            settings["full_name_attr"],
            settings["username_attr"],
            "mail",
            "displayName",
            "cn",
            "sAMAccountName",
            "userPrincipalName",
            "userAccountControl",
        }
    )


def _build_directory_text_filter(settings: Dict[str, Any], query: str) -> str:
    """Фильтр поиска по типу ввода: логин (prefix), email, ФИО (substring)."""
    q = (query or "").strip()
    escaped = escape_filter_chars(q)
    username_attr = (
        str(settings.get("username_attr") or "sAMAccountName").strip()
        or "sAMAccountName"
    )
    email_attr = str(settings.get("email_attr") or "mail").strip() or "mail"
    full_name_attr = (
        str(settings.get("full_name_attr") or "displayName").strip() or "displayName"
    )
    id_attr = str(settings.get("id_attr") or username_attr).strip() or username_attr

    if "@" in q:
        return f"(|({email_attr}={escaped}*)(mail={escaped}*)(userPrincipalName={escaped}*))"

    if re.fullmatch(r"[a-zA-Z0-9._-]+", q):
        return (
            f"(|({username_attr}={escaped}*)(sAMAccountName={escaped}*)"
            f"({id_attr}={escaped}*)(userPrincipalName={escaped}*))"
        )

    clauses: List[str] = [
        f"({full_name_attr}=*{escaped}*)",
        f"(displayName=*{escaped}*)",
        f"(cn=*{escaped}*)",
        f"({username_attr}={escaped}*)",
        f"(mail={escaped}*)",
    ]
    return f"(|{''.join(clauses)})"


def _build_directory_search_filter(settings: Dict[str, Any], query: str) -> str:
    """Полный LDAP-фильтр: scope (группы) + текстовый поиск."""
    text_filter = _build_directory_text_filter(settings, query)
    scope_filter = _build_directory_scope_filter(settings)
    return f"(&{scope_filter}{text_filter})"


def _ldap_service_search(
    settings: Dict[str, Any],
    search_filter: str,
    attributes: List[str],
    *,
    size_limit: int,
) -> List[Dict[str, Any]]:
    """Один service-bind + search; возвращает attrs по каждой записи."""
    server = _build_server(settings["url"], 10, settings["ca_cert_path"])
    try:
        conn = Connection(
            server,
            user=settings["bind_dn"],
            password=settings["bind_credentials"],
            authentication=SIMPLE,
            receive_timeout=10,
            auto_bind=True,
        )
    except (LDAPBindError, LDAPException) as exc:
        logger.warning("LDAP search: service bind failed reason=%s", str(exc))
        return []

    entries: List[Dict[str, Any]] = []
    try:
        try:
            found = conn.search(
                search_base=settings["base_dn"],
                search_filter=search_filter,
                search_scope=SUBTREE,
                attributes=attributes,
                size_limit=size_limit,
            )
        except LDAPException as size_exc:
            logger.debug(
                "LDAP search: size/limit notice filter=%s reason=%s",
                search_filter[:120],
                str(size_exc),
            )
            found = bool(conn.entries)
        if found and conn.entries:
            entries = [entry.entry_attributes_as_dict for entry in conn.entries]
    except LDAPException as exc:
        logger.warning(
            "LDAP search: ошибка filter=%s reason=%s", search_filter[:120], str(exc)
        )
    finally:
        try:
            conn.unbind()
        except Exception:
            pass
    return entries


def _directory_hit_from_attrs(
    attrs: Dict[str, Any], settings: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    if _ldap_account_status_error(attrs):
        return None
    profile = _build_user_profile_from_attrs(
        attrs,
        settings,
        fallback_username=_extract_first(attrs, settings.get("username_attr") or "")
        or "",
    )
    uid = (profile.get("user_id") or profile.get("username") or "").strip()
    if not uid:
        return None
    return {
        "user_id": uid,
        "username": uid,
        "full_name": profile.get("full_name") or None,
        "email": profile.get("email") or None,
    }


def fetch_ldap_user_profiles_bulk(usernames: List[str]) -> Dict[str, Dict[str, Any]]:
    """Пакетный поиск профилей по логинам (один LDAP round-trip)."""
    if not LDAP_AVAILABLE or not is_ldap_enabled():
        return {}
    unique: List[str] = []
    seen: Set[str] = set()
    for raw in usernames:
        uid = (raw or "").strip()
        if not uid:
            continue
        key = uid.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(uid)
    if not unique:
        return {}

    settings = _get_ldap_settings()
    if not settings.get("bind_dn") or not settings.get("bind_credentials"):
        return {}
    if not settings.get("url") or not settings.get("base_dn"):
        return {}

    username_attr = (
        str(settings.get("username_attr") or "sAMAccountName").strip()
        or "sAMAccountName"
    )
    id_attr = str(settings.get("id_attr") or username_attr).strip() or username_attr
    identity_clauses: List[str] = []
    for uid in unique:
        escaped = escape_filter_chars(uid)
        identity_clauses.extend(
            [
                f"({username_attr}={escaped})",
                f"(sAMAccountName={escaped})",
                f"({id_attr}={escaped})",
                f"(userPrincipalName={escaped})",
            ]
        )
    identity_filter = f"(|{''.join(identity_clauses)})"
    search_filter = f"(&{_build_directory_scope_filter(settings)}{identity_filter})"
    attributes = _ldap_profile_lookup_attributes(settings)

    entries = _ldap_service_search(
        settings,
        search_filter,
        attributes,
        size_limit=max(len(unique) * 2, 16),
    )

    by_login: Dict[str, Dict[str, Any]] = {}
    for attrs in entries:
        hit = _directory_hit_from_attrs(attrs, settings)
        if not hit:
            continue
        for key in (hit["user_id"], hit.get("username") or ""):
            normalized = str(key or "").strip().lower()
            if normalized:
                by_login[normalized] = hit

    result: Dict[str, Dict[str, Any]] = {}
    for uid in unique:
        hit = by_login.get(uid.lower())
        if hit:
            result[uid.lower()] = hit
    return result


def _ldap_group_lookup_attributes(settings: Dict[str, Any]) -> List[str]:
    """Атрибуты для резолва групп пользователя (memberOf) при share."""
    attrs = _ldap_profile_lookup_attributes(settings)
    attrs.append("memberOf")
    return list(dict.fromkeys(attrs))


def fetch_ldap_user_group_profiles_bulk(
    usernames: List[str],
) -> Dict[str, Dict[str, Any]]:
    """Пакетный поиск профилей с группами LDAP (memberOf) для проверки platform role."""
    if not LDAP_AVAILABLE or not is_ldap_enabled():
        return {}
    unique: List[str] = []
    seen: Set[str] = set()
    for raw in usernames:
        uid = (raw or "").strip()
        if not uid:
            continue
        key = uid.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(uid)
    if not unique:
        return {}

    settings = _get_ldap_settings()
    if not settings.get("bind_dn") or not settings.get("bind_credentials"):
        return {}
    if not settings.get("url") or not settings.get("base_dn"):
        return {}

    username_attr = (
        str(settings.get("username_attr") or "sAMAccountName").strip()
        or "sAMAccountName"
    )
    id_attr = str(settings.get("id_attr") or username_attr).strip() or username_attr
    identity_clauses: List[str] = []
    for uid in unique:
        escaped = escape_filter_chars(uid)
        identity_clauses.extend(
            [
                f"({username_attr}={escaped})",
                f"(sAMAccountName={escaped})",
                f"({id_attr}={escaped})",
                f"(userPrincipalName={escaped})",
            ]
        )
    identity_filter = f"(|{''.join(identity_clauses)})"
    search_filter = f"(&{_build_directory_scope_filter(settings)}{identity_filter})"
    attributes = _ldap_group_lookup_attributes(settings)

    entries = _ldap_service_search(
        settings,
        search_filter,
        attributes,
        size_limit=max(len(unique) * 2, 16),
    )

    by_login: Dict[str, Dict[str, Any]] = {}
    for attrs in entries:
        if _ldap_account_status_error(attrs):
            continue
        profile = _build_user_profile_from_attrs(
            attrs,
            settings,
            fallback_username=_extract_first(attrs, settings.get("username_attr") or "")
            or "",
        )
        uid = (profile.get("user_id") or profile.get("username") or "").strip()
        if not uid:
            continue
        for key in (uid, profile.get("username") or ""):
            normalized = str(key or "").strip().lower()
            if normalized:
                by_login[normalized] = profile

    result: Dict[str, Dict[str, Any]] = {}
    for uid in unique:
        profile = by_login.get(uid.lower())
        if profile:
            result[uid.lower()] = profile
    return result


def _ldap_user_has_share_platform_role(
    attrs: Dict[str, Any], settings: Dict[str, Any]
) -> bool:
    """Есть ли у пользователя хотя бы viewer platform role (для фильтра people picker)."""
    try:
        from backend.auth.agent_platform_roles import (
            get_platform_role_from_group_identifiers,
            is_agent_platform_role_mapping_enabled,
        )
    except Exception:
        return True

    if not is_agent_platform_role_mapping_enabled():
        return True

    profile = _build_user_profile_from_attrs(
        attrs,
        settings,
        fallback_username=_extract_first(attrs, settings.get("username_attr") or "")
        or "",
    )
    group_ids: Set[str] = set()
    from backend.auth.agent_platform_roles import _normalize_group_identifier

    for g in profile.get("groups") or []:
        group_ids.update(_normalize_group_identifier(str(g)))
    for g in profile.get("ldap_groups") or []:
        group_ids.update(_normalize_group_identifier(str(g)))
    return get_platform_role_from_group_identifiers(group_ids) is not None


def search_ldap_users(
    query: str, limit: int = 10, *, for_share: bool = False
) -> List[Dict[str, Any]]:
    """Поиск пользователей в LDAP по gpbu / ФИО / email (service bind).

    Returns:
        Список словарей: user_id, username, full_name, email.
    """
    if not LDAP_AVAILABLE or not is_ldap_enabled():
        return []
    q = (query or "").strip()
    if len(q) < 2:
        return []
    limit = max(1, min(int(limit or 10), 20))

    settings = _get_ldap_settings()
    if not settings.get("bind_dn") or not settings.get("bind_credentials"):
        return []
    required_keys = ("url", "base_dn")
    missing = [key for key in required_keys if not settings.get(key)]
    if missing:
        logger.warning(
            "LDAP search: неполная конфигурация missing=%s", ",".join(missing)
        )
        return []

    search_filter = _build_directory_search_filter(settings, q)
    if for_share:
        attributes = _ldap_group_lookup_attributes(settings)
    else:
        attributes = _ldap_directory_search_attributes(settings)

    fetch_limit = limit * 4 if for_share else limit * 2
    entries = _ldap_service_search(
        settings,
        search_filter,
        attributes,
        size_limit=fetch_limit,
    )

    results: List[Dict[str, Any]] = []
    seen_users: set = set()
    for attrs in entries:
        if len(results) >= limit:
            break
        if for_share and not _ldap_user_has_share_platform_role(attrs, settings):
            continue
        hit = _directory_hit_from_attrs(attrs, settings)
        if not hit:
            continue
        key = hit["user_id"].lower()
        if key in seen_users:
            continue
        seen_users.add(key)
        results.append(hit)

    return results


def is_ldap_enabled() -> bool:
    """Проверить, включена ли LDAP аутентификация."""
    settings = _get_ldap_settings()
    return settings["enabled"] and LDAP_AVAILABLE
