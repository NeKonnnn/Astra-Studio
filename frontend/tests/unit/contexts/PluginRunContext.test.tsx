import React from 'react';
import { act, renderHook } from '@testing-library/react';

const mockShowNotification = jest.fn();
jest.mock('@/contexts/AppContext', () => ({
  useAppActions: () => ({ showNotification: mockShowNotification }),
}));

const mockInvokePlugin = jest.fn();
jest.mock('@/plugins/api', () => ({
  invokePlugin: (...a: unknown[]) => mockInvokePlugin(...a),
}));

const mockIncrementTab = jest.fn();
jest.mock('@/utils/tabNotifications', () => ({
  incrementTabNotification: () => mockIncrementTab(),
}));

import { PluginRunProvider, usePluginRuns } from '@/contexts/PluginRunContext';

const STORAGE_KEY = 'plugin_runs_last';
const wrapper = ({ children }: { children: React.ReactNode }) => (
  <PluginRunProvider>{children}</PluginRunProvider>
);

const file = (name = 'model.xlsx') => new File(['x'], name);
const PLUGIN = { id: 'cashflow', display_name: 'Аудит ДДС' };

/** Управляемый промис для invokePlugin. */
function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const flush = async () => {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
};

beforeEach(() => {
  jest.clearAllMocks();
  localStorage.clear();
});

describe('PluginRunContext: значения по умолчанию', () => {
  it('вне провайдера отдаёт пустые безопасные заглушки', () => {
    const { result } = renderHook(() => usePluginRuns());
    expect(result.current.runs).toEqual({});
    expect(result.current.getRun('x')).toBeNull();
    expect(result.current.isRunning('x')).toBe(false);
    expect(result.current.runningPlugins).toEqual([]);
    expect(result.current.elapsedSec('x')).toBe(0);
    expect(result.current.startRun(PLUGIN, { modelFile: file() })).toBe(false);
    expect(() => result.current.clearRun('x')).not.toThrow();
  });
});

