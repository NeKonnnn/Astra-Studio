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

// ======================= загрузка и выбор агента =======================
describe('AgentConstructorPanel: открытие и загрузка данных', () => {
  it('isOpen=false: ничего не рендерит и не ходит в сеть', async () => {
    const { container } = await renderPanel({ open: false });
    expect(container.textContent).toBe('');
    expect(fetchMock).not.toHaveBeenCalled();
    expect(mockFetchAgents).not.toHaveBeenCalled();
  });

  it('при открытии загружает модели, настройки, документы, skills, агентов и лимиты цепочки один раз', async () => {
    const { rerender } = await renderPanel();
    expect(callsTo('GET', '/api/models/available')).toHaveLength(1);
    expect(callsTo('GET', '/api/llm-providers')).toHaveLength(1);
    expect(callsTo('GET', '/api/models/settings')[0][1].headers).toEqual({ Authorization: 'Bearer tok' });
    expect(callsTo('GET', '/api/kb/documents')).toHaveLength(1);
    expect(callsTo('GET', '/api/skills/list')[0][1].headers).toEqual({ Authorization: 'Bearer tok' });
    expect(mockFetchAgents).toHaveBeenCalledWith('tok', 100, { isAdmin: false });
    // повторный рендер при открытой панели не перезагружает данные
    rerender(<AgentConstructorPanel isDarkMode isOpen />);
    await flush();
    expect(callsTo('GET', '/api/models/available')).toHaveLength(1);
    // закрыли и открыли — загрузка повторяется
    rerender(<AgentConstructorPanel isDarkMode isOpen={false} />);
    rerender(<AgentConstructorPanel isDarkMode isOpen />);
    await flush();
    expect(callsTo('GET', '/api/models/available')).toHaveLength(2);
  });

  it('admin: агенты запрашиваются с isAdmin=true', async () => {
    mockUser = { is_admin: true, user_id: 'A' };
    await renderPanel();
    expect(mockFetchAgents).toHaveBeenCalledWith('tok', 100, { isAdmin: true });
  });

  it('лимиты цепочки/субагентов/шагов из конфигурации сервера попадают в дочерние компоненты', async () => {
    await renderPanel();
    expect(screen.getByTestId('chain-settings').getAttribute('data-steps')).toBe('40');
    expect(screen.getByTestId('chain-settings').getAttribute('data-max-recursion')).toBe('400');
    expect(screen.getByTestId('limit-Максимум агентов в цепочке').getAttribute('data-max')).toBe('6');
    expect(screen.getByTestId('limit-Максимум субагентов').getAttribute('data-max')).toBe('7');
    expect(screen.getByTestId('chain').getAttribute('data-max')).toBe('6');
    expect(screen.getByTestId('subagents').getAttribute('data-max')).toBe('7');
  });

  it('ошибки загрузок не ломают панель (silent)', async () => {
    routes['GET /api/models/available'] = new Error('x');
    routes['GET /api/models/settings'] = new Error('x');
    routes['GET /api/kb/documents'] = new Error('x');
    routes['GET /api/skills/list'] = new Error('x');
    mockFetchMcp.mockRejectedValue(new Error('x'));
    mockFetchPlugins.mockRejectedValue(new Error('x'));
    mockFetchAgents.mockRejectedValue(new Error('x'));
    await renderPanel();
    expect(agentNameInput()).toBeTruthy();
    fireEvent.click(screen.getByText('Агенты'));
    expect(await screen.findByText('+ Новый агент')).toBeTruthy();
  });

  it('не-OK ответы настроек, документов и skills игнорируются', async () => {
    routes['GET /api/models/available'] = { ok: false, status: 500 };
    routes['GET /api/llm-providers'] = { ok: false, status: 500 };
    routes['GET /api/models/settings'] = { ok: false, status: 500 };
    routes['GET /api/kb/documents'] = { ok: false, status: 500 };
    routes['GET /api/skills/list'] = { ok: false, status: 500 };
    await renderPanel();
    fireEvent.click(modelInput());
    const modal = screen.getByTestId('model-modal');
    expect(modal.getAttribute('data-models')).toBe('[]');
    expect(modal.getAttribute('data-providers')).toBe('[]');
  });

  it('настройки пользователя не-объект игнорируются; объект подмешивается к дефолтам', async () => {
    routes['GET /api/models/settings'] = { body: null };
    await renderPanel();
    fireEvent.click(modelInput());
    expect(JSON.parse(screen.getByTestId('model-modal').getAttribute('data-settings')!)).not.toHaveProperty('temperature', 0.1);
  });

  it('каталог моделей и провайдеров: нормализуются строки/объекты, пустые отбрасываются', async () => {
    await renderPanel();
    fireEvent.click(modelInput());
    const modal = screen.getByTestId('model-modal');
    expect(JSON.parse(modal.getAttribute('data-models')!)).toEqual(['llm-svc://h/m1', 'llm-svc://h/m2']);
    expect(JSON.parse(modal.getAttribute('data-provider-models')!)).toEqual([
      { name: 'llm-svc://h/m1', path: 'llm-svc://h/m1' },
      { name: 'n2', path: 'llm-svc://h/m2', display_name: 'M2', provider_id: 'host1', llm_host_id: 'host1' },
    ]);
    expect(JSON.parse(modal.getAttribute('data-providers')!)).toEqual(['prov1']);
  });

  it('каталог моделей может быть массивом без обёртки; провайдеры без поля — пустой список', async () => {
    routes['GET /api/models/available'] = { body: ['llm-svc://h/z'] };
    routes['GET /api/llm-providers'] = { body: {} };
    await renderPanel();
    fireEvent.click(modelInput());
    expect(JSON.parse(screen.getByTestId('model-modal').getAttribute('data-models')!)).toEqual(['llm-svc://h/z']);
    expect(screen.getByTestId('model-modal').getAttribute('data-providers')).toBe('[]');
  });

  it('новая карточка подставляет первую доступную модель после загрузки каталога (resetForm)', async () => {
    await renderPanel();
    // модель пуста: resetForm вызвался до загрузки каталога
    expect(modelInput().value).toBe('');
    await selectAgent('Owner Agent');
    fireEvent.click(screen.getByText('Агенты'));
    fireEvent.click(await screen.findByText('+ Новый агент'));
    await flush(2);
    expect(modelInput().value).toBe('m1');
  });
});

