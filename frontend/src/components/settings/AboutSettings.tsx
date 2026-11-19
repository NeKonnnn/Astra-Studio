import React, { useState, useEffect } from 'react';
import {
  Box,
  Typography,
  Card,
  CardContent,
  Chip,
  Button,
  Alert,
  List,
  ListItem,
  ListItemText,
  ListItemIcon,
  Paper,
} from '@mui/material';
import {
  InfoOutlined as InfoIcon,
  ComputerOutlined as ComputerIcon,
  LanguageOutlined as LanguageIcon,
  StorageOutlined as StorageIcon,
  SpeedOutlined as SpeedIcon,
  SecurityOutlined as SecurityIcon,
  Refresh as RefreshIcon,
  DownloadOutlined as DownloadIcon,
} from '@mui/icons-material';
import { MENU_ICON_MIN_WIDTH, MENU_ICON_TO_TEXT_GAP_PX, MENU_ICON_FONT_SIZE_PX } from '../../constants/menuStyles';
import UpdatesSettings from './UpdatesSettings';

interface SystemInfo {
  version: string;
  platform: string;
  browser: string;
  backend: string;
  frontend: string;
  connection: string;
  memory_usage?: string;
  cpu_usage?: string;
  disk_usage?: string;
}

export default function AboutSettings() {
  const [systemInfo] = useState<SystemInfo>({
    version: 'AstraStudio (см. блок «Обновления» / VERSION)',
    platform: navigator.platform || 'Неизвестно',
    browser: navigator.userAgent.split(' ')[0] || 'Неизвестно',
    backend: 'FastAPI + Python',
    frontend: 'React + TypeScript',
    connection: 'Socket.IO',
  });
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    loadSystemInfo();
  }, []);

  const loadSystemInfo = async () => {
    try {
      setIsLoading(true);
      setError(null);

      // Пока эндпоинт /api/system/info не реализован, используем базовую информацию
      // В будущем можно будет заменить на реальный API:
      // const response = await fetch(`${API_BASE_URL}/api/system/info`);
      // if (response.ok) {
      //   const data = await response.json();
      //   setSystemInfo(prev => ({ ...prev, ...data }));
      // }
    } catch (error) {
      console.warn('Не удалось загрузить дополнительную системную информацию:', error);
      setError('Не удалось загрузить полную системную информацию');
    } finally {
      setIsLoading(false);
    }
  };

  const getBrowserInfo = () => {
    const userAgent = navigator.userAgent;
    let browserName = 'Unknown';
    let browserVersion = '';

    if (userAgent.includes('Chrome')) {
      browserName = 'Chrome';
      browserVersion = userAgent.match(/Chrome\/(\d+)/)?.[1] || '';
    } else if (userAgent.includes('Firefox')) {
      browserName = 'Firefox';
      browserVersion = userAgent.match(/Firefox\/(\d+)/)?.[1] || '';
    } else if (userAgent.includes('Safari')) {
      browserName = 'Safari';
      browserVersion = userAgent.match(/Version\/(\d+)/)?.[1] || '';
    } else if (userAgent.includes('Edge')) {
      browserName = 'Edge';
      browserVersion = userAgent.match(/Edge\/(\d+)/)?.[1] || '';
    }

    return { name: browserName, version: browserVersion };
  };

  const browserInfo = getBrowserInfo();

  return (
    <Box sx={{ p: 3 }}>
      {error && (
        <Alert severity="warning" sx={{ mb: 3 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      <UpdatesSettings />

      {/* Основная информация */}
      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Typography variant="h6" gutterBottom sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <InfoIcon color="primary" />
            Информация о приложении
          </Typography>
          
          <Box sx={{ display: 'flex', flexDirection: { xs: 'column', md: 'row' }, gap: 3 }}>
            <Box sx={{ flex: 1, p: 2, border: '1px solid', borderColor: 'grey.300', borderRadius: 1 }}>
              <Typography variant="subtitle1" fontWeight="600" gutterBottom>
                Название
              </Typography>
              <Typography variant="body1" sx={{ mb: 2 }}>
                AstraStudio
              </Typography>
              
              <Typography variant="subtitle1" fontWeight="600" gutterBottom>
                Версия
              </Typography>
              <Typography variant="body1" sx={{ mb: 2 }}>
                {systemInfo.version}
              </Typography>
              
              <Typography variant="subtitle1" fontWeight="600" gutterBottom>
                Описание
              </Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                Интеллектуальный голосовой ассистент с поддержкой агентной архитектуры, 
                транскрибации речи и управления моделями ИИ.
              </Typography>

              <Typography variant="subtitle1" fontWeight="600" gutterBottom>
                Разработчик
              </Typography>
              <Typography variant="body1">
                Константин Некрасов
              </Typography>
            </Box>
            
            <Box sx={{ flex: 1, p: 2, border: '1px solid', borderColor: 'grey.300', borderRadius: 1 }}>
              <Typography variant="subtitle1" fontWeight="600" gutterBottom>
                Возможности
              </Typography>
              <List dense>
                <ListItem sx={{ px: 0 }}>
                  <ListItemIcon sx={{ minWidth: `${MENU_ICON_MIN_WIDTH}px`, marginRight: `${MENU_ICON_TO_TEXT_GAP_PX}px`, '& .MuiSvgIcon-root': { fontSize: `${MENU_ICON_FONT_SIZE_PX}px` } }}>
                    <ComputerIcon fontSize="small" />
                  </ListItemIcon>
                  <ListItemText 
                    primary="Управление моделями ИИ" 
                    secondary="Загрузка и настройка различных языковых моделей"
                  />
                </ListItem>
                <ListItem sx={{ px: 0 }}>
                  <ListItemIcon sx={{ minWidth: `${MENU_ICON_MIN_WIDTH}px`, marginRight: `${MENU_ICON_TO_TEXT_GAP_PX}px`, '& .MuiSvgIcon-root': { fontSize: `${MENU_ICON_FONT_SIZE_PX}px` } }}>
                    <LanguageIcon fontSize="small" />
                  </ListItemIcon>
                  <ListItemText 
                    primary="Голосовое взаимодействие" 
                    secondary="Транскрибация речи и синтез ответов"
                  />
                </ListItem>
                <ListItem sx={{ px: 0 }}>
                  <ListItemIcon sx={{ minWidth: `${MENU_ICON_MIN_WIDTH}px`, marginRight: `${MENU_ICON_TO_TEXT_GAP_PX}px`, '& .MuiSvgIcon-root': { fontSize: `${MENU_ICON_FONT_SIZE_PX}px` } }}>
                    <StorageIcon fontSize="small" />
                  </ListItemIcon>
                  <ListItemText 
                    primary="Агентная архитектура" 
                    secondary="Специализированные агенты для решения задач"
                  />
                </ListItem>
              </List>
            </Box>
          </Box>
        </CardContent>
      </Card>

      {/* Техническая информация */}
      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Typography variant="h6" gutterBottom sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <ComputerIcon color="primary" />
            Техническая информация
          </Typography>
          
          <Box sx={{ display: 'flex', flexDirection: { xs: 'column', md: 'row' }, gap: 3 }}>
            <Box sx={{ flex: 1 }}>
              <Typography variant="subtitle2" color="text.secondary" gutterBottom>
                Платформа
              </Typography>
              <Typography variant="body1" sx={{ mb: 2 }}>
                {systemInfo.platform}
              </Typography>
              
              <Typography variant="subtitle2" color="text.secondary" gutterBottom>
                Браузер
              </Typography>
              <Typography variant="body1" sx={{ mb: 2 }}>
                {browserInfo.name} {browserInfo.version && `v${browserInfo.version}`}
              </Typography>
              
              <Typography variant="subtitle2" color="text.secondary" gutterBottom>
                Backend
              </Typography>
              <Typography variant="body1" sx={{ mb: 2 }}>
                {systemInfo.backend}
              </Typography>
            </Box>
            
            <Box sx={{ flex: 1 }}>
              <Typography variant="subtitle2" color="text.secondary" gutterBottom>
                Frontend
              </Typography>
              <Typography variant="body1" sx={{ mb: 2 }}>
                {systemInfo.frontend}
              </Typography>
              
              <Typography variant="subtitle2" color="text.secondary" gutterBottom>
                Соединение
              </Typography>
              <Typography variant="body1" sx={{ mb: 2 }}>
                {systemInfo.connection}
              </Typography>
              
              <Typography variant="subtitle2" color="text.secondary" gutterBottom>
                Статус
              </Typography>
              <Chip label="Активен" color="success" size="small" />
            </Box>
          </Box>
        </CardContent>
      </Card>

      {/* Системные ресурсы */}
      {(systemInfo.memory_usage || systemInfo.cpu_usage || systemInfo.disk_usage) && (
        <Card sx={{ mb: 3 }}>
          <CardContent>
            <Typography variant="h6" gutterBottom sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
              <SpeedIcon color="primary" />
              Системные ресурсы
            </Typography>
            
            <Box sx={{ display: 'flex', flexDirection: { xs: 'column', md: 'row' }, gap: 2 }}>
              {systemInfo.memory_usage && (
                <Box sx={{ flex: 1 }}>
                  <Paper sx={{ p: 2, textAlign: 'center' }}>
                    <StorageIcon color="primary" sx={{ fontSize: 40, mb: 1 }} />
                    <Typography variant="h6" gutterBottom>
                      {systemInfo.memory_usage}
                    </Typography>
                    <Typography variant="body2" color="text.secondary">
                      Использование памяти
                    </Typography>
                  </Paper>
                </Box>
              )}
              
              {systemInfo.cpu_usage && (
                <Box sx={{ flex: 1 }}>
                  <Paper sx={{ p: 2, textAlign: 'center' }}>
                    <SpeedIcon color="primary" sx={{ fontSize: 40, mb: 1 }} />
                    <Typography variant="h6" gutterBottom>
                      {systemInfo.cpu_usage}
                    </Typography>
                    <Typography variant="body2" color="text.secondary">
                      Загрузка процессора
                    </Typography>
                  </Paper>
                </Box>
              )}
              
              {systemInfo.disk_usage && (
                <Box sx={{ flex: 1 }}>
                  <Paper sx={{ p: 2, textAlign: 'center' }}>
                    <StorageIcon color="primary" sx={{ fontSize: 40, mb: 1 }} />
                    <Typography variant="h6" gutterBottom>
                      {systemInfo.disk_usage}
                    </Typography>
                    <Typography variant="body2" color="text.secondary">
                      Использование диска
                    </Typography>
                  </Paper>
                </Box>
              )}
            </Box>
          </CardContent>
        </Card>
      )}

      {/* Безопасность и лицензия */}
      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Typography variant="h6" gutterBottom sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <SecurityIcon color="primary" />
            Безопасность и лицензия
          </Typography>
          
          <Box sx={{ display: 'flex', flexDirection: { xs: 'column', md: 'row' }, gap: 3 }}>
            <Box sx={{ flex: 1 }}>
              <Typography variant="subtitle2" color="text.secondary" gutterBottom>
                Лицензия
              </Typography>
              <Typography variant="body1" sx={{ mb: 2 }}>
                MIT License
              </Typography>
              
              <Typography variant="subtitle2" color="text.secondary" gutterBottom>
                Безопасность
              </Typography>
              <Typography variant="body1" sx={{ mb: 2 }}>
                Локальная обработка данных
              </Typography>
            </Box>
            
            <Box sx={{ flex: 1 }}>
              <Typography variant="subtitle2" color="text.secondary" gutterBottom>
                Конфиденциальность
              </Typography>
              <Typography variant="body1" sx={{ mb: 2 }}>
                Данные не передаются третьим лицам
              </Typography>
              
              <Typography variant="subtitle2" color="text.secondary" gutterBottom>
                Обновления
              </Typography>
              <Typography variant="body1">
                Проверка и установка — в блоке «Обновления» выше (только admin)
              </Typography>
            </Box>
          </Box>
        </CardContent>
      </Card>

      {/* Кнопки управления */}
      <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap' }}>
        <Button
          variant="outlined"
          startIcon={<RefreshIcon />}
          onClick={loadSystemInfo}
          disabled={isLoading}
        >
          Обновить информацию
        </Button>
        
        <Button
          variant="outlined"
          startIcon={<DownloadIcon />}
          onClick={() => {
            const info = {
              ...systemInfo,
              timestamp: new Date().toISOString(),
              browser: browserInfo,
            };
            const blob = new Blob([JSON.stringify(info, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'system-info.json';
            a.click();
            URL.revokeObjectURL(url);
          }}
        >
          Экспорт информации
        </Button>
      </Box>
    </Box>
  );
}
