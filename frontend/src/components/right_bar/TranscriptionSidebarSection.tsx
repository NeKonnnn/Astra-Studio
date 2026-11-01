import React, { useRef, useState } from 'react';
import {
  Box,
  Button,
  CircularProgress,
  TextField,
  Typography,
} from '@mui/material';
import {
  Stop as SquareIcon,
  Upload as UploadIcon,
  YouTube as YouTubeIcon,
} from '@mui/icons-material';
import { useNavigate } from 'react-router-dom';
import { authFetch, getApiUrl, API_ENDPOINTS } from '../../config/api';
import { useAppActions } from '../../contexts/AppContext';
import { incrementTabNotification } from '../../utils/tabNotifications';
import TranscriptionResultModal from '../TranscriptionResultModal';

/** Вставка текста транскрибации в поле ввода чата (слушают UnifiedChatPage / ProjectPage). */
export const ASTRA_INSERT_CHAT_TEXT = 'astrachat:insert-chat-text';
export const ASTRA_INSERT_CHAT_TEXT_KEY = 'astrachat_insert_chat_text';

export function dispatchInsertChatText(text: string) {
  try {
    sessionStorage.setItem(ASTRA_INSERT_CHAT_TEXT_KEY, text);
  } catch {
    /* ignore */
  }
  window.dispatchEvent(new CustomEvent(ASTRA_INSERT_CHAT_TEXT, { detail: { text } }));
}

interface TranscriptionSidebarSectionProps {
  open: boolean;
}

