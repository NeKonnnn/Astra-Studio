import { defaultRedirectUri, fetchIntegrationsConfig } from '../config';
import { buildAuthorizeUrl, openOAuthPopup } from '../shared/oauthPopup';
import { exchangeOAuthCode, integrationProxy } from '../shared/proxy';
import type { ListItem } from '../types';

let token: string | null = null;

export async function getNotionToken(): Promise<string> {
  if (token) return token;
  const cfg = await fetchIntegrationsConfig();
  if (!cfg.notion.client_id) throw new Error('Notion: задайте NOTION_CLIENT_ID (+ SECRET)');
  const redirectUri = cfg.notion.redirect_uri || defaultRedirectUri();
  const url = buildAuthorizeUrl('https://api.notion.com/v1/oauth/authorize', {
    client_id: cfg.notion.client_id,
    response_type: 'code',
    owner: 'user',
    redirect_uri: redirectUri,
  });
  const result = await openOAuthPopup(url);
  if (!result.code) throw new Error('Notion: нет code');
  token = await exchangeOAuthCode({
    provider: 'notion',
    code: result.code,
    redirectUri,
  });
  return token;
}

export async function listNotionPages(accessToken: string): Promise<ListItem[]> {
  const proxied = await integrationProxy({
    url: 'https://api.notion.com/v1/search',
    token: accessToken,
    method: 'POST',
    jsonBody: {
      page_size: 30,
      filter: { property: 'object', value: 'page' },
    },
  });
  if (!proxied.ok) throw new Error(`Notion search ${proxied.status}`);
  const results = proxied.data?.results || [];
  return results.map((p: any) => {
    const title =
      p.properties?.title?.title?.[0]?.plain_text ||
      p.properties?.Name?.title?.[0]?.plain_text ||
      p.id;
    return { id: p.id, title: String(title), subtitle: p.url };
  });
}

export async function fetchNotionPageText(
  accessToken: string,
  pageId: string,
  title: string,
): Promise<{ name: string; text: string }> {
  const blocks = await integrationProxy({
    url: `https://api.notion.com/v1/blocks/${pageId}/children?page_size=50`,
    token: accessToken,
  });
  if (!blocks.ok) throw new Error(`Notion blocks ${blocks.status}`);
  const lines: string[] = [`# ${title}`, ''];
  for (const b of blocks.data?.results || []) {
    const rich =
      b.paragraph?.rich_text ||
      b.heading_1?.rich_text ||
      b.heading_2?.rich_text ||
      b.heading_3?.rich_text ||
      b.bulleted_list_item?.rich_text ||
      b.to_do?.rich_text ||
      [];
    const t = (rich || []).map((x: any) => x.plain_text).join('');
    if (t) lines.push(t);
  }
  return { name: `${title.slice(0, 80)}.md`, text: lines.join('\n') };
}
