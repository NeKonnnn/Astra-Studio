/** Per-chat selected knowledge bases — как skills, не глобальный singleton. */

export const KB_SELECTION_CHANGED_EVENT = 'astrachatKnowledgeBaseSelectionChanged';

export type ActiveKnowledgeBaseRef = {
  id: number;
  name?: string;
};

function storageKey(chatId: string): string {
  return `chat:${chatId}:active_knowledge_base_ids`;
}

function normalizeRefs(raw: unknown): ActiveKnowledgeBaseRef[] {
  if (!Array.isArray(raw)) return [];
  const out: ActiveKnowledgeBaseRef[] = [];
  const seen = new Set<number>();
  for (const x of raw) {
    let id = 0;
    let name: string | undefined;
    if (typeof x === 'number' && Number.isFinite(x)) {
      id = Math.trunc(x);
    } else if (typeof x === 'string' && /^\d+$/.test(x.trim())) {
      id = Number(x.trim());
    } else if (x && typeof x === 'object') {
      const obj = x as { id?: unknown; name?: unknown };
      id = Number(obj.id);
      const n = String(obj.name || '').trim();
      if (n) name = n;
    }
    if (!Number.isFinite(id) || id <= 0 || seen.has(id)) continue;
    seen.add(id);
    out.push(name ? { id, name } : { id });
  }
  return out;
}

function readRefs(key: string): ActiveKnowledgeBaseRef[] {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return [];
    return normalizeRefs(JSON.parse(raw));
  } catch {
    return [];
  }
}

function writeRefs(chatId: string, refs: ActiveKnowledgeBaseRef[]): void {
  const normalized = normalizeRefs(refs);
  const payload = normalized.map((r) => (r.name ? { id: r.id, name: r.name } : r.id));
  localStorage.setItem(storageKey(chatId), JSON.stringify(payload));
  window.dispatchEvent(
    new CustomEvent(KB_SELECTION_CHANGED_EVENT, {
      detail: {
        chatId,
        knowledgeBaseIds: normalized.map((r) => r.id),
        knowledgeBases: normalized,
      },
    }),
  );
}

export function getActiveKnowledgeBaseRefs(
  chatId?: string | null,
): ActiveKnowledgeBaseRef[] {
  if (!chatId) return [];
  return readRefs(storageKey(chatId));
}

export function getActiveKnowledgeBaseIds(chatId?: string | null): number[] {
  return getActiveKnowledgeBaseRefs(chatId).map((r) => r.id);
}

export function setActiveKnowledgeBaseRefs(
  chatId: string | null | undefined,
  refs: ActiveKnowledgeBaseRef[],
): void {
  if (!chatId) return;
  writeRefs(chatId, refs);
}

export function clearActiveKnowledgeBases(chatId?: string | null): void {
  if (!chatId) return;
  writeRefs(chatId, []);
}

export function toggleActiveKnowledgeBase(
  chatId: string | null | undefined,
  id: number,
  name?: string,
): ActiveKnowledgeBaseRef[] {
  if (!chatId || !Number.isFinite(id) || id <= 0) return [];
  const current = getActiveKnowledgeBaseRefs(chatId);
  const exists = current.some((r) => r.id === id);
  const next = exists
    ? current.filter((r) => r.id !== id)
    : [...current, name ? { id, name } : { id }];
  writeRefs(chatId, next);
  return next;
}
