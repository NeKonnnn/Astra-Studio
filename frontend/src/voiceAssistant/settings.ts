import {
  VOICE_ASSISTANT_SETTINGS_CHANGED,
  type VoiceAssistantSettings,
  type VoiceAssistantTtsEngine,
} from './types';

const STORAGE_KEY = 'astra_voice_assistant_settings';

export const DEFAULT_VOICE_ASSISTANT_SETTINGS: VoiceAssistantSettings = {
  enabled: false,
  wakeWord: 'астра',
  confirmTts: true,
  language: 'ru-RU',
  voiceSpeaker: 'baya',
  voiceId: 'ru',
  speechRate: 1.05,
  /** auto = browser-first (слышно сразу); silero — по явному выбору */
  ttsEngine: 'auto',
};

function normalizeTtsEngine(v: unknown): VoiceAssistantTtsEngine {
  if (v === 'silero' || v === 'browser' || v === 'auto' || v === 'omnivoice') return v;
  return 'auto';
}

export function loadVoiceAssistantSettings(): VoiceAssistantSettings {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    // Совместимость с голосом из VoiceChatDialog
    const legacySpeaker = localStorage.getItem('voice_speaker') || undefined;
    const legacyId = localStorage.getItem('voice_id') || undefined;
    const legacyRate = localStorage.getItem('speech_rate');
    if (!raw) {
      return {
        ...DEFAULT_VOICE_ASSISTANT_SETTINGS,
        voiceSpeaker: legacySpeaker || DEFAULT_VOICE_ASSISTANT_SETTINGS.voiceSpeaker,
        voiceId: legacyId || DEFAULT_VOICE_ASSISTANT_SETTINGS.voiceId,
        speechRate: legacyRate ? Number(legacyRate) || 1.05 : 1.05,
      };
    }
    const parsed = JSON.parse(raw) as Partial<VoiceAssistantSettings>;
    return {
      enabled: Boolean(parsed.enabled),
      wakeWord:
        String(parsed.wakeWord || DEFAULT_VOICE_ASSISTANT_SETTINGS.wakeWord).trim() || 'астра',
      confirmTts: parsed.confirmTts !== false,
      language: String(parsed.language || DEFAULT_VOICE_ASSISTANT_SETTINGS.language),
      voiceSpeaker: String(
        parsed.voiceSpeaker || legacySpeaker || DEFAULT_VOICE_ASSISTANT_SETTINGS.voiceSpeaker,
      ),
      voiceId: String(parsed.voiceId || legacyId || DEFAULT_VOICE_ASSISTANT_SETTINGS.voiceId),
      speechRate:
        typeof parsed.speechRate === 'number' && Number.isFinite(parsed.speechRate)
          ? parsed.speechRate
          : legacyRate
            ? Number(legacyRate) || 1.05
            : 1.05,
      ttsEngine: normalizeTtsEngine(parsed.ttsEngine),
    };
  } catch {
    return { ...DEFAULT_VOICE_ASSISTANT_SETTINGS };
  }
}

export function saveVoiceAssistantSettings(next: VoiceAssistantSettings): void {
  const normalized: VoiceAssistantSettings = {
    enabled: Boolean(next.enabled),
    wakeWord: String(next.wakeWord || 'астра').trim() || 'астра',
    confirmTts: next.confirmTts !== false,
    language: String(next.language || 'ru-RU'),
    voiceSpeaker: String(next.voiceSpeaker || 'baya'),
    voiceId: String(next.voiceId || 'ru'),
    speechRate:
      typeof next.speechRate === 'number' && Number.isFinite(next.speechRate)
        ? Math.min(2, Math.max(0.5, next.speechRate))
        : 1.05,
    ttsEngine: normalizeTtsEngine(next.ttsEngine),
  };
  localStorage.setItem(STORAGE_KEY, JSON.stringify(normalized));
  // Синхронизация с остальным приложением (VoiceChatDialog / UnifiedChatPage)
  localStorage.setItem('voice_speaker', normalized.voiceSpeaker);
  localStorage.setItem('voice_id', normalized.voiceId);
  localStorage.setItem('speech_rate', String(normalized.speechRate));
  window.dispatchEvent(
    new CustomEvent(VOICE_ASSISTANT_SETTINGS_CHANGED, { detail: normalized }),
  );
}

export function updateVoiceAssistantSettings(
  patch: Partial<VoiceAssistantSettings>,
): VoiceAssistantSettings {
  const next = { ...loadVoiceAssistantSettings(), ...patch };
  saveVoiceAssistantSettings(next);
  return next;
}
