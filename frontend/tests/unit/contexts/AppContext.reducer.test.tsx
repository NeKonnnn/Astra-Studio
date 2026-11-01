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

import {
  AppProvider,
  CHAT_DELETED_EVENT,
  chatIsListedInAllChatsSection,
  useAppActions,
  useAppContext,
} from '@/contexts/AppContext';

const fetchMock = jest.fn();
const wrapper = ({ children }: { children: React.ReactNode }) => <AppProvider>{children}</AppProvider>;

const useAll = () => {
  const ctx = useAppContext();
  const actions = useAppActions();
  return { ...ctx, actions };
};

const msg = (id: string, role: 'user' | 'assistant', content: string, extra: any = {}) => ({
  id,
  role,
  content,
  timestamp: 't',
  ...extra,
});
const chat = (id: string, messages: any[] = [], extra: any = {}) => ({
  id,
  title: `T-${id}`,
  messages,
  createdAt: 'c',
  updatedAt: '2024-01-01T00:00:00.000Z',
  ...extra,
});

let seq = 0;
const uid = (p: string) => `${p}-${(seq += 1)}`;

beforeEach(() => {
  jest.clearAllMocks();
  localStorage.clear();
  mockUseAuth.mockReturnValue({ token: null });
  mockInitSettings.mockResolvedValue(undefined);
  fetchMock.mockReset();
  fetchMock.mockResolvedValue({ ok: false, json: () => Promise.resolve({}) });
  (global as any).fetch = fetchMock;
});

async function setup() {
  const hook = renderHook(() => useAll(), { wrapper });
  await waitFor(() => expect(hook.result.current.state.isInitialized).toBe(true));
  return hook;
}

describe('chatIsListedInAllChatsSection', () => {
  it('скрытая ветка не показывается', () => {
    expect(chatIsListedInAllChatsSection(chat('a', [msg('1', 'user', 'x')], { hiddenFromSidebarUntilUserMessage: true }) as any)).toBe(false);
  });
  it('выгруженный чат — по unloadedMessageCount', () => {
    expect(chatIsListedInAllChatsSection(chat('a', [], { messagesUnloaded: true, unloadedMessageCount: 3 }) as any)).toBe(true);
    expect(chatIsListedInAllChatsSection(chat('a', [], { messagesUnloaded: true }) as any)).toBe(false);
  });
  it('обычный чат — по наличию сообщений', () => {
    expect(chatIsListedInAllChatsSection(chat('a', [msg('1', 'user', 'x')]) as any)).toBe(true);
    expect(chatIsListedInAllChatsSection(chat('a') as any)).toBe(false);
  });
});

describe('useAppContext', () => {
  it('вне провайдера бросает ошибку', () => {
    const spy = jest.spyOn(console, 'error').mockImplementation(() => {});
    expect(() => renderHook(() => useAppContext())).toThrow('useAppContext must be used within an AppProvider');
    spy.mockRestore();
  });

  it('начальное состояние без сохранённых данных', async () => {
    const { result } = await setup();
    expect(result.current.state).toMatchObject({
      chats: [],
      currentChatId: null,
      folders: [],
      projects: [],
      isLoading: false,
      loadingChatIds: [],
      isRecording: false,
      isSpeaking: false,
      loadedDocument: null,
      stats: { totalMessages: 0, totalTokens: 0, sessionsToday: 0 },
    });
    expect(result.current.state.modelSettings.streaming_speed).toBe(20);
  });
});

