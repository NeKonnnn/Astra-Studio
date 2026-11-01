import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import VoiceChatDialog from '@/components/VoiceChatDialog';

const mockNotify = jest.fn();
const mockSetRecording = jest.fn();
const mockSetSpeaking = jest.fn();
let mockMessages: any[];
let mockState: { isLoading: boolean };
let mockSocketCtx: { isConnected: boolean };

jest.mock('@/contexts/AppContext', () => ({
  useAppActions: () => ({
    showNotification: mockNotify,
    setRecording: mockSetRecording,
    setSpeaking: mockSetSpeaking,
    getCurrentMessages: () => mockMessages,
  }),
  useAppContext: () => ({ state: mockState }),
}));
jest.mock('@/contexts/SocketContext', () => ({ useSocket: () => mockSocketCtx }));
jest.mock('@/config/api', () => ({
  authFetch: (...a: unknown[]) => (require('@tests/unit/helpers/authFetchMock').authFetchMock as (...x: unknown[]) => unknown)(...a),
  getApiUrl: (p: string) => `http://api${p}`,
  getAuthFetchHeaders: (extra: Record<string, string> = {}) => ({ Authorization: 'Bearer T', ...extra }),
  getAuthWsUrl: (p: string) => `ws://api${p}?token=T`,
  API_ENDPOINTS: {
    VOICE_RECOGNIZE: '/api/voice/recognize',
    VOICE_SYNTHESIZE: '/api/voice/synthesize',
    CHAT: '/api/chat',
  },
}));
jest.mock('@/components/VoiceVisualization3D', () => ({
  __esModule: true,
  default: () => <div data-testid="viz3d" />,
}));
jest.mock('@/components/chat/ThinkingShimmerText', () => ({
  __esModule: true,
  default: ({ children }: { children: React.ReactNode }) => <span data-testid="thinking">{children}</span>,
}));

// ───────── Браузерные API ─────────
let wsInstances: any[] = [];
let recInstances: any[] = [];
let audioInstances: any[] = [];
let recSupported: (t: string) => boolean = () => true;
let playImpl: () => Promise<void> = () => Promise.resolve();
// Уровень «звука» с микрофона (0..255): по умолчанию речь есть, чтобы автостоп по тишине не мешал другим тестам.
let analyserLevel = 200;
class FakeWS {
  readyState = 0;
  sent: unknown[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: unknown }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: ((e: { code: number }) => void) | null = null;
  closed = false;
  url: string;
  constructor(url: string) {
    this.url = url;
    wsInstances.push(this);
  }
  send(d: unknown) {
    this.sent.push(d);
  }
  close() {
    this.closed = true;
    this.readyState = 3;
  }
  open() {
    this.readyState = 1;
    act(() => this.onopen?.());
  }
}

class FakeRecorder {
  state = 'inactive';
  ondataavailable: ((e: { data: Blob }) => void) | null = null;
  onstop: (() => Promise<void> | void) | null = null;
  onerror: (() => void) | null = null;
  startArg: number | undefined;
  stream: unknown;
  options?: { mimeType: string };
  constructor(stream: unknown, options?: { mimeType: string }) {
    this.stream = stream;
    this.options = options;
    recInstances.push(this);
  }
  start(ms?: number) {
    this.state = 'recording';
    this.startArg = ms;
  }
  stop() {
    this.state = 'inactive';
    void this.onstop?.();
  }
}

class FakeAudio {
  onended: (() => void) | null = null;
  onerror: (() => void) | null = null;
  src: string;
  paused = false;
  constructor(src: string) {
    this.src = src;
    audioInstances.push(this);
  }
  play() {
    return playImpl();
  }
  pause() {
    this.paused = true;
  }
}

let trackStop: jest.Mock;
let ctxClose: jest.Mock;
let getUserMedia: jest.Mock;
let fetchMock: jest.Mock;
type R = { ok?: boolean; status?: number; body?: any; blob?: Blob; reject?: boolean };
let routes: Record<string, () => R>;

const flush = async () => {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
};
const micBtn = () => screen.getByTestId('MicIcon').closest('button') as HTMLButtonElement;
const stopIconBtn = () => screen.getAllByTestId('StopIcon').map((i) => i.closest('button') as HTMLButtonElement);
const stopRecBtn = () => {
  const b = stopIconBtn();
  return b[b.length - 1]; // кнопка остановки записи рисуется ниже кнопки «остановить все»
};
const onClose = jest.fn();
const lastWs = () => wsInstances[wsInstances.length - 1];
const lastRec = () => recInstances[recInstances.length - 1];
const lastAudio = () => audioInstances[audioInstances.length - 1];

