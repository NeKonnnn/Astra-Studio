"""realtime/rag_evidence: выбор RAG-источников, сборка контекста по бюджету, гард «нет опоры в документах»."""

import logging

import pytest

from backend.rag_query.hit_types import RagHit
from backend.realtime import rag_evidence as ev

pytestmark = pytest.mark.unit


def hit(content="текст", score=0.8, doc=1, chunk=0, cosine=None):
    return RagHit(content, score, doc, chunk, cosine)


# --------------------------------------------------------------------- flags


class TestMemoryFlag:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            (None, True), ("", True), ("1", True), ("true", True), ("YES", True), ("on", True),
            ("0", False), ("false", False), ("no", False), ("off", False), ("garbage", False),
        ],
    )
    def test_flag(self, monkeypatch, raw, expected):
        if raw is None:
            monkeypatch.delenv("RAG_MEMORY_ENABLED", raising=False)
        else:
            monkeypatch.setenv("RAG_MEMORY_ENABLED", raw)
        assert ev.memory_rag_enabled() is expected


# -------------------------------------------------------------- names/labels


class TestNames:
    def test_id_to_filename_skips_broken_rows_and_falls_back_to_id(self):
        out = ev.build_rag_id_to_filename(
            [{"id": 1, "filename": " a.pdf "}, {"id": "2"}, {"id": "x"}, {"id": None}, "junk", {"id": 3, "filename": "  "}]
        )
        assert out == {1: "a.pdf", 2: "2", 3: "3"}
        assert ev.build_rag_id_to_filename(None) == {} and ev.build_rag_id_to_filename([]) == {}

    @pytest.mark.parametrize(
        "doc,expected",
        [(None, "неизвестный документ"), ("abc", "неизвестный документ"), (1, "a.pdf"), ("1", "a.pdf"), (99, "документ без имени")],
    )
    def test_label(self, doc, expected):
        assert ev.rag_document_label(doc, {1: " a.pdf "}) == expected

    def test_scoped_filenames(self):
        names = {1: "b.pdf", 2: "a.pdf", 3: "a.pdf", 4: " "}
        assert ev.rag_scoped_filenames(names) == ["a.pdf", "b.pdf"]
        assert ev.rag_scoped_filenames(names, only_ids=[1, "3", "x", None]) == ["a.pdf", "b.pdf"]
        assert ev.rag_scoped_filenames(names, only_ids=[]) == []
        assert ev.rag_scoped_filenames(names, only_ids=[2]) == ["a.pdf"]
        assert ev.rag_scoped_filenames({}, None) == []

    def test_documents_named_in_query(self):
        names = {1: "Отчёт.pdf", 2: "image.png", 3: "1.pdf", 4: "договор.docx", 5: ""}
        assert ev.documents_named_in_query("что в файле image.png?", names) == [2]
        assert ev.documents_named_in_query("покажи отчёт за год", names) == [1]
        assert ev.documents_named_in_query("найди 1 пункт", names) == []
        assert ev.documents_named_in_query("1.pdf", names) == [3]
        assert ev.documents_named_in_query("", names) == []
        assert ev.documents_named_in_query("договор и отчёт", names) == [1, 4]

    def test_documents_named_in_query_scoping_and_limit(self):
        names = {i: f"file{i}.txt" for i in range(1, 8)}
        q = " ".join(f"file{i}.txt" for i in range(1, 8))
        assert ev.documents_named_in_query(q, names) == [1, 2, 3]
        assert ev.documents_named_in_query(q, names, limit=5) == [1, 2, 3, 4, 5]
        assert ev.documents_named_in_query(q, names, limit=0) == []
        assert ev.documents_named_in_query(q, names, only_ids=[7, "x"]) == [7]
        assert ev.documents_named_in_query(q, names, only_ids=[]) == []

    def test_merge_extra_hits_keeps_order_and_removes_duplicates(self):
        primary = [hit("a", doc=1, chunk=0), hit("b", doc=1, chunk=1)]
        extra = [hit("b2", doc=1, chunk=1), hit("c", doc=2, chunk=0), hit("c-again", doc=2, chunk=0), "bad"]
        merged = ev.merge_extra_hits(primary, extra)
        assert [h[0] for h in merged] == ["a", "b", "c"]
        assert ev.merge_extra_hits(None, None) == []
        assert ev.merge_extra_hits(None, extra)[0][0] == "b2"


