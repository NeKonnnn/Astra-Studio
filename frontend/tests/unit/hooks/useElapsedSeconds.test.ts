import { act, renderHook } from '@testing-library/react';
import {
  computeGenerationDurationSec,
  formatGenerationDuration,
  useElapsedSeconds,
} from '@/hooks/useElapsedSeconds';

describe('useElapsedSeconds', () => {
  beforeEach(() => {
    jest.useFakeTimers('modern');
    jest.setSystemTime(new Date('2026-01-01T00:00:10Z'));
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  const t0 = new Date('2026-01-01T00:00:00Z').getTime();

  it('is null while inactive or without a start time', () => {
    expect(renderHook(() => useElapsedSeconds(t0, false)).result.current).toBeNull();
    expect(renderHook(() => useElapsedSeconds(null, true)).result.current).toBeNull();
    expect(renderHook(() => useElapsedSeconds(0, true)).result.current).toBeNull();
    expect(renderHook(() => useElapsedSeconds(undefined, true)).result.current).toBeNull();
  });

  it('shows elapsed seconds immediately and ticks every second', () => {
    const { result } = renderHook(() => useElapsedSeconds(t0, true));

    expect(result.current).toBe(10);

    act(() => {
      jest.advanceTimersByTime(3000);
    });

    expect(result.current).toBe(13);
  });

  it('never goes negative for a start time in the future', () => {
    const { result } = renderHook(() => useElapsedSeconds(t0 + 60_000, true));

    expect(result.current).toBe(0);
  });

  it('resets to null when deactivated and stops the timer', () => {
    const { result, rerender } = renderHook(({ active }) => useElapsedSeconds(t0, active), {
      initialProps: { active: true },
    });
    expect(result.current).toBe(10);

    rerender({ active: false });

    expect(result.current).toBeNull();
    expect(jest.getTimerCount()).toBe(0);
  });

  it('clears the interval on unmount', () => {
    const { unmount } = renderHook(() => useElapsedSeconds(t0, true));
    expect(jest.getTimerCount()).toBe(1);

    unmount();

    expect(jest.getTimerCount()).toBe(0);
  });
});

describe('formatGenerationDuration', () => {
  it.each([
    [0, '0:00'],
    [5, '0:05'],
    [59.6, '1:00'],
    [75, '1:15'],
    [600, '10:00'],
    [3725, '62:05'],
    [-4, '0:00'],
  ])('%p -> %p', (seconds, expected) => {
    expect(formatGenerationDuration(seconds)).toBe(expected);
  });
});

describe('computeGenerationDurationSec', () => {
  beforeEach(() => {
    jest.useFakeTimers('modern');
    jest.setSystemTime(1_000_000);
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it('returns at least one second', () => {
    expect(computeGenerationDurationSec(1_000_000)).toBe(1);
    expect(computeGenerationDurationSec(999_800)).toBe(1);
  });

  it('rounds to whole seconds', () => {
    expect(computeGenerationDurationSec(1_000_000 - 4_400)).toBe(4);
    expect(computeGenerationDurationSec(1_000_000 - 4_600)).toBe(5);
  });

  it('falls back to one second without a valid start', () => {
    expect(computeGenerationDurationSec(null)).toBe(1);
    expect(computeGenerationDurationSec(undefined)).toBe(1);
    expect(computeGenerationDurationSec(0)).toBe(1);
  });
});
