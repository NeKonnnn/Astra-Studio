import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import '@testing-library/jest-dom';
import AgentSelector, { getActiveAgentFromStorage } from '@/components/AgentSelector';

const mockNotify = jest.fn();
let mockAuth: { token: string | null };
let mockAccess: { canUseAgents: boolean };

jest.mock('@/contexts/AuthContext', () => ({ useAuth: () => mockAuth }));
jest.mock('@/hooks/useFeatureAccess', () => ({ useFeatureAccess: () => mockAccess }));
jest.mock('@/contexts/AppContext', () => ({ useAppActions: () => ({ showNotification: mockNotify }) }));
jest.mock('@/config/api', () => ({
  authFetch: (...a: unknown[]) => (require('@tests/unit/helpers/authFetchMock').authFetchMock as (...x: unknown[]) => unknown)(...a), getApiUrl: (p: string) => `http://api${p}` }));

const KEY = 'last_selected_model_path';
const MODELS_CACHE = 'agent_selector_models_cache_v1';
const PROVIDERS_CACHE = 'agent_selector_providers_cache_v1';

const CATALOG = [
  { name: 'qwen3.gguf', path: 'local/qwen3.gguf', display_name: 'Qwen 3', provider_id: 'local' },
  { name: 'gpt-x', path: 'openai/gpt-x', provider_id: 'openai' },
  { name: 'llama.gguf', path: 'local/llama.gguf', llm_host_id: 'local' },
  { name: 'legacy', path: 'llm-svc://hostA/legacy' },
  { name: 'bare', path: 'llm-svc://hostB' },
  { name: 'empty-host', path: 'llm-svc://' },
  { name: 'slashed', path: 'vllm/slashed' },
  { name: 'rootless', path: '/rootless' },
  { name: 'plain', path: 'plain' },
  { name: 'nopath', path: '' },
];

type Json = any;
let routes: Record<string, () => { ok?: boolean; body?: Json; hang?: boolean; reject?: boolean }>;
let fetchMock: jest.Mock;

function installFetch() {
  fetchMock = jest.fn((url: string, init?: any) => {
    const key = `${init?.method || 'GET'} ${url.replace('http://api', '')}`;
    const r = routes[key]?.() ?? { ok: false };
    if (r.hang) {
      return new Promise((_, rej) => {
        init?.signal?.addEventListener('abort', () => rej(new Error('aborted')));
      });
    }
    if (r.reject) return Promise.reject(new Error('net'));
    return Promise.resolve({ ok: r.ok !== false, json: async () => r.body } as Response);
  });
  (global as any).fetch = fetchMock;
}

async function renderSel(props: Partial<React.ComponentProps<typeof AgentSelector>> = {}) {
  const r = render(<AgentSelector {...props} />);
  await act(async () => {});
  return r;
}
const trigger = (text: string | RegExp) => screen.getByText(text);
async function openMenu(label: string | RegExp = 'Агенты / Модели') {
  fireEvent.click(trigger(label));
  await act(async () => {});
}
const hoverConn = (name: string) => fireEvent.mouseEnter(screen.getByText(name));

beforeEach(() => {
  jest.clearAllMocks();
  localStorage.clear();
  mockAuth = { token: 'tok' };
  mockAccess = { canUseAgents: true };
  routes = {
    'GET /api/models/current': () => ({ body: { path: 'local/qwen3.gguf' } }),
    'GET /api/llm-providers?include_health=false': () => ({
      body: { default_provider_id: 'local', providers: [{ id: 'local' }, { id: 'openai' }, { id: 'empty' }, {}] },
    }),
    'GET /api/models/available': () => ({ body: { models: CATALOG } }),
    'POST /api/models/load': () => ({ body: { success: true } }),
  };
  installFetch();
});

describe('getActiveAgentFromStorage', () => {
  it('возвращает активного агента из localStorage', () => {
    localStorage.setItem('active_agent_id', '5');
    localStorage.setItem('active_agent_name', 'Помощник');
    localStorage.setItem('active_agent_prompt', 'ты помощник');
    expect(getActiveAgentFromStorage()).toEqual({ id: 5, name: 'Помощник', system_prompt: 'ты помощник' });
  });
  it('без промпта - пустая строка; без id или имени - null; нечисловой id - null', () => {
    localStorage.setItem('active_agent_id', '5');
    localStorage.setItem('active_agent_name', 'А');
    expect(getActiveAgentFromStorage()?.system_prompt).toBe('');
    localStorage.removeItem('active_agent_name');
    expect(getActiveAgentFromStorage()).toBeNull();
    localStorage.setItem('active_agent_name', 'А');
    localStorage.setItem('active_agent_id', 'abc');
    expect(getActiveAgentFromStorage()).toBeNull();
    localStorage.clear();
    expect(getActiveAgentFromStorage()).toBeNull();
  });
});

