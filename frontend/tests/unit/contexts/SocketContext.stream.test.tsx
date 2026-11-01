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

async function ready() {
  const hook = await boot();
  open();
  let cid = '';
  act(() => {
    cid = hook.result.current.actions.createChat('Чат');
  });
  return { hook, cid };
}
/** Отправляет сообщение и возвращает request_id запроса. */
async function startStream(streaming = true, expectMulti = false) {
  const { hook, cid } = await ready();
  send(hook, 'вопрос', cid, streaming, undefined, expectMulti);
  const rid = lastEmit('chat_message').request_id as string;
  return { hook, cid, rid };
}
const assistant = (hook: Hook, cid: string) => chatOf(hook, cid).messages.filter((m) => m.role === 'assistant');

describe('стрим: chat_chunk', () => {
  it('accumulated заменяет текст, chunk дописывает; isStreaming=true', async () => {
    const { hook, cid, rid } = await startStream();
    server('chat_chunk', { request_id: rid, accumulated: 'При' });
    expect(assistant(hook, cid)[0]).toMatchObject({ content: 'При', isStreaming: true });
    server('chat_chunk', { request_id: rid, accumulated: 'Привет' });
    expect(assistant(hook, cid)[0].content).toBe('Привет');
    server('chat_chunk', { request_id: rid, chunk: ', мир' });
    expect(assistant(hook, cid)[0].content).toBe('Привет, мир');
    expect(assistant(hook, cid)).toHaveLength(1);
  });

  it('stream_seq: устаревший или повторный пакет отбрасывается', async () => {
    const { hook, cid, rid } = await startStream();
    server('chat_chunk', { request_id: rid, accumulated: 'abcd', stream_seq: 2 });
    server('chat_chunk', { request_id: rid, accumulated: 'ab', stream_seq: 1 });
    server('chat_chunk', { request_id: rid, accumulated: 'zzzz', stream_seq: 2 });
    expect(assistant(hook, cid)[0].content).toBe('abcd');
    server('chat_chunk', { request_id: rid, accumulated: 'abcde', stream_seq: 3 });
    expect(assistant(hook, cid)[0].content).toBe('abcde');
  });

  it('отладочный флаг пишет предупреждение об отброшенном чанке', async () => {
    localStorage.setItem('astra:debug:presentation', '1');
    const { rid } = await startStream();
    server('chat_chunk', { request_id: rid, accumulated: 'abcd', stream_seq: 5 });
    server('chat_chunk', { request_id: rid, accumulated: 'ab', stream_seq: 4 });
    expect((console.warn as jest.Mock).mock.calls.some((c) => String(c[0]).includes('устаревший чанк по stream_seq'))).toBe(true);
    server('chat_chunk', { request_id: rid, accumulated: 'abcdefgh' }); // без seq — принимается
    server('chat_chunk', { request_id: rid, accumulated: 'abc' }); // короче — отбрасывается
    expect((console.warn as jest.Mock).mock.calls.some((c) => String(c[0]).includes('чанк короче накопленного'))).toBe(true);
  });

  it('без stream_seq более короткий накопленный текст отбрасывается, кроме случая вырезания reasoning', async () => {
    const { hook, cid, rid } = await startStream();
    server('chat_chunk', { request_id: rid, accumulated: '<think>aaaaaaaaaaaa</think>hello world' });
    server('chat_chunk', { request_id: rid, accumulated: 'hello' });
    expect(assistant(hook, cid)[0].content).toBe('<think>aaaaaaaaaaaa</think>hello world');
    server('chat_chunk', { request_id: rid, accumulated: '<think>a</think>hello world!' });
    expect(assistant(hook, cid)[0].content).toBe('<think>a</think>hello world!');
  });

  it('chunk без данных не ломает накопленный текст', async () => {
    const { hook, cid, rid } = await startStream();
    server('chat_chunk', { request_id: rid, accumulated: 'abc' });
    server('chat_chunk', { request_id: rid });
    expect(assistant(hook, cid)[0].content).toBe('abc');
  });

  it('если плейсхолдера нет (streaming=false), первый чанк создаёт сообщение', async () => {
    const { hook, cid, rid } = await startStream(false);
    expect(assistant(hook, cid)).toHaveLength(0);
    server('chat_chunk', { request_id: rid, accumulated: 'A' });
    expect(assistant(hook, cid)).toHaveLength(1);
    expect(assistant(hook, cid)[0]).toMatchObject({ content: 'A', isStreaming: true });
    server('chat_chunk', { request_id: rid, accumulated: 'AB' });
    expect(assistant(hook, cid)).toHaveLength(1);
    expect(assistant(hook, cid)[0].content).toBe('AB');
  });

  it('событие без request_id маршрутизируется по conversation_id', async () => {
    const { hook, cid } = await startStream();
    server('chat_chunk', { conversation_id: cid, accumulated: 'по чату' });
    expect(assistant(hook, cid)[0].content).toBe('по чату');
  });

  it('старый сервер без идентификаторов: событие привязывается к единственной сессии', async () => {
    const { hook, cid } = await startStream();
    server('chat_chunk', { accumulated: 'без id' });
    expect(assistant(hook, cid)[0].content).toBe('без id');
  });

  it('без идентификаторов и без сессий событие игнорируется', async () => {
    const { hook, cid } = await ready();
    server('chat_chunk', { accumulated: 'сирота' });
    expect(chatOf(hook, cid).messages).toHaveLength(0);
  });

  it('чужой request_id не пишется в чат; снимок запрашивается один раз', async () => {
    const { hook, cid } = await startStream();
    const before = emitsOf('resume_generations').length;
    server('chat_chunk', { request_id: 'req_foreign', accumulated: 'чужое' });
    server('chat_chunk', { request_id: 'req_foreign', accumulated: 'чужое2' });
    expect(assistant(hook, cid)[0].content).toBe('');
    const asked = emitsOf('resume_generations').slice(before);
    expect(asked).toEqual([{ request_id: 'req_foreign' }]);
  });

  it('событие чужого чата (conversation_id без сессии) отбрасывается', async () => {
    const { hook, cid } = await startStream();
    server('chat_chunk', { conversation_id: 'other-chat', accumulated: 'нет' });
    expect(assistant(hook, cid)[0].content).toBe('');
  });

  it('сессия остановленной генерации чата не принимает события по conversation_id', async () => {
    const { hook, cid, rid } = await startStream();
    act(() => hook.result.current.s.stopGeneration());
    server('chat_chunk', { conversation_id: cid, accumulated: 'после стопа' });
    expect(assistant(hook, cid)[0].content).toBe('');
    expect(rid).toBeTruthy();
  });

  it('в режиме multi-LLM обычные chat_chunk игнорируются', async () => {
    const { hook, cid, rid } = await startStream(true, true);
    server('chat_chunk', { request_id: rid, accumulated: 'дубль' });
    expect(assistant(hook, cid)).toHaveLength(1);
    expect(assistant(hook, cid)[0].content).toBe('');
  });
});

