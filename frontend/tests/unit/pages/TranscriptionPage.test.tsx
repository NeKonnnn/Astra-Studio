import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import TranscriptionPage from '@/pages/TranscriptionPage';

// TranscriptionPage нигде не импортируется (не подключена к App), но покрываем её.
const mockNotify = jest.fn();
jest.mock('@/contexts/AppContext', () => ({
  useAppActions: () => ({ showNotification: mockNotify }),
}));
jest.mock('@/config/api', () => ({
  authFetch: (...a: unknown[]) => (require('@tests/unit/helpers/authFetchMock').authFetchMock as (...x: unknown[]) => unknown)(...a),
  getApiUrl: (p: string) => `http://api${p}`,
}));

const fetchMock = jest.fn();
const json = (data: any) => ({ json: async () => data });

function makeFile(name: string, type: string, size = 1024) {
  const f = new File(['x'], name, { type });
  Object.defineProperty(f, 'size', { value: size });
  return f;
}
const fileInput = () => document.querySelector('input[type="file"]') as HTMLInputElement;
async function pick(file: File) {
  await act(async () => {
    fireEvent.change(fileInput(), { target: { files: [file] } });
  });
}

describe('TranscriptionPage', () => {
  beforeEach(() => {
    mockNotify.mockReset();
    fetchMock.mockReset();
    (global as any).fetch = fetchMock;
    (URL as any).createObjectURL = jest.fn(() => 'blob:x');
    (URL as any).revokeObjectURL = jest.fn();
    jest.spyOn(console, 'error').mockImplementation(() => {});
  });
  afterEach(() => jest.restoreAllMocks());

  it('по умолчанию открыта вкладка файла с зоной загрузки', () => {
    render(<TranscriptionPage />);
    expect(screen.getByText('Перетащите файл сюда или нажмите для выбора')).toBeTruthy();
    expect(screen.queryByText('Транскрибация YouTube видео')).toBeNull();
  });

  it('клик по зоне открывает выбор файла', () => {
    render(<TranscriptionPage />);
    const spy = jest.spyOn(fileInput(), 'click');
    fireEvent.click(screen.getByText('Перетащите файл сюда или нажмите для выбора'));
    expect(spy).toHaveBeenCalled();
  });

  describe('выбор файла', () => {
    it('аудио/видео принимается, показывается имя и размер, кнопка «Выбрать другой файл» сбрасывает', async () => {
      render(<TranscriptionPage />);
      await pick(makeFile('речь.mp3', 'audio/mpeg', 1536));
      expect(screen.getByText('речь.mp3')).toBeTruthy();
      expect(screen.getByText('1.5 KB')).toBeTruthy();
      expect(screen.getByTestId('AudioFileIcon')).toBeTruthy();
      expect(mockNotify).toHaveBeenCalledWith('success', 'Файл речь.mp3 готов к транскрибации');
      fireEvent.click(screen.getByText('Выбрать другой файл'));
      expect(screen.getByText('Перетащите файл сюда или нажмите для выбора')).toBeTruthy();
    });

    it('видеофайл получает иконку видео, нулевой размер форматируется как «0 Byte»', async () => {
      render(<TranscriptionPage />);
      await pick(makeFile('клип.mp4', 'video/mp4', 0));
      expect(screen.getByTestId('VideocamIcon')).toBeTruthy();
      expect(screen.getByText('0 Byte')).toBeTruthy();
    });

    it('файл с допустимым расширением, но неизвестным MIME принимается с нейтральной иконкой', async () => {
      render(<TranscriptionPage />);
      await pick(makeFile('запись.flac', ''));
      expect(screen.getByText('запись.flac')).toBeTruthy();
      expect(screen.getByTestId('AudioFileIcon')).toBeTruthy();
    });

    it('не аудио/видео отклоняется', async () => {
      render(<TranscriptionPage />);
      await pick(makeFile('doc.pdf', 'application/pdf'));
      expect(mockNotify).toHaveBeenCalledWith('error', 'Поддерживаются только аудио и видео файлы');
      expect(screen.getByText('Перетащите файл сюда или нажмите для выбора')).toBeTruthy();
    });

    it('файл больше 500 МБ отклоняется', async () => {
      render(<TranscriptionPage />);
      await pick(makeFile('big.mp4', 'video/mp4', 500 * 1024 * 1024 + 1));
      expect(mockNotify).toHaveBeenCalledWith('error', 'Размер файла не должен превышать 500MB');
    });

    it('проверка типа по подстроке имени пропускает не-медиа файл (known issue)', async () => {
      // Как правильно: проверять расширение/MIME, а не вхождение «avi/mov/wav…» в имя файла.
      // Сейчас «Гавилов.pdf» (application/pdf) принимается, т.к. в имени есть «avi».
      render(<TranscriptionPage />);
      await pick(makeFile('Gavilov-avi.pdf', 'application/pdf'));
      expect(screen.getByText('Gavilov-avi.pdf')).toBeTruthy();
      expect(mockNotify).toHaveBeenCalledWith('success', 'Файл Gavilov-avi.pdf готов к транскрибации');
    });

    it('пустой выбор файла игнорируется', async () => {
      render(<TranscriptionPage />);
      await act(async () => {
        fireEvent.change(fileInput(), { target: { files: [] } });
      });
      expect(mockNotify).not.toHaveBeenCalled();
    });

    it('drag&drop: подсветка, сброс, drop берёт первый файл; пустой drop игнорируется', async () => {
      render(<TranscriptionPage />);
      const zone = screen.getByText('Перетащите файл сюда или нажмите для выбора').parentElement!;
      fireEvent.dragOver(zone);
      fireEvent.dragLeave(zone);
      await act(async () => {
        fireEvent.drop(zone, { dataTransfer: { files: [] } });
      });
      expect(mockNotify).not.toHaveBeenCalled();
      await act(async () => {
        fireEvent.drop(zone, { dataTransfer: { files: [makeFile('a.wav', 'audio/wav'), makeFile('b.wav', 'audio/wav')] } });
      });
      expect(screen.getByText('a.wav')).toBeTruthy();
      expect(screen.queryByText('b.wav')).toBeNull();
    });
  });

  describe('транскрибация файла', () => {
    it('успех: POST FormData, диалог с результатом и счётчиками', async () => {
      fetchMock.mockResolvedValue(json({ success: true, transcription: 'раз два три' }));
      render(<TranscriptionPage />);
      await pick(makeFile('a.mp3', 'audio/mpeg'));
      await act(async () => {
        fireEvent.click(screen.getByText('Начать транскрибацию'));
      });
      const [url, init] = fetchMock.mock.calls[0];
      expect(url).toBe('http://api/api/transcribe/upload');
      expect(init.method).toBe('POST');
      expect((init.body as FormData).get('file')).toHaveProperty('name', 'a.mp3');
      const dialog = screen.getByRole('dialog');
      expect(within(dialog).getByText('раз два три')).toBeTruthy();
      expect(within(dialog).getByText('11 символов')).toBeTruthy();
      expect(within(dialog).getByText('~3 слов')).toBeTruthy();
      expect(mockNotify).toHaveBeenLastCalledWith('success', 'Транскрибация завершена');
    });

    it('во время обработки виден индикатор и кнопки заблокированы', async () => {
      let resolve!: (v: any) => void;
      fetchMock.mockReturnValue(new Promise((r) => { resolve = r; }));
      render(<TranscriptionPage />);
      await pick(makeFile('a.mp3', 'audio/mpeg'));
      await act(async () => {
        fireEvent.click(screen.getByText('Начать транскрибацию'));
      });
      expect(screen.getByText('Выполняется транскрибация...')).toBeTruthy();
      expect((screen.getByText('Начать транскрибацию').closest('button') as HTMLButtonElement).disabled).toBe(true);
      expect((screen.getByText('Выбрать другой файл').closest('button') as HTMLButtonElement).disabled).toBe(true);
      await act(async () => { resolve(json({ success: false })); });
      expect(screen.queryByText('Выполняется транскрибация...')).toBeNull();
    });

    it('отказ бэкенда показывает message либо общий текст', async () => {
      fetchMock.mockResolvedValueOnce(json({ success: false, message: 'Формат не поддержан' }));
      render(<TranscriptionPage />);
      await pick(makeFile('a.mp3', 'audio/mpeg'));
      await act(async () => {
        fireEvent.click(screen.getByText('Начать транскрибацию'));
      });
      expect(mockNotify).toHaveBeenLastCalledWith('error', 'Формат не поддержан');
      fetchMock.mockResolvedValueOnce(json({ success: false }));
      await act(async () => {
        fireEvent.click(screen.getByText('Начать транскрибацию'));
      });
      expect(mockNotify).toHaveBeenLastCalledWith('error', 'Ошибка при транскрибации');
    });

    it('сетевая ошибка показывает уведомление', async () => {
      fetchMock.mockRejectedValue(new Error('net'));
      render(<TranscriptionPage />);
      await pick(makeFile('a.mp3', 'audio/mpeg'));
      await act(async () => {
        fireEvent.click(screen.getByText('Начать транскрибацию'));
      });
      expect(mockNotify).toHaveBeenLastCalledWith('error', 'Ошибка при отправке файла');
    });
  });

  describe('вкладка YouTube', () => {
    const openYoutube = () => fireEvent.click(screen.getByRole('tab', { name: /YouTube видео/ }));
    const urlBox = () => screen.getByPlaceholderText('https://www.youtube.com/watch?v=...');
    const run = () => screen.getByText('Транскрибировать').closest('button') as HTMLButtonElement;

    it('кнопка заблокирована при пустом URL; переключение вкладки сбрасывает введённое', () => {
      render(<TranscriptionPage />);
      openYoutube();
      expect(run().disabled).toBe(true);
      fireEvent.change(urlBox(), { target: { value: 'https://youtu.be/x' } });
      expect(run().disabled).toBe(false);
      fireEvent.click(screen.getByRole('tab', { name: /Загрузить файл/ }));
      openYoutube();
      expect((urlBox() as HTMLInputElement).value).toBe('');
    });

    it('чужой URL отклоняется без запроса', () => {
      render(<TranscriptionPage />);
      openYoutube();
      fireEvent.change(urlBox(), { target: { value: 'https://vimeo.com/1' } });
      fireEvent.click(run());
      expect(mockNotify).toHaveBeenCalledWith('error', 'Некорректный URL YouTube');
      expect(fetchMock).not.toHaveBeenCalled();
    });

    it('успех: POST JSON с url и результат в диалоге', async () => {
      fetchMock.mockResolvedValue(json({ success: true, transcription: 'текст видео' }));
      render(<TranscriptionPage />);
      openYoutube();
      fireEvent.change(urlBox(), { target: { value: 'https://www.youtube.com/watch?v=1' } });
      await act(async () => { fireEvent.click(run()); });
      const [url, init] = fetchMock.mock.calls[0];
      expect(url).toBe('http://api/api/transcribe/youtube');
      expect(init.headers).toEqual({ 'Content-Type': 'application/json' });
      expect(JSON.parse(init.body)).toEqual({ url: 'https://www.youtube.com/watch?v=1' });
      expect(within(screen.getByRole('dialog')).getByText('текст видео')).toBeTruthy();
      expect(mockNotify).toHaveBeenLastCalledWith('success', 'Транскрибация YouTube видео завершена');
    });

    it('отказ бэкенда и сетевая ошибка', async () => {
      render(<TranscriptionPage />);
      openYoutube();
      fireEvent.change(urlBox(), { target: { value: 'https://youtu.be/abc' } });
      fetchMock.mockResolvedValueOnce(json({ success: false, message: 'Видео приватное' }));
      await act(async () => { fireEvent.click(run()); });
      expect(mockNotify).toHaveBeenLastCalledWith('error', 'Видео приватное');
      fetchMock.mockResolvedValueOnce(json({ success: false }));
      await act(async () => { fireEvent.click(run()); });
      expect(mockNotify).toHaveBeenLastCalledWith('error', 'Ошибка при транскрибации YouTube');
      fetchMock.mockRejectedValueOnce(new Error('net'));
      await act(async () => { fireEvent.click(run()); });
      expect(mockNotify).toHaveBeenLastCalledWith('error', 'Ошибка при обработке YouTube URL');
    });
  });

  describe('диалог результата', () => {
    async function withResult(text = 'привет мир') {
      fetchMock.mockResolvedValue(json({ success: true, transcription: text }));
      render(<TranscriptionPage />);
      await pick(makeFile('a.mp3', 'audio/mpeg'));
      await act(async () => {
        fireEvent.click(screen.getByText('Начать транскрибацию'));
      });
      return screen.getByRole('dialog');
    }

    it('копирование: успех и отказ буфера', async () => {
      const dialog = await withResult();
      const writeText = jest.fn().mockResolvedValueOnce(undefined).mockRejectedValueOnce(new Error('no'));
      Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
      await act(async () => { fireEvent.click(within(dialog).getByText('Копировать')); });
      expect(writeText).toHaveBeenCalledWith('привет мир');
      expect(mockNotify).toHaveBeenLastCalledWith('success', 'Транскрипция скопирована в буфер обмена');
      await act(async () => { fireEvent.click(within(dialog).getByTitle('Копировать')); });
      expect(mockNotify).toHaveBeenLastCalledWith('error', 'Не удалось скопировать текст');
    });

    it('скачивание создаёт файл transcription_<дата>.txt', async () => {
      const dialog = await withResult();
      let download = '';
      jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
        download = this.download;
      });
      fireEvent.click(within(dialog).getByText('Скачать'));
      expect(download).toMatch(/^transcription_\d{4}-\d{2}-\d{2}\.txt$/);
      expect((URL.createObjectURL as jest.Mock).mock.calls[0][0].type).toBe('text/plain');
      fireEvent.click(within(dialog).getByTitle('Скачать'));
      expect(URL.revokeObjectURL).toHaveBeenCalledTimes(2);
    });

    it('«Закрыть» и Esc закрывают диалог', async () => {
      const dialog = await withResult();
      fireEvent.click(within(dialog).getByText('Закрыть'));
      await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
      // повторное открытие и Esc
      await act(async () => {
        fireEvent.click(screen.getByText('Начать транскрибацию'));
      });
      fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
      await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    });

    it('пустая транскрипция: заглушка, 0 символов и «~1 слов» (known issue)', async () => {
      // Как правильно: для пустого текста счётчик слов должен быть 0 (''.split(' ') даёт [''] → 1).
      const dialog = await withResult('');
      expect(within(dialog).getByText('Результат транскрибации появится здесь...')).toBeTruthy();
      expect(within(dialog).getByText('0 символов')).toBeTruthy();
      expect(within(dialog).getByText('~1 слов')).toBeTruthy();
    });
  });

  it('защитные проверки без файла / без URL недостижимы из UI: кнопки скрыты или заблокированы', () => {
    render(<TranscriptionPage />);
    expect(screen.queryByText('Начать транскрибацию')).toBeNull();
    fireEvent.click(screen.getByRole('tab', { name: /YouTube видео/ }));
    fireEvent.click(screen.getByText('Транскрибировать'));
    expect(fetchMock).not.toHaveBeenCalled();
    expect(mockNotify).not.toHaveBeenCalled();
  });
});
