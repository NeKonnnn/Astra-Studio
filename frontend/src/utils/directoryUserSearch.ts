import { getApiUrl } from '../config/api';

export type DirectoryUserHit = {
  user_id: string;
  username: string;
  full_name?: string | null;
  email?: string | null;
};

const SEARCH_CACHE_TTL_MS = 60_000;
const SEARCH_CACHE_MAX = 128;
const searchCache = new Map<string, { at: number; users: DirectoryUserHit[] }>();

function getCachedSearch(cacheKey: string): DirectoryUserHit[] | null {
  const row = searchCache.get(cacheKey);
  if (!row) return null;
  if (Date.now() - row.at > SEARCH_CACHE_TTL_MS) {
    searchCache.delete(cacheKey);
    return null;
  }
  return row.users;
}

function putCachedSearch(cacheKey: string, users: DirectoryUserHit[]): void {
  if (searchCache.size >= SEARCH_CACHE_MAX) {
    let oldestKey: string | undefined;
    let oldestAt = Infinity;
    searchCache.forEach((row, key) => {
      if (row.at < oldestAt) {
        oldestAt = row.at;
        oldestKey = key;
      }
    });
    if (oldestKey) searchCache.delete(oldestKey);
  }
  searchCache.set(cacheKey, { at: Date.now(), users });
}

/** Сбросить кэш поиска (например, при выходе / смене пользователя). */
export function clearDirectorySearchCache(): void {
  searchCache.clear();
}

/**
 * Идентификатор пользователя из JWT (`sub` / `user_id` / `username`) — только для скоупа кэша.
 * Сам токен в ключ не попадает. Если id извлечь нельзя (не JWT) — вернём null и кэшировать не будем.
 */
function cacheScopeFromToken(token: string): string | null {
  try {
    const part = (token || '').split('.')[1];
    if (!part) return null;
    const base64 = part.replace(/-/g, '+').replace(/_/g, '/');
    const padded = base64 + '='.repeat((4 - (base64.length % 4)) % 4);
    const json =
      typeof atob === 'function'
        ? decodeURIComponent(
            Array.from(atob(padded))
              .map((c) => '%' + c.charCodeAt(0).toString(16).padStart(2, '0'))
              .join(''),
          )
        : Buffer.from(padded, 'base64').toString('utf8');
    const payload = JSON.parse(json) as Record<string, unknown>;
    const id = payload.sub ?? payload.user_id ?? payload.username;
    return typeof id === 'string' || typeof id === 'number' ? String(id) : null;
  } catch {
    return null;
  }
}

export async function searchDirectoryUsers(
  token: string,
  q: string,
  limit = 10,
  signal?: AbortSignal,
  forShare = false,
): Promise<DirectoryUserHit[]> {
  const query = (q || '').trim();
  if (query.length < 2) return [];
  const scope = cacheScopeFromToken(token);
  const cacheKey =
    scope === null
      ? null
      : `${scope}\u0000${query.toLowerCase()}:${limit}:share=${forShare ? 1 : 0}`;
  const cached = cacheKey ? getCachedSearch(cacheKey) : null;
  if (cached) return cached;

  const shareParam = forShare ? '&for_share=true' : '';
  const resp = await fetch(
    getApiUrl(`/api/users/search?q=${encodeURIComponent(query)}&limit=${limit}${shareParam}`),
    { headers: { Authorization: `Bearer ${token}` }, signal },
  );
  if (!resp.ok) return [];
  const data = await resp.json().catch(() => ({}));
  const users: DirectoryUserHit[] = Array.isArray(data?.users) ? data.users : [];
  if (cacheKey) putCachedSearch(cacheKey, users);
  return users;
}

/** Явная форма логина: `@gpbu123` → `gpbu123` (email вида user@host сюда не попадает). */
function parseExplicitLogin(part: string): string | null {
  const m = part.match(/^@([a-zA-Z0-9._-]+)$/);
  return m ? m[1] : null;
}

function hitLogin(h: DirectoryUserHit): string {
  return (h.username || h.user_id || '').trim();
}

/**
 * Выбор пользователя только по ТОЧНОМУ совпадению токена (логин, email или ФИО).
 * Единственный частичный результат поиска не принимается; неоднозначное ФИО — тоже.
 */
