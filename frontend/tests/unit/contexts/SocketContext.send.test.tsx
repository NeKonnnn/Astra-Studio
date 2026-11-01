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

import { setMcpToolIdsForChat } from '@/mcp/selectionStorage';
import { setChatArtifactsToolMode } from '@/utils/artifactsSelectionStorage';
import { setActiveSkillRefs } from '@/utils/skillSelectionStorage';
import { MODEL_THINKING_MODE_STORAGE_KEY, LAST_SELECTED_MODEL_PATH_STORAGE_KEY } from '@/utils/modelThinking';

type Hook = { result: { current: ReturnType<typeof useAll> } };

const emitsOf = (name: string) => sock().emit.mock.calls.filter((c: any[]) => c[0] === name).map((c: any[]) => c[1]);
const lastEmit = (name: string) => {
  const all = emitsOf(name);
  return all[all.length - 1];
};
const chatOf = (hook: Hook, id: string) => hook.result.current.app.state.chats.find((c) => c.id === id)!;

async function ready() {
  const hook = await boot();
  open();
  let cid = '';
  act(() => {
    cid = hook.result.current.actions.createChat('Чат');
  });
  return { hook, cid };
}
const send = (hook: Hook, ...args: Parameters<ReturnType<typeof useAll>['s']['sendMessage']>) =>
  act(() => {
    hook.result.current.s.sendMessage(...args);
  });

