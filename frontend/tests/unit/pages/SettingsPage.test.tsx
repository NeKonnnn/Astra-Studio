import React from 'react';
import { render, screen, fireEvent, act, within, waitFor } from '@testing-library/react';

const mockShowNotification = jest.fn();
const mockUseAuth = jest.fn();
jest.mock('@/contexts/AppContext', () => ({
  useAppActions: () => ({ showNotification: mockShowNotification }),
}));
jest.mock('@/contexts/AuthContext', () => ({
  useAuth: () => mockUseAuth(),
}));
jest.mock('@/config/api', () => ({
  authFetch: (...a: unknown[]) => (require('@tests/unit/helpers/authFetchMock').authFetchMock as (...x: unknown[]) => unknown)(...a),
  getApiUrl: (p: string) => `http://api.test${p}`,
  getAuthFetchHeaders: (extra: Record<string, string> = {}) => {
    const tok = mockUseAuth()?.token;
    return { ...(tok ? { Authorization: `Bearer ${tok}` } : {}), ...extra };
  },
}));
import SettingsPage from '@/pages/SettingsPage';

type Handler = (init?: RequestInit) => any;
const fetchMock = jest.fn();
let routes: Record<string, Handler>;

const ok = (data: unknown = {}) => ({ ok: true, status: 200, json: async () => data });
const bad = (status = 500) => ({ ok: false, status, json: async () => ({}) });

const MODEL_A = {
  name: 'a.gguf',
  display_name: 'Model A',
  path: 'models/a.gguf',
  size: 1024 * 1024,
  size_mb: 1,
  context_prompt: 'prompt of A',
  has_custom_prompt: true,
};
const MODEL_B = {
  name: 'b.gguf',
  path: 'models/b.gguf',
  size: 0,
  size_mb: 0,
  context_prompt: '',
  has_custom_prompt: false,
};

function defaultRoutes(): Record<string, Handler> {
  return {
    'GET /api/models/settings': () => ok({ context_size: 4096, temperature: 0.3, streaming: true, streaming_speed: 50 }),
    'GET /api/models/settings/recommended': () => ok({ max_values: { context_size: 8192, output_tokens: 2048, temperature: 1.5, top_p: 1, repeat_penalty: 1.8 } }),
    'GET /api/transcription/settings': () => ok({ language: 'en', auto_detect: false }),
    'GET /api/memory/settings': () => ok({ max_messages: 30, include_system_prompts: false }),
    'GET /api/models': () =>
      ok({
        models: [
          { name: 'a.gguf', path: 'models/a.gguf', size: 1024 * 1024, size_mb: 1 },
          { name: 'b.gguf', path: 'models/b.gguf', size: 0, size_mb: 0 },
          { display_name: 'Named C', name: 'c.gguf', path: 'models/c.gguf', size: 5 * 1024 * 1024 * 1024, size_mb: 5120 },
          { name: 'd.gguf', path: 'models/d.gguf', size: 2048, size_mb: 0 },
        ],
      }),
    'GET /api/models/current': () => ok({ loaded: false }),
    'GET /api/context-prompts/global': () => ok({ prompt: 'GLOBAL' }),
    'GET /api/context-prompts/models': () => ok({ models: [MODEL_A, MODEL_B] }),
    'GET /api/context-prompts/custom': () => ok({ prompts: {} }),
  };
}

function installFetch() {
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
    const path = String(url).replace('http://api.test', '');
    const key = `${(init && init.method) || 'GET'} ${path}`;
    const handler = routes[key];
    if (!handler) throw new Error(`unrouted fetch: ${key}`);
    return handler(init);
  });
}
const callsTo = (method: string, path: string) =>
  fetchMock.mock.calls.filter(
    ([url, init]) => String(url) === `http://api.test${path}` && ((init && init.method) || 'GET') === method,
  );
const bodyOf = (call: any[]) => JSON.parse(call[1].body as string);

const settle = async () => {
  await act(async () => {
    for (let i = 0; i < 30; i += 1) await Promise.resolve();
  });
};

async function renderPage(overrides: Record<string, Handler> = {}) {
  routes = { ...defaultRoutes(), ...overrides };
  installFetch();
  const utils = render(<SettingsPage />);
  await settle();
  return utils;
}

const field = (label: RegExp | string) =>
  screen.getAllByLabelText(label).find((el) => el.tagName === 'INPUT') as HTMLInputElement;
const maxMessages = () => field(/Максимум сообщений в контексте/);
const button = (name: RegExp | string, index = 0) => screen.getAllByRole('button', { name })[index] as HTMLButtonElement;
const switchOfLabel = (label: string) =>
  (screen.getByText(label).closest('label') as HTMLElement).querySelector('input[type="checkbox"]') as HTMLInputElement;

beforeEach(() => {
  jest.clearAllMocks();
  fetchMock.mockReset();
  localStorage.clear();
  (global as any).fetch = fetchMock;
  mockUseAuth.mockReturnValue({ token: 'tok' });
  jest.spyOn(console, 'error').mockImplementation(() => undefined);
  jest.spyOn(console, 'warn').mockImplementation(() => undefined);
});
afterEach(() => {
  jest.restoreAllMocks();
});

