import { authFetch, getApiUrl } from '../../config/api';

export async function integrationProxy<T = any>(opts: {
  url: string;
  token: string;
  tokenType?: string;
  method?: string;
  jsonBody?: unknown;
}): Promise<{ status: number; ok: boolean; data: T }> {
  const response = await authFetch(getApiUrl('/api/integrations/proxy'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      url: opts.url,
      token: opts.token,
      token_type: opts.tokenType || 'Bearer',
      method: opts.method || 'GET',
      json_body: opts.jsonBody ?? null,
    }),
  });
  if (!response.ok) {
    const detail = await response.text().catch(() => '');
    throw new Error(detail || `proxy ${response.status}`);
  }
  return response.json();
}

export async function exchangeOAuthCode(opts: {
  provider: string;
  code: string;
  redirectUri: string;
}): Promise<string> {
  const response = await authFetch(getApiUrl('/api/integrations/oauth/exchange'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      provider: opts.provider,
      code: opts.code,
      redirect_uri: opts.redirectUri,
    }),
  });
  if (!response.ok) {
    const detail = await response.text().catch(() => '');
    throw new Error(detail || `oauth exchange ${response.status}`);
  }
  const data = await response.json();
  const token = String(data?.access_token || '').trim();
  if (!token) throw new Error('No access_token');
  return token;
}
