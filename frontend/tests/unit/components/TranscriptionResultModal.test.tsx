import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import TranscriptionResultModal from '@/components/TranscriptionResultModal';

const mockNotify = jest.fn();
jest.mock('@/contexts/AppContext', () => ({
  useAppActions: () => ({ showNotification: mockNotify }),
}));

const onClose = jest.fn();
const onInsert = jest.fn();
const onResultChange = jest.fn();

const TEXT = 'SPEAKER_00: привет мир\nSPEAKER_01: как дела';

function setup(props: Partial<React.ComponentProps<typeof TranscriptionResultModal>> = {}) {
  return render(
    <TranscriptionResultModal
      open
      onClose={onClose}
      transcriptionResult={TEXT}
      onInsertToChat={onInsert}
      onResultChange={onResultChange}
      {...props}
    />,
  );
}
const panelClose = () =>
  screen.getAllByRole('button', { name: 'Закрыть' }).find((b) => b.classList.contains('MuiButton-root')) as HTMLElement;
const btn = (icon: string) => screen.getByTestId(icon).closest('button') as HTMLElement;

beforeEach(() => {
  jest.clearAllMocks();
});

describe('TranscriptionResultModal: отображение', () => {
  it('показывает текст, число символов и слов', () => {
    setup({ transcriptionResult: 'раз два три' });
    expect(screen.getByText('раз два три')).toBeInTheDocument();
    expect(screen.getByText('11 символов')).toBeInTheDocument();
    expect(screen.getByText('~3 слов')).toBeInTheDocument();
  });

  it('пустой результат: заглушка и нули в счётчиках', () => {
    setup({ transcriptionResult: '' });
    expect(screen.getByText('Результат транскрибации появится здесь...')).toBeInTheDocument();
    expect(screen.getByText('0 символов')).toBeInTheDocument();
    expect(screen.getByText('~0 слов')).toBeInTheDocument();
  });

  it('закрытый диалог не показывается', () => {
    setup({ open: false });
    expect(screen.queryByText('Результат транскрибации')).toBeNull();
  });

  it('кнопка «Закрыть» вызывает onClose', () => {
    setup();
    fireEvent.click(btn('CloseIcon'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('кнопка настройки спикеров видна только если в тексте есть спикеры', () => {
    const { unmount } = setup({ transcriptionResult: 'просто текст' });
    expect(screen.queryByTestId('PersonIcon')).toBeNull();
    unmount();
    setup();
    expect(screen.getByTestId('PersonIcon')).toBeInTheDocument();
  });

  it('кнопка вставки в чат видна только при onInsertToChat', () => {
    const { unmount } = setup({ onInsertToChat: undefined });
    expect(screen.queryByTestId('SendIcon')).toBeNull();
    unmount();
    setup();
    expect(screen.getByTestId('SendIcon')).toBeInTheDocument();
  });
});

describe('TranscriptionResultModal: копирование, скачивание, вставка', () => {
  it('копирование пишет текст в буфер обмена и уведомляет', async () => {
    const writeText = jest.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    setup();
    await act(async () => {
      fireEvent.click(btn('ContentCopyIcon'));
    });
    expect(writeText).toHaveBeenCalledWith(TEXT);
    expect(mockNotify).toHaveBeenCalledWith('success', 'Транскрипция скопирована в буфер обмена');
  });

  it('ошибка буфера обмена: уведомление об ошибке', async () => {
    Object.assign(navigator, { clipboard: { writeText: jest.fn().mockRejectedValue(new Error('denied')) } });
    setup();
    await act(async () => {
      fireEvent.click(btn('ContentCopyIcon'));
    });
    expect(mockNotify).toHaveBeenCalledWith('error', 'Не удалось скопировать текст');
  });

  it('копирование пустого результата отправляет пустую строку', async () => {
    const writeText = jest.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    setup({ transcriptionResult: '' });
    await act(async () => {
      fireEvent.click(btn('ContentCopyIcon'));
    });
    expect(writeText).toHaveBeenCalledWith('');
  });

  it('скачивание создаёт txt-файл transcription_<дата>.txt с текстом и освобождает URL', async () => {
    const createUrl = jest.fn().mockReturnValue('blob:x');
    const revoke = jest.fn();
    (URL as any).createObjectURL = createUrl;
    (URL as any).revokeObjectURL = revoke;
    const clicked: HTMLAnchorElement[] = [];
    const clickSpy = jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      clicked.push(this);
    });
    setup();
    fireEvent.click(btn('DownloadIcon'));
    const blob: Blob = createUrl.mock.calls[0][0];
    expect(blob.type).toBe('text/plain');
    expect(blob.size).toBe(new Blob([TEXT]).size);
    expect(clicked[0].download).toMatch(/^transcription_\d{4}-\d{2}-\d{2}\.txt$/);
    expect(clicked[0].href).toBe('blob:x');
    expect(revoke).toHaveBeenCalledWith('blob:x');
    clickSpy.mockRestore();
  });

  it('скачивание пустого результата создаёт пустой файл', () => {
    const createUrl = jest.fn().mockReturnValue('blob:y');
    (URL as any).createObjectURL = createUrl;
    (URL as any).revokeObjectURL = jest.fn();
    const clickSpy = jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    setup({ transcriptionResult: '' });
    fireEvent.click(btn('DownloadIcon'));
    expect((createUrl.mock.calls[0][0] as Blob).size).toBe(0);
    clickSpy.mockRestore();
  });

  it('вставка в чат передаёт текст, уведомляет и закрывает диалог', () => {
    setup();
    fireEvent.click(btn('SendIcon'));
    expect(onInsert).toHaveBeenCalledWith(TEXT);
    expect(mockNotify).toHaveBeenCalledWith('success', 'Текст вставлен в поле ввода чата');
    expect(onClose).toHaveBeenCalled();
  });

  it('вставка пустого результата ничего не делает', () => {
    setup({ transcriptionResult: '' });
    fireEvent.click(btn('SendIcon'));
    expect(onInsert).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    expect(mockNotify).not.toHaveBeenCalled();
  });
});

describe('TranscriptionResultModal: имена спикеров', () => {
  const openSettings = () => fireEvent.click(btn('PersonIcon'));
  const nameField = (speaker: string) => screen.getByPlaceholderText(`Введите имя для ${speaker}`) as HTMLInputElement;

  it('панель показывает обнаруженных спикеров (все шаблоны) и их количество', () => {
    setup({ transcriptionResult: 'SPEAKER_SPEAKER_03 а SPEAKER_01 б Speaker_A в Спикер_2 г' });
    openSettings();
    expect(screen.getByText('Настройка имен спикеров')).toBeInTheDocument();
    ['SPEAKER_SPEAKER_03', 'SPEAKER_01', 'Speaker_A', 'Спикер_2'].forEach((s) => {
      expect(screen.getByText(`${s}:`)).toBeInTheDocument();
    });
  });

  it('один спикер - подпись в единственном числе', () => {
    setup({ transcriptionResult: 'SPEAKER_00: привет' });
    openSettings();
    expect(screen.getByText('1 спикер')).toBeInTheDocument();
    expect(screen.getByText('SPEAKER_00:')).toBeInTheDocument();
  });

  it('панель закрывается крестиком и кнопкой «Закрыть»', () => {
    setup();
    openSettings();
    fireEvent.click(screen.getAllByTestId('CloseIcon')[0].closest('button')!);
    expect(screen.queryByText('Настройка имен спикеров')).toBeNull();
    openSettings();
    fireEvent.click(panelClose());
    expect(screen.queryByText('Настройка имен спикеров')).toBeNull();
  });

  it('применение имён подставляет их в исходный текст и передаёт в onResultChange', () => {
    setup();
    openSettings();
    fireEvent.change(nameField('SPEAKER_00'), { target: { value: 'Анна' } });
    fireEvent.change(nameField('SPEAKER_01'), { target: { value: '   ' } }); // пустое имя игнорируется
    fireEvent.click(screen.getByRole('button', { name: /Применить имена/ }));
    expect(onResultChange).toHaveBeenCalledWith('Анна: привет мир\nSPEAKER_01: как дела');
    expect(mockNotify).toHaveBeenCalledWith('success', 'Имена спикеров применены');
  });

  it('повторное применение берёт оригинал, а не уже переименованный текст', () => {
    const r = setup();
    openSettings();
    fireEvent.change(nameField('SPEAKER_00'), { target: { value: 'Анна' } });
    fireEvent.click(screen.getByRole('button', { name: /Применить имена/ }));
    r.rerender(
      <TranscriptionResultModal
        open
        onClose={onClose}
        transcriptionResult="Анна: привет мир\nSPEAKER_01: как дела"
        onResultChange={onResultChange}
      />,
    );
    fireEvent.change(nameField('SPEAKER_00'), { target: { value: 'Борис' } });
    fireEvent.click(screen.getByRole('button', { name: /Применить имена/ }));
    expect(onResultChange).toHaveBeenLastCalledWith('Борис: привет мир\nSPEAKER_01: как дела');
  });

  it('применение без onResultChange не падает', () => {
    setup({ onResultChange: undefined });
    openSettings();
    fireEvent.click(screen.getByRole('button', { name: /Применить имена/ }));
    expect(mockNotify).toHaveBeenCalledWith('success', 'Имена спикеров применены');
  });

  it('добавление спикера вручную: пустой идентификатор - кнопка заблокирована, Enter предупреждает', () => {
    setup();
    openSettings();
    const add = screen.getByRole('button', { name: /Добавить/ });
    expect(add).toBeDisabled();
    fireEvent.keyPress(screen.getByPlaceholderText('Например: SPEAKER_SPEAKER_05'), { key: 'Enter', charCode: 13 });
    expect(mockNotify).toHaveBeenCalledWith('warning', 'Введите идентификатор спикера');
  });

  it('добавление нового спикера, затем дубликат', () => {
    setup();
    openSettings();
    const field = screen.getByPlaceholderText('Например: SPEAKER_SPEAKER_05');
    fireEvent.change(field, { target: { value: 'SPEAKER_07' } });
    fireEvent.click(screen.getByRole('button', { name: /Добавить/ }));
    expect(mockNotify).toHaveBeenCalledWith('success', 'Спикер добавлен');
    expect(screen.getByText('SPEAKER_07:')).toBeInTheDocument();
    expect((field as HTMLInputElement).value).toBe('');
    fireEvent.change(field, { target: { value: 'SPEAKER_07' } });
    fireEvent.click(screen.getByRole('button', { name: /Добавить/ }));
    expect(mockNotify).toHaveBeenLastCalledWith('warning', 'Такой спикер уже существует');
  });

  it('удаление спикера из списка', () => {
    setup();
    openSettings();
    fireEvent.click(screen.getAllByTitle('Удалить спикера')[0]);
    expect(screen.queryByText('SPEAKER_00:')).toBeNull();
    expect(mockNotify).toHaveBeenCalledWith('info', 'Спикер удален из списка');
  });

  it('если спикеров не осталось, показывается подсказка', () => {
    setup({ transcriptionResult: 'SPEAKER_00: привет' });
    openSettings();
    fireEvent.click(screen.getByTitle('Удалить спикера'));
    expect(screen.getByText(/Спикеры не найдены/)).toBeInTheDocument();
    expect(screen.getByText('0 спикеров')).toBeInTheDocument();
  });

  it('введённые имена сохраняются при повторном открытии панели', () => {
    setup();
    openSettings();
    fireEvent.change(nameField('SPEAKER_00'), { target: { value: 'Анна' } });
    fireEvent.click(panelClose());
    openSettings();
    expect(nameField('SPEAKER_00').value).toBe('Анна');
  });

  it('закрытие диалога сбрасывает исходный текст и панель настроек', () => {
    const r = setup();
    openSettings();
    r.rerender(
      <TranscriptionResultModal open={false} onClose={onClose} transcriptionResult={TEXT} onResultChange={onResultChange} />,
    );
    r.rerender(
      <TranscriptionResultModal open onClose={onClose} transcriptionResult={TEXT} onResultChange={onResultChange} />,
    );
    expect(screen.queryByText('Настройка имен спикеров')).toBeNull();
  });

  it('подстановка имени для SPEAKER_1 не портит SPEAKER_10 (BUG-FE-65)', () => {
    setup({ transcriptionResult: 'SPEAKER_1: а\nSPEAKER_10: б' });
    openSettings();
    fireEvent.change(nameField('SPEAKER_1'), { target: { value: 'Аня' } });
    fireEvent.click(screen.getByRole('button', { name: /Применить имена/ }));
    expect(onResultChange).toHaveBeenCalledWith('Аня: а\nSPEAKER_10: б');
  });

  it('SPEAKER_SPEAKER_01 — один спикер, без вложенных SPEAKER_01 и SPEAKER_SPEAKER (BUG-FE-65)', () => {
    setup({ transcriptionResult: 'SPEAKER_SPEAKER_01: привет' });
    openSettings();
    expect(screen.getByText('SPEAKER_SPEAKER_01:')).toBeInTheDocument();
    expect(screen.queryByText('SPEAKER_01:')).toBeNull();
    expect(screen.queryByText('SPEAKER_SPEAKER:')).toBeNull();
    expect(screen.getByText('1 спикер')).toBeInTheDocument();
  });
});
