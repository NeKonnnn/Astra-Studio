"""api_users + auth/user_directory: поиск сотрудников в LDAP для шаринга, кэш ФИО."""

from types import SimpleNamespace

import pytest

from backend import api_users as module
from backend.auth import user_directory as directory

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clean_caches():
    directory._search_cache.clear()
    directory._full_name_cache.clear()
    yield
    directory._search_cache.clear()
    directory._full_name_cache.clear()


@pytest.fixture
def ldap(monkeypatch):
    import backend.auth.ldap_auth as ldap_auth

    state = SimpleNamespace(enabled=True, hits=[{"user_id": "ivanov", "username": "ivanov", "full_name": "Иван Иванов", "email": "i@corp"}], calls=[], error=None, profiles={})

    def search(query, limit, for_share=False):
        state.calls.append((query, limit, for_share))
        if state.error:
            raise state.error
        return state.hits

    def bulk(names):
        state.calls.append(("bulk", list(names)))
        return {n.lower(): state.profiles[n.lower()] for n in names if n.lower() in state.profiles}

    monkeypatch.setattr(ldap_auth, "is_ldap_enabled", lambda: state.enabled)
    monkeypatch.setattr(ldap_auth, "search_ldap_users", search)
    monkeypatch.setattr(ldap_auth, "fetch_ldap_user_profiles_bulk", bulk)
    return state


class TestSearchEndpoint:
    URL = "/api/users/search"

    async def test_requires_login(self, make_client, ldap):
        response = await make_client(module.router, user=None).get(self.URL, params={"q": "iv"})

        assert response.status_code in (401, 403) and ldap.calls == []

    async def test_returns_matching_users(self, make_client, ldap):
        response = await make_client(module.router).get(self.URL, params={"q": "ivan"})

        assert response.json() == {"users": [{"user_id": "ivanov", "username": "ivanov", "full_name": "Иван Иванов", "email": "i@corp"}]}
        assert ldap.calls == [("ivan", 10, False)]

    async def test_share_mode_and_limit_are_forwarded(self, make_client, ldap):
        await make_client(module.router).get(self.URL, params={"q": "ivan", "limit": 5, "for_share": True})

        assert ldap.calls == [("ivan", 5, True)]

    @pytest.mark.parametrize("params", [{}, {"q": ""}, {"q": "x" * 201}, {"q": "ab", "limit": 0}, {"q": "ab", "limit": 21}])
    async def test_validates_parameters(self, make_client, ldap, params):
        assert (await make_client(module.router).get(self.URL, params=params)).status_code == 422

    async def test_single_character_queries_return_nothing_without_hitting_ldap(self, make_client, ldap):
        response = await make_client(module.router).get(self.URL, params={"q": "i"})

        assert response.json() == {"users": []} and ldap.calls == []

    async def test_ldap_disabled(self, make_client, ldap):
        ldap.enabled = False

        assert (await make_client(module.router).get(self.URL, params={"q": "ivan"})).json() == {"users": []}

    async def test_ldap_errors_give_an_empty_list(self, make_client, ldap):
        ldap.error = RuntimeError("ldap down")

        response = await make_client(module.router).get(self.URL, params={"q": "ivan"})

        assert response.status_code == 200 and response.json() == {"users": []}

    async def test_email_and_names_are_exposed_to_every_logged_in_user_known_issue(self, make_client, ldap):
        # Каталог сотрудников (ФИО + почта) доступен любому вошедшему пользователю:
        # перечисление всей организации по префиксам. Ограничений частоты нет.
        response = await make_client(module.router, user={"username": "intern", "user_id": "intern"}).get(self.URL, params={"q": "iv"})

        assert response.json()["users"][0]["email"] == "i@corp"


class TestSearchCache:
    async def test_repeated_queries_hit_the_cache(self, ldap):
        await directory.search_directory_users("Ivan", 10)
        await directory.search_directory_users("ivan", 10)

        assert len(ldap.calls) == 1

    async def test_cache_key_includes_limit_and_share_mode(self, ldap):
        await directory.search_directory_users("ivan", 10)
        await directory.search_directory_users("ivan", 5)
        await directory.search_directory_users("ivan", 10, for_share=True)

        assert len(ldap.calls) == 3

    async def test_expired_entries_are_refreshed(self, ldap, monkeypatch):
        await directory.search_directory_users("ivan", 10)
        for key in list(directory._search_cache):
            cached_at, users = directory._search_cache[key]
            directory._search_cache[key] = (cached_at - 1000, users)

        await directory.search_directory_users("ivan", 10)

        assert len(ldap.calls) == 2

    async def test_cache_is_bounded(self, ldap, monkeypatch):
        monkeypatch.setattr(directory, "_SEARCH_CACHE_MAX", 3)

        for i in range(6):
            await directory.search_directory_users(f"user{i}", 10)

        assert len(directory._search_cache) == 3

    async def test_errors_are_not_cached(self, ldap):
        ldap.error = RuntimeError("down")
        assert await directory.search_directory_users("ivan", 10) == []
        ldap.error = None

        assert len(await directory.search_directory_users("ivan", 10)) == 1

    async def test_limit_is_clamped(self, ldap):
        await directory.search_directory_users("ivan", 999)

        assert ldap.calls[0][1] == 20


class TestFullNames:
    async def test_resolves_names_in_one_bulk_call_and_caches_them(self, ldap):
        ldap.profiles = {"ivanov": {"full_name": "Иван Иванов"}, "petrov": {"full_name": None}}

        first = await directory.resolve_full_names(["ivanov", "Petrov", "ivanov", "  ", "ghost"])
        second = await directory.resolve_full_names(["ivanov"])

        assert first == {"ivanov": "Иван Иванов", "Petrov": None, "ghost": None}
        assert second == {"ivanov": "Иван Иванов"}
        assert [c for c in ldap.calls if c[0] == "bulk"] == [("bulk", ["ivanov", "Petrov", "ghost"])]

    async def test_without_ldap_every_name_is_unknown(self, ldap):
        ldap.enabled = False

        assert await directory.resolve_full_names(["ivanov"]) == {"ivanov": None}

    async def test_lookup_failures_are_survivable(self, ldap, monkeypatch):
        import backend.auth.ldap_auth as ldap_auth

        def boom(names):
            raise RuntimeError("ldap down")

        monkeypatch.setattr(ldap_auth, "fetch_ldap_user_profiles_bulk", boom)

        assert await directory.resolve_full_names(["ivanov"]) == {"ivanov": None}

    async def test_enrich_sets_author_full_names_case_insensitively(self, ldap):
        ldap.profiles = {"ivanov": {"full_name": "Иван Иванов"}}
        items = [SimpleNamespace(author_id="Ivanov", author_full_name="old"), SimpleNamespace(author_name="ivanov", author_full_name=None), SimpleNamespace(author_full_name="keep")]

        await directory.enrich_items_author_full_names(items)

        # Третий элемент без автора: уже заданное имя затирается None (поведение функции).
        assert [i.author_full_name for i in items] == ["Иван Иванов", "Иван Иванов", None]

    async def test_enrich_with_no_items(self, ldap):
        await directory.enrich_items_author_full_names([])

        assert ldap.calls == []
