import { LAST_SELECTED_MODEL_PATH_STORAGE_KEY } from '@/utils/modelThinking';
import { MODEL_PATH_CHANGED_EVENT } from '@/utils/contextTokens';
import {
  applyAgentMcpToChat,
  applyStoredAgentMcpToChat,
  getMcpToolIdsFromAgentConfig,
  persistAgentMcpConfig,
} from '@/utils/applyAgentMcp';
import {
  loadAgentModelOnly,
  modelPathsLikelyEqual,
  rememberAgentModelPath,
} from '@/utils/applyAgentServer';
import { saveAgentRagSystemPromptFromInstructions, saveProjectRagSystemPromptFromInstructions } from '@/utils/applyAgentRagSettings';
import { saveEntityRagSettings, type EntityRagDraft } from '@/utils/entityRagSettings';
import { getMcpToolIdsForChat } from '@/mcp/selectionStorage';

jest.mock('@/config/api', () => ({
  authFetch: (...a: unknown[]) => (require('@tests/unit/helpers/authFetchMock').authFetchMock as (...x: unknown[]) => unknown)(...a),
  getApiUrl: (endpoint: string) => `http://api.test${endpoint}`,
  getAuthFetchHeaders: (extra?: Record<string, string>) => {
    const token = (globalThis as any).localStorage.getItem('auth_token');
    return { ...(extra || {}), ...(token ? { Authorization: `Bearer ${token}` } : {}) };
  },
}));

type FetchMock = jest.Mock<Promise<Partial<Response>>, [string, RequestInit?]>;

function mockFetch(...responses: Array<Partial<Response> | Error>): FetchMock {
  const fn: FetchMock = jest.fn();
  responses.forEach((r) =>
    r instanceof Error ? fn.mockRejectedValueOnce(r) : fn.mockResolvedValueOnce(r),
  );
  (global as unknown as { fetch: FetchMock }).fetch = fn;
  return fn;
}

const json = (body: unknown, ok = true, status = 200): Partial<Response> => ({
  ok,
  status,
  json: async () => body,
  text: async () => JSON.stringify(body),
});

beforeEach(() => {
  localStorage.clear();
  jest.spyOn(console, 'debug').mockImplementation(() => undefined);
});

afterEach(() => jest.restoreAllMocks());

describe('agent MCP config', () => {
  it('turns server ids from the card into tool ids', () => {
    expect(getMcpToolIdsFromAgentConfig({ mcp_enabled: true, mcp_server_ids: ['docs', ' wiki ', '', 5] })).toEqual([
      'server:mcp:docs',
      'server:mcp:wiki',
      'server:mcp:5',
    ]);
  });

  it.each([null, undefined, {}, { mcp_enabled: false, mcp_server_ids: ['docs'] }, { mcp_enabled: true }, { mcp_enabled: true, mcp_server_ids: 'docs' }])(
    'gives no tools for %p',
    (config) => {
      expect(getMcpToolIdsFromAgentConfig(config as never)).toEqual([]);
    },
  );

  it('applies the card selection to a chat only when MCP is enabled on the card', () => {
    applyAgentMcpToChat('c1', { mcp_enabled: false, mcp_server_ids: ['docs'] });
    applyAgentMcpToChat(null, { mcp_enabled: true, mcp_server_ids: ['docs'] });
    expect(getMcpToolIdsForChat('c1')).toEqual([]);

    applyAgentMcpToChat('c1', { mcp_enabled: true, mcp_server_ids: ['docs'] });
    expect(getMcpToolIdsForChat('c1')).toEqual(['server:mcp:docs']);
  });

  it('replaces the previous selection instead of merging with it', () => {
    applyAgentMcpToChat('c1', { mcp_enabled: true, mcp_server_ids: ['docs'] });
    applyAgentMcpToChat('c1', { mcp_enabled: true, mcp_server_ids: [] });

    expect(getMcpToolIdsForChat('c1')).toEqual([]);
  });

  it('remembers the active agent MCP config and restores it into a new chat', () => {
    persistAgentMcpConfig({ mcp_enabled: true, mcp_server_ids: [' docs ', '', 'wiki'] });

    expect(localStorage.getItem('active_agent_mcp_enabled')).toBe('true');
    expect(JSON.parse(localStorage.getItem('active_agent_mcp_server_ids')!)).toEqual(['docs', 'wiki']);

    applyStoredAgentMcpToChat('c2');
    expect(getMcpToolIdsForChat('c2')).toEqual(['server:mcp:docs', 'server:mcp:wiki']);
  });

  it('forgets the config when the agent has no MCP', () => {
    persistAgentMcpConfig({ mcp_enabled: true, mcp_server_ids: ['docs'] });

    persistAgentMcpConfig({ mcp_enabled: false });

    expect(localStorage.getItem('active_agent_mcp_enabled')).toBeNull();
    expect(localStorage.getItem('active_agent_mcp_server_ids')).toBeNull();
    applyStoredAgentMcpToChat('c1');
    expect(getMcpToolIdsForChat('c1')).toEqual([]);
  });

  it('survives a damaged stored list', () => {
    localStorage.setItem('active_agent_mcp_enabled', 'true');
    localStorage.setItem('active_agent_mcp_server_ids', '{broken');

    expect(() => applyStoredAgentMcpToChat('c1')).not.toThrow();
    expect(getMcpToolIdsForChat('c1')).toEqual([]);
  });
});

