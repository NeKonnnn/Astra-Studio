"""services/retrieval_pipeline: выбор стратегии, векторный/лексический/гибридный путь, якоря, сбои — на фейках."""

import copy

import pytest

from app.core.config import get_settings
from app.database.models import DocumentVector
from app.services import retrieval_pipeline as rp
from app.services.bm25_index import InMemoryBm25Index

pytestmark = pytest.mark.unit

TOPICS = ["кошк", "собак", "птиц", "налог", "космос"]


def emb(text):
    low = (text or "").lower()
    vec = [float(low.count(t)) for t in TOPICS] + [0.01]
    norm = sum(x * x for x in vec) ** 0.5
    return [x / norm for x in vec]


def cos(a, b):
    return sum(x * y for x, y in zip(a, b))


CORPUS = {
    (1, 0): "Кошки любят спать на окне весь день, кошка мурлычет тихо",
    (1, 1): "Кошки охотятся на мышей по ночам и кошка приносит добычу",
    (2, 0): "Собаки верные друзья, собака охраняет дом и двор от чужих",
    (2, 1): "Собака гуляет во дворе, собаки любят играть с мячом вместе",
    (3, 0): "Налог на прибыль уплачивается ежеквартально и проверяется службой",
    (3, 1): "Космос огромен, и космос изучают с помощью спутников и зондов",
}
NAMES = {1: "cats.txt", 2: "dogs.txt", 3: "Налоги_отчёт.docx"}


def dv(doc, chunk, with_emb=True):
    text = CORPUS[(doc, chunk)]
    return DocumentVector(
        id=doc * 10 + chunk,
        document_id=doc,
        chunk_index=chunk,
        embedding=emb(text) if with_emb else None,
        content=text,
        metadata={},
    )


class FakeClient:
    def __init__(self):
        self.fail = None
        self.empty = False
        self.calls = []

    async def embed(self, texts, model=None, kind=None):
        self.calls.append((list(texts), kind))
        if self.fail:
            raise self.fail
        if self.empty:
            return []
        return [emb(t) for t in texts]


class FakeWorld:
    def __init__(self):
        self.vector_calls = []
        self.keyword_calls = []
        self.filename_calls = []
        self.fail_keywords = None

    async def search_vectors(self, query_emb, limit):
        self.vector_calls.append(limit)
        scored = [(dv(*key), cos(query_emb, emb(text))) for key, text in CORPUS.items()]
        scored.sort(key=lambda p: p[1], reverse=True)
        return scored[:limit]

    async def search_keywords(self, text, limit):
        self.keyword_calls.append(text)
        if self.fail_keywords:
            raise self.fail_keywords
        words = [w for w in text.lower().split() if len(w) > 3]
        out = []
        for key, body in CORPUS.items():
            n = sum(1 for w in words if w[:5] in body.lower())
            if n:
                out.append((dv(*key), float(n)))
        out.sort(key=lambda p: p[1], reverse=True)
        return out[:limit]

    async def substring_search(self, tokens, limit):
        out = []
        for key, body in CORPUS.items():
            n = sum(1 for t in tokens if t.lower() in body.lower())
            if n:
                out.append((dv(*key), float(n)))
        return out[:limit]

    async def fetch_document_chunks(self, doc_id):
        return [dv(d, c) for (d, c) in CORPUS if d == doc_id]

    async def find_docs_by_filename(self, name):
        self.filename_calls.append(name)
        return [d for d, n in NAMES.items() if name.lower() in n.lower()]

    async def get_vector_by_document_and_chunk(self, doc_id, chunk):
        return dv(doc_id, chunk) if (doc_id, chunk) in CORPUS else None


def make_bm25():
    async def fetch(document_ids=None):
        return [(d, c, t) for (d, c), t in CORPUS.items()]

    return InMemoryBm25Index(fetch)


@pytest.fixture
def cfg():
    c = copy.copy(get_settings().rag)
    for name, value in dict(
        use_reranking=False,
        rerank_top_k=20,
        min_vector_similarity=0.0,
        min_chunk_length=5,
        use_hybrid_search=True,
        hybrid_bm25_weight=0.3,
        hybrid_diversify_results=True,
        hybrid_max_chunks_per_document=2,
        vector_max_chunks_per_document=6,
        enable_graph_rag=False,
        rerank_min_score=0.0,
        sentence_window=0,
    ).items():
        setattr(c, name, value)
    return c


