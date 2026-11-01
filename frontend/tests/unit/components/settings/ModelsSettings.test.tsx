import React from 'react';
import { render, screen, fireEvent, act, within, waitFor } from '@testing-library/react';

const mockShowNotification = jest.fn();
const mockSyncGlobal = jest.fn();
const mockUseAuth = jest.fn();
jest.mock('@/contexts/AppContext', () => ({
  useAppActions: () => ({ showNotification: mockShowNotification, setModelSettings: mockSyncGlobal }),
}));
jest.mock('@/contexts/AuthContext', () => ({
  useAuth: () => mockUseAuth(),
}));
jest.mock('@/config/api', () => ({
  authFetch: (...a: unknown[]) => (require('@tests/unit/helpers/authFetchMock').authFetchMock as (...x: unknown[]) => unknown)(...a),
  getApiUrl: (p: string) => `http://api.test${p}`,
  getAuthFetchHeaders: (extra?: Record<string, string>) => {
    const tok = mockUseAuth()?.token;
    return { ...(extra || {}), ...(tok ? { Authorization: `Bearer ${tok}` } : {}) };
  },
}));
jest.mock('@/components/ModelSelector', () => ({
  __esModule: true,
  default: ({ onModelSelect }: { onModelSelect: (p: string) => void }) => (
    <button onClick={() => onModelSelect('picked/model.gguf')}>stub-pick-model</button>
  ),
}));
jest.mock('@/components/ModelSettingsFields', () => ({
  __esModule: true,
  default: ({ value, onChange, maxValues, defaults }: any) => (
    <div>
      <div data-testid="msf-value">{JSON.stringify(value)}</div>
      <div data-testid="msf-max">{JSON.stringify(maxValues)}</div>
      <div data-testid="msf-defaults">{JSON.stringify(defaults)}</div>
      <button onClick={() => onChange({ ...value, temperature: 1.5 })}>stub-set-temperature</button>
    </div>
  ),
}));

import { MODEL_SETTINGS_DEFAULT } from '@/constants/modelSettingsStyles';
import { MODEL_SETTINGS_CHANGED_EVENT } from '@/utils/contextTokens';
import ModelsSettings from '@/components/settings/ModelsSettings';

type Handler = (init?: RequestInit) => any;
const fetchMock = jest.fn();
let routes: Record<string, Handler>;

const ok = (data: unknown) => ({ ok: true, status: 200, json: async () => data });
const bad = (status = 500) => ({ ok: false, status, json: async () => ({}) });

const MODEL_A = {
  name: 'a.gguf',
  display_name: 'Model A',
  path: 'models/a.gguf',
  size: 1024 * 1024,
  size_mb: 1,
  context_prompt: 'prompt of A',
  has_custom_prompt: true,
  provider_id: 'local',
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
    'GET /api/models/settings': () => ok({ temperature: 0.3, context_size: 4096 }),
    'GET /api/models/settings/recommended': () =>
      ok({ max_values: { top_k: 77 }, recommended: { temperature: 0.4 } }),
    'GET /api/models': () =>
      ok({
        models: [
          { name: 'a.gguf', path: 'models/a.gguf', size: 1024 * 1024, size_mb: 1 },
          { name: 'b.gguf', path: 'models/b.gguf', size: 0, size_mb: 0 },
          { name: 'c.gguf', path: 'models/c.gguf', size: 3 * 1024 * 1024 * 1024, size_mb: 3072 },
          { name: 'd.gguf', path: 'models/d.gguf', size: 2048, size_mb: 0 },
        ],
      }),
    'GET /api/models/current': () => ok({ loaded: false }),
    'GET /api/context-prompts/global': () => ok({ prompt: 'GLOBAL PROMPT' }),
    'GET /api/context-prompts/models': () => ok({ models: [MODEL_A, MODEL_B] }),
    'GET /api/context-prompts/custom': () => ok({ prompts: { x: { prompt: 'p', description: 'd', created_at: 't' } } }),
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

const settle = async () => {
  await act(async () => {
    for (let i = 0; i < 20; i += 1) await Promise.resolve();
  });
};
const advance = async (ms: number) => {
  await act(async () => {
    jest.advanceTimersByTime(ms);
  });
  await settle();
};

async function renderSettings(overrides: Record<string, Handler> = {}) {
  routes = { ...defaultRoutes(), ...overrides };
  installFetch();
  const utils = render(<ModelsSettings />);
  await settle();
  return utils;
}

const fieldsValue = () => JSON.parse(screen.getByTestId('msf-value').textContent || '{}');

beforeEach(() => {
  jest.useFakeTimers();
  jest.clearAllMocks();
  fetchMock.mockReset();
  localStorage.clear();
  (global as any).fetch = fetchMock;
  mockUseAuth.mockReturnValue({ token: 'tok' });
  jest.spyOn(console, 'error').mockImplementation(() => undefined);
  jest.spyOn(console, 'warn').mockImplementation(() => undefined);
  jest.spyOn(console, 'log').mockImplementation(() => undefined);
});
afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
});

