import React from 'react';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';

const mockActions = {
  deleteAllChats: jest.fn(),
  exportChats: jest.fn(),
  importChats: jest.fn(),
  archiveAllChats: jest.fn(),
  showNotification: jest.fn(),
};
let mockState: any;
jest.mock('@/contexts/AppContext', () => ({
  useAppContext: () => ({ state: mockState }),
  useAppActions: () => mockActions,
}));
jest.mock('@/components/ManageSharesDialog', () => ({
  __esModule: true,
  default: ({ open, onClose, isDarkMode }: any) =>
    open ? (
      <div data-testid="shares-dialog" data-dark={String(isDarkMode)}>
        <button onClick={onClose}>закрыть-ссылки</button>
      </div>
    ) : null,
}));

import ChatSettings from '@/components/settings/ChatSettings';
import { CHAT_FONT_SIZE_EVENT, CHAT_FONT_SIZE_KEY } from '@/utils/chatFontSize';
import { FOLLOW_UP_AUTO_GENERATE_KEY } from '@/chat/followUpSettings';
import { KEEP_CHAT_SETTINGS_KEY } from '@/utils/newChatSettings';

const stateWith = (chats: number, folders = 0, totalMessages = 0) => ({
  chats: Array.from({ length: chats }, (_, i) => ({ id: `c${i}` })),
  folders: Array.from({ length: folders }, (_, i) => ({ id: `f${i}` })),
  stats: { totalMessages },
});

const switchOf = (label: string) =>
  (screen.getByText(label).closest('li') as HTMLElement).querySelector('input[type="checkbox"]') as HTMLInputElement;

beforeEach(() => {
  jest.clearAllMocks();
  localStorage.clear();
  mockState = stateWith(3, 2, 42);
});

describe('ChatSettings: chat suggestions switch', () => {
  it('is on by default and persists the choice, notifies and emits interfaceSettingsChanged', () => {
    const events = jest.fn();
    window.addEventListener('interfaceSettingsChanged', events);
    render(<ChatSettings />);
    const sw = switchOf('Подсказки в чате');
    expect(sw.checked).toBe(true);

    fireEvent.click(sw);

    expect(localStorage.getItem(FOLLOW_UP_AUTO_GENERATE_KEY)).toBe('false');
    expect(mockActions.showNotification).toHaveBeenCalledWith('success', 'Подсказки в чате отключены');
    expect(events).toHaveBeenCalled();
    expect(switchOf('Подсказки в чате').checked).toBe(false);

    fireEvent.click(switchOf('Подсказки в чате'));
    expect(localStorage.getItem(FOLLOW_UP_AUTO_GENERATE_KEY)).toBe('true');
    expect(mockActions.showNotification).toHaveBeenLastCalledWith('success', 'Подсказки в чате включены');
    window.removeEventListener('interfaceSettingsChanged', events);
  });

  it('starts off when the stored setting is false', () => {
    localStorage.setItem(FOLLOW_UP_AUTO_GENERATE_KEY, 'false');
    render(<ChatSettings />);

    expect(switchOf('Подсказки в чате').checked).toBe(false);
  });

  it('re-reads the setting when interfaceSettingsChanged fires elsewhere, and unsubscribes on unmount', () => {
    const { unmount } = render(<ChatSettings />);
    expect(switchOf('Подсказки в чате').checked).toBe(true);

    localStorage.setItem(FOLLOW_UP_AUTO_GENERATE_KEY, 'false');
    fireEvent(window, new Event('interfaceSettingsChanged'));
    expect(switchOf('Подсказки в чате').checked).toBe(false);

    unmount();
    expect(() => fireEvent(window, new Event('interfaceSettingsChanged'))).not.toThrow();
  });
});

describe('ChatSettings: keep settings in new chat', () => {
  it('persists the flag and tells the user what the next chat will look like', () => {
    render(<ChatSettings />);
    expect(switchOf('Переносить настройки в новый чат').checked).toBe(false);

    fireEvent.click(switchOf('Переносить настройки в новый чат'));
    expect(localStorage.getItem(KEEP_CHAT_SETTINGS_KEY)).toBe('true');
    expect(mockActions.showNotification).toHaveBeenCalledWith('success', 'Новый чат получит настройки текущего');

    fireEvent.click(switchOf('Переносить настройки в новый чат'));
    expect(localStorage.getItem(KEEP_CHAT_SETTINGS_KEY)).toBe('false');
    expect(mockActions.showNotification).toHaveBeenLastCalledWith('success', 'Новый чат будет открываться чистым');
  });

  it('reflects a previously stored enabled flag', () => {
    localStorage.setItem(KEEP_CHAT_SETTINGS_KEY, 'true');
    render(<ChatSettings />);

    expect(switchOf('Переносить настройки в новый чат').checked).toBe(true);
  });
});

