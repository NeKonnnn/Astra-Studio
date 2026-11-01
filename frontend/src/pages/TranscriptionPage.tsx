import React, { useState, useRef } from 'react';
import {
  Box,
  Paper,
  Typography,
  Container,
  Card,
  CardContent,
  Button,
  TextField,
  Tabs,
  Tab,
  LinearProgress,
  Alert,
  IconButton,
  Chip,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
} from '@mui/material';
import {
  Upload as UploadIcon,
  YouTube as YouTubeIcon,
  PlayArrow as PlayIcon,
  Download as DownloadIcon,
  ContentCopy as CopyIcon,
  Videocam as VideoIcon,
  AudioFile as AudioIcon,
} from '@mui/icons-material';
import { useAppActions } from '../contexts/AppContext';
import { authFetch, getApiUrl } from '../config/api';

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

export default function TranscriptionPage() {
  const [tabValue, setTabValue] = useState(0);
  const [isDragging, setIsDragging] = useState(false);
  const [isTranscribing, setIsTranscribing] = useState(false);
  const [youtubeUrl, setYoutubeUrl] = useState('');
  const [transcriptionResult, setTranscriptionResult] = useState('');
  const [uploadedFile, setUploadedFile] = useState<File | null>(null);
  const [showResult, setShowResult] = useState(false);
  
  const { showNotification } = useAppActions();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleTabChange = (event: React.SyntheticEvent, newValue: number) => {
    setTabValue(newValue);
    setTranscriptionResult('');
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

    // Проверка размера файла (макс 500MB)
    if (file.size > 500 * 1024 * 1024) {
      showNotification('error', 'Размер файла не должен превышать 500MB');
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

    setIsTranscribing(true);
    
    try {
      const formData = new FormData();
      formData.append('file', uploadedFile);

      const response = await authFetch(getApiUrl('/api/transcribe/upload'), {
        method: 'POST',
        body: formData,
      });

      const result = await response.json();

             if (result.success) {
         // Бэкенд теперь всегда возвращает отформатированную строку
         setTranscriptionResult(result.transcription);
         setShowResult(true);
         showNotification('success', 'Транскрибация завершена');
       } else {
        showNotification('error', result.message || 'Ошибка при транскрибации');
      }
    } catch (error) {
      console.error('Ошибка транскрибации:', error);
      showNotification('error', 'Ошибка при отправке файла');
    } finally {
      setIsTranscribing(false);
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

    setIsTranscribing(true);
    
    try {
      const response = await authFetch(getApiUrl('/api/transcribe/youtube'), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ url: youtubeUrl }),
      });

      const result = await response.json();

             if (result.success) {
         // Бэкенд теперь всегда возвращает отформатированную строку
         setTranscriptionResult(result.transcription);
         setShowResult(true);
         showNotification('success', 'Транскрибация YouTube видео завершена');
       } else {
        showNotification('error', result.message || 'Ошибка при транскрибации YouTube');
      }
    } catch (error) {
      console.error('Ошибка YouTube транскрибации:', error);
      showNotification('error', 'Ошибка при обработке YouTube URL');
    } finally {
      setIsTranscribing(false);
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

  return (
    <Box sx={{ height: '100vh', display: 'flex', flexDirection: 'column' }}>
      {/* Заголовок */}
      <Paper elevation={2} sx={{ p: 2, borderRadius: 0 }}>
        <Container maxWidth="lg">
          <Typography variant="h5" fontWeight="600">
            Транскрибация
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Преобразование аудио и видео в текст
          </Typography>
        </Container>
      </Paper>

      <Container maxWidth="lg" sx={{ flexGrow: 1, py: 3 }}>
        {/* Вкладки */}
        <Paper sx={{ mb: 3 }}>
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
        </Paper>

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
                    Максимальный размер: 500MB
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
              <Box sx={{ textAlign: 'center' }}>
                <LinearProgress sx={{ mb: 2 }} />
                <Typography variant="h6" gutterBottom>
                  Выполняется транскрибация...
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  Это может занять несколько минут в зависимости от размера файла
                </Typography>
              </Box>
            </CardContent>
          </Card>
        )}
      </Container>

      {/* Диалог с результатом */}
      <Dialog
        open={showResult}
        onClose={() => setShowResult(false)}
        maxWidth="md"
        fullWidth
        scroll="paper"
        TransitionComponent={undefined}
        transitionDuration={0}
      >
        <DialogTitle>
          <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <Typography variant="h6">Результат транскрибации</Typography>
            <Box>
              <IconButton onClick={handleCopyTranscription} title="Копировать">
                <CopyIcon />
              </IconButton>
              <IconButton onClick={handleDownloadTranscription} title="Скачать">
                <DownloadIcon />
              </IconButton>
            </Box>
          </Box>
        </DialogTitle>
        <DialogContent>
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
        </DialogContent>
        <DialogActions>
          <Button onClick={handleCopyTranscription} startIcon={<CopyIcon />}>
            Копировать
          </Button>
          <Button onClick={handleDownloadTranscription} startIcon={<DownloadIcon />}>
            Скачать
          </Button>
          <Button onClick={() => setShowResult(false)} variant="contained">
            Закрыть
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