describe('редьюсер: чаты и сообщения', () => {
  it('createChat создаёт чат и делает его текущим', async () => {
    const { result } = await setup();
    let id = '';
    act(() => {
      id = result.current.actions.createChat('Заметки');
    });
    expect(result.current.state.currentChatId).toBe(id);
    expect(result.current.state.chats[0]).toMatchObject({ id, title: 'Заметки', messages: [] });
    act(() => {
      result.current.actions.createChat();
    });
    expect(result.current.state.chats[1].title).toBe('Новый чат');
  });

  it('updateChatTitle меняет заголовок локально (без токена бэкенд не трогаем)', async () => {
    const { result } = await setup();
    let id = '';
    act(() => {
      id = result.current.actions.createChat('A');
    });
    act(() => result.current.actions.updateChatTitle(id, 'B'));
    expect(result.current.state.chats[0].title).toBe('B');
    expect(fetchMock.mock.calls.filter((c) => String(c[0]).includes('/title'))).toHaveLength(0);
  });

  it('addMessage: id msg_<12 hex>, обновляет статистику и updatedAt', async () => {
    const { result } = await setup();
    let cid = '';
    act(() => {
      cid = result.current.actions.createChat();
    });
    let mid = '';
    act(() => {
      mid = result.current.actions.addMessage(cid, { role: 'user', content: 'abcd', timestamp: 't' });
    });
    expect(mid).toMatch(/^msg_[0-9a-f]{12}$/);
    expect(result.current.state.chats[0].messages[0]).toMatchObject({ id: mid, content: 'abcd' });
    expect(result.current.state.stats.totalMessages).toBe(1);
    expect(result.current.state.stats.totalTokens).toBe(1);
  });

  it('сообщение пользователя снимает скрытие ветки в сайдбаре, сообщение ассистента — нет', async () => {
    const { result } = await setup();
    const cid = uid('br');
    act(() => {
      result.current.dispatch({
        type: 'CREATE_CHAT',
        payload: chat(cid, [], { hiddenFromSidebarUntilUserMessage: true }),
      } as any);
    });
    act(() => {
      result.current.actions.addMessage(cid, { role: 'assistant', content: 'a', timestamp: 't' });
    });
    expect(result.current.state.chats[0].hiddenFromSidebarUntilUserMessage).toBe(true);
    act(() => {
      result.current.actions.addMessage(cid, { role: 'user', content: 'u', timestamp: 't' });
    });
    expect(result.current.state.chats[0].hiddenFromSidebarUntilUserMessage).toBeUndefined();
  });

  it('updateChatMessages заменяет сообщения и сбрасывает признак выгрузки', async () => {
    const { result } = await setup();
    const cid = uid('um');
    act(() => {
      result.current.dispatch({
        type: 'CREATE_CHAT',
        payload: chat(cid, [], { messagesUnloaded: true, unloadedMessageCount: 4 }),
      } as any);
    });
    act(() => result.current.actions.updateChatMessages(cid, [msg('1', 'user', 'x')] as any));
    const c = result.current.state.chats[0];
    expect(c.messages).toHaveLength(1);
    expect(c.messagesUnloaded).toBe(false);
    expect(c.unloadedMessageCount).toBeUndefined();
  });

  it('UPDATE_MESSAGE меняет только переданные поля и пересчитывает токены', async () => {
    const { result } = await setup();
    const cid = uid('upd');
    act(() => {
      result.current.dispatch({
        type: 'CREATE_CHAT',
        payload: chat(cid, [msg('m1', 'assistant', 'abcd', { isStreaming: true })]),
      } as any);
    });
    const tokensBefore = result.current.state.stats.totalTokens;
    act(() =>
      result.current.actions.updateMessage(
        cid, 'm1', 'abcdefgh', false,
        [{ model: 'x', content: 'c' }], ['alt'], 1,
        { query: 'q', sourceFiles: [], hits: [] },
        'think',
        [{ type: 'mcp_tool_start', server_id: 's', tool: 't', qualified_name: 's.t' }],
        [{ name: 'f.txt', contentType: 'text' }],
        true,
        [[{ name: 'v.png', contentType: 'image' }]],
        { rating: 'like' } as any,
      ),
    );
    const m = result.current.state.chats[0].messages[0];
    expect(m).toMatchObject({
      content: 'abcdefgh',
      isStreaming: false,
      alternativeResponses: ['alt'],
      currentResponseIndex: 1,
      reasoningContent: 'think',
      isImageGenerating: true,
      feedback: { rating: 'like' },
    });
    expect(m.multiLLMResponses).toHaveLength(1);
    expect(m.documentSearch?.query).toBe('q');
    expect(m.mcpToolCalls).toHaveLength(1);
    expect(m.inlineAttachments).toHaveLength(1);
    expect(m.inlineAttachmentVariants).toHaveLength(1);
    expect(result.current.state.stats.totalTokens).toBe(tokensBefore + 1); // 'abcd'(1) → 'abcdefgh'(2)
  });

  it('UPDATE_MESSAGE без content не трогает content и токены', async () => {
    const { result } = await setup();
    const cid = uid('upd2');
    act(() => {
      result.current.dispatch({ type: 'CREATE_CHAT', payload: chat(cid, [msg('m1', 'assistant', 'abcd')]) } as any);
    });
    const before = result.current.state.stats.totalTokens;
    act(() => result.current.actions.updateMessage(cid, 'm1', undefined, true));
    expect(result.current.state.chats[0].messages[0]).toMatchObject({ content: 'abcd', isStreaming: true });
    expect(result.current.state.stats.totalTokens).toBe(before);
  });

  it('UPDATE_MESSAGE с null сбрасывает documentSearch/reasoning/mcp (перегенерация)', async () => {
    const { result } = await setup();
    const cid = uid('clr');
    act(() => {
      result.current.dispatch({
        type: 'CREATE_CHAT',
        payload: chat(cid, [
          msg('m1', 'assistant', 'old', {
            documentSearch: { query: 'q', sourceFiles: ['a.docx'], hits: [] },
            reasoningContent: 'think',
            mcpToolCalls: [{ type: 'mcp_tool_start', server_id: 's', tool: 't', qualified_name: 's.t' }],
          }),
        ]),
      } as any);
    });
    act(() =>
      result.current.actions.updateMessage(
        cid, 'm1', '', true, undefined, ['old', ''], 1, null, null, null,
      ),
    );
    const m = result.current.state.chats[0].messages[0];
    expect(m.content).toBe('');
    expect(m.isStreaming).toBe(true);
    expect(m.currentResponseIndex).toBe(1);
    expect(m.documentSearch).toBeUndefined();
    expect(m.reasoningContent).toBeUndefined();
    expect(m.mcpToolCalls).toBeUndefined();
  });

  it('UPDATE_MESSAGE при завершении стрима фиксирует generationDurationSec (мин. 1 с)', async () => {
    const { result } = await setup();
    const cid = uid('dur');
    const started = Date.now() - 5400;
    act(() => {
      result.current.dispatch({
        type: 'CREATE_CHAT',
        payload: chat(cid, [msg('m1', 'assistant', 'x', { isStreaming: true, generationStartedAtMs: started })]),
      } as any);
    });
    act(() => result.current.actions.updateMessage(cid, 'm1', undefined, false));
    expect(result.current.state.chats[0].messages[0].generationDurationSec).toBeGreaterThanOrEqual(5);
    // повторное завершение уже завершённого сообщения длительность не меняет
    const dur = result.current.state.chats[0].messages[0].generationDurationSec;
    act(() => result.current.actions.updateMessage(cid, 'm1', undefined, false));
    expect(result.current.state.chats[0].messages[0].generationDurationSec).toBe(dur);
  });

  it('UPDATE_MESSAGE: очень короткая генерация округляется вверх до 1 с', async () => {
    const { result } = await setup();
    const cid = uid('dur1');
    act(() => {
      result.current.dispatch({
        type: 'CREATE_CHAT',
        payload: chat(cid, [msg('m1', 'assistant', 'x', { isStreaming: true, generationStartedAtMs: Date.now() })]),
      } as any);
    });
    act(() => result.current.actions.updateMessage(cid, 'm1', undefined, false));
    expect(result.current.state.chats[0].messages[0].generationDurationSec).toBe(1);
  });

  it('UPDATE_MESSAGE несуществующего сообщения/чата ничего не ломает', async () => {
    const { result } = await setup();
    const cid = uid('none');
    act(() => {
      result.current.dispatch({ type: 'CREATE_CHAT', payload: chat(cid, [msg('m1', 'assistant', 'x')]) } as any);
    });
    act(() => result.current.actions.updateMessage(cid, 'zzz', 'new'));
    act(() => result.current.actions.updateMessage('nochat', 'm1', 'new'));
    expect(result.current.state.chats[0].messages[0].content).toBe('x');
  });

  it('patchMessageFields применяет поля; без изменений возвращает то же состояние', async () => {
    const { result } = await setup();
    const cid = uid('patch');
    act(() => {
      result.current.dispatch({ type: 'CREATE_CHAT', payload: chat(cid, [msg('m1', 'assistant', 'x')]) } as any);
    });
    act(() => result.current.actions.patchMessageFields(cid, 'm1', { followUpSuggestionsLoading: true }));
    expect(result.current.state.chats[0].messages[0].followUpSuggestionsLoading).toBe(true);
    const stateBefore = result.current.state;
    act(() => result.current.actions.patchMessageFields(cid, 'm1', { followUpSuggestionsLoading: true }));
    expect(result.current.state).toBe(stateBefore);
    act(() => result.current.actions.patchMessageFields(cid, 'nomsg', { followUpSuggestionsLoading: false }));
    expect(result.current.state).toBe(stateBefore);
    act(() => result.current.actions.patchMessageFields('nochat', 'm1', { followUpSuggestionsLoading: false }));
    expect(result.current.state).toBe(stateBefore);
  });

  describe('appendChunk (склейка чанков стрима)', () => {
    const run = async (existing: string, chunk: string, isStreaming?: boolean) => {
      const { result } = await setup();
      const cid = uid('chunk');
      act(() => {
        result.current.dispatch({
          type: 'CREATE_CHAT',
          payload: chat(cid, [msg('m1', 'assistant', existing, { isStreaming: true })]),
        } as any);
      });
      act(() => result.current.actions.appendChunk(cid, 'm1', chunk, isStreaming));
      return result.current.state.chats[0].messages[0];
    };

    it('простое склеивание', async () => {
      expect((await run('Привет', ', мир')).content).toBe('Привет, мир');
    });
    it('пустой существующий / пустой новый', async () => {
      expect((await run('', 'abc')).content).toBe('abc');
      expect((await run('abc', '')).content).toBe('abc');
    });
    it('после ```python добавляется перенос строки', async () => {
      expect((await run('```python', 'print(1)')).content).toBe('```python\nprint(1)');
    });
    it('кодовый блок после текста отделяется пустой строкой', async () => {
      expect((await run('Вот код', '```js')).content).toBe('Вот код\n\n```js');
    });
    it('кодовый блок после знака препинания — без вставок', async () => {
      expect((await run('Вот код:', '```js')).content).toBe('Вот код:```js');
    });
    it('isStreaming передаётся в сообщение; токены чанка учитываются', async () => {
      const { result } = await setup();
      const cid = uid('chunk2');
      act(() => {
        result.current.dispatch({ type: 'CREATE_CHAT', payload: chat(cid, [msg('m1', 'assistant', 'a', { isStreaming: true })]) } as any);
      });
      const before = result.current.state.stats.totalTokens;
      act(() => result.current.actions.appendChunk(cid, 'm1', 'abcdefgh', false));
      expect(result.current.state.chats[0].messages[0].isStreaming).toBe(false);
      expect(result.current.state.stats.totalTokens).toBe(before + 2);
    });
    it('чанк для несуществующего сообщения не создаёт его', async () => {
      const { result } = await setup();
      const cid = uid('chunk3');
      act(() => {
        result.current.dispatch({ type: 'CREATE_CHAT', payload: chat(cid, []) } as any);
      });
      act(() => result.current.actions.appendChunk(cid, 'nomsg', 'abc'));
      expect(result.current.state.chats[0].messages).toEqual([]);
    });
  });

  it('clearMessages очищает чат и обнуляет общую статистику (known issue: сбрасывается статистика всех чатов)', async () => {
    const { result } = await setup();
    const a = uid('cm-a');
    const b = uid('cm-b');
    act(() => {
      result.current.dispatch({ type: 'CREATE_CHAT', payload: chat(a, []) } as any);
      result.current.dispatch({ type: 'CREATE_CHAT', payload: chat(b, []) } as any);
    });
    act(() => {
      result.current.actions.addMessage(a, { role: 'user', content: 'aaaa', timestamp: 't' });
      result.current.actions.addMessage(b, { role: 'user', content: 'bbbb', timestamp: 't' });
    });
    expect(result.current.state.stats.totalMessages).toBe(2);
    act(() => result.current.actions.clearMessages(a));
    expect(result.current.state.chats.find((c) => c.id === a)!.messages).toEqual([]);
    expect(result.current.state.chats.find((c) => c.id === b)!.messages).toHaveLength(1);
    // текущее поведение: статистика обнуляется целиком, хотя в чате b сообщение осталось
    expect(result.current.state.stats.totalMessages).toBe(0);
  });

  it('удаление чата: событие CHAT_DELETED_EVENT, текущий чат сбрасывается, loading снимается', async () => {
    const { result } = await setup();
    const handler = jest.fn();
    window.addEventListener(CHAT_DELETED_EVENT, handler);
    let id = '';
    act(() => {
      id = result.current.actions.createChat();
    });
    act(() => result.current.actions.setChatLoading(id, true));
    act(() => result.current.actions.deleteChat(id));
    window.removeEventListener(CHAT_DELETED_EVENT, handler);
    expect((handler.mock.calls[0][0] as CustomEvent).detail).toEqual({ chatId: id });
    expect(result.current.state.chats).toEqual([]);
    expect(result.current.state.currentChatId).toBeNull();
    expect(result.current.state.loadingChatIds).toEqual([]);
  });

  it('удаление не текущего чата оставляет currentChatId', async () => {
    const { result } = await setup();
    let a = '';
    let b = '';
    act(() => {
      a = result.current.actions.createChat();
    });
    act(() => {
      b = result.current.actions.createChat();
    });
    act(() => result.current.actions.deleteChat(a));
    expect(result.current.state.currentChatId).toBe(b);
  });

  it('deleteAllChats: событие с chatId=null, чаты/папки/loading очищены', async () => {
    const { result } = await setup();
    const handler = jest.fn();
    window.addEventListener(CHAT_DELETED_EVENT, handler);
    act(() => {
      const id = result.current.actions.createChat();
      result.current.actions.createFolder('F');
      result.current.actions.setChatLoading(id, true);
    });
    act(() => result.current.actions.deleteAllChats());
    window.removeEventListener(CHAT_DELETED_EVENT, handler);
    expect((handler.mock.calls[0][0] as CustomEvent).detail).toEqual({ chatId: null });
    expect(result.current.state).toMatchObject({ chats: [], folders: [], loadingChatIds: [], currentChatId: null });
  });
});

