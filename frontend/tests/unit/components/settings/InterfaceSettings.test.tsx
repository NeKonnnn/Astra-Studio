import React from 'react';
import { render, screen, fireEvent, waitFor, within, act } from '@testing-library/react';

const mockShowNotification = jest.fn();
jest.mock('@/contexts/AppContext', () => ({
  useAppActions: () => ({ showNotification: mockShowNotification }),
}));

const mockIsSupported = jest.fn();
const mockRequestPermission = jest.fn();
const mockSetNotificationsEnabled = jest.fn();
jest.mock('@/utils/browserNotifications', () => ({
  isNotificationSupported: () => mockIsSupported(),
  requestNotificationPermission: () => mockRequestPermission(),
  areNotificationsEnabled: jest.fn(),
  setNotificationsEnabled: (v: boolean) => mockSetNotificationsEnabled(v),
}));
const mockSetTabNotificationsEnabled = jest.fn();
jest.mock('@/utils/tabNotifications', () => ({
  TAB_NOTIFICATIONS_ENABLED_KEY: 'tab_notifications_enabled',
  setTabNotificationsEnabled: (v: boolean) => mockSetTabNotificationsEnabled(v),
}));

import InterfaceSettings from '@/components/settings/InterfaceSettings';

const rowOf = (label: string) => screen.getByText(label).closest('li') as HTMLElement;
const switchOf = (label: string) => rowOf(label).querySelector('input[type="checkbox"]') as HTMLInputElement;
const triggerOf = (label: string) =>
  rowOf(label).querySelector('[data-testid="ExpandMoreIcon"]')!.parentElement as HTMLElement;

const lastPopover = () => {
  const all = Array.from(document.querySelectorAll('.MuiPopover-root')) as HTMLElement[];
  return within(all[all.length - 1]);
};
const noPopover = () => waitFor(() => expect(document.querySelector('.MuiPopover-root')).toBeNull());

const events = jest.fn();
const colorEvents = jest.fn();

beforeEach(() => {
  jest.clearAllMocks();
  localStorage.clear();
  mockIsSupported.mockReturnValue(true);
  mockRequestPermission.mockResolvedValue(true);
  window.addEventListener('interfaceSettingsChanged', events);
  window.addEventListener('sidebarColorChanged', colorEvents);
});
afterEach(() => {
  window.removeEventListener('interfaceSettingsChanged', events);
  window.removeEventListener('sidebarColorChanged', colorEvents);
});

