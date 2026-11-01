"""Генерация картинок (memo-only): триггеры чата, метаданные вариантов, пресеты."""

from types import SimpleNamespace

import pytest

from backend.services import image_generation_presets as presets
from backend.services import image_generation_service as ig

pytestmark = pytest.mark.unit


class TestChatTriggers:
    def test_extract_prompt_from_intent(self):
        assert ig.extract_image_prompt_from_chat("нарисуй кота в шляпе") == "кота в шляпе"
        assert ig.extract_image_prompt_from_chat("сгенерируй картинку: закат") == "закат"
        assert ig.extract_image_prompt_from_chat("/img red balloon") == "red balloon"
        assert ig.extract_image_prompt_from_chat("привет") is None
        assert ig.extract_image_prompt_from_chat("") is None

    def test_mode_enabled_uses_whole_message(self):
        assert ig.resolve_image_prompt_from_chat("просто текст", mode_enabled=True) == "просто текст"
        assert ig.resolve_image_prompt_from_chat("просто текст", mode_enabled=False) is None

    def test_is_request_respects_settings(self, monkeypatch):
        monkeypatch.setattr(
            ig,
            "get_image_generation_settings",
            lambda: SimpleNamespace(enabled=True, chat_triggers_enabled=True),
        )
        assert ig.is_image_generation_chat_request("сгенерируй закат") is True
        assert ig.is_image_generation_chat_request("привет") is False

        monkeypatch.setattr(
            ig,
            "get_image_generation_settings",
            lambda: SimpleNamespace(enabled=False, chat_triggers_enabled=True),
        )
        assert ig.is_image_generation_chat_request("сгенерируй закат") is False

    def test_mode_enabled_bypasses_trigger_patterns(self, monkeypatch):
        monkeypatch.setattr(
            ig,
            "get_image_generation_settings",
            lambda: SimpleNamespace(enabled=True, chat_triggers_enabled=False),
        )
        assert ig.is_image_generation_chat_request("любой промпт", mode_enabled=True) is True
        assert ig.is_image_generation_chat_request("любой промпт", mode_enabled=False) is False


class TestVariantMetadata:
    def test_initial_and_append(self):
        stored = {
            "inline_attachments": [
                {"name": "generated_1.png", "contentType": "image", "data_uri": "data:image/png;base64,aaa", "prompt": "кот"}
            ],
            "image_gen_preset_id": "p1",
        }
        meta = ig.build_initial_image_variant_metadata(stored)
        assert len(meta["image_variants"]) == 1
        assert meta["current_image_variant_index"] == 0

        next_meta = ig.append_image_variant_metadata(
            meta,
            {
                "inline_attachments": [
                    {"name": "generated_1.png", "contentType": "image", "data_uri": "data:image/png;base64,bbb", "prompt": "кот 2"}
                ],
                "image_gen_preset_id": "p2",
            },
        )
        assert len(next_meta["image_variants"]) == 2
        assert next_meta["current_image_variant_index"] == 1
        assert next_meta["image_gen_preset_id"] == "p2"

    def test_build_attachments(self):
        text, meta = ig.build_generated_image_attachments(
            [(b"\x89PNG", "image/png")],
            prompt="п",
        )
        assert "п" in text
        atts = meta["inline_attachments"]
        assert len(atts) == 1
        assert atts[0]["generated"] is True
        assert atts[0]["data_uri"].startswith("data:image/png;base64,")


class TestPresets:
    def test_apply_preset_fills_defaults(self, monkeypatch):
        monkeypatch.setattr(
            presets,
            "_cfg",
            lambda: SimpleNamespace(
                workflow_path="wf.json",
                checkpoint_name="ckpt.safetensors",
                default_width=640,
                default_height=480,
                default_steps=12,
                node_map={"prompt": ("1", "text")},
            ),
        )
        out = presets.apply_preset_to_generation_params(
            {"id": "x", "label": "X", "default_width": 1024},
            width=None,
            height=None,
            steps=None,
        )
        assert out["width"] == 1024
        assert out["height"] == 480
        assert out["steps"] == 12
        assert out["preset_id"] == "x"

    def test_save_user_presets_skips_invalid(self, monkeypatch):
        saved = {}

        monkeypatch.setattr(presets, "save_app_settings", lambda u: saved.update(u))
        monkeypatch.setattr(presets, "load_app_settings", lambda: {})
        cleaned = presets.save_user_presets(
            [
                {"id": "a", "label": "A", "default_width": 512},
                {"id": "", "label": "bad"},
                "junk",
            ],
            default_preset_id="a",
        )
        assert len(cleaned) == 1 and cleaned[0]["id"] == "a"
        assert saved["image_generation_default_preset_id"] == "a"