describe('sendMessage: отправка и состояние', () => {
  it('отправляет chat_message с полями по умолчанию и добавляет сообщения в чат', async () => {
    const { hook, cid } = await ready();
    const abort = jest.fn();
    window.addEventListener('astrachat-abort-follow-ups', abort);
    send(hook, 'привет', cid);
    window.removeEventListener('astrachat-abort-follow-ups', abort);

    const payload = lastEmit('chat_message');
    expect(payload).toMatchObject({
      message: 'привет',
      streaming: true,
      conversation_id: cid,
      use_kb_rag: false,
      use_memory_library_rag: false,
      rag_strategy: 'auto',
      agent_id: null,
      agent_name: null,
      project_id: null,
      project_name: null,
      project_memory: null,
      project_instructions: null,
      model_comparison_enabled: false,
      enable_thinking: false,
      artifacts_settings: { artifacts_enabled: false, shadcn_enabled: false, user_prompt_mode: false },
    });
    expect(payload.request_id).toMatch(/^req_\d+_[a-z0-9]+$/);
    expect(payload.skill_ids).toBeUndefined();
    expect(payload.tag_ids).toBeUndefined();
    expect(payload.tool_ids).toBeUndefined();
    expect(payload.inline_context).toBeUndefined();
    expect(abort).toHaveBeenCalledTimes(1);

    const msgs = chatOf(hook, cid).messages;
    expect(msgs.map((m) => m.role)).toEqual(['user', 'assistant']);
    expect(msgs[0].content).toBe('привет');
    expect(payload.message_id).toBe(msgs[0].id);
    expect(msgs[1]).toMatchObject({ content: '', isStreaming: false });
    expect(msgs[1].generationStartedAtMs).toBeDefined();
    expect(hook.result.current.app.state.loadingChatIds).toEqual([cid]);
  });

  it('streaming=false: плейсхолдер ответа не создаётся', async () => {
    const { hook, cid } = await ready();
    send(hook, 'q', cid, false);
    expect(lastEmit('chat_message').streaming).toBe(false);
    expect(chatOf(hook, cid).messages.map((m) => m.role)).toEqual(['user']);
  });

  it('настройка модели streaming=false применяется, если аргумент не передан; явный аргумент приоритетнее', async () => {
    const { hook, cid } = await ready();
    act(() => hook.result.current.actions.setModelSettings({ ...hook.result.current.app.state.modelSettings, streaming: false }));
    send(hook, 'q1', cid);
    expect(lastEmit('chat_message').streaming).toBe(false);
    send(hook, 'q2', cid, true);
    expect(lastEmit('chat_message').streaming).toBe(true);
  });

  it('значения из localStorage: RAG, агент, стратегия, режим мышления', async () => {
    localStorage.setItem('use_kb_rag', 'true');
    localStorage.setItem('use_memory_library_rag', 'true');
    localStorage.setItem('active_agent_id', '7');
    localStorage.setItem('active_agent_name', '  Юрист  ');
    localStorage.setItem('rag_strategy', 'reranking');
    localStorage.setItem(MODEL_THINKING_MODE_STORAGE_KEY, 'thinking');
    const { hook, cid } = await ready();
    send(hook, 'q', cid);
    expect(lastEmit('chat_message')).toMatchObject({
      use_kb_rag: true,
      use_memory_library_rag: true,
      agent_id: 7,
      agent_name: 'Юрист',
      rag_strategy: 'hybrid',
      enable_thinking: true,
    });
  });

  it.each([
    ['standard', 'vector'],
    ['graph', 'graph'],
    ['lexical', 'lexical'],
    ['  HYBRID ', 'hybrid'],
    ['совсем-не-стратегия', 'auto'],
  ])('rag_strategy %s → %s', async (raw, expected) => {
    localStorage.setItem('rag_strategy', raw);
    const { hook, cid } = await ready();
    send(hook, 'q', cid);
    expect(lastEmit('chat_message').rag_strategy).toBe(expected);
  });

  it('персональная стратегия пользователя (rag_strategy:<логин>) приоритетнее общей', async () => {
    localStorage.setItem('rag_strategy', 'graph');
    localStorage.setItem('rag_strategy:alice', 'lexical');
    const { hook, cid } = await ready();
    send(hook, 'q', cid);
    expect(lastEmit('chat_message').rag_strategy).toBe('lexical');
  });

  it('ключ пользователя строится из user_id в нижнем регистре', async () => {
    mockAuth = { token: 'tok', user: { user_id: 'UID-9', username: 'alice' } };
    localStorage.setItem('rag_strategy:uid-9', 'vector');
    const { hook, cid } = await ready();
    send(hook, 'q', cid);
    expect(lastEmit('chat_message').rag_strategy).toBe('vector');
  });

  it('без пользователя используется только общая стратегия', async () => {
    mockAuth = { token: 'tok', user: null };
    localStorage.setItem('rag_strategy', 'graph');
    const { hook, cid } = await ready();
    send(hook, 'q', cid);
    expect(lastEmit('chat_message').rag_strategy).toBe('graph');
  });

  it('нечисловой active_agent_id и пустое имя агента → null', async () => {
    localStorage.setItem('active_agent_id', 'abc');
    localStorage.setItem('active_agent_name', '   ');
    const { hook, cid } = await ready();
    send(hook, 'q', cid);
    expect(lastEmit('chat_message')).toMatchObject({ agent_id: null, agent_name: null });
  });

  it('режим мышления: неизвестное значение → fast; auto зависит от модели', async () => {
    localStorage.setItem(MODEL_THINKING_MODE_STORAGE_KEY, 'bogus');
    const { hook, cid } = await ready();
    send(hook, 'q', cid);
    expect(lastEmit('chat_message').enable_thinking).toBe(false);
    localStorage.setItem(MODEL_THINKING_MODE_STORAGE_KEY, 'auto');
    localStorage.setItem(LAST_SELECTED_MODEL_PATH_STORAGE_KEY, 'llm-svc://qwen3-32b');
    send(hook, 'q', cid);
    expect(lastEmit('chat_message').enable_thinking).toBe(true);
    localStorage.setItem(LAST_SELECTED_MODEL_PATH_STORAGE_KEY, 'llm-svc://llama');
    send(hook, 'q', cid);
    expect(lastEmit('chat_message').enable_thinking).toBe(false);
  });

  it('данные проекта чата попадают в запрос; overrideProjectId приоритетнее (в т.ч. null)', async () => {
    const { hook, cid } = await ready();
    let pid = '';
    act(() => {
      pid = hook.result.current.actions.createProject({ name: 'Альфа', memory: 'project-only', instructions: 'Отвечай кратко' });
    });
    act(() => hook.result.current.actions.moveChatToProject(cid, pid));
    send(hook, 'q', cid);
    expect(lastEmit('chat_message')).toMatchObject({
      project_id: pid,
      project_name: 'Альфа',
      project_memory: 'project-only',
      project_instructions: 'Отвечай кратко',
    });
    send(hook, 'q', cid, true, null);
    expect(lastEmit('chat_message')).toMatchObject({ project_id: null, project_name: null });
    send(hook, 'q', cid, true, 'ghost');
    expect(lastEmit('chat_message')).toMatchObject({ project_id: 'ghost', project_name: null, project_memory: null });
  });

  it('MCP-инструменты, skills (меню + упоминания), теги и артефакты чата', async () => {
    const { hook, cid } = await ready();
    setMcpToolIdsForChat(cid, ['mcp:srv1']);
    setActiveSkillRefs(cid, [{ slug: 'analyst' }, { slug: 'mermaid' }]);
    setChatArtifactsToolMode(cid, 'shadcn_enabled', true);
    send(hook, 'сделай <$mermaid|Mermaid> и <$extra|E> <#5|Тег> <#5|Тег> <#0|bad>', cid);
    const p = lastEmit('chat_message');
    expect(p.tool_ids).toEqual(['mcp:srv1']);
    expect(p.skill_ids).toEqual(['analyst', 'mermaid', 'extra']);
    expect(p.tag_ids).toEqual([5]);
    expect(p.artifacts_settings).toEqual({ artifacts_enabled: true, shadcn_enabled: true, user_prompt_mode: false });
  });

  it('при активном агенте без presentation-skill skills презентации из меню не отправляются, упоминания — отправляются', async () => {
    localStorage.setItem('active_agent_id', '3');
    const { hook, cid } = await ready();
    setActiveSkillRefs(cid, [{ slug: 'pptx-maker' }, { slug: 'analyst' }]);
    send(hook, 'q <$pptx-maker|P>', cid);
    expect(lastEmit('chat_message').skill_ids).toEqual(['analyst', 'pptx-maker']);
    send(hook, 'q', cid);
    expect(lastEmit('chat_message').skill_ids).toEqual(['analyst']);
    localStorage.setItem('active_agent_presentation_skills_v2', '1');
    send(hook, 'q', cid);
    expect(lastEmit('chat_message').skill_ids).toEqual(['pptx-maker', 'analyst']);
  });

  it('inline-вложения: превью в пузыре, мета без превью — в запросе', async () => {
    const { hook, cid } = await ready();
    send(hook, 'смотри', cid, true, undefined, false, {
      inline_context: 'текст файла',
      inline_images: ['data:image/png;base64,AAA'],
      attachments_meta: [
        { name: 'a.png', contentType: 'image', preview: 'data:image/png;base64,AAA', minio_object: 'o', minio_bucket: 'b', size: 10, tokenEstimate: 5 },
        { name: 'b.txt', contentType: 'text', size: 0, tokenEstimate: 0 },
      ],
    });
    const p = lastEmit('chat_message');
    expect(p.inline_context).toBe('текст файла');
    expect(p.inline_images).toEqual(['data:image/png;base64,AAA']);
    expect(p.inline_attachments).toEqual([
      { name: 'a.png', contentType: 'image', minio_object: 'o', minio_bucket: 'b', size: 10, tokenEstimate: 5 },
      { name: 'b.txt', contentType: 'text' },
    ]);
    const user = chatOf(hook, cid).messages[0];
    expect(user.inlineAttachments).toHaveLength(2);
    expect(user.inlineAttachments![0].preview).toBe('data:image/png;base64,AAA');
  });

  it('пустые inline-поля не попадают в запрос', async () => {
    const { hook, cid } = await ready();
    send(hook, 'q', cid, true, undefined, false, { inline_context: '', inline_images: [], attachments_meta: [] });
    const p = lastEmit('chat_message');
    expect(p.inline_context).toBeUndefined();
    expect(p.inline_images).toBeUndefined();
    expect(p.inline_attachments).toBeUndefined();
    expect(chatOf(hook, cid).messages[0].inlineAttachments).toBeUndefined();
  });

  it('multi-LLM: плейсхолдер со слотами и model_comparison_enabled', async () => {
    const { hook, cid } = await ready();
    send(hook, 'q', cid, true, undefined, true);
    expect(lastEmit('chat_message').model_comparison_enabled).toBe(true);
    const assistant = chatOf(hook, cid).messages[1];
    expect(assistant).toMatchObject({ isStreaming: true, multiLLMResponses: [] });
  });

  it('блокировка при перечанковке RAG: предупреждение и никакой отправки', async () => {
    const { hook, cid } = await ready();
    mockRagStatus.block = true;
    hook.rerender();
    send(hook, 'q', cid);
    expect(emitsOf('chat_message')).toHaveLength(0);
    expect(chatOf(hook, cid).messages).toHaveLength(0);
    expect(notes(hook)).toContain('warning:RAG пересобирается');
  });
});