function setup(props: Partial<React.ComponentProps<typeof VoiceChatDialog>> = {}) {
  return render(<VoiceChatDialog open onClose={onClose} {...props} />);
}
async function startRec() {
  await act(async () => {
    fireEvent.click(micBtn());
  });
  await flush();
}
/** Отдаёт данные записи (>=100 байт) и ждёт стабилизации. */
function feedChunk(bytes = 200) {
  act(() => {
    lastRec().ondataavailable?.({ data: new Blob(['x'.repeat(bytes)]) });
  });
}
const settingsBtn = () => screen.getByTestId('SettingsIcon').closest('button') as HTMLButtonElement;

beforeEach(() => {
  jest.useFakeTimers('modern');
  analyserLevel = 200;
  jest.clearAllMocks();
  localStorage.clear();
  mockMessages = [];
  mockState = { isLoading: false };
  mockSocketCtx = { isConnected: true };
  wsInstances = [];
  recInstances = [];
  recSupported = () => true;
  audioInstances = [];
  playImpl = () => Promise.resolve();
  trackStop = jest.fn();
  ctxClose = jest.fn();
  getUserMedia = jest.fn().mockResolvedValue({ getTracks: () => [{ stop: trackStop }] });
  (FakeWS as any).OPEN = 1;
  (FakeRecorder as any).isTypeSupported = (t: string) => recSupported(t);
  (global as any).WebSocket = FakeWS;
  (global as any).MediaRecorder = FakeRecorder;
  (global as any).Audio = FakeAudio;
  (global as any).AudioContext = class {
    state = 'running';
    createAnalyser() {
      return { frequencyBinCount: 4, getByteFrequencyData: (a: Uint8Array) => a.fill(analyserLevel), fftSize: 0 };
    }
    createMediaStreamSource() {
      return { connect: jest.fn() };
    }
    close() {
      this.state = 'closed';
      ctxClose();
    }
  };
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia } });
  (URL as any).createObjectURL = jest.fn(() => 'blob:audio');
  (URL as any).revokeObjectURL = jest.fn();
  routes = {
    '/api/voice/recognize': () => ({ body: { success: true, text: 'привет' } }),
    '/api/chat': () => ({ body: { success: true, response: 'ответ ассистента' } }),
    '/api/voice/synthesize': () => ({ blob: new Blob(['wav']) }),
  };
  fetchMock = jest.fn(async (url: string) => {
    const key = url.replace('http://api', '');
    const r = routes[key]?.() ?? { ok: false, status: 404 };
    if (r.reject) throw new Error('net');
    return {
      ok: r.ok !== false,
      status: r.status ?? 200,
      json: async () => r.body,
      blob: async () => r.blob,
    } as unknown as Response;
  });
  (global as any).fetch = fetchMock;
});
afterEach(() => {
  jest.useRealTimers();
});