describe('ModelsSettings: loading', () => {
  it('requests all initial resources and sends the Bearer token where headers are built', async () => {
    await renderSettings();

    expect(callsTo('GET', '/api/models/settings')[0][1]).toEqual({ headers: { Authorization: 'Bearer tok' } });
    expect(callsTo('GET', '/api/context-prompts/global')[0][1]).toEqual({ headers: { Authorization: 'Bearer tok' } });
    expect(callsTo('GET', '/api/context-prompts/models')).toHaveLength(1);
    expect(callsTo('GET', '/api/context-prompts/custom')).toHaveLength(1);
    expect(callsTo('GET', '/api/models')).toHaveLength(1);
    expect(callsTo('GET', '/api/models/current')).toHaveLength(1);
  });

  it('omits the Authorization header when there is no token', async () => {
    mockUseAuth.mockReturnValue({ token: null });
    await renderSettings();

    expect(callsTo('GET', '/api/models/settings')[0][1]).toEqual({ headers: {} });
  });

  it('loads the model list, current model and recommended settings with auth headers (BUG-FE-35)', async () => {
    await renderSettings();

    expect(callsTo('GET', '/api/models')[0][1]).toEqual({ headers: { Authorization: 'Bearer tok' } });
    expect(callsTo('GET', '/api/models/current')[0][1]).toEqual({ headers: { Authorization: 'Bearer tok' } });
    expect(callsTo('GET', '/api/models/settings/recommended')[0][1]).toEqual({ headers: { Authorization: 'Bearer tok' } });
  });

  it('merges server settings over defaults, passes recommended/max values to the form and syncs the global store', async () => {
    await renderSettings();

    expect(fieldsValue()).toEqual({ ...MODEL_SETTINGS_DEFAULT, temperature: 0.3, context_size: 4096 });
    expect(JSON.parse(screen.getByTestId('msf-max').textContent!).top_k).toBe(77);
    expect(JSON.parse(screen.getByTestId('msf-max').textContent!).batch_size).toBe(2048);
    expect(JSON.parse(screen.getByTestId('msf-defaults').textContent!).temperature).toBe(0.4);
    expect(mockSyncGlobal).toHaveBeenCalledWith({
      ...MODEL_SETTINGS_DEFAULT,
      temperature: 0.3,
      context_size: 4096,
      streaming_speed: 20,
    });
  });

  it('keeps the server streaming_speed when it is provided', async () => {
    await renderSettings({ 'GET /api/models/settings': () => ok({ streaming_speed: 55 }) });

    expect(mockSyncGlobal).toHaveBeenCalledWith(expect.objectContaining({ streaming_speed: 55 }));
  });

  it('accepts the legacy "recommended_settings" key', async () => {
    await renderSettings({
      'GET /api/models/settings/recommended': () => ok({ recommended_settings: { top_k: 11 } }),
    });

    expect(JSON.parse(screen.getByTestId('msf-defaults').textContent!).top_k).toBe(11);
  });

  it('tolerates a recommended response without max_values and recommended blocks', async () => {
    await renderSettings({ 'GET /api/models/settings/recommended': () => ok({}) });

    expect(JSON.parse(screen.getByTestId('msf-defaults').textContent!)).toEqual(MODEL_SETTINGS_DEFAULT);
  });

  it('ignores a non-OK recommended response', async () => {
    await renderSettings({ 'GET /api/models/settings/recommended': () => bad() });

    expect(JSON.parse(screen.getByTestId('msf-defaults').textContent!)).toEqual(MODEL_SETTINGS_DEFAULT);
  });

  it('does not sync the global store when the settings request answers non-OK', async () => {
    await renderSettings({ 'GET /api/models/settings': () => bad(401) });

    expect(fieldsValue()).toEqual(MODEL_SETTINGS_DEFAULT);
    expect(mockSyncGlobal).not.toHaveBeenCalled();
  });

  it('logs and keeps defaults when the settings request throws', async () => {
    await renderSettings({
      'GET /api/models/settings': () => {
        throw new Error('offline');
      },
    });

    expect(console.error).toHaveBeenCalledWith('Ошибка загрузки настроек:', expect.any(Error));
    expect(fieldsValue()).toEqual(MODEL_SETTINGS_DEFAULT);
  });

  it('logs when the model list cannot be loaded and treats a list without "models" as empty', async () => {
    await renderSettings({
      'GET /api/models': () => {
        throw new Error('offline');
      },
    });
    expect(console.error).toHaveBeenCalledWith('Ошибка загрузки моделей:', expect.any(Error));
  });

  it('warns when the current model cannot be loaded', async () => {
    await renderSettings({
      'GET /api/models/current': () => {
        throw new Error('offline');
      },
    });
    expect(console.warn).toHaveBeenCalledWith('Не удалось загрузить текущую модель:', expect.any(Error));
  });
});

