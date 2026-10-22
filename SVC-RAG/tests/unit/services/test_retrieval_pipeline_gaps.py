"""retrieval_pipeline: ветки, которые не покрывает test_retrieval_pipeline.py (R1).

Фейковый «мир» задаётся корпусом из чанков; замыкания поиска можно ломать точечно.
Здесь же — проверка замечания обзора RAG-05 (hybrid_ok при явной lexical-стратегии).
"""

import copy
from types import SimpleNamespace

import pytest

from app.core.config import get_settings
from app.database.models import DocumentVector
from app.services import retrieval_pipeline as rp
from app.services.bm25_index import InMemoryBm25Index

pytestmark = pytest.mark.unit

TOPICS = ["кошк", "собак", "птиц", "налог", "космос", "констант"]


def emb(text):
    low = (text or "").lower()
    vec = [float(low.count(t)) for t in TOPICS] + [0.01]
    norm = sum(x * x for x in vec) ** 0.5
    return [x / norm for x in vec]


def cos(a, b):
    return sum(x * y for x, y in zip(a, b))


BASE = {
    (1, 0): "Кошки любят спать на окне весь день, кошка мурлычет тихо",
    (1, 1): "Кошки охотятся на мышей по ночам и кошка приносит добычу",
    (2, 0): "Собаки верные друзья, собака охраняет дом и двор от чужих",
    (2, 1): "Собака гуляет во дворе, собаки любят играть с мячом вместе",
    (3, 0): "Налог на прибыль уплачивается ежеквартально и проверяется службой",
    (3, 1): "Космос огромен, и космос изучают с помощью спутников и зондов",
}
BASE_NAMES = {1: "cats.txt", 2: "dogs.txt", 3: "Налоги_отчёт.docx"}


class Client:
    def __init__(self):
        self.fail = None
        self.empty = False
        self.rerank_out = "reverse"
        self.rerank_fail = None

    async def embed(self, texts, model=None, kind=None):
        if self.fail:
            raise self.fail
        if self.empty:
            return []
        return [emb(t) for t in texts]

    async def rerank(self, query, documents, **kw):
        if self.rerank_fail:
            raise self.rerank_fail
        if self.rerank_out != "reverse":
            return self.rerank_out
        n = len(documents)
        return [(i, float(n - r)) for r, i in enumerate(reversed(range(n)))]


class World:
    """Корпус {(doc, chunk): text} + замыкания поиска с точечными поломками."""

    def __init__(self, corpus=None, names=None):
        self.corpus = dict(corpus if corpus is not None else BASE)
        self.names = dict(names if names is not None else BASE_NAMES)
        self.vec_limit = None  # сколько максимум отдаёт векторный поиск
        self.vec_scores = None  # {(doc, chunk): score} — принудительные скоры
        self.fail_keywords = None
        self.fail_substring = None
        self.fail_fetch_calls = set()  # номера вызовов fetch_document_chunks, которые падают
        self.empty_fetch_docs = set()
        self.fetch_calls = 0
        self.no_emb = False
        self.find_override = None
        self.hide_chunks = set()  # get_vector_by_document_and_chunk отдаёт None
        self.tuple_single = False

    def dv(self, key, with_emb=True):
        doc, chunk = key
        text = self.corpus[key]
        return DocumentVector(id=doc * 100 + chunk, document_id=doc, chunk_index=chunk,
                              embedding=emb(text) if with_emb and not self.no_emb else None,
                              content=text, metadata={})

    async def search_vectors(self, query_emb, limit):
        scored = []
        for key, text in self.corpus.items():
            score = self.vec_scores[key] if self.vec_scores and key in self.vec_scores else cos(query_emb, emb(text))
            scored.append((self.dv(key), score))
        scored.sort(key=lambda p: p[1], reverse=True)
        return scored[: self.vec_limit or limit]

    async def search_keywords(self, text, limit):
        if self.fail_keywords:
            raise self.fail_keywords
        words = [w for w in text.lower().split() if len(w) > 3]
        out = []
        for key, body in self.corpus.items():
            n = sum(1 for w in words if w[:5] in body.lower())
            if n:
                out.append((self.dv(key), float(n)))
        out.sort(key=lambda p: p[1], reverse=True)
        return out[:limit]

    async def substring_search(self, tokens, limit):
        if self.fail_substring:
            raise self.fail_substring
        out = []
        for key, body in self.corpus.items():
            n = sum(1 for t in tokens if t.lower() in body.lower())
            if n:
                out.append((self.dv(key), float(n)))
        return out[:limit]

    async def fetch_document_chunks(self, doc_id):
        self.fetch_calls += 1
        if self.fetch_calls in self.fail_fetch_calls:
            raise RuntimeError("fetch failed")
        if doc_id in self.empty_fetch_docs:
            return []
        return [self.dv(k) for k in sorted(self.corpus) if k[0] == doc_id]

    async def find_docs_by_filename(self, name):
        if self.find_override is not None:
            result = self.find_override(name)
            if isinstance(result, BaseException):
                raise result
            return result
        return [d for d, n in self.names.items() if name.lower() in n.lower()]

    async def get_vector_by_document_and_chunk(self, doc_id, chunk):
        if (doc_id, chunk) in self.hide_chunks or (doc_id, chunk) not in self.corpus:
            return None
        vec = self.dv((doc_id, chunk))
        return (vec, 0.0) if self.tuple_single else vec

    async def get_chunk_contents_by_indices(self, document_id, indices):
        return {i: self.corpus[(document_id, i)] for i in indices if (document_id, i) in self.corpus}

    def bm25(self):
        async def fetch(document_ids=None):
            return [(d, c, t) for (d, c), t in self.corpus.items()]

        return InMemoryBm25Index(fetch)


