import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import ChatInputBar, { ChatInputBarProps } from '@/components/ChatInputBar';

jest.mock('@/config/api', () => ({
  authFetch: (...a: unknown[]) => (require('@tests/unit/helpers/authFetchMock').authFetchMock as (...x: unknown[]) => unknown)(...a),
  getApiUrl: (p: string) => `http://api${p}`,
  API_ENDPOINTS: { VOICE_RECOGNIZE: '/voice/recognize' },
}));
const mockPrepare = jest.fn();
jest.mock('@/utils/dictationAudio', () => ({
  prepareSegmentForStt: (b: Blob) => mockPrepare(b),
}));
jest.mock('@/components/chat/SkillMentionAutocomplete', () => ({ __esModule: true, default: () => null }));
jest.mock('@/components/chat/TagMentionAutocomplete', () => ({ __esModule: true, default: () => null }));

class FakeRecorder {
  static instances: FakeRecorder[] = [];
  static supported: string[] = ['audio/webm'];
  static isTypeSupported = (t: string) => FakeRecorder.supported.includes(t);
  state = 'inactive';
  ondataavailable: ((e: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  onerror: (() => void) | null = null;
  stopThrows = false;
  constructor(public stream: unknown, public options?: { mimeType: string }) {
    FakeRecorder.instances.push(this);
  }
  start() {
    this.state = 'recording';
  }
  stop() {
    if (this.stopThrows) throw new Error('stop failed');
    this.state = 'inactive';
  }
  /** Браузер асинхронно присылает данные и stop после stop(). */
  flush(bytes = 500, type = 'audio/webm') {
    this.ondataavailable?.({ data: new Blob([new Uint8Array(bytes)], { type }) });
    this.onstop?.();
  }
}

const track = { stop: jest.fn() };
const stream = { getTracks: () => [track] };
let analyserFill = 128;
const audioClose = jest.fn().mockResolvedValue(undefined);
class FakeAudioContext {
  createMediaStreamSource() {
    return { connect: jest.fn() };
  }
  createAnalyser() {
    return {
      fftSize: 0,
      smoothingTimeConstant: 0,
      connect: jest.fn(),
      getByteTimeDomainData: (arr: Uint8Array) => arr.fill(analyserFill),
    };
  }
  close = audioClose;
}

const fetchMock = jest.fn();
const getUserMedia = jest.fn();

function lastRecorder(): FakeRecorder {
  return FakeRecorder.instances[FakeRecorder.instances.length - 1];
}

function setup(over: Partial<ChatInputBarProps> = {}) {
  const props: ChatInputBarProps = { value: 'база', onChange: jest.fn(), onKeyPress: jest.fn(), ...over };
  const utils = render(<ChatInputBar {...props} />);
  return { props, ...utils };
}

async function flushAsync(ms = 0) {
  await act(async () => {
    jest.advanceTimersByTime(ms);
  });
  await act(async () => {
    for (let i = 0; i < 10; i++) await Promise.resolve();
  });
}

async function startDictation() {
  await act(async () => {
    fireEvent.click(screen.getByLabelText('Диктовка в поле ввода'));
  });
  await flushAsync();
}

function recognizeOk(text: string) {
  fetchMock.mockResolvedValue({ ok: true, json: async () => ({ success: true, text }) });
}

describe('ChatInputBar: диктовка', () => {
  beforeEach(() => {
    jest.useFakeTimers('modern');
    FakeRecorder.instances = [];
    FakeRecorder.supported = ['audio/webm'];
    track.stop.mockClear();
    audioClose.mockClear();
    analyserFill = 128;
    mockPrepare.mockReset().mockImplementation(async (b: Blob) => b);
    fetchMock.mockReset();
    recognizeOk('привет');
    getUserMedia.mockReset().mockResolvedValue(stream);
    (global as any).fetch = fetchMock;
    (global as any).MediaRecorder = FakeRecorder;
    (window as any).AudioContext = FakeAudioContext;
    Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia } });
  });
  afterEach(() => {
    jest.useRealTimers();
    delete (global as any).MediaRecorder;
    delete (window as any).AudioContext;
    Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: undefined });
  });

  it('без MediaRecorder/getUserMedia кнопки диктовки нет', () => {
    delete (global as any).MediaRecorder;
    setup();
    expect(screen.queryByLabelText('Диктовка в поле ввода')).toBeNull();
  });

  it('кнопка заблокирована при inputDisabled/voiceDisabled', () => {
    const a = setup({ inputDisabled: true });
    expect(screen.getByLabelText('Диктовка в поле ввода')).toBeDisabled();
    a.unmount();
    setup({ voiceDisabled: true });
    expect(screen.getByLabelText('Диктовка в поле ввода')).toBeDisabled();
  });

  it('старт: запрашивает микрофон, показывает «Слушаю...», выбирает поддерживаемый формат', async () => {
    setup();
    await startDictation();
    expect(getUserMedia).toHaveBeenCalledWith({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    expect(screen.getByText('Слушаю...')).toBeInTheDocument();
    expect(screen.queryByRole('textbox')).toBeNull();
    expect(lastRecorder().options).toEqual({ mimeType: 'audio/webm' });
    expect(lastRecorder().state).toBe('recording');
    // во время записи доступны только «отмена» и «подтвердить»: обычные кнопки скрыты
    expect(screen.queryByLabelText('Диктовка в поле ввода')).toBeNull();
    expect(screen.getAllByRole('button')).toHaveLength(2);
  });

  it('если ни один формат не поддерживается, рекордер создаётся без опций', async () => {
    FakeRecorder.supported = [];
    setup();
    await startDictation();
    expect(lastRecorder().options).toBeUndefined();
  });

  it('предпочитает первый поддерживаемый формат из списка (webm;codecs=opus)', async () => {
    FakeRecorder.supported = ['audio/webm;codecs=opus', 'audio/webm'];
    setup();
    await startDictation();
    expect(lastRecorder().options).toEqual({ mimeType: 'audio/webm;codecs=opus' });
  });

  it('отказ в доступе к микрофону: диктовка не включается, ресурсы очищены', async () => {
    getUserMedia.mockRejectedValue(new Error('denied'));
    setup();
    await startDictation();
    expect(screen.queryByText('Слушаю...')).toBeNull();
    expect(screen.getByRole('textbox')).toBeInTheDocument();
    expect(FakeRecorder.instances).toHaveLength(0);
  });

  it('сегмент 2.5 с: распознанный текст дописывается к исходному, превью показывает его', async () => {
    const { props } = setup({ value: 'база' });
    await startDictation();
    const first = lastRecorder();
    act(() => {
      jest.advanceTimersByTime(2500);
    });
    expect(first.state).toBe('inactive'); // таймер остановил запись сегмента
    await act(async () => {
      first.flush();
    });
    await flushAsync();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('http://api/voice/recognize');
    expect(init.method).toBe('POST');
    expect((init.body as FormData).get('audio_file')).toBeInstanceOf(Blob);
    expect(((init.body as FormData).get('audio_file') as File).name).toBe('dictation.webm');
    expect(props.onChange).toHaveBeenLastCalledWith('база привет');
    expect(screen.getByText('привет')).toBeInTheDocument();
    // начался следующий сегмент
    expect(FakeRecorder.instances).toHaveLength(2);
    expect(lastRecorder().state).toBe('recording');
  });

  it('wav-сегмент отправляется с именем dictation.wav', async () => {
    setup();
    await startDictation();
    await act(async () => {
      lastRecorder().flush(500, 'audio/wav');
    });
    await flushAsync();
    expect(((fetchMock.mock.calls[0][1].body as FormData).get('audio_file') as File).name).toBe('dictation.wav');
  });

  it('несколько сегментов склеиваются через пробел', async () => {
    const { props } = setup({ value: '' });
    await startDictation();
    recognizeOk('раз');
    await act(async () => {
      lastRecorder().flush();
    });
    await flushAsync();
    recognizeOk('два');
    await act(async () => {
      lastRecorder().flush();
    });
    await flushAsync();
    expect(props.onChange).toHaveBeenLastCalledWith('раз два');
  });

  it('слишком короткие сегменты (<100 байт) не отправляются на распознавание', async () => {
    setup();
    await startDictation();
    await act(async () => {
      lastRecorder().flush(50);
    });
    await flushAsync();
    expect(mockPrepare).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('если подготовка сегмента вернула null — распознавания нет', async () => {
    mockPrepare.mockResolvedValue(null);
    setup();
    await startDictation();
    await act(async () => {
      lastRecorder().flush();
    });
    await flushAsync();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([
    ['HTTP-ошибка', { ok: false, json: async () => ({}) }],
    ['success=false', { ok: true, json: async () => ({ success: false }) }],
    ['пустой текст', { ok: true, json: async () => ({ success: true, text: '   ' }) }],
    ['text отсутствует', { ok: true, json: async () => ({ success: true }) }],
  ])('%s: текст не добавляется', async (_n, resp) => {
    fetchMock.mockResolvedValue(resp);
    const { props } = setup();
    await startDictation();
    await act(async () => {
      lastRecorder().flush();
    });
    await flushAsync();
    expect(props.onChange).not.toHaveBeenCalled();
  });

  it('сетевая ошибка распознавания: превью «Ошибка распознавания»', async () => {
    fetchMock.mockRejectedValue(new Error('net'));
    setup();
    await startDictation();
    await act(async () => {
      lastRecorder().flush();
    });
    await flushAsync();
    expect(screen.getByText('Ошибка распознавания')).toBeInTheDocument();
  });

  it('сетевая ошибка после уже распознанного текста не затирает превью', async () => {
    setup({ value: '' });
    await startDictation();
    await act(async () => {
      lastRecorder().flush();
    });
    await flushAsync();
    fetchMock.mockRejectedValue(new Error('net'));
    await act(async () => {
      lastRecorder().flush();
    });
    await flushAsync();
    expect(screen.getByText('привет')).toBeInTheDocument();
    expect(screen.queryByText('Ошибка распознавания')).toBeNull();
  });

  it('во время распознавания первого сегмента показывается «Распознаю...»', async () => {
    let resolveJson!: (v: unknown) => void;
    fetchMock.mockResolvedValue({ ok: true, json: () => new Promise((r) => (resolveJson = r)) });
    setup();
    await startDictation();
    await act(async () => {
      lastRecorder().flush();
    });
    await flushAsync();
    expect(screen.getByText('Распознаю...')).toBeInTheDocument();
    await act(async () => {
      resolveJson({ success: true, text: 'ок' });
    });
    await flushAsync();
    expect(screen.getByText('ок')).toBeInTheDocument();
  });

  it('галочка завершает диктовку: дожидается очереди и возвращает поле ввода', async () => {
    const { props } = setup({ value: '' });
    await startDictation();
    await act(async () => {
      fireEvent.click(screen.getAllByRole('button')[1]); // подтвердить
    });
    expect(lastRecorder().state).toBe('inactive');
    await act(async () => {
      lastRecorder().flush();
    });
    await flushAsync(200);
    await flushAsync(200);
    expect(props.onChange).toHaveBeenLastCalledWith('привет');
    expect(screen.queryByText('Слушаю...')).toBeNull();
    expect(screen.getByRole('textbox')).toBeInTheDocument();
    expect(track.stop).toHaveBeenCalled();
    expect(audioClose).toHaveBeenCalled();
  });

  it('во время финализации показывается «Распознаю...» и кнопки заблокированы', async () => {
    let resolveJson!: (v: unknown) => void;
    fetchMock.mockResolvedValue({ ok: true, json: () => new Promise((r) => (resolveJson = r)) });
    setup({ value: '' });
    await startDictation();
    await act(async () => {
      lastRecorder().flush();
    });
    await flushAsync();
    await act(async () => {
      fireEvent.click(screen.getAllByRole('button')[1]);
    });
    await act(async () => {
      lastRecorder().flush(500);
    });
    await flushAsync(100);
    screen.getAllByRole('button').forEach((b) => expect(b).toBeDisabled());
    await act(async () => {
      resolveJson({ success: true, text: 'итог' });
    });
    await flushAsync(300);
    expect(screen.queryByText('Слушаю...')).toBeNull();
  });

  it('завершение, когда рекордер уже остановлен, сразу финализирует', async () => {
    const { props } = setup({ value: 'x' });
    await startDictation();
    act(() => {
      jest.advanceTimersByTime(2500); // таймер вызвал stop(), событие stop ещё не пришло
    });
    expect(lastRecorder().state).toBe('inactive');
    await act(async () => {
      fireEvent.click(screen.getAllByRole('button')[1]);
    });
    await flushAsync(300);
    expect(screen.getByRole('textbox')).toBeInTheDocument();
    expect(props.onChange).not.toHaveBeenCalled();
  });

  it('крестик отменяет диктовку: возвращается исходный текст, запросов нет', async () => {
    const { props } = setup({ value: 'исходный' });
    await startDictation();
    await act(async () => {
      fireEvent.click(screen.getAllByRole('button')[0]); // отмена
    });
    expect(lastRecorder().state).toBe('inactive');
    await act(async () => {
      lastRecorder().flush();
    });
    expect(props.onChange).toHaveBeenLastCalledWith('исходный');
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByRole('textbox')).toBeInTheDocument();
    expect(track.stop).toHaveBeenCalled();
  });

  it('отмена, когда рекордер уже остановлен, возвращает текст сразу', async () => {
    const { props } = setup({ value: 'исходный' });
    await startDictation();
    act(() => {
      jest.advanceTimersByTime(2500);
    });
    await act(async () => {
      fireEvent.click(screen.getAllByRole('button')[0]);
    });
    expect(props.onChange).toHaveBeenLastCalledWith('исходный');
    expect(screen.getByRole('textbox')).toBeInTheDocument();
  });

  it('ошибка рекордера останавливает запись и отменяет обработку', async () => {
    const { props } = setup({ value: 'исходный' });
    await startDictation();
    const rec = lastRecorder();
    await act(async () => {
      rec.onerror?.();
    });
    expect(rec.state).toBe('inactive');
    await act(async () => {
      rec.flush();
    });
    expect(props.onChange).toHaveBeenLastCalledWith('исходный');
    expect(screen.getByRole('textbox')).toBeInTheDocument();
  });

  it('ошибка рекордера и сбой stop(): состояние всё равно сбрасывается', async () => {
    const { props } = setup({ value: 'исх' });
    await startDictation();
    const rec = lastRecorder();
    rec.stopThrows = true;
    await act(async () => {
      rec.onerror?.();
    });
    expect(props.onChange).toHaveBeenLastCalledWith('исх');
    expect(screen.getByRole('textbox')).toBeInTheDocument();
  });

  it('ошибка уже остановленного рекордера ничего не останавливает повторно', async () => {
    setup();
    await startDictation();
    const rec = lastRecorder();
    rec.state = 'inactive';
    rec.stopThrows = true;
    await act(async () => {
      expect(() => rec.onerror?.()).not.toThrow();
    });
  });

  it('индикатор голоса: 72 столбика; при речи они выше, чем в тишине', async () => {
    setup();
    await startDictation();
    const barHeights = () => {
      const wave = screen.getByText('Слушаю...').nextElementSibling as HTMLElement;
      return Array.from(wave.children).map((b) => parseFloat(getComputedStyle(b).height));
    };
    const quiet = barHeights();
    expect(quiet).toHaveLength(72);
    analyserFill = 250;
    act(() => {
      jest.advanceTimersByTime(2000);
    });
    const loud = barHeights();
    expect(Math.max(...loud)).toBeGreaterThan(Math.max(...quiet));
    analyserFill = 128;
    act(() => {
      jest.advanceTimersByTime(3000);
    });
    expect(Math.max(...barHeights())).toBeLessThan(Math.max(...loud));
  });

  it('размонтирование во время записи останавливает рекордер и микрофон', async () => {
    const { unmount } = setup();
    await startDictation();
    const rec = lastRecorder();
    unmount();
    expect(rec.state).toBe('inactive');
    expect(track.stop).toHaveBeenCalled();
  });

  it('размонтирование: сбой stop() рекордера не пробрасывается', async () => {
    const { unmount } = setup();
    await startDictation();
    lastRecorder().stopThrows = true;
    expect(() => unmount()).not.toThrow();
  });

  it('во время диктовки скрыты кнопки отправки, голоса и отчёта; после отмены возвращаются', async () => {
    setup({ onSendClick: jest.fn(), onVoiceClick: jest.fn(), showReportButton: true, onReportClick: jest.fn() });
    expect(screen.getByLabelText('Отправить')).toBeInTheDocument();
    await startDictation();
    expect(screen.queryByLabelText('Отправить')).toBeNull();
    expect(screen.queryByLabelText('Голосовой ввод')).toBeNull();
    expect(screen.queryByLabelText('Сгенерировать отчет об уверенности')).toBeNull();
    await act(async () => {
      fireEvent.click(screen.getAllByRole('button')[0]);
      lastRecorder().flush();
    });
    expect(screen.getByLabelText('Отправить')).toBeInTheDocument();
  });
});