describe('AgentConstructorPanel: открытие агента извне', () => {
  it('id в sessionStorage открывает агента после загрузки списка и очищает ключ', async () => {
    sessionStorage.setItem(ASTRA_OPEN_AGENT_CONSTRUCTOR_ID_KEY, '1');
    await renderPanel();
    expect(agentNameInput().value).toBe('Owner Agent');
    expect(sessionStorage.getItem(ASTRA_OPEN_AGENT_CONSTRUCTOR_ID_KEY)).toBeNull();
  });

  it('связанные агенты (related + из config) подтягиваются и попадают в список', async () => {
    sessionStorage.setItem(ASTRA_OPEN_AGENT_CONSTRUCTOR_ID_KEY, '1');
    sessionStorage.setItem('openAgentConstructorRelatedIds', JSON.stringify([4, 1, 0]));
    mockFetchAgents.mockResolvedValue([]);
    await renderPanel();
    expect(agentNameInput().value).toBe('Owner Agent');
    // агенты 2 и 3 из config.agent_ids, 4 из related — все запрошены
    [2, 3, 4].forEach((id) => expect(callsTo('GET', `/api/agents/${id}`)).toHaveLength(1));
    fireEvent.click(screen.getByText('Агенты'));
    expect(await screen.findByText('Bare Agent')).toBeTruthy();
  });

  it('падение запроса связанного агента не мешает открыть основной', async () => {
    sessionStorage.setItem(ASTRA_OPEN_AGENT_CONSTRUCTOR_ID_KEY, '1');
    routes['GET /api/agents/2'] = new Error('x');
    routes['GET /api/agents/3'] = { ok: false, status: 404 };
    await renderPanel();
    expect(agentNameInput().value).toBe('Owner Agent');
  });

  it('основной агент не найден -> уведомление об ошибке', async () => {
    sessionStorage.setItem(ASTRA_OPEN_AGENT_CONSTRUCTOR_ID_KEY, '77');
    await renderPanel();
    expect(mockNotify).toHaveBeenCalledWith('error', 'Не удалось открыть агента в конструкторе');
  });

  it('исключение при запросе основного агента -> то же уведомление', async () => {
    sessionStorage.setItem(ASTRA_OPEN_AGENT_CONSTRUCTOR_ID_KEY, '1');
    routes['GET /api/agents/1'] = new Error('boom');
    await renderPanel();
    expect(mockNotify).toHaveBeenCalledWith('error', 'Не удалось открыть агента в конструкторе');
  });

  it.each(['abc', '0', '-3'])('некорректный id %s в sessionStorage игнорируется', async (raw) => {
    sessionStorage.setItem(ASTRA_OPEN_AGENT_CONSTRUCTOR_ID_KEY, raw);
    await renderPanel();
    expect(callsTo('GET', '/api/agents/1')).toHaveLength(0);
    expect(agentNameInput().value).toBe('');
  });

  it('sessionStorage бросает исключение -> панель работает', async () => {
    const spy = jest.spyOn(Storage.prototype, 'getItem').mockImplementation((key: string) => {
      if (key === ASTRA_OPEN_AGENT_CONSTRUCTOR_ID_KEY) throw new Error('denied');
      return null;
    });
    await renderPanel();
    spy.mockRestore();
    expect(agentNameInput()).toBeTruthy();
  });

  it('событие ASTRA_OPEN_AGENT_CONSTRUCTOR с agentId открывает агента (relatedAgentIds учитываются)', async () => {
    await renderPanel();
    act(() => {
      window.dispatchEvent(
        new CustomEvent(ASTRA_OPEN_AGENT_CONSTRUCTOR, { detail: { agentId: 4, relatedAgentIds: [2] } }),
      );
    });
    await flush();
    expect(agentNameInput().value).toBe('Bare Agent');
    expect(callsTo('GET', '/api/agents/2').length).toBeGreaterThan(0);
  });

  it.each([
    ['без detail', undefined],
    ['agentId строка', { agentId: '4' }],
    ['agentId=0', { agentId: 0 }],
    ['agentId=NaN', { agentId: NaN }],
  ])('событие открытия %s игнорируется', async (_n, detail) => {
    await renderPanel();
    act(() => {
      window.dispatchEvent(new CustomEvent(ASTRA_OPEN_AGENT_CONSTRUCTOR, { detail }));
    });
    await flush();
    expect(callsTo('GET', '/api/agents/4')).toHaveLength(0);
  });

  it('при открытии без токена заголовок Authorization не отправляется', async () => {
    mockToken = null;
    sessionStorage.setItem(ASTRA_OPEN_AGENT_CONSTRUCTOR_ID_KEY, '1');
    await renderPanel();
    // без токена authFetch не подставляет Authorization ни в один запрос панели
    for (const call of [
      ...callsTo('GET', '/api/agents/1'),
      ...callsTo('GET', '/api/skills/list'),
      ...callsTo('GET', '/api/models/settings'),
    ]) {
      expect((call[1]?.headers ?? {}) as Record<string, string>).not.toHaveProperty('Authorization');
    }
  });
});