class TestInventory:
    def test_block_lists_sections(self, monkeypatch):
        monkeypatch.setattr("backend.rag_query.prompts.documents_inventory_enabled", lambda: True)
        out = ev.build_rag_inventory_block([("Проект", ["a.pdf", "b.pdf"]), ("Библиотека", []), ("Агент", [None, "c.txt"])])
        assert out.startswith("ДОСТУПНЫЕ ФАЙЛЫ")
        assert "Проект (2): a.pdf, b.pdf" in out and "Агент (1): c.txt" in out and "Библиотека" not in out

    def test_empty_when_nothing_to_show_or_disabled(self, monkeypatch):
        monkeypatch.setattr("backend.rag_query.prompts.documents_inventory_enabled", lambda: True)
        assert ev.build_rag_inventory_block([]) == "" and ev.build_rag_inventory_block([("x", [])]) == ""
        monkeypatch.setattr("backend.rag_query.prompts.documents_inventory_enabled", lambda: False)
        assert ev.build_rag_inventory_block([("x", ["a"])]) == ""

    def test_setting_read_failure_does_not_hide_the_inventory(self, monkeypatch):
        def broken():
            raise RuntimeError("cfg")

        monkeypatch.setattr("backend.rag_query.prompts.documents_inventory_enabled", broken)
        assert "a" in ev.build_rag_inventory_block([("x", ["a"])])


# ------------------------------------------------------------ active sources


class TestActiveSources:
    def resolve(self, **kw):
        base = dict(
            project_id=None, use_agent_scoped_kb=False, agent_kb_doc_ids=None,
            use_memory_library_rag=False, use_kb_rag=False,
        )
        base.update(kw)
        return ev.resolve_active_rag_sources(**base)

    def test_nothing_enabled(self):
        s = self.resolve()
        assert not s.any and s.store_list() == []
        assert s.as_dict() == {"project": False, "agent_kb": False, "memory": False}

    def test_each_source_independently(self):
        assert self.resolve(project_id="p").store_list() == ["project"]
        assert self.resolve(use_agent_scoped_kb=True, agent_kb_doc_ids=[1]).store_list() == ["kb"]
        assert self.resolve(use_memory_library_rag=True).store_list() == ["memory"]

    def test_agent_kb_needs_documents(self):
        assert self.resolve(use_agent_scoped_kb=True, agent_kb_doc_ids=[]).agent_kb is False
        assert self.resolve(use_agent_scoped_kb=True, agent_kb_doc_ids=None).agent_kb is False
        assert self.resolve(use_agent_scoped_kb=False, agent_kb_doc_ids=[1]).agent_kb is False

    def test_either_library_flag_enables_memory(self):
        assert self.resolve(use_kb_rag=True).memory is True
        assert self.resolve(use_memory_library_rag=True).memory is True

    def test_global_switch_overrides_the_users_toggle(self, monkeypatch):
        monkeypatch.setenv("RAG_MEMORY_ENABLED", "0")
        assert self.resolve(use_memory_library_rag=True, use_kb_rag=True).memory is False

    def test_sources_are_additive(self):
        s = self.resolve(project_id="p", use_agent_scoped_kb=True, agent_kb_doc_ids=[1], use_memory_library_rag=True)
        assert s.store_list() == ["project", "kb", "memory"] and s.any

    def test_only_one_source_never_leaks_into_the_others(self):
        s = self.resolve(project_id="p")
        assert (s.agent_kb, s.memory) == (False, False)


