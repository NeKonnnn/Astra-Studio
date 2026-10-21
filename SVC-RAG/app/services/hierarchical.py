# Иерархическая суммаризация и оптимизированный индекс (аналог backend document_summarizer)
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.database.models import DocumentVector

logger = logging.getLogger(__name__)

class DocumentSummarizer:
    """
    Многоуровневая суммаризация документа (Level 0/1/2).
    Level 0: оригинальные чанки; Level 1: промежуточные блоки; Level 2: финальное краткое содержание (опционально через LLM).
    """

    def __init__(
        self,
        llm_function: Optional[Callable[[str], Any]] = None,
        max_chunk_size: int = 1500,
        chunk_overlap: int = 200,
        intermediate_summary_chunks: int = 8,
    ):
        self.llm_function = llm_function
        self.max_chunk_size = max_chunk_size
        self.chunk_overlap = chunk_overlap
        self.intermediate_summary_chunks = intermediate_summary_chunks
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=max_chunk_size,
            chunk_overlap=chunk_overlap,
            length_function=len,
            separators=["\n\n", "\n", ". ", " ", ""],
        )

    def apply_chunk_params(
        self,
        chunk_size: Optional[int] = None,
        chunk_overlap: Optional[int] = None,
    ) -> Optional[tuple]:
        """Временно переопределить размер чанка для одной индексации."""
        if chunk_size is None and chunk_overlap is None:
            return None
        cs = (
            max(200, int(chunk_size)) if chunk_size is not None else self.max_chunk_size
        )
        co = (
            max(0, int(chunk_overlap))
            if chunk_overlap is not None
            else self.chunk_overlap
        )
        if co >= cs:
            co = max(0, cs // 4)
        backup = (self.max_chunk_size, self.chunk_overlap, self.text_splitter)
        self.max_chunk_size = cs
        self.chunk_overlap = co
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=cs,
            chunk_overlap=co,
            length_function=len,
            separators=["\n\n", "\n", ". ", " ", ""],
        )
        return backup

    def restore_chunk_params(self, backup: Optional[tuple]) -> None:
        if not backup:
            return
        self.max_chunk_size, self.chunk_overlap, self.text_splitter = backup

    async def create_hierarchical_summary_async(
        self,
        text: str,
        doc_name: str,
        create_full_summary: bool = True,
    ) -> Dict[str, Any]:
        chunks = self.text_splitter.split_text(text)
        if not chunks:
            chunks = [text] if text else [f"[Документ: {doc_name}]"]

        level_0_chunks = [
            {"content": chunk, "chunk_index": i, "level": 0, "doc_name": doc_name}
            for i, chunk in enumerate(chunks)
        ]

        level_1_summaries = []
        try:
            n = int(self.intermediate_summary_chunks)
        except (TypeError, ValueError):
            n = 0
        if n < 1:
            # 0/отрицательное (например, из env) давало range(..., 0) → ValueError.
            n = 8
        for i in range(0, len(level_0_chunks), n):
            batch = level_0_chunks[i : i + n]
            combined_text = "\n\n".join([c["content"] for c in batch])
            chunk_range = f"чанки {batch[0]['chunk_index']}-{batch[-1]['chunk_index']}"
            content = f"[РАЗДЕЛ ДОКУМЕНТА '{doc_name}' ({chunk_range})]\n\n{combined_text[:3000]}..."
            level_1_summaries.append(
                {
                    "content": content,
                    "summary_index": len(level_1_summaries),
                    "level": 1,
                    "chunk_range": (batch[0]["chunk_index"], batch[-1]["chunk_index"]),
                    "doc_name": doc_name,
                }
            )

        level_2_summary = ""
        if create_full_summary and self.llm_function:
            try:
                summary_text = "=== НАЧАЛО ДОКУМЕНТА ===\n"
                for chunk in level_0_chunks[:3]:
                    summary_text += chunk["content"] + "\n\n"
                if len(level_0_chunks) > 10:
                    summary_text += "\n=== ОСНОВНАЯ ЧАСТЬ ===\n"
                    step = max(1, len(level_0_chunks) // 5)
                    for i in range(3, len(level_0_chunks) - 3, step):
                        summary_text += level_0_chunks[i]["content"][:500] + "...\n\n"
                summary_text += "\n=== КОНЕЦ ДОКУМЕНТА ===\n"
                for chunk in level_0_chunks[-3:]:
                    summary_text += chunk["content"] + "\n\n"
                if len(summary_text) > 15000:
                    summary_text = summary_text[:15000] + "\n\n[...обрезано...]"

                prompt = f"""Создай структурированное краткое содержание следующего документа.
Включи:
1. Основную тему документа
2. Ключевые разделы и темы
3. Важные факты и данные
4. Выводы (если есть)

Документ "{doc_name}":

{summary_text}

Краткое содержание (на русском):"""

                fn = self.llm_function
                if hasattr(fn, "__call__"):
                    import asyncio

                    if asyncio.iscoroutinefunction(fn):
                        level_2_summary = await fn(prompt)
                    else:
                        level_2_summary = fn(prompt)
                else:
                    level_2_summary = ""
                if not (level_2_summary and level_2_summary.strip()):
                    level_2_summary = (
                        f"[КРАТКОЕ СОДЕРЖАНИЕ '{doc_name}']\n\n" + text[:2000] + "..."
                    )
            except Exception as e:
                logger.warning("Ошибка LLM суммаризации: %s", e)
                level_2_summary = (
                    f"[КРАТКОЕ СОДЕРЖАНИЕ '{doc_name}']\n\n" + text[:2000] + "..."
                )
        else:
            level_2_summary = (
                f"[ДОКУМЕНТ '{doc_name}' - {len(text)} символов, {len(chunks)} чанков]\n\n"
                + text[:2000]
            )

        return {
            "full_text": text,
            "level_0_chunks": level_0_chunks,
            "level_1_summaries": level_1_summaries,
            "level_2_summary": level_2_summary,
            "metadata": {
                "doc_name": doc_name,
                "total_chars": len(text),
                "total_chunks": len(level_0_chunks),
                "total_intermediate_summaries": len(level_1_summaries),
            },
        }

class OptimizedDocumentIndex:
    """
    Индекс с иерархией: индексация Level 2/1/0 и умный поиск (summary / detailed).
    """

    def __init__(self, rag_client: Any, vector_repo: Any, model: Optional[str] = None):
        self.rag_client = rag_client
        self.vector_repo = vector_repo
        # Имя модели этого пользователя: клиент уже выбран роутером,
        # но native-клиенту модель едет в теле запроса.
        self.model = model

    async def index_document_hierarchical_async(
        self,
        hierarchical_doc: Dict[str, Any],
        document_id: int,
    ) -> bool:
        doc_name = hierarchical_doc["metadata"]["doc_name"]
        logger.info("Индексирование документа '%s' с иерархией", doc_name)

        try:
            vectors_to_save: List[Dict[str, Any]] = []

            level_2_summary = hierarchical_doc["level_2_summary"]
            vectors_to_save.append(
                {
                    "content": level_2_summary,
                    "chunk_index": -1,
                    "metadata": {
                        "level": 2,
                        "doc_name": doc_name,
                        "type": "full_summary",
                        "source": doc_name,
                    },
                }
            )

            for summary in hierarchical_doc["level_1_summaries"]:
                vectors_to_save.append(
                    {
                        "content": summary["content"],
                        "chunk_index": -2 - summary["summary_index"],
                        "metadata": {
                            "level": 1,
                            "doc_name": doc_name,
                            "summary_index": summary["summary_index"],
                            "chunk_range": summary["chunk_range"],
                            "type": "intermediate_summary",
                            "source": doc_name,
                        },
                    }
                )

            level_0_chunks = hierarchical_doc["level_0_chunks"]
            for chunk in level_0_chunks:
                vectors_to_save.append(
                    {
                        "content": chunk["content"],
                        "chunk_index": chunk["chunk_index"],
                        "metadata": {
                            "level": 0,
                            "doc_name": doc_name,
                            "type": "detail_chunk",
                            "source": doc_name,
                        },
                    }
                )

            texts = [
                v["content"] if len(v["content"]) <= 10000 else v["content"][:10000]
                for v in vectors_to_save
            ]
            embeddings = await self.rag_client.embed(
                texts, model=self.model, kind="document"
            )
            if len(embeddings) != len(vectors_to_save):
                logger.error("Число эмбеддингов не совпадает с числом записей")
                return False

            document_vectors = [
                DocumentVector(
                    document_id=document_id,
                    chunk_index=vectors_to_save[i]["chunk_index"],
                    embedding=embeddings[i],
                    content=vectors_to_save[i]["content"],
                    metadata=vectors_to_save[i]["metadata"],
                )
                for i in range(len(vectors_to_save))
            ]
            saved = await self.vector_repo.create_vectors_batch(document_vectors)
            logger.info(
                "Иерархия: сохранено %s векторов для документа '%s'", saved, doc_name
            )
            return saved > 0
        except Exception as e:
            logger.error("Ошибка иерархической индексации '%s': %s", doc_name, e)
            # Пробрасываем исходную причину (503 embed, dim mismatch и т.п.),
            # иначе вверх уходит только «не сохранила вектора».
            raise RuntimeError(
                f"иерархическая индексация не сохранила вектора: {e}"
            ) from e

    async def smart_search_async(
        self,
        query: str,
        k: int = 12,
        search_strategy: str = "auto",
    ) -> List[Tuple[str, float, Optional[int], Optional[int]]]:
        """Умный поиск по иерархии. Возвращает (content, score, document_id, chunk_index)."""
        if search_strategy == "auto":
            query_lower = query.lower()
            summary_keywords = [
                "саммари",
                "summary",
                "краткое содержание",
                "обзор",
                "резюме",
                "о чем документ",
                "основная тема",
                "содержание документа",
            ]
            if any(kw in query_lower for kw in summary_keywords):
                search_strategy = "summary"
            else:
                search_strategy = "detailed"

        query_embedding = await self.rag_client.embed_single(
            query, model=self.model, kind="query"
        )
        limit = k * 3

        if search_strategy == "summary":
            pairs = await self.vector_repo.similarity_search(
                query_embedding, limit=limit
            )
            results_l2 = []
            results_l1 = []
            for v, sim in pairs:
                t = v.metadata.get("type", "")
                if t == "full_summary":
                    results_l2.append((v, sim))
                elif t == "intermediate_summary":
                    results_l1.append((v, sim))
            results_l2 = results_l2[: min(k, 3)]
            results_l1 = results_l1[:k]
            all_results = results_l2 + results_l1
            # Fallback: если нет summary-чанков — возвращаем детальные
            if not all_results:
                all_results = pairs
        else:
            # Детальный поиск: только level-0 чанки, без summary-обёрток
            pairs = await self.vector_repo.similarity_search(
                query_embedding, limit=limit
            )
            detail_only = [
                (v, s) for v, s in pairs if v.metadata.get("type", "") == "detail_chunk"
            ]
            all_results = detail_only if detail_only else pairs

        return [
            (v.content, float(score), v.document_id, v.chunk_index)
            for v, score in all_results[:k]
        ]