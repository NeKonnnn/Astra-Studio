"""POST /v1/embeddings: OpenAI-формат, legacy-поля, prompt_name, ошибки модели, валидация."""

import json
import logging
from types import SimpleNamespace

import pytest

from app.api.endpoints import embed
from app.api.endpoints.embed import EmbedRequest, _encode_texts, _prompt_name_for

pytestmark = pytest.mark.unit

URL = "/v1/embeddings"


def _leaf(path: str) -> str:
    """Имя папки модели: сервис рассчитан на POSIX-пути, на Windows разделитель другой."""
    return path.replace("\\", "/").rsplit("/", 1)[-1]


class TestEmbedRequestModel:
    def test_input_string(self):
        assert EmbedRequest(input="a").get_texts() == ["a"]

    def test_input_list_items_stringified(self):
        assert EmbedRequest(input=["a", "b"]).get_texts() == ["a", "b"]

    def test_input_has_priority_over_texts_and_text(self):
        r = EmbedRequest(input="i", texts=["t"], text="x")
        assert r.get_texts() == ["i"]

    def test_texts_before_text(self):
        assert EmbedRequest(texts=["t1", "t2"], text="x").get_texts() == ["t1", "t2"]

    def test_text_only(self):
        assert EmbedRequest(text="only").get_texts() == ["only"]

    def test_empty_string_is_still_a_text(self):
        assert EmbedRequest(input="").get_texts() == [""]

    @pytest.mark.parametrize("kw", [{}, {"input": []}, {"texts": []}, {"model": "FRIDA"}])
    def test_nothing_to_embed_is_rejected(self, kw):
        with pytest.raises(ValueError, match="Нужно передать input, text или texts"):
            EmbedRequest(**kw)


class TestPromptNameFor:
    @staticmethod
    def _model(prompts):
        return SimpleNamespace(prompts=prompts)

    @pytest.mark.parametrize(
        "kind,expected",
        [
            ("query", "search_query"), ("search_query", "search_query"), ("QUERY", "search_query"),
            ("  query  ", "search_query"),
            ("document", "search_document"), ("doc", "search_document"),
            ("passage", "search_document"), ("search_document", "search_document"),
        ],
    )
    def test_known_kinds_map_to_prompts(self, kind, expected):
        model = self._model({"search_query": "q: ", "search_document": "d: "})
        assert _prompt_name_for(model, kind) == expected

    @pytest.mark.parametrize("kind", [None, "", "weird", "text"])
    def test_unknown_or_empty_kind(self, kind):
        model = self._model({"search_query": "q: ", "search_document": "d: "})
        assert _prompt_name_for(model, kind) is None

    def test_model_without_prompts_gets_none(self):
        assert _prompt_name_for(self._model({}), "query") is None
        assert _prompt_name_for(SimpleNamespace(), "query") is None
        assert _prompt_name_for(self._model(None), "query") is None

    def test_prompt_missing_in_model_is_none(self):
        assert _prompt_name_for(self._model({"other": "x"}), "query") is None

    def test_unhashable_container_is_none(self):
        assert _prompt_name_for(self._model(5), "query") is None  # 'x in 5' -> TypeError

    def test_empty_configured_prompt_name_disables_prefix(self, rm_settings, monkeypatch):
        monkeypatch.setattr(rm_settings, "query_prompt_name", "")
        assert _prompt_name_for(self._model({"": "x"}), "query") is None


class TestEncodeTexts:
    def test_progress_bar_only_for_big_batches(self):
        calls = []

        class M:
            def encode(self, texts, **kw):
                calls.append(kw)
                return "ok"

        assert _encode_texts(M(), ["a", "b", "c"], 2, None) == "ok"
        _encode_texts(M(), ["a"], 2, "search_query")
        assert calls[0] == {"convert_to_numpy": True, "batch_size": 2, "show_progress_bar": True}
        assert calls[1] == {
            "convert_to_numpy": True, "batch_size": 2, "show_progress_bar": False,
            "prompt_name": "search_query",
        }