describe('редьюсер: простые поля состояния', () => {
  it('loading / chat loading (без дублей) / модель / голос / транскрибация / документ', async () => {
    const { result } = await setup();
    act(() => result.current.actions.setLoading(true));
    expect(result.current.state.isLoading).toBe(true);
    act(() => result.current.actions.setChatLoading('c1', true));
    act(() => result.current.actions.setChatLoading('c1', true));
    act(() => result.current.actions.setChatLoading('c2', true));
    expect(result.current.state.loadingChatIds).toEqual(['c1', 'c2']);
    act(() => result.current.actions.setChatLoading('c1', false));
    expect(result.current.state.loadingChatIds).toEqual(['c2']);

    act(() => result.current.actions.setCurrentModel({ loaded: true, path: 'm' }));
    expect(result.current.state.currentModel).toEqual({ loaded: true, path: 'm' });
    const settings = { ...result.current.state.modelSettings, temperature: 0.1 };
    act(() => result.current.actions.setModelSettings(settings));
    expect(result.current.state.modelSettings.temperature).toBe(0.1);
    act(() => result.current.actions.setRecording(true));
    act(() => result.current.actions.setSpeaking(true));
    expect(result.current.state.isRecording).toBe(true);
    expect(result.current.state.isSpeaking).toBe(true);
    act(() => result.current.actions.setTranscriptionSettings({ engine: 'vosk', language: 'en', auto_detect: false }));
    expect(result.current.state.transcriptionSettings).toEqual({ engine: 'vosk', language: 'en', auto_detect: false });

    act(() => {
      result.current.dispatch({ type: 'SET_AVAILABLE_MODELS', payload: [{ path: 'a' }] } as any);
      result.current.dispatch({ type: 'SET_VOICE_SETTINGS', payload: { voice_id: 'en', speech_rate: 2 } } as any);
      result.current.dispatch({ type: 'SET_LOADED_DOCUMENT', payload: 'doc.pdf' } as any);
      result.current.dispatch({ type: 'UPDATE_STATS', payload: { sessionsToday: 4 } } as any);
      result.current.dispatch({ type: 'SET_INITIALIZED', payload: true } as any);
    });
    expect(result.current.state.availableModels).toEqual([{ path: 'a' }]);
    expect(result.current.state.voiceSettings).toEqual({ voice_id: 'en', speech_rate: 2 });
    expect(result.current.state.loadedDocument).toBe('doc.pdf');
    expect(result.current.state.stats.sessionsToday).toBe(4);
  });

  it('уведомления: добавление и удаление', async () => {
    const { result } = await setup();
    act(() => result.current.actions.showNotification('error', 'Ой'));
    const n = result.current.state.notifications[0];
    expect(n).toMatchObject({ type: 'error', message: 'Ой' });
    expect(typeof n.id).toBe('string');
    act(() => result.current.actions.removeNotification(n.id));
    expect(result.current.state.notifications).toEqual([]);
  });

  it('неизвестное действие возвращает прежнее состояние', async () => {
    const { result } = await setup();
    const before = result.current.state;
    act(() => result.current.dispatch({ type: 'NOPE' } as any));
    expect(result.current.state).toBe(before);
  });
});

