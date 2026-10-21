from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from motor.motor_asyncio import AsyncIOMotorCollection
from bson import ObjectId

from .models import Conversation, Message
from .connection import MongoDBConnection
from backend.settings.logging import get_logger

logger = get_logger(__name__)


class ConversationRepository:
    """Репозиторий для работы с диалогами"""

    def __init__(self, db_connection: MongoDBConnection):
        """
        Инициализация репозитория

        Args:
            db_connection: Подключение к MongoDB
        """
        self.db_connection = db_connection
        self.collection_name = "conversations"

    def _get_collection(self) -> AsyncIOMotorCollection:
        """Получение коллекции диалогов"""
        return self.db_connection.get_collection(self.collection_name)

    async def create_indexes(self):
        """Создание индексов для оптимизации поиска"""
        collection = self._get_collection()

        # Индекс по conversation_id (уникальный)
        await collection.create_index("conversation_id", unique=True)

        # Индекс по user_id для быстрого поиска диалогов пользователя
        await collection.create_index("user_id")

        # Индекс по updated_at для сортировки
        await collection.create_index("updated_at")

        # TTL индекс для автоматической очистки старых диалогов
        if await collection.find_one({"expires_at": {"$exists": True}}):
            await collection.create_index("expires_at", expireAfterSeconds=0)

        # Текстовый индекс для поиска по содержимому сообщений
        await collection.create_index([
            ("title", "text"),
            ("messages.content", "text")
        ])

        # Индекс для быстрой фильтрации по project_id
        await collection.create_index("project_id")
        
        logger.info("Индексы для коллекции conversations созданы")

    async def create_conversation(self, conversation: Conversation) -> Optional[str]:
        """
        Создание нового диалога

        Args:
            conversation: Объект диалога

        Returns:
            ID созданного диалога или None в случае ошибки
        """
        try:
            collection = self._get_collection()
            conversation_dict = conversation.model_dump()
            conversation_dict["_id"] = ObjectId()

            result = await collection.insert_one(conversation_dict)
            logger.info(f"Создан диалог: {conversation.conversation_id}")
            return str(result.inserted_id)

        except Exception as e:
            logger.error(f"Ошибка при создании диалога: {e}")
            return None

    async def get_conversation(self, conversation_id: str) -> Optional[Conversation]:
        """
        Получение диалога по ID

        Args:
            conversation_id: ID диалога

        Returns:
            Объект диалога или None
        """
        try:
            collection = self._get_collection()
            result = await collection.find_one({"conversation_id": conversation_id})

            if result:
                result.pop("_id", None)  # Удаляем MongoDB _id
                return Conversation(**result)
            return None

        except Exception as e:
            logger.error(f"Ошибка при получении диалога: {e}")
            return None

    async def add_message(self, conversation_id: str, message: Message) -> bool:
        """
        Добавление сообщения в диалог

        Args:
            conversation_id: ID диалога
            message: Сообщение для добавления

        Returns:
            True если успешно, False в случае ошибки
        """
        try:
            collection = self._get_collection()
            result = await collection.update_one(
                {"conversation_id": conversation_id},
                {
                    "$push": {"messages": message.model_dump()},
                    "$set": {"updated_at": datetime.utcnow()},
                },
            )
            if result.matched_count == 0:
                logger.warning(
                    f"Диалог {conversation_id} не найден: сообщение не добавлено"
                )
                return False
            logger.debug(f"Добавлено сообщение в диалог: {conversation_id}")
            from backend.settings.cef_logger.storage_audit import log_mongo_success

            log_mongo_success(
                "MongoDB.updateOne(conversations.$push)", request_uuid=conversation_id
            )
            return True
        except Exception as e:
            from backend.settings.cef_logger.storage_audit import log_mongo_failure

            log_mongo_failure(
                "MongoDB.updateOne(conversations.$push)",
                str(e),
                request_uuid=conversation_id,
            )
            logger.exception("Ошибка при добавлении сообщения")
            return False


    async def update_message(
        self,
        conversation_id: str,
        message_id: str,
        content: str,
        old_content: str = None
    ) -> bool:
        """
        Обновление сообщения в диалоге

        Args:
            conversation_id: ID диалога
            message_id: ID сообщения для обновления (может быть фронтенд ID)
            content: Новое содержимое сообщения
            old_content: Старое содержимое сообщения (для поиска, если message_id не найден)

        Returns:
            True если успешно, False в случае ошибки
        """
        try:
            collection = self._get_collection()

            # Сначала пытаемся найти по message_id
            result = await collection.update_one(
                {
                    "conversation_id": conversation_id,
                    "messages.message_id": message_id
                },
                {
                    "$set": {
                        "messages.$.content": content,
                        "updated_at": datetime.utcnow()
                    }
                }
            )

            if result.modified_count > 0:
                logger.debug(f"Обновлено сообщение {message_id} в диалоге: {conversation_id}")
                return True

            # Если не найдено по message_id и передано старое содержимое, ищем по нему
            if old_content:
                logger.debug(f"Сообщение {message_id} не найдено, ищем по старому содержимому")
                result = await collection.update_one(
                    {
                        "conversation_id": conversation_id,
                        "messages.content": old_content
                    },
                    {
                        "$set": {
                            "messages.$.content": content,
                            "updated_at": datetime.utcnow()
                        }
                    }
                )

                if result.modified_count > 0:
                    logger.debug(f"Обновлено сообщение по старому содержимому в диалоге: {conversation_id}")
                    return True

            logger.warning(f"Сообщение {message_id} не найдено в диалоге {conversation_id}")
            return False

        except Exception as e:
            logger.error(f"Ошибка при обновлении сообщения: {e}")
            return False

    async def update_message_feedback(
        self,
        conversation_id: str,
        message_id: str,
        feedback: Optional[Dict[str, Any]],
        *,
        multi_llm_slot_index: Optional[int] = None,
    ) -> bool:
        """
        Сохраняет/очищает feedback (like/dislike) в metadata сообщения.
        Для multi-LLM — в metadata.multi_llm_responses[slot].feedback.
        """
        try:
            collection = self._get_collection()
            conversation = await self.get_conversation(conversation_id)
            if not conversation or not conversation.messages:
                return False

            msg_index = None
            for i, msg in enumerate(conversation.messages):
                if getattr(msg, "message_id", None) == message_id:
                    msg_index = i
                    break
            if msg_index is None:
                logger.warning(f"Сообщение {message_id} не найдено в диалоге {conversation_id}")
                return False

            msg = conversation.messages[msg_index]
            meta = dict(msg.metadata or {})

            if multi_llm_slot_index is not None:
                slots = list(meta.get("multi_llm_responses") or meta.get("multiLLMResponses") or [])
                if not isinstance(slots, list) or multi_llm_slot_index < 0 or multi_llm_slot_index >= len(slots):
                    logger.warning(
                        "Некорректный multi_llm_slot_index=%s для сообщения %s",
                        multi_llm_slot_index,
                        message_id,
                    )
                    return False
                slot = dict(slots[multi_llm_slot_index] or {})
                if feedback is None:
                    slot.pop("feedback", None)
                else:
                    slot["feedback"] = feedback
                slots[multi_llm_slot_index] = slot
                meta["multi_llm_responses"] = slots
            else:
                if feedback is None:
                    meta.pop("feedback", None)
                else:
                    meta["feedback"] = feedback

            result = await collection.update_one(
                {"conversation_id": conversation_id},
                {
                    "$set": {
                        f"messages.{msg_index}.metadata": meta,
                        "updated_at": datetime.utcnow(),
                    }
                },
            )
            return result.matched_count > 0
        except Exception as e:
            logger.error(f"Ошибка при сохранении feedback сообщения: {e}")
            return False

    async def update_assistant_message(
        self,
        conversation_id: str,
        message_id: str,
        *,
        content: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Обновляет content и/или metadata сообщения ассистента."""
        if content is None and metadata is None:
            return False
        try:
            collection = self._get_collection()
            set_fields: Dict[str, Any] = {"updated_at": datetime.utcnow()}
            if content is not None:
                set_fields["messages.$.content"] = content
            if metadata is not None:
                set_fields["messages.$.metadata"] = metadata
            result = await collection.update_one(
                {"conversation_id": conversation_id, "messages.message_id": message_id},
                {"$set": set_fields},
            )
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"Ошибка при обновлении metadata сообщения: {e}")
            return False

    async def get_user_conversations(
        self, 
        user_id: str, 
        limit: int = 50,
        skip: int = 0
    ) -> List[Conversation]:
        """
        Получение диалогов пользователя

        Args:
            user_id: ID пользователя
            limit: Максимальное количество диалогов
            skip: Количество пропущенных диалогов

        Returns:
            Список диалогов
        """
        try:
            collection = self._get_collection()

            cursor = collection.find({"user_id": user_id}).sort("updated_at", -1).skip(skip).limit(limit)
            results = await cursor.to_list(length=limit)

            conversations = []
            for result in results:
                result.pop("_id", None)
                conversations.append(Conversation(**result))

            return conversations

        except Exception as e:
            logger.error(f"Ошибка при получении диалогов пользователя: {e}")
            return []

    async def search_conversations(
        self, 
        query: str, 
        user_id: Optional[str] = None,
        limit: int = 50
    ) -> List[Conversation]:
        """
        Поиск диалогов по тексту

        Args:
            query: Поисковый запрос
            user_id: Опциональный фильтр по пользователю
            limit: Максимальное количество результатов

        Returns:
            Список найденных диалогов
        """
        try:
            collection = self._get_collection()

            search_filter: Dict[str, Any] = {"$text": {"$search": query}}
            if user_id:
                search_filter["user_id"] = user_id

            cursor = collection.find(search_filter).sort("score", {"$meta": "textScore"}).limit(limit)
            results = await cursor.to_list(length=limit)

            conversations = []
            for result in results:
                result.pop("_id", None)
                conversations.append(Conversation(**result))

            return conversations

        except Exception as e:
            logger.error(f"Ошибка при поиске диалогов: {e}")
            return []

    async def update_conversation(
        self, conversation_id: str, updates: Dict[str, Any]
    ) -> bool:
        """
        Обновление диалога

        Args:
            conversation_id: ID диалога
            updates: Словарь с обновлениями

        Returns:
            True если успешно, False в случае ошибки
        """
        try:
            collection = self._get_collection()
            # Не мутируем словарь вызывающего кода.
            set_fields = {**updates, "updated_at": datetime.utcnow()}
            result = await collection.update_one(
                {"conversation_id": conversation_id}, {"$set": set_fields}
            )
            if result.matched_count == 0:
                logger.warning(f"Диалог {conversation_id} не найден: обновление пропущено")
                return False
            logger.debug(f"Обновлен диалог: {conversation_id}")
            from backend.settings.cef_logger.storage_audit import log_mongo_success

            log_mongo_success(
                "MongoDB.updateOne(conversations)", request_uuid=conversation_id
            )
            return True
        except Exception as e:
            from backend.settings.cef_logger.storage_audit import log_mongo_failure

            log_mongo_failure(
                "MongoDB.updateOne(conversations)", str(e), request_uuid=conversation_id
            )
            logger.exception("Ошибка при обновлении диалога")
            return False


    async def delete_conversation(self, conversation_id: str) -> bool:
        """
        Удаление диалога

        Args:
            conversation_id: ID диалога

        Returns:
            True если успешно, False в случае ошибки
        """
        try:
            collection = self._get_collection()
            result = await collection.delete_one({"conversation_id": conversation_id})
            if result.deleted_count == 0:
                logger.warning(f"Диалог {conversation_id} не найден: удалять нечего")
                return False
            logger.info(f"Удален диалог: {conversation_id}")
            from backend.settings.cef_logger.storage_audit import log_mongo_success

            log_mongo_success(
                "MongoDB.deleteOne(conversations)", request_uuid=conversation_id
            )
            return True
        except Exception as e:
            from backend.settings.cef_logger.storage_audit import log_mongo_failure

            log_mongo_failure(
                "MongoDB.deleteOne(conversations)", str(e), request_uuid=conversation_id
            )
            logger.exception("Ошибка при удалении диалога")
            return False


    async def delete_user_conversations(self, user_id: str) -> int:
        """Удаление всех диалогов пользователя. Возвращает количество удалённых."""
        try:
            collection = self._get_collection()
            result = await collection.delete_many({"user_id": user_id})
            logger.info(f"Удалено {result.deleted_count} диалогов пользователя {user_id}")
            return result.deleted_count
        except Exception as e:
            logger.error(f"Ошибка при удалении диалогов пользователя {user_id}: {e}")
            return 0

    async def set_conversation_ttl(
        self, 
        conversation_id: str, 
        days: int
    ) -> bool:
        """
        Установка TTL для диалога

        Args:
            conversation_id: ID диалога
            days: Количество дней до истечения

        Returns:
            True если успешно, False в случае ошибки
        """
        try:
            expires_at = datetime.utcnow() + timedelta(days=days)
            return await self.update_conversation(conversation_id, {"expires_at": expires_at})

        except Exception as e:
            logger.error(f"Ошибка при установке TTL: {e}")
            return False

    # ─── Методы для работы с памятью проектов ─────────────────────────────────

    async def get_conversations_by_project(
        self,
        project_id: str,
        limit: int = 200,
    ) -> List[Conversation]:
        """Все диалоги конкретного проекта (project_id = given)."""
        try:
            collection = self._get_collection()
            cursor = (
                collection.find({"project_id": project_id})
                .sort("updated_at", -1)
                .limit(limit)
            )
            results = await cursor.to_list(length=limit)
            conversations = []
            for result in results:
                result.pop("_id", None)
                conversations.append(Conversation(**result))
            return conversations
        except Exception as e:
            logger.error(f"Ошибка при получении диалогов проекта {project_id}: {e}")
            return []

    async def get_global_conversations(self, limit:int = 200) -> List[Conversation]:
        """Все глобальные (не привязанные к проекту) диалоги."""
        try:
            collection = self._get_collection()
            cursor = (
                collection.find({"$or": [{"project_id": None}, {"project_id": {"$exists": False}}]})
                .sort("updated_at", -1)
                .limit(limit)
            )
            results = await cursor.to_list(length=limit)
            conversations = []
            for result in results:
                result.pop("_id", None)
                conversations.append(Conversation(**result))
            return conversations
        except Exception as e:
            logger.error(f"Ошибка при получении глобальных диалогов: {e}")
            return []

    async def delete_conversations_by_project(self, project_id: str) -> int:
        """Удаляет все диалоги проекта. Возвращает количество удалённых."""
        try:
            collection = self._get_collection()
            result = await collection.delete_many({"project_id": project_id})
            deleted = result.deleted_count
            logger.info(f"Удалено {deleted} диалогов проекта {project_id}")
            return deleted
        except Exception as e:
            logger.error(f"Ошибка при удалении диалогов проекта {project_id}: {e}")
            return 0

    async def set_conversation_project(
        self, conversation_id: str, project_id: Optional[str]
    ) -> bool:
        """Установить/сбросить привязку диалога к проекту."""
        try:
            collection = self._get_collection()
            result = await collection.update_one(
                {"conversation_id": conversation_id},
                {"$set": {"project_id": project_id, "updated_at": datetime.utcnow()}},
            )
            if result.matched_count == 0:
                logger.warning(f"Диалог {conversation_id} не найден: project_id не изменён")
                return False
            from backend.settings.cef_logger.storage_audit import log_mongo_success

            log_mongo_success(
                "MongoDB.updateOne(conversations.project_id)",
                request_uuid=conversation_id,
            )
            return True
        except Exception as e:
            from backend.settings.cef_logger.storage_audit import log_mongo_failure

            log_mongo_failure(
                "MongoDB.updateOne(conversations.project_id)",
                str(e),
                request_uuid=conversation_id,
            )
            logger.exception("Ошибка при обновлении project_id диалога")
            return False


    async def remove_last_message(
        self, conversation_id: str, role: Optional[str] = None
    ) -> bool:
        """
        Удаление последнего сообщения из диалога

        Args:
            conversation_id: ID диалога
            role: Если указано, удаляет последнее сообщение с этой ролью (user, assistant)

        Returns:
            True если успешно, False в случае ошибки
        """
        try:
            collection = self._get_collection()
            conversation = await self.get_conversation(conversation_id)
            if not conversation or not conversation.messages:
                logger.warning(f"Диалог {conversation_id} не найден или пуст")
                return False
            if role:
                last_index = -1
                for i in range(len(conversation.messages) - 1, -1, -1):
                    if conversation.messages[i].role == role:
                        last_index = i
                        break
                if last_index == -1:
                    logger.warning(
                        f"Сообщение с ролью '{role}' не найдено в диалоге {conversation_id}"
                    )
                    return False
                message_to_remove = conversation.messages[last_index]
                # Удаляем ровно один элемент по индексу: $pull по message_id уносил
                # бы все сообщения с одинаковым id (например, после регенерации).
                # Условие на message_id по индексу защищает от гонки с параллельной правкой.
                result = await collection.update_one(
                    {
                        "conversation_id": conversation_id,
                        f"messages.{last_index}.message_id": message_to_remove.message_id,
                    },
                    [
                        {
                            "$set": {
                                "messages": {
                                    "$concatArrays": [
                                        {"$slice": ["$messages", last_index]},
                                        {
                                            "$slice": [
                                                "$messages",
                                                last_index + 1,
                                                {"$size": "$messages"},
                                            ]
                                        },
                                    ]
                                },
                                "updated_at": datetime.utcnow(),
                            }
                        }
                    ],
                )
                if result.matched_count == 0:
                    logger.warning(
                        f"Диалог {conversation_id} изменился во время удаления сообщения"
                    )
                    return False
                logger.debug(
                    f"Удалено последнее сообщение с ролью '{role}' из диалога {conversation_id}"
                )
            else:
                await collection.update_one(
                    {"conversation_id": conversation_id},
                    {
                        "$pop": {"messages": 1},
                        "$set": {"updated_at": datetime.utcnow()},
                    },
                )
                logger.debug(
                    f"Удалено последнее сообщение из диалога {conversation_id}"
                )
            from backend.settings.cef_logger.storage_audit import log_mongo_success

            method = (
                "MongoDB.updateOne(conversations.messages.$slice)"
                if role
                else "MongoDB.updateOne(conversations.$pop)"
            )
            log_mongo_success(method, request_uuid=conversation_id)
            return True
        except Exception as e:
            from backend.settings.cef_logger.storage_audit import log_mongo_failure

            log_mongo_failure(
                "MongoDB.updateOne(conversations.messages)",
                str(e),
                request_uuid=conversation_id,
            )
            logger.exception("Ошибка при удалении последнего сообщения")
            return False

