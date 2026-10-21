// Конфигурация API для astrachat Frontend
// Использует новый модуль settings
import axios from 'axios';
import { getSettings } from '../settings';
import {
  mapApiPolicyToConfig,
  type LoginLockoutConfig,
} from '../settings/loginLockout';
import {
  mapApiSessionPolicyToConfig,
  type SessionTimeoutConfig,
} from '../settings/sessionTimeout';

// API эндпоинты
export const API_ENDPOINTS = {
  // Чат
  CHAT: '/api/chat',
  
  // Голос
  VOICE_RECOGNIZE: '/api/voice/recognize',
  VOICE_SYNTHESIZE: '/api/voice/synthesize',
  VOICE_SETTINGS: '/api/voice/settings',
  VOICE_WS: '/ws/voice',
  VOICE_DICTATION_WS: '/ws/dictation',
  
  // Транскрибация
  TRANSCRIBE_UPLOAD: '/api/transcribe/upload',
  TRANSCRIBE_YOUTUBE: '/api/transcribe/youtube',
  TRANSCRIPTION_SETTINGS: '/api/transcription/settings',
  
  // Документы
  DOCUMENTS_UPLOAD: '/api/documents/upload',
  /** Прикрепление к сообщению: MinIO + inline без RAG */
  DOCUMENTS_ATTACH: '/api/documents/attach',
  DOCUMENTS_QUERY: '/api/documents/query',
  DOCUMENTS_LIST: '/api/documents',
  DOCUMENTS_DELETE: '/api/documents',

  // База Знаний (Knowledge Base RAG)
  KB_DOCUMENTS_UPLOAD: '/api/kb/documents',
  KB_DOCUMENTS_LIST: '/api/kb/documents',
  KB_DOCUMENTS_DELETE: '/api/kb/documents',

  MEMORY_RAG_UPLOAD: '/api/memory-rag/documents',
  MEMORY_RAG_LIST: '/api/memory-rag/documents',
  MEMORY_RAG_DELETE: '/api/memory-rag/documents',

  // RAG проектов (файлы, привязанные к конкретному проекту)
  PROJECT_RAG_UPLOAD: (projectId: string) =>
    `/api/project-rag/projects/${encodeURIComponent(projectId)}/documents`,
  PROJECT_RAG_LIST: (projectId: string) =>
    `/api/project-rag/projects/${encodeURIComponent(projectId)}/documents`,
  PROJECT_RAG_DELETE_DOC: (projectId: string, documentId: number) =>
    `/api/project-rag/projects/${encodeURIComponent(projectId)}/documents/${encodeURIComponent(String(documentId))}`,
  PROJECT_RAG_SEARCH: (projectId: string) =>
    `/api/project-rag/projects/${encodeURIComponent(projectId)}/search`,

  // Управление проектами
  PROJECTS: '/api/projects',
  PROJECT_ITEM: (projectId: string) => `/api/projects/${encodeURIComponent(projectId)}`,
  PROJECT_DELETE: (projectId: string) => `/api/projects/${encodeURIComponent(projectId)}`,
  
  // Модели
  MODELS: '/api/models',
  MODELS_CURRENT: '/api/models/current',
  MODELS_SETTINGS: '/api/models/settings',
  MODELS_LOAD: '/api/models/load',
  
  // История
  HISTORY: '/api/history',
  
  // Сообщения
  UPDATE_MESSAGE: '/api/messages',
  MESSAGE_FEEDBACK: '/api/messages',

  // Аутентификация (политики — источник правды на backend)
  AUTH_LOGIN_LOCKOUT_POLICY: '/api/auth/login-lockout-policy',
  AUTH_SESSION_POLICY: '/api/auth/session-policy',
  AUTH_SERVER_INSTANCE: '/api/auth/server-instance',
  AUTH_SSO_PROVIDERS: '/api/auth/sso/providers',
  AUTH_SSO_KEYCLOAK_LOGIN: '/api/auth/sso/keycloak/login',
  AUTH_SSO_KEYCLOAK_EXCHANGE: '/api/auth/sso/keycloak/exchange',
  AUTH_ME_AVATAR: '/api/auth/me/avatar',

  // MCP (Model Context Protocol)
  MCP_SERVERS: '/api/mcp/servers',
  MCP_STATUS: '/api/mcp/status',
  MCP_TOOLS: '/api/mcp/tools',
  MCP_AGENT_STATUS: '/api/agent/mcp/status',
  MCP_SERVER_STATUS: (id: string) => `/api/mcp/servers/${encodeURIComponent(id)}/status`,
  MCP_SERVER_HEALTH: (id: string) => `/api/mcp/servers/${encodeURIComponent(id)}/health`,
  MCP_SERVER_TOOLS: (id: string) => `/api/mcp/servers/${encodeURIComponent(id)}/tools`,
  MCP_SERVER_VERIFY: (id: string) => `/api/mcp/servers/${encodeURIComponent(id)}/verify`,
  MCP_SERVER_CREDENTIALS: (id: string) => `/api/mcp/servers/${encodeURIComponent(id)}/credentials`,
  MCP_ATLASSIAN_CONFIG: '/api/mcp/servers/atlassian/config',
  MCP_ATLASSIAN_CREDENTIALS: '/api/mcp/servers/atlassian/credentials',

  // Skills (markdown instructions)
  SKILLS: '/api/skills',

  // Knowledge Bases (named RAG collections)
  KNOWLEDGE_BASES: '/api/knowledge-bases',

  // Plugins (HTTP microservices gallery)
  PLUGINS: '/api/plugins',
};

