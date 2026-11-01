import {
  ASTRA_HOTKEYS_CHANGED,
  DEFAULT_HOTKEY_BINDINGS,
  HOTKEY_ACTIONS,
  HOTKEY_CATEGORY_ORDER,
  HOTKEY_CATEGORY_LABELS,
  bindingFromKeyboardEvent,
  bindingsEqual,
  findHotkeyConflict,
  formatHotkeyBinding,
  formatKeyCode,
  getDisabledHotkeys,
  getHotkeyBindings,
  hotkeyLabel,
  isHotkeyEnabled,
  isPrimaryModifier,
  isTypingInField,
  matchesHotkeyBinding,
  primaryModifierLabel,
  resetAllHotkeyBindings,
  resetHotkeyBinding,
  saveHotkeyBinding,
  setHotkeyEnabled,
  type HotkeyBinding,
} from '@/constants/hotkeys';

const BINDINGS_KEY = 'astra_hotkey_bindings';
const DISABLED_KEY = 'astra_hotkey_disabled';

const key = (init: KeyboardEventInit & { code: string }) => new KeyboardEvent('keydown', init);
const setUserAgent = (ua: string) =>
  Object.defineProperty(window.navigator, 'userAgent', { value: ua, configurable: true });

const ORIGINAL_UA = navigator.userAgent;

beforeEach(() => {
  localStorage.clear();
  setUserAgent('Mozilla/5.0 (X11; Linux x86_64)');
});

afterAll(() => setUserAgent(ORIGINAL_UA));

describe('defaults and metadata', () => {
  it('every action has a default binding and metadata', () => {
    for (const action of HOTKEY_ACTIONS) {
      expect(DEFAULT_HOTKEY_BINDINGS[action.id].code).toBeTruthy();
      expect(HOTKEY_CATEGORY_ORDER).toContain(action.category);
      expect(HOTKEY_CATEGORY_LABELS[action.category]).toBeTruthy();
    }
    expect(new Set(HOTKEY_ACTIONS.map((a) => a.id)).size).toBe(HOTKEY_ACTIONS.length);
  });

  it('default bindings do not conflict with each other', () => {
    for (const action of HOTKEY_ACTIONS) {
      expect(
        findHotkeyConflict(action.id, DEFAULT_HOTKEY_BINDINGS[action.id], DEFAULT_HOTKEY_BINDINGS),
      ).toBeNull();
    }
  });

  it('only attaching files works while typing', () => {
    expect(HOTKEY_ACTIONS.filter((a) => a.worksInInput).map((a) => a.id)).toEqual(['attachFiles']);
  });
});

describe('modifier helpers', () => {
  it('isPrimaryModifier accepts ctrl or meta', () => {
    expect(isPrimaryModifier({ ctrlKey: true, metaKey: false })).toBe(true);
    expect(isPrimaryModifier({ ctrlKey: false, metaKey: true })).toBe(true);
    expect(isPrimaryModifier({ ctrlKey: false, metaKey: false })).toBe(false);
  });

  it('primaryModifierLabel depends on the platform', () => {
    setUserAgent('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15)');
    expect(primaryModifierLabel()).toBe('⌘');
    setUserAgent('Mozilla/5.0 (iPhone; CPU iPhone OS 17_0)');
    expect(primaryModifierLabel()).toBe('⌘');
    setUserAgent('Mozilla/5.0 (X11; Linux x86_64)');
    expect(primaryModifierLabel()).toBe('Ctrl');
  });

  it('isTypingInField recognises inputs, textareas, selects and contenteditable', () => {
    expect(isTypingInField(null)).toBe(false);
    expect(isTypingInField(document.createElement('div'))).toBe(false);
    expect(isTypingInField(document.createElement('input'))).toBe(true);
    expect(isTypingInField(document.createElement('textarea'))).toBe(true);
    expect(isTypingInField(document.createElement('select'))).toBe(true);
    const editable = document.createElement('div');
    Object.defineProperty(editable, 'isContentEditable', { value: true });
    expect(isTypingInField(editable)).toBe(true);
    expect(isTypingInField(window as unknown as EventTarget)).toBe(false);
  });
});

