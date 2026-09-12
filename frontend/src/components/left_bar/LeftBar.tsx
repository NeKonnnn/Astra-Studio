import React from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import {
  Drawer,
  List,
  ListItem,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Divider,
  IconButton,
  Box,
  Typography,
  Avatar,
  Chip,
  Button,
  TextField,
  InputAdornment,
  Popover,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Tooltip,
  Link,
} from '@mui/material';
import {
  ChatOutlined as ChatIcon,
  SettingsOutlined as SettingsIcon,
  InfoOutlined as InfoIcon,
  DeleteOutlined as DeleteIcon,
  EditOutlined as EditIcon,
  MoreVert as MoreVertIcon,
  ExpandMore as ExpandMoreIcon,
  FolderOutlined as FolderIcon,
  CreateNewFolderOutlined as AddFolderIcon,
  LogoutOutlined as LogoutIcon,
  ChevronLeft as ChevronLeftIcon,
  ChevronRight as ChevronRightIcon,
  ArchiveOutlined as ArchiveIcon,
  PushPinOutlined as PushPinIcon,
  HistoryOutlined as UpdatesIcon,
  KeyboardOutlined as KeyboardIcon,
  HelpOutline as HelpOutlineIcon,
  Close as CloseIcon,
} from '@mui/icons-material';
import { PROJECT_ICON_MAP as projectIconMap } from '../../constants/projectIcons';
import { useAppContext, useAppActions, chatIsListedInAllChatsSection } from '../../contexts/AppContext';
import { groupChatsBySidebarTime } from '../../utils/chatListTimeGroups';
import { useSocket } from '../../contexts/SocketContext';
import { useAuth } from '../../contexts/AuthContext';
import ArchiveModal from '../ArchiveModal';
import NewProjectModal from '../NewProjectModal';
import EditProjectModal from '../EditProjectModal';
import SidebarRailMenuGlyph from '../../icons/SidebarRailMenuGlyph';
import {
  MENU_BORDER_RADIUS_PX,
  getMenuColors,
  SIDEBAR_PROJECT_AVATAR_SIZE,
  getProjectIconGlyphSx,
  getDropdownItemSx,
  MENU_ACTION_TEXT_SIZE,
  MENU_COMPACT_PANEL_WIDTH_PX,
  getDropdownPanelSx,
  SIDEBAR_CONTROL_HEIGHT_PX,
  SIDEBAR_CONTROL_RADIUS,
  SIDEBAR_CONTROL_PX,
  SIDEBAR_ICON_LEADING_PL,
  SIDEBAR_LIST_LEADING_ICON_SX,
  SIDEBAR_HIDE_SCROLLBAR_SX,
  SIDEBAR_CHAT_ROW_LIST_ITEM_BUTTON_SX,
  SIDEBAR_LIST_ICON_SX,
} from '../../constants/menuStyles';
import {
  SIDEBAR_PANEL_COLOR_KEY,
  getSidebarPanelBackground,
  getSidebarChromeSx,
  getSidebarForcedContrastSx,
  getSidebarPanelHoverBackground,
  isSidebarPanelLight,
} from '../../constants/sidebarPanelColor';
import { ASTRA_REQUEST_DELETE_CURRENT_CHAT, ASTRA_OPEN_SETTINGS } from '../../constants/hotkeys';
import { useHotkeyBindings } from '../../hooks/useHotkeyBindings';
import HotkeysSettingsDialog from '../HotkeysSettingsDialog';
import { SidebarRailAddIcon, SidebarRailSearchIcon } from '../../constants/sidebarRailIcons';
import SidebarRailCollapsedAction, { SidebarRailShortcutTooltip } from './SidebarRailCollapsedAction';
import { getApiUrl, getAuthFetchHeaders } from '../../config/api';
import AuthenticatedInlineImage from '../AuthenticatedInlineImage';
import { useImageCreations } from '../../hooks/useImageCreations';
import { resolveCreationPreviewSrc } from '../../utils/imageCreations';
import CollectionsBookmarkOutlinedIcon from '@mui/icons-material/CollectionsBookmarkOutlined';

interface SidebarProps {
  open: boolean;
  onToggle: () => void;
  isDarkMode: boolean;
  onToggleTheme: () => void;
  onHide?: () => void;
  /** Увеличивается при глобальном запросе фокуса на поле поиска (Ctrl+O). */
  searchFocusNonce?: number;
}

const menuItems: any[] = [];

const SIDEBAR_SECTION_CHEVRON_SX = {
  fontSize: '1.25rem',
  color: 'var(--sidebar-fg, #ffffff)',
  opacity: 0.9,
} as const;

/** Строка заголовка секции («Проекты», «Все чаты», папка): те же высота и поля, что у «Все чаты». */
const SIDEBAR_SECTION_HEADER_ROW_SX = {
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  px: SIDEBAR_CONTROL_PX,
  py: 0,
  minHeight: SIDEBAR_CONTROL_HEIGHT_PX,
  borderRadius: SIDEBAR_CONTROL_RADIUS,
  '&:hover': {
    backgroundColor: 'var(--sidebar-hover-bg, rgba(255,255,255,0.05))',
  },
  transition: 'background-color 0.2s ease',
} as const;

const SIDEBAR_SECTION_HEADER_TITLE_SX = {
  opacity: 0.8,
  fontSize: '0.75rem',
  lineHeight: 1.25,
  fontWeight: 500,
} as const;

// Маппинг иконок для проектов — см. constants/projectIcons (outlined)

