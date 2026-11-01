"""
Сервис для работы с памятью диалогов через MongoDB
Файловый режим отключен - используется только MongoDB
"""

import contextvars
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from backend.settings.logging import get_logger

logger = get_logger(__name__)


def _strip_reasoning_from_history_content(text: str) -> str:
    """Удаляет reasoning-блоки из исторического контента перед подачей в LLM-контекст."""
    if not text:
        return text
    cleaned = re.sub("<think>[\\s\\S]*?</think>", "", text, flags=re.IGNORECASE)
    cleaned = re.sub("<think>[\\s\\S]*$", "", cleaned, flags=re.IGNORECASE)
    if "</think>".lower() in cleaned.lower():
        cleaned = re.sub(r"^[\s\S]*?</think>", "", cleaned, count=1, flags=re.IGNORECASE)
    return cleaned.strip()


def _is_event_loop_closed(error: BaseException) -> bool:
    """Ошибка «Event loop is closed» (клиент Mongo создан в другом event loop)."""
    return "Event loop is closed" in str(error)


def _history_content_with_inline_attach(
    role: str, content: str, metadata: Optional[Dict[str, Any]]
) -> str:
    """Для LLM: к user-сообщению из истории подмешиваем сохранённый inline_context.

    В Mongo content хранит только вопрос (для UI), текст файла — в metadata.inline_context.
    Без этого follow-up «по тому же файлу» не видит вложение.
    """
    cleaned = _strip_reasoning_from_history_content(content)
    if (role or "").strip().lower() != "user":
        return cleaned
    meta = metadata if isinstance(metadata, dict) else {}
    ctx = str(meta.get("inline_context") or "").strip()
    if not ctx:
        return cleaned
    if "[Прикреплённый документ]" in (cleaned or ""):
        return cleaned
    question = (cleaned or "").strip()
    if question:
        return f"[Прикреплённый документ]\n{ctx}\n\n[Вопрос пользователя]\n{question}"
    return f"[Прикреплённый документ]\n{ctx}"


def _message_to_history_entry(message: Any) -> Dict[str, Any]:
    meta = getattr(message, "metadata", None)
    if not isinstance(meta, dict):
        meta = {}
    from backend.services.inline_images import image_refs_from_metadata

    entry: Dict[str, Any] = {
        "role": message.role,
        "content": _history_content_with_inline_attach(
            message.role, message.content, meta
        ),
        "timestamp": (
            message.timestamp.isoformat()
            if getattr(message, "timestamp", None)
            else None
        ),
    }
    refs = image_refs_from_metadata(meta)
    if refs:
        entry["image_refs"] = refs
    return entry


mongodb_available = False
conversation_repo = None
get_conversation_repository = None
Conversation = None
Message = None
try:
    from backend.database.init_db import get_conversation_repository
    from backend.database.mongodb.models import Conversation, Message

    logger.info("MongoDB модуль импортирован для работы с памятью")
    logger.debug(
        f"get_conversation_repository импортирован: {get_conversation_repository is not None}"
    )
except ImportError:
    logger.exception("MongoDB недоступен")
    logger.exception("Приложение не сможет сохранять диалоги без MongoDB!")
    get_conversation_repository = None
    Conversation = None
    Message = None


def _check_mongodb_available() -> bool:
    """Проверка реальной доступности MongoDB (не только импорт модулей)"""
    global conversation_repo, mongodb_available
    if get_conversation_repository is None:
        logger.warning(
            "get_conversation_repository is None - MongoDB модули не импортированы"
        )
        logger.warning(
            "  Убедитесь, что motor и pymongo установлены в виртуальном окружении"
        )
        mongodb_available = False
        return False
    try:
        conversation_repo = get_conversation_repository()
        mongodb_available = True
        logger.debug("MongoDB доступен - репозиторий получен успешно")
        return True
    except RuntimeError as e:
        mongodb_available = False
        logger.warning(f"MongoDB не инициализирован: {e}")
        logger.warning(
            "  Убедитесь, что init_mongodb() был вызван при старте приложения"
        )
        return False
    except Exception:
        mongodb_available = False
        logger.exception("Ошибка при проверке доступности MongoDB")
        import traceback

        logger.exception(f"Traceback: {traceback.format_exc()}")
        return False


