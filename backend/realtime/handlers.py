"""
socket_handlers.py - все @sio.event обработчики Socket.IO
Регистрируется в main.py:
    from backend.socket_handlers import register_handlers
    register_handlers(sio)
"""

import asyncio
import concurrent.futures
import contextvars
import time
from datetime import datetime
from typing import Any, Dict, Optional


from backend.settings.rag_client import RagReindexInProgress
import backend.app_state as state
from backend.app_state import (
    ask_agent,
    context_prompt_manager,
    get_current_model_path,
    get_model_comparison_models,
    get_rag_chat_top_k,
    get_recent_dialog_history,
    rag_client,
    save_dialog_entry,
    stop_generation_flags,
    stop_transcription_flags,
)
from backend.database.memory_service import save_assistant_response
from backend.auth.jwt_handler import decode_token, decode_token_signature_only
from backend.llm_providers import split_model_path
from backend.rag_query.post_generation import maybe_replace_ungrounded
from backend.rag_query.prompts import RAG_STRICT_NOT_FOUND_MESSAGE, merge_strict_rag_system_prompt
from backend.rag_query.stage_timer import StageTimer
from backend.realtime.helpers import (
    _is_structure_query,
    _resolve_agent_chat_params,
    _terminal_chat_inference_banner,
    agent_mcp_tool_ids,
    agent_plugin_ids,
    kb_search_agent_documents,
    merge_chat_artifacts_settings,
)
from backend.services.user_feedback_context import (
    build_user_feedback_system_block,
    merge_feedback_into_system_prompt,
)
from backend.services.user_llm_settings import (
    bind_user_model_runtime,
    enrich_agent_profile_with_user_settings,
    get_user_prompt_manager,
    reset_user_model_runtime,
)
from backend.services.user_rag_settings import (
    bind_user_rag_runtime,
    get_entity_rag_settings,
    get_user_memory_strategy,
    reset_user_rag_runtime,
    runtime_agentic_max_iterations,
    runtime_agentic_rag_enabled,
    runtime_memory_strategy,
    runtime_rag_strategy,
    runtime_rag_system_prompt,
)
from backend.realtime.rag_evidence import (
    build_rag_id_to_filename,
    filter_rag_hits_by_score,
    format_rag_fragments,
    maybe_rag_no_evidence_message,
    rag_guard_env,
    rag_reindex_blocks_active_sources,
    resolve_active_rag_sources,
)
from backend.rag_query.context_budget import rag_context_max_chars
from backend.rag_query.relevance import hits_to_relevance_percents
from backend.services.memory_rag_env import get_memory_similarity_threshold
from backend.settings.cef_logger.cef_audit_context import cef_socket_remote_from_environ
from backend.settings import get_settings
from backend.settings.logging import get_logger
from backend.settings.logging.errors import logged_suppress
from backend.mcp.resolvers import resolve_chat_tool_ids
from backend.agents.config import resolve_recursion_limit
from backend.agents.step_debug import (
    describe_limit_source,
    log_chain_hop,
    log_plain_llm,
    log_pre_loop,
)

logger = get_logger(__name__)

_PLACEHOLDER_MODEL_PATHS = frozenset({"llm-svc", "llm-svc://", "local", "default"})


def _client_model_path_from_payload(data) -> Optional[str]:
    """model_path из UI (localStorage селектора), если передан в socket payload."""
    if not isinstance(data, dict):
        return None
    raw = str(data.get("model_path") or "").strip()
    if not raw:
        return None
    lower = raw.lower()
    if lower in _PLACEHOLDER_MODEL_PATHS:
        return None
    if lower.startswith("llm-svc://"):
        rest = raw[len("llm-svc://") :].strip().lstrip("/")
        return raw if rest else None
    if lower.endswith(".gguf"):
        return raw
    if "/" in raw:
        return raw
    return None


def _get_set_tool_context():
    from backend.tools.tool_context import set_tool_context
    return set_tool_context

async def _notify_reindex_wait(sio, sid) -> None:
    """UI-уведомление: база переиндексируется, поиск временно без документов."""
    try:
        await sio.emit(
            "chat_info",
            {
                "message": (
                    "База документов переиндексируется — поиск временно "
                    "недоступен, ответ будет без источников. Повторите вопрос "
                    "через пару минут."
                )
            },
            room=sid,
        )
    except Exception:
        logger.exception("notify reindex")

REINDEX_WAIT_MESSAGE = (
    "База документов сейчас переиндексируется - поиск по документам "
    "временно недоступен. Повторите вопрос через пару минут."
)

# Номер генерации на сокет. Флаг остановки один на сокет, а генераций в один
# момент может быть две
_generation_seq: Dict[str, int] = {}
# Всё, что запущено НЕ ПОЗЖЕ этого номера, считается остановленным.
_stopped_upto: Dict[str, int] = {}
# Номер СВОЕЙ генерации. ContextVar, а не аргумент: значение доезжает и в
# рабочие потоки - _make_ctx_runner копирует контекст перед run_in_executor
_my_generation: contextvars.ContextVar = contextvars.ContextVar(
    "chat_generation", default=0
)

# conversation_id / request_id текущего ответа — чтобы фронт маршрутизировал
# чанки в нужный чат, даже если на сокете параллельно идут две генерации.
_stream_conversation_id: contextvars.ContextVar = contextvars.ContextVar(
    "stream_conversation_id", default=None
)
_stream_request_id: contextvars.ContextVar = contextvars.ContextVar(
    "stream_request_id", default=None
)


def _bind_stream_ids(conversation_id, request_id):
    """Привязать id стрима к текущему asyncio/executor-контексту."""
    rid = None
    if request_id is not None:
        rid = str(request_id).strip() or None
    return (
        _stream_conversation_id.set(conversation_id or None),
        _stream_request_id.set(rid),
    )


def _reset_stream_ids(tokens) -> None:
    if not tokens:
        return
    try:
        _stream_conversation_id.reset(tokens[0])
        _stream_request_id.reset(tokens[1])
    except Exception:
        pass


def _stream_ids_payload(payload: Optional[dict] = None) -> dict:
    """Добавить conversation_id/request_id во все chat_* / multi_llm_* события."""
    out = dict(payload or {})
    cid = _stream_conversation_id.get()
    rid = _stream_request_id.get()
    if cid and "conversation_id" not in out:
        out["conversation_id"] = cid
    if rid and "request_id" not in out:
        out["request_id"] = rid
    return out

def _begin_generation(sid: str) -> int:
    """Начать новую генерацию на этом сокете и запомнить её номер в контексте."""
    seq = _generation_seq.get(sid, 0) + 1
    _generation_seq[sid] = seq
    _my_generation.set(seq)
    return seq

def _generation_stopped(sid: str) -> bool:
    """Остановлена ли ИМЕННО эта генерация.

    Заменяет stop_generation_flags.get(sid): общий флаг отвечал на вопрос
    «нажимал ли пользователь стоп хоть когда-нибудь на этом сокете», а не
    «остановили ли меня». Из-за этого новый запрос снимал стоп со старого.
    """
    my = _my_generation.get()
    if not my:
        # Номера нет - генерацию начали в обход _begin_generation. Считать её
        # остановленной нельзя: молча пропадёт весь ответ.
        return False
    return _stopped_upto.get(sid, 0) >= my

def _forget_generation(sid: str) -> None:
    _generation_seq.pop(sid, None)
    _stopped_upto.pop(sid, None)

async def _abort_chat_reindex(
    sio, sid, conversation_id, project_id, current_user
) -> None:
    """Вместо генерации без контекста - штатный ответ «подождите» + сохранение."""
    await sio.emit(
        "chat_complete",
        _stream_ids_payload(
            {
                "response": REINDEX_WAIT_MESSAGE,
                "timestamp": datetime.now().isoformat(),
                "was_streaming": False,
            }
        ),
        room=sid,
    )
    try:
        await save_assistant_response(
            REINDEX_WAIT_MESSAGE,
            {"reindex_wait": True},
            conversation_id=conversation_id,
            user_id=(current_user or {}).get("user_id"),
            project_id=project_id,
        )
    except Exception:
        logger.exception("Сохранение reindex-wait ответа")

async def _abort_multi_llm_reindex(
    sio, sid, multi_llm_models, conversation_id, project_id, current_user
) -> None:
    """Multi-LLM: закрыть все слоты сообщением «подождите» + сохранить."""
    for i, model_name in enumerate(multi_llm_models):
        await sio.emit(
            "multi_llm_complete",
            _stream_ids_payload(
                {
                    "model": model_name,
                    "response": REINDEX_WAIT_MESSAGE,
                    "error": False,
                    "index": i,
                    "total": len(multi_llm_models),
                }
            ),
            room=sid,
        )
    try:
        if conversation_id and multi_llm_models:
            slots = [
                {"model": m, "content": REINDEX_WAIT_MESSAGE, "error": False}
                for m in multi_llm_models
            ]
            combined = "\n\n".join(f"{s['model']}:\n{s['content']}" for s in slots)
            await save_assistant_response(
                combined,
                {"multi_llm_responses": slots, "reindex_wait": True},
                conversation_id=conversation_id,
                user_id=(current_user or {}).get("user_id"),
                project_id=project_id,
            )
    except Exception:
        logger.exception("Сохранение multi-LLM reindex-wait ответа")

async def _compute_and_emit_rag_metrics(
    sio,
    sid,
    *,
    query: str,
    document_search_trace: Optional[dict],
    context_text: str,
    answer: str,
    context_added: bool,
) -> Optional[dict]:
    """Считает online RAG-метрики (RR/Context Precision/Faithfulness) и шлёт их в UI.

    Fail-safe: любая ошибка гасится, основной ответ уже отдан пользователю.
    """
    if not context_added or not document_search_trace or not answer:
        return None
    try:
        from backend.rag_query.metrics import compute_online_rag_metrics

        metrics = await compute_online_rag_metrics(
            query=query,
            hits=(document_search_trace or {}).get("hits") or [],
            answer=answer,
            context_text=context_text,
        )
        if metrics:
            with logged_suppress(logger):
                await sio.emit("chat_rag_metrics", _stream_ids_payload({"metrics": metrics}), room=sid)
        return metrics
    except Exception:
        logger.exception("rag online metrics")
        return None


def _regen_save_kwargs(data: Optional[dict]) -> dict:
    """Параметры regenerate из socket payload для save_assistant_response."""
    if not isinstance(data, dict):
        return {"regenerate": False}
    alts = data.get("alternative_responses")
    if not isinstance(alts, list):
        alts = data.get("alternativeResponses")
    idx = data.get("current_response_index")
    if not isinstance(idx, int):
        idx = data.get("currentResponseIndex")
    return {
        "regenerate": bool(data.get("regenerate")),
        "assistant_message_id": str(data.get("assistant_message_id") or "").strip() or None,
        "alternative_responses": alts if isinstance(alts, list) else None,
        "current_response_index": idx if isinstance(idx, int) else None,
    }


def _generation_duration_sec(started_at: Optional[float]) -> int:
    """Секунды генерации ответа (минимум 1), для metadata и UI «думал mm:ss»."""
    if not started_at:
        return 1
    return max(1, int(round(time.perf_counter() - float(started_at))))


def _with_generation_duration(
    meta: Optional[Dict[str, Any]],
    started_at: Optional[float],
) -> Dict[str, Any]:
    """Добавляет generation_duration_sec в metadata сообщения ассистента."""
    out = dict(meta or {})
    out["generation_duration_sec"] = _generation_duration_sec(started_at)
    return out


def _run_sync_preserving_cef_audit(factory):
    """Устаревший вариант: copy_context() вызывался внутри потока (там контекст пустой).
    Оставлен для совместимости; для новых вызовов используй _make_ctx_runner."""
    return contextvars.copy_context().run(factory)


def _make_ctx_runner(factory):
    """Захватывает текущий contextvars.Context прямо здесь (в asyncio-задаче)
    и возвращает нуль-аргументный callable для передачи в run_in_executor.

    Правило: вызывать СТРОГО в asyncio-задаче, до передачи в executor:
        runner = _make_ctx_runner(lambda: ask_agent(...))
        await loop.run_in_executor(ex, runner)
    """
    _ctx = contextvars.copy_context()

    def _runner():
        return _ctx.run(factory)

    return _runner


_VALID_RAG_STRATEGIES = {"auto", "hierarchical", "hybrid", "vector", "lexical", "graph"}


def _extract_socket_token(auth: Any, environ: Optional[Dict[str, Any]]) -> Optional[str]:
    """Извлекает bearer-токен из Socket.IO handshake auth/environ."""
    token: Optional[str] = None
    if isinstance(auth, dict):
        raw = auth.get("token") or auth.get("access_token")
        if isinstance(raw, str) and raw.strip():
            token = raw.strip()
    if not token and isinstance(environ, dict):
        auth_header = environ.get("HTTP_AUTHORIZATION")
        if isinstance(auth_header, str) and auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
    return token if token else None


async def _get_socket_user_context(sio, sid: str) -> Optional[Dict[str, Any]]:
    """Возвращает user context, сохранённый в Socket.IO сессии.

    Проверяет, что сессия всё ещё активна (не была вытеснена новым логином
    с другого устройства/браузера). При ревоцированной сессии возвращает None
    — вызывающий код эмитирует ошибку «Сессия завершена» и фронтенд
    перенаправит пользователя на страницу входа.
    """
    try:
        session = await sio.get_session(sid)
    except Exception:
        logger.exception("Ошибка операции")
        return None
    if not isinstance(session, dict):
        return None
    user_ctx = session.get("user")
    if not isinstance(user_ctx, dict):
        return None
    token = user_ctx.get("token")
    if token:
        try:
            decode_token(token)
        except Exception:
            logger.exception("Socket.IO msg rejected: сессия ревоцирована sid= user_id=")
            return None
    return user_ctx


def _multi_llm_llm_svc_pool_style_path(model_path: str) -> bool:
    """Пути multi-LLM через llm-svc: не держим глобальный model_load_lock — пул и load на стороне llm-svc."""
    provider_id, _model_id = split_model_path(model_path or "")
    return bool(provider_id)


async def get_conversation_project_id(conversation_id: str) -> "Optional[str]":
    """Возвращает project_id диалога из MongoDB, или None если диалог не привязан к проекту."""
    if not conversation_id:
        return None
    try:
        from backend.database.init_db import get_conversation_repository

        repo = get_conversation_repository()
        conv = await repo.get_conversation(conversation_id)
        return conv.project_id if conv else None
    except Exception:
        logger.exception("_get_conversation_project_id()")
        return None