describe('стрим: chat_thinking', () => {
  it('рассуждение показывается блоком <think> в плейсхолдере, затем текст ответа добавляется после него', async () => {
    const { hook, cid, rid } = await startStream();
    server('chat_thinking', { request_id: rid, accumulated: 'думаю' });
    expect(assistant(hook, cid)[0]).toMatchObject({ content: '<think>думаю</think>', isStreaming: true });
    server('chat_thinking', { request_id: rid, chunk: ' ещё' });
    expect(assistant(hook, cid)[0].content).toBe('<think>думаю ещё</think>');
    server('chat_thinking', { request_id: rid, thinking: '!' });
    expect(assistant(hook, cid)[0].content).toBe('<think>думаю ещё!</think>');
    server('chat_chunk', { request_id: rid, accumulated: 'Ответ' });
    expect(assistant(hook, cid)[0].content).toBe('<think>думаю ещё!</think>\n\nОтвет');
  });

  it('без плейсхолдера thinking создаёт сообщение заранее', async () => {
    const { hook, cid, rid } = await startStream(false);
    server('chat_thinking', { request_id: rid, accumulated: 'раздумья' });
    expect(assistant(hook, cid)).toHaveLength(1);
    expect(assistant(hook, cid)[0]).toMatchObject({ content: '<think>раздумья</think>', isStreaming: true });
  });

  it('пустой thinking не меняет накопленное рассуждение', async () => {
    const { hook, cid, rid } = await startStream();
    server('chat_thinking', { request_id: rid, accumulated: 'a' });
    server('chat_thinking', { request_id: rid });
    expect(assistant(hook, cid)[0].content).toBe('<think>a</think>');
  });

  it('при остановке и в multi-LLM режиме рассуждение в сообщение не пишется', async () => {
    const { hook, cid, rid } = await startStream(true, true);
    server('chat_thinking', { request_id: rid, accumulated: 'x' });
    expect(assistant(hook, cid)[0].content).toBe('');
  });

  it('thinking при перегенерации обновляет выбранный вариант', async () => {
    const { hook, cid } = await ready();
    let aid = '';
    act(() => {
      aid = hook.result.current.actions.addMessage(cid, { role: 'assistant', content: 'old', timestamp: 't' });
    });
    act(() => hook.result.current.s.regenerateResponse('q', aid, cid, ['old'], 0));
    const rid = lastEmit('chat_message').request_id;
    server('chat_thinking', { request_id: rid, accumulated: 'идея' });
    const msg = chatOf(hook, cid).messages[0];
    expect(msg.content).toBe('<think>идея</think>');
    expect(msg.alternativeResponses).toEqual(['<think>идея</think>']);
    expect(msg.currentResponseIndex).toBe(0);
  });

  it('thinking при перегенерации нового варианта (индекс за пределами) добавляет вариант', async () => {
    const { hook, cid } = await ready();
    let aid = '';
    act(() => {
      aid = hook.result.current.actions.addMessage(cid, { role: 'assistant', content: 'old', timestamp: 't' });
    });
    act(() => hook.result.current.s.regenerateResponse('q', aid, cid, ['old'], 1));
    const rid = lastEmit('chat_message').request_id;
    server('chat_thinking', { request_id: rid, accumulated: 'идея' });
    expect(chatOf(hook, cid).messages[0].alternativeResponses).toEqual(['old', '<think>идея</think>']);
  });
});