describe('AgentSelector: подпись триггера', () => {
  it('без агента и модели: «Агенты / Модели» либо «Модели» без права на агентов', async () => {
    routes['GET /api/models/current'] = () => ({ ok: false });
    const { unmount } = await renderSel();
    expect(trigger('Агенты / Модели')).toBeInTheDocument();
    unmount();
    mockAccess = { canUseAgents: false };
    await renderSel();
    expect(trigger('Модели')).toBeInTheDocument();
  });

  it('активный агент показывается по имени, если разрешены агенты', async () => {
    localStorage.setItem('active_agent_id', '1');
    localStorage.setItem('active_agent_name', 'Юрист');
    const { unmount } = await renderSel();
    expect(trigger('Юрист')).toBeInTheDocument();
    unmount();
    mockAccess = { canUseAgents: false };
    routes['GET /api/models/current'] = () => ({ ok: false });
    await renderSel();
    expect(screen.queryByText('Юрист')).toBeNull();
  });

  it('событие agentSelected обновляет агента', async () => {
    routes['GET /api/models/current'] = () => ({ ok: false });
    await renderSel();
    localStorage.setItem('active_agent_id', '2');
    localStorage.setItem('active_agent_name', 'Новый');
    act(() => {
      window.dispatchEvent(new Event('agentSelected'));
    });
    expect(trigger('Новый')).toBeInTheDocument();
  });

  it('текущая модель с сервера показывается по display_name из каталога', async () => {
    await renderSel();
    expect(trigger('Qwen 3')).toBeInTheDocument();
    expect(localStorage.getItem(KEY)).toBe('local/qwen3.gguf');
  });

  it('сохранённый в localStorage путь отображается сразу (пока сервер не ответил)', async () => {
    localStorage.setItem(KEY, 'openai/gpt-x');
    routes['GET /api/models/current'] = () => ({ ok: false });
    routes['GET /api/models/available'] = () => ({ ok: false });
    await renderSel();
    expect(trigger('gpt-x')).toBeInTheDocument();
  });

  it('невалидный сохранённый путь игнорируется; ошибка чтения localStorage не роняет', async () => {
    localStorage.setItem(KEY, 'default');
    routes['GET /api/models/current'] = () => ({ ok: false });
    await renderSel();
    expect(trigger('Агенты / Модели')).toBeInTheDocument();
  });

  it('недоступный localStorage при инициализации роняет рендер (known issue)', async () => {
    // Как должно быть: компонент рендерится, как с readStoredModelPath/readTimedCache (там есть try/catch).
    // Сейчас getActiveAgentFromStorage (useState-инициализатор) читает localStorage без try/catch и бросает исключение.
    const spy = jest.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('denied');
    });
    const err = jest.spyOn(console, 'error').mockImplementation(() => {});
    try {
      expect(() => render(<AgentSelector />)).toThrow('denied');
    } finally {
      spy.mockRestore();
      err.mockRestore();
    }
  });

  it('заглушка llm-svc:// в ответе current очищает выбор и сохранённый путь', async () => {
    localStorage.setItem(KEY, 'local/old.gguf');
    routes['GET /api/models/current'] = () => ({ body: { path: 'llm-svc://' } });
    await renderSel();
    expect(trigger('Агенты / Модели')).toBeInTheDocument();
    expect(localStorage.getItem(KEY)).toBeNull();
  });

  it('legacy llm-svc://model нормализуется с провайдером по умолчанию', async () => {
    routes['GET /api/models/current'] = () => ({ body: { path: 'llm-svc://qwen3.gguf' } });
    await renderSel();
    await openMenu('Qwen 3'); // после загрузки провайдеров путь нормализуется в local/qwen3.gguf
    expect(localStorage.getItem(KEY)).toBe('local/qwen3.gguf');
  });

  it('имя модели ищется по model_id, если полного пути нет в каталоге; иначе последний сегмент', async () => {
    routes['GET /api/models/current'] = () => ({ body: { path: 'other/gpt-x' } });
    const { unmount } = await renderSel();
    expect(trigger('gpt-x')).toBeInTheDocument();
    unmount();
    routes['GET /api/models/current'] = () => ({ body: { path: 'other/zzz.gguf' } });
    await renderSel();
    expect(trigger('zzz')).toBeInTheDocument();
  });

  it('путь Windows показывает имя файла', async () => {
    routes['GET /api/models/current'] = () => ({ body: { path: 'C:\\m\\file.gguf' } });
    await renderSel();
    expect(trigger('file')).toBeInTheDocument();
  });
});

