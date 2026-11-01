"""services/context_breakdown, inline_images; prompts/artifacts; context_prompts; storage/rag_pvc: хвосты."""

import os
import sys
from types import SimpleNamespace

import pytest

from backend import context_prompts as cp
from backend.prompts import artifacts as art_prompts
from backend.services import context_breakdown as cb
from backend.services import inline_images
from backend.storage import rag_pvc as pvc

pytestmark = pytest.mark.unit


# ───────────────────────────── context_breakdown ─────────────────────────────


class TestContextBreakdownTails:
    async def test_missing_mcp_modules_give_zero_tokens(self, monkeypatch):
        cb._MCP_TOKENS_CACHE.clear()
        monkeypatch.setitem(sys.modules, "backend.mcp.platform", None)  # import -> ImportError

        assert await cb._estimate_mcp_tools_tokens(["mcp:a"], {"user_id": "u"}) == 0
        cb._MCP_TOKENS_CACHE.clear()

    async def test_artifacts_failure_does_not_break_overhead(self, monkeypatch):
        async def resolve(agent_id, user_id, user=None):
            return {"artifacts_enabled": True}

        async def enrich(profile, user_id):
            return profile

        class PM:
            def get_global_prompt(self):
                return ""

            def get_effective_prompt(self, m):
                return ""

        async def get_pm(user_id):
            return PM()

        async def mcp(tool_ids, user):
            return 0

        async def inject(**kw):
            return None, None, None, None

        async def user_ms(user_id):
            return {}

        def boom(profile):
            raise RuntimeError("artifacts prompt failed")

        monkeypatch.setattr(cb, "get_user_model_settings", user_ms)
        monkeypatch.setattr(cb, "_resolve_agent_chat_params", resolve)
        monkeypatch.setattr(cb, "enrich_agent_profile_with_user_settings", enrich)
        monkeypatch.setattr(cb, "get_user_prompt_manager", get_pm)
        monkeypatch.setattr(cb, "_estimate_mcp_tools_tokens", mcp)
        monkeypatch.setattr("backend.services.skills.resolve_and_build_skill_injection", inject)
        monkeypatch.setattr("backend.prompts.artifacts.maybe_artifacts_prompt_for_agent", boom)

        out = await cb.build_context_overhead()

        assert all(s["id"] != "artifacts" for s in out["segments"])


# ───────────────────────────── inline_images ─────────────────────────────


def test_history_skips_non_user_messages(monkeypatch):
    monkeypatch.setattr(inline_images, "inline_attachment_as_data_url", lambda b, o: f"data:{b}/{o}")
    history = [
        {"role": "assistant", "image_refs": [{"minio_object": "a", "minio_bucket": "b"}]},
        {"role": " USER ", "image_refs": [{"minio_object": "o1", "minio_bucket": "bk"}]},
    ]

    assert inline_images.merge_inline_images_from_history(history) == ["data:bk/o1"]


# ───────────────────────────── prompts/artifacts ─────────────────────────────


class TestArtifactsPrompt:
    def test_build_without_shadcn(self):
        assert art_prompts.build_artifacts_prompt() == art_prompts.ARTIFACTS_PROMPT

    def test_build_with_shadcn_appends_addendum(self):
        text = art_prompts.build_artifacts_prompt(shadcn_enabled=True)

        assert text == f"{art_prompts.ARTIFACTS_PROMPT}\n\n{art_prompts.SHADCN_ARTIFACTS_ADDENDUM}"

    @pytest.mark.parametrize(
        "profile",
        [None, "строка", {}, {"artifacts_enabled": False}, {"artifacts_enabled": True, "user_prompt_mode": True}],
    )
    def test_no_prompt_when_disabled_or_custom_mode(self, profile):
        assert art_prompts.maybe_artifacts_prompt_for_agent(profile) is None

    def test_enabled_agent_gets_prompt_with_optional_shadcn(self):
        plain = art_prompts.maybe_artifacts_prompt_for_agent({"artifacts_enabled": True})
        rich = art_prompts.maybe_artifacts_prompt_for_agent({"artifacts_enabled": True, "shadcn_enabled": True})

        assert plain == art_prompts.ARTIFACTS_PROMPT and rich.endswith(art_prompts.SHADCN_ARTIFACTS_ADDENDUM)


# ───────────────────────────── context_prompts ─────────────────────────────


def test_prompts_path_is_resolved_lazily(monkeypatch, tmp_path):
    target = tmp_path / "prompts.json"
    monkeypatch.setattr(cp, "resolve_context_prompts_file_path_primary", lambda: target)
    monkeypatch.setattr(cp, "resolve_context_prompts_file_path", lambda p: target)
    manager = cp.ContextPromptManager()
    manager._prompts_path = None

    assert manager._validated_prompts_path() == target
    assert manager._prompts_path == target  # результат закэширован


# ───────────────────────────── rag_pvc ─────────────────────────────


def test_unreadable_file_is_listed_without_size(tmp_path, monkeypatch):
    good = tmp_path / "ok.txt"
    good.write_text("abc")
    bad = tmp_path / "bad.txt"
    bad.write_text("x")
    real_stat = os.stat

    def stat(path, *a, **k):
        if str(path).endswith("bad.txt"):
            raise OSError("нет доступа")
        return real_stat(path, *a, **k)

    monkeypatch.setattr(pvc.os, "stat", stat)

    files, total = pvc._collect_rag_pvc_files(str(tmp_path))

    by_name = {f["name"]: f for f in files}
    assert by_name["bad.txt"]["size"] is None and by_name["bad.txt"]["modified"] is None
    assert by_name["ok.txt"]["size"] == 3 and total == 3


def test_prompts_path_cached_value_is_returned_without_resolving(monkeypatch, tmp_path):
    target = tmp_path / "prompts.json"
    monkeypatch.setattr(cp, "resolve_context_prompts_file_path_primary", lambda: target)
    monkeypatch.setattr(cp, "resolve_context_prompts_file_path", lambda p: target)
    manager = cp.ContextPromptManager()
    monkeypatch.setattr(
        cp, "resolve_context_prompts_file_path", lambda p: pytest.fail("повторный резолв не нужен")
    )

    assert manager._validated_prompts_path() == target


def test_history_images_are_capped_across_messages(monkeypatch):
    monkeypatch.setattr(inline_images, "inline_attachment_as_data_url", lambda b, o: f"data:{b}/{o}")
    history = [
        {"role": "user", "image_refs": [{"minio_object": "o1", "minio_bucket": "bk"}]},
        {"role": "user", "image_refs": [{"minio_object": "o2", "minio_bucket": "bk"}]},
    ]

    result = inline_images.merge_inline_images_from_history(history, max_history_images=1)

    assert result == ["data:bk/o1"]
