"""GET /v1/models, /v1/models/current, POST /v1/models/select, GET /v1/health."""

import os

import pytest

from app.api.endpoints import models as models_endpoint
from app.dependencies import rag_models_handler as h

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- health
class TestHealth:
    async def test_disabled(self, client, rm_settings, monkeypatch):
        monkeypatch.setattr(rm_settings, "enabled", False)
        r = await client.get("/v1/health")
        assert r.status_code == 200
        assert r.json() == {
            "status": "disabled", "service": "rag-models",
            "embedding_loaded": False, "reranker_loaded": False,
        }

    async def test_unhealthy_when_models_fail(self, client, fake_ml, models_dir):
        r = await client.get("/v1/health")
        assert r.status_code == 503
        body = r.json()
        assert body["status"] == "unhealthy"
        assert body["embedding_loaded"] is False and body["reranker_loaded"] is False
        assert body["models_dir"] == str(models_dir)

    async def test_healthy_reports_dim_and_device(self, client, disk_models, models_dir):
        r = await client.get("/v1/health")
        assert r.status_code == 200
        assert r.json() == {
            "status": "healthy", "service": "rag-models",
            "embedding_loaded": True, "reranker_loaded": True,
            "embedding_dim": 8, "device": "cpu", "models_dir": str(models_dir),
        }

    async def test_health_is_not_audited_and_needs_no_key(self, client, disk_models, monkeypatch, cef_lines):
        monkeypatch.setenv("RAG_MODELS_API_KEY", "secret")
        r = await client.get("/v1/health")
        assert r.status_code == 200
        assert cef_lines == []


# --------------------------------------------------------------------------- GET /models
class TestListModels:
    async def test_catalog_structure(self, client, disk_models, models_dir):
        r = await client.get("/v1/models")
        assert r.status_code == 200
        body = r.json()
        assert body["object"] == "list"
        assert body["offline"] is True
        assert body["models_dir"] == str(models_dir)
        ids = [m["id"] for m in body["data"]]
        assert ids == ["FRIDA", "Giga-Embeddings-instruct", "ms-marco-MiniLM-L6-v2"]
        assert {m["kind"] for m in body["data"]} == {"embedding", "reranker"}
        assert set(body["models"]) == {"embedding", "reranker"}
        assert body["current"]["embedding"]["name"] == "FRIDA"
        assert body["cluster_default"]["reranker"]["name"] == "ms-marco-MiniLM-L6-v2"

    async def test_unavailable_models_not_in_data_but_in_catalog(self, client, fake_ml):
        body = (await client.get("/v1/models")).json()
        assert body["data"] == []
        assert any(not m["available"] for m in body["models"]["embedding"])

    @pytest.mark.parametrize("value", ["embedding", "RERANKER", " reranker "])
    async def test_type_filter(self, client, disk_models, value):
        body = (await client.get("/v1/models", params={"type": value})).json()
        assert list(body["models"]) == [value.strip().lower()]
        assert {m["kind"] for m in body["data"]} == {value.strip().lower()}

    async def test_bad_type_gives_400(self, client, disk_models):
        r = await client.get("/v1/models", params={"type": "llm"})
        assert r.status_code == 400
        assert "embedding или reranker" in r.json()["detail"]


# --------------------------------------------------------------------------- /models/current
class TestCurrentModels:
    async def test_loaded_with_dim(self, client, disk_models):
        r = await client.get("/v1/models/current")
        body = r.json()
        assert r.status_code == 200
        assert body["loaded"] is True
        assert body["embedding_dim"] == 8
        assert body["offline"] is True
        assert body["current"]["embedding"]["available"] is True

    async def test_not_loaded_when_weights_missing(self, client, fake_ml):
        body = (await client.get("/v1/models/current")).json()
        assert body["loaded"] is False and body["embedding_dim"] is None

    async def test_disabled_reports_not_loaded(self, client, rm_settings, monkeypatch):
        monkeypatch.setattr(rm_settings, "enabled", False)
        assert (await client.get("/v1/models/current")).json()["loaded"] is False


# --------------------------------------------------------------------------- /models/select
SEL = "/v1/models/select"


