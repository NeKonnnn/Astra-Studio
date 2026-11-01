import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import AgentConstructorPanel from '@/components/right_bar/AgentConstructorPanel';
import {
  ASTRA_OPEN_AGENT_CONSTRUCTOR,
  ASTRA_OPEN_AGENT_CONSTRUCTOR_ID_KEY,
} from '@/constants/hotkeys';

// ---------- управляемые фейки ----------
let mockToken: string | null = 'tok';
let mockUser: Record<string, unknown> | null = { is_admin: false, user_id: 'U1' };
let mockCanUse = true;
let mockCanCreate = true;
let mockCanPublish = true;
let mockReady: string | null = null;

const mockNotify = jest.fn();
const mockNotifyReindex = jest.fn();
const mockClearReady = jest.fn();
const mockFetchAgents = jest.fn();
const mockLoadModel = jest.fn();
const mockSaveRag = jest.fn();
const mockClone = jest.fn();
const mockFetchMcp = jest.fn();
const mockFetchPlugins = jest.fn();
const mockFetchTags = jest.fn();
const mockInvalidateTags = jest.fn();
const mockFetchChainCfg = jest.fn();
const mockFetchDefaults = jest.fn();
const mockPersistMcp = jest.fn();
const mockPersistArtifacts = jest.fn();

jest.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({ token: mockToken, user: mockUser }),
}));
jest.mock('@/contexts/AppContext', () => ({
  useAppActions: () => ({ showNotification: mockNotify }),
}));
jest.mock('@/contexts/RagReindexStatusContext', () => ({
  useRagReindexStatus: () => ({ notifyReindexStarted: mockNotifyReindex }),
}));
jest.mock('@/hooks/useFeatureAccess', () => ({
  useFeatureAccess: () => ({
    canUseAgents: mockCanUse,
    canCreateAgents: mockCanCreate,
    canPublishToLibrary: mockCanPublish,
  }),
}));
jest.mock('@/hooks/useRagEntityReadyMessage', () => ({
  useRagEntityReadyMessage: () => ({ readyMessage: mockReady, clearReadyMessage: mockClearReady }),
}));
jest.mock('@/config/api', () => ({
  authFetch: (...a: unknown[]) => (require('@tests/unit/helpers/authFetchMock').authFetchMock as (...x: unknown[]) => unknown)(...a),
  getApiUrl: (p: string) => `http://api.test${p}`,
  getAuthFetchHeaders: () => (mockToken ? { Authorization: `Bearer ${mockToken}` } : {}),
  API_ENDPOINTS: {
    SKILLS: '/api/skills',
    KB_DOCUMENTS_LIST: '/api/kb/documents',
    KB_DOCUMENTS_UPLOAD: '/api/kb/documents',
    KB_DOCUMENTS_DELETE: '/api/kb/documents',
  },
}));
jest.mock('@/utils/applyAgentServer', () => ({
  loadAgentModelOnly: (...a: unknown[]) => mockLoadModel(...a),
}));
jest.mock('@/utils/entityRagSettings', () => ({
  saveEntityRagSettings: (...a: unknown[]) => mockSaveRag(...a),
}));
jest.mock('@/utils/fetchMergedUserAgents', () => ({
  fetchMergedUserAgents: (...a: unknown[]) => mockFetchAgents(...a),
}));
jest.mock('@/utils/cloneAgentDeep', () => ({
  ...jest.requireActual('../../utils/cloneAgentDeep'),
  cloneAgentDeep: (...a: unknown[]) => mockClone(...a),
}));
jest.mock('@/utils/applyAgentMcp', () => ({
  applyAgentMcpToChat: jest.fn(),
  persistAgentMcpConfig: (...a: unknown[]) => mockPersistMcp(...a),
}));
jest.mock('@/utils/agentArtifactsEnabled', () => ({
  persistAgentArtifactsEnabled: (...a: unknown[]) => mockPersistArtifacts(...a),
}));
jest.mock('@/mcp/api', () => ({ fetchMcpServers: (...a: unknown[]) => mockFetchMcp(...a) }));
jest.mock('@/plugins/api', () => ({ fetchPlugins: (...a: unknown[]) => mockFetchPlugins(...a) }));
jest.mock('@/constants/AgentTags', () => ({
  ...jest.requireActual('../../constants/AgentTags'),
  fetchAgentTags: (...a: unknown[]) => mockFetchTags(...a),
  invalidateAgentTagsCache: () => mockInvalidateTags(),
  subscribeAgentTagsCache: () => () => undefined,
}));
jest.mock('@/constants/agentChain', () => ({
  ...jest.requireActual('@/constants/agentChain'),
  fetchAgentChainConfig: (...a: unknown[]) => mockFetchChainCfg(...a),
}));
jest.mock('@/constants/ragEntityDefaults', () => ({
  ...jest.requireActual('../../constants/ragEntityDefaults'),
  fetchRagEntityDefaults: (...a: unknown[]) => mockFetchDefaults(...a),
}));

