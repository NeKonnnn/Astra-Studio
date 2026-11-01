import { APIConnectionConfigImpl, WebSocketConnectionConfigImpl } from '@/settings/connections';

const API_BASE = { baseUrl: 'https://app.corp', timeout: 30000, retryEnabled: true, retryAttempts: 3, retryDelay: 1000 };
const WS_BASE = { baseUrl: 'wss://app.corp', timeout: 1, pingInterval: 2, pingTimeout: 3, reconnectionAttempts: 4, reconnectionDelay: 5, reconnectionDelayMax: 6 };

beforeEach(() => {
  jest.spyOn(console, 'log').mockImplementation(() => undefined);
  jest.spyOn(console, 'error').mockImplementation(() => undefined);
});

afterEach(() => jest.restoreAllMocks());

describe('APIConnectionConfigImpl', () => {
  it('copies the values and builds urls without double slashes', () => {
    const cfg = new APIConnectionConfigImpl({ ...API_BASE, baseUrl: 'https://app.corp/' });

    expect(cfg.getApiUrl('/api/chat')).toBe('https://app.corp/api/chat');
    expect(cfg.getApiUrl('api/chat')).toBe('https://app.corp/api/chat');
    expect(cfg.getApiUrl('')).toBe('https://app.corp/');
    expect([cfg.timeout, cfg.retryAttempts, cfg.retryDelay]).toEqual([30000, 3, 1000]);
  });

  it('enables retries unless they are switched off explicitly', () => {
    expect(new APIConnectionConfigImpl({ ...API_BASE, retryEnabled: undefined as never }).retryEnabled).toBe(true);
    expect(new APIConnectionConfigImpl({ ...API_BASE, retryEnabled: false }).retryEnabled).toBe(false);
  });

  it.each(['baseUrl', 'timeout', 'retryAttempts', 'retryDelay'] as const)('refuses a configuration without %s', (field) => {
    expect(() => new APIConnectionConfigImpl({ ...API_BASE, [field]: undefined as never })).toThrow(`${field} не задан`);
  });

  it('treats null like a missing number and accepts zero', () => {
    expect(() => new APIConnectionConfigImpl({ ...API_BASE, timeout: null as never })).toThrow('timeout не задан');
    expect(new APIConnectionConfigImpl({ ...API_BASE, retryAttempts: 0, retryDelay: 0 }).retryAttempts).toBe(0);
  });

  it('rejects an empty base url', () => {
    expect(() => new APIConnectionConfigImpl({ ...API_BASE, baseUrl: '' })).toThrow('baseUrl не задан');
  });
});

describe('WebSocketConnectionConfigImpl', () => {
  it('converts http(s) into ws(s) and keeps ws urls', () => {
    expect(new WebSocketConnectionConfigImpl({ ...WS_BASE, baseUrl: 'http://x.test' }).baseUrl).toBe('ws://x.test');
    expect(new WebSocketConnectionConfigImpl({ ...WS_BASE, baseUrl: 'https://x.test' }).baseUrl).toBe('wss://x.test');
    expect(new WebSocketConnectionConfigImpl({ ...WS_BASE, baseUrl: 'ws://x.test' }).baseUrl).toBe('ws://x.test');
    expect(new WebSocketConnectionConfigImpl(WS_BASE).baseUrl).toBe('wss://app.corp');
  });

  it('builds endpoint urls', () => {
    const cfg = new WebSocketConnectionConfigImpl({ ...WS_BASE, baseUrl: 'https://x.test/' });

    expect(cfg.getWsUrl('/ws/chat')).toBe('wss://x.test/ws/chat');
    expect(cfg.getWsUrl('ws/voice')).toBe('wss://x.test/ws/voice');
  });

  it('keeps the timing values', () => {
    const cfg = new WebSocketConnectionConfigImpl(WS_BASE);

    expect([cfg.timeout, cfg.pingInterval, cfg.pingTimeout, cfg.reconnectionAttempts, cfg.reconnectionDelay, cfg.reconnectionDelayMax]).toEqual([1, 2, 3, 4, 5, 6]);
  });

  it.each(['baseUrl', 'timeout', 'pingInterval', 'pingTimeout', 'reconnectionAttempts', 'reconnectionDelay', 'reconnectionDelayMax'] as const)(
    'refuses a configuration without %s',
    (field) => {
      expect(() => new WebSocketConnectionConfigImpl({ ...WS_BASE, [field]: undefined as never })).toThrow(`${field} не задан`);
    },
  );

  it('replaces only the first http:// occurrence of the url (known issue)', () => {
    // .replace('http://', 'ws://') без глобального флага: «http://a/?redirect=http://b» теряет протокол только у первого.
    expect(new WebSocketConnectionConfigImpl({ ...WS_BASE, baseUrl: 'http://a/?u=http://b' }).baseUrl).toBe('ws://a/?u=http://b');
  });
});

