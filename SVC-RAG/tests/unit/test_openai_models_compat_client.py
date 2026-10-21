"""clients/openai_models_compat_client: эмбеддинги и реранк через OpenAI-совместимый шлюз (respx) — формат запросов, повторы, отказы, адаптация батча."""

import json

import httpx
import pytest
import respx

from app.clients import embed_parallel
from app.clients import openai_models_compat_client as oc

pytestmark = pytest.mark.unit

BASE = "http://gw.test"
EMBED = f"{BASE}/v1/embeddings"
RERANK = f"{BASE}/v1/rerank"
ROUTER = respx.MockRouter(assert_all_called=False, assert_all_mocked=True)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    async def no_sleep(_):
        return None

    monkeypatch.setattr(oc.asyncio, "sleep", no_sleep)
    oc._RERANK_PAUSED_UNTIL.clear()
    monkeypatch.setattr(oc, "_RERANK_PAUSE_SEC", 600.0)
    with ROUTER:
        yield
    ROUTER.reset()
    oc._RERANK_PAUSED_UNTIL.clear()


def client(**kw):
    params = dict(provider_id="gw", base_url=BASE + "/", embedding_model="emb-1", reranker_model="rr-1", embed_batch_size=4, embed_concurrency=1)
    params.update(kw)
    return oc.OpenAICompatModelsClient(**params)


def echo_embeddings(request, dim=8):
    """Шлюз-эхо: вектор кодирует длину текста, чтобы проверять соответствие текстов и векторов."""
    body = json.loads(request.content)
    inputs = body.get("input") or body.get("texts")
    items = [{"index": i, "embedding": [float(len(t))] * dim} for i, t in enumerate(inputs)]
    return httpx.Response(200, json={"data": items})


class TestConstruction:
    def test_normalisation(self):
        c = client(embed_path="embed", rerank_path="rank", embed_batch_size=0, embed_concurrency=0, timeout="12", embed_payload_style=" Bifrost-Passthrough ")

        assert (c.base_url, c.embed_path, c.rerank_path) == (BASE, "/embed", "/rank") and c.timeout == 12.0
        assert (c.embed_batch_size, c.embed_concurrency, c.embed_payload_style) == (24, 1, "bifrost-passthrough")

    def test_headers(self, monkeypatch):
        monkeypatch.setenv("GW_KEY", " sk-1 ")

        headers = client(api_key_env="GW_KEY", extra_headers={"x-bf-passthrough-extra-params": "true"})._headers()

        assert headers["Authorization"] == "Bearer sk-1" and headers["X-API-Key"] == "sk-1" and headers["x-bf-passthrough-extra-params"] == "true"
        assert "Authorization" not in client()._headers() and client(api_key_env="MISSING_ENV").get_api_key() == ""

    def test_payload_styles(self):
        assert client()._embed_payload(["a"], "m", "query") == {"model": "m", "input": ["a"]}
        bifrost = client(embed_payload_style="bifrost-passthrough")._embed_payload(["a"], "m", None)
        assert bifrost == {"model": "m", "messages": [{"role": "user", "content": "Send request"}], "texts": ["a"], "kind": "document"}
        assert "input" not in bifrost  # оба поля сразу шлюз отвергает (400)

    def test_rerank_payload_styles(self):
        assert client()._rerank_payload("q", ["a", "b"], "m", 5) == {"model": "m", "query": "q", "documents": ["a", "b"], "top_n": 2}
        bifrost = client(rerank_payload_style="bifrost-passthrough")._rerank_payload("q", ["a", "b", "c"], "m", 2)
        assert bifrost["passages"] == ["a", "b", "c"] and bifrost["top_k"] == "2" and bifrost["messages"]


