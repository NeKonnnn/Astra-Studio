import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import '@testing-library/jest-dom';
import TranscriptionModal from '@/components/TranscriptionModal';

const mockNotify = jest.fn();
const mockTabNotify = jest.fn();

jest.mock('@/contexts/AppContext', () => ({ useAppActions: () => ({ showNotification: mockNotify }) }));
jest.mock('@/utils/tabNotifications', () => ({ incrementTabNotification: () => mockTabNotify() }));
jest.mock('@/config/api', () => ({
  authFetch: (...a: unknown[]) => (require('@tests/unit/helpers/authFetchMock').authFetchMock as (...x: unknown[]) => unknown)(...a),
  getApiUrl: (p: string) => `http://api${p}`,
  API_ENDPOINTS: { TRANSCRIBE_UPLOAD: '/api/transcribe/upload', TRANSCRIBE_YOUTUBE: '/api/transcribe/youtube' },
}));

const onClose = jest.fn();
let fetchMock: jest.Mock;
type Resp = { ok?: boolean; status?: number; body?: any; badJson?: boolean };
let uploadResp: () => Promise<Resp> | Resp;
let youtubeResp: () => Promise<Resp> | Resp;
let stopResp: () => Promise<Resp> | Resp;

const mkResp = (r: Resp) =>
  ({
    ok: r.ok !== false,
    status: r.status ?? 200,
    json: async () => {
      if (r.badJson) throw new Error('bad');
      return r.body;
    },
  } as unknown as Response);

const audio = (name = 'talk.mp3', type = 'audio/mpeg', size?: number) => {
  const f = new File(['abc'], name, { type });
  if (size != null) Object.defineProperty(f, 'size', { value: size });
  return f;
};

function setup(props: Partial<React.ComponentProps<typeof TranscriptionModal>> = {}) {
  return render(<TranscriptionModal open onClose={onClose} {...props} />);
}
const fileInput = () => document.querySelector('input[type="file"]') as HTMLInputElement;
async function pickFile(f: File) {
  await act(async () => {
    fireEvent.change(fileInput(), { target: { files: [f] } });
  });
}
const startBtn = () => screen.getByRole('button', { name: /Начать транскрибацию/ });
async function startFile(f = audio()) {
  await pickFile(f);
  await act(async () => {
    fireEvent.click(startBtn());
  });
}

beforeEach(() => {
  jest.clearAllMocks();
  uploadResp = () => ({ body: { success: true, transcription: 'SPEAKER_00: привет мир' } });
  youtubeResp = () => ({ body: { success: true, transcription: 'текст видео' } });
  stopResp = () => ({ body: { success: true } });
  fetchMock = jest.fn(async (url: string) => {
    if (url.endsWith('/transcribe/upload')) return mkResp(await uploadResp());
    if (url.endsWith('/transcribe/youtube')) return mkResp(await youtubeResp());
    if (url.endsWith('/transcribe/stop')) return mkResp(await stopResp());
    throw new Error('unexpected ' + url);
  });
  (global as any).fetch = fetchMock;
  jest.spyOn(console, 'error').mockImplementation(() => {});
});
afterEach(() => jest.restoreAllMocks());

