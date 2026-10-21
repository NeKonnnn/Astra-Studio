import React, { useState, useEffect } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  Box,
  List,
  ListItem,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Typography,
  IconButton,
  useTheme,
  useMediaQuery,
} from '@mui/material';
import {
  Close as CloseIcon,
  SettingsOutlined as SettingsIcon,
  MicOutlined as MicIcon,
  InfoOutlined as InfoIcon,
  PaletteOutlined as PaletteIcon,
  PersonOutlined as PersonIcon,
  ChatOutlined as ChatIcon,
  ComputerOutlined as ComputerIcon,
  SearchOutlined as SearchIcon,
} from '@mui/icons-material';
import { MENU_ICON_MIN_WIDTH, MENU_ICON_TO_TEXT_GAP_PX, MENU_ICON_FONT_SIZE_PX } from '../constants/menuStyles';
import {
  GeneralSettings,
  ProfileSettings,
  ModelsSettings,
  RAGSettings,
  TranscriptionSettings,
  AboutSettings,
  ChatSettings,
  InterfaceSettings
} from './settings';
import { ASTRA_OPEN_SETTINGS_SECTION } from '../constants/hotkeys';

interface SettingsModalProps {
  open: boolean;
  onClose: () => void;
  isDarkMode: boolean;
  onToggleTheme: () => void;
  initialSection?: SettingsSection;
}

type SettingsSection = 'general' | 'profile' | 'interface' | 'models' | 'rag' | 'transcription' | 'chats' | 'about';

const settingsSections = [
  {
    id: 'general' as SettingsSection,
    title: 'Общее',
    icon: <PaletteIcon />,
    description: 'Тема и настройки памяти'
  },
  {
    id: 'profile' as SettingsSection,
    title: 'Профиль',
    icon: <PersonIcon />,
    description: 'Личная информация и аккаунт'
  },
  {
    id: 'interface' as SettingsSection,
    title: 'Интерфейс',
    icon: <ComputerIcon />,
    description: 'Настройки интерфейса приложения'
  },
  {
    id: 'chats' as SettingsSection,
    title: 'Чаты',
    icon: <ChatIcon />,
    description: 'Управление чатами и сообщениями'
  },
  {
    id: 'models' as SettingsSection,
    title: 'Модели',
    icon: <SettingsIcon />,
    description: 'Управление моделями и промпты'
  },
  {
    id: 'rag' as SettingsSection,
    title: 'RAG',
    icon: <SearchIcon />,
    description: 'Общая библиотека · проекты · агенты'
  },
  {
    id: 'transcription' as SettingsSection,
    title: 'Транскрибация',
    icon: <MicIcon />,
    description: 'Настройки распознавания речи'
  },
  {
    id: 'about' as SettingsSection,
    title: 'О приложении',
    icon: <InfoIcon />,
    description: 'Системная информация'
  }
];

