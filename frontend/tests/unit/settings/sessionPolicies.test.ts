import {
  AUTH_SERVER_INSTANCE_KEY,
  LOGIN_SESSION_NOTICE_KEY,
  LOGIN_SESSION_NOTICE_MESSAGES,
  cacheServerInstanceId,
  clearServerInstanceId,
  consumeLoginSessionNotice,
  getCachedServerInstanceId,
  isLdapBlockedAuthDetail,
  isServerInstanceMismatch,
  isServerRestartAuthDetail,
  resolveLoginSessionNoticeReason,
  setLoginSessionNotice,
} from '@/settings/sessionValidity';
import {
  AUTH_SESSION_POLICY_KEY,
  AUTH_SESSION_STARTED_KEY,
  cacheSessionPolicy,
  clearSessionPolicy,
  clearSessionStarted,
  clearSessionTimeoutState,
  getCachedSessionPolicy,
  getJwtIssuedAtMs,
  getSessionRemainingMs,
  getSessionStartedMs,
  isSessionExpired,
  mapApiSessionPolicyToConfig,
  markSessionStarted,
} from '@/settings/sessionTimeout';
import {
  LOGIN_LOCKOUT_EXHAUSTED_MESSAGE,
  formatDurationRu,
  formatLockoutPolicyHint,
  formatRemainingAttemptsHint,
  mapApiPolicyToConfig,
  parseLoginErrorDetail,
} from '@/settings/loginLockout';

const NOW = 1_800_000_000_000;

