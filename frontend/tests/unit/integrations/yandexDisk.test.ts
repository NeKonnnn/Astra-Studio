/**
 * @jest-environment jsdom
 */
import {
  parseOAuthHash,
  buildYandexAuthorizeUrl,
  YANDEX_DISK_OAUTH_SCOPE,
} from '@/integrations/yandex-disk/oauth';
import { __testables } from '@/integrations/yandex-disk/api';
import { API_ENDPOINTS } from '@/config/api';
import { INTEGRATIONS_CATALOG } from '@/integrations/catalog';

describe('Yandex Disk OAuth helpers', () => {
  it('parseOAuthHash reads access_token and expires_in', () => {
    const parsed = parseOAuthHash(
      '#access_token=tok123&token_type=bearer&expires_in=3600',
    );
    expect(parsed.accessToken).toBe('tok123');
    expect(parsed.expiresIn).toBe(3600);
    expect(parsed.error).toBeNull();
  });

  it('buildYandexAuthorizeUrl includes client_id, redirect and scope', () => {
    const url = buildYandexAuthorizeUrl(
      'client-1',
      'http://localhost:3000/oauth/yandex/callback',
    );
    const parsed = new URL(url);
    expect(parsed.searchParams.get('client_id')).toBe('client-1');
    expect(parsed.searchParams.get('scope')).toBe(YANDEX_DISK_OAUTH_SCOPE);
  });
});

describe('Yandex Disk API path helpers', () => {
  const { normalizePath, parentDiskPath } = __testables;

  it('normalizePath and parentDiskPath', () => {
    expect(normalizePath('/Docs/a')).toBe('disk:/Docs/a');
    expect(parentDiskPath('disk:/Docs/a')).toBe('disk:/Docs');
  });
});

describe('integrations catalog', () => {
  it('marks all requested providers available', () => {
    expect(INTEGRATIONS_CATALOG.every((x) => x.status === 'available')).toBe(true);
    expect(INTEGRATIONS_CATALOG.map((x) => x.id)).toEqual(
      expect.arrayContaining([
        'yandex-disk',
        'yandex-mail',
        'google-drive',
        'gmail',
        'onedrive',
        'outlook',
        'notion',
        'github',
      ]),
    );
  });

  it('exposes CONFIG_INTEGRATIONS', () => {
    expect(API_ENDPOINTS.CONFIG_INTEGRATIONS).toBe('/api/config/integrations');
  });
});
