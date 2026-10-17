import { act, renderHook } from '@testing-library/react';
import {
  SIDEBARS_AUTO_COLLAPSED_STORAGE_KEY,
  SIDEBARS_COMPACT_MAX_WIDTH_PX,
  SIDEBARS_PHONE_MAX_WIDTH_PX,
  getSidebarPolicy,
  useResponsiveSidebars,
} from './useResponsiveSidebars';

describe('getSidebarPolicy', () => {
  it.each([
    [1920, false, false],
    [1440, false, false],
    [SIDEBARS_COMPACT_MAX_WIDTH_PX, false, false],
    [SIDEBARS_COMPACT_MAX_WIDTH_PX - 1, true, false],
    [820, true, false],
    [SIDEBARS_PHONE_MAX_WIDTH_PX, true, false],
    [SIDEBARS_PHONE_MAX_WIDTH_PX - 1, true, true],
    [390, true, true],
  ])('ширина %p: collapse=%p hideRight=%p', (width, collapse, hideRight) => {
    expect(getSidebarPolicy(width)).toEqual({ collapse, hideRight });
  });
});

/** Подмена matchMedia: хранит слушателей, `fire()` имитирует пересечение порога. */
function installMatchMedia() {
  const listeners = new Set<() => void>();
  window.matchMedia = ((query: string) => ({
    media: query,
    matches: false,
    onchange: null,
    addEventListener: (_: string, l: () => void) => listeners.add(l),
    removeEventListener: (_: string, l: () => void) => listeners.delete(l),
    addListener: (l: () => void) => listeners.add(l),
    removeListener: (l: () => void) => listeners.delete(l),
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
  return {
    fire: () => act(() => listeners.forEach((l) => l())),
    count: () => listeners.size,
  };
}

function setWidth(width: number) {
  Object.defineProperty(window, 'innerWidth', { configurable: true, writable: true, value: width });
}

function setup(initial: { left: boolean; right: boolean; hidden: boolean }, width: number) {
  setWidth(width);
  const mm = installMatchMedia();
  const state = { left: initial.left, right: initial.right, hidden: initial.hidden };
  const setters = {
    setSidebarOpen: jest.fn((v: boolean) => { state.left = v; }),
    setRightOpen: jest.fn((v: boolean) => { state.right = v; }),
    setRightHidden: jest.fn((v: boolean) => { state.hidden = v; }),
  };
  const hook = renderHook(
    () =>
      useResponsiveSidebars({
        sidebarOpen: state.left,
        rightSidebarOpen: state.right,
        rightSidebarHidden: state.hidden,
        ...setters,
      }),
  );
  const rerender = () => act(() => hook.rerender());
  return { mm, state, setters, rerender, hook };
}

describe('useResponsiveSidebars', () => {
  beforeEach(() => window.localStorage.clear());

  it('на широком экране ничего не трогает', () => {
    const { setters } = setup({ left: true, right: true, hidden: false }, 1440);
    expect(setters.setSidebarOpen).not.toHaveBeenCalled();
    expect(setters.setRightOpen).not.toHaveBeenCalled();
    expect(setters.setRightHidden).not.toHaveBeenCalled();
  });

  it('планшет при монтировании: обе панели сворачиваются, правая не скрывается', () => {
    const { setters } = setup({ left: true, right: true, hidden: false }, 820);
    expect(setters.setSidebarOpen).toHaveBeenCalledWith(false);
    expect(setters.setRightOpen).toHaveBeenCalledWith(false);
    expect(setters.setRightHidden).not.toHaveBeenCalled();
  });

  it('телефон при монтировании: панели свёрнуты, правая скрыта', () => {
    const { setters } = setup({ left: true, right: true, hidden: false }, 390);
    expect(setters.setSidebarOpen).toHaveBeenCalledWith(false);
    expect(setters.setRightOpen).toHaveBeenCalledWith(false);
    expect(setters.setRightHidden).toHaveBeenCalledWith(true);
  });

  it('уже свёрнутые панели не трогаются повторно', () => {
    const { setters } = setup({ left: false, right: false, hidden: true }, 390);
    expect(setters.setSidebarOpen).not.toHaveBeenCalled();
    expect(setters.setRightOpen).not.toHaveBeenCalled();
    expect(setters.setRightHidden).not.toHaveBeenCalled();
  });

  it('при расширении окна возвращает то, что свернул сам', () => {
    const { mm, state, setters, rerender } = setup({ left: true, right: true, hidden: false }, 390);
    rerender(); // состояние уже свёрнуто/скрыто
    setWidth(1440);
    mm.fire();
    expect(setters.setSidebarOpen).toHaveBeenLastCalledWith(true);
    expect(setters.setRightOpen).toHaveBeenLastCalledWith(true);
    expect(setters.setRightHidden).toHaveBeenLastCalledWith(false);
    expect(state).toEqual({ left: true, right: true, hidden: false });
  });

  it('не возвращает панель, которую пользователь свернул до сужения', () => {
    const { mm, setters, rerender } = setup({ left: false, right: true, hidden: false }, 820);
    rerender();
    setWidth(1440);
    mm.fire();
    expect(setters.setSidebarOpen).not.toHaveBeenCalledWith(true);
    expect(setters.setRightOpen).toHaveBeenLastCalledWith(true);
  });

  it('не перетирает выбор пользователя, сделанный в узком окне', () => {
    const { mm, state, setters, rerender } = setup({ left: true, right: true, hidden: false }, 820);
    state.left = true; // пользователь сам раскрыл левую панель
    rerender();
    setWidth(1440);
    mm.fire();
    // левая уже открыта — повторно не открываем
    expect(setters.setSidebarOpen).toHaveBeenCalledTimes(1); // только автосворачивание
  });

  it('переход планшет → телефон скрывает правую панель', () => {
    const { mm, setters, rerender } = setup({ left: true, right: true, hidden: false }, 820);
    rerender();
    setWidth(390);
    mm.fire();
    expect(setters.setRightHidden).toHaveBeenCalledWith(true);
  });

  it('подписывается на пороги и отписывается при размонтировании', () => {
    const { mm, hook } = setup({ left: true, right: true, hidden: false }, 1440);
    expect(mm.count()).toBeGreaterThan(0);
    hook.unmount();
    expect(mm.count()).toBe(0);
  });

  it('без matchMedia (старые окружения) не падает', () => {
    setWidth(1440);
    const original = window.matchMedia;
    // @ts-expect-error — имитируем окружение без matchMedia
    delete window.matchMedia;
    expect(() =>
      renderHook(() =>
        useResponsiveSidebars({
          sidebarOpen: true,
          rightSidebarOpen: true,
          rightSidebarHidden: false,
          setSidebarOpen: jest.fn(),
          setRightOpen: jest.fn(),
          setRightHidden: jest.fn(),
        }),
      ),
    ).not.toThrow();
    window.matchMedia = original;
  });

  describe('восстановление после перезагрузки страницы', () => {
    const readFlag = () => {
      const raw = window.localStorage.getItem(SIDEBARS_AUTO_COLLAPSED_STORAGE_KEY);
      return raw ? JSON.parse(raw) : null;
    };

    it('при автосворачивании запоминает прежнее состояние', () => {
      setup({ left: true, right: true, hidden: false }, 390);
      expect(readFlag()).toEqual({ left: true, right: true, hidRight: true });
    });

    it('при планшетной ширине запоминает, что правую панель не скрывали', () => {
      setup({ left: true, right: false, hidden: false }, 820);
      expect(readFlag()).toEqual({ left: true, right: false, hidRight: false });
    });

    it('на широком экране флаг не создаётся', () => {
      setup({ left: true, right: true, hidden: false }, 1440);
      expect(readFlag()).toBeNull();
    });

    it('после перезагрузки на широком экране возвращает панели и стирает флаг', () => {
      // Состояние, как после работы на телефоне: панели свёрнуты, правая скрыта, флаг сохранён.
      window.localStorage.setItem(
        SIDEBARS_AUTO_COLLAPSED_STORAGE_KEY,
        JSON.stringify({ left: true, right: true, hidRight: true }),
      );
      const { setters, state } = setup({ left: false, right: false, hidden: true }, 1440);
      expect(setters.setSidebarOpen).toHaveBeenCalledWith(true);
      expect(setters.setRightOpen).toHaveBeenCalledWith(true);
      expect(setters.setRightHidden).toHaveBeenCalledWith(false);
      expect(state).toEqual({ left: true, right: true, hidden: false });
      expect(readFlag()).toBeNull();
    });

    it('после перезагрузки на узком экране прежнее состояние не затирается', () => {
      window.localStorage.setItem(
        SIDEBARS_AUTO_COLLAPSED_STORAGE_KEY,
        JSON.stringify({ left: true, right: true, hidRight: true }),
      );
      const { setters } = setup({ left: false, right: false, hidden: true }, 390);
      expect(setters.setSidebarOpen).not.toHaveBeenCalled();
      expect(setters.setRightOpen).not.toHaveBeenCalled();
      expect(readFlag()).toEqual({ left: true, right: true, hidRight: true });
    });

    it('панели, свёрнутые пользователем до сужения, после перезагрузки не открываются', () => {
      window.localStorage.setItem(
        SIDEBARS_AUTO_COLLAPSED_STORAGE_KEY,
        JSON.stringify({ left: false, right: false, hidRight: false }),
      );
      const { setters } = setup({ left: false, right: false, hidden: false }, 1440);
      expect(setters.setSidebarOpen).not.toHaveBeenCalled();
      expect(setters.setRightOpen).not.toHaveBeenCalled();
      expect(setters.setRightHidden).not.toHaveBeenCalled();
    });

    it('повреждённый флаг игнорируется', () => {
      window.localStorage.setItem(SIDEBARS_AUTO_COLLAPSED_STORAGE_KEY, '{не json');
      expect(() => setup({ left: true, right: true, hidden: false }, 1440)).not.toThrow();
    });
  });
});

