import { getApiUrl, getAuthFetchHeaders } from '../config/api';
import type { Message } from '../contexts/AppContext';

/** Дефолт, если ConfigMap ещё не подтянулся. */
export const DEFAULT_MAX_CHAIN_AGENTS = 10;
export const DEFAULT_MAX_SUBAGENTS = 10;
export const DEFAULT_GRAPH_STEPS = 50;

export interface AgentChainStep {
  agentId: number;
  agentName: string;
  content: string;
  reasoning?: string;
  documentSearch?: Message['documentSearch'];
}

export interface AgentChainCurrent {
  agentId?: number | null;
  agentName: string;
  index: number;
  total: number;
  hideSequential?: boolean;
  isLast?: boolean;
}

export interface AgentChainConfig {
  maxAgents: number;
  maxAgentsCap: number;
  maxSubagents: number;
  maxSubagentsCap: number;
  graphSteps: number;
  defaultRecursionLimit: number;
  maxRecursionLimit: number;
}

let cachedConfig: AgentChainConfig | null = null;
let loadPromise: Promise<AgentChainConfig> | null = null;

function clampLimit(raw: unknown, fallback: number, lo: number, hi: number): number {
  const n = Number(raw);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(lo, Math.min(Math.trunc(n), hi));
}

export function getCachedChainConfig(): AgentChainConfig {
  return (
    cachedConfig || {
      maxAgents: DEFAULT_MAX_CHAIN_AGENTS,
      maxAgentsCap: 50,
      maxSubagents: DEFAULT_MAX_SUBAGENTS,
      maxSubagentsCap: 50,
      graphSteps: DEFAULT_GRAPH_STEPS,
      defaultRecursionLimit: DEFAULT_GRAPH_STEPS,
      maxRecursionLimit: 500,
    }
  );
}

export async function fetchAgentChainConfig(): Promise<AgentChainConfig> {
  if (cachedConfig) return cachedConfig;
  if (!loadPromise) {
    loadPromise = (async () => {
      try {
        const resp = await fetch(getApiUrl('/api/agents/chain-config'), {
          headers: getAuthFetchHeaders(),
        });
        const data = resp.ok ? await resp.json() : {};
        cachedConfig = {
          maxAgents: clampLimit(data.max_agents ?? data.maxAgents, DEFAULT_MAX_CHAIN_AGENTS, 1, 50),
          maxAgentsCap: clampLimit(data.max_agents_cap ?? data.maxAgentsCap, 50, 1, 50),
          maxSubagents: clampLimit(
            data.max_subagents ?? data.maxSubagents,
            DEFAULT_MAX_SUBAGENTS,
            1,
            50,
          ),
          maxSubagentsCap: clampLimit(data.max_subagents_cap ?? data.maxSubagentsCap, 50, 1, 50),
          graphSteps: clampLimit(data.graph_steps ?? data.graphSteps, DEFAULT_GRAPH_STEPS, 1, 500),
          defaultRecursionLimit: clampLimit(
            data.default_recursion_limit ?? data.graph_steps ?? data.graphSteps,
            DEFAULT_GRAPH_STEPS,
            1,
            500,
          ),
          maxRecursionLimit: clampLimit(
            data.max_recursion_limit ?? data.maxRecursionLimit,
            500,
            1,
            500,
          ),
        };
        return cachedConfig;
      } catch {
        cachedConfig = {
          maxAgents: DEFAULT_MAX_CHAIN_AGENTS,
          maxAgentsCap: 50,
          maxSubagents: DEFAULT_MAX_SUBAGENTS,
          maxSubagentsCap: 50,
          graphSteps: DEFAULT_GRAPH_STEPS,
          defaultRecursionLimit: DEFAULT_GRAPH_STEPS,
          maxRecursionLimit: 500,
        };
        return cachedConfig;
      }
    })();
  }
  return loadPromise;
}

export function parseAgentIds(
  raw: unknown,
  excludeId?: number | null,
  maxAgents: number = getCachedChainConfig().maxAgents,
): number[] {
  if (!Array.isArray(raw)) return [];
  const seen = new Set<number>();
  if (typeof excludeId === 'number' && Number.isFinite(excludeId)) seen.add(excludeId);
  const limit = Math.max(1, maxAgents);
  const out: number[] = [];
  for (const item of raw) {
    const id = Number(item);
    if (!Number.isFinite(id) || id <= 0 || seen.has(id)) continue;
    seen.add(id);
    out.push(id);
    if (out.length >= limit) break;
  }
  return out;
}

