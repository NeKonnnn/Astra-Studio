/** Единый логгер голосового помощника: в DevTools фильтр `[VA]`. */

const TAG = '[VA]';

/** console.log — не info: в Chrome Info часто скрыт фильтром. */
export function vaLog(event: string, details?: Record<string, unknown>): void {
  if (details && Object.keys(details).length > 0) {
    console.log(TAG, event, details);
  } else {
    console.log(TAG, event);
  }
}

export function vaWarn(event: string, details?: Record<string, unknown>): void {
  if (details && Object.keys(details).length > 0) {
    console.warn(TAG, event, details);
  } else {
    console.warn(TAG, event);
  }
}
