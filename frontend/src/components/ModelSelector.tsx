import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  Box,
  Typography,
  Button,
  Menu,
  MenuItem,
  CircularProgress,
} from '@mui/material';
import {
  Computer as ComputerIcon,
  KeyboardArrowDown as KeyboardArrowDownIcon,
} from '@mui/icons-material';
import { useAppActions } from '../contexts/AppContext';
import { authFetch, getApiUrl } from '../config/api';
import { MENU_BORDER_RADIUS_PX } from '../constants/menuStyles';
import {
  isValidSelectedModelPath,
  LAST_SELECTED_MODEL_PATH_STORAGE_KEY,
} from '../utils/modelThinking';
import { MODEL_PATH_CHANGED_EVENT } from '../utils/contextTokens';
import {
  ensureSelectedModelWeights,
  MODEL_WEIGHTS_ENSURE_EVENT,
  type ModelWeightsEnsureDetail,
} from '../utils/applyAgentServer';
import { useAuth } from '../contexts/AuthContext';


interface Model {
  name: string;
  path: string;
  size?: number;
  size_mb?: number;
}

interface ModelSelectorProps {
  isDarkMode: boolean;
  onModelSelect?: (modelPath: string) => void;
}

// Описания моделей на основе их имен
const getModelDescription = (modelName: string): string => {
  const name = modelName.toLowerCase();
  
  if (name.includes('qwen3-max')) {
    return 'Самая мощная языковая модель в серии Qwen.';
  }
  if (name.includes('qwen3-vl') && name.includes('235b')) {
    return 'Мощная модель взаимодействия изображений и языка на основе Qwen3';
  }
  if (name.includes('qwen3-coder')) {
    return 'Сильный агент программирования, способный выполнять задачи на длительном временном горизонте';
  }
  if (name.includes('qwen3-vl') && name.includes('32b')) {
    return 'Мощная плотная модель визуально-речевого взаимодействия из серии Qwen3-VL.';
  }
  if (name.includes('qwen')) {
    return 'Мощная языковая модель серии Qwen';
  }
  if (name.includes('coder') || name.includes('code')) {
    return 'Специализированная модель для программирования и работы с кодом';
  }
  if (name.includes('mistral')) {
    return 'Эффективная языковая модель Mistral';
  }
  if (name.includes('deepseek')) {
    return 'Продвинутая модель для рассуждений и программирования';
  }
  if (name.includes('llama') || name.includes('codellama')) {
    return 'Мощная модель для программирования и общих задач';
  }
  
  return 'Языковая модель для различных задач';
};

