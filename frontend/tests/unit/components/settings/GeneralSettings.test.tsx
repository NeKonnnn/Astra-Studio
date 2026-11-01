import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const mockShowNotification = jest.fn();
jest.mock('@/contexts/AppContext', () => ({
  useAppActions: () => ({ showNotification: mockShowNotification }),
}));
const AUTH = { Authorization: 'Bearer test-token' };
jest.mock('@/config/api', () => ({
  authFetch: (...a: unknown[]) => (require('@tests/unit/helpers/authFetchMock').authFetchMock as (...x: unknown[]) => unknown)(...a),
  getApiUrl: (p: string) => `http://api.test${p}`,
  getAuthFetchHeaders: (extra?: Record<string, string>) => ({
    ...(extra || {}),
    Authorization: 'Bearer test-token',
  }),
}));

import GeneralSettings from '@/components/settings/GeneralSettings';

const fetchMock = jest.fn();
const DEFAULTS = {
  max_messages: 20,
  include_system_prompts: true,
  clear_on_restart: false,
  unlimited_memory: false,
};

const okJson = (data: unknown) => ({ ok: true, json: async () => data });

function mockLoad(data: unknown = DEFAULTS) {
  fetchMock.mockResolvedValueOnce(okJson(data));
}

function putCalls() {
  return fetchMock.mock.calls.filter(([, init]) => init && init.method === 'PUT');
}

const switchByLabel = (label: string) => {
  const row = screen.getByText(label).closest('li') as HTMLElement;
  return row.querySelector('input[type="checkbox"]') as HTMLInputElement;
};

const maxInput = () =>
  screen.queryAllByLabelText(/Максимум сообщений в контексте/).find((el) => el.tagName === 'INPUT') as
    | HTMLInputElement
    | undefined;

function setMaxMessages(value: string) {
  const input = maxInput()!;
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value } });
  fireEvent.blur(input);
  return input;
}

beforeEach(() => {
  jest.clearAllMocks();
  (global as any).fetch = fetchMock;
  fetchMock.mockReset();
  jest.spyOn(console, 'warn').mockImplementation(() => undefined);
  jest.spyOn(console, 'error').mockImplementation(() => undefined);
});
afterEach(() => {
  jest.restoreAllMocks();
});

describe('GeneralSettings: theme', () => {
  it('reflects the current theme and toggles only when a different theme is picked', async () => {
    mockLoad();
    const onToggle = jest.fn();
    const { rerender } = render(<GeneralSettings isDarkMode={false} onToggleTheme={onToggle} />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());

    const dark = screen.getByLabelText('Темная') as HTMLInputElement;
    const light = screen.getByLabelText('Светлая') as HTMLInputElement;
    expect(light.checked).toBe(true);
    expect(dark.checked).toBe(false);

    fireEvent.click(dark);
    expect(onToggle).toHaveBeenCalledTimes(1);

    rerender(<GeneralSettings isDarkMode onToggleTheme={onToggle} />);
    expect((screen.getByLabelText('Темная') as HTMLInputElement).checked).toBe(true);
    fireEvent.click(screen.getByLabelText('Светлая'));
    expect(onToggle).toHaveBeenCalledTimes(2);
  });
});

describe('GeneralSettings: loading memory settings', () => {
  it('loads settings from the memory endpoint and merges them over the defaults', async () => {
    mockLoad({ max_messages: 60, unlimited_memory: false });
    render(<GeneralSettings isDarkMode={false} onToggleTheme={jest.fn()} />);

    await waitFor(() =>
      expect(maxInput()!.value).toBe('60'),
    );
    expect(fetchMock).toHaveBeenCalledWith('http://api.test/api/memory/settings', { headers: AUTH });
    // пропущенные поля остаются значениями по умолчанию
    expect(switchByLabel('Включать системные промпты в контекст').checked).toBe(true);
    expect(switchByLabel('Очищать память при перезапуске').checked).toBe(false);
  });

  it('keeps defaults when the server answers non-OK', async () => {
    fetchMock.mockResolvedValueOnce({ ok: false, json: async () => ({ max_messages: 99 }) });
    render(<GeneralSettings isDarkMode={false} onToggleTheme={jest.fn()} />);

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(maxInput()!.value).toBe('20');
  });

  it('keeps defaults and only warns when the load request fails', async () => {
    fetchMock.mockRejectedValueOnce(new Error('offline'));
    render(<GeneralSettings isDarkMode={false} onToggleTheme={jest.fn()} />);

    await waitFor(() => expect(console.warn).toHaveBeenCalled());
    expect(maxInput()!.value).toBe('20');
    expect(mockShowNotification).not.toHaveBeenCalled();
  });

  it('shows the derived token estimate (messages x 150) as a read-only field', async () => {
    mockLoad({ max_messages: 40 });
    render(<GeneralSettings isDarkMode={false} onToggleTheme={jest.fn()} />);

    await waitFor(() => {
      const tokens = screen
        .getAllByLabelText(/Размер контекста \(токены\)/)
        .find((el) => el.tagName === 'INPUT') as HTMLInputElement;
      expect(tokens.value).toBe('6000');
      expect(tokens.disabled).toBe(true);
    });
  });
});

