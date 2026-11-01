"""services/agent_model_settings_migration: разовая уборка model_settings у агентов с дефолтными значениями."""

import json

import pytest

from backend.services import agent_model_settings_migration as amm

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("AGENT_MODEL_SETTINGS_MIGRATE", raising=False)
    monkeypatch.setattr("backend.utils.llm_defaults.llm_settings_defaults", lambda: {"temperature": 0.3, "top_k": 10})


class TestAsDict:
    def test_dict_and_json_string(self):
        assert amm._as_dict({"a": 1}) == {"a": 1}
        assert amm._as_dict('{"a": 1}') == {"a": 1}

    @pytest.mark.parametrize("value", [None, "", "   ", "not json", "[1, 2]", 5, ["x"]])
    def test_everything_else_is_none(self, value):
        assert amm._as_dict(value) is None


class TestSameValue:
    @pytest.mark.parametrize(
        ("left", "right", "expected"),
        [
            (0.7, 0.7000000001, True),
            (1, 1.0, True),
            (0.7, 0.8, False),
            (True, True, True),
            (True, 1, False),
            (1, True, False),
            (False, False, True),
            ("a", "a", True),
            ("a", "b", False),
            (None, 0, False),
        ],
    )
    def test_compare(self, left, right, expected):
        assert amm._same_value(left, right) is expected


class TestMatchesDefaults:
    def test_equal_values_match(self):
        assert amm.matches_defaults({"temperature": 0.7, "top_k": 40}, amm._CONSTRUCTOR_DEFAULT) is True

    def test_ignored_and_unknown_keys_do_not_matter(self):
        stored = {"streaming_speed": 999, "brand_new_key": "x", "temperature": 0.7}

        assert amm.matches_defaults(stored, amm._CONSTRUCTOR_DEFAULT) is True

    def test_custom_value_does_not_match(self):
        assert amm.matches_defaults({"temperature": 1.5}, amm._CONSTRUCTOR_DEFAULT) is False

    def test_boolean_is_not_confused_with_number(self):
        assert amm.matches_defaults({"use_gpu": 0}, amm._CONSTRUCTOR_DEFAULT) is False

    def test_empty_card_matches(self):
        assert amm.matches_defaults({}, amm._CONSTRUCTOR_DEFAULT) is True


class FakeTx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeConn:
    def __init__(self, *, applied=False, rows=(), fail_on_fetch=None):
        self.applied, self.rows, self.fail_on_fetch = applied, list(rows), fail_on_fetch
        self.executed = []

    def transaction(self):
        return FakeTx()

    async def execute(self, sql, *args):
        self.executed.append((" ".join(sql.split()), args))

    async def fetchrow(self, sql, *args):
        return {"applied_at": "now"} if self.applied else None

    async def fetch(self, sql, *args):
        if self.fail_on_fetch:
            raise self.fail_on_fetch
        return self.rows


class FakeAcquired:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


class FakeAgentRepo:
    def __init__(self, conn):
        self.db_connection = self
        self.conn = conn

    async def acquire(self):
        return FakeAcquired(self.conn)


def agent(agent_id, model_settings=None, *, raw=None):
    config = raw if raw is not None else {"model_settings": model_settings}
    return {"id": agent_id, "config": config}


def statements(conn, prefix):
    return [(sql, args) for sql, args in conn.executed if sql.startswith(prefix)]


class TestEnsureMarksTable:
    async def test_creates_the_marks_table(self):
        conn = FakeConn()

        await amm._ensure_marks_table(conn)

        assert "CREATE TABLE IF NOT EXISTS backend_data_migrations" in conn.executed[0][0]


class TestCleanup:
    async def test_missing_repository_is_a_noop(self):
        assert await amm.cleanup_default_agent_model_settings(None) is None

    async def test_defaults_are_cleared_and_custom_settings_kept(self):
        conn = FakeConn(
            rows=[
                agent(1, dict(amm._CONSTRUCTOR_DEFAULT)),
                agent(2, {"temperature": 1.9}),
                agent(3, {"temperature": 0.3, "top_k": 10}),  # дефолты кластера
                agent(4, json.dumps(dict(amm._CONSTRUCTOR_DEFAULT))),  # config приходит строкой JSON-объекта
                agent(5, "not an object"),  # model_settings не объект
                agent(6, raw="broken json"),  # config нечитаем
                agent(7, None),  # model_settings отсутствует/null
            ]
        )
        conn.rows[3]["config"] = json.dumps({"model_settings": json.dumps(dict(amm._CONSTRUCTOR_DEFAULT))})

        await amm.cleanup_default_agent_model_settings(FakeAgentRepo(conn))

        [(_, update_args)] = statements(conn, "UPDATE agents")
        assert update_args == ([1, 3, 4, 5, 7],)
        [(_, mark_args)] = statements(conn, "INSERT INTO backend_data_migrations")
        assert mark_args[0] == amm.MIGRATION_NAME
        assert json.loads(mark_args[1]) == {"cleared": 5, "kept": 1, "unreadable": 1}
        assert statements(conn, "SELECT pg_advisory_xact_lock")[0][1] == (amm._MIGRATION_LOCK_ID,)

    async def test_nothing_to_clear_skips_the_update(self):
        conn = FakeConn(rows=[agent(1, {"temperature": 1.9})])

        await amm.cleanup_default_agent_model_settings(FakeAgentRepo(conn))

        assert statements(conn, "UPDATE agents") == []
        assert json.loads(statements(conn, "INSERT INTO backend_data_migrations")[0][1][1])["kept"] == 1

    async def test_already_applied_cleanup_is_skipped(self):
        conn = FakeConn(applied=True, rows=[agent(1, dict(amm._CONSTRUCTOR_DEFAULT))])

        await amm.cleanup_default_agent_model_settings(FakeAgentRepo(conn))

        assert statements(conn, "UPDATE agents") == []
        assert statements(conn, "INSERT INTO backend_data_migrations") == []

    async def test_force_removes_the_mark_and_runs_again(self, monkeypatch):
        monkeypatch.setenv("AGENT_MODEL_SETTINGS_MIGRATE", "FORCE")
        conn = FakeConn(applied=True, rows=[agent(1, dict(amm._CONSTRUCTOR_DEFAULT))])

        await amm.cleanup_default_agent_model_settings(FakeAgentRepo(conn))

        [(_, delete_args)] = statements(conn, "DELETE FROM backend_data_migrations")
        assert delete_args == (amm.MIGRATION_NAME,)
        assert statements(conn, "UPDATE agents")[0][1] == ([1],)

    async def test_database_errors_do_not_break_startup(self):
        conn = FakeConn(fail_on_fetch=RuntimeError("db down"))

        await amm.cleanup_default_agent_model_settings(FakeAgentRepo(conn))

        assert statements(conn, "INSERT INTO backend_data_migrations") == []