describe('редьюсер: папки', () => {
  it('создание, переименование, раскрытие, удаление', async () => {
    const { result } = await setup();
    let fid = '';
    act(() => {
      fid = result.current.actions.createFolder('Работа');
    });
    expect(result.current.state.folders[0]).toEqual({ id: fid, name: 'Работа', chatIds: [], expanded: true });
    act(() => result.current.actions.updateFolder(fid, 'Дом'));
    expect(result.current.actions.getFolders()[0].name).toBe('Дом');
    act(() => result.current.actions.toggleFolder(fid));
    expect(result.current.state.folders[0].expanded).toBe(false);
    act(() => result.current.actions.deleteFolder(fid));
    expect(result.current.state.folders).toEqual([]);
  });

  it('moveChatToFolder переносит чат между папками и из папки', async () => {
    const { result } = await setup();
    let f1 = '';
    let f2 = '';
    act(() => {
      f1 = result.current.actions.createFolder('A');
      f2 = result.current.actions.createFolder('B');
    });
    act(() => result.current.actions.moveChatToFolder('c1', f1));
    expect(result.current.state.folders[0].chatIds).toEqual(['c1']);
    act(() => result.current.actions.moveChatToFolder('c1', f2));
    expect(result.current.state.folders.map((f) => f.chatIds)).toEqual([[], ['c1']]);
    act(() => result.current.actions.moveChatToFolder('c1', null));
    expect(result.current.state.folders.map((f) => f.chatIds)).toEqual([[], []]);
  });

  it('папка «Закреплено» удаляется, когда в ней не остаётся чатов', async () => {
    const { result } = await setup();
    let pin = '';
    act(() => {
      pin = result.current.actions.createFolder('Закреплено');
      result.current.actions.createFolder('Другая');
    });
    act(() => result.current.actions.moveChatToFolder('c1', pin));
    expect(result.current.state.folders).toHaveLength(2);
    act(() => result.current.actions.moveChatToFolder('c1', null));
    expect(result.current.state.folders.map((f) => f.name)).toEqual(['Другая']);
  });

  it('(known issue) повторный перенос чата в ту же папку дублирует id в chatIds', async () => {
    const { result } = await setup();
    let f1 = '';
    act(() => {
      f1 = result.current.actions.createFolder('A');
    });
    act(() => result.current.actions.moveChatToFolder('c1', f1));
    act(() => result.current.actions.moveChatToFolder('c1', f1));
    // текущее поведение: дубль; правильно было бы ['c1']
    expect(result.current.state.folders[0].chatIds).toEqual(['c1', 'c1']);
  });
});

