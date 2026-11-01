import { McpApiError, extractServerIdFromPath, formatMcpApiError } from '@/mcp/errors';
import {
  MCP_SELECTION_CHANGED_EVENT,
  copyMcpToolIds,
  getGlobalMcpToolIds,
  getMcpToolIdsForChat,
  isMcpServerEnabledForChat,
  mcpServerToolId,
  parseMcpServerIdFromToolId,
  projectMcpChatKey,
  setGlobalMcpToolIds,
  setMcpToolIdsForChat,
  toggleMcpServerForChat,
} from '@/mcp/selectionStorage';
import { formatMcpAggregateLabel, isMcpPlatformHealthy } from '@/mcp/utils/statusLabel';
import { normalizeMcpToolCallList, normalizeMcpToolCallRecord } from '@/mcp/utils/normalizeToolCall';
import { mergeMcpToolCalls } from '@/mcp/utils/mergeToolCalls';
import type { McpPlatformStatus, McpToolCallRecord } from '@/mcp/types';

const rec = (overrides: Partial<McpToolCallRecord>): McpToolCallRecord =>
  ({ type: 'mcp_tool_start', server_id: 'docs', tool: 'search', qualified_name: 'mcp_docs_search', ...overrides } as McpToolCallRecord);

describe('mergeMcpToolCalls', () => {
  it('joins start and end of one call into a single card', () => {
    const merged = mergeMcpToolCalls([
      rec({ call_id: 'c1', arguments: { q: 'налоги' }, timestamp: 100 }),
      rec({ type: 'mcp_tool_end', call_id: 'c1', success: true, result: 'готово', duration_ms: 40, timestamp: 140 }),
    ]);

    expect(merged).toHaveLength(1);
    expect(merged[0]).toMatchObject({ call_id: 'c1', status: 'completed', arguments: { q: 'налоги' }, result: 'готово', duration_ms: 40, started_at: 100, ended_at: 140 });
  });

  it('marks unsuccessful calls as failed and keeps the error', () => {
    const [call] = mergeMcpToolCalls([rec({ call_id: 'c1' }), rec({ type: 'mcp_tool_end', call_id: 'c1', success: false, error: 'таймаут' })]);

    expect(call).toMatchObject({ status: 'failed', error: 'таймаут' });
  });

  it('treats a missing success flag as success', () => {
    const [call] = mergeMcpToolCalls([rec({ call_id: 'c1' }), rec({ type: 'mcp_tool_end', call_id: 'c1' })]);

    expect(call.status).toBe('completed');
  });

  it('pairs an end without call_id with the latest running call of the same tool', () => {
    const merged = mergeMcpToolCalls([
      rec({ call_id: 'a' }),
      rec({ call_id: 'b' }),
      rec({ type: 'mcp_tool_end', success: true }),
    ]);

    expect(merged.map((m) => [m.call_id, m.status])).toEqual([['a', 'running'], ['b', 'completed']]);
  });

  it('does not pair an id-less end with a different tool', () => {
    const merged = mergeMcpToolCalls([rec({ call_id: 'a' }), rec({ type: 'mcp_tool_end', qualified_name: 'mcp_wiki_page', tool: 'page' })]);

    expect(merged).toHaveLength(2);
    expect(merged[0].status).toBe('running');
  });

  it('creates a card for an end that arrived without a start', () => {
    const [call] = mergeMcpToolCalls([rec({ type: 'mcp_tool_end', call_id: 'late', success: true, result_preview: 'превью' })]);

    expect(call).toMatchObject({ call_id: 'late', status: 'completed', result: 'превью', result_preview: 'превью' });
  });

  it('falls back to the preview for the result and keeps the earlier arguments', () => {
    const [call] = mergeMcpToolCalls([
      rec({ call_id: 'c1', arguments: { q: 1 } }),
      rec({ type: 'mcp_tool_end', call_id: 'c1', result_preview: 'p' }),
    ]);

    expect(call.result).toBe('p');
    expect(call.arguments).toEqual({ q: 1 });
  });

  it('does not reopen a finished call when a duplicate start arrives', () => {
    const merged = mergeMcpToolCalls([
      rec({ call_id: 'c1' }),
      rec({ type: 'mcp_tool_end', call_id: 'c1', success: true }),
      rec({ call_id: 'c1', arguments: { retry: true }, timestamp: 500 }),
    ]);

    expect(merged).toHaveLength(1);
    expect(merged[0]).toMatchObject({ status: 'completed', arguments: { retry: true }, started_at: 500 });
  });

  it('synthesises a call id from the tool and timestamp when none is given', () => {
    const merged = mergeMcpToolCalls([rec({ timestamp: 111 }), rec({ timestamp: 222 })]);

    expect(merged.map((m) => m.call_id)).toEqual(['mcp_docs_search:111', 'mcp_docs_search:222']);
  });

  it('keeps document search and attachment flags from the end event', () => {
    const [call] = mergeMcpToolCalls([
      rec({ call_id: 'c1' }),
      rec({ type: 'mcp_tool_end', call_id: 'c1', has_image: true, download_urls: [{ url: '/f.pptx' }], document_search: { hits: [] } as never }),
    ]);

    expect(call.has_image).toBe(true);
    expect(call.download_urls).toEqual([{ url: '/f.pptx' }]);
    expect(call.document_search).toEqual({ hits: [] });
  });

  it('returns an empty list for no events', () => {
    expect(mergeMcpToolCalls([])).toEqual([]);
  });
});