describe('стрим: chat_complete', () => {
  it('завершает ответ: текст, isStreaming=false, индикатор загрузки снят, сессия закрыта', async () => {
    const { hook, cid, rid } = await startStream();
    server('chat_chunk', { request_id: rid, accumulated: 'час' });
    server('chat_complete', { request_id: rid, response: 'Готовый ответ' });
    const msg = assistant(hook, cid)[0];
    expect(msg).toMatchObject({ content: 'Готовый ответ', isStreaming: false });
    expect(hook.result.current.app.state.loadingChatIds).toEqual([]);
    // событие того же request_id после завершения не воскрешает генерацию
    const asked = emitsOf('resume_generations').length;
    server('chat_chunk', { request_id: rid, accumulated: 'хвост' });
    expect(assistant(hook, cid)[0].content).toBe('Готовый ответ');
    expect(emitsOf('resume_generations').length).toBe(asked);
  });

  it('рассуждение, накопленное по ходу стрима, добавляется перед ответом', async () => {
    const { hook, cid, rid } = await startStream();
    server('chat_thinking', { request_id: rid, accumulated: ' мысль ' });
    server('chat_complete', { request_id: rid, response: 'Ответ' });
    expect(assistant(hook, cid)[0].content).toBe('<think>мысль</think>\n\nОтвет');
  });

  it('если ответ уже содержит <think>, рассуждение не дублируется', async () => {
    const { hook, cid, rid } = await startStream();
    server('chat_thinking', { request_id: rid, accumulated: 'мысль' });
    server('chat_complete', { request_id: rid, response: '<think>своё</think>Ответ' });
    expect(assistant(hook, cid)[0].content).toBe('<think>своё</think>Ответ');
  });

  it('длительность с сервера и цепочка агентов записываются в сообщение', async () => {
    const { hook, cid, rid } = await startStream();
    server('chat_agent_update', { request_id: rid, agent_id: 3, agent_name: ' Аналитик ', index: 1, total: 2, hide_sequential: 1, is_last: 0 });
    expect(assistant(hook, cid)[0].chainCurrentAgent).toEqual({
      agentId: 3, agentName: 'Аналитик', index: 1, total: 2, hideSequential: true, isLast: false,
    });
    server('chat_complete', {
      request_id: rid,
      response: 'Итог',
      generation_duration_sec: 12.4,
      hide_sequential_outputs: true,
      chain_steps: [
        { agent_id: 3, agent_name: 'A', content: 'шаг', reasoning: ' рассуждение ', document_search: { query: 'q' } },
        { agentId: '4', agentName: 'B', content: 5, reasoning_content: '   ' },
        {},
      ],
    });
    const msg = assistant(hook, cid)[0];
    expect(msg.generationDurationSec).toBe(12);
    expect(msg.hideSequentialOutputs).toBe(true);
    expect(msg.chainCurrentAgent).toBeUndefined();
    expect(msg.chainSteps).toEqual([
      { agentId: 3, agentName: 'A', content: 'шаг', reasoning: 'рассуждение', documentSearch: { query: 'q' } },
      { agentId: 4, agentName: 'B', content: '5', reasoning: undefined, documentSearch: undefined },
      { agentId: 0, agentName: 'Агент', content: '', reasoning: undefined, documentSearch: undefined },
    ]);
  });

  it('поиск по документам нормализуется (поля, snake_case, sourceFiles по умолчанию)', async () => {
    const { hook, cid, rid } = await startStream();
    server('chat_complete', {
      request_id: rid,
      response: 'ок',
      document_search: {
        query: 'вопрос',
        hits: [
          { file: 'a.pdf', anchor: 'p1', relevance: '0.9', content: 'текст', chunk_index: '2', document_id: '7', store: 'kb' },
          { file: 'a.pdf' },
          { file: 'b.pdf', chunkIndex: 1, documentId: 2 },
        ],
      },
    });
    const ds = assistant(hook, cid)[0].documentSearch!;
    expect(ds.query).toBe('вопрос');
    expect(ds.sourceFiles).toEqual(['a.pdf', 'b.pdf']);
    expect(ds.hits[0]).toEqual({ file: 'a.pdf', anchor: 'p1', relevance: 0.9, content: 'текст', chunkIndex: 2, documentId: 7, store: 'kb' });
    expect(ds.hits[1]).toEqual({ file: 'a.pdf', anchor: '', relevance: 0, content: '', chunkIndex: 0, documentId: 0, store: '' });
  });

  it('document_search с явным sourceFiles и без hits', async () => {
    const { hook, cid, rid } = await startStream();
    server('chat_complete', { request_id: rid, response: 'ок', document_search: { sourceFiles: ['x', 5] } });
    expect(assistant(hook, cid)[0].documentSearch).toEqual({ query: '', sourceFiles: ['x', '5'], hits: [] });
  });

  it('вызовы MCP из события complete сохраняются, если по ходу стрима их не было', async () => {
    const { hook, cid, rid } = await startStream();
    server('chat_complete', {
      request_id: rid,
      response: 'ок',
      mcp_tool_calls: [{ type: 'mcp_tool_end', server_id: 's', tool: 't', qualified_name: 's.t', call_id: 'c1', success: true }],
    });
    expect(assistant(hook, cid)[0].mcpToolCalls).toHaveLength(1);
    expect(assistant(hook, cid)[0].mcpToolCalls![0]).toMatchObject({ call_id: 'c1' });
  });

  it('при streaming=false без плейсхолдера ответ добавляется новым сообщением (с серверной длительностью)', async () => {
    const { hook, cid, rid } = await startStream(false);
    server('chat_complete', { request_id: rid, response: 'Целиком', timestamp: '2024-05-05T00:00:00Z', generation_duration_sec: 7 });
    const msgs = assistant(hook, cid);
    expect(msgs).toHaveLength(1);
    expect(msgs[0]).toMatchObject({ content: 'Целиком', isStreaming: false, timestamp: '2024-05-05T00:00:00Z', generationDurationSec: 7 });
  });

  it('при streaming=false длительность по умолчанию 1 с; документы и MCP прикрепляются', async () => {
    const { hook, cid, rid } = await startStream(false);
    server('chat_complete', {
      request_id: rid,
      response: 'Целиком',
      document_search: { query: 'q', hits: [] },
      mcp_tool_calls: [{ type: 'mcp_tool_end', server_id: 's', tool: 't', qualified_name: 's.t', call_id: 'c' }],
    });
    const m = assistant(hook, cid)[0];
    expect(m.generationDurationSec).toBe(1);
    expect(m.documentSearch?.query).toBe('q');
    expect(m.mcpToolCalls).toHaveLength(1);
  });

  it('пустой response без плейсхолдера сообщение не создаёт', async () => {
    const { hook, cid, rid } = await startStream(false);
    server('chat_complete', { request_id: rid, response: '' });
    expect(assistant(hook, cid)).toHaveLength(0);
    expect(hook.result.current.app.state.loadingChatIds).toEqual([]);
  });

  it('complete без чата и сессий игнорируется', async () => {
    const { hook } = await ready();
    server('chat_complete', { response: 'ничей' });
    expect(hook.result.current.app.state.loadingChatIds).toEqual([]);
  });

  it('в режиме multi-LLM chat_complete игнорируется', async () => {
    const { hook, cid, rid } = await startStream(true, true);
    server('chat_complete', { request_id: rid, response: 'дубль' });
    expect(assistant(hook, cid)).toHaveLength(1);
    expect(assistant(hook, cid)[0].content).toBe('');
    expect(hook.result.current.app.state.loadingChatIds).toEqual([cid]);
  });

  it('перегенерация: complete записывает ответ в выбранный вариант и снимает индикатор', async () => {
    const { hook, cid } = await ready();
    let aid = '';
    act(() => {
      aid = hook.result.current.actions.addMessage(cid, { role: 'assistant', content: 'old', timestamp: 't' });
    });
    act(() => hook.result.current.s.regenerateResponse('q', aid, cid, ['old'], 1));
    const rid = lastEmit('chat_message').request_id;
    expect(hook.result.current.s.regeneratingAssistantId).toBe(aid);
    server('chat_chunk', { request_id: rid, accumulated: 'нов' });
    let msg = chatOf(hook, cid).messages[0];
    expect(msg.alternativeResponses).toEqual(['old', 'нов']);
    server('chat_chunk', { request_id: rid, accumulated: 'новый' });
    msg = chatOf(hook, cid).messages[0];
    expect(msg.alternativeResponses).toEqual(['old', 'новый']);
    server('chat_complete', { request_id: rid, response: 'новый итог' });
    msg = chatOf(hook, cid).messages[0];
    expect(msg).toMatchObject({ content: 'новый итог', isStreaming: false, currentResponseIndex: 1 });
    expect(msg.alternativeResponses).toEqual(['old', 'новый итог']);
    expect(hook.result.current.s.regeneratingAssistantId).toBeNull();
  });

  it('перегенерация существующего индекса заменяет вариант на месте', async () => {
    const { hook, cid } = await ready();
    let aid = '';
    act(() => {
      aid = hook.result.current.actions.addMessage(cid, { role: 'assistant', content: 'old', timestamp: 't' });
    });
    act(() => hook.result.current.s.regenerateResponse('q', aid, cid, ['old', 'old2'], 0));
    const rid = lastEmit('chat_message').request_id;
    server('chat_complete', { request_id: rid, response: 'итог' });
    expect(chatOf(hook, cid).messages[0].alternativeResponses).toEqual(['итог', 'old2']);
  });

  it('браузерное уведомление показывается, только если включено и поддерживается', async () => {
    mockBrowser.enabled = true;
    mockBrowser.supported = true;
    const { rid } = await startStream();
    server('chat_complete', { request_id: rid, response: 'ок' });
    expect(mockBrowser.show).toHaveBeenCalledWith('Сообщение готово', { body: 'Ассистент завершил генерацию ответа', icon: '/astra.ico' });
  });

  it('ошибка браузерного уведомления логируется и не мешает завершению', async () => {
    mockBrowser.enabled = true;
    mockBrowser.supported = true;
    mockBrowser.show.mockImplementation(() => {
      throw new Error('denied');
    });
    const { hook, cid, rid } = await startStream();
    server('chat_complete', { request_id: rid, response: 'ок' });
    expect(console.error).toHaveBeenCalledWith('Ошибка при показе уведомления:', expect.any(Error));
    expect(assistant(hook, cid)[0]).toMatchObject({ content: 'ок', isStreaming: false });
  });
});