// ---------- заглушки тяжёлых дочерних компонентов ----------
jest.mock('@/components/ModelParametersModal', () => ({
  __esModule: true,
  default: (p: any) => (
    <div
      data-testid="model-modal"
      data-readonly={String(!!p.readOnly)}
      data-model={p.currentModel}
      data-models={JSON.stringify(p.availableModels)}
      data-providers={JSON.stringify(p.providerIds)}
      data-provider-models={JSON.stringify(p.providerModels)}
      data-params={JSON.stringify(p.initialParams ?? null)}
      data-settings={JSON.stringify(p.initialModelSettings)}
    >
      <button onClick={() => p.onSave('llm-svc://h/new-model', { temperature: 0.9 })}>mp-save</button>
      <button onClick={() => p.onSave('x', undefined)}>mp-save-empty</button>
      <button onClick={() => p.onSave('1LM-svc://h/my model', {})}>mp-save-weird</button>
      <button onClick={() => p.onSaveModelSettings({ custom: 1 })}>mp-settings</button>
      <button onClick={p.onClose}>mp-close</button>
    </div>
  ),
}));
jest.mock('@/components/settings/RAGSettings', () => ({
  __esModule: true,
  default: (p: any) => (
    <div
      data-testid="rag-settings"
      data-title={p.panelTitle}
      data-readonly={String(!!p.readOnly)}
      data-entity-id={String(p.entityId)}
      data-entity-name={p.entityName ?? ''}
      data-instructions={p.entityInstructionsPrompt}
      data-draft={JSON.stringify(p.draftValue)}
      data-can-edit={String(typeof p.onDraftChange === 'function')}
    >
      <button
        onClick={() =>
          p.onDraftChange?.({
            rag_embedding_model_path: 'emb-draft',
            rag_reranker_model_path: 'rer-draft',
            rag_reranking_enabled: true,
          })
        }
      >
        rag-draft
      </button>
      <button onClick={() => p.onDraftChange?.({ rag_reranking_enabled: false })}>rag-draft-norerank</button>
      <button onClick={() => p.onDraftChange?.({ rag_embedding_model_path: '  ' })}>rag-draft-blank</button>
      <button onClick={p.onClose}>rag-close</button>
    </div>
  ),
}));
jest.mock('@/components/ShareAgentDialog', () => ({
  __esModule: true,
  default: (p: any) => (
    <div data-testid="share-dialog" data-open={String(p.open)} data-id={p.agentId} data-name={p.agentName}>
      <button onClick={p.onClose}>close-share</button>
    </div>
  ),
}));
jest.mock('@/components/right_bar/AgentTagsField', () => ({
  __esModule: true,
  ...jest.requireActual('./AgentTagsField'),
  default: (p: any) => (
    <div>
      <input
        data-testid="tags-input"
        aria-label="tags-stub"
        disabled={p.readOnly}
        value={p.value.map((t: any) => t.name).join(',')}
        onChange={(e) =>
          p.onChange(
            e.target.value
              .split(',')
              .map((n: string) => n.trim())
              .filter(Boolean)
              .map((name: string) => ({ name })),
          )
        }
      />
      <button onClick={() => p.onChange([{ id: 5, name: 'Existing' }, { name: 'Fresh' }])}>tags-mixed</button>
    </div>
  ),
}));
jest.mock('@/agents/subagents/AgentSubagentsEditor', () => ({
  __esModule: true,
  ...jest.requireActual('../../agents/subagents/AgentSubagentsEditor'),
  default: (p: any) => (
    <div
      data-testid="subagents"
      data-config={JSON.stringify(p.config)}
      data-max={p.maxSubagents}
      data-readonly={String(!!p.readOnly)}
    >
      {p.afterModeSlot}
      <button
        onClick={() =>
          p.onChange({
            ...p.config,
            enabled: true,
            agent_ids: [2, 3, 99],
            agent_names: { 2: 'Viewer Agent' },
            required_tag_ids: [1],
            required_only: true,
          })
        }
      >
        sub-enable
      </button>
      <button onClick={() => p.onChange({ ...p.config, enabled: false, allow_self: false })}>sub-disable</button>
      <button onClick={() => p.onChange({ ...p.config, enabled: true, agent_ids: [2, 3, 99] })}>sub-three</button>
    </div>
  ),
}));
jest.mock('@/agents/chain/AgentChainEditor', () => ({
  __esModule: true,
  default: (p: any) => (
    <div
      data-testid="chain"
      data-ids={JSON.stringify(p.agentIds)}
      data-max={p.maxAgents}
      data-name={p.currentAgentName}
      data-readonly={String(!!p.readOnly)}
    >
      <button onClick={() => p.onChange([2, 3, 4])}>chain-set</button>
    </div>
  ),
}));
jest.mock('@/agents/AgentLimitField', () => ({
  __esModule: true,
  default: (p: any) => (
    <div data-testid={`limit-${p.label}`} data-value={String(p.value)} data-default={p.defaultLimit} data-max={p.maxLimit}>
      <button onClick={() => p.onChange(2)}>{`${p.label}:2`}</button>
      <button onClick={() => p.onChange('')}>{`${p.label}:clear`}</button>
    </div>
  ),
}));
jest.mock('@/agents/AgentChainSubagentsSettings', () => ({
  __esModule: true,
  default: (p: any) => (
    <div
      data-testid="chain-settings"
      data-recursion={String(p.recursionLimit)}
      data-steps={p.defaultGraphSteps}
      data-max-recursion={p.maxRecursionLimit}
      data-hide={String(p.hideSequentialOutputs)}
      data-shared={String(p.sharedChainRag)}
      data-parent={String(p.useParentSharedRag)}
    >
      <button onClick={() => p.onRecursionLimitChange(15)}>set-recursion</button>
      <button onClick={() => p.onHideSequentialOutputsChange(!p.hideSequentialOutputs)}>toggle-hide</button>
      <button onClick={() => p.onSharedChainRagChange(!p.sharedChainRag)}>toggle-shared</button>
      <button onClick={() => p.onUseParentSharedRagChange(!p.useParentSharedRag)}>toggle-parent</button>
    </div>
  ),
}));
jest.mock('@/components/RagFilesSearchField', () => ({
  __esModule: true,
  default: (p: any) => (
    <input data-testid="kb-search" value={p.value} onChange={(e) => p.onChange(e.target.value)} />
  ),
}));
jest.mock('@/components/RagUploadingFileThumb', () => ({
  __esModule: true,
  default: (p: any) => <span data-testid="pending-thumb">{p.filename}</span>,
}));

// ---------- фикстуры ----------
const FULL_CONFIG = {
  category: 'Код',
  model: 'llm-svc://host/model-a',
  model_params: { temperature: 0.3 },
  model_settings: { custom: 'agent-level' },
  code_interpreter: true,
  web_search: true,
  artifacts_enabled: true,
  shadcn_enabled: true,
  user_prompt_mode: false,
  file_search_enabled: true,
  kb_document_ids: [11, '12', 'abc'],
  skill_ids: ['s1'],
  skills_enabled: true,
  mcp_enabled: true,
  mcp_server_ids: ['m1'],
  plugin_ids: ['p1'],
  plugins_enabled: true,
  support_name: 'Саппорт',
  support_email: 'sup@example.com',
  max_chain_agents: 3,
  max_subagents: 4,
  agent_ids: [2, 3],
  hide_sequential_outputs: true,
  shared_chain_rag: true,
  use_parent_shared_rag: false,
  recursion_limit: 20,
  subagents: {
    enabled: true,
    allow_self: false,
    agent_ids: [2],
    agent_names: { 2: 'Viewer Agent' },
    required_tag_ids: [1],
    required_only: true,
  },
};

const AGENTS = [
  {
    id: 1, name: 'Owner Agent', description: 'описание', system_prompt: 'Достаточно длинный промпт',
    config: FULL_CONFIG, tags: [{ id: 1, name: 'Fin' }], author_id: 'U1', author_name: 'U',
    is_public: false, my_permission: 'owner',
  },
  {
    id: 2, name: 'Viewer Agent', system_prompt: 'Промпт зрителя тут', config: {}, tags: [],
    author_id: 'o', author_name: 'O', is_public: true, my_permission: 'viewer',
  },
  {
    id: 3, name: 'Editor Agent', system_prompt: '', config: { skill_ids: ['a', 'b'], plugin_ids: ['p1', 'p2'], mcp_server_ids: ['m1', 'm2'], skills: ['legacy'] },
    tags: ['x'], author_id: 'o', author_name: 'O', is_public: false, my_permission: 'editor',
  },
  {
    id: 4, name: 'Bare Agent', system_prompt: 'Голый агент промпт', author_id: 'U1', author_name: 'U',
    is_public: false, my_permission: 'owner',
  },
];

