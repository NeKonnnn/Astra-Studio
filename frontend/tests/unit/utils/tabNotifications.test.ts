type Mod = typeof import('./tabNotifications');

function load(): Mod {
  let mod!: Mod;
  jest.isolateModules(() => {
    mod = require('./tabNotifications');
  });
  return mod;
}

const KEY = 'tab_notifications_enabled';
let hidden = true;
let focused = false;
let favicon: HTMLLinkElement;
let ctx: Record<string, jest.Mock | string | number>;

beforeEach(() => {
  jest.useFakeTimers();
  localStorage.clear();
  document.head.innerHTML = '<link rel="icon" href="http://localhost/original.ico">';
  document.title = 'AstraChat';
  favicon = document.querySelector('link[rel="icon"]') as HTMLLinkElement;
  hidden = true;
  focused = false;
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden });
  jest.spyOn(document, 'hasFocus').mockImplementation(() => focused);

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
  jest
    .spyOn(HTMLCanvasElement.prototype, 'toBlob')
    .mockImplementation(((cb: BlobCallback) => cb(new Blob(['png'], { type: 'image/png' }))) as any);
  (URL as any).createObjectURL = jest.fn(() => 'blob:badge');
  (URL as any).revokeObjectURL = jest.fn();

  // Image мгновенно «загружается»
  (global as any).Image = class {
    crossOrigin = '';
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;
    set src(_v: string) {
      Promise.resolve().then(() => this.onload?.());
    }
  };
});

afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
});

async function flushPromises() {
  for (let i = 0; i < 8; i += 1) await Promise.resolve();
}

describe('enabled flag', () => {
  it('is off by default and persists "true"/"false"', () => {
    const m = load();
    expect(m.areTabNotificationsEnabled()).toBe(false);
    m.setTabNotificationsEnabled(true);
    expect(localStorage.getItem(KEY)).toBe('true');
    expect(m.areTabNotificationsEnabled()).toBe(true);
    m.setTabNotificationsEnabled(false);
    expect(localStorage.getItem(KEY)).toBe('false');
  });

  it('turning it off clears an active indicator', () => {
    const m = load();
    m.setTabNotificationsEnabled(true);
    m.incrementTabNotification();
    expect(document.title).toBe('(1) AstraChat');
    m.setTabNotificationsEnabled(false);
    expect(document.title).toBe('AstraChat');
    jest.advanceTimersByTime(5000);
    expect(document.title).toBe('AstraChat');
  });
});

describe('incrementTabNotification', () => {
  it('does nothing while notifications are disabled', () => {
    const m = load();
    m.incrementTabNotification();
    expect(document.title).toBe('AstraChat');
  });

  it('does nothing while the tab is visible and focused', () => {
    const m = load();
    m.setTabNotificationsEnabled(true);
    hidden = false;
    focused = true;
    m.incrementTabNotification();
    expect(document.title).toBe('AstraChat');
  });

  it('counts in the title when the tab is hidden', () => {
    const m = load();
    m.setTabNotificationsEnabled(true);
    m.incrementTabNotification();
    m.incrementTabNotification();
    expect(document.title).toBe('(2) AstraChat');
  });

  it('a visible but unfocused tab also counts', () => {
    const m = load();
    m.setTabNotificationsEnabled(true);
    hidden = false;
    focused = false;
    m.incrementTabNotification();
    expect(document.title).toBe('(1) AstraChat');
  });

  it('blinks the title every second and does not start a second timer', () => {
    const m = load();
    m.setTabNotificationsEnabled(true);
    m.incrementTabNotification();
    m.incrementTabNotification();
    expect(jest.getTimerCount()).toBe(1);
    jest.advanceTimersByTime(1000);
    expect(document.title).toBe('AstraChat');
    jest.advanceTimersByTime(1000);
    expect(document.title).toBe('(2) AstraChat');
  });

  it('a new notification makes the title visible again', () => {
    const m = load();
    m.setTabNotificationsEnabled(true);
    m.incrementTabNotification();
    jest.advanceTimersByTime(1000);
    expect(document.title).toBe('AstraChat');
    m.incrementTabNotification();
    expect(document.title).toBe('(2) AstraChat');
  });
});

