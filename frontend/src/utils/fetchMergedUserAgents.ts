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
  is_public?: boolean;
  /** full | rag_llm — объём правки на своём клоне */
  edit_scope?: 'full' | 'rag_llm' | null;
}

export type FetchMergedUserAgentsOptions = {
  /** LDAP/Keycloak admin — все агенты платформы, в т.ч. приватные */
  isAdmin?: boolean;
};

const ADMIN_PAGE_SIZE = 200;

type AgentsPageResponse = {
  agents?: UserAgentListItem[];
  total?: number;
  page?: number;
  pages?: number;
};

/** Загрузить все страницы /api/agents/admin/all (без потолка по числу агентов). */
export async function fetchAllAgentsForAdmin(): Promise<UserAgentListItem[]> {
  const all: UserAgentListItem[] = [];
  let page = 1;
  let pages = 1;

  while (page <= pages) {
    const resp = await authFetch(
      getApiUrl(`/api/agents/admin/all?limit=${ADMIN_PAGE_SIZE}&page=${page}`),
    );
    if (!resp.ok) break;
    const data = (await resp.json()) as AgentsPageResponse;
    const batch = data.agents || [];
    for (const a of batch) {
      all.push({
        ...a,
        my_permission: (a.my_permission || 'owner') as UserAgentListItem['my_permission'],
      });
    }
    pages = Math.max(1, Number(data.pages) || 1);
    // Защита от пустой/битой пагинации
    if (batch.length === 0) break;
    page += 1;
  }

  return all;
}

/** Мои + расшаренные + из галереи (закладки) — единый список для конструктора и меню. */
export async function fetchMergedUserAgents(
  token: string | null | undefined,
  limit = 100,
  options?: FetchMergedUserAgentsOptions,
): Promise<UserAgentListItem[]> {
  if (!token) return [];

  const qs = `?limit=${limit}`;

  if (options?.isAdmin) {
    const all = await fetchAllAgentsForAdmin();
    // Fallback: admin/all мог упасть (старый le=100 на AgentFilters) — хотя бы «мои».
    if (all.length > 0) return all;
  }
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
