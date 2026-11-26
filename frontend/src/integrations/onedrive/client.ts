import { defaultRedirectUri, fetchIntegrationsConfig } from '../config';
import { buildAuthorizeUrl, openOAuthPopup } from '../shared/oauthPopup';
import type { ListItem } from '../types';

const GRAPH = 'https://graph.microsoft.com/v1.0';
let token: string | null = null;

export async function getOneDriveToken(): Promise<string> {
  if (token) return token;
  const cfg = await fetchIntegrationsConfig();
  const clientId = cfg.onedrive.client_id;
  if (!clientId) throw new Error('OneDrive: задайте ONEDRIVE_CLIENT_ID / MICROSOFT_CLIENT_ID');
  const redirectUri = cfg.onedrive.redirect_uri || defaultRedirectUri();
  const url = buildAuthorizeUrl(
    'https://login.microsoftonline.com/common/oauth2/v2.0/authorize',
    {
      client_id: clientId,
      response_type: 'token',
      redirect_uri: redirectUri,
      scope: 'Files.Read User.Read offline_access',
      response_mode: 'fragment',
    },
  );
  const result = await openOAuthPopup(url);
  if (!result.accessToken) throw new Error('OneDrive: нет access_token');
  token = result.accessToken;
  return token;
}

export async function listOneDriveFiles(accessToken: string, path = 'root'): Promise<ListItem[]> {
  const url =
    path === 'root'
      ? `${GRAPH}/me/drive/root/children?$top=50`
      : `${GRAPH}/me/drive/items/${encodeURIComponent(path)}/children?$top=50`;
  const resp = await fetch(url, { headers: { Authorization: `Bearer ${accessToken}` } });
  if (!resp.ok) throw new Error(`OneDrive list ${resp.status}`);
  const data = await resp.json();
  return (data.value || [])
    .filter((x: any) => x.file)
    .map((x: any) => ({
      id: x.id,
      title: x.name,
      subtitle: x.size != null ? `${Math.round(x.size / 1024)} KB` : undefined,
      path: x.id,
    }));
}

export async function downloadOneDriveFile(
  accessToken: string,
  id: string,
  name: string,
): Promise<{ name: string; blob: Blob }> {
  const meta = await fetch(`${GRAPH}/me/drive/items/${encodeURIComponent(id)}`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!meta.ok) throw new Error(`OneDrive meta ${meta.status}`);
  const info = await meta.json();
  const downloadUrl = info['@microsoft.graph.downloadUrl'];
  if (!downloadUrl) throw new Error('OneDrive: нет downloadUrl');
  const fileResp = await fetch(downloadUrl);
  if (!fileResp.ok) throw new Error(`OneDrive download ${fileResp.status}`);
  return { name: name || info.name || 'file', blob: await fileResp.blob() };
}
