"""Import official PyPI ``mcp`` SDK without shadowing by ``backend/mcp`` on sys.path."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

_BACKEND_ROOT = Path(__file__).resolve().parent.parent


def _is_local_shadow(mod: ModuleType) -> bool:
    mod_file = getattr(mod, "__file__", None)
    if mod_file:
        normalized = Path(mod_file).resolve().as_posix()
        local_root = (_BACKEND_ROOT / "mcp").resolve().as_posix()
        return normalized == local_root or normalized.startswith(f"{local_root}/")
    return mod.__name__ == "mcp" and not hasattr(mod, "ClientSession")


def _purge_shadowed_mcp_modules() -> None:
    for key in list(sys.modules):
        if key != "mcp" and not key.startswith("mcp."):
            continue
        mod = sys.modules.get(key)
        if mod is None or _is_local_shadow(mod):
            del sys.modules[key]


def import_mcp_sdk() -> ModuleType:
    """Return the installed ``mcp`` package from site-packages."""
    existing = sys.modules.get("mcp")
    if existing is not None and not _is_local_shadow(existing):
        return existing

    _purge_shadowed_mcp_modules()
    backend_path = _BACKEND_ROOT.resolve().as_posix()
    saved_path = sys.path[:]
    try:
        sys.path = [p for p in sys.path if Path(p).resolve().as_posix() != backend_path]
        return importlib.import_module("mcp")
    finally:
        sys.path = saved_path


def import_mcp_sdk_submodule(dotted: str) -> ModuleType:
    """Import ``mcp.client.stdio`` and other SDK submodules from site-packages."""
    import_mcp_sdk()
    return importlib.import_module(dotted)