def _build_user_inline_attachments_metadata(raw: Any, inline_context: str = "") -> Optional[Dict[str, Any]]:
    """Метаданные вложений для MongoDB.

    Без base64 картинок — только MinIO-ссылки и имена.
    Текст документа кладём в ``inline_context``, чтобы follow-up в том же чате
    снова видел файл через историю LLM (UI по-прежнему показывает только вопрос).
    """
    items: list = []
    if isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "file").strip() or "file"
            ct = entry.get("contentType") or entry.get("content_type")
            if ct not in ("text", "image"):
                continue
            item: Dict[str, Any] = {"name": name, "contentType": ct}
            mo = entry.get("minio_object")
            mb = entry.get("minio_bucket")
            if mo:
                item["minio_object"] = str(mo)
            if mb:
                item["minio_bucket"] = str(mb)
            sz = entry.get("size")
            if isinstance(sz, (int, float)) and sz > 0:
                item["size"] = int(sz)
            te = entry.get("tokenEstimate")
            if isinstance(te, (int, float)) and te > 0:
                item["tokenEstimate"] = int(te)
            items.append(item)
    meta: Dict[str, Any] = {}
    if items:
        meta["inline_attachments"] = items
    ctx = str(inline_context or "").strip()
    if ctx:
        meta["inline_context"] = ctx
    return meta or None


def _subagent_artifact_blocks(result) -> list:
    """Все :::artifact-блоки из ответа субагента, включая незакрытые.

    Модель ставит закрывающий code-fence и забывает про :::. Рендер в её
    собственном чате это прощает, строгая регулярка - нет: блок терялся, и
    родитель пересказывал слайды текстом. Незакрытый блок берём до следующего
    заголовка или до конца ответа и закрываем сами (и fence, если открыт).
    Голый html-документ в fence без заголовка заворачиваем в text/html.
    """
    import hashlib
    import re

    fence = chr(96) * 3
    text = str(result or "")
    if not text.strip():
        return []
    header_re = re.compile(r":::artifact\{[^}\n]*\}")
    close_re = re.compile(r"\n:::[ \t]*(?:\n|$)")
    ident_re = re.compile(r"identifier=[\"']([^\"']+)[\"']")
    heads = list(header_re.finditer(text))
    blocks: list = []
    for i, h in enumerate(heads):
        stop = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        m = close_re.search(text, h.end(), stop)
        if m:
            blocks.append(text[h.start() : m.end()].strip())
            continue
        body = text[h.start() : stop].rstrip()
        fences = sum(1 for ln in body.splitlines() if ln.strip().startswith(fence))
        if fences % 2:
            body += "\n" + fence
        im = ident_re.search(h.group(0))
        logger.info(
            "[subagent] артефакт %s без закрывающего :::, закрыт принудительно",
            im.group(1) if im else "?",
        )
        blocks.append(body + "\n:::")
    if blocks:
        return blocks
    fence_re = re.compile(fence + r"html[ \t]*\n([\s\S]*?)\n" + fence, re.IGNORECASE)
    for fm in fence_re.finditer(text):
        html = fm.group(1)
        low = html.lstrip().lower()
        if not (low.startswith("<!doctype html") or low.startswith("<html")):
            continue
        tm = re.search(r"<title>([^<]{1,120})</title>", html, re.IGNORECASE)
        title = ((tm.group(1).strip() if tm else "") or "HTML-документ субагента").replace('"', "'")
        ident = "subagent-html-" + hashlib.md5(html.encode("utf-8", "ignore")).hexdigest()[:8]
        logger.info("[subagent] html-документ от субагента без :::artifact обёрнут в %s", ident)
        blocks.append(
            ':::artifact{identifier="' + ident + '" type="text/html" title="' + title + '"}\n'
            + fence + "html\n" + html + "\n" + fence + "\n:::"
        )
    return blocks


def _carry_subagent_artifacts(response, mcp_tool_events) -> str:
    """Артефакты и файлы из ответов субагентов - в итоговый ответ родителя.

    Ребёнок отдаёт :::artifact-блок (схема, презентация, график) и ссылки на
    файлы текстом tool-результата; родитель их пересказывает, и до рендера
    они не доходят. Блоки, чьего identifier в ответе нет, дописываем в конец
    как есть; ссылки, которых в ответе нет, - блоком «Файлы:». Воспроизвёл
    родитель сам - второго не будет.
    """
    text = str(response or "")
    try:
        import re

        if not mcp_tool_events:
            return text
        ident_re = re.compile(r"identifier=[\"']([^\"']+)[\"']")
        have = set(ident_re.findall(text))
        extra: list = []
        links: list = []
        seen_urls: set = set()
        for ev in mcp_tool_events:
            if not isinstance(ev, dict) or ev.get("type") != "mcp_tool_end":
                continue
            if ev.get("tool") == "subagent":
                for block in _subagent_artifact_blocks(ev.get("result")):
                    m = ident_re.search(block)
                    ident = m.group(1) if m else block
                    if ident in have:
                        continue
                    have.add(ident)
                    extra.append(block.strip())
            for link in ev.get("download_urls") or []:
                if not isinstance(link, dict):
                    continue
                url = str(link.get("url") or "")
                if url and url not in text and url not in seen_urls:
                    seen_urls.add(url)
                    links.append(dict(link))
        if not extra and not links:
            return text
        out = text
        if extra:
            out = (out.rstrip() + "\n\n" + "\n\n".join(extra)).strip()
        if links:
            from backend.mcp.result_parser import append_download_links_to_content

            out = append_download_links_to_content(out, links)
        logger.info(
            "[subagent] в ответ родителя добавлено: артефактов=%s файлов=%s",
            len(extra),
            len(links),
        )
        return out
    except Exception:
        logger.debug("[subagent] не удалось перенести артефакты", exc_info=True)
        return text


async def _handle_chat_image_generation_request(
    sio,
    sid: str,
    *,
    user_message: str,
    conversation_id: Optional[str],
    project_id: Optional[str],
    current_user: Optional[dict],
    streaming: bool,
    image_gen_preset_id: Optional[str] = None,
    image_generation_mode: bool = False,
    regenerate: bool = False,
    assistant_message_id: Optional[str] = None,
):
    """Генерация изображения (режим «генерация» или legacy-триггеры) без вызова LLM."""
    from backend.services.comfyui_image_generation import ComfyImageGenError
    from backend.services.image_generation_service import (
        handle_chat_image_generation,
        is_image_generation_chat_request,
        save_image_generation_assistant_message,
    )

    if not is_image_generation_chat_request(
        user_message,
        mode_enabled=bool(image_generation_mode),
    ):
        return False

    await sio.emit(
        "chat_thinking",
        _stream_ids_payload(
            {
                "status": "processing",
                "message": "Генерирую изображение в ComfyUI…",
                "image_generation": True,
            }
        ),
        room=sid,
    )

    try:
        result = await handle_chat_image_generation(
            user_message,
            preset_id=image_gen_preset_id,
            mode_enabled=bool(image_generation_mode),
        )
    except ComfyImageGenError as exc:
        err_text = f"Не удалось сгенерировать изображение: {exc}"
        logger.warning("Chat image generation failed: %s", exc)
        await sio.emit("chat_complete", _stream_ids_payload({
            "response": err_text,
            "timestamp": datetime.now().isoformat(),
            "was_streaming": streaming,
            "image_generation_error": True,
        }), room=sid)
        try:
            meta = {"image_generation_error": True}
            if project_id:
                from backend.database.memory_service import save_dialog_entry_to_project
                await save_dialog_entry_to_project(
                    "assistant",
                    err_text,
                    project_id,
                    conversation_id,
                    metadata=meta,
                    user_id=(current_user or {}).get("user_id"),
                )
            else:
                await save_dialog_entry(
                    "assistant",
                    err_text,
                    meta,
                    None,
                    conversation_id,
                    user_id=(current_user or {}).get("user_id"),
                )
        except Exception as save_exc:
            logger.warning("Не удалось сохранить ошибку image gen: %s", save_exc)
        return True

    response = result.get("response") or ""
    meta = result.get("metadata") or {}
    inline_attachments = result.get("inline_attachments") or []

    try:
        await save_image_generation_assistant_message(
            content=response,
            metadata=meta,
            conversation_id=conversation_id,
            project_id=project_id,
            user_id=(current_user or {}).get("user_id"),
            regenerate=regenerate,
            assistant_message_id=assistant_message_id,
        )
    except Exception as save_exc:
        logger.warning("Не удалось сохранить ответ image gen: %s", save_exc)

    payload = _stream_ids_payload({
        "response": response,
        "timestamp": datetime.now().isoformat(),
        "was_streaming": streaming,
        "inline_attachments": inline_attachments,
        "image_generation": True,
    })
    await sio.emit("chat_complete", payload, room=sid)
    return True


async def _handle_chat_video_generation_request(
    sio,
    sid: str,
    *,
    user_message: str,
    conversation_id: Optional[str],
    project_id: Optional[str],
    current_user: Optional[dict],
    streaming: bool,
    video_gen_preset_id: Optional[str] = None,
    video_generation_mode: bool = False,
    regenerate: bool = False,
    assistant_message_id: Optional[str] = None,
):
    """Генерация видео (режим «генерация видео») без вызова LLM."""
    from backend.services.comfyui_image_generation import ComfyImageGenError
    from backend.services.video_generation_service import (
        handle_chat_video_generation,
        is_video_generation_chat_request,
        save_video_generation_assistant_message,
    )

    if not is_video_generation_chat_request(
        user_message,
        mode_enabled=bool(video_generation_mode),
    ):
        return False

    await sio.emit(
        "chat_thinking",
        _stream_ids_payload(
            {
                "status": "processing",
                "message": "Генерирую видео в ComfyUI…",
                "video_generation": True,
            }
        ),
        room=sid,
    )

    try:
        result = await handle_chat_video_generation(
            user_message,
            preset_id=video_gen_preset_id,
            mode_enabled=bool(video_generation_mode),
        )
    except ComfyImageGenError as exc:
        err_text = f"Не удалось сгенерировать видео: {exc}"
        logger.warning("Chat video generation failed: %s", exc)
        await sio.emit("chat_complete", _stream_ids_payload({
            "response": err_text,
            "timestamp": datetime.now().isoformat(),
            "was_streaming": streaming,
            "video_generation_error": True,
        }), room=sid)
        try:
            meta = {"video_generation_error": True}
            if project_id:
                from backend.database.memory_service import save_dialog_entry_to_project
                await save_dialog_entry_to_project(
                    "assistant",
                    err_text,
                    project_id,
                    conversation_id,
                    metadata=meta,
                    user_id=(current_user or {}).get("user_id"),
                )
            else:
                await save_dialog_entry(
                    "assistant",
                    err_text,
                    meta,
                    None,
                    conversation_id,
                    user_id=(current_user or {}).get("user_id"),
                )
        except Exception as save_exc:
            logger.warning("Не удалось сохранить ошибку video gen: %s", save_exc)
        return True

    response = result.get("response") or ""
    meta = result.get("metadata") or {}
    inline_attachments = result.get("inline_attachments") or []

    try:
        await save_video_generation_assistant_message(
            content=response,
            metadata=meta,
            conversation_id=conversation_id,
            project_id=project_id,
            user_id=(current_user or {}).get("user_id"),
            regenerate=regenerate,
            assistant_message_id=assistant_message_id,
        )
    except Exception as save_exc:
        logger.warning("Не удалось сохранить ответ video gen: %s", save_exc)

    payload = _stream_ids_payload({
        "response": response,
        "timestamp": datetime.now().isoformat(),
        "was_streaming": streaming,
        "inline_attachments": inline_attachments,
        "video_generation": True,
    })
    await sio.emit("chat_complete", payload, room=sid)
    return True


