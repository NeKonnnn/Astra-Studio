# -*- coding: utf-8 -*-
"""Общее тестовое окружение backend: sys.path и минимальный env для Settings."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Settings.from_yaml требует USE_LLM_SVC; без него импорт backend.* падает.
os.environ.setdefault("USE_LLM_SVC", "false")

# backend.auth.jwt_handler читает JWT-настройки при импорте (синтетические тестовые значения).
os.environ.setdefault("JWT_SECRET", "test-access-secret-not-for-production-0123456789")
os.environ.setdefault("JWT_REFRESH_SECRET", "test-refresh-secret-not-for-production-0123456789")
os.environ.setdefault("JWT_ACCESS_TOKEN_EXPIRE_SECONDS", "900")
os.environ.setdefault("JWT_REFRESH_TOKEN_EXPIRE_SECONDS", "3600")
os.environ.setdefault("AUTH_MAX_ACTIVE_SESSION", "3")
