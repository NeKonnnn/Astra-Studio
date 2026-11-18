import { authFetch, getApiUrl, API_ENDPOINTS } from '../config/api';
import type { TtsStatusResponse } from './types';
import { vaLog, vaWarn } from './log';

let cached: { at: number; data: TtsStatusResponse } | null = null;

export async function fetchVaTtsStatus(force = false): Promise<TtsStatusResponse> {
  if (!force && cached && Date.now() - cached.at < 15_000) return cached.data;
  try {
    const resp = await authFetch(getApiUrl(API_ENDPOINTS.VOICE_ASSISTANT_TTS_STATUS));
    if (!resp.ok) {
      vaWarn('tts_status_http', { status: resp.status });
      const data: TtsStatusResponse = {
        available: false,
        blocked: true,
        status: 'unreachable',
        engine: 'silero',
        hint: 'Не удалось проверить Silero TTS',
      };
      cached = { at: Date.now(), data };
      return data;
    }
    const data = (await resp.json()) as TtsStatusResponse;
    cached = { at: Date.now(), data };
    vaLog('tts_status', {
      available: data.available,
      blocked: data.blocked,
      status: data.status,
    });
    return data;
  } catch (e) {
    vaWarn('tts_status_error', { error: e instanceof Error ? e.message : String(e) });
    const data: TtsStatusResponse = {
      available: false,
      blocked: true,
      status: 'error',
      engine: 'silero',
      error: e instanceof Error ? e.message : String(e),
      hint: 'Silero недоступен — будет голос браузера',
    };
    cached = { at: Date.now(), data };
    return data;
  }
}