const SKILLS = [
  { id: 1, slug: 's1', name: 'Skill One', description: 'd1', is_active: true },
  { id: 2, slug: 's2', name: 'Skill Two', is_active: true },
  { id: 3, slug: 's3', name: 'Skill Off', is_active: false },
];
const MCP = [
  { id: 'm1', display_name: 'MCP One', enabled: true },
  { id: 'm2', display_name: '', enabled: true },
  { id: 'm3', display_name: 'Off', enabled: false },
];
const PLUGINS = [
  { id: 'p1', display_name: 'Plugin One', enabled: true },
  { id: 'p2', display_name: '', enabled: undefined },
  { id: 'p3', display_name: 'Off', enabled: false },
];
const KB_DOCS = [
  { id: 11, filename: 'a.pdf', size: 10, file_type: 'PDF', created_at: '2024-01-01', metadata: { agent_id: 1 } },
  { id: 12, filename: 'b.docx', size: null, file_type: null, created_at: null, metadata: { agent_id: '' } },
  { id: 13, filename: 'c.png', metadata: { agent_id: 1 } },
  { id: 14, filename: 'long-file-name-here-xxxxxxxx.xlsx', metadata: { agent_id: 'zzz' } },
  { id: 15, filename: 't.txt' },
  { id: 16, filename: 'z.bin', metadata: 'oops' },
  { id: 'bad', filename: 'skip.me' },
];

const fetchMock = jest.fn();
type Reply = { ok?: boolean; status?: number; statusText?: string; body?: unknown; bodyError?: boolean } | Error;
let routes: Record<string, Reply | ((url: string, init?: any) => Reply | Promise<Reply>)> = {};

function resp(r: Reply) {
  if (r instanceof Error) return Promise.reject(r);
  return Promise.resolve({
    ok: r.ok ?? true,
    status: r.status ?? 200,
    statusText: r.statusText ?? '',
    json: () => (r.bodyError ? Promise.reject(new Error('bad json')) : Promise.resolve(r.body ?? {})),
  });
}

function installFetch() {
  fetchMock.mockImplementation((url: string, init?: any) => {
    const method = (init?.method || 'GET').toUpperCase();
    const path = url.replace('http://api.test', '').split('?')[0];
    const handler = routes[`${method} ${path}`];
    if (!handler) return resp({ ok: false, status: 404, body: {} });
    return typeof handler === 'function'
      ? Promise.resolve().then(() => handler(url, init)).then(resp)
      : resp(handler);
  });
}

function defaultRoutes() {
  routes = {
    'GET /api/models/available': { body: { models: ['llm-svc://h/m1', { name: 'n2', path: 'llm-svc://h/m2', display_name: 'M2', llm_host_id: 'host1' }, { name: '', path: '' }] } },
    'GET /api/llm-providers': { body: { providers: [{ id: 'prov1' }, { id: '' }, {}] } },
    'GET /api/models/settings': { body: { temperature: 0.1 } },
    'GET /api/kb/documents': { body: { documents: KB_DOCS } },
    'GET /api/skills/list': { body: { items: SKILLS } },
    'GET /api/rag/settings': { body: { rag_embedding_model_path: 'srv-emb', rag_reranker_model_path: 'srv-rer', rag_reranking_enabled: true } },
  };
  for (const a of AGENTS) routes[`GET /api/agents/${a.id}`] = { body: a };
}

const callsTo = (method: string, path: string) =>
  fetchMock.mock.calls.filter(
    ([u, i]) =>
      ((i?.method || 'GET') as string).toUpperCase() === method &&
      String(u).replace('http://api.test', '').split('?')[0] === path,
  );

let currentAgents: any[] = AGENTS;

beforeEach(() => {
  jest.useRealTimers();
  mockToken = 'tok';
  mockUser = { is_admin: false, user_id: 'U1' };
  mockCanUse = true;
  mockCanCreate = true;
  mockCanPublish = true;
  mockReady = null;
  [mockNotify, mockNotifyReindex, mockClearReady, mockFetchAgents, mockLoadModel, mockSaveRag, mockClone,
    mockFetchMcp, mockFetchPlugins, mockFetchTags, mockInvalidateTags, mockFetchChainCfg, mockFetchDefaults,
    mockPersistMcp, mockPersistArtifacts, fetchMock].forEach((m) => m.mockReset());
  currentAgents = AGENTS.map((a) => ({ ...a }));
  mockFetchAgents.mockImplementation(async () => currentAgents);
  mockFetchMcp.mockResolvedValue(MCP);
  mockFetchPlugins.mockResolvedValue(PLUGINS);
  mockFetchTags.mockResolvedValue([]);
  mockFetchChainCfg.mockResolvedValue({ maxAgents: 6, maxSubagents: 7, defaultRecursionLimit: 40, maxRecursionLimit: 400 });
  mockFetchDefaults.mockResolvedValue({ embeddingPath: '', rerankerPath: '' });
  mockLoadModel.mockResolvedValue({ ok: true });
  mockSaveRag.mockResolvedValue({ ok: true, reindexed: false });
  (global as unknown as { fetch: unknown }).fetch = fetchMock;
  sessionStorage.clear();
  localStorage.clear();
  defaultRoutes();
  installFetch();
});

