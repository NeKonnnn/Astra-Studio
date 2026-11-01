import { act, renderHook, waitFor } from '@testing-library/react';

jest.mock('@/config/api', () => ({
  authFetch: (...a: unknown[]) => (require('@tests/unit/helpers/authFetchMock').authFetchMock as (...x: unknown[]) => unknown)(...a),
  getApiUrl: (p: string) => `http://api.test${p}`,
}));

import { useChatContextUsage } from '@/hooks/useChatContextUsage';
import { setMcpToolIdsForChat } from '@/mcp/selectionStorage';
import { LAST_SELECTED_MODEL_PATH_STORAGE_KEY } from '@/utils/modelThinking';
import { MODEL_PATH_CHANGED_EVENT, MODEL_SETTINGS_CHANGED_EVENT } from '@/utils/contextTokens';

const fetchMock = jest.fn();

const resp = (data: unknown, ok = true) =>
  Promise.resolve({ ok, json: () => Promise.resolve(data) });

const msg = (id: string, role: 'user' | 'assistant', content: string, extra: any = {}) => ({
  id,
  role,
  content,
  timestamp: '2024-01-01T00:00:00Z',
  ...extra,
});

const models = [
  { path: 'm/a', context_size: 4000 },
  { path: 'm/b', context_size: 16000 },
];

/** Роутинг fetch по URL: настройки моделей и разбивка контекста. */
function route({
  settings = { context_size: 0 } as any,
  settingsOk = true,
  breakdown = { segments: [] } as any,
  breakdownOk = true,
  breakdownError = false,
} = {}) {
  fetchMock.mockImplementation((url: string) => {
    if (url.endsWith('/api/models/settings')) return resp(settings, settingsOk);
    if (url.endsWith('/api/chat/context-breakdown')) {
      if (breakdownError) return Promise.reject(new Error('boom'));
      return resp(breakdown, breakdownOk);
    }
    return resp({});
  });
}

const base = { messages: [] as any[], availableModels: models as any };

beforeEach(() => {
  fetchMock.mockReset();
  (global as any).fetch = fetchMock;
  localStorage.clear();
  route();
});

const breakdownCalls = () =>
  fetchMock.mock.calls.filter((c) => String(c[0]).endsWith('/api/chat/context-breakdown'));