describe('AgentConstructorPanel: выбор агента в списке', () => {
  it('список содержит «+ Новый агент» и всех агентов; поиск фильтрует и показывает «Не найдено»', async () => {
    await renderPanel();
    fireEvent.click(screen.getByText('Агенты'));
    expect(await screen.findByText('+ Новый агент')).toBeTruthy();
    ['Owner Agent', 'Viewer Agent', 'Editor Agent', 'Bare Agent'].forEach((n) => expect(screen.getByText(n)).toBeTruthy());
    const search = screen.getByPlaceholderText('Поиск агентов по имени');
    fireEvent.change(search, { target: { value: 'EDITOR' } });
    expect(screen.queryByText('Owner Agent')).toBeNull();
    expect(screen.getByText('Editor Agent')).toBeTruthy();
    fireEvent.change(search, { target: { value: 'нет такого' } });
    expect(screen.getByText('Не найдено')).toBeTruthy();
  });

  it('закрытие списка (Escape) сбрасывает поиск', async () => {
    await renderPanel();
    fireEvent.click(screen.getByText('Агенты'));
    const search = await screen.findByPlaceholderText('Поиск агентов по имени');
    fireEvent.change(search, { target: { value: 'abc' } });
    fireEvent.keyDown(search, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByPlaceholderText('Поиск агентов по имени')).toBeNull());
    fireEvent.click(screen.getByText('Агенты'));
    expect((await screen.findByPlaceholderText('Поиск агентов по имени') as HTMLInputElement).value).toBe('');
  });

  it('без права создавать агентов «+ Новый агент» скрыт', async () => {
    mockCanCreate = false;
    await renderPanel();
    fireEvent.click(screen.getByText('Агенты'));
    await screen.findByText('Owner Agent');
    expect(screen.queryByText('+ Новый агент')).toBeNull();
  });

  it('«+ Новый агент» очищает форму выбранного агента', async () => {
    await renderPanel();
    await selectAgent('Owner Agent');
    expect(agentNameInput().value).toBe('Owner Agent');
    fireEvent.click(screen.getByText('Агенты'));
    fireEvent.click(await screen.findByText('+ Новый агент'));
    await flush(2);
    expect(agentNameInput().value).toBe('');
    expect(descInput().value).toBe('');
    expect(tagsInput().value).toBe('');
    expect(categoryInput().value).toBe('Общий');
    expect(supportEmailInput().value).toBe('');
    expect(screen.queryByText(/agent_0000/)).toBeNull();
  });

  it('при выборе агента форма заполняется из карточки', async () => {
    await renderPanel();
    await selectAgent('Owner Agent');
    expect(agentNameInput().value).toBe('Owner Agent');
    expect(screen.getByText('agent_000001')).toBeTruthy();
    expect(descInput().value).toBe('описание');
    expect(tagsInput().value).toBe('Fin');
    expect(categoryInput().value).toBe('Код');
    expect(instrInput().value).toBe('Достаточно длинный промпт');
    expect(modelInput().value).toBe('model-a');
    expect(supportNameInput().value).toBe('Саппорт');
    expect(supportEmailInput().value).toBe('sup@example.com');
    expect(switchFor('Включить артефакты').checked).toBe(true);
    expect(switchFor('Включить компоненты shadcn/ui').checked).toBe(true);
    expect(switchFor('Режим пользовательского промта').checked).toBe(false);
    expect(screen.getByTestId('chain').getAttribute('data-ids')).toBe('[2,3]');
    expect(screen.getByTestId('chain').getAttribute('data-max')).toBe('3');
    expect(screen.getByTestId('chain').getAttribute('data-name')).toBe('Owner Agent');
    const cs = screen.getByTestId('chain-settings');
    expect(cs.getAttribute('data-hide')).toBe('true');
    expect(cs.getAttribute('data-shared')).toBe('true');
    expect(cs.getAttribute('data-parent')).toBe('false');
    expect(cs.getAttribute('data-recursion')).toBe('20');
    expect(screen.getByTestId('limit-Максимум агентов в цепочке').getAttribute('data-value')).toBe('3');
    expect(screen.getByTestId('limit-Максимум субагентов').getAttribute('data-value')).toBe('4');
    const sub = JSON.parse(screen.getByTestId('subagents').getAttribute('data-config')!);
    expect(sub).toMatchObject({ enabled: true, allow_self: false, agent_ids: [2], required_tag_ids: [1], required_only: true });
    expect(sub.agent_names).toMatchObject({ 2: 'Viewer Agent' });
    // skills / mcp / plugins: по одному выбранному показывается название
    expect((document.getElementById('agent-constructor-skills') as HTMLInputElement).value).toBe('Skill One');
    expect((document.getElementById('agent-constructor-mcp') as HTMLInputElement).value).toBe('MCP One');
    expect((document.getElementById('agent-constructor-plugins') as HTMLInputElement).value).toBe('Plugin One');
  });

  it('карточка с пустым config: дефолты; переключение между агентами сбрасывает значения', async () => {
    await renderPanel();
    await selectAgent('Owner Agent');
    await selectAgent('Bare Agent');
    expect(agentNameInput().value).toBe('Bare Agent');
    expect(descInput().value).toBe('');
    expect(categoryInput().value).toBe('Общий');
    expect(modelInput().value).toBe('');
    expect(tagsInput().value).toBe('');
    expect(switchFor('Включить артефакты').checked).toBe(false);
    expect(screen.getByTestId('chain').getAttribute('data-ids')).toBe('[]');
    expect(screen.getByTestId('chain-settings').getAttribute('data-parent')).toBe('true');
    expect(screen.getByTestId('chain-settings').getAttribute('data-recursion')).toBe('');
    expect(screen.getByTestId('limit-Максимум субагентов').getAttribute('data-value')).toBe('');
    expect(JSON.parse(screen.getByTestId('subagents').getAttribute('data-config')!).enabled).toBe(false);
  });

  it('несколько выбранных элементов -> «Выбрано: N», строковые теги; плагины включаются по наличию plugin_ids', async () => {
    currentAgents[2].config = { ...currentAgents[2].config, mcp_enabled: true };
    await renderPanel();
    await selectAgent('Editor Agent');
    expect(tagsInput().value).toBe('x');
    // skills_enabled не задан, skill_ids непусты -> включено; два skill -> «Выбрано: 2»
    expect((document.getElementById('agent-constructor-skills') as HTMLInputElement).value).toBe('Выбрано: 2');
    expect((document.getElementById('agent-constructor-mcp') as HTMLInputElement).value).toBe('Выбрано: 2');
    expect((document.getElementById('agent-constructor-plugins') as HTMLInputElement).value).toBe('Выбрано: 2');
    expect(instrInput().value).toBe('');
  });

  it('config.skills (старый формат) используется, если skill_ids отсутствует', async () => {
    currentAgents[2].config = { skills: ['legacy', ' '] };
    await renderPanel();
    await selectAgent('Editor Agent');
    // skills_enabled не boolean и skill_ids нет -> выключено
    expect(screen.queryByLabelText(/^Skills$/)).toBeNull();
    fireEvent.click(screen.getByLabelText('Включить skills для агента'));
    expect((document.getElementById('agent-constructor-skills') as HTMLInputElement).value).toBe('legacy');
  });

  it('скалярные поля config некорректных типов нормализуются', async () => {
    currentAgents[3].config = {
      model_params: undefined, kb_document_ids: 'nope', skill_ids: 'nope', mcp_server_ids: 'nope',
      plugin_ids: 'nope', max_chain_agents: -1, max_subagents: 'x', recursion_limit: 0,
      subagents: { enabled: 'yes', allowSelf: false },
      shadcn_enabled: true, user_prompt_mode: true,
    };
    await renderPanel();
    await selectAgent('Bare Agent');
    expect(screen.getByTestId('limit-Максимум агентов в цепочке').getAttribute('data-value')).toBe('');
    expect(screen.getByTestId('chain-settings').getAttribute('data-recursion')).toBe('');
    const sub = JSON.parse(screen.getByTestId('subagents').getAttribute('data-config')!);
    expect(sub.enabled).toBe(false);
    expect(sub.allow_self).toBe(false);
    // shadcn и user_prompt вместе -> оба сбрасываются
    expect(switchFor('Режим пользовательского промта').checked).toBe(false);
    expect(switchFor('Включить компоненты shadcn/ui').checked).toBe(false);
  });

  it('user_prompt_mode без shadcn включает режим пользовательского промта', async () => {
    currentAgents[3].config = { artifacts_enabled: true, user_prompt_mode: true };
    await renderPanel();
    await selectAgent('Bare Agent');
    expect(switchFor('Режим пользовательского промта').checked).toBe(true);
    expect(switchFor('Включить компоненты shadcn/ui').disabled).toBe(true);
  });
});

