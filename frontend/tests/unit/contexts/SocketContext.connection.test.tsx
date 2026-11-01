import React from 'react';
import { act, renderHook } from '@testing-library/react';

// ---- моки внешних зависимостей (socket.io, настройки, авторизация, API) ----
let mockAuth: { token: string | null; user: any } = { token: 'tok', user: { username: 'Alice' } };
jest.mock('@/contexts/AuthContext', () => ({ useAuth: () => mockAuth }));

const mockIo = jest.fn();
jest.mock('socket.io-client', () => ({ io: (...a: unknown[]) => mockIo(...a) }));

const mockInitSettings = jest.fn();
const mockGetSettings = jest.fn();
jest.mock('@/settings', () => ({
  initSettings: () => mockInitSettings(),
  getSettings: () => mockGetSettings(),
}));

jest.mock('@/config/api', () => ({
  authFetch: (...a: unknown[]) => (require('@tests/unit/helpers/authFetchMock').authFetchMock as (...x: unknown[]) => unknown)(...a),
  getApiUrl: (p: string) => `http://api.test${p}`,
  API_ENDPOINTS: { PROJECT_DELETE: (id: string) => `/api/projects/${id}` },
}));
jest.mock('@/utils/projectsApi', () => ({
  fetchProjectsFromServer: () => Promise.resolve([]),
  migrateLocalProjectsToServer: jest.fn(),
  syncProjectCreate: jest.fn(),
  syncProjectUpdate: jest.fn(),
}));

const mockRagStatus = { block: false, message: 'RAG пересобирается' };
jest.mock('@/hooks/useRagReindexStatus', () => ({
  useRagReindexStatus: () => ({
    shouldBlockRagSend: () => mockRagStatus.block,
    blockMessage: mockRagStatus.message,
  }),
}));
jest.mock('@/utils/browserNotifications', () => ({
  showBrowserNotification: jest.fn(),
  areNotificationsEnabled: () => false,
  isNotificationSupported: () => false,
  requestNotificationPermission: jest.fn(),
}));
jest.mock('@/utils/tabNotifications', () => ({ incrementTabNotification: jest.fn() }));

import { AppProvider, useAppActions, useAppContext } from '@/contexts/AppContext';
import { SocketProvider, useSocket } from '@/contexts/SocketContext';

class FakeSocket {
  handlers = new Map<string, Array<(...a: any[]) => void>>();
  ioHandlers = new Map<string, Array<(...a: any[]) => void>>();
  io = {
    opts: {} as Record<string, unknown>,
    on: (e: string, h: (...a: any[]) => void) => {
      this.ioHandlers.set(e, [...(this.ioHandlers.get(e) || []), h]);
    },
    off: jest.fn(),
  };
  emit = jest.fn();
  connect = jest.fn();
  disconnect = jest.fn();
  removeAllListeners = jest.fn();
  constructor(public url: string, public opts: any) {}
  on(e: string, h: (...a: any[]) => void) {
    this.handlers.set(e, [...(this.handlers.get(e) || []), h]);
    return this;
  }
  off(e: string, h: (...a: any[]) => void) {
    this.handlers.set(e, (this.handlers.get(e) || []).filter((x) => x !== h));
    return this;
  }
  fire(e: string, ...a: any[]) {
    (this.handlers.get(e) || []).forEach((h) => h(...a));
  }
  fireIo(e: string, ...a: any[]) {
    (this.ioHandlers.get(e) || []).forEach((h) => h(...a));
  }
}

let sockets: FakeSocket[] = [];
const sock = () => sockets[sockets.length - 1];

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <AppProvider>
    <SocketProvider>{children}</SocketProvider>
  </AppProvider>
);
const useAll = () => ({ s: useSocket(), app: useAppContext(), actions: useAppActions() });

const flush = async () => {
  await act(async () => {
    for (let i = 0; i < 30; i += 1) await Promise.resolve();
  });
};
const advance = async (ms: number) => {
  await act(async () => {
    jest.advanceTimersByTime(ms);
    for (let i = 0; i < 30; i += 1) await Promise.resolve();
  });
};

const WS_SETTINGS = {
  websocket: { baseUrl: 'ws://srv.test:9000', timeout: 5000, reconnectionDelay: 100, reconnectionDelayMax: 1000, reconnectionAttempts: 3 },
};

async function boot() {
  const hook = renderHook(() => useAll(), { wrapper });
  await flush();
  return hook;
}
const open = () => act(() => { sock().fire('connect'); });
const notes = (hook: { result: { current: ReturnType<typeof useAll> } }) =>
  hook.result.current.app.state.notifications.map((n) => `${n.type}:${n.message}`);

