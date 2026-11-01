"""user_directory: резолв ФИО, обогащение авторов, поиск пользователей и кэш (LDAP подменён)."""

import sys
from types import SimpleNamespace

import pytest

from backend.auth import user_directory as ud

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clean_caches(monkeypatch):
    monkeypatch.setattr(ud, "_full_name_cache", {})
    monkeypatch.setattr(ud, "_search_cache", {})


@pytest.fixture
def ldap(monkeypatch):
    """Фейковый backend.auth.ldap_auth."""
    state = SimpleNamespace(enabled=True, profiles={}, bulk_calls=[], search_calls=[], hits=[], bulk_error=None, search_error=None)

    def bulk(ids):
        state.bulk_calls.append(list(ids))
        if state.bulk_error:
            raise state.bulk_error
        return state.profiles

    def search(q, limit, for_share=False):
        state.search_calls.append((q, limit, for_share))
        if state.search_error:
            raise state.search_error
        return state.hits

    fake = SimpleNamespace(
        fetch_ldap_user_profiles_bulk=bulk, is_ldap_enabled=lambda: state.enabled, search_ldap_users=search
    )
    monkeypatch.setitem(sys.modules, "backend.auth.ldap_auth", fake)
    return state


class TestSearchCache:
    def test_expired_entry_is_dropped(self, monkeypatch):
        ud._search_cache["k"] = (0.0, [{"u": 1}])
        monkeypatch.setattr(ud.time, "monotonic", lambda: 1000.0)

        assert ud._search_cache_get("k") is None and "k" not in ud._search_cache

    def test_fresh_entry_is_returned(self, monkeypatch):
        ud._search_cache["k"] = (990.0, [{"u": 1}])
        monkeypatch.setattr(ud.time, "monotonic", lambda: 1000.0)

        assert ud._search_cache_get("k") == [{"u": 1}]

    def test_put_evicts_oldest_when_full(self, monkeypatch):
        monkeypatch.setattr(ud, "_SEARCH_CACHE_MAX", 2)
        ud._search_cache["old"] = (1.0, [])
        ud._search_cache["new"] = (5.0, [])

        ud._search_cache_put("third", [])

        assert set(ud._search_cache) == {"new", "third"}


class TestResolveFullNames:
    async def test_resolves_and_caches(self, ldap):
        ldap.profiles = {"ivan": {"full_name": "Иван Иванов"}}

        first = await ud.resolve_full_names([" Ivan ", "ivan", "", "ghost"])
        second = await ud.resolve_full_names(["IVAN"])

        assert first == {"Ivan": "Иван Иванов", "ghost": None}
        assert second == {"IVAN": "Иван Иванов"}
        assert ldap.bulk_calls == [["Ivan", "ghost"]]  # дубликаты отброшены, повтор из кэша

    async def test_all_cached_skips_ldap(self, ldap):
        ud._full_name_cache["ivan"] = "Иван"

        assert await ud.resolve_full_names(["ivan"]) == {"ivan": "Иван"}
        assert ldap.bulk_calls == []

    async def test_ldap_disabled_gives_none(self, ldap):
        ldap.enabled = False

        assert await ud.resolve_full_names(["a", "b"]) == {"a": None, "b": None}
        assert ldap.bulk_calls == []

    async def test_bulk_error_gives_none_names(self, ldap):
        ldap.bulk_error = RuntimeError("ldap down")

        assert await ud.resolve_full_names(["a"]) == {"a": None}

    async def test_thread_failure_gives_none_names(self, ldap, monkeypatch):
        async def boom(fn, *a, **k):
            raise RuntimeError("thread")

        monkeypatch.setattr(ud.asyncio, "to_thread", boom)

        assert await ud.resolve_full_names(["a"]) == {"a": None}


class Item:
    def __init__(self, author_id=None, author_name=None):
        self.author_id, self.author_name, self.author_full_name = author_id, author_name, "старое"


class TestEnrichItems:
    async def test_empty_list_is_noop(self):
        await ud.enrich_items_author_full_names([])

    async def test_sets_names_case_insensitively(self, ldap):
        ldap.profiles = {"ivan": {"full_name": "Иван"}}
        a, b, c = Item(author_id="Ivan"), Item(author_name="IVAN"), Item()

        await ud.enrich_items_author_full_names([a, b, c])

        assert a.author_full_name == "Иван" and b.author_full_name == "Иван"
        assert c.author_full_name is None  # автора нет - имя сброшено

    async def test_no_names_resolved_clears_field(self, monkeypatch):
        async def none(ids):
            return {}

        monkeypatch.setattr(ud, "resolve_full_names", none)
        with_field = Item(author_id="x")
        without_field = SimpleNamespace(author_id="y")

        await ud.enrich_items_author_full_names([with_field, without_field])

        assert with_field.author_full_name is None and not hasattr(without_field, "author_full_name")

    async def test_readonly_item_in_empty_branch_is_ignored(self, monkeypatch):
        async def none(ids):
            return {}

        class Frozen:
            author_id = "x"

            @property
            def author_full_name(self):
                return "const"

            @author_full_name.setter
            def author_full_name(self, v):
                raise AttributeError("read-only")

        monkeypatch.setattr(ud, "resolve_full_names", none)

        await ud.enrich_items_author_full_names([Frozen()])  # не должно падать

    async def test_readonly_item_with_names_is_ignored(self, monkeypatch):
        async def names(ids):
            return {"x": "Имя"}

        class Frozen:
            author_id = "x"
            __slots__ = ()

        monkeypatch.setattr(ud, "resolve_full_names", names)

        await ud.enrich_items_author_full_names([Frozen()])  # присваивание падает - игнорируется


class TestSearchDirectoryUsers:
    async def test_short_query_returns_empty(self, ldap):
        assert await ud.search_directory_users(" a ") == [] and ldap.search_calls == []

    async def test_limit_is_clamped_and_result_cached(self, ldap):
        ldap.hits = [{"username": "ivan"}]

        first = await ud.search_directory_users("iva", limit=500, for_share=True)
        second = await ud.search_directory_users("IVA", limit=500, for_share=True)

        assert first == second == [{"username": "ivan"}]
        assert ldap.search_calls == [("iva", 20, True)]

    async def test_limit_lower_bound(self, ldap):
        await ud.search_directory_users("iva", limit=-5)

        assert ldap.search_calls[0][1] == 1

    async def test_ldap_disabled_returns_empty(self, ldap):
        ldap.enabled = False

        assert await ud.search_directory_users("ivan") == []

    async def test_search_error_returns_empty_and_is_not_cached(self, ldap):
        ldap.search_error = RuntimeError("down")

        assert await ud.search_directory_users("ivan") == []
        assert ud._search_cache == {}

    async def test_import_failure_returns_empty(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "backend.auth.ldap_auth", None)  # import -> ImportError

        assert await ud.search_directory_users("ivan") == []
