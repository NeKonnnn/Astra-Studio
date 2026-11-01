import '@testing-library/jest-dom';
import React from 'react';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import ShareConfirmDialog from '@/components/ShareConfirmDialog';
import ArchiveModal from '@/components/ArchiveModal';

const mockNavigate = jest.fn();
jest.mock('react-router-dom', () => ({ useNavigate: () => mockNavigate }));

let mockChats: any[] = [];
const mockActions = {
  unarchiveChat: jest.fn(),
  deleteChat: jest.fn(),
  exportChats: jest.fn(),
  unarchiveAllChats: jest.fn(),
  setCurrentChat: jest.fn(),
};
jest.mock('@/contexts/AppContext', () => ({
  useAppContext: () => ({ state: { chats: mockChats } }),
  useAppActions: () => mockActions,
}));

describe('ShareConfirmDialog', () => {
  let writeText: jest.Mock;

  beforeEach(() => {
    jest.useFakeTimers();
    writeText = jest.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
  });
  afterEach(() => {
    jest.useRealTimers();
    jest.restoreAllMocks();
  });

  const setup = (over: Record<string, unknown> = {}) => {
    const onClose = jest.fn();
    const onConfirm = jest.fn().mockResolvedValue('https://share/abc');
    render(<ShareConfirmDialog open onClose={onClose} onConfirm={onConfirm} isDarkMode={false} selectedCount={3} {...(over as any)} />);
    return { onClose, onConfirm };
  };

  it('закрытый диалог не рисуется', () => {
    render(<ShareConfirmDialog open={false} onClose={jest.fn()} onConfirm={jest.fn()} isDarkMode selectedCount={1} />);
    expect(screen.queryByText('Создать публичную ссылку')).toBeNull();
  });

  it('форма подтверждения показывает число выбранных пар', () => {
    setup({ isDarkMode: true });
    expect(screen.getByText('Создать публичную ссылку')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
    expect(screen.getByText('Создать и скопировать')).toBeInTheDocument();
  });

  it('создание: вызывает onConfirm, копирует ссылку, показывает её и «Скопировано» на 2 секунды', async () => {
    const { onConfirm } = setup();
    await act(async () => {
      fireEvent.click(screen.getByText('Создать и скопировать'));
    });
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(writeText).toHaveBeenCalledWith('https://share/abc');
    expect((screen.getByDisplayValue('https://share/abc') as HTMLInputElement).readOnly).toBe(true);
    expect(screen.getAllByText('Скопировано').length).toBeGreaterThan(0);
    act(() => {
      jest.advanceTimersByTime(2100);
    });
    expect(screen.queryByText('Скопировано')).toBeNull();
    expect(screen.getByText('Копировать')).toBeInTheDocument();
  });

  it('индикатор «Создание...» во время запроса', async () => {
    let resolve!: (v: string) => void;
    setup({ onConfirm: jest.fn(() => new Promise<string>((r) => (resolve = r))) });
    fireEvent.click(screen.getByText('Создать и скопировать'));
    expect(screen.getByText('Создание...')).toBeInTheDocument();
    expect(screen.getByText('Создание...').closest('button')).toBeDisabled();
    await act(async () => resolve('u'));
    expect(screen.getByDisplayValue('u')).toBeInTheDocument();
  });

  it('ошибка создания логируется, форма остаётся доступной', async () => {
    const err = jest.spyOn(console, 'error').mockImplementation(() => {});
    setup({ onConfirm: jest.fn().mockRejectedValue(new Error('boom')) });
    await act(async () => {
      fireEvent.click(screen.getByText('Создать и скопировать'));
    });
    expect(err).toHaveBeenCalledWith('Ошибка создания ссылки:', expect.any(Error));
    expect(screen.getByText('Создать и скопировать').closest('button')).not.toBeDisabled();
  });

  it('повторное копирование и закрытие сбрасывают состояние', async () => {
    const { onClose } = setup();
    await act(async () => {
      fireEvent.click(screen.getByText('Создать и скопировать'));
    });
    act(() => {
      jest.advanceTimersByTime(2100);
    });
    await act(async () => {
      fireEvent.click(screen.getByText('Копировать'));
    });
    expect(writeText).toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getAllByRole('button')[0]);
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe('ArchiveModal', () => {
  const chat = (id: string, title: string, content = '') => ({
    id,
    title,
    isArchived: true,
    messages: content ? [{ content }] : [],
  });

  beforeEach(() => {
    mockChats = [chat('a', 'Первый чат', 'про яблоки'), chat('b', 'Второй чат', 'про груши'), { id: 'x', title: 'Живой', isArchived: false, messages: [] }];
    Object.values(mockActions).forEach((f) => f.mockReset());
    mockNavigate.mockReset();
  });

  afterEach(() => jest.restoreAllMocks());

  const setup = (over: Record<string, unknown> = {}) => {
    const onClose = jest.fn();
    render(<ArchiveModal open onClose={onClose} isDarkMode={false} {...(over as any)} />);
    return { onClose };
  };

  it('показывает только архивные чаты', () => {
    setup();
    expect(screen.getByText('Первый чат')).toBeInTheDocument();
    expect(screen.getByText('Второй чат')).toBeInTheDocument();
    expect(screen.queryByText('Живой')).toBeNull();
  });

  it('пустой архив и пустой результат поиска', () => {
    mockChats = [];
    const { unmount } = render(<ArchiveModal open onClose={jest.fn()} isDarkMode />);
    expect(screen.getByText('Архив пуст')).toBeInTheDocument();
    expect(screen.queryByText('Разархивировать все архивированные чаты')).toBeNull();
    unmount();
    mockChats = [chat('a', 'Первый')];
    render(<ArchiveModal open onClose={jest.fn()} isDarkMode />);
    fireEvent.change(screen.getByPlaceholderText('Поиск в чатах'), { target: { value: 'zzz' } });
    expect(screen.getByText('Ничего не найдено')).toBeInTheDocument();
  });

  it('поиск по заголовку и тексту сообщений без учёта регистра', () => {
    setup();
    const input = screen.getByPlaceholderText('Поиск в чатах');
    fireEvent.change(input, { target: { value: 'ГРУШИ' } });
    expect(screen.queryByText('Первый чат')).toBeNull();
    expect(screen.getByText('Второй чат')).toBeInTheDocument();
    fireEvent.change(input, { target: { value: 'первый' } });
    expect(screen.getByText('Первый чат')).toBeInTheDocument();
    fireEvent.change(input, { target: { value: '   ' } });
    expect(screen.getByText('Второй чат')).toBeInTheDocument();
  });

  it('клик по чату открывает его, закрывает архив и переходит на /', () => {
    const { onClose } = setup();
    fireEvent.click(screen.getByText('Первый чат'));
    expect(mockActions.setCurrentChat).toHaveBeenCalledWith('a');
    expect(onClose).toHaveBeenCalled();
    expect(mockNavigate).toHaveBeenCalledWith('/');
  });

  it('кнопки в строке не открывают чат: разархивирование и удаление с подтверждением', () => {
    setup();
    const row = screen.getByText('Первый чат').closest('.MuiPaper-root') as HTMLElement;
    const [unarchive, del] = within(row).getAllByRole('button');
    fireEvent.click(unarchive);
    expect(mockActions.unarchiveChat).toHaveBeenCalledWith('a');
    expect(mockActions.setCurrentChat).not.toHaveBeenCalled();

    const confirm = jest.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true);
    fireEvent.click(del);
    expect(mockActions.deleteChat).not.toHaveBeenCalled();
    fireEvent.click(del);
    expect(confirm).toHaveBeenCalledWith('Вы уверены, что хотите удалить этот чат?');
    expect(mockActions.deleteChat).toHaveBeenCalledWith('a');
  });

  it('«Разархивировать все» требует подтверждения', () => {
    setup();
    const confirm = jest.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true);
    fireEvent.click(screen.getByText('Разархивировать все архивированные чаты'));
    expect(mockActions.unarchiveAllChats).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText('Разархивировать все архивированные чаты'));
    expect(mockActions.unarchiveAllChats).toHaveBeenCalledTimes(1);
    expect(confirm).toHaveBeenCalledWith('Вы уверены, что хотите разархивировать все чаты?');
  });

  it('экспорт скачивает JSON только с архивными чатами', async () => {
    jest.useFakeTimers();
    jest.setSystemTime(new Date('2025-02-03T12:00:00Z'));
    const blobs: Blob[] = [];
    (URL as any).createObjectURL = jest.fn((b: Blob) => {
      blobs.push(b);
      return 'blob:x';
    });
    (URL as any).revokeObjectURL = jest.fn();
    const names: string[] = [];
    jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      names.push(this.download);
    });
    setup();
    fireEvent.click(screen.getByText('Экспортировать все архивированные чаты'));
    expect(names).toEqual(['archived_chats_export_2025-02-03.json']);
    jest.useRealTimers();
    const text: string = await new Promise((res) => {
      const fr = new FileReader();
      fr.onload = () => res(String(fr.result));
      fr.readAsText(blobs[0]);
    });
    const data = JSON.parse(text);
    expect(data.chats.map((c: any) => c.id)).toEqual(['a', 'b']);
    expect(data.version).toBe('1.0');
    expect(data.folders).toEqual([]);
    expect((URL as any).revokeObjectURL).toHaveBeenCalledWith('blob:x');
  });

  it('крестик закрывает окно; закрытое окно не рисуется', () => {
    const { onClose } = setup();
    fireEvent.click(screen.getAllByRole('button')[0]);
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
