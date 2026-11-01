"""mcp/credentials: шифрование и изоляция хранилища учётных данных, провайдеры Atlassian/Null."""

import json
import logging
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from backend.mcp.credentials import atlassian as atl
from backend.mcp.credentials import base as cbase
from backend.mcp.credentials import store
from backend.mcp.types import McpCallContext

pytestmark = pytest.mark.unit

KEY = Fernet.generate_key().decode()
CTX = McpCallContext(user_id="u1", username="ivan")


class FakeCollection:
    """Минимальная in-memory коллекция Mongo с ключом (user_id, server_id)."""

    def __init__(self):
        self.docs = {}
        self.indexes = []
        self.fail_index = False

    async def create_index(self, keys, **kw):
        if self.fail_index:
            raise RuntimeError("index boom")
        self.indexes.append((keys, kw))

    async def find_one(self, flt):
        return self.docs.get((flt["user_id"], flt["server_id"]))

    async def update_one(self, flt, update, upsert=False):
        k = (flt["user_id"], flt["server_id"])
        doc = self.docs.get(k)
        if doc is None:
            doc = dict(flt)
            doc.update(update.get("$setOnInsert", {}))
        doc.update(update["$set"])
        self.docs[k] = doc

    async def delete_one(self, flt):
        k = (flt["user_id"], flt["server_id"])
        return SimpleNamespace(deleted_count=1 if self.docs.pop(k, None) else 0)


@pytest.fixture
def col(monkeypatch):
    c = FakeCollection()
    monkeypatch.setenv("MCP_CREDENTIALS_ENCRYPTION_KEY", KEY)
    monkeypatch.setattr(store, "_get_collection", lambda: c)
    return c


class TestFernet:
    def test_no_key_gives_none(self, monkeypatch):
        monkeypatch.delenv("MCP_CREDENTIALS_ENCRYPTION_KEY", raising=False)
        assert store._get_fernet() is None

    def test_blank_key_gives_none(self, monkeypatch):
        monkeypatch.setenv("MCP_CREDENTIALS_ENCRYPTION_KEY", "   ")
        assert store._get_fernet() is None

    def test_invalid_key_gives_none_and_does_not_leak_the_key(self, monkeypatch, caplog):
        monkeypatch.setenv("MCP_CREDENTIALS_ENCRYPTION_KEY", "not-a-fernet-key-SECRET")
        with caplog.at_level(logging.ERROR):
            assert store._get_fernet() is None
        assert "not-a-fernet-key-SECRET" not in caplog.text

    def test_valid_key(self, monkeypatch):
        monkeypatch.setenv("MCP_CREDENTIALS_ENCRYPTION_KEY", KEY)
        assert isinstance(store._get_fernet(), Fernet)


class TestCollection:
    def test_returns_named_collection(self, monkeypatch):
        import backend.database.init_db as init_db

        seen = []
        conn = SimpleNamespace(get_collection=lambda n: seen.append(n) or "COL")
        monkeypatch.setattr(init_db, "get_mongodb_connection", lambda: conn)
        assert store._get_collection() == "COL" and seen == [store.COLLECTION_NAME]

    def test_no_connection(self, monkeypatch):
        import backend.database.init_db as init_db

        monkeypatch.setattr(init_db, "get_mongodb_connection", lambda: None)
        assert store._get_collection() is None

    def test_connection_error_is_swallowed(self, monkeypatch):
        import backend.database.init_db as init_db

        def boom():
            raise RuntimeError("mongo down")

        monkeypatch.setattr(init_db, "get_mongodb_connection", boom)
        assert store._get_collection() is None


class TestIndexes:
    async def test_without_collection_is_noop(self, monkeypatch):
        monkeypatch.setattr(store, "_get_collection", lambda: None)
        await store.ensure_indexes()

    async def test_creates_unique_index(self, col):
        await store.ensure_indexes()
        keys, kw = col.indexes[0]
        assert keys == [("user_id", 1), ("server_id", 1)] and kw["unique"] is True

    async def test_failure_is_swallowed(self, col):
        col.fail_index = True
        await store.ensure_indexes()
        assert col.indexes == []