describe('ChatSettings: font size', () => {
  it.each([
    [null, 'Средний'],
    ['small', 'Мелкий'],
    ['large', 'Большой'],
    ['bogus', 'Средний'],
  ])('shows the label for stored size %j as %s', (stored, label) => {
    if (stored) localStorage.setItem(CHAT_FONT_SIZE_KEY, stored);
    render(<ChatSettings />);

    expect(screen.getByText(label)).toBeTruthy();
  });

  it('stores the chosen size, emits the font-size event and notifies', async () => {
    const onEvent = jest.fn();
    window.addEventListener(CHAT_FONT_SIZE_EVENT, onEvent);
    render(<ChatSettings />);

    fireEvent.click(screen.getByText('Средний'));
    fireEvent.click(screen.getByText('Большой'));

    expect(localStorage.getItem(CHAT_FONT_SIZE_KEY)).toBe('large');
    expect((onEvent.mock.calls[0][0] as CustomEvent).detail).toBe('large');
    expect(mockActions.showNotification).toHaveBeenCalledWith('success', 'Размер шрифта изменен');
    await waitFor(() => expect(screen.queryByText('Мелкий')).toBeNull());
    expect(screen.getByText('Большой')).toBeTruthy();
    window.removeEventListener(CHAT_FONT_SIZE_EVENT, onEvent);
  });

  it('can pick the small size', async () => {
    render(<ChatSettings />);

    fireEvent.click(screen.getByText('Средний'));
    fireEvent.click(screen.getByText('Мелкий'));

    expect(localStorage.getItem(CHAT_FONT_SIZE_KEY)).toBe('small');
    await waitFor(() => expect(screen.queryByText('Большой')).toBeNull());
  });

  it('closes the popover on backdrop click without changing the size', async () => {
    render(<ChatSettings />);
    fireEvent.click(screen.getByText('Средний'));
    expect(screen.getByText('Мелкий')).toBeTruthy();

    fireEvent.click(document.querySelector('.MuiBackdrop-root') as HTMLElement);

    await waitFor(() => expect(screen.queryByText('Мелкий')).toBeNull());
    expect(localStorage.getItem(CHAT_FONT_SIZE_KEY)).toBeNull();
    expect(mockActions.showNotification).not.toHaveBeenCalled();
  });

  it('uses the isDarkMode prop for the shares dialog', () => {
    render(<ChatSettings isDarkMode />);
    fireEvent.click(screen.getByRole('button', { name: 'Управление' }));

    expect(screen.getByTestId('shares-dialog').getAttribute('data-dark')).toBe('true');
  });
});