describe('InterfaceSettings: initial state from localStorage', () => {
  it('uses documented defaults when nothing is stored', () => {
    render(<InterfaceSettings />);

    expect(switchOf('Автогенерация заголовков').checked).toBe(true);
    expect(switchOf('Панель с диалогами').checked).toBe(true);
    expect(switchOf('Подсказки в чате').checked).toBe(true);
    expect(switchOf('Вставить большой текст как файл').checked).toBe(false);
    expect(switchOf('Использовать папки').checked).toBe(false);
    expect(switchOf('Режим широкоформатного экрана').checked).toBe(false);
    expect(switchOf('Автопрокрутка чата при ответе модели').checked).toBe(false);
    expect(screen.getByText('Компактный')).toBeTruthy();
    expect(screen.getByText('Стиль выбора модели: AtraChat')).toBeTruthy();
    expect(screen.getByText('Включен функционал проектов. Папки недоступны.')).toBeTruthy();
    expect(screen.getByText('Скрывать после следующего сообщения')).toBeTruthy();
    expect(screen.getByText('Вставить в поле ввода')).toBeTruthy();
  });

  it('restores every stored boolean', () => {
    const stored: Record<string, string> = {
      auto_generate_titles: 'false',
      large_text_as_file: 'true',
      user_no_border: 'true',
      assistant_no_border: 'true',
      left_align_messages: 'true',
      widescreen_mode: 'true',
      show_user_name: 'true',
      enable_notification: 'true',
      use_folders_mode: 'true',
      show_dialogues_panel: 'false',
      chat_autoscroll_streaming: 'true',
      tab_notifications_enabled: 'true',
      browser_notifications_enabled: 'true',
    };
    Object.entries(stored).forEach(([k, v]) => localStorage.setItem(k, v));
    render(<InterfaceSettings />);

    expect(switchOf('Автогенерация заголовков').checked).toBe(false);
    expect(switchOf('Вставить большой текст как файл').checked).toBe(true);
    expect(switchOf('Отображать имя пользователя вместо \'Вы\' в чате').checked).toBe(true);
    expect(switchOf('Сообщения пользователя без рамки').checked).toBe(true);
    expect(switchOf('Сообщения ассистента без рамки').checked).toBe(true);
    expect(switchOf('Включить звуковое оповещение').checked).toBe(true);
    expect(switchOf('Выравнивание по левому краю').checked).toBe(true);
    expect(switchOf('Режим широкоформатного экрана').checked).toBe(true);
    expect(switchOf('Использовать папки').checked).toBe(true);
    expect(switchOf('Панель с диалогами').checked).toBe(false);
    expect(switchOf('Автопрокрутка чата при ответе модели').checked).toBe(true);
    expect(switchOf('Уведомления').checked).toBe(true);
    expect(switchOf('Браузерные уведомления').checked).toBe(true);
    expect(screen.getByText('Уведомления включены')).toBeTruthy();
    expect(screen.getByText('Счётчик на вкладке включён')).toBeTruthy();
    expect(screen.getByText('Включен функционал папок. Проекты недоступны.')).toBeTruthy();
  });

  it('restores stored model selector mode and migrates the legacy boolean flag', () => {
    localStorage.setItem('model_selector_mode', 'workspace');
    const { unmount } = render(<InterfaceSettings />);
    expect(screen.getByText('Стиль выбора модели: классический')).toBeTruthy();
    expect(screen.getByText('В рабочей зоне показывается классический селектор моделей')).toBeTruthy();
    unmount();

    localStorage.removeItem('model_selector_mode');
    localStorage.setItem('show_model_selector_in_settings', 'true');
    render(<InterfaceSettings />);
    expect(screen.getByText('Выбор модели в настройках')).toBeTruthy();
    expect(screen.getByText('Выбор модели доступен в разделе Настройки → Модели')).toBeTruthy();
  });

  it('ignores an unknown stored model selector mode', () => {
    localStorage.setItem('model_selector_mode', 'weird');
    render(<InterfaceSettings />);

    expect(screen.getByText('Стиль выбора модели: AtraChat')).toBeTruthy();
    expect(screen.getByText('В рабочей зоне показывается селектор AstraChat (агент/модель)')).toBeTruthy();
  });

  it('shows the classic input style when stored', () => {
    localStorage.setItem('chat_input_style', 'classic');
    render(<InterfaceSettings />);

    expect(screen.getByText('Классический')).toBeTruthy();
  });

  it('restores follow-up preferences', () => {
    localStorage.setItem('follow_up_show_scope', 'all');
    localStorage.setItem('follow_up_click_action', 'send');
    render(<InterfaceSettings />);

    expect(screen.getByText('Оставлять в истории чата')).toBeTruthy();
    expect(screen.getByText('Отправить сразу')).toBeTruthy();
  });

  it('(known issue) the chat input contrast is 20 (not the intended default 35) when the key was never stored', () => {
    // Number(null) === 0 конечно, поэтому ветка "35" недостижима для отсутствующего ключа: правильно — 35.
    localStorage.setItem('chat_input_color', 'rgba(255, 255, 255, 0.10)');
    render(<InterfaceSettings />);
    fireEvent.click(triggerOf('Цвет окна ввода'));
    fireEvent.click(lastPopover().getByText('Цвет и контрастность окна ввода'));

    expect((screen.getByRole('slider') as HTMLInputElement).value).toBe('20');
  });

  it.each([
    ['abc', '35'],
    ['150', '100'],
    ['5', '20'],
    ['65', '65'],
  ])('contrast stored as %j is shown as %s', (stored, shown) => {
    localStorage.setItem('chat_input_contrast', stored);
    render(<InterfaceSettings />);
    fireEvent.click(triggerOf('Цвет окна ввода'));
    fireEvent.click(lastPopover().getByText('Цвет и контрастность окна ввода'));

    expect((screen.getByRole('slider') as HTMLInputElement).value).toBe(shown);
  });
});

describe('InterfaceSettings: boolean switches', () => {
  it.each([
    ['Автогенерация заголовков', 'auto_generate_titles', 'false'],
    ['Автопрокрутка чата при ответе модели', 'chat_autoscroll_streaming', 'true'],
    ['Вставить большой текст как файл', 'large_text_as_file', 'true'],
    ["Отображать имя пользователя вместо 'Вы' в чате", 'show_user_name', 'true'],
    ['Сообщения пользователя без рамки', 'user_no_border', 'true'],
    ['Сообщения ассистента без рамки', 'assistant_no_border', 'true'],
    ['Включить звуковое оповещение', 'enable_notification', 'true'],
    ['Выравнивание по левому краю', 'left_align_messages', 'true'],
    ['Режим широкоформатного экрана', 'widescreen_mode', 'true'],
    ['Использовать папки', 'use_folders_mode', 'true'],
    ['Панель с диалогами', 'show_dialogues_panel', 'false'],
  ])('toggling "%s" persists %s=%s, notifies and fires interfaceSettingsChanged', async (label, key, expected) => {
    render(<InterfaceSettings />);
    events.mockClear();

    fireEvent.click(switchOf(label));

    await waitFor(() => expect(localStorage.getItem(key)).toBe(expected));
    expect(mockShowNotification).toHaveBeenCalledWith('success', 'Настройки интерфейса сохранены');
    expect(events).toHaveBeenCalled();
    expect(switchOf(label).checked).toBe(expected === 'true');
  });

  it('writes the full settings snapshot on every change (other keys get their current values)', async () => {
    render(<InterfaceSettings />);

    fireEvent.click(switchOf('Использовать папки'));

    await waitFor(() => expect(localStorage.getItem('use_folders_mode')).toBe('true'));
    expect(localStorage.getItem('auto_generate_titles')).toBe('true');
    expect(localStorage.getItem('show_dialogues_panel')).toBe('true');
    expect(localStorage.getItem('widescreen_mode')).toBe('false');
    expect(mockSetNotificationsEnabled).toHaveBeenCalledWith(false);
    expect(mockSetTabNotificationsEnabled).toHaveBeenCalledWith(false);
  });

  it('toggling the tab counter updates the tab-notifications module', async () => {
    render(<InterfaceSettings />);

    fireEvent.click(switchOf('Уведомления'));

    await waitFor(() => expect(mockSetTabNotificationsEnabled).toHaveBeenCalledWith(true));
    expect(screen.getByText('Счётчик на вкладке включён')).toBeTruthy();
  });

  it('help icons next to notification switches do not toggle them', () => {
    render(<InterfaceSettings />);

    document
      .querySelectorAll('[data-testid="HelpOutlineIcon"]')
      .forEach((icon) => fireEvent.click(icon.closest('button') as HTMLElement));

    expect(switchOf('Уведомления').checked).toBe(false);
    expect(switchOf('Браузерные уведомления').checked).toBe(false);
    expect(mockShowNotification).not.toHaveBeenCalled();
  });
});

