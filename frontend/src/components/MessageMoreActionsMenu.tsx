import React, { useMemo, useState } from 'react';
import { Box, IconButton, Popover, Tooltip, Typography } from '@mui/material';
import {
  MoreHoriz as MoreHorizIcon,
  Edit as EditIcon,
  VolumeUp as VolumeUpIcon,
  PictureAsPdf as PdfIcon,
  Description as WordIcon,
  IosShare as ExportIcon,
  ChevronRight as ChevronRightIcon,
} from '@mui/icons-material';
import type { SxProps, Theme } from '@mui/material/styles';
import SplitArrowIcon from '../icons/SplitArrowIcon';
import {
  getDropdownPanelSx,
  getDropdownItemSx,
  getMenuColors,
  MENU_ACTION_TEXT_SIZE,
} from '../constants/menuStyles';
import type { MessageExportFormat } from '../utils/exportMessageContent';

const MAIN_PANEL_W = 220;
const EXPORT_PANEL_W = 200;

export interface MessageMoreActionsMenuProps {
  isDarkMode: boolean;
  compact?: boolean;
  disabled?: boolean;
  isSpeaking?: boolean;
  branchDisabled?: boolean;
  showBranch?: boolean;
  /** Показывать пункт экспорта ответа (PDF / Word). */
  showExport?: boolean;
  exportDisabled?: boolean;
  onEdit: () => void;
  onReadAloud: () => void;
  onBranch?: () => void;
  onExport?: (format: MessageExportFormat) => void;
  iconSx?: SxProps<Theme>;
}

