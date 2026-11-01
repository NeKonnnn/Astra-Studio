import React, { useState, useEffect, useMemo, useRef } from 'react';
import { useTheme } from '@mui/material/styles';
import {
  Box,
  Typography,
  Card,
  CardContent,
  FormControlLabel,
  List,
  ListItem,
  ListItemText,
  Switch,
  Button,
  IconButton,
  Tooltip,
  Alert,
  Divider,
  Popover,
} from '@mui/material';
import {
  Mic as MicIcon,
  Refresh as RefreshIcon,
  HelpOutline as HelpOutlineIcon,
  Restore as RestoreIcon,
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
import { getApiUrl } from '../../config/api';

type Engine = 'whisperx';
type Language = 'ru' | 'en' | 'auto';

export default function TranscriptionSettings({ isDarkMode: isDarkModeProp }: { isDarkMode?: boolean } = {}) {
  const theme = useTheme();
  const isDarkMode = isDarkModeProp ?? theme.palette.mode === 'dark';
  const dropdownItemSx = useMemo(() => getDropdownItemSx(isDarkMode), [isDarkMode]);
  const dropdownTriggerSx = useMemo(() => getDropdownTriggerButtonSx(isDarkMode), [isDarkMode]);
  const dropdownTriggerTextSx = useMemo(() => getDropdownTriggerTextSx(isDarkMode), [isDarkMode]);
  const dropdownChevronSx = useMemo(() => getDropdownChevronSx(isDarkMode), [isDarkMode]);
  const [transcriptionSettings, setTranscriptionSettings] = useState({
    engine: 'whisperx' as Engine,
    language: 'ru' as Language,
    auto_detect: true,
  });
  const [isLoading, setIsLoading] = useState(false);
  const [enginePopoverAnchor, setEnginePopoverAnchor] = useState<HTMLElement | null>(null);
  const [languagePopoverAnchor, setLanguagePopoverAnchor] = useState<HTMLElement | null>(null);

  const { showNotification } = useAppActions();

  // BUG-FE-33: автосохранение разрешено только после завершения первой загрузки и только
  // для правок пользователя; значения по умолчанию и загруженные данные на сервер не уходят.
  const settingsLoadedRef = useRef(false);
  const dirtyRef = useRef(false);

  useEffect(() => {
    loadTranscriptionSettings();
  }, []);

  // Автосохранение настроек транскрибации
  useEffect(() => {
    if (!settingsLoadedRef.current || !dirtyRef.current) return undefined;
    const timeoutId = setTimeout(() => {
      if (!settingsLoadedRef.current || !dirtyRef.current) return;
      dirtyRef.current = false;
      saveTranscriptionSettings();
    }, 1000);

    return () => clearTimeout(timeoutId);
  }, [transcriptionSettings]);

  const loadTranscriptionSettings = async () => {
    try {
      setIsLoading(true);
      const response = await fetch(getApiUrl('/api/transcription/settings'));
      if (response.ok) {
        const data = await response.json();
        // Данные сервера приоритетнее неотправленных правок: загрузка не должна вызывать PUT.
        dirtyRef.current = false;
        setTranscriptionSettings(prev => ({ ...prev, ...data, engine: 'whisperx' as Engine }));
      }
    } catch (error) {
      console.error('Ошибка загрузки настроек транскрибации:', error);
    } finally {
      settingsLoadedRef.current = true;
      setIsLoading(false);
    }
  };

  const saveTranscriptionSettings = async () => {
    try {
      const response = await fetch(getApiUrl('/api/transcription/settings'), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(transcriptionSettings),
      });

      if (response.ok) {
        showNotification('success', 'Настройки транскрибации сохранены');
      } else {
        throw new Error(`Ошибка сохранения настроек транскрибации: ${response.status}`);
      }
    } catch (error) {
      console.error('Ошибка сохранения настроек транскрибации:', error);
      showNotification('error', 'Ошибка сохранения настроек транскрибации');
    }
  };

  const handleSettingChange = (key: keyof typeof transcriptionSettings, value: any) => {
    dirtyRef.current = true;
    setTranscriptionSettings(prev => ({ ...prev, [key]: value }));
  };

  const resetToDefaults = () => {
    dirtyRef.current = true;
    setTranscriptionSettings({
      engine: 'whisperx',
      language: 'ru',
      auto_detect: true,
    });
    showNotification('info', 'Настройки транскрибации сброшены к значениям по умолчанию');
  };

  const getEngineLabel = (_engine: Engine): string => 'WhisperX';

  const getEngineDescription = (_engine: Engine): string =>
    'Высокая точность распознавания, поддержка множества языков, хорошо работает с шумом.';

  const getEngineUseCase = (_engine: Engine): string =>
    'Используйте для транскрипции записей и голосового ввода.';

  return (
    <Box sx={{ p: 3 }}>
      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Typography variant="h6" gutterBottom sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <MicIcon color="primary" />
            Основные настройки
            <Tooltip
              title="Настройки распознавания речи для голосового ввода. Сохраняются автоматически при изменении."
              arrow
            >
              <IconButton
                size="small"
                sx={{
                  ml: 0.5,
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
          </Typography>

          <List sx={{ p: 0 }}>
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
                    Движок транскрибации
                    <Tooltip
                      title="Движок распознавания речи: WhisperX."
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
                            '& .MuiSvgIcon-root': { color: 'primary.main' },
                          },
                        }}
                        onClick={(e) => e.stopPropagation()}
                      >
                        <HelpOutlineIcon fontSize="small" color="action" />
                      </IconButton>
                    </Tooltip>
                  </Box>
                }
                primaryTypographyProps={{ variant: 'body1', fontWeight: 500 }}
              />
              <Box sx={{ minWidth: 280 }}>
                <Box
                  onClick={(e) => !isLoading && setEnginePopoverAnchor(e.currentTarget)}
                  sx={{
                    ...dropdownTriggerSx,
                    opacity: isLoading ? 0.7 : 1,
                    pointerEvents: isLoading ? 'none' : 'auto',
                  }}
                >
                  <Typography sx={dropdownTriggerTextSx}>
                    WhisperX
                  </Typography>
                  <ExpandMoreIcon sx={{ ...dropdownChevronSx, transform: enginePopoverAnchor ? 'rotate(180deg)' : 'none' }} />
                </Box>
                <Popover
                  open={Boolean(enginePopoverAnchor)}
                  anchorEl={enginePopoverAnchor}
                  onClose={() => setEnginePopoverAnchor(null)}
                  anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
                  transformOrigin={{ vertical: 'top', horizontal: 'left' }}
                  slotProps={{ paper: { sx: getDropdownPopoverPaperSx(enginePopoverAnchor, isDarkMode) } }}
                >
                  <Box sx={{ py: 0.5 }}>
                    {(['whisperx'] as const).map((engine) => (
                      <Box
                        key={engine}
                        onClick={() => { handleSettingChange('engine', engine); setEnginePopoverAnchor(null); }}
                        sx={{
                          ...dropdownItemSx,
                          ...getDropdownItemStateSx(isDarkMode, transcriptionSettings.engine === engine),
                        }}
                      >
                        WhisperX
                      </Box>
                    ))}
                  </Box>
                </Popover>
              </Box>
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
                primary={
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                    Язык транскрибации
                    <Tooltip
                      title="Язык распознавания. Автоопределение доступно при включённой опции ниже."
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
                            '& .MuiSvgIcon-root': { color: 'primary.main' },
                          },
                        }}
                        onClick={(e) => e.stopPropagation()}
                      >
                        <HelpOutlineIcon fontSize="small" color="action" />
                      </IconButton>
                    </Tooltip>
                  </Box>
                }
                primaryTypographyProps={{ variant: 'body1', fontWeight: 500 }}
              />
              <Box sx={{ minWidth: 280 }}>
                <Box
                  onClick={(e) => !isLoading && setLanguagePopoverAnchor(e.currentTarget)}
                  sx={{
                    ...dropdownTriggerSx,
                    opacity: isLoading ? 0.7 : 1,
                    pointerEvents: isLoading ? 'none' : 'auto',
                  }}
                >
                  <Typography sx={dropdownTriggerTextSx}>
                    {transcriptionSettings.language === 'ru' ? 'Русский' : transcriptionSettings.language === 'en' ? 'English' : 'Автоопределение'}
                  </Typography>
                  <ExpandMoreIcon sx={{ ...dropdownChevronSx, transform: languagePopoverAnchor ? 'rotate(180deg)' : 'none' }} />
                </Box>
                <Popover
                  open={Boolean(languagePopoverAnchor)}
                  anchorEl={languagePopoverAnchor}
                  onClose={() => setLanguagePopoverAnchor(null)}
                  anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
                  transformOrigin={{ vertical: 'top', horizontal: 'left' }}
                  slotProps={{ paper: { sx: getDropdownPopoverPaperSx(languagePopoverAnchor, isDarkMode) } }}
                >
                  <Box sx={{ py: 0.5 }}>
                    {(['ru', 'en', 'auto'] as const).map((lang) => (
                      <Box
                        key={lang}
                        onClick={() => { handleSettingChange('language', lang); setLanguagePopoverAnchor(null); }}
                        sx={{
                          ...dropdownItemSx,
                          ...getDropdownItemStateSx(isDarkMode, transcriptionSettings.language === lang),
                        }}
                      >
                        {lang === 'ru' ? 'Русский' : lang === 'en' ? 'English' : 'Автоопределение'}
                      </Box>
                    ))}
                  </Box>
                </Popover>
              </Box>
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
                primary={
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                    Автоматическое определение языка
                    <Tooltip
                      title="Если включено, язык может определяться автоматически по аудио."
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
                            '& .MuiSvgIcon-root': { color: 'primary.main' },
                          },
                        }}
                        onClick={(e) => e.stopPropagation()}
                      >
                        <HelpOutlineIcon fontSize="small" color="action" />
                      </IconButton>
                    </Tooltip>
                  </Box>
                }
                primaryTypographyProps={{ variant: 'body1', fontWeight: 500 }}
              />
              <Switch
                checked={transcriptionSettings.auto_detect}
                onChange={(e) => handleSettingChange('auto_detect', e.target.checked)}
                disabled={isLoading}
              />
            </ListItem>
          </List>

          {/* Информационный блок о выбранном движке — как в RAG */}
          <Alert
            severity="info"
            sx={{
              mt: 2,
              '& .MuiAlert-message': { width: '100%' },
            }}
          >
            <Box>
              <Typography variant="subtitle2" fontWeight="600" gutterBottom>
                {getEngineLabel(transcriptionSettings.engine)}
              </Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                {getEngineDescription(transcriptionSettings.engine)}
              </Typography>
              <Typography variant="body2" fontWeight="500" sx={{ mt: 1 }}>
                {getEngineUseCase(transcriptionSettings.engine)}
              </Typography>
            </Box>
          </Alert>

          <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap', mt: 3 }}>
            <Button
              variant="outlined"
              startIcon={<RefreshIcon />}
              onClick={loadTranscriptionSettings}
              disabled={isLoading}
            >
              Обновить настройки
            </Button>
            <Button
              variant="outlined"
              startIcon={<RestoreIcon />}
              onClick={resetToDefaults}
            >
              Восстановить настройки
            </Button>
          </Box>
        </CardContent>
      </Card>
    </Box>
  );
}







