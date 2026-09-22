import json
from datetime import datetime
from typing import List, Optional, Tuple

from backend.database.postgresql.agent_models import (
    AGENT_PERMISSION_EDITOR,
    AGENT_PERMISSION_OWNER,
    AGENT_PERMISSION_VIEWER,
    AGENT_SHARE_PERMISSIONS,
    AgentCreate,
    AgentFilters,
    AgentShare,
    AgentStats,
    AgentUpdate,
    AgentWithTags,
    normalize_permission,
)
from backend.database.postgresql.connection import PostgreSQLConnection
from backend.database.postgresql.agent_models import Tag
from backend.settings.logging import get_logger

logger = get_logger(__name__)


class AgentRepository:
    """Репозиторий для работы с агентами"""

    def __init__(self, db_connection: PostgreSQLConnection):
        """
        Инициализация репозитория

        Args:
            db_connection: Подключение к PostgreSQL
        """
        self.db_connection = db_connection

    async def create_tables(self):
        """Создание таблиц для агентов"""
        try:
            async with await self.db_connection.acquire() as conn:
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS agents (
                        id SERIAL PRIMARY KEY,
                        name VARCHAR(255) NOT NULL,
                        description TEXT,
                        system_prompt TEXT NOT NULL,
                        config JSONB DEFAULT '{}'::jsonb,
                        tools JSONB DEFAULT '[]'::jsonb,
                        author_id VARCHAR(100) NOT NULL,
                        author_name VARCHAR(255) NOT NULL,
                        created_at TIMESTAMP DEFAULT NOW(),
                        updated_at TIMESTAMP DEFAULT NOW(),
                        is_public BOOLEAN DEFAULT true,
                        usage_count INTEGER DEFAULT 0,
                        views_count INTEGER DEFAULT 0
                    )
                    """)
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS tags (
                        id SERIAL PRIMARY KEY,
                        name VARCHAR(100) UNIQUE NOT NULL,
                        description TEXT,
                        color VARCHAR(7),
                        created_at TIMESTAMP DEFAULT NOW()
                    )
                    """)
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS agent_tags (
                        agent_id INTEGER REFERENCES agents(id) ON DELETE CASCADE,
                        tag_id INTEGER REFERENCES tags(id) ON DELETE CASCADE,
                        PRIMARY KEY (agent_id, tag_id)
                    )
                    """)
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS agent_ratings (
                        id SERIAL PRIMARY KEY,
                        agent_id INTEGER REFERENCES agents(id) ON DELETE CASCADE,
                        user_id VARCHAR(100) NOT NULL,
                        rating INTEGER CHECK (rating >= 1 AND rating <= 5),
                        created_at TIMESTAMP DEFAULT NOW(),
                        updated_at TIMESTAMP DEFAULT NOW(),
                        UNIQUE(agent_id, user_id)
                    )
                    """)
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS agent_bookmarks (
                        id SERIAL PRIMARY KEY,
                        agent_id INTEGER REFERENCES agents(id) ON DELETE CASCADE,
                        user_id VARCHAR(100) NOT NULL,
                        created_at TIMESTAMP DEFAULT NOW(),
                        UNIQUE(agent_id, user_id)
                    )
                    """)
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS agent_shares (
                        id SERIAL PRIMARY KEY,
                        agent_id INTEGER REFERENCES agents(id) ON DELETE CASCADE,
                        owner_id VARCHAR(100) NOT NULL,
                        shared_with_user_id VARCHAR(100) NOT NULL,
                        permission VARCHAR(20) DEFAULT 'viewer',
                        created_at TIMESTAMP DEFAULT NOW(),
                        UNIQUE(agent_id, shared_with_user_id)
                    )
                    """)
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_agents_author ON agents(author_id)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_agents_created ON agents(created_at DESC)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_agents_public ON agents(is_public)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_tags_agent ON agent_tags(agent_id)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_tags_tag ON agent_tags(tag_id)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_ratings_agent ON agent_ratings(agent_id)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_ratings_user ON agent_ratings(user_id)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_bookmarks_user ON agent_bookmarks(user_id)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_bookmarks_agent ON agent_bookmarks(agent_id)")
                await conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_agent_shares_recipient ON agent_shares(shared_with_user_id)"
                )
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_shares_agent ON agent_shares(agent_id)")
                logger.info("Таблицы для галереи агентов созданы")
        except Exception:
            logger.exception("Ошибка при создании таблиц агентов")
            raise

    async def create_agent(self, agent_data: AgentCreate, author_id: str, author_name: str) -> Optional[int]:
        """
        Создание нового агента

        Args:
            agent_data: Данные агента
            author_id: ID автора
            author_name: Имя автора

        Returns:
            ID созданного агента или None в случае ошибки
        """
        try:
            author_id = author_id.strip().lower() if author_id else author_id
            async with await self.db_connection.acquire() as conn:
                config_json = json.dumps(agent_data.config or {})
                tools_json = json.dumps(agent_data.tools or [])
                result = await conn.fetchrow(
                    """
                    INSERT INTO agents (name, description, system_prompt, config, tools, author_id, author_name, is_public)
                    VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6, $7, $8)
                    RETURNING id
                    """,
                    agent_data.name,
                    agent_data.description,
                    agent_data.system_prompt,
                    config_json,
                    tools_json,
                    author_id,
                    author_name,
                    agent_data.is_public,
                )
                agent_id = result["id"]
                all_tag_ids = list(agent_data.tag_ids) if agent_data.tag_ids else []
                if agent_data.new_tags:
                    for tag_name in agent_data.new_tags:
                        tag_name = tag_name.strip()
                        if not tag_name or len(tag_name) < 2:
                            logger.warning(f"Пропущен тег с некорректным именем (меньше 2 символов): '{tag_name}'")
                            continue
                        existing_tag = await conn.fetchrow(
                            """
                            SELECT id FROM tags WHERE LOWER(name) = LOWER($1)
                            """,
                            tag_name,
                        )
                        if existing_tag:
                            all_tag_ids.append(existing_tag["id"])
                        else:
                            new_tag = await conn.fetchrow(
                                """
                                INSERT INTO tags (name)
                                VALUES ($1)
                                RETURNING id
                                """,
                                tag_name,
                            )
                            all_tag_ids.append(new_tag["id"])
                            logger.info(f"Создан новый тег: {tag_name}")
                for tag_id in set(all_tag_ids):
                    await conn.execute(
                        """
                        INSERT INTO agent_tags (agent_id, tag_id)
                        VALUES ($1, $2)
                        ON CONFLICT DO NOTHING
                        """,
                        agent_id,
                        tag_id,
                    )
                logger.info(f"Создан агент: {agent_data.name} (ID: {agent_id})")
                return agent_id
        except Exception:
            logger.exception("Ошибка при создании агента")
            return None

    async def get_agent(self, agent_id: int, user_id: Optional[str] = None) -> Optional[AgentWithTags]:
        """
        Получение агента по ID с тегами и рейтингом

        Args:
            agent_id: ID агента
            user_id: ID пользователя (для получения его оценки)

        Returns:
            Агент с тегами и рейтингом или None
        """
        try:
            async with await self.db_connection.acquire() as conn:
                agent_row = await conn.fetchrow(
                    """
                    SELECT a.*,
                           COALESCE(AVG(ar.rating), 0) AS average_rating,
                           COUNT(ar.id) AS total_votes
                    FROM agents a
                    LEFT JOIN agent_ratings ar ON a.id = ar.agent_id
                    WHERE a.id = $1
                    GROUP BY a.id
                    """,
                    agent_id,
                )
                if not agent_row:
                    return None
                tag_rows = await conn.fetch(
                    """
                    SELECT t.*
                    FROM tags t
                    JOIN agent_tags at ON t.id = at.tag_id
                    WHERE at.agent_id = $1
                    """,
                    agent_id,
                )
                tags = []
                for row in tag_rows:
                    try:
                        tag = Tag(**dict(row))
                        tags.append(tag.dict())
                    except Exception:
                        logger.exception("Пропущен некорректный тег (ID: , name: )")
                        continue
                user_rating = None
                is_bookmarked = False
                is_shared_with_me = False
                my_permission = None
                if user_id:
                    normalized_user_id = user_id.strip().lower() if user_id else user_id
                    rating_row = await conn.fetchrow(
                        """
                        SELECT rating FROM agent_ratings
                        WHERE agent_id = $1 AND LOWER(TRIM(user_id)) = LOWER(TRIM($2))
                        """,
                        agent_id,
                        normalized_user_id,
                    )
                    if rating_row:
                        user_rating = rating_row["rating"]
                    bookmark_row = await conn.fetchrow(
                        """
                        SELECT id FROM agent_bookmarks
                        WHERE agent_id = $1 AND LOWER(TRIM(user_id)) = LOWER(TRIM($2))
                        """,
                        agent_id,
                        normalized_user_id,
                    )
                    is_bookmarked = bookmark_row is not None
                    share_row = await conn.fetchrow(
                        """
                        SELECT permission FROM agent_shares
                        WHERE agent_id = $1 AND LOWER(TRIM(shared_with_user_id)) = LOWER(TRIM($2))
                        """,
                        agent_id,
                        normalized_user_id,
                    )
                    is_shared_with_me = share_row is not None
                    if (agent_row["author_id"] or "").strip().lower() == normalized_user_id:
                        my_permission = AGENT_PERMISSION_OWNER
                    elif share_row is not None:
                        my_permission = normalize_permission(share_row["permission"])
                    elif agent_row["is_public"]:
                        my_permission = AGENT_PERMISSION_VIEWER
                config = (
                    agent_row["config"]
                    if isinstance(agent_row["config"], dict)
                    else json.loads(agent_row["config"] or "{}")
                )
                tools = (
                    agent_row["tools"]
                    if isinstance(agent_row["tools"], list)
                    else json.loads(agent_row["tools"] or "[]")
                )
                agent = AgentWithTags(
                    id=agent_row["id"],
                    name=agent_row["name"],
                    description=agent_row["description"],
                    system_prompt=agent_row["system_prompt"],
                    config=config,
                    tools=tools,
                    author_id=agent_row["author_id"],
                    author_name=agent_row["author_name"],
                    created_at=agent_row["created_at"],
                    updated_at=agent_row["updated_at"],
                    is_public=agent_row["is_public"],
                    usage_count=agent_row["usage_count"],
                    views_count=agent_row["views_count"],
                    tags=tags,
                    average_rating=float(agent_row["average_rating"]),
                    total_votes=agent_row["total_votes"],
                    user_rating=user_rating,
                    is_bookmarked=is_bookmarked,
                    is_shared_with_me=is_shared_with_me,
                    my_permission=my_permission,
                )
                return agent
        except Exception:
            logger.exception("Ошибка при получении агента")
            return None

    async def get_agents(self, filters: AgentFilters, user_id: Optional[str] = None) -> Tuple[List[AgentWithTags], int]:
        """
        Получение списка агентов с фильтрацией

        Args:
            filters: Фильтры для поиска
            user_id: ID пользователя (для получения его оценок)

        Returns:
            Кортеж (список агентов, общее количество)
        """
        try:
            async with await self.db_connection.acquire() as conn:
                if getattr(filters, "author_only", False) and filters.author_id:
                    where_conditions = ["a.author_id = $1"]
                    params = [filters.author_id]
                    param_num = 2
                else:
                    where_conditions = ["a.is_public = true"]
                    params = []
                    param_num = 1
                if filters.search_query:
                    search_pattern = f"%{filters.search_query}%"
                    where_conditions.append(
                        f"(a.name ILIKE ${param_num} OR a.description ILIKE ${param_num} OR a.system_prompt ILIKE ${param_num})"
                    )
                    params.append(search_pattern)
                    param_num += 1
                if filters.author_id and (not getattr(filters, "author_only", False)):
                    where_conditions.append(f"a.author_id = ${param_num}")
                    params.append(filters.author_id)
                    param_num += 1
                tag_join = ""
                if filters.tag_ids:
                    tag_join = "JOIN agent_tags at ON a.id = at.agent_id"
                    placeholders = ", ".join([f"${i}" for i in range(param_num, param_num + len(filters.tag_ids))])
                    where_conditions.append(f"at.tag_id IN ({placeholders})")
                    params.extend(filters.tag_ids)
                    param_num += len(filters.tag_ids)
                where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"
                sort_map = {
                    "rating": "avg_rating",
                    "date": "a.created_at",
                    "views": "a.views_count",
                    "usage": "a.usage_count",
                    "votes": "total_votes",
                }
                sort_field = sort_map.get(filters.sort_by, "avg_rating")
                sort_order = "DESC" if filters.sort_order.lower() == "desc" else "ASC"
                rating_filter = ""
                if filters.min_rating is not None:
                    rating_filter = f" AND COALESCE(agg.avg_rating, 0) >= ${param_num}"
                    params.append(filters.min_rating)
                    param_num += 1
                limit_param = f"${param_num}"
                offset_param = f"${param_num + 1}"
                params.extend([filters.limit, filters.offset])
                query = f"""
SELECT
    a.*,
    COALESCE(agg.avg_rating, 0) AS avg_rating,
    COALESCE(agg.total_votes, 0) AS total_votes
FROM agents a
LEFT JOIN (
    SELECT
        ar.agent_id,
        AVG(ar.rating) AS avg_rating,
        COUNT(ar.id) AS total_votes
    FROM agent_ratings ar
    GROUP BY ar.agent_id
) agg ON a.id = agg.agent_id
{tag_join}
WHERE {where_clause}{rating_filter}
ORDER BY {sort_field} {sort_order}, a.id DESC
LIMIT {limit_param} OFFSET {offset_param}
""".strip()  # noqa: S608
                agent_rows = await conn.fetch(query, *params)
                count_params = params[:-2]
                count_query = f"""
SELECT COUNT(*)
FROM agents a
LEFT JOIN (
    SELECT
        ar.agent_id,
        AVG(ar.rating) AS avg_rating
    FROM agent_ratings ar
    GROUP BY ar.agent_id
) agg ON a.id = agg.agent_id
{tag_join}
WHERE {where_clause}{rating_filter}
""".strip()  # noqa: S608
                total_count = await conn.fetchval(count_query, *count_params)
                agents = []
                for row in agent_rows:
                    tag_rows = await conn.fetch(
                        """
                        SELECT t.*
                        FROM tags t
                        JOIN agent_tags at ON t.id = at.tag_id
                        WHERE at.agent_id = $1
                        """,
                        row["id"],
                    )
                    tags = []
                    for tag_row in tag_rows:
                        try:
                            tag = Tag(**dict(tag_row))
                            tags.append(tag.dict())
                        except Exception:
                            logger.exception("Пропущен некорректный тег для агента")
                            continue
                    user_rating = None
                    is_bookmarked = False
                    is_shared_with_me = False
                    my_permission = None
                    if user_id:
                        normalized_user_id = user_id.strip().lower() if user_id else user_id
                        rating_row = await conn.fetchrow(
                            """
                            SELECT rating FROM agent_ratings
                            WHERE agent_id = $1 AND LOWER(TRIM(user_id)) = LOWER(TRIM($2))
                            """,
                            row["id"],
                            normalized_user_id,
                        )
                        if rating_row:
                            user_rating = rating_row["rating"]
                        bookmark_row = await conn.fetchrow(
                            """
                            SELECT id FROM agent_bookmarks
                            WHERE agent_id = $1 AND LOWER(TRIM(user_id)) = LOWER(TRIM($2))
                            """,
                            row["id"],
                            normalized_user_id,
                        )
                        is_bookmarked = bookmark_row is not None
                        share_row = await conn.fetchrow(
                            """
                            SELECT permission FROM agent_shares
                            WHERE agent_id = $1 AND LOWER(TRIM(shared_with_user_id)) = LOWER(TRIM($2))
                            """,
                            row["id"],
                            normalized_user_id,
                        )
                        is_shared_with_me = share_row is not None
                        if (row["author_id"] or "").strip().lower() == normalized_user_id:
                            my_permission = AGENT_PERMISSION_OWNER
                        elif share_row is not None:
                            my_permission = normalize_permission(share_row["permission"])
                        elif row["is_public"]:
                            my_permission = AGENT_PERMISSION_VIEWER
                    config = row["config"] if isinstance(row["config"], dict) else json.loads(row["config"] or "{}")
                    tools = row["tools"] if isinstance(row["tools"], list) else json.loads(row["tools"] or "[]")
                    agent = AgentWithTags(
                        id=row["id"],
                        name=row["name"],
                        description=row["description"],
                        system_prompt=row["system_prompt"],
                        config=config,
                        tools=tools,
                        author_id=row["author_id"],
                        author_name=row["author_name"],
                        created_at=row["created_at"],
                        updated_at=row["updated_at"],
                        is_public=row["is_public"],
                        usage_count=row["usage_count"],
                        views_count=row["views_count"],
                        tags=tags,
                        average_rating=float(row["avg_rating"]),
                        total_votes=row["total_votes"],
                        user_rating=user_rating,
                        is_bookmarked=is_bookmarked,
                        is_shared_with_me=is_shared_with_me,
                        my_permission=my_permission,
                    )
                    agents.append(agent)
                return (agents, total_count)
        except Exception:
            logger.exception("Ошибка при получении списка агентов")
            return ([], 0)

    async def update_agent(self, agent_id: int, agent_data: AgentUpdate, author_id: str) -> bool:
        """
        Обновление агента (автор-владелец или редактор).

        Args:
            agent_id: ID агента
            agent_data: Новые данные
            author_id: ID пользователя, выполняющего изменение (для проверки прав)

        Returns:
            True если успешно, False в случае ошибки
        """
        try:
            author_id = author_id.strip().lower() if author_id else author_id
            permission = await self.get_user_permission(agent_id, author_id)
            if permission not in (AGENT_PERMISSION_OWNER, AGENT_PERMISSION_EDITOR):
                logger.warning(
                    f"Попытка редактирования без прав: user={author_id}, agent={agent_id}, perm={permission}"
                )
                return False
            async with await self.db_connection.acquire() as conn:
                update_fields = []
                params = []
                param_num = 1
                if agent_data.name is not None:
                    update_fields.append(f"name = ${param_num}")
                    params.append(agent_data.name)
                    param_num += 1
                if agent_data.description is not None:
                    update_fields.append(f"description = ${param_num}")
                    params.append(agent_data.description)
                    param_num += 1
                if agent_data.system_prompt is not None:
                    update_fields.append(f"system_prompt = ${param_num}")
                    params.append(agent_data.system_prompt)
                    param_num += 1
                if agent_data.config is not None:
                    update_fields.append(f"config = ${param_num}::jsonb")
                    params.append(json.dumps(agent_data.config))
                    param_num += 1
                if agent_data.tools is not None:
                    update_fields.append(f"tools = ${param_num}::jsonb")
                    params.append(json.dumps(agent_data.tools))
                    param_num += 1
                if agent_data.is_public is not None:
                    # Публикация в галерею — только владелец; у редактора поле игнорируем
                    if permission == AGENT_PERMISSION_OWNER:
                        update_fields.append(f"is_public = ${param_num}")
                        params.append(agent_data.is_public)
                        param_num += 1
                    else:
                        logger.info(
                            f"Игнорируем смену is_public от редактора: user={author_id}, agent={agent_id}"
                        )
                update_fields.append(f"updated_at = ${param_num}")
                params.append(datetime.utcnow())
                param_num += 1
                if update_fields:
                    params.append(agent_id)
                    query = f"""
UPDATE agents
SET {', '.join(update_fields)}
WHERE id = ${param_num}
""".strip()  # noqa: S608
                    await conn.execute(query, *params)
                if agent_data.tag_ids is not None or agent_data.new_tags is not None:
                    await conn.execute("DELETE FROM agent_tags WHERE agent_id = $1", agent_id)
                    all_tag_ids = list(agent_data.tag_ids) if agent_data.tag_ids else []
                    if agent_data.new_tags:
                        for tag_name in agent_data.new_tags:
                            tag_name = tag_name.strip()
                            if not tag_name or len(tag_name) < 2:
                                logger.warning(f"Пропущен тег с некорректным именем: '{tag_name}'")
                                continue
                            existing_tag = await conn.fetchrow(
                                """
                                SELECT id FROM tags WHERE LOWER(name) = LOWER($1)
                                """,
                                tag_name,
                            )
                            if existing_tag:
                                all_tag_ids.append(existing_tag["id"])
                            else:
                                new_tag = await conn.fetchrow(
                                    """
                                    INSERT INTO tags (name)
                                    VALUES ($1)
                                    RETURNING id
                                    """,
                                    tag_name,
                                )
                                all_tag_ids.append(new_tag["id"])
                    for tag_id in set(all_tag_ids):
                        await conn.execute(
                            """
                            INSERT INTO agent_tags (agent_id, tag_id)
                            VALUES ($1, $2)
                            ON CONFLICT DO NOTHING
                            """,
                            agent_id,
                            tag_id,
                        )
                logger.info(f"Обновлён агент: {agent_id}")
                return True
        except Exception:
            logger.exception("Ошибка при обновлении агента")
            return False

    async def find_orphan_kb_document_ids(self, document_ids: List[int]) -> List[int]:
        """Из переданных id вернуть те, на которые НЕ ссылается ни один агент.

        Ссылки живут в agents.config->'kb_document_ids' (JSONB-массив).
        Вызывать ПОСЛЕ удаления агента — тогда его собственные ссылки уже не учитываются.
        При любой ошибке возвращаем пустой список: лучше оставить мусор, чем снести чужое.
        """
        wanted = sorted({int(d) for d in (document_ids or []) if d is not None})
        if not wanted:
            return []
        try:
            async with await self.db_connection.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT DISTINCT x.doc_id::int AS doc_id
                    FROM agents a,
                         LATERAL jsonb_array_elements_text(
                             CASE WHEN jsonb_typeof(a.config->'kb_document_ids') = 'array'
                                  THEN a.config->'kb_document_ids'
                                  ELSE '[]'::jsonb END
                         ) AS x(doc_id)
                    WHERE x.doc_id ~ '^[0-9]+$'
                      AND x.doc_id::int = ANY($1::int[])
                    """,
                    wanted,
                )
                still_used = {int(r["doc_id"]) for r in rows}
                return [d for d in wanted if d not in still_used]
        except Exception:
            logger.exception("Не удалось вычислить осиротевшие kb_document_ids")
            return []

    async def find_documents_claimed_by_other_agents(
        self, agent_id: Optional[int], document_ids: List[int]
    ) -> List[int]:
        """Из переданных id вернуть те, что уже числятся за ДРУГИМ агентом.

        Файл принадлежит ровно одному агенту: иначе перечанковка одного перекроит
        чанки второго его параметрами, и второй об этом не узнает. Источник истины —
        ``agents.config->'kb_document_ids'``, тот же, что у ``find_orphan_kb_document_ids``.

        При ошибке возвращаем пустой список: лучше пропустить спорную привязку,
        чем запретить пользователю сохранить своего агента.
        """
        wanted = sorted({int(d) for d in (document_ids or []) if d is not None})
        if not wanted:
            return []
        try:
            async with await self.db_connection.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT DISTINCT x.doc_id::int AS doc_id
                    FROM agents a,
                         LATERAL jsonb_array_elements_text(
                             CASE WHEN jsonb_typeof(a.config->'kb_document_ids') = 'array'
                                  THEN a.config->'kb_document_ids'
                                  ELSE '[]'::jsonb END
                         ) AS x(doc_id)
                    WHERE ($1::int IS NULL OR a.id <> $1::int)
                      AND x.doc_id ~ '^[0-9]+$'
                      AND x.doc_id::int = ANY($2::int[])
                    """,
                    int(agent_id) if agent_id is not None else None,
                    wanted,
                )
                return sorted({int(r["doc_id"]) for r in rows})
        except Exception:
            logger.exception("Не удалось проверить принадлежность kb_document_ids")
            return []

    async def delete_agent(self, agent_id: int, author_id: str) -> bool:
        """
        Удаление агента (только автор может удалить)

        Args:
            agent_id: ID агента
            author_id: ID автора (для проверки прав)

        Returns:
            True если успешно, False в случае ошибки
        """
        try:
            author_id = author_id.strip().lower() if author_id else author_id
            async with await self.db_connection.acquire() as conn:
                author_check = await conn.fetchval(
                    """
                    SELECT LOWER(TRIM(author_id)) FROM agents WHERE id = $1
                    """,
                    agent_id,
                )
                if author_check != author_id:
                    logger.warning(f"Попытка удаления чужого агента: user={author_id}, agent={agent_id}")
                    return False
                await conn.execute("DELETE FROM agents WHERE id = $1", agent_id)
                logger.info(f"Удалён агент: {agent_id}")
            # Настройки лежат в отдельной таблице без внешнего ключа (agents.id —
            # INTEGER, user_projects.id — VARCHAR, один FK на двоих не сделать),
            # поэтому убираем их явно. Иначе новый агент с тем же id унаследовал бы
            # чужие параметры нарезки.
            await self._delete_entity_settings(agent_id)
            await self.remove_agent_from_chains(agent_id)
            await self.remove_agent_from_subagents(agent_id)
            return True
        except Exception:
            logger.exception("Ошибка при удалении агента")
            return False

    async def remove_agent_from_chains(self, deleted_id: int) -> None:
        """Убрать удалённого агента из config.agent_ids чужих карточек."""
        try:
            async with await self.db_connection.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, config FROM agents
                    WHERE jsonb_typeof(config->'agent_ids') = 'array'
                    """
                )
                for row in rows:
                    cfg = row["config"] if isinstance(row["config"], dict) else {}
                    raw_ids = cfg.get("agent_ids") if isinstance(cfg, dict) else None
                    if not isinstance(raw_ids, list):
                        continue
                    new_ids = []
                    changed = False
                    for item in raw_ids:
                        try:
                            aid = int(item)
                        except (TypeError, ValueError):
                            continue
                        if aid == deleted_id:
                            changed = True
                            continue
                        new_ids.append(aid)
                    if not changed:
                        continue
                    next_cfg = dict(cfg)
                    next_cfg["agent_ids"] = new_ids
                    await conn.execute(
                        """
                        UPDATE agents SET config = $1::jsonb, updated_at = NOW()
                        WHERE id = $2
                        """,
                        json.dumps(next_cfg),
                        row["id"],
                    )
        except Exception:
            logger.exception("Не удалось вычистить agent_ids после удаления %s", deleted_id)

    async def list_tags(self) -> List[dict]:
        """Справочник тегов для карточки агента: id, name, color."""
        try:
            async with await self.db_connection.acquire() as conn:
                rows = await conn.fetch("SELECT id, name, color FROM tags ORDER BY name")
                return [
                    {"id": int(r["id"]), "name": r["name"], "color": r["color"]}
                    for r in rows
                ]
        except Exception:
            logger.exception("Ошибка чтения справочника тегов")
            return []

    async def list_agent_ids_by_tags(self, tag_ids: List[int]) -> List[int]:
        """id агентов, у которых есть хотя бы один из тегов. Доступ не проверяется."""
        ids: List[int] = []
        for raw in tag_ids or []:
            try:
                ids.append(int(raw))
            except (TypeError, ValueError):
                continue
        if not ids:
            return []
        try:
            async with await self.db_connection.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT DISTINCT agent_id FROM agent_tags WHERE tag_id = ANY($1::int[]) ORDER BY agent_id",
                    ids,
                )
                return [int(r["agent_id"]) for r in rows]
        except Exception:
            logger.exception("Ошибка выборки агентов по тегам %s", ids)
            return []

    async def remove_agent_from_subagents(self, deleted_id: int) -> None:
        """Убрать удалённого агента из config.subagents.agent_ids чужих карточек."""
        try:
            async with await self.db_connection.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, config FROM agents
                    WHERE jsonb_typeof(config->'subagents'->'agent_ids') = 'array'
                    """
                )
                for row in rows:
                    cfg = row["config"] if isinstance(row["config"], dict) else {}
                    subagents = cfg.get("subagents") if isinstance(cfg, dict) else None
                    if not isinstance(subagents, dict):
                        continue
                    raw_ids = subagents.get("agent_ids")
                    if not isinstance(raw_ids, list):
                        continue
                    new_ids = []
                    changed = False
                    for item in raw_ids:
                        try:
                            aid = int(item)
                        except (TypeError, ValueError):
                            continue
                        if aid == deleted_id:
                            changed = True
                            continue
                        new_ids.append(aid)
                    if not changed:
                        continue
                    next_cfg = dict(cfg)
                    next_sub = dict(subagents)
                    next_sub["agent_ids"] = new_ids
                    next_cfg["subagents"] = next_sub
                    await conn.execute(
                        """
                        UPDATE agents SET config = $1::jsonb, updated_at = NOW()
                        WHERE id = $2
                        """,
                        json.dumps(next_cfg),
                        row["id"],
                    )
        except Exception:
            logger.exception("Не удалось вычистить subagents после удаления %s", deleted_id)

    async def _delete_entity_settings(self, agent_id: int) -> None:
        """Убрать RAG-настройки удалённого агента. Сбой тут агента не воскрешает."""
        try:
            from backend.database.init_db import get_entity_settings_repository

            repo = get_entity_settings_repository()
            if repo is not None:
                await repo.delete("agent", str(agent_id))
        except Exception:
            logger.debug(
                "Не удалось убрать настройки агента %s — подчистятся на старте", agent_id,
                exc_info=True,
            )

    async def rate_agent(self, agent_id: int, user_id: str, rating: int) -> bool:
        """
        Оценка агента пользователем

        Args:
            agent_id: ID агента
            user_id: ID пользователя
            rating: Оценка (1-5)

        Returns:
            True если успешно, False в случае ошибки
        """
        try:
            user_id = user_id.strip().lower() if user_id else user_id
            async with await self.db_connection.acquire() as conn:
                exists = await conn.fetchval("SELECT EXISTS(SELECT 1 FROM agents WHERE id = $1)", agent_id)
                if not exists:
                    logger.warning(f"Попытка оценить несуществующий агент: {agent_id}")
                    return False
                existing_rating = await conn.fetchrow(
                    """
                    SELECT rating, id FROM agent_ratings
                    WHERE agent_id = $1 AND LOWER(TRIM(user_id)) = LOWER(TRIM($2))
                    """,
                    agent_id,
                    user_id,
                )
                if existing_rating:
                    await conn.execute(
                        """
                        UPDATE agent_ratings
                        SET rating = $1, updated_at = NOW()
                        WHERE agent_id = $2 AND LOWER(TRIM(user_id)) = LOWER(TRIM($3))
                        """,
                        rating,
                        agent_id,
                        user_id,
                    )
                    logger.info(
                        f"Пользователь {user_id} обновил оценку агента {agent_id} с {existing_rating['rating']} на {rating}"
                    )
                else:
                    await conn.execute(
                        """
                        INSERT INTO agent_ratings (agent_id, user_id, rating)
                        VALUES ($1, $2, $3)
                        """,
                        agent_id,
                        user_id,
                        rating,
                    )
                    logger.info(f"Пользователь {user_id} впервые оценил агента {agent_id} на {rating}")
                return True
        except Exception:
            logger.exception("Ошибка при оценке агента")
            return False

    async def increment_views(self, agent_id: int) -> bool:
        """Увеличить счётчик просмотров"""
        try:
            async with await self.db_connection.acquire() as conn:
                current_views = await conn.fetchval(
                    """
                    SELECT views_count FROM agents WHERE id = $1
                    """,
                    agent_id,
                )
                if current_views is None:
                    logger.warning(f"Агент с ID {agent_id} не найден")
                    return False
                await conn.execute(
                    """
                    UPDATE agents SET views_count = views_count + 1
                    WHERE id = $1
                    """,
                    agent_id,
                )
                logger.info(f"Агент {agent_id}: просмотры {current_views} -> {current_views + 1}")
                return True
        except Exception:
            logger.exception("Ошибка при увеличении просмотров для агента")
            return False

    async def increment_usage(self, agent_id: int) -> bool:
        """Увеличить счётчик использований"""
        try:
            async with await self.db_connection.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE agents SET usage_count = usage_count + 1
                    WHERE id = $1
                    """,
                    agent_id,
                )
                return True
        except Exception:
            logger.exception("Ошибка при увеличении использований")
            return False

    async def get_agent_stats(self, agent_id: int) -> Optional[AgentStats]:
        """Получить статистику агента"""
        try:
            async with await self.db_connection.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT
                        a.views_count,
                        a.usage_count,
                        COALESCE(AVG(ar.rating), 0) AS average_rating,
                        COUNT(ar.id) AS total_votes
                    FROM agents a
                    LEFT JOIN agent_ratings ar ON a.id = ar.agent_id
                    WHERE a.id = $1
                    GROUP BY a.id, a.views_count, a.usage_count
                    """,
                    agent_id,
                )
                if not row:
                    return None
                distribution_rows = await conn.fetch(
                    """
                    SELECT rating, COUNT(*) AS count
                    FROM agent_ratings
                    WHERE agent_id = $1
                    GROUP BY rating
                    """,
                    agent_id,
                )
                rating_distribution = {row["rating"]: row["count"] for row in distribution_rows}
                return AgentStats(
                    agent_id=agent_id,
                    views_count=row["views_count"],
                    usage_count=row["usage_count"],
                    average_rating=float(row["average_rating"]),
                    total_votes=row["total_votes"],
                    rating_distribution=rating_distribution,
                )
        except Exception:
            logger.exception("Ошибка при получении статистики")
            return None

    async def add_bookmark(self, agent_id: int, user_id: str) -> bool:
        """Добавить агента в закладки"""
        try:
            user_id = user_id.strip().lower() if user_id else user_id
            async with await self.db_connection.acquire() as conn:
                exists = await conn.fetchval("SELECT EXISTS(SELECT 1 FROM agents WHERE id = $1)", agent_id)
                if not exists:
                    logger.warning(f"Попытка добавить в закладки несуществующий агент: {agent_id}")
                    return False
                await conn.execute(
                    """
                    INSERT INTO agent_bookmarks (agent_id, user_id)
                    VALUES ($1, $2)
                    ON CONFLICT (agent_id, user_id) DO NOTHING
                    """,
                    agent_id,
                    user_id,
                )
                logger.info(f"Пользователь {user_id} добавил агента {agent_id} в закладки")
                return True
        except Exception:
            logger.exception("Ошибка при добавлении в закладки")
            return False

    async def remove_bookmark(self, agent_id: int, user_id: str) -> bool:
        """Удалить агента из закладок"""
        try:
            user_id = user_id.strip().lower() if user_id else user_id
            async with await self.db_connection.acquire() as conn:
                await conn.execute(
                    """
                    DELETE FROM agent_bookmarks
                    WHERE agent_id = $1 AND LOWER(TRIM(user_id)) = LOWER(TRIM($2))
                    """,
                    agent_id,
                    user_id,
                )
                logger.info(f"Пользователь {user_id} удалил агента {agent_id} из закладок")
                return True
        except Exception:
            logger.exception("Ошибка при удалении из закладок")
            return False

    async def get_user_bookmarks(self, user_id: str, limit: int = 100, offset: int = 0) -> Tuple[List[int], int]:
        """Получить список ID агентов в закладках пользователя"""
        try:
            user_id = user_id.strip().lower() if user_id else user_id
            async with await self.db_connection.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT agent_id
                    FROM agent_bookmarks
                    WHERE LOWER(TRIM(user_id)) = LOWER(TRIM($1))
                    ORDER BY created_at DESC
                    LIMIT $2 OFFSET $3
                    """,
                    user_id,
                    limit,
                    offset,
                )
                total = await conn.fetchval(
                    """
                    SELECT COUNT(*)
                    FROM agent_bookmarks
                    WHERE LOWER(TRIM(user_id)) = LOWER(TRIM($1))
                    """,
                    user_id,
                )
                agent_ids = [row["agent_id"] for row in rows]
                return (agent_ids, total)
        except Exception:
            logger.exception("Ошибка при получении закладок")
            return ([], 0)

    async def get_user_permission(self, agent_id: int, user_id: Optional[str] = None) -> Optional[str]:
        """
        Роль пользователя для агента: owner | editor | viewer | None.

        - автор → owner
        - запись в agent_shares → editor/viewer (старое 'use' = viewer)
        - публичный агент → viewer
        - иначе None
        """
        try:
            async with await self.db_connection.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT is_public, LOWER(TRIM(author_id)) AS author_id
                    FROM agents WHERE id = $1
                    """,
                    agent_id,
                )
                if not row:
                    return None
                normalized = user_id.strip().lower() if user_id else None
                if normalized and row["author_id"] == normalized:
                    return AGENT_PERMISSION_OWNER
                if normalized:
                    share = await conn.fetchval(
                        """
                        SELECT permission FROM agent_shares
                        WHERE agent_id = $1 AND LOWER(TRIM(shared_with_user_id)) = LOWER(TRIM($2))
                        """,
                        agent_id,
                        normalized,
                    )
                    if share is not None:
                        return normalize_permission(share)
                if row["is_public"]:
                    return AGENT_PERMISSION_VIEWER
                return None
        except Exception:
            logger.exception("Ошибка определения роли пользователя для агента")
            return None

    async def user_can_access_agent(self, agent_id: int, user_id: Optional[str] = None) -> bool:
        """Проверка доступа: публичный / автор / получатель шаринга."""
        try:
            async with await self.db_connection.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT is_public, LOWER(TRIM(author_id)) AS author_id
                    FROM agents WHERE id = $1
                    """,
                    agent_id,
                )
                if not row:
                    return False
                if row["is_public"]:
                    return True
                if not user_id:
                    return False
                normalized = user_id.strip().lower()
                if row["author_id"] == normalized:
                    return True
                shared = await conn.fetchval(
                    """
                    SELECT EXISTS(
                        SELECT 1 FROM agent_shares
                        WHERE agent_id = $1 AND LOWER(TRIM(shared_with_user_id)) = LOWER(TRIM($2))
                    )
                    """,
                    agent_id,
                    normalized,
                )
                return bool(shared)
        except Exception:
            logger.exception("Ошибка проверки доступа к агенту")
            return False

    async def share_agent(
        self, agent_id: int, owner_id: str, usernames: List[str], permission: str = AGENT_PERMISSION_VIEWER
    ) -> Tuple[List[str], List[str]]:
        """
        Расшарить агента с пользователями.

        Returns:
            (успешно добавленные user_id, пропущенные/ошибочные)
        """
        owner_id = owner_id.strip().lower() if owner_id else owner_id
        permission = normalize_permission(permission)
        if permission not in AGENT_SHARE_PERMISSIONS:
            permission = AGENT_PERMISSION_VIEWER
        added: List[str] = []
        skipped: List[str] = []
        try:
            async with await self.db_connection.acquire() as conn:
                author = await conn.fetchval(
                    """
                    SELECT LOWER(TRIM(author_id)) FROM agents WHERE id = $1
                    """,
                    agent_id,
                )
                if not author:
                    return ([], usernames)
                if author != owner_id:
                    logger.warning(f"Попытка шаринга чужого агента: user={owner_id}, agent={agent_id}")
                    return ([], usernames)
                for raw in usernames:
                    recipient = (raw or "").strip().lower()
                    if not recipient:
                        skipped.append(raw or "")
                        continue
                    if recipient == owner_id:
                        skipped.append(recipient)
                        continue
                    await conn.execute(
                        """
                        INSERT INTO agent_shares (agent_id, owner_id, shared_with_user_id, permission)
                        VALUES ($1, $2, $3, $4)
                        ON CONFLICT (agent_id, shared_with_user_id) DO UPDATE
                        SET permission = EXCLUDED.permission
                        """,
                        agent_id,
                        owner_id,
                        recipient,
                        permission,
                    )
                    added.append(recipient)
                logger.info(f"Агент {agent_id} расшарен с: {added}")
                return (added, skipped)
        except Exception:
            logger.exception("Ошибка шаринга агента")
            return ([], usernames)

    async def unshare_agent(self, agent_id: int, actor_id: str, target_user_id: str) -> bool:
        """Снять шаринг. Может владелец или сам получатель (выйти)."""
        actor_id = actor_id.strip().lower() if actor_id else actor_id
        target_user_id = target_user_id.strip().lower() if target_user_id else target_user_id
        try:
            async with await self.db_connection.acquire() as conn:
                author = await conn.fetchval(
                    """
                    SELECT LOWER(TRIM(author_id)) FROM agents WHERE id = $1
                    """,
                    agent_id,
                )
                if not author:
                    return False
                if actor_id != author and actor_id != target_user_id:
                    logger.warning(
                        f"Нет прав на unshare: actor={actor_id}, target={target_user_id}, agent={agent_id}"
                    )
                    return False
                result = await conn.execute(
                    """
                    DELETE FROM agent_shares
                    WHERE agent_id = $1 AND LOWER(TRIM(shared_with_user_id)) = LOWER(TRIM($2))
                    """,
                    agent_id,
                    target_user_id,
                )
                return result != "DELETE 0"
        except Exception:
            logger.exception("Ошибка снятия шаринга")
            return False

    async def list_agent_shares(self, agent_id: int, owner_id: str) -> List[AgentShare]:
        """Список получателей шаринга (только автор)."""
        owner_id = owner_id.strip().lower() if owner_id else owner_id
        try:
            async with await self.db_connection.acquire() as conn:
                author = await conn.fetchval(
                    """
                    SELECT LOWER(TRIM(author_id)) FROM agents WHERE id = $1
                    """,
                    agent_id,
                )
                if not author or author != owner_id:
                    return []
                rows = await conn.fetch(
                    """
                    SELECT id, agent_id, owner_id, shared_with_user_id, permission, created_at
                    FROM agent_shares
                    WHERE agent_id = $1
                    ORDER BY created_at DESC
                    """,
                    agent_id,
                )
                return [
                    AgentShare(
                        id=row["id"],
                        agent_id=row["agent_id"],
                        owner_id=row["owner_id"],
                        shared_with_user_id=row["shared_with_user_id"],
                        permission=row["permission"] or "use",
                        created_at=row["created_at"],
                    )
                    for row in rows
                ]
        except Exception:
            logger.exception("Ошибка получения списка шарингов")
            return []

    async def get_shared_with_me(
        self, user_id: str, limit: int = 20, offset: int = 0
    ) -> Tuple[List[AgentWithTags], int]:
        """Агенты, расшаренные текущему пользователю."""
        user_id = user_id.strip().lower() if user_id else user_id
        try:
            async with await self.db_connection.acquire() as conn:
                total = await conn.fetchval(
                    """
                    SELECT COUNT(*)
                    FROM agent_shares
                    WHERE LOWER(TRIM(shared_with_user_id)) = LOWER(TRIM($1))
                    """,
                    user_id,
                )
                rows = await conn.fetch(
                    """
                    SELECT agent_id
                    FROM agent_shares
                    WHERE LOWER(TRIM(shared_with_user_id)) = LOWER(TRIM($1))
                    ORDER BY created_at DESC
                    LIMIT $2 OFFSET $3
                    """,
                    user_id,
                    limit,
                    offset,
                )
            agents: List[AgentWithTags] = []
            for row in rows:
                agent = await self.get_agent(row["agent_id"], user_id)
                if agent:
                    agent.is_shared_with_me = True
                    agents.append(agent)
            return (agents, int(total or 0))
        except Exception:
            logger.exception("Ошибка получения shared-with-me")
            return ([], 0)
