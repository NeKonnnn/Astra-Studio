"""API smoke для /api/model-downloads (admin-only, без реальной сети)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routes import model_downloads as module
from backend.services.model_downloads.job_manager import DownloadJob, get_job_manager


pytestmark = pytest.mark.unit


def _app_with_user(is_admin: bool = True):
    app = FastAPI()
    app.include_router(module.router)

    async def fake_user():
        return {"user_id": "u1", "username": "admin" if is_admin else "user", "is_admin": is_admin}

    app.dependency_overrides[module.get_current_user] = fake_user
    return app


def test_catalog_requires_admin():
    client = TestClient(_app_with_user(is_admin=False))
    r = client.get("/api/model-downloads/catalog")
    assert r.status_code == 403


def test_catalog_ok_for_admin():
    client = TestClient(_app_with_user(is_admin=True))
    r = client.get("/api/model-downloads/catalog")
    assert r.status_code == 200
    data = r.json()
    assert "models" in data
    assert isinstance(data["models"], list)
    assert any(m.get("id") for m in data["models"])


def test_hf_token_mask_roundtrip(monkeypatch, tmp_path):
    token_file = tmp_path / "hf_token.txt"
    monkeypatch.setattr(
        "backend.services.model_downloads.token_store._token_file",
        lambda: token_file,
    )
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)

    client = TestClient(_app_with_user(is_admin=True))
    r = client.put("/api/model-downloads/hf-token", json={"token": "hf_secret_token_xx"})
    assert r.status_code == 200
    assert r.json()["configured"] is True
    assert "hf_s" in r.json()["masked"]

    g = client.get("/api/model-downloads/hf-token")
    assert g.json()["configured"] is True


def test_start_catalog_mocked(monkeypatch):
    mgr = get_job_manager()

    def fake_start(catalog_id, apply=True):
        job = DownloadJob(
            id="job-1",
            status="queued",
            model_type="llm_gguf",
            display_name="test",
            catalog_id=catalog_id,
        )
        mgr._jobs[job.id] = job
        return job

    monkeypatch.setattr(mgr, "start_from_catalog", fake_start)
    client = TestClient(_app_with_user(is_admin=True))
    r = client.post("/api/model-downloads/start", json={"catalog_id": "frida-embed"})
    assert r.status_code == 200
    assert r.json()["id"] == "job-1"

    listed = client.get("/api/model-downloads")
    assert listed.status_code == 200
    assert any(j["id"] == "job-1" for j in listed.json()["jobs"])
