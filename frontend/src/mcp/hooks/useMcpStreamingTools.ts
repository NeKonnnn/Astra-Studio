import { useCallback, useEffect, useMemo, useState } from 'react';
import type { McpToolCallRecord } from '../types';

export const MCP_TOOL_ACTIVITY_EVENT = 'astrachatMcpToolActivity';

interface McpToolActivityDetail {
  record: McpToolCallRecord;
  phase: 'start' | 'end';
  /** Чат, в котором идёт генерация с этим вызовом. */
  chatId?: string | null;
}

type ActiveTool = { record: McpToolCallRecord; chatId: string | null };

const toolKey = (r: McpToolCallRecord) => r.call_id || `${r.qualified_name}:${r.model || ''}`;

/**
 * In-flight MCP tools во время генерации (live indicator F-6).
 * С chatId - только вызовы этого чата: иначе субагенты удалённого или
 * соседнего чата «работают» в открытом. Без chatId - все вызовы.
 */
export function useMcpStreamingTools(chatId?: string | null) {
  const [active, setActive] = useState<ActiveTool[]>([]);

  useEffect(() => {
    const onActivity = (e: Event) => {
      const detail = (e as CustomEvent<McpToolActivityDetail>).detail;
      if (!detail?.record) return;
      const { record, phase } = detail;
      const owner = detail.chatId || null;
      const key = toolKey(record);
      setActive((prev) => {
        if (phase === 'start') {
          if (prev.some((x) => x.chatId === owner && toolKey(x.record) === key)) return prev;
          return [...prev, { record, chatId: owner }];
        }
        return prev.filter((x) => !(x.chatId === owner && toolKey(x.record) === key));
      });
    };
    // detail.chatId - снять вызовы одного чата, без него - все.
    const onClear = (e: Event) => {
      const cid = (e as CustomEvent<{ chatId?: string | null } | null>).detail?.chatId;
      setActive((prev) => (cid ? prev.filter((x) => x.chatId !== cid) : []));
    };
    window.addEventListener(MCP_TOOL_ACTIVITY_EVENT, onActivity);
    window.addEventListener('astrachatMcpToolActivityClear', onClear);
    return () => {
      window.removeEventListener(MCP_TOOL_ACTIVITY_EVENT, onActivity);
      window.removeEventListener('astrachatMcpToolActivityClear', onClear);
    };
  }, []);

  const activeMcpTools = useMemo(
    () =>
      active
        .filter((x) => chatId === undefined || x.chatId === (chatId || null))
        .map((x) => x.record),
    [active, chatId],
  );

  const clear = useCallback(() => setActive([]), []);

  return { activeMcpTools, clearActiveMcpTools: clear };
}