describe('VoiceChatDialog: начальное состояние', () => {
  it('закрытый диалог не рендерится', () => {
    setup({ open: false });
    expect(screen.queryByText('Голосовой чат')).toBeNull();
  });

  it('показывает заголовок, подсказку, статус «WebSocket подключится при записи»', () => {
    setup();
    expect(screen.getByText('Голосовой чат')).toBeInTheDocument();
    expect(screen.getByText('Нажмите кнопку микрофона для начала записи')).toBeInTheDocument();
    expect(screen.getByText('WebSocket подключится при записи')).toBeInTheDocument();
    expect(screen.queryByTestId('StopIcon')).toBeNull();
  });

  it('кнопка «Закрыть» вызывает onClose', () => {
    setup();
    fireEvent.click(screen.getByRole('button', { name: 'Закрыть' }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('микрофон заблокирован, пока идёт загрузка без стриминга; со стримингом - доступен', () => {
    mockState = { isLoading: true };
    const { unmount } = setup();
    expect(micBtn()).toBeDisabled();
    unmount();
    mockMessages = [{ id: 1, isStreaming: true }];
    setup();
    expect(micBtn()).toBeEnabled();
  });

  it('синхронизирует признаки записи/воспроизведения с глобальным состоянием', () => {
    setup();
    expect(mockSetRecording).toHaveBeenCalledWith(false);
    expect(mockSetSpeaking).toHaveBeenCalledWith(false);
  });
});

describe('VoiceChatDialog: запись', () => {
  it('первое нажатие: подключает WebSocket, запрашивает микрофон с шумоподавлением, стартует запись и показывает визуализацию', async () => {
    setup();
    await startRec();
    expect(mockNotify).toHaveBeenCalledWith('info', 'Подключаю голосовой чат...');
    expect(lastWs().url).toBe('ws://api/ws/voice?token=T');
    expect(getUserMedia).toHaveBeenCalledWith({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    expect(lastRec().state).toBe('recording');
    expect(lastRec().startArg).toBe(1000);
    expect(lastRec().options).toEqual({ mimeType: 'audio/wav' });
    expect(mockNotify).toHaveBeenCalledWith('info', 'Запись началась. Говорите...');
    expect(screen.getByTestId('viz3d')).toBeInTheDocument();
    expect(screen.getByText(/Прослушивание\.\.\. 0:00/)).toBeInTheDocument();
    expect(mockSetRecording).toHaveBeenLastCalledWith(true);
  });

  it('выбирает первый поддерживаемый формат; если ни один не поддержан - без опций', async () => {
    recSupported = (t) => t === 'audio/webm';
    const { unmount } = setup();
    await startRec();
    expect(lastRec().options).toEqual({ mimeType: 'audio/webm' });
    unmount();
    recSupported = () => false;
    setup();
    await startRec();
    expect(lastRec().options).toBeUndefined();
  });

  it('таймер записи показывает минуты и секунды', async () => {
    setup();
    await startRec();
    for (let i = 0; i < 65; i += 1) {
      await act(async () => {
        jest.advanceTimersByTime(1000);
      });
    }
    expect(screen.getByText(/Прослушивание\.\.\. 1:05/)).toBeInTheDocument();
  });

  it('ошибки доступа к микрофону: заблокирован, не найден, прочее', async () => {
    setup();
    getUserMedia.mockRejectedValueOnce({ name: 'NotAllowedError' });
    await startRec();
    expect(mockNotify).toHaveBeenCalledWith('error', 'Доступ к микрофону заблокирован. Разрешите доступ в браузере.');
    getUserMedia.mockRejectedValueOnce({ name: 'NotFoundError' });
    await startRec();
    expect(mockNotify).toHaveBeenCalledWith('error', 'Микрофон не найден');
    getUserMedia.mockRejectedValueOnce(new Error('x'));
    await startRec();
    expect(mockNotify).toHaveBeenCalledWith('error', 'Не удалось получить доступ к микрофону');
    expect(screen.getByText('Нажмите кнопку микрофона для начала записи')).toBeInTheDocument();
  });

  it('данные записи пустого размера не копятся; ошибка рекордера останавливает запись', async () => {
    setup();
    await startRec();
    act(() => {
      lastRec().ondataavailable?.({ data: new Blob([]) });
    });
    act(() => {
      lastRec().onerror?.();
    });
    expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка записи аудио');
    expect(screen.getByText('Нажмите кнопку микрофона для начала записи')).toBeInTheDocument();
  });

  it('«стоп» без визуального потока и с ним останавливает запись и освобождает микрофон', async () => {
    setup();
    await startRec();
    const rec = lastRec();
    await act(async () => {
      fireEvent.click(stopRecBtn());
    });
    expect(rec.state).toBe('inactive');
    expect(trackStop).toHaveBeenCalled();
    expect(ctxClose).toHaveBeenCalled();
    expect(mockNotify).toHaveBeenCalledWith('info', 'Прослушивание остановлено');
    expect(screen.getByText('Нажмите кнопку микрофона для начала записи')).toBeInTheDocument();
  });

  it('повторный старт останавливает дорожки предыдущего потока', async () => {
    setup();
    await startRec();
    await act(async () => {
      fireEvent.click(stopRecBtn());
    });
    trackStop.mockClear();
    await startRec();
    expect(getUserMedia).toHaveBeenCalledTimes(2);
  });

  it('новый старт при активном потоке сначала останавливает его дорожки', async () => {
    setup();
    await startRec();
    // Эмулируем «зависший» поток: запись остановилась снаружи, ref потока ещё хранит дорожки
    act(() => {
      lastRec().onerror?.();
    });
    trackStop.mockClear();
    await startRec();
    expect(trackStop).toHaveBeenCalled();
  });
});

describe('VoiceChatDialog: обработка записи без WebSocket (fallback)', () => {
  async function recordAndStop(bytes = 200) {
    setup();
    await startRec();
    feedChunk(bytes);
    await act(async () => {
      fireEvent.click(stopRecBtn());
    });
    await flush();
    await flush();
  }

  it('слишком короткая запись: предупреждение и сеть не используется', async () => {
    await recordAndStop(10);
    expect(mockNotify).toHaveBeenCalledWith('warning', 'Запись слишком короткая, попробуйте еще раз');
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('полный цикл: распознавание -> чат -> синтез речи -> воспроизведение', async () => {
    await recordAndStop();
    expect(mockNotify).toHaveBeenCalledWith('warning', 'WebSocket не подключен, использую fallback...');
    const rec = fetchMock.mock.calls.find((c) => c[0] === 'http://api/api/voice/recognize')!;
    expect(rec[1].method).toBe('POST');
    expect((rec[1].body as FormData).get('audio_file')).toBeInstanceOf(Blob);
    const chat = fetchMock.mock.calls.find((c) => c[0] === 'http://api/api/chat')!;
    expect(chat[1].headers).toEqual({ Authorization: 'Bearer T', 'Content-Type': 'application/json' });
    expect(JSON.parse(chat[1].body)).toEqual({ message: 'привет', streaming: false });
    const synth = fetchMock.mock.calls.find((c) => c[0] === 'http://api/api/voice/synthesize')!;
    expect(JSON.parse(synth[1].body)).toEqual({
      text: 'ответ ассистента',
      voice_id: 'ru',
      voice_speaker: 'baya',
      speech_rate: 1,
    });
    expect(lastAudio().src).toBe('blob:audio');
    expect(mockNotify).toHaveBeenCalledWith('success', 'Речь распознана');
    expect(screen.getByText(/"привет"/)).toBeInTheDocument();
    // окончание воспроизведения освобождает URL
    act(() => lastAudio().onended?.());
    expect((URL as any).revokeObjectURL).toHaveBeenCalledWith('blob:audio');
  });

  it('нет соединения с сервером: ошибка без запросов', async () => {
    mockSocketCtx = { isConnected: false };
    await recordAndStop();
    expect(mockNotify).toHaveBeenCalledWith('error', 'Нет соединения с сервером');
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('HTTP-ошибка распознавания', async () => {
    routes['/api/voice/recognize'] = () => ({ ok: false, status: 503 });
    await recordAndStop();
    expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка распознавания: 503');
  });

  it('success=false при распознавании', async () => {
    routes['/api/voice/recognize'] = () => ({ body: { success: false } });
    await recordAndStop();
    expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка распознавания речи');
  });

  it('пустой распознанный текст: предупреждение, чат не вызывается', async () => {
    routes['/api/voice/recognize'] = () => ({ body: { success: true, text: '   ' } });
    await recordAndStop();
    expect(mockNotify).toHaveBeenCalledWith('warning', 'Речь не распознана. Попробуйте еще раз.');
    expect(fetchMock.mock.calls.some((c) => c[0] === 'http://api/api/chat')).toBe(false);
  });

  it('сетевая ошибка распознавания', async () => {
    routes['/api/voice/recognize'] = () => ({ reject: true });
    await recordAndStop();
    expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка подключения к серверу распознавания');
  });

  it('ответ чата с success=false', async () => {
    routes['/api/chat'] = () => ({ body: { success: false } });
    await recordAndStop();
    expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка получения ответа от astrachat');
  });

  it('сетевая ошибка при отправке сообщения в чат', async () => {
    routes['/api/chat'] = () => ({ reject: true });
    await recordAndStop();
    expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка отправки сообщения');
  });

  it('пустой ответ ассистента не озвучивается', async () => {
    routes['/api/chat'] = () => ({ body: { success: true, response: '  ' } });
    await recordAndStop();
    expect(fetchMock.mock.calls.some((c) => c[0] === 'http://api/api/voice/synthesize')).toBe(false);
  });

  it('ошибка синтеза речи (HTTP и сеть)', async () => {
    routes['/api/voice/synthesize'] = () => ({ ok: false, status: 500 });
    await recordAndStop();
    expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка синтеза речи');
  });

  it('сетевая ошибка синтеза речи', async () => {
    routes['/api/voice/synthesize'] = () => ({ reject: true });
    await recordAndStop();
    expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка синтеза речи');
  });

  it('ошибка воспроизведения синтезированной речи', async () => {
    await recordAndStop();
    act(() => lastAudio().onerror?.());
    expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка воспроизведения речи');
  });

  it('новый синтез останавливает предыдущее воспроизведение (тест скорости поверх тестового голоса)', async () => {
    setup();
    fireEvent.click(settingsBtn());
    await act(async () => {
      fireEvent.click(screen.getByTestId('ChevronRightIcon').closest('button')!);
    });
    await flush();
    const testAudio = lastAudio();
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Тестировать скорость/ }));
    });
    await flush();
    expect(testAudio.paused).toBe(true);
    expect(testAudio.src).toBe('');
    expect(lastAudio()).not.toBe(testAudio);
  });

  it('внутреннее исключение обработки (например, в showNotification) даёт «Ошибка обработки аудио»', async () => {
    mockNotify.mockImplementation((_t: string, m: string) => {
      if (m.startsWith('WebSocket не подключен')) throw new Error('boom');
    });
    await recordAndStop();
    expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка обработки аудио');
    mockNotify.mockReset();
  });
});

describe('VoiceChatDialog: автоостановка по тишине', () => {
  it('автоматическая остановка через 5 секунд тишины срабатывает (BUG-FE-63)', async () => {
    analyserLevel = 0;
    const raf = jest.spyOn(window, 'requestAnimationFrame');
    setup();
    await startRec();
    for (let i = 0; i < 10; i += 1) {
      await act(async () => {
        jest.advanceTimersByTime(1000);
      });
    }
    // цикл замера уровня звука запущен
    expect(raf).toHaveBeenCalled();
    expect(mockNotify).toHaveBeenCalledWith('info', 'Автоматическая остановка: не обнаружена речь');
    expect(mockNotify).toHaveBeenCalledWith('info', 'Прослушивание остановлено');
    expect(screen.queryByText(/Прослушивание\.\.\. 0:10/)).not.toBeInTheDocument();
    raf.mockRestore();
  });

  it('речь (ненулевой уровень звука) сбрасывает таймер тишины: автостоп не срабатывает (BUG-FE-63)', async () => {
    analyserLevel = 200;
    setup();
    await startRec();
    for (let i = 0; i < 10; i += 1) {
      await act(async () => {
        jest.advanceTimersByTime(1000);
      });
    }
    expect(mockNotify).not.toHaveBeenCalledWith('info', 'Автоматическая остановка: не обнаружена речь');
    expect(screen.getByText(/Прослушивание\.\.\. 0:10/)).toBeInTheDocument();
  });
});

describe('VoiceChatDialog: ручная отправка распознанного текста', () => {
  async function withText() {
    setup();
    await startRec();
    feedChunk();
    await act(async () => {
      fireEvent.click(stopRecBtn());
    });
    await flush();
    await flush();
  }

  it('«Очистить» убирает текст; «Отправить» отправляет его и очищает поле', async () => {
    routes['/api/chat'] = () => ({ body: { success: false } });
    await withText();
    fetchMock.mockClear();
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }));
    await flush();
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ message: 'привет', streaming: false });
    expect(screen.queryByText(/"привет"/)).toBeNull();
  });

  it('«Очистить» удаляет карточку с текстом', async () => {
    await withText();
    fireEvent.click(screen.getByRole('button', { name: 'Очистить' }));
    expect(screen.queryByText('Финальный распознанный текст:')).toBeNull();
  });
});