describe('ChatSettings: shares dialog', () => {
  it('opens and closes the manage-shares dialog; theme mode comes from the MUI theme by default', () => {
    render(<ChatSettings />);
    expect(screen.queryByTestId('shares-dialog')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Управление' }));
    expect(screen.getByTestId('shares-dialog').getAttribute('data-dark')).toBe('false');

    fireEvent.click(screen.getByText('закрыть-ссылки'));
    expect(screen.queryByTestId('shares-dialog')).toBeNull();
  });
});

describe('ChatSettings: buttons depend on the number of chats', () => {
  it('disables export, archive and delete when there are no chats, but keeps import and shares', () => {
    mockState = stateWith(0);
    render(<ChatSettings />);

    expect((screen.getByRole('button', { name: 'Экспортировать чаты' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'Архивировать все чаты' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'Удалить все чаты' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'Импортировать чаты' }) as HTMLButtonElement).disabled).toBe(false);
    expect((screen.getByRole('button', { name: 'Управление' }) as HTMLButtonElement).disabled).toBe(false);
  });
});

describe('ChatSettings: export', () => {
  it('exports and notifies', () => {
    render(<ChatSettings />);

    fireEvent.click(screen.getByRole('button', { name: 'Экспортировать чаты' }));

    expect(mockActions.exportChats).toHaveBeenCalledTimes(1);
    expect(mockActions.showNotification).toHaveBeenCalledWith('success', 'Чаты успешно экспортированы');
  });

  it('shows an error when exporting throws', () => {
    mockActions.exportChats.mockImplementationOnce(() => {
      throw new Error('quota');
    });
    render(<ChatSettings />);

    fireEvent.click(screen.getByRole('button', { name: 'Экспортировать чаты' }));

    expect(mockActions.showNotification).toHaveBeenCalledWith('error', 'Ошибка при экспорте чатов');
    expect(mockActions.showNotification).not.toHaveBeenCalledWith('success', expect.anything());
  });
});

describe('ChatSettings: import', () => {
  const fileInput = () => document.querySelector('input[type="file"]') as HTMLInputElement;
  const pick = (file: File | null) =>
    fireEvent.change(fileInput(), { target: { files: file ? [file] : [] } });

  it('the visible button delegates to the hidden file input and the input accepts only .json', () => {
    render(<ChatSettings />);
    const click = jest.spyOn(fileInput(), 'click').mockImplementation(() => undefined);

    fireEvent.click(screen.getByRole('button', { name: 'Импортировать чаты' }));

    expect(click).toHaveBeenCalledTimes(1);
    expect(fileInput().accept).toBe('.json');
  });

  it('imports a .json file and notifies', async () => {
    mockActions.importChats.mockResolvedValue(undefined);
    render(<ChatSettings />);
    const file = new File(['[]'], 'chats.json', { type: 'application/json' });

    pick(file);

    await waitFor(() => expect(mockActions.showNotification).toHaveBeenCalledWith('success', 'Чаты успешно импортированы'));
    expect(mockActions.importChats).toHaveBeenCalledWith(file);
  });

  it('does nothing when no file is chosen', () => {
    render(<ChatSettings />);

    pick(null);

    expect(mockActions.importChats).not.toHaveBeenCalled();
    expect(mockActions.showNotification).not.toHaveBeenCalled();
  });

  it('rejects files that are not .json before calling import', () => {
    render(<ChatSettings />);

    pick(new File(['x'], 'chats.txt', { type: 'text/plain' }));

    expect(mockActions.importChats).not.toHaveBeenCalled();
    expect(mockActions.showNotification).toHaveBeenCalledWith('error', 'Файл должен быть в формате JSON');
  });

  it('(known issue) accepts only a lowercase ".json" extension, so CHATS.JSON is rejected', () => {
    render(<ChatSettings />);

    pick(new File(['[]'], 'CHATS.JSON', { type: 'application/json' }));

    expect(mockActions.importChats).not.toHaveBeenCalled();
    expect(mockActions.showNotification).toHaveBeenCalledWith('error', 'Файл должен быть в формате JSON');
  });

  it('shows the error message from the import failure', async () => {
    mockActions.importChats.mockRejectedValue(new Error('Неверный формат файла'));
    render(<ChatSettings />);

    pick(new File(['{'], 'bad.json'));

    await waitFor(() => expect(mockActions.showNotification).toHaveBeenCalledWith('error', 'Неверный формат файла'));
    expect(mockActions.showNotification).not.toHaveBeenCalledWith('success', expect.anything());
  });

  it('falls back to a default message when the failure has no message', async () => {
    mockActions.importChats.mockRejectedValue({});
    render(<ChatSettings />);

    pick(new File(['{'], 'bad.json'));

    await waitFor(() => expect(mockActions.showNotification).toHaveBeenCalledWith('error', 'Ошибка при импорте чатов'));
  });
});

describe('ChatSettings: archive', () => {
  it('shows counts in the confirmation dialog and archives on confirm', async () => {
    render(<ChatSettings />);

    fireEvent.click(screen.getByRole('button', { name: 'Архивировать все чаты' }));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText('Чатов: 3')).toBeTruthy();
    expect(within(dialog).getByText('Сообщений: 42')).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Архивировать' }));

    expect(mockActions.archiveAllChats).toHaveBeenCalledTimes(1);
    expect(mockActions.showNotification).toHaveBeenCalledWith('success', 'Все чаты успешно архивированы');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('cancel closes the dialog without archiving', async () => {
    render(<ChatSettings />);

    fireEvent.click(screen.getByRole('button', { name: 'Архивировать все чаты' }));
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Отменить' }));

    expect(mockActions.archiveAllChats).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('does not archive and shows an info message if the chats disappeared while the dialog was open', () => {
    const { rerender } = render(<ChatSettings />);
    fireEvent.click(screen.getByRole('button', { name: 'Архивировать все чаты' }));
    mockState = stateWith(0);
    rerender(<ChatSettings />);

    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Архивировать' }));

    expect(mockActions.archiveAllChats).not.toHaveBeenCalled();
    expect(mockActions.showNotification).toHaveBeenCalledWith('info', 'Нет чатов для архивирования');
  });
});

describe('ChatSettings: delete all chats', () => {
  it('shows what will be deleted and deletes only after confirmation', async () => {
    render(<ChatSettings />);

    fireEvent.click(screen.getByRole('button', { name: 'Удалить все чаты' }));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText('Все чаты (3)')).toBeTruthy();
    expect(within(dialog).getByText('Все сообщения (42)')).toBeTruthy();
    expect(within(dialog).getByText('Все папки (2)')).toBeTruthy();
    expect(mockActions.deleteAllChats).not.toHaveBeenCalled();

    fireEvent.click(within(dialog).getAllByRole('button', { name: 'Удалить все чаты' })[0]);

    expect(mockActions.deleteAllChats).toHaveBeenCalledTimes(1);
    expect(mockActions.showNotification).toHaveBeenCalledWith('success', 'Все чаты успешно удалены');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('cancel keeps all chats', async () => {
    render(<ChatSettings />);

    fireEvent.click(screen.getByRole('button', { name: 'Удалить все чаты' }));
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Отменить' }));

    expect(mockActions.deleteAllChats).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });
});

describe('ChatSettings: dismissing dialogs with Escape', () => {
  it('Escape closes the archive confirmation without archiving', async () => {
    render(<ChatSettings />);
    fireEvent.click(screen.getByRole('button', { name: 'Архивировать все чаты' }));

    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(mockActions.archiveAllChats).not.toHaveBeenCalled();
  });

  it('Escape closes the delete confirmation without deleting', async () => {
    render(<ChatSettings />);
    fireEvent.click(screen.getByRole('button', { name: 'Удалить все чаты' }));

    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(mockActions.deleteAllChats).not.toHaveBeenCalled();
  });
});
