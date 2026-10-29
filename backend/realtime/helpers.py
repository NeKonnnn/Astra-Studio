"""
socket_helpers.py - утилиты, общие для socket_handlers и роутеров
"""

import json
from typing import Any, Dict, List, Optional, Tuple

from backend.settings.logging import get_logger

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

    # 10 — это уровень DEBUG (то же, что logging.DEBUG)
    # INFO=20, WARNING=30, ERROR=40
    if not logger.isEnabledFor(10):
        return

    lines = [
        "",
        "=" * 76,
        "  [ЧАТ] Генерация ответа - что использует сервер СЕЙЧАС",
        "=" * 76,
        f"  Режим: {mode_label}",
        f"  Socket: {str(sid)[:20]}…  |  conversation_id: {conversation_id}",
        f"  Текст запроса (начало): {(user_preview or '')[:160]!r}",
    ]
    path = (
        model_path_for_call
        if model_path_for_call is not None
        else _state.get_current_model_path()
    )
    lines.append(f"  Модель (путь для вызова LLM): {path!r}")
    if enable_thinking is None:
        lines.append(
            "  enable_thinking: не передан в баннер (не socket / старый вызов)"
        )
    else:
        lines.append(
            f"  enable_thinking (режим «Мышление» и т.п.): {enable_thinking!r}"
        )
    if _state.get_model_info:
        try:
            info = _state.get_model_info()
            if info:
                lines.append(
                    f"  get_model_info: {json.dumps(info, ensure_ascii=False, default=str)[:500]}"
                )
        except Exception:
            logger.exception("get_model_info: ошибка")
            lines.append("  get_model_info: ошибка")
    if _state.model_settings:
        try:
            st = _state.model_settings.get_all()
            lines.append(
                f"  Настройки модели: {json.dumps(st, ensure_ascii=False, default=str)}"
            )
        except Exception:
            logger.exception("Настройки модели: недоступны")
            lines.append("  Настройки модели: недоступны")
    else:
        lines.append("  Настройки модели: модуль недоступен")
    if _state.context_prompt_manager:
        try:
            gp = _state.context_prompt_manager.get_global_prompt() or ""
            prev = gp[:500] + ("…" if len(gp) > 500 else "")
            lines.append(
                f"  Глобальный системный промпт ({len(gp)} симв., начало): {prev!r}"
            )
        except Exception:
            logger.exception("Глобальный промпт: ошибка")
            lines.append("  Глобальный промпт: ошибка")
    else:
        lines.append("  Глобальный промпт: менеджер недоступен")
    if extra_line:
        lines.append(f"  {extra_line}")
    lines.append(
        "  Примечание: блок get_model_info - глобальное состояние llm-svc (что загружено в память)"
    )
    lines.append(
        "  Реальный ответ строится по полю model в POST /v1/chat/completions (см. лог generate_response выше);"
    )
    lines.append(
        "  при выбранном агенте туда подставляется модель из конструктора (llm-svc://id)"
    )
    lines.append("=" * 76)
    for line in lines:
        logger.debug("%s", line)


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
    except Exception:
        logger.exception("KB search по документам агента %s", ids)
        return []
    return list(hits)[:k]


