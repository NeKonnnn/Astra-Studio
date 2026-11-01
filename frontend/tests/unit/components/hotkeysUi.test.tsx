import '@testing-library/jest-dom';
import React from 'react';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import GlobalKeyboardShortcuts from '@/components/GlobalKeyboardShortcuts';
import HotkeyBindingCapture, { getDefaultHotkeyBinding } from '@/components/HotkeyBindingCapture';
import HotkeysSettingsDialog from '@/components/HotkeysSettingsDialog';
import {
  ASTRA_FOCUS_CHAT_SEARCH,
  ASTRA_HOTKEYS_CHANGED,
  ASTRA_OPEN_AGENT_CONSTRUCTOR,
  ASTRA_OPEN_SETTINGS,
  ASTRA_OPEN_TRANSCRIPTION_SIDEBAR,
  ASTRA_REQUEST_DELETE_CURRENT_CHAT,
  ASTRA_TRIGGER_ATTACH,
  DEFAULT_HOTKEY_BINDINGS,
  HOTKEY_ACTIONS,
  saveHotkeyBinding,
  setHotkeyEnabled,
} from '@/constants/hotkeys';

const mockNavigate = jest.fn();
jest.mock('react-router-dom', () => ({ useNavigate: () => mockNavigate }));

let mockState: any;
const mockActions = { createChat: jest.fn(), setCurrentChat: jest.fn(), deleteChat: jest.fn() };
jest.mock('@/contexts/AppContext', () => ({
  useAppContext: () => ({ state: mockState }),
  useAppActions: () => mockActions,
  chatIsListedInAllChatsSection: (c: any) => Boolean(c.listed),
}));

const mockPrepare = jest.fn();
jest.mock('@/utils/newChatSettings', () => ({
  prepareNewChatSettings: (...a: unknown[]) => mockPrepare(...a),
}));

const key = (init: KeyboardEventInit & { target?: Element }) => {
  const ev = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init });
  (init.target ?? window).dispatchEvent(ev);
  return ev;
};

beforeEach(() => {
  localStorage.clear();
  mockNavigate.mockReset();
  mockActions.createChat.mockReset().mockReturnValue('new-id');
  mockActions.setCurrentChat.mockReset();
  mockActions.deleteChat.mockReset();
  mockPrepare.mockReset();
  mockState = { chats: [], currentChatId: null, loadingChatIds: [] };
});

