"""Хвосты покрытия clients/{rag_models_client,openai_models_compat_client,embed_parallel} (R1).

Сеть мокается через respx; sleep в повторах отключён.
"""

import asyncio
import json
import runpy

import httpx
import pytest
import respx

import app.core.cef_logger as cef_logger
from app.clients import embed_parallel as ep
from app.clients import openai_models_compat_client as oc
from app.clients import rag_models_client as rmc

pytestmark = pytest.mark.unit

RM = "http://models.tail"
GW = "http://gw.tail"
ROUTER = respx.MockRouter(assert_all_called=False, assert_all_mocked=True)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    async def no_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    oc._RERANK_PAUSED_UNTIL.clear()
    rmc.RagModelsClient._last_ensured_dim = None
    with ROUTER:
        yield
    ROUTER.reset()
    oc._RERANK_PAUSED_UNTIL.clear()
    rmc.RagModelsClient._last_ensured_dim = None


@pytest.fixture
def ensured(monkeypatch):
    import app.dependencies as dependencies

    async def ensure(dim):
        return {"embedding_dim": dim}

    monkeypatch.setattr(dependencies, "ensure_embedding_dim", ensure)


def boom(*a, **kw):
    raise RuntimeError("cef недоступен")


def rm_client(**kw):
    c = rmc.RagModelsClient(base_url=RM, timeout=5)
    c.embed_batch_size = kw.pop("batch", 4)
    c.embed_concurrency = kw.pop("concurrency", 1)
    c.embedding_model = kw.pop("embedding_model", "emb")
    c.reranker_model = kw.pop("reranker_model", "rr")
    return c


def gw_client(**kw):
    params = dict(provider_id="gw", base_url=GW, embedding_model="emb-1", reranker_model="rr-1",
                  embed_batch_size=4, embed_concurrency=1)
    params.update(kw)
    return oc.OpenAICompatModelsClient(**params)


def echo(request, dim=8):
    body = json.loads(request.content)
    inputs = body.get("input") or body.get("texts")
    return httpx.Response(200, json={"data": [{"index": i, "embedding": [float(len(t))] * dim}
                                               for i, t in enumerate(inputs)]})


# ───────────────────────── rag_models_client ─────────────────────────


class TestRagModelsClientTail:
    def test_effective_concurrency_respects_cap(self):
        c = rm_client(concurrency=4)
        assert c._effective_concurrency() == 4
        c._conc_cap = 2
        assert c._effective_concurrency() == 2
        c._conc_cap = 99
        assert c._effective_concurrency() == 4

    def test_shrink_is_ignored_when_adaptation_disabled(self, monkeypatch):
        monkeypatch.setattr(ep, "ADAPT_ON_TIMEOUT", False)
        c = rm_client()
        c._note_batch_shrink(8)
        assert c._batch_cap is None and c._shrink_events == 0 and c._shrunk_this_run is True

    def test_shrink_never_raises_the_cap(self):
        c = rm_client(batch=24)
        c._note_batch_shrink(8)
        assert c._batch_cap == 4
        c._note_batch_shrink(40)  # 20 > 4: потолок остаётся прежним
        assert c._batch_cap == 4 and c._shrink_events == 2

    def test_cap_equal_to_configured_batch_is_dropped_without_concurrency_cap(self):
        c = rm_client(batch=4)
        c._batch_cap = 4
        c._clean_runs = ep.ADAPT_RECOVER_AFTER - 1
        c._note_embed_ok(True)
        assert c._batch_cap is None and c._conc_cap is None and c._shrink_events == 0

    async def test_embed_survives_cef_audit_failures(self, monkeypatch, ensured):
        monkeypatch.setattr(cef_logger, "log_cef_int003_model_request", boom)
        ROUTER.post(f"{RM}/v1/embeddings").mock(side_effect=echo)
        assert await rm_client().embed(["ab", "cde"]) == [[2.0] * 8, [3.0] * 8]

    async def test_embed_error_is_not_masked_by_failing_audit(self, monkeypatch, ensured):
        monkeypatch.setattr(cef_logger, "log_cef_int006_outbound_failure", boom)
        ROUTER.post(f"{RM}/v1/embeddings").respond(500, text="down")
        with pytest.raises(httpx.HTTPStatusError):
            await rm_client().embed(["ab"])

    async def test_rerank_survives_cef_audit_failures(self, monkeypatch):
        monkeypatch.setattr(cef_logger, "log_cef_int003_model_request", boom)
        ROUTER.post(f"{RM}/v1/rerank").respond(200, json={"results": [{"index": 1, "relevance_score": 0.9},
                                                                       {"index": 0, "relevance_score": 0.1}]})
        assert await rm_client().rerank("q", ["a", "b"], top_k=2) == [(1, 0.9), (0, 0.1)]

    async def test_rerank_error_is_not_masked_by_failing_audit(self, monkeypatch):
        monkeypatch.setattr(cef_logger, "log_cef_int006_outbound_failure", boom)
        ROUTER.post(f"{RM}/v1/rerank").respond(503, text="down")
        with pytest.raises(httpx.HTTPStatusError):
            await rm_client().rerank("q", ["a"])