class TestEmbeddingsEndpoint:
    async def test_openai_response_shape(self, client, disk_models):
        r = await client.post(URL, json={"input": ["abc", "de"]})
        assert r.status_code == 200
        body = r.json()
        assert body["object"] == "list"
        assert _leaf(body["model"]) == "FRIDA"
        assert body["usage"] == {"prompt_tokens": 0, "total_tokens": 0}
        assert [d["index"] for d in body["data"]] == [0, 1]
        assert all(d["object"] == "embedding" and len(d["embedding"]) == 8 for d in body["data"])

    async def test_vectors_are_deterministic_and_text_dependent(self, client, disk_models):
        a = (await client.post(URL, json={"input": "abc"})).json()["data"][0]["embedding"]
        b = (await client.post(URL, json={"input": "abc"})).json()["data"][0]["embedding"]
        c = (await client.post(URL, json={"input": "abd"})).json()["data"][0]["embedding"]
        assert a == b and a != c
        # 'abc': сумма ord = 294; j=0 -> 294 % 17 / 17
        assert a[0] == pytest.approx((294 % 17) / 17.0, rel=1e-6)

    async def test_legacy_text_and_texts_fields(self, client, disk_models):
        one = await client.post(URL, json={"text": "solo"})
        many = await client.post(URL, json={"texts": ["x", "y", "z"]})
        assert len(one.json()["data"]) == 1
        assert len(many.json()["data"]) == 3

    async def test_default_model_when_not_named(self, client, disk_models):
        await client.post(URL, json={"input": "x"})
        assert disk_models.st_calls[0]["path"].endswith("FRIDA")

    async def test_named_model_selected(self, client, disk_models):
        r = await client.post(URL, json={"input": "x", "model": "local/Giga-Embeddings-instruct"})
        assert r.status_code == 200
        assert _leaf(r.json()["model"]) == "Giga-Embeddings-instruct"

    @pytest.mark.parametrize(
        "kind,prompt", [("query", "search_query"), ("document", "search_document"), ("passage", "search_document")]
    )
    async def test_asymmetric_model_gets_prompt_name(self, client, disk_models, kind, prompt):
        r = await client.post(URL, json={"input": "x", "kind": kind})
        assert r.status_code == 200
        assert disk_models.models[0].encode_calls[0]["prompt_name"] == prompt
        # фейк сдвигает вектор на 1.0, если промпт применён
        assert r.json()["data"][0]["embedding"][0] >= 1.0

    async def test_symmetric_model_ignores_kind(self, client, disk_models):
        r = await client.post(URL, json={"input": "x", "kind": "query", "model": "Giga-Embeddings-instruct"})
        assert r.status_code == 200
        giga = [m for m in disk_models.models if "Giga" in m.path][0]
        assert "prompt_name" not in giga.encode_calls[0]

    async def test_no_kind_means_no_prompt(self, client, disk_models):
        await client.post(URL, json={"input": "x"})
        assert "prompt_name" not in disk_models.models[0].encode_calls[0]

    async def test_batch_size_from_settings(self, client, disk_models, rm_settings, monkeypatch):
        monkeypatch.setattr(rm_settings, "embed_batch_size", 2)
        await client.post(URL, json={"input": ["a", "b", "c"]})
        call = disk_models.models[0].encode_calls[0]
        assert call["batch_size"] == 2 and call["show_progress_bar"] is True
        assert call["convert_to_numpy"] is True

    async def test_non_positive_batch_size_clamped_to_one(self, client, disk_models, rm_settings, monkeypatch):
        monkeypatch.setattr(rm_settings, "embed_batch_size", 0)
        await client.post(URL, json={"input": ["a"]})
        assert disk_models.models[0].encode_calls[0]["batch_size"] == 1

    async def test_single_vector_output_is_wrapped(self, client, disk_models):
        disk_models.encode_1d = True
        r = await client.post(URL, json={"input": "x"})
        assert r.status_code == 200
        assert len(r.json()["data"]) == 1 and len(r.json()["data"][0]["embedding"]) == 8

    async def test_actual_vector_length_updates_entry_dim(self, client, disk_models):
        from app.dependencies import rag_models_handler as h

        disk_models.dim = 5
        await client.post(URL, json={"input": "x"})
        assert h._MODEL_CACHE[("embedding", "FRIDA")]["dim"] == 5

    async def test_empty_vectors_leave_dim_unchanged(self, client, disk_models):
        from app.dependencies import rag_models_handler as h

        disk_models.dim = 0
        r = await client.post(URL, json={"input": ["a", "b"]})
        assert r.status_code == 200
        assert r.json()["data"][0]["embedding"] == []
        assert h._MODEL_CACHE[("embedding", "FRIDA")]["dim"] == 0

    async def test_service_disabled_gives_503(self, client, disk_models, rm_settings, monkeypatch):
        monkeypatch.setattr(rm_settings, "enabled", False)
        r = await client.post(URL, json={"input": "x"})
        assert r.status_code == 503
        assert r.json()["detail"] == "Сервис RAG-моделей выключен"

    async def test_unknown_model_gives_400(self, client, disk_models):
        r = await client.post(URL, json={"input": "x", "model": "NotConfigured"})
        assert r.status_code == 400
        assert "не разрешена" in r.json()["detail"]
        assert disk_models.st_calls == []

    @pytest.mark.parametrize("name", ["../../etc/passwd", "..", "/etc/passwd", "local/../../secret"])
    async def test_path_traversal_in_model_name_never_loads_outside_models_dir(self, client, disk_models, name):
        r = await client.post(URL, json={"input": "x", "model": name})
        assert r.status_code == 400
        assert disk_models.st_calls == []

    async def test_model_load_failure_gives_503(self, client, disk_models):
        disk_models.st_error = RuntimeError("битые веса")
        r = await client.post(URL, json={"input": "x"})
        assert r.status_code == 503
        assert r.json()["detail"] == "Эмбеддинг-модель не загружена"

    async def test_missing_weights_gives_503(self, client, fake_ml):
        r = await client.post(URL, json={"input": "x"})
        assert r.status_code == 503
        assert r.json()["detail"] == "Эмбеддинг-модель не загружена"

    async def test_load_error_detail_does_not_expose_server_path(self, client, fake_ml, models_dir):
        """503 не отдаёт клиенту абсолютный путь каталога моделей на сервере."""
        r = await client.post(URL, json={"input": "x"})
        assert r.status_code == 503
        assert str(models_dir) not in r.json()["detail"]

    async def test_encode_failure_is_500_not_a_hang(self, client, disk_models, monkeypatch):
        def boom(model, texts, batch_size, prompt_name):
            raise RuntimeError("OOM")

        monkeypatch.setattr(embed, "_encode_texts", boom)
        r = await client.post(URL, json={"input": "x"})
        assert r.status_code == 500

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"input": []},
            {"model": "FRIDA"},
            {"input": 5},
            {"input": [["nested"]]},
            {"input": {"a": 1}},
            {"texts": "not-a-list"},
            {"texts": [1, 2]},
            {"text": ["x"]},
            {"input": "x", "model": ["FRIDA"]},
        ],
    )
    async def test_invalid_payload_gives_422(self, client, disk_models, payload):
        r = await client.post(URL, json=payload)
        assert r.status_code == 422
        assert disk_models.st_calls == []

    async def test_malformed_json_gives_422(self, client, disk_models):
        r = await client.post(URL, content=b"{not json", headers={"content-type": "application/json"})
        assert r.status_code == 422

    async def test_wrong_method_gives_405(self, client, disk_models):
        assert (await client.get(URL)).status_code == 405

    async def test_unicode_text_roundtrip(self, client, disk_models):
        r = await client.post(URL, json={"input": "Привет, мир ✓"})
        assert r.status_code == 200
        assert len(r.json()["data"]) == 1


