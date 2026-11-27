import { keyframes } from '@mui/material/styles';

/** Появление кнопки в узком rail (лёгкий сдвиг + fade). */
export const sidebarRailItemEnter = keyframes`
  from {
    opacity: 0;
    transform: translateX(-6px);
  }
  to {
    opacity: 1;
    transform: translateX(0);
  }
`;

/** «Новый чат»: плюс поворачивается на 90°. */
export const sidebarRailNewChatIconHover = keyframes`
  0% {
    transform: rotate(0deg) scale(1);
  }
  55% {
    transform: rotate(90deg) scale(1.12);
  }
  100% {
    transform: rotate(90deg) scale(1.06);
  }
`;

/** «Поиск»: лёгкий zoom + микропульс. */
export const sidebarRailSearchIconHover = keyframes`
  0% {
    transform: scale(1);
    opacity: 0.92;
  }
  45% {
    transform: scale(1.18);
    opacity: 1;
  }
  100% {
    transform: scale(1.08);
    opacity: 1;
  }
`;

/** «Моё творение»: мягкий подпрыг. */
export const sidebarRailCreationsIconHover = keyframes`
  0% {
    transform: translateY(0) scale(1);
  }
  35% {
    transform: translateY(-3px) scale(1.1);
  }
  70% {
    transform: translateY(1px) scale(1.04);
  }
  100% {
    transform: translateY(0) scale(1.06);
  }
`;

export type SidebarRailCollapsedActionVariant = 'newChat' | 'search' | 'creations';

const ICON_HOVER_ANIMATION: Record<SidebarRailCollapsedActionVariant, string> = {
  newChat: `${sidebarRailNewChatIconHover} 0.42s cubic-bezier(0.34, 1.2, 0.64, 1) forwards`,
  search: `${sidebarRailSearchIconHover} 0.38s cubic-bezier(0.34, 1.15, 0.64, 1) forwards`,
  creations: `${sidebarRailCreationsIconHover} 0.45s cubic-bezier(0.34, 1.25, 0.64, 1) forwards`,
};

/** Класс иконки внутри кнопки rail — для селектора :hover в sx кнопки. */
export const SIDEBAR_RAIL_ICON_CLASS = 'sidebar-rail-collapsed-icon';

export function getSidebarRailCollapsedIconHoverSx(
  variant: SidebarRailCollapsedActionVariant,
): Record<string, unknown> {
  return {
    [`& .${SIDEBAR_RAIL_ICON_CLASS}`]: {
      transition: 'transform 0.22s ease, opacity 0.22s ease',
      transformOrigin: 'center center',
      '@media (prefers-reduced-motion: reduce)': {
        transition: 'none',
        animation: 'none !important',
        transform: 'none !important',
      },
    },
    '@media (prefers-reduced-motion: no-preference)': {
      [`&:hover .${SIDEBAR_RAIL_ICON_CLASS}`]: {
        animation: ICON_HOVER_ANIMATION[variant],
      },
    },
    [`&:not(:hover) .${SIDEBAR_RAIL_ICON_CLASS}`]: {
      animation: 'none',
      transform: 'none',
    },
  };
}

export function getSidebarRailCollapsedItemEnterSx(index: number): Record<string, unknown> {
  return {
    '@media (prefers-reduced-motion: no-preference)': {
      animation: `${sidebarRailItemEnter} 0.32s cubic-bezier(0.22, 1, 0.36, 1) backwards`,
      animationDelay: `${index * 55}ms`,
    },
  };
}
