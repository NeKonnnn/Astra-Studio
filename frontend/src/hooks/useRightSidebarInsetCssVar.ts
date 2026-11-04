import { useEffect, useState } from 'react';
import {
  RIGHT_SIDEBAR_DEFAULT_WIDTH_PX,
  clampRightSidebarWidthPx,
} from './useRightSidebarWidth';

/** Ширина правой панели в развёрнутом и rail-состоянии (как у Drawer). */
export const RIGHT_SIDEBAR_EXPANDED_WIDTH_PX = RIGHT_SIDEBAR_DEFAULT_WIDTH_PX;
export const RIGHT_SIDEBAR_COLLAPSED_WIDTH_PX = 64;
export const LEFT_SIDEBAR_COLLAPSED_WIDTH_PX = 64;
export const RIGHT_SIDEBAR_INSET_CSS_VAR = '--right-sidebar-inset';
/** Доп. padding контента только при margin-right:-64px у main (свёрнутый rail). */
export const RIGHT_SIDEBAR_RAIL_OVERLAP_CSS_VAR = '--right-sidebar-rail-overlap';
/** Доп. padding контента только при margin-left:-64px у main (свёрнутый left rail). */
export const LEFT_SIDEBAR_RAIL_OVERLAP_CSS_VAR = '--left-sidebar-rail-overlap';

export function getRightSidebarInsetPx(
  open: boolean,
  hidden: boolean,
  expandedWidthPx: number = RIGHT_SIDEBAR_DEFAULT_WIDTH_PX,
): number {
  if (hidden) return 0;
  return open ? clampRightSidebarWidthPx(expandedWidthPx) : RIGHT_SIDEBAR_COLLAPSED_WIDTH_PX;
}

/** Перекрытие main под свёрнутым rail — не путать с полной шириной панели. */
export function getRightSidebarRailOverlapPx(open: boolean, hidden: boolean): number {
  if (hidden || open) return 0;
  return RIGHT_SIDEBAR_COLLAPSED_WIDTH_PX;
}

export function getLeftSidebarRailOverlapPx(open: boolean, hidden: boolean): number {
  if (hidden || open) return 0;
  return LEFT_SIDEBAR_COLLAPSED_WIDTH_PX;
}

/** Публикует ширину правой панели в CSS-переменную для fixed-элементов. */
export function useRightSidebarInsetCssVar(
  open: boolean,
  hidden: boolean,
  expandedWidthPx: number = RIGHT_SIDEBAR_DEFAULT_WIDTH_PX,
) {
  useEffect(() => {
    const root = document.documentElement;
    root.style.setProperty(
      RIGHT_SIDEBAR_INSET_CSS_VAR,
      `${getRightSidebarInsetPx(open, hidden, expandedWidthPx)}px`,
    );
    root.style.setProperty(
      RIGHT_SIDEBAR_RAIL_OVERLAP_CSS_VAR,
      `${getRightSidebarRailOverlapPx(open, hidden)}px`,
    );
    return () => {
      root.style.setProperty(RIGHT_SIDEBAR_INSET_CSS_VAR, '0px');
      root.style.setProperty(RIGHT_SIDEBAR_RAIL_OVERLAP_CSS_VAR, '0px');
    };
  }, [open, hidden, expandedWidthPx]);
}

/** Публикует overlap левого свёрнутого rail (main margin-left:-64px). */
export function useLeftSidebarRailOverlapCssVar(open: boolean, hidden: boolean) {
  useEffect(() => {
    const root = document.documentElement;
    root.style.setProperty(
      LEFT_SIDEBAR_RAIL_OVERLAP_CSS_VAR,
      `${getLeftSidebarRailOverlapPx(open, hidden)}px`,
    );
    return () => {
      root.style.setProperty(LEFT_SIDEBAR_RAIL_OVERLAP_CSS_VAR, '0px');
    };
  }, [open, hidden]);
}

function readDockedRightRailWidthPx(): number {
  const el = (document.querySelector('.astra-right-rail') ||
    document.querySelector('.MuiDrawer-docked.MuiDrawer-anchorRight .MuiDrawer-paper')) as HTMLElement | null;
  if (!el) return 0;
  const width = el.getBoundingClientRect().width;
  return width > 8 ? Math.round(width) : 0;
}

/** Живая ширина правой docked-панели — для position:fixed виджетов. */
export function useRightRailOffsetPx(): number {
  const [width, setWidth] = useState(0);

  useEffect(() => {
    const apply = () => setWidth(readDockedRightRailWidthPx());
    apply();

    const railRo = new ResizeObserver(apply);
    const watchPaper = () => {
      railRo.disconnect();
      const el =
        document.querySelector('.astra-right-rail') ||
        document.querySelector('.MuiDrawer-docked.MuiDrawer-anchorRight .MuiDrawer-paper');
      if (el) railRo.observe(el);
    };
    watchPaper();

    const mo = new MutationObserver(() => {
      apply();
      watchPaper();
    });
    mo.observe(document.body, { childList: true, subtree: true });
    window.addEventListener('resize', apply);
    return () => {
      railRo.disconnect();
      mo.disconnect();
      window.removeEventListener('resize', apply);
    };
  }, []);

  return width;
}
