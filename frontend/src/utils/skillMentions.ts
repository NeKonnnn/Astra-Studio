/** Mentions skills: <$slug|Name> */

export const SKILL_MENTION_RE = /<\$([^|>]+)\|?([^>]*)>/g;

export function extractSkillIds(text: string): string[] {
  if (!text) return [];
  const ids: string[] = [];
  const seen = new Set<string>();
  const re = new RegExp(SKILL_MENTION_RE.source, 'g');
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    const id = (m[1] || '').trim();
    if (id && !seen.has(id)) {
      seen.add(id);
      ids.push(id);
    }
  }
  return ids;
}

export function serializeSkillMention(slug: string, name: string): string {
  const s = (slug || '').trim();
  const n = (name || slug || '').trim();
  return `<$${s}|${n}>`;
}

export function stripSkillMentions(text: string): string {
  return (text || '').replace(/<\$[^>]+>/g, '').trim();
}

/** Split text into plain / mention parts for rendering */
export type SkillTextPart =
  | { type: 'text'; value: string }
  | { type: 'skill'; slug: string; name: string };

export function splitSkillMentions(text: string): SkillTextPart[] {
  if (!text) return [];
  const parts: SkillTextPart[] = [];
  const re = new RegExp(SKILL_MENTION_RE.source, 'g');
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) {
      parts.push({ type: 'text', value: text.slice(last, m.index) });
    }
    parts.push({
      type: 'skill',
      slug: (m[1] || '').trim(),
      name: (m[2] || m[1] || '').trim(),
    });
    last = m.index + m[0].length;
  }
  if (last < text.length) {
    parts.push({ type: 'text', value: text.slice(last) });
  }
  return parts.length ? parts : [{ type: 'text', value: text }];
}

/** Detect `$query` at cursor for autocomplete */
export function getSkillDollarQuery(text: string, cursor: number): { start: number; query: string } | null {
  const before = text.slice(0, cursor);
  const m = before.match(/(^|[\s\n])\$([^\s$]*)$/);
  if (!m) return null;
  const query = m[2] || '';
  const start = before.length - query.length - 1;
  return { start, query };
}
