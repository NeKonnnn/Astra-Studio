"""services/user_feedback_context и agent_model_settings_migration: блок предпочтений из лайков и сравнение настроек с дефолтами."""

from datetime import datetime
from types import SimpleNamespace

import pytest

from backend.services import agent_model_settings_migration as migration
from backend.services import user_feedback_context as fb

pytestmark = pytest.mark.unit


def msg(content="Ответ", feedback=None, slots=None, ts=None, **extra):
    metadata = dict(extra)
    if feedback is not None:
        metadata["feedback"] = feedback
    if slots is not None:
        metadata["multi_llm_responses"] = slots
    return SimpleNamespace(content=content, metadata=metadata, timestamp=ts)


class TestTimestamps:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (datetime(2026, 1, 2, 3, 4, 5), datetime(2026, 1, 2, 3, 4, 5)),
            ("2026-01-02T03:04:05Z", datetime(2026, 1, 2, 3, 4, 5)),
            ("2026-01-02T03:04:05+00:00", datetime(2026, 1, 2, 3, 4, 5)),
            ("2026-01-02T03:04:05", datetime(2026, 1, 2, 3, 4, 5)),
            ("", datetime.min), ("   ", datetime.min), ("вчера", datetime.min), (None, datetime.min), (5, datetime.min),
        ],
    )
    def test_parse(self, value, expected):
        assert fb._parse_ts(value) == expected

    @pytest.mark.xfail(strict=True, reason="BUG: время с поясом, отличным от UTC, даёт aware-datetime; сортировка с naive-значениями падает TypeError")
    def test_mixed_timezones_do_not_break_the_formatting(self):
        entries = [
            {"rating": "like", "updated_at": "2026-01-02T03:04:05+03:00", "content_preview": "a"},
            {"rating": "like", "updated_at": "2026-01-01T03:04:05", "content_preview": "b"},
        ]

        assert fb.format_feedback_system_block(entries)


class TestExtraction:
    def test_message_feedback_and_slots(self):
        messages = [
            msg("Длинный ответ " + "я" * 300, feedback={"rating": "dislike", "tags": ["too_verbose"], "comment": "  много  ", "updated_at": "2026-01-02T00:00:00"}),
            msg(feedback={"rating": "like"}, ts=datetime(2026, 1, 1)),
            msg(feedback={"rating": "maybe"}),
            msg(feedback="junk"),
            msg(slots=[{"content": "слот", "model": "qwen", "feedback": {"rating": "like", "comment": "супер"}}, {"feedback": {"rating": "x"}}, "junk"]),
            {"content": "словарь", "metadata": {"feedback": {"rating": "dislike", "tags": None}}},
            SimpleNamespace(content="без метаданных", metadata=None),
        ]

        entries = fb._extract_feedback_entries(messages)

        assert [e["rating"] for e in entries] == ["dislike", "like", "like", "dislike"]
        assert entries[0]["comment"] == "много" and len(entries[0]["content_preview"]) == 180 and entries[0]["tags"] == ["too_verbose"]
        assert entries[1]["updated_at"] == datetime(2026, 1, 1)
        assert entries[2]["model"] == "qwen" and entries[2]["content_preview"] == "слот" and entries[2]["comment"] == "супер"
        assert entries[3]["content_preview"] == "словарь" and entries[3]["tags"] == []

    def test_camel_case_slots(self):
        entries = fb._extract_feedback_entries([SimpleNamespace(content="", metadata={"multiLLMResponses": [{"content": "c", "feedback": {"rating": "like"}}]}, timestamp=None)])

        assert len(entries) == 1

    def test_empty(self):
        assert fb._extract_feedback_entries([]) == [] and fb._extract_feedback_entries(None) == []


