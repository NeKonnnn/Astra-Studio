"""
Тонкий async-клиент для SVC-RAG-MODELS (эмбеддинги и cross-encoder).
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

import httpx

from backend.settings.config import get_settings
from backend.settings.logging import get_logger

logger = get_logger(__name__)


def _rag_models_request_url(base_url: str, path: str) -> str:
    b = (base_url or "").strip().rstrip("/")
    p = path if path.startswith("/") else f"/{path}"
    return f"{b}/v1{p}"


class RagModelsClient:
    def __init__(self, base_url: Optional[str] = None, timeout: float = 600.0):
        # LLM-реранкеры (MiniCPM ~2B) на CPU грузятся минутами — 120с мало.
        settings = get_settings()
        if base_url:
            self.base_url = base_url.strip().rstrip("/")
        else:
            env_url = os.getenv("RAG_MODELS_SERVICE_URL", "").strip()
            if env_url:
                self.base_url = env_url.rstrip("/")
            else:
                try:
                    self.base_url = settings.microservice_http_base(
                        "rag_models_service_docker",
                        "rag_models_service_port",
                    )
                except ValueError:
                    self.base_url = "http://localhost:8010"
        env_timeout = os.getenv("RAG_MODELS_HTTP_TIMEOUT", "").strip()
        self.timeout = float(env_timeout) if env_timeout else timeout


    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        url = _rag_models_request_url(self.base_url, path)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.request(method=method, url=url, json=json, params=params)
            if resp.is_error:
                detail = None
                try:
                    body = resp.json()
                    detail = body.get("detail") or body.get("message")
                    if isinstance(detail, list):
                        detail = "; ".join(str(x) for x in detail)
                except Exception:
                    detail = (resp.text or "").strip()[:500] or None
                msg = f"{resp.status_code} {resp.reason_phrase}"
                if detail:
                    msg = f"{msg}: {detail}"
                raise httpx.HTTPStatusError(msg, request=resp.request, response=resp)
            return resp.json()

    async def list_models(self, model_type: Optional[str] = None) -> Dict[str, Any]:
        params = {"type": model_type} if model_type else None
        return await self._request("GET", "/models", params=params)

    async def get_current(self) -> Dict[str, Any]:
        return await self._request("GET", "/models/current")

    async def select_model(self, model_type: str, model_path: str) -> Dict[str, Any]:
        return await self._request(
            "POST",
            "/models/select",
            json={"model_type": model_type, "model_path": model_path},
        )

    async def health(self) -> Dict[str, Any]:
        return await self._request("GET", "/health")


_rag_models_client_singleton: Optional[RagModelsClient] = None


def get_rag_models_client() -> RagModelsClient:
    global _rag_models_client_singleton
    if _rag_models_client_singleton is None:
        _rag_models_client_singleton = RagModelsClient()
    return _rag_models_client_singleton
