"""services/user_llm_settings: персональные настройки генерации и контекстные промпты поверх глобальных дефолтов."""

from types import SimpleNamespace

import pytest

from backend.services import user_llm_settings as uls

pytestmark = pytest.mark.unit

DEFAULTS = {
    "context_size": 8192, "output_tokens": 2048, "temperature": 0.7, "top_p": 0.95, "repeat_penalty": 1.05, "top_k": 40,
    "min_p": 0.05, "frequency_penalty": 0.0, "presence_penalty": 0.0, "use_gpu": False, "streaming": True, "streaming_speed": 20, "verbose": False,
}


class FakeUserSettingsRepo:
    def __init__(self, row=None):
        self.row = row
        self.upserts = []
        self.fail_upsert = False

    async def get(self, user_id):
        return None if self.row is None else dict(self.row)

    async def upsert(self, user_id, model_settings=None, context_prompts=None):
        self.upserts.append((user_id, model_settings, context_prompts))
        if self.fail_upsert:
            return None
        row = dict(self.row or {})
        if model_settings is not None:
            row["model_settings"] = model_settings
        if context_prompts is not None:
            row["context_prompts"] = context_prompts
        self.row = row
        return row


@pytest.fixture(autouse=True)
def storage(monkeypatch):
    state = SimpleNamespace(repo=FakeUserSettingsRepo())
    monkeypatch.setattr(uls, "_default_model_settings", lambda: dict(DEFAULTS))
    monkeypatch.setattr(uls, "_get_repo", lambda: state.repo)
    return state


class TestRuntimeOverride:
    def test_no_override_means_the_defaults(self):
        assert uls.get_active_model_settings() == DEFAULTS

    def test_override_wins_for_known_keys_and_skips_none_and_unknown_ones(self):
        token = uls.bind_user_model_runtime({"temperature": 0.1, "top_k": None, "secret_key": "x", "use_gpu": True})
        try:
            active = uls.get_active_model_settings()
        finally:
            uls.reset_user_model_runtime(token)

        assert active["temperature"] == 0.1 and active["top_k"] == 40 and active["use_gpu"] is True and "secret_key" not in active

    def test_override_is_scoped_and_empty_values_do_nothing(self):
        token = uls.bind_user_model_runtime({"temperature": 0.1})
        uls.reset_user_model_runtime(token)
        assert uls.get_active_model_settings()["temperature"] == 0.7

        for empty in (None, {}, "x", []):
            token = uls.bind_user_model_runtime(empty)  # type: ignore[arg-type]
            try:
                assert uls.get_active_model_settings() == DEFAULTS
            finally:
                uls.reset_user_model_runtime(token)

    def test_the_override_is_copied(self):
        source = {"temperature": 0.1}
        token = uls.bind_user_model_runtime(source)
        source["temperature"] = 9.9
        try:
            assert uls.get_active_model_settings()["temperature"] == 0.1
        finally:
            uls.reset_user_model_runtime(token)

    async def test_the_override_does_not_leak_between_tasks(self):
        import asyncio

        async def worker(value):
            token = uls.bind_user_model_runtime({"temperature": value})
            try:
                await asyncio.sleep(0.01)
                return uls.get_active_model_settings()["temperature"]
            finally:
                uls.reset_user_model_runtime(token)

        assert await asyncio.gather(worker(0.1), worker(0.2), worker(0.3)) == [0.1, 0.2, 0.3]


class TestMerging:
    def test_model_settings_only_accept_known_keys(self):
        merged = uls._merge_model_settings({"temperature": 0.2, "top_k": None, "is_admin": True, "evil": 1})

        assert merged["temperature"] == 0.2 and merged["top_k"] == 40 and "is_admin" not in merged and "evil" not in merged

    @pytest.mark.parametrize("stored", [None, "x", [], 5])
    def test_garbage_stored_settings(self, stored):
        assert uls._merge_model_settings(stored) == DEFAULTS

    def test_prompts_merge(self):
        merged = uls._merge_context_prompts({"global_prompt": "Общий", "model_prompts": {"gw/m": "для модели"}, "custom_prompts": {"p1": {"prompt": "x"}}})

        assert merged == {"global_prompt": "Общий", "model_prompts": {"gw/m": "для модели"}, "custom_prompts": {"p1": {"prompt": "x"}}}

    def test_prompts_defaults_and_garbage(self):
        empty = {"global_prompt": "", "model_prompts": {}, "custom_prompts": {}}

        assert uls._merge_context_prompts(None) == empty and uls._merge_context_prompts("x") == empty
        assert uls._merge_context_prompts({"global_prompt": None})["global_prompt"] == ""
        assert uls._merge_context_prompts({"model_prompts": "bad", "custom_prompts": [1]}) == empty

    def test_prompt_manager_lives_in_memory_only(self):
        mgr = uls.prompt_manager_from_data({"global_prompt": "Привет", "model_prompts": {"m": "p"}})

        assert mgr.context_prompts["global_prompt"] == "Привет" and mgr.prompts_file == ""
        assert mgr.save_context_prompts({"global_prompt": "Новый"}) is True
        assert mgr.context_prompts == {"global_prompt": "Новый"}
        assert mgr.save_context_prompts() is True