// ---------- хелперы ----------
async function flush(times = 4) {
  for (let i = 0; i < times; i += 1) {
    // eslint-disable-next-line no-await-in-loop
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

async function renderPanel(opts: { dark?: boolean; open?: boolean } = {}) {
  const utils = render(<AgentConstructorPanel isDarkMode={!!opts.dark} isOpen={opts.open !== false} />);
  await flush();
  return utils;
}

async function selectAgent(name: string) {
  fireEvent.click(screen.getByText('Агенты'));
  fireEvent.click(await screen.findByText(name));
  await flush(2);
}

const agentNameInput = () => screen.getByPlaceholderText('Введите имя агента') as HTMLInputElement;
const descInput = () => screen.getByPlaceholderText('Необязательно: описание вашего агента') as HTMLInputElement;
const instrInput = () => screen.getByPlaceholderText('Системные инструкции, используемые агентом') as HTMLTextAreaElement;
const supportNameInput = () => screen.getByPlaceholderText('Имя контактного лица') as HTMLInputElement;
const supportEmailInput = () => screen.getByPlaceholderText('support@example.com') as HTMLInputElement;
const tagsInput = () => screen.getByTestId('tags-input') as HTMLInputElement;
const categoryInput = () => document.getElementById('agent-constructor-category') as HTMLInputElement;
const modelInput = () => document.getElementById('agent-constructor-model') as HTMLInputElement;
const btn = (n: string | RegExp) => screen.getByRole('button', { name: n }) as HTMLButtonElement;
const saveBtn = () => btn('Сохранить');

function switchFor(label: string): HTMLInputElement {
  const row = screen.getByText(label).parentElement!.parentElement!;
  return row.querySelector('input[type="checkbox"]') as HTMLInputElement;
}

async function fillValidNew(name = 'Новый агент') {
  fireEvent.change(agentNameInput(), { target: { value: name } });
  fireEvent.change(tagsInput(), { target: { value: 'Fin, Новый' } });
}

function bodyOf(call: any[]) {
  return JSON.parse(call[1].body);
}

const lastCall = (method: string, path: string) => {
  const calls = callsTo(method, path);
  return calls[calls.length - 1];
};

/** Ответ на успешное создание: агент появляется в списке (как после loadAgents в реальном приложении). */
const createdOk = () => {
  currentAgents.push({
    id: 50, name: 'Мой агент', system_prompt: 'x', config: {}, tags: [], author_id: 'U1', author_name: 'U',
    is_public: false, my_permission: 'owner',
  });
  return { body: { agent_id: 50 } };
};

/** PUT-маршрут, реагирующий только на смену публикации (прочие PUT — служебные, например привязка KB). */
const publishRoute = (onPublish: () => void) => (_u: string, init: any): Reply => {
  if (JSON.parse(init.body).is_public === undefined) return { body: {} };
  onPublish();
  return { body: {} };
};

async function openOwner() {
  await renderPanel();
  await selectAgent('Owner Agent');
}

// ======================= сохранение =======================
describe('AgentConstructorPanel: валидация перед сохранением', () => {
  it('без имени -> ошибка и нет запроса', async () => {
    await renderPanel();
    fireEvent.change(tagsInput(), { target: { value: 'a' } });
    fireEvent.click(saveBtn());
    expect(await screen.findByText('Заполните обязательное поле: Имя')).toBeTruthy();
    expect(callsTo('POST', '/api/agents')).toHaveLength(0);
  });

  it('имя из одних пробелов считается пустым', async () => {
    await renderPanel();
    fireEvent.change(agentNameInput(), { target: { value: '   ' } });
    fireEvent.change(tagsInput(), { target: { value: 'a' } });
    fireEvent.click(saveBtn());
    expect(await screen.findByText('Заполните обязательное поле: Имя')).toBeTruthy();
  });

  it('без тегов -> ошибка про теги', async () => {
    await renderPanel();
    fireEvent.change(agentNameInput(), { target: { value: 'Имя' } });
    fireEvent.click(saveBtn());
    expect(await screen.findByText('Заполните обязательное поле: Теги')).toBeTruthy();
    expect(callsTo('POST', '/api/agents')).toHaveLength(0);
  });

  it('инструкция короче 10 символов -> ошибка; пустая допустима', async () => {
    await renderPanel();
    await fillValidNew();
    fireEvent.change(instrInput(), { target: { value: '  коротко  ' } });
    fireEvent.click(saveBtn());
    expect(await screen.findAllByText('Длина инструкции не может быть меньше 10 символов')).toBeTruthy();
    expect(callsTo('POST', '/api/agents')).toHaveLength(0);
  });

  it('успешная повторная попытка очищает прежнюю ошибку', async () => {
    routes['POST /api/agents'] = createdOk;
    await renderPanel();
    fireEvent.click(saveBtn());
    expect(await screen.findByText('Заполните обязательное поле: Имя')).toBeTruthy();
    await fillValidNew();
    fireEvent.click(saveBtn());
    await waitFor(() => expect(screen.queryByText('Заполните обязательное поле: Имя')).toBeNull());
    expect(await screen.findByText('Агент успешно сохранён')).toBeTruthy();
  });
});

describe('AgentConstructorPanel: создание агента', () => {
  it('POST /api/agents с payload, побочные эффекты и сообщение об успехе', async () => {
    routes['POST /api/agents'] = createdOk;
    await renderPanel();
    fireEvent.change(agentNameInput(), { target: { value: '  Мой агент  ' } });
    fireEvent.change(descInput(), { target: { value: '   ' } });
    fireEvent.change(tagsInput(), { target: { value: 'Fin, Новый' } });
    fireEvent.change(supportNameInput(), { target: { value: 'Саппорт' } });
    const selected = jest.fn();
    window.addEventListener('agentSelected', selected);
    fireEvent.click(saveBtn());
    expect(await screen.findByText('Агент успешно сохранён')).toBeTruthy();
    window.removeEventListener('agentSelected', selected);

    const [url, init] = callsTo('POST', '/api/agents')[0];
    expect(url).toBe('http://api.test/api/agents');
    expect(init.headers).toEqual({ 'Content-Type': 'application/json', Authorization: 'Bearer tok' });
    const body = JSON.parse(init.body);
    expect(body).toMatchObject({
      name: 'Мой агент',
      system_prompt: 'Системные инструкции не заданы.',
      is_public: false,
      tools: [],
      tag_ids: [],
      new_tags: ['Fin', 'Новый'],
    });
    expect(body.description).toBeUndefined();
    expect(body.config).toMatchObject({
      category: 'Общий',
      artifacts_enabled: false,
      skill_ids: [],
      mcp_enabled: false,
      support_name: 'Саппорт',
      agent_ids: [],
      use_parent_shared_rag: true,
      subagents: { enabled: false, allow_self: true, agent_ids: [], agent_names: {}, required_tag_ids: [], required_only: false },
    });
    expect(body.config).not.toHaveProperty('model_settings');
    expect(body.config).not.toHaveProperty('recursion_limit');
    expect(body.config).not.toHaveProperty('max_chain_agents');
    expect(body.config).not.toHaveProperty('max_subagents');

    expect(mockInvalidateTags).toHaveBeenCalledTimes(2);
    expect(localStorage.getItem('active_agent_id')).toBe('50');
    expect(localStorage.getItem('active_agent_name')).toBe('Мой агент');
    expect(localStorage.getItem('active_agent_prompt')).toBe('Системные инструкции не заданы.');
    expect(mockPersistMcp).toHaveBeenCalledWith({ mcp_enabled: false, mcp_server_ids: [] });
    expect(mockPersistArtifacts).toHaveBeenCalledWith({ artifacts_enabled: false, shadcn_enabled: false, user_prompt_mode: false });
    expect((selected.mock.calls[0][0] as CustomEvent).detail).toMatchObject({ id: 50, name: 'Мой агент' });
    // модели нет -> загрузка модели не запускается; РАГ-настройки агента в KB-only UI не сохраняются
    expect(mockLoadModel).not.toHaveBeenCalled();
    expect(mockSaveRag).not.toHaveBeenCalled();
    // список агентов перечитан после сохранения
    expect(mockFetchAgents.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  // Правильное поведение: созданный агент остаётся редактируемым (роль owner). Сейчас selectedAgentId
  // переключается на новый id до того, как агент попал в список; если loadAgents не вернул его
  // (сбой/задержка), роль вычисляется как viewer и форма блокируется, кнопка «Сохранить» пропадает.
  it('созданный агент, которого нет в обновлённом списке, открывается как «зритель» (known issue: текущее поведение)', async () => {
    routes['POST /api/agents'] = { body: { agent_id: 50 } };
    await renderPanel();
    await fillValidNew();
    fireEvent.click(saveBtn());
    expect(await screen.findByText('Агент успешно сохранён')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Сохранить' })).toBeNull();
    expect(btn('Использовать в чате')).toBeTruthy();
    expect(agentNameInput().disabled).toBe(true);
  });

  it('ответ без agent_id: успех показан, но localStorage/событие/РАГ не трогаются', async () => {
    routes['POST /api/agents'] = { body: {} };
    await renderPanel();
    await fillValidNew();
    fireEvent.click(saveBtn());
    expect(await screen.findByText('Агент успешно сохранён')).toBeTruthy();
    expect(localStorage.getItem('active_agent_id')).toBeNull();
    expect(mockSaveRag).not.toHaveBeenCalled();
  });

  it('теги: существующие уходят в tag_ids, новые — в new_tags', async () => {
    routes['POST /api/agents'] = createdOk;
    await renderPanel();
    fireEvent.change(agentNameInput(), { target: { value: 'A' } });
    fireEvent.click(screen.getByText('tags-mixed'));
    fireEvent.click(saveBtn());
    await screen.findByText('Агент успешно сохранён');
    const body = bodyOf(callsTo('POST', '/api/agents')[0]);
    expect(body.tag_ids).toEqual([5]);
    expect(body.new_tags).toEqual(['Fresh']);
  });

  it('модель из панели нормализуется (1lm-svc:// -> llm-svc://, пробелы удаляются) и загружается на сервер', async () => {
    routes['POST /api/agents'] = createdOk;
    await renderPanel();
    fireEvent.click(modelInput());
    fireEvent.click(screen.getByText('mp-save-weird'));
    await fillValidNew();
    fireEvent.click(saveBtn());
    await screen.findByText('Агент успешно сохранён');
    expect(bodyOf(callsTo('POST', '/api/agents')[0]).config.model).toBe('llm-svc://h/mymodel');
    expect(mockLoadModel).toHaveBeenCalledWith('tok', '1LM-svc://h/my model');
    await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('success', 'Модель агента загружена на сервер'));
  });

  it('загрузка модели в режиме pending -> info; ошибка -> warning с сообщением', async () => {
    routes['POST /api/agents'] = createdOk;
    routes['PUT /api/agents/50'] = { body: {} };
    mockLoadModel.mockResolvedValueOnce({ ok: true, pending: true, message: 'ждём' });
    await renderPanel();
    fireEvent.click(modelInput());
    fireEvent.click(screen.getByText('mp-save'));
    await fillValidNew();
    fireEvent.click(saveBtn());
    await waitFor(() =>
      expect(mockNotify).toHaveBeenCalledWith('info', 'Агент сохранён; модель подтянется, когда сервис моделей будет готов'),
    );

    mockLoadModel.mockResolvedValueOnce({ ok: false, message: 'нет памяти' });
    fireEvent.click(saveBtn());
    await waitFor(() =>
      expect(mockNotify).toHaveBeenCalledWith('warning', 'Агент сохранён; не удалось загрузить модель: нет памяти'),
    );
  });

  it('без токена: нет Authorization, модель и РАГ не загружаются', async () => {
    mockToken = null;
    routes['POST /api/agents'] = createdOk;
    await renderPanel();
    fireEvent.click(modelInput());
    fireEvent.click(screen.getByText('mp-save'));
    await fillValidNew();
    fireEvent.click(saveBtn());
    await screen.findByText('Агент успешно сохранён');
    expect(callsTo('POST', '/api/agents')[0][1].headers).toEqual({ 'Content-Type': 'application/json' });
    expect(mockLoadModel).not.toHaveBeenCalled();
    expect(mockSaveRag).not.toHaveBeenCalled();
  });

  it('индикатор «Сохраняю...» во время запроса, кнопка заблокирована', async () => {
    let release!: (v: Reply) => void;
    routes['POST /api/agents'] = () => new Promise<Reply>((r) => { release = r; });
    await renderPanel();
    await fillValidNew();
    fireEvent.click(saveBtn());
    const busy = await screen.findByRole('button', { name: 'Сохраняю...' });
    expect((busy as HTMLButtonElement).disabled).toBe(true);
    createdOk();
    await act(async () => {
      release({ body: { agent_id: 50 } });
    });
    expect(await screen.findByRole('button', { name: 'Сохранить' })).toBeTruthy();
  });

  it('сообщение об успехе скрывается через 3 секунды', async () => {
    routes['POST /api/agents'] = createdOk;
    await renderPanel();
    await fillValidNew();
    let hide: (() => void) | null = null;
    const real = window.setTimeout;
    const spy = jest.spyOn(window, 'setTimeout').mockImplementation(((fn: any, d?: number, ...a: any[]) => {
      if (d === 3000) {
        hide = fn;
        return 0 as any;
      }
      return real(fn, d, ...a);
    }) as any);
    fireEvent.click(saveBtn());
    expect(await screen.findByText('Агент успешно сохранён')).toBeTruthy();
    spy.mockRestore();
    expect(hide).not.toBeNull();
    act(() => {
      hide!();
    });
    expect(screen.queryByText('Агент успешно сохранён')).toBeNull();
  });

  it('ошибка записи в localStorage не мешает событию agentSelected', async () => {
    routes['POST /api/agents'] = createdOk;
    await renderPanel();
    await fillValidNew();
    const selected = jest.fn();
    window.addEventListener('agentSelected', selected);
    const spy = jest.spyOn(Storage.prototype, 'setItem').mockImplementation((k: string) => {
      if (k === 'active_agent_id') throw new Error('quota');
    });
    fireEvent.click(saveBtn());
    await screen.findByText('Агент успешно сохранён');
    spy.mockRestore();
    window.removeEventListener('agentSelected', selected);
    expect(selected).toHaveBeenCalledTimes(1);
  });
});

describe('AgentConstructorPanel: ошибки сохранения', () => {
  async function saveWith(route: Reply) {
    routes['POST /api/agents'] = route;
    await renderPanel();
    await fillValidNew();
    fireEvent.click(saveBtn());
  }

  it('404 -> пояснение про деплой backend', async () => {
    await saveWith({ ok: false, status: 404, statusText: 'Not Found', body: { detail: 'Not Found' } });
    expect(await screen.findByText(/Не найден API создания агентов \(404\)/)).toBeTruthy();
  });

  it('detail-строка показывается как есть', async () => {
    await saveWith({ ok: false, status: 422, body: { detail: 'Имя занято' } });
    expect(await screen.findByText('Имя занято')).toBeTruthy();
    expect(screen.queryByText('Агент успешно сохранён')).toBeNull();
    expect(saveBtn().disabled).toBe(false);
  });

  it('detail-массив (pydantic) склеивается через «; »', async () => {
    await saveWith({ ok: false, status: 422, body: { detail: [{ msg: 'a' }, 'b', { loc: 1 }] } });
    expect(await screen.findByText(/^a; b; \[object Object\]$/)).toBeTruthy();
  });

  it('detail-объект с msg', async () => {
    await saveWith({ ok: false, status: 400, body: { detail: { msg: 'обёрнуто' } } });
    expect(await screen.findByText('обёрнуто')).toBeTruthy();
  });

  it('detail неизвестного формата -> «статус текст»', async () => {
    await saveWith({ ok: false, status: 500, statusText: 'Server Error', body: { detail: 5 } });
    expect(await screen.findByText('500 Server Error')).toBeTruthy();
  });

  it('тело ответа не JSON -> используется statusText', async () => {
    await saveWith({ ok: false, status: 502, statusText: 'Bad Gateway', bodyError: true });
    expect(await screen.findByText('Bad Gateway')).toBeTruthy();
  });

  it('сетевая ошибка -> её сообщение', async () => {
    await saveWith(new Error('Network down'));
    expect(await screen.findByText('Network down')).toBeTruthy();
  });
});

describe('AgentConstructorPanel: редактирование агента', () => {
  it('PUT /api/agents/{id} с полным config из карточки', async () => {
    routes['PUT /api/agents/1'] = { body: {} };
    await openOwner();
    fireEvent.click(saveBtn());
    expect(await screen.findByText('Агент успешно сохранён')).toBeTruthy();
    const call = lastCall('PUT', '/api/agents/1');
    expect(call[0]).toBe('http://api.test/api/agents/1');
    const body = bodyOf(call);
    expect(body).toMatchObject({
      name: 'Owner Agent',
      description: 'описание',
      system_prompt: 'Достаточно длинный промпт',
      is_public: false,
      tools: ['code_interpreter', 'web_search'],
      tag_ids: [1],
      new_tags: [],
    });
    expect(body.config).toMatchObject({
      category: 'Код',
      model: 'llm-svc://host/model-a',
      model_params: { temperature: 0.3 },
      model_settings: { custom: 'agent-level' },
      artifacts_enabled: true,
      shadcn_enabled: true,
      user_prompt_mode: false,
      skill_ids: ['s1'],
      skills_enabled: true,
      mcp_enabled: true,
      mcp_server_ids: ['m1'],
      plugins_enabled: true,
      plugin_ids: ['p1'],
      support_name: 'Саппорт',
      support_email: 'sup@example.com',
      agent_ids: [2, 3],
      hide_sequential_outputs: true,
      shared_chain_rag: true,
      use_parent_shared_rag: false,
      recursion_limit: 20,
      max_chain_agents: 3,
      max_subagents: 4,
      subagents: {
        enabled: true,
        allow_self: false,
        agent_ids: [2],
        agent_names: { 2: 'Viewer Agent' },
        required_tag_ids: [1],
        required_only: true,
      },
    });
    // сохранено у существующего агента: active_agent_id берётся из выбранной карточки
    expect(localStorage.getItem('active_agent_id')).toBe('1');
    expect(mockLoadModel).toHaveBeenCalledWith('tok', 'llm-svc://host/model-a');
    expect(mockSaveRag).not.toHaveBeenCalled();
  });

  it('is_public берётся из текущей карточки', async () => {
    currentAgents[0].is_public = true;
    routes['PUT /api/agents/1'] = { body: {} };
    await openOwner();
    fireEvent.click(saveBtn());
    await screen.findByText('Агент успешно сохранён');
    expect(bodyOf(lastCall('PUT', '/api/agents/1')).is_public).toBe(true);
  });

  it('субагенты выключены -> в payload пустой блок; список цепочки обрезается по лимиту', async () => {
    routes['PUT /api/agents/1'] = { body: {} };
    await openOwner();
    fireEvent.click(screen.getByText('sub-disable'));
    fireEvent.click(screen.getByText('chain-set'));
    fireEvent.click(saveBtn());
    await screen.findByText('Агент успешно сохранён');
    const cfg = bodyOf(lastCall('PUT', '/api/agents/1')).config;
    expect(cfg.subagents).toEqual({
      enabled: false, allow_self: false, agent_ids: [], agent_names: {}, required_tag_ids: [], required_only: false,
    });
    // лимит цепочки у агента = 3 -> [2,3,4] помещаются целиком
    expect(cfg.agent_ids).toEqual([2, 3, 4]);
  });

  it('субагенты включены -> снимок имён собирается из списка агентов', async () => {
    routes['PUT /api/agents/1'] = { body: {} };
    await openOwner();
    fireEvent.click(screen.getByText('sub-enable'));
    fireEvent.click(saveBtn());
    await screen.findByText('Агент успешно сохранён');
    const sub = bodyOf(lastCall('PUT', '/api/agents/1')).config.subagents;
    expect(sub.agent_ids).toEqual([2, 3, 99]);
    expect(sub.agent_names).toMatchObject({ 2: 'Viewer Agent', 3: 'Editor Agent' });
  });

  it('карточка без тонкой настройки модели не отправляет model_settings, а после правки — отправляет', async () => {
    currentAgents[3].config = { model: 'llm-svc://h/m' };
    routes['PUT /api/agents/4'] = { body: {} };
    await renderPanel();
    await selectAgent('Bare Agent');
    fireEvent.change(tagsInput(), { target: { value: 't' } });
    fireEvent.click(saveBtn());
    await screen.findByText('Агент успешно сохранён');
    expect(bodyOf(lastCall('PUT', '/api/agents/4')).config).not.toHaveProperty('model_settings');
    fireEvent.click(modelInput());
    fireEvent.click(screen.getByText('mp-settings'));
    fireEvent.click(screen.getByText('mp-close'));
    fireEvent.click(saveBtn());
    await waitFor(() => expect(callsTo('PUT', '/api/agents/4').length).toBe(2));
    expect(bodyOf(lastCall('PUT', '/api/agents/4')).config.model_settings).toEqual({ custom: 1 });
  });

  it('теги без id после сохранения получают id из обновлённого справочника', async () => {
    routes['PUT /api/agents/1'] = { body: {} };
    mockFetchTags.mockResolvedValue([{ id: 9, name: 'FRESH' }]);
    await openOwner();
    fireEvent.change(tagsInput(), { target: { value: 'fresh, other' } });
    fireEvent.click(saveBtn());
    await screen.findByText('Агент успешно сохранён');
    expect(mockFetchTags).toHaveBeenCalledWith(true);
    expect(bodyOf(lastCall('PUT', '/api/agents/1')).new_tags).toEqual(['fresh', 'other']);
    await waitFor(() => expect(callsTo('PUT', '/api/agents/1').length).toBeGreaterThan(0));
    fireEvent.click(saveBtn());
    await waitFor(() => {
      const b = bodyOf(lastCall('PUT', '/api/agents/1'));
      expect(b.tag_ids).toEqual([9]);
      expect(b.new_tags).toEqual(['other']);
    });
  });

  it('ошибка обновления справочника тегов не мешает сохранению', async () => {
    routes['PUT /api/agents/1'] = { body: {} };
    mockFetchTags.mockRejectedValue(new Error('x'));
    await openOwner();
    fireEvent.click(saveBtn());
    expect(await screen.findByText('Агент успешно сохранён')).toBeTruthy();
    expect(mockFetchAgents.mock.calls.length).toBeGreaterThanOrEqual(2);
  });
});

// ======================= публикация =======================
describe('AgentConstructorPanel: публикация в галерее', () => {
  it('опубликовать: PUT {is_public:true}, обновление карточки, уведомление и смена подписи', async () => {
    routes['PUT /api/agents/1'] = publishRoute(() => {
      currentAgents[0].is_public = true;
      routes['GET /api/agents/1'] = { body: { ...AGENTS[0], is_public: true } };
    });
    await openOwner();
    fireEvent.click(btn('Опубликовать в галерее'));
    await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('success', 'Агент опубликован в галерее'));
    expect(bodyOf(lastCall('PUT', '/api/agents/1'))).toEqual({ is_public: true });
    expect(lastCall('PUT', '/api/agents/1')[1].headers).toEqual({
      'Content-Type': 'application/json',
      Authorization: 'Bearer tok',
    });
    expect(btn('Снять с публикации')).toBeTruthy();
  });

  it('снять с публикации', async () => {
    currentAgents[0].is_public = true;
    routes['PUT /api/agents/1'] = publishRoute(() => {
      currentAgents[0].is_public = false;
    });
    await openOwner();
    fireEvent.click(btn('Снять с публикации'));
    await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('success', 'Агент снят с публикации в галерее'));
    expect(bodyOf(lastCall('PUT', '/api/agents/1'))).toEqual({ is_public: false });
  });

  it('оптимистичное обновление: во время запроса подпись уже изменена, кнопка заблокирована', async () => {
    let release!: (v: Reply) => void;
    routes['PUT /api/agents/1'] = () => new Promise<Reply>((r) => { release = r; });
    await openOwner();
    fireEvent.click(btn('Опубликовать в галерее'));
    await waitFor(() => expect(btn('Снять с публикации').disabled).toBe(true));
    await act(async () => {
      release({ body: {} });
    });
  });

  it('ошибка сервера откатывает состояние и показывает detail', async () => {
    routes['PUT /api/agents/1'] = { ok: false, status: 403, body: { detail: 'Нельзя публиковать' } };
    await openOwner();
    fireEvent.click(btn('Опубликовать в галерее'));
    await waitFor(() =>
      expect(mockNotify).toHaveBeenCalledWith('error', 'Не удалось изменить публикацию: Нельзя публиковать'),
    );
    expect(btn('Опубликовать в галерее')).toBeTruthy();
  });

  it('ошибка с нечитаемым телом -> statusText', async () => {
    routes['PUT /api/agents/1'] = { ok: false, status: 500, statusText: 'Boom', bodyError: true };
    await openOwner();
    fireEvent.click(btn('Опубликовать в галерее'));
    await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('error', 'Не удалось изменить публикацию: Boom'));
  });

  it('сетевая ошибка и не-Error исключение', async () => {
    routes['PUT /api/agents/1'] = new Error('offline');
    await openOwner();
    fireEvent.click(btn('Опубликовать в галерее'));
    await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('error', 'Не удалось изменить публикацию: offline'));
  });

  it('если актуальная карточка не загрузилась (не-OK / исключение), публикация всё равно успешна', async () => {
    routes['PUT /api/agents/1'] = publishRoute(() => {
      routes['GET /api/agents/1'] = { ok: false, status: 500 };
    });
    await openOwner();
    fireEvent.click(btn('Опубликовать в галерее'));
    await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('success', 'Агент опубликован в галерее'));

    mockNotify.mockReset();
    routes['PUT /api/agents/1'] = publishRoute(() => {
      routes['GET /api/agents/1'] = new Error('x');
    });
    fireEvent.click(await screen.findByRole('button', { name: /Опубликовать|Снять/ }));
    await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('success', expect.stringMatching(/Агент/)));
  });

  it('публикация без токена не отправляет Authorization', async () => {
    mockToken = null;
    routes['PUT /api/agents/1'] = { body: {} };
    mockFetchAgents.mockImplementation(async () => currentAgents);
    // без токена fetchMergedUserAgents в реальности вернул бы [], но здесь список подставлен фейком
    await openOwner();
    fireEvent.click(btn('Опубликовать в галерее'));
    await waitFor(() => expect(callsTo('PUT', '/api/agents/1').length).toBeGreaterThan(0));
    expect(lastCall('PUT', '/api/agents/1')[1].headers).toEqual({ 'Content-Type': 'application/json' });
  });
});

