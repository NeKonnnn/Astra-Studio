/** Нормализация русской речи для сравнения wake-word и команд. */
export function normalizeSpeechText(input: string): string {
  return String(input || '')
    .toLowerCase()
    .replace(/ё/g, 'е')
    .replace(/[^\p{L}\p{N}\s]+/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

const WAKE_ALIASES: Record<string, string[]> = {
  астра: ['астра', 'astra', 'астрачат', 'astra chat'],
};

function wakeVariants(wakeWord: string): string[] {
  const base = normalizeSpeechText(wakeWord);
  if (!base) return ['астра'];
  const extras = WAKE_ALIASES[base] || [];
  return Array.from(new Set([base, ...extras.map(normalizeSpeechText)]));
}

export type WakeWordHit = {
  matched: boolean;
  /** Текст после wake-word в той же фразе (если команда сразу следом). */
  remainder: string;
  matchedVariant?: string;
};

/** Ищет wake-word в тексте; возвращает остаток команды, если он в той же фразе. */
export function detectWakeWord(text: string, wakeWord: string): WakeWordHit {
  const normalized = normalizeSpeechText(text);
  if (!normalized) return { matched: false, remainder: '' };

  for (const variant of wakeVariants(wakeWord)) {
    if (!variant) continue;
    if (normalized === variant) {
      return { matched: true, remainder: '', matchedVariant: variant };
    }
    const idx = normalized.indexOf(variant);
    if (idx < 0) continue;
    // Wake-word как отдельное слово / начало фразы
    const before = idx === 0 ? '' : normalized[idx - 1];
    const afterIdx = idx + variant.length;
    const after = afterIdx >= normalized.length ? '' : normalized[afterIdx];
    if (before && before !== ' ') continue;
    if (after && after !== ' ') continue;
    const remainder = normalized.slice(afterIdx).trim();
    return { matched: true, remainder, matchedVariant: variant };
  }
  return { matched: false, remainder: '' };
}
