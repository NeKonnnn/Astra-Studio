"""Хвосты покрытия services/{bm25_index,chunker,document_duplicate,embed_routing,hierarchical,llm_chat,
llm_rag_judge,rag_search_helpers,rerank_helpers,retrieval_eval} (R1)."""

import asyncio
import logging
import sys
from types import SimpleNamespace

import httpx
import pytest
import respx

from app.core import heap_trim
from app.core.config import get_settings
from app.database.models import DocumentVector
from app.services import bm25_index as bm
from app.services import chunker as ch
from app.services import document_duplicate as dd
from app.services import embed_routing as er
from app.services import llm_chat, llm_rag_judge, rag_search_helpers as rsh
from app.services import rerank_helpers as rr
from app.services import retrieval_eval as ev
from app.services.hierarchical import DocumentSummarizer

pytestmark = pytest.mark.unit


def dv(doc, chunk, content="текст"):
    return DocumentVector(id=doc * 100 + chunk, document_id=doc, chunk_index=chunk, embedding=[0.1] * 8,
                          content=content, metadata={})


# ───────────────────────── bm25_index ─────────────────────────


class TestBm25Tail:
    def test_trim_heap_calls_allocator_when_enabled(self, monkeypatch):
        calls = []
        monkeypatch.setattr(get_settings().rag, "bm25_malloc_trim", True)
        monkeypatch.setattr(heap_trim, "trim", lambda reason: calls.append(reason))
        bm._trim_heap("тест")
        assert calls == ["тест"]

    def test_trim_heap_is_skipped_when_disabled(self, monkeypatch):
        calls = []
        monkeypatch.setattr(get_settings().rag, "bm25_malloc_trim", False)
        monkeypatch.setattr(heap_trim, "trim", lambda reason: calls.append(reason))
        bm._trim_heap("тест")
        assert calls == []

    def test_trim_heap_never_raises(self, monkeypatch):
        def broken(reason):
            raise OSError("malloc_trim недоступен")

        monkeypatch.setattr(get_settings().rag, "bm25_malloc_trim", True)
        monkeypatch.setattr(heap_trim, "trim", broken)
        bm._trim_heap("тест")  # исключение проглочено

    def test_deferred_release_is_skipped_without_running_loop(self):
        async def fetch():
            return []

        idx = bm.InMemoryBm25Index(fetch)
        idx.index, idx.metadatas, idx.chunk_count = object(), [{"document_id": 1, "chunk": 0}], 1
        idx.retain_seconds = 5.0
        idx._release_after_search()  # вне event loop отложить нечем — индекс остаётся
        assert idx.ready is True

    async def test_concurrent_builds_share_one_fetch(self):
        calls = {"n": 0}

        async def fetch():
            calls["n"] += 1
            await asyncio.sleep(0)
            return [(1, 0, "кошка сидит"), (1, 1, "собака бежит")]

        idx = bm.InMemoryBm25Index(fetch)
        first, second = await asyncio.gather(idx.ensure_built(), idx.ensure_built())
        assert first is True and second is True and calls["n"] == 1

    async def test_duplicate_and_missing_bm25_hits(self):
        class Stub:
            async def search(self, query, pool):
                return [(2, 5, 2.0), (1, 0, 1.5), (1, 0, 1.0)]

        async def fetch_chunk(doc, chunk):
            return None

        out = await bm.hybrid_combine_vector_bm25(
            "q", [(dv(1, 0), 0.9)], 3, bm25_index=Stub(), bm25_weight=0.5, fetch_chunk=fetch_chunk)
        assert [(v.document_id, v.chunk_index) for v, _ in out] == [(1, 0)]


    async def test_guaranteed_sparse_slot_unwraps_tuple_rows_from_repo(self):
        class Stub:
            async def search(self, query, pool):
                return [(2, 5, 2.0)]

        async def fetch_chunk(doc, chunk):
            return (dv(doc, chunk, "только в BM25"), 0.0)

        out = await bm.hybrid_combine_vector_bm25(
            "q", [(dv(1, 0), 0.9)], 1, bm25_index=Stub(), bm25_weight=0.5, fetch_chunk=fetch_chunk)
        assert {(v.document_id, v.chunk_index) for v, _ in out} == {(2, 5)}

    async def test_guaranteed_sparse_slot_uses_vector_candidate_when_known(self):
        class Stub:
            async def search(self, query, pool):
                return [(2, 2, 2.0)]

        async def fetch_chunk(doc, chunk):
            raise AssertionError("кандидат уже есть среди векторных — в репозиторий ходить не нужно")

        vectors = [(dv(1, 0), 0.9), (dv(2, 2), 0.1)]
        out = await bm.hybrid_combine_vector_bm25(
            "q", vectors, 1, bm25_index=Stub(), bm25_weight=0.5, fetch_chunk=fetch_chunk)
        assert {(v.document_id, v.chunk_index) for v, _ in out} == {(2, 2)}


# ───────────────────────── chunker ─────────────────────────


