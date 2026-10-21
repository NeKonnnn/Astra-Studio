import React, { createContext, useContext, useState, useEffect, ReactNode } from 'react';
import axios from 'axios';
import { initSettings, parseLoginErrorDetail } from '../settings';
import {
  markSessionStarted,
  clearSessionTimeoutState,
  cacheSessionPolicy,
  mapApiSessionPolicyToConfig,
  type SessionPolicyResponse,
} from '../settings/sessionTimeout';
import { cacheServerInstanceId, clearServerInstanceId, resolveLoginSessionNoticeReason, setLoginSessionNotice, type LoginSessionNoticeReason } from '../settings/sessionValidity';
import { getApiUrl, fetchSessionPolicy } from '../config/api';
import { isMissingRoleLoginError, MISSING_ROLE_LOGIN_MESSAGE } from './missingRoleLoginError';

export type LoginErrorMeta = {
  remainingAttempts?: number;
  maxFailedAttempts?: number;
  lockoutDurationSeconds?: number;
  retryAfterSeconds?: number;
};

export class AuthLoginError extends Error {
  readonly meta: LoginErrorMeta;

  constructor(message: string, meta: LoginErrorMeta = {}) {
    super(message);
    this.name = 'AuthLoginError';
    this.meta = meta;
  }
}

export { isMissingRoleLoginError };

interface User {
  username: string;
  user_id?: string;  // ID пользователя (может быть равен username)
  email: string | null;
  full_name: string | null;
  is_active: boolean;
  is_admin: boolean;
  /** owner | editor | viewer | base */
  platform_role?: string | null;
  can_use_agents?: boolean;
  can_use_mcp?: boolean;
  can_create_agents?: boolean;
  can_publish_to_library?: boolean;
  features?: {
    agents?: boolean;
    mcp?: boolean;
    create_agents?: boolean;
    publish_to_library?: boolean;
  };
}

interface AuthContextType {
  user: User | null;
  token: string | null;
  login: (username: string, password: string) => Promise<void>;
  completeSsoLogin: (ticket: string) => Promise<void>;
  logout: () => void;
  isAuthenticated: boolean;
  isLoading: boolean;
  updateUser: (userData: Partial<User>) => void;
}

const AuthContext = createContext<AuthContextType | null>(null);
const REFRESH_TOKEN_STORAGE_KEY = 'auth_refresh_token';
const TOKEN_REFRESH_SKEW_MS = 2 * 60 * 1000;
const TOKEN_REFRESH_FALLBACK_MS = 25 * 60 * 1000;

/** URL API для auth: после initSettings() — из public/config/config.yml (или REACT_APP_API_URL при сборке). */
const authApiUrl = (path: string): string => {
  if (process.env.REACT_APP_API_URL) {
    const base = process.env.REACT_APP_API_URL.replace(/\/$/, '');
    const p = path.startsWith('/') ? path : `/${path}`;
    return `${base}${p}`;
  }
  return getApiUrl(path);
};

const getJwtExpiryMs = (jwtToken: string): number | null => {
  try {
    const payloadPart = jwtToken.split('.')[1];
    if (!payloadPart) return null;
    const base64 = payloadPart.replace(/-/g, '+').replace(/_/g, '/');
    const padded = base64 + '='.repeat((4 - (base64.length % 4)) % 4);
    const payloadJson = atob(padded);
    const payload = JSON.parse(payloadJson);
    if (typeof payload.exp !== 'number') return null;
    return payload.exp * 1000;
  } catch {
    return null;
  }
};