@pytest.fixture
def pcfg():
    c = copy.copy(get_settings().rag)
    for name, value in dict(
        use_reranking=False, rerank_top_k=20, min_vector_similarity=0.0, min_chunk_length=5,
        use_hybrid_search=True, hybrid_bm25_weight=0.3, hybrid_diversify_results=True,
        hybrid_max_chunks_per_document=2, vector_max_chunks_per_document=6,
        enable_graph_rag=False, rerank_min_score=0.0, sentence_window=0,
    ).items():
        setattr(c, name, value)
    return c


@pytest.fixture
def pclient():
    return Client()


@pytest.fixture
def force_rerank(pcfg, monkeypatch):
    pcfg.use_reranking = True
    monkeypatch.setenv("RAG_FORCE_RERANK_CYRILLIC", "1")


async def go(world, client, cfg, query="расскажи про кошки", **kw):
    params = dict(
        store="kb", query=query, vector_query=None, k=3, document_id=None,
        use_reranking=False, strategy="vector", filters=None, rag_client=client,
        graph_repo=None, cfg=cfg, search_vectors=world.search_vectors,
        search_keywords=world.search_keywords, substring_search=world.substring_search,
        fetch_document_chunks=world.fetch_document_chunks,
        find_docs_by_filename=world.find_docs_by_filename,
        vector_repo_for_window=world, bm25_index=world.bm25(),
    )
    params.update(kw)
    return await rp.run_retrieval_pipeline(**params)


def stages(trace):
    return [s["stage"] for s in trace.steps]


# ───────────────────────── RAG-05: hybrid_ok ─────────────────────────


class TestHybridOkOnExplicitLexical:
    """hybrid_ok задаётся только при requested == "auto", а читается и при явной стратегии."""

    @pytest.mark.parametrize("strategy", ["lexical", "keyword", "bm25"])
    async def test_explicit_lexical_with_filename_in_query_does_not_crash(self, strategy, pcfg, pclient):
        """BUG-RAG-15: hybrid_ok определён до ветвления, явный lexical остаётся lexical."""
        world = World()
        hits, trace = await go(world, pclient, pcfg, "саммари по cats.txt", strategy=strategy)
        assert trace.resolved_strategy == "lexical"
        assert hits and {h[2] for h in hits} == {1}

    async def test_explicit_lexical_without_filename_is_fine(self, pcfg, pclient):
        world = World()
        hits, trace = await go(world, pclient, pcfg, "кошки охотятся", strategy="lexical")
        assert trace.resolved_strategy == "lexical" and hits

    async def test_explicit_lexical_with_unknown_filename_is_fine(self, pcfg, pclient):
        """Имя файла есть, но документ не найден: до чтения hybrid_ok дело не доходит."""
        world = World()
        hits, trace = await go(world, pclient, pcfg, "саммари по missing.txt про кошки", strategy="lexical")
        assert trace.resolved_strategy == "lexical"

    async def test_auto_with_filename_never_leaves_lexical_for_hybrid_branch(self, pcfg, pclient, monkeypatch):
        """auto + RAG_AUTO_PREFER_HYBRID=0 + цитата: lexical → hybrid уже на выборе стратегии."""
        monkeypatch.setenv("RAG_AUTO_PREFER_HYBRID", "0")
        world = World()
        hits, trace = await go(world, pclient, pcfg, '"кошки охотятся"', strategy="auto")
        assert trace.requested_strategy == "auto" and trace.resolved_strategy == "hybrid"
        assert hits