describe('GlobalKeyboardShortcuts', () => {
  const events: string[] = [];
  const names = [
    ASTRA_FOCUS_CHAT_SEARCH,
    ASTRA_TRIGGER_ATTACH,
    ASTRA_REQUEST_DELETE_CURRENT_CHAT,
    ASTRA_OPEN_SETTINGS,
    ASTRA_OPEN_AGENT_CONSTRUCTOR,
    ASTRA_OPEN_TRANSCRIPTION_SIDEBAR,
  ];
  const listener = (e: Event) => events.push(e.type);

  beforeEach(() => {
    events.length = 0;
    names.forEach((n) => window.addEventListener(n, listener));
  });
  afterEach(() => names.forEach((n) => window.removeEventListener(n, listener)));

  it('сочетания, генерирующие события приложения', () => {
    render(<GlobalKeyboardShortcuts />);
    expect(key({ code: 'KeyO', ctrlKey: true }).defaultPrevented).toBe(true);
    key({ code: 'KeyU', ctrlKey: true });
    key({ code: 'Delete', ctrlKey: true });
    key({ code: 'KeyS', altKey: true });
    key({ code: 'KeyA', altKey: true });
    key({ code: 'KeyT', altKey: true });
    expect(events).toEqual(names);
  });

  it('неизвестное сочетание не перехватывается', () => {
    render(<GlobalKeyboardShortcuts />);
    expect(key({ code: 'KeyQ', ctrlKey: true }).defaultPrevented).toBe(false);
    expect(events).toEqual([]);
  });

  it('в поле ввода работает только «прикрепить файлы»', () => {
    render(<GlobalKeyboardShortcuts />);
    const input = document.createElement('input');
    document.body.appendChild(input);
    expect(key({ code: 'KeyO', ctrlKey: true, target: input }).defaultPrevented).toBe(false);
    expect(key({ code: 'KeyU', ctrlKey: true, target: input }).defaultPrevented).toBe(true);
    expect(events).toEqual([ASTRA_TRIGGER_ATTACH]);
    input.remove();
  });

  it('отключённое действие игнорируется, включение обновляется по событию изменения', () => {
    render(<GlobalKeyboardShortcuts />);
    act(() => setHotkeyEnabled('searchChats', false));
    expect(key({ code: 'KeyO', ctrlKey: true }).defaultPrevented).toBe(false);
    act(() => setHotkeyEnabled('searchChats', true));
    expect(key({ code: 'KeyO', ctrlKey: true }).defaultPrevented).toBe(true);
  });

  it('переназначенная привязка используется', () => {
    render(<GlobalKeyboardShortcuts />);
    act(() => saveHotkeyBinding('searchChats', { code: 'KeyJ', mod: true }));
    key({ code: 'KeyO', ctrlKey: true });
    expect(events).toEqual([]);
    key({ code: 'KeyJ', ctrlKey: true });
    expect(events).toEqual([ASTRA_FOCUS_CHAT_SEARCH]);
  });

  it('новый чат: пустой текущий чат удаляется, создаётся новый, настройки переносятся, переход на /', () => {
    mockState = {
      chats: [{ id: 'cur', messages: [{ isStreaming: false }], listed: false }],
      currentChatId: 'cur',
      loadingChatIds: [],
    };
    render(<GlobalKeyboardShortcuts />);
    key({ code: 'KeyK', ctrlKey: true, shiftKey: true });
    expect(mockActions.deleteChat).toHaveBeenCalledWith('cur');
    expect(mockActions.createChat).toHaveBeenCalledTimes(1);
    expect(mockPrepare).toHaveBeenCalledWith('cur', 'new-id');
    expect(mockActions.setCurrentChat).toHaveBeenCalledWith('new-id');
    expect(mockNavigate).toHaveBeenCalledWith('/');
  });

  it('новый чат: чат из списка, загружающийся или стримящийся не удаляется; без текущего чата тоже', () => {
    const run = (chat: any, loading: string[] = []) => {
      mockActions.deleteChat.mockClear();
      mockPrepare.mockClear();
      mockState = { chats: chat ? [chat] : [], currentChatId: chat?.id ?? null, loadingChatIds: loading };
      const { unmount } = render(<GlobalKeyboardShortcuts />);
      key({ code: 'KeyK', ctrlKey: true, shiftKey: true });
      unmount();
    };
    run({ id: 'a', messages: [], listed: true });
    expect(mockActions.deleteChat).not.toHaveBeenCalled();
    run({ id: 'b', messages: [], listed: false }, ['b']);
    expect(mockActions.deleteChat).not.toHaveBeenCalled();
    run({ id: 'c', messages: [{ isStreaming: true }], listed: false });
    expect(mockActions.deleteChat).not.toHaveBeenCalled();
    run(null);
    expect(mockActions.deleteChat).not.toHaveBeenCalled();
    expect(mockPrepare).toHaveBeenCalledWith(undefined, 'new-id');
  });

  it('после размонтирования слушатель снят', () => {
    const { unmount } = render(<GlobalKeyboardShortcuts />);
    unmount();
    expect(key({ code: 'KeyO', ctrlKey: true }).defaultPrevented).toBe(false);
  });
});

