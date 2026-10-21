"""services/tag_mentions, inline_context, inline_images, follow_up_suggestions: разбор mentions, вложения, подсказки."""

import base64
from types import SimpleNamespace

import pytest

from backend.services import follow_up_suggestions as fu
from backend.services import inline_context as ic
from backend.services import inline_images as ii
from backend.services import tag_mentions as tm

pytestmark = pytest.mark.unit


class TestTagMentions:
    def test_ids_from_text(self):
        assert tm.extract_tag_ids_from_text("<#3|Юристы> и <#7> снова <#3|Юристы> и <#x|bad> и <#0> <#-2|neg>") == [3, 7]
        assert tm.extract_tag_ids_from_text("") == [] and tm.extract_tag_ids_from_text(None) == []
        assert tm.extract_tag_ids_from_text("обычный #3 текст") == []

    def test_ids_from_messages_with_mixed_content_shapes(self):
        messages = [
            {"role": "user", "content": "<#1|A>"},
            {"role": "user", "content": [{"type": "text", "text": "<#2|B>"}, {"type": "image_url", "image_url": "<#9>"}, "str"]},
            "<#3|C> просто строка",
            {"role": "user", "content": None},
            {"role": "user", "content": "<#1|A> <#4>"},
        ]

        assert tm.extract_tag_ids_from_messages(messages) == [1, 2, 3, 4]
        assert tm.extract_tag_ids_from_messages(None) == []

    def test_mentions_are_stripped(self):
        assert tm.strip_tag_mentions("  <#3|Юристы> проверь договор <#4|Риски>  ") == "проверь договор"
        assert tm.strip_tag_mentions("") == "" and tm.strip_tag_mentions(None) is None

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [(None, []), ([], []), ("5", [5]), (7, [7]), ([1, "2", "2", 0, -1, "x", None, 3.0], [1, 2, 3]), ("abc", []), (0, [])],
    )
    def test_id_list_normalization(self, raw, expected):
        assert tm.normalize_tag_id_list(raw) == expected

    def test_payload_and_text_are_merged_without_duplicates(self):
        assert tm.collect_mention_tag_ids(user_message="<#2|B> <#5|C>", data={"tag_ids": [5, "1", 2]}) == [5, 1, 2]
        assert tm.collect_mention_tag_ids() == [] and tm.collect_mention_tag_ids(data="junk", user_message="<#4>") == [4]


@pytest.fixture
def plugin_tools(monkeypatch):
    import backend.plugins.tools as tools

    state = SimpleNamespace(owned_names=["model.xlsx"], calls=[])

    def owned(ids, names):
        state.calls.append((list(ids), list(names)))
        return [n for n in names if n in state.owned_names]

    monkeypatch.setattr(tools, "plugin_owned_attachment_names", owned)
    monkeypatch.setattr(tools, "plugin_attachment_prompt_note", lambda ids, names: f"[файл {', '.join(names)} обработает плагин {ids[0]}]")
    return state


class TestInlineContext:
    def test_attachment_names(self):
        assert ic.attachment_names([{"name": " a.txt "}, "b.txt", {"name": ""}, None, {"x": 1}, 5]) == ["a.txt", "b.txt", "5"]
        assert ic.attachment_names(None) == []

    def test_marker_positions_are_found_in_order(self):
        text = "[a.txt]\nодин\n\n[b.txt]\nдва"

        assert ic._marker_positions(text, ["a.txt", "b.txt", "ghost"]) == [("a.txt", 0), ("b.txt", text.index("[b.txt]"))]

    def test_same_name_twice_uses_the_second_marker(self):
        text = "[a.txt]\nодин\n\n[a.txt]\nдва"

        assert [p for _, p in ic._marker_positions(text, ["a.txt", "a.txt"])] == [0, text.rindex("[a.txt]")]

    def test_plugin_owned_file_is_replaced_by_a_note(self, plugin_tools):
        text = "[notes.txt]\nзаметки\n\n[model.xlsx]\nЯчейка A1 | 5 | 10\n"

        out, owned = ic.strip_plugin_owned_attachments(text, plugin_ids=["cash-flow"], attachments=[{"name": "notes.txt"}, {"name": "model.xlsx"}])

        assert owned == ["model.xlsx"] and out == "[notes.txt]\nзаметки\n\n[файл model.xlsx обработает плагин cash-flow]"
        assert "Ячейка" not in out

    def test_unknown_markup_is_replaced_entirely(self, plugin_tools):
        out, owned = ic.strip_plugin_owned_attachments("дамп без маркеров", plugin_ids=["cash-flow"], attachments=["model.xlsx"])

        assert out == "[файл model.xlsx обработает плагин cash-flow]" and owned == ["model.xlsx"]

    def test_note_override(self, plugin_tools):
        out, _ = ic.strip_plugin_owned_attachments("[model.xlsx]\nx", plugin_ids=["p"], attachments=["model.xlsx"], note_override="  плагин уже выполнен  ")

        assert out == "плагин уже выполнен"

    @pytest.mark.parametrize(
        ("text", "plugin_ids", "attachments"),
        [("", ["p"], ["model.xlsx"]), ("   ", ["p"], ["model.xlsx"]), ("[a.txt]\nx", None, ["a.txt"]), ("[a.txt]\nx", [" ", ""], ["a.txt"]), ("[a.txt]\nx", ["p"], ["a.txt"])],
    )
    def test_nothing_to_cut(self, plugin_tools, text, plugin_ids, attachments):
        assert ic.strip_plugin_owned_attachments(text, plugin_ids=plugin_ids, attachments=attachments) == (text, [])

    def test_prepare_returns_only_the_text(self, plugin_tools):
        assert ic.prepare_inline_context("[model.xlsx]\nx", plugin_ids=["p"], attachments=["model.xlsx"], label="ws") == "[файл model.xlsx обработает плагин p]"
        assert ic.prepare_inline_context(None) == ""

    def test_the_full_attachment_text_is_otherwise_kept_without_a_size_limit(self, plugin_tools):
        big = "[a.txt]\n" + "я" * 500_000

        assert ic.prepare_inline_context(big, plugin_ids=["p"], attachments=["a.txt"]) == big


