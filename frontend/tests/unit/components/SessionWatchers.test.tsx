import React from 'react';
import { act, render } from '@testing-library/react';
import SessionTimeoutWatcher from '@/components/SessionTimeoutWatcher';
import SessionValidityWatcher from '@/components/SessionValidityWatcher';
import { AUTH_SESSION_POLICY_KEY, AUTH_SESSION_STARTED_KEY } from '@/settings/sessionTimeout';
import { AUTH_SERVER_INSTANCE_KEY, LOGIN_SESSION_NOTICE_KEY } from '@/settings/sessionValidity';

const mockNavigate = jest.fn();
jest.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate,
}));

let mockAuth: any;
jest.mock('@/contexts/AuthContext', () => ({
  useAuth: () => mockAuth,
}));

let mockSocket = { isConnected: false };
jest.mock('@/contexts/SocketContext', () => ({
  useSocket: () => mockSocket,
}));

const mockInitSettings = jest.fn();
jest.mock('@/settings', () => ({
  initSettings: () => mockInitSettings(),
}));

const mockFetchPolicy = jest.fn();
jest.mock('@/config/api', () => ({
  authFetch: (...a: unknown[]) => (require('@tests/unit/helpers/authFetchMock').authFetchMock as (...x: unknown[]) => unknown)(...a),
  getApiUrl: (p: string) => `http://api.test${p}`,
  fetchSessionPolicy: () => mockFetchPolicy(),
}));

const mockAxiosGet = jest.fn();
jest.mock('axios', () => ({
  __esModule: true,
  default: { get: (...a: unknown[]) => mockAxiosGet(...a) },
}));

const NOW = new Date('2025-03-01T10:00:00.000Z');

function setVisibility(state: 'visible' | 'hidden') {
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => state });
}

beforeEach(() => {
  jest.useFakeTimers();
  jest.setSystemTime(NOW);
  sessionStorage.clear();
  localStorage.clear();
  mockNavigate.mockReset();
  mockInitSettings.mockReset().mockResolvedValue(undefined);
  mockFetchPolicy.mockReset();
  mockAxiosGet.mockReset();
  mockAuth = { isAuthenticated: true, isLoading: false, logout: jest.fn().mockResolvedValue(undefined), token: 'tok' };
  mockSocket = { isConnected: false };
  setVisibility('visible');
  jest.spyOn(console, 'error').mockImplementation(() => {});
  jest.spyOn(console, 'warn').mockImplementation(() => {});
});

afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
  delete process.env.REACT_APP_API_URL;
});

const flush = async () => {
  await act(async () => {
    for (let i = 0; i < 6; i += 1) await Promise.resolve();
  });
};

/** Через час после старта автовыхода быть не должно (ни таймера, ни опроса). */
async function quiet() {
  jest.setSystemTime(new Date(NOW.getTime() + 24 * 3600_000));
  await act(async () => {
    jest.advanceTimersByTime(3 * 3600_000);
  });
  expect(mockAuth.logout).not.toHaveBeenCalled();
}

function seedPolicy(timeoutSeconds: number, enabled = true) {
  sessionStorage.setItem(AUTH_SESSION_POLICY_KEY, JSON.stringify({ enabled, timeoutSeconds }));
  sessionStorage.setItem(AUTH_SESSION_STARTED_KEY, String(Date.now()));
}