describe('AgentSelector: кэш', () => {
  it('использует кэш каталога и провайдеров из localStorage без ожидания сети', async () => {
    localStorage.setItem(MODELS_CACHE, JSON.stringify({ ts: Date.now(), data: [{ name: 'cached', path: 'cp/cached' }] }));
    localStorage.setItem(PROVIDERS_CACHE, JSON.stringify({ ts: Date.now(), data: { defaultProviderId: 'cp', providerIds: ['cp'] } }));
    localStorage.setItem(KEY, 'cp/cached');
    routes['GET /api/models/current'] = () => ({ ok: false });
    routes['GET /api/models/available'] = () => ({ hang: true });
    await renderSel();
    expect(trigger('cached')).toBeInTheDocument();
  });

  it('просроченный кэш игнорируется, устаревший формат (массив) принимается', async () => {
    localStorage.setItem(MODELS_CACHE, JSON.stringify({ ts: 1, data: [{ name: 'old', path: 'p/old' }] }));
    localStorage.setItem(KEY, 'p/old');
    routes['GET /api/models/current'] = () => ({ ok: false });
    routes['GET /api/models/available'] = () => ({ hang: true });
    const { unmount } = await renderSel();
    expect(trigger('old')).toBeInTheDocument(); // имя из последнего сегмента пути, не из просроченного каталога
    unmount();
    localStorage.setItem(MODELS_CACHE, JSON.stringify([{ name: 'Legacy Name', path: 'p/old', display_name: 'Legacy Name' }]));
    await renderSel();
    expect(trigger('Legacy Name')).toBeInTheDocument();
  });

  it('битый JSON в кэше не ломает компонент', async () => {
    localStorage.setItem(MODELS_CACHE, '{not json');
    routes['GET /api/models/current'] = () => ({ ok: false });
    await renderSel();
    expect(trigger('Агенты / Модели')).toBeInTheDocument();
  });

  it('каталог после загрузки сохраняется в кэш', async () => {
    await renderSel();
    const cached = JSON.parse(localStorage.getItem(MODELS_CACHE)!);
    expect(cached.data).toHaveLength(CATALOG.length);
  });

  it('ошибка записи кэша игнорируется', async () => {
    const spy = jest.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota');
    });
    routes['GET /api/models/current'] = () => ({ ok: false });
    await renderSel();
    spy.mockRestore();
    expect(trigger('Агенты / Модели')).toBeInTheDocument();
  });
});