class TestInlineImages:
    def test_refs_from_metadata(self):
        meta = {"inline_attachments": [
            {"contentType": "image", "minio_object": "o1", "minio_bucket": "b", "name": " фото "},
            {"content_type": "image", "minio_object": "o2", "minio_bucket": "b"},
            {"contentType": "text", "minio_object": "o3", "minio_bucket": "b"},
            {"contentType": "image", "minio_object": "", "minio_bucket": "b"},
            {"contentType": "image", "minio_object": "o4"},
            "junk",
        ]}

        assert ii.image_refs_from_metadata(meta) == [
            {"minio_object": "o1", "minio_bucket": "b", "name": "фото"},
            {"minio_object": "o2", "minio_bucket": "b", "name": "image"},
        ]

    @pytest.mark.parametrize("meta", [None, {}, "x", {"inline_attachments": None}, {"inline_attachments": "x"}])
    def test_refs_of_garbage(self, meta):
        assert ii.image_refs_from_metadata(meta) == []

    @pytest.fixture
    def store(self, monkeypatch):
        import backend.routes.documents as documents

        files = {("b", "o1"): (b"\x89PNG-1", "image/png"), ("b", "o2"): (b"JPEG-2", "image/jpeg"), ("b", "empty"): (b"", "image/png")}
        calls = []

        def read(bucket, name):
            calls.append((bucket, name))
            return files.get((bucket, name))

        monkeypatch.setattr(documents, "read_inline_attachment_bytes", read)
        return SimpleNamespace(calls=calls)

    def test_data_url(self, store):
        url = ii.inline_attachment_as_data_url("b", "o1")

        assert url == "data:image/png;base64," + base64.b64encode(b"\x89PNG-1").decode()
        assert ii.inline_attachment_as_data_url("b", "ghost") is None and ii.inline_attachment_as_data_url("b", "empty") is None

    def test_history_images_come_first_then_current_ones(self, store):
        history = [
            {"role": "assistant", "image_refs": [{"minio_bucket": "b", "minio_object": "o1"}]},
            {"role": "USER", "image_refs": [{"minio_bucket": "b", "minio_object": "o1"}, {"minio_bucket": "b", "minio_object": "o2"}, {"minio_bucket": "b"}, "junk"]},
            {"role": "user", "image_refs": [{"minio_bucket": "b", "minio_object": "o1"}]},  # дубликат
            {"role": "user", "image_refs": "x"},
        ]

        out = ii.merge_inline_images_from_history(history, ["data:image/png;base64,CURRENT", "  ", None, "data:image/png;base64,CURRENT"])

        assert [u[:22] for u in out] == ["data:image/png;base64,", "data:image/jpeg;base64", "data:image/png;base64,"] and out[-1].endswith("CURRENT") and len(out) == 3
        assert store.calls.count(("b", "o1")) == 1  # повторно картинку не читаем

    def test_unreadable_history_images_are_skipped(self, store):
        out = ii.merge_inline_images_from_history([{"role": "user", "image_refs": [{"minio_bucket": "b", "minio_object": "ghost"}]}], ["cur"])

        assert out == ["cur"]

    def test_history_cap_applies_before_current_images(self, store):
        history = [{"role": "user", "image_refs": [{"minio_bucket": "b", "minio_object": "o1"}, {"minio_bucket": "b", "minio_object": "o2"}]}]

        out = ii.merge_inline_images_from_history(history, ["cur"], max_history_images=1)

        assert len(out) == 2 and out[1] == "cur"

    def test_default_cap(self, store):
        assert ii.MAX_HISTORY_INLINE_IMAGES == 8
        assert ii.merge_inline_images_from_history(None, None) == [] and ii.merge_inline_images_from_history([], ["x"]) == ["x"]

    def test_unavailable_reader_gives_none(self, monkeypatch):
        import backend.routes.documents as documents

        monkeypatch.delattr(documents, "read_inline_attachment_bytes")

        assert ii.inline_attachment_as_data_url("b", "o1") is None


