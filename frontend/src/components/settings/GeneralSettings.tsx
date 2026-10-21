import React, { useState, useEffect } from 'react';
import {
  Box,
  Typography,
  Card,
  CardContent,
  FormControl,
  FormControlLabel,
  RadioGroup,
  Radio,
  TextField,
  Switch,
  Button,
  Alert,
  IconButton,
  Tooltip,
  List,
  ListItem,
  ListItemText,
  Divider,
} from '@mui/material';
import {
  Palette as PaletteIcon,
  Memory as MemoryIcon,
  HelpOutline as HelpOutlineIcon,
  Restore as RestoreIcon,
} from '@mui/icons-material';
import { useAppActions } from '../../contexts/AppContext';
import { getApiUrl } from '../../config/api';
import ClampedNumberField from '../ClampedNumberField';
import {
  MODEL_SETTINGS_LABEL_WRAPPER_SX,
  MODEL_SETTINGS_HELP_ICON_BUTTON_SX,
} from '../../constants/modelSettingsStyles';

interface GeneralSettingsProps {
  isDarkMode: boolean;
  onToggleTheme: () => void;
}

export default function GeneralSettings({ isDarkMode, onToggleTheme }: GeneralSettingsProps) {
  
  const [memorySettings, setMemorySettings] = useState({
    max_messages: 20,
    include_system_prompts: true,
    clear_on_restart: false,
    unlimited_memory: false,
  });
  
  const { showNotification } = useAppActions();

  useEffect(() => {
    loadMemorySettings();
  }, []);

  const loadMemorySettings = async () => {
    try {
      const response = await fetch(getApiUrl('/api/memory/settings'));
      if (response.ok) {
        const data = await response.json();
        setMemorySettings(prev => ({ ...prev, ...data }));
      }
    } catch (error) {
      console.warn('Не удалось загрузить настройки памяти:', error);
    }
  };

  const saveMemorySettings = async (newSettings: typeof memorySettings) => {
    try {
      const response = await fetch(getApiUrl('/api/memory/settings'), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(newSettings),
      });
      
      if (response.ok) {
        showNotification('success', 'Настройки контекста диалога сохранены');
        return true;
      } else {
        throw new Error('Ошибка сохранения настроек контекста диалога');
      }
    } catch (error) {
      console.error('Ошибка сохранения настроек контекста диалога:', error);
      showNotification('error', 'Ошибка сохранения настроек контекста диалога');
      return false;
    }
  };

  const handleMemorySettingChange = async (key: keyof typeof memorySettings, value: any) => {
    const newSettings = { ...memorySettings, [key]: value };
    setMemorySettings(newSettings);
    await saveMemorySettings(newSettings);
  };

  const resetMemorySettings = () => {
    const defaultSettings = {
      max_messages: 20,
      include_system_prompts: true,
      clear_on_restart: false,
      unlimited_memory: false,
    };
    setMemorySettings(defaultSettings);
    saveMemorySettings(defaultSettings);
    showNotification('info', 'Настройки контекста диалога сброшены к значениям по умолчанию');
  };


  return (
    <Box sx={{ p: 3 }}>
      {/* Настройки темы */}
      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Typography variant="h6" gutterBottom sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <PaletteIcon color="primary" />
            Тема приложения
          </Typography>
          
          <FormControl component="fieldset">
            <RadioGroup
              value={isDarkMode ? 'dark' : 'light'}
              onChange={(e) => {
                if (e.target.value === 'dark' && !isDarkMode) {
                  onToggleTheme();
                } else if (e.target.value === 'light' && isDarkMode) {
                  onToggleTheme();
                }
              }}
            >
              <FormControlLabel
                value="dark"
                control={<Radio />}
                label="Темная"
              />
              <FormControlLabel
                value="light"
                control={<Radio />}
                label="Светлая"
              />
            </RadioGroup>
          </FormControl>
        </CardContent>
      </Card>

      {/* Контекст диалога (история сообщений) — не путать с общей библиотекой документов */}
      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Typography variant="h6" gutterBottom sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <MemoryIcon color="primary" />
            Контекст диалога
            <Tooltip 
              title="Сколько последних сообщений чата учитывать в ответе. Это история переписки, а не общая библиотека документов и не RAG. Рекомендуется: 20–40 сообщений." 
              arrow
            >
              <IconButton 
                size="small" 
                sx={{ 
                  ml: 0.5,
                  opacity: 0.7,
                  '&:hover': {
                    opacity: 1,
                    '& .MuiSvgIcon-root': {
                      color: 'primary.main',
                    },
                  },
                }}
              >
                <HelpOutlineIcon fontSize="small" color="action" />
              </IconButton>
            </Tooltip>
          </Typography>

          <List>
            {/* Неограниченная память */}
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
                    Неограниченная история
                    <Tooltip 
                      title="Ассистент будет учитывать все сообщения в диалоге. На длинных чатах это сильнее нагружает контекст модели." 
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
                primaryTypographyProps={{
                  variant: 'body1',
                  fontWeight: 500,
                }}
              />
              <Switch
                checked={memorySettings.unlimited_memory}
                onChange={(e) => handleMemorySettingChange('unlimited_memory', e.target.checked)}
              />
            </ListItem>

            <Divider />

            {/* Максимум сообщений в контексте - показывается только если неограниченная память выключена */}
            {!memorySettings.unlimited_memory && (
              <>
                <ListItem sx={{ px: 0, py: 1.5, display: 'block' }}>
                  <Box
                    sx={{
                      display: 'flex',
                      flexDirection: { xs: 'column', sm: 'row' },
                      gap: 2,
                      alignItems: { sm: 'flex-start' },
                      flexWrap: 'wrap',
                    }}
                  >
                    <Box sx={{ maxWidth: { xs: '100%', sm: 300 }, minWidth: { sm: 260 }, flex: { sm: '0 0 auto' } }}>
                      <ClampedNumberField
                        fullWidth
                        size="small"
                        label={
                          <Box sx={MODEL_SETTINGS_LABEL_WRAPPER_SX} component="span">
                            Максимум сообщений в контексте
                            <Tooltip
                              title="Количество последних сообщений, которые ассистент запоминает в диалоге. Диапазон 5–100 (шаг в поле — 5). Больше сообщений — богаче контекст, выше нагрузка на память и время ответа."
                              arrow
                            >
                              <IconButton
                                size="small"
                                sx={MODEL_SETTINGS_HELP_ICON_BUTTON_SX}
                                onClick={(e) => e.stopPropagation()}
                                aria-label="Справка: максимум сообщений в контексте"
                              >
                                <HelpOutlineIcon fontSize="small" color="action" />
                              </IconButton>
                            </Tooltip>
                          </Box>
                        }
                        value={memorySettings.max_messages}
                        onValueChange={(v) => handleMemorySettingChange('max_messages', v)}
                        min={5}
                        max={100}
                        step={5}
                        defaultValue={memorySettings.max_messages}
                        integer
                        InputLabelProps={{ shrink: true }}
                      />
                    </Box>
                    <Box sx={{ maxWidth: { xs: '100%', sm: 236 }, minWidth: 0, flex: { sm: '0 0 auto' } }}>
                      <TextField
                        fullWidth
                        size="small"
                        type="number"
                        disabled
                        label={
                          <Box sx={MODEL_SETTINGS_LABEL_WRAPPER_SX} component="span">
                            Размер контекста (токены)
                            <Tooltip
                              title="Примерный размер контекста в токенах (только для чтения): значение считается как «максимум сообщений» × 150 — грубая оценка для ориентира, не лимит модели."
                              arrow
                            >
                              <IconButton
                                size="small"
                                sx={MODEL_SETTINGS_HELP_ICON_BUTTON_SX}
                                onClick={(e) => e.stopPropagation()}
                                aria-label="Справка: размер контекста в токенах"
                              >
                                <HelpOutlineIcon fontSize="small" color="action" />
                              </IconButton>
                            </Tooltip>
                          </Box>
                        }
                        value={Math.round(memorySettings.max_messages * 150)}
                        InputLabelProps={{ shrink: true }}
                      />
                    </Box>
                  </Box>
                </ListItem>

                <Divider />
              </>
            )}

            {/* Включать системные промпты в контекст */}
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
                primary="Включать системные промпты в контекст"
                primaryTypographyProps={{
                  variant: 'body1',
                  fontWeight: 500,
                }}
              />
              <Switch
                checked={memorySettings.include_system_prompts}
                onChange={(e) => handleMemorySettingChange('include_system_prompts', e.target.checked)}
              />
            </ListItem>

            <Divider />

            {/* Очищать историю при перезапуске */}
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
                primary="Очищать историю при перезапуске"
                primaryTypographyProps={{
                  variant: 'body1',
                  fontWeight: 500,
                }}
              />
              <Switch
                checked={memorySettings.clear_on_restart}
                onChange={(e) => handleMemorySettingChange('clear_on_restart', e.target.checked)}
              />
            </ListItem>
          </List>

          {!memorySettings.unlimited_memory && memorySettings.max_messages > 50 && (
            <Alert severity="warning" sx={{ mb: 2 }}>
              <Typography variant="body2">
                <strong>Внимание:</strong> Установлено большое количество сообщений ({memorySettings.max_messages}). 
                Это может замедлить работу ассистента и увеличить потребление памяти.
              </Typography>
            </Alert>
          )}

          <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap', mt: 3 }}>
            <Button
              variant="outlined"
              startIcon={<RestoreIcon />}
              onClick={resetMemorySettings}
            >
              Восстановить настройки
            </Button>
          </Box>
        </CardContent>
      </Card>

    </Box>
  );
}