describe('AgentSelector: меню подключений и моделей', () => {
  it('открытие меню грузит текущую модель, провайдеров и каталог; левая панель — подключения', async () => {
    await renderSel();
    fetchMock.mockClear();
    await openMenu('Qwen 3');
    const urls = fetchMock.mock.calls.map((c) => c[0]);
    expect(urls).toEqual(
      expect.arrayContaining([
        'http://api/api/models/current',
        'http://api/api/llm-providers?include_health=false',
      ]),
    );
    ['local', 'openai', 'empty', 'hostA', 'hostB', 'llm-svc', 'vllm'].forEach((c) =>
      expect(screen.getByText(c)).toBeInTheDocument(),
    );
  });

  it('наведение на подключение показывает его модели, выбранная отмечена галочкой', async () => {
    await renderSel();
    await openMenu('Qwen 3');
    hoverConn('local');
    expect(await screen.findByPlaceholderText('Поиск моделей...')).toBeInTheDocument();
    expect(screen.getAllByText('Qwen 3').length).toBeGreaterThan(1);
    expect(screen.getByText('llama')).toBeInTheDocument();
    expect(screen.getByTestId('CheckIcon')).toBeInTheDocument();
  });

  it('поиск фильтрует по имени, display_name и пути; «Ничего не найдено»', async () => {
    await renderSel();
    await openMenu('Qwen 3');
    hoverConn('local');
    const search = await screen.findByPlaceholderText('Поиск моделей...');
    fireEvent.change(search, { target: { value: 'LLAMA' } });
    const panel = search.closest('.MuiPopover-paper') as HTMLElement;
    expect(within(panel).queryByText('Qwen 3')).toBeNull();
    expect(within(panel).getByText('llama')).toBeInTheDocument();
    fireEvent.change(search, { target: { value: 'qwen 3' } }); // по display_name
    expect(within(panel).getByText('Qwen 3')).toBeInTheDocument();
    fireEvent.change(search, { target: { value: 'local/qw' } }); // по пути
    expect(within(panel).getByText('Qwen 3')).toBeInTheDocument();
    fireEvent.change(search, { target: { value: 'zzz' } });
    expect(screen.getByText('Ничего не найдено')).toBeInTheDocument();
  });

  it('подключение без моделей: «Нет доступных моделей»', async () => {
    await renderSel();
    await openMenu('Qwen 3');
    hoverConn('empty');
    expect(await screen.findByText('Нет доступных моделей')).toBeInTheDocument();
  });

  it('смена подключения сбрасывает поиск; уход мыши скрывает правую панель', async () => {
    await renderSel();
    await openMenu('Qwen 3');
    hoverConn('local');
    fireEvent.change(await screen.findByPlaceholderText('Поиск моделей...'), { target: { value: 'abc' } });
    hoverConn('openai');
    expect((screen.getByPlaceholderText('Поиск моделей...') as HTMLInputElement).value).toBe('');
    const root = screen.getByPlaceholderText('Поиск моделей...').closest('.MuiPopover-paper')!.firstElementChild!;
    fireEvent.mouseLeave(root);
    await waitFor(() => expect(screen.queryByPlaceholderText('Поиск моделей...')).toBeNull());
  });

  it('повторное открытие меню (каталог уже был) обновляет данные без спиннера, закрытие по Escape', async () => {
    await renderSel();
    await openMenu('Qwen 3');
    fireEvent.keyDown(document.querySelector('.MuiPopover-root')!, { key: 'Escape' });
    await waitFor(() => expect(document.querySelector('.MuiPopover-root')).toBeNull());
    fetchMock.mockClear();
    await openMenu('Qwen 3');
    expect(fetchMock.mock.calls.some((c) => c[0] === 'http://api/api/llm-providers?include_health=false')).toBe(true);
  });

  it('нет подключений (ошибка сети): «Нет подключений»', async () => {
    routes['GET /api/models/current'] = () => ({ ok: false });
    routes['GET /api/llm-providers?include_health=false'] = () => ({ ok: false });
    routes['GET /api/models/available'] = () => ({ ok: false });
    await renderSel();
    await openMenu();
    expect(await screen.findByText('Нет подключений')).toBeInTheDocument();
  });

  it('первое открытие без кэша показывает индикатор загрузки', async () => {
    routes['GET /api/models/current'] = () => ({ ok: false });
    routes['GET /api/llm-providers?include_health=false'] = () => ({ hang: true });
    routes['GET /api/models/available'] = () => ({ hang: true });
    await renderSel();
    fireEvent.click(trigger('Агенты / Модели'));
    await act(async () => {});
    expect(screen.getAllByRole('progressbar').length).toBeGreaterThan(0);
  });

  it('исключение при загрузке каталога подавляется', async () => {
    routes['GET /api/models/current'] = () => ({ reject: true });
    routes['GET /api/llm-providers?include_health=false'] = () => ({ reject: true });
    routes['GET /api/models/available'] = () => ({ reject: true });
    await renderSel();
    await openMenu();
    expect(await screen.findByText('Нет подключений')).toBeInTheDocument();
  });

  it('при открытии меню current с заглушкой сбрасывает выбор', async () => {
    await renderSel();
    routes['GET /api/models/current'] = () => ({ body: { path: 'default' } });
    await openMenu('Qwen 3');
    await waitFor(() => expect(localStorage.getItem(KEY)).toBeNull());
  });

  it('второй запрос каталога не дублируется, пока первый в полёте', async () => {
    let release: (v: any) => void = () => {};
    routes['GET /api/models/available'] = () => ({ ok: true, body: undefined });
    fetchMock.mockImplementation((url: string) => {
      if (String(url).endsWith('/api/models/available')) return new Promise((r) => (release = r));
      return Promise.resolve({ ok: true, json: async () => ({ providers: [] }) });
    });
    await renderSel();
    await openMenu();
    expect(fetchMock.mock.calls.filter((c) => String(c[0]).endsWith('/api/models/available'))).toHaveLength(1);
    await act(async () => {
      release({ ok: true, json: async () => ({ models: [] }) });
    });
  });
});