class TestRefusalHelpers:
    @pytest.mark.parametrize(
        ("body", "permanent"),
        [("Content blocked by guardrail (pre_call): custom policy", True), ("BLOCKED BY CONTENT POLICY", True), ('{"error":"content_filter"}', True), ("Guardrail service unavailable", False), ("", False)],
    )
    def test_permanent_refusal(self, body, permanent):
        assert oc._is_permanent_refusal(httpx.Response(500, text=body)) is permanent

    def test_reason_fields_are_found_at_any_depth(self):
        body = {"error": {"message": "blocked", "code": 400, "details": {"entities": ["PERSON", "ORG"], "raw": {"x": 1}}}, "other": "skip"}

        fields = oc._walk_reason_fields(body)

        assert fields == {"error.message": "blocked", "error.code": 400, "error.details.entities": ["PERSON", "ORG"]}

    def test_reason_walk_is_bounded(self):
        deep = cur = {}
        for _ in range(20):
            cur["message"] = "m"
            cur["next"] = {}
            cur = cur["next"]

        assert len(oc._walk_reason_fields(deep)) <= 8

    def test_guardrail_report(self):
        response = httpx.Response(500, json={"error": {"message": "Content blocked by guardrail"}}, headers={"x-request-id": "r1", "set-cookie": "secret=1"})

        report = oc._guardrail_report(response)

        assert "HTTP 500" in report and "сработавший маркер: ['content blocked by guardrail']" in report and "error.message" in report
        assert "x-request-id" in report and "secret" not in report

    def test_report_for_non_json_bodies(self):
        assert "тело не JSON" in oc._guardrail_report(httpx.Response(500, text="<html>oops</html>"))

    def test_rejected_text_report_and_preview_modes(self, monkeypatch):
        text = "строка один\nстрока \"два\""

        monkeypatch.setattr(oc, "_BLOCKED_PREVIEW_CHARS", -1)
        assert oc._preview(text) == json.dumps(text, ensure_ascii=False)
        monkeypatch.setattr(oc, "_BLOCKED_PREVIEW_CHARS", 0)
        assert "скрыт" in oc._preview(text) and "строка один" not in oc._text_report(text)
        monkeypatch.setattr(oc, "_BLOCKED_PREVIEW_CHARS", 5)
        assert oc._preview(text) == "строк…"
        assert "sha1=" in oc._text_report(text) and "длина=" in oc._text_report(text)

    def test_embed_log_context(self):
        oc.set_embed_log_context(store="kb", document_id=7, file=None)

        assert oc.get_embed_log_context() == {"store": "kb", "document_id": 7} and "kb" in oc._ctx_line()
        oc.set_embed_log_context()
        assert oc.get_embed_log_context() is None and "не задан" in oc._ctx_line()


class TestParsing:
    P = staticmethod(oc.OpenAICompatModelsClient._parse_embeddings)

    def test_openai_shape_is_sorted_by_index(self):
        data = {"data": [{"index": 1, "embedding": [2.0]}, {"index": 0, "embedding": [1.0]}]}

        assert self.P(data, 2) == [[1.0], [2.0]]

    def test_native_shape(self):
        assert self.P({"embeddings": [[1, 2], [3, 4]]}, 2) == [[1, 2], [3, 4]]

    @pytest.mark.parametrize("data", [{"data": [{"index": 0, "embedding": [1.0]}]}, {"embeddings": [[1.0]]}])
    def test_wrong_count_is_an_error(self, data):
        with pytest.raises(ValueError, match="не совпадает"):
            self.P(data, 2)

    def test_vectors_wrapped_into_a_chat_completion_are_found(self):
        vec = [0.1] * 8
        data = {"choices": [{"message": {"embedding": vec}}, {"message": {"embedding": [0.2] * 8}}], "usage": {}}

        assert self.P(data, 2) == [vec, [0.2] * 8]

    def test_wrapped_vectors_respect_choice_index(self):
        data = {"choices": [{"index": 1, "message": {"embedding": [2.0] * 8}}, {"index": 0, "message": {"embedding": [1.0] * 8}}]}

        assert self.P(data, 2) == [[1.0] * 8, [2.0] * 8]

    def test_no_vectors(self):
        with pytest.raises(ValueError, match="нет вектора"):
            self.P({"choices": None, "error": "x"}, 1)

    def test_vector_detection(self):
        is_vector = oc.OpenAICompatModelsClient._is_vector

        assert is_vector([0.1] * 8) and is_vector([1] * 100)
        assert not is_vector([0.1] * 7) and not is_vector([True] * 8) and not is_vector(["a"] * 8) and not is_vector("x" * 8) and not is_vector(None)

    def test_structure_description_never_leaks_data(self):
        text = json.dumps(oc.OpenAICompatModelsClient._describe({"secret": "пароль", "vec": [0.5] * 8, "list": [{"a": 1}], "n": 5, "empty": []}), ensure_ascii=False)

        assert "пароль" not in text and "ВЕКТОР[8]" in text and "строка(6)" in text and "список[0]" in text


