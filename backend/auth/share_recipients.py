"""Валидация получателей при шаринге агентов и skills."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from backend.auth.agent_platform_roles import (
    ROLE_EDITOR,
    ROLE_VIEWER,
    get_platform_role_from_group_identifiers,
    is_agent_platform_role_mapping_enabled,
    resolve_share_permission,
)
from backend.settings.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ShareRecipientSkip:
    user_id: str
    reason: str


@dataclass
class ShareRecipientCap:
    user_id: str
    requested: str
    granted: str


@dataclass
class ShareRecipientsPrepareResult:
    """Результат подготовки списка получателей для share."""

    to_share: List[Tuple[str, str]] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    skipped_details: List[ShareRecipientSkip] = field(default_factory=list)
    capped: List[ShareRecipientCap] = field(default_factory=list)


def _normalize_requested_permission(requested: str) -> str:
    req = (requested or "").strip().lower()
    if req in ("use", "", "read", "reader"):
        return ROLE_VIEWER
    if req in ("edit", "write", "writer"):
        return ROLE_EDITOR
    if req in (ROLE_VIEWER, ROLE_EDITOR):
        return req
    return ROLE_VIEWER


def _group_identifiers_from_profile(profile: dict) -> Set[str]:
    from backend.auth.agent_platform_roles import _normalize_group_identifier

    out: Set[str] = set()
    for g in profile.get("groups") or []:
        out.update(_normalize_group_identifier(str(g)))
    for g in profile.get("ldap_groups") or []:
        out.update(_normalize_group_identifier(str(g)))
    return out


async def _fetch_recipient_profiles(usernames: List[str]) -> Dict[str, Optional[dict]]:
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

    try:
        from backend.auth.ldap_auth import (
            fetch_ldap_user_group_profiles_bulk,
            is_ldap_enabled,
        )
    except Exception:
        return {uid.lower(): None for uid in unique}

    if not is_ldap_enabled():
        return {uid.lower(): None for uid in unique}

    def _bulk() -> Dict[str, Optional[dict]]:
        try:
            return fetch_ldap_user_group_profiles_bulk(unique)
        except Exception:
            logger.exception("Ошибка bulk lookup групп получателей share")
            return {}

    profiles = await asyncio.to_thread(_bulk)
    return {uid.lower(): profiles.get(uid.lower()) for uid in unique}


async def prepare_share_recipients(
    usernames: List[str],
    requested_permission: str,
) -> ShareRecipientsPrepareResult:
    """
    Проверить получателей перед share: существование в LDAP и platform role.

    При выключенном AGENT_PLATFORM_ROLE_MAPPING — возвращает всех с запрошенной ролью
    (поведение как раньше, без LDAP-проверки).
    """
    result = ShareRecipientsPrepareResult()
    mapping_enabled = is_agent_platform_role_mapping_enabled()
    req = _normalize_requested_permission(requested_permission)

    profiles: Dict[str, Optional[dict]] = {}
    if mapping_enabled:
        profiles = await _fetch_recipient_profiles(usernames)

    for raw in usernames:
        recipient = (raw or "").strip().lower()
        if not recipient:
            result.skipped.append(raw or "")
            result.skipped_details.append(
                ShareRecipientSkip(user_id=raw or "", reason="Пустой логин")
            )
            continue

        if not mapping_enabled:
            result.to_share.append((recipient, req))
            continue

        profile = profiles.get(recipient)
        if not profile:
            result.skipped.append(recipient)
            result.skipped_details.append(
                ShareRecipientSkip(
                    user_id=recipient,
                    reason="Пользователь не найден в каталоге или не имеет доступа к платформе",
                )
            )
            continue

        group_ids = _group_identifiers_from_profile(profile)
        platform_role = get_platform_role_from_group_identifiers(group_ids)
        granted = resolve_share_permission(req, platform_role)

        if granted is None:
            result.skipped.append(recipient)
            result.skipped_details.append(
                ShareRecipientSkip(
                    user_id=recipient,
                    reason="Нет платформенной роли для доступа к агентам и skills",
                )
            )
            continue

        if granted != req:
            result.capped.append(
                ShareRecipientCap(user_id=recipient, requested=req, granted=granted)
            )

        result.to_share.append((recipient, granted))

    return result


def build_share_api_response(
    prepared: ShareRecipientsPrepareResult,
    added: List[str],
    repo_skipped: List[str],
    *,
    resource_label: str,
) -> dict:
    """Собрать JSON-ответ POST /share с детализацией skipped/capped."""
    all_skipped = list(dict.fromkeys([*prepared.skipped, *repo_skipped]))
    capped_payload = [
        {"user_id": c.user_id, "requested": c.requested, "granted": c.granted}
        for c in prepared.capped
    ]
    skipped_details = [
        {"user_id": s.user_id, "reason": s.reason} for s in prepared.skipped_details
    ]
    if repo_skipped:
        for uid in repo_skipped:
            if uid and not any(d["user_id"] == uid for d in skipped_details):
                skipped_details.append(
                    {"user_id": uid, "reason": "Не удалось выдать доступ"}
                )

    messages: List[str] = []
    if added:
        messages.append(f"{resource_label} доступен для: {', '.join(added)}")
    if capped_payload:
        capped_users = ", ".join(
            f"{c['user_id']} ({c['granted']})" for c in capped_payload
        )
        messages.append(f"Роль понижена для: {capped_users}")
    if skipped_details and not added:
        reasons = "; ".join(
            f"{d['user_id']}: {d['reason']}" for d in skipped_details[:3]
        )
        messages.append(reasons)

    return {
        "success": bool(added),
        "shared_with": added,
        "skipped": all_skipped,
        "skipped_details": skipped_details,
        "capped": capped_payload,
        "message": messages[0] if messages else "Никого не добавлено",
        "warnings": messages[1:] if len(messages) > 1 else [],
    }