# ───────────────────────── openai_models_compat_client ─────────────────────────


class TestOpenAiCompatTail:
    def test_invalid_preview_env_falls_back_to_full_text(self, monkeypatch):
        monkeypatch.setenv("RAG_EMBED_BLOCKED_PREVIEW_CHARS", "много")
        namespace = runpy.run_path(oc.__file__, run_name="r1_isolated_openai_client")
        assert namespace["_BLOCKED_PREVIEW_CHARS"] == -1

    def test_guardrail_report_for_json_without_known_fields(self):
        resp = httpx.Response(400, json={"foo": 1})
        report = oc._guardrail_report(resp)
        assert "знакомых ключей нет" in report and "HTTP 400" in report

    def test_guardrail_report_for_non_json_body(self):
        report = oc._guardrail_report(httpx.Response(400, text="blocked by guardrail"))
        assert "тело не JSON" in report

    async def test_retry_loop_stops_early_on_gateway_timeout_when_splittable(self):
        route = ROUTER.post(f"{GW}/x").respond(504, text="Gateway Time-out")
        c = gw_client()
        async with httpx.AsyncClient() as http:
            with pytest.raises(httpx.HTTPStatusError):
                await c._post_with_retry(http, f"{GW}/x", {}, "POST /x", splittable=True)
        assert route.call_count == oc._TIMEOUT_RETRY_ATTEMPTS

    async def test_retry_loop_stops_early_on_read_timeout_when_splittable(self):
        route = ROUTER.post(f"{GW}/x").mock(side_effect=httpx.ReadTimeout("slow"))
        c = gw_client()
        async with httpx.AsyncClient() as http:
            with pytest.raises(httpx.ReadTimeout):
                await c._post_with_retry(http, f"{GW}/x", {}, "POST /x", splittable=True)
        assert route.call_count == oc._TIMEOUT_RETRY_ATTEMPTS

    def test_timeout_reason_for_local_errors(self):
        assert oc.OpenAICompatModelsClient._timeout_reason(httpx.ReadTimeout("x")) == "ReadTimeout на нашей стороне"
        resp = httpx.Response(504, request=httpx.Request("POST", GW))
        err = httpx.HTTPStatusError("x", request=resp.request, response=resp)
        assert oc.OpenAICompatModelsClient._timeout_reason(err) == "HTTP 504 от шлюза"

    async def test_local_read_timeout_becomes_batch_timeout_with_short_reason(self):
        ROUTER.post(f"{GW}/v1/embeddings").mock(side_effect=httpx.ReadTimeout("slow"))
        with pytest.raises(ep.EmbedBatchTimeout, match="ReadTimeout на нашей стороне"):
            await gw_client().embed(["один"])

    async def test_non_http_errors_are_raised_unchanged(self):
        ROUTER.post(f"{GW}/v1/embeddings").mock(side_effect=httpx.ConnectError("refused"))
        with pytest.raises(httpx.ConnectError):
            await gw_client().embed(["один"])

    async def test_dimension_note_is_logged_for_foreign_dimension(self):
        ROUTER.post(f"{GW}/v1/embeddings").mock(side_effect=lambda r: echo(r, dim=16))
        c = gw_client()
        assert await c.embed(["ab"]) == [[2.0] * 16]
        assert c._logged_dim_note is True

    async def test_second_embed_does_not_log_dimension_again(self):
        ROUTER.post(f"{GW}/v1/embeddings").mock(side_effect=echo)
        c = gw_client()
        await c.embed(["ab"])
        assert c._logged_dim is True
        assert await c.embed(["abc"]) == [[3.0] * 8]

    async def test_empty_vectors_are_returned_as_is(self):
        ROUTER.post(f"{GW}/v1/embeddings").respond(200, json={"data": [{"index": 0, "embedding": []}]})
        assert await gw_client().embed(["ab"]) == [[]]

    def test_recover_keeps_concurrency_state_when_cap_equals_batch(self):
        c = gw_client(embed_batch_size=4)
        c._batch_cap = 4
        c._clean_runs = ep.ADAPT_RECOVER_AFTER - 1
        c._note_embed_ok(True)
        assert c._batch_cap is None and c._conc_cap is None

    async def test_embeddings_key_with_wrong_count(self):
        ROUTER.post(f"{GW}/v1/embeddings").respond(200, json={"embeddings": [[0.1] * 8]})
        with pytest.raises(ValueError, match="не совпадает с размером батча"):
            await gw_client().embed(["a", "b"])

    async def test_structurally_found_vectors_with_wrong_count(self):
        vec = [0.5] * 8
        ROUTER.post(f"{GW}/v1/embeddings").respond(200, json={"wrapper": {"payload": [vec]}})
        with pytest.raises(ValueError, match="Структура ответа"):
            await gw_client().embed(["a", "b"])

    async def _embed_with_one_timeout(self):
        def handler(request):
            texts = json.loads(request.content)["input"]
            if len(texts) > 1:
                return httpx.Response(504, text="Gateway Time-out")
            return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0] * 8}]})

        ROUTER.post(f"{GW}/v1/embeddings").mock(side_effect=handler)
        c = gw_client(embed_batch_size=2)
        assert len(await c.embed(["a", "b"])) == 2
        return c

    @pytest.mark.xfail(strict=True, reason="BUG: embed() сбрасывает и читает _shrink_this_run, а _note_batch_shrink "
                                           "ставит _shrunk_this_run (опечатка в имени, openai_models_compat_client.py:942/952): "
                                           "прогон с таймаутом засчитывается «чистым» и ускоряет откат батча/параллелизма")
    async def test_run_with_timeout_does_not_count_as_clean(self):
        c = await self._embed_with_one_timeout()
        assert c._clean_runs == 0

    async def test_run_with_timeout_counts_as_clean_known_issue(self):
        c = await self._embed_with_one_timeout()
        assert c._shrunk_this_run is True and c._clean_runs == 1

    async def test_cef_audit_failures_do_not_break_embed(self, monkeypatch):
        monkeypatch.setattr(cef_logger, "log_cef_int003_model_request", boom)
        ROUTER.post(f"{GW}/v1/embeddings").mock(side_effect=echo)
        assert await gw_client().embed(["ab"]) == [[2.0] * 8]

    async def test_failing_cef_audit_does_not_mask_http_error(self, monkeypatch):
        monkeypatch.setattr(cef_logger, "log_cef_int006_outbound_failure", boom)
        ROUTER.post(f"{GW}/v1/embeddings").respond(400, text="bad request")
        with pytest.raises(RuntimeError, match="HTTP 400"):
            await gw_client().embed(["ab"])

    # --- разбор «чьего-то» ответа ---

    def test_collect_vectors_edge_cases(self):
        collect = oc.OpenAICompatModelsClient._collect_vectors
        vec = [0.5] * 8
        assert collect([], 0) == []
        assert collect(vec, 7) == []  # глубже 6 уровней не ищем
        assert collect([vec, [1.0] * 8]) == [vec, [1.0] * 8]
        assert collect([{"index": 1, "embedding": [2.0] * 8}, {"index": 0, "embedding": [1.0] * 8}]) == [
            [1.0] * 8, [2.0] * 8]
        # embedding пустые: ничего не найдено в этой ветке, идём вглубь
        assert collect([{"embedding": []}, {"embedding": []}]) == []
        assert collect([1, 2, 3]) == []
        assert collect([{"a": 1}, {"b": {"embedding": vec}}, {"c": vec}]) == [vec]
        assert collect({"x": [{"y": 1}], "z": {"data": [vec]}}) == [vec]
        assert collect("строка") == []

    def test_collect_vectors_orders_wrapped_items_by_index(self):
        collect = oc.OpenAICompatModelsClient._collect_vectors
        a, b = [1.0] * 8, [2.0] * 8
        wrapped = [{"index": 1, "message": {"embedding": b}}, {"index": 0, "message": {"embedding": a}}]
        assert collect(wrapped) == [a, b]
        no_index = [{"message": {"embedding": b}}, {"message": {"embedding": a}}]
        assert collect(no_index) == [b, a]

    def test_describe_is_depth_limited(self):
        deep = {"a": {"b": {"c": {"d": {"e": {"f": 1}}}}}}
        described = oc.OpenAICompatModelsClient._describe(deep)
        assert described["a"]["b"]["c"]["d"]["e"] == "..."

    # --- реранк ---

    def test_parse_rerank_content_variants(self):
        parse = oc.OpenAICompatModelsClient._parse_rerank
        wrapped = {"choices": [
            "не словарь",
            {"message": {"content": ""}},
            {"message": {"content": "не json"}},
            {"message": {"content": 'ответ: {"x": 1}'}},
            {"message": {"content": 'ответ: {"scores": [0.2, 0.9]}'}},
        ]}
        assert parse(wrapped, 2) == [(1, 0.9), (0, 0.2)]
        assert parse({"choices": [{"message": {"content": "{}"}}]}, 2) == []
        assert parse({"choices": [{"message": {"content": 5}}]}, 2) == []

    def test_rerank_from_obj_shapes(self):
        parse = oc.OpenAICompatModelsClient._rerank_from_obj
        assert parse({"results": [{"index": 0, "score": 0.4}, {"nope": 1}]}, 2) == [(0, 0.4)]
        assert parse({"results": [{"nope": 1}]}, 2) == []
        assert parse({"relevance_scores": [0.1, 0.3, 0.2]}, 3) == [(1, 0.3), (2, 0.2), (0, 0.1)]
        assert parse({"scores": [0.1, True]}, 2) == []  # bool — не скор
        assert parse({"scores": [0.1]}, 2) == []  # не выровнено с пассажами
        assert parse([{"junk": 1}, {"deeper": {"scores": [0.5, 0.6]}}], 2) == [(1, 0.6), (0, 0.5)]
        assert parse([1, 2], 2) == []
        assert parse("текст", 2) == []

    async def test_rerank_survives_cef_audit_failures(self, monkeypatch):
        monkeypatch.setattr(cef_logger, "log_cef_int003_model_request", boom)
        ROUTER.post(f"{GW}/v1/rerank").respond(200, json={"results": [{"index": 0, "relevance_score": 0.5}]})
        assert await gw_client().rerank("q", ["a"]) == [(0, 0.5)]

    async def test_rerank_error_is_not_masked_by_failing_audit(self, monkeypatch):
        monkeypatch.setattr(cef_logger, "log_cef_int006_outbound_failure", boom)
        ROUTER.post(f"{GW}/v1/rerank").respond(400, text="no model")
        with pytest.raises(RuntimeError, match="HTTP 400"):
            await gw_client().rerank("q", ["a"])


