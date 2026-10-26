/** События для действий, требующих UI вне глобального обработчика (фокус поиска, вложения). */
export const ASTRA_FOCUS_CHAT_SEARCH = 'astrachat:focus-chat-search';
export const ASTRA_PREFILL_CHAT_INPUT = 'astrachatPrefillChatInput';
export const ASTRA_TRIGGER_ATTACH = 'astrachat:trigger-attach';
export const ASTRA_REQUEST_DELETE_CURRENT_CHAT = 'astrachat:request-delete-current-chat';
export const ASTRA_OPEN_SETTINGS = 'astrachat:open-settings';
export const ASTRA_OPEN_SETTINGS_SECTION = 'astrachat:open-settings-section';
export const ASTRA_OPEN_AGENT_CONSTRUCTOR = 'astrachat:open-agent-constructor';
export const ASTRA_OPEN_TRANSCRIPTION_SIDEBAR = 'astrachat:open-transcription-sidebar';
export const ASTRA_OPEN_SKILLS_SIDEBAR = 'astrachat:open-skills-sidebar';
export const ASTRA_OPEN_KNOWLEDGE_BASES_SIDEBAR = 'astrachat:open-knowledge-bases-sidebar';
/** Закрыть popover «Инструменты» в чате (после перехода в настройки сущности). */
export const ASTRA_CLOSE_CHAT_GEAR = 'astrachat:close-chat-gear';

/** sessionStorage: id агента, которого нужно открыть в конструкторе после перехода в чат */
export const ASTRA_OPEN_AGENT_CONSTRUCTOR_ID_KEY = 'astrachat_open_agent_constructor_id';
/** sessionStorage: id skill для открытия в панели Skills после перехода в чат */
export const ASTRA_OPEN_SKILLS_SIDEBAR_ID_KEY = 'astrachat_open_skills_sidebar_id';
/** sessionStorage: id базы знаний для открытия в панели БЗ после перехода в чат */
export const ASTRA_OPEN_KNOWLEDGE_BASES_SIDEBAR_ID_KEY =
  'astrachat_open_knowledge_bases_sidebar_id';

export const ASTRA_HOTKEYS_CHANGED = 'astrachat:hotkeys-changed';

export type HotkeyActionId =
  | 'newChat'
  | 'searchChats'
  | 'attachFiles'
  | 'deleteChat'
  | 'openSettings'
  | 'openAgentConstructor'
  | 'openTranscription';

export interface HotkeyBinding {
  /** KeyboardEvent.code — физическая клавиша, не зависит от раскладки. */
  code: string;
  /** Ctrl (Win/Linux) или ⌘ (Mac). */
  mod?: boolean;
  shift?: boolean;
  alt?: boolean;
}

export type HotkeyCategoryId = 'editor' | 'application';

export interface HotkeyActionMeta {
  id: HotkeyActionId;
  label: string;
  category: HotkeyCategoryId;
  /** Срабатывает даже когда фокус в поле ввода. */
  worksInInput: boolean;
}

export const HOTKEY_CATEGORY_LABELS: Record<HotkeyCategoryId, string> = {
  editor: 'Редактор сообщений',
  application: 'Приложение',
};

export const HOTKEY_CATEGORY_ORDER: HotkeyCategoryId[] = ['editor', 'application'];

export const HOTKEY_ACTIONS: HotkeyActionMeta[] = [
  { id: 'attachFiles', label: 'Прикрепить файлы', category: 'editor', worksInInput: true },
  { id: 'newChat', label: 'Открыть новый чат', category: 'application', worksInInput: false },
  { id: 'searchChats', label: 'Поиск в чатах', category: 'application', worksInInput: false },
  { id: 'deleteChat', label: 'Удалить текущий чат', category: 'application', worksInInput: false },
  { id: 'openSettings', label: 'Окно настроек', category: 'application', worksInInput: false },
  { id: 'openAgentConstructor', label: 'Конструктор агента', category: 'application', worksInInput: false },
  { id: 'openTranscription', label: 'Транскрибатор', category: 'application', worksInInput: false },
];

export const DEFAULT_HOTKEY_BINDINGS: Record<HotkeyActionId, HotkeyBinding> = {
  newChat: { code: 'KeyK', mod: true, shift: true },
  searchChats: { code: 'KeyO', mod: true },
  attachFiles: { code: 'KeyU', mod: true },
  deleteChat: { code: 'Delete', mod: true },
  openSettings: { code: 'KeyS', alt: true },
  openAgentConstructor: { code: 'KeyA', alt: true },
  openTranscription: { code: 'KeyT', alt: true },
};