describe('settings loader', () => {
  const YAML = `
app:
  name: Test App
  llm_status_poll_seconds: 5
urls:
  frontend_port: http://front.test
  backend_port: http://back.test
  ingress_port: https://ingress.test
  llm_service_port: http://llm.test
api:
  timeout: 12345
  retryEnabled: false
websocket:
  pingInterval: 777
`;

  const ENV_KEYS = ['REACT_APP_API_URL', 'REACT_APP_WS_URL', 'REACT_APP_API_TIMEOUT', 'REACT_APP_WS_TIMEOUT', 'REACT_APP_FRONTEND_PORT', 'REACT_APP_API_RETRY_ENABLED'];

  function loadModule(response: Partial<Response> | Error) {
    const fetchMock = jest.fn();
    if (response instanceof Error) fetchMock.mockRejectedValue(response);
    else fetchMock.mockResolvedValue(response);
    (global as unknown as { fetch: jest.Mock }).fetch = fetchMock;
    let mod!: typeof import('./config');
    jest.isolateModules(() => {
      // eslint-disable-next-line @typescript-eslint/no-var-requires
      mod = require('./config');
    });
    return { mod, fetchMock };
  }

  const yamlResponse = (text: string): Partial<Response> => ({ ok: true, text: async () => text });

  beforeEach(() => {
    ENV_KEYS.forEach((k) => delete process.env[k]);
  });

  afterAll(() => {
    ENV_KEYS.forEach((k) => delete process.env[k]);
  });

  it('refuses to hand out settings before initialisation', () => {
    const { mod } = loadModule(yamlResponse(YAML));

    expect(() => mod.getSettings()).toThrow('Конфигурация не загружена');
    expect(() => mod.getUrl('backend_port')).toThrow('Конфигурация не загружена');
  });

  it('loads config.yml and builds the settings with defaults for the rest', async () => {
    const { mod, fetchMock } = loadModule(yamlResponse(YAML));

    const settings = await mod.initSettings();

    expect(fetchMock).toHaveBeenCalledWith('/config/config.yml');
    expect(settings.app).toMatchObject({ name: 'Test App', version: '1.0.0', debug: false, llmStatusPollSeconds: 5, llmStatusPollTrace: false, ragReindexStatusPollSeconds: 10 });
    expect(settings.urls.backend_port).toBe('http://back.test');
    expect(settings.api.baseUrl).toBe('https://ingress.test');
    expect(settings.api.getApiUrl('/api/x')).toBe('https://ingress.test/api/x');
    expect(settings.api).toMatchObject({ timeout: 12345, retryEnabled: false, retryAttempts: 3, retryDelay: 1000 });
    expect(settings.websocket.baseUrl).toBe('wss://ingress.test');
    expect(settings.websocket).toMatchObject({ pingInterval: 777, pingTimeout: 10000, reconnectionAttempts: 10 });
    expect(mod.getSettings()).toBe(settings);
    expect(mod.getUrl('llm_service_port')).toBe('http://llm.test');
  });

  it('loads the file only once for concurrent callers', async () => {
    const { mod, fetchMock } = loadModule(yamlResponse(YAML));

    const [a, b] = await Promise.all([mod.initSettings(), mod.initConfig()]);
    await mod.initSettings();

    expect(a).toBe(b);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('lets environment variables override the urls', async () => {
    process.env.REACT_APP_API_URL = 'http://api-override.test';
    process.env.REACT_APP_WS_URL = 'https://ws-override.test';
    const { mod } = loadModule(yamlResponse(YAML));

    const settings = await mod.initSettings();

    expect(settings.api.baseUrl).toBe('http://api-override.test');
    expect(settings.websocket.baseUrl).toBe('wss://ws-override.test');
  });

  it('takes missing yaml values from the environment, converting by the default type', async () => {
    process.env.REACT_APP_WS_TIMEOUT = '2500';
    const { mod } = loadModule(yamlResponse(YAML));

    expect((await mod.initSettings()).websocket.timeout).toBe(2500);
  });

  it('prefers yaml over the environment for the same value', async () => {
    process.env.REACT_APP_API_TIMEOUT = '1';
    const { mod } = loadModule(yamlResponse(YAML));

    expect((await mod.initSettings()).api.timeout).toBe(12345);
  });

  it('can reload the settings', async () => {
    const { mod, fetchMock } = loadModule(yamlResponse(YAML));
    const first = await mod.initSettings();

    const second = await mod.resetSettings();

    expect(second).not.toBe(first);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('parses the trace flag case-insensitively', async () => {
    const { mod } = loadModule(yamlResponse(YAML.replace('llm_status_poll_seconds: 5', 'llm_status_poll_seconds: 5\n  llm_status_poll_trace: "TRUE"')));

    expect((await mod.initSettings()).app.llmStatusPollTrace).toBe(true);
  });

  it.each([
    ['a missing urls section', 'app:\n  llm_status_poll_seconds: 5\n', 'отсутствует секция urls'],
    ['a missing poll interval', 'urls:\n  frontend_port: a\n  backend_port: b\n  ingress_port: c\n  llm_service_port: d\n', 'llm_status_poll_seconds'],
    ['a non-positive poll interval', 'app:\n  llm_status_poll_seconds: 0\nurls:\n  frontend_port: a\n  backend_port: b\n  ingress_port: c\n  llm_service_port: d\n', 'положительным'],
    ['a missing url', 'app:\n  llm_status_poll_seconds: 5\nurls:\n  frontend_port: a\n  ingress_port: c\n  llm_service_port: d\n', 'backend_port не задан'],
    ['an empty file', '', 'отсутствует секция urls'],
  ])('fails with %s', async (_label, text, message) => {
    const { mod } = loadModule(yamlResponse(text));

    await expect(mod.initSettings()).rejects.toThrow(message);
    expect(() => mod.getSettings()).toThrow('Конфигурация не загружена');
  });

  it('fails when the file cannot be fetched and allows a retry', async () => {
    const { mod, fetchMock } = loadModule({ ok: false, statusText: 'Not Found' });

    await expect(mod.initSettings()).rejects.toThrow('Не удалось загрузить config.yml: Not Found');

    fetchMock.mockResolvedValue(yamlResponse(YAML));
    await expect(mod.initSettings()).resolves.toBeDefined();
  });

  it('propagates network errors', async () => {
    const { mod } = loadModule(new Error('offline'));

    await expect(mod.initSettings()).rejects.toThrow('offline');
  });

  it('rejects a null yaml value for a numeric setting (known issue)', async () => {
    // `timeout:` без значения в YAML даёт null, а не undefined: getConfigValue не берёт значение по умолчанию,
    // и приложение не стартует вместо того, чтобы использовать 30000.
    const { mod } = loadModule(yamlResponse(YAML.replace('timeout: 12345', 'timeout:')));

    await expect(mod.initSettings()).rejects.toThrow('timeout не задан');
  });

  it('reads the url helper for a missing key as an error', async () => {
    const { mod } = loadModule(yamlResponse(YAML));
    await mod.initSettings();

    expect(() => mod.getUrl('nope' as never)).toThrow("Ключ 'nope' не найден");
  });
});