class TestChunkerTail:
    def test_table_keeps_blank_and_separator_lines_inside(self):
        assert ch._protect_tables("| a | b |\n\n| c | d |\nпосле") == ["| a | b |\n\n| c | d |", "после"]

    def test_page_break_only_text_is_kept_as_one_chunk(self):
        out = ch.split_into_chunks_with_meta("--- Страница 1 ---\n", chunk_size=300)
        assert [t for t, _ in out] == ["--- Страница 1 ---"]

    def test_markdown_without_hash_headings_uses_generic_headings(self):
        out = ch.split_into_chunks_with_meta("Простой текст без решёток. " * 30, chunk_size=300,
                                             chunk_overlap=0, chunking_strategy="markdown")
        assert len(out) >= 2 and all(m["chunking_strategy"] for _, m in out)

    @pytest.mark.parametrize("raw", [None, "", " Semantic ", "structure", "library", "default", "weird", "HIERARCHICAL"])
    def test_normalized_strategy_is_always_one_the_dispatcher_knows(self, raw):
        """Поэтому финальный else в split_into_chunks_with_meta недостижим."""
        assert ch.normalize_chunking_strategy(raw) in {"universal", "fixed", "markdown", "separators",
                                                       "semantic", "hierarchical"}

    def test_semantic_without_paragraphs_gives_nothing(self):
        assert ch._split_semantic("   ", chunk_size=300, chunk_overlap=0) == []

    def test_semantic_without_overlap_starts_next_chunk_from_new_paragraph(self):
        paras = "\n\n".join(f"Абзац {i}. " + "слово " * 40 for i in range(6))
        out = ch.split_into_chunks_with_meta(paras, chunk_size=300, chunk_overlap=0, chunking_strategy="semantic")
        assert len(out) == 6 and out[1][0].startswith("Абзац 1.")

    def test_blank_pieces_from_splitter_are_dropped(self):
        class Splitter:
            def split_text(self, text):
                return ["  ", "первая часть", "", "вторая часть"]

        packed = ch._pack_plain(["x" * 50], chunk_size=10, chunk_overlap=0, strategy="fixed", splitter=Splitter())
        assert [t for t, _ in packed] == ["первая часть", "вторая часть"]

    def test_universal_drops_blank_pieces_of_oversized_blocks(self, monkeypatch):
        class Splitter:
            def split_text(self, text):
                return ["  ", "осталась только эта часть"]

        monkeypatch.setattr(ch, "_make_splitter", lambda *a, **kw: Splitter())
        out = ch._split_universal("Z" * 400, chunk_size=200, chunk_overlap=0)
        assert [t for t, _ in out] == ["осталась только эта часть"]


# ───────────────────────── document_duplicate ─────────────────────────


class TestDocumentDuplicate:
    def test_http_detail_defaults(self):
        assert dd.duplicate_http_detail() == {"code": dd.DUPLICATE_CODE, "message": dd.DUPLICATE_MESSAGE,
                                              "document_id": None, "filename": None}

    def test_http_detail_with_values(self):
        out = dd.duplicate_http_detail(document_id=5, filename="a.txt", message="своё")
        assert out == {"code": "duplicate_document", "message": "своё", "document_id": 5, "filename": "a.txt"}


# ───────────────────────── embed_routing ─────────────────────────


class TestEmbedRoutingConcurrency:
    async def test_default_model_is_picked_once_for_concurrent_callers(self, monkeypatch):
        monkeypatch.setattr(er, "_auto_models", {})
        monkeypatch.setattr(er, "_auto_locks", {})
        monkeypatch.setenv("RAG_EMBED_AUTOPICK", "1")
        entry = SimpleNamespace(id="R1-PROVIDER", embedding_model="", chat_only=False)
        monkeypatch.setattr(er, "_provider_entry", lambda pid: entry)
        calls = {"n": 0}

        async def autopick(e):
            calls["n"] += 1
            await asyncio.sleep(0)
            return "auto-embed-1"

        monkeypatch.setattr(er, "_autopick_embedding_model", autopick)
        got = await asyncio.gather(er._default_model_for("R1-PROVIDER"), er._default_model_for("R1-PROVIDER"))
        assert got == ["auto-embed-1", "auto-embed-1"] and calls["n"] == 1

    async def test_dimension_is_probed_once_for_concurrent_callers(self, monkeypatch):
        monkeypatch.setattr(er, "_dims", {})
        monkeypatch.setattr(er, "_dim_locks", {})
        calls = {"n": 0}

        class Client:
            async def embed(self, texts, model=None, kind=None):
                calls["n"] += 1
                await asyncio.sleep(0)
                return [[0.1] * 12]

        client = Client()
        got = await asyncio.gather(er.resolve_dim(client, "R1-PROVIDER", "m"), er.resolve_dim(client, "R1-PROVIDER", "m"))
        assert got == [12, 12] and calls["n"] == 1


# ───────────────────────── hierarchical / llm_chat / llm_rag_judge ─────────────────────────