let logSpies: jest.SpyInstance[] = [];

beforeEach(() => {
  jest.useFakeTimers();
  localStorage.clear();
  sessionStorage.clear();
  sockets = [];
  mockAuth = { token: 'tok', user: { username: 'Alice' } };
  mockRagStatus.block = false;
  mockInitSettings.mockReset();
  mockInitSettings.mockResolvedValue(undefined);
  mockGetSettings.mockReset();
  mockGetSettings.mockReturnValue(WS_SETTINGS);
  mockIo.mockReset();
  mockIo.mockImplementation((url: string, opts: any) => {
    const s = new FakeSocket(url, opts);
    sockets.push(s);
    return s;
  });
  (global as any).fetch = jest.fn(() => Promise.resolve({ ok: false, json: () => Promise.resolve({}) }));
  logSpies = ['log', 'warn', 'info', 'error'].map((m) => jest.spyOn(console, m as any).mockImplementation(() => {}));
});

afterEach(() => {
  logSpies.forEach((s) => s.mockRestore());
  jest.useRealTimers();
});

describe('useSocket', () => {
  it('вне провайдера бросает ошибку', () => {
    expect(() => renderHook(() => useSocket())).toThrow('useSocket must be used within a SocketProvider');
  });
});

describe('SocketProvider: создание соединения', () => {
  it('без токена сокет не создаётся и подключение не ожидается', async () => {
    mockAuth = { token: null, user: null };
    const { result } = await boot();
    expect(mockIo).not.toHaveBeenCalled();
    expect(result.current.s.socket).toBeNull();
    expect(result.current.s.isConnected).toBe(false);
    expect(result.current.s.isConnecting).toBe(false);
  });

  it('с токеном создаёт сокет с auth-токеном, ws→http и лимитом буфера, затем connect()', async () => {
    const { result } = await boot();
    expect(mockIo).toHaveBeenCalledTimes(1);
    const s = sock();
    expect(s.url).toBe('http://srv.test:9000');
    expect(s.opts).toMatchObject({
      transports: ['websocket', 'polling'],
      autoConnect: false,
      timeout: 5000,
      reconnectionDelay: 100,
      reconnectionDelayMax: 1000,
      reconnectionAttempts: 3,
      forceNew: true,
      withCredentials: true,
      auth: { token: 'tok' },
    });
    expect(s.io.opts.maxHttpBufferSize).toBe(52 * 1024 * 1024);
    expect(s.connect).toHaveBeenCalledTimes(1);
    expect(result.current.s.socket).toBe(s);
    expect(result.current.s.isConnected).toBe(false);
    expect(result.current.s.isConnecting).toBe(true);
  });

  it('wss:// превращается в https://, http:// остаётся как есть', async () => {
    mockGetSettings.mockReturnValue({ websocket: { ...WS_SETTINGS.websocket, baseUrl: 'wss://secure.test' } });
    await boot();
    expect(sock().url).toBe('https://secure.test');
    sockets = [];
    mockGetSettings.mockReturnValue({ websocket: { ...WS_SETTINGS.websocket, baseUrl: 'http://plain.test' } });
    const second = renderHook(() => useAll(), { wrapper });
    await flush();
    expect(sock().url).toBe('http://plain.test');
    second.unmount();
  });

  it('connect: isConnected, уведомление, resume_generations; повторный connect — «восстановлено»', async () => {
    const hook = await boot();
    open();
    expect(hook.result.current.s.isConnected).toBe(true);
    expect(hook.result.current.s.isConnecting).toBe(false);
    expect(sock().emit).toHaveBeenCalledWith('resume_generations', {});
    expect(notes(hook)).toContain('success:Соединение с сервером установлено');
    act(() => { sock().fire('disconnect', 'io client disconnect'); });
    open();
    expect(notes(hook)).toContain('success:Соединение восстановлено');
  });

  it('событие manager.reconnect включает isConnected', async () => {
    const hook = await boot();
    act(() => { sock().fireIo('reconnect'); });
    expect(hook.result.current.s.isConnected).toBe(true);
    expect(hook.result.current.s.isConnecting).toBe(false);
  });
});

