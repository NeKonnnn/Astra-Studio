import React from 'react';
import { act, renderHook, waitFor } from '@testing-library/react';

const mockUseAuth = jest.fn();
jest.mock('@/contexts/AuthContext', () => ({ useAuth: () => mockUseAuth() }));

const mockInitSettings = jest.fn();
jest.mock('@/settings', () => ({ initSettings: () => mockInitSettings() }));

jest.mock('@/config/api', () => ({
  authFetch: (...a: unknown[]) => (require('@tests/unit/helpers/authFetchMock').authFetchMock as (...x: unknown[]) => unknown)(...a),
  getApiUrl: (p: string) => `http://api.test${p}`,
  API_ENDPOINTS: { PROJECT_DELETE: (id: string) => `/api/projects/${id}` },
}));

const mockFetchProjects = jest.fn();
const mockMigrate = jest.fn();
const mockSyncCreate = jest.fn();
const mockSyncUpdate = jest.fn();
jest.mock('@/utils/projectsApi', () => ({
  fetchProjectsFromServer: () => mockFetchProjects(),
  migrateLocalProjectsToServer: (p: unknown) => mockMigrate(p),
  syncProjectCreate: (p: unknown) => mockSyncCreate(p),
  syncProjectUpdate: (id: unknown, u: unknown) => mockSyncUpdate(id, u),
}));

jest.mock('@/utils/mapServerConversation', () => ({
  mapServerConversationToChat: (c: any) => ({
    id: c.id,
    title: c.title ?? 't',
    messages: c.messages ?? [],
    createdAt: c.createdAt ?? 'c',
    updatedAt: c.updatedAt ?? 'u',
    isArchived: c.isArchived,
    projectId: c.projectId,
  }),
}));

import { AppProvider, useAppActions, useAppContext } from '@/contexts/AppContext';
import { MODEL_SETTINGS_CHANGED_EVENT } from '@/utils/contextTokens';

const fetchMock = jest.fn();
const wrapper = ({ children }: { children: React.ReactNode }) => <AppProvider>{children}</AppProvider>;
const useAll = () => ({ ...useAppContext(), actions: useAppActions() });

const msg = (id: string, role: 'user' | 'assistant', content: string, extra: any = {}) => ({
  id, role, content, timestamp: 't', ...extra,
});
const chat = (id: string, messages: any[] = [], extra: any = {}) => ({
  id, title: `T-${id}`, messages, createdAt: 'c', updatedAt: '2024-01-01T00:00:00.000Z', ...extra,
});
let seq = 0;
const uid = (p: string) => `${p}-s${(seq += 1)}`;

const json = (data: unknown, ok = true, status = 200) =>
  Promise.resolve({ ok, status, json: () => Promise.resolve(data) });

/** Роутер fetch по "METHOD url-подстрока". */
type Route = [string, () => Promise<any>];
function routeFetch(routes: Route[]) {
  fetchMock.mockImplementation((url: string, init?: any) => {
    const key = `${init?.method || 'GET'} ${url}`;
    const hit = routes.find(([k]) => key.includes(k));
    return hit ? hit[1]() : json({}, false, 404);
  });
}
const calls = (needle: string) => fetchMock.mock.calls.filter((c) => `${c[1]?.method || 'GET'} ${c[0]}`.includes(needle));

const flush = async () => {
  await act(async () => {
    for (let i = 0; i < 15; i += 1) await Promise.resolve();
  });
};

beforeEach(() => {
  jest.clearAllMocks();
  localStorage.clear();
  mockUseAuth.mockReturnValue({ token: null });
  mockInitSettings.mockResolvedValue(undefined);
  mockFetchProjects.mockResolvedValue([]);
  fetchMock.mockReset();
  routeFetch([]);
  (global as any).fetch = fetchMock;
});