describe('InterfaceSettings: browser notifications permission', () => {
  it('requests permission and enables notifications when granted', async () => {
    render(<InterfaceSettings />);

    fireEvent.click(switchOf('Браузерные уведомления'));

    await waitFor(() => expect(mockSetNotificationsEnabled).toHaveBeenCalledWith(true));
    expect(mockRequestPermission).toHaveBeenCalledTimes(1);
    expect(switchOf('Браузерные уведомления').checked).toBe(true);
    expect(screen.getByText('Уведомления включены')).toBeTruthy();
  });

  it('keeps notifications off and warns when the permission is refused', async () => {
    mockRequestPermission.mockResolvedValue(false);
    render(<InterfaceSettings />);

    fireEvent.click(switchOf('Браузерные уведомления'));

    await waitFor(() =>
      expect(mockShowNotification).toHaveBeenCalledWith('warning', 'Разрешение на уведомления не было предоставлено'),
    );
    expect(switchOf('Браузерные уведомления').checked).toBe(false);
    expect(mockSetNotificationsEnabled).not.toHaveBeenCalled();
    expect(localStorage.getItem('auto_generate_titles')).toBeNull();
  });

  it('shows "not supported" and disables the switch when the API is missing', () => {
    mockIsSupported.mockReturnValue(false);
    localStorage.setItem('browser_notifications_enabled', 'true');
    render(<InterfaceSettings />);

    expect(screen.getByText('Не поддерживается вашим браузером')).toBeTruthy();
    expect(switchOf('Браузерные уведомления').disabled).toBe(true);
    expect(switchOf('Браузерные уведомления').checked).toBe(false);
  });

  it('warns when unsupported browsers somehow trigger the switch', async () => {
    render(<InterfaceSettings />);
    mockIsSupported.mockReturnValue(false);

    fireEvent.click(switchOf('Браузерные уведомления'));

    await waitFor(() =>
      expect(mockShowNotification).toHaveBeenCalledWith(
        'warning',
        'Браузерные уведомления не поддерживаются вашим браузером',
      ),
    );
    expect(mockRequestPermission).not.toHaveBeenCalled();
  });

  it('turning notifications off does not ask for permission', async () => {
    localStorage.setItem('browser_notifications_enabled', 'true');
    render(<InterfaceSettings />);

    fireEvent.click(switchOf('Браузерные уведомления'));

    await waitFor(() => expect(mockSetNotificationsEnabled).toHaveBeenCalledWith(false));
    expect(mockRequestPermission).not.toHaveBeenCalled();
  });
});

