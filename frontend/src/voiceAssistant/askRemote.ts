import { authFetch, getApiUrl, API_ENDPOINTS } from '../config/api';
import { vaLog, vaWarn } from './log';

/** Короткий голосовой ответ LLM на свободный вопрос (не команда UI). */
export async function askVoiceAssistant(text: string): Promise<string | null> {
  const trimmed = String(text || '').trim();
  if (!trimmed) return null;
  vaLog('ask_remote', { text: trimmed });
  try {
    const resp = await authFetch(getApiUrl(API_ENDPOINTS.VOICE_ASSISTANT_ASK), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: trimmed }),
    });
    if (!resp.ok) {
      vaWarn('ask_remote_fail', { status: resp.status });
      return null;
    }
    const data = (await resp.json()) as { answer?: string };
    const answer = String(data.answer || '').trim();
    vaLog('ask_remote_ok', { answer: answer.slice(0, 200) });
    return answer || null;
  } catch (e) {
    vaWarn('ask_remote_error', { error: e instanceof Error ? e.message : String(e) });
    return null;
  }
}
