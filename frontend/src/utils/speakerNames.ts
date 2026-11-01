/**
 * Спикеры в результате транскрибации: поиск и подстановка имён (BUG-FE-65).
 *
 * Токен спикера ищется целиком (границы по буквам/цифрам/`_`), поэтому:
 * - `SPEAKER_1` не портит `SPEAKER_10`;
 * - `SPEAKER_SPEAKER_01` — один спикер, а не три (`SPEAKER_SPEAKER_01`, `SPEAKER_01`, `SPEAKER_SPEAKER`).
 */

const WORD_CHAR = '\\p{L}\\p{N}_';

// Порядок альтернатив важен: сначала самые длинные шаблоны.
const SPEAKER_TOKEN_SOURCE = 'SPEAKER_SPEAKER_\\d+|SPEAKER_\\d+|Speaker_[A-Z0-9]+|Спикер_\\d+';

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/** Уникальные спикеры из текста (отсортированы). */
export function extractSpeakers(text: string): string[] {
  if (!text) return [];
  const regex = new RegExp(`(?:^|[^${WORD_CHAR}])(${SPEAKER_TOKEN_SOURCE})(?![${WORD_CHAR}])`, 'giu');
  const found = new Set<string>();
  let match: RegExpExecArray | null;
  while ((match = regex.exec(text)) !== null) {
    found.add(match[1]);
  }
  return Array.from(found).sort();
}

/** Подставляет имена вместо токенов спикеров целиком; пустые имена пропускаются. */
export function applySpeakerMapping(text: string, mapping: Record<string, string>): string {
  const entries = Object.entries(mapping).filter(([original, newName]) => original && newName.trim());
  if (entries.length === 0) return text;

  const replacements = new Map(entries);
  const alternatives = entries
    .map(([original]) => original)
    .sort((a, b) => b.length - a.length)
    .map(escapeRegExp)
    .join('|');
  const regex = new RegExp(`(^|[^${WORD_CHAR}])(${alternatives})(?![${WORD_CHAR}])`, 'gu');

  return text.replace(regex, (full: string, prefix: string, token: string) => {
    const replacement = replacements.get(token);
    return replacement === undefined ? full : `${prefix}${replacement}`;
  });
}
