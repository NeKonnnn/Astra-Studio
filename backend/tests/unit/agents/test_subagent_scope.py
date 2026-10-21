"""agents/subagents/scope: «смотри только в файл…» в карточке субагента → ограничение поиска по документам."""

from types import SimpleNamespace

import pytest

from backend.agents.subagents import scope

pytestmark = pytest.mark.unit

NAMES = {
    1: "Проект ДС № 14.docx",
    2: "Заявка на БГ.docx",
    3: "ВНД_Регламент НЭР.docx",
    4: "Методика №ВНД-381-1.docx",
    5: "Прочий файл 14.docx",
}


def ids(card, names=NAMES):
    result = scope._card_file_scope(card, names)
    return None if result is None else result["ids"]


class TestCardFileScope:
    def test_single_file_by_name(self):
        result = scope._card_file_scope("Ты аналитик. Смотри только в файл Заявка на БГ.docx", NAMES)

        assert result == {"ids": [2], "missing": [], "reasons": ["Ты аналитик. Смотри только в файл Заявка на БГ.docx"], "rule": "имя файла"}

    def test_several_files_by_name_and_tail_of_a_name_does_not_pull_similar_files(self):
        # «14.docx» — хвост имени «Проект ДС № 14.docx»; «Прочий файл 14.docx» не должен зацепиться.
        assert ids("Отвечай только по файлам Проект ДС № 14.docx и Заявка на БГ.docx") == [1, 2]

    def test_name_without_extension(self):
        assert ids("Смотри только Заявка на БГ") == [2]

    def test_short_names_need_the_extension(self):
        assert ids("Смотри только в a", {1: "a.docx"}) is None

    def test_case_and_yo_are_ignored(self):
        assert ids("ТОЛЬКО ЁЛКА.DOCX", {1: "елка.docx"}) == [1]

    def test_name_with_a_missing_extension_is_reported(self):
        result = scope._card_file_scope("Смотри только в 999.docx", NAMES)

        assert result["ids"] == [] and result["missing"] == ["999.docx"] and result["rule"] == "имя файла"

    def test_file_tail_from_the_text_matches_the_end_of_a_name(self):
        assert ids("Смотри только НЭР.docx") == [3]

    @pytest.mark.parametrize(
        ("card", "expected"),
        [
            ("Смотри только про ВНД", [3, 4]),
            ("Смотри только в документах БГ", [2]),
            ("Используй только файлы БГ и ВНД", [2, 3, 4]),
            ("только РСХА, ПТА, БГ, ВНД", [2, 3, 4]),
            ("только в документах: ВНД, СФКА", [3, 4]),
            ("Смотри ТОЛЬКО ВНД, остальное не смотри", [3, 4]),
        ],
    )
    def test_abbreviation_lists(self, card, expected):
        assert ids(card) == expected

    def test_abbreviation_rule_name(self):
        assert scope._card_file_scope("Смотри только про ВНД", NAMES)["rule"] == "аббревиатура"

    def test_both_rules_are_reported(self):
        result = scope._card_file_scope("Смотри только Заявка на БГ.docx\nИ ещё только про ВНД", NAMES)

        assert result["ids"] == [2, 3, 4] and result["rule"] == "имя файла + аббревиатура"

    @pytest.mark.parametrize(
        "card",
        [
            "Ты помощник",
            "",
            None,
            "Используй только утверждённые решения",  # обычные слова не задают файл
            "Отвечай только в рублях РФ",  # РФ нет ни в одном имени файла
            "Справочники ВНД, СФКА. Применяются ТОЛЬКО для сверки параметров.",  # аббревиатуры до «только»
            "Ответ только если уверен: ВНД важнее",  # после «только» идёт обычное слово
            "Смотри ВНД и БГ",  # нет слова «только»
        ],
    )
    def test_no_restriction(self, card):
        assert ids(card) is None

    def test_file_names_inside_the_line_are_not_re_read_as_abbreviations(self):
        # «БГ» — часть имени «Заявка на БГ.docx», а не указание «все файлы про БГ».
        assert ids("Смотри только Заявка на БГ.docx", {**NAMES, 6: "БГ_реестр.xlsx"}) == [2]

    def test_files_listed_below_the_instruction(self):
        card = "Смотри только в эти файлы:\n- Проект ДС № 14.docx\n- Заявка на БГ.docx\n\n- Прочий файл 14.docx"

        assert ids(card) == [1, 2]  # пустая строка обрывает столбик

    def test_a_rule_list_does_not_become_a_file_list(self):
        card = "- Используй только факты из документов\n- Не выдумывай\n- Суть бери из Заявка на БГ.docx"

        assert ids(card) is None

    def test_missing_file_in_a_column_counts_only_when_the_line_is_that_name(self):
        assert scope._card_file_scope("Смотри только в файлы:\n- Заявка на БГ.docx\n- 555.docx", NAMES)["missing"] == ["555.docx"]
        assert scope._card_file_scope("Смотри только в файлы:\n- Заявка на БГ.docx\n- Прочитай 555.docx внимательно", NAMES)["missing"] == []

    def test_reasons_are_trimmed(self):
        result = scope._card_file_scope("Смотри только Заявка на БГ.docx " + "я" * 400, NAMES)

        assert len(result["reasons"][0]) <= 160

    def test_empty_knowledge_base(self):
        assert scope._card_file_scope("Смотри только в 1.docx", {})["missing"] == ["1.docx"]
        assert ids("Смотри только про ВНД", {}) is None

    def test_garbage_names_are_ignored(self):
        assert ids("Смотри только Заявка.docx", {1: "Заявка.docx", 2: "", 3: None}) == [1]

    def test_numbers_and_dates_are_not_taken_for_files(self):
        assert scope._card_file_scope("Смотри только данные на 01.04.2026 и 3.2.", NAMES) is None

    def test_regexes(self):
        assert scope._SCOPE_RESTRICT_RE.search("ТОЛЬКО") and scope._SCOPE_RESTRICT_RE.search("лишь") and not scope._SCOPE_RESTRICT_RE.search("несколько")
        assert scope._SCOPE_ABBR_RE.findall("ВНД и СФКА, БГ, ab, А, ПОЛНОСТЬЮЗАГЛАВНОЕ") == ["ВНД", "СФКА", "БГ"]
        assert scope._scope_norm("  ЁЖик  ") == "ежик" and scope._scope_strip_ext("a.b.DOCX") == "a.b"