describe('normalizeMcpToolCallRecord', () => {
  it('coerces types and defaults', () => {
    const record = normalizeMcpToolCallRecord({ type: 'mcp_tool_end', tool: 'search', server_id: 7, call_id: 5, success: 'yes', duration_ms: '12', timestamp: 99 });

    expect(record).toMatchObject({
      type: 'mcp_tool_end',
      server_id: '7',
      tool: 'search',
      qualified_name: 'search',
      call_id: '5',
      success: undefined,
      duration_ms: undefined,
      timestamp: 99,
      has_image: false,
      has_audio: false,
      has_resource: false,
    });
  });

  it('treats every unknown event type as a start', () => {
    expect(normalizeMcpToolCallRecord({ type: 'whatever' }).type).toBe('mcp_tool_start');
    expect(normalizeMcpToolCallRecord({}).type).toBe('mcp_tool_start');
  });

  it('keeps only object arguments and the document search payload', () => {
    expect(normalizeMcpToolCallRecord({ arguments: { a: 1 } }).arguments).toEqual({ a: 1 });
    expect(normalizeMcpToolCallRecord({ arguments: 'raw' }).arguments).toBeUndefined();
    expect(normalizeMcpToolCallRecord({ document_search: 'x' }).document_search).toBeUndefined();
    expect(normalizeMcpToolCallRecord({ document_search: { files: [] } }).document_search).toEqual({ files: [] });
  });

  it('normalises download links', () => {
    const record = normalizeMcpToolCallRecord({ download_urls: [{ url: '/a', label: 'A', mime: 'x/y' }, { label: '' }, {}] });

    expect(record.download_urls).toEqual([
      { url: '/a', label: 'A', mime: 'x/y' },
      { url: '', label: undefined, mime: undefined },
      { url: '', label: undefined, mime: undefined },
    ]);
  });

  it('turns an explicit error value into text but not a missing one', () => {
    expect(normalizeMcpToolCallRecord({ error: 0 }).error).toBe('0');
    expect(normalizeMcpToolCallRecord({ error: null }).error).toBeUndefined();
  });

  it('drops falsy results (an empty result is not preserved)', () => {
    expect(normalizeMcpToolCallRecord({ result: '' }).result).toBeUndefined();
    expect(normalizeMcpToolCallRecord({ result: 'ok', result_preview: 'p' })).toMatchObject({ result: 'ok', result_preview: 'p' });
  });

  it('breaks on a null link entry (known issue)', () => {
    // download_urls: [null] → TypeError: нормализатор не защищён от нестроковых элементов.
    expect(() => normalizeMcpToolCallRecord({ download_urls: [null] })).toThrow(TypeError);
  });

  it('normalises lists defensively', () => {
    expect(normalizeMcpToolCallList(null)).toEqual([]);
    expect(normalizeMcpToolCallList('x')).toEqual([]);
    expect(normalizeMcpToolCallList([null, 5, 'x', { type: 'mcp_tool_end', tool: 't' }]).map((r) => r.type)).toEqual(['mcp_tool_end']);
  });
});

