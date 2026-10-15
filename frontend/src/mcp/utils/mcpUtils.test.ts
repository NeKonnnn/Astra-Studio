import type { McpPlatformStatus, McpToolCallRecord } from '../types';
import { mergeMcpToolCalls } from './mergeToolCalls';
import { normalizeMcpToolCallList, normalizeMcpToolCallRecord } from './normalizeToolCall';
import { formatMcpAggregateLabel, isMcpPlatformHealthy } from './statusLabel';

const start = (over: Partial<McpToolCallRecord> = {}): McpToolCallRecord => ({
  type: 'mcp_tool_start',
  server_id: 'srv',
  tool: 'search',
  qualified_name: 'srv.search',
  ...over,
});

const end = (over: Partial<McpToolCallRecord> = {}): McpToolCallRecord => ({
  type: 'mcp_tool_end',
  server_id: 'srv',
  tool: 'search',
  qualified_name: 'srv.search',
  ...over,
});

describe('mergeMcpToolCalls', () => {
  it('пустой список — пустой результат', () => {
    expect(mergeMcpToolCalls([])).toEqual([]);
  });

  it('start без end — карточка в статусе running', () => {
    const [exec] = mergeMcpToolCalls([start({ call_id: 'c1', arguments: { q: 'кот' }, timestamp: 10 })]);
    expect(exec).toMatchObject({
      call_id: 'c1',
      status: 'running',
      arguments: { q: 'кот' },
      started_at: 10,
    });
  });

  it('start + end по call_id сливаются в одну карточку', () => {
    const merged = mergeMcpToolCalls([
      start({ call_id: 'c1', timestamp: 1 }),
      end({ call_id: 'c1', success: true, result: 'ok', duration_ms: 50, timestamp: 2 }),
    ]);
    expect(merged).toHaveLength(1);
    expect(merged[0]).toMatchObject({
      status: 'completed',
      result: 'ok',
      duration_ms: 50,
      started_at: 1,
      ended_at: 2,
    });
  });

  it('success=false → failed, error сохраняется', () => {
    const [exec] = mergeMcpToolCalls([
      start({ call_id: 'c1' }),
      end({ call_id: 'c1', success: false, error: 'boom' }),
    ]);
    expect(exec.status).toBe('failed');
    expect(exec.error).toBe('boom');
  });

  it('end без явного success считается завершённым', () => {
    const [exec] = mergeMcpToolCalls([start({ call_id: 'c1' }), end({ call_id: 'c1' })]);
    expect(exec.status).toBe('completed');
  });

  it('end без call_id привязывается к последнему running с тем же именем', () => {
    const merged = mergeMcpToolCalls([
      start({ call_id: 'c1' }),
      start({ call_id: 'c2' }),
      end({ success: true }),
    ]);
    expect(merged).toHaveLength(2);
    expect(merged[0].status).toBe('running');
    expect(merged[1].status).toBe('completed');
  });

  it('end без пары создаёт самостоятельную карточку', () => {
    const merged = mergeMcpToolCalls([end({ call_id: 'orphan', success: true, result_preview: 'r' })]);
    expect(merged).toHaveLength(1);
    expect(merged[0]).toMatchObject({ call_id: 'orphan', status: 'completed', result: 'r' });
  });

  it('повторный start не откатывает завершённый статус', () => {
    const [exec] = mergeMcpToolCalls([
      start({ call_id: 'c1' }),
      end({ call_id: 'c1', success: true }),
      start({ call_id: 'c1', arguments: { late: true } }),
    ]);
    expect(exec.status).toBe('completed');
    expect(exec.arguments).toEqual({ late: true });
  });

  it('разные инструменты не смешиваются', () => {
    const merged = mergeMcpToolCalls([
      start({ call_id: 'a', qualified_name: 'srv.one', tool: 'one' }),
      start({ call_id: 'b', qualified_name: 'srv.two', tool: 'two' }),
      end({ call_id: 'b', qualified_name: 'srv.two', tool: 'two', success: true }),
    ]);
    expect(merged.map((m) => [m.qualified_name, m.status])).toEqual([
      ['srv.one', 'running'],
      ['srv.two', 'completed'],
    ]);
  });

  it('без call_id генерирует ключ из имени и timestamp', () => {
    const [exec] = mergeMcpToolCalls([start({ timestamp: 77 })]);
    expect(exec.call_id).toBe('srv.search:77');
  });

  it('результат берёт из result, затем из result_preview', () => {
    const [a] = mergeMcpToolCalls([end({ call_id: 'a', result: 'full', result_preview: 'short' })]);
    const [b] = mergeMcpToolCalls([end({ call_id: 'b', result_preview: 'short' })]);
    expect(a.result).toBe('full');
    expect(b.result).toBe('short');
  });

  it('флаги вложений и ссылки на скачивание переносятся', () => {
    const [exec] = mergeMcpToolCalls([
      start({ call_id: 'c1' }),
      end({
        call_id: 'c1',
        has_image: true,
        download_urls: [{ url: 'http://x/f.pdf', label: 'f' }],
      }),
    ]);
    expect(exec.has_image).toBe(true);
    expect(exec.download_urls).toEqual([{ url: 'http://x/f.pdf', label: 'f' }]);
  });

  it('не мутирует входные записи', () => {
    const records = [start({ call_id: 'c1' }), end({ call_id: 'c1', success: true })];
    const snapshot = JSON.parse(JSON.stringify(records));
    mergeMcpToolCalls(records);
    expect(records).toEqual(snapshot);
  });
});

