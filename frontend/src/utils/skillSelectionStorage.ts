/** Per-chat selected skills (slugs) — как MCP tool_ids, не глобальный singleton. */

export const SKILL_SELECTION_CHANGED_EVENT = 'astrachatSkillSelectionChanged';

/** Legacy global key — больше не читаем как источник правды (утечка между чатами). */
const LEGACY_GLOBAL_KEY = 'active_skill_ids';

export type ActiveSkillRef = {
  slug: string;
  name?: string;
};

function storageKey(chatId: string): string {
  return `chat:${chatId}:active_skill_ids`;
}

function normalizeRefs(raw: unknown): ActiveSkillRef[] {
  if (!Array.isArray(raw)) return [];
  const out: ActiveSkillRef[] = [];
  const seen = new Set<string>();
  for (const x of raw) {
    let slug = '';
    let name: string | undefined;
    if (typeof x === 'string') {
      slug = x.trim();
    } else if (x && typeof x === 'object') {
      const obj = x as { slug?: unknown; id?: unknown; name?: unknown };
      slug = String(obj.slug || obj.id || '').trim();
      const n = String(obj.name || '').trim();
      if (n) name = n;
    }
    if (!slug || seen.has(slug)) continue;
    seen.add(slug);
    out.push(name ? { slug, name } : { slug });
  }
  return out;
}

function readRefs(key: string): ActiveSkillRef[] {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return [];
    return normalizeRefs(JSON.parse(raw));
  } catch {
    return [];
  }
}

function writeRefs(chatId: string, refs: ActiveSkillRef[]): void {
  const normalized = normalizeRefs(refs);
  const payload = normalized.map((r) => (r.name ? { slug: r.slug, name: r.name } : r.slug));
  localStorage.setItem(storageKey(chatId), JSON.stringify(payload));
  // Legacy больше не используем — иначе skills снова «протекут» во все чаты.
  try {
    localStorage.removeItem(LEGACY_GLOBAL_KEY);
  } catch {
    /* ignore */
  }
  window.dispatchEvent(
    new CustomEvent(SKILL_SELECTION_CHANGED_EVENT, {
      detail: {
        chatId,
        skillIds: normalized.map((r) => r.slug),
        skills: normalized,
      },
    }),
  );
}

export function getActiveSkillRefs(chatId?: string | null): ActiveSkillRef[] {
  if (!chatId) return [];
  return readRefs(storageKey(chatId));
}

export function getActiveSkillIds(chatId?: string | null): string[] {
  return getActiveSkillRefs(chatId).map((r) => r.slug);
}

export function setActiveSkillRefs(chatId: string | null | undefined, refs: ActiveSkillRef[]): void {
  if (!chatId) return;
  writeRefs(chatId, refs);
}

export function setActiveSkillIds(chatId: string | null | undefined, ids: string[]): void {
  if (!chatId) return;
  const prev = getActiveSkillRefs(chatId);
  const nameBySlug = new Map(prev.map((r) => [r.slug, r.name]));
  setActiveSkillRefs(
    chatId,
    ids.map((id) => {
      const slug = String(id || '').trim();
      const name = nameBySlug.get(slug);
      return name ? { slug, name } : { slug };
    }),
  );
}

export function isSkillActive(chatId: string | null | undefined, slug: string): boolean {
  const id = (slug || '').trim();
  return Boolean(chatId && id && getActiveSkillIds(chatId).includes(id));
}

export function toggleActiveSkill(
  chatId: string | null | undefined,
  slug: string,
  enabled: boolean,
  name?: string,
): string[] {
  if (!chatId) return [];
  const id = (slug || '').trim();
  if (!id) return getActiveSkillIds(chatId);
  const current = getActiveSkillRefs(chatId);
  let next: ActiveSkillRef[];
  if (enabled) {
    if (current.some((r) => r.slug === id)) {
      next = current.map((r) =>
        r.slug === id && name ? { slug: id, name: name.trim() || r.name } : r,
      );
    } else {
      const trimmed = (name || '').trim();
      next = [...current, trimmed ? { slug: id, name: trimmed } : { slug: id }];
    }
  } else {
    next = current.filter((r) => r.slug !== id);
  }
  setActiveSkillRefs(chatId, next);
  return next.map((r) => r.slug);
}

export function clearActiveSkills(chatId?: string | null): void {
  if (!chatId) return;
  setActiveSkillRefs(chatId, []);
}

/** Переименовать slug во всех чатах, где skill был выбран (после rename в редакторе). */
export function renameSkillSlugInAllChats(oldSlug: string, newSlug: string, name?: string): void {
  const from = (oldSlug || '').trim();
  const to = (newSlug || '').trim();
  if (!from || !to || from === to) return;
  const prefix = 'chat:';
  const suffix = ':active_skill_ids';
  for (let i = 0; i < localStorage.length; i += 1) {
    const key = localStorage.key(i);
    if (!key || !key.startsWith(prefix) || !key.endsWith(suffix)) continue;
    const chatId = key.slice(prefix.length, key.length - suffix.length);
    if (!chatId) continue;
    const ids = getActiveSkillIds(chatId);
    if (!ids.includes(from)) continue;
    setActiveSkillIds(
      chatId,
      ids.map((id) => (id === from ? to : id)),
    );
    if (name) toggleActiveSkill(chatId, to, true, name);
  }
}

/** Убрать удалённый skill из активных во всех чатах (чтобы погасла синяя иконка в поле ввода). */
export function removeSkillSlugFromAllChats(slug: string): void {
  const id = (slug || '').trim();
  if (!id) return;
  const prefix = 'chat:';
  const suffix = ':active_skill_ids';
  const keys: string[] = [];
  for (let i = 0; i < localStorage.length; i += 1) {
    const key = localStorage.key(i);
    if (key && key.startsWith(prefix) && key.endsWith(suffix)) keys.push(key);
  }
  for (const key of keys) {
    const chatId = key.slice(prefix.length, key.length - suffix.length);
    if (!chatId) continue;
    const refs = getActiveSkillRefs(chatId);
    if (!refs.some((r) => r.slug === id)) continue;
    setActiveSkillRefs(
      chatId,
      refs.filter((r) => r.slug !== id),
    );
  }
}

export function copyActiveSkills(fromChatId: string, toChatId: string): void {
  if (!fromChatId || !toChatId || fromChatId === toChatId) return;
  const refs = getActiveSkillRefs(fromChatId);
  if (refs.length) setActiveSkillRefs(toChatId, refs);
}