function pickExactMatch(hits: DirectoryUserHit[], part: string): DirectoryUserHit | undefined {
  const lower = part.trim().toLowerCase();
  const byLogin = hits.filter((h) => hitLogin(h).toLowerCase() === lower);
  if (byLogin.length === 1) return byLogin[0];
  const byEmail = hits.filter((h) => (h.email || '').trim().toLowerCase() === lower);
  if (byEmail.length === 1) return byEmail[0];
  const byName = hits.filter((h) => (h.full_name || '').trim().toLowerCase() === lower);
  if (byName.length === 1) return byName[0];
  return undefined;
}

/**
 * Разобрать ввод (логин / ФИО / email, через запятую) в логины для POST /share.
 * - `@login` — явный логин, принимается как есть;
 * - любой другой токен (в т.ч. «gpbu123») ищется в LDAP и принимается только при точном
 *   совпадении логина, email или ФИО. Частичные/единственные совпадения → unresolved.
 */
export async function resolveShareUsernames(
  token: string,
  rawInput: string,
): Promise<{ usernames: string[]; unresolved: string[] }> {
  const parts = rawInput
    .split(/[,;\n]+/)
    .map((s) => s.trim())
    .filter(Boolean);
  if (parts.length === 0) return { usernames: [], unresolved: [] };

  const usernames: string[] = [];
  const unresolved: string[] = [];
  const seen = new Set<string>();

  const push = (uid: string) => {
    const key = uid.trim().toLowerCase();
    if (!key || seen.has(key)) return;
    seen.add(key);
    usernames.push(uid.trim());
  };

  // Результаты считаем параллельно, но применяем строго в порядке ввода (детерминированный вывод).
  const resolved = await Promise.all(
    parts.map(async (part): Promise<string | null> => {
      const explicit = parseExplicitLogin(part);
      if (explicit) return explicit;
      try {
        const hits = await searchDirectoryUsers(token, part, 8, undefined, true);
        const match = pickExactMatch(hits, part);
        return match ? hitLogin(match) || null : null;
      } catch (err) {
        console.warn('resolveShareUsernames: directory search failed', part, err);
        return null;
      }
    }),
  );

  parts.forEach((part, i) => {
    const login = resolved[i];
    if (login) push(login);
    else unresolved.push(part);
  });

  return { usernames, unresolved };
}

export function directoryUserOptionLabel(u: DirectoryUserHit): string {
  const fio = (u.full_name || '').trim();
  const login = (u.username || u.user_id || '').trim();
  const email = (u.email || '').trim();
  if (fio) return fio;
  return login || email || '';
}

export function directoryUserOptionSecondary(u: DirectoryUserHit): string {
  const login = (u.username || u.user_id || '').trim();
  const email = (u.email || '').trim();
  if (login && email) return `${login} · ${email}`;
  return email || login;
}

export type ShareApiResponse = {
  success?: boolean;
  message?: string;
  warnings?: string[];
  skipped_details?: Array<{ user_id: string; reason: string }>;
  capped?: Array<{ user_id: string; requested: string; granted: string }>;
};

export function formatShareFeedback(data: ShareApiResponse): {
  success: string | null;
  warning: string | null;
} {
  const messages: string[] = [];
  if (typeof data.message === 'string' && data.message.trim()) {
    messages.push(data.message.trim());
  }
  if (Array.isArray(data.warnings)) {
    for (const w of data.warnings) {
      if (typeof w === 'string' && w.trim()) messages.push(w.trim());
    }
  }
  if (Array.isArray(data.skipped_details)) {
    for (const row of data.skipped_details) {
      if (row?.user_id && row?.reason) {
        messages.push(`${row.user_id}: ${row.reason}`);
      }
    }
  }

  const unique = Array.from(new Set(messages));
  if (unique.length === 0) {
    return { success: 'Доступ выдан', warning: null };
  }

  const hasSuccess = Boolean(data.success);
  if (hasSuccess) {
    return { success: unique[0], warning: unique.length > 1 ? unique.slice(1).join(' ') : null };
  }
  return { success: null, warning: unique.join(' ') };
}
