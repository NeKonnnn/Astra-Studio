import { act, renderHook } from '@testing-library/react';
import { CHAT_FONT_SIZE_EVENT, CHAT_FONT_SIZE_KEY, setChatFontSize } from '@/utils/chatFontSize';
import { useChatFontSize } from '@/hooks/useChatFontSize';
import { useCommittedContent } from '@/hooks/useCommittedContent';
import { useInViewport } from '@/hooks/useInViewport';
import {
  RIGHT_SIDEBAR_DEFAULT_WIDTH_PX,
  RIGHT_SIDEBAR_MAX_WIDTH_PX,
  RIGHT_SIDEBAR_MIN_WIDTH_PX,
  RIGHT_SIDEBAR_WIDTH_PINNED_STORAGE_KEY,
  RIGHT_SIDEBAR_WIDTH_STORAGE_KEY,
  clampRightSidebarWidthPx,
  readRightSidebarWidthPinned,
  readRightSidebarWidthPx,
} from '@/hooks/useRightSidebarWidth';

describe('useChatFontSize', () => {
  beforeEach(() => localStorage.clear());

  it('starts with the stored size', () => {
    localStorage.setItem(CHAT_FONT_SIZE_KEY, 'large');

    const { result } = renderHook(() => useChatFontSize());

    expect(result.current).toEqual({ fontSize: 'large', fontSizeValue: '1.125rem' });
  });

  it('reacts to the custom event without polling', () => {
    const { result } = renderHook(() => useChatFontSize());
    expect(result.current.fontSize).toBe('medium');

    act(() => setChatFontSize('small'));

    expect(result.current).toEqual({ fontSize: 'small', fontSizeValue: '0.875rem' });
  });

  it('re-reads storage on an invalid event payload', () => {
    localStorage.setItem(CHAT_FONT_SIZE_KEY, 'large');
    const { result } = renderHook(() => useChatFontSize());
    localStorage.setItem(CHAT_FONT_SIZE_KEY, 'small');

    act(() => {
      window.dispatchEvent(new CustomEvent(CHAT_FONT_SIZE_EVENT, { detail: 'bogus' }));
    });

    expect(result.current.fontSize).toBe('small');
  });

  it('reacts to storage events for its own key only', () => {
    const { result } = renderHook(() => useChatFontSize());
    localStorage.setItem(CHAT_FONT_SIZE_KEY, 'large');

    act(() => {
      window.dispatchEvent(new StorageEvent('storage', { key: 'other' }));
    });
    expect(result.current.fontSize).toBe('medium');

    act(() => {
      window.dispatchEvent(new StorageEvent('storage', { key: CHAT_FONT_SIZE_KEY }));
    });
    expect(result.current.fontSize).toBe('large');
  });

  it('stops listening after unmount', () => {
    const { result, unmount } = renderHook(() => useChatFontSize());
    unmount();

    act(() => setChatFontSize('large'));

    expect(result.current.fontSize).toBe('medium');
  });
});

describe('useCommittedContent', () => {
  it('returns the live content while streaming', () => {
    const { result, rerender } = renderHook(({ c, s }) => useCommittedContent(c, s), {
      initialProps: { c: 'a', s: true },
    });
    expect(result.current).toBe('a');

    rerender({ c: 'ab', s: true });

    expect(result.current).toBe('ab');
  });

  it('commits the final content when streaming stops', () => {
    const { result, rerender } = renderHook(({ c, s }) => useCommittedContent(c, s), {
      initialProps: { c: 'a', s: true },
    });

    rerender({ c: 'итог', s: false });

    expect(result.current).toBe('итог');
  });

  it('follows content changes after streaming', () => {
    const { result, rerender } = renderHook(({ c }) => useCommittedContent(c, false), {
      initialProps: { c: 'v1' },
    });

    rerender({ c: 'v2' });

    expect(result.current).toBe('v2');
  });
});