class TestResolveCardScope:
    @pytest.fixture
    def kb(self, monkeypatch):
        import backend.app_state as app_state
        import backend.realtime.rag_evidence as evidence

        state = SimpleNamespace(docs=[{"id": i, "filename": n} for i, n in NAMES.items()], error=None, calls=0)

        async def list_documents():
            state.calls += 1
            if state.error:
                raise state.error
            return state.docs

        monkeypatch.setattr(app_state, "rag_client", SimpleNamespace(kb_list_documents=list_documents), raising=False)
        monkeypatch.setattr(evidence, "build_rag_id_to_filename", lambda docs: {d["id"]: d["filename"] for d in docs})
        return state

    async def test_scope_is_stored_in_the_child_profile(self, kb):
        profile = {"name": "Юрист", "system_prompt": "Смотри только Заявка на БГ.docx", "kb_document_ids": [2, 3]}

        result = await scope._resolve_card_scope(profile, [1], 7)

        assert result == [2] and profile["_scope_doc_ids"] == [2]
        assert profile["_scope_info"] == {"files": ["Заявка на БГ.docx"], "missing": [], "rule": "имя файла"}

    async def test_only_available_documents_are_considered(self, kb):
        # Файла 2 нет ни в общей базе главного, ни в своей базе ребёнка — ограничить нечем.
        profile = {"system_prompt": "Смотри только Заявка на БГ.docx", "kb_document_ids": [3]}

        result = await scope._resolve_card_scope(profile, [1], 7)

        # Файла 2 среди доступных нет: просили только его, поэтому область пустая (искать нечего), а файл числится «не найден».
        assert result == [] and profile["_scope_info"]["files"] == [] and profile["_scope_info"]["missing"] == ["БГ.docx"]

    async def test_ids_are_coerced_and_deduplicated(self, kb):
        profile = {"system_prompt": "Смотри только про ВНД", "kb_document_ids": ["3", "x", None, 4]}

        assert await scope._resolve_card_scope(profile, ["3", 3], 7) == [3, 4]

    async def test_no_restriction_words_means_no_network_call(self, kb):
        assert await scope._resolve_card_scope({"system_prompt": "Ты помощник"}, [1], 7) is None
        assert kb.calls == 0

    async def test_no_available_documents(self, kb):
        assert await scope._resolve_card_scope({"system_prompt": "Смотри только про ВНД"}, [], 7) is None
        assert kb.calls == 0

    async def test_kb_failure_falls_back_to_no_restriction(self, kb):
        kb.error = RuntimeError("rag down")
        profile = {"system_prompt": "Смотри только про ВНД", "kb_document_ids": [3]}

        assert await scope._resolve_card_scope(profile, [], 7) is None
        assert "_scope_doc_ids" not in profile

    async def test_no_rag_client(self, monkeypatch):
        import backend.app_state as app_state

        monkeypatch.setattr(app_state, "rag_client", None, raising=False)

        assert await scope._resolve_card_scope({"system_prompt": "Смотри только про ВНД", "kb_document_ids": [3]}, [], 7) is None