describe('SettingsPage: loading', () => {
  it('loads every section on mount and shows a success banner', async () => {
    await renderPage();

    expect(field('Размер контекста').value).toBe('4096');
    expect(field('Температура').value).toBe('0.3');
    expect(field('Размер контекста').max).toBe('8192');
    expect(field('Температура').max).toBe('1.5');
    expect(switchOfLabel('Автоматическое определение языка').checked).toBe(false);
    expect(maxMessages().value).toBe('30');
    expect(switchOfLabel('Включать системные промпты в контекст').checked).toBe(false);
    expect(screen.getByText('Настройки загружены')).toBeTruthy();
    expect(screen.getByText('GLOBAL')).toBeTruthy();
    expect(screen.getByText('Модель не загружена')).toBeTruthy();
  });

  it('sends the Bearer token to the model-settings and prompt requests only', async () => {
    await renderPage();

    expect(callsTo('GET', '/api/models/settings')[0][1]).toEqual({ headers: { Authorization: 'Bearer tok' } });
    expect(callsTo('GET', '/api/context-prompts/global')[0][1]).toEqual({ headers: { Authorization: 'Bearer tok' } });
  });

  it('(known issue) transcription, memory, model list and current-model requests carry no Authorization header', async () => {
    await renderPage();

    expect(callsTo('GET', '/api/transcription/settings')[0][1]).toBeUndefined();
    expect(callsTo('GET', '/api/memory/settings')[0][1]).toBeUndefined();
    expect(callsTo('GET', '/api/models')[0][1]).toBeUndefined();
    expect(callsTo('GET', '/api/models/current')[0][1]).toBeUndefined();
  });

  it('works without a token', async () => {
    mockUseAuth.mockReturnValue({ token: null });
    await renderPage();

    expect(callsTo('GET', '/api/models/settings')[0][1]).toEqual({ headers: {} });
  });

  it('(known issue) still says "Настройки загружены" when the server answered with errors', async () => {
    // Правильно — показать ошибку: баннер успеха выводится даже если ни один запрос не был успешным.
    await renderPage({
      'GET /api/models/settings': () => bad(401),
      'GET /api/transcription/settings': () => bad(500),
      'GET /api/memory/settings': () => bad(500),
    });

    expect(screen.getByText('Настройки загружены')).toBeTruthy();
    expect(field('Размер контекста').value).toBe('2048');
  });

  it('shows an error when the core settings requests throw', async () => {
    await renderPage({
      'GET /api/models/settings': () => {
        throw new Error('offline');
      },
    });

    expect(screen.getByText('Не удалось загрузить настройки')).toBeTruthy();
    expect(console.error).toHaveBeenCalled();
  });

  it('only warns when the memory settings cannot be loaded', async () => {
    await renderPage({
      'GET /api/memory/settings': () => {
        throw new Error('offline');
      },
    });

    expect(console.warn).toHaveBeenCalledWith('Не удалось загрузить настройки памяти:', expect.any(Error));
    expect(screen.getByText('Настройки загружены')).toBeTruthy();
    expect(maxMessages().value).toBe('20');
  });

  it('keeps default max values when the recommended response has none', async () => {
    await renderPage({ 'GET /api/models/settings/recommended': () => ok({}) });

    expect(field('Размер контекста').max).toBe('1000000');
  });

  it('ignores a non-OK recommended response', async () => {
    await renderPage({ 'GET /api/models/settings/recommended': () => bad(500) });

    expect(field('Размер контекста').max).toBe('1000000');
  });

  it('reports a failed model list request', async () => {
    await renderPage({ 'GET /api/models': () => bad(500) });

    expect(screen.getByText('Не удалось загрузить список моделей')).toBeTruthy();
  });

  it('reports an exception while loading the model list', async () => {
    await renderPage({
      'GET /api/models': () => {
        throw new Error('offline');
      },
    });

    expect(screen.getByText(/Ошибка загрузки моделей: Error: offline/)).toBeTruthy();
  });

  it('only warns when the current model cannot be loaded', async () => {
    await renderPage({
      'GET /api/models/current': () => {
        throw new Error('offline');
      },
    });

    expect(console.warn).toHaveBeenCalledWith('Не удалось загрузить текущую модель:', expect.any(Error));
  });

  it('notifies when the context prompts cannot be loaded', async () => {
    await renderPage({
      'GET /api/context-prompts/global': () => {
        throw new Error('offline');
      },
    });

    expect(mockShowNotification).toHaveBeenCalledWith('error', 'Ошибка загрузки контекстных промптов');
  });

  it('tolerates missing sections of the prompts responses', async () => {
    await renderPage({
      'GET /api/context-prompts/global': () => bad(),
      'GET /api/context-prompts/models': () => ok({}),
      'GET /api/context-prompts/custom': () => ok({}),
    });

    expect(screen.getByText('Глобальный промпт не установлен')).toBeTruthy();
    expect(screen.getByText('Индивидуальные промпты для моделей не созданы')).toBeTruthy();
  });

  it('skips prompt sections when the server answers non-OK', async () => {
    await renderPage({
      'GET /api/context-prompts/models': () => bad(),
      'GET /api/context-prompts/custom': () => bad(),
    });

    expect(screen.getByText('Индивидуальные промпты для моделей не созданы')).toBeTruthy();
  });

  it('shows the details of the loaded model', async () => {
    await renderPage({
      'GET /api/models/current': () =>
        ok({ loaded: true, path: 'models/a.gguf', n_ctx: 8192, metadata: { 'general.name': 'Llama X', 'general.architecture': 'llama' } }),
    });

    expect(screen.getByText('Загружена')).toBeTruthy();
    expect(screen.getByText('Llama X')).toBeTruthy();
    expect(screen.getByText('Архитектура: llama')).toBeTruthy();
    expect(screen.getByText('Контекст: 8192 токенов')).toBeTruthy();
    expect(screen.getByText('Путь: models/a.gguf')).toBeTruthy();
  });

  it('uses placeholders for a loaded model without metadata', async () => {
    await renderPage({ 'GET /api/models/current': () => ok({ loaded: true }) });

    expect(screen.getByText('Неизвестная модель')).toBeTruthy();
    expect(screen.getByText('Архитектура: Неизвестно')).toBeTruthy();
    expect(screen.getByText('Контекст: Неизвестно токенов')).toBeTruthy();
  });

  it('error and success banners can be dismissed', async () => {
    await renderPage({ 'GET /api/models': () => bad() });

    const banners = screen.getAllByRole('alert');
    const error = banners.find((b) => b.textContent!.includes('Не удалось загрузить список моделей'))!;
    fireEvent.click(within(error).getByRole('button', { name: /close/i }));
    expect(screen.queryByText('Не удалось загрузить список моделей')).toBeNull();

    const success = screen.getAllByRole('alert').find((b) => b.textContent!.includes('Настройки загружены'))!;
    fireEvent.click(within(success).getByRole('button', { name: /close/i }));
    expect(screen.queryByText('Настройки загружены')).toBeNull();
  });

  it('refresh button reloads the settings', async () => {
    await renderPage();
    routes['GET /api/models/settings'] = () => ok({ context_size: 1024 });

    fireEvent.click(button('Обновить данные', 0));
    await settle();

    expect(field('Размер контекста').value).toBe('1024');
  });
});