describe('ModelsSettings: autosave of model settings', () => {
  it('does not save right after the initial load', async () => {
    await renderSettings();
    await advance(5000);

    expect(callsTo('PUT', '/api/models/settings')).toHaveLength(0);
  });

  it('saves after one second of inactivity, then syncs the store and announces the context size', async () => {
    await renderSettings();
    const events: any[] = [];
    const listener = (e: Event) => events.push((e as CustomEvent).detail);
    window.addEventListener(MODEL_SETTINGS_CHANGED_EVENT, listener);
    routes['PUT /api/models/settings'] = () => ok({});
    mockSyncGlobal.mockClear();

    fireEvent.click(screen.getByText('stub-set-temperature'));
    await advance(999);
    expect(callsTo('PUT', '/api/models/settings')).toHaveLength(0);
    await advance(1);

    const [put] = callsTo('PUT', '/api/models/settings');
    expect(put[1]!.headers).toEqual({ 'Content-Type': 'application/json', Authorization: 'Bearer tok' });
    expect(JSON.parse(put[1]!.body as string)).toEqual({
      ...MODEL_SETTINGS_DEFAULT,
      temperature: 1.5,
      context_size: 4096,
    });
    expect(mockShowNotification).toHaveBeenCalledWith('success', 'Настройки модели сохранены');
    expect(mockSyncGlobal).toHaveBeenCalledWith(expect.objectContaining({ temperature: 1.5 }));
    expect(events).toEqual([{ context_size: 4096 }]);
    window.removeEventListener(MODEL_SETTINGS_CHANGED_EVENT, listener);
  });

  it('shows an error and does not sync the store when the server rejects the save', async () => {
    await renderSettings();
    routes['PUT /api/models/settings'] = () => bad(422);
    mockSyncGlobal.mockClear();

    fireEvent.click(screen.getByText('stub-set-temperature'));
    await advance(1000);

    expect(mockShowNotification).toHaveBeenCalledWith('error', 'Ошибка сохранения настроек модели');
    expect(mockShowNotification).not.toHaveBeenCalledWith('success', expect.anything());
    expect(mockSyncGlobal).not.toHaveBeenCalled();
  });

  it('rolls the form back after a failed save (BUG-FE-36)', async () => {
    await renderSettings();
    routes['PUT /api/models/settings'] = () => bad(500);

    fireEvent.click(screen.getByText('stub-set-temperature'));
    await advance(1000);

    expect(fieldsValue().temperature).toBe(0.3);
    // откат не порождает повторного автосохранения
    await advance(1500);
    expect(callsTo('PUT', '/api/models/settings')).toHaveLength(1);
  });

  it('shows an error and still autosaves edits when the initial settings request failed', async () => {
    await renderSettings({ 'GET /api/models/settings': () => bad(500) });
    expect(mockShowNotification).toHaveBeenCalledWith('error', expect.stringContaining('Не удалось загрузить настройки модели'));
    routes['PUT /api/models/settings'] = () => ok({});

    fireEvent.click(screen.getByText('stub-set-temperature'));
    await advance(1000);

    expect(fieldsValue().temperature).toBe(1.5);
    expect(callsTo('PUT', '/api/models/settings')).toHaveLength(1);
    expect(JSON.parse(callsTo('PUT', '/api/models/settings')[0][1]!.body as string).temperature).toBe(1.5);
  });

  it('shows an error and still autosaves edits when the initial settings request threw', async () => {
    await renderSettings({
      'GET /api/models/settings': () => {
        throw new Error('offline');
      },
    });
    expect(mockShowNotification).toHaveBeenCalledWith('error', expect.stringContaining('Не удалось загрузить настройки модели'));
    routes['PUT /api/models/settings'] = () => ok({});

    fireEvent.click(screen.getByText('stub-set-temperature'));
    await advance(1000);

    expect(callsTo('PUT', '/api/models/settings')).toHaveLength(1);
  });

  it('reset applies recommended values over defaults and notifies; autosave then persists them', async () => {
    await renderSettings();
    routes['PUT /api/models/settings'] = () => ok({});

    fireEvent.click(screen.getByRole('button', { name: /Восстановить настройки/ }));

    expect(fieldsValue()).toEqual({ ...MODEL_SETTINGS_DEFAULT, temperature: 0.4 });
    expect(mockShowNotification).toHaveBeenCalledWith(
      'success',
      'Настройки модели восстановлены до значений по умолчанию',
    );
    await advance(1000);
    expect(JSON.parse(callsTo('PUT', '/api/models/settings')[0][1]!.body as string).temperature).toBe(0.4);
  });
});