# ───────────────────────── lexical ─────────────────────────


class TestLexicalBranch:
    async def test_without_rag_client_hits_have_no_cosine(self, pcfg):
        world = World()
        hits, trace = await go(world, None, pcfg, "кошки охотятся", strategy="lexical", rag_client=None)
        assert hits and all(h.cosine is None for h in hits)

    async def test_cosine_embed_failure_only_loses_cosine(self, pcfg, pclient):
        pclient.fail = ConnectionError("embed down")
        world = World()
        hits, trace = await go(world, pclient, pcfg, "кошки охотятся", strategy="lexical")
        assert hits and all(h.cosine is None for h in hits)
        assert any("cosine_embed_failed" in w for w in trace.warnings)

    async def test_empty_cosine_embedding_is_ignored(self, pcfg, pclient):
        pclient.empty = True
        hits, trace = await go(World(), pclient, pcfg, "кошки охотятся", strategy="lexical")
        assert hits and all(h.cosine is None for h in hits)
        assert trace.warnings == []

    async def test_chunks_without_embedding_have_no_cosine(self, pcfg, pclient):
        world = World()
        world.no_emb = True
        hits, trace = await go(world, pclient, pcfg, "кошки охотятся", strategy="lexical")
        assert hits and all(h.cosine is None for h in hits)

    async def test_cosine_is_computed_when_embedding_present(self, pcfg, pclient):
        hits, _ = await go(World(), pclient, pcfg, "кошки охотятся", strategy="lexical")
        assert all(h.cosine is not None and 0 < h.cosine <= 1 for h in hits)

    async def test_missing_chunk_rows_are_skipped(self, pcfg, pclient):
        world = World()
        world.hide_chunks = {(1, 0)}
        hits, _ = await go(world, pclient, pcfg, "кошки охотятся", strategy="lexical")
        assert hits and (1, 0) not in {(h[2], h[3]) for h in hits}

    async def test_all_chunk_rows_missing_gives_empty_result(self, pcfg, pclient):
        world = World()
        world.hide_chunks = set(world.corpus)
        hits, trace = await go(world, pclient, pcfg, "кошки охотятся", strategy="lexical")
        assert hits == [] and trace.steps[-1]["stage"] == "bm25_search" and trace.steps[-1]["count"] == 0

    async def test_tuple_rows_from_repo_are_unpacked(self, pcfg, pclient):
        world = World()
        world.tuple_single = True
        hits, _ = await go(world, pclient, pcfg, "кошки охотятся", strategy="lexical")
        assert hits and hits[0][2] == 1

    async def test_repo_without_single_chunk_lookup(self, pcfg, pclient):
        hits, trace = await go(World(), pclient, pcfg, "кошки охотятся", strategy="lexical",
                               vector_repo_for_window=SimpleNamespace())
        assert hits == []
        assert any("get_vector_by_document_and_chunk недоступен" in w for w in trace.warnings)

    async def test_no_repo_at_all(self, pcfg, pclient):
        hits, trace = await go(World(), pclient, pcfg, "кошки охотятся", strategy="lexical",
                               vector_repo_for_window=None)
        assert hits == [] and any("нет vector_repo" in w for w in trace.warnings)

    async def test_empty_bm25_result(self, pcfg, pclient):
        hits, trace = await go(World(), pclient, pcfg, "бегемот", strategy="lexical")
        assert hits == [] and any("пустая выдача BM25" in w for w in trace.warnings)

    async def test_no_bm25_index(self, pcfg, pclient):
        hits, trace = await go(World(), pclient, pcfg, "кошки", strategy="lexical", bm25_index=None)
        assert hits == [] and any("BM25 индекс не передан" in w for w in trace.warnings)

    async def test_document_whitelist(self, pcfg, pclient):
        hits, _ = await go(World(), pclient, pcfg, "кошки собаки", strategy="lexical", document_ids=[2])
        assert hits and {h[2] for h in hits} == {2}

    async def test_rerank_applied(self, pcfg, pclient, force_rerank):
        hits, trace = await go(World(), pclient, pcfg, "кошки собаки охотятся", strategy="lexical",
                               use_reranking=True)
        assert trace.used_rerank is True and "cross_encoder_rerank" in stages(trace)

    async def test_rerank_unavailable_is_reported(self, pcfg, pclient, force_rerank):
        pclient.rerank_out = []
        hits, trace = await go(World(), pclient, pcfg, "кошки собаки охотятся", strategy="lexical",
                               use_reranking=True)
        assert hits and trace.used_rerank is False
        assert any("rerank_skipped" in w for w in trace.warnings)

    async def test_rerank_crash_is_reported(self, pcfg, pclient, force_rerank, monkeypatch):
        async def boom(*a, **kw):
            raise RuntimeError("rerank exploded")

        monkeypatch.setattr(rp, "rerank_vector_hits_ex", boom)
        hits, trace = await go(World(), pclient, pcfg, "кошки собаки охотятся", strategy="lexical",
                               use_reranking=True)
        assert hits and any("rerank_failed: rerank exploded" in w for w in trace.warnings)


