/**
 * Personal entity KB (meta.source=memory_library) for the «Библиотека» toggle.
 */
import { API_ENDPOINTS, getApiUrl, getAuthFetchHeaders } from '../config/api';

const CACHE_KEY = 'personal_library_kb_id';

export function getCachedPersonalLibraryKbId(): number | null {
  if (typeof localStorage === 'undefined') return null;
  const raw = localStorage.getItem(CACHE_KEY);
  if (!raw) return null;
  const n = parseInt(raw, 10);
  return Number.isFinite(n) && n > 0 ? n : null;
}

export function setCachedPersonalLibraryKbId(id: number | null): void {
  if (typeof localStorage === 'undefined') return;
  if (id == null || !Number.isFinite(id) || id <= 0) {
    localStorage.removeItem(CACHE_KEY);
    return;
  }
  localStorage.setItem(CACHE_KEY, String(id));
}

export async function ensurePersonalLibraryKbId(): Promise<number | null> {
  const cached = getCachedPersonalLibraryKbId();
  if (cached != null) return cached;
  const resp = await fetch(
    getApiUrl(`${API_ENDPOINTS.KNOWLEDGE_BASES}/ensure-personal-library`),
    { method: 'POST', headers: getAuthFetchHeaders() },
  );
  if (!resp.ok) return null;
  const data = (await resp.json()) as { id?: number };
  const id = typeof data.id === 'number' ? data.id : null;
  if (id != null) setCachedPersonalLibraryKbId(id);
  return id;
}
