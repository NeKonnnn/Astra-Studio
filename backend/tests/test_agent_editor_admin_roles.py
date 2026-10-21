# -*- coding: utf-8 -*-
"""Права platform admin / editor на агентов и skills: create, publish, share, delete.

Изолированная загрузка модулей (без backend.__init__ / asyncpg) —
как в test_agent_clone.py. Запуск: ``python backend/tests/test_agent_editor_admin_roles.py -v``.
"""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

_ROOT = Path(__file__).resolve().parents[2]


def _ensure_pkg(name: str, path: Optional[Path] = None):
    if name in sys.modules:
        return sys.modules[name]
    mod = types.ModuleType(name)
    if path is not None:
        mod.__path__ = [str(path)]  # type: ignore[attr-defined]
    sys.modules[name] = mod
    return mod


def _prepared_share(usernames: Optional[List[str]] = None):
    users = usernames or ["peer1"]
    return SimpleNamespace(
        to_share=[(u, "viewer") for u in users],
        skipped=[],
        skipped_details=[],
        capped=[],
    )


def _load_modules():
    """roles + api_agents + api_skills с заглушками зависимостей."""
    cached = (
        sys.modules.get("backend.auth.agent_platform_roles"),
        sys.modules.get("backend.api_agents"),
        sys.modules.get("backend.api_skills"),
    )
    if (
        cached[0]
        and cached[1]
        and hasattr(cached[1], "create_agent")
        and cached[2]
        and hasattr(cached[2], "create_skill")
    ):
        return cached

    from pydantic import BaseModel
    from typing import Any as TypingAny

    _ensure_pkg("backend", _ROOT / "backend")
    _ensure_pkg("backend.auth", _ROOT / "backend" / "auth")
    _ensure_pkg("backend.database", _ROOT / "backend" / "database")
    _ensure_pkg(
        "backend.database.postgresql", _ROOT / "backend" / "database" / "postgresql"
    )
    _ensure_pkg("backend.database.init_db")
    _ensure_pkg("backend.settings", _ROOT / "backend" / "settings")
    _ensure_pkg("backend.settings.cef_logger", _ROOT / "backend" / "settings" / "cef_logger")
    _ensure_pkg("backend.settings.logging", _ROOT / "backend" / "settings" / "logging")
    _ensure_pkg("backend.agents", _ROOT / "backend" / "agents")
    _ensure_pkg("backend.agents.subagents", _ROOT / "backend" / "agents" / "subagents")

    ldap = _ensure_pkg("backend.auth.ldap_auth")
    ldap._normalize_ldap_dn = lambda x: x  # type: ignore[attr-defined]
    ldap.extract_member_of_dns_from_search_filter = lambda x: set()  # type: ignore[attr-defined]

    if "backend.auth.agent_platform_roles" not in sys.modules or not hasattr(
        sys.modules["backend.auth.agent_platform_roles"], "user_can_publish_to_library"
    ):
        roles_path = _ROOT / "backend" / "auth" / "agent_platform_roles.py"
        roles_spec = importlib.util.spec_from_file_location(
            "backend.auth.agent_platform_roles", roles_path
        )
        assert roles_spec and roles_spec.loader
        roles = importlib.util.module_from_spec(roles_spec)
        sys.modules["backend.auth.agent_platform_roles"] = roles
        roles_spec.loader.exec_module(roles)
    else:
        roles = sys.modules["backend.auth.agent_platform_roles"]

    jwt = _ensure_pkg("backend.auth.jwt_handler")
    jwt.get_current_user = MagicMock()  # type: ignore[attr-defined]
    jwt.get_optional_user = MagicMock()  # type: ignore[attr-defined]

    ud = _ensure_pkg("backend.auth.user_directory")
    ud.enrich_items_author_full_names = AsyncMock()  # type: ignore[attr-defined]
    ud.resolve_full_names = AsyncMock(return_value={})  # type: ignore[attr-defined]

    sr = _ensure_pkg("backend.auth.share_recipients")
    sr.build_share_api_response = MagicMock(  # type: ignore[attr-defined]
        return_value={"success": True, "added": ["peer1"], "message": "ok"}
    )
    sr.prepare_share_recipients = AsyncMock(return_value=_prepared_share())  # type: ignore[attr-defined]

    init_db = sys.modules["backend.database.init_db"]
    init_db.get_agent_repository = MagicMock()  # type: ignore[attr-defined]
    init_db.get_skill_repository = MagicMock()  # type: ignore[attr-defined]
    init_db.get_entity_settings_repository = MagicMock(return_value=None)  # type: ignore[attr-defined]

    am = _ensure_pkg("backend.database.postgresql.agent_models")

    class AgentCreate(BaseModel):
        name: str
        description: Optional[str] = None
        system_prompt: Optional[str] = None
        config: dict = {}
        tools: list = []
        is_public: bool = False
        tag_ids: list = []
        new_tags: list = []

    class AgentUpdate(BaseModel):
        name: Optional[str] = None
        description: Optional[str] = None
        system_prompt: Optional[str] = None
        config: Optional[dict] = None
        tools: Optional[list] = None
        is_public: Optional[bool] = None

    class AgentWithTags(BaseModel):
        id: int
        name: str
        description: Optional[str] = None
        system_prompt: Optional[str] = None
        config: dict = {}
        tools: list = []
        author_id: str = ""
        author_name: str = ""
        created_at: TypingAny = None
        updated_at: TypingAny = None
        is_public: bool = False
        usage_count: int = 0
        views_count: int = 0
        tags: list = []
        average_rating: float = 0
        total_votes: int = 0
        user_rating: TypingAny = None
        is_bookmarked: bool = False
        is_shared_with_me: bool = False
        my_permission: Optional[str] = None

    class AgentFilters(BaseModel):
        search_query: Optional[str] = None
        tag_ids: Optional[list] = None
        author_id: Optional[str] = None
        author_only: bool = False
        min_rating: Optional[float] = None
        sort_by: str = "rating"
        sort_order: str = "desc"
        limit: int = 20
        offset: int = 0

    class AgentShareEntry(BaseModel):
        user_id: str
        full_name: Optional[str] = None
        permission: str = "viewer"

    class AgentShareRequest(BaseModel):
        usernames: list = []
        permission: str = "viewer"

    class AgentSharesResponse(BaseModel):
        owner: TypingAny = None
        shares: list = []

    class AgentStats(BaseModel):
        agent_id: int = 0

    am.AgentCreate = AgentCreate
    am.AgentUpdate = AgentUpdate
    am.AgentWithTags = AgentWithTags
    am.AgentFilters = AgentFilters
    am.AgentShareEntry = AgentShareEntry
    am.AgentShareRequest = AgentShareRequest
    am.AgentSharesResponse = AgentSharesResponse
    am.AgentStats = AgentStats
    am.AGENT_PERMISSION_OWNER = "owner"
    am.AGENT_PERMISSION_EDITOR = "editor"
    am.AGENT_PERMISSION_VIEWER = "viewer"

    sm = _ensure_pkg("backend.database.postgresql.skill_models")

    class SkillCreate(BaseModel):
        slug: str = "skill"
        name: str
        display_title: Optional[str] = None
        description: Optional[str] = None
        content: str = "body"
        meta: dict = {}
        is_active: bool = True
        is_public: bool = False
        user_invocable: bool = True
        disable_model_invocation: bool = False
        always_apply: bool = False
        allowed_tools: list = []
        category: Optional[str] = None

    class SkillUpdate(BaseModel):
        slug: Optional[str] = None
        name: Optional[str] = None
        display_title: Optional[str] = None
        description: Optional[str] = None
        content: Optional[str] = None
        meta: Optional[dict] = None
        is_active: Optional[bool] = None
        is_public: Optional[bool] = None
        user_invocable: Optional[bool] = None
        disable_model_invocation: Optional[bool] = None
        always_apply: Optional[bool] = None
        allowed_tools: Optional[list] = None
        category: Optional[str] = None

    class SkillOut(BaseModel):
        id: int
        slug: str = "skill"
        name: str
        display_title: Optional[str] = None
        description: Optional[str] = None
        content: str = ""
        meta: dict = {}
        is_active: bool = True
        is_public: bool = False
        user_invocable: bool = True
        disable_model_invocation: bool = False
        always_apply: bool = False
        allowed_tools: list = []
        category: Optional[str] = None
        version: int = 1
        file_count: int = 0
        author_id: str = ""
        author_name: str = ""
        my_permission: Optional[str] = None
        write_access: bool = False

    class SkillListItem(BaseModel):
        id: int
        slug: str = "skill"
        name: str
        author_id: str = ""
        author_name: str = ""
        is_public: bool = False
        my_permission: Optional[str] = None
        write_access: bool = False

    class SkillFilters(BaseModel):
        search_query: Optional[str] = None
        view_option: Optional[str] = None
        limit: int = 30
        offset: int = 0

    class SkillShareRequest(BaseModel):
        usernames: list = []
        permission: str = "viewer"

    class SkillShareEntry(BaseModel):
        user_id: str
        full_name: Optional[str] = None
        permission: str = "viewer"

    class SkillSharesResponse(BaseModel):
        owner: TypingAny = None
        shares: list = []

    class SkillFileCreate(BaseModel):
        relative_path: str = "a.md"
        content: str = ""

    class SkillFileOut(BaseModel):
        id: int = 1
        relative_path: str = "a.md"

    class SkillRatingRequest(BaseModel):
        rating: int = 5

    sm.SkillCreate = SkillCreate
    sm.SkillUpdate = SkillUpdate
    sm.SkillOut = SkillOut
    sm.SkillListItem = SkillListItem
    sm.SkillFilters = SkillFilters
    sm.SkillShareRequest = SkillShareRequest
    sm.SkillShareEntry = SkillShareEntry
    sm.SkillSharesResponse = SkillSharesResponse
    sm.SkillFileCreate = SkillFileCreate
    sm.SkillFileOut = SkillFileOut
    sm.SkillRatingRequest = SkillRatingRequest
    sm.SKILL_PERMISSION_OWNER = "owner"
    sm.SKILL_PERMISSION_EDITOR = "editor"
    sm.SKILL_PERMISSION_VIEWER = "viewer"

    cef = _ensure_pkg("backend.settings.cef_logger.cef_logger")
    cef.log_cef_event = MagicMock()  # type: ignore[attr-defined]
    logmod = _ensure_pkg("backend.settings.logging")
    logmod.get_logger = lambda name: MagicMock()  # type: ignore[attr-defined]

    if "backend.api_agents" not in sys.modules or not hasattr(
        sys.modules["backend.api_agents"], "create_agent"
    ):
        api_path = _ROOT / "backend" / "api_agents.py"
        api_spec = importlib.util.spec_from_file_location("backend.api_agents", api_path)
        assert api_spec and api_spec.loader
        agents_api = importlib.util.module_from_spec(api_spec)
        sys.modules["backend.api_agents"] = agents_api
        api_spec.loader.exec_module(agents_api)
    else:
        agents_api = sys.modules["backend.api_agents"]

    if "backend.api_skills" not in sys.modules or not hasattr(
        sys.modules["backend.api_skills"], "create_skill"
    ):
        skills_path = _ROOT / "backend" / "api_skills.py"
        skills_spec = importlib.util.spec_from_file_location(
            "backend.api_skills", skills_path
        )
        assert skills_spec and skills_spec.loader
        skills_api = importlib.util.module_from_spec(skills_spec)
        sys.modules["backend.api_skills"] = skills_api
        skills_spec.loader.exec_module(skills_api)
    else:
        skills_api = sys.modules["backend.api_skills"]

    return roles, agents_api, skills_api