class TestEncryption:
    def test_roundtrip_and_ciphertext_hides_secret(self, monkeypatch):
        monkeypatch.setenv("MCP_CREDENTIALS_ENCRYPTION_KEY", KEY)
        blob = store._encrypt_payload({"jira_pat": "SECRET-PAT", "имя": "значение"})
        assert b"SECRET-PAT" not in blob
        assert store._decrypt_payload(blob) == {"jira_pat": "SECRET-PAT", "имя": "значение"}

    def test_encrypt_without_key(self, monkeypatch):
        monkeypatch.delenv("MCP_CREDENTIALS_ENCRYPTION_KEY", raising=False)
        with pytest.raises(RuntimeError, match="not configured"):
            store._encrypt_payload({"a": 1})

    def test_decrypt_without_key(self, monkeypatch):
        monkeypatch.delenv("MCP_CREDENTIALS_ENCRYPTION_KEY", raising=False)
        with pytest.raises(RuntimeError, match="not configured"):
            store._decrypt_payload(b"x")

    def test_decrypt_with_wrong_key_raises(self, monkeypatch):
        monkeypatch.setenv("MCP_CREDENTIALS_ENCRYPTION_KEY", KEY)
        blob = store._encrypt_payload({"a": 1})
        monkeypatch.setenv("MCP_CREDENTIALS_ENCRYPTION_KEY", Fernet.generate_key().decode())
        with pytest.raises(RuntimeError, match="Failed to decrypt"):
            store._decrypt_payload(blob)

    def test_decrypt_non_json_raises(self, monkeypatch):
        monkeypatch.setenv("MCP_CREDENTIALS_ENCRYPTION_KEY", KEY)
        blob = Fernet(KEY.encode()).encrypt(b"not json")
        with pytest.raises(RuntimeError, match="Failed to decrypt"):
            store._decrypt_payload(blob)

    def test_decrypt_non_dict_json_gives_empty(self, monkeypatch):
        monkeypatch.setenv("MCP_CREDENTIALS_ENCRYPTION_KEY", KEY)
        blob = Fernet(KEY.encode()).encrypt(json.dumps([1, 2]).encode())
        assert store._decrypt_payload(blob) == {}


class TestCrud:
    async def test_all_operations_without_storage(self, monkeypatch):
        monkeypatch.setattr(store, "_get_collection", lambda: None)
        assert await store.get_credentials("u", "s") is None
        assert await store.save_credentials("u", "s", {"a": 1}) is False
        assert await store.delete_credentials("u", "s") is False

    async def test_save_stores_only_ciphertext(self, col):
        assert await store.save_credentials("u1", "atlassian", {"jira_pat": "SECRET-PAT"}) is True
        doc = col.docs[("u1", "atlassian")]
        assert "SECRET-PAT" not in repr(doc) and doc["encrypted_payload"]
        assert doc["created_at"] and doc["updated_at"]

    async def test_save_without_key_raises_and_writes_nothing(self, col, monkeypatch):
        monkeypatch.delenv("MCP_CREDENTIALS_ENCRYPTION_KEY")
        with pytest.raises(RuntimeError):
            await store.save_credentials("u1", "s", {"a": 1})
        assert col.docs == {}

    async def test_resave_keeps_created_at(self, col):
        await store.save_credentials("u1", "s", {"a": 1})
        created = col.docs[("u1", "s")]["created_at"]
        await store.save_credentials("u1", "s", {"a": 2})
        assert col.docs[("u1", "s")]["created_at"] == created
        assert await store.get_credentials("u1", "s") == {"a": 2}

    async def test_users_are_isolated(self, col):
        await store.save_credentials("alice", "atlassian", {"jira_pat": "A"})
        await store.save_credentials("bob", "atlassian", {"jira_pat": "B"})
        assert (await store.get_credentials("alice", "atlassian"))["jira_pat"] == "A"
        assert (await store.get_credentials("bob", "atlassian"))["jira_pat"] == "B"
        assert await store.get_credentials("carol", "atlassian") is None
        assert await store.get_credentials("alice", "other") is None

    async def test_missing_or_empty_payload(self, col):
        assert await store.get_credentials("u", "s") is None
        col.docs[("u", "s")] = {"user_id": "u", "server_id": "s", "encrypted_payload": b""}
        assert await store.get_credentials("u", "s") is None

    async def test_delete(self, col):
        await store.save_credentials("u1", "s", {"a": 1})
        assert await store.delete_credentials("u1", "s") is True
        assert await store.delete_credentials("u1", "s") is False


class TestMetadata:
    async def test_never_returns_secret_values(self, col):
        await store.save_credentials("u1", "s", {"jira_pat": "SECRET-PAT", "jira_url": "https://j", "empty": ""})
        meta = await store.credentials_metadata("u1", "s")
        assert meta == {"configured": True, "fields": {"jira_pat": True, "jira_url": True}, "storage_available": True}
        assert "SECRET-PAT" not in json.dumps(meta)

    async def test_nothing_stored(self, col):
        assert await store.credentials_metadata("u1", "s") == {
            "configured": False, "fields": {}, "storage_available": True,
        }

    async def test_nothing_stored_and_no_key(self, monkeypatch):
        monkeypatch.delenv("MCP_CREDENTIALS_ENCRYPTION_KEY", raising=False)
        monkeypatch.setattr(store, "_get_collection", lambda: None)
        assert (await store.credentials_metadata("u", "s"))["storage_available"] is False

    async def test_undecryptable_means_storage_unavailable(self, col, monkeypatch):
        await store.save_credentials("u1", "s", {"a": 1})
        monkeypatch.setenv("MCP_CREDENTIALS_ENCRYPTION_KEY", Fernet.generate_key().decode())
        assert await store.credentials_metadata("u1", "s") == {
            "configured": False, "fields": {}, "storage_available": False,
        }

    async def test_only_empty_values_is_not_configured(self, col):
        await store.save_credentials("u1", "s", {"jira_pat": ""})
        assert (await store.credentials_metadata("u1", "s"))["configured"] is False


