import { useEffect, useRef } from 'react';

/** Ниже этой ширины обе боковые панели сворачиваются в «рельсы» (md в MUI = 900). */
export const SIDEBARS_COMPACT_MAX_WIDTH_PX = 900;
/** Ниже этой ширины (телефон) правая панель скрывается совсем (sm в MUI = 600). */
export const SIDEBARS_PHONE_MAX_WIDTH_PX = 600;

export interface SidebarPolicy {
  /** Свернуть обе панели в рельсы */
  collapse: boolean;
  /** Скрыть правую панель совсем */
  hideRight: boolean;
}

/** Чистая политика: что делать с панелями при данной ширине окна. */
export function getSidebarPolicy(widthPx: number): SidebarPolicy {
  return {
    collapse: widthPx < SIDEBARS_COMPACT_MAX_WIDTH_PX,
    hideRight: widthPx < SIDEBARS_PHONE_MAX_WIDTH_PX,
  };
}

/** Что было до автосворачивания: переживает перезагрузку страницы (состояние панелей хранится в localStorage). */
export const SIDEBARS_AUTO_COLLAPSED_STORAGE_KEY = 'astra-sidebars-auto-collapsed';

interface AutoCollapsed {
  left: boolean;
  right: boolean;
  /** правую панель скрыли мы (а не пользователь) */
  hidRight: boolean;
}

function readAutoCollapsed(): AutoCollapsed | null {
  try {
    const raw = window.localStorage.getItem(SIDEBARS_AUTO_COLLAPSED_STORAGE_KEY);
    if (!raw) return null;
    const v = JSON.parse(raw);
    if (v && typeof v.left === 'boolean' && typeof v.right === 'boolean') {
      return { left: v.left, right: v.right, hidRight: v.hidRight === true };
    }
  } catch {
    /* повреждённое значение — игнорируем */
  }
  return null;
}

function writeAutoCollapsed(value: AutoCollapsed | null): void {
  try {
    if (value) window.localStorage.setItem(SIDEBARS_AUTO_COLLAPSED_STORAGE_KEY, JSON.stringify(value));
    else window.localStorage.removeItem(SIDEBARS_AUTO_COLLAPSED_STORAGE_KEY);
  } catch {
    /* localStorage недоступен — работаем без восстановления после перезагрузки */
  }
}

interface Options {
  sidebarOpen: boolean;
  rightSidebarOpen: boolean;
  rightSidebarHidden: boolean;
  setSidebarOpen: (open: boolean) => void;
  setRightOpen: (open: boolean) => void;
  setRightHidden: (hidden: boolean) => void;
}

function subscribe(mq: MediaQueryList, listener: () => void): () => void {
  if (typeof mq.addEventListener === 'function') {
    mq.addEventListener('change', listener);
    return () => mq.removeEventListener('change', listener);
  }
  // Safari < 14
  mq.addListener(listener);
  return () => mq.removeListener(listener);
}

/**
 * Адаптивные боковые панели. Раньше обе панели (по 240 px) оставались раскрытыми на любой ширине:
 * на планшете центральная колонка сжималась до ~340 px, на телефоне уезжала за экран.
 *
 * - ширина < 900 px: обе панели сворачиваются в рельсы (64 px);
 * - ширина < 600 px: правая панель скрывается совсем (остаётся кнопка «показать»);
 * - при возврате к широкому окну восстанавливается то, что было до автосворачивания
 *   (только если пользователь сам ничего не менял, иначе его выбор не трогаем).
 * Срабатывает при монтировании и при пересечении порогов, а не на каждый пиксель ресайза.
 */
export function useResponsiveSidebars({
  sidebarOpen,
  rightSidebarOpen,
  rightSidebarHidden,
  setSidebarOpen,
  setRightOpen,
  setRightHidden,
}: Options): void {
  const latest = useRef({ sidebarOpen, rightSidebarOpen, rightSidebarHidden });
  latest.current = { sidebarOpen, rightSidebarOpen, rightSidebarHidden };
  const setters = useRef({ setSidebarOpen, setRightOpen, setRightHidden });
  setters.current = { setSidebarOpen, setRightOpen, setRightHidden };

  // Что мы свернули/скрыли сами и что нужно вернуть при расширении окна.
  // Начальное значение читаем из localStorage: так восстановление работает и после перезагрузки.
  const stored = useRef<AutoCollapsed | null | undefined>(undefined);
  if (stored.current === undefined) {
    stored.current = typeof window === 'undefined' ? null : readAutoCollapsed();
  }
  const saved = useRef<AutoCollapsed | null>(stored.current);

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return undefined;

    const apply = () => {
      const policy = getSidebarPolicy(window.innerWidth);
      const cur = latest.current;
      const set = setters.current;

      if (policy.collapse) {
        if (saved.current === null) {
          saved.current = { left: cur.sidebarOpen, right: cur.rightSidebarOpen, hidRight: false };
          if (cur.sidebarOpen) set.setSidebarOpen(false);
          if (cur.rightSidebarOpen) set.setRightOpen(false);
        }
        if (policy.hideRight && !saved.current.hidRight && !cur.rightSidebarHidden) {
          saved.current = { ...saved.current, hidRight: true };
          set.setRightHidden(true);
        } else if (!policy.hideRight && saved.current.hidRight) {
          saved.current = { ...saved.current, hidRight: false };
          if (cur.rightSidebarHidden) set.setRightHidden(false);
        }
        writeAutoCollapsed(saved.current);
      } else if (saved.current !== null) {
        const prev = saved.current;
        saved.current = null;
        writeAutoCollapsed(null);
        // Возвращаем только то, что мы сами свернули/скрыли.
        if (prev.left && !cur.sidebarOpen) set.setSidebarOpen(true);
        if (prev.right && !cur.rightSidebarOpen) set.setRightOpen(true);
        if (prev.hidRight && cur.rightSidebarHidden) set.setRightHidden(false);
      }
    };

    apply();
    const compactMq = window.matchMedia(`(max-width: ${SIDEBARS_COMPACT_MAX_WIDTH_PX - 0.02}px)`);
    const phoneMq = window.matchMedia(`(max-width: ${SIDEBARS_PHONE_MAX_WIDTH_PX - 0.02}px)`);
    const offCompact = subscribe(compactMq, apply);
    const offPhone = subscribe(phoneMq, apply);
    return () => {
      offCompact();
      offPhone();
    };
  }, []);
}