describe('PluginRunContext: startRun', () => {
  it('отклоняет запуск без id плагина или без файла модели', () => {
    const { result } = renderHook(() => usePluginRuns(), { wrapper });
    let ok = true;
    act(() => {
      ok = result.current.startRun({ id: '' }, { modelFile: file() });
    });
    expect(ok).toBe(false);
    act(() => {
      ok = result.current.startRun(PLUGIN, { modelFile: undefined as any });
    });
    expect(ok).toBe(false);
    expect(mockInvokePlugin).not.toHaveBeenCalled();
  });

  it('успешный запуск: running → done, markdown из ответа, уведомление success', async () => {
    const d = deferred<any>();
    mockInvokePlugin.mockReturnValue(d.promise);
    const { result } = renderHook(() => usePluginRuns(), { wrapper });
    let ok = false;
    act(() => {
      ok = result.current.startRun(PLUGIN, {
        modelFile: file('m.xlsx'),
        qualityFile: file('q.xlsx'),
        prompt: '  проверь  ',
      });
    });
    expect(ok).toBe(true);
    expect(result.current.isRunning('cashflow')).toBe(true);
    expect(result.current.runningPlugins).toHaveLength(1);
    const running = result.current.getRun('cashflow')!;
    expect(running).toMatchObject({
      pluginName: 'Аудит ДДС',
      status: 'running',
      modelFileName: 'm.xlsx',
      qualityFileName: 'q.xlsx',
      prompt: 'проверь',
    });
    expect(mockInvokePlugin).toHaveBeenCalledWith('cashflow', {
      modelFile: expect.any(File),
      qualityFile: expect.any(File),
      prompt: '  проверь  ',
    });

    await act(async () => {
      d.resolve({ markdown: '# Готово', result: { status: 'ok' } });
    });
    const done = result.current.getRun('cashflow')!;
    expect(done.status).toBe('done');
    expect(done.markdown).toBe('# Готово');
    expect(done.resultStatus).toBe('ok');
    expect(done.summary).toContain('Аудит завершён');
    expect(done.error).toBeUndefined();
    expect(result.current.isRunning('cashflow')).toBe(false);
    expect(mockShowNotification).toHaveBeenCalledWith('success', 'Аудит ДДС: Аудит завершён');
    expect(mockIncrementTab).toHaveBeenCalledTimes(1);
    // сохранено в localStorage
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY)!);
    expect(stored.cashflow.status).toBe('done');
  });

  it('pluginName по умолчанию = id; prompt из пробелов не сохраняется', () => {
    mockInvokePlugin.mockReturnValue(new Promise(() => {}));
    const { result } = renderHook(() => usePluginRuns(), { wrapper });
    act(() => {
      result.current.startRun({ id: 'p2' }, { modelFile: file(), prompt: '   ' });
    });
    expect(result.current.getRun('p2')).toMatchObject({ pluginName: 'p2', prompt: undefined });
  });

  it('второй запуск того же плагина, пока первый идёт, отклоняется', () => {
    mockInvokePlugin.mockReturnValue(new Promise(() => {}));
    const { result } = renderHook(() => usePluginRuns(), { wrapper });
    act(() => {
      result.current.startRun(PLUGIN, { modelFile: file() });
    });
    let second = true;
    act(() => {
      second = result.current.startRun(PLUGIN, { modelFile: file() });
    });
    expect(second).toBe(false);
    expect(mockInvokePlugin).toHaveBeenCalledTimes(1);
  });

  it('после завершения можно запустить снова, новый запуск затирает прошлый результат', async () => {
    mockInvokePlugin.mockResolvedValueOnce({ markdown: 'one', result: { status: 'ok' } });
    const { result } = renderHook(() => usePluginRuns(), { wrapper });
    act(() => {
      result.current.startRun(PLUGIN, { modelFile: file('a.xlsx') });
    });
    await flush();
    expect(result.current.getRun('cashflow')!.markdown).toBe('one');

    const d = deferred<any>();
    mockInvokePlugin.mockReturnValueOnce(d.promise);
    let ok = false;
    act(() => {
      ok = result.current.startRun(PLUGIN, { modelFile: file('b.xlsx') });
    });
    expect(ok).toBe(true);
    expect(result.current.getRun('cashflow')).toMatchObject({
      status: 'running',
      modelFileName: 'b.xlsx',
    });
    expect(result.current.getRun('cashflow')!.markdown).toBeUndefined();
    await act(async () => {
      d.resolve({ markdown: 'two', result: { status: 'ok' } });
    });
    expect(result.current.getRun('cashflow')!.markdown).toBe('two');
  });

  it('пустой markdown заменяется заглушкой', async () => {
    mockInvokePlugin.mockResolvedValue({ result: {} });
    const { result } = renderHook(() => usePluginRuns(), { wrapper });
    act(() => {
      result.current.startRun(PLUGIN, { modelFile: file() });
    });
    await flush();
    const run = result.current.getRun('cashflow')!;
    expect(run.status).toBe('done');
    expect(run.markdown).toContain('ответ получен, но markdown пуст');
    expect(run.resultStatus).toBeUndefined();
    expect(run.summary).toBe('Аудит завершён со статусом: unknown.');
  });

  it('status=error в ответе сервиса → done, но уведомление error', async () => {
    mockInvokePlugin.mockResolvedValue({ markdown: 'm', result: { status: 'error' } });
    const { result } = renderHook(() => usePluginRuns(), { wrapper });
    act(() => {
      result.current.startRun(PLUGIN, { modelFile: file() });
    });
    await flush();
    expect(result.current.getRun('cashflow')!.status).toBe('done');
    expect(mockShowNotification).toHaveBeenCalledWith('error', 'Аудит ДДС: Статус: error');
  });

  it('вложенный result.result используется для статуса', async () => {
    mockInvokePlugin.mockResolvedValue({ result: { result: { status: 'degraded', message: 'без LLM' } } });
    const { result } = renderHook(() => usePluginRuns(), { wrapper });
    act(() => {
      result.current.startRun(PLUGIN, { modelFile: file() });
    });
    await flush();
    const run = result.current.getRun('cashflow')!;
    expect(run.resultStatus).toBe('degraded');
    expect(run.markdown).toContain('без LLM');
  });

  it('ошибка запуска: status=error, текст ошибки, уведомление и снятие inflight', async () => {
    mockInvokePlugin.mockRejectedValueOnce(new Error('  сервис недоступен  '));
    const { result } = renderHook(() => usePluginRuns(), { wrapper });
    act(() => {
      result.current.startRun(PLUGIN, { modelFile: file() });
    });
    await flush();
    const run = result.current.getRun('cashflow')!;
    expect(run.status).toBe('error');
    expect(run.error).toBe('сервис недоступен');
    expect(run.finishedAtMs).toBeDefined();
    expect(mockShowNotification).toHaveBeenCalledWith('error', 'Аудит ДДС: сервис недоступен');
    expect(mockIncrementTab).toHaveBeenCalledTimes(1);

    // inflight снят — повторный запуск разрешён
    mockInvokePlugin.mockReturnValueOnce(new Promise(() => {}));
    let again = false;
    act(() => {
      again = result.current.startRun(PLUGIN, { modelFile: file() });
    });
    expect(again).toBe(true);
  });

  it('не-Error исключение приводится к строке; пустое — к общему сообщению', async () => {
    mockInvokePlugin.mockRejectedValueOnce('строковая ошибка');
    const { result } = renderHook(() => usePluginRuns(), { wrapper });
    act(() => {
      result.current.startRun({ id: 'a' }, { modelFile: file() });
    });
    await flush();
    expect(result.current.getRun('a')!.error).toBe('строковая ошибка');

    mockInvokePlugin.mockRejectedValueOnce(new Error('   '));
    act(() => {
      result.current.startRun({ id: 'b' }, { modelFile: file() });
    });
    await flush();
    expect(result.current.getRun('b')!.error).toContain('Запуск плагина прервался без сообщения');

    mockInvokePlugin.mockRejectedValueOnce(undefined);
    act(() => {
      result.current.startRun({ id: 'c' }, { modelFile: file() });
    });
    await flush();
    expect(result.current.getRun('c')!.error).toContain('Запуск плагина прервался без сообщения');
  });

  it('параллельные запуски разных плагинов независимы', async () => {
    const d1 = deferred<any>();
    const d2 = deferred<any>();
    mockInvokePlugin.mockReturnValueOnce(d1.promise).mockReturnValueOnce(d2.promise);
    const { result } = renderHook(() => usePluginRuns(), { wrapper });
    act(() => {
      result.current.startRun({ id: 'p1' }, { modelFile: file() });
      result.current.startRun({ id: 'p2' }, { modelFile: file() });
    });
    expect(result.current.runningPlugins).toHaveLength(2);
    await act(async () => {
      d1.resolve({ markdown: 'one', result: { status: 'ok' } });
    });
    expect(result.current.isRunning('p1')).toBe(false);
    expect(result.current.isRunning('p2')).toBe(true);
    await act(async () => {
      d2.reject(new Error('fail'));
    });
    expect(result.current.getRun('p2')!.status).toBe('error');
  });
});