describe('SessionTimeoutWatcher', () => {
  it('для неавторизованного или загружающегося пользователя ничего не делает', async () => {
    mockAuth = { ...mockAuth, isAuthenticated: false };
    render(<SessionTimeoutWatcher />);
    await flush();
    expect(mockFetchPolicy).not.toHaveBeenCalled();
    await quiet();
    mockAuth = { ...mockAuth, isAuthenticated: true, isLoading: true };
    render(<SessionTimeoutWatcher />);
    await flush();
    await quiet();
  });

  it('если политики нет в кэше, запрашивает и кэширует её; выключенная политика таймеров не ставит', async () => {
    mockFetchPolicy.mockResolvedValue({ enabled: false, timeoutSeconds: 0 });
    render(<SessionTimeoutWatcher />);
    await flush();
    expect(mockInitSettings).toHaveBeenCalledTimes(1);
    expect(mockFetchPolicy).toHaveBeenCalledTimes(1);
    expect(JSON.parse(sessionStorage.getItem(AUTH_SESSION_POLICY_KEY)!)).toEqual({ enabled: false, timeoutSeconds: 0 });
    await quiet();
  });

  it('по истечении времени выходит из системы и переходит на /login', async () => {
    seedPolicy(60);
    render(<SessionTimeoutWatcher />);
    await flush();
    expect(mockFetchPolicy).not.toHaveBeenCalled();
    await act(async () => {
      jest.advanceTimersByTime(59_000);
    });
    expect(mockAuth.logout).not.toHaveBeenCalled();
    await act(async () => {
      jest.advanceTimersByTime(1_500);
    });
    expect(mockAuth.logout).toHaveBeenCalledTimes(1);
    expect(mockNavigate).toHaveBeenCalledWith('/login', { replace: true });
    expect(sessionStorage.getItem(AUTH_SESSION_STARTED_KEY)).toBeNull();
  });

  it('если срок уже вышел к моменту планирования, выход происходит сразу', async () => {
    sessionStorage.setItem(AUTH_SESSION_POLICY_KEY, JSON.stringify({ enabled: true, timeoutSeconds: 10 }));
    sessionStorage.setItem(AUTH_SESSION_STARTED_KEY, String(Date.now() - 60_000));
    render(<SessionTimeoutWatcher />);
    await flush();
    expect(mockAuth.logout).toHaveBeenCalledTimes(1);
    expect(mockNavigate).toHaveBeenCalledWith('/login', { replace: true });
  });

  it('опрос раз в секунду ловит скачок часов раньше таймера', async () => {
    seedPolicy(3600);
    render(<SessionTimeoutWatcher />);
    await flush();
    jest.setSystemTime(new Date(NOW.getTime() + 3600_000 + 5000));
    await act(async () => {
      jest.advanceTimersByTime(1000);
    });
    expect(mockAuth.logout).toHaveBeenCalledTimes(1);
  });

  it('повторные сигналы во время выхода не вызывают logout дважды', async () => {
    seedPolicy(3600);
    let finish!: () => void;
    mockAuth.logout = jest.fn(() => new Promise<void>((r) => (finish = r)));
    render(<SessionTimeoutWatcher />);
    await flush();
    jest.setSystemTime(new Date(NOW.getTime() + 3600_000 + 5000));
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
      document.dispatchEvent(new Event('visibilitychange'));
    });
    expect(mockAuth.logout).toHaveBeenCalledTimes(1);
    await act(async () => finish());
    expect(mockNavigate).toHaveBeenCalledTimes(1);
  });

  it('при возвращении на вкладку проверяет срок; скрытая вкладка и валидная сессия игнорируются', async () => {
    seedPolicy(3600);
    render(<SessionTimeoutWatcher />);
    await flush();
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    expect(mockAuth.logout).not.toHaveBeenCalled();
    jest.setSystemTime(new Date(NOW.getTime() + 3600_000 + 5000));
    setVisibility('hidden');
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    expect(mockAuth.logout).not.toHaveBeenCalled();
    setVisibility('visible');
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    expect(mockAuth.logout).toHaveBeenCalledTimes(1);
  });

  it('при выключенной политике смена видимости ничего не делает', async () => {
    mockFetchPolicy.mockResolvedValue({ enabled: false, timeoutSeconds: 0 });
    render(<SessionTimeoutWatcher />);
    await flush();
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    expect(mockAuth.logout).not.toHaveBeenCalled();
  });

  it('ошибка получения политики логируется, автовыход отключён', async () => {
    mockFetchPolicy.mockRejectedValue(new Error('500'));
    render(<SessionTimeoutWatcher />);
    await flush();
    expect(console.error).toHaveBeenCalledWith(expect.stringContaining('Не удалось применить политику сессии'), expect.any(Error));
    await quiet();
  });

  it('размонтирование до ответа политики и очистка таймеров', async () => {
    let resolve!: (v: unknown) => void;
    mockFetchPolicy.mockReturnValue(new Promise((r) => (resolve = r)));
    const first = render(<SessionTimeoutWatcher />);
    first.unmount();
    await act(async () => resolve({ enabled: true, timeoutSeconds: 30 }));
    await quiet();

    seedPolicy(60);
    const second = render(<SessionTimeoutWatcher />);
    await flush();
    second.unmount();
    await quiet();
  });

  it('смена пользователя на неавторизованного снимает таймеры', async () => {
    seedPolicy(60);
    const { rerender } = render(<SessionTimeoutWatcher />);
    await flush();
    mockAuth = { ...mockAuth, isAuthenticated: false };
    rerender(<SessionTimeoutWatcher />);
    await quiet();
  });
});