describe('stored bindings', () => {
  it('returns defaults when nothing is stored', () => {
    expect(getHotkeyBindings()).toEqual(DEFAULT_HOTKEY_BINDINGS);
  });

  it('returns a copy, not the shared defaults object', () => {
    const bindings = getHotkeyBindings();
    bindings.newChat = { code: 'KeyZ' };
    expect(DEFAULT_HOTKEY_BINDINGS.newChat.code).toBe('KeyK');
  });

  it('merges stored overrides over defaults and normalises flags', () => {
    localStorage.setItem(
      BINDINGS_KEY,
      JSON.stringify({ newChat: { code: 'KeyN', mod: 1 }, unknown: { code: 'KeyX' } }),
    );
    const bindings = getHotkeyBindings();
    expect(bindings.newChat).toEqual({ code: 'KeyN', mod: true, shift: false, alt: false });
    expect(bindings.searchChats).toEqual(DEFAULT_HOTKEY_BINDINGS.searchChats);
    expect((bindings as Record<string, unknown>).unknown).toBeUndefined();
  });

  it.each([
    ['not json', '{broken'],
    ['array', '[1,2]'],
    ['string', '"x"'],
    ['null', 'null'],
  ])('falls back to defaults for %s', (_name, raw) => {
    localStorage.setItem(BINDINGS_KEY, raw);
    expect(getHotkeyBindings()).toEqual(DEFAULT_HOTKEY_BINDINGS);
  });

  it('ignores invalid entries individually', () => {
    localStorage.setItem(
      BINDINGS_KEY,
      JSON.stringify({ newChat: { code: '' }, searchChats: 'KeyQ', attachFiles: { code: 'KeyP', mod: true } }),
    );
    const bindings = getHotkeyBindings();
    expect(bindings.newChat).toEqual(DEFAULT_HOTKEY_BINDINGS.newChat);
    expect(bindings.searchChats).toEqual(DEFAULT_HOTKEY_BINDINGS.searchChats);
    expect(bindings.attachFiles.code).toBe('KeyP');
  });
});

describe('saving bindings', () => {
  it('stores only differences from the defaults and notifies listeners', () => {
    const listener = jest.fn();
    window.addEventListener(ASTRA_HOTKEYS_CHANGED, listener);
    saveHotkeyBinding('openSettings', { code: 'KeyP', alt: true });
    window.removeEventListener(ASTRA_HOTKEYS_CHANGED, listener);

    expect(JSON.parse(localStorage.getItem(BINDINGS_KEY) as string)).toEqual({
      openSettings: { code: 'KeyP', alt: true },
    });
    expect(listener).toHaveBeenCalledTimes(1);
    expect(getHotkeyBindings().openSettings.code).toBe('KeyP');
  });

  it('removes the storage key when everything is back to defaults', () => {
    saveHotkeyBinding('openSettings', { code: 'KeyP', alt: true });
    saveHotkeyBinding('openSettings', { ...DEFAULT_HOTKEY_BINDINGS.openSettings });
    expect(localStorage.getItem(BINDINGS_KEY)).toBeNull();
  });

  it('keeps other overrides when one is changed', () => {
    saveHotkeyBinding('openSettings', { code: 'KeyP', alt: true });
    saveHotkeyBinding('newChat', { code: 'KeyN', mod: true });
    expect(Object.keys(JSON.parse(localStorage.getItem(BINDINGS_KEY) as string)).sort()).toEqual([
      'newChat',
      'openSettings',
    ]);
  });

  it('resetHotkeyBinding restores one action only', () => {
    saveHotkeyBinding('openSettings', { code: 'KeyP', alt: true });
    saveHotkeyBinding('newChat', { code: 'KeyN', mod: true });
    resetHotkeyBinding('openSettings');
    expect(getHotkeyBindings().openSettings).toEqual(DEFAULT_HOTKEY_BINDINGS.openSettings);
    expect(getHotkeyBindings().newChat.code).toBe('KeyN');
  });

  it('resetAllHotkeyBindings clears bindings and disabled flags', () => {
    saveHotkeyBinding('newChat', { code: 'KeyN', mod: true });
    setHotkeyEnabled('searchChats', false);
    const listener = jest.fn();
    window.addEventListener(ASTRA_HOTKEYS_CHANGED, listener);
    resetAllHotkeyBindings();
    window.removeEventListener(ASTRA_HOTKEYS_CHANGED, listener);
    expect(localStorage.getItem(BINDINGS_KEY)).toBeNull();
    expect(localStorage.getItem(DISABLED_KEY)).toBeNull();
    expect(listener).toHaveBeenCalledTimes(1);
  });
});

