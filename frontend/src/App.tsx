import React, { useState, useEffect, useCallback, startTransition } from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import { CssBaseline, Box, IconButton, Tooltip } from '@mui/material';
import { ChevronRight as ChevronRightIcon } from '@mui/icons-material';
import { LeftBar } from './components/left_bar';
import { RightBar, RightBarProvider } from './components/right_bar';
import GlobalKeyboardShortcuts from './components/GlobalKeyboardShortcuts';
import SettingsModal from './components/SettingsModal';
import { ASTRA_FOCUS_CHAT_SEARCH, ASTRA_OPEN_SETTINGS, ASTRA_OPEN_SETTINGS_SECTION } from './constants/hotkeys';
import { useRightSidebarInsetCssVar } from './hooks/useRightSidebarInsetCssVar';
import {
  readRightSidebarWidthPinned,
  readRightSidebarWidthPx,
  RIGHT_SIDEBAR_WIDTH_PINNED_STORAGE_KEY,
  RIGHT_SIDEBAR_WIDTH_STORAGE_KEY,
} from './hooks/useRightSidebarWidth';
import UnifiedChatPage from './pages/UnifiedChatPage';
import VoicePage from './pages/VoicePage';
import DocumentsPage from './pages/DocumentsPage';
// import SettingsPage from './pages/SettingsPage'; // Удалено - теперь используется модальное окно
import HistoryPage from './pages/HistoryPage';
import AgentGalleryPage from './pages/AgentGalleryPage';
import PluginGalleryPage from './pages/PluginGalleryPage';
import GalleryHubPage from './pages/GalleryHubPage';
import SkillsPage from './pages/SkillsPage';
import ReleaseNotesPage from './pages/ReleaseNotesPage';
import ProjectPage from './pages/ProjectPage';
import KnowledgeBasePage from './pages/KnowledgeBasePage';
import { SocketProvider } from './contexts/SocketContext';
import { AppProvider } from './contexts/AppContext';
import { AuthProvider } from './contexts/AuthContext';
import PrivateRoute from './components/PrivateRoute';
import SessionTimeoutWatcher from './components/SessionTimeoutWatcher';
import SessionValidityWatcher from './components/SessionValidityWatcher';
import LoginPage from './pages/LoginPage';
import SsoCallbackPage from './pages/SsoCallbackPage';
import ProfilePage from './pages/ProfilePage';
import CreationsPage from './pages/CreationsPage';
import ShareViewPage from './pages/ShareViewPage';
import { initSettings } from './settings';
import LlmStatusBanner from './components/LlmStatusBanner';
import RagReindexStatusBanner from './components/RagReindexStatusBanner';
import StatusToastHost from './components/StatusToastHost';
import { RagReindexStatusProvider } from './contexts/RagReindexStatusContext';
import PluginRunBanner from './components/PluginRunBanner';
import { PluginRunProvider } from './contexts/PluginRunContext';
import TabNotificationWatcher from './components/TabNotificationWatcher';
import SupportAssistantWidget from './support_assistant';
import './App.css';
import { MENU_ITEM_HOVER_DARK, MENU_ITEM_HOVER_LIGHT, MENU_BORDER_RADIUS_PX, MENU_ITEM_HOVER_RADIUS_PX, MENU_ITEM_HOVER_MARGIN_PX, MENU_MIN_WIDTH_PX, MENU_ICON_MIN_WIDTH, MENU_ICON_TO_TEXT_GAP_PX, MENU_ICON_FONT_SIZE_PX } from './constants/menuStyles';

const MENU_ITEM_MARGIN = MENU_ITEM_HOVER_MARGIN_PX;
const MENU_ITEM_RADIUS = MENU_ITEM_HOVER_RADIUS_PX;

