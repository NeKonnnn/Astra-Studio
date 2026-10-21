import React, { createContext, useContext, useReducer, useEffect, ReactNode } from 'react';
import { getApiUrl, API_ENDPOINTS } from '../config/api';
import { initSettings } from '../settings';
import { useAuth } from './AuthContext';
import { normalizeMcpToolCallList } from '../mcp/utils/normalizeToolCall';
import { buildBranchChatTitle, cloneMessagesForBranch } from '../utils/branchChat';
import { mapServerConversationToChat } from '../utils/mapServerConversation';
import type { MessageFeedback } from '../constants/messageFeedback';
import type { ChatInputSuggestion } from '../chat/inputSuggestions';
import { MODEL_SETTINGS_CHANGED_EVENT } from '../utils/contextTokens';
import {
  fetchProjectsFromServer,
  migrateLocalProjectsToServer,
  syncProjectCreate,
  syncProjectUpdate,
} from '../utils/projectsApi';

export type { MessageFeedback };

/** Один столбец ответа в режиме multi-LLM (на карточке — свои кнопки и варианты перегенерации). */
export interface MultiLLMResponseSlot {
  model: string;
  content: string;
  isStreaming?: boolean;
  error?: boolean;
  alternativeResponses?: string[];
  currentResponseIndex?: number;
  /** Лайк/дизлайк для конкретного слота multi-LLM */
  feedback?: MessageFeedback | null;
  /** Когда слот начал генерироваться (ms). */
  generationStartedAtMs?: number;
  /** Сколько секунд слот генерировался до готовности. */
  generationDurationSec?: number;
}

// Типы данных
export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: string;
  isStreaming?: boolean;
  // Для режима multi-llm: несколько ответов от разных моделей
  multiLLMResponses?: MultiLLMResponseSlot[];
  // Для хранения нескольких вариантов ответов (при перегенерации)
  alternativeResponses?: string[];
  currentResponseIndex?: number; // Индекс текущего отображаемого варианта (0-based)
  /** Лайк/дизлайк ответа ассистента */
  feedback?: MessageFeedback | null;
  /** Трейс поиска по документам (общая библиотека / проект / агент) */
  documentSearch?: {
    query: string;
    sourceFiles: string[];
    hits: Array<{
      file: string;
      anchor: string;
      relevance: number;
      content: string;
      chunkIndex: number;
      documentId: number;
      store: string;
    }>;
  };
  /** Рассуждение модели отдельно от основного ответа (стриминг chat_thinking) */
  reasoningContent?: string;
  /** Inline-вложения (картинки/документы), прикреплённые к сообщению напрямую без RAG */
  inlineAttachments?: Array<{
    name: string;
    contentType: 'text' | 'image' | 'video';
    /** base64 data URL для изображений/видео; пустая строка для текстовых (контент уже в промпте) */
    preview?: string;
    size?: number;
    tokenEstimate?: number;
  }>;
  /** Варианты inline-вложений при перегенерации изображений */
  inlineAttachmentVariants?: Array<NonNullable<Message['inlineAttachments']>>;
  /** Идёт генерация изображения (ComfyUI / image_generation) */
  isImageGenerating?: boolean;
  /** Идёт генерация видео (ComfyUI / video_generation) */
  isVideoGenerating?: boolean;
  /** MCP tool calls (B-22 / F-6) */
  mcpToolCalls?: Array<{
    type: 'mcp_tool_start' | 'mcp_tool_end';
    server_id: string;
    tool: string;
    qualified_name: string;
    call_id?: string;
    arguments?: Record<string, unknown>;
    success?: boolean;
    duration_ms?: number;
    error?: string | null;
    model?: string;
    timestamp?: number;
    result_preview?: string;
    result?: string;
    has_image?: boolean;
    has_audio?: boolean;
    has_resource?: boolean;
    download_urls?: Array<{ url: string; label?: string; mime?: string }>;
  }>;
  /** Динамические follow-up подсказки (генерируются фоновым LLM-запросом) */
  followUpSuggestions?: ChatInputSuggestion[];
  followUpSuggestionsLoading?: boolean;
  /** Когда началась генерация ответа ассистента (Date.now()). */
  generationStartedAtMs?: number;
  /** Длительность генерации в секундах (фиксируется по завершении). */
  generationDurationSec?: number;
  /** Текущий агент последовательной цепочки (стрим). */
  chainCurrentAgent?: {
    agentId?: number | null;
    agentName: string;
    index: number;
    total: number;
    hideSequential?: boolean;
    isLast?: boolean;
  };
  /** Шаги Mixture-of-Agents (сохраняются в metadata). */
  chainSteps?: Array<{
    agentId: number;
    agentName: string;
    content: string;
    reasoning?: string;
    documentSearch?: Message['documentSearch'];
  }>;
  hideSequentialOutputs?: boolean;
}

export interface Chat {
  id: string;
  title: string;
  messages: Message[];
  createdAt: string;
  updatedAt: string;
  isArchived?: boolean;
  projectId?: string;
  isPinnedInProject?: boolean;
  /** Ветка: есть история в UI, но в сайдбаре не показываем, пока пользователь не отправит новое сообщение. */
  hiddenFromSidebarUntilUserMessage?: boolean;
  /** Сообщения выгружены из RAM; подгружаются при открытии чата. */
  messagesUnloaded?: boolean;
  /** Сколько сообщений было до выгрузки (для сайдбара / listing). */
  unloadedMessageCount?: number;
}

/** Чат показывается в блоке сайдбара «Все чаты», если уже есть переписка (сообщения пользователя и/или ассистента). */
export function chatIsListedInAllChatsSection(chat: Chat): boolean {
  if (chat.hiddenFromSidebarUntilUserMessage) return false;
  if (chat.messagesUnloaded) return (chat.unloadedMessageCount ?? 0) > 0;
  return chat.messages.length > 0;
}

export interface ModelInfo {
  loaded: boolean;
  metadata?: {
    'general.name': string;
    'general.architecture': string;
    'general.size_label': string;
  };
  path?: string;
  n_ctx?: number;
  n_gpu_layers?: number;
}

export interface ModelSettings {
  context_size: number;
  output_tokens: number;
  temperature: number;
  top_p: number;
  repeat_penalty: number;
  top_k: number;
  min_p: number;
  frequency_penalty: number;
  presence_penalty: number;
  use_gpu: boolean;
  streaming: boolean;
  streaming_speed: number; // Скорость потоковой генерации в миллисекундах
}

export interface Folder {
  id: string;
  name: string;
  chatIds: string[];
  expanded: boolean;
}

export interface Project {
  id: string;
  name: string;
  icon?: string;
  iconType?: 'icon' | 'emoji';
  iconColor?: string;
  memory: 'default' | 'project-only';
  instructions: string;
  knowledgeBasesEnabled?: boolean;
  knowledgeBaseIds?: number[];
  workspacePath?: string;
  createdAt: string;
  updatedAt: string;
}

export interface AppState {
  // Чаты
  chats: Chat[];
  currentChatId: string | null;
  folders: Folder[];
  projects: Project[];
  isLoading: boolean;
  loadingChatIds: string[];
  isInitialized: boolean;
  
  // Модель
  currentModel: ModelInfo | null;
  modelSettings: ModelSettings;
  availableModels: any[];
  
  // Голос
  isRecording: boolean;
  isSpeaking: boolean;
  voiceSettings: {
    voice_id: string;
    speech_rate: number;
  };
  
  // Транскрибация
  transcriptionSettings: {
    engine: 'whisperx' | 'vosk';
    language: string;
    auto_detect: boolean;
  };
  
  // Документы
  loadedDocument: string | null;
  
  // Системные уведомления
  notifications: Array<{
    id: string;
    type: 'success' | 'error' | 'info' | 'warning';
    message: string;
    timestamp: string;
  }>;
  
  // Статистика
  stats: {
    totalMessages: number;
    totalTokens: number;
    sessionsToday: number;
  };
}