describe('SessionValidityWatcher', () => {
  const instanceOk = (id: string) => ({ data: { instance_id: id } });

  it('без авторизации проверок нет', async () => {
    mockAuth = { ...mockAuth, isAuthenticated: false };
    render(<SessionValidityWatcher />);
    await flush();
    expect(mockAxiosGet).not.toHaveBeenCalled();
    mockAuth = { ...mockAuth, isAuthenticated: true, isLoading: true };
    render(<SessionValidityWatcher />);
    await flush();
    expect(mockAxiosGet).not.toHaveBeenCalled();
  });

  it('первая проверка кэширует instance_id и подтверждает токен', async () => {
    mockAxiosGet.mockResolvedValueOnce(instanceOk('srv-1')).mockResolvedValueOnce({ data: {} });
    render(<SessionValidityWatcher />);
    await flush();
    expect(mockInitSettings).toHaveBeenCalled();
    expect(mockAxiosGet.mock.calls[0][0]).toBe('http://api.test/api/auth/server-instance');
    expect(mockAxiosGet.mock.calls[1][0]).toBe('http://api.test/api/auth/verify');
    expect(mockAxiosGet.mock.calls[1][1].headers.Authorization).toBe('Bearer tok');
    expect(sessionStorage.getItem(AUTH_SERVER_INSTANCE_KEY)).toBe('srv-1');
    expect(mockAuth.logout).not.toHaveBeenCalled();
  });

  it('REACT_APP_API_URL переопределяет адрес', async () => {
    process.env.REACT_APP_API_URL = 'http://env.test/';
    mockAxiosGet.mockResolvedValue({ data: {} });
    render(<SessionValidityWatcher />);
    await flush();
    expect(mockAxiosGet.mock.calls[0][0]).toBe('http://env.test/api/auth/server-instance');
  });

  it('смена instance_id (рестарт backend) завершает сессию с причиной server_restart', async () => {
    sessionStorage.setItem(AUTH_SERVER_INSTANCE_KEY, 'old');
    mockAxiosGet.mockResolvedValueOnce(instanceOk('new'));
    render(<SessionValidityWatcher />);
    await flush();
    expect(mockAuth.logout).toHaveBeenCalledTimes(1);
    expect(mockNavigate).toHaveBeenCalledWith('/login', { replace: true });
    expect(sessionStorage.getItem(LOGIN_SESSION_NOTICE_KEY)).toBe('server_restart');
    expect(mockAxiosGet).toHaveBeenCalledTimes(1); // verify уже не вызывается
  });

  it('совпадающий instance_id и пустой instance_id не вызывают выхода', async () => {
    sessionStorage.setItem(AUTH_SERVER_INSTANCE_KEY, 'same');
    mockAxiosGet.mockResolvedValueOnce(instanceOk('same')).mockResolvedValueOnce({ data: {} });
    render(<SessionValidityWatcher />);
    await flush();
    expect(mockAuth.logout).not.toHaveBeenCalled();
    mockAxiosGet.mockReset();
    mockAxiosGet.mockResolvedValueOnce({ data: {} }).mockResolvedValueOnce({ data: {} });
    await act(async () => {
      jest.advanceTimersByTime(30_000);
    });
    expect(mockAuth.logout).not.toHaveBeenCalled();
    expect(mockAxiosGet).toHaveBeenCalledTimes(2);
  });

  it('401 при проверке токена завершает сессию с причиной из detail', async () => {
    mockAxiosGet
      .mockResolvedValueOnce(instanceOk('srv'))
      .mockRejectedValueOnce({ response: { status: 401, data: { detail: 'Server restarted' } } });
    render(<SessionValidityWatcher />);
    await flush();
    expect(mockAuth.logout).toHaveBeenCalledTimes(1);
    expect(sessionStorage.getItem(LOGIN_SESSION_NOTICE_KEY)).toBeTruthy();
    expect(mockNavigate).toHaveBeenCalledWith('/login', { replace: true });
  });

  it('не-401 ошибка verify игнорируется, сетевая ошибка server-instance логируется', async () => {
    mockAxiosGet.mockResolvedValueOnce(instanceOk('srv')).mockRejectedValueOnce({ response: { status: 500 } });
    render(<SessionValidityWatcher />);
    await flush();
    expect(mockAuth.logout).not.toHaveBeenCalled();
    mockAxiosGet.mockReset();
    mockAxiosGet.mockRejectedValueOnce(new Error('offline'));
    await act(async () => {
      jest.advanceTimersByTime(30_000);
    });
    expect(console.warn).toHaveBeenCalledWith(expect.stringContaining('сетевая ошибка'), expect.any(Error));
    expect(mockAuth.logout).not.toHaveBeenCalled();
  });

  it('проверка не стартует без токена и не запускается параллельно', async () => {
    mockAuth = { ...mockAuth, token: null };
    render(<SessionValidityWatcher />);
    await flush();
    expect(mockAxiosGet).not.toHaveBeenCalled();

    mockAuth = { ...mockAuth, token: 'tok' };
    let release!: (v: unknown) => void;
    mockAxiosGet.mockReturnValueOnce(new Promise((r) => (release = r)));
    const second = render(<SessionValidityWatcher />);
    await flush();
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    expect(mockAxiosGet).toHaveBeenCalledTimes(1);
    mockAxiosGet.mockResolvedValue({ data: {} });
    await act(async () => release({ data: {} }));
    second.unmount();
  });

  it('видимость вкладки запускает проверку, скрытая вкладка нет; после размонтирования опроса нет', async () => {
    mockAxiosGet.mockResolvedValue({ data: {} });
    const { unmount } = render(<SessionValidityWatcher />);
    await flush();
    mockAxiosGet.mockClear();
    setVisibility('hidden');
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    expect(mockAxiosGet).not.toHaveBeenCalled();
    setVisibility('visible');
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    expect(mockAxiosGet).toHaveBeenCalled();
    unmount();
    mockAxiosGet.mockClear();
    await act(async () => {
      jest.advanceTimersByTime(60_000);
    });
    expect(mockAxiosGet).not.toHaveBeenCalled();
  });

  it('переподключение сокета запускает внеочередную проверку', async () => {
    mockAxiosGet.mockResolvedValue({ data: {} });
    const { rerender } = render(<SessionValidityWatcher />);
    await flush();
    mockAxiosGet.mockClear();
    mockSocket = { isConnected: true };
    rerender(<SessionValidityWatcher />);
    await flush();
    expect(mockAxiosGet).toHaveBeenCalled();
    mockAxiosGet.mockClear();
    rerender(<SessionValidityWatcher />); // без изменения состояния
    await flush();
    expect(mockAxiosGet).not.toHaveBeenCalled();
    // потеря авторизации сбрасывает признак
    mockAuth = { ...mockAuth, isAuthenticated: false };
    rerender(<SessionValidityWatcher />);
    mockAuth = { ...mockAuth, isAuthenticated: true };
    mockAxiosGet.mockClear();
    rerender(<SessionValidityWatcher />);
    await flush();
    expect(mockAxiosGet).toHaveBeenCalled();
  });
});