class TestModelSettingsStorage:
    async def test_anonymous_and_missing_rows_get_defaults(self, storage):
        assert await uls.get_user_model_settings(None) == DEFAULTS
        assert await uls.get_user_model_settings("alice") == DEFAULTS

    async def test_unavailable_storage_gives_defaults_for_reads(self, storage):
        storage.repo = None

        assert await uls.get_user_model_settings("alice") == DEFAULTS
        assert await uls.get_user_context_prompts("alice") == {"global_prompt": "", "model_prompts": {}, "custom_prompts": {}}

    async def test_saved_settings_override_the_defaults(self, storage):
        saved = await uls.save_user_model_settings("alice", {"temperature": 0.3, "top_k": 10})

        assert saved["temperature"] == 0.3 and saved["top_k"] == 10 and saved["top_p"] == 0.95
        assert (await uls.get_user_model_settings("alice"))["temperature"] == 0.3

    async def test_first_save_also_stores_the_prompt_seed(self, storage):
        await uls.save_user_model_settings("alice", {"temperature": 0.3})

        _, settings, prompts = storage.repo.upserts[0]
        assert prompts == {"global_prompt": "", "model_prompts": {}, "custom_prompts": {}} and settings["temperature"] == 0.3

    async def test_later_saves_leave_the_prompts_alone(self, storage):
        storage.repo.row = {"model_settings": dict(DEFAULTS), "context_prompts": {"global_prompt": "мой"}}

        await uls.save_user_model_settings("alice", {"temperature": 0.1})

        assert storage.repo.upserts[0][2] is None and storage.repo.row["context_prompts"] == {"global_prompt": "мой"}

    async def test_unknown_keys_cannot_be_stored(self, storage):
        await uls.save_user_model_settings("alice", {"is_admin": True, "temperature": 0.2})

        assert "is_admin" not in storage.repo.row["model_settings"]

    async def test_values_are_not_range_checked_here_known_issue(self, storage):
        # Диапазоны проверяет схема запроса API; сервис сохраняет что дали (температура 999, отрицательные токены).
        saved = await uls.save_user_model_settings("alice", {"temperature": 999, "output_tokens": -5})

        assert saved["temperature"] == 999 and saved["output_tokens"] == -5

    async def test_save_failures(self, storage):
        storage.repo.fail_upsert = True
        with pytest.raises(RuntimeError, match="Не удалось сохранить настройки модели"):
            await uls.save_user_model_settings("alice", {})

        storage.repo = None
        with pytest.raises(RuntimeError, match="недоступно"):
            await uls.save_user_model_settings("alice", {})

    async def test_reset_restores_the_defaults(self, storage):
        await uls.save_user_model_settings("alice", {"temperature": 0.1})

        assert await uls.reset_user_model_settings("alice") == DEFAULTS