describe('GeneralSettings: saving memory settings', () => {
  it('PUTs the full settings object and notifies on success when a switch is toggled', async () => {
    mockLoad();
    render(<GeneralSettings isDarkMode={false} onToggleTheme={jest.fn()} />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    fetchMock.mockResolvedValueOnce({ ok: true });

    fireEvent.click(switchByLabel('Очищать память при перезапуске'));

    await waitFor(() => expect(mockShowNotification).toHaveBeenCalledWith('success', 'Настройки памяти сохранены'));
    const [url, init] = putCalls()[0];
    expect(url).toBe('http://api.test/api/memory/settings');
    expect(init.headers).toEqual({ 'Content-Type': 'application/json', ...AUTH });
    expect(JSON.parse(init.body)).toEqual({ ...DEFAULTS, clear_on_restart: true });
  });

  it('toggling include_system_prompts sends the new value', async () => {
    mockLoad();
    render(<GeneralSettings isDarkMode={false} onToggleTheme={jest.fn()} />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    fetchMock.mockResolvedValueOnce({ ok: true });

    fireEvent.click(switchByLabel('Включать системные промпты в контекст'));

    await waitFor(() => expect(putCalls()).toHaveLength(1));
    expect(JSON.parse(putCalls()[0][1].body).include_system_prompts).toBe(false);
  });

  it('hides the max-messages controls once unlimited memory is switched on', async () => {
    mockLoad();
    render(<GeneralSettings isDarkMode={false} onToggleTheme={jest.fn()} />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    fetchMock.mockResolvedValueOnce({ ok: true });
    expect(maxInput()).toBeDefined();

    const unlimited = screen.getByText('Неограниченная память').closest('li')!.querySelector('input[type="checkbox"]')!;
    fireEvent.click(unlimited);

    await waitFor(() => expect(putCalls()).toHaveLength(1));
    expect(JSON.parse(putCalls()[0][1].body).unlimited_memory).toBe(true);
    expect(maxInput()).toBeUndefined();
  });

  it('shows an error and rolls the switch back when saving fails (BUG-FE-36)', async () => {
    mockLoad();
    render(<GeneralSettings isDarkMode={false} onToggleTheme={jest.fn()} />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    fetchMock.mockResolvedValueOnce({ ok: false });

    fireEvent.click(switchByLabel('Очищать память при перезапуске'));

    await waitFor(() => expect(mockShowNotification).toHaveBeenCalledWith('error', 'Ошибка сохранения настроек памяти'));
    await waitFor(() => expect(switchByLabel('Очищать память при перезапуске').checked).toBe(false));
  });

  it('reports a network error on save through the error notification', async () => {
    mockLoad();
    render(<GeneralSettings isDarkMode={false} onToggleTheme={jest.fn()} />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    fetchMock.mockRejectedValueOnce(new Error('boom'));

    fireEvent.click(switchByLabel('Включать системные промпты в контекст'));

    await waitFor(() => expect(mockShowNotification).toHaveBeenCalledWith('error', 'Ошибка сохранения настроек памяти'));
    expect(mockShowNotification).not.toHaveBeenCalledWith('success', expect.anything());
  });
});

describe('GeneralSettings: max_messages validation', () => {
  async function renderLoaded(data: unknown = DEFAULTS) {
    mockLoad(data);
    render(<GeneralSettings isDarkMode={false} onToggleTheme={jest.fn()} />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    fetchMock.mockResolvedValue({ ok: true });
  }

  it.each([
    ['150', 100, '100'],
    ['1', 5, '5'],
    ['-30', 5, '5'],
    ['42', 42, '42'],
    ['37.9', 37, '37'],
  ])('clamps typed value %s to %i and saves it', async (typed, saved, shown) => {
    await renderLoaded();

    const input = setMaxMessages(typed);

    await waitFor(() => expect(putCalls()).toHaveLength(1));
    expect(JSON.parse(putCalls()[0][1].body).max_messages).toBe(saved);
    expect(input.value).toBe(shown);
  });

  it('falls back to the current value (default) when the field is cleared', async () => {
    await renderLoaded({ max_messages: 45 });
    await waitFor(() =>
      expect(maxInput()!.value).toBe('45'),
    );

    const input = setMaxMessages('');

    await waitFor(() => expect(putCalls()).toHaveLength(1));
    expect(JSON.parse(putCalls()[0][1].body).max_messages).toBe(45);
    expect(input.value).toBe('45');
  });

  it('shows the heavy-memory warning only above 50 messages', async () => {
    await renderLoaded();
    expect(screen.queryByText(/Внимание:/)).toBeNull();

    setMaxMessages('80');

    expect(await screen.findByText(/Внимание:/)).toBeTruthy();
    expect(screen.getByText(/Установлено большое количество сообщений \(80\)/, { exact: false })).toBeTruthy();

    setMaxMessages('50');
    await waitFor(() => expect(screen.queryByText(/Внимание:/)).toBeNull());
  });
});

describe('GeneralSettings: reset', () => {
  it('restores defaults, persists them and informs the user', async () => {
    mockLoad({ max_messages: 80, include_system_prompts: false, clear_on_restart: true, unlimited_memory: false });
    render(<GeneralSettings isDarkMode={false} onToggleTheme={jest.fn()} />);
    await waitFor(() =>
      expect(maxInput()!.value).toBe('80'),
    );
    fetchMock.mockResolvedValueOnce({ ok: true });

    fireEvent.click(screen.getByRole('button', { name: /Восстановить настройки/ }));

    await waitFor(() => expect(putCalls()).toHaveLength(1));
    expect(JSON.parse(putCalls()[0][1].body)).toEqual(DEFAULTS);
    expect(mockShowNotification).toHaveBeenCalledWith('info', 'Настройки памяти сброшены к значениям по умолчанию');
    expect(maxInput()!.value).toBe('20');
    expect(switchByLabel('Очищать память при перезапуске').checked).toBe(false);
    expect(switchByLabel('Включать системные промпты в контекст').checked).toBe(true);
  });

  it('does not claim the reset happened and rolls the form back when persisting it failed (BUG-FE-36)', async () => {
    mockLoad({ max_messages: 80, include_system_prompts: false, clear_on_restart: true, unlimited_memory: false });
    render(<GeneralSettings isDarkMode={false} onToggleTheme={jest.fn()} />);
    await waitFor(() => expect(maxInput()!.value).toBe('80'));
    fetchMock.mockRejectedValueOnce(new Error('offline'));

    fireEvent.click(screen.getByRole('button', { name: /Восстановить настройки/ }));

    await waitFor(() => expect(mockShowNotification).toHaveBeenCalledWith('error', 'Ошибка сохранения настроек памяти'));
    expect(mockShowNotification).not.toHaveBeenCalledWith(
      'info',
      'Настройки памяти сброшены к значениям по умолчанию',
    );
    await waitFor(() => expect(maxInput()!.value).toBe('80'));
    expect(switchByLabel('Очищать память при перезапуске').checked).toBe(true);
  });
});

describe('GeneralSettings: requests carry credentials (BUG-FE-35)', () => {
  it('loads and saves memory settings with an Authorization header', async () => {
    mockLoad();
    render(<GeneralSettings isDarkMode={false} onToggleTheme={jest.fn()} />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    fetchMock.mockResolvedValueOnce({ ok: true });
    fireEvent.click(switchByLabel('Очищать память при перезапуске'));
    await waitFor(() => expect(putCalls()).toHaveLength(1));

    expect(fetchMock.mock.calls[0][1].headers).toEqual(AUTH);
    expect(putCalls()[0][1].headers).toEqual(expect.objectContaining(AUTH));
    expect(putCalls()[0][1].headers['Content-Type']).toBe('application/json');
  });
});

describe('GeneralSettings: help icons', () => {
  it('clicking a help icon next to a control does not toggle it nor trigger a save', async () => {
    mockLoad();
    render(<GeneralSettings isDarkMode={false} onToggleTheme={jest.fn()} />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    const icons = document.querySelectorAll('[data-testid="HelpOutlineIcon"]');
    expect(icons.length).toBeGreaterThanOrEqual(4);
    icons.forEach((icon) => fireEvent.click(icon.closest('button') as HTMLElement));

    expect(putCalls()).toHaveLength(0);
    expect(switchByLabel('Неограниченная память').checked).toBe(false);
    expect(maxInput()!.value).toBe('20');
  });
});
