/* eslint-disable @typescript-eslint/no-explicit-any */
import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import VoicePage from '@/pages/VoicePage';

// VoicePage нигде не импортируется (не подключена к App; голос встроен в UnifiedChatPage), но покрываем её.
const mock: any = {};
function reset() {
  mock.isConnected = true;
  mock.notify = jest.fn();
  mock.setSpeaking = jest.fn();
  mock.setRecording = jest.fn();
}
reset();

jest.mock('react-router-dom', () => ({ useLocation: () => ({ pathname: '/voice' }) }));
jest.mock('@/config/api', () => ({
  authFetch: (...a: unknown[]) => (require('@tests/unit/helpers/authFetchMock').authFetchMock as (...x: unknown[]) => unknown)(...a),
  getApiUrl: (p: string) => `http://api${p}`,
  getAuthFetchHeaders: (extra?: any) => ({ ...(extra || {}), Authorization: 'Bearer t' }),
  getAuthWsUrl: (p: string) => `ws://api${p}?token=t`,
  API_ENDPOINTS: { VOICE_RECOGNIZE: '/api/voice/recognize', CHAT: '/api/chat', VOICE_SYNTHESIZE: '/api/voice/synthesize' },
}));
jest.mock('@/contexts/AppContext', () => ({
  useAppContext: () => ({ state: {} }),
  useAppActions: () => ({
    showNotification: mock.notify,
    setSpeaking: mock.setSpeaking,
    setRecording: mock.setRecording,
  }),
}));
jest.mock('@/contexts/SocketContext', () => ({ useSocket: () => ({ isConnected: mock.isConnected }) }));
jest.mock('@/components/VoiceVisualization3D', () => ({
  __esModule: true,
  default: ({ stream }: any) => <div data-testid="viz3d" data-has-stream={String(Boolean(stream))} />,
}));

// ---------------------------------------------------------------------------
// Фейки браузерных API
// ---------------------------------------------------------------------------
const wsInstances: any[] = [];
function FakeWS(this: any, url: string) {
  this.url = url;
  this.readyState = 0;
  this.sent = [];
  wsInstances.push(this);
}
FakeWS.prototype.send = function send(this: any, d: any) {
  this.sent.push(d);
};
FakeWS.prototype.open = function open(this: any) {
  this.readyState = 1;
  if (this.onopen) this.onopen();
};
FakeWS.prototype.msg = function msg(this: any, data: any) {
  if (this.onmessage) this.onmessage({ data: typeof data === 'string' ? data : JSON.stringify(data) });
};
(FakeWS as any).CONNECTING = 0;
(FakeWS as any).OPEN = 1;
(FakeWS as any).CLOSING = 2;
(FakeWS as any).CLOSED = 3;

const mr: { last: any; supported: string[] } = { last: null, supported: [] };
function FakeMR(this: any, stream: any, options?: any) {
  this.stream = stream;
  this.options = options;
  this.state = 'inactive';
  mr.last = this;
}
FakeMR.isTypeSupported = (t: string) => mr.supported.includes(t);
FakeMR.prototype.start = function start(this: any, ms: number) {
  this.state = 'recording';
  this.startArg = ms;
};
FakeMR.prototype.stop = function stop(this: any) {
  this.state = 'inactive';
  if (this.onstop) this.onstop();
};

const ctx: { last: any } = { last: null };
function FakeAudioCtx(this: any) {
  this.state = 'running';
  this.analyser = { fftSize: 0, frequencyBinCount: 8, getByteFrequencyData: jest.fn() };
  ctx.last = this;
}
FakeAudioCtx.prototype.createAnalyser = function createAnalyser(this: any) {
  return this.analyser;
};
FakeAudioCtx.prototype.createMediaStreamSource = function createMediaStreamSource() {
  return { connect: jest.fn() };
};
FakeAudioCtx.prototype.close = function close(this: any) {
  this.state = 'closed';
};

const aud: { last: any; playError: any } = { last: null, playError: null };
function FakeAudio(this: any, src: string) {
  this.src = src;
  this.pause = jest.fn();
  aud.last = this;
}
FakeAudio.prototype.play = function play() {
  return aud.playError ? Promise.reject(aud.playError) : Promise.resolve();
};