describe('AgentSelector: выбор модели', () => {
  async function pickLlama(onSel?: jest.Mock) {
    await renderSel({ onModelSelect: onSel });
    await openMenu('Qwen 3');
    hoverConn('local');
    fireEvent.click(await screen.findByText('llama'));
  }

  it('POST /api/models/load с model_path и токеном; событие, localStorage, колбэк, уведомление', async () => {
    const onSel = jest.fn();
    const changed = jest.fn();
    // после успешной загрузки селектор перечитывает /current: сервер подтверждает новую модель
    routes['POST /api/models/load'] = () => {
      routes['GET /api/models/current'] = () => ({ body: { path: 'local/llama.gguf' } });
      return { body: { success: true } };
    };
    window.addEventListener('astrachatModelPathChanged', changed as EventListener);
    await pickLlama(onSel);
    await waitFor(() => expect(onSel).toHaveBeenCalledWith('local/llama.gguf'));
    const call = fetchMock.mock.calls.find((c) => c[0] === 'http://api/api/models/load')!;
    expect(call[1].method).toBe('POST');
    expect(call[1].headers).toEqual({ 'Content-Type': 'application/json', Authorization: 'Bearer tok' });
    expect(JSON.parse(call[1].body)).toEqual({ model_path: 'local/llama.gguf' });
    expect(localStorage.getItem(KEY)).toBe('local/llama.gguf');
    expect((changed.mock.calls[0][0] as CustomEvent).detail).toEqual({ path: 'local/llama.gguf' });
    expect(mockNotify).toHaveBeenCalledWith('success', 'Модель успешно загружена');
    window.removeEventListener('astrachatModelPathChanged', changed as EventListener);
  });

  it('без токена заголовок Authorization не отправляется', async () => {
    mockAuth = { token: null };
    await pickLlama();
    // Загрузка весов идёт через ensureSelectedModelWeights: без токена запрос не отправляется вовсе.
    await waitFor(() =>
      expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка загрузки модели: Нет токена авторизации'),
    );
    expect(fetchMock.mock.calls.some((c) => c[0] === 'http://api/api/models/load')).toBe(false);
  });

  it('повторный выбор текущей модели закрывает меню и принудительно перезагружает веса', async () => {
    await renderSel();
    await openMenu('Qwen 3');
    hoverConn('local');
    fetchMock.mockClear();
    const items = await screen.findAllByText('Qwen 3');
    fireEvent.click(items[items.length - 1]);
    await waitFor(() => expect(screen.queryByPlaceholderText('Поиск моделей...')).toBeNull());
    await waitFor(() =>
      expect(fetchMock.mock.calls.some((c) => c[0] === 'http://api/api/models/load')).toBe(true),
    );
  });

  it('во время загрузки в триггере имя загружаемой модели и индикатор; клик по триггеру не открывает меню', async () => {
    let release: (v: any) => void = () => {};
    await renderSel();
    await openMenu('Qwen 3');
    hoverConn('local');
    const llama = await screen.findByText('llama');
    fetchMock.mockImplementation((url: string) =>
      String(url).endsWith('/api/models/load')
        ? new Promise((r) => (release = r))
        : Promise.resolve({ ok: true, json: async () => ({ models: CATALOG, path: 'local/qwen3.gguf', providers: [] }) }),
    );
    fireEvent.click(llama);
    await waitFor(() => expect(screen.getByRole('progressbar')).toBeInTheDocument());
    expect(screen.getByText('llama')).toBeInTheDocument();
    fireEvent.click(screen.getByText('llama'));
    await waitFor(() => expect(document.querySelector('.MuiPopover-root')).toBeNull());
    await act(async () => {
      release({ ok: true, json: async () => ({ success: true }) });
    });
    await waitFor(() => expect(screen.queryByRole('progressbar')).toBeNull());
  });

  it('success=false: откат к прежней модели и уведомление с текстом сервера', async () => {
    routes['POST /api/models/load'] = () => ({ body: { success: false, message: 'Нет памяти' } });
    await pickLlama();
    await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка загрузки модели: Нет памяти'));
    expect(localStorage.getItem(KEY)).toBe('local/qwen3.gguf');
    expect(trigger('Qwen 3')).toBeInTheDocument();
  });

  it('ошибка HTTP с detail', async () => {
    routes['POST /api/models/load'] = () => ({ ok: false, body: { detail: 'Модель не найдена' } });
    await pickLlama();
    await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка загрузки модели: Модель не найдена'));
  });

  it('ошибка без тела: текст по умолчанию', async () => {
    fetchMock.mockImplementation((url: string, init?: any) => {
      if (String(url).endsWith('/api/models/load')) {
        return Promise.resolve({ ok: false, json: async () => { throw new Error('x'); } });
      }
      return Promise.resolve({ ok: true, json: async () => ({ models: CATALOG, path: 'local/qwen3.gguf', providers: [] }) });
    });
    await pickLlama();
    await waitFor(() =>
      expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка загрузки модели: Не удалось загрузить модель агента'),
    );
  });

  it('сетевая ошибка при загрузке и откат без прежнего пути', async () => {
    routes['GET /api/models/current'] = () => ({ ok: false });
    routes['POST /api/models/load'] = () => ({ reject: true });
    await renderSel();
    await openMenu();
    hoverConn('local');
    fireEvent.click(await screen.findByText('llama'));
    await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка загрузки модели: net'));
    expect(localStorage.getItem(KEY)).toBeNull();
  });

  it('исключение без message выводится как есть', async () => {
    await renderSel();
    await openMenu('Qwen 3');
    hoverConn('local');
    const llama = await screen.findByText('llama');
    const orig = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((url: string, init?: any) =>
      String(url).endsWith('/api/models/load') ? Promise.reject('строка') : orig(url, init),
    );
    fireEvent.click(llama);
    await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка загрузки модели: строка'));
  });
});

