import '@testing-library/jest-dom';
import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import TabNotificationWatcher from '@/components/TabNotificationWatcher';
import TopErrorBanner, { TOP_ERROR_BANNER_AUTO_DISMISS_MS } from '@/components/TopErrorBanner';
import TopStatusBanner from '@/components/TopStatusBanner';
import StatusToastHost from '@/components/StatusToastHost';
import LoginSessionNotice from '@/components/LoginSessionNotice';
import LlmStatusBanner from '@/components/LlmStatusBanner';

const mockInit = jest.fn();
const mockClear = jest.fn();
jest.mock('@/utils/tabNotifications', () => ({
  initTabNotifications: () => mockInit(),
  clearTabNotifications: () => mockClear(),
}));

let mockNotifications: Array<{ id: string; type: string; message: string }> = [];
const mockRemove = jest.fn();
jest.mock('@/contexts/AppContext', () => ({
  useAppContext: () => ({ state: { notifications: mockNotifications } }),
  useAppActions: () => ({ removeNotification: mockRemove }),
}));

const mockConsume = jest.fn();
jest.mock('@/settings/sessionValidity', () => ({
  consumeLoginSessionNotice: () => mockConsume(),
}));

const mockGetSettings = jest.fn();
const mockInitSettings = jest.fn();
jest.mock('@/settings', () => ({
  getSettings: () => mockGetSettings(),
  initSettings: () => mockInitSettings(),
}));
jest.mock('@/config/api', () => ({
  authFetch: (...a: unknown[]) => (require('@tests/unit/helpers/authFetchMock').authFetchMock as (...x: unknown[]) => unknown)(...a), getApiUrl: (p: string) => `http://api.test${p}` }));

beforeEach(() => {
  jest.useFakeTimers();
  mockInit.mockReset();
  mockClear.mockReset();
  mockRemove.mockReset();
  mockConsume.mockReset();
  mockGetSettings.mockReset();
  mockInitSettings.mockReset();
  mockNotifications = [];
});

afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
});

describe('TabNotificationWatcher', () => {
  it('инициализирует и сбрасывает счётчик при возвращении на видимую вкладку', () => {
    const { unmount } = render(<TabNotificationWatcher />);
    expect(mockInit).toHaveBeenCalledTimes(1);
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
    window.dispatchEvent(new Event('focus'));
    expect(mockClear).not.toHaveBeenCalled();
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => false });
    window.dispatchEvent(new Event('focus'));
    document.dispatchEvent(new Event('visibilitychange'));
    expect(mockClear).toHaveBeenCalledTimes(2);
    unmount();
    window.dispatchEvent(new Event('focus'));
    expect(mockClear).toHaveBeenCalledTimes(2);
  });
});

