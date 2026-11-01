type Mod = typeof import('./debugReact185');

function load(): Mod {
  let m!: Mod;
  jest.isolateModules(() => {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    m = require('./debugReact185');
  });
  return m;
}

describe('debugReact185', () => {
  let log: jest.SpyInstance;
  let warn: jest.SpyInstance;
  let error: jest.SpyInstance;
  let info: jest.SpyInstance;

  beforeEach(() => {
    jest.useFakeTimers();
    jest.setSystemTime(new Date('2025-01-01T00:00:00.000Z'));
    localStorage.clear();
    log = jest.spyOn(console, 'log').mockImplementation(() => {});
    warn = jest.spyOn(console, 'warn').mockImplementation(() => {});
    error = jest.spyOn(console, 'error').mockImplementation(() => {});
    info = jest.spyOn(console, 'info').mockImplementation(() => {});
    delete (window as any).__ASTRA_185_INSTALLED__;
    delete (window as any).__ASTRA_185_DUMP__;
    delete (window as any).__ASTRA_185_LAST_DUMP__;
  });

  afterEach(() => {
    jest.useRealTimers();
    jest.restoreAllMocks();
  });

  it('astra185Mark логирует только важные префиксы', () => {
    const m = load();
    m.astra185Mark('effect:foo', 'd');
    m.astra185Mark('render-loop', undefined);
    m.astra185Mark('error:x');
    m.astra185Mark('chat:a');
    m.astra185Mark('stream:a');
    m.astra185Mark('patch:a');
    m.astra185Mark('grok:a');
    expect(log).toHaveBeenCalledTimes(7);
    expect(log.mock.calls[0].slice(0, 3)).toEqual(['[ASTRA#185]', 'effect:foo', 'd']);
    expect(log.mock.calls[1][2]).toBe('');
    m.astra185Mark('other:noise');
    expect(log).toHaveBeenCalledTimes(7);
  });

  it('при astra_debug_185=0 или false ничего не пишет', () => {
    const m = load();
    localStorage.setItem('astra_debug_185', '0');
    m.astra185Mark('effect:foo');
    m.astra185MarkRender({ a: 1 });
    expect(log).not.toHaveBeenCalled();
    localStorage.setItem('astra_debug_185', 'false');
    m.astra185Mark('effect:foo');
    expect(log).not.toHaveBeenCalled();
    m.astra185Dump();
    expect((window as any).__ASTRA_185_LAST_DUMP__.breadcrumbs).toEqual([]);
  });

  it('если localStorage бросает, диагностика остаётся включённой', () => {
    const m = load();
    jest.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('denied');
    });
    m.astra185Mark('chat:x');
    expect(log).toHaveBeenCalledTimes(1);
  });

  it('буфер хлебных крошек ограничен 120 записями', () => {
    const m = load();
    for (let i = 0; i < 130; i += 1) m.astra185Mark(`k${i}`);
    m.astra185Dump();
    const d = (window as any).__ASTRA_185_LAST_DUMP__;
    expect(d.breadcrumbs).toHaveLength(120);
    expect(d.breadcrumbs[0]).toContain('| k10');
    expect(d.breadcrumbs[119]).toContain('| k129');
  });

  it('astra185MarkRender: предупреждения на 10 и 25 рендере, дамп на 40 один раз', () => {
    const m = load();
    for (let i = 0; i < 40; i += 1) m.astra185MarkRender({ i });
    expect(warn).toHaveBeenCalledTimes(2);
    expect(warn.mock.calls[0][1]).toBe('renders-in-window=10');
    expect(warn.mock.calls[1][1]).toBe('renders-in-window=25');
    const loopMsgs = error.mock.calls.filter((c) => String(c[1]).includes('ПОДОЗРЕНИЕ'));
    expect(loopMsgs).toHaveLength(1);
    expect((window as any).__ASTRA_185_LAST_DUMP__.reason).toBe('auto-render-loop');
    error.mockClear();
    for (let i = 0; i < 5; i += 1) m.astra185MarkRender({ i });
    expect(error).not.toHaveBeenCalled();
  });

  it('окно рендеров сбрасывается через 1500 мс', () => {
    const m = load();
    for (let i = 0; i < 9; i += 1) m.astra185MarkRender({});
    jest.advanceTimersByTime(1600);
    for (let i = 0; i < 9; i += 1) m.astra185MarkRender({});
    expect(warn).not.toHaveBeenCalled();
    m.astra185MarkRender({});
    expect(warn).toHaveBeenCalledTimes(1);
  });

  it('astra185Dump печатает payload и последние 30 строк', () => {
    const m = load();
    for (let i = 0; i < 40; i += 1) m.astra185Mark(i % 2 ? 'a' : 'b', i === 3 ? 'det' : undefined);
    m.astra185Dump('why');
    const last = (window as any).__ASTRA_185_LAST_DUMP__;
    expect(last.reason).toBe('why');
    expect(last.breadcrumbs.some((l: string) => l.endsWith('| a | det'))).toBe(true);
    expect(error).toHaveBeenCalledTimes(32);
  });

  it('astra185Dump переживает запрет записи в window', () => {
    const m = load();
    Object.defineProperty(window, '__ASTRA_185_LAST_DUMP__', {
      configurable: true,
      set() {
        throw new Error('ro');
      },
      get() {
        return undefined;
      },
    });
    expect(() => m.astra185Dump()).not.toThrow();
    delete (window as any).__ASTRA_185_LAST_DUMP__;
  });

  it('installAstra185GlobalHandlers: идемпотентна и ставит глобальный dump', () => {
    const m = load();
    const add = jest.spyOn(window, 'addEventListener');
    m.installAstra185GlobalHandlers();
    m.installAstra185GlobalHandlers();
    expect(add.mock.calls.filter((c) => c[0] === 'error')).toHaveLength(1);
    expect(info).toHaveBeenCalledTimes(1);
    (window as any).__ASTRA_185_DUMP__();
    expect((window as any).__ASTRA_185_LAST_DUMP__.reason).toBe('manual');
  });

  it('обработчик error реагирует только на #185 / maximum update depth', () => {
    const m = load();
    m.installAstra185GlobalHandlers();
    window.dispatchEvent(new ErrorEvent('error', { message: 'boom' }));
    expect((window as any).__ASTRA_185_LAST_DUMP__).toBeUndefined();
    window.dispatchEvent(new ErrorEvent('error', { message: 'Minified React error #185' }));
    expect((window as any).__ASTRA_185_LAST_DUMP__.reason).toBe('caught-error-185');
    delete (window as any).__ASTRA_185_LAST_DUMP__;
    const err = new Error('Maximum update depth exceeded');
    window.dispatchEvent(new ErrorEvent('error', { error: err, message: 'x' }));
    expect((window as any).__ASTRA_185_LAST_DUMP__.reason).toBe('caught-error-185');
  });

  it('обработчик unhandledrejection реагирует только на #185', () => {
    const m = load();
    m.installAstra185GlobalHandlers();
    const mk = (reason: unknown) => {
      const ev: any = new Event('unhandledrejection');
      ev.reason = reason;
      window.dispatchEvent(ev);
    };
    mk(new Error('network'));
    expect((window as any).__ASTRA_185_LAST_DUMP__).toBeUndefined();
    mk({ message: 'error #185 here' });
    expect((window as any).__ASTRA_185_LAST_DUMP__.reason).toBe('caught-unhandled-185');
    delete (window as any).__ASTRA_185_LAST_DUMP__;
    mk('Maximum Update Depth');
    expect((window as any).__ASTRA_185_LAST_DUMP__.reason).toBe('caught-unhandled-185');
  });
});
