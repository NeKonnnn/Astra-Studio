jest.mock('@/config/api', () => ({
  authFetch: (...a: unknown[]) => (require('@tests/unit/helpers/authFetchMock').authFetchMock as (...x: unknown[]) => unknown)(...a),
  getApiUrl: (path: string) => `http://api.test${path}`,
  getAuthFetchHeaders: (extra: Record<string, string> = {}) => ({ Authorization: 'Bearer t', ...extra }),
}));

import {
  buildFollowUpHistory,
  fetchFollowUpSuggestions,
  mapFollowUpApiSuggestions,
} from '@/chat/fetchFollowUpSuggestions';

const msg = (role: string, content: string, extra: Record<string, unknown> = {}) =>
  ({ id: `${role}-${content}`, role, content, timestamp: new Date(), ...extra }) as any;

describe('mapFollowUpApiSuggestions', () => {
  it('trims fields, assigns ids and marks the source', () => {
    const out = mapFollowUpApiSuggestions([
      { title: ' Тема ', subtitle: ' Подпись ', content: ' Вопрос? ' },
      { id: 'custom', title: 'B', content: 'C' },
    ]);
    expect(out).toEqual([
      { id: 'follow-up-0', title: 'Тема', subtitle: 'Подпись', content: 'Вопрос?', source: 'general' },
      { id: 'custom', title: 'B', subtitle: undefined, content: 'C', source: 'general' },
    ]);
  });

  it('drops rows without title or content', () => {
    const out = mapFollowUpApiSuggestions([
      { title: '', content: 'x' },
      { title: 'x', content: '   ' },
      { title: '   ', content: 'x' },
      { title: 'ok', content: 'ok' },
    ] as any);
    expect(out.map((s) => s.title)).toEqual(['ok']);
  });

  it('keeps at most three suggestions', () => {
    const items = Array.from({ length: 6 }, (_, i) => ({ title: `t${i}`, content: `c${i}` }));
    expect(mapFollowUpApiSuggestions(items).map((s) => s.title)).toEqual(['t0', 't1', 't2']);
  });

  it('empty input gives an empty list', () => {
    expect(mapFollowUpApiSuggestions([])).toEqual([]);
  });
});

describe('buildFollowUpHistory', () => {
  it('keeps user and assistant messages only', () => {
    const out = buildFollowUpHistory([
      msg('system', 's'),
      msg('user', 'привет'),
      msg('assistant', 'здравствуйте'),
      msg('tool', 't'),
    ]);
    expect(out).toEqual([
      { role: 'user', content: 'привет' },
      { role: 'assistant', content: 'здравствуйте' },
    ]);
  });

  it('skips messages that are still streaming', () => {
    const out = buildFollowUpHistory([msg('user', 'q'), msg('assistant', 'частичный', { isStreaming: true })]);
    expect(out).toEqual([{ role: 'user', content: 'q' }]);
  });

  it('removes the reasoning block from assistant replies', () => {
    const out = buildFollowUpHistory([msg('assistant', '<think>скрытые мысли</think>Видимый ответ')]);
    expect(out).toEqual([{ role: 'assistant', content: 'Видимый ответ' }]);
  });

  it('does not strip think-tags from user messages', () => {
    const out = buildFollowUpHistory([msg('user', 'что значит <think>x</think> в тексте?')]);
    expect(out[0].content).toContain('<think>');
  });

  it('drops empty messages, including replies that were pure reasoning', () => {
    const out = buildFollowUpHistory([
      msg('user', '   '),
      msg('assistant', '<think>только мысли</think>'),
      msg('assistant', ''),
      msg('user', 'ok'),
    ]);
    expect(out).toEqual([{ role: 'user', content: 'ok' }]);
  });

  it('trims content and tolerates missing content', () => {
    const out = buildFollowUpHistory([msg('user', '  текст  '), { role: 'assistant', content: undefined } as any]);
    expect(out).toEqual([{ role: 'user', content: 'текст' }]);
  });

  it('empty history', () => {
    expect(buildFollowUpHistory([])).toEqual([]);
  });
});

describe('fetchFollowUpSuggestions', () => {
  let fetchMock: jest.Mock;
  beforeEach(() => {
    fetchMock = jest.fn();
    (global as any).fetch = fetchMock;
  });

  const ok = (body: unknown) => ({ ok: true, json: async () => body }) as unknown as Response;

  it('does not call the API for an empty conversation', async () => {
    expect(await fetchFollowUpSuggestions({ messages: [] })).toEqual([]);
    expect(await fetchFollowUpSuggestions({ messages: [msg('system', 's')] })).toEqual([]);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('posts the cleaned history with the model and abort signal', async () => {
    fetchMock.mockResolvedValue(ok({ suggestions: [{ title: 'A', content: 'B' }] }));
    const controller = new AbortController();

    const out = await fetchFollowUpSuggestions({
      messages: [msg('user', 'вопрос'), msg('assistant', '<think>x</think>ответ')],
      modelPath: 'llm-svc://m',
      signal: controller.signal,
    });

    expect(out).toEqual([
      { id: 'follow-up-0', title: 'A', subtitle: undefined, content: 'B', source: 'general' },
    ]);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('http://api.test/api/chat/follow-up-suggestions');
    expect(init.method).toBe('POST');
    expect(init.headers).toEqual({ Authorization: 'Bearer t', 'Content-Type': 'application/json' });
    expect(init.signal).toBe(controller.signal);
    expect(JSON.parse(init.body)).toEqual({
      messages: [
        { role: 'user', content: 'вопрос' },
        { role: 'assistant', content: 'ответ' },
      ],
      model_path: 'llm-svc://m',
    });
  });

  it('model_path is null when not given', async () => {
    fetchMock.mockResolvedValue(ok({ suggestions: [] }));
    await fetchFollowUpSuggestions({ messages: [msg('user', 'q')], modelPath: '' });
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).model_path).toBeNull();
  });

  it('HTTP errors give an empty list', async () => {
    fetchMock.mockResolvedValue({ ok: false, json: async () => ({}) });
    expect(await fetchFollowUpSuggestions({ messages: [msg('user', 'q')] })).toEqual([]);
  });

  it('a body without a suggestions array gives an empty list', async () => {
    fetchMock.mockResolvedValue(ok({ suggestions: 'none' }));
    expect(await fetchFollowUpSuggestions({ messages: [msg('user', 'q')] })).toEqual([]);
  });

  it('network errors and aborts reach the caller', async () => {
    fetchMock.mockRejectedValue(Object.assign(new Error('aborted'), { name: 'AbortError' }));
    await expect(fetchFollowUpSuggestions({ messages: [msg('user', 'q')] })).rejects.toThrow('aborted');
  });
});
