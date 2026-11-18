import { authFetch, getApiUrl, API_ENDPOINTS } from '../config/api';
import type { VoiceIntent } from './types';

export async function parseIntentRemote(text: string): Promise<VoiceIntent | null> {
  const trimmed = String(text || '').trim();
  if (!trimmed) return null;
  try {
    const resp = await authFetch(getApiUrl(API_ENDPOINTS.VOICE_ASSISTANT_PARSE), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: trimmed }),
    });
    if (!resp.ok) return null;
    const data = (await resp.json()) as {
      intent?: string;
      params?: Record<string, unknown>;
      confidence?: number;
      confirm_phrase?: string;
    };
    const id = String(data.intent || 'unknown');
    if (!id || id === 'unknown') return null;
    return {
      id: id as VoiceIntent['id'],
      params: (data.params || {}) as VoiceIntent['params'],
      confidence: typeof data.confidence === 'number' ? data.confidence : 0.6,
      confirmPhrase: data.confirm_phrase,
      rawText: trimmed,
    };
  } catch {
    return null;
  }
}
