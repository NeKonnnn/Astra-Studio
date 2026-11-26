import { defaultRedirectUri, fetchIntegrationsConfig } from '../config';
import { buildAuthorizeUrl, openOAuthPopup } from '../shared/oauthPopup';
import { integrationProxy } from '../shared/proxy';
import type { ListItem } from '../types';

const tokens: Record<string, string> = {};

async function yandexToken(kind: 'mail' | 'calendar' | 'meetings', scope: string): Promise<string> {
  if (tokens[kind]) return tokens[kind];
  const cfg = await fetchIntegrationsConfig();
  const provider =
    kind === 'mail'
      ? cfg.yandex_mail
      : kind === 'calendar'
        ? cfg.yandex_calendar
        : cfg.yandex_meetings;
  const clientId = provider.client_id || cfg.yandex_disk.client_id;
  if (!clientId) throw new Error('Яндекс: задайте YANDEX_OAUTH_CLIENT_ID / YANDEX_DISK_CLIENT_ID');
  const redirectUri =
    provider.redirect_uri || cfg.yandex_disk.redirect_uri || defaultRedirectUri('/oauth/yandex/callback');
  const url = buildAuthorizeUrl('https://oauth.yandex.ru/authorize', {
    response_type: 'token',
    client_id: clientId,
    redirect_uri: redirectUri,
    scope,
  });
  const result = await openOAuthPopup(url);
  if (!result.accessToken) throw new Error('Яндекс: нет access_token');
  tokens[kind] = result.accessToken;
  return tokens[kind];
}

export const getYandexMailToken = () =>
  yandexToken('mail', 'login:info mail:imap_ro mail:smtp');
export const getYandexCalendarToken = () =>
  yandexToken('calendar', 'login:info calendar:read');
export const getYandexMeetingsToken = () =>
  yandexToken('meetings', 'login:info telemost-api:conferences.read cloud_api:disk.read');

/** Яндекс Почта: через passport info + подсказка; полный IMAP в браузере недоступен — тянем метаданные пользователя и черновик контекста. */
export async function listYandexMailItems(accessToken: string): Promise<ListItem[]> {
  const me = await integrationProxy({
    url: 'https://login.yandex.ru/info?format=json',
    token: accessToken,
    tokenType: 'OAuth',
  });
  const login = me.data?.login || me.data?.default_email || 'yandex';
  // Реалистичный MVP: пользователь прикрепляет «срез» ящика как текстовый контекст.
  return [
    {
      id: 'inbox-summary',
      title: `Яндекс Почта · ${login}`,
      subtitle: 'Прикрепить профиль/контекст почты (IMAP API недоступен из браузера)',
    },
  ];
}

export async function fetchYandexMailText(
  accessToken: string,
  id: string,
): Promise<{ name: string; text: string }> {
  const me = await integrationProxy({
    url: 'https://login.yandex.ru/info?format=json',
    token: accessToken,
    tokenType: 'OAuth',
  });
  const text = [
    'Яндекс Почта (OAuth OK)',
    `login: ${me.data?.login || ''}`,
    `email: ${me.data?.default_email || ''}`,
    `real_name: ${me.data?.real_name || ''}`,
    '',
    'Полный список писем требует server-side IMAP. Токен сохранён для дальнейших MCP/backend-коннекторов.',
    `item: ${id}`,
  ].join('\n');
  return { name: 'yandex-mail.txt', text };
}

export async function listYandexCalendarItems(accessToken: string): Promise<ListItem[]> {
  // CalDAV discovery через proxy
  const resp = await integrationProxy({
    url: 'https://caldav.yandex.ru/',
    token: accessToken,
    tokenType: 'OAuth',
  });
  const raw = typeof resp.data?.raw === 'string' ? resp.data.raw : JSON.stringify(resp.data || {});
  return [
    {
      id: 'calendar-root',
      title: 'Яндекс Календарь',
      subtitle: resp.ok ? 'CalDAV доступен — прикрепить снимок' : `HTTP ${resp.status}`,
      path: raw.slice(0, 200),
    },
  ];
}

export async function fetchYandexCalendarText(
  accessToken: string,
  _id: string,
): Promise<{ name: string; text: string }> {
  const me = await integrationProxy({
    url: 'https://login.yandex.ru/info?format=json',
    token: accessToken,
    tokenType: 'OAuth',
  });
  const cal = await integrationProxy({
    url: 'https://caldav.yandex.ru/',
    token: accessToken,
    tokenType: 'OAuth',
  });
  const text = [
    'Яндекс Календарь (OAuth OK)',
    `user: ${me.data?.login || ''}`,
    `caldav_status: ${cal.status}`,
    '',
    String(cal.data?.raw || cal.data || '').slice(0, 8000),
  ].join('\n');
  return { name: 'yandex-calendar.txt', text };
}

export async function listYandexMeetingsItems(accessToken: string): Promise<ListItem[]> {
  const me = await integrationProxy({
    url: 'https://login.yandex.ru/info?format=json',
    token: accessToken,
    tokenType: 'OAuth',
  });
  return [
    {
      id: 'meetings-profile',
      title: 'Яндекс Встречи / Телемост',
      subtitle: me.data?.login || 'аккаунт подключён',
    },
  ];
}

export async function fetchYandexMeetingsText(
  accessToken: string,
  _id: string,
): Promise<{ name: string; text: string }> {
  const me = await integrationProxy({
    url: 'https://login.yandex.ru/info?format=json',
    token: accessToken,
    tokenType: 'OAuth',
  });
  const text = [
    'Яндекс Встречи (OAuth OK)',
    `user: ${me.data?.login || ''}`,
    `email: ${me.data?.default_email || ''}`,
    '',
    'Создавайте встречи в Телемосте; токен готов для API/MCP-коннектора.',
  ].join('\n');
  return { name: 'yandex-meetings.txt', text };
}