describe('стрим: chat_error', () => {
  it('показывает ошибку, снимает загрузку и стриминг, закрывает сессию', async () => {
    const { hook, cid, rid } = await startStream();
    server('chat_chunk', { request_id: rid, accumulated: 'частично' });
    server('chat_error', { request_id: rid, error: 'модель недоступна' });
    expect(notes(hook)).toContain('error:Ошибка сервера: модель недоступна');
    expect(assistant(hook, cid)[0]).toMatchObject({ content: 'частично', isStreaming: false });
    expect(hook.result.current.app.state.loadingChatIds).toEqual([]);
    const asked = emitsOf('resume_generations').length;
    server('chat_chunk', { request_id: rid, accumulated: 'поздно' });
    expect(assistant(hook, cid)[0].content).toBe('частично');
    expect(emitsOf('resume_generations').length).toBe(asked);
  });

  it('ошибка без текста тоже обрабатывается', async () => {
    const { hook, rid } = await startStream();
    server('chat_error', { request_id: rid });
    expect(notes(hook)).toContain('error:Ошибка сервера: undefined');
  });

  it('ошибка во время перегенерации сбрасывает индикатор перегенерации', async () => {
    const { hook, cid } = await ready();
    let aid = '';
    act(() => {
      aid = hook.result.current.actions.addMessage(cid, { role: 'assistant', content: 'old', timestamp: 't' });
    });
    act(() => hook.result.current.s.regenerateResponse('q', aid, cid, ['old'], 0));
    const rid = lastEmit('chat_message').request_id;
    server('chat_error', { request_id: rid, error: 'x' });
    expect(hook.result.current.s.regeneratingAssistantId).toBeNull();
    // после ошибки можно снова отправлять сообщения
    send(hook, 'новое', cid);
    expect(lastEmit('chat_message').message).toBe('новое');
  });

  describe.each(['Сессия завершена другим входом', 'Вы НЕ АВТОРИЗОВАНЫ? не авторизован'])('ошибка сессии: %s', (text) => {
    const realLocation = window.location;
    beforeEach(() => {
      delete (window as any).location;
      (window as any).location = { href: '/chat' };
    });
    afterEach(() => {
      (window as any).location = realLocation;
    });

    it('очищает авторизацию в localStorage и перенаправляет на /login', async () => {
      localStorage.setItem('auth_token', 't');
      localStorage.setItem('auth_user', 'u');
      const { hook, cid, rid } = await startStream();
      server('chat_error', { request_id: rid, error: text });
      expect(localStorage.getItem('auth_token')).toBeNull();
      expect(localStorage.getItem('auth_user')).toBeNull();
      expect(window.location.href).toBe('/login');
      expect(notes(hook)).toContain('error:Сессия завершена. Выполнен вход в другом окне.');
      expect(hook.result.current.app.state.loadingChatIds).toEqual([]);
      expect(assistant(hook, cid)[0].isStreaming).toBe(false);
    });
  });

  it('при завершении сессии активный сокет отключается и сбрасывается в состоянии', async () => {
    const realLocation = window.location;
    delete (window as any).location;
    (window as any).location = { href: '/chat' };
    const { hook, rid } = await startStream();
    server('chat_error', { request_id: rid, error: 'Сессия завершена' });
    expect(sock().disconnect).toHaveBeenCalled();
    expect(hook.result.current.s.socket).toBeNull();
    (window as any).location = realLocation;
  });
});