describe('SettingsPage: header controls', () => {
  it('hides and shows the settings page', async () => {
    await renderPage();

    fireEvent.click(button('Скрыть'));
    expect(screen.getByText('Настройки скрыты')).toBeTruthy();
    expect(screen.queryByText('Сохранить настройки')).toBeNull();

    fireEvent.click(button('Показать настройки'));
    expect(screen.getByText('Конфигурация моделей, голоса и других параметров')).toBeTruthy();
  });

  it('toggles the expand/collapse button title', async () => {
    await renderPage();

    fireEvent.click(screen.getByTitle('Свернуть секции'));
    expect(screen.getByTitle('Развернуть секции')).toBeTruthy();
    fireEvent.click(screen.getByTitle('Развернуть секции'));
    expect(screen.getByTitle('Свернуть секции')).toBeTruthy();
  });

  it('shows platform and browser info with fallbacks', async () => {
    jest.spyOn(window.navigator, 'platform', 'get').mockReturnValue('');
    jest.spyOn(window.navigator, 'userAgent', 'get').mockReturnValue('');
    await renderPage();

    expect(screen.getAllByText('Неизвестно').length).toBe(2);
  });

  it('shows real platform and browser tokens', async () => {
    jest.spyOn(window.navigator, 'platform', 'get').mockReturnValue('Win32');
    jest.spyOn(window.navigator, 'userAgent', 'get').mockReturnValue('Mozilla/5.0 (X)');
    await renderPage();

    expect(screen.getByText('Win32')).toBeTruthy();
    expect(screen.getByText('Mozilla/5.0')).toBeTruthy();
  });
});