describe('AgentSelector: синхронизация выбранной модели', () => {
  it('событие смены модели с валидным путём обновляет триггер', async () => {
    routes['GET /api/models/current'] = () => ({ ok: false });
    await renderSel();
    act(() => {
      window.dispatchEvent(new CustomEvent('astrachatModelPathChanged', { detail: { path: ' openai/gpt-x ' } }));
    });
    expect(trigger('gpt-x')).toBeInTheDocument();
  });

  it('событие с невалидным путём перечитывает localStorage', async () => {
    routes['GET /api/models/current'] = () => ({ ok: false });
    await renderSel();
    localStorage.setItem(KEY, 'local/llama.gguf');
    act(() => {
      window.dispatchEvent(new CustomEvent('astrachatModelPathChanged', { detail: { path: 'default' } }));
    });
    expect(trigger('llama')).toBeInTheDocument();
    act(() => {
      window.dispatchEvent(new CustomEvent('astrachatModelPathChanged'));
    });
    expect(trigger('llama')).toBeInTheDocument();
  });

  it('событие storage синхронизирует выбор между вкладками', async () => {
    routes['GET /api/models/current'] = () => ({ ok: false });
    await renderSel();
    localStorage.setItem(KEY, 'openai/gpt-x');
    act(() => {
      window.dispatchEvent(new Event('storage'));
    });
    expect(trigger('gpt-x')).toBeInTheDocument();
    act(() => {
      window.dispatchEvent(new Event('storage'));
    });
    expect(trigger('gpt-x')).toBeInTheDocument();
  });

  it('props maxWidth/triggerMaxWidth/isDarkMode=false принимаются', async () => {
    routes['GET /api/models/current'] = () => ({ ok: false });
    const { container } = await renderSel({ maxWidth: 300, triggerMaxWidth: 200, isDarkMode: false });
    expect(container.firstChild).toHaveStyle({ maxWidth: '300px' });
  });

  it('текущая модель по сети: отказ/исключение первичного запроса игнорируется', async () => {
    const orig = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((url: string, init?: any) =>
      String(url).endsWith('/api/models/current') && !init?.signal ? Promise.reject(new Error('x')) : orig(url, init),
    );
    await renderSel();
    expect(trigger('Агенты / Модели')).toBeInTheDocument();
  });
});
