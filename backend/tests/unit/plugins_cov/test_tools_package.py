"""tools/*: контекст инструментов, view_skill, совместимые заглушки пакета и промпт артефактов."""

import asyncio
import contextvars
import json
from types import SimpleNamespace

import pytest

from backend import tools as tools_pkg
from backend.prompts import artifacts as art
from backend.tools import skill_tools as st
from backend.tools import tool_context as tc

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def clean_context():
    tc.set_tool_context({})
    yield
    tc.set_tool_context({})


class TestToolsPackage:
    def test_static_registry_is_empty(self):
        assert tools_pkg.get_all_tools() == [] and tools_pkg.get_tool_categories() == {}
        assert tools_pkg.get_tools_info() == {"total_count": 0, "categories": {}, "tools": []}


class TestToolContext:
    def test_contextvar_value_wins(self):
        tc.set_tool_context({"streaming": True, "current_user": {"user_id": "a"}})
        assert tc.get_tool_context()["current_user"] == {"user_id": "a"}

    def test_set_stores_the_object_itself(self):
        ctx = {"k": 1}
        tc.set_tool_context(ctx)
        ctx["k"] = 2
        assert tc.get_tool_context()["k"] == 2

    def test_empty_context_gives_empty_dict(self):
        assert contextvars.Context().run(tc.get_tool_context) == {}

    def test_context_does_not_leak_between_requests(self):
        contextvars.Context().run(tc.set_tool_context, {"current_user": {"user_id": "alice"}})
        assert contextvars.Context().run(tc.get_tool_context) == {}

    def test_explicit_copy_context_passes_context_to_worker(self):
        tc.set_tool_context({"current_user": {"user_id": "bob"}})
        ctx = contextvars.copy_context()
        assert ctx.run(tc.get_tool_context) == {"current_user": {"user_id": "bob"}}


class Skill:
    def __init__(self, name="Deploy", content="шаги", active=True):
        self.name, self.content, self.is_active = name, content, active


class FakeRepo:
    def __init__(self, skills=(), error=None):
        self.skills, self.error, self.calls = list(skills), error, []

    async def resolve_skills_for_user(self, identifiers, user_id, is_admin=False):
        self.calls.append((identifiers, user_id, is_admin))
        if self.error:
            raise self.error
        return list(self.skills)


@pytest.fixture
def repo(monkeypatch):
    import backend.database.init_db as init_db

    holder = SimpleNamespace(repo=FakeRepo([Skill()]))
    monkeypatch.setattr(init_db, "get_skill_repository", lambda: holder.repo)
    tc.set_tool_context({"current_user": {"user_id": "u1", "is_admin": True}})
    return holder


def call(skill_id="Deploy"):
    return json.loads(st.view_skill.invoke({"id": skill_id}))


class TestViewSkill:
    def test_returns_name_and_content(self, repo):
        assert call(" Deploy ") == {"name": "Deploy", "content": "шаги"}
        assert repo.repo.calls == [(["deploy"], "u1", True)]

    @pytest.mark.parametrize("value", ["", "   "])
    def test_empty_id(self, repo, value):
        assert call(value) == {"error": "Skill id is required"}
        assert repo.repo.calls == []

    def test_without_user_context(self, repo):
        tc.set_tool_context({})
        assert call() == {"error": "User context not available"}
        assert repo.repo.calls == []

    def test_unknown_skill(self, repo):
        repo.repo.skills = []
        assert call("Ghost") == {"error": "Skill 'Ghost' not found"}

    def test_inactive_skill_is_hidden(self, repo):
        repo.repo.skills = [Skill(active=False)]
        assert call() == {"error": "Skill 'Deploy' not found"}

    def test_repository_error_is_returned_as_json(self, repo):
        repo.repo.error = RuntimeError("db down")
        assert call() == {"error": "db down"}

    def test_regular_user_is_not_admin(self, repo):
        tc.set_tool_context({"current_user": {"user_id": "u2"}})
        call()
        assert repo.repo.calls[0][1:] == ("u2", False)

    async def test_works_inside_running_loop(self, repo):
        assert call() == {"name": "Deploy", "content": "шаги"}

    async def test_works_from_worker_thread(self, repo):
        out = await asyncio.to_thread(st.view_skill.invoke, {"id": "Deploy"})
        assert json.loads(out)["name"] == "Deploy"

    def test_skill_outside_agent_allowlist_is_rejected(self, repo):
        tc.set_tool_context({"current_user": {"user_id": "u1"}, "__skill_ids__": ["other-skill"]})
        assert call("Deploy") == {"error": "Skill 'Deploy' not found"}
        assert repo.repo.calls == []

    def test_skill_inside_agent_allowlist_is_loaded(self, repo):
        tc.set_tool_context({"current_user": {"user_id": "u1"}, "__skill_ids__": [" Deploy "]})
        assert call("deploy")["name"] == "Deploy"


class TestGetSkillTools:
    def test_returns_tool_only_with_skills(self):
        assert st.get_skill_tools() == [] and st.get_skill_tools([]) == []
        assert st.get_skill_tools(["a"]) == [st.view_skill]
        assert st.view_skill.name == "view_skill"


class TestArtifactsPrompt:
    def test_base_prompt(self):
        assert art.build_artifacts_prompt() == art.ARTIFACTS_PROMPT
        assert ":::artifact" in art.ARTIFACTS_PROMPT

    def test_shadcn_addendum(self):
        out = art.build_artifacts_prompt(shadcn_enabled=True)
        assert out.startswith(art.ARTIFACTS_PROMPT) and out.endswith(art.SHADCN_ARTIFACTS_ADDENDUM)
        assert "shadcn/ui" in art.SHADCN_ARTIFACTS_ADDENDUM

    @pytest.mark.parametrize("profile", [None, "x", {}, {"artifacts_enabled": False}, {"artifacts_enabled": True, "user_prompt_mode": True}])
    def test_no_injection(self, profile):
        assert art.maybe_artifacts_prompt_for_agent(profile) is None

    def test_enabled(self):
        assert art.maybe_artifacts_prompt_for_agent({"artifacts_enabled": True}) == art.ARTIFACTS_PROMPT
        assert art.maybe_artifacts_prompt_for_agent({"artifacts_enabled": 1, "shadcn_enabled": 1}) == art.build_artifacts_prompt(shadcn_enabled=True)
