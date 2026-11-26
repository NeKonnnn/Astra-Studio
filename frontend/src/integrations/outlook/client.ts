import { defaultRedirectUri, fetchIntegrationsConfig } from '../config';
import { buildAuthorizeUrl, openOAuthPopup } from '../shared/oauthPopup';
import type { ListItem } from '../types';

const GRAPH = 'https://graph.microsoft.com/v1.0';
let token: string | null = null;

export async function getOutlookToken(): Promise<string> {
  if (token) return token;
  const cfg = await fetchIntegrationsConfig();
  const clientId = cfg.outlook.client_id || cfg.onedrive.client_id;
  if (!clientId) throw new Error('Outlook: задайте MICROSOFT_CLIENT_ID / OUTLOOK_CLIENT_ID');
  const redirectUri = cfg.outlook.redirect_uri || cfg.onedrive.redirect_uri || defaultRedirectUri();
  const url = buildAuthorizeUrl(
    'https://login.microsoftonline.com/common/oauth2/v2.0/authorize',
    {
      client_id: clientId,
      response_type: 'token',
      redirect_uri: redirectUri,
      scope: 'Mail.Read Calendars.Read User.Read offline_access',
      response_mode: 'fragment',
    },
  );
  const result = await openOAuthPopup(url);
  if (!result.accessToken) throw new Error('Outlook: нет access_token');
  token = result.accessToken;
  return token;
}

export async function listOutlookMessages(accessToken: string): Promise<ListItem[]> {
  const resp = await fetch(
    `${GRAPH}/me/messages?$top=30&$select=id,subject,from,receivedDateTime,bodyPreview`,
    { headers: { Authorization: `Bearer ${accessToken}` } },
  );
  if (!resp.ok) throw new Error(`Outlook list ${resp.status}`);
  const data = await resp.json();
  return (data.value || []).map((m: any) => ({
    id: m.id,
    title: m.subject || '(без темы)',
    subtitle: `${m.from?.emailAddress?.address || ''} · ${m.receivedDateTime || ''}`.trim(),
  }));
}

export async function fetchOutlookMessageText(
  accessToken: string,
  id: string,
): Promise<{ name: string; text: string }> {
  const resp = await fetch(
    `${GRAPH}/me/messages/${encodeURIComponent(id)}?$select=subject,from,receivedDateTime,body`,
    { headers: { Authorization: `Bearer ${accessToken}` } },
  );
  if (!resp.ok) throw new Error(`Outlook get ${resp.status}`);
  const m = await resp.json();
  const subject = m.subject || 'outlook-message';
  const body = m.body?.content || '';
  const text = `From: ${m.from?.emailAddress?.address || ''}\nDate: ${m.receivedDateTime || ''}\nSubject: ${subject}\n\n${body}`;
  return { name: `${String(subject).slice(0, 80)}.txt`, text };
}

export async function listOutlookEvents(accessToken: string): Promise<ListItem[]> {
  const resp = await fetch(
    `${GRAPH}/me/events?$top=30&$select=id,subject,start,end,organizer&$orderby=start/dateTime`,
    { headers: { Authorization: `Bearer ${accessToken}` } },
  );
  if (!resp.ok) throw new Error(`Outlook events ${resp.status}`);
  const data = await resp.json();
  return (data.value || []).map((e: any) => ({
    id: e.id,
    title: e.subject || '(событие)',
    subtitle: `${e.start?.dateTime || ''} → ${e.end?.dateTime || ''}`,
  }));
}

export async function fetchOutlookEventText(
  accessToken: string,
  id: string,
): Promise<{ name: string; text: string }> {
  const resp = await fetch(`${GRAPH}/me/events/${encodeURIComponent(id)}`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!resp.ok) throw new Error(`Outlook event ${resp.status}`);
  const e = await resp.json();
  const subject = e.subject || 'event';
  const text = `Subject: ${subject}\nStart: ${e.start?.dateTime}\nEnd: ${e.end?.dateTime}\nOrganizer: ${e.organizer?.emailAddress?.address || ''}\n\n${e.body?.content || e.bodyPreview || ''}`;
  return { name: `${String(subject).slice(0, 80)}.txt`, text };
}