describe('PluginRunContext: clearRun', () => {
  it('удаляет завершённый запуск и обновляет localStorage', async () => {
    mockInvokePlugin.mockResolvedValue({ markdown: 'm', result: { status: 'ok' } });
    const { result } = renderHook(() => usePluginRuns(), { wrapper });
    act(() => {
      result.current.startRun(PLUGIN, { modelFile: file() });
    });
    await flush();
    act(() => result.current.clearRun('cashflow'));
    expect(result.current.getRun('cashflow')).toBeNull();
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY)!)).toEqual({});
  });

  it('не удаляет идущий запуск и игнорирует неизвестный id', () => {
    mockInvokePlugin.mockReturnValue(new Promise(() => {}));
    const { result } = renderHook(() => usePluginRuns(), { wrapper });
    act(() => {
      result.current.startRun(PLUGIN, { modelFile: file() });
    });
    act(() => result.current.clearRun('cashflow'));
    expect(result.current.isRunning('cashflow')).toBe(true);
    act(() => result.current.clearRun('nope'));
    expect(Object.keys(result.current.runs)).toEqual(['cashflow']);
  });
});

describe('PluginRunContext: восстановление из localStorage', () => {
  it('«running» после перезагрузки страницы превращается в error с пояснением', () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        cashflow: { pluginId: 'cashflow', pluginName: 'N', status: 'running', startedAtMs: 1, modelFileName: 'm' },
        ok: { pluginId: 'ok', pluginName: 'O', status: 'done', startedAtMs: 1, finishedAtMs: 5, modelFileName: 'm' },
        broken: null,
      }),
    );
    const { result } = renderHook(() => usePluginRuns(), { wrapper });
    const restored = result.current.getRun('cashflow')!;
    expect(restored.status).toBe('error');
    expect(restored.error).toContain('Страница была перезагружена');
    expect(restored.finishedAtMs).toBeGreaterThan(0);
    expect(result.current.getRun('ok')!.status).toBe('done');
    expect(result.current.getRun('broken')).toBeNull();
    expect(result.current.isRunning('cashflow')).toBe(false);
  });

  it('у уже завершённой записи finishedAtMs не затирается', () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        p: { pluginId: 'p', pluginName: 'P', status: 'running', startedAtMs: 1, finishedAtMs: 77, modelFileName: 'm' },
      }),
    );
    const { result } = renderHook(() => usePluginRuns(), { wrapper });
    expect(result.current.getRun('p')!.finishedAtMs).toBe(77);
  });

  it.each([
    ['битый JSON', '{not json'],
    ['null', 'null'],
    ['примитив', '42'],
  ])('невалидное содержимое (%s) → пустое состояние', (_n, raw) => {
    localStorage.setItem(STORAGE_KEY, raw);
    const { result } = renderHook(() => usePluginRuns(), { wrapper });
    expect(result.current.runs).toEqual({});
  });
});