class TestHierarchicalTail:
    async def test_non_callable_llm_function_falls_back_to_text_head(self):
        s = DocumentSummarizer(llm_function="это не функция", max_chunk_size=400, chunk_overlap=50,
                               intermediate_summary_chunks=4)
        text = "\n\n".join(f"Абзац {i}. " + "слово " * 60 for i in range(5))
        doc = await s.create_hierarchical_summary_async(text, "doc.txt", create_full_summary=True)
        assert doc["level_2_summary"].startswith("[КРАТКОЕ СОДЕРЖАНИЕ 'doc.txt']")


LLM_URL = "http://backend.test:8000/api/internal/rag/llm"


class TestLlmChatTail:
    async def test_unreadable_error_body_does_not_hide_the_http_error(self, monkeypatch):
        def broken_text(self):
            raise ValueError("кодировка")

        with respx.MockRouter(assert_all_called=False, assert_all_mocked=True) as router:
            router.post(LLM_URL).respond(502, content=b"bad gateway")
            monkeypatch.setattr(httpx.Response, "text", property(broken_text))
            with pytest.raises(httpx.HTTPStatusError) as exc:
                await llm_chat.chat("p", purpose="x", temperature=0, max_tokens=1)
        assert exc.value.response.status_code == 502


class TestLlmJudgeTail:
    def test_broken_json_object_at_the_end_gives_none(self):
        assert llm_rag_judge._extract_json_object("ответ: {не json}") is None

    def test_json_object_with_noise_before_is_found(self):
        assert llm_rag_judge._extract_json_object('вот результат {"relevant": [true]}') == {"relevant": [True]}


# ───────────────────────── rag_search_helpers ─────────────────────────


class TestSearchHelpersTail:
    def test_long_query_boosts_hierarchical_for_global_store(self):
        query = "дай обзор: " + "что угодно про документ " * 12
        assert len(query) > 220
        got = rsh.resolve_auto_pipeline_strategy(query, store="global", document_id=None,
                                                 hierarchical_available=True, graph_available=False,
                                                 hybrid_available=False)
        assert got == "hierarchical"

    async def test_graph_fusion_ignores_seed_without_document(self):
        orphan = DocumentVector.model_construct(id=None, document_id=None, chunk_index=0, embedding=None,
                                                content="сирота", metadata={})

        async def fetch_chunk(doc, chunk):
            return None

        out = await rsh.fuse_seed_and_graph_hits([(orphan, 0.9), (dv(1, 0), 0.5)], {}, fetch_chunk=fetch_chunk)
        assert [(v.document_id, v.chunk_index) for v, _ in out] == [(1, 0)]

    async def test_graph_fusion_counts_repeated_seed_once(self):
        async def fetch_chunk(doc, chunk):
            return None

        out = await rsh.fuse_seed_and_graph_hits([(dv(1, 0), 0.9), (dv(1, 0), 0.4)], {}, fetch_chunk=fetch_chunk)
        assert len(out) == 1 and out[0][1] == pytest.approx(0.65 / 61)

    def test_report_survives_broken_stderr(self, monkeypatch):
        class BrokenStderr:
            def write(self, text):
                raise OSError("stderr закрыт")

            def flush(self):
                raise OSError("stderr закрыт")

        monkeypatch.setenv("RAG_METRICS_STDOUT", "1")
        monkeypatch.setattr(sys, "stderr", BrokenStderr())
        rsh.log_rag_retrieval_report(store="kb", strategy_resolved="vector", pipeline="p",
                                     final_hits=[("текст", 0.5, 1, 0)], k_requested=1)

    def test_report_goes_to_stderr_when_enabled(self, monkeypatch, capsys):
        monkeypatch.setenv("RAG_METRICS_STDOUT", "1")
        rsh.log_rag_retrieval_report(store="kb", strategy_resolved="vector", pipeline="p",
                                     final_hits=[("текст", 0.5, 1, 0)], k_requested=1)
        assert "Использована стратегия поиска: vector" in capsys.readouterr().err


# ───────────────────────── rerank_helpers / retrieval_eval ─────────────────────────


class TestRerankTable:
    async def test_debug_log_shows_moves_and_dropped_chunks(self, caplog):
        class Client:
            async def rerank(self, query, contents, top_k=None, model=None):
                return [(2, 0.9), (0, 0.5)]  # чанк 1 реранкер выбросил

        hits = [(dv(1, i, "а" * 100), 0.9 - i * 0.1) for i in range(3)]
        with caplog.at_level(logging.DEBUG, logger="app.services.rerank_helpers"):
            out, ok = await rr.rerank_vector_hits_ex("запрос", hits, Client(), top_k=2)
        assert ok is True and [d.chunk_index for d, _ in out] == [2, 0]
        assert "позиция до -> после" in caplog.text
        assert "выброшен реранкером" in caplog.text and "…" in caplog.text


class TestRetrievalEvalTail:
    def test_gold_metrics_without_gold_are_empty(self):
        hits = [("текст", 0.9, 1, 0)]
        assert ev._gold_chunk_metrics(hits, 3, []) == []
        assert ev._gold_doc_metrics(hits, 3, []) == []