class TestProviderRegistry:
    def test_no_id_gives_null(self):
        assert isinstance(cbase.get_credential_provider(None), cbase.NullCredentialProvider)
        assert isinstance(cbase.get_credential_provider(""), cbase.NullCredentialProvider)

    def test_atlassian_case_and_spaces_insensitive(self):
        assert isinstance(cbase.get_credential_provider(" Atlassian "), atl.AtlassianCredentialProvider)

    def test_unknown_gives_null_with_warning(self, caplog):
        with caplog.at_level(logging.WARNING):
            p = cbase.get_credential_provider("jenkins")
        assert isinstance(p, cbase.NullCredentialProvider)
        assert "Unknown MCP credential_provider" in caplog.text

    async def test_null_provider(self):
        p = cbase.NullCredentialProvider()
        assert await p.build_headers(SimpleNamespace(), CTX) == {}
        assert await p.health_metadata(SimpleNamespace(), CTX) == {}


class TestAtlassianValidation:
    def test_not_a_dict(self):
        assert atl.validate_atlassian_payload([]) == ["payload must be an object"]

    def test_requires_a_complete_pair(self):
        assert len(atl.validate_atlassian_payload({"jira_url": "u"})) == 1
        assert len(atl.validate_atlassian_payload({})) == 1

    def test_jira_or_confluence_pair_is_enough(self):
        assert atl.validate_atlassian_payload({"jira_url": "u", "jira_pat": "p"}) == []
        assert atl.validate_atlassian_payload({"confluence_url": "u", "confluence_pat": "p"}) == []


class TestAtlassianProvider:
    def server(self, mode="per_user"):
        return SimpleNamespace(id="atlassian", auth_mode=mode)

    async def test_service_account_sends_no_secrets(self, monkeypatch):
        async def boom(*a):
            raise AssertionError("хранилище не должно читаться")

        monkeypatch.setattr(atl, "get_credentials", boom)
        p = atl.AtlassianCredentialProvider()
        assert await p.build_headers(self.server(None), CTX) == {}
        assert await p.build_headers(self.server("SERVICE_ACCOUNT"), CTX) == {}

    async def test_per_user_without_credentials(self, monkeypatch):
        async def none(*a):
            return None

        monkeypatch.setattr(atl, "get_credentials", none)
        assert await atl.AtlassianCredentialProvider().build_headers(self.server(), CTX) == {}

    async def test_per_user_uses_current_user_id(self, monkeypatch):
        seen = []

        async def fake(user_id, server_id):
            seen.append((user_id, server_id))
            return {"jira_url": "https://j", "jira_pat": "JP", "confluence_url": "https://c", "confluence_pat": "CP"}

        monkeypatch.setattr(atl, "get_credentials", fake)
        h = await atl.AtlassianCredentialProvider().build_headers(self.server(), CTX)
        assert seen == [("u1", "atlassian")]
        assert h == {
            "X-Atlassian-Jira-Url": "https://j",
            "X-Atlassian-Jira-Personal-Token": "JP",
            "X-Atlassian-Confluence-Url": "https://c",
            "X-Atlassian-Confluence-Personal-Token": "CP",
            "Authorization": "Token JP",
        }

    async def test_confluence_only_authorization(self, monkeypatch):
        async def fake(*a):
            return {"confluence_pat": "CP"}

        monkeypatch.setattr(atl, "get_credentials", fake)
        h = await atl.AtlassianCredentialProvider().build_headers(self.server(), CTX)
        assert h == {"X-Atlassian-Confluence-Personal-Token": "CP", "Authorization": "Token CP"}

    async def test_health_metadata_service_account(self):
        meta = await atl.AtlassianCredentialProvider().health_metadata(self.server(None), CTX)
        assert meta == {"auth_mode": "service_account", "jira_configured": True, "confluence_configured": True}

    async def test_health_metadata_per_user(self, monkeypatch):
        async def fake(*a):
            return {"jira_url": "u", "jira_pat": "p", "confluence_url": "u"}

        monkeypatch.setattr(atl, "get_credentials", fake)
        meta = await atl.AtlassianCredentialProvider().health_metadata(self.server(), CTX)
        assert meta == {"auth_mode": "per_user", "jira_configured": True, "confluence_configured": False}

    async def test_health_metadata_per_user_without_credentials(self, monkeypatch):
        async def none(*a):
            return None

        monkeypatch.setattr(atl, "get_credentials", none)
        meta = await atl.AtlassianCredentialProvider().health_metadata(self.server(), CTX)
        assert meta["jira_configured"] is False and meta["confluence_configured"] is False

    def test_sanitize_masks_values(self):
        p = atl.AtlassianCredentialProvider()
        assert p.sanitize_payload_for_response(None) == {}
        out = p.sanitize_payload_for_response({"jira_pat": "SECRET", "jira_url": "", "extra": "x"})
        assert out == {"jira_url": False, "jira_pat": True, "confluence_url": False, "confluence_pat": False}
        assert "SECRET" not in json.dumps(out)