def register_handlers(sio):
    """Регистрирует все Socket.IO обработчики на переданный sio-сервер"""

    @sio.event
    async def connect(sid, environ, auth = None):
        token = _extract_socket_token(auth, environ)
        if not token:
            logger.warning("Socket.IO connect rejected: отсутствует токен sid=%s", sid)
            raise ConnectionRefusedError("Не авторизован")
        try:
            # Проверяем ТОЛЬКО подпись и срок действия JWT, без _is_active_session.
            # Это позволяет переподключиться после рестарта пода (когда in-memory словарь
            # сессий пуст, но JWT ещё валиден). Проверка активности сессии выполняется
            # при обработке каждого сообщения (_get_socket_user_context).
            user_data = decode_token_signature_only(token)
        except Exception as e:
            logger.warning("Socket.IO connect rejected: невалидный JWT sid=%s reason=%s", sid, e)
            raise ConnectionRefusedError("Неверный или просроченный токен")

        _cef_remote = cef_socket_remote_from_environ(environ if isinstance(environ, dict) else None)
        _sess: Dict[str, Any] = {
            "user": {
                "username": user_data["username"],
                "user_id": user_data["user_id"],
                "session_id": user_data.get("session_id"),
                "token": token,
            }
        }
        if _cef_remote:
            _sess["cef_remote"] = _cef_remote
        await sio.save_session(sid, _sess)
        logger.info(
            "Socket.IO client connected: sid=%s user_id=%s session_id=%s",
            sid,
            user_data.get("user_id"),
            user_data.get("session_id"),
        )
        stop_generation_flags[sid] = False
        await sio.emit("connected", {"data": "Connected to astrachat"}, room=sid)

    @sio.event
    async def disconnect(sid):
        logger.debug(f"Socket.IO client disconnected: {sid}")
        stop_generation_flags.pop(sid, None)
        _forget_generation(sid)

    @sio.event
    async def ping(sid, data):
        try:
            await sio.emit(
                "pong", {"timestamp": data.get("timestamp", 0), "server_time": datetime.now().isoformat()}, room=sid
            )
        except Exception:
            logger.exception("Ошибка обработки ping")

    @sio.event
    async def stop_generation(sid, data):
        logger.info(f"Socket.IO: команда остановки генерации от {sid}")
        stop_generation_flags[sid] = True
        # Останавливаем всё, что уже запущено на этом сокете. Генерация,
        # начатая ПОСЛЕ нажатия, этим стопом не затрагивается.
        _stopped_upto[sid] = _generation_seq.get(sid, 0)
        payload = {
            "content": "Генерация остановлена",
            "timestamp": datetime.now().isoformat(),
        }
        if isinstance(data, dict):
            if data.get("conversation_id"):
                payload["conversation_id"] = data.get("conversation_id")
            if data.get("request_id"):
                payload["request_id"] = data.get("request_id")
        await sio.emit("generation_stopped", payload, room=sid)

    @sio.event
    async def stop_transcription(sid, data):
        logger.info(f"Socket.IO: команда остановки транскрибации от {sid}")
        stop_transcription_flags[sid] = True
        await sio.emit(
            "transcription_stopped",
            {"message": "Транскрибация остановлена", "timestamp": datetime.now().isoformat()},
            room=sid,
        )

    @sio.event
    async def chat_message(sid, data):
        if not ask_agent or not save_dialog_entry:
            await sio.emit("chat_error", {"error": "AI services not available"}, room=sid)
            return
        rag_runtime_token = None
        model_runtime_token = None
        try:
            user_ctx = await _get_socket_user_context(sio, sid)
            if not user_ctx:
                await sio.emit("chat_error", {"error": "Не авторизован"}, room=sid)
                await sio.disconnect(sid)
                return
            try:
                validated_user = decode_token(user_ctx.get("token", ""))
            except Exception:
                logger.exception("Socket.IO chat rejected: session invalid sid= reason=")
                await sio.emit(
                    "chat_error", {"error": "Сессия завершена: выполнен вход с другого устройства/окна"}, room=sid
                )
                await sio.disconnect(sid)
                return
            user_message = data.get("message", "")
            streaming = data.get("streaming", True)
            _et = data.get("enable_thinking", False)
            if isinstance(_et, bool):
                enable_thinking = _et
            elif isinstance(_et, (int, float)) and _et == 0:
                enable_thinking = False
            elif isinstance(_et, str) and _et.strip().lower() in ("0", "false", "no", "off", ""):
                enable_thinking = False
            else:
                enable_thinking = bool(_et)
            # Новая генерация получает свой номер. Общий флаг больше не трогаем:
            # он снимал стоп с еще живой предыдущей генерации.
            _begin_generation(sid)
            user_message_id = data.get("message_id", None)
            conversation_id = data.get("conversation_id", None)
            # Изоляция пользователей: не даём читать/писать чужой диалог по
            # угаданному conversation_id и не полагаемся на общий на процесс стейт.
            if conversation_id:
                import backend.database.memory_service as mem_mod

                _owner_ok = await mem_mod.verify_conversation_owner(
                    conversation_id,
                    validated_user.get("user_id") if validated_user else None,
                )
                if not _owner_ok:
                    logger.warning(
                        "chat_message: отказ в доступе к чужому диалогу conversation_id=%s user_id=%s",
                        conversation_id,
                        validated_user.get("user_id") if validated_user else None,
                    )
                    await sio.emit("chat_error", {"error": "Нет доступа к этому диалогу"}, room=sid)
                    return
                # Значение живёт только в контексте текущей задачи (не глобально).
                mem_mod.set_current_conversation_id(conversation_id)
            request_id = data.get("request_id", None)
            stream_id_tokens = _bind_stream_ids(conversation_id, request_id)
            use_kb_rag = bool(data.get("use_kb_rag", False))
            use_memory_library_rag = bool(data.get("use_memory_library_rag", False))
            _raw_inline_ctx = data.get("inline_context") or ""
            inline_context = str(_raw_inline_ctx).strip() if _raw_inline_ctx else ""
            _raw_inline_imgs = data.get("inline_images")
            inline_images: list = [str(x) for x in _raw_inline_imgs if x] if isinstance(_raw_inline_imgs, list) else []
            user_message_metadata = _build_user_inline_attachments_metadata(
                data.get("inline_attachments"),
                inline_context=inline_context,
            )
            requested_rag_strategy = str(data.get("rag_strategy") or "").strip().lower()
            # Стратегия стора подтянется через runtime settings после bind entity_ids
            effective_rag_strategy = (
                requested_rag_strategy
                if requested_rag_strategy in _VALID_RAG_STRATEGIES
                else None
            )
            agent_profile = await _resolve_agent_chat_params(
                data.get("agent_id"), validated_user.get("user_id") if validated_user else None
            )
            agent_profile = await enrich_agent_profile_with_user_settings(
                agent_profile, validated_user.get("user_id") if validated_user else None
            )
            agent_profile = merge_chat_artifacts_settings(
                agent_profile if isinstance(agent_profile, dict) else {},
                data if isinstance(data, dict) else {},
            )
            _agent_ms = agent_profile.get("model_settings") if isinstance(agent_profile, dict) else None
            # Биндим ИТОГ (персональные настройки + карточка агента поверх), а не
            # одну карточку: иначе ключи, которых в ней нет, приезжали бы из
            # заводских дефолтов, а «Настройки → Модели» не работали бы нигде.
            # Без агента итог — это персональные настройки, их тоже надо
            # применить: раньше обычный чат жил на дефолтах кластера.
            _eff_ms = (
                agent_profile.get("effective_model_settings")
                if isinstance(agent_profile, dict)
                else None
            )
            if isinstance(_eff_ms, dict) and _eff_ms:
                model_runtime_token = bind_user_model_runtime(_eff_ms)
                logger.info(
                    "[LLM] Настройка генерации: агент id=%s поверх персональных user_id=%s",
                    data.get("agent_id"),
                    validated_user.get("user_id") if validated_user else None,
                )
            _ams_stream = _agent_ms.get("streaming") if isinstance(_agent_ms, dict) else None
            if _ams_stream is not None:
                streaming = bool(_ams_stream)
            if not (data.get("tool_ids") or data.get("mcp_tool_ids")):
                _agent_mcp = agent_mcp_tool_ids(agent_profile if isinstance(agent_profile, dict) else {})
                if _agent_mcp:
                    data["tool_ids"] = _agent_mcp
            _agent_plugins = agent_plugin_ids(agent_profile if isinstance(agent_profile, dict) else {})
            if _agent_plugins:
                data["__plugin_ids__"] = _agent_plugins
            agent_kb_enabled = bool(agent_profile.get("file_search_enabled"))
            agent_kb_doc_ids = agent_profile.get("kb_document_ids") or []
            use_agent_scoped_kb = (
                agent_kb_enabled and isinstance(agent_kb_doc_ids, list) and (len(agent_kb_doc_ids) > 0)
            )
            is_regenerate = bool(data.get("regenerate"))
            skip_user_save = is_regenerate
            with logged_suppress(logger):
                from backend.settings.cef_logger.cef_audit_context import cef_audit_set

                cef_rem = None
                with logged_suppress(logger):
                    _sess = await sio.get_session(sid)
                    if isinstance(_sess, dict):
                        _cr = _sess.get("cef_remote")
                        if isinstance(_cr, dict) and _cr.get("src"):
                            cef_rem = _cr
                cef_audit_set(user=validated_user, conversation_id=conversation_id, socket_remote=cef_rem)
            project_id = data.get("project_id") or None
            if project_id:
                logger.debug(f"[chat_message] project_id из payload: {project_id}")
            else:
                project_id = await get_conversation_project_id(conversation_id)
                if project_id:
                    logger.debug(f"[chat_message] project_id из MongoDB: {project_id}")

            # Настройки берём у САМИХ сущностей, не у собеседника: агента
            # могли расшарить, и искать надо теми параметрами, которыми залит
            # его корпус, а не теми, что стоят у читателя.
            try:
                _uid = validated_user.get("user_id")
                entity_ids: dict = {}
                if project_id:
                    entity_ids["project"] = str(project_id)
                _agent_id_raw = data.get("agent_id")
                if _agent_id_raw is not None:
                    try:
                        _aid = int(_agent_id_raw)
                        if _aid > 0:
                            entity_ids["agent"] = str(_aid)
                    except (TypeError, ValueError):
                        pass
                _scopes_snapshot: dict = {}
                if entity_ids.get("project"):
                    _scopes_snapshot["project"] = await get_entity_rag_settings(
                        "project", entity_ids["project"]
                    )
                if entity_ids.get("agent"):
                    _scopes_snapshot["agent"] = await get_entity_rag_settings(
                        "agent", entity_ids["agent"]
                    )
                rag_runtime_token = bind_user_rag_runtime(
                    _scopes_snapshot, await get_user_memory_strategy(_uid)
                )
                _project_log_name = str(data.get("project_name") or "").strip()
                if entity_ids.get("project"):
                    logger.info(
                        "[RAG] Настройки применены для проекта %s",
                        _project_log_name or entity_ids["project"],
                    )
                if entity_ids.get("agent"):
                    _agent_log_name = str(
                        (agent_profile or {}).get("name")
                        or data.get("agent_name")
                        or ""
                    ).strip()
                    logger.info(
                        "[RAG] Настройки применены для агента %s",
                        _agent_log_name or entity_ids["agent"],
                    )
            except Exception:
                logger.exception("Не удалось загрузить персональные RAG-настройки")
                rag_runtime_token = bind_user_rag_runtime({})

            project_memory = data.get("project_memory") or "default"
            project_instructions = data.get("project_instructions") or ""
            if project_id and project_memory == "project-only":
                from backend.database.memory_service import get_project_memory_history

                history = await get_project_memory_history(project_id, max_entries=state.memory_max_messages)
                logger.debug(f"[chat_message] project-only история: {len(history)} сообщений из проекта {project_id}")
            else:
                history = await get_recent_dialog_history(
                    max_entries=state.memory_max_messages, conversation_id=conversation_id
                )
            # Follow-up по ранее прикреплённым картинкам: подтянуть из истории.
            try:
                from backend.services.inline_images import merge_inline_images_from_history

                inline_images = merge_inline_images_from_history(history, inline_images)
            except Exception:
                logger.exception("Не удалось подтянуть inline-картинки из истории")
            if not skip_user_save:
                try:
                    if project_id:
                        from backend.database.memory_service import save_dialog_entry_to_project

                        await save_dialog_entry_to_project(
                            "user",
                            user_message,
                            project_id,
                            conversation_id,
                            user_message_id,
                            metadata=user_message_metadata,
                            user_id=validated_user["user_id"],
                        )
                    else:
                        await save_dialog_entry(
                            "user",
                            user_message,
                            user_message_metadata,
                            user_message_id,
                            conversation_id,
                            user_id=validated_user["user_id"],
                        )
                except RuntimeError as e:
                    if "MongoDB" in str(e):
                        await sio.emit("chat_error", _stream_ids_payload({"error": "MongoDB недоступен."}), room=sid)
                        return
                    raise
            if not bool(data.get("coding_mode")):
                handled_video = await _handle_chat_video_generation_request(
                    sio,
                    sid,
                    user_message=user_message,
                    conversation_id=conversation_id,
                    project_id=project_id,
                    current_user=validated_user,
                    streaming=streaming,
                    video_gen_preset_id=(data.get("video_gen_preset_id") or None),
                    video_generation_mode=bool(data.get("video_generation_mode")),
                    regenerate=is_regenerate,
                    assistant_message_id=str(data.get("assistant_message_id") or "").strip() or None,
                )
                if handled_video:
                    return
                handled_image = await _handle_chat_image_generation_request(
                    sio,
                    sid,
                    user_message=user_message,
                    conversation_id=conversation_id,
                    project_id=project_id,
                    current_user=validated_user,
                    streaming=streaming,
                    image_gen_preset_id=(data.get("image_gen_preset_id") or None),
                    image_generation_mode=bool(data.get("image_generation_mode")),
                    regenerate=is_regenerate,
                    assistant_message_id=str(data.get("assistant_message_id") or "").strip() or None,
                )
                if handled_image:
                    return

            use_multi_llm_mode = bool(data.get("model_comparison_enabled", False))
            chat_mode = "model-comparison" if use_multi_llm_mode else "direct"
            logger.info(
                "[RAG] chat_message mode=%s coding_mode=%s plan_mode=%s workspace_path=%r effective_strategy=%s payload_rag_strategy=%r settings_rag_strategy=%s agentic_rag_enabled=%s use_kb_rag=%s use_memory_library_rag=%s use_agent_scoped_kb=%s project_id=%s",
                chat_mode,
                bool(data.get("coding_mode")),
                bool(data.get("plan_mode")),
                str(data.get("workspace_path") or "").strip(),
                effective_rag_strategy,
                requested_rag_strategy or "",
                "project=%s agent=%s memory=%s"
                % (
                    runtime_rag_strategy("project"),
                    runtime_rag_strategy("agent"),
                    runtime_memory_strategy(),
                ),
                runtime_agentic_rag_enabled(),
                use_kb_rag,
                use_memory_library_rag,
                use_agent_scoped_kb,
                project_id,
            )
            _ia = data.get("inline_attachments") if isinstance(data, dict) else None
            logger.info(
                "[plugin-dispatch] chat_message mode=%s agent_id=%s plugins_enabled=%s plugin_ids=%s "
                "inline_attachments=%s inline_context_chars=%s",
                chat_mode,
                data.get("agent_id") if isinstance(data, dict) else None,
                (agent_profile or {}).get("plugins_enabled") if isinstance(agent_profile, dict) else None,
                (agent_profile or {}).get("plugin_ids") if isinstance(agent_profile, dict) else None,
                [
                    {
                        "name": (a or {}).get("name") if isinstance(a, dict) else None,
                        "has_object": bool(isinstance(a, dict) and a.get("minio_object")),
                        "bucket": (a or {}).get("minio_bucket") if isinstance(a, dict) else None,
                    }
                    for a in (_ia or [])
                ]
                if isinstance(_ia, list)
                else _ia,
                len(inline_context or ""),
            )

            async def async_stream_cb(chunk, acc, stream_role="content"):
                with logged_suppress(logger):
                    if stream_role == "reasoning":
                        await sio.emit(
                            "chat_thinking",
                            _stream_ids_payload({"chunk": chunk, "accumulated": acc, "thinking": chunk, "stream_role": "reasoning"}),
                            room=sid,
                        )
                    else:
                        await sio.emit("chat_chunk", _stream_ids_payload({"chunk": chunk, "accumulated": acc}), room=sid)

            loop = asyncio.get_event_loop()

            def sync_stream_cb(chunk, acc, stream_role="content"):
                if _generation_stopped(sid):
                    return False
                asyncio.run_coroutine_threadsafe(async_stream_cb(chunk, acc, stream_role), loop)
                return True

            generation_t0 = time.perf_counter()
            if use_multi_llm_mode and not bool(data.get("coding_mode")):
                slot = str(data.get("multi_llm_slot_regenerate") or "").strip()
                models_subset = [slot] if bool(data.get("regenerate")) and slot else None
                await _handle_multi_llm(
                    sio,
                    sid,
                    data,
                    user_message,
                    streaming,
                    conversation_id,
                    use_kb_rag,
                    use_memory_library_rag,
                    loop,
                    use_agent_scoped_kb,
                    agent_kb_doc_ids,
                    project_id=project_id,
                    project_instructions=project_instructions,
                    rag_strategy=effective_rag_strategy,
                    models_subset=models_subset,
                    enable_thinking=enable_thinking,
                    inline_context=inline_context,
                    inline_images=inline_images,
                    current_user=validated_user,
                    agent_profile=agent_profile,
                    generation_started_at=generation_t0,
                )
                return
            await _run_direct_or_chain(
                sio,
                sid,
                data,
                user_message,
                streaming,
                conversation_id,
                history,
                use_kb_rag,
                use_memory_library_rag,
                agent_profile,
                sync_stream_cb,
                loop,
                use_agent_scoped_kb,
                agent_kb_doc_ids,
                project_id=project_id,
                project_instructions=project_instructions,
                rag_strategy=effective_rag_strategy,
                current_user=validated_user,
                enable_thinking=enable_thinking,
                inline_context=inline_context,
                inline_images=inline_images,
                generation_started_at=generation_t0,
            )
        except Exception as e:
            logger.exception("Ошибка операции")
            logger.error(f"Socket.IO chat error: {e}", exc_info=True)
            with logged_suppress(logger):
                await sio.emit("chat_error", _stream_ids_payload({"error": str(e)}), room=sid)
        finally:
            with logged_suppress(logger):
                _reset_stream_ids(locals().get("stream_id_tokens"))
            if rag_runtime_token is not None:
                with logged_suppress(logger):
                    reset_user_rag_runtime(rag_runtime_token)
            if model_runtime_token is not None:
                with logged_suppress(logger):
                    reset_user_model_runtime(model_runtime_token)


