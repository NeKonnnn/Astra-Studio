import { defaultRedirectUri, fetchIntegrationsConfig } from '../config';
import { buildAuthorizeUrl, openOAuthPopup } from '../shared/oauthPopup';
import { exchangeOAuthCode } from '../shared/proxy';
import type { ListItem } from '../types';

let token: string | null = null;

export async function getGitHubToken(): Promise<string> {
  if (token) return token;
  const cfg = await fetchIntegrationsConfig();
  if (!cfg.github.client_id) throw new Error('GitHub: задайте GITHUB_CLIENT_ID (+ SECRET на backend)');
  const redirectUri = cfg.github.redirect_uri || defaultRedirectUri();
  const url = buildAuthorizeUrl('https://github.com/login/oauth/authorize', {
    client_id: cfg.github.client_id,
    redirect_uri: redirectUri,
    scope: 'repo read:user',
  });
  const result = await openOAuthPopup(url);
  if (result.accessToken) {
    token = result.accessToken;
    return token;
  }
  if (!result.code) throw new Error('GitHub: нет code');
  token = await exchangeOAuthCode({
    provider: 'github',
    code: result.code,
    redirectUri,
  });
  return token;
}

export async function listGitHubRepos(accessToken: string): Promise<ListItem[]> {
  const resp = await fetch('https://api.github.com/user/repos?per_page=30&sort=updated', {
    headers: {
      Authorization: `Bearer ${accessToken}`,
      Accept: 'application/vnd.github+json',
    },
  });
  if (!resp.ok) throw new Error(`GitHub repos ${resp.status}`);
  const data = await resp.json();
  return (data || []).map((r: any) => ({
    id: r.full_name,
    title: r.full_name,
    subtitle: r.description || r.language || undefined,
    path: r.full_name,
  }));
}

export async function listGitHubFiles(
  accessToken: string,
  fullName: string,
  path = '',
): Promise<ListItem[]> {
  const url = `https://api.github.com/repos/${fullName}/contents/${path}`;
  const resp = await fetch(url, {
    headers: {
      Authorization: `Bearer ${accessToken}`,
      Accept: 'application/vnd.github+json',
    },
  });
  if (!resp.ok) throw new Error(`GitHub contents ${resp.status}`);
  const data = await resp.json();
  const arr = Array.isArray(data) ? data : [data];
  return arr.map((f: any) => ({
    id: `${fullName}:${f.path}`,
    title: f.name,
    subtitle: f.type,
    path: f.path,
  }));
}

export async function fetchGitHubFileText(
  accessToken: string,
  fullName: string,
  path: string,
): Promise<{ name: string; text: string }> {
  const resp = await fetch(
    `https://api.github.com/repos/${fullName}/contents/${path}`,
    {
      headers: {
        Authorization: `Bearer ${accessToken}`,
        Accept: 'application/vnd.github.raw',
      },
    },
  );
  if (!resp.ok) throw new Error(`GitHub file ${resp.status}`);
  const text = await resp.text();
  const name = path.split('/').pop() || 'file.txt';
  return { name, text };
}
