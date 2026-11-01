"""services/entity_rag_migration: разовый перенос RAG-настроек пользователей к агентам и проектам."""

import pytest

from backend.services import entity_rag_migration as erm
from backend.services import user_rag_settings as urs

pytestmark = pytest.mark.unit

KEYS = ("rag_strategy", "rag_chat_top_k", "rag_system_prompt")


@pytest.fixture(autouse=True)
def _no_force(monkeypatch):
    monkeypatch.delenv("RAG_ENTITY_SETTINGS_MIGRATE", raising=False)


class TestLegacyEffectiveSettings:
    def test_non_dict_storage_gives_nothing(self):
        assert erm.legacy_effective_settings(None, "agent", "1", KEYS) == {}
        assert erm.legacy_effective_settings("junk", "agent", "1", KEYS) == {}

    def test_root_keys_only_known_and_non_null(self):
        stored = {"rag_strategy": "vector", "rag_chat_top_k": None, "other": 1}

        assert erm.legacy_effective_settings(stored, "agent", "1", KEYS) == {"rag_strategy": "vector"}

    def test_scope_overrides_root_and_entity_overrides_scope(self):
        stored = {
            "rag_strategy": "root",
            "rag_chat_top_k": 5,
            "scopes": {
                "agent": {
                    "rag_strategy": "scope",
                    "rag_system_prompt": "from scope",
                    "rag_memory_strategy": "legacy-only-key",
                    "rag_chunk_size": None,
                    "not_a_legacy_key": 1,
                },
                "project": {"rag_strategy": "other-scope"},
            },
            "entities": {"agent": {"7": {"rag_strategy": "entity", "rag_chat_top_k": None, "junk": 1}}},
        }

        out = erm.legacy_effective_settings(stored, "agent", 7, KEYS + ("rag_memory_strategy",))

        assert out == {
            "rag_strategy": "entity",
            "rag_chat_top_k": 5,
            "rag_system_prompt": "from scope",
            "rag_memory_strategy": "legacy-only-key",
        }

    def test_malformed_scopes_and_entities_are_tolerated(self):
        stored = {"scopes": ["x"], "entities": {"agent": "junk"}, "rag_strategy": "root"}
        assert erm.legacy_effective_settings(stored, "agent", "1", KEYS) == {"rag_strategy": "root"}
        stored = {"scopes": {"agent": "junk"}, "entities": {"agent": {"1": "junk"}}}
        assert erm.legacy_effective_settings(stored, "agent", "1", KEYS) == {}
        assert erm.legacy_effective_settings({"entities": "junk"}, "agent", "1", KEYS) == {}


class TestDiffersFromDefaults:
    def test_only_changed_keys_are_kept(self):
        snapshot = {"rag_strategy": "vector", "rag_chat_top_k": 12, "extra": 1}
        defaults = {"rag_strategy": "hybrid", "rag_chat_top_k": 12}

        assert erm._differs_from_defaults(snapshot, defaults, KEYS) == {"rag_strategy": "vector"}

    def test_everything_equal_gives_empty_payload(self):
        assert erm._differs_from_defaults({"rag_strategy": "hybrid"}, {"rag_strategy": "hybrid"}, KEYS) == {}

    def test_key_without_default_counts_as_different(self):
        assert erm._differs_from_defaults({"rag_system_prompt": "x"}, {}, KEYS) == {"rag_system_prompt": "x"}


class FakeRepo:
    def __init__(self, *, applied=False, agents=(), projects=(), inserted=None):
        self.applied = applied
        self.agents, self.projects = list(agents), list(projects)
        self.inserted = inserted
        self.cleared = False
        self.marks = []
        self.insert_calls = []

    async def clear_migration_mark(self):
        self.cleared = True

    async def migration_applied(self):
        return self.applied

    async def owners_snapshot(self):
        return self.agents, self.projects

    async def insert_missing(self, rows):
        self.insert_calls.append(rows)
        return len(rows) if self.inserted is None else self.inserted

    async def count_foreign_entity_copies(self):
        return 2

    async def mark_migration(self, details):
        self.marks.append(details)