// Создаем тему Material-UI
const createAppTheme = (isDark: boolean) => createTheme({
  palette: {
    mode: isDark ? 'dark' : 'light',
    primary: {
      main: '#2196f3',
      dark: '#1976d2',
      light: '#64b5f6',
    },
    secondary: {
      main: '#f50057',
      dark: '#c51162',
      light: '#ff5983',
    },
    background: {
      default: isDark ? '#121212' : '#fafafa',
      paper: isDark ? '#1e1e1e' : '#ffffff',
    },
    action: {
      hover: isDark ? MENU_ITEM_HOVER_DARK : MENU_ITEM_HOVER_LIGHT,
    },
  },
  typography: {
    fontFamily: '"Roboto", "Helvetica", "Arial", sans-serif',
    h4: {
      fontWeight: 600,
    },
    h6: {
      fontWeight: 500,
    },
  },
  shape: {
    borderRadius: 12,
  },
  components: {
    MuiButton: {
      styleOverrides: {
        root: {
          textTransform: 'none',
          borderRadius: 8,
        },
      },
    },
    MuiCard: {
      styleOverrides: {
        root: {
          boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
        },
      },
    },
    // Единый цвет подсветки при наведении + округлая подушечка (не во всю ширину)
    MuiMenuItem: {
      styleOverrides: {
        root: ({ theme }) => ({
          marginLeft: MENU_ITEM_MARGIN,
          marginRight: MENU_ITEM_MARGIN,
          borderRadius: MENU_ITEM_RADIUS,
          '&:hover': {
            backgroundColor: theme.palette.action.hover,
          },
        }),
      },
    },
    MuiListItemButton: {
      styleOverrides: {
        root: ({ theme }) => ({
          marginLeft: MENU_ITEM_MARGIN,
          marginRight: MENU_ITEM_MARGIN,
          borderRadius: MENU_ITEM_RADIUS,
          '&:hover': {
            backgroundColor: theme.palette.action.hover,
          },
        }),
      },
    },
    // Опции в Select и Autocomplete — тот же серый hover
    MuiAutocomplete: {
      styleOverrides: {
        listbox: ({ theme }) => ({
          '& .MuiMenuItem-root:hover': {
            backgroundColor: theme.palette.action.hover,
          },
        }),
      },
    },
  },
});