describe('HotkeyBindingCapture', () => {
  const setup = (over: Record<string, unknown> = {}) => {
    const h = { onChange: jest.fn(), onReset: jest.fn(), onActivate: jest.fn(), onDeactivate: jest.fn() };
    const props = {
      actionId: 'newChat' as const,
      binding: DEFAULT_HOTKEY_BINDINGS.newChat,
      allBindings: DEFAULT_HOTKEY_BINDINGS,
      isDarkMode: true,
      isActive: false,
      enabled: true,
      ...h,
      ...over,
    };
    const utils = render(<HotkeyBindingCapture {...(props as any)} />);
    return { ...h, ...utils, props };
  };

  it('getDefaultHotkeyBinding отдаёт значение по умолчанию', () => {
    expect(getDefaultHotkeyBinding('searchChats')).toBe(DEFAULT_HOTKEY_BINDINGS.searchChats);
  });

  it('неактивный: показывает привязку, клик активирует', () => {
    const { onActivate } = setup();
    const btn = screen.getByRole('button');
    expect(btn).toHaveTextContent('Ctrl + Shift + K');
    expect(btn).toHaveAttribute('title', 'Нажмите, чтобы задать сочетание клавиш');
    fireEvent.click(btn);
    expect(onActivate).toHaveBeenCalledTimes(1);
  });

  it('выключенное действие: кнопка недоступна, подсказка просит включить переключатель', () => {
    const { onActivate } = setup({ enabled: false, isDarkMode: false });
    const btn = screen.getByRole('button');
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute('title', 'Включите переключатель слева');
    fireEvent.click(btn);
    expect(onActivate).not.toHaveBeenCalled();
  });

  it('активный режим: подсказка, фокус и повторный клик не активирует', () => {
    const { onActivate } = setup({ isActive: true });
    const btn = screen.getByRole('button');
    expect(btn).toHaveTextContent('Нажмите клавиши…');
    expect(btn).toHaveAttribute('title', 'Esc — отмена, Backspace — сброс к умолчанию');
    expect(document.activeElement).toBe(btn);
    fireEvent.click(btn);
    expect(onActivate).not.toHaveBeenCalled();
  });

  it('Escape отменяет, Backspace/Delete сбрасывают к умолчанию', () => {
    const h = setup({ isActive: true });
    expect(key({ key: 'Escape', code: 'Escape' }).defaultPrevented).toBe(true);
    expect(h.onDeactivate).toHaveBeenCalledTimes(1);
    expect(h.onChange).not.toHaveBeenCalled();
    key({ key: 'Backspace', code: 'Backspace' });
    key({ key: 'Delete', code: 'Delete' });
    expect(h.onReset).toHaveBeenCalledTimes(2);
    expect(h.onDeactivate).toHaveBeenCalledTimes(3);
  });

  it('только модификатор или IME-ввод игнорируются, обычное сочетание сохраняется', () => {
    const h = setup({ isActive: true });
    key({ key: 'Shift', code: 'ShiftLeft', shiftKey: true });
    key({ key: 'Process', code: 'KeyA' });
    expect(h.onChange).not.toHaveBeenCalled();
    key({ key: 'j', code: 'KeyJ', ctrlKey: true, shiftKey: true });
    expect(h.onChange).toHaveBeenCalledWith({ code: 'KeyJ', mod: true, shift: true, alt: false });
    expect(h.onDeactivate).toHaveBeenCalledTimes(1);
  });

  it('конфликт: показывается «Занято: …» на 2 секунды, привязка не меняется', () => {
    jest.useFakeTimers();
    const h = setup({ isActive: true });
    // Ctrl+O занято действием «Поиск в чатах»
    act(() => {
      key({ key: 'o', code: 'KeyO', ctrlKey: true });
    });
    expect(h.onChange).not.toHaveBeenCalled();
    const label = HOTKEY_ACTIONS.find((a) => a.id === 'searchChats')!.label;
    h.rerender(<HotkeyBindingCapture {...(h.props as any)} isActive={false} />);
    expect(screen.getByRole('button')).toHaveTextContent(`Занято: ${label}`);
    act(() => {
      jest.advanceTimersByTime(2100);
    });
    expect(screen.getByRole('button')).toHaveTextContent('Ctrl + Shift + K');
    jest.useRealTimers();
  });

  it('конфликт с неизвестным действием показывает запасной текст', () => {
    jest.useFakeTimers();
    const h = setup({
      isActive: true,
      allBindings: { ...DEFAULT_HOTKEY_BINDINGS, ghost: { code: 'KeyZ', mod: true } },
    });
    // конфликт ищется только среди HOTKEY_ACTIONS, поэтому «ghost» конфликтом не считается
    act(() => {
      key({ key: 'z', code: 'KeyZ', ctrlKey: true });
    });
    expect(h.onChange).toHaveBeenCalledWith({ code: 'KeyZ', mod: true, shift: false, alt: false });
    jest.useRealTimers();
  });

  it('после деактивации слушатель снят', () => {
    const h = setup({ isActive: true });
    h.rerender(<HotkeyBindingCapture {...(h.props as any)} isActive={false} />);
    key({ key: 'Escape', code: 'Escape' });
    expect(h.onDeactivate).not.toHaveBeenCalled();
  });
});