/** Меню транскрибации под кнопкой в правой панели + модалка результата. */
export default function TranscriptionSidebarSection({ open }: TranscriptionSidebarSectionProps) {
  const { showNotification } = useAppActions();
  const navigate = useNavigate();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [isTranscribing, setIsTranscribing] = useState(false);
  const [transcriptionResult, setTranscriptionResult] = useState('');
  const [transcriptionYoutubeUrl, setTranscriptionYoutubeUrl] = useState('');
  const [transcriptionId, setTranscriptionId] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files?.length) return;
    const file = files[0];
    const allowedTypes = [
      'audio/mpeg', 'audio/wav', 'audio/m4a', 'audio/aac', 'audio/flac',
      'video/mp4', 'video/avi', 'video/mov', 'video/mkv', 'video/webm',
    ];
    const isValidType = allowedTypes.some(
      (type) =>
        file.type.includes(type.split('/')[1]) ||
        file.name.toLowerCase().includes(type.split('/')[1]),
    );
    if (!isValidType) {
      showNotification('error', 'Поддерживаются только аудио и видео файлы');
      e.target.value = '';
      return;
    }
    if (file.size > 5 * 1024 * 1024 * 1024) {
      showNotification('error', 'Размер файла не должен превышать 5GB');
      e.target.value = '';
      return;
    }
    e.target.value = '';
    void startFileTranscription(file);
  };

  const startFileTranscription = async (file: File) => {
    setIsTranscribing(true);
    const currentId = `transcribe_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
    setTranscriptionId(currentId);
    try {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('request_id', currentId);
      const response = await authFetch(getApiUrl(API_ENDPOINTS.TRANSCRIBE_UPLOAD), {
        method: 'POST',
        body: formData,
      });
      if (!response.ok) {
        if (response.status === 499) {
          const errorData = await response.json().catch(() => ({ detail: 'Транскрибация была остановлена' }));
          throw Object.assign(new Error(errorData.detail || 'Транскрибация была остановлена'), {
            status: 499,
          });
        }
        const errorData = await response.json().catch(() => ({ detail: 'Ошибка при транскрибации' }));
        throw new Error(errorData.detail || 'Ошибка при транскрибации');
      }
      const result = await response.json();
      if (result.success) {
        if (result.transcription_id) setTranscriptionId(result.transcription_id);
        setTranscriptionResult(result.transcription ?? '');
        showNotification('success', 'Транскрибация завершена');
        incrementTabNotification();
      } else {
        showNotification('error', result.message || 'Ошибка при транскрибации');
      }
    } catch (err: unknown) {
      const e = err as { status?: number; message?: string };
      if (e?.status === 499 || e?.message?.includes('остановлена')) {
        showNotification('info', 'Транскрибация была остановлена');
      } else {
        showNotification('error', e?.message || 'Ошибка при отправке файла');
      }
    } finally {
      setIsTranscribing(false);
      setTranscriptionId(null);
    }
  };

  const handleStop = async () => {
    if (!transcriptionId) return;
    try {
      const response = await authFetch(getApiUrl('/api/transcribe/stop'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ transcription_id: transcriptionId }),
      });
      const result = await response.json();
      if (result.success) {
        showNotification('info', 'Транскрибация остановлена');
      } else {
        showNotification('error', result.message || 'Ошибка остановки');
      }
    } catch {
      showNotification('error', 'Ошибка при остановке транскрибации');
    }
    setTranscriptionId(null);
    setIsTranscribing(false);
  };

  const startYouTube = async () => {
    const url = transcriptionYoutubeUrl.trim();
    if (!url) {
      showNotification('warning', 'Введите URL YouTube видео');
      return;
    }
    if (!url.includes('youtube.com') && !url.includes('youtu.be')) {
      showNotification('error', 'Некорректный URL YouTube');
      return;
    }
    setIsTranscribing(true);
    try {
      const response = await authFetch(getApiUrl(API_ENDPOINTS.TRANSCRIBE_YOUTUBE), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url }),
      });
      const result = await response.json();
      if (result.success) {
        setTranscriptionResult(result.transcription ?? '');
        showNotification('success', 'Транскрибация YouTube завершена');
        incrementTabNotification();
      } else {
        showNotification('error', result.message || 'Ошибка при транскрибации YouTube');
      }
    } catch {
      showNotification('error', 'Ошибка при обработке YouTube URL');
    } finally {
      setIsTranscribing(false);
    }
  };

  const insertToChat = (text: string) => {
    dispatchInsertChatText(text);
    const path = window.location.pathname;
    if (
      path !== '/' &&
      !path.startsWith('/project') &&
      path !== '/voice' &&
      path !== '/documents' &&
      path !== '/search'
    ) {
      navigate('/');
    }
  };

  return (
    <>
      {open && (
        <Box
          sx={{
            borderTop: '1px solid rgba(255,255,255,0.08)',
            p: 1.5,
            display: 'flex',
            flexDirection: 'column',
            gap: 1,
          }}
        >
          <input
            ref={fileInputRef}
            type="file"
            accept="audio/*,video/*"
            hidden
            onChange={handleFileSelect}
          />
          {isTranscribing && (
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.75 }}>
              <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.8)', fontSize: '0.78rem' }}>
                Транскрибация идёт...
              </Typography>
              <Box sx={{ display: 'flex', gap: 0.5, alignItems: 'center' }}>
                <CircularProgress size={16} sx={{ color: 'primary.main' }} />
                <Button
                  size="small"
                  startIcon={<SquareIcon sx={{ fontSize: '0.75rem' }} />}
                  onClick={() => void handleStop()}
                  disabled={!transcriptionId}
                  sx={{
                    fontSize: '0.7rem',
                    textTransform: 'none',
                    color: 'rgba(255,255,255,0.7)',
                    py: 0.5,
                    minWidth: 0,
                    '&:hover': { bgcolor: 'rgba(255,255,255,0.08)' },
                  }}
                >
                  Остановить
                </Button>
              </Box>
            </Box>
          )}
          {transcriptionResult && !isTranscribing && (
            <Button
              size="small"
              fullWidth
              variant="outlined"
              onClick={() => setModalOpen(true)}
              sx={{
                fontSize: '0.78rem',
                textTransform: 'none',
                color: 'primary.main',
                borderColor: 'primary.main',
                py: 0.75,
                '&:hover': { borderColor: 'primary.light', bgcolor: 'rgba(33,150,243,0.08)' },
              }}
            >
              Посмотреть результат
            </Button>
          )}
          <Typography
            variant="caption"
            sx={{ color: 'rgba(255,255,255,0.6)', fontSize: '0.7rem', display: 'block', lineHeight: 1.35 }}
          >
            Форматы: MP3, WAV, M4A, AAC, FLAC, MP4, AVI, MOV, MKV, WebM
            <br />
            Максимальный размер: 5GB
          </Typography>
          <Button
            size="small"
            fullWidth
            startIcon={<UploadIcon sx={{ fontSize: '0.85rem !important' }} />}
            onClick={() => fileInputRef.current?.click()}
            disabled={isTranscribing}
            sx={{
              fontSize: '0.72rem',
              textTransform: 'none',
              color: 'rgba(255,255,255,0.6)',
              border: '1px dashed rgba(255,255,255,0.2)',
              py: 0.75,
              justifyContent: 'flex-start',
              '&:hover': { bgcolor: 'rgba(255,255,255,0.06)', borderColor: 'rgba(255,255,255,0.35)' },
              '&:disabled': { color: 'rgba(255,255,255,0.35)', borderColor: 'rgba(255,255,255,0.1)' },
            }}
          >
            Загрузить файл
          </Button>
          <Typography
            variant="caption"
            sx={{ color: 'rgba(255,255,255,0.6)', fontSize: '0.7rem', display: 'block', mt: 0.5 }}
          >
            Вставить ссылку на ютуб
          </Typography>
          <TextField
            size="small"
            fullWidth
            placeholder="https://www.youtube.com/watch?v=..."
            value={transcriptionYoutubeUrl}
            onChange={(e) => setTranscriptionYoutubeUrl(e.target.value)}
            disabled={isTranscribing}
            sx={{
              '& .MuiOutlinedInput-root': {
                fontSize: '0.78rem',
                bgcolor: 'rgba(255,255,255,0.06)',
                color: 'rgba(255,255,255,0.9)',
                borderColor: 'rgba(255,255,255,0.2)',
                '&:hover .MuiOutlinedInput-notchedOutline': { borderColor: 'rgba(255,255,255,0.35)' },
                '&.Mui-focused .MuiOutlinedInput-notchedOutline': { borderColor: 'primary.main' },
              },
              '& .MuiInputBase-input::placeholder': { color: 'rgba(255,255,255,0.4)', opacity: 1 },
            }}
          />
          <Button
            size="small"
            fullWidth
            startIcon={<YouTubeIcon sx={{ fontSize: '0.85rem !important' }} />}
            onClick={() => void startYouTube()}
            disabled={!transcriptionYoutubeUrl.trim() || isTranscribing}
            sx={{
              fontSize: '0.72rem',
              textTransform: 'none',
              color: 'rgba(255,255,255,0.9)',
              bgcolor: 'rgba(255,255,255,0.08)',
              py: 0.65,
              '&:hover': { bgcolor: 'rgba(255,255,255,0.12)' },
              '&:disabled': { color: 'rgba(255,255,255,0.4)' },
            }}
          >
            Транскрибировать
          </Button>
        </Box>
      )}
      <TranscriptionResultModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        transcriptionResult={transcriptionResult}
        onResultChange={(text) => setTranscriptionResult(text)}
        onInsertToChat={insertToChat}
      />
    </>
  );
}