describe('TranscriptionModal: выбор файла', () => {
  it('закрытое окно ничего не показывает', () => {
    setup({ open: false });
    expect(screen.queryByText('Транскрибация')).toBeNull();
  });

  it('показывает зону загрузки; клик по ней открывает выбор файла', () => {
    setup();
    expect(screen.getByText('Перетащите файл сюда или нажмите для выбора')).toBeInTheDocument();
    const click = jest.spyOn(fileInput(), 'click');
    fireEvent.click(screen.getByText('Перетащите файл сюда или нажмите для выбора'));
    expect(click).toHaveBeenCalled();
  });

  it('допустимый аудиофайл принимается: показываются имя и размер, уведомление', async () => {
    setup();
    await pickFile(audio('talk.mp3', 'audio/mpeg', 2048));
    expect(screen.getByText('talk.mp3')).toBeInTheDocument();
    expect(screen.getByText('2 KB')).toBeInTheDocument();
    expect(mockNotify).toHaveBeenCalledWith('success', 'Файл talk.mp3 готов к транскрибации');
  });

  it('форматирование размера: 0 байт, байты, МБ', async () => {
    setup();
    await pickFile(audio('a.mp3', 'audio/mpeg', 0));
    expect(screen.getByText('0 Byte')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать другой файл' }));
    await pickFile(audio('b.mp3', 'audio/mpeg', 5 * 1024 * 1024));
    expect(screen.getByText('5 MB')).toBeInTheDocument();
  });

  it('видеофайл принимается (иконка видео); файл с типом, пустым в браузере, определяется по имени', async () => {
    setup();
    await pickFile(audio('clip.mp4', 'video/mp4'));
    expect(screen.getByTestId('VideocamIcon')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать другой файл' }));
    await pickFile(audio('record.wav', ''));
    expect(screen.getByText('record.wav')).toBeInTheDocument();
    expect(screen.getByTestId('AudioFileIcon')).toBeInTheDocument();
  });

  it('недопустимый тип: ошибка, файл не выбран', async () => {
    setup();
    await pickFile(audio('notes.txt', 'text/plain'));
    expect(mockNotify).toHaveBeenCalledWith('error', 'Поддерживаются только аудио и видео файлы');
    expect(screen.getByText('Перетащите файл сюда или нажмите для выбора')).toBeInTheDocument();
  });

  it('файл больше 5GB отклоняется', async () => {
    setup();
    await pickFile(audio('big.mp3', 'audio/mpeg', 5 * 1024 * 1024 * 1024 + 1));
    expect(mockNotify).toHaveBeenCalledWith('error', 'Размер файла не должен превышать 5GB');
    expect(screen.queryByText('big.mp3')).toBeNull();
  });

  it('имя файла с подстрокой формата принимается даже при чужом типе (known issue)', async () => {
    // Как должно быть: проверка по MIME-типу/расширению, report.pdf отклоняется.
    // Сейчас file.name.includes('avi') - любой файл с «avi» в имени (например, «navigation.pdf») проходит проверку.
    setup();
    await pickFile(audio('navigation.pdf', 'application/pdf'));
    expect(screen.getByText('navigation.pdf')).toBeInTheDocument();
    expect(mockNotify).toHaveBeenCalledWith('success', 'Файл navigation.pdf готов к транскрибации');
  });

  it('пустой выбор файла игнорируется', async () => {
    setup();
    await act(async () => {
      fireEvent.change(fileInput(), { target: { files: [] } });
    });
    expect(mockNotify).not.toHaveBeenCalled();
  });

  it('drag&drop: подсветка при перетаскивании, снятие при уходе, обработка первого файла при drop', async () => {
    setup();
    const zone = screen.getByText('Перетащите файл сюда или нажмите для выбора').closest('div') as HTMLElement;
    expect(fireEvent.dragOver(zone)).toBe(false);
    fireEvent.dragLeave(zone);
    await act(async () => {
      fireEvent.drop(zone, { dataTransfer: { files: [audio('drop.mp3'), audio('second.mp3')] } });
    });
    expect(screen.getByText('drop.mp3')).toBeInTheDocument();
    expect(screen.queryByText('second.mp3')).toBeNull();
  });

  it('drop без файлов ничего не делает', async () => {
    setup();
    const zone = screen.getByText('Перетащите файл сюда или нажмите для выбора').closest('div') as HTMLElement;
    fireEvent.drop(zone, { dataTransfer: { files: [] } });
    expect(mockNotify).not.toHaveBeenCalled();
  });

  it('«Выбрать другой файл» возвращает зону загрузки', async () => {
    setup();
    await pickFile(audio());
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать другой файл' }));
    expect(screen.getByText('Перетащите файл сюда или нажмите для выбора')).toBeInTheDocument();
  });

  it('смена вкладки сбрасывает выбранный файл и введённый URL', async () => {
    setup();
    await pickFile(audio());
    fireEvent.click(screen.getByRole('tab', { name: /YouTube видео/ }));
    fireEvent.change(screen.getByPlaceholderText(/youtube.com/), { target: { value: 'https://youtu.be/x' } });
    fireEvent.click(screen.getByRole('tab', { name: /Загрузить файл/ }));
    expect(screen.getByText('Перетащите файл сюда или нажмите для выбора')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: /YouTube видео/ }));
    expect((screen.getByPlaceholderText(/youtube.com/) as HTMLInputElement).value).toBe('');
  });

  it('initialTab=1 открывает вкладку YouTube', () => {
    setup({ initialTab: 1 });
    expect(screen.getByText('Транскрибация YouTube видео')).toBeInTheDocument();
  });

  it('закрытие сбрасывает состояние и вызывает onClose', async () => {
    setup();
    await pickFile(audio());
    fireEvent.click(screen.getByTestId('CloseIcon').closest('button')!);
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(screen.getByText('Перетащите файл сюда или нажмите для выбора')).toBeInTheDocument();
  });
});