class TestFormatting:
    def entry(self, rating, ts, **kw):
        return {"rating": rating, "updated_at": ts, "tags": kw.get("tags", []), "comment": kw.get("comment", ""), "content_preview": kw.get("preview", "")}

    def test_empty(self):
        assert fb.format_feedback_system_block([]) is None and fb.format_feedback_system_block([{"rating": "other"}]) is None

    def test_dislikes_use_human_labels_and_comments(self):
        block = fb.format_feedback_system_block([self.entry("dislike", "2026-01-01T00:00:00", tags=["too_verbose", "custom_tag"], comment="много воды", preview="строка1\nстрока2")])

        assert block.startswith("[Обратная связь пользователя по прошлым ответам]") and "Плохие ответы (избегай подобного):" in block
        assert "Слишком длинный / многословный, custom_tag" in block and "Комментарий пользователя: «много воды»" in block and "«строка1 строка2»" in block
        assert "Хорошие ответы" not in block

    def test_dislike_without_tags(self):
        assert "Причины: без уточнения причины." in fb.format_feedback_system_block([self.entry("dislike", None)])

    def test_likes_section(self):
        block = fb.format_feedback_system_block([self.entry("like", None, comment="ок", preview="хороший текст")])

        assert "Хорошие ответы (ориентируйся на подобный стиль и полезность):" in block and "Комментарий: «ок»" in block and "Фрагмент: «хороший текст»" in block

    def test_newest_entries_win_and_limits_apply(self):
        entries = [self.entry("dislike", f"2026-01-{day:02d}T00:00:00", preview=f"d{day}") for day in range(1, 13)]
        entries += [self.entry("like", f"2026-02-{day:02d}T00:00:00", preview=f"l{day}") for day in range(1, 9)]

        block = fb.format_feedback_system_block(entries, max_items=8)

        assert block.count("Причины:") == 8 and block.count("отметил ответ как хороший") == 4  # лайков max_items // 2
        assert "«d12»" in block and "«d5»" in block and "«d4»" not in block and "«l8»" in block and "«l5»" in block and "«l4»" not in block

    def test_previews_are_cut(self):
        block = fb.format_feedback_system_block([self.entry("like", None, preview="я" * 500)])

        assert "я" * 120 in block and "я" * 121 not in block

    def test_merge_into_the_system_prompt(self):
        assert fb.merge_feedback_into_system_prompt("Ты помощник.", "БЛОК") == "Ты помощник.\n\nБЛОК"
        assert fb.merge_feedback_into_system_prompt(None, " БЛОК ") == "БЛОК"
        assert fb.merge_feedback_into_system_prompt("Ты помощник.", None) == "Ты помощник."
        assert fb.merge_feedback_into_system_prompt(None, "  ") is None


class TestCollection:
    @pytest.fixture
    def repo(self, monkeypatch):
        import backend.app_state as app_state

        state = SimpleNamespace(
            current=None, others=[], calls=[], missing=False, error=None,
        )

        class Repo:
            async def get_conversation(self, cid):
                state.calls.append(("get", cid))
                return state.current

            async def get_user_conversations(self, user_id, limit, skip):
                state.calls.append(("list", user_id, limit, skip))
                if state.error:
                    raise state.error
                return state.others

        monkeypatch.setattr(app_state, "get_conversation_repository", lambda: None if state.missing else Repo(), raising=False)
        return state

    def conv(self, cid, *messages):
        return SimpleNamespace(conversation_id=cid, messages=list(messages))

    async def test_current_conversation_and_recent_ones_are_merged_without_duplicates(self, repo):
        like = msg("тот же ответ", feedback={"rating": "like", "updated_at": "2026-01-01T00:00:00"})
        repo.current = self.conv("c1", like, msg("дизлайк", feedback={"rating": "dislike", "updated_at": "2026-01-03T00:00:00"}))
        repo.others = [self.conv("c1", msg("не должен повториться", feedback={"rating": "like"})), self.conv("c2", like)]

        entries = await fb.collect_user_feedback_entries("alice", conversation_id="c1")

        assert [e["rating"] for e in entries] == ["dislike", "like"]  # одинаковый лайк в двух диалогах схлопнут, новые первыми
        assert ("list", "alice", 15, 0) in repo.calls

    async def test_entries_are_capped(self, repo):
        repo.others = [self.conv("c", *[msg(f"ответ {i}", feedback={"rating": "like", "comment": str(i)}) for i in range(30)])]

        assert len(await fb.collect_user_feedback_entries("alice", max_entries=5)) == 5

    async def test_without_user_or_repository(self, repo):
        assert await fb.collect_user_feedback_entries("") == [] and repo.calls == []
        repo.missing = True
        assert await fb.collect_user_feedback_entries("alice") == []

    async def test_storage_errors_give_an_empty_list(self, repo):
        repo.error = RuntimeError("mongo down")

        assert await fb.collect_user_feedback_entries("alice") == []

    async def test_block_for_a_user(self, repo):
        repo.others = [self.conv("c", msg("плохо", feedback={"rating": "dislike", "tags": ["inaccurate"]}))]

        block = await fb.build_user_feedback_system_block("alice")

        assert "Неточный / фактические ошибки" in block
        assert await fb.build_user_feedback_system_block(None) is None and await fb.build_user_feedback_system_block("") is None

    async def test_block_is_none_without_feedback(self, repo):
        assert await fb.build_user_feedback_system_block("alice") is None


