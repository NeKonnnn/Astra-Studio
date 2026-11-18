"""Каталог интентов для rule-matching и LLM-подсказки."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

INTENT_DESCRIPTIONS: Dict[str, str] = {
    "newChat": "Создать новый чат",
    "searchChats": "Открыть поиск по чатам",
    "deleteChat": "Удалить текущий чат",
    "openSettings": "Открыть настройки (params.section: general|profile|interface|chats|models|rag|transcription|voiceAssistant|about)",
    "openTools": "Открыть меню Инструменты",
    "openToolsPanel": "Открыть раздел инструментов (params.panel: agents|artifacts|skills|knowledge-bases|mcp|coding|generation|model-mode)",
    "openAgentConstructor": "Открыть конструктор агента",
    "openTranscription": "Открыть транскрибатор",
    "openSkills": "Открыть редактор skills",
    "openKnowledgeBases": "Открыть редактор баз знаний",
    "openProject": "Открыть проект по имени (params.projectName)",
    "createProject": "Открыть меню создания проекта",
    "sendMessage": "Отправить сообщение в чат (params.text)",
    "stopGeneration": "Остановить генерацию ответа",
    "toggleMcp": "Включить/выключить MCP сервер (params.server, params.enabled)",
    "selectAgent": "Выбрать агента по имени (params.agentName)",
    "selectSkill": "Выбрать skill/навык по имени (params.skillName)",
    "selectKnowledgeBase": "Выбрать базу знаний по имени (params.knowledgeBaseName)",
    "clearAgent": "Сбросить выбранного агента",
    "navigate": "Перейти на страницу (params.path: /, /agents, /skills, /knowledge-bases, /plugins, /admin)",
    "readLastReply": "Озвучить последний ответ ассистента",
    "disableAssistant": "Выключить голосовой помощник",
    "unknown": "Команда не распознана",
}

CONFIRM_PHRASES: Dict[str, str] = {
    "newChat": "Создаю чат",
    "searchChats": "Открываю поиск",
    "deleteChat": "Удаляю чат",
    "openSettings": "Открываю настройки",
    "openTools": "Открываю инструменты",
    "openToolsPanel": "Открываю раздел",
    "openAgentConstructor": "Открываю конструктор агента",
    "openTranscription": "Открываю транскрибатор",
    "openSkills": "Открываю редактор навыков",
    "openKnowledgeBases": "Открываю редактор баз знаний",
    "openProject": "Открываю проект",
    "createProject": "Открываю создание проекта",
    "sendMessage": "Отправляю",
    "stopGeneration": "Останавливаю",
    "toggleMcp": "Обновляю MCP",
    "selectAgent": "Выбираю агента",
    "selectSkill": "Выбираю навык",
    "selectKnowledgeBase": "Выбираю базу знаний",
    "clearAgent": "Агент сброшен",
    "navigate": "Перехожу",
    "readLastReply": "Читаю ответ",
    "disableAssistant": "Голосовой помощник выключен",
    "unknown": "Не поняла",
}


def _norm(text: str) -> str:
    t = (text or "").lower().replace("ё", "е")
    t = re.sub(r"[^\w\s]+", " ", t, flags=re.UNICODE)
    return re.sub(r"\s+", " ", t).strip()


def _settings_section(text: str) -> Optional[str]:
    if "голос" in text:
        return "voiceAssistant"
    if "общ" in text:
        return "general"
    if "профил" in text:
        return "profile"
    if "интерф" in text:
        return "interface"
    if "чат" in text:
        return "chats"
    if "модел" in text:
        return "models"
    if re.search(r"\brag\b", text):
        return "rag"
    if "транскриб" in text:
        return "transcription"
    if "о приложении" in text or text == "about":
        return "about"
    return None


def _tools_panel(text: str) -> str:
    if "артефакт" in text:
        return "artifacts"
    if "skill" in text or "навык" in text:
        return "skills"
    if "баз" in text or "knowledge" in text:
        return "knowledge-bases"
    if re.search(r"\bmcp\b", text):
        return "mcp"
    if "coding" in text or "кодинг" in text or "код" in text:
        return "coding"
    if "генерац" in text:
        return "generation"
    if "режим модели" in text or "model mode" in text or "мышлен" in text:
        return "model-mode"
    if "агент" in text:
        return "agents"
    return "main"


def _build_rules() -> List[Tuple[str, re.Pattern[str], Any]]:
    return [
        ("disableAssistant", re.compile(r"^(выключи|отключи)\s+(голосового\s+)?(помощника|ассистента)$"), None),
        ("stopGeneration", re.compile(r"^(останови|стоп|прекрати)(\s+(генерацию|ответ))?$"), None),
        ("createProject", re.compile(r"^(создай|создать|новый)\s+проект$|^(открой|открыть)\s+(меню\s+)?(создания\s+)?проекта$"), None),
        (
            "openProject",
            re.compile(r"^(открой|открыть|перейди\s+в|покажи)\s+проект\s+(.+)$|^проект\s+(.+)$"),
            lambda m, t: {"projectName": (m.group(2) or m.group(3) or "").strip()},
        ),
        (
            "openSettings",
            re.compile(
                r"^(открой|открыть)\s+настройки(\s+(общее|общие|профиль|интерфейс|чаты|модели|rag|транскрибаци\w*|голосовой\s+помощник|о\s+приложении))?$"
            ),
            lambda m, t: ({"section": s} if (s := _settings_section(t)) else {}),
        ),
        (
            "openToolsPanel",
            re.compile(
                r"^(открой|открыть|покажи)\s+(в\s+инструментах\s+)?"
                r"(агентов|агенты|артефакты|skills?|навыки|базы\s+знаний|базу\s+знаний|mcp|coding|кодинг|"
                r"режим\s+генерации|генерацию|режим\s+модели)$"
                r"|^(открой|открыть)\s+инструменты\s+"
                r"(агентов|агенты|артефакты|skills?|навыки|базы\s+знаний|mcp|coding|кодинг|генераци\w*|режим\s+модели)$"
            ),
            lambda m, t: {"panel": _tools_panel(t), "label": _tools_panel(t)},
        ),
        ("openTools", re.compile(r"^(открой|открыть|покажи)\s+инструменты$|^инструменты$"), None),
        ("newChat", re.compile(r"^(создай|открой|новый)\s+(чат|диалог)$"), None),
        ("searchChats", re.compile(r"^(найди|поиск)\s+(в\s+)?чат"), None),
        ("deleteChat", re.compile(r"^(удали|удалить)\s+(текущий\s+)?чат$"), None),
        ("openAgentConstructor", re.compile(r"^(открой|открыть)\s+конструктор\s+агента$"), None),
        ("openTranscription", re.compile(r"^(открой|открыть)\s+транскриб"), None),
        ("openSkills", re.compile(r"^(открой|открыть)\s+(редактор|сайдбар)\s+(skills|навык)"), None),
        ("openKnowledgeBases", re.compile(r"^(открой|открыть)\s+(редактор|сайдбар)\s+(баз|knowledge)"), None),
        ("clearAgent", re.compile(r"^(сбрось|убери|отключи|очисти)\s+агента$"), None),
        ("readLastReply", re.compile(r"^(прочитай|озвучь).*(ответ|сообщение)"), None),
        (
            "navigate",
            re.compile(
                r"^(открой|перейди|покажи)\s+(галерею\s+агентов|страницу\s+агентов|страницу\s+skills|"
                r"страницу\s+навыков|страницу\s+баз|плагины|админк|главн|чат)"
            ),
            lambda m, t: _nav_params(t),
        ),
        (
            "selectAgent",
            re.compile(r"^(выбери|включи|поставь|используй|открой|открыть)\s+агента\s+(.+)$"),
            lambda m, t: {"agentName": (m.group(2) or "").strip()},
        ),
        (
            "selectSkill",
            re.compile(r"^(выбери|включи|поставь|используй|открой|открыть)\s+(skill|скилл|навык)\s+(.+)$"),
            lambda m, t: {"skillName": (m.group(3) or "").strip(), "enabled": True},
        ),
        (
            "selectKnowledgeBase",
            re.compile(
                r"^(выбери|включи|поставь|используй|открой|открыть)\s+(базу\s+знаний|базу|knowledge\s*base)\s+(.+)$"
            ),
            lambda m, t: {"knowledgeBaseName": (m.group(3) or "").strip(), "enabled": True},
        ),
        (
            "toggleMcp",
            re.compile(
                r"^(открой|открыть|используй|выбери)\s+mcp(\s+сервер(а)?)?\s+(.+)$|"
                r"^(включи|подключи|выключи|отключи)\s+mcp(\s+сервер(а)?)?\s+(.+)$|"
                r"^(enable|disable)\s+mcp\s+(.+)$"
            ),
            lambda m, t: _mcp_params(m),
        ),
        (
            "sendMessage",
            re.compile(r"^(напиши|отправь|скажи)\s+(.+)$"),
            lambda m, t: {"text": (m.group(2) or "").strip()},
        ),
    ]


def _mcp_params(m: re.Match[str]) -> Dict[str, Any]:
    # Альтернативы: (1,4) | (5,8) | (9,10) — см. группы в regex toggleMcp
    action = m.group(1) or m.group(5) or m.group(9) or ""
    server = (m.group(4) or m.group(8) or m.group(10) or "").strip()
    server = re.sub(r"^(mcp\s+|сервер(а)?\s+|server\s+)", "", server, flags=re.I)
    enabled = bool(re.search(r"включи|подключи|открой|открыть|используй|выбери|enable", action, re.I))
    return {"enabled": enabled, "server": server}


def _nav_params(text: str) -> Dict[str, Any]:
    if "агент" in text:
        return {"path": "/agents", "label": "агенты"}
    if "skill" in text or "навык" in text:
        return {"path": "/skills", "label": "навыки"}
    if "баз" in text or "knowledge" in text:
        return {"path": "/knowledge-bases", "label": "базы знаний"}
    if "плагин" in text or "plugin" in text:
        return {"path": "/plugins", "label": "плагины"}
    if "админ" in text or "admin" in text:
        return {"path": "/admin", "label": "админка"}
    return {"path": "/", "label": "чат"}


def match_rules(text: str) -> Optional[Dict[str, Any]]:
    normalized = _norm(text)
    if not normalized:
        return None
    for intent_id, pattern, extractor in _build_rules():
        m = pattern.search(normalized)
        if not m:
            continue
        params = extractor(m, normalized) if extractor else {}
        return {
            "intent": intent_id,
            "params": params or {},
            "confidence": 0.9,
            "confirm_phrase": CONFIRM_PHRASES.get(intent_id, "Готово"),
        }
    return None


def llm_system_prompt() -> str:
    lines = ["Доступные интенты:"]
    for k, v in INTENT_DESCRIPTIONS.items():
        if k == "unknown":
            continue
        lines.append(f"- {k}: {v}")
    return (
        "Ты классификатор голосовых команд приложения AstraChat. "
        "Верни ТОЛЬКО JSON без markdown: "
        '{"intent":"...","params":{},"confidence":0.0,"confirm_phrase":"..."}. '
        "Если не уверен — intent=unknown.\n" + "\n".join(lines)
    )
