import React, { useState, useRef, useEffect } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  Box,
  Typography,
  Card,
  CardContent,
  Button,
  TextField,
  Tabs,
  Tab,
  Alert,
  IconButton,
  Chip,
  useTheme,
  useMediaQuery,
  Paper,
  Tooltip,
} from '@mui/material';
import { authFetch, getApiUrl, API_ENDPOINTS } from '../config/api';
import { incrementTabNotification } from '../utils/tabNotifications';
import { applySpeakerMapping, extractSpeakers } from '../utils/speakerNames';
import {
  Close as CloseIcon,
  Upload as UploadIcon,
  YouTube as YouTubeIcon,
  PlayArrow as PlayIcon,
  Download as DownloadIcon,
  ContentCopy as CopyIcon,
  Videocam as VideoIcon,
  AudioFile as AudioIcon,
  Send as SendIcon,
  Stop as StopIcon,
  Person as PersonIcon,
  Settings as SettingsIcon,
  Add as AddIcon,
  Delete as DeleteIcon,
} from '@mui/icons-material';
import { useAppActions } from '../contexts/AppContext';

interface TabPanelProps {
  children?: React.ReactNode;
  index: number;
  value: number;
}

function TabPanel(props: TabPanelProps) {
  const { children, value, index, ...other } = props;
  return (
    <div role="tabpanel" hidden={value !== index} {...other}>
      {value === index && <Box sx={{ py: 3 }}>{children}</Box>}
    </div>
  );
}

interface TranscriptionModalProps {
  open: boolean;
  onClose: () => void;
  isTranscribing?: boolean;
  transcriptionResult?: string;
  onTranscriptionStart?: () => void;
  onTranscriptionComplete?: (result: string) => void;
  onTranscriptionError?: (error: string) => void;
  onInsertToChat?: (text: string) => void;
  /** При открытии сразу начать транскрибацию этого файла (из меню «Загрузить файл» на правом баре). */
  initialFile?: File | null;
  /** При открытии переключить на вкладку: 0 — файл, 1 — YouTube. */
  initialTab?: 0 | 1;
}