export function mapChainStepsFromMeta(raw: unknown): AgentChainStep[] | undefined {
  if (!Array.isArray(raw) || raw.length === 0) return undefined;
  const steps = raw
    .filter((s): s is Record<string, unknown> => Boolean(s) && typeof s === 'object')
    .map((s) => ({
      agentId: Number(s.agent_id ?? s.agentId ?? 0),
      agentName: String(s.agent_name ?? s.agentName ?? 'Агент'),
      content: String(s.content ?? ''),
      reasoning: String(s.reasoning ?? s.reasoning_content ?? '').trim() || undefined,
      documentSearch: mapDocumentSearchTrace(s.document_search ?? s.documentSearch),
    }));
  return steps.length ? steps : undefined;
}

function mapDocumentSearchTrace(raw: unknown): Message['documentSearch'] | undefined {
  if (!raw || typeof raw !== 'object') return undefined;
  const ds = raw as Record<string, unknown>;
  const hitsRaw = Array.isArray(ds.hits) ? ds.hits : [];
  const hits = hitsRaw
    .filter((h): h is Record<string, unknown> => Boolean(h) && typeof h === 'object')
    .map((h) => ({
      file: String(h.file ?? ''),
      anchor: String(h.anchor ?? ''),
      relevance: Number(h.relevance ?? 0),
      content: String(h.content ?? ''),
      chunkIndex: Number(h.chunkIndex ?? h.chunk_index ?? 0),
      documentId: Number(h.documentId ?? h.document_id ?? 0),
      store: String(h.store ?? ''),
    }));
  const sourceFiles = Array.isArray(ds.sourceFiles)
    ? ds.sourceFiles.map(String)
    : Array.isArray(ds.source_files)
      ? ds.source_files.map(String)
      : Array.from(new Set(hits.map((h) => h.file).filter(Boolean)));
  if (!hits.length && !sourceFiles.length && !String(ds.query ?? '').trim()) {
    return undefined;
  }
  return {
    query: String(ds.query ?? ''),
    sourceFiles,
    hits,
  };
}

/** @deprecated используйте getCachedChainConfig().maxAgents */
export const MAX_CHAIN_AGENTS = DEFAULT_MAX_CHAIN_AGENTS;

/** Заголовок шага в теле сообщения: `**▸ Имя**` (см. backend chain_step_header). */
const CHAIN_HEADER_RE = /\*\*[▸▶]\s*(.+?)\*\*/g;

export interface ChainRenderSegment {
  agentId: number;
  agentName: string;
  content: string;
  reasoning?: string;
  documentSearch?: Message['documentSearch'];
  /** Текущий шаг ещё стримится. */
  running?: boolean;
}

function splitBodyByChainHeaders(body: string): Array<{ agentName: string; content: string }> {
  const text = String(body || '');
  if (!text.trim()) return [];
  const re = new RegExp(CHAIN_HEADER_RE.source, 'g');
  const matches: Array<{ name: string; start: number; end: number }> = [];
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    matches.push({
      name: String(m[1] || '').trim() || 'Агент',
      start: m.index,
      end: m.index + m[0].length,
    });
  }
  if (!matches.length) return [];
  const out: Array<{ agentName: string; content: string }> = [];
  for (let i = 0; i < matches.length; i++) {
    const contentStart = matches[i].end;
    const contentEnd = i + 1 < matches.length ? matches[i + 1].start : text.length;
    out.push({
      agentName: matches[i].name,
      content: text.slice(contentStart, contentEnd).replace(/^\n+/, ''),
    });
  }
  return out;
}

/**
 * Сегменты UI для Mixture-of-Agents: либо из metadata.chain_steps / chainCurrentAgent,
 * либо разбор заголовков `**▸ Name**` в теле (стрим / старые сообщения).
 * null — обычный одиночный ответ без цепочки.
 */
