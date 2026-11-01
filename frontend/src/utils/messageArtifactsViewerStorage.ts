/** Persist: для каких сообщений уже показывали artifact/presentation viewer. */

const STORAGE_KEY = 'astrachat:message_artifacts_viewer_v1';
export const MESSAGE_ARTIFACTS_VIEWER_CHANGED_EVENT = 'astrachatMessageArtifactsViewerChanged';

function readSet(): Set<string> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return new Set();
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return new Set();
    return new Set(parsed.filter((x): x is string => typeof x === 'string' && Boolean(x.trim())));
  } catch {
    return new Set();
  }
}

function writeSet(ids: Set<string>): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(Array.from(ids)));
    window.dispatchEvent(
      new CustomEvent(MESSAGE_ARTIFACTS_VIEWER_CHANGED_EVENT, {
        detail: { messageIds: Array.from(ids) },
      }),
    );
  } catch {
    /* */
  }
}

export function isMessageArtifactsViewerPinned(messageId?: string | null): boolean {
  if (!messageId) return false;
  return readSet().has(messageId);
}

/** Запомнить, что для этого сообщения viewer уже был показан. */
export function pinMessageArtifactsViewer(messageId?: string | null): void {
  if (!messageId) return;
  const set = readSet();
  if (set.has(messageId)) return;
  set.add(messageId);
  writeSet(set);
}

/** Skill, который порождает GPB HTML-презентации (не mermaid/диаграммы). */
export function skillImpliesPresentation(slug: string): boolean {
  const s = (slug || '').trim().toLowerCase();
  if (!s) return false;
  return (
    s.includes('present') ||
    s.includes('html-prese') ||
    s.includes('deck-html') ||
    s.includes('pptx') ||
    s.includes('презента') ||
    s.includes('слайд') ||
    /(^|[-_])slide(s)?($|[-_])/.test(s)
  );
}

/** Skill, который обычно порождает HTML-презентации / визуалы с viewer. */
export function skillImpliesArtifactsViewer(slug: string): boolean {
  const s = (slug || '').trim().toLowerCase();
  if (!s) return false;
  if (skillImpliesPresentation(s)) return true;
  return (
    s.includes('mermaid') ||
    s.includes('artifact') ||
    s.includes('visual') ||
    s.includes('diagram')
  );
}