export default function ModelSelector({ isDarkMode, onModelSelect }: ModelSelectorProps) {
  const [availableModels, setAvailableModels] = useState<Model[]>([]);
  const [currentModel, setCurrentModel] = useState<any>(null);
  const [selectedModelPath, setSelectedModelPath] = useState<string>('');
  const [isLoading, setIsLoading] = useState(false);
  const [isLoadingModel, setIsLoadingModel] = useState(false);
  const [anchorEl, setAnchorEl] = useState<null | HTMLElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const open = Boolean(anchorEl);
  
  const { showNotification } = useAppActions();
  const { token } = useAuth();

  const loadModels = useCallback(async () => {
    try {
      setIsLoading(true);
      const response = await authFetch(getApiUrl('/api/models'));
      if (response.ok) {
        const data = await response.json();
        setAvailableModels(data.models || []);
      }
    } catch (err) {
      
    } finally {
      setIsLoading(false);
    }
  }, []);

  const loadCurrentModel = useCallback(async () => {
    try {
      const response = await authFetch(getApiUrl('/api/models/current'));
      if (response.ok) {
        const data = await response.json();
        
        setCurrentModel(data);
        const path = typeof data.path === 'string' ? data.path : '';
        if (isValidSelectedModelPath(path)) {
          setSelectedModelPath(path);
          localStorage.setItem(LAST_SELECTED_MODEL_PATH_STORAGE_KEY, path);
        } else {
          setSelectedModelPath('');
          localStorage.removeItem(LAST_SELECTED_MODEL_PATH_STORAGE_KEY);
        }
      }
    } catch (err) {
      
    }
  }, []);

  useEffect(() => {
    loadModels();
    loadCurrentModel();
  }, [loadModels, loadCurrentModel]);

  useEffect(() => {
    const onAgentStatusChanged = () => {
      loadModels();
      loadCurrentModel();
    };
    window.addEventListener('astrachatAgentStatusChanged', onAgentStatusChanged);
    return () => window.removeEventListener('astrachatAgentStatusChanged', onAgentStatusChanged);
  }, [loadModels, loadCurrentModel]);

  useEffect(() => {
    const onEnsure = (e: Event) => {
      const detail = (e as CustomEvent<ModelWeightsEnsureDetail>).detail;
      if (!detail?.path) return;
      setIsLoadingModel(detail.phase === 'start');
    };
    window.addEventListener(MODEL_WEIGHTS_ENSURE_EVENT, onEnsure);
    return () => window.removeEventListener(MODEL_WEIGHTS_ENSURE_EVENT, onEnsure);
  }, []);

  useEffect(() => {
    if (!token) return;
    const path =
      (typeof selectedModelPath === 'string' && selectedModelPath) ||
      (localStorage.getItem(LAST_SELECTED_MODEL_PATH_STORAGE_KEY) || '');
    if (!isValidSelectedModelPath(path)) return;
    void ensureSelectedModelWeights(token, path);
  }, [token, selectedModelPath]);

  const handleClick = (event: React.MouseEvent<HTMLElement>) => {
    setAnchorEl(event.currentTarget);
  };

  const handleClose = () => {
    setAnchorEl(null);
  };

  const handleModelSelect = async (modelPath: string) => {
    const sameAsSelected = modelPath === selectedModelPath;
    try {
      handleClose();
      if (!token) {
        throw new Error('Нет токена авторизации');
      }
      const result = await ensureSelectedModelWeights(token, modelPath, { force: true });
      if (!(result.ok && !('pending' in result && result.pending))) {
        throw new Error(
          ('message' in result && result.message) || 'Не удалось загрузить модель',
        );
      }
      setSelectedModelPath(modelPath);
      localStorage.setItem(LAST_SELECTED_MODEL_PATH_STORAGE_KEY, modelPath);
      window.dispatchEvent(
        new CustomEvent(MODEL_PATH_CHANGED_EVENT, { detail: { path: modelPath } }),
      );
      await loadCurrentModel();
      showNotification(
        'success',
        sameAsSelected ? 'Веса модели перезагружены' : 'Модель успешно загружена!',
      );
      onModelSelect?.(modelPath);
    } catch (err: any) {
      showNotification('error', `Ошибка загрузки модели: ${err.message || err}`);
    }
  };
  const getCurrentModelName = () => {
    const modelPath = currentModel?.path || selectedModelPath;
    if (!isValidSelectedModelPath(modelPath)) {
      return 'Выберите модель';
    }

    // Сначала проверяем, есть ли название модели напрямую в ответе API
    if (currentModel?.name && currentModel.name !== 'Неизвестно' && currentModel.name !== 'Unknown') {
      return currentModel.name;
    }
    
    // Проверяем метаданные, но игнорируем "Неизвестно"
    const metadataName = currentModel?.metadata?.['general.name'];
    if (metadataName && metadataName !== 'Неизвестно' && metadataName !== 'Unknown') {
      return metadataName;
    }
    
    // Если есть путь к модели, пытаемся найти её в списке доступных моделей
    if (modelPath) {
      const model = availableModels.find(m => m.path === modelPath);
      if (model) {
        return model.name.replace('.gguf', '');
      }
      // Если модель не найдена в списке, извлекаем название из пути
      const fileName = modelPath.split('/').pop() || modelPath.split('\\').pop();
      if (fileName) {
        return fileName.replace('.gguf', '');
      }
    }
    
    return 'Выберите модель';
  };

  if (isLoading) {
    return (
      <Button
        disabled
        sx={{
          color: isDarkMode ? 'rgba(255, 255, 255, 0.7)' : 'rgba(0, 0, 0, 0.7)',
          textTransform: 'none',
        }}
      >
        <CircularProgress size={16} sx={{ mr: 1 }} />
        Загрузка моделей...
      </Button>
    );
  }

  if (availableModels.length === 0) {
    return null;
  }

  const currentModelName = getCurrentModelName();
  const hasSelectedModel = currentModelName !== 'Выберите модель';

  return (
    <Box>
      <Box
        ref={containerRef}
        onClick={isLoadingModel ? undefined : handleClick}
        sx={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'flex-start',
          gap: 0.5,
          userSelect: 'none',
          cursor: isLoadingModel ? 'default' : 'pointer',
          opacity: isLoadingModel ? 0.6 : 1,
          transition: 'opacity 0.2s',
          '&:hover': isLoadingModel ? {} : {
            opacity: 0.8,
          },
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          {isLoadingModel ? (
            <>
              <Typography
                variant="body1"
                sx={{
                  color: isDarkMode ? 'white' : '#333',
                  fontSize: '1rem',
                  fontWeight: 400,
                }}
              >
                Загрузка...
              </Typography>
              <CircularProgress size={16} />
            </>
          ) : hasSelectedModel ? (
            <>
              <Typography
                variant="body1"
                sx={{
                  color: isDarkMode ? 'white' : '#333',
                  fontSize: '1rem',
                  fontWeight: 400,
                }}
              >
                {currentModelName}
              </Typography>
              <KeyboardArrowDownIcon 
                fontSize="small" 
                sx={{
                  color: isDarkMode ? 'white' : '#333',
                }}
              />
            </>
          ) : (
            <>
              <Typography
                variant="body1"
                sx={{
                  color: isDarkMode ? 'white' : '#333',
                  fontSize: '1rem',
                  fontWeight: 400,
                }}
              >
                Выбрать модель
              </Typography>
              <KeyboardArrowDownIcon 
                fontSize="small" 
                sx={{
                  color: isDarkMode ? 'white' : '#333',
                }}
              />
            </>
          )}
        </Box>
      </Box>

      <Menu
        anchorEl={anchorEl}
        open={open}
        onClose={handleClose}
        PaperProps={{
          sx: {
            bgcolor: isDarkMode ? '#1e1e1e' : '#ffffff',
            minWidth: 400,
            maxWidth: 600,
            maxHeight: 500,
            mt: 1,
            borderRadius: `${MENU_BORDER_RADIUS_PX}px`,
            border: `1px solid ${isDarkMode ? 'rgba(255, 255, 255, 0.1)' : 'rgba(0, 0, 0, 0.1)'}`,
            boxShadow: isDarkMode 
              ? '0 8px 32px rgba(0, 0, 0, 0.4)'
              : '0 8px 32px rgba(0, 0, 0, 0.15)',
            '& .MuiMenuItem-root': {
              py: 1.5,
              px: 2,
            },
          },
        }}
        MenuListProps={{
          sx: {
            py: 1,
          },
        }}
      >
        {availableModels.map((model) => {
          const isSelected = selectedModelPath === model.path;
          const description = getModelDescription(model.name);
          
          return (
            <MenuItem
              key={model.path}
              onClick={() => handleModelSelect(model.path)}
              selected={isSelected}
              disabled={isLoadingModel && !isSelected}
              sx={{
                flexDirection: 'column',
                alignItems: 'flex-start',
                bgcolor: isSelected
                  ? isDarkMode
                    ? 'rgba(156, 39, 176, 0.25)'
                    : 'rgba(156, 39, 176, 0.12)'
                  : 'transparent',
                '&:hover': {
                  bgcolor: isSelected
                    ? isDarkMode
                      ? 'rgba(156, 39, 176, 0.35)'
                      : 'rgba(156, 39, 176, 0.18)'
                    : isDarkMode
                      ? 'rgba(255, 255, 255, 0.05)'
                      : 'rgba(0, 0, 0, 0.05)',
                },
                '&.Mui-selected': {
                  bgcolor: isDarkMode
                    ? 'rgba(156, 39, 176, 0.25)'
                    : 'rgba(156, 39, 176, 0.12)',
                  '&:hover': {
                    bgcolor: isDarkMode
                      ? 'rgba(156, 39, 176, 0.35)'
                      : 'rgba(156, 39, 176, 0.18)',
                  },
                },
              }}
            >
              <Box sx={{ display: 'flex', alignItems: 'center', width: '100%', mb: 0.5 }}>
                <Typography
                  variant="body1"
                  sx={{
                    fontWeight: 600,
                    color: isDarkMode ? 'white' : '#333',
                    fontSize: '0.9375rem',
                    flex: 1,
                  }}
                >
                  {model.name.replace('.gguf', '')}
                </Typography>
                {isSelected && (
                  <ComputerIcon 
                    fontSize="small" 
                    sx={{ 
                      color: isDarkMode ? '#9c27b0' : '#9c27b0',
                      ml: 1,
                    }}
                  />
                )}
              </Box>
              <Typography
                variant="body2"
                sx={{
                  color: isDarkMode ? 'rgba(255, 255, 255, 0.7)' : 'rgba(0, 0, 0, 0.7)',
                  fontSize: '0.8125rem',
                  mt: 0.25,
                }}
              >
                {description}
              </Typography>
            </MenuItem>
          );
        })}
      </Menu>
    </Box>
  );
}
