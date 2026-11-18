import type { NavigateFunction } from 'react-router-dom';
import {
  ASTRA_FOCUS_CHAT_SEARCH,
  ASTRA_OPEN_AGENT_CONSTRUCTOR,
  ASTRA_OPEN_CHAT_GEAR,
  ASTRA_OPEN_CREATE_PROJECT,
  ASTRA_OPEN_KNOWLEDGE_BASES_SIDEBAR,
  ASTRA_OPEN_SETTINGS,
  ASTRA_OPEN_SETTINGS_SECTION,
  ASTRA_OPEN_SKILLS_SIDEBAR,
  ASTRA_OPEN_TRANSCRIPTION_SIDEBAR,
  ASTRA_REQUEST_DELETE_CURRENT_CHAT,
} from '../constants/hotkeys';
import { authFetch, getApiUrl, API_ENDPOINTS } from '../config/api';
import { fetchMcpServers } from '../mcp/api';
import { toggleMcpServerForChat } from '../mcp/selectionStorage';
import { clearActiveAgent } from '../utils/clearActiveAgent';
import { persistAgentMcpConfig } from '../utils/applyAgentMcp';
import { fetchMergedUserAgents } from '../utils/fetchMergedUserAgents';
import { prepareNewChatSettings } from '../utils/newChatSettings';
import { chatIsListedInAllChatsSection } from '../contexts/AppContext';
import type { AppState } from '../contexts/AppContext';
import { toggleActiveSkill } from '../utils/skillSelectionStorage';
import {
  getActiveKnowledgeBaseIds,
  toggleActiveKnowledgeBase,
} from '../utils/knowledgeBaseSelectionStorage';
import { normalizeSpeechText } from './wakeWord';
import { updateVoiceAssistantSettings } from './settings';
import { INTENT_CONFIRM } from './intentCatalog';
import { speakConfirmation } from './ttsConfirm';
import type { VoiceGearPanel, VoiceIntent, VoiceSettingsSection } from './types';

export type IntentExecutorDeps = {
  navigate: NavigateFunction;
  state: AppState;
  createChat: (title?: string) => string;
  setCurrentChat: (id: string | null) => void;
  deleteChat: (id: string) => void;
  sendMessage: (message: string, chatId: string, streaming?: boolean) => void;
  stopGeneration: () => void;
  showNotification: (type: 'success' | 'error' | 'info' | 'warning', message: string) => void;
  token?: string | null;
  confirmTts: boolean;
};

function fuzzyIncludes(haystack: string, needle: string): boolean {
  const h = normalizeSpeechText(haystack);
  const n = normalizeSpeechText(needle);
  if (!n) return false;
  return h.includes(n) || n.includes(h);
}

function openChatGear(panel: VoiceGearPanel = 'main'): void {
  window.dispatchEvent(
    new CustomEvent(ASTRA_OPEN_CHAT_GEAR, { detail: { panel } }),
  );
}

async function resolveMcpServerId(serverQuery: string): Promise<string | null> {
  const q = normalizeSpeechText(serverQuery);
  if (!q) return null;
  try {
    const servers = await fetchMcpServers();
    const exact = servers.find(
      (s) =>
        normalizeSpeechText(s.id) === q ||
        normalizeSpeechText(s.display_name || '') === q,
    );
    if (exact) return exact.id;
    const fuzzy = servers.find(
      (s) => fuzzyIncludes(s.id, q) || fuzzyIncludes(s.display_name || '', q),
    );
    return fuzzy?.id || null;
  } catch {
    return null;
  }
}

async function selectAgentByName(
  agentName: string,
  token: string | null | undefined,
  chatId: string | null,
): Promise<string | null> {
  const q = normalizeSpeechText(agentName);
  if (!q) return null;
  const agents = await fetchMergedUserAgents(token, 100);
  const hit =
    agents.find((a) => normalizeSpeechText(a.name) === q) ||
    agents.find((a) => fuzzyIncludes(a.name, q));
  if (!hit) return null;
  localStorage.setItem('active_agent_id', String(hit.id));
  localStorage.setItem('active_agent_name', hit.name);
  localStorage.setItem(
    'active_agent_prompt',
    hit.system_prompt || 'Системные инструкции не заданы.',
  );
  persistAgentMcpConfig(hit.config || null);
  window.dispatchEvent(new CustomEvent('agentSelected', { detail: hit }));
  if (chatId && hit.config) {
    const { applyAgentMcpToChat } = await import('../utils/applyAgentMcp');
    applyAgentMcpToChat(chatId, hit.config);
  }
  return hit.name;
}

