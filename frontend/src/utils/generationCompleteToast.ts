import { stripReasoningMarkers } from './reasoningSplit';

export const GENERATION_COMPLETE_TOAST_DISMISS_MS = 20_000;

const DEFAULT_PREVIEW_LEN = 140;

/** Начало ответа для тоста после завершения генерации. */
export function previewGenerationReply(raw: string, maxLen = DEFAULT_PREVIEW_LEN): string {
  const visible = stripReasoningMarkers(raw || '').replace(/\s+/g, ' ').trim();
  if (!visible) return 'Ответ готов';
  if (visible.length <= maxLen) return visible;
  return `${visible.slice(0, Math.max(1, maxLen - 1)).trimEnd()}…`;
}

/** Заголовок плашки: имя чата или запасной текст. */
export function generationCompleteToastTitle(chatTitle: string | null | undefined): string {
  const title = (chatTitle || '').trim();
  return title || 'Чат';
}

/** @deprecated используйте title + preview отдельно */
export function formatGenerationCompleteToast(
  chatTitle: string | null | undefined,
  rawReply: string,
  maxLen = DEFAULT_PREVIEW_LEN,
): string {
  const preview = previewGenerationReply(rawReply, maxLen);
  return `${generationCompleteToastTitle(chatTitle)}: ${preview}`;
}

/** Показывать плашку только если пользователь смотрит другой чат. */
export function shouldShowGenerationCompleteToast(
  completedChatId: string | null | undefined,
  viewingChatId: string | null | undefined,
): boolean {
  const done = (completedChatId || '').trim();
  if (!done) return false;
  const viewing = (viewingChatId || '').trim();
  return !viewing || viewing !== done;
}
