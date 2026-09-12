import React from 'react';
import {
  Menu,
  MenuItem,
  ListItemIcon,
  ListItemText,
  Box,
} from '@mui/material';
import {
  FolderOutlined as FolderIcon,
  CreateNewFolderOutlined as AddFolderIcon,
  ChatOutlined as ChatIcon,
} from '@mui/icons-material';
import type { Folder, Project } from '../contexts/AppContext';
import { MENU_BORDER_RADIUS_PX, MENU_ICON_MIN_WIDTH, MENU_ICON_TO_TEXT_GAP_PX, MENU_ICON_FONT_SIZE_PX, MENU_MIN_WIDTH_PX, getProjectIconGlyphSx, getDropdownItemSx, getDropdownPanelSx } from '../constants/menuStyles';
import { PROJECT_ICON_MAP } from '../constants/projectIcons';

// Включить логи подменю в консоль: в DevTools выполнить window.__SUBMENU_DEBUG__ = true и обновить страницу
const SUBMENU_DEBUG = typeof window !== 'undefined' && (window as any).__SUBMENU_DEBUG__;
const log = SUBMENU_DEBUG ? (...args: unknown[]) => console.log('[Submenu]', ...args) : () => {};

// Держим короткие тайминги, чтобы переключение подменю ощущалось мгновенным,
// как в «Модели», но без визуального мигания.
const GRACE_PERIOD_MS = 10;
const CLOSE_CHECK_DELAY_MS = 5;

export interface MoveToMenusHookConfig {
  chatMenuOpen: boolean;
  moveChatToFolder: (chatId: string, folderId: string | null) => void;
  handleChatMenuClose: () => void;
}

export interface MoveToMenusHookResult {
  closeSubmenus: () => void;
  handleFolderSubmenuEnter: (e: React.MouseEvent<HTMLElement>) => void;
  handleFolderSubmenuLeave: (e: React.MouseEvent<HTMLElement>) => void;
  handleProjectSubmenuEnter: (e: React.MouseEvent<HTMLElement>) => void;
  handleProjectSubmenuLeave: (e: React.MouseEvent<HTMLElement>) => void;
  handleMoveToFolder: (chatId: string, folderId: string) => void;
  handleRemoveFromFolder: (chatId: string) => void;
  showMoveToFolderMenu: boolean;
  setShowMoveToFolderMenu: React.Dispatch<React.SetStateAction<boolean>>;
  showMoveToProjectMenu: boolean;
  setShowMoveToProjectMenu: React.Dispatch<React.SetStateAction<boolean>>;
  projectMenuAnchorForChat: HTMLElement | null;
  setProjectMenuAnchorForChat: React.Dispatch<React.SetStateAction<HTMLElement | null>>;
  folderMenuAnchorForChat: HTMLElement | null;
  setFolderMenuAnchorForChat: React.Dispatch<React.SetStateAction<HTMLElement | null>>;
  closeFolderTimerRef: React.MutableRefObject<ReturnType<typeof setTimeout> | null>;
  closeProjectTimerRef: React.MutableRefObject<ReturnType<typeof setTimeout> | null>;
  logRef: React.MutableRefObject<(...args: unknown[]) => void>;
}