// Действия
type AppAction =
  | { type: 'CREATE_CHAT'; payload: Chat }
  | { type: 'RESTORE_CHATS'; payload: { chats: Chat[]; currentChatId: string | null; folders: Folder[]; unloadInactive?: boolean } }
  | { type: 'SET_CURRENT_CHAT'; payload: string | null | { chatId: string | null; unloadInactive?: boolean } }
  | { type: 'UPDATE_CHAT_TITLE'; payload: { chatId: string; title: string } }
  | { type: 'UPDATE_CHAT_MESSAGES'; payload: { chatId: string; messages: Message[]; updatedAt?: string } }
  | { type: 'DELETE_CHAT'; payload: string }
  | { type: 'DELETE_ALL_CHATS' }
  | { type: 'ADD_MESSAGE'; payload: { chatId: string; message: Message } }
  | { type: 'UPDATE_MESSAGE'; payload: { chatId: string; messageId: string; content?: string; isStreaming?: boolean; multiLLMResponses?: Array<{ model: string; content: string; isStreaming?: boolean; error?: boolean; feedback?: MessageFeedback | null }>; alternativeResponses?: string[]; currentResponseIndex?: number; documentSearch?: Message['documentSearch']; reasoningContent?: string; mcpToolCalls?: Message['mcpToolCalls']; inlineAttachments?: Message['inlineAttachments']; isImageGenerating?: boolean; isVideoGenerating?: boolean; inlineAttachmentVariants?: Message['inlineAttachmentVariants']; feedback?: MessageFeedback | null } }
  | { type: 'PATCH_MESSAGE_FIELDS'; payload: { chatId: string; messageId: string; fields: Partial<Pick<Message, 'followUpSuggestions' | 'followUpSuggestionsLoading' | 'generationStartedAtMs' | 'generationDurationSec' | 'chainCurrentAgent' | 'chainSteps' | 'hideSequentialOutputs'>> } }
  | { type: 'APPEND_CHUNK'; payload: { chatId: string; messageId: string; chunk: string; isStreaming?: boolean } }
  | { type: 'SET_LOADING'; payload: boolean }
  | { type: 'SET_CHAT_LOADING'; payload: { chatId: string; loading: boolean } }
  | { type: 'SET_CURRENT_MODEL'; payload: ModelInfo }
  | { type: 'SET_MODEL_SETTINGS'; payload: ModelSettings }
  | { type: 'SET_AVAILABLE_MODELS'; payload: any[] }
  | { type: 'SET_RECORDING'; payload: boolean }
  | { type: 'SET_SPEAKING'; payload: boolean }
  | { type: 'SET_VOICE_SETTINGS'; payload: { voice_id: string; speech_rate: number } }
  | { type: 'SET_TRANSCRIPTION_SETTINGS'; payload: { engine: 'whisperx' | 'vosk'; language: string; auto_detect: boolean } }
  | { type: 'SET_LOADED_DOCUMENT'; payload: string | null }
  | { type: 'ADD_NOTIFICATION'; payload: { type: string; message: string } }
  | { type: 'REMOVE_NOTIFICATION'; payload: string }
  | { type: 'CLEAR_MESSAGES'; payload: string }
  | { type: 'CREATE_FOLDER'; payload: Folder }
  | { type: 'UPDATE_FOLDER'; payload: { folderId: string; name: string } }
  | { type: 'DELETE_FOLDER'; payload: string }
  | { type: 'MOVE_CHAT_TO_FOLDER'; payload: { chatId: string; folderId: string | null } }
  | { type: 'TOGGLE_FOLDER'; payload: string }
  | { type: 'ARCHIVE_ALL_CHATS' }
  | { type: 'ARCHIVE_CHAT'; payload: string }
  | { type: 'ARCHIVE_FOLDER'; payload: string }
  | { type: 'UNARCHIVE_CHAT'; payload: string }
  | { type: 'UNARCHIVE_ALL_CHATS' }
  | { type: 'UPDATE_STATS'; payload: Partial<AppState['stats']> }
  | { type: 'SET_INITIALIZED'; payload: boolean }
  | { type: 'CREATE_PROJECT'; payload: Project }
  | { type: 'UPDATE_PROJECT'; payload: { projectId: string; updates: Partial<Project> } }
  | { type: 'DELETE_PROJECT'; payload: string }
  | { type: 'RESTORE_PROJECTS'; payload: Project[] }
  | { type: 'MOVE_CHAT_TO_PROJECT'; payload: { chatId: string; projectId: string | null } }
  | { type: 'TOGGLE_PIN_IN_PROJECT'; payload: { chatId: string } };

// Функция для оценки количества токенов в тексте
function estimateTokens(text: string): number {
  if (!text) return 0;
  
  // Простая эвристика: 1 токен ≈ 4 символа для смешанного текста
  // Для русского текста может быть немного больше из-за длинных слов
  const baseTokens = Math.ceil(text.length / 4);
  
  // Дополнительные токены для специальных символов и форматирования
  const specialChars = (text.match(/[^\w\sа-яё]/g) || []).length;
  const newlines = (text.match(/\n/g) || []).length;
  
  return baseTokens + Math.ceil(specialChars / 2) + Math.ceil(newlines / 2);
}

// Функция для склеивания чанков (минимальные исправления)
function smartConcatenateChunk(existingContent: string, newChunk: string): string {
  if (!existingContent) return newChunk;
  if (!newChunk) return existingContent;
  
  // ТОЛЬКО критические исправления для кодовых блоков
  
  // 1. После ``` и языка программирования должен быть перенос строки
  const languageEndPattern = /```(python|javascript|typescript|java|cpp|c|php|ruby|go|rust|swift|kotlin|scala|html|css|sql|bash|shell|json|xml|yaml)$/;
  if (languageEndPattern.test(existingContent)) {
    return existingContent + '\n' + newChunk;
  }
  
  // 2. Начало кодового блока после текста
  if (newChunk.startsWith('```')) {
    const lastChar = existingContent[existingContent.length - 1];
    if (/[а-яёa-z]/i.test(lastChar)) {
      return existingContent + '\n\n' + newChunk;
    }
  }
  
  // ВСЕ ОСТАЛЬНОЕ - как было в оригинале (простое склеивание)
  return existingContent + newChunk;
}

/** LRU-кэш сообщений вне state — переживает unload, мгновенное переключение чатов. */
const CHAT_MESSAGES_CACHE_LIMIT = 16;
const chatMessagesCache = new Map<string, Message[]>();

function touchChatMessagesCache(chatId: string, messages: Message[]): void {
  if (!messages.length) return;
  if (chatMessagesCache.has(chatId)) chatMessagesCache.delete(chatId);
  chatMessagesCache.set(chatId, messages);
  while (chatMessagesCache.size > CHAT_MESSAGES_CACHE_LIMIT) {
    const oldest = chatMessagesCache.keys().next().value;
    if (oldest) chatMessagesCache.delete(oldest);
  }
}

function readChatMessagesCache(chatId: string): Message[] | null {
  const hit = chatMessagesCache.get(chatId);
  if (!hit?.length) return null;
  chatMessagesCache.delete(chatId);
  chatMessagesCache.set(chatId, hit);
  return hit;
}

function seedChatMessagesCache(chats: Chat[]): void {
  for (const chat of chats) {
    if (chat.messages?.length) touchChatMessagesCache(chat.id, chat.messages);
  }
}

function applyCachedMessagesIfNeeded(chat: Chat): Chat {
  if (chat.messages.length > 0) return chat;
  const cached = readChatMessagesCache(chat.id);
  if (!cached?.length) return chat;
  return {
    ...chat,
    messages: cached,
    messagesUnloaded: false,
    unloadedMessageCount: undefined,
  };
}

function unloadChatPayload(chat: Chat): Chat {
  if (chat.messagesUnloaded || chat.messages.length === 0) return chat;
  touchChatMessagesCache(chat.id, chat.messages);
  return {
    ...chat,
    messages: [],
    messagesUnloaded: true,
    unloadedMessageCount: chat.messages.length,
  };
}