function jwtWith(payload: Record<string, unknown>): string {
  const encode = (value: unknown) =>
    btoa(JSON.stringify(value)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  return `${encode({ alg: 'HS256' })}.${encode(payload)}.signature`;
}

beforeEach(() => {
  sessionStorage.clear();
  jest.spyOn(Date, 'now').mockReturnValue(NOW);
});

afterEach(() => {
  jest.restoreAllMocks();
});

describe('server instance (определение рестарта backend)', () => {
  it('stores and trims the instance id', () => {
    cacheServerInstanceId('  abc-1  ');

    expect(sessionStorage.getItem(AUTH_SERVER_INSTANCE_KEY)).toBe('abc-1');
    expect(getCachedServerInstanceId()).toBe('abc-1');
  });

  it('ignores blank ids and clears on demand', () => {
    cacheServerInstanceId('   ');
    expect(getCachedServerInstanceId()).toBeNull();

    cacheServerInstanceId('x');
    clearServerInstanceId();
    expect(getCachedServerInstanceId()).toBeNull();
  });

  it('treats a stored blank value as missing', () => {
    sessionStorage.setItem(AUTH_SERVER_INSTANCE_KEY, '   ');

    expect(getCachedServerInstanceId()).toBeNull();
  });

  it('detects a different instance only when one was cached', () => {
    expect(isServerInstanceMismatch('new')).toBe(false);

    cacheServerInstanceId('old');
    expect(isServerInstanceMismatch('old')).toBe(false);
    expect(isServerInstanceMismatch('  old  ')).toBe(false);
    expect(isServerInstanceMismatch('new')).toBe(true);
  });
});

describe('login session notices', () => {
  it('shows each notice once', () => {
    setLoginSessionNotice('server_restart');

    expect(consumeLoginSessionNotice()).toBe(LOGIN_SESSION_NOTICE_MESSAGES.server_restart);
    expect(consumeLoginSessionNotice()).toBeNull();
  });

  it.each(['session_expired', 'ldap_blocked'] as const)('maps %s to its message', (reason) => {
    setLoginSessionNotice(reason);

    expect(consumeLoginSessionNotice()).toBe(LOGIN_SESSION_NOTICE_MESSAGES[reason]);
  });

  it('removes unknown values without showing them', () => {
    sessionStorage.setItem(LOGIN_SESSION_NOTICE_KEY, '<script>alert(1)</script>');

    expect(consumeLoginSessionNotice()).toBeNull();
    expect(sessionStorage.getItem(LOGIN_SESSION_NOTICE_KEY)).toBeNull();
  });

  it('recognises a server restart in the error detail', () => {
    expect(isServerRestartAuthDetail('Сервер ПЕРЕЗАПУЩЕН, войдите снова')).toBe(true);
    expect(isServerRestartAuthDetail('Токен истёк')).toBe(false);
    expect(isServerRestartAuthDetail(undefined)).toBe(false);
    expect(isServerRestartAuthDetail({ detail: 'перезапущен' })).toBe(false);
  });

  it.each([
    ['Учётная запись LDAP заблокирована', true],
    ['Пользователь отключен', true],
    ['Пользователь не найден', true],
    ['Токен истёк', false],
    [42, false],
    [null, false],
  ])('classifies %p as ldap-blocked=%p', (detail, expected) => {
    expect(isLdapBlockedAuthDetail(detail)).toBe(expected);
  });

  it('resolves the reason with the restart taking priority', () => {
    expect(resolveLoginSessionNoticeReason('Сервер перезапущен, LDAP недоступен')).toBe('server_restart');
    expect(resolveLoginSessionNoticeReason('LDAP: заблокирован')).toBe('ldap_blocked');
    expect(resolveLoginSessionNoticeReason('Токен истёк')).toBe('session_expired');
    expect(resolveLoginSessionNoticeReason(undefined)).toBe('session_expired');
  });

  it('classifies any "not found" detail as an LDAP block (known issue)', () => {
    // Подстрока «не найден» совпадает и с «Сессия не найдена»: протухшая сессия показывается как блокировка в LDAP.
    expect(resolveLoginSessionNoticeReason('Сессия не найдена')).toBe('ldap_blocked');
  });
});

describe('session policy', () => {
  it('maps the API policy and disables it for non-positive timeouts', () => {
    expect(mapApiSessionPolicyToConfig({ enabled: true, timeout_seconds: 3600 })).toEqual({ enabled: true, timeoutSeconds: 3600 });
    expect(mapApiSessionPolicyToConfig({ enabled: true, timeout_seconds: 0 })).toEqual({ enabled: false, timeoutSeconds: 0 });
    expect(mapApiSessionPolicyToConfig({ enabled: false, timeout_seconds: 600 })).toEqual({ enabled: false, timeoutSeconds: 600 });
    expect(mapApiSessionPolicyToConfig({ enabled: true, timeout_seconds: -5 })).toEqual({ enabled: false, timeoutSeconds: 0 });
    expect(mapApiSessionPolicyToConfig({ enabled: true, timeout_seconds: 'abc' as unknown as number }).enabled).toBe(false);
  });

  it('round-trips through sessionStorage', () => {
    cacheSessionPolicy({ enabled: true, timeoutSeconds: 900 });

    expect(getCachedSessionPolicy()).toEqual({ enabled: true, timeoutSeconds: 900 });
    clearSessionPolicy();
    expect(getCachedSessionPolicy()).toBeNull();
  });

  it.each(['not json', '{}', '{"enabled":"yes","timeoutSeconds":5}', '{"enabled":true,"timeoutSeconds":"5"}', 'null'])(
    'rejects a damaged cached policy %p',
    (raw) => {
      sessionStorage.setItem(AUTH_SESSION_POLICY_KEY, raw);

      expect(getCachedSessionPolicy()).toBeNull();
    },
  );
});

describe('JWT issued-at', () => {
  it('reads iat in milliseconds from url-safe base64 payloads', () => {
    const token = jwtWith({ iat: 1_700_000_000, name: '>>>???~~~' });

    expect(token.includes('+') || token.includes('/')).toBe(false); // url-safe алфавит: «+» и «/» заменены на «-» и «_»
    expect(getJwtIssuedAtMs(token)).toBe(1_700_000_000_000);
  });

  it.each(['', 'abc', 'a.b', `a.${btoa('{"iat":"1"}')}.c`, `a.${btoa('{}')}.c`, 'a.!!!.c'])('returns null for %p', (token) => {
    expect(getJwtIssuedAtMs(token)).toBeNull();
  });
});

describe('absolute session timeout', () => {
  it('starts the clock at login', () => {
    markSessionStarted();

    expect(sessionStorage.getItem(AUTH_SESSION_STARTED_KEY)).toBe(String(NOW));
    expect(getSessionStartedMs()).toBe(NOW);
  });

  it('expires exactly at the timeout and not before', () => {
    markSessionStarted();

    (Date.now as jest.Mock).mockReturnValue(NOW + 59_999);
    expect(isSessionExpired(60)).toBe(false);
    (Date.now as jest.Mock).mockReturnValue(NOW + 60_000);
    expect(isSessionExpired(60)).toBe(true);
  });

  it('reports the remaining time and never goes below zero', () => {
    markSessionStarted();

    (Date.now as jest.Mock).mockReturnValue(NOW + 10_000);
    expect(getSessionRemainingMs(60)).toBe(50_000);
    (Date.now as jest.Mock).mockReturnValue(NOW + 120_000);
    expect(getSessionRemainingMs(60)).toBe(0);
  });

  it('never expires without a positive timeout', () => {
    markSessionStarted();
    (Date.now as jest.Mock).mockReturnValue(NOW + 10 ** 12);

    expect(isSessionExpired(0)).toBe(false);
    expect(isSessionExpired(-1)).toBe(false);
    expect(getSessionRemainingMs(0)).toBe(Infinity);
  });

  it('is not reset by a token refresh: the start comes from the first login', () => {
    markSessionStarted();
    (Date.now as jest.Mock).mockReturnValue(NOW + 30_000);
    const refreshedToken = jwtWith({ iat: (NOW + 30_000) / 1000 });

    expect(getSessionStartedMs(refreshedToken)).toBe(NOW);
  });

  it('falls back to the token iat after a reload that lost the stored start, and remembers it', () => {
    const issuedAt = NOW - 20_000;
    const token = jwtWith({ iat: issuedAt / 1000 });

    expect(getSessionStartedMs(token)).toBe(issuedAt);
    expect(sessionStorage.getItem(AUTH_SESSION_STARTED_KEY)).toBe(String(issuedAt));
    expect(isSessionExpired(10, token)).toBe(true);
  });

  it('ignores a corrupted stored start', () => {
    sessionStorage.setItem(AUTH_SESSION_STARTED_KEY, 'yesterday');

    expect(getSessionStartedMs(jwtWith({ iat: (NOW - 5_000) / 1000 }))).toBe(NOW - 5_000);
  });

  it('restarts the clock when neither a start nor a token is known (known issue)', () => {
    // Без отметки входа и токена таймер стартует «сейчас»: сессия, потерявшая sessionStorage, получает полный новый срок.
    expect(getSessionStartedMs(null)).toBe(NOW);
    (Date.now as jest.Mock).mockReturnValue(NOW + 3_000);
    expect(isSessionExpired(60, null)).toBe(false);
  });

  it('clears all timeout state', () => {
    markSessionStarted();
    cacheSessionPolicy({ enabled: true, timeoutSeconds: 60 });

    clearSessionTimeoutState();

    expect(sessionStorage.getItem(AUTH_SESSION_STARTED_KEY)).toBeNull();
    expect(getCachedSessionPolicy()).toBeNull();
    clearSessionStarted();
  });
});

describe('login lockout presentation', () => {
  it.each([
    [0, '0 сек.'],
    [45, '45 сек.'],
    [60, '1 мин.'],
    [90, '1 мин. 30 сек.'],
    [900, '15 мин.'],
    [-5, '0 сек.'],
    [59.9, '59 сек.'],
  ])('formats %p seconds as %p', (seconds, expected) => {
    expect(formatDurationRu(seconds)).toBe(expected);
  });

  it('describes the policy only when it is enabled', () => {
    expect(formatLockoutPolicyHint({ enabled: false, maxFailedAttempts: 5, lockoutDurationSeconds: 900 })).toBe('');
    expect(formatLockoutPolicyHint({ enabled: true, maxFailedAttempts: 5, lockoutDurationSeconds: 900 })).toBe(
      'При 5 неверных попытках подряд вход будет заблокирован на 15 мин..',
    );
  });

  it('warns about the last attempt and counts the remaining ones', () => {
    expect(formatRemainingAttemptsHint(2, 5, 900)).toBe('Осталось попыток до блокировки: 2 из 5.');
    expect(formatRemainingAttemptsHint(0, 5, 300)).toBe('Следующая неудачная попытка заблокирует вход на 5 мин..');
    expect(formatRemainingAttemptsHint(-1, 5, 30)).toContain('30 сек.');
  });

  it('hardcodes 15 minutes in the exhausted message whatever the policy says (known issue)', () => {
    expect(LOGIN_LOCKOUT_EXHAUSTED_MESSAGE).toContain('через 15 минут');
  });

  it('maps the API policy and supports the legacy minutes field', () => {
    expect(mapApiPolicyToConfig({ enabled: true, max_failed_attempts: 5, lockout_duration_seconds: 120 })).toEqual({
      enabled: true,
      maxFailedAttempts: 5,
      lockoutDurationSeconds: 120,
    });
    expect(mapApiPolicyToConfig({ enabled: true, max_failed_attempts: 3, lockout_duration_minutes: 10 }).lockoutDurationSeconds).toBe(600);
    expect(mapApiPolicyToConfig({ enabled: false, max_failed_attempts: 3 })).toEqual({ enabled: false, maxFailedAttempts: 3, lockoutDurationSeconds: 900 });
    expect(mapApiPolicyToConfig({ enabled: true, max_failed_attempts: 3, lockout_duration_seconds: 0, lockout_duration_minutes: 10 }).lockoutDurationSeconds).toBe(0);
  });

  it('parses login errors of every shape', () => {
    expect(parseLoginErrorDetail('Неверный пароль')).toEqual({ message: 'Неверный пароль' });
    expect(parseLoginErrorDetail(undefined)).toEqual({ message: 'Ошибка при входе в систему' });
    expect(parseLoginErrorDetail(42)).toEqual({ message: 'Ошибка при входе в систему' });
    expect(parseLoginErrorDetail({})).toEqual({
      message: 'Ошибка при входе в системе',
      remainingAttempts: undefined,
      maxFailedAttempts: undefined,
      lockoutDurationSeconds: undefined,
      retryAfterSeconds: undefined,
    });
  });

  it('keeps the attempt counters from a structured error', () => {
    const parsed = parseLoginErrorDetail({
      enabled: true,
      message: 'Неверный пароль',
      remaining_attempts: 0,
      max_failed_attempts: 5,
      lockout_duration_minutes: 15,
      retry_after_seconds: 42,
    });

    expect(parsed).toEqual({ message: 'Неверный пароль', remainingAttempts: 0, maxFailedAttempts: 5, lockoutDurationSeconds: 900, retryAfterSeconds: 42 });
    expect(parseLoginErrorDetail({ message: 'x', remaining_attempts: null }).remainingAttempts).toBeUndefined();
  });
});
