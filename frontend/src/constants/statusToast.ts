/**
 * Варианты и стили тёмного toast-уведомления (success / warning / error).
 * Текст сообщения передаётся с места вызова; сюда вынесены общие цвета и типовые фразы.
 */

export type StatusToastType = 'success' | 'warning' | 'error' | 'info';

/** Фон рамки (как на скрине) */
export const STATUS_TOAST_BG = '#262626';

/** Цвет текста */
export const STATUS_TOAST_TEXT = '#FFFFFF';

/** Цвета иконок по типу */
export const STATUS_TOAST_ICON_COLORS: Record<StatusToastType, string> = {
  success: '#4CAF50',
  warning: '#FF9800',
  error: '#F44336',
  info: '#42A5F5',
};

/** Автоскрытие длительных плашек (перечанковка, LLM, ошибки в рабочей зоне) */
export const STATUS_TOAST_AUTO_DISMISS_MS = 10_000;

/** Короткое автоскрытие (копирование и т.п.) */
export const STATUS_TOAST_SHORT_DISMISS_MS = 2_500;

/** Типовые сообщения для переиспользования */
export const STATUS_TOAST_MESSAGES = {
  COPY_SUCCESS: 'Копирование в буфер обмена прошло успешно!',
  COPY_SUCCESS_SHORT: 'Скопировано',
  COPY_FAILED: 'Не удалось скопировать текст',
} as const;