describe('sendMessage: нет соединения', () => {
  it('сообщение откладывается и уходит после подключения (без дубля)', async () => {
    const hook = await boot(); // сокет создан, но connect ещё не было
    let cid = '';
    act(() => {
      cid = hook.result.current.actions.createChat('Чат');
    });
    send(hook, 'отложенное', cid);
    expect(emitsOf('chat_message')).toHaveLength(0);
    expect(notes(hook)).toContain('info:Подключаемся к серверу, сообщение будет отправлено автоматически');
    expect(sockets).toHaveLength(1); // идёт подключение — reconnect не нужен
    expect(chatOf(hook, cid).messages).toHaveLength(0);

    open();
    expect(emitsOf('chat_message')).toHaveLength(1);
    expect(lastEmit('chat_message').message).toBe('отложенное');
    expect(chatOf(hook, cid).messages.filter((m) => m.role === 'user')).toHaveLength(1);
  });

  it('если подключение не идёт, отправка запускает reconnect и затем уходит на новый сокет', async () => {
    const { hook, cid } = await ready();
    act(() => { sock().fire('disconnect', 'io client disconnect'); });
    // connectingRef сброшен подключением; watchdog ещё не сработал
    send(hook, 'после обрыва', cid);
    await flush();
    expect(sockets).toHaveLength(2);
    expect(emitsOf('chat_message')).toHaveLength(0);
    open();
    expect(lastEmit('chat_message').message).toBe('после обрыва');
  });

  it('следующая отправка заменяет отложенную (хранится одно сообщение)', async () => {
    const hook = await boot();
    let cid = '';
    act(() => {
      cid = hook.result.current.actions.createChat('Чат');
    });
    send(hook, 'первое', cid);
    send(hook, 'второе', cid);
    open();
    expect(emitsOf('chat_message').map((p: any) => p.message)).toEqual(['второе']);
  });

  it('отправка во время перегенерации игнорируется', async () => {
    const { hook, cid } = await ready();
    let aid = '';
    act(() => {
      aid = hook.result.current.actions.addMessage(cid, { role: 'assistant', content: 'old', timestamp: 't' });
    });
    act(() => hook.result.current.s.regenerateResponse('q', aid, cid, ['old'], 0));
    send(hook, 'новое', cid);
    expect(emitsOf('chat_message').filter((p: any) => p.message === 'новое')).toHaveLength(0);
  });
});