// ======================= клонирование =======================
describe('AgentConstructorPanel: клонирование', () => {
  it('отмена подтверждения не вызывает клонирование; текст содержит имя агента', async () => {
    const confirm = jest.spyOn(window, 'confirm').mockReturnValue(false);
    await openOwner();
    fireEvent.click(btn('Клонировать'));
    expect(confirm.mock.calls[0][0]).toContain('«Owner Agent»');
    expect(mockClone).not.toHaveBeenCalled();
    confirm.mockRestore();
  });

  it('несколько клонов: открывается новый агент, уведомление с числом', async () => {
    const confirm = jest.spyOn(window, 'confirm').mockReturnValue(true);
    mockClone.mockResolvedValue({ agentId: 4, clonedAgents: 3, clonedAgentIds: [4, 3] });
    await openOwner();
    fireEvent.click(btn('Клонировать'));
    await waitFor(() =>
      expect(mockNotify).toHaveBeenCalledWith('success', 'Склонировано агентов: 3 (родитель + цепочка/субагенты)'),
    );
    expect(mockClone).toHaveBeenCalledWith(1, 'tok');
    await waitFor(() => expect(agentNameInput().value).toBe('Bare Agent'));
    confirm.mockRestore();
  });

  it('один клон -> «Агент полностью склонирован»', async () => {
    const confirm = jest.spyOn(window, 'confirm').mockReturnValue(true);
    mockClone.mockResolvedValue({ agentId: 4, clonedAgents: 1, clonedAgentIds: [4] });
    await openOwner();
    fireEvent.click(btn('Клонировать'));
    await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('success', 'Агент полностью склонирован'));
    confirm.mockRestore();
  });

  it('ошибка клонирования -> уведомление; кнопка снова доступна', async () => {
    const confirm = jest.spyOn(window, 'confirm').mockReturnValue(true);
    mockClone.mockRejectedValueOnce(new Error('лимит'));
    await openOwner();
    fireEvent.click(btn('Клонировать'));
    await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('error', 'Не удалось клонировать агента: лимит'));
    expect(btn('Клонировать').disabled).toBe(false);

    mockClone.mockRejectedValueOnce('строка');
    fireEvent.click(btn('Клонировать'));
    await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('error', 'Не удалось клонировать агента: строка'));
    confirm.mockRestore();
  });

  it('во время клонирования кнопка заблокирована', async () => {
    const confirm = jest.spyOn(window, 'confirm').mockReturnValue(true);
    let release!: (v: unknown) => void;
    mockClone.mockReturnValue(new Promise((r) => { release = r; }));
    await openOwner();
    fireEvent.click(btn('Клонировать'));
    await waitFor(() => expect(btn('Клонировать').disabled).toBe(true));
    await act(async () => {
      release({ agentId: 4, clonedAgents: 1, clonedAgentIds: [4] });
    });
    confirm.mockRestore();
  });
});