class TestListAndHealth:
    async def test_models(self):
        ROUTER.get(f"{BASE}/v1/models").mock(return_value=httpx.Response(200, json={"data": [{"id": "a"}, {"id": ""}, "x", {"id": "b"}]}))

        assert await client().list_models() == ["a", "b"]

    @pytest.mark.parametrize("response", [httpx.Response(500), httpx.Response(200, text="not json")])
    async def test_models_failures_give_an_empty_list(self, response):
        ROUTER.get(f"{BASE}/v1/models").mock(return_value=response)

        assert await client().list_models() == []

    async def test_health(self):
        route = ROUTER.get(f"{BASE}/v1/models").mock(return_value=httpx.Response(200, json={}))
        assert await client().health() is True

        route.mock(return_value=httpx.Response(401))
        assert await client().health() is False

        route.mock(side_effect=httpx.ConnectError("refused"))
        assert await client().health() is False


class TestEmbed:
    async def test_batches_are_split_and_order_is_kept(self):
        route = ROUTER.post(EMBED).mock(side_effect=echo_embeddings)
        data = [("т" * n) for n in (1, 2, 3, 4, 5, 6, 7, 8, 9)]

        vectors = await client(embed_batch_size=4, embed_concurrency=3).embed(data)

        assert [v[0] for v in vectors] == [float(len(t)) for t in data] and route.call_count == 3

    async def test_request_shape_and_auth(self, monkeypatch):
        monkeypatch.setenv("GW_KEY", "sk-1")
        route = ROUTER.post(EMBED).mock(side_effect=echo_embeddings)

        await client(api_key_env="GW_KEY").embed(["привет"], model="custom")

        request = route.calls[0].request
        assert json.loads(request.content) == {"model": "custom", "input": ["привет"]} and request.headers["authorization"] == "Bearer sk-1"

    async def test_bifrost_style_sends_texts_and_kind(self):
        route = ROUTER.post(EMBED).mock(side_effect=echo_embeddings)

        await client(embed_payload_style="bifrost-passthrough").embed(["a"], kind="query")

        body = json.loads(route.calls[0].request.content)
        assert body["texts"] == ["a"] and body["kind"] == "query" and "input" not in body

    async def test_empty_and_unconfigured(self):
        assert await client().embed([]) == []
        with pytest.raises(ValueError, match="embedding_model не задан"):
            await client(embedding_model=None).embed(["a"])

    async def test_embed_single(self):
        ROUTER.post(EMBED).mock(side_effect=echo_embeddings)

        assert await client().embed_single("abc") == [3.0] * 8

    async def test_flaky_gateway_is_retried(self):
        route = ROUTER.post(EMBED).mock(side_effect=[httpx.Response(500, text="Guardrail service unavailable"), httpx.Response(503), echo_embeddings])

        vectors = await client().embed(["ab"])

        assert vectors == [[2.0] * 8] and route.call_count == 3

    async def test_retries_are_bounded(self):
        route = ROUTER.post(EMBED).mock(return_value=httpx.Response(500, text="upstream down"))

        with pytest.raises(RuntimeError, match="HTTP 500"):
            await client().embed(["ab"])

        assert route.call_count == oc._RETRY_ATTEMPTS

    async def test_network_errors_are_retried(self):
        route = ROUTER.post(EMBED).mock(side_effect=[httpx.ConnectError("refused"), echo_embeddings])

        assert await client().embed(["ab"]) == [[2.0] * 8] and route.call_count == 2

    async def test_client_errors_are_not_retried_and_carry_the_gateway_text(self):
        route = ROUTER.post(EMBED).mock(return_value=httpx.Response(400, text="bad model name"))

        with pytest.raises(RuntimeError) as exc:
            await client().embed(["ab"])

        assert route.call_count == 1 and "HTTP 400" in str(exc.value) and "bad model name" in str(exc.value)

    async def test_policy_refusals_are_not_retried(self):
        route = ROUTER.post(EMBED).mock(return_value=httpx.Response(500, text="Content blocked by guardrail (pre_call)"))

        with pytest.raises(RuntimeError):
            await client(embed_batch_size=1).embed(["секрет"])

        assert route.call_count == 2  # попытка + разбор отклонённого фрагмента по одному (диагностика)

    async def test_blocked_batch_is_isolated_per_text(self, monkeypatch, caplog):
        caplog.set_level("ERROR")

        def respond(request):
            texts = json.loads(request.content)["input"]
            if any("запрещено" in t for t in texts):
                return httpx.Response(500, text="Content blocked by guardrail")
            return echo_embeddings(request)

        route = ROUTER.post(EMBED).mock(side_effect=respond)

        with pytest.raises(RuntimeError):
            await client(embed_batch_size=3).embed(["ok", "запрещено", "тоже ok"])

        assert route.call_count == 1 + 3  # весь батч + три одиночных

    async def test_isolation_can_be_disabled(self, monkeypatch):
        monkeypatch.setattr(oc, "_ISOLATE_BLOCKED", False)
        route = ROUTER.post(EMBED).mock(return_value=httpx.Response(500, text="Content blocked by guardrail"))

        with pytest.raises(RuntimeError):
            await client().embed(["a", "b"])

        assert route.call_count == 1

    async def test_timeouts_split_the_batch_and_shrink_the_client_limits(self):
        def respond(request):
            if len(json.loads(request.content)["input"]) > 2:
                return httpx.Response(504, text="<html>504 Gateway Time-out</html>")
            return echo_embeddings(request)

        ROUTER.post(EMBED).mock(side_effect=respond)
        c = client(embed_batch_size=8, embed_concurrency=4)
        data = [("т" * n) for n in range(1, 9)]

        vectors = await c.embed(data)

        assert [v[0] for v in vectors] == [float(len(t)) for t in data]
        # 8 → 4 → 2: каждый неудачный уровень сужает потолок; на пороге 2 заодно падает параллелизм.
        assert c._batch_cap == 2 and c._effective_batch_size() == 2 and c._conc_cap == 2 and c._effective_concurrency() == 2

    async def test_a_hard_timeout_error_has_a_short_readable_message(self):
        ROUTER.post(EMBED).mock(return_value=httpx.Response(504, text="<html>" + "x" * 1000 + "</html>"))

        with pytest.raises(embed_parallel.EmbedBatchTimeout) as exc:
            await client().embed(["a"])

        assert "HTTP 504 от шлюза" in str(exc.value) and len(str(exc.value)) < 300

    async def test_wrong_vector_count_from_the_gateway(self):
        ROUTER.post(EMBED).mock(return_value=httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0] * 8}]}))

        with pytest.raises(ValueError, match="не совпадает"):
            await client().embed(["a", "b"])

    async def test_probe_dim(self):
        route = ROUTER.post(EMBED).mock(side_effect=lambda request: echo_embeddings(request, dim=1536))

        assert await client().probe_dim() == 1536 and json.loads(route.calls[0].request.content)["input"] == ["ping"]

    async def test_probe_requires_a_model_and_reports_gateway_errors(self):
        with pytest.raises(ValueError):
            await client(embedding_model=None).probe_dim()

        ROUTER.post(EMBED).mock(return_value=httpx.Response(404, text="no such route"))
        with pytest.raises(RuntimeError, match="HTTP 404"):
            await client().probe_dim()