describe('normalizeMcpToolCallRecord', () => {
  it('приводит типы и подставляет значения по умолчанию', () => {
    expect(normalizeMcpToolCallRecord({})).toMatchObject({
      type: 'mcp_tool_start',
      server_id: '',
      tool: '',
      qualified_name: '',
      has_image: false,
      has_audio: false,
      has_resource: false,
    });
  });

  it('любой тип, кроме mcp_tool_end, превращается в start', () => {
    expect(normalizeMcpToolCallRecord({ type: 'whatever' }).type).toBe('mcp_tool_start');
    expect(normalizeMcpToolCallRecord({ type: 'mcp_tool_end' }).type).toBe('mcp_tool_end');
  });

  it('qualified_name по умолчанию равен tool', () => {
    expect(normalizeMcpToolCallRecord({ tool: 't' }).qualified_name).toBe('t');
  });

  it('call_id приводится к строке, пустой становится undefined', () => {
    expect(normalizeMcpToolCallRecord({ call_id: 42 }).call_id).toBe('42');
    expect(normalizeMcpToolCallRecord({ call_id: '' }).call_id).toBeUndefined();
  });

  it('числа и булевы значения принимаются только своих типов', () => {
    const r = normalizeMcpToolCallRecord({ success: 'true', duration_ms: '5', timestamp: 9 });
    expect(r.success).toBeUndefined();
    expect(r.duration_ms).toBeUndefined();
    expect(r.timestamp).toBe(9);
  });

  it('error: null остаётся пустым, значения приводятся к строке', () => {
    expect(normalizeMcpToolCallRecord({ error: null }).error).toBeUndefined();
    expect(normalizeMcpToolCallRecord({ error: 'x' }).error).toBe('x');
    expect(normalizeMcpToolCallRecord({ error: 0 }).error).toBe('0');
  });

  it('arguments — только объект', () => {
    expect(normalizeMcpToolCallRecord({ arguments: { a: 1 } }).arguments).toEqual({ a: 1 });
    expect(normalizeMcpToolCallRecord({ arguments: 'str' }).arguments).toBeUndefined();
  });

  it('download_urls нормализуются построчно', () => {
    const r = normalizeMcpToolCallRecord({
      download_urls: [{ url: 'http://x', label: 'L', mime: 'application/pdf' }, { url: 5 }, {}],
    });
    expect(r.download_urls).toEqual([
      { url: 'http://x', label: 'L', mime: 'application/pdf' },
      { url: '5', label: undefined, mime: undefined },
      { url: '', label: undefined, mime: undefined },
    ]);
  });

  it('download_urls не массив → undefined', () => {
    expect(normalizeMcpToolCallRecord({ download_urls: 'x' }).download_urls).toBeUndefined();
  });

  it('truthy-флаги вложений приводятся к boolean', () => {
    const r = normalizeMcpToolCallRecord({ has_image: 1, has_audio: 0, has_resource: 'yes' });
    expect([r.has_image, r.has_audio, r.has_resource]).toEqual([true, false, true]);
  });
});

