import json
import logging
import math
import re
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from app.text_sanitize import strip_null_bytes

if TYPE_CHECKING:
    from app.database.connection import PostgreSQLConnection

logger = logging.getLogger(__name__)


def _tokenize(text: str) -> List[str]:
    return re.findall(r"\b\w+\b", (text or "").lower())


def _jaccard(tokens_a: List[str], tokens_b: List[str]) -> float:
    a = set(tokens_a)
    b = set(tokens_b)
    if not a and not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b) or 1
    return inter / union


class GraphRepository:
    """Граф связей чанков для разных RAG-хранилищ."""

    def __init__(self, db: "PostgreSQLConnection"):
        self.db = db

    async def create_tables(self):
        async with await self.db.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS rag_graph_nodes (
                    id BIGSERIAL PRIMARY KEY,
                    store_type VARCHAR(32) NOT NULL,
                    document_id INTEGER NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    metadata JSONB DEFAULT '{}'::jsonb,
                    created_at TIMESTAMP DEFAULT NOW(),
                    UNIQUE(store_type, document_id, chunk_index)
                )
                """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS rag_graph_edges (
                    id BIGSERIAL PRIMARY KEY,
                    store_type VARCHAR(32) NOT NULL,
                    from_node_id BIGINT NOT NULL REFERENCES rag_graph_nodes(id) ON DELETE CASCADE,
                    to_node_id BIGINT NOT NULL REFERENCES rag_graph_nodes(id) ON DELETE CASCADE,
                    edge_type VARCHAR(32) NOT NULL,
                    weight DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                    metadata JSONB DEFAULT '{}'::jsonb,
                    created_at TIMESTAMP DEFAULT NOW(),
                    UNIQUE(store_type, from_node_id, to_node_id, edge_type)
                )
                """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_rag_graph_nodes_store_doc ON rag_graph_nodes(store_type, document_id)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_rag_graph_edges_store_from ON rag_graph_edges(store_type, from_node_id)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_rag_graph_edges_store_to ON rag_graph_edges(store_type, to_node_id)"
            )

    async def delete_document_graph(self, store_type: str, document_id: int) -> None:
        async with await self.db.acquire() as conn:
            await conn.execute(
                "DELETE FROM rag_graph_nodes WHERE store_type = $1 AND document_id = $2",
                store_type,
                document_id,
            )

    async def rebuild_document_graph(
        self, store_type: str, document_id: int, chunks: List[Tuple[int, str]]
    ) -> None:
        """Полная пересборка графа по документу.

        chunks: [(chunk_index, content), ...]
        """
        if not chunks:
            await self.delete_document_graph(store_type, document_id)
            return

        async with await self.db.acquire() as conn:
            # Всё в одной транзакции: сбой посреди (повтор chunk_index, обрыв
            # соединения) откатывает и удаление, и частичную вставку — старый граф
            # документа остаётся целым (BUG-RAG-06).
            async with conn.transaction():
                await self._rebuild_in_tx(conn, store_type, document_id, chunks)

    async def _rebuild_in_tx(
        self,
        conn: Any,
        store_type: str,
        document_id: int,
        chunks: List[Tuple[int, str]],
    ) -> None:
        await conn.execute(
            "DELETE FROM rag_graph_nodes WHERE store_type = $1 AND document_id = $2",
            store_type,
            document_id,
        )
        node_ids: Dict[int, int] = {}
        for chunk_index, content in chunks:
            safe = strip_null_bytes(content or "")
            row = await conn.fetchrow(
                """
                INSERT INTO rag_graph_nodes (store_type, document_id, chunk_index, content, metadata)
                VALUES ($1, $2, $3, $4, $5::jsonb)
                RETURNING id
                """,
                store_type,
                document_id,
                chunk_index,
                safe,
                json.dumps({"chunk_index": chunk_index}),
            )
            if row:
                node_ids[chunk_index] = int(row["id"])

        ordered = sorted(chunks, key=lambda x: x[0])
        tokenized = {
            idx: _tokenize(strip_null_bytes(content or ""))
            for idx, content in ordered
        }

        # adjacency edges (sequential chunks)
        for i in range(len(ordered) - 1):
            a_idx = ordered[i][0]
            b_idx = ordered[i + 1][0]
            a_id = node_ids.get(a_idx)
            b_id = node_ids.get(b_idx)
            if not a_id or not b_id:
                continue
            await self._upsert_edge(
                conn, store_type, a_id, b_id, "adjacent", 1.0, {"distance": 1}
            )
            await self._upsert_edge(
                conn, store_type, b_id, a_id, "adjacent", 1.0, {"distance": 1}
            )

        # semantic edges — ограничиваем расстояние вместо числа пар,
        # чтобы равномерно покрывать весь документ независимо от его длины
        max_distance = 30
        edge_count = 0
        max_edges = 8000
        for i in range(len(ordered)):
            if edge_count >= max_edges:
                break
            a_idx = ordered[i][0]
            for j in range(i + 1, min(i + max_distance + 1, len(ordered))):
                if edge_count >= max_edges:
                    break
                b_idx = ordered[j][0]
                score = _jaccard(tokenized.get(a_idx, []), tokenized.get(b_idx, []))
                if score < 0.18:
                    continue
                distance = abs(a_idx - b_idx)
                weight = max(0.05, score * (1.0 / (1.0 + math.log1p(distance))))
                a_id = node_ids.get(a_idx)
                b_id = node_ids.get(b_idx)
                if not a_id or not b_id:
                    continue
                meta = {"distance": distance, "jaccard": score}
                await self._upsert_edge(
                    conn, store_type, a_id, b_id, "semantic", weight, meta
                )
                await self._upsert_edge(
                    conn, store_type, b_id, a_id, "semantic", weight, meta
                )
                edge_count += 1

    async def expand_neighbors(
        self,
        store_type: str,
        document_id: Optional[int],
        seed_chunk_indexes: List[int],
        max_hops: int = 2,
        max_nodes: int = 40,
        seed_doc_chunk_pairs: Optional[List[Tuple[int, int]]] = None,
    ) -> Dict[Tuple[int, int], float]:
        """Возвращает карту (document_id, chunk_index) -> graph_score (0..1+).

        seed_doc_chunk_pairs: точные пары (doc_id, chunk_idx) для поиска seed-узлов без
        коллизий chunk_index между документами. Требуется при document_id=None.
        """
        if not seed_chunk_indexes:
            return {}

        async with await self.db.acquire() as conn:
            if document_id is not None:
                rows = await conn.fetch(
                    """
                    SELECT id, document_id, chunk_index
                    FROM rag_graph_nodes
                    WHERE store_type = $1 AND document_id = $2 AND chunk_index = ANY($3::int[])
                    """,
                    store_type,
                    document_id,
                    seed_chunk_indexes,
                )
            elif seed_doc_chunk_pairs:
                # Точный поиск по (doc_id, chunk_idx) — без коллизий chunk_index разных документов
                doc_ids = list({p[0] for p in seed_doc_chunk_pairs})
                chunk_idxs = list({p[1] for p in seed_doc_chunk_pairs})
                candidate_rows = await conn.fetch(
                    """
                    SELECT id, document_id, chunk_index
                    FROM rag_graph_nodes
                    WHERE store_type = $1
                      AND document_id = ANY($2::int[])
                      AND chunk_index = ANY($3::int[])
                    """,
                    store_type,
                    doc_ids,
                    chunk_idxs,
                )
                valid_pairs = set(seed_doc_chunk_pairs)
                rows = [
                    r
                    for r in candidate_rows
                    if (int(r["document_id"]), int(r["chunk_index"])) in valid_pairs
                ]
            else:
                # Без document_id и без точных пар seed-узлы нельзя сопоставить
                # документу: поиск только по chunk_index смешивал бы чужие документы
                # (BUG-RAG-06). Старый «fallback» убран — возвращаем пусто.
                logger.warning(
                    "graph.expand_neighbors(%s): нужен document_id или seed_doc_chunk_pairs — "
                    "расширение пропущено",
                    store_type,
                )
                return {}

            frontier: List[Tuple[int, float, int]] = []
            scores_by_node: Dict[int, float] = {}
            # node_id -> (doc_id, chunk_idx)
            chunk_by_node: Dict[int, Tuple[int, int]] = {}
            for r in rows:
                node_id = int(r["id"])
                doc_id = int(r["document_id"])
                chunk_idx = int(r["chunk_index"])
                chunk_by_node[node_id] = (doc_id, chunk_idx)
                scores_by_node[node_id] = 1.0
                frontier.append((node_id, 1.0, 0))

            cursor = 0
            while cursor < len(frontier):
                node_id, current_score, hop = frontier[cursor]
                cursor += 1
                if hop >= max_hops:
                    continue
                edges = await conn.fetch(
                    """
                    SELECT to_node_id, weight
                    FROM rag_graph_edges
                    WHERE store_type = $1 AND from_node_id = $2
                    ORDER BY weight DESC
                    LIMIT 24
                    """,
                    store_type,
                    node_id,
                )
                for edge in edges:
                    to_id = int(edge["to_node_id"])
                    w = float(edge["weight"])
                    propagated = current_score * (0.82 ** (hop + 1)) * w
                    if propagated < 0.03:
                        continue
                    prev = scores_by_node.get(to_id, 0.0)
                    if propagated > prev:
                        scores_by_node[to_id] = propagated
                        frontier.append((to_id, propagated, hop + 1))
                    if len(scores_by_node) >= max_nodes:
                        break
                if len(scores_by_node) >= max_nodes:
                    break

            # Загружаем (doc_id, chunk_idx) для всех найденных соседних узлов
            if scores_by_node:
                missing = [
                    nid for nid in scores_by_node.keys() if nid not in chunk_by_node
                ]
                if missing:
                    mrows = await conn.fetch(
                        """
                        SELECT id, document_id, chunk_index
                        FROM rag_graph_nodes
                        WHERE id = ANY($1::bigint[])
                        """,
                        missing,
                    )
                    for r in mrows:
                        chunk_by_node[int(r["id"])] = (
                            int(r["document_id"]),
                            int(r["chunk_index"]),
                        )

            # Итоговый словарь: (doc_id, chunk_idx) -> max_score
            chunk_scores: Dict[Tuple[int, int], float] = {}
            for node_id, score in scores_by_node.items():
                key = chunk_by_node.get(node_id)
                if key is None:
                    continue
                if score > chunk_scores.get(key, 0.0):
                    chunk_scores[key] = score
            return chunk_scores

    async def _upsert_edge(
        self,
        conn: Any,
        store_type: str,
        from_node_id: int,
        to_node_id: int,
        edge_type: str,
        weight: float,
        metadata: Dict[str, float],
    ) -> None:
        await conn.execute(
            """
            INSERT INTO rag_graph_edges (store_type, from_node_id, to_node_id, edge_type, weight, metadata)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb)
            ON CONFLICT (store_type, from_node_id, to_node_id, edge_type)
            DO UPDATE SET weight = EXCLUDED.weight, metadata = EXCLUDED.metadata
            """,
            store_type,
            from_node_id,
            to_node_id,
            edge_type,
            float(weight),
            json.dumps(metadata or {}),
        )