# ВАЖНО (изоляция пользователей): раньше "текущий диалог" хранился в
# module-global `current_conversation_id`, общий на весь процесс. При
# конкурентных запросах разных пользователей значение перезаписывалось, и любой
# код, читавший/сохранявший историю без явного conversation_id, получал ЧУЖОЙ
# диалог — пользователи видели сообщения и инструкции друг друга.
#
# Теперь это ContextVar: значение изолировано в рамках одной asyncio-задачи
# (один HTTP-запрос / одно событие Socket.IO / одно WS-соединение). Утечки между
# параллельными пользователями больше невозможны через этот механизм.
_current_conversation_id: contextvars.ContextVar[Optional[str]] = (
    contextvars.ContextVar("current_conversation_id", default=None)
)


def set_current_conversation_id(conversation_id: Optional[str]) -> None:
    """Устанавливает ID текущего диалога ТОЛЬКО в контексте текущей задачи."""
    _current_conversation_id.set(conversation_id or None)


def get_or_create_conversation_id() -> str:
    """Получение или создание ID текущего диалога (в пределах текущей задачи)."""
    cid = _current_conversation_id.get()
    if not cid:
        cid = f"conv_{uuid.uuid4().hex[:12]}"
        _current_conversation_id.set(cid)
    return cid


def reset_conversation():
    """Сброс текущего диалога (начало нового) в контексте текущей задачи."""
    _current_conversation_id.set(None)


def __getattr__(name: str):
    """Совместимость для legacy-обращений к `memory_service.current_conversation_id`.

    Возвращает значение из ContextVar, чтобы старый код не читал/писал общий на
    процесс атрибут. Прямое присваивание модулю не должно использоваться —
    вместо него есть set_current_conversation_id().
    """
    if name == "current_conversation_id":
        return _current_conversation_id.get()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


async def verify_conversation_owner(
    conversation_id: Optional[str], user_id: Optional[str]
) -> bool:
    """Проверяет, что диалог можно читать/писать текущему пользователю.

    Возвращает True, если:
      - conversation_id/user_id не заданы (нечего проверять);
      - диалога ещё нет (новый диалог создаётся при первом сообщении);
      - диалог "ничей"/legacy (owner пустой или "default_user");
      - владелец диалога совпадает с user_id.

    Возвращает False только когда диалог принадлежит ДРУГОМУ пользователю —
    это защита от чтения/записи чужой истории по угаданному conversation_id.
    При недоступности БД делаем fail-open (True), чтобы не ронять чат.
    """
    if not conversation_id or not user_id:
        return True
    try:
        global conversation_repo
        if not _check_mongodb_available():
            return True
        if conversation_repo is None:
            conversation_repo = get_conversation_repository()
        conv = await conversation_repo.get_conversation(conversation_id)
        if conv is None:
            return True
        owner = getattr(conv, "user_id", None)
        if owner in (None, "", "default_user"):
            return True
        return owner == user_id
    except Exception:
        logger.exception("Ошибка проверки владельца диалога %s", conversation_id)
        return True


