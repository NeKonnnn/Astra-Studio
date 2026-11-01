import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import LoginPage from '@/pages/LoginPage';

const mockNavigate = jest.fn();
const mockLogin = jest.fn();
const mockInitSettings = jest.fn();
const mockFetchPolicy = jest.fn();
const mockFormatHint = jest.fn();

jest.mock('react-router-dom', () => ({ useNavigate: () => mockNavigate }));
jest.mock('@/contexts/AuthContext', () => {
  class AuthLoginError extends Error {
    meta: any;
    constructor(message: string, meta: any = {}) {
      super(message);
      this.meta = meta;
    }
  }
  return { AuthLoginError, useAuth: () => ({ login: mockLogin }) };
});
jest.mock('@/settings', () => ({
  initSettings: () => mockInitSettings(),
  formatRemainingAttemptsHint: (...a: any[]) => mockFormatHint(...a),
  LOGIN_LOCKOUT_EXHAUSTED_MESSAGE: 'Аккаунт временно заблокирован',
}));
jest.mock('@/config/api', () => ({
  authFetch: (...a: unknown[]) => (require('@tests/unit/helpers/authFetchMock').authFetchMock as (...x: unknown[]) => unknown)(...a),
  fetchLoginLockoutPolicy: () => mockFetchPolicy(),
  getApiUrl: (p: string) => `http://api${p}`,
  API_ENDPOINTS: {
    AUTH_SSO_PROVIDERS: '/api/auth/sso/providers',
    AUTH_SSO_KEYCLOAK_LOGIN: '/api/auth/sso/keycloak/login',
  },
}));
jest.mock('@/components/LoginSessionNotice', () => ({
  __esModule: true,
  default: () => <div data-testid="notice" />,
}));

// eslint-disable-next-line import/first
import { AuthLoginError } from '@/contexts/AuthContext';

const fetchMock = jest.fn();
const originalLocation = window.location;

function ssoResponse(data: any, ok = true, status = 200) {
  return { ok, status, json: async () => data };
}

async function renderPage() {
  const utils = render(<LoginPage />);
  // дождаться завершения асинхронной инициализации (initSettings → policy → sso)
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
  return utils;
}

function submit(user = 'ivan', pass = 'pw') {
  fireEvent.change(screen.getByLabelText(/Имя пользователя/), { target: { value: user } });
  fireEvent.change(screen.getByLabelText(/Пароль/), { target: { value: pass } });
  fireEvent.click(screen.getByRole('button', { name: 'Войти' }));
}