class TestAdaptation:
    @pytest.fixture(autouse=True)
    def _recover_after_three(self, monkeypatch):
        monkeypatch.setattr(embed_parallel, "ADAPT_RECOVER_AFTER", 3)
        monkeypatch.setattr(embed_parallel, "ADAPT_ON_TIMEOUT", True)
        monkeypatch.setattr(embed_parallel, "CONC_DROP_BATCH_FLOOR", 2)

    def test_defaults_without_caps(self):
        c = client(embed_batch_size=24, embed_concurrency=4)

        assert (c._effective_batch_size(), c._effective_concurrency()) == (24, 4)

    def test_a_timeout_halves_the_batch(self):
        c = client(embed_batch_size=24, embed_concurrency=4)

        c._note_batch_shrink(24)

        assert c._batch_cap == 12 and c._effective_batch_size() == 12 and c._effective_concurrency() == 4

    def test_concurrency_drops_only_when_the_batch_cannot_shrink_further(self):
        c = client(embed_batch_size=8, embed_concurrency=4)

        c._note_batch_shrink(8)  # cap=4
        assert c._conc_cap is None
        c._note_batch_shrink(4)  # cap=2 → порог достигнут
        assert c._batch_cap == 2 and c._conc_cap == 2 and c._effective_concurrency() == 2

    def test_caps_never_grow_from_a_shrink(self):
        c = client(embed_batch_size=8)
        c._note_batch_shrink(4)

        c._note_batch_shrink(16)

        assert c._batch_cap == 2

    def test_recovery_needs_clean_full_batch_runs(self):
        c = client(embed_batch_size=8, embed_concurrency=4)
        c._note_batch_shrink(8)
        c._note_batch_shrink(4)  # batch_cap=2, conc_cap=2

        for _ in range(2):
            c._note_embed_ok(used_full_batch=False)  # поисковые запросы ничего не доказывают
        assert c._batch_cap == 2
        for _ in range(3):
            c._note_embed_ok(used_full_batch=True)
        assert c._batch_cap == 4  # батч растёт первым
        for _ in range(3):
            c._note_embed_ok(used_full_batch=True)
        assert c._batch_cap is None  # дорос до конфигурационного
        for _ in range(3):
            c._note_embed_ok(used_full_batch=True)
        assert c._conc_cap is None and c._effective_concurrency() == 4  # параллелизм возвращается последним

    def test_recovery_can_be_disabled(self, monkeypatch):
        monkeypatch.setattr(embed_parallel, "ADAPT_RECOVER_AFTER", 0)
        c = client(embed_batch_size=8)
        c._note_batch_shrink(8)

        for _ in range(10):
            c._note_embed_ok(used_full_batch=True)

        assert c._batch_cap == 4

    def test_adaptation_can_be_disabled(self, monkeypatch):
        monkeypatch.setattr(embed_parallel, "ADAPT_ON_TIMEOUT", False)
        c = client(embed_batch_size=8)

        c._note_batch_shrink(8)

        assert c._batch_cap is None and c._shrunk_this_run is True

    def test_a_new_timeout_resets_the_clean_run_counter(self):
        c = client(embed_batch_size=16)
        c._note_batch_shrink(16)
        c._note_embed_ok(True)
        c._note_embed_ok(True)

        c._note_batch_shrink(8)
        c._note_embed_ok(True)

        assert c._clean_runs == 1