function App() {
  // Инициализация конфигурации при загрузке приложения
  useEffect(() => {
    initSettings().catch((error) => {
      console.error('Ошибка загрузки конфигурации:', error);
    });
  }, []);

  const [isDarkMode, setIsDarkMode] = useState(() => {
    const saved = localStorage.getItem('gazikii-dark-mode');
    return saved ? JSON.parse(saved) : false;
  });

  const [sidebarOpen, setSidebarOpen] = useState(() => {
    const saved = localStorage.getItem('sidebarOpen');
    return saved !== null ? saved === 'true' : true;
  });
  const [sidebarHidden, setSidebarHidden] = useState(() => {
    const saved = localStorage.getItem('sidebarHidden');
    return saved !== null ? saved === 'true' : false;
  });
  const [rightSidebarOpen, setRightSidebarOpen] = useState(() => {
    const saved = localStorage.getItem('rightSidebarOpen');
    return saved !== null ? saved === 'true' : true;
  });
  const [rightSidebarHidden, setRightSidebarHidden] = useState(() => {
    const saved = localStorage.getItem('rightSidebarHidden');
    return saved !== null ? saved === 'true' : false;
  });
  const [rightSidebarWidthPx, setRightSidebarWidthPx] = useState(() => readRightSidebarWidthPx());
  const [rightSidebarWidthPinned, setRightSidebarWidthPinned] = useState(() => readRightSidebarWidthPinned());

  const setRightOpenStable = useCallback(
    (v: boolean | ((prev: boolean) => boolean)) => setRightSidebarOpen(v),
    [],
  );
  const setRightHiddenStable = useCallback(
    (v: boolean | ((prev: boolean) => boolean)) => setRightSidebarHidden(v),
    [],
  );
  const setRightWidthStable = useCallback(
    (v: number | ((prev: number) => number)) => setRightSidebarWidthPx(v),
    [],
  );
  const setRightWidthPinnedStable = useCallback(
    (v: boolean | ((prev: boolean) => boolean)) => setRightSidebarWidthPinned(v),
    [],
  );

  useRightSidebarInsetCssVar(rightSidebarOpen, rightSidebarHidden, rightSidebarWidthPx);

  /** Счётчик для фокуса поля «Поиск в чатах» (в т.ч. после показа скрытого сайдбара). */
  const [searchFocusNonce, setSearchFocusNonce] = useState(0);
  /** Настройки по Alt+S — в App, чтобы работало даже при скрытой левой панели. */
  const [settingsFromHotkeyOpen, setSettingsFromHotkeyOpen] = useState(false);
  const [settingsInitialSection, setSettingsInitialSection] = useState<
    'general' | 'profile' | 'interface' | 'models' | 'rag' | 'transcription' | 'chats' | 'about' | undefined
  >(undefined);

  useEffect(() => {
    const onOpenSettings = () => setSettingsFromHotkeyOpen(true);
    window.addEventListener(ASTRA_OPEN_SETTINGS, onOpenSettings);
    return () => window.removeEventListener(ASTRA_OPEN_SETTINGS, onOpenSettings);
  }, []);

  useEffect(() => {
    const onOpenSection = (e: Event) => {
      const section = (e as CustomEvent<{ section?: string }>).detail?.section;
      if (section) {
        setSettingsInitialSection(section as typeof settingsInitialSection);
        setSettingsFromHotkeyOpen(true);
      }
    };
    window.addEventListener(ASTRA_OPEN_SETTINGS_SECTION, onOpenSection);
    return () => window.removeEventListener(ASTRA_OPEN_SETTINGS_SECTION, onOpenSection);
  }, []);

  useEffect(() => {
    const onFocusSearch = () => {
      startTransition(() => {
        setSidebarHidden(false);
        setSidebarOpen(true);
      });
      setSearchFocusNonce((n) => n + 1);
    };
    window.addEventListener(ASTRA_FOCUS_CHAT_SEARCH, onFocusSearch);
    return () => window.removeEventListener(ASTRA_FOCUS_CHAT_SEARCH, onFocusSearch);
  }, []);

  useEffect(() => {
    localStorage.setItem('gazikii-dark-mode', JSON.stringify(isDarkMode));
    // Дефолтный цвет панелей зависит от темы — обновляем слушателей.
    window.dispatchEvent(
      new CustomEvent('sidebarColorChanged', { detail: { isDarkMode } }),
    );
  }, [isDarkMode]);

  useEffect(() => {
    localStorage.setItem('sidebarOpen', String(sidebarOpen));
  }, [sidebarOpen]);

  useEffect(() => {
    localStorage.setItem('sidebarHidden', String(sidebarHidden));
  }, [sidebarHidden]);

  useEffect(() => {
    localStorage.setItem('rightSidebarOpen', String(rightSidebarOpen));
  }, [rightSidebarOpen]);

  useEffect(() => {
    localStorage.setItem('rightSidebarHidden', String(rightSidebarHidden));
  }, [rightSidebarHidden]);

  useEffect(() => {
    localStorage.setItem(RIGHT_SIDEBAR_WIDTH_STORAGE_KEY, String(rightSidebarWidthPx));
  }, [rightSidebarWidthPx]);

  useEffect(() => {
    localStorage.setItem(RIGHT_SIDEBAR_WIDTH_PINNED_STORAGE_KEY, String(rightSidebarWidthPinned));
  }, [rightSidebarWidthPinned]);

  // CSS-переменные для меню: единый серый hover, скругление, подушечка подсветки (перебивают глобальные стили в App.css)
  useEffect(() => {
    const root = document.documentElement;
    root.style.setProperty('--menu-item-hover', isDarkMode ? MENU_ITEM_HOVER_DARK : MENU_ITEM_HOVER_LIGHT);
    root.style.setProperty('--menu-border-radius', `${MENU_BORDER_RADIUS_PX}px`);
    root.style.setProperty('--menu-item-hover-radius', `${MENU_ITEM_HOVER_RADIUS_PX}px`);
    root.style.setProperty('--menu-item-hover-margin', `${MENU_ITEM_HOVER_MARGIN_PX}px`);
    root.style.setProperty('--menu-min-width', `${MENU_MIN_WIDTH_PX}px`); /* дублируем из index.tsx при смене темы */
    root.style.setProperty('--menu-icon-min-width', `${MENU_ICON_MIN_WIDTH}px`);
    root.style.setProperty('--menu-icon-to-text-gap', `${MENU_ICON_TO_TEXT_GAP_PX}px`);
    root.style.setProperty('--menu-icon-font-size', `${MENU_ICON_FONT_SIZE_PX}px`);
  }, [isDarkMode]);

  const theme = createAppTheme(isDarkMode);

  const toggleTheme = () => {
    const next = !isDarkMode;
    // Пишем тему синхронно до рендера детей — иначе getSidebarPanelBackground()
    // в эффектах панелей ещё видит старое значение.
    localStorage.setItem('gazikii-dark-mode', JSON.stringify(next));
    setIsDarkMode(next);
  };

  const toggleSidebar = () => {
    startTransition(() => {
      setSidebarOpen((o) => !o);
    });
  };

  const toggleRightSidebar = () => {
    startTransition(() => {
      setRightSidebarOpen((o) => !o);
    });
  };

  const rightBarLayout = {
    open: rightSidebarOpen,
    hidden: rightSidebarHidden,
    expandedWidthPx: rightSidebarWidthPx,
    widthPinned: rightSidebarWidthPinned,
    setOpen: setRightOpenStable,
    setHidden: setRightHiddenStable,
    setExpandedWidthPx: setRightWidthStable,
    setWidthPinned: setRightWidthPinnedStable,
  };

  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <LlmStatusBanner />
      <AuthProvider>
        <AppProvider>
          <Router>
            <RagReindexStatusProvider>
              <RagReindexStatusBanner />
              <StatusToastHost />
              <PluginRunProvider>
              <PluginRunBanner />
              <SocketProvider>
                <TabNotificationWatcher />
                <SessionTimeoutWatcher />
                <SessionValidityWatcher />
                <Routes>
                {/* Публичный маршрут для логина */}
                <Route path="/login" element={<LoginPage />} />
                <Route path="/login/sso-callback" element={<SsoCallbackPage />} />
                
                {/* Публичный маршрут для просмотра публичных ссылок */}
                <Route path="/share/:shareId" element={<ShareViewPage />} />
                
                {/* Защищенные маршруты */}
                <Route
                  path="/*"
                  element={
                    <PrivateRoute>
                      <RightBarProvider value={rightBarLayout}>
                      <Box sx={{ display: 'flex', height: '100vh', overflow: 'hidden', bgcolor: 'background.default' }}>
                        {!sidebarHidden && (
                          <LeftBar 
                            open={sidebarOpen} 
                            onToggle={toggleSidebar}
                            isDarkMode={isDarkMode}
                            onToggleTheme={toggleTheme}
                            onHide={() => startTransition(() => setSidebarHidden(true))}
                            searchFocusNonce={searchFocusNonce}
                          />
                        )}
                        <GlobalKeyboardShortcuts />
                        <SettingsModal
                          open={settingsFromHotkeyOpen}
                          onClose={() => {
                            setSettingsFromHotkeyOpen(false);
                            setSettingsInitialSection(undefined);
                          }}
                          initialSection={settingsInitialSection}
                          isDarkMode={isDarkMode}
                          onToggleTheme={toggleTheme}
                        />
                        <Box 
                          component="main" 
                          sx={{ 
                            flexGrow: 1,
                            minWidth: 0,
                            minHeight: 0,
                            height: '100%',
                            display: 'flex',
                            flexDirection: 'column',
                            overflow: 'hidden',
                            // При hide с collapsed: margin -64→0 без анимации, иначе на 0.3s шире viewport → серые скроллбары.
                            marginLeft: sidebarHidden ? 0 : (sidebarOpen ? 0 : '-64px'),
                            marginRight: rightSidebarHidden ? 0 : (rightSidebarOpen ? 0 : '-64px'),
                            transition: [
                              sidebarHidden ? 'margin-left 0s' : 'margin-left 0.3s ease',
                              rightSidebarHidden ? 'margin-right 0s' : 'margin-right 0.3s ease',
                            ].join(', '),
                            position: 'relative',
                            bgcolor: 'background.default',
                          }}
                        >
                          {/* Кнопка для показа скрытой левой панели */}
                          {sidebarHidden && (
                            <Box
                              sx={{
                                position: 'fixed',
                                left: 0,
                                top: '50%',
                                transform: 'translateY(-50%)',
                                zIndex: 1200,
                              }}
                            >
                              <Tooltip title="Показать панель" placement="right">
                                <IconButton
                                  onClick={() => {
                                    startTransition(() => {
                                      setSidebarHidden(false);
                                      setSidebarOpen(false);
                                    });
                                  }}
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
                                  <ChevronRightIcon />
                                </IconButton>
                              </Tooltip>
                            </Box>
                          )}
                          
                          <Routes>
                            <Route path="/" element={<UnifiedChatPage isDarkMode={isDarkMode} sidebarOpen={sidebarOpen} sidebarHidden={sidebarHidden} />} />
                            <Route path="/project" element={<UnifiedChatPage isDarkMode={isDarkMode} sidebarOpen={sidebarOpen} sidebarHidden={sidebarHidden} />} />
                            <Route path="/project/:projectId" element={<ProjectPage sidebarOpen={sidebarOpen} sidebarHidden={sidebarHidden} />} />
                            <Route path="/voice" element={<UnifiedChatPage isDarkMode={isDarkMode} sidebarOpen={sidebarOpen} sidebarHidden={sidebarHidden} />} />
                            <Route path="/documents" element={<UnifiedChatPage isDarkMode={isDarkMode} sidebarOpen={sidebarOpen} sidebarHidden={sidebarHidden} />} />
                            <Route path="/search" element={<UnifiedChatPage isDarkMode={isDarkMode} sidebarOpen={sidebarOpen} sidebarHidden={sidebarHidden} />} />
                            <Route path="/creations" element={<CreationsPage />} />
                            <Route path="/gallery" element={<GalleryHubPage />} />
                            <Route path="/prompts" element={<Navigate to="/gallery?tab=agents" replace />} />
                            <Route path="/agents-gallery" element={<AgentGalleryPage />} />
                            <Route path="/plugins-gallery" element={<PluginGalleryPage />} />
                            <Route path="/skills" element={<SkillsPage />} />
                            <Route path="/docs/astrachat-release-1.0" element={<ReleaseNotesPage />} />
                            <Route path="/docs/astrachat-release-1.0.html" element={<ReleaseNotesPage />} />
                            <Route path="/profile" element={<ProfilePage />} />
                            <Route path="/history" element={<HistoryPage />} />
                          </Routes>
                          {/* Прототип: плавающий помощник поддержки (LLM/RAG — следующий этап) */}
                          <SupportAssistantWidget />
                        </Box>
                        <RightBar
                          open={rightSidebarOpen}
                          hidden={rightSidebarHidden}
                          expandedWidthPx={rightSidebarWidthPx}
                          widthPinned={rightSidebarWidthPinned}
                          isDarkMode={isDarkMode}
                          onToggleOpen={toggleRightSidebar}
                          onHide={() => startTransition(() => setRightSidebarHidden(true))}
                          onShow={() => {
                            startTransition(() => {
                              setRightSidebarHidden(false);
                              setRightSidebarOpen(false);
                            });
                          }}
                          setOpen={setRightOpenStable}
                          setHidden={setRightHiddenStable}
                          setExpandedWidthPx={setRightWidthStable}
                          setWidthPinned={setRightWidthPinnedStable}
                        />
                      </Box>
                      </RightBarProvider>
                    </PrivateRoute>
                  }
                />
                </Routes>
              </SocketProvider>
              </PluginRunProvider>
            </RagReindexStatusProvider>
          </Router>
        </AppProvider>
      </AuthProvider>
    </ThemeProvider>
  );
}

export default App;