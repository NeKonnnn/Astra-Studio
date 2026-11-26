import { parseOAuthHash } from '../yandex-disk/oauth';

export const INTEGRATION_OAUTH_MESSAGE = 'integration-oauth';

export function buildAuthorizeUrl(
  base: string,
  params: Record<string, string>,
): string {
  const url = new URL(base);
  Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));
  return url.toString();
}

/** Implicit / token / code popup. Callback page postMessage'ит результат. */
export function openOAuthPopup(authorizeUrl: string): Promise<{
  accessToken?: string;
  code?: string;
  expiresIn?: number | null;
}> {
  return new Promise((resolve, reject) => {
    const width = 560;
    const height = 720;
    const left = Math.max(0, window.screenX + (window.outerWidth - width) / 2);
    const top = Math.max(0, window.screenY + (window.outerHeight - height) / 2);
    const popup = window.open(
      authorizeUrl,
      'integration_oauth',
      `width=${width},height=${height},left=${left},top=${top},menubar=no,toolbar=no,status=no`,
    );
    if (!popup) {
      reject(new Error('Popup blocked'));
      return;
    }

    let settled = false;
    const finish = (fn: () => void) => {
      if (settled) return;
      settled = true;
      window.removeEventListener('message', onMessage);
      window.clearInterval(closeWatcher);
      fn();
    };

    const onMessage = (event: MessageEvent) => {
      if (event.origin !== window.location.origin) return;
      const data = event.data;
      if (!data || data.type !== INTEGRATION_OAUTH_MESSAGE) return;
      if (data.error) {
        finish(() => reject(new Error(String(data.error))));
        return;
      }
      finish(() =>
        resolve({
          accessToken: data.accessToken || undefined,
          code: data.code || undefined,
          expiresIn: data.expiresIn ?? null,
        }),
      );
      try {
        popup.close();
      } catch {
        /* ignore */
      }
    };

    window.addEventListener('message', onMessage);
    const closeWatcher = window.setInterval(() => {
      if (popup.closed) finish(() => reject(new Error('OAuth отменён')));
    }, 500);
  });
}

export { parseOAuthHash };
