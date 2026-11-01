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
const mockBrowser = { enabled: false, supported: false, show: jest.fn() };
jest.mock('@/utils/browserNotifications', () => ({
  showBrowserNotification: (...a: unknown[]) => mockBrowser.show(...a),
  areNotificationsEnabled: () => mockBrowser.enabled,
  isNotificationSupported: () => mockBrowser.supported,
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
  mockBrowser.enabled = false;
  mockBrowser.supported = false;
  mockBrowser.show.mockReset();
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

type Hook = { result: { current: ReturnType<typeof useAll> } };

const emitsOf = (name: string) => sock().emit.mock.calls.filter((c: any[]) => c[0] === name).map((c: any[]) => c[1]);
const lastEmit = (name: string) => {
  const all = emitsOf(name);
  return all[all.length - 1];
};
const chatOf = (hook: Hook, id: string) => hook.result.current.app.state.chats.find((c) => c.id === id)!;
const server = (event: string, data?: any) => {
  act(() => {
    sock().fire(event, data);
  });
};
const send = (hook: Hook, ...args: Parameters<ReturnType<typeof useAll>['s']['sendMessage']>) =>
  act(() => {
    hook.result.current.s.sendMessage(...args);
  });
const assistant = (hook: Hook, cid: string) => chatOf(hook, cid).messages.filter((m) => m.role === 'assistant');

async function ready() {
  const hook = await boot();
  open();
  let cid = '';
  act(() => {
    cid = hook.result.current.actions.createChat('Чат');
  });
  return { hook, cid };
}
/** Ручное переподключение: обработчики нового сокета замыкаются на актуальное состояние чатов. */
async function reconnectFresh(hook: Hook) {
  await act(async () => {
    hook.result.current.s.reconnect();
  });
  await flush();
  open();
}

describe('переподключение: обработчики видят чаты на момент создания сокета', () => {
  it('merge multi-LLM сохраняет варианты слота и добавляет новую модель', async () => {
    const { hook, cid } = await ready();
    let aid = '';
    act(() => {
      aid = hook.result.current.actions.addMessage(cid, {
        role: 'assistant', content: '', timestamp: 't', isStreaming: false,
        multiLLMResponses: [
          { model: 'm1', content: 'b', alternativeResponses: ['a', 'b'], currentResponseIndex: 1, generationStartedAtMs: 1 },
          { model: 'm3', content: 'три', generationStartedAtMs: 1, generationDurationSec: 4 },
        ],
      } as any);
    });
    await reconnectFresh(hook);
    act(() => hook.result.current.s.regenerateMultiLlmSlot('q', aid, cid, 'm1'));
    const rid = lastEmit('chat_message').request_id;
    server('multi_llm_chunk', { request_id: rid, model: 'm1', accumulated: 'новый' });
    let slots = chatOf(hook, cid).messages[0].multiLLMResponses!;
    expect(slots.find((s) => s.model === 'm1')).toMatchObject({
      content: 'новый', alternativeResponses: ['a', 'новый'], currentResponseIndex: 1, isStreaming: true,
    });
    expect(slots.find((s) => s.model === 'm3')).toMatchObject({ content: 'три', generationDurationSec: 4 });

    // модель, которой не было в сообщении, добавляется в конец
    server('multi_llm_chunk', { request_id: rid, model: 'm9', accumulated: 'девять' });
    slots = chatOf(hook, cid).messages[0].multiLLMResponses!;
    expect(slots.map((s) => s.model)).toEqual(['m1', 'm3', 'm9']);
    expect(slots[2]).toMatchObject({ content: 'девять', isStreaming: true });

    // завершение слота фиксирует длительность
    server('multi_llm_complete', { request_id: rid, model: 'm1', response: 'финал', total: 1 });
    expect(chatOf(hook, cid).messages[0].multiLLMResponses!.find((s) => s.model === 'm1')!.isStreaming).toBe(false);
  });

  it('без переподключения merge читает свежий чат: варианты слота сохраняются при стриме', async () => {
    const { hook, cid } = await ready();
    let aid = '';
    act(() => {
      aid = hook.result.current.actions.addMessage(cid, {
        role: 'assistant', content: '', timestamp: 't', isStreaming: false,
        multiLLMResponses: [{ model: 'm1', content: 'b', alternativeResponses: ['a', 'b'], currentResponseIndex: 1 }],
      } as any);
    });
    act(() => hook.result.current.s.regenerateMultiLlmSlot('q', aid, cid, 'm1'));
    const rid = lastEmit('chat_message').request_id;
    server('multi_llm_chunk', { request_id: rid, model: 'm1', accumulated: 'новый' });
    const slot = chatOf(hook, cid).messages[0].multiLLMResponses![0];
    expect(slot.content).toBe('новый');
    expect(slot.alternativeResponses).toEqual(['a', 'новый']);
  });

  it('complete без плейсхолдера находит незавершённое сообщение чата и дописывает в него', async () => {
    const { hook, cid } = await ready();
    let sid = '';
    act(() => {
      sid = hook.result.current.actions.addMessage(cid, { role: 'assistant', content: 'недописанное', timestamp: 't', isStreaming: true });
    });
    await reconnectFresh(hook);
    send(hook, 'q', cid, false);
    const rid = lastEmit('chat_message').request_id;
    server('chat_complete', { request_id: rid, response: 'итог', generation_duration_sec: 3, document_search: { query: 'q' } });
    const msgs = assistant(hook, cid);
    expect(msgs).toHaveLength(1);
    expect(msgs[0]).toMatchObject({ id: sid, content: 'итог', isStreaming: false, generationDurationSec: 3 });
  });

  it('complete не дублирует уже существующий такой же ответ', async () => {
    const { hook, cid } = await ready();
    act(() => {
      hook.result.current.actions.addMessage(cid, { role: 'assistant', content: 'итог', timestamp: 't', isStreaming: false });
    });
    await reconnectFresh(hook);
    send(hook, 'q', cid, false);
    const rid = lastEmit('chat_message').request_id;
    server('chat_complete', { request_id: rid, response: 'итог' });
    expect(assistant(hook, cid)).toHaveLength(1);
  });

  it('chat_agent_update без отслеживаемого сообщения берёт последнее сообщение ассистента из чата', async () => {
    const { hook, cid } = await ready();
    let aid = '';
    act(() => {
      aid = hook.result.current.actions.addMessage(cid, { role: 'assistant', content: 'прежний', timestamp: 't' });
    });
    await reconnectFresh(hook);
    send(hook, 'q', cid, false);
    const rid = lastEmit('chat_message').request_id;
    server('chat_agent_update', { request_id: rid, agent_name: 'Саб', index: 1, total: 2 });
    expect(chatOf(hook, cid).messages.find((m) => m.id === aid)!.chainCurrentAgent).toMatchObject({ agentName: 'Саб' });
  });

  it('завершение сессии: socket.disconnect() вызывается у активного (текущего) сокета', async () => {
    const realLocation = window.location;
    delete (window as any).location;
    (window as any).location = { href: '/chat' };
    const { hook, cid } = await ready();
    const first = sock();
    await reconnectFresh(hook);
    const second = sock();
    expect(second).not.toBe(first);
    send(hook, 'q', cid);
    const rid = lastEmit('chat_message').request_id;
    first.disconnect.mockClear();
    server('chat_error', { request_id: rid, error: 'Не авторизован' });
    expect(second.disconnect).toHaveBeenCalled(); // активный сокет закрыт
    expect(first.disconnect).not.toHaveBeenCalled(); // старый сокет не трогаем
    expect(hook.result.current.s.socket).toBeNull();
    expect(window.location.href).toBe('/login');
    (window as any).location = realLocation;
  });
});

describe('завершение сессии во время генерации', () => {
  beforeEach(() => {
    delete (window as any).location;
    (window as any).location = { href: '/chat' };
  });

  it('сбрасывает multi-LLM сообщение и принимает дальнейшие события без идентификаторов как «осиротевшие»', async () => {
    const hook = await boot();
    open();
    let cid = '';
    act(() => {
      cid = hook.result.current.actions.createChat('Чат');
    });
    send(hook, 'q', cid, true, undefined, true);
    const rid = lastEmit('chat_message').request_id;
    server('multi_llm_chunk', { request_id: rid, model: 'm1', accumulated: 'a' });
    server('chat_error', { request_id: rid, error: 'Сессия завершена' });
    const msg = assistant(hook, cid)[0];
    expect(msg.isStreaming).toBe(false);
    expect(hook.result.current.app.state.loadingChatIds).toEqual([]);
    // событие без идентификаторов после сброса не создаёт сообщений
    server('chat_chunk', { accumulated: 'x' });
    expect(assistant(hook, cid)).toHaveLength(1);
  });
});

describe('мелкие ветки управления соединением', () => {
  it('ошибка повторной попытки подключения после сбоя настроек логируется', async () => {
    mockInitSettings.mockRejectedValueOnce(new Error('cfg'));
    mockIo.mockImplementationOnce(() => {
      throw new Error('io crashed');
    });
    await boot();
    await advance(1000);
    expect(console.error).toHaveBeenCalledWith('Повторная попытка подключения WebSocket завершилась ошибкой:', expect.any(Error));
  });

  it('(known issue) первый реальный разрыв после ручного reconnect() не считается потерей связи: нет уведомления и таймера переподключения', async () => {
    const { hook } = await ready();
    await reconnectFresh(hook);
    expect(sockets).toHaveLength(2);
    act(() => { sock().fire('disconnect', 'transport close'); });
    // флаг socketRecreateIntentRef остался true после пересоздания и «съедает» первый настоящий disconnect
    expect(notes(hook).some((n) => n.startsWith('warning:'))).toBe(false);
    await advance(1600);
    expect(sockets).toHaveLength(2);
    // второй разрыв обрабатывается штатно
    open();
    act(() => { sock().fire('disconnect', 'transport close'); });
    expect(notes(hook)).toContain('warning:Соединение с сервером потеряно. Переподключаемся...');
  });

  it('запоминает не более 300 завершённых запросов (самые старые забываются)', async () => {
    const { hook } = await ready();
    server('generation_resume', { request_id: 'first', generations: [] });
    for (let i = 0; i < 300; i += 1) {
      act(() => { sock().fire('generation_resume', { request_id: `fin-${i}`, generations: [] }); });
    }
    // «first» вытеснен — по нему снова можно спросить снимок; свежий — нельзя
    const before = emitsOf('resume_generations').length;
    server('chat_chunk', { request_id: 'fin-299', accumulated: 'x' });
    expect(emitsOf('resume_generations').length).toBe(before);
    server('chat_chunk', { request_id: 'first', accumulated: 'x' });
    expect(emitsOf('resume_generations').slice(before)).toEqual([{ request_id: 'first' }]);
    expect(hook.result.current.app.state.chats).toHaveLength(1);
  });
});

describe('отложенные подхваченные сессии: прочие события', () => {
  it('события multi-LLM для отложенной сессии игнорируются', async () => {
    const hook = await boot();
    open();
    let cid = '';
    act(() => {
      cid = hook.result.current.actions.createChat('Пустой');
    });
    server('generation_resume', { generations: [{ request_id: 'r1', conversation_id: cid, accumulated: 'a', stream_seq: 1 }] });
    server('multi_llm_start', { request_id: 'r1', total_models: 2 });
    expect(assistant(hook, cid)).toHaveLength(0);
  });
});

describe('перегенерация: сервер остановил генерацию', () => {
  it('complete с индексом за пределами добавляет вариант в конец', async () => {
    const { hook, cid } = await ready();
    let aid = '';
    act(() => {
      aid = hook.result.current.actions.addMessage(cid, { role: 'assistant', content: 'old', timestamp: 't' });
    });
    act(() => hook.result.current.s.regenerateResponse('q', aid, cid, ['old'], 1));
    const rid = lastEmit('chat_message').request_id;
    server('chat_complete', { request_id: rid, response: 'итог' });
    expect(chatOf(hook, cid).messages[0].alternativeResponses).toEqual(['old', 'итог']);
  });

  it('generation_stopped сбрасывает режим перегенерации: индикатор снимается, новые сообщения отправляются', async () => {
    const { hook, cid } = await ready();
    let aid = '';
    act(() => {
      aid = hook.result.current.actions.addMessage(cid, { role: 'assistant', content: 'old', timestamp: 't' });
    });
    act(() => hook.result.current.s.regenerateResponse('q', aid, cid, ['old'], 1));
    const rid = lastEmit('chat_message').request_id;
    server('generation_stopped', { request_id: rid });
    expect(hook.result.current.app.state.loadingChatIds).toEqual([]);
    expect(hook.result.current.s.regeneratingAssistantId).toBeNull();
    const before = emitsOf('chat_message').length;
    send(hook, 'новое сообщение', cid);
    expect(emitsOf('chat_message').length).toBe(before + 1);
  });

  it('отложенная отправка отбрасывается, если к моменту подключения подхвачена перегенерация', async () => {
    const hook = await boot(); // сокет создан, соединения ещё нет
    let cid = '';
    let aid = '';
    act(() => {
      cid = hook.result.current.actions.createChat('Чат');
    });
    act(() => {
      hook.result.current.actions.addMessage(cid, { role: 'user', content: 'вопрос', timestamp: 't' });
      aid = hook.result.current.actions.addMessage(cid, { role: 'assistant', content: 'old', timestamp: 't' });
    });
    send(hook, 'отложенное', cid);
    expect(notes(hook)).toContain('info:Подключаемся к серверу, сообщение будет отправлено автоматически');
    act(() => {
      sock().fire('connect');
      sock().fire('generation_resume', {
        generations: [{ request_id: 'rr', conversation_id: cid, regenerate: true, assistant_message_id: aid, accumulated: 'нов', stream_seq: 1 }],
      });
      sock().fire('chat_chunk', { request_id: 'rr', accumulated: 'новее', stream_seq: 2 });
    });
    expect(emitsOf('chat_message')).toHaveLength(0);
    expect(hook.result.current.s.regeneratingAssistantId).toBe(aid);
  });
});

describe('soft-unlock использует снимок чатов первого рендера', () => {
  it('сообщение с идущим слотом multi-LLM в сохранённом чате учитывается при проверке зависания', async () => {
    localStorage.setItem(
      'memo-chats',
      JSON.stringify({
        chats: [{
          id: 'c-boot', title: 'Старый', createdAt: 'c', updatedAt: '2024-01-01T00:00:00.000Z',
          messages: [
            { id: 'm0', role: 'user', content: 'q', timestamp: 't' },
            { id: 'm1', role: 'assistant', content: '', timestamp: 't', isStreaming: false, multiLLMResponses: [{ model: 'a', content: '', isStreaming: true }] },
          ],
        }],
        currentChatId: 'c-boot',
      }),
    );
    const ReactActual = jest.requireActual('react');
    let mods: any;
    jest.isolateModules(() => {
      jest.doMock('react', () => ReactActual);
      mods = { app: require('@/contexts/AppContext'), socket: require('@/contexts/SocketContext') };
    });
    const FreshWrapper = ({ children }: { children: React.ReactNode }) => (
      <mods.app.AppProvider>
        <mods.socket.SocketProvider>{children}</mods.socket.SocketProvider>
      </mods.app.AppProvider>
    );
    const hook = renderHook(
      () => ({ s: mods.socket.useSocket(), app: mods.app.useAppContext(), actions: mods.app.useAppActions() }),
      { wrapper: FreshWrapper },
    );
    await flush();
    open();
    await flush();
    expect(hook.result.current.app.state.chats.map((c: any) => c.id)).toEqual(['c-boot']);
    act(() => {
      hook.result.current.s.sendMessage('новый вопрос', 'c-boot', true);
    });
    await advance(8 * 60 * 1000 + 30_000);
    const texts = hook.result.current.app.state.notifications.map((n: any) => n.message);
    expect(texts.some((t: string) => t.includes('Давно нет новых фрагментов ответа'))).toBe(true);
    jest.dontMock('react');
  });
});

describe('generation_stopped в режиме multi-LLM', () => {
  it('слоты перестают стримиться, поздние чанки не создают сообщений', async () => {
    const { hook, cid } = await ready();
    send(hook, 'q', cid, true, undefined, true);
    const rid = lastEmit('chat_message').request_id;
    server('multi_llm_chunk', { request_id: rid, model: 'm1', accumulated: 'a' });
    server('multi_llm_chunk', { request_id: rid, model: 'm2', accumulated: 'b' });
    server('generation_stopped', { request_id: rid });
    const msg = assistant(hook, cid)[0];
    expect(msg.isStreaming).toBe(false);
    expect(msg.multiLLMResponses!.map((r) => [r.model, r.isStreaming])).toEqual([['m1', false], ['m2', false]]);
    expect(hook.result.current.app.state.loadingChatIds).toEqual([]);
    server('multi_llm_chunk', { request_id: rid, model: 'm1', accumulated: 'поздно' });
    expect(assistant(hook, cid)).toHaveLength(1);
  });

  it('без накопленных слотов стриминг сообщения просто снимается', async () => {
    const { hook, cid } = await ready();
    send(hook, 'q', cid, true, undefined, true);
    const rid = lastEmit('chat_message').request_id;
    server('generation_stopped', { request_id: rid });
    expect(assistant(hook, cid)[0].isStreaming).toBe(false);
  });
});

describe('запрос с пустым идентификатором чата', () => {
  it('chat_complete закрывает сессию, не обращаясь к чату, и снимает отслеживание сообщения', async () => {
    const { hook } = await ready();
    send(hook, 'q', '');
    const rid = lastEmit('chat_message').request_id;
    expect(lastEmit('chat_message').conversation_id).toBe('');
    server('chat_complete', { request_id: rid, response: 'ответ' });
    // сессия закрыта: повторное событие трактуется как событие завершённого запроса
    const asked = emitsOf('resume_generations').length;
    server('chat_chunk', { request_id: rid, accumulated: 'x' });
    expect(emitsOf('resume_generations').length).toBe(asked);
    expect(hook.result.current.app.state.chats.every((c) => c.messages.length === 0)).toBe(true);
  });
});