async def _resolve_agent_chat_params(
    agent_id_raw, user_id=None, user: Optional[dict] = None, *, skip_access_check: bool = False
) -> dict:
    """Модель и параметры из карточки агента (конструктор).

    skip_access_check: для субагентов, явно указанных в карточке родителя —
    пользователь уже имеет доступ к родителю, отдельный ACL на ребёнка
    не требуется (иначе при шаринге/галерее parent видит имя, а запуск падает).
    """
    empty = {
        "name": None,
        "model_path": None,
        "max_tokens": None,
        "temperature": None,
        "system_prompt": None,
        "file_search_enabled": False,
        "kb_document_ids": [],
        "knowledge_bases_enabled": False,
        "knowledge_base_ids": [],
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
        "recursion_limit": None,
        "subagents": None,
    }
    if agent_id_raw is None:
        return empty
    try:
        aid = int(agent_id_raw)
    except (TypeError, ValueError):
        return empty

    # Глобальная роль (ENV + LDAP/Keycloak): base / без viewer+ → агенты в чате недоступны.
    try:
        from backend.auth.agent_platform_roles import user_can_use_agents

        if user is not None and not user_can_use_agents(user):
            logger.warning(
                "[chat] blocked by agents feature gate user=%s agent_id=%s",
                user.get("user_id"),
                aid,
            )
            return empty
    except Exception:
        # Роль-резолвинг не должен ронять чат: в случае ошибки лучше "пустить" (legacy behaviour).
        pass

    try:
        from backend.database.init_db import get_agent_repository

        repo = get_agent_repository()
        if repo is None:
            return empty
        if not skip_access_check and not await repo.user_can_access_agent(aid, user_id):
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
            elif (
                "/" in mp
                or mp.lower().endswith(".gguf")
                or (len(mp) > 2 and mp[1] == ":")
            ):
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
        raw_kbase_ids = cfg.get("knowledge_base_ids")
        if isinstance(raw_kbase_ids, list):
            kbase_ids: List[int] = []
            for v in raw_kbase_ids:
                try:
                    kid = int(v)
                except (TypeError, ValueError):
                    continue
                if kid > 0:
                    kbase_ids.append(kid)
            out["knowledge_base_ids"] = sorted(set(kbase_ids))
        if "knowledge_bases_enabled" in cfg:
            out["knowledge_bases_enabled"] = bool(cfg.get("knowledge_bases_enabled"))
        else:
            out["knowledge_bases_enabled"] = bool(out["knowledge_base_ids"])
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
            out["mcp_server_ids"] = [
                str(v).strip() for v in raw_mcp_ids if str(v).strip()
            ]
        raw_plugin_ids = cfg.get("plugin_ids")
        if isinstance(raw_plugin_ids, list):
            out["plugin_ids"] = [
                str(v).strip() for v in raw_plugin_ids if str(v).strip()
            ]
        if "plugins_enabled" in cfg:
            out["plugins_enabled"] = bool(cfg.get("plugins_enabled"))
        else:
            out["plugins_enabled"] = bool(out["plugin_ids"])
        out["artifacts_enabled"] = bool(cfg.get("artifacts_enabled", False))
        out["shadcn_enabled"] = bool(cfg.get("shadcn_enabled", False))
        out["user_prompt_mode"] = bool(cfg.get("user_prompt_mode", False))
        from backend.agents.chain.settings import parse_agent_ids

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
        # «Смотреть общий RAG родителя»: нет поля (старые карточки) - включён.
        out["use_parent_shared_rag"] = cfg.get("use_parent_shared_rag") is not False
        raw_recursion = cfg.get("recursion_limit")
        if isinstance(raw_recursion, int) and raw_recursion > 0:
            out["recursion_limit"] = raw_recursion
        elif isinstance(raw_recursion, str) and raw_recursion.strip().isdigit():
            parsed = int(raw_recursion.strip())
            if parsed > 0:
                out["recursion_limit"] = parsed
        if isinstance(cfg.get("subagents"), dict):
            out["subagents"] = dict(cfg["subagents"])
        logger.debug(
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
            f"use_parent_shared_rag={out['use_parent_shared_rag']}, "
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

def _normalize_kb_ids(knowledge_base_ids: Optional[List[Any]]) -> List[int]:
    out: List[int] = []
    if not isinstance(knowledge_base_ids, list):
        return out
    seen: set[int] = set()
    for raw in knowledge_base_ids:
        try:
            kid = int(raw)
        except (TypeError, ValueError):
            continue
        if kid > 0 and kid not in seen:
            seen.add(kid)
            out.append(kid)
    return out


async def map_knowledge_base_document_ids(
    rag_client: Any,
    knowledge_base_ids: Optional[List[Any]],
) -> Dict[int, List[int]]:
    """kb_id → document_ids по metadata.knowledge_base_id."""
    want = set(_normalize_kb_ids(knowledge_base_ids))
    if not rag_client or not want:
        return {}
    try:
        rows = list(await rag_client.kb_list_documents() or [])
    except Exception:
        logger.exception("map_knowledge_base_document_ids: kb_list_documents failed")
        return {}
    by_kb: Dict[int, List[int]] = {kid: [] for kid in want}
    for row in rows:
        if not isinstance(row, dict):
            continue
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        raw_kb = meta.get("knowledge_base_id", row.get("knowledge_base_id"))
        try:
            kid = int(raw_kb)
        except (TypeError, ValueError):
            continue
        if kid not in want:
            continue
        try:
            by_kb[kid].append(int(row["id"]))
        except (TypeError, ValueError, KeyError):
            continue
    for kid in list(by_kb.keys()):
        by_kb[kid] = sorted(set(by_kb[kid]))
    empty = sorted(kid for kid, docs in by_kb.items() if not docs)
    if empty:
        logger.warning(
            "map_knowledge_base_document_ids: kb_ids=%s без документов "
            "(kb_list_documents=%s)",
            empty,
            len(rows),
        )
    return by_kb


async def expand_knowledge_base_document_ids(
    rag_client: Any,
    knowledge_base_ids: Optional[List[Any]],
) -> List[int]:
    """Развернуть id баз знаний в document_id по metadata.knowledge_base_id."""
    by_kb = await map_knowledge_base_document_ids(rag_client, knowledge_base_ids)
    out: List[int] = []
    for docs in by_kb.values():
        out.extend(docs)
    return sorted(set(out))


def document_ids_from_kb_rows(
    rows: Optional[List[Any]],
    knowledge_base_ids: Optional[List[Any]],
) -> List[int]:
    """Вытащить document_id из уже загруженного kb_list по metadata.knowledge_base_id."""
    want = set(_normalize_kb_ids(knowledge_base_ids))
    if not want or not rows:
        return []
    out: List[int] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        raw_kb = meta.get("knowledge_base_id", row.get("knowledge_base_id"))
        try:
            kid = int(raw_kb)
            did = int(row["id"])
        except (TypeError, ValueError, KeyError):
            continue
        if kid in want:
            out.append(did)
    return sorted(set(out))


def _is_rag_search_timeout(exc: BaseException) -> bool:
    try:
        import httpx

        if isinstance(exc, (httpx.ReadTimeout, httpx.TimeoutException)):
            return True
    except Exception:
        pass
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    return "timeout" in name or "timeout" in msg or "read_timeout" in msg


async def _kb_search_with_rerank_timeout_fallback(
    rag_client: Any,
    *,
    query: str,
    k: int,
    document_ids: List[int],
    strategy: Optional[str],
    settings: Dict[str, Any],
    kb_id: int,
) -> Optional[List[Any]]:
    """kb_search; при таймауте с включённым реранкером — повтор без него.

    Cold MiniCPM на CPU часто > SVC_RAG_SEARCH_READ_TIMEOUT: svc-rag ещё
    считает hits, а бэкенд уже RecvTimeout → чат «не видит» живой корпус.
    """
    override = dict(settings) if settings else {}
    try:
        return await rag_client.kb_search(
            query,
            k=k,
            document_ids=document_ids,
            strategy=strategy,
            settings_override=override or None,
            settings_source="kb",
        )
    except Exception as first_exc:
        rerank_on = bool(override.get("rag_reranking_enabled"))
        if not (rerank_on and _is_rag_search_timeout(first_exc)):
            logger.exception(
                "kb_search_knowledge_bases: поиск kb_id=%s docs=%s",
                kb_id,
                len(document_ids),
            )
            return None
        logger.warning(
            "[RAG] kb_id=%s: поиск с реранкером ушёл в timeout (%s) — "
            "повтор без реранкера (увеличьте SVC_RAG_SEARCH_READ_TIMEOUT "
            "или выключите RAG_ENTITY_RERANKING_ENABLED)",
            kb_id,
            type(first_exc).__name__,
        )
        fast = dict(override)
        fast["rag_reranking_enabled"] = False
        try:
            return await rag_client.kb_search(
                query,
                k=k,
                document_ids=document_ids,
                strategy=strategy,
                settings_override=fast,
                settings_source="kb",
                use_reranking=False,
            )
        except Exception:
            logger.exception(
                "kb_search_knowledge_bases: повтор без реранкера kb_id=%s docs=%s",
                kb_id,
                len(document_ids),
            )
            return None


async def kb_search_knowledge_bases(
    rag_client: Any,
    query: str,
    knowledge_base_ids: Optional[List[Any]],
    k: int = 8,
    strategy: Optional[str] = None,
    *,
    docs_by_kb: Optional[Dict[int, List[int]]] = None,
    only_document_ids: Optional[List[int]] = None,
) -> List[Tuple[str, float, Optional[int], Optional[int]]]:
    """Поиск по нескольким entity-БЗ: у каждой свой embedding-снимок.

    Документы разных БЗ могут быть залиты разными моделями — один общий
    ``kb_search`` с настройками первой БЗ даёт пустую выдачу. Поэтому ищем
    по каждой БЗ отдельно и сливаем hits.
    """
    if not rag_client or not query:
        return []
    kb_ids = _normalize_kb_ids(knowledge_base_ids)
    if not kb_ids:
        return []
    if docs_by_kb is None:
        docs_by_kb = await map_knowledge_base_document_ids(rag_client, kb_ids)

    from backend.realtime.rag_evidence import filter_rag_hits_by_score
    from backend.services.user_rag_settings import get_entity_rag_settings

    allow_docs: Optional[set[int]] = None
    if only_document_ids is not None:
        allow_docs = set()
        for raw in only_document_ids:
            try:
                allow_docs.add(int(raw))
            except (TypeError, ValueError):
                continue

    merged: List[Tuple[str, float, Optional[int], Optional[int]]] = []
    seen: set[Tuple[Optional[int], Optional[int], str]] = set()

    for kb_id in kb_ids:
        doc_ids = list(docs_by_kb.get(kb_id) or [])
        if allow_docs is not None:
            doc_ids = [d for d in doc_ids if d in allow_docs]
        if not doc_ids:
            continue
        try:
            settings = await get_entity_rag_settings("kb", kb_id)
        except Exception:
            logger.exception(
                "kb_search_knowledge_bases: настройки kb_id=%s недоступны", kb_id
            )
            settings = {}
        try:
            top_k = int((settings or {}).get("rag_chat_top_k") or k)
        except (TypeError, ValueError):
            top_k = k
        top_k = max(1, min(top_k, 64))
        try:
            min_sim = float((settings or {}).get("rag_similarity_threshold") or 0.0)
        except (TypeError, ValueError):
            min_sim = 0.0
        emb_path = (settings or {}).get("rag_embedding_model_path")
        hits = await _kb_search_with_rerank_timeout_fallback(
            rag_client,
            query=query,
            k=top_k,
            document_ids=doc_ids,
            strategy=strategy,
            settings=settings if isinstance(settings, dict) else {},
            kb_id=kb_id,
        )
        if hits is None:
            continue
        hit_list = filter_rag_hits_by_score(list(hits or []), min_sim)
        logger.info(
            "[RAG] kb_search kb_id=%s emb=%s docs=%s hits=%s",
            kb_id,
            emb_path,
            len(doc_ids),
            len(hit_list),
        )
        if doc_ids and not hit_list:
            logger.warning(
                "[RAG] kb_id=%s: docs=%s, но hits=0 при emb=%s — часто mismatch "
                "размерности (индекс Giga/2048 vs query FRIDA/1536) или таймаут "
                "реранкера. Переиндексируйте БЗ той же моделью, что в настройках.",
                kb_id,
                len(doc_ids),
                emb_path,
            )
        for h in hit_list:
            try:
                content, score, did, idx = h[0], h[1], h[2], h[3]
            except (TypeError, ValueError, IndexError):
                continue
            key = (did, idx, str(content)[:120])
            if key in seen:
                continue
            seen.add(key)
            merged.append((content, score, did, idx))

    merged.sort(
        key=lambda h: float(h[1]) if isinstance(h[1], (int, float)) else -1.0,
        reverse=True,
    )
    limit = max(1, min(int(k) * max(1, len(kb_ids)), 64))
    return merged[:limit]


async def partition_document_ids_by_knowledge_base(
    rag_client: Any,
    document_ids: Optional[List[Any]],
) -> Tuple[Dict[int, List[int]], List[int]]:
    """Разделить document_ids на entity-БЗ и legacy (без knowledge_base_id)."""
    want: set[int] = set()
    for raw in document_ids or []:
        try:
            want.add(int(raw))
        except (TypeError, ValueError):
            continue
    if not rag_client or not want:
        return {}, sorted(want)
    try:
        rows = list(await rag_client.kb_list_documents() or [])
    except Exception:
        logger.exception("partition_document_ids_by_knowledge_base failed")
        return {}, sorted(want)
    by_kb: Dict[int, List[int]] = {}
    legacy: List[int] = []
    found: set[int] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            did = int(row["id"])
        except (TypeError, ValueError, KeyError):
            continue
        if did not in want:
            continue
        found.add(did)
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        raw_kb = meta.get("knowledge_base_id", row.get("knowledge_base_id"))
        try:
            kid = int(raw_kb)
        except (TypeError, ValueError):
            legacy.append(did)
            continue
        if kid > 0:
            by_kb.setdefault(kid, []).append(did)
        else:
            legacy.append(did)
    for did in want - found:
        legacy.append(did)
    for kid in list(by_kb.keys()):
        by_kb[kid] = sorted(set(by_kb[kid]))
    return by_kb, sorted(set(legacy))


async def kb_search_scoped_documents(
    rag_client: Any,
    query: str,
    *,
    knowledge_base_ids: Optional[List[Any]] = None,
    legacy_doc_ids: Optional[List[int]] = None,
    k: int = 8,
    strategy: Optional[str] = None,
    only_document_ids: Optional[List[int]] = None,
    docs_by_kb: Optional[Dict[int, List[int]]] = None,
) -> List[Tuple[str, float, Optional[int], Optional[int]]]:
    """Entity-БЗ (per-KB emb) + legacy agent docs (scope agent)."""
    from backend.realtime.rag_evidence import merge_extra_hits

    hits: List[Tuple[str, float, Optional[int], Optional[int]]] = []
    kb_ids = _normalize_kb_ids(knowledge_base_ids)
    if kb_ids:
        hits = await kb_search_knowledge_bases(
            rag_client,
            query,
            kb_ids,
            k=k,
            strategy=strategy,
            docs_by_kb=docs_by_kb,
            only_document_ids=only_document_ids,
        )
    legacy_ids = list(legacy_doc_ids or [])
    if only_document_ids is not None:
        allow = set()
        for raw in only_document_ids:
            try:
                allow.add(int(raw))
            except (TypeError, ValueError):
                continue
        legacy_ids = [d for d in legacy_ids if d in allow]
    if legacy_ids:
        legacy_hits = await kb_search_agent_documents(
            rag_client, query, legacy_ids, k=k, strategy=strategy
        )
        try:
            from backend.realtime.rag_evidence import (
                filter_rag_hits_by_score,
                rag_guard_env,
            )

            min_sim, _ = rag_guard_env("agent")
            legacy_hits = filter_rag_hits_by_score(list(legacy_hits or []), min_sim)
        except Exception:
            logger.debug("legacy kb filter skipped", exc_info=True)
        hits = merge_extra_hits(hits, legacy_hits)
    return list(hits or [])


def resolve_agent_legacy_kb_document_ids(
    agent_profile: Optional[dict],
) -> List[int]:
    """Только legacy file_search document_ids агента (без entity БЗ)."""
    if not isinstance(agent_profile, dict):
        return []
    if not agent_profile.get("file_search_enabled"):
        return []
    out: List[int] = []
    raw_docs = agent_profile.get("kb_document_ids") or []
    if not isinstance(raw_docs, list):
        return []
    for v in raw_docs:
        try:
            out.append(int(v))
        except (TypeError, ValueError):
            continue
    return sorted(set(out))


async def resolve_agent_kb_document_ids(
    rag_client: Any,
    agent_profile: Optional[dict],
) -> List[int]:
    """Собрать document_ids агента: legacy kb_document_ids + прикреплённые БЗ."""
    if not isinstance(agent_profile, dict):
        return []
    out: List[int] = list(resolve_agent_legacy_kb_document_ids(agent_profile))
    kb_ids = _normalize_kb_ids(agent_profile.get("knowledge_base_ids"))
    # Ids в карточке важнее «забытого» knowledge_bases_enabled=false.
    if agent_profile.get("knowledge_bases_enabled") or kb_ids:
        expanded = await expand_knowledge_base_document_ids(rag_client, kb_ids)
        if kb_ids and not expanded:
            logger.warning(
                "[RAG] agent knowledge_base_ids=%s не развернулись в document_id "
                "(нет metadata.knowledge_base_id) — поиск по БЗ будет пустым",
                kb_ids,
            )
        out.extend(expanded)
    return sorted(set(out))