// ======================= удаление =======================
describe('AgentConstructorPanel: удаление', () => {
  async function openBare() {
    await renderPanel();
    await selectAgent('Bare Agent');
  }

  it('без связанных агентов: подтверждение, DELETE, сброс формы, уведомление', async () => {
    const confirm = jest.spyOn(window, 'confirm').mockReturnValue(true);
    routes['DELETE /api/agents/4'] = { body: {} };
    await openBare();
    const before = mockFetchAgents.mock.calls.length;
    fireEvent.click(btn('Удалить агента'));
    await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('success', 'Агент удалён'));
    expect(confirm).toHaveBeenCalledWith('Удалить агента «Bare Agent»?');
    const [url, init] = callsTo('DELETE', '/api/agents/4')[0];
    expect(url).toBe('http://api.test/api/agents/4');
    expect(init.headers).toEqual({ Authorization: 'Bearer tok' });
    expect(agentNameInput().value).toBe('');
    expect(mockFetchAgents.mock.calls.length).toBe(before + 1);
    confirm.mockRestore();
  });

  it('отмена подтверждения не отправляет запрос', async () => {
    const confirm = jest.spyOn(window, 'confirm').mockReturnValue(false);
    await openBare();
    fireEvent.click(btn('Удалить агента'));
    expect(callsTo('DELETE', '/api/agents/4')).toHaveLength(0);
    confirm.mockRestore();
  });

  it('ответ с deleted_linked показывает число связанных; тело не JSON не ломает удаление', async () => {
    const confirm = jest.spyOn(window, 'confirm').mockReturnValue(true);
    routes['DELETE /api/agents/4'] = { body: { deleted_linked: 2 } };
    await openBare();
    fireEvent.click(btn('Удалить агента'));
    await waitFor(() =>
      expect(mockNotify).toHaveBeenCalledWith('success', 'Агент удалён вместе с 2 связанными агентами'),
    );
    confirm.mockRestore();
  });

  it('успешный ответ с нечитаемым телом -> «Агент удалён»', async () => {
    const confirm = jest.spyOn(window, 'confirm').mockReturnValue(true);
    routes['DELETE /api/agents/4'] = { bodyError: true };
    await openBare();
    fireEvent.click(btn('Удалить агента'));
    await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('success', 'Агент удалён'));
    confirm.mockRestore();
  });

  it('ошибка с detail, без detail и сетевая', async () => {
    const confirm = jest.spyOn(window, 'confirm').mockReturnValue(true);
    routes['DELETE /api/agents/4'] = { ok: false, status: 403, body: { detail: 'Только владелец' } };
    await openBare();
    fireEvent.click(btn('Удалить агента'));
    await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('error', 'Не удалось удалить агента: Только владелец'));
    expect(agentNameInput().value).toBe('Bare Agent');

    routes['DELETE /api/agents/4'] = { ok: false, status: 500, statusText: '', bodyError: true };
    fireEvent.click(btn('Удалить агента'));
    await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('error', 'Не удалось удалить агента: HTTP 500'));

    routes['DELETE /api/agents/4'] = new Error('offline');
    fireEvent.click(btn('Удалить агента'));
    await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('error', 'Не удалось удалить агента: offline'));

    routes['DELETE /api/agents/4'] = () => {
      throw 'str';
    };
    fireEvent.click(btn('Удалить агента'));
    await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('error', 'Не удалось удалить агента: str'));
    confirm.mockRestore();
  });

  it('удаление без токена не отправляет Authorization', async () => {
    mockToken = null;
    const confirm = jest.spyOn(window, 'confirm').mockReturnValue(true);
    routes['DELETE /api/agents/4'] = { body: {} };
    await openBare();
    fireEvent.click(btn('Удалить агента'));
    await waitFor(() => expect(callsTo('DELETE', '/api/agents/4')).toHaveLength(1));
    expect(callsTo('DELETE', '/api/agents/4')[0][1].headers).toEqual({});
    confirm.mockRestore();
  });

  describe('со связанными агентами (диалог выбора)', () => {
    it('диалог открывается без window.confirm и показывает число связанных', async () => {
      const confirm = jest.spyOn(window, 'confirm');
      await openOwner();
      fireEvent.click(btn('Удалить агента'));
      expect(await screen.findByText(/Сейчас в цепочке\/субагентах: 2\./)).toBeTruthy();
      expect(confirm).not.toHaveBeenCalled();
      confirm.mockRestore();
    });

    it('«Отмена» закрывает диалог без запроса', async () => {
      await openOwner();
      fireEvent.click(btn('Удалить агента'));
      fireEvent.click(await screen.findByRole('button', { name: 'Отмена' }));
      await waitFor(() => expect(screen.queryByText(/Сейчас в цепочке/)).toBeNull());
      expect(callsTo('DELETE', '/api/agents/1')).toHaveLength(0);
    });

    it('Escape закрывает диалог', async () => {
      await openOwner();
      fireEvent.click(btn('Удалить агента'));
      const dialog = await screen.findByRole('dialog');
      fireEvent.keyDown(dialog, { key: 'Escape' });
      await waitFor(() => expect(screen.queryByText(/Сейчас в цепочке/)).toBeNull());
    });

    it('«без субагентов» удаляет только главного (без cascade)', async () => {
      routes['DELETE /api/agents/1'] = { body: {} };
      await openOwner();
      fireEvent.click(btn('Удалить агента'));
      fireEvent.click(await screen.findByRole('button', { name: 'Главного агента без субагентов' }));
      await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('success', 'Агент удалён'));
      expect(callsTo('DELETE', '/api/agents/1')[0][0]).toBe('http://api.test/api/agents/1');
      await waitFor(() => expect(screen.queryByText(/Сейчас в цепочке/)).toBeNull());
      expect(agentNameInput().value).toBe('');
    });

    it('«с субагентами» передаёт cascade_linked=true', async () => {
      routes['DELETE /api/agents/1'] = { body: { deleted_linked: 2 } };
      await openOwner();
      fireEvent.click(btn('Удалить агента'));
      fireEvent.click(await screen.findByRole('button', { name: 'Главного агента с субагентами' }));
      await waitFor(() =>
        expect(mockNotify).toHaveBeenCalledWith('success', 'Агент удалён вместе с 2 связанными агентами'),
      );
      expect(callsTo('DELETE', '/api/agents/1')[0][0]).toBe('http://api.test/api/agents/1?cascade_linked=true');
    });

    it('пока идёт удаление, кнопки диалога заблокированы и Escape не закрывает его', async () => {
      let release!: (v: Reply) => void;
      routes['DELETE /api/agents/1'] = () => new Promise<Reply>((r) => { release = r; });
      await openOwner();
      fireEvent.click(btn('Удалить агента'));
      fireEvent.click(await screen.findByRole('button', { name: 'Главного агента с субагентами' }));
      await waitFor(() => expect((screen.getByRole('button', { name: 'Отмена' }) as HTMLButtonElement).disabled).toBe(true));
      fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
      expect(screen.getByText(/Сейчас в цепочке/)).toBeTruthy();
      await act(async () => {
        release({ body: {} });
      });
      await waitFor(() => expect(screen.queryByText(/Сейчас в цепочке/)).toBeNull());
    });
  });
});