class FakeUserSettingsRepo:
    def __init__(self, rows):
        self.rows, self.reads = rows, []

    async def get(self, owner):
        self.reads.append(owner)
        return self.rows.get(owner)


@pytest.fixture
def defaults(monkeypatch):
    monkeypatch.setattr(urs, "RAG_SETTING_KEYS", KEYS)
    monkeypatch.setattr(urs, "default_rag_settings_snapshot", lambda: {"rag_strategy": "hybrid", "rag_chat_top_k": 12, "rag_system_prompt": ""})


def use_user_repo(monkeypatch, repo):
    monkeypatch.setattr(erm, "_get_user_settings_repo", lambda: repo)


class TestMigrate:
    async def test_already_applied_does_nothing(self, defaults):
        repo = FakeRepo(applied=True, agents=[("1", "alice")])

        await erm.migrate_user_settings_to_entities(repo)

        assert repo.marks == [] and repo.insert_calls == [] and repo.cleared is False

    async def test_force_clears_the_mark_and_reruns(self, defaults, monkeypatch):
        monkeypatch.setenv("RAG_ENTITY_SETTINGS_MIGRATE", " FORCE ")
        repo = FakeRepo(applied=True)

        await erm.migrate_user_settings_to_entities(repo)

        assert repo.cleared is True
        assert repo.marks == [{"agents": 0, "projects": 0}]

    async def test_no_entities_marks_the_migration_as_empty(self, defaults):
        repo = FakeRepo()

        await erm.migrate_user_settings_to_entities(repo)

        assert repo.marks == [{"agents": 0, "projects": 0}]

    async def test_unavailable_user_settings_means_no_mark(self, defaults, monkeypatch):
        use_user_repo(monkeypatch, None)
        repo = FakeRepo(agents=[("1", "alice")])

        await erm.migrate_user_settings_to_entities(repo)

        assert repo.marks == [] and repo.insert_calls == []

    async def test_only_entities_that_differ_from_defaults_are_moved(self, defaults, monkeypatch):
        users = FakeUserSettingsRepo(
            {
                "alice": {
                    "rag_settings": {
                        "scopes": {"agent": {"rag_chat_top_k": 30}},
                        "entities": {"project": {"p1": {"rag_strategy": "vector"}}},
                    }
                },
                "bob": {"rag_settings": "junk"},
                "carol": None,
            }
        )
        use_user_repo(monkeypatch, users)
        repo = FakeRepo(
            agents=[("1", "alice"), ("2", "bob")],
            projects=[("p1", "alice"), ("p2", "carol")],
        )

        await erm.migrate_user_settings_to_entities(repo)

        assert repo.insert_calls == [
            [
                ("agent", "1", {"rag_chat_top_k": 30}, "alice"),
                ("project", "p1", {"rag_strategy": "vector"}, "alice"),
            ]
        ]
        assert repo.marks == [
            {
                "agent_moved": 1,
                "agent_default": 1,
                "project_moved": 1,
                "project_default": 1,
                "inserted": 2,
                "dropped_foreign": 2,
            }
        ]
        # строка каждого владельца читается один раз
        assert sorted(users.reads) == ["alice", "bob", "carol"]

    async def test_partial_insert_is_recorded(self, defaults, monkeypatch):
        users = FakeUserSettingsRepo({"alice": {"rag_settings": {"rag_strategy": "vector"}}})
        use_user_repo(monkeypatch, users)
        repo = FakeRepo(agents=[("1", "alice")], inserted=0)

        await erm.migrate_user_settings_to_entities(repo)

        assert repo.marks[0]["inserted"] == 0 and repo.marks[0]["agent_moved"] == 1


class TestUserSettingsRepoLookup:
    def test_returns_repository(self, monkeypatch):
        sentinel = object()
        monkeypatch.setattr("backend.database.init_db.get_user_settings_repository", lambda: sentinel)

        assert erm._get_user_settings_repo() is sentinel

    def test_failure_gives_none(self, monkeypatch):
        def boom():
            msg = "db not initialised"
            raise RuntimeError(msg)

        monkeypatch.setattr("backend.database.init_db.get_user_settings_repository", boom)

        assert erm._get_user_settings_repo() is None
