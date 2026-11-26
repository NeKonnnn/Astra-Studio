import { fetchIntegrationsConfig } from '../config';
import type { ListItem } from '../types';

declare global {
  interface Window {
    google?: any;
  }
}

let clientId = '';
let token: string | null = null;

async function loadGis(): Promise<void> {
  if (window.google?.accounts?.oauth2) return;
  await new Promise<void>((resolve, reject) => {
    const s = document.createElement('script');
    s.src = 'https://accounts.google.com/gsi/client';
    s.onload = () => resolve();
    s.onerror = () => reject(new Error('GSI load failed'));
    document.body.appendChild(s);
  });
}

export async function getGmailToken(): Promise<string> {
  if (token) return token;
  const cfg = await fetchIntegrationsConfig();
  clientId = cfg.gmail.client_id || cfg.google_drive.client_id;
  if (!clientId) throw new Error('Gmail: задайте GOOGLE_CLIENT_ID / GMAIL_CLIENT_ID');
  await loadGis();
  return new Promise((resolve, reject) => {
    const tc = window.google.accounts.oauth2.initTokenClient({
      client_id: clientId,
      scope: 'https://www.googleapis.com/auth/gmail.readonly',
      callback: (r: any) => {
        if (r.access_token) {
          token = r.access_token;
          resolve(token!);
        } else reject(new Error('Gmail OAuth failed'));
      },
      error_callback: (e: any) => reject(new Error(e?.message || 'Gmail OAuth error')),
    });
    tc.requestAccessToken();
  });
}

export async function listGmailMessages(accessToken: string): Promise<ListItem[]> {
  const listUrl =
    'https://gmail.googleapis.com/gmail/v1/users/me/messages?maxResults=30&q=in:inbox';
  const listResp = await fetch(listUrl, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!listResp.ok) throw new Error(`Gmail list ${listResp.status}`);
  const listData = await listResp.json();
  const ids: string[] = (listData.messages || []).map((m: any) => m.id);
  const items: ListItem[] = [];
  for (const id of ids.slice(0, 20)) {
    const meta = await fetch(
      `https://gmail.googleapis.com/gmail/v1/users/me/messages/${id}?format=metadata&metadataHeaders=Subject&metadataHeaders=From&metadataHeaders=Date`,
      { headers: { Authorization: `Bearer ${accessToken}` } },
    );
    if (!meta.ok) continue;
    const msg = await meta.json();
    const headers = msg.payload?.headers || [];
    const get = (n: string) =>
      headers.find((h: any) => String(h.name).toLowerCase() === n.toLowerCase())?.value || '';
    items.push({
      id,
      title: get('Subject') || '(без темы)',
      subtitle: `${get('From')} · ${get('Date')}`.trim(),
    });
  }
  return items;
}

export async function fetchGmailMessageText(
  accessToken: string,
  id: string,
): Promise<{ name: string; text: string }> {
  const resp = await fetch(
    `https://gmail.googleapis.com/gmail/v1/users/me/messages/${id}?format=full`,
    { headers: { Authorization: `Bearer ${accessToken}` } },
  );
  if (!resp.ok) throw new Error(`Gmail get ${resp.status}`);
  const msg = await resp.json();
  const headers = msg.payload?.headers || [];
  const get = (n: string) =>
    headers.find((h: any) => String(h.name).toLowerCase() === n.toLowerCase())?.value || '';
  const subject = get('Subject') || 'gmail-message';
  let body = '';
  const walk = (part: any) => {
    if (!part) return;
    if (part.mimeType === 'text/plain' && part.body?.data) {
      body += atob(String(part.body.data).replace(/-/g, '+').replace(/_/g, '/'));
    }
    (part.parts || []).forEach(walk);
  };
  walk(msg.payload);
  if (!body) body = msg.snippet || '';
  const text = `From: ${get('From')}\nDate: ${get('Date')}\nSubject: ${subject}\n\n${body}`;
  return { name: `${subject.slice(0, 80)}.txt`, text };
}
