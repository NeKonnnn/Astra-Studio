import React, { useState, useEffect } from 'react';
import {
  Box,
  Paper,
  Typography,
  Container,
  Card,
  CardContent,
  TextField,
  Button,
  Switch,
  FormControl,
  FormControlLabel,
  InputLabel,
  Select,
  MenuItem,
  Alert,
  List,
  ListItem,
  ListItemText,
  LinearProgress,
  IconButton,
  Collapse,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Chip,
  Tooltip,
} from '@mui/material';
import {
  Save as SaveIcon,
  Refresh as RefreshIcon,
  ExpandMore as ExpandMoreIcon,
  ExpandLess as ExpandLessIcon,
  Upload as UploadIcon,
  Computer as ComputerIcon,
  Memory as MemoryIcon,
  HelpOutline as HelpOutlineIcon,
} from '@mui/icons-material';
import { useAppActions } from '../contexts/AppContext';

// Backend URL
import { getApiUrl, getAuthFetchHeaders } from '../config/api';
import {
  MODEL_SETTINGS_LABEL_WRAPPER_SX,
  MODEL_SETTINGS_HELP_ICON_BUTTON_SX,
} from '../constants/modelSettingsStyles';

export default function SettingsPage() {
  const [isExpanded, setIsExpanded] = useState(true);
  const [isVisible, setIsVisible] = useState(true);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  
  const [modelSettings, setModelSettings] = useState({
    context_size: 2048,
    output_tokens: 512,
    temperature: 0.7,
    top_p: 0.95,
    repeat_penalty: 1.05,
    use_gpu: false,
    streaming: true,
    streaming_speed: 20, // Скорость потоковой генерации в миллисекундах
  });

  const [maxValues, setMaxValues] = useState({
    context_size: 1000000,
    output_tokens: 1000000,
    batch_size: 2048,
    n_threads: 24,
    temperature: 2.0,
    top_p: 1.0,
    repeat_penalty: 2.0
  });


  const [transcriptionSettings, setTranscriptionSettings] = useState({
    engine: "whisperx" as "whisperx",
    language: "ru",
    auto_detect: true,
  });

  const [memorySettings, setMemorySettings] = useState({
    max_messages: 20,
    include_system_prompts: true,
    clear_on_restart: false,
  });

  // Состояния для контекстных промптов
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

  const [selectedModelForPrompt, setSelectedModelForPrompt] = useState<string>('');
  const [promptDialogOpen, setPromptDialogOpen] = useState(false);
  const [promptDialogType, setPromptDialogType] = useState<'global' | 'model'>('global');
  const [promptDialogData, setPromptDialogData] = useState({ prompt: '', description: '', id: '' });
  const [showModelPromptDialog, setShowModelPromptDialog] = useState(false);

  const [availableModels, setAvailableModels] = useState<Array<{
    name: string;
    path: string;
    size: number;
    size_mb: number;
  }>>([]);

  const [selectedModelPath, setSelectedModelPath] = useState<string>("");
  const [currentModel, setCurrentModel] = useState<any>(null);
  const [isLoadingModel, setIsLoadingModel] = useState(false);
  const [showModelDialog, setShowModelDialog] = useState(false);
  

  const { showNotification } = useAppActions();

  const getModelLabel = (model: { display_name?: string; name: string; path: string }) =>
    model.display_name || model.name || model.path;

  // Загрузка данных при монтировании компонента
  useEffect(() => {
    loadSettings();
    loadModels();
    loadCurrentModel();
    loadContextPrompts();
  }, []);

  // Функция для разворачивания/сворачивания секций
  const toggleExpanded = () => {
    setIsExpanded(!isExpanded);
  };

  // Функция для скрытия/показа настроек
  const toggleVisibility = () => {
    setIsVisible(!isVisible);
  };


  // Функции для работы с контекстными промптами
  const loadContextPrompts = async () => {
    try {
      const authHeaders = getAuthFetchHeaders();
      // Загружаем глобальный промпт
      const globalResponse = await fetch(getApiUrl('/api/context-prompts/global'), { headers: authHeaders });
      if (globalResponse.ok) {
        const globalData = await globalResponse.json();
        setContextPrompts(prev => ({ ...prev, globalPrompt: globalData.prompt }));
      }

      // Загружаем модели с промптами
      const modelsResponse = await fetch(getApiUrl('/api/context-prompts/models'), { headers: authHeaders });
      if (modelsResponse.ok) {
        const modelsData = await modelsResponse.json();
        setModelsWithPrompts(modelsData.models || []);
        
        // Обновляем промпты моделей
        const modelPrompts: Record<string, string> = {};
        modelsData.models?.forEach((model: any) => {
          if (model.has_custom_prompt) {
            modelPrompts[model.path] = model.context_prompt;
          }
        });
        setContextPrompts(prev => ({ ...prev, modelPrompts }));
      }

      // Загружаем пользовательские промпты
      const customResponse = await fetch(getApiUrl('/api/context-prompts/custom'), { headers: authHeaders });
      if (customResponse.ok) {
        const customData = await customResponse.json();
        setContextPrompts(prev => ({ ...prev, customPrompts: customData.prompts || {} }));
      }
    } catch (error) {
      console.error('Ошибка загрузки контекстных промптов:', error);
      showNotification('error', 'Ошибка загрузки контекстных промптов');
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
        showNotification('success', 'Глобальный промпт сохранен');
        return true;
      }
      const detail = await response.text();
      throw new Error(detail || `HTTP ${response.status}`);
    } catch (error) {
      console.error('Ошибка сохранения глобального промпта:', error);
      showNotification('error', 'Ошибка сохранения глобального промпта');
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
        showNotification('success', 'Промпт модели сохранен');
        return true;
      }
      const detail = await response.text();
      throw new Error(detail || `HTTP ${response.status}`);
    } catch (error) {
      console.error('Ошибка сохранения промпта модели:', error);
      showNotification('error', 'Ошибка сохранения промпта модели');
      return false;
    }
  };

  const deleteModelPrompt = async (modelPath: string) => {
    try {
      // Удаляем промпт модели, устанавливая пустую строку
      const response = await fetch(getApiUrl(`/api/context-prompts/model/${encodeURIComponent(modelPath)}`), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: '' })
      });
      
      if (response.ok) {
        setContextPrompts(prev => {
          const newModelPrompts = { ...prev.modelPrompts };
          delete newModelPrompts[modelPath];
          return { ...prev, modelPrompts: newModelPrompts };
        });
        showNotification('success', 'Промпт модели удален');
        await loadContextPrompts(); // Перезагружаем данные
        return true;
      } else {
        throw new Error('Ошибка удаления промпта модели');
      }
    } catch (error) {
      console.error('Ошибка удаления промпта модели:', error);
      showNotification('error', 'Ошибка удаления промпта модели');
      return false;
    }
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
      await loadContextPrompts(); // Перезагружаем данные
    }
  };

  const loadSettings = async () => {
    setIsLoading(true);
    try {
      // Загружаем настройки модели
      const modelResponse = await fetch(getApiUrl('/api/models/settings'), {
        headers: getAuthFetchHeaders(),
      });
      if (modelResponse.ok) {
        const modelData = await modelResponse.json();
        setModelSettings(prev => ({ ...prev, ...modelData }));
      }
      
      // Загружаем максимальные значения
      const maxResponse = await fetch(getApiUrl('/api/models/settings/recommended'));
      if (maxResponse.ok) {
        const maxData = await maxResponse.json();
        if (maxData.max_values) {
          setMaxValues(maxData.max_values);
        }
      }
      
      
             // Загружаем настройки транскрибации
       const transcriptionResponse = await fetch(getApiUrl('/api/transcription/settings'));
       if (transcriptionResponse.ok) {
         const transcriptionData = await transcriptionResponse.json();
         setTranscriptionSettings(prev => ({ ...prev, ...transcriptionData }));
       }
       
       // Загружаем настройки памяти
       try {
         const memoryResponse = await fetch(getApiUrl('/api/memory/settings'));
         if (memoryResponse.ok) {
           const memoryData = await memoryResponse.json();
           setMemorySettings(prev => ({ ...prev, ...memoryData }));
         }
       } catch (error) {
         console.warn('Не удалось загрузить настройки памяти:', error);
       }
      
      setSuccess('Настройки загружены');
    } catch (error) {
      console.error('Ошибка загрузки настроек:', error);
      setError('Не удалось загрузить настройки');
    } finally {
      setIsLoading(false);
    }
  };

  const loadModels = async () => {
    try {
      const response = await fetch(getApiUrl('/api/models'));
      if (response.ok) {
        const data = await response.json();
        setAvailableModels(data.models || []);
      } else {
        setError('Не удалось загрузить список моделей');
      }
    } catch (err) {
      setError(`Ошибка загрузки моделей: ${err}`);
    }
  };

  const loadCurrentModel = async () => {
    try {
      const response = await fetch(getApiUrl('/api/models/current'));
      if (response.ok) {
        const data = await response.json();
        setCurrentModel(data);
        setSelectedModelPath(data.path || "");
      }
    } catch (err) {
      console.warn('Не удалось загрузить текущую модель:', err);
    }
  };

  const saveSettings = async () => {
    setIsLoading(true);
    try {
      // Сохраняем настройки модели
      const modelResponse = await fetch(getApiUrl('/api/models/settings'), {
        method: 'PUT',
        headers: getAuthFetchHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(modelSettings),
      });
      
      if (!modelResponse.ok) {
        throw new Error(`Ошибка сохранения настроек модели: ${modelResponse.status}`);
      }
      
      
             // Сохраняем настройки транскрибации
       const transcriptionResponse = await fetch(getApiUrl('/api/transcription/settings'), {
         method: 'PUT',
         headers: { 'Content-Type': 'application/json' },
         body: JSON.stringify(transcriptionSettings),
       });
       
       if (!transcriptionResponse.ok) {
         throw new Error(`Ошибка сохранения настроек транскрибации: ${transcriptionResponse.status}`);
       }
       
       // Сохраняем настройки памяти
       try {
         const memoryResponse = await fetch(getApiUrl('/api/memory/settings'), {
           method: 'PUT',
           headers: { 'Content-Type': 'application/json' },
           body: JSON.stringify(memorySettings),
         });
         
         if (!memoryResponse.ok) {
           console.warn(`Предупреждение: не удалось сохранить настройки памяти: ${memoryResponse.status}`);
         }
       } catch (error) {
         console.warn('Предупреждение: не удалось сохранить настройки памяти:', error);
       }
      
      setSuccess('Настройки сохранены успешно');
      showNotification('success', 'Настройки сохранены');
    } catch (error) {
      console.error('Ошибка сохранения настроек:', error);
      setError(`Ошибка сохранения: ${error}`);
      showNotification('error', 'Ошибка сохранения настроек');
    } finally {
      setIsLoading(false);
    }
  };

  // Функции для работы с настройками модели
  const loadModelSettings = async () => {
    setIsLoading(true);
    try {
      // Загружаем настройки модели
      const settingsResponse = await fetch(getApiUrl('/api/models/settings'), {
        headers: getAuthFetchHeaders(),
      });
      if (settingsResponse.ok) {
        const settingsData = await settingsResponse.json();
        setModelSettings(prev => ({ ...prev, ...settingsData }));
      }
      
      // Загружаем максимальные значения
      const maxResponse = await fetch(getApiUrl('/api/models/settings/recommended'));
      if (maxResponse.ok) {
        const maxData = await maxResponse.json();
        if (maxData.max_values) {
          setMaxValues(maxData.max_values);
        }
      }
      
      setSuccess('Настройки модели загружены');
    } catch (error) {
      console.error('Ошибка загрузки настроек модели:', error);
      setError(`Ошибка загрузки настроек модели: ${error}`);
    } finally {
      setIsLoading(false);
    }
  };

  const saveModelSettings = async () => {
    setIsLoading(true);
    try {
      const response = await fetch(getApiUrl('/api/models/settings'), {
        method: 'PUT',
        headers: getAuthFetchHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(modelSettings),
      });
      
      if (response.ok) {
        setSuccess('Настройки модели сохранены');
        showNotification('success', 'Настройки модели сохранены');
      } else {
        throw new Error(`Ошибка сохранения настроек модели: ${response.status}`);
      }
    } catch (error) {
      console.error('Ошибка сохранения настроек модели:', error);
      setError(`Ошибка сохранения настроек модели: ${error}`);
      showNotification('error', 'Ошибка сохранения настроек модели');
    } finally {
      setIsLoading(false);
    }
  };

  const resetModelSettings = async () => {
    setIsLoading(true);
    try {
      const response = await fetch(getApiUrl('/api/models/settings/reset'), {
        method: 'POST',
        headers: getAuthFetchHeaders({ 'Content-Type': 'application/json' }),
      });
      
      if (response.ok) {
        const data = await response.json();
        if (data.success) {
          setModelSettings(prev => ({ ...prev, ...data.settings }));
          setSuccess('Настройки модели сброшены к рекомендуемым значениям');
          showNotification('success', 'Настройки сброшены к рекомендуемым');
        } else {
          throw new Error('Не удалось сбросить настройки');
        }
      } else {
        throw new Error(`Ошибка сброса настроек: ${response.status}`);
      }
    } catch (error) {
      console.error('Ошибка сброса настроек модели:', error);
      setError(`Ошибка сброса настроек модели: ${error}`);
      showNotification('error', 'Ошибка сброса настроек модели');
    } finally {
      setIsLoading(false);
    }
  };

  const loadModel = async (modelPath: string) => {
    try {
      setIsLoadingModel(true);
      setError(null);
      
      const response = await fetch(getApiUrl('/api/models/load'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model_path: modelPath }),
      });
      
      if (response.ok) {
        const data = await response.json();
        if (data.success) {
          setSuccess('Модель успешно загружена!');
          showNotification('success', 'Модель загружена');
          setSelectedModelPath(modelPath);
          loadCurrentModel(); // Обновляем информацию о текущей модели
        } else {
          throw new Error(data.message || 'Не удалось загрузить модель');
        }
      } else {
        throw new Error('Ошибка загрузки модели');
      }
      
    } catch (err) {
      setError(`Ошибка загрузки модели: ${err}`);
      showNotification('error', 'Ошибка загрузки модели');
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

  // Если страница скрыта, показываем минимизированную версию
  if (!isVisible) {
    return (
      <Container maxWidth="lg" sx={{ mt: 4 }}>
        <Card sx={{ p: 3, textAlign: 'center' }}>
          <Typography variant="h6" gutterBottom>
            Настройки скрыты
          </Typography>
          <Button
            variant="contained"
            color="primary"
            size="large"
            onClick={toggleVisibility}
            sx={{ minWidth: 200 }}
          >
            Показать настройки
          </Button>
        </Card>
      </Container>
    );
  }

  return (
    <Box sx={{ height: '100vh', display: 'flex', flexDirection: 'column' }}>
      {/* Заголовок */}
      <Paper elevation={2} sx={{ p: 2, borderRadius: 0 }}>
        <Container maxWidth="lg">
          <Box display="flex" alignItems="center" justifyContent="space-between">
            <Box>
              <Typography variant="h5" fontWeight="600">
                Настройки
              </Typography>
              <Typography variant="body2" color="text.secondary">
                Конфигурация моделей, голоса и других параметров
              </Typography>
            </Box>
            <Box sx={{ display: 'flex', gap: 1 }}>
              <IconButton 
                onClick={toggleExpanded}
                color="primary"
                size="large"
                title={isExpanded ? "Свернуть секции" : "Развернуть секции"}
              >
                {isExpanded ? <ExpandLessIcon /> : <ExpandMoreIcon />}
              </IconButton>
              <Button
                variant="outlined"
                color="secondary"
                onClick={toggleVisibility}
                size="small"
              >
                Скрыть
              </Button>
            </Box>
          </Box>
        </Container>
      </Paper>

      {/* Основной контент */}
      <Container maxWidth="lg" sx={{ flexGrow: 1, py: 3, overflow: 'auto' }}>

        {/* Уведомления */}
        {error && (
          <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
            {error}
          </Alert>
        )}
        
        {success && (
          <Alert severity="success" sx={{ mb: 2 }} onClose={() => setSuccess(null)}>
            {success}
          </Alert>
        )}

        <Collapse in={isExpanded}>
          {isLoading && <LinearProgress sx={{ mb: 2 }} />}

        {/* Кнопки управления */}
        <Box display="flex" gap={2} mb={3}>
          <Button
            variant="contained"
            color="primary"
            startIcon={<SaveIcon />}
            onClick={saveSettings}
            disabled={isLoading}
          >
            Сохранить настройки
          </Button>
          
          <Button
            variant="outlined"
            startIcon={<RefreshIcon />}
            onClick={loadSettings}
            disabled={isLoading}
          >
            Обновить данные
          </Button>
        </Box>

        {/* Настройки модели */}
        <Card sx={{ mb: 3 }}>
          <CardContent>
            <Typography variant="h6" gutterBottom>
                              Настройки модели Газик ИИ
            </Typography>
            
            <Box display="grid" gridTemplateColumns="repeat(auto-fit, minmax(250px, 1fr))" gap={2}>
              <TextField
                label="Размер контекста"
                type="number"
                value={modelSettings.context_size}
                onChange={(e) => setModelSettings(prev => ({ 
                  ...prev, 
                  context_size: parseInt(e.target.value) || 2048 
                }))}
                inputProps={{ min: 512, max: maxValues.context_size, step: 512 }}
                fullWidth
              />
              
              <TextField
                label="Лимит токенов ответа"
                type="number"
                value={modelSettings.output_tokens}
                onChange={(e) => setModelSettings(prev => ({ 
                  ...prev, 
                  output_tokens: parseInt(e.target.value) || 512 
                }))}
                inputProps={{ min: 64, max: maxValues.output_tokens, step: 64 }}
                fullWidth
              />
              
              <TextField
                label="Температура"
                type="number"
                value={modelSettings.temperature}
                onChange={(e) => setModelSettings(prev => ({ 
                  ...prev, 
                  temperature: parseFloat(e.target.value) || 0.7 
                }))}
                inputProps={{ min: 0.1, max: maxValues.temperature, step: 0.1 }}
                fullWidth
              />
              
              <TextField
                label="Top-p"
                type="number"
                value={modelSettings.top_p}
                onChange={(e) => setModelSettings(prev => ({ 
                  ...prev, 
                  top_p: parseFloat(e.target.value) || 0.95 
                }))}
                inputProps={{ min: 0.1, max: maxValues.top_p, step: 0.05 }}
                fullWidth
              />
              
              <TextField
                label="Штраф за повторы"
                type="number"
                value={modelSettings.repeat_penalty}
                onChange={(e) => setModelSettings(prev => ({ 
                  ...prev, 
                  repeat_penalty: parseFloat(e.target.value) || 1.05 
                }))}
                inputProps={{ min: 1.0, max: maxValues.repeat_penalty, step: 0.05 }}
                fullWidth
              />
            </Box>
            
            <Box mt={2}>
              <FormControlLabel
                control={
                  <Switch
                    checked={modelSettings.use_gpu}
                    onChange={(e) => setModelSettings(prev => ({ 
                      ...prev, 
                      use_gpu: e.target.checked 
                    }))}
                  />
                }
                label="Использовать GPU"
              />
              
              <FormControlLabel
                control={
                  <Switch
                    checked={modelSettings.streaming}
                    onChange={(e) => setModelSettings(prev => ({ 
                      ...prev, 
                      streaming: e.target.checked 
                    }))}
                  />
                }
                label="Потоковая генерация"
              />
              
              {modelSettings.streaming && (
                <Box sx={{ mt: 2 }}>
                  <Typography variant="body2" color="text.secondary" gutterBottom>
                    Скорость потоковой генерации: {modelSettings.streaming_speed}ms
                  </Typography>
                  <input
                    type="range"
                    min="10"
                    max="200"
                    step="10"
                    value={modelSettings.streaming_speed}
                    onChange={(e) => setModelSettings(prev => ({ 
                      ...prev, 
                      streaming_speed: parseInt(e.target.value) 
                    }))}
                    style={{
                      width: '100%',
                      height: '6px',
                      borderRadius: '3px',
                      background: 'linear-gradient(to right, #1976d2 0%, #1976d2 50%, #e0e0e0 50%, #e0e0e0 100%)',
                      outline: 'none',
                      WebkitAppearance: 'none',
                    }}
                  />
                  <Box sx={{ display: 'flex', justifyContent: 'space-between', mt: 1 }}>
                    <Typography variant="caption" color="text.secondary">
                      Быстро (10ms)
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      Медленно (200ms)
                    </Typography>
                  </Box>
                </Box>
              )}
            </Box>
            
            {/* Кнопки управления настройками модели */}
            <Box sx={{ mt: 3, display: 'flex', gap: 2, flexWrap: 'wrap' }}>
              <Button
                variant="contained"
                startIcon={<SaveIcon />}
                onClick={saveModelSettings}
                disabled={isLoading}
                color="primary"
              >
                Сохранить настройки модели
              </Button>
              
              <Button
                variant="outlined"
                startIcon={<RefreshIcon />}
                onClick={resetModelSettings}
                disabled={isLoading}
                color="secondary"
              >
                Сбросить к рекомендуемым
              </Button>
              
              <Button
                variant="outlined"
                startIcon={<RefreshIcon />}
                onClick={loadModelSettings}
                disabled={isLoading}
              >
                Обновить данные
              </Button>
            </Box>
          </CardContent>
        </Card>

        {/* Управление моделями */}
        <Card sx={{ mb: 3 }}>
          <CardContent>
            <Typography variant="h6" gutterBottom>
              Управление моделями
            </Typography>
            
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

        {/* Контекстные промпты */}
        <Card sx={{ mb: 3 }}>
          <CardContent>
            <Typography variant="h6" gutterBottom>
              Контекстные промпты
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
              Настройте системные промпты для моделей. Глобальный промпт применяется ко всем моделям по умолчанию, 
              но вы можете создать индивидуальные промпты для конкретных моделей.
            </Typography>

            {/* Глобальный промпт */}
            <Box sx={{ mb: 3 }}>
              <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 2 }}>
                <Typography variant="subtitle1" fontWeight="600">
                  Глобальный промпт
                </Typography>
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
                  {contextPrompts.globalPrompt || 'Глобальный промпт не установлен'}
                </Typography>
              </Box>
            </Box>

            {/* Промпты для моделей */}
            <Box sx={{ mb: 2 }}>
              <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 2 }}>
                <Typography variant="subtitle1" fontWeight="600">
                  Промпты для моделей
                </Typography>
                <Button
                  variant="contained"
                  size="small"
                  startIcon={<span>+</span>}
                  onClick={() => setShowModelPromptDialog(true)}
                >
                  Добавить промпт
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
                        <Button
                          variant="outlined"
                          size="small"
                          color="error"
                          onClick={() => deleteModelPrompt(model.path)}
                        >
                          Удалить
                        </Button>
                      </Box>
                    </ListItem>
                  ))}
                </List>
              ) : (
                <Typography variant="body2" color="text.secondary">
                  Индивидуальные промпты для моделей не созданы
                </Typography>
              )}
            </Box>
          </CardContent>
        </Card>


        {/* Настройки транскрибации */}
        <Card sx={{ mb: 3 }}>
          <CardContent>
            <Typography variant="h6" gutterBottom>
              Настройки транскрибации
            </Typography>
            
            <Box display="grid" gridTemplateColumns="repeat(auto-fit, minmax(250px, 1fr))" gap={2}>
              <FormControl fullWidth>
                <InputLabel>Движок транскрибации</InputLabel>
                <Select
                  value={transcriptionSettings.engine}
                  label="Движок транскрибации"
                  onChange={(e) => setTranscriptionSettings(prev => ({ 
                    ...prev, 
                    engine: e.target.value as "whisperx"
                  }))}
                >
                  <MenuItem value="whisperx">WhisperX</MenuItem>
                </Select>
              </FormControl>
              
              <FormControl fullWidth>
                <InputLabel>Язык транскрибации</InputLabel>
                <Select
                  value={transcriptionSettings.language}
                  label="Язык транскрибации"
                  onChange={(e) => setTranscriptionSettings(prev => ({ 
                    ...prev, 
                    language: e.target.value 
                  }))}
                >
                  <MenuItem value="ru">Русский</MenuItem>
                  <MenuItem value="en">English</MenuItem>
                  <MenuItem value="auto">Автоопределение</MenuItem>
                </Select>
              </FormControl>
            </Box>
            
            <Box mt={2}>
              <FormControlLabel
                control={
                  <Switch
                    checked={transcriptionSettings.auto_detect}
                    onChange={(e) => setTranscriptionSettings(prev => ({ 
                      ...prev, 
                      auto_detect: e.target.checked 
                    }))}
                  />
                }
                label="Автоматическое определение языка"
              />
            </Box>
          </CardContent>
        </Card>

                 {/* Контекст диалога + общая библиотека (legacy страница) */}
         <Card sx={{ mb: 3 }}>
           <CardContent>
             <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
               <MemoryIcon color="primary" />
               <Typography variant="h6" gutterBottom sx={{ mb: 0 }}>
                 Контекст диалога
               </Typography>
             </Box>
             
             <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
               История сообщений чата (отдельно от баз знаний RAG)
             </Typography>

             <Alert severity="info" sx={{ mb: 2 }}>
               <Typography variant="body2" sx={{ mb: 1 }}>
                 Документы для RAG — через <strong>Базы знаний</strong> (правая панель); к чату,
                 агенту и проекту можно прикрепить 1…N баз.
               </Typography>
               <Typography variant="body2">
                 <strong>Контекст диалога:</strong> ассистент использует последние сообщения чата.
                 Больше сообщений = лучше понимание, но больше потребление памяти. Рекомендуется: 20-40.
               </Typography>
             </Alert>
             
             <Alert severity="success" sx={{ mb: 2 }}>
               <Typography variant="body2">
                 <strong>Важно:</strong> Изменения вступят в силу после нажатия кнопки "Сохранить настройки" вверху страницы.
               </Typography>
             </Alert>
             
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
                 <TextField
                   fullWidth
                   size="small"
                   type="number"
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
                   onChange={(e) => {
                     const raw = e.target.value;
                     if (raw === '') return;
                     const v = parseInt(raw, 10);
                     if (Number.isNaN(v)) return;
                     const clamped = Math.max(5, Math.min(100, v));
                     setMemorySettings((prev) =>
                       clamped !== prev.max_messages ? { ...prev, max_messages: clamped } : prev
                     );
                   }}
                   onBlur={(e) => {
                     const raw = e.target.value.trim();
                     let v = parseInt(raw, 10);
                     if (raw === '' || Number.isNaN(v)) {
                       v = memorySettings.max_messages;
                     }
                     v = Math.max(5, Math.min(100, v));
                     setMemorySettings((prev) => (v !== prev.max_messages ? { ...prev, max_messages: v } : prev));
                   }}
                   inputProps={{ min: 5, max: 100, step: 5 }}
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
             
             <Box mt={2}>
               <FormControlLabel
                 control={
                   <Switch
                     checked={memorySettings.include_system_prompts}
                     onChange={(e) => setMemorySettings(prev => ({ 
                       ...prev, 
                       include_system_prompts: e.target.checked 
                     }))}
                   />
                 }
                 label="Включать системные промпты в контекст"
               />
               
               <FormControlLabel
                 control={
                   <Switch
                     checked={memorySettings.clear_on_restart}
                     onChange={(e) => setMemorySettings(prev => ({ 
                       ...prev, 
                       clear_on_restart: e.target.checked 
                     }))}
                   />
                 }
                 label="Очищать память при перезапуске"
               />
             </Box>
             
             {memorySettings.max_messages > 50 && (
               <Alert severity="warning" sx={{ mb: 2 }}>
                 <Typography variant="body2">
                   <strong>Внимание:</strong> Установлено большое количество сообщений ({memorySettings.max_messages}). 
                   Это может замедлить работу ассистента и увеличить потребление памяти.
                 </Typography>
               </Alert>
             )}
             
             <Box sx={{ mt: 2, p: 2, bgcolor: 'background.default', borderRadius: 1 }}>
               <Typography variant="body2" color="text.secondary" gutterBottom>
                 <strong>Текущие настройки памяти:</strong>
               </Typography>
               <Typography variant="body2" color="text.secondary">
                 • Ассистент запоминает последние <strong>{memorySettings.max_messages}</strong> сообщений
               </Typography>
               <Typography variant="body2" color="text.secondary">
                 • Примерный размер контекста: <strong>{Math.round(memorySettings.max_messages * 150)}</strong> токенов
               </Typography>
               <Typography variant="body2" color="text.secondary">
                 • Системные промпты: <strong>{memorySettings.include_system_prompts ? 'включены' : 'отключены'}</strong>
               </Typography>
               <Typography variant="body2" color="text.secondary">
                 • Очистка при перезапуске: <strong>{memorySettings.clear_on_restart ? 'включена' : 'отключена'}</strong>
               </Typography>
             </Box>
             
             <Box sx={{ mt: 2, display: 'flex', gap: 2, flexWrap: 'wrap' }}>
               <Button
                 variant="outlined"
                 color="warning"
                 onClick={async () => {
                   try {
                     const response = await fetch(getApiUrl('/api/memory/clear'), {
                       method: 'POST',
                     });
                     if (response.ok) {
                       showNotification('success', 'Память ассистента очищена');
                     } else {
                       showNotification('error', 'Не удалось очистить память');
                     }
                   } catch (error) {
                     showNotification('error', 'Ошибка при очистке памяти');
                   }
                 }}
               >
                 Очистить память сейчас
               </Button>
               <Button
                 variant="outlined"
                 color="info"
                 onClick={async () => {
                   try {
                     const response = await fetch(getApiUrl('/api/memory/status'));
                     if (response.ok) {
                       const data = await response.json();
                       showNotification('info', `В памяти: ${data.message_count || 0} сообщений`);
                     }
                   } catch (error) {
                     showNotification('error', 'Не удалось получить статус памяти');
                   }
                 }}
               >
                 Статус памяти
               </Button>
               <Button
                 variant="outlined"
                 color="secondary"
                 onClick={() => {
                   setMemorySettings({
                     max_messages: 20,
                     include_system_prompts: true,
                     clear_on_restart: false,
                   });
                   showNotification('info', 'Настройки контекста диалога сброшены к значениям по умолчанию');
                 }}
               >
                 Сбросить к умолчаниям
               </Button>
             </Box>
           </CardContent>
         </Card>

         {/* Системная информация */}
         <Card>
           <CardContent>
             <Typography variant="h6" gutterBottom>
               Системная информация
             </Typography>
            
            <Box display="grid" gridTemplateColumns="repeat(auto-fit, minmax(200px, 1fr))" gap={2}>
              <Box>
                <Typography variant="subtitle2" color="text.secondary">
                  Версия веб-приложения
                </Typography>
                <Typography variant="body1">
                  Web Interface v1.0.3
                </Typography>
              </Box>
              
              <Box>
                <Typography variant="subtitle2" color="text.secondary">
                  Платформа
                </Typography>
                <Typography variant="body1">
                  {navigator.platform || 'Неизвестно'}
                </Typography>
              </Box>
              
              <Box>
                <Typography variant="subtitle2" color="text.secondary">
                  Браузер
                </Typography>
                <Typography variant="body1">
                  {navigator.userAgent.split(' ')[0] || 'Неизвестно'}
                </Typography>
              </Box>
              
              <Box>
                <Typography variant="subtitle2" color="text.secondary">
                  Backend
                </Typography>
                <Typography variant="body1">
                  FastAPI + Python
                </Typography>
              </Box>
              
              <Box>
                <Typography variant="subtitle2" color="text.secondary">
                  Frontend
                </Typography>
                <Typography variant="body1">
                  React + TypeScript
                </Typography>
              </Box>
              
              <Box>
                <Typography variant="subtitle2" color="text.secondary">
                  Соединение
                </Typography>
                <Typography variant="body1">
                  Socket.IO
                </Typography>
              </Box>
            </Box>
          </CardContent>
        </Card>
        </Collapse>
      </Container>

      {/* Диалог редактирования промптов */}
      <Dialog
        open={promptDialogOpen}
        onClose={() => setPromptDialogOpen(false)}
        maxWidth="md"
        fullWidth
      >
        <DialogTitle>
          {promptDialogType === 'global' && 'Редактирование глобального промпта'}
          {promptDialogType === 'model' && 'Редактирование промпта модели'}
        </DialogTitle>
        <DialogContent>
          <TextField
            label="Промпт"
            value={promptDialogData.prompt}
            onChange={(e) => setPromptDialogData(prev => ({ ...prev, prompt: e.target.value }))}
            fullWidth
            multiline
            rows={8}
            placeholder="Введите системный промпт для модели..."
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

      {/* Диалог выбора модели для промпта */}
      <Dialog
        open={showModelPromptDialog}
        onClose={() => setShowModelPromptDialog(false)}
        maxWidth="md"
        fullWidth
      >
        <DialogTitle>Выберите модель для создания промпта</DialogTitle>
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
                        {model.has_custom_prompt && (
                          <Chip label="Есть промпт" size="small" color="primary" />
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
      
    </Box>
  );
}
