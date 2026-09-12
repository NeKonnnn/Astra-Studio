import React from 'react';
import { Box } from '@mui/material';
import {
  SIDEBAR_RAIL_MENU_TOGGLE_DATA_URL_LEFT,
  SIDEBAR_RAIL_MENU_TOGGLE_DATA_URL_RIGHT,
} from './sidebarMenuToggleIcon';
import { SIDEBAR_RAIL_MENU_TOGGLE_GLYPH_SIZE } from '../constants/menuStyles';

type Props = {
  side: 'left' | 'right';
  className?: string;
};

/**
 * Кнопка меню rail: `<img>` + data URL (надёжнее `background-image` для data:).
 * Общий для left_bar и right_bar.
 */
export default function SidebarRailMenuGlyph({ side, className }: Props) {
  const src =
    side === 'left' ? SIDEBAR_RAIL_MENU_TOGGLE_DATA_URL_LEFT : SIDEBAR_RAIL_MENU_TOGGLE_DATA_URL_RIGHT;

  return (
    <Box
      className={className}
      component="img"
      src={src}
      alt=""
      draggable={false}
      data-memo-rail-menu-glyph
      sx={{
        width: SIDEBAR_RAIL_MENU_TOGGLE_GLYPH_SIZE,
        height: SIDEBAR_RAIL_MENU_TOGGLE_GLYPH_SIZE,
        display: 'block',
        flexShrink: 0,
        objectFit: 'contain',
      }}
    />
  );
}