describe('MCP status labels', () => {
  const status = (value: Record<string, unknown>) => value as unknown as McpPlatformStatus;

  it('formats the aggregate for the modern shape', () => {
    expect(formatMcpAggregateLabel(status({ servers_connected: 2, servers_total: 3, tools_total: 14 }))).toBe('MCP: 2/3 серверов, 14 инструментов');
    expect(formatMcpAggregateLabel(status({}))).toBe('MCP: 0/0 серверов, 0 инструментов');
    expect(formatMcpAggregateLabel(status({ total_servers: 4, servers_connected: 1, tools: 2 }))).toBe('MCP: 1/4 серверов, 2 инструментов');
  });

  it('formats legacy shapes', () => {
    expect(formatMcpAggregateLabel(status({ servers: [{ connected: true }, { connected: false }], tools_total: 5 }))).toBe('MCP: 1/2 серверов, 5 инструментов');
    expect(formatMcpAggregateLabel(status({ servers: 6, tools: 1 }))).toBe('MCP: 6 серверов, 1 инструментов');
  });

  it('prefers explicit counters over the server list', () => {
    expect(formatMcpAggregateLabel(status({ servers: [{ connected: false }], servers_connected: 3, servers_total: 9 }))).toBe('MCP: 3/9 серверов, 0 инструментов');
  });

  it('decides health from the most specific field', () => {
    expect(isMcpPlatformHealthy(status({ initialized: true, enabled: false }))).toBe(true);
    expect(isMcpPlatformHealthy(status({ initialized: false, enabled: true }))).toBe(false);
    expect(isMcpPlatformHealthy(status({ enabled: true }))).toBe(true);
    expect(isMcpPlatformHealthy(status({ servers: [{ connected: false }, { connected: true }] }))).toBe(true);
    expect(isMcpPlatformHealthy(status({ servers: [{ connected: false }] }))).toBe(false);
    expect(isMcpPlatformHealthy(status({ servers_connected: 2 }))).toBe(true);
    expect(isMcpPlatformHealthy(status({}))).toBe(false);
  });
});

describe('MCP API errors', () => {
  it('builds a typed error', () => {
    const err = new McpApiError('boom', 503, 'docs');

    expect(err).toBeInstanceOf(Error);
    expect(err).toMatchObject({ name: 'McpApiError', message: 'boom', status: 503, serverId: 'docs' });
  });

  it.each([
    [401, 'любое', undefined, 'Требуется повторный вход в систему'],
    [403, '  MCP недоступен для вашей роли  ', undefined, 'MCP недоступен для вашей роли'],
    [403, '', undefined, 'Недостаточно прав для операции MCP'],
    [404, 'x', 'docs', 'MCP-сервер «docs» не найден в конфигурации'],
    [404, '', undefined, 'MCP-сервер не найден в конфигурации'],
    [503, 'x', 'docs', 'MCP-сервер «docs» недоступен'],
    [503, '', undefined, 'MCP-платформа недоступна'],
    [500, 'Internal', undefined, 'Internal'],
    [502, '', undefined, 'Ошибка MCP API (502)'],
  ])('maps %p / %p / %p', (status, detail, serverId, expected) => {
    expect(formatMcpApiError(status, detail, serverId)).toBe(expected);
  });

  it('extracts and decodes the server id from a path', () => {
    expect(extractServerIdFromPath('/api/mcp/servers/docs/tools')).toBe('docs');
    expect(extractServerIdFromPath('/api/mcp/servers/my%20server/status')).toBe('my server');
    expect(extractServerIdFromPath('/api/mcp/tools')).toBeUndefined();
    expect(extractServerIdFromPath('')).toBeUndefined();
  });

  it('throws on a malformed percent-escape in the path (known issue)', () => {
    expect(() => extractServerIdFromPath('/api/mcp/servers/%E0%A4%A/status')).toThrow(URIError);
  });
});