class TestGuardrailIsolation:
    """Отказ политики шлюза на батче → разбор по одному фрагменту."""

    BLOCK = "Content blocked by guardrail (pre_call): custom policy"

    def _gateway(self, per_text):
        def handler(request):
            texts = json.loads(request.content)["input"]
            if len(texts) > 1:
                return httpx.Response(400, text=self.BLOCK)
            return per_text(texts[0])

        ROUTER.post(f"{GW}/v1/embeddings").mock(side_effect=handler)

    async def test_blocked_fragment_is_isolated_and_error_is_raised(self, caplog):
        self._gateway(lambda t: httpx.Response(400, text=self.BLOCK) if t == "bad" else httpx.Response(
            200, json={"data": [{"index": 0, "embedding": [1.0] * 8}]}))
        with pytest.raises(RuntimeError, match="HTTP 400"):
            await gw_client().embed(["ok", "bad"])
        assert "GUARDRAIL ИТОГ" in caplog.text and "отклонено 1 из 2" in caplog.text

    async def test_non_policy_errors_in_isolation_are_skipped(self, caplog):
        self._gateway(lambda t: httpx.Response(500, text="boom"))
        with pytest.raises(RuntimeError):
            await gw_client().embed(["a", "b"])
        assert "по одному все 2 фрагментов прошли" in caplog.text

    async def test_network_errors_in_isolation_are_skipped(self, caplog):
        def handler(request):
            texts = json.loads(request.content)["input"]
            if len(texts) > 1:
                return httpx.Response(400, text=self.BLOCK)
            raise httpx.ConnectError("refused")

        ROUTER.post(f"{GW}/v1/embeddings").mock(side_effect=handler)
        with pytest.raises(RuntimeError):
            await gw_client().embed(["a", "b"])
        assert "по одному все 2 фрагментов прошли" in caplog.text

    async def test_isolation_can_be_disabled(self, monkeypatch, caplog):
        monkeypatch.setattr(oc, "_ISOLATE_BLOCKED", False)
        self._gateway(lambda t: httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0] * 8}]}))
        with pytest.raises(RuntimeError):
            await gw_client().embed(["a", "b"])
        assert "разбор батча 1–2 по фрагментам ПРОПУЩЕН" in caplog.text