class TestFollowUps:
    def test_prompt_uses_the_last_messages_and_trims_them(self):
        messages = [{"role": "user", "content": f"вопрос {i}"} for i in range(30)] + [{"role": "assistant", "content": "я" * 5000}, {"role": "assistant", "content": "  "}]

        prompt = fu._build_dialogue_prompt(messages)

        assert prompt.count("Пользователь:") == 10 and prompt.count("Ассистент:") == 1  # окно 12 сообщений, пустое пропущено
        assert "вопрос 19" not in prompt and "вопрос 20" in prompt and ("я" * 1999 + "…") in prompt

    def test_roles_default_to_the_user(self):
        assert fu._build_dialogue_prompt([{"content": "привет"}]) == "Пользователь: привет"

    @pytest.mark.parametrize(("text", "limit", "expected"), [("abc", 5, "abc"), ("  abc  ", 5, "abc"), ("abcdef", 4, "abc…"), (None, 3, "")])
    def test_truncate(self, text, limit, expected):
        assert fu._truncate(text, limit) == expected

    def test_array_of_suggestions(self):
        raw = '[{"title":"Итоги","subtitle":"кратко","content":"Подведи итоги"},{"title":"Риски","content":"Какие риски?"},{"title":"","content":"x"},{"title":"y"},"junk"]'

        assert fu._parse_suggestions(raw) == [
            {"id": "follow-up-0", "title": "Итоги", "subtitle": "кратко", "content": "Подведи итоги"},
            {"id": "follow-up-1", "title": "Риски", "subtitle": "", "content": "Какие риски?"},
        ]

    def test_json_inside_markdown_and_text(self):
        raw = 'Вот подсказки:\n```json\n[{"title":"A","content":"a?"}]\n```\nГотово'

        assert [s["title"] for s in fu._parse_suggestions(raw)] == ["A"]

    def test_at_most_three_suggestions_from_an_array(self):
        raw = "[" + ",".join('{"title":"t%d","content":"c%d"}' % (i, i) for i in range(6)) + "]"

        assert len(fu._parse_suggestions(raw)) == 3

    def test_object_with_follow_ups(self):
        raw = '{"follow_ups": ["Первый вопрос?", " ", "Второй вопрос?"]}'

        assert fu._parse_suggestions(raw) == [
            {"id": "follow-up-0", "title": "Первый вопрос?", "subtitle": "", "content": "Первый вопрос?"},
            {"id": "follow-up-2", "title": "Второй вопрос?", "subtitle": "", "content": "Второй вопрос?"},
        ]

    @pytest.mark.parametrize("raw", ["", None, "не JSON", "[]", "{}", '{"follow_ups": []}', '[{"title":"только заголовок"}]', "[1, 2]", "[{broken"])
    def test_unusable_answers(self, raw):
        assert fu._parse_suggestions(raw) == []

    async def test_generation_calls_the_model_off_the_event_loop(self):
        calls = []

        def ask_agent(prompt, **kwargs):
            calls.append((prompt, kwargs))
            return '[{"title":"Далее","content":"Продолжи"}]'

        out = await fu.generate_follow_up_suggestions(ask_agent=ask_agent, messages=[{"role": "user", "content": "привет"}, {"role": "assistant", "content": "здравствуйте"}], model_path="gw/m")

        assert out[0]["title"] == "Далее"
        prompt, kwargs = calls[0]
        assert "Пользователь: привет" in prompt and kwargs["system_prompt"] == fu.FOLLOW_UP_SYSTEM_PROMPT
        assert kwargs["model_path"] == "gw/m" and kwargs["streaming"] is False and kwargs["enable_thinking"] is False and kwargs["history"] is None

    async def test_empty_dialogue_does_not_call_the_model(self):
        def ask_agent(*a, **k):
            raise AssertionError("не должна вызываться")

        assert await fu.generate_follow_up_suggestions(ask_agent=ask_agent, messages=[{"role": "user", "content": "  "}], model_path=None) == []

    async def test_failures_and_empty_responses(self):
        def broken(*a, **k):
            raise RuntimeError("llm down")

        assert await fu.generate_follow_up_suggestions(ask_agent=broken, messages=[{"role": "user", "content": "q"}], model_path=None) == []
        assert await fu.generate_follow_up_suggestions(ask_agent=lambda *a, **k: "", messages=[{"role": "user", "content": "q"}], model_path=None) == []
        assert await fu.generate_follow_up_suggestions(ask_agent=None, messages=[], model_path=None) == []