class TestSelectModel:
    async def test_select_embedding_reloads_and_updates_settings(self, client, disk_models, make_model, rm_settings):
        make_model("Other-Embedding", config={})
        await client.get("/v1/health")  # прогреть кэш
        assert disk_models.st_calls
        before = len(disk_models.st_calls)
        r = await client.post(SEL, json={"model_type": "embedding", "model_path": "local/Other-Embedding"})
        assert r.status_code == 200
        body = r.json()
        assert body["success"] is True and body["message"] == "Модель загружена"
        assert body["model_type"] == "embedding" and body["model_path"] == "local/Other-Embedding"
        assert body["embedding_dim"] == 8
        assert body["current"]["embedding"]["name"] == "Other-Embedding"
        assert rm_settings.embedding_model == "Other-Embedding"
        # кэш был выгружен и загружен заново (новый дефолт)
        assert len(disk_models.st_calls) > before
        assert ("embedding", "Other-Embedding") in h._MODEL_CACHE
        assert ("embedding", "FRIDA") not in h._MODEL_CACHE

    async def test_select_reranker(self, client, disk_models, make_model, rm_settings):
        make_model("tiny-rerank", config={"architectures": ["Bert"]})
        r = await client.post(SEL, json={"model_type": "reranker", "model_path": "tiny-rerank"})
        assert r.status_code == 200
        assert rm_settings.reranker_model == "tiny-rerank"
        assert r.json()["current"]["reranker"]["name"] == "tiny-rerank"

    async def test_service_disabled_gives_503(self, client, disk_models, rm_settings, monkeypatch):
        monkeypatch.setattr(rm_settings, "enabled", False)
        r = await client.post(SEL, json={"model_type": "embedding", "model_path": "local/FRIDA"})
        assert r.status_code == 503

    @pytest.mark.parametrize(
        "path,fragment",
        [
            ("", "model_path пуст"),
            ("huggingface/org/model", "Внешний каталог"),
            ("phoenix/some", "Phoenix"),
        ],
    )
    async def test_rejected_paths_give_400(self, client, disk_models, path, fragment):
        r = await client.post(SEL, json={"model_type": "embedding", "model_path": path})
        assert r.status_code == 400
        assert fragment in r.json()["detail"]

    async def test_non_local_source_guard(self, client, disk_models, monkeypatch):
        """Защитная ветка: источник, отличный от local, после parse_model_path.

        Реальными входами недостижима (phoenix/ отсекается раньше в config_value_for_path),
        поэтому источник подменяется - проверяем, что защита сработала бы.
        """
        monkeypatch.setattr(models_endpoint, "parse_model_path", lambda p: ("s3", "x"))
        r = await client.post(SEL, json={"model_type": "embedding", "model_path": "s3/x"})
        assert r.status_code == 400
        assert "только локальные модели" in r.json()["detail"]

    async def test_missing_folder_gives_400_and_keeps_cache(self, client, disk_models, models_dir):
        await client.get("/v1/health")
        r = await client.post(SEL, json={"model_type": "embedding", "model_path": "local/Ghost"})
        assert r.status_code == 400
        assert "Папка модели не найдена" in r.json()["detail"]
        assert ("embedding", "FRIDA") in h._MODEL_CACHE  # выгрузки не было

    async def test_load_failure_gives_502_with_reason(self, client, disk_models, make_model):
        make_model("Broken-Embedding", config={})
        disk_models.st_error = RuntimeError("битые веса")
        r = await client.post(SEL, json={"model_type": "embedding", "model_path": "local/Broken-Embedding"})
        assert r.status_code == 502
        assert "битые веса" in r.json()["detail"]

    async def test_load_failure_without_message_uses_default_text(self, client, disk_models, make_model, monkeypatch):
        make_model("Other-Embedding", config={})

        async def none_handler():
            return None

        monkeypatch.setattr(models_endpoint, "get_rag_models_handler", none_handler)
        monkeypatch.setattr(models_endpoint, "get_last_rag_models_error", lambda: None)
        r = await client.post(SEL, json={"model_type": "embedding", "model_path": "Other-Embedding"})
        assert r.status_code == 502
        assert r.json()["detail"] == "Не удалось загрузить модель"

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"model_type": "embedding"},
            {"model_path": "local/FRIDA"},
            {"model_type": "llm", "model_path": "local/FRIDA"},
            {"model_type": "embedding", "model_path": 5},
        ],
    )
    async def test_invalid_payload_gives_422(self, client, disk_models, payload):
        assert (await client.post(SEL, json=payload)).status_code == 422

    async def test_traversal_through_nested_path_keeps_only_last_component(self, client, disk_models, models_dir):
        r = await client.post(SEL, json={"model_type": "embedding", "model_path": "local/../../etc"})
        assert r.status_code == 400  # 'etc' в models_dir нет

    @pytest.mark.parametrize("path", ["local/..", "local/."])
    async def test_dot_segments_must_not_escape_models_dir(self, client, disk_models, rm_settings, path):
        """BUG-RM-02: '.'/'..' не должны становиться активной моделью (выход за models_dir)."""
        r = await client.post(SEL, json={"model_type": "embedding", "model_path": path})
        assert r.status_code == 400
        assert rm_settings.embedding_model == "FRIDA"
        assert disk_models.st_calls == []

    async def test_failed_select_keeps_previous_model_configured(self, client, disk_models, make_model, rm_settings):
        make_model("Broken-Embedding", config={})
        await client.get("/v1/health")
        disk_models.st_error = RuntimeError("битые веса")
        r = await client.post(SEL, json={"model_type": "embedding", "model_path": "local/Broken-Embedding"})
        assert r.status_code == 502
        assert rm_settings.embedding_model == "FRIDA"

    async def test_failed_select_reports_original_error_and_restores_previous_model(
        self, client, disk_models, make_model, rm_settings
    ):
        """BUG-RM-03: откат конфигурации; прежняя модель снова доступна, ошибка - исходная."""
        make_model("Broken-Embedding", config={})
        await client.get("/v1/health")
        disk_models.st_error = RuntimeError("битые веса")
        r = await client.post(SEL, json={"model_type": "embedding", "model_path": "local/Broken-Embedding"})
        assert r.status_code == 502 and "битые веса" in r.json()["detail"]
        assert rm_settings.embedding_model == "FRIDA"
        disk_models.st_error = None
        assert (await client.get("/v1/health")).status_code == 200

    async def test_failed_reranker_select_restores_previous_reranker(self, client, disk_models, make_model, rm_settings):
        make_model("Broken-Reranker", config={})
        await client.get("/v1/health")
        disk_models.ce_error = RuntimeError("битый реранкер")
        r = await client.post(SEL, json={"model_type": "reranker", "model_path": "local/Broken-Reranker"})
        assert r.status_code == 502
        assert rm_settings.reranker_model == "ms-marco-MiniLM-L6-v2"