describe('редьюсер: архив', () => {
  const seed = async () => {
    const hook = await setup();
    const ids = [uid('ar-a'), uid('ar-b'), uid('ar-c')];
    act(() => {
      ids.forEach((id) => hook.result.current.dispatch({ type: 'CREATE_CHAT', payload: chat(id, [msg('m', 'user', 'x')]) } as any));
    });
    return { ...hook, ids };
  };

  it('archiveChat: помечает чат; сбрасывает currentChatId только для текущего', async () => {
    const { result, ids } = await seed();
    expect(result.current.state.currentChatId).toBe(ids[2]);
    act(() => result.current.actions.archiveChat(ids[0]));
    expect(result.current.state.chats[0].isArchived).toBe(true);
    expect(result.current.state.currentChatId).toBe(ids[2]);
    act(() => result.current.actions.archiveChat(ids[2]));
    expect(result.current.state.currentChatId).toBeNull();
    expect(result.current.actions.getArchivedChats().map((c) => c.id)).toEqual([ids[0], ids[2]]);
  });

  it('unarchiveChat снимает флаг', async () => {
    const { result, ids } = await seed();
    act(() => result.current.actions.archiveChat(ids[0]));
    act(() => result.current.actions.unarchiveChat(ids[0]));
    expect(result.current.state.chats[0].isArchived).toBe(false);
  });

  it('archiveAllChats / unarchiveAllChats', async () => {
    const { result } = await seed();
    act(() => result.current.actions.archiveAllChats());
    expect(result.current.state.chats.every((c) => c.isArchived)).toBe(true);
    expect(result.current.state.currentChatId).toBeNull();
    act(() => result.current.actions.unarchiveAllChats());
    expect(result.current.state.chats.every((c) => c.isArchived === false)).toBe(true);
  });

  it('archiveFolder архивирует чаты папки, сбрасывает current, если он внутри; неизвестная папка — no-op', async () => {
    const { result, ids } = await seed();
    let fid = '';
    act(() => {
      fid = result.current.actions.createFolder('F');
    });
    act(() => result.current.actions.moveChatToFolder(ids[0], fid));
    act(() => result.current.actions.archiveFolder(fid));
    expect(result.current.state.chats[0].isArchived).toBe(true);
    expect(result.current.state.chats[1].isArchived).toBeUndefined();
    expect(result.current.state.currentChatId).toBe(ids[2]);
    act(() => result.current.actions.moveChatToFolder(ids[2], fid));
    act(() => result.current.actions.archiveFolder(fid));
    expect(result.current.state.currentChatId).toBeNull();
    const before = result.current.state;
    act(() => result.current.actions.archiveFolder('missing'));
    expect(result.current.state).toBe(before);
  });
});

describe('редьюсер: проекты (локально, без токена)', () => {
  const projectData = { name: 'P', memory: 'default' as const, instructions: 'i' };

  it('createProject / updateProject / getters / deleteProject', async () => {
    const { result } = await setup();
    let pid = '';
    act(() => {
      pid = result.current.actions.createProject(projectData);
    });
    expect(result.current.state.projects[0]).toMatchObject({ id: pid, name: 'P' });
    expect(result.current.actions.getProjectById(pid)?.name).toBe('P');
    expect(result.current.actions.getProjectById('zz')).toBeNull();
    act(() => result.current.actions.updateProject(pid, { name: 'Q' }));
    expect(result.current.actions.getProjects()[0].name).toBe('Q');
    act(() => result.current.actions.deleteProject(pid));
    expect(result.current.state.projects).toEqual([]);
    expect(mockSyncCreate).not.toHaveBeenCalled();
    expect(fetchMock.mock.calls.filter((c) => String(c[0]).includes('/api/projects'))).toHaveLength(0);
  });

  it('moveChatToProject и togglePinInProject', async () => {
    const { result } = await setup();
    let cid = '';
    act(() => {
      cid = result.current.actions.createChat();
    });
    act(() => result.current.actions.moveChatToProject(cid, 'p1'));
    expect(result.current.state.chats[0].projectId).toBe('p1');
    act(() => result.current.actions.moveChatToProject(cid, null));
    expect(result.current.state.chats[0].projectId).toBeUndefined();
    act(() => result.current.actions.togglePinInProject(cid));
    expect(result.current.state.chats[0].isPinnedInProject).toBe(true);
    act(() => result.current.actions.togglePinInProject(cid));
    expect(result.current.state.chats[0].isPinnedInProject).toBe(false);
  });
});

