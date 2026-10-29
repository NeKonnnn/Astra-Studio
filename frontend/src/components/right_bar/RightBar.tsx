import React, { startTransition, useCallback, useEffect, useMemo, useState } from 'react';
import {
  Box,
  Divider,
  Drawer,
  IconButton,
  List,
  ListItem,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Tooltip,
  Typography,
} from '@mui/material';
import {
  Apps as AppsIcon,
  ChevronLeft as ChevronLeftIcon,
  ChevronRight as ChevronRightIcon,
  PushPin as PushPinIcon,
  PushPinOutlined as PushPinOutlinedIcon,
} from '@mui/icons-material';
import { useNavigate } from 'react-router-dom';
import SkillsNavIcon from '../../icons/SkillsIcon';
import KnowledgeBaseIcon from '../../icons/KnowledgeBaseIcon';
import {
  MENU_ACTION_TEXT_SIZE,
  SIDEBAR_CHAT_ROW_LIST_ITEM_BUTTON_SX,
  SIDEBAR_HIDE_SCROLLBAR_SX,
  SIDEBAR_LIST_ICON_SX,
  SIDEBAR_LIST_ICON_TO_TEXT_GAP_PX,
  getDropdownItemSx,
  getDropdownPanelSx,
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
  SidebarRailPromptsIcon,
  SidebarRailTranscribeIcon,
} from '../../constants/sidebarRailIcons';
import {
  ASTRA_OPEN_AGENT_CONSTRUCTOR,
  ASTRA_OPEN_KNOWLEDGE_BASES_SIDEBAR,
  ASTRA_OPEN_SKILLS_SIDEBAR,
  ASTRA_OPEN_TRANSCRIPTION_SIDEBAR,
} from '../../constants/hotkeys';
import { usePendingAgentConstructorOpen } from '../../hooks/usePendingAgentConstructorOpen';
import { usePendingKnowledgeBaseSidebarOpen } from '../../hooks/usePendingKnowledgeBaseSidebarOpen';
import { usePendingSkillSidebarOpen } from '../../hooks/usePendingSkillSidebarOpen';
import AgentConstructorPanel from './AgentConstructorPanel';
import GalleryNavButton from './GalleryNavButton';
import KnowledgeBasesSidebarPanel from './KnowledgeBasesSidebarPanel';
import SkillsSidebarPanel from './SkillsSidebarPanel';
import SidebarRailMenuGlyph from '../../icons/SidebarRailMenuGlyph';
import TranscriptionSidebarSection from './TranscriptionSidebarSection';
import RightSidebarResizeHandle from './RightSidebarResizeHandle';
import { clampRightSidebarWidthPx } from '../../hooks/useRightSidebarWidth';
import { RIGHT_SIDEBAR_COLLAPSED_WIDTH_PX } from '../../hooks/useRightSidebarInsetCssVar';