export function resolveChainRenderSegments(
  visibleBody: string,
  chainSteps: Message['chainSteps'] | undefined,
  chainCurrent: Message['chainCurrentAgent'] | undefined,
  isStreaming?: boolean,
  hideSequentialOutputs?: boolean,
): ChainRenderSegment[] | null {
  const hide =
    Boolean(hideSequentialOutputs) ||
    Boolean(chainCurrent?.hideSequential);
  const steps = Array.isArray(chainSteps) ? chainSteps : [];
  const streaming = Boolean(isStreaming);
  const hasCurrent = Boolean(chainCurrent?.agentName?.trim());

  if (!steps.length && !hasCurrent) {
    const parsed = splitBodyByChainHeaders(visibleBody);
    if (parsed.length < 2) return null;
    return parsed.map((p, i) => ({
      agentId: 0,
      agentName: p.agentName,
      content: p.content,
      running: streaming && i === parsed.length - 1,
    }));
  }

  // Скрывать промежуточные: только текущий (стрим) или последний завершённый шаг.
  if (hide) {
    if (streaming && hasCurrent) {
      return [
        {
          agentId: Number(chainCurrent?.agentId) || 0,
          agentName: String(chainCurrent?.agentName || 'Агент'),
          content: visibleBody,
          running: true,
        },
      ];
    }
    if (steps.length) {
      const last = steps[steps.length - 1];
      return [
        {
          agentId: Number(last.agentId) || 0,
          agentName: String(last.agentName || 'Агент'),
          content: last.content || '',
          reasoning: last.reasoning,
          documentSearch: last.documentSearch,
          running: false,
        },
      ];
    }
    return null;
  }

  // Есть сохранённые шаги (после complete или из history).
  if (steps.length && !(streaming && hasCurrent && !chainCurrent?.isLast)) {
    // Завершённая цепочка или стрим последнего шага уже с метаданными всех шагов.
    if (!streaming || chainCurrent?.isLast || !hasCurrent) {
      return steps.map((s, i) => ({
        agentId: Number(s.agentId) || 0,
        agentName: String(s.agentName || 'Агент'),
        content: s.content || '',
        reasoning: s.reasoning,
        documentSearch: s.documentSearch,
        running: Boolean(streaming && hasCurrent && i === steps.length - 1),
      }));
    }
  }

  // Стрим: завершённые шаги + текущий running.
  if (streaming && hasCurrent) {
    const completed = steps.map((s) => ({
      agentId: Number(s.agentId) || 0,
      agentName: String(s.agentName || 'Агент'),
      content: s.content || '',
      reasoning: s.reasoning,
      documentSearch: s.documentSearch,
      running: false,
    }));

    // Тело содержит prefix предыдущих + header текущего — вытащим хвост текущего шага.
    const parsed = splitBodyByChainHeaders(visibleBody);
    let currentContent = visibleBody;
    if (parsed.length) {
      const byName = [...parsed].reverse().find(
        (p) => p.agentName.toLowerCase() === String(chainCurrent?.agentName || '').trim().toLowerCase(),
      );
      currentContent = (byName || parsed[parsed.length - 1]).content;
    } else if (completed.length) {
      // Без заголовков (не должно быть при hide=false) — весь body как текущий.
      currentContent = visibleBody;
    }

    const curId = Number(chainCurrent?.agentId) || 0;
    const already = completed.some(
      (s) =>
        (curId > 0 && s.agentId === curId) ||
        s.agentName.toLowerCase() === String(chainCurrent?.agentName || '').trim().toLowerCase(),
    );
    if (!already) {
      completed.push({
        agentId: curId,
        agentName: String(chainCurrent?.agentName || 'Агент'),
        content: currentContent,
        reasoning: undefined,
        documentSearch: undefined,
        running: true,
      });
    } else {
      const last = completed[completed.length - 1];
      last.content = currentContent || last.content;
      last.running = true;
    }
    return completed.length ? completed : null;
  }

  if (steps.length) {
    return steps.map((s) => ({
      agentId: Number(s.agentId) || 0,
      agentName: String(s.agentName || 'Агент'),
      content: s.content || '',
      reasoning: s.reasoning,
      documentSearch: s.documentSearch,
      running: false,
    }));
  }

  return null;
}