describe('InterfaceSettings: follow-up suggestions', () => {
  it('toggling off persists, announces it and hides the dependent dropdowns', async () => {
    render(<InterfaceSettings />);
    expect(screen.getByText('Где показывать подсказки')).toBeTruthy();

    fireEvent.click(switchOf('Подсказки в чате'));

    expect(localStorage.getItem('follow_up_auto_generate')).toBe('false');
    expect(mockShowNotification).toHaveBeenCalledWith('success', 'Подсказки в чате отключены');
    expect(screen.queryByText('Где показывать подсказки')).toBeNull();
    expect(screen.queryByText('Действие при клике на подсказку')).toBeNull();

    fireEvent.click(switchOf('Подсказки в чате'));
    expect(mockShowNotification).toHaveBeenLastCalledWith('success', 'Подсказки в чате включены');
    expect(screen.getByText('Где показывать подсказки')).toBeTruthy();
  });

  it('persists the chosen scope and click action', async () => {
    render(<InterfaceSettings />);

    fireEvent.click(triggerOf('Где показывать подсказки'));
    fireEvent.click(lastPopover().getByText('Оставлять в истории чата'));
    expect(localStorage.getItem('follow_up_show_scope')).toBe('all');
    await noPopover();

    fireEvent.click(triggerOf('Действие при клике на подсказку'));
    fireEvent.click(lastPopover().getByText('Отправить сразу'));
    expect(localStorage.getItem('follow_up_click_action')).toBe('send');
    expect(mockShowNotification).toHaveBeenCalledWith('success', 'Настройки подсказок сохранены');
    await noPopover();
    expect(screen.getByText('Оставлять в истории чата')).toBeTruthy();
  });

  it('re-reads follow-up settings when interfaceSettingsChanged fires', () => {
    render(<InterfaceSettings />);
    localStorage.setItem('follow_up_auto_generate', 'false');

    act(() => {
      window.dispatchEvent(new Event('interfaceSettingsChanged'));
    });

    expect(switchOf('Подсказки в чате').checked).toBe(false);
    expect(screen.queryByText('Где показывать подсказки')).toBeNull();
  });

  it('closes the scope and action popovers on backdrop click', async () => {
    render(<InterfaceSettings />);

    fireEvent.click(triggerOf('Где показывать подсказки'));
    fireEvent.click(document.querySelector('.MuiBackdrop-root') as HTMLElement);
    await noPopover();
    fireEvent.click(triggerOf('Действие при клике на подсказку'));
    fireEvent.click(document.querySelector('.MuiBackdrop-root') as HTMLElement);
    await noPopover();

    expect(localStorage.getItem('follow_up_show_scope')).toBeNull();
    expect(localStorage.getItem('follow_up_click_action')).toBeNull();
  });
});

describe('InterfaceSettings: model selector location', () => {
  it('stores the mode in both new and legacy keys and notifies', async () => {
    render(<InterfaceSettings />);

    fireEvent.click(triggerOf('Место расположения выбора модели'));
    fireEvent.click(lastPopover().getByText('Выбор модели в настройках'));

    expect(localStorage.getItem('model_selector_mode')).toBe('settings');
    expect(localStorage.getItem('show_model_selector_in_settings')).toBe('true');
    expect(events).toHaveBeenCalled();
    expect(mockShowNotification).toHaveBeenCalledWith('success', 'Настройки интерфейса сохранены');
    await noPopover();

    fireEvent.click(triggerOf('Место расположения выбора модели'));
    fireEvent.click(lastPopover().getByText('Стиль выбора модели: классический'));
    expect(localStorage.getItem('model_selector_mode')).toBe('workspace');
    expect(localStorage.getItem('show_model_selector_in_settings')).toBe('false');
  });

  it('closes on backdrop click without changes', async () => {
    render(<InterfaceSettings />);

    fireEvent.click(triggerOf('Место расположения выбора модели'));
    fireEvent.click(document.querySelector('.MuiBackdrop-root') as HTMLElement);

    await noPopover();
    expect(localStorage.getItem('model_selector_mode')).toBeNull();
  });
});

describe('InterfaceSettings: chat input style and colour', () => {
  it('switches the input style and persists it', async () => {
    render(<InterfaceSettings />);

    fireEvent.click(triggerOf('Стиль поля ввода'));
    fireEvent.click(lastPopover().getByText('Классический'));

    expect(localStorage.getItem('chat_input_style')).toBe('classic');
    expect(mockShowNotification).toHaveBeenCalledWith('success', 'Стиль поля ввода изменён');
    await noPopover();
    fireEvent.click(triggerOf('Стиль поля ввода'));
    await waitFor(() => expect(lastPopover().getAllByText('Компактный').length).toBeGreaterThan(0));
    fireEvent.click(document.querySelector('.MuiBackdrop-root') as HTMLElement);
  });

  it('resets the input colour to default', async () => {
    localStorage.setItem('chat_input_color', 'rgba(5, 150, 105, 0.38)');
    render(<InterfaceSettings />);
    expect(screen.getByText('Изумруд')).toBeTruthy();

    fireEvent.click(triggerOf('Цвет окна ввода'));
    fireEvent.click(lastPopover().getByText('По умолчанию'));

    expect(localStorage.getItem('chat_input_color')).toBe('');
    expect(mockShowNotification).toHaveBeenCalledWith('success', 'Цвет окна ввода сброшен');
    await noPopover();
    expect(rowOf('Цвет окна ввода').textContent).toContain('По умолчанию');
  });

  it('picks a preset colour from the dialog, saves it and closes the dialog', async () => {
    render(<InterfaceSettings />);

    fireEvent.click(triggerOf('Цвет окна ввода'));
    fireEvent.click(lastPopover().getByText('Цвет и контрастность окна ввода'));
    const dialog = screen.getByRole('dialog');
    fireEvent.click(within(dialog).getByTitle('Графит'));

    expect(localStorage.getItem('chat_input_color')).toBe('rgba(32, 32, 38, 0.72)');
    expect(mockShowNotification).toHaveBeenCalledWith('success', 'Цвет окна ввода изменён');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(rowOf('Цвет окна ввода').textContent).toContain('Графит');
  });

  it('shows "Пользовательский" for a stored colour outside the palette', () => {
    localStorage.setItem('chat_input_color', 'rgba(1, 2, 3, 0.5)');
    render(<InterfaceSettings />);

    expect(rowOf('Цвет окна ввода').textContent).toContain('Пользовательский');
  });

  it('the contrast slider persists its value and fires the settings event, without a toast', () => {
    localStorage.setItem('chat_input_contrast', '40');
    render(<InterfaceSettings />);
    fireEvent.click(triggerOf('Цвет окна ввода'));
    fireEvent.click(lastPopover().getByText('Цвет и контрастность окна ввода'));
    events.mockClear();
    mockShowNotification.mockClear();

    fireEvent.change(screen.getByRole('slider'), { target: { value: 75 } });

    expect(localStorage.getItem('chat_input_contrast')).toBe('75');
    expect(events).toHaveBeenCalled();
    expect(mockShowNotification).not.toHaveBeenCalled();
  });

  it('the dialog can be closed with its close button; popover with backdrop', async () => {
    render(<InterfaceSettings />);
    fireEvent.click(triggerOf('Цвет окна ввода'));
    fireEvent.click(document.querySelector('.MuiBackdrop-root') as HTMLElement);
    await noPopover();

    fireEvent.click(triggerOf('Цвет окна ввода'));
    fireEvent.click(lastPopover().getByText('Цвет и контрастность окна ввода'));
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Закрыть' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(localStorage.getItem('chat_input_color')).toBeNull();
  });

  it('Escape closes the input colour dialog', async () => {
    render(<InterfaceSettings />);
    fireEvent.click(triggerOf('Цвет окна ввода'));
    fireEvent.click(lastPopover().getByText('Цвет и контрастность окна ввода'));

    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });
});

