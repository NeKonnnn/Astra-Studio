/**
 * @jest-environment node
 */
import { DEFAULT_HOTKEY_BINDINGS, getDisabledHotkeys, getHotkeyBindings } from '@/constants/hotkeys';
import {
  getDefaultSidebarPanelBackground,
  getSavedSidebarPanelColor,
  getSidebarPanelBackground,
  isAppDarkMode,
} from '@/constants/sidebarPanelColor';

describe('SSR (нет window)', () => {
  it('hotkeys: привязки по умолчанию, пустой набор отключённых', () => {
    expect(typeof window).toBe('undefined');
    expect(getHotkeyBindings()).toEqual(DEFAULT_HOTKEY_BINDINGS);
    expect(getHotkeyBindings()).not.toBe(DEFAULT_HOTKEY_BINDINGS);
    expect(getDisabledHotkeys().size).toBe(0);
  });

  it('sidebarPanelColor: светлая тема по умолчанию, цвет не сохранён', () => {
    expect(isAppDarkMode()).toBe(false);
    expect(getSavedSidebarPanelColor()).toBe('');
    expect(getSidebarPanelBackground()).toBe(getDefaultSidebarPanelBackground(false));
    expect(getSidebarPanelBackground(true)).toBe(getDefaultSidebarPanelBackground(true));
  });
});