async def _handle_multi_llm(
    sio,
    sid,
    data,
    user_message,
    streaming,
    conversation_id,
    use_kb_rag,
    use_memory_library_rag,
    loop,
    use_agent_scoped_kb=False,
    agent_kb_doc_ids=None,
    project_id=None,
    project_instructions=None,
    rag_strategy="auto",
    models_subset=None,
    enable_thinking=False,
    inline_context: str = "",
    inline_images: list = None,
    current_user=None,
    agent_profile=None,
    generation_started_at: Optional[float] = None,
):
    multi_llm_models = get_model_comparison_models()
    if not multi_llm_models:
        await sio.emit("chat_error", _stream_ids_payload({"error": "Модели не выбраны"}), room=sid)
        return
    if models_subset is not None:
        allowed = set(multi_llm_models)
        multi_llm_models = [m for m in models_subset if m in allowed]
        if not multi_llm_models:
            await sio.emit("chat_error", _stream_ids_payload({"error": "Указанная модель не входит в список multi-LLM"}), room=sid)
            return
    _terminal_chat_inference_banner(
        sid=sid,
        conversation_id=conversation_id,
        user_preview=user_message,
        mode_label=f"MULTI-LLM - модели: {', '.join(multi_llm_models)}",
        extra_line="Ниже для каждой модели - отдельный блок перед вызовом LLM.",
        enable_thinking=enable_thinking,
    )
    min_sim, rag_block = rag_guard_env()
    project_min_sim, _ = rag_guard_env("project")
    agent_min_sim, _ = rag_guard_env("agent")
    sources = resolve_active_rag_sources(
        project_id=project_id,
        use_agent_scoped_kb=use_agent_scoped_kb,
        agent_kb_doc_ids=agent_kb_doc_ids,
        use_memory_library_rag=use_memory_library_rag,
        use_kb_rag=use_kb_rag,
    )
    if await rag_reindex_blocks_active_sources(sources, rag_client, project_id=project_id):
        await _notify_reindex_wait(sio, sid)
        await _abort_multi_llm_reindex(
            sio, sid, multi_llm_models, conversation_id, project_id, current_user
        )
        return
    context_added = False
    rag_scopes: set = set()
    final_user_message = user_message
    if rag_client and sources.project:
        try:
            proj_rows = list(await rag_client.project_rag_list_documents(project_id) or [])
            proj_id_name = build_rag_id_to_filename(proj_rows)
            proj_hits = await rag_client.project_rag_search(
                user_message, project_id=project_id, k=get_rag_chat_top_k("project"), strategy=rag_strategy
            )
            proj_hits = filter_rag_hits_by_score(proj_hits, project_min_sim)
            if proj_hits:
                parts, _m = format_rag_fragments(
                    proj_hits,
                    proj_id_name,
                    max_chars=rag_context_max_chars("project (multi-llm)"),
                    store_label="project (multi-llm)",
                    include_chunk_meta=False,
                )
                if parts:
                    final_user_message = f"""Документы проекта (RAG):
{chr(10).join(parts)}
Вопрос: {user_message}"""
                    context_added = True
                    rag_scopes.add("project")
                    logger.info(f"[multi-llm project_rag] {len(proj_hits)} фрагментов, project={project_id}")
        except RagReindexInProgress:
            await _notify_reindex_wait(sio, sid)
            await _abort_multi_llm_reindex(
                sio, sid, multi_llm_models, conversation_id, project_id, current_user
            )
            return
        except Exception:
            logger.exception("multi-llm project RAG error")
    # KB — только документы выбранного агента; широкий поиск по всей KB убран
    # (тумблер библиотеки без агента ищет только memory-rag).
    if rag_client and sources.agent_kb:
        prefix = "База Знаний (документы агента)"
        try:
            kb_id_name = build_rag_id_to_filename(list(await rag_client.kb_list_documents() or []))
            hits = await kb_search_agent_documents(
                rag_client,
                user_message,
                agent_kb_doc_ids or [],
                k=get_rag_chat_top_k("agent"),
                strategy=rag_strategy,
            )
            hits = filter_rag_hits_by_score(list(hits or []), agent_min_sim)
            if hits:
                parts, _m = format_rag_fragments(
                    hits,
                    kb_id_name,
                    max_chars=rag_context_max_chars("kb (multi-llm)"),
                    store_label="kb (multi-llm)",
                    include_chunk_meta=False,
                )
                if parts:
                    final_user_message = f"""{prefix}:
{''.join(parts)}

{final_user_message}"""
                    context_added = True
                    rag_scopes.add("agent")
        except RagReindexInProgress:
            await _notify_reindex_wait(sio, sid)
            await _abort_multi_llm_reindex(
                sio, sid, multi_llm_models, conversation_id, project_id, current_user
            )
            return
        except Exception:
            logger.exception("multi-llm kb_search error")
    if sources.memory and rag_client:
        try:
            mem_id_name = build_rag_id_to_filename(list(await rag_client.memory_rag_list_documents() or []))
            hits = await rag_client.memory_rag_search(user_message, strategy=rag_strategy)
            # Memory: порог только из env (RAG_MEMORY_SIMILARITY_THRESHOLD), не из UI.
            hits = filter_rag_hits_by_score(list(hits or []), get_memory_similarity_threshold())
            prefix = "Документы из настроек (библиотека памяти)"
            if hits:
                parts, _m = format_rag_fragments(
                    hits,
                    mem_id_name,
                    max_chars=rag_context_max_chars("memory (multi-llm)"),
                    store_label="memory (multi-llm)",
                    include_chunk_meta=False,
                )
                if parts:
                    final_user_message = f"""{prefix}:
{''.join(parts)}

{final_user_message}"""
                    context_added = True
                    rag_scopes.add("memory")
        except RagReindexInProgress:
            await _notify_reindex_wait(sio, sid)
            await _abort_multi_llm_reindex(
                sio, sid, multi_llm_models, conversation_id, project_id, current_user
            )
            return
        except Exception:
            logger.exception("multi-llm memory_rag_search error")
    if inline_context:
        inline_block = f"""[Прикреплённый документ]
{inline_context}"""
        if final_user_message != user_message:
            final_user_message = f"{inline_block}\n\n{final_user_message}"
        else:
            final_user_message = f"""{inline_block}

[Вопрос пользователя]
{user_message}"""
        context_added = True
        logger.info(
            f"[multi-llm inline_context] {len(inline_context)} символов, RAG-контекст {('совмещён' if final_user_message != inline_block else 'не применялся')}"
        )
    inline_imgs = list(inline_images) if inline_images else None
    rag_override = runtime_rag_system_prompt(rag_scopes) or None

    feedback_block = await build_user_feedback_system_block(
        (current_user or {}).get("user_id"),
        conversation_id=conversation_id,
    )
    user_cpm = await get_user_prompt_manager((current_user or {}).get("user_id"))
    if user_cpm is None:
        user_cpm = context_prompt_manager

    skill_append = ""
    lazy_skill_ids: list = []
    try:
        from backend.services.skills import apply_skills_to_chat, strip_skill_mentions

        skill_append, _stripped, lazy_skill_ids, allowed_tools_extra, _primed = await apply_skills_to_chat(
            system_prompt="",
            user_message=user_message,
            data=data or {},
            agent_profile=agent_profile if isinstance(agent_profile, dict) else {},
            current_user=current_user,
        )
        final_user_message = strip_skill_mentions(final_user_message)
        try:
            from backend.services.tag_mentions import strip_tag_mentions

            final_user_message = strip_tag_mentions(final_user_message)
        except Exception:
            pass
        if lazy_skill_ids or allowed_tools_extra:
            from backend.tools.tool_context import get_tool_context, set_tool_context

            ctx = get_tool_context() or {}
            if lazy_skill_ids:
                ctx["__skill_ids__"] = lazy_skill_ids
            ctx["current_user"] = current_user
            set_tool_context(ctx)
        if allowed_tools_extra:
            existing = list(data.get("tool_ids") or data.get("mcp_tool_ids") or [])
            merged = list(dict.fromkeys([*existing, *allowed_tools_extra]))
            data["tool_ids"] = merged
        log_pre_loop(
            phase="skills",
            chat_id=conversation_id,
            agent_id=(
                (agent_profile or {}).get("agent_id")
                if isinstance(agent_profile, dict)
                else None
            ),
            detail=(
                f"отложенные={list(lazy_skill_ids or [])} "
                f"ручные={list((_primed or {}).get('manual') or [])} "
                f"всегда={list((_primed or {}).get('always_apply') or [])} "
                f"доп. инструменты={list(allowed_tools_extra or [])} "
                f"символов в промпте={len(skill_append or '')}"
            ),
        )
    except Exception:
        logger.exception("[multi-llm] skills injection failed")

    def _system_prompt_for_model(model_path: Optional[str]) -> Optional[str]:
        prompt = None
        agent_sp = (agent_profile.get("system_prompt") or "") if isinstance(agent_profile, dict) else ""
        if user_cpm:
            prompt = user_cpm.resolve_chat_system_prompt(
                model_path,
                agent_system_prompt=agent_sp,
                project_instructions=project_instructions,
            )
        elif project_instructions and project_instructions.strip():
            prompt = project_instructions.strip()
        elif agent_sp and agent_sp.strip():
            prompt = agent_sp.strip()
        prompt = merge_feedback_into_system_prompt(prompt, feedback_block)
        if context_added:
            prompt = merge_strict_rag_system_prompt(prompt, rag_override=rag_override)
        if skill_append:
            from backend.services.skills import append_to_system_prompt

            prompt = append_to_system_prompt(prompt, skill_append)
        try:
            from backend.plugins.tools import build_plugins_system_append
            from backend.realtime.helpers import agent_plugin_ids as _agent_plugin_ids
            from backend.services.skills import append_to_system_prompt as _append_plugins

            _pids = _agent_plugin_ids(agent_profile if isinstance(agent_profile, dict) else {})
            plugins_block = build_plugins_system_append(_pids)
            if plugins_block:
                prompt = _append_plugins(prompt, plugins_block)
        except Exception:
            logger.exception("[multi-llm] plugins prompt injection failed")
        try:
            from backend.prompts.artifacts import maybe_artifacts_prompt_for_agent
            from backend.services.skills import append_to_system_prompt as _append_sp

            artifacts_block = maybe_artifacts_prompt_for_agent(
                agent_profile if isinstance(agent_profile, dict) else None
            )
            if artifacts_block:
                prompt = _append_sp(prompt, artifacts_block)
        except Exception:
            logger.exception("[multi-llm] artifacts prompt injection failed")
        return prompt

    canned = await maybe_rag_no_evidence_message(
        rag_client,
        block_when_no_evidence=rag_block,
        context_added=context_added,
        project_id=project_id,
        use_kb_rag=use_kb_rag,
        use_memory_library_rag=sources.memory,
        use_agent_scoped_kb=sources.agent_kb,
        agent_kb_doc_ids=agent_kb_doc_ids,
    )
    if canned:
        if multi_llm_models:
            await sio.emit(
                "multi_llm_start",
                _stream_ids_payload(
                    {"model": multi_llm_models[0], "models": multi_llm_models, "total_models": len(multi_llm_models)}
                ),
                room=sid,
            )
        for i, model_name in enumerate(multi_llm_models):
            await sio.emit(
                "multi_llm_complete",
                _stream_ids_payload(
                    {"model": model_name, "response": canned, "error": False, "index": i, "total": len(multi_llm_models)}
                ),
                room=sid,
            )
        # Сохраняем canned-ответ в историю — иначе после F5 он исчезает
        try:
            if conversation_id and multi_llm_models:
                dur = _generation_duration_sec(generation_started_at)
                slots = [
                    {
                        "model": m,
                        "content": canned,
                        "error": False,
                        "generation_duration_sec": dur,
                    }
                    for m in multi_llm_models
                ]
                combined = "\n\n".join(
                    f"{s['model']}:\n{s['content']}" for s in slots
                )
                assistant_meta = _with_generation_duration(
                    {"multi_llm_responses": slots},
                    generation_started_at,
                )
                if project_id:
                    from backend.database.memory_service import save_dialog_entry_to_project

                    await save_dialog_entry_to_project(
                        "assistant",
                        combined,
                        project_id,
                        conversation_id,
                        metadata=assistant_meta,
                        user_id=(current_user or {}).get("user_id"),
                    )
                else:
                    await save_dialog_entry(
                        "assistant",
                        combined,
                        assistant_meta,
                        None,
                        conversation_id,
                        user_id=(current_user or {}).get("user_id"),
                    )
        except Exception:
            logger.exception("Ошибка сохранения canned multi-LLM ответа")
        return
    n_models = len(multi_llm_models)
    tool_ids = resolve_chat_tool_ids(data.get("tool_ids") or data.get("mcp_tool_ids"))
    _ap = agent_profile if isinstance(agent_profile, dict) else {}
    mcp_enabled = bool((tool_ids or (_ap.get("subagents") or {}).get("enabled")) and current_user and (not inline_imgs))
    mcp_temperature = float(
        data.get("temperature") if data.get("temperature") is not None else (_ap.get("temperature") or 0.7)
    )
    mcp_max_tokens = int(
        data.get("max_tokens") if data.get("max_tokens") is not None else (_ap.get("max_tokens") or 1024)
    )

    async def _gen_one(model_name: str):
        idx = multi_llm_models.index(model_name)
        eff_system_prompt = _system_prompt_for_model(model_name)

        async def _emit_complete(res: dict) -> dict:
            # Единственная точка, где мульти-LLM отдаёт готовый ответ. Без проверки
            # стопа остановленная генерация досылала ответ каждой модели, даже когда
            # пользователь уже задал новый вопрос.
            if _generation_stopped(sid):
                return res
            await sio.emit(
                "multi_llm_complete",
                _stream_ids_payload(
                    {
                        "model": res.get("model", model_name),
                        "response": res.get("response", "") or "",
                        "error": bool(res.get("error", False)),
                        "index": idx,
                        "total": n_models,
                        "mcp_mode": res.get("mcp_mode"),
                        "mcp_tool_calls": res.get("mcp_tool_calls"),
                    }
                ),
                room=sid,
            )
            return res

        try:
            await sio.emit(
                "multi_llm_start",
                _stream_ids_payload(
                    {"model": model_name, "models": multi_llm_models, "total_models": n_models, "mcp_enabled": mcp_enabled}
                ),
                room=sid,
            )
            if mcp_enabled:
                try:
                    from backend.mcp.chat_integration import run_mcp_for_chat

                    _model_events: list = []

                    async def _mcp_event_cb(payload):
                        event = dict(payload)
                        event["model"] = model_name
                        _model_events.append(event)
                        await sio.emit("chat_mcp_event", _stream_ids_payload(event), room=sid)

                    mcp_result = await run_mcp_for_chat(
                        tool_ids=tool_ids,
                        user_message=final_user_message,
                        history=[],
                        system_prompt=eff_system_prompt,
                        model_path=model_name,
                        user=current_user,
                        chat_id=conversation_id,
                        message_id=data.get("message_id"),
                        temperature=mcp_temperature,
                        max_tokens=mcp_max_tokens,
                        enable_thinking=enable_thinking,
                        emit_event=_mcp_event_cb,
                        agent_profile=_ap if _ap else None,
                        inline_attachments=(
                            data.get("inline_attachments") if isinstance(data, dict) else None
                        ),
                    )
                    if mcp_result is not None:
                        resp = _carry_subagent_artifacts(mcp_result.content or "", _model_events)
                        if streaming and resp:
                            await sio.emit(
                                "multi_llm_chunk",
                                _stream_ids_payload({"model": model_name, "chunk": resp, "accumulated": resp}),
                                room=sid,
                            )
                        if context_added and resp.strip():
                            resp = await maybe_replace_ungrounded(
                                final_user_message[:20000], resp, RAG_STRICT_NOT_FOUND_MESSAGE
                            )
                        logger.info(
                            "Multi-LLM MCP: model=%s mode=%s tools=%s",
                            model_name,
                            mcp_result.mode,
                            mcp_result.tool_calls_executed,
                        )
                        return await _emit_complete(
                            {
                                "model": model_name,
                                "response": resp,
                                "error": False,
                                "mcp_mode": mcp_result.mode,
                                "mcp_tool_calls": mcp_result.tool_calls_executed,
                            }
                        )
                except Exception:
                    logger.exception("Multi-LLM MCP error model=")
            model_path = model_name

            def _model_stream_cb(chunk, acc):
                if _generation_stopped(sid):
                    return False
                asyncio.run_coroutine_threadsafe(
                    sio.emit("multi_llm_chunk", _stream_ids_payload({"model": model_name, "chunk": chunk, "accumulated": acc}), room=sid),
                    loop,
                )
                return True

            with concurrent.futures.ThreadPoolExecutor() as ex:
                _runner = _make_ctx_runner(
                    lambda: ask_agent(
                        final_user_message,
                        [],
                        mcp_max_tokens,
                        streaming,
                        _model_stream_cb if streaming else None,
                        model_path,
                        None,
                        images=inline_imgs,
                        system_prompt=eff_system_prompt,
                        temperature=mcp_temperature,
                        enable_thinking=enable_thinking,
                    )
                )
                resp = await asyncio.get_event_loop().run_in_executor(ex, _runner)
            if context_added and isinstance(resp, str) and resp.strip():
                resp = await maybe_replace_ungrounded(final_user_message[:20000], resp, RAG_STRICT_NOT_FOUND_MESSAGE)
            return await _emit_complete(
                {"model": model_name, "response": resp if isinstance(resp, str) else "", "error": False}
            )
        except Exception as e:
            logger.exception("Ошибка операции")
            return await _emit_complete({"model": model_name, "response": f"Ошибка: {e}", "error": True})

    results: list = await asyncio.gather(*[_gen_one(m) for m in multi_llm_models], return_exceptions=True)
    for i, result in enumerate(results):
        if isinstance(result, dict):
            continue
        await sio.emit(
            "multi_llm_complete",
            _stream_ids_payload(
                {
                    "model": multi_llm_models[i] if i < len(multi_llm_models) else "unknown",
                    "response": str(result),
                    "error": True,
                    "index": i,
                    "total": n_models,
                }
            ),
            room=sid,
        )

    # Сохраняем агрегированный multi-LLM ответ в историю, чтобы он не исчезал после перезагрузки.
    try:
        slots: list[dict] = []
        for i, model_name in enumerate(multi_llm_models):
            row = results[i] if i < len(results) and isinstance(results[i], dict) else None
            content = str((row or {}).get("response") or "")
            has_error = bool((row or {}).get("error", row is None))
            slot: dict = {
                "model": str((row or {}).get("model") or model_name),
                "content": content,
                "error": has_error,
            }
            alts = (row or {}).get("alternative_responses")
            if isinstance(alts, list) and alts:
                slot["alternative_responses"] = [str(v) for v in alts]
            current_idx = (row or {}).get("current_response_index")
            if isinstance(current_idx, int):
                slot["current_response_index"] = current_idx
            slots.append(slot)

        if conversation_id and slots:
            dur = _generation_duration_sec(generation_started_at)
            for slot in slots:
                slot["generation_duration_sec"] = dur
            combined = "\n\n".join(
                (
                    f"{slot['model']}:\n{slot['content']}".strip()
                    if slot.get("content")
                    else f"{slot['model']}:\n[пустой ответ]"
                )
                for slot in slots
            )
            assistant_meta = _with_generation_duration(
                {"multi_llm_responses": slots},
                generation_started_at,
            )
            if project_id:
                from backend.database.memory_service import save_dialog_entry_to_project

                await save_dialog_entry_to_project(
                    "assistant",
                    combined,
                    project_id,
                    conversation_id,
                    metadata=assistant_meta,
                    user_id=(current_user or {}).get("user_id"),
                )
            else:
                await save_dialog_entry(
                    "assistant",
                    combined,
                    assistant_meta,
                    None,
                    conversation_id,
                    user_id=(current_user or {}).get("user_id"),
                )
    except RuntimeError as e:
        logger.warning("Не удалось сохранить multi-LLM ответ: %s", e)
    except Exception:
        logger.exception("Ошибка сохранения агрегированного multi-LLM ответа")