describe('enabling and disabling', () => {
  it('all actions are enabled by default', () => {
    for (const a of HOTKEY_ACTIONS) expect(isHotkeyEnabled(a.id)).toBe(true);
    expect(getDisabledHotkeys().size).toBe(0);
  });

  it('disables and re-enables an action', () => {
    setHotkeyEnabled('deleteChat', false);
    expect(isHotkeyEnabled('deleteChat')).toBe(false);
    expect(isHotkeyEnabled('newChat')).toBe(true);
    setHotkeyEnabled('deleteChat', true);
    expect(isHotkeyEnabled('deleteChat')).toBe(true);
    expect(localStorage.getItem(DISABLED_KEY)).toBeNull();
  });

  it('disabling twice does not duplicate entries', () => {
    setHotkeyEnabled('deleteChat', false);
    setHotkeyEnabled('deleteChat', false);
    expect(JSON.parse(localStorage.getItem(DISABLED_KEY) as string)).toEqual(['deleteChat']);
  });

  it('drops unknown ids and survives corrupted storage', () => {
    localStorage.setItem(DISABLED_KEY, JSON.stringify(['newChat', 'bogus', 5, null]));
    expect(Array.from(getDisabledHotkeys())).toEqual(['newChat']);
    localStorage.setItem(DISABLED_KEY, '{broken');
    expect(getDisabledHotkeys().size).toBe(0);
    localStorage.setItem(DISABLED_KEY, '{"a":1}');
    expect(getDisabledHotkeys().size).toBe(0);
  });
});

describe('formatting', () => {
  it.each([
    ['KeyK', 'K'],
    ['Digit5', '5'],
    ['Numpad7', 'Num7'],
    ['Delete', 'Del'],
    ['Escape', 'Esc'],
    ['ArrowUp', '↑'],
    ['ArrowRight', '→'],
    ['F5', 'F5'],
    ['Backspace', 'Backspace'],
  ])('formatKeyCode(%s) = %s', (code, expected) => {
    expect(formatKeyCode(code)).toBe(expected);
  });

  it('formatHotkeyBinding joins modifiers in a stable order', () => {
    expect(formatHotkeyBinding({ code: 'KeyK', mod: true, shift: true })).toBe('Ctrl + Shift + K');
    expect(formatHotkeyBinding({ code: 'KeyS', alt: true })).toBe('Alt + S');
    expect(formatHotkeyBinding({ code: 'KeyX', mod: true, alt: true, shift: true })).toBe(
      'Ctrl + Alt + Shift + X',
    );
    expect(formatHotkeyBinding({ code: 'Delete' })).toBe('Del');
  });

  it('hotkeyLabel reflects custom bindings', () => {
    expect(hotkeyLabel.newChat()).toBe('Ctrl + Shift + K');
    saveHotkeyBinding('newChat', { code: 'KeyN', mod: true });
    expect(hotkeyLabel.newChat()).toBe('Ctrl + N');
  });
});