class TestBlocking:
    def test_status_helper(self):
        status = {"memory": {"reindexing": True}, "project": {"reindexing": True}, "kb": {"reindexing": True}}
        assert ev.should_block_rag_send(status, library_enabled=True) is True
        assert ev.should_block_rag_send(status, library_enabled=False) is False
        assert ev.should_block_rag_send(status, library_enabled=False, project_has_documents=True) is True
        assert ev.should_block_rag_send(status, library_enabled=False, agent_has_kb=True) is True
        assert ev.should_block_rag_send({}, library_enabled=True) is False
        assert ev.should_block_rag_send({"memory": {"reindexing": False}}, library_enabled=True) is False

    class Rag:
        def __init__(self, status=None, docs=None, status_error=None, docs_error=None):
            self.status, self.docs = status or {}, docs if docs is not None else []
            self.status_error, self.docs_error = status_error, docs_error

        async def get_reindex_status(self):
            if self.status_error:
                raise self.status_error
            return self.status

        async def project_rag_list_documents(self, project_id):
            if self.docs_error:
                raise self.docs_error
            return self.docs

    async def test_no_active_sources_or_client_means_no_block(self):
        none = ev.ActiveRagSources(project=False, agent_kb=False, memory=False)
        assert await ev.rag_reindex_blocks_active_sources(none, self.Rag({"memory": {"reindexing": True}})) is False
        memory = ev.ActiveRagSources(project=False, agent_kb=False, memory=True)
        assert await ev.rag_reindex_blocks_active_sources(memory, None) is False

    async def test_memory_and_kb_block_when_their_store_is_reindexing(self):
        status = {"memory": {"reindexing": True}, "kb": {"reindexing": True}}
        mem = ev.ActiveRagSources(project=False, agent_kb=False, memory=True)
        kb = ev.ActiveRagSources(project=False, agent_kb=True, memory=False)
        assert await ev.rag_reindex_blocks_active_sources(mem, self.Rag(status)) is True
        assert await ev.rag_reindex_blocks_active_sources(kb, self.Rag(status)) is True
        idle = {"memory": {"reindexing": False}}
        assert await ev.rag_reindex_blocks_active_sources(mem, self.Rag(idle)) is False

    async def test_project_blocks_only_if_it_has_documents(self):
        sources = ev.ActiveRagSources(project=True, agent_kb=False, memory=False)
        status = {"project": {"reindexing": True}}
        assert await ev.rag_reindex_blocks_active_sources(sources, self.Rag(status, docs=[{"id": 1}]), project_id="p") is True
        assert await ev.rag_reindex_blocks_active_sources(sources, self.Rag(status, docs=[]), project_id="p") is False
        assert await ev.rag_reindex_blocks_active_sources(sources, self.Rag(status, docs=[{"id": 1}]), project_id=None) is False
        broken = self.Rag(status, docs_error=RuntimeError("x"))
        assert await ev.rag_reindex_blocks_active_sources(sources, broken, project_id="p") is False

    async def test_status_failure_never_blocks(self):
        sources = ev.ActiveRagSources(project=True, agent_kb=True, memory=True)
        down = self.Rag(status_error=RuntimeError("down"))
        assert await ev.rag_reindex_blocks_active_sources(sources, down, project_id="p") is False


class TestStatusMessage:
    def test_nothing_reindexing_gives_empty_text(self):
        assert ev.build_reindex_status_message(memory_reindexing=False, project_reindexing=False, kb_reindexing=False) == ""

    @pytest.mark.parametrize(
        "flags,expected",
        [
            ((True, False, False), "Библиотеки"),
            ((False, True, False), "документов проекта"),
            ((False, False, True), "базы знаний агента"),
            ((True, True, False), "Библиотеки и документов проекта"),
            ((True, True, True), "Библиотеки, документов проекта и базы знаний агента"),
        ],
    )
    def test_store_names(self, flags, expected):
        msg = ev.build_reindex_status_message(memory_reindexing=flags[0], project_reindexing=flags[1], kb_reindexing=flags[2])
        assert f"Идёт перечанковка {expected}." in msg and "временно недоступен" in msg

    def test_named_entities_replace_store_names(self):
        active = [
            {"scope": "agent", "name": "Юрист", "entity_id": "5"},
            {"scope": "project", "name": "", "entity_id": "p1"},
            {"scope": "agent", "name": "Юрист"},
            "junk",
        ]
        msg = ev.build_reindex_status_message(memory_reindexing=False, project_reindexing=False, kb_reindexing=True, active=active)
        assert msg.startswith("Идёт пересборка агента «Юрист» и проекта p1.")

    def test_single_named_entity(self):
        msg = ev.build_reindex_status_message(
            memory_reindexing=False, project_reindexing=True, kb_reindexing=False,
            active=[{"scope": "project", "name": "Отчётность"}],
        )
        assert "проекта «Отчётность»" in msg and "перечанковка" not in msg

    def test_memory_reindex_ignores_entity_names(self):
        msg = ev.build_reindex_status_message(
            memory_reindexing=True, project_reindexing=False, kb_reindexing=False,
            active=[{"scope": "agent", "name": "X"}],
        )
        assert msg.startswith("Идёт перечанковка Библиотеки")