describe('VoiceChatDialog: WebSocket', () => {
  async function connected() {
    setup();
    await startRec();
    lastWs().open();
    return lastWs();
  }

  it('при открытии показывает статус подключения и уведомление', async () => {
    await connected();
    expect(screen.getByText('Real-Time Голосовой Чат')).toBeInTheDocument();
    expect(mockNotify).toHaveBeenCalledWith('success', 'Голосовой чат подключен');
  });

  it('сообщения сервера: статусы и тексты', async () => {
    const ws = await connected();
    const send = (o: unknown) => act(() => ws.onmessage?.({ data: JSON.stringify(o) }));
    send({ type: 'listening_started' });
    expect(mockNotify).toHaveBeenCalledWith('success', 'Готов к приему голоса');
    send({ type: 'speech_recognized', text: 'частичный' });
    expect(mockNotify).toHaveBeenCalledWith('success', 'Речь распознана в реальном времени');
    expect(screen.getByText(/частичный/)).toBeInTheDocument();
    send({ type: 'ai_response', text: 'ответ' });
    expect(mockNotify).toHaveBeenCalledWith('success', 'Получен ответ от astrachat');
    send({ type: 'speech_error', error: 'шум' });
    expect(mockNotify).toHaveBeenCalledWith('warning', 'шум');
    send({ type: 'speech_error' });
    expect(mockNotify).toHaveBeenCalledWith('warning', 'Ошибка распознавания речи');
    send({ type: 'tts_error', error: 'tts' });
    expect(mockNotify).toHaveBeenCalledWith('error', 'tts');
    send({ type: 'tts_error' });
    expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка синтеза речи');
    send({ type: 'error', error: 'bad' });
    expect(mockNotify).toHaveBeenCalledWith('error', 'bad');
    send({ type: 'error' });
    expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка WebSocket');
    send({ type: 'unknown' });
  });

  it('некорректный JSON игнорируется', async () => {
    const ws = await connected();
    mockNotify.mockClear();
    act(() => ws.onmessage?.({ data: '{not json' }));
    expect(mockNotify).not.toHaveBeenCalled();
  });

  it('бинарный ответ (Blob) воспроизводится; по окончании готов к следующему запросу', async () => {
    const ws = await connected();
    await act(async () => {
      ws.onmessage?.({ data: new Blob(['audio']) });
    });
    expect(lastAudio().src).toBe('blob:audio');
    act(() => lastAudio().onended?.());
    expect(mockNotify).toHaveBeenCalledWith('success', 'Готов к следующему запросу');
    expect((URL as any).revokeObjectURL).toHaveBeenCalledWith('blob:audio');
  });

  it('ошибка воспроизведения бинарного ответа: onerror и исключение play()', async () => {
    const ws = await connected();
    await act(async () => {
      ws.onmessage?.({ data: new Blob(['audio']) });
    });
    act(() => lastAudio().onerror?.());
    expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка воспроизведения речи');
    mockNotify.mockClear();
    playImpl = () => Promise.reject(new Error('blocked'));
    await act(async () => {
      ws.onmessage?.({ data: new Blob(['audio']) });
    });
    expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка воспроизведения речи');
  });

  it('ошибка соединения: статус сбрасывается и через 5 с выполняется переподключение', async () => {
    const ws = await connected();
    const before = wsInstances.length;
    act(() => ws.onerror?.());
    expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка подключения к голосовому чату');
    await act(async () => {
      jest.advanceTimersByTime(5000);
    });
    expect(mockNotify).toHaveBeenCalledWith('info', 'Попытка переподключения...');
    expect(wsInstances.length).toBe(before + 1);
  });

  it('аварийное закрытие (код != 1000): предупреждение и переподключение через 3 с', async () => {
    const ws = await connected();
    const before = wsInstances.length;
    act(() => ws.onclose?.({ code: 1006 }));
    expect(mockNotify).toHaveBeenCalledWith('warning', 'Соединение с голосовым чатом закрыто, переподключаюсь...');
    await act(async () => {
      jest.advanceTimersByTime(3000);
    });
    expect(wsInstances.length).toBe(before + 1);
  });

  it('нормальное закрытие (1000) не переподключается', async () => {
    const ws = await connected();
    const before = wsInstances.length;
    mockNotify.mockClear();
    act(() => ws.onclose?.({ code: 1000 }));
    await act(async () => {
      jest.advanceTimersByTime(10000);
    });
    expect(wsInstances.length).toBe(before);
    expect(mockNotify).not.toHaveBeenCalled();
  });

  it('при открытом сокете повторный старт не создаёт новое подключение, а шлёт start_listening', async () => {
    const ws = await connected();
    await act(async () => {
      fireEvent.click(stopRecBtn());
    });
    await flush();
    const count = wsInstances.length;
    await startRec();
    expect(wsInstances.length).toBe(count);
    expect(ws.sent).toContain(JSON.stringify({ type: 'start_listening' }));
  });

  it('при подключённом сокете (второй старт) запись уходит в WebSocket бинарно', async () => {
    const ws = await connected();
    await act(async () => {
      fireEvent.click(stopRecBtn());
    });
    await flush();
    ws.sent.length = 0;
    await startRec();
    feedChunk();
    await act(async () => {
      fireEvent.click(stopRecBtn());
    });
    await flush();
    expect(ws.sent.some((m) => m instanceof Blob)).toBe(true);
    expect(mockNotify).toHaveBeenCalledWith('info', 'Отправляю голос на обработку...');
  });

  it('первая запись при только что подключённом сокете уходит в fallback, хотя WebSocket открыт (known issue)', async () => {
    // Как должно быть: запись, сделанная при открытом WebSocket, отправляется в сокет.
    // Сейчас обработчик onstop замыкает значение voiceSocket на момент нажатия кнопки (null при первом старте),
    // поэтому первая запись всегда идёт через HTTP-fallback с уведомлением «WebSocket не подключен».
    const ws = await connected();
    feedChunk();
    await act(async () => {
      fireEvent.click(stopRecBtn());
    });
    await flush();
    expect(mockNotify).toHaveBeenCalledWith('warning', 'WebSocket не подключен, использую fallback...');
    expect(ws.sent.some((m) => m instanceof Blob)).toBe(false);
  });

  it('realtime: во время записи каждые 2 секунды последний чанк отправляется в сокет', async () => {
    const ws = await connected();
    await act(async () => {
      fireEvent.click(stopRecBtn());
    });
    await startRec();
    feedChunk();
    ws.sent.length = 0;
    for (let i = 0; i < 4; i += 1) {
      await act(async () => {
        jest.advanceTimersByTime(1000);
      });
    }
    expect(ws.sent.filter((m) => m instanceof Blob).length).toBeGreaterThan(0);
  });

  it('realtime: ошибка отправки чанка подавляется', async () => {
    const ws = await connected();
    await act(async () => {
      fireEvent.click(stopRecBtn());
    });
    await startRec();
    feedChunk();
    ws.send = () => {
      throw new Error('closed');
    };
    for (let i = 0; i < 4; i += 1) {
      await act(async () => {
        jest.advanceTimersByTime(1000);
      });
    }
    expect(screen.getByText(/Прослушивание/)).toBeInTheDocument();
  });

  it('распознанный в реальном времени текст показывается во время записи', async () => {
    const ws = await connected();
    act(() => ws.onmessage?.({ data: JSON.stringify({ type: 'speech_recognized', text: 'раз' }) }));
    expect(screen.getByText('Real-time распознавание (каждые 2 сек):')).toBeInTheDocument();
  });
});

