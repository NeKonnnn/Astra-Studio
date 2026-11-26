import { getYandexDiskCredentials } from './config';

export const YANDEX_DISK_OAUTH_SCOPE = 'cloud_api:disk.read';
export const YANDEX_OAUTH_MESSAGE_TYPE = 'yandex-disk-oauth';

const TOKEN_STORAGE_KEY = 'yandex_disk_oauth_token';

type StoredToken = {
  accessToken: string;
  expiresAt: number;
};

let memoryToken: StoredToken | null = null;

export function parseOAuthHash(hash: string): {
  accessToken: string | null;
  expiresIn: number | null;
  error: string | null;
} {
  const raw = hash.startsWith('#') ? hash.slice(1) : hash;
  const params = new URLSearchParams(raw);
  const accessToken = params.get('access_token');
  const expiresRaw = params.get('expires_in');
  const expiresIn = expiresRaw ? Number(expiresRaw) : null;
  const error = params.get('error') || params.get('error_description');
  return {
    accessToken: accessToken || null,
    expiresIn: Number.isFinite(expiresIn) ? expiresIn : null,
    error: error || null,
  };
}

function readStoredToken(): StoredToken | null {
  if (memoryToken && memoryToken.expiresAt > Date.now() + 30_000) {
    return memoryToken;
  }
  try {
    const raw = sessionStorage.getItem(TOKEN_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as StoredToken;
    if (
      parsed?.accessToken &&
      typeof parsed.expiresAt === 'number' &&
      parsed.expiresAt > Date.now() + 30_000
    ) {
      memoryToken = parsed;
      return parsed;
    }
  } catch {
    /* ignore */
  }
  return null;
}

function storeToken(accessToken: string, expiresInSec: number | null): void {
  const ttl = expiresInSec && expiresInSec > 0 ? expiresInSec : 3600;
  const stored: StoredToken = {
    accessToken,
    expiresAt: Date.now() + ttl * 1000,
  };
  memoryToken = stored;
  try {
    sessionStorage.setItem(TOKEN_STORAGE_KEY, JSON.stringify(stored));
  } catch {
    /* ignore */
  }
}

export function clearYandexDiskToken(): void {
  memoryToken = null;
  try {
    sessionStorage.removeItem(TOKEN_STORAGE_KEY);
  } catch {
    /* ignore */
  }
}

export function buildYandexAuthorizeUrl(clientId: string, redirectUri: string): string {
  const url = new URL('https://oauth.yandex.ru/authorize');
  url.searchParams.set('response_type', 'token');
  url.searchParams.set('client_id', clientId);
  url.searchParams.set('redirect_uri', redirectUri);
  url.searchParams.set('scope', YANDEX_DISK_OAUTH_SCOPE);
  return url.toString();
}

/**
 * Implicit OAuth popup (аналог Google GIS token client / OneDrive MSAL popup).
 */
export async function getYandexDiskAuthToken(force = false): Promise<string> {
  if (!force) {
    const existing = readStoredToken();
    if (existing) return existing.accessToken;
  }

  const { enabled, clientId, redirectUri } = await getYandexDiskCredentials();
  if (!enabled || !clientId) {
    throw new Error('Яндекс.Диск не настроен (ENABLE_YANDEX_DISK_INTEGRATION / CLIENT_ID)');
  }

  const authorizeUrl = buildYandexAuthorizeUrl(clientId, redirectUri);

  return new Promise<string>((resolve, reject) => {
    const width = 560;
    const height = 720;
    const left = Math.max(0, window.screenX + (window.outerWidth - width) / 2);
    const top = Math.max(0, window.screenY + (window.outerHeight - height) / 2);
    const popup = window.open(
      authorizeUrl,
      'yandex_disk_oauth',
      `width=${width},height=${height},left=${left},top=${top},menubar=no,toolbar=no,status=no`,
    );

    if (!popup) {
      reject(new Error('Не удалось открыть окно авторизации Яндекса (popup blocked)'));
      return;
    }

    let settled = false;
    const cleanup = () => {
      window.removeEventListener('message', onMessage);
      window.clearInterval(closeWatcher);
    };

    const finish = (fn: () => void) => {
      if (settled) return;
      settled = true;
      cleanup();
      fn();
    };

    const onMessage = (event: MessageEvent) => {
      if (event.origin !== window.location.origin) return;
      const data = event.data;
      if (!data || data.type !== YANDEX_OAUTH_MESSAGE_TYPE) return;

      if (data.error) {
        finish(() => reject(new Error(String(data.error))));
        return;
      }
      const token = String(data.accessToken || '').trim();
      if (!token) {
        finish(() => reject(new Error('Яндекс не вернул access_token')));
        return;
      }
      storeToken(token, typeof data.expiresIn === 'number' ? data.expiresIn : null);
      finish(() => resolve(token));
      try {
        popup.close();
      } catch {
        /* ignore */
      }
    };

    window.addEventListener('message', onMessage);

    const closeWatcher = window.setInterval(() => {
      if (popup.closed) {
        finish(() => reject(new Error('Авторизация Яндекс.Диска отменена')));
      }
    }, 500);
  });
}