describe('MCP tool selection storage', () => {
  beforeEach(() => localStorage.clear());

  it('builds and parses tool ids', () => {
    expect(mcpServerToolId('docs')).toBe('server:mcp:docs');
    expect(parseMcpServerIdFromToolId('server:mcp:docs')).toBe('docs');
    expect(parseMcpServerIdFromToolId('mcp:docs')).toBe('docs');
    expect(parseMcpServerIdFromToolId('  docs ')).toBe('docs');
    expect(parseMcpServerIdFromToolId('')).toBeNull();
    expect(parseMcpServerIdFromToolId(undefined as unknown as string)).toBeNull();
    expect(projectMcpChatKey('p1')).toBe('project:p1');
  });

  it('keeps MCP off in a chat without an explicit choice', () => {
    expect(getMcpToolIdsForChat('c1')).toEqual([]);
    expect(getMcpToolIdsForChat('')).toEqual([]);
    expect(isMcpServerEnabledForChat('c1', 'docs')).toBe(false);
  });

  it('stores the selection per chat and announces the change', () => {
    const listener = jest.fn();
    window.addEventListener(MCP_SELECTION_CHANGED_EVENT, listener);

    setMcpToolIdsForChat('c1', ['server:mcp:docs']);
    window.removeEventListener(MCP_SELECTION_CHANGED_EVENT, listener);

    expect(getMcpToolIdsForChat('c1')).toEqual(['server:mcp:docs']);
    expect(getMcpToolIdsForChat('c2')).toEqual([]);
    expect(isMcpServerEnabledForChat('c1', 'docs')).toBe(true);
    expect((listener.mock.calls[0][0] as CustomEvent).detail).toEqual({ chatId: 'c1' });
  });

  it('ignores writes without a chat id', () => {
    setMcpToolIdsForChat('', ['x']);

    expect(localStorage.length).toBe(0);
  });

  it('toggles servers without duplicates', () => {
    expect(toggleMcpServerForChat('c1', 'docs', true)).toEqual(['server:mcp:docs']);
    expect(toggleMcpServerForChat('c1', 'docs', true)).toEqual(['server:mcp:docs']);
    expect(toggleMcpServerForChat('c1', 'wiki', true)).toEqual(['server:mcp:docs', 'server:mcp:wiki']);
    expect(toggleMcpServerForChat('c1', 'docs', false)).toEqual(['server:mcp:wiki']);
    expect(toggleMcpServerForChat('c1', 'ghost', false)).toEqual(['server:mcp:wiki']);
  });

  it('copies a selection into a new chat only when there is one', () => {
    copyMcpToolIds('src', 'dst');
    expect(localStorage.getItem('chat:dst:mcp_tool_ids')).toBeNull();

    setMcpToolIdsForChat('src', ['server:mcp:docs']);
    copyMcpToolIds('src', 'dst');
    expect(getMcpToolIdsForChat('dst')).toEqual(['server:mcp:docs']);
  });

  it('survives damaged storage', () => {
    localStorage.setItem('chat:c1:mcp_tool_ids', '{broken');
    localStorage.setItem('global:mcp_tool_ids', '"string"');

    expect(getMcpToolIdsForChat('c1')).toEqual([]);
    expect(getGlobalMcpToolIds()).toEqual([]);
  });

  it('filters non-string entries and has a separate global selection', () => {
    localStorage.setItem('chat:c1:mcp_tool_ids', JSON.stringify(['a', 5, null, 'b']));
    setGlobalMcpToolIds(['server:mcp:global']);

    expect(getMcpToolIdsForChat('c1')).toEqual(['a', 'b']);
    expect(getGlobalMcpToolIds()).toEqual(['server:mcp:global']);
  });
});