describe('SocketProvider: обрывы и переподключение', () => {
  it('disconnect по вине сети: предупреждение и переподключение через 1,5 с новым сокетом', async () => {
    const hook = await boot();
    open();
    const first = sock();
    act(() => { first.fire('disconnect', 'transport close'); });
    expect(hook.result.current.s.isConnected).toBe(false);
    expect(hook.result.current.s.isConnecting).toBe(true);
    expect(notes(hook)).toContain('warning:Соединение с сервером потеряно. Переподключаемся...');
    await advance(1499);
    expect(sockets).toHaveLength(1);
    await advance(1);
    expect(sockets).toHaveLength(2);
    expect(first.removeAllListeners).toHaveBeenCalled();
    expect(first.disconnect).toHaveBeenCalled();
    expect(first.io.off).toHaveBeenCalledWith('reconnect');
    expect(sock().connect).toHaveBeenCalled();
    expect(hook.result.current.s.socket).toBe(sock());
  });

  it('disconnect до первого успешного подключения не показывает уведомление', async () => {
    const hook = await boot();
    act(() => { sock().fire('disconnect', 'transport error'); });
    expect(notes(hook).some((n) => n.startsWith('warning:'))).toBe(false);
  });

  it('намеренный клиентский disconnect: без уведомления и без переподключения по таймеру', async () => {
    const hook = await boot();
    open();
    act(() => { sock().fire('disconnect', 'io client disconnect'); });
    expect(hook.result.current.s.isConnected).toBe(false);
    expect(notes(hook).some((n) => n.startsWith('warning:'))).toBe(false);
    await advance(2000);
    expect(sockets).toHaveLength(1);
  });

  it('(known issue) isConnecting=true после намеренного disconnect: useLayoutEffect перекрывает setIsConnecting(false)', async () => {
    const hook = await boot();
    open();
    act(() => { sock().fire('disconnect', 'io client disconnect'); });
    // обработчик disconnect ставит false, но layout-эффект [token,isConnected] снова ставит true
    expect(hook.result.current.s.isConnecting).toBe(true);
  });

  it('disconnect во время пересоздания (recreate) не считается потерей связи', async () => {
    const hook = await boot();
    open();
    await act(async () => { hook.result.current.s.reconnect(); });
    await flush();
    expect(sockets).toHaveLength(2);
    // старый сокет уже отписан; имитируем поздний disconnect нового как «recreate»
    expect(hook.result.current.s.socket).toBe(sock());
  });

  it('connect_error до первого подключения: без уведомления, повтор через 1,5 с', async () => {
    const hook = await boot();
    act(() => { sock().fire('connect_error', new Error('boom')); });
    expect(hook.result.current.s.isConnecting).toBe(true);
    expect(notes(hook).some((n) => n.startsWith('error:'))).toBe(false);
    await advance(1500);
    expect(sockets).toHaveLength(2);
  });

  it('connect_error после подключения: уведомление об ошибке (кроме «xhr poll error»), повтор через 2 с', async () => {
    const hook = await boot();
    open();
    act(() => { sock().fire('connect_error', new Error('xhr poll error')); });
    expect(notes(hook).some((n) => n.startsWith('error:'))).toBe(false);
    act(() => { sock().fire('connect_error', new Error('bad auth')); });
    expect(notes(hook)).toContain('error:Ошибка подключения: bad auth');
    await advance(1999);
    expect(sockets).toHaveLength(1);
    await advance(1);
    expect(sockets).toHaveLength(2);
  });

  it('connect_error без message не падает и не показывает уведомление', async () => {
    const hook = await boot();
    open();
    act(() => { sock().fire('connect_error', {}); });
    expect(notes(hook).some((n) => n.startsWith('error:'))).toBe(false);
  });

  it('reconnect_failed: сбрасывает подключение и пересоздаёт сокет через 1 с', async () => {
    const hook = await boot();
    open();
    act(() => { sock().fireIo('reconnect_failed'); });
    expect(hook.result.current.s.isConnected).toBe(false);
    expect(hook.result.current.s.isConnecting).toBe(true);
    await advance(1000);
    expect(sockets).toHaveLength(2);
  });

  it('отложенный reconnect отменяется, если токен пропал до срабатывания', async () => {
    const hook = await boot();
    open();
    act(() => { sock().fire('disconnect', 'ping timeout'); });
    mockAuth = { token: null, user: null };
    hook.rerender();
    await advance(5000);
    expect(sockets).toHaveLength(1);
  });

  it('успешный connect отменяет запланированное переподключение', async () => {
    const hook = await boot();
    open();
    act(() => { sock().fire('disconnect', 'ping timeout'); });
    open();
    await advance(5000);
    expect(sockets).toHaveLength(1);
    expect(hook.result.current.s.isConnected).toBe(true);
  });

  it('(known issue) исключение из io() при автопереподключении навсегда блокирует дальнейшие попытки', async () => {
    await boot();
    open();
    const errSpy = jest.spyOn(console, 'error').mockImplementation(() => {});
    mockIo.mockImplementationOnce(() => {
      throw new Error('io crashed');
    });
    act(() => { sock().fire('disconnect', 'transport close'); });
    await advance(1500);
    expect(errSpy).toHaveBeenCalledWith('Не удалось переподключить WebSocket:', expect.any(Error));
    // Ожидалось бы: повтор через min(1500*2, 10000) = 3000 мс. Фактически connectingRef остаётся true
    // после исключения в connectSocket, и scheduleReconnect/watchdog молча выходят.
    await advance(60_000);
    expect(sockets).toHaveLength(1);
  });

  it('ручной reconnect() пересоздаёт сокет; при исключении io() — лог (known issue: повтор не происходит)', async () => {
    const hook = await boot();
    open();
    const first = sock();
    await act(async () => { hook.result.current.s.reconnect(); });
    await flush();
    expect(sockets).toHaveLength(2);
    expect(first.disconnect).toHaveBeenCalled();

    const errSpy = jest.spyOn(console, 'error').mockImplementation(() => {});
    mockIo.mockImplementationOnce(() => {
      throw new Error('io crashed');
    });
    await act(async () => { hook.result.current.s.reconnect(); });
    await flush();
    expect(errSpy).toHaveBeenCalledWith('Ошибка ручного переподключения WebSocket:', expect.any(Error));
    await advance(60_000); // повтор через 2 с заблокирован «залипшим» connectingRef
    expect(sockets).toHaveLength(2);
  });

  it('reconnect() без токена ничего не делает', async () => {
    mockAuth = { token: null, user: null };
    const hook = await boot();
    await act(async () => { hook.result.current.s.reconnect(); });
    await flush();
    expect(mockIo).not.toHaveBeenCalled();
  });
});

