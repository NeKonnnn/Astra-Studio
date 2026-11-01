import React from 'react';
import { render, screen, fireEvent, waitFor, act, within } from '@testing-library/react';

const mockShowNotification = jest.fn();
const mockNotifyReindexStarted = jest.fn();
const mockUseAuth = jest.fn();
const mockSaveEntity = jest.fn();
const mockDispatchApplied = jest.fn();
const mockFetchDefaults = jest.fn();

jest.mock('@/contexts/AppContext', () => ({
  useAppActions: () => ({ showNotification: mockShowNotification }),
}));
jest.mock('@/contexts/RagReindexStatusContext', () => ({
  useRagReindexStatus: () => ({ notifyReindexStarted: mockNotifyReindexStarted }),
}));
jest.mock('@/contexts/AuthContext', () => ({
  useAuth: () => mockUseAuth(),
}));
jest.mock('@/config/api', () => ({
  authFetch: (...a: unknown[]) => (require('@tests/unit/helpers/authFetchMock').authFetchMock as (...x: unknown[]) => unknown)(...a),
  getApiUrl: (p: string) => `http://api.test${p}`,
  getAuthFetchHeaders: (extra: Record<string, string> = {}) => ({ Authorization: 'Bearer t', ...extra }),
}));
jest.mock('@/constants/ragEntityDefaults', () => ({
  fetchRagEntityDefaults: (scope: string) => mockFetchDefaults(scope),
  resolveRagEmbeddingModelPath: (p: string | null | undefined) => (p && p.trim()) || 'env/embedding',
  resolveRagRerankerModelPath: (p: string | null | undefined) => (p && p.trim()) || 'env/reranker',
}));
jest.mock('@/utils/entityRagSettings', () => ({
  saveEntityRagSettings: (args: any) => mockSaveEntity(args),
}));
jest.mock('@/constants/ragEntityEvents', () => ({
  dispatchRagEntitySettingsApplied: (detail: any) => mockDispatchApplied(detail),
}));
jest.mock('@/components/settings/MemoryRagLibrarySection', () => ({
  __esModule: true,
  default: ({ variant }: { variant: string }) => <div data-testid="memory-section" data-variant={variant} />,
}));
jest.mock('@/components/RagModelSelector', () => ({
  __esModule: true,
  default: ({ kind, scope, disabled, preferredPath, onModelSelect, label }: any) => (
    <div data-testid={`selector-${kind}`} data-scope={scope} data-disabled={String(Boolean(disabled))} data-preferred={preferredPath ?? ''}>
      {label}
      <button onClick={() => onModelSelect(`new/${kind}.gguf`)}>pick-{kind}</button>
    </div>
  ),
}));

import RAGSettings from '@/components/settings/RAGSettings';

type Handler = (init?: RequestInit) => any;
const fetchMock = jest.fn();
let routes: Record<string, Handler>;

const ok = (data: unknown = {}) => ({
  ok: true,
  status: 200,
  json: async () => data,
  text: async () => JSON.stringify(data),
});
const fail = (status = 500, text = '') => ({
  ok: false,
  status,
  json: async () => ({}),
  text: async () => text,
});

function installFetch() {
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
    const full = String(url).replace('http://api.test', '');
    const method = (init && init.method) || 'GET';
    const key = `${method} ${full}`;
    const handler = routes[key] ?? routes[`${method} ${full.split('?')[0]}`];
    if (!handler) throw new Error(`unrouted fetch: ${key}`);
    return handler(init);
  });
}
const calls = (method: string, startsWith: string) =>
  fetchMock.mock.calls.filter(
    ([url, init]) =>
      String(url).replace('http://api.test', '').startsWith(startsWith) && ((init && init.method) || 'GET') === method,
  );
const bodyOf = (call: any[]) => JSON.parse(call[1].body as string);

const settle = async () => {
  await act(async () => {
    for (let i = 0; i < 30; i += 1) await Promise.resolve();
  });
};

const SERVER = {
  strategy: 'vector',
  agentic_rag_enabled: false,
  rag_query_fix_typos: true,
  rag_multi_query_enabled: true,
  rag_hyde_enabled: true,
  rag_chat_top_k: 8,
  rag_chunking_strategy: 'markdown',
  rag_chunk_overlap: 50,
  rag_chunk_size: 1500,
  rag_similarity_threshold: 0.25,
  rag_reranking_enabled: false,
  rag_rerank_top_n: 7,
  rag_system_prompt: 'srv prompt',
  can_edit: true,
};

async function renderRag(props: any = {}, server: any = SERVER, extraRoutes: Record<string, Handler> = {}) {
  routes = {
    'GET /api/rag/settings': () => ok(server),
    'PUT /api/rag/settings': () => ok({}),
    ...extraRoutes,
  };
  installFetch();
  const utils = render(<RAGSettings variant="panel" lockedScope="project" entityId="p1" entityName=" Мой проект " {...props} />);
  await settle();
  return utils;
}

const numInput = (re: RegExp) =>
  screen.getAllByLabelText(re).find((el) => el.tagName === 'INPUT') as HTMLInputElement;
const topK = () => numInput(/Количество чанков \(K\)/);
const chunkSize = () => numInput(/Размер чанка/);
const overlap = () => numInput(/Размер перекрытия/);
const threshold = () => numInput(/Порог схожести/);
const topN = () => numInput(/Top-N/);
const typeInto = (input: HTMLInputElement, value: string) => {
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value } });
  fireEvent.blur(input);
};
const switchOf = (label: string) =>
  (screen.getByText(label).closest('div')!.parentElement as HTMLElement).querySelector('input[type="checkbox"]') as HTMLInputElement;
const button = (name: RegExp | string) => screen.getByRole('button', { name }) as HTMLButtonElement;

beforeEach(() => {
  jest.clearAllMocks();
  fetchMock.mockReset();
  localStorage.clear();
  (global as any).fetch = fetchMock;
  mockUseAuth.mockReturnValue({ user: { user_id: ' Alice ', username: 'alice' } });
  mockFetchDefaults.mockResolvedValue({ embeddingPath: 'env/embedding', rerankerPath: 'env/reranker' });
  mockSaveEntity.mockResolvedValue({ ok: true });
  jest.spyOn(console, 'error').mockImplementation(() => undefined);
  jest.spyOn(console, 'debug').mockImplementation(() => undefined);
  jest.spyOn(window, 'confirm').mockReturnValue(true);
});
afterEach(() => {
  jest.restoreAllMocks();
});