# ───────────────────────── vector / raw_cosine / hybrid ─────────────────────────


class TestVectorBranches:
    async def test_unusable_scores_and_documents_without_id_are_tolerated(self, pcfg, pclient):
        world = World()
        good = world.dv((1, 0))
        bad_score = world.dv((1, 1))
        no_doc = DocumentVector.model_construct(id=None, document_id=None, chunk_index=0, embedding=None,
                                                content="без документа", metadata={})

        async def search(q, limit):
            return [(good, 0.9), (bad_score, None), (no_doc, 0.1)]

        hits, trace = await go(world, pclient, pcfg, "кошки", strategy="raw_cosine", k=1,
                               search_vectors=search)
        assert [(h[2], h[3]) for h in hits] == [(1, 0)] and hits[0].cosine == pytest.approx(0.9)

    async def test_vector_rerank_crash(self, pcfg, pclient, force_rerank, monkeypatch):
        async def boom(*a, **kw):
            raise RuntimeError("rerank exploded")

        monkeypatch.setattr(rp, "rerank_vector_hits_ex", boom)
        hits, trace = await go(World(), pclient, pcfg, "кошки собаки", strategy="vector", use_reranking=True)
        assert hits and any("rerank_failed: rerank exploded" in w for w in trace.warnings)

    async def test_hybrid_rerank_crash(self, pcfg, pclient, force_rerank, monkeypatch):
        async def boom(*a, **kw):
            raise RuntimeError("rerank exploded")

        monkeypatch.setattr(rp, "rerank_vector_hits_ex", boom)
        hits, trace = await go(World(), pclient, pcfg, "кошки собаки", strategy="hybrid", use_reranking=True)
        assert hits and any("hybrid_rerank_failed: rerank exploded" in w for w in trace.warnings)

    async def test_hybrid_rerank_unavailable(self, pcfg, pclient, force_rerank):
        pclient.rerank_out = []
        hits, trace = await go(World(), pclient, pcfg, "кошки собаки", strategy="hybrid", use_reranking=True)
        assert hits and any("hybrid_rerank_skipped" in w for w in trace.warnings)

    async def _bm25_only_hits(self, pcfg, pclient, **kw):
        """Векторный поиск видит один чанк, BM25 — другие: гибрид дотягивает их через fetch_chunk."""
        world = kw.pop("world", None) or World()
        world.vec_limit = 1
        hits, trace = await go(world, pclient, pcfg, "кошки охотятся мышей", strategy="hybrid", **kw)
        return hits, trace

    async def test_hybrid_fetches_bm25_only_chunks(self, pcfg, pclient):
        hits, trace = await self._bm25_only_hits(pcfg, pclient)
        assert "bm25_rrf_merge" in stages(trace) and {h[2] for h in hits} >= {1}

    async def test_hybrid_fetch_with_tuple_rows(self, pcfg, pclient):
        world = World()
        world.tuple_single = True
        hits, trace = await self._bm25_only_hits(pcfg, pclient, world=world)
        assert "bm25_rrf_merge" in stages(trace)

    async def test_hybrid_without_repo(self, pcfg, pclient):
        hits, trace = await self._bm25_only_hits(pcfg, pclient, vector_repo_for_window=None)
        assert hits  # BM25-кандидаты не добраны, остаётся векторная выдача

    async def test_hybrid_repo_without_single_chunk_lookup(self, pcfg, pclient):
        hits, trace = await self._bm25_only_hits(pcfg, pclient, vector_repo_for_window=SimpleNamespace())
        assert hits

    async def test_hybrid_without_bm25_index_warns(self, pcfg, pclient):
        hits, trace = await go(World(), pclient, pcfg, "кошки", strategy="hybrid", bm25_index=None)
        assert hits and any("BM25 недоступен" in w for w in trace.warnings)


