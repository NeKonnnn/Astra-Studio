/**
 * Что получает новый чат от предыдущего (кнопка «Новый чат» и горячая клавиша).
 *
 * По умолчанию новый чат чистый: без агента, Skills, инструментов MCP,
 * режима артефактов, общей библиотеки, Coding и генерации изображений/видео.
 * Настройка «Переносить настройки в новый чат» (Настройки → Чаты) переносит
 * всё это из текущего чата.
 *
 * Агент и общая библиотека хранятся глобально, Skills / MCP / артефакты /
 * Coding / image-gen — по chatId. Поэтому при сбросе снимаем глобальные,
 * а при переносе копируем поштучные на id нового чата.
 *
 * Локальный режим: всё в localStorage, без серверных API.
 */

import { clearActiveAgent } from './clearActiveAgent';
import { setKnowledgeRagEnabled } from './knowledgeRagStorage';
import { copyActiveSkills } from './skillSelectionStorage';
import { copyMcpToolIds } from '../mcp/selectionStorage';
import {
  disableChatArtifactsToolsForChat,
  getChatArtifactsToolsState,
  hasChatArtifactsOverride,
  setChatArtifactsToolMode,
} from './artifactsSelectionStorage';
import {
  isCodingModeEnabled,
  isCodingPlanModeEnabled,
  setCodingModeEnabled,
  setCodingPlanModeEnabled,
} from '../coding/selectionStorage';
import {
  disableAllGenerationModes,
  isImageGenerationModeEnabled,
  isVideoGenerationModeEnabled,
  setImageGenerationModeEnabled,
  setVideoGenerationModeEnabled,
} from '../imageGeneration/selectionStorage';

export const KEEP_CHAT_SETTINGS_KEY = 'new_chat_keep_settings';

export function isKeepChatSettingsEnabled(): boolean {
  try {
    return localStorage.getItem(KEEP_CHAT_SETTINGS_KEY) === 'true';
  } catch {
    return false;
  }
}

export function setKeepChatSettingsEnabled(enabled: boolean): void {
  try {
    localStorage.setItem(KEEP_CHAT_SETTINGS_KEY, String(enabled));
  } catch {
    /* ignore */
  }
}

/** Режим артефактов (Инструменты → Артефакты), если в чате его меняли руками. */
function copyArtifactsSettings(fromChatId: string, toChatId: string): void {
  if (!hasChatArtifactsOverride(fromChatId)) return;
  const s = getChatArtifactsToolsState(fromChatId);
  if (s.shadcn_enabled) setChatArtifactsToolMode(toChatId, 'shadcn_enabled', true);
  else if (s.user_prompt_mode) setChatArtifactsToolMode(toChatId, 'user_prompt_mode', true);
  else disableChatArtifactsToolsForChat(toChatId);
}

function copyCodingSettings(fromChatId: string, toChatId: string): void {
  setCodingModeEnabled(toChatId, isCodingModeEnabled(fromChatId));
  setCodingPlanModeEnabled(toChatId, isCodingPlanModeEnabled(fromChatId));
}

function copyGenerationSettings(fromChatId: string, toChatId: string): void {
  setImageGenerationModeEnabled(toChatId, isImageGenerationModeEnabled(fromChatId));
  setVideoGenerationModeEnabled(toChatId, isVideoGenerationModeEnabled(fromChatId));
}

function clearLocalChatModes(chatId: string): void {
  setCodingModeEnabled(chatId, false);
  setCodingPlanModeEnabled(chatId, false);
  disableAllGenerationModes(chatId);
}

/**
 * Вызывать сразу после createChat(), до setCurrentChat(newChatId):
 * эффекты чата (MCP агента и т.п.) срабатывают на смену текущего чата и
 * должны увидеть уже итоговое состояние.
 */
export function prepareNewChatSettings(prevChatId: string | null | undefined, newChatId: string): void {
  if (isKeepChatSettingsEnabled()) {
    if (prevChatId && prevChatId !== newChatId) {
      copyActiveSkills(prevChatId, newChatId);
      copyMcpToolIds(prevChatId, newChatId);
      copyArtifactsSettings(prevChatId, newChatId);
      copyCodingSettings(prevChatId, newChatId);
      copyGenerationSettings(prevChatId, newChatId);
    }
    return;
  }
  clearActiveAgent();
  setKnowledgeRagEnabled(false);
  clearLocalChatModes(newChatId);
}