describe('AgentConstructorPanel: права и режим только чтения', () => {
  it('новый агент: все поля доступны, кнопка «Сохранить», нет кнопок управления существующим', async () => {
    await renderPanel();
    expect(agentNameInput().disabled).toBe(false);
    expect(saveBtn()).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Клонировать' })).toBeNull();
    expect(screen.queryByTestId('share-dialog')).toBeNull();
  });

  it('owner: доступны Клонировать/Поделиться/Опубликовать/Удалить/Сохранить, баннера нет', async () => {
    await renderPanel();
    await selectAgent('Owner Agent');
    ['Клонировать', 'Поделиться агентом', 'Опубликовать в галерее', 'Удалить агента', 'Сохранить'].forEach((n) =>
      expect(btn(n)).toBeTruthy(),
    );
    expect(screen.queryByText(/Общий агент/)).toBeNull();
  });

  it('viewer: поля заблокированы, баннер «Зритель», кнопка «Использовать в чате» вместо «Сохранить»', async () => {
    await renderPanel();
    await selectAgent('Viewer Agent');
    expect(screen.getByText(/роль «Зритель»/)).toBeTruthy();
    expect(agentNameInput().disabled).toBe(true);
    expect(instrInput().disabled).toBe(true);
    expect(supportEmailInput().disabled).toBe(true);
    expect(tagsInput().disabled).toBe(true);
    expect(screen.queryByRole('button', { name: 'Сохранить' })).toBeNull();
    expect(btn('Использовать в чате')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Удалить агента' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Поделиться агентом' })).toBeNull();
    expect(screen.queryByRole('button', { name: /Опубликовать|Снять/ })).toBeNull();
    expect(btn('Клонировать')).toBeTruthy();
    expect(switchFor('Включить артефакты').disabled).toBe(true);
    // клики по выпадающим полям не открывают списки в режиме чтения
    fireEvent.click(categoryInput());
    expect(screen.queryByText('Исследование')).toBeNull();
  });

  it('viewer: «Использовать в чате» записывает агента в localStorage и рассылает agentSelected', async () => {
    await renderPanel();
    await selectAgent('Viewer Agent');
    const handler = jest.fn();
    window.addEventListener('agentSelected', handler);
    fireEvent.click(btn('Использовать в чате'));
    await waitFor(() => expect(localStorage.getItem('active_agent_id')).toBe('2'));
    window.removeEventListener('agentSelected', handler);
    expect(localStorage.getItem('active_agent_prompt')).toBe('Промпт зрителя тут');
    expect(localStorage.getItem('active_agent_name')).toBe('Viewer Agent');
    // persist получает config карточки, дополненный текущими флагами формы
    expect(mockPersistMcp).toHaveBeenCalledWith(expect.objectContaining({ mcp_enabled: false, mcp_server_ids: [] }));
    expect(mockPersistArtifacts).toHaveBeenCalledWith(expect.objectContaining({ artifacts_enabled: expect.any(Boolean) }));
    expect((handler.mock.calls[0][0] as CustomEvent).detail).toMatchObject({ id: 2, name: 'Viewer Agent' });
  });

  it('viewer без config: persist получает пустой объект; с config — сам config', async () => {
    currentAgents[1].config = undefined;
    await renderPanel();
    await selectAgent('Viewer Agent');
    fireEvent.click(btn('Использовать в чате'));
    await waitFor(() =>
      expect(mockPersistMcp).toHaveBeenCalledWith(expect.objectContaining({ mcp_enabled: false, mcp_server_ids: [] })),
    );
  });

  it('editor: можно сохранять, но нет шаринга/удаления/публикации; баннер «Редактор»', async () => {
    await renderPanel();
    await selectAgent('Editor Agent');
    expect(screen.getByText(/роль «Редактор»/)).toBeTruthy();
    expect(agentNameInput().disabled).toBe(false);
    expect(btn('Сохранить')).toBeTruthy();
    expect(btn('Клонировать')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Поделиться агентом' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Удалить агента' })).toBeNull();
    expect(screen.queryByRole('button', { name: /Опубликовать|Снять/ })).toBeNull();
  });

  it('admin получает права владельца на чужого агента', async () => {
    mockUser = { is_admin: true, user_id: 'A' };
    await renderPanel();
    await selectAgent('Viewer Agent');
    expect(agentNameInput().disabled).toBe(false);
    expect(btn('Удалить агента')).toBeTruthy();
    expect(btn('Поделиться агентом')).toBeTruthy();
    expect(screen.queryByText(/Общий агент/)).toBeNull();
  });

  it('агент без my_permission трактуется как зритель', async () => {
    currentAgents[3].my_permission = null;
    await renderPanel();
    await selectAgent('Bare Agent');
    expect(agentNameInput().disabled).toBe(true);
  });

  it('без права публиковать в библиотеку кнопки публикации нет; без canUseAgents нет «Клонировать»', async () => {
    mockCanPublish = false;
    mockCanUse = false;
    await renderPanel();
    await selectAgent('Owner Agent');
    expect(screen.queryByRole('button', { name: /Опубликовать|Снять/ })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Клонировать' })).toBeNull();
    expect(btn('Удалить агента')).toBeTruthy();
  });

  it('опубликованный агент: кнопка «Снять с публикации»', async () => {
    currentAgents[0].is_public = true;
    await renderPanel();
    await selectAgent('Owner Agent');
    expect(btn('Снять с публикации')).toBeTruthy();
  });

  it('«Поделиться агентом» открывает диалог с id и именем; закрытие работает', async () => {
    await renderPanel();
    await selectAgent('Owner Agent');
    expect(screen.getByTestId('share-dialog').getAttribute('data-open')).toBe('false');
    fireEvent.click(btn('Поделиться агентом'));
    const d = screen.getByTestId('share-dialog');
    expect(d.getAttribute('data-open')).toBe('true');
    expect(d.getAttribute('data-id')).toBe('1');
    expect(d.getAttribute('data-name')).toBe('Owner Agent');
    fireEvent.click(screen.getByText('close-share'));
    expect(screen.getByTestId('share-dialog').getAttribute('data-open')).toBe('false');
  });
});