describe('normalizeMcpToolCallList', () => {
  it.each([null, undefined, 'x', 5, {}])('не массив (%p) → []', (raw) => {
    expect(normalizeMcpToolCallList(raw)).toEqual([]);
  });

  it('отбрасывает не-объекты и нормализует остальное', () => {
    const list = normalizeMcpToolCallList([null, 'x', 1, { tool: 'a' }, { tool: 'b', type: 'mcp_tool_end' }]);
    expect(list.map((r) => [r.tool, r.type])).toEqual([
      ['a', 'mcp_tool_start'],
      ['b', 'mcp_tool_end'],
    ]);
  });
});

describe('formatMcpAggregateLabel', () => {
  it('новый формат со счётчиками', () => {
    const status: McpPlatformStatus = { servers_connected: 2, servers_total: 3, tools_total: 10 };
    expect(formatMcpAggregateLabel(status)).toBe('MCP: 2/3 серверов, 10 инструментов');
  });

  it('total_servers и tools — запасные источники', () => {
    expect(formatMcpAggregateLabel({ servers_connected: 1, total_servers: 4, tools: 7 })).toBe(
      'MCP: 1/4 серверов, 7 инструментов',
    );
  });

  it('пустой статус', () => {
    expect(formatMcpAggregateLabel({})).toBe('MCP: 0/0 серверов, 0 инструментов');
  });

  it('legacy: массив servers считается по connected', () => {
    const status = {
      servers: [
        { id: 'a', display_name: 'A', enabled: true, connected: true },
        { id: 'b', display_name: 'B', enabled: true, connected: false },
      ],
      tools: 3,
    } as McpPlatformStatus;
    expect(formatMcpAggregateLabel(status)).toBe('MCP: 1/2 серверов, 3 инструментов');
  });

  it('legacy: servers как число', () => {
    expect(formatMcpAggregateLabel({ servers: 5, tools: 2 } as unknown as McpPlatformStatus)).toBe(
      'MCP: 5 серверов, 2 инструментов',
    );
  });
});

describe('isMcpPlatformHealthy', () => {
  it('initialized имеет наивысший приоритет', () => {
    expect(isMcpPlatformHealthy({ initialized: true, enabled: false })).toBe(true);
    expect(isMcpPlatformHealthy({ initialized: false, enabled: true })).toBe(false);
  });

  it('затем enabled', () => {
    expect(isMcpPlatformHealthy({ enabled: true })).toBe(true);
    expect(isMcpPlatformHealthy({ enabled: false, servers_connected: 3 })).toBe(false);
  });

  it('legacy: хотя бы один connected сервер', () => {
    const connected = { servers: [{ id: 'a', display_name: 'A', enabled: true, connected: true }] };
    const none = { servers: [{ id: 'a', display_name: 'A', enabled: true, connected: false }] };
    expect(isMcpPlatformHealthy(connected as McpPlatformStatus)).toBe(true);
    expect(isMcpPlatformHealthy(none as McpPlatformStatus)).toBe(false);
  });

  it('иначе по servers_connected', () => {
    expect(isMcpPlatformHealthy({ servers_connected: 1 })).toBe(true);
    expect(isMcpPlatformHealthy({ servers_connected: 0 })).toBe(false);
    expect(isMcpPlatformHealthy({})).toBe(false);
  });
});
