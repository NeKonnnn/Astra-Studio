import * as settings from '@/settings/index';
import * as config from '@/settings/config';
import * as lockout from '@/settings/loginLockout';
import * as timeout from '@/settings/sessionTimeout';
import * as connections from '@/settings/connections';

describe('settings public API (index)', () => {
  it('re-exports the configuration loader functions from ./config', () => {
    expect(settings.getSettings).toBe(config.getSettings);
    expect(settings.initSettings).toBe(config.initSettings);
    expect(settings.initConfig).toBe(config.initConfig);
    expect(settings.resetSettings).toBe(config.resetSettings);
    expect(settings.getUrl).toBe(config.getUrl);
  });

  it('re-exports the login lockout helpers', () => {
    expect(settings.formatDurationRu).toBe(lockout.formatDurationRu);
    expect(settings.formatLockoutPolicyHint).toBe(lockout.formatLockoutPolicyHint);
    expect(settings.formatRemainingAttemptsHint).toBe(lockout.formatRemainingAttemptsHint);
    expect(settings.mapApiPolicyToConfig).toBe(lockout.mapApiPolicyToConfig);
    expect(settings.parseLoginErrorDetail).toBe(lockout.parseLoginErrorDetail);
    expect(settings.LOGIN_LOCKOUT_EXHAUSTED_MESSAGE).toBe(lockout.LOGIN_LOCKOUT_EXHAUSTED_MESSAGE);
  });

  it('re-exports the session timeout helpers and storage keys', () => {
    expect(settings.mapApiSessionPolicyToConfig).toBe(timeout.mapApiSessionPolicyToConfig);
    expect(settings.markSessionStarted).toBe(timeout.markSessionStarted);
    expect(settings.clearSessionStarted).toBe(timeout.clearSessionStarted);
    expect(settings.clearSessionTimeoutState).toBe(timeout.clearSessionTimeoutState);
    expect(settings.cacheSessionPolicy).toBe(timeout.cacheSessionPolicy);
    expect(settings.getCachedSessionPolicy).toBe(timeout.getCachedSessionPolicy);
    expect(settings.AUTH_SESSION_STARTED_KEY).toBe(timeout.AUTH_SESSION_STARTED_KEY);
    expect(settings.AUTH_SESSION_POLICY_KEY).toBe(timeout.AUTH_SESSION_POLICY_KEY);
  });

  it('re-exports the connection classes', () => {
    expect(settings.APIConnectionConfigImpl).toBe(connections.APIConnectionConfigImpl);
    expect(settings.WebSocketConnectionConfigImpl).toBe(connections.WebSocketConnectionConfigImpl);
  });
});
