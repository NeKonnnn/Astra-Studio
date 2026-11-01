"""POST /v1/rerank: native (passages/top_k) и Cohere/OpenAI (documents/top_n) форматы."""

import numpy as np
import pytest

from app.api.endpoints.rerank import RerankRequest, _predict
from app.dependencies import rag_models_handler as h

pytestmark = pytest.mark.unit

URL = "/v1/rerank"


def _leaf(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1]


class TestRerankRequestModel:
    def test_passages_have_priority_over_documents(self):
        r = RerankRequest(query="q", passages=["p"], documents=["d"])
        assert r.passages == ["p"]

    def test_documents_used_when_no_passages(self):
        assert RerankRequest(query="q", documents=["d1", "d2"]).passages == ["d1", "d2"]

    def test_empty_passages_not_replaced_by_documents(self):
        assert RerankRequest(query="q", passages=[], documents=["d"]).passages == []

    def test_no_documents_gives_empty_list(self):
        assert RerankRequest(query="q").passages == []

    def test_top_k_defaults_to_20_and_prefers_top_k_over_top_n(self):
        assert RerankRequest(query="q").top_k == 20
        assert RerankRequest(query="q", top_n=3).top_k == 3
        assert RerankRequest(query="q", top_k=2, top_n=3).top_k == 2

    def test_predict_helper_delegates(self):
        class M:
            def predict(self, pairs):
                return ["called", pairs]

        assert _predict(M(), [["a", "b"]]) == ["called", [["a", "b"]]]


