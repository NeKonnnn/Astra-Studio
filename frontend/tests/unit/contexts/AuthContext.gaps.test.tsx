import React from 'react';
import { act, renderHook, waitFor } from '@testing-library/react';

jest.mock('axios', () => {
  const instance: any = jest.fn();
  instance.get = jest.fn();
  instance.post = jest.fn();
  instance.defaults = { headers: { common: {} } };
  instance.interceptors = { response: { use: jest.fn(() => 7), eject: jest.fn() } };
  return { __esModule: true, default: instance };
});

jest.mock('@/settings', () => ({
  ...jest.requireActual('@/settings'),
  initSettings: jest.fn().mockResolvedValue(undefined),
}));

jest.mock('@/config/api', () => ({
  getApiUrl: (path: string) => `http://api.test${path}`,
  fetchSessionPolicy: jest.fn().mockResolvedValue({ timeoutSeconds: 600 }),
}));

import axios from 'axios';
import { initSettings } from '@/settings';
import { AuthProvider, useAuth } from '@/contexts/AuthContext';

const http: any = axios;
const wrapper = ({ children }: { children: React.ReactNode }) => <AuthProvider>{children}</AuthProvider>;

const USER = {
  username: 'alice',
  email: null,
  full_name: 'Alice',
  is_active: true,
  is_admin: false,
  avatar_url: null,
};