# ------------------------------------------------------------- guard settings


class TestGuardEnv:
    def test_defaults(self, monkeypatch):
        monkeypatch.setattr("backend.services.user_rag_settings.runtime_rag_similarity_threshold", lambda scope=None: 0.2)
        monkeypatch.delenv("RAG_BLOCK_ON_NO_EVIDENCE", raising=False)
        assert ev.rag_guard_env("agent") == (0.2, True)

    @pytest.mark.parametrize("raw", ["0", "false", "No", "OFF"])
    def test_block_can_be_switched_off(self, monkeypatch, raw):
        monkeypatch.setattr("backend.services.user_rag_settings.runtime_rag_similarity_threshold", lambda scope=None: 0)
        monkeypatch.setenv("RAG_BLOCK_ON_NO_EVIDENCE", raw)
        assert ev.rag_guard_env()[1] is False

    def test_threshold_is_clamped(self, monkeypatch):
        monkeypatch.setattr("backend.services.user_rag_settings.runtime_rag_similarity_threshold", lambda scope=None: 5)
        assert ev.rag_guard_env()[0] == 1.0
        monkeypatch.setattr("backend.services.user_rag_settings.runtime_rag_similarity_threshold", lambda scope=None: -3)
        assert ev.rag_guard_env()[0] == 0.0

    def test_falls_back_to_app_state_then_env(self, monkeypatch):
        def broken(scope=None):
            raise RuntimeError("no user")

        monkeypatch.setattr("backend.services.user_rag_settings.runtime_rag_similarity_threshold", broken)
        import backend.app_state as state

        monkeypatch.setattr(state, "rag_similarity_threshold", 0.4, raising=False)
        assert ev.rag_guard_env()[0] == 0.4
        monkeypatch.setattr(state, "rag_similarity_threshold", "not a number", raising=False)
        monkeypatch.setenv("RAG_MIN_SIMILARITY", "0.15")
        assert ev.rag_guard_env()[0] == 0.15
        monkeypatch.setenv("RAG_MIN_SIMILARITY", "oops")
        assert ev.rag_guard_env()[0] == 0.0


class TestScoreFilter:
    HITS = [("a", 0.9, 1, 0), ("b", 0.5, 1, 1), ("c", 0.2, 2, 0)]

    def test_no_threshold_keeps_everything(self):
        assert ev.filter_rag_hits_by_score(self.HITS, 0.0) == self.HITS
        assert ev.filter_rag_hits_by_score(None, 0.5) == [] and ev.filter_rag_hits_by_score([], 0.5) == []

    def test_threshold_cuts_low_scores(self):
        assert [h[0] for h in ev.filter_rag_hits_by_score(self.HITS, 0.5)] == ["a", "b"]

    def test_nothing_passes_but_scores_look_like_cosine_rescues_top_chunks(self, monkeypatch):
        monkeypatch.delenv("RAG_RESCUE_LOW_SCORE_TOP", raising=False)
        hits = [(str(i), 0.01 * i, 1, i) for i in range(1, 8)]
        out = ev.filter_rag_hits_by_score(hits, 0.9)
        assert len(out) == 7 and out[0][0] == "7"
        monkeypatch.setenv("RAG_RESCUE_LOW_SCORE_TOP", "2")
        assert len(ev.filter_rag_hits_by_score(hits, 0.9)) == 4
        monkeypatch.setenv("RAG_RESCUE_LOW_SCORE_TOP", "100")
        many = [(str(i), 0.001, 1, i) for i in range(40)]
        assert len(ev.filter_rag_hits_by_score(many, 0.9)) == 24
        monkeypatch.setenv("RAG_RESCUE_LOW_SCORE_TOP", "x")
        assert len(ev.filter_rag_hits_by_score(hits, 0.9)) == 7

    def test_negative_scores_look_like_reranker_logits_and_are_kept(self):
        hits = [("a", -2.0, 1, 0), ("b", -5.0, 1, 1)]
        assert ev.filter_rag_hits_by_score(hits, 0.5) == hits

    def test_junk_rows_are_ignored(self):
        assert ev.filter_rag_hits_by_score([("a", "x", 1, 0), ("b", 0.7, 1, 1)], 0.5) == [("b", 0.7, 1, 1)]


