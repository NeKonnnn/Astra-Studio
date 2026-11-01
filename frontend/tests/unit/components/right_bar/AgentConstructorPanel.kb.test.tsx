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
  getAuthFetchHeaders: () => ({ Authorization: 'Bearer tok' }),
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
  ...jest.requireActual('@/utils/cloneAgentDeep'),
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
  ...jest.requireActual('@/constants/AgentTags'),
  fetchAgentTags: (...a: unknown[]) => mockFetchTags(...a),
  invalidateAgentTagsCache: () => mockInvalidateTags(),
  subscribeAgentTagsCache: () => () => undefined,
}));
jest.mock('@/agents/agentsConfig', () => ({
  ...jest.requireActual('@/agents/agentsConfig'),
  fetchAgentChainConfig: (...a: unknown[]) => mockFetchChainCfg(...a),
}));
jest.mock('@/constants/ragEntityDefaults', () => ({
  ...jest.requireActual('@/constants/ragEntityDefaults'),
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
  ...jest.requireActual('@/components/right_bar/AgentTagsField'),
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
  ...jest.requireActual('@/agents/subagents/AgentSubagentsEditor'),
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
    config: FULL_CONFIG, tags: [{ id: 1, name: 'Fin' }], author_id: 'u', author_name: 'U',
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
    id: 4, name: 'Bare Agent', system_prompt: 'Голый агент промпт', author_id: 'u', author_name: 'U',
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

const lastCallOf = (method: string, path: string) => {
  const calls = callsTo(method, path);
  return calls[calls.length - 1];
};

const fileInput = () => document.querySelector('input[type="file"]') as HTMLInputElement;
const pickFiles = (files: File[]) => fireEvent.change(fileInput(), { target: { files } });
const pdf = (name = 'new.pdf') => new File(['x'], name, { type: 'application/pdf' });
const closeIcons = () =>
  Array.from(document.querySelectorAll('button')).filter((b) => b.querySelector('[data-testid="CloseIcon"]'));

async function openOwnerKb() {
  routes['PUT /api/agents/1'] = { body: {} };
  await renderPanel();
  await selectAgent('Owner Agent');
}

const kbCheckbox = () =>
  screen.getByText('Прикрепить базы знаний').closest('label')!.querySelector('input[type="checkbox"]') as HTMLInputElement;

// Per-agent загрузка документов (legacy RAG) удалена: агент работает только с именованными
// базами знаний (config.knowledge_bases_enabled / config.knowledge_base_ids).
describe('AgentConstructorPanel: базы знаний агента (KB-only UI)', () => {
  it('легаси-интерфейс загрузки файлов агента не отображается', async () => {
    currentAgents[0].config = { ...FULL_CONFIG, kb_document_ids: [11, 12] };
    routes['GET /api/agents/1'] = { body: currentAgents[0] };
    await openOwnerKb();
    expect(screen.queryByRole('button', { name: 'Добавить файлы' })).toBeNull();
    expect(screen.queryByLabelText('Искать по файлам агента')).toBeNull();
    expect(screen.queryByTestId('kb-search')).toBeNull();
    expect(document.querySelector('input[type="file"]')).toBeNull();
    expect(screen.queryByText('a.pdf')).toBeNull();
  });

  it('показывает секцию «Базы знаний»; чекбокс выключен, если БЗ не прикреплены', async () => {
    await openOwnerKb();
    expect(screen.getAllByText('Базы знаний').length).toBeGreaterThan(0);
    expect(kbCheckbox().checked).toBe(false);
  });

  it('прикреплённые БЗ из карточки агента включают чекбокс и попадают в сохраняемый config', async () => {
    currentAgents[0].config = {
      ...FULL_CONFIG,
      knowledge_bases_enabled: true,
      knowledge_base_ids: [5, 6],
    };
    routes['GET /api/agents/1'] = { body: currentAgents[0] };
    await openOwnerKb();
    expect(kbCheckbox().checked).toBe(true);
    fireEvent.click(saveBtn());
    await flush();
    const body = bodyOf(lastCallOf('PUT', '/api/agents/1'));
    expect(body.config.knowledge_bases_enabled).toBe(true);
    expect(body.config.knowledge_base_ids).toEqual([5, 6]);
  });

  it('выключение чекбокса сбрасывает список БЗ в сохраняемом config', async () => {
    currentAgents[0].config = {
      ...FULL_CONFIG,
      knowledge_bases_enabled: true,
      knowledge_base_ids: [5, 6],
    };
    routes['GET /api/agents/1'] = { body: currentAgents[0] };
    await openOwnerKb();
    fireEvent.click(kbCheckbox());
    expect(kbCheckbox().checked).toBe(false);
    fireEvent.click(saveBtn());
    await flush();
    const body = bodyOf(lastCallOf('PUT', '/api/agents/1'));
    expect(body.config.knowledge_bases_enabled).toBe(false);
    expect(body.config.knowledge_base_ids).toEqual([]);
  });

  it('если knowledge_bases_enabled не задан, но БЗ есть — режим считается включённым', async () => {
    currentAgents[0].config = { ...FULL_CONFIG, knowledge_base_ids: [7] };
    routes['GET /api/agents/1'] = { body: currentAgents[0] };
    await openOwnerKb();
    expect(kbCheckbox().checked).toBe(true);
  });
});