describe('редьюсер: текущий чат, выгрузка и LRU-кэш', () => {
  const bigChat = (id: string, n = 1) => chat(id, Array.from({ length: n }, (_, i) => msg(`${id}-m${i}`, 'user', `t${i}`)));

  it('RESTORE_CHATS с unloadInactive выгружает неактивные чаты, текущий оставляет', async () => {
    const { result } = await setup();
    const [a, b] = [uid('rs-a'), uid('rs-b')];
    act(() =>
      result.current.dispatch({
        type: 'RESTORE_CHATS',
        payload: { chats: [bigChat(a, 2), bigChat(b, 3)], currentChatId: a, folders: [{ id: 'f', name: 'F', chatIds: [], expanded: true }], unloadInactive: true },
      } as any),
    );
    const [ca, cb] = result.current.state.chats;
    expect(ca.messages).toHaveLength(2);
    expect(cb).toMatchObject({ messages: [], messagesUnloaded: true, unloadedMessageCount: 3 });
    expect(result.current.state.folders).toHaveLength(1);
    expect(result.current.state.isInitialized).toBe(true);
  });

  it('RESTORE_CHATS не выгружает чат с живой работой (стрим или loadingChatIds)', async () => {
    const { result } = await setup();
    const [cur, streaming, imgGen, multi, loading, idle] = ['cur', 'st', 'img', 'mu', 'lo', 'id'].map(uid);
    act(() => result.current.actions.setChatLoading(loading, true));
    act(() =>
      result.current.dispatch({
        type: 'RESTORE_CHATS',
        payload: {
          currentChatId: cur,
          folders: [],
          chats: [
            bigChat(cur),
            chat(streaming, [msg('1', 'assistant', 'x', { isStreaming: true })]),
            chat(imgGen, [msg('1', 'assistant', 'x', { isImageGenerating: true })]),
            chat(multi, [msg('1', 'assistant', 'x', { multiLLMResponses: [{ model: 'm', content: '', isStreaming: true }] })]),
            bigChat(loading),
            bigChat(idle),
          ],
        },
      } as any),
    );
    const byId = Object.fromEntries(result.current.state.chats.map((c) => [c.id, c]));
    expect(byId[streaming].messagesUnloaded).toBeUndefined();
    expect(byId[imgGen].messagesUnloaded).toBeUndefined();
    expect(byId[multi].messagesUnloaded).toBeUndefined();
    expect(byId[loading].messagesUnloaded).toBeUndefined();
    expect(byId[idle].messagesUnloaded).toBe(true);
  });

  it('RESTORE_CHATS: пустой серверный чат сохраняет локальные сообщения; isArchived приводится к boolean', async () => {
    const { result } = await setup();
    const id = uid('keep');
    act(() => result.current.dispatch({ type: 'CREATE_CHAT', payload: bigChat(id, 2) } as any));
    act(() =>
      result.current.dispatch({
        type: 'RESTORE_CHATS',
        payload: { chats: [chat(id, [], { isArchived: undefined })], currentChatId: id, folders: [] },
      } as any),
    );
    const c = result.current.state.chats[0];
    expect(c.messages).toHaveLength(2);
    expect(c.isArchived).toBe(false);
  });

  it('RESTORE_CHATS без папок ставит пустой список; без currentChatId не гидратирует из кэша', async () => {
    const { result } = await setup();
    act(() =>
      result.current.dispatch({
        type: 'RESTORE_CHATS',
        payload: { chats: [bigChat(uid('nc'))], currentChatId: null, folders: undefined, unloadInactive: false },
      } as any),
    );
    expect(result.current.state.folders).toEqual([]);
    expect(result.current.state.currentChatId).toBeNull();
  });

  it('SET_CURRENT_CHAT: выгружает все кроме нового и предыдущего; возврат гидратируется из кэша', async () => {
    const { result } = await setup();
    const [a, b, c] = [uid('sc-a'), uid('sc-b'), uid('sc-c')];
    act(() => {
      [a, b, c].forEach((id) => result.current.dispatch({ type: 'CREATE_CHAT', payload: bigChat(id, 2) } as any));
    });
    // current = c; переключаем на b → a выгружается, c остаётся (предыдущий)
    act(() => result.current.dispatch({ type: 'SET_CURRENT_CHAT', payload: b } as any));
    const st = () => Object.fromEntries(result.current.state.chats.map((x) => [x.id, x]));
    expect(st()[a].messagesUnloaded).toBe(true);
    expect(st()[c].messagesUnloaded).toBeUndefined();
    // переключаем на a: сообщения возвращаются из LRU-кэша, b (предыдущий) остаётся, c выгружается
    act(() => result.current.dispatch({ type: 'SET_CURRENT_CHAT', payload: a } as any));
    expect(st()[a].messages).toHaveLength(2);
    expect(st()[a].messagesUnloaded).toBe(false);
    expect(st()[c].messagesUnloaded).toBe(true);
  });

  it('SET_CURRENT_CHAT(null) с выгрузкой: чат не активируется', async () => {
    const { result } = await setup();
    const a = uid('sn-a');
    act(() => result.current.dispatch({ type: 'CREATE_CHAT', payload: bigChat(a) } as any));
    act(() => result.current.dispatch({ type: 'SET_CURRENT_CHAT', payload: null } as any));
    expect(result.current.state.currentChatId).toBeNull();
  });

  it('SET_CURRENT_CHAT с unloadInactive=false: без выгрузки, но с гидратацией из кэша; null — только сброс', async () => {
    const { result } = await setup();
    const [a, b] = [uid('nu-a'), uid('nu-b')];
    act(() => {
      result.current.dispatch({ type: 'CREATE_CHAT', payload: bigChat(a, 2) } as any);
      result.current.dispatch({ type: 'CREATE_CHAT', payload: bigChat(b, 1) } as any);
    });
    // имитируем выгрузку a (сообщения остались в кэше после CREATE_CHAT? кэш наполняется через UPDATE_CHAT_MESSAGES)
    act(() => result.current.actions.updateChatMessages(a, bigChat(a, 2).messages as any));
    act(() => result.current.dispatch({ type: 'SET_CURRENT_CHAT', payload: b } as any)); // a выгрузился
    expect(result.current.state.chats[0].messagesUnloaded).toBe(true);
    act(() => result.current.actions.setCurrentChat(a)); // без токена: unloadInactive=false
    expect(result.current.state.chats[0].messages).toHaveLength(2);
    expect(result.current.state.chats[1].messagesUnloaded).toBeUndefined(); // b не выгружен
    act(() => result.current.actions.setCurrentChat(null));
    expect(result.current.state.currentChatId).toBeNull();
  });

  it('setCurrentChat с токеном запрашивает выгрузку неактивных (canRehydrate)', async () => {
    mockUseAuth.mockReturnValue({ token: 'tok' });
    fetchMock.mockResolvedValue({ ok: false, json: () => Promise.resolve({}) });
    mockFetchProjects.mockResolvedValue([]);
    const { result } = await setup();
    const [a, b, c] = [uid('tk-a'), uid('tk-b'), uid('tk-c')];
    act(() => {
      [a, b, c].forEach((id) => result.current.dispatch({ type: 'CREATE_CHAT', payload: bigChat(id, 1) } as any));
    });
    act(() => result.current.actions.setCurrentChat(a));
    act(() => result.current.actions.setCurrentChat(b));
    const byId = Object.fromEntries(result.current.state.chats.map((x) => [x.id, x]));
    expect(byId[c].messagesUnloaded).toBe(true);
  });

  it('LRU-кэш ограничен 16 чатами: самый старый вытесняется и не восстанавливается', async () => {
    const { result } = await setup();
    const ids = Array.from({ length: 18 }, () => uid('lru'));
    act(() => {
      ids.forEach((id) => result.current.actions.updateChatMessages(id, [msg(`${id}-1`, 'user', 'x')] as any));
    });
    // чаты в state отсутствуют → создаём пустые и "открываем"
    act(() => {
      ids.forEach((id) => result.current.dispatch({ type: 'CREATE_CHAT', payload: chat(id, [], { messagesUnloaded: true }) } as any));
    });
    act(() => result.current.dispatch({ type: 'SET_CURRENT_CHAT', payload: ids[0] } as any));
    act(() => result.current.dispatch({ type: 'SET_CURRENT_CHAT', payload: ids[17] } as any));
    const byId = Object.fromEntries(result.current.state.chats.map((x) => [x.id, x]));
    expect(byId[ids[0]].messages).toEqual([]); // вытеснен (0 и 1 — самые старые)
    expect(byId[ids[17]].messages).toHaveLength(1);
  });

  it('пустой массив сообщений в кэш не кладётся', async () => {
    const { result } = await setup();
    const id = uid('empty');
    act(() => result.current.actions.updateChatMessages(id, []));
    act(() => result.current.dispatch({ type: 'CREATE_CHAT', payload: chat(id, [], { messagesUnloaded: true, unloadedMessageCount: 1 }) } as any));
    act(() => result.current.dispatch({ type: 'SET_CURRENT_CHAT', payload: id } as any));
    expect(result.current.state.chats[0].messages).toEqual([]);
  });
});

