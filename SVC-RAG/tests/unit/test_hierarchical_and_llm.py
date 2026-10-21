"""services/hierarchical, hierarchical_indexing, llm_chat, llm_rag_judge."""

import json
from types import SimpleNamespace

import httpx
import pytest
import respx

from app.core.cef_logger import cef_audit_context as cef
from app.database.models import DocumentVector
from app.services import hierarchical_indexing as hi
from app.services import llm_chat, llm_rag_judge
from app.services.hierarchical import DocumentSummarizer, OptimizedDocumentIndex

pytestmark = pytest.mark.unit

ROUTER = respx.MockRouter(assert_all_called=False, assert_all_mocked=True)
LLM_URL = "http://backend.test:8000/api/internal/rag/llm"


@pytest.fixture(autouse=True)
def _respx():
    with ROUTER:
        yield
    ROUTER.reset()


@pytest.fixture(autouse=True)
def _clean_context():
    token = cef._chat_model.set(None)
    yield
    cef._chat_model.reset(token)


LONG_TEXT = "\n\n".join(f"Абзац номер {i}. " + "слово " * 60 for i in range(30))


# ------------------------------------------------------------ DocumentSummarizer


class TestDocumentSummarizer:
    async def test_levels_structure_and_metadata(self):
        s = DocumentSummarizer(max_chunk_size=400, chunk_overlap=50, intermediate_summary_chunks=4)
        doc = await s.create_hierarchical_summary_async(LONG_TEXT, "doc.txt", create_full_summary=False)
        l0, l1 = doc["level_0_chunks"], doc["level_1_summaries"]
        assert [c["chunk_index"] for c in l0] == list(range(len(l0)))
        assert all(c["level"] == 0 and c["doc_name"] == "doc.txt" for c in l0)
        assert len(l1) == -(-len(l0) // 4)
        assert l1[0]["chunk_range"] == (0, 3) and l1[0]["summary_index"] == 0
        assert l1[-1]["chunk_range"][1] == len(l0) - 1
        assert doc["metadata"] == {
            "doc_name": "doc.txt",
            "total_chars": len(LONG_TEXT),
            "total_chunks": len(l0),
            "total_intermediate_summaries": len(l1),
        }
        assert doc["full_text"] == LONG_TEXT

    async def test_level1_content_is_truncated_to_3000_chars_and_always_ends_with_dots(self):
        s = DocumentSummarizer(max_chunk_size=2000, chunk_overlap=0, intermediate_summary_chunks=8)
        doc = await s.create_hierarchical_summary_async(LONG_TEXT, "d", create_full_summary=False)
        for item in doc["level_1_summaries"]:
            assert item["content"].endswith("...")
            body = item["content"].split("\n\n", 1)[1]
            assert len(body) <= 3003

    async def test_short_text_is_a_single_chunk(self):
        s = DocumentSummarizer()
        doc = await s.create_hierarchical_summary_async("коротко", "d", create_full_summary=False)
        assert [c["content"] for c in doc["level_0_chunks"]] == ["коротко"]
        assert len(doc["level_1_summaries"]) == 1

    async def test_empty_text_gets_placeholder_chunk(self):
        s = DocumentSummarizer()
        doc = await s.create_hierarchical_summary_async("", "empty.pdf", create_full_summary=False)
        assert doc["level_0_chunks"][0]["content"] == "[Документ: empty.pdf]"

    async def test_plain_level2_without_llm(self):
        s = DocumentSummarizer()
        doc = await s.create_hierarchical_summary_async("текст", "d", create_full_summary=False)
        assert doc["level_2_summary"].startswith("[ДОКУМЕНТ 'd' - 5 символов, 1 чанков]")

    async def test_level2_through_async_llm(self):
        seen = []

        async def llm(prompt):
            seen.append(prompt)
            return "  Итоговое резюме  "

        s = DocumentSummarizer(llm_function=llm, max_chunk_size=400, chunk_overlap=0)
        doc = await s.create_hierarchical_summary_async(LONG_TEXT, "doc.txt")
        assert doc["level_2_summary"] == "  Итоговое резюме  "
        assert 'Документ "doc.txt"' in seen[0]
        assert "=== НАЧАЛО ДОКУМЕНТА ===" in seen[0] and "=== КОНЕЦ ДОКУМЕНТА ===" in seen[0]
        assert "=== ОСНОВНАЯ ЧАСТЬ ===" in seen[0]
        assert len(seen[0]) < 15000 + 1000

    async def test_level2_through_sync_llm(self):
        s = DocumentSummarizer(llm_function=lambda p: "синхронно")
        doc = await s.create_hierarchical_summary_async("текст", "d")
        assert doc["level_2_summary"] == "синхронно"

    @pytest.mark.parametrize("answer", ["", "   ", None])
    async def test_empty_llm_answer_falls_back_to_text_head(self, answer):
        async def llm(prompt):
            return answer

        s = DocumentSummarizer(llm_function=llm)
        doc = await s.create_hierarchical_summary_async("начало текста", "d")
        assert doc["level_2_summary"].startswith("[КРАТКОЕ СОДЕРЖАНИЕ 'd']")
        assert "начало текста" in doc["level_2_summary"]

    async def test_llm_exception_falls_back(self):
        async def llm(prompt):
            raise RuntimeError("down")

        s = DocumentSummarizer(llm_function=llm)
        doc = await s.create_hierarchical_summary_async("текст", "d")
        assert doc["level_2_summary"].startswith("[КРАТКОЕ СОДЕРЖАНИЕ 'd']")

    async def test_prompt_is_capped_at_15000_chars(self):
        seen = []

        async def llm(prompt):
            seen.append(prompt)
            return "ok"

        big = "\n\n".join("x" * 2900 for _ in range(40))
        s = DocumentSummarizer(llm_function=llm, max_chunk_size=3000, chunk_overlap=0)
        await s.create_hierarchical_summary_async(big, "d")
        assert "[...обрезано...]" in seen[0]

    @pytest.mark.parametrize("bad", [0, -3, None, "x"])
    async def test_non_positive_intermediate_chunks_fall_back_to_default(self, bad):
        """``intermediate_summary_chunks=0`` (например из env) раньше давал
        ``range(…, 0)`` и ValueError; теперь используется значение по умолчанию."""
        s = DocumentSummarizer(intermediate_summary_chunks=bad)
        doc = await s.create_hierarchical_summary_async("текст", "d", create_full_summary=False)
        assert doc["metadata"]["total_chunks"] == 1


class TestChunkParams:
    def test_none_means_no_change(self):
        s = DocumentSummarizer()
        assert s.apply_chunk_params() is None

    def test_apply_and_restore(self):
        s = DocumentSummarizer(max_chunk_size=1500, chunk_overlap=200)
        old_splitter = s.text_splitter
        backup = s.apply_chunk_params(chunk_size=500, chunk_overlap=50)
        assert (s.max_chunk_size, s.chunk_overlap) == (500, 50)
        s.restore_chunk_params(backup)
        assert (s.max_chunk_size, s.chunk_overlap) == (1500, 200)
        assert s.text_splitter is old_splitter
        s.restore_chunk_params(None)

    def test_size_floor_and_overlap_guard(self):
        s = DocumentSummarizer()
        s.apply_chunk_params(chunk_size=10, chunk_overlap=5000)
        assert s.max_chunk_size == 200 and s.chunk_overlap == 50

    def test_negative_overlap_is_clamped(self):
        s = DocumentSummarizer()
        s.apply_chunk_params(chunk_overlap=-10)
        assert s.chunk_overlap == 0


# ------------------------------------------------------- OptimizedDocumentIndex


class FakeRagClient:
    def __init__(self, count_delta=0, error=None):
        self.embed_calls = []
        self.single_calls = []
        self.count_delta = count_delta
        self.error = error

    async def embed(self, texts, model=None, kind=None):
        if self.error:
            raise self.error
        self.embed_calls.append((list(texts), model, kind))
        out = [[float(i)] for i in range(len(texts))]
        return out[: len(out) + self.count_delta] if self.count_delta < 0 else out

    async def embed_single(self, text, model=None, kind=None):
        self.single_calls.append((text, model, kind))
        return [1.0]


class FakeVectorRepo:
    def __init__(self, pairs=None, saved=None):
        self.created = []
        self.pairs = pairs or []
        self.saved = saved
        self.limit = None

    async def create_vectors_batch(self, vectors):
        self.created.extend(vectors)
        return len(vectors) if self.saved is None else self.saved

    async def similarity_search(self, embedding, limit=10):
        self.limit = limit
        return self.pairs


def _dv(kind, text, doc=1, idx=0):
    return DocumentVector(
        document_id=doc, chunk_index=idx, embedding=None, content=text, metadata={"type": kind}
    )


async def _hier_doc(**kw):
    s = DocumentSummarizer(max_chunk_size=400, chunk_overlap=0, intermediate_summary_chunks=4)
    return await s.create_hierarchical_summary_async(LONG_TEXT, "doc.txt", create_full_summary=False)


class TestOptimizedIndexWrite:
    async def test_writes_summary_sections_and_chunks_with_negative_indexes(self):
        doc = await _hier_doc()
        client, repo = FakeRagClient(), FakeVectorRepo()
        ok = await OptimizedDocumentIndex(client, repo, model="m").index_document_hierarchical_async(doc, 5)
        assert ok is True
        idx = [v.chunk_index for v in repo.created]
        n0, n1 = len(doc["level_0_chunks"]), len(doc["level_1_summaries"])
        assert idx[0] == -1
        assert idx[1 : 1 + n1] == [-2 - i for i in range(n1)]
        assert idx[1 + n1 :] == list(range(n0))
        assert {v.metadata["type"] for v in repo.created} == {"full_summary", "intermediate_summary", "detail_chunk"}
        assert all(v.document_id == 5 for v in repo.created)
        texts, model, kind = client.embed_calls[0]
        assert (model, kind, len(texts)) == ("m", "document", len(repo.created))

    async def test_texts_for_embedding_are_capped_at_10000_chars(self):
        doc = await _hier_doc()
        doc["level_2_summary"] = "я" * 25000
        client, repo = FakeRagClient(), FakeVectorRepo()
        await OptimizedDocumentIndex(client, repo).index_document_hierarchical_async(doc, 1)
        assert max(len(t) for t in client.embed_calls[0][0]) == 10000
        assert len(repo.created[0].content) == 25000  # в базе — полный текст

    async def test_embedding_count_mismatch_returns_false(self):
        doc = await _hier_doc()
        ok = await OptimizedDocumentIndex(FakeRagClient(count_delta=-1), FakeVectorRepo()).index_document_hierarchical_async(doc, 1)
        assert ok is False

    async def test_zero_saved_returns_false(self):
        doc = await _hier_doc()
        ok = await OptimizedDocumentIndex(FakeRagClient(), FakeVectorRepo(saved=0)).index_document_hierarchical_async(doc, 1)
        assert ok is False

    async def test_errors_are_wrapped_with_the_original_cause(self):
        doc = await _hier_doc()
        index = OptimizedDocumentIndex(FakeRagClient(error=ConnectionError("503 embed")), FakeVectorRepo())
        with pytest.raises(RuntimeError, match="503 embed") as exc:
            await index.index_document_hierarchical_async(doc, 1)
        assert isinstance(exc.value.__cause__, ConnectionError)


class TestSmartSearch:
    PAIRS = [
        (_dv("detail_chunk", "d1", idx=1), 0.9),
        (_dv("full_summary", "s2", idx=-1), 0.8),
        (_dv("intermediate_summary", "s1", idx=-2), 0.7),
        (_dv("detail_chunk", "d2", idx=2), 0.6),
    ]

    async def _search(self, query, pairs=None, **kw):
        client, repo = FakeRagClient(), FakeVectorRepo(pairs if pairs is not None else self.PAIRS)
        out = await OptimizedDocumentIndex(client, repo, model="mm").smart_search_async(query, **kw)
        return out, client, repo

    async def test_auto_detects_summary_queries(self):
        out, client, repo = await self._search("Дай краткое содержание документа", k=5)
        assert [o[0] for o in out] == ["s2", "s1"]
        assert client.single_calls == [("Дай краткое содержание документа", "mm", "query")]
        assert repo.limit == 15

    async def test_auto_defaults_to_detailed(self):
        out, _, _ = await self._search("какой срок оплаты", k=5)
        assert [o[0] for o in out] == ["d1", "d2"]

    @pytest.mark.parametrize("q", ["САММАРИ", "summary please", "Обзор", "о чем документ?", "резюме"])
    async def test_summary_keywords(self, q):
        out, _, _ = await self._search(q)
        assert [o[0] for o in out] == ["s2", "s1"]

    async def test_explicit_strategy_overrides_keywords(self):
        out, _, _ = await self._search("саммари", search_strategy="detailed")
        assert [o[0] for o in out] == ["d1", "d2"]

    async def test_summary_falls_back_to_all_pairs_when_no_summaries(self):
        pairs = [(_dv("detail_chunk", "only"), 0.5)]
        out, _, _ = await self._search("саммари", pairs=pairs)
        assert [o[0] for o in out] == ["only"]

    async def test_detailed_falls_back_to_all_pairs_when_no_details(self):
        pairs = [(_dv("full_summary", "sum"), 0.5)]
        out, _, _ = await self._search("вопрос", pairs=pairs)
        assert [o[0] for o in out] == ["sum"]

    async def test_k_limits_and_result_shape(self):
        out, _, _ = await self._search("вопрос", k=1)
        assert out == [("d1", 0.9, 1, 1)]

    async def test_at_most_three_full_summaries(self):
        pairs = [(_dv("full_summary", f"f{i}", idx=-1), 0.9 - i / 10) for i in range(5)]
        out, _, _ = await self._search("саммари", pairs=pairs, k=10)
        assert len(out) == 3


# --------------------------------------------------------- hierarchical_indexing


class TestLooksLikeLlmError:
    @pytest.mark.parametrize(
        "text",
        [
            "Ошибка LLM (HTTP 422)",
            "  не удалось получить ответ от модели  ",
            "Сервис LLM недоступен",
            "Запрос не помещается в контекстное окно модели",
            "Модель не загружена на стороне провайдера",
        ],
    )
    def test_error_markers(self, text):
        assert hi._looks_like_llm_error(text) is True

    @pytest.mark.parametrize("text", ["", None, "   ", "Нормальное резюме документа", "ошибка в договоре"])
    def test_normal_text(self, text):
        assert hi._looks_like_llm_error(text) is False

    def test_marker_only_counts_near_the_start(self):
        assert hi._looks_like_llm_error("x" * 250 + " ошибка llm (http 500)") is False


class TestSummarizeViaBackend:
    async def test_returns_model_text(self, monkeypatch):
        async def chat(prompt, **kw):
            assert kw["purpose"] == "summarize"
            return "резюме"

        monkeypatch.setattr(llm_chat, "chat", chat)
        assert await hi._summarize_via_backend("p") == "резюме"

    async def test_exception_gives_empty_string(self, monkeypatch):
        async def chat(prompt, **kw):
            raise httpx.ConnectError("x")

        monkeypatch.setattr(llm_chat, "chat", chat)
        assert await hi._summarize_via_backend("p") == ""

    async def test_error_text_from_backend_is_not_indexed(self, monkeypatch):
        async def chat(prompt, **kw):
            return "Ошибка LLM (HTTP 422): bad"

        monkeypatch.setattr(llm_chat, "chat", chat)
        assert await hi._summarize_via_backend("p") == ""


class TestIndexDocumentHierarchically:
    async def test_returns_level0_count_and_writes_vectors(self):
        client, repo = FakeRagClient(), FakeVectorRepo()
        n = await hi.index_document_hierarchically(
            LONG_TEXT, 3, filename="f.txt", vector_repo=repo, rag_client=client, chunk_size=400, chunk_overlap=0
        )
        assert n == sum(1 for v in repo.created if v.metadata["type"] == "detail_chunk")
        assert n > 1

    async def test_overlap_not_smaller_than_size_is_reduced(self, monkeypatch):
        captured = {}
        real = hi.DocumentSummarizer

        def spy(**kw):
            captured.update(kw)
            return real(**kw)

        monkeypatch.setattr(hi, "DocumentSummarizer", spy)
        await hi.index_document_hierarchically(
            "текст", 1, filename="f", vector_repo=FakeVectorRepo(), rag_client=FakeRagClient(),
            chunk_size=300, chunk_overlap=900,
        )
        assert captured["max_chunk_size"] == 300 and captured["chunk_overlap"] == 75

    async def test_size_has_a_floor_of_200(self, monkeypatch):
        captured = {}
        real = hi.DocumentSummarizer
        monkeypatch.setattr(hi, "DocumentSummarizer", lambda **kw: (captured.update(kw), real(**kw))[1])
        await hi.index_document_hierarchically(
            "текст", 1, filename="f", vector_repo=FakeVectorRepo(), rag_client=FakeRagClient(), chunk_size=10
        )
        assert captured["max_chunk_size"] == 200

    async def test_defaults_come_from_config(self, monkeypatch):
        captured = {}
        real = hi.DocumentSummarizer
        monkeypatch.setattr(hi, "DocumentSummarizer", lambda **kw: (captured.update(kw), real(**kw))[1])
        await hi.index_document_hierarchically(
            "текст", 1, filename="f", vector_repo=FakeVectorRepo(), rag_client=FakeRagClient()
        )
        cfg = hi.get_settings().rag
        assert captured["max_chunk_size"] == cfg.hierarchical_chunk_size
        assert captured["intermediate_summary_chunks"] == cfg.intermediate_summary_chunks

    async def test_raises_when_nothing_saved(self):
        with pytest.raises(RuntimeError, match="не сохранила вектора"):
            await hi.index_document_hierarchically(
                "текст", 1, filename="f", vector_repo=FakeVectorRepo(saved=0), rag_client=FakeRagClient()
            )


# --------------------------------------------------------------------- llm_chat


class TestLlmChat:
    async def test_posts_prompt_and_returns_content(self):
        route = ROUTER.post(LLM_URL).respond(200, json={"content": "ответ", "finish_reason": "stop"})
        out = await llm_chat.chat("вопрос", purpose="judge", temperature=0.1, max_tokens=64)
        assert out == "ответ"
        body = json.loads(route.calls.last.request.content)
        assert body == {"prompt": "вопрос", "purpose": "judge", "temperature": 0.1, "max_tokens": 64}

    async def test_chat_model_from_request_context_is_forwarded(self):
        route = ROUTER.post(LLM_URL).respond(200, json={"content": "x"})
        cef.chat_model_set_from_request(SimpleNamespace(headers={"x-astra-model": " gpt-x "}))
        await llm_chat.chat("p", purpose="summarize", temperature=0, max_tokens=1)
        assert json.loads(route.calls.last.request.content)["model_path"] == "gpt-x"

    @pytest.mark.parametrize("payload", [{}, {"content": None}, {"content": ""}])
    async def test_missing_content_is_empty_string(self, payload):
        ROUTER.post(LLM_URL).respond(200, json=payload)
        assert await llm_chat.chat("p", purpose="x", temperature=0, max_tokens=1) == ""

    @pytest.mark.parametrize("status", [400, 422, 500, 503])
    async def test_http_error_propagates(self, status):
        ROUTER.post(LLM_URL).respond(status, text="boom")
        with pytest.raises(httpx.HTTPStatusError):
            await llm_chat.chat("p", purpose="x", temperature=0, max_tokens=1)

    async def test_network_error_propagates(self):
        ROUTER.post(LLM_URL).mock(side_effect=httpx.ConnectTimeout("t"))
        with pytest.raises(httpx.ConnectTimeout):
            await llm_chat.chat("p", purpose="x", temperature=0, max_tokens=1)

    async def test_non_json_response_propagates(self):
        ROUTER.post(LLM_URL).respond(200, text="<html>")
        with pytest.raises(ValueError):
            await llm_chat.chat("p", purpose="x", temperature=0, max_tokens=1)

    async def test_no_authorization_is_sent(self):
        route = ROUTER.post(LLM_URL).respond(200, json={"content": "x"})
        await llm_chat.chat("p", purpose="x", temperature=0, max_tokens=1)
        assert "authorization" not in {k.lower() for k in route.calls.last.request.headers}

    def test_auth_hook_reads_env_key(self, monkeypatch):
        monkeypatch.setenv("MY_KEY", "k")
        assert llm_chat._auth_present(SimpleNamespace(api_key_env="MY_KEY")) is True
        assert llm_chat._auth_present(SimpleNamespace(api_key_env="MISSING_KEY")) is False
        assert llm_chat._auth_present(SimpleNamespace()) is False


# ------------------------------------------------------------------- llm judge


class TestExtractJson:
    def test_plain_json(self):
        assert llm_rag_judge._extract_json_object('{"relevant": [true]}') == {"relevant": [True]}

    def test_json_after_prose(self):
        assert llm_rag_judge._extract_json_object('Вот ответ: {"relevant": [false, true]}') == {
            "relevant": [False, True]
        }

    def test_code_fence_and_trailing_text_are_tolerated(self):
        """Закрывающие ``` или фраза после JSON больше не ломают разбор."""
        assert llm_rag_judge._extract_json_object('```json\n{"relevant": [true]}\n```') == {"relevant": [True]}
        assert llm_rag_judge._extract_json_object('{"relevant": [true]} Спасибо!') == {"relevant": [True]}

    @pytest.mark.parametrize("text", ["", None, "не json", "{broken"])
    def test_garbage(self, text):
        assert llm_rag_judge._extract_json_object(text) is None


class TestJudge:
    @pytest.fixture(autouse=True)
    def _allowed(self, monkeypatch):
        cfg = llm_rag_judge.get_settings()
        monkeypatch.setattr(cfg.rag, "eval_llm_judge_allowed", True, raising=False)

    def _llm(self, monkeypatch, answer=None, error=None):
        calls = []

        async def chat(prompt, **kw):
            calls.append((prompt, kw))
            if error:
                raise error
            return answer

        monkeypatch.setattr(llm_chat, "chat", chat)
        return calls

    async def test_no_passages(self):
        assert await llm_rag_judge.judge_chunk_relevance("q", []) == ([], None)

    async def test_disabled_by_config(self, monkeypatch):
        monkeypatch.setattr(llm_rag_judge.get_settings().rag, "eval_llm_judge_allowed", False, raising=False)
        calls = self._llm(monkeypatch, '{"relevant": [true]}')
        flags, err = await llm_rag_judge.judge_chunk_relevance("q", ["a"])
        assert flags == [False] and "отключён" in err and calls == []

    async def test_flags_in_order(self, monkeypatch):
        self._llm(monkeypatch, '{"relevant": [true, false, true]}')
        assert await llm_rag_judge.judge_chunk_relevance("q", ["a", "b", "c"]) == ([True, False, True], None)

    async def test_short_answer_is_padded_with_false_and_long_is_cut(self, monkeypatch):
        self._llm(monkeypatch, '{"relevant": [true]}')
        assert (await llm_rag_judge.judge_chunk_relevance("q", ["a", "b"]))[0] == [True, False]
        self._llm(monkeypatch, '{"relevant": [true, true, true]}')
        assert (await llm_rag_judge.judge_chunk_relevance("q", ["a"]))[0] == [True]

    async def test_unparseable_and_wrong_shape(self, monkeypatch):
        self._llm(monkeypatch, "не знаю")
        assert await llm_rag_judge.judge_chunk_relevance("q", ["a"]) == ([False], "parse_error")
        self._llm(monkeypatch, '{"other": 1}')
        assert await llm_rag_judge.judge_chunk_relevance("q", ["a"]) == ([False], "parse_error")
        self._llm(monkeypatch, '{"relevant": "yes"}')
        assert await llm_rag_judge.judge_chunk_relevance("q", ["a"]) == ([False], "relevant_not_list")

    async def test_llm_failure_returns_all_false_with_message(self, monkeypatch):
        self._llm(monkeypatch, error=RuntimeError("gateway down"))
        assert await llm_rag_judge.judge_chunk_relevance("q", ["a", "b"]) == ([False, False], "gateway down")

    async def test_prompt_contains_numbered_truncated_passages(self, monkeypatch):
        calls = self._llm(monkeypatch, '{"relevant": [true, true]}')
        await llm_rag_judge.judge_chunk_relevance("  вопрос  ", ["строка\nс переносом", "я" * 2000])
        prompt, kw = calls[0]
        assert "Запрос:\nвопрос\n" in prompt
        assert "[0] строка с переносом" in prompt
        assert "[1] " + "я" * 480 + "\n" in prompt or prompt.count("я") == 480
        assert "ровно из 2 булевых" in prompt
        assert (kw["purpose"], kw["temperature"], kw["max_tokens"]) == ("judge", 0.0, 512)

    async def test_string_false_is_not_treated_as_true(self, monkeypatch):
        """``bool("false")`` — True; строки вместо булевых приводятся строго."""
        self._llm(monkeypatch, '{"relevant": ["false", "false"]}')
        assert (await llm_rag_judge.judge_chunk_relevance("q", ["a", "b"]))[0] == [False, False]

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("true", True), ("TRUE", True), (" yes ", True), ("false", False), ("no", False), ("", False),
         (True, True), (False, False), (1, True), (0, False), (None, False), ([], False)],
    )
    def test_coerce_flag(self, raw, expected):
        assert llm_rag_judge._coerce_flag(raw) is expected