@pytest.fixture
def world():
    return FakeWorld()


@pytest.fixture
def client():
    return FakeClient()


async def run(world, client, cfg, query="расскажи про кошки", **kw):
    params = dict(
        store="kb",
        query=query,
        vector_query=None,
        k=3,
        document_id=None,
        use_reranking=False,
        strategy="vector",
        filters=None,
        rag_client=client,
        graph_repo=None,
        cfg=cfg,
        search_vectors=world.search_vectors,
        search_keywords=world.search_keywords,
        substring_search=world.substring_search,
        fetch_document_chunks=world.fetch_document_chunks,
        find_docs_by_filename=world.find_docs_by_filename,
        vector_repo_for_window=world,
        bm25_index=make_bm25(),
    )
    params.update(kw)
    return await rp.run_retrieval_pipeline(**params)


def stages(trace):
    return [s["stage"] for s in trace.steps]


async def only_doc(world, query_emb, limit, doc):
    pairs = await world.search_vectors(query_emb, 100)
    return [p for p in pairs if p[0].document_id == doc][:limit]


class TestEnumerationDetector:
    @pytest.mark.parametrize(
        "text",
        ["перечисли все документы", "в каких документах упоминается Константин", "выведи списком все файлы"],
    )
    def test_enumeration_phrases(self, text):
        assert rp._is_enumeration_query(text) is True

    @pytest.mark.parametrize("text", ["", "какой срок оплаты", "расскажи про кошек"])
    def test_plain_questions(self, text):
        assert rp._is_enumeration_query(text) is False


class TestTrace:
    def test_add_warn_stage_and_to_dict(self):
        t = rp.RetrievalTrace("kb", "auto", "vector", "p")
        t.add("a", count=2, details={"x": 1})
        t.warn("careful")
        t.mark_stage("embed", 0.123456)
        t.mark_stage("embed", 0.1)
        t.mark_stage(" ", -5)
        t.seconds = 1.23456789
        d = t.to_dict()
        assert d["steps"] == [{"stage": "a", "count": 2, "x": 1}]
        assert d["warnings"] == ["careful"] and d["seconds"] == 1.2346
        assert d["stage_seconds"] == {"embed": 0.2235, "unknown": 0.0}
        d["warnings"].append("x")
        assert t.warnings == ["careful"]


class TestEmptyAndErrors:
    async def test_empty_query_returns_nothing_with_warning(self, world, client, cfg):
        hits, trace = await run(world, client, cfg, query="   ")
        assert hits == [] and any("empty_query" in w for w in trace.warnings)
        assert client.calls == []

    async def test_vector_query_alone_is_enough(self, world, client, cfg):
        hits, _ = await run(world, client, cfg, query="", vector_query="кошки")
        assert hits and client.calls[0][0] == ["кошки"]

    async def test_embed_error_gives_empty_with_warning(self, world, client, cfg):
        client.fail = ConnectionError("models down")
        hits, trace = await run(world, client, cfg)
        assert hits == [] and any("embed_error" in w and "models down" in w for w in trace.warnings)
        assert world.vector_calls == []

    async def test_empty_embedding_gives_empty_with_warning(self, world, client, cfg):
        client.empty = True
        hits, trace = await run(world, client, cfg)
        assert hits == [] and any("empty_embedding" in w for w in trace.warnings)


class TestStrategyResolution:
    @pytest.mark.parametrize(
        "requested,resolved",
        [
            ("vector", "vector"),
            ("flat", "vector"),
            ("standard", "vector"),
            ("keyword", "lexical"),
            ("bm25", "lexical"),
            ("LEXICAL", "lexical"),
            ("hierarchical", "vector"),
            ("hybrid", "hybrid"),
            ("raw_cosine", "raw_cosine"),
        ],
    )
    async def test_aliases(self, world, client, cfg, requested, resolved):
        _, trace = await run(world, client, cfg, strategy=requested)
        assert trace.resolved_strategy == resolved

    async def test_auto_resolves_to_something_known(self, world, client, cfg):
        _, trace = await run(world, client, cfg, strategy="auto")
        assert trace.requested_strategy == "auto"
        assert trace.resolved_strategy in {"vector", "hybrid", "graph", "raw_cosine"}

    async def test_none_strategy_means_auto(self, world, client, cfg):
        _, trace = await run(world, client, cfg, strategy=None)
        assert trace.requested_strategy == "auto"