class TestContextPromptStorage:
    async def test_global_model_and_custom_prompts(self, storage):
        await uls.set_user_global_prompt("alice", "Отвечай кратко")
        await uls.set_user_model_prompt("alice", "gw/qwen", "Для qwen")
        await uls.set_user_custom_prompt("alice", "p1", "Мой промпт", "описание")

        data = await uls.get_user_context_prompts("alice")
        assert data["global_prompt"] == "Отвечай кратко" and data["model_prompts"] == {"gw/qwen": "Для qwen"}
        assert data["custom_prompts"]["p1"]["prompt"] == "Мой промпт" and data["custom_prompts"]["p1"]["description"] == "описание" and data["custom_prompts"]["p1"]["created_at"]

    async def test_a_blank_model_prompt_removes_it(self, storage):
        await uls.set_user_model_prompt("alice", "gw/qwen", "Для qwen")

        data = await uls.set_user_model_prompt("alice", "gw/qwen", "   ")

        assert data["model_prompts"] == {}

    async def test_global_prompt_can_be_cleared(self, storage):
        await uls.set_user_global_prompt("alice", "x")

        assert (await uls.set_user_global_prompt("alice", None))["global_prompt"] == ""  # type: ignore[arg-type]

    async def test_custom_prompts_are_deleted_by_id(self, storage):
        await uls.set_user_custom_prompt("alice", "p1", "x")

        assert await uls.delete_user_custom_prompt("alice", "p1") is True
        assert await uls.delete_user_custom_prompt("alice", "p1") is False
        assert (await uls.get_user_context_prompts("alice"))["custom_prompts"] == {}

    async def test_first_prompt_save_stores_the_settings_seed(self, storage):
        await uls.save_user_context_prompts("alice", {"global_prompt": "x"})

        assert storage.repo.upserts[0][1] == DEFAULTS

    async def test_users_do_not_see_each_others_prompts_through_the_service(self, storage):
        # Репозиторий адресуется по user_id; сервис всегда передаёт его вызывающего.
        await uls.set_user_global_prompt("alice", "секрет алисы")

        assert storage.repo.upserts[0][0] == "alice"

    async def test_prompt_manager_for_a_user(self, storage):
        await uls.set_user_global_prompt("alice", "Привет")

        mgr = await uls.get_user_prompt_manager("alice")

        assert mgr.context_prompts["global_prompt"] == "Привет"
        assert (await uls.get_user_prompt_manager(None)).context_prompts["global_prompt"] == ""

    async def test_save_failure(self, storage):
        storage.repo.fail_upsert = True

        with pytest.raises(RuntimeError, match="контекстные промпты"):
            await uls.save_user_context_prompts("alice", {})

        storage.repo = None
        with pytest.raises(RuntimeError, match="недоступно"):
            await uls.save_user_context_prompts("alice", {})


class TestAgentEnrichment:
    async def test_agent_card_overrides_key_by_key(self, storage):
        await uls.save_user_model_settings("alice", {"temperature": 0.2, "top_p": 0.5})

        profile = await uls.enrich_agent_profile_with_user_settings({"model_settings": {"temperature": 0.9, "top_p": None}}, "alice")

        effective = profile["effective_model_settings"]
        assert effective["temperature"] == 0.9 and effective["top_p"] == 0.5  # None в карточке = «не задано», берём персональное
        assert profile["model_settings"] == {"temperature": 0.9, "top_p": None}  # сырая карточка не меняется

    async def test_derived_generation_parameters(self, storage):
        profile = await uls.enrich_agent_profile_with_user_settings({}, "alice")

        assert profile["max_tokens"] == 2048 and profile["temperature"] == 0.7

    async def test_explicit_agent_values_are_kept(self, storage):
        profile = await uls.enrich_agent_profile_with_user_settings({"max_tokens": 100, "temperature": 0.0}, "alice")

        assert profile["max_tokens"] == 100 and profile["temperature"] == 0.0

    async def test_broken_numeric_settings_fall_back_to_safe_values(self, storage):
        await uls.save_user_model_settings("alice", {"output_tokens": "много", "temperature": "жарко"})

        profile = await uls.enrich_agent_profile_with_user_settings(None, "alice")

        assert profile["max_tokens"] == 1024 and profile["temperature"] == 0.7

    async def test_zero_output_tokens_means_the_default_limit(self, storage):
        await uls.save_user_model_settings("alice", {"output_tokens": 0})

        assert (await uls.enrich_agent_profile_with_user_settings({}, "alice"))["max_tokens"] == 1024

    async def test_anonymous_users_get_the_defaults(self, storage):
        profile = await uls.enrich_agent_profile_with_user_settings({"name": "A"}, None)

        assert profile["name"] == "A" and profile["effective_model_settings"] == DEFAULTS

    async def test_unknown_card_keys_do_not_leak_into_the_effective_settings(self, storage):
        profile = await uls.enrich_agent_profile_with_user_settings({"model_settings": {"evil": 1, "temperature": 0.5}}, "alice")

        assert "evil" not in profile["effective_model_settings"]
