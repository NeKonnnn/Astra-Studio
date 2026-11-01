import '@testing-library/jest-dom';
import React from 'react';
import { render } from '@testing-library/react';
import WorkZoneSnowfall from '@/components/WorkZoneSnowfall';
import WorkZoneStarrySky from '@/components/WorkZoneStarrySky';

type Rec = { fn: string; args: unknown[] };

let rafQueue: Array<(ts: number) => void>;
let rafId: number;
let cancelled: number[];
let ctxCalls: Rec[];
let gradients: Array<{ args: unknown[]; stops: unknown[][] }>;
let roCallbacks: Array<() => void>;
let roDisconnect: jest.Mock;
let ctxAvailable: boolean;
let size: { w: number; h: number };
let transform: unknown[] | null;

const origGetContext = HTMLCanvasElement.prototype.getContext;

function makeCtx() {
  const target: any = {};
  return new Proxy(target, {
    get(_t, prop: string) {
      if (prop === 'createLinearGradient') {
        return (...args: unknown[]) => {
          const g = { args, stops: [] as unknown[][], addColorStop: (...a: unknown[]) => g.stops.push(a) };
          gradients.push(g);
          return g;
        };
      }
      if (prop in target) return target[prop];
      return (...args: unknown[]) => {
        ctxCalls.push({ fn: prop, args });
        if (prop === 'setTransform') transform = args;
      };
    },
    set(t, prop: string, value) {
      t[prop] = value;
      ctxCalls.push({ fn: `set:${prop}`, args: [value] });
      return true;
    },
  });
}

function runFrames(count: number, startTs = 1000, step = 16) {
  for (let i = 0; i < count; i += 1) {
    const cbs = rafQueue.splice(0);
    cbs.forEach((cb) => cb(startTs + i * step));
  }
}

beforeEach(() => {
  rafQueue = [];
  rafId = 0;
  cancelled = [];
  ctxCalls = [];
  gradients = [];
  roCallbacks = [];
  roDisconnect = jest.fn();
  ctxAvailable = true;
  size = { w: 400, h: 300 };
  transform = null;
  jest.spyOn(window, 'requestAnimationFrame').mockImplementation(((cb: FrameRequestCallback) => {
    rafQueue.push(cb as (ts: number) => void);
    return ++rafId;
  }) as any);
  jest.spyOn(window, 'cancelAnimationFrame').mockImplementation(((id: number) => {
    cancelled.push(id);
  }) as any);
  (global as any).ResizeObserver = class {
    constructor(cb: () => void) {
      roCallbacks.push(cb);
    }
    observe() {}
    disconnect() {
      roDisconnect();
    }
  };
  HTMLCanvasElement.prototype.getContext = (() => (ctxAvailable ? makeCtx() : null)) as any;
  Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, get: () => size.w });
  Object.defineProperty(HTMLElement.prototype, 'clientHeight', { configurable: true, get: () => size.h });
  jest.spyOn(performance, 'now').mockReturnValue(1000);
});

afterEach(() => {
  HTMLCanvasElement.prototype.getContext = origGetContext;
  delete (HTMLElement.prototype as any).clientWidth;
  delete (HTMLElement.prototype as any).clientHeight;
  jest.restoreAllMocks();
});

const arcs = () => ctxCalls.filter((c) => c.fn === 'arc');
const fills = () => ctxCalls.filter((c) => c.fn === 'set:fillStyle').map((c) => String(c.args[0]));

