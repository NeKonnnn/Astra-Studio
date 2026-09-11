import React, { useState, useMemo, useEffect, useRef } from 'react';
import { useTheme } from '@mui/material/styles';
import {
  Box,
  Button,
  Typography,
  Card,
  CardContent,
  List,
  ListItem,
  ListItemText,
  Switch,
  Divider,
  Dialog,
  DialogTitle,
  DialogContent,
  IconButton,
  Tooltip,
  Popover,
  Slider,
} from '@mui/material';
import {
  Computer as ComputerIcon,
  Notifications as NotificationsIcon,
  HelpOutline as HelpOutlineIcon,
  Close as CloseIcon,
  ExpandMore as ExpandMoreIcon,
} from '@mui/icons-material';
import {
  getDropdownPopoverPaperSx,
  getDropdownItemSx,
  getDropdownTriggerButtonSx,
  getDropdownTriggerTextSx,
  getDropdownChevronSx,
  getDropdownItemStateSx,
} from '../../constants/menuStyles';
import { useAppActions } from '../../contexts/AppContext';
import {
  SIDEBAR_PANEL_COLOR_KEY,
  getDefaultSidebarPanelBackground,
} from '../../constants/sidebarPanelColor';
import {
  WORK_ZONE_BG_MODE_KEY,
  WORK_ZONE_BG_CUSTOM_IMAGE_KEY,
  getWorkZoneBgMode,
  getWorkZoneCustomImage,
  type WorkZoneBgMode,
} from '../../constants/workZoneBackground';
import {
  isNotificationSupported,
  requestNotificationPermission,
  areNotificationsEnabled,
  setNotificationsEnabled,
} from '../../utils/browserNotifications';
import {
  setTabNotificationsEnabled,
  TAB_NOTIFICATIONS_ENABLED_KEY,
} from '../../utils/tabNotifications';
import {
  loadFollowUpSettings,
  saveFollowUpAutoGenerate,
  saveFollowUpShowScope,
  saveFollowUpClickAction,
  type FollowUpClickAction,
  type FollowUpShowScope,
} from '../../chat/followUpSettings';

const SIDEBAR_PALETTE = [
  { name: 'По умолчанию', value: '' },
  { name: 'Фиолетовый градиент', value: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)' },
  { name: 'Синий', value: '#2196f3' },
  { name: 'Тёмно-синий', value: '#1976d2' },
  { name: 'Бирюзовый', value: '#009688' },
  { name: 'Зелёный', value: '#4caf50' },
  { name: 'Тёмно-зелёный', value: '#2e7d32' },
  { name: 'Коричневый', value: '#795548' },
  { name: 'Серый', value: '#607d8b' },
  { name: 'Тёмно-серый', value: '#455a64' },
  { name: 'Тёмный графит', value: '#212128' },
  { name: 'Индиго', value: '#3f51b5' },
  { name: 'Пурпурный', value: '#9c27b0' },
  { name: 'Тёмно-пурпурный', value: '#673ab7' },
  { name: 'Красно-фиолетовый', value: '#7b1fa2' },
  { name: 'Оранжевый', value: '#ff9800' },
  { name: 'Тёмно-оранжевый', value: '#e65100' },
  { name: 'Глубокий чёрный', value: '#1D1D1F' },
  { name: 'Насыщенный тёмно-синий', value: '#05386B' },
  { name: 'Мягкий светло-зелёный', value: '#EDF5E1' },
];

const HEX_COLOR_RE = /^#[0-9A-Fa-f]{6}$/;
const MAX_WORK_ZONE_IMAGE_SIZE_MB = 4;
const MAX_WORK_ZONE_IMAGE_SIZE_BYTES = MAX_WORK_ZONE_IMAGE_SIZE_MB * 1024 * 1024;
const MAX_WORK_ZONE_IMAGE_DIMENSION_PX = 4096;
const CHAT_INPUT_CONTRAST_KEY = 'chat_input_contrast';
const CHAT_INPUT_COLOR_KEY = 'chat_input_color';

const CHAT_INPUT_PALETTE = [
  { name: 'По умолчанию', value: '' },
  { name: 'Лёгкое стекло', value: 'rgba(255, 255, 255, 0.10)' },
  { name: 'Плотное стекло', value: 'rgba(255, 255, 255, 0.18)' },
  { name: 'Тёмное стекло', value: 'rgba(20, 24, 32, 0.55)' },
  { name: 'Графит', value: 'rgba(32, 32, 38, 0.72)' },
  { name: 'Индиго', value: 'rgba(79, 70, 229, 0.45)' },
  { name: 'Фиолетовый', value: 'rgba(124, 58, 237, 0.45)' },
  { name: 'Бирюзовый', value: 'rgba(13, 148, 136, 0.40)' },
  { name: 'Изумруд', value: 'rgba(5, 150, 105, 0.38)' },
  { name: 'Тёплый янтарный', value: 'rgba(245, 158, 11, 0.36)' },
];

