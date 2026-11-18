import type { McpToolCallRecord } from '../types';

function optInt(raw: unknown): number | null | undefined {
  if (raw == null || raw === '') return undefined;
  const n = Number(raw);
  return Number.isFinite(n) ? Math.trunc(n) : undefined;
}

function optStr(raw: unknown): string | undefined {
  if (raw == null) return undefined;
  const s = String(raw).trim();
  return s || undefined;
}

/** Нормализует событие MCP с backend/socket к формату UI. */
export function normalizeMcpToolCallRecord(raw: Record<string, unknown>): McpToolCallRecord {
  const eventType = String(raw.type || '');
  return {
    type: eventType === 'mcp_tool_end' ? 'mcp_tool_end' : 'mcp_tool_start',
    server_id: String(raw.server_id || ''),
    tool: String(raw.tool || ''),
    qualified_name: String(raw.qualified_name || raw.tool || ''),
    call_id: raw.call_id ? String(raw.call_id) : undefined,
    arguments:
      raw.arguments && typeof raw.arguments === 'object'
        ? (raw.arguments as Record<string, unknown>)
        : undefined,
    success: typeof raw.success === 'boolean' ? raw.success : undefined,
    duration_ms: typeof raw.duration_ms === 'number' ? raw.duration_ms : undefined,
    error: raw.error != null ? String(raw.error) : undefined,
    model: raw.model ? String(raw.model) : undefined,
    timestamp: typeof raw.timestamp === 'number' ? raw.timestamp : undefined,
    result_preview: raw.result_preview ? String(raw.result_preview) : undefined,
    result: raw.result ? String(raw.result) : undefined,
    has_image: Boolean(raw.has_image),
    has_audio: Boolean(raw.has_audio),
    has_resource: Boolean(raw.has_resource),
    download_urls: Array.isArray(raw.download_urls)
      ? raw.download_urls.map((item) => {
          const row = item as Record<string, unknown>;
          return {
            url: String(row.url || ''),
            label: row.label ? String(row.label) : undefined,
            mime: row.mime ? String(row.mime) : undefined,
          };
        })
      : undefined,
    document_search:
      raw.document_search && typeof raw.document_search === 'object'
        ? (raw.document_search as McpToolCallRecord['document_search'])
        : undefined,
    agent_id: optInt(raw.agent_id ?? raw.agentId),
    agent_name: optStr(raw.agent_name ?? raw.agentName),
    chain_step_index: optInt(raw.chain_step_index ?? raw.chainStepIndex),
    chain_step_total: optInt(raw.chain_step_total ?? raw.chainStepTotal),
    parent_call_id: optStr(raw.parent_call_id ?? raw.parentCallId) ?? null,
    depth: optInt(raw.depth) ?? undefined,
    activity_kind: optStr(raw.activity_kind ?? raw.activityKind),
    llm_iteration: optInt(raw.llm_iteration ?? raw.llmIteration),
  };
}

export function normalizeMcpToolCallList(raw: unknown): McpToolCallRecord[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((item) => item && typeof item === 'object')
    .map((item) => normalizeMcpToolCallRecord(item as Record<string, unknown>));
}