# ------------------------------------------------------------------ formatting


class TestFragmentSource:
    @pytest.mark.parametrize(
        "title,expected",
        [
            ("scan.PNG", "текст, распознанный на изображении «scan.PNG»"),
            ("a.pdf", "документ «a.pdf»"),
            ("photo.webp", "текст, распознанный на изображении «photo.webp»"),
        ],
    )
    def test_source(self, title, expected):
        assert ev.rag_fragment_source(title) == expected


class TestFormatFragments:
    NAMES = {1: "a.pdf", 2: "b.png"}

    def test_empty(self):
        parts, metrics = ev.format_rag_fragments([], self.NAMES, max_chars=1000, store_label="kb")
        assert parts == [] and metrics["received"] == 0
        assert ev.format_rag_fragments(None, self.NAMES, max_chars=10, store_label="kb")[0] == []

    def test_plain_format_without_chunk_meta(self):
        parts, _ = ev.format_rag_fragments([hit("Содержимое", doc=1)], self.NAMES, max_chars=1000, store_label="kb")
        assert parts == ["Фрагмент 1 (документ «a.pdf»): Содержимое\n"]

    def test_chunk_meta_adds_chunk_and_relevance(self):
        parts, _ = ev.format_rag_fragments(
            [hit("T", score=0.5, doc=2, chunk=7, cosine=0.42)], self.NAMES, max_chars=1000, store_label="kb", include_chunk_meta=True
        )
        assert "текст, распознанный на изображении «b.png»" in parts[0]
        assert "чанк 7" in parts[0] and "релевантность: 42%" in parts[0]

    def test_relevance_falls_back_to_score_without_cosine(self):
        parts, _ = ev.format_rag_fragments(
            [hit("T", score=0.5, doc=1)], self.NAMES, max_chars=1000, store_label="kb", include_chunk_meta=True
        )
        assert "релевантность: 50%" in parts[0]

    def test_round_robin_guarantees_every_document_a_fragment(self):
        big = "я" * 300
        hits = [hit(big, doc=1, chunk=0), hit(big, doc=1, chunk=1), hit(big, doc=1, chunk=2), hit("маленький", doc=2, chunk=0)]
        parts, metrics = ev.format_rag_fragments(hits, self.NAMES, max_chars=700, store_label="kb")
        assert metrics["documents_in_prompt"] == 2 and any("b.png" in p for p in parts)
        assert metrics["received"] == 4

    def test_numbering_follows_the_original_order(self):
        hits = [hit("a", doc=1, chunk=0), hit("b", doc=2, chunk=0), hit("c", doc=1, chunk=1)]
        parts, _ = ev.format_rag_fragments(hits, self.NAMES, max_chars=10_000, store_label="kb")
        assert [p.split(" ")[1] for p in parts] == ["1", "2", "3"]

    def test_budget_is_respected_and_last_fragment_is_truncated(self):
        hits = [hit("я" * 200, doc=1, chunk=i) for i in range(5)]
        parts, metrics = ev.format_rag_fragments(hits, self.NAMES, max_chars=500, store_label="kb")
        assert metrics["truncated_last"] == 1 and parts[-1].endswith("... [обрезано]\n")
        assert metrics["total_chars"] <= 500 and metrics["dropped"] >= 1
        assert metrics["used_full"] == len(parts) - 1

    def test_custom_truncate_marker(self):
        hits = [hit("я" * 400, doc=1, chunk=0), hit("я" * 400, doc=1, chunk=1)]
        parts, _ = ev.format_rag_fragments(hits, self.NAMES, max_chars=300, store_label="kb", truncate_marker="…")
        assert parts[-1].endswith("…")

    def test_malformed_row_before_a_valid_one_crashes_known_issue(self):
        """Известная проблема: номер фрагмента берётся по позиции в исходном списке, а массив
        выбранных — по числу валидных строк. Строка не из 4 элементов в начале списка даёт
        ``IndexError`` (ответ чата не строится), хотя код явно собирался такие строки пропускать."""
        with pytest.raises(IndexError):
            ev.format_rag_fragments([("only-two", 1.0), hit("ok", doc=1)], self.NAMES, max_chars=1000, store_label="kb")

    def test_malformed_row_after_valid_ones_is_skipped(self):
        parts, metrics = ev.format_rag_fragments([hit("ok", doc=1), ("only-two", 1.0)], self.NAMES, max_chars=1000, store_label="kb")
        assert len(parts) == 1 and metrics["received"] == 2

    def test_debug_logging_path(self, caplog):
        with caplog.at_level(logging.DEBUG, logger=ev.logger.name):
            ev.format_rag_fragments(
                [hit("Первый фрагмент для лога", doc=1, cosine=0.3), hit("я" * 500, doc=2, chunk=1)],
                self.NAMES, max_chars=300, store_label="kb",
            )
        assert any("RAG/fragments" in r.getMessage() for r in caplog.records)


