import { act, renderHook, waitFor } from '@testing-library/react';
import { useChatInputMcpIndicators } from '@/mcp/hooks/useChatInputMcpIndicators';
import { MCP_TOOL_ACTIVITY_EVENT, useMcpStreamingTools } from '@/mcp/hooks/useMcpStreamingTools';
import { useChatMcpSelection } from '@/mcp/hooks/useChatMcpSelection';
import { MCP_SELECTION_CHANGED_EVENT, setMcpToolIdsForChat } from '@/mcp/selectionStorage';

const mockFetchServers = jest.fn();
const mockFetchStatus = jest.fn();

jest.mock('@/mcp/api', () => ({
  fetchMcpServers: (...a: unknown[]) => mockFetchServers(...a),
  fetchMcpStatus: (...a: unknown[]) => mockFetchStatus(...a),
}));

beforeEach(() => {
  localStorage.clear();
  mockFetchServers.mockReset();
  mockFetchStatus.mockReset();
});

describe('useChatInputMcpIndicators', () => {
  it('без chatId индикаторов нет', async () => {
    mockFetchServers.mockResolvedValue([]);
    mockFetchStatus.mockResolvedValue({ servers: [] });
    const { result } = renderHook(() => useChatInputMcpIndicators(null));
    await waitFor(() => expect(mockFetchServers).toHaveBeenCalled());
    expect(result.current).toEqual([]);
  });

  it('склеивает выбранные серверы с конфигом и статусом; имя берётся из конфига, затем статуса, затем id', async () => {
    setMcpToolIdsForChat('c1', ['server:mcp:a', 'mcp:b', 'c', 'server:mcp:']);
    mockFetchServers.mockResolvedValue([{ id: 'a', display_name: 'Сервер A' }]);
    mockFetchStatus.mockResolvedValue({
      servers: [
        { id: 'a', display_name: 'ignored', connected: true },
        { id: 'b', display_name: 'Сервер B', connected: false },
      ],
    });
    const { result } = renderHook(() => useChatInputMcpIndicators('c1'));
    await waitFor(() => expect(result.current.find((x) => x.id === 'a')?.connected).toBe(true));
    expect(result.current).toEqual([
      { id: 'a', display_name: 'Сервер A', connected: true },
      { id: 'b', display_name: 'Сервер B', connected: false },
      { id: 'c', display_name: 'c', connected: undefined },
    ]);
  });

  it('при ошибке загрузки метаданных остаются прежние данные, id служит именем', async () => {
    setMcpToolIdsForChat('c1', ['server:mcp:x']);
    mockFetchServers.mockRejectedValue(new Error('down'));
    mockFetchStatus.mockResolvedValue({});
    const { result } = renderHook(() => useChatInputMcpIndicators('c1'));
    await waitFor(() => expect(mockFetchServers).toHaveBeenCalled());
    expect(result.current).toEqual([{ id: 'x', display_name: 'x', connected: undefined }]);
  });

  it('реагирует на событие выбора своего чата и игнорирует чужой', async () => {
    mockFetchServers.mockResolvedValue([]);
    mockFetchStatus.mockResolvedValue({ servers: [] });
    const { result } = renderHook(() => useChatInputMcpIndicators('c1'));
    await waitFor(() => expect(mockFetchServers).toHaveBeenCalled());
    localStorage.setItem('chat:c1:mcp_tool_ids', JSON.stringify(['server:mcp:z']));
    act(() => {
      window.dispatchEvent(new CustomEvent(MCP_SELECTION_CHANGED_EVENT, { detail: { chatId: 'other' } }));
    });
    expect(result.current).toEqual([]);
    act(() => {
      window.dispatchEvent(new CustomEvent(MCP_SELECTION_CHANGED_EVENT, { detail: { chatId: 'c1' } }));
    });
    expect(result.current.map((x) => x.id)).toEqual(['z']);
    act(() => {
      localStorage.setItem('chat:c1:mcp_tool_ids', JSON.stringify([]));
      window.dispatchEvent(new CustomEvent(MCP_SELECTION_CHANGED_EVENT));
    });
    expect(result.current).toEqual([]);
  });
});