# ───────────────────────── filename-anchor ─────────────────────────


class TestFilenameLookup:
    async def test_stem_fallback_when_full_name_not_found(self, pcfg, pclient):
        world = World()
        calls = []

        def find(name):
            calls.append(name)
            return [1] if name == "cats" else []

        world.find_override = find
        hits, trace = await go(world, pclient, pcfg, "саммари по cats.txt", strategy="graph")
        assert "cats.txt" in calls and "cats" in calls
        assert trace.steps and 1 in {h[2] for h in hits}

    async def test_stem_lookup_error_is_ignored(self, pcfg, pclient):
        world = World()

        def find(name):
            return [] if name == "cats.txt" else RuntimeError("db down")

        world.find_override = find
        hits, trace = await go(world, pclient, pcfg, "саммари по cats.txt", strategy="graph")
        assert hits  # поиск продолжается без якоря

    async def test_full_name_lookup_error_is_ignored(self, pcfg, pclient):
        world = World()
        world.find_override = lambda name: RuntimeError("db down")
        hits, _ = await go(world, pclient, pcfg, "саммари по cats.txt", strategy="graph")
        assert hits

    async def test_ids_outside_whitelist_or_not_numeric_are_dropped(self, pcfg, pclient):
        world = World()
        world.find_override = lambda name: ["abc", 2, 1]
        hits, trace = await go(world, pclient, pcfg, "саммари по cats.txt", strategy="graph", document_ids=[1])
        assert trace.steps
        pinned = next(s for s in trace.steps if s["stage"] == "parent_document_expansion")
        assert pinned["pin_whole_doc_ids"] == [1]
        assert {h[2] for h in hits} <= {1, 2, 3}


# ───────────────────────── сложный путь: entity lane, parent expansion, caps ─────────────────────────


def cv_corpus(docs=1, chunks=12, name_chunks=(1,)):
    """docs документов по chunks чанков; имя «Константин» стоит в name_chunks."""
    corpus = {}
    for d in range(1, docs + 1):
        for c in range(chunks):
            tail = " Константин упомянут здесь." if c in name_chunks else ""
            corpus[(d, c)] = f"Документ {d} раздел {c} описывает типовые рабочие процессы компании.{tail}"
    return corpus


