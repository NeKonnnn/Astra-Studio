import {
  clearIntegrationsConfigCache,
  defaultRedirectUri,
  fetchIntegrationsConfig,
} from '../config';

export { fetchIntegrationsConfig, clearIntegrationsConfigCache };

export function defaultYandexRedirectUri(): string {
  return defaultRedirectUri('/oauth/yandex/callback');
}

export async function getYandexDiskCredentials(): Promise<{
  enabled: boolean;
  clientId: string;
  redirectUri: string;
  flagOn: boolean;
  configured: boolean;
}> {
  const cfg = await fetchIntegrationsConfig();
  const clientId = cfg.yandex_disk.client_id;
  const redirectUri = cfg.yandex_disk.redirect_uri || defaultYandexRedirectUri();
  const flagOn = Boolean(cfg.features.enable_yandex_disk_integration);
  const configured = Boolean(cfg.yandex_disk.configured ?? clientId);
  return {
    enabled: flagOn && configured,
    clientId,
    redirectUri,
    flagOn,
    configured,
  };
}