type SkillRow = {
  id: number;
  slug: string;
  name: string;
  display_title?: string | null;
};

async function resolveSkillByName(
  skillName: string,
  token: string | null | undefined,
): Promise<SkillRow | null> {
  const q = normalizeSpeechText(skillName);
  if (!q) return null;
  const headers: HeadersInit = token ? { Authorization: `Bearer ${token}` } : {};
  try {
    const [createdResp, sharedResp] = await Promise.all([
      authFetch(`${getApiUrl(API_ENDPOINTS.SKILLS)}/list?limit=100&view_option=created`, {
        headers,
      }),
      authFetch(`${getApiUrl(API_ENDPOINTS.SKILLS)}/list?limit=100&view_option=shared`, {
        headers,
      }),
    ]);
    const rows: SkillRow[] = [];
    if (createdResp.ok) {
      const data = await createdResp.json();
      rows.push(...((data.items || []) as SkillRow[]));
    }
    if (sharedResp.ok) {
      const data = await sharedResp.json();
      rows.push(...((data.items || []) as SkillRow[]));
    }
    const label = (s: SkillRow) => s.display_title || s.name || s.slug;
    return (
      rows.find((s) => normalizeSpeechText(label(s)) === q) ||
      rows.find((s) => normalizeSpeechText(s.slug) === q) ||
      rows.find((s) => fuzzyIncludes(label(s), q) || fuzzyIncludes(s.slug, q)) ||
      null
    );
  } catch {
    return null;
  }
}

type KbRow = { id: number; name: string };

async function resolveKnowledgeBaseByName(
  name: string,
  token: string | null | undefined,
): Promise<KbRow | null> {
  const q = normalizeSpeechText(name);
  if (!q) return null;
  const headers: HeadersInit = token ? { Authorization: `Bearer ${token}` } : {};
  try {
    const resp = await authFetch(
      `${getApiUrl(API_ENDPOINTS.KNOWLEDGE_BASES)}/list?limit=100&view_option=created`,
      { headers },
    );
    if (!resp.ok) return null;
    const data = await resp.json();
    const rows = (data.items || []) as KbRow[];
    return (
      rows.find((k) => normalizeSpeechText(k.name) === q) ||
      rows.find((k) => fuzzyIncludes(k.name, q)) ||
      null
    );
  } catch {
    return null;
  }
}

function lastAssistantText(state: AppState): string {
  const chat = state.chats.find((c) => c.id === state.currentChatId);
  if (!chat?.messages?.length) return '';
  for (let i = chat.messages.length - 1; i >= 0; i -= 1) {
    const m = chat.messages[i];
    if (m.role === 'assistant' && m.content?.trim()) return m.content.trim();
  }
  return '';
}

const SETTINGS_SECTION_LABELS: Record<VoiceSettingsSection, string> = {
  general: 'Общее',
  profile: 'Профиль',
  interface: 'Интерфейс',
  chats: 'Чаты',
  models: 'Модели',
  rag: 'RAG',
  transcription: 'Транскрибация',
  voiceAssistant: 'Голосовой помощник',
  about: 'О приложении',
};

const GEAR_PANEL_LABELS: Record<VoiceGearPanel, string> = {
  main: 'инструменты',
  agents: 'Агенты',
  artifacts: 'Артефакты',
  skills: 'Skills',
  'knowledge-bases': 'Базы знаний',
  mcp: 'MCP',
  coding: 'Coding',
  generation: 'Режим генерации',
  'model-mode': 'Режим модели',
};