describe('HotkeysSettingsDialog', () => {
  it('закрытый диалог ничего не показывает', () => {
    render(<HotkeysSettingsDialog open={false} onClose={jest.fn()} isDarkMode />);
    expect(screen.queryByText('Сочетания клавиш')).toBeNull();
  });

  it('показывает категории и все действия со своими привязками', () => {
    render(<HotkeysSettingsDialog open onClose={jest.fn()} isDarkMode={false} />);
    expect(screen.getByText('Сочетания клавиш')).toBeInTheDocument();
    for (const a of HOTKEY_ACTIONS) {
      expect(screen.getByText(a.label)).toBeInTheDocument();
    }
    screen.getAllByRole('switch').forEach((sw) => expect(sw).toBeChecked());
    expect(screen.getAllByRole('switch')).toHaveLength(HOTKEY_ACTIONS.length);
    expect(screen.getByText('Ctrl + Shift + K')).toBeInTheDocument();
  });

  it('aria-label из inputProps переключателей не попадает в DOM (known issue)', () => {
    // Правильно: у переключателя должно быть имя «Включить: <действие>» для скринридера.
    // Сейчас MUI Switch 7.3 не пробрасывает inputProps, имени у input нет.
    render(<HotkeysSettingsDialog open onClose={jest.fn()} isDarkMode />);
    expect(screen.queryByLabelText('Включить: Окно настроек')).toBeNull();
  });

  it('переключатель отключает действие и сохраняет это', () => {
    render(<HotkeysSettingsDialog open onClose={jest.fn()} isDarkMode />);
    const sw = within(screen.getByText('Окно настроек').parentElement as HTMLElement).getByRole('switch');
    fireEvent.click(sw);
    expect(JSON.parse(localStorage.getItem('astra_hotkey_disabled')!)).toEqual(['openSettings']);
    expect(sw).not.toBeChecked();
  });

  it('запись нового сочетания: клик, нажатие клавиш, сохранение', () => {
    render(<HotkeysSettingsDialog open onClose={jest.fn()} isDarkMode />);
    const row = screen.getByText('Окно настроек').parentElement as HTMLElement;
    fireEvent.click(within(row).getByRole('button'));
    expect(within(row).getByRole('button')).toHaveTextContent('Нажмите клавиши…');
    act(() => {
      key({ key: 'j', code: 'KeyJ', altKey: true });
    });
    expect(JSON.parse(localStorage.getItem('astra_hotkey_bindings')!)).toEqual({
      openSettings: { code: 'KeyJ', mod: false, shift: false, alt: true },
    });
    expect(within(row).getByRole('button')).toHaveTextContent('Alt + J');
  });

  it('Backspace во время записи сбрасывает привязку действия к умолчанию', () => {
    saveHotkeyBinding('openSettings', { code: 'KeyJ', alt: true });
    render(<HotkeysSettingsDialog open onClose={jest.fn()} isDarkMode />);
    const row = screen.getByText('Окно настроек').parentElement as HTMLElement;
    expect(within(row).getByRole('button')).toHaveTextContent('Alt + J');
    fireEvent.click(within(row).getByRole('button'));
    act(() => {
      key({ key: 'Backspace', code: 'Backspace' });
    });
    expect(localStorage.getItem('astra_hotkey_bindings')).toBeNull();
    expect(within(row).getByRole('button')).toHaveTextContent('Alt + S');
  });

  it('«Восстановить значения по умолчанию» сбрасывает привязки и отключения', () => {
    saveHotkeyBinding('openSettings', { code: 'KeyJ', alt: true });
    setHotkeyEnabled('newChat', false);
    render(<HotkeysSettingsDialog open onClose={jest.fn()} isDarkMode />);
    fireEvent.click(screen.getByText('Восстановить значения по умолчанию'));
    expect(localStorage.getItem('astra_hotkey_bindings')).toBeNull();
    expect(localStorage.getItem('astra_hotkey_disabled')).toBeNull();
    screen.getAllByRole('switch').forEach((sw) => expect(sw).toBeChecked());
  });

  it('закрытие кнопкой и по Escape вызывает onClose и прекращает запись', () => {
    const onClose = jest.fn();
    render(<HotkeysSettingsDialog open onClose={onClose} isDarkMode />);
    const row = screen.getByText('Окно настроек').parentElement as HTMLElement;
    fireEvent.click(within(row).getByRole('button'));
    fireEvent.click(screen.getAllByRole('button')[0]);
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(2);
    expect(within(row).getByRole('button')).not.toHaveTextContent('Нажмите клавиши…');
  });

  it('реагирует на внешнее изменение привязок', () => {
    render(<HotkeysSettingsDialog open onClose={jest.fn()} isDarkMode />);
    act(() => {
      saveHotkeyBinding('searchChats', { code: 'KeyP', mod: true });
      window.dispatchEvent(new Event(ASTRA_HOTKEYS_CHANGED));
    });
    expect(screen.getByText('Ctrl + P')).toBeInTheDocument();
  });
});
