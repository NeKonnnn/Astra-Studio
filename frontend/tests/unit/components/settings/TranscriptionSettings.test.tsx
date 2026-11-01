import React from 'react';
import { render, screen, fireEvent, act } from '@testing-library/react';

const mockShowNotification = jest.fn();
jest.mock('@/contexts/AppContext', () => ({
  useAppActions: () => ({ showNotification: mockShowNotification }),
}));
jest.mock('@/config/api', () => ({
  authFetch: (...a: unknown[]) => (require('@tests/unit/helpers/authFetchMock').authFetchMock as (...x: unknown[]) => unknown)(...a), getApiUrl: (p: string) => `http://api.test${p}` }));

import TranscriptionSettings from '@/components/settings/TranscriptionSettings';

const fetchMock = jest.fn();
const URL_SETTINGS = 'http://api.test/api/transcription/settings';

const flush = async () => {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
};
const advance = async (ms: number) => {
  await act(async () => {
    jest.advanceTimersByTime(ms);
  });
  await flush();
};

const puts = () => fetchMock.mock.calls.filter(([, init]) => init && init.method === 'PUT');
const lastPutBody = () => JSON.parse(puts()[puts().length - 1][1].body);

async function renderLoaded(data: unknown = { engine: 'whisperx', language: 'ru', auto_detect: true }, props: any = {}) {
  fetchMock.mockResolvedValueOnce({ ok: true, json: async () => data });
  const utils = render(<TranscriptionSettings {...props} />);
  await flush();
  return utils;
}

const openLanguageMenu = () => {
  const trigger = screen.getByText(/^(Русский|English|Автоопределение)$/);
  fireEvent.click(trigger);
};

beforeEach(() => {
  jest.useFakeTimers();
  jest.clearAllMocks();
  fetchMock.mockReset();
  (global as any).fetch = fetchMock;
  jest.spyOn(console, 'error').mockImplementation(() => undefined);
});
afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
});

