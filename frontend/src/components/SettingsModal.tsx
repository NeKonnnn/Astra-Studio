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
  RecordVoiceOver as VoiceAssistantIcon,
  InfoOutlined as InfoIcon,
  PaletteOutlined as PaletteIcon,
  PersonOutlined as PersonIcon,
  ChatOutlined as ChatIcon,
  ComputerOutlined as ComputerIcon,
} from '@mui/icons-material';
import { MENU_ICON_MIN_WIDTH, MENU_ICON_TO_TEXT_GAP_PX, MENU_ICON_FONT_SIZE_PX } from '../constants/menuStyles';
import {
  GeneralSettings,
  ProfileSettings,
  ModelsSettings,
  TranscriptionSettings,
  VoiceAssistantSettings,
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
  /** Открыть «Тонкая настройка» в разделе Модели. */
  expandModelFineTune?: boolean;
  /** Открыть вкладку ComfyUI в блоке генерации изображений. */
  openImageComfyUi?: boolean;
}

type SettingsSection =
  | 'general'
  | 'profile'
  | 'interface'
  | 'models'
  | 'modelDownloads'
  | 'rag'
  | 'transcription'
  | 'voiceAssistant'
  | 'chats'
  | 'about';

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
    id: 'transcription' as SettingsSection,
    title: 'Транскрибация',
    icon: <MicIcon />,
    description: 'Настройки распознавания речи'
  },
  {
    id: 'voiceAssistant' as SettingsSection,
    title: 'Голосовой помощник',
    icon: <VoiceAssistantIcon />,
    description: 'Wake-word и голосовые команды'
  },
  {
    id: 'about' as SettingsSection,
    title: 'О приложении',
    icon: <InfoIcon />,
    description: 'Системная информация'
  }
];

function normalizeSection(section?: SettingsSection | string | null): SettingsSection | undefined {
  if (!section) return undefined;
  // старый пункт «Manage Models» перенесён внутрь «Модели»
  if (section === 'modelDownloads') return 'models';
  // раздел RAG убран из настроек (остался в боковой панели баз знаний)
  if (section === 'rag') return undefined;
  if (settingsSections.some((s) => s.id === section)) return section as SettingsSection;
  return undefined;
}

export default function SettingsModal({
  open,
  onClose,
  isDarkMode,
  onToggleTheme,
  initialSection,
  expandModelFineTune = false,
  openImageComfyUi = false,
}: SettingsModalProps) {
  const [activeSection, setActiveSection] = useState<SettingsSection>('general');
  const [expandFineTune, setExpandFineTune] = useState(false);
  const [openComfyUiTab, setOpenComfyUiTab] = useState(false);
  const theme = useTheme();
  const isMobile = useMediaQuery(theme.breakpoints.down('md'));
  useEffect(() => {
    if (!open) return;
    const section = normalizeSection(initialSection);
    if (section) setActiveSection(section);
    if (expandModelFineTune) setExpandFineTune(true);
    if (openImageComfyUi) setOpenComfyUiTab(true);
  }, [open, initialSection, expandModelFineTune, openImageComfyUi]);

  useEffect(() => {
    const onSection = (e: Event) => {
      const detail = (e as CustomEvent<{
        section?: SettingsSection;
        expandFineTune?: boolean;
        openImageComfyUi?: boolean;
      }>).detail;
      const section = normalizeSection(detail?.section);
      if (section) setActiveSection(section);
      if (detail?.expandFineTune) setExpandFineTune(true);
      if (detail?.openImageComfyUi) setOpenComfyUiTab(true);
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
      case 'modelDownloads':
        return (
          <ModelsSettings
            expandFineTune={expandFineTune}
            onFineTuneExpandHandled={() => setExpandFineTune(false)}
            openImageComfyUi={openComfyUiTab}
            onImageComfyUiHandled={() => setOpenComfyUiTab(false)}
          />
        );
      case 'transcription':
        return <TranscriptionSettings />;
      case 'voiceAssistant':
        return <VoiceAssistantSettings />;
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