import React, { useState, useEffect } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  Box,
  Typography,
  Paper,
  IconButton,
  Chip,
  Tooltip,
  Card,
  Alert,
  TextField,
  Button,
} from '@mui/material';
import {
  Close as CloseIcon,
  ContentCopy as CopyIcon,
  Download as DownloadIcon,
  Send as SendIcon,
  Person as PersonIcon,
  Settings as SettingsIcon,
  Add as AddIcon,
  Delete as DeleteIcon,
} from '@mui/icons-material';
import { useAppActions } from '../contexts/AppContext';
import { applySpeakerMapping, extractSpeakers } from '../utils/speakerNames';

const iconButtonSx = {
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
};

interface TranscriptionResultModalProps {
  open: boolean;
  onClose: () => void;
  transcriptionResult: string;
  onInsertToChat?: (text: string) => void;
  /** Вызов при применении имён спикеров — родитель может обновить результат. */
  onResultChange?: (text: string) => void;
}

export default function TranscriptionResultModal({
  open,
  onClose,
  transcriptionResult,
  onInsertToChat,
  onResultChange,
}: TranscriptionResultModalProps) {
  const { showNotification } = useAppActions();
  const [showSpeakerSettings, setShowSpeakerSettings] = useState(false);
  const [speakerNames, setSpeakerNames] = useState<Record<string, string>>({});
  const [newSpeakerId, setNewSpeakerId] = useState('');
  const [originalResult, setOriginalResult] = useState('');

  useEffect(() => {
    if (open && transcriptionResult && !originalResult) {
      setOriginalResult(transcriptionResult);
    }
    if (!open) {
      setOriginalResult('');
      setShowSpeakerSettings(false);
    }
  }, [open, transcriptionResult, originalResult]);

  const handleOpenSpeakerSettings = () => {
    const sourceText = originalResult || transcriptionResult || '';
    const speakers = extractSpeakers(sourceText);
    const currentMapping = { ...speakerNames };
    speakers.forEach(speaker => {
      if (!(speaker in currentMapping)) currentMapping[speaker] = '';
    });
    setSpeakerNames(currentMapping);
    setShowSpeakerSettings(true);
  };

  const handleApplySpeakerNames = () => {
    const sourceText = originalResult || transcriptionResult || '';
    if (!sourceText) return;
    const mappedText = applySpeakerMapping(sourceText, speakerNames);
    onResultChange?.(mappedText);
    showNotification('success', 'Имена спикеров применены');
  };

  const handleAddNewSpeaker = () => {
    if (!newSpeakerId.trim()) {
      showNotification('warning', 'Введите идентификатор спикера');
      return;
    }
    if (newSpeakerId in speakerNames) {
      showNotification('warning', 'Такой спикер уже существует');
      return;
    }
    setSpeakerNames(prev => ({ ...prev, [newSpeakerId]: '' }));
    setNewSpeakerId('');
    showNotification('success', 'Спикер добавлен');
  };

  const handleRemoveSpeaker = (speakerId: string) => {
    const next = { ...speakerNames };
    delete next[speakerId];
    setSpeakerNames(next);
    showNotification('info', 'Спикер удален из списка');
  };

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(transcriptionResult || '');
      showNotification('success', 'Транскрипция скопирована в буфер обмена');
    } catch {
      showNotification('error', 'Не удалось скопировать текст');
    }
  };

  const handleDownload = () => {
    const text = transcriptionResult || '';
    const blob = new Blob([text], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `transcription_${new Date().toISOString().split('T')[0]}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleInsertToChat = () => {
    if (transcriptionResult && onInsertToChat) {
      onInsertToChat(transcriptionResult);
      showNotification('success', 'Текст вставлен в поле ввода чата');
      onClose();
    } else if (!onInsertToChat) {
      showNotification('warning', 'Функция вставки в чат недоступна');
    }
  };

  const speakers = extractSpeakers(transcriptionResult || '');

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth scroll="paper">
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
              <IconButton size="small" onClick={() => setShowSpeakerSettings(false)}>
                <CloseIcon />
              </IconButton>
            </Box>
            <Alert severity="info" sx={{ mb: 2 }}>
              Введите имена для каждого спикера. Оставьте поле пустым, чтобы оставить оригинальное имя.
              Вы можете добавлять новых спикеров вручную, если они не были автоматически обнаружены.
            </Alert>
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
                  onKeyPress={(e) => { if (e.key === 'Enter') handleAddNewSpeaker(); }}
                />
                <Button variant="outlined" startIcon={<AddIcon />} onClick={handleAddNewSpeaker} disabled={!newSpeakerId.trim()}>
                  Добавить
                </Button>
              </Box>
            </Box>
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, mb: 2, maxHeight: 400, overflow: 'auto' }}>
              {Object.keys(speakerNames).length === 0 ? (
                <Alert severity="info">Спикеры не найдены. Добавьте их вручную, используя форму выше.</Alert>
              ) : (
                Object.keys(speakerNames).map((speaker) => (
                  <Box key={speaker} sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
                    <Typography variant="body2" sx={{ minWidth: 150, fontWeight: 'bold' }}>{speaker}:</Typography>
                    <TextField
                      fullWidth
                      size="small"
                      placeholder={`Введите имя для ${speaker}`}
                      value={speakerNames[speaker] || ''}
                      onChange={(e) => setSpeakerNames(prev => ({ ...prev, [speaker]: e.target.value }))}
                      variant="outlined"
                    />
                    <IconButton size="small" onClick={() => handleRemoveSpeaker(speaker)} color="error" title="Удалить спикера">
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
                <Button variant="outlined" onClick={() => setShowSpeakerSettings(false)}>Закрыть</Button>
                <Button variant="contained" onClick={handleApplySpeakerNames} startIcon={<PersonIcon />}>
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
          <Chip label={`${(transcriptionResult || '').length} символов`} size="small" variant="outlined" />
          <Chip label={`~${Math.ceil((transcriptionResult || '').split(/\s+/).filter(Boolean).length)} слов`} size="small" variant="outlined" />
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
          {speakers.length > 0 && (
            <Tooltip title="Настроить имя спикера">
              <IconButton size="small" onClick={handleOpenSpeakerSettings} sx={iconButtonSx}>
                <PersonIcon />
              </IconButton>
            </Tooltip>
          )}
          <Tooltip title="Копировать">
            <IconButton size="small" onClick={handleCopy} sx={iconButtonSx}>
              <CopyIcon />
            </IconButton>
          </Tooltip>
          <Tooltip title="Скачать">
            <IconButton size="small" onClick={handleDownload} sx={iconButtonSx}>
              <DownloadIcon />
            </IconButton>
          </Tooltip>
          {onInsertToChat && (
            <Tooltip title="Вставить в чат">
              <IconButton size="small" onClick={handleInsertToChat} sx={iconButtonSx}>
                <SendIcon />
              </IconButton>
            </Tooltip>
          )}
          <Tooltip title="Закрыть">
            <IconButton size="small" onClick={onClose} sx={iconButtonSx}>
              <CloseIcon />
            </IconButton>
          </Tooltip>
        </Box>
      </DialogContent>
    </Dialog>
  );
}