const b64url = (obj: unknown) =>
  btoa(JSON.stringify(obj)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
const jwt = (expInSeconds: number) => `h.${b64url({ exp: Math.floor(Date.now() / 1000) + expInSeconds })}.s`;

const LOGIN_OK = (overrides: Record<string, unknown> = {}) => ({
  data: {
    access_token: jwt(3600),
    refresh_token: 'refresh-1',
    user: USER,
    session_policy: { timeout_seconds: 900 },
    server_instance_id: 'srv-1',
    ...overrides,
  },
});

const httpError = (status: number, data: unknown = {}) =>
  Object.assign(new Error(`HTTP ${status}`), { response: { status, data } });

async function mounted() {
  const hook = renderHook(() => useAuth(), { wrapper });
  await waitFor(() => expect(hook.result.current.isLoading).toBe(false));
  return hook;
}

function seedSession(token: string, refresh: string | null = 'r') {
  localStorage.setItem('auth_token', token);
  if (refresh) localStorage.setItem('auth_refresh_token', refresh);
  localStorage.setItem('auth_user', JSON.stringify(USER));
}

beforeEach(() => {
  jest.clearAllMocks();
  localStorage.clear();
  sessionStorage.clear();
  http.get.mockReset();
  http.post.mockReset();
  http.mockReset();
  http.defaults.headers.common = {};
  http.interceptors.response.use.mockImplementation(() => 7);
  (initSettings as jest.Mock).mockResolvedValue(undefined);
  jest.spyOn(console, 'warn').mockImplementation(() => undefined);
  jest.spyOn(console, 'error').mockImplementation(() => undefined);
});

afterEach(() => {
  jest.restoreAllMocks();
  jest.useRealTimers();
  delete process.env.REACT_APP_API_URL;
});

describe('AuthContext: недостающие ветки', () => {
  it('REACT_APP_API_URL заменяет базовый адрес и убирает хвостовой слэш', async () => {
    process.env.REACT_APP_API_URL = 'http://env.test/';
    http.post.mockResolvedValue(LOGIN_OK());
    const { result } = await mounted();
    await act(async () => {
      await result.current.login('alice', 'pw');
    });
    expect(http.post.mock.calls[0][0]).toBe('http://env.test/api/auth/login');
  });

  it('401 с объектом detail: маркер роли в detail.error/detail.detail даёт сообщение об отсутствии роли', async () => {
    http.post.mockRejectedValueOnce(httpError(401, { detail: { error: 'Insufficient permissions' } }));
    const { result } = await mounted();
    await expect(result.current.login('a', 'b')).rejects.toThrow('У вас нет необходимой роли');
    http.post.mockRejectedValueOnce(httpError(401, { detail: { message: 'Неверно', detail: 'нет необходимой роли' } }));
    await expect(result.current.login('a', 'b')).rejects.toThrow('У вас нет необходимой роли');
  });

  it('401: одиночное слово forbidden в detail без кода/фразы роли (BUG-FE-06) не считается отсутствием роли', async () => {
    http.post.mockRejectedValueOnce(httpError(401, { detail: { message: 'Неверно', detail: 'forbidden' } }));
    const { result } = await mounted();
    await expect(result.current.login('a', 'b')).rejects.toThrow('Неверно');
  });

  it('401 с объектом detail без маркеров сохраняет текст сервера', async () => {
    http.post.mockRejectedValueOnce(httpError(401, { detail: { message: 'Неверные данные', remaining_attempts: 2 } }));
    const { result } = await mounted();
    await expect(result.current.login('a', 'b')).rejects.toMatchObject({
      message: 'Неверные данные',
      meta: { remainingAttempts: 2 },
    });
  });

  it('JWT без полезной нагрузки или с битой нагрузкой не считается просроченным', async () => {
    http.get.mockResolvedValue({ data: { valid: true } });
    seedSession('просто-строка');
    const first = await mounted();
    expect(first.result.current.isAuthenticated).toBe(true);
    first.unmount();
    seedSession('h.@@@.s');
    const second = await mounted();
    expect(second.result.current.isAuthenticated).toBe(true);
    expect(http.post).not.toHaveBeenCalled();
  });

  it('refresh возвращает ответ без токенов: сессия сбрасывается', async () => {
    seedSession(jwt(-60));
    http.post.mockResolvedValue({ data: { access_token: 'x' } });
    const { result } = await mounted();
    expect(result.current.isAuthenticated).toBe(false);
    expect(localStorage.getItem('auth_token')).toBeNull();
  });

  it('успешный refresh при старте кэширует server_instance_id и обновляет пользователя', async () => {
    seedSession(jwt(-60));
    const fresh = jwt(3600);
    http.post.mockResolvedValue({
      data: { access_token: fresh, refresh_token: 'r2', user: { ...USER, full_name: 'Новое имя' }, server_instance_id: 'srv-7' },
    });
    http.get.mockResolvedValue({ data: { valid: true } });
    const { result } = await mounted();
    expect(result.current.token).toBe(fresh);
    expect(result.current.user?.full_name).toBe('Новое имя');
    expect(sessionStorage.getItem('auth_server_instance_id')).toBe('srv-7');
    expect(localStorage.getItem('auth_refresh_token')).toBe('r2');
  });

  it('401 при проверке, затем неудачный refresh: сессия очищается', async () => {
    seedSession(jwt(3600));
    http.get.mockRejectedValue(httpError(401, { detail: 'Token expired' }));
    http.post.mockRejectedValue(httpError(401, { detail: 'Refresh expired' }));
    const { result } = await mounted();
    expect(result.current.isAuthenticated).toBe(false);
    expect(localStorage.getItem('auth_refresh_token')).toBeNull();
  });

  it('жёсткий таймаут инициализации (20 с) снимает экран загрузки', async () => {
    jest.useFakeTimers();
    (initSettings as jest.Mock).mockReturnValue(new Promise(() => {}));
    const { result } = renderHook(() => useAuth(), { wrapper });
    expect(result.current.isLoading).toBe(true);
    act(() => {
      jest.advanceTimersByTime(19999);
    });
    expect(result.current.isLoading).toBe(true);
    act(() => {
      jest.advanceTimersByTime(2);
    });
    expect(result.current.isLoading).toBe(false);
    expect(console.warn).toHaveBeenCalledWith('Таймаут инициализации auth — снимаем экран загрузки');
  });

  it('размонтирование до завершения инициализации не обновляет состояние', async () => {
    let resolve!: () => void;
    (initSettings as jest.Mock).mockReturnValue(new Promise<void>((r) => (resolve = r)));
    seedSession(jwt(3600));
    http.get.mockResolvedValue({ data: { valid: true } });
    const hook = renderHook(() => useAuth(), { wrapper });
    hook.unmount();
    await act(async () => resolve());
    expect(console.error).not.toHaveBeenCalled();
  });

  it('login продолжается, если initSettings падает', async () => {
    http.post.mockResolvedValue(LOGIN_OK());
    const { result } = await mounted();
    (initSettings as jest.Mock).mockRejectedValueOnce(new Error('cfg'));
    await act(async () => {
      await result.current.login('alice', 'pw');
    });
    expect(result.current.isAuthenticated).toBe(true);
    expect(console.warn).toHaveBeenCalledWith(expect.stringContaining('перед login'), expect.any(Error));
  });

  it('SSO-обмен продолжается, если initSettings падает', async () => {
    http.post.mockResolvedValue(LOGIN_OK());
    const { result } = await mounted();
    (initSettings as jest.Mock).mockRejectedValueOnce(new Error('cfg'));
    await act(async () => {
      await result.current.completeSsoLogin('ticket-1');
    });
    expect(http.post.mock.calls[0][1]).toEqual({ ticket: 'ticket-1' });
    expect(result.current.isAuthenticated).toBe(true);
    expect(console.warn).toHaveBeenCalledWith(expect.stringContaining('перед SSO exchange'), expect.any(Error));
  });

  it('успешный ответ перехватчика возвращается без изменений', async () => {
    await mounted();
    const calls = http.interceptors.response.use.mock.calls;
    const onFulfilled = calls[calls.length - 1][0];
    const response = { data: 1 };
    expect(onFulfilled(response)).toBe(response);
  });
});

// Пропущено: «AuthContext: фото профиля» (avatarSrc / blob) — в memo нет Astra avatar pipeline.