describe('equality and conflicts', () => {
  it('bindingsEqual treats missing flags as false', () => {
    expect(bindingsEqual({ code: 'KeyA' }, { code: 'KeyA', mod: false, shift: false, alt: false })).toBe(true);
    expect(bindingsEqual({ code: 'KeyA', mod: true }, { code: 'KeyA' })).toBe(false);
    expect(bindingsEqual({ code: 'KeyA' }, { code: 'KeyB' })).toBe(false);
  });

  it('finds the action already using the binding', () => {
    expect(findHotkeyConflict('openSettings', { ...DEFAULT_HOTKEY_BINDINGS.newChat })).toBe('newChat');
    expect(findHotkeyConflict('openSettings', { code: 'KeyZ', mod: true })).toBeNull();
  });

  it('an action never conflicts with itself', () => {
    expect(findHotkeyConflict('newChat', { ...DEFAULT_HOTKEY_BINDINGS.newChat })).toBeNull();
  });

  it('uses stored bindings by default', () => {
    saveHotkeyBinding('searchChats', { code: 'KeyQ', mod: true });
    expect(findHotkeyConflict('newChat', { code: 'KeyQ', mod: true })).toBe('searchChats');
  });
});

describe('matching keyboard events', () => {
  const ctrlShiftK: HotkeyBinding = { code: 'KeyK', mod: true, shift: true };

  it('matches with the exact modifier set', () => {
    expect(matchesHotkeyBinding(key({ code: 'KeyK', ctrlKey: true, shiftKey: true }), ctrlShiftK)).toBe(true);
    expect(matchesHotkeyBinding(key({ code: 'KeyK', metaKey: true, shiftKey: true }), ctrlShiftK)).toBe(true);
  });

  it('rejects other keys and missing or extra modifiers', () => {
    expect(matchesHotkeyBinding(key({ code: 'KeyL', ctrlKey: true, shiftKey: true }), ctrlShiftK)).toBe(false);
    expect(matchesHotkeyBinding(key({ code: 'KeyK', ctrlKey: true }), ctrlShiftK)).toBe(false);
    expect(
      matchesHotkeyBinding(key({ code: 'KeyK', ctrlKey: true, shiftKey: true, altKey: true }), ctrlShiftK),
    ).toBe(false);
    expect(matchesHotkeyBinding(key({ code: 'KeyK', shiftKey: true }), ctrlShiftK)).toBe(false);
  });

  it('a plain binding does not fire when a modifier is held', () => {
    const plain: HotkeyBinding = { code: 'KeyJ' };
    expect(matchesHotkeyBinding(key({ code: 'KeyJ' }), plain)).toBe(true);
    expect(matchesHotkeyBinding(key({ code: 'KeyJ', ctrlKey: true }), plain)).toBe(false);
    expect(matchesHotkeyBinding(key({ code: 'KeyJ', altKey: true }), plain)).toBe(false);
  });

  it('matches by physical key, independent of the layout', () => {
    expect(
      matchesHotkeyBinding(key({ code: 'KeyK', key: 'л', ctrlKey: true, shiftKey: true }), ctrlShiftK),
    ).toBe(true);
  });
});

describe('recording a binding from an event', () => {
  it.each(['Shift', 'Control', 'Alt', 'Meta', 'Process'])('ignores a bare %s press', (k) => {
    expect(bindingFromKeyboardEvent(key({ code: 'X', key: k }))).toBeNull();
  });

  it('captures code and modifier state', () => {
    expect(
      bindingFromKeyboardEvent(key({ code: 'KeyP', key: 'p', ctrlKey: true, altKey: true })),
    ).toEqual({ code: 'KeyP', mod: true, shift: false, alt: true });
  });

  it('a recorded binding matches the same event afterwards', () => {
    const event = key({ code: 'Delete', key: 'Delete', metaKey: true });
    const binding = bindingFromKeyboardEvent(event) as HotkeyBinding;
    expect(matchesHotkeyBinding(event, binding)).toBe(true);
  });
});
