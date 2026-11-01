"""Полный in-memory репозиторий агентов для тестов api_agents (не тест)."""

from types import SimpleNamespace

from backend.database.postgresql.agent_models import AgentStats, AgentWithTags

LONG_PROMPT = "Ты полезный ассистент банка."


def make_agent(aid, author="alice", **kw):
    return AgentWithTags(
        id=aid,
        name=kw.pop("name", f"Agent {aid}"),
        system_prompt=kw.pop("system_prompt", LONG_PROMPT),
        author_id=author,
        author_name=author,
        my_permission=kw.pop("my_permission", "owner"),
        **kw,
    )


class FullAgentRepo:
    """Репозиторий агентов: ACL как в настоящем (get_agent(user_id=None) отдаёт без проверки)."""

    def __init__(self):
        self.agents = {}
        self.calls = []
        self.fail_on = set()  # имена методов, которые падают RuntimeError("db down")
        self.deny_access = False
        self.hidden_ids = set()  # get_agent(..., user_id=<не None>) их не видит
        self.hidden_for_none = set()  # get_agent(..., user_id=None) их не видит
        self.next_ids = []  # скрипт id для create_agent (None = отказ); потом автоинкремент
        self.create_raises_for = set()
        self.foreign_docs = []
        self.orphans = []
        self.tags = [{"id": 1, "name": "bank"}]
        self.list_result = ([], 0)
        self.shared_result = ([], 0)
        self.bookmarks = []
        self.shares = []
        self.update_ok = True
        self.delete_results = {}
        self.share_result = (["bob"], [])
        self.unshare_ok = True
        self.rate_ok = True
        self.stats = None
        self.views_ok = True
        self.usage_ok = True
        self.bookmark_ok = True
        self.vanish_after = {}  # id -> сколько раз get_agent(user_id=None) ещё отдаёт агента

    def add(self, agent):
        self.agents[agent.id] = agent
        return agent

    def _chk(self, name):
        self.calls.append((name,))
        if name in self.fail_on:
            raise RuntimeError("db down")

    async def list_tags(self):
        self._chk("list_tags")
        return self.tags

    async def get_agent(self, aid, user_id=None, is_admin=False):
        self._chk("get_agent")
        agent = self.agents.get(aid)
        if not agent:
            return None
        if user_id is None:
            if aid in self.vanish_after:
                if self.vanish_after[aid] <= 0:
                    return None
                self.vanish_after[aid] -= 1
            return None if aid in self.hidden_for_none else agent
        if aid in self.hidden_ids:
            return None
        if is_admin or agent.is_public or agent.author_id == user_id:
            return agent
        return None

    async def user_can_access_agent(self, aid, user_id, is_admin=False):
        self._chk("user_can_access_agent")
        return not self.deny_access

    async def increment_views(self, aid):
        self._chk("increment_views")
        return self.views_ok and aid in self.agents

    async def increment_usage(self, aid):
        self._chk("increment_usage")
        return self.usage_ok and aid in self.agents

    async def create_agent(self, agent_data, author_id, author_name):
        self._chk("create_agent")
        self.calls.append(("create", agent_data.name, author_id, author_name, agent_data.is_public))
        if self.next_ids:
            aid = self.next_ids.pop(0)
        else:
            aid = max(self.agents, default=0) + 1
        if aid in self.create_raises_for:
            raise RuntimeError("create failed")
        if aid is None:
            return None
        stored = make_agent(
            aid,
            author_id,
            name=agent_data.name,
            description=agent_data.description,
            system_prompt=agent_data.system_prompt,
            config=dict(agent_data.config or {}),
            tools=list(agent_data.tools or []),
            is_public=agent_data.is_public,
        )
        # Как «вернул существующий id»: перезаписываем только если id свободен.
        self.agents.setdefault(aid, stored)
        return aid

    async def update_agent(self, agent_id, agent_data, author_id, is_admin=False):
        self._chk("update_agent")
        self.calls.append(("update", agent_id, author_id, is_admin, agent_data.config))
        agent = self.agents.get(agent_id)
        if not self.update_ok or not agent:
            return False
        if agent_data.config is not None:
            agent.config = dict(agent_data.config)
        return True

    async def delete_agent(self, agent_id, author_id, is_admin=False):
        self._chk("delete_agent")
        ok = self.delete_results.get(agent_id, True)
        if ok and agent_id in self.agents:
            self.calls.append(("delete", agent_id, author_id, is_admin))
            del self.agents[agent_id]
            return True
        return False

    async def find_documents_claimed_by_other_agents(self, agent_id, ids):
        self._chk("find_documents_claimed_by_other_agents")
        return [i for i in ids if i in self.foreign_docs]

    async def find_orphan_kb_document_ids(self, ids):
        self._chk("find_orphan_kb_document_ids")
        return [i for i in ids if i in self.orphans]

    async def get_agents(self, filters, user_id=None, is_admin=False):
        self._chk("get_agents")
        self.calls.append(("get_agents", filters, user_id, is_admin))
        return self.list_result

    async def get_shared_with_me(self, user_id, limit, offset):
        self._chk("get_shared_with_me")
        self.calls.append(("shared", user_id, limit, offset))
        return self.shared_result

    async def rate_agent(self, agent_id, user_id, rating):
        self._chk("rate_agent")
        self.calls.append(("rate", agent_id, user_id, rating))
        return self.rate_ok

    async def get_agent_stats(self, agent_id):
        self._chk("get_agent_stats")
        return self.stats

    async def add_bookmark(self, agent_id, user_id):
        self._chk("add_bookmark")
        return self.bookmark_ok

    async def remove_bookmark(self, agent_id, user_id):
        self._chk("remove_bookmark")
        return self.bookmark_ok

    async def get_user_bookmarks(self, user_id, limit, offset):
        self._chk("get_user_bookmarks")
        self.calls.append(("bookmarks", user_id, limit, offset))
        return self.bookmarks, len(self.bookmarks)

    async def share_agent(self, agent_id, owner_id, usernames, permission, recipient_permissions):
        self._chk("share_agent")
        self.calls.append(("share", agent_id, owner_id, list(usernames), permission))
        return self.share_result

    async def list_agent_shares(self, agent_id, owner_id):
        self._chk("list_agent_shares")
        return self.shares

    async def unshare_agent(self, agent_id, actor_id, target_user_id):
        self._chk("unshare_agent")
        self.calls.append(("unshare", agent_id, actor_id, target_user_id))
        return self.unshare_ok


def stats_for(aid):
    return AgentStats(
        agent_id=aid, views_count=3, usage_count=2, average_rating=4.5, total_votes=2, rating_distribution={5: 1, 4: 1}
    )


def share_row(uid, permission="viewer"):
    return SimpleNamespace(shared_with_user_id=uid, permission=permission)