async def _run_direct_or_chain(
    sio,
    sid,
    data,
    user_message,
    streaming,
    conversation_id,
    history,
    use_kb_rag,
    use_memory_library_rag,
    agent_profile,
    sync_stream_cb,
    loop,
    use_agent_scoped_kb=False,
    agent_kb_doc_ids=None,
    project_id=None,
    project_instructions=None,
    rag_strategy="auto",
    current_user=None,
    enable_thinking=False,
    inline_context: str = "",
    inline_images: list = None,
    generation_started_at: Optional[float] = None,
):
    """Один агент или последовательная цепочка Mixture-of-Agents."""
    from backend.agents.chain import (
        build_chain_user_message,
        format_visible_chain_content,
        iter_chain_stream_prefixes,
        prepare_step_socket_data,
        resolve_agent_chain,
    )

    user_id = (current_user or {}).get("user_id") if isinstance(current_user, dict) else None
    chain = await resolve_agent_chain(
        data.get("agent_id") if isinstance(data, dict) else None,
        agent_profile,
        user_id,
        user=current_user if isinstance(current_user, dict) else None,
    )
    if len(chain) <= 1:
        await _handle_direct(
            sio,
            sid,
            data,
            user_message,
            streaming,
            conversation_id,
            history,
            use_kb_rag,
            use_memory_library_rag,
            agent_profile,
            sync_stream_cb,
            loop,
            use_agent_scoped_kb,
            agent_kb_doc_ids,
            project_id=project_id,
            project_instructions=project_instructions,
            rag_strategy=rag_strategy,
            current_user=current_user,
            enable_thinking=enable_thinking,
            inline_context=inline_context,
            inline_images=inline_images,
            generation_started_at=generation_started_at,
        )
        return

    hide_seq = bool((chain[0] or {}).get("hide_sequential_outputs"))
    # Общий RAG для цепочки: головной агент (chain[0]) делится своей базой знаний
    # со всеми последующими агентами. Индивидуальный per-agent RAG при этом не трогаем —
    # он продолжает работать для одиночных агентов и когда флаг выключен.
    head_profile = chain[0] or {}
    shared_chain_rag = bool(head_profile.get("shared_chain_rag"))
    shared_kb_ids = head_profile.get("kb_document_ids") or []
    shared_kb_enabled = (
        shared_chain_rag
        and bool(head_profile.get("file_search_enabled"))
        and isinstance(shared_kb_ids, list)
        and len(shared_kb_ids) > 0
    )
    steps: list = []
    # #теги: фиксируем id в payload первого шага и убираем mentions из текста,
    # чтобы следующие hop и история не тащили сырой <#id|…>.
    if isinstance(data, dict):
        try:
            from backend.services.tag_mentions import (
                collect_mention_tag_ids,
                strip_tag_mentions,
            )

            mention_tags = collect_mention_tag_ids(
                user_message=user_message, data=data
            )
            if mention_tags:
                data = dict(data)
                data["tag_ids"] = mention_tags
            original_message = strip_tag_mentions(user_message)
        except Exception:
            original_message = user_message
    else:
        original_message = user_message
    logger.info(
        "[шаги агента] Старт цепочки: %s агентов подряд | скрывать промежуточные=%s | "
        "id=%s | чат=%s (это смена агентов, не лимит шагов LLM↔инструменты)",
        len(chain),
        hide_seq,
        [p.get("agent_id") for p in chain],
        conversation_id,
    )
    logger.debug(
        "[agent-chain] start n=%s hide_sequential=%s ids=%s",
        len(chain),
        hide_seq,
        [p.get("agent_id") for p in chain],
    )

    for i, profile in enumerate(chain):
        if _generation_stopped(sid):
            await sio.emit("generation_stopped", _stream_ids_payload({"message": "Генерация остановлена"}), room=sid)
            return
        is_first = i == 0
        is_last = i == len(chain) - 1
        step_name = (profile.get("name") or "Агент").strip() or "Агент"
        step_kb_ids_peek = profile.get("kb_document_ids") or []
        step_use_kb_peek = (
            bool(profile.get("file_search_enabled"))
            and isinstance(step_kb_ids_peek, list)
            and len(step_kb_ids_peek) > 0
        )
        log_chain_hop(
            index=i + 1,
            total=len(chain),
            agent_id=profile.get("agent_id"),
            agent_name=step_name,
            chat_id=conversation_id,
            has_rag=bool(
                (use_kb_rag if is_first else False)
                or use_memory_library_rag
                or step_use_kb_peek
                or shared_kb_enabled
                or project_id
            ),
            recursion_limit=resolve_recursion_limit(
                profile if isinstance(profile, dict) else None
            ),
        )
        await sio.emit(
            "chat_agent_update",
            _stream_ids_payload(
                {
                    "agent_id": profile.get("agent_id"),
                    "agent_name": step_name,
                    "index": i,
                    "total": len(chain),
                    "hide_sequential": hide_seq,
                    "is_last": is_last,
                }
            ),
            room=sid,
        )
        if hide_seq and not is_last:
            await sio.emit(
                "chat_thinking",
                _stream_ids_payload(
                    {
                        "status": "processing",
                        "message": f"{step_name} думает…",
                        "agent_chain": True,
                    }
                ),
                room=sid,
            )

        step_profile = await enrich_agent_profile_with_user_settings(profile, user_id)
        _eff_ms = step_profile.get("effective_model_settings") if isinstance(step_profile, dict) else None
        if isinstance(_eff_ms, dict) and _eff_ms:
            bind_user_model_runtime(_eff_ms)

        if shared_kb_enabled:
            # Общая база знаний головного агента для каждого шага цепочки
            # и субагентов этих шагов (_shared_chain_rag_ids / shared_chain_rag).
            step_profile = dict(step_profile) if isinstance(step_profile, dict) else {}
            step_profile["shared_chain_rag"] = True
            step_profile["_shared_chain_rag_ids"] = list(shared_kb_ids)
            if not step_profile.get("file_search_enabled"):
                step_profile["file_search_enabled"] = True
            step_kb_ids = list(shared_kb_ids)
            step_use_kb = True
        else:
            step_kb_ids = step_profile.get("kb_document_ids") or []
            step_use_kb = (
                bool(step_profile.get("file_search_enabled"))
                and isinstance(step_kb_ids, list)
                and len(step_kb_ids) > 0
            )
        step_data = prepare_step_socket_data(data, step_profile, is_first=is_first)
        step_message = original_message if is_first else build_chain_user_message(original_message, steps)
        stream_prefix, _header = iter_chain_stream_prefixes(steps, step_name, hide_sequential_outputs=hide_seq)

        raw = await _handle_direct(
            sio,
            sid,
            step_data,
            step_message,
            streaming,
            conversation_id,
            history,
            use_kb_rag if is_first else False,
            use_memory_library_rag,
            step_profile,
            sync_stream_cb,
            loop,
            step_use_kb,
            step_kb_ids,
            project_id=project_id,
            project_instructions=project_instructions,
            rag_strategy=rag_strategy,
            current_user=current_user,
            enable_thinking=enable_thinking,
            inline_context=inline_context if is_first else "",
            inline_images=inline_images if is_first else None,
            generation_started_at=generation_started_at,
            rag_query=original_message,
            stream_prefix=stream_prefix,
            emit_complete=False,
            save_response=False,
            suppress_ui_stream=hide_seq and not is_last,
        )
        if raw is None or _generation_stopped(sid):
            return
        step_content = raw.get("content") if isinstance(raw, dict) else raw
        step_reasoning = raw.get("reasoning") if isinstance(raw, dict) else ""
        step_document_search = raw.get("document_search") if isinstance(raw, dict) else None
        steps.append(
            {
                "agent_id": profile.get("agent_id"),
                "agent_name": step_name,
                "content": step_content or "",
                "reasoning": step_reasoning or "",
                "document_search": step_document_search or None,
            }
        )

    visible = format_visible_chain_content(steps, hide_sequential_outputs=hide_seq)
    last_profile = chain[-1]
    last_name = (last_profile.get("name") or "Агент").strip() or "Агент"
    payload = _stream_ids_payload(
        {
            "response": visible,
            "timestamp": datetime.now().isoformat(),
            "was_streaming": streaming,
            "generation_duration_sec": _generation_duration_sec(generation_started_at),
            "chain_steps": steps,
            "hide_sequential_outputs": hide_seq,
            "agent_id": last_profile.get("agent_id"),
            "agent_name": last_name,
        }
    )
    await sio.emit("chat_complete", payload, room=sid)
    try:
        meta = {
            "chain_steps": steps,
            "hide_sequential_outputs": hide_seq,
        }
        meta = _with_generation_duration(meta, generation_started_at)
        regen = _regen_save_kwargs(data)
        await save_assistant_response(
            visible,
            meta,
            conversation_id=conversation_id,
            user_id=user_id,
            project_id=project_id,
            **regen,
        )
    except Exception as e:
        logger.warning(f"Не удалось сохранить ответ цепочки: {e}")


