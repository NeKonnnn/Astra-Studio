import React from 'react';
import { act, renderHook } from '@testing-library/react';

let mockPathname = '/';
jest.mock('react-router-dom', () => ({
  useLocation: () => ({ pathname: mockPathname, state: null }),
}));

let mockAppState: any = { chats: [], currentChatId: null };
jest.mock('@/contexts/AppContext', () => ({
  useAppContext: () => ({ state: mockAppState }),
}));

jest.mock('@/config/api', () => ({
  authFetch: (...a: unknown[]) => (require('@tests/unit/helpers/authFetchMock').authFetchMock as (...x: unknown[]) => unknown)(...a),
  getApiUrl: (p: string) => `http://api.test${p}`,
  getAuthFetchHeaders: () => ({ Authorization: 'Bearer t' }),
}));

jest.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({ token: 't', isLoading: false }),
}));

jest.mock('@/utils/authFetch', () => ({
  authFetch: (...args: unknown[]) => (global as any).fetch(...args),
}));

const mockGetSettings = jest.fn();
const mockInitSettings = jest.fn();
jest.mock('@/settings', () => ({
  getSettings: () => mockGetSettings(),
  initSettings: () => mockInitSettings(),
}));

import { RagReindexStatusProvider, useRagReindexStatus } from '@/contexts/RagReindexStatusContext';

const fetchMock = jest.fn();
const POLL = 10; // секунд, ragReindexStatusPollSeconds

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <RagReindexStatusProvider>{children}</RagReindexStatusProvider>
);

const payload = (over: Record<string, unknown> = {}) => ({
  memory: { reindexing: false },
  project: { reindexing: false },
  kb: { reindexing: false },
  any_reindexing: false,
  agent_has_kb: false,
  project_has_documents: false,
  memory_rag_enabled: true,
  message: '',
  active: [],
  ...over,
});

const ok = (data: unknown) => Promise.resolve({ ok: true, json: () => Promise.resolve(data) });

const settle = async () => {
  await act(async () => {
    for (let i = 0; i < 10; i += 1) await Promise.resolve();
  });
};

const advance = async (ms: number) => {
  await act(async () => {
    jest.advanceTimersByTime(ms);
    for (let i = 0; i < 10; i += 1) await Promise.resolve();
  });
};

const urls = () => fetchMock.mock.calls.map((c) => String(c[0]));

beforeEach(() => {
  jest.useFakeTimers();
  fetchMock.mockReset();
  fetchMock.mockImplementation(() => ok(payload()));
  (global as any).fetch = fetchMock;
  mockGetSettings.mockReset();
  mockGetSettings.mockReturnValue({ app: { ragReindexStatusPollSeconds: POLL } });
  mockInitSettings.mockReset();
  mockPathname = '/';
  mockAppState = { chats: [], currentChatId: null };
  localStorage.clear();
  localStorage.setItem('auth_token', 't');
});

afterEach(() => {
  jest.useRealTimers();
});

describe('RagReindexStatusContext: значения по умолчанию', () => {
  it('вне провайдера — безопасные заглушки', () => {
    const { result } = renderHook(() => useRagReindexStatus());
    expect(result.current.status).toBeNull();
    expect(result.current.anyReindexing).toBe(false);
    expect(result.current.agentHasKb).toBe(false);
    expect(result.current.projectHasDocuments).toBe(false);
    expect(result.current.blockMessage).toBe('');
    expect(result.current.memoryRagEnabled).toBe(true);
    expect(result.current.shouldBlockRagSend({ libraryEnabled: true })).toBe(false);
    expect(() => result.current.notifyReindexStarted()).not.toThrow();
  });
});