class TestSearchTrace:
    def test_none_for_empty_or_unattributed_hits(self):
        assert ev.hits_to_document_search_trace(query="q", hits=[], id_to_name={}) is None
        assert ev.hits_to_document_search_trace(query="q", hits=[("x", 1.0, None, 0)], id_to_name={}) is None
        assert ev.hits_to_document_search_trace(query="q", hits=[("bad",)], id_to_name={}) is None

    def test_builds_card_with_files_and_relevance(self):
        trace = ev.hits_to_document_search_trace(
            query="вопрос",
            hits=[hit("Текст", doc=1, chunk=3, cosine=0.9), hit("Ещё", doc=7, chunk=0, cosine=0.5)],
            id_to_name={1: "a.pdf"},
            store="memory",
            strategy="hybrid",
        )
        assert trace["query"] == "вопрос" and trace["strategy"] == "hybrid"
        assert trace["sourceFiles"] == ["a.pdf", "doc*7"]
        first = trace["hits"][0]
        assert first["file"] == "a.pdf" and first["anchor"] == "chunk@3(a.pdf)" and first["relevance"] == 90
        assert first["store"] == "memory" and first["documentId"] == 1

    def test_content_is_capped(self):
        trace = ev.hits_to_document_search_trace(query="q", hits=[hit("я" * 20_000, doc=1)], id_to_name={1: "a"})
        assert len(trace["hits"][0]["content"]) == 12_000

    def test_merge_traces(self):
        a = {"query": "q1", "strategy": "vector", "sourceFiles": ["a.pdf"], "hits": [{"file": "a.pdf"}]}
        b = {"query": "q2", "strategy": "hybrid", "sourceFiles": ["b.pdf", ""], "hits": [{"file": "c.pdf"}, "junk"]}
        merged = ev.merge_document_search_traces(a, None, b, "junk")
        assert merged["query"] == "q1" and merged["strategy"] == "hybrid"
        assert merged["sourceFiles"] == ["a.pdf", "b.pdf", "c.pdf"] and len(merged["hits"]) == 2

    def test_merge_of_nothing_is_none(self):
        assert ev.merge_document_search_traces() is None
        assert ev.merge_document_search_traces(None, {"hits": []}) is None
        assert ev.merge_document_search_traces({"sourceFiles": ["x"]})["sourceFiles"] == ["x"]


# ----------------------------------------------------------------- counts/guard