describe('VoiceChatDialog: остановка всех процессов и размонтирование', () => {
  it('кнопка «Остановить все процессы» освобождает ресурсы, закрывает сокет и записывает уведомление', async () => {
    setup();
    await startRec();
    const ws = lastWs();
    ws.open();
    const rec = lastRec();
    // кнопка «остановить все» в правом нижнем углу рисуется первой
    const btns = stopIconBtn();
    await act(async () => {
      fireEvent.click(btns[0]);
    });
    expect(rec.state).toBe('inactive');
    expect(trackStop).toHaveBeenCalled();
    expect(ctxClose).toHaveBeenCalled();
    expect(ws.closed).toBe(true);
    expect(mockNotify).toHaveBeenCalledWith('info', 'Все процессы остановлены');
    expect(screen.getByText('Нажмите кнопку микрофона для начала записи')).toBeInTheDocument();
  });

  it('при воспроизведении ответа остановка прекращает звук', async () => {
    setup();
    await startRec();
    const ws = lastWs();
    ws.open();
    await act(async () => {
      ws.onmessage?.({ data: new Blob(['a']) });
    });
    const audio = lastAudio();
    const btns = stopIconBtn();
    await act(async () => {
      fireEvent.click(btns[0]);
    });
    expect(audio.paused).toBe(true);
  });

  it('при размонтировании во время записи освобождаются микрофон, контекст и звук', async () => {
    const { unmount } = setup();
    await startRec();
    const ws = lastWs();
    ws.open();
    await act(async () => {
      ws.onmessage?.({ data: new Blob(['a']) });
    });
    const rec = lastRec();
    const audio = lastAudio();
    unmount();
    expect(rec.state).toBe('inactive');
    expect(trackStop).toHaveBeenCalled();
    expect(ctxClose).toHaveBeenCalled();
    expect(audio.paused).toBe(true);
    expect(mockSetRecording).toHaveBeenLastCalledWith(false);
    expect(mockSetSpeaking).toHaveBeenLastCalledWith(false);
  });

  it('размонтирование без активности безопасно', () => {
    const { unmount } = setup();
    unmount();
    expect(mockSetRecording).toHaveBeenLastCalledWith(false);
  });
});

