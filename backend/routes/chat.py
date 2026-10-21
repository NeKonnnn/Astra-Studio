"""
routes/chat.py - REST /api/chat, WebSocket /ws/chat, WebSocket /ws/voice, WebSocket /ws/dictation
"""

import asyncio
import json
import os
import uuid
from datetime import datetime
from typing import Annotated, Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect

import backend.app_state as state
from backend.app_state import (
    ask_agent,
    get_conversation_repository,
    get_current_model_path,
    get_model_comparison_models,
    get_recent_dialog_history,
    minio_client,
    recognize_speech_from_file,
    save_dialog_entry,
    speak_text,
    verify_conversation_owner,
)
from backend.auth.jwt_handler import decode_token_signature_only, get_current_user
from backend.mcp.resolvers import resolve_chat_tool_ids
from backend.database.mongodb.models import Conversation, Message as DbMessage
from backend.llm_providers import get_registry
from backend.realtime.helpers import _terminal_chat_inference_banner
from backend.schemas import (
    ChatMessage,
    ContextBreakdownRequest,
    FollowUpSuggestionsRequest,
    MessageFeedbackRequest,
)
from backend.services.context_breakdown import build_context_overhead
from backend.services.follow_up_suggestions import generate_follow_up_suggestions
from backend.settings.cef_logger.cef_logger import log_cef_event
from backend.settings.logging import get_logger
from backend.settings.logging.errors import logged_suppress
from backend.utils.http_errors import public_error_detail

logger = get_logger(__name__)

router = APIRouter(tags=["chat"])


async def _authenticate_ws(websocket: WebSocket) -> Optional[dict]:
    """Аутентифицирует WebSocket-подключение по JWT.

    Токен берём из query (?token=/?access_token=) или заголовка Authorization.
    Возвращает данные пользователя либо None (в этом случае соединение уже
    закрыто с кодом 1008). Раньше legacy /ws/chat был открыт всем без проверки.
    """
    token = websocket.query_params.get("token") or websocket.query_params.get("access_token")
    if not token:
        auth = websocket.headers.get("authorization") or ""
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
    if not token:
        with logged_suppress(logger):
            await websocket.close(code=1008, reason="Unauthorized")
        return None
    try:
        return decode_token_signature_only(token)
    except Exception:
        logger.warning("WS: отклонено подключение с невалидным токеном")
        with logged_suppress(logger):
            await websocket.close(code=1008, reason="Unauthorized")
        return None


class ConnectionManager:

    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active_connections.append(ws)

    def disconnect(self, ws: WebSocket):
        self.active_connections.remove(ws)


manager = ConnectionManager()