describe('regenerateResponse', () => {
  it('без соединения — ошибка; при блокировке RAG — отказ', async () => {
    const hook = await boot();
    let cid = '';
    act(() => {
      cid = hook.result.current.actions.createChat('Чат');
    });
    act(() => hook.result.current.s.regenerateResponse('q', 'a1', cid, [], 0));
    expect(notes(hook)).toContain('error:Нет соединения с сервером');
    open();
    mockRagStatus.block = true;
    hook.rerender();
    act(() => hook.result.current.s.regenerateResponse('q', 'a1', cid, [], 0));
    expect(emitsOf('chat_message')).toHaveLength(0);
    expect(notes(hook)).toContain('warning:RAG пересобирается');
  });

  it('отправляет regenerate-запрос и включает индикатор перегенерации', async () => {
    localStorage.setItem('active_agent_id', '4');
    localStorage.setItem('active_agent_name', 'Бот');
    const { hook, cid } = await ready();
    let pid = '';
    let aid = '';
    act(() => {
      pid = hook.result.current.actions.createProject({ name: 'П', memory: 'default', instructions: '' });
      aid = hook.result.current.actions.addMessage(cid, { role: 'assistant', content: 'old', timestamp: 't' });
    });
    act(() => hook.result.current.actions.moveChatToProject(cid, pid));
    const abort = jest.fn();
    window.addEventListener('astrachat-abort-follow-ups', abort);
    act(() => hook.result.current.s.regenerateResponse('вопрос <#7|т>', aid, cid, ['old'], 0, undefined, 'u-1'));
    window.removeEventListener('astrachat-abort-follow-ups', abort);

    expect(abort).toHaveBeenCalledTimes(1);
    expect(hook.result.current.s.regeneratingAssistantId).toBe(aid);
    const p = lastEmit('chat_message');
    expect(p).toMatchObject({
      message: 'вопрос <#7|т>',
      regenerate: true,
      assistant_message_id: aid,
      alternative_responses: ['old'],
      current_index: 0,
      conversation_id: cid,
      message_id: 'u-1',
      agent_id: 4,
      agent_name: 'Бот',
      project_id: pid,
      project_name: 'П',
      streaming: true,
      tag_ids: [7],
    });
    expect(p.request_id).toMatch(/^req_/);
    expect(hook.result.current.app.state.loadingChatIds).toContain(cid);
    expect(chatOf(hook, cid).messages[0].generationStartedAtMs).toBeDefined();
  });

  it('без userMessageId поле message_id не передаётся; без проекта project_id=null', async () => {
    const { hook, cid } = await ready();
    let aid = '';
    act(() => {
      aid = hook.result.current.actions.addMessage(cid, { role: 'assistant', content: 'old', timestamp: 't' });
    });
    act(() => hook.result.current.s.regenerateResponse('q', aid, cid, [], 0, false));
    const p = lastEmit('chat_message');
    expect('message_id' in p).toBe(false);
    expect(p.project_id).toBeNull();
    expect(p.streaming).toBe(false);
  });
});