describe('AgentConstructorPanel: поля формы', () => {
  it('текстовые поля обновляются; ошибка инструкции короче 10 символов показывается', async () => {
    await renderPanel();
    fireEvent.change(agentNameInput(), { target: { value: 'N' } });
    fireEvent.change(descInput(), { target: { value: 'D' } });
    fireEvent.change(supportNameInput(), { target: { value: 'S' } });
    fireEvent.change(supportEmailInput(), { target: { value: 'e@e.e' } });
    expect(agentNameInput().value).toBe('N');
    expect(descInput().value).toBe('D');
    expect(supportNameInput().value).toBe('S');
    expect(supportEmailInput().value).toBe('e@e.e');
    expect(screen.queryByRole('alert')).toBeNull();
    fireEvent.change(instrInput(), { target: { value: 'коротко' } });
    expect(screen.getByRole('alert').textContent).toContain('не может быть меньше 10 символов');
    fireEvent.change(instrInput(), { target: { value: 'достаточно длинная инструкция' } });
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('категория выбирается из выпадающего списка', async () => {
    await renderPanel();
    fireEvent.click(categoryInput());
    fireEvent.click(await screen.findByText('Исследование'));
    await waitFor(() => expect(categoryInput().value).toBe('Исследование'));
  });

  it('закрытие списка категорий по Escape', async () => {
    await renderPanel();
    fireEvent.click(categoryInput());
    const item = await screen.findByText('Анализ');
    fireEvent.keyDown(item, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByText('Анализ')).toBeNull());
    expect(categoryInput().value).toBe('Общий');
  });

  it('артефакты: shadcn и пользовательский промт взаимоисключающие; выключение артефактов сбрасывает оба', async () => {
    await renderPanel();
    expect(switchFor('Включить компоненты shadcn/ui').disabled).toBe(true);
    fireEvent.click(switchFor('Включить артефакты'));
    expect(switchFor('Включить артефакты').checked).toBe(true);
    fireEvent.click(switchFor('Включить компоненты shadcn/ui'));
    expect(switchFor('Включить компоненты shadcn/ui').checked).toBe(true);
    expect(switchFor('Режим пользовательского промта').disabled).toBe(true);
    fireEvent.click(switchFor('Включить компоненты shadcn/ui'));
    fireEvent.click(switchFor('Режим пользовательского промта'));
    expect(switchFor('Режим пользовательского промта').checked).toBe(true);
    expect(switchFor('Включить компоненты shadcn/ui').disabled).toBe(true);
    fireEvent.click(switchFor('Включить артефакты'));
    expect(switchFor('Режим пользовательского промта').checked).toBe(false);
    expect(switchFor('Включить компоненты shadcn/ui').checked).toBe(false);
  });

  it('shadcn при включении снимает пользовательский промт (программно через оба переключателя)', async () => {
    currentAgents[3].config = { artifacts_enabled: true, user_prompt_mode: true };
    await renderPanel();
    await selectAgent('Bare Agent');
    fireEvent.click(switchFor('Режим пользовательского промта'));
    fireEvent.click(switchFor('Включить компоненты shadcn/ui'));
    expect(switchFor('Включить компоненты shadcn/ui').checked).toBe(true);
    expect(switchFor('Режим пользовательского промта').checked).toBe(false);
  });

  it('теги: изменение через поле обновляет значение', async () => {
    await renderPanel();
    fireEvent.change(tagsInput(), { target: { value: 'a, b' } });
    expect(tagsInput().value).toBe('a,b');
  });
});

describe('AgentConstructorPanel: skills / MCP / плагины', () => {
  it('skills: включение, выбор нескольких, сводка, снятие выбора, выключение сбрасывает выбор', async () => {
    await renderPanel();
    expect(document.getElementById('agent-constructor-skills')).toBeNull();
    fireEvent.click(screen.getByLabelText('Включить skills для агента'));
    const input = document.getElementById('agent-constructor-skills') as HTMLInputElement;
    expect(input.value).toBe('');
    fireEvent.click(input);
    fireEvent.click(await screen.findByText('Skill One'));
    expect(input.value).toBe('Skill One');
    fireEvent.click(screen.getByText('Skill Two'));
    expect(input.value).toBe('Выбрано: 2');
    expect(screen.queryByText('Skill Off')).toBeNull();
    fireEvent.click(screen.getByText('Skill One'));
    expect(input.value).toBe('Skill Two');
    fireEvent.keyDown(screen.getByText('Skill Two'), { key: 'Escape' });
    await waitFor(() => expect(screen.queryByText('Skill Two')).toBeNull());
    fireEvent.click(screen.getByLabelText('Включить skills для агента'));
    expect(document.getElementById('agent-constructor-skills')).toBeNull();
    fireEvent.click(screen.getByLabelText('Включить skills для агента'));
    expect((document.getElementById('agent-constructor-skills') as HTMLInputElement).value).toBe('');
  });

  it('skills: пустой список -> «Нет доступных skills»; название по slug, если skill неизвестен', async () => {
    routes['GET /api/skills/list'] = { body: {} };
    currentAgents[3].config = { skill_ids: ['ghost'] };
    await renderPanel();
    await selectAgent('Bare Agent');
    const input = document.getElementById('agent-constructor-skills') as HTMLInputElement;
    expect(input.value).toBe('ghost');
    fireEvent.click(input);
    expect(await screen.findByText('Нет доступных skills')).toBeTruthy();
  });

  it('MCP: включение, выбор, названия, «Нет подключённых MCP-серверов», выключение сбрасывает', async () => {
    await renderPanel();
    fireEvent.click(screen.getByLabelText(/Добавить MCP/));
    const input = document.getElementById('agent-constructor-mcp') as HTMLInputElement;
    fireEvent.click(input);
    fireEvent.click(await screen.findByText('MCP One'));
    expect(input.value).toBe('MCP One');
    fireEvent.click(screen.getByText('m2'));
    expect(input.value).toBe('Выбрано: 2');
    expect(screen.queryByText('Off')).toBeNull();
    fireEvent.click(screen.getByText('MCP One'));
    expect(input.value).toBe('m2');
    fireEvent.keyDown(screen.getByText('m2'), { key: 'Escape' });
    await waitFor(() => expect(screen.queryByText('m2')).toBeNull());
    fireEvent.click(screen.getByLabelText(/Добавить MCP/));
    expect(document.getElementById('agent-constructor-mcp')).toBeNull();
  });

  it('MCP: пустой список серверов', async () => {
    mockFetchMcp.mockResolvedValue([]);
    await renderPanel();
    fireEvent.click(screen.getByLabelText(/Добавить MCP/));
    fireEvent.click(document.getElementById('agent-constructor-mcp')!);
    expect(await screen.findByText('Нет подключённых MCP-серверов')).toBeTruthy();
  });

  it('плагины: включение, выбор, сводка, «Нет плагинов в галерее», выключение сбрасывает', async () => {
    await renderPanel();
    fireEvent.click(screen.getByLabelText(/Добавить плагины/));
    const input = document.getElementById('agent-constructor-plugins') as HTMLInputElement;
    fireEvent.click(input);
    fireEvent.click(await screen.findByText('Plugin One'));
    expect(input.value).toBe('Plugin One');
    fireEvent.click(screen.getAllByText('p2')[0]);
    expect(input.value).toBe('Выбрано: 2');
    expect(screen.queryByText('Off')).toBeNull();
    fireEvent.click(screen.getByText('Plugin One'));
    expect(input.value).toBe('p2');
    fireEvent.keyDown(screen.getAllByText('p2')[0], { key: 'Escape' });
    await waitFor(() => expect(screen.queryByText('Plugin One')).toBeNull());
    fireEvent.click(screen.getByLabelText(/Добавить плагины/));
    expect(document.getElementById('agent-constructor-plugins')).toBeNull();
  });

  it('плагины: пустой список', async () => {
    mockFetchPlugins.mockResolvedValue([]);
    await renderPanel();
    fireEvent.click(screen.getByLabelText(/Добавить плагины/));
    fireEvent.click(document.getElementById('agent-constructor-plugins')!);
    expect(await screen.findByText('Нет плагинов в галерее')).toBeTruthy();
  });
});

describe('AgentConstructorPanel: режим чтения для вложенных списков', () => {
  it('зритель не может открыть списки skills/MCP/плагинов', async () => {
    currentAgents[1].config = { skills_enabled: true, mcp_enabled: true, plugins_enabled: true };
    await renderPanel();
    await selectAgent('Viewer Agent');
    expect((screen.getByLabelText('Включить skills для агента') as HTMLInputElement).disabled).toBe(true);
    fireEvent.click(document.getElementById('agent-constructor-skills')!);
    fireEvent.click(document.getElementById('agent-constructor-mcp')!);
    fireEvent.click(document.getElementById('agent-constructor-plugins')!);
    expect(screen.queryByText('Skill One')).toBeNull();
    expect(screen.queryByText('MCP One')).toBeNull();
    expect(screen.queryByText('Plugin One')).toBeNull();
  });
});

describe('AgentConstructorPanel: лимиты, цепочка, субагенты', () => {
  it('лимит цепочки: установка обрезает цепочку, сброс возвращает платформенный лимит', async () => {
    await renderPanel();
    fireEvent.click(screen.getByText('chain-set'));
    expect(screen.getByTestId('chain').getAttribute('data-ids')).toBe('[2,3,4]');
    fireEvent.click(screen.getByText('Максимум агентов в цепочке:2'));
    expect(screen.getByTestId('chain').getAttribute('data-ids')).toBe('[2,3]');
    expect(screen.getByTestId('chain').getAttribute('data-max')).toBe('2');
    fireEvent.click(screen.getByText('Максимум агентов в цепочке:clear'));
    expect(screen.getByTestId('chain').getAttribute('data-max')).toBe('6');
  });

  it('лимит субагентов обрезает список и пересобирает снимок имён; при сбросе используется платформенный', async () => {
    await renderPanel();
    fireEvent.click(screen.getByText('sub-three'));
    expect(JSON.parse(screen.getByTestId('subagents').getAttribute('data-config')!).agent_ids).toEqual([2, 3, 99]);
    fireEvent.click(screen.getByText('Максимум субагентов:2'));
    const cfg = JSON.parse(screen.getByTestId('subagents').getAttribute('data-config')!);
    expect(cfg.agent_ids).toEqual([2, 3]);
    expect(cfg.agent_names).toMatchObject({ 2: 'Viewer Agent', 3: 'Editor Agent' });
    fireEvent.click(screen.getByText('Максимум субагентов:clear'));
    expect(screen.getByTestId('subagents').getAttribute('data-max')).toBe('7');
  });

  it('общие настройки цепочки: все колбэки меняют состояние', async () => {
    await renderPanel();
    const cs = () => screen.getByTestId('chain-settings');
    fireEvent.click(screen.getByText('set-recursion'));
    expect(cs().getAttribute('data-recursion')).toBe('15');
    fireEvent.click(screen.getByText('toggle-hide'));
    expect(cs().getAttribute('data-hide')).toBe('true');
    fireEvent.click(screen.getByText('toggle-shared'));
    expect(cs().getAttribute('data-shared')).toBe('true');
    fireEvent.click(screen.getByText('toggle-parent'));
    expect(cs().getAttribute('data-parent')).toBe('false');
  });
});

describe('AgentConstructorPanel: внутренние панели', () => {
  it('«Модель LLM» открывает панель параметров; сохранение меняет модель и параметры', async () => {
    await renderPanel();
    fireEvent.click(modelInput());
    const modal = screen.getByTestId('model-modal');
    expect(modal.getAttribute('data-readonly')).toBe('false');
    expect(modal.getAttribute('data-params')).toBe('null');
    fireEvent.click(screen.getByText('mp-save'));
    expect(screen.queryByTestId('model-modal')).toBeNull();
    expect(modelInput().value).toBe('new-model');
  });

  it('сохранение без params -> пустые параметры; закрытие без сохранения не меняет модель', async () => {
    await renderPanel();
    await selectAgent('Owner Agent');
    fireEvent.click(modelInput());
    expect(JSON.parse(screen.getByTestId('model-modal').getAttribute('data-params')!)).toEqual({ temperature: 0.3 });
    expect(JSON.parse(screen.getByTestId('model-modal').getAttribute('data-settings')!)).toMatchObject({ custom: 'agent-level' });
    fireEvent.click(screen.getByText('mp-close'));
    expect(modelInput().value).toBe('model-a');
    fireEvent.click(modelInput());
    fireEvent.click(screen.getByText('mp-save-empty'));
    expect(modelInput().value).toBe('x');
    fireEvent.click(modelInput());
    expect(screen.getByTestId('model-modal').getAttribute('data-params')).toBe('null');
  });

  it('тонкая настройка модели сохраняется как настройки агента', async () => {
    await renderPanel();
    fireEvent.click(modelInput());
    expect(JSON.parse(screen.getByTestId('model-modal').getAttribute('data-settings')!)).toMatchObject({ temperature: 0.1 });
    fireEvent.click(screen.getByText('mp-settings'));
    fireEvent.click(screen.getByText('mp-close'));
    fireEvent.click(modelInput());
    expect(JSON.parse(screen.getByTestId('model-modal').getAttribute('data-settings')!)).toEqual({ custom: 1 });
  });

  it('зритель: панель модели в режиме чтения, сохранение не меняет модель', async () => {
    currentAgents[1].config = { model: 'llm-svc://h/viewer-model' };
    await renderPanel();
    await selectAgent('Viewer Agent');
    fireEvent.click(modelInput());
    expect(screen.getByTestId('model-modal').getAttribute('data-readonly')).toBe('true');
    fireEvent.click(screen.getByText('mp-save'));
    expect(screen.queryByTestId('model-modal')).toBeNull();
    expect(modelInput().value).toBe('viewer-model');
  });

});

describe('AgentConstructorPanel: прочее', () => {
  it('смена цвета сайдбара и тёмная тема не ломают рендер', async () => {
    await renderPanel({ dark: true });
    act(() => {
      window.dispatchEvent(new Event('sidebarColorChanged'));
    });
    expect(agentNameInput()).toBeTruthy();
  });

  it('светлая панель (белый цвет сайдбара) рендерится без ошибок', async () => {
    localStorage.setItem('sidebar_panel_color', '#ffffff');
    await renderPanel();
    expect(agentNameInput()).toBeTruthy();
  });
});
