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

  it('does NOT match generic standalone words role/forbidden in wrong-password text (BUG-FE-06)', () => {
    expect(isMissingRoleLoginError('Неверный пароль, роль')).toBe(false);
    expect(isMissingRoleLoginError('Роль')).toBe(false);
    expect(isMissingRoleLoginError('Invalid password for user role_admin')).toBe(false);
    expect(isMissingRoleLoginError('Wrong password. role: forbidden')).toBe(false);
    expect(isMissingRoleLoginError('Неверное имя пользователя или пароль', 'forbidden role')).toBe(false);
    // «forbidden» без HTTP 403 — не признак отсутствия роли
    expect(isMissingRoleLoginError('403 Forbidden')).toBe(false);
    expect(isMissingRoleLoginError({ message: 'forbidden' })).toBe(false);
  });

  it('matches the phrase about a missing role in various forms', () => {
    expect(isMissingRoleLoginError('У пользователя нет роль')).toBe(true);
    expect(isMissingRoleLoginError('Нет роль.')).toBe(true);
    expect(isMissingRoleLoginError('Отсутствует необходимая роль')).toBe(true);
    expect(
      isMissingRoleLoginError(
        'У вас нет необходимой роли для взаимодействия с AstraChat. Обратитесь к администратору проекта',
      ),
    ).toBe(true);
  });

  it('matches english phrases as whole words', () => {
    expect(isMissingRoleLoginError('User has no role')).toBe(true);
    expect(isMissingRoleLoginError('Missing roles: astra_user')).toBe(true);
    expect(isMissingRoleLoginError('Insufficient permissions')).toBe(true);
  });

  it('treats a standalone "forbidden" as a role error only with HTTP 403', () => {
    expect(isMissingRoleLoginError('403 Forbidden', undefined, 403)).toBe(true);
    expect(isMissingRoleLoginError({ message: 'forbidden' }, undefined, 403)).toBe(true);
    expect(isMissingRoleLoginError('Forbidden', undefined, 401)).toBe(false);
  });

  it('prefers the backend error code when available', () => {
    expect(isMissingRoleLoginError({ code: 'missing_role' })).toBe(true);
    expect(isMissingRoleLoginError({ code: 'insufficient-role' })).toBe(true);
    expect(isMissingRoleLoginError({ error_code: 'ROLE_REQUIRED' })).toBe(true);
    expect(isMissingRoleLoginError('missing_role')).toBe(true);
    expect(isMissingRoleLoginError({ code: 'invalid_credentials', message: 'Неверный пароль' })).toBe(false);
  });

  it('matches the "доступ запрещен/запрещён" phrase', () => {
    expect(isMissingRoleLoginError('Доступ запрещен')).toBe(true);
    expect(isMissingRoleLoginError('Доступ запрещён!')).toBe(true);
    expect(isMissingRoleLoginError('доступ  запрещен')).toBe(true);
  });

  it('checks parsed message and detail object fields', () => {
    expect(isMissingRoleLoginError(undefined, 'Нет нужной роль')).toBe(true);
    expect(isMissingRoleLoginError({ message: 'no role assigned' })).toBe(true);
    expect(isMissingRoleLoginError({ detail: 'no role' })).toBe(true);
    expect(isMissingRoleLoginError({ error: 'Insufficient permissions' })).toBe(true);
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