class TestVector:
    async def test_orders_by_cosine_and_cuts_to_k(self, world, client, cfg):
        hits, trace = await run(world, client, cfg, query="кошки", k=2)
        assert len(hits) <= 2 and hits[0][2] == 1
        assert all(isinstance(h[0], str) and isinstance(h[1], float) for h in hits)
        assert hits[0][1] >= hits[-1][1]
        assert "pgvector_cosine" in trace.pipeline and "vector_cosine_only" in stages(trace)
        assert client.calls[0][1] == "query"

    async def test_fetch_limit_is_not_smaller_than_k(self, world, client, cfg):
        await run(world, client, cfg, k=3)
        assert world.vector_calls[0] >= 3

    async def test_enumeration_query_widens_fetch(self, world, client, cfg):
        await run(world, client, cfg, query="перечисли все документы про кошки", k=3)
        assert world.vector_calls[0] >= 24

    async def test_summary_chunks_are_not_answers_for_vector(self, world, client, cfg):
        async def vectors(q, limit):
            summary = DocumentVector(
                document_id=9, chunk_index=-1, embedding=None, content="сводка", metadata={"level": 2}
            )
            return [(summary, 0.99), (dv(1, 0), 0.5)]

        hits, trace = await run(world, client, cfg, search_vectors=vectors)
        assert [h[2] for h in hits] == [1] and "hierarchical_detail_filter" in stages(trace)

    async def test_only_summaries_are_kept_when_there_are_no_details(self, world, client, cfg):
        async def vectors(q, limit):
            return [
                (
                    DocumentVector(
                        document_id=9, chunk_index=-1, embedding=None, content="сводка", metadata={"level": 2}
                    ),
                    0.9,
                )
            ]

        hits, _ = await run(world, client, cfg, search_vectors=vectors)
        assert [h[2] for h in hits] == [9]

    async def test_diversification_caps_chunks_per_document(self, world, client, cfg):
        cfg.vector_max_chunks_per_document = 1
        hits, trace = await run(world, client, cfg, query="кошки собаки налог космос", k=3)
        assert "vector_diversify" in stages(trace)
        docs = [h[2] for h in hits]
        assert len(docs) == len(set(docs))

    async def test_diversification_is_off_when_cap_is_zero_or_document_is_fixed(self, world, client, cfg):
        cfg.vector_max_chunks_per_document = 0
        _, trace = await run(world, client, cfg)
        assert "vector_diversify" not in stages(trace)
        cfg.vector_max_chunks_per_document = 1
        _, trace = await run(world, client, cfg, document_id=1)
        assert "vector_diversify" not in stages(trace)

    async def test_rerank_needs_config_switch_even_when_requested(self, world, client, cfg, monkeypatch):
        monkeypatch.setenv("RAG_FORCE_RERANK_CYRILLIC", "1")
        calls = []

        class Reranker:
            async def rerank(self, *a, **k):
                calls.append(1)
                raise TimeoutError("rerank down")

        cfg.use_reranking = False
        _, trace = await run(
            world, client, cfg, use_reranking=True, rerank_client=Reranker(), query="кошки"
        )
        assert calls == [] and not trace.warnings

    async def test_failing_reranker_degrades_to_vector_order(self, world, client, cfg, monkeypatch):
        """Докстринг пайплайна утверждает «vector / lexical — без rerank», но
        ``effective_use_reranking`` разрешает реранк и для них (known issue: расхождение)."""
        monkeypatch.setenv("RAG_FORCE_RERANK_CYRILLIC", "1")

        class Reranker:
            async def rerank(self, *a, **k):
                raise TimeoutError("rerank down")

        cfg.use_reranking = True
        hits, trace = await run(
            world, client, cfg, use_reranking=True, rerank_client=Reranker(), query="кошки", k=3
        )
        assert hits and trace.used_rerank is False
        assert any("rerank" in w for w in trace.warnings)

    async def test_user_can_switch_rerank_off_per_request(self, world, client, cfg, monkeypatch):
        monkeypatch.setenv("RAG_FORCE_RERANK_CYRILLIC", "1")

        class Reranker:
            async def rerank(self, *a, **k):
                raise AssertionError("не должен вызываться")

        cfg.use_reranking = True
        hits, trace = await run(
            world, client, cfg, use_reranking=False, rerank_client=Reranker(), query="кошки"
        )
        assert hits and not trace.warnings