export default function SettingsModal({ open, onClose, isDarkMode, onToggleTheme, initialSection }: SettingsModalProps) {
  const [activeSection, setActiveSection] = useState<SettingsSection>('general');
  const theme = useTheme();
  const isMobile = useMediaQuery(theme.breakpoints.down('md'));

  useEffect(() => {
    if (open && initialSection && settingsSections.some((s) => s.id === initialSection)) {
      setActiveSection(initialSection);
    }
  }, [open, initialSection]);

  useEffect(() => {
    const onSection = (e: Event) => {
      const section = (e as CustomEvent<{ section?: SettingsSection }>).detail?.section;
      if (section && settingsSections.some((s) => s.id === section)) {
        setActiveSection(section);
      }
    };
    window.addEventListener(ASTRA_OPEN_SETTINGS_SECTION, onSection);
    return () => window.removeEventListener(ASTRA_OPEN_SETTINGS_SECTION, onSection);
  }, []);

  const handleSectionChange = (section: SettingsSection) => {
    setActiveSection(section);
  };

  const renderActiveSection = () => {
    switch (activeSection) {
      case 'general':
        return <GeneralSettings isDarkMode={isDarkMode} onToggleTheme={onToggleTheme} />;
      case 'profile':
        return <ProfileSettings />;
      case 'interface':
        return <InterfaceSettings isDarkMode={isDarkMode} />;
      case 'models':
        return <ModelsSettings />;
      case 'rag':
        return <RAGSettings isDarkMode={isDarkMode} />;
      case 'transcription':
        return <TranscriptionSettings />;
      case 'chats':
        return <ChatSettings isDarkMode={isDarkMode} />;
      case 'about':
        return <AboutSettings />;
      default:
        return <GeneralSettings isDarkMode={isDarkMode} onToggleTheme={onToggleTheme} />;
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="lg"
      fullWidth
      fullScreen={isMobile}
      PaperProps={{
        sx: {
          height: isMobile ? '100vh' : '80vh',
          maxHeight: isMobile ? '100vh' : '80vh',
          borderRadius: isMobile ? 0 : 2,
          backgroundColor: theme.palette.mode === 'dark' ? '#1a1a1a' : '#ffffff',
        }
      }}
    >
      <DialogTitle
        sx={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          p: 2,
          borderBottom: `1px solid ${theme.palette.divider}`,
          backgroundColor: theme.palette.mode === 'dark' ? '#2a2a2a' : '#f5f5f5',
        }}
      >
        <Typography component="span" variant="h6" fontWeight="600">
          Настройки
        </Typography>
        <IconButton
          onClick={onClose}
          size="small"
          sx={{
            color: theme.palette.text.secondary,
            '&:hover': {
              backgroundColor: theme.palette.action.hover,
            }
          }}
        >
          <CloseIcon />
        </IconButton>
      </DialogTitle>

      <DialogContent sx={{ p: 0, display: 'flex', height: '100%' }}>
        {/* Левая панель навигации */}
        <Box
          sx={{
            width: 280,
            minWidth: 280,
            borderRight: `1px solid ${theme.palette.divider}`,
            backgroundColor: theme.palette.mode === 'dark' ? '#2a2a2a' : '#f8f9fa',
            overflow: 'auto',
          }}
        >
          <List sx={{ p: 1 }}>
            {settingsSections.map((section, index) => (
              <React.Fragment key={section.id}>
                <ListItem disablePadding>
                  <ListItemButton
                    onClick={() => handleSectionChange(section.id)}
                    selected={activeSection === section.id}
                    sx={{
                      borderRadius: 1,
                      mb: 0.5,
                      '&.Mui-selected': {
                        backgroundColor: theme.palette.primary.main,
                        color: theme.palette.primary.contrastText,
                        '&:hover': {
                          backgroundColor: theme.palette.primary.dark,
                        },
                        '& .MuiListItemIcon-root': {
                          color: theme.palette.primary.contrastText,
                        }
                      },
                      '&:hover': {
                        backgroundColor: theme.palette.action.hover,
                      }
                    }}
                  >
                    <ListItemIcon
                      sx={{
                        color: activeSection === section.id ? 'inherit' : theme.palette.text.secondary,
                        minWidth: `${MENU_ICON_MIN_WIDTH}px`,
                        marginRight: `${MENU_ICON_TO_TEXT_GAP_PX}px`,
                        '& .MuiSvgIcon-root': { fontSize: `${MENU_ICON_FONT_SIZE_PX}px` },
                      }}
                    >
                      {section.icon}
                    </ListItemIcon>
                    <ListItemText
                      primary={section.title}
                      primaryTypographyProps={{
                        fontSize: '0.875rem',
                        fontWeight: activeSection === section.id ? 600 : 400,
                      }}
                    />
                  </ListItemButton>
                </ListItem>
              </React.Fragment>
            ))}
          </List>
        </Box>

        {/* Правая панель контента */}
        <Box
          sx={{
            flex: 1,
            overflow: 'auto',
            backgroundColor: theme.palette.background.default,
          }}
        >
          {renderActiveSection()}
        </Box>
      </DialogContent>
    </Dialog>
  );
}