/** Access JWT просрочен (с запасом TOKEN_REFRESH_SKEW_MS, как при авто-refresh). */
const isAccessTokenExpired = (jwtToken: string): boolean => {
  const expMs = getJwtExpiryMs(jwtToken);
  if (expMs === null) return false;
  return expMs <= Date.now() + TOKEN_REFRESH_SKEW_MS;
};

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const refreshInFlightRef = React.useRef<Promise<string | null> | null>(null);

  const clearStoredAuth = (noticeReason?: LoginSessionNoticeReason) => {
    if (noticeReason) {
      setLoginSessionNotice(noticeReason);
    }
    localStorage.removeItem('auth_token');
    localStorage.removeItem(REFRESH_TOKEN_STORAGE_KEY);
    localStorage.removeItem('auth_user');
    clearSessionTimeoutState();
    clearServerInstanceId();
    setToken(null);
    setUser(null);
  };

  // Проверка валидности токена
  const verifyToken = async (token: string) => {
    try {
      const response = await axios.get(authApiUrl('/api/auth/verify'), {
        headers: {
          Authorization: `Bearer ${token}`,
        },
        timeout: 10000,
      });
      if (typeof response.data?.server_instance_id === 'string') {
        cacheServerInstanceId(response.data.server_instance_id);
      }
      return response.data.valid;
    } catch (error: any) {
      // Если это ошибка авторизации (401), токен точно невалиден
      if (error.response?.status === 401) {
        throw error;
      }
      // Для других ошибок (сеть, сервер недоступен и т.д.) не считаем токен невалидным
      // Просто логируем и продолжаем работу
      console.warn('Не удалось проверить токен (возможна сетевая ошибка):', error.message);
      return true; // Предполагаем, что токен валиден, если это не ошибка авторизации
    }
  };

  const refreshAccessToken = async (refreshToken: string): Promise<string | null> => {
    try {
      const response = await axios.post(
        authApiUrl('/api/auth/refresh'),
        { refresh_token: refreshToken },
        { timeout: 10000 },
      );
      const { access_token, refresh_token, user: userData } = response.data;
      if (!access_token || !refresh_token) {
        return null;
      }
      setToken(access_token);
      localStorage.setItem('auth_token', access_token);
      localStorage.setItem(REFRESH_TOKEN_STORAGE_KEY, refresh_token);
      if (userData) {
        setUser(userData);
        localStorage.setItem('auth_user', JSON.stringify(userData));
      }
      if (typeof response.data.server_instance_id === 'string') {
        cacheServerInstanceId(response.data.server_instance_id);
      }
      return access_token;
    } catch (error: any) {
      if (error?.response?.status === 401) {
        setLoginSessionNotice(resolveLoginSessionNoticeReason(error?.response?.data?.detail));
      }
      return null;
    }
  };

  // Инициализация: восстанавливаем сессию только после проверки/refresh (без гонки с API/WS).
  useEffect(() => {
    let cancelled = false;
    const AUTH_INIT_HARD_TIMEOUT_MS = 20000;

    const initializeAuth = async () => {
      // Сначала убеждаемся, что настройки загружены
      try {
        await initSettings();
      } catch (error) {
        console.warn('Не удалось загрузить настройки, используем дефолтные значения:', error);
      }

      const savedToken = localStorage.getItem('auth_token');
      const savedRefreshToken = localStorage.getItem(REFRESH_TOKEN_STORAGE_KEY);
      const savedUser = localStorage.getItem('auth_user');

      if (savedToken && savedUser) {
        try {
          const parsedUser = JSON.parse(savedUser) as User;
          let effectiveToken = savedToken;

          if (isAccessTokenExpired(savedToken)) {
            if (!savedRefreshToken) {
              console.warn('Access-токен просрочен, refresh отсутствует — очищаем авторизацию');
              clearStoredAuth();
              if (!cancelled) setIsLoading(false);
              return;
            }
            const refreshedToken = await refreshAccessToken(savedRefreshToken);
            if (!refreshedToken) {
              clearStoredAuth();
              if (!cancelled) setIsLoading(false);
              return;
            }
            effectiveToken = refreshedToken;
          }

          try {
            await verifyToken(effectiveToken);
          } catch (error: any) {
            if (error.response?.status !== 401) {
              console.warn('Не удалось проверить токен, но продолжаем работу:', error.message);
            } else if (effectiveToken === savedToken && savedRefreshToken) {
              const refreshedToken = await refreshAccessToken(savedRefreshToken);
              if (!refreshedToken) {
                clearStoredAuth(
                  resolveLoginSessionNoticeReason(error?.response?.data?.detail),
                );
                if (!cancelled) setIsLoading(false);
                return;
              }
              effectiveToken = refreshedToken;
              try {
                await verifyToken(effectiveToken);
              } catch (verifyAfterRefresh: any) {
                if (verifyAfterRefresh.response?.status === 401) {
                  clearStoredAuth(
                    resolveLoginSessionNoticeReason(verifyAfterRefresh?.response?.data?.detail),
                  );
                  if (!cancelled) setIsLoading(false);
                  return;
                }
              }
            } else {
              clearStoredAuth(resolveLoginSessionNoticeReason(error?.response?.data?.detail));
              if (!cancelled) setIsLoading(false);
              return;
            }
          }

          const latestUserRaw = localStorage.getItem('auth_user');
          if (!cancelled) {
            setToken(localStorage.getItem('auth_token') || effectiveToken);
            setUser(latestUserRaw ? (JSON.parse(latestUserRaw) as User) : parsedUser);
          }
        } catch (error) {
          console.error('Ошибка при инициализации авторизации:', error);
          clearStoredAuth();
        }
      }

      if (!cancelled) setIsLoading(false);
    };

    const hardTimeout = window.setTimeout(() => {
      if (!cancelled) {
        console.warn('Таймаут инициализации auth — снимаем экран загрузки');
        setIsLoading(false);
      }
    }, AUTH_INIT_HARD_TIMEOUT_MS);

    initializeAuth().finally(() => {
      window.clearTimeout(hardTimeout);
    });

    return () => {
      cancelled = true;
      window.clearTimeout(hardTimeout);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!token) return;

    const refreshToken = localStorage.getItem(REFRESH_TOKEN_STORAGE_KEY);
    if (!refreshToken) return;

    const expiresAtMs = getJwtExpiryMs(token);
    const nowMs = Date.now();
    const targetTimeMs = expiresAtMs
      ? Math.max(0, expiresAtMs - TOKEN_REFRESH_SKEW_MS - nowMs)
      : TOKEN_REFRESH_FALLBACK_MS;

    const timerId = window.setTimeout(async () => {
      if (!refreshInFlightRef.current) {
        refreshInFlightRef.current = refreshAccessToken(refreshToken)
          .finally(() => {
            refreshInFlightRef.current = null;
          });
      }
      const newAccessToken = await refreshInFlightRef.current;
      if (!newAccessToken) {
        localStorage.removeItem('auth_token');
        localStorage.removeItem(REFRESH_TOKEN_STORAGE_KEY);
        localStorage.removeItem('auth_user');
        clearSessionTimeoutState();
        clearServerInstanceId();
        setToken(null);
        setUser(null);
      }
    }, targetTimeMs);

    return () => {
      window.clearTimeout(timerId);
    };
  }, [token]);

  // Настройка axios для автоматического добавления токена
  useEffect(() => {
    axios.defaults.withCredentials = true;
    if (token) {
      axios.defaults.headers.common['Authorization'] = `Bearer ${token}`;
    } else {
      delete axios.defaults.headers.common['Authorization'];
    }
  }, [token]);

  useEffect(() => {
    const interceptorId = axios.interceptors.response.use(
      (response: any) => response,
      async (error: any) => {
        const originalRequest = error?.config as any;
        const statusCode = error?.response?.status;
        const isAuthEndpoint = typeof originalRequest?.url === 'string' && originalRequest.url.includes('/api/auth/');
        if (statusCode !== 401 || !originalRequest || originalRequest._retry || isAuthEndpoint) {
          return Promise.reject(error);
        }

        const refreshToken = localStorage.getItem(REFRESH_TOKEN_STORAGE_KEY);
        if (!refreshToken) {
          return Promise.reject(error);
        }

        originalRequest._retry = true;
        if (!refreshInFlightRef.current) {
          refreshInFlightRef.current = refreshAccessToken(refreshToken)
            .finally(() => {
              refreshInFlightRef.current = null;
            });
        }

        const newAccessToken = await refreshInFlightRef.current;
        if (!newAccessToken) {
          localStorage.removeItem('auth_token');
          localStorage.removeItem(REFRESH_TOKEN_STORAGE_KEY);
          localStorage.removeItem('auth_user');
          clearSessionTimeoutState();
          clearServerInstanceId();
          setToken(null);
          setUser(null);
          return Promise.reject(error);
        }

        originalRequest.headers = originalRequest.headers || {};
        originalRequest.headers.Authorization = `Bearer ${newAccessToken}`;
        return axios(originalRequest);
      },
    );

    return () => {
      axios.interceptors.response.eject(interceptorId);
    };
  }, []);

  const applyLoginResponse = async (responseData: {
    access_token: string;
    refresh_token: string;
    user: User;
    session_policy?: SessionPolicyResponse;
    server_instance_id?: string;
  }) => {
    const { access_token, refresh_token, user: userData, session_policy } = responseData;
    if (!refresh_token) {
      throw new Error('Сервер не вернул refresh-токен');
    }

    markSessionStarted();
    if (typeof responseData.server_instance_id === 'string') {
      cacheServerInstanceId(responseData.server_instance_id);
    }
    if (session_policy) {
      cacheSessionPolicy(mapApiSessionPolicyToConfig(session_policy));
    } else {
      try {
        await initSettings();
        cacheSessionPolicy(await fetchSessionPolicy());
      } catch (policyError) {
        console.warn('Не удалось загрузить session_policy после login:', policyError);
      }
    }

    setToken(access_token);
    setUser(userData);
    localStorage.setItem('auth_token', access_token);
    localStorage.setItem(REFRESH_TOKEN_STORAGE_KEY, refresh_token);
    localStorage.setItem('auth_user', JSON.stringify(userData));
  };

  const login = async (username: string, password: string) => {
    try {
      try {
        await initSettings();
      } catch (settingsError) {
        console.warn('Не удалось загрузить настройки перед login, используем дефолтные:', settingsError);
      }
      const response = await axios.post(authApiUrl('/api/auth/login'), {
        username,
        password,
      });
      await applyLoginResponse(response.data);
    } catch (error: any) {
      const detail = error.response?.data?.detail;
      const parsed = parseLoginErrorDetail(detail);
      const missingRole = isMissingRoleLoginError(detail, parsed.message);
      if (error.response?.status === 401) {
        throw new AuthLoginError(missingRole ? MISSING_ROLE_LOGIN_MESSAGE : parsed.message, {
          remainingAttempts: parsed.remainingAttempts,
          maxFailedAttempts: parsed.maxFailedAttempts,
          lockoutDurationSeconds: parsed.lockoutDurationSeconds,
        });
      }
      if (error.response?.status === 403) {
        throw new AuthLoginError(
          missingRole
            ? MISSING_ROLE_LOGIN_MESSAGE
            : parsed.message || 'Доступ запрещен',
        );
      }
      if (error.response?.status === 429) {
        throw new AuthLoginError(
          parsed.message || 'Слишком много неудачных попыток входа. Попробуйте позже.',
          {
            remainingAttempts: 0,
            maxFailedAttempts: parsed.maxFailedAttempts,
            lockoutDurationSeconds: parsed.lockoutDurationSeconds,
            retryAfterSeconds: parsed.retryAfterSeconds,
          },
        );
      }
      if (error.response?.status === 503) {
        throw new AuthLoginError('Сервис аутентификации временно недоступен');
      }
      throw new AuthLoginError('Ошибка при входе в систему');
    }
  };

  const completeSsoLogin = async (ticket: string) => {
    // Сбрасываем старую LDAP/SSO-сессию до обмена ticket — иначе параллельные запросы
    // с устаревшим client binding дают 401 и мешают новому входу.
    setToken(null);
    setUser(null);
    delete axios.defaults.headers.common['Authorization'];
    localStorage.removeItem('auth_token');
    localStorage.removeItem(REFRESH_TOKEN_STORAGE_KEY);
    localStorage.removeItem('auth_user');
    clearSessionTimeoutState();
    clearServerInstanceId();

    try {
      await initSettings();
    } catch (error) {
      console.warn('Не удалось загрузить настройки перед SSO exchange, используем дефолтные:', error);
    }
    const response = await axios.post(authApiUrl('/api/auth/sso/keycloak/exchange'), { ticket });
    await applyLoginResponse(response.data);
  };

  const logout = async () => {
    try {
      if (token) {
        await axios.post(authApiUrl('/api/auth/logout'), {}, {
          headers: {
            Authorization: `Bearer ${token}`,
          },
        });
      }
    } catch (error) {
      console.error('Ошибка при выходе:', error);
    } finally {
      // Очищаем данные независимо от результата запроса
      setToken(null);
      setUser(null);
      localStorage.removeItem('auth_token');
      localStorage.removeItem(REFRESH_TOKEN_STORAGE_KEY);
      localStorage.removeItem('auth_user');
      clearSessionTimeoutState();
      clearServerInstanceId();
    }
  };

  const updateUser = (userData: Partial<User>) => {
    if (user) {
      const updatedUser = { ...user, ...userData };
      setUser(updatedUser);
      localStorage.setItem('auth_user', JSON.stringify(updatedUser));
    }
  };

  const value = {
    user,
    token,
    login,
    completeSsoLogin,
    logout,
    isAuthenticated: !!token && !!user,
    isLoading,
    updateUser,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}