async def _handle_direct(
    sio,
    sid,
    data,
    user_message,
    streaming,
    conversation_id,
    history,
    use_kb_rag,
    use_memory_library_rag,
    agent_profile,
    sync_stream_cb,
    loop,
    use_agent_scoped_kb=False,
    agent_kb_doc_ids=None,
    project_id=None,
    project_instructions=None,
    rag_strategy="auto",
    current_user=None,
    enable_thinking=False,
    inline_context: str = "",
    inline_images: list = None,
    generation_started_at: Optional[float] = None,
    rag_query: Optional[str] = None,
    stream_prefix: str = "",
    emit_complete: bool = True,
    save_response: bool = True,
    suppress_ui_stream: bool = False,
    complete_response: Optional[str] = None,
    extra_save_meta: Optional[dict] = None,
):
    chat_timer = StageTimer(
        "CHAT",
        store="direct",
        conversation_id=conversation_id,
        strategy=rag_strategy,
    )
    rag_query = (rag_query if rag_query is not None else user_message) or ""
    stream_prefix = stream_prefix or ""
    min_sim, rag_block = rag_guard_env()
    project_min_sim, _ = rag_guard_env("project")
    agent_min_sim, _ = rag_guard_env("agent")
    sources = resolve_active_rag_sources(
        project_id=project_id,
        use_agent_scoped_kb=use_agent_scoped_kb,
        agent_kb_doc_ids=agent_kb_doc_ids,
        use_memory_library_rag=use_memory_library_rag,
        use_kb_rag=use_kb_rag,
    )
    if await rag_reindex_blocks_active_sources(sources, rag_client, project_id=project_id):
        await _notify_reindex_wait(sio, sid)
        await _abort_chat_reindex(
            sio, sid, conversation_id, project_id, current_user
        )
        chat_timer.log(logger)
        return
    context_added = False
    rag_scopes: set = set()
    final_message = user_message
    images = list(inline_images) if inline_images else None
    proj_hits_for_trace = []
    proj_id_name: dict = {}
    _t_rag0 = time.perf_counter()
    if rag_client and sources.project:
        with logged_suppress(logger):
            proj_rows = list(await rag_client.project_rag_list_documents(project_id) or [])
            proj_id_name = build_rag_id_to_filename(proj_rows)
    if rag_client and sources.project:
        try:
            proj_hits = await rag_client.project_rag_search(
                rag_query, project_id=project_id, k=get_rag_chat_top_k("project"), strategy=rag_strategy
            )
            proj_hits = filter_rag_hits_by_score(proj_hits, project_min_sim)
            if proj_hits:
                if _is_structure_query(rag_query):
                    seen = {(d, i) for _, _, d, i in proj_hits}
                    for doc_id in {d for _, _, d, _ in proj_hits if d is not None}:
                        with logged_suppress(logger):
                            for c, sc, did, idx in await rag_client.get_document_start_chunks(doc_id, max_chunks=2):
                                if (did, idx) not in seen:
                                    proj_hits = [(c, sc, did, idx)] + proj_hits
                                    seen.add((did, idx))
                parts, _m = format_rag_fragments(
                    proj_hits,
                    proj_id_name,
                    max_chars=rag_context_max_chars("project (direct)"),
                    store_label="project (direct)",
                    include_chunk_meta=False,
                )
                proj_context = "\n".join(parts)
                final_message = f"""Документы проекта (RAG):
{proj_context}
Вопрос: {rag_query}
Ответь на основе этих документов. Перечисляй только то, что явно есть в фрагментах."""
                proj_hits_for_trace = proj_hits
                context_added = True
                rag_scopes.add("project")
                logger.info(f"[direct project_rag] {len(proj_hits)} фрагментов, project={project_id}")
        except RagReindexInProgress:
            await _notify_reindex_wait(sio, sid)
            await _abort_chat_reindex(
                sio, sid, conversation_id, project_id, current_user
            )
            return
        except Exception:
            logger.exception("Direct project RAG error")
    # «Библиотека» не вызывает global /search — только KB + memory ниже.
    document_search_trace = None
    kb_hits, mem_hits = ([], [])
    kb_id_name: dict = {}
    mem_id_name: dict = {}
    if proj_hits_for_trace and rag_client:
        trace_proj_map = proj_id_name
        if not trace_proj_map:
            try:
                trace_proj_map = build_rag_id_to_filename(
                    list(await rag_client.project_rag_list_documents(project_id) or [])
                )
            except Exception:
                logger.exception("Ошибка операции")
                trace_proj_map = {}
        hits_out, files_used = ([], set())
        _proj_rows = []
        for hit in proj_hits_for_trace:
            _, _, doc_id, _ = hit
            if doc_id is None:
                continue
            try:
                fn = trace_proj_map.get(int(doc_id))
            except (TypeError, ValueError):
                fn = None
            if not fn:
                fn = f"doc_{doc_id}"
            files_used.add(fn)
            _proj_rows.append((hit, fn))
        _proj_pcts = hits_to_relevance_percents([h for h, _ in _proj_rows])
        for ((content, _score, doc_id, chunk_idx), fn), pct in zip(_proj_rows, _proj_pcts):
            hits_out.append(
                {
                    "file": fn,
                    "anchor": f"chunk@{chunk_idx}({fn})",
                    "relevance": pct,
                    "content": (content or "")[:12000],
                    "chunkIndex": chunk_idx,
                    "documentId": doc_id,
                    "store": "project",
                }
            )
        if hits_out:
            document_search_trace = {
                "query": rag_query,
                "strategy": rag_strategy,
                "sourceFiles": sorted(files_used),
                "hits": hits_out,
            }
    if rag_client and (sources.agent_kb or sources.memory):
        kb_rows: list = []
        mem_rows: list = []
        kb_hits: list = []
        mem_hits: list = []
        if sources.agent_kb:
            try:
                kb_rows = list(await rag_client.kb_list_documents() or [])
            except Exception:
                logger.exception("Ошибка операции")
                kb_rows = []
        if sources.memory:
            try:
                mem_rows = list(await rag_client.memory_rag_list_documents() or [])
            except Exception:
                logger.exception("Ошибка операции")
                mem_rows = []
        if sources.agent_kb:
            try:
                kb_hits = list(
                    await kb_search_agent_documents(
                        rag_client,
                        rag_query,
                        agent_kb_doc_ids or [],
                        k=get_rag_chat_top_k("agent"),
                        strategy=rag_strategy,
                    )
                    or []
                )
            except RagReindexInProgress:
                await _notify_reindex_wait(sio, sid)
                await _abort_chat_reindex(
                    sio, sid, conversation_id, project_id, current_user
                )
                return
            except Exception:
                logger.exception("KB search error")
            kb_hits = filter_rag_hits_by_score(kb_hits, agent_min_sim)
        if sources.memory:
            try:
                mem_hits = list(
                    await rag_client.memory_rag_search(rag_query, strategy=rag_strategy)
                    or []
                )
            except RagReindexInProgress:
                await _notify_reindex_wait(sio, sid)
                await _abort_chat_reindex(
                    sio, sid, conversation_id, project_id, current_user
                )
                return
            except Exception:
                logger.exception("memory_rag search error")
            mem_hits = filter_rag_hits_by_score(mem_hits, get_memory_similarity_threshold())
        kb_id_name = build_rag_id_to_filename(kb_rows)
        mem_id_name = build_rag_id_to_filename(mem_rows)
        hits_out = document_search_trace["hits"] if document_search_trace else []
        files_used = set(document_search_trace["sourceFiles"]) if document_search_trace else set()
        # Собираем все новые хиты, затем одним проходом ставим Relevance 1..100.
        _pending_trace: list = []
        for hits_src, id_name, store_name in (
            (kb_hits, kb_id_name, "kb"),
            (mem_hits, mem_id_name, "memory"),
        ):
            for hit in hits_src:
                _, _, doc_id, _ = hit
                if doc_id is None:
                    continue
                try:
                    fn = id_name.get(int(doc_id))
                except (TypeError, ValueError):
                    fn = None
                if not fn:
                    fn = f"doc_{doc_id}"
                files_used.add(fn)
                _pending_trace.append((hit, fn, store_name))
        _pcts = hits_to_relevance_percents([row[0] for row in _pending_trace])
        for ((content, _score, doc_id, chunk_idx), fn, store), pct in zip(_pending_trace, _pcts):
            hits_out.append(
                {
                    "file": fn,
                    "anchor": f"chunk@{chunk_idx}({fn})",
                    "relevance": pct,
                    "content": (content or "")[:12000],
                    "chunkIndex": chunk_idx,
                    "documentId": doc_id,
                    "store": store,
                }
            )
        document_search_trace = {
            "query": rag_query,
            "strategy": rag_strategy,
            "sourceFiles": sorted(files_used),
            "hits": hits_out,
        }
    for hits_list, prefix, idnm, store_lbl, hit_scope in [
        (kb_hits, "База Знаний (постоянные документы)", kb_id_name, "kb (direct)", "agent"),
        (mem_hits, "Документы из настроек (библиотека памяти)", mem_id_name, "memory (direct)", "memory"),
    ]:
        if hits_list:
            parts, _m = format_rag_fragments(
                hits_list,
                idnm,
                max_chars=rag_context_max_chars(store_lbl),
                store_label=store_lbl,
                include_chunk_meta=False,
            )
            final_message = f"""{prefix}:
{''.join(parts)}

{final_message}"""
            context_added = True
            if hit_scope:
                rag_scopes.add(hit_scope)
    chat_timer.mark("rag_retrieve", time.perf_counter() - _t_rag0)
    log_pre_loop(
        phase="rag_retrieve_done",
        chat_id=conversation_id,
        agent_id=(
            (agent_profile or {}).get("agent_id")
            if isinstance(agent_profile, dict)
            else None
        ),
        detail=(
            f"контекст добавлен={'да' if context_added else 'нет'} "
            f"источники={sorted(rag_scopes) if rag_scopes else []} "
            f"заняло={int((time.perf_counter() - _t_rag0) * 1000)} мс "
            f"(поиск до навыков/LLM; не считается шагом лимита)"
        ),
    )
    # Инструментов в прямом режиме нет, поэтому плагин вызывает сам backend.
    # Решаем это до сборки промпта: файл, который уйдёт в сервис, дампом ячеек
    # в промпт не вставляем — вместо него будет вердикт плагина.
    direct_plugin_run = None
    direct_plugin_ids: list = []
    try:
        from backend.plugins.orchestrator_bridge import resolve_agent_plugin_ids
        from backend.services.plugins_direct import pick_plugin_run

        direct_plugin_ids = resolve_agent_plugin_ids(
            agent_profile if isinstance(agent_profile, dict) else None
        )
        logger.info(
            "[plugin-dispatch] mode=direct gate: plugin_ids=%s has_inline_attachments=%s "
            "inline_attachments_count=%s agent_name=%r",
            direct_plugin_ids,
            bool(isinstance(data, dict) and data.get("inline_attachments")),
            len(data.get("inline_attachments") or []) if isinstance(data, dict) else 0,
            (agent_profile or {}).get("name") if isinstance(agent_profile, dict) else None,
        )
        if direct_plugin_ids:
            direct_plugin_run = pick_plugin_run(
                direct_plugin_ids,
                data.get("inline_attachments") if isinstance(data, dict) else None,
                user_message,
                chat_mode="direct",
            )
            if not direct_plugin_run:
                logger.info(
                    "[plugin-dispatch] mode=direct: плагин к сервису НЕ отправляем "
                    "(см. SKIP выше)"
                )
        else:
            logger.info(
                "[plugin-dispatch] mode=direct SKIP: у агента нет активных plugin_ids"
            )
    except Exception:
        logger.exception("[direct] выбор плагина для вложения не удался")
    if inline_context and direct_plugin_run:
        from backend.services.inline_context import prepare_inline_context

        inline_context = prepare_inline_context(
            inline_context,
            plugin_ids=[direct_plugin_run.plugin_id],
            attachments=data.get("inline_attachments") if isinstance(data, dict) else None,
            label="direct",
            note_override=(
                f"[Файл «{direct_plugin_run.file_name}» целиком отправлен в сервис плагина "
                f"«{direct_plugin_run.label}»; его вердикт приведён в этом сообщении.]"
            ),
        )
    if inline_context:
        inline_block = f"""[Прикреплённый документ]
{inline_context}"""
        if final_message != user_message:
            final_message = f"{inline_block}\n\n{final_message}"
        else:
            final_message = f"""{inline_block}

[Вопрос пользователя]
{user_message}"""
        context_added = True
        logger.info(
            f"[direct inline_context] {len(inline_context)} символов, RAG-контекст {('совмещён' if final_message != inline_block else 'не применялся')}"
        )
    if (user_message or "").strip() != (rag_query or "").strip():
        if (user_message or "") not in (final_message or ""):
            final_message = f"{final_message}\n\n{user_message}"
    client_model_path = _client_model_path_from_payload(data)
    server_model_path = get_current_model_path()
    eff_model_path = (
        agent_profile.get("model_path") or client_model_path or server_model_path
    )
    if client_model_path and client_model_path != (server_model_path or ""):
        logger.info(
            "[chat] model_path из UI=%s (current на сервере=%s)",
            client_model_path,
            server_model_path or "(не задан)",
        )
    base_system_prompt = agent_profile["system_prompt"] or ""
    user_cpm = await get_user_prompt_manager((current_user or {}).get("user_id"))
    if user_cpm is None:
        user_cpm = context_prompt_manager
    if user_cpm:
        eff_system_prompt = user_cpm.resolve_chat_system_prompt(
            eff_model_path,
            agent_system_prompt=base_system_prompt,
            project_instructions=project_instructions,
        )
        if project_instructions and project_instructions.strip():
            logger.debug(f"[direct] project_instructions применены к system_prompt (project={project_id})")
    elif project_instructions and project_instructions.strip():
        if base_system_prompt:
            eff_system_prompt = f"{project_instructions.strip()}\n\n{base_system_prompt}"
        else:
            eff_system_prompt = project_instructions.strip()
        logger.debug(f"[direct] project_instructions применены к system_prompt (project={project_id})")
    else:
        eff_system_prompt = base_system_prompt or None
    feedback_block = await build_user_feedback_system_block(
        (current_user or {}).get("user_id"),
        conversation_id=conversation_id,
    )
    eff_system_prompt = merge_feedback_into_system_prompt(eff_system_prompt, feedback_block)
    if context_added:
        eff_system_prompt = merge_strict_rag_system_prompt(
            eff_system_prompt, rag_override=runtime_rag_system_prompt(rag_scopes) or None
        )
    try:
        from backend.services.skills import apply_skills_to_chat, strip_skill_mentions

        eff_system_prompt, _s, lazy_skill_ids, allowed_tools_extra, _primed = await apply_skills_to_chat(
            system_prompt=eff_system_prompt,
            user_message=rag_query or user_message,
            data=data or {},
            agent_profile=agent_profile if isinstance(agent_profile, dict) else {},
            current_user=current_user,
            history=history,
        )
        final_message = strip_skill_mentions(final_message)
        try:
            from backend.services.tag_mentions import strip_tag_mentions

            final_message = strip_tag_mentions(final_message)
        except Exception:
            pass
        if lazy_skill_ids or allowed_tools_extra:
            from backend.tools.tool_context import get_tool_context, set_tool_context

            ctx = get_tool_context() or {}
            if lazy_skill_ids:
                ctx["__skill_ids__"] = lazy_skill_ids
            ctx["current_user"] = current_user
            set_tool_context(ctx)
        if allowed_tools_extra:
            existing = list(data.get("tool_ids") or data.get("mcp_tool_ids") or [])
            data["tool_ids"] = list(dict.fromkeys([*existing, *allowed_tools_extra]))
        log_pre_loop(
            phase="skills",
            chat_id=conversation_id,
            agent_id=(
                (agent_profile or {}).get("agent_id")
                if isinstance(agent_profile, dict)
                else None
            ),
            detail=(
                f"отложенные={list(lazy_skill_ids or [])} "
                f"ручные={list((_primed or {}).get('manual') or [])} "
                f"всегда={list((_primed or {}).get('always_apply') or [])} "
                f"доп. инструменты={list(allowed_tools_extra or [])} "
                f"(инструменты из навыков попадут в цикл; сам навык ≠ шаг)"
            ),
        )
    except Exception:
        logger.exception("[direct] skills injection failed")
    try:
        from backend.prompts.artifacts import maybe_artifacts_prompt_for_agent
        from backend.services.skills import append_to_system_prompt

        artifacts_block = maybe_artifacts_prompt_for_agent(
            agent_profile if isinstance(agent_profile, dict) else None
        )
        if artifacts_block:
            eff_system_prompt = append_to_system_prompt(eff_system_prompt, artifacts_block)
        log_pre_loop(
            phase="artifacts",
            chat_id=conversation_id,
            agent_id=(
                (agent_profile or {}).get("agent_id")
                if isinstance(agent_profile, dict)
                else None
            ),
            detail=(
                f"включены={'да' if artifacts_block else 'нет'} "
                f"символов в промпте={len(artifacts_block or '')} "
                f"(только system prompt, не шаг лимита)"
            ),
        )
    except Exception:
        logger.exception("[direct] artifacts prompt injection failed")
    try:
        from backend.plugins.tools import build_plugins_system_append
        from backend.services.plugins_chat import apply_plugins_to_context
        from backend.services.skills import append_to_system_prompt as _append_plugins
        from backend.tools.tool_context import get_tool_context, set_tool_context

        plugins_append, plugin_ids, ctx = apply_plugins_to_context(
            agent_profile=agent_profile if isinstance(agent_profile, dict) else {},
            context=get_tool_context() or {},
        )
        if plugin_ids:
            ctx["current_user"] = current_user
            if isinstance(data, dict) and data.get("inline_attachments"):
                ctx["inline_attachments"] = data.get("inline_attachments")
            set_tool_context(ctx)
        if plugins_append:
            eff_system_prompt = _append_plugins(eff_system_prompt, plugins_append)
        log_pre_loop(
            phase="plugins_prompt",
            chat_id=conversation_id,
            agent_id=(
                (agent_profile or {}).get("agent_id")
                if isinstance(agent_profile, dict)
                else None
            ),
            detail=(
                f"плагины={list(plugin_ids or [])} "
                f"символов в промпте={len(plugins_append or '')} "
                f"(промпт/инструменты; прямой вызов — отдельно)"
            ),
        )
    except Exception:
        logger.exception("[direct] plugins injection failed")
    plugin_direct_artifact = ""
    plugin_direct_ran = False
    if direct_plugin_ids:
        try:
            from backend.services.plugins_direct import (
                prompt_block_for_outcome,
                run_plugin_direct,
                system_note_no_tools,
                system_note_prerun,
            )
            from backend.services.skills import append_to_system_prompt as _append_plugin_note

            plugin_note = (
                system_note_prerun(direct_plugin_run)
                if direct_plugin_run
                else system_note_no_tools(direct_plugin_ids)
            )
            if plugin_note:
                eff_system_prompt = _append_plugin_note(eff_system_prompt, plugin_note)
            if direct_plugin_run:
                await sio.emit(
                    "chat_thinking",
                    _stream_ids_payload(
                        {
                            "status": "processing",
                            "message": (
                                f"Плагин «{direct_plugin_run.label}»: файл "
                                f"«{direct_plugin_run.file_name}» отправлен в сервис. "
                                "Полный аудит модели занимает до нескольких десятков минут."
                            ),
                        }
                    ),
                    room=sid,
                )
                logger.info(
                    "[direct plugin] %s: запускаю для «%s» object=%s bucket=%s",
                    direct_plugin_run.plugin_id,
                    direct_plugin_run.file_name,
                    direct_plugin_run.minio_object,
                    direct_plugin_run.minio_bucket,
                )
                _t_plugin0 = time.perf_counter()
                outcome = await run_plugin_direct(direct_plugin_run, chat_mode="direct")
                chat_timer.mark("plugin_direct", time.perf_counter() - _t_plugin0)
                plugin_direct_ran = True
                log_pre_loop(
                    phase="plugin_direct",
                    chat_id=conversation_id,
                    agent_id=(
                        (agent_profile or {}).get("agent_id")
                        if isinstance(agent_profile, dict)
                        else None
                    ),
                    detail=(
                        f"плагин={direct_plugin_run.plugin_id} "
                        f"файл=«{direct_plugin_run.file_name}» "
                        f"успех={'да' if outcome.ok else 'нет'} "
                        f"заняло={int((time.perf_counter() - _t_plugin0) * 1000)} мс "
                        f"(вне цикла агента, не шаг лимита)"
                    ),
                )
                logger.info(
                    "[plugin-dispatch] mode=direct DONE ok=%s bytes=%s url=%s status=%s error=%r",
                    outcome.ok,
                    outcome.bytes_sent,
                    outcome.invoke_url or "?",
                    outcome.result_status or "?",
                    outcome.error or "",
                )
                final_message = (
                    f"{prompt_block_for_outcome(direct_plugin_run, outcome)}\n\n{final_message}"
                )
                if outcome.ok:
                    plugin_direct_artifact = outcome.artifact_markdown
                else:
                    await sio.emit(
                        "chat_info",
                        {
                            "message": (
                                f"Плагин «{direct_plugin_run.label}» не смог обработать файл: "
                                f"{outcome.error}"
                            )
                        },
                        room=sid,
                    )
        except Exception:
            logger.exception("[direct] вызов плагина не удался")
    canned = await maybe_rag_no_evidence_message(
        rag_client,
        block_when_no_evidence=rag_block,
        # Вердикт плагина — тоже контекст: иначе строгий RAG подменит его заглушкой.
        context_added=context_added or plugin_direct_ran,
        project_id=project_id,
        use_kb_rag=use_kb_rag,
        use_memory_library_rag=sources.memory,
        use_agent_scoped_kb=sources.agent_kb,
        agent_kb_doc_ids=agent_kb_doc_ids,
        # Есть переписка - пусть отвечает модель: ответ мог прозвучать выше
        has_history=bool(history),
    )
    _agent_steps_limit = resolve_recursion_limit(
        agent_profile if isinstance(agent_profile, dict) else None
    )
    _agent_steps_src = describe_limit_source(
        agent_profile if isinstance(agent_profile, dict) else None
    )
    log_pre_loop(
        phase="rag_before_agent_loop",
        chat_id=conversation_id,
        agent_id=(
            (agent_profile or {}).get("agent_id")
            if isinstance(agent_profile, dict)
            else None
        ),
        recursion_limit=_agent_steps_limit,
        limit_source=_agent_steps_src,
        detail=(
            f"контекст добавлен={'да' if context_added else 'нет'} "
            f"заглушка без LLM={'да' if canned else 'нет'} "
            f"источники RAG={sorted(rag_scopes) if rag_scopes else []} "
            f"(RAG не входит в лимит шагов)"
        ),
    )
    _terminal_chat_inference_banner(
        sid=sid,
        conversation_id=conversation_id,
        user_preview=final_message,
        mode_label="Прямой чат с LLM (одна модель)"
        + (" - параметры из выбранного агента" if agent_profile["model_path"] else ""),
        model_path_for_call=eff_model_path,
        extra_line="RAG/KB уже учтены в final_message при необходимости."
        + (" [RAG: ответ без LLM — нет релевантных фрагментов]" if canned else ""),
        enable_thinking=enable_thinking,
    )
    mcp_tool_events: list = []
    if not canned and current_user:
        # Обязательные субагенты по тегам карточки - кодом, до ответа родителя.
        # Возвращает сообщение с их ответами и профиль для цикла инструментов
        # (при required_only - без tool subagent). Без тегов - ничего не делает.
        try:
            from backend.agents.required_subagents import run_required_subagents
            from backend.services.tag_mentions import collect_mention_tag_ids

            async def _required_event_cb(payload):
                mcp_tool_events.append(dict(payload))
                if not suppress_ui_stream:
                    await sio.emit(
                        "chat_mcp_event", _stream_ids_payload(payload), room=sid
                    )

            mention_tags = collect_mention_tag_ids(
                user_message=str(
                    (data or {}).get("message") if isinstance(data, dict) else ""
                )
                or final_message,
                data=data if isinstance(data, dict) else None,
            )
            logger.info(
                "[chat-#tag] handlers: mention_tags=%s payload_tag_ids=%s "
                "raw_message_has_mention=%s parent_agent_id=%s",
                mention_tags,
                (data or {}).get("tag_ids") if isinstance(data, dict) else None,
                "<#"
                in str((data or {}).get("message") if isinstance(data, dict) else ""),
                (agent_profile or {}).get("agent_id")
                if isinstance(agent_profile, dict)
                else None,
            )
            (
                final_message,
                agent_profile,
                _required_called,
                mention_direct_answer,
            ) = await run_required_subagents(
                agent_profile=agent_profile,
                user_message=final_message,
                history=history,
                user=current_user,
                user_id=(current_user or {}).get("user_id"),
                enable_thinking=enable_thinking,
                emit_event=_required_event_cb,
                stopped=lambda: _generation_stopped(sid),
                inline_attachments=(
                    data.get("inline_attachments") if isinstance(data, dict) else None
                ),
                mention_tag_ids=mention_tags,
            )
            if mention_direct_answer:
                # #тег без выбранного агента → ответ вызванных агентов сразу в UI.
                canned = mention_direct_answer
                logger.info(
                    "[chat-#tag] handlers: используем прямой ответ агентов "
                    "(без родительского LLM), called=%s len=%s",
                    _required_called,
                    len(mention_direct_answer),
                )
            elif _required_called:
                logger.info(
                    "[chat-#tag] handlers: ответы агентов вложены в промпт родителя, "
                    "called=%s",
                    _required_called,
                )
        except Exception:
            logger.exception("[chat-#tag] предвызов не удался, отвечаю без него")
    tool_ids = resolve_chat_tool_ids(data.get("tool_ids") or data.get("mcp_tool_ids"))
    mcp_result = None
    _subagents_on = bool(
        agent_profile and (agent_profile.get("subagents") or {}).get("enabled")
    )
    coding_mode = bool(data.get("coding_mode"))
    plan_mode = bool(data.get("plan_mode"))
    workspace_path = str(data.get("workspace_path") or "").strip()
    coding_cfg = getattr(get_settings(), "coding_agent", None)
    auto_coding = bool(getattr(coding_cfg, "auto_enable_on_file_intent", False))
    if not coding_mode and not plan_mode and auto_coding:
        from backend.coding_agent.loop import message_wants_coding_agent

        if message_wants_coding_agent(user_message):
            if not workspace_path:
                from backend.coding_agent.workspace import resolve_workspace_path

                workspace_path = str(resolve_workspace_path(None) or "").strip()
            if workspace_path:
                coding_mode = True
                logger.info(
                    "Coding agent: auto-enabled (file intent) sid=%s workspace=%s",
                    sid,
                    workspace_path,
                )
    if coding_mode and not workspace_path:
        from backend.coding_agent.workspace import resolve_workspace_path

        workspace_path = str(resolve_workspace_path(None) or "").strip()
    approved_plan = str(data.get("approved_plan") or "").strip() or None
    coding_result = None
    coding_tool_events: list = []
    if not canned and coding_mode and current_user:
        try:
            from backend.coding_agent.chat_integration import run_coding_for_chat

            async def _coding_event_cb(payload):
                coding_tool_events.append(dict(payload))
                if not suppress_ui_stream:
                    await sio.emit("chat_coding_event", _stream_ids_payload(payload), room=sid)

            logger.info(
                "Coding agent: start sid=%s conversation_id=%s workspace_path=%s plan_mode=%s model_path=%s",
                sid,
                conversation_id,
                workspace_path,
                plan_mode,
                eff_model_path,
            )
            if not suppress_ui_stream:
                await sio.emit(
                    "chat_thinking",
                    _stream_ids_payload(
                        {
                            "status": "processing",
                            "message": "Coding agent: запуск (tools + workspace)…",
                            "coding_agent": True,
                        }
                    ),
                    room=sid,
                )
            coding_result = await run_coding_for_chat(
                user_message=user_message,
                history=history,
                system_prompt=eff_system_prompt,
                model_path=eff_model_path,
                workspace_path=workspace_path,
                plan_mode=plan_mode,
                approved_plan=approved_plan,
                temperature=agent_profile.get("temperature") or 0.7,
                max_tokens=max(agent_profile.get("max_tokens") or 1024, 4096),
                enable_thinking=enable_thinking,
                emit_event=_coding_event_cb,
                max_rounds=agent_profile.get("recursion_limit"),
            )
            logger.info(
                "Coding agent: done sid=%s mode=%s tool_calls_executed=%s iterations=%s plan_mode=%s",
                sid,
                getattr(coding_result, "mode", None),
                getattr(coding_result, "tool_calls_executed", None),
                getattr(coding_result, "iterations", None),
                plan_mode,
            )
        except Exception:
            logger.exception("Coding agent loop error")
    elif not canned and (tool_ids or _subagents_on) and current_user:
        try:
            from backend.mcp.chat_integration import run_mcp_for_chat

            async def _mcp_event_cb(payload):
                mcp_tool_events.append(dict(payload))
                if not suppress_ui_stream:
                    await sio.emit("chat_mcp_event", _stream_ids_payload(payload), room=sid)

            mcp_result = await run_mcp_for_chat(
                tool_ids=tool_ids,
                user_message=final_message,
                history=history,
                system_prompt=eff_system_prompt,
                model_path=eff_model_path,
                user=current_user,
                chat_id=conversation_id,
                message_id=data.get("message_id"),
                temperature=agent_profile.get("temperature") or 0.7,
                max_tokens=max(agent_profile.get("max_tokens") or 1024, 4096),
                enable_thinking=enable_thinking,
                emit_event=_mcp_event_cb,
                agent_profile=agent_profile,
                inline_attachments=(
                    data.get("inline_attachments") if isinstance(data, dict) else None
                ),
            )
            if mcp_result is None:
                log_pre_loop(
                    phase="mcp_skipped_no_tools",
                    chat_id=conversation_id,
                    agent_id=(
                        (agent_profile or {}).get("agent_id")
                        if isinstance(agent_profile, dict)
                        else None
                    ),
                    recursion_limit=_agent_steps_limit,
                    limit_source=_agent_steps_src,
                    detail=(
                        f"tool_ids={tool_ids} субагенты={'да' if _subagents_on else 'нет'} "
                        f"→ дальше обычное обращение к LLM"
                    ),
                )
        except Exception:
            logger.exception("MCP agent loop error")
    elif not canned:
        log_pre_loop(
            phase="skip_agent_loop",
            chat_id=conversation_id,
            agent_id=(
                (agent_profile or {}).get("agent_id")
                if isinstance(agent_profile, dict)
                else None
            ),
            recursion_limit=_agent_steps_limit,
            limit_source=_agent_steps_src,
            detail=(
                f"нет MCP-инструментов и субагентов "
                f"(tool_ids={tool_ids}, субагенты={'да' if _subagents_on else 'нет'}) → один вызов LLM"
            ),
        )
    reasoning_trace_accumulated = ""

    def _direct_stream_cb(chunk, acc, stream_role="content"):
        nonlocal reasoning_trace_accumulated
        if stream_role == "reasoning":
            if isinstance(acc, str) and acc:
                reasoning_trace_accumulated = acc
            elif isinstance(chunk, str) and chunk:
                reasoning_trace_accumulated += chunk
            if suppress_ui_stream:
                return True
            return sync_stream_cb(chunk, acc, stream_role)
        if suppress_ui_stream:
            return True
        visible_acc = f"{stream_prefix}{acc}" if stream_prefix else acc
        return sync_stream_cb(chunk, visible_acc, stream_role)

    def _run_ask(stream, cb):
        return ask_agent(
            final_message,
            history=history,
            max_tokens=agent_profile["max_tokens"],
            streaming=stream,
            stream_callback=cb,
            model_path=eff_model_path,
            custom_prompt_id=None,
            images=images,
            system_prompt=eff_system_prompt,
            temperature=agent_profile["temperature"],
            enable_thinking=enable_thinking,
        )

    _t_llm0 = time.perf_counter()
    if canned:
        log_pre_loop(
            phase="canned_skip_llm",
            chat_id=conversation_id,
            agent_id=(
                (agent_profile or {}).get("agent_id")
                if isinstance(agent_profile, dict)
                else None
            ),
            recursion_limit=_agent_steps_limit,
            limit_source=_agent_steps_src,
            detail="ответ без LLM: нет релевантных фрагментов RAG",
        )
        response = canned
        if streaming and not suppress_ui_stream:
            vis = f"{stream_prefix}{canned}" if stream_prefix else canned
            await sio.emit("chat_chunk", _stream_ids_payload({"chunk": canned, "accumulated": vis}), room=sid)
    elif coding_result is not None:
        response = coding_result.content
        if streaming and response and not suppress_ui_stream:
            vis = f"{stream_prefix}{response}" if stream_prefix else response
            await sio.emit("chat_chunk", _stream_ids_payload({"chunk": response, "accumulated": vis}), room=sid)
        logger.info(
            "Coding agent loop: mode=%s tools=%s iterations=%s plan=%s",
            coding_result.mode,
            coding_result.tool_calls_executed,
            coding_result.iterations,
            coding_result.plan_mode,
        )
    elif mcp_result is not None:
        response = mcp_result.content
        if streaming and response and not suppress_ui_stream:
            vis = f"{stream_prefix}{response}" if stream_prefix else response
            await sio.emit("chat_chunk", _stream_ids_payload({"chunk": response, "accumulated": vis}), room=sid)
        logger.info(
            "[шаги агента] Итог цикла | режим=%s | ИТОГО шагов: %s из %s (осталось %s) | "
            "вызовов инструментов=%s | чат=%s",
            mcp_result.mode,
            mcp_result.iterations,
            _agent_steps_limit,
            max(0, int(_agent_steps_limit) - int(mcp_result.iterations or 0)),
            mcp_result.tool_calls_executed,
            conversation_id,
        )
        logger.debug(
            "MCP agent loop: mode=%s tools=%s iterations=%s",
            mcp_result.mode,
            mcp_result.tool_calls_executed,
            mcp_result.iterations,
        )
    elif streaming:
        log_plain_llm(
            phase="start",
            chat_id=conversation_id,
            agent_id=(
                (agent_profile or {}).get("agent_id")
                if isinstance(agent_profile, dict)
                else None
            ),
            model=str(eff_model_path or ""),
            recursion_limit=_agent_steps_limit,
            detail="стриминг включён",
        )
        with concurrent.futures.ThreadPoolExecutor() as ex:
            response = await asyncio.get_event_loop().run_in_executor(
                ex, _make_ctx_runner(lambda: _run_ask(True, _direct_stream_cb))
            )
        log_plain_llm(
            phase="done",
            chat_id=conversation_id,
            agent_id=(
                (agent_profile or {}).get("agent_id")
                if isinstance(agent_profile, dict)
                else None
            ),
            model=str(eff_model_path or ""),
            recursion_limit=_agent_steps_limit,
            duration_ms=int((time.perf_counter() - _t_llm0) * 1000),
            content_preview=response if isinstance(response, str) else "",
            detail="стриминг включён",
        )
        if response is None or _generation_stopped(sid):
            await sio.emit("generation_stopped", _stream_ids_payload({"message": "Генерация остановлена"}), room=sid)
            chat_timer.mark("llm", time.perf_counter() - _t_llm0)
            chat_timer.log(logger)
            return
    else:
        log_plain_llm(
            phase="start",
            chat_id=conversation_id,
            agent_id=(
                (agent_profile or {}).get("agent_id")
                if isinstance(agent_profile, dict)
                else None
            ),
            model=str(eff_model_path or ""),
            recursion_limit=_agent_steps_limit,
            detail="стриминг выключен",
        )
        with concurrent.futures.ThreadPoolExecutor() as ex:
            response = await asyncio.get_event_loop().run_in_executor(
                ex, _make_ctx_runner(lambda: _run_ask(False, None))
            )
        log_plain_llm(
            phase="done",
            chat_id=conversation_id,
            agent_id=(
                (agent_profile or {}).get("agent_id")
                if isinstance(agent_profile, dict)
                else None
            ),
            model=str(eff_model_path or ""),
            recursion_limit=_agent_steps_limit,
            duration_ms=int((time.perf_counter() - _t_llm0) * 1000),
            content_preview=response if isinstance(response, str) else "",
            detail="стриминг выключен",
        )
    chat_timer.mark("llm", time.perf_counter() - _t_llm0)
    if context_added and (not canned) and response:
        response = await maybe_replace_ungrounded(final_message[:20000], response, RAG_STRICT_NOT_FOUND_MESSAGE)
    if plugin_direct_artifact:
        from backend.plugins.artifact_format import append_artifacts_to_answer

        response = append_artifacts_to_answer(response, plugin_direct_artifact)
    # После всех веток (цикл, стрим, обычный вызов): артефакты и файлы детей,
    # которые родитель пересказал вместо того, чтобы воспроизвести.
    if mcp_tool_events and isinstance(response, str):
        response = _carry_subagent_artifacts(response, mcp_tool_events)
    if _generation_stopped(sid):
        await sio.emit("generation_stopped", _stream_ids_payload({"message": "Генерация остановлена"}), room=sid)
        return None
    visible_out = complete_response if complete_response is not None else (
        f"{stream_prefix}{response}" if stream_prefix else response
    )
    # Сначала отдаём ответ в UI: зависание Mongo на regenerate не должно
    # блокировать стрим и кнопку «Стоп».
    payload = _stream_ids_payload(
        {
            "response": visible_out,
            "timestamp": datetime.now().isoformat(),
            "was_streaming": streaming,
            "generation_duration_sec": _generation_duration_sec(generation_started_at),
        }
    )
    if extra_save_meta and extra_save_meta.get("chain_steps"):
        payload["chain_steps"] = extra_save_meta.get("chain_steps")
        payload["hide_sequential_outputs"] = bool(extra_save_meta.get("hide_sequential_outputs"))
    if document_search_trace:
        payload["document_search"] = document_search_trace
    if mcp_tool_events:
        payload["mcp_tool_calls"] = mcp_tool_events
    if coding_tool_events:
        payload["coding_tool_calls"] = coding_tool_events
        payload["mcp_tool_calls"] = list(mcp_tool_events) + [
            e for e in coding_tool_events if e.get("type") in ("mcp_tool_start", "mcp_tool_end")
        ]
    if coding_result is not None:
        if getattr(coding_result, "pending_ask_user", None):
            payload["ask_user"] = coding_result.pending_ask_user
        if getattr(coding_result, "active_plan", None):
            payload["active_plan"] = coding_result.active_plan
    chat_timer.meta["context_added"] = context_added
    chat_timer.log(logger)
    if emit_complete:
        await sio.emit("chat_complete", payload, room=sid)
    rag_metrics = await _compute_and_emit_rag_metrics(
        sio,
        sid,
        query=rag_query,
        document_search_trace=document_search_trace,
        context_text=final_message,
        answer=response if isinstance(response, str) else "",
        context_added=context_added,
    )
    if save_response:
        try:
            meta = {"document_search": document_search_trace} if document_search_trace else None
            if rag_metrics:
                meta = dict(meta or {})
                meta["rag_metrics"] = rag_metrics
            if reasoning_trace_accumulated.strip():
                meta = dict(meta or {})
                meta["reasoning_content"] = reasoning_trace_accumulated.strip()
            if mcp_tool_events:
                meta = dict(meta or {})
                meta["mcp_tool_calls"] = mcp_tool_events
            if coding_tool_events:
                meta = dict(meta or {})
                meta["coding_tool_calls"] = coding_tool_events
            if mcp_result and getattr(mcp_result, "attachments", None):
                meta = dict(meta or {})
                meta["mcp_attachments"] = mcp_result.attachments
            if extra_save_meta:
                meta = dict(meta or {})
                meta.update(extra_save_meta)
            meta = _with_generation_duration(meta, generation_started_at)
            regen = _regen_save_kwargs(data)
            await save_assistant_response(
                visible_out,
                meta,
                conversation_id=conversation_id,
                user_id=(current_user or {}).get("user_id"),
                project_id=project_id,
                **regen,
            )
        except Exception as e:
            logger.warning(f"Не удалось сохранить ответ: {e}")
    return {
        "content": response if isinstance(response, str) else (str(response) if response is not None else ""),
        "reasoning": (reasoning_trace_accumulated or "").strip(),
        "document_search": document_search_trace,
    }
