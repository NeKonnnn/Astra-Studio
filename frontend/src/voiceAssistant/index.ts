export { default as VoiceAssistantController } from './VoiceAssistantController';
export { default as VoiceAssistantStatusChip } from './VoiceAssistantStatusChip';
export {
  loadVoiceAssistantSettings,
  saveVoiceAssistantSettings,
  updateVoiceAssistantSettings,
  DEFAULT_VOICE_ASSISTANT_SETTINGS,
} from './settings';
export { matchLocalIntent } from './intentMatcher';
export { executeVoiceIntent } from './intentExecutor';
export { isWebSpeechAvailable } from './speechRecognition';
export { vaLog, vaWarn } from './log';
export { fetchVaTtsStatus } from './ttsStatus';
export { startLiveAsk } from './liveAsk';
export {
  loadVaHistory,
  appendVaHistory,
  clearVaHistory,
  VA_HISTORY_CHANGED,
} from './conversationHistory';
export type { VaHistoryEntry, VaHistoryRole } from './conversationHistory';
export type {
  VoiceAssistantSettings,
  VoiceAssistantStatus,
  VoiceAssistantPhase,
  VoiceIntent,
  VoiceIntentId,
  TtsStatusResponse,
  VoiceAssistantTtsEngine,
} from './types';
export {
  VOICE_ASSISTANT_SETTINGS_CHANGED,
  VOICE_ASSISTANT_STATUS_CHANGED,
  SILERO_VOICE_OPTIONS,
} from './types';