describe('RagReindexStatusContext: опрос статуса', () => {
  it('делает первый опрос сразу и отдаёт дефолтный статус до ответа', async () => {
    const { result } = renderHook(() => useRagReindexStatus(), { wrapper });
    expect(result.current.status).toMatchObject({ any_reindexing: false, active: [] });
    await settle();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe('http://api.test/api/rag/reindex-status');
  });

  it('передаёт agent_id и project_id (проект из маршрута) в запросе', async () => {
    localStorage.setItem('active_agent_id', '7');
    mockPathname = '/project/proj-42/chat';
    renderHook(() => useRagReindexStatus(), { wrapper });
    await settle();
    expect(urls()[0]).toBe('http://api.test/api/rag/reindex-status?agent_id=7&project_id=proj-42');
  });

  it('project_id берётся из текущего чата, если маршрут не проектный', async () => {
    mockAppState = {
      chats: [{ id: 'c1', projectId: 'p-chat' }, { id: 'c2' }],
      currentChatId: 'c1',
    };
    renderHook(() => useRagReindexStatus(), { wrapper });
    await settle();
    expect(urls()[0]).toContain('project_id=p-chat');
  });

  it('нечисловой active_agent_id в запрос не попадает', async () => {
    localStorage.setItem('active_agent_id', 'abc');
    renderHook(() => useRagReindexStatus(), { wrapper });
    await settle();
    expect(urls()[0]).toBe('http://api.test/api/rag/reindex-status');
  });

  it('нормализует ответ: булевы приведены, active-массив сохранён', async () => {
    fetchMock.mockImplementation(() =>
      ok({
        memory: { reindexing: 1 },
        any_reindexing: 1,
        agent_has_kb: true,
        project_has_documents: 'yes',
        memory_rag_enabled: false,
        message: 'Идёт пересборка',
        active: [{ scope: 'agent', entity_id: '1', name: 'A' }],
      }),
    );
    const { result } = renderHook(() => useRagReindexStatus(), { wrapper });
    await settle();
    expect(result.current.status).toEqual({
      memory: { reindexing: true },
      project: { reindexing: false },
      kb: { reindexing: false },
      any_reindexing: true,
      agent_has_kb: true,
      project_has_documents: true,
      memory_rag_enabled: false,
      message: 'Идёт пересборка',
      active: [{ scope: 'agent', entity_id: '1', name: 'A' }],
    });
    expect(result.current.agentHasKb).toBe(true);
    expect(result.current.projectHasDocuments).toBe(true);
    expect(result.current.memoryRagEnabled).toBe(false);
    expect(result.current.anyReindexing).toBe(true); // memory.reindexing → плашка
  });

  it('ответ без полей: message не строка → "", active не массив → []', async () => {
    fetchMock.mockImplementation(() => ok({ message: 5, active: 'x' }));
    const { result } = renderHook(() => useRagReindexStatus(), { wrapper });
    await settle();
    expect(result.current.status).toMatchObject({ message: '', active: [], memory_rag_enabled: true });
  });

  it('повторяет опрос с интервалом из настроек', async () => {
    renderHook(() => useRagReindexStatus(), { wrapper });
    await settle();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await advance(POLL * 1000 - 1);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await advance(1);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await advance(POLL * 1000);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it('пока идёт пересборка — опрос чаще (3 с)', async () => {
    fetchMock.mockImplementation(() => ok(payload({ any_reindexing: true })));
    renderHook(() => useRagReindexStatus(), { wrapper });
    await settle();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await advance(3000);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await advance(3000);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it('не-OK ответ и сетевая ошибка игнорируются, опрос продолжается', async () => {
    fetchMock.mockResolvedValueOnce({ ok: false, json: () => Promise.resolve({}) });
    const { result } = renderHook(() => useRagReindexStatus(), { wrapper });
    await settle();
    expect(result.current.status).toMatchObject({ any_reindexing: false });
    fetchMock.mockRejectedValueOnce(new Error('offline'));
    await advance(POLL * 1000);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    fetchMock.mockImplementation(() => ok(payload({ agent_has_kb: true })));
    await advance(POLL * 1000);
    expect(result.current.agentHasKb).toBe(true);
  });

  it('размонтирование останавливает опрос', async () => {
    const { unmount } = renderHook(() => useRagReindexStatus(), { wrapper });
    await settle();
    unmount();
    await advance(POLL * 1000 * 3);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('смена маршрута перезапускает цепочку опроса с новыми параметрами', async () => {
    const { rerender } = renderHook(() => useRagReindexStatus(), { wrapper });
    await settle();
    mockPathname = '/project/zzz';
    rerender();
    await settle();
    expect(urls().some((u) => u.includes('project_id=zzz'))).toBe(true);
  });

  it('пересоздание state.chats не перезапускает опрос: интервал не сбивается', async () => {
    mockAppState = { chats: [{ id: 'c1' }], currentChatId: 'c1' };
    const { rerender } = renderHook(() => useRagReindexStatus(), { wrapper });
    await settle();
    await advance(4000);
    mockAppState = { chats: [{ id: 'c1' }], currentChatId: 'c1' }; // новая ссылка, тот же projectId
    rerender();
    await settle();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await advance(POLL * 1000 - 4000);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

describe('RagReindexStatusContext: настройки', () => {
  it('если getSettings бросает — вызывает initSettings и опрашивает', async () => {
    mockGetSettings.mockImplementation(() => {
      throw new Error('not init');
    });
    mockInitSettings.mockResolvedValue({ app: { ragReindexStatusPollSeconds: 5 } });
    renderHook(() => useRagReindexStatus(), { wrapper });
    await settle();
    expect(mockInitSettings).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await advance(5000);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('если и initSettings падает — опроса нет, ошибки не выбрасываются', async () => {
    mockGetSettings.mockImplementation(() => {
      throw new Error('not init');
    });
    mockInitSettings.mockRejectedValue(new Error('no config'));
    renderHook(() => useRagReindexStatus(), { wrapper });
    await settle();
    await advance(60_000);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('размонтирование до завершения initSettings отменяет запуск опроса', async () => {
    let resolveInit!: (v: unknown) => void;
    mockGetSettings.mockImplementation(() => {
      throw new Error('not init');
    });
    mockInitSettings.mockReturnValue(new Promise((r) => (resolveInit = r)));
    const { unmount } = renderHook(() => useRagReindexStatus(), { wrapper });
    unmount();
    await act(async () => {
      resolveInit({ app: { ragReindexStatusPollSeconds: 5 } });
      await Promise.resolve();
    });
    await advance(10_000);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe('RagReindexStatusContext: notifyReindexStarted (разгон опроса)', () => {
  it('сразу опрашивает и затем идёт с шагом 1 с, пока не увидит флаг', async () => {
    const { result } = renderHook(() => useRagReindexStatus(), { wrapper });
    await settle();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      result.current.notifyReindexStarted();
      for (let i = 0; i < 10; i += 1) await Promise.resolve();
    });
    expect(fetchMock).toHaveBeenCalledTimes(2); // немедленный
    await advance(1000);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    await advance(1000);
    expect(fetchMock).toHaveBeenCalledTimes(4);

    // сервер поднял флаг → разгон снят, шаг 3 с
    fetchMock.mockImplementation(() => ok(payload({ any_reindexing: true })));
    await advance(1000);
    const callsAfterFlag = fetchMock.mock.calls.length;
    await advance(1000);
    expect(fetchMock.mock.calls.length).toBe(callsAfterFlag); // шаг уже 3 с
    await advance(2000);
    expect(fetchMock.mock.calls.length).toBe(callsAfterFlag + 1);
  });

  it('окно разгона заканчивается через 25 с и возвращается обычный интервал', async () => {
    const { result } = renderHook(() => useRagReindexStatus(), { wrapper });
    await settle();
    await act(async () => {
      result.current.notifyReindexStarted();
      for (let i = 0; i < 10; i += 1) await Promise.resolve();
    });
    // цепочка таймеров строится после await, поэтому двигаем время по секунде
    for (let i = 0; i < 25; i += 1) await advance(1000);
    const after = fetchMock.mock.calls.length;
    expect(after).toBeGreaterThanOrEqual(25);
    for (let i = 0; i < 5; i += 1) await advance(1000); // окно закрыто: уже не 1 с
    expect(fetchMock.mock.calls.length).toBeLessThanOrEqual(after + 1);
  });

  it('kick во время незавершённого опроса не плодит параллельных запросов', async () => {
    let resolveFirst!: (v: unknown) => void;
    fetchMock.mockImplementationOnce(() => new Promise((r) => (resolveFirst = r)));
    const { result } = renderHook(() => useRagReindexStatus(), { wrapper });
    await settle();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await act(async () => {
      result.current.notifyReindexStarted();
      await Promise.resolve();
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await act(async () => {
      resolveFirst({ ok: true, json: () => Promise.resolve(payload()) });
      for (let i = 0; i < 10; i += 1) await Promise.resolve();
    });
  });

  it('kick после размонтирования ничего не запрашивает', async () => {
    const { result, unmount } = renderHook(() => useRagReindexStatus(), { wrapper });
    await settle();
    const kick = result.current.notifyReindexStarted;
    unmount();
    await act(async () => {
      kick();
      await Promise.resolve();
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe('RagReindexStatusContext: блокировка отправки и плашка', () => {
  it('shouldBlockRagSend блокирует при пересборке KB текущего агента', async () => {
    localStorage.setItem('active_agent_id', '7');
    fetchMock.mockImplementation(() =>
      ok(
        payload({
          any_reindexing: true,
          agent_has_kb: true,
          active: [{ scope: 'agent', entity_id: '7', name: 'Бот' }],
        }),
      ),
    );
    const { result } = renderHook(() => useRagReindexStatus(), { wrapper });
    await settle();
    expect(result.current.shouldBlockRagSend({ libraryEnabled: false })).toBe(true);
    expect(result.current.anyReindexing).toBe(true);
    expect(result.current.blockMessage).toContain('агента «Бот»');
  });

  it('пересборка чужого агента отправку не блокирует', async () => {
    localStorage.setItem('active_agent_id', '7');
    fetchMock.mockImplementation(() =>
      ok(payload({ any_reindexing: true, agent_has_kb: true, active: [{ scope: 'agent', entity_id: '99' }] })),
    );
    const { result } = renderHook(() => useRagReindexStatus(), { wrapper });
    await settle();
    expect(result.current.shouldBlockRagSend({ libraryEnabled: false })).toBe(false);
  });

  it('пересборка памяти блокирует только при включённой библиотеке', async () => {
    fetchMock.mockImplementation(() =>
      ok(payload({ any_reindexing: true, memory: { reindexing: true }, message: 'Память пересобирается' })),
    );
    const { result } = renderHook(() => useRagReindexStatus(), { wrapper });
    await settle();
    expect(result.current.shouldBlockRagSend({ libraryEnabled: true })).toBe(true);
    expect(result.current.shouldBlockRagSend({ libraryEnabled: false })).toBe(false);
    expect(result.current.blockMessage).toBe('Память пересобирается');
  });

  it('проект: плашка и блокировка только для открытого проекта', async () => {
    mockPathname = '/project/p1';
    fetchMock.mockImplementation(() =>
      ok(
        payload({
          any_reindexing: true,
          project_has_documents: true,
          active: [{ scope: 'project', entity_id: 'p1', name: 'Альфа' }],
        }),
      ),
    );
    const { result } = renderHook(() => useRagReindexStatus(), { wrapper });
    await settle();
    expect(result.current.anyReindexing).toBe(true);
    expect(result.current.shouldBlockRagSend({ libraryEnabled: false })).toBe(true);
    expect(result.current.blockMessage).toContain('проекта «Альфа»');
  });

  it('чужой проект плашку не показывает', async () => {
    mockPathname = '/project/p1';
    fetchMock.mockImplementation(() =>
      ok(payload({ any_reindexing: true, active: [{ scope: 'project', entity_id: 'other' }] })),
    );
    const { result } = renderHook(() => useRagReindexStatus(), { wrapper });
    await settle();
    expect(result.current.anyReindexing).toBe(false);
    expect(result.current.blockMessage).toBe('');
  });
});
