/** Шаги цепочки агентов в сообщении чата: текущий агент и ответы всех шагов. */
import type { Message } from '../../contexts/AppContext';

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
  if (!hits.length && !sourceFiles.length) {
    return { query: String(ds.query ?? '') };
  }
  return {
    query: String(ds.query ?? ''),
    sourceFiles,
    hits,
  };
}
