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

/** Чат с одним сообщением пользователя — как после загрузки истории при F5. */
async function readyWithHistory() {
  const hook = await boot();
  open();
  let cid = '';
  let uid = '';
  act(() => {
    cid = hook.result.current.actions.createChat('Чат');
  });
  act(() => {
    uid = hook.result.current.actions.addMessage(cid, { role: 'user', content: 'вопрос', timestamp: 't' });
  });
  return { hook, cid, uid };
}

const snapshot = (cid: string, uid: string, over: Record<string, unknown> = {}) => ({
  request_id: 'r1',
  conversation_id: cid,
  accumulated: 'часть ответа',
  thinking: 'мысль',
  stream_seq: 4,
  started_at: 1_700_000_000,
  user_message_id: uid,
  user_message: 'вопрос',
  ...over,
});

describe('generation_resume: подхват генерации после F5', () => {
  it('создаёт сообщение ассистента из снимка и включает индикаторы', async () => {
    const { hook, cid, uid } = await readyWithHistory();
    const activity = jest.fn();
    window.addEventListener('astrachatMcpToolActivity', activity);
    server('generation_resume', {
      generations: [
        snapshot(cid, uid, {
          mcp_events: [
            { type: 'mcp_tool_start', call_id: 'c1', server_id: 's', tool: 't', qualified_name: 's.t' },
            { type: 'mcp_tool_start', call_id: 'c2', server_id: 's', tool: 't2', qualified_name: 's.t2' },
            { type: 'mcp_tool_end', call_id: 'c2', server_id: 's', tool: 't2', qualified_name: 's.t2' },
          ],
          agent_update: { agent_name: 'Субагент', index: 1, total: 2 },
        }),
      ],
    });
    window.removeEventListener('astrachatMcpToolActivity', activity);
    const msgs = chatOf(hook, cid).messages;
    expect(msgs.map((m) => m.role)).toEqual(['user', 'assistant']); // вопрос не дублируется
    expect(msgs[1]).toMatchObject({
      content: '<think>мысль</think>\n\nчасть ответа',
      isStreaming: true,
      generationStartedAtMs: 1_700_000_000_000,
    });
    expect(msgs[1].mcpToolCalls).toHaveLength(3);
    expect(msgs[1].chainCurrentAgent).toMatchObject({ agentName: 'Субагент', index: 1, total: 2 });
    expect(hook.result.current.app.state.loadingChatIds).toEqual([cid]);
    // индикатор только у вызова c1 (c2 уже завершён)
    expect(activity).toHaveBeenCalledTimes(1);
    expect((activity.mock.calls[0][0] as CustomEvent).detail.record.call_id).toBe('c1');
    expect(console.info).toHaveBeenCalledWith('[resume] генерация подхвачена', expect.objectContaining({ requestId: 'r1', chatId: cid }));
  });

  it('дальнейшие события той же генерации обновляют подхваченное сообщение и завершают его', async () => {
    const { hook, cid, uid } = await readyWithHistory();
    server('generation_resume', { generations: [snapshot(cid, uid)] });
    server('chat_chunk', { request_id: 'r1', accumulated: 'часть ответа и продолжение', stream_seq: 5 });
    expect(assistant(hook, cid)[0].content).toBe('<think>мысль</think>\n\nчасть ответа и продолжение');
    server('chat_chunk', { request_id: 'r1', accumulated: 'старое', stream_seq: 3 });
    expect(assistant(hook, cid)[0].content).toContain('продолжение');
    server('chat_complete', { request_id: 'r1', response: 'Итог' });
    expect(assistant(hook, cid)[0]).toMatchObject({ content: '<think>мысль</think>\n\nИтог', isStreaming: false });
    expect(hook.result.current.app.state.loadingChatIds).toEqual([]);
  });

  it('без накопленного текста сообщение создаётся нестримящимся (виден «думает»)', async () => {
    const { hook, cid, uid } = await readyWithHistory();
    server('generation_resume', { generations: [snapshot(cid, uid, { accumulated: '', thinking: '' })] });
    expect(assistant(hook, cid)[0]).toMatchObject({ content: '', isStreaming: false });
  });

  it('вопрос из другой вкладки добавляется в чат перед ответом', async () => {
    const { hook, cid } = await readyWithHistory();
    server('generation_resume', {
      generations: [snapshot(cid, 'u-other', { user_message: 'вопрос из соседней вкладки' })],
    });
    const msgs = chatOf(hook, cid).messages;
    expect(msgs.map((m) => m.content)).toEqual(['вопрос', 'вопрос из соседней вкладки', '<think>мысль</think>\n\nчасть ответа']);
  });

  it('started_at отсутствует → время старта берётся текущее', async () => {
    const { hook, cid, uid } = await readyWithHistory();
    server('generation_resume', { generations: [snapshot(cid, uid, { started_at: undefined })] });
    expect(assistant(hook, cid)[0].generationStartedAtMs).toBe(Date.now());
  });

  it('перегенерация: новый вариант добавляется последним в alternativeResponses существующего сообщения', async () => {
    const { hook, cid, uid } = await readyWithHistory();
    let aid = '';
    act(() => {
      aid = hook.result.current.actions.addMessage(cid, { role: 'assistant', content: 'старый ответ', timestamp: 't' });
    });
    server('generation_resume', {
      generations: [snapshot(cid, uid, { regenerate: true, assistant_message_id: aid, accumulated: 'новый вариант', thinking: '' })],
    });
    const msg = chatOf(hook, cid).messages.find((m) => m.id === aid)!;
    expect(msg.alternativeResponses).toEqual(['старый ответ', 'новый вариант']);
    expect(msg.currentResponseIndex).toBe(1);
    expect(msg.content).toBe('новый вариант');
    expect(msg.isStreaming).toBe(true);
    expect(chatOf(hook, cid).messages).toHaveLength(2); // новых сообщений не добавлено

    server('chat_chunk', { request_id: 'r1', accumulated: 'новый вариант + ещё', stream_seq: 5 });
    expect(hook.result.current.s.regeneratingAssistantId).toBe(aid);
    expect(chatOf(hook, cid).messages.find((m) => m.id === aid)!.alternativeResponses).toEqual(['старый ответ', 'новый вариант + ещё']);
  });

  it('перегенерация сообщения, у которого уже есть варианты, дописывает вариант', async () => {
    const { hook, cid, uid } = await readyWithHistory();
    let aid = '';
    act(() => {
      aid = hook.result.current.actions.addMessage(cid, {
        role: 'assistant', content: 'b', timestamp: 't', alternativeResponses: ['a', 'b'], currentResponseIndex: 1,
      });
    });
    server('generation_resume', {
      generations: [snapshot(cid, uid, { regenerate: true, assistant_message_id: aid, accumulated: 'c', thinking: '' })],
    });
    expect(chatOf(hook, cid).messages.find((m) => m.id === aid)!.alternativeResponses).toEqual(['a', 'b', 'c']);
  });

  it('чат без загруженных сообщений: события копятся в сессии и применяются, когда чат загрузится', async () => {
    const hook = await boot();
    open();
    let cid = '';
    act(() => {
      cid = hook.result.current.actions.createChat('Пустой');
    });
    server('generation_resume', {
      generations: [snapshot(cid, 'u-x', { accumulated: 'a', thinking: '', stream_seq: 1, mcp_events: [] })],
    });
    expect(assistant(hook, cid)).toHaveLength(0);

    // события идут в отложенную сессию
    server('chat_chunk', { request_id: 'r1', accumulated: 'abc', stream_seq: 2 });
    server('chat_chunk', { request_id: 'r1', accumulated: 'old', stream_seq: 2 }); // дубль seq
    server('chat_chunk', { request_id: 'r1', chunk: 'd' });
    server('chat_thinking', { request_id: 'r1', accumulated: 'мысль' });
    server('chat_thinking', { request_id: 'r1', chunk: '!' });
    server('chat_mcp_event', { request_id: 'r1', type: 'mcp_tool_start', call_id: 'k1', tool: 't' });
    server('chat_mcp_event', { request_id: 'r1', type: 'mcp_tool_start', call_id: 'k1', tool: 't' });
    server('chat_mcp_event', { request_id: 'r1', type: 'mcp_tool_end', call_id: 'k1', tool: 't' });
    server('chat_agent_update', { request_id: 'r1', agent_name: 'Саб', index: 2, total: 3 });
    server('chat_info', {});
    expect(assistant(hook, cid)).toHaveLength(0);

    // чат загрузился (сообщения пришли с сервера) — генерация появляется в UI
    act(() => {
      hook.result.current.actions.addMessage(cid, { role: 'user', content: 'вопрос', timestamp: 't' });
    });
    const msgs = chatOf(hook, cid).messages;
    expect(msgs.map((m) => m.role)).toEqual(['user', 'user', 'assistant']);
    const a = msgs[2];
    expect(a.content).toBe('<think>мысль!</think>\n\nabcd');
    expect(a.mcpToolCalls!.map((c) => c.type)).toEqual(['mcp_tool_start', 'mcp_tool_end']);
    expect(a.chainCurrentAgent).toMatchObject({ agentName: 'Саб', index: 2, total: 3 });
  });

  it('завершение отложенной генерации закрывает сессию без создания сообщения', async () => {
    const hook = await boot();
    open();
    let cid = '';
    act(() => {
      cid = hook.result.current.actions.createChat('Пустой');
    });
    server('generation_resume', { generations: [snapshot(cid, 'u-x')] });
    server('chat_complete', { request_id: 'r1', response: 'итог' });
    act(() => {
      hook.result.current.actions.addMessage(cid, { role: 'user', content: 'вопрос', timestamp: 't' });
    });
    expect(assistant(hook, cid)).toHaveLength(0);
    // закрытые сессии больше не принимают события
    const asked = emitsOf('resume_generations').length;
    server('chat_chunk', { request_id: 'r1', accumulated: 'x' });
    expect(emitsOf('resume_generations').length).toBe(asked);
  });

  it.each(['error', 'stopped'])('завершение отложенной генерации через %s', async (kind) => {
    const hook = await boot();
    open();
    let cid = '';
    act(() => {
      cid = hook.result.current.actions.createChat('Пустой');
    });
    server('generation_resume', { generations: [snapshot(cid, 'u-x')] });
    server(kind === 'error' ? 'chat_error' : 'generation_stopped', { request_id: 'r1', error: 'x' });
    act(() => {
      hook.result.current.actions.addMessage(cid, { role: 'user', content: 'вопрос', timestamp: 't' });
    });
    expect(assistant(hook, cid)).toHaveLength(0);
  });

  it('чат ушёл в выгруженное состояние — сессия ждёт, пока сообщения вернутся', async () => {
    const hook = await boot();
    open();
    let cid = '';
    act(() => {
      cid = hook.result.current.actions.createChat('Чат');
    });
    act(() => {
      hook.result.current.app.dispatch({ type: 'RESTORE_CHATS', payload: { chats: [{ id: cid, title: 'Чат', messages: [], messagesUnloaded: true, unloadedMessageCount: 2, createdAt: 'c', updatedAt: 'u' }], currentChatId: null, folders: [], unloadInactive: false } } as any);
    });
    server('generation_resume', { generations: [snapshot(cid, 'u-x')] });
    expect(assistant(hook, cid)).toHaveLength(0);
  });

  it('если сообщения чата заменили серверными, ответ ставится заново', async () => {
    const { hook, cid, uid } = await readyWithHistory();
    server('generation_resume', { generations: [snapshot(cid, uid)] });
    const firstId = assistant(hook, cid)[0].id;
    // загрузка списка после F5 заменила сообщения: нашего ответа нет
    act(() => {
      hook.result.current.actions.updateChatMessages(cid, [chatOf(hook, cid).messages[0]]);
    });
    const after = assistant(hook, cid);
    expect(after).toHaveLength(1);
    expect(after[0].id).not.toBe(firstId);
    expect(after[0].content).toBe('<think>мысль</think>\n\nчасть ответа');
  });

  it('повторный снимок своей активной генерации дополняет текст, рассуждение и вызовы MCP', async () => {
    const { hook, cid } = await readyWithHistory();
    send(hook, 'q2', cid);
    const rid = lastEmit('chat_message').request_id;
    server('chat_chunk', { request_id: rid, accumulated: 'ab', stream_seq: 1 });
    server('generation_resume', {
      generations: [
        {
          request_id: rid,
          conversation_id: cid,
          accumulated: 'abcdef',
          thinking: 'рассуждение',
          stream_seq: 5,
          mcp_events: [{ type: 'mcp_tool_start', call_id: 'c1', tool: 't' }],
        },
      ],
    });
    const msg = assistant(hook, cid).slice(-1)[0];
    expect(msg.content).toBe('<think>рассуждение</think>\n\nabcdef');
    expect(msg.mcpToolCalls).toHaveLength(1);
    expect(msg.isStreaming).toBe(true);

    // тот же снимок ничего не меняет
    server('generation_resume', {
      generations: [{ request_id: rid, conversation_id: cid, accumulated: 'abcdef', thinking: '', stream_seq: 5, mcp_events: [{ type: 'mcp_tool_start', call_id: 'c1', tool: 't' }] }],
    });
    expect(assistant(hook, cid).slice(-1)[0].content).toBe('<think>рассуждение</think>\n\nabcdef');
    expect(assistant(hook, cid).slice(-1)[0].mcpToolCalls).toHaveLength(1);
  });

  it('снимок своей перегенерации не затирает content, но дополняет вызовы MCP', async () => {
    const { hook, cid } = await readyWithHistory();
    let aid = '';
    act(() => {
      aid = hook.result.current.actions.addMessage(cid, { role: 'assistant', content: 'old', timestamp: 't' });
    });
    act(() => hook.result.current.s.regenerateResponse('q', aid, cid, ['old'], 1));
    const rid = lastEmit('chat_message').request_id;
    server('generation_resume', {
      generations: [{ request_id: rid, conversation_id: cid, accumulated: 'нов', stream_seq: 2, mcp_events: [{ type: 'mcp_tool_end', call_id: 'z', tool: 't' }] }],
    });
    const msg = chatOf(hook, cid).messages.find((m) => m.id === aid)!;
    expect(msg.content).toBe('old');
    expect(msg.mcpToolCalls).toHaveLength(1);
  });

  it('ответ на конкретный request_id без такой генерации помечает её завершённой', async () => {
    const { hook, cid } = await readyWithHistory();
    server('chat_chunk', { request_id: 'ghost', accumulated: 'x' });
    expect(emitsOf('resume_generations')).toContainEqual({ request_id: 'ghost' });
    server('generation_resume', { request_id: 'ghost', generations: [] });
    const asked = emitsOf('resume_generations').length;
    server('chat_chunk', { request_id: 'ghost', accumulated: 'x' });
    expect(emitsOf('resume_generations').length).toBe(asked);
    expect(chatOf(hook, cid).messages).toHaveLength(1);
  });

  it('некорректные и уже завершённые записи пропускаются', async () => {
    const { hook, cid, uid } = await readyWithHistory();
    server('generation_resume', {});
    server('generation_resume', { generations: 'x' });
    server('generation_resume', { generations: [null, { request_id: 'r' }, { conversation_id: cid }, snapshot(cid, uid, { request_id: 'done' })] });
    // 'done' — валидная запись и должна быть подхвачена; завершим её и проверим, что повторно не подхватывается
    expect(assistant(hook, cid)).toHaveLength(1);
    server('chat_complete', { request_id: 'done', response: 'ок' });
    server('generation_resume', { generations: [snapshot(cid, uid, { request_id: 'done' })] });
    expect(assistant(hook, cid)).toHaveLength(1);
  });

  it('нечисловые поля снимка заменяются значениями по умолчанию', async () => {
    const { hook, cid, uid } = await readyWithHistory();
    server('generation_resume', {
      generations: [{ request_id: 'r9', conversation_id: cid, accumulated: 5, thinking: 6, stream_seq: 'x', mcp_events: 'no', agent_update: 'no', user_message_id: uid }],
    });
    expect(assistant(hook, cid)[0]).toMatchObject({ content: '', isStreaming: false });
  });
});