describe('стрим: события MCP', () => {
  const start = { type: 'mcp_tool_start', call_id: 'c1', server_id: 'srv', tool: 'search', qualified_name: 'srv.search', arguments: { q: 1 } };

  it('start/end пишутся в сообщение и рассылаются как window-события; повтор игнорируется', async () => {
    const { hook, cid, rid } = await startStream();
    const activity = jest.fn();
    window.addEventListener('astrachatMcpToolActivity', activity);
    server('chat_mcp_event', { ...start, request_id: rid });
    server('chat_mcp_event', { ...start, request_id: rid }); // дубль по call_id+type
    server('chat_mcp_event', {
      type: 'mcp_tool_end', call_id: 'c1', server_id: 'srv', tool: 'search', qualified_name: 'srv.search',
      success: true, duration_ms: 12, result: 'ok', result_preview: 'ok', download_urls: [{ url: 'u' }], document_search: { query: 'q' }, request_id: rid,
    });
    window.removeEventListener('astrachatMcpToolActivity', activity);
    const calls = assistant(hook, cid)[0].mcpToolCalls!;
    expect(calls.map((c) => c.type)).toEqual(['mcp_tool_start', 'mcp_tool_end']);
    expect(calls[0].arguments).toEqual({ q: 1 });
    expect(calls[1]).toMatchObject({ success: true, duration_ms: 12, result: 'ok', download_urls: [{ url: 'u' }] });
    expect(activity).toHaveBeenCalledTimes(2);
    expect((activity.mock.calls[0][0] as CustomEvent).detail).toMatchObject({ phase: 'start', chatId: cid });
    expect((activity.mock.calls[1][0] as CustomEvent).detail).toMatchObject({ phase: 'end' });

    // итоговые вызовы попадают в завершённое сообщение
    server('chat_complete', { request_id: rid, response: 'итог' });
    expect(assistant(hook, cid)[0].mcpToolCalls).toHaveLength(2);
  });

  it('очистка индикаторов MCP рассылается по завершении', async () => {
    const { rid } = await startStream();
    const clear = jest.fn();
    window.addEventListener('astrachatMcpToolActivityClear', clear);
    server('chat_complete', { request_id: rid, response: 'ок' });
    window.removeEventListener('astrachatMcpToolActivityClear', clear);
    expect(clear).toHaveBeenCalled();
  });

  it('событие без call_id и пустые поля приводятся к строкам', async () => {
    const { hook, cid, rid } = await startStream();
    server('chat_mcp_event', { type: 'weird', request_id: rid });
    const rec = assistant(hook, cid)[0].mcpToolCalls![0];
    expect(rec).toMatchObject({ type: 'mcp_tool_start', server_id: '', tool: '', qualified_name: '' });
    expect(rec.call_id).toBeUndefined();
    expect(rec.arguments).toBeUndefined();
  });

  it('после остановки события MCP игнорируются', async () => {
    const { hook, cid, rid } = await startStream();
    act(() => hook.result.current.s.stopGeneration());
    server('chat_mcp_event', { ...start, request_id: rid });
    expect(assistant(hook, cid)[0].mcpToolCalls).toBeUndefined();
  });
});

