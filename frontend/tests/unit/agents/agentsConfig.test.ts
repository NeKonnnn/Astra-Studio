type Mod = typeof import('./agentsConfig');

jest.mock('@/config/api', () => ({
  authFetch: (...a: unknown[]) => (require('@tests/unit/helpers/authFetchMock').authFetchMock as (...x: unknown[]) => unknown)(...a),
  getApiUrl: (p: string) => `http://api.test${p}`,
  getAuthFetchHeaders: () => ({ Authorization: 'Bearer t' }),
}));

function load(): Mod {
  let m!: Mod;
  jest.isolateModules(() => {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    m = require('./agentsConfig');
  });
  return m;
}

describe('agentsConfig', () => {
  let fetchMock: jest.Mock;
  beforeEach(() => {
    fetchMock = jest.fn();
    (global as any).fetch = fetchMock;
  });

  it('getCachedChainConfig без загрузки отдаёт значения по умолчанию', () => {
    const m = load();
    expect(m.getCachedChainConfig()).toEqual({
      maxAgents: 10,
      maxAgentsCap: 50,
      maxSubagents: 10,
      maxSubagentsCap: 50,
      graphSteps: 50,
      defaultRecursionLimit: 50,
      maxRecursionLimit: 500,
    });
  });

  it('fetchAgentChainConfig: snake_case и camelCase поля, ограничение диапазона, кэш', async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({
        max_agents: 7,
        maxAgentsCap: 999,
        max_subagents: '0',
        max_subagents_cap: 20,
        graph_steps: 120,
        max_recursion_limit: 'abc',
      }),
    });
    const m = load();
    const cfg = await m.fetchAgentChainConfig();
    expect(fetchMock).toHaveBeenCalledWith('http://api.test/api/agents/chain-config', {
      headers: { Authorization: 'Bearer t' },
    });
    expect(cfg).toEqual({
      maxAgents: 7,
      maxAgentsCap: 50, // зажато до 50
      maxSubagents: 1, // 0 -> нижняя граница
      maxSubagentsCap: 20,
      graphSteps: 120,
      defaultRecursionLimit: 120, // берётся из graph_steps
      maxRecursionLimit: 500, // нечисловое -> запасное значение
    });
    expect(m.getCachedChainConfig()).toBe(cfg);
    expect(await m.fetchAgentChainConfig()).toBe(cfg);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('параллельные вызовы делят один запрос', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({}) });
    const m = load();
    const [a, b] = await Promise.all([m.fetchAgentChainConfig(), m.fetchAgentChainConfig()]);
    expect(a).toBe(b);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(a.maxAgents).toBe(10);
  });

  it('HTTP-ошибка даёт значения по умолчанию', async () => {
    fetchMock.mockResolvedValue({ ok: false, json: async () => ({ max_agents: 3 }) });
    const m = load();
    expect((await m.fetchAgentChainConfig()).maxAgents).toBe(10);
  });

  it('сбой сети даёт значения по умолчанию и кэшируется', async () => {
    fetchMock.mockRejectedValue(new Error('offline'));
    const m = load();
    const cfg = await m.fetchAgentChainConfig();
    expect(cfg.maxRecursionLimit).toBe(500);
    expect(m.getCachedChainConfig()).toBe(cfg);
  });

  it('parseAgentIds: фильтрует мусор, дубли, исключённый id и режет по лимиту', () => {
    const m = load();
    expect(m.parseAgentIds('нет')).toEqual([]);
    expect(m.parseAgentIds([1, '2', 2, 0, -3, 'x', NaN, 3], 3)).toEqual([1, 2]);
    expect(m.parseAgentIds([5, 6, 7], undefined, 2)).toEqual([5, 6]);
    expect(m.parseAgentIds([5, 6], null, 0)).toEqual([5]);
    expect(m.MAX_CHAIN_AGENTS).toBe(10);
  });
});