export default function TranscriptionModal({ 
  open, 
  onClose,
  isTranscribing: externalIsTranscribing,
  transcriptionResult: externalTranscriptionResult,
  onTranscriptionStart,
  onTranscriptionComplete,
  onTranscriptionError,
  onInsertToChat,
  initialFile,
  initialTab = 0,
}: TranscriptionModalProps) {
  const theme = useTheme();
  const fullScreen = useMediaQuery(theme.breakpoints.down('sm'));
  const startedForFileRef = useRef<File | null>(null);
  
  const [tabValue, setTabValue] = useState(0);
  const [isDragging, setIsDragging] = useState(false);
  const [internalIsTranscribing, setInternalIsTranscribing] = useState(false);
  const [youtubeUrl, setYoutubeUrl] = useState('');
  const [internalTranscriptionResult, setInternalTranscriptionResult] = useState('');
  const [uploadedFile, setUploadedFile] = useState<File | null>(null);
  const [showResult, setShowResult] = useState(false);
  const [transcriptionId, setTranscriptionId] = useState<string | null>(null);
  const [speakerNames, setSpeakerNames] = useState<Record<string, string>>({});
  const [showSpeakerSettings, setShowSpeakerSettings] = useState(false);
  const [originalTranscriptionResult, setOriginalTranscriptionResult] = useState<string>('');
  const [newSpeakerId, setNewSpeakerId] = useState<string>('');
  
  // Используем внешнее состояние, если оно передано, иначе внутреннее
  const isTranscribing = externalIsTranscribing !== undefined ? externalIsTranscribing : internalIsTranscribing;
  const transcriptionResult = externalTranscriptionResult !== undefined ? externalTranscriptionResult : internalTranscriptionResult;
  
  // Сохраняем оригинальный текст при изменении результата извне
  useEffect(() => {
    if (externalTranscriptionResult && externalTranscriptionResult !== originalTranscriptionResult) {
      // Сохраняем оригинальный текст только если он еще не был сохранен
      // или если новый текст не содержит уже примененные имена (т.е. это новый оригинальный результат)
      if (!originalTranscriptionResult) {
        setOriginalTranscriptionResult(externalTranscriptionResult);
      }
    }
  }, [externalTranscriptionResult, originalTranscriptionResult]);

  // При открытии с вкладки YouTube — переключаем на неё
  useEffect(() => {
    if (open && initialTab === 1) setTabValue(1);
  }, [open, initialTab]);

  // При открытии с файлом из меню правого бара — сразу запускаем транскрибацию
  useEffect(() => {
    if (!open) {
      startedForFileRef.current = null;
      return;
    }
    if (initialFile && startedForFileRef.current !== initialFile) {
      startedForFileRef.current = initialFile;
      handleFileTranscriptionWithFile(initialFile);
    }
  }, [open, initialFile]);
  
  const { showNotification } = useAppActions();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleTabChange = (event: React.SyntheticEvent, newValue: number) => {
    setTabValue(newValue);
    // Не сбрасываем transcriptionResult, так как он управляется извне
    if (!externalTranscriptionResult) {
      setInternalTranscriptionResult('');
    }
    setUploadedFile(null);
    setYoutubeUrl('');
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    
    const files = Array.from(e.dataTransfer.files);
    if (files.length > 0) {
      handleFileSelect(files[0]);
    }
  };

  const handleFileInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (files && files.length > 0) {
      handleFileSelect(files[0]);
    }
  };

  const handleFileSelect = (file: File) => {
    // Проверка типа файла
    const allowedTypes = [
      'audio/mpeg', 'audio/wav', 'audio/m4a', 'audio/aac', 'audio/flac',
      'video/mp4', 'video/avi', 'video/mov', 'video/mkv', 'video/webm',
    ];

    const isValidType = allowedTypes.some(type => 
      file.type.includes(type.split('/')[1]) || 
      file.name.toLowerCase().includes(type.split('/')[1])
    );

    if (!isValidType) {
      showNotification('error', 'Поддерживаются только аудио и видео файлы');
      return;
    }

    // Проверка размера файла (макс 5GB)
    if (file.size > 5 * 1024 * 1024 * 1024) {
      showNotification('error', 'Размер файла не должен превышать 5GB');
      return;
    }

    setUploadedFile(file);
    showNotification('success', `Файл ${file.name} готов к транскрибации`);
  };

  const handleFileTranscription = async () => {
    if (!uploadedFile) {
      showNotification('warning', 'Выберите файл для транскрибации');
      return;
    }

    if (onTranscriptionStart) {
      onTranscriptionStart();
    } else {
      setInternalIsTranscribing(true);
    }
    
    // Генерируем уникальный ID для транскрибации
    const currentTranscriptionId = `transcribe_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
    setTranscriptionId(currentTranscriptionId);
    
    try {
      const formData = new FormData();
      formData.append('file', uploadedFile);
      formData.append('request_id', currentTranscriptionId);

      const response = await authFetch(getApiUrl(API_ENDPOINTS.TRANSCRIBE_UPLOAD), {
        method: 'POST',
        body: formData,
      });

      // Проверяем статус ответа
      if (!response.ok) {
        // Если статус 499 (Client Closed Request) - транскрибация была остановлена
        if (response.status === 499) {
          const errorData = await response.json().catch(() => ({ detail: 'Транскрибация была остановлена' }));
          const error = new Error(errorData.detail || 'Транскрибация была остановлена') as Error & { status: number };
          error.status = 499;
          throw error;
        }
        // Для других ошибок
        const errorData = await response.json().catch(() => ({ detail: 'Ошибка при транскрибации' }));
        throw new Error(errorData.detail || 'Ошибка при транскрибации');
      }

      const result = await response.json();

      if (result.success) {
        // Сохраняем ID транскрибации, если он передан
        if (result.transcription_id) {
          setTranscriptionId(result.transcription_id);
        }
        
        // Сохраняем оригинальный результат для возможности повторного редактирования
        const transcriptionText = result.transcription;
        setOriginalTranscriptionResult(transcriptionText);
        
        if (onTranscriptionComplete) {
          onTranscriptionComplete(transcriptionText);
        } else {
          setInternalTranscriptionResult(transcriptionText);
        }
        setShowResult(true);
        showNotification('success', 'Транскрибация завершена');
        incrementTabNotification();
        setTranscriptionId(null); // Сбрасываем ID после завершения
      } else {
        const errorMsg = result.message || 'Ошибка при транскрибации';
        if (onTranscriptionError) {
          onTranscriptionError(errorMsg);
        }
        showNotification('error', errorMsg);
        setTranscriptionId(null); // Сбрасываем ID при ошибке
      }
    } catch (error: any) {
      console.error('Ошибка транскрибации:', error);
      // Проверяем, была ли транскрибация остановлена
      if (error?.status === 499 || error?.message?.includes('остановлена')) {
        const errorMsg = 'Транскрибация была остановлена';
        if (onTranscriptionError) {
          onTranscriptionError(errorMsg);
        }
        showNotification('info', errorMsg);
      } else {
        const errorMsg = 'Ошибка при отправке файла';
        if (onTranscriptionError) {
          onTranscriptionError(errorMsg);
        }
        showNotification('error', errorMsg);
      }
      setTranscriptionId(null);
    } finally {
      if (!onTranscriptionStart) {
        setInternalIsTranscribing(false);
      }
    }
  };

  /** Запуск транскрибации переданного файла (из меню «Загрузить файл» на правом баре). */
  const handleFileTranscriptionWithFile = async (file: File) => {
    if (onTranscriptionStart) onTranscriptionStart();
    else setInternalIsTranscribing(true);
    const currentTranscriptionId = `transcribe_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
    setTranscriptionId(currentTranscriptionId);
    try {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('request_id', currentTranscriptionId);
      const response = await authFetch(getApiUrl(API_ENDPOINTS.TRANSCRIBE_UPLOAD), {
        method: 'POST',
        body: formData,
      });
      if (!response.ok) {
        if (response.status === 499) {
          const errorData = await response.json().catch(() => ({ detail: 'Транскрибация была остановлена' }));
          const err = new Error(errorData.detail || 'Транскрибация была остановлена') as Error & { status: number };
          err.status = 499;
          throw err;
        }
        const errorData = await response.json().catch(() => ({ detail: 'Ошибка при транскрибации' }));
        throw new Error(errorData.detail || 'Ошибка при транскрибации');
      }
      const result = await response.json();
      if (result.success) {
        if (result.transcription_id) setTranscriptionId(result.transcription_id);
        const transcriptionText = result.transcription;
        setOriginalTranscriptionResult(transcriptionText);
        if (onTranscriptionComplete) onTranscriptionComplete(transcriptionText);
        else setInternalTranscriptionResult(transcriptionText);
        setShowResult(true);
        showNotification('success', 'Транскрибация завершена');
        incrementTabNotification();
        setTranscriptionId(null);
      } else {
        const errorMsg = result.message || 'Ошибка при транскрибации';
        if (onTranscriptionError) onTranscriptionError(errorMsg);
        showNotification('error', errorMsg);
        setTranscriptionId(null);
      }
    } catch (error: any) {
      if (error?.status === 499 || error?.message?.includes('остановлена')) {
        if (onTranscriptionError) onTranscriptionError('Транскрибация была остановлена');
        showNotification('info', 'Транскрибация была остановлена');
      } else {
        if (onTranscriptionError) onTranscriptionError(error?.message || 'Ошибка');
        showNotification('error', error?.message || 'Ошибка при отправке файла');
      }
      setTranscriptionId(null);
    } finally {
      if (!onTranscriptionStart) setInternalIsTranscribing(false);
    }
  };

  const handleYouTubeTranscription = async () => {
    if (!youtubeUrl.trim()) {
      showNotification('warning', 'Введите URL YouTube видео');
      return;
    }

    // Простая проверка URL
    if (!youtubeUrl.includes('youtube.com') && !youtubeUrl.includes('youtu.be')) {
      showNotification('error', 'Некорректный URL YouTube');
      return;
    }

    if (onTranscriptionStart) {
      onTranscriptionStart();
    } else {
      setInternalIsTranscribing(true);
    }
    
    try {
      const response = await authFetch(getApiUrl(API_ENDPOINTS.TRANSCRIBE_YOUTUBE), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ url: youtubeUrl }),
      });

      const result = await response.json();

      if (result.success) {
        // Сохраняем оригинальный результат для возможности повторного редактирования
        const transcriptionText = result.transcription;
        setOriginalTranscriptionResult(transcriptionText);
        
        if (onTranscriptionComplete) {
          onTranscriptionComplete(transcriptionText);
        } else {
          setInternalTranscriptionResult(transcriptionText);
        }
        setShowResult(true);
        showNotification('success', 'Транскрибация YouTube видео завершена');
        incrementTabNotification();
      } else {
        const errorMsg = result.message || 'Ошибка при транскрибации YouTube';
        if (onTranscriptionError) {
          onTranscriptionError(errorMsg);
        }
        showNotification('error', errorMsg);
      }
    } catch (error) {
      console.error('Ошибка YouTube транскрибации:', error);
      const errorMsg = 'Ошибка при обработке YouTube URL';
      if (onTranscriptionError) {
        onTranscriptionError(errorMsg);
      }
      showNotification('error', errorMsg);
    } finally {
      if (!onTranscriptionStart) {
        setInternalIsTranscribing(false);
      }
    }
  };

  const handleCopyTranscription = async () => {
    try {
      const textToCopy = transcriptionResult || '';
      await navigator.clipboard.writeText(textToCopy);
      showNotification('success', 'Транскрипция скопирована в буфер обмена');
    } catch (error) {
      showNotification('error', 'Не удалось скопировать текст');
    }
  };

  const handleDownloadTranscription = () => {
    const textToDownload = transcriptionResult || '';
    const blob = new Blob([textToDownload], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `transcription_${new Date().toISOString().split('T')[0]}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleInsertToChat = () => {
    const textToInsert = transcriptionResult || '';
    if (textToInsert && onInsertToChat) {
      onInsertToChat(textToInsert);
      showNotification('success', 'Текст вставлен в поле ввода чата');
      // Закрываем диалог с результатом, но не само модальное окно
      setShowResult(false);
    } else if (!onInsertToChat) {
      showNotification('warning', 'Функция вставки в чат недоступна');
    }
  };

  // extractSpeakers / applySpeakerMapping — см. utils/speakerNames (поиск спикеров по границам токена).

  const handleApplySpeakerNames = () => {
    // Используем оригинальный текст, если он есть, иначе текущий результат
    const sourceText = originalTranscriptionResult || transcriptionResult || '';
    if (!sourceText) return;
    
    const mappedText = applySpeakerMapping(sourceText, speakerNames);
    
    if (onTranscriptionComplete) {
      onTranscriptionComplete(mappedText);
    } else {
      setInternalTranscriptionResult(mappedText);
    }
    
    showNotification('success', 'Имена спикеров применены');
    // Не закрываем меню, чтобы можно было редактировать дальше
    // setShowSpeakerSettings(false);
  };

  const handleOpenSpeakerSettings = () => {
    // Используем оригинальный текст для извлечения спикеров, если он есть
    const sourceText = originalTranscriptionResult || transcriptionResult || '';
    const speakers = extractSpeakers(sourceText);
    
    // Объединяем найденных спикеров с уже существующими в маппинге
    const currentMapping = { ...speakerNames };
    speakers.forEach(speaker => {
      if (!(speaker in currentMapping)) {
        currentMapping[speaker] = '';
      }
    });
    
    setSpeakerNames(currentMapping);
    setShowSpeakerSettings(true);
  };

  const handleAddNewSpeaker = () => {
    if (!newSpeakerId.trim()) {
      showNotification('warning', 'Введите идентификатор спикера');
      return;
    }
    
    // Проверяем, не существует ли уже такой спикер
    if (newSpeakerId in speakerNames) {
      showNotification('warning', 'Такой спикер уже существует');
      return;
    }
    
    setSpeakerNames({
      ...speakerNames,
      [newSpeakerId]: '',
    });
    setNewSpeakerId('');
    showNotification('success', 'Спикер добавлен');
  };

  const handleRemoveSpeaker = (speakerId: string) => {
    const newMapping = { ...speakerNames };
    delete newMapping[speakerId];
    setSpeakerNames(newMapping);
    showNotification('info', 'Спикер удален из списка');
  };

  const handleStopTranscription = async () => {
    if (!transcriptionId) {
      showNotification('warning', 'ID транскрибации не найден');
      return;
    }

    try {
      const response = await authFetch(getApiUrl('/api/transcribe/stop'), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ transcription_id: transcriptionId }),
      });

      const result = await response.json();

      if (result.success) {
        showNotification('info', 'Транскрибация остановлена');
        // Сбрасываем состояние транскрибации
        if (onTranscriptionError) {
          onTranscriptionError('Транскрибация была остановлена пользователем');
        }
        setTranscriptionId(null);
      } else {
        showNotification('error', result.message || 'Ошибка при остановке транскрибации');
      }
    } catch (error) {
      console.error('Ошибка остановки транскрибации:', error);
      showNotification('error', 'Ошибка при отправке команды остановки');
    }
  };

  const formatFileSize = (bytes: number) => {
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    if (bytes === 0) return '0 Byte';
    const i = parseInt(Math.floor(Math.log(bytes) / Math.log(1024)).toString());
    return Math.round(bytes / Math.pow(1024, i) * 100) / 100 + ' ' + sizes[i];
  };

  const getFileIcon = (file: File) => {
    if (file.type.startsWith('video/')) return <VideoIcon color="primary" />;
    if (file.type.startsWith('audio/')) return <AudioIcon color="secondary" />;
    return <AudioIcon />;
  };

  const handleClose = () => {
    // Не сбрасываем состояние транскрибации, если она идет или есть результат
    // Позволяем закрыть окно, но сохраняем состояние
    setTabValue(0);
    setUploadedFile(null);
    setYoutubeUrl('');
    setShowResult(false);
    // Не сбрасываем isTranscribing и transcriptionResult - они управляются извне или сохраняются
    onClose();
  };

  return (
    <>
      <Dialog
        open={open}
        onClose={handleClose}
        maxWidth="lg"
        fullWidth
        fullScreen={fullScreen}
        PaperProps={{
          sx: {
            height: fullScreen ? '100%' : '90vh',
            maxHeight: '90vh',
          },
        }}
      >
        <DialogTitle>
          <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', position: 'relative' }}>
            <Box sx={{ flex: 1, textAlign: 'center' }}>
              <Typography variant="h5" fontWeight="600">
                Транскрибация
              </Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                Преобразование аудио и видео в текст
              </Typography>
            </Box>
            <IconButton 
              onClick={handleClose} 
              size="small"
              sx={{ position: 'absolute', right: 0 }}
            >
              <CloseIcon />
            </IconButton>
          </Box>
        </DialogTitle>

        <DialogContent dividers sx={{ p: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
          <Box sx={{ flex: 1, overflow: 'auto', p: 3 }}>
            {/* Вкладки */}
            <Card sx={{ mb: 3 }}>
              <Tabs
                value={tabValue}
                onChange={handleTabChange}
                variant="fullWidth"
                textColor="primary"
                indicatorColor="primary"
              >
                <Tab icon={<UploadIcon />} label="Загрузить файл" />
                <Tab icon={<YouTubeIcon />} label="YouTube видео" />
              </Tabs>
            </Card>

            {/* Вкладка загрузки файла */}
            <TabPanel value={tabValue} index={0}>
              <Card>
                <CardContent>
                  {!uploadedFile ? (
                    <Box
                      sx={{
                        border: '2px dashed',
                        borderColor: isDragging ? 'primary.main' : 'grey.300',
                        borderRadius: 2,
                        p: 6,
                        textAlign: 'center',
                        backgroundColor: isDragging ? 'primary.50' : 'background.default',
                        cursor: 'pointer',
                        transition: 'all 0.3s ease',
                        '&:hover': {
                          borderColor: 'primary.main',
                          backgroundColor: 'primary.50',
                        },
                      }}
                      onDragOver={handleDragOver}
                      onDragLeave={handleDragLeave}
                      onDrop={handleDrop}
                      onClick={() => fileInputRef.current?.click()}
                    >
                      <UploadIcon sx={{ fontSize: 64, color: 'primary.main', mb: 2 }} />
                      <Typography variant="h5" gutterBottom>
                        Перетащите файл сюда или нажмите для выбора
                      </Typography>
                      <Typography variant="body1" color="text.secondary" sx={{ mb: 2 }}>
                        Поддерживаются аудио и видео файлы
                      </Typography>
                      <Typography variant="body2" color="text.secondary">
                        Форматы: MP3, WAV, M4A, AAC, FLAC, MP4, AVI, MOV, MKV, WebM
                      </Typography>
                      <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 1 }}>
                        Максимальный размер: 5GB
                      </Typography>
                    </Box>
                  ) : (
                    <Box>
                      <Alert severity="success" sx={{ mb: 3 }}>
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                          {getFileIcon(uploadedFile)}
                          <Box>
                            <Typography variant="body1" fontWeight="500">
                              {uploadedFile.name}
                            </Typography>
                            <Typography variant="body2" color="text.secondary">
                              {formatFileSize(uploadedFile.size)}
                            </Typography>
                          </Box>
                        </Box>
                      </Alert>
                      
                      <Box sx={{ display: 'flex', gap: 2, justifyContent: 'center' }}>
                        <Button
                          variant="contained"
                          size="large"
                          startIcon={<PlayIcon />}
                          onClick={handleFileTranscription}
                          disabled={isTranscribing}
                        >
                          Начать транскрибацию
                        </Button>
                        <Button
                          variant="outlined"
                          onClick={() => setUploadedFile(null)}
                          disabled={isTranscribing}
                        >
                          Выбрать другой файл
                        </Button>
                      </Box>
                    </Box>
                  )}
                  
                  <input
                    ref={fileInputRef}
                    type="file"
                    hidden
                    accept="audio/*,video/*"
                    onChange={handleFileInputChange}
                  />
                </CardContent>
              </Card>
            </TabPanel>

            {/* Вкладка YouTube */}
            <TabPanel value={tabValue} index={1}>
              <Card>
                <CardContent>
                  <Box sx={{ textAlign: 'center', mb: 4 }}>
                    <YouTubeIcon sx={{ fontSize: 64, color: 'error.main', mb: 2 }} />
                    <Typography variant="h5" gutterBottom>
                      Транскрибация YouTube видео
                    </Typography>
                    <Typography variant="body1" color="text.secondary">
                      Вставьте ссылку на YouTube видео для получения транскрипции
                    </Typography>
                  </Box>

                  <Box sx={{ display: 'flex', gap: 2, mb: 3 }}>
                    <TextField
                      fullWidth
                      placeholder="https://www.youtube.com/watch?v=..."
                      value={youtubeUrl}
                      onChange={(e) => setYoutubeUrl(e.target.value)}
                      disabled={isTranscribing}
                      variant="outlined"
                    />
                    <Button
                      variant="contained"
                      size="large"
                      startIcon={<PlayIcon />}
                      onClick={handleYouTubeTranscription}
                      disabled={!youtubeUrl.trim() || isTranscribing}
                      sx={{ minWidth: 200 }}
                    >
                      Транскрибировать
                    </Button>
                  </Box>

                  <Alert severity="info">
                    <Typography variant="body2">
                      Поддерживаются публичные YouTube видео. Время обработки зависит от длительности видео.
                    </Typography>
                  </Alert>
                </CardContent>
              </Card>
            </TabPanel>

            {/* Индикатор загрузки */}
            {isTranscribing && (
              <Card sx={{ mt: 3 }}>
                <CardContent>
                  <Box sx={{ textAlign: 'center', py: 4 }}>
                    {/* Три мигающие точки */}
                    <Box sx={{ 
                      display: 'flex', 
                      justifyContent: 'center', 
                      alignItems: 'center',
                      gap: 1.5,
                      mb: 4,
                      height: 50
                    }}>
                      {[0, 1, 2].map((index) => (
                        <Box
                          key={index}
                          sx={{
                            width: 12,
                            height: 12,
                            borderRadius: '50%',
                            backgroundColor: 'primary.main',
                            animation: 'pulse 1.4s ease-in-out infinite',
                            animationDelay: `${index * 0.2}s`,
                            '@keyframes pulse': {
                              '0%, 80%, 100%': {
                                transform: 'scale(0.8)',
                                opacity: 0.5,
                              },
                              '40%': {
                                transform: 'scale(1.2)',
                                opacity: 1,
                              },
                            },
                          }}
                        />
                      ))}
                    </Box>
                    <Typography variant="h6" gutterBottom sx={{ fontWeight: 600 }}>
                      Выполняется транскрибация...
                    </Typography>
                    <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
                      Это может занять несколько минут в зависимости от размера файла
                    </Typography>
                    {/* Кнопка остановки транскрибации */}
                    <Button
                      variant="outlined"
                      color="error"
                      size="large"
                      startIcon={<StopIcon />}
                      onClick={handleStopTranscription}
                      disabled={!transcriptionId}
                      sx={{ mt: 2 }}
                    >
                      Остановить транскрибацию
                    </Button>
                    <Typography variant="body2" color="text.secondary" sx={{ mt: 2, fontStyle: 'italic' }}>
                      Вы можете закрыть это окно, транскрибация продолжится в фоне
                    </Typography>
                  </Box>
                </CardContent>
              </Card>
            )}
            
            {/* Показываем результат, если транскрибация завершена и есть результат */}
            {!isTranscribing && transcriptionResult && !showResult && (
              <Card sx={{ mt: 3 }}>
                <CardContent>
                  <Alert severity="success" sx={{ mb: 2 }}>
                    Транскрибация завершена! Нажмите кнопку ниже, чтобы просмотреть результат.
                  </Alert>
                  <Box sx={{ display: 'flex', justifyContent: 'center' }}>
                    <Button
                      variant="contained"
                      onClick={() => setShowResult(true)}
                      startIcon={<CopyIcon />}
                    >
                      Просмотреть результат
                    </Button>
                  </Box>
                </CardContent>
              </Card>
            )}
          </Box>
        </DialogContent>
      </Dialog>

      {/* Диалог с результатом */}
      <Dialog
        open={showResult}
        onClose={() => setShowResult(false)}
        maxWidth="md"
        fullWidth
        scroll="paper"
      >
        <DialogTitle>
          <Typography variant="h6">Результат транскрибации</Typography>
        </DialogTitle>
        <DialogContent>
          {showSpeakerSettings && (
            <Card sx={{ mb: 2, p: 2 }}>
              <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 2 }}>
                <Typography variant="h6" sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                  <SettingsIcon />
                  Настройка имен спикеров
                </Typography>
                <IconButton 
                  size="small" 
                  onClick={() => setShowSpeakerSettings(false)}
                >
                  <CloseIcon />
                </IconButton>
              </Box>
              <Alert severity="info" sx={{ mb: 2 }}>
                Введите имена для каждого спикера. Оставьте поле пустым, чтобы оставить оригинальное имя.
                Вы можете добавлять новых спикеров вручную, если они не были автоматически обнаружены.
              </Alert>
              
              {/* Добавление нового спикера */}
              <Box sx={{ mb: 3, p: 2, backgroundColor: 'background.default', borderRadius: 1 }}>
                <Typography variant="subtitle2" gutterBottom>
                  Добавить нового спикера
                </Typography>
                <Box sx={{ display: 'flex', gap: 1, alignItems: 'center' }}>
                  <TextField
                    size="small"
                    placeholder="Например: SPEAKER_SPEAKER_05"
                    value={newSpeakerId}
                    onChange={(e) => setNewSpeakerId(e.target.value)}
                    variant="outlined"
                    sx={{ flex: 1 }}
                    onKeyPress={(e) => {
                      if (e.key === 'Enter') {
                        handleAddNewSpeaker();
                      }
                    }}
                  />
                  <Button
                    variant="outlined"
                    startIcon={<AddIcon />}
                    onClick={handleAddNewSpeaker}
                    disabled={!newSpeakerId.trim()}
                  >
                    Добавить
                  </Button>
                </Box>
              </Box>

              {/* Список спикеров */}
              <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, mb: 2, maxHeight: 400, overflow: 'auto' }}>
                {Object.keys(speakerNames).length === 0 ? (
                  <Alert severity="info">
                    Спикеры не найдены. Добавьте их вручную, используя форму выше.
                  </Alert>
                ) : (
                  Object.keys(speakerNames).map((speaker) => (
                    <Box key={speaker} sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
                      <Typography variant="body2" sx={{ minWidth: 150, fontWeight: 'bold' }}>
                        {speaker}:
                      </Typography>
                      <TextField
                        fullWidth
                        size="small"
                        placeholder={`Введите имя для ${speaker}`}
                        value={speakerNames[speaker] || ''}
                        onChange={(e) => {
                          setSpeakerNames({
                            ...speakerNames,
                            [speaker]: e.target.value,
                          });
                        }}
                        variant="outlined"
                      />
                      <IconButton
                        size="small"
                        onClick={() => handleRemoveSpeaker(speaker)}
                        color="error"
                        title="Удалить спикера"
                      >
                        <DeleteIcon fontSize="small" />
                      </IconButton>
                    </Box>
                  ))
                )}
              </Box>
              <Box sx={{ display: 'flex', gap: 2, justifyContent: 'space-between', alignItems: 'center' }}>
                <Typography variant="body2" color="text.secondary">
                  {Object.keys(speakerNames).length} {Object.keys(speakerNames).length === 1 ? 'спикер' : 'спикеров'}
                </Typography>
                <Box sx={{ display: 'flex', gap: 2 }}>
                  <Button 
                    variant="outlined" 
                    onClick={() => setShowSpeakerSettings(false)}
                  >
                    Закрыть
                  </Button>
                  <Button 
                    variant="contained" 
                    onClick={handleApplySpeakerNames}
                    startIcon={<PersonIcon />}
                  >
                    Применить имена
                  </Button>
                </Box>
              </Box>
            </Card>
          )}
          <Paper variant="outlined" sx={{ p: 2, backgroundColor: 'background.default' }}>
            <Typography
              variant="body1"
              sx={{
                whiteSpace: 'pre-wrap',
                fontFamily: 'monospace',
                lineHeight: 1.6,
                maxHeight: 400,
                overflow: 'auto',
              }}
            >
              {transcriptionResult || 'Результат транскрибации появится здесь...'}
            </Typography>
          </Paper>
          
          <Box sx={{ mt: 2, display: 'flex', gap: 1 }}>
            <Chip
              label={`${(transcriptionResult || '').length} символов`}
              size="small"
              variant="outlined"
            />
            <Chip
              label={`~${Math.ceil((transcriptionResult || '').split(' ').length)} слов`}
              size="small"
              variant="outlined"
            />
          </Box>

          {/* Кнопки действий — в стиле кнопок под сообщениями в чате */}
          <Box
            sx={{
              display: 'flex',
              justifyContent: 'center',
              alignItems: 'center',
              gap: 0.5,
              mt: 2,
              pt: 2,
              borderTop: 1,
              borderColor: 'divider',
              minHeight: 28,
            }}
          >
            {extractSpeakers(transcriptionResult || '').length > 0 && (
              <Tooltip title="Настроить имя спикера">
                <IconButton
                  size="small"
                  onClick={handleOpenSpeakerSettings}
                  sx={{
                    opacity: 0.7,
                    p: 0.5,
                    borderRadius: '6px',
                    minWidth: '28px',
                    width: '28px',
                    height: '28px',
                    '&:hover': {
                      opacity: 1,
                      '& .MuiSvgIcon-root': { color: 'primary.main' },
                    },
                    '& .MuiSvgIcon-root': {
                      fontSize: '18px !important',
                      width: '18px !important',
                      height: '18px !important',
                    },
                  }}
                >
                  <PersonIcon />
                </IconButton>
              </Tooltip>
            )}
            <Tooltip title="Копировать">
              <IconButton
                size="small"
                onClick={handleCopyTranscription}
                sx={{
                  opacity: 0.7,
                  p: 0.5,
                  borderRadius: '6px',
                  minWidth: '28px',
                  width: '28px',
                  height: '28px',
                  '&:hover': {
                    opacity: 1,
                    '& .MuiSvgIcon-root': { color: 'primary.main' },
                  },
                  '& .MuiSvgIcon-root': {
                    fontSize: '18px !important',
                    width: '18px !important',
                    height: '18px !important',
                  },
                }}
              >
                <CopyIcon />
              </IconButton>
            </Tooltip>
            <Tooltip title="Скачать">
              <IconButton
                size="small"
                onClick={handleDownloadTranscription}
                sx={{
                  opacity: 0.7,
                  p: 0.5,
                  borderRadius: '6px',
                  minWidth: '28px',
                  width: '28px',
                  height: '28px',
                  '&:hover': {
                    opacity: 1,
                    '& .MuiSvgIcon-root': { color: 'primary.main' },
                  },
                  '& .MuiSvgIcon-root': {
                    fontSize: '18px !important',
                    width: '18px !important',
                    height: '18px !important',
                  },
                }}
              >
                <DownloadIcon />
              </IconButton>
            </Tooltip>
            {onInsertToChat && (
              <Tooltip title="Вставить в чат">
                <IconButton
                  size="small"
                  onClick={handleInsertToChat}
                  sx={{
                    opacity: 0.7,
                    p: 0.5,
                    borderRadius: '6px',
                    minWidth: '28px',
                    width: '28px',
                    height: '28px',
                    '&:hover': {
                      opacity: 1,
                      '& .MuiSvgIcon-root': { color: 'primary.main' },
                    },
                    '& .MuiSvgIcon-root': {
                      fontSize: '18px !important',
                      width: '18px !important',
                      height: '18px !important',
                    },
                  }}
                >
                  <SendIcon />
                </IconButton>
              </Tooltip>
            )}
            <Tooltip title="Закрыть">
              <IconButton
                size="small"
                onClick={() => setShowResult(false)}
                sx={{
                  opacity: 0.7,
                  p: 0.5,
                  borderRadius: '6px',
                  minWidth: '28px',
                  width: '28px',
                  height: '28px',
                  '&:hover': {
                    opacity: 1,
                    '& .MuiSvgIcon-root': { color: 'primary.main' },
                  },
                  '& .MuiSvgIcon-root': {
                    fontSize: '18px !important',
                    width: '18px !important',
                    height: '18px !important',
                  },
                }}
              >
                <CloseIcon />
              </IconButton>
            </Tooltip>
          </Box>
        </DialogContent>
      </Dialog>
    </>
  );
}

