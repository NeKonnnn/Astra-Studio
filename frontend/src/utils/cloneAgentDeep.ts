import { authFetch, getApiUrl } from '../config/api';

export interface CloneAgentDeepResult {
  agentId: number;
  clonedAgents: number;
  clonedAgentIds: number[];
  message?: string;
}

function formatCloneError(detail: unknown, fallback: string): string {
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    return (
      detail
        .map((d) =>
          d && typeof d === 'object' && 'msg' in d
            ? String((d as { msg: unknown }).msg)
            : String(d),
        )
        .join('; ') || fallback
    );
  }
  if (detail && typeof detail === 'object' && 'message' in detail) {
    const msg = (detail as { message?: unknown }).message;
    if (typeof msg === 'string' && msg.trim()) return msg;
  }
  return fallback;
}

/** Полный deep-clone через POST /api/agents/{id}/duplicate (родитель + цепочка + субагенты). */
export async function cloneAgentDeep(
  agentId: number,
  token: string | null | undefined,
): Promise<CloneAgentDeepResult> {
  if (!token) {
    throw new Error('Для клонирования агента нужно войти в систему');
  }
  if (!Number.isFinite(agentId) || agentId <= 0) {
    throw new Error('Некорректный id агента');
  }

  const resp = await authFetch(getApiUrl(`/api/agents/${agentId}/duplicate`), {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}` },
  });

  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(
      formatCloneError(err?.detail ?? err, resp.statusText || 'Не удалось клонировать'),
    );
  }

  const result = await resp.json().catch(() => ({}));
  const newId = Number(result?.agent_id);
  if (!Number.isFinite(newId) || newId <= 0) {
    throw new Error('Сервер не вернул id склонированного агента');
  }

  const rawIds = Array.isArray(result?.cloned_agent_ids) ? result.cloned_agent_ids : [];
  const clonedAgentIds = rawIds
    .map((x: unknown) => Number(x))
    .filter((x: number) => Number.isFinite(x) && x > 0);

  if (!clonedAgentIds.includes(newId)) {
    clonedAgentIds.unshift(newId);
  }

  return {
    agentId: newId,
    clonedAgents: Number(result?.cloned_agents) || clonedAgentIds.length || 1,
    clonedAgentIds,
    message: typeof result?.message === 'string' ? result.message : undefined,
  };
}

/** id цепочки и субагентов из config карточки. */
export function extractLinkedAgentIdsFromConfig(config: unknown): number[] {
  if (!config || typeof config !== 'object') return [];
  const cfg = config as Record<string, unknown>;
  const out: number[] = [];
  const seen = new Set<number>();

  const push = (raw: unknown) => {
    if (!Array.isArray(raw)) return;
    for (const item of raw) {
      const id = Number(item);
      if (!Number.isFinite(id) || id <= 0 || seen.has(id)) continue;
      seen.add(id);
      out.push(id);
    }
  };

  push(cfg.agent_ids);
  const sub = cfg.subagents;
  if (sub && typeof sub === 'object') {
    push((sub as { agent_ids?: unknown }).agent_ids);
  }
  return out;
}
