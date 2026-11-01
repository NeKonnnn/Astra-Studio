type Mod = typeof import('./tabNotifications');

function load(): Mod {
  let mod!: Mod;
  jest.isolateModules(() => {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    mod = require('./tabNotifications');
  });
  return mod;
}

let favicon: HTMLLinkElement;
let ctx: Record<string, jest.Mock>;
let toBlobImpl: (cb: BlobCallback) => void;

beforeEach(() => {
  jest.useFakeTimers();
  localStorage.clear();
  document.head.innerHTML = '<link rel="icon" href="http://localhost/original.ico">';
  document.title = 'AstraChat';
  favicon = document.querySelector('link[rel="icon"]') as HTMLLinkElement;
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
  jest.spyOn(document, 'hasFocus').mockImplementation(() => false);
  ctx = {
    drawImage: jest.fn(),
    fillRect: jest.fn(),
    beginPath: jest.fn(),
    arc: jest.fn(),
    fill: jest.fn(),
    stroke: jest.fn(),
    fillText: jest.fn(),
  };
  jest.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation((() => ctx) as any);
  toBlobImpl = (cb) => cb(new Blob(['png'], { type: 'image/png' }));
  jest.spyOn(HTMLCanvasElement.prototype, 'toBlob').mockImplementation(((cb: BlobCallback) => toBlobImpl(cb)) as any);
  (URL as any).createObjectURL = jest.fn(() => 'blob:badge');
  (URL as any).revokeObjectURL = jest.fn();
});

afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
});

async function flush() {
  for (let i = 0; i < 10; i += 1) await Promise.resolve();
}

describe('tabNotifications: недостающие ветки', () => {
  it('ошибка загрузки иконки не блокирует рисование бейджа', async () => {
    (global as any).Image = class {
      crossOrigin = '';
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;
      set src(_v: string) {
        Promise.resolve().then(() => this.onerror?.());
      }
    };
    const m = load();
    m.setTabNotificationsEnabled(true);
    m.incrementTabNotification();
    await flush();
    expect(ctx.fillText).toHaveBeenCalledWith('1', expect.any(Number), expect.any(Number));
    expect(favicon.href).toBe('blob:badge');
  });

  it('если canvas не вернул blob, иконка остаётся прежней', async () => {
    (global as any).Image = class {
      crossOrigin = '';
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;
      set src(_v: string) {
        Promise.resolve().then(() => this.onload?.());
      }
    };
    toBlobImpl = (cb) => cb(null);
    const m = load();
    m.setTabNotificationsEnabled(true);
    m.incrementTabNotification();
    await flush();
    expect(URL.createObjectURL).not.toHaveBeenCalled();
    expect(favicon.href).toBe('http://localhost/original.ico');
    expect(document.title).toBe('(1) AstraChat');
  });
});