describe('TranscriptionModal: транскрибация файла', () => {
  it('отправляет файл и request_id на сервер, показывает результат, уведомляет и увеличивает счётчик вкладки', async () => {
    setup();
    await startFile(audio('talk.mp3'));
    const call = fetchMock.mock.calls.find((c) => String(c[0]).endsWith('/transcribe/upload'))!;
    expect(call[0]).toBe('http://api/api/transcribe/upload');
    expect(call[1].method).toBe('POST');
    const fd = call[1].body as FormData;
    expect((fd.get('file') as File).name).toBe('talk.mp3');
    expect(String(fd.get('request_id'))).toMatch(/^transcribe_\d+_[a-z0-9]+$/);
    expect(await screen.findByText('Результат транскрибации')).toBeInTheDocument();
    expect(screen.getByText('SPEAKER_00: привет мир')).toBeInTheDocument();
    expect(mockNotify).toHaveBeenCalledWith('success', 'Транскрибация завершена');
    expect(mockTabNotify).toHaveBeenCalledTimes(1);
  });

  it('с внешними колбэками: onTranscriptionStart/Complete вызываются, внутреннее состояние не используется', async () => {
    const onStart = jest.fn();
    const onComplete = jest.fn();
    setup({ onTranscriptionStart: onStart, onTranscriptionComplete: onComplete, transcriptionResult: '' });
    await startFile();
    expect(onStart).toHaveBeenCalledTimes(1);
    expect(onComplete).toHaveBeenCalledWith('SPEAKER_00: привет мир');
  });

  it('транскрибация без выбранного файла невозможна (кнопки нет)', () => {
    setup();
    expect(screen.queryByRole('button', { name: /Начать транскрибацию/ })).toBeNull();
  });

  it('во время транскрибации показывается индикатор и кнопка остановки; кнопка старта заблокирована', async () => {
    let release: (v: Resp) => void = () => {};
    uploadResp = () => new Promise((r) => (release = r));
    setup();
    await startFile();
    expect(screen.getByText('Выполняется транскрибация...')).toBeInTheDocument();
    expect(startBtn()).toBeDisabled();
    await act(async () => {
      release({ body: { success: true, transcription: 'ok' } });
    });
    expect(screen.queryByText('Выполняется транскрибация...')).toBeNull();
  });

  it('transcription_id из ответа сохраняется, затем сбрасывается', async () => {
    uploadResp = () => ({ body: { success: true, transcription: 'ok', transcription_id: 'srv-1' } });
    setup();
    await startFile();
    expect(await screen.findByText('Результат транскрибации')).toBeInTheDocument();
  });

  it('success=false: сообщение сервера (или по умолчанию) в уведомлении и onTranscriptionError', async () => {
    const onErr = jest.fn();
    uploadResp = () => ({ body: { success: false, message: 'Формат не поддерживается' } });
    setup({ onTranscriptionError: onErr });
    await startFile();
    expect(mockNotify).toHaveBeenCalledWith('error', 'Формат не поддерживается');
    expect(onErr).toHaveBeenCalledWith('Формат не поддерживается');
    uploadResp = () => ({ body: { success: false } });
    await act(async () => {
      fireEvent.click(startBtn());
    });
    expect(mockNotify).toHaveBeenLastCalledWith('error', 'Ошибка при транскрибации');
  });

  it('success=false без колбэка onTranscriptionError', async () => {
    uploadResp = () => ({ body: { success: false } });
    setup();
    await startFile();
    expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка при транскрибации');
  });

  it('HTTP 499: «Транскрибация была остановлена» (info)', async () => {
    const onErr = jest.fn();
    uploadResp = () => ({ ok: false, status: 499, body: { detail: 'Транскрибация была остановлена клиентом' } });
    setup({ onTranscriptionError: onErr });
    await startFile();
    expect(mockNotify).toHaveBeenCalledWith('info', 'Транскрибация была остановлена');
    expect(onErr).toHaveBeenCalledWith('Транскрибация была остановлена');
  });

  it('HTTP 499 без JSON и без detail', async () => {
    uploadResp = () => ({ ok: false, status: 499, badJson: true });
    setup();
    await startFile();
    expect(mockNotify).toHaveBeenCalledWith('info', 'Транскрибация была остановлена');
    uploadResp = () => ({ ok: false, status: 499, body: {} });
    await act(async () => {
      fireEvent.click(startBtn());
    });
    expect(mockNotify).toHaveBeenLastCalledWith('info', 'Транскрибация была остановлена');
  });

  it('другая HTTP-ошибка: «Ошибка при отправке файла» и onTranscriptionError', async () => {
    const onErr = jest.fn();
    uploadResp = () => ({ ok: false, status: 500, body: { detail: 'внутренняя ошибка' } });
    setup({ onTranscriptionError: onErr });
    await startFile();
    expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка при отправке файла');
    expect(onErr).toHaveBeenCalledWith('Ошибка при отправке файла');
  });

  it('HTTP-ошибка без JSON / без detail', async () => {
    uploadResp = () => ({ ok: false, status: 500, badJson: true });
    setup();
    await startFile();
    expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка при отправке файла');
    uploadResp = () => ({ ok: false, status: 500, body: {} });
    await act(async () => {
      fireEvent.click(startBtn());
    });
    expect(mockNotify).toHaveBeenCalledTimes(3);
  });

  it('сетевое исключение: «Ошибка при отправке файла»', async () => {
    fetchMock.mockRejectedValueOnce(new Error('network'));
    setup();
    await startFile();
    expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка при отправке файла');
  });

  it('сетевое исключение с «остановлена» в тексте трактуется как остановка', async () => {
    fetchMock.mockRejectedValueOnce(new Error('Транскрибация остановлена пользователем'));
    const onErr = jest.fn();
    setup({ onTranscriptionError: onErr });
    await startFile();
    expect(mockNotify).toHaveBeenCalledWith('info', 'Транскрибация была остановлена');
    expect(onErr).toHaveBeenCalledWith('Транскрибация была остановлена');
  });

  it('внешний isTranscribing блокирует кнопки выбора файла', async () => {
    setup({ isTranscribing: true });
    expect(screen.getByText('Выполняется транскрибация...')).toBeInTheDocument();
  });
});

