import { getApiUrl, API_ENDPOINTS } from '../config/api';
import type { IntegrationsPublicConfig, ProviderPublicConfig } from './types';

const EMPTY_PROVIDER: ProviderPublicConfig = {
  client_id: '',
  api_key: '',
  redirect_uri: '',
  configured: false,
};

const EMPTY: IntegrationsPublicConfig = {
  features: {},
  yandex_disk: { ...EMPTY_PROVIDER },
  yandex_mail: { ...EMPTY_PROVIDER },
  yandex_calendar: { ...EMPTY_PROVIDER },
  yandex_meetings: { ...EMPTY_PROVIDER },
  google_drive: { ...EMPTY_PROVIDER },
  gmail: { ...EMPTY_PROVIDER },
  onedrive: { ...EMPTY_PROVIDER },
  outlook: { ...EMPTY_PROVIDER },
  notion: { ...EMPTY_PROVIDER },
  github: { ...EMPTY_PROVIDER },
};

let cached: IntegrationsPublicConfig | null = null;
let inflight: Promise<IntegrationsPublicConfig> | null = null;

function normProvider(raw: any): ProviderPublicConfig {
  const client_id = String(raw?.client_id || '').trim();
  return {
    client_id,
    api_key: String(raw?.api_key || '').trim(),
    redirect_uri: String(raw?.redirect_uri || '').trim(),
    configured: raw?.configured != null ? Boolean(raw.configured) : Boolean(client_id),
  };
}

export async function fetchIntegrationsConfig(force = false): Promise<IntegrationsPublicConfig> {
  if (!force && cached) return cached;
  if (!force && inflight) return inflight;

  inflight = (async () => {
    try {
      const response = await fetch(getApiUrl(API_ENDPOINTS.CONFIG_INTEGRATIONS), {
        credentials: 'include',
        headers: { Accept: 'application/json' },
      });
      if (!response.ok) throw new Error(`integrations config ${response.status}`);
      const data = await response.json();
      cached = {
        features: { ...(data?.features || {}) },
        yandex_disk: normProvider(data?.yandex_disk),
        yandex_mail: normProvider(data?.yandex_mail),
        yandex_calendar: normProvider(data?.yandex_calendar),
        yandex_meetings: normProvider(data?.yandex_meetings),
        google_drive: normProvider(data?.google_drive),
        gmail: normProvider(data?.gmail),
        onedrive: normProvider(data?.onedrive),
        outlook: normProvider(data?.outlook),
        notion: normProvider(data?.notion),
        github: normProvider(data?.github),
      };
      return cached;
    } catch {
      cached = EMPTY;
      return cached;
    } finally {
      inflight = null;
    }
  })();

  return inflight;
}

export function clearIntegrationsConfigCache(): void {
  cached = null;
  inflight = null;
}

export function defaultRedirectUri(path = '/oauth/integration/callback'): string {
  if (typeof window === 'undefined') return path;
  return `${window.location.origin}${path}`;
}
