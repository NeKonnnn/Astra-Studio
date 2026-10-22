import { authFetch, getApiUrl } from '../config/api';

/** Тег из справочника (таблица tags): общий на всех, имя уникально. */
export interface AgentTag {
  id: number;
  name: string;
  color?: string | null;
}

// Минута: после сохранения карточки с новым тегом справочник должен
// подтянуть его без перезагрузки страницы, но и дёргать API на каждое
// открытие поля незачем.
const CACHE_TTL_MS = 60000;

/**
 * Строгий разбор id тега: только положительное целое число (number) либо строка из цифр.
 * `null`, `undefined`, `''`, `true`, NaN, дроби (`0.5`) и нуль отвергаются, а не приводятся к 0.
 */
export function toPositiveIntId(value: unknown): number | null {
  if (typeof value === 'number') {
    return Number.isSafeInteger(value) && value > 0 ? value : null;
  }
  if (typeof value === 'string' && /^\d+$/.test(value.trim())) {
    const n = Number(value.trim());
    return Number.isSafeInteger(n) && n > 0 ? n : null;
  }
  return null;
}

let cached: { at: number; tags: AgentTag[] } | null = null;
let inflight: Promise<AgentTag[]> | null = null;

type TagsCacheListener = () => void;
const listeners = new Set<TagsCacheListener>();

function notifyAgentTagsCacheListeners(): void {
  listeners.forEach((listener) => {
    try {
      listener();
    } catch {
      /* ignore subscriber errors */
    }
  });
}

/** Подписка на сброс кэша (после save агента с новыми тегами). */
export function subscribeAgentTagsCache(listener: TagsCacheListener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function invalidateAgentTagsCache(): void {
  cached = null;
  notifyAgentTagsCacheListeners();
}

/** GET /api/agents/tags - справочник для поля «Теги» и «Обязательные теги». */
export async function fetchAgentTags(force = false): Promise<AgentTag[]> {
  if (!force && cached && Date.now() - cached.at < CACHE_TTL_MS) return cached.tags;
  if (inflight) return inflight;
  inflight = (async () => {
    try {
      const resp = await authFetch(getApiUrl('/api/agents/tags'));
      if (!resp.ok) {
        // Ошибку (в т.ч. 5xx) не кэшируем: пустой справочник не должен
        // «залипать» на минуту. Отдаём прежний кэш, если он был.
        return cached ? cached.tags : [];
      }
      const data = await resp.json();
      const raw: unknown[] = Array.isArray(data?.tags) ? data.tags : [];
      const tags: AgentTag[] = [];
      for (const item of raw) {
        const rec = (item || {}) as Record<string, unknown>;
        const id = toPositiveIntId(rec.id);
        const name = String(rec.name ?? '').trim();
        if (id === null || !name) continue;
        tags.push({ id, name, color: typeof rec.color === 'string' ? rec.color : null });
      }
      cached = { at: Date.now(), tags };
      return tags;
    } catch {
      return cached ? cached.tags : [];
    } finally {
      inflight = null;
    }
  })();
  return inflight;
}

/** id тегов из config.subagents.required_tag_ids: уникальные положительные int. */
export function parseTagIds(raw: unknown): number[] {
  if (!Array.isArray(raw)) return [];
  const out: number[] = [];
  for (const item of raw) {
    const id = toPositiveIntId(item);
    if (id === null) continue;
    if (!out.includes(id)) out.push(id);
  }
  return out;
}
