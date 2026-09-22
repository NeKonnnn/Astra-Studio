"""
socket_helpers.py - утилиты, общие для socket_handlers и роутеров
"""

import json
from typing import Any, List, Optional, Tuple

from backend.settings.logging import get_logger
from backend.settings.rag_client import RagReindexInProgress

logger = get_logger(__name__)

# Заглушка конструктора агентов. Карточка требует промпт длиной от 10 символов
# (AgentCreate.system_prompt, min_length=10), поэтому за пустое поле фронт
# подставляет эту фразу - и дальше она жила как настоящая инструкция агента
AGENT_PROMPT_PLACEHOLDER = "Системные инструкции не заданы."


def _is_structure_query(text: str) -> bool:
    """Запрос про оглавление/структуру/главы - добавляем начало документа в RAG"""
    if not text or len(text.strip()) < 3:
        return False
    t = text.lower().strip()
    keywords = (
        "оглавление",
        "содержание",
        "главы",
        "глава",
        "пункт",
        "подпункт",
        "структура работы",
        "структуру работы",
        "названия глав",
        "какие главы",
    )
    return any((k in t for k in keywords))


def _terminal_chat_inference_banner(
    *,
    sid: str,
    conversation_id,
    user_preview: str,
    mode_label: str,
    model_path_for_call: str = None,
    extra_line: str = None,
    enable_thinking: Optional[bool] = None,
):
    import backend.app_state as _state

    lines = [
        "",
        "=" * 76,
        "  [ЧАТ] Генерация ответа - что использует сервер СЕЙЧАС",
        "=" * 76,
        f"  Режим: {mode_label}",
        f"  Socket: {str(sid)[:20]}…  |  conversation_id: {conversation_id}",
        f"  Текст запроса (начало): {(user_preview or '')[:160]!r}",
    ]
    path = model_path_for_call if model_path_for_call is not None else _state.get_current_model_path()
    lines.append(f"  Модель (путь для вызова LLM): {path!r}")
    if enable_thinking is None:
        lines.append("  enable_thinking: не передан в баннер (не socket / старый вызов)")
    else:
        lines.append(f"  enable_thinking (режим «Мышление» и т.п.): {enable_thinking!r}")
    if _state.get_model_info:
        try:
            info = _state.get_model_info()
            if info:
                lines.append(f"  get_model_info: {json.dumps(info, ensure_ascii=False, default=str)[:500]}")
        except Exception:
            logger.exception("get_model_info: ошибка")
            lines.append("  get_model_info: ошибка")
    if _state.model_settings:
        try:
            st = _state.model_settings.get_all()
            lines.append(f"  Настройки модели: {json.dumps(st, ensure_ascii=False, default=str)}")
        except Exception:
            logger.exception("Настройки модели: недоступны")
            lines.append("  Настройки модели: недоступны")
    else:
        lines.append("  Настройки модели: модуль недоступен")
    if _state.context_prompt_manager:
        try:
            gp = _state.context_prompt_manager.get_global_prompt() or ""
            prev = gp[:500] + ("…" if len(gp) > 500 else "")
            lines.append(f"  Глобальный системный промпт ({len(gp)} симв., начало): {prev!r}")
        except Exception:
            logger.exception("Глобальный промпт: ошибка")
            lines.append("  Глобальный промпт: ошибка")
    else:
        lines.append("  Глобальный промпт: менеджер недоступен")
    if extra_line:
        lines.append(f"  {extra_line}")
    lines.append("  Примечание: блок get_model_info - глобальное состояние llm-svc (что загружено в память)")
    lines.append(
        "  Реальный ответ строится по полю model в POST /v1/chat/completions (см. лог generate_response выше);"
    )
    lines.append("  при выбранном агенте туда подставляется модель из конструктора (llm-svc://id)")
    lines.append("=" * 76)
    for line in lines:
        logger.info("%s", line)


async def kb_search_agent_documents(
    rag_client: Any,
    query: str,
    kb_doc_ids: List[int],
    k: int = 8,
    strategy: Optional[str] = None,
) -> List[Tuple[str, float, Optional[int], Optional[int]]]:
    """Поиск по KB только внутри document_id, привязанных к агенту."""
    if not rag_client or not kb_doc_ids:
        return []
    ids: List[int] = []
    for raw in kb_doc_ids:
        try:
            ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    if not ids:
        return []
    # ОДИН запрос на все документы агента: раньше был цикл, и svc-rag заново
    # считал эмбеддинг вопроса для каждого файла (с тяжёлой моделью — минуты).
    # Заодно top-k теперь считается по корпусу агента, а не по каждому файлу.
    try:
        hits = await rag_client.kb_search(
            query,
            k=max(1, k),
            document_ids=ids,
            strategy=strategy,
        )
    except RagReindexInProgress:
        raise
    except Exception:
        logger.exception("KB search по документам агента %s", ids)
        return []
    return list(hits)[:k]