// Для обратной совместимости (deprecated - используйте getSettings())
export const API_CONFIG = {
  get BASE_URL(): string {
    const settings = getSettings();
    return settings.api.baseUrl;
  },
  
  get WS_URL(): string {
    const settings = getSettings();
    return settings.websocket.baseUrl;
  },
  
  ENDPOINTS: API_ENDPOINTS,
};

// Функция для получения полного URL API
export const getApiUrl = (endpoint: string): string => {
  const settings = getSettings();
  return settings.api.getApiUrl(endpoint);
};

/** Заголовки Authorization для fetch к защищённым API. */
export function getAuthFetchHeaders(extra?: Record<string, string>): Record<string, string> {
  const token =
    typeof window !== 'undefined'
      ? localStorage.getItem('auth_token')
      : null;
  return {
    ...(extra || {}),
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

// Функция для получения WebSocket URL
export const getWsUrl = (endpoint: string): string => {
  const settings = getSettings();
  return settings.websocket.getWsUrl(endpoint);
};

/**
 * WebSocket URL с JWT-токеном в query (?token=) — для защищённых WS-роутов,
 * где заголовки авторизации недоступны (браузерный WebSocket их не передаёт).
 */
export const getAuthWsUrl = (endpoint: string): string => {
  const base = getWsUrl(endpoint);
  const token =
    typeof window !== 'undefined'
      ? localStorage.getItem('auth_token')
      : null;
  if (!token) return base;
  const sep = base.includes('?') ? '&' : '?';
  return `${base}${sep}token=${encodeURIComponent(token)}`;
};

/** Политика блокировки входа с backend (без дублирования в поде frontend). */
export async function fetchLoginLockoutPolicy(): Promise<LoginLockoutConfig> {
  const { data } = await axios.get(getApiUrl(API_ENDPOINTS.AUTH_LOGIN_LOCKOUT_POLICY));
  return mapApiPolicyToConfig(data);
}

/** Политика автовыхода при неактивности с backend. */
export async function fetchSessionPolicy(): Promise<SessionTimeoutConfig> {
  const { data } = await axios.get(getApiUrl(API_ENDPOINTS.AUTH_SESSION_POLICY));
  return mapApiSessionPolicyToConfig(data);
}
