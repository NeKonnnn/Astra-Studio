"""
Глобальная (платформенная) роль пользователя для операций с агентами и skills.

Идея:
- внутренние права в БД остаются прежними: `owner | editor | viewer` (из agent_shares / автор / публичность)
- назначение ролей: ENV + LDAP/Keycloak (`groups` / `ldap_groups` в JWT)
- роли: owner > editor > viewer > base
- base (`AGENT_ROLE_BASE_USER_GROUPS`): без агентов и MCP (UI + API + чат)

Пример:

  AGENT_ROLE_BASE_USER_GROUPS: >
    (&(|(sAMAccountName={{username}})...)(|(memberOf=CN=App-NDP-D-UserRole-CORSUR,...))(objectClass=user))
    ---KEYCLOAK---
    App-NDP-D-UserRole-CORSUR
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from typing import Optional, Set

from backend.auth.ldap_auth import (
    _normalize_ldap_dn,
    extract_member_of_dns_from_search_filter,
)

ROLE_OWNER = "owner"
ROLE_EDITOR = "editor"
ROLE_VIEWER = "viewer"
ROLE_BASE = "base"

# owner/editor/viewer участвуют в cap ACL; base — без доступа к агентам/MCP
ROLE_RANK: dict[str, int] = {ROLE_VIEWER: 1, ROLE_EDITOR: 2, ROLE_OWNER: 3}

_KEYCLOAK_SECTION_MARKER = "---KEYCLOAK---"
_AGENT_ROLE_ENV_NAMES = (
    "AGENT_ROLE_OWNER_GROUPS",
    "AGENT_ROLE_EDITOR_GROUPS",
    "AGENT_ROLE_VIEWER_GROUPS",
    "AGENT_ROLE_BASE_USER_GROUPS",
    "AGENT_ROLE_OWNER_KEYCLOAK_GROUPS",
    "AGENT_ROLE_EDITOR_KEYCLOAK_GROUPS",
    "AGENT_ROLE_VIEWER_KEYCLOAK_GROUPS",
    "AGENT_ROLE_BASE_USER_KEYCLOAK_GROUPS",
)


def _split_keycloak_section(raw: str) -> tuple[str, str]:
    """Разделить LDAP-часть и Keycloak-часть в одной ENV-строке."""
    text = str(raw or "")
    marker = _KEYCLOAK_SECTION_MARKER.casefold()
    for line_no, line in enumerate(text.splitlines()):
        if line.strip().casefold() == marker:
            ldap_part = "\n".join(text.splitlines()[:line_no]).strip()
            kc_part = "\n".join(text.splitlines()[line_no + 1 :]).strip()
            return ldap_part, kc_part
    return text.strip(), ""


def _parse_simple_role_list(raw: str) -> list[str]:
    """CSV/; список имён ролей или CN (Keycloak realm/client roles, groups claim)."""
    text = str(raw or "").strip()
    if not text:
        return []
    text = text.replace(";", ",")
    return [item.strip() for item in text.split(",") if item.strip()]


def _parse_env_role_groups(raw: str) -> list[str]:
    """
    Разобрать фрагмент AGENT_ROLE_* (LDAP или простой список).

    Поддерживаются:
    - список CN/DN/имён ролей через запятую
    - полный LDAP-фильтр с ``memberOf=CN=...,OU=...`` (как LDAP_SEARCH_FILTER)
    """
    text = str(raw or "").strip()
    if not text:
        return []
    if re.search(r"(?i)memberOf=", text):
        dns = sorted(extract_member_of_dns_from_search_filter(text))
        out: list[str] = []
        seen: Set[str] = set()
        for dn in dns:
            for item in (dn, _extract_cn(dn)):
                key = item.casefold()
                if item and key not in seen:
                    seen.add(key)
                    out.append(item)
        return out
    return _parse_simple_role_list(text)


def _companion_keycloak_env_name(groups_env_name: str) -> str:
    if groups_env_name.endswith("_GROUPS"):
        return groups_env_name[: -len("_GROUPS")] + "_KEYCLOAK_GROUPS"
    return f"{groups_env_name}_KEYCLOAK_GROUPS"


def _env_role_groups(name: str) -> list[str]:
    """
    LDAP + Keycloak идентификаторы для одной платформенной роли.

    Источники (объединяются):
    - ``AGENT_ROLE_*_GROUPS`` — LDAP-фильтр / DN / CN (+ опционально ``---KEYCLOAK---``)
    - ``AGENT_ROLE_*_KEYCLOAK_GROUPS`` — отдельный CSV ролей Keycloak
    """
    raw = os.getenv(name, "")
    ldap_raw, inline_kc_raw = _split_keycloak_section(raw)
    items: list[str] = []
    items.extend(_parse_env_role_groups(ldap_raw))
    items.extend(_parse_simple_role_list(inline_kc_raw))

    companion = _companion_keycloak_env_name(name)
    companion_raw = os.getenv(companion, "")
    if companion_raw.strip():
        items.extend(_parse_simple_role_list(companion_raw))

    return items


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _extract_cn(value: str) -> str:
    """
    Пытаемся извлечь CN из DN/строки.
    Примеры входа:
      - "CN=astra-agent-viewers,OU=Groups,DC=example,DC=ru"
      - "memberOf=CN=...,OU=..."
      - "cn=GroupName"
    """
    v = str(value or "").strip()
    if not v:
        return ""
    m = re.search(r"(?i)\bcn\s*=\s*([^,]+)", v)
    return (m.group(1).strip() if m else "").strip()


def _normalize_group_identifier(value: str) -> Set[str]:
    """
    Нормализация для сравнения:
    - casefold + strip
    - extracted CN (только для LDAP DN, не для Keycloak-путей)
    - Keycloak-путь сравнивается ЦЕЛИКОМ (``/org/Owners`` ≠ ``Owners``):
      сопоставление по последнему сегменту давало повышение привилегий для
      группы ``/attackers/Owners``. Верхнеуровневая группа ``/Owners``
      эквивалентна имени ``Owners``.
    """
    v = str(value or "").strip()
    if not v:
        return set()
    out: Set[str] = {v.casefold()}
    is_keycloak_path = v.startswith("/")
    if not is_keycloak_path:
        cn = _extract_cn(v)
        if cn:
            out.add(cn.casefold())
    trimmed = v.strip("/")
    if trimmed and trimmed != v:
        out.add(trimmed.casefold())
    return out


def clear_role_cache() -> None:
    """Сбросить кэши платформенных ролей (после смены ENV / перезагрузки конфигурации).

    Вызывается из ``reset_settings()``; тесты и админ-хуки могут вызывать напрямую.
    """
    is_agent_platform_role_mapping_enabled.cache_clear()
    _allowed_sets.cache_clear()


@lru_cache(maxsize=1)
def is_agent_platform_role_mapping_enabled() -> bool:
    """
    Если ENV явно включили — используем.
    Иначе автоподключаемся, если заданы хотя бы какие-то группы для ролей.
    """
    explicit = os.getenv("AGENT_PLATFORM_ROLE_MAPPING_ENABLED")
    if explicit is not None and explicit.strip() != "":
        return _env_bool("AGENT_PLATFORM_ROLE_MAPPING_ENABLED", default=False)

    owner = _env_role_groups("AGENT_ROLE_OWNER_GROUPS")
    editor = _env_role_groups("AGENT_ROLE_EDITOR_GROUPS")
    viewer = _env_role_groups("AGENT_ROLE_VIEWER_GROUPS")
    base = _env_role_groups("AGENT_ROLE_BASE_USER_GROUPS")
    if owner or editor or viewer or base:
        return True
    for env_name in _AGENT_ROLE_ENV_NAMES[4:]:
        if _parse_simple_role_list(os.getenv(env_name, "")):
            return True
    return False


@lru_cache(maxsize=1)
def _allowed_sets() -> tuple[Set[str], Set[str], Set[str], Set[str]]:
    """
    Множества идентификаторов групп, из которых пользователь назначается на роли.

    Сами ENV-строки могут быть:
    - CN имён групп / имён ролей Keycloak
    - полными DN (LDAP)
    - LDAP-фильтр с ``memberOf=DN...``
    - доп. CSV в ``AGENT_ROLE_*_KEYCLOAK_GROUPS`` или после ``---KEYCLOAK---``
    """
    owner_groups = _env_role_groups("AGENT_ROLE_OWNER_GROUPS")
    editor_groups = _env_role_groups("AGENT_ROLE_EDITOR_GROUPS")
    viewer_groups = _env_role_groups("AGENT_ROLE_VIEWER_GROUPS")
    base_groups = _env_role_groups("AGENT_ROLE_BASE_USER_GROUPS")

    owner: Set[str] = set()
    editor: Set[str] = set()
    viewer: Set[str] = set()
    base: Set[str] = set()

    for g in owner_groups:
        owner.update(_normalize_group_identifier(g))
    for g in editor_groups:
        editor.update(_normalize_group_identifier(g))
    for g in viewer_groups:
        viewer.update(_normalize_group_identifier(g))
    for g in base_groups:
        base.update(_normalize_group_identifier(g))

    return owner, editor, viewer, base


def _user_group_identifiers(user: Optional[dict]) -> Set[str]:
    if not user or not isinstance(user, dict):
        return set()

    out: Set[str] = set()
    for g in user.get("groups") or []:
        out.update(_normalize_group_identifier(str(g)))
    for g in user.get("ldap_groups") or []:
        raw = str(g).strip()
        if not raw:
            continue
        out.add(_normalize_ldap_dn(raw))
        out.update(_normalize_group_identifier(raw))
    return out


def get_platform_role_from_group_identifiers(group_ids: Set[str]) -> Optional[str]:
    """Платформенная роль по множеству идентификаторов групп (LDAP DN / CN / Keycloak)."""
    if not is_agent_platform_role_mapping_enabled():
        return None
    if not group_ids:
        return None

    owner_set, editor_set, viewer_set, base_set = _allowed_sets()
    if owner_set and (group_ids & owner_set):
        return ROLE_OWNER
    if editor_set and (group_ids & editor_set):
        return ROLE_EDITOR
    if viewer_set and (group_ids & viewer_set):
        return ROLE_VIEWER
    if base_set and (group_ids & base_set):
        return ROLE_BASE
    return None


def get_agent_platform_role(user: Optional[dict]) -> Optional[str]:
    """
    Возвращает платформенную роль: `owner | editor | viewer | base | None`.
    """
    if not is_agent_platform_role_mapping_enabled():
        return None

    user_ids = _user_group_identifiers(user)
    return get_platform_role_from_group_identifiers(user_ids)


# CEF RoleModel (cs4): ADMIN остаётся в cef_logger; здесь — платформенные роли.
_CEF_ROLE_MODEL: dict[str, str] = {
    ROLE_OWNER: "OWNER",
    ROLE_EDITOR: "EDITOR",
    ROLE_VIEWER: "VIEWER",
    ROLE_BASE: "BASE",
}


def _role_set_for_platform_role(platform_role: str) -> Set[str]:
    owner_set, editor_set, viewer_set, base_set = _allowed_sets()
    if platform_role == ROLE_OWNER:
        return owner_set
    if platform_role == ROLE_EDITOR:
        return editor_set
    if platform_role == ROLE_VIEWER:
        return viewer_set
    if platform_role == ROLE_BASE:
        return base_set
    return set()


def _display_group_from_match(
    user: Optional[dict], role_set: Set[str]
) -> str:
    """Человекочитаемое имя группы AD/Keycloak, попавшей в role_set."""
    if not role_set or not user or not isinstance(user, dict):
        return "-"
    raw_groups: list[str] = []
    for g in list(user.get("ldap_groups") or []) + list(user.get("groups") or []):
        sg = str(g).strip()
        if sg:
            raw_groups.append(sg)
    for g in raw_groups:
        if _normalize_group_identifier(g) & role_set:
            cn = _extract_cn(g)
            if cn:
                return cn
            if "/" in g:
                return g.rsplit("/", 1)[-1].strip() or g
            return g
    # fallback: короткое имя из множества роли
    for item in sorted(role_set):
        if "=" not in item and "/" not in item:
            return item
    return sorted(role_set)[0] if role_set else "-"


def resolve_cef_role_model(user: Optional[dict]) -> tuple[str, str]:
    """
    RoleModel (cs4) и matched group (cs3) для SEC001/USR001.

    - ``is_admin`` → ``("ADMIN", "")`` — matched group заполняет cef_logger
      из LDAP/Keycloak admin groups.
    - mapping включён → ``OWNER|EDITOR|VIEWER|BASE`` + группа из ``AGENT_ROLE_*``.
      Без match (как в UI) → ``BASE``.
    - mapping выключен → ``("USER", "-")``.
    """
    if not user or not isinstance(user, dict):
        return "USER", "-"
    if user.get("is_admin"):
        return "ADMIN", ""
    if not is_agent_platform_role_mapping_enabled():
        return "USER", "-"

    role = get_agent_platform_role(user)
    if role is None:
        return "BASE", "-"
    cef_role = _CEF_ROLE_MODEL.get(role, role.upper())
    matched = _display_group_from_match(user, _role_set_for_platform_role(role))
    return cef_role, matched


def user_can_use_agents(user: Optional[dict]) -> bool:
    """
    Доступ к агентам (UI + API + чат).

    - admin → да
    - mapping выключен → да (обратная совместимость)
    - owner / editor / viewer → да
    - base / None → нет
    """
    if not user or not isinstance(user, dict):
        return False
    if user.get("is_admin"):
        return True
    if not is_agent_platform_role_mapping_enabled():
        return True
    return get_agent_platform_role(user) in (ROLE_OWNER, ROLE_EDITOR, ROLE_VIEWER)


def user_can_use_mcp(user: Optional[dict]) -> bool:
    """Доступ к MCP (UI + API + чат). Та же политика, что у агентов."""
    return user_can_use_agents(user)


def user_can_create_agents(user: Optional[dict]) -> bool:
    """
    Создание агентов и skills.

    - admin → да
    - mapping выключен → да
    - owner / editor → да
    - viewer / base / None → нет
    """
    if not user or not isinstance(user, dict):
        return False
    if user.get("is_admin"):
        return True
    if not is_agent_platform_role_mapping_enabled():
        return True
    return get_agent_platform_role(user) in (ROLE_OWNER, ROLE_EDITOR)


def user_can_publish_to_library(user: Optional[dict]) -> bool:
    """
    Публикация агентов/skills в галерею (is_public).

    - admin → да
    - mapping выключен → да
    - owner → да
    - editor / viewer / base / None → нет (editor шарит точечно через /share)
    """
    if not user or not isinstance(user, dict):
        return False
    if user.get("is_admin"):
        return True
    if not is_agent_platform_role_mapping_enabled():
        return True
    return get_agent_platform_role(user) == ROLE_OWNER


def enrich_user_feature_flags(user: Optional[dict]) -> Optional[dict]:
    """Добавить platform_role / can_use_agents / can_use_mcp в профиль пользователя."""
    if not user or not isinstance(user, dict):
        return user
    role = get_agent_platform_role(user)
    # Без match при включённом mapping трактуем как base для UI
    if is_agent_platform_role_mapping_enabled() and role is None and not user.get("is_admin"):
        display_role = ROLE_BASE
    else:
        display_role = role
    can_agents = user_can_use_agents(user)
    can_mcp = user_can_use_mcp(user)
    can_create = user_can_create_agents(user)
    can_publish = user_can_publish_to_library(user)
    user["platform_role"] = display_role
    user["can_use_agents"] = can_agents
    user["can_use_mcp"] = can_mcp
    user["can_create_agents"] = can_create
    user["can_publish_to_library"] = can_publish
    user["features"] = {
        "agents": can_agents,
        "mcp": can_mcp,
        "create_agents": can_create,
        "publish_to_library": can_publish,
    }
    return user


def cap_agent_permission(
    db_permission: Optional[str], platform_role: Optional[str]
) -> Optional[str]:
    """
    Сжать роль агента из БД (owner/editor/viewer) до максимально разрешённой платформенной роли.

    Порядок: owner > editor > viewer.
    Если platform_role == None / base — запрещаем возвращать любую роль.
    """
    if db_permission is None:
        return None
    if db_permission not in ROLE_RANK:
        return db_permission
    if platform_role is None or platform_role == ROLE_BASE:
        return None
    if platform_role not in ROLE_RANK:
        return None

    db_rank = ROLE_RANK[db_permission]
    plat_rank = ROLE_RANK[platform_role]

    if db_rank <= plat_rank:
        return db_permission

    return platform_role


def resolve_share_permission(
    requested: str,
    recipient_platform_role: Optional[str],
) -> Optional[str]:
    """
    Определить роль для записи в share-таблицу.

    Запрошенный ACL (`viewer` | `editor`) сохраняем как есть, если у получателя
    есть доступ к агентам/skills (owner/editor/viewer). Не капим по рангу
    platform-роли: LDAP-профиль получателя часто не видит Keycloak-группы
    editor и ошибочно даёт viewer — из‑за этого «editor → editor» превращался
    в «Роль понижена … (viewer)».

    При чтении явный шаринг тоже не капится (`is_shared_with_me`): иначе
    выданный editor снова показывался бы получателю как viewer.

    Returns:
        `viewer` | `editor` — роль для БД;
        `None` — шаринг запрещён (base / нет platform role).
    """
    req = str(requested or "").strip().lower()
    if req in ("use", "", "read", "reader"):
        req = ROLE_VIEWER
    elif req in ("edit", "write", "writer"):
        req = ROLE_EDITOR
    if req not in (ROLE_VIEWER, ROLE_EDITOR):
        req = ROLE_VIEWER

    if not is_agent_platform_role_mapping_enabled():
        return req

    if recipient_platform_role is None or recipient_platform_role == ROLE_BASE:
        return None
    if recipient_platform_role not in ROLE_RANK:
        return None
    return req