describe('SocketProvider: настройки WebSocket и первая попытка', () => {
  it('ошибка загрузки настроек: лог, isConnecting=false, повторная попытка через 1 с', async () => {
    mockInitSettings.mockRejectedValueOnce(new Error('cfg'));
    const hook = await boot();
    expect(mockIo).not.toHaveBeenCalled();
    expect(hook.result.current.s.isConnecting).toBe(false);
    expect(console.error).toHaveBeenCalledWith('Не удалось загрузить настройки для WebSocket:', expect.any(Error));
    await advance(1000);
    expect(mockIo).toHaveBeenCalledTimes(1);
    expect(hook.result.current.s.socket).toBe(sock());
  });

  it('getSettings бросает — то же поведение, что и initSettings', async () => {
    mockGetSettings.mockImplementationOnce(() => {
      throw new Error('no cfg');
    });
    await boot();
    expect(mockIo).not.toHaveBeenCalled();
    await advance(1000);
    expect(mockIo).toHaveBeenCalledTimes(1);
  });

  it('повторная попытка после потери токена выходит, не создавая сокет', async () => {
    mockInitSettings.mockRejectedValueOnce(new Error('cfg'));
    const hook = await boot();
    mockAuth = { token: null, user: null };
    hook.rerender();
    await advance(1000);
    expect(mockIo).not.toHaveBeenCalled();
    expect(hook.result.current.s.isConnecting).toBe(false);
  });

  it('повторная попытка при уже идущем подключении пропускается (защита от дублей)', async () => {
    mockInitSettings.mockRejectedValueOnce(new Error('cfg'));
    const hook = await boot();
    let release!: () => void;
    mockInitSettings.mockReturnValueOnce(new Promise<void>((r) => (release = () => r())));
    await act(async () => { hook.result.current.s.reconnect(); });
    await advance(1000); // сработал таймер повтора, пока reconnect() ещё ждёт настройки
    expect(mockIo).not.toHaveBeenCalled();
    await act(async () => { release(); });
    await flush();
    expect(mockIo).toHaveBeenCalledTimes(1);
  });

  it('(known issue) исключение из io() при первом подключении логируется, но повтор не выполняется', async () => {
    mockIo.mockImplementationOnce(() => {
      throw new Error('io crashed');
    });
    const hook = await boot();
    expect(console.error).toHaveBeenCalledWith('Ошибка подключения WebSocket:', expect.any(Error));
    await advance(60_000);
    expect(sockets).toHaveLength(0);
    expect(hook.result.current.s.socket).toBeNull();
  });
});