describe('InterfaceSettings: sidebar colour', () => {
  const openPicker = async () => {
    fireEvent.click(triggerOf('Цвет боковых панелей'));
    fireEvent.click(lastPopover().getByText('Выбрать цвет'));
    return screen.getByRole('dialog');
  };

  it('shows the label for the stored value: default, preset, custom hex, custom non-hex', () => {
    const { unmount } = render(<InterfaceSettings />);
    expect(rowOf('Цвет боковых панелей').textContent).toContain('По умолчанию');
    unmount();

    localStorage.setItem('sidebar_panel_color', '#009688');
    const second = render(<InterfaceSettings />);
    expect(rowOf('Цвет боковых панелей').textContent).toContain('Бирюзовый');
    second.unmount();

    localStorage.setItem('sidebar_panel_color', '#123456');
    const third = render(<InterfaceSettings />);
    expect(rowOf('Цвет боковых панелей').textContent).toContain('Пользовательский (#123456)');
    third.unmount();

    localStorage.setItem('sidebar_panel_color', 'rgb(1,2,3)');
    render(<InterfaceSettings />);
    expect(rowOf('Цвет боковых панелей').textContent).toContain('Пользовательский');
    expect(rowOf('Цвет боковых панелей').textContent).not.toContain('Пользовательский (');
  });

  it('resetting to default stores an empty value and announces the change', async () => {
    localStorage.setItem('sidebar_panel_color', '#009688');
    render(<InterfaceSettings />);

    fireEvent.click(triggerOf('Цвет боковых панелей'));
    fireEvent.click(lastPopover().getByText('По умолчанию'));

    expect(localStorage.getItem('sidebar_panel_color')).toBe('');
    expect((colorEvents.mock.calls[0][0] as CustomEvent).detail).toBe('');
    expect(mockShowNotification).toHaveBeenCalledWith('success', 'Цвет панелей сброшен');
    await noPopover();
  });

  it('offers the current custom colour as a no-op entry that just closes the menu', async () => {
    localStorage.setItem('sidebar_panel_color', '#123456');
    render(<InterfaceSettings />);

    fireEvent.click(triggerOf('Цвет боковых панелей'));
    fireEvent.click(lastPopover().getByText('Пользовательский (#123456)'));

    await noPopover();
    expect(localStorage.getItem('sidebar_panel_color')).toBe('#123456');
    expect(mockShowNotification).not.toHaveBeenCalled();
  });

  it('shows a plain "Пользовательский" entry for a non-hex custom colour', async () => {
    localStorage.setItem('sidebar_panel_color', 'rgb(1,2,3)');
    render(<InterfaceSettings />);

    fireEvent.click(triggerOf('Цвет боковых панелей'));

    expect(lastPopover().getByText('Пользовательский')).toBeTruthy();
    fireEvent.click(document.querySelector('.MuiBackdrop-root') as HTMLElement);
    await noPopover();
  });

  it('picking a palette swatch applies it and closes the dialog', async () => {
    render(<InterfaceSettings />);
    const dialog = await openPicker();

    fireEvent.click(within(dialog).getByTitle('Синий'));

    expect(localStorage.getItem('sidebar_panel_color')).toBe('#2196f3');
    expect((colorEvents.mock.calls[0][0] as CustomEvent).detail).toBe('#2196f3');
    expect(mockShowNotification).toHaveBeenCalledWith('success', 'Цвет панелей применён');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(rowOf('Цвет боковых панелей').textContent).toContain('Синий');
  });

  it('applies a valid custom HEX in upper case', async () => {
    render(<InterfaceSettings />);
    const dialog = await openPicker();
    const input = within(dialog).getByPlaceholderText('#RRGGBB') as HTMLInputElement;
    expect(input.value).toBe('#667EEA');

    fireEvent.change(input, { target: { value: 'ab12ef' } });
    expect(input.value).toBe('#AB12EF');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Применить' }));

    expect(localStorage.getItem('sidebar_panel_color')).toBe('#AB12EF');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('rejects an incomplete HEX with a warning and keeps the dialog open', async () => {
    render(<InterfaceSettings />);
    const dialog = await openPicker();

    fireEvent.change(within(dialog).getByPlaceholderText('#RRGGBB'), { target: { value: '#12' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Применить' }));

    expect(mockShowNotification).toHaveBeenCalledWith('warning', 'Введите HEX-цвет в формате #RRGGBB');
    expect(localStorage.getItem('sidebar_panel_color')).toBeNull();
    expect(screen.getByRole('dialog')).toBeTruthy();
  });

  it('strips non-hex characters, truncates to 7 chars and clears on empty input', async () => {
    render(<InterfaceSettings />);
    const dialog = await openPicker();
    const input = within(dialog).getByPlaceholderText('#RRGGBB') as HTMLInputElement;

    fireEvent.change(input, { target: { value: ' zz#12GG34567890 ' } });
    expect(input.value).toBe('#123456');

    fireEvent.change(input, { target: { value: 'qq' } });
    expect(input.value).toBe('');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Применить' }));
    expect(mockShowNotification).toHaveBeenCalledWith('warning', 'Введите HEX-цвет в формате #RRGGBB');
  });

  it('the native colour input feeds the custom HEX field', async () => {
    render(<InterfaceSettings />);
    const dialog = await openPicker();
    const color = dialog.querySelector('input[type="color"]') as HTMLInputElement;

    fireEvent.change(color, { target: { value: '#00ff00' } });

    expect((within(dialog).getByPlaceholderText('#RRGGBB') as HTMLInputElement).value).toBe('#00FF00');
  });

  it('starts the custom HEX field from the stored colour', async () => {
    localStorage.setItem('sidebar_panel_color', '#abcdef');
    render(<InterfaceSettings />);
    const dialog = await openPicker();

    expect((within(dialog).getByPlaceholderText('#RRGGBB') as HTMLInputElement).value).toBe('#ABCDEF');
  });

  it('picking the default swatch in the dialog clears the colour', async () => {
    localStorage.setItem('sidebar_panel_color', '#009688');
    render(<InterfaceSettings />);
    const dialog = await openPicker();

    fireEvent.click(within(dialog).getAllByTitle('По умолчанию')[0]);

    expect(localStorage.getItem('sidebar_panel_color')).toBe('');
  });

  it('the dialog closes by its button, by Escape and the colour popover by backdrop', async () => {
    render(<InterfaceSettings />);
    let dialog = await openPicker();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Закрыть' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

    dialog = await openPicker();
    fireEvent.keyDown(dialog, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

    fireEvent.click(triggerOf('Цвет боковых панелей'));
    fireEvent.click(document.querySelector('.MuiBackdrop-root') as HTMLElement);
    await noPopover();
    expect(localStorage.getItem('sidebar_panel_color')).toBeNull();
  });
});

describe('InterfaceSettings: work zone background', () => {
  const chooseBg = async (label: string) => {
    fireEvent.click(triggerOf('Фон рабочей зоны'));
    fireEvent.click(lastPopover().getByText(label));
  };

  it.each([
    ['Звёздное небо', 'starry', 'Включён фон «Звёздное небо» в рабочей зоне'],
    ['Снегопад', 'snowfall', 'Включён фон «Снегопад» в рабочей зоне'],
  ])('choosing "%s" stores %s and announces it', async (label, mode, message) => {
    render(<InterfaceSettings />);

    await chooseBg(label);

    expect(localStorage.getItem('work_zone_bg_mode')).toBe(mode);
    expect(mockShowNotification).toHaveBeenCalledWith('success', message);
    expect(events).toHaveBeenCalled();
    await noPopover();
    expect(rowOf('Фон рабочей зоны').textContent).toContain(label);
  });

  it('choosing default resets the mode', async () => {
    localStorage.setItem('work_zone_bg_mode', 'starry');
    render(<InterfaceSettings />);

    await chooseBg('По умолчанию');

    expect(localStorage.getItem('work_zone_bg_mode')).toBe('default');
    expect(mockShowNotification).toHaveBeenCalledWith('success', 'Фон рабочей зоны: по умолчанию');
  });

  it('shows "Своё изображение" when custom mode has an image', () => {
    localStorage.setItem('work_zone_bg_mode', 'custom');
    localStorage.setItem('work_zone_bg_custom_image', 'data:image/png;base64,AAA');
    render(<InterfaceSettings />);

    expect(rowOf('Фон рабочей зоны').textContent).toContain('Своё изображение');
  });

  it('falls back to default if custom mode is stored without an image', () => {
    localStorage.setItem('work_zone_bg_mode', 'custom');
    render(<InterfaceSettings />);

    expect(rowOf('Фон рабочей зоны').textContent).toContain('По умолчанию');
  });

  it('follows interfaceSettingsChanged for the work zone mode', () => {
    render(<InterfaceSettings />);
    localStorage.setItem('work_zone_bg_mode', 'snowfall');

    act(() => {
      window.dispatchEvent(new Event('interfaceSettingsChanged'));
    });

    expect(rowOf('Фон рабочей зоны').textContent).toContain('Снегопад');
  });

  it('closes the popover on backdrop click', async () => {
    render(<InterfaceSettings />);
    fireEvent.click(triggerOf('Фон рабочей зоны'));
    fireEvent.click(document.querySelector('.MuiBackdrop-root') as HTMLElement);

    await noPopover();
    expect(localStorage.getItem('work_zone_bg_mode')).toBeNull();
  });
});

describe('InterfaceSettings: custom work zone image', () => {
  const OriginalImage = (global as any).Image;
  let imageSize: { width: number; height: number } | 'error';

  class FakeImage {
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;
    naturalWidth = 0;
    naturalHeight = 0;
    set src(_v: string) {
      Promise.resolve().then(() => {
        if (imageSize === 'error') {
          this.onerror && this.onerror();
        } else {
          this.naturalWidth = imageSize.width;
          this.naturalHeight = imageSize.height;
          this.onload && this.onload();
        }
      });
    }
  }

  const openDialog = async () => {
    fireEvent.click(triggerOf('Фон рабочей зоны'));
    fireEvent.click(lastPopover().getByText('Своё изображение'));
    return screen.getByRole('dialog');
  };
  const fileInput = () => document.querySelector('input[type="file"]') as HTMLInputElement;
  const upload = (file: File) => fireEvent.change(fileInput(), { target: { files: [file] } });
  const png = (size = 100) => {
    const f = new File(['x'], 'bg.png', { type: 'image/png' });
    Object.defineProperty(f, 'size', { value: size });
    return f;
  };

  beforeEach(() => {
    imageSize = { width: 800, height: 600 };
    (global as any).Image = FakeImage;
    (URL as any).createObjectURL = jest.fn(() => 'blob:img');
    (URL as any).revokeObjectURL = jest.fn();
  });
  afterEach(() => {
    (global as any).Image = OriginalImage;
    jest.restoreAllMocks();
  });

  it('opens with upload enabled and remove/apply disabled while no image is stored', async () => {
    render(<InterfaceSettings />);
    const dialog = await openDialog();

    expect(within(dialog).getByRole('button', { name: 'Загрузить фото' })).toBeTruthy();
    expect((within(dialog).getByRole('button', { name: 'Удалить фото' }) as HTMLButtonElement).disabled).toBe(true);
    expect((within(dialog).getByRole('button', { name: 'Применить' }) as HTMLButtonElement).disabled).toBe(true);
    expect(fileInput().accept).toBe('image/*');
  });

  it('the upload button opens the native file picker', async () => {
    render(<InterfaceSettings />);
    const dialog = await openDialog();
    const click = jest.spyOn(fileInput(), 'click').mockImplementation(() => undefined);

    fireEvent.click(within(dialog).getByRole('button', { name: 'Загрузить фото' }));

    expect(click).toHaveBeenCalledTimes(1);
  });

  it('stores a valid image as data URL, switches the background to custom and notifies', async () => {
    render(<InterfaceSettings />);
    await openDialog();

    upload(png());

    await waitFor(() =>
      expect(mockShowNotification).toHaveBeenCalledWith('success', 'Фото загружено и установлено фоном рабочей зоны'),
    );
    expect(localStorage.getItem('work_zone_bg_custom_image')).toMatch(/^data:image\/png;base64,/);
    expect(localStorage.getItem('work_zone_bg_mode')).toBe('custom');
    expect(rowOf('Фон рабочей зоны').textContent).toContain('Своё изображение');
    expect((URL as any).revokeObjectURL).toHaveBeenCalledWith('blob:img');
  });

  it('ignores an empty file selection', async () => {
    render(<InterfaceSettings />);
    await openDialog();

    fireEvent.change(fileInput(), { target: { files: [] } });

    expect(mockShowNotification).not.toHaveBeenCalled();
  });

  it('rejects non-image files', async () => {
    render(<InterfaceSettings />);
    await openDialog();

    upload(new File(['x'], 'doc.pdf', { type: 'application/pdf' }));

    expect(mockShowNotification).toHaveBeenCalledWith('warning', 'Можно загрузить только изображение');
    expect(localStorage.getItem('work_zone_bg_custom_image')).toBeNull();
  });

  it('rejects images over 4 MB but accepts exactly 4 MB', async () => {
    render(<InterfaceSettings />);
    await openDialog();

    upload(png(4 * 1024 * 1024 + 1));
    expect(mockShowNotification).toHaveBeenCalledWith('warning', 'Размер изображения должен быть не больше 4 МБ');
    expect(localStorage.getItem('work_zone_bg_custom_image')).toBeNull();

    upload(png(4 * 1024 * 1024));
    await waitFor(() => expect(localStorage.getItem('work_zone_bg_custom_image')).not.toBeNull());
  });

  it.each([
    [{ width: 4097, height: 100 }],
    [{ width: 100, height: 5000 }],
  ])('rejects images with a side above 4096 px (%j) but accepts 4096', async (dims) => {
    imageSize = dims;
    render(<InterfaceSettings />);
    await openDialog();

    upload(png());

    await waitFor(() =>
      expect(mockShowNotification).toHaveBeenCalledWith('warning', 'Максимальный размер изображения: 4096x4096 px'),
    );
    expect(localStorage.getItem('work_zone_bg_custom_image')).toBeNull();

    imageSize = { width: 4096, height: 4096 };
    upload(png());
    await waitFor(() => expect(localStorage.getItem('work_zone_bg_custom_image')).not.toBeNull());
  });

  it('reports a decoding failure', async () => {
    imageSize = 'error';
    render(<InterfaceSettings />);
    await openDialog();

    upload(png());

    await waitFor(() =>
      expect(mockShowNotification).toHaveBeenCalledWith('error', 'Не удалось прочитать размеры изображения'),
    );
    expect(localStorage.getItem('work_zone_bg_custom_image')).toBeNull();
    expect((URL as any).revokeObjectURL).toHaveBeenCalledWith('blob:img');
  });

  it('reports an error when FileReader yields no string result', async () => {
    class EmptyReader {
      result: any = null;
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;
      readAsDataURL() {
        this.onload && this.onload();
      }
    }
    jest.spyOn(window as any, 'FileReader').mockImplementation(() => new EmptyReader() as any);
    render(<InterfaceSettings />);
    await openDialog();

    upload(png());

    await waitFor(() => expect(mockShowNotification).toHaveBeenCalledWith('error', 'Не удалось прочитать изображение'));
    expect(localStorage.getItem('work_zone_bg_custom_image')).toBeNull();
  });

  it('reports a FileReader error', async () => {
    class BrokenReader {
      result: any = null;
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;
      readAsDataURL() {
        this.onerror && this.onerror();
      }
    }
    jest.spyOn(window as any, 'FileReader').mockImplementation(() => new BrokenReader() as any);
    render(<InterfaceSettings />);
    await openDialog();

    upload(png());

    await waitFor(() => expect(mockShowNotification).toHaveBeenCalledWith('error', 'Ошибка при загрузке изображения'));
  });

  it('removes the stored image and falls back from custom mode to default', async () => {
    localStorage.setItem('work_zone_bg_mode', 'custom');
    localStorage.setItem('work_zone_bg_custom_image', 'data:image/png;base64,AAA');
    render(<InterfaceSettings />);
    const dialog = await openDialog();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Удалить фото' }));

    expect(localStorage.getItem('work_zone_bg_custom_image')).toBeNull();
    expect(localStorage.getItem('work_zone_bg_mode')).toBe('default');
    expect(mockShowNotification).toHaveBeenCalledWith('success', 'Пользовательский фон удалён');
    expect(rowOf('Фон рабочей зоны').textContent).toContain('По умолчанию');
    expect((within(dialog).getByRole('button', { name: 'Удалить фото' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('removing the image keeps a non-custom mode untouched', async () => {
    localStorage.setItem('work_zone_bg_mode', 'starry');
    localStorage.setItem('work_zone_bg_custom_image', 'data:image/png;base64,AAA');
    render(<InterfaceSettings />);
    const dialog = await openDialog();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Удалить фото' }));

    expect(localStorage.getItem('work_zone_bg_mode')).toBe('starry');
  });

  it('applies the stored image as the background and closes the dialog', async () => {
    localStorage.setItem('work_zone_bg_custom_image', 'data:image/png;base64,AAA');
    render(<InterfaceSettings />);
    const dialog = await openDialog();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Применить' }));

    expect(localStorage.getItem('work_zone_bg_mode')).toBe('custom');
    expect(mockShowNotification).toHaveBeenCalledWith('success', 'Включён пользовательский фон рабочей зоны');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('closes by the close button and by Escape; help icon is inert', async () => {
    render(<InterfaceSettings />);
    let dialog = await openDialog();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Закрыть' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    await noPopover();

    dialog = await openDialog();
    fireEvent.click(dialog.querySelector('[data-testid="HelpOutlineIcon"]')!.closest('button') as HTMLElement);
    expect(screen.getByRole('dialog')).toBeTruthy();
    fireEvent.keyDown(dialog, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });
});
