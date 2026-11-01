type Mod = typeof import('./config');

function load(): Mod {
  let mod!: Mod;
  jest.isolateModules(() => {
    mod = require('./config');
  });
  return mod;
}

const yamlResponse = (text: string, ok = true, statusText = 'OK') =>
  ({ ok, statusText, text: async () => text }) as unknown as Response;

let fetchMock: jest.Mock;
beforeEach(() => {
  fetchMock = jest.fn();
  (global as any).fetch = fetchMock;
  jest.spyOn(console, 'error').mockImplementation(() => undefined);
});
afterEach(() => jest.restoreAllMocks());

describe('getUrl before initialisation', () => {
  it('fails loudly instead of returning a default', () => {
    const { getUrl } = load();
    expect(() => getUrl('backend')).toThrow(/Конфигурация не загружена/);
    expect(() => getUrl('backend')).toThrow("getUrl('backend')");
  });
});

describe('initConfig', () => {
  it('loads /config/config.yml and serves urls from the cache', async () => {
    fetchMock.mockResolvedValue(yamlResponse('urls:\n  backend: http://b.test\n  ws: ws://w.test\n'));
    const { initConfig, getUrl } = load();

    await initConfig();

    expect(fetchMock).toHaveBeenCalledWith('/config/config.yml');
    expect(getUrl('backend')).toBe('http://b.test');
    expect(getUrl('ws')).toBe('ws://w.test');
  });

  it('a second call does not fetch again', async () => {
    fetchMock.mockResolvedValue(yamlResponse('urls:\n  a: http://a\n'));
    const { initConfig } = load();
    await initConfig();
    await initConfig();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('concurrent calls share one request', async () => {
    fetchMock.mockResolvedValue(yamlResponse('urls:\n  a: http://a\n'));
    const { initConfig } = load();
    await Promise.all([initConfig(), initConfig(), initConfig()]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('an HTTP error aborts startup with a helpful message', async () => {
    fetchMock.mockResolvedValue(yamlResponse('', false, 'Not Found'));
    const { initConfig } = load();
    await expect(initConfig()).rejects.toThrow(/Не удалось загрузить config\.yml: Not Found/);
  });

  it('a config without the urls section is rejected', async () => {
    fetchMock.mockResolvedValue(yamlResponse('other: 1\n'));
    const { initConfig } = load();
    await expect(initConfig()).rejects.toThrow(/отсутствует секция urls/);
  });

  it('an empty file is rejected as missing urls', async () => {
    fetchMock.mockResolvedValue(yamlResponse(''));
    const { initConfig } = load();
    await expect(initConfig()).rejects.toThrow(/отсутствует секция urls/);
  });

  it('invalid YAML is rejected', async () => {
    fetchMock.mockResolvedValue(yamlResponse('urls: [unclosed'));
    const { initConfig } = load();
    await expect(initConfig()).rejects.toBeTruthy();
  });

  it('a network failure is rejected and can be retried', async () => {
    fetchMock
      .mockRejectedValueOnce(new TypeError('offline'))
      .mockResolvedValueOnce(yamlResponse('urls:\n  a: http://a\n'));
    const { initConfig, getUrl } = load();

    await expect(initConfig()).rejects.toThrow('offline');
    await initConfig();

    expect(getUrl('a')).toBe('http://a');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

describe('getUrl after initialisation', () => {
  async function ready(yaml: string) {
    fetchMock.mockResolvedValue(yamlResponse(yaml));
    const mod = load();
    await mod.initConfig();
    return mod;
  }

  it('unknown keys are an error naming the key', async () => {
    const { getUrl } = await ready('urls:\n  a: http://a\n');
    expect(() => getUrl('missing')).toThrow(/Ключ 'missing' не найден/);
  });

  it('an empty value counts as missing', async () => {
    const { getUrl } = await ready('urls:\n  a:\n  b: ""\n');
    expect(() => getUrl('a')).toThrow(/не найден/);
    expect(() => getUrl('b')).toThrow(/не найден/);
  });

  it('non-string values are returned as parsed (known issue: no type validation)', async () => {
    const { getUrl } = await ready('urls:\n  port: 8080\n');
    expect(getUrl('port')).toBe(8080 as unknown as string);
  });
});