async def _resolve_agent_chat_params(
    agent_id_raw, user_id=None, user: Optional[dict] = None
) -> dict:
    """Модель и параметры из карточки агента (конструктор)."""
    empty = {
        "name": None,
        "model_path": None,
        "max_tokens": None,
        "temperature": None,
        "system_prompt": None,
        "file_search_enabled": False,
        "kb_document_ids": [],
        "skill_ids": [],
        "skills_enabled": False,
        "mcp_enabled": False,
        "mcp_server_ids": [],
        "plugins_enabled": False,
        "plugin_ids": [],
        "artifacts_enabled": False,
        "shadcn_enabled": False,
        "user_prompt_mode": False,
        "agent_id": None,
        "agent_ids": [],
        "hide_sequential_outputs": False,
        "shared_chain_rag": False,
        "max_chain_agents": None,
        "max_subagents": None,
        "recursion_limit": None,
        "subagents": None,
    }
    if agent_id_raw is None:
        return empty
    try:
        aid = int(agent_id_raw)
    except (TypeError, ValueError):
        return empty
    try:
        from backend.database.init_db import get_agent_repository

        repo = get_agent_repository()
        if repo is None:
            return empty
        if not await repo.user_can_access_agent(aid, user_id):
            logger.warning(f"[chat] нет доступа к agent_id={aid} для user={user_id}")
            return empty
        ag = await repo.get_agent(aid, user_id)
        if not ag:
            return empty
        cfg = ag.config if isinstance(ag.config, dict) else {}
        mp = str(cfg.get("model") or cfg.get("model_path") or "").strip()
        out = {**empty, "name": (ag.name or "").strip() or None, "agent_id": aid}
        if mp:
            low = mp.lower()
            if low.startswith("1lm-svc://"):
                mp = "llm-svc://" + mp[10:]
                low = mp.lower()
            if low.startswith("llm-svc://"):
                out["model_path"] = mp
            elif "/" in mp or mp.lower().endswith(".gguf") or (len(mp) > 2 and mp[1] == ":"):
                out["model_path"] = mp
            else:
                out["model_path"] = f"llm-svc://{mp}"
        ms = cfg.get("model_settings")
        if isinstance(ms, dict):
            out["model_settings"] = dict(ms)
            if ms.get("output_tokens") is not None:
                try:
                    out["max_tokens"] = int(ms["output_tokens"])
                except (TypeError, ValueError):
                    pass
            if ms.get("temperature") is not None:
                try:
                    out["temperature"] = float(ms["temperature"])
                except (TypeError, ValueError):
                    pass
        sp = (ag.system_prompt or "").strip()
        # Заглушку отсеиваем здесь, а не у каждого потребителя: из этого поля
        # промпт агента забирают и обычный чат, и мульти-LLM, и agent mode
        # Фильтр стоял только в agent mode, в остальных фраза уезжала в модель
        # как инструкция, а после коммита 5 ещё и с приоритетом над правилами
        if sp and sp != AGENT_PROMPT_PLACEHOLDER:
            out["system_prompt"] = sp
        out["file_search_enabled"] = bool(cfg.get("file_search_enabled", False))
        raw_kb_ids = cfg.get("kb_document_ids")
        if isinstance(raw_kb_ids, list):
            kb_ids: List[int] = []
            for v in raw_kb_ids:
                try:
                    kb_ids.append(int(v))
                except (TypeError, ValueError):
                    continue
            out["kb_document_ids"] = sorted(set(kb_ids))
        raw_skill_ids = cfg.get("skill_ids") or cfg.get("skills")
        if isinstance(raw_skill_ids, list):
            out["skill_ids"] = [str(v).strip() for v in raw_skill_ids if str(v).strip()]
        if "skills_enabled" in cfg:
            out["skills_enabled"] = bool(cfg.get("skills_enabled"))
        else:
            out["skills_enabled"] = bool(out["skill_ids"])
        out["mcp_enabled"] = bool(cfg.get("mcp_enabled", False))
        raw_mcp_ids = cfg.get("mcp_server_ids")
        if isinstance(raw_mcp_ids, list):
            out["mcp_server_ids"] = [str(v).strip() for v in raw_mcp_ids if str(v).strip()]
        raw_plugin_ids = cfg.get("plugin_ids")
        if isinstance(raw_plugin_ids, list):
            out["plugin_ids"] = [str(v).strip() for v in raw_plugin_ids if str(v).strip()]
        if "plugins_enabled" in cfg:
            out["plugins_enabled"] = bool(cfg.get("plugins_enabled"))
        else:
            out["plugins_enabled"] = bool(out["plugin_ids"])
        out["artifacts_enabled"] = bool(cfg.get("artifacts_enabled", False))
        out["shadcn_enabled"] = bool(cfg.get("shadcn_enabled", False))
        out["user_prompt_mode"] = bool(cfg.get("user_prompt_mode", False))
        from backend.agents.chain import parse_agent_ids

        for limit_key in ("max_chain_agents", "max_subagents"):
            raw_limit = cfg.get(limit_key)
            if isinstance(raw_limit, int) and raw_limit > 0:
                out[limit_key] = raw_limit
            elif isinstance(raw_limit, str) and raw_limit.strip().isdigit():
                parsed_limit = int(raw_limit.strip())
                if parsed_limit > 0:
                    out[limit_key] = parsed_limit
        out["agent_ids"] = parse_agent_ids(
            cfg.get("agent_ids"), exclude_id=aid, agent_profile=out
        )
        out["hide_sequential_outputs"] = bool(cfg.get("hide_sequential_outputs", False))
        out["shared_chain_rag"] = bool(cfg.get("shared_chain_rag", False))
        raw_recursion = cfg.get("recursion_limit")
        if isinstance(raw_recursion, int) and raw_recursion > 0:
            out["recursion_limit"] = raw_recursion
        elif isinstance(raw_recursion, str) and raw_recursion.strip().isdigit():
            parsed = int(raw_recursion.strip())
            if parsed > 0:
                out["recursion_limit"] = parsed
        if isinstance(cfg.get("subagents"), dict):
            out["subagents"] = dict(cfg["subagents"])
        logger.info(
            f"[chat] agent_id={aid} → model_path={out['model_path']}, "
            f"max_tokens={out['max_tokens']}, temperature={out['temperature']}, "
            f"file_search={out['file_search_enabled']}, "
            f"kb_document_ids={out['kb_document_ids']}, "
            f"mcp_enabled={out['mcp_enabled']}, mcp_server_ids={out['mcp_server_ids']}, "
            f"plugins_enabled={out['plugins_enabled']}, plugin_ids={out['plugin_ids']}, "
            f"artifacts_enabled={out['artifacts_enabled']}, "
            f"shadcn_enabled={out['shadcn_enabled']}, "
            f"user_prompt_mode={out['user_prompt_mode']}, "
            f"agent_ids={out['agent_ids']}, hide_sequential={out['hide_sequential_outputs']}, "
            f"shared_chain_rag={out['shared_chain_rag']}, "
            f"recursion_limit={out.get('recursion_limit')}, "
            f"subagents_enabled={bool((out.get('subagents') or {}).get('enabled'))}"
        )
        return out
    except Exception:
        logger.exception("_resolve_agent_chat_params")
        return empty


