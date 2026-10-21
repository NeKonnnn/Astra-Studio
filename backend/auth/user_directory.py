"""Резолв ФИО и поиск пользователей в LDAP (общий для agents/skills/share)."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional, Tuple

from backend.settings.logging import get_logger

logger = get_logger(__name__)

# Кэш ФИО по gpbu (логин один и тот же для LDAP и SSO)
_full_name_cache: Dict[str, Optional[str]] = {}
_SEARCH_CACHE_TTL_SEC = 90.0
_SEARCH_CACHE_MAX = 256
_search_cache: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}


def _search_cache_get(key: str) -> Optional[List[Dict[str, Any]]]:
    row = _search_cache.get(key)
    if not row:
        return None
    cached_at, users = row
    if time.monotonic() - cached_at > _SEARCH_CACHE_TTL_SEC:
        _search_cache.pop(key, None)
        return None
    return users


def _search_cache_put(key: str, users: List[Dict[str, Any]]) -> None:
    if len(_search_cache) >= _SEARCH_CACHE_MAX:
        oldest_key = min(_search_cache, key=lambda k: _search_cache[k][0])
        _search_cache.pop(oldest_key, None)
    _search_cache[key] = (time.monotonic(), users)


async def resolve_full_names(user_ids: List[str]) -> Dict[str, Optional[str]]:
    """ФИО по списку gpbu из LDAP (service bind). Логин одинаков для LDAP и SSO."""
    result: Dict[str, Optional[str]] = {}
    to_lookup: List[str] = []
    seen: set = set()
    for raw in user_ids:
        uid = (raw or "").strip()
        if not uid:
            continue
        key = uid.lower()
        if key in seen:
            continue
        seen.add(key)
        if key in _full_name_cache:
            result[uid] = _full_name_cache[key]
        else:
            to_lookup.append(uid)

    if not to_lookup:
        return result

    try:
        from backend.auth.ldap_auth import (
            fetch_ldap_user_profiles_bulk,
            is_ldap_enabled,
        )
    except Exception:  # pragma: no cover
        for uid in to_lookup:
            result[uid] = None
        return result

    if not is_ldap_enabled():
        for uid in to_lookup:
            result[uid] = None
        return result

    def _bulk() -> Dict[str, Optional[str]]:
        out: Dict[str, Optional[str]] = {}
        try:
            profiles = fetch_ldap_user_profiles_bulk(to_lookup)
        except Exception:
            logger.exception("Ошибка пакетного резолва ФИО по gpbu")
            profiles = {}
        for uid in to_lookup:
            profile = profiles.get(uid.lower())
            name = profile.get("full_name") if profile else None
            out[uid] = name
        return out

    try:
        looked = await asyncio.to_thread(_bulk)
    except Exception:
        logger.exception("Ошибка резолва ФИО по gpbu")
        looked = {uid: None for uid in to_lookup}

    for uid, name in looked.items():
        _full_name_cache[uid.lower()] = name
        result[uid] = name
    return result


async def enrich_items_author_full_names(items: List[Any]) -> None:
    """Проставить author_full_name у объектов с author_id / author_name."""
    if not items:
        return
    ids: List[str] = []
    for item in items:
        aid = getattr(item, "author_id", None) or getattr(item, "author_name", None)
        if aid:
            ids.append(str(aid))
    name_map = await resolve_full_names(ids)
    if not name_map:
        for item in items:
            if hasattr(item, "author_full_name"):
                try:
                    item.author_full_name = None
                except Exception:
                    pass
        return

    lower_map = {k.lower(): v for k, v in name_map.items()}
    for item in items:
        aid = (
            getattr(item, "author_id", None) or getattr(item, "author_name", None) or ""
        )
        key = str(aid).strip()
        name = name_map.get(key)
        if name is None and key:
            name = lower_map.get(key.lower())
        try:
            item.author_full_name = name
        except Exception:
            pass


async def search_directory_users(
    query: str, limit: int = 10, *, for_share: bool = False
) -> List[Dict[str, Any]]:
    """Поиск пользователей по gpbu / ФИО / email через LDAP."""
    q = (query or "").strip()
    if len(q) < 2:
        return []
    limit = max(1, min(int(limit or 10), 20))
    cache_key = f"{q.casefold()}:{limit}:share={int(for_share)}"
    cached = _search_cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        from backend.auth.ldap_auth import is_ldap_enabled, search_ldap_users
    except Exception:
        return []

    if not is_ldap_enabled():
        return []

    try:
        hits = await asyncio.to_thread(search_ldap_users, q, limit, for_share=for_share)
    except Exception:
        logger.exception("Ошибка LDAP search users q=%s", q)
        return []

    _search_cache_put(cache_key, hits)
    return hits