describe('TranscriptionModal: initialFile', () => {
  it('сразу запускает транскрибацию переданного файла, один раз для одного файла', async () => {
    const f = audio('menu.mp3');
    const r = setup({ initialFile: f });
    await act(async () => {});
    const calls = () => fetchMock.mock.calls.filter((c) => String(c[0]).endsWith('/transcribe/upload'));
    expect(calls()).toHaveLength(1);
    expect(((calls()[0][1].body as FormData).get('file') as File).name).toBe('menu.mp3');
    expect(mockNotify).toHaveBeenCalledWith('success', 'Транскрибация завершена');
    r.rerender(<TranscriptionModal open onClose={onClose} initialFile={f} />);
    await act(async () => {});
    expect(calls()).toHaveLength(1);
  });

  it('новый файл запускает новую транскрибацию; закрытие сбрасывает запомненный файл', async () => {
    const f = audio('a.mp3');
    const r = setup({ initialFile: f });
    await act(async () => {});
    r.rerender(<TranscriptionModal open={false} onClose={onClose} initialFile={f} />);
    r.rerender(<TranscriptionModal open onClose={onClose} initialFile={f} />);
    await act(async () => {});
    expect(fetchMock.mock.calls.filter((c) => String(c[0]).endsWith('/transcribe/upload'))).toHaveLength(2);
  });

  it('с внешними колбэками результат уходит в onTranscriptionComplete, ID из ответа сохраняется', async () => {
    uploadResp = () => ({ body: { success: true, transcription: 'T', transcription_id: 'x1' } });
    const onStart = jest.fn();
    const onComplete = jest.fn();
    setup({ initialFile: audio(), onTranscriptionStart: onStart, onTranscriptionComplete: onComplete });
    await act(async () => {});
    expect(onStart).toHaveBeenCalled();
    expect(onComplete).toHaveBeenCalledWith('T');
  });

  it('success=false', async () => {
    uploadResp = () => ({ body: { success: false, message: 'нет речи' } });
    const onErr = jest.fn();
    setup({ initialFile: audio(), onTranscriptionError: onErr });
    await act(async () => {});
    expect(mockNotify).toHaveBeenCalledWith('error', 'нет речи');
    expect(onErr).toHaveBeenCalledWith('нет речи');
  });

  it('success=false без message и без колбэка', async () => {
    uploadResp = () => ({ body: { success: false } });
    setup({ initialFile: audio() });
    await act(async () => {});
    expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка при транскрибации');
  });

  it('HTTP 499: информирует об остановке', async () => {
    uploadResp = () => ({ ok: false, status: 499, body: { detail: 'Стоп' } });
    const onErr = jest.fn();
    setup({ initialFile: audio(), onTranscriptionError: onErr });
    await act(async () => {});
    expect(mockNotify).toHaveBeenCalledWith('info', 'Транскрибация была остановлена');
    expect(onErr).toHaveBeenCalledWith('Транскрибация была остановлена');
  });

  it('HTTP 499 без JSON; без колбэка', async () => {
    uploadResp = () => ({ ok: false, status: 499, badJson: true });
    setup({ initialFile: audio() });
    await act(async () => {});
    expect(mockNotify).toHaveBeenCalledWith('info', 'Транскрибация была остановлена');
  });

  it('HTTP-ошибка с detail: сообщение выводится пользователю', async () => {
    uploadResp = () => ({ ok: false, status: 500, body: { detail: 'GPU занят' } });
    const onErr = jest.fn();
    setup({ initialFile: audio(), onTranscriptionError: onErr });
    await act(async () => {});
    expect(mockNotify).toHaveBeenCalledWith('error', 'GPU занят');
    expect(onErr).toHaveBeenCalledWith('GPU занят');
  });

  it('HTTP-ошибка без JSON / без detail: текст по умолчанию', async () => {
    uploadResp = () => ({ ok: false, status: 500, badJson: true });
    setup({ initialFile: audio() });
    await act(async () => {});
    expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка при транскрибации');
  });

  it('исключение без message: «Ошибка при отправке файла» и «Ошибка» для колбэка', async () => {
    fetchMock.mockRejectedValueOnce({});
    const onErr = jest.fn();
    setup({ initialFile: audio(), onTranscriptionError: onErr });
    await act(async () => {});
    expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка при отправке файла');
    expect(onErr).toHaveBeenCalledWith('Ошибка');
  });
});