function readPersistedChatBootstrap(): Pick<AppState, 'chats' | 'currentChatId' | 'folders' | 'isInitialized'> {
  try {
    const saved = localStorage.getItem('memo-chats');
    if (!saved) {
      return { chats: [], currentChatId: null, folders: [], isInitialized: false };
    }
    const parsed = JSON.parse(saved);
    if (!Array.isArray(parsed?.chats) || parsed.chats.length === 0) {
      return { chats: [], currentChatId: null, folders: [], isInitialized: false };
    }
    return {
      chats: parsed.chats,
      currentChatId: parsed.currentChatId || parsed.chats[0]?.id || null,
      folders: parsed.folders || [],
      isInitialized: true,
    };
  } catch {
    return { chats: [], currentChatId: null, folders: [], isInitialized: false };
  }
}

const persistedChatBootstrap = readPersistedChatBootstrap();
seedChatMessagesCache(persistedChatBootstrap.chats);

// Начальное состояние
const initialState: AppState = {
  chats: persistedChatBootstrap.chats,
  currentChatId: persistedChatBootstrap.currentChatId,
  folders: persistedChatBootstrap.folders,
  projects: [],
  isLoading: false,
  loadingChatIds: [],
  isInitialized: persistedChatBootstrap.isInitialized,
  currentModel: null,
  modelSettings: {
    context_size: 2048,
    output_tokens: 512,
    temperature: 0.7,
    top_p: 0.95,
    repeat_penalty: 1.05,
    top_k: 40,
    min_p: 0.05,
    frequency_penalty: 0.0,
    presence_penalty: 0.0,
    use_gpu: false,
    streaming: true,
    streaming_speed: 20, // Default streaming speed
  },
  availableModels: [],
  isRecording: false,
  isSpeaking: false,
  voiceSettings: {
    voice_id: 'ru',
    speech_rate: 1.0,
  },
  transcriptionSettings: {
    engine: 'whisperx',
    language: 'ru',
    auto_detect: true,
  },
  loadedDocument: null,
  notifications: [],
  stats: {
    totalMessages: 0,
    totalTokens: 0,
    sessionsToday: 0,
  },
};

function chatHasLiveWork(chat: Chat, loadingChatIds: string[]): boolean {
  if (loadingChatIds.includes(chat.id)) return true;
  return chat.messages.some(
    (m) =>
      m.isStreaming ||
      m.isImageGenerating ||
      m.isVideoGenerating ||
      m.multiLLMResponses?.some((r) => r.isStreaming),
  );
}

function unloadInactiveChats(
  chats: Chat[],
  keepIds: Set<string>,
  loadingChatIds: string[],
): Chat[] {
  return chats.map((chat) => {
    if (keepIds.has(chat.id) || chatHasLiveWork(chat, loadingChatIds)) {
      return chat;
    }
    return unloadChatPayload(chat);
  });
}

