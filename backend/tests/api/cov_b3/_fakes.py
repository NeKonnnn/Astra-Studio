"""Общие заглушки для тестов B3 (не тесты): роли платформы и «падающие» репозитории."""

import pytest

from backend.auth import agent_platform_roles as roles

ROLE_ENV = (
    "AGENT_PLATFORM_ROLE_MAPPING_ENABLED",
    "AGENT_ROLE_OWNER_GROUPS",
    "AGENT_ROLE_EDITOR_GROUPS",
    "AGENT_ROLE_VIEWER_GROUPS",
    "AGENT_ROLE_BASE_USER_GROUPS",
)


def _clear_role_caches():
    roles.is_agent_platform_role_mapping_enabled.cache_clear()
    roles._allowed_sets.cache_clear()


@pytest.fixture(autouse=True)
def role_caches(monkeypatch):
    """Сбрасывает кэш платформенных ролей и env до и после теста."""
    for name in ROLE_ENV:
        monkeypatch.delenv(name, raising=False)
    _clear_role_caches()
    yield
    _clear_role_caches()


@pytest.fixture
def with_mapping(monkeypatch):
    """Платформенные роли: owner=Owners, editor=Editors, viewer=Viewers, base=Basic."""
    monkeypatch.setenv("AGENT_ROLE_OWNER_GROUPS", "Owners")
    monkeypatch.setenv("AGENT_ROLE_EDITOR_GROUPS", "Editors")
    monkeypatch.setenv("AGENT_ROLE_VIEWER_GROUPS", "Viewers")
    monkeypatch.setenv("AGENT_ROLE_BASE_USER_GROUPS", "Basic")
    _clear_role_caches()


def in_group(group, base):
    """Пользователь ``base`` с единственной группой ``group``."""
    return {**base, "groups": [group], "ldap_groups": []}


class ExplodingRepo:
    """Репозиторий, любой метод которого падает (имитация недоступной БД)."""

    message = "db down"

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)

        async def _boom(*args, **kwargs):
            raise RuntimeError(self.message)

        return _boom
