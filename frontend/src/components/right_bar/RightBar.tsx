import React, { startTransition, useCallback, useEffect, useMemo, useState } from 'react';
import {
  Box,
  Drawer,
  IconButton,
  List,
  ListItem,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Tooltip,
} from '@mui/material';
import {
  ChevronLeft as ChevronLeftIcon,
  ChevronRight as ChevronRightIcon,
  PushPin as PushPinIcon,
  PushPinOutlined as PushPinOutlinedIcon,
} from '@mui/icons-material';
import SkillsNavIcon from '../../icons/SkillsIcon';
import {
  SIDEBAR_CHAT_ROW_LIST_ITEM_BUTTON_SX,
  SIDEBAR_HIDE_SCROLLBAR_SX,
  SIDEBAR_LIST_ICON_SX,
  SIDEBAR_LIST_ICON_TO_TEXT_GAP_PX,
  getSidebarRailCollapsedListItemButtonSx,
} from '../../constants/menuStyles';
import {
  getSidebarChromeSx,
  getSidebarForcedContrastSx,
  getSidebarPanelBackground,
  isSidebarPanelLight,
} from '../../constants/sidebarPanelColor';
import {
  SidebarRailAgentIcon,
  SidebarRailTranscribeIcon,
} from '../../constants/sidebarRailIcons';
import {
  ASTRA_OPEN_AGENT_CONSTRUCTOR,
  ASTRA_OPEN_SKILLS_SIDEBAR,
  ASTRA_OPEN_TRANSCRIPTION_SIDEBAR,
} from '../../constants/hotkeys';
import { usePendingAgentConstructorOpen } from '../../hooks/usePendingAgentConstructorOpen';
import { usePendingSkillSidebarOpen } from '../../hooks/usePendingSkillSidebarOpen';
import AgentConstructorPanel from './AgentConstructorPanel';
import GalleryNavButton from './GalleryNavButton';
import SkillsSidebarPanel from './SkillsSidebarPanel';
import SidebarRailMenuGlyph from '../../icons/SidebarRailMenuGlyph';
import TranscriptionSidebarSection from './TranscriptionSidebarSection';
import RightSidebarResizeHandle from './RightSidebarResizeHandle';
import { clampRightSidebarWidthPx } from '../../hooks/useRightSidebarWidth';
import { RIGHT_SIDEBAR_COLLAPSED_WIDTH_PX } from '../../hooks/useRightSidebarInsetCssVar';

export interface RightBarProps {
  open: boolean;
  hidden: boolean;
  expandedWidthPx: number;
  widthPinned: boolean;
  isDarkMode: boolean;
  onToggleOpen: () => void;
  onHide: () => void;
  onShow: () => void;
  setOpen: (open: boolean | ((prev: boolean) => boolean)) => void;
  setHidden: (hidden: boolean | ((prev: boolean) => boolean)) => void;
  setExpandedWidthPx: (widthPx: number | ((prev: number) => number)) => void;
  setWidthPinned: (pinned: boolean | ((prev: boolean) => boolean)) => void;
}