describe('LoginPage', () => {
  beforeEach(() => {
    localStorage.clear();
    mockNavigate.mockReset();
    mockLogin.mockReset();
    mockInitSettings.mockReset().mockResolvedValue(undefined);
    mockFetchPolicy.mockReset().mockResolvedValue({ maxFailedAttempts: 5, lockoutDurationSeconds: 600 });
    mockFormatHint.mockReset().mockImplementation((r, m, s) => `осталось ${r} из ${m}, блок ${s}`);
    fetchMock.mockReset().mockResolvedValue(ssoResponse({ keycloak_enabled: false }));
    (global as any).fetch = fetchMock;
    jest.spyOn(console, 'warn').mockImplementation(() => {});
  });
  afterEach(() => {
    jest.restoreAllMocks();
    Object.defineProperty(window, 'location', { configurable: true, value: originalLocation });
  });

  it('рендерит форму; SSO-кнопки нет, если провайдер выключен', async () => {
    await renderPage();
    expect(screen.getByText('Вход в систему')).toBeTruthy();
    expect(screen.getByTestId('notice')).toBeTruthy();
    expect(screen.queryByLabelText('Войти через SSO')).toBeNull();
    expect(fetchMock).toHaveBeenCalledWith('http://api/api/auth/sso/providers');
  });

  it('успешный вход вызывает login и ведёт на главную', async () => {
    mockLogin.mockResolvedValue(undefined);
    await renderPage();
    submit('ivan', 'secret');
    await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith('/'));
    expect(mockLogin).toHaveBeenCalledWith('ivan', 'secret');
  });

  it('во время входа кнопка заменяется спиннером и поля блокируются', async () => {
    let resolve!: () => void;
    mockLogin.mockReturnValue(new Promise<void>((r) => { resolve = r; }));
    await renderPage();
    submit();
    await waitFor(() => expect((screen.getByLabelText(/Пароль/) as HTMLInputElement).disabled).toBe(true));
    expect(screen.queryByText('Войти')).toBeNull();
    await act(async () => { resolve(); });
  });

  it('AuthLoginError с retryAfterSeconds показывает сообщение о блокировке без подсказки', async () => {
    mockLogin.mockRejectedValue(new AuthLoginError('x', { retryAfterSeconds: 30, remainingAttempts: 1, maxFailedAttempts: 5 }));
    await renderPage();
    submit();
    expect(await screen.findByText('Аккаунт временно заблокирован')).toBeTruthy();
    expect(mockFormatHint).not.toHaveBeenCalled();
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it('AuthLoginError с оставшимися попытками показывает подсказку из меты', async () => {
    mockLogin.mockRejectedValue(
      new AuthLoginError('любое', { remainingAttempts: 2, maxFailedAttempts: 4, lockoutDurationSeconds: 120 }),
    );
    await renderPage();
    submit();
    expect(await screen.findByText('Неверное имя пользователя или пароль')).toBeTruthy();
    expect(screen.getByText('осталось 2 из 4, блок 120')).toBeTruthy();
    expect(mockFormatHint).toHaveBeenCalledWith(2, 4, 120);
  });

  it('если мета без max/lock, берутся значения политики с backend', async () => {
    mockLogin.mockRejectedValue(new AuthLoginError('любое', { remainingAttempts: 3 }));
    await renderPage();
    submit();
    expect(await screen.findByText('осталось 3 из 5, блок 600')).toBeTruthy();
  });

  it('без политики длительность блокировки по умолчанию 900 с, а без max подсказки нет', async () => {
    mockFetchPolicy.mockRejectedValue(new Error('no policy'));
    mockLogin.mockRejectedValue(new AuthLoginError('любое', { remainingAttempts: 3 }));
    await renderPage();
    submit();
    expect(await screen.findByText('Неверное имя пользователя или пароль')).toBeTruthy();
    expect(mockFormatHint).not.toHaveBeenCalled();
    // с max в мете, но без политики — 900 по умолчанию
    mockLogin.mockRejectedValue(new AuthLoginError('любое', { remainingAttempts: 1, maxFailedAttempts: 3 }));
    fireEvent.click(screen.getByRole('button', { name: 'Войти' }));
    await waitFor(() => expect(mockFormatHint).toHaveBeenCalledWith(1, 3, 900));
  });

  it('сообщение «Сервис аутентификации временно недоступен» показывается как есть', async () => {
    mockLogin.mockRejectedValue(new AuthLoginError('Сервис аутентификации временно недоступен'));
    await renderPage();
    submit();
    expect(await screen.findByText('Сервис аутентификации временно недоступен')).toBeTruthy();
  });

  it('обычная Error показывает свой текст, не-Error — общий', async () => {
    mockLogin.mockRejectedValueOnce(new Error('Нет роли'));
    await renderPage();
    submit();
    expect(await screen.findByText('Нет роли')).toBeTruthy();
    mockLogin.mockRejectedValueOnce('str');
    fireEvent.click(screen.getByRole('button', { name: 'Войти' }));
    expect(await screen.findByText('Ошибка при входе в систему')).toBeTruthy();
  });

  it('новая попытка входа сбрасывает прошлую ошибку', async () => {
    mockLogin.mockRejectedValueOnce(new Error('первая'));
    await renderPage();
    submit();
    await screen.findByText('первая');
    let resolve!: () => void;
    mockLogin.mockReturnValueOnce(new Promise<void>((r) => { resolve = r; }));
    fireEvent.click(screen.getByRole('button', { name: 'Войти' }));
    await waitFor(() => expect(screen.queryByText('первая')).toBeNull());
    await act(async () => { resolve(); });
  });

  it('кнопка-глаз переключает видимость пароля', async () => {
    await renderPage();
    const pw = screen.getByLabelText(/Пароль/) as HTMLInputElement;
    expect(pw.type).toBe('password');
    const toggle = pw.closest('.MuiInputBase-root')!.querySelector('button')!;
    fireEvent.click(toggle);
    expect(pw.type).toBe('text');
    fireEvent.click(toggle);
    expect(pw.type).toBe('password');
  });

  it('переключатель темы сохраняет выбор и красит body', async () => {
    await renderPage();
    expect(document.body.style.backgroundColor).toBe('rgb(245, 245, 245)');
    const themeBtn = screen.getAllByRole('button')[0];
    fireEvent.click(themeBtn);
    expect(localStorage.getItem('theme')).toBe('dark');
    expect(document.body.style.backgroundColor).toBe('rgb(18, 18, 18)');
    fireEvent.click(screen.getAllByRole('button')[0]);
    expect(localStorage.getItem('theme')).toBe('light');
  });

  it('сохранённая тёмная тема подхватывается при старте, а при размонтировании фон body сбрасывается', async () => {
    localStorage.setItem('theme', 'dark');
    const { unmount } = await renderPage();
    expect(document.body.style.backgroundColor).toBe('rgb(18, 18, 18)');
    unmount();
    expect(document.body.style.backgroundColor).toBe('');
  });

  it('сбой initSettings и политики не мешает отрисовке (предупреждения в консоль)', async () => {
    mockInitSettings.mockRejectedValue(new Error('cfg'));
    mockFetchPolicy.mockRejectedValue(new Error('pol'));
    await renderPage();
    expect(screen.getByText('Вход в систему')).toBeTruthy();
    expect(console.warn).toHaveBeenCalledTimes(2);
  });

  it('HTTP-ошибка SSO-провайдеров оставляет только парольный вход', async () => {
    fetchMock.mockResolvedValue(ssoResponse({}, false, 502));
    await renderPage();
    expect(screen.queryByLabelText('Войти через SSO')).toBeNull();
    expect(console.warn).toHaveBeenCalled();
  });

  it('сетевой сбой SSO-провайдеров не ломает страницу', async () => {
    fetchMock.mockRejectedValue(new Error('net'));
    await renderPage();
    expect(screen.getByText('Вход в систему')).toBeTruthy();
    expect(screen.queryByLabelText('Войти через SSO')).toBeNull();
  });

  it('включённый Keycloak показывает SSO-кнопку; клик переводит на URL из ответа', async () => {
    fetchMock.mockResolvedValue(ssoResponse({ keycloak_enabled: true, keycloak_login_url: 'http://kc/login' }));
    const loc: any = { href: 'http://app/login' };
    Object.defineProperty(window, 'location', { configurable: true, value: loc });
    await renderPage();
    fireEvent.click(screen.getByLabelText('Войти через SSO'));
    expect(loc.href).toBe('http://kc/login');
    expect(screen.getByText('Переход к SSO…')).toBeTruthy();
    expect((screen.getByLabelText('Войти через SSO') as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'Войти' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('без keycloak_login_url берётся стандартный endpoint backend', async () => {
    fetchMock.mockResolvedValue(ssoResponse({ keycloak_enabled: true }));
    const loc: any = { href: 'x' };
    Object.defineProperty(window, 'location', { configurable: true, value: loc });
    await renderPage();
    fireEvent.click(screen.getByLabelText('Войти через SSO'));
    expect(loc.href).toBe('http://api/api/auth/sso/keycloak/login');
  });

  it('размонтирование до ответа политики/SSO не приводит к обновлению состояния', async () => {
    let resolvePolicy!: (v: any) => void;
    mockFetchPolicy.mockReturnValue(new Promise((r) => { resolvePolicy = r; }));
    fetchMock.mockResolvedValue(ssoResponse({ keycloak_enabled: true, keycloak_login_url: 'u' }));
    const errSpy = jest.spyOn(console, 'error').mockImplementation(() => {});
    const { unmount } = render(<LoginPage />);
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    unmount();
    await act(async () => { resolvePolicy({ maxFailedAttempts: 1, lockoutDurationSeconds: 1 }); });
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(errSpy).not.toHaveBeenCalled();
  });

  it('при cancelled ответ SSO с keycloak_enabled игнорируется', async () => {
    let resolveSso!: (v: any) => void;
    fetchMock.mockReturnValue(new Promise((r) => { resolveSso = r; }));
    const { unmount } = render(<LoginPage />);
    await act(async () => { for (let i = 0; i < 6; i++) await Promise.resolve(); });
    unmount();
    await act(async () => { resolveSso(ssoResponse({ keycloak_enabled: true })); });
    expect(document.body.textContent).not.toContain('SSO');
  });
});
