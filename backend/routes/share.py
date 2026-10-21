"""
Роутер для создания и просмотра публичных ссылок на сообщения
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Annotated, List, Dict, Any, Optional
import uuid
from datetime import datetime

from backend.database.init_db import get_mongodb_connection
from backend.auth.jwt_handler import get_current_user, get_optional_user
from backend.settings.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/share", tags=["share"])


class Message(BaseModel):
    """Модель сообщения"""
    id: str
    role: str
    content: str
    timestamp: Optional[str] = None
    model: Optional[str] = None


class CreateShareRequest(BaseModel):
    """Запрос на создание публичной ссылки"""
    messages: List[Message]


class ShareResponse(BaseModel):
    """Ответ с ID публичной ссылки"""
    share_id: str
    url: str


class SharedConversation(BaseModel):
    """Публичная беседа"""
    share_id: str
    messages: List[Message]
    created_at: str
    created_by: Optional[str] = None


@router.post("/create", response_model=ShareResponse)
async def create_share_link(
    request: CreateShareRequest,
    current_user=Depends(get_optional_user)
):
    """
    Создать публичную ссылку на выбранные сообщения
    """
    try:
        if not request.messages:
            raise HTTPException(status_code=400, detail="Не выбрано ни одного сообщения")

        # Получаем MongoDB подключение
        mongodb_conn = get_mongodb_connection()
        if mongodb_conn is None or mongodb_conn.db is None:
            raise HTTPException(status_code=500, detail="База данных недоступна")

        # Генерируем уникальный ID
        share_id = str(uuid.uuid4())
        
        # Подготавливаем данные для сохранения
        messages_data = [msg.dict() for msg in request.messages]
        
        # Получаем user_id
        user_id = current_user.get("user_id") if current_user else None
        
        logger.info(f"Создание публичной ссылки: current_user={current_user}, user_id={user_id}")
        
        # Сохраняем в базу данных
        share_data = {
            "share_id": share_id,
            "messages": messages_data,
            "created_at": datetime.utcnow().isoformat(),
            "created_by": user_id,
        }
        
        # Сохраняем в коллекцию shared_conversations
        result = await mongodb_conn.db.shared_conversations.insert_one(share_data)
        
        if not result.inserted_id:
            raise HTTPException(status_code=500, detail="Ошибка при создании публичной ссылки")
        
        logger.info(f"Создана публичная ссылка: {share_id}, пользователь: {user_id}, created_by в БД: {share_data['created_by']}")
        
        return ShareResponse(
            share_id=share_id,
            url=f"/share/{share_id}"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при создании публичной ссылки: {str(e)}")
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера")


@router.get("/my-shares", response_model=List[SharedConversation])
async def get_my_shares(
    current_user=Depends(get_optional_user)
):
    """
    Получить список всех публичных ссылок текущего пользователя
    """
    try:
        # Получаем MongoDB подключение
        mongodb_conn = get_mongodb_connection()
        if mongodb_conn is None or mongodb_conn.db is None:
            raise HTTPException(status_code=500, detail="База данных недоступна")

        # Получаем user_id
        if not current_user:
            # Если пользователь не авторизован, возвращаем пустой список
            logger.warning("Запрос списка ссылок без авторизации")
            return []
        
        user_id = current_user.get("user_id")
        
        if not user_id:
            # Если user_id отсутствует, возвращаем пустой список
            logger.warning(f"Запрос списка ссылок: current_user={current_user}, но user_id отсутствует")
            return []

        logger.info(f"Поиск публичных ссылок для пользователя: {user_id}")

        # Ищем все ссылки, созданные этим пользователем
        cursor = mongodb_conn.db.shared_conversations.find(
            {"created_by": user_id}
        ).sort("created_at", -1)  # Сортируем по дате создания (новые первыми)
        
        shares = []
        async for doc in cursor:
            # Удаляем _id из MongoDB для корректной сериализации
            doc.pop("_id", None)
            shares.append(SharedConversation(**doc))
        
        logger.info(f"Найдено {len(shares)} публичных ссылок для пользователя {user_id}")
        
        # Дополнительная отладка: проверим все ссылки в БД
        all_shares_cursor = mongodb_conn.db.shared_conversations.find({})
        all_shares_count = 0
        async for _ in all_shares_cursor:
            all_shares_count += 1
        logger.debug(f"Всего ссылок в БД: {all_shares_count}")
        
        return shares
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при получении списка публичных ссылок: {str(e)}")
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера")


@router.get("/{share_id}", response_model=SharedConversation)
async def get_shared_conversation(
    share_id: str
):
    """
    Получить публичную беседу по ID
    """
    try:
        # Получаем MongoDB подключение
        mongodb_conn = get_mongodb_connection()
        if mongodb_conn is None or mongodb_conn.db is None:
            raise HTTPException(status_code=500, detail="База данных недоступна")

        # Ищем в базе данных
        shared_data = await mongodb_conn.db.shared_conversations.find_one({"share_id": share_id})
        
        if not shared_data:
            raise HTTPException(status_code=404, detail="Публичная ссылка не найдена")
        
        # Удаляем _id из MongoDB для корректной сериализации
        shared_data.pop("_id", None)
        
        return SharedConversation(**shared_data)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при получении публичной беседы {share_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера")


@router.delete("/{share_id}")
async def delete_shared_conversation(
    share_id: str, current_user: Annotated[dict, Depends(get_current_user)]
):
    """
    Удалить публичную ссылку (только создатель может удалить)
    """
    try:
        # Получаем MongoDB подключение
        mongodb_conn = get_mongodb_connection()
        if mongodb_conn is None or mongodb_conn.db is None:
            raise HTTPException(status_code=500, detail="База данных недоступна")

        # Ищем публичную ссылку
        shared_data = await mongodb_conn.db.shared_conversations.find_one(
            {"share_id": share_id}
        )

        if not shared_data:
            raise HTTPException(status_code=404, detail="Публичная ссылка не найдена")

        # Только создатель может удалить
        if shared_data.get("created_by") != current_user.get("user_id"):
            raise HTTPException(status_code=403, detail="Нет прав для удаления")

        # Удаляем
        result = await mongodb_conn.db.shared_conversations.delete_one({"share_id": share_id})

        if result.deleted_count == 0:
            raise HTTPException(status_code=500, detail="Ошибка при удалении")

        logger.info(f"Удалена публичная ссылка: {share_id}")

        return {"message": "Публичная ссылка успешно удалена"}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка при удалении публичной ссылки %s", share_id)
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера") from e