def merge_chat_artifacts_settings(agent_profile: dict, data: dict) -> dict:
    """Пер-чат override артефактов из UI «Инструменты → Артефакты»."""
    raw = data.get("artifacts_settings") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return agent_profile
    out = dict(agent_profile) if isinstance(agent_profile, dict) else {}
    if "artifacts_enabled" in raw:
        out["artifacts_enabled"] = bool(raw.get("artifacts_enabled"))
    if "shadcn_enabled" in raw:
        out["shadcn_enabled"] = bool(raw.get("shadcn_enabled"))
    if "user_prompt_mode" in raw:
        out["user_prompt_mode"] = bool(raw.get("user_prompt_mode"))
    return out


def agent_mcp_tool_ids(agent_profile: dict) -> List[str]:
    """tool_ids из config агента (server:mcp:{id})."""
    if not isinstance(agent_profile, dict) or not agent_profile.get("mcp_enabled"):
        return []
    raw = agent_profile.get("mcp_server_ids") or []
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for sid in raw:
        s = str(sid).strip()
        if s:
            tid = f"server:mcp:{s}"
            if tid not in out:
                out.append(tid)
    return out


def agent_plugin_ids(agent_profile: dict) -> List[str]:
    """Активные plugin_ids из карточки агента."""
    if not isinstance(agent_profile, dict):
        return []
    enabled = agent_profile.get("plugins_enabled")
    raw = agent_profile.get("plugin_ids") or []
    if not isinstance(raw, list):
        raw = []
    ids = [str(v).strip() for v in raw if str(v).strip()]
    if enabled is None:
        enabled = bool(ids)
    if not enabled:
        return []
    return ids