@router.post("/api/chat/context-breakdown")
async def chat_context_breakdown(
    body: ContextBreakdownRequest,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Оценка сегментов контекста (системные промпты, RAG, MCP) для UI-счётчика."""
    try:
        payload = await build_context_overhead(
            model_path=body.model_path,
            project_instructions=body.project_instructions,
            agent_id=body.agent_id,
            use_kb_rag=body.use_kb_rag,
            tool_ids=body.tool_ids,
            user=current_user,
        )
        payload["success"] = True
        return payload
    except Exception as e:
        logger.exception("context-breakdown error")
        raise HTTPException(status_code=500, detail=public_error_detail(e))


@router.post("/api/chat/follow-up-suggestions")
async def chat_follow_up_suggestions(
    body: FollowUpSuggestionsRequest,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Фоновая генерация follow-up подсказок по контексту диалога."""
    del current_user  # авторизация обязательна; данные запроса не привязаны к user_id
    if not ask_agent:
        raise HTTPException(status_code=503, detail="AI agent не доступен")
    if not body.messages:
        return {"success": True, "suggestions": []}

    history = [{"role": m.role, "content": m.content} for m in body.messages]
    model_path = body.model_path or get_current_model_path()
    suggestions = await generate_follow_up_suggestions(
        ask_agent=ask_agent,
        messages=history,
        model_path=model_path,
    )
    return {"success": True, "suggestions": suggestions}


@router.post("/api/chat")
async def chat_with_ai(
    request: Request, message: ChatMessage, current_user: Annotated[dict, Depends(get_current_user)]
):
    if not ask_agent:
        raise HTTPException(status_code=503, detail="AI agent не доступен")
    if not save_dialog_entry:
        raise HTTPException(status_code=503, detail="Memory service не доступен")
    from backend.settings.cef_logger.cef_audit_context import cef_audit_reset, cef_audit_set

    _audit_tok = cef_audit_set(request=request, user=current_user, socket_remote=None)
    try:
        # Изоляция по пользователю: не полагаемся на общий на процесс "текущий
        # диалог". Если клиент прислал conversation_id — проверяем владельца,
        # иначе используем стабильный персональный диалог этого пользователя.
        if message.conversation_id and not await verify_conversation_owner(
            message.conversation_id, current_user["user_id"]
        ):
            raise HTTPException(status_code=403, detail="Нет доступа к этому диалогу")
        conv_id = (message.conversation_id or "").strip() or f"voice_{current_user['user_id']}"
        history = (
            await get_recent_dialog_history(max_entries=state.memory_max_messages, conversation_id=conv_id)
            if get_recent_dialog_history
            else []
        )
        from backend.services.user_feedback_context import (
            build_user_feedback_system_block,
            merge_feedback_into_system_prompt,
        )

        feedback_block = await build_user_feedback_system_block(
            current_user.get("user_id"),
            conversation_id=message.conversation_id,
        )
        logger.info("ПРЯМОЙ РЕЖИМ: Переключение на прямое общение с LLM")
        logger.info(
            f"Запрос пользователя: '{message.message[:100]}{('...' if len(message.message) > 100 else '')}'"
        )
        response = None
        tool_ids = resolve_chat_tool_ids(message.tool_ids or message.mcp_tool_ids)
        current_model_path = message.model or get_current_model_path()
        rest_system_prompt = merge_feedback_into_system_prompt(None, feedback_block)
        if tool_ids:
            try:
                from backend.mcp.chat_integration import run_mcp_for_chat

                mcp_result = await run_mcp_for_chat(
                    tool_ids=tool_ids,
                    user_message=message.message,
                    history=history,
                    system_prompt=rest_system_prompt,
                    model_path=current_model_path,
                    user=current_user,
                    chat_id=message.conversation_id,
                    message_id=message.message_id,
                )
                if mcp_result is not None:
                    response = mcp_result.content
                    logger.info(
                        "REST MCP agent loop: mode=%s tools=%s", mcp_result.mode, mcp_result.tool_calls_executed
                    )
            except Exception:
                logger.exception("REST MCP agent loop error")
        # Global documents store отключён: REST /api/chat без явных store-флагов
        # идёт напрямую в LLM (KB/memory/project — через Socket.IO чат).
        if not response:
            logger.info("ПРЯМОЙ РЕЖИМ: Используем обычный AI agent без контекста документов")
            current_model_path = message.model or get_current_model_path()
            _terminal_chat_inference_banner(
                sid="HTTP-POST-/api/chat",
                conversation_id=None,
                user_preview=message.message,
                mode_label="REST /api/chat — прямой LLM (без RAG)",
                model_path_for_call=current_model_path,
            )
            response = ask_agent(
                message.message,
                history=history,
                streaming=False,
                model_path=current_model_path,
                system_prompt=rest_system_prompt,
            )
        else:
            logger.info(f"ПРЯМОЙ РЕЖИМ: ответ готов, длина: {len(response)} символов")
        await save_dialog_entry("user", message.message, conversation_id=conv_id, user_id=current_user["user_id"])
        await save_dialog_entry("assistant", response, conversation_id=conv_id, user_id=current_user["user_id"])
        return {"response": response, "timestamp": datetime.now().isoformat(), "success": True}
    except HTTPException:
        # 403/503 и т.п. — осознанные ответы, их нельзя превращать в 500.
        raise
    except Exception as e:
        logger.exception("Ошибка операции")
        logger.error(f"/api/chat error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e
    finally:
        with logged_suppress(logger):
            cef_audit_reset(_audit_tok)


@router.get("/api/conversations")
async def get_conversations(current_user: Annotated[dict, Depends(get_current_user)], limit: int = 200):
    repo = get_conversation_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="MongoDB repository не доступен")
    user_id = current_user["user_id"]
    conversations = await repo.get_user_conversations(user_id=user_id, limit=limit)
    result = []
    for conv in conversations:
        result.append(
            {
                "conversation_id": conv.conversation_id,
                "title": conv.title,
                "created_at": conv.created_at.isoformat() if conv.created_at else None,
                "updated_at": conv.updated_at.isoformat() if conv.updated_at else None,
                "project_id": conv.project_id,
                "metadata": conv.metadata or {},
                "messages": [
                    {
                        "message_id": msg.message_id,
                        "role": msg.role,
                        "content": msg.content,
                        "timestamp": msg.timestamp.isoformat() if msg.timestamp else None,
                        "metadata": msg.metadata or {},
                    }
                    for msg in conv.messages or []
                ],
            }
        )
    return {"conversations": result, "count": len(result)}


@router.get("/api/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str, current_user: Annotated[dict, Depends(get_current_user)]
):
    """Один диалог целиком — для гидратации после выгрузки сообщений из RAM на фронте."""
    repo = get_conversation_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="MongoDB repository не доступен")
    conv = await repo.get_conversation(conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Диалог не найден")
    if conv.user_id != current_user["user_id"]:
        raise HTTPException(status_code=403, detail="Нет доступа к этому диалогу")
    return {
        "conversation": {
            "conversation_id": conv.conversation_id,
            "title": conv.title,
            "created_at": conv.created_at.isoformat() if conv.created_at else None,
            "updated_at": conv.updated_at.isoformat() if conv.updated_at else None,
            "project_id": conv.project_id,
            "metadata": conv.metadata or {},
            "messages": [
                {
                    "message_id": msg.message_id,
                    "role": msg.role,
                    "content": msg.content,
                    "timestamp": msg.timestamp.isoformat() if msg.timestamp else None,
                    "metadata": msg.metadata or {},
                }
                for msg in conv.messages or []
            ],
        }
    }


@router.delete("/api/conversations")
async def delete_all_conversations(request: Request, current_user: Annotated[dict, Depends(get_current_user)]):
    repo = get_conversation_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="MongoDB repository не доступен")
    deleted = await repo.delete_user_conversations(current_user["user_id"])
    log_cef_event(
        "CNV005",
        request=request,
        current_user=current_user,
        status_code=200,
        extra={"cs2": "all", "cs2Label": "ConversationId"},
    )
    return {"success": True, "deleted": deleted}