describe('regenerateMultiLlmSlot', () => {
  it('без соединения — ошибка; при блокировке RAG — отказ', async () => {
    const hook = await boot();
    let cid = '';
    act(() => {
      cid = hook.result.current.actions.createChat('Чат');
    });
    act(() => hook.result.current.s.regenerateMultiLlmSlot('q', 'a1', cid, 'm1'));
    expect(notes(hook)).toContain('error:Нет соединения с сервером');
    open();
    mockRagStatus.block = true;
    hook.rerender();
    act(() => hook.result.current.s.regenerateMultiLlmSlot('q', 'a1', cid, 'm1'));
    expect(emitsOf('chat_message')).toHaveLength(0);
  });

  it('перегенерирует один слот: остальные сохраняют контент, выбранный начинает стримиться', async () => {
    const { hook, cid } = await ready();
    let aid = '';
    act(() => {
      aid = hook.result.current.actions.addMessage(cid, {
        role: 'assistant',
        content: '',
        timestamp: 't',
        multiLLMResponses: [
          { model: 'm1', content: 'one', generationStartedAtMs: 1, generationDurationSec: 3 },
          { model: 'm2', content: 'two', alternativeResponses: ['two', 'two-b'], currentResponseIndex: 1, generationDurationSec: 4 },
        ],
      } as any);
    });
    act(() => hook.result.current.s.regenerateMultiLlmSlot('вопрос', aid, cid, 'm2', undefined, null));
    const p = lastEmit('chat_message');
    expect(p).toMatchObject({
      regenerate: true,
      assistant_message_id: aid,
      multi_llm_slot_regenerate: 'm2',
      model_comparison_enabled: true,
      project_id: null,
      conversation_id: cid,
    });
    const slots = chatOf(hook, cid).messages[0].multiLLMResponses!;
    expect(slots.find((r) => r.model === 'm2')).toMatchObject({ isStreaming: true });
    expect(slots.find((r) => r.model === 'm2')!.generationDurationSec).toBeUndefined();
    expect(slots.find((r) => r.model === 'm1')).toMatchObject({ content: 'one' });
    expect(hook.result.current.app.state.loadingChatIds).toContain(cid);
  });

  it('если у сообщения нет слотов, всё равно отправляет запрос; проект берётся из чата', async () => {
    const { hook, cid } = await ready();
    let pid = '';
    let aid = '';
    act(() => {
      pid = hook.result.current.actions.createProject({ name: 'П', memory: 'default', instructions: 'и' });
      aid = hook.result.current.actions.addMessage(cid, { role: 'assistant', content: '', timestamp: 't' });
    });
    act(() => hook.result.current.actions.moveChatToProject(cid, pid));
    act(() => hook.result.current.s.regenerateMultiLlmSlot('q', aid, cid, 'm1'));
    expect(lastEmit('chat_message')).toMatchObject({ project_id: pid, project_instructions: 'и' });
  });
});

