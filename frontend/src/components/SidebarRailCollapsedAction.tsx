import React from 'react';
import { Box, ListItem, ListItemButton, Tooltip, Typography } from '@mui/material';
import type { SxProps, Theme } from '@mui/material/styles';
import {
  getSidebarRailCollapsedListItemButtonSx,
} from '../constants/menuStyles';
import {
  getSidebarRailCollapsedIconHoverSx,
  getSidebarRailCollapsedItemEnterSx,
  SIDEBAR_RAIL_ICON_CLASS,
  type SidebarRailCollapsedActionVariant,
} from '../constants/sidebarRailAnimations';

interface SidebarRailCollapsedActionProps {
  variant: SidebarRailCollapsedActionVariant;
  /** Светлый фон боковой панели (контраст иконок/hover). */
  panelIsLight: boolean;
  enterIndex: number;
  onClick: () => void;
  icon: React.ReactElement;
  tooltipTitle: React.ReactNode;
  active?: boolean;
}

export default function SidebarRailCollapsedAction({
  variant,
  panelIsLight,
  enterIndex,
  onClick,
  icon,
  tooltipTitle,
  active = false,
}: SidebarRailCollapsedActionProps) {
  return (
    <ListItem
      disablePadding
      sx={{ mb: 0.5, display: 'block', ...getSidebarRailCollapsedItemEnterSx(enterIndex) }}
    >
      <Tooltip placement="right" title={tooltipTitle}>
        <Box component="span" sx={{ display: 'flex', width: '100%', justifyContent: 'center' }}>
          <ListItemButton
            onClick={onClick}
            sx={[
              getSidebarRailCollapsedListItemButtonSx(panelIsLight),
              getSidebarRailCollapsedIconHoverSx(variant),
              active ? { bgcolor: panelIsLight ? 'rgba(0,0,0,0.08)' : 'rgba(255,255,255,0.15)' } : {},
            ] as SxProps<Theme>}
          >
            <Box
              className={SIDEBAR_RAIL_ICON_CLASS}
              sx={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', lineHeight: 0 }}
            >
              {icon}
            </Box>
          </ListItemButton>
        </Box>
      </Tooltip>
    </ListItem>
  );
}

/** Tooltip с заголовком и горячей клавишей (как у раскрытого сайдбара). */
export function SidebarRailShortcutTooltip({
  title,
  shortcut,
}: {
  title: string;
  shortcut?: string;
}) {
  if (!shortcut) {
    return <Typography variant="body2">{title}</Typography>;
  }
  return (
    <Box>
      <Typography variant="body2" component="span" display="block">
        {title}
      </Typography>
      <Typography variant="caption" sx={{ opacity: 0.85, display: 'block', mt: 0.25 }}>
        {shortcut}
      </Typography>
    </Box>
  );
}