"""Plugins platform: list / health / invoke (HTTP proxy)."""

from __future__ import annotations

import re
from time import monotonic
from typing import Any, Dict, List, Optional, Tuple

import httpx

from backend.plugins.registry import PluginsRegistry
from backend.plugins.types import PluginHealthResult, PluginPublic
from backend.settings.config import PluginConfig, Settings, get_settings
from backend.settings.logging import get_logger

log = get_logger(__name__)

_platform: Optional["PluginsPlatformService"] = None


class PluginInvokeTimeout(TimeoutError):
    """Плагин не ответил за timeout_seconds — сервис может всё ещё считать."""


def _timeout_env_key(plugin_id: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", plugin_id).strip("_").upper()
    return f"PLUGIN_{slug}_TIMEOUT_SECONDS"


def _format_health_error(exc: BaseException, url: str) -> str:
    msg = str(exc).strip()
    if not msg:
        msg = repr(exc).strip("'\"")
    name = type(exc).__name__
    if name in msg:
        return f"{msg} (url={url})"
    return f"{name}: {msg} (url={url})"


class PluginsPlatformService:
    def __init__(self, settings: Optional[Settings] = None):
        self._settings = settings or get_settings()
        self._registry = PluginsRegistry(self._settings)
        self._initialized = False

    @property
    def initialized(self) -> bool:
        return self._initialized

    @property
    def enabled(self) -> bool:
        return self._registry.enabled

    def initialize(self) -> None:
        if self._initialized:
            return
        self._registry.initialize()
        self._initialized = True
        plugins = self._registry.list_plugins()
        log.info(
            "Plugins platform initialized: enabled=%s count=%s ids=%s",
            self._registry.enabled,
            len(plugins),
            [p.id for p in plugins],
        )
        for plugin in plugins:
            try:
                base = self.resolve_base_url(plugin)
                log.info(
                    "Plugin catalog entry id=%s enabled=%s base_url=%s health=%s invoke=%s",
                    plugin.id,
                    plugin.enabled,
                    base,
                    plugin.health_path or "/health",
                    plugin.invoke_path or "/audit",
                )
            except Exception as e:
                log.warning(
                    "Plugin catalog entry id=%s enabled=%s base_url unresolved: %s",
                    plugin.id,
                    plugin.enabled,
                    e,
                )

    def list_plugins(self) -> List[PluginConfig]:
        return self._registry.list_plugins()

    def list_enabled_plugins(self) -> List[PluginConfig]:
        return self._registry.list_enabled_plugins()

    def get_plugin(self, plugin_id: str) -> Optional[PluginConfig]:
        return self._registry.get_plugin(plugin_id)

    def resolve_base_url(self, plugin: PluginConfig) -> str:
        explicit = str(plugin.base_url or "").strip().rstrip("/")
        if explicit:
            log.debug("Plugin %s base_url from catalog: %s", plugin.id, explicit)
            return explicit
        resolved = self._settings.resolve_plugin_base_url(plugin.id)
        log.debug("Plugin %s base_url from urls.*: %s", plugin.id, resolved)
        return resolved

    def to_public(self, plugin: PluginConfig, *, healthy: Optional[bool] = None, health_detail: Optional[Dict[str, Any]] = None) -> PluginPublic:
        return PluginPublic(
            id=plugin.id,
            display_name=plugin.display_name or plugin.id,
            description=plugin.description or "",
            enabled=bool(plugin.enabled),
            kind=plugin.kind or "http",
            category=plugin.category or "",
            tags=list(plugin.tags or []),
            health_path=plugin.health_path or "/health",
            invoke_path=plugin.invoke_path or "/audit",
            healthy=healthy,
            health_detail=health_detail,
        )

    def list_plugins_public(self) -> List[PluginPublic]:
        return [self.to_public(p) for p in self._registry.list_plugins()]

    async def health(self, plugin_id: str) -> PluginHealthResult:
        plugin = self._registry.get_plugin(plugin_id)
        if not plugin:
            log.warning("Plugin health: not found id=%s", plugin_id)
            return PluginHealthResult(id=plugin_id, ok=False, error="Plugin not found")
        if not plugin.enabled:
            log.info("Plugin health: disabled id=%s", plugin_id)
            return PluginHealthResult(id=plugin_id, ok=False, error="Plugin disabled")
        try:
            base = self.resolve_base_url(plugin)
        except Exception as e:
            log.warning("Plugin health: base_url unresolved id=%s err=%s", plugin_id, e)
            return PluginHealthResult(id=plugin_id, ok=False, error=str(e))
        path = str(plugin.health_path or "/health").strip() or "/health"
        if not path.startswith("/"):
            path = f"/{path}"
        url = f"{base}{path}"
        timeout = httpx.Timeout(min(30.0, float(plugin.timeout_seconds or 60)), connect=5.0)
        log.info("Plugin health check start id=%s url=%s", plugin_id, url)
        try:
            async with httpx.AsyncClient(timeout=timeout, verify=True) as client:
                resp = await client.get(url)
            detail: Dict[str, Any] = {}
            try:
                detail = resp.json()
            except Exception:
                detail = {"raw": (resp.text or "")[:500]}
            ok = 200 <= resp.status_code < 300
            log.info(
                "Plugin health check done id=%s url=%s status=%s ok=%s",
                plugin_id,
                url,
                resp.status_code,
                ok,
            )
            return PluginHealthResult(
                id=plugin_id,
                ok=ok,
                status_code=resp.status_code,
                detail=detail if isinstance(detail, dict) else {"value": detail},
                error=None if ok else f"HTTP {resp.status_code} for {url}",
            )
        except Exception as e:
            err = _format_health_error(e, url)
            log.warning("Plugin health failed id=%s url=%s err=%s", plugin_id, url, err)
            return PluginHealthResult(id=plugin_id, ok=False, error=err)

    async def invoke_multipart(
        self,
        plugin_id: str,
        *,
        files: List[Tuple[str, Tuple[str, bytes, str]]],
        form: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Проксирует multipart POST на invoke_path плагина.

        files: список (field_name, (filename, content, content_type))
        """
        plugin = self._registry.require_plugin(plugin_id)
        if not plugin.enabled:
            raise RuntimeError(f"Plugin '{plugin_id}' is disabled")
        base = self.resolve_base_url(plugin)
        path = str(plugin.invoke_path or "/audit").strip() or "/audit"
        if not path.startswith("/"):
            path = f"/{path}"
        url = f"{base}{path}"
        timeout_seconds = float(plugin.timeout_seconds or 600)
        timeout = httpx.Timeout(timeout_seconds, connect=10.0)
        data = {k: v for k, v in (form or {}).items() if v is not None}
        file_names = [meta[0] for _, meta in files]
        file_sizes = [len(meta[1] or b"") for _, meta in files]
        log.info(
            "Plugin invoke start id=%s url=%s files=%s sizes_bytes=%s form_keys=%s timeout=%.0fs",
            plugin_id,
            url,
            file_names,
            file_sizes,
            list(data.keys()),
            timeout_seconds,
        )
        started = monotonic()
        try:
            from backend.services.admin_activity import schedule_record_usage
            from backend.settings.cef_logger.cef_audit_context import cef_audit_peek

            _req, _user, _conv = cef_audit_peek()
            _uid = ""
            if isinstance(_user, dict):
                _uid = str(_user.get("user_id") or _user.get("username") or "")
            schedule_record_usage(
                entity_type="plugin",
                entity_id=str(plugin_id or ""),
                user_id=_uid or "anonymous",
                action="invoked",
                entity_name=str(
                    getattr(plugin, "display_name", None) or plugin_id or ""
                ),
                meta={
                    "conversation_id": _conv,
                    "source": "plugin_invoke_multipart",
                    "files": file_names[:8],
                },
            )
        except Exception:
            pass
        try:
            async with httpx.AsyncClient(timeout=timeout, verify=True) as client:
                resp = await client.post(url, data=data or None, files=files)
        except httpx.TimeoutException as e:
            elapsed = monotonic() - started
            log.warning(
                "Plugin invoke timeout id=%s url=%s elapsed=%.0fs limit=%.0fs kind=%s",
                plugin_id,
                url,
                elapsed,
                timeout_seconds,
                type(e).__name__,
            )
            raise PluginInvokeTimeout(
                f"Плагин '{plugin_id}' не ответил за {timeout_seconds:.0f} с "
                f"(прошло {elapsed:.0f} с, {url}). Сервис, скорее всего, ещё считает: "
                f"увеличьте plugins.catalog.{plugin_id}.timeout_seconds "
                f"(ENV {_timeout_env_key(plugin_id)}) и proxy_read_timeout в nginx."
            ) from e
        except httpx.HTTPError as e:
            elapsed = monotonic() - started
            detail = str(e).strip() or type(e).__name__
            log.warning(
                "Plugin invoke transport error id=%s url=%s elapsed=%.0fs err=%s",
                plugin_id,
                url,
                elapsed,
                detail,
            )
            raise RuntimeError(f"Плагин '{plugin_id}' недоступен: {detail} (url={url})") from e
        log.info(
            "Plugin invoke response id=%s url=%s status=%s elapsed=%.0fs",
            plugin_id,
            url,
            resp.status_code,
            monotonic() - started,
        )
        try:
            payload = resp.json()
        except Exception:
            payload = {"raw": (resp.text or "")[:4000]}
        if resp.status_code >= 400:
            detail = payload.get("detail") if isinstance(payload, dict) else None
            log.warning(
                "Plugin invoke HTTP error id=%s status=%s detail=%s",
                plugin_id,
                resp.status_code,
                detail,
            )
            raise RuntimeError(
                str(detail).strip()
                if detail
                else f"Плагин '{plugin_id}' вернул HTTP {resp.status_code} ({url})"
            )
        if isinstance(payload, dict):
            return payload
        return {"result": payload}


def get_plugins_platform() -> PluginsPlatformService:
    global _platform
    if _platform is None:
        _platform = PluginsPlatformService()
        _platform.initialize()
    return _platform