describe('TopErrorBanner', () => {
  it('показывает сообщение, закрывается кнопкой и автоматически через 10 с', () => {
    const onClose = jest.fn();
    render(<TopErrorBanner message="Ошибка!" onClose={onClose} />);
    expect(screen.getByText('Ошибка!')).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('Закрыть уведомление об ошибке'));
    expect(onClose).toHaveBeenCalledTimes(1);
    act(() => {
      jest.advanceTimersByTime(TOP_ERROR_BANNER_AUTO_DISMISS_MS + 10);
    });
    expect(onClose.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it('произвольная подпись кнопки закрытия', () => {
    render(<TopErrorBanner message="m" onClose={jest.fn()} ariaLabel="Закрыть X" />);
    expect(screen.getByLabelText('Закрыть X')).toBeInTheDocument();
  });
});

describe('TopStatusBanner', () => {
  it('не рисуется закрытым или с пустым текстом', () => {
    const { container, rerender } = render(<TopStatusBanner open={false} message="x" onClose={jest.fn()} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<TopStatusBanner open message="   " onClose={jest.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('показывает сообщение, автозакрытие по таймеру и закрытие кнопкой', () => {
    const onClose = jest.fn();
    render(<TopStatusBanner open message="Готово" severity="warning" onClose={onClose} autoHideMs={500} ariaLabel="Закрыть Z" top={60} />);
    expect(screen.getByRole('status')).toHaveTextContent('Готово');
    act(() => {
      jest.advanceTimersByTime(499);
    });
    expect(onClose).not.toHaveBeenCalled();
    act(() => {
      jest.advanceTimersByTime(2);
    });
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByLabelText('Закрыть Z'));
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it('таймер использует актуальный onClose и сбрасывается при закрытии', () => {
    const first = jest.fn();
    const second = jest.fn();
    const { rerender } = render(<TopStatusBanner open message="m" onClose={first} autoHideMs={100} />);
    rerender(<TopStatusBanner open message="m" onClose={second} autoHideMs={100} />);
    act(() => {
      jest.advanceTimersByTime(150);
    });
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);
    rerender(<TopStatusBanner open={false} message="m" onClose={second} autoHideMs={100} />);
    act(() => {
      jest.advanceTimersByTime(500);
    });
    expect(second).toHaveBeenCalledTimes(1);
  });

  it.each(['success', 'error', 'info'] as const)('поддерживает severity=%s', (severity) => {
    render(<TopStatusBanner open message="msg" severity={severity} onClose={jest.fn()} />);
    expect(screen.getByRole('status')).toBeInTheDocument();
  });
});

describe('StatusToastHost', () => {
  it('ничего не рисует без уведомлений', () => {
    const { container } = render(<StatusToastHost />);
    expect(container).toBeEmptyDOMElement();
  });

  it('показывает только последние 4, неизвестный тип приводится к info, закрытие вызывает removeNotification', () => {
    mockNotifications = Array.from({ length: 6 }, (_, i) => ({
      id: `n${i}`,
      type: i === 5 ? 'weird' : 'success',
      message: `Сообщение ${i}`,
    }));
    render(<StatusToastHost />);
    expect(screen.queryByText('Сообщение 1')).toBeNull();
    expect(screen.getByText('Сообщение 2')).toBeInTheDocument();
    expect(screen.getByText('Сообщение 5')).toBeInTheDocument();
    fireEvent.click(screen.getAllByLabelText('Закрыть уведомление')[0]);
    expect(mockRemove).toHaveBeenCalledWith('n2');
  });

  it('при очереди длиннее 8 лишние старые удаляются', () => {
    mockNotifications = Array.from({ length: 10 }, (_, i) => ({ id: `n${i}`, type: 'info', message: `m${i}` }));
    render(<StatusToastHost />);
    expect(mockRemove.mock.calls.map((c) => c[0])).toEqual(['n0', 'n1']);
  });

  it('тосты автоматически убираются через 2,5 с', () => {
    mockNotifications = [{ id: 'a', type: 'error', message: 'Короткое' }];
    render(<StatusToastHost />);
    act(() => {
      jest.advanceTimersByTime(2600);
    });
    expect(mockRemove).toHaveBeenCalledWith('a');
  });
});

describe('LoginSessionNotice', () => {
  it('ничего не показывает, если уведомления нет', () => {
    mockConsume.mockReturnValue(null);
    const { container } = render(<LoginSessionNotice />);
    expect(container).toBeEmptyDOMElement();
  });

  it('показывает причину, закрывается крестиком и автоматически через 10 с', () => {
    mockConsume.mockReturnValue('Сессия завершена');
    const { unmount } = render(<LoginSessionNotice />);
    expect(screen.getByText('Сессия завершена')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /close|закрыть/i }));
    expect(screen.queryByText('Сессия завершена')).toBeNull();
    unmount();

    mockConsume.mockReturnValue('Снова');
    render(<LoginSessionNotice />);
    act(() => {
      jest.advanceTimersByTime(9999);
    });
    expect(screen.getByText('Снова')).toBeInTheDocument();
    act(() => {
      jest.advanceTimersByTime(2);
    });
    expect(screen.queryByText('Снова')).toBeNull();
  });

  it('размонтирование очищает таймер автоскрытия', () => {
    mockConsume.mockReturnValue('x');
    const clear = jest.spyOn(global, 'clearTimeout');
    const { unmount } = render(<LoginSessionNotice />);
    unmount();
    expect(clear).toHaveBeenCalled();
  });
});

describe('LlmStatusBanner', () => {
  let fetchMock: jest.Mock;
  const settings = (over: Record<string, unknown> = {}) => ({
    app: { llmStatusPollSeconds: 30, llmStatusPollTrace: false, ...over },
  });
  const flush = async () => {
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
  };

  beforeEach(() => {
    fetchMock = jest.fn();
    (global as any).fetch = fetchMock;
  });

  it('при недоступном LLM показывает сообщение сервера, закрытие скрывает баннер', async () => {
    mockGetSettings.mockReturnValue(settings());
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ use_llm_svc: true, connected: false, message: 'LLM лежит' }) });
    render(<LlmStatusBanner />);
    await flush();
    expect(screen.getByText('LLM лежит')).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith('http://api.test/api/llm/status');
    fireEvent.click(screen.getByLabelText('Закрыть уведомление о подключении к LLM'));
    await flush();
    expect(screen.queryByText('LLM лежит')).toBeNull();
  });

  it('пустое сообщение заменяется текстом по умолчанию', async () => {
    mockGetSettings.mockReturnValue(settings());
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ use_llm_svc: true, connected: false, message: '  ' }) });
    render(<LlmStatusBanner />);
    await flush();
    expect(screen.getByText(/Подключиться к LLM не удалось/)).toBeInTheDocument();
  });

  it('когда LLM доступен или не используется, баннера нет; опрос повторяется по интервалу', async () => {
    mockGetSettings.mockReturnValue(settings({ llmStatusPollSeconds: 5 }));
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ use_llm_svc: true, connected: true }) });
    const { container } = render(<LlmStatusBanner />);
    await flush();
    expect(container).toBeEmptyDOMElement();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await act(async () => {
      jest.advanceTimersByTime(5000);
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('ответ с ошибкой HTTP и сбой сети баннер не показывают', async () => {
    mockGetSettings.mockReturnValue(settings());
    fetchMock.mockResolvedValueOnce({ ok: false });
    const { container } = render(<LlmStatusBanner />);
    await flush();
    expect(container).toBeEmptyDOMElement();
    fetchMock.mockRejectedValueOnce(new Error('offline'));
    await act(async () => {
      jest.advanceTimersByTime(30000);
    });
    expect(container).toBeEmptyDOMElement();
  });

  it('если настройки ещё не загружены, вызывается initSettings; при его ошибке опроса нет', async () => {
    mockGetSettings.mockImplementation(() => {
      throw new Error('not ready');
    });
    mockInitSettings.mockResolvedValueOnce(settings());
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({}) });
    const first = render(<LlmStatusBanner />);
    await flush();
    expect(mockInitSettings).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    first.unmount();
    fetchMock.mockClear();
    mockInitSettings.mockRejectedValueOnce(new Error('fail'));
    render(<LlmStatusBanner />);
    await flush();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('трассировка опроса пишет в консоль интервалы между тиками', async () => {
    mockGetSettings.mockReturnValue(settings({ llmStatusPollTrace: true, llmStatusPollSeconds: 2 }));
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({}) });
    const info = jest.spyOn(console, 'info').mockImplementation(() => {});
    render(<LlmStatusBanner />);
    await flush();
    await act(async () => {
      jest.advanceTimersByTime(2000);
    });
    expect(info.mock.calls[0][0]).toContain('delta=first');
    expect(info.mock.calls[1][0]).toMatch(/delta=2\.\d\ds/);
  });

  it('размонтирование до загрузки настроек не запускает опрос', async () => {
    let resolveSettings!: (v: unknown) => void;
    mockGetSettings.mockImplementation(() => {
      throw new Error('not ready');
    });
    mockInitSettings.mockReturnValue(new Promise((r) => (resolveSettings = r)));
    const { unmount } = render(<LlmStatusBanner />);
    unmount();
    await act(async () => resolveSettings(settings()));
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