describe.each([
  ['WorkZoneSnowfall', WorkZoneSnowfall],
  ['WorkZoneStarrySky', WorkZoneStarrySky],
] as const)('%s: общая механика canvas', (_name, Component) => {
  it('canvas скрыт от скринридеров, размеры учитывают devicePixelRatio (не более 2)', () => {
    Object.defineProperty(window, 'devicePixelRatio', { configurable: true, value: 3 });
    const { container } = render(<Component isDarkMode />);
    const canvas = container.querySelector('canvas')!;
    expect(canvas).toHaveAttribute('aria-hidden', 'true');
    expect(canvas.width).toBe(800);
    expect(canvas.height).toBe(600);
    expect(canvas.style.width).toBe('400px');
    expect(transform).toEqual([2, 0, 0, 2, 0, 0]);
    Object.defineProperty(window, 'devicePixelRatio', { configurable: true, value: 1 });
  });

  it('без devicePixelRatio используется масштаб 1', () => {
    Object.defineProperty(window, 'devicePixelRatio', { configurable: true, value: 0 });
    const { container } = render(<Component isDarkMode={false} />);
    expect(container.querySelector('canvas')!.width).toBe(400);
    Object.defineProperty(window, 'devicePixelRatio', { configurable: true, value: 1 });
  });

  it('без 2D-контекста анимация не запускается', () => {
    ctxAvailable = false;
    render(<Component isDarkMode />);
    expect(rafQueue).toHaveLength(0);
  });

  it('слишком маленький контейнер: рисование ждёт нормального размера', () => {
    size = { w: 1, h: 1 };
    render(<Component isDarkMode />);
    runFrames(3);
    expect(ctxCalls.filter((c) => c.fn === 'fillRect')).toHaveLength(0);
    expect(rafQueue.length).toBe(1); // кадр перепланирован
    size = { w: 300, h: 200 };
    roCallbacks[0]();
    runFrames(1);
    expect(ctxCalls.filter((c) => c.fn === 'fillRect').length).toBeGreaterThan(0);
  });

  it('ResizeObserver пересоздаёт частицы только при смене размера', () => {
    render(<Component isDarkMode />);
    runFrames(1);
    const first = arcs().length;
    ctxCalls = [];
    roCallbacks[0]();
    runFrames(1);
    expect(arcs().length).toBe(first); // тот же размер: то же число частиц
    size = { w: 2000, h: 1500 };
    ctxCalls = [];
    roCallbacks[0]();
    runFrames(1);
    expect(arcs().length).toBeGreaterThan(first); // больше площадь: до 130 частиц
  });

  it('при размонтировании отключается наблюдатель и отменяется кадр', () => {
    const { unmount } = render(<Component isDarkMode />);
    unmount();
    expect(roDisconnect).toHaveBeenCalledTimes(1);
    expect(cancelled.length).toBe(1);
  });

  it('частицы, ушедшие за нижний край, возрождаются сверху', () => {
    size = { w: 200, h: 60 };
    render(<Component isDarkMode />);
    runFrames(200, 1000, 40);
    const ys = arcs().map((c) => c.args[1] as number);
    expect(Math.max(...ys.slice(-60))).toBeLessThan(60 + 25);
  });

  it('частицы выходящие за боковые границы переносятся на противоположную сторону', () => {
    size = { w: 2, h: 100000 };
    const sinSpy = jest.spyOn(Math, 'sin').mockReturnValue(1);
    render(<Component isDarkMode />);
    runFrames(120, 1000, 40);
    let xs = arcs().slice(-56).map((c) => c.args[0] as number);
    expect(Math.max(...xs)).toBeLessThanOrEqual(2 + 30 + 2);
    sinSpy.mockReturnValue(-1);
    ctxCalls = [];
    runFrames(200, 5000, 40);
    xs = arcs().slice(-56).map((c) => c.args[0] as number);
    expect(Math.min(...xs)).toBeGreaterThanOrEqual(-30 - 2);
  });
});

describe('WorkZoneSnowfall: отрисовка', () => {
  it('тёмная и светлая тема используют разные градиенты неба', () => {
    const { unmount } = render(<WorkZoneSnowfall isDarkMode />);
    runFrames(1);
    expect(gradients[0].stops[0]).toEqual([0, '#070f22']);
    unmount();
    rafQueue = [];
    gradients = [];
    render(<WorkZoneSnowfall isDarkMode={false} />);
    runFrames(1);
    expect(gradients[0].stops[0]).toEqual([0, '#1a2d4a']);
  });

  it('рисует и «подробные» снежинки (6 лучей), и мягкие кружки', () => {
    render(<WorkZoneSnowfall isDarkMode />);
    runFrames(1);
    const strokes = ctxCalls.filter((c) => c.fn === 'stroke').length;
    expect(strokes).toBeGreaterThan(0);
    expect(strokes % 6).toBe(0);
    expect(fills().some((f) => f.startsWith('rgba(255,255,255,'))).toBe(true);
    expect(ctxCalls.filter((c) => c.fn === 'save').length).toBe(ctxCalls.filter((c) => c.fn === 'restore').length);
  });

  it('смена темы перезапускает анимацию', () => {
    const { rerender } = render(<WorkZoneSnowfall isDarkMode />);
    rerender(<WorkZoneSnowfall isDarkMode={false} />);
    expect(roDisconnect).toHaveBeenCalledTimes(1);
  });
});

describe('WorkZoneStarrySky: отрисовка', () => {
  it('фон чёрный, звёзды белые с прозрачностью до 0.9', () => {
    render(<WorkZoneStarrySky isDarkMode />);
    runFrames(1);
    expect(fills()[0]).toBe('#000000');
    const star = fills().filter((f) => f.startsWith('rgba(255,255,255,'));
    expect(star.length).toBeGreaterThanOrEqual(56);
    for (const s of star) {
      const a = Number(s.replace('rgba(255,255,255,', '').replace(')', ''));
      expect(a).toBeLessThanOrEqual(0.9);
      expect(a).toBeGreaterThan(0.3);
    }
  });
});