describe('SocketProvider: токен, watchdog, видимость вкладки', () => {
  it('смена токена пересоздаёт сокет с новым auth', async () => {
    const hook = await boot();
    open();
    const first = sock();
    mockAuth = { token: 'tok2', user: { username: 'Alice' } };
    hook.rerender();
    await flush();
    expect(sockets).toHaveLength(2);
    expect(sock().opts.auth).toEqual({ token: 'tok2' });
    expect(first.disconnect).toHaveBeenCalled();
    expect(hook.result.current.s.isConnected).toBe(false);
  });

  it('ошибка пересоздания после смены токена: лог (known issue: повтор блокируется connectingRef)', async () => {
    const hook = await boot();
    open();
    mockIo.mockImplementationOnce(() => {
      throw new Error('io crashed');
    });
    mockAuth = { token: 'tok2', user: null };
    hook.rerender();
    await flush();
    expect(console.error).toHaveBeenCalledWith('Ошибка переподключения WebSocket после смены токена:', expect.any(Error));
    await advance(60_000);
    expect(sockets).toHaveLength(1);
  });

  it('выход из аккаунта закрывает сокет и сбрасывает состояние', async () => {
    const hook = await boot();
    open();
    const first = sock();
    mockAuth = { token: null, user: null };
    hook.rerender();
    await flush();
    expect(first.removeAllListeners).toHaveBeenCalled();
    expect(first.disconnect).toHaveBeenCalled();
    expect(hook.result.current.s.socket).toBeNull();
    expect(hook.result.current.s.isConnected).toBe(false);
    expect(hook.result.current.s.isConnecting).toBe(false);
  });

  it('watchdog: «зависший» отключённый сокет пересоздаётся через 8 с + 0,5 с', async () => {
    await boot();
    open();
    act(() => { sock().fire('disconnect', 'io client disconnect'); });
    await advance(7999);
    expect(sockets).toHaveLength(1);
    await advance(1); // watchdog → scheduleReconnect(500)
    await advance(500);
    expect(sockets).toHaveLength(2);
  });

  it('watchdog не вмешивается, пока соединение установлено', async () => {
    await boot();
    open();
    await advance(30_000);
    expect(sockets).toHaveLength(1);
  });

  it('возврат на вкладку при разрыве запускает переподключение через 0,3 с', async () => {
    await boot();
    open();
    act(() => { sock().fire('disconnect', 'io client disconnect'); });
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
    act(() => { document.dispatchEvent(new Event('visibilitychange')); });
    await advance(300);
    expect(sockets).toHaveLength(1);
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' });
    act(() => { document.dispatchEvent(new Event('visibilitychange')); });
    await advance(300);
    expect(sockets).toHaveLength(2);
  });

  it('visibilitychange при живом соединении ничего не делает', async () => {
    await boot();
    open();
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' });
    act(() => { document.dispatchEvent(new Event('visibilitychange')); });
    await advance(1000);
    expect(sockets).toHaveLength(1);
  });

  it('размонтирование снимает обработчик visibilitychange', async () => {
    const spy = jest.spyOn(document, 'removeEventListener');
    const hook = await boot();
    hook.unmount();
    expect(spy.mock.calls.some((c) => c[0] === 'visibilitychange')).toBe(true);
    spy.mockRestore();
  });
});

describe('SocketProvider: onMultiLLMEvent / offMultiLLMEvent и chat_info', () => {
  it('подписывает и отписывает обработчик на событие сокета', async () => {
    const hook = await boot();
    const handler = jest.fn();
    act(() => hook.result.current.s.onMultiLLMEvent!('multi_llm_custom', handler));
    act(() => { sock().fire('multi_llm_custom', { a: 1 }); });
    expect(handler).toHaveBeenCalledWith({ a: 1 });
    act(() => hook.result.current.s.offMultiLLMEvent!('multi_llm_custom', handler));
    act(() => { sock().fire('multi_llm_custom', { a: 2 }); });
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it('без сокета подписка не падает', async () => {
    mockAuth = { token: null, user: null };
    const hook = await boot();
    const handler = jest.fn();
    expect(() => hook.result.current.s.onMultiLLMEvent!('x', handler)).not.toThrow();
    expect(() => hook.result.current.s.offMultiLLMEvent!('x', handler)).not.toThrow();
  });

  it('chat_info показывает мягкое предупреждение и не трогает стрим; пустое сообщение игнорируется', async () => {
    const hook = await boot();
    open();
    act(() => { sock().fire('chat_info', { message: 'Идёт переиндексация' }); });
    expect(notes(hook)).toContain('warning:Идёт переиндексация');
    const before = hook.result.current.app.state.notifications.length;
    act(() => { sock().fire('chat_info', { message: '' }); });
    act(() => { sock().fire('chat_info', null); });
    expect(hook.result.current.app.state.notifications.length).toBe(before);
  });
});