describe('остановка и удаление чата при подхваченных генерациях', () => {
  it('stopGeneration останавливает подхваченную генерацию открытого чата по её request_id', async () => {
    const { hook, cid, uid } = await readyWithHistory();
    server('generation_resume', { generations: [snapshot(cid, uid)] });
    act(() => hook.result.current.s.stopGeneration());
    const stops = emitsOf('stop_generation');
    expect(stops).toContainEqual(expect.objectContaining({ conversation_id: cid, request_id: 'r1' }));
    expect(assistant(hook, cid)[0].isStreaming).toBe(false);
    expect(hook.result.current.app.state.loadingChatIds).toEqual([]);
  });

  it('удаление чата с идущей генерацией останавливает её на сервере и снимает индикаторы', async () => {
    const { hook, cid } = await readyWithHistory();
    send(hook, 'q', cid);
    const rid = lastEmit('chat_message').request_id;
    const clear = jest.fn();
    window.addEventListener('astrachatMcpToolActivityClear', clear);
    act(() => hook.result.current.actions.deleteChat(cid));
    window.removeEventListener('astrachatMcpToolActivityClear', clear);
    expect(lastEmit('stop_generation')).toMatchObject({ conversation_id: cid, request_id: rid });
    expect((clear.mock.calls[0][0] as CustomEvent).detail).toEqual({ chatId: cid });
    expect(hook.result.current.app.state.loadingChatIds).toEqual([]);
    // запоздавшие события удалённого чата игнорируются
    const asked = emitsOf('resume_generations').length;
    server('chat_chunk', { request_id: rid, accumulated: 'зомби' });
    expect(emitsOf('resume_generations').length).toBe(asked);
  });

  it('удаление чата без генерации не шлёт stop', async () => {
    const { hook, cid } = await readyWithHistory();
    act(() => hook.result.current.actions.deleteChat(cid));
    expect(emitsOf('stop_generation')).toHaveLength(0);
  });

  it('удаление чужого чата не трогает генерацию текущего', async () => {
    const { hook, cid } = await readyWithHistory();
    send(hook, 'q', cid);
    act(() => hook.result.current.actions.deleteChat('another'));
    expect(emitsOf('stop_generation')).toHaveLength(0);
    expect(hook.result.current.app.state.loadingChatIds).toEqual([cid]);
  });

  it('удаление всех чатов останавливает всё без conversation_id', async () => {
    const { hook, cid } = await readyWithHistory();
    send(hook, 'q', cid);
    act(() => hook.result.current.actions.deleteAllChats());
    const stop = lastEmit('stop_generation');
    expect('conversation_id' in stop).toBe(false);
    expect(stop.request_id).toMatch(/^req_/);
  });

  it('удаление подхваченной генерации чата: stop по её request_id', async () => {
    const { hook, cid, uid } = await readyWithHistory();
    server('generation_resume', { generations: [snapshot(cid, uid)] });
    act(() => hook.result.current.actions.deleteChat(cid));
    expect(lastEmit('stop_generation')).toMatchObject({ conversation_id: cid, request_id: 'r1' });
  });

  it('без соединения stop при удалении не отправляется', async () => {
    const { hook, cid } = await readyWithHistory();
    send(hook, 'q', cid);
    act(() => { sock().fire('disconnect', 'io client disconnect'); });
    const before = emitsOf('stop_generation').length;
    act(() => hook.result.current.actions.deleteChat(cid));
    expect(emitsOf('stop_generation').length).toBe(before);
  });
});

