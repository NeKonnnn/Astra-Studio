# -*- coding: utf-8 -*-
"""Клонирование агентов из галереи: роли, deep-clone цепочки/субагентов, права автора клона.

Импорт идёт без ``backend.__init__`` (asyncpg и пр. могут отсутствовать локально) —
как в остальных unit-тестах backend/tests.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
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


def _load_clone_modules():
    """Загрузить platform_roles + api_agents с заглушками тяжёлых зависимостей."""
    if "backend.api_agents" in sys.modules and hasattr(
        sys.modules["backend.api_agents"], "_extract_linked_agent_ids"
    ):
        return (
            sys.modules["backend.auth.agent_platform_roles"],
            sys.modules["backend.api_agents"],
        )

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

    roles_path = _ROOT / "backend" / "auth" / "agent_platform_roles.py"
    roles_spec = importlib.util.spec_from_file_location(
        "backend.auth.agent_platform_roles", roles_path
    )
    assert roles_spec and roles_spec.loader
    roles = importlib.util.module_from_spec(roles_spec)
    sys.modules["backend.auth.agent_platform_roles"] = roles
    roles_spec.loader.exec_module(roles)

    jwt = _ensure_pkg("backend.auth.jwt_handler")
    jwt.get_current_user = MagicMock()  # type: ignore[attr-defined]
    jwt.get_optional_user = MagicMock()  # type: ignore[attr-defined]

    ud = _ensure_pkg("backend.auth.user_directory")
    ud.enrich_items_author_full_names = AsyncMock()  # type: ignore[attr-defined]
    ud.resolve_full_names = AsyncMock(return_value={})  # type: ignore[attr-defined]

    sr = _ensure_pkg("backend.auth.share_recipients")
    sr.build_share_api_response = MagicMock()  # type: ignore[attr-defined]
    sr.prepare_share_recipients = AsyncMock()  # type: ignore[attr-defined]

    init_db = sys.modules["backend.database.init_db"]
    init_db.get_agent_repository = MagicMock()  # type: ignore[attr-defined]
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

    cef = _ensure_pkg("backend.settings.cef_logger.cef_logger")
    cef.log_cef_event = MagicMock()  # type: ignore[attr-defined]
    logmod = _ensure_pkg("backend.settings.logging")
    logmod.get_logger = lambda name: MagicMock()  # type: ignore[attr-defined]

    api_path = _ROOT / "backend" / "api_agents.py"
    api_spec = importlib.util.spec_from_file_location("backend.api_agents", api_path)
    assert api_spec and api_spec.loader
    api = importlib.util.module_from_spec(api_spec)
    sys.modules["backend.api_agents"] = api
    api_spec.loader.exec_module(api)
    return roles, api


def _agent(
    *,
    id: int,
    name: str,
    author_id: str,
    config: Optional[dict] = None,
    system_prompt: str = "prompt",
    is_public: bool = False,
    my_permission: Optional[str] = None,
    tags: Optional[list] = None,
    tools: Optional[list] = None,
):
    return SimpleNamespace(
        id=id,
        name=name,
        description="",
        system_prompt=system_prompt,
        config=dict(config or {}),
        tools=list(tools or []),
        author_id=author_id,
        author_name=author_id,
        is_public=is_public,
        my_permission=my_permission,
        tags=list(tags or []),
        usage_count=0,
        views_count=0,
    )


class TestCloneHelpers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.roles, cls.api = _load_clone_modules()

    def test_extract_linked_ids_from_chain_and_subagents(self):
        cfg = {
            "agent_ids": [10, "11", 10, "x"],
            "subagents": {"agent_ids": [11, 12], "agent_names": {"11": "A"}},
        }
        self.assertEqual(self.api._extract_linked_agent_ids(cfg), [10, 11, 12])

    def test_remap_rewrites_chain_subagents_and_drops_unmapped(self):
        cfg = {
            "agent_ids": [10, 99],
            "subagents": {
                "enabled": True,
                "agent_ids": [10, 20],
                "agent_names": {"10": "ChainA", "20": "SubB", "99": "Gone"},
            },
            "kb_document_ids": [5],
        }
        mapped = self.api._remap_config_agent_refs(cfg, {10: 110, 20: 120})
        self.assertEqual(mapped["agent_ids"], [110])
        self.assertEqual(mapped["subagents"]["agent_ids"], [110, 120])
        self.assertEqual(
            mapped["subagents"]["agent_names"],
            {"110": "ChainA", "120": "SubB"},
        )
        # KB ids remap не трогает — ими занимается отдельный клон документов
        self.assertEqual(mapped["kb_document_ids"], [5])

    def test_platform_cap_keeps_owner_for_clone_author_even_if_viewer(self):
        user = {"user_id": "viewer1", "is_admin": False}
        clone = _agent(
            id=50,
            name="X (копия)",
            author_id="viewer1",
            my_permission="owner",
        )
        gallery = _agent(
            id=1,
            name="Gallery",
            author_id="owner1",
            is_public=True,
            my_permission="owner",  # как будто авторский ранг из БД
        )
        with patch.object(
            self.api, "is_agent_platform_role_mapping_enabled", return_value=True
        ):
            self.api._apply_platform_permission_cap(clone, user, "viewer")
            self.api._apply_platform_permission_cap(gallery, user, "viewer")
        self.assertEqual(clone.my_permission, "owner")
        self.assertEqual(gallery.my_permission, "viewer")

    def test_user_can_use_agents_roles(self):
        # mapping выключен — все могут
        with patch.object(self.roles, "is_agent_platform_role_mapping_enabled", return_value=False):
            self.assertTrue(self.roles.user_can_use_agents({"user_id": "u"}))

        with patch.object(self.roles, "is_agent_platform_role_mapping_enabled", return_value=True):
            with patch.object(self.roles, "get_agent_platform_role", return_value="owner"):
                self.assertTrue(self.roles.user_can_use_agents({"user_id": "u"}))
            with patch.object(self.roles, "get_agent_platform_role", return_value="editor"):
                self.assertTrue(self.roles.user_can_use_agents({"user_id": "u"}))
            with patch.object(self.roles, "get_agent_platform_role", return_value="viewer"):
                self.assertTrue(self.roles.user_can_use_agents({"user_id": "u"}))
            with patch.object(self.roles, "get_agent_platform_role", return_value="base"):
                self.assertFalse(self.roles.user_can_use_agents({"user_id": "u"}))

    def test_user_can_create_agents_roles(self):
        with patch.object(self.roles, "is_agent_platform_role_mapping_enabled", return_value=False):
            self.assertTrue(self.roles.user_can_create_agents({"user_id": "u"}))

        with patch.object(self.roles, "is_agent_platform_role_mapping_enabled", return_value=True):
            with patch.object(self.roles, "get_agent_platform_role", return_value="owner"):
                self.assertTrue(self.roles.user_can_create_agents({"user_id": "u"}))
            with patch.object(self.roles, "get_agent_platform_role", return_value="editor"):
                self.assertTrue(self.roles.user_can_create_agents({"user_id": "u"}))
            with patch.object(self.roles, "get_agent_platform_role", return_value="viewer"):
                self.assertFalse(self.roles.user_can_create_agents({"user_id": "u"}))
            with patch.object(self.roles, "get_agent_platform_role", return_value="base"):
                self.assertFalse(self.roles.user_can_create_agents({"user_id": "u"}))
        self.assertTrue(self.roles.user_can_create_agents({"user_id": "u", "is_admin": True}))

    def test_user_can_publish_to_library_roles(self):
        with patch.object(self.roles, "is_agent_platform_role_mapping_enabled", return_value=False):
            self.assertTrue(self.roles.user_can_publish_to_library({"user_id": "u"}))

        with patch.object(self.roles, "is_agent_platform_role_mapping_enabled", return_value=True):
            with patch.object(self.roles, "get_agent_platform_role", return_value="owner"):
                self.assertTrue(self.roles.user_can_publish_to_library({"user_id": "u"}))
            with patch.object(self.roles, "get_agent_platform_role", return_value="editor"):
                self.assertFalse(self.roles.user_can_publish_to_library({"user_id": "u"}))
            with patch.object(self.roles, "get_agent_platform_role", return_value="viewer"):
                self.assertFalse(self.roles.user_can_publish_to_library({"user_id": "u"}))
        self.assertTrue(
            self.roles.user_can_publish_to_library({"user_id": "u", "is_admin": True})
        )

    def test_enrich_feature_flags_include_create_and_publish(self):
        with patch.object(self.roles, "is_agent_platform_role_mapping_enabled", return_value=True):
            with patch.object(self.roles, "get_agent_platform_role", return_value="editor"):
                user = self.roles.enrich_user_feature_flags({"user_id": "u"})
        self.assertEqual(user["platform_role"], "editor")
        self.assertTrue(user["can_use_agents"])
        self.assertTrue(user["can_create_agents"])
        self.assertFalse(user["can_publish_to_library"])
        self.assertTrue(user["features"]["create_agents"])
        self.assertFalse(user["features"]["publish_to_library"])


class TestCloneGraphAndDuplicate(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.roles, cls.api = _load_clone_modules()

    async def test_collect_clone_graph_includes_private_chain_and_subagents(self):
        """Связанные агенты подтягиваются даже без publish/share."""
        root = _agent(
            id=1,
            name="Parent",
            author_id="creator",
            is_public=True,
            config={
                "agent_ids": [2],
                "subagents": {"agent_ids": [3], "enabled": True},
                "kb_document_ids": [100],
            },
        )
        chain = _agent(
            id=2,
            name="PrivateChain",
            author_id="creator",
            is_public=False,
            config={"agent_ids": [], "kb_document_ids": [200]},
        )
        sub = _agent(
            id=3,
            name="PrivateSub",
            author_id="creator",
            is_public=False,
            config={"subagents": {"agent_ids": [2]}},  # цикл/пересечение
        )
        by_id = {1: root, 2: chain, 3: sub}

        repo = MagicMock()
        repo.get_agent = AsyncMock(side_effect=lambda aid, user_id=None, **kw: by_id.get(int(aid)))

        graph = await self.api._collect_clone_graph_ids(repo, 1)
        self.assertEqual(set(graph), {1, 2, 3})
        self.assertEqual(graph[0], 1)

    async def test_duplicate_deep_clone_for_all_platform_roles(self):
        """owner/editor/viewer клонируют галерейного родителя + приватных связанных."""
        for platform_role in ("owner", "editor", "viewer"):
            with self.subTest(platform_role=platform_role):
                await self._assert_deep_clone_for_role(platform_role)

    async def _assert_deep_clone_for_role(self, platform_role: str):
        api = self.api
        cloner = {
            "user_id": f"user-{platform_role}",
            "username": f"user-{platform_role}",
            "is_admin": False,
        }
        root = _agent(
            id=1,
            name="GalleryParent",
            author_id="creator",
            is_public=True,
            my_permission="viewer",
            config={
                "agent_ids": [2],
                "subagents": {
                    "enabled": True,
                    "agent_ids": [3],
                    "agent_names": {"2": "ChainBot", "3": "SubBot"},
                },
                "kb_document_ids": [100],
                "artifacts_enabled": True,
                "mcp_enabled": True,
                "mcp_server_ids": ["mcp-1"],
            },
            system_prompt="parent prompt",
            tags=[{"id": 7, "name": "tag"}],
        )
        chain = _agent(
            id=2,
            name="HiddenChain",
            author_id="creator",
            is_public=False,
            config={"kb_document_ids": [200], "agent_ids": []},
            system_prompt="chain prompt",
        )
        sub = _agent(
            id=3,
            name="HiddenSub",
            author_id="creator",
            is_public=False,
            config={"kb_document_ids": [], "subagents": {"agent_ids": []}},
            system_prompt="sub prompt",
        )
        store: Dict[int, Any] = {1: root, 2: chain, 3: sub}
        next_id = {"n": 100}

        async def get_agent(aid, user_id=None, is_admin=False, **kw):
            agent = store.get(int(aid))
            if agent is None:
                return None
            # Для клонера корень «виден» как публичный; скрытые — только без ACL
            # (user_id is None) или уже свои клоны.
            if user_id and int(aid) == 1:
                return agent
            if user_id and str(getattr(agent, "author_id", "")).lower() == str(user_id).lower():
                return agent
            if user_id is None or is_admin:
                return agent
            return None

        async def create_agent(agent_data, author_id, author_name):
            next_id["n"] += 1
            nid = next_id["n"]
            created = _agent(
                id=nid,
                name=agent_data.name,
                author_id=author_id,
                config=dict(agent_data.config or {}),
                system_prompt=agent_data.system_prompt or "",
                tools=list(agent_data.tools or []),
                is_public=bool(agent_data.is_public),
                my_permission="owner",
                tags=[{"id": tid} for tid in (agent_data.tag_ids or [])],
            )
            store[nid] = created
            return nid

        async def update_agent(agent_id, agent_data, author_id, is_admin=False):
            ag = store[int(agent_id)]
            if agent_data.config is not None:
                ag.config = dict(agent_data.config)
            if agent_data.system_prompt is not None:
                ag.system_prompt = agent_data.system_prompt
            return True

        repo = MagicMock()
        repo.get_agent = AsyncMock(side_effect=get_agent)
        repo.user_can_access_agent = AsyncMock(return_value=True)
        repo.create_agent = AsyncMock(side_effect=create_agent)
        repo.update_agent = AsyncMock(side_effect=update_agent)

        kb_counter = {"n": 1000}

        async def fake_materialize(*, agent_repo, source, current_user, owner_uid, username):
            # Упрощённый materialize: новая карточка + новые kb ids
            cfg = api._clone_agent_config(source.config)
            old_kb = api._parse_int_ids(cfg.get("kb_document_ids"))
            new_kb = []
            for _ in old_kb:
                kb_counter["n"] += 1
                new_kb.append(kb_counter["n"])
            cfg["kb_document_ids"] = new_kb
            cfg["cloned_from"] = int(source.id)
            data = api.AgentCreate(
                name=f"{source.name} (копия)",
                description=source.description,
                system_prompt=source.system_prompt,
                config=cfg,
                tools=list(source.tools or []),
                is_public=False,
                tag_ids=[
                    int(t["id"])
                    for t in (source.tags or [])
                    if isinstance(t, dict) and t.get("id")
                ],
            )
            return await create_agent(data, current_user["user_id"], current_user.get("username"))

        req = MagicMock()
        with patch.object(api, "get_agent_repository", return_value=repo), patch.object(
            api, "is_agent_platform_role_mapping_enabled", return_value=True
        ), patch.object(
            api, "get_agent_platform_role", return_value=platform_role
        ), patch.object(
            api, "_materialize_single_agent_clone", AsyncMock(side_effect=fake_materialize)
        ), patch.object(
            api, "log_cef_event", MagicMock()
        ):
            result = await api.duplicate_agent(req, 1, cloner)

        self.assertTrue(result["success"])
        root_clone_id = int(result["agent_id"])
        self.assertEqual(result["cloned_agents"], 3)
        self.assertNotEqual(root_clone_id, 1)

        root_clone = store[root_clone_id]
        self.assertEqual(root_clone.author_id, cloner["user_id"])
        self.assertFalse(root_clone.is_public)
        self.assertEqual(root_clone.system_prompt, "parent prompt")
        self.assertTrue(root_clone.config.get("artifacts_enabled"))
        self.assertEqual(root_clone.config.get("mcp_server_ids"), ["mcp-1"])

        # Ссылки переписаны на клоны, не на оригиналы
        chain_ids = set(root_clone.config.get("agent_ids") or [])
        sub_ids = set((root_clone.config.get("subagents") or {}).get("agent_ids") or [])
        self.assertTrue(chain_ids)
        self.assertTrue(sub_ids)
        self.assertNotIn(2, chain_ids)
        self.assertNotIn(3, sub_ids)
        for cid in chain_ids | sub_ids:
            self.assertEqual(store[cid].author_id, cloner["user_id"])
            self.assertIn("(копия)", store[cid].name)

        # KB клона ≠ KB оригинала
        self.assertNotEqual(
            root_clone.config.get("kb_document_ids"),
            [100],
        )
        self.assertEqual(store[1].config.get("kb_document_ids"), [100])

        # Оригиналы не тронуты
        self.assertEqual(store[1].author_id, "creator")
        self.assertEqual(store[2].system_prompt, "chain prompt")
        self.assertEqual(store[3].system_prompt, "sub prompt")

        # Автор клона (в т.ч. viewer) может менять настройки клона цепочки
        chain_clone_id = next(iter(chain_ids))
        with patch.object(api, "get_agent_repository", return_value=repo), patch.object(
            api, "is_agent_platform_role_mapping_enabled", return_value=True
        ), patch.object(
            api, "get_agent_platform_role", return_value=platform_role
        ), patch.object(
            api, "_reject_foreign_kb_documents", AsyncMock()
        ), patch.object(
            api, "log_cef_event", MagicMock()
        ):
            edited = await api.update_agent(
                MagicMock(),
                chain_clone_id,
                api.AgentUpdate(
                    system_prompt="edited chain prompt",
                    config={
                        "kb_document_ids": [9001],
                        "agent_ids": [],
                    },
                ),
                cloner,
            )
        self.assertTrue(edited["success"])
        # исходный HiddenChain не изменился
        self.assertEqual(store[2].system_prompt, "chain prompt")
        self.assertEqual(store[2].config.get("kb_document_ids"), [200])

    async def test_clone_author_viewer_can_update_and_delete_clone_not_original(self):
        api = self.api
        viewer = {"user_id": "viewer1", "username": "viewer1", "is_admin": False}
        original = _agent(
            id=1,
            name="Orig",
            author_id="creator",
            is_public=True,
            my_permission="viewer",
            config={"kb_document_ids": [1]},
        )
        clone = _agent(
            id=50,
            name="Orig (копия)",
            author_id="viewer1",
            my_permission="owner",
            # без KB — delete не тянет backend.routes.rag / storage
            config={"kb_document_ids": [], "cloned_from": 1},
            system_prompt="old",
        )
        store = {1: original, 50: clone}

        async def get_agent(aid, user_id=None, is_admin=False, **kw):
            return store.get(int(aid))

        repo = MagicMock()
        repo.get_agent = AsyncMock(side_effect=get_agent)
        repo.update_agent = AsyncMock(return_value=True)
        repo.delete_agent = AsyncMock(return_value=True)
        repo.find_orphan_kb_document_ids = AsyncMock(return_value=[])

        req = MagicMock()
        with patch.object(api, "get_agent_repository", return_value=repo), patch.object(
            api, "is_agent_platform_role_mapping_enabled", return_value=True
        ), patch.object(
            api, "get_agent_platform_role", return_value="viewer"
        ), patch.object(
            api, "_reject_foreign_kb_documents", AsyncMock()
        ), patch.object(
            api, "log_cef_event", MagicMock()
        ):
            # Клон: правка и удаление ок
            ok = await api.update_agent(
                req,
                50,
                api.AgentUpdate(system_prompt="new prompt", config={"agent_ids": [60]}),
                viewer,
            )
            self.assertTrue(ok["success"])
            repo.update_agent.assert_awaited()

            deleted = await api.delete_agent(req, 50, viewer)
            self.assertTrue(deleted["success"])

            # Оригинал: viewer без авторства → 403 на update/delete
            from fastapi import HTTPException

            with self.assertRaises(HTTPException) as upd_ctx:
                await api.update_agent(
                    req, 1, api.AgentUpdate(system_prompt="hack"), viewer
                )
            self.assertEqual(upd_ctx.exception.status_code, 403)

            with self.assertRaises(HTTPException) as del_ctx:
                await api.delete_agent(req, 1, viewer)
            self.assertEqual(del_ctx.exception.status_code, 403)

    async def test_clone_kb_ids_not_shared_with_original(self):
        """После materialize у клона свои kb id; исходные id исходника не переиспользуются."""
        api = self.api
        source = _agent(
            id=7,
            name="WithFiles",
            author_id="creator",
            config={"kb_document_ids": [10, 11], "agent_ids": []},
        )
        created_configs: List[dict] = []

        async def create_agent(agent_data, author_id, author_name):
            created_configs.append(dict(agent_data.config or {}))
            return 77

        repo = MagicMock()
        repo.create_agent = AsyncMock(side_effect=create_agent)
        repo.update_agent = AsyncMock(return_value=True)

        with patch.object(
            api, "_clone_agent_rag_settings", AsyncMock()
        ), patch.object(
            api,
            "_clone_agent_kb_documents",
            AsyncMock(return_value=[501, 502]),
        ):
            new_id = await api._materialize_single_agent_clone(
                agent_repo=repo,
                source=source,
                current_user={"user_id": "u1", "username": "u1"},
                owner_uid="u1",
                username="u1",
            )
        self.assertEqual(new_id, 77)
        # create ушёл с пустыми kb (чтобы не шарить id), update — с новыми
        self.assertEqual(created_configs[0].get("kb_document_ids"), [])
        update_cfg = repo.update_agent.await_args.kwargs["agent_data"].config
        self.assertEqual(update_cfg.get("kb_document_ids"), [501, 502])
        self.assertEqual(source.config.get("kb_document_ids"), [10, 11])


class TestClonePermissionsMatrix(unittest.TestCase):
    """Сводная матрица: какие роли могут клонировать / править клон / править оригинал."""

    @classmethod
    def setUpClass(cls):
        cls.roles, cls.api = _load_clone_modules()

    def test_author_bypass_matrix(self):
        cases = [
            # role, is_author, expect_cap_keeps_db_owner
            ("owner", True, True),
            ("editor", True, True),
            ("viewer", True, True),
            ("owner", False, True),  # platform owner → cap оставляет owner
            ("editor", False, False),  # db owner → editor
            ("viewer", False, False),  # db owner → viewer
        ]
        for role, is_author, expect_owner in cases:
            with self.subTest(role=role, is_author=is_author):
                user = {"user_id": "u1", "is_admin": False}
                agent = _agent(
                    id=1,
                    name="A",
                    author_id="u1" if is_author else "other",
                    my_permission="owner",
                )
                with patch.object(
                    self.api, "is_agent_platform_role_mapping_enabled", return_value=True
                ):
                    self.api._apply_platform_permission_cap(agent, user, role)
                if expect_owner:
                    self.assertEqual(agent.my_permission, "owner")
                else:
                    self.assertEqual(agent.my_permission, role)


class TestEditorCreateAndPublish(unittest.IsolatedAsyncioTestCase):
    """Editor создаёт агентов, но не публикует в галерею."""

    @classmethod
    def setUpClass(cls):
        cls.roles, cls.api = _load_clone_modules()

    async def test_editor_can_create_agent_forced_private(self):
        api = self.api
        editor = {"user_id": "ed1", "username": "ed1", "is_admin": False}
        created = {}

        async def create_agent(*, agent_data, author_id, author_name):
            created["is_public"] = bool(agent_data.is_public)
            created["author_id"] = author_id
            return 101

        repo = MagicMock()
        repo.create_agent = AsyncMock(side_effect=create_agent)
        req = MagicMock()
        with patch.object(api, "get_agent_repository", return_value=repo), patch.object(
            api, "is_agent_platform_role_mapping_enabled", return_value=True
        ), patch.object(
            api, "get_agent_platform_role", return_value="editor"
        ), patch.object(
            api, "user_can_create_agents", return_value=True
        ), patch.object(
            api, "user_can_publish_to_library", return_value=False
        ), patch.object(
            api, "_reject_foreign_kb_documents", AsyncMock()
        ), patch.object(
            api, "log_cef_event", MagicMock()
        ):
            data = api.AgentCreate(
                name="Editor Agent",
                system_prompt="1234567890",
                is_public=True,
            )
            result = await api.create_agent(req, data, editor)
        self.assertFalse(created["is_public"])
        self.assertEqual(created["author_id"], "ed1")
        self.assertTrue(result["success"])
        self.assertEqual(result["agent_id"], 101)

    async def test_viewer_cannot_create_agent(self):
        api = self.api
        viewer = {"user_id": "v1", "username": "v1", "is_admin": False}
        req = MagicMock()
        from fastapi import HTTPException

        with patch.object(
            api, "is_agent_platform_role_mapping_enabled", return_value=True
        ), patch.object(
            api, "get_agent_platform_role", return_value="viewer"
        ), patch.object(
            api, "user_can_create_agents", return_value=False
        ), patch.object(
            api, "log_cef_event", MagicMock()
        ):
            with self.assertRaises(HTTPException) as ctx:
                await api.create_agent(
                    req,
                    api.AgentCreate(name="Nope", system_prompt="1234567890"),
                    viewer,
                )
        self.assertEqual(ctx.exception.status_code, 403)

    async def test_editor_cannot_publish_own_agent(self):
        api = self.api
        editor = {"user_id": "ed1", "username": "ed1", "is_admin": False}
        agent = _agent(
            id=9,
            name="Mine",
            author_id="ed1",
            is_public=False,
            my_permission="owner",
        )
        repo = MagicMock()
        repo.get_agent = AsyncMock(return_value=agent)
        repo.update_agent = AsyncMock(return_value=True)
        req = MagicMock()
        from fastapi import HTTPException

        with patch.object(api, "get_agent_repository", return_value=repo), patch.object(
            api, "is_agent_platform_role_mapping_enabled", return_value=True
        ), patch.object(
            api, "get_agent_platform_role", return_value="editor"
        ), patch.object(
            api, "user_can_publish_to_library", return_value=False
        ), patch.object(
            api, "_reject_foreign_kb_documents", AsyncMock()
        ):
            with self.assertRaises(HTTPException) as ctx:
                await api.update_agent(
                    req, 9, api.AgentUpdate(is_public=True), editor
                )
        self.assertEqual(ctx.exception.status_code, 403)
        repo.update_agent.assert_not_awaited()

    async def test_owner_can_publish_agent(self):
        api = self.api
        owner = {"user_id": "ow1", "username": "ow1", "is_admin": False}
        agent = _agent(
            id=9,
            name="Mine",
            author_id="ow1",
            is_public=False,
            my_permission="owner",
        )
        repo = MagicMock()
        repo.get_agent = AsyncMock(return_value=agent)
        repo.update_agent = AsyncMock(return_value=True)
        req = MagicMock()
        with patch.object(api, "get_agent_repository", return_value=repo), patch.object(
            api, "is_agent_platform_role_mapping_enabled", return_value=True
        ), patch.object(
            api, "get_agent_platform_role", return_value="owner"
        ), patch.object(
            api, "user_can_publish_to_library", return_value=True
        ), patch.object(
            api, "_reject_foreign_kb_documents", AsyncMock()
        ), patch.object(
            api, "log_cef_event", MagicMock()
        ):
            ok = await api.update_agent(
                req, 9, api.AgentUpdate(is_public=True), owner
            )
        self.assertTrue(ok["success"])
        repo.update_agent.assert_awaited()


if __name__ == "__main__":
    unittest.main()