describe('model path comparison', () => {
  it.each([
    ['llm-svc://qwen3', 'qwen3', true],
    ['gw/qwen3', 'qwen3', true],
    ['gw/qwen3', 'other/qwen3', true],
    ['qwen3-32b', 'qwen3', true],
    ['LLM-SVC://Qwen3', 'qwen3', true],
    ['qwen3', 'llama3', false],
    ['qwen3', 'qwen', false],
    ['', 'qwen3', false],
    [null, null, false],
    [undefined, 'x', false],
  ])('%p ≈ %p → %p', (a, b, expected) => {
    expect(modelPathsLikelyEqual(a, b)).toBe(expected);
  });

  it('repairs the "1lm-svc" typo and strips whitespace', () => {
    expect(modelPathsLikelyEqual('1lm-svc://qwen 3', 'qwen3')).toBe(true);
  });

  it('treats different models with a common prefix as equal (known issue)', () => {
    // «qwen3» и «qwen3-coder» считаются одной моделью из-за сравнения по префиксу с дефисом.
    expect(modelPathsLikelyEqual('qwen3', 'qwen3-coder')).toBe(true);
  });
});

describe('remembering the agent model', () => {
  it('stores a valid path as llm-svc and announces it', () => {
    const events: CustomEvent[] = [];
    const handler = (e: Event) => events.push(e as CustomEvent);
    window.addEventListener(MODEL_PATH_CHANGED_EVENT, handler);

    rememberAgentModelPath('qwen3');
    window.removeEventListener(MODEL_PATH_CHANGED_EVENT, handler);

    expect(localStorage.getItem(LAST_SELECTED_MODEL_PATH_STORAGE_KEY)).toBe('llm-svc://qwen3');
    expect(events[0].detail).toEqual({ path: 'llm-svc://qwen3' });
  });

  it('keeps provider paths and gguf files untouched', () => {
    rememberAgentModelPath('gw/qwen3');
    expect(localStorage.getItem(LAST_SELECTED_MODEL_PATH_STORAGE_KEY)).toBe('gw/qwen3');

    rememberAgentModelPath('model.gguf');
    expect(localStorage.getItem(LAST_SELECTED_MODEL_PATH_STORAGE_KEY)).toBe('model.gguf');
  });

  it.each([null, undefined, '', '   '])('ignores %p', (value) => {
    rememberAgentModelPath(value);

    expect(localStorage.getItem(LAST_SELECTED_MODEL_PATH_STORAGE_KEY)).toBeNull();
  });

  it.each([
    ['llm-svc', 'llm-svc://llm-svc'],
    ['default', 'llm-svc://default'],
  ])('stores the placeholder %p as a real model path (known issue)', (value, stored) => {
    // normalizeForLoad оборачивает заглушку в llm-svc://… ДО проверки isValidSelectedModelPath,
    // поэтому защита от заглушек не срабатывает и в localStorage уходит несуществующая модель.
    rememberAgentModelPath(value);

    expect(localStorage.getItem(LAST_SELECTED_MODEL_PATH_STORAGE_KEY)).toBe(stored);
  });
});