describe('soft-unlock зависшего стрима', () => {
  const EIGHT_MIN = 8 * 60 * 1000;

  it('через 8 минут без чанков снимает блокировку кнопки и предупреждает один раз', async () => {
    const { hook, cid } = await readyWithHistory();
    send(hook, 'q', cid);
    const rid = lastEmit('chat_message').request_id;
    server('chat_chunk', { request_id: rid, accumulated: 'начало' });
    await advance(EIGHT_MIN - 30_000);
    expect(hook.result.current.app.state.loadingChatIds).toEqual([cid]);
    await advance(60_000);
    expect(hook.result.current.app.state.loadingChatIds).toEqual([]);
    expect(assistant(hook, cid)[0].isStreaming).toBe(false);
    const warns = notes(hook).filter((n) => n.includes('Давно нет новых фрагментов ответа'));
    expect(warns).toHaveLength(1);
    await advance(60_000);
    expect(notes(hook).filter((n) => n.includes('Давно нет новых фрагментов ответа'))).toHaveLength(1);

    // чанк возвращает стрим в рабочее состояние
    server('chat_chunk', { request_id: rid, accumulated: 'начало и продолжение' });
    expect(assistant(hook, cid)[0]).toMatchObject({ content: 'начало и продолжение', isStreaming: true });
  });

  it('недавняя активность не вызывает разблокировку', async () => {
    const { hook, cid } = await readyWithHistory();
    send(hook, 'q', cid);
    await advance(EIGHT_MIN - 60_000);
    expect(notes(hook).some((n) => n.includes('Давно нет'))).toBe(false);
  });

  it('при остановленной генерации и после завершения разблокировка не срабатывает', async () => {
    const { hook, cid } = await readyWithHistory();
    send(hook, 'q', cid);
    const rid = lastEmit('chat_message').request_id;
    server('chat_complete', { request_id: rid, response: 'ок' });
    await advance(EIGHT_MIN + 60_000);
    expect(notes(hook).some((n) => n.includes('Давно нет'))).toBe(false);

    send(hook, 'q2', cid);
    act(() => hook.result.current.s.stopGeneration());
    await advance(EIGHT_MIN + 60_000);
    expect(notes(hook).some((n) => n.includes('Давно нет'))).toBe(false);
  });

  it('без активного чата ничего не делает', async () => {
    const hook = await boot();
    open();
    await advance(EIGHT_MIN + 60_000);
    expect(notes(hook).some((n) => n.includes('Давно нет'))).toBe(false);
  });

  it('multi-LLM: слоты перестают стримиться при разблокировке', async () => {
    const { hook, cid } = await readyWithHistory();
    send(hook, 'q', cid, true, undefined, true);
    const rid = lastEmit('chat_message').request_id;
    server('multi_llm_chunk', { request_id: rid, model: 'm1', accumulated: 'a' });
    await advance(EIGHT_MIN + 30_000);
    const msg = assistant(hook, cid)[0];
    expect(msg.isStreaming).toBe(false);
    expect(msg.multiLLMResponses![0]).toMatchObject({ model: 'm1', isStreaming: false });
    expect(hook.result.current.app.state.loadingChatIds).toEqual([]);
  });

  it('multi-LLM без слотов: разблокировка снимает стриминг сообщения', async () => {
    const { hook, cid } = await readyWithHistory();
    send(hook, 'q', cid, true, undefined, true);
    await advance(EIGHT_MIN + 30_000);
    expect(assistant(hook, cid)[0].isStreaming).toBe(false);
  });
});