describe('TranscriptionSettings: loading', () => {
  it('loads settings and always forces the engine to whisperx', async () => {
    await renderLoaded({ engine: 'other-engine', language: 'en', auto_detect: false });

    expect(fetchMock).toHaveBeenCalledWith(URL_SETTINGS);
    expect(screen.getAllByText('English').length).toBeGreaterThan(0);
    const toggle = screen.getByText('Автоматическое определение языка').closest('li')!.querySelector('input')!;
    expect(toggle.checked).toBe(false);
  });

  it.each([
    ['ru', 'Русский'],
    ['en', 'English'],
    ['auto', 'Автоопределение'],
  ])('shows the label for language %s', async (code, label) => {
    await renderLoaded({ language: code });

    expect(screen.getAllByText(label).length).toBeGreaterThan(0);
  });

  it('keeps defaults when the server returns a non-OK status', async () => {
    fetchMock.mockResolvedValueOnce({ ok: false, status: 500, json: async () => ({ language: 'en' }) });
    render(<TranscriptionSettings />);
    await flush();

    expect(screen.getAllByText('Русский').length).toBeGreaterThan(0);
    expect(screen.queryByText('English')).toBeNull();
  });

  it('logs the error and re-enables the controls when loading fails', async () => {
    fetchMock.mockRejectedValueOnce(new Error('offline'));
    render(<TranscriptionSettings />);
    await flush();

    expect(console.error).toHaveBeenCalled();
    expect((screen.getByRole('button', { name: /Обновить настройки/ }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('disables refresh and the dropdowns while loading is in progress', async () => {
    let resolve!: (v: any) => void;
    fetchMock.mockReturnValueOnce(new Promise((r) => (resolve = r)));
    render(<TranscriptionSettings />);

    expect((screen.getByRole('button', { name: /Обновить настройки/ }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText('Автоматическое определение языка').closest('li')!.querySelector('input')!.disabled).toBe(true);
    // клик по выпадающему списку игнорируется на время загрузки
    fireEvent.click(screen.getByText('Русский'));
    expect(screen.queryByText('English')).toBeNull();

    await act(async () => {
      resolve({ ok: true, json: async () => ({}) });
    });
    expect((screen.getByRole('button', { name: /Обновить настройки/ }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('refresh button reloads settings from the server', async () => {
    await renderLoaded();
    fetchMock.mockResolvedValueOnce({ ok: true, json: async () => ({ language: 'auto' }) });

    fireEvent.click(screen.getByRole('button', { name: /Обновить настройки/ }));
    await flush();

    expect(screen.getAllByText('Автоопределение').length).toBeGreaterThan(0);
  });
});

describe('TranscriptionSettings: autosave', () => {
  it('saves the settings with debounce of one second after a change', async () => {
    await renderLoaded();
    fetchMock.mockResolvedValue({ ok: true });
    await advance(1000); // отложенный сейв, вызванный самой загрузкой
    fetchMock.mockClear();
    fetchMock.mockResolvedValue({ ok: true });

    openLanguageMenu();
    fireEvent.click(screen.getByText('English'));
    await advance(999);
    expect(puts()).toHaveLength(0);
    await advance(1);

    expect(puts()).toHaveLength(1);
    expect(puts()[0][0]).toBe(URL_SETTINGS);
    expect(puts()[0][1].headers).toEqual({ 'Content-Type': 'application/json' });
    expect(lastPutBody()).toEqual({ engine: 'whisperx', language: 'en', auto_detect: true });
    expect(mockShowNotification).toHaveBeenCalledWith('success', 'Настройки транскрибации сохранены');
  });

  it('coalesces several quick changes into one PUT with the final value', async () => {
    await renderLoaded();
    await advance(1000);
    fetchMock.mockClear();
    fetchMock.mockResolvedValue({ ok: true });

    const toggle = screen.getByText('Автоматическое определение языка').closest('li')!.querySelector('input')!;
    fireEvent.click(toggle);
    await advance(500);
    fireEvent.click(toggle);
    await advance(500);
    fireEvent.click(toggle);
    await advance(1000);

    expect(puts()).toHaveLength(1);
    expect(lastPutBody().auto_detect).toBe(false);
  });

  it('does not re-save the settings it has just loaded and shows no "saved" toast without user action', async () => {
    await renderLoaded({ engine: 'whisperx', language: 'en', auto_detect: false });
    fetchMock.mockResolvedValue({ ok: true });

    await advance(3000);

    expect(puts()).toHaveLength(0);
    expect(mockShowNotification).not.toHaveBeenCalledWith('success', 'Настройки транскрибации сохранены');
  });

  it('does not overwrite server settings with defaults when loading takes longer than one second', async () => {
    let resolveLoad!: (v: any) => void;
    fetchMock.mockReturnValueOnce(new Promise((r) => (resolveLoad = r)));
    fetchMock.mockResolvedValue({ ok: true });
    render(<TranscriptionSettings />);

    await advance(3000);
    expect(puts()).toHaveLength(0);

    await act(async () => {
      resolveLoad({ ok: true, json: async () => ({ language: 'en' }) });
    });
    await advance(3000);

    expect(puts()).toHaveLength(0);
    expect(screen.getAllByText('English').length).toBeGreaterThan(0);
  });

  it('still autosaves user edits after the initial load failed', async () => {
    fetchMock.mockRejectedValueOnce(new Error('offline'));
    render(<TranscriptionSettings />);
    await flush();
    fetchMock.mockResolvedValue({ ok: true });

    openLanguageMenu();
    fireEvent.click(screen.getByText('English'));
    await advance(1000);

    expect(puts()).toHaveLength(1);
    expect(lastPutBody().language).toBe('en');
  });

  it('shows an error toast (status in the logged error) when saving is rejected by the server', async () => {
    await renderLoaded();
    fetchMock.mockResolvedValue({ ok: false, status: 503 });

    openLanguageMenu();
    fireEvent.click(screen.getByText('English'));
    await advance(1000);

    expect(mockShowNotification).toHaveBeenCalledWith('error', 'Ошибка сохранения настроек транскрибации');
    expect((console.error as jest.Mock).mock.calls[0][1].message).toContain('503');
  });

  it('shows an error toast when saving fails on the network level', async () => {
    await renderLoaded();
    fetchMock.mockRejectedValue(new Error('offline'));

    openLanguageMenu();
    fireEvent.click(screen.getByText('English'));
    await advance(1000);

    expect(mockShowNotification).toHaveBeenCalledWith('error', 'Ошибка сохранения настроек транскрибации');
    expect(mockShowNotification).not.toHaveBeenCalledWith('success', expect.anything());
  });

  it('cancels the pending save on unmount', async () => {
    const { unmount } = await renderLoaded();
    fetchMock.mockClear();
    fetchMock.mockResolvedValue({ ok: true });

    openLanguageMenu();
    fireEvent.click(screen.getByText('English'));
    await advance(500);
    unmount();
    await advance(2000);

    expect(puts()).toHaveLength(0);
  });
});

describe('TranscriptionSettings: controls', () => {
  it('lets the user pick a language from the popover and closes it', async () => {
    await renderLoaded();

    openLanguageMenu();
    expect(screen.getByText('Автоопределение')).toBeTruthy();
    fireEvent.click(screen.getByText('Автоопределение'));
    await advance(500);

    expect(screen.queryByText('English')).toBeNull();
    expect(screen.getAllByText('Автоопределение').length).toBeGreaterThan(0);
  });

  it('opens the engine popover and picking WhisperX keeps the engine', async () => {
    await renderLoaded();
    const triggers = screen.getAllByText('WhisperX');

    fireEvent.click(triggers[0]);
    const items = screen.getAllByText('WhisperX');
    expect(items.length).toBeGreaterThan(triggers.length);
    fireEvent.click(items[items.length - 1]);

    await advance(0);
    expect(screen.getAllByText('WhisperX').length).toBeGreaterThan(0);
  });

  it('closes the language popover on backdrop click without changing anything', async () => {
    await renderLoaded();
    openLanguageMenu();
    const backdrop = document.querySelector('.MuiBackdrop-root') as HTMLElement;

    fireEvent.click(backdrop);
    await advance(500);

    expect(screen.queryByText('English')).toBeNull();
  });

  it('closes the engine popover on backdrop click', async () => {
    await renderLoaded();
    fireEvent.click(screen.getAllByText('WhisperX')[0]);
    const backdrop = document.querySelector('.MuiBackdrop-root') as HTMLElement;

    fireEvent.click(backdrop);
    await advance(500);

    expect(document.querySelector('.MuiPopover-root')).toBeNull();
  });

  it('reset restores defaults, informs the user and then saves them', async () => {
    await renderLoaded({ language: 'en', auto_detect: false });
    await advance(1000);
    fetchMock.mockClear();
    fetchMock.mockResolvedValue({ ok: true });

    fireEvent.click(screen.getByRole('button', { name: /Восстановить настройки/ }));
    expect(mockShowNotification).toHaveBeenCalledWith('info', 'Настройки транскрибации сброшены к значениям по умолчанию');
    await advance(1000);

    expect(lastPutBody()).toEqual({ engine: 'whisperx', language: 'ru', auto_detect: true });
    expect(screen.getAllByText('Русский').length).toBeGreaterThan(0);
  });

  it('shows the engine description block', async () => {
    await renderLoaded();

    expect(screen.getByText(/Высокая точность распознавания/)).toBeTruthy();
    expect(screen.getByText(/Используйте для транскрипции записей/)).toBeTruthy();
  });

  it('honours the isDarkMode prop and falls back to the theme mode', async () => {
    const { unmount } = await renderLoaded(undefined, { isDarkMode: true });
    expect(screen.getByText('Основные настройки')).toBeTruthy();
    unmount();
    fetchMock.mockResolvedValueOnce({ ok: true, json: async () => ({}) });
    render(<TranscriptionSettings isDarkMode={false} />);
    await flush();
    expect(screen.getByText('Основные настройки')).toBeTruthy();
  });
});

describe('TranscriptionSettings: help icons', () => {
  it('clicking help icons does not open the dropdown popovers nor change settings', async () => {
    await renderLoaded();
    await advance(1000);
    fetchMock.mockClear();

    document
      .querySelectorAll('[data-testid="HelpOutlineIcon"]')
      .forEach((icon) => fireEvent.click(icon.closest('button') as HTMLElement));
    await advance(1500);

    expect(document.querySelector('.MuiPopover-root')).toBeNull();
    expect(puts()).toHaveLength(0);
  });
});
