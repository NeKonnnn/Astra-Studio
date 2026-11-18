import { INTENT_PATTERNS } from './intentCatalog';
import { normalizeSpeechText } from './wakeWord';
import type { VoiceIntent } from './types';

const LOCAL_CONFIDENCE = 0.92;

export function matchLocalIntent(rawText: string): VoiceIntent | null {
  const text = normalizeSpeechText(rawText);
  if (!text) return null;

  for (const entry of INTENT_PATTERNS) {
    for (const pattern of entry.patterns) {
      const match = pattern.exec(text);
      if (!match) continue;
      const extracted = entry.extract?.(text, match) || {};
      return {
        id: entry.id,
        params: extracted,
        confidence: LOCAL_CONFIDENCE,
        confirmPhrase: entry.confirmPhrase,
        rawText: text,
      };
    }
  }
  return null;
}