class CountingRag:
    def __init__(self, project=None, kb=None, memory=None, errors=()):
        self.project, self.kb, self.memory, self.errors = project, kb, memory, set(errors)

    async def project_rag_list_documents(self, project_id):
        if "project" in self.errors:
            raise RuntimeError("x")
        return self.project

    async def kb_list_documents(self):
        if "kb" in self.errors:
            raise RuntimeError("x")
        return self.kb

    async def memory_rag_list_documents(self):
        if "memory" in self.errors:
            raise RuntimeError("x")
        return self.memory


class TestStoreCounts:
    async def count(self, rag, **kw):
        base = dict(project_id=None, use_kb_rag=False, use_agent_scoped_kb=False, agent_kb_doc_ids=None, use_memory_library_rag=False)
        base.update(kw)
        return await ev.fetch_rag_store_counts(rag, **base)

    async def test_no_client_or_no_sources(self):
        zero = {"project": 0, "kb": 0, "memory": 0, "agent_kb": 0}
        assert await self.count(None, project_id="p") == zero
        assert await self.count(CountingRag(project=[1])) == zero

    async def test_counts_each_enabled_store(self):
        rag = CountingRag(project=[{}, {}], kb=[{"id": 1}, {"id": 2}, {"id": 3}, {"id": "x"}], memory=[{}])
        out = await self.count(rag, project_id="p", use_agent_scoped_kb=True, agent_kb_doc_ids=[1, "3", "abc"], use_memory_library_rag=True)
        assert out == {"project": 2, "kb": 4, "memory": 1, "agent_kb": 2}

    async def test_non_list_answers_count_as_zero_and_errors_are_absorbed(self):
        zero = {"project": 0, "kb": 0, "memory": 0, "agent_kb": 0}
        rag = CountingRag(project="x", kb={"a": 1}, memory=None)
        out = await self.count(rag, project_id="p", use_agent_scoped_kb=True, agent_kb_doc_ids=[1], use_memory_library_rag=True)
        assert out == zero
        broken = CountingRag(errors={"project", "kb", "memory"})
        out = await self.count(broken, project_id="p", use_agent_scoped_kb=True, use_memory_library_rag=True)
        assert out == zero


class TestNoEvidenceGuard:
    async def guard(self, rag, **kw):
        base = dict(
            block_when_no_evidence=True, context_added=False, project_id=None, use_kb_rag=False,
            use_memory_library_rag=False, use_agent_scoped_kb=False, agent_kb_doc_ids=None,
        )
        base.update(kw)
        return await ev.maybe_rag_no_evidence_message(rag, **base)

    async def test_blocks_when_corpus_is_not_empty_but_context_is(self):
        rag = CountingRag(memory=[{"id": 1}])
        assert await self.guard(rag, use_memory_library_rag=True) == ev.RAG_NO_RELEVANT_CONTEXT_MESSAGE

    async def test_project_and_agent_corpora_also_count(self):
        assert await self.guard(CountingRag(project=[{}]), project_id="p") == ev.RAG_NO_RELEVANT_CONTEXT_MESSAGE
        rag = CountingRag(kb=[{"id": 4}])
        assert await self.guard(rag, use_agent_scoped_kb=True, agent_kb_doc_ids=[4]) == ev.RAG_NO_RELEVANT_CONTEXT_MESSAGE

    async def test_no_block_when_corpus_is_empty(self):
        assert await self.guard(CountingRag(memory=[]), use_memory_library_rag=True) is None
        assert await self.guard(CountingRag(kb=[{"id": 9}]), use_agent_scoped_kb=True, agent_kb_doc_ids=[4]) is None

    async def test_no_block_when_disabled_or_context_exists_or_no_client(self):
        rag = CountingRag(memory=[{"id": 1}])
        assert await self.guard(rag, use_memory_library_rag=True, block_when_no_evidence=False) is None
        assert await self.guard(rag, use_memory_library_rag=True, context_added=True) is None
        assert await self.guard(None, use_memory_library_rag=True) is None

    async def test_history_leaves_the_decision_to_the_model(self):
        rag = CountingRag(memory=[{"id": 1}])
        assert await self.guard(rag, use_memory_library_rag=True, has_history=True) is None

    async def test_kb_toggle_alone_does_not_count_the_library(self):
        rag = CountingRag(memory=[{"id": 1}])
        assert await self.guard(rag, use_kb_rag=True) is None