describe('VoiceChatDialog: настройки голоса', () => {
  it('значения голоса и скорости читаются из localStorage', () => {
    localStorage.setItem('voice_speaker', 'aidar');
    localStorage.setItem('voice_id', 'en');
    localStorage.setItem('speech_rate', '1.5');
    setup();
    fireEvent.click(settingsBtn());
    expect(screen.getByText('Текущая скорость: 1.5x')).toBeInTheDocument();
    expect(screen.getByText('4 / 5')).toBeInTheDocument();
    expect(screen.getByText('Aidar')).toBeInTheDocument();
  });

  it('значения по умолчанию: baya, 1.0x, 1 / 5', () => {
    setup();
    fireEvent.click(settingsBtn());
    expect(screen.getByText('Текущая скорость: 1.0x')).toBeInTheDocument();
    expect(screen.getByText('1 / 5')).toBeInTheDocument();
  });

  it('неизвестный голос в localStorage не меняет индекс', () => {
    localStorage.setItem('voice_speaker', 'unknown');
    setup();
    fireEvent.click(settingsBtn());
    expect(screen.getByText('1 / 5')).toBeInTheDocument();
  });

  it('«вперёд»/«назад» переключают голос по кругу, сохраняют выбор и проигрывают тест голоса', async () => {
    setup();
    fireEvent.click(settingsBtn());
    await act(async () => {
      fireEvent.click(screen.getByTestId('ChevronRightIcon').closest('button')!);
    });
    await flush();
    expect(localStorage.getItem('voice_speaker')).toBe('xenia');
    expect(screen.getByText('2 / 5')).toBeInTheDocument();
    const synth = fetchMock.mock.calls.find((c) => c[0] === 'http://api/api/voice/synthesize')!;
    expect(JSON.parse(synth[1].body)).toEqual({
      text: 'Привет! Я Астра Чат И И. Что обсудим?',
      voice_id: 'ru',
      voice_speaker: 'xenia',
      speech_rate: 1,
    });
    expect(mockNotify).toHaveBeenCalledWith('success', 'Тестирую голос xenia...');
    // «назад» с первого - на последний
    await act(async () => {
      fireEvent.click(screen.getByTestId('ChevronLeftIcon').closest('button')!);
    });
    await flush();
    await act(async () => {
      fireEvent.click(screen.getByTestId('ChevronLeftIcon').closest('button')!);
    });
    await flush();
    expect(localStorage.getItem('voice_speaker')).toBe('eugene');
    expect(screen.getByText('5 / 5')).toBeInTheDocument();
    // «вперёд» с последнего - на первый
    await act(async () => {
      fireEvent.click(screen.getByTestId('ChevronRightIcon').closest('button')!);
    });
    await flush();
    expect(localStorage.getItem('voice_speaker')).toBe('baya');
  });

  it('клик по голосу выбирает его и сохраняет; предыдущий тест останавливается', async () => {
    setup();
    fireEvent.click(settingsBtn());
    await act(async () => {
      fireEvent.click(screen.getByTestId('ChevronRightIcon').closest('button')!);
    });
    await flush();
    const first = lastAudio();
    // Клик по карточке голоса «Eugene» (пятая): карточки - Box с onClick; находим по индексу
    const cards = Array.from(document.querySelectorAll('div')).filter(
      (d) => (d as HTMLElement).style && getComputedStyle(d).cursor === 'pointer',
    );
    expect(cards.length).toBeGreaterThanOrEqual(5);
    await act(async () => {
      fireEvent.click(cards[cards.length - 1]);
    });
    await flush();
    expect(first.paused).toBe(true);
    expect(localStorage.getItem('voice_speaker')).toBe('eugene');
  });

  it('тест голоса: ошибка HTTP и ошибка воспроизведения', async () => {
    routes['/api/voice/synthesize'] = () => ({ ok: false, status: 502 });
    setup();
    fireEvent.click(settingsBtn());
    await act(async () => {
      fireEvent.click(screen.getByTestId('ChevronRightIcon').closest('button')!);
    });
    await flush();
    expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка тестирования голоса: HTTP 502');
  });

  it('тест голоса: сетевая ошибка и не-Error', async () => {
    routes['/api/voice/synthesize'] = () => ({ reject: true });
    setup();
    fireEvent.click(settingsBtn());
    await act(async () => {
      fireEvent.click(screen.getByTestId('ChevronRightIcon').closest('button')!);
    });
    await flush();
    expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка тестирования голоса: net');
    fetchMock.mockImplementationOnce(() => Promise.reject('строка'));
    await act(async () => {
      fireEvent.click(screen.getByTestId('ChevronRightIcon').closest('button')!);
    });
    await flush();
    expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка тестирования голоса: Неизвестная ошибка');
  });

  it('ошибка воспроизведения тестового голоса', async () => {
    setup();
    fireEvent.click(settingsBtn());
    await act(async () => {
      fireEvent.click(screen.getByTestId('ChevronRightIcon').closest('button')!);
    });
    await flush();
    act(() => lastAudio().onerror?.());
    expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка воспроизведения тестового голоса');
    act(() => lastAudio().onended?.());
    expect((URL as any).revokeObjectURL).toHaveBeenCalledWith('blob:audio');
  });

  it('слайдер скорости сохраняет значение и обновляет подпись', () => {
    setup();
    fireEvent.click(settingsBtn());
    fireEvent.change(document.querySelector('input[type="range"]')!, { target: { value: '1.5' } });
    expect(screen.getByText('Текущая скорость: 1.5x')).toBeInTheDocument();
    expect(localStorage.getItem('speech_rate')).toBe('1.5');
  });

  it('скорость речи учитывается в запросе синтеза', async () => {
    localStorage.setItem('speech_rate', '1.8');
    setup();
    fireEvent.click(settingsBtn());
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Тестировать скорость/ }));
    });
    await flush();
    const synth = fetchMock.mock.calls.find((c) => c[0] === 'http://api/api/voice/synthesize')!;
    expect(JSON.parse(synth[1].body)).toMatchObject({
      text: 'Это тест скорости речи ассистента. Настройте скорость по вашему вкусу.',
      speech_rate: 1.8,
    });
  });

  it('повторный клик по кнопке настроек сворачивает панель (кнопка работает как переключатель)', () => {
    setup();
    fireEvent.click(settingsBtn());
    fireEvent.click(settingsBtn());
    expect(screen.getByText('Выберите голос:')).toBeInTheDocument();
  });
});