class TestRerank:
    async def test_cohere_style_results(self):
        route = ROUTER.post(RERANK).mock(return_value=httpx.Response(200, json={"results": [{"index": 2, "relevance_score": 0.9}, {"index": 0, "score": 0.4}, {"index": None}, "x"]}))

        pairs = await client().rerank("запрос", ["a", "b", "c"], top_k=2)

        assert pairs == [(2, 0.9), (0, 0.4)]
        assert json.loads(route.calls[0].request.content) == {"model": "rr-1", "query": "запрос", "documents": ["a", "b", "c"], "top_n": 2}

    async def test_aligned_scores_are_sorted_into_pairs(self):
        ROUTER.post(RERANK).mock(return_value=httpx.Response(200, json={"scores": [0.1, 0.9, 0.5]}))

        assert await client().rerank("q", ["a", "b", "c"]) == [(1, 0.9), (2, 0.5), (0, 0.1)]

    async def test_scores_nested_in_a_gateway_envelope(self):
        ROUTER.post(RERANK).mock(return_value=httpx.Response(200, json={"choices": None, "extra_fields": {"raw_response": {"relevance_scores": [0.2, 0.8]}}}))

        assert await client().rerank("q", ["a", "b"]) == [(1, 0.8), (0, 0.2)]

    async def test_scores_inside_a_message_content(self):
        content = 'Вот результат: {"results": [{"index": 1, "relevance_score": 0.7}]} спасибо'
        ROUTER.post(RERANK).mock(return_value=httpx.Response(200, json={"choices": [{"message": {"content": content}}]}))

        assert await client().rerank("q", ["a", "b"]) == [(1, 0.7)]

    async def test_bifrost_style_payload(self):
        route = ROUTER.post(RERANK).mock(return_value=httpx.Response(200, json={"results": [{"index": 0, "relevance_score": 1.0}]}))

        await client(rerank_payload_style="bifrost-passthrough").rerank("q", ["a"], top_k=5)

        body = json.loads(route.calls[0].request.content)
        assert body["passages"] == ["a"] and body["top_k"] == "1" and "documents" not in body

    @pytest.mark.parametrize("payload", [{}, {"results": []}, {"scores": [0.1]}, {"scores": ["a", "b"]}])
    async def test_unparseable_answers_are_errors_not_empty_lists(self, payload):
        ROUTER.post(RERANK).mock(return_value=httpx.Response(200, json=payload))

        with pytest.raises(ValueError, match="реранк не разобран"):
            await client().rerank("q", ["a", "b"])

    async def test_nothing_to_rerank_or_no_model(self):
        assert await client().rerank("q", []) == []
        with pytest.raises(ValueError, match="reranker_model не задан"):
            await client(reranker_model=None).rerank("q", ["a"])

    async def test_a_gateway_timeout_pauses_the_reranker(self):
        route = ROUTER.post(RERANK).mock(return_value=httpx.Response(504, text="Gateway Time-out"))
        c = client()

        with pytest.raises(RuntimeError):
            await c.rerank("q", ["a"])
        calls_after_failure = route.call_count
        with pytest.raises(RuntimeError, match="на паузе"):
            await c.rerank("q", ["a"])

        assert route.call_count == calls_after_failure and "rr-1" in oc._RERANK_PAUSED_UNTIL

    async def test_the_pause_is_lifted_after_a_success(self):
        oc._RERANK_PAUSED_UNTIL["rr-1"] = 0.0  # пауза истекла
        ROUTER.post(RERANK).mock(return_value=httpx.Response(200, json={"results": [{"index": 0, "relevance_score": 1.0}]}))

        await client().rerank("q", ["a"])

        assert "rr-1" not in oc._RERANK_PAUSED_UNTIL

    async def test_pause_is_per_model_and_can_be_disabled(self, monkeypatch):
        ROUTER.post(RERANK).mock(return_value=httpx.Response(504, text="Gateway Time-out"))
        monkeypatch.setattr(oc, "_RERANK_PAUSE_SEC", 0.0)

        with pytest.raises(RuntimeError):
            await client().rerank("q", ["a"])

        assert oc._RERANK_PAUSED_UNTIL == {}

    async def test_other_errors_do_not_pause_the_reranker(self):
        ROUTER.post(RERANK).mock(return_value=httpx.Response(404, text="no route"))

        with pytest.raises(RuntimeError, match="HTTP 404"):
            await client().rerank("q", ["a"])

        assert oc._RERANK_PAUSED_UNTIL == {}