def _agent(
    *,
    id: int,
    name: str,
    author_id: str,
    is_public: bool = False,
    my_permission: Optional[str] = "owner",
    is_shared_with_me: bool = False,
    config: Optional[dict] = None,
    system_prompt: str = "prompt text",
):
    return SimpleNamespace(
        id=id,
        name=name,
        description="",
        system_prompt=system_prompt,
        config=config if config is not None else {"kb_document_ids": []},
        tools=[],
        author_id=author_id,
        author_name=author_id,
        is_public=is_public,
        my_permission=my_permission,
        is_shared_with_me=is_shared_with_me,
        tags=[],
    )


def _skill(
    *,
    id: int,
    name: str,
    author_id: str,
    is_public: bool = False,
    my_permission: Optional[str] = "owner",
    is_shared_with_me: bool = False,
    write_access: bool = True,
):
    return SimpleNamespace(
        id=id,
        slug=name.lower().replace(" ", "-"),
        name=name,
        display_title=name,
        description="",
        content="skill body",
        meta={},
        is_active=True,
        is_public=is_public,
        user_invocable=True,
        disable_model_invocation=False,
        always_apply=False,
        allowed_tools=[],
        category=None,
        version=1,
        file_count=0,
        author_id=author_id,
        author_name=author_id,
        my_permission=my_permission,
        is_shared_with_me=is_shared_with_me,
        write_access=write_access,
    )