class TestRawCosine:
    async def test_scores_are_pure_cosine_without_postprocessing(self, world, client, cfg):
        q = "кошки собаки"
        hits, trace = await run(world, client, cfg, query=q, strategy="raw_cosine", k=4)
        expected = sorted((cos(emb(q), emb(t)) for t in CORPUS.values()), reverse=True)[:4]
        assert [h[1] for h in hits] == pytest.approx(expected)
        assert "raw_cosine_cut" in stages(trace)
        assert world.keyword_calls == []


class TestLexical:
    async def test_bm25_only_without_vector_search(self, world, client, cfg):
        hits, trace = await run(world, client, cfg, query="собаки двор", strategy="lexical", k=2)
        assert hits and {h[2] for h in hits} == {2}
        assert "bm25_search" in stages(trace) and trace.pipeline.startswith("bm25_only")
        assert world.vector_calls == []
        # эмбеддинг запроса считается лишь ради процента релевантности
        assert client.calls and client.calls[0][1] == "query"

    async def test_no_index_warns(self, world, client, cfg):
        hits, trace = await run(world, client, cfg, strategy="lexical", bm25_index=None)
        assert hits == [] and any("BM25 индекс не передан" in w for w in trace.warnings)

    async def test_no_matches_warns(self, world, client, cfg):
        hits, trace = await run(world, client, cfg, query="абракадабраxyz", strategy="lexical")
        assert hits == [] and any("пустая выдача BM25" in w for w in trace.warnings)

    async def test_white_list_scopes_bm25_rows(self, world, client, cfg):
        hits, _ = await run(
            world, client, cfg, query="кошки собаки", strategy="lexical", document_ids=[2], k=5
        )
        assert hits and {h[2] for h in hits} == {2}

    async def test_document_id_narrows_inside_whitelist(self, world, client, cfg):
        hits, _ = await run(
            world, client, cfg, query="кошки собаки", strategy="lexical",
            document_ids=[1, 2], document_id=1, k=5,
        )
        assert hits and {h[2] for h in hits} == {1}

    async def test_missing_window_repo_warns(self, world, client, cfg):
        hits, trace = await run(
            world, client, cfg, query="кошки", strategy="lexical", vector_repo_for_window=None
        )
        assert hits == [] and any("нет vector_repo" in w for w in trace.warnings)


class TestHybrid:
    async def test_merges_vector_and_bm25(self, world, client, cfg):
        hits, trace = await run(world, client, cfg, query="кошки двор", strategy="hybrid", k=4)
        assert "bm25_rrf_merge" in stages(trace) and "bm25_rrf_merge" in trace.pipeline
        assert hits and all(h[1] > 0 for h in hits)
        assert "hybrid_rrf_diversify" in stages(trace)

    async def test_without_bm25_falls_back_to_vector_with_warning(self, world, client, cfg):
        hits, trace = await run(world, client, cfg, query="кошки", strategy="hybrid", bm25_index=None)
        assert hits and any("BM25 недоступен" in w for w in trace.warnings)
        assert "bm25_skipped" in trace.pipeline

    async def test_hybrid_switch_in_config_disables_bm25(self, world, client, cfg):
        cfg.use_hybrid_search = False
        _, trace = await run(world, client, cfg, strategy="hybrid")
        assert "bm25_rrf_merge" not in stages(trace)

    async def test_per_document_cap(self, world, client, cfg):
        cfg.hybrid_max_chunks_per_document = 1
        hits, _ = await run(world, client, cfg, query="кошки собаки налог", strategy="hybrid", k=3)
        docs = [h[2] for h in hits]
        assert len(docs) == len(set(docs))

    async def test_whitelist_scopes_bm25_part(self, world, client, cfg):
        hits, _ = await run(
            world, client, cfg, query="собаки кошки", strategy="hybrid", document_ids=[2], k=6,
            search_vectors=lambda e, l: only_doc(world, e, l, 2),
        )
        assert hits and {h[2] for h in hits} <= {2}