describe('clearTabNotifications', () => {
  it('stops blinking and restores the title and favicon', async () => {
    const m = load();
    m.setTabNotificationsEnabled(true);
    m.incrementTabNotification();
    await flushPromises();
    expect(favicon.href).toBe('blob:badge');

    m.clearTabNotifications();

    expect(document.title).toBe('AstraChat');
    expect(favicon.href).toBe('http://localhost/original.ico');
    expect(jest.getTimerCount()).toBe(0);
    expect((URL as any).revokeObjectURL).toHaveBeenCalledWith('blob:badge');
  });

  it('is harmless when nothing is pending', () => {
    const m = load();
    expect(() => m.clearTabNotifications()).not.toThrow();
    expect(document.title).toBe('AstraChat');
  });

  it('a counter after clear starts from one', () => {
    const m = load();
    m.setTabNotificationsEnabled(true);
    m.incrementTabNotification();
    m.incrementTabNotification();
    m.clearTabNotifications();
    m.incrementTabNotification();
    expect(document.title).toBe('(1) AstraChat');
  });
});

describe('initTabNotifications', () => {
  it('remembers the current title as the base title', () => {
    const m = load();
    document.title = '  Мой чат  ';
    m.initTabNotifications();
    m.setTabNotificationsEnabled(true);
    m.incrementTabNotification();
    expect(document.title).toBe('(1) Мой чат');
    m.clearTabNotifications();
    expect(document.title).toBe('Мой чат');
  });

  it('ignores a title that already carries a counter', () => {
    const m = load();
    document.title = '(3) Чужой заголовок';
    m.initTabNotifications();
    m.clearTabNotifications();
    expect(document.title).toBe('AstraChat');
  });

  it('keeps the default for an empty title', () => {
    const m = load();
    document.title = '   ';
    m.initTabNotifications();
    m.clearTabNotifications();
    expect(document.title).toBe('AstraChat');
  });
});

describe('favicon badge', () => {
  it('draws the original icon and a red badge with the count', async () => {
    const m = load();
    m.setTabNotificationsEnabled(true);
    m.incrementTabNotification();
    await flushPromises();
    expect(ctx.drawImage).toHaveBeenCalled();
    expect(ctx.fillText).toHaveBeenCalledWith('1', expect.any(Number), expect.any(Number));
    expect(favicon.href).toBe('blob:badge');
  });

  it('caps the label at 99+', async () => {
    const m = load();
    m.setTabNotificationsEnabled(true);
    for (let i = 0; i < 120; i += 1) m.incrementTabNotification();
    await flushPromises();
    expect(ctx.fillText).toHaveBeenLastCalledWith('99+', expect.any(Number), expect.any(Number));
  });

  it('dims the icon on the blink-off phase', async () => {
    const m = load();
    m.setTabNotificationsEnabled(true);
    m.incrementTabNotification();
    await flushPromises();
    jest.advanceTimersByTime(1000);
    await flushPromises();
    expect(ctx.globalAlpha).toBe(1); // после отрисовки альфа возвращается
    expect(ctx.drawImage).toHaveBeenCalledTimes(2);
  });

  it('falls back to a filled square when the icon cannot be drawn', async () => {
    (ctx.drawImage as jest.Mock).mockImplementation(() => {
      throw new Error('broken image');
    });
    const m = load();
    m.setTabNotificationsEnabled(true);
    m.incrementTabNotification();
    await flushPromises();
    expect(ctx.fillRect).toHaveBeenCalled();
  });

  it('keeps working without a favicon link or canvas context', async () => {
    document.head.innerHTML = '';
    const m = load();
    m.setTabNotificationsEnabled(true);
    expect(() => m.incrementTabNotification()).not.toThrow();
    await flushPromises();
    expect(document.title).toBe('(1) AstraChat');

    document.head.innerHTML = '<link rel="icon" href="http://localhost/original.ico">';
    jest.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation((() => null) as any);
    const m2 = load();
    m2.setTabNotificationsEnabled(true);
    expect(() => m2.incrementTabNotification()).not.toThrow();
  });

  it('uses the "shortcut icon" link when there is no plain icon', async () => {
    document.head.innerHTML = '<link rel="shortcut icon" href="http://localhost/s.ico">';
    const m = load();
    m.setTabNotificationsEnabled(true);
    m.incrementTabNotification();
    await flushPromises();
    expect((document.querySelector('link[rel="shortcut icon"]') as HTMLLinkElement).href).toBe('blob:badge');
  });

  it('revokes the previous blob when a new one is created', async () => {
    const m = load();
    m.setTabNotificationsEnabled(true);
    m.incrementTabNotification();
    await flushPromises();
    m.incrementTabNotification();
    await flushPromises();
    expect((URL as any).revokeObjectURL).toHaveBeenCalledWith('blob:badge');
  });
});