describe('localStorage: сохранение состояния', () => {
  const lastSaved = () => JSON.parse(localStorage.getItem('memo-chats') || 'null');

  it('сохраняет чаты, текущий чат, папки и проекты', async () => {
    const { result } = await setup();
    let id = '';
    act(() => {
      id = result.current.actions.createChat('Сохр');
      result.current.actions.createFolder('F');
    });
    const saved = lastSaved();
    expect(saved.currentChatId).toBe(id);
    expect(saved.chats[0].title).toBe('Сохр');
    expect(saved.folders).toHaveLength(1);
    expect(saved.projects).toEqual([]);
  });

  it('облегчает сообщения: вырезает data-URL превью, режет hits, MCP и длинные тексты', async () => {
    const { result } = await setup();
    const cid = uid('slim');
    const long = 'я'.repeat(9000);
    const heavy = msg('m1', 'assistant', long, {
      reasoningContent: 'р'.repeat(5000),
      inlineAttachments: [
        { name: 'a.png', contentType: 'image', preview: 'data:image/png;base64,AAAA' },
        { name: 'b.png', contentType: 'image', preview: 'http://x/b.png' },
      ],
      inlineAttachmentVariants: [[{ name: 'v.png', contentType: 'image', preview: 'data:image/png;base64,BBBB' }]],
      documentSearch: {
        query: 'q',
        sourceFiles: [],
        hits: [{ file: 'f', anchor: '', relevance: 1, content: 'x'.repeat(500), chunkIndex: 0, documentId: 1, store: 's' }],
      },
      mcpToolCalls: [
        { type: 'mcp_tool_end', server_id: 's', tool: 't', qualified_name: 's.t', result: 'big', result_preview: 'p'.repeat(500), arguments: { a: 1 } },
      ],
    });
    act(() => {
      result.current.dispatch({ type: 'CREATE_CHAT', payload: chat(cid, [heavy]) } as any);
    });
    const m = lastSaved().chats.find((c: any) => c.id === cid).messages[0];
    expect(m.content.length).toBe(8002);
    expect(m.content.endsWith('\n…')).toBe(true);
    expect(m.reasoningContent.length).toBe(4002);
    expect(m.inlineAttachments[0].preview).toBeUndefined();
    expect(m.inlineAttachments[1].preview).toBe('http://x/b.png');
    expect(m.inlineAttachmentVariants[0][0].preview).toBeUndefined();
    expect(m.documentSearch.hits[0].content).toHaveLength(200);
    expect(m.mcpToolCalls[0].result).toBeUndefined();
    expect(m.mcpToolCalls[0].arguments).toBeUndefined();
    expect(m.mcpToolCalls[0].result_preview).toHaveLength(200);
    // в самом state полные данные остаются
    expect(result.current.state.chats.find((c) => c.id === cid)!.messages[0].content).toHaveLength(9000);
  });

  it('хранит не более 40 последних по updatedAt чатов и 80 сообщений на чат', async () => {
    const { result } = await setup();
    const many = Array.from({ length: 45 }, (_, i) =>
      chat(uid('many'), [], { updatedAt: `2024-01-${String(i + 1).padStart(2, '0')}T00:00:00.000Z` }),
    );
    const msgs = Array.from({ length: 100 }, (_, i) => msg(`mm${i}`, 'user', `t${i}`));
    act(() => {
      result.current.dispatch({
        type: 'RESTORE_CHATS',
        payload: { chats: [...many.slice(0, 44), chat('long-chat', msgs, { updatedAt: '2030-01-01T00:00:00.000Z' })], currentChatId: 'long-chat', folders: [], unloadInactive: false },
      } as any);
    });
    const saved = lastSaved();
    expect(saved.chats).toHaveLength(40);
    expect(saved.chats[0].id).toBe('long-chat');
    expect(saved.chats[0].messages).toHaveLength(80);
    expect(saved.chats[0].messages[0].id).toBe('mm20');
  });

  it('при QuotaExceededError сначала урезает до 20×30, затем до метаданных', async () => {
    const { result } = await setup();
    const realSet = Storage.prototype.setItem;
    const sizes: number[] = [];
    const spy = jest.spyOn(Storage.prototype, 'setItem').mockImplementation(function (this: Storage, k: string, v: string) {
      if (k === 'memo-chats') {
        sizes.push(v.length);
        if (sizes.length <= 2) throw new DOMException('quota', 'QuotaExceededError');
      }
      return realSet.call(this, k, v);
    });
    const warn = jest.spyOn(console, 'warn').mockImplementation(() => {});
    act(() => {
      result.current.dispatch({ type: 'CREATE_CHAT', payload: chat(uid('q'), [msg('1', 'user', 'text')]) } as any);
    });
    expect(sizes.length).toBeGreaterThanOrEqual(3);
    const saved = lastSaved();
    expect(saved.chats.every((c: any) => c.messages.length === 0)).toBe(true);
    expect(warn).toHaveBeenCalledWith('localStorage: сохранены только метаданные чатов (квота переполнена)');
    spy.mockRestore();
    warn.mockRestore();
  });

  it('второй уровень (20×30) срабатывает, если первый не влез', async () => {
    const { result } = await setup();
    const realSet = Storage.prototype.setItem;
    let attempts = 0;
    const spy = jest.spyOn(Storage.prototype, 'setItem').mockImplementation(function (this: Storage, k: string, v: string) {
      if (k === 'memo-chats') {
        attempts += 1;
        if (attempts === 1) throw new DOMException('quota', 'QuotaExceededError');
      }
      return realSet.call(this, k, v);
    });
    act(() => {
      result.current.dispatch({ type: 'CREATE_CHAT', payload: chat(uid('q2'), [msg('1', 'user', 'text')]) } as any);
    });
    expect(attempts).toBe(2);
    expect(lastSaved().chats.some((c: any) => c.messages.length > 0)).toBe(true);
    spy.mockRestore();
  });

  it('если не влезают даже метаданные — console.error с подсказкой', async () => {
    const { result } = await setup();
    const spy = jest.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('quota', 'QuotaExceededError');
    });
    const err = jest.spyOn(console, 'error').mockImplementation(() => {});
    act(() => {
      result.current.dispatch({ type: 'CREATE_CHAT', payload: chat(uid('q3'), []) } as any);
    });
    expect(err.mock.calls.some((c) => String(c[0]).includes('QuotaExceededError'))).toBe(true);
    spy.mockRestore();
    err.mockRestore();
  });

  it('иная ошибка записи логируется и не запускает повторные попытки', async () => {
    const { result } = await setup();
    let attempts = 0;
    const spy = jest.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      attempts += 1;
      throw new Error('disk failure');
    });
    const err = jest.spyOn(console, 'error').mockImplementation(() => {});
    act(() => {
      result.current.dispatch({ type: 'CREATE_CHAT', payload: chat(uid('q4'), []) } as any);
    });
    expect(attempts).toBe(1);
    expect(err).toHaveBeenCalledWith('Ошибка сохранения состояния в localStorage:', expect.any(Error));
    spy.mockRestore();
    err.mockRestore();
  });
});