class TestTextsNeverReachLogs:
    """Содержимое текстов не должно оказываться ни в app-логе, ни в CEF-аудите, ни в stdout."""

    SECRET = "СЕКРЕТНЫЙ-ТЕКСТ-ПАСПОРТ-4510-123456"

    async def test_embedding_text_not_logged(self, disk_models, caplog, capsys, cef_lines):
        import httpx

        from app import main

        caplog.set_level(logging.DEBUG)
        transport = httpx.ASGITransport(app=main.app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://svc") as c:
            r = await c.post(URL, json={"input": [self.SECRET, "второй чанк"], "kind": "document"})
        assert r.status_code == 200
        out = capsys.readouterr()
        everything = caplog.text + out.out + out.err + "\n".join(cef_lines)
        assert self.SECRET not in everything
        assert "СЕКРЕТНЫЙ" not in everything
        # а метаданные запроса в логе есть (форма без содержимого)
        assert "input=<2 шт." in caplog.text

    async def test_validation_error_does_not_log_text(self, disk_models, caplog, capsys, cef_lines):
        import httpx

        from app import main

        caplog.set_level(logging.DEBUG)
        transport = httpx.ASGITransport(app=main.app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://svc") as c:
            r = await c.post(URL, content=json.dumps({"input": self.SECRET, "texts": 5}).encode(),
                             headers={"content-type": "application/json"})
        assert r.status_code == 422
        out = capsys.readouterr()
        assert self.SECRET not in caplog.text + out.out + out.err + "\n".join(cef_lines)