class TestHybridRerank:
    async def test_failing_reranker_degrades_to_fused_order(self, world, client, cfg, monkeypatch):
        monkeypatch.setenv("RAG_FORCE_RERANK_CYRILLIC", "1")
        cfg.use_reranking = True

        class BrokenReranker:
            async def rerank(self, *a, **k):
                raise TimeoutError("rerank down")

        hits, trace = await run(
            world, client, cfg, strategy="hybrid", use_reranking=True,
            rerank_client=BrokenReranker(), query="кошки", k=3,
        )
        assert hits and trace.used_rerank is False
        assert any("rerank" in w for w in trace.warnings)


class TestFilenameAnchors:
    async def test_mentioned_file_is_resolved_and_its_chunks_come_back(self, world, client, cfg):
        hits, _ = await run(world, client, cfg, query="сделай саммари по dogs.txt", strategy="hybrid", k=6)
        assert "dogs.txt" in world.filename_calls
        assert 2 in {h[2] for h in hits}

    async def test_stem_is_tried_when_full_name_is_unknown(self, world, client, cfg):
        await run(world, client, cfg, query="что в файле Налоги_отчёт.pdf", strategy="vector")
        assert world.filename_calls[0] == "Налоги_отчёт.pdf"
        assert "Налоги_отчёт" in world.filename_calls

    async def test_lookup_error_is_tolerated(self, world, client, cfg):
        async def broken(name):
            raise RuntimeError("db")

        hits, _ = await run(world, client, cfg, query="смотри dogs.txt", find_docs_by_filename=broken)
        assert isinstance(hits, list)

    async def test_whitelist_removes_foreign_anchor_documents(self, world, client, cfg):
        hits, _ = await run(
            world, client, cfg, query="смотри dogs.txt", strategy="vector", document_ids=[1],
            search_vectors=lambda e, l: only_doc(world, e, l, 1),
        )
        assert {h[2] for h in hits} <= {1}


class TestKeywordAndEntityPaths:
    async def test_graph_strategy_runs_keyword_search_and_merges(self, world, client, cfg):
        _, trace = await run(world, client, cfg, query="расскажи про кошки", strategy="graph")
        assert "keyword_search" in stages(trace) and "merge_vector_keyword" in stages(trace)
        assert world.keyword_calls

    async def test_keyword_failure_is_a_warning_not_an_error(self, world, client, cfg):
        world.fail_keywords = RuntimeError("fts down")
        hits, trace = await run(world, client, cfg, query="расскажи про кошки", strategy="graph")
        assert hits and any("keyword_search_failed" in w for w in trace.warnings)

    async def test_entity_lane_finds_proper_noun_by_substring(self, world, client, cfg):
        hits, trace = await run(world, client, cfg, query="Что известно про Космос", strategy="graph", k=5)
        lane = next(s for s in trace.steps if s["stage"] == "entity_lane")
        assert lane["count"] >= 1 and "космос" in lane["entities"]
        assert 3 in {h[2] for h in hits}

    async def test_graph_expansion_failure_is_a_warning(self, world, client, cfg):
        class BrokenGraph:
            async def expand_neighbors(self, *a, **k):
                raise RuntimeError("graph down")

        cfg.enable_graph_rag = True
        hits, trace = await run(
            world, client, cfg, query="расскажи про кошки", strategy="graph", graph_repo=BrokenGraph()
        )
        assert hits and any("graph_expand_failed" in w for w in trace.warnings)

    async def test_graph_strategy_with_repo_reports_expansion_step(self, world, client, cfg):
        class Graph:
            async def expand_neighbors(self, store, document_id, seed_chunks, **kw):
                return {(3, 1): 0.9}

        cfg.enable_graph_rag = True
        _, trace = await run(
            world, client, cfg, query="кошки", strategy="graph", graph_repo=Graph(), k=8
        )
        assert "graph_expand_neighbors" in trace.pipeline


class TestTraceTiming:
    async def test_trace_has_stage_seconds_and_total(self, world, client, cfg):
        _, trace = await run(world, client, cfg, query="кошки")
        d = trace.to_dict()
        assert d["seconds"] > 0 and "embed_query" in d["stage_seconds"]
        assert d["store"] == "kb" and d["resolved_strategy"] == "vector"
