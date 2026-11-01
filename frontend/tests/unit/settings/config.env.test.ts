const BASE_URLS = `
urls:
  frontend_port: http://front.test
  backend_port: http://back.test
  ingress_port: https://ingress.test
  llm_service_port: http://llm.test
`;
const APP = `
app:
  llm_status_poll_seconds: 5
`;

const ENV_KEYS = [
  'REACT_APP_API_TIMEOUT',
  'REACT_APP_API_RETRY_ENABLED',
  'REACT_APP_WS_RECONNECTION_ATTEMPTS',
  'REACT_APP_LLM_SERVICE_PORT',
  'REACT_APP_INGRESS_PORT',
  'REACT_APP_API_URL',
  'REACT_APP_WS_URL',
];

function load(yamlText: string) {
  (global as unknown as { fetch: jest.Mock }).fetch = jest
    .fn()
    .mockResolvedValue({ ok: true, text: async () => yamlText });
  let mod!: typeof import('./config');
  jest.isolateModules(() => {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    mod = require('./config');
  });
  return mod;
}

beforeEach(() => {
  ENV_KEYS.forEach((k) => delete process.env[k]);
  jest.spyOn(console, 'error').mockImplementation(() => undefined);
});
afterEach(() => {
  ENV_KEYS.forEach((k) => delete process.env[k]);
  jest.restoreAllMocks();
});

describe('settings loader: rag reindex poll interval', () => {
  it('defaults to 10 seconds when the YAML does not define it', async () => {
    const mod = load(APP + BASE_URLS);

    expect((await mod.initSettings()).app.ragReindexStatusPollSeconds).toBe(10);
  });

  it('accepts a configured interval', async () => {
    const mod = load(`app:\n  llm_status_poll_seconds: 5\n  rag_reindex_status_poll_seconds: 3\n${BASE_URLS}`);

    expect((await mod.initSettings()).app.ragReindexStatusPollSeconds).toBe(3);
  });

  it.each(['0', '-4', 'abc'])('rejects the interval %j', async (value) => {
    const mod = load(`app:\n  llm_status_poll_seconds: 5\n  rag_reindex_status_poll_seconds: ${value}\n${BASE_URLS}`);

    await expect(mod.initSettings()).rejects.toThrow(
      'rag_reindex_status_poll_seconds должен быть положительным целым числом',
    );
  });
});

describe('settings loader: environment fallbacks', () => {
  const PARTIAL_URLS = 'urls:\n  frontend_port: http://f\n  backend_port: http://b\n  ingress_port: https://i\n';

  it('takes a missing url from the environment', async () => {
    process.env.REACT_APP_LLM_SERVICE_PORT = 'http://env-llm.test';
    const mod = load(APP + PARTIAL_URLS);

    expect((await mod.initSettings()).urls.llm_service_port).toBe('http://env-llm.test');
  });

  it('the YAML url wins over the environment', async () => {
    process.env.REACT_APP_LLM_SERVICE_PORT = 'http://env-llm.test';
    const mod = load(APP + BASE_URLS);

    expect((await mod.initSettings()).urls.llm_service_port).toBe('http://llm.test');
  });

  it('fails naming both the YAML key and the env variable when a url is nowhere', async () => {
    const mod = load(APP + PARTIAL_URLS);

    await expect(mod.initSettings()).rejects.toThrow(
      'llm_service_port не задан в YAML (urls.llm_service_port) или ENV (REACT_APP_LLM_SERVICE_PORT)',
    );
  });

  it('parses numeric env values as integers when the YAML key is absent', async () => {
    process.env.REACT_APP_API_TIMEOUT = '4500';
    process.env.REACT_APP_WS_RECONNECTION_ATTEMPTS = '7';
    const mod = load(APP + BASE_URLS);
    const settings = await mod.initSettings();

    expect(settings.api.timeout).toBe(4500);
    expect(settings.websocket.reconnectionAttempts).toBe(7);
  });

  it.each([
    ['TRUE', true],
    ['true', true],
    ['false', false],
    ['0', false],
  ])('parses the boolean env value %j as %s', async (raw, expected) => {
    process.env.REACT_APP_API_RETRY_ENABLED = raw;
    const mod = load(APP + BASE_URLS);

    expect((await mod.initSettings()).api.retryEnabled).toBe(expected);
  });

  it('(known issue) a non-numeric numeric env value becomes NaN and the error does not name the env variable', async () => {
    // parseInt('abc') === NaN: правильно — явно сообщить о неверном значении переменной окружения.
    process.env.REACT_APP_API_TIMEOUT = 'abc';
    const mod = load(APP + BASE_URLS);

    const result: any = await mod.initSettings().catch((e) => e);

    if (result instanceof Error) {
      expect(result.message).not.toContain('REACT_APP_API_TIMEOUT');
    } else {
      expect(Number.isNaN(result.api.timeout)).toBe(true);
    }
  });

  it('REACT_APP_API_URL overrides the ingress url for the API base', async () => {
    process.env.REACT_APP_API_URL = 'https://override.test';
    const mod = load(APP + BASE_URLS);

    expect((await mod.initSettings()).api.baseUrl).toBe('https://override.test');
  });

  it('keeps ws:// and wss:// urls as they are and converts https:// to wss://', async () => {
    process.env.REACT_APP_WS_URL = 'ws://direct.test';
    let mod = load(APP + BASE_URLS);
    expect((await mod.initSettings()).websocket.baseUrl).toBe('ws://direct.test');

    process.env.REACT_APP_WS_URL = 'wss://secure.test';
    mod = load(APP + BASE_URLS);
    expect((await mod.initSettings()).websocket.baseUrl).toBe('wss://secure.test');

    delete process.env.REACT_APP_WS_URL;
    mod = load(APP + BASE_URLS);
    expect((await mod.initSettings()).websocket.baseUrl).toBe('wss://ingress.test');
  });
});