const normalizeHexInput = (rawValue: string): string => {
  const trimmed = rawValue.trim().replace(/[^0-9a-fA-F#]/g, '');
  if (!trimmed) return '';
  const withPrefix = trimmed.startsWith('#') ? trimmed : `#${trimmed}`;
  return withPrefix.slice(0, 7);
};

const readImageDimensions = (file: File): Promise<{ width: number; height: number }> =>
  new Promise((resolve, reject) => {
    const objectUrl = URL.createObjectURL(file);
    const image = new Image();
    image.onload = () => {
      resolve({ width: image.naturalWidth, height: image.naturalHeight });
      URL.revokeObjectURL(objectUrl);
    };
    image.onerror = () => {
      reject(new Error('invalid-image'));
      URL.revokeObjectURL(objectUrl);
    };
    image.src = objectUrl;
  });

export default function InterfaceSettings({ isDarkMode: isDarkModeProp }: { isDarkMode?: boolean } = {}) {
  const theme = useTheme();
  const isDarkMode = isDarkModeProp ?? theme.palette.mode === 'dark';
  const dropdownItemSx = useMemo(() => getDropdownItemSx(isDarkMode), [isDarkMode]);
  const dropdownTriggerSx = useMemo(() => getDropdownTriggerButtonSx(isDarkMode), [isDarkMode]);
  const dropdownTriggerTextSx = useMemo(() => getDropdownTriggerTextSx(isDarkMode), [isDarkMode]);
  const dropdownChevronSx = useMemo(() => getDropdownChevronSx(isDarkMode), [isDarkMode]);
  const [interfaceSettings, setInterfaceSettings] = useState(() => {
    const savedAutoTitle = localStorage.getItem('auto_generate_titles');
    const savedLargeTextAsFile = localStorage.getItem('large_text_as_file');
    const savedUserNoBorder = localStorage.getItem('user_no_border');
    const savedAssistantNoBorder = localStorage.getItem('assistant_no_border');
    const savedLeftAlignMessages = localStorage.getItem('left_align_messages');
    const savedWidescreenMode = localStorage.getItem('widescreen_mode');
    const savedShowUserName = localStorage.getItem('show_user_name');
    const savedEnableNotification = localStorage.getItem('enable_notification');
    const savedUseFoldersMode = localStorage.getItem('use_folders_mode');
    const savedBrowserNotifications = localStorage.getItem('browser_notifications_enabled');
    const savedTabNotifications = localStorage.getItem(TAB_NOTIFICATIONS_ENABLED_KEY);
    const savedShowDialoguesPanel = localStorage.getItem('show_dialogues_panel');
    const savedChatAutoscrollStreaming = localStorage.getItem('chat_autoscroll_streaming');
    const savedChatInputStyle = localStorage.getItem('chat_input_style');
    const savedChatInputColor = localStorage.getItem(CHAT_INPUT_COLOR_KEY) || '';
    const savedChatInputContrastRaw = localStorage.getItem(CHAT_INPUT_CONTRAST_KEY);
    const savedSidebarColor = localStorage.getItem(SIDEBAR_PANEL_COLOR_KEY) || '';
    const savedChatInputContrast = Number(savedChatInputContrastRaw);
    const followUpSettings = loadFollowUpSettings();
    return {
      autoGenerateTitles: savedAutoTitle !== null ? savedAutoTitle === 'true' : true,
      largeTextAsFile: savedLargeTextAsFile !== null ? savedLargeTextAsFile === 'true' : false,
      userNoBorder: savedUserNoBorder !== null ? savedUserNoBorder === 'true' : false,
      assistantNoBorder: savedAssistantNoBorder !== null ? savedAssistantNoBorder === 'true' : false,
      leftAlignMessages: savedLeftAlignMessages !== null ? savedLeftAlignMessages === 'true' : false,
      widescreenMode: savedWidescreenMode !== null ? savedWidescreenMode === 'true' : false,
      showUserName: savedShowUserName !== null ? savedShowUserName === 'true' : false,
      enableNotification: savedEnableNotification !== null ? savedEnableNotification === 'true' : false,
      useFoldersMode: savedUseFoldersMode !== null ? savedUseFoldersMode === 'true' : false, // По умолчанию выкл. (проекты)
      browserNotifications: savedBrowserNotifications !== null ? savedBrowserNotifications === 'true' : false,
      tabNotifications: savedTabNotifications !== null ? savedTabNotifications === 'true' : false,
      showDialoguesPanel: savedShowDialoguesPanel !== null ? savedShowDialoguesPanel === 'true' : true,
      autoScrollWhileStreaming:
        savedChatAutoscrollStreaming !== null ? savedChatAutoscrollStreaming === 'true' : false,
      chatInputStyle: (savedChatInputStyle as 'compact' | 'classic') || 'compact',
      chatInputColor: savedChatInputColor,
      chatInputContrast: Number.isFinite(savedChatInputContrast)
        ? Math.min(100, Math.max(20, savedChatInputContrast))
        : 35,
      sidebarPanelColor: savedSidebarColor,
      followUpAutoGenerate: followUpSettings.followUpAutoGenerate,
      followUpShowScope: followUpSettings.followUpShowScope,
      followUpClickAction: followUpSettings.followUpClickAction,
    };
  });

  const [colorPickerOpen, setColorPickerOpen] = useState(false);
  const [chatInputColorPickerOpen, setChatInputColorPickerOpen] = useState(false);
  const [workZoneImageDialogOpen, setWorkZoneImageDialogOpen] = useState(false);
  const [stylePopoverAnchor, setStylePopoverAnchor] = useState<HTMLElement | null>(null);
  const [colorPopoverAnchor, setColorPopoverAnchor] = useState<HTMLElement | null>(null);
  const [chatInputColorPopoverAnchor, setChatInputColorPopoverAnchor] = useState<HTMLElement | null>(null);
  const [workZoneBgPopoverAnchor, setWorkZoneBgPopoverAnchor] = useState<HTMLElement | null>(null);
  const [modelModePopoverAnchor, setModelModePopoverAnchor] = useState<HTMLElement | null>(null);
  const [followUpScopePopoverAnchor, setFollowUpScopePopoverAnchor] = useState<HTMLElement | null>(null);
  const [followUpClickPopoverAnchor, setFollowUpClickPopoverAnchor] = useState<HTMLElement | null>(null);
  const [customSidebarHex, setCustomSidebarHex] = useState<string>(() => {
    const savedColor = localStorage.getItem(SIDEBAR_PANEL_COLOR_KEY) || '';
    return HEX_COLOR_RE.test(savedColor) ? savedColor.toUpperCase() : '#667EEA';
  });

  const [workZoneBgMode, setWorkZoneBgMode] = useState<WorkZoneBgMode>(() => getWorkZoneBgMode());
  const [workZoneCustomImage, setWorkZoneCustomImage] = useState<string | null>(() => getWorkZoneCustomImage());
  const workZoneImageInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    const sync = () => setWorkZoneBgMode(getWorkZoneBgMode());
    sync();
    window.addEventListener('interfaceSettingsChanged', sync);
    return () => window.removeEventListener('interfaceSettingsChanged', sync);
  }, []);

  type ModelSelectorMode = 'settings' | 'workspace' | 'workspace_agent';
  const [modelSelectorMode, setModelSelectorModeState] = useState<ModelSelectorMode>(() => {
    const saved = localStorage.getItem('model_selector_mode');
    if (saved === 'settings' || saved === 'workspace' || saved === 'workspace_agent') return saved;
    // Миграция со старого булевого ключа
    const oldBool = localStorage.getItem('show_model_selector_in_settings');
    return oldBool === 'true' ? 'settings' : 'workspace_agent';
  });

  const MODEL_SELECTOR_OPTIONS: { value: ModelSelectorMode; label: string }[] = [
    { value: 'settings', label: 'Выбор модели в настройках' },
    { value: 'workspace', label: 'Стиль выбора модели: классический' },
    { value: 'workspace_agent', label: 'Стиль выбора модели: AtraChat' },
  ];

  const FOLLOW_UP_SCOPE_OPTIONS: { value: FollowUpShowScope; label: string }[] = [
    { value: 'last', label: 'Скрывать после следующего сообщения' },
    { value: 'all', label: 'Оставлять в истории чата' },
  ];

  const FOLLOW_UP_CLICK_OPTIONS: { value: FollowUpClickAction; label: string }[] = [
    { value: 'insert', label: 'Вставить в поле ввода' },
    { value: 'send', label: 'Отправить сразу' },
  ];

  const handleModelSelectorModeChange = (mode: ModelSelectorMode) => {
    setModelSelectorModeState(mode);
    localStorage.setItem('model_selector_mode', mode);
    // Синхронизируем старый ключ для обратной совместимости
    localStorage.setItem('show_model_selector_in_settings', String(mode === 'settings'));
    window.dispatchEvent(new Event('interfaceSettingsChanged'));
    showNotification('success', 'Настройки интерфейса сохранены');
    setModelModePopoverAnchor(null);
  };

  const { showNotification } = useAppActions();

  const handleSidebarColorSelect = (value: string) => {
    if (value === 'pick') {
      setColorPickerOpen(true);
      return;
    }
    const newSettings = { ...interfaceSettings, sidebarPanelColor: value };
    setInterfaceSettings(newSettings);
    localStorage.setItem(SIDEBAR_PANEL_COLOR_KEY, value);
    window.dispatchEvent(new CustomEvent('sidebarColorChanged', { detail: value }));
    showNotification('success', value ? 'Цвет панелей изменён' : 'Цвет панелей сброшен');
  };

  const handlePaletteColorPick = (value: string) => {
    const newSettings = { ...interfaceSettings, sidebarPanelColor: value };
    setInterfaceSettings(newSettings);
    localStorage.setItem(SIDEBAR_PANEL_COLOR_KEY, value);
    window.dispatchEvent(new CustomEvent('sidebarColorChanged', { detail: value }));
    setColorPickerOpen(false);
    showNotification('success', 'Цвет панелей применён');
  };

  const handleApplyCustomSidebarColor = () => {
    if (!HEX_COLOR_RE.test(customSidebarHex)) {
      showNotification('warning', 'Введите HEX-цвет в формате #RRGGBB');
      return;
    }
    handlePaletteColorPick(customSidebarHex.toUpperCase());
  };

  const selectedSidebarPreset = SIDEBAR_PALETTE.find((item) => item.value === interfaceSettings.sidebarPanelColor);
  const selectedChatInputPreset = CHAT_INPUT_PALETTE.find((item) => item.value === interfaceSettings.chatInputColor);

  const handleChatInputStyleChange = (value: 'compact' | 'classic') => {
    const newSettings = { ...interfaceSettings, chatInputStyle: value };
    setInterfaceSettings(newSettings);
    localStorage.setItem('chat_input_style', value);
    window.dispatchEvent(new Event('interfaceSettingsChanged'));
    showNotification('success', 'Стиль поля ввода изменён');
  };

  const handleChatInputColorSelect = (value: string) => {
    const newSettings = { ...interfaceSettings, chatInputColor: value };
    setInterfaceSettings(newSettings);
    localStorage.setItem(CHAT_INPUT_COLOR_KEY, value);
    window.dispatchEvent(new Event('interfaceSettingsChanged'));
    showNotification('success', value ? 'Цвет окна ввода изменён' : 'Цвет окна ввода сброшен');
  };

  const handleChatInputContrastChange = (_: Event, value: number | number[]) => {
    const next = Array.isArray(value) ? value[0] : value;
    setInterfaceSettings((prev) => ({ ...prev, chatInputContrast: next }));
    localStorage.setItem(CHAT_INPUT_CONTRAST_KEY, String(next));
    window.dispatchEvent(new Event('interfaceSettingsChanged'));
  };

  const handleWorkZoneBgMode = (mode: WorkZoneBgMode) => {
    if (mode === 'custom' && !workZoneCustomImage) {
      showNotification('warning', 'Сначала загрузите своё изображение для фона');
      return;
    }
    setWorkZoneBgMode(mode);
    localStorage.setItem(WORK_ZONE_BG_MODE_KEY, mode);
    window.dispatchEvent(new Event('interfaceSettingsChanged'));
    showNotification(
      'success',
      mode === 'starry'
        ? 'Включён фон «Звёздное небо» в рабочей зоне'
        : mode === 'snowfall'
          ? 'Включён фон «Снегопад» в рабочей зоне'
      : mode === 'custom'
            ? 'Включён пользовательский фон рабочей зоны'
          : 'Фон рабочей зоны: по умолчанию',
    );
    setWorkZoneBgPopoverAnchor(null);
  };

  const handleUploadWorkZonePhoto = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    if (!file.type.startsWith('image/')) {
      showNotification('warning', 'Можно загрузить только изображение');
      event.target.value = '';
      return;
    }
    if (file.size > MAX_WORK_ZONE_IMAGE_SIZE_BYTES) {
      showNotification('warning', `Размер изображения должен быть не больше ${MAX_WORK_ZONE_IMAGE_SIZE_MB} МБ`);
      event.target.value = '';
      return;
    }
    try {
      const { width, height } = await readImageDimensions(file);
      if (width > MAX_WORK_ZONE_IMAGE_DIMENSION_PX || height > MAX_WORK_ZONE_IMAGE_DIMENSION_PX) {
        showNotification(
          'warning',
          `Максимальный размер изображения: ${MAX_WORK_ZONE_IMAGE_DIMENSION_PX}x${MAX_WORK_ZONE_IMAGE_DIMENSION_PX} px`,
        );
        event.target.value = '';
        return;
      }
    } catch {
      showNotification('error', 'Не удалось прочитать размеры изображения');
      event.target.value = '';
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      const result = typeof reader.result === 'string' ? reader.result : null;
      if (!result) {
        showNotification('error', 'Не удалось прочитать изображение');
        return;
      }
      localStorage.setItem(WORK_ZONE_BG_CUSTOM_IMAGE_KEY, result);
      setWorkZoneCustomImage(result);
      localStorage.setItem(WORK_ZONE_BG_MODE_KEY, 'custom');
      setWorkZoneBgMode('custom');
      window.dispatchEvent(new Event('interfaceSettingsChanged'));
      showNotification('success', 'Фото загружено и установлено фоном рабочей зоны');
    };
    reader.onerror = () => showNotification('error', 'Ошибка при загрузке изображения');
    reader.readAsDataURL(file);
    event.target.value = '';
  };

  const handleRemoveWorkZonePhoto = () => {
    localStorage.removeItem(WORK_ZONE_BG_CUSTOM_IMAGE_KEY);
    setWorkZoneCustomImage(null);
    if (workZoneBgMode === 'custom') {
      localStorage.setItem(WORK_ZONE_BG_MODE_KEY, 'default');
      setWorkZoneBgMode('default');
    }
    window.dispatchEvent(new Event('interfaceSettingsChanged'));
    showNotification('success', 'Пользовательский фон удалён');
  };

  const handleApplyCustomWorkZoneBg = () => {
    if (!workZoneCustomImage) {
      showNotification('warning', 'Сначала загрузите своё изображение для фона');
      return;
    }
    handleWorkZoneBgMode('custom');
    setWorkZoneImageDialogOpen(false);
  };

  const handleInterfaceSettingChange = async (key: keyof typeof interfaceSettings, value: boolean) => {
    // Для браузерных уведомлений запрашиваем разрешение при включении
    if (key === 'browserNotifications' && value) {
      if (!isNotificationSupported()) {
        showNotification('warning', 'Браузерные уведомления не поддерживаются вашим браузером');
        return;
      }
      
      const permissionGranted = await requestNotificationPermission();
      if (!permissionGranted) {
        showNotification('warning', 'Разрешение на уведомления не было предоставлено');
        return;
      }
    }
    
    const newSettings = { ...interfaceSettings, [key]: value };
    setInterfaceSettings(newSettings);
    localStorage.setItem('auto_generate_titles', String(newSettings.autoGenerateTitles));
    localStorage.setItem('large_text_as_file', String(newSettings.largeTextAsFile));
    localStorage.setItem('user_no_border', String(newSettings.userNoBorder));
    localStorage.setItem('assistant_no_border', String(newSettings.assistantNoBorder));
    localStorage.setItem('left_align_messages', String(newSettings.leftAlignMessages));
    localStorage.setItem('widescreen_mode', String(newSettings.widescreenMode));
    localStorage.setItem('show_user_name', String(newSettings.showUserName));
    localStorage.setItem('enable_notification', String(newSettings.enableNotification));
    localStorage.setItem('use_folders_mode', String(newSettings.useFoldersMode));
    localStorage.setItem('show_dialogues_panel', String(newSettings.showDialoguesPanel));
    localStorage.setItem('chat_autoscroll_streaming', String(newSettings.autoScrollWhileStreaming));
    setNotificationsEnabled(newSettings.browserNotifications);
    setTabNotificationsEnabled(newSettings.tabNotifications);

    // Отправляем кастомное событие для обновления настроек в том же окне
    window.dispatchEvent(new Event('interfaceSettingsChanged'));
    showNotification('success', 'Настройки интерфейса сохранены');
  };

  const handleFollowUpAutoGenerateChange = (value: boolean) => {
    const newSettings = { ...interfaceSettings, followUpAutoGenerate: value };
    setInterfaceSettings(newSettings);
    saveFollowUpAutoGenerate(value);
    window.dispatchEvent(new Event('interfaceSettingsChanged'));
    showNotification('success', value ? 'Подсказки в чате включены' : 'Подсказки в чате отключены');
  };

  const handleFollowUpShowScopeChange = (value: FollowUpShowScope) => {
    const newSettings = { ...interfaceSettings, followUpShowScope: value };
    setInterfaceSettings(newSettings);
    saveFollowUpShowScope(value);
    window.dispatchEvent(new Event('interfaceSettingsChanged'));
    showNotification('success', 'Настройки подсказок сохранены');
    setFollowUpScopePopoverAnchor(null);
  };

  const handleFollowUpClickActionChange = (value: FollowUpClickAction) => {
    const newSettings = { ...interfaceSettings, followUpClickAction: value };
    setInterfaceSettings(newSettings);
    saveFollowUpClickAction(value);
    window.dispatchEvent(new Event('interfaceSettingsChanged'));
    showNotification('success', 'Настройки подсказок сохранены');
    setFollowUpClickPopoverAnchor(null);
  };

  return (
    <Box sx={{ p: 3 }}>
      <Card>
        <CardContent>
          <Typography variant="h6" gutterBottom sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <ComputerIcon color="primary" />
            Настройки интерфейса
          </Typography>

          <List>
            {/* Автогенерация заголовков */}
            <ListItem
              sx={{
                px: 0,
                py: 2,
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
              }}
            >
              <ListItemText
                primary="Автогенерация заголовков"
                primaryTypographyProps={{
                  variant: 'body1',
                  fontWeight: 500,
                }}
              />
              <Switch
                checked={interfaceSettings.autoGenerateTitles}
                onChange={(e) => handleInterfaceSettingChange('autoGenerateTitles', e.target.checked)}
              />
            </ListItem>

            <Divider />

            <ListItem
              sx={{
                px: 0,
                py: 2,
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
              }}
            >
              <ListItemText
                primary="Автопрокрутка чата при ответе модели"
                secondary="Если выключено, окно чата не прокручивается к концу по мере появления текста от модели"
                primaryTypographyProps={{
                  variant: 'body1',
                  fontWeight: 500,
                }}
                secondaryTypographyProps={{
                  variant: 'body2',
                  sx: { mt: 0.5 },
                }}
              />
              <Switch
                checked={interfaceSettings.autoScrollWhileStreaming}
                onChange={(e) => handleInterfaceSettingChange('autoScrollWhileStreaming', e.target.checked)}
              />
            </ListItem>

            <Divider />

            <ListItem
              sx={{
                px: 0,
                py: 2,
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
              }}
            >
              <ListItemText
                primary="Подсказки в чате"
                secondary="Динамические подсказки под ответом модели и «Предложено» в новом чате"
                primaryTypographyProps={{
                  variant: 'body1',
                  fontWeight: 500,
                }}
                secondaryTypographyProps={{
                  variant: 'body2',
                  sx: { mt: 0.5 },
                }}
              />
              <Switch
                checked={interfaceSettings.followUpAutoGenerate}
                onChange={(e) => handleFollowUpAutoGenerateChange(e.target.checked)}
              />
            </ListItem>

            {interfaceSettings.followUpAutoGenerate ? (
              <>
                <ListItem
                  sx={{
                    px: 0,
                    py: 2,
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    gap: 2,
                  }}
                >
                  <ListItemText
                    primary="Где показывать подсказки"
                    primaryTypographyProps={{ variant: 'body1', fontWeight: 500 }}
                  />
                  <Box sx={{ minWidth: 220, flexShrink: 0 }}>
                    <Box onClick={(e) => setFollowUpScopePopoverAnchor(e.currentTarget)} sx={dropdownTriggerSx}>
                      <Typography sx={{ ...dropdownTriggerTextSx, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {FOLLOW_UP_SCOPE_OPTIONS.find((o) => o.value === interfaceSettings.followUpShowScope)?.label ?? ''}
                      </Typography>
                      <ExpandMoreIcon sx={{ ...dropdownChevronSx, transform: followUpScopePopoverAnchor ? 'rotate(180deg)' : 'none', flexShrink: 0 }} />
                    </Box>
                    <Popover
                      open={Boolean(followUpScopePopoverAnchor)}
                      anchorEl={followUpScopePopoverAnchor}
                      onClose={() => setFollowUpScopePopoverAnchor(null)}
                      anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
                      transformOrigin={{ vertical: 'top', horizontal: 'left' }}
                      slotProps={{ paper: { sx: getDropdownPopoverPaperSx(followUpScopePopoverAnchor, isDarkMode) } }}
                    >
                      <Box sx={{ py: 0.5 }}>
                        {FOLLOW_UP_SCOPE_OPTIONS.map((opt) => (
                          <Box
                            key={opt.value}
                            onClick={() => handleFollowUpShowScopeChange(opt.value)}
                            sx={{
                              ...dropdownItemSx,
                              ...getDropdownItemStateSx(isDarkMode, interfaceSettings.followUpShowScope === opt.value),
                            }}
                          >
                            {opt.label}
                          </Box>
                        ))}
                      </Box>
                    </Popover>
                  </Box>
                </ListItem>

                <ListItem
                  sx={{
                    px: 0,
                    py: 2,
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    gap: 2,
                  }}
                >
                  <ListItemText
                    primary="Действие при клике на подсказку"
                    primaryTypographyProps={{ variant: 'body1', fontWeight: 500 }}
                  />
                  <Box sx={{ minWidth: 220, flexShrink: 0 }}>
                    <Box onClick={(e) => setFollowUpClickPopoverAnchor(e.currentTarget)} sx={dropdownTriggerSx}>
                      <Typography sx={{ ...dropdownTriggerTextSx, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {FOLLOW_UP_CLICK_OPTIONS.find((o) => o.value === interfaceSettings.followUpClickAction)?.label ?? ''}
                      </Typography>
                      <ExpandMoreIcon sx={{ ...dropdownChevronSx, transform: followUpClickPopoverAnchor ? 'rotate(180deg)' : 'none', flexShrink: 0 }} />
                    </Box>
                    <Popover
                      open={Boolean(followUpClickPopoverAnchor)}
                      anchorEl={followUpClickPopoverAnchor}
                      onClose={() => setFollowUpClickPopoverAnchor(null)}
                      anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
                      transformOrigin={{ vertical: 'top', horizontal: 'left' }}
                      slotProps={{ paper: { sx: getDropdownPopoverPaperSx(followUpClickPopoverAnchor, isDarkMode) } }}
                    >
                      <Box sx={{ py: 0.5 }}>
                        {FOLLOW_UP_CLICK_OPTIONS.map((opt) => (
                          <Box
                            key={opt.value}
                            onClick={() => handleFollowUpClickActionChange(opt.value)}
                            sx={{
                              ...dropdownItemSx,
                              ...getDropdownItemStateSx(isDarkMode, interfaceSettings.followUpClickAction === opt.value),
                            }}
                          >
                            {opt.label}
                          </Box>
                        ))}
                      </Box>
                    </Popover>
                  </Box>
                </ListItem>
              </>
            ) : null}

            <Divider />

            {/* Вставить большой текст как файл */}
            <ListItem
              sx={{
                px: 0,
                py: 2,
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
              }}
            >
              <ListItemText
                primary="Вставить большой текст как файл"
                primaryTypographyProps={{
                  variant: 'body1',
                  fontWeight: 500,
                }}
              />
              <Switch
                checked={interfaceSettings.largeTextAsFile}
                onChange={(e) => handleInterfaceSettingChange('largeTextAsFile', e.target.checked)}
              />
            </ListItem>

            <Divider />

            {/* Отображение имени пользователя */}
            <ListItem
              sx={{
                px: 0,
                py: 2,
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
              }}
            >
              <ListItemText
                primary="Отображать имя пользователя вместо 'Вы' в чате"
                primaryTypographyProps={{
                  variant: 'body1',
                  fontWeight: 500,
                }}
              />
              <Switch
                checked={interfaceSettings.showUserName}
                onChange={(e) => handleInterfaceSettingChange('showUserName', e.target.checked)}
              />
            </ListItem>

            <Divider />

            {/* Режим отображения сообщений пользователя */}
            <ListItem
              sx={{
                px: 0,
                py: 2,
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
              }}
            >
              <ListItemText
                primary="Сообщения пользователя без рамки"
                primaryTypographyProps={{
                  variant: 'body1',
                  fontWeight: 500,
                }}
              />
              <Switch
                checked={interfaceSettings.userNoBorder}
                onChange={(e) => handleInterfaceSettingChange('userNoBorder', e.target.checked)}
              />
            </ListItem>

            <Divider />

            {/* Режим отображения сообщений ассистента */}
            <ListItem
              sx={{
                px: 0,
                py: 2,
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
              }}
            >
              <ListItemText
                primary="Сообщения ассистента без рамки"
                primaryTypographyProps={{
                  variant: 'body1',
                  fontWeight: 500,
                }}
              />
              <Switch
                checked={interfaceSettings.assistantNoBorder}
                onChange={(e) => handleInterfaceSettingChange('assistantNoBorder', e.target.checked)}
              />
            </ListItem>

            <Divider />

            {/* Включить оповещение (звуковое) */}
            <ListItem
              sx={{
                px: 0,
                py: 2,
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
              }}
            >
              <ListItemText
                primary="Включить звуковое оповещение"
                secondary="Звуковое оповещение при готовности сообщения"
                primaryTypographyProps={{
                  variant: 'body1',
                  fontWeight: 500,
                }}
                secondaryTypographyProps={{
                  variant: 'body2',
                  sx: { mt: 0.5 }
                }}
              />
              <Switch
                checked={interfaceSettings.enableNotification}
                onChange={(e) => handleInterfaceSettingChange('enableNotification', e.target.checked)}
              />
            </ListItem>

            <Divider />

            {/* Браузерные уведомления */}
            <ListItem
              sx={{
                px: 0,
                py: 2,
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
              }}
            >
              <ListItemText
                primary={
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                    Браузерные уведомления
                    <Tooltip 
                      title="Показывать всплывающие уведомления рядом с иконкой браузера, когда ассистент завершает генерацию ответа. Требуется разрешение браузера." 
                      arrow
                    >
                      <IconButton 
                        size="small" 
                        sx={{ 
                          p: 0,
                          ml: 0.5,
                          opacity: 0.7,
                          '&:hover': {
                            opacity: 1,
                            '& .MuiSvgIcon-root': {
                              color: 'primary.main',
                            },
                          },
                        }}
                        onClick={(e) => e.stopPropagation()}
                      >
                        <HelpOutlineIcon fontSize="small" color="action" />
                      </IconButton>
                    </Tooltip>
                  </Box>
                }
                secondary={
                  !isNotificationSupported() 
                    ? 'Не поддерживается вашим браузером'
                    : interfaceSettings.browserNotifications
                    ? 'Уведомления включены'
                    : 'Уведомления выключены'
                }
                primaryTypographyProps={{
                  variant: 'body1',
                  fontWeight: 500,
                }}
                secondaryTypographyProps={{
                  variant: 'body2',
                  sx: { mt: 0.5 }
                }}
              />
              <Switch
                checked={interfaceSettings.browserNotifications && isNotificationSupported()}
                disabled={!isNotificationSupported()}
                onChange={(e) => handleInterfaceSettingChange('browserNotifications', e.target.checked)}
              />
            </ListItem>

            <Divider />

            {/* Счётчик на вкладке браузера */}
            <ListItem
              sx={{
                px: 0,
                py: 2,
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
              }}
            >
              <ListItemText
                primary={
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                    Уведомления
                    <Tooltip
                      title="Показывать счётчик на вкладке браузера (в заголовке и на иконке), когда завершится генерация ответа или транскрибация, пока вы на другой вкладке или в другом окне."
                      arrow
                    >
                      <IconButton
                        size="small"
                        sx={{
                          p: 0,
                          ml: 0.5,
                          opacity: 0.7,
                          '&:hover': {
                            opacity: 1,
                            '& .MuiSvgIcon-root': {
                              color: 'primary.main',
                            },
                          },
                        }}
                        onClick={(e) => e.stopPropagation()}
                      >
                        <HelpOutlineIcon fontSize="small" color="action" />
                      </IconButton>
                    </Tooltip>
                  </Box>
                }
                secondary={
                  interfaceSettings.tabNotifications
                    ? 'Счётчик на вкладке включён'
                    : 'Счётчик на вкладке выключен'
                }
                primaryTypographyProps={{
                  variant: 'body1',
                  fontWeight: 500,
                }}
                secondaryTypographyProps={{
                  variant: 'body2',
                  sx: { mt: 0.5 },
                }}
              />
              <Switch
                checked={interfaceSettings.tabNotifications}
                onChange={(e) => handleInterfaceSettingChange('tabNotifications', e.target.checked)}
              />
            </ListItem>

            <Divider />

            {/* Выравнивание сообщений */}
            <ListItem
              sx={{
                px: 0,
                py: 2,
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
              }}
            >
              <ListItemText
                primary="Выравнивание по левому краю"
                primaryTypographyProps={{
                  variant: 'body1',
                  fontWeight: 500,
                }}
              />
              <Switch
                checked={interfaceSettings.leftAlignMessages}
                onChange={(e) => handleInterfaceSettingChange('leftAlignMessages', e.target.checked)}
              />
            </ListItem>

            <Divider />

            {/* Режим широкоформатного экрана */}
            <ListItem
              sx={{
                px: 0,
                py: 2,
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
              }}
            >
              <ListItemText
                primary="Режим широкоформатного экрана"
                primaryTypographyProps={{
                  variant: 'body1',
                  fontWeight: 500,
                }}
              />
              <Switch
                checked={interfaceSettings.widescreenMode}
                onChange={(e) => handleInterfaceSettingChange('widescreenMode', e.target.checked)}
              />
            </ListItem>

            <Divider />

            {/* Место расположения выбора модели */}
            <ListItem
              sx={{
                px: 0,
                py: 2,
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                gap: 2,
              }}
            >
              <ListItemText
                primary="Место расположения выбора модели"
                primaryTypographyProps={{ variant: 'body1', fontWeight: 500 }}
                secondary={
                  modelSelectorMode === 'settings'
                    ? 'Выбор модели доступен в разделе Настройки → Модели'
                    : modelSelectorMode === 'workspace'
                    ? 'В рабочей зоне показывается классический селектор моделей'
                    : 'В рабочей зоне показывается селектор AstraChat (агент/модель)'
                }
                secondaryTypographyProps={{ variant: 'body2', sx: { mt: 0.5 } }}
              />
              <Box sx={{ minWidth: 220, flexShrink: 0 }}>
                <Box onClick={(e) => setModelModePopoverAnchor(e.currentTarget)} sx={dropdownTriggerSx}>
                  <Typography sx={{ ...dropdownTriggerTextSx, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {MODEL_SELECTOR_OPTIONS.find((o) => o.value === modelSelectorMode)?.label ?? ''}
                  </Typography>
                  <ExpandMoreIcon sx={{ ...dropdownChevronSx, transform: modelModePopoverAnchor ? 'rotate(180deg)' : 'none', flexShrink: 0 }} />
                </Box>
                <Popover
                  open={Boolean(modelModePopoverAnchor)}
                  anchorEl={modelModePopoverAnchor}
                  onClose={() => setModelModePopoverAnchor(null)}
                  anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
                  transformOrigin={{ vertical: 'top', horizontal: 'left' }}
                  slotProps={{ paper: { sx: getDropdownPopoverPaperSx(modelModePopoverAnchor, isDarkMode) } }}
                >
                  <Box sx={{ py: 0.5 }}>
                    {MODEL_SELECTOR_OPTIONS.map((opt) => (
                      <Box
                        key={opt.value}
                        onClick={() => handleModelSelectorModeChange(opt.value)}
                        sx={{
                          ...dropdownItemSx,
                          ...getDropdownItemStateSx(isDarkMode, modelSelectorMode === opt.value),
                        }}
                      >
                        {opt.label}
                      </Box>
                    ))}
                  </Box>
                </Popover>
              </Box>
            </ListItem>

            <Divider />

            {/* Использовать папки вместо проектов */}
            <ListItem
              sx={{
                px: 0,
                py: 2,
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
              }}
            >
              <ListItemText
                primary="Использовать папки"
                primaryTypographyProps={{
                  variant: 'body1',
                  fontWeight: 500,
                }}
                secondary={interfaceSettings.useFoldersMode 
                  ? "Включен функционал папок. Проекты недоступны." 
                  : "Включен функционал проектов. Папки недоступны."}
                secondaryTypographyProps={{
                  variant: 'body2',
                  sx: { mt: 0.5 }
                }}
              />
              <Switch
                checked={interfaceSettings.useFoldersMode}
                onChange={(e) => handleInterfaceSettingChange('useFoldersMode', e.target.checked)}
              />
            </ListItem>

            <Divider />

            {/* Стиль поля ввода */}
            <ListItem
              sx={{
                px: 0,
                py: 2,
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                gap: 2,
              }}
            >
              <ListItemText
                primary="Стиль поля ввода"
                primaryTypographyProps={{ variant: 'body1', fontWeight: 500 }}
                secondary="Компактный — пилюльная форма с кнопками внутри. Классический — прямоугольник с тулбаром кнопок снизу."
                secondaryTypographyProps={{ variant: 'body2', sx: { mt: 0.5 } }}
              />
              <Box sx={{ minWidth: 180, flexShrink: 0 }}>
                <Box onClick={(e) => setStylePopoverAnchor(e.currentTarget)} sx={dropdownTriggerSx}>
                  <Typography sx={dropdownTriggerTextSx}>
                    {interfaceSettings.chatInputStyle === 'compact' ? 'Компактный' : 'Классический'}
                  </Typography>
                  <ExpandMoreIcon sx={{ ...dropdownChevronSx, transform: stylePopoverAnchor ? 'rotate(180deg)' : 'none' }} />
                </Box>
                <Popover
                  open={Boolean(stylePopoverAnchor)}
                  anchorEl={stylePopoverAnchor}
                  onClose={() => setStylePopoverAnchor(null)}
                  anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
                  transformOrigin={{ vertical: 'top', horizontal: 'left' }}
                  slotProps={{ paper: { sx: getDropdownPopoverPaperSx(stylePopoverAnchor, isDarkMode) } }}
                >
                  <Box sx={{ py: 0.5 }}>
                    {(['compact', 'classic'] as const).map((v) => (
                      <Box
                        key={v}
                        onClick={() => { handleChatInputStyleChange(v); setStylePopoverAnchor(null); }}
                        sx={{
                          ...dropdownItemSx,
                          ...getDropdownItemStateSx(isDarkMode, interfaceSettings.chatInputStyle === v),
                        }}
                      >
                        {v === 'compact' ? 'Компактный' : 'Классический'}
                      </Box>
                    ))}
                  </Box>
                </Popover>
              </Box>
            </ListItem>

            <Divider />

            {/* Цвет окна ввода */}
            <ListItem
              sx={{
                px: 0,
                py: 2,
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                gap: 2,
                flexWrap: 'wrap',
              }}
            >
              <ListItemText
                primary="Цвет окна ввода"
                primaryTypographyProps={{ variant: 'body1', fontWeight: 500 }}
                secondary="Цвет пилюли ввода поверх рабочей зоны"
                secondaryTypographyProps={{ variant: 'body2', sx: { mt: 0.5 } }}
              />
              <Box sx={{ minWidth: 220, flexShrink: 0 }}>
                <Box onClick={(e) => setChatInputColorPopoverAnchor(e.currentTarget)} sx={dropdownTriggerSx}>
                  <Typography sx={dropdownTriggerTextSx}>
                    {!interfaceSettings.chatInputColor
                      ? 'По умолчанию'
                      : selectedChatInputPreset
                        ? selectedChatInputPreset.name
                        : 'Пользовательский'}
                  </Typography>
                  <ExpandMoreIcon sx={{ ...dropdownChevronSx, transform: chatInputColorPopoverAnchor ? 'rotate(180deg)' : 'none' }} />
                </Box>
                <Popover
                  open={Boolean(chatInputColorPopoverAnchor)}
                  anchorEl={chatInputColorPopoverAnchor}
                  onClose={() => setChatInputColorPopoverAnchor(null)}
                  anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
                  transformOrigin={{ vertical: 'top', horizontal: 'left' }}
                  slotProps={{ paper: { sx: getDropdownPopoverPaperSx(chatInputColorPopoverAnchor, isDarkMode) } }}
                >
                  <Box sx={{ py: 0.5 }}>
                    <Box
                      onClick={() => { handleChatInputColorSelect(''); setChatInputColorPopoverAnchor(null); }}
                      sx={{
                        ...dropdownItemSx,
                        ...getDropdownItemStateSx(isDarkMode, !interfaceSettings.chatInputColor),
                      }}
                    >
                      По умолчанию
                    </Box>
                    <Box
                      onClick={() => { setChatInputColorPopoverAnchor(null); setChatInputColorPickerOpen(true); }}
                      sx={{ ...dropdownItemSx, ...getDropdownItemStateSx(isDarkMode, false) }}
                    >
                      Цвет и контрастность окна ввода
                    </Box>
                  </Box>
                </Popover>
              </Box>
            </ListItem>

            <Divider />

            {/* Панель с диалогами (навигация по сообщениям) */}
            <ListItem
              sx={{
                px: 0,
                py: 2,
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
              }}
            >
              <ListItemText
                primary="Панель с диалогами"
                primaryTypographyProps={{
                  variant: 'body1',
                  fontWeight: 500,
                }}
                secondary="Вертикальная панель справа со списком вопросов для навигации по сообщениям"
                secondaryTypographyProps={{
                  variant: 'body2',
                  sx: { mt: 0.5 }
                }}
              />
              <Switch
                checked={interfaceSettings.showDialoguesPanel}
                onChange={(e) => handleInterfaceSettingChange('showDialoguesPanel', e.target.checked)}
              />
            </ListItem>

            <Divider />

            {/* Фон рабочей зоны (чат, проект) */}
            <ListItem
              sx={{
                px: 0,
                py: 2,
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                flexWrap: 'wrap',
                gap: 2,
              }}
            >
              <ListItemText
                primary="Фон рабочей зоны"
                primaryTypographyProps={{ variant: 'body1', fontWeight: 500 }}
                secondary="Область чата и страницы проекта (боковые панели без изменений)"
                secondaryTypographyProps={{ variant: 'body2', sx: { mt: 0.5 } }}
              />
              <Box sx={{ minWidth: 200, flexShrink: 0 }}>
                <Box onClick={(e) => setWorkZoneBgPopoverAnchor(e.currentTarget)} sx={dropdownTriggerSx}>
                  <Typography sx={dropdownTriggerTextSx}>
                    {workZoneBgMode === 'starry'
                      ? 'Звёздное небо'
                      : workZoneBgMode === 'snowfall'
                        ? 'Снегопад'
                        : workZoneBgMode === 'custom'
                          ? 'Своё изображение'
                        : 'По умолчанию'}
                  </Typography>
                  <ExpandMoreIcon
                    sx={{ ...dropdownChevronSx, transform: workZoneBgPopoverAnchor ? 'rotate(180deg)' : 'none' }}
                  />
                </Box>
                <Popover
                  open={Boolean(workZoneBgPopoverAnchor)}
                  anchorEl={workZoneBgPopoverAnchor}
                  onClose={() => setWorkZoneBgPopoverAnchor(null)}
                  anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
                  transformOrigin={{ vertical: 'top', horizontal: 'left' }}
                  slotProps={{ paper: { sx: getDropdownPopoverPaperSx(workZoneBgPopoverAnchor, isDarkMode) } }}
                >
                  <Box sx={{ py: 0.5 }}>
                    <Box
                      onClick={() => handleWorkZoneBgMode('default')}
                      sx={{
                        ...dropdownItemSx,
                        ...getDropdownItemStateSx(isDarkMode, workZoneBgMode === 'default'),
                      }}
                    >
                      По умолчанию
                    </Box>
                    <Box
                      onClick={() => handleWorkZoneBgMode('starry')}
                      sx={{
                        ...dropdownItemSx,
                        ...getDropdownItemStateSx(isDarkMode, workZoneBgMode === 'starry'),
                      }}
                    >
                      Звёздное небо
                    </Box>
                    <Box
                      onClick={() => handleWorkZoneBgMode('snowfall')}
                      sx={{
                        ...dropdownItemSx,
                        ...getDropdownItemStateSx(isDarkMode, workZoneBgMode === 'snowfall'),
                      }}
                    >
                      Снегопад
                    </Box>
                    <Box
                      onClick={() => {
                        setWorkZoneBgPopoverAnchor(null);
                        setWorkZoneImageDialogOpen(true);
                      }}
                      sx={{
                        ...dropdownItemSx,
                        ...getDropdownItemStateSx(isDarkMode, workZoneBgMode === 'custom'),
                      }}
                    >
                      Своё изображение
                    </Box>
                  </Box>
                </Popover>
              </Box>
            </ListItem>

            <Divider />

            {/* Цвет боковых панелей */}
            <ListItem
              sx={{
                px: 0,
                py: 2,
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                flexWrap: 'wrap',
                gap: 2,
              }}
            >
              <ListItemText
                primary="Цвет боковых панелей"
                primaryTypographyProps={{ variant: 'body1', fontWeight: 500 }}
                secondary="Левой и правой панелей (сайдбар с чатами и панель действий)"
                secondaryTypographyProps={{ variant: 'body2', sx: { mt: 0.5 } }}
              />
              <Box sx={{ minWidth: 200, flexShrink: 0 }}>
                <Box onClick={(e) => setColorPopoverAnchor(e.currentTarget)} sx={dropdownTriggerSx}>
                  <Typography sx={dropdownTriggerTextSx}>
                    {!interfaceSettings.sidebarPanelColor
                      ? 'По умолчанию'
                      : selectedSidebarPreset
                        ? selectedSidebarPreset.name
                      : interfaceSettings.sidebarPanelColor.startsWith('#')
                        ? `Пользовательский (${interfaceSettings.sidebarPanelColor})`
                        : 'Пользовательский'}
                  </Typography>
                  <ExpandMoreIcon sx={{ ...dropdownChevronSx, transform: colorPopoverAnchor ? 'rotate(180deg)' : 'none' }} />
                </Box>
                <Popover
                  open={Boolean(colorPopoverAnchor)}
                  anchorEl={colorPopoverAnchor}
                  onClose={() => setColorPopoverAnchor(null)}
                  anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
                  transformOrigin={{ vertical: 'top', horizontal: 'left' }}
                  slotProps={{ paper: { sx: getDropdownPopoverPaperSx(colorPopoverAnchor, isDarkMode) } }}
                >
                  <Box sx={{ py: 0.5 }}>
                    <Box
                      onClick={() => { handleSidebarColorSelect(''); setColorPopoverAnchor(null); }}
                      sx={{
                        ...dropdownItemSx,
                        ...getDropdownItemStateSx(isDarkMode, !interfaceSettings.sidebarPanelColor),
                      }}
                    >
                      По умолчанию
                    </Box>
                    {interfaceSettings.sidebarPanelColor && (
                      <Box
                        onClick={() => setColorPopoverAnchor(null)}
                        sx={{ ...dropdownItemSx, ...getDropdownItemStateSx(isDarkMode, false) }}
                      >
                        {interfaceSettings.sidebarPanelColor.startsWith('#')
                          ? `Пользовательский (${interfaceSettings.sidebarPanelColor})`
                          : 'Пользовательский'}
                      </Box>
                    )}
                    <Box
                      onClick={() => { setColorPopoverAnchor(null); setColorPickerOpen(true); }}
                      sx={{ ...dropdownItemSx, ...getDropdownItemStateSx(isDarkMode, false) }}
                    >
                      Выбрать цвет
                    </Box>
                  </Box>
                </Popover>
              </Box>
            </ListItem>
          </List>
        </CardContent>
      </Card>

      {/* Модальное окно выбора цвета панелей */}
      <Dialog open={colorPickerOpen} onClose={() => setColorPickerOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          Выберите цвет панелей
          <IconButton onClick={() => setColorPickerOpen(false)} size="small" aria-label="Закрыть">
            <CloseIcon />
          </IconButton>
        </DialogTitle>
        <DialogContent>
          <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1.5, pt: 1 }}>
            {SIDEBAR_PALETTE.map((item) => (
              <Box
                key={item.value || 'default'}
                onClick={() => handlePaletteColorPick(item.value)}
                sx={{
                  width: 48,
                  height: 48,
                  borderRadius: 2,
                  background: item.value || getDefaultSidebarPanelBackground(isDarkMode),
                  cursor: 'pointer',
                  border: '2px solid',
                  borderColor: interfaceSettings.sidebarPanelColor === item.value ? 'primary.main' : 'transparent',
                  '&:hover': { opacity: 0.9, borderColor: 'primary.light' },
                }}
                title={item.name}
              />
            ))}
          </Box>
          <Divider sx={{ my: 2 }} />
          <Typography variant="body2" sx={{ mb: 1 }}>
            Свой цвет (HEX)
          </Typography>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
            <Box
              component="input"
              type="color"
              value={HEX_COLOR_RE.test(customSidebarHex) ? customSidebarHex : '#667EEA'}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) => setCustomSidebarHex(e.target.value.toUpperCase())}
              sx={{
                width: 52,
                height: 40,
                p: 0,
                border: '1px solid',
                borderColor: 'divider',
                borderRadius: 1,
                backgroundColor: 'transparent',
                cursor: 'pointer',
              }}
            />
            <Box
              component="input"
              type="text"
              value={customSidebarHex}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) => {
                setCustomSidebarHex(normalizeHexInput(e.target.value).toUpperCase());
              }}
              placeholder="#RRGGBB"
              sx={{
                height: 40,
                minWidth: 120,
                px: 1.5,
                borderRadius: 1,
                border: '1px solid',
                borderColor: 'divider',
                outline: 'none',
                color: 'text.primary',
                backgroundColor: 'background.paper',
                '&:focus': {
                  borderColor: 'primary.main',
                },
              }}
            />
            <Button variant="contained" size="small" onClick={handleApplyCustomSidebarColor}>
              Применить
            </Button>
          </Box>
        </DialogContent>
      </Dialog>

      {/* Модальное окно выбора цвета окна ввода */}
      <Dialog open={chatInputColorPickerOpen} onClose={() => setChatInputColorPickerOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          Выберите цвет окна ввода
          <IconButton onClick={() => setChatInputColorPickerOpen(false)} size="small" aria-label="Закрыть">
            <CloseIcon />
          </IconButton>
        </DialogTitle>
        <DialogContent sx={{ overflowX: 'hidden', pb: 3 }}>
          <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1.5, pt: 1 }}>
            {CHAT_INPUT_PALETTE.map((item) => (
              <Box
                key={item.value || 'default'}
                onClick={() => { handleChatInputColorSelect(item.value); setChatInputColorPickerOpen(false); }}
                sx={{
                  width: 48,
                  height: 48,
                  borderRadius: 2,
                  background: item.value || (theme.palette.mode === 'dark' ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.08)'),
                  cursor: 'pointer',
                  border: '2px solid',
                  borderColor: interfaceSettings.chatInputColor === item.value ? 'primary.main' : 'transparent',
                  '&:hover': { opacity: 0.92, borderColor: 'primary.light' },
                }}
                title={item.name}
              />
            ))}
          </Box>
          <Divider sx={{ my: 2 }} />
          <Typography variant="body2" sx={{ mb: 1 }}>
            Контрастность окна ввода
          </Typography>
          <Slider
            size="small"
            min={20}
            max={100}
            step={5}
            value={interfaceSettings.chatInputContrast}
            onChange={handleChatInputContrastChange}
            valueLabelDisplay="auto"
            marks={[
              { value: 20, label: 'Мягко' },
              { value: 55, label: 'Баланс' },
              { value: 100, label: 'Ярко' },
            ]}
            sx={{
              px: 0.5,
              '& .MuiSlider-rail': {
                height: 7,
                borderRadius: 999,
                opacity: 1,
                background: theme.palette.mode === 'dark'
                  ? 'linear-gradient(90deg, rgba(255,255,255,0.18) 0%, rgba(255,255,255,0.45) 100%)'
                  : 'linear-gradient(90deg, rgba(0,0,0,0.12) 0%, rgba(0,0,0,0.35) 100%)',
              },
              '& .MuiSlider-track': {
                height: 7,
                border: 0,
                borderRadius: 999,
                background: 'linear-gradient(90deg, #6D7CFF 0%, #8B5CF6 55%, #22D3EE 100%)',
              },
              '& .MuiSlider-thumb': {
                width: 18,
                height: 18,
                boxShadow: theme.palette.mode === 'dark'
                  ? '0 0 0 6px rgba(109,124,255,0.15)'
                  : '0 0 0 6px rgba(109,124,255,0.20)',
                '&:hover, &.Mui-focusVisible, &.Mui-active': {
                  boxShadow: theme.palette.mode === 'dark'
                    ? '0 0 0 8px rgba(109,124,255,0.22)'
                    : '0 0 0 8px rgba(109,124,255,0.28)',
                },
              },
              '& .MuiSlider-markLabel': {
                mt: 0.5,
                color: theme.palette.text.secondary,
                fontSize: '0.72rem',
                whiteSpace: 'nowrap',
              },
              '& .MuiSlider-markLabel[data-index="0"]': {
                transform: 'translateX(0%)',
              },
              '& .MuiSlider-markLabel[data-index="2"]': {
                transform: 'translateX(-100%)',
              },
              '& .MuiSlider-valueLabel': {
                background: 'linear-gradient(135deg, #6D7CFF 0%, #8B5CF6 100%)',
                fontWeight: 600,
              },
            }}
          />
        </DialogContent>
      </Dialog>

      {/* Модальное окно пользовательского фона рабочей зоны */}
      <Dialog open={workZoneImageDialogOpen} onClose={() => setWorkZoneImageDialogOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          Своё изображение
          <IconButton onClick={() => setWorkZoneImageDialogOpen(false)} size="small" aria-label="Закрыть">
            <CloseIcon />
          </IconButton>
        </DialogTitle>
        <DialogContent>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mb: 1.5 }}>
            <Typography variant="body2">
              Загрузите изображение для фона рабочей зоны или удалите текущее.
            </Typography>
            <Tooltip
              title={`Лимиты: до ${MAX_WORK_ZONE_IMAGE_DIMENSION_PX}x${MAX_WORK_ZONE_IMAGE_DIMENSION_PX} px и до ${MAX_WORK_ZONE_IMAGE_SIZE_MB} МБ.`}
              arrow
            >
              <IconButton
                size="small"
                sx={{
                  p: 0,
                  opacity: 0.7,
                  '&:hover': {
                    opacity: 1,
                    '& .MuiSvgIcon-root': { color: 'primary.main' },
                  },
                }}
              >
                <HelpOutlineIcon fontSize="small" color="action" />
              </IconButton>
            </Tooltip>
          </Box>
          <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
            <Button variant="outlined" size="small" onClick={() => workZoneImageInputRef.current?.click()}>
              Загрузить фото
            </Button>
            <Button
              variant="text"
              size="small"
              color="inherit"
              disabled={!workZoneCustomImage}
              onClick={handleRemoveWorkZonePhoto}
            >
              Удалить фото
            </Button>
            <Button variant="contained" size="small" disabled={!workZoneCustomImage} onClick={handleApplyCustomWorkZoneBg}>
              Применить
            </Button>
          </Box>
          <Box
            component="input"
            ref={workZoneImageInputRef}
            type="file"
            accept="image/*"
            onChange={handleUploadWorkZonePhoto}
            sx={{ display: 'none' }}
          />
        </DialogContent>
      </Dialog>
    </Box>
  );
}