class _RoleApiCase(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.roles, cls.agents_api, cls.skills_api = _load_modules()

    def _mapping_on(self, api, role: str):
        return (
            patch.object(api, "is_agent_platform_role_mapping_enabled", return_value=True),
            patch.object(api, "get_agent_platform_role", return_value=role),
            patch.object(
                self.roles, "is_agent_platform_role_mapping_enabled", return_value=True
            ),
            patch.object(self.roles, "get_agent_platform_role", return_value=role),
        )


# ─── Role helpers ─────────────────────────────────────────────────────────────


class TestRoleHelpers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.roles, _, _ = _load_modules()

    def test_admin_can_create_and_publish(self):
        admin = {"user_id": "a1", "is_admin": True}
        with patch.object(
            self.roles, "is_agent_platform_role_mapping_enabled", return_value=True
        ), patch.object(self.roles, "get_agent_platform_role", return_value=None):
            self.assertTrue(self.roles.user_can_create_agents(admin))
            self.assertTrue(self.roles.user_can_publish_to_library(admin))
            self.assertTrue(self.roles.user_can_use_agents(admin))

    def test_editor_can_create_but_not_publish(self):
        user = {"user_id": "e1", "is_admin": False}
        with patch.object(
            self.roles, "is_agent_platform_role_mapping_enabled", return_value=True
        ), patch.object(self.roles, "get_agent_platform_role", return_value="editor"):
            self.assertTrue(self.roles.user_can_create_agents(user))
            self.assertFalse(self.roles.user_can_publish_to_library(user))
            self.assertTrue(self.roles.user_can_use_agents(user))

    def test_enrich_flags_admin_and_editor(self):
        with patch.object(
            self.roles, "is_agent_platform_role_mapping_enabled", return_value=True
        ), patch.object(self.roles, "get_agent_platform_role", return_value="editor"):
            editor = self.roles.enrich_user_feature_flags(
                {"user_id": "e1", "is_admin": False}
            )
        self.assertTrue(editor["can_create_agents"])
        self.assertFalse(editor["can_publish_to_library"])

        with patch.object(
            self.roles, "is_agent_platform_role_mapping_enabled", return_value=True
        ), patch.object(self.roles, "get_agent_platform_role", return_value="viewer"):
            admin = self.roles.enrich_user_feature_flags(
                {"user_id": "a1", "is_admin": True}
            )
        self.assertTrue(admin["can_create_agents"])
        self.assertTrue(admin["can_publish_to_library"])
        self.assertTrue(admin["can_use_agents"])

    def test_resolve_share_permission_keeps_editor_for_any_agent_role(self):
        """ACL editor не понижается, даже если LDAP дал получателю viewer."""
        with patch.object(
            self.roles, "is_agent_platform_role_mapping_enabled", return_value=True
        ):
            self.assertEqual(
                self.roles.resolve_share_permission("editor", "editor"), "editor"
            )
            self.assertEqual(
                self.roles.resolve_share_permission("editor", "owner"), "editor"
            )
            # Раньше капилось в viewer → «Роль понижена для: … (viewer)»
            self.assertEqual(
                self.roles.resolve_share_permission("editor", "viewer"), "editor"
            )
            self.assertEqual(
                self.roles.resolve_share_permission("viewer", "viewer"), "viewer"
            )
            self.assertIsNone(
                self.roles.resolve_share_permission("editor", "base")
            )
            self.assertIsNone(
                self.roles.resolve_share_permission("editor", None)
            )

    def test_resolve_share_permission_mapping_off_passthrough(self):
        with patch.object(
            self.roles, "is_agent_platform_role_mapping_enabled", return_value=False
        ):
            self.assertEqual(
                self.roles.resolve_share_permission("editor", None), "editor"
            )

    def test_resolve_cef_role_model_admin_and_legacy_user(self):
        self.assertEqual(
            self.roles.resolve_cef_role_model({"user_id": "a1", "is_admin": True}),
            ("ADMIN", ""),
        )
        with patch.object(
            self.roles, "is_agent_platform_role_mapping_enabled", return_value=False
        ):
            self.assertEqual(
                self.roles.resolve_cef_role_model(
                    {"user_id": "u1", "is_admin": False}
                ),
                ("USER", "-"),
            )

    def test_resolve_cef_role_model_base_and_viewer(self):
        base_cn = "App-NDP-D-UserRole-CORSUR"
        viewer_cn = "App-NDP-D-ViewRole-CORSUR"
        with patch.object(
            self.roles, "is_agent_platform_role_mapping_enabled", return_value=True
        ), patch.object(
            self.roles,
            "_allowed_sets",
            return_value=(set(), set(), {viewer_cn.casefold()}, {base_cn.casefold()}),
        ):
            role, matched = self.roles.resolve_cef_role_model(
                {
                    "user_id": "b1",
                    "is_admin": False,
                    "ldap_groups": [f"CN={base_cn},OU=Groups,DC=example,DC=ru"],
                }
            )
            self.assertEqual(role, "BASE")
            self.assertEqual(matched, base_cn)

            role, matched = self.roles.resolve_cef_role_model(
                {
                    "user_id": "v1",
                    "is_admin": False,
                    "groups": [viewer_cn],
                }
            )
            self.assertEqual(role, "VIEWER")
            self.assertEqual(matched, viewer_cn)

            # без match при включённом mapping — как UI: BASE
            role, matched = self.roles.resolve_cef_role_model(
                {"user_id": "x1", "is_admin": False, "ldap_groups": []}
            )
            self.assertEqual(role, "BASE")
            self.assertEqual(matched, "-")


class TestSharePermissionGrant(unittest.IsolatedAsyncioTestCase):
    """Шаринговая выдача ACL editor не должна понижаться до viewer."""

    @classmethod
    def setUpClass(cls):
        cls.roles, _, _ = _load_modules()
        # share_recipients уже заглушен в _load_modules — подгружаем реальный модуль
        # под отдельным именем, чтобы не ломать stubs api_* тестов.
        path = _ROOT / "backend" / "auth" / "share_recipients.py"
        spec = importlib.util.spec_from_file_location(
            "backend.auth.share_recipients_real", path
        )
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        # Подменяем импорты на уже загруженные roles
        sys.modules["backend.auth.share_recipients_real"] = mod
        # Модуль импортирует из backend.auth.agent_platform_roles — уже в sys.modules
        logging_stub = sys.modules.get("backend.settings.logging")
        if logging_stub is None:
            logging_stub = _ensure_pkg("backend.settings.logging")
            logging_stub.get_logger = lambda name: MagicMock()  # type: ignore[attr-defined]
        spec.loader.exec_module(mod)
        cls.share = mod

    async def test_prepare_share_keeps_editor_when_ldap_says_viewer(self):
        """Типичный баг: LDAP/search_filter дал viewer, а просили editor."""
        profile = {
            "user_id": "peer1",
            "username": "peer1",
            "groups": ["App-Viewer"],
            "ldap_groups": ["CN=App-Viewer,OU=Groups,DC=example,DC=com"],
        }

        with patch.object(
            self.share, "is_agent_platform_role_mapping_enabled", return_value=True
        ), patch.object(
            self.share,
            "get_platform_role_from_group_identifiers",
            return_value="viewer",
        ), patch.object(
            self.share,
            "_fetch_recipient_profiles",
            AsyncMock(return_value={"peer1": profile}),
        ), patch.object(
            self.roles, "is_agent_platform_role_mapping_enabled", return_value=True
        ):
            # resolve_share_permission берётся из roles при импорте share_recipients_real —
            # патчим функцию на самом модуле share
            prepared = await self.share.prepare_share_recipients(["peer1"], "editor")

        self.assertEqual(prepared.to_share, [("peer1", "editor")])
        self.assertEqual(prepared.capped, [])
        self.assertEqual(prepared.skipped, [])

    async def test_prepare_share_skips_base_recipient(self):
        profile = {"user_id": "base1", "username": "base1", "groups": [], "ldap_groups": []}
        with patch.object(
            self.share, "is_agent_platform_role_mapping_enabled", return_value=True
        ), patch.object(
            self.roles, "is_agent_platform_role_mapping_enabled", return_value=True
        ), patch.object(
            self.share,
            "get_platform_role_from_group_identifiers",
            return_value="base",
        ), patch.object(
            self.share,
            "_fetch_recipient_profiles",
            AsyncMock(return_value={"base1": profile}),
        ):
            prepared = await self.share.prepare_share_recipients(["base1"], "editor")
        self.assertEqual(prepared.to_share, [])
        self.assertIn("base1", prepared.skipped)


# ─── Admin: agents ────────────────────────────────────────────────────────────


class TestAdminAgents(_RoleApiCase):
    """Admin создаёт/публикует/шарит/удаляет без ограничений platform-роли."""

    async def test_admin_creates_public_agent(self):
        api = self.agents_api
        admin = {"user_id": "admin1", "username": "admin1", "is_admin": True}
        created: dict[str, Any] = {}

        async def create_agent(*, agent_data, author_id, author_name):
            created["is_public"] = bool(agent_data.is_public)
            created["author_id"] = author_id
            return 201

        repo = MagicMock()
        repo.create_agent = AsyncMock(side_effect=create_agent)
        req = MagicMock()
        with patch.object(api, "get_agent_repository", return_value=repo), patch.object(
            api, "is_agent_platform_role_mapping_enabled", return_value=True
        ), patch.object(
            api, "get_agent_platform_role", return_value="viewer"  # роль не важна для admin
        ), patch.object(
            api, "_reject_foreign_kb_documents", AsyncMock()
        ), patch.object(
            api, "log_cef_event", MagicMock()
        ), patch.object(
            self.roles, "is_agent_platform_role_mapping_enabled", return_value=True
        ), patch.object(
            self.roles, "get_agent_platform_role", return_value="viewer"
        ):
            result = await api.create_agent(
                req,
                api.AgentCreate(
                    name="Admin Public",
                    system_prompt="1234567890",
                    is_public=True,
                ),
                admin,
            )
        self.assertTrue(result["success"])
        self.assertTrue(created["is_public"])
        self.assertEqual(created["author_id"], "admin1")

    async def test_admin_can_publish_and_unpublish(self):
        api = self.agents_api
        admin = {"user_id": "admin1", "username": "admin1", "is_admin": True}
        agent = _agent(id=5, name="X", author_id="other", is_public=False)
        repo = MagicMock()
        repo.get_agent = AsyncMock(return_value=agent)
        repo.update_agent = AsyncMock(return_value=True)
        req = MagicMock()
        with patch.object(api, "get_agent_repository", return_value=repo), patch.object(
            api, "is_agent_platform_role_mapping_enabled", return_value=True
        ), patch.object(
            api, "get_agent_platform_role", return_value="viewer"
        ), patch.object(
            api, "_reject_foreign_kb_documents", AsyncMock()
        ), patch.object(
            api, "log_cef_event", MagicMock()
        ), patch.object(
            self.roles, "is_agent_platform_role_mapping_enabled", return_value=True
        ), patch.object(
            self.roles, "get_agent_platform_role", return_value="viewer"
        ):
            ok = await api.update_agent(
                req, 5, api.AgentUpdate(is_public=True), admin
            )
        self.assertTrue(ok["success"])
        repo.update_agent.assert_awaited()

    async def test_admin_can_share_foreign_agent(self):
        api = self.agents_api
        admin = {"user_id": "admin1", "username": "admin1", "is_admin": True}
        agent = _agent(id=7, name="Foreign", author_id="someone", is_public=False)
        repo = MagicMock()
        repo.get_agent = AsyncMock(return_value=agent)
        repo.share_agent = AsyncMock(return_value=(["peer1"], []))
        req = MagicMock()
        prepared = _prepared_share()
        with patch.object(api, "get_agent_repository", return_value=repo), patch.object(
            api, "is_agent_platform_role_mapping_enabled", return_value=True
        ), patch.object(
            api, "get_agent_platform_role", return_value="viewer"
        ), patch.object(
            api, "prepare_share_recipients", AsyncMock(return_value=prepared)
        ), patch.object(
            api,
            "build_share_api_response",
            return_value={"success": True, "added": ["peer1"]},
        ), patch.object(
            api, "log_cef_event", MagicMock()
        ):
            # admin считается автором через _is_agent_author;
            # в репозиторий уходит author_id карточки (не admin1)
            result = await api.share_agent(
                req, 7, api.AgentShareRequest(usernames=["peer1"]), admin
            )
        self.assertTrue(result.get("success"))
        repo.share_agent.assert_awaited()
        call_kwargs = repo.share_agent.await_args.kwargs
        self.assertEqual(call_kwargs.get("owner_id"), "someone")

    async def test_admin_can_unshare_foreign_agent_without_platform_role(self):
        """Админ без LDAP AGENT_ROLE_* снимает чужой шаринг от имени автора."""
        api = self.agents_api
        admin = {"user_id": "admin1", "username": "admin1", "is_admin": True}
        agent = _agent(id=71, name="Foreign", author_id="someone", is_public=False)
        repo = MagicMock()
        repo.get_agent = AsyncMock(return_value=agent)
        repo.unshare_agent = AsyncMock(return_value=True)
        req = MagicMock()
        with patch.object(api, "get_agent_repository", return_value=repo), patch.object(
            api, "is_agent_platform_role_mapping_enabled", return_value=True
        ), patch.object(
            api, "get_agent_platform_role", return_value=None
        ), patch.object(
            api, "log_cef_event", MagicMock()
        ):
            result = await api.unshare_agent(req, 71, "peer1", admin)
        self.assertTrue(result.get("success"))
        repo.unshare_agent.assert_awaited()
        call_kwargs = repo.unshare_agent.await_args.kwargs
        self.assertEqual(call_kwargs.get("actor_id"), "someone")
        self.assertEqual(call_kwargs.get("target_user_id"), "peer1")

    def test_shared_agent_keeps_editor_despite_platform_viewer(self):
        """Явный шаринг editor не сжимается до viewer по platform-роли."""
        api = self.agents_api
        viewer = {"user_id": "v1", "is_admin": False}
        shared = _agent(
            id=72,
            name="Shared",
            author_id="other",
            my_permission="editor",
            is_shared_with_me=True,
        )
        public_only = _agent(
            id=73,
            name="Public",
            author_id="other",
            is_public=True,
            my_permission="editor",
            is_shared_with_me=False,
        )
        with patch.object(
            api, "is_agent_platform_role_mapping_enabled", return_value=True
        ):
            api._apply_platform_permission_cap(shared, viewer, "viewer")
            api._apply_platform_permission_cap(public_only, viewer, "viewer")
        self.assertEqual(shared.my_permission, "editor")
        self.assertEqual(public_only.my_permission, "viewer")

    async def test_admin_can_delete_foreign_agent(self):
        api = self.agents_api
        admin = {"user_id": "admin1", "username": "admin1", "is_admin": True}
        agent = _agent(
            id=8,
            name="Foreign",
            author_id="someone",
            config={"kb_document_ids": []},
        )
        repo = MagicMock()
        repo.get_agent = AsyncMock(return_value=agent)
        repo.delete_agent = AsyncMock(return_value=True)
        repo.find_orphan_kb_document_ids = AsyncMock(return_value=[])
        req = MagicMock()
        with patch.object(api, "get_agent_repository", return_value=repo), patch.object(
            api, "is_agent_platform_role_mapping_enabled", return_value=True
        ), patch.object(
            api, "get_agent_platform_role", return_value="viewer"
        ), patch.object(
            api, "log_cef_event", MagicMock()
        ):
            result = await api.delete_agent(req, 8, admin)
        self.assertTrue(result["success"])
        repo.delete_agent.assert_awaited()


# ─── Editor: agents ───────────────────────────────────────────────────────────


class TestEditorAgents(_RoleApiCase):
    """Editor создаёт и шарит свои, но не публикует в галерею."""

    async def test_editor_creates_agent_forced_private(self):
        api = self.agents_api
        editor = {"user_id": "ed1", "username": "ed1", "is_admin": False}
        created: dict[str, Any] = {}

        async def create_agent(*, agent_data, author_id, author_name):
            created["is_public"] = bool(agent_data.is_public)
            return 301

        repo = MagicMock()
        repo.create_agent = AsyncMock(side_effect=create_agent)
        req = MagicMock()
        with patch.object(api, "get_agent_repository", return_value=repo), patch.object(
            api, "is_agent_platform_role_mapping_enabled", return_value=True
        ), patch.object(
            api, "get_agent_platform_role", return_value="editor"
        ), patch.object(
            api, "_reject_foreign_kb_documents", AsyncMock()
        ), patch.object(
            api, "log_cef_event", MagicMock()
        ), patch.object(
            self.roles, "is_agent_platform_role_mapping_enabled", return_value=True
        ), patch.object(
            self.roles, "get_agent_platform_role", return_value="editor"
        ):
            result = await api.create_agent(
                req,
                api.AgentCreate(
                    name="Ed Agent", system_prompt="1234567890", is_public=True
                ),
                editor,
            )
        self.assertTrue(result["success"])
        self.assertFalse(created["is_public"])

    async def test_editor_cannot_publish_own_agent(self):
        from fastapi import HTTPException

        api = self.agents_api
        editor = {"user_id": "ed1", "username": "ed1", "is_admin": False}
        agent = _agent(id=9, name="Mine", author_id="ed1", is_public=False)
        repo = MagicMock()
        repo.get_agent = AsyncMock(return_value=agent)
        repo.update_agent = AsyncMock(return_value=True)
        req = MagicMock()
        with patch.object(api, "get_agent_repository", return_value=repo), patch.object(
            api, "is_agent_platform_role_mapping_enabled", return_value=True
        ), patch.object(
            api, "get_agent_platform_role", return_value="editor"
        ), patch.object(
            api, "_reject_foreign_kb_documents", AsyncMock()
        ), patch.object(
            self.roles, "is_agent_platform_role_mapping_enabled", return_value=True
        ), patch.object(
            self.roles, "get_agent_platform_role", return_value="editor"
        ):
            with self.assertRaises(HTTPException) as ctx:
                await api.update_agent(
                    req, 9, api.AgentUpdate(is_public=True), editor
                )
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertIn("публикац", ctx.exception.detail.lower())
        repo.update_agent.assert_not_awaited()

    async def test_editor_can_update_own_agent_without_publish(self):
        api = self.agents_api
        editor = {"user_id": "ed1", "username": "ed1", "is_admin": False}
        agent = _agent(id=9, name="Mine", author_id="ed1", is_public=False)
        repo = MagicMock()
        repo.get_agent = AsyncMock(return_value=agent)
        repo.update_agent = AsyncMock(return_value=True)
        req = MagicMock()
        with patch.object(api, "get_agent_repository", return_value=repo), patch.object(
            api, "is_agent_platform_role_mapping_enabled", return_value=True
        ), patch.object(
            api, "get_agent_platform_role", return_value="editor"
        ), patch.object(
            api, "_reject_foreign_kb_documents", AsyncMock()
        ), patch.object(
            api, "log_cef_event", MagicMock()
        ), patch.object(
            self.roles, "is_agent_platform_role_mapping_enabled", return_value=True
        ), patch.object(
            self.roles, "get_agent_platform_role", return_value="editor"
        ):
            ok = await api.update_agent(
                req, 9, api.AgentUpdate(name="Renamed", system_prompt="1234567890"), editor
            )
        self.assertTrue(ok["success"])
        repo.update_agent.assert_awaited()

    async def test_editor_can_share_own_agent(self):
        api = self.agents_api
        editor = {"user_id": "ed1", "username": "ed1", "is_admin": False}
        agent = _agent(id=10, name="Mine", author_id="ed1")
        repo = MagicMock()
        repo.get_agent = AsyncMock(return_value=agent)
        repo.share_agent = AsyncMock(return_value=(["peer1"], []))
        req = MagicMock()
        prepared = _prepared_share()
        with patch.object(api, "get_agent_repository", return_value=repo), patch.object(
            api, "is_agent_platform_role_mapping_enabled", return_value=True
        ), patch.object(
            api, "get_agent_platform_role", return_value="editor"
        ), patch.object(
            api, "prepare_share_recipients", AsyncMock(return_value=prepared)
        ), patch.object(
            api,
            "build_share_api_response",
            return_value={"success": True, "added": ["peer1"]},
        ), patch.object(
            api, "log_cef_event", MagicMock()
        ):
            result = await api.share_agent(
                req, 10, api.AgentShareRequest(usernames=["peer1"]), editor
            )
        self.assertTrue(result.get("success"))
        repo.share_agent.assert_awaited()

    async def test_editor_cannot_share_foreign_agent(self):
        from fastapi import HTTPException

        api = self.agents_api
        editor = {"user_id": "ed1", "username": "ed1", "is_admin": False}
        agent = _agent(id=11, name="Foreign", author_id="other")
        repo = MagicMock()
        repo.get_agent = AsyncMock(return_value=agent)
        repo.share_agent = AsyncMock(return_value=(["peer1"], []))
        req = MagicMock()
        with patch.object(api, "get_agent_repository", return_value=repo), patch.object(
            api, "is_agent_platform_role_mapping_enabled", return_value=True
        ), patch.object(
            api, "get_agent_platform_role", return_value="editor"
        ):
            with self.assertRaises(HTTPException) as ctx:
                await api.share_agent(
                    req, 11, api.AgentShareRequest(usernames=["peer1"]), editor
                )
        self.assertEqual(ctx.exception.status_code, 403)
        repo.share_agent.assert_not_awaited()

    async def test_editor_cannot_delete_foreign_agent(self):
        from fastapi import HTTPException

        api = self.agents_api
        editor = {"user_id": "ed1", "username": "ed1", "is_admin": False}
        agent = _agent(
            id=12,
            name="Foreign",
            author_id="other",
            config={"kb_document_ids": []},
        )
        repo = MagicMock()
        repo.get_agent = AsyncMock(return_value=agent)
        repo.delete_agent = AsyncMock(return_value=True)
        req = MagicMock()
        with patch.object(api, "get_agent_repository", return_value=repo), patch.object(
            api, "is_agent_platform_role_mapping_enabled", return_value=True
        ), patch.object(
            api, "get_agent_platform_role", return_value="editor"
        ):
            with self.assertRaises(HTTPException) as ctx:
                await api.delete_agent(req, 12, editor)
        self.assertEqual(ctx.exception.status_code, 403)
        repo.delete_agent.assert_not_awaited()

    async def test_editor_can_delete_own_agent(self):
        api = self.agents_api
        editor = {"user_id": "ed1", "username": "ed1", "is_admin": False}
        agent = _agent(
            id=13,
            name="Mine",
            author_id="ed1",
            config={"kb_document_ids": []},
        )
        repo = MagicMock()
        repo.get_agent = AsyncMock(return_value=agent)
        repo.delete_agent = AsyncMock(return_value=True)
        repo.find_orphan_kb_document_ids = AsyncMock(return_value=[])
        req = MagicMock()
        with patch.object(api, "get_agent_repository", return_value=repo), patch.object(
            api, "is_agent_platform_role_mapping_enabled", return_value=True
        ), patch.object(
            api, "get_agent_platform_role", return_value="editor"
        ), patch.object(
            api, "log_cef_event", MagicMock()
        ):
            result = await api.delete_agent(req, 13, editor)
        self.assertTrue(result["success"])
        repo.delete_agent.assert_awaited()


# ─── Admin: skills ────────────────────────────────────────────────────────────


class TestAdminSkills(_RoleApiCase):
    async def test_admin_creates_public_skill(self):
        api = self.skills_api
        admin = {"user_id": "admin1", "username": "admin1", "is_admin": True}
        created: dict[str, Any] = {}

        async def create_skill(skill_data, author_id, author_name):
            created["is_public"] = bool(skill_data.is_public)
            return _skill(
                id=401,
                name=skill_data.name,
                author_id=author_id,
                is_public=skill_data.is_public,
            )

        repo = MagicMock()
        repo.create_skill = AsyncMock(side_effect=create_skill)
        with patch.object(api, "get_skill_repository", return_value=repo), patch.object(
            self.roles, "is_agent_platform_role_mapping_enabled", return_value=True
        ), patch.object(
            self.roles, "get_agent_platform_role", return_value="viewer"
        ), patch.object(
            api, "is_agent_platform_role_mapping_enabled", return_value=True
        ), patch.object(
            api, "get_agent_platform_role", return_value="viewer"
        ):
            skill = await api.create_skill(
                api.SkillCreate(name="Admin Skill", content="x", is_public=True),
                admin,
            )
        self.assertTrue(created["is_public"])
        self.assertEqual(skill.id, 401)

    async def test_admin_can_publish_skill(self):
        api = self.skills_api
        admin = {"user_id": "admin1", "username": "admin1", "is_admin": True}
        existing = _skill(id=402, name="S", author_id="other", is_public=False)
        updated = _skill(id=402, name="S", author_id="other", is_public=True)
        repo = MagicMock()
        repo.get_skill_by_id = AsyncMock(return_value=existing)
        repo.update_skill = AsyncMock(return_value=updated)
        with patch.object(api, "get_skill_repository", return_value=repo), patch.object(
            self.roles, "is_agent_platform_role_mapping_enabled", return_value=True
        ), patch.object(
            self.roles, "get_agent_platform_role", return_value="viewer"
        ), patch.object(
            api, "is_agent_platform_role_mapping_enabled", return_value=True
        ), patch.object(
            api, "get_agent_platform_role", return_value="viewer"
        ):
            skill = await api.update_skill(
                402, api.SkillUpdate(is_public=True), admin
            )
        self.assertTrue(skill.is_public)
        repo.update_skill.assert_awaited()

    async def test_admin_can_share_skill(self):
        api = self.skills_api
        admin = {"user_id": "admin1", "username": "admin1", "is_admin": True}
        existing = _skill(id=403, name="S", author_id="other")
        repo = MagicMock()
        repo.get_skill_by_id = AsyncMock(return_value=existing)
        repo.share_skill = AsyncMock(return_value=(["peer1"], []))
        prepared = _prepared_share()
        with patch.object(api, "get_skill_repository", return_value=repo), patch.object(
            api, "prepare_share_recipients", AsyncMock(return_value=prepared)
        ), patch.object(
            api,
            "build_share_api_response",
            return_value={"success": True, "added": ["peer1"]},
        ):
            result = await api.share_skill(
                403, api.SkillShareRequest(usernames=["peer1"]), admin
            )
        self.assertTrue(result.get("success"))
        repo.share_skill.assert_awaited()
        call_kwargs = repo.share_skill.await_args.kwargs
        self.assertEqual(call_kwargs.get("owner_id"), "other")

    async def test_admin_can_unshare_foreign_skill_without_platform_role(self):
        """Админ без LDAP AGENT_ROLE_* снимает чужой шаринг skill от имени автора."""
        api = self.skills_api
        admin = {"user_id": "admin1", "username": "admin1", "is_admin": True}
        existing = _skill(id=404, name="S", author_id="other")
        repo = MagicMock()
        repo.get_skill_by_id = AsyncMock(return_value=existing)
        repo.unshare_skill = AsyncMock(return_value=True)
        with patch.object(api, "get_skill_repository", return_value=repo), patch.object(
            api, "is_agent_platform_role_mapping_enabled", return_value=True
        ), patch.object(api, "get_agent_platform_role", return_value=None):
            result = await api.unshare_skill(404, "peer1", admin)
        self.assertTrue(result.get("success"))
        repo.unshare_skill.assert_awaited_with(404, "other", "peer1")


# ─── Editor: skills ───────────────────────────────────────────────────────────


class TestEditorSkills(_RoleApiCase):
    async def test_editor_creates_skill_forced_private(self):
        api = self.skills_api
        editor = {"user_id": "ed1", "username": "ed1", "is_admin": False}
        created: dict[str, Any] = {}

        async def create_skill(skill_data, author_id, author_name):
            created["is_public"] = bool(skill_data.is_public)
            return _skill(
                id=501,
                name=skill_data.name,
                author_id=author_id,
                is_public=skill_data.is_public,
            )

        repo = MagicMock()
        repo.create_skill = AsyncMock(side_effect=create_skill)
        with patch.object(api, "get_skill_repository", return_value=repo), patch.object(
            self.roles, "is_agent_platform_role_mapping_enabled", return_value=True
        ), patch.object(
            self.roles, "get_agent_platform_role", return_value="editor"
        ), patch.object(
            api, "is_agent_platform_role_mapping_enabled", return_value=True
        ), patch.object(
            api, "get_agent_platform_role", return_value="editor"
        ):
            skill = await api.create_skill(
                api.SkillCreate(name="Ed Skill", content="x", is_public=True),
                editor,
            )
        self.assertFalse(created["is_public"])
        self.assertEqual(skill.author_id, "ed1")

    async def test_editor_cannot_publish_own_skill(self):
        from fastapi import HTTPException

        api = self.skills_api
        editor = {"user_id": "ed1", "username": "ed1", "is_admin": False}
        existing = _skill(id=502, name="Mine", author_id="ed1", is_public=False)
        repo = MagicMock()
        repo.get_skill_by_id = AsyncMock(return_value=existing)
        repo.update_skill = AsyncMock(
            return_value=_skill(id=502, name="Mine", author_id="ed1", is_public=True)
        )
        with patch.object(api, "get_skill_repository", return_value=repo), patch.object(
            self.roles, "is_agent_platform_role_mapping_enabled", return_value=True
        ), patch.object(
            self.roles, "get_agent_platform_role", return_value="editor"
        ), patch.object(
            api, "is_agent_platform_role_mapping_enabled", return_value=True
        ), patch.object(
            api, "get_agent_platform_role", return_value="editor"
        ):
            with self.assertRaises(HTTPException) as ctx:
                await api.update_skill(
                    502, api.SkillUpdate(is_public=True), editor
                )
        self.assertEqual(ctx.exception.status_code, 403)
        repo.update_skill.assert_not_awaited()

    async def test_editor_can_update_own_skill_content(self):
        api = self.skills_api
        editor = {"user_id": "ed1", "username": "ed1", "is_admin": False}
        existing = _skill(id=503, name="Mine", author_id="ed1", is_public=False)
        updated = _skill(id=503, name="Mine2", author_id="ed1", is_public=False)
        repo = MagicMock()
        repo.get_skill_by_id = AsyncMock(return_value=existing)
        repo.update_skill = AsyncMock(return_value=updated)
        with patch.object(api, "get_skill_repository", return_value=repo), patch.object(
            self.roles, "is_agent_platform_role_mapping_enabled", return_value=True
        ), patch.object(
            self.roles, "get_agent_platform_role", return_value="editor"
        ), patch.object(
            api, "is_agent_platform_role_mapping_enabled", return_value=True
        ), patch.object(
            api, "get_agent_platform_role", return_value="editor"
        ):
            skill = await api.update_skill(
                503, api.SkillUpdate(name="Mine2", content="new"), editor
            )
        self.assertEqual(skill.name, "Mine2")
        repo.update_skill.assert_awaited()

    async def test_editor_can_share_own_skill(self):
        api = self.skills_api
        editor = {"user_id": "ed1", "username": "ed1", "is_admin": False}
        existing = _skill(id=504, name="Mine", author_id="ed1")
        repo = MagicMock()
        repo.get_skill_by_id = AsyncMock(return_value=existing)
        repo.share_skill = AsyncMock(return_value=(["peer1"], []))
        prepared = _prepared_share()
        with patch.object(api, "get_skill_repository", return_value=repo), patch.object(
            api, "prepare_share_recipients", AsyncMock(return_value=prepared)
        ), patch.object(
            api,
            "build_share_api_response",
            return_value={"success": True, "added": ["peer1"]},
        ), patch.object(
            api, "is_agent_platform_role_mapping_enabled", return_value=True
        ), patch.object(
            api, "get_agent_platform_role", return_value="editor"
        ):
            result = await api.share_skill(
                504, api.SkillShareRequest(usernames=["peer1"]), editor
            )
        self.assertTrue(result.get("success"))
        repo.share_skill.assert_awaited()

    async def test_editor_cannot_share_foreign_skill(self):
        from fastapi import HTTPException

        api = self.skills_api
        editor = {"user_id": "ed1", "username": "ed1", "is_admin": False}
        existing = _skill(id=505, name="Foreign", author_id="other")
        repo = MagicMock()
        repo.get_skill_by_id = AsyncMock(return_value=existing)
        repo.share_skill = AsyncMock(return_value=(["peer1"], []))
        with patch.object(api, "get_skill_repository", return_value=repo), patch.object(
            api, "is_agent_platform_role_mapping_enabled", return_value=True
        ), patch.object(
            api, "get_agent_platform_role", return_value="editor"
        ):
            with self.assertRaises(HTTPException) as ctx:
                await api.share_skill(
                    505, api.SkillShareRequest(usernames=["peer1"]), editor
                )
        self.assertEqual(ctx.exception.status_code, 403)
        repo.share_skill.assert_not_awaited()

    async def test_editor_author_keeps_owner_permission_cap(self):
        """Автор-editor не теряет ACL owner на своей карточке skill."""
        api = self.skills_api
        editor = {"user_id": "ed1", "is_admin": False}
        own = _skill(id=506, name="Mine", author_id="ed1", my_permission="owner")
        foreign = _skill(
            id=507, name="Foreign", author_id="other", my_permission="owner"
        )
        with patch.object(
            api, "is_agent_platform_role_mapping_enabled", return_value=True
        ):
            api._apply_platform_role_to_skill(own, "editor", user=editor)
            api._apply_platform_role_to_skill(foreign, "editor", user=editor)
        self.assertEqual(own.my_permission, "owner")
        self.assertEqual(foreign.my_permission, "editor")

    async def test_shared_skill_keeps_editor_despite_platform_viewer(self):
        """Явный шаринг editor не сжимается до viewer по platform-роли."""
        api = self.skills_api
        viewer = {"user_id": "v1", "is_admin": False}
        shared = _skill(
            id=510,
            name="Shared",
            author_id="other",
            my_permission="editor",
            is_shared_with_me=True,
        )
        public_only = _skill(
            id=511,
            name="Public",
            author_id="other",
            my_permission="editor",
            is_shared_with_me=False,
            is_public=True,
        )
        with patch.object(
            api, "is_agent_platform_role_mapping_enabled", return_value=True
        ):
            api._apply_platform_role_to_skill(shared, "viewer", user=viewer)
            api._apply_platform_role_to_skill(public_only, "viewer", user=viewer)
        self.assertEqual(shared.my_permission, "editor")
        self.assertTrue(shared.write_access)
        self.assertEqual(public_only.my_permission, "viewer")
        self.assertFalse(public_only.write_access)

    async def test_editor_can_delete_own_skill(self):
        api = self.skills_api
        editor = {"user_id": "ed1", "username": "ed1", "is_admin": False}
        existing = _skill(id=508, name="Mine", author_id="ed1")
        repo = MagicMock()
        repo.get_skill_by_id = AsyncMock(return_value=existing)
        repo.delete_skill = AsyncMock(return_value=True)
        with patch.object(api, "get_skill_repository", return_value=repo), patch.object(
            api, "is_agent_platform_role_mapping_enabled", return_value=True
        ), patch.object(
            api, "get_agent_platform_role", return_value="editor"
        ):
            result = await api.delete_skill(508, editor)
        self.assertTrue(result["success"])
        repo.delete_skill.assert_awaited()

    async def test_editor_cannot_delete_foreign_skill(self):
        from fastapi import HTTPException

        api = self.skills_api
        editor = {"user_id": "ed1", "username": "ed1", "is_admin": False}
        existing = _skill(id=509, name="Foreign", author_id="other")
        repo = MagicMock()
        repo.get_skill_by_id = AsyncMock(return_value=existing)
        repo.delete_skill = AsyncMock(return_value=True)
        with patch.object(api, "get_skill_repository", return_value=repo), patch.object(
            api, "is_agent_platform_role_mapping_enabled", return_value=True
        ), patch.object(
            api, "get_agent_platform_role", return_value="editor"
        ):
            with self.assertRaises(HTTPException) as ctx:
                await api.delete_skill(509, editor)
        self.assertEqual(ctx.exception.status_code, 403)
        repo.delete_skill.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
