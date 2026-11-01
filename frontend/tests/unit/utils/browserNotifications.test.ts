import {
  areNotificationsEnabled,
  getNotificationPermission,
  isNotificationSupported,
  requestNotificationPermission,
  setNotificationsEnabled,
  showBrowserNotification,
} from '@/utils/browserNotifications';

type FakeNotificationInstance = {
  title: string;
  options: any;
  close: jest.Mock;
  onclick?: () => void;
  onshow?: () => void;
  onerror?: (e: unknown) => void;
  onclose?: () => void;
};

let instances: FakeNotificationInstance[];
let permission: 'default' | 'granted' | 'denied';
let requestPermission: jest.Mock;

function installNotification(): void {
  instances = [];
  class FakeNotification {
    static get permission() {
      return permission;
    }
    static requestPermission = requestPermission;
    title: string;
    options: any;
    close = jest.fn();
    constructor(title: string, options: any) {
      this.title = title;
      this.options = options;
      instances.push(this as unknown as FakeNotificationInstance);
    }
  }
  (window as any).Notification = FakeNotification;
}

beforeEach(() => {
  permission = 'default';
  requestPermission = jest.fn(async () => permission);
  installNotification();
  localStorage.clear();
  jest.spyOn(console, 'log').mockImplementation(() => undefined);
  jest.spyOn(console, 'warn').mockImplementation(() => undefined);
  jest.spyOn(console, 'error').mockImplementation(() => undefined);
});

afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
});

describe('support and permission', () => {
  it('reports support by the presence of window.Notification', () => {
    expect(isNotificationSupported()).toBe(true);
    delete (window as any).Notification;
    expect(isNotificationSupported()).toBe(false);
  });

  it.each([
    ['granted', { granted: true, denied: false, default: false }],
    ['denied', { granted: false, denied: true, default: false }],
    ['default', { granted: false, denied: false, default: true }],
  ] as const)('maps %s to flags', (value, expected) => {
    permission = value;
    expect(getNotificationPermission()).toEqual(expected);
  });

  it('without support all flags are false', () => {
    delete (window as any).Notification;
    expect(getNotificationPermission()).toEqual({ granted: false, denied: false, default: false });
  });
});

describe('requestNotificationPermission', () => {
  it('returns false without support', async () => {
    delete (window as any).Notification;
    expect(await requestNotificationPermission()).toBe(false);
  });

  it('does not ask again when already granted', async () => {
    permission = 'granted';
    expect(await requestNotificationPermission()).toBe(true);
    expect(requestPermission).not.toHaveBeenCalled();
  });

  it('does not ask again when denied', async () => {
    permission = 'denied';
    expect(await requestNotificationPermission()).toBe(false);
    expect(requestPermission).not.toHaveBeenCalled();
  });

  it('asks the user and reports the answer', async () => {
    requestPermission.mockResolvedValue('granted');
    expect(await requestNotificationPermission()).toBe(true);
    requestPermission.mockResolvedValue('denied');
    expect(await requestNotificationPermission()).toBe(false);
  });

  it('turns a failing prompt into false', async () => {
    requestPermission.mockRejectedValue(new Error('blocked'));
    expect(await requestNotificationPermission()).toBe(false);
  });
});

describe('showBrowserNotification', () => {
  it('does nothing without support or without permission', () => {
    showBrowserNotification('x');
    expect(instances).toHaveLength(0);
    permission = 'denied';
    showBrowserNotification('x');
    expect(instances).toHaveLength(0);
    delete (window as any).Notification;
    showBrowserNotification('x');
  });

  it('creates a notification with defaults that the caller can override', () => {
    permission = 'granted';
    showBrowserNotification('Ответ готов', { body: 'Текст', tag: 'custom' });
    expect(instances).toHaveLength(1);
    expect(instances[0].title).toBe('Ответ готов');
    expect(instances[0].options).toMatchObject({
      icon: '/astra.ico',
      badge: '/astra.ico',
      requireInteraction: false,
      silent: false,
      body: 'Текст',
      tag: 'custom',
    });
  });

  it('closes itself after five seconds', () => {
    jest.useFakeTimers();
    permission = 'granted';
    showBrowserNotification('x');
    jest.advanceTimersByTime(4999);
    expect(instances[0].close).not.toHaveBeenCalled();
    jest.advanceTimersByTime(2);
    expect(instances[0].close).toHaveBeenCalledTimes(1);
  });

  it('a click focuses the window and closes the notification', () => {
    permission = 'granted';
    const focus = jest.spyOn(window, 'focus').mockImplementation(() => undefined);
    showBrowserNotification('x');
    instances[0].onclick?.();
    expect(focus).toHaveBeenCalled();
    expect(instances[0].close).toHaveBeenCalled();
  });

  it('lifecycle handlers do not throw', () => {
    permission = 'granted';
    showBrowserNotification('x');
    expect(() => {
      instances[0].onshow?.();
      instances[0].onerror?.(new Error('e'));
      instances[0].onclose?.();
    }).not.toThrow();
  });

  it('a constructor failure is swallowed', () => {
    permission = 'granted';
    (window as any).Notification = Object.assign(
      function Broken() {
        throw new Error('Illegal constructor');
      },
      { permission: 'granted' },
    );
    expect(() => showBrowserNotification('x')).not.toThrow();
  });
});

describe('enabled flag', () => {
  it('is off by default and persists as a string', () => {
    expect(areNotificationsEnabled()).toBe(false);
    setNotificationsEnabled(true);
    expect(localStorage.getItem('browser_notifications_enabled')).toBe('true');
    expect(areNotificationsEnabled()).toBe(true);
    setNotificationsEnabled(false);
    expect(areNotificationsEnabled()).toBe(false);
  });

  it('only the exact string "true" counts', () => {
    localStorage.setItem('browser_notifications_enabled', '1');
    expect(areNotificationsEnabled()).toBe(false);
  });
});
