import { getAuthFetchHeaders } from '../config/api';

type RefreshFn = () => Promise<string | null>;

let refreshHandler: RefreshFn | null = null;

/** Регистрируется из AuthContext (единый in-flight refresh с axios-interceptor). */
export function setAuthTokenRefreshHandler(fn: RefreshFn | null): void {
  refreshHandler = fn;
}

function mergeAuthHeaders(initHeaders?: HeadersInit): Headers {
  const headers = new Headers(initHeaders || {});
  const auth = getAuthFetchHeaders();
  Object.entries(auth).forEach(([key, value]) => {
    if (!headers.has(key)) {
      headers.set(key, value);
    }
  });
  return headers;
}

/**
 * fetch с Authorization; при 401 один раз пробует refresh (если handler задан) и повторяет.
 * Нужен для raw-fetch вне axios (projects, reindex-status и т.п.).
 */
export async function authFetch(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  const firstHeaders = mergeAuthHeaders(init?.headers);
  const response = await fetch(input, { ...init, headers: firstHeaders });
  if (response.status !== 401 || !refreshHandler) {
    return response;
  }
  const newToken = await refreshHandler();
  if (!newToken) {
    return response;
  }
  const retryHeaders = mergeAuthHeaders(init?.headers);
  retryHeaders.set('Authorization', `Bearer ${newToken}`);
  return fetch(input, { ...init, headers: retryHeaders });
}