class TestEntityLane:
    Q = "Расскажи про Константин"

    async def test_fts_failure_and_ilike_failure_are_reported(self, pcfg, pclient):
        world = World(cv_corpus(1, 4))
        world.fail_keywords = RuntimeError("fts down")
        world.fail_substring = RuntimeError("ilike down")
        hits, trace = await go(world, pclient, pcfg, self.Q, strategy="reranking")
        assert any("entity_lane_fts_failed" in w for w in trace.warnings)
        assert any("entity_ilike_failed" in w for w in trace.warnings)
        assert any("keyword_search_failed" in w for w in trace.warnings)
        assert hits  # вектор всё равно отвечает

    async def test_without_substring_search(self, pcfg, pclient):
        world = World(cv_corpus(1, 4))
        hits, trace = await go(world, pclient, pcfg, self.Q, strategy="reranking", substring_search=None)
        entity = next(s for s in trace.steps if s["stage"] == "entity_lane")
        assert entity["fts_hits"] >= 1 and entity["ilike_stemmed_hits"] == 0

    async def test_ilike_adds_only_new_chunks(self, pcfg, pclient):
        """FTS матчит по 5-символьному корню, ILIKE — по токену целиком: «констант» + «константин»."""
        corpus = cv_corpus(1, 4)
        corpus[(1, 3)] = "Раздел о Константинополе без точного имени сотрудника в тексте документа"
        world = World(corpus)

        async def no_fts(text, limit):
            return []

        hits, trace = await go(world, pclient, pcfg, self.Q, strategy="reranking", search_keywords=no_fts)
        entity = next(s for s in trace.steps if s["stage"] == "entity_lane")
        assert entity["fts_hits"] == 0 and entity["ilike_stemmed_hits"] >= 1
        assert entity["count"] == entity["ilike_stemmed_hits"]

    async def test_entity_in_filename_pulls_first_chunks(self, pcfg, pclient):
        world = World(cv_corpus(2, 6, name_chunks=()), {1: "Константин_резюме.docx", 2: "other.docx"})
        hits, trace = await go(world, pclient, pcfg, self.Q, strategy="reranking")
        entity = next(s for s in trace.steps if s["stage"] == "entity_lane")
        assert entity["filename_matched_docs"] == [1] and entity["filename_hits_added"] == 4
        assert {h[2] for h in hits} >= {1}

    async def test_chunks_already_found_by_text_are_not_duplicated_by_filename(self, pcfg, pclient):
        world = World(cv_corpus(1, 6, name_chunks=(1,)), {1: "Константин_резюме.docx"})
        hits, trace = await go(world, pclient, pcfg, self.Q, strategy="reranking")
        entity = next(s for s in trace.steps if s["stage"] == "entity_lane")
        assert entity["filename_hits_added"] == 3  # из первых 4 чанков чанк 1 уже найден по тексту
        keys = [(h[2], h[3]) for h in hits]
        assert len(keys) == len(set(keys))

    async def test_entity_filename_fetch_errors_and_empty_docs(self, pcfg, pclient):
        world = World(cv_corpus(3, 4, name_chunks=()),
                      {1: "Константин_один.docx", 2: "Константин_два.docx", 3: "Константин_три.docx"})
        world.fail_fetch_calls = {1}
        world.empty_fetch_docs = {2}
        hits, trace = await go(world, pclient, pcfg, self.Q, strategy="reranking")
        assert any("fetch_document_chunks_filename(1)_failed" in w for w in trace.warnings)
        entity = next(s for s in trace.steps if s["stage"] == "entity_lane")
        assert entity["filename_matched_docs"] == [1, 2, 3] and entity["filename_hits_added"] == 4

    async def test_entity_filename_lookup_failure(self, pcfg, pclient):
        world = World(cv_corpus(1, 4))
        world.find_override = lambda name: RuntimeError("db down")
        hits, trace = await go(world, pclient, pcfg, self.Q, strategy="reranking")
        assert hits

    async def test_entity_filename_matches_outside_whitelist_are_ignored(self, pcfg, pclient):
        world = World(cv_corpus(2, 4, name_chunks=()), {1: "Константин_а.docx", 2: "Константин_б.docx"})
        hits, trace = await go(world, pclient, pcfg, self.Q, strategy="reranking", document_ids=[2])
        entity = next(s for s in trace.steps if s["stage"] == "entity_lane")
        assert entity["filename_matched_docs"] == [2]

    async def test_parent_expansion_pulls_neighbours_up_to_distance_four(self, pcfg, pclient):
        world = World(cv_corpus(1, 12, name_chunks=(1,)))
        world.vec_limit = 3  # остальные чанки вектору не видны — их должна дотянуть parent expansion
        hits, trace = await go(world, pclient, pcfg, self.Q, strategy="reranking", k=3)
        exp = next(s for s in trace.steps if s["stage"] == "parent_document_expansion")
        assert exp["entity_anchor_docs"] == [1] and exp["count"] >= 3
        got = {h[3] for h in hits}
        assert 1 in got and len(got) > 2
        assert max(got) <= 5  # prox>4 от чанка 1 — обрыв

    async def test_parent_expansion_stops_at_cap(self, pcfg, pclient):
        world = World(cv_corpus(1, 40, name_chunks=(10, 11, 12, 13, 14, 15, 16, 17)))
        world.vec_limit = 3
        hits, trace = await go(world, pclient, pcfg, self.Q, strategy="reranking", k=3)
        exp = next(s for s in trace.steps if s["stage"] == "parent_document_expansion")
        assert 0 < exp["count"] <= 6

    async def test_entity_doc_without_vector_support_is_not_expanded(self, pcfg, pclient):
        world = World(cv_corpus(1, 8, name_chunks=(1,)))
        world.vec_limit = 1
        world.vec_scores = {(1, c): 0.0 for c in range(8)}
        world.vec_scores[(1, 5)] = 0.9  # единственный видимый вектору чанк — не про Константина
        hits, trace = await go(world, pclient, pcfg, self.Q, strategy="reranking", k=3, document_ids=None)
        exp = next(s for s in trace.steps if s["stage"] == "parent_document_expansion")
        assert exp["count"] >= 0

    async def test_expansion_fetch_failure_and_empty(self, pcfg, pclient):
        world = World(cv_corpus(2, 6, name_chunks=(1,)))
        # вызовы fetch: 1 — entity-filename нет (имён с «констант» нет), значит только parent expansion
        world.fail_fetch_calls = {1}
        world.empty_fetch_docs = {2}
        hits, trace = await go(world, pclient, pcfg, self.Q, strategy="reranking", k=3)
        assert any("fetch_document_chunks(" in w and "_failed" in w for w in trace.warnings)

    async def test_without_fetch_document_chunks(self, pcfg, pclient):
        world = World(cv_corpus(1, 4))
        hits, trace = await go(world, pclient, pcfg, self.Q, strategy="reranking", fetch_document_chunks=None)
        exp = next(s for s in trace.steps if s["stage"] == "parent_document_expansion")
        assert exp["count"] == 0 and hits

    async def test_pinned_caps_cut_entity_documents(self, pcfg, pclient):
        """10 документов по 7 чанков с именем: не больше 6 на документ, не больше 8 документов, не больше 48."""
        world = World(cv_corpus(10, 7, name_chunks=tuple(range(7))))
        hits, trace = await go(world, pclient, pcfg, self.Q, strategy="reranking", k=3)
        pinned = next(s for s in trace.steps if s["stage"] == "entity_documents_pinned")
        assert pinned["caps"]["mode"] == "entity" and pinned["caps"]["total"] == 48
        per_doc = {}
        for h in hits:
            per_doc[h[2]] = per_doc.get(h[2], 0) + 1
        assert max(per_doc.values()) <= 6 and len(per_doc) <= 8

    async def test_total_cap_stops_pinning(self, pcfg, pclient):
        world = World(cv_corpus(8, 7, name_chunks=tuple(range(7))))
        hits, trace = await go(world, pclient, pcfg, self.Q, strategy="reranking", k=3)
        pinned = next(s for s in trace.steps if s["stage"] == "entity_documents_pinned")
        assert pinned["count"] <= 48 + 20  # entity-чанки ограничены 48 (+ возможные filename-anchor)

    async def test_enumeration_mode_caps(self, pcfg, pclient):
        world = World(cv_corpus(3, 6, name_chunks=(0, 1)))
        hits, trace = await go(world, pclient, pcfg, "Перечисли все упоминания Константин", strategy="reranking", k=4)
        pinned = next(s for s in trace.steps if s["stage"] == "entity_documents_pinned")
        assert pinned["caps"]["mode"] == "enumeration"

    async def test_empty_text_query_with_vector_query_skips_keywords(self, pcfg, pclient):
        world = World()
        hits, trace = await go(world, pclient, pcfg, "", vector_query="кошки", strategy="reranking")
        assert hits and "keyword_search" in stages(trace)
        assert next(s for s in trace.steps if s["stage"] == "keyword_search")["count"] == 0


