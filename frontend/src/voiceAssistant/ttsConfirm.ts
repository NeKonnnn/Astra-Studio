import { loadVoiceAssistantSettings } from './settings';
import {
  bargeInStopAll,
  enqueueSpeak,
  flushTtsQueue,
  isLiveTtsSpeaking,
  resetTtsAbortFlag,
  unlockAudioPlayback,
} from './ttsQueue';
import { vaLog } from './log';

export function isVoiceAssistantSpeaking(): boolean {
  return isLiveTtsSpeaking();
}

export function stopVoiceAssistantSpeech(): void {
  bargeInStopAll();
}

/** Короткая фраза подтверждения / ответ (не streaming). */
export async function speakConfirmation(
  text: string,
  opts?: { voice_id?: string; voice_speaker?: string; speech_rate?: number; sileroAvailable?: boolean },
): Promise<void> {
  const phrase = String(text || '').trim();
  if (!phrase) return;
  const settings = loadVoiceAssistantSettings();
  await unlockAudioPlayback();
  resetTtsAbortFlag();
  bargeInStopAll();
  resetTtsAbortFlag();
  vaLog('tts_confirm', { text: phrase.slice(0, 160) });
  await enqueueSpeak(phrase, {
    voice_id: opts?.voice_id || settings.voiceId,
    voice_speaker: opts?.voice_speaker || settings.voiceSpeaker,
    speech_rate: opts?.speech_rate ?? settings.speechRate,
    engine: settings.ttsEngine,
    sileroAvailable: opts?.sileroAvailable,
  });
  await flushTtsQueue();
}