describe('PluginRunContext: переполнение localStorage', () => {
  it('при ошибке записи сохраняет облегчённую копию без raw', async () => {
    mockInvokePlugin.mockResolvedValue({ markdown: 'm', result: { status: 'ok' } });
    const realSetItem = Storage.prototype.setItem;
    let firstFailure = true;
    const spy = jest.spyOn(Storage.prototype, 'setItem').mockImplementation(function (this: Storage, k: string, v: string) {
      if (k === STORAGE_KEY && v.includes('"raw"') && firstFailure) {
        firstFailure = false;
        throw new Error('QuotaExceededError');
      }
      return realSetItem.call(this, k, v);
    });
    const { result } = renderHook(() => usePluginRuns(), { wrapper });
    act(() => {
      result.current.startRun(PLUGIN, { modelFile: file() });
    });
    await flush();
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY)!);
    expect(stored.cashflow.status).toBe('done');
    expect(stored.cashflow.raw).toBeUndefined();
    spy.mockRestore();
  });

  it('если и облегчённая запись падает — запуск не ломается', async () => {
    mockInvokePlugin.mockResolvedValue({ markdown: 'm', result: { status: 'ok' } });
    const spy = jest.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceededError');
    });
    const { result } = renderHook(() => usePluginRuns(), { wrapper });
    act(() => {
      result.current.startRun(PLUGIN, { modelFile: file() });
    });
    await flush();
    expect(result.current.getRun('cashflow')!.status).toBe('done');
    spy.mockRestore();
  });
});

describe('PluginRunContext: секундомер и beforeunload', () => {
  beforeEach(() => {
    jest.useFakeTimers();
  });
  afterEach(() => {
    jest.useRealTimers();
  });

  it('elapsedSec: 0 для неизвестного, растёт у идущего, фиксируется у завершённого', async () => {
    const d = deferred<any>();
    mockInvokePlugin.mockReturnValue(d.promise);
    const { result } = renderHook(() => usePluginRuns(), { wrapper });
    expect(result.current.elapsedSec('nope')).toBe(0);
    act(() => {
      result.current.startRun(PLUGIN, { modelFile: file() });
    });
    expect(result.current.elapsedSec('cashflow')).toBe(0);
    act(() => jest.advanceTimersByTime(3000));
    expect(result.current.elapsedSec('cashflow')).toBe(3);

    await act(async () => {
      d.resolve({ markdown: 'm', result: { status: 'ok' } });
    });
    const frozen = result.current.elapsedSec('cashflow');
    act(() => jest.advanceTimersByTime(10_000));
    expect(result.current.elapsedSec('cashflow')).toBe(frozen);
  });

  it('elapsedSec для записи без finishedAtMs считает до текущего момента', () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        p: { pluginId: 'p', pluginName: 'P', status: 'done', startedAtMs: Date.now() - 5000, modelFileName: 'm' },
      }),
    );
    const { result } = renderHook(() => usePluginRuns(), { wrapper });
    expect(result.current.elapsedSec('p')).toBe(5);
  });

  it('пока есть идущий запуск — вешает beforeunload и снимает после завершения', async () => {
    const d = deferred<any>();
    mockInvokePlugin.mockReturnValue(d.promise);
    const addSpy = jest.spyOn(window, 'addEventListener');
    const removeSpy = jest.spyOn(window, 'removeEventListener');
    const { result } = renderHook(() => usePluginRuns(), { wrapper });
    expect(addSpy.mock.calls.some((c) => c[0] === 'beforeunload')).toBe(false);
    act(() => {
      result.current.startRun(PLUGIN, { modelFile: file() });
    });
    const call = addSpy.mock.calls.find((c) => c[0] === 'beforeunload')!;
    expect(call).toBeDefined();
    const handler = call[1] as (e: BeforeUnloadEvent) => string;
    const evt = { preventDefault: jest.fn(), returnValue: 'x' } as any;
    expect(handler(evt)).toBe('');
    expect(evt.preventDefault).toHaveBeenCalled();
    expect(evt.returnValue).toBe('');

    await act(async () => {
      d.resolve({ markdown: 'm', result: { status: 'ok' } });
    });
    expect(removeSpy.mock.calls.some((c) => c[0] === 'beforeunload')).toBe(true);
    addSpy.mockRestore();
    removeSpy.mockRestore();
  });

  it('интервал секундомера останавливается, когда запусков нет', async () => {
    const clearSpy = jest.spyOn(window, 'clearInterval');
    mockInvokePlugin.mockResolvedValue({ markdown: 'm', result: { status: 'ok' } });
    const { result } = renderHook(() => usePluginRuns(), { wrapper });
    act(() => {
      result.current.startRun(PLUGIN, { modelFile: file() });
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(clearSpy).toHaveBeenCalled();
    clearSpy.mockRestore();
  });
});