class TestRerankEndpoint:
    async def test_ranks_by_relevance_native_format(self, client, disk_models):
        passages = ["совсем другое", "кошка спит дома", "кошка"]
        r = await client.post(URL, json={"query": "кошка спит", "passages": passages})
        assert r.status_code == 200
        body = r.json()
        # пересечения слов с запросом: 0, 2, 1
        assert body["indices"] == [1, 2, 0]
        assert body["scores"] == [2.0, 1.0, 0.0]
        assert body["results"] == [
            {"index": 1, "relevance_score": 2.0},
            {"index": 2, "relevance_score": 1.0},
            {"index": 0, "relevance_score": 0.0},
        ]
        assert _leaf(body["model"]) == "ms-marco-MiniLM-L6-v2"

    async def test_cohere_style_documents_and_top_n(self, client, disk_models):
        r = await client.post(
            URL, json={"query": "a b", "documents": ["x", "a", "a b", "b"], "top_n": 2}
        )
        assert r.status_code == 200
        assert r.json()["indices"] == [2, 1]
        assert r.json()["scores"] == [2.0, 1.0]

    async def test_top_k_larger_than_passages_returns_all(self, client, disk_models):
        r = await client.post(URL, json={"query": "a", "passages": ["a", "b"], "top_k": 50})
        assert len(r.json()["indices"]) == 2

    async def test_default_top_k_is_20(self, client, disk_models):
        r = await client.post(URL, json={"query": "w", "passages": [f"w{i}" for i in range(30)]})
        assert len(r.json()["indices"]) == 20

    async def test_pairs_are_query_passage(self, client, disk_models, monkeypatch):
        seen = []
        from app.api.endpoints import rerank

        orig = rerank._predict
        monkeypatch.setattr(rerank, "_predict", lambda m, pairs: (seen.append(pairs), orig(m, pairs))[1])
        await client.post(URL, json={"query": "Q", "passages": ["a", "b"]})
        assert seen == [[["Q", "a"], ["Q", "b"]]]

    async def test_empty_passages_return_empty_without_loading_model(self, client, disk_models):
        r = await client.post(URL, json={"query": "q", "passages": []})
        assert r.status_code == 200
        assert r.json() == {"indices": [], "scores": [], "model": None, "results": []}
        assert disk_models.ce_calls == []

    async def test_missing_passages_return_empty(self, client, disk_models):
        r = await client.post(URL, json={"query": "q"})
        assert r.status_code == 200 and r.json()["indices"] == []

    async def test_named_reranker_selected(self, client, disk_models):
        r = await client.post(URL, json={"query": "q", "passages": ["q"], "model": "local/ms-marco-MiniLM-L6-v2"})
        assert r.status_code == 200

    async def test_unknown_model_gives_400(self, client, disk_models):
        r = await client.post(URL, json={"query": "q", "passages": ["a"], "model": "NoSuch"})
        assert r.status_code == 400
        assert "не разрешена" in r.json()["detail"]

    @pytest.mark.parametrize("name", ["../../etc/passwd", "..", "/etc/passwd"])
    async def test_path_traversal_in_model_name_rejected(self, client, disk_models, name):
        r = await client.post(URL, json={"query": "q", "passages": ["a"], "model": name})
        assert r.status_code == 400
        assert disk_models.ce_calls == []

    async def test_service_disabled_gives_503(self, client, disk_models, rm_settings, monkeypatch):
        monkeypatch.setattr(rm_settings, "enabled", False)
        r = await client.post(URL, json={"query": "q", "passages": ["a"]})
        assert r.status_code == 503
        assert r.json()["detail"] == "Сервис RAG-моделей выключен"

    async def test_disabled_check_precedes_empty_passages_shortcut(self, client, disk_models, rm_settings, monkeypatch):
        monkeypatch.setattr(rm_settings, "enabled", False)
        r = await client.post(URL, json={"query": "q", "passages": []})
        assert r.status_code == 503

    async def test_reranker_load_failure_gives_503(self, client, disk_models):
        disk_models.ce_error = RuntimeError("битые веса")
        r = await client.post(URL, json={"query": "q", "passages": ["a"]})
        assert r.status_code == 503
        assert r.json()["detail"] == "Реранкер не загружен"

    async def test_missing_reranker_weights_give_503(self, client, fake_ml):
        r = await client.post(URL, json={"query": "q", "passages": ["a"]})
        assert r.status_code == 503
        assert r.json()["detail"] == "Реранкер не загружен"  # без серверного пути (CWE-209)

    async def test_plain_list_scores_of_equal_length(self, client, disk_models):
        await client.post(URL, json={"query": "q", "passages": ["a"]})
        model = h._MODEL_CACHE[("reranker", "ms-marco-MiniLM-L6-v2")]["model"]
        model.predict = lambda pairs: [0.1, 0.9]
        r = await client.post(URL, json={"query": "q", "passages": ["a", "b"]})
        assert r.json()["indices"] == [1, 0]
        assert r.json()["scores"] == [0.9, 0.1]

    async def test_fewer_scores_than_passages_do_not_crash(self, client, disk_models):
        await client.post(URL, json={"query": "q", "passages": ["a"]})
        model = h._MODEL_CACHE[("reranker", "ms-marco-MiniLM-L6-v2")]["model"]
        model.predict = lambda pairs: np.asarray([0.5], dtype=np.float32)
        r = await client.post(URL, json={"query": "q", "passages": ["a", "b", "c"]})
        assert r.status_code == 200
        assert r.json()["indices"] == [0]

    async def test_ties_keep_input_order(self, client, disk_models):
        r = await client.post(URL, json={"query": "zzz", "passages": ["a", "b", "c"]})
        assert r.json()["indices"] == [0, 1, 2]

    async def test_predict_failure_is_500(self, client, disk_models):
        await client.post(URL, json={"query": "q", "passages": ["a"]})
        model = h._MODEL_CACHE[("reranker", "ms-marco-MiniLM-L6-v2")]["model"]

        def boom(pairs):
            raise RuntimeError("CUDA OOM")

        model.predict = boom
        r = await client.post(URL, json={"query": "q", "passages": ["a"]})
        assert r.status_code == 500

    async def test_llm_reranker_end_to_end(self, client, fake_ml, make_model, rm_settings, monkeypatch):
        monkeypatch.setattr(rm_settings, "reranker_model", "bge-reranker-v2-gemma")
        make_model("bge-reranker-v2-gemma", config={"architectures": ["GemmaForCausalLM"]})
        r = await client.post(URL, json={"query": "q", "passages": ["короткий", "очень длинный пассаж текста"]})
        assert r.status_code == 200
        assert r.json()["indices"] == [1, 0]  # больше токенов - выше «Yes»-логит у фейка
        assert fake_ml.ce_calls == []

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"passages": ["a"]},
            {"query": 5, "passages": ["a"]},
            {"query": "q", "passages": "not-a-list"},
            {"query": "q", "passages": [1, 2]},
            {"query": "q", "passages": ["a"], "top_k": "many"},
            {"query": "q", "passages": ["a"], "top_n": [1]},
            {"query": "q", "passages": ["a"], "model": {"x": 1}},
        ],
    )
    async def test_invalid_payload_gives_422(self, client, disk_models, payload):
        r = await client.post(URL, json=payload)
        assert r.status_code == 422

    async def test_wrong_method_gives_405(self, client, disk_models):
        assert (await client.get(URL)).status_code == 405

    @pytest.mark.parametrize(
        "top_k",
        [
            0,
            -1,
        ],
    )
    async def test_non_positive_top_k_is_not_expanded(self, client, disk_models, top_k):
        r = await client.post(URL, json={"query": "a", "passages": ["a", "b", "c"], "top_k": top_k})
        assert r.status_code == 422 or r.json()["indices"] == []

    async def test_top_k_zero_gives_empty_and_negative_gives_422(self, client, disk_models):
        passages = ["a", "b", "c"]
        zero = await client.post(URL, json={"query": "a", "passages": passages, "top_k": 0})
        neg = await client.post(URL, json={"query": "a", "passages": passages, "top_k": -1})
        assert zero.status_code == 200 and zero.json()["indices"] == []
        assert neg.status_code == 422