export default function Sidebar({ open, onToggle, isDarkMode, onToggleTheme, onHide, searchFocusNonce = 0 }: SidebarProps) {
  const navigate = useNavigate();
  const location = useLocation();
  const { state } = useAppContext();
  const stateRef = React.useRef(state);
  stateRef.current = state;
  const { user, logout, token } = useAuth();
  const { 
    createChat, 
    setCurrentChat, 
    deleteChat, 
    updateChatTitle, 
    getCurrentChat,
    createFolder,
    updateFolder,
    deleteFolder,
    moveChatToFolder,
    toggleFolder,
    getFolders,
    archiveChat,
    archiveFolder,
    createProject,
    updateProject,
    deleteProject,
    getProjects,
    moveChatToProject,
    getChatById,
    togglePinInProject
  } = useAppActions();
  const { isConnected } = useSocket();
  const { items: creationItems } = useImageCreations(3);
  const creationPreviews = React.useMemo(
    () =>
      creationItems.slice(0, 3).map((item) => ({
        id: item.id,
        preview_url: resolveCreationPreviewSrc(item.preview_url),
        prompt: item.prompt,
      })),
    [creationItems],
  );
  
  // Получаем папки из состояния и сортируем (папка "Закреплено" должна быть первой)
  const allFolders = getFolders();
  const folders = React.useMemo(() => {
    const pinnedFolder = allFolders.find(f => f.name === 'Закреплено');
    const otherFolders = allFolders.filter(f => f.name !== 'Закреплено');
    return pinnedFolder ? [pinnedFolder, ...otherFolders] : otherFolders;
  }, [allFolders]);
  
  const [editingChatId, setEditingChatId] = React.useState<string | null>(null);
  const [editingTitle, setEditingTitle] = React.useState('');
  const [anchorEl, setAnchorEl] = React.useState<null | HTMLElement>(null);
  const [userMenuSubmenu, setUserMenuSubmenu] = React.useState<'help' | null>(null);
  const [userMenuSubmenuOffsetTop, setUserMenuSubmenuOffsetTop] = React.useState(0);
  const userSubmenuCloseTimerRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  const [showHelpDialog, setShowHelpDialog] = React.useState(false);
  const [showUpdatesDialog, setShowUpdatesDialog] = React.useState(false);
  const [showShortcutsDialog, setShowShortcutsDialog] = React.useState(false);

  const onyxNewsUrl =
    'https://xlnk.ms/join/channel/MTcwYzM0NjMtNzJmMC01ZTQxLTg2MzQtMjEyNWM3ODIwMDdhOjc1ZjkzYmE4LWJlZTMtNWIwYi05OTM2LWNhNGExYmZkNDQ2MDpjZmIzMzY3Ny1iNTI5LTVmOWMtYWZhYy03NWY5MjgyYmU3OTM6NGNkNGM2ZjAtYjU3ZC01Njg5LWI4ZTEtM2UxMjU3Yzg2Njk5';
  const onyxSupportUrl =
    'https://xlnk.ms/join/chat/MTBkMWNhZDktNzA2MC01ZDc0LTlkOGItM2FkN2E2NWI3YWQyOjc1ZjkzYmE4LWJlZTMtNWIwYi05OTM2LWNhNGExYmZkNDQ2MDo0MWFjYTFhZS1iNzMwLTViNWEtODQ5My0xYmZkMWUxOTkyMWM6NGNkNGM2ZjAtYjU3ZC01Njg5LWI4ZTEtM2UxMjU3Yzg2Njk5';
  const helpDialogPaperSx = {
    width: { xs: '94vw', sm: 'min(920px, 88vw)' },
    maxWidth: 920,
    m: 2,
    backgroundColor: isDarkMode ? '#2c2c2c' : '#ffffff',
    color: isDarkMode ? 'rgba(255,255,255,0.92)' : '#111',
    borderRadius: '12px',
    border: isDarkMode ? '1px solid rgba(255,255,255,0.08)' : '1px solid rgba(0,0,0,0.12)',
    boxShadow: isDarkMode ? '0 12px 40px rgba(0,0,0,0.55)' : '0 8px 28px rgba(0,0,0,0.16)',
    overflow: 'hidden',
  } as const;
  const helpLinkSx = {
    fontSize: '0.9375rem',
    color: isDarkMode ? '#64b5f6' : '#1976d2',
    textDecorationColor: isDarkMode ? '#64b5f6' : '#1976d2',
    '&:hover': { color: isDarkMode ? '#90caf9' : '#1565c0' },
  } as const;
  const { bindings, format } = useHotkeyBindings();
  const [newChatShortcutHover, setNewChatShortcutHover] = React.useState(false);
  const [searchFieldHover, setSearchFieldHover] = React.useState(false);
  const [chatMenuAnchor, setChatMenuAnchor] = React.useState<null | HTMLElement>(null);
  const [chatMenuSubmenu, setChatMenuSubmenu] = React.useState<'folder' | 'project' | null>(null);
  const [chatMenuSubmenuOffsetTop, setChatMenuSubmenuOffsetTop] = React.useState(0);
  const chatSubmenuCloseTimerRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  const [selectedChatId, setSelectedChatId] = React.useState<string | null>(null);
  const [showDeleteDialog, setShowDeleteDialog] = React.useState(false);
  const [chatsExpanded, setChatsExpanded] = React.useState(true);
  const [showArchiveModal, setShowArchiveModal] = React.useState(false);
  const [showNewProjectModal, setShowNewProjectModal] = React.useState(false);
  const [pendingChatIdForProject, setPendingChatIdForProject] = React.useState<string | null>(null);
  const [projectsExpanded, setProjectsExpanded] = React.useState(true);
  const [expandedProjects, setExpandedProjects] = React.useState<Set<string>>(new Set());
  const [projectMenuAnchor, setProjectMenuAnchor] = React.useState<null | HTMLElement>(null);
  const [selectedProjectId, setSelectedProjectId] = React.useState<string | null>(null);
  const [showDeleteProjectDialog, setShowDeleteProjectDialog] = React.useState(false);
  const [showEditProjectModal, setShowEditProjectModal] = React.useState(false);
  const [projectIdToEdit, setProjectIdToEdit] = React.useState<string | null>(null);
  const [searchQuery, setSearchQuery] = React.useState('');
  const [showCreateFolderDialog, setShowCreateFolderDialog] = React.useState(false);
  const [newFolderName, setNewFolderName] = React.useState('');
  const [folderMenuAnchor, setFolderMenuAnchor] = React.useState<null | HTMLElement>(null);
  const [selectedFolderId, setSelectedFolderId] = React.useState<string | null>(null);
  const [showRenameFolderDialog, setShowRenameFolderDialog] = React.useState(false);
  const [renamingFolderName, setRenamingFolderName] = React.useState('');
  const [showDeleteFolderDialog, setShowDeleteFolderDialog] = React.useState(false);
  const [deleteWithContent, setDeleteWithContent] = React.useState(false);
  const [sidebarColorTick, setSidebarColorTick] = React.useState(0);
  const sidebarPanelBg = React.useMemo(
    () => getSidebarPanelBackground(isDarkMode),
    // tick — сброс/смена цвета в настройках; isDarkMode — смена темы
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [isDarkMode, sidebarColorTick],
  );
  const searchInputRef = React.useRef<HTMLInputElement>(null);
  const menuOpen = Boolean(anchorEl);

  React.useEffect(() => {
    if (searchFocusNonce === 0) return;
    if (!open) {
      onToggle();
      const t = window.setTimeout(() => searchInputRef.current?.focus(), 320);
      return () => clearTimeout(t);
    }
    searchInputRef.current?.focus();
    return undefined;
  }, [searchFocusNonce, open, onToggle]);

  React.useEffect(() => {
    const onRequestDelete = () => {
      const id = stateRef.current.currentChatId;
      if (!id) return;
      setSelectedChatId(id);
      setShowDeleteDialog(true);
    };
    window.addEventListener(ASTRA_REQUEST_DELETE_CURRENT_CHAT, onRequestDelete);
    return () => window.removeEventListener(ASTRA_REQUEST_DELETE_CURRENT_CHAT, onRequestDelete);
  }, []);

  React.useEffect(() => {
    // Нормализуем залипшие дефолтные HEX (#F7F7F9 / #212128) → пустая строка.
    const raw = (localStorage.getItem(SIDEBAR_PANEL_COLOR_KEY) || '').trim();
    if (
      raw &&
      (raw.toLowerCase() === '#f7f7f9' || raw.toLowerCase() === '#212128')
    ) {
      localStorage.setItem(SIDEBAR_PANEL_COLOR_KEY, '');
      setSidebarColorTick((n) => n + 1);
    }
    const onColorChanged = () => setSidebarColorTick((n) => n + 1);
    window.addEventListener('sidebarColorChanged', onColorChanged);
    return () => window.removeEventListener('sidebarColorChanged', onColorChanged);
  }, []);
  const chatMenuOpen = Boolean(chatMenuAnchor);
  const folderMenuOpen = Boolean(folderMenuAnchor);
  const projectMenuOpen = Boolean(projectMenuAnchor);

  const { menuBg, menuBorder, menuItemColor, menuItemHover, menuDividerBorder, menuDisabledColor } = getMenuColors(isDarkMode);
  const dropdownPanelSx = getDropdownPanelSx(isDarkMode);
  const dropdownItemSx = React.useMemo(() => getDropdownItemSx(isDarkMode), [isDarkMode]);
  const submenuIconColor = isDarkMode ? '#ffffff' : 'rgba(0,0,0,0.6)';
  const submenuChevronColor = isDarkMode ? 'rgba(255,255,255,0.85)' : 'rgba(0,0,0,0.45)';

  // Получаем проекты
  const projects = getProjects();

  // Загружаем настройку использования папок/проектов
  const [useFoldersMode, setUseFoldersMode] = React.useState(() => {
    const saved = localStorage.getItem('use_folders_mode');
    return saved !== null ? saved === 'true' : false; // По умолчанию проекты (как в InterfaceSettings)
  });

  // Слушаем изменения настроек интерфейса
  React.useEffect(() => {
    const handleSettingsChange = () => {
      const saved = localStorage.getItem('use_folders_mode');
      setUseFoldersMode(saved !== null ? saved === 'true' : false);
    };
    
    window.addEventListener('interfaceSettingsChanged', handleSettingsChange);
    return () => {
      window.removeEventListener('interfaceSettingsChanged', handleSettingsChange);
    };
  }, []);

  const handleNavigation = (path: string) => {
    navigate(path);
  };

  const handleCreateChat = () => {
    const cur = getCurrentChat();
    if (cur && !chatIsListedInAllChatsSection(cur)) {
      deleteChat(cur.id);
    }
    const chatId = createChat();
    setCurrentChat(chatId);
    navigate('/');
  };

  const handleSelectChat = (chatId: string) => {
    setCurrentChat(chatId);
    navigate('/');
  };



  const handleSaveEdit = () => {
    if (editingChatId && editingTitle.trim()) {
      updateChatTitle(editingChatId, editingTitle.trim());
    }
    setEditingChatId(null);
    setEditingTitle('');
    setSelectedChatId(null); // Сбрасываем selectedChatId после завершения редактирования
  };

  const handleCancelEdit = () => {
    setEditingChatId(null);
    setEditingTitle('');
    setSelectedChatId(null); // Сбрасываем selectedChatId после отмены редактирования
  };

  const handleKeyPress = (event: React.KeyboardEvent) => {
    if (event.key === 'Enter') {
      handleSaveEdit();
    } else if (event.key === 'Escape') {
      handleCancelEdit();
    }
  };

  const currentChat = getCurrentChat();

  // Функция для определения папки, в которой находится чат
  const getChatFolder = (chatId: string) => {
    return folders.find(folder => folder.chatIds.includes(chatId));
  };

  // Функция для закрепления/открепления чата
  const handleTogglePin = (chatId: string) => {
    const chat = getChatById(chatId);
    
    // Если чат находится в проекте, используем локальное закрепление
    if (chat?.projectId) {
      togglePinInProject(chatId);
      handleChatMenuClose();
      return;
    }
    
    // Для чатов вне проекта используем старую логику с папкой "Закреплено"
    const pinnedFolder = folders.find(f => f.name === 'Закреплено');
    const currentFolder = getChatFolder(chatId);
    
    if (currentFolder?.name === 'Закреплено') {
      // Открепляем чат - убираем из папки "Закреплено"
      // Папка автоматически удалится в reducer, если станет пустой
      moveChatToFolder(chatId, null);
    } else {
      // Закрепляем чат - перемещаем в папку "Закреплено"
      if (!pinnedFolder) {
        // Создаем папку "Закреплено" если её нет
        const pinnedFolderId = createFolder('Закреплено');
        moveChatToFolder(chatId, pinnedFolderId);
      } else {
        moveChatToFolder(chatId, pinnedFolder.id);
      }
    }
    handleChatMenuClose();
  };

  const handleMenuClick = (event: React.MouseEvent<HTMLElement>) => {
    if (userSubmenuCloseTimerRef.current) {
      clearTimeout(userSubmenuCloseTimerRef.current);
      userSubmenuCloseTimerRef.current = null;
    }
    setAnchorEl(event.currentTarget);
    setUserMenuSubmenu(null);
    setUserMenuSubmenuOffsetTop(0);
  };

  const handleMenuClose = () => {
    if (userSubmenuCloseTimerRef.current) {
      clearTimeout(userSubmenuCloseTimerRef.current);
      userSubmenuCloseTimerRef.current = null;
    }
    setAnchorEl(null);
    setUserMenuSubmenu(null);
    setUserMenuSubmenuOffsetTop(0);
  };

  const handleUserMenuSubmenuEnter = (event: React.MouseEvent<HTMLElement>) => {
    if (userSubmenuCloseTimerRef.current) {
      clearTimeout(userSubmenuCloseTimerRef.current);
      userSubmenuCloseTimerRef.current = null;
    }
    const target = event.currentTarget;
    setUserMenuSubmenu('help');
    setUserMenuSubmenuOffsetTop(Math.max(0, target.offsetTop - 4));
  };

  const scheduleUserSubmenuClose = () => {
    if (userSubmenuCloseTimerRef.current) {
      clearTimeout(userSubmenuCloseTimerRef.current);
    }
    userSubmenuCloseTimerRef.current = setTimeout(() => {
      setUserMenuSubmenu(null);
      userSubmenuCloseTimerRef.current = null;
    }, 220);
  };

  const cancelUserSubmenuClose = () => {
    if (userSubmenuCloseTimerRef.current) {
      clearTimeout(userSubmenuCloseTimerRef.current);
      userSubmenuCloseTimerRef.current = null;
    }
  };

  const handleMenuAction = (action: string) => {
    handleMenuClose();
    switch (action) {
      case 'settings':
        window.dispatchEvent(new CustomEvent(ASTRA_OPEN_SETTINGS));
        break;
      case 'archive':
        setShowArchiveModal(true);
        break;
      case 'agents-gallery':
        navigate('/gallery?tab=agents');
        break;
      case 'logout':
        logout();
        navigate('/login');
        break;
    }
  };

  const handleChatMenuClick = (event: React.MouseEvent<HTMLElement>, chatId: string) => {
    event.stopPropagation();
    setChatMenuAnchor(event.currentTarget);
    setChatMenuSubmenu(null);
    setSelectedChatId(chatId);
  };

  const handleChatMenuClose = () => {
    if (chatSubmenuCloseTimerRef.current) {
      clearTimeout(chatSubmenuCloseTimerRef.current);
      chatSubmenuCloseTimerRef.current = null;
    }
    setChatMenuAnchor(null);
    setChatMenuSubmenu(null);
    setChatMenuSubmenuOffsetTop(0);
    // Не сбрасываем selectedChatId сразу, чтобы не потерять его при редактировании
    // Он будет сброшен после завершения редактирования
  };

  const handleChatMenuSubmenuEnter = (submenu: 'folder' | 'project', event: React.MouseEvent<HTMLElement>) => {
    if (chatSubmenuCloseTimerRef.current) {
      clearTimeout(chatSubmenuCloseTimerRef.current);
      chatSubmenuCloseTimerRef.current = null;
    }
    const target = event.currentTarget;
    setChatMenuSubmenu(submenu);
    // Выравниваем верх правого окна с верхом строки-триггера.
    setChatMenuSubmenuOffsetTop(Math.max(0, target.offsetTop - 4));
  };

  const scheduleChatSubmenuClose = () => {
    if (chatSubmenuCloseTimerRef.current) {
      clearTimeout(chatSubmenuCloseTimerRef.current);
    }
    chatSubmenuCloseTimerRef.current = setTimeout(() => {
      setChatMenuSubmenu(null);
      chatSubmenuCloseTimerRef.current = null;
    }, 220);
  };

  const cancelChatSubmenuClose = () => {
    if (chatSubmenuCloseTimerRef.current) {
      clearTimeout(chatSubmenuCloseTimerRef.current);
      chatSubmenuCloseTimerRef.current = null;
    }
  };

  const handleChatMenuAction = (action: string) => {
    if (!selectedChatId) {
      return;
    }
    
    // Сохраняем selectedChatId перед закрытием меню
    const chatIdToAction = selectedChatId;
    
    switch (action) {
      case 'pin':
        handleTogglePin(chatIdToAction);
        break;
      case 'edit':
        const chatToEdit = state.chats.find(chat => chat.id === chatIdToAction);
        if (chatToEdit) {
          handleChatMenuClose();
          // Используем requestAnimationFrame для гарантированного обновления после закрытия меню
          requestAnimationFrame(() => {
            setEditingChatId(chatIdToAction);
            setEditingTitle(chatToEdit.title);
          });
        }
        break;
      case 'archive':
        handleChatMenuClose();
        archiveChat(chatIdToAction);
        break;
      case 'removeFromProject':
        handleChatMenuClose();
        moveChatToProject(chatIdToAction, null);
        break;
      case 'delete':
        handleChatMenuClose();
        setSelectedChatId(chatIdToAction); // Восстанавливаем selectedChatId для диалога
        setShowDeleteDialog(true);
        break;
      default:
        handleChatMenuClose();
        break;
    }
  };

  const handleConfirmDelete = () => {
    if (selectedChatId) {
      deleteChat(selectedChatId);
      if (state.currentChatId === selectedChatId) {
        const remainingChats = state.chats.filter(chat => chat.id !== selectedChatId);
        const next =
          remainingChats.find((c) => chatIsListedInAllChatsSection(c)) ?? remainingChats[0] ?? null;
        setCurrentChat(next ? next.id : null);
      }
      setShowDeleteDialog(false);
      setSelectedChatId(null);
    }
  };

  // Функция для фильтрации чатов по поисковому запросу
  const filteredChats = React.useMemo(() => {
    // Исключаем чаты, которые уже находятся в папках или проектах (в зависимости от режима), и архивированные чаты
    const chatsInFolders = useFoldersMode ? new Set(folders.flatMap(folder => folder.chatIds)) : new Set();
    const chatsInProjects = !useFoldersMode ? new Set(state.chats.filter(chat => chat.projectId).map(chat => chat.id)) : new Set();
    const availableChats = state.chats.filter(
      (chat) =>
        !chatsInFolders.has(chat.id) &&
        !chatsInProjects.has(chat.id) &&
        !chat.isArchived &&
        chatIsListedInAllChatsSection(chat)
    );
    
    if (!searchQuery.trim()) {
      return availableChats;
    }
    return availableChats.filter(chat => 
      chat.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
      chat.messages.some(msg => 
        msg.content.toLowerCase().includes(searchQuery.toLowerCase())
      )
    );
  }, [state.chats, searchQuery, folders, projects]);

  const filteredChatsByTime = React.useMemo(
    () => groupChatsBySidebarTime(filteredChats),
    [filteredChats]
  );
  
  // Функция для получения чатов проекта
  const getProjectChats = (projectId: string) => {
    const chats = state.chats.filter(
      (chat) =>
        chat.projectId === projectId &&
        !chat.isArchived &&
        chatIsListedInAllChatsSection(chat)
    );
    
    // Сортируем: запиненные чаты сначала
    return chats.sort((a, b) => {
      const aIsPinned = a.isPinnedInProject || false;
      const bIsPinned = b.isPinnedInProject || false;
      
      if (aIsPinned && !bIsPinned) return -1;
      if (!aIsPinned && bIsPinned) return 1;
      
      // Если оба запинены или оба незапинены, сортируем по дате обновления
      return new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime();
    });
  };
  
  // Функция для переключения раскрытия проекта
  const handleToggleProject = (projectId: string) => {
    setExpandedProjects(prev => {
      const newSet = new Set(prev);
      if (newSet.has(projectId)) {
        newSet.delete(projectId);
      } else {
        newSet.add(projectId);
      }
      return newSet;
    });
  };


  // Функции для работы с папками
  const handleCreateFolder = () => {
    if (newFolderName.trim()) {
      createFolder(newFolderName.trim());
      setNewFolderName('');
      setShowCreateFolderDialog(false);
    }
  };

  const handleToggleFolder = (folderId: string) => {
    toggleFolder(folderId);
  };

  // Функции для управления папками
  const handleFolderMenuClick = (event: React.MouseEvent<HTMLElement>, folderId: string) => {
    event.stopPropagation();
    setFolderMenuAnchor(event.currentTarget);
    setSelectedFolderId(folderId);
  };

  const handleFolderMenuClose = () => {
    setFolderMenuAnchor(null);
    setSelectedFolderId(null);
  };

  const handleFolderMenuAction = (action: string) => {
    if (!selectedFolderId) {
      return;
    }
    
    // Сохраняем selectedFolderId перед закрытием меню
    const folderIdToAction = selectedFolderId;
    
    if (action === 'rename') {
      const folder = folders.find(f => f.id === folderIdToAction);
      if (folder) {
        setRenamingFolderName(folder.name);
        setShowRenameFolderDialog(true);
      }
    } else if (action === 'archive') {
      handleFolderMenuClose();
      archiveFolder(folderIdToAction);
    } else if (action === 'delete') {
      setShowDeleteFolderDialog(true);
    }
  };

  const handleRenameFolder = () => {
    if (selectedFolderId && renamingFolderName.trim()) {
      updateFolder(selectedFolderId, renamingFolderName.trim());
      setRenamingFolderName('');
      setShowRenameFolderDialog(false);
      handleFolderMenuClose(); // Закрываем меню после переименования
    }
  };

  const handleDeleteFolder = () => {
    if (!selectedFolderId) {
      return;
    }
    
    const folder = folders.find(f => f.id === selectedFolderId);
    if (!folder) {
      return;
    }
    
    if (deleteWithContent) {
      // Удаляем папку со всем содержимым
      folder.chatIds.forEach(chatId => {
        deleteChat(chatId);
      });
    } else {
      // Перемещаем чаты в "Все чаты" (убираем из папки)
      folder.chatIds.forEach(chatId => {
        moveChatToFolder(chatId, null);
      });
    }
    
    // Удаляем папку
    deleteFolder(selectedFolderId);
    setShowDeleteFolderDialog(false);
    setDeleteWithContent(false);
    setSelectedFolderId(null);
    handleFolderMenuClose(); // Закрываем меню после удаления
  };

  // Функции для работы с проектами
  const handleProjectMenuClick = (event: React.MouseEvent<HTMLElement>, projectId: string) => {
    event.stopPropagation();
    setProjectMenuAnchor(event.currentTarget);
    setSelectedProjectId(projectId);
  };

  const handleProjectMenuClose = () => {
    setProjectMenuAnchor(null);
    setSelectedProjectId(null);
  };

  const handleProjectMenuAction = (action: string) => {
    if (!selectedProjectId) {
      return;
    }
    
    const projectIdToAction = selectedProjectId;
    
    switch (action) {
      case 'edit':
        setProjectIdToEdit(projectIdToAction);
        handleProjectMenuClose();
        setShowEditProjectModal(true);
        break;
      case 'delete':
        handleProjectMenuClose();
        setSelectedProjectId(projectIdToAction);
        setShowDeleteProjectDialog(true);
        break;
      default:
        handleProjectMenuClose();
        break;
    }
  };

  const handleConfirmDeleteProject = () => {
    if (selectedProjectId) {
      deleteProject(selectedProjectId);
      setShowDeleteProjectDialog(false);
      setSelectedProjectId(null);
    }
  };

  return (
    <Drawer
      variant="persistent"
      anchor="left"
      open={true}
      sx={{
        width: open ? 240 : 64,
        flexShrink: 0,
        transition: 'width 0.3s ease',
        '& .MuiDrawer-paper': {
          width: open ? 240 : 64,
          boxSizing: 'border-box',
          ...getSidebarChromeSx(sidebarPanelBg),
          ...getSidebarForcedContrastSx(sidebarPanelBg),
          borderRight: '1px solid var(--sidebar-border-color, rgba(255,255,255,0.08))',
          transition: 'width 0.3s ease, background 0.3s ease, color 0.3s ease',
          overflowX: 'hidden',
          display: 'flex',
          flexDirection: 'column',
        },
      }}
    >
      {/* Заголовок */}
      <Box
        sx={{
          // Одинаковая вертикальная сетка в широком и узком режиме — без «прыжка» при сворачивании
          px: open ? 2 : 1,
          py: 1.5,
          display: 'flex',
          alignItems: 'center',
          justifyContent: open ? 'space-between' : 'center',
          background: 'transparent',
          minHeight: 64,
          boxSizing: 'border-box',
        }}
      >
        {open && (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
            <Box
              sx={{
                width: 38,
                height: 38,
                borderRadius: '50%',
                overflow: 'hidden',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                flexShrink: 0,
              }}
            >
              <Box
                component="img"
                src="/astra.png"
                alt="Astra"
                sx={{
                  width: '70%',
                  height: '70%',
                  objectFit: 'cover',
                  transform: 'scale(1.2)',
                }}
              />
            </Box>
            <Box>
              <Typography variant="h6" fontWeight="bold">
                AstraChat
              </Typography>
            </Box>
          </Box>
        )}
        <Tooltip title={open ? 'Свернуть панель' : 'Открыть панель'} placement="right">
          <IconButton
            onClick={onToggle}
            sx={{
              color: 'var(--sidebar-fg, #ffffff)',
              opacity: 1,
              width: 40,
              height: 40,
              borderRadius: 1,
              p: 0,
              '&:hover': {
                backgroundColor: getSidebarPanelHoverBackground(sidebarPanelBg),
                opacity: 1,
              },
            }}
          >
            <SidebarRailMenuGlyph side="left" />
          </IconButton>
        </Tooltip>
      </Box>

      {open && (
        <>
          {/* «Новый чат» и «Поиск» — те же ListItem + ListItemButton, отступы и mb, что у строк чатов */}
          <List disablePadding sx={{ px: 1, pt: 0, pb: 0 }}>
            <ListItem disablePadding sx={{ mb: 0.5 }}>
              <ListItemButton
                onClick={handleCreateChat}
                onMouseEnter={() => setNewChatShortcutHover(true)}
                onMouseLeave={() => setNewChatShortcutHover(false)}
                sx={{
                  ...SIDEBAR_CHAT_ROW_LIST_ITEM_BUTTON_SX,
                  pl: SIDEBAR_ICON_LEADING_PL,
                  pr: SIDEBAR_CONTROL_PX,
                  position: 'relative',
                  alignItems: 'center',
                }}
              >
                <ListItemIcon sx={SIDEBAR_LIST_LEADING_ICON_SX}>
                  <SidebarRailAddIcon sx={SIDEBAR_LIST_ICON_SX} />
                </ListItemIcon>
                <ListItemText
                  sx={{ flex: 1, minWidth: 0, my: 0, pr: 5 }}
                  primary={
                    <Typography
                      variant="body2"
                      sx={{
                        color: 'white',
                        fontWeight: 400,
                        fontSize: '0.8rem',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      Новый чат
                    </Typography>
                  }
                />
                <Typography
                  component="span"
                  sx={{
                    position: 'absolute',
                    right: 8,
                    top: '50%',
                    transform: 'translateY(-50%)',
                    fontSize: '0.7rem',
                    fontWeight: 600,
                    color: 'rgba(255,255,255,0.85)',
                    opacity: newChatShortcutHover ? 0.65 : 0,
                    transition: 'opacity 0.12s ease',
                    pointerEvents: 'none',
                    whiteSpace: 'nowrap',
                    letterSpacing: '0.02em',
                  }}
                >
                  {format(bindings.newChat)}
                </Typography>
              </ListItemButton>
            </ListItem>
            <ListItem disablePadding sx={{ mb: 0.5 }}>
              <ListItemButton
                component="div"
                onMouseEnter={() => setSearchFieldHover(true)}
                onMouseLeave={() => setSearchFieldHover(false)}
                sx={{
                  ...SIDEBAR_CHAT_ROW_LIST_ITEM_BUTTON_SX,
                  pl: SIDEBAR_ICON_LEADING_PL,
                  pr: SIDEBAR_CONTROL_PX,
                  display: 'flex',
                  flexDirection: 'row',
                  alignItems: 'center',
                  cursor: 'text',
                }}
              >
                <ListItemIcon sx={SIDEBAR_LIST_LEADING_ICON_SX}>
                  <SidebarRailSearchIcon sx={SIDEBAR_LIST_ICON_SX} />
                </ListItemIcon>
                <TextField
                  inputRef={searchInputRef}
                  fullWidth
                  placeholder="Поиск в чатах"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  variant="standard"
                  size="small"
                  InputProps={{
                    disableUnderline: true,
                    endAdornment: (
                      <InputAdornment position="end" sx={{ maxHeight: 'none', mr: 0 }}>
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
                          <Typography
                            component="span"
                            sx={{
                              fontSize: '0.7rem',
                              fontWeight: 600,
                              color: 'rgba(255,255,255,0.85)',
                              opacity: searchFieldHover ? 0.65 : 0,
                              maxWidth: searchFieldHover ? 120 : 0,
                              overflow: 'hidden',
                              transition: 'opacity 0.12s ease, max-width 0.15s ease',
                              pointerEvents: 'none',
                              whiteSpace: 'nowrap',
                              letterSpacing: '0.02em',
                            }}
                          >
                            {format(bindings.searchChats)}
                          </Typography>
                          {useFoldersMode ? (
                            <Tooltip title="Создать папку">
                              <IconButton
                                size="small"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  setShowCreateFolderDialog(true);
                                }}
                                sx={{ color: '#ffffff', p: 0.5, '& .MuiSvgIcon-root': { fontSize: '1.25rem' } }}
                              >
                                <AddFolderIcon />
                              </IconButton>
                            </Tooltip>
                          ) : null}
                        </Box>
                      </InputAdornment>
                    ),
                    sx: {
                      color: 'white',
                      fontSize: '0.8rem',
                      '& .MuiInputBase-input': {
                        color: 'white',
                        fontSize: '0.8rem',
                        py: 0.25,
                        '&::placeholder': {
                          color: 'rgba(255,255,255,0.7)',
                          opacity: 1,
                        },
                      },
                    },
                  }}
                  sx={{
                    flex: 1,
                    minWidth: 0,
                    m: 0,
                    '& .MuiInputBase-root': {
                      mt: 0,
                    },
                  }}
                />
              </ListItemButton>
            </ListItem>
            <ListItem disablePadding sx={{ mb: 0.5, display: 'block' }}>
              <ListItemButton
                onClick={() => navigate('/creations')}
                sx={{
                  ...SIDEBAR_CHAT_ROW_LIST_ITEM_BUTTON_SX,
                  pl: SIDEBAR_ICON_LEADING_PL,
                  pr: SIDEBAR_CONTROL_PX,
                  alignItems: 'center',
                  backgroundColor:
                    location.pathname === '/creations' ? 'rgba(255,255,255,0.15)' : 'transparent',
                  '&:hover': {
                    backgroundColor:
                      location.pathname === '/creations'
                        ? 'rgba(255,255,255,0.2)'
                        : 'rgba(255,255,255,0.08)',
                  },
                }}
              >
                <ListItemIcon sx={SIDEBAR_LIST_LEADING_ICON_SX}>
                  <CollectionsBookmarkOutlinedIcon sx={SIDEBAR_LIST_ICON_SX} />
                </ListItemIcon>
                <ListItemText
                  sx={{ flex: 1, minWidth: 0, my: 0 }}
                  primary={
                    <Typography variant="body2" sx={{ color: 'white', fontSize: '0.8rem' }}>
                      Моё творение
                    </Typography>
                  }
                />
                <ChevronRightIcon sx={{ fontSize: '1rem', color: 'rgba(255,255,255,0.55)', flexShrink: 0 }} />
              </ListItemButton>
              {creationPreviews.length > 0 ? (
                <Box
                  sx={{
                    display: 'flex',
                    gap: 0.5,
                    mt: 0.5,
                    flexWrap: 'nowrap',
                    pl: 3.25,
                  }}
                >
                  {creationPreviews.map((item) => (
                    <Box
                      key={item.id}
                      sx={{
                        width: 52,
                        height: 52,
                        flexShrink: 0,
                        borderRadius: 1,
                        overflow: 'hidden',
                        bgcolor: 'rgba(255,255,255,0.08)',
                      }}
                    >
                      {item.preview_url ? (
                        <AuthenticatedInlineImage
                          src={item.preview_url}
                          alt={item.prompt || ''}
                          sx={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
                        />
                      ) : null}
                    </Box>
                  ))}
                </Box>
              ) : null}
            </ListItem>
          </List>
        </>
      )}

      {/* Кнопки в свернутом состоянии — те же ListItem + ListItemButton, что у строк чатов */}
      {!open && (
        <>
          <List disablePadding sx={{ px: 1, pt: 0, pb: 1, width: '100%', boxSizing: 'border-box' }}>
            <SidebarRailCollapsedAction
              variant="newChat"
              panelIsLight={isSidebarPanelLight(sidebarPanelBg)}
              enterIndex={0}
              onClick={handleCreateChat}
              icon={<SidebarRailAddIcon sx={SIDEBAR_LIST_ICON_SX} />}
              tooltipTitle={
                <SidebarRailShortcutTooltip title="Новый чат" shortcut={format(bindings.newChat)} />
              }
            />
            <SidebarRailCollapsedAction
              variant="search"
              panelIsLight={isSidebarPanelLight(sidebarPanelBg)}
              enterIndex={1}
              onClick={() => {
                onToggle();
                setTimeout(() => {
                  searchInputRef.current?.focus();
                }, 300);
              }}
              icon={<SidebarRailSearchIcon sx={SIDEBAR_LIST_ICON_SX} />}
              tooltipTitle={
                <SidebarRailShortcutTooltip title="Поиск в чатах" shortcut={format(bindings.searchChats)} />
              }
            />
            <SidebarRailCollapsedAction
              variant="creations"
              panelIsLight={isSidebarPanelLight(sidebarPanelBg)}
              enterIndex={2}
              onClick={() => navigate('/creations')}
              icon={<CollectionsBookmarkOutlinedIcon sx={SIDEBAR_LIST_ICON_SX} />}
              tooltipTitle="Моё творение"
              active={location.pathname === '/creations'}
            />
          </List>

          {/* Кнопка "Скрыть панель" — та же стилистика, что на правом сайдбаре (fixed по центру высоты) */}
          {onHide && (
            <Box sx={{
              position: 'fixed',
              left: 0,
              top: '50%',
              transform: 'translateY(-50%)',
              width: 64,
              display: 'flex',
              justifyContent: 'center',
              alignItems: 'center',
              zIndex: 1200,
            }}>
              <Tooltip title="Скрыть панель" placement="right">
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
                  <ChevronLeftIcon />
                </IconButton>
              </Tooltip>
            </Box>
          )}

          {/* Кнопка пользователя внизу (как в раскрытом состоянии) */}
          <Box sx={{ mt: 'auto', px: 0, py: 1, display: 'flex', justifyContent: 'center', width: '100%' }}>
            <Tooltip title={user ? (user.full_name || user.username) : 'Меню'} placement="right">
              <IconButton
                onClick={handleMenuClick}
                sx={{
                  color: 'white',
                  opacity: 1,
                  width: 32,
                  height: 32,
                  borderRadius: 1,
                  p: 0,
                  '&:hover': {
                    backgroundColor: isDarkMode ? 'rgba(255, 255, 255, 0.1)' : 'rgba(0, 0, 0, 0.1)',
                    opacity: 1,
                  },
                }}
              >
                <Avatar
                  sx={{
                    width: 40,
                    height: 40,
                    bgcolor: 'primary.main',
                    fontSize: 14,
                    fontWeight: 600,
                  }}
                >
                  {user ? user.username.charAt(0).toUpperCase() : 'М'}
                </Avatar>
              </IconButton>
            </Tooltip>
          </Box>
        </>
      )}

      {/* Раздел Проекты */}
      {!useFoldersMode && open && (
        <Box sx={{ px: 1, mb: 1 }}>
          <Box
            onClick={() => setProjectsExpanded(!projectsExpanded)}
            sx={{
              ...SIDEBAR_SECTION_HEADER_ROW_SX,
              cursor: 'pointer',
            }}
          >
            <Typography variant="subtitle2" sx={SIDEBAR_SECTION_HEADER_TITLE_SX}>
              Проекты
            </Typography>
            <ExpandMoreIcon
              sx={{
                ...SIDEBAR_SECTION_CHEVRON_SX,
                transform: projectsExpanded ? 'rotate(0deg)' : 'rotate(-90deg)',
                transition: 'transform 0.2s ease',
              }}
            />
          </Box>
          {projectsExpanded && (
            <List sx={{ py: 0 }}>
              <Box sx={{ mb: 0.5 }}>
                <ListItem disablePadding>
                  <Box
                    role="button"
                    tabIndex={0}
                    onClick={() => setShowNewProjectModal(true)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        setShowNewProjectModal(true);
                      }
                    }}
                    sx={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      width: '100%',
                      pl: SIDEBAR_ICON_LEADING_PL,
                      pr: SIDEBAR_CONTROL_PX,
                      py: 0,
                      minHeight: SIDEBAR_CONTROL_HEIGHT_PX,
                      borderRadius: 2,
                      cursor: 'pointer',
                      '&:hover': {
                        backgroundColor: 'rgba(255,255,255,0.05)',
                      },
                      transition: 'background-color 0.2s ease',
                    }}
                  >
                    <Box
                      sx={{
                        display: 'flex',
                        alignItems: 'center',
                        flex: 1,
                        minWidth: 0,
                      }}
                    >
                      <ListItemIcon
                        sx={{
                          ...SIDEBAR_LIST_LEADING_ICON_SX,
                          '& .MuiSvgIcon-root': { fontSize: '1.375rem' },
                        }}
                      >
                        <AddFolderIcon />
                      </ListItemIcon>
                      <ListItemText
                        primary={
                          <Typography
                            variant="body2"
                            sx={{
                              color: 'white',
                              fontWeight: 400,
                              overflow: 'hidden',
                              textOverflow: 'ellipsis',
                              whiteSpace: 'nowrap',
                              fontSize: '0.8rem',
                            }}
                          >
                            Новый проект
                          </Typography>
                        }
                      />
                    </Box>
                  </Box>
                </ListItem>
              </Box>
              {projects.map((project) => {
                const renderProjectIcon = () => {
                  const sizePx = SIDEBAR_PROJECT_AVATAR_SIZE;
                  const iconColor = project.iconColor || '#9ca3af';
                  // Глиф растягиваем примерно до «старого круга»
                  const glyphPx = Math.max(16, Math.round(sizePx * 1.12));
                  const glyphSx = getProjectIconGlyphSx(glyphPx, iconColor);
                  // Бокс = ширина колонки ListItemIcon (sizePx + 4), чтобы зазор
                  // «глиф → текст» совпадал с кнопками «Новый чат» / «Поиск».
                  const boxPx = sizePx + 4;
                  const iconWrapSx = {
                    width: `${boxPx}px`,
                    height: `${boxPx}px`,
                    display: 'flex' as const,
                    alignItems: 'center',
                    justifyContent: 'center',
                    color: iconColor,
                  };
                  if (project.iconType === 'emoji' && project.icon) {
                    return (
                      <Box
                        sx={{
                          ...iconWrapSx,
                          fontSize: `${glyphPx}px`,
                          lineHeight: 1,
                          transform: 'translateY(-0.25px)',
                        }}
                      >
                        {project.icon}
                      </Box>
                    );
                  }
                  if (project.iconType === 'icon' && project.icon) {
                    const IconComponent = projectIconMap[project.icon] || FolderIcon;
                    return (
                      <Box sx={iconWrapSx}>
                        <IconComponent sx={{ ...glyphSx, fontSize: `${glyphPx}px`, color: 'currentColor' }} />
                      </Box>
                    );
                  }
                  return (
                    <Box sx={iconWrapSx}>
                      <FolderIcon sx={{ ...glyphSx, fontSize: `${glyphPx}px`, color: 'currentColor' }} />
                    </Box>
                  );
                };

                const projectChats = getProjectChats(project.id);
                const isExpanded = expandedProjects.has(project.id);
                
                return (
                  <Box key={project.id} sx={{ mb: 0.5 }}>
                    <ListItem disablePadding>
                      <Box
                        sx={{
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'space-between',
                          width: '100%',
                          pl: SIDEBAR_ICON_LEADING_PL,
                          pr: SIDEBAR_CONTROL_PX,
                          py: 0,
                          minHeight: SIDEBAR_CONTROL_HEIGHT_PX,
                          borderRadius: 2,
                          '&:hover': {
                            backgroundColor: 'rgba(255,255,255,0.05)',
                          },
                          transition: 'background-color 0.2s ease',
                        }}
                      >
                        <Box
                          onClick={() => navigate(`/project/${project.id}`)}
                          sx={{
                            display: 'flex',
                            alignItems: 'center',
                            flex: 1,
                            cursor: 'pointer',
                            minWidth: 0,
                          }}
                        >
                          <ListItemIcon sx={SIDEBAR_LIST_LEADING_ICON_SX}>
                            {renderProjectIcon()}
                          </ListItemIcon>
                          <ListItemText
                            primary={
                              <Typography
                                variant="body2"
                                sx={{
                                  color: 'white',
                                  fontWeight: 400,
                                  overflow: 'hidden',
                                  textOverflow: 'ellipsis',
                                  whiteSpace: 'nowrap',
                                  fontSize: '0.8rem',
                                }}
                              >
                                {project.name}
                              </Typography>
                            }
                          />
                        </Box>
                        <IconButton
                          size="small"
                          onClick={(e) => {
                            e.stopPropagation();
                            handleToggleProject(project.id);
                          }}
                          sx={{ color: '#ffffff', p: 0.5, mr: 0.25, '& .MuiSvgIcon-root': { fontSize: '1.25rem' } }}
                          aria-label={isExpanded ? 'Свернуть чаты проекта' : 'Развернуть чаты проекта'}
                        >
                          <ExpandMoreIcon
                            sx={{
                              ...SIDEBAR_SECTION_CHEVRON_SX,
                              transform: isExpanded ? 'rotate(0deg)' : 'rotate(-90deg)',
                              transition: 'transform 0.2s ease',
                            }}
                          />
                        </IconButton>
                        <IconButton
                          size="small"
                          onClick={(e) => {
                            e.stopPropagation();
                            setProjectMenuAnchor(e.currentTarget);
                            setSelectedProjectId(project.id);
                          }}
                          sx={{ color: '#ffffff', p: 0.5, '& .MuiSvgIcon-root': { fontSize: '1.25rem' } }}
                        >
                          <MoreVertIcon />
                        </IconButton>
                      </Box>
                    </ListItem>
                    {isExpanded && projectChats.length > 0 && (
                      <List sx={{ py: 0, pl: 2 }}>
                        {projectChats.map((chat) => {
                          return (
                            <ListItem key={chat.id} disablePadding sx={{ mb: 0.5 }}>
                              <ListItemButton
                                onClick={(e) => {
                                  if (editingChatId === chat.id) {
                                    e.stopPropagation();
                                    return;
                                  }
                                  handleSelectChat(chat.id);
                                }}
                                sx={{
                                  borderRadius: 2,
                                  backgroundColor: state.currentChatId === chat.id ? 'rgba(255,255,255,0.15)' : 'transparent',
                                  '&:hover': {
                                    backgroundColor: state.currentChatId === chat.id ? 'rgba(255,255,255,0.2)' : 'rgba(255,255,255,0.08)',
                                  },
                                  transition: 'all 0.2s ease',
                                  py: 0,
                                  minHeight: SIDEBAR_CONTROL_HEIGHT_PX,
                                  px: SIDEBAR_CONTROL_PX,
                                }}
                              >
                              <ListItemText
                                primary={
                                  editingChatId === chat.id ? (
                                    <TextField
                                      value={editingTitle}
                                      onChange={(e) => setEditingTitle(e.target.value)}
                                      onBlur={handleSaveEdit}
                                      onKeyDown={handleKeyPress}
                                      onClick={(e) => e.stopPropagation()}
                                      autoFocus
                                      size="small"
                                      fullWidth
                                      sx={{
                                        '& .MuiInputBase-input': {
                                          color: 'white',
                                          fontSize: '0.8rem',
                                          py: 0.5,
                                        },
                                        '& .MuiOutlinedInput-notchedOutline': {
                                          borderColor: 'rgba(255,255,255,0.3)',
                                        },
                                        '&:hover .MuiOutlinedInput-notchedOutline': {
                                          borderColor: 'rgba(255,255,255,0.5)',
                                        },
                                        '& .MuiOutlinedInput-root': {
                                          '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
                                            borderColor: 'rgba(255,255,255,0.7)',
                                          },
                                        },
                                      }}
                                    />
                                  ) : (
                                    <Typography
                                      variant="body2"
                                      sx={{
                                        color: 'white',
                                        fontWeight: state.currentChatId === chat.id ? 600 : 400,
                                        overflow: 'hidden',
                                        textOverflow: 'ellipsis',
                                        whiteSpace: 'nowrap',
                                        fontSize: '0.8rem',
                                      }}
                                    >
                                      {chat.title}
                                    </Typography>
                                  )
                                }
                              />
                              {!editingChatId && (
                                <IconButton
                                  size="small"
                                  onClick={(e) => handleChatMenuClick(e, chat.id)}
                                  sx={{ color: '#ffffff', p: 0.5, '& .MuiSvgIcon-root': { fontSize: '1.25rem' } }}
                                >
                                  <MoreVertIcon />
                                </IconButton>
                              )}
                              </ListItemButton>
                            </ListItem>
                          );
                        })}
                      </List>
                    )}
                  </Box>
                );
              })}
            </List>
          )}
        </Box>
      )}

      {/* Список чатов */}
      {open && (
      <Box
        sx={{
          flexGrow: 1,
          overflow: 'auto',
          ...SIDEBAR_HIDE_SCROLLBAR_SX,
        }}
      >
        <Box sx={{ p: 1 }}>
          {/* Отображение папок - только если включен режим папок */}
          {useFoldersMode && (
            <>
              {/* Отображение папки "Закреплено" первой, если она существует */}
              {folders.find(f => f.name === 'Закреплено') && (() => {
            const pinnedFolder = folders.find(f => f.name === 'Закреплено');
            if (!pinnedFolder) return null;
            return (
              <Box key={pinnedFolder.id} sx={{ mb: 1 }}>
                <Box sx={SIDEBAR_SECTION_HEADER_ROW_SX}>
                  <Box
                    onClick={() => handleToggleFolder(pinnedFolder.id)}
                    sx={{
                      display: 'flex',
                      alignItems: 'center',
                      flex: 1,
                      cursor: 'pointer',
                    }}
                  >
                    <Typography variant="subtitle2" sx={SIDEBAR_SECTION_HEADER_TITLE_SX}>
                      {pinnedFolder.name}
                    </Typography>
                    <ExpandMoreIcon
                      sx={{
                        ...SIDEBAR_SECTION_CHEVRON_SX,
                        transform: pinnedFolder.expanded ? 'rotate(0deg)' : 'rotate(-90deg)',
                        transition: 'transform 0.2s ease',
                        ml: 1,
                      }}
                    />
                  </Box>
                  <IconButton
                    size="small"
                    onClick={(e) => handleFolderMenuClick(e, pinnedFolder.id)}
                    sx={{ color: '#ffffff', p: 0.5, '& .MuiSvgIcon-root': { fontSize: '1.25rem' } }}
                  >
                    <MoreVertIcon />
                  </IconButton>
                </Box>
                {pinnedFolder.expanded && (
                  <List sx={{ py: 0 }}>
                    {(() => {
                      const filteredFolderChats = pinnedFolder.chatIds
                        .map(chatId => ({ chatId, chat: state.chats.find(c => c.id === chatId) }))
                        .filter(({ chat }) => {
                          if (!chat) return false;
                          if (chat.isArchived) return false;
                          if (!searchQuery.trim()) return true;
                          return chat.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
                                 chat.messages.some(msg => 
                                   msg.content.toLowerCase().includes(searchQuery.toLowerCase())
                                 );
                        });
                      
                      if (filteredFolderChats.length === 0 && searchQuery.trim()) {
                        return (
                          <Typography variant="body2" sx={{ px: 2, py: 2, opacity: 0.6, textAlign: 'center', fontSize: '0.8rem' }}>
                            Ничего не найдено
                          </Typography>
                        );
                      }
                      
                      return filteredFolderChats.map(({ chatId, chat }) => {
                        if (!chat) return null;
                        return (
                        <ListItem key={chatId} disablePadding sx={{ mb: 0.5 }}>
                          <ListItemButton
                            onClick={(e) => {
                              if (editingChatId === chatId) {
                                e.stopPropagation();
                                return;
                              }
                              handleSelectChat(chatId);
                            }}
                            sx={{
                              borderRadius: 2,
                              backgroundColor: state.currentChatId === chatId ? 'rgba(255,255,255,0.15)' : 'transparent',
                              '&:hover': {
                                backgroundColor: state.currentChatId === chatId ? 'rgba(255,255,255,0.2)' : 'rgba(255,255,255,0.08)',
                              },
                              transition: 'all 0.2s ease',
                              py: 0,
                              minHeight: SIDEBAR_CONTROL_HEIGHT_PX,
                              px: SIDEBAR_CONTROL_PX,
                            }}
                          >
                            <ListItemText
                              primary={
                                editingChatId === chatId ? (
                                  <TextField
                                    value={editingTitle}
                                    onChange={(e) => setEditingTitle(e.target.value)}
                                    onBlur={handleSaveEdit}
                                    onKeyDown={handleKeyPress}
                                    onClick={(e) => e.stopPropagation()}
                                    autoFocus
                                    size="small"
                                    fullWidth
                                    sx={{
                                      '& .MuiInputBase-input': {
                                        color: 'white',
                                        fontSize: '0.8rem',
                                        py: 0.5,
                                      },
                                      '& .MuiOutlinedInput-notchedOutline': {
                                        borderColor: 'rgba(255,255,255,0.3)',
                                      },
                                      '&:hover .MuiOutlinedInput-notchedOutline': {
                                        borderColor: 'rgba(255,255,255,0.5)',
                                      },
                                      '& .MuiOutlinedInput-root': {
                                        '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
                                          borderColor: 'rgba(255,255,255,0.7)',
                                        },
                                      },
                                    }}
                                  />
                                ) : (
                                  <Typography
                                    variant="body2"
                                    sx={{
                                      color: 'white',
                                      fontWeight: state.currentChatId === chatId ? 600 : 400,
                                      overflow: 'hidden',
                                      textOverflow: 'ellipsis',
                                      whiteSpace: 'nowrap',
                                      fontSize: '0.8rem',
                                    }}
                                  >
                                    {chat.title}
                                  </Typography>
                                )
                              }
                            />
                            {!editingChatId && (
                              <IconButton
                                size="small"
                                onClick={(e) => handleChatMenuClick(e, chatId)}
                                sx={{ color: '#ffffff', p: 0.5, '& .MuiSvgIcon-root': { fontSize: '1.25rem' } }}
                              >
                                <MoreVertIcon />
                              </IconButton>
                            )}
                          </ListItemButton>
                        </ListItem>
                        );
                      });
                    })()}
                  </List>
                )}
              </Box>
            );
          })()}
            </>
          )}

          <Box
            onClick={() => setChatsExpanded(!chatsExpanded)}
            sx={{
              ...SIDEBAR_SECTION_HEADER_ROW_SX,
              cursor: 'pointer',
            }}
          >
            <Typography variant="subtitle2" sx={SIDEBAR_SECTION_HEADER_TITLE_SX}>
              Все чаты
            </Typography>
            <ExpandMoreIcon
              sx={{
                ...SIDEBAR_SECTION_CHEVRON_SX,
                transform: chatsExpanded ? 'rotate(0deg)' : 'rotate(-90deg)',
                transition: 'transform 0.2s ease',
              }}
            />
          </Box>
          {chatsExpanded && (
            <>
              {filteredChats.length === 0 ? (
                <Typography variant="body2" sx={{ px: 2, py: 2, opacity: 0.6, textAlign: 'center' }}>
                  {searchQuery ? 'Ничего не найдено' : 'Пока нет чатов'}
                </Typography>
              ) : (
                <List sx={{ py: 0 }}>
                  {filteredChatsByTime.map((section, sectionIdx) => (
                    <React.Fragment key={section.key}>
                      <Typography
                        component="div"
                        variant="caption"
                        sx={{
                          px: 2,
                          pt: sectionIdx === 0 ? 0.25 : 1.25,
                          pb: 0.5,
                          opacity: 0.65,
                          fontSize: '0.7rem',
                          fontWeight: 600,
                          textTransform: 'none',
                          letterSpacing: '0.02em',
                          color: 'rgba(255,255,255,0.85)',
                        }}
                      >
                        {section.label}
                      </Typography>
                      {section.chats.map((chat) => (
                        <ListItem key={chat.id} disablePadding sx={{ mb: 0.5 }}>
                          <ListItemButton
                            onClick={(e) => {
                              if (editingChatId === chat.id) {
                                e.stopPropagation();
                                return;
                              }
                              handleSelectChat(chat.id);
                            }}
                            sx={{
                              borderRadius: 2,
                              backgroundColor: state.currentChatId === chat.id ? 'rgba(255,255,255,0.15)' : 'transparent',
                              '&:hover': {
                                backgroundColor: state.currentChatId === chat.id ? 'rgba(255,255,255,0.2)' : 'rgba(255,255,255,0.08)',
                              },
                              transition: 'all 0.2s ease',
                              py: 0,
                              minHeight: SIDEBAR_CONTROL_HEIGHT_PX,
                              px: SIDEBAR_CONTROL_PX,
                            }}
                          >
                            <ListItemText
                              primary={
                                editingChatId === chat.id ? (
                                  <TextField
                                    value={editingTitle}
                                    onChange={(e) => setEditingTitle(e.target.value)}
                                    onBlur={handleSaveEdit}
                                    onKeyDown={handleKeyPress}
                                    onClick={(e) => e.stopPropagation()}
                                    autoFocus
                                    size="small"
                                    fullWidth
                                    sx={{
                                      '& .MuiInputBase-input': {
                                        color: 'white',
                                        fontSize: '0.875rem',
                                        py: 0.5,
                                      },
                                      '& .MuiOutlinedInput-notchedOutline': {
                                        borderColor: 'rgba(255,255,255,0.3)',
                                      },
                                      '&:hover .MuiOutlinedInput-notchedOutline': {
                                        borderColor: 'rgba(255,255,255,0.5)',
                                      },
                                      '& .MuiOutlinedInput-root': {
                                        '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
                                          borderColor: 'rgba(255,255,255,0.7)',
                                        },
                                      },
                                    }}
                                  />
                                ) : (
                                  <Typography
                                    variant="body2"
                                    sx={{
                                      fontWeight: state.currentChatId === chat.id ? 600 : 400,
                                      overflow: 'hidden',
                                      textOverflow: 'ellipsis',
                                      whiteSpace: 'nowrap',
                                      fontSize: '0.8rem',
                                    }}
                                  >
                                    {chat.title}
                                  </Typography>
                                )
                              }
                            />
                            {!editingChatId && (
                              <IconButton
                                size="small"
                                onClick={(e) => handleChatMenuClick(e, chat.id)}
                                sx={{ color: '#ffffff', p: 0.5, '& .MuiSvgIcon-root': { fontSize: '1.25rem' } }}
                              >
                                <MoreVertIcon />
                              </IconButton>
                            )}
                          </ListItemButton>
                        </ListItem>
                      ))}
                    </React.Fragment>
                  ))}
                </List>
              )}
            </>
          )}

          {/* Отображение остальных папок (кроме "Закреплено") */}
          {folders.filter(f => f.name !== 'Закреплено').map((folder) => (
            <Box key={folder.id} sx={{ mb: 1 }}>
              <Box sx={SIDEBAR_SECTION_HEADER_ROW_SX}>
                <Box
                  onClick={() => handleToggleFolder(folder.id)}
                  sx={{
                    display: 'flex',
                    alignItems: 'center',
                    flex: 1,
                    cursor: 'pointer',
                  }}
                >
                  <Typography variant="subtitle2" sx={SIDEBAR_SECTION_HEADER_TITLE_SX}>
                    {folder.name}
                  </Typography>
                  <ExpandMoreIcon
                    sx={{
                      ...SIDEBAR_SECTION_CHEVRON_SX,
                      transform: folder.expanded ? 'rotate(0deg)' : 'rotate(-90deg)',
                      transition: 'transform 0.2s ease',
                      ml: 1,
                    }}
                  />
                </Box>
                <IconButton
                  size="small"
                  onClick={(e) => handleFolderMenuClick(e, folder.id)}
                  sx={{ color: '#ffffff', p: 0.5, '& .MuiSvgIcon-root': { fontSize: '1.25rem' } }}
                >
                  <MoreVertIcon />
                </IconButton>
              </Box>
              {folder.expanded && (
                <List sx={{ py: 0 }}>
                  {(() => {
                    const filteredFolderChats = folder.chatIds
                      .map(chatId => ({ chatId, chat: state.chats.find(c => c.id === chatId) }))
                      .filter(({ chat }) => {
                        if (!chat) return false;
                        // Исключаем архивированные чаты
                        if (chat.isArchived) return false;
                        if (!searchQuery.trim()) return true;
                        return chat.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
                               chat.messages.some(msg => 
                                 msg.content.toLowerCase().includes(searchQuery.toLowerCase())
                               );
                      });
                    
                    if (filteredFolderChats.length === 0 && searchQuery.trim()) {
                      return (
                        <Typography variant="body2" sx={{ px: 2, py: 2, opacity: 0.6, textAlign: 'center', fontSize: '0.8rem' }}>
                          Ничего не найдено
                        </Typography>
                      );
                    }
                    
                    return filteredFolderChats.map(({ chatId, chat }) => {
                      if (!chat) return null;
                      return (
                      <ListItem key={chatId} disablePadding sx={{ mb: 0.5 }}>
                        <ListItemButton
                          onClick={(e) => {
                            // Не открываем чат, если идет редактирование
                            if (editingChatId === chatId) {
                              e.stopPropagation();
                              return;
                            }
                            handleSelectChat(chatId);
                          }}
                          sx={{
                            borderRadius: 2,
                            backgroundColor: state.currentChatId === chatId ? 'rgba(255,255,255,0.15)' : 'transparent',
                            '&:hover': {
                              backgroundColor: state.currentChatId === chatId ? 'rgba(255,255,255,0.2)' : 'rgba(255,255,255,0.08)',
                            },
                            transition: 'all 0.2s ease',
                            py: 0,
                            minHeight: SIDEBAR_CONTROL_HEIGHT_PX,
                            px: SIDEBAR_CONTROL_PX,
                          }}
                        >
                          <ListItemText
                            primary={
                              editingChatId === chatId ? (
                                <TextField
                                  value={editingTitle}
                                  onChange={(e) => setEditingTitle(e.target.value)}
                                  onBlur={handleSaveEdit}
                                  onKeyDown={handleKeyPress}
                                  onClick={(e) => e.stopPropagation()}
                                  autoFocus
                                  size="small"
                                  fullWidth
                                  sx={{
                                    '& .MuiInputBase-input': {
                                      color: 'white',
                                      fontSize: '0.8rem',
                                      py: 0.5,
                                    },
                                    '& .MuiOutlinedInput-notchedOutline': {
                                      borderColor: 'rgba(255,255,255,0.3)',
                                    },
                                    '&:hover .MuiOutlinedInput-notchedOutline': {
                                      borderColor: 'rgba(255,255,255,0.5)',
                                    },
                                    '& .MuiOutlinedInput-root': {
                                      '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
                                        borderColor: 'rgba(255,255,255,0.7)',
                                      },
                                    },
                                  }}
                                />
                              ) : (
                                <Typography
                                  variant="body2"
                                  sx={{
                                    color: 'white',
                                    fontWeight: state.currentChatId === chatId ? 600 : 400,
                                    overflow: 'hidden',
                                    textOverflow: 'ellipsis',
                                    whiteSpace: 'nowrap',
                                    fontSize: '0.8rem',
                                  }}
                                >
                                  {chat.title}
                                </Typography>
                              )
                            }
                          />
                          {!editingChatId && (
                            <IconButton
                              size="small"
                              onClick={(e) => handleChatMenuClick(e, chatId)}
                              sx={{ color: '#ffffff', p: 0.5, '& .MuiSvgIcon-root': { fontSize: '1.25rem' } }}
                            >
                              <MoreVertIcon />
                            </IconButton>
                          )}
                        </ListItemButton>
                      </ListItem>
                      );
                    });
                  })()}
                </List>
              )}
            </Box>
          ))}
        </Box>
      </Box>
      )}

      {/* Навигационное меню */}
      {open && (
      <List sx={{ flexGrow: 1, px: 1 }}>
        {menuItems.map((item) => {
          const Icon = item.icon;
          const isActive = location.pathname === item.path;
          
          return (
            <ListItem key={item.path} disablePadding sx={{ mb: 0.5 }}>
              <ListItemButton
                onClick={() => handleNavigation(item.path)}
                sx={{
                  borderRadius: 2,
                  backgroundColor: isActive ? 'rgba(255,255,255,0.15)' : 'transparent',
                  '&:hover': {
                    backgroundColor: isActive ? 'rgba(255,255,255,0.2)' : 'rgba(255,255,255,0.08)',
                  },
                  transition: 'all 0.2s ease',
                  py: 1,
                  px: 2,
                }}
              >
                <ListItemIcon
                  sx={{
                    color: '#ffffff',
                    minWidth: 32,
                    '& .MuiSvgIcon-root': { fontSize: '1.375rem' },
                  }}
                >
                  <Icon />
                </ListItemIcon>
                <ListItemText 
                  primary={item.label}
                  secondary={item.description}
                  primaryTypographyProps={{
                    fontWeight: isActive ? 600 : 400,
                  }}
                  secondaryTypographyProps={{
                    sx: { opacity: 0.8, fontSize: '0.75rem' }
                  }}
                />
              </ListItemButton>
            </ListItem>
          );
        })}
      </List>
      )}


      {/* Кнопка пользователя внизу */}
      {open && (
      <Box sx={{ p: 1.5, background: 'transparent' }}>
        {user ? (
          <Box
            onClick={handleMenuClick}
            sx={{
              display: 'flex',
              alignItems: 'center',
              gap: 1.5,
              py: 0,
              px: SIDEBAR_CONTROL_PX,
              minHeight: SIDEBAR_CONTROL_HEIGHT_PX,
              borderRadius: SIDEBAR_CONTROL_RADIUS,
              backgroundColor: 'transparent',
              cursor: 'pointer',
              transition: 'background-color 0.2s',
              '&:hover': {
                backgroundColor: 'transparent',
              },
            }}
          >
            <Avatar
              sx={{
                width: 28,
                height: 28,
                bgcolor: 'primary.main',
                fontSize: 14,
              }}
            >
              {user.username.charAt(0).toUpperCase()}
            </Avatar>
            <Box sx={{ flexGrow: 1, minWidth: 0 }}>
              <Typography variant="body2" fontWeight="600" noWrap sx={{ fontSize: '0.875rem' }}>
                {user.full_name || user.username}
              </Typography>
            </Box>
            <MoreVertIcon sx={{ opacity: 0.5, fontSize: '1.2rem' }} />
          </Box>
        ) : (
          <Button
            fullWidth
            variant="contained"
            startIcon={<MoreVertIcon />}
            onClick={handleMenuClick}
            sx={{
              backgroundColor: 'rgba(255,255,255,0.1)',
              color: 'white',
              '&:hover': {
                backgroundColor: 'rgba(255,255,255,0.2)',
              },
              textTransform: 'none',
              fontWeight: 500,
              py: 0,
              px: SIDEBAR_CONTROL_PX,
              justifyContent: 'flex-start',
              borderRadius: SIDEBAR_CONTROL_RADIUS,
              fontSize: MENU_ACTION_TEXT_SIZE,
              minHeight: SIDEBAR_CONTROL_HEIGHT_PX,
            }}
          >
            Меню
          </Button>
        )}
      </Box>
      )}

      {/* Меню пользователя (та же логика, что у «Модели» и меню чатов) */}
      <Popover
        open={menuOpen}
        anchorEl={anchorEl}
        onClose={handleMenuClose}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
        transformOrigin={{ vertical: 'bottom', horizontal: 'right' }}
        slotProps={{
          paper: {
            sx: {
              mt: 0.5,
              p: 0,
              overflow: 'visible',
              background: 'transparent !important',
              backgroundColor: 'transparent !important',
              boxShadow: 'none !important',
              border: 'none',
            },
          },
        }}
      >
        <Box
          sx={{ position: 'relative' }}
          onMouseEnter={cancelUserSubmenuClose}
          onMouseLeave={scheduleUserSubmenuClose}
        >
          <Box sx={{ ...dropdownPanelSx, width: MENU_COMPACT_PANEL_WIDTH_PX, flexShrink: 0, display: 'flex', flexDirection: 'column' }}>
            <Box sx={{ py: 0.5, px: 0.5 }}>
              <Box
                onMouseEnter={() => {
                  cancelUserSubmenuClose();
                  setUserMenuSubmenu(null);
                }}
                onClick={() => handleMenuAction('settings')}
                sx={{ ...dropdownItemSx, display: 'flex', alignItems: 'center', gap: 1, color: menuItemColor }}
              >
                <SettingsIcon sx={{ fontSize: 22, color: submenuIconColor, flexShrink: 0 }} />
                <Typography sx={{ flex: 1, fontSize: MENU_ACTION_TEXT_SIZE }}>Настройки</Typography>
              </Box>

              <Box
                onMouseEnter={() => {
                  cancelUserSubmenuClose();
                  setUserMenuSubmenu(null);
                }}
                onClick={() => handleMenuAction('archive')}
                sx={{ ...dropdownItemSx, display: 'flex', alignItems: 'center', gap: 1, color: menuItemColor }}
              >
                <ArchiveIcon sx={{ fontSize: 22, color: submenuIconColor, flexShrink: 0 }} />
                <Typography sx={{ flex: 1, fontSize: MENU_ACTION_TEXT_SIZE }}>Архив</Typography>
              </Box>

              <Box
                onMouseEnter={handleUserMenuSubmenuEnter}
                sx={{
                  ...dropdownItemSx,
                  display: 'flex',
                  alignItems: 'center',
                  gap: 1,
                  color: menuItemColor,
                  ...(userMenuSubmenu === 'help' && { backgroundColor: menuItemHover }),
                }}
              >
                <InfoIcon sx={{ fontSize: 22, color: submenuIconColor, flexShrink: 0 }} />
                <Typography sx={{ flex: 1, fontSize: MENU_ACTION_TEXT_SIZE }}>Справка</Typography>
                <ChevronRightIcon sx={{ fontSize: 22, color: submenuChevronColor, flexShrink: 0 }} />
              </Box>

              <Box
                onMouseEnter={() => {
                  cancelUserSubmenuClose();
                  setUserMenuSubmenu(null);
                }}
                onClick={() => handleMenuAction('logout')}
                sx={{ ...dropdownItemSx, display: 'flex', alignItems: 'center', gap: 1, color: menuItemColor }}
              >
                <LogoutIcon sx={{ fontSize: 22, color: submenuIconColor, flexShrink: 0 }} />
                <Typography sx={{ flex: 1, fontSize: MENU_ACTION_TEXT_SIZE }}>Выйти из аккаунта</Typography>
              </Box>
            </Box>
          </Box>

          {userMenuSubmenu === 'help' && (
            <Box
              sx={{
                ...dropdownPanelSx,
                width: MENU_COMPACT_PANEL_WIDTH_PX,
                position: 'absolute',
                left: 'calc(100% + 6px)',
                top: `${userMenuSubmenuOffsetTop}px`,
                display: 'flex',
                flexDirection: 'column',
              }}
              onMouseEnter={cancelUserSubmenuClose}
              onMouseLeave={scheduleUserSubmenuClose}
            >
              <Box sx={{ py: 0.5, px: 0.5 }}>
                <Box
                  onClick={() => {
                    handleMenuClose();
                    setShowHelpDialog(true);
                  }}
                  sx={{ ...dropdownItemSx, display: 'flex', alignItems: 'center', gap: 1, color: menuItemColor }}
                >
                  <HelpOutlineIcon sx={{ fontSize: 22, color: submenuIconColor, flexShrink: 0 }} />
                  <Typography sx={{ flex: 1, fontSize: MENU_ACTION_TEXT_SIZE }}>Помощь</Typography>
                </Box>
                <Box
                  onClick={() => {
                    handleMenuClose();
                    setShowUpdatesDialog(true);
                  }}
                  sx={{ ...dropdownItemSx, display: 'flex', alignItems: 'center', gap: 1, color: menuItemColor }}
                >
                  <UpdatesIcon sx={{ fontSize: 22, color: submenuIconColor, flexShrink: 0 }} />
                  <Typography sx={{ flex: 1, fontSize: MENU_ACTION_TEXT_SIZE }}>Обновления</Typography>
                </Box>
                <Box
                  onClick={() => {
                    handleMenuClose();
                    setShowShortcutsDialog(true);
                  }}
                  sx={{ ...dropdownItemSx, display: 'flex', alignItems: 'center', gap: 1, color: menuItemColor }}
                >
                  <KeyboardIcon sx={{ fontSize: 22, color: submenuIconColor, flexShrink: 0 }} />
                  <Typography sx={{ flex: 1, fontSize: MENU_ACTION_TEXT_SIZE }}>Сочетание клавиш</Typography>
                </Box>
              </Box>
            </Box>
          )}
        </Box>
      </Popover>

      {/* Диалог «Помощь» — тот же стиль, что «Обновления» */}
      <Dialog
        open={showHelpDialog}
        onClose={() => setShowHelpDialog(false)}
        maxWidth={false}
        fullWidth
        BackdropProps={{
          sx: { backgroundColor: isDarkMode ? 'rgba(0,0,0,0.55)' : 'rgba(0,0,0,0.4)' },
        }}
        PaperProps={{ sx: helpDialogPaperSx }}
      >
        <Box sx={{ px: { xs: 2.5, sm: 3.5 }, pt: 2, pb: 0 }}>
          <Box
            sx={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              pb: 0.5,
              borderBottom: `1px solid ${isDarkMode ? 'rgba(255,255,255,0.14)' : 'rgba(0,0,0,0.12)'}`,
            }}
          >
            <Typography
              component="span"
              sx={{
                fontSize: '1.75rem',
                fontWeight: 700,
                color: isDarkMode ? '#ffffff' : '#111',
                lineHeight: 1.15,
              }}
            >
              Помощь
            </Typography>
            <IconButton
              onClick={() => setShowHelpDialog(false)}
              size="small"
              aria-label="Закрыть"
              sx={{
                color: isDarkMode ? 'rgba(255,255,255,0.5)' : 'rgba(0,0,0,0.45)',
                '&:hover': {
                  backgroundColor: isDarkMode ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.04)',
                },
              }}
            >
              <CloseIcon fontSize="small" />
            </IconButton>
          </Box>
        </Box>
        <DialogContent
          sx={{
            px: { xs: 2.5, sm: 3.5 },
            pt: 2.5,
            pb: 3,
            backgroundColor: 'transparent',
          }}
        >
          <Typography
            sx={{
              color: isDarkMode ? 'rgba(255,255,255,0.88)' : 'rgba(0,0,0,0.85)',
              fontSize: '0.9375rem',
              lineHeight: 1.55,
              mb: 1.5,
            }}
          >
            По возникающим вопросам обращаться:
          </Typography>
          <Box
            component="ul"
            sx={{
              m: 0,
              pl: 2.5,
              mb: 2.5,
              color: isDarkMode ? 'rgba(255,255,255,0.88)' : 'rgba(0,0,0,0.85)',
              fontSize: '0.9375rem',
              lineHeight: 1.55,
              '& li': { mb: 0.5 },
            }}
          >
            <li>в jabber или onyx (ссылки ниже)</li>
            <li>по почте: команде AstraChat</li>
          </Box>

          <Typography
            sx={{
              fontSize: '0.8125rem',
              color: isDarkMode ? 'rgba(255,255,255,0.45)' : 'rgba(0,0,0,0.5)',
              mb: 0.75,
            }}
          >
            Onyx
          </Typography>
          <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: 0.75 }}>
            <Link
              href={onyxNewsUrl}
              target="_blank"
              rel="noopener noreferrer"
              underline="hover"
              sx={helpLinkSx}
            >
              AstraChat News
            </Link>
            <Link
              href={onyxSupportUrl}
              target="_blank"
              rel="noopener noreferrer"
              underline="hover"
              sx={helpLinkSx}
            >
              AstraChat Support
            </Link>
          </Box>
        </DialogContent>
      </Dialog>

      {/* Диалог «Обновления» — широкий layout: дата слева, контент справа */}
      <Dialog
        open={showUpdatesDialog}
        onClose={() => setShowUpdatesDialog(false)}
        maxWidth={false}
        fullWidth
        BackdropProps={{
          sx: { backgroundColor: isDarkMode ? 'rgba(0,0,0,0.55)' : 'rgba(0,0,0,0.4)' },
        }}
        PaperProps={{ sx: helpDialogPaperSx }}
      >
        <Box sx={{ px: { xs: 2.5, sm: 3.5 }, pt: 2, pb: 0 }}>
          <Box
            sx={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              pb: 0.5,
              borderBottom: `1px solid ${isDarkMode ? 'rgba(255,255,255,0.14)' : 'rgba(0,0,0,0.12)'}`,
            }}
          >
            <Typography
              component="span"
              sx={{
                fontSize: '1.75rem',
                fontWeight: 700,
                color: isDarkMode ? '#ffffff' : '#111',
                lineHeight: 1.15,
              }}
            >
              Обновления
            </Typography>
            <IconButton
              onClick={() => setShowUpdatesDialog(false)}
              size="small"
              aria-label="Закрыть"
              sx={{
                color: isDarkMode ? 'rgba(255,255,255,0.5)' : 'rgba(0,0,0,0.45)',
                '&:hover': {
                  backgroundColor: isDarkMode ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.04)',
                },
              }}
            >
              <CloseIcon fontSize="small" />
            </IconButton>
          </Box>
        </Box>
        <DialogContent
          sx={{
            px: { xs: 2.5, sm: 3.5 },
            pt: 2.5,
            pb: 3,
            backgroundColor: 'transparent',
          }}
        >
          {/* Блок релиза (ниже можно добавлять новые блоки через такую же полосу) */}
          <Box
            sx={{
              display: 'flex',
              flexDirection: { xs: 'column', sm: 'row' },
              alignItems: 'flex-start',
              gap: { xs: 1.5, sm: 4 },
              pb: 2.75,
            }}
          >
            <Typography
              sx={{
                flexShrink: 0,
                width: { sm: 132 },
                pt: { sm: 0.35 },
                fontSize: '0.8125rem',
                color: isDarkMode ? 'rgba(255,255,255,0.45)' : 'rgba(0,0,0,0.45)',
                whiteSpace: 'nowrap',
              }}
            >
              23 июня, 2026
            </Typography>

            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Typography
                sx={{
                  fontSize: '1.5rem',
                  fontWeight: 700,
                  color: isDarkMode ? '#ffffff' : '#111',
                  mb: 1.75,
                  lineHeight: 1.3,
                }}
              >
                Релиз версии 1.0 AstraChat
              </Typography>

              <Box
                sx={{
                  color: isDarkMode ? 'rgba(255,255,255,0.88)' : 'rgba(0,0,0,0.85)',
                  fontSize: '0.9375rem',
                  lineHeight: 1.55,
                  '& .section-title': {
                    fontWeight: 700,
                    color: isDarkMode ? '#ffffff' : '#111',
                    mt: 1.5,
                    mb: 0.5,
                  },
                  '& .section-title:first-of-type': { mt: 0 },
                  '& ul': { m: 0, pl: 2.5, mb: 0.25 },
                  '& li': { mb: 0.35 },
                }}
              >
                <Typography className="section-title" component="div">
                  Авторизация:
                </Typography>
                <Box component="ul">
                  <li>Добавлена авторизация по LDAP</li>
                  <li>Добавлена авторизация по SSO</li>
                </Box>

                <Typography className="section-title" component="div">
                  Чат:
                </Typography>
                <Box component="ul">
                  <li>Настроена интеграция с LLM моделями домена CORSUR</li>
                  <li>Настроена интеграция с LLM моделями домена PHOENIX</li>
                </Box>

                <Typography className="section-title" component="div">
                  Файлы:
                </Typography>
                <Box component="ul">
                  <li>
                    Реализован функционал по прикреплению текстовых файлов (pdf, doc, docx, xlsx, txt)
                    через кнопку &quot;+&quot; в окне ввода текста
                  </li>
                </Box>
              </Box>

              <Link
                component="button"
                type="button"
                underline="always"
                onClick={() => {
                  setShowUpdatesDialog(false);
                  navigate('/docs/astrachat-release-1.0');
                }}
                sx={{
                  display: 'inline-block',
                  mt: 2.25,
                  border: 'none',
                  background: 'none',
                  cursor: 'pointer',
                  font: 'inherit',
                  p: 0,
                  textAlign: 'left',
                  ...helpLinkSx,
                }}
              >
                Читать подробнее.
              </Link>
            </Box>
          </Box>

          {/* Нижняя полоса — разделитель для следующих релизов */}
          <Box
            sx={{
              height: 0,
              border: 'none',
              borderTop: `1px solid ${isDarkMode ? 'rgba(255,255,255,0.14)' : 'rgba(0,0,0,0.12)'}`,
            }}
          />
        </DialogContent>
      </Dialog>

      {/* Диалог «Сочетание клавиш» */}
      <HotkeysSettingsDialog
        open={showShortcutsDialog}
        onClose={() => setShowShortcutsDialog(false)}
        isDarkMode={isDarkMode}
      />

      {/* Выпадающее меню для чатов — по паттерну «Модели»: левое меню + правое подменю */}
      <Popover
        open={chatMenuOpen}
        anchorEl={chatMenuAnchor}
        onClose={handleChatMenuClose}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
        transformOrigin={{ vertical: 'bottom', horizontal: 'right' }}
        slotProps={{
          paper: {
            sx: {
              mt: 0.5,
              p: 0,
              overflow: 'visible',
              background: 'transparent !important',
              backgroundColor: 'transparent !important',
              boxShadow: 'none !important',
              border: 'none',
            },
          },
        }}
      >
        <Box
          onMouseEnter={cancelChatSubmenuClose}
          onMouseLeave={scheduleChatSubmenuClose}
          sx={{ position: 'relative' }}
        >
          <Box sx={{ ...dropdownPanelSx, width: MENU_COMPACT_PANEL_WIDTH_PX, flexShrink: 0, display: 'flex', flexDirection: 'column' }}>
            <Box sx={{ py: 0.5, px: 0.5 }}>
              <Box
                onMouseEnter={() => {
                  cancelChatSubmenuClose();
                  setChatMenuSubmenu(null);
                }}
                onClick={() => handleChatMenuAction('pin')}
                sx={{ ...dropdownItemSx, display: 'flex', alignItems: 'center', gap: 1, color: menuItemColor }}
              >
                <PushPinIcon sx={{ fontSize: 22, color: submenuIconColor, flexShrink: 0 }} />
                <Typography sx={{ flex: 1, fontSize: MENU_ACTION_TEXT_SIZE }}>
                  {(() => {
                    const chat = selectedChatId ? getChatById(selectedChatId) : null;
                    if (chat?.projectId) return chat.isPinnedInProject ? 'Открепить' : 'Пин';
                    return getChatFolder(selectedChatId || '')?.name === 'Закреплено' ? 'Открепить' : 'Пин';
                  })()}
                </Typography>
              </Box>

              <Box
                onMouseEnter={() => {
                  cancelChatSubmenuClose();
                  setChatMenuSubmenu(null);
                }}
                onClick={() => handleChatMenuAction('edit')}
                sx={{ ...dropdownItemSx, display: 'flex', alignItems: 'center', gap: 1, color: menuItemColor }}
              >
                <EditIcon sx={{ fontSize: 22, color: submenuIconColor, flexShrink: 0 }} />
                <Typography sx={{ flex: 1, fontSize: MENU_ACTION_TEXT_SIZE }}>Переименовать</Typography>
              </Box>

              <Box
                onMouseEnter={(e) => handleChatMenuSubmenuEnter(useFoldersMode ? 'folder' : 'project', e)}
                sx={{
                  ...dropdownItemSx,
                  display: 'flex',
                  alignItems: 'center',
                  gap: 1,
                  color: menuItemColor,
                  ...(chatMenuSubmenu !== null && { backgroundColor: menuItemHover }),
                }}
              >
                <FolderIcon sx={{ fontSize: 22, color: submenuIconColor, flexShrink: 0 }} />
                <Typography sx={{ flex: 1, fontSize: MENU_ACTION_TEXT_SIZE, whiteSpace: 'nowrap' }}>
                  {useFoldersMode ? 'Перейти в папку' : 'Перейти в проект'}
                </Typography>
                <ChevronRightIcon sx={{ fontSize: 22, color: submenuChevronColor, flexShrink: 0 }} />
              </Box>

              <Box
                onMouseEnter={() => {
                  cancelChatSubmenuClose();
                  setChatMenuSubmenu(null);
                }}
                onClick={() => handleChatMenuAction('archive')}
                sx={{ ...dropdownItemSx, display: 'flex', alignItems: 'center', gap: 1, color: menuItemColor }}
              >
                <ArchiveIcon sx={{ fontSize: 22, color: submenuIconColor, flexShrink: 0 }} />
                <Typography sx={{ flex: 1, fontSize: MENU_ACTION_TEXT_SIZE }}>Архив</Typography>
              </Box>

              {!useFoldersMode && selectedChatId && getChatById(selectedChatId)?.projectId && (
                <Box
                  onMouseEnter={() => {
                    cancelChatSubmenuClose();
                    setChatMenuSubmenu(null);
                  }}
                  onClick={() => handleChatMenuAction('removeFromProject')}
                  sx={{ ...dropdownItemSx, display: 'flex', alignItems: 'center', gap: 1, color: menuItemColor }}
                >
                  <FolderIcon sx={{ fontSize: 22, color: submenuIconColor, flexShrink: 0 }} />
                  <Typography sx={{ flex: 1, fontSize: MENU_ACTION_TEXT_SIZE }}>Перенести из проекта</Typography>
                </Box>
              )}

              <Divider sx={{ my: 0.5, borderColor: menuDividerBorder }} />

              <Box
                onMouseEnter={() => {
                  cancelChatSubmenuClose();
                  setChatMenuSubmenu(null);
                }}
                onClick={() => handleChatMenuAction('delete')}
                sx={{
                  ...dropdownItemSx,
                  display: 'flex',
                  alignItems: 'center',
                  gap: 1,
                  color: '#d32f2f',
                  '&:hover': { backgroundColor: 'rgba(211, 47, 47, 0.1)' },
                }}
              >
                <DeleteIcon sx={{ fontSize: 22, color: '#d32f2f', flexShrink: 0 }} />
                <Typography sx={{ flex: 1, fontSize: MENU_ACTION_TEXT_SIZE, color: '#d32f2f' }}>Удалить</Typography>
              </Box>
            </Box>
          </Box>

          {chatMenuSubmenu === 'folder' && (
            <Box
              sx={{
                ...dropdownPanelSx,
                width: MENU_COMPACT_PANEL_WIDTH_PX,
                position: 'absolute',
                left: 'calc(100% + 6px)',
                top: `${chatMenuSubmenuOffsetTop}px`,
                display: 'flex',
                flexDirection: 'column',
              }}
              onMouseEnter={cancelChatSubmenuClose}
              onMouseLeave={scheduleChatSubmenuClose}
            >
              <Box sx={{ maxHeight: 260, overflowY: 'auto', py: 0.5, '&::-webkit-scrollbar': { width: 3 }, '&::-webkit-scrollbar-thumb': { bgcolor: 'rgba(255,255,255,0.12)', borderRadius: 2 } }}>
                <Box
                  onClick={() => {
                    setShowCreateFolderDialog(true);
                    handleChatMenuClose();
                  }}
                  sx={{ ...dropdownItemSx, display: 'flex', alignItems: 'center', gap: 1, color: menuItemColor }}
                >
                  <AddFolderIcon sx={{ fontSize: 22, color: submenuIconColor, flexShrink: 0 }} />
                  <Typography sx={{ flex: 1, fontSize: MENU_ACTION_TEXT_SIZE }}>Создать папку</Typography>
                </Box>

                <Box
                  onClick={() => {
                    if (selectedChatId && getChatFolder(selectedChatId)) {
                      moveChatToFolder(selectedChatId, null);
                      handleChatMenuClose();
                    }
                  }}
                  sx={{
                    ...dropdownItemSx,
                    display: 'flex',
                    alignItems: 'center',
                    gap: 1,
                    color: selectedChatId && !getChatFolder(selectedChatId) ? menuDisabledColor : menuItemColor,
                    pointerEvents: selectedChatId && !getChatFolder(selectedChatId) ? 'none' : 'auto',
                    opacity: selectedChatId && !getChatFolder(selectedChatId) ? 0.55 : 1,
                  }}
                >
                  <ChatIcon sx={{ fontSize: 22, color: submenuIconColor, flexShrink: 0 }} />
                  <Typography sx={{ flex: 1, fontSize: MENU_ACTION_TEXT_SIZE }}>Все чаты</Typography>
                </Box>

                {folders
                  .filter((folder) => {
                    const currentFolder = selectedChatId ? getChatFolder(selectedChatId) : null;
                    return !currentFolder || folder.id !== currentFolder.id;
                  })
                  .map((folder) => (
                    <Box
                      key={folder.id}
                      onClick={() => {
                        if (selectedChatId) {
                          moveChatToFolder(selectedChatId, folder.id);
                          handleChatMenuClose();
                        }
                      }}
                      sx={{ ...dropdownItemSx, display: 'flex', alignItems: 'center', gap: 1, color: menuItemColor }}
                    >
                      <FolderIcon sx={{ fontSize: 22, color: submenuIconColor, flexShrink: 0 }} />
                      <Typography sx={{ flex: 1, fontSize: MENU_ACTION_TEXT_SIZE }}>{folder.name}</Typography>
                    </Box>
                  ))}
              </Box>
            </Box>
          )}

          {chatMenuSubmenu === 'project' && (
            <Box
              sx={{
                ...dropdownPanelSx,
                width: MENU_COMPACT_PANEL_WIDTH_PX,
                position: 'absolute',
                left: 'calc(100% + 6px)',
                top: `${chatMenuSubmenuOffsetTop}px`,
                display: 'flex',
                flexDirection: 'column',
              }}
              onMouseEnter={cancelChatSubmenuClose}
              onMouseLeave={scheduleChatSubmenuClose}
            >
              <Box sx={{ maxHeight: 260, overflowY: 'auto', py: 0.5, '&::-webkit-scrollbar': { width: 3 }, '&::-webkit-scrollbar-thumb': { bgcolor: 'rgba(255,255,255,0.12)', borderRadius: 2 } }}>
                <Box
                  onClick={() => {
                    if (selectedChatId) {
                      setPendingChatIdForProject(selectedChatId);
                      setShowNewProjectModal(true);
                      handleChatMenuClose();
                    }
                  }}
                  sx={{ ...dropdownItemSx, display: 'flex', alignItems: 'center', gap: 1, color: menuItemColor }}
                >
                  <AddFolderIcon sx={{ fontSize: 22, color: submenuIconColor, flexShrink: 0 }} />
                  <Typography sx={{ flex: 1, fontSize: MENU_ACTION_TEXT_SIZE }}>Новый проект</Typography>
                </Box>

                {projects.map((project) => {
                  const chat = selectedChatId ? state.chats.find((c) => c.id === selectedChatId) : null;
                  const isSelected = chat?.projectId === project.id;
                  return (
                    <Box
                      key={project.id}
                      onClick={() => {
                        if (selectedChatId && !isSelected) {
                          moveChatToProject(selectedChatId, project.id);
                          handleChatMenuClose();
                        }
                      }}
                      sx={{
                        ...dropdownItemSx,
                        display: 'flex',
                        alignItems: 'center',
                        gap: 1,
                        color: isSelected ? menuDisabledColor : menuItemColor,
                        pointerEvents: isSelected ? 'none' : 'auto',
                        opacity: isSelected ? 0.55 : 1,
                      }}
                    >
                      <FolderIcon sx={{ fontSize: 22, color: submenuIconColor, flexShrink: 0 }} />
                      <Typography sx={{ flex: 1, fontSize: MENU_ACTION_TEXT_SIZE }}>{project.name}</Typography>
                    </Box>
                  );
                })}
              </Box>
            </Box>
          )}
        </Box>
      </Popover>

      {/* Диалог подтверждения удаления (адаптивный к светлой/тёмной теме, как в проекте) */}
      <Dialog
        open={showDeleteDialog}
        onClose={() => setShowDeleteDialog(false)}
        maxWidth="sm"
        fullWidth
        PaperProps={{
          sx: {
            backgroundColor: isDarkMode ? '#1e1e1e' : '#ffffff',
            color: isDarkMode ? 'white' : '#333',
            borderRadius: 2,
          }
        }}
      >
        <DialogTitle sx={{ color: isDarkMode ? 'white' : '#333', fontWeight: 'bold' }}>
          Удалить чат
        </DialogTitle>
        <DialogContent>
          <Typography sx={{ color: isDarkMode ? 'rgba(255,255,255,0.9)' : 'rgba(0,0,0,0.8)', mt: 1 }}>
            Это действие навсегда удалит выбранный чат и не может быть отменено.
            Пожалуйста, подтвердите для продолжения.
          </Typography>
        </DialogContent>
        <DialogActions sx={{ p: 2, gap: 1 }}>
          <Button
            onClick={() => setShowDeleteDialog(false)}
            sx={{
              backgroundColor: isDarkMode ? 'black' : 'rgba(0,0,0,0.08)',
              color: isDarkMode ? 'white' : '#333',
              '&:hover': { backgroundColor: isDarkMode ? 'rgba(0,0,0,0.8)' : 'rgba(0,0,0,0.12)' },
              textTransform: 'none',
              px: 3,
            }}
          >
            Отменить
          </Button>
          <Button
            onClick={handleConfirmDelete}
            sx={{
              backgroundColor: '#d32f2f',
              color: 'white',
              '&:hover': { backgroundColor: '#b71c1c' },
              textTransform: 'none',
              px: 3,
            }}
          >
            Удалить
          </Button>
        </DialogActions>
      </Dialog>

      {/* Диалог создания папки */}
      <Dialog
        open={showCreateFolderDialog}
        onClose={() => setShowCreateFolderDialog(false)}
        maxWidth="sm"
        fullWidth
        PaperProps={{
          sx: {
            backgroundColor: '#1e1e1e',
            color: 'white',
            borderRadius: 2,
          }
        }}
      >
        <DialogTitle sx={{ color: 'white', fontWeight: 'bold' }}>
          Создать папку
        </DialogTitle>
        <DialogContent>
          <TextField
            autoFocus
            fullWidth
            placeholder="Название папки"
            value={newFolderName}
            onChange={(e) => setNewFolderName(e.target.value)}
            onKeyPress={(e) => {
              if (e.key === 'Enter') {
                handleCreateFolder();
              }
            }}
              sx={{
              mt: 2,
              '& .MuiOutlinedInput-root': {
                  color: 'white',
                '& fieldset': {
                  borderColor: 'rgba(255,255,255,0.3)',
                },
                '&:hover fieldset': {
                  borderColor: 'rgba(255,255,255,0.5)',
                },
                '&.Mui-focused fieldset': {
                  borderColor: 'rgba(255,255,255,0.7)',
                },
                },
              }}
            />
        </DialogContent>
        <DialogActions sx={{ p: 2, gap: 1 }}>
          <Button
            onClick={() => setShowCreateFolderDialog(false)}
            sx={{
              backgroundColor: 'black',
              color: 'white',
              '&:hover': { backgroundColor: 'rgba(0,0,0,0.8)' },
              textTransform: 'none',
              px: 3,
            }}
          >
            Отменить
          </Button>
          <Button
            onClick={handleCreateFolder}
            disabled={!newFolderName.trim()}
            sx={{
              backgroundColor: '#2196f3',
              color: 'white',
              '&:hover': { backgroundColor: '#1976d2' },
              '&:disabled': { backgroundColor: 'rgba(255,255,255,0.1)' },
              textTransform: 'none',
              px: 3,
            }}
          >
            Создать
          </Button>
        </DialogActions>
      </Dialog>

      {/* Меню папки — тот же стиль и размеры, что у меню чата */}
      <Popover
        open={folderMenuOpen}
        anchorEl={folderMenuAnchor}
        onClose={handleFolderMenuClose}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
        transformOrigin={{ vertical: 'bottom', horizontal: 'right' }}
        slotProps={{
          paper: {
            sx: {
              mt: 0.5,
              p: 0,
              overflow: 'visible',
              background: 'transparent !important',
              backgroundColor: 'transparent !important',
              boxShadow: 'none !important',
              border: 'none',
            },
          },
        }}
      >
        <Box sx={{ ...dropdownPanelSx, width: MENU_COMPACT_PANEL_WIDTH_PX, display: 'flex', flexDirection: 'column' }}>
          <Box sx={{ py: 0.5, px: 0.5 }}>
            <Box
              onClick={() => handleFolderMenuAction('rename')}
              sx={{ ...dropdownItemSx, display: 'flex', alignItems: 'center', gap: 1, color: menuItemColor }}
            >
              <EditIcon sx={{ fontSize: 22, color: submenuIconColor, flexShrink: 0 }} />
              <Typography sx={{ flex: 1, fontSize: MENU_ACTION_TEXT_SIZE }}>Переименовать</Typography>
            </Box>
            <Box
              onClick={() => handleFolderMenuAction('archive')}
              sx={{ ...dropdownItemSx, display: 'flex', alignItems: 'center', gap: 1, color: menuItemColor }}
            >
              <ArchiveIcon sx={{ fontSize: 22, color: submenuIconColor, flexShrink: 0 }} />
              <Typography sx={{ flex: 1, fontSize: MENU_ACTION_TEXT_SIZE }}>Архив</Typography>
            </Box>
            <Box
              onClick={() => handleFolderMenuAction('delete')}
              sx={{
                ...dropdownItemSx,
                display: 'flex',
                alignItems: 'center',
                gap: 1,
                color: '#d32f2f',
                '&:hover': { backgroundColor: 'rgba(211, 47, 47, 0.1)' },
              }}
            >
              <DeleteIcon sx={{ fontSize: 22, color: '#d32f2f', flexShrink: 0 }} />
              <Typography sx={{ flex: 1, fontSize: MENU_ACTION_TEXT_SIZE, color: '#d32f2f' }}>Удалить</Typography>
            </Box>
          </Box>
        </Box>
      </Popover>

      {/* Диалог переименования папки */}
      <Dialog
        open={showRenameFolderDialog}
        onClose={() => {
          setShowRenameFolderDialog(false);
          handleFolderMenuClose();
        }}
        maxWidth="sm"
        fullWidth
        PaperProps={{
          sx: {
            backgroundColor: '#1e1e1e',
            color: 'white',
            borderRadius: 2,
          }
        }}
      >
        <DialogTitle sx={{ color: 'white', fontWeight: 'bold' }}>
          Переименовать папку
        </DialogTitle>
        <DialogContent>
          <TextField
            autoFocus
            fullWidth
            placeholder="Название папки"
            value={renamingFolderName}
            onChange={(e) => setRenamingFolderName(e.target.value)}
            onKeyPress={(e) => {
              if (e.key === 'Enter') {
                handleRenameFolder();
              }
            }}
            sx={{
              mt: 2,
              '& .MuiOutlinedInput-root': {
                color: 'white',
                '& fieldset': {
                  borderColor: 'rgba(255,255,255,0.3)',
                },
                '&:hover fieldset': {
                  borderColor: 'rgba(255,255,255,0.5)',
                },
                '&.Mui-focused fieldset': {
                  borderColor: 'rgba(255,255,255,0.7)',
                },
              },
            }}
          />
        </DialogContent>
        <DialogActions sx={{ p: 2, gap: 1 }}>
          <Button
            onClick={() => {
              setShowRenameFolderDialog(false);
              handleFolderMenuClose();
            }}
            sx={{
              backgroundColor: 'black',
              color: 'white',
              '&:hover': { backgroundColor: 'rgba(0,0,0,0.8)' },
              textTransform: 'none',
              px: 3,
            }}
          >
            Отменить
          </Button>
          <Button
            onClick={handleRenameFolder}
            disabled={!renamingFolderName.trim()}
            sx={{
              backgroundColor: '#2196f3',
              color: 'white',
              '&:hover': { backgroundColor: '#1976d2' },
              '&:disabled': { backgroundColor: 'rgba(255,255,255,0.1)' },
              textTransform: 'none',
              px: 3,
            }}
          >
            Переименовать
          </Button>
        </DialogActions>
      </Dialog>

      {/* Диалог удаления папки */}
      <Dialog
        open={showDeleteFolderDialog}
        onClose={() => {
          setShowDeleteFolderDialog(false);
          handleFolderMenuClose();
        }}
        maxWidth="sm"
        fullWidth
        PaperProps={{
          sx: {
            backgroundColor: '#1e1e1e',
            color: 'white',
            borderRadius: 2,
          }
        }}
      >
        <DialogTitle sx={{ color: 'white', fontWeight: 'bold' }}>
          Удалить папку
        </DialogTitle>
        <DialogContent>
          <Typography sx={{ color: 'white', mt: 1, mb: 2 }}>
            Что делать с чатами в этой папке?
          </Typography>
          
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
            <Button
              fullWidth
              variant={deleteWithContent ? 'contained' : 'outlined'}
              onClick={() => setDeleteWithContent(true)}
              sx={{
                justifyContent: 'flex-start',
                textTransform: 'none',
                color: deleteWithContent ? 'white' : 'rgba(255,255,255,0.7)',
                borderColor: 'rgba(255,255,255,0.3)',
                '&:hover': {
                  borderColor: 'rgba(255,255,255,0.5)',
                  backgroundColor: 'rgba(255,255,255,0.1)',
                },
              }}
            >
              Удалить со всем содержимым
            </Button>
            
            <Button
              fullWidth
              variant={!deleteWithContent ? 'contained' : 'outlined'}
              onClick={() => setDeleteWithContent(false)}
              sx={{
                justifyContent: 'flex-start',
                textTransform: 'none',
                color: !deleteWithContent ? 'white' : 'rgba(255,255,255,0.7)',
                borderColor: 'rgba(255,255,255,0.3)',
                '&:hover': {
                  borderColor: 'rgba(255,255,255,0.5)',
                  backgroundColor: 'rgba(255,255,255,0.1)',
                },
              }}
            >
              Переместить чаты в "Все чаты"
            </Button>
      </Box>
        </DialogContent>
        <DialogActions sx={{ p: 2, gap: 1 }}>
          <Button
            onClick={() => {
              setShowDeleteFolderDialog(false);
              handleFolderMenuClose();
            }}
            sx={{
              backgroundColor: 'black',
              color: 'white',
              '&:hover': { backgroundColor: 'rgba(0,0,0,0.8)' },
              textTransform: 'none',
              px: 3,
            }}
          >
            Отменить
          </Button>
          <Button
            onClick={handleDeleteFolder}
            sx={{
              backgroundColor: '#d32f2f',
              color: 'white',
              '&:hover': { backgroundColor: '#b71c1c' },
              textTransform: 'none',
              px: 3,
            }}
          >
            Удалить
          </Button>
        </DialogActions>
      </Dialog>

      {/* Модальное окно архива */}
      <ArchiveModal
        open={showArchiveModal}
        onClose={() => setShowArchiveModal(false)}
        isDarkMode={isDarkMode}
      />

      {/* Модальное окно создания проекта */}
      <EditProjectModal
        open={showEditProjectModal}
        onClose={() => {
          setShowEditProjectModal(false);
          setProjectIdToEdit(null);
        }}
        project={projectIdToEdit ? (projects.find((p) => p.id === projectIdToEdit) ?? null) : null}
        onSave={(projectId, updates) => {
          updateProject(projectId, updates);
          setShowEditProjectModal(false);
          setProjectIdToEdit(null);
        }}
      />

      <NewProjectModal
        open={showNewProjectModal}
        onClose={() => {
          setShowNewProjectModal(false);
          setPendingChatIdForProject(null);
        }}
        ensureDraftProjectForRag={(draft) => {
          const projectId = createProject({
            name: draft.name,
            icon: draft.icon,
            iconType: draft.iconType,
            iconColor: draft.iconColor,
            memory: draft.memory,
            instructions: draft.instructions,
          });
          if (pendingChatIdForProject) {
            moveChatToProject(pendingChatIdForProject, projectId);
            setPendingChatIdForProject(null);
          }
          return projectId;
        }}
        finalizeDraftProject={(projectId, updates) => {
          updateProject(projectId, {
            name: updates.name,
            icon: updates.icon,
            iconType: updates.iconType,
            iconColor: updates.iconColor,
            memory: updates.memory,
            instructions: updates.instructions,
          });
          if (pendingChatIdForProject) {
            moveChatToProject(pendingChatIdForProject, projectId);
            setPendingChatIdForProject(null);
          }
        }}
        cancelDraftProject={(projectId) => {
          deleteProject(projectId);
        }}
        onCreateProject={(projectData) => {
          const projectId = createProject({
            name: projectData.name,
            icon: projectData.icon,
            iconType: projectData.iconType,
            iconColor: projectData.iconColor,
            memory: projectData.memory,
            instructions: projectData.instructions,
          });
          if (pendingChatIdForProject) {
            moveChatToProject(pendingChatIdForProject, projectId);
            setPendingChatIdForProject(null);
          }
          return projectId;
        }}
      />

      {/* Меню проекта — тот же стиль и размеры, что у меню чата */}
      <Popover
        open={projectMenuOpen}
        anchorEl={projectMenuAnchor}
        onClose={handleProjectMenuClose}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
        transformOrigin={{ vertical: 'bottom', horizontal: 'right' }}
        slotProps={{
          paper: {
            sx: {
              mt: 0.5,
              p: 0,
              overflow: 'visible',
              background: 'transparent !important',
              backgroundColor: 'transparent !important',
              boxShadow: 'none !important',
              border: 'none',
            },
          },
        }}
      >
        <Box sx={{ ...dropdownPanelSx, width: MENU_COMPACT_PANEL_WIDTH_PX, display: 'flex', flexDirection: 'column' }}>
          <Box sx={{ py: 0.5, px: 0.5 }}>
            <Box
              onClick={() => handleProjectMenuAction('edit')}
              sx={{ ...dropdownItemSx, display: 'flex', alignItems: 'center', gap: 1, color: menuItemColor }}
            >
              <EditIcon sx={{ fontSize: 22, color: submenuIconColor, flexShrink: 0 }} />
              <Typography sx={{ flex: 1, fontSize: MENU_ACTION_TEXT_SIZE }}>Редактировать проект</Typography>
            </Box>
            <Box
              onClick={() => handleProjectMenuAction('delete')}
              sx={{
                ...dropdownItemSx,
                display: 'flex',
                alignItems: 'center',
                gap: 1,
                color: '#d32f2f',
                '&:hover': { backgroundColor: 'rgba(211, 47, 47, 0.1)' },
              }}
            >
              <DeleteIcon sx={{ fontSize: 22, color: '#d32f2f', flexShrink: 0 }} />
              <Typography sx={{ flex: 1, fontSize: MENU_ACTION_TEXT_SIZE, color: '#d32f2f' }}>Удалить проект</Typography>
            </Box>
          </Box>
        </Box>
      </Popover>

      {/* Диалог подтверждения удаления проекта */}
      <Dialog
        open={showDeleteProjectDialog}
        onClose={() => setShowDeleteProjectDialog(false)}
        maxWidth="sm"
        fullWidth
        PaperProps={{
          sx: {
            backgroundColor: '#1e1e1e',
            color: 'white',
            borderRadius: 2,
          }
        }}
      >
        <DialogTitle sx={{ color: 'white', fontWeight: 'bold' }}>
          Удалить проект
        </DialogTitle>
        <DialogContent>
          <Typography sx={{ color: 'white', mt: 1 }}>
            Это действие навсегда удалит выбранный проект и не может быть отменено. 
            Пожалуйста, подтвердите для продолжения.
          </Typography>
        </DialogContent>
        <DialogActions sx={{ p: 2, gap: 1 }}>
          <Button
            onClick={() => setShowDeleteProjectDialog(false)}
            sx={{
              backgroundColor: 'black',
              color: 'white',
              '&:hover': { backgroundColor: 'rgba(0,0,0,0.8)' },
              textTransform: 'none',
              px: 3,
            }}
          >
            Отменить
          </Button>
          <Button
            onClick={handleConfirmDeleteProject}
            sx={{
              backgroundColor: '#d32f2f',
              color: 'white',
              '&:hover': { backgroundColor: '#b71c1c' },
              textTransform: 'none',
              px: 3,
            }}
          >
            Удалить
          </Button>
        </DialogActions>
      </Dialog>
    </Drawer>
  );
}