describe('SettingsPage: saving everything', () => {
  it('saves model, transcription and memory settings in order and reports success', async () => {
    await renderPage();
    routes['PUT /api/models/settings'] = () => ok();
    routes['PUT /api/transcription/settings'] = () => ok();
    routes['PUT /api/memory/settings'] = () => ok();

    fireEvent.click(button('Сохранить настройки'));
    await settle();

    const putModel = callsTo('PUT', '/api/models/settings')[0];
    expect(putModel[1]!.headers).toEqual({ 'Content-Type': 'application/json', Authorization: 'Bearer tok' });
    expect(bodyOf(putModel)).toEqual(
      expect.objectContaining({ context_size: 4096, temperature: 0.3, streaming_speed: 50 }),
    );
    expect(bodyOf(callsTo('PUT', '/api/transcription/settings')[0])).toEqual({
      engine: 'whisperx',
      language: 'en',
      auto_detect: false,
    });
    expect(bodyOf(callsTo('PUT', '/api/memory/settings')[0])).toEqual({
      max_messages: 30,
      include_system_prompts: false,
      clear_on_restart: false,
    });
    expect(screen.getByText('Настройки сохранены успешно')).toBeTruthy();
    expect(mockShowNotification).toHaveBeenCalledWith('success', 'Настройки сохранены');
  });

  it('(known issue, auth) transcription and memory are saved without an Authorization header', async () => {
    await renderPage();
    routes['PUT /api/models/settings'] = () => ok();
    routes['PUT /api/transcription/settings'] = () => ok();
    routes['PUT /api/memory/settings'] = () => ok();

    fireEvent.click(button('Сохранить настройки'));
    await settle();

    expect(callsTo('PUT', '/api/transcription/settings')[0][1]!.headers).toEqual({ 'Content-Type': 'application/json' });
    expect(callsTo('PUT', '/api/memory/settings')[0][1]!.headers).toEqual({ 'Content-Type': 'application/json' });
  });

  it('stops at the first failure: a rejected model save prevents the other saves', async () => {
    await renderPage();
    routes['PUT /api/models/settings'] = () => bad(403);

    fireEvent.click(button('Сохранить настройки'));
    await settle();

    expect(callsTo('PUT', '/api/transcription/settings')).toHaveLength(0);
    expect(callsTo('PUT', '/api/memory/settings')).toHaveLength(0);
    expect(screen.getByText(/Ошибка сохранения: Error: Ошибка сохранения настроек модели: 403/)).toBeTruthy();
    expect(mockShowNotification).toHaveBeenCalledWith('error', 'Ошибка сохранения настроек');
    expect(screen.queryByText('Настройки сохранены успешно')).toBeNull();
  });

  it('(known issue) a failing transcription save leaves the model settings already saved (partial save, no rollback)', async () => {
    await renderPage();
    routes['PUT /api/models/settings'] = () => ok();
    routes['PUT /api/transcription/settings'] = () => bad(500);

    fireEvent.click(button('Сохранить настройки'));
    await settle();

    expect(callsTo('PUT', '/api/models/settings')).toHaveLength(1);
    expect(callsTo('PUT', '/api/memory/settings')).toHaveLength(0);
    expect(screen.getByText(/Ошибка сохранения настроек транскрибации: 500/)).toBeTruthy();
  });

  it('(known issue) reports success even when saving the memory settings failed', async () => {
    await renderPage();
    routes['PUT /api/models/settings'] = () => ok();
    routes['PUT /api/transcription/settings'] = () => ok();
    routes['PUT /api/memory/settings'] = () => bad(500);

    fireEvent.click(button('Сохранить настройки'));
    await settle();

    expect(console.warn).toHaveBeenCalledWith(expect.stringContaining('не удалось сохранить настройки памяти: 500'));
    expect(screen.getByText('Настройки сохранены успешно')).toBeTruthy();
    expect(mockShowNotification).toHaveBeenCalledWith('success', 'Настройки сохранены');
  });

  it('(known issue) reports success even when the memory save request throws', async () => {
    await renderPage();
    routes['PUT /api/models/settings'] = () => ok();
    routes['PUT /api/transcription/settings'] = () => ok();
    routes['PUT /api/memory/settings'] = () => {
      throw new Error('offline');
    };

    fireEvent.click(button('Сохранить настройки'));
    await settle();

    expect(console.warn).toHaveBeenCalledWith(
      'Предупреждение: не удалось сохранить настройки памяти:',
      expect.any(Error),
    );
    expect(mockShowNotification).toHaveBeenCalledWith('success', 'Настройки сохранены');
  });

  it('disables the action buttons while saving', async () => {
    await renderPage();
    let resolve!: (v: any) => void;
    routes['PUT /api/models/settings'] = () => new Promise((r) => (resolve = r));

    fireEvent.click(button('Сохранить настройки'));
    await settle();

    expect(button('Сохранить настройки').disabled).toBe(true);
    expect(button('Сохранить настройки модели').disabled).toBe(true);
    expect(document.querySelector('.MuiLinearProgress-root')).not.toBeNull();
    await act(async () => {
      resolve(bad(500));
    });
  });
});

