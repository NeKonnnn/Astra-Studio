import type { McpToolCallRecord, McpToolExecution } from '../types';

function findRunningIndex(executions: McpToolExecution[], qualifiedName: string): number {
  for (let i = executions.length - 1; i >= 0; i -= 1) {
    if (executions[i].qualified_name === qualifiedName && executions[i].status === 'running') {
      return i;
    }
  }
  return -1;
}

function pickScope(
  target: McpToolExecution,
  record: McpToolCallRecord,
): Partial<McpToolExecution> {
  return {
    agent_id: record.agent_id ?? target.agent_id,
    agent_name: record.agent_name ?? target.agent_name,
    chain_step_index: record.chain_step_index ?? target.chain_step_index,
    chain_step_total: record.chain_step_total ?? target.chain_step_total,
    parent_call_id: record.parent_call_id ?? target.parent_call_id,
    depth: record.depth ?? target.depth,
    activity_kind: record.activity_kind ?? target.activity_kind,
  };
}

/** Объединяет mcp_tool_start / mcp_tool_end в одну карточку на инструмент. */
export function mergeMcpToolCalls(records: McpToolCallRecord[]): McpToolExecution[] {
  const executions: McpToolExecution[] = [];
  const indexByCallId = new Map<string, number>();

  records.forEach((record, index) => {
    let callId = record.call_id;
    if (!callId && record.type === 'mcp_tool_end') {
      const runningIdx = findRunningIndex(executions, record.qualified_name);
      if (runningIdx >= 0) {
        callId = executions[runningIdx].call_id;
      }
    }
    if (!callId) {
      callId = `${record.qualified_name}:${record.timestamp ?? index}`;
    }

    if (record.type === 'mcp_tool_start') {
      const existingIdx = indexByCallId.get(callId);
      if (existingIdx != null) {
        const prev = executions[existingIdx];
        executions[existingIdx] = {
          ...prev,
          status: prev.status === 'completed' || prev.status === 'failed' ? prev.status : 'running',
          arguments: record.arguments ?? prev.arguments,
          started_at: record.timestamp ?? prev.started_at,
          ...pickScope(prev, record),
        };
        return;
      }

      indexByCallId.set(callId, executions.length);
      executions.push({
        call_id: callId,
        server_id: record.server_id,
        tool: record.tool,
        qualified_name: record.qualified_name,
        model: record.model,
        status: 'running',
        arguments: record.arguments,
        started_at: record.timestamp,
        agent_id: record.agent_id,
        agent_name: record.agent_name,
        chain_step_index: record.chain_step_index,
        chain_step_total: record.chain_step_total,
        parent_call_id: record.parent_call_id,
        depth: record.depth,
        activity_kind: record.activity_kind,
      });
      return;
    }

    const existingIdx = indexByCallId.get(callId);
    const base: McpToolExecution =
      existingIdx != null
        ? executions[existingIdx]
        : {
            call_id: callId,
            server_id: record.server_id,
            tool: record.tool,
            qualified_name: record.qualified_name,
            model: record.model,
            status: 'running',
          };

    const merged: McpToolExecution = {
      ...base,
      status: record.success === false ? 'failed' : 'completed',
      arguments: record.arguments ?? base.arguments,
      result: record.result ?? record.result_preview ?? base.result,
      result_preview: record.result_preview ?? base.result_preview,
      error: record.error ?? base.error,
      duration_ms: record.duration_ms ?? base.duration_ms,
      download_urls: record.download_urls ?? base.download_urls,
      has_image: record.has_image ?? base.has_image,
      has_audio: record.has_audio ?? base.has_audio,
      has_resource: record.has_resource ?? base.has_resource,
      ended_at: record.timestamp ?? base.ended_at,
      document_search: record.document_search ?? base.document_search,
      ...pickScope(base, record),
    };

    if (existingIdx != null) {
      executions[existingIdx] = merged;
    } else {
      indexByCallId.set(callId, executions.length);
      executions.push(merged);
    }
  });

  return executions;
}

/** Дерево: дети с parent_call_id === родителя. */
export function buildMcpToolTree(records: McpToolCallRecord[]): McpToolExecution[] {
  const flat = mergeMcpToolCalls(records);
  const byId = new Map<string, McpToolExecution>();
  for (const ex of flat) {
    byId.set(ex.call_id, { ...ex, children: [] });
  }
  const roots: McpToolExecution[] = [];
  Array.from(byId.values()).forEach((ex) => {
    const parentId = ex.parent_call_id || null;
    if (parentId && byId.has(parentId)) {
      const parent = byId.get(parentId)!;
      parent.children = [...(parent.children || []), ex];
    } else {
      roots.push(ex);
    }
  });
  return roots;
}

/** Tools корня шага цепочки (без вложенных под чужой parent). */
export function filterToolCallsForChainStep(
  records: McpToolCallRecord[] | undefined,
  stepIndex: number,
  agentId?: number | null,
): McpToolCallRecord[] {
  if (!records?.length) return [];
  const byStep = records.filter((r) => {
    if (r.chain_step_index == null) return false;
    return Number(r.chain_step_index) === stepIndex;
  });
  if (byStep.length) return byStep;
  if (agentId != null && Number.isFinite(agentId) && agentId > 0) {
    const byAgent = records.filter(
      (r) => r.chain_step_index == null && Number(r.agent_id) === Number(agentId),
    );
    if (byAgent.length) return byAgent;
  }
  return [];
}

/** Корневые вызовы (без parent), для обычного сообщения без цепочки. */
export function filterRootToolCalls(
  records: McpToolCallRecord[] | undefined,
): McpToolCallRecord[] {
  if (!records?.length) return [];
  const ids = new Set(records.map((r) => r.call_id).filter(Boolean) as string[]);
  return records.filter((r) => {
    const parent = r.parent_call_id;
    if (!parent) return true;
    // orphan parent — показываем на корне
    return !ids.has(parent);
  });
}
