import React, { useState, useEffect, useRef } from 'react';
import {
  Box,
  Typography,
  Card,
  CardContent,
  TextField,
  Switch,
  Button,
  Alert,
  List,
  ListItem,
  ListItemText,
  Chip,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  LinearProgress,
  Divider,
  IconButton,
  Tooltip,
} from '@mui/material';
import {
  Settings as SettingsIcon,
  Upload as UploadIcon,
  Computer as ComputerIcon,
  Memory as MemoryIcon,
  HelpOutline as HelpOutlineIcon,
  Restore as RestoreIcon,
} from '@mui/icons-material';
import { useAppActions } from '../../contexts/AppContext';
import ModelSelector from '../ModelSelector';
import ModelSettingsFields from '../ModelSettingsFields';
import { useTheme } from '@mui/material';
import { getApiUrl, getAuthFetchHeaders } from '../../config/api';
import { MODEL_SETTINGS_SECTION_TITLE_SX, MODEL_SETTINGS_CARD_SX, MODEL_SETTINGS_RESET_BUTTON_SX, MODEL_SETTINGS_DEFAULT, MODEL_SETTINGS_MAX_DEFAULT } from '../../constants/modelSettingsStyles';
import { MODEL_SETTINGS_CHANGED_EVENT } from '../../utils/contextTokens';
import ImageGenerationSettingsSection from './ImageGenerationSettingsSection';