class TestMigrationLogic:
    def test_dict_decoding(self):
        assert migration._as_dict({"a": 1}) == {"a": 1} and migration._as_dict('{"a": 1}') == {"a": 1}
        for junk in (None, "", "  ", "not json", "[1]", 5, []):
            assert migration._as_dict(junk) is None

    @pytest.mark.parametrize(
        ("left", "right", "same"),
        [(0.7, 0.7, True), (1, 1.0, True), (0.1 + 0.2, 0.3, True), (0.7, 0.71, False), (True, True, True), (True, 1, False), (1, True, False), (False, 0, False), ("a", "a", True), ("a", "b", False), (None, None, True)],
    )
    def test_value_comparison_keeps_bool_and_numbers_apart(self, left, right, same):
        assert migration._same_value(left, right) is same

    def test_constructor_defaults_match_themselves(self):
        assert migration.matches_defaults(dict(migration._CONSTRUCTOR_DEFAULT), migration._CONSTRUCTOR_DEFAULT) is True

    def test_any_custom_value_keeps_the_card(self):
        stored = {**migration._CONSTRUCTOR_DEFAULT, "temperature": 0.2}

        assert migration.matches_defaults(stored, migration._CONSTRUCTOR_DEFAULT) is False

    def test_streaming_speed_and_unknown_keys_are_ignored(self):
        stored = {**migration._CONSTRUCTOR_DEFAULT, "streaming_speed": 99, "legacy_option": "x"}

        assert migration.matches_defaults(stored, migration._CONSTRUCTOR_DEFAULT) is True

    def test_empty_card_matches(self):
        assert migration.matches_defaults({}, migration._CONSTRUCTOR_DEFAULT) is True

    def test_database_numbers_equal_code_numbers(self):
        assert migration.matches_defaults({"temperature": 0.7000000000001, "top_k": 40.0, "use_gpu": False}, migration._CONSTRUCTOR_DEFAULT) is True
        assert migration.matches_defaults({"use_gpu": 0}, migration._CONSTRUCTOR_DEFAULT) is False

    @pytest.mark.parametrize(("raw", "expected"), [(None, False), ("", False), ("force", True), (" FORCE ", True), ("1", False), ("true", False)])
    def test_force_flag(self, monkeypatch, raw, expected):
        monkeypatch.delenv("AGENT_MODEL_SETTINGS_MIGRATE", raising=False)
        if raw is not None:
            monkeypatch.setenv("AGENT_MODEL_SETTINGS_MIGRATE", raw)

        assert migration._force_requested() is expected

    def test_lock_ids_of_the_two_migrations_differ(self):
        from backend.services import entity_rag_migration

        assert migration._MIGRATION_LOCK_ID != getattr(entity_rag_migration, "_MIGRATION_LOCK_ID", None)