describe('SettingsPage: model settings section', () => {
  it('saves only the model settings', async () => {
    await renderPage();
    routes['PUT /api/models/settings'] = () => ok();

    fireEvent.click(button('Сохранить настройки модели'));
    await settle();

    expect(callsTo('PUT', '/api/models/settings')).toHaveLength(1);
    expect(callsTo('PUT', '/api/transcription/settings')).toHaveLength(0);
    expect(screen.getByText('Настройки модели сохранены')).toBeTruthy();
    expect(mockShowNotification).toHaveBeenCalledWith('success', 'Настройки модели сохранены');
  });

  it('reports a rejected model save', async () => {
    await renderPage();
    routes['PUT /api/models/settings'] = () => bad(500);

    fireEvent.click(button('Сохранить настройки модели'));
    await settle();

    expect(screen.getByText(/Ошибка сохранения настроек модели: Error: Ошибка сохранения настроек модели: 500/)).toBeTruthy();
    expect(mockShowNotification).toHaveBeenCalledWith('error', 'Ошибка сохранения настроек модели');
  });

  it('resets to the recommended values returned by the server', async () => {
    await renderPage();
    routes['POST /api/models/settings/reset'] = () => ok({ success: true, settings: { context_size: 1111, temperature: 0.9 } });

    fireEvent.click(button('Сбросить к рекомендуемым'));
    await settle();

    expect(callsTo('POST', '/api/models/settings/reset')[0][1]!.headers).toEqual({
      'Content-Type': 'application/json',
      Authorization: 'Bearer tok',
    });
    expect(field('Размер контекста').value).toBe('1111');
    expect(field('Температура').value).toBe('0.9');
    expect(mockShowNotification).toHaveBeenCalledWith('success', 'Настройки сброшены к рекомендуемым');
  });

  it('reports a reset that the server did not accept (success=false)', async () => {
    await renderPage();
    routes['POST /api/models/settings/reset'] = () => ok({ success: false });

    fireEvent.click(button('Сбросить к рекомендуемым'));
    await settle();

    expect(screen.getByText(/Не удалось сбросить настройки/)).toBeTruthy();
    expect(mockShowNotification).toHaveBeenCalledWith('error', 'Ошибка сброса настроек модели');
    expect(field('Размер контекста').value).toBe('4096');
  });

  it('reports a failed reset request', async () => {
    await renderPage();
    routes['POST /api/models/settings/reset'] = () => bad(500);

    fireEvent.click(button('Сбросить к рекомендуемым'));
    await settle();

    expect(screen.getByText(/Ошибка сброса настроек: 500/)).toBeTruthy();
  });

  it('reloads only the model settings and max values', async () => {
    await renderPage();
    routes['GET /api/models/settings'] = () => ok({ context_size: 777 });
    routes['GET /api/models/settings/recommended'] = () => ok({ max_values: { context_size: 9999 } });
    const memoryCalls = callsTo('GET', '/api/memory/settings').length;

    fireEvent.click(button('Обновить данные', 1));
    await settle();

    expect(field('Размер контекста').value).toBe('777');
    expect(field('Размер контекста').max).toBe('9999');
    expect(screen.getByText('Настройки модели загружены')).toBeTruthy();
    expect(callsTo('GET', '/api/memory/settings')).toHaveLength(memoryCalls);
  });

  it('reloading ignores non-OK responses and reports exceptions', async () => {
    await renderPage();
    routes['GET /api/models/settings'] = () => bad();
    routes['GET /api/models/settings/recommended'] = () => bad();
    fireEvent.click(button('Обновить данные', 1));
    await settle();
    expect(field('Размер контекста').value).toBe('4096');

    routes['GET /api/models/settings'] = () => {
      throw new Error('offline');
    };
    fireEvent.click(button('Обновить данные', 1));
    await settle();
    expect(screen.getByText(/Ошибка загрузки настроек модели: Error: offline/)).toBeTruthy();
  });

  it('(known issue) the max values from the server replace the whole object, dropping keys it does not send', async () => {
    await renderPage();
    routes['GET /api/models/settings/recommended'] = () => ok({ max_values: { context_size: 5000 } });

    fireEvent.click(button('Обновить данные', 1));
    await settle();

    expect(field('Размер контекста').max).toBe('5000');
    expect(field('Температура').getAttribute('max')).toBeNull();
  });

  it('numeric fields parse input and fall back to defaults on empty or zero input', async () => {
    await renderPage();

    fireEvent.change(field('Размер контекста'), { target: { value: '1500' } });
    fireEvent.change(field('Лимит токенов ответа'), { target: { value: '300' } });
    fireEvent.change(field('Температура'), { target: { value: '1.2' } });
    fireEvent.change(field('Top-p'), { target: { value: '0.5' } });
    fireEvent.change(field('Штраф за повторы'), { target: { value: '1.3' } });
    expect(field('Размер контекста').value).toBe('1500');
    expect(field('Лимит токенов ответа').value).toBe('300');
    expect(field('Температура').value).toBe('1.2');
    expect(field('Top-p').value).toBe('0.5');
    expect(field('Штраф за повторы').value).toBe('1.3');

    fireEvent.change(field('Размер контекста'), { target: { value: '' } });
    fireEvent.change(field('Лимит токенов ответа'), { target: { value: '' } });
    fireEvent.change(field('Температура'), { target: { value: '' } });
    fireEvent.change(field('Top-p'), { target: { value: '' } });
    fireEvent.change(field('Штраф за повторы'), { target: { value: '' } });
    expect([
      field('Размер контекста').value,
      field('Лимит токенов ответа').value,
      field('Температура').value,
      field('Top-p').value,
      field('Штраф за повторы').value,
    ]).toEqual(['2048', '512', '0.7', '0.95', '1.05']);
  });

  it('(known issue) a typed 0 temperature is replaced with 0.7 (zero is treated as empty)', async () => {
    await renderPage();

    fireEvent.change(field('Температура'), { target: { value: '0' } });

    expect(field('Температура').value).toBe('0.7');
  });

  it('(known issue) values are not clamped to the max shown by the field attributes', async () => {
    await renderPage();

    fireEvent.change(field('Размер контекста'), { target: { value: '99999999' } });

    expect(field('Размер контекста').value).toBe('99999999');
  });

  it('GPU and streaming switches toggle, and the speed slider exists only while streaming is on', async () => {
    await renderPage();
    expect(screen.getByText(/Скорость потоковой генерации: 50ms/)).toBeTruthy();

    fireEvent.change(document.querySelector('input[type="range"]') as HTMLInputElement, { target: { value: '120' } });
    expect(screen.getByText(/Скорость потоковой генерации: 120ms/)).toBeTruthy();

    fireEvent.click(switchOfLabel('Использовать GPU'));
    expect(switchOfLabel('Использовать GPU').checked).toBe(true);

    fireEvent.click(switchOfLabel('Потоковая генерация'));
    expect(screen.queryByText(/Скорость потоковой генерации/)).toBeNull();
  });
});

describe('SettingsPage: transcription section', () => {
  it('changes the language through the select', async () => {
    await renderPage();

    fireEvent.mouseDown(screen.getAllByRole('combobox')[1]);
    fireEvent.click(within(screen.getByRole('listbox')).getByText('Автоопределение'));

    await waitFor(() => expect(screen.getAllByRole('combobox')[1].textContent).toBe('Автоопределение'));
  });

  it('changes the engine through the select and toggles auto detection', async () => {
    await renderPage();

    fireEvent.mouseDown(screen.getAllByRole('combobox')[0]);
    fireEvent.click(within(screen.getByRole('listbox')).getByText('WhisperX'));
    fireEvent.click(switchOfLabel('Автоматическое определение языка'));

    expect(switchOfLabel('Автоматическое определение языка').checked).toBe(true);
    await waitFor(() => expect(screen.getAllByRole('combobox')[0].textContent).toBe('WhisperX'));
  });
});