describe('right sidebar width helpers', () => {
  beforeEach(() => localStorage.clear());

  it.each([
    [0, RIGHT_SIDEBAR_MIN_WIDTH_PX],
    [100, RIGHT_SIDEBAR_MIN_WIDTH_PX],
    [300.4, 300],
    [300.6, 301],
    [5000, RIGHT_SIDEBAR_MAX_WIDTH_PX],
  ])('clamps %p to %p', (value, expected) => {
    expect(clampRightSidebarWidthPx(value)).toBe(expected);
  });

  it('uses the default when nothing or garbage is stored', () => {
    expect(readRightSidebarWidthPx()).toBe(RIGHT_SIDEBAR_DEFAULT_WIDTH_PX);
    localStorage.setItem(RIGHT_SIDEBAR_WIDTH_STORAGE_KEY, 'wide');
    expect(readRightSidebarWidthPx()).toBe(RIGHT_SIDEBAR_DEFAULT_WIDTH_PX);
  });

  it('reads and clamps the stored width', () => {
    localStorage.setItem(RIGHT_SIDEBAR_WIDTH_STORAGE_KEY, '400');
    expect(readRightSidebarWidthPx()).toBe(400);
    localStorage.setItem(RIGHT_SIDEBAR_WIDTH_STORAGE_KEY, '99999');
    expect(readRightSidebarWidthPx()).toBe(RIGHT_SIDEBAR_MAX_WIDTH_PX);
  });

  it('reads the pinned flag strictly', () => {
    expect(readRightSidebarWidthPinned()).toBe(false);
    localStorage.setItem(RIGHT_SIDEBAR_WIDTH_PINNED_STORAGE_KEY, 'true');
    expect(readRightSidebarWidthPinned()).toBe(true);
    localStorage.setItem(RIGHT_SIDEBAR_WIDTH_PINNED_STORAGE_KEY, '1');
    expect(readRightSidebarWidthPinned()).toBe(false);
  });
});

describe('useInViewport', () => {
  type Callback = (entries: Array<{ isIntersecting: boolean }>) => void;
  let callback: Callback | null;
  let observe: jest.Mock;
  let disconnect: jest.Mock;
  const original = (global as unknown as { IntersectionObserver?: unknown }).IntersectionObserver;

  beforeEach(() => {
    callback = null;
    observe = jest.fn();
    disconnect = jest.fn();
    (global as unknown as { IntersectionObserver: unknown }).IntersectionObserver = jest.fn((cb: Callback) => {
      callback = cb;
      return { observe, disconnect };
    });
  });

  afterEach(() => {
    (global as unknown as { IntersectionObserver?: unknown }).IntersectionObserver = original;
  });

  function mount(rootMargin: string | undefined, initiallyVisible: boolean) {
    return renderHook(() => {
      const value = useInViewport<HTMLDivElement>(rootMargin, initiallyVisible);
      // Привязываем ref к реальному элементу до срабатывания эффекта.
      if (!value.ref.current) value.ref.current = document.createElement('div');
      return value;
    });
  }

  it('is visible and does not observe when initially visible', () => {
    const { result } = mount(undefined, true);

    expect(result.current.inView).toBe(true);
    expect(observe).not.toHaveBeenCalled();
  });

  it('observes when not initially visible and follows intersection changes', () => {
    const { result } = mount('100px 0px', false);

    expect(observe).toHaveBeenCalledTimes(1);
    expect(result.current.inView).toBe(false);

    act(() => callback!([{ isIntersecting: true }]));
    expect(result.current.inView).toBe(true);

    act(() => callback!([{ isIntersecting: true }]));
    expect(result.current.inView).toBe(true);

    act(() => callback!([{ isIntersecting: false }]));
    expect(result.current.inView).toBe(false);
  });

  it('ignores empty entries and disconnects on unmount', () => {
    const { result, unmount } = mount(undefined, false);

    act(() => callback!([]));
    expect(result.current.inView).toBe(false);

    unmount();
    expect(disconnect).toHaveBeenCalledTimes(1);
  });

  it('stays visible when IntersectionObserver is unavailable', () => {
    (global as unknown as { IntersectionObserver?: unknown }).IntersectionObserver = undefined;

    const { result } = mount(undefined, false);

    expect(result.current.inView).toBe(false);
  });
});