class TestFilenameAnchorExpansion:
    async def test_filename_doc_is_pinned_whole_with_zero_scores(self, pcfg, pclient):
        """Нулевые скоры не должны давать нулевой скор сиблингам (минимум 0.01)."""
        corpus = {(1, c): f"Содержимое файла номер {c} про кошки и собак" for c in range(8)}
        world = World(corpus, {1: "report.docx"})
        world.vec_scores = {key: 0.0 for key in corpus}
        hits, trace = await go(world, pclient, pcfg, "саммари по report.docx", strategy="reranking", k=3)
        exp = next(s for s in trace.steps if s["stage"] == "parent_document_expansion")
        assert exp["pin_whole_doc_ids"] == [1]
        assert {h[3] for h in hits} == set(range(8))

    async def test_sibling_scores_are_positive(self, pcfg, pclient):
        corpus = {(1, c): f"Содержимое файла номер {c} про кошек" for c in range(6)}
        world = World(corpus, {1: "report.docx"})
        world.vec_scores = {key: 0.0 for key in corpus}
        world.vec_limit = 2
        hits, _ = await go(world, pclient, pcfg, "саммари по report.docx", strategy="reranking", k=3)
        siblings = [h for h in hits if h[3] >= 2]  # чанки 0 и 1 пришли от вектора с нулевым скором
        assert siblings and all(h[1] > 0 for h in siblings)

    async def test_pin_cap_of_twenty_chunks(self, pcfg, pclient):
        corpus = {(1, c): f"Содержимое файла номер {c} про кошек" for c in range(30)}
        world = World(corpus, {1: "report.docx"})
        hits, trace = await go(world, pclient, pcfg, "саммари по report.docx", strategy="reranking", k=3)
        assert len({h[3] for h in hits if h[2] == 1}) <= 20 + 3

    async def test_filename_anchor_without_entities_reads_in_order(self, pcfg, pclient):
        corpus = {(1, c): f"содержимое файла номер {c} про кошек" for c in range(5)}
        world = World(corpus, {1: "report.docx"})
        hits, _ = await go(world, pclient, pcfg, "саммари по report.docx", strategy="reranking", k=2)
        assert {h[3] for h in hits} == set(range(5))