export default function RightBar({
  open,
  hidden,
  expandedWidthPx,
  widthPinned,
  isDarkMode,
  onToggleOpen,
  onHide,
  onShow,
  setOpen,
  setHidden,
  setExpandedWidthPx,
  setWidthPinned,
}: RightBarProps) {
  const [sidebarColorTick, setSidebarColorTick] = useState(0);
  const panelBg = useMemo(
    () => getSidebarPanelBackground(isDarkMode),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [isDarkMode, sidebarColorTick],
  );
  const [transcriptionMenuOpen, setTranscriptionMenuOpen] = useState(false);
  const [skillsPanelOpen, setSkillsPanelOpen] = useState(false);
  const [agentConstructorOpen, setAgentConstructorOpen] = useState(false);
  const [isResizing, setIsResizing] = useState(false);

  const drawerWidth = open ? clampRightSidebarWidthPx(expandedWidthPx) : RIGHT_SIDEBAR_COLLAPSED_WIDTH_PX;

  useEffect(() => {
    const raw = (localStorage.getItem('sidebar_panel_color') || '').trim();
    if (raw && (raw.toLowerCase() === '#f7f7f9' || raw.toLowerCase() === '#212128')) {
      localStorage.setItem('sidebar_panel_color', '');
      setSidebarColorTick((n) => n + 1);
    }
    const sync = () => setSidebarColorTick((n) => n + 1);
    window.addEventListener('sidebarColorChanged', sync);
    window.addEventListener('storage', sync);
    return () => {
      window.removeEventListener('sidebarColorChanged', sync);
      window.removeEventListener('storage', sync);
    };
  }, []);

  const openConstructorSidebar = useCallback(() => {
    startTransition(() => {
      setHidden(false);
      setOpen(true);
      setSkillsPanelOpen(false);
      setAgentConstructorOpen(true);
    });
  }, [setHidden, setOpen]);

  const openSkillsSidebar = useCallback(() => {
    startTransition(() => {
      setHidden(false);
      setOpen(true);
      setAgentConstructorOpen(false);
      setSkillsPanelOpen(true);
    });
  }, [setHidden, setOpen]);

  usePendingAgentConstructorOpen(openConstructorSidebar);
  usePendingSkillSidebarOpen(openSkillsSidebar);

  useEffect(() => {
    const onAgent = () => openConstructorSidebar();
    const onSkills = () => openSkillsSidebar();
    const onTranscription = () => {
      startTransition(() => {
        setHidden(false);
        setOpen(true);
        setTranscriptionMenuOpen(true);
      });
    };
    window.addEventListener(ASTRA_OPEN_AGENT_CONSTRUCTOR, onAgent);
    window.addEventListener(ASTRA_OPEN_SKILLS_SIDEBAR, onSkills);
    window.addEventListener(ASTRA_OPEN_TRANSCRIPTION_SIDEBAR, onTranscription);
    return () => {
      window.removeEventListener(ASTRA_OPEN_AGENT_CONSTRUCTOR, onAgent);
      window.removeEventListener(ASTRA_OPEN_SKILLS_SIDEBAR, onSkills);
      window.removeEventListener(ASTRA_OPEN_TRANSCRIPTION_SIDEBAR, onTranscription);
    };
  }, [openConstructorSidebar, openSkillsSidebar, setHidden, setOpen]);

  const panelIsLight = isSidebarPanelLight(panelBg);

  const handleResize = useCallback(
    (widthPx: number) => {
      setExpandedWidthPx(clampRightSidebarWidthPx(widthPx));
    },
    [setExpandedWidthPx],
  );

  const handleResizeEnd = useCallback(
    (widthPx: number) => {
      setExpandedWidthPx(clampRightSidebarWidthPx(widthPx));
      setIsResizing(false);
    },
    [setExpandedWidthPx],
  );

  const toggleWidthPinned = useCallback(() => {
    setWidthPinned((prev) => !prev);
  }, [setWidthPinned]);

  return (
    <>
      {!hidden && (
        <Drawer
          variant="persistent"
          anchor="right"
          open={true}
          slotProps={{ paper: { className: 'astra-right-rail' } }}
          sx={{
            width: drawerWidth,
            flexShrink: 0,
            transition: isResizing ? 'none' : 'width 0.3s ease',
            '& .MuiDrawer-paper': {
              width: drawerWidth,
              boxSizing: 'border-box',
              position: 'relative',
              ...getSidebarChromeSx(panelBg),
              ...getSidebarForcedContrastSx(panelBg),
              borderLeft: '1px solid var(--sidebar-border-color, rgba(255,255,255,0.08))',
              transition: isResizing
                ? 'none'
                : 'width 0.3s ease, background 0.3s ease, color 0.3s ease',
              overflowX: 'hidden',
              overflowY: 'auto',
              display: 'flex',
              flexDirection: 'column',
              ...SIDEBAR_HIDE_SCROLLBAR_SX,
            },
          }}
        >
          {open && (
            <RightSidebarResizeHandle
              disabled={widthPinned}
              onResize={handleResize}
              onResizeEnd={handleResizeEnd}
              onResizeStart={() => setIsResizing(true)}
            />
          )}
          {!open && (
            <>
              <Box
                sx={{
                  px: 1,
                  py: 1.5,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  minHeight: 64,
                  boxSizing: 'border-box',
                }}
              >
                <Tooltip title="Открыть панель" placement="left">
                  <IconButton
                    onClick={onToggleOpen}
                    sx={{
                      color: 'white',
                      opacity: 1,
                      width: 40,
                      height: 40,
                      borderRadius: 1,
                      p: 0,
                      '&:hover': {
                        backgroundColor: isDarkMode ? 'rgba(255, 255, 255, 0.1)' : 'rgba(0, 0, 0, 0.1)',
                        opacity: 1,
                      },
                    }}
                  >
                    <SidebarRailMenuGlyph side="right" />
                  </IconButton>
                </Tooltip>
              </Box>
              <List disablePadding sx={{ px: 1, pt: 0, pb: 1, width: '100%', boxSizing: 'border-box' }}>
                <ListItem disablePadding sx={{ mb: 0.5, display: 'block' }}>
                  <Tooltip title="Транскрибация" placement="left">
                    <Box component="span" sx={{ display: 'flex', width: '100%', justifyContent: 'center' }}>
                      <ListItemButton
                        onClick={() => {
                          setOpen(true);
                          setTranscriptionMenuOpen(true);
                        }}
                        sx={getSidebarRailCollapsedListItemButtonSx(panelIsLight)}
                      >
                        <SidebarRailTranscribeIcon sx={SIDEBAR_LIST_ICON_SX} />
                      </ListItemButton>
                    </Box>
                  </Tooltip>
                </ListItem>
                <GalleryNavButton variant="collapsed" isDarkMode={isDarkMode} panelIsLight={panelIsLight} />
                <ListItem disablePadding sx={{ mb: 0.5, display: 'block' }}>
                  <Tooltip title="Skills" placement="left">
                    <Box component="span" sx={{ display: 'flex', width: '100%', justifyContent: 'center' }}>
                      <ListItemButton
                        onClick={openSkillsSidebar}
                        selected={skillsPanelOpen}
                        sx={getSidebarRailCollapsedListItemButtonSx(panelIsLight)}
                      >
                        <SkillsNavIcon sx={SIDEBAR_LIST_ICON_SX} />
                      </ListItemButton>
                    </Box>
                  </Tooltip>
                </ListItem>
                <ListItem disablePadding sx={{ mb: 0.5, display: 'block' }}>
                  <Tooltip title="Конструктор агента" placement="left">
                    <Box component="span" sx={{ display: 'flex', width: '100%', justifyContent: 'center' }}>
                      <ListItemButton
                        onClick={() => {
                          setOpen(true);
                          setSkillsPanelOpen(false);
                          setAgentConstructorOpen(true);
                        }}
                        sx={getSidebarRailCollapsedListItemButtonSx(panelIsLight)}
                      >
                        <SidebarRailAgentIcon sx={SIDEBAR_LIST_ICON_SX} />
                      </ListItemButton>
                    </Box>
                  </Tooltip>
                </ListItem>
              </List>
              <Box
                sx={{
                  position: 'fixed',
                  right: 0,
                  top: '50%',
                  transform: 'translateY(-50%)',
                  width: RIGHT_SIDEBAR_COLLAPSED_WIDTH_PX,
                  display: 'flex',
                  justifyContent: 'center',
                  zIndex: 1300,
                }}
              >
                <Tooltip title="Скрыть панель" placement="left">
                  <IconButton
                    onClick={onHide}
                    sx={{
                      color: 'white',
                      opacity: 1,
                      width: 40,
                      height: 40,
                      borderRadius: 1,
                      '&:hover': {
                        backgroundColor: isDarkMode ? 'rgba(255, 255, 255, 0.1)' : 'rgba(0, 0, 0, 0.1)',
                        opacity: 1,
                      },
                    }}
                  >
                    <ChevronRightIcon />
                  </IconButton>
                </Tooltip>
              </Box>
            </>
          )}

          {open && (
            <Box
              sx={{
                display: 'flex',
                flexDirection: 'column',
                height: '100%',
                overflow: 'hidden',
                width: '100%',
                minWidth: 0,
                boxSizing: 'border-box',
              }}
            >
              <Box
                sx={{
                  px: 2,
                  py: 1.5,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  gap: 1,
                  minHeight: 64,
                  flexShrink: 0,
                  boxSizing: 'border-box',
                }}
              >
                <Tooltip title="Свернуть панель" placement="left">
                  <IconButton
                    onClick={onToggleOpen}
                    sx={{
                      color: 'white',
                      opacity: 1,
                      width: 40,
                      height: 40,
                      borderRadius: 1,
                      p: 0,
                      '&:hover': {
                        backgroundColor: isDarkMode ? 'rgba(255, 255, 255, 0.1)' : 'rgba(0, 0, 0, 0.1)',
                        opacity: 1,
                      },
                    }}
                  >
                    <SidebarRailMenuGlyph side="right" />
                  </IconButton>
                </Tooltip>
                <Tooltip
                  title={
                    widthPinned
                      ? 'Ширина закреплена — нажмите, чтобы изменить'
                      : 'Закрепить текущую ширину панели'
                  }
                  placement="left"
                >
                  <IconButton
                    onClick={toggleWidthPinned}
                    aria-pressed={widthPinned}
                    sx={{
                      color: 'white',
                      opacity: widthPinned ? 1 : 0.85,
                      width: 40,
                      height: 40,
                      borderRadius: 1,
                      p: 0,
                      '&:hover': {
                        backgroundColor: isDarkMode ? 'rgba(255, 255, 255, 0.1)' : 'rgba(0, 0, 0, 0.1)',
                        opacity: 1,
                      },
                    }}
                  >
                    {widthPinned ? <PushPinIcon fontSize="small" /> : <PushPinOutlinedIcon fontSize="small" />}
                  </IconButton>
                </Tooltip>
              </Box>
              <List sx={{ py: 0, px: 1, flexShrink: 0 }}>
                <ListItem disablePadding sx={{ mb: 0.5 }}>
                  <ListItemButton
                    onClick={() => setTranscriptionMenuOpen((prev) => !prev)}
                    sx={{
                      ...SIDEBAR_CHAT_ROW_LIST_ITEM_BUTTON_SX,
                      color: 'white',
                      backgroundColor: transcriptionMenuOpen ? 'rgba(255,255,255,0.15)' : 'transparent',
                      '&:hover': {
                        backgroundColor: transcriptionMenuOpen
                          ? 'rgba(255,255,255,0.2)'
                          : 'rgba(255,255,255,0.08)',
                      },
                    }}
                  >
                    <ListItemIcon
                      sx={{
                        color: '#ffffff',
                        minWidth: 40,
                        mr: `${SIDEBAR_LIST_ICON_TO_TEXT_GAP_PX}px`,
                        '& .MuiSvgIcon-root': { fontSize: '1.375rem' },
                      }}
                    >
                      <SidebarRailTranscribeIcon />
                    </ListItemIcon>
                    <ListItemText
                      primary="Транскрибация"
                      primaryTypographyProps={{
                        sx: { fontSize: '0.8rem', fontWeight: 400, color: '#ffffff' },
                      }}
                    />
                  </ListItemButton>
                </ListItem>
                <TranscriptionSidebarSection open={transcriptionMenuOpen} />
                <GalleryNavButton variant="expanded" isDarkMode={isDarkMode} panelIsLight={panelIsLight} />
                <ListItem disablePadding sx={{ mb: 0.5 }}>
                  <ListItemButton
                    onClick={() => {
                      setSkillsPanelOpen((prev) => {
                        const next = !prev;
                        if (next) setAgentConstructorOpen(false);
                        return next;
                      });
                    }}
                    sx={{
                      ...SIDEBAR_CHAT_ROW_LIST_ITEM_BUTTON_SX,
                      color: 'white',
                      backgroundColor: skillsPanelOpen ? 'rgba(255,255,255,0.15)' : 'transparent',
                      '&:hover': {
                        backgroundColor: skillsPanelOpen
                          ? 'rgba(255,255,255,0.2)'
                          : 'rgba(255,255,255,0.08)',
                      },
                    }}
                  >
                    <ListItemIcon
                      sx={{
                        color: '#ffffff',
                        minWidth: 40,
                        mr: `${SIDEBAR_LIST_ICON_TO_TEXT_GAP_PX}px`,
                        '& .MuiSvgIcon-root': { fontSize: '1.375rem' },
                      }}
                    >
                      <SkillsNavIcon />
                    </ListItemIcon>
                    <ListItemText
                      primary="Skills"
                      primaryTypographyProps={{
                        sx: { fontSize: '0.8rem', fontWeight: 400, color: '#ffffff' },
                      }}
                    />
                  </ListItemButton>
                </ListItem>
              </List>
              {skillsPanelOpen && (
                <Box
                  sx={{
                    flex: 1,
                    minHeight: 0,
                    overflow: 'hidden',
                    display: 'flex',
                    flexDirection: 'column',
                    borderTop: '1px solid rgba(255,255,255,0.08)',
                  }}
                >
                  <SkillsSidebarPanel isOpen={true} />
                </Box>
              )}
              <List sx={{ py: 0, px: open ? 1 : 0.5 }}>
                <ListItem disablePadding sx={{ mb: 0.5 }}>
                  <ListItemButton
                    onClick={() => {
                      setAgentConstructorOpen((prev) => {
                        const next = !prev;
                        if (next) setSkillsPanelOpen(false);
                        return next;
                      });
                    }}
                    sx={{
                      ...SIDEBAR_CHAT_ROW_LIST_ITEM_BUTTON_SX,
                      color: 'white',
                      backgroundColor: agentConstructorOpen ? 'rgba(255,255,255,0.15)' : 'transparent',
                      '&:hover': {
                        backgroundColor: agentConstructorOpen
                          ? 'rgba(255,255,255,0.2)'
                          : 'rgba(255,255,255,0.08)',
                      },
                    }}
                  >
                    <ListItemIcon
                      sx={{
                        color: '#ffffff',
                        minWidth: 40,
                        mr: `${SIDEBAR_LIST_ICON_TO_TEXT_GAP_PX}px`,
                        '& .MuiSvgIcon-root': { fontSize: '1.375rem' },
                      }}
                    >
                      <SidebarRailAgentIcon />
                    </ListItemIcon>
                    <ListItemText
                      primary="Конструктор агента"
                      primaryTypographyProps={{
                        sx: { fontSize: '0.8rem', fontWeight: 400, color: '#ffffff' },
                      }}
                    />
                  </ListItemButton>
                </ListItem>
              </List>
              {agentConstructorOpen && (
                <Box
                  sx={{
                    flex: 1,
                    minHeight: 0,
                    overflow: 'hidden',
                    display: 'flex',
                    flexDirection: 'column',
                    borderTop: '1px solid rgba(255,255,255,0.08)',
                  }}
                >
                  <AgentConstructorPanel isDarkMode={isDarkMode} isOpen={true} />
                </Box>
              )}
            </Box>
          )}
        </Drawer>
      )}

      {hidden && (
        <Box
          sx={{
            position: 'fixed',
            right: 0,
            top: '50%',
            transform: 'translateY(-50%)',
            zIndex: 1200,
          }}
        >
          <Tooltip title="Показать панель" placement="left">
            <IconButton
              onClick={onShow}
              sx={{
                bgcolor: 'transparent',
                color: 'text.primary',
                opacity: 0.7,
                '&:hover': {
                  bgcolor: 'transparent',
                  opacity: 1,
                },
              }}
            >
              <ChevronLeftIcon />
            </IconButton>
          </Tooltip>
        </Box>
      )}
    </>
  );
}