describe('stopGeneration', () => {
  it('без соединения — ошибка', async () => {
    const hook = await boot();
    act(() => hook.result.current.s.stopGeneration());
    expect(notes(hook)).toContain('error:Нет соединения с сервером');
  });

  it('останавливает текущую генерацию: stop_generation, снятие индикаторов, уведомление', async () => {
    const { hook, cid } = await ready();
    send(hook, 'q', cid);
    const rid = lastEmit('chat_message').request_id;
    server('chat_chunk', { request_id: rid, chunk: 'часть' });
    expect(chatOf(hook, cid).messages[1]).toMatchObject({ content: 'часть', isStreaming: true });

    act(() => hook.result.current.s.stopGeneration());
    const stop = lastEmit('stop_generation');
    expect(stop).toMatchObject({ conversation_id: cid, request_id: rid });
    expect(typeof stop.timestamp).toBe('string');
    expect(chatOf(hook, cid).messages[1].isStreaming).toBe(false);
    expect(hook.result.current.app.state.loadingChatIds).toEqual([]);
    expect(notes(hook)).toContain('info:Генерация остановлена');

    // запоздавшие события остановленной генерации игнорируются и не вызывают resume
    emitsOf('resume_generations').length;
    const resumeCalls = emitsOf('resume_generations').length;
    server('chat_chunk', { request_id: rid, chunk: ' позже' });
    server('chat_complete', { request_id: rid, response: 'итог' });
    expect(chatOf(hook, cid).messages).toHaveLength(2);
    expect(chatOf(hook, cid).messages[1].content).toBe('часть');
    expect(emitsOf('resume_generations').length).toBe(resumeCalls);
  });

  it('останавливает multi-LLM: слоты перестают стримиться', async () => {
    const { hook, cid } = await ready();
    send(hook, 'q', cid, true, undefined, true);
    const rid = lastEmit('chat_message').request_id;
    server('multi_llm_start', { request_id: rid, total_models: 2 });
    server('multi_llm_chunk', { request_id: rid, model: 'm1', accumulated: 'a' });
    expect(chatOf(hook, cid).messages[1].multiLLMResponses![0].isStreaming).toBe(true);
    act(() => hook.result.current.s.stopGeneration());
    const msg = chatOf(hook, cid).messages[1];
    expect(msg.isStreaming).toBe(false);
    expect(msg.multiLLMResponses![0]).toMatchObject({ model: 'm1', isStreaming: false });
  });

  it('остановка multi-LLM без слотов тоже снимает стриминг', async () => {
    const { hook, cid } = await ready();
    send(hook, 'q', cid, true, undefined, true);
    act(() => hook.result.current.s.stopGeneration());
    expect(chatOf(hook, cid).messages[1].isStreaming).toBe(false);
  });

  it('остановка без активной генерации не падает и шлёт stop без request_id', async () => {
    const { hook } = await ready();
    act(() => hook.result.current.s.stopGeneration());
    const stop = lastEmit('stop_generation');
    expect('conversation_id' in stop).toBe(false);
    expect('request_id' in stop).toBe(false);
  });

  it('событие generation_stopped от сервера завершает генерацию', async () => {
    const { hook, cid } = await ready();
    send(hook, 'q', cid);
    const rid = lastEmit('chat_message').request_id;
    server('chat_chunk', { request_id: rid, chunk: 'x' });
    server('generation_stopped', { request_id: rid });
    expect(chatOf(hook, cid).messages[1].isStreaming).toBe(false);
    expect(hook.result.current.app.state.loadingChatIds).toEqual([]);
  });
});

function server(event: string, data?: any) {
  act(() => {
    sock().fire(event, data);
  });
}
