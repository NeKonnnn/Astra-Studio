import type { YandexDiskListResult, YandexDiskResource } from './types';

const DISK_API_BASE = 'https://cloud-api.yandex.net/v1/disk';

function authHeaders(token: string): HeadersInit {
  return {
    Authorization: `OAuth ${token}`,
    Accept: 'application/json',
  };
}

function normalizePath(path: string): string {
  const trimmed = (path || 'disk:/').trim();
  if (!trimmed || trimmed === '/') return 'disk:/';
  if (trimmed.startsWith('disk:')) return trimmed;
  return `disk:/${trimmed.replace(/^\//, '')}`;
}

export function parentDiskPath(path: string): string | null {
  const normalized = normalizePath(path);
  if (normalized === 'disk:/' || normalized === 'disk:') return null;
  const withoutPrefix = normalized.replace(/^disk:/, '');
  const parts = withoutPrefix.split('/').filter(Boolean);
  if (parts.length <= 1) return 'disk:/';
  parts.pop();
  return `disk:/${parts.join('/')}`;
}

export async function listYandexDiskResources(
  token: string,
  path = 'disk:/',
  limit = 100,
): Promise<YandexDiskListResult> {
  const diskPath = normalizePath(path);
  const url = new URL(`${DISK_API_BASE}/resources`);
  url.searchParams.set('path', diskPath);
  url.searchParams.set('limit', String(limit));
  url.searchParams.set('offset', '0');
  url.searchParams.set('sort', 'name');
  url.searchParams.set(
    'fields',
    'name,path,_embedded.items.name,_embedded.items.path,_embedded.items.type,_embedded.items.mime_type,_embedded.items.size,_embedded.items.modified,_embedded.items.preview',
  );

  const response = await fetch(url.toString(), { headers: authHeaders(token) });
  if (!response.ok) {
    const text = await response.text().catch(() => '');
    throw new Error(`Яндекс.Диск: не удалось получить список (${response.status}) ${text}`);
  }

  const data = await response.json();
  const rawItems = data?._embedded?.items || [];
  const items: YandexDiskResource[] = (Array.isArray(rawItems) ? rawItems : []).map(
    (item: Record<string, unknown>) => ({
      name: String(item.name || ''),
      path: String(item.path || ''),
      type: String(item.type || 'file'),
      mime_type: item.mime_type ? String(item.mime_type) : undefined,
      size: typeof item.size === 'number' ? item.size : undefined,
      modified: item.modified ? String(item.modified) : undefined,
      preview: item.preview ? String(item.preview) : undefined,
    }),
  );

  return {
    path: String(data.path || diskPath),
    name: String(data.name || ''),
    items,
  };
}

export async function getYandexDiskDownloadHref(
  token: string,
  path: string,
): Promise<string> {
  const url = new URL(`${DISK_API_BASE}/resources/download`);
  url.searchParams.set('path', normalizePath(path));

  const response = await fetch(url.toString(), { headers: authHeaders(token) });
  if (!response.ok) {
    const text = await response.text().catch(() => '');
    throw new Error(`Яндекс.Диск: нет ссылки на скачивание (${response.status}) ${text}`);
  }
  const data = await response.json();
  const href = String(data?.href || '').trim();
  if (!href) {
    throw new Error('Яндекс.Диск: пустая ссылка на скачивание');
  }
  return href;
}

export async function downloadYandexDiskFile(
  token: string,
  path: string,
  name: string,
): Promise<{ name: string; path: string; blob: Blob }> {
  const href = await getYandexDiskDownloadHref(token, path);
  const response = await fetch(href);
  if (!response.ok) {
    throw new Error(`Яндекс.Диск: ошибка скачивания (${response.status})`);
  }
  const blob = await response.blob();
  return { name, path, blob };
}

/** Чистые helpers для unit-тестов. */
export const __testables = {
  normalizePath,
  parentDiskPath,
};