describe('стрим: chat_agent_update', () => {
  it('без активного чата игнорируется', async () => {
    const hook = await boot();
    open();
    server('chat_agent_update', { agent_name: 'X' });
    expect(hook.result.current.app.state.chats).toEqual([]);
  });

  it('значения по умолчанию для пустых полей', async () => {
    const { hook, cid, rid } = await startStream();
    server('chat_agent_update', { request_id: rid, agent_id: 'abc', agent_name: '   ', index: 'x', total: 0 });
    expect(assistant(hook, cid)[0].chainCurrentAgent).toEqual({
      agentId: null, agentName: 'Агент', index: 0, total: 1, hideSequential: false, isLast: false,
    });
  });

  it('если сообщение ещё не отслеживается, берётся последнее сообщение ассистента чата', async () => {
    const { hook, cid, rid } = await startStream(false);
    // streaming=false: плейсхолдера нет. Создаём сообщение ассистента вручную — как после перезагрузки истории.
    act(() => {
      hook.result.current.actions.addMessage(cid, { role: 'assistant', content: 'ранее', timestamp: 't' });
    });
    server('chat_agent_update', { request_id: rid, agent_name: 'Субагент', index: 1, total: 3 });
    const last = assistant(hook, cid)[0];
    // обработчик сокета читает свежее состояние (appStateRef), поэтому сообщение, добавленное позже, находится
    expect(last.chainCurrentAgent).toMatchObject({ agentName: 'Субагент' });
  });
});

