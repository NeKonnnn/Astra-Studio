"""
schemas.py - Pydantic-модели запросов/ответов
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    message: str
    streaming: bool = True
    tool_ids: Optional[List[str]] = None
    mcp_tool_ids: Optional[List[str]] = None
    model: Optional[str] = None
    conversation_id: Optional[str] = None
    message_id: Optional[str] = None


class MessageFeedbackRequest(BaseModel):
    """Лайк / дизлайк ответа ассистента."""

    rating: Optional[Literal["like", "dislike"]] = None
    tags: Optional[List[str]] = Field(default_factory=list)
    comment: Optional[str] = None
    multi_llm_slot_index: Optional[int] = None


class ContextBreakdownRequest(BaseModel):
    model_path: Optional[str] = None
    agent_id: Optional[int] = None
    use_kb_rag: bool = False
    tool_ids: Optional[List[str]] = None
    project_instructions: Optional[str] = None


class FollowUpHistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class FollowUpSuggestionsRequest(BaseModel):
    messages: List[FollowUpHistoryMessage]
    model_path: Optional[str] = None


class ModelSettings(BaseModel):
    context_size: int = 2048
    output_tokens: int = 512
    temperature: float = 0.7
    top_p: float = 0.95
    repeat_penalty: float = 1.05
    top_k: int = 40
    min_p: float = 0.05
    frequency_penalty: float = 0.0
    presence_penalty: float = 0.0
    use_gpu: bool = False
    streaming: bool = True


class VoiceSettings(BaseModel):
    voice_id: str = "ru"
    speech_rate: float = 1.0
    voice_speaker: str = "baya"


class MemorySettings(BaseModel):
    max_messages: int = 20
    include_system_prompts: bool = True
    clear_on_restart: bool = False


class ModelLoadRequest(BaseModel):
    model_path: str


class ModelLoadResponse(BaseModel):
    message: str
    success: bool


class VoiceSynthesizeRequest(BaseModel):
    text: str
    voice_id: str = "ru"
    voice_speaker: str = "baya"
    speech_rate: float = 1.0


class TranscriptionSettings(BaseModel):
    engine: str = "whisperx"
    language: str = "ru"
    auto_detect: bool = True


class YouTubeTranscribeRequest(BaseModel):
    url: str


class DocumentQueryRequest(BaseModel):
    query: str


class RAGSettings(BaseModel):
    scope: Optional[str] = None
    project_id: Optional[str] = None
    agent_id: Optional[int] = None
    knowledge_base_id: Optional[int] = None
    entity_name: Optional[str] = None
    strategy: Optional[str] = None  # auto | hybrid | vector | lexical | raw_cosine | graph
    rag_memory_strategy: Optional[str] = None
    agentic_rag_enabled: Optional[bool] = None
    agentic_max_iterations: Optional[int] = None
    # Препроцесс запроса перед поиском в SVC-RAG (доп. вызовы LLM при включении)
    rag_query_fix_typos: Optional[bool] = None
    rag_multi_query_enabled: Optional[bool] = None
    rag_hyde_enabled: Optional[bool] = None
    # Сколько чанков подмешивать в контекст чата (1–64, по умолчанию 8)
    rag_chat_top_k: Optional[int] = None
    # Стратегия нарезки новых документов на чанки (UI-настройка)
    rag_chunking_strategy: Optional[str] = None  # hierarchical | fixed | markdown | separators | semantic
    # Размер перекрытия соседних чанков при индексации
    rag_chunk_overlap: Optional[int] = None
    # Целевой размер чанка при индексации (символы)
    rag_chunk_size: Optional[int] = None
    # Порог похожести retrieval [0..1]
    rag_similarity_threshold: Optional[float] = None
    # Включить cross-encoder reranking
    rag_reranking_enabled: Optional[bool] = None
    # Сколько чанков оставить после reranking (Top-N)
    rag_rerank_top_n: Optional[int] = None
    # Пользовательский системный промпт для ответа с RAG-контекстом
    rag_system_prompt: Optional[str] = None
    # Пути выбранных моделей (local/<папка>, phoenix/<id>, corsur/<id>)
    rag_embedding_model_path: Optional[str] = None
    rag_reranker_model_path: Optional[str] = None


class RagModelSelectRequest(BaseModel):
    model_type: str  # embedding | reranker
    model_path: str
    scope: Optional[str] = None
    project_id: Optional[str] = None
    agent_id: Optional[int] = None
    knowledge_base_id: Optional[int] = None
    entity_name: Optional[str] = None


class AgentModeRequest(BaseModel):
    mode: str  # "agent" | "direct"


class ModelComparisonModelsRequest(BaseModel):
    models: List[str]


class AgentStatusResponse(BaseModel):
    is_initialized: bool
    mode: str
    available_agents: int
    orchestrator_active: bool
