/** История диалога с голосовым помощником (localStorage). */

export type VaHistoryRole = 'user' | 'assistant' | 'system';

export type VaHistoryEntry = {
  id: string;
  role: VaHistoryRole;
  text: string;
  at: number;
  kind?: 'command' | 'question' | 'confirm' | 'answer' | 'info' | 'error';
};

const STORAGE_KEY = 'astra_voice_assistant_history_v1';
const MAX_ENTRIES = 80;

export const VA_HISTORY_CHANGED = 'astrachat:voice-assistant-history-changed';

export function loadVaHistory(): VaHistoryEntry[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (x) => x && typeof x.text === 'string' && typeof x.role === 'string',
    ) as VaHistoryEntry[];
  } catch {
    return [];
  }
}

function saveVaHistory(entries: VaHistoryEntry[]): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(entries.slice(-MAX_ENTRIES)));
  window.dispatchEvent(new CustomEvent(VA_HISTORY_CHANGED, { detail: entries }));
}

export function appendVaHistory(
  role: VaHistoryRole,
  text: string,
  kind?: VaHistoryEntry['kind'],
): VaHistoryEntry | null {
  const trimmed = String(text || '').trim();
  if (!trimmed) return null;
  const entry: VaHistoryEntry = {
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    role,
    text: trimmed.slice(0, 2000),
    at: Date.now(),
    kind,
  };
  const next = [...loadVaHistory(), entry];
  saveVaHistory(next);
  return entry;
}

export function clearVaHistory(): void {
  saveVaHistory([]);
}