export default function ModelsSettings() {
  const readIsSettingsMode = () => {
    const mode = localStorage.getItem('model_selector_mode');
    if (mode) return mode === 'settings';
    const oldBool = localStorage.getItem('show_model_selector_in_settings');
    return oldBool === 'true';
  };
  const [showModelSelectorInSettings, setShowModelSelectorInSettings] = useState(readIsSettingsMode);
  
  // Стартовое состояние; заводские значения приходят из llm_settings.json
  // (GET /api/models/settings/recommended)
  const [modelSettings, setModelSettings] = useState({ ...MODEL_SETTINGS_DEFAULT });

  const settingsLoadedRef = useRef(false);
  const skipNextAutosaveRef = useRef(true);
  // Последнее подтверждённое сервером состояние — для отката при ошибке сохранения (BUG-FE-36).
  const confirmedModelSettingsRef = useRef<typeof modelSettings>({ ...MODEL_SETTINGS_DEFAULT });

  const [maxValues, setMaxValues] = useState({
    ...MODEL_SETTINGS_MAX_DEFAULT,
    batch_size: 2048,
    n_threads: 24,
  });

  // Заводские значения с сервера — по ним работает кнопка сброса
  const [recommendedSettings, setRecommendedSettings] = useState({ ...MODEL_SETTINGS_DEFAULT });

  const [contextPrompts, setContextPrompts] = useState({
    globalPrompt: '',
    modelPrompts: {} as Record<string, string>,
    customPrompts: {} as Record<string, { prompt: string; description: string; created_at: string }>,
  });

  const [modelsWithPrompts, setModelsWithPrompts] = useState<Array<{
    name: string;
    display_name?: string;
    path: string;
    size: number;
    size_mb: number;
    context_prompt: string;
    has_custom_prompt: boolean;
    provider_id?: string;
  }>>([]);

  const [availableModels, setAvailableModels] = useState<Array<{
    name: string;
    path: string;
    size: number;
    size_mb: number;
  }>>([]);

  const [currentModel, setCurrentModel] = useState<any>(null);
  const [selectedModelPath, setSelectedModelPath] = useState<string>("");
  const [isLoadingModel, setIsLoadingModel] = useState(false);
  const [showModelDialog, setShowModelDialog] = useState(false);
  const [promptDialogOpen, setPromptDialogOpen] = useState(false);
  const [promptDialogType, setPromptDialogType] = useState<'global' | 'model'>('global');
  const [promptDialogData, setPromptDialogData] = useState({ prompt: '', description: '', id: '' });
  const [selectedModelForPrompt, setSelectedModelForPrompt] = useState<string>('');
  const [showModelPromptDialog, setShowModelPromptDialog] = useState(false);

  const { showNotification, setModelSettings: syncGlobalModelSettings } = useAppActions();

  useEffect(() => {
    loadSettings();
    loadModels();
    loadCurrentModel();
    loadContextPrompts();
    
    // Слушаем изменения настроек
    const handleSettingsChange = () => {
      setShowModelSelectorInSettings(readIsSettingsMode());
    };
    
    window.addEventListener('interfaceSettingsChanged', handleSettingsChange);
    return () => window.removeEventListener('interfaceSettingsChanged', handleSettingsChange);
  }, []);

  // Автосохранение настроек модели (только после загрузки с сервера)
  useEffect(() => {
    if (!settingsLoadedRef.current || skipNextAutosaveRef.current) {
      skipNextAutosaveRef.current = false;
      return;
    }
    const timeoutId = setTimeout(() => {
      saveModelSettings();
    }, 1000);

    return () => clearTimeout(timeoutId);
  }, [modelSettings]);

  const loadSettings = async () => {
    try {
      // Загружаем настройки модели
      const modelResponse = await fetch(getApiUrl('/api/models/settings'), {
        headers: getAuthFetchHeaders(),
      });
      if (modelResponse.ok) {
        const modelData = await modelResponse.json();
        skipNextAutosaveRef.current = true;
        confirmedModelSettingsRef.current = { ...confirmedModelSettingsRef.current, ...modelData };
        setModelSettings(prev => ({ ...prev, ...modelData }));
        syncGlobalModelSettings({ ...MODEL_SETTINGS_DEFAULT, ...modelData, streaming_speed: modelData.streaming_speed ?? 20 });
        settingsLoadedRef.current = true;
      } else {
        // BUG-FE-34: загрузка не удалась — всё равно разрешаем автосохранение правок пользователя и сообщаем об ошибке.
        settingsLoadedRef.current = true;
        showNotification(
          'error',
          `Не удалось загрузить настройки модели (${modelResponse.status}). Показаны значения по умолчанию.`,
        );
      }
      
      // Заводские + максимальные (API: recommended, max_values)
      const maxResponse = await fetch(getApiUrl('/api/models/settings/recommended'), {
        headers: getAuthFetchHeaders(),
      });
      if (maxResponse.ok) {
        const maxData = await maxResponse.json();
        if (maxData.max_values) {
          setMaxValues(prev => ({ ...prev, ...maxData.max_values }));
        }
        const recommended = maxData.recommended ?? maxData.recommended_settings;
        if (recommended) {
          setRecommendedSettings(prev => ({ ...prev, ...recommended }));
        }
      }
    } catch (error) {
      console.error('Ошибка загрузки настроек:', error);
      if (!settingsLoadedRef.current) {
        // BUG-FE-34: правки пользователя не должны молча теряться.
        settingsLoadedRef.current = true;
        showNotification('error', 'Не удалось загрузить настройки модели. Показаны значения по умолчанию.');
      }
    }
  };

  const saveModelSettings = async () => {
    try {
      const response = await fetch(getApiUrl('/api/models/settings'), {
        method: 'PUT',
        headers: getAuthFetchHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(modelSettings),
      });
      
      if (response.ok) {
        confirmedModelSettingsRef.current = modelSettings;
        showNotification('success', 'Настройки модели сохранены');
        syncGlobalModelSettings(modelSettings);
        window.dispatchEvent(
          new CustomEvent(MODEL_SETTINGS_CHANGED_EVENT, {
            detail: { context_size: modelSettings.context_size },
          }),
        );
      } else {
        throw new Error(`Ошибка сохранения настроек модели: ${response.status}`);
      }
    } catch (error) {
      console.error('Ошибка сохранения настроек модели:', error);
      showNotification('error', 'Ошибка сохранения настроек модели');
      // Откат формы к последнему подтверждённому состоянию (без повторного автосохранения).
      skipNextAutosaveRef.current = true;
      setModelSettings({ ...confirmedModelSettingsRef.current });
    }
  };

  const resetModelSettings = () => {
    // Заводские значения приходят с сервера (llm_settings.json); MODEL_SETTINGS_DEFAULT — запасной вариант.
    setModelSettings({ ...MODEL_SETTINGS_DEFAULT, ...recommendedSettings });
    showNotification('success', 'Настройки модели восстановлены до значений по умолчанию');
  };

  const loadModels = async () => {
    try {
      const response = await fetch(getApiUrl('/api/models'), {
        headers: getAuthFetchHeaders(),
      });
      if (response.ok) {
        const data = await response.json();
        setAvailableModels(data.models || []);
      }
    } catch (err) {
      console.error('Ошибка загрузки моделей:', err);
    }
  };

  const loadCurrentModel = async () => {
    try {
      const response = await fetch(getApiUrl('/api/models/current'), {
        headers: getAuthFetchHeaders(),
      });
      if (response.ok) {
        const data = await response.json();
        setCurrentModel(data);
        setSelectedModelPath(data.path || "");
      }
    } catch (err) {
      console.warn('Не удалось загрузить текущую модель:', err);
    }
  };

  const getModelLabel = (model: { display_name?: string; name: string; path: string }) =>
    model.display_name || model.name || model.path;

  const loadContextPrompts = async () => {
    try {
      const authHeaders = getAuthFetchHeaders();
      // Загружаем глобальную инструкцию
      const globalResponse = await fetch(getApiUrl('/api/context-prompts/global'), { headers: authHeaders });
      if (globalResponse.ok) {
        const globalData = await globalResponse.json();
        setContextPrompts(prev => ({ ...prev, globalPrompt: globalData.prompt }));
      }

      // Загружаем модели с инструкциями
      const modelsResponse = await fetch(getApiUrl('/api/context-prompts/models'), { headers: authHeaders });
      if (modelsResponse.ok) {
        const modelsData = await modelsResponse.json();
        setModelsWithPrompts(modelsData.models || []);
        
        // Обновляем инструкции моделей
        const modelPrompts: Record<string, string> = {};
        modelsData.models?.forEach((model: any) => {
          if (model.has_custom_prompt) {
            modelPrompts[model.path] = model.context_prompt;
          }
        });
        setContextPrompts(prev => ({ ...prev, modelPrompts }));
      }

      // Загружаем пользовательские инструкции
      const customResponse = await fetch(getApiUrl('/api/context-prompts/custom'), { headers: authHeaders });
      if (customResponse.ok) {
        const customData = await customResponse.json();
        setContextPrompts(prev => ({ ...prev, customPrompts: customData.prompts || {} }));
      }
    } catch (error) {
      console.error('Ошибка загрузки контекстных инструкций:', error);
      showNotification('error', 'Ошибка загрузки контекстных инструкций');
    }
  };

  const saveGlobalPrompt = async (prompt: string) => {
    try {
      const response = await fetch(getApiUrl('/api/context-prompts/global'), {
        method: 'PUT',
        headers: getAuthFetchHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ prompt }),
      });
      
      if (response.ok) {
        setContextPrompts(prev => ({ ...prev, globalPrompt: prompt }));
        showNotification('success', 'Глобальная инструкция сохранена');
        return true;
      }
      const detail = await response.text();
      throw new Error(detail || `HTTP ${response.status}`);
    } catch (error) {
      console.error('Ошибка сохранения глобальной инструкции:', error);
      showNotification('error', 'Ошибка сохранения глобальной инструкции');
      return false;
    }
  };

  const saveModelPrompt = async (modelPath: string, prompt: string) => {
    try {
      const response = await fetch(getApiUrl(`/api/context-prompts/model/${encodeURIComponent(modelPath)}`), {
        method: 'PUT',
        headers: getAuthFetchHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ prompt }),
      });
      
      if (response.ok) {
        setContextPrompts(prev => ({
          ...prev,
          modelPrompts: { ...prev.modelPrompts, [modelPath]: prompt }
        }));
        showNotification('success', 'Инструкция модели сохранена');
        return true;
      }
      const detail = await response.text();
      throw new Error(detail || `HTTP ${response.status}`);
    } catch (error) {
      console.error('Ошибка сохранения инструкции модели:', error);
      showNotification('error', 'Ошибка сохранения инструкции модели');
      return false;
    }
  };

  const loadModel = async (modelPath: string) => {
    try {
      setIsLoadingModel(true);
      
      const response = await fetch(getApiUrl('/api/models/load'), {
        method: 'POST',
        headers: getAuthFetchHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ model_path: modelPath }),
      });
      
      if (response.ok) {
        const data = await response.json();
        if (data.success) {
          showNotification('success', 'Модель успешно загружена!');
          setSelectedModelPath(modelPath);
          loadCurrentModel();
        } else {
          throw new Error(data.message || 'Не удалось загрузить модель');
        }
      } else {
        throw new Error('Ошибка загрузки модели');
      }
      
    } catch (err) {
      showNotification('error', `Ошибка загрузки модели: ${err}`);
    } finally {
      setIsLoadingModel(false);
    }
  };

  const formatFileSize = (bytes: number): string => {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
  };

  const openPromptDialog = (type: 'global' | 'model', data?: any) => {
    setPromptDialogType(type);
    if (type === 'global') {
      setPromptDialogData({ prompt: contextPrompts.globalPrompt, description: '', id: '' });
    } else if (type === 'model' && data) {
      setPromptDialogData({ 
        prompt: contextPrompts.modelPrompts[data.path] || contextPrompts.globalPrompt, 
        description: '', 
        id: data.path 
      });
      setSelectedModelForPrompt(data.path);
    } else {
      setPromptDialogData({ prompt: '', description: '', id: '' });
    }
    setPromptDialogOpen(true);
  };

  const handlePromptDialogSave = async () => {
    let success = false;
    if (promptDialogType === 'global') {
      success = await saveGlobalPrompt(promptDialogData.prompt);
    } else if (promptDialogType === 'model') {
      success = await saveModelPrompt(selectedModelForPrompt, promptDialogData.prompt);
    }

    if (success) {
      setPromptDialogOpen(false);
      await loadContextPrompts();
    }
  };

  const theme = useTheme();
  const isDarkMode = theme.palette.mode === 'dark';

  return (
    <Box sx={{ p: 3 }}>
      {/* Выбор модели - показываем только если включено в настройках */}
      {showModelSelectorInSettings && (
      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Typography variant="h6" gutterBottom sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <ComputerIcon color="primary" />
            Выбор модели
          </Typography>
          
          <Box sx={{ mb: 2 }}>
            <ModelSelector 
              isDarkMode={isDarkMode}
              onModelSelect={(modelPath) => {
                console.log('Модель выбрана:', modelPath);
                loadCurrentModel();
              }}
            />
          </Box>
          
          {/* Информация о текущей модели */}
          {currentModel?.loaded ? (
            <Box sx={{ mb: 2 }}>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
                <ComputerIcon color="primary" />
                <Chip label="Загружена" color="success" size="small" />
              </Box>
              <Typography variant="body1" fontWeight="500" gutterBottom>
                {currentModel.metadata?.['general.name'] || 'Неизвестная модель'}
              </Typography>
              <Typography variant="body2" color="text.secondary" gutterBottom>
                Архитектура: {currentModel.metadata?.['general.architecture'] || 'Неизвестно'}
              </Typography>
              <Typography variant="body2" color="text.secondary" gutterBottom>
                Контекст: {currentModel.n_ctx || 'Неизвестно'} токенов
              </Typography>
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                Путь: {currentModel.path}
              </Typography>
            </Box>
          ) : (
            <Alert severity="warning" sx={{ mb: 2 }}>
              Модель не загружена
            </Alert>
          )}
          
          <Button
            variant="outlined"
            startIcon={<UploadIcon />}
            onClick={() => setShowModelDialog(true)}
            disabled={isLoadingModel}
            fullWidth
          >
            {isLoadingModel ? 'Загрузка модели...' : 'Сменить модель'}
          </Button>
        </CardContent>
      </Card>
      )}

      {/* Настройки модели astrachat */}
      <Card sx={MODEL_SETTINGS_CARD_SX}>
        <CardContent>
          <Typography variant="h6" gutterBottom sx={MODEL_SETTINGS_SECTION_TITLE_SX}>
            <SettingsIcon color="primary" />
            Настройки выбранной модели
            <Tooltip title="Настройки автоматически сохраняются при изменении" arrow>
              <IconButton size="small" sx={{ ml: 0.5, opacity: 0.7, '&:hover': { opacity: 1, '& .MuiSvgIcon-root': { color: 'primary.main' } } }}>
                <HelpOutlineIcon fontSize="small" color="action" />
              </IconButton>
            </Tooltip>
          </Typography>

          <ModelSettingsFields
            value={modelSettings}
            onChange={setModelSettings}
            maxValues={maxValues}
            defaults={recommendedSettings}
            accordion
            darkPanel={isDarkMode}
          />

          <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap', ...MODEL_SETTINGS_RESET_BUTTON_SX }}>
            <Button variant="outlined" startIcon={<RestoreIcon />} onClick={resetModelSettings}>
              Восстановить настройки
            </Button>
          </Box>
        </CardContent>
      </Card>

      {/* Контекстные инструкции */}
      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Typography variant="h6" gutterBottom sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <MemoryIcon color="primary" />
            Контекстные инструкции
          </Typography>

          {/* Глобальная инструкция */}
          <Box sx={{ mb: 3 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 2 }}>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                <Typography variant="subtitle1" fontWeight="600">
                  Глобальная инструкция
                </Typography>
                <Tooltip title="Инструкция применяется ко всем моделям" arrow>
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
              </Box>
              <Button
                variant="outlined"
                size="small"
                onClick={() => openPromptDialog('global')}
              >
                Редактировать
              </Button>
            </Box>
            <Box sx={{ 
              p: 2, 
              bgcolor: 'background.default', 
              borderRadius: 1, 
              maxHeight: 150,
              overflow: 'auto'
            }}>
              <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap', color: 'text.secondary' }}>
                {contextPrompts.globalPrompt || 'Глобальная инструкция не установлена'}
              </Typography>
            </Box>
          </Box>

          {/* Инструкции для моделей */}
          <Box sx={{ mb: 2 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 2 }}>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                <Typography variant="subtitle1" fontWeight="600">
                  Инструкции для моделей
                </Typography>
                <Tooltip title="Инструкция применяется к конкретной модели" arrow>
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
              </Box>
              <Button
                variant="outlined"
                size="small"
                onClick={() => setShowModelPromptDialog(true)}
              >
                Добавить инструкцию
              </Button>
            </Box>
            {modelsWithPrompts.filter(model => model.has_custom_prompt).length > 0 ? (
              <List>
                {modelsWithPrompts.filter(model => model.has_custom_prompt).map((model) => (
                  <ListItem key={model.path} sx={{ 
                    border: '1px solid', 
                    borderColor: 'grey.400', 
                    borderRadius: 1, 
                    mb: 1,
                    bgcolor: 'background.default'
                  }}>
                    <ListItemText
                      primary={
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                          <Typography variant="subtitle2" color="text.primary">
                            {getModelLabel(model)}
                          </Typography>
                          {model.provider_id && (
                            <Chip label={model.provider_id} size="small" variant="outlined" />
                          )}
                          <Chip label="Кастомный" size="small" color="primary" />
                        </Box>
                      }
                      secondary={
                        <Box>
                          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                            {model.path}
                          </Typography>
                          <Box sx={{ 
                            p: 1, 
                            bgcolor: 'background.default', 
                            borderRadius: 1,
                            maxHeight: 60,
                            overflow: 'hidden'
                          }}>
                            <Typography variant="body2" sx={{ 
                              whiteSpace: 'pre-wrap',
                              color: 'text.secondary'
                            }}>
                              {model.context_prompt}
                            </Typography>
                          </Box>
                        </Box>
                      }
                    />
                    <Box sx={{ display: 'flex', gap: 1 }}>
                      <Button
                        variant="outlined"
                        size="small"
                        onClick={() => openPromptDialog('model', model)}
                      >
                        Изменить
                      </Button>
                    </Box>
                  </ListItem>
                ))}
              </List>
            ) : (
              <Typography variant="body2" color="text.secondary">
                Индивидуальные инструкции для моделей не созданы
              </Typography>
            )}
          </Box>
        </CardContent>
      </Card>

      {/* Диалог редактирования инструкций */}
      <Dialog
        open={promptDialogOpen}
        onClose={() => setPromptDialogOpen(false)}
        maxWidth="md"
        fullWidth
      >
        <DialogTitle>
          {promptDialogType === 'global' && 'Редактирование глобальной инструкции'}
          {promptDialogType === 'model' && 'Редактирование инструкции модели'}
        </DialogTitle>
        <DialogContent>
          <TextField
            label="Инструкция"
            value={promptDialogData.prompt}
            onChange={(e) => setPromptDialogData(prev => ({ ...prev, prompt: e.target.value }))}
            fullWidth
            multiline
            rows={8}
            placeholder="Введите системную инструкцию для модели..."
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setPromptDialogOpen(false)}>
            Отмена
          </Button>
          <Button onClick={handlePromptDialogSave} variant="contained">
            Сохранить
          </Button>
        </DialogActions>
      </Dialog>

      {/* Диалог выбора модели для инструкции */}
      <Dialog
        open={showModelPromptDialog}
        onClose={() => setShowModelPromptDialog(false)}
        maxWidth="md"
        fullWidth
      >
        <DialogTitle>Выберите модель для создания инструкции</DialogTitle>
        <DialogContent>
          {modelsWithPrompts.length > 0 ? (
            <List>
              {modelsWithPrompts.map((model) => (
                <ListItem 
                  key={model.path} 
                  component="div"
                  onClick={() => {
                    setSelectedModelForPrompt(model.path);
                    openPromptDialog('model', model);
                    setShowModelPromptDialog(false);
                  }}
                  sx={{ 
                    border: '1px solid', 
                    borderColor: 'grey.300', 
                    borderRadius: 1, 
                    mb: 1,
                    bgcolor: model.has_custom_prompt ? 'primary.50' : 'background.default',
                    cursor: 'pointer',
                    '&:hover': {
                      bgcolor: model.has_custom_prompt ? 'primary.100' : 'action.hover'
                    }
                  }}
                >
                  <ListItemText
                    primary={
                      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                        <Typography variant="subtitle2">
                          {getModelLabel(model)}
                        </Typography>
                        {model.provider_id && (
                          <Chip label={model.provider_id} size="small" variant="outlined" />
                        )}
                        {model.has_custom_prompt && (
                          <Chip label="Есть инструкция" size="small" color="primary" />
                        )}
                      </Box>
                    }
                    secondary={
                      <Box>
                        <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                          {model.path}
                        </Typography>
                        <Typography variant="body2" color="text.secondary">
                          Размер: {model.size_mb} MB
                        </Typography>
                      </Box>
                    }
                  />
                </ListItem>
              ))}
            </List>
          ) : (
            <Typography variant="body2" color="text.secondary">
              Модели не найдены
            </Typography>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setShowModelPromptDialog(false)}>
            Отмена
          </Button>
        </DialogActions>
      </Dialog>

      {/* Диалог выбора модели */}
      <Dialog
        open={showModelDialog}
        onClose={() => setShowModelDialog(false)}
        maxWidth="md"
        fullWidth
        TransitionComponent={undefined}
        transitionDuration={0}
      >
        <DialogTitle>Выбор модели</DialogTitle>
        <DialogContent>
          {availableModels.length === 0 ? (
            <Alert severity="info">
              Модели не найдены. Поместите GGUF файлы в директорию models/
            </Alert>
          ) : (
            <List>
              {availableModels.map((model, index) => (
                <ListItem
                  key={index}
                  component="div"
                  sx={{ 
                    cursor: 'pointer',
                    borderRadius: 1,
                    '&:hover': { backgroundColor: 'action.hover' },
                    backgroundColor: selectedModelPath === model.path ? 'action.selected' : 'transparent',
                    border: selectedModelPath === model.path ? '2px solid #1976d2' : '1px solid transparent',
                    mb: 1,
                  }}
                  onClick={() => setSelectedModelPath(model.path)}
                >
                  <ListItemText
                    primary={getModelLabel(model)}
                    secondary={
                      <Box>
                        <Typography variant="caption" display="block">
                          Размер: {formatFileSize(model.size)}
                        </Typography>
                        <Typography variant="caption" color="text.secondary">
                          {model.path}
                        </Typography>
                      </Box>
                    }
                  />
                </ListItem>
              ))}
            </List>
          )}
          
          {isLoadingModel && <LinearProgress sx={{ mt: 2 }} />}
        </DialogContent>
        <DialogActions>
          <Button 
            onClick={() => setShowModelDialog(false)} 
            disabled={isLoadingModel}
          >
            Отмена
          </Button>
          <Button
            onClick={() => {
              if (selectedModelPath) {
                loadModel(selectedModelPath);
                setShowModelDialog(false);
              }
            }}
            disabled={!selectedModelPath || isLoadingModel}
            variant="contained"
          >
            {isLoadingModel ? 'Загрузка...' : 'Загрузить модель'}
          </Button>
        </DialogActions>
      </Dialog>

      <ImageGenerationSettingsSection />
    </Box>
  );
}