describe('loadAgentModelOnly', () => {
  it('asks the backend to load the model and reports success', async () => {
    const fetchMock = mockFetch(json({ success: true }));

    const result = await loadAgentModelOnly('tok', 'qwen3');

    expect(result).toEqual({ ok: true });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('http://api.test/api/models/load');
    expect(init).toMatchObject({ method: 'POST' });
    expect(JSON.parse(String(init!.body))).toEqual({ model_path: 'llm-svc://qwen3' });
    expect((init!.headers as Record<string, string>).Authorization).toBe('Bearer tok');
    expect(localStorage.getItem(LAST_SELECTED_MODEL_PATH_STORAGE_KEY)).toBe('llm-svc://qwen3');
  });

  it('treats a refusal as "pending": the model will be loaded on the first message', async () => {
    mockFetch(json({ success: false, message: 'llm-svc ещё запускается' }, false, 503));

    expect(await loadAgentModelOnly('tok', 'qwen3')).toEqual({ ok: true, pending: true, message: 'llm-svc ещё запускается' });
  });

  it('falls back to the detail and then to a default message', async () => {
    mockFetch(json({ detail: 'нет места' }, false, 500), json({}, false, 500));

    expect((await loadAgentModelOnly('tok', 'a')) as { message: string }).toMatchObject({ pending: true, message: 'нет места' });
    expect((await loadAgentModelOnly('tok', 'a')) as { message: string }).toMatchObject({ message: 'Не удалось загрузить модель агента' });
  });

  it('turns network failures into a pending result as well', async () => {
    mockFetch(new Error('Failed to fetch'));

    expect(await loadAgentModelOnly('tok', 'qwen3')).toEqual({ ok: true, pending: true, message: 'Failed to fetch' });
  });

  it('uses the last selected model for an agent without its own', async () => {
    localStorage.setItem(LAST_SELECTED_MODEL_PATH_STORAGE_KEY, 'gw/llama3');
    const fetchMock = mockFetch(json({ success: true }));

    await loadAgentModelOnly('tok', null);

    expect(JSON.parse(String(fetchMock.mock.calls[0][1]!.body))).toEqual({ model_path: 'gw/llama3' });
  });

  it('asks the backend for the current model when nothing was selected', async () => {
    const fetchMock = mockFetch(json({ path: 'gw/current' }), json({ success: true }));

    await loadAgentModelOnly('tok', undefined);

    expect(fetchMock.mock.calls[0][0]).toBe('http://api.test/api/models/current');
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]!.body))).toEqual({ model_path: 'gw/current' });
  });

  it('does nothing when no model can be determined', async () => {
    const fetchMock = mockFetch(json({ path: 'llm-svc' }));

    expect(await loadAgentModelOnly('tok', '')).toEqual({ ok: true });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('survives a failing current-model lookup', async () => {
    mockFetch(new Error('offline'));

    expect(await loadAgentModelOnly('tok', null)).toEqual({ ok: true });
  });
});

describe('RAG instruction saving', () => {
  it('sends the agent instruction as rag_system_prompt', async () => {
    const fetchMock = mockFetch({ ok: true, status: 200 });
    localStorage.setItem('auth_token', 'jwt');

    const result = await saveAgentRagSystemPromptFromInstructions({ agentId: 7, agentName: 'Юрист', instructions: '  Отвечай по документам  ' });

    expect(result).toEqual({ ok: true });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('http://api.test/api/rag/settings');
    expect(init!.method).toBe('PUT');
    expect(JSON.parse(String(init!.body))).toEqual({ scope: 'agent', entity_name: 'Юрист', rag_system_prompt: 'Отвечай по документам', agent_id: 7 });
    expect(init!.headers).toMatchObject({ 'Content-Type': 'application/json', Authorization: 'Bearer jwt' });
  });

  it('uses project_id for projects', async () => {
    const fetchMock = mockFetch({ ok: true, status: 200 });

    await saveProjectRagSystemPromptFromInstructions({ projectId: 'p1', projectName: 'Проект', instructions: 'x' });

    expect(JSON.parse(String(fetchMock.mock.calls[0][1]!.body))).toEqual({ scope: 'project', entity_name: 'Проект', rag_system_prompt: 'x', project_id: 'p1' });
  });

  it('reports the server text or the status code on failure', async () => {
    mockFetch({ ok: false, status: 500, text: async () => 'ошибка БД' }, { ok: false, status: 502, text: async () => '' }, { ok: false, status: 500, text: async () => { throw new Error('x'); } });

    const opts = { agentId: 1, agentName: 'A', instructions: 'i' };
    expect(await saveAgentRagSystemPromptFromInstructions(opts)).toEqual({ ok: false, message: 'ошибка БД' });
    expect(await saveAgentRagSystemPromptFromInstructions(opts)).toEqual({ ok: false, message: 'Ошибка сохранения RAG-инструкции: 502' });
    expect(await saveAgentRagSystemPromptFromInstructions(opts)).toEqual({ ok: false, message: 'Ошибка сохранения RAG-инструкции: 500' });
  });
});