describe('useChatContextUsage: лимит контекста', () => {
  it('без выбранной модели и настроек — лимит по умолчанию 8192', async () => {
    const { result } = renderHook(() => useChatContextUsage(base));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(result.current.maxTokens).toBe(8192);
    expect(result.current.currentTokens).toBe(0);
    expect(result.current.isOverLimit).toBe(false);
  });

  it('берёт окно выбранной модели из localStorage', async () => {
    localStorage.setItem(LAST_SELECTED_MODEL_PATH_STORAGE_KEY, 'm/a');
    const { result } = renderHook(() => useChatContextUsage(base));
    expect(result.current.maxTokens).toBe(4000);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  });

  it('configuredContextSize ограничивает окно модели (min)', async () => {
    localStorage.setItem(LAST_SELECTED_MODEL_PATH_STORAGE_KEY, 'm/b');
    const { result } = renderHook(() => useChatContextUsage({ ...base, configuredContextSize: 2048 }));
    expect(result.current.maxTokens).toBe(2048);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  });

  it('loadedModelCtx используется, если у модели нет собственного окна', async () => {
    localStorage.setItem(LAST_SELECTED_MODEL_PATH_STORAGE_KEY, 'm/unknown');
    const { result } = renderHook(() => useChatContextUsage({ ...base, loadedModelCtx: 12345 }));
    expect(result.current.maxTokens).toBe(12345);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  });

  it('context_size из /api/models/settings приоритетнее configuredContextSize', async () => {
    route({ settings: { context_size: 3000 } });
    localStorage.setItem(LAST_SELECTED_MODEL_PATH_STORAGE_KEY, 'm/b');
    const { result } = renderHook(() => useChatContextUsage({ ...base, configuredContextSize: 9000 }));
    await waitFor(() => expect(result.current.maxTokens).toBe(3000));
  });

  it('невалидный context_size (0, не число) в настройках игнорируется', async () => {
    route({ settings: { context_size: 'abc' } });
    const { result } = renderHook(() => useChatContextUsage({ ...base, configuredContextSize: 5000 }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    await act(async () => {});
    expect(result.current.maxTokens).toBe(5000);
  });

  it('не-OK ответ настроек и сетевая ошибка не ломают хук', async () => {
    route({ settingsOk: false });
    const first = renderHook(() => useChatContextUsage(base));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(first.result.current.maxTokens).toBe(8192);

    fetchMock.mockReset();
    fetchMock.mockRejectedValue(new Error('offline'));
    const second = renderHook(() => useChatContextUsage(base));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    await act(async () => {});
    expect(second.result.current.maxTokens).toBe(8192);
  });

  it('смена настроек модели (событие) перечитывает /api/models/settings', async () => {
    route({ settings: { context_size: 1000 } });
    const { result } = renderHook(() => useChatContextUsage(base));
    await waitFor(() => expect(result.current.maxTokens).toBe(1000));
    route({ settings: { context_size: 2000 } });
    act(() => {
      window.dispatchEvent(new Event(MODEL_SETTINGS_CHANGED_EVENT));
    });
    await waitFor(() => expect(result.current.maxTokens).toBe(2000));
  });

  it('заголовок Authorization берётся из auth_token (legacy-ключ token игнорируется)', async () => {
    localStorage.setItem('token', 'legacy-token');
    localStorage.setItem('auth_token', 'tok');
    renderHook(() => useChatContextUsage(base));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(fetchMock.mock.calls[0][1].headers).toEqual(
      expect.objectContaining({ Authorization: 'Bearer tok' }),
    );
  });

  it('без токена заголовок Authorization не отправляется', async () => {
    renderHook(() => useChatContextUsage(base));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(fetchMock.mock.calls[0][1]?.headers ?? {}).not.toHaveProperty('Authorization');
  });

  it('событие смены модели с path обновляет лимит; без path — перечитывает localStorage', async () => {
    const { result } = renderHook(() => useChatContextUsage(base));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    act(() => {
      window.dispatchEvent(new CustomEvent(MODEL_PATH_CHANGED_EVENT, { detail: { path: 'm/a' } }));
    });
    expect(result.current.maxTokens).toBe(4000);
    localStorage.setItem(LAST_SELECTED_MODEL_PATH_STORAGE_KEY, 'm/b');
    act(() => {
      window.dispatchEvent(new CustomEvent(MODEL_PATH_CHANGED_EVENT, { detail: {} }));
    });
    expect(result.current.maxTokens).toBe(16000);
    localStorage.setItem(LAST_SELECTED_MODEL_PATH_STORAGE_KEY, 'm/a');
    act(() => {
      window.dispatchEvent(new Event('storage'));
    });
    expect(result.current.maxTokens).toBe(4000);
  });

  it('multi-LLM: лимит = минимум по выбранным моделям', async () => {
    const { result } = renderHook(() =>
      useChatContextUsage({
        ...base,
        isMultiLlmMode: true,
        multiLlmModelPaths: ['m/b', '', 'm/a'],
      }),
    );
    expect(result.current.maxTokens).toBe(4000);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  });

  it('multi-LLM без непустых путей откатывается на выбранную модель', async () => {
    localStorage.setItem(LAST_SELECTED_MODEL_PATH_STORAGE_KEY, 'm/b');
    const { result } = renderHook(() =>
      useChatContextUsage({ ...base, isMultiLlmMode: true, multiLlmModelPaths: ['', ''] }),
    );
    expect(result.current.maxTokens).toBe(16000);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  });
});

describe('useChatContextUsage: подсчёт токенов', () => {
  it('считает токены переписки и черновика и ставит isOverLimit', async () => {
    localStorage.setItem(LAST_SELECTED_MODEL_PATH_STORAGE_KEY, 'm/a');
    const longText = 'слово '.repeat(5000);
    const { result } = renderHook(() =>
      useChatContextUsage({
        ...base,
        messages: [msg('1', 'user', 'привет'), msg('2', 'assistant', 'здравствуйте')],
        draftText: longText,
      }),
    );
    expect(result.current.currentTokens).toBeGreaterThan(4000);
    expect(result.current.isOverLimit).toBe(true);
    expect(result.current.percent).toBeGreaterThan(100);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  });

  it('segments содержат memory и current_user', async () => {
    const { result } = renderHook(() =>
      useChatContextUsage({
        ...base,
        messages: [msg('1', 'user', 'вопрос'), msg('2', 'assistant', 'ответ')],
      }),
    );
    const ids = (result.current.segments || []).map((s) => s.id);
    expect(ids).toEqual(expect.arrayContaining(['context_instructions', 'memory', 'current_user']));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  });
});

describe('useChatContextUsage: overhead (context-breakdown)', () => {
  const breakdown = {
    segments: [
      { id: 'context_instructions', label: 'x', tokens: 100, active: true },
      { id: 'skills', label: 's', tokens: 50, active: true },
      { id: 'artifacts', label: 'a', tokens: 30, active: true },
    ],
    entity_context_size: 6000,
  };

  it('refreshOverhead запрашивает разбивку и добавляет токены оверхеда', async () => {
    route({ breakdown });
    const { result } = renderHook(() => useChatContextUsage(base));
    expect(result.current.overheadLoading).toBe(false);
    act(() => result.current.refreshOverhead());
    await waitFor(() => expect(result.current.currentTokens).toBe(180));
    expect(result.current.overheadLoading).toBe(false);
    const call = breakdownCalls()[0];
    expect(call[1].method).toBe('POST');
    expect(JSON.parse(call[1].body)).toEqual({
      model_path: null,
      agent_id: null,
      use_kb_rag: false,
      tool_ids: [],
      project_instructions: null,
    });
    // entity_context_size задаёт лимит
    expect(result.current.maxTokens).toBe(6000);
    const segIds = (result.current.segments || []).map((s) => s.id);
    expect(segIds).toEqual(expect.arrayContaining(['skills', 'artifacts']));
  });

  it('повторный refreshOverhead с тем же ключом запрос не повторяет', async () => {
    route({ breakdown });
    const { result } = renderHook(() => useChatContextUsage(base));
    act(() => result.current.refreshOverhead());
    await waitFor(() => expect(result.current.currentTokens).toBe(180));
    act(() => result.current.refreshOverhead());
    expect(breakdownCalls()).toHaveLength(1);
  });

  it('флаги skills_enabled / artifacts_enabled включают сегменты без токенов', async () => {
    route({ breakdown: { segments: [], skills_enabled: true, artifacts_enabled: true } });
    const { result } = renderHook(() => useChatContextUsage(base));
    act(() => result.current.refreshOverhead());
    await waitFor(() => expect(breakdownCalls()).toHaveLength(1));
    await waitFor(() =>
      expect((result.current.segments || []).map((s) => s.id)).toEqual(
        expect.arrayContaining(['skills', 'artifacts']),
      ),
    );
  });

  it('не-OK ответ breakdown оставляет loading=false и оверхед пустым, повтор разрешён', async () => {
    route({ breakdownOk: false });
    const { result } = renderHook(() => useChatContextUsage(base));
    act(() => result.current.refreshOverhead());
    await waitFor(() => expect(breakdownCalls()).toHaveLength(1));
    await waitFor(() => expect(result.current.overheadLoading).toBe(false));
    expect(result.current.currentTokens).toBe(0);
    act(() => result.current.refreshOverhead());
    await waitFor(() => expect(breakdownCalls()).toHaveLength(2));
  });

  it('сетевая ошибка breakdown сбрасывает оверхед', async () => {
    route({ breakdownError: true });
    const { result } = renderHook(() => useChatContextUsage(base));
    act(() => result.current.refreshOverhead());
    await waitFor(() => expect(breakdownCalls()).toHaveLength(1));
    await waitFor(() => expect(result.current.overheadLoading).toBe(false));
    expect(result.current.currentTokens).toBe(0);
  });

  it('segments не массив → пустой оверхед; entity_context_size <= 0 игнорируется', async () => {
    route({ breakdown: { segments: 'nope', entity_context_size: 0 } });
    const { result } = renderHook(() => useChatContextUsage({ ...base, configuredContextSize: 7000 }));
    act(() => result.current.refreshOverhead());
    await waitFor(() => expect(breakdownCalls()).toHaveLength(1));
    await waitFor(() => expect(result.current.overheadLoading).toBe(false));
    expect(result.current.maxTokens).toBe(7000);
    expect(result.current.currentTokens).toBe(0);
  });

  it('ключ запроса включает агента, KB-RAG, инструкции проекта и MCP чата', async () => {
    localStorage.setItem('active_agent_id', '42');
    setMcpToolIdsForChat('chat-1', ['mcp:srv1']);
    route({ breakdown: { segments: [] } });
    const { result } = renderHook(() =>
      useChatContextUsage({
        ...base,
        chatId: 'chat-1',
        useKbRag: true,
        projectInstructions: '  инструкции  ',
      }),
    );
    act(() => result.current.refreshOverhead());
    await waitFor(() => expect(breakdownCalls()).toHaveLength(1));
    expect(JSON.parse(breakdownCalls()[0][1].body)).toEqual({
      model_path: null,
      agent_id: 42,
      use_kb_rag: true,
      tool_ids: ['mcp:srv1'],
      project_instructions: 'инструкции',
    });
    await waitFor(() => expect(result.current.overheadLoading).toBe(false));
  });

  it('смена ключа сбрасывает оверхед и разрешает новый запрос', async () => {
    route({ breakdown });
    const { result, rerender } = renderHook(({ kb }) => useChatContextUsage({ ...base, useKbRag: kb }), {
      initialProps: { kb: false },
    });
    act(() => result.current.refreshOverhead());
    await waitFor(() => expect(result.current.currentTokens).toBe(180));
    rerender({ kb: true });
    await waitFor(() => expect(result.current.currentTokens).toBe(0));
    act(() => result.current.refreshOverhead());
    await waitFor(() => expect(breakdownCalls()).toHaveLength(2));
  });
});

describe('useChatContextUsage: агент и MCP', () => {
  it('событие agentSelected перечитывает активного агента', async () => {
    route();
    const { result } = renderHook(() => useChatContextUsage(base));
    localStorage.setItem('active_agent_id', '9');
    act(() => {
      window.dispatchEvent(new Event('agentSelected'));
    });
    act(() => result.current.refreshOverhead());
    await waitFor(() => expect(breakdownCalls()).toHaveLength(1));
    expect(JSON.parse(breakdownCalls()[0][1].body).agent_id).toBe(9);
    await waitFor(() => expect(result.current.overheadLoading).toBe(false));
  });

  it('нечисловой active_agent_id даёт agent_id=null', async () => {
    localStorage.setItem('active_agent_id', 'abc');
    const { result } = renderHook(() => useChatContextUsage(base));
    act(() => result.current.refreshOverhead());
    await waitFor(() => expect(breakdownCalls()).toHaveLength(1));
    expect(JSON.parse(breakdownCalls()[0][1].body).agent_id).toBeNull();
    await waitFor(() => expect(result.current.overheadLoading).toBe(false));
  });

  it('MCP-событие своего чата / без chatId обновляет tool_ids, чужого — нет', async () => {
    route();
    const { result } = renderHook(() => useChatContextUsage({ ...base, chatId: 'c1' }));
    localStorage.setItem('chat:c1:mcp_tool_ids', JSON.stringify(['x']));
    act(() => setMcpToolIdsForChat('c2', ['y']));
    act(() => result.current.refreshOverhead());
    await waitFor(() => expect(breakdownCalls()).toHaveLength(1));
    const toolIdsFirst = JSON.parse(breakdownCalls()[0][1].body).tool_ids;
    expect(toolIdsFirst).not.toContain('y');
    await waitFor(() => expect(result.current.overheadLoading).toBe(false));

    act(() => setMcpToolIdsForChat('c1', ['mcp:z']));
    act(() => result.current.refreshOverhead());
    await waitFor(() => expect(breakdownCalls()).toHaveLength(2));
    expect(JSON.parse(breakdownCalls()[1][1].body).tool_ids).toEqual(['mcp:z']);
    await waitFor(() => expect(result.current.overheadLoading).toBe(false));

    act(() => {
      window.dispatchEvent(new CustomEvent('astrachatMcpSelectionChanged', { detail: {} }));
    });
  });

  it('без chatId MCP-инструменты всегда пустые', async () => {
    const { result } = renderHook(() => useChatContextUsage({ ...base, chatId: null }));
    act(() => {
      window.dispatchEvent(new CustomEvent('astrachatMcpSelectionChanged', { detail: { chatId: 'zz' } }));
    });
    act(() => result.current.refreshOverhead());
    await waitFor(() => expect(breakdownCalls()).toHaveLength(1));
    expect(JSON.parse(breakdownCalls()[0][1].body).tool_ids).toEqual([]);
    await waitFor(() => expect(result.current.overheadLoading).toBe(false));
  });

  it('снимает подписки при размонтировании', async () => {
    const removeSpy = jest.spyOn(window, 'removeEventListener');
    const { unmount } = renderHook(() => useChatContextUsage(base));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    unmount();
    const removed = removeSpy.mock.calls.map((c) => c[0]);
    expect(removed).toEqual(
      expect.arrayContaining([
        MODEL_SETTINGS_CHANGED_EVENT,
        MODEL_PATH_CHANGED_EVENT,
        'storage',
        'agentSelected',
        'astrachatMcpSelectionChanged',
      ]),
    );
    removeSpy.mockRestore();
  });
});

describe('useChatContextUsage: недоступный localStorage', () => {
  it('чтение модели и агента из хранилища не роняет хук (значения по умолчанию)', async () => {
    const spy = jest.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('storage blocked');
    });
    try {
      const { result } = renderHook(() => useChatContextUsage(base));
      expect(result.current.maxTokens).toBe(8192);
      act(() => result.current.refreshOverhead());
      await waitFor(() => expect(result.current.overheadLoading).toBe(false));
      expect(JSON.parse(breakdownCalls()[0]?.[1]?.body ?? '{"agent_id":null}').agent_id).toBeNull();
    } finally {
      spy.mockRestore();
    }
  });
});