export function useMoveToFolderAndProjectMenus(config: MoveToMenusHookConfig): MoveToMenusHookResult {
  const { chatMenuOpen, moveChatToFolder, handleChatMenuClose } = config;

  const [showMoveToFolderMenu, setShowMoveToFolderMenu] = React.useState(false);
  const [showMoveToProjectMenu, setShowMoveToProjectMenu] = React.useState(false);
  const [projectMenuAnchorForChat, setProjectMenuAnchorForChat] = React.useState<HTMLElement | null>(null);
  const [folderMenuAnchorForChat, setFolderMenuAnchorForChat] = React.useState<HTMLElement | null>(null);

  const submenuFolderOpenedAtRef = React.useRef(0);
  const submenuProjectOpenedAtRef = React.useRef(0);
  const projectSubmenuOpenRef = React.useRef(false);
  const folderSubmenuOpenRef = React.useRef(false);
  const projectMenuAnchorRef = React.useRef<HTMLElement | null>(null);
  const folderMenuAnchorRef = React.useRef<HTMLElement | null>(null);
  const closeFolderTimerRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  const closeProjectTimerRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  const mousePositionRef = React.useRef({ clientX: 0, clientY: 0 });
  const logRef = React.useRef(log);
  logRef.current = log;

  React.useEffect(() => {
    if (!chatMenuOpen) return;
    const onMove = (e: MouseEvent) => {
      mousePositionRef.current = { clientX: e.clientX, clientY: e.clientY };
    };
    window.addEventListener('mousemove', onMove);
    return () => window.removeEventListener('mousemove', onMove);
  }, [chatMenuOpen]);

  // При открытом подменю: закрывать, если курсор над другим пунктом основного меню (работает даже при высоком z-index подменю)
  React.useEffect(() => {
    if (!showMoveToFolderMenu && !showMoveToProjectMenu) return;
    const onMove = () => {
      if (!folderSubmenuOpenRef.current && !projectSubmenuOpenRef.current) return;
      const curTrigger = folderSubmenuOpenRef.current ? 'folder' : 'project';
      const anchor = folderSubmenuOpenRef.current ? folderMenuAnchorRef.current : projectMenuAnchorRef.current;
      const menu = anchor?.closest('[role="menu"]') as HTMLElement | null;
      if (!menu) return;
      const { clientX, clientY } = mousePositionRef.current;
      const elements = document.elementsFromPoint(clientX, clientY);
      for (const el of elements) {
        if (!menu.contains(el)) continue;
        const item = (el as HTMLElement).closest?.('[role="menuitem"]');
        if (!item) continue;
        if (item.getAttribute?.('data-submenu-trigger') === curTrigger) break; // наш триггер — не закрывать
        // Курсор над другим пунктом основного меню — закрывать
        log('mousemove: курсор над другим пунктом, закрываем');
        if (closeFolderTimerRef.current) {
          clearTimeout(closeFolderTimerRef.current);
          closeFolderTimerRef.current = null;
        }
        if (closeProjectTimerRef.current) {
          clearTimeout(closeProjectTimerRef.current);
          closeProjectTimerRef.current = null;
        }
        if (curTrigger === 'folder') {
          folderSubmenuOpenRef.current = false;
          folderMenuAnchorRef.current = null;
          setShowMoveToFolderMenu(false);
          setFolderMenuAnchorForChat(null);
        } else {
          projectSubmenuOpenRef.current = false;
          projectMenuAnchorRef.current = null;
          setShowMoveToProjectMenu(false);
          setProjectMenuAnchorForChat(null);
        }
        break;
      }
    };
    const id = setInterval(onMove, 10);
    return () => clearInterval(id);
  }, [showMoveToFolderMenu, showMoveToProjectMenu]);

  const closeSubmenus = React.useCallback(() => {
    log('closeSubmenus');
    if (closeFolderTimerRef.current) {
      clearTimeout(closeFolderTimerRef.current);
      closeFolderTimerRef.current = null;
    }
    if (closeProjectTimerRef.current) {
      clearTimeout(closeProjectTimerRef.current);
      closeProjectTimerRef.current = null;
    }
    projectSubmenuOpenRef.current = false;
    folderSubmenuOpenRef.current = false;
    projectMenuAnchorRef.current = null;
    folderMenuAnchorRef.current = null;
    setShowMoveToFolderMenu(false);
    setShowMoveToProjectMenu(false);
    setFolderMenuAnchorForChat(null);
    setProjectMenuAnchorForChat(null);
  }, []);

  const handleFolderSubmenuEnter = React.useCallback((e: React.MouseEvent<HTMLElement>) => {
    const target = e.currentTarget;
    if (folderSubmenuOpenRef.current && folderMenuAnchorRef.current === target) return;
    const isOpening = !folderSubmenuOpenRef.current;
    folderMenuAnchorRef.current = target;
    folderSubmenuOpenRef.current = true;
    projectSubmenuOpenRef.current = false;
    projectMenuAnchorRef.current = null;
    submenuFolderOpenedAtRef.current = Date.now(); // всегда обновляем — убирает мигание при remount
    log('folder enter', { isOpening });
    setFolderMenuAnchorForChat(target);
    setShowMoveToFolderMenu(true);
    setShowMoveToProjectMenu(false);
    setProjectMenuAnchorForChat(null);
  }, []);

  const handleFolderSubmenuLeave = React.useCallback((e: React.MouseEvent<HTMLElement>) => {
    const to = e.relatedTarget as HTMLElement | null;
    const currentTarget = e.currentTarget;
    const mainMenu = currentTarget.closest('[role="menu"]') as HTMLElement | null;
    const targetMenu = to?.closest?.('[role="menu"]');
    const msSinceOpen = Date.now() - submenuFolderOpenedAtRef.current;
    if (!showMoveToFolderMenu) {
      folderSubmenuOpenRef.current = false;
      folderMenuAnchorRef.current = null;
      setShowMoveToFolderMenu(false);
      setFolderMenuAnchorForChat(null);
      return;
    }
    if (closeFolderTimerRef.current) {
      clearTimeout(closeFolderTimerRef.current);
      closeFolderTimerRef.current = null;
    }
    // Grace period: сразу после открытия не реагируем на leave — убирает мигание
    if (msSinceOpen < GRACE_PERIOD_MS) {
      log('folder leave — игнор (grace period)');
      return;
    }
    // Не закрывать, если уходим на наш же триггер (remount даёт новый DOM-узел)
    const toMenuItem = to?.closest?.('[role="menuitem"]');
    const wentToOurTrigger = toMenuItem?.getAttribute?.('data-submenu-trigger') === 'folder';
    const wentToOtherMainMenuItem =
      to && mainMenu?.contains(to) && toMenuItem !== currentTarget && !wentToOurTrigger;
    if (wentToOtherMainMenuItem) {
      log('folder leave — переход на другой пункт основного меню, закрываем');
      folderSubmenuOpenRef.current = false;
      folderMenuAnchorRef.current = null;
      setShowMoveToFolderMenu(false);
      setFolderMenuAnchorForChat(null);
      return;
    }
    // Курсор ушёл в подменю (другое меню) — не закрываем
    if (targetMenu && targetMenu !== mainMenu) {
      return;
    }
    const closeFolderSubmenu = () => {
      folderSubmenuOpenRef.current = false;
      folderMenuAnchorRef.current = null;
      setShowMoveToFolderMenu(false);
      setFolderMenuAnchorForChat(null);
    };
    const checkPosition = () => {
      closeFolderTimerRef.current = null;
      const { clientX, clientY } = mousePositionRef.current;
      const el = document.elementFromPoint(clientX, clientY) as HTMLElement | null;
      if (!el) {
        closeFolderSubmenu();
        return;
      }
      // Курсор над нашим подменю (папки) — не закрывать
      if (el.closest('[data-chat-submenu="folder"]')) return;
      if (mainMenu?.contains(el)) {
        const underMenuItem = el.closest('[role="menuitem"]');
        if (underMenuItem === currentTarget) return;
        if (underMenuItem && underMenuItem.getAttribute?.('data-submenu-trigger') !== 'folder') {
          closeFolderSubmenu();
          return;
        }
      }
      closeFolderSubmenu();
    };
    const scheduleCheck = () => {
      const { clientX, clientY } = mousePositionRef.current;
      const el = document.elementFromPoint(clientX, clientY) as HTMLElement | null;
      if (el && mainMenu?.contains(el)) {
        const underMenuItem = el.closest('[role="menuitem"]');
        if (underMenuItem && underMenuItem.getAttribute?.('data-submenu-trigger') !== 'folder') {
          log('folder leave — быстрая проверка: курсор на другом пункте');
          closeFolderSubmenu();
          return;
        }
      }
      if (closeFolderTimerRef.current) clearTimeout(closeFolderTimerRef.current);
      closeFolderTimerRef.current = setTimeout(checkPosition, CLOSE_CHECK_DELAY_MS);
    };
    requestAnimationFrame(() => scheduleCheck());
  }, [showMoveToFolderMenu]);

  const handleProjectSubmenuEnter = React.useCallback((e: React.MouseEvent<HTMLElement>) => {
    const target = e.currentTarget;
    if (projectSubmenuOpenRef.current && projectMenuAnchorRef.current === target) return;
    const isOpening = !projectSubmenuOpenRef.current;
    projectMenuAnchorRef.current = target;
    projectSubmenuOpenRef.current = true;
    folderSubmenuOpenRef.current = false;
    folderMenuAnchorRef.current = null;
    submenuProjectOpenedAtRef.current = Date.now();
    log('project enter', { isOpening });
    setProjectMenuAnchorForChat(target);
    setShowMoveToProjectMenu(true);
    setShowMoveToFolderMenu(false);
    setFolderMenuAnchorForChat(null);
  }, []);

  const handleProjectSubmenuLeave = React.useCallback((e: React.MouseEvent<HTMLElement>) => {
    const to = e.relatedTarget as HTMLElement | null;
    const currentTarget = e.currentTarget;
    const mainMenu = currentTarget.closest('[role="menu"]') as HTMLElement | null;
    const targetMenu = to?.closest?.('[role="menu"]');
    const msSinceOpen = Date.now() - submenuProjectOpenedAtRef.current;
    if (!showMoveToProjectMenu) {
      projectSubmenuOpenRef.current = false;
      projectMenuAnchorRef.current = null;
      setShowMoveToProjectMenu(false);
      setProjectMenuAnchorForChat(null);
      return;
    }
    if (closeProjectTimerRef.current) {
      clearTimeout(closeProjectTimerRef.current);
      closeProjectTimerRef.current = null;
    }
    // Grace period: сразу после открытия не реагируем на leave — убирает мигание
    if (msSinceOpen < GRACE_PERIOD_MS) {
      log('project leave — игнор (grace period)');
      return;
    }
    const toMenuItemProject = to?.closest?.('[role="menuitem"]');
    const wentToOurTriggerProject = toMenuItemProject?.getAttribute?.('data-submenu-trigger') === 'project';
    const wentToOtherMainMenuItemProject =
      to && mainMenu?.contains(to) && toMenuItemProject !== currentTarget && !wentToOurTriggerProject;
    if (wentToOtherMainMenuItemProject) {
      log('project leave — переход на другой пункт основного меню, закрываем');
      projectSubmenuOpenRef.current = false;
      projectMenuAnchorRef.current = null;
      setShowMoveToProjectMenu(false);
      setProjectMenuAnchorForChat(null);
      return;
    }
    if (targetMenu && targetMenu !== mainMenu) {
      return;
    }
    const closeProjectSubmenu = () => {
      projectSubmenuOpenRef.current = false;
      projectMenuAnchorRef.current = null;
      setShowMoveToProjectMenu(false);
      setProjectMenuAnchorForChat(null);
    };
    const checkPosition = () => {
      closeProjectTimerRef.current = null;
      const { clientX, clientY } = mousePositionRef.current;
      const el = document.elementFromPoint(clientX, clientY) as HTMLElement | null;
      if (!el) {
        closeProjectSubmenu();
        return;
      }
      if (el.closest('[data-chat-submenu="project"]')) return;
      if (mainMenu?.contains(el)) {
        const underMenuItem = el.closest('[role="menuitem"]');
        if (underMenuItem === currentTarget) return;
        if (underMenuItem && underMenuItem.getAttribute?.('data-submenu-trigger') !== 'project') {
          closeProjectSubmenu();
          return;
        }
      }
      closeProjectSubmenu();
    };
    const scheduleCheck = () => {
      const { clientX, clientY } = mousePositionRef.current;
      const el = document.elementFromPoint(clientX, clientY) as HTMLElement | null;
      if (el && mainMenu?.contains(el)) {
        const underMenuItem = el.closest('[role="menuitem"]');
        if (underMenuItem && underMenuItem.getAttribute?.('data-submenu-trigger') !== 'project') {
          log('project leave — быстрая проверка: курсор на другом пункте');
          closeProjectSubmenu();
          return;
        }
      }
      if (closeProjectTimerRef.current) clearTimeout(closeProjectTimerRef.current);
      closeProjectTimerRef.current = setTimeout(checkPosition, CLOSE_CHECK_DELAY_MS);
    };
    requestAnimationFrame(() => scheduleCheck());
  }, [showMoveToProjectMenu]);

  const handleMoveToFolder = React.useCallback((chatId: string, folderId: string) => {
    moveChatToFolder(chatId, folderId);
    setShowMoveToFolderMenu(false);
    handleChatMenuClose();
  }, [moveChatToFolder, handleChatMenuClose]);

  const handleRemoveFromFolder = React.useCallback((chatId: string) => {
    moveChatToFolder(chatId, null);
    setShowMoveToFolderMenu(false);
    handleChatMenuClose();
  }, [moveChatToFolder, handleChatMenuClose]);

  return {
    closeSubmenus,
    handleFolderSubmenuEnter,
    handleFolderSubmenuLeave,
    handleProjectSubmenuEnter,
    handleProjectSubmenuLeave,
    handleMoveToFolder,
    handleRemoveFromFolder,
    showMoveToFolderMenu,
    setShowMoveToFolderMenu,
    showMoveToProjectMenu,
    setShowMoveToProjectMenu,
    projectMenuAnchorForChat,
    setProjectMenuAnchorForChat,
    folderMenuAnchorForChat,
    setFolderMenuAnchorForChat,
    closeFolderTimerRef,
    closeProjectTimerRef,
    logRef,
  };
}