describe('useMcpStreamingTools', () => {
  const rec = (call_id: string, extra: Record<string, unknown> = {}) =>
    ({ call_id, qualified_name: `srv.${call_id}`, ...extra } as any);
  const fire = (record: any, phase: 'start' | 'end', chatId?: string | null) =>
    act(() => {
      window.dispatchEvent(new CustomEvent(MCP_TOOL_ACTIVITY_EVENT, { detail: { record, phase, chatId } }));
    });

  it('start добавляет, повторный start не дублирует, end убирает', () => {
    const { result } = renderHook(() => useMcpStreamingTools());
    fire(rec('1'), 'start', 'c1');
    fire(rec('1'), 'start', 'c1');
    fire(rec('2'), 'start', null);
    expect(result.current.activeMcpTools.map((r) => r.call_id)).toEqual(['1', '2']);
    fire(rec('1'), 'end', 'c1');
    expect(result.current.activeMcpTools.map((r) => r.call_id)).toEqual(['2']);
  });

  it('ключ без call_id строится из имени и модели', () => {
    const { result } = renderHook(() => useMcpStreamingTools());
    const r = { qualified_name: 'a.b', model: 'm' } as any;
    fire(r, 'start');
    fire(r, 'start');
    expect(result.current.activeMcpTools).toHaveLength(1);
    fire({ qualified_name: 'a.b' }, 'start');
    expect(result.current.activeMcpTools).toHaveLength(2);
  });

  it('фильтрует по chatId; undefined показывает все; событие без record игнорируется', () => {
    const all = renderHook(() => useMcpStreamingTools());
    const mine = renderHook(() => useMcpStreamingTools('c1'));
    const none = renderHook(() => useMcpStreamingTools(null));
    fire(rec('1'), 'start', 'c1');
    fire(rec('2'), 'start', 'c2');
    fire(rec('3'), 'start');
    act(() => {
      window.dispatchEvent(new CustomEvent(MCP_TOOL_ACTIVITY_EVENT, { detail: {} }));
    });
    expect(all.result.current.activeMcpTools).toHaveLength(3);
    expect(mine.result.current.activeMcpTools.map((r) => r.call_id)).toEqual(['1']);
    expect(none.result.current.activeMcpTools.map((r) => r.call_id)).toEqual(['3']);
  });

  it('Clear снимает вызовы одного чата или все; clearActiveMcpTools обнуляет', () => {
    const { result } = renderHook(() => useMcpStreamingTools());
    fire(rec('1'), 'start', 'c1');
    fire(rec('2'), 'start', 'c2');
    act(() => {
      window.dispatchEvent(new CustomEvent('astrachatMcpToolActivityClear', { detail: { chatId: 'c1' } }));
    });
    expect(result.current.activeMcpTools.map((r) => r.call_id)).toEqual(['2']);
    act(() => {
      window.dispatchEvent(new CustomEvent('astrachatMcpToolActivityClear'));
    });
    expect(result.current.activeMcpTools).toEqual([]);
    fire(rec('3'), 'start');
    act(() => result.current.clearActiveMcpTools());
    expect(result.current.activeMcpTools).toEqual([]);
  });
});

describe('useChatMcpSelection', () => {
  it('читает выбор чата, обновляется по событию и пишет изменения', () => {
    setMcpToolIdsForChat('c1', ['server:mcp:a']);
    const { result } = renderHook(() => useChatMcpSelection('c1'));
    expect(result.current.enabledMcpToolIds).toEqual(['server:mcp:a']);

    act(() => result.current.toggleServer('b', true));
    expect(result.current.enabledMcpToolIds).toEqual(['server:mcp:a', 'server:mcp:b']);
    act(() => result.current.toggleServer('a', false));
    expect(result.current.enabledMcpToolIds).toEqual(['server:mcp:b']);

    act(() => result.current.setEnabledMcpToolIds(['server:mcp:z']));
    expect(JSON.parse(localStorage.getItem('chat:c1:mcp_tool_ids')!)).toEqual(['server:mcp:z']);

    localStorage.setItem('chat:c1:mcp_tool_ids', JSON.stringify(['server:mcp:q']));
    act(() => {
      window.dispatchEvent(new CustomEvent(MCP_SELECTION_CHANGED_EVENT, { detail: { chatId: 'other' } }));
    });
    expect(result.current.enabledMcpToolIds).toEqual(['server:mcp:z']);
    act(() => {
      window.dispatchEvent(new CustomEvent(MCP_SELECTION_CHANGED_EVENT, { detail: { chatId: 'c1' } }));
    });
    expect(result.current.enabledMcpToolIds).toEqual(['server:mcp:q']);
  });

  it('без chatId выбор пуст и изменения не пишутся', () => {
    const { result } = renderHook(() => useChatMcpSelection(undefined));
    expect(result.current.enabledMcpToolIds).toEqual([]);
    act(() => result.current.toggleServer('a', true));
    act(() => result.current.setEnabledMcpToolIds(['x']));
    expect(result.current.enabledMcpToolIds).toEqual([]);
    expect(localStorage.length).toBe(0);
    act(() => {
      window.dispatchEvent(new CustomEvent(MCP_SELECTION_CHANGED_EVENT, { detail: { chatId: 'c1' } }));
    });
    expect(result.current.enabledMcpToolIds).toEqual([]);
  });
});
