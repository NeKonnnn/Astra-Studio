import { authFetch, getApiUrl } from '../config/api';

export interface UserAgentListItem {
  id: number;
  name: string;
  description?: string;
  system_prompt: string;
  config?: Record<string, unknown>;
  author_id?: string;
  author_name?: string;
  author_full_name?: string | null;
  my_permission?: 'owner' | 'editor' | 'viewer' | null;
  is_shared_with_me?: boolean;
  is_bookmarked?: boolean;
}

/** Мои + расшаренные + из галереи (закладки) — единый список для конструктора и меню. */
export async function fetchMergedUserAgents(
  token: string | null | undefined,
  limit = 100,
): Promise<UserAgentListItem[]> {
  if (!token) return [];

  const qs = `?limit=${limit}`;
  const [mineResp, sharedResp, bookmarksResp] = await Promise.all([
    authFetch(getApiUrl(`/api/agents/my/agents${qs}`)),
    authFetch(getApiUrl(`/api/agents/my/shared${qs}`)),
    authFetch(getApiUrl(`/api/agents/my/bookmarks${qs}`)),
  ]);

  const mine: UserAgentListItem[] = mineResp.ok ? (await mineResp.json()).agents || [] : [];
  const shared: UserAgentListItem[] = sharedResp.ok
    ? ((await sharedResp.json()).agents || []).map((a: UserAgentListItem) => ({
        ...a,
        is_shared_with_me: true,
      }))
    : [];
  const bookmarks: UserAgentListItem[] = bookmarksResp.ok
    ? ((await bookmarksResp.json()).agents || []).map((a: UserAgentListItem) => ({
        ...a,
        is_bookmarked: true,
        my_permission: a.my_permission || 'viewer',
      }))
    : [];

  const byId = new Map<number, UserAgentListItem>();
  for (const a of mine) {
    byId.set(a.id, { ...a, my_permission: a.my_permission || 'owner' });
  }
  for (const a of shared) {
    if (!byId.has(a.id)) byId.set(a.id, a);
  }
  for (const a of bookmarks) {
    if (!byId.has(a.id)) byId.set(a.id, a);
  }
  return Array.from(byId.values());
}
