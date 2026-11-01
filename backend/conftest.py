"""Корневой conftest: обход сломанного junction backend/backend при сборе pytest на Windows."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.hookimpl(tryfirst=True)
def pytest_ignore_collect(collection_path, config):
    try:
        path = Path(collection_path)
        if path.name == "backend" and path.parent.name == "backend":
            return True
        path.stat()
    except OSError:
        return True
    return None