type RightBarSection = 'transcription' | 'skills' | 'knowledge' | 'agent';

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
  const navigate = useNavigate();
  const [transcriptionMenuOpen, setTranscriptionMenuOpen] = useState(false);
  const [skillsPanelOpen, setSkillsPanelOpen] = useState(false);
  const [knowledgeBasesPanelOpen, setKnowledgeBasesPanelOpen] = useState(false);
  const [agentConstructorOpen, setAgentConstructorOpen] = useState(false);
  const [isResizing, setIsResizing] = useState(false);
  const [sectionMenuAnchor, setSectionMenuAnchor] = useState<null | HTMLElement>(null);

  const drawerWidth = open ? clampRightSidebarWidthPx(expandedWidthPx) : RIGHT_SIDEBAR_COLLAPSED_WIDTH_PX;

  const activeSection: RightBarSection | null = agentConstructorOpen
    ? 'agent'
    : skillsPanelOpen
      ? 'skills'
      : knowledgeBasesPanelOpen
        ? 'knowledge'
        : transcriptionMenuOpen
          ? 'transcription'
          : null;

  const activeSectionLabel =
    activeSection === 'agent'
      ? 'Конструктор агента'
      : activeSection === 'skills'
        ? 'Skills'
        : activeSection === 'knowledge'
          ? 'Базы знаний'
          : activeSection === 'transcription'
            ? 'Транскрибация'
            : null;

  const closeAllSections = useCallback(() => {
    setTranscriptionMenuOpen(false);
    setSkillsPanelOpen(false);
    setKnowledgeBasesPanelOpen(false);
    setAgentConstructorOpen(false);
  }, []);

  const openSection = useCallback((section: RightBarSection) => {
    setTranscriptionMenuOpen(section === 'transcription');
    setSkillsPanelOpen(section === 'skills');
    setKnowledgeBasesPanelOpen(section === 'knowledge');
    setAgentConstructorOpen(section === 'agent');
  }, []);

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
      openSection('agent');
    });
  }, [openSection, setHidden, setOpen]);

  const openKnowledgeBasesSidebar = useCallback(() => {
    startTransition(() => {
      setHidden(false);
      setOpen(true);
      openSection('knowledge');
    });
  }, [openSection, setHidden, setOpen]);

  const openSkillsSidebar = useCallback(() => {
    startTransition(() => {
      setHidden(false);
      setOpen(true);
      openSection('skills');
    });
  }, [openSection, setHidden, setOpen]);

  usePendingAgentConstructorOpen(openConstructorSidebar);
  usePendingSkillSidebarOpen(openSkillsSidebar);
  usePendingKnowledgeBaseSidebarOpen(openKnowledgeBasesSidebar);

  useEffect(() => {
    const onAgent = () => openConstructorSidebar();
    const onSkills = () => openSkillsSidebar();
    const onKnowledgeBases = () => openKnowledgeBasesSidebar();
    const onTranscription = () => {
      startTransition(() => {
        setHidden(false);
        setOpen(true);
        openSection('transcription');
      });
    };
    window.addEventListener(ASTRA_OPEN_AGENT_CONSTRUCTOR, onAgent);
    window.addEventListener(ASTRA_OPEN_SKILLS_SIDEBAR, onSkills);
    window.addEventListener(ASTRA_OPEN_KNOWLEDGE_BASES_SIDEBAR, onKnowledgeBases);
    window.addEventListener(ASTRA_OPEN_TRANSCRIPTION_SIDEBAR, onTranscription);
    return () => {
      window.removeEventListener(ASTRA_OPEN_AGENT_CONSTRUCTOR, onAgent);
      window.removeEventListener(ASTRA_OPEN_SKILLS_SIDEBAR, onSkills);
      window.removeEventListener(ASTRA_OPEN_KNOWLEDGE_BASES_SIDEBAR, onKnowledgeBases);
      window.removeEventListener(ASTRA_OPEN_TRANSCRIPTION_SIDEBAR, onTranscription);
    };
  }, [openConstructorSidebar, openSkillsSidebar, openKnowledgeBasesSidebar, openSection, setHidden, setOpen]);

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
                          openSection('transcription');
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
                  <Tooltip title="Базы знаний" placement="left">
                    <Box component="span" sx={{ display: 'flex', width: '100%', justifyContent: 'center' }}>
                      <ListItemButton
                        onClick={openKnowledgeBasesSidebar}
                        selected={knowledgeBasesPanelOpen}
                        sx={getSidebarRailCollapsedListItemButtonSx(panelIsLight)}
                      >
                        <KnowledgeBaseIcon sx={SIDEBAR_LIST_ICON_SX} />
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
                          openSection('agent');
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
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.25, minWidth: 0, flex: 1 }}>
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
                        flexShrink: 0,
                        '&:hover': {
                          backgroundColor: isDarkMode ? 'rgba(255, 255, 255, 0.1)' : 'rgba(0, 0, 0, 0.1)',
                          opacity: 1,
                        },
                      }}
                    >
                      <SidebarRailMenuGlyph side="right" />
                    </IconButton>
                  </Tooltip>
                  {activeSection ? (
                    <Tooltip title="Разделы панели" placement="left">
                      <IconButton
                        onClick={(e) => setSectionMenuAnchor(e.currentTarget)}
                        aria-haspopup="menu"
                        aria-expanded={Boolean(sectionMenuAnchor)}
                        sx={{
                          color: 'white',
                          opacity: 1,
                          width: 40,
                          height: 40,
                          borderRadius: 1,
                          p: 0,
                          flexShrink: 0,
                          '&:hover': {
                            backgroundColor: isDarkMode ? 'rgba(255, 255, 255, 0.1)' : 'rgba(0, 0, 0, 0.1)',
                            opacity: 1,
                          },
                        }}
                      >
                        <AppsIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                  ) : null}
                  {activeSectionLabel ? (
                    <Typography
                      sx={{
                        flex: 1,
                        minWidth: 0,
                        ml: 0.5,
                        fontSize: '0.9rem',
                        fontWeight: 600,
                        color: 'inherit',
                        whiteSpace: 'nowrap',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                      }}
                    >
                      {activeSectionLabel}
                    </Typography>
                  ) : null}
                </Box>
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
                      flexShrink: 0,
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

              <Menu
                anchorEl={sectionMenuAnchor}
                open={Boolean(sectionMenuAnchor)}
                onClose={() => setSectionMenuAnchor(null)}
                anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
                transformOrigin={{ vertical: 'top', horizontal: 'left' }}
                slotProps={{
                  paper: {
                    sx: {
                      ...getDropdownPanelSx(isDarkMode),
                      minWidth: 200,
                      py: 0.5,
                    },
                  },
                }}
              >
                {(
                  [
                    {
                      id: 'transcription' as const,
                      label: 'Транскрибация',
                      icon: <SidebarRailTranscribeIcon fontSize="small" />,
                    },
                    {
                      id: 'gallery' as const,
                      label: 'Галерея',
                      icon: <SidebarRailPromptsIcon fontSize="small" />,
                    },
                    {
                      id: 'skills' as const,
                      label: 'Skills',
                      icon: <SkillsNavIcon sx={{ fontSize: 20 }} />,
                    },
                    {
                      id: 'knowledge' as const,
                      label: 'Базы знаний',
                      icon: <KnowledgeBaseIcon sx={{ fontSize: 20 }} />,
                    },
                    {
                      id: 'agent' as const,
                      label: 'Конструктор агента',
                      icon: <SidebarRailAgentIcon sx={{ fontSize: 20 }} />,
                    },
                  ] as const
                ).map((item) => (
                  <MenuItem
                    key={item.id}
                    selected={item.id !== 'gallery' && activeSection === item.id}
                    onClick={() => {
                      setSectionMenuAnchor(null);
                      if (item.id === 'gallery') {
                        closeAllSections();
                        navigate('/gallery');
                        return;
                      }
                      if (activeSection === item.id) {
                        closeAllSections();
                        return;
                      }
                      openSection(item.id);
                    }}
                    sx={{
                      ...getDropdownItemSx(isDarkMode),
                      gap: 1,
                      mx: 0.5,
                      borderRadius: '10px',
                    }}
                  >
                    <ListItemIcon sx={{ minWidth: 28, color: 'inherit' }}>{item.icon}</ListItemIcon>
                    <ListItemText
                      primary={item.label}
                      primaryTypographyProps={{ sx: { fontSize: MENU_ACTION_TEXT_SIZE } }}
                    />
                  </MenuItem>
                ))}
                <Divider sx={{ my: 0.5, borderColor: isDarkMode ? 'rgba(255,255,255,0.1)' : 'rgba(0,0,0,0.1)' }} />
                <MenuItem
                  onClick={() => {
                    setSectionMenuAnchor(null);
                    closeAllSections();
                  }}
                  sx={{ ...getDropdownItemSx(isDarkMode), mx: 0.5, borderRadius: '10px' }}
                >
                  <ListItemText
                    primary="Все разделы"
                    primaryTypographyProps={{ sx: { fontSize: MENU_ACTION_TEXT_SIZE } }}
                  />
                </MenuItem>
              </Menu>

              {!activeSection && (
                <List sx={{ py: 0, px: 1, flexShrink: 0 }}>
                  <ListItem disablePadding sx={{ mb: 0.5 }}>
                    <ListItemButton
                      onClick={() => openSection('transcription')}
                      sx={{
                        ...SIDEBAR_CHAT_ROW_LIST_ITEM_BUTTON_SX,
                        color: 'white',
                        '&:hover': { backgroundColor: 'rgba(255,255,255,0.08)' },
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
                  <GalleryNavButton variant="expanded" isDarkMode={isDarkMode} panelIsLight={panelIsLight} />
                  <ListItem disablePadding sx={{ mb: 0.5 }}>
                    <ListItemButton
                      onClick={() => openSection('skills')}
                      sx={{
                        ...SIDEBAR_CHAT_ROW_LIST_ITEM_BUTTON_SX,
                        color: 'white',
                        '&:hover': { backgroundColor: 'rgba(255,255,255,0.08)' },
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
                  <ListItem disablePadding sx={{ mb: 0.5 }}>
                    <ListItemButton
                      onClick={() => openSection('knowledge')}
                      sx={{
                        ...SIDEBAR_CHAT_ROW_LIST_ITEM_BUTTON_SX,
                        color: 'white',
                        '&:hover': { backgroundColor: 'rgba(255,255,255,0.08)' },
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
                        <KnowledgeBaseIcon />
                      </ListItemIcon>
                      <ListItemText
                        primary="Базы знаний"
                        primaryTypographyProps={{
                          sx: { fontSize: '0.8rem', fontWeight: 400, color: '#ffffff' },
                        }}
                      />
                    </ListItemButton>
                  </ListItem>
                  <ListItem disablePadding sx={{ mb: 0.5 }}>
                    <ListItemButton
                      onClick={() => openSection('agent')}
                      sx={{
                        ...SIDEBAR_CHAT_ROW_LIST_ITEM_BUTTON_SX,
                        color: 'white',
                        '&:hover': { backgroundColor: 'rgba(255,255,255,0.08)' },
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
              )}

              {activeSection === 'transcription' && (
                <Box
                  sx={{
                    flex: 1,
                    minHeight: 0,
                    overflow: 'auto',
                    px: 1,
                    ...SIDEBAR_HIDE_SCROLLBAR_SX,
                  }}
                >
                  <TranscriptionSidebarSection open />
                </Box>
              )}
              {activeSection === 'skills' && (
                <Box
                  sx={{
                    flex: 1,
                    minHeight: 0,
                    overflow: 'hidden',
                    display: 'flex',
                    flexDirection: 'column',
                  }}
                >
                  <SkillsSidebarPanel isOpen />
                </Box>
              )}
              {activeSection === 'knowledge' && (
                <Box
                  sx={{
                    flex: 1,
                    minHeight: 0,
                    overflow: 'hidden',
                    display: 'flex',
                    flexDirection: 'column',
                  }}
                >
                  <KnowledgeBasesSidebarPanel isOpen />
                </Box>
              )}
              {activeSection === 'agent' && (
                <Box
                  sx={{
                    flex: 1,
                    minHeight: 0,
                    overflow: 'hidden',
                    display: 'flex',
                    flexDirection: 'column',
                  }}
                >
                  <AgentConstructorPanel isDarkMode={isDarkMode} isOpen />
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