describe('saveEntityRagSettings', () => {
  const draft: EntityRagDraft = {
    strategy: 'hybrid',
    agentic_rag_enabled: true,
    rag_query_fix_typos: false,
    rag_multi_query_enabled: true,
    rag_hyde_enabled: false,
    rag_chat_top_k: 8,
    rag_chunking_strategy: 'recursive',
    rag_chunk_size: 800,
    rag_chunk_overlap: 100,
    rag_similarity_threshold: 0.3,
    rag_reranking_enabled: true,
    rag_rerank_top_n: 20,
    rag_embedding_model_path: null,
    rag_reranker_model_path: 'gw/rr',
  };

  it('sends the whole draft and reports a re-chunking started by the backend', async () => {
    const fetchMock = mockFetch(json({ reindexed: true }));

    const result = await saveEntityRagSettings({ scope: 'agent', entityId: '7', entityName: 'Юрист', instructions: '  инструкция ', draft });

    expect(result).toEqual({ ok: true, reindexed: true });
    const body = JSON.parse(String(fetchMock.mock.calls[0][1]!.body));
    expect(body).toMatchObject({ scope: 'agent', agent_id: 7, entity_name: 'Юрист', rag_system_prompt: 'инструкция', strategy: 'hybrid', rag_chunk_size: 800, rag_reranker_model_path: 'gw/rr' });
    expect(body).not.toHaveProperty('project_id');
  });

  it('does not send models the user did not touch (null) so the choice is not reset', async () => {
    const fetchMock = mockFetch(json({}));

    await saveEntityRagSettings({ scope: 'project', entityId: 12, entityName: 'П', instructions: '', draft });

    const body = JSON.parse(String(fetchMock.mock.calls[0][1]!.body));
    expect(body).not.toHaveProperty('rag_embedding_model_path');
    expect(body.rag_reranker_model_path).toBe('gw/rr');
    expect(body.project_id).toBe('12');
    expect(body).not.toHaveProperty('agent_id');
  });

  it('sends an empty string for a model that was cleared explicitly', async () => {
    const fetchMock = mockFetch(json({}));

    await saveEntityRagSettings({ scope: 'agent', entityId: 1, entityName: 'A', instructions: '', draft: { ...draft, rag_embedding_model_path: '' } });

    expect(JSON.parse(String(fetchMock.mock.calls[0][1]!.body)).rag_embedding_model_path).toBe('');
  });

  it('saves only the instruction without a draft', async () => {
    const fetchMock = mockFetch(json({}));

    await saveEntityRagSettings({ scope: 'agent', entityId: 1, entityName: 'A', instructions: 'i' });

    expect(Object.keys(JSON.parse(String(fetchMock.mock.calls[0][1]!.body))).sort()).toEqual(['agent_id', 'entity_name', 'rag_system_prompt', 'scope']);
  });

  it('does not report a re-chunking when the answer has no JSON', async () => {
    mockFetch({ ok: true, status: 200, json: async () => { throw new Error('no body'); } });

    expect(await saveEntityRagSettings({ scope: 'agent', entityId: 1, entityName: 'A', instructions: '' })).toEqual({ ok: true, reindexed: false });
  });

  it('explains a permission failure and passes other errors through', async () => {
    mockFetch({ ok: false, status: 403, text: async () => 'Forbidden' }, { ok: false, status: 500, text: async () => 'сломалось' }, { ok: false, status: 500, text: async () => '' });
    const opts = { scope: 'agent' as const, entityId: 1, entityName: 'A', instructions: '' };

    expect(await saveEntityRagSettings(opts)).toEqual({ ok: false, message: 'Недостаточно прав: настройки может менять владелец или редактор' });
    expect(await saveEntityRagSettings(opts)).toEqual({ ok: false, message: 'сломалось' });
    expect(await saveEntityRagSettings(opts)).toEqual({ ok: false, message: 'Ошибка сохранения настроек РАГ: 500' });
  });

  it('turns an agent id into a number', async () => {
    const fetchMock = mockFetch(json({}));

    await saveEntityRagSettings({ scope: 'agent', entityId: 'abc', entityName: 'A', instructions: '' });

    expect(JSON.parse(String(fetchMock.mock.calls[0][1]!.body)).agent_id).toBeNull(); // NaN → null в JSON (known issue)
  });
});