async def save_dialog_entry_mongodb(
    role: str,
    content: str,
    metadata: Optional[Dict[str, Any]] = None,
    message_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> bool:
    """
    Сохранение сообщения в MongoDB

    Args:
        role: Роль отправителя (user, assistant, system)
        content: Содержание сообщения
        metadata: Дополнительные метаданные
        message_id: ID сообщения (если не указан, генерируется автоматически)
        conversation_id: ID диалога (если не указан, используется текущий или создается новый)

    Returns:
        True если успешно, False в случае ошибки
    """
    try:
        global conversation_repo
        if not _check_mongodb_available():
            logger.error("MongoDB не инициализирован. Не удалось сохранить сообщение.")
            return False
        if conversation_repo is None:
            conversation_repo = get_conversation_repository()
        if conversation_id is None:
            conversation_id = get_or_create_conversation_id()
        if message_id is None:
            message_id = f"msg_{uuid.uuid4().hex[:12]}"
        message = Message(
            message_id=message_id,
            role=role,
            content=content,
            timestamp=datetime.utcnow(),
            metadata=metadata or {},
        )
        existing_conversation = await conversation_repo.get_conversation(
            conversation_id
        )
        promote_branch_draft = False
        if (
            existing_conversation
            and role == "user"
            and isinstance(existing_conversation.metadata, dict)
            and existing_conversation.metadata.get(
                "hidden_from_sidebar_until_user_message"
            )
        ):
            seeded = existing_conversation.metadata.get("branch_seeded_message_count")
            if (
                isinstance(seeded, int)
                and len(existing_conversation.messages or []) == seeded
            ):
                promote_branch_draft = True
        if existing_conversation is None:
            conversation = Conversation(
                conversation_id=conversation_id,
                user_id=user_id or "default_user",
                title=content[:60],
                messages=[message],
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            await conversation_repo.create_conversation(conversation)
            logger.debug(f"Создан новый диалог: {conversation_id}")
        else:
            await conversation_repo.add_message(conversation_id, message)
            logger.debug(f"Добавлено сообщение в диалог: {conversation_id}")
            if promote_branch_draft:
                new_meta = dict(existing_conversation.metadata or {})
                new_meta.pop("hidden_from_sidebar_until_user_message", None)
                await conversation_repo.update_conversation(
                    conversation_id, {"metadata": new_meta}
                )
        return True
    except RuntimeError as e:
        if _is_event_loop_closed(e):
            raise  # повтор с переинициализацией делает save_dialog_entry
        logger.exception("MongoDB не инициализирован")
        return False
    except Exception as e:
        if _is_event_loop_closed(e):
            raise
        logger.exception("Ошибка при сохранении сообщения в MongoDB")
        return False


async def save_dialog_entry(
    role: str,
    content: str,
    metadata: Optional[Dict[str, Any]] = None,
    message_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    user_id: Optional[str] = None,
):
    """
    Сохранение сообщения в MongoDB (файловый режим отключен)
    При "Event loop is closed" переинициализирует MongoDB в текущем loop и повторяет попытку.
    """
    if not _check_mongodb_available():
        logger.error("MongoDB недоступен! Сообщение не будет сохранено.")
        msg = "MongoDB недоступен. Невозможно сохранить сообщение."
        raise RuntimeError(msg)
    if not await verify_conversation_owner(conversation_id, user_id):
        msg = "Диалог принадлежит другому пользователю"
        raise PermissionError(msg)
    for attempt in range(2):
        try:
            success = await save_dialog_entry_mongodb(
                role, content, metadata, message_id, conversation_id, user_id
            )
            if not success:
                msg = "Не удалось сохранить сообщение в MongoDB"
                raise RuntimeError(msg)
            return
        except Exception as e:
            if isinstance(e, RuntimeError) and not _is_event_loop_closed(e):
                raise
            if attempt == 0 and _is_event_loop_closed(e):
                logger.warning(
                    "MongoDB создан в другом event loop, переинициализируем в текущем..."
                )
                try:
                    global conversation_repo
                    from backend.database.init_db import (
                        get_conversation_repository,
                        init_mongodb,
                        reset_mongodb_globals,
                    )

                    reset_mongodb_globals()
                    await init_mongodb()
                    conversation_repo = get_conversation_repository()
                except Exception:
                    logger.exception("Не удалось переинициализировать MongoDB")
                continue
            logger.exception("Ошибка при сохранении сообщения")
            msg = f"Ошибка при сохранении сообщения: {e}"
            raise RuntimeError(msg) from e


async def load_dialog_history_mongodb(
    conversation_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Загрузка истории диалога из MongoDB

    Args:
        conversation_id: ID диалога (если None, используется текущий)

    Returns:
        Список сообщений в формате словарей
    """
    try:
        global conversation_repo
        if not _check_mongodb_available():
            logger.warning("MongoDB не инициализирован. Не удалось загрузить историю.")
            return []
        if conversation_repo is None:
            conversation_repo = get_conversation_repository()
        if conversation_id is None:
            conversation_id = get_or_create_conversation_id()
        conversation = await conversation_repo.get_conversation(conversation_id)
        if conversation is None:
            return []
        history = []
        for message in conversation.messages:
            history.append(_message_to_history_entry(message))
        return history
    except RuntimeError as e:
        if _is_event_loop_closed(e):
            raise  # повтор с переинициализацией делает get_recent_dialog_history
        logger.warning(f"MongoDB не инициализирован: {e}")
        return []
    except Exception as e:
        if _is_event_loop_closed(e):
            raise
        logger.exception("Ошибка при загрузке истории из MongoDB")
        return []


def _coerce_alternative_responses(
    raw: Any,
    *,
    fallback_content: str,
    new_content: str,
    current_index: Optional[int],
) -> tuple:
    """Собирает массив вариантов ответа и индекс текущего для metadata."""
    new = str(new_content or "")
    if isinstance(raw, list) and raw:
        alts = [str(v if v is not None else "") for v in raw]
        idx = (
            int(current_index)
            if isinstance(current_index, int) and current_index >= 0
            else max(len(alts) - 1, 0)
        )
        while len(alts) <= idx:
            alts.append("")
        alts[idx] = new
        return alts, idx
    base = str(fallback_content or "")
    if base and base != new:
        return [base, new], 1
    return [new], 0


async def save_assistant_response(
    content: str,
    metadata: Optional[Dict[str, Any]] = None,
    *,
    conversation_id: Optional[str] = None,
    user_id: Optional[str] = None,
    project_id: Optional[str] = None,
    message_id: Optional[str] = None,
    regenerate: bool = False,
    assistant_message_id: Optional[str] = None,
    alternative_responses: Optional[list] = None,
    current_response_index: Optional[int] = None,
) -> Optional[str]:
    """Сохраняет ответ ассистента.

    При regenerate=True обновляет существующее сообщение (не создаёт новое),
    записывая alternative_responses / current_response_index в metadata —
    иначе после F5 варианты «разъезжаются» в отдельные сообщения.
    """
    meta = dict(metadata or {})
    target_id = (assistant_message_id or "").strip() or None

    if regenerate and conversation_id:
        if not _check_mongodb_available():
            raise RuntimeError("MongoDB недоступен. Невозможно обновить сообщение.")
        global conversation_repo
        if conversation_repo is None:
            conversation_repo = get_conversation_repository()
        conv = await conversation_repo.get_conversation(conversation_id)
        if not conv:
            logger.warning("Диалог %s не найден для regenerate", conversation_id)
        else:
            existing = None
            if target_id:
                for msg in conv.messages or []:
                    if (
                        getattr(msg, "message_id", None) == target_id
                        and getattr(msg, "role", None) == "assistant"
                    ):
                        existing = msg
                        break
            if existing is None:
                # Фронт мог держать локальный id до синхронизации с Mongo — берём последний assistant.
                for msg in reversed(conv.messages or []):
                    if getattr(msg, "role", None) == "assistant":
                        existing = msg
                        break
            if existing is not None:
                existing_meta = dict(getattr(existing, "metadata", None) or {})
                prev_alts = (
                    alternative_responses
                    if isinstance(alternative_responses, list) and alternative_responses
                    else existing_meta.get("alternative_responses")
                    or existing_meta.get("alternativeResponses")
                )
                alts, idx = _coerce_alternative_responses(
                    prev_alts,
                    fallback_content=str(getattr(existing, "content", "") or ""),
                    new_content=content,
                    current_index=current_response_index,
                )
                merged = {**existing_meta, **meta}
                merged["alternative_responses"] = alts
                merged["current_response_index"] = idx
                ok = await conversation_repo.update_assistant_message(
                    conversation_id,
                    existing.message_id,
                    content=content,
                    metadata=merged,
                )
                if ok:
                    return str(existing.message_id)
                logger.warning(
                    "Не удалось обновить assistant %s при regenerate — сохраняем как новое",
                    getattr(existing, "message_id", None),
                )

        alts, idx = _coerce_alternative_responses(
            alternative_responses,
            fallback_content="",
            new_content=content,
            current_index=current_response_index,
        )
        meta["alternative_responses"] = alts
        meta["current_response_index"] = idx

    effective_id = message_id or target_id
    if project_id:
        ok = await save_dialog_entry_to_project(
            "assistant",
            content,
            project_id,
            conversation_id,
            effective_id,
            metadata=meta or None,
            user_id=user_id,
        )
        return effective_id if ok else None

    await save_dialog_entry(
        "assistant",
        content,
        meta or None,
        effective_id,
        conversation_id,
        user_id=user_id,
    )
    return effective_id


async def load_dialog_history() -> List[Dict[str, Any]]:
    """
    Загрузка истории диалога из MongoDB (файловый режим отключен)
    """
    if not _check_mongodb_available():
        logger.warning("MongoDB недоступен! Возвращаем пустую историю.")
        return []
    try:
        return await load_dialog_history_mongodb()
    except Exception:
        logger.exception("Ошибка при загрузке истории")
        return []


async def get_recent_dialog_history_mongodb(
    max_entries: Optional[int] = None, conversation_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Получение последних N сообщений из MongoDB

    Args:
        max_entries: Максимальное количество сообщений. Если None, возвращает всю историю (неограниченная память)
        conversation_id: ID диалога (если None, используется текущий)

    Returns:
        Список последних сообщений
    """
    try:
        history = await load_dialog_history_mongodb(conversation_id)
        if max_entries is None:
            return history
        return history[-max_entries:] if len(history) > max_entries else history
    except Exception as e:
        if _is_event_loop_closed(e):
            raise
        logger.exception("Ошибка при получении последних сообщений из MongoDB")
        return []


async def get_recent_dialog_history(
    max_entries: Optional[int] = None, conversation_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Получение последних сообщений из MongoDB (файловый режим отключен)

    Args:
        max_entries: Максимальное количество сообщений
        conversation_id: ID диалога (если None, используется текущий)
    """
    if not _check_mongodb_available():
        logger.warning("MongoDB недоступен! Возвращаем пустую историю.")
        return []
    if conversation_id is None:
        conversation_id = get_or_create_conversation_id()
    for attempt in range(2):
        try:
            return await get_recent_dialog_history_mongodb(max_entries, conversation_id)
        except Exception as e:
            if attempt == 0 and _is_event_loop_closed(e):
                logger.warning(
                    "MongoDB создан в другом event loop, переинициализируем в текущем..."
                )
                try:
                    global conversation_repo
                    from backend.database.init_db import (
                        get_conversation_repository,
                        init_mongodb,
                        reset_mongodb_globals,
                    )

                    reset_mongodb_globals()
                    await init_mongodb()
                    conversation_repo = get_conversation_repository()
                except Exception:
                    logger.exception("Не удалось переинициализировать MongoDB")
                continue
            logger.exception("Ошибка при получении последних сообщений")
            return []
    return []


async def clear_dialog_history_mongodb() -> str:
    """Очистка истории диалога в MongoDB"""
    try:
        global conversation_repo
        if not _check_mongodb_available():
            logger.error("MongoDB не инициализирован. Не удалось очистить историю.")
            return "Ошибка: MongoDB не инициализирован"
        if conversation_repo is None:
            conversation_repo = get_conversation_repository()
        conversation_id = get_or_create_conversation_id()
        await conversation_repo.delete_conversation(conversation_id)
        reset_conversation()
        logger.info(f"История диалога {conversation_id} очищена")
        return "История диалога очищена"
    except RuntimeError:
        logger.exception("MongoDB не инициализирован")
        return "Ошибка: MongoDB не инициализирован"
    except Exception as e:
        logger.exception("Ошибка при очистке истории в MongoDB")
        return f"Ошибка при очистке истории: {str(e)}"


async def clear_dialog_history() -> str:
    """
    Очистка истории диалога в MongoDB (файловый режим отключен)
    """
    if not _check_mongodb_available():
        logger.error("MongoDB недоступен! Невозможно очистить историю.")
        return "Ошибка: MongoDB недоступен"
    try:
        return await clear_dialog_history_mongodb()
    except Exception as e:
        logger.exception("Ошибка при очистке истории")
        return f"Ошибка при очистке: {str(e)}"


async def search_conversations(
    query: str, user_id: Optional[str] = None, limit: int = 50
) -> List[Dict[str, Any]]:
    """
    Поиск диалогов по тексту (только MongoDB)

    Args:
        query: Поисковый запрос
        user_id: Опциональный фильтр по пользователю
        limit: Максимальное количество результатов

    Returns:
        Список найденных диалогов
    """
    if not _check_mongodb_available():
        logger.warning("Поиск доступен только с MongoDB")
        return []
    try:
        global conversation_repo
        if conversation_repo is None:
            conversation_repo = get_conversation_repository()
        conversations = await conversation_repo.search_conversations(
            query, user_id, limit
        )
        results = []
        for conv in conversations:
            results.append(
                {
                    "conversation_id": conv.conversation_id,
                    "title": conv.title,
                    "created_at": (
                        conv.created_at.isoformat() if conv.created_at else None
                    ),
                    "updated_at": (
                        conv.updated_at.isoformat() if conv.updated_at else None
                    ),
                    "message_count": len(conv.messages),
                }
            )
        return results
    except RuntimeError as e:
        logger.warning(f"MongoDB не инициализирован: {e}")
        return []
    except Exception:
        logger.exception("Ошибка при поиске диалогов")
        return []


async def remove_last_user_message(conversation_id: Optional[str] = None) -> bool:
    """
    Удаление последнего сообщения пользователя из диалога
    Используется при остановке генерации в обычном (не streaming) режиме

    Args:
        conversation_id: ID диалога (если None, используется текущий)

    Returns:
        True если успешно, False в случае ошибки
    """
    if not _check_mongodb_available():
        logger.warning("MongoDB недоступен! Невозможно удалить сообщение.")
        return False
    try:
        global conversation_repo
        if conversation_repo is None:
            conversation_repo = get_conversation_repository()
        if conversation_id is None:
            conversation_id = get_or_create_conversation_id()
        success = await conversation_repo.remove_last_message(
            conversation_id, role="user"
        )
        if success:
            logger.info(
                f"Последнее сообщение пользователя удалено из диалога {conversation_id}"
            )
        return success
    except RuntimeError as e:
        logger.warning(f"MongoDB не инициализирован: {e}")
        return False
    except Exception:
        logger.exception("Ошибка при удалении последнего сообщения пользователя")
        return False


async def save_dialog_entry_to_project(
    role: str,
    content: str,
    project_id: str,
    conversation_id: Optional[str] = None,
    message_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
) -> bool:
    """
    Сохраняет сообщение в MongoDB с привязкой к проекту.
    Используется чатами внутри проекта.
    """
    if not _check_mongodb_available():
        logger.error("MongoDB недоступен. Сообщение проекта не сохранено.")
        return False
    if Conversation is None or Message is None:
        logger.error("MongoDB модели недоступны (Conversation/Message is None)")
        return False
    try:
        global conversation_repo
        if conversation_repo is None:
            conversation_repo = get_conversation_repository()
        if conversation_id is None:
            conversation_id = get_or_create_conversation_id()
        if message_id is None:
            message_id = f"msg_{uuid.uuid4().hex[:12]}"
        message = Message(
            message_id=message_id,
            role=role,
            content=content,
            timestamp=datetime.utcnow(),
            metadata=metadata or {},
        )
        existing = await conversation_repo.get_conversation(conversation_id)
        promote_branch_draft = False
        if (
            existing
            and role == "user"
            and isinstance(existing.metadata, dict)
            and existing.metadata.get("hidden_from_sidebar_until_user_message")
        ):
            seeded = existing.metadata.get("branch_seeded_message_count")
            if isinstance(seeded, int) and len(existing.messages or []) == seeded:
                promote_branch_draft = True
        if existing is None:
            conversation = Conversation(
                conversation_id=conversation_id,
                user_id=user_id or "default_user",
                title=content[:60],
                messages=[message],
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
                project_id=project_id,
            )
            await conversation_repo.create_conversation(conversation)
        else:
            await conversation_repo.add_message(conversation_id, message)
            if existing.project_id != project_id:
                await conversation_repo.set_conversation_project(
                    conversation_id, project_id
                )
            if promote_branch_draft:
                new_meta = dict(existing.metadata or {})
                new_meta.pop("hidden_from_sidebar_until_user_message", None)
                await conversation_repo.update_conversation(
                    conversation_id, {"metadata": new_meta}
                )
        return True
    except Exception:
        logger.exception("Ошибка при сохранении сообщения проекта в MongoDB")
        return False


async def get_project_memory_history(
    project_id: str, max_entries: Optional[int] = None
) -> List[Dict[str, Any]]:
    """
    Возвращает историю диалогов только из указанного проекта.
    Используется при memory='project-only'.
    """
    if not _check_mongodb_available():
        return []
    try:
        global conversation_repo
        if conversation_repo is None:
            conversation_repo = get_conversation_repository()
        conversations = await conversation_repo.get_conversations_by_project(project_id)
        history: List[Dict[str, Any]] = []
        for conv in conversations:
            for msg in conv.messages:
                history.append(_message_to_history_entry(msg))
        if max_entries and len(history) > max_entries:
            history = history[-max_entries:]
        return history
    except Exception:
        logger.exception("Ошибка при получении памяти проекта")
        return []


async def get_default_memory_history(
    max_entries: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Возвращает историю только из глобальных (не привязанных к проекту) диалогов.
    Используется при memory='default'.
    """
    if not _check_mongodb_available():
        return []
    try:
        global conversation_repo
        if conversation_repo is None:
            conversation_repo = get_conversation_repository()
        conversations = await conversation_repo.get_global_conversations()
        history: List[Dict[str, Any]] = []
        for conv in conversations:
            for msg in conv.messages:
                history.append(_message_to_history_entry(msg))
        if max_entries and len(history) > max_entries:
            history = history[-max_entries:]
        return history
    except Exception:
        logger.exception("Ошибка при получении глобальной памяти")
        return []


async def delete_project_memory(project_id: str) -> int:
    """
    Удаляет все диалоги проекта из MongoDB (вызывается при удалении проекта).
    Возвращает количество удалённых диалогов.
    """
    if not _check_mongodb_available():
        return 0
    try:
        global conversation_repo
        if conversation_repo is None:
            conversation_repo = get_conversation_repository()
        return await conversation_repo.delete_conversations_by_project(project_id)
    except Exception:
        logger.exception("Ошибка при удалении памяти проекта")
        return 0


async def save_to_memory(role: str, message: str):
    """
    Сохраняет сообщение в память в простом формате (для совместимости со старым API)
    Использует MongoDB для хранения

    Args:
        role: Роль отправителя (например, "Пользователь", "Агент")
        message: Содержание сообщения
    """
    role_normalized = role.lower()
    if "пользователь" in role_normalized or "user" in role_normalized:
        role_normalized = "user"
    elif "агент" in role_normalized or "assistant" in role_normalized:
        role_normalized = "assistant"
    else:
        role_normalized = "system"
    try:
        await save_dialog_entry(role_normalized, message)
    except Exception:
        logger.exception("Ошибка при сохранении в память через save_to_memory")