describe('SettingsPage: dialog memory settings', () => {
  it('(known issue) clamps while typing, so a two-digit value cannot be typed starting from a small digit', async () => {
    // Правильно: ограничивать на blur. Сейчас "1" немедленно превращается в 5 и набрать "10" с нуля нельзя.
    await renderPage();

    fireEvent.change(maxMessages(), { target: { value: '1' } });

    expect(maxMessages().value).toBe('5');
  });

  it.each([
    ['500', '100'],
    ['75', '75'],
    ['3', '5'],
  ])('clamps typed %s to %s', async (typed, shown) => {
    await renderPage();

    fireEvent.change(maxMessages(), { target: { value: typed } });

    expect(maxMessages().value).toBe(shown);
  });

  it('the field snaps back to the last valid value when it is cleared', async () => {
    await renderPage();

    fireEvent.change(maxMessages(), { target: { value: '' } });

    expect(maxMessages().value).toBe('30');
  });

  it('leaving the field with an empty value keeps the stored number', async () => {
    await renderPage();

    fireEvent.blur(maxMessages(), { target: { value: '' } });

    expect(screen.getByText(/Ассистент запоминает последние/).textContent).toContain('30');
  });

  it('shows the derived token estimate and summary, and warns above 50 messages', async () => {
    await renderPage();
    expect(screen.queryByText(/Внимание:/)).toBeNull();
    expect(field(/Размер контекста \(токены\)/).value).toBe('4500');
    expect(field(/Размер контекста \(токены\)/).disabled).toBe(true);

    fireEvent.change(maxMessages(), { target: { value: '80' } });

    expect(screen.getByText(/Внимание:/)).toBeTruthy();
    expect(screen.getByText(/Примерный размер контекста:/).textContent).toContain('12000');
    expect(screen.getByText(/Ассистент запоминает последние/).textContent).toContain('80');
  });

  it('the switches update the summary', async () => {
    await renderPage();
    expect(screen.getByText(/Системные промпты:/).textContent).toContain('отключены');
    expect(screen.getByText(/Очистка при перезапуске:/).textContent).toContain('отключена');

    fireEvent.click(switchOfLabel('Включать системные промпты в контекст'));
    fireEvent.click(switchOfLabel('Очищать память при перезапуске'));

    expect(screen.getByText(/Системные промпты:/).textContent).toContain('включены');
    expect(screen.getByText(/Очистка при перезапуске:/).textContent).toContain('включена');
  });

  it('resets to the defaults locally with a notification (nothing is sent until the global save)', async () => {
    await renderPage();

    fireEvent.click(button('Сбросить к умолчаниям'));

    expect(maxMessages().value).toBe('20');
    expect(switchOfLabel('Включать системные промпты в контекст').checked).toBe(true);
    expect(mockShowNotification).toHaveBeenCalledWith('info', 'Настройки контекста диалога сброшены к значениям по умолчанию');
    expect(callsTo('PUT', '/api/memory/settings')).toHaveLength(0);
  });

  it('clears the memory now', async () => {
    await renderPage();
    routes['POST /api/memory/clear'] = () => ok();

    fireEvent.click(button('Очистить память сейчас'));
    await settle();

    expect(mockShowNotification).toHaveBeenCalledWith('success', 'Память ассистента очищена');
  });

  it('(known issue) the clear-memory request has no Authorization header', async () => {
    await renderPage();
    routes['POST /api/memory/clear'] = () => ok();

    fireEvent.click(button('Очистить память сейчас'));
    await settle();

    expect(callsTo('POST', '/api/memory/clear')[0][1]).toEqual({ method: 'POST' });
  });

  it('reports a rejected clear and a network failure', async () => {
    await renderPage();
    routes['POST /api/memory/clear'] = () => bad(500);
    fireEvent.click(button('Очистить память сейчас'));
    await settle();
    expect(mockShowNotification).toHaveBeenCalledWith('error', 'Не удалось очистить память');

    routes['POST /api/memory/clear'] = () => {
      throw new Error('offline');
    };
    fireEvent.click(button('Очистить память сейчас'));
    await settle();
    expect(mockShowNotification).toHaveBeenCalledWith('error', 'Ошибка при очистке памяти');
  });

  it('shows the memory status', async () => {
    await renderPage();
    routes['GET /api/memory/status'] = () => ok({ message_count: 12 });
    fireEvent.click(button('Статус памяти'));
    await settle();
    expect(mockShowNotification).toHaveBeenCalledWith('info', 'В памяти: 12 сообщений');

    routes['GET /api/memory/status'] = () => ok({});
    fireEvent.click(button('Статус памяти'));
    await settle();
    expect(mockShowNotification).toHaveBeenCalledWith('info', 'В памяти: 0 сообщений');
  });

  it('(known issue) a non-OK memory status response produces no feedback at all', async () => {
    await renderPage();
    routes['GET /api/memory/status'] = () => bad(500);

    fireEvent.click(button('Статус памяти'));
    await settle();

    expect(mockShowNotification).not.toHaveBeenCalled();
  });

  it('reports a status request failure', async () => {
    await renderPage();
    routes['GET /api/memory/status'] = () => {
      throw new Error('offline');
    };

    fireEvent.click(button('Статус памяти'));
    await settle();

    expect(mockShowNotification).toHaveBeenCalledWith('error', 'Не удалось получить статус памяти');
  });

  it('help icons are inert', async () => {
    await renderPage();

    screen
      .getAllByRole('button', { name: /Справка/ })
      .forEach((b) => fireEvent.click(b));

    expect(maxMessages().value).toBe('30');
  });
});