export async function executeVoiceIntent(
  intent: VoiceIntent,
  deps: IntentExecutorDeps,
): Promise<{ ok: boolean; confirmPhrase: string; error?: string }> {
  let confirm =
    intent.confirmPhrase ||
    INTENT_CONFIRM[intent.id] ||
    'Готово';

  try {
    switch (intent.id) {
      case 'newChat': {
        const s = deps.state;
        const cur = s.chats.find((c) => c.id === s.currentChatId) ?? null;
        if (
          cur &&
          !chatIsListedInAllChatsSection(cur) &&
          !s.loadingChatIds.includes(cur.id) &&
          !cur.messages.some((m) => m.isStreaming)
        ) {
          deps.deleteChat(cur.id);
        }
        const id = deps.createChat();
        prepareNewChatSettings(cur?.id, id);
        deps.setCurrentChat(id);
        deps.navigate('/');
        break;
      }
      case 'searchChats':
        window.dispatchEvent(new CustomEvent(ASTRA_FOCUS_CHAT_SEARCH));
        break;
      case 'deleteChat':
        window.dispatchEvent(new CustomEvent(ASTRA_REQUEST_DELETE_CURRENT_CHAT));
        break;
      case 'openSettings': {
        const section = intent.params?.section as VoiceSettingsSection | undefined;
        if (section) {
          window.dispatchEvent(
            new CustomEvent(ASTRA_OPEN_SETTINGS_SECTION, { detail: { section } }),
          );
          confirm = `Открываю настройки: ${SETTINGS_SECTION_LABELS[section] || section}`;
        } else {
          window.dispatchEvent(new CustomEvent(ASTRA_OPEN_SETTINGS));
        }
        break;
      }
      case 'openTools':
        openChatGear('main');
        break;
      case 'openToolsPanel': {
        const panel = (intent.params?.panel as VoiceGearPanel) || 'main';
        openChatGear(panel);
        confirm = `Открываю ${GEAR_PANEL_LABELS[panel] || panel}`;
        break;
      }
      case 'openProject': {
        const name = String(intent.params?.projectName || '').trim();
        const q = normalizeSpeechText(name);
        if (!q) {
          return { ok: false, confirmPhrase: 'Не назван проект', error: 'empty' };
        }
        const projects = deps.state.projects || [];
        const hit =
          projects.find((p) => normalizeSpeechText(p.name) === q) ||
          projects.find((p) => fuzzyIncludes(p.name, q));
        if (!hit) {
          return { ok: false, confirmPhrase: 'Проект не найден', error: 'project_not_found' };
        }
        deps.navigate(`/project/${hit.id}`);
        confirm = `Открываю проект «${hit.name}»`;
        break;
      }
      case 'createProject':
        window.dispatchEvent(new CustomEvent(ASTRA_OPEN_CREATE_PROJECT));
        break;
      case 'openAgentConstructor':
        window.dispatchEvent(new CustomEvent(ASTRA_OPEN_AGENT_CONSTRUCTOR));
        break;
      case 'openTranscription':
        window.dispatchEvent(new CustomEvent(ASTRA_OPEN_TRANSCRIPTION_SIDEBAR));
        break;
      case 'openSkills':
        window.dispatchEvent(new CustomEvent(ASTRA_OPEN_SKILLS_SIDEBAR));
        break;
      case 'openKnowledgeBases':
        window.dispatchEvent(new CustomEvent(ASTRA_OPEN_KNOWLEDGE_BASES_SIDEBAR));
        break;
      case 'stopGeneration':
        deps.stopGeneration();
        break;
      case 'sendMessage': {
        const text = String(intent.params?.text || '').trim();
        if (!text) return { ok: false, confirmPhrase: 'Нечего отправлять', error: 'empty' };
        let chatId = deps.state.currentChatId;
        if (!chatId) {
          chatId = deps.createChat();
          deps.setCurrentChat(chatId);
          deps.navigate('/');
        }
        deps.sendMessage(text, chatId, true);
        break;
      }
      case 'toggleMcp': {
        const chatId = deps.state.currentChatId;
        if (!chatId) return { ok: false, confirmPhrase: 'Нет активного чата', error: 'no_chat' };
        const serverQuery = String(intent.params?.server || '').trim();
        if (!serverQuery) {
          return { ok: false, confirmPhrase: 'Не указан MCP', error: 'empty' };
        }
        const enabled = Boolean(intent.params?.enabled);
        const serverId = await resolveMcpServerId(serverQuery);
        if (!serverId) {
          return {
            ok: false,
            confirmPhrase: 'MCP сервер не найден',
            error: 'mcp_not_found',
          };
        }
        toggleMcpServerForChat(chatId, serverId, enabled);
        confirm = enabled ? `MCP «${serverId}» включён` : `MCP «${serverId}» выключен`;
        deps.showNotification('success', confirm);
        break;
      }
      case 'selectAgent': {
        const name = String(intent.params?.agentName || '').trim();
        const selected = await selectAgentByName(name, deps.token, deps.state.currentChatId);
        if (!selected) {
          return { ok: false, confirmPhrase: 'Агент не найден', error: 'agent_not_found' };
        }
        confirm = `Агент «${selected}»`;
        deps.showNotification('success', confirm);
        break;
      }
      case 'selectSkill': {
        const chatId = deps.state.currentChatId;
        if (!chatId) return { ok: false, confirmPhrase: 'Нет активного чата', error: 'no_chat' };
        const skillName = String(intent.params?.skillName || '').trim();
        const skill = await resolveSkillByName(skillName, deps.token);
        if (!skill) {
          return { ok: false, confirmPhrase: 'Навык не найден', error: 'skill_not_found' };
        }
        const label = skill.display_title || skill.name || skill.slug;
        toggleActiveSkill(chatId, skill.slug, true, label);
        confirm = `Навык «${label}»`;
        deps.showNotification('success', confirm);
        break;
      }
      case 'selectKnowledgeBase': {
        const chatId = deps.state.currentChatId;
        if (!chatId) return { ok: false, confirmPhrase: 'Нет активного чата', error: 'no_chat' };
        const kbName = String(intent.params?.knowledgeBaseName || '').trim();
        const kb = await resolveKnowledgeBaseByName(kbName, deps.token);
        if (!kb) {
          return { ok: false, confirmPhrase: 'База знаний не найдена', error: 'kb_not_found' };
        }
        if (!getActiveKnowledgeBaseIds(chatId).includes(kb.id)) {
          toggleActiveKnowledgeBase(chatId, kb.id, kb.name);
        }
        confirm = `База знаний «${kb.name}»`;
        deps.showNotification('success', confirm);
        break;
      }
      case 'clearAgent':
        clearActiveAgent();
        deps.showNotification('info', 'Агент сброшен');
        break;
      case 'navigate': {
        const path = String(intent.params?.path || '/');
        const allowed = new Set([
          '/',
          '/agents',
          '/skills',
          '/knowledge-bases',
          '/plugins',
          '/admin',
        ]);
        if (!allowed.has(path)) {
          return { ok: false, confirmPhrase: 'Некуда переходить', error: 'bad_path' };
        }
        deps.navigate(path);
        break;
      }
      case 'readLastReply': {
        const text = lastAssistantText(deps.state);
        if (!text) {
          return { ok: false, confirmPhrase: 'Нет ответа для чтения', error: 'no_reply' };
        }
        if (deps.confirmTts) {
          await speakConfirmation(text.slice(0, 800));
          return { ok: true, confirmPhrase: '' };
        }
        break;
      }
      case 'disableAssistant':
        updateVoiceAssistantSettings({ enabled: false });
        break;
      default:
        return { ok: false, confirmPhrase: 'Не поняла', error: 'unknown' };
    }

    if (deps.confirmTts && confirm) {
      void speakConfirmation(confirm);
    } else if (confirm) {
      deps.showNotification('success', confirm);
    }
    return { ok: true, confirmPhrase: confirm };
  } catch (e) {
    const msg = e instanceof Error ? e.message : 'Ошибка выполнения';
    deps.showNotification('error', msg);
    return { ok: false, confirmPhrase: 'Ошибка', error: msg };
  }
};