const fetchMock = jest.fn();
let track: { stop: jest.Mock; kind: string; label: string };
let stream: any;
const getUserMedia = jest.fn();

async function flush(n = 8) {
  await act(async () => {
    let i = 0;
    while (i < n) {
      await Promise.resolve();
      i += 1;
    }
  });
}
function tick(n: number) {
  Array.from({ length: n }).forEach(() => {
    act(() => { jest.advanceTimersByTime(1000); });
  });
}
const mic = () => screen.getByTestId('MicIcon').closest('button') as HTMLButtonElement;
const chunk = (size = 500) => ({ data: new Blob([new Uint8Array(size)]) });

/** Нажимает микрофон; при openWs открывает созданный WebSocket. */
async function startRecording(opts: { openWs?: boolean } = {}) {
  fireEvent.click(mic());
  await flush();
  const ws = wsInstances[wsInstances.length - 1];
  if (opts.openWs && ws) {
    act(() => ws.open());
  }
  return ws;
}
async function stopViaButton() {
  await act(async () => {
    fireEvent.click(screen.getByText('Остановить'));
  });
  await flush();
}

describe('VoicePage', () => {
  beforeEach(() => {
    jest.useFakeTimers('modern');
    reset();
    wsInstances.length = 0;
    mr.last = null;
    mr.supported = [];
    ctx.last = null;
    aud.last = null;
    aud.playError = null;
    fetchMock.mockReset();
    track = { stop: jest.fn(), kind: 'audio', label: 'mic' };
    stream = { getTracks: () => [track] };
    getUserMedia.mockReset().mockResolvedValue(stream);
    (global as any).fetch = fetchMock;
    (global as any).WebSocket = FakeWS;
    (global as any).MediaRecorder = FakeMR;
    (global as any).AudioContext = FakeAudioCtx;
    (global as any).Audio = FakeAudio;
    (global as any).requestAnimationFrame = jest.fn(() => 1);
    (global as any).cancelAnimationFrame = jest.fn();
    Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia } });
    (URL as any).createObjectURL = jest.fn(() => 'blob:audio');
    (URL as any).revokeObjectURL = jest.fn();
    jest.spyOn(console, 'log').mockImplementation(() => {});
    jest.spyOn(console, 'error').mockImplementation(() => {});
  });
  afterEach(() => {
    jest.useRealTimers();
    jest.restoreAllMocks();
  });

  describe('начальный экран', () => {
    it('показывает подсказку, статус соединения и серый индикатор WebSocket', () => {
      render(<VoicePage />);
      expect(screen.getByText('Нажмите для прослушивания')).toBeTruthy();
      expect(screen.getByText('Подключено')).toBeTruthy();
      expect(screen.getByText('WebSocket подключится при записи')).toBeTruthy();
      expect(screen.queryByText(/Нет соединения с сервером/)).toBeNull();
      expect(mock.setRecording).toHaveBeenCalledWith(false);
      expect(mock.setSpeaking).toHaveBeenCalledWith(false);
    });

    it('без соединения: статус «Отключено» и предупреждение', () => {
      mock.isConnected = false;
      render(<VoicePage />);
      expect(screen.getByText('Отключено')).toBeTruthy();
      expect(screen.getByText('Нет соединения с сервером. Голосовой чат недоступен.')).toBeTruthy();
    });
  });

  describe('запись', () => {
    it('первое нажатие: подключает WebSocket, берёт микрофон, запускает MediaRecorder и показывает 3D-режим', async () => {
      render(<VoicePage />);
      const ws = await startRecording();
      expect(mock.notify).toHaveBeenCalledWith('info', 'Подключаю голосовой чат...');
      expect(ws.url).toBe('ws://api/ws/voice?token=t');
      expect(getUserMedia).toHaveBeenCalledWith({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      expect(mr.last!.startArg).toBe(1000);
      expect(mr.last!.options).toBeUndefined();
      expect(ctx.last!.analyser.fftSize).toBe(256);
      expect(screen.getByTestId('viz3d').getAttribute('data-has-stream')).toBe('true');
      expect(screen.getByText(/Прослушивание\.\.\. 0:00/)).toBeTruthy();
      expect(mock.notify).toHaveBeenCalledWith('info', 'Запись началась. Говорите...');
      expect(mock.setRecording).toHaveBeenLastCalledWith(true);
    });

    it.each([
      ['audio/wav', ['audio/wav']],
      ['audio/webm;codecs=opus', ['audio/webm;codecs=opus', 'audio/webm']],
      ['audio/ogg;codecs=opus', ['audio/ogg;codecs=opus']],
    ])('выбирается лучший поддерживаемый формат: %s', async (expected, supported) => {
      mr.supported = supported;
      render(<VoicePage />);
      await startRecording();
      expect(mr.last!.options).toEqual({ mimeType: expected });
    });

    it('таймер записи показывает минуты и секунды', async () => {
      render(<VoicePage />);
      await startRecording();
      tick(65);
      expect(screen.getByText(/Прослушивание\.\.\. 1:05/)).toBeTruthy();
    });

    it('каждые 2 секунды последний чанк отправляется в WebSocket для real-time распознавания', async () => {
      render(<VoicePage />);
      const ws = await startRecording({ openWs: true });
      act(() => { mr.last!.ondataavailable(chunk(300)); });
      act(() => { mr.last!.ondataavailable({ data: new Blob([]) }); }); // пустой чанк игнорируется
      tick(3);
      const blobs = ws.sent.filter((x) => x instanceof Blob);
      expect(blobs.length).toBeGreaterThan(0);
      expect(blobs[0].size).toBe(300);
    });

    it('real-time чанк не отправляется, если WebSocket не открыт', async () => {
      render(<VoicePage />);
      const ws = await startRecording();
      act(() => { mr.last!.ondataavailable(chunk(300)); });
      tick(4);
      expect(ws.sent).toEqual([]);
    });

    it('ошибка отправки real-time чанка логируется', async () => {
      render(<VoicePage />);
      const ws = await startRecording({ openWs: true });
      ws.send = () => { throw new Error('closed'); };
      act(() => { mr.last!.ondataavailable(chunk(300)); });
      tick(3);
      expect(console.error).toHaveBeenCalledWith('Ошибка real-time распознавания:', expect.any(Error));
    });

    it('при уже открытом WebSocket шлёт start_listening и reset_processing без нового подключения', async () => {
      render(<VoicePage />);
      const ws = await startRecording({ openWs: true });
      await stopViaButton();
      const count = wsInstances.length;
      fireEvent.click(mic());
      await flush();
      expect(wsInstances.length).toBe(count);
      const msgs = ws.sent.filter((x) => typeof x === 'string').map((x) => JSON.parse(x).type);
      expect(msgs).toEqual(expect.arrayContaining(['start_listening', 'reset_processing']));
      expect(mock.notify).toHaveBeenCalledWith('info', 'Отправляю команду начала прослушивания...');
    });

    it('уровень звука и автоостановка по тишине не работают: updateAudioLevel видит isRecording=false (known issue)', async () => {
      // Как правильно: после старта записи должен запускаться цикл requestAnimationFrame,
      // обновляющий уровень и таймер тишины, а через 5 с без речи запись должна останавливаться
      // («Автоостановка через 5 сек тишины» в UI). Сейчас updateAudioLevel вызывается из замыкания,
      // где isRecording ещё false, и цикл не стартует: ни уровня, ни автоостановки.
      render(<VoicePage />);
      await startRecording();
      expect((global as any).requestAnimationFrame).not.toHaveBeenCalled();
      expect(ctx.last!.analyser.getByteFrequencyData).not.toHaveBeenCalled();
      tick(12);
      expect(mock.notify).not.toHaveBeenCalledWith('info', 'Автоматическая остановка: не обнаружена речь');
      expect(screen.getByText('Остановить')).toBeTruthy();
    });

    it.each([
      ['NotAllowedError', 'Доступ к микрофону заблокирован. Разрешите доступ в браузере.'],
      ['NotFoundError', 'Микрофон не найден'],
      ['SomethingElse', 'Не удалось получить доступ к микрофону'],
    ])('ошибка getUserMedia %s показывает понятное сообщение', async (name, text) => {
      getUserMedia.mockRejectedValue({ name });
      render(<VoicePage />);
      await startRecording();
      expect(mock.notify).toHaveBeenCalledWith('error', text);
      expect(screen.getByText('Нажмите для прослушивания')).toBeTruthy();
    });

    it('ошибка после получения потока: повторное нажатие сначала останавливает прежний трек', async () => {
      mr.supported = [];
      const Orig = (global as any).MediaRecorder;
      (global as any).MediaRecorder = function () { throw new Error('no recorder'); };
      (global as any).MediaRecorder.isTypeSupported = () => false;
      render(<VoicePage />);
      await startRecording();
      expect(mock.notify).toHaveBeenCalledWith('error', 'Не удалось получить доступ к микрофону');
      (global as any).MediaRecorder = Orig;
      fireEvent.click(mic());
      await flush();
      expect(track.stop).toHaveBeenCalled();
      expect(screen.getByText('Остановить')).toBeTruthy();
    });

    it('ошибка MediaRecorder в процессе записи завершает запись', async () => {
      render(<VoicePage />);
      await startRecording();
      act(() => { mr.last!.onerror({}); });
      expect(mock.notify).toHaveBeenCalledWith('error', 'Ошибка записи аудио');
      expect(screen.getByText('Нажмите для прослушивания')).toBeTruthy();
    });
  });

  describe('остановка и обработка записи', () => {
    it('кнопка «Остановить»: останавливает рекордер, поток, контекст, шлёт stop_processing при открытом сокете', async () => {
      render(<VoicePage />);
      const ws = await startRecording({ openWs: true });
      act(() => { mr.last!.ondataavailable(chunk(10)); });
      await stopViaButton();
      expect(mr.last!.state).toBe('inactive');
      expect(track.stop).toHaveBeenCalled();
      expect(ctx.last!.state).toBe('closed');
      const stop = ws.sent.filter((x) => typeof x === 'string').map((x) => JSON.parse(x)).find((m) => m.type === 'stop_processing');
      expect(stop).toBeTruthy();
      expect(typeof stop.timestamp).toBe('string');
      expect(mock.notify).toHaveBeenCalledWith('info', 'Все процессы остановлены');
      expect(mock.setRecording).toHaveBeenLastCalledWith(false);
      expect(screen.getByText('Нажмите для прослушивания')).toBeTruthy();
    });

    it('stop_processing не отправляется, если WebSocket не подключён или отправка падает', async () => {
      render(<VoicePage />);
      const ws = await startRecording();
      await stopViaButton();
      expect(ws.sent.filter((x) => typeof x === 'string').length).toBe(0);
      expect(console.log).toHaveBeenCalledWith('WebSocket не активен, пропускаю отправку команды остановки');
    });

    it('сбой отправки stop_processing не мешает очистке', async () => {
      render(<VoicePage />);
      const ws = await startRecording({ openWs: true });
      ws.send = () => { throw new Error('boom'); };
      await stopViaButton();
      expect(console.error).toHaveBeenCalledWith('Ошибка отправки команды остановки:', expect.any(Error));
      expect(mock.notify).toHaveBeenCalledWith('info', 'Все процессы остановлены');
    });

    it('слишком короткая запись: предупреждение и без отправки', async () => {
      render(<VoicePage />);
      await startRecording();
      act(() => { mr.last!.ondataavailable(chunk(10)); });
      await stopViaButton();
      expect(mock.notify).toHaveBeenCalledWith('warning', 'Запись слишком короткая, попробуйте еще раз');
      expect(fetchMock).not.toHaveBeenCalled();
      expect(screen.queryByText('Обрабатываю речь...')).toBeNull();
    });

    it('первая запись уходит через HTTP-fallback, а не через только что открытый WebSocket (known issue)', async () => {
      // Как правильно: после подключения сокета запись должна отправляться по WebSocket.
      // Сейчас onstop замыкает voiceSocket=null из момента нажатия, поэтому первая запись
      // всегда идёт в HTTP-fallback с предупреждением «WebSocket не подключен».
      fetchMock.mockResolvedValue({ ok: true, json: async () => ({ success: true, text: '' }), text: async () => '' });
      render(<VoicePage />);
      const ws = await startRecording({ openWs: true });
      act(() => { mr.last!.ondataavailable(chunk(500)); });
      await stopViaButton();
      expect(mock.notify).toHaveBeenCalledWith('warning', 'WebSocket не подключен, использую fallback...');
      expect(ws.sent.some((x) => x instanceof Blob)).toBe(false);
    });

    it('вторая запись (сокет уже открыт) отправляется по WebSocket', async () => {
      render(<VoicePage />);
      const ws = await startRecording({ openWs: true });
      await stopViaButton();
      fireEvent.click(mic());
      await flush();
      act(() => { mr.last!.ondataavailable(chunk(500)); });
      await stopViaButton();
      expect(ws.sent.some((x) => x instanceof Blob && x.size === 500)).toBe(true);
      expect(mock.notify).toHaveBeenCalledWith('info', 'Отправляю голос на обработку...');
    });
  });

  describe('HTTP-fallback', () => {
    async function recordAndStop() {
      render(<VoicePage />);
      await startRecording();
      act(() => { mr.last!.ondataavailable(chunk(500)); });
      await stopViaButton();
    }
    const json = (data: any, ok = true, status = 200) => ({ ok, status, json: async () => data, text: async () => 'текст ошибки', blob: async () => new Blob([new Uint8Array(20)]) });

    it('без соединения с сервером: ошибка и без запроса', async () => {
      mock.isConnected = false;
      await recordAndStop();
      expect(mock.notify).toHaveBeenCalledWith('error', 'Нет соединения с сервером');
      expect(fetchMock).not.toHaveBeenCalled();
    });

    it('ошибка HTTP распознавания показывает статус', async () => {
      fetchMock.mockResolvedValueOnce(json({}, false, 502));
      await recordAndStop();
      expect(fetchMock.mock.calls[0][0]).toBe('http://api/api/voice/recognize');
      expect((fetchMock.mock.calls[0][1].body as FormData).get('audio_file')).toBeInstanceOf(Blob);
      expect(mock.notify).toHaveBeenCalledWith('error', 'Ошибка распознавания: 502');
    });

    it('распознавание не удалось (success=false)', async () => {
      fetchMock.mockResolvedValueOnce(json({ success: false }));
      await recordAndStop();
      expect(mock.notify).toHaveBeenCalledWith('error', 'Ошибка распознавания речи');
    });

    it('пустой распознанный текст — предупреждение, без запроса к LLM', async () => {
      fetchMock.mockResolvedValueOnce(json({ success: true, text: '   ' }));
      await recordAndStop();
      expect(mock.notify).toHaveBeenCalledWith('warning', 'Речь не распознана. Попробуйте еще раз.');
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });

    it('сетевой сбой распознавания', async () => {
      fetchMock.mockRejectedValueOnce(new Error('net'));
      await recordAndStop();
      expect(mock.notify).toHaveBeenCalledWith('error', 'Ошибка подключения к серверу распознавания');
      expect(screen.queryByText('Обрабатываю речь...')).toBeNull();
    });

    it('полный цикл: распознавание → чат → синтез → воспроизведение до конца', async () => {
      fetchMock
        .mockResolvedValueOnce(json({ success: true, text: 'привет' }))
        .mockResolvedValueOnce(json({ success: true, response: 'здравствуйте' }))
        .mockResolvedValueOnce(json({}));
      await recordAndStop();
      await flush();
      const chatCall = fetchMock.mock.calls[1];
      expect(chatCall[0]).toBe('http://api/api/chat');
      expect(chatCall[1].headers).toEqual({ 'Content-Type': 'application/json', Authorization: 'Bearer t' });
      expect(JSON.parse(chatCall[1].body)).toEqual({ message: 'привет', streaming: false });
      const synth = fetchMock.mock.calls[2];
      expect(synth[0]).toBe('http://api/api/voice/synthesize');
      expect(JSON.parse(synth[1].body)).toEqual({ text: 'здравствуйте', voice_id: 'ru', voice_speaker: 'baya' });
      expect(mock.notify).toHaveBeenCalledWith('success', 'Речь распознана');
      expect(screen.getByText('Говорю ответ...')).toBeTruthy();
      expect(mic().disabled).toBe(true);
      act(() => { aud.last!.onended(); });
      expect(screen.queryByText('Говорю ответ...')).toBeNull();
      expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:audio');
    });

    it('ошибка воспроизведения синтезированной речи', async () => {
      fetchMock
        .mockResolvedValueOnce(json({ success: true, text: 'привет' }))
        .mockResolvedValueOnce(json({ success: true, response: 'ответ' }))
        .mockResolvedValueOnce(json({}));
      await recordAndStop();
      await flush();
      act(() => { aud.last!.onerror(); });
      expect(mock.notify).toHaveBeenCalledWith('error', 'Ошибка воспроизведения речи');
      expect(screen.queryByText('Говорю ответ...')).toBeNull();
    });

    it('ответ LLM с success=false / исключение при отправке', async () => {
      fetchMock
        .mockResolvedValueOnce(json({ success: true, text: 'привет' }))
        .mockResolvedValueOnce(json({ success: false }));
      await recordAndStop();
      await flush();
      expect(mock.notify).toHaveBeenCalledWith('error', 'Ошибка получения ответа от astrachat');
    });

    it('сетевой сбой при отправке в LLM', async () => {
      fetchMock
        .mockResolvedValueOnce(json({ success: true, text: 'привет' }))
        .mockRejectedValueOnce(new Error('net'));
      await recordAndStop();
      await flush();
      expect(mock.notify).toHaveBeenCalledWith('error', 'Ошибка отправки сообщения');
    });

    it('ошибка синтеза речи (HTTP) и пустой ответ LLM', async () => {
      fetchMock
        .mockResolvedValueOnce(json({ success: true, text: 'привет' }))
        .mockResolvedValueOnce(json({ success: true, response: 'ответ' }))
        .mockResolvedValueOnce(json({}, false, 500));
      await recordAndStop();
      await flush();
      expect(mock.notify).toHaveBeenCalledWith('error', 'Ошибка синтеза речи');
      expect(screen.queryByText('Говорю ответ...')).toBeNull();
    });

    it('пустой текст ответа не синтезируется', async () => {
      fetchMock
        .mockResolvedValueOnce(json({ success: true, text: 'привет' }))
        .mockResolvedValueOnce(json({ success: true, response: '  ' }));
      await recordAndStop();
      await flush();
      expect(fetchMock).toHaveBeenCalledTimes(2);
    });
  });

  describe('сообщения WebSocket', () => {
    async function connected() {
      render(<VoicePage />);
      const ws = await startRecording({ openWs: true });
      return ws;
    }

    it('открытие: уведомление и индикатор «Real-Time»', async () => {
      const ws = await connected();
      await stopViaButton();
      expect(mock.notify).toHaveBeenCalledWith('success', 'Голосовой чат подключен');
      expect(screen.getByText('Real-Time Голосовой Чат')).toBeTruthy();
      expect(ws.url).toContain('/ws/voice');
    });

    it.each([
      [{ type: 'listening_started' }, 'success', 'Готов к приему голоса'],
      [{ type: 'speech_error', error: 'шум' }, 'warning', 'шум'],
      [{ type: 'speech_error' }, 'warning', 'Ошибка распознавания речи'],
      [{ type: 'tts_error', error: 'tts' }, 'error', 'tts'],
      [{ type: 'tts_error' }, 'error', 'Ошибка синтеза речи'],
      [{ type: 'error', error: 'общая' }, 'error', 'общая'],
      [{ type: 'error' }, 'error', 'Ошибка WebSocket'],
      [{ type: 'processing_stopped' }, 'info', 'Обработка остановлена'],
      [{ type: 'processing_reset' }, 'success', 'Обработка возобновлена'],
    ])('сообщение %j → уведомление', async (payload, level, text) => {
      const ws = await connected();
      act(() => ws.msg(payload));
      expect(mock.notify).toHaveBeenCalledWith(level, text);
    });

    it('неизвестный тип и не-JSON сообщения не ломают страницу', async () => {
      const ws = await connected();
      act(() => ws.msg({ type: 'mystery' }));
      act(() => ws.msg('not json'));
      expect(console.error).toHaveBeenCalledWith('Ошибка обработки WebSocket сообщения:', expect.any(Error));
      act(() => { ws.onmessage({ data: new ArrayBuffer(2) }); });
      expect(screen.getByText('Остановить')).toBeTruthy();
    });

    it('speech_recognized накапливает real-time текст во время записи', async () => {
      const ws = await connected();
      act(() => ws.msg({ type: 'speech_recognized', text: 'привет' }));
      act(() => ws.msg({ type: 'speech_recognized', text: 'мир' }));
      expect(screen.getByText(/привет мир/)).toBeTruthy();
      expect(mock.notify).toHaveBeenCalledWith('success', 'Речь распознана в реальном времени');
    });

    it('ai_response показывает распознанный текст; «Очистить» убирает, «Отправить» шлёт в LLM', async () => {
      const ws = await connected();
      await stopViaButton();
      act(() => ws.msg({ type: 'ai_response', text: 'ответ ИИ' }));
      expect(screen.getByText('"ответ ИИ"')).toBeTruthy();
      expect(mock.notify).toHaveBeenCalledWith('success', 'Получен ответ от astrachat');
      fireEvent.click(screen.getByText('Очистить'));
      expect(screen.queryByText('"ответ ИИ"')).toBeNull();
      act(() => ws.msg({ type: 'ai_response', text: 'второй' }));
      fetchMock.mockResolvedValueOnce({ json: async () => ({ success: false }) });
      await act(async () => { fireEvent.click(screen.getByText('Отправить')); });
      expect(JSON.parse(fetchMock.mock.calls[0][1].body).message).toBe('второй');
      expect(screen.queryByText('"второй"')).toBeNull();
    });

    it('«Отправить» для пустого текста недоступно (ничего не отправляется)', async () => {
      const ws = await connected();
      await stopViaButton();
      act(() => ws.msg({ type: 'ai_response', text: '   ' }));
      await act(async () => { fireEvent.click(screen.getByText('Отправить')); });
      expect(fetchMock).not.toHaveBeenCalled();
    });

    it('двоичные данные воспроизводятся как аудио-ответ и блокируют микрофон до конца', async () => {
      const ws = await connected();
      await stopViaButton();
      await act(async () => { ws.onmessage({ data: new Blob([new Uint8Array(30)]) }); });
      expect(aud.last!.src).toBe('blob:audio');
      expect(screen.getByText('Говорю ответ...')).toBeTruthy();
      act(() => { aud.last!.onended(); });
      expect(mock.notify).toHaveBeenCalledWith('success', 'Готов к следующему запросу');
      expect(screen.getByText('Нажмите для прослушивания')).toBeTruthy();
    });

    it('ошибка воспроизведения аудио-ответа (onerror и reject play)', async () => {
      const ws = await connected();
      await stopViaButton();
      await act(async () => { ws.onmessage({ data: new Blob([new Uint8Array(30)]) }); });
      act(() => { aud.last!.onerror(); });
      expect(mock.notify).toHaveBeenCalledWith('error', 'Ошибка воспроизведения речи');
      mock.notify.mockClear();
      aud.playError = new Error('autoplay');
      await act(async () => { ws.onmessage({ data: new Blob([new Uint8Array(30)]) }); });
      expect(mock.notify).toHaveBeenCalledWith('error', 'Ошибка воспроизведения речи');
      expect(screen.queryByText('Говорю ответ...')).toBeNull();
    });

    it('ошибка сокета с закрытым состоянием запускает переподключение через 3 с', async () => {
      const ws = await connected();
      ws.readyState = (FakeWS as any).CLOSED;
      act(() => ws.onerror(new Event('error')));
      expect(mock.notify).toHaveBeenCalledWith('warning', 'Временная ошибка WebSocket, пытаюсь восстановить...');
      const before = wsInstances.length;
      act(() => { jest.advanceTimersByTime(3000); });
      expect(mock.notify).toHaveBeenCalledWith('info', 'Попытка переподключения...');
      expect(wsInstances.length).toBe(before + 1);
    });

    it('временная ошибка при открытом сокете не рвёт соединение', async () => {
      const ws = await connected();
      const before = wsInstances.length;
      act(() => ws.onerror(new Event('error')));
      act(() => { jest.advanceTimersByTime(5000); });
      expect(wsInstances.length).toBe(before);
    });

    it('неожиданное закрытие (код 1006) переподключает, нормальное (1000) — нет', async () => {
      const ws = await connected();
      await stopViaButton();
      const before = wsInstances.length;
      act(() => ws.onclose({ code: 1000, reason: 'bye' }));
      act(() => { jest.advanceTimersByTime(4000); });
      expect(wsInstances.length).toBe(before);
      expect(screen.getByText('WebSocket подключится при записи')).toBeTruthy();
      act(() => ws.onclose({ code: 1006, reason: '' }));
      expect(mock.notify).toHaveBeenCalledWith('warning', 'Соединение с голосовым чатом закрыто, переподключаюсь...');
      act(() => { jest.advanceTimersByTime(3000); });
      expect(wsInstances.length).toBe(before + 1);
    });
  });

  describe('очистка ресурсов', () => {
    it('размонтирование во время записи освобождает микрофон, контекст и сбрасывает глобальные флаги', async () => {
      const { unmount } = render(<VoicePage />);
      await startRecording();
      mock.setRecording.mockClear();
      mock.setSpeaking.mockClear();
      unmount();
      expect(mr.last!.state).toBe('inactive');
      expect(track.stop).toHaveBeenCalled();
      expect(ctx.last!.state).toBe('closed');
      expect(mock.setRecording).toHaveBeenCalledWith(false);
      expect(mock.setSpeaking).toHaveBeenCalledWith(false);
    });

    it('размонтирование во время воспроизведения останавливает аудио', async () => {
      const { unmount } = render(<VoicePage />);
      const ws = await startRecording({ openWs: true });
      await stopViaButton();
      await act(async () => { ws.onmessage({ data: new Blob([new Uint8Array(30)]) }); });
      const audio = aud.last!;
      unmount();
      expect(audio.pause).toHaveBeenCalled();
      expect(audio.src).toBe('');
    });

    it('beforeunload освобождает ресурсы так же, как размонтирование', async () => {
      render(<VoicePage />);
      await startRecording();
      mock.setRecording.mockClear();
      act(() => { window.dispatchEvent(new Event('beforeunload')); });
      expect(track.stop).toHaveBeenCalled();
      expect(ctx.last!.state).toBe('closed');
      expect(mock.setRecording).toHaveBeenCalledWith(false);
    });

    it('beforeunload во время воспроизведения останавливает аудио', async () => {
      render(<VoicePage />);
      const ws = await startRecording({ openWs: true });
      await stopViaButton();
      await act(async () => { ws.onmessage({ data: new Blob([new Uint8Array(30)]) }); });
      const audio = aud.last!;
      act(() => { window.dispatchEvent(new Event('beforeunload')); });
      expect(audio.pause).toHaveBeenCalled();
    });

    it('«Остановить» во время воспроизведения аудио-ответа прерывает его', async () => {
      render(<VoicePage />);
      const ws = await startRecording({ openWs: true });
      await act(async () => { ws.onmessage({ data: new Blob([new Uint8Array(30)]) }); });
      const audio = aud.last;
      await stopViaButton();
      expect(audio.pause).toHaveBeenCalled();
      expect(audio.src).toBe('');
      expect(screen.queryByText('Говорю ответ...')).toBeNull();
    });

    it('сбой отправки записи по WebSocket (вторая запись) показывает ошибку обработки', async () => {
      render(<VoicePage />);
      const ws = await startRecording({ openWs: true });
      await stopViaButton();
      fireEvent.click(mic());
      await flush();
      ws.send = (d: any) => { if (d instanceof Blob) throw new Error('send failed'); };
      act(() => { mr.last.ondataavailable(chunk(500)); });
      await stopViaButton();
      expect(mock.notify).toHaveBeenCalledWith('error', 'Ошибка обработки аудио');
      expect(screen.queryByText('Обрабатываю речь...')).toBeNull();
    });

    it('синтез речи прерывает ещё играющий предыдущий аудио-ответ', async () => {
      let resolveRecognize!: (v: any) => void;
      fetchMock
        .mockReturnValueOnce(new Promise((r) => { resolveRecognize = r; }))
        .mockResolvedValueOnce({ json: async () => ({ success: true, response: 'ответ' }) })
        .mockResolvedValueOnce({ ok: true, blob: async () => new Blob([new Uint8Array(5)]) });
      render(<VoicePage />);
      const ws = await startRecording({ openWs: true });
      act(() => { mr.last.ondataavailable(chunk(500)); });
      await stopViaButton();
      await act(async () => { ws.onmessage({ data: new Blob([new Uint8Array(30)]) }); });
      const first = aud.last;
      await act(async () => {
        resolveRecognize({ ok: true, json: async () => ({ success: true, text: 'привет' }) });
      });
      await flush();
      expect(first.pause).toHaveBeenCalled();
      expect(aud.last).not.toBe(first);
    });
  });
});
