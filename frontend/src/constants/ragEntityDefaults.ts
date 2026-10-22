import { authFetch, getApiUrl, getAuthFetchHeaders } from '../config/api';

/** Дефолтные модели РАГ для проектов/агентов с backend (ConfigMap / ENV). */
export type RagEntityModelDefaults = {
  embeddingPath: string;
  rerankerPath: string;
};

let cachedDefaults: RagEntityModelDefaults | null = null;
let loadPromise: Promise<RagEntityModelDefaults> | null = null;

/** Сброс кэша (например, после смены пользователя). */
export function resetRagEntityDefaultsCache(): void {
  cachedDefaults = null;
  loadPromise = null;
}

/** Загрузить дефолты из GET /api/rag/settings (без entity_id). */
export async function fetchRagEntityDefaults(
  scope: 'project' | 'agent' | 'kb' = 'project',
): Promise<RagEntityModelDefaults> {
  if (cachedDefaults) return cachedDefaults;
  if (!loadPromise) {
    loadPromise = (async () => {
      try {
        const resp = await authFetch(getApiUrl(`/api/rag/settings?scope=${scope}`), {
          headers: getAuthFetchHeaders(),
        });
        if (!resp.ok) {
          return { embeddingPath: '', rerankerPath: '' };
        }
        const data = (await resp.json()) as Record<string, unknown>;
        const result: RagEntityModelDefaults = {
          embeddingPath: String(data.rag_embedding_model_path || '').trim(),
          rerankerPath: String(data.rag_reranker_model_path || '').trim(),
        };
        cachedDefaults = result;
        return result;
      } catch {
        return { embeddingPath: '', rerankerPath: '' };
      }
    })();
  }
  return loadPromise;
}

export function getCachedRagEntityDefaults(): RagEntityModelDefaults | null {
  return cachedDefaults;
}

export function resolveRagEmbeddingModelPath(
  path: string | null | undefined,
  fallback?: string,
): string {
  const trimmed = String(path ?? '').trim();
  if (trimmed) return trimmed;
  const fb = fallback ?? cachedDefaults?.embeddingPath ?? '';
  return fb.trim();
}

export function resolveRagRerankerModelPath(
  path: string | null | undefined,
  fallback?: string,
): string {
  const trimmed = String(path ?? '').trim();
  if (trimmed) return trimmed;
  const fb = fallback ?? cachedDefaults?.rerankerPath ?? '';
  return fb.trim();
}