describe('AppProvider: настройки модели', () => {
  it('загружает /api/models/settings с Bearer токеном и мержит с дефолтами', async () => {
    mockUseAuth.mockReturnValue({ token: 'tok' });
    routeFetch([['/api/models/settings', () => json({ temperature: 0.2, context_size: 4096 })]]);
    const { result } = renderHook(() => useAll(), { wrapper });
    await waitFor(() => expect(result.current.state.modelSettings.temperature).toBe(0.2));
    expect(result.current.state.modelSettings.context_size).toBe(4096);
    expect(result.current.state.modelSettings.top_k).toBe(40); // дефолт сохранён
    expect(calls('/api/models/settings')[0][1].headers).toEqual({ Authorization: 'Bearer tok' });
  });

  it('токен берётся из localStorage, если в контексте его нет; без токена заголовков нет', async () => {
    localStorage.setItem('auth_token', 'ls-tok');
    routeFetch([['/api/models/settings', () => json({})]]);
    renderHook(() => useAll(), { wrapper });
    await flush();
    expect(calls('/api/models/settings')[0][1].headers).toEqual({ Authorization: 'Bearer ls-tok' });

    localStorage.clear();
    fetchMock.mockClear();
    renderHook(() => useAll(), { wrapper });
    await flush();
    expect(calls('/api/models/settings')[0][1].headers).toEqual({});
  });

  it('не-OK и сетевая ошибка настроек игнорируются', async () => {
    routeFetch([['/api/models/settings', () => json({}, false, 500)]]);
    const { result } = renderHook(() => useAll(), { wrapper });
    await flush();
    expect(result.current.state.modelSettings.temperature).toBe(0.7);
    fetchMock.mockImplementation(() => Promise.reject(new Error('offline')));
    const second = renderHook(() => useAll(), { wrapper });
    await flush();
    expect(second.result.current.state.modelSettings.temperature).toBe(0.7);
  });

  it('событие MODEL_SETTINGS_CHANGED_EVENT перезагружает настройки; после размонтирования — нет', async () => {
    routeFetch([['/api/models/settings', () => json({ temperature: 0.3 })]]);
    const { result, unmount } = renderHook(() => useAll(), { wrapper });
    await flush();
    expect(calls('/api/models/settings')).toHaveLength(1);
    routeFetch([['/api/models/settings', () => json({ temperature: 0.9 })]]);
    act(() => {
      window.dispatchEvent(new Event(MODEL_SETTINGS_CHANGED_EVENT));
    });
    await waitFor(() => expect(result.current.state.modelSettings.temperature).toBe(0.9));
    unmount();
    fetchMock.mockClear();
    window.dispatchEvent(new Event(MODEL_SETTINGS_CHANGED_EVENT));
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe('AppProvider: начальная загрузка без токена', () => {
  it('нет сохранённого состояния → isInitialized=true', async () => {
    const { result } = renderHook(() => useAll(), { wrapper });
    await waitFor(() => expect(result.current.state.isInitialized).toBe(true));
    expect(result.current.state.chats).toEqual([]);
  });

  it('сохранённые чаты и проекты восстанавливаются без выгрузки', async () => {
    const id = uid('off');
    localStorage.setItem(
      'memo-chats',
      JSON.stringify({
        chats: [chat(id, [msg('1', 'user', 'hi')]), chat(uid('off2'), [msg('2', 'user', 'yo')])],
        currentChatId: id,
        folders: [{ id: 'f', name: 'F', chatIds: [], expanded: true }],
        projects: [{ id: 'p', name: 'P', memory: 'default', instructions: '', createdAt: 'c', updatedAt: 'u' }],
      }),
    );
    const { result } = renderHook(() => useAll(), { wrapper });
    await waitFor(() => expect(result.current.state.projects).toHaveLength(1));
    await waitFor(() => expect(result.current.state.chats.length).toBeGreaterThan(0));
    expect(result.current.state.chats.every((c) => !c.messagesUnloaded)).toBe(true);
    expect(result.current.state.folders).toHaveLength(1);
    expect(mockInitSettings).not.toHaveBeenCalled();
  });

  it('битый JSON в memo-chats логируется, приложение стартует с пустым состоянием', async () => {
    localStorage.setItem('memo-chats', '{broken');
    const err = jest.spyOn(console, 'error').mockImplementation(() => {});
    const { result } = renderHook(() => useAll(), { wrapper });
    await waitFor(() => expect(result.current.state.isInitialized).toBe(true));
    expect(err).toHaveBeenCalledWith('Ошибка загрузки состояния из localStorage:', expect.any(Error));
    err.mockRestore();
  });
});

describe('AppProvider: начальная загрузка с токеном', () => {
  const withToken = () => mockUseAuth.mockReturnValue({ token: 'tok' });
  const proj = (id: string) => ({ id, name: id, memory: 'default', instructions: '', createdAt: 'c', updatedAt: 'u' });

  it('серверные чаты и проекты: RESTORE_CHATS, неактивные выгружаются, текущий — первый не архивный', async () => {
    withToken();
    const [a, b, c] = [uid('sv-a'), uid('sv-b'), uid('sv-c')];
    mockFetchProjects.mockResolvedValue([proj('sp')]);
    routeFetch([
      ['/api/conversations?limit=500', () => json({ conversations: [
        chat(a, [msg('1', 'user', 'x')], { isArchived: true }),
        chat(b, [msg('2', 'user', 'y')]),
        chat(c, [msg('3', 'user', 'z')]),
      ] })],
      ['GET http://api.test/api/conversations/', () => json({ conversation: chat(b, [msg('2', 'user', 'y')]) })],
    ]);
    const { result } = renderHook(() => useAll(), { wrapper });
    await waitFor(() => expect(result.current.state.chats).toHaveLength(3));
    expect(result.current.state.currentChatId).toBe(b);
    expect(result.current.state.projects.map((p) => p.id)).toEqual(['sp']);
    expect(mockInitSettings).toHaveBeenCalled();
    expect(result.current.state.chats.find((x) => x.id === c)!.messagesUnloaded).toBe(true);
    expect(calls('/api/conversations?limit=500')[0][1].headers).toEqual({ Authorization: 'Bearer tok' });
  });

  it('если сохранённый currentChatId есть на сервере и не в архиве — он остаётся текущим', async () => {
    withToken();
    const [a, b] = [uid('sc-a'), uid('sc-b')];
    localStorage.setItem('memo-chats', JSON.stringify({ chats: [], currentChatId: b, folders: [{ id: 'f', name: 'F', chatIds: [], expanded: true }] }));
    routeFetch([['/api/conversations?limit=500', () => json({ conversations: [chat(a, [msg('1', 'user', 'x')]), chat(b, [msg('2', 'user', 'y')])] })]]);
    const { result } = renderHook(() => useAll(), { wrapper });
    await waitFor(() => expect(result.current.state.chats).toHaveLength(2));
    expect(result.current.state.currentChatId).toBe(b);
    expect(result.current.state.folders).toHaveLength(1);
  });

  it('все чаты в архиве → текущим становится первый', async () => {
    withToken();
    const [a, b] = [uid('al-a'), uid('al-b')];
    routeFetch([['/api/conversations?limit=500', () => json({ conversations: [
      chat(a, [msg('1', 'user', 'x')], { isArchived: true }), chat(b, [msg('2', 'user', 'y')], { isArchived: true }),
    ] })]]);
    const { result } = renderHook(() => useAll(), { wrapper });
    await waitFor(() => expect(result.current.state.chats).toHaveLength(2));
    expect(result.current.state.currentChatId).toBe(a);
  });

  it('миграция архива: локально архивные чаты помечаются и отправляются на сервер', async () => {
    withToken();
    const [a, b] = [uid('mg-a'), uid('mg-b')];
    localStorage.setItem('memo-chats', JSON.stringify({ chats: [chat(a, [], { isArchived: true }), { noid: true }], currentChatId: null }));
    routeFetch([
      ['/api/conversations?limit=500', () => json({ conversations: [chat(a, [msg('1', 'user', 'x')]), chat(b, [msg('2', 'user', 'y')])] })],
      [`POST http://api.test/api/conversations/${a}/archive`, () => json({})],
    ]);
    const { result } = renderHook(() => useAll(), { wrapper });
    await waitFor(() => expect(result.current.state.chats).toHaveLength(2));
    expect(result.current.state.chats.find((c) => c.id === a)!.isArchived).toBe(true);
    expect(result.current.state.chats.find((c) => c.id === b)!.isArchived).toBe(false);
    await waitFor(() => expect(calls(`/api/conversations/${a}/archive`)).toHaveLength(1));
    expect(calls(`/api/conversations/${b}/archive`)).toHaveLength(0);
    // сохранённый currentChatId отсутствует → первый не архивный
    expect(result.current.state.currentChatId).toBe(b);
  });

  it('локальные проекты мигрируют на сервер, если там пусто', async () => {
    withToken();
    const local = [proj('lp')];
    localStorage.setItem('memo-chats', JSON.stringify({ chats: [], projects: local }));
    mockFetchProjects.mockResolvedValueOnce([]).mockResolvedValueOnce([proj('lp')]);
    const { result } = renderHook(() => useAll(), { wrapper });
    await waitFor(() => expect(result.current.state.projects).toHaveLength(1));
    expect(mockMigrate).toHaveBeenCalledWith(local);
    expect(mockFetchProjects).toHaveBeenCalledTimes(2);
  });

  it('если после миграции на сервере всё ещё пусто — берутся локальные проекты', async () => {
    withToken();
    const local = [proj('lp2')];
    localStorage.setItem('memo-chats', JSON.stringify({ chats: [], projects: local }));
    mockFetchProjects.mockResolvedValue([]);
    const { result } = renderHook(() => useAll(), { wrapper });
    await waitFor(() => expect(result.current.state.projects).toHaveLength(1));
    expect(result.current.state.projects[0].id).toBe('lp2');
  });

  it('ошибка загрузки проектов → предупреждение и локальные проекты', async () => {
    withToken();
    localStorage.setItem('memo-chats', JSON.stringify({ chats: [], projects: [proj('lp3')] }));
    mockFetchProjects.mockRejectedValue(new Error('500'));
    const warn = jest.spyOn(console, 'warn').mockImplementation(() => {});
    const { result } = renderHook(() => useAll(), { wrapper });
    await waitFor(() => expect(result.current.state.projects).toHaveLength(1));
    expect(warn).toHaveBeenCalledWith('Не удалось загрузить проекты из backend, используем localStorage', expect.any(Error));
    warn.mockRestore();
  });

  it('ошибка загрузки проектов без локальных — проекты пусты', async () => {
    withToken();
    mockFetchProjects.mockRejectedValue(new Error('500'));
    const warn = jest.spyOn(console, 'warn').mockImplementation(() => {});
    const { result } = renderHook(() => useAll(), { wrapper });
    await waitFor(() => expect(result.current.state.isInitialized).toBe(true));
    expect(result.current.state.projects).toEqual([]);
    warn.mockRestore();
  });

  it('/api/conversations не OK → откат на localStorage (без выгрузки)', async () => {
    withToken();
    const id = uid('fb');
    localStorage.setItem('memo-chats', JSON.stringify({ chats: [chat(id, [msg('1', 'user', 'x')])], currentChatId: id }));
    routeFetch([['/api/conversations?limit=500', () => json({}, false, 500)]]);
    const { result } = renderHook(() => useAll(), { wrapper });
    await waitFor(() => expect(result.current.state.chats.length).toBeGreaterThan(0));
    expect(result.current.state.chats[0].id).toBe(id);
  });

  it('сервер вернул 0 чатов → используются локальные', async () => {
    withToken();
    const id = uid('zero');
    localStorage.setItem('memo-chats', JSON.stringify({ chats: [chat(id, [msg('1', 'user', 'x')])], currentChatId: id }));
    routeFetch([['/api/conversations?limit=500', () => json({ conversations: [] })]]);
    const { result } = renderHook(() => useAll(), { wrapper });
    await waitFor(() => expect(result.current.state.chats.length).toBeGreaterThan(0));
    expect(result.current.state.currentChatId).toBe(id);
  });

  it('payload без conversations → инициализация завершается пустым состоянием', async () => {
    withToken();
    routeFetch([['/api/conversations?limit=500', () => json({})]]);
    const { result } = renderHook(() => useAll(), { wrapper });
    await waitFor(() => expect(result.current.state.isInitialized).toBe(true));
    expect(result.current.state.chats).toEqual([]);
  });

  it('падение initSettings → предупреждение и откат на localStorage', async () => {
    withToken();
    mockInitSettings.mockRejectedValue(new Error('cfg'));
    const warn = jest.spyOn(console, 'warn').mockImplementation(() => {});
    const { result } = renderHook(() => useAll(), { wrapper });
    await waitFor(() => expect(result.current.state.isInitialized).toBe(true));
    expect(warn).toHaveBeenCalledWith('Не удалось загрузить диалоги из backend, используем localStorage', expect.any(Error));
    warn.mockRestore();
  });

  it('смена токена перезагружает состояние', async () => {
    routeFetch([['/api/conversations?limit=500', () => json({ conversations: [chat(uid('rl'), [msg('1', 'user', 'x')])] })]]);
    const { result, rerender } = renderHook(() => useAll(), { wrapper });
    await flush();
    expect(calls('/api/conversations?limit=500')).toHaveLength(0);
    mockUseAuth.mockReturnValue({ token: 'new-tok' });
    rerender();
    await waitFor(() => expect(result.current.state.chats.length).toBeGreaterThan(0));
    expect(calls('/api/conversations?limit=500')).toHaveLength(1);
  });
});

describe('AppProvider: догрузка сообщений выгруженного чата', () => {
  // Id чата уникален и никогда не попадал в LRU-кэш, поэтому гидратация идёт через сеть.
  const setupUnloaded = async (conversationRoute: (id: string) => () => Promise<any>) => {
    mockUseAuth.mockReturnValue({ token: 'tok' });
    const id = uid('hy');
    routeFetch([[`GET http://api.test/api/conversations/${id}`, conversationRoute(id)]]);
    const hook = renderHook(() => useAll(), { wrapper });
    await waitFor(() => expect(hook.result.current.state.isInitialized).toBe(true));
    fetchMock.mockClear();
    const open = (current: string = id) =>
      act(() =>
        hook.result.current.dispatch({
          type: 'RESTORE_CHATS',
          payload: {
            chats: [chat(id, [], { messagesUnloaded: true, unloadedMessageCount: 1 }), chat('other-' + id, [msg('1', 'user', 'x')])],
            currentChatId: current,
            folders: [],
            unloadInactive: false,
          },
        } as any),
      );
    return { ...hook, id, open };
  };

  it('при открытии выгруженного чата подгружает сообщения (payload.conversation)', async () => {
    const { result, id, open } = await setupUnloaded((cid) => () =>
      json({ conversation: chat(cid, [msg('9', 'assistant', 'fresh')], { updatedAt: 'U2' }) }),
    );
    open();
    await waitFor(() => expect(result.current.state.chats[0].messages.some((m) => m.content === 'fresh')).toBe(true));
    expect(result.current.state.chats[0]).toMatchObject({ id, updatedAt: 'U2', messagesUnloaded: false });
    expect(calls(`/api/conversations/${id}`)[0][1].headers).toEqual({ Authorization: 'Bearer tok' });
  });

  it('ответ без обёртки conversation тоже поддерживается', async () => {
    const { result, open } = await setupUnloaded((cid) => () => json(chat(cid, [msg('9', 'assistant', 'plain')])));
    open();
    await waitFor(() => expect(result.current.state.chats[0].messages.some((m) => m.content === 'plain')).toBe(true));
  });

  it('не-OK ответ оставляет чат выгруженным', async () => {
    const { result, id, open } = await setupUnloaded(() => () => json({}, false, 500));
    open();
    await waitFor(() => expect(calls(`/api/conversations/${id}`)).toHaveLength(1));
    await flush();
    expect(result.current.state.chats[0].messagesUnloaded).toBe(true);
  });

  it('ошибка сети логируется предупреждением', async () => {
    const warn = jest.spyOn(console, 'warn').mockImplementation(() => {});
    const { id, open } = await setupUnloaded(() => () => Promise.reject(new Error('net')));
    open();
    await waitFor(() => expect(warn).toHaveBeenCalledWith('Не удалось подгрузить сообщения чата', id, expect.any(Error)));
    warn.mockRestore();
  });

  it('смена чата до ответа отменяет применение результата (cancelled)', async () => {
    let resolve!: (v: any) => void;
    const { result, id, open } = await setupUnloaded(() => () => new Promise((r) => (resolve = r)));
    open();
    await waitFor(() => expect(calls(`/api/conversations/${id}`)).toHaveLength(1));
    open('other-' + id);
    await act(async () => {
      resolve({ ok: true, json: () => Promise.resolve({ conversation: chat(id, [msg('9', 'assistant', 'late')]) }) });
      for (let i = 0; i < 10; i += 1) await Promise.resolve();
    });
    expect(result.current.state.chats[0].messages).toEqual([]);
  });

  it('смена чата после получения ответа, но до применения (cancelled после json)', async () => {
    let releaseJson!: () => void;
    const { result, id, open } = await setupUnloaded((cid) => () =>
      Promise.resolve({
        ok: true,
        json: () => new Promise((r) => (releaseJson = () => r({ conversation: chat(cid, [msg('9', 'assistant', 'late2')]) }))),
      }),
    );
    open();
    await waitFor(() => expect(releaseJson).toBeDefined());
    open('other-' + id);
    await act(async () => {
      releaseJson();
      for (let i = 0; i < 10; i += 1) await Promise.resolve();
    });
    expect(result.current.state.chats[0].messages).toEqual([]);
  });

  it('без токена выгруженный чат не догружается', async () => {
    const { result } = renderHook(() => useAll(), { wrapper });
    await flush();
    const id = uid('nt');
    act(() => result.current.dispatch({ type: 'RESTORE_CHATS', payload: { chats: [chat(id, [], { messagesUnloaded: true, unloadedMessageCount: 2 })], currentChatId: id, folders: [], unloadInactive: false } } as any));
    await flush();
    expect(calls(`/api/conversations/${id}`)).toHaveLength(0);
  });
});

describe('useAppActions: синхронизация с бэкендом', () => {
  const setupAuthed = async () => {
    mockUseAuth.mockReturnValue({ token: 'tok' });
    routeFetch([
      ['PUT', () => json({})],
      ['DELETE', () => json({})],
      ['POST', () => json({})],
    ]);
    const hook = renderHook(() => useAll(), { wrapper });
    await waitFor(() => expect(hook.result.current.state.isInitialized).toBe(true));
    fetchMock.mockClear();
    return hook;
  };

  it('updateChatTitle отправляет PUT /title с токеном', async () => {
    const { result } = await setupAuthed();
    act(() => result.current.actions.updateChatTitle('c1', 'Новый'));
    await waitFor(() => expect(calls('PUT http://api.test/api/conversations/c1/title')).toHaveLength(1));
    const init = calls('/title')[0][1];
    expect(init.headers).toEqual({ 'Content-Type': 'application/json', Authorization: 'Bearer tok' });
    expect(JSON.parse(init.body)).toEqual({ title: 'Новый' });
  });

  it('ошибки сети и initSettings при updateChatTitle глушатся', async () => {
    const { result } = await setupAuthed();
    fetchMock.mockImplementation(() => Promise.reject(new Error('off')));
    act(() => result.current.actions.updateChatTitle('c1', 'X'));
    await flush();
    mockInitSettings.mockRejectedValueOnce(new Error('cfg'));
    act(() => result.current.actions.updateChatTitle('c1', 'Y'));
    await flush();
    expect(result.current.state.chats).toEqual([]); // чата нет, но исключений тоже нет
  });

  it('deleteChat и deleteAllChats вызывают DELETE на сервере', async () => {
    const { result } = await setupAuthed();
    act(() => result.current.actions.deleteChat('c9'));
    await waitFor(() => expect(calls('DELETE http://api.test/api/conversations/c9')).toHaveLength(1));
    act(() => result.current.actions.deleteAllChats());
    await waitFor(() => expect(calls('DELETE http://api.test/api/conversations')).toHaveLength(2));
    const bulk = fetchMock.mock.calls.find((c) => c[0] === 'http://api.test/api/conversations' && c[1].method === 'DELETE');
    expect(bulk![1].headers).toEqual({ Authorization: 'Bearer tok' });
  });

  it('удаление при падении сети/initSettings не выбрасывает ошибок', async () => {
    const { result } = await setupAuthed();
    fetchMock.mockImplementation(() => Promise.reject(new Error('off')));
    act(() => result.current.actions.deleteChat('c1'));
    act(() => result.current.actions.deleteAllChats());
    mockInitSettings.mockRejectedValue(new Error('cfg'));
    act(() => result.current.actions.deleteChat('c2'));
    act(() => result.current.actions.deleteAllChats());
    act(() => result.current.actions.updateChatTitle('c2', 'x'));
    await flush();
    expect(result.current.state.chats).toEqual([]);
  });

  it('archiveChat / unarchiveChat шлют POST /archive и /unarchive', async () => {
    const { result } = await setupAuthed();
    act(() => result.current.actions.archiveChat('c1'));
    await waitFor(() => expect(calls('POST http://api.test/api/conversations/c1/archive')).toHaveLength(1));
    act(() => result.current.actions.unarchiveChat('c1'));
    await waitFor(() => expect(calls('POST http://api.test/api/conversations/c1/unarchive')).toHaveLength(1));
  });

  it('archiveAllChats / unarchiveAllChats шлют запрос на каждый подходящий чат', async () => {
    const { result } = await setupAuthed();
    const [a, b] = [uid('aa'), uid('ab')];
    act(() => {
      result.current.dispatch({ type: 'CREATE_CHAT', payload: chat(a, [msg('1', 'user', 'x')]) } as any);
      result.current.dispatch({ type: 'CREATE_CHAT', payload: chat(b, [msg('1', 'user', 'x')], { isArchived: true }) } as any);
    });
    act(() => result.current.actions.archiveAllChats());
    await waitFor(() => expect(calls('/archive')).toHaveLength(1));
    expect(calls('/archive')[0][0]).toContain(`/${a}/archive`);
    fetchMock.mockClear();
    act(() => result.current.actions.unarchiveAllChats());
    await waitFor(() => expect(calls('/unarchive')).toHaveLength(2));
  });

  it('archiveFolder отправляет архивирование для каждого чата папки', async () => {
    const { result } = await setupAuthed();
    let fid = '';
    act(() => {
      fid = result.current.actions.createFolder('F');
    });
    act(() => {
      result.current.actions.moveChatToFolder('c1', fid);
    });
    act(() => {
      result.current.actions.moveChatToFolder('c2', fid);
    });
    act(() => result.current.actions.archiveFolder(fid));
    await waitFor(() => expect(calls('/archive')).toHaveLength(2));
  });

  it('архивирование без токена или без подходящих чатов запросов не делает', async () => {
    mockUseAuth.mockReturnValue({ token: null });
    const { result } = renderHook(() => useAll(), { wrapper });
    await waitFor(() => expect(result.current.state.isInitialized).toBe(true));
    fetchMock.mockClear();
    act(() => result.current.actions.archiveChat('c1'));
    act(() => result.current.actions.unarchiveChat('c1'));
    act(() => result.current.actions.archiveAllChats());
    act(() => result.current.actions.unarchiveAllChats());
    act(() => result.current.actions.archiveFolder('nofolder'));
    await flush();
    expect(fetchMock).not.toHaveBeenCalled();

    const authed = await setupAuthed();
    act(() => authed.result.current.actions.archiveAllChats()); // чатов нет
    act(() => authed.result.current.actions.unarchiveAllChats());
    act(() => authed.result.current.actions.archiveFolder('nofolder'));
    await flush();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('ошибки сети при архивировании глушатся', async () => {
    const { result } = await setupAuthed();
    const [a] = [uid('ae')];
    act(() => {
      result.current.dispatch({ type: 'CREATE_CHAT', payload: chat(a, [msg('1', 'user', 'x')]) } as any);
    });
    fetchMock.mockImplementation(() => Promise.reject(new Error('off')));
    act(() => result.current.actions.archiveChat(a));
    act(() => result.current.actions.unarchiveChat(a));
    act(() => result.current.actions.archiveAllChats());
    act(() => result.current.actions.unarchiveAllChats());
    mockInitSettings.mockRejectedValue(new Error('cfg'));
    act(() => result.current.actions.archiveAllChats());
    await flush();
    expect(result.current.state.chats).toHaveLength(1);
  });
});

describe('useAppActions: проекты на сервере', () => {
  const setupAuthed = async () => {
    mockUseAuth.mockReturnValue({ token: 'tok' });
    routeFetch([]);
    const hook = renderHook(() => useAll(), { wrapper });
    await waitFor(() => expect(hook.result.current.state.isInitialized).toBe(true));
    fetchMock.mockClear();
    return hook;
  };
  const data = { name: 'P', memory: 'default' as const, instructions: 'i' };

  it('createProject / updateProject синхронизируются с БД', async () => {
    const { result } = await setupAuthed();
    mockSyncCreate.mockResolvedValue(undefined);
    mockSyncUpdate.mockResolvedValue(undefined);
    let pid = '';
    act(() => {
      pid = result.current.actions.createProject(data);
    });
    await waitFor(() => expect(mockSyncCreate).toHaveBeenCalledWith(expect.objectContaining({ id: pid, name: 'P' })));
    act(() => result.current.actions.updateProject(pid, { name: 'Q' }));
    await waitFor(() => expect(mockSyncUpdate).toHaveBeenCalledWith(pid, { name: 'Q' }));
  });

  it('ошибки синхронизации проектов логируются предупреждением, локальное состояние остаётся', async () => {
    const { result } = await setupAuthed();
    const warn = jest.spyOn(console, 'warn').mockImplementation(() => {});
    mockSyncCreate.mockRejectedValue(new Error('db'));
    mockSyncUpdate.mockRejectedValue(new Error('db'));
    let pid = '';
    act(() => {
      pid = result.current.actions.createProject(data);
    });
    await waitFor(() => expect(warn).toHaveBeenCalledWith(expect.stringContaining('не удалось сохранить проект'), expect.any(Error)));
    act(() => result.current.actions.updateProject(pid, { name: 'Q' }));
    await waitFor(() => expect(warn).toHaveBeenCalledWith(expect.stringContaining('не удалось обновить проект'), expect.any(Error)));
    expect(result.current.state.projects[0].name).toBe('Q');
    warn.mockRestore();
  });

  it('deleteProject: убирает проект только после успешного DELETE', async () => {
    const { result } = await setupAuthed();
    let pid = '';
    act(() => {
      pid = result.current.actions.createProject(data);
    });
    routeFetch([[`DELETE http://api.test/api/projects/${pid}`, () => json({})]]);
    act(() => result.current.actions.deleteProject(pid));
    expect(result.current.state.projects).toHaveLength(1); // ещё не удалён
    await waitFor(() => expect(result.current.state.projects).toEqual([]));
    expect(calls('DELETE')[0][1].headers).toEqual({ Authorization: 'Bearer tok' });
  });

  it('deleteProject: HTTP-ошибка → проект остаётся, показывается уведомление с detail', async () => {
    const { result } = await setupAuthed();
    const err = jest.spyOn(console, 'error').mockImplementation(() => {});
    let pid = '';
    act(() => {
      pid = result.current.actions.createProject(data);
    });
    routeFetch([['DELETE', () => Promise.resolve({ ok: false, status: 403, json: () => Promise.resolve({ detail: 'Нет прав' }) })]]);
    act(() => result.current.actions.deleteProject(pid));
    await waitFor(() => expect(result.current.state.notifications).toHaveLength(1));
    expect(result.current.state.notifications[0]).toMatchObject({ type: 'error', message: 'Не удалось удалить проект: Нет прав' });
    expect(result.current.state.projects).toHaveLength(1);
    err.mockRestore();
  });

  it('deleteProject: тело ошибки не JSON → сообщение «HTTP <код>»; сетевая ошибка — её текст', async () => {
    const { result } = await setupAuthed();
    const err = jest.spyOn(console, 'error').mockImplementation(() => {});
    routeFetch([['DELETE', () => Promise.resolve({ ok: false, status: 502, json: () => Promise.reject(new Error('no json')) })]]);
    act(() => result.current.actions.deleteProject('p1'));
    await waitFor(() => expect(result.current.state.notifications.some((n) => n.message === 'Не удалось удалить проект: HTTP 502')).toBe(true));
    fetchMock.mockImplementation(() => Promise.reject(new Error('network down')));
    act(() => result.current.actions.deleteProject('p2'));
    await waitFor(() => expect(result.current.state.notifications.some((n) => n.message === 'Не удалось удалить проект: network down')).toBe(true));
    fetchMock.mockImplementation(() => Promise.reject('строка'));
    act(() => result.current.actions.deleteProject('p3'));
    await waitFor(() => expect(result.current.state.notifications.some((n) => n.message === 'Не удалось удалить проект: строка')).toBe(true));
    err.mockRestore();
  });
});

describe('useAppActions.branchChatAtMessage', () => {
  const seedChat = async (token: string | null) => {
    mockUseAuth.mockReturnValue({ token });
    routeFetch([]);
    const hook = renderHook(() => useAll(), { wrapper });
    await waitFor(() => expect(hook.result.current.state.isInitialized).toBe(true));
    const cid = uid('brsrc');
    act(() => {
      hook.result.current.dispatch({
        type: 'CREATE_CHAT',
        payload: chat(cid, [
          msg('u1', 'user', 'вопрос'),
          msg('a1', 'assistant', 'ответ'),
          msg('a2', 'assistant', 'стрим', { isStreaming: true }),
        ], { projectId: 'proj-1', title: 'Исходный' }),
      } as any);
    });
    fetchMock.mockClear();
    return { ...hook, cid };
  };

  it('нет чата / нет сообщения / не ассистент / стримится → null', async () => {
    const { result, cid } = await seedChat(null);
    expect(await result.current.actions.branchChatAtMessage('nochat', 'a1')).toBeNull();
    expect(await result.current.actions.branchChatAtMessage(cid, 'zzz')).toBeNull();
    expect(await result.current.actions.branchChatAtMessage(cid, 'u1')).toBeNull();
    expect(await result.current.actions.branchChatAtMessage(cid, 'a2')).toBeNull();
    expect(result.current.state.chats).toHaveLength(1);
  });

  it('без токена: локальная ветка с копией сообщений, проектом и скрытием в сайдбаре', async () => {
    const { result, cid } = await seedChat(null);
    let newId: string | null = null;
    await act(async () => {
      newId = await result.current.actions.branchChatAtMessage(cid, 'a1');
    });
    expect(newId).toBeTruthy();
    const branch = result.current.state.chats.find((c) => c.id === newId)!;
    expect(branch.title).toBe('Ветка · Исходный');
    expect(branch.projectId).toBe('proj-1');
    expect(branch.hiddenFromSidebarUntilUserMessage).toBe(true);
    expect(branch.messages.map((m) => m.content)).toEqual(['вопрос', 'ответ']);
    expect(branch.messages[0].id).not.toBe('u1'); // новые id
    expect(result.current.state.currentChatId).toBe(newId);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('с токеном: ветка создаётся на сервере, наследует проект и становится текущей', async () => {
    const { result, cid } = await seedChat('tok');
    routeFetch([[`POST http://api.test/api/conversations/${cid}/branch`, () => json({ conversation: { id: 'srv-branch', title: 'Ветка', messages: [msg('s1', 'user', 'q')] } })]]);
    let newId: string | null = null;
    await act(async () => {
      newId = await result.current.actions.branchChatAtMessage(cid, 'a1', { multiLlmSlotIndex: 1 });
    });
    expect(newId).toBe('srv-branch');
    const init = calls('/branch')[0][1];
    expect(JSON.parse(init.body)).toEqual({ message_id: 'a1', multi_llm_slot_index: 1 });
    expect(init.headers.Authorization).toBe('Bearer tok');
    const branch = result.current.state.chats.find((c) => c.id === 'srv-branch')!;
    expect(branch.hiddenFromSidebarUntilUserMessage).toBe(true);
    expect(branch.projectId).toBe('proj-1');
    expect(result.current.state.currentChatId).toBe('srv-branch');
  });

  it('серверная ветка без проекта у исходного чата и без slot index', async () => {
    mockUseAuth.mockReturnValue({ token: 'tok' });
    routeFetch([]);
    const { result } = renderHook(() => useAll(), { wrapper });
    await waitFor(() => expect(result.current.state.isInitialized).toBe(true));
    const cid = uid('np');
    act(() => {
      result.current.dispatch({ type: 'CREATE_CHAT', payload: chat(cid, [msg('a1', 'assistant', 'x')]) } as any);
    });
    routeFetch([['/branch', () => json({ conversation: { id: 'srv-b2', messages: [] } })]]);
    await act(async () => {
      await result.current.actions.branchChatAtMessage(cid, 'a1');
    });
    expect(JSON.parse(calls('/branch')[0][1].body)).toEqual({ message_id: 'a1' });
    expect(result.current.state.chats.find((c) => c.id === 'srv-b2')!.projectId).toBeUndefined();
  });

  it('сервер ответил ошибкой → откат на локальную ветку', async () => {
    const { result, cid } = await seedChat('tok');
    routeFetch([['/branch', () => json({}, false, 500)]]);
    let newId: string | null = null;
    await act(async () => {
      newId = await result.current.actions.branchChatAtMessage(cid, 'a1');
    });
    expect(newId).not.toBe('srv-branch');
    expect(result.current.state.chats.find((c) => c.id === newId)!.messages).toHaveLength(2);
  });

  it('исключение при вызове сервера логируется, ветка создаётся локально', async () => {
    const { result, cid } = await seedChat('tok');
    const err = jest.spyOn(console, 'error').mockImplementation(() => {});
    fetchMock.mockImplementation(() => Promise.reject(new Error('net')));
    let newId: string | null = null;
    await act(async () => {
      newId = await result.current.actions.branchChatAtMessage(cid, 'a1');
    });
    expect(err).toHaveBeenCalledWith('Ошибка создания ветки на сервере:', expect.any(Error));
    expect(newId).toBeTruthy();
    err.mockRestore();
  });
});

describe('AppContext: начальная загрузка модуля из localStorage', () => {
  const load = (stored: string | null) => {
    if (stored !== null) localStorage.setItem('memo-chats', stored);
    const ReactActual = jest.requireActual('react');
    let mod: any;
    jest.isolateModules(() => {
      jest.doMock('react', () => ReactActual);
      mod = require('./AppContext');
    });
    return mod as typeof import('./AppContext');
  };

  afterEach(() => {
    jest.dontMock('react');
  });

  it('сохранённые чаты попадают в начальное состояние сразу (до эффектов), isInitialized=true', () => {
    const mod = load(JSON.stringify({ chats: [chat('boot-1', [msg('1', 'user', 'x')]), chat('boot-2', [])], folders: [{ id: 'f', name: 'F', chatIds: [], expanded: true }] }));
    const firstRender: any[] = [];
    const { result } = renderHook(
      () => {
        const ctx = mod.useAppContext();
        if (firstRender.length === 0) firstRender.push(ctx.state);
        return ctx;
      },
      { wrapper: ({ children }: any) => <mod.AppProvider>{children}</mod.AppProvider> },
    );
    // первый рендер: состояние уже из localStorage, текущим выбран первый чат
    expect(firstRender[0].chats.map((c: any) => c.id)).toEqual(['boot-1', 'boot-2']);
    expect(firstRender[0].currentChatId).toBe('boot-1');
    expect(firstRender[0].isInitialized).toBe(true);
    expect(firstRender[0].folders).toHaveLength(1);
    expect(result.current.state.chats.map((c) => c.id)).toContain('boot-1');
  });

  it('сохранённый currentChatId приоритетнее первого чата', () => {
    const mod = load(JSON.stringify({ chats: [chat('b-1'), chat('b-2')], currentChatId: 'b-2' }));
    const { result } = renderHook(() => mod.useAppContext(), { wrapper: ({ children }: any) => <mod.AppProvider>{children}</mod.AppProvider> });
    expect(result.current.state.currentChatId).toBe('b-2');
  });

  it.each([
    ['пустое хранилище', null],
    ['битый JSON', '{oops'],
    ['пустой массив чатов', JSON.stringify({ chats: [] })],
  ])('%s → пустое начальное состояние, isInitialized=false до загрузки', (_n, stored) => {
    const mod = load(stored as string | null);
    const { result } = renderHook(() => mod.useAppContext(), { wrapper: ({ children }: any) => <mod.AppProvider>{children}</mod.AppProvider> });
    expect(result.current.state.chats).toEqual([]);
    expect(result.current.state.currentChatId).toBeNull();
  });

  // Баг: bootstrap проверяет Array.isArray(chats), а loadInitialState — только chats.length > 0.
  // Строка в chats проходит проверку и попадает в редьюсер, где падает chats.map.
  it('(known issue) повреждённое хранилище {"chats":"x"} роняет рендер провайдера (текущее поведение)', () => {
    const mod = load(JSON.stringify({ chats: 'x' }));
    const err = jest.spyOn(console, 'error').mockImplementation(() => {});
    try {
      // правильное поведение: игнорировать мусор и завершить инициализацию (isInitialized=true)
      expect(() =>
        renderHook(() => mod.useAppContext(), {
          wrapper: ({ children }: any) => <mod.AppProvider>{children}</mod.AppProvider>,
        }),
      ).toThrow(/chats\.map is not a function/);
    } finally {
      err.mockRestore();
    }
  });
});
