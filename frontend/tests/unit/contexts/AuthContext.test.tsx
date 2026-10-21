/**
 * @jest-environment jsdom
 */
import { isMissingRoleLoginError } from '@/contexts/AuthContext';
import { containsRoleErrorMarker } from '@/contexts/missingRoleLoginError';

jest.mock('axios', () => ({
  __esModule: true,
  default: { get: jest.fn(), post: jest.fn(), defaults: { headers: { common: {} } } },
}));
jest.mock('@/settings', () => ({
  initSettings: jest.fn(),
  parseLoginErrorDetail: jest.fn(),
}));
jest.mock('@/settings/sessionTimeout', () => ({
  markSessionStarted: jest.fn(),
  clearSessionTimeoutState: jest.fn(),
  cacheSessionPolicy: jest.fn(),
  mapApiSessionPolicyToConfig: jest.fn(),
}));
jest.mock('@/settings/sessionValidity', () => ({
  cacheServerInstanceId: jest.fn(),
  clearServerInstanceId: jest.fn(),
  resolveLoginSessionNoticeReason: jest.fn(),
  setLoginSessionNotice: jest.fn(),
}));
jest.mock('@/config/api', () => ({
  getApiUrl: (p: string) => p,
  fetchSessionPolicy: jest.fn(),
}));

describe('isMissingRoleLoginError', () => {
  it('does NOT match "роль" inside "пароль"', () => {
    expect(isMissingRoleLoginError('Неверный пароль')).toBe(false);
    expect(isMissingRoleLoginError('Неверный логин или пароль')).toBe(false);
    expect(isMissingRoleLoginError('Пароль истёк', 'Пароль истёк')).toBe(false);
    expect(isMissingRoleLoginError({ message: 'Неверный пароль' })).toBe(false);
    expect(isMissingRoleLoginError(undefined, 'ПАРОЛЬ неверен')).toBe(false);
  });

  it('does NOT match role/forbidden as a substring of another word', () => {
    expect(isMissingRoleLoginError('controller failed')).toBe(false);
    expect(isMissingRoleLoginError('Rolex watch')).toBe(false);
    expect(isMissingRoleLoginError('unforbiddenly')).toBe(false);
  });

  it('matches the standalone word "роль" in various forms', () => {
    expect(isMissingRoleLoginError('У пользователя нет роль')).toBe(true);
    expect(isMissingRoleLoginError('Нет роль.')).toBe(true);
    expect(isMissingRoleLoginError('Роль')).toBe(true);
    expect(isMissingRoleLoginError('(роль) отсутствует')).toBe(true);
  });

  it('matches english markers as whole words', () => {
    expect(isMissingRoleLoginError('User has no role')).toBe(true);
    expect(isMissingRoleLoginError('Missing roles: astra_user')).toBe(true);
    expect(isMissingRoleLoginError('Insufficient permissions')).toBe(true);
    expect(isMissingRoleLoginError('403 Forbidden')).toBe(true);
  });

  it('matches error codes with underscores / dashes', () => {
    expect(isMissingRoleLoginError({ code: 'missing_role' })).toBe(true);
    expect(isMissingRoleLoginError({ code: 'insufficient-role' })).toBe(true);
  });

  it('matches the "доступ запрещен/запрещён" phrase', () => {
    expect(isMissingRoleLoginError('Доступ запрещен')).toBe(true);
    expect(isMissingRoleLoginError('Доступ запрещён!')).toBe(true);
    expect(isMissingRoleLoginError('доступ  запрещен')).toBe(true);
  });

  it('checks parsed message and detail object fields', () => {
    expect(isMissingRoleLoginError(undefined, 'Нет нужной роль')).toBe(true);
    expect(isMissingRoleLoginError({ message: 'forbidden' })).toBe(true);
    expect(isMissingRoleLoginError({ detail: 'no role' })).toBe(true);
    expect(isMissingRoleLoginError({ error: 'Insufficient scope' })).toBe(true);
  });

  it('returns false for unrelated / non-string input', () => {
    expect(isMissingRoleLoginError(undefined)).toBe(false);
    expect(isMissingRoleLoginError(null)).toBe(false);
    expect(isMissingRoleLoginError(42)).toBe(false);
    expect(isMissingRoleLoginError({})).toBe(false);
    expect(isMissingRoleLoginError('Account locked')).toBe(false);
  });
});

describe('containsRoleErrorMarker', () => {
  it('rejects non-strings', () => {
    expect(containsRoleErrorMarker(123)).toBe(false);
    expect(containsRoleErrorMarker(undefined)).toBe(false);
  });
});
