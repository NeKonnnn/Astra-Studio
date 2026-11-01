import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import TranscriptionSidebarSection, {
  ASTRA_INSERT_CHAT_TEXT,
  ASTRA_INSERT_CHAT_TEXT_KEY,
  dispatchInsertChatText,
} from '@/components/right_bar/TranscriptionSidebarSection';

const mockNotify = jest.fn();
const mockNavigate = jest.fn();
const mockIncrement = jest.fn();

jest.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate,
}));

jest.mock('@/config/api', () => ({
  authFetch: (...a: unknown[]) => (require('@tests/unit/helpers/authFetchMock').authFetchMock as (...x: unknown[]) => unknown)(...a),
  getApiUrl: (p: string) => `http://api.test${p}`,
  API_ENDPOINTS: {
    TRANSCRIBE_UPLOAD: '/api/transcribe/upload',
    TRANSCRIBE_YOUTUBE: '/api/transcribe/youtube',
  },
}));

jest.mock('@/contexts/AppContext', () => ({
  useAppActions: () => ({ showNotification: mockNotify }),
}));

jest.mock('@/utils/tabNotifications', () => ({
  incrementTabNotification: () => mockIncrement(),
}));

// Модалка результата подменена простым фейком, который отдаёт свои пропсы тесту.
jest.mock('@/components/TranscriptionResultModal', () => ({
  __esModule: true,
  default: (props: {
    open: boolean;
    onClose: () => void;
    transcriptionResult: string;
    onResultChange: (t: string) => void;
    onInsertToChat: (t: string) => void;
  }) =>
    props.open ? (
      <div data-testid="result-modal">
        <span data-testid="result-text">{props.transcriptionResult}</span>
        <button onClick={props.onClose}>close-modal</button>
        <button onClick={() => props.onResultChange('изменённый текст')}>edit-result</button>
        <button onClick={() => props.onInsertToChat(props.transcriptionResult)}>insert-chat</button>
      </div>
    ) : null,
}));

const fetchMock = jest.fn();

function jsonResponse(body: unknown, init: { ok?: boolean; status?: number } = {}) {
  return {
    ok: init.ok ?? true,
    status: init.status ?? 200,
    json: () => Promise.resolve(body),
  };
}

function fileInput(container: HTMLElement) {
  return container.querySelector('input[type="file"]') as HTMLInputElement;
}

function pickFile(container: HTMLElement, file: File) {
  fireEvent.change(fileInput(container), { target: { files: [file] } });
}

const audio = () => new File(['x'], 'rec.mp3', { type: 'audio/mpeg' });

beforeEach(() => {
  mockNotify.mockReset();
  mockNavigate.mockReset();
  mockIncrement.mockReset();
  fetchMock.mockReset();
  (global as unknown as { fetch: unknown }).fetch = fetchMock;
  sessionStorage.clear();
  window.history.pushState({}, '', '/');
});

describe('dispatchInsertChatText', () => {
  it('кладёт текст в sessionStorage и рассылает событие', () => {
    const handler = jest.fn();
    window.addEventListener(ASTRA_INSERT_CHAT_TEXT, handler);
    dispatchInsertChatText('привет');
    window.removeEventListener(ASTRA_INSERT_CHAT_TEXT, handler);
    expect(sessionStorage.getItem(ASTRA_INSERT_CHAT_TEXT_KEY)).toBe('привет');
    expect(handler).toHaveBeenCalledTimes(1);
    expect((handler.mock.calls[0][0] as CustomEvent).detail).toEqual({ text: 'привет' });
  });

  it('если sessionStorage недоступен, всё равно рассылает событие', () => {
    const spy = jest.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota');
    });
    const handler = jest.fn();
    window.addEventListener(ASTRA_INSERT_CHAT_TEXT, handler);
    expect(() => dispatchInsertChatText('t')).not.toThrow();
    window.removeEventListener(ASTRA_INSERT_CHAT_TEXT, handler);
    spy.mockRestore();
    expect(handler).toHaveBeenCalledTimes(1);
  });
});

