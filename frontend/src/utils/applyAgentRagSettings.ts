import { authFetch, getApiUrl, getAuthFetchHeaders } from '../config/api';

type RagPromptSaveResult = { ok: true } | { ok: false; message: string };

async function saveEntityRagSystemPrompt(opts: {
  scope: 'agent' | 'project';
  entityId: number | string;
  entityName: string;
  instructions: string;
  logLabel: string;
}): Promise<RagPromptSaveResult> {
  const body: Record<string, unknown> = {
    scope: opts.scope,
    entity_name: opts.entityName,
    rag_system_prompt: opts.instructions.trim(),
  };
  if (opts.scope === 'agent') {
    body.agent_id = opts.entityId;
  } else {
    body.project_id = opts.entityId;
  }

  const response = await authFetch(getApiUrl('/api/rag/settings'), {
    method: 'PUT',
    headers: getAuthFetchHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(body),
  });

  if (response.ok) {
    console.debug(
      `[RAG] Инструкция сохранена как rag_system_prompt для ${opts.logLabel} «${opts.entityName}» (id=${opts.entityId})`,
    );
    return { ok: true };
  }

  const details = await response.text().catch(() => '');
  return {
    ok: false,
    message: details || `Ошибка сохранения RAG-инструкции: ${response.status}`,
  };
}

/**
 * Сохраняет текст «Инструкции» агента как rag_system_prompt для этого агента (per-entity).
 */
export async function saveAgentRagSystemPromptFromInstructions(opts: {
  agentId: number | string;
  agentName: string;
  instructions: string;
}): Promise<RagPromptSaveResult> {
  return saveEntityRagSystemPrompt({
    scope: 'agent',
    entityId: opts.agentId,
    entityName: opts.agentName,
    instructions: opts.instructions,
    logLabel: 'агента',
  });
}

/**
 * Сохраняет текст «Инструкции проекта» как rag_system_prompt для этого проекта (per-entity).
 */
export async function saveProjectRagSystemPromptFromInstructions(opts: {
  projectId: string;
  projectName: string;
  instructions: string;
}): Promise<RagPromptSaveResult> {
  return saveEntityRagSystemPrompt({
    scope: 'project',
    entityId: opts.projectId,
    entityName: opts.projectName,
    instructions: opts.instructions,
    logLabel: 'проекта',
  });
}
