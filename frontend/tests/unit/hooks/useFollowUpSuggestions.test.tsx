import { act, renderHook } from '@testing-library/react';

const mockFetchFollowUps = jest.fn();
jest.mock('@/chat/fetchFollowUpSuggestions', () => ({
  fetchFollowUpSuggestions: (...args: unknown[]) => mockFetchFollowUps(...args),
}));
jest.mock('@/utils/debugReact185', () => ({ astra185Mark: jest.fn() }));

import { useFollowUpSuggestions } from '@/hooks/useFollowUpSuggestions';
import { LAST_SELECTED_MODEL_PATH_STORAGE_KEY } from '@/utils/modelThinking';

const DELAY = 2500;

const m = (id: string, role: 'user' | 'assistant', content: string, extra: any = {}) => ({
  id,
  role,
  content,
  timestamp: 't',
  ...extra,
});

const SUGGESTIONS = [{ id: 's1', title: 'T', content: 'C', source: 'general' }];

let patch: jest.Mock;

type Props = {
  chatId?: string;
  messages: any[];
  enabled?: boolean;
  showScope?: 'last' | 'all';
};

function setup(initial: Props) {
  return renderHook(
    (p: Props) =>
      useFollowUpSuggestions({
        chatId: p.chatId,
        messages: p.messages,
        enabled: p.enabled ?? true,
        showScope: (p.showScope ?? 'last') as any,
        patchMessageFields: patch,
      }),
    { initialProps: initial },
  );
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

beforeEach(() => {
  jest.useFakeTimers();
  mockFetchFollowUps.mockReset();
  mockFetchFollowUps.mockResolvedValue(SUGGESTIONS);
  patch = jest.fn();
  localStorage.clear();
});

afterEach(() => {
  jest.useRealTimers();
});

describe('useFollowUpSuggestions: запуск запроса', () => {
  it('не запрашивает до истечения задержки, затем запрашивает и пишет подсказки в сообщение', async () => {
    const messages = [m('1', 'user', 'q'), m('2', 'assistant', 'ответ')];
    setup({ chatId: 'c1', messages });
    act(() => jest.advanceTimersByTime(DELAY - 1));
    expect(mockFetchFollowUps).not.toHaveBeenCalled();
    act(() => jest.advanceTimersByTime(1));
    expect(mockFetchFollowUps).toHaveBeenCalledTimes(1);
    const args = mockFetchFollowUps.mock.calls[0][0];
    expect(args.messages.map((x: any) => x.id)).toEqual(['1', '2']);
    expect(args.modelPath).toBeNull();
    expect(args.signal).toBeInstanceOf(AbortSignal);
    await flush();
    expect(patch).toHaveBeenCalledWith('c1', '2', { followUpSuggestions: SUGGESTIONS });
  });

  it('передаёт модель из localStorage', () => {
    localStorage.setItem(LAST_SELECTED_MODEL_PATH_STORAGE_KEY, 'm/x');
    setup({ chatId: 'c1', messages: [m('1', 'user', 'q'), m('2', 'assistant', 'a')] });
    act(() => jest.advanceTimersByTime(DELAY));
    expect(mockFetchFollowUps.mock.calls[0][0].modelPath).toBe('m/x');
  });

  it('пустой список подсказок пишется как undefined', async () => {
    mockFetchFollowUps.mockResolvedValue([]);
    setup({ chatId: 'c1', messages: [m('1', 'assistant', 'a')] });
    act(() => jest.advanceTimersByTime(DELAY));
    await flush();
    expect(patch).toHaveBeenCalledWith('c1', '1', { followUpSuggestions: undefined });
  });

  it('ничего не делает при enabled=false или без chatId', () => {
    setup({ chatId: 'c1', messages: [m('1', 'assistant', 'a')], enabled: false });
    setup({ chatId: undefined, messages: [m('1', 'assistant', 'a')] });
    act(() => jest.advanceTimersByTime(DELAY * 2));
    expect(mockFetchFollowUps).not.toHaveBeenCalled();
  });

  it('пока идёт любой стрим, запрос не стартует', () => {
    setup({
      chatId: 'c1',
      messages: [m('1', 'assistant', 'old'), m('2', 'assistant', 'new', { isStreaming: true })],
    });
    act(() => jest.advanceTimersByTime(DELAY * 2));
    expect(mockFetchFollowUps).not.toHaveBeenCalled();
  });

  it.each([
    ['сообщение пользователя', m('1', 'user', 'q')],
    ['пустой ответ', m('1', 'assistant', '   ')],
    ['multi-LLM ответ', m('1', 'assistant', 'a', { multiLLMResponses: [{ model: 'x' }] })],
    ['генерация изображения', m('1', 'assistant', 'a', { isImageGenerating: true })],
  ])('не подходит для подсказок: %s', (_name, message) => {
    setup({ chatId: 'c1', messages: [message] });
    act(() => jest.advanceTimersByTime(DELAY));
    expect(mockFetchFollowUps).not.toHaveBeenCalled();
  });

  it('showScope=last берёт только последнее сообщение, all — все подходящие', () => {
    const messages = [m('1', 'assistant', 'a1'), m('2', 'user', 'q'), m('3', 'assistant', 'a3')];
    setup({ chatId: 'c1', messages, showScope: 'last' });
    act(() => jest.advanceTimersByTime(DELAY));
    expect(mockFetchFollowUps).toHaveBeenCalledTimes(1);

    mockFetchFollowUps.mockClear();
    setup({ chatId: 'c2', messages, showScope: 'all' });
    act(() => jest.advanceTimersByTime(DELAY));
    expect(mockFetchFollowUps).toHaveBeenCalledTimes(2);
    // история для первого — только до него включительно
    const hist = mockFetchFollowUps.mock.calls.map((c) => c[0].messages.map((x: any) => x.id));
    expect(hist).toEqual([['1'], ['1', '2', '3']]);
  });

  it('showScope=last и пустой список сообщений — без запросов', () => {
    setup({ chatId: 'c1', messages: [] });
    act(() => jest.advanceTimersByTime(DELAY));
    expect(mockFetchFollowUps).not.toHaveBeenCalled();
  });

  it('если подсказки уже есть — запрос не отправляется', () => {
    setup({
      chatId: 'c1',
      messages: [m('1', 'assistant', 'a', { followUpSuggestions: SUGGESTIONS })],
    });
    act(() => jest.advanceTimersByTime(DELAY));
    expect(mockFetchFollowUps).not.toHaveBeenCalled();
  });

  it('повторный рендер с теми же сообщениями не создаёт второй таймер/запрос', () => {
    const messages = [m('1', 'assistant', 'a')];
    const { rerender } = setup({ chatId: 'c1', messages });
    rerender({ chatId: 'c1', messages: [...messages] });
    act(() => jest.advanceTimersByTime(DELAY));
    expect(mockFetchFollowUps).toHaveBeenCalledTimes(1);
    rerender({ chatId: 'c1', messages: [...messages] });
    act(() => jest.advanceTimersByTime(DELAY));
    expect(mockFetchFollowUps).toHaveBeenCalledTimes(1);
  });

  it('inflight: пока запрос не завершён, новый не стартует', () => {
    mockFetchFollowUps.mockReturnValue(new Promise(() => {}));
    const messages = [m('1', 'assistant', 'a')];
    const { rerender } = setup({ chatId: 'c1', messages });
    act(() => jest.advanceTimersByTime(DELAY));
    rerender({ chatId: 'c1', messages: [...messages] });
    act(() => jest.advanceTimersByTime(DELAY));
    expect(mockFetchFollowUps).toHaveBeenCalledTimes(1);
  });
});

describe('useFollowUpSuggestions: ошибки', () => {
  it('ошибка запроса очищает подсказки и разрешает повтор', async () => {
    mockFetchFollowUps.mockRejectedValueOnce(new Error('500'));
    const messages = [m('1', 'assistant', 'a')];
    const { rerender } = setup({ chatId: 'c1', messages });
    act(() => jest.advanceTimersByTime(DELAY));
    await flush();
    expect(patch).toHaveBeenCalledWith('c1', '1', { followUpSuggestions: undefined });
    mockFetchFollowUps.mockResolvedValue(SUGGESTIONS);
    rerender({ chatId: 'c1', messages: [...messages] });
    act(() => jest.advanceTimersByTime(DELAY));
    expect(mockFetchFollowUps).toHaveBeenCalledTimes(2);
  });

  it('результат отменённого запроса не пишется в сообщение (успех)', async () => {
    let resolve!: (v: unknown) => void;
    mockFetchFollowUps.mockReturnValue(new Promise((r) => (resolve = r)));
    setup({ chatId: 'c1', messages: [m('1', 'assistant', 'a')] });
    act(() => jest.advanceTimersByTime(DELAY));
    act(() => {
      window.dispatchEvent(new Event('astrachat-abort-follow-ups'));
    });
    await act(async () => {
      resolve(SUGGESTIONS);
    });
    expect(patch).not.toHaveBeenCalled();
  });

  it('ошибка отменённого запроса тоже игнорируется', async () => {
    let reject!: (e: unknown) => void;
    mockFetchFollowUps.mockReturnValue(new Promise((_r, rej) => (reject = rej)));
    setup({ chatId: 'c1', messages: [m('1', 'assistant', 'a')] });
    act(() => jest.advanceTimersByTime(DELAY));
    act(() => {
      window.dispatchEvent(new Event('astrachat-abort-follow-ups'));
    });
    await act(async () => {
      reject(new Error('aborted'));
    });
    expect(patch).not.toHaveBeenCalled();
  });
});

describe('useFollowUpSuggestions: отмена и очистка', () => {
  it('событие astrachat-abort-follow-ups отменяет отложенный таймер и сам запрос', () => {
    setup({ chatId: 'c1', messages: [m('1', 'assistant', 'a')] });
    act(() => {
      window.dispatchEvent(new Event('astrachat-abort-follow-ups'));
    });
    act(() => jest.advanceTimersByTime(DELAY));
    expect(mockFetchFollowUps).not.toHaveBeenCalled();
  });

  it('abort рвёт signal уже отправленного запроса', () => {
    mockFetchFollowUps.mockReturnValue(new Promise(() => {}));
    setup({ chatId: 'c1', messages: [m('1', 'assistant', 'a')] });
    act(() => jest.advanceTimersByTime(DELAY));
    const signal: AbortSignal = mockFetchFollowUps.mock.calls[0][0].signal;
    expect(signal.aborted).toBe(false);
    act(() => {
      window.dispatchEvent(new Event('astrachat-abort-follow-ups'));
    });
    expect(signal.aborted).toBe(true);
  });

  it('смена enabled на false прерывает активный запрос', () => {
    mockFetchFollowUps.mockReturnValue(new Promise(() => {}));
    const messages = [m('1', 'assistant', 'a')];
    const { rerender } = setup({ chatId: 'c1', messages });
    act(() => jest.advanceTimersByTime(DELAY));
    const signal: AbortSignal = mockFetchFollowUps.mock.calls[0][0].signal;
    rerender({ chatId: 'c1', messages, enabled: false });
    expect(signal.aborted).toBe(true);
  });

  it('смена enabled на false отменяет ещё не сработавший таймер', () => {
    const messages = [m('1', 'assistant', 'a')];
    const { rerender } = setup({ chatId: 'c1', messages });
    rerender({ chatId: 'c1', messages, enabled: false });
    act(() => jest.advanceTimersByTime(DELAY));
    expect(mockFetchFollowUps).not.toHaveBeenCalled();
  });

  it('смена чата прерывает запрос прошлого чата', () => {
    mockFetchFollowUps.mockReturnValue(new Promise(() => {}));
    const { rerender } = setup({ chatId: 'c1', messages: [m('1', 'assistant', 'a')] });
    act(() => jest.advanceTimersByTime(DELAY));
    const signal: AbortSignal = mockFetchFollowUps.mock.calls[0][0].signal;
    rerender({ chatId: 'c2', messages: [m('9', 'assistant', 'b')] });
    expect(signal.aborted).toBe(true);
  });

  it('размонтирование отменяет запрос и таймеры', () => {
    mockFetchFollowUps.mockReturnValue(new Promise(() => {}));
    const first = setup({ chatId: 'c1', messages: [m('1', 'assistant', 'a')] });
    act(() => jest.advanceTimersByTime(DELAY));
    const signal: AbortSignal = mockFetchFollowUps.mock.calls[0][0].signal;
    first.unmount();
    expect(signal.aborted).toBe(true);

    mockFetchFollowUps.mockClear();
    const second = setup({ chatId: 'c1', messages: [m('1', 'assistant', 'a')] });
    second.unmount();
    act(() => jest.advanceTimersByTime(DELAY));
    expect(mockFetchFollowUps).not.toHaveBeenCalled();
  });

  it('начало стрима отменяет отложенный таймер (запрос не уходит во время генерации)', () => {
    const idle = [m('1', 'assistant', 'a')];
    const { rerender } = setup({ chatId: 'c1', messages: idle });
    act(() => jest.advanceTimersByTime(DELAY - 100));
    rerender({ chatId: 'c1', messages: [m('1', 'assistant', 'a', { isStreaming: true })] });
    act(() => jest.advanceTimersByTime(DELAY));
    expect(mockFetchFollowUps).not.toHaveBeenCalled();
  });
});

describe('useFollowUpSuggestions: переходы стриминга', () => {
  it('старт стрима сообщения с подсказками обнуляет их и прерывает запрос', () => {
    mockFetchFollowUps.mockReturnValue(new Promise(() => {}));
    const idle = [m('1', 'assistant', 'a')];
    const { rerender } = setup({ chatId: 'c1', messages: idle });
    act(() => jest.advanceTimersByTime(DELAY));
    const signal: AbortSignal = mockFetchFollowUps.mock.calls[0][0].signal;

    rerender({
      chatId: 'c1',
      messages: [m('1', 'assistant', 'a', { isStreaming: true, followUpSuggestions: SUGGESTIONS })],
    });
    expect(signal.aborted).toBe(true);
    expect(patch).toHaveBeenCalledWith('c1', '1', {
      followUpSuggestions: undefined,
      followUpSuggestionsLoading: undefined,
    });
  });

  it('старт стрима без follow-up полей patch не вызывает', () => {
    const { rerender } = setup({ chatId: 'c1', messages: [m('1', 'assistant', 'a')] });
    rerender({ chatId: 'c1', messages: [m('1', 'assistant', 'a', { isStreaming: true })] });
    expect(patch).not.toHaveBeenCalled();
  });

  it('завершение стрима с остатками полей очищает их, без полей — нет', () => {
    const { rerender } = setup({
      chatId: 'c1',
      messages: [m('1', 'assistant', 'a', { isStreaming: true, followUpSuggestionsLoading: true })],
    });
    patch.mockClear();
    rerender({
      chatId: 'c1',
      messages: [m('1', 'assistant', 'a', { followUpSuggestionsLoading: true })],
    });
    expect(patch).toHaveBeenCalledWith('c1', '1', {
      followUpSuggestions: undefined,
      followUpSuggestionsLoading: undefined,
    });

    patch.mockClear();
    const second = setup({
      chatId: 'c1',
      messages: [m('2', 'assistant', 'a', { isStreaming: true })],
    });
    second.rerender({ chatId: 'c1', messages: [m('2', 'assistant', 'a')] });
    expect(patch).not.toHaveBeenCalled();
  });

  it('после завершения стрима подсказки запрашиваются заново', () => {
    const { rerender } = setup({
      chatId: 'c1',
      messages: [m('1', 'assistant', 'a', { isStreaming: true })],
    });
    rerender({ chatId: 'c1', messages: [m('1', 'assistant', 'a final')] });
    act(() => jest.advanceTimersByTime(DELAY));
    expect(mockFetchFollowUps).toHaveBeenCalledTimes(1);
  });

  it('стрим сообщения пользователя не трогает follow-up состояние', () => {
    const { rerender } = setup({ chatId: 'c1', messages: [m('1', 'user', 'q')] });
    rerender({ chatId: 'c1', messages: [m('1', 'user', 'q', { isStreaming: true, followUpSuggestions: SUGGESTIONS })] });
    rerender({ chatId: 'c1', messages: [m('1', 'user', 'q', { followUpSuggestions: SUGGESTIONS })] });
    expect(patch).not.toHaveBeenCalled();
  });
});