// Reducer
function appReducer(state: AppState, action: AppAction): AppState {
  switch (action.type) {
    case 'CREATE_CHAT':
      return {
        ...state,
        chats: [...state.chats, action.payload],
        currentChatId: action.payload.id,
      };
      
    case 'RESTORE_CHATS': {
      const currentId = action.payload.currentChatId;
      const keep = new Set<string>();
      if (currentId) keep.add(currentId);
      const shouldUnload = action.payload.unloadInactive !== false;
      const prevById = new Map(state.chats.map((c) => [c.id, c]));
      let chats: Chat[] = action.payload.chats.map((serverChat) => {
        const prev = prevById.get(serverChat.id);
        // isArchived с сервера — источник истины (явный boolean из metadata.archived).
        const archived = Boolean(serverChat.isArchived);
        if (prev && prev.messages.length > 0 && serverChat.messages.length === 0) {
          return {
            ...serverChat,
            messages: prev.messages,
            messagesUnloaded: prev.messagesUnloaded,
            unloadedMessageCount: prev.unloadedMessageCount,
            isArchived: archived,
          };
        }
        return {
          ...serverChat,
          isArchived: archived,
        };
      });
      if (shouldUnload) {
        chats = unloadInactiveChats(chats, keep, state.loadingChatIds);
      }
      if (currentId) {
        chats = chats.map((c) => (c.id === currentId ? applyCachedMessagesIfNeeded(c) : c));
      }
      return {
        ...state,
        chats,
        currentChatId: currentId,
        folders: action.payload.folders || [],
        isInitialized: true,
      };
    }
      
    case 'SET_CURRENT_CHAT': {
      const raw = action.payload;
      const nextId = raw && typeof raw === 'object' ? raw.chatId : raw;
      const unloadInactive =
        raw && typeof raw === 'object' ? Boolean(raw.unloadInactive) : true;
      const prevId = state.currentChatId;
      if (!unloadInactive) {
        // Даже если выгрузка/гидратация отключены (например, нет токена),
        // попробуем восстановить чат из in-memory LRU-кэша.
        if (!nextId) {
          return { ...state, currentChatId: nextId };
        }
        return {
          ...state,
          currentChatId: nextId,
          chats: state.chats.map((c) => (c.id === nextId ? applyCachedMessagesIfNeeded(c) : c)),
        };
      }
      const keep = new Set<string>();
      if (nextId) keep.add(nextId);
      // Держим предыдущий чат гидратированным для быстрого переключения туда-обратно.
      if (prevId && prevId !== nextId) keep.add(prevId);
      let chats = unloadInactiveChats(state.chats, keep, state.loadingChatIds);
      if (nextId) {
        chats = chats.map((c) => (c.id === nextId ? applyCachedMessagesIfNeeded(c) : c));
      }
      return {
        ...state,
        currentChatId: nextId,
        chats,
      };
    }
      
    case 'UPDATE_CHAT_TITLE':
      return {
        ...state,
        chats: state.chats.map(chat =>
          chat.id === action.payload.chatId
            ? { ...chat, title: action.payload.title, updatedAt: new Date().toISOString() }
            : chat
        ),
      };
      
    case 'UPDATE_CHAT_MESSAGES':
      touchChatMessagesCache(action.payload.chatId, action.payload.messages);
      return {
        ...state,
        chats: state.chats.map(chat =>
          chat.id === action.payload.chatId
            ? {
                ...chat,
                messages: action.payload.messages,
                messagesUnloaded: false,
                unloadedMessageCount: undefined,
                // Гидратация из API не должна сдвигать чат в «Сегодня» — берём дату с сервера или оставляем текущую.
                updatedAt: action.payload.updatedAt ?? chat.updatedAt,
              }
            : chat
        ),
      };
      
    case 'DELETE_CHAT':
      return {
        ...state,
        chats: state.chats.filter(chat => chat.id !== action.payload),
        currentChatId: state.currentChatId === action.payload ? null : state.currentChatId,
      };
      
    case 'DELETE_ALL_CHATS':
      return {
        ...state,
        chats: [],
        currentChatId: null,
        folders: [], // Также удаляем все папки, так как они пустые
      };
      
    case 'ADD_MESSAGE': {
      const { chatId, message } = action.payload;
      return {
        ...state,
        chats: state.chats.map(chat =>
          chat.id === chatId
            ? {
                ...chat,
                messages: [...chat.messages, message],
                messagesUnloaded: false,
                unloadedMessageCount: undefined,
                updatedAt: new Date().toISOString(),
                ...(chat.hiddenFromSidebarUntilUserMessage && message.role === 'user'
                  ? { hiddenFromSidebarUntilUserMessage: undefined }
                  : {}),
              }
            : chat
        ),
        stats: {
          ...state.stats,
          totalMessages: state.stats.totalMessages + 1,
          totalTokens: state.stats.totalTokens + estimateTokens(message.content),
        },
      };
    }
      
    case 'UPDATE_MESSAGE': {
      const { chatId, messageId, content, isStreaming, multiLLMResponses, alternativeResponses, currentResponseIndex, documentSearch, reasoningContent, mcpToolCalls, inlineAttachments, isImageGenerating, isVideoGenerating, inlineAttachmentVariants, feedback } = action.payload;
      
      const currentChat = state.chats.find(chat => chat.id === chatId);
      const updatedMessage = currentChat?.messages.find(msg => msg.id === messageId);
      const nextContent = content !== undefined ? content : (updatedMessage?.content || '');
      const finishing =
        isStreaming === false &&
        Boolean(updatedMessage?.generationStartedAtMs) &&
        (updatedMessage?.isStreaming || !updatedMessage?.generationDurationSec);
      const generationDurationSec = finishing
        ? Math.max(
            1,
            Math.round((Date.now() - (updatedMessage!.generationStartedAtMs as number)) / 1000),
          )
        : undefined;
      
      const newState = {
        ...state,
        chats: state.chats.map(chat =>
          chat.id === chatId
            ? {
                ...chat,
                messages: chat.messages.map(msg =>
                  msg.id === messageId
                    ? { 
                        ...msg, 
                        ...(content !== undefined ? { content } : {}),
                        ...(isStreaming !== undefined ? { isStreaming } : {}),
                        ...(multiLLMResponses !== undefined ? { multiLLMResponses } : {}),
                        ...(alternativeResponses !== undefined ? { alternativeResponses } : {}),
                        ...(currentResponseIndex !== undefined ? { currentResponseIndex } : {}),
                        ...(documentSearch !== undefined ? { documentSearch } : {}),
                        ...(reasoningContent !== undefined ? { reasoningContent } : {}),
                        ...(mcpToolCalls !== undefined ? { mcpToolCalls } : {}),
                        ...(inlineAttachments !== undefined ? { inlineAttachments } : {}),
                        ...(isImageGenerating !== undefined ? { isImageGenerating } : {}),
                        ...(isVideoGenerating !== undefined ? { isVideoGenerating } : {}),
                        ...(inlineAttachmentVariants !== undefined ? { inlineAttachmentVariants } : {}),
                        ...(feedback !== undefined ? { feedback } : {}),
                        ...(generationDurationSec !== undefined ? { generationDurationSec } : {}),
                      }
                    : msg
                ),
                updatedAt: new Date().toISOString(),
              }
            : chat
        ),
        stats: {
          ...state.stats,
          totalTokens:
            content !== undefined && updatedMessage
              ? state.stats.totalTokens -
                estimateTokens(updatedMessage.content || '') +
                estimateTokens(nextContent || '')
              : state.stats.totalTokens,
        },
      };
      
      return newState;
    }

    case 'PATCH_MESSAGE_FIELDS': {
      const { chatId, messageId, fields } = action.payload;
      const targetChat = state.chats.find((chat) => chat.id === chatId);
      const targetMsg = targetChat?.messages.find((msg) => msg.id === messageId);
      if (!targetMsg) return state;
      const needsUpdate = (Object.keys(fields) as Array<keyof typeof fields>).some(
        (key) => targetMsg[key] !== fields[key],
      );
      if (!needsUpdate) return state;
      return {
        ...state,
        chats: state.chats.map((chat) =>
          chat.id === chatId
            ? {
                ...chat,
                messages: chat.messages.map((msg) =>
                  msg.id === messageId ? { ...msg, ...fields } : msg,
                ),
              }
            : chat,
        ),
      };
    }
      
    case 'APPEND_CHUNK': {
      const { chatId, messageId, chunk, isStreaming } = action.payload;
      
      const currentChat = state.chats.find(chat => chat.id === chatId);
      const chunkMessage = currentChat?.messages.find(msg => msg.id === messageId);
      
      const newContent = chunkMessage ? smartConcatenateChunk(chunkMessage.content, chunk) : chunk;
      
      return {
        ...state,
        chats: state.chats.map(chat =>
          chat.id === chatId
            ? {
                ...chat,
                messages: chat.messages.map(msg =>
                  msg.id === messageId
                    ? {
                        ...msg,
                        content: newContent,
                        ...(isStreaming !== undefined && { isStreaming })
                      }
                    : msg
                ),
                updatedAt: new Date().toISOString(),
              }
            : chat
        ),
        stats: {
          ...state.stats,
          // Добавляем токены для нового чанка
          totalTokens: state.stats.totalTokens + estimateTokens(chunk),
        },
      };
    }
      
    case 'SET_LOADING':
      return {
        ...state,
        isLoading: action.payload,
      };

    case 'SET_CHAT_LOADING': {
      const { chatId, loading } = action.payload;
      const hasChat = state.loadingChatIds.includes(chatId);
      const nextLoadingChatIds = loading
        ? (hasChat ? state.loadingChatIds : [...state.loadingChatIds, chatId])
        : state.loadingChatIds.filter((id) => id !== chatId);
      return {
        ...state,
        loadingChatIds: nextLoadingChatIds,
      };
    }
      
    case 'SET_CURRENT_MODEL':
      return {
        ...state,
        currentModel: action.payload,
      };
      
    case 'SET_MODEL_SETTINGS':
      return {
        ...state,
        modelSettings: action.payload,
      };
      
    case 'SET_AVAILABLE_MODELS':
      return {
        ...state,
        availableModels: action.payload,
      };
      
    case 'SET_RECORDING':
      return {
        ...state,
        isRecording: action.payload,
      };
      
    case 'SET_SPEAKING':
      return {
        ...state,
        isSpeaking: action.payload,
      };
      
    case 'SET_VOICE_SETTINGS':
      return {
        ...state,
        voiceSettings: action.payload,
      };
      
    case 'SET_TRANSCRIPTION_SETTINGS':
      return {
        ...state,
        transcriptionSettings: action.payload,
      };
      
    case 'SET_LOADED_DOCUMENT':
      return {
        ...state,
        loadedDocument: action.payload,
      };
      
    case 'ADD_NOTIFICATION': {
      const notification = {
        id: Date.now().toString(),
        type: action.payload.type as 'success' | 'error' | 'info' | 'warning',
        message: action.payload.message,
        timestamp: new Date().toISOString(),
      };
      return {
        ...state,
        notifications: [...state.notifications, notification],
      };
    }
      
    case 'REMOVE_NOTIFICATION':
      return {
        ...state,
        notifications: state.notifications.filter(n => n.id !== action.payload),
      };
      
    case 'CLEAR_MESSAGES': {
      const chatId = action.payload;
      return {
        ...state,
        chats: state.chats.map(chat =>
          chat.id === chatId
            ? { ...chat, messages: [], updatedAt: new Date().toISOString() }
            : chat
        ),
        stats: {
          ...state.stats,
          totalMessages: 0,
          totalTokens: 0,
        },
      };
    }
      
    case 'UPDATE_STATS':
      return {
        ...state,
        stats: {
          ...state.stats,
          ...action.payload,
        },
      };
      
    case 'CREATE_FOLDER':
      return {
        ...state,
        folders: [...state.folders, action.payload],
      };
      
    case 'UPDATE_FOLDER':
      return {
        ...state,
        folders: state.folders.map(folder =>
          folder.id === action.payload.folderId
            ? { ...folder, name: action.payload.name }
            : folder
        ),
      };
      
    case 'DELETE_FOLDER':
      return {
        ...state,
        folders: state.folders.filter(folder => folder.id !== action.payload),
      };
      
    case 'MOVE_CHAT_TO_FOLDER': {
      // Обновляем папки
      const updatedFolders = state.folders.map(folder => ({
        ...folder,
        chatIds: folder.id === action.payload.folderId
          ? [...folder.chatIds, action.payload.chatId]
          : folder.chatIds.filter(id => id !== action.payload.chatId)
      }));
      
      // Удаляем папку "Закреплено" если она стала пустой
      const pinnedFolder = updatedFolders.find(f => f.name === 'Закреплено');
      const finalFolders = pinnedFolder && pinnedFolder.chatIds.length === 0
        ? updatedFolders.filter(f => f.id !== pinnedFolder.id)
        : updatedFolders;
      
      return {
        ...state,
        folders: finalFolders,
      };
    }
      
    case 'TOGGLE_FOLDER':
      return {
        ...state,
        folders: state.folders.map(folder =>
          folder.id === action.payload
            ? { ...folder, expanded: !folder.expanded }
            : folder
        ),
      };
      
    case 'ARCHIVE_ALL_CHATS': {
      // Помечаем все неархивированные чаты как архивированные
      // Если текущий чат архивируется, сбрасываем currentChatId
      return {
        ...state,
        chats: state.chats.map(chat => ({
          ...chat,
          isArchived: true,
        })),
        currentChatId: null, // Сбрасываем текущий чат, так как все архивируются
      };
    }
    
    case 'ARCHIVE_CHAT': {
      // Помечаем конкретный чат как архивированный
      // Если архивируется текущий чат, сбрасываем currentChatId
      return {
        ...state,
        chats: state.chats.map(chat =>
          chat.id === action.payload
            ? { ...chat, isArchived: true }
            : chat
        ),
        currentChatId: state.currentChatId === action.payload ? null : state.currentChatId,
      };
    }
    
    case 'ARCHIVE_FOLDER': {
      // Архивируем все чаты в папке
      const folder = state.folders.find(f => f.id === action.payload);
      if (!folder) {
        return state;
      }
      
      const chatIdsToArchive = folder.chatIds;
      const newCurrentChatId = chatIdsToArchive.includes(state.currentChatId || '') 
        ? null 
        : state.currentChatId;
      
      return {
        ...state,
        chats: state.chats.map(chat =>
          chatIdsToArchive.includes(chat.id)
            ? { ...chat, isArchived: true }
            : chat
        ),
        currentChatId: newCurrentChatId,
      };
    }
    
    case 'UNARCHIVE_CHAT': {
      // Убираем пометку архивирования у конкретного чата
      return {
        ...state,
        chats: state.chats.map(chat =>
          chat.id === action.payload
            ? { ...chat, isArchived: false }
            : chat
        ),
      };
    }
    
    case 'UNARCHIVE_ALL_CHATS': {
      // Убираем пометку архивирования у всех чатов
      return {
        ...state,
        chats: state.chats.map(chat => ({
          ...chat,
          isArchived: false,
        })),
      };
    }
      
    case 'SET_INITIALIZED':
      return {
        ...state,
        isInitialized: action.payload,
      };
    
    case 'CREATE_PROJECT':
      return {
        ...state,
        projects: [...state.projects, action.payload],
      };
    
    case 'UPDATE_PROJECT':
      return {
        ...state,
        projects: state.projects.map(project =>
          project.id === action.payload.projectId
            ? { ...project, ...action.payload.updates, updatedAt: new Date().toISOString() }
            : project
        ),
      };
    
    case 'DELETE_PROJECT':
      return {
        ...state,
        projects: state.projects.filter(project => project.id !== action.payload),
      };
    
    case 'RESTORE_PROJECTS':
      return {
        ...state,
        projects: action.payload,
      };
    
    case 'MOVE_CHAT_TO_PROJECT':
      return {
        ...state,
        chats: state.chats.map(chat =>
          chat.id === action.payload.chatId
            ? { ...chat, projectId: action.payload.projectId ?? undefined }
            : chat
        ),
      };
    
    case 'TOGGLE_PIN_IN_PROJECT':
      return {
        ...state,
        chats: state.chats.map(chat =>
          chat.id === action.payload.chatId
            ? { ...chat, isPinnedInProject: !chat.isPinnedInProject }
            : chat
        ),
      };
      
    default:
      return state;
  }
}

