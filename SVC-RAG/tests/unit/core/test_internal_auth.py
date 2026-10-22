"""core/internal_auth: общий секрет для вызовов SVC-RAG → backend /api/internal/rag/*."""

import pytest

from app.core import internal_auth as ia

pytestmark = pytest.mark.unit


def test_no_secret_means_no_header(monkeypatch):
    monkeypatch.delenv("INTERNAL_RAG_SECRET", raising=False)

    assert ia.internal_rag_headers() == {}


def test_secret_is_added_to_the_headers(monkeypatch):
    monkeypatch.setenv("INTERNAL_RAG_SECRET", "  s3cret  ")

    assert ia.internal_rag_headers() == {"X-Internal-Rag-Secret": "s3cret"}


def test_extra_headers_are_kept_and_not_mutated(monkeypatch):
    monkeypatch.setenv("INTERNAL_RAG_SECRET", "s3cret")
    extra = {"X-CEF-Suser": "ivan"}

    out = ia.internal_rag_headers(extra)

    assert out == {"X-CEF-Suser": "ivan", "X-Internal-Rag-Secret": "s3cret"}
    assert extra == {"X-CEF-Suser": "ivan"}


def test_blank_secret_is_treated_as_unset(monkeypatch):
    monkeypatch.setenv("INTERNAL_RAG_SECRET", "   ")

    assert ia.internal_rag_headers() == {}