describe('TranscriptionModal: YouTube', () => {
  const openYt = () => fireEvent.click(screen.getByRole('tab', { name: /YouTube видео/ }));
  const urlField = () => screen.getByPlaceholderText(/youtube.com/) as HTMLInputElement;
  const ytBtn = () => screen.getByRole('button', { name: 'Транскрибировать' });

  it('кнопка заблокирована, пока URL пуст', () => {
    setup();
    openYt();
    expect(ytBtn()).toBeDisabled();
    fireEvent.change(urlField(), { target: { value: '   ' } });
    expect(ytBtn()).toBeDisabled();
  });

  it('некорректный URL: ошибка, запрос не отправляется', () => {
    setup();
    openYt();
    fireEvent.change(urlField(), { target: { value: 'https://vimeo.com/1' } });
    fireEvent.click(ytBtn());
    expect(mockNotify).toHaveBeenCalledWith('error', 'Некорректный URL YouTube');
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('проверка URL - подстрока youtube.com, поэтому чужой адрес со строкой youtube.com принимается (known issue)', async () => {
    // Как должно быть: проверка хоста (youtube.com / youtu.be), а не подстроки.
    setup();
    openYt();
    fireEvent.change(urlField(), { target: { value: 'https://evil.example/?next=youtube.com' } });
    await act(async () => {
      fireEvent.click(ytBtn());
    });
    expect(fetchMock).toHaveBeenCalledWith('http://api/api/transcribe/youtube', expect.anything());
  });

  it('успех: POST JSON с url, результат показан, уведомление и счётчик', async () => {
    setup();
    openYt();
    fireEvent.change(urlField(), { target: { value: 'https://youtu.be/abc' } });
    await act(async () => {
      fireEvent.click(ytBtn());
    });
    const call = fetchMock.mock.calls[0];
    expect(call[0]).toBe('http://api/api/transcribe/youtube');
    expect(call[1].headers).toEqual({ 'Content-Type': 'application/json' });
    expect(JSON.parse(call[1].body)).toEqual({ url: 'https://youtu.be/abc' });
    expect(await screen.findByText('текст видео')).toBeInTheDocument();
    expect(mockNotify).toHaveBeenCalledWith('success', 'Транскрибация YouTube видео завершена');
    expect(mockTabNotify).toHaveBeenCalled();
  });

  it('с внешними колбэками: onTranscriptionStart/Complete', async () => {
    const onStart = jest.fn();
    const onComplete = jest.fn();
    setup({ onTranscriptionStart: onStart, onTranscriptionComplete: onComplete });
    openYt();
    fireEvent.change(urlField(), { target: { value: 'https://www.youtube.com/watch?v=1' } });
    await act(async () => {
      fireEvent.click(ytBtn());
    });
    expect(onStart).toHaveBeenCalled();
    expect(onComplete).toHaveBeenCalledWith('текст видео');
  });

  it('success=false: сообщение сервера и колбэк; без message - текст по умолчанию', async () => {
    const onErr = jest.fn();
    youtubeResp = () => ({ body: { success: false, message: 'Видео недоступно' } });
    setup({ onTranscriptionError: onErr });
    openYt();
    fireEvent.change(urlField(), { target: { value: 'https://youtu.be/abc' } });
    await act(async () => {
      fireEvent.click(ytBtn());
    });
    expect(mockNotify).toHaveBeenCalledWith('error', 'Видео недоступно');
    expect(onErr).toHaveBeenCalledWith('Видео недоступно');
    youtubeResp = () => ({ body: { success: false } });
    await act(async () => {
      fireEvent.click(ytBtn());
    });
    expect(mockNotify).toHaveBeenLastCalledWith('error', 'Ошибка при транскрибации YouTube');
  });

  it('success=false без колбэка ошибок', async () => {
    youtubeResp = () => ({ body: { success: false } });
    setup();
    openYt();
    fireEvent.change(urlField(), { target: { value: 'https://youtu.be/abc' } });
    await act(async () => {
      fireEvent.click(ytBtn());
    });
    expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка при транскрибации YouTube');
  });

  it('сетевая ошибка: «Ошибка при обработке YouTube URL»', async () => {
    const onErr = jest.fn();
    fetchMock.mockRejectedValueOnce(new Error('down'));
    setup({ onTranscriptionError: onErr });
    openYt();
    fireEvent.change(urlField(), { target: { value: 'https://youtu.be/abc' } });
    await act(async () => {
      fireEvent.click(ytBtn());
    });
    expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка при обработке YouTube URL');
    expect(onErr).toHaveBeenCalledWith('Ошибка при обработке YouTube URL');
  });

  it('сетевая ошибка без колбэка', async () => {
    fetchMock.mockRejectedValueOnce(new Error('down'));
    setup();
    openYt();
    fireEvent.change(urlField(), { target: { value: 'https://youtu.be/abc' } });
    await act(async () => {
      fireEvent.click(ytBtn());
    });
    expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка при обработке YouTube URL');
  });
});

describe('TranscriptionModal: остановка', () => {
  async function startHanging() {
    let release: (v: Resp) => void = () => {};
    uploadResp = () => new Promise((r) => (release = r));
    setup();
    await startFile();
    return release;
  }
  const stopBtn = () => screen.getByRole('button', { name: /Остановить транскрибацию/ });

  it('отправляет id запроса из загрузки на /api/transcribe/stop', async () => {
    const release = await startHanging();
    const upload = fetchMock.mock.calls.find((c) => String(c[0]).endsWith('/transcribe/upload'))!;
    const requestId = (upload[1].body as FormData).get('request_id');
    await act(async () => {
      fireEvent.click(stopBtn());
    });
    const stop = fetchMock.mock.calls.find((c) => String(c[0]).endsWith('/transcribe/stop'))!;
    expect(stop[1].method).toBe('POST');
    expect(JSON.parse(stop[1].body)).toEqual({ transcription_id: requestId });
    expect(mockNotify).toHaveBeenCalledWith('info', 'Транскрибация остановлена');
    await act(async () => {
      release({ ok: false, status: 499, body: {} });
    });
  });

  it('успешная остановка вызывает onTranscriptionError с пометкой пользователя', async () => {
    const onErr = jest.fn();
    let release: (v: Resp) => void = () => {};
    uploadResp = () => new Promise((r) => (release = r));
    setup({ onTranscriptionError: onErr });
    await startFile();
    await act(async () => {
      fireEvent.click(stopBtn());
    });
    expect(onErr).toHaveBeenCalledWith('Транскрибация была остановлена пользователем');
    await act(async () => {
      release({ ok: false, status: 499, body: {} });
    });
  });

  it('отказ сервера: сообщение об ошибке (или по умолчанию)', async () => {
    const release = await startHanging();
    stopResp = () => ({ body: { success: false, message: 'Уже завершено' } });
    await act(async () => {
      fireEvent.click(stopBtn());
    });
    expect(mockNotify).toHaveBeenCalledWith('error', 'Уже завершено');
    stopResp = () => ({ body: { success: false } });
    await act(async () => {
      fireEvent.click(stopBtn());
    });
    expect(mockNotify).toHaveBeenLastCalledWith('error', 'Ошибка при остановке транскрибации');
    await act(async () => {
      release({ ok: false, status: 499, body: {} });
    });
  });

  it('сетевая ошибка при остановке', async () => {
    const release = await startHanging();
    stopResp = () => {
      throw new Error('down');
    };
    await act(async () => {
      fireEvent.click(stopBtn());
    });
    expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка при отправке команды остановки');
    await act(async () => {
      release({ ok: false, status: 499, body: {} });
    });
  });

  it('без id транскрибации (внешний isTranscribing) кнопка остановки заблокирована', () => {
    setup({ isTranscribing: true });
    expect(stopBtn()).toBeDisabled();
  });
});

describe('TranscriptionModal: окно результата', () => {
  const TEXT = 'SPEAKER_00: привет мир\nSPEAKER_01: как дела';
  const btn = (icon: string) => screen.getByTestId(icon).closest('button') as HTMLElement;
  const resultDialog = () => screen.getByText('Результат транскрибации').closest('[role="dialog"]') as HTMLElement;

  async function openResult(props: Partial<React.ComponentProps<typeof TranscriptionModal>> = {}) {
    setup({ transcriptionResult: TEXT, ...props });
    fireEvent.click(screen.getByRole('button', { name: /Просмотреть результат/ }));
    await screen.findByText('Результат транскрибации');
  }

  it('при готовом внешнем результате предлагается просмотреть его', () => {
    setup({ transcriptionResult: TEXT });
    expect(screen.getByText(/Транскрибация завершена! Нажмите кнопку ниже/)).toBeInTheDocument();
  });

  it('пока идёт транскрибация, предложение просмотра скрыто', () => {
    setup({ transcriptionResult: TEXT, isTranscribing: true });
    expect(screen.queryByRole('button', { name: /Просмотреть результат/ })).toBeNull();
  });

  it('показывает текст, число символов и слов; для пустого текста - заглушка', async () => {
    await openResult();
    expect(within(resultDialog()).getByText(/SPEAKER_00: привет мир/)).toBeInTheDocument();
    expect(screen.getByText(`${TEXT.length} символов`)).toBeInTheDocument();
  });

  it('пустой результат во внутреннем состоянии даёт «~1 слов» (known issue)', async () => {
    // Как должно быть: «~0 слов» (в TranscriptionResultModal пустая строка считается корректно).
    // Сейчас ''.split(' ') = [''] -> длина 1.
    uploadResp = () => ({ body: { success: true, transcription: '' } });
    setup();
    await startFile();
    expect(await screen.findByText('Результат транскрибации')).toBeInTheDocument();
    expect(screen.getByText('Результат транскрибации появится здесь...')).toBeInTheDocument();
    expect(screen.getByText('0 символов')).toBeInTheDocument();
    expect(screen.getByText('~1 слов')).toBeInTheDocument();
  });

  it('копирование в буфер: успех и ошибка', async () => {
    const writeText = jest.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    await openResult();
    await act(async () => {
      fireEvent.click(btn('ContentCopyIcon'));
    });
    expect(writeText).toHaveBeenCalledWith(TEXT);
    expect(mockNotify).toHaveBeenCalledWith('success', 'Транскрипция скопирована в буфер обмена');
    writeText.mockRejectedValueOnce(new Error('denied'));
    await act(async () => {
      fireEvent.click(btn('ContentCopyIcon'));
    });
    expect(mockNotify).toHaveBeenLastCalledWith('error', 'Не удалось скопировать текст');
  });

  it('скачивание: txt-файл с датой в имени', async () => {
    const createUrl = jest.fn().mockReturnValue('blob:z');
    (URL as any).createObjectURL = createUrl;
    (URL as any).revokeObjectURL = jest.fn();
    const downloads: string[] = [];
    jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      downloads.push(this.download);
    });
    await openResult();
    fireEvent.click(btn('DownloadIcon'));
    expect((createUrl.mock.calls[0][0] as Blob).size).toBe(new Blob([TEXT]).size);
    expect(downloads[0]).toMatch(/^transcription_\d{4}-\d{2}-\d{2}\.txt$/);
    expect((URL as any).revokeObjectURL).toHaveBeenCalledWith('blob:z');
  });

  it('вставка в чат: текст, уведомление, окно результата закрывается', async () => {
    const onInsert = jest.fn();
    await openResult({ onInsertToChat: onInsert });
    fireEvent.click(btn('SendIcon'));
    expect(onInsert).toHaveBeenCalledWith(TEXT);
    expect(mockNotify).toHaveBeenCalledWith('success', 'Текст вставлен в поле ввода чата');
    await waitFor(() => expect(screen.queryByText('Результат транскрибации')).toBeNull());
  });

  it('кнопки вставки нет без onInsertToChat', async () => {
    await openResult();
    expect(screen.queryByTestId('SendIcon')).toBeNull();
  });

  it('кнопка «Закрыть» закрывает окно результата', async () => {
    await openResult();
    const closeBtns = within(resultDialog()).getAllByTestId('CloseIcon');
    fireEvent.click(closeBtns[closeBtns.length - 1].closest('button')!);
    await waitFor(() => expect(screen.queryByText('Результат транскрибации')).toBeNull());
  });

  it('Escape закрывает окно результата', async () => {
    await openResult();
    fireEvent.keyDown(resultDialog(), { key: 'Escape' });
    await waitFor(() => expect(screen.queryByText('Результат транскрибации')).toBeNull());
  });

  describe('имена спикеров', () => {
    const openSettings = () => fireEvent.click(btn('PersonIcon'));
    const nameField = (s: string) => screen.getByPlaceholderText(`Введите имя для ${s}`) as HTMLInputElement;

    it('кнопки спикеров нет, если в тексте нет спикеров', async () => {
      await openResult({ transcriptionResult: 'просто текст' });
      expect(screen.queryByTestId('PersonIcon')).toBeNull();
    });

    it('панель показывает спикеров, применение имён отправляет заменённый текст в onTranscriptionComplete', async () => {
      const onComplete = jest.fn();
      await openResult({ onTranscriptionComplete: onComplete });
      openSettings();
      expect(screen.getByText('SPEAKER_00:')).toBeInTheDocument();
      expect(screen.getByText('2 спикеров')).toBeInTheDocument();
      fireEvent.change(nameField('SPEAKER_00'), { target: { value: 'Анна' } });
      fireEvent.change(nameField('SPEAKER_01'), { target: { value: ' ' } });
      fireEvent.click(screen.getByRole('button', { name: /Применить имена/ }));
      expect(onComplete).toHaveBeenCalledWith('Анна: привет мир\nSPEAKER_01: как дела');
      expect(mockNotify).toHaveBeenCalledWith('success', 'Имена спикеров применены');
    });

    it('применение без внешнего колбэка обновляет внутренний результат', async () => {
      uploadResp = () => ({ body: { success: true, transcription: 'SPEAKER_00: привет' } });
      setup();
      await startFile();
      await screen.findByText('Результат транскрибации');
      openSettings();
      fireEvent.change(nameField('SPEAKER_00'), { target: { value: 'Борис' } });
      fireEvent.click(screen.getByRole('button', { name: /Применить имена/ }));
      expect(await screen.findByText('Борис: привет')).toBeInTheDocument();
    });

    it('добавление спикера: пустой идентификатор, новый, дубликат', async () => {
      await openResult();
      openSettings();
      const field = screen.getByPlaceholderText('Например: SPEAKER_SPEAKER_05');
      expect(screen.getByRole('button', { name: /Добавить/ })).toBeDisabled();
      fireEvent.keyPress(field, { key: 'Enter', charCode: 13 });
      expect(mockNotify).toHaveBeenCalledWith('warning', 'Введите идентификатор спикера');
      fireEvent.change(field, { target: { value: 'SPEAKER_09' } });
      fireEvent.keyPress(field, { key: 'Enter', charCode: 13 });
      expect(screen.getByText('SPEAKER_09:')).toBeInTheDocument();
      expect(mockNotify).toHaveBeenCalledWith('success', 'Спикер добавлен');
      fireEvent.change(field, { target: { value: 'SPEAKER_09' } });
      fireEvent.click(screen.getByRole('button', { name: /Добавить/ }));
      expect(mockNotify).toHaveBeenLastCalledWith('warning', 'Такой спикер уже существует');
      fireEvent.change(field, { target: { value: 'x' } });
      fireEvent.keyPress(field, { key: 'a', charCode: 97 });
    });

    it('удаление спикеров до пустого списка, единственное число подписи', async () => {
      await openResult({ transcriptionResult: 'SPEAKER_00: привет' });
      openSettings();
      expect(screen.getByText('1 спикер')).toBeInTheDocument();
      fireEvent.click(screen.getByTitle('Удалить спикера'));
      expect(mockNotify).toHaveBeenCalledWith('info', 'Спикер удален из списка');
      expect(screen.getByText(/Спикеры не найдены/)).toBeInTheDocument();
    });

    it('панель закрывается крестиком и кнопкой «Закрыть»', async () => {
      await openResult();
      openSettings();
      const cross = within(resultDialog()).getAllByTestId('CloseIcon')[0].closest('button')!;
      fireEvent.click(cross);
      expect(screen.queryByText('Настройка имен спикеров')).toBeNull();
      openSettings();
      const closeBtn = screen.getAllByRole('button', { name: 'Закрыть' }).find((b) => b.classList.contains('MuiButton-root'))!;
      fireEvent.click(closeBtn);
      expect(screen.queryByText('Настройка имен спикеров')).toBeNull();
    });

    it('применение берёт оригинальный текст, а не уже переименованный', async () => {
      const onComplete = jest.fn();
      const r = render(
        <TranscriptionModal open onClose={onClose} transcriptionResult={TEXT} onTranscriptionComplete={onComplete} />,
      );
      fireEvent.click(screen.getByRole('button', { name: /Просмотреть результат/ }));
      await screen.findByText('Результат транскрибации');
      openSettings();
      fireEvent.change(nameField('SPEAKER_00'), { target: { value: 'Анна' } });
      fireEvent.click(screen.getByRole('button', { name: /Применить имена/ }));
      r.rerender(
        <TranscriptionModal
          open
          onClose={onClose}
          transcriptionResult="Анна: привет мир\nSPEAKER_01: как дела"
          onTranscriptionComplete={onComplete}
        />,
      );
      fireEvent.change(nameField('SPEAKER_00'), { target: { value: 'Борис' } });
      fireEvent.click(screen.getByRole('button', { name: /Применить имена/ }));
      expect(onComplete).toHaveBeenLastCalledWith('Борис: привет мир\nSPEAKER_01: как дела');
    });

    it('подстановка имени для SPEAKER_1 не портит SPEAKER_10 (BUG-FE-65)', async () => {
      const onComplete = jest.fn();
      await openResult({ transcriptionResult: 'SPEAKER_1: а\nSPEAKER_10: б', onTranscriptionComplete: onComplete });
      openSettings();
      fireEvent.change(nameField('SPEAKER_1'), { target: { value: 'Аня' } });
      fireEvent.click(screen.getByRole('button', { name: /Применить имена/ }));
      expect(onComplete).toHaveBeenCalledWith('Аня: а\nSPEAKER_10: б');
    });
  });
});
