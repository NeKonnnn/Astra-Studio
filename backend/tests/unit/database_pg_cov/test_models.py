"""Pydantic-модели и чистые хелперы пакета backend.database.postgresql."""

from __future__ import annotations

import numpy as np
import pytest
from pydantic import ValidationError

from backend.database.postgresql.agent_models import (
    AGENT_PERMISSION_EDITOR,
    AGENT_PERMISSION_VIEWER,
    AgentCreate,
    Tag,
    normalize_permission,
)
from backend.database.postgresql.models import Document, DocumentVector
from backend.database.postgresql.project_models import ProjectCreate, ProjectUpdate
from backend.database.postgresql.skill_models import (
    SKILL_PERMISSION_EDITOR,
    SKILL_PERMISSION_VIEWER,
    infer_skill_file_category,
    normalize_skill_permission,
    slugify_skill_id,
)


@pytest.mark.parametrize(
    "raw",
    ["use", "USE", "", None, "read", "Reader", "viewer", "unknown", "owner", "  "],
)
def test_normalize_permission_viewer_values(raw):
    assert normalize_permission(raw) == AGENT_PERMISSION_VIEWER


@pytest.mark.parametrize("raw", ["edit", "editor", "Writer", " write ", "EDITOR"])
def test_normalize_permission_editor_values(raw):
    assert normalize_permission(raw) == AGENT_PERMISSION_EDITOR


@pytest.mark.parametrize("raw", ["use", "", None, "read", "reader", "viewer", "x"])
def test_normalize_skill_permission_viewer_values(raw):
    assert normalize_skill_permission(raw) == SKILL_PERMISSION_VIEWER


@pytest.mark.parametrize("raw", ["edit", "editor", "WRITE", "writer"])
def test_normalize_skill_permission_editor_values(raw):
    assert normalize_skill_permission(raw) == SKILL_PERMISSION_EDITOR


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("My Skill", "my-skill"),
        ("  Already-Kebab  ", "already-kebab"),
        ("a!!b??c", "a-b-c"),
        ("v1.2_x", "v1.2_x"),
        ("---x---", "x"),
        ("Привет", "privet"),
        ("Анализ отчётов", "analiz-otchetov"),
        ("Щётка Ёжик Юла Съезд Объём", "shchetka-ezhik-yula-sezd-obem"),
        ("Йод Ыы", "yod-yy"),
        ("ъь", ""),
        ("", ""),
        (None, ""),
    ],
)
def test_slugify_skill_id(raw, expected):
    assert slugify_skill_id(raw) == expected


@pytest.mark.parametrize(
    "path, expected",
    [
        ("scripts/run.py", "script"),
        ("/References/doc.md", "reference"),
        ("assets\\logo.png", "asset"),
        ("README.md", "other"),
        ("", "other"),
        (None, "other"),
    ],
)
def test_infer_skill_file_category(path, expected):
    assert infer_skill_file_category(path) == expected


def test_document_defaults_and_validation():
    doc = Document(filename="a.txt", content="x")
    assert doc.id is None and doc.metadata == {}
    assert doc.created_at is not None and doc.updated_at is not None
    with pytest.raises(ValidationError):
        Document(content="x")  # type: ignore[call-arg]


def test_document_vector_numpy_roundtrip():
    vector = DocumentVector(document_id=1, chunk_index=2, embedding=[1, 2, 3], content="c")
    array = vector.to_numpy()
    assert array.dtype == np.float32
    assert array.tolist() == [1.0, 2.0, 3.0]

    rebuilt = DocumentVector.from_numpy(
        np.array([0.5, 0.25], dtype=np.float32), document_id=7, chunk_index=0, content="z"
    )
    assert rebuilt.embedding == [0.5, 0.25]
    assert (rebuilt.document_id, rebuilt.content) == (7, "z")


def test_tag_color_pattern_is_validated():
    assert Tag(name="ab", color="#A1b2C3").color == "#A1b2C3"
    with pytest.raises(ValidationError):
        Tag(name="ab", color="red")
    with pytest.raises(ValidationError):
        Tag(name="a")  # слишком короткое имя


def test_agent_create_validates_lengths():
    with pytest.raises(ValidationError):
        AgentCreate(name="ab", system_prompt="достаточно длинный промпт")
    with pytest.raises(ValidationError):
        AgentCreate(name="abc", system_prompt="короткий")


def test_project_models_accept_camel_case_aliases_and_field_names():
    by_alias = ProjectCreate(name="P", iconType="emoji", iconColor="#fff")
    by_name = ProjectCreate(name="P", icon_type="icon", icon_color="#000")
    assert (by_alias.icon_type, by_alias.icon_color) == ("emoji", "#fff")
    assert (by_name.icon_type, by_name.icon_color) == ("icon", "#000")
    assert by_alias.memory == "default" and by_alias.instructions == ""
    update = ProjectUpdate(iconType="icon")
    assert update.model_dump(exclude_unset=True) == {"icon_type": "icon"}
    with pytest.raises(ValidationError):
        ProjectCreate(name="P", memory="everything")


def test_share_permission_normalizers_never_return_owner_or_unknown_values():
    """Доказательство недостижимости: нормализаторы возвращают только viewer/editor,
    поэтому проверки ``not in SHARE_PERMISSIONS`` и ``v == 'editor'`` после них мертвы."""
    from backend.database.postgresql.agent_models import AGENT_SHARE_PERMISSIONS
    from backend.database.postgresql.skill_models import SKILL_SHARE_PERMISSIONS

    samples = [None, "", " ", "owner", "OWNER", "admin", "viewer", "editor", "use", "edit",
               "read", "write", "writer", "reader", "x" * 50, "редактор", "0", "None"]
    for raw in samples:
        assert normalize_permission(raw) in AGENT_SHARE_PERMISSIONS
        assert normalize_skill_permission(raw) in SKILL_SHARE_PERMISSIONS