// Контекст
const AppContext = createContext<{
  state: AppState;
  dispatch: React.Dispatch<AppAction>;
} | null>(null);

// Провайдер
export function AppProvider({ children }: { children: ReactNode }) {
  // Загружаем состояние из localStorage при инициализации
  const [state, dispatch] = useReducer(appReducer, initialState);
  const { token } = useAuth();

  // Персональные настройки модели с бэкенда (по user_id)
  useEffect(() => {
    const loadModelSettings = async () => {
      try {
        const effectiveToken = token || localStorage.getItem('auth_token');
        const response = await fetch(getApiUrl('/api/models/settings'), {
          headers: effectiveToken ? { Authorization: `Bearer ${effectiveToken}` } : {},
        });
        if (response.ok) {
          const data = await response.json();
          dispatch({
            type: 'SET_MODEL_SETTINGS',
            payload: { ...initialState.modelSettings, ...data },
          });
        }
      } catch {
        /* ignore */
      }
    };
    void loadModelSettings();

    const onSettingsChanged = () => {
      void loadModelSettings();
    };
    window.addEventListener(MODEL_SETTINGS_CHANGED_EVENT, onSettingsChanged);
    return () => window.removeEventListener(MODEL_SETTINGS_CHANGED_EVENT, onSettingsChanged);
  }, [token]);

  // Инициализация/перезагрузка состояния при смене токена (LDAP login/logout)
  useEffect(() => {
    const loadInitialState = async () => {
      let parsed: any = null;
      try {
        const savedState = localStorage.getItem('memo-chats');
        parsed = savedState ? JSON.parse(savedState) : null;
      } catch (error) {
        console.error('Ошибка загрузки состояния из localStorage:', error);
      }

      const localProjects: Project[] = Array.isArray(parsed?.projects) ? parsed.projects : [];

      const effectiveToken = token || localStorage.getItem('auth_token');
      if (effectiveToken) {
        try {
          await initSettings();

          try {
            let serverProjects = await fetchProjectsFromServer();
            if (serverProjects.length === 0 && localProjects.length > 0) {
              await migrateLocalProjectsToServer(localProjects);
              serverProjects = await fetchProjectsFromServer();
            }
            if (serverProjects.length > 0) {
              dispatch({ type: 'RESTORE_PROJECTS', payload: serverProjects });
            } else if (localProjects.length > 0) {
              dispatch({ type: 'RESTORE_PROJECTS', payload: localProjects });
            }
          } catch (error) {
            console.warn('Не удалось загрузить проекты из backend, используем localStorage', error);
            if (localProjects.length > 0) {
              dispatch({ type: 'RESTORE_PROJECTS', payload: localProjects });
            }
          }

          const response = await fetch(getApiUrl('/api/conversations?limit=500'), {
            headers: { Authorization: `Bearer ${effectiveToken}` },
          });
          if (response.ok) {
            const payload = await response.json();
            const serverChats: Chat[] = Array.isArray(payload?.conversations)
              ? payload.conversations.map(mapServerConversationToChat)
              : [];
            if (serverChats.length > 0) {
              // Миграция: раньше архив был только в localStorage — переносим на сервер.
              const localArchivedIds = new Set(
                (Array.isArray(parsed?.chats) ? parsed.chats : [])
                  .filter((c: Chat) => c?.isArchived && c?.id)
                  .map((c: Chat) => String(c.id)),
              );
              const mergedChats = serverChats.map((c) =>
                localArchivedIds.has(c.id) && !c.isArchived ? { ...c, isArchived: true } : c,
              );
              const toSyncArchive = mergedChats
                .filter((c) => c.isArchived && localArchivedIds.has(c.id))
                .map((c) => c.id);
              if (toSyncArchive.length) {
                void Promise.all(
                  toSyncArchive.map((chatId) =>
                    fetch(getApiUrl(`/api/conversations/${chatId}/archive`), {
                      method: 'POST',
                      headers: { Authorization: `Bearer ${effectiveToken}` },
                    }).catch(() => {}),
                  ),
                );
              }
              const savedCurrentId =
                typeof parsed?.currentChatId === 'string' ? parsed.currentChatId : null;
              const restoredCurrentId =
                savedCurrentId && mergedChats.some((c) => c.id === savedCurrentId && !c.isArchived)
                  ? savedCurrentId
                  : mergedChats.find((c) => !c.isArchived)?.id || mergedChats[0]?.id || null;
              dispatch({
                type: 'RESTORE_CHATS',
                payload: {
                  chats: mergedChats,
                  currentChatId: restoredCurrentId,
                  folders: parsed?.folders || [],
                  unloadInactive: true,
                },
              });
              return;
            }
          }
        } catch (error) {
          console.warn('Не удалось загрузить диалоги из backend, используем localStorage', error);
        }
      } else if (localProjects.length > 0) {
        dispatch({ type: 'RESTORE_PROJECTS', payload: localProjects });
      }

      if (parsed?.chats?.length > 0) {
        dispatch({
          type: 'RESTORE_CHATS',
          payload: {
            chats: parsed.chats || [],
            currentChatId: parsed.currentChatId || null,
            folders: parsed.folders || [],
            // Offline-кэш: без API не выгружаем, иначе нечего гидратировать.
            unloadInactive: false,
          },
        });
      } else {
        dispatch({ type: 'SET_INITIALIZED', payload: true });
      }
    };

    loadInitialState();
  }, [token]);

  // Подгрузка сообщений выгруженного чата при переключении.
  const currentChatNeedsHydration = Boolean(
    state.currentChatId &&
      state.chats.find((c) => c.id === state.currentChatId)?.messagesUnloaded,
  );

  useEffect(() => {
    const chatId = state.currentChatId;
    if (!chatId || !currentChatNeedsHydration) return;

    let cancelled = false;
    const effectiveToken = token || localStorage.getItem('auth_token');
    if (!effectiveToken) return;

    (async () => {
      try {
        await initSettings();
        const response = await fetch(getApiUrl(`/api/conversations/${chatId}`), {
          headers: { Authorization: `Bearer ${effectiveToken}` },
        });
        if (!response.ok || cancelled) return;
        const payload = await response.json();
        const conversation = payload?.conversation ?? payload;
        const mapped = mapServerConversationToChat(conversation);
        if (cancelled) return;
        dispatch({
          type: 'UPDATE_CHAT_MESSAGES',
          payload: { chatId, messages: mapped.messages, updatedAt: mapped.updatedAt },
        });
      } catch (error) {
        console.warn('Не удалось подгрузить сообщения чата', chatId, error);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [state.currentChatId, currentChatNeedsHydration, token]);

  // Сохраняем состояние в localStorage при изменении.
  // Полные чаты с base64-картинками быстро превышают квоту (~5 МБ) → QuotaExceededError.
  useEffect(() => {
    const stripHeavyMessage = (msg: Message): Message => {
      const { inlineAttachments, inlineAttachmentVariants, documentSearch, mcpToolCalls, ...rest } = msg;
      const slim: Message = { ...rest };
      // Превью картинок (data URL) — главный раздуватель квоты; имена/типы оставляем.
      if (inlineAttachments?.length) {
        slim.inlineAttachments = inlineAttachments.map(({ preview, ...att }) => ({
          ...att,
          preview: preview && preview.startsWith('data:') ? undefined : preview,
        }));
      }
      if (inlineAttachmentVariants?.length) {
        slim.inlineAttachmentVariants = inlineAttachmentVariants.map((variant) =>
          (variant || []).map(({ preview, ...att }) => ({
            ...att,
            preview: preview && preview.startsWith('data:') ? undefined : preview,
          })),
        );
      }
      if (documentSearch) {
        slim.documentSearch = {
          ...documentSearch,
          hits: (documentSearch.hits || []).map((h) => ({
            ...h,
            content: (h.content || '').slice(0, 200),
          })),
        };
      }
      if (mcpToolCalls?.length) {
        slim.mcpToolCalls = mcpToolCalls.map((c) => ({
          ...c,
          result: undefined,
          result_preview: (c.result_preview || '').slice(0, 200),
          arguments: undefined,
        }));
      }
      // Длинные тексты сообщений тоже режем для offline-кэша (источник правды — backend).
      if (typeof slim.content === 'string' && slim.content.length > 8000) {
        slim.content = slim.content.slice(0, 8000) + '\n…';
      }
      if (slim.reasoningContent && slim.reasoningContent.length > 4000) {
        slim.reasoningContent = slim.reasoningContent.slice(0, 4000) + '\n…';
      }
      return slim;
    };

    const buildSlimState = (maxChats: number, maxMessagesPerChat: number) => {
      const chats = (state.chats || [])
        .slice()
        .sort((a, b) => String(b.updatedAt || '').localeCompare(String(a.updatedAt || '')))
        .slice(0, maxChats)
        .map((chat) => ({
          ...chat,
          messages: (chat.messages || []).slice(-maxMessagesPerChat).map(stripHeavyMessage),
        }));
      return {
        chats,
        currentChatId: state.currentChatId,
        folders: state.folders,
        projects: state.projects,
      };
    };

    const trySave = (payload: unknown): boolean => {
      try {
        localStorage.setItem('memo-chats', JSON.stringify(payload));
        return true;
      } catch (error) {
        if (error instanceof DOMException && error.name === 'QuotaExceededError') {
          return false;
        }
        console.error('Ошибка сохранения состояния в localStorage:', error);
        return true; // другая ошибка — не крутим retry
      }
    };

    // 1) обычный slim → 2) сильнее урезаем → 3) только метаданные чатов
    if (trySave(buildSlimState(40, 80))) return;
    if (trySave(buildSlimState(20, 30))) return;
    if (
      trySave({
        chats: (state.chats || []).slice(0, 30).map((c) => ({
          id: c.id,
          title: c.title,
          messages: [],
          createdAt: c.createdAt,
          updatedAt: c.updatedAt,
          isArchived: c.isArchived,
          projectId: c.projectId,
          isPinnedInProject: c.isPinnedInProject,
        })),
        currentChatId: state.currentChatId,
        folders: state.folders,
        projects: state.projects,
      })
    ) {
      console.warn('localStorage: сохранены только метаданные чатов (квота переполнена)');
      return;
    }
    console.error(
      'Ошибка сохранения состояния в localStorage: QuotaExceededError — не удалось уместить даже метаданные. Очистите данные сайта для localhost:3000.',
    );
  }, [state.chats, state.currentChatId, state.folders, state.projects]);

  return (
    <AppContext.Provider value={{ state, dispatch }}>
      {children}
    </AppContext.Provider>
  );
}

// Хук для использования контекста
export function useAppContext() {
  const context = useContext(AppContext);
  if (!context) {
    throw new Error('useAppContext must be used within an AppProvider');
  }
  return context;
}

// Хелперы для часто используемых действий
export function useAppActions() {
  const { dispatch, state } = useAppContext();
  const { token } = useAuth();

  return {
    createChat: (title: string = 'Новый чат') => {
      const chatId = Date.now().toString() + Math.random().toString(36).substr(2, 9);
      const now = new Date().toISOString();
      
      const newChat: Chat = {
        id: chatId,
        title,
        messages: [],
        createdAt: now,
        updatedAt: now,
      };
      
      dispatch({
        type: 'CREATE_CHAT',
        payload: newChat,
      });
      
      return chatId;
    },
    
    setCurrentChat: (chatId: string | null) => {
      const canRehydrate = Boolean(token || localStorage.getItem('auth_token'));
      dispatch({
        type: 'SET_CURRENT_CHAT',
        payload: { chatId, unloadInactive: canRehydrate },
      });
    },
    
    updateChatTitle: (chatId: string, title: string) => {
      dispatch({
        type: 'UPDATE_CHAT_TITLE',
        payload: { chatId, title },
      });
      // Синхронизируем заголовок с бэкендом в фоне
      const effectiveToken = token || localStorage.getItem('auth_token');
      if (effectiveToken) {
        initSettings().then(() => {
          fetch(getApiUrl(`/api/conversations/${chatId}/title`), {
            method: 'PUT',
            headers: {
              'Content-Type': 'application/json',
              Authorization: `Bearer ${effectiveToken}`,
            },
            body: JSON.stringify({ title }),
          }).catch(() => {
            // Тихая ошибка — заголовок обновлён локально, backend недоступен
          });
        }).catch(() => {});
      }
    },
    
    updateChatMessages: (chatId: string, messages: Message[]) => {
      dispatch({
        type: 'UPDATE_CHAT_MESSAGES',
        payload: { chatId, messages },
      });
    },
    
    deleteChat: (chatId: string) => {
      dispatch({
        type: 'DELETE_CHAT',
        payload: chatId,
      });
      const effectiveToken = token || localStorage.getItem('auth_token');
      if (effectiveToken) {
        initSettings().then(() => {
          fetch(getApiUrl(`/api/conversations/${chatId}`), {
            method: 'DELETE',
            headers: { Authorization: `Bearer ${effectiveToken}` },
          }).catch(() => {});
        }).catch(() => {});
      }
    },
    
    deleteAllChats: () => {
      dispatch({
        type: 'DELETE_ALL_CHATS',
      });
      const effectiveToken = token || localStorage.getItem('auth_token');
      if (effectiveToken) {
        initSettings().then(() => {
          fetch(getApiUrl('/api/conversations'), {
            method: 'DELETE',
            headers: { Authorization: `Bearer ${effectiveToken}` },
          }).catch(() => {});
        }).catch(() => {});
      }
    },
    
    addMessage: (chatId: string, message: Omit<Message, 'id'>) => {
      // Генерируем ID в формате msg_{12 hex символов}, как в MongoDB
      const randomHex = Array.from({ length: 12 }, () => 
        Math.floor(Math.random() * 16).toString(16)
      ).join('');
      const messageId = `msg_${randomHex}`;
      
      dispatch({
        type: 'ADD_MESSAGE',
        payload: {
          chatId,
          message: {
            ...message,
            id: messageId,
          },
        },
      });
      return messageId;
    },
    
    updateMessage: (chatId: string, messageId: string, content?: string, isStreaming?: boolean, multiLLMResponses?: Array<{ model: string; content: string; isStreaming?: boolean; error?: boolean; feedback?: MessageFeedback | null }>, alternativeResponses?: string[], currentResponseIndex?: number, documentSearch?: Message['documentSearch'], reasoningContent?: string, mcpToolCalls?: Message['mcpToolCalls'], inlineAttachments?: Message['inlineAttachments'], isImageGenerating?: boolean, inlineAttachmentVariants?: Message['inlineAttachmentVariants'], feedback?: MessageFeedback | null, isVideoGenerating?: boolean) => {
      dispatch({ type: 'UPDATE_MESSAGE', payload: { chatId, messageId, content, isStreaming, multiLLMResponses, alternativeResponses, currentResponseIndex, documentSearch, reasoningContent, mcpToolCalls, inlineAttachments, isImageGenerating, isVideoGenerating, inlineAttachmentVariants, feedback } });
    },

    patchMessageFields: (
      chatId: string,
      messageId: string,
      fields: Partial<
        Pick<
          Message,
          | 'followUpSuggestions'
          | 'followUpSuggestionsLoading'
          | 'generationStartedAtMs'
          | 'generationDurationSec'
          | 'chainCurrentAgent'
          | 'chainSteps'
          | 'hideSequentialOutputs'
        >
      >,
    ) => {
      dispatch({ type: 'PATCH_MESSAGE_FIELDS', payload: { chatId, messageId, fields } });
    },
    
    appendChunk: (chatId: string, messageId: string, chunk: string, isStreaming?: boolean) => {
      dispatch({ type: 'APPEND_CHUNK', payload: { chatId, messageId, chunk, isStreaming } });
    },
    
    setLoading: (loading: boolean) => {
      dispatch({ type: 'SET_LOADING', payload: loading });
    },

    setChatLoading: (chatId: string, loading: boolean) => {
      dispatch({ type: 'SET_CHAT_LOADING', payload: { chatId, loading } });
    },
    
    showNotification: (type: 'success' | 'error' | 'info' | 'warning', message: string) => {
      dispatch({ type: 'ADD_NOTIFICATION', payload: { type, message } });
    },
    
    removeNotification: (id: string) => {
      dispatch({ type: 'REMOVE_NOTIFICATION', payload: id });
    },
    
    clearMessages: (chatId: string) => {
      dispatch({ type: 'CLEAR_MESSAGES', payload: chatId });
    },
    
    setCurrentModel: (model: ModelInfo) => {
      dispatch({ type: 'SET_CURRENT_MODEL', payload: model });
    },
    
    setModelSettings: (settings: ModelSettings) => {
      dispatch({ type: 'SET_MODEL_SETTINGS', payload: settings });
    },
    
    setRecording: (recording: boolean) => {
      dispatch({ type: 'SET_RECORDING', payload: recording });
    },
    
    setSpeaking: (speaking: boolean) => {
      dispatch({ type: 'SET_SPEAKING', payload: speaking });
    },
    
    setTranscriptionSettings: (settings: { engine: 'whisperx' | 'vosk'; language: string; auto_detect: boolean }) => {
      dispatch({ type: 'SET_TRANSCRIPTION_SETTINGS', payload: settings });
    },
    
    // Хелперы для получения данных
    getCurrentChat: () => {
      return state.chats.find(chat => chat.id === state.currentChatId) || null;
    },
    
    getCurrentMessages: () => {
      const currentChat = state.chats.find(chat => chat.id === state.currentChatId);
      return currentChat?.messages || [];
    },
    
    // Функции для работы с папками
    createFolder: (name: string) => {
      const folderId = Date.now().toString() + Math.random().toString(36).substr(2, 9);
      const newFolder: Folder = {
        id: folderId,
        name,
        chatIds: [],
        expanded: true,
      };
      dispatch({ type: 'CREATE_FOLDER', payload: newFolder });
      return folderId;
    },
    
    updateFolder: (folderId: string, name: string) => {
      dispatch({ type: 'UPDATE_FOLDER', payload: { folderId, name } });
    },
    
    deleteFolder: (folderId: string) => {
      dispatch({ type: 'DELETE_FOLDER', payload: folderId });
    },
    
    moveChatToFolder: (chatId: string, folderId: string | null) => {
      dispatch({ type: 'MOVE_CHAT_TO_FOLDER', payload: { chatId, folderId } });
    },
    
    toggleFolder: (folderId: string) => {
      dispatch({ type: 'TOGGLE_FOLDER', payload: folderId });
    },
    
    getFolders: () => {
      return state.folders;
    },
    
    exportChats: () => {
      const exportData = {
        chats: state.chats,
        folders: state.folders,
        exportDate: new Date().toISOString(),
        version: '1.0',
      };
      const dataStr = JSON.stringify(exportData, null, 2);
      const dataBlob = new Blob([dataStr], { type: 'application/json' });
      const url = URL.createObjectURL(dataBlob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `chats_export_${new Date().toISOString().split('T')[0]}.json`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
    },
    
    importChats: (file: File): Promise<boolean> => {
      return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = (e) => {
          try {
            const content = e.target?.result as string;
            const importData = JSON.parse(content);
            
            // Валидация структуры данных
            if (!importData.chats || !Array.isArray(importData.chats)) {
              throw new Error('Неверный формат файла: отсутствует массив чатов');
            }
            
            // Восстанавливаем чаты и папки
            dispatch({
              type: 'RESTORE_CHATS',
              payload: {
                chats: importData.chats,
                currentChatId: importData.currentChatId || null,
                folders: importData.folders || [],
                unloadInactive: false,
              },
            });
            
            resolve(true);
          } catch (error) {
            reject(error);
          }
        };
        reader.onerror = () => reject(new Error('Ошибка чтения файла'));
        reader.readAsText(file);
      });
    },
    
    archiveAllChats: () => {
      const ids = state.chats.filter((c) => !c.isArchived).map((c) => c.id);
      dispatch({ type: 'ARCHIVE_ALL_CHATS' });
      const effectiveToken = token || localStorage.getItem('auth_token');
      if (effectiveToken && ids.length) {
        initSettings()
          .then(() =>
            Promise.all(
              ids.map((chatId) =>
                fetch(getApiUrl(`/api/conversations/${chatId}/archive`), {
                  method: 'POST',
                  headers: { Authorization: `Bearer ${effectiveToken}` },
                }).catch(() => {}),
              ),
            ),
          )
          .catch(() => {});
      }
    },
    
    archiveChat: (chatId: string) => {
      dispatch({ type: 'ARCHIVE_CHAT', payload: chatId });
      const effectiveToken = token || localStorage.getItem('auth_token');
      if (effectiveToken) {
        initSettings()
          .then(() =>
            fetch(getApiUrl(`/api/conversations/${chatId}/archive`), {
              method: 'POST',
              headers: { Authorization: `Bearer ${effectiveToken}` },
            }),
          )
          .catch(() => {});
      }
    },
    
    archiveFolder: (folderId: string) => {
      const folder = state.folders.find((f) => f.id === folderId);
      const chatIds = folder?.chatIds ? [...folder.chatIds] : [];
      dispatch({ type: 'ARCHIVE_FOLDER', payload: folderId });
      const effectiveToken = token || localStorage.getItem('auth_token');
      if (effectiveToken && chatIds.length) {
        initSettings()
          .then(() =>
            Promise.all(
              chatIds.map((chatId) =>
                fetch(getApiUrl(`/api/conversations/${chatId}/archive`), {
                  method: 'POST',
                  headers: { Authorization: `Bearer ${effectiveToken}` },
                }).catch(() => {}),
              ),
            ),
          )
          .catch(() => {});
      }
    },
    
    unarchiveChat: (chatId: string) => {
      dispatch({ type: 'UNARCHIVE_CHAT', payload: chatId });
      const effectiveToken = token || localStorage.getItem('auth_token');
      if (effectiveToken) {
        initSettings()
          .then(() =>
            fetch(getApiUrl(`/api/conversations/${chatId}/unarchive`), {
              method: 'POST',
              headers: { Authorization: `Bearer ${effectiveToken}` },
            }),
          )
          .catch(() => {});
      }
    },
    
    unarchiveAllChats: () => {
      const ids = state.chats.filter((c) => c.isArchived).map((c) => c.id);
      dispatch({ type: 'UNARCHIVE_ALL_CHATS' });
      const effectiveToken = token || localStorage.getItem('auth_token');
      if (effectiveToken && ids.length) {
        initSettings()
          .then(() =>
            Promise.all(
              ids.map((chatId) =>
                fetch(getApiUrl(`/api/conversations/${chatId}/unarchive`), {
                  method: 'POST',
                  headers: { Authorization: `Bearer ${effectiveToken}` },
                }).catch(() => {}),
              ),
            ),
          )
          .catch(() => {});
      }
    },
    
    getChatById: (chatId: string) => {
      return state.chats.find(chat => chat.id === chatId) || null;
    },
    
    getArchivedChats: () => {
      return state.chats.filter(chat => chat.isArchived === true);
    },
    
    // Функции для работы с проектами
    createProject: (projectData: Omit<Project, 'id' | 'createdAt' | 'updatedAt'>) => {
      const projectId = Date.now().toString() + Math.random().toString(36).substr(2, 9);
      const newProject: Project = {
        id: projectId,
        ...projectData,
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
      };
      dispatch({ type: 'CREATE_PROJECT', payload: newProject });
      const effectiveToken = token || localStorage.getItem('auth_token');
      if (effectiveToken) {
        initSettings()
          .then(() => syncProjectCreate(newProject))
          .catch((error) => {
            console.warn(`[projects] не удалось сохранить проект ${projectId} в БД:`, error);
          });
      }
      return projectId;
    },
    
    updateProject: (projectId: string, updates: Partial<Project>) => {
      dispatch({ type: 'UPDATE_PROJECT', payload: { projectId, updates } });
      const effectiveToken = token || localStorage.getItem('auth_token');
      if (effectiveToken) {
        initSettings()
          .then(() => syncProjectUpdate(projectId, updates))
          .catch((error) => {
            console.warn(`[projects] не удалось обновить проект ${projectId} в БД:`, error);
          });
      }
    },
    
    deleteProject: (projectId: string) => {
      dispatch({ type: 'DELETE_PROJECT', payload: projectId });
      // Бэкенд чистит RAG-документы проекта, файлы в MinIO/PVC и диалоги.
      // Без этого вызова документы остаются в БД навсегда и переиндексируются впустую.
      const effectiveToken = token || localStorage.getItem('auth_token');
      if (effectiveToken) {
        initSettings()
          .then(() => {
            fetch(getApiUrl(API_ENDPOINTS.PROJECT_DELETE(projectId)), {
              method: 'DELETE',
              headers: { Authorization: `Bearer ${effectiveToken}` },
            })
              .then((response) => {
                if (!response.ok) {
                  console.error(
                    `Не удалось удалить данные проекта ${projectId}: HTTP ${response.status}`,
                  );
                }
              })
              .catch((error) => {
                console.error(`Ошибка удаления данных проекта ${projectId}:`, error);
              });
          })
          .catch(() => {});
      }
    },
    
    getProjects: () => {
      return state.projects;
    },
    
    getProjectById: (projectId: string) => {
      return state.projects.find(project => project.id === projectId) || null;
    },
    
    moveChatToProject: (chatId: string, projectId: string | null) => {
      dispatch({ type: 'MOVE_CHAT_TO_PROJECT', payload: { chatId, projectId } });
    },
    
    togglePinInProject: (chatId: string) => {
      dispatch({ type: 'TOGGLE_PIN_IN_PROJECT', payload: { chatId } });
    },

    branchChatAtMessage: async (
      sourceChatId: string,
      messageId: string,
      options?: { multiLlmSlotIndex?: number },
    ): Promise<string | null> => {
      const sourceChat = state.chats.find((chat) => chat.id === sourceChatId);
      if (!sourceChat) return null;

      const messageIndex = sourceChat.messages.findIndex((message) => message.id === messageId);
      if (messageIndex < 0) return null;

      const branchMessage = sourceChat.messages[messageIndex];
      if (branchMessage.role !== 'assistant' || branchMessage.isStreaming) return null;

      const effectiveToken = token || localStorage.getItem('auth_token');
      if (effectiveToken) {
        try {
          await initSettings();
          const body: Record<string, unknown> = { message_id: messageId };
          if (options?.multiLlmSlotIndex !== undefined) {
            body.multi_llm_slot_index = options.multiLlmSlotIndex;
          }
          const response = await fetch(getApiUrl(`/api/conversations/${sourceChatId}/branch`), {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              Authorization: `Bearer ${effectiveToken}`,
            },
            body: JSON.stringify(body),
          });
          if (response.ok) {
            const payload = await response.json();
            const newChat = mapServerConversationToChat(payload.conversation);
            dispatch({
              type: 'CREATE_CHAT',
              payload: { ...newChat, hiddenFromSidebarUntilUserMessage: true },
            });
            if (sourceChat.projectId) {
              dispatch({
                type: 'MOVE_CHAT_TO_PROJECT',
                payload: { chatId: newChat.id, projectId: sourceChat.projectId },
              });
            }
            dispatch({ type: 'SET_CURRENT_CHAT', payload: newChat.id });
            return newChat.id;
          }
        } catch (error) {
          console.error('Ошибка создания ветки на сервере:', error);
        }
      }

      const branchedMessages = cloneMessagesForBranch(
        sourceChat.messages,
        messageIndex,
        options?.multiLlmSlotIndex,
      );
      const newChatId = Date.now().toString() + Math.random().toString(36).substr(2, 9);
      const now = new Date().toISOString();
      const newChat: Chat = {
        id: newChatId,
        title: buildBranchChatTitle(sourceChat.title),
        messages: branchedMessages,
        createdAt: now,
        updatedAt: now,
        projectId: sourceChat.projectId,
        hiddenFromSidebarUntilUserMessage: true,
      };
      dispatch({ type: 'CREATE_CHAT', payload: newChat });
      dispatch({ type: 'SET_CURRENT_CHAT', payload: newChatId });
      return newChatId;
    },
  };
}