@router.delete("/api/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: str, request: Request, current_user: Annotated[dict, Depends(get_current_user)]
):
    repo = get_conversation_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="MongoDB repository не доступен")
    conv = await repo.get_conversation(conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Диалог не найден")
    if conv.user_id != current_user["user_id"]:
        raise HTTPException(status_code=403, detail="Нет доступа к этому диалогу")
    await repo.delete_conversation(conversation_id)
    log_cef_event(
        "CNV004",
        request=request,
        current_user=current_user,
        status_code=200,
        extra={"cs2": conversation_id, "cs2Label": "ConversationId"},
    )
    return {"success": True, "conversation_id": conversation_id}


@router.put("/api/conversations/{conversation_id}/title")
async def update_conversation_title(
    conversation_id: str, request: Request, current_user: Annotated[dict, Depends(get_current_user)]
):
    body = await request.json()
    title = body.get("title", "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Поле 'title' обязательно")
    repo = get_conversation_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="MongoDB repository не доступен")
    conv = await repo.get_conversation(conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Диалог не найден")
    if conv.user_id != current_user["user_id"]:
        raise HTTPException(status_code=403, detail="Нет доступа к этому диалогу")
    await repo.update_conversation(conversation_id, {"title": title})
    log_cef_event(
        "CNV001",
        request=request,
        current_user=current_user,
        status_code=200,
        extra={"cs2": conversation_id, "cs2Label": "ConversationId"},
    )
    return {"success": True, "conversation_id": conversation_id, "title": title}


@router.post("/api/conversations/{conversation_id}/archive")
async def archive_conversation(
    conversation_id: str, request: Request, current_user: Annotated[dict, Depends(get_current_user)]
):
    repo = get_conversation_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="MongoDB repository не доступен")
    conv = await repo.get_conversation(conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Диалог не найден")
    if conv.user_id != current_user["user_id"]:
        raise HTTPException(status_code=403, detail="Нет доступа к этому диалогу")
    metadata = dict(conv.metadata or {})
    metadata["archived"] = True
    await repo.update_conversation(conversation_id, {"metadata": metadata})
    log_cef_event(
        "CNV002",
        request=request,
        current_user=current_user,
        status_code=200,
        extra={"cs2": conversation_id, "cs2Label": "ConversationId"},
    )
    return {"success": True, "conversation_id": conversation_id, "archived": True}


@router.post("/api/conversations/{conversation_id}/unarchive")
async def unarchive_conversation(
    conversation_id: str, request: Request, current_user: Annotated[dict, Depends(get_current_user)]
):
    repo = get_conversation_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="MongoDB repository не доступен")
    conv = await repo.get_conversation(conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Диалог не найден")
    if conv.user_id != current_user["user_id"]:
        raise HTTPException(status_code=403, detail="Нет доступа к этому диалогу")
    metadata = dict(conv.metadata or {})
    metadata["archived"] = False
    await repo.update_conversation(conversation_id, {"metadata": metadata})
    log_cef_event(
        "CNV002",
        request=request,
        current_user=current_user,
        status_code=200,
        extra={"cs2": conversation_id, "cs2Label": "ConversationId"},
    )
    return {"success": True, "conversation_id": conversation_id, "archived": False}


@router.post("/api/conversations/{conversation_id}/duplicate")
async def duplicate_conversation(
    conversation_id: str, request: Request, current_user: Annotated[dict, Depends(get_current_user)]
):
    repo = get_conversation_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="MongoDB repository не доступен")
    conv = await repo.get_conversation(conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Диалог не найден")
    if conv.user_id != current_user["user_id"]:
        raise HTTPException(status_code=403, detail="Нет доступа к этому диалогу")
    new_id = str(uuid.uuid4())
    clone = Conversation(
        conversation_id=new_id,
        user_id=current_user["user_id"],
        title=(conv.title or "Диалог") + " (copy)",
        messages=conv.messages,
        metadata=conv.metadata or {},
        project_id=conv.project_id,
    )
    created_id = await repo.create_conversation(clone)
    if not created_id:
        raise HTTPException(status_code=500, detail="Не удалось создать копию диалога")
    log_cef_event(
        "CNV003",
        request=request,
        current_user=current_user,
        status_code=201,
        extra={"cs2": new_id, "cs2Label": "ConversationId"},
    )
    return {"success": True, "conversation_id": new_id}


def _new_branch_message_id() -> str:
    return f"msg_{uuid.uuid4().hex[:12]}"


def _resolve_multi_llm_slot_content(slot: Dict[str, Any]) -> str:
    alts = slot.get("alternative_responses") or slot.get("alternativeResponses")
    if isinstance(alts, list) and alts:
        idx = slot.get("current_response_index", slot.get("currentResponseIndex", 0))
        if not isinstance(idx, int) or idx < 0 or idx >= len(alts):
            idx = 0
        return str(alts[idx])
    return str(slot.get("content") or "")


def _clone_message_for_branch(
    msg: DbMessage,
    *,
    is_branch_point: bool,
    multi_llm_slot_index: Optional[int] = None,
) -> DbMessage:
    meta = dict(msg.metadata or {})
    content = msg.content
    if is_branch_point and msg.role == "assistant":
        if multi_llm_slot_index is not None:
            slots = meta.get("multi_llm_responses") or meta.get("multiLLMResponses") or []
            if isinstance(slots, list) and 0 <= multi_llm_slot_index < len(slots):
                slot = slots[multi_llm_slot_index]
                if isinstance(slot, dict):
                    content = _resolve_multi_llm_slot_content(slot)
                    meta = {
                        k: v
                        for k, v in meta.items()
                        if k not in ("multi_llm_responses", "multiLLMResponses")
                    }
                    model = slot.get("model")
                    if model:
                        meta["model"] = model
        else:
            alts = meta.get("alternative_responses") or meta.get("alternativeResponses")
            if isinstance(alts, list) and alts:
                idx = meta.get("current_response_index", meta.get("currentResponseIndex", 0))
                if not isinstance(idx, int) or idx < 0 or idx >= len(alts):
                    idx = 0
                content = str(alts[idx])
                for key in (
                    "alternative_responses",
                    "alternativeResponses",
                    "current_response_index",
                    "currentResponseIndex",
                ):
                    meta.pop(key, None)
    return DbMessage(
        message_id=_new_branch_message_id(),
        role=msg.role,
        content=content,
        timestamp=msg.timestamp or datetime.utcnow(),
        metadata=meta,
    )


def _serialize_conversation_for_client(conv: Conversation) -> dict:
    return {
        "conversation_id": conv.conversation_id,
        "title": conv.title,
        "created_at": conv.created_at.isoformat() if conv.created_at else None,
        "updated_at": conv.updated_at.isoformat() if conv.updated_at else None,
        "project_id": conv.project_id,
        "metadata": conv.metadata or {},
        "messages": [
            {
                "message_id": item.message_id,
                "role": item.role,
                "content": item.content,
                "timestamp": item.timestamp.isoformat() if item.timestamp else None,
                "metadata": item.metadata or {},
            }
            for item in conv.messages or []
        ],
    }


@router.post("/api/conversations/{conversation_id}/branch")
async def branch_conversation(
    conversation_id: str,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    repo = get_conversation_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="MongoDB repository не доступен")
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Некорректное тело запроса") from exc
    message_id = str(body.get("message_id") or "").strip()
    if not message_id:
        raise HTTPException(status_code=400, detail="Поле 'message_id' обязательно")
    multi_llm_slot_index = body.get("multi_llm_slot_index")
    if multi_llm_slot_index is not None and not isinstance(multi_llm_slot_index, int):
        raise HTTPException(status_code=400, detail="Поле 'multi_llm_slot_index' должно быть числом")

    conv = await repo.get_conversation(conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Диалог не найден")
    if conv.user_id != current_user["user_id"]:
        raise HTTPException(status_code=403, detail="Нет доступа к этому диалогу")

    source_messages = conv.messages or []
    branch_index = next(
        (idx for idx, item in enumerate(source_messages) if item.message_id == message_id),
        -1,
    )
    if branch_index < 0:
        raise HTTPException(status_code=404, detail="Сообщение не найдено")
    branch_message = source_messages[branch_index]
    if branch_message.role != "assistant":
        raise HTTPException(status_code=400, detail="Ветку можно создать только от ответа ассистента")

    branched_messages = [
        _clone_message_for_branch(
            item,
            is_branch_point=(idx == branch_index),
            multi_llm_slot_index=multi_llm_slot_index if idx == branch_index else None,
        )
        for idx, item in enumerate(source_messages[: branch_index + 1])
    ]

    new_id = str(uuid.uuid4())
    base_title = (conv.title or "Чат").strip() or "Чат"
    branch_title = f"Ветка · {base_title[:40]}"
    now = datetime.utcnow()
    clone = Conversation(
        conversation_id=new_id,
        user_id=current_user["user_id"],
        title=branch_title,
        messages=branched_messages,
        metadata={
            **(conv.metadata or {}),
            "branched_from": conversation_id,
            "branch_message_id": message_id,
            "hidden_from_sidebar_until_user_message": True,
            "branch_seeded_message_count": len(branched_messages),
        },
        project_id=conv.project_id,
        created_at=now,
        updated_at=now,
    )
    created_id = await repo.create_conversation(clone)
    if not created_id:
        raise HTTPException(status_code=500, detail="Не удалось создать ветку диалога")

    log_cef_event(
        "CNV004",
        request=request,
        current_user=current_user,
        status_code=201,
        extra={"cs2": new_id, "cs2Label": "ConversationId", "cs3": conversation_id, "cs3Label": "SourceConversationId"},
    )
    return {
        "success": True,
        "conversation_id": new_id,
        "conversation": _serialize_conversation_for_client(clone),
    }


@router.put("/api/messages/{conversation_id}/{message_id}")
async def update_message(
    conversation_id: str,
    message_id: str,
    request: dict,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    try:
        from backend.app_state import get_conversation_repository

        repo = get_conversation_repository()
        if repo is None:
            raise HTTPException(status_code=503, detail="MongoDB repository не доступен")
        conversation = await repo.get_conversation(conversation_id)
        if not conversation:
            raise HTTPException(status_code=404, detail="Диалог не найден")
        if getattr(conversation, "user_id", None) and conversation.user_id != current_user.get("user_id"):
            raise HTTPException(status_code=403, detail="Нет доступа к этому диалогу")
        content = request.get("content", "")
        if not content:
            raise HTTPException(status_code=400, detail="Поле 'content' обязательно")
        success = await repo.update_message(conversation_id, message_id, content, request.get("old_content"))
        if success:
            return {"message": "Сообщение обновлено", "success": True, "timestamp": datetime.now().isoformat()}
        raise HTTPException(status_code=404, detail="Сообщение не найдено")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка операции")
        raise HTTPException(status_code=500, detail=public_error_detail(e)) from e


@router.put("/api/messages/{conversation_id}/{message_id}/feedback")
async def upsert_message_feedback(
    conversation_id: str,
    message_id: str,
    body: MessageFeedbackRequest,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Сохранить или сбросить лайк/дизлайк ответа ассистента."""
    try:
        from backend.app_state import get_conversation_repository

        repo = get_conversation_repository()
        if repo is None:
            raise HTTPException(status_code=503, detail="MongoDB repository не доступен")

        conversation = await repo.get_conversation(conversation_id)
        if not conversation:
            raise HTTPException(status_code=404, detail="Диалог не найден")
        if getattr(conversation, "user_id", None) and conversation.user_id != current_user.get("user_id"):
            raise HTTPException(status_code=403, detail="Нет доступа к этому диалогу")

        msg = next(
            (m for m in (conversation.messages or []) if getattr(m, "message_id", None) == message_id),
            None,
        )
        if not msg:
            raise HTTPException(status_code=404, detail="Сообщение не найдено")
        if getattr(msg, "role", None) != "assistant":
            raise HTTPException(status_code=400, detail="Оценку можно ставить только ответам ассистента")

        allowed_tags = {
            "did_not_follow_instructions",
            "dislike_style",
            "inaccurate",
            "too_verbose",
            "too_short",
            "irrelevant",
            "biased",
            "safety_or_legal",
            "other",
        }
        rating = body.rating
        if rating is None:
            feedback_payload = None
        else:
            raw_tags = [t for t in (body.tags or []) if t in allowed_tags]
            comment = (body.comment or "").strip()
            if len(comment) > 2000:
                comment = comment[:2000]
            if rating == "dislike" and not raw_tags and not comment:
                raise HTTPException(
                    status_code=400,
                    detail="Для дизлайка укажите причину (тег) или комментарий",
                )
            feedback_payload = {
                "rating": rating,
                "tags": raw_tags if rating == "dislike" else [],
                "comment": comment,
                "user_id": current_user.get("user_id"),
                "updated_at": datetime.now().isoformat(),
            }

        success = await repo.update_message_feedback(
            conversation_id,
            message_id,
            feedback_payload,
            multi_llm_slot_index=body.multi_llm_slot_index,
        )
        if not success:
            raise HTTPException(status_code=404, detail="Не удалось сохранить отзыв")
        return {
            "success": True,
            "feedback": feedback_payload,
            "timestamp": datetime.now().isoformat(),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка сохранения feedback")
        raise HTTPException(status_code=500, detail=public_error_detail(e))


@router.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    if not ask_agent or not save_dialog_entry:
        await websocket.close(code=1008, reason="AI services not available")
        return
    ws_user = await _authenticate_ws(websocket)
    if ws_user is None:
        return
    ws_user_id = ws_user.get("user_id")
    # Диалог привязан к пользователю, а не к общему на процесс стейту.
    ws_conv_id = f"wschat_{ws_user_id}"
    await manager.connect(websocket)
    try:
        while True:
            data = json.loads(await websocket.receive_text())
            user_message = data.get("message", "")
            streaming = data.get("streaming", True)
            history = (
                await get_recent_dialog_history(max_entries=state.memory_max_messages, conversation_id=ws_conv_id)
                if get_recent_dialog_history
                else []
            )
            await save_dialog_entry("user", user_message, conversation_id=ws_conv_id, user_id=ws_user_id)
            use_multi_llm = bool(data.get("model_comparison_enabled", False))

            def stream_cb(chunk, acc):
                try:
                    asyncio.create_task(
                        websocket.send_text(json.dumps({"type": "chunk", "chunk": chunk, "accumulated": acc}))
                    )
                    return True
                except Exception:
                    logger.exception("Ошибка операции")
                    return False

            try:
                if use_multi_llm:
                    models = get_model_comparison_models()
                    if not models:
                        await websocket.send_text(json.dumps({"type": "error", "error": "Модели не выбраны"}))
                        continue
                    # Global store отключён — multi-LLM WS без автоподмешивания /search
                    final_user_message = user_message

                    async def _gen_one(
                        model_name,
                        *,
                        _models=models,
                        _final_user_message=final_user_message,
                        _streaming=streaming,
                    ):
                        """Одна генерация multi-LLM через ProviderRegistry (без глобальных локов)."""
                        await websocket.send_text(
                            json.dumps(
                                {
                                    "type": "multi_llm_start",
                                    "model": model_name,
                                    "total_models": len(_models),
                                    "models": _models,
                                }
                            )
                        )
                        try:
                            registry = await get_registry()
                            provider, model_id = registry.resolve(model_name)
                            if not model_id:
                                return {
                                    "model": model_name,
                                    "response": f"Некорректный путь модели {model_name!r}",
                                    "error": True,
                                }
                            if not await provider.ensure_model_loaded(model_id):
                                return {
                                    "model": model_name,
                                    "response": f"Модель {model_id!r} недоступна на провайдере {provider.id!r}. Проверьте состояние сервера.",
                                    "error": True,
                                }
                            from backend.llm_client import get_llm_service

                            service = await get_llm_service()
                            messages = service.prepare_messages(
                                prompt=_final_user_message, history=None, system_prompt=None
                            )
                            if _streaming:

                                def _cb(chunk: str, acc: str) -> bool:
                                    try:
                                        asyncio.create_task(
                                            websocket.send_text(
                                                json.dumps(
                                                    {
                                                        "type": "multi_llm_chunk",
                                                        "model": model_name,
                                                        "chunk": chunk,
                                                        "accumulated": acc,
                                                    }
                                                )
                                            )
                                        )
                                    except Exception:
                                        logger.exception("WebSocket: ошибка отправки чанка")
                                    return True

                                resp = await provider.stream_chat(
                                    messages=messages, model=model_id, callback=_cb, temperature=0.7, max_tokens=1024
                                )
                            else:
                                resp = await provider.chat(
                                    messages=messages, model=model_id, temperature=0.7, max_tokens=1024
                                )
                            return {"model": model_name, "response": resp}
                        except Exception as e:
                            logger.exception("multi-llm /ws/chat: ошибка для модели %s", model_name)
                            return {"model": model_name, "response": f"Ошибка: {e}", "error": True}

                    results = await asyncio.gather(*[_gen_one(m) for m in models], return_exceptions=True)
                    for r in results:
                        if isinstance(r, Exception):
                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "type": "multi_llm_complete",
                                        "model": "unknown",
                                        "response": str(r),
                                        "error": True,
                                    }
                                )
                            )
                        else:
                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "type": "multi_llm_complete",
                                        "model": r.get("model", "unknown"),
                                        "response": r.get("response", ""),
                                        "error": r.get("error", False),
                                    }
                                )
                            )
                    logger.info("WebSocket: Все ответы от моделей сгенерированы")
                    continue
                response = ask_agent(
                    user_message,
                    history=history,
                    streaming=streaming,
                    stream_callback=stream_cb if streaming else None,
                    model_path=get_current_model_path(),
                )
                logger.info(f"WebSocket: получен ответ от AI agent, длина: {len(response)} символов")
                await save_dialog_entry("assistant", response, conversation_id=ws_conv_id, user_id=ws_user_id)
                await websocket.send_text(
                    json.dumps({"type": "complete", "response": response, "timestamp": datetime.now().isoformat()})
                )
            except Exception as e:
                logger.exception("Ошибка операции")
                await websocket.send_text(json.dumps({"type": "error", "error": public_error_detail(e)}))
    except WebSocketDisconnect:
        logger.info("WebSocket /ws/chat отключен")
        with logged_suppress(logger):
            manager.disconnect(websocket)
    except Exception:
        logger.exception("WebSocket /ws/chat error")
        manager.disconnect(websocket)


@router.websocket("/ws/voice")
async def websocket_voice(websocket: WebSocket):
    ws_user = await _authenticate_ws(websocket)
    if ws_user is None:
        return
    ws_user_id = ws_user.get("user_id")
    # Голосовой чат привязан к персональному диалогу пользователя.
    ws_conv_id = f"voice_{ws_user_id}"
    await manager.connect(websocket)
    if not ask_agent or not save_dialog_entry:
        with logged_suppress(logger):
            await websocket.send_text(json.dumps({"type": "error", "error": "AI сервисы недоступны."}))
    try:
        while True:
            raw = await websocket.receive()
            if raw.get("type") == "websocket.disconnect":
                break
            if "text" in raw:
                try:
                    cmd = json.loads(raw["text"])
                    t = cmd.get("type", "")
                    if t == "start_listening":
                        await websocket.send_text(json.dumps({"type": "listening_started", "message": "Готов"}))
                    elif t == "stop_processing":
                        state.voice_chat_stop_flag = True
                        await websocket.send_text(json.dumps({"type": "processing_stopped"}))
                    elif t == "reset_processing":
                        state.voice_chat_stop_flag = False
                        await websocket.send_text(json.dumps({"type": "processing_reset"}))
                except json.JSONDecodeError:
                    pass
            elif "bytes" in raw:
                try:
                    await _process_audio(
                        websocket, raw["bytes"], user_id=ws_user_id, conversation_id=ws_conv_id
                    )
                except Exception as e:
                    logger.exception("Ошибка операции")
                    with logged_suppress(logger):
                        await websocket.send_text(json.dumps({"type": "error", "error": public_error_detail(e)}))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.exception("Ошибка операции")
        logger.error(f"W**ebSocket /ws/voice error: {e}", exc_info=True)
    finally:
        with logged_suppress(logger):
            manager.disconnect(websocket)


def _write_audio_bytes_to_file(data: bytes, *, upload_to_minio: bool = True) -> str:
    import tempfile

    if len(data) < 100:
        msg = "Некорректные аудио данные"
        raise ValueError(msg)
    if data[:4] == b"RIFF" and b"WAVE" in data[:12]:
        ext, ct = (".wav", "audio/wav")
    elif data[:4] == b"\x1aE\xdf\xa3":
        ext, ct = (".webm", "audio/webm")
    elif data[:4] == b"OggS":
        ext, ct = (".ogg", "audio/ogg")
    else:
        ext, ct = (".webm", "audio/webm")
    temp_dir = tempfile.gettempdir()
    audio_file = os.path.join(temp_dir, f"voice{datetime.now().timestamp()}{ext}")
    if minio_client and upload_to_minio:
        _vb = getattr(minio_client, "bucket_name", "") or "default"
        try:
            obj = minio_client.generate_object_name(prefix="voice_", extension=ext)
            minio_client.upload_file(data, obj, content_type=ct, cef_display_name="voice_upload")
            audio_file = minio_client.get_file_path(obj)
        except Exception:
            logger.exception("Ошибка загрузки аудио в MinIO")
            with open(audio_file, "wb") as f:
                f.write(data)
    else:
        with open(audio_file, "wb") as f:
            f.write(data)
    return audio_file


async def _process_dictation_audio(websocket: WebSocket, data: bytes):
    if not recognize_speech_from_file:
        await websocket.send_text(json.dumps({"type": "error", "error": "STT недоступен"}))
        return
    if len(data) < 100:
        await websocket.send_text(json.dumps({"type": "speech_error", "error": "Слишком короткий аудиофрагмент"}))
        return
    audio_file = None
    try:
        loop = asyncio.get_event_loop()
        audio_file = await loop.run_in_executor(None, lambda: _write_audio_bytes_to_file(data, upload_to_minio=False))
        text = await loop.run_in_executor(None, lambda: recognize_speech_from_file(audio_file))
        if not (text and text.strip()):
            await websocket.send_text(json.dumps({"type": "speech_error", "error": "Речь не распознана"}))
            return
        await websocket.send_text(json.dumps({"type": "speech_recognized", "text": text.strip()}))
    except ValueError as e:
        await websocket.send_text(json.dumps({"type": "error", "error": str(e)}))
    except Exception as e:
        logger.exception("Ошибка операции")
        logger.warning(f"Dictation STT error: {e}")
        await websocket.send_text(json.dumps({"type": "speech_error", "error": "Ошибка распознавания"}))
    finally:
        if audio_file:
            with logged_suppress(logger):
                if os.path.exists(audio_file):
                    os.remove(audio_file)


@router.websocket("/ws/dictation")
async def websocket_dictation(websocket: WebSocket):
    await manager.connect(websocket)
    if not recognize_speech_from_file:
        with logged_suppress(logger):
            await websocket.send_text(json.dumps({"type": "error", "error": "STT недоступен"}))
    try:
        while True:
            raw = await websocket.receive()
            if raw.get("type") == "websocket.disconnect":
                break
            if "text" in raw:
                try:
                    cmd = json.loads(raw["text"])
                    if cmd.get("type") == "start":
                        await websocket.send_text(json.dumps({"type": "ready", "message": "Готов к диктовке"}))
                except json.JSONDecodeError:
                    pass
            elif "bytes" in raw:
                try:
                    await _process_dictation_audio(websocket, raw["bytes"])
                except Exception as e:
                    logger.exception("Ошибка операции")
                    with logged_suppress(logger):
                        await websocket.send_text(json.dumps({"type": "error", "error": public_error_detail(e)}))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.exception("Ошибка операции")
        logger.error(f"WebSocket /ws/dictation error: {e}", exc_info=True)
    finally:
        with logged_suppress(logger):
            manager.disconnect(websocket)


async def _process_audio(
    websocket: WebSocket,
    data: bytes,
    *,
    user_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
):
    import tempfile

    if state.voice_chat_stop_flag:
        return
    if len(data) < 100:
        await websocket.send_text(json.dumps({"type": "error", "error": "Некорректные аудио данные"}))
        return
    if data[:4] == b"RIFF" and b"WAVE" in data[:12]:
        ext, ct = (".wav", "audio/wav")
    elif data[:4] == b"\x1aE\xdf\xa3":
        ext, ct = (".webm", "audio/webm")
    elif data[:4] == b"OggS":
        ext, ct = (".ogg", "audio/ogg")
    else:
        ext, ct = (".webm", "audio/webm")
    temp_dir = tempfile.gettempdir()
    audio_file = os.path.join(temp_dir, f"voice{datetime.now().timestamp()}{ext}")
    try:
        if minio_client:
            _vb = getattr(minio_client, "bucket_name", "") or "default"
            try:
                obj = minio_client.generate_object_name(prefix="voice_", extension=ext)
                minio_client.upload_file(data, obj, content_type=ct, cef_display_name="voice_upload")
                audio_file = minio_client.get_file_path(obj)
            except Exception:
                logger.exception("Ошибка загрузки аудио в MinIO")
                with open(audio_file, "wb") as f:
                    f.write(data)
        else:
            with open(audio_file, "wb") as f:
                f.write(data)
        if not recognize_speech_from_file:
            await websocket.send_text(json.dumps({"type": "error", "error": "STT недоступен"}))
            return
        loop = asyncio.get_event_loop()
        text = await loop.run_in_executor(None, lambda: recognize_speech_from_file(audio_file))
        if not (text and text.strip()):
            await websocket.send_text(json.dumps({"type": "speech_error", "error": "Речь не распознана"}))
            return
        await websocket.send_text(json.dumps({"type": "speech_recognized", "text": text}))
        history = (
            await get_recent_dialog_history(max_entries=state.memory_max_messages, conversation_id=conversation_id)
            if get_recent_dialog_history
            else []
        )
        voice_prompt = "Ты — голосовой AI-ассистент AstraChat. Отвечай кратко, без markdown и emoji."
        ai_resp = await loop.run_in_executor(
            None,
            lambda: ask_agent(
                text, history=history, streaming=False, model_path=get_current_model_path(), system_prompt=voice_prompt
            ),
        )
        await save_dialog_entry("user", text, conversation_id=conversation_id, user_id=user_id)
        await save_dialog_entry("assistant", ai_resp, conversation_id=conversation_id, user_id=user_id)
        await websocket.send_text(json.dumps({"type": "ai_response", "text": ai_resp}))
        speech_file = os.path.join(temp_dir, f"speech_{datetime.now().timestamp()}.wav")
        try:
            ok = await loop.run_in_executor(
                None, lambda: speak_text(ai_resp, speaker="baya", voice_id="ru", save_to_file=speech_file)
            )
            if ok and os.path.exists(speech_file) and (os.path.getsize(speech_file) > 44):
                with open(speech_file, "rb") as f:
                    await websocket.send_bytes(f.read())
                with logged_suppress(logger):
                    os.remove(speech_file)
            else:
                await websocket.send_text(json.dumps({"type": "tts_error", "error": "Ошибка TTS"}))
        except Exception as e:
            logger.exception("Ошибка операции")
            await websocket.send_text(json.dumps({"type": "tts_error", "error": public_error_detail(e)}))
    finally:
        with logged_suppress(logger):
            if os.path.exists(audio_file):
                os.remove(audio_file)
