import { getApiUrl, getAuthFetchHeaders } from '../config/api';

/**
 * Черновик РАГ-настроек агента или проекта.
 *
 * В БД он уходит одним запросом при сохранении САМОЙ сущности — по кнопке
 * «Сохранить» в конструкторе агента или в модалке проекта. Панель настроек
 * ничего не пишет: иначе правки переживали бы отказ от сохранения агента, а
 * перечанковка стартовала бы раньше, чем пользователь закончил настраивать.
 */
export interface EntityRagDraft {
  strategy: string;
  agentic_rag_enabled: boolean;
  rag_query_fix_typos: boolean;
  rag_multi_query_enabled: boolean;
  rag_hyde_enabled: boolean;
  rag_chat_top_k: number;
  rag_chunking_strategy: string;
  rag_chunk_size: number;
  rag_chunk_overlap: number;
  rag_similarity_threshold: number;
  rag_reranking_enabled: boolean;
  rag_rerank_top_n: number;
  /** Путь выбранной модели. null — не меняли, сущность остаётся на текущей. */
  rag_embedding_model_path: string | null;
  rag_reranker_model_path: string | null;
}

export type EntityRagScope = 'agent' | 'project' | 'kb';

type SaveResult =
  /** reindexed — backend поставил перечанковку: включаем частый опрос. */
  | { ok: true; reindexed: boolean }
  | { ok: false; message: string };

/**
 * Записать черновик в настройки сущности.
 *
 * ```instructions``` уезжает как ```rag_system_prompt```: у агента и проекта это
 * один и тот же текст «Инструкции», второго поля в интерфейсе нет.
 * Перечанковку решает backend — он сравнивает, изменилось ли то, что лежит в
 * индексе (нарезка и эмбеддер), и ставит задачу сам.
 */
export async function saveEntityRagSettings(opts: {
  scope: EntityRagScope;
  entityId: number | string;
  entityName: string;
  instructions: string;
  draft?: EntityRagDraft | null;
}): Promise<SaveResult> {
  const body: Record<string, unknown> = {
    scope: opts.scope,
    entity_name: opts.entityName,
    rag_system_prompt: opts.instructions.trim(),
  };
  if (opts.scope === 'agent') {
    body.agent_id = Number(opts.entityId);
  } else if (opts.scope === 'kb') {
    body.knowledge_base_id = Number(opts.entityId);
  } else {
    body.project_id = String(opts.entityId);
  }

  const draft = opts.draft;
  if (draft) {
    body.strategy = draft.strategy;
    body.agentic_rag_enabled = draft.agentic_rag_enabled;
    body.rag_query_fix_typos = draft.rag_query_fix_typos;
    body.rag_multi_query_enabled = draft.rag_multi_query_enabled;
    body.rag_hyde_enabled = draft.rag_hyde_enabled;
    body.rag_chat_top_k = draft.rag_chat_top_k;
    body.rag_chunking_strategy = draft.rag_chunking_strategy;
    body.rag_chunk_size = draft.rag_chunk_size;
    body.rag_chunk_overlap = draft.rag_chunk_overlap;
    body.rag_similarity_threshold = draft.rag_similarity_threshold;
    body.rag_reranking_enabled = draft.rag_reranking_enabled;
    body.rag_rerank_top_n = draft.rag_rerank_top_n;
    // null — «не трогали»: не отправляем, чтобы не сбросить выбранную модель.
    if (draft.rag_embedding_model_path !== null) {
      body.rag_embedding_model_path = draft.rag_embedding_model_path;
    }
    if (draft.rag_reranker_model_path !== null) {
      body.rag_reranker_model_path = draft.rag_reranker_model_path;
    }
  }

  const response = await fetch(getApiUrl('/api/rag/settings'), {
    method: 'PUT',
    headers: getAuthFetchHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(body),
  });

  if (response.ok) {
    // Перечанковку решает backend, и он же сообщает о ней полем reindexed.
    // Без этого вызывающий не знает, что пора учащать опрос статуса, и плашка
    // всплывает только на следующем тике.
    const saved = await response.json().catch(() => ({}));
    const label =
      opts.scope === 'agent' ? 'агента' : opts.scope === 'kb' ? 'базы знаний' : 'проекта';
    console.debug(
      `[RAG] Настройки ${label} «${opts.entityName}» (id=${opts.entityId}) сохранены`,
    );
    return { ok: true, reindexed: Boolean(saved?.reindexed) };
  }

  if (response.status === 403) {
    return { ok: false, message: 'Недостаточно прав: настройки может менять владелец или редактор' };
  }
  const details = await response.text().catch(() => '');
  return { ok: false, message: details || `Ошибка сохранения настроек РАГ: ${response.status}` };
}