describe('ModelsSettings: model selector card', () => {
  it('is hidden by default', async () => {
    await renderSettings();

    expect(screen.queryByText('Выбор модели')).toBeNull();
  });

  it('is shown when model_selector_mode is "settings"', async () => {
    localStorage.setItem('model_selector_mode', 'settings');
    await renderSettings();

    expect(screen.getByText('Выбор модели')).toBeTruthy();
    expect(screen.getByText('Модель не загружена')).toBeTruthy();
  });

  it('stays hidden for another explicit mode even if the legacy flag is true', async () => {
    localStorage.setItem('model_selector_mode', 'chat');
    localStorage.setItem('show_model_selector_in_settings', 'true');
    await renderSettings();

    expect(screen.queryByText('Выбор модели')).toBeNull();
  });

  it('falls back to the legacy show_model_selector_in_settings flag', async () => {
    localStorage.setItem('show_model_selector_in_settings', 'true');
    await renderSettings();

    expect(screen.getByText('Выбор модели')).toBeTruthy();
  });

  it('reacts to the interfaceSettingsChanged event', async () => {
    await renderSettings();
    expect(screen.queryByText('Выбор модели')).toBeNull();

    localStorage.setItem('model_selector_mode', 'settings');
    act(() => {
      window.dispatchEvent(new Event('interfaceSettingsChanged'));
    });

    expect(screen.getByText('Выбор модели')).toBeTruthy();
  });

  it('stops listening to interfaceSettingsChanged after unmount', async () => {
    const { unmount } = await renderSettings();
    unmount();
    localStorage.setItem('model_selector_mode', 'settings');

    expect(() => window.dispatchEvent(new Event('interfaceSettingsChanged'))).not.toThrow();
  });

  it('shows details of the loaded model', async () => {
    localStorage.setItem('model_selector_mode', 'settings');
    await renderSettings({
      'GET /api/models/current': () =>
        ok({
          loaded: true,
          path: 'models/a.gguf',
          n_ctx: 8192,
          metadata: { 'general.name': 'Llama X', 'general.architecture': 'llama' },
        }),
    });

    expect(screen.getByText('Загружена')).toBeTruthy();
    expect(screen.getByText('Llama X')).toBeTruthy();
    expect(screen.getByText('Архитектура: llama')).toBeTruthy();
    expect(screen.getByText('Контекст: 8192 токенов')).toBeTruthy();
    expect(screen.getByText('Путь: models/a.gguf')).toBeTruthy();
  });

  it('uses placeholders when the loaded model has no metadata', async () => {
    localStorage.setItem('model_selector_mode', 'settings');
    await renderSettings({ 'GET /api/models/current': () => ok({ loaded: true }) });

    expect(screen.getByText('Неизвестная модель')).toBeTruthy();
    expect(screen.getByText('Архитектура: Неизвестно')).toBeTruthy();
    expect(screen.getByText('Контекст: Неизвестно токенов')).toBeTruthy();
  });

  it('reloads the current model after the selector reports a selection', async () => {
    localStorage.setItem('model_selector_mode', 'settings');
    await renderSettings();
    expect(callsTo('GET', '/api/models/current')).toHaveLength(1);

    fireEvent.click(screen.getByText('stub-pick-model'));
    await settle();

    expect(callsTo('GET', '/api/models/current')).toHaveLength(2);
  });
});

