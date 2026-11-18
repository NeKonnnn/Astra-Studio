export type VoiceAssistantPhase =
  | 'idle'
  | 'listening_wake'
  | 'capturing_command'
  | 'executing'
  | 'speaking';

export type VoiceGearPanel =
  | 'main'
  | 'agents'
  | 'artifacts'
  | 'skills'
  | 'knowledge-bases'
  | 'mcp'
  | 'coding'
  | 'generation'
  | 'model-mode';

export type VoiceSettingsSection =
  | 'general'
  | 'profile'
  | 'interface'
  | 'chats'
  | 'models'
  | 'rag'
  | 'transcription'
  | 'voiceAssistant'
  | 'about';

export type VoiceIntentId =
  | 'newChat'
  | 'searchChats'
  | 'deleteChat'
  | 'openSettings'
  | 'openTools'
  | 'openToolsPanel'
  | 'openAgentConstructor'
  | 'openTranscription'
  | 'openSkills'
  | 'openKnowledgeBases'
  | 'openProject'
  | 'createProject'
  | 'sendMessage'
  | 'stopGeneration'
  | 'toggleMcp'
  | 'selectAgent'
  | 'selectSkill'
  | 'selectKnowledgeBase'
  | 'clearAgent'
  | 'navigate'
  | 'readLastReply'
  | 'disableAssistant'
  | 'unknown';

export type VoiceIntentParams = {
  text?: string;
  server?: string;
  enabled?: boolean;
  agentName?: string;
  skillName?: string;
  knowledgeBaseName?: string;
  projectName?: string;
  path?: string;
  label?: string;
  panel?: VoiceGearPanel;
  section?: VoiceSettingsSection;
};

export type VoiceIntent = {
  id: VoiceIntentId;
  params?: VoiceIntentParams;
  confidence: number;
  confirmPhrase?: string;
  rawText?: string;
};

export type VoiceAssistantTtsEngine = 'silero' | 'browser' | 'auto' | 'omnivoice';

export type VoiceAssistantSettings = {
  enabled: boolean;
  wakeWord: string;
  confirmTts: boolean;
  language: string;
  /** Silero speaker: baya / xenia / kseniya / aidar / eugene */
  voiceSpeaker: string;
  voiceId: string;
  speechRate: number;
  /** auto: browser-first; omnivoice — клон своего голоса */
  ttsEngine: VoiceAssistantTtsEngine;
};

export type VoiceAssistantStatus = {
  phase: VoiceAssistantPhase;
  lastHeard?: string;
  lastCommand?: string;
  lastAnswer?: string;
  lastError?: string;
  engine: 'webspeech' | 'whisper' | 'none';
  ttsEngine?: 'silero' | 'browser' | 'omnivoice' | 'none';
  sileroBlocked?: boolean;
};

export type TtsStatusResponse = {
  available: boolean;
  blocked: boolean;
  status: string;
  engine: string;
  voices?: Record<string, string[]>;
  error?: string | null;
  hint?: string | null;
  omnivoice?: {
    available?: boolean;
    ready?: boolean;
    status?: string;
    error?: string | null;
    hint?: string | null;
    license_note?: string | null;
  };
};

export const VOICE_ASSISTANT_SETTINGS_CHANGED = 'astrachat:voice-assistant-settings-changed';
export const VOICE_ASSISTANT_STATUS_CHANGED = 'astrachat:voice-assistant-status-changed';

export const SILERO_VOICE_OPTIONS: Array<{ id: string; label: string }> = [
  { id: 'baya', label: 'Baya (ж)' },
  { id: 'xenia', label: 'Xenia (ж)' },
  { id: 'kseniya', label: 'Kseniya (ж)' },
  { id: 'aidar', label: 'Aidar (м)' },
  { id: 'eugene', label: 'Eugene (м)' },
];
