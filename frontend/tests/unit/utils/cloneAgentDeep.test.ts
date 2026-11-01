import { cloneAgentDeep, extractLinkedAgentIdsFromConfig } from '@/utils/cloneAgentDeep';

jest.mock('@/config/api', () => ({
  authFetch: (...a: unknown[]) => (require('@tests/unit/helpers/authFetchMock').authFetchMock as (...x: unknown[]) => unknown)(...a),
  getApiUrl: (path: string) => `http://api.test${path}`,
}));

function mockFetch(response: Partial<Response> & { json?: () => Promise<unknown> }) {
  const fn = jest.fn().mockResolvedValue(response);
  (global as unknown as { fetch: unknown }).fetch = fn;
  return fn;
}

describe('cloneAgentDeep', () => {
  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('requires a token', async () => {
    await expect(cloneAgentDeep(1, null)).rejects.toThrow('нужно войти');
  });

  it.each([0, -1, NaN, Infinity])('rejects an invalid id %p', async (id) => {
    await expect(cloneAgentDeep(id, 't')).rejects.toThrow('Некорректный id агента');
  });

  it('posts to the duplicate endpoint with the bearer token', async () => {
    const fetchMock = mockFetch({
      ok: true,
      json: async () => ({ agent_id: 7, cloned_agent_ids: [8, 9], cloned_agents: 3, message: 'ok' }),
    });

    const result = await cloneAgentDeep(5, 'tok');

    expect(fetchMock).toHaveBeenCalledWith('http://api.test/api/agents/5/duplicate', {
      method: 'POST',
      headers: { Authorization: 'Bearer tok' },
    });
    expect(result).toEqual({ agentId: 7, clonedAgents: 3, clonedAgentIds: [7, 8, 9], message: 'ok' });
  });

  it('does not duplicate the new id in the list', async () => {
    mockFetch({ ok: true, json: async () => ({ agent_id: 7, cloned_agent_ids: [7, 8] }) });

    const result = await cloneAgentDeep(5, 't');

    expect(result.clonedAgentIds).toEqual([7, 8]);
    expect(result.clonedAgents).toBe(2);
  });

  it('filters garbage ids and defaults the counters', async () => {
    mockFetch({ ok: true, json: async () => ({ agent_id: '4', cloned_agent_ids: ['x', 0, -1, '6'] }) });

    const result = await cloneAgentDeep(5, 't');

    expect(result.clonedAgentIds).toEqual([4, 6]);
    expect(result.clonedAgents).toBe(2);
    expect(result.message).toBeUndefined();
  });

  it('fails when the server returns no id', async () => {
    mockFetch({ ok: true, json: async () => ({}) });

    await expect(cloneAgentDeep(5, 't')).rejects.toThrow('не вернул id');
  });

  it('shows a string error detail', async () => {
    mockFetch({ ok: false, statusText: 'Bad', json: async () => ({ detail: 'Нет прав' }) });

    await expect(cloneAgentDeep(5, 't')).rejects.toThrow('Нет прав');
  });

  it('joins validation errors', async () => {
    mockFetch({
      ok: false,
      statusText: 'Unprocessable',
      json: async () => ({ detail: [{ msg: 'поле а' }, { msg: 'поле б' }] }),
    });

    await expect(cloneAgentDeep(5, 't')).rejects.toThrow('поле а; поле б');
  });

  it('reads a message from an object detail', async () => {
    mockFetch({ ok: false, statusText: 'x', json: async () => ({ detail: { message: 'Лимит' } }) });

    await expect(cloneAgentDeep(5, 't')).rejects.toThrow('Лимит');
  });

  it('falls back to the status text when the body is not JSON', async () => {
    mockFetch({
      ok: false,
      statusText: 'Server Error',
      json: async () => {
        throw new Error('not json');
      },
    });

    await expect(cloneAgentDeep(5, 't')).rejects.toThrow('Server Error');
  });

  it('falls back to a default message without a status text', async () => {
    mockFetch({ ok: false, statusText: '', json: async () => ({}) });

    await expect(cloneAgentDeep(5, 't')).rejects.toThrow('Не удалось клонировать');
  });
});

describe('extractLinkedAgentIdsFromConfig', () => {
  it.each([null, undefined, 'x', 5])('returns nothing for %p', (config) => {
    expect(extractLinkedAgentIdsFromConfig(config)).toEqual([]);
  });

  it('collects chain and subagent ids without duplicates', () => {
    const config = { agent_ids: [1, '2', 2], subagents: { agent_ids: [2, 3] } };

    expect(extractLinkedAgentIdsFromConfig(config)).toEqual([1, 2, 3]);
  });

  it('skips invalid ids and non-array values', () => {
    expect(
      extractLinkedAgentIdsFromConfig({ agent_ids: [0, -1, 'x', 4], subagents: { agent_ids: 'nope' } }),
    ).toEqual([4]);
    expect(extractLinkedAgentIdsFromConfig({ agent_ids: 'nope', subagents: null })).toEqual([]);
  });
});
