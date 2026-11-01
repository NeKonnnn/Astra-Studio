import {
  DEFAULT_HOTKEY_BINDINGS,
  formatHotkeyBinding,
  hotkeyLabel,
  matchesHotkeyBinding,
  saveHotkeyBinding,
} from '@/constants/hotkeys';
import {
  getAgentConstructorSaveButtonSx,
  getProjectAvatarOutlineBorder,
  getProjectAvatarOutlineBox,
  getSidebarChatRowHoverBg,
} from '@/constants/menuStyles';
import { DISLIKE_FEEDBACK_TAGS } from '@/constants/messageFeedback';
import { MESSAGE_BUBBLE_GLASS_EFFECT_KEY } from '@/constants/messageBubbleGlass';
import { getSidebarPanelBackground, isSidebarPanelLight } from '@/constants/sidebarPanelColor';

describe('hotkeys: подписи и совпадение событий', () => {
  beforeEach(() => localStorage.clear());

  it('hotkeyLabel отдаёт форматированные привязки по умолчанию для каждого действия', () => {
    expect(hotkeyLabel.newChat()).toBe(formatHotkeyBinding(DEFAULT_HOTKEY_BINDINGS.newChat));
    expect(hotkeyLabel.searchChats()).toBe(formatHotkeyBinding(DEFAULT_HOTKEY_BINDINGS.searchChats));
    expect(hotkeyLabel.attachFiles()).toBe(formatHotkeyBinding(DEFAULT_HOTKEY_BINDINGS.attachFiles));
    expect(hotkeyLabel.deleteChat()).toBe(formatHotkeyBinding(DEFAULT_HOTKEY_BINDINGS.deleteChat));
    expect(hotkeyLabel.openSettings()).toBe(formatHotkeyBinding(DEFAULT_HOTKEY_BINDINGS.openSettings));
    expect(hotkeyLabel.openAgentConstructor()).toBe(
      formatHotkeyBinding(DEFAULT_HOTKEY_BINDINGS.openAgentConstructor),
    );
    expect(hotkeyLabel.openTranscription()).toBe(
      formatHotkeyBinding(DEFAULT_HOTKEY_BINDINGS.openTranscription),
    );
  });

  it('hotkeyLabel учитывает переназначенную пользователем привязку', () => {
    saveHotkeyBinding('newChat', { code: 'KeyJ', mod: true, shift: true });
    expect(hotkeyLabel.newChat()).toBe(formatHotkeyBinding({ code: 'KeyJ', mod: true, shift: true }));
    expect(hotkeyLabel.newChat()).not.toBe(formatHotkeyBinding(DEFAULT_HOTKEY_BINDINGS.newChat));
  });

  it('matchesHotkeyBinding: модификаторы должны совпадать точно', () => {
    const ev = (o: Partial<KeyboardEvent>) =>
      ({ code: 'KeyK', ctrlKey: false, metaKey: false, shiftKey: false, altKey: false, ...o } as KeyboardEvent);
    expect(matchesHotkeyBinding(ev({ ctrlKey: true }), { code: 'KeyK', mod: true })).toBe(true);
    expect(matchesHotkeyBinding(ev({}), { code: 'KeyK', mod: true })).toBe(false);
    expect(matchesHotkeyBinding(ev({ ctrlKey: true }), { code: 'KeyK' })).toBe(false);
    expect(matchesHotkeyBinding(ev({ shiftKey: true }), { code: 'KeyK' })).toBe(false);
    expect(matchesHotkeyBinding(ev({ altKey: true }), { code: 'KeyK' })).toBe(false);
    expect(matchesHotkeyBinding(ev({ code: 'KeyX' }), { code: 'KeyK' })).toBe(false);
    expect(matchesHotkeyBinding(ev({}), { code: 'KeyK' })).toBe(true);
  });
});

describe('menuStyles: помощники', () => {
  it('getProjectAvatarOutlineBorder зависит от размера', () => {
    expect(getProjectAvatarOutlineBorder(22)).toBe('2.25px');
    expect(getProjectAvatarOutlineBorder(23)).toBe('2.75px');
  });

  it('getProjectAvatarOutlineBox собирает контур нужного цвета и размера', () => {
    expect(getProjectAvatarOutlineBox(30, '#f00')).toEqual({
      width: '30px',
      height: '30px',
      bgcolor: 'transparent',
      border: '2.75px solid',
      borderColor: '#f00',
      color: '#f00',
      boxSizing: 'border-box',
    });
  });

  it('кнопка сохранения агента отличается для светлой и тёмной темы', () => {
    const light: any = getAgentConstructorSaveButtonSx(false);
    const dark: any = getAgentConstructorSaveButtonSx(true);
    expect(light.bgcolor).toBe('primary.main');
    expect(light['&:disabled'].color).toBe('action.disabled');
    expect(dark).not.toBe(light);
    expect(dark.bgcolor).not.toBe('primary.main');
  });

  it('фон строки чата при наведении', () => {
    expect(getSidebarChatRowHoverBg(true)).toBe('rgba(0,0,0,0.06)');
    expect(getSidebarChatRowHoverBg(false)).toBe('rgba(255,255,255,0.08)');
  });
});

describe('прочие константы', () => {
  it('теги дизлайка: уникальные id, есть «other», подписи не пустые', () => {
    const ids = DISLIKE_FEEDBACK_TAGS.map((t) => t.id);
    expect(new Set(ids).size).toBe(ids.length);
    expect(ids).toContain('other');
    expect(DISLIKE_FEEDBACK_TAGS.every((t) => t.label.trim().length > 0)).toBe(true);
  });

  it('ключ эффекта стекла', () => {
    expect(MESSAGE_BUBBLE_GLASS_EFFECT_KEY).toBe('message_bubble_glass_effect');
  });

  it('isSidebarPanelLight: именованные цвета без hex-образцов', () => {
    expect(isSidebarPanelLight('white')).toBe(true);
    expect(isSidebarPanelLight('rgba(255, 255, 255, 0.5)')).toBe(true);
    expect(isSidebarPanelLight('black')).toBe(false);
    expect(isSidebarPanelLight('#000')).toBe(false);
    expect(isSidebarPanelLight('#ffffff')).toBe(true);
  });

  it('getSidebarPanelBackground без сохранённого цвета следует теме', () => {
    localStorage.clear();
    expect(getSidebarPanelBackground(true)).not.toBe(getSidebarPanelBackground(false));
  });
});