# ───────────────────────── embed_parallel ─────────────────────────


class TestEmbedParallelTail:
    @staticmethod
    def vec(batch):
        return [[float(len(t))] for t in batch]

    @pytest.mark.parametrize("concurrency", [1, 3])
    async def test_order_log_off_keeps_order(self, monkeypatch, concurrency):
        monkeypatch.setattr(ep, "_ORDER_LOG", False)

        async def embed_batch(start, batch):
            return self.vec(batch)

        texts = ["a", "bb", "ccc", "dddd", "eeeee"]
        out = await ep.embed_texts_in_batches(texts, batch_size=2, concurrency=concurrency, embed_batch=embed_batch)
        assert out == [[1.0], [2.0], [3.0], [4.0], [5.0]]

    @pytest.mark.parametrize("concurrency", [1, 2])
    async def test_timeout_split_without_shrink_callback(self, concurrency):
        calls = []

        async def embed_batch(start, batch):
            calls.append(len(batch))
            if len(batch) > 1:
                raise ep.EmbedBatchTimeout("slow")
            return self.vec(batch)

        out = await ep.embed_texts_in_batches(["a", "bb", "ccc", "dddd"], batch_size=4, concurrency=concurrency,
                                              embed_batch=embed_batch)
        assert out == [[1.0], [2.0], [3.0], [4.0]] and calls[0] == 4

    async def test_failing_shrink_callback_does_not_stop_indexing(self):
        async def embed_batch(start, batch):
            if len(batch) > 1:
                raise ep.EmbedBatchTimeout("slow")
            return self.vec(batch)

        def broken(size):
            raise RuntimeError("callback bug")

        out = await ep.embed_texts_in_batches(["a", "bb"], batch_size=2, concurrency=1, embed_batch=embed_batch,
                                              on_batch_shrink=broken)
        assert out == [[1.0], [2.0]]