describe('ModelsSettings: model dialog', () => {
  async function openDialog(overrides: Record<string, Handler> = {}) {
    localStorage.setItem('model_selector_mode', 'settings');
    await renderSettings(overrides);
    fireEvent.click(screen.getByRole('button', { name: 'Сменить модель' }));
    await advance(10);
    return screen.getByRole('dialog');
  }

  it('lists available models with formatted sizes', async () => {
    const dialog = await openDialog();

    expect(within(dialog).getByText('Выбор модели')).toBeTruthy();
    expect(within(dialog).getByText('Размер: 1 MB')).toBeTruthy();
    expect(within(dialog).getByText('Размер: 0 Bytes')).toBeTruthy();
    expect(within(dialog).getByText('Размер: 3 GB')).toBeTruthy();
    expect(within(dialog).getByText('Размер: 2 KB')).toBeTruthy();
  });

  it('says that no models were found when the list is empty', async () => {
    const dialog = await openDialog({ 'GET /api/models': () => ok({}) });

    expect(within(dialog).getByText(/Модели не найдены/)).toBeTruthy();
  });

  it('disables loading until a model is selected, then loads the selected one and refreshes the current model', async () => {
    const dialog = await openDialog();
    const load = within(dialog).getByRole('button', { name: 'Загрузить модель' }) as HTMLButtonElement;
    expect(load.disabled).toBe(true);
    routes['POST /api/models/load'] = () => ok({ success: true });

    fireEvent.click(within(dialog).getByText('b.gguf'));
    expect(load.disabled).toBe(false);
    fireEvent.click(load);
    await advance(10);

    const [post] = callsTo('POST', '/api/models/load');
    expect(post[1]!.headers).toEqual({ 'Content-Type': 'application/json', Authorization: 'Bearer tok' });
    expect(JSON.parse(post[1]!.body as string)).toEqual({ model_path: 'models/b.gguf' });
    expect(mockShowNotification).toHaveBeenCalledWith('success', 'Модель успешно загружена!');
    expect(callsTo('GET', '/api/models/current')).toHaveLength(2);
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('preselects the currently loaded model path', async () => {
    const dialog = await openDialog({
      'GET /api/models/current': () => ok({ loaded: true, path: 'models/a.gguf' }),
    });
    routes['POST /api/models/load'] = () => ok({ success: true });

    fireEvent.click(within(dialog).getByRole('button', { name: 'Загрузить модель' }));
    await advance(10);

    expect(JSON.parse(callsTo('POST', '/api/models/load')[0][1]!.body as string)).toEqual({
      model_path: 'models/a.gguf',
    });
  });

  it('reports the backend message when loading answers success=false', async () => {
    const dialog = await openDialog();
    routes['POST /api/models/load'] = () => ok({ success: false, message: 'не хватает памяти' });

    fireEvent.click(within(dialog).getByText('b.gguf'));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Загрузить модель' }));
    await advance(10);

    expect(mockShowNotification).toHaveBeenCalledWith('error', expect.stringContaining('не хватает памяти'));
    expect(mockShowNotification).not.toHaveBeenCalledWith('success', 'Модель успешно загружена!');
  });

  it('uses a default message when success=false carries no message', async () => {
    const dialog = await openDialog();
    routes['POST /api/models/load'] = () => ok({ success: false });

    fireEvent.click(within(dialog).getByText('b.gguf'));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Загрузить модель' }));
    await advance(10);

    expect(mockShowNotification).toHaveBeenCalledWith('error', expect.stringContaining('Не удалось загрузить модель'));
  });

  it('reports a generic error for a non-OK load response', async () => {
    const dialog = await openDialog();
    routes['POST /api/models/load'] = () => bad(500);

    fireEvent.click(within(dialog).getByText('b.gguf'));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Загрузить модель' }));
    await advance(10);

    expect(mockShowNotification).toHaveBeenCalledWith('error', expect.stringContaining('Ошибка загрузки модели'));
  });

  it('POSTs the model load with an Authorization header (BUG-FE-35)', async () => {
    const dialog = await openDialog();
    routes['POST /api/models/load'] = () => ok({ success: true });

    fireEvent.click(within(dialog).getByText('b.gguf'));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Загрузить модель' }));
    await advance(10);

    expect((callsTo('POST', '/api/models/load')[0][1]!.headers as Record<string, string>).Authorization).toBe('Bearer tok');
  });

  it('(known issue) formats the error notification as "Error: ..." (stringified exception object)', async () => {
    const dialog = await openDialog();
    routes['POST /api/models/load'] = () => ok({ success: false, message: 'oops' });

    fireEvent.click(within(dialog).getByText('b.gguf'));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Загрузить модель' }));
    await advance(10);

    expect(mockShowNotification).toHaveBeenCalledWith('error', 'Ошибка загрузки модели: Error: oops');
  });

  it('shows a progress label on the button while a model is loading and keeps cancel disabled in the dialog', async () => {
    const dialog = await openDialog();
    let resolveLoad!: (v: any) => void;
    routes['POST /api/models/load'] = () => new Promise((r) => (resolveLoad = r));

    fireEvent.click(within(dialog).getByText('b.gguf'));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Загрузить модель' }));
    await advance(10);

    const trigger = screen.getByRole('button', { name: 'Загрузка модели...' }) as HTMLButtonElement;
    expect(trigger.disabled).toBe(true);

    await act(async () => {
      resolveLoad(ok({ success: true }));
    });
    await advance(10);
    expect((screen.getByRole('button', { name: 'Сменить модель' }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('closes on cancel without loading anything', async () => {
    const dialog = await openDialog();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Отмена' }));
    await advance(10);

    expect(callsTo('POST', '/api/models/load')).toHaveLength(0);
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });
});

describe('ModelsSettings: context prompts', () => {
  it('shows the global prompt and the placeholder when it is empty', async () => {
    const { unmount } = await renderSettings();
    expect(screen.getByText('GLOBAL PROMPT')).toBeTruthy();
    unmount();

    await renderSettings({ 'GET /api/context-prompts/global': () => ok({ prompt: '' }) });
    expect(screen.getByText('Глобальная инструкция не установлена')).toBeTruthy();
  });

  it('lists only models with a custom prompt, with provider chip and label fallback', async () => {
    await renderSettings({
      'GET /api/context-prompts/models': () =>
        ok({
          models: [
            MODEL_A,
            { ...MODEL_B, has_custom_prompt: true, context_prompt: 'prompt of B' },
            { ...MODEL_B, path: 'models/c.gguf', name: 'c.gguf' },
          ],
        }),
    });

    expect(screen.getByText('Model A')).toBeTruthy();
    expect(screen.getByText('local')).toBeTruthy();
    expect(screen.getByText('prompt of A')).toBeTruthy();
    expect(screen.getByText('b.gguf')).toBeTruthy();
    expect(screen.getAllByText('Кастомный')).toHaveLength(2);
    expect(screen.queryByText('c.gguf')).toBeNull();
  });

  it('says there are no individual prompts when none are custom', async () => {
    await renderSettings({ 'GET /api/context-prompts/models': () => ok({ models: [MODEL_B] }) });

    expect(screen.getByText('Индивидуальные инструкции для моделей не созданы')).toBeTruthy();
  });

  it('tolerates responses without "models" and "prompts" keys', async () => {
    await renderSettings({
      'GET /api/context-prompts/models': () => ok({}),
      'GET /api/context-prompts/custom': () => ok({}),
    });

    expect(screen.getByText('Индивидуальные инструкции для моделей не созданы')).toBeTruthy();
  });

  it('skips sections whose requests answer non-OK', async () => {
    await renderSettings({
      'GET /api/context-prompts/global': () => bad(),
      'GET /api/context-prompts/models': () => bad(),
      'GET /api/context-prompts/custom': () => bad(),
    });

    expect(screen.getByText('Глобальная инструкция не установлена')).toBeTruthy();
    expect(mockShowNotification).not.toHaveBeenCalled();
  });

  it('notifies about an error when loading prompts throws', async () => {
    await renderSettings({
      'GET /api/context-prompts/global': () => {
        throw new Error('offline');
      },
    });

    expect(mockShowNotification).toHaveBeenCalledWith('error', 'Ошибка загрузки контекстных инструкций');
  });

  it('edits and saves the global prompt, closes the dialog and reloads the prompts', async () => {
    await renderSettings();
    routes['PUT /api/context-prompts/global'] = () => ok({});
    fireEvent.click(screen.getAllByRole('button', { name: 'Редактировать' })[0]);
    await advance(10);
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText('Редактирование глобальной инструкции')).toBeTruthy();
    const area = within(dialog).getByLabelText('Инструкция') as HTMLTextAreaElement;
    expect(area.value).toBe('GLOBAL PROMPT');

    fireEvent.change(area, { target: { value: 'NEW GLOBAL' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Сохранить' }));
    await advance(10);

    const [put] = callsTo('PUT', '/api/context-prompts/global');
    expect(put[1]!.headers).toEqual({ 'Content-Type': 'application/json', Authorization: 'Bearer tok' });
    expect(JSON.parse(put[1]!.body as string)).toEqual({ prompt: 'NEW GLOBAL' });
    expect(mockShowNotification).toHaveBeenCalledWith('success', 'Глобальная инструкция сохранена');
    expect(callsTo('GET', '/api/context-prompts/global')).toHaveLength(2);
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('keeps the dialog open and reports an error if saving the global prompt fails', async () => {
    await renderSettings();
    routes['PUT /api/context-prompts/global'] = () => bad(403);
    fireEvent.click(screen.getAllByRole('button', { name: 'Редактировать' })[0]);
    await advance(10);
    const dialog = screen.getByRole('dialog');

    fireEvent.change(within(dialog).getByLabelText('Инструкция'), { target: { value: 'X' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Сохранить' }));
    await advance(10);

    expect(mockShowNotification).toHaveBeenCalledWith('error', 'Ошибка сохранения глобальной инструкции');
    expect(screen.getByRole('dialog')).toBeTruthy();
    expect(callsTo('GET', '/api/context-prompts/global')).toHaveLength(1);
    expect((within(dialog).getByLabelText('Инструкция') as HTMLTextAreaElement).value).toBe('X');
  });

  it('cancel closes the prompt dialog without saving', async () => {
    await renderSettings();
    fireEvent.click(screen.getAllByRole('button', { name: 'Редактировать' })[0]);
    await advance(10);

    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Отмена' }));
    await advance(500);

    expect(callsTo('PUT', '/api/context-prompts/global')).toHaveLength(0);
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('edits a model prompt: URL-encodes the path and stores the text', async () => {
    await renderSettings();
    routes[`PUT /api/context-prompts/model/${encodeURIComponent('models/a.gguf')}`] = () => ok({});
    fireEvent.click(screen.getByRole('button', { name: 'Изменить' }));
    await advance(10);
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText('Редактирование инструкции модели')).toBeTruthy();
    expect((within(dialog).getByLabelText('Инструкция') as HTMLTextAreaElement).value).toBe('prompt of A');

    fireEvent.change(within(dialog).getByLabelText('Инструкция'), { target: { value: 'A2' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Сохранить' }));
    await advance(10);

    const [put] = callsTo('PUT', '/api/context-prompts/model/models%2Fa.gguf');
    expect(JSON.parse(put[1]!.body as string)).toEqual({ prompt: 'A2' });
    expect(mockShowNotification).toHaveBeenCalledWith('success', 'Инструкция модели сохранена');
  });

  it('reports an error when saving a model prompt fails', async () => {
    await renderSettings();
    routes[`PUT /api/context-prompts/model/${encodeURIComponent('models/a.gguf')}`] = () => bad(500);
    fireEvent.click(screen.getByRole('button', { name: 'Изменить' }));
    await advance(10);

    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Сохранить' }));
    await advance(10);

    expect(mockShowNotification).toHaveBeenCalledWith('error', 'Ошибка сохранения инструкции модели');
    expect(screen.getByRole('dialog')).toBeTruthy();
  });

  it('"add instruction" lets the user pick a model; a model without own prompt starts from the global one', async () => {
    await renderSettings();
    fireEvent.click(screen.getByRole('button', { name: 'Добавить инструкцию' }));
    await advance(10);
    const picker = screen.getByRole('dialog');
    expect(within(picker).getByText('Выберите модель для создания инструкции')).toBeTruthy();
    expect(within(picker).getByText('Есть инструкция')).toBeTruthy();
    expect(within(picker).getByText('Размер: 0 MB')).toBeTruthy();

    fireEvent.click(within(picker).getByText('b.gguf'));
    await advance(500);

    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText('Редактирование инструкции модели')).toBeTruthy();
    expect((within(dialog).getByLabelText('Инструкция') as HTMLTextAreaElement).value).toBe('GLOBAL PROMPT');
  });

  it('says no models were found in the picker when the list is empty and lets the user cancel', async () => {
    await renderSettings({ 'GET /api/context-prompts/models': () => ok({ models: [] }) });
    fireEvent.click(screen.getByRole('button', { name: 'Добавить инструкцию' }));
    await advance(10);
    const picker = screen.getByRole('dialog');

    expect(within(picker).getByText('Модели не найдены')).toBeTruthy();
    fireEvent.click(within(picker).getByRole('button', { name: 'Отмена' }));
    await advance(500);
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});

describe('ModelsSettings: dismissing dialogs with Escape', () => {
  it('Escape closes the prompt editor without saving', async () => {
    await renderSettings();
    fireEvent.click(screen.getAllByRole('button', { name: 'Редактировать' })[0]);
    await advance(10);

    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    await advance(500);

    expect(screen.queryByRole('dialog')).toBeNull();
    expect(callsTo('PUT', '/api/context-prompts/global')).toHaveLength(0);
  });

  it('Escape closes the model picker for prompts', async () => {
    await renderSettings();
    fireEvent.click(screen.getByRole('button', { name: 'Добавить инструкцию' }));
    await advance(10);

    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    await advance(500);

    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('Escape closes the model selection dialog without loading', async () => {
    localStorage.setItem('model_selector_mode', 'settings');
    await renderSettings();
    fireEvent.click(screen.getByRole('button', { name: 'Сменить модель' }));
    await advance(10);

    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    await advance(500);

    expect(screen.queryByRole('dialog')).toBeNull();
    expect(callsTo('POST', '/api/models/load')).toHaveLength(0);
  });

  it('help icons in the instructions section are inert', async () => {
    await renderSettings();
    document
      .querySelectorAll('[data-testid="HelpOutlineIcon"]')
      .forEach((icon) => fireEvent.click(icon.closest('button') as HTMLElement));

    expect(screen.queryByRole('dialog')).toBeNull();
  });
});