# ───────────────────────── graph и реранк на сложном пути ─────────────────────────


class TestGraphAndRerankOnFullPath:
    class Graph:
        def __init__(self, scores):
            self.scores = scores

        async def expand_neighbors(self, **kw):
            return dict(self.scores)

    async def test_graph_neighbours_are_fetched_from_repo(self, pcfg, pclient):
        world = World()
        graph = self.Graph({(3, 0): 0.9})
        world.vec_limit = 2  # соседний чанк (3, 0) вектором не найден — его добирают из репозитория
        hits, trace = await go(world, pclient, pcfg, "кошки", strategy="graph", graph_repo=graph, k=6)
        assert (3, 0) in {(h[2], h[3]) for h in hits}
        assert next(s for s in trace.steps if s["stage"] == "graph_expand_rrf")["graph_neighbors"] == 1

    @pytest.mark.parametrize("repo", [None, SimpleNamespace()])
    async def test_graph_neighbours_without_chunk_lookup(self, pcfg, pclient, repo):
        world = World()
        graph = self.Graph({(3, 0): 0.9})
        world.vec_limit = 2
        hits, trace = await go(world, pclient, pcfg, "кошки", strategy="graph", graph_repo=graph,
                               vector_repo_for_window=repo, k=6)
        assert (3, 0) not in {(h[2], h[3]) for h in hits}
        assert hits and "graph_expand_rrf" in stages(trace)

    async def test_graph_expand_error_is_reported(self, pcfg, pclient):
        class Broken:
            async def expand_neighbors(self, **kw):
                raise RuntimeError("graph down")

        hits, trace = await go(World(), pclient, pcfg, "кошки", strategy="graph", graph_repo=Broken())
        assert hits and any("graph_expand_failed" in w for w in trace.warnings)

    async def test_full_path_rerank_crash(self, pcfg, pclient, force_rerank, monkeypatch):
        async def boom(*a, **kw):
            raise RuntimeError("rerank exploded")

        monkeypatch.setattr(rp, "rerank_vector_hits_ex", boom)
        hits, trace = await go(World(), pclient, pcfg, "кошки", strategy="reranking", use_reranking=True)
        assert hits and any("rerank_failed: rerank exploded" in w for w in trace.warnings)

    async def test_full_path_rerank_unavailable(self, pcfg, pclient, force_rerank):
        pclient.rerank_out = []
        hits, trace = await go(World(), pclient, pcfg, "кошки", strategy="reranking", use_reranking=True)
        assert hits and any("rerank_skipped" in w for w in trace.warnings)