export interface MoveToSubmenusProps {
  moveTo: MoveToMenusHookResult;
  menuBg: string;
  menuBorder: string;
  menuItemColor: string;
  menuItemHover: string;
  menuDisabledColor: string;
  folders: Folder[];
  projects: Project[];
  selectedChatId: string | null;
  getChatFolder: (chatId: string) => Folder | undefined;
  chats: Array<{ id: string; projectId?: string }>;
  isDarkMode: boolean;
  useFoldersMode: boolean;
  setShowCreateFolderDialog: (v: boolean) => void;
  setPendingChatIdForProject: (v: string | null) => void;
  setShowNewProjectModal: (v: boolean) => void;
  handleChatMenuClose: () => void;
  moveChatToProject: (chatId: string, projectId: string | null) => void;
}

export function MoveToFolderAndProjectSubmenus(props: MoveToSubmenusProps) {
  const {
    moveTo,
    menuBg,
    menuBorder,
    menuItemColor,
    menuItemHover,
    menuDisabledColor,
    folders,
    projects,
    selectedChatId,
    getChatFolder,
    chats,
    isDarkMode,
    useFoldersMode,
    setShowCreateFolderDialog,
    setPendingChatIdForProject,
    setShowNewProjectModal,
    handleChatMenuClose,
    moveChatToProject,
  } = props;
  const dropdownPanelSx = getDropdownPanelSx(isDarkMode);
  const dropdownItemSx = getDropdownItemSx(isDarkMode);

  const {
    showMoveToProjectMenu,
    setShowMoveToProjectMenu,
    setProjectMenuAnchorForChat,
    showMoveToFolderMenu,
    setShowMoveToFolderMenu,
    setFolderMenuAnchorForChat,
    handleMoveToFolder,
    handleRemoveFromFolder,
    closeFolderTimerRef,
    closeProjectTimerRef,
    logRef,
  } = moveTo;

  return (
    <>
      {/* Подменю для перемещения в проект */}
      <Menu
        anchorEl={moveTo.projectMenuAnchorForChat}
        open={showMoveToProjectMenu}
        onClose={() => {
          setShowMoveToProjectMenu(false);
          setProjectMenuAnchorForChat(null);
        }}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
        transformOrigin={{ vertical: 'top', horizontal: 'left' }}
        slotProps={{
          root: { sx: { zIndex: 1301 } },
          backdrop: { style: { pointerEvents: 'none' } },
        }}
        PaperProps={{
          'data-chat-submenu': 'project',
          sx: {
            ...dropdownPanelSx,
            minWidth: `${MENU_MIN_WIDTH_PX}px`,
            zIndex: 1301,
          },
          onMouseEnter: () => {
            if (closeProjectTimerRef.current) {
              clearTimeout(closeProjectTimerRef.current);
              closeProjectTimerRef.current = null;
              logRef.current('paper project enter — отмена таймера закрытия');
            }
          },
          onMouseLeave: (e: React.MouseEvent<HTMLDivElement>) => {
            const to = e.relatedTarget as HTMLElement;
            const mainPaper = moveTo.projectMenuAnchorForChat?.closest('.MuiPopover-paper');
            if (mainPaper?.contains(to)) {
              logRef.current('paper project leave — уход в основное меню, не закрываем');
              return;
            }
            logRef.current('paper project leave — закрываем подменю', { to: to?.tagName });
            setShowMoveToProjectMenu(false);
            setProjectMenuAnchorForChat(null);
          },
        }}
        MenuListProps={{
          sx: { '& .MuiListItemText-root': { marginLeft: 0 } },
        }}
        disableAutoFocusItem
        disableAutoFocus
        disableEnforceFocus
        disableScrollLock
      >
        <MenuItem
          onClick={() => {
            if (selectedChatId) {
              setPendingChatIdForProject(selectedChatId);
              setShowNewProjectModal(true);
              setShowMoveToProjectMenu(false);
              setProjectMenuAnchorForChat(null);
              handleChatMenuClose();
            }
          }}
          sx={{ ...dropdownItemSx, color: menuItemColor, '&:hover': { backgroundColor: menuItemHover } }}
        >
          <ListItemIcon sx={{ color: menuItemColor, minWidth: `${MENU_ICON_MIN_WIDTH}px`, marginRight: `${MENU_ICON_TO_TEXT_GAP_PX}px`, '& .MuiSvgIcon-root': { fontSize: `${MENU_ICON_FONT_SIZE_PX}px` } }}>
            <AddFolderIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText primary="Новый проект" />
        </MenuItem>
        {projects.map((project) => {
          const renderProjectIcon = () => {
            const iconColor = project.iconColor || '#9ca3af';
            const glyphSx = getProjectIconGlyphSx(11, iconColor);
            const iconWrapSx = {
              width: 20,
              height: 20,
              display: 'flex' as const,
              alignItems: 'center',
              justifyContent: 'center',
              color: iconColor,
            };
            if (project.iconType === 'emoji' && project.icon) {
              return (
                <Box
                  sx={{
                    width: 20,
                    height: 20,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontSize: 12,
                    lineHeight: 1,
                    transform: 'translateY(-0.25px)',
                  }}
                >
                  {project.icon}
                </Box>
              );
            }
            if (project.iconType === 'icon' && project.icon) {
              const IconComponent = PROJECT_ICON_MAP[project.icon] || FolderIcon;
              return (
                <Box sx={iconWrapSx}>
                  <IconComponent sx={{ ...glyphSx, color: 'currentColor' }} />
                </Box>
              );
            }
            return (
              <Box sx={iconWrapSx}>
                <FolderIcon sx={{ ...glyphSx, color: 'currentColor' }} />
              </Box>
            );
          };

          const chat = selectedChatId ? chats.find((c) => c.id === selectedChatId) : null;
          const isSelected = chat?.projectId === project.id;

          return (
            <MenuItem
              key={project.id}
              onClick={() => {
                if (selectedChatId) {
                  moveChatToProject(selectedChatId, isSelected ? null : project.id);
                  setShowMoveToProjectMenu(false);
                  setProjectMenuAnchorForChat(null);
                  handleChatMenuClose();
                }
              }}
              sx={{
                ...dropdownItemSx,
                color: isSelected ? menuDisabledColor : menuItemColor,
                '&:hover': { backgroundColor: menuItemHover },
              }}
              disabled={isSelected}
            >
              <ListItemIcon sx={{ color: isSelected ? menuDisabledColor : menuItemColor, minWidth: `${MENU_ICON_MIN_WIDTH}px`, marginRight: `${MENU_ICON_TO_TEXT_GAP_PX}px`, '& .MuiSvgIcon-root': { fontSize: `${MENU_ICON_FONT_SIZE_PX}px` } }}>
                {renderProjectIcon()}
              </ListItemIcon>
              <ListItemText primary={project.name} />
            </MenuItem>
          );
        })}
      </Menu>

      {/* Меню перемещения в папку */}
      <Menu
        anchorEl={moveTo.folderMenuAnchorForChat}
        open={showMoveToFolderMenu}
        onClose={() => {
          setShowMoveToFolderMenu(false);
          setFolderMenuAnchorForChat(null);
        }}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
        transformOrigin={{ vertical: 'top', horizontal: 'left' }}
        slotProps={{
          root: { sx: { zIndex: 1301 } },
          backdrop: { style: { pointerEvents: 'none' } },
        }}
        PaperProps={{
          'data-chat-submenu': 'folder',
          sx: {
            ...dropdownPanelSx,
            minWidth: `${MENU_MIN_WIDTH_PX}px`,
            zIndex: 1301,
          },
          onMouseEnter: () => {
            if (closeFolderTimerRef.current) {
              clearTimeout(closeFolderTimerRef.current);
              closeFolderTimerRef.current = null;
              logRef.current('paper folder enter — отмена таймера закрытия');
            }
          },
          onMouseLeave: (e: React.MouseEvent<HTMLDivElement>) => {
            const to = e.relatedTarget as HTMLElement;
            const mainPaper = moveTo.folderMenuAnchorForChat?.closest('.MuiPopover-paper');
            if (mainPaper?.contains(to)) {
              logRef.current('paper folder leave — уход в основное меню, не закрываем');
              return;
            }
            logRef.current('paper folder leave — закрываем подменю', { to: to?.tagName });
            setShowMoveToFolderMenu(false);
            setFolderMenuAnchorForChat(null);
          },
        }}
        MenuListProps={{
          sx: { '& .MuiListItemText-root': { marginLeft: 0 } },
        }}
        disableAutoFocusItem
        disableAutoFocus
        disableEnforceFocus
        disableScrollLock
      >
        {useFoldersMode && (
          <MenuItem
            onClick={() => {
              setShowCreateFolderDialog(true);
              setShowMoveToFolderMenu(false);
              setFolderMenuAnchorForChat(null);
              handleChatMenuClose();
            }}
            sx={{ ...dropdownItemSx, color: menuItemColor, '&:hover': { backgroundColor: menuItemHover } }}
          >
            <ListItemIcon sx={{ color: menuItemColor, minWidth: `${MENU_ICON_MIN_WIDTH}px`, marginRight: `${MENU_ICON_TO_TEXT_GAP_PX}px`, '& .MuiSvgIcon-root': { fontSize: `${MENU_ICON_FONT_SIZE_PX}px` } }}>
              <AddFolderIcon fontSize="small" />
            </ListItemIcon>
            <ListItemText primary="Создать папку" />
          </MenuItem>
        )}

        {useFoldersMode && (
          <MenuItem
            onClick={() => {
              if (selectedChatId) {
                handleRemoveFromFolder(selectedChatId);
                setShowMoveToFolderMenu(false);
                setFolderMenuAnchorForChat(null);
                handleChatMenuClose();
              }
            }}
            sx={{
              ...dropdownItemSx,
              color: selectedChatId && !getChatFolder(selectedChatId) ? menuDisabledColor : menuItemColor,
              '&:hover': { backgroundColor: menuItemHover },
            }}
            disabled={selectedChatId ? !getChatFolder(selectedChatId) : false}
          >
            <ListItemIcon
              sx={{
                color: selectedChatId && !getChatFolder(selectedChatId) ? menuDisabledColor : menuItemColor,
                minWidth: `${MENU_ICON_MIN_WIDTH}px`,
                marginRight: `${MENU_ICON_TO_TEXT_GAP_PX}px`,
                '& .MuiSvgIcon-root': { fontSize: `${MENU_ICON_FONT_SIZE_PX}px` },
              }}
            >
              <ChatIcon fontSize="small" />
            </ListItemIcon>
            <ListItemText primary="Все чаты" />
          </MenuItem>
        )}

        {useFoldersMode &&
          folders
            .filter((folder) => {
              const currentFolder = selectedChatId ? getChatFolder(selectedChatId) : null;
              return !currentFolder || folder.id !== currentFolder.id;
            })
            .map((folder) => (
              <MenuItem
                key={folder.id}
                onClick={() => {
                  if (selectedChatId) {
                    handleMoveToFolder(selectedChatId, folder.id);
                    setShowMoveToFolderMenu(false);
                    setFolderMenuAnchorForChat(null);
                    handleChatMenuClose();
                  }
                }}
                sx={{ ...dropdownItemSx, color: menuItemColor, '&:hover': { backgroundColor: menuItemHover } }}
              >
                <ListItemIcon sx={{ color: menuItemColor, minWidth: `${MENU_ICON_MIN_WIDTH}px`, marginRight: `${MENU_ICON_TO_TEXT_GAP_PX}px`, '& .MuiSvgIcon-root': { fontSize: `${MENU_ICON_FONT_SIZE_PX}px` } }}>
                  <FolderIcon fontSize="small" />
                </ListItemIcon>
                <ListItemText primary={folder.name} />
              </MenuItem>
            ))}
        {folders.filter((folder) => {
          const currentFolder = selectedChatId ? getChatFolder(selectedChatId) : null;
          return !currentFolder || folder.id !== currentFolder.id;
        }).length === 0 && (
          <MenuItem disabled sx={{ color: menuDisabledColor }}>
            <ListItemText primary="Нет доступных папок" />
          </MenuItem>
        )}
      </Menu>
    </>
  );
}