describe('TranscriptionSidebarSection', () => {
  it('open=false: форма не отображается', () => {
    render(<TranscriptionSidebarSection open={false} />);
    expect(screen.queryByText('Загрузить файл')).toBeNull();
  });

  it('open=true: показывает форму, кнопка YouTube отключена при пустой ссылке', () => {
    render(<TranscriptionSidebarSection open />);
    expect(screen.getByText('Загрузить файл')).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Транскрибировать' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('кнопка «Загрузить файл» открывает выбор файла', () => {
    const { container } = render(<TranscriptionSidebarSection open />);
    const click = jest.spyOn(fileInput(container), 'click');
    fireEvent.click(screen.getByRole('button', { name: /Загрузить файл/ }));
    expect(click).toHaveBeenCalled();
  });

  describe('загрузка файла', () => {
    it('пустой выбор файлов игнорируется', () => {
      const { container } = render(<TranscriptionSidebarSection open />);
      fireEvent.change(fileInput(container), { target: { files: [] } });
      expect(fetchMock).not.toHaveBeenCalled();
      expect(mockNotify).not.toHaveBeenCalled();
    });

    it('недопустимый тип файла -> ошибка, запрос не отправляется', () => {
      const { container } = render(<TranscriptionSidebarSection open />);
      pickFile(container, new File(['x'], 'doc.pdf', { type: 'application/pdf' }));
      expect(mockNotify).toHaveBeenCalledWith('error', 'Поддерживаются только аудио и видео файлы');
      expect(fetchMock).not.toHaveBeenCalled();
    });

    it('тип определяется и по имени файла, если MIME пустой', async () => {
      fetchMock.mockResolvedValue(jsonResponse({ success: true, transcription: 'ok' }));
      const { container } = render(<TranscriptionSidebarSection open />);
      pickFile(container, new File(['x'], 'Запись.FLAC', { type: '' }));
      await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    });

    it('файл больше 5GB отклоняется', () => {
      const { container } = render(<TranscriptionSidebarSection open />);
      const big = audio();
      Object.defineProperty(big, 'size', { value: 5 * 1024 * 1024 * 1024 + 1 });
      pickFile(container, big);
      expect(mockNotify).toHaveBeenCalledWith('error', 'Размер файла не должен превышать 5GB');
      expect(fetchMock).not.toHaveBeenCalled();
    });

    it('успех: отправляет FormData, уведомляет, показывает «Посмотреть результат»', async () => {
      fetchMock.mockResolvedValue(
        jsonResponse({ success: true, transcription: 'Привет, мир', transcription_id: 'srv-1' }),
      );
      const { container } = render(<TranscriptionSidebarSection open />);
      pickFile(container, audio());

      await screen.findByRole('button', { name: 'Посмотреть результат' });
      const [url, init] = fetchMock.mock.calls[0];
      expect(url).toBe('http://api.test/api/transcribe/upload');
      expect(init.method).toBe('POST');
      expect(init.body.get('file').name).toBe('rec.mp3');
      expect(init.body.get('request_id')).toMatch(/^transcribe_\d+_/);
      expect(mockNotify).toHaveBeenCalledWith('success', 'Транскрибация завершена');
      expect(mockIncrement).toHaveBeenCalledTimes(1);

      fireEvent.click(screen.getByRole('button', { name: 'Посмотреть результат' }));
      expect(screen.getByTestId('result-text').textContent).toBe('Привет, мир');
    });

    it('успех без поля transcription -> пустой результат, кнопки результата нет', async () => {
      fetchMock.mockResolvedValue(jsonResponse({ success: true }));
      const { container } = render(<TranscriptionSidebarSection open />);
      pickFile(container, audio());
      await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('success', 'Транскрибация завершена'));
      expect(screen.queryByRole('button', { name: 'Посмотреть результат' })).toBeNull();
    });

    it('success=false -> показывает message сервера', async () => {
      fetchMock.mockResolvedValue(jsonResponse({ success: false, message: 'Нет речи' }));
      const { container } = render(<TranscriptionSidebarSection open />);
      pickFile(container, audio());
      await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('error', 'Нет речи'));
      expect(mockIncrement).not.toHaveBeenCalled();
    });

    it('success=false без message -> сообщение по умолчанию', async () => {
      fetchMock.mockResolvedValue(jsonResponse({ success: false }));
      const { container } = render(<TranscriptionSidebarSection open />);
      pickFile(container, audio());
      await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка при транскрибации'));
    });

    it('HTTP-ошибка: detail из тела ответа показывается пользователю', async () => {
      fetchMock.mockResolvedValue(jsonResponse({ detail: 'Слишком длинное аудио' }, { ok: false, status: 413 }));
      const { container } = render(<TranscriptionSidebarSection open />);
      pickFile(container, audio());
      await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('error', 'Слишком длинное аудио'));
    });

    it('HTTP-ошибка с нечитаемым телом -> «Ошибка при транскрибации»', async () => {
      fetchMock.mockResolvedValue({
        ok: false,
        status: 500,
        json: () => Promise.reject(new Error('bad json')),
      });
      const { container } = render(<TranscriptionSidebarSection open />);
      pickFile(container, audio());
      await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка при транскрибации'));
    });

    it('HTTP-ошибка без detail -> «Ошибка при транскрибации»', async () => {
      fetchMock.mockResolvedValue(jsonResponse({}, { ok: false, status: 500 }));
      const { container } = render(<TranscriptionSidebarSection open />);
      pickFile(container, audio());
      await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка при транскрибации'));
    });

    it('статус 499 -> info «Транскрибация была остановлена»', async () => {
      fetchMock.mockResolvedValue(jsonResponse({ detail: 'Транскрибация остановлена' }, { ok: false, status: 499 }));
      const { container } = render(<TranscriptionSidebarSection open />);
      pickFile(container, audio());
      await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('info', 'Транскрибация была остановлена'));
      expect(mockNotify).not.toHaveBeenCalledWith('error', expect.anything());
    });

    it('статус 499 с нечитаемым телом и пустым detail тоже даёт info', async () => {
      fetchMock.mockResolvedValueOnce({
        ok: false,
        status: 499,
        json: () => Promise.reject(new Error('x')),
      });
      const { container } = render(<TranscriptionSidebarSection open />);
      pickFile(container, audio());
      await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('info', 'Транскрибация была остановлена'));
    });

    it('статус 499 с пустым detail -> текст по умолчанию, тоже info', async () => {
      fetchMock.mockResolvedValue(jsonResponse({ detail: '' }, { ok: false, status: 499 }));
      const { container } = render(<TranscriptionSidebarSection open />);
      pickFile(container, audio());
      await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('info', 'Транскрибация была остановлена'));
    });

    it('сетевая ошибка -> текст ошибки; без message — «Ошибка при отправке файла»', async () => {
      fetchMock.mockRejectedValueOnce(new Error('Network down'));
      const { container } = render(<TranscriptionSidebarSection open />);
      pickFile(container, audio());
      await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('error', 'Network down'));

      mockNotify.mockReset();
      fetchMock.mockRejectedValueOnce({});
      pickFile(container, audio());
      await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка при отправке файла'));
    });

    it('во время транскрибации показываются индикатор и кнопка «Остановить»; ввод заблокирован', async () => {
      let resolve!: (v: unknown) => void;
      fetchMock.mockReturnValue(new Promise((r) => { resolve = r; }));
      const { container } = render(<TranscriptionSidebarSection open />);
      pickFile(container, audio());
      expect(await screen.findByText('Транскрибация идёт...')).toBeTruthy();
      expect((screen.getByRole('button', { name: /Загрузить файл/ }) as HTMLButtonElement).disabled).toBe(true);
      expect((screen.getByPlaceholderText(/youtube/) as HTMLInputElement).disabled).toBe(true);
      expect((screen.getByRole('button', { name: /Остановить/ }) as HTMLButtonElement).disabled).toBe(false);
      await act(async () => {
        resolve(jsonResponse({ success: false, message: 'm' }));
      });
      expect(screen.queryByText('Транскрибация идёт...')).toBeNull();
    });
  });

  describe('остановка', () => {
    async function startPending() {
      let resolveUpload!: (v: unknown) => void;
      fetchMock.mockImplementationOnce(() => new Promise((r) => { resolveUpload = r; }));
      const utils = render(<TranscriptionSidebarSection open />);
      pickFile(utils.container, audio());
      await screen.findByText('Транскрибация идёт...');
      const reqId = fetchMock.mock.calls[0][1].body.get('request_id');
      return { ...utils, reqId, resolveUpload: () => resolveUpload(jsonResponse({}, { ok: false, status: 499 })) };
    }

    it('успешная остановка отправляет transcription_id и показывает info', async () => {
      const { reqId } = await startPending();
      fetchMock.mockResolvedValueOnce(jsonResponse({ success: true }));
      fireEvent.click(screen.getByRole('button', { name: /Остановить/ }));
      await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('info', 'Транскрибация остановлена'));
      const [url, init] = fetchMock.mock.calls[1];
      expect(url).toBe('http://api.test/api/transcribe/stop');
      expect(JSON.parse(init.body)).toEqual({ transcription_id: reqId });
      expect(screen.queryByText('Транскрибация идёт...')).toBeNull();
    });

    it('отказ сервера: показывается message, без message — «Ошибка остановки»', async () => {
      await startPending();
      fetchMock.mockResolvedValueOnce(jsonResponse({ success: false, message: 'Нельзя' }));
      fireEvent.click(screen.getByRole('button', { name: /Остановить/ }));
      await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('error', 'Нельзя'));
    });

    it('отказ сервера без message -> «Ошибка остановки»', async () => {
      await startPending();
      fetchMock.mockResolvedValueOnce(jsonResponse({ success: false }));
      fireEvent.click(screen.getByRole('button', { name: /Остановить/ }));
      await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка остановки'));
    });

    it('сбой сети при остановке -> «Ошибка при остановке транскрибации»', async () => {
      await startPending();
      fetchMock.mockRejectedValueOnce(new Error('boom'));
      fireEvent.click(screen.getByRole('button', { name: /Остановить/ }));
      await waitFor(() =>
        expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка при остановке транскрибации'),
      );
      expect(screen.queryByText('Транскрибация идёт...')).toBeNull();
    });
  });

  describe('YouTube', () => {
    function typeUrl(url: string) {
      fireEvent.change(screen.getByPlaceholderText(/youtube/), { target: { value: url } });
    }
    const go = () => fireEvent.click(screen.getByRole('button', { name: 'Транскрибировать' }));

    it('пустая ссылка из пробелов — кнопка отключена и запроса нет', () => {
      render(<TranscriptionSidebarSection open />);
      typeUrl('   ');
      expect((screen.getByRole('button', { name: 'Транскрибировать' }) as HTMLButtonElement).disabled).toBe(true);
    });

    it('не YouTube-ссылка -> ошибка валидации без запроса', () => {
      render(<TranscriptionSidebarSection open />);
      typeUrl('https://vimeo.com/123');
      go();
      expect(mockNotify).toHaveBeenCalledWith('error', 'Некорректный URL YouTube');
      expect(fetchMock).not.toHaveBeenCalled();
    });

    it('успех (youtube.com): отправляет обрезанный URL и показывает результат', async () => {
      fetchMock.mockResolvedValue(jsonResponse({ success: true, transcription: 'Текст ролика' }));
      render(<TranscriptionSidebarSection open />);
      typeUrl('  https://www.youtube.com/watch?v=abc  ');
      go();
      await screen.findByRole('button', { name: 'Посмотреть результат' });
      const [url, init] = fetchMock.mock.calls[0];
      expect(url).toBe('http://api.test/api/transcribe/youtube');
      expect(JSON.parse(init.body)).toEqual({ url: 'https://www.youtube.com/watch?v=abc' });
      expect(mockNotify).toHaveBeenCalledWith('success', 'Транскрибация YouTube завершена');
      expect(mockIncrement).toHaveBeenCalledTimes(1);
    });

    it('успех (youtu.be) без поля transcription -> пустой результат', async () => {
      fetchMock.mockResolvedValue(jsonResponse({ success: true }));
      render(<TranscriptionSidebarSection open />);
      typeUrl('https://youtu.be/abc');
      go();
      await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('success', 'Транскрибация YouTube завершена'));
      expect(screen.queryByRole('button', { name: 'Посмотреть результат' })).toBeNull();
    });

    it('success=false: message сервера или значение по умолчанию', async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse({ success: false, message: 'Видео недоступно' }));
      render(<TranscriptionSidebarSection open />);
      typeUrl('https://youtu.be/abc');
      go();
      await waitFor(() => expect(mockNotify).toHaveBeenCalledWith('error', 'Видео недоступно'));

      mockNotify.mockReset();
      fetchMock.mockResolvedValueOnce(jsonResponse({ success: false }));
      go();
      await waitFor(() =>
        expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка при транскрибации YouTube'),
      );
    });

    it('сбой сети -> «Ошибка при обработке YouTube URL»', async () => {
      fetchMock.mockRejectedValue(new Error('x'));
      render(<TranscriptionSidebarSection open />);
      typeUrl('https://youtu.be/abc');
      go();
      await waitFor(() =>
        expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка при обработке YouTube URL'),
      );
    });
  });

  describe('модалка результата и вставка в чат', () => {
    async function withResult() {
      fetchMock.mockResolvedValue(jsonResponse({ success: true, transcription: 'Готовый текст' }));
      const utils = render(<TranscriptionSidebarSection open />);
      pickFile(utils.container, audio());
      fireEvent.click(await screen.findByRole('button', { name: 'Посмотреть результат' }));
    }

    it('модалка открывается, закрывается и редактирует результат', async () => {
      await withResult();
      expect(screen.getByTestId('result-text').textContent).toBe('Готовый текст');
      fireEvent.click(screen.getByText('edit-result'));
      expect(screen.getByTestId('result-text').textContent).toBe('изменённый текст');
      fireEvent.click(screen.getByText('close-modal'));
      expect(screen.queryByTestId('result-modal')).toBeNull();
    });

    it('на главной странице вставка в чат не делает навигацию', async () => {
      await withResult();
      const handler = jest.fn();
      window.addEventListener(ASTRA_INSERT_CHAT_TEXT, handler);
      fireEvent.click(screen.getByText('insert-chat'));
      window.removeEventListener(ASTRA_INSERT_CHAT_TEXT, handler);
      expect(handler).toHaveBeenCalledTimes(1);
      expect(sessionStorage.getItem(ASTRA_INSERT_CHAT_TEXT_KEY)).toBe('Готовый текст');
      expect(mockNavigate).not.toHaveBeenCalled();
    });

    it.each(['/project/12', '/voice', '/documents', '/search'])(
      'на странице %s вставка в чат не уводит пользователя',
      async (path) => {
        window.history.pushState({}, '', path);
        await withResult();
        fireEvent.click(screen.getByText('insert-chat'));
        expect(mockNavigate).not.toHaveBeenCalled();
      },
    );

    it('на странице, где нет поля чата (/gallery), переходит на «/»', async () => {
      window.history.pushState({}, '', '/gallery');
      await withResult();
      fireEvent.click(screen.getByText('insert-chat'));
      expect(mockNavigate).toHaveBeenCalledWith('/');
    });
  });
});