describe('экспорт и импорт чатов', () => {
  it('exportChats скачивает JSON с чатами и папками', async () => {
    const { result } = await setup();
    act(() => {
      result.current.actions.createChat('Экспорт');
    });
    const created: string[] = [];
    (URL as any).createObjectURL = jest.fn((b: Blob) => {
      created.push(String(b.size));
      return 'blob:x';
    });
    (URL as any).revokeObjectURL = jest.fn();
    const clicked: HTMLAnchorElement[] = [];
    const clickSpy = jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      clicked.push(this);
    });
    act(() => result.current.actions.exportChats());
    expect(clicked).toHaveLength(1);
    expect(clicked[0].download).toMatch(/^chats_export_\d{4}-\d{2}-\d{2}\.json$/);
    expect(clicked[0].href).toBe('blob:x');
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:x');
    expect(document.body.querySelector('a')).toBeNull();
    const blobArg = ((URL as any).createObjectURL as jest.Mock).mock.calls[0][0] as Blob;
    expect(blobArg.type).toBe('application/json');
    clickSpy.mockRestore();
  });

  it('importChats восстанавливает чаты и папки из файла', async () => {
    const { result } = await setup();
    const id = uid('imp');
    const file = new File([JSON.stringify({ chats: [chat(id, [msg('1', 'user', 'hi')])], currentChatId: id, folders: [{ id: 'f', name: 'F', chatIds: [], expanded: true }] })], 'x.json');
    let ok: boolean | undefined;
    await act(async () => {
      ok = await result.current.actions.importChats(file);
    });
    expect(ok).toBe(true);
    expect(result.current.state.chats[0].id).toBe(id);
    expect(result.current.state.currentChatId).toBe(id);
    expect(result.current.state.folders).toHaveLength(1);
  });

  it('importChats без массива chats отклоняет промис с понятной ошибкой', async () => {
    const { result } = await setup();
    const file = new File([JSON.stringify({ foo: 1 })], 'x.json');
    await expect(result.current.actions.importChats(file)).rejects.toThrow('Неверный формат файла: отсутствует массив чатов');
  });

  it('importChats с битым JSON отклоняется', async () => {
    const { result } = await setup();
    await expect(result.current.actions.importChats(new File(['{bad'], 'x.json'))).rejects.toThrow();
  });

  it('importChats: ошибка чтения файла', async () => {
    const { result } = await setup();
    const spy = jest.spyOn(FileReader.prototype, 'readAsText').mockImplementation(function (this: FileReader) {
      setTimeout(() => (this as any).onerror?.(new Error('x')), 0);
    });
    await expect(result.current.actions.importChats(new File(['x'], 'x.json'))).rejects.toThrow('Ошибка чтения файла');
    spy.mockRestore();
  });

  it('getCurrentChat / getCurrentMessages / getChatById', async () => {
    const { result } = await setup();
    expect(result.current.actions.getCurrentChat()).toBeNull();
    expect(result.current.actions.getCurrentMessages()).toEqual([]);
    let id = '';
    act(() => {
      id = result.current.actions.createChat();
    });
    act(() => {
      result.current.actions.addMessage(id, { role: 'user', content: 'x', timestamp: 't' });
    });
    expect(result.current.actions.getCurrentChat()?.id).toBe(id);
    expect(result.current.actions.getCurrentMessages()).toHaveLength(1);
    expect(result.current.actions.getChatById(id)?.id).toBe(id);
    expect(result.current.actions.getChatById('zz')).toBeNull();
  });
});