class TestScopeNote:
    def test_no_scope_no_note(self):
        assert scope._card_scope_note({}) == "" and scope._card_scope_note({"_scope_info": None}) == ""

    def test_files_and_missing(self):
        note = scope._card_scope_note({"_scope_info": {"files": ["a.docx", "b.docx"], "missing": ["c.docx"]}})

        assert note == "Поиск по документам ограничен файлами из твоей инструкции: a.docx, b.docx. Не найдены в базе: c.docx."

    def test_nothing_was_found(self):
        note = scope._card_scope_note({"_scope_info": {"files": [], "missing": ["c.docx"]}})

        assert "не найдены: c.docx" in note and "Поиск по документам не выполнялся" in note
        assert "не найдены: -" in scope._card_scope_note({"_scope_info": {"files": [], "missing": [], "rule": "x"}})


class TestPullNamedDocuments:
    @pytest.fixture
    def patched(self, monkeypatch):
        import backend.realtime.rag_evidence as evidence

        state = SimpleNamespace(named=[], search_error=None, searches=[])

        def named(query, id_to_name, only_ids=None):
            state.named_args = (query, id_to_name, only_ids)
            return list(state.named)

        async def search(rag_client, query, doc_ids, k=None, strategy=None):
            state.searches.append((query, doc_ids, k, strategy))
            if state.search_error:
                raise state.search_error
            return [f"hit-{doc_ids[0]}"]

        monkeypatch.setattr(evidence, "documents_named_in_query", named)
        monkeypatch.setattr(evidence, "merge_extra_hits", lambda hits, extra: [*hits, *extra])
        monkeypatch.setattr(scope, "kb_search_agent_documents", search)
        return state

    async def test_named_files_are_pulled_and_recorded(self, patched):
        patched.named = [2, 3]
        profile = {}

        hits = await scope._pull_named_documents(object(), "что в Заявке?", ["base"], NAMES, [2, 3], 5, profile, strategy="hybrid")

        assert hits == ["base", "hit-2", "hit-3"] and profile["_named_files"] == ["Заявка на БГ.docx (+1)", "ВНД_Регламент НЭР.docx (+1)"]
        assert patched.searches[0] == ("что в Заявке?", [2], 5, "hybrid") and patched.named_args[2] == [2, 3]

    async def test_nothing_named_nothing_added(self, patched):
        assert await scope._pull_named_documents(object(), "q", ["base"], NAMES, [1], 5, {}) == ["base"]
        assert patched.searches == []

    async def test_one_failing_search_does_not_stop_the_others(self, patched, monkeypatch):
        patched.named = [2, 3]
        calls = []

        async def flaky(rag_client, query, doc_ids, k=None, strategy=None):
            calls.append(doc_ids[0])
            if doc_ids[0] == 2:
                raise RuntimeError("boom")
            return ["hit-3"]

        monkeypatch.setattr(scope, "kb_search_agent_documents", flaky)

        hits = await scope._pull_named_documents(object(), "q", [], NAMES, [2, 3], 5, None)

        assert hits == ["hit-3"] and calls == [2, 3]

    async def test_lookup_errors_return_the_hits_unchanged(self, monkeypatch):
        import backend.realtime.rag_evidence as evidence

        def boom(*args, **kwargs):
            raise RuntimeError("bad names")

        monkeypatch.setattr(evidence, "documents_named_in_query", boom)

        assert await scope._pull_named_documents(object(), "q", ["base"], NAMES, [1], 5, {}) == ["base"]

    async def test_unknown_id_name_falls_back_to_the_id(self, patched):
        patched.named = [42]
        profile = {}

        await scope._pull_named_documents(object(), "q", [], {}, [42], 5, profile)

        assert profile["_named_files"] == ["42 (+1)"]
