# -*- coding: utf-8 -*-
"""Корневой conftest llm-svc: заглушка нативного llama_cpp для окружений без него.

app.services импортирует llama_handler, а тот — llama_cpp (нативная сборка).
Для юнит-тестов реальный пакет не нужен: все вызовы модели мокаются.
"""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock

try:  # pragma: no cover - зависит от окружения
    import llama_cpp  # noqa: F401
except ImportError:
    stub = ModuleType("llama_cpp")
    stub.Llama = MagicMock(name="Llama")
    chat_format = ModuleType("llama_cpp.llama_chat_format")
    chat_format.Jinja2ChatFormatter = MagicMock(name="Jinja2ChatFormatter")
    stub.llama_chat_format = chat_format
    sys.modules["llama_cpp"] = stub
    sys.modules["llama_cpp.llama_chat_format"] = chat_format