const HOTKEY_BINDINGS_STORAGE_KEY = 'astra_hotkey_bindings';
const HOTKEY_DISABLED_STORAGE_KEY = 'astra_hotkey_disabled';

const MODIFIER_KEYS = new Set(['Shift', 'Control', 'Alt', 'Meta']);

export function isPrimaryModifier(e: { ctrlKey: boolean; metaKey: boolean }): boolean {
  return e.ctrlKey || e.metaKey;
}

/** Поля ввода: для этих целей не перехватываем Shift+K / O / Delete (кроме отдельных исключений). */
export function isTypingInField(target: EventTarget | null): boolean {
  if (!target || !(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
  if (target.isContentEditable) return true;
  return false;
}

export function primaryModifierLabel(): string {
  if (typeof navigator !== 'undefined' && /Mac|iPhone|iPad|iPod/i.test(navigator.userAgent)) {
    return '⌘';
  }
  return 'Ctrl';
}

function isValidBinding(binding: unknown): binding is HotkeyBinding {
  if (!binding || typeof binding !== 'object') return false;
  const b = binding as HotkeyBinding;
  return typeof b.code === 'string' && b.code.length > 0;
}

function normalizeStoredBindings(raw: unknown): Partial<Record<HotkeyActionId, HotkeyBinding>> {
  if (!raw || typeof raw !== 'object') return {};
  const result: Partial<Record<HotkeyActionId, HotkeyBinding>> = {};
  for (const action of HOTKEY_ACTIONS) {
    const value = (raw as Record<string, unknown>)[action.id];
    if (isValidBinding(value)) {
      result[action.id] = {
        code: value.code,
        mod: !!value.mod,
        shift: !!value.shift,
        alt: !!value.alt,
      };
    }
  }
  return result;
}

export function getHotkeyBindings(): Record<HotkeyActionId, HotkeyBinding> {
  if (typeof window === 'undefined') {
    return { ...DEFAULT_HOTKEY_BINDINGS };
  }
  try {
    const raw = localStorage.getItem(HOTKEY_BINDINGS_STORAGE_KEY);
    if (!raw) return { ...DEFAULT_HOTKEY_BINDINGS };
    const stored = normalizeStoredBindings(JSON.parse(raw));
    return { ...DEFAULT_HOTKEY_BINDINGS, ...stored };
  } catch {
    return { ...DEFAULT_HOTKEY_BINDINGS };
  }
}

export function saveHotkeyBinding(actionId: HotkeyActionId, binding: HotkeyBinding): void {
  const current = getHotkeyBindings();
  current[actionId] = binding;
  const toStore: Partial<Record<HotkeyActionId, HotkeyBinding>> = {};
  for (const action of HOTKEY_ACTIONS) {
    const value = current[action.id];
    const def = DEFAULT_HOTKEY_BINDINGS[action.id];
    if (
      value.code !== def.code ||
      !!value.mod !== !!def.mod ||
      !!value.shift !== !!def.shift ||
      !!value.alt !== !!def.alt
    ) {
      toStore[action.id] = value;
    }
  }
  if (Object.keys(toStore).length === 0) {
    localStorage.removeItem(HOTKEY_BINDINGS_STORAGE_KEY);
  } else {
    localStorage.setItem(HOTKEY_BINDINGS_STORAGE_KEY, JSON.stringify(toStore));
  }
  window.dispatchEvent(new Event(ASTRA_HOTKEYS_CHANGED));
}

export function resetHotkeyBinding(actionId: HotkeyActionId): void {
  saveHotkeyBinding(actionId, DEFAULT_HOTKEY_BINDINGS[actionId]);
}

export function resetAllHotkeyBindings(): void {
  localStorage.removeItem(HOTKEY_BINDINGS_STORAGE_KEY);
  localStorage.removeItem(HOTKEY_DISABLED_STORAGE_KEY);
  window.dispatchEvent(new Event(ASTRA_HOTKEYS_CHANGED));
}

export function getDisabledHotkeys(): Set<HotkeyActionId> {
  if (typeof window === 'undefined') return new Set();
  try {
    const raw = localStorage.getItem(HOTKEY_DISABLED_STORAGE_KEY);
    if (!raw) return new Set();
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return new Set();
    const valid = new Set(HOTKEY_ACTIONS.map((a) => a.id));
    return new Set(parsed.filter((id): id is HotkeyActionId => typeof id === 'string' && valid.has(id as HotkeyActionId)));
  } catch {
    return new Set();
  }
}

export function isHotkeyEnabled(actionId: HotkeyActionId): boolean {
  return !getDisabledHotkeys().has(actionId);
}

export function setHotkeyEnabled(actionId: HotkeyActionId, enabled: boolean): void {
  const disabled = getDisabledHotkeys();
  if (enabled) {
    disabled.delete(actionId);
  } else {
    disabled.add(actionId);
  }
  if (disabled.size === 0) {
    localStorage.removeItem(HOTKEY_DISABLED_STORAGE_KEY);
  } else {
    localStorage.setItem(HOTKEY_DISABLED_STORAGE_KEY, JSON.stringify(Array.from(disabled)));
  }
  window.dispatchEvent(new Event(ASTRA_HOTKEYS_CHANGED));
}

export function formatKeyCode(code: string): string {
  if (code.startsWith('Key')) return code.slice(3);
  if (code.startsWith('Digit')) return code.slice(5);
  if (code.startsWith('Numpad')) return `Num${code.slice(6)}`;
  const named: Record<string, string> = {
    Delete: 'Del',
    Backspace: 'Backspace',
    Enter: 'Enter',
    Escape: 'Esc',
    Tab: 'Tab',
    Space: 'Space',
    ArrowUp: '↑',
    ArrowDown: '↓',
    ArrowLeft: '←',
    ArrowRight: '→',
  };
  return named[code] ?? code;
}

export function formatHotkeyBinding(binding: HotkeyBinding): string {
  const parts: string[] = [];
  if (binding.mod) parts.push(primaryModifierLabel());
  if (binding.alt) parts.push('Alt');
  if (binding.shift) parts.push('Shift');
  parts.push(formatKeyCode(binding.code));
  return parts.join(' + ');
}

export function bindingsEqual(a: HotkeyBinding, b: HotkeyBinding): boolean {
  return (
    a.code === b.code &&
    !!a.mod === !!b.mod &&
    !!a.shift === !!b.shift &&
    !!a.alt === !!b.alt
  );
}

export function findHotkeyConflict(
  actionId: HotkeyActionId,
  binding: HotkeyBinding,
  bindings: Record<HotkeyActionId, HotkeyBinding> = getHotkeyBindings(),
): HotkeyActionId | null {
  for (const action of HOTKEY_ACTIONS) {
    if (action.id === actionId) continue;
    if (bindingsEqual(bindings[action.id], binding)) return action.id;
  }
  return null;
}

export function matchesHotkeyBinding(e: KeyboardEvent, binding: HotkeyBinding): boolean {
  if (e.code !== binding.code) return false;
  if (!!binding.mod !== isPrimaryModifier(e)) return false;
  if (!!binding.shift !== e.shiftKey) return false;
  if (!!binding.alt !== e.altKey) return false;
  if (!binding.mod && isPrimaryModifier(e)) return false;
  if (!binding.alt && e.altKey) return false;
  if (!binding.shift && e.shiftKey) return false;
  return true;
}

export function bindingFromKeyboardEvent(e: KeyboardEvent): HotkeyBinding | null {
  if (MODIFIER_KEYS.has(e.key)) return null;
  if (e.key === 'Process') return null;
  return {
    code: e.code,
    mod: isPrimaryModifier(e),
    shift: e.shiftKey,
    alt: e.altKey,
  };
}

export const hotkeyLabel = {
  newChat: () => formatHotkeyBinding(getHotkeyBindings().newChat),
  searchChats: () => formatHotkeyBinding(getHotkeyBindings().searchChats),
  attachFiles: () => formatHotkeyBinding(getHotkeyBindings().attachFiles),
  deleteChat: () => formatHotkeyBinding(getHotkeyBindings().deleteChat),
  openSettings: () => formatHotkeyBinding(getHotkeyBindings().openSettings),
  openAgentConstructor: () => formatHotkeyBinding(getHotkeyBindings().openAgentConstructor),
  openTranscription: () => formatHotkeyBinding(getHotkeyBindings().openTranscription),
} as const;