describe('SettingsPage: model dialog', () => {
  const openDialog = async () => {
    fireEvent.click(button('Сменить модель'));
    await settle();
    return screen.getByRole('dialog');
  };

  it('lists the models with formatted sizes and label fallbacks', async () => {
    await renderPage();
    const dialog = await openDialog();

    expect(within(dialog).getByText('Размер: 1 MB')).toBeTruthy();
    expect(within(dialog).getByText('Размер: 0 Bytes')).toBeTruthy();
    expect(within(dialog).getByText('Размер: 5 GB')).toBeTruthy();
    expect(within(dialog).getByText('Размер: 2 KB')).toBeTruthy();
    expect(within(dialog).getByText('Named C')).toBeTruthy();
    expect(within(dialog).getByText('b.gguf')).toBeTruthy();
  });

  it('says when no models were found', async () => {
    await renderPage({ 'GET /api/models': () => ok({}) });
    const dialog = await openDialog();

    expect(within(dialog).getByText(/Модели не найдены/)).toBeTruthy();
  });

  it('loads the selected model, closes the dialog and refreshes the current model', async () => {
    await renderPage();
    routes['POST /api/models/load'] = () => ok({ success: true });
    const dialog = await openDialog();
    const load = within(dialog).getByRole('button', { name: 'Загрузить модель' }) as HTMLButtonElement;
    expect(load.disabled).toBe(true);

    fireEvent.click(within(dialog).getByText('b.gguf'));
    fireEvent.click(load);
    await settle();

    const [post] = callsTo('POST', '/api/models/load');
    expect(bodyOf(post)).toEqual({ model_path: 'models/b.gguf' });
    expect(screen.getByText('Модель успешно загружена!')).toBeTruthy();
    expect(mockShowNotification).toHaveBeenCalledWith('success', 'Модель загружена');
    expect(callsTo('GET', '/api/models/current')).toHaveLength(2);
  });

  it('(known issue) the load request has no Authorization header', async () => {
    await renderPage();
    routes['POST /api/models/load'] = () => ok({ success: true });
    const dialog = await openDialog();

    fireEvent.click(within(dialog).getByText('b.gguf'));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Загрузить модель' }));
    await settle();

    expect(callsTo('POST', '/api/models/load')[0][1]!.headers).toEqual({ 'Content-Type': 'application/json' });
  });

  it.each([
    [ok({ success: false, message: 'нет памяти' }), 'нет памяти'],
    [ok({ success: false }), 'Не удалось загрузить модель'],
    [bad(500), 'Ошибка загрузки модели'],
  ])('shows an error banner for a failed load (%#)', async (response, text) => {
    await renderPage();
    routes['POST /api/models/load'] = () => response;
    const dialog = await openDialog();

    fireEvent.click(within(dialog).getByText('b.gguf'));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Загрузить модель' }));
    await settle();

    expect(screen.getByText(new RegExp(text))).toBeTruthy();
    expect(mockShowNotification).toHaveBeenCalledWith('error', 'Ошибка загрузки модели');
  });

  it('cancel closes the dialog without loading', async () => {
    await renderPage();
    const dialog = await openDialog();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Отмена' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

    expect(callsTo('POST', '/api/models/load')).toHaveLength(0);
  });

  it('disables the controls and shows progress while a model loads', async () => {
    await renderPage();
    let resolve!: (v: any) => void;
    routes['POST /api/models/load'] = () => new Promise((r) => (resolve = r));
    const dialog = await openDialog();

    fireEvent.click(within(dialog).getByText('b.gguf'));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Загрузить модель' }));
    await settle();

    expect((screen.getByText('Загрузка модели...').closest('button') as HTMLButtonElement).disabled).toBe(true);
    await act(async () => {
      resolve(ok({ success: true }));
    });
    expect((screen.getByText('Сменить модель').closest('button') as HTMLButtonElement).disabled).toBe(false);
  });

  it('Escape closes the dialog', async () => {
    await renderPage();
    const dialog = await openDialog();

    fireEvent.keyDown(dialog, { key: 'Escape' });

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('preselects the currently loaded model', async () => {
    await renderPage({ 'GET /api/models/current': () => ok({ loaded: true, path: 'models/c.gguf' }) });
    routes['POST /api/models/load'] = () => ok({ success: true });
    const dialog = await openDialog();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Загрузить модель' }));
    await settle();

    expect(bodyOf(callsTo('POST', '/api/models/load')[0])).toEqual({ model_path: 'models/c.gguf' });
  });
});

describe('SettingsPage: context prompts', () => {
  const openGlobalDialog = async () => {
    fireEvent.click(button('Редактировать'));
    await settle();
    return screen.getByRole('dialog');
  };

  it('edits and saves the global prompt, closes the dialog and reloads prompts', async () => {
    await renderPage();
    routes['PUT /api/context-prompts/global'] = () => ok();
    const dialog = await openGlobalDialog();
    expect(within(dialog).getByText('Редактирование глобального промпта')).toBeTruthy();
    const area = within(dialog).getByLabelText('Промпт') as HTMLTextAreaElement;
    expect(area.value).toBe('GLOBAL');

    fireEvent.change(area, { target: { value: 'NEW' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Сохранить' }));
    await settle();

    expect(bodyOf(callsTo('PUT', '/api/context-prompts/global')[0])).toEqual({ prompt: 'NEW' });
    expect(mockShowNotification).toHaveBeenCalledWith('success', 'Глобальный промпт сохранен');
    expect(callsTo('GET', '/api/context-prompts/global')).toHaveLength(2);
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('keeps the dialog open and notifies if saving the global prompt fails', async () => {
    await renderPage();
    routes['PUT /api/context-prompts/global'] = () => bad(500);
    const dialog = await openGlobalDialog();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Сохранить' }));
    await settle();

    expect(mockShowNotification).toHaveBeenCalledWith('error', 'Ошибка сохранения глобального промпта');
    expect(screen.getByRole('dialog')).toBeTruthy();
  });

  it('edits a model prompt using an URL-encoded path', async () => {
    await renderPage();
    routes['PUT /api/context-prompts/model/models%2Fa.gguf'] = () => ok();
    fireEvent.click(button('Изменить'));
    await settle();
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText('Редактирование промпта модели')).toBeTruthy();
    expect((within(dialog).getByLabelText('Промпт') as HTMLTextAreaElement).value).toBe('prompt of A');

    fireEvent.change(within(dialog).getByLabelText('Промпт'), { target: { value: 'A2' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Сохранить' }));
    await settle();

    expect(bodyOf(callsTo('PUT', '/api/context-prompts/model/models%2Fa.gguf')[0])).toEqual({ prompt: 'A2' });
    expect(mockShowNotification).toHaveBeenCalledWith('success', 'Промпт модели сохранен');
  });

  it('reports a failed model prompt save', async () => {
    await renderPage();
    routes['PUT /api/context-prompts/model/models%2Fa.gguf'] = () => bad(500);
    fireEvent.click(button('Изменить'));
    await settle();

    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Сохранить' }));
    await settle();

    expect(mockShowNotification).toHaveBeenCalledWith('error', 'Ошибка сохранения промпта модели');
  });

  it('deletes a model prompt by saving an empty prompt and reloads the list', async () => {
    await renderPage();
    routes['PUT /api/context-prompts/model/models%2Fa.gguf'] = () => ok();

    fireEvent.click(button('Удалить'));
    await settle();

    expect(bodyOf(callsTo('PUT', '/api/context-prompts/model/models%2Fa.gguf')[0])).toEqual({ prompt: '' });
    expect(mockShowNotification).toHaveBeenCalledWith('success', 'Промпт модели удален');
    expect(callsTo('GET', '/api/context-prompts/models')).toHaveLength(2);
  });

  it('reports a failed deletion', async () => {
    await renderPage();
    routes['PUT /api/context-prompts/model/models%2Fa.gguf'] = () => bad(500);

    fireEvent.click(button('Удалить'));
    await settle();

    expect(mockShowNotification).toHaveBeenCalledWith('error', 'Ошибка удаления промпта модели');
    expect(screen.getByText('Model A')).toBeTruthy();
  });

  it('"add prompt" opens the model picker; a model without prompt starts from the global one', async () => {
    await renderPage();
    fireEvent.click(button(/Добавить промпт/));
    await settle();
    const picker = screen.getByRole('dialog');
    expect(within(picker).getByText('Есть промпт')).toBeTruthy();
    expect(within(picker).getByText('Размер: 0 MB')).toBeTruthy();

    fireEvent.click(within(picker).getByText('b.gguf'));
    await settle();

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Редактирование промпта модели')).toBeTruthy();
    await waitFor(() => expect((within(dialog).getByLabelText('Промпт') as HTMLTextAreaElement).value).toBe('GLOBAL'));
  });

  it('the picker says when there are no models and can be cancelled', async () => {
    await renderPage({ 'GET /api/context-prompts/models': () => ok({ models: [] }) });
    fireEvent.click(button(/Добавить промпт/));
    await settle();
    const picker = screen.getByRole('dialog');

    expect(within(picker).getByText('Модели не найдены')).toBeTruthy();
    fireEvent.click(within(picker).getByRole('button', { name: 'Отмена' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('Escape closes the prompt editor and the picker', async () => {
    await renderPage();
    const editor = await openGlobalDialog();
    fireEvent.keyDown(editor, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

    fireEvent.click(button(/Добавить промпт/));
    await settle();
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('cancel in the editor closes it without saving', async () => {
    await renderPage();
    const editor = await openGlobalDialog();

    fireEvent.click(within(editor).getByRole('button', { name: 'Отмена' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

    expect(callsTo('PUT', '/api/context-prompts/global')).toHaveLength(0);
  });

  it('lists a model prompt with its label (display name or name or path)', async () => {
    await renderPage({
      'GET /api/context-prompts/models': () =>
        ok({ models: [MODEL_A, { ...MODEL_B, name: '', has_custom_prompt: true, context_prompt: 'x' }] }),
    });

    expect(screen.getByText('Model A')).toBeTruthy();
    expect(screen.getAllByText('models/b.gguf').length).toBeGreaterThan(0);
  });
});
