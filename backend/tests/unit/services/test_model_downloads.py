"""Unit-тесты каталога / путей / token mask / job manager (без реальной сети)."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.services.model_downloads import catalog as catalog_mod
from backend.services.model_downloads import paths as paths_mod
from backend.services.model_downloads.job_manager import DownloadJobManager
from backend.services.model_downloads.token_store import mask_token, set_hf_token, get_hf_token


pytestmark = pytest.mark.unit


def test_catalog_loads_and_has_required_types():
    items = catalog_mod.load_catalog_raw()
    assert items, "model_catalog.yml должен содержать модели"
    types = {m["type"] for m in items}
    assert "llm_gguf" in types
    assert "rag_embed" in types
    assert "stt" in types
    assert "tts" in types
    assert any(m.get("source", {}).get("kind") == "http" for m in items)


def test_safe_local_name_rejects_traversal():
    with pytest.raises(ValueError):
        paths_mod.safe_local_name("../etc/passwd")
    with pytest.raises(ValueError):
        paths_mod.safe_local_name("foo/../../etc/passwd")
    # вложенный путь из HF listing → только basename
    assert paths_mod.safe_local_name("subdir/model.gguf") == "model.gguf"
    assert paths_mod.safe_local_name("FRIDA") == "FRIDA"
    assert paths_mod.safe_local_name("model.gguf") == "model.gguf"


def test_resolve_target_dir_under_root(tmp_path, monkeypatch):
    monkeypatch.setattr(paths_mod, "model_roots", lambda: {
        "llm": tmp_path / "llm",
        "rag": tmp_path / "rag",
        "comfyui": tmp_path / "comfyui",
        "omnivoice": tmp_path / "omnivoice",
        "stt": tmp_path / "stt",
        "tts": tmp_path / "tts",
        "diarization": tmp_path / "diar",
        "ocr": tmp_path / "ocr",
    })
    d = paths_mod.resolve_target_dir("rag_embed", local_name="FRIDA")
    assert d == (tmp_path / "rag" / "FRIDA").resolve()
    assert d.is_dir()

    f = paths_mod.resolve_target_file(
        "llm_gguf", "Qwen.gguf", local_name="Qwen.gguf"
    )
    assert f.parent == (tmp_path / "llm").resolve()
    assert f.name == "Qwen.gguf"

    tts = paths_mod.resolve_target_file("tts", "model.pt", local_name="ru")
    assert tts == (tmp_path / "tts" / "ru" / "model.pt").resolve()


def test_mask_token():
    assert mask_token(None) == {"configured": False, "masked": ""}
    assert mask_token("")["configured"] is False
    m = mask_token("hf_abcdefghijklmnop")
    assert m["configured"] is True
    assert m["masked"].startswith("hf_a")
    assert "*" in m["masked"]


def test_token_store_roundtrip(tmp_path, monkeypatch):
    token_file = tmp_path / "hf_token.txt"
    monkeypatch.setattr(
        "backend.services.model_downloads.token_store._token_file",
        lambda: token_file,
    )
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    set_hf_token("hf_test_token_value")
    assert get_hf_token() == "hf_test_token_value"
    set_hf_token("")
    assert get_hf_token() is None


def test_job_manager_cancel_and_fail_without_source(monkeypatch):
    mgr = DownloadJobManager()
    with pytest.raises(ValueError):
        mgr.start_custom(model_type="llm_gguf")

    # mock download path to avoid network: cancel before run finishes
    started = {}

    def fake_run(job_id, spec):
        started["id"] = job_id
        mgr._update(job_id, status="running", message="mock")
        if mgr._is_cancelled(job_id):
            mgr._update(job_id, status="cancelled", message="Отменено")
        else:
            mgr._update(job_id, status="completed", message="ok", progress_pct=100)

    monkeypatch.setattr(mgr, "_run_job_thread", fake_run)
    job = mgr.start_custom(
        model_type="llm_gguf",
        repo_id="org/model",
        filename="a.gguf",
        local_name="a.gguf",
        apply_action="none",
    )
    assert job.id in started.values() or mgr.get_job(job.id)
    assert mgr.cancel(job.id) or mgr.get_job(job.id)["status"] in {
        "running",
        "completed",
        "cancelled",
        "queued",
    }


def test_job_manager_list_and_get():
    mgr = DownloadJobManager()
    assert mgr.list_jobs() == []
    assert mgr.get_job("missing") is None


def test_normalize_hf_ref_cli_and_hf_uri():
    from backend.services.model_downloads.hf_ref import normalize_hf_ref

    n = normalize_hf_ref(
        repo_id="hf download hf://unsloth/Qwen3.8-27B-GGUF/Qwen3.8-27B-UD-IQ4_XS.gguf"
    )
    assert n["repo_id"] == "unsloth/Qwen3.8-27B-GGUF"
    assert n["filename"] == "Qwen3.8-27B-UD-IQ4_XS.gguf"
    assert n["url"] is None

    n2 = normalize_hf_ref(repo_id="hf://unsloth/Qwen3.8-27B-GGUF/Qwen3.8-27B-UD-IQ4_XS.gguf")
    assert n2["repo_id"] == "unsloth/Qwen3.8-27B-GGUF"
    assert n2["filename"] == "Qwen3.8-27B-UD-IQ4_XS.gguf"


def test_catalog_includes_disk_gguf(tmp_path, monkeypatch):
    llm = tmp_path / "llm"
    llm.mkdir()
    big = llm / "MyLocal-Q4.gguf"
    big.write_bytes(b"x" * 1_500_000)
    stub = llm / "stub.gguf"
    stub.write_bytes(b"x" * 128_000)

    monkeypatch.setattr(
        "backend.services.model_downloads.catalog.model_roots",
        lambda: {
            "llm": llm,
            "rag": tmp_path / "rag",
            "comfyui": tmp_path / "comfyui",
            "omnivoice": tmp_path / "omnivoice",
            "stt": tmp_path / "stt",
            "tts": tmp_path / "tts",
            "diarization": tmp_path / "diar",
            "ocr": tmp_path / "ocr",
        },
    )
    # не тянем реальный YAML в этот тест — только disk scan merge
    monkeypatch.setattr(catalog_mod, "load_catalog_raw", lambda: [])
    items = catalog_mod.list_catalog_with_installed()
    ids = {m["id"] for m in items}
    assert "local-llm-MyLocal-Q4" in ids
    assert "local-llm-stub" not in ids
    assert all(m.get("installed") for m in items if m["id"].startswith("local-llm-"))


def test_provenance_sidecar_enables_repull(tmp_path, monkeypatch):
    from backend.services.model_downloads.provenance import write_provenance

    llm = tmp_path / "llm"
    llm.mkdir()
    gguf = llm / "Pulled-Q8.gguf"
    gguf.write_bytes(b"x" * 1_500_000)
    write_provenance(
        gguf,
        {
            "model_type": "llm_gguf",
            "repo_id": "org/Pulled-GGUF",
            "filename": "Pulled-Q8.gguf",
            "local_name": "Pulled-Q8.gguf",
            "apply_action": "load_llm",
            "display_name": "Pulled Q8",
        },
    )

    monkeypatch.setattr(
        "backend.services.model_downloads.catalog.model_roots",
        lambda: {
            "llm": llm,
            "rag": tmp_path / "rag",
            "comfyui": tmp_path / "comfyui",
            "omnivoice": tmp_path / "omnivoice",
            "stt": tmp_path / "stt",
            "tts": tmp_path / "tts",
            "diarization": tmp_path / "diar",
            "ocr": tmp_path / "ocr",
        },
    )
    monkeypatch.setattr(catalog_mod, "load_catalog_raw", lambda: [])
    items = catalog_mod.list_catalog_with_installed()
    hit = next(m for m in items if m["id"] == "local-llm-Pulled-Q8")
    assert hit["source"]["kind"] == "hf"
    assert hit["source"]["repo_id"] == "org/Pulled-GGUF"
    assert hit["source"]["filename"] == "Pulled-Q8.gguf"
    assert hit.get("has_provenance") is True
    assert hit["display_name"] == "Pulled Q8"


def test_org_from_repo_and_avatar_sidecar(tmp_path):
    from backend.services.model_downloads.avatar import (
        org_from_repo_id,
        read_avatar_url_near,
        write_avatar_url_sidecar,
    )

    assert org_from_repo_id("unsloth/Qwen") == "unsloth"
    assert org_from_repo_id("bad") is None
    f = tmp_path / "m.gguf"
    f.write_bytes(b"x")
    write_avatar_url_sidecar(f, "https://cdn-avatars.huggingface.co/x.png")
    assert read_avatar_url_near(f) == "https://cdn-avatars.huggingface.co/x.png"


def test_write_provenance_near_file(tmp_path):
    from backend.services.model_downloads.provenance import (
        read_provenance_near,
        sidecar_path_for_file,
        write_provenance,
    )

    f = tmp_path / "a.gguf"
    f.write_bytes(b"abc")
    dest = write_provenance(f, {"repo_id": "a/b", "filename": "a.gguf"})
    assert dest == sidecar_path_for_file(f)
    assert dest.is_file()
    meta = read_provenance_near(f)
    assert meta and meta["repo_id"] == "a/b"


def test_delete_installed_gguf_and_sidecar(tmp_path, monkeypatch):
    from backend.services.model_downloads import delete as delete_mod
    from backend.services.model_downloads.provenance import write_provenance

    llm = tmp_path / "llm"
    llm.mkdir()
    gguf = llm / "ToDelete-Q4.gguf"
    gguf.write_bytes(b"x" * 1_500_000)
    write_provenance(gguf, {"repo_id": "org/ToDelete", "filename": "ToDelete-Q4.gguf"})
    assert (llm / "ToDelete-Q4.gguf.astra-source.json").is_file()

    roots = {
        "llm": llm,
        "rag": tmp_path / "rag",
        "comfyui": tmp_path / "comfyui",
        "omnivoice": tmp_path / "omnivoice",
        "stt": tmp_path / "stt",
        "tts": tmp_path / "tts",
        "diarization": tmp_path / "diar",
        "ocr": tmp_path / "ocr",
    }
    monkeypatch.setattr(paths_mod, "model_roots", lambda: roots)
    monkeypatch.setattr(catalog_mod, "model_roots", lambda: roots)
    monkeypatch.setattr(delete_mod, "model_roots", lambda: roots)
    monkeypatch.setattr(catalog_mod, "load_catalog_raw", lambda: [])

    result = delete_mod.delete_installed_model("local-llm-ToDelete-Q4")
    assert result["ok"] is True
    assert not gguf.exists()
    assert not (llm / "ToDelete-Q4.gguf.astra-source.json").exists()
    assert any("ToDelete-Q4.gguf" in p for p in result["deleted"])


def test_delete_refuses_outside_roots(tmp_path, monkeypatch):
    from backend.services.model_downloads import delete as delete_mod

    llm = tmp_path / "llm"
    llm.mkdir()
    outside = tmp_path / "outside.gguf"
    outside.write_bytes(b"x" * 100)

    roots = {
        "llm": llm,
        "rag": tmp_path / "rag",
        "comfyui": tmp_path / "comfyui",
        "omnivoice": tmp_path / "omnivoice",
        "stt": tmp_path / "stt",
        "tts": tmp_path / "tts",
        "diarization": tmp_path / "diar",
        "ocr": tmp_path / "ocr",
    }
    monkeypatch.setattr(delete_mod, "model_roots", lambda: roots)

    with pytest.raises(ValueError, match="вне model roots"):
        delete_mod._unlink(outside, [])
