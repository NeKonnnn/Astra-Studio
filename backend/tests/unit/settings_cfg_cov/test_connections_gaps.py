"""settings/connections: ветки «на вход пришёл не dict» у валидаторов PostgreSQL/MinIO/LLM."""

import pytest

from backend.settings import connections as conn

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (
        "POSTGRES_HOST",
        "MINIO_ENDPOINT",
        "USE_LLM_SVC",
    ):
        monkeypatch.delenv(name, raising=False)


def test_postgres_non_dict_input_requires_env():
    with pytest.raises(ValueError, match="POSTGRES_HOST не задан"):
        conn.PostgreSQLConnectionConfig.load_from_yaml_or_env("junk")


def test_minio_non_dict_input_requires_env():
    with pytest.raises(ValueError, match="MINIO_ENDPOINT не задан"):
        conn.MinIOConnectionConfig.load_from_yaml_or_env(None)


def test_llm_non_dict_input_requires_env():
    with pytest.raises(ValueError, match="USE_LLM_SVC не задан"):
        conn.LLMServiceConnectionConfig.load_from_yaml_or_env(["x"])