export default function MessageMoreActionsMenu({
  isDarkMode,
  compact = false,
  disabled = false,
  isSpeaking = false,
  branchDisabled = false,
  showBranch = false,
  showExport = false,
  exportDisabled = false,
  onEdit,
  onReadAloud,
  onBranch,
  onExport,
  iconSx,
}: MessageMoreActionsMenuProps) {
  const [anchorEl, setAnchorEl] = useState<null | HTMLElement>(null);
  const [exportSubmenuOpen, setExportSubmenuOpen] = useState(false);
  const open = Boolean(anchorEl);

  const { menuItemColor, menuItemHover } = getMenuColors(isDarkMode);
  const windowSx = useMemo(() => ({ ...getDropdownPanelSx(isDarkMode) }) as Record<string, unknown>, [isDarkMode]);
  const dropdownItemSx = useMemo(() => getDropdownItemSx(isDarkMode), [isDarkMode]);
  const iconColor = isDarkMode ? 'rgba(255,255,255,0.7)' : 'rgba(0,0,0,0.6)';
  const mutedTextColor = isDarkMode ? 'rgba(255,255,255,0.9)' : 'rgba(0,0,0,0.85)';
  const subtleColor = isDarkMode ? 'rgba(255,255,255,0.5)' : 'rgba(0,0,0,0.5)';

  const defaultIconSx: SxProps<Theme> = {
    opacity: 0.7,
    p: 0.5,
    borderRadius: '6px',
    minWidth: compact ? '26px' : '28px',
    width: compact ? '26px' : '28px',
    height: compact ? '26px' : '28px',
    '&:hover:not(:disabled)': { opacity: 1, '& .MuiSvgIcon-root': { color: 'primary.main' } },
    '&:disabled': { opacity: 0.4 },
    '& .MuiSvgIcon-root': {
      fontSize: compact ? '16px !important' : '18px !important',
      width: compact ? '16px !important' : '18px !important',
      height: compact ? '16px !important' : '18px !important',
    },
  };

  const close = () => {
    setAnchorEl(null);
    setExportSubmenuOpen(false);
  };

  const handleEdit = () => {
    close();
    onEdit();
  };

  const handleReadAloud = () => {
    close();
    onReadAloud();
  };

  const handleBranch = () => {
    close();
    onBranch?.();
  };

  const handleExport = (format: MessageExportFormat) => {
    close();
    onExport?.(format);
  };

  const entrySx = (active = false, disabledEntry = false) => ({
    ...dropdownItemSx,
    display: 'flex',
    alignItems: 'center',
    gap: 1,
    color: active ? menuItemColor : mutedTextColor,
    fontWeight: active ? 600 : 400,
    bgcolor: active ? menuItemHover : 'transparent',
    opacity: disabledEntry ? 0.45 : 1,
    cursor: disabledEntry ? 'default' : 'pointer',
    pointerEvents: disabledEntry ? ('none' as const) : ('auto' as const),
  });

  // Как у AgentSelector: прозрачный paper, стилизуется каждая карточка.
  const paperSx = {
    mt: 0.75,
    p: 0,
    overflow: 'visible',
    background: 'transparent !important',
    backgroundColor: 'transparent !important',
    boxShadow: 'none !important',
    backdropFilter: 'none',
    border: 'none',
    maxWidth: '96vw',
  };

  return (
    <>
      <Tooltip title="Больше действий">
        <span>
          <IconButton
            size="small"
            disabled={disabled}
            onClick={(e) => setAnchorEl(e.currentTarget)}
            className="message-more-actions-button"
            data-theme={isDarkMode ? 'dark' : 'light'}
            sx={iconSx ?? defaultIconSx}
            aria-label="Больше действий"
          >
            <MoreHorizIcon />
          </IconButton>
        </span>
      </Tooltip>
      <Popover
        open={open}
        anchorEl={anchorEl}
        onClose={close}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
        transformOrigin={{ vertical: 'top', horizontal: 'left' }}
        slotProps={{
          paper: { sx: paperSx },
        }}
      >
        <Box
          onMouseLeave={() => setExportSubmenuOpen(false)}
          sx={{ display: 'flex', flexDirection: 'row', alignItems: 'flex-start', gap: '6px' }}
        >
          <Box
            sx={{
              ...windowSx,
              width: MAIN_PANEL_W,
              flexShrink: 0,
              display: 'flex',
              flexDirection: 'column',
            }}
          >
            <Box sx={{ py: 0.5, px: 0.5 }}>
              {showExport ? (
                <Box
                  onMouseEnter={() => {
                    if (!exportDisabled && onExport) setExportSubmenuOpen(true);
                  }}
                  sx={entrySx(exportSubmenuOpen, exportDisabled || !onExport)}
                >
                  <ExportIcon sx={{ fontSize: 18, color: iconColor, flexShrink: 0 }} />
                  <Typography
                    sx={{
                      flex: 1,
                      minWidth: 0,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                      fontSize: MENU_ACTION_TEXT_SIZE,
                    }}
                  >
                    Экспорт
                  </Typography>
                  <ChevronRightIcon
                    sx={{
                      fontSize: 18,
                      color: subtleColor,
                      flexShrink: 0,
                      transform: exportSubmenuOpen ? 'rotate(90deg)' : 'none',
                      transition: 'transform 0.15s',
                    }}
                  />
                </Box>
              ) : null}

              <Box
                onMouseEnter={() => setExportSubmenuOpen(false)}
                onClick={isSpeaking ? undefined : handleReadAloud}
                sx={entrySx(false, isSpeaking)}
              >
                <VolumeUpIcon sx={{ fontSize: 18, color: iconColor, flexShrink: 0 }} />
                <Typography sx={{ flex: 1, fontSize: MENU_ACTION_TEXT_SIZE }}>Прочесть вслух</Typography>
              </Box>

              <Box
                onMouseEnter={() => setExportSubmenuOpen(false)}
                onClick={handleEdit}
                sx={entrySx()}
              >
                <EditIcon sx={{ fontSize: 18, color: iconColor, flexShrink: 0 }} />
                <Typography sx={{ flex: 1, fontSize: MENU_ACTION_TEXT_SIZE }}>Редактировать</Typography>
              </Box>

              {showBranch ? (
                <Box
                  onMouseEnter={() => setExportSubmenuOpen(false)}
                  onClick={branchDisabled ? undefined : handleBranch}
                  sx={entrySx(false, branchDisabled)}
                >
                  <Box component="span" sx={{ display: 'inline-flex', color: iconColor, flexShrink: 0 }}>
                    <SplitArrowIcon sx={{ fontSize: 18 }} />
                  </Box>
                  <Typography sx={{ flex: 1, fontSize: MENU_ACTION_TEXT_SIZE }}>Ветка в новом чате</Typography>
                </Box>
              ) : null}
            </Box>
          </Box>

          {showExport && exportSubmenuOpen ? (
            <Box
              sx={{
                ...windowSx,
                width: EXPORT_PANEL_W,
                display: 'flex',
                flexDirection: 'column',
              }}
            >
              <Box sx={{ py: 0.5, px: 0.5 }}>
                <Box
                  onClick={() => handleExport('pdf')}
                  sx={{
                    ...dropdownItemSx,
                    display: 'flex',
                    alignItems: 'center',
                    gap: 1,
                    color: mutedTextColor,
                  }}
                >
                  <PdfIcon sx={{ fontSize: 18, color: '#d94a3a', flexShrink: 0 }} />
                  <Typography
                    sx={{
                      flex: 1,
                      minWidth: 0,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                      fontSize: MENU_ACTION_TEXT_SIZE,
                    }}
                  >
                    PDF
                  </Typography>
                </Box>
                <Box
                  onClick={() => handleExport('word')}
                  sx={{
                    ...dropdownItemSx,
                    display: 'flex',
                    alignItems: 'center',
                    gap: 1,
                    color: mutedTextColor,
                  }}
                >
                  <WordIcon sx={{ fontSize: 18, color: '#2b579a', flexShrink: 0 }} />
                  <Typography
                    sx={{
                      flex: 1,
                      minWidth: 0,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                      fontSize: MENU_ACTION_TEXT_SIZE,
                    }}
                  >
                    Word
                  </Typography>
                </Box>
              </Box>
            </Box>
          ) : null}
        </Box>
      </Popover>
    </>
  );
}