describe('multi-LLM', () => {
  it('полный цикл: старт, чанки по моделям, завершения, итоговое сообщение', async () => {
    const { hook, cid, rid } = await startStream(true, true);
    server('multi_llm_start', { request_id: rid, total_models: 2 });
    server('multi_llm_chunk', { request_id: rid, model: 'm1', accumulated: 'А' });
    server('multi_llm_chunk', { request_id: rid, model: 'm2', chunk: 'Б' });
    server('multi_llm_chunk', { request_id: rid, model: 'm1', accumulated: 'АА' });
    let slots = assistant(hook, cid)[0].multiLLMResponses!;
    expect(slots.map((s) => [s.model, s.content, s.isStreaming])).toEqual([['m1', 'АА', true], ['m2', 'Б', true]]);

    jest.advanceTimersByTime(3000);
    server('multi_llm_complete', { request_id: rid, model: 'm1', response: 'Готово 1' });
    expect(hook.result.current.app.state.loadingChatIds).toEqual([cid]); // ещё одна модель в работе
    server('multi_llm_complete', { request_id: rid, model: 'm2', response: 'Готово 2', error: true });
    slots = assistant(hook, cid)[0].multiLLMResponses!;
    expect(slots.map((s) => [s.model, s.content, s.isStreaming, Boolean(s.error)])).toEqual([
      ['m1', 'Готово 1', false, false],
      ['m2', 'Готово 2', false, true],
    ]);
    expect(slots[0].generationDurationSec).toBeGreaterThanOrEqual(1);
    expect(assistant(hook, cid)[0].isStreaming).toBe(false);
    expect(hook.result.current.app.state.loadingChatIds).toEqual([]);

    // сессия завершена: запоздалый чанк не создаёт новых сообщений
    server('multi_llm_chunk', { request_id: rid, model: 'm1', accumulated: 'поздно' });
    expect(assistant(hook, cid)).toHaveLength(1);
  });

  it('total из события учитывается как порог завершения', async () => {
    const { hook, cid, rid } = await startStream(true, true);
    server('multi_llm_complete', { request_id: rid, model: 'm1', response: 'a', total: 1 });
    expect(hook.result.current.app.state.loadingChatIds).toEqual([]);
    expect(assistant(hook, cid)[0].multiLLMResponses).toHaveLength(1);
  });

  it('без total и без multi_llm_start завершение не фиксируется (порог 0)', async () => {
    const { hook, cid, rid } = await startStream(true, true);
    server('multi_llm_complete', { request_id: rid, model: 'm1', response: 'a' });
    expect(hook.result.current.app.state.loadingChatIds).toEqual([cid]);
  });

  it('если плейсхолдера нет (generation_resume/другая вкладка), события создают сообщение', async () => {
    const { hook, cid, rid } = await startStream(false, true);
    server('multi_llm_start', { request_id: rid, total_models: 1 });
    expect(assistant(hook, cid)).toHaveLength(1);
    expect(assistant(hook, cid)[0]).toMatchObject({ isStreaming: true, multiLLMResponses: [] });
  });

  it('multi_llm_chunk без start создаёт сообщение; multi_llm_complete без чанков — тоже', async () => {
    const first = await startStream(false, true);
    server('multi_llm_chunk', { request_id: first.rid, model: 'm1', accumulated: 'x' });
    expect(assistant(first.hook, first.cid)).toHaveLength(1);
    first.hook.unmount();

    const second = await startStream(false, true);
    server('multi_llm_complete', { request_id: second.rid, model: 'm9', response: 'y', total: 5 });
    expect(assistant(second.hook, second.cid)).toHaveLength(1);
    expect(assistant(second.hook, second.cid)[0].multiLLMResponses![0]).toMatchObject({ model: 'm9', content: 'y' });
  });

  it('модель без имени получает "unknown"; пустые данные чанка не ломаются', async () => {
    const { hook, cid, rid } = await startStream(true, true);
    server('multi_llm_chunk', { request_id: rid, accumulated: 'z' });
    expect(assistant(hook, cid)[0].multiLLMResponses![0].model).toBe('unknown');
  });

  it('события multi-LLM без активного чата и после остановки игнорируются', async () => {
    const hook = await boot();
    open();
    server('multi_llm_start', { total_models: 2 });
    server('multi_llm_chunk', { model: 'm', accumulated: 'x' });
    server('multi_llm_complete', { model: 'm', response: 'x' });
    expect(hook.result.current.app.state.chats).toEqual([]);

    const { hook: h2, cid, rid } = await startStream(true, true);
    act(() => h2.result.current.s.stopGeneration());
    server('multi_llm_chunk', { request_id: rid, model: 'm', accumulated: 'x' });
    expect(assistant(h2, cid)[0].multiLLMResponses).toEqual([]);
  });

  it('повторный чанк обновляет существующий слот, слот с alternativeResponses сохраняет варианты', async () => {
    const { hook, cid } = await ready();
    let aid = '';
    act(() => {
      aid = hook.result.current.actions.addMessage(cid, {
        role: 'assistant', content: '', timestamp: 't', isStreaming: false,
        multiLLMResponses: [{ model: 'm1', content: 'first', alternativeResponses: ['first', 'second'], currentResponseIndex: 1 }],
      } as any);
    });
    act(() => hook.result.current.s.regenerateMultiLlmSlot('q', aid, cid, 'm1'));
    const rid = lastEmit('chat_message').request_id;
    server('multi_llm_chunk', { request_id: rid, model: 'm1', accumulated: 'новый текст' });
    const slot = chatOf(hook, cid).messages[0].multiLLMResponses![0];
    // обработчик сокета читает свежий чат, поэтому варианты слота из текущего чата попадают в merge
    expect(slot.content).toBe('новый текст');
    expect(slot.alternativeResponses).toEqual(['first', 'новый текст']);
    expect(slot.isStreaming).toBe(true);
  });
});