describe('RAGSettings: page variant', () => {
  // memo: нет legacy «общей библиотеки» — страница отсылает к multi-KB в правой панели.
  it('shows only the knowledge-bases notice and does not load project settings', async () => {
    routes = {};
    installFetch();
    render(<RAGSettings />);
    await settle();

    expect(screen.getByText('Базы знаний')).toBeTruthy();
    expect(screen.getByText(/Старая «общая библиотека»/)).toBeTruthy();
    expect(screen.queryByTestId('memory-section')).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(mockFetchDefaults).not.toHaveBeenCalled();
  });
});

describe('RAGSettings: panel header', () => {
  it.each([
    ['project', 'Настройки РАГ для проектов'],
    ['agent', 'Настройки РАГ для агента'],
  ])('uses a default title for scope %s', async (scope, title) => {
    await renderRag({ lockedScope: scope, entityId: 5 });

    expect(screen.getByText(title)).toBeTruthy();
  });

  it('uses the explicit panel title and the back button calls onClose', async () => {
    const onClose = jest.fn();
    await renderRag({ panelTitle: 'Мой заголовок', onClose });

    expect(screen.getByText('Мой заголовок')).toBeTruthy();
    fireEvent.click(document.querySelector('[data-testid="ArrowBackIcon"]')!.closest('button') as HTMLElement);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('prefetches environment defaults for the scope in panel mode', async () => {
    await renderRag({ lockedScope: 'agent', entityId: 3 });

    expect(mockFetchDefaults).toHaveBeenCalledWith('agent');
  });

  it('passes the scope and disabled state to the model selectors', async () => {
    await renderRag({ lockedScope: 'agent', entityId: 3 });

    expect(screen.getByTestId('selector-embedding').getAttribute('data-scope')).toBe('agent');
    expect(screen.getByTestId('selector-reranker').getAttribute('data-disabled')).toBe('false');
  });

  it('works in the light theme too (isDarkMode=false)', async () => {
    await renderRag({ isDarkMode: false });

    expect(screen.getByText('Модели РАГ')).toBeTruthy();
  });
});

describe('RAGSettings: loading', () => {
  it('requests project settings with the entity id and auth headers', async () => {
    await renderRag();

    const [call] = calls('GET', '/api/rag/settings');
    expect(call[0]).toBe('http://api.test/api/rag/settings?scope=project&project_id=p1');
    expect(call[1]).toEqual({ headers: { Authorization: 'Bearer t' } });
  });

  it('requests agent settings by agent_id and encodes ids', async () => {
    await renderRag({ lockedScope: 'agent', entityId: 'a b' });

    expect(calls('GET', '/api/rag/settings')[0][0]).toBe(
      'http://api.test/api/rag/settings?scope=agent&agent_id=a%20b',
    );
  });

  it('omits the entity part when there is no entity id', async () => {
    await renderRag({ entityId: undefined });

    expect(calls('GET', '/api/rag/settings')[0][0]).toBe('http://api.test/api/rag/settings?scope=project');
  });

  it('resolves the id lazily through onResolveEntityId', async () => {
    const onResolve = jest.fn().mockResolvedValue('lazy-1');
    await renderRag({ entityId: undefined, onResolveEntityId: onResolve });

    expect(calls('GET', '/api/rag/settings')[0][0]).toContain('project_id=lazy-1');
  });

  it('applies server values to the form and caches strategies per lower-cased user id', async () => {
    await renderRag();

    expect(topK().value).toBe('8');
    expect(chunkSize().value).toBe('1500');
    expect(overlap().value).toBe('50');
    expect(threshold().value).toBe('0.25');
    expect(topN().value).toBe('7');
    expect((screen.getByLabelText('Стратегия поиска') as HTMLInputElement).value).toBe('Векторный');
    expect((screen.getByLabelText('Стратегия чанкования') as HTMLInputElement).value).toBe('По разметке');
    expect(switchOf('Agentic RAG').checked).toBe(false);
    expect(switchOf('Переранжирование').checked).toBe(false);
    expect(switchOf('Исправление опечаток в запросе').checked).toBe(true);
    expect(switchOf('Несколько формулировок (multi-query)').checked).toBe(true);
    expect(switchOf('HyDE (гипотетический ответ для поиска)').checked).toBe(true);
    expect(localStorage.getItem('rag_strategy:alice')).toBe('vector');
    expect(localStorage.getItem('rag_chunking_strategy:alice')).toBe('markdown');
    expect(console.debug).toHaveBeenCalledWith(expect.stringContaining('проекта «Мой проект»'));
  });

  it('falls back to username for the cache key and to a global key without user', async () => {
    mockUseAuth.mockReturnValue({ user: { username: 'Bob' } });
    const { unmount } = await renderRag();
    expect(localStorage.getItem('rag_strategy:bob')).toBe('vector');
    unmount();

    mockUseAuth.mockReturnValue({ user: null });
    await renderRag();
    expect(localStorage.getItem('rag_strategy')).toBe('vector');
  });

  it('clamps out-of-range server values', async () => {
    await renderRag({}, {
      ...SERVER,
      rag_chat_top_k: 500,
      rag_chunk_overlap: 99999,
      rag_chunk_size: 10,
      rag_similarity_threshold: 7,
      rag_rerank_top_n: -3,
      rag_reranking_enabled: true,
    });

    expect(topK().value).toBe('64');
    expect(overlap().value).toBe('2000');
    expect(chunkSize().value).toBe('200');
    expect(threshold().value).toBe('1');
    expect(topN().value).toBe('1');
  });

  it('clamps a small threshold and rounds fractional integers from the server', async () => {
    await renderRag({}, { ...SERVER, rag_similarity_threshold: -1, rag_chat_top_k: 7.6, rag_chunk_size: 1000.4 });

    expect(threshold().value).toBe('0');
    expect(topK().value).toBe('8');
    expect(chunkSize().value).toBe('1000');
  });

  it('ignores values of the wrong type and keeps the initial form', async () => {
    await renderRag({}, {
      strategy: '',
      agentic_rag_enabled: 'yes',
      rag_chat_top_k: 'many',
      rag_chunk_size: NaN,
      rag_chunk_overlap: null,
      rag_similarity_threshold: '0.5',
      rag_rerank_top_n: Infinity,
      rag_chunking_strategy: 5,
      rag_system_prompt: 5,
    });

    expect(topK().value).toBe('12');
    expect(chunkSize().value).toBe('4000');
    expect(overlap().value).toBe('100');
    expect(threshold().value).toBe('0');
    expect(topN().value).toBe('12');
    expect((screen.getByLabelText('Стратегия поиска') as HTMLInputElement).value).toBe('Гибридный');
    expect((screen.getByLabelText('Стратегия чанкования') as HTMLInputElement).value).toBe('Фиксированное');
    expect(switchOf('Agentic RAG').checked).toBe(true);
  });

  it.each([
    ['reranking', 'Гибридный'],
    ['standard', 'Векторный'],
    ['  GRAPH ', 'Графовый'],
    ['lexical', 'Лексический'],
    ['auto', 'Автоматический выбор'],
    ['nonsense', 'Гибридный'],
  ])('normalises the server strategy %j to "%s"', async (raw, label) => {
    await renderRag({}, { strategy: raw });

    expect((screen.getByLabelText('Стратегия поиска') as HTMLInputElement).value).toBe(label);
  });

  it.each([
    ['hierarchical', 'Иерархическое'],
    ['SEMANTIC', 'Семантическое'],
    ['separators', 'По разделителям'],
    ['weird', 'Фиксированное'],
  ])('normalises the chunking strategy %j to "%s"', async (raw, label) => {
    await renderRag({}, { rag_chunking_strategy: raw });

    expect((screen.getByLabelText('Стратегия чанкования') as HTMLInputElement).value).toBe(label);
  });

  it('keeps the form and does not report an error on 404', async () => {
    await renderRag({}, SERVER, { 'GET /api/rag/settings': () => fail(404) });

    expect(topK().value).toBe('12');
    expect(console.error).not.toHaveBeenCalled();
    expect(button('Сохранить настройки').disabled).toBe(false);
  });

  it('logs and re-enables the form when loading throws', async () => {
    await renderRag({}, SERVER, {
      'GET /api/rag/settings': () => {
        throw new Error('offline');
      },
    });

    expect(console.error).toHaveBeenCalledWith('Ошибка загрузки настроек RAG:', expect.any(Error));
    expect(button('Сохранить настройки').disabled).toBe(false);
  });

  it('disables the form while the load is in flight', async () => {
    let resolve!: (v: any) => void;
    routes = { 'GET /api/rag/settings': () => new Promise((r) => (resolve = r)) };
    installFetch();
    render(<RAGSettings variant="panel" lockedScope="project" entityId="p1" />);
    await settle();

    expect(button('Сохранить настройки').disabled).toBe(true);
    expect(topK().disabled).toBe(true);

    await act(async () => {
      resolve(ok(SERVER));
    });
    expect(button('Сохранить настройки').disabled).toBe(false);
  });

  it('reloads when the scope changes or the user changes', async () => {
    const { rerender } = await renderRag();
    expect(calls('GET', '/api/rag/settings')).toHaveLength(1);

    rerender(<RAGSettings variant="panel" lockedScope="agent" entityId="p1" />);
    await settle();

    expect(calls('GET', '/api/rag/settings')).toHaveLength(2);
    expect(calls('GET', '/api/rag/settings')[1][0]).toContain('scope=agent');
  });
});

describe('RAGSettings: permissions', () => {
  it('locks the form and explains who may edit when the server says can_edit=false', async () => {
    await renderRag({}, { ...SERVER, can_edit: false });

    expect(topK().disabled).toBe(true);
    expect(chunkSize().disabled).toBe(true);
    expect(switchOf('Agentic RAG').disabled).toBe(true);
    expect(screen.getByTestId('selector-embedding').getAttribute('data-disabled')).toBe('true');
    expect(screen.queryByRole('button', { name: 'Сохранить настройки' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Восстановить настройки' })).toBeNull();
    expect(screen.getByText(/Настройки задаёт владелец/).textContent).toContain('этот проект');
  });

  it('mentions the agent for agent scope', async () => {
    await renderRag({ lockedScope: 'agent', entityId: 4 }, { ...SERVER, can_edit: false });

    expect(screen.getByText(/Настройки задаёт владелец/).textContent).toContain('этот агент');
  });

  it('treats a missing can_edit as editable', async () => {
    const { can_edit, ...withoutFlag } = SERVER;
    await renderRag({}, withoutFlag);

    expect(topK().disabled).toBe(false);
  });

  it('readOnly forces the view mode even if the server allows editing', async () => {
    await renderRag({ readOnly: true });

    expect(topK().disabled).toBe(true);
    expect(screen.queryByRole('button', { name: 'Сохранить настройки' })).toBeNull();
  });

  it('switches to the view mode when the parent turns readOnly on', async () => {
    const { rerender } = await renderRag();
    expect(topK().disabled).toBe(false);

    rerender(<RAGSettings variant="panel" lockedScope="project" entityId="p1" readOnly />);
    await settle();

    expect(topK().disabled).toBe(true);
    expect(screen.queryByRole('button', { name: 'Сохранить настройки' })).toBeNull();
  });

  it('draft mode ignores can_edit=false from the server (the entity may not exist yet)', async () => {
    await renderRag({ draft: true }, { ...SERVER, can_edit: false });

    expect(topK().disabled).toBe(false);
    expect(button('Применить настройки')).toBeTruthy();
  });

  it('a disabled form cannot be edited through the dropdown inputs', async () => {
    await renderRag({}, { ...SERVER, can_edit: false });
    fireEvent.change(topK(), { target: { value: '30' } });

    expect(topK().value).toBe('8');
  });
});

describe('RAGSettings: editing the form', () => {
  it('selects a search strategy from the dropdown', async () => {
    await renderRag();
    fireEvent.click(screen.getByLabelText('Стратегия поиска'));
    fireEvent.click(within(document.querySelector('.MuiPopover-root') as HTMLElement).getByText('Графовый'));

    await waitFor(() =>
      expect((screen.getByLabelText('Стратегия поиска') as HTMLInputElement).value).toBe('Графовый'),
    );
    await waitFor(() => expect(document.querySelector('.MuiPopover-root')).toBeNull());
  });

  it.each([
    ['Автоматический выбор'],
    ['Векторный'],
    ['Лексический'],
    ['Гибридный'],
    ['Графовый'],
  ])('can select the strategy "%s"', async (label) => {
    await renderRag({}, { strategy: label === 'Гибридный' ? 'auto' : 'hybrid' });
    fireEvent.click(screen.getByLabelText('Стратегия поиска'));
    fireEvent.click(within(document.querySelector('.MuiPopover-root') as HTMLElement).getByText(label));

    await waitFor(() => expect((screen.getByLabelText('Стратегия поиска') as HTMLInputElement).value).toBe(label));
  });

  it.each([
    ['Иерархическое'],
    ['Фиксированное'],
    ['По разметке'],
    ['По разделителям'],
    ['Семантическое'],
  ])('can select the chunking "%s"', async (label) => {
    await renderRag({}, { rag_chunking_strategy: label === 'Фиксированное' ? 'markdown' : 'fixed' });
    fireEvent.click(screen.getByLabelText('Стратегия чанкования'));
    fireEvent.click(within(document.querySelector('.MuiPopover-root') as HTMLElement).getByText(label));

    await waitFor(() => expect((screen.getByLabelText('Стратегия чанкования') as HTMLInputElement).value).toBe(label));
  });

  it('closes dropdown popovers on backdrop click without changing the value', async () => {
    await renderRag();
    fireEvent.click(screen.getByLabelText('Стратегия поиска'));
    fireEvent.click(document.querySelector('.MuiBackdrop-root') as HTMLElement);
    await waitFor(() => expect(document.querySelector('.MuiPopover-root')).toBeNull());
    fireEvent.click(screen.getByLabelText('Стратегия чанкования'));
    fireEvent.click(document.querySelector('.MuiBackdrop-root') as HTMLElement);
    await waitFor(() => expect(document.querySelector('.MuiPopover-root')).toBeNull());

    expect((screen.getByLabelText('Стратегия поиска') as HTMLInputElement).value).toBe('Векторный');
    expect((screen.getByLabelText('Стратегия чанкования') as HTMLInputElement).value).toBe('По разметке');
  });

  it('ignores clicks on the dropdowns while loading', async () => {
    let resolve!: (v: any) => void;
    routes = { 'GET /api/rag/settings': () => new Promise((r) => (resolve = r)) };
    installFetch();
    render(<RAGSettings variant="panel" lockedScope="project" entityId="p1" />);
    await settle();

    fireEvent.click(screen.getByLabelText('Стратегия поиска'));
    fireEvent.click(screen.getByLabelText('Стратегия чанкования'));

    expect(document.querySelector('.MuiPopover-root')).toBeNull();
    await act(async () => {
      resolve(ok(SERVER));
    });
  });

  it.each([
    ['K', () => topK(), '999', '64'],
    ['K', () => topK(), '0', '1'],
    ['K', () => topK(), '', '5'],
    ['chunk size', () => chunkSize(), '100', '200'],
    ['chunk size', () => chunkSize(), '9000', '8000'],
    ['chunk size', () => chunkSize(), '', '1000'],
    ['overlap', () => overlap(), '2500', '2000'],
    ['overlap', () => overlap(), '-5', '0'],
    ['overlap', () => overlap(), '', '200'],
    ['threshold', () => threshold(), '1.5', '1'],
    ['threshold', () => threshold(), '0,5', '0.5'],
    ['threshold', () => threshold(), '0.123456', '0.1235'],
    ['threshold', () => threshold(), '', '0'],
  ])('clamps %s typed as %j to %s', async (_name, field, typed, expected) => {
    await renderRag();

    typeInto(field() as HTMLInputElement, typed);

    expect((field() as HTMLInputElement).value).toBe(expected);
  });

  it('(known issue) a cleared chunk size/overlap/K falls back to 1000/200/5, not to the initial 4000/100/12 nor to the values shown in the help', async () => {
    // Значения по умолчанию в подсказках (K=5, размер=1000, перекрытие=200) расходятся
    // с начальным состоянием формы (12 / 4000 / 100): после очистки поля форма «прыгает» на другое значение.
    await renderRag({}, {});

    typeInto(topK(), '');
    typeInto(chunkSize(), '');
    typeInto(overlap(), '');

    expect([topK().value, chunkSize().value, overlap().value]).toEqual(['5', '1000', '200']);
  });

  it('refuses to save an overlap larger than the chunk size (BUG-FE-38)', async () => {
    await renderRag();
    typeInto(chunkSize(), '300');
    typeInto(overlap(), '1500');
    routes['PUT /api/rag/settings'] = () => ok({});

    fireEvent.click(button('Сохранить настройки'));
    await settle();

    expect(calls('PUT', '/api/rag/settings')).toHaveLength(0);
    expect(mockShowNotification).toHaveBeenCalledWith('error', expect.stringContaining('Перекрытие чанков'));
    expect(mockShowNotification).not.toHaveBeenCalledWith('success', 'Настройки RAG сохранены');
  });

  it('refuses to save an overlap equal to the chunk size (BUG-FE-38)', async () => {
    await renderRag();
    typeInto(chunkSize(), '300');
    typeInto(overlap(), '300');
    routes['PUT /api/rag/settings'] = () => ok({});

    fireEvent.click(button('Сохранить настройки'));
    await settle();

    expect(calls('PUT', '/api/rag/settings')).toHaveLength(0);
  });

  it('saves an overlap smaller than the chunk size (BUG-FE-38)', async () => {
    await renderRag();
    typeInto(chunkSize(), '300');
    typeInto(overlap(), '100');
    routes['PUT /api/rag/settings'] = () => ok({});

    fireEvent.click(button('Сохранить настройки'));
    await settle();

    const body = bodyOf(calls('PUT', '/api/rag/settings')[0]);
    expect(body.rag_chunk_size).toBe(300);
    expect(body.rag_chunk_overlap).toBe(100);
    expect(mockShowNotification).toHaveBeenCalledWith('success', 'Настройки RAG сохранены');
  });

  it('Top-N is disabled while reranking is off and enabled again after switching it on', async () => {
    await renderRag();
    expect(topN().disabled).toBe(true);

    fireEvent.click(switchOf('Переранжирование'));

    expect(topN().disabled).toBe(false);
    typeInto(topN(), '100');
    expect(topN().value).toBe('64');
  });

  it('the switches change their state', async () => {
    await renderRag({}, {});
    for (const label of [
      'Agentic RAG',
      'Исправление опечаток в запросе',
      'Несколько формулировок (multi-query)',
      'HyDE (гипотетический ответ для поиска)',
    ]) {
      const before = switchOf(label).checked;
      fireEvent.click(switchOf(label));
      expect(switchOf(label).checked).toBe(!before);
    }
  });

  it('picking models only stores a draft and passes it back to the selectors', async () => {
    await renderRag();

    fireEvent.click(screen.getByText('pick-embedding'));
    fireEvent.click(screen.getByText('pick-reranker'));

    expect(screen.getByTestId('selector-embedding').getAttribute('data-preferred')).toBe('new/embedding.gguf');
    expect(screen.getByTestId('selector-reranker').getAttribute('data-preferred')).toBe('new/reranker.gguf');
    expect(calls('POST', '/api/rag/models/select')).toHaveLength(0);
  });
});

describe('RAGSettings: saving (non-draft)', () => {
  it('PUTs the full form with scope, entity body and trimmed prompt', async () => {
    await renderRag({ entityId: 'p1', entityName: ' Мой проект ' });
    routes['PUT /api/rag/settings'] = () => ok({});

    fireEvent.click(button('Сохранить настройки'));
    await settle();

    const [put] = calls('PUT', '/api/rag/settings');
    expect(put[1]!.headers).toEqual({ Authorization: 'Bearer t', 'Content-Type': 'application/json' });
    expect(bodyOf(put)).toEqual({
      scope: 'project',
      project_id: 'p1',
      entity_name: 'Мой проект',
      strategy: 'vector',
      agentic_rag_enabled: false,
      rag_query_fix_typos: true,
      rag_multi_query_enabled: true,
      rag_hyde_enabled: true,
      rag_chat_top_k: 8,
      rag_chunking_strategy: 'markdown',
      rag_chunk_size: 1500,
      rag_chunk_overlap: 50,
      rag_similarity_threshold: 0.25,
      rag_reranking_enabled: false,
      rag_rerank_top_n: 7,
      rag_system_prompt: 'srv prompt',
    });
    expect(mockShowNotification).toHaveBeenCalledWith('success', 'Настройки RAG сохранены');
    expect(mockDispatchApplied).toHaveBeenCalledWith({ scope: 'project', entityId: 'p1', entityName: ' Мой проект ' });
    expect(mockNotifyReindexStarted).not.toHaveBeenCalled();
    expect(console.debug).toHaveBeenCalledWith(expect.stringContaining('Настройки сохранены'));
  });

  it('an agent entity is sent as a numeric agent_id', async () => {
    await renderRag({ lockedScope: 'agent', entityId: '7', entityName: '' });

    fireEvent.click(button('Сохранить настройки'));
    await settle();

    const body = bodyOf(calls('PUT', '/api/rag/settings')[0]);
    expect(body.scope).toBe('agent');
    expect(body.agent_id).toBe(7);
    expect(body).not.toHaveProperty('entity_name');
    expect(body).not.toHaveProperty('project_id');
  });

  it('prefers the entity instructions over the stored RAG prompt', async () => {
    await renderRag({ entityInstructionsPrompt: '  Инструкции сущности  ' });

    fireEvent.click(button('Сохранить настройки'));
    await settle();

    expect(bodyOf(calls('PUT', '/api/rag/settings')[0]).rag_system_prompt).toBe('Инструкции сущности');
  });

  it('sends an empty prompt when neither the entity nor the server has one', async () => {
    await renderRag({}, { ...SERVER, rag_system_prompt: undefined });

    fireEvent.click(button('Сохранить настройки'));
    await settle();

    expect(bodyOf(calls('PUT', '/api/rag/settings')[0]).rag_system_prompt).toBe('');
  });

  it('announces a re-index and refreshes the status when the server reports one', async () => {
    await renderRag();
    routes['PUT /api/rag/settings'] = () => ok({ reindexed: true });

    fireEvent.click(button('Сохранить настройки'));
    await settle();

    expect(mockNotifyReindexStarted).toHaveBeenCalledTimes(1);
  });

  it('copes with a non-JSON success body', async () => {
    await renderRag();
    routes['PUT /api/rag/settings'] = () => ({
      ok: true,
      status: 200,
      json: async () => {
        throw new Error('not json');
      },
      text: async () => '',
    });

    fireEvent.click(button('Сохранить настройки'));
    await settle();

    expect(mockShowNotification).toHaveBeenCalledWith('success', 'Настройки RAG сохранены');
  });

  it('shows the server status and details on failure and keeps the form', async () => {
    await renderRag();
    routes['PUT /api/rag/settings'] = () => fail(422, 'bad size');

    fireEvent.click(button('Сохранить настройки'));
    await settle();

    expect(mockShowNotification).toHaveBeenCalledWith('error', 'Ошибка сохранения настроек RAG: 422 — bad size');
    expect(mockDispatchApplied).not.toHaveBeenCalled();
    expect(topK().value).toBe('8');
    expect(button('Сохранить настройки').disabled).toBe(false);
  });

  it('omits the details suffix when the failure body is empty or unreadable', async () => {
    await renderRag();
    routes['PUT /api/rag/settings'] = () => ({
      ok: false,
      status: 500,
      json: async () => ({}),
      text: async () => {
        throw new Error('stream');
      },
    });

    fireEvent.click(button('Сохранить настройки'));
    await settle();

    expect(mockShowNotification).toHaveBeenCalledWith('error', 'Ошибка сохранения настроек RAG: 500');
  });

  it('uses a generic message when a non-Error is thrown', async () => {
    await renderRag();
    routes['PUT /api/rag/settings'] = () => Promise.reject('boom');

    fireEvent.click(button('Сохранить настройки'));
    await settle();

    expect(mockShowNotification).toHaveBeenCalledWith('error', 'Не удалось сохранить настройки RAG');
  });

  it('shows "Сохранение…" and blocks the buttons while saving', async () => {
    await renderRag();
    let resolve!: (v: any) => void;
    routes['PUT /api/rag/settings'] = () => new Promise((r) => (resolve = r));

    fireEvent.click(button('Сохранить настройки'));
    await settle();

    expect(button('Сохранение…').disabled).toBe(true);
    expect(button('Восстановить настройки').disabled).toBe(true);
    expect(topK().disabled).toBe(true);
    await act(async () => {
      resolve(ok({}));
    });
    expect(button('Сохранить настройки').disabled).toBe(false);
  });

  it('refuses to save when the entity cannot be resolved', async () => {
    const onResolve = jest.fn().mockResolvedValue(null);
    await renderRag({ entityId: undefined, onResolveEntityId: onResolve });

    fireEvent.click(button('Сохранить настройки'));
    await settle();

    expect(mockShowNotification).toHaveBeenCalledWith(
      'error',
      'Не удалось определить проект или агента для сохранения настроек',
    );
    expect(calls('PUT', '/api/rag/settings')).toHaveLength(0);
  });

  it('without an entity or resolver the settings are saved without entity fields', async () => {
    await renderRag({ entityId: undefined });

    fireEvent.click(button('Сохранить настройки'));
    await settle();

    const body = bodyOf(calls('PUT', '/api/rag/settings')[0]);
    expect(body).not.toHaveProperty('project_id');
    expect(mockDispatchApplied).not.toHaveBeenCalled();
  });
});

describe('RAGSettings: applying models on save', () => {
  it('asks for confirmation before switching the embedding model and aborts when declined', async () => {
    (window.confirm as jest.Mock).mockReturnValue(false);
    await renderRag();
    fireEvent.click(screen.getByText('pick-embedding'));

    fireEvent.click(button('Сохранить настройки'));
    await settle();

    expect(window.confirm).toHaveBeenCalledTimes(1);
    expect(calls('POST', '/api/rag/models/select')).toHaveLength(0);
    expect(calls('PUT', '/api/rag/settings')).toHaveLength(0);
    expect(mockShowNotification).not.toHaveBeenCalled();
  });

  it('applies the embedding model after confirmation, announces re-indexing, then saves and clears the draft', async () => {
    await renderRag();
    routes['POST /api/rag/models/select'] = () => ok({ reindexed: true });
    fireEvent.click(screen.getByText('pick-embedding'));

    fireEvent.click(button('Сохранить настройки'));
    await settle();

    const [post] = calls('POST', '/api/rag/models/select');
    expect(post[1]!.headers).toEqual({ Authorization: 'Bearer t', 'Content-Type': 'application/json' });
    expect(bodyOf(post)).toEqual({
      model_type: 'embedding',
      model_path: 'new/embedding.gguf',
      scope: 'project',
      project_id: 'p1',
      entity_name: 'Мой проект',
    });
    expect(mockNotifyReindexStarted).toHaveBeenCalled();
    expect(mockShowNotification).toHaveBeenCalledWith(
      'success',
      'Модель загружена. Запущена переиндексация ваших документов (без Memory RAG).',
    );
    expect(calls('PUT', '/api/rag/settings')).toHaveLength(1);
    expect(screen.getByTestId('selector-embedding').getAttribute('data-preferred')).toBe('');
  });

  it('applies the reranker without a confirmation dialog and without re-index message when not re-indexed', async () => {
    await renderRag();
    routes['POST /api/rag/models/select'] = () => ok({});
    fireEvent.click(screen.getByText('pick-reranker'));

    fireEvent.click(button('Сохранить настройки'));
    await settle();

    expect(window.confirm).not.toHaveBeenCalled();
    expect(bodyOf(calls('POST', '/api/rag/models/select')[0]).model_type).toBe('reranker');
    expect(mockShowNotification).not.toHaveBeenCalledWith('success', expect.stringContaining('переиндексация'));
    expect(mockShowNotification).toHaveBeenCalledWith('success', 'Настройки RAG сохранены');
  });

  it('applies both models in order (embedding first)', async () => {
    await renderRag();
    routes['POST /api/rag/models/select'] = () => ok({});
    fireEvent.click(screen.getByText('pick-reranker'));
    fireEvent.click(screen.getByText('pick-embedding'));

    fireEvent.click(button('Сохранить настройки'));
    await settle();

    expect(calls('POST', '/api/rag/models/select').map((c) => bodyOf(c).model_type)).toEqual([
      'embedding',
      'reranker',
    ]);
  });

  it('stops after a declined embedding switch and does not apply the reranker', async () => {
    (window.confirm as jest.Mock).mockReturnValue(false);
    await renderRag();
    fireEvent.click(screen.getByText('pick-embedding'));
    fireEvent.click(screen.getByText('pick-reranker'));

    fireEvent.click(button('Сохранить настройки'));
    await settle();

    expect(calls('POST', '/api/rag/models/select')).toHaveLength(0);
  });

  it('a failed reranker switch after the embedding was applied aborts the settings save', async () => {
    await renderRag();
    routes['POST /api/rag/models/select'] = (init) =>
      JSON.parse(init!.body as string).model_type === 'embedding' ? ok({}) : fail(500);
    fireEvent.click(screen.getByText('pick-embedding'));
    fireEvent.click(screen.getByText('pick-reranker'));

    fireEvent.click(button('Сохранить настройки'));
    await settle();

    expect(calls('PUT', '/api/rag/settings')).toHaveLength(0);
    expect(mockShowNotification).toHaveBeenCalledWith('error', 'Не удалось применить модель reranker');
  });

  it.each([
    [{ ok: false, status: 400, json: async () => ({ detail: 'нет такой модели' }) }, 'нет такой модели'],
    [{ ok: false, status: 400, json: async () => ({ message: 'msg' }) }, 'msg'],
    [{ ok: false, status: 400, json: async () => ({}) }, 'Не удалось применить модель reranker'],
    [
      {
        ok: false,
        status: 500,
        json: async () => {
          throw new Error('not json');
        },
      },
      'Не удалось применить модель reranker',
    ],
  ])('surfaces model switch errors (%#)', async (response, message) => {
    await renderRag();
    routes['POST /api/rag/models/select'] = () => response;
    fireEvent.click(screen.getByText('pick-reranker'));

    fireEvent.click(button('Сохранить настройки'));
    await settle();

    expect(mockShowNotification).toHaveBeenCalledWith('error', message);
    expect(calls('PUT', '/api/rag/settings')).toHaveLength(0);
  });
});

describe('RAGSettings: draft mode', () => {
  const DRAFT = {
    strategy: 'graph',
    agentic_rag_enabled: false,
    rag_query_fix_typos: true,
    rag_multi_query_enabled: false,
    rag_hyde_enabled: true,
    rag_chat_top_k: 9,
    rag_chunking_strategy: 'semantic',
    rag_chunk_size: 900,
    rag_chunk_overlap: 90,
    rag_similarity_threshold: 0.3,
    rag_reranking_enabled: true,
    rag_rerank_top_n: 4,
    rag_embedding_model_path: 'draft/emb',
    rag_reranker_model_path: '',
  };

  it('restores an unsaved parent draft instead of the server values (no request at all)', async () => {
    routes = {};
    installFetch();
    render(<RAGSettings variant="panel" lockedScope="project" entityId="p1" draft draftValue={DRAFT} />);
    await settle();

    expect(fetchMock).not.toHaveBeenCalled();
    expect(topK().value).toBe('9');
    expect(chunkSize().value).toBe('900');
    expect((screen.getByLabelText('Стратегия поиска') as HTMLInputElement).value).toBe('Графовый');
    expect((screen.getByLabelText('Стратегия чанкования') as HTMLInputElement).value).toBe('Семантическое');
    expect(screen.getByTestId('selector-embedding').getAttribute('data-preferred')).toBe('draft/emb');
    expect(screen.getByTestId('selector-reranker').getAttribute('data-preferred')).toBe('env/reranker');
    expect(button('Применить настройки').disabled).toBe(false);
  });

  it('a read-only viewer sees the draft but cannot edit', async () => {
    routes = {};
    installFetch();
    render(<RAGSettings variant="panel" lockedScope="project" entityId="p1" draft draftValue={DRAFT} readOnly />);
    await settle();

    expect(topK().disabled).toBe(true);
    expect(screen.queryByRole('button', { name: 'Применить настройки' })).toBeNull();
  });

  it('loads server values and fills model drafts from the server or the environment defaults', async () => {
    await renderRag({ draft: true }, { ...SERVER, rag_embedding_model_path: ' srv/emb ', rag_reranker_model_path: '' });

    expect(screen.getByTestId('selector-embedding').getAttribute('data-preferred')).toBe('srv/emb');
    expect(screen.getByTestId('selector-reranker').getAttribute('data-preferred')).toBe('env/reranker');
    expect(screen.getByText(/«Применить» записывает настройки/)).toBeTruthy();
  });

  it('"Применить" without an entity id hands the draft to the parent and closes without any request', async () => {
    const onDraftChange = jest.fn();
    const onClose = jest.fn();
    await renderRag({ draft: true, entityId: undefined, onDraftChange, onClose });

    fireEvent.click(button('Применить настройки'));
    await settle();

    expect(onDraftChange).toHaveBeenCalledWith(
      expect.objectContaining({
        strategy: 'vector',
        rag_chat_top_k: 8,
        rag_chunk_size: 1500,
        rag_embedding_model_path: 'env/embedding',
        rag_reranker_model_path: 'env/reranker',
      }),
    );
    expect(mockSaveEntity).not.toHaveBeenCalled();
    expect(calls('PUT', '/api/rag/settings')).toHaveLength(0);
    expect(mockShowNotification).toHaveBeenCalledWith(
      'success',
      'Настройки РАГ запомнены. Они запишутся, когда вы сохраните проект или агента.',
    );
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem('rag_strategy:alice')).toBe('vector');
  });

  it('"Применить" with an existing entity writes through saveEntityRagSettings', async () => {
    const onDraftChange = jest.fn();
    await renderRag({ draft: true, onDraftChange, entityInstructionsPrompt: ' инстр ' });

    fireEvent.click(button('Применить настройки'));
    await settle();

    expect(mockSaveEntity).toHaveBeenCalledWith(
      expect.objectContaining({
        scope: 'project',
        entityId: 'p1',
        entityName: ' Мой проект ',
        instructions: 'инстр',
        draft: expect.objectContaining({ strategy: 'vector' }),
      }),
    );
    expect(mockShowNotification).toHaveBeenCalledWith('success', 'Настройки РАГ сохранены');
    expect(mockDispatchApplied).toHaveBeenCalledWith({ scope: 'project', entityId: 'p1', entityName: ' Мой проект ' });
  });

  it('falls back to the RAG prompt and to the entity id as the name', async () => {
    await renderRag({ draft: true, entityName: undefined });

    fireEvent.click(button('Применить настройки'));
    await settle();

    expect(mockSaveEntity).toHaveBeenCalledWith(
      expect.objectContaining({ entityName: 'p1', instructions: 'srv prompt' }),
    );
  });

  it('warns that the settings are only remembered when the server write fails', async () => {
    mockSaveEntity.mockResolvedValue({ ok: false, message: 'нет сущности' });
    const onClose = jest.fn();
    await renderRag({ draft: true, onClose });

    fireEvent.click(button('Применить настройки'));
    await settle();

    expect(mockShowNotification).toHaveBeenCalledWith(
      'warning',
      expect.stringContaining('(нет сущности)'),
    );
    expect(mockDispatchApplied).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  it('treats a failing id resolver as "no entity yet"', async () => {
    const onResolve = jest.fn().mockRejectedValue(new Error('no id'));
    await renderRag({ draft: true, entityId: undefined, onResolveEntityId: onResolve });

    fireEvent.click(button('Применить настройки'));
    await settle();

    expect(mockSaveEntity).not.toHaveBeenCalled();
    expect(mockShowNotification).toHaveBeenCalledWith('success', expect.stringContaining('запомнены'));
  });

  it('works without the optional callbacks', async () => {
    await renderRag({ draft: true, entityId: undefined });

    fireEvent.click(button('Применить настройки'));
    await settle();

    expect(mockShowNotification).toHaveBeenCalled();
  });
});

describe('RAGSettings: reset', () => {
  it('POSTs a reset for the entity, reloads the form and caches auto/hierarchical first', async () => {
    await renderRag();
    routes['POST /api/rag/settings/reset'] = () => ok({});
    routes['GET /api/rag/settings'] = () => ok({ strategy: 'auto', rag_chunking_strategy: 'hierarchical', can_edit: true });
    fireEvent.click(screen.getByText('pick-embedding'));

    fireEvent.click(button('Восстановить настройки'));
    await settle();

    const [post] = calls('POST', '/api/rag/settings/reset');
    expect(post[0]).toBe('http://api.test/api/rag/settings/reset?scope=project&project_id=p1');
    expect(post[1]).toEqual({ method: 'POST', headers: { Authorization: 'Bearer t' } });
    expect(mockShowNotification).toHaveBeenCalledWith('success', 'Настройки RAG восстановлены по умолчанию');
    expect((screen.getByLabelText('Стратегия поиска') as HTMLInputElement).value).toBe('Автоматический выбор');
    expect(localStorage.getItem('rag_strategy:alice')).toBe('auto');
    expect(screen.getByTestId('selector-embedding').getAttribute('data-preferred')).toBe('');
  });

  it('reports a failed reset and leaves the form untouched', async () => {
    await renderRag();
    routes['POST /api/rag/settings/reset'] = () => fail(500);

    fireEvent.click(button('Восстановить настройки'));
    await settle();

    expect(mockShowNotification).toHaveBeenCalledWith('error', 'Не удалось сбросить настройки RAG');
    expect(topK().value).toBe('8');
  });

  it('in draft mode loads the cluster defaults into the form without touching the entity', async () => {
    await renderRag({ draft: true }, SERVER);
    routes['GET /api/rag/settings'] = () =>
      ok({
        strategy: 'hybrid',
        agentic_rag_enabled: true,
        rag_chat_top_k: 0,
        rag_chunking_strategy: '',
        rag_chunk_size: 0,
        rag_chunk_overlap: 0,
        rag_similarity_threshold: 0,
        rag_reranking_enabled: true,
        rag_rerank_top_n: 0,
      });

    fireEvent.click(button('Восстановить настройки'));
    await settle();

    expect(calls('POST', '/api/rag/settings/reset')).toHaveLength(0);
    expect(calls('GET', '/api/rag/settings').slice(-1)[0][0]).toBe('http://api.test/api/rag/settings?scope=project');
    expect(mockShowNotification).toHaveBeenCalledWith('success', 'Настройки РАГ возвращены к значениям по умолчанию');
    expect(topK().value).toBe('12');
    expect(chunkSize().value).toBe('4000');
    expect(overlap().value).toBe('100');
    expect((screen.getByLabelText('Стратегия чанкования') as HTMLInputElement).value).toBe('Фиксированное');
    expect(screen.getByTestId('selector-embedding').getAttribute('data-preferred')).toBe('env/embedding');
  });

  it('in draft mode reports when the defaults cannot be fetched', async () => {
    await renderRag({ draft: true }, SERVER);
    routes['GET /api/rag/settings'] = () => fail(503);

    fireEvent.click(button('Восстановить настройки'));
    await settle();

    expect(mockShowNotification).toHaveBeenCalledWith('error', 'Не удалось получить настройки по умолчанию');
    expect(topK().value).toBe('8');
  });
});

describe('RAGSettings: tooltips describe the selected strategy', () => {
  it.each([
    ['auto', 'Автоматический выбор'],
    ['hybrid', 'Гибридный'],
    ['vector', 'Векторный'],
    ['lexical', 'Лексический'],
    ['graph', 'Графовый'],
  ])('renders help for %s', async (strategy, label) => {
    await renderRag({}, { strategy });
    fireEvent.mouseOver(screen.getByRole('button', { name: 'Справка: стратегия поиска' }));

    const tip = await screen.findByRole('tooltip');
    expect(tip.textContent).toContain(label);
    expect(tip.textContent).toMatch(/Выбирайте/);
  });

  it.each([
    ['hierarchical', 'Иерархическое'],
    ['fixed', 'Фиксированное'],
    ['markdown', 'По разметке'],
    ['separators', 'По разделителям'],
    ['semantic', 'Семантическое'],
  ])('renders help for chunking %s', async (chunking, label) => {
    await renderRag({}, { rag_chunking_strategy: chunking });
    fireEvent.mouseOver(screen.getByRole('button', { name: 'Справка: стратегия чанкования' }));

    const tip = await screen.findByRole('tooltip');
    expect(tip.textContent).toContain(label);
    expect(tip.textContent).toContain('Общая библиотека всегда режется своим чанкером');
  });

  it('help buttons next to numeric fields are inert', async () => {
    await renderRag();

    ['Справка: количество чанков', 'Справка: размер чанка', 'Справка: размер перекрытия', 'Справка: порог схожести', 'Справка: Top-N после реранкинга'].forEach(
      (name) => fireEvent.click(screen.getByRole('button', { name })),
    );

    expect(topK().value).toBe('8');
  });

  it('agent scope shows the agent-specific hints', async () => {
    await renderRag({ lockedScope: 'agent', entityId: 3 });
    fireEvent.mouseOver(screen.getByRole('button', { name: 'Справка: количество чанков' }));

    expect((await screen.findByRole('tooltip')).textContent).toContain('документов агента');
  });
});
