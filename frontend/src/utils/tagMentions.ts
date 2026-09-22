/** Mentions агентов по тегам: <#tagId|Name> — зеркало <$slug|Name> для skills */

export const TAG_MENTION_RE = /<#([^|>]+)\|?([^>]*)>/g;

export function extractTagIds(text: string): number[] {
  if (!text) return [];
  const ids: number[] = [];
  const seen = new Set<number>();
  const re = new RegExp(TAG_MENTION_RE.source, 'g');
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    const n = Number((m[1] || '').trim());
    if (!Number.isFinite(n) || n <= 0) continue;
    const id = Math.trunc(n);
    if (seen.has(id)) continue;
    seen.add(id);
    ids.push(id);
  }
  return ids;
}

export function serializeTagMention(id: number, name: string): string {
  const n = Math.trunc(Number(id));
  const label = (name || String(n) || '').trim();
  return `<#${n}|${label}>`;
}

export function stripTagMentions(text: string): string {
  return (text || '').replace(/<#[^>]+>/g, '').trim();
}

/** Detect `#query` at cursor for autocomplete (как `$` для skills). */
export function getTagHashQuery(
  text: string,
  cursor: number,
): { start: number; query: string } | null {
  const before = text.slice(0, cursor);
  const m = before.match(/(^|[\s\n])#([^\s#]*)$/);
  if (!m) return null;
  const query = m[2] || '';
  const start = before.length - query.length - 1;
  return { start, query };
}
