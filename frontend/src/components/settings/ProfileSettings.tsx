import React, { useRef, useState } from 'react';
import {
  Box,
  Typography,
  TextField,
  Button,
  Alert,
  CircularProgress,
  Paper,
  Divider,
  IconButton,
  Tooltip,
  Popover,
} from '@mui/material';
import {
  Person as PersonIcon,
  Email as EmailIcon,
  Save as SaveIcon,
  PhotoCamera as PhotoCameraIcon,
  DeleteOutline as DeleteIcon,
  ChatBubbleOutline as StatusIcon,
  EmojiEmotionsOutlined as EmojiIcon,
} from '@mui/icons-material';
import { useAuth } from '../../contexts/AuthContext';
import axios from 'axios';
import { getSettings } from '../../settings';
import UserAvatar from '../UserAvatar';

const getApiBaseUrl = () => {
  const settings = getSettings();
  return process.env.REACT_APP_API_URL || settings.api.baseUrl;
};

/** Расширенный набор эмодзи для статуса профиля. */
const STATUS_EMOJI_OPTIONS = [
  // Статус / доступность
  '🟢', '🟡', '🔴', '🔵', '🟣', '🟠', '⚫', '⚪', '🟤',
  '✅', '❌', '⚠️', '⛔', '🚫', '💤', '🔔', '🔕', '⏰', '⌛', '⏳',
  // Работа / учёба
  '💼', '💻', '🖥️', '⌨️', '🖱️', '📱', '📞', '📧', '✉️', '📨',
  '📊', '📈', '📉', '🗂️', '📁', '📂', '📝', '✍️', '📌', '📍',
  '🧠', '💡', '🎯', '🚀', '🛠️', '⚙️', '🔧', '🧪', '🔬', '📚',
  '🎓', '🏫', '📖', '🗓️', '📅', '🗒️',
  // Настроение
  '😀', '😁', '😂', '🤣', '😊', '😇', '🙂', '😉', '😍', '🤩',
  '🤔', '😐', '😑', '😶', '🙄', '😏', '😴', '🥱', '😮', '🤯',
  '😎', '🤓', '🧐', '😢', '😭', '😤', '🤬', '🤗', '🤫', '🤝',
  '👍', '👎', '👏', '🙌', '✌️', '🤞', '💪', '🫡', '🫠', '🫥',
  // Место / активность
  '🏠', '🏢', '🏥', '✈️', '🚗', '🚆', '🚌', '🚲', '🚶', '🏃',
  '🏖️', '🏝️', '⛰️', '🏕️', '🌍', '🗺️', '🧳', '🎒',
  '☕', '🍵', '🧃', '🍕', '🍔', '🥗', '🍜', '🍣', '🍩', '🍪',
  '🎮', '🎲', '🎬', '🎵', '🎧', '🎤', '🎸', '🎹', '🎨', '🖼️',
  '⚽', '🏀', '🎾', '🏊', '🚴', '🏋️', '🧘', '⛷️',
  // Разное
  '❤️', '🧡', '💛', '💚', '💙', '💜', '🖤', '🤍', '💔', '✨',
  '⭐', '🌟', '🔥', '💥', '🎉', '🎊', '🎁', '🏆', '🥇', '🏅',
  '🌙', '☀️', '⛅', '🌧️', '❄️', '🌈', '🍀', '🌸', '🌺', '🌱',
  '🐱', '🐶', '🦄', '🐼', '🦊', '🐝', '🦋', '🐧',
];

export default function ProfileSettings() {
  const { user, updateUser, token, avatarSrc } = useAuth();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [email, setEmail] = useState(user?.email || '');
  const [fullName, setFullName] = useState(user?.full_name || '');
  const [statusText, setStatusText] = useState(user?.status || '');
  const [statusEmoji, setStatusEmoji] = useState(user?.status_emoji || '');
  const [emojiAnchor, setEmojiAnchor] = useState<HTMLElement | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [avatarBusy, setAvatarBusy] = useState(false);
  const [successMessage, setSuccessMessage] = useState('');
  const [errorMessage, setErrorMessage] = useState('');

  if (!user) {
    return (
      <Box sx={{ p: 3, textAlign: 'center' }}>
        <Typography variant="body1" color="text.secondary">
          Необходимо авторизоваться
        </Typography>
      </Box>
    );
  }

  const authHeaders = { Authorization: `Bearer ${token}` };
  const previewStatus = statusText || user.status || '';
  const previewEmoji = statusEmoji || user.status_emoji || '';

  const handleSave = async () => {
    setIsLoading(true);
    setSuccessMessage('');
    setErrorMessage('');

    try {
      const response = await axios.put(
        `${getApiBaseUrl()}/api/auth/me`,
        null,
        {
          headers: authHeaders,
          params: {
            email: email || '',
            full_name: fullName || '',
            status: statusText,
            status_emoji: statusEmoji || '',
          },
        },
      );

      updateUser(response.data);
      setStatusText(response.data?.status || '');
      setStatusEmoji(response.data?.status_emoji || '');
      setSuccessMessage('Профиль успешно обновлен!');
      setTimeout(() => setSuccessMessage(''), 3000);
    } catch (err: unknown) {
      console.error('Failed to update profile:', err);
      if (axios.isAxiosError(err) && err.response) {
        setErrorMessage(err.response.data.detail || 'Ошибка обновления профиля');
      } else {
        setErrorMessage('Произошла непредвиденная ошибка');
      }
    } finally {
      setIsLoading(false);
    }
  };

  const handleAvatarPick = async (file: File | null) => {
    if (!file) return;
    if (!file.type.startsWith('image/')) {
      setErrorMessage('Выберите файл изображения');
      return;
    }
    if (file.size > 5 * 1024 * 1024) {
      setErrorMessage('Размер аватара не должен превышать 5MB');
      return;
    }
    setAvatarBusy(true);
    setErrorMessage('');
    try {
      const form = new FormData();
      form.append('file', file);
      const response = await axios.post(`${getApiBaseUrl()}/api/auth/me/avatar`, form, {
        headers: authHeaders,
      });
      updateUser({
        ...response.data,
        avatar_url: `/api/auth/me/avatar?v=${Date.now()}`,
      });
      setSuccessMessage('Аватар обновлён');
      setTimeout(() => setSuccessMessage(''), 3000);
    } catch (err: unknown) {
      if (axios.isAxiosError(err) && err.response) {
        setErrorMessage(err.response.data.detail || 'Не удалось загрузить аватар');
      } else {
        setErrorMessage('Не удалось загрузить аватар');
      }
    } finally {
      setAvatarBusy(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const handleAvatarDelete = async () => {
    setAvatarBusy(true);
    setErrorMessage('');
    try {
      const response = await axios.delete(`${getApiBaseUrl()}/api/auth/me/avatar`, {
        headers: authHeaders,
      });
      updateUser({
        ...response.data,
        avatar_url: response.data?.avatar_url
          ? `${response.data.avatar_url}?v=${Date.now()}`
          : null,
      });
      setSuccessMessage('Свой аватар удалён');
      setTimeout(() => setSuccessMessage(''), 3000);
    } catch (err: unknown) {
      if (axios.isAxiosError(err) && err.response) {
        setErrorMessage(err.response.data.detail || 'Не удалось удалить аватар');
      } else {
        setErrorMessage('Не удалось удалить аватар');
      }
    } finally {
      setAvatarBusy(false);
    }
  };

  const pickEmoji = (emoji: string) => {
    setStatusEmoji((prev) => (prev === emoji ? '' : emoji));
    setEmojiAnchor(null);
  };

  return (
    <Box sx={{ p: 3 }}>
      {successMessage && (
        <Alert severity="success" sx={{ mb: 3 }} onClose={() => setSuccessMessage('')}>
          {successMessage}
        </Alert>
      )}

      {errorMessage && (
        <Alert severity="error" sx={{ mb: 3 }} onClose={() => setErrorMessage('')}>
          {errorMessage}
        </Alert>
      )}

      <Paper
        elevation={0}
        sx={{
          p: 3,
          mb: 3,
          backgroundColor: 'action.hover',
          borderRadius: 2,
        }}
      >
        <Box
          sx={{
            display: 'flex',
            alignItems: 'center',
            mb: 3,
            gap: 2,
          }}
        >
          <Box sx={{ position: 'relative' }}>
            <UserAvatar
              sx={{
                width: 72,
                height: 72,
                bgcolor: 'primary.main',
                fontSize: 28,
              }}
            />
            <input
              ref={fileInputRef}
              type="file"
              accept="image/jpeg,image/png,image/webp,image/gif"
              hidden
              onChange={(e) => void handleAvatarPick(e.target.files?.[0] || null)}
            />
            <Tooltip title="Загрузить фото">
              <IconButton
                size="small"
                disabled={avatarBusy}
                onClick={() => fileInputRef.current?.click()}
                sx={{
                  position: 'absolute',
                  right: -4,
                  bottom: -4,
                  bgcolor: 'background.paper',
                  border: '1px solid',
                  borderColor: 'divider',
                  '&:hover': { bgcolor: 'action.hover' },
                }}
              >
                {avatarBusy ? <CircularProgress size={16} /> : <PhotoCameraIcon fontSize="small" />}
              </IconButton>
            </Tooltip>
          </Box>
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography variant="h6" fontWeight="bold">
              {user.full_name || user.username}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              @{user.username}
            </Typography>
            {(previewEmoji || previewStatus) && (
              <Typography variant="body2" sx={{ mt: 0.5, opacity: 0.85 }}>
                {previewEmoji ? `${previewEmoji} ` : ''}
                {previewStatus}
              </Typography>
            )}
            {user.is_admin && (
              <Typography
                variant="caption"
                sx={{
                  bgcolor: 'primary.main',
                  color: 'white',
                  px: 1,
                  py: 0.5,
                  borderRadius: 1,
                  display: 'inline-block',
                  mt: 0.5,
                }}
              >
                Администратор
              </Typography>
            )}
            {avatarSrc ? (
              <Box sx={{ mt: 1 }}>
                <Button
                  size="small"
                  color="inherit"
                  startIcon={<DeleteIcon />}
                  disabled={avatarBusy}
                  onClick={() => void handleAvatarDelete()}
                >
                  Удалить свой аватар
                </Button>
              </Box>
            ) : null}
          </Box>
        </Box>

        <Divider sx={{ mb: 3 }} />

        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
            <PersonIcon sx={{ color: 'text.secondary' }} />
            <TextField
              fullWidth
              label="Полное имя"
              variant="outlined"
              size="small"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              disabled={isLoading}
            />
          </Box>

          <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
            <EmailIcon sx={{ color: 'text.secondary' }} />
            <TextField
              fullWidth
              label="Email"
              type="email"
              variant="outlined"
              size="small"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              disabled={isLoading}
            />
          </Box>

          <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 2 }}>
            <StatusIcon sx={{ color: 'text.secondary', mt: 1.25 }} />
            <Box sx={{ flex: 1, display: 'flex', gap: 1, alignItems: 'flex-start' }}>
              <Tooltip title={statusEmoji ? 'Сменить эмодзи статуса' : 'Добавить эмодзи к статусу'}>
                <IconButton
                  size="small"
                  disabled={isLoading}
                  onClick={(e) => setEmojiAnchor(e.currentTarget)}
                  sx={{
                    // Как у MUI TextField size="small" outlined (~40px), без лишнего padding
                    mt: '1px',
                    width: 40,
                    height: 40,
                    minWidth: 40,
                    minHeight: 40,
                    maxHeight: 40,
                    p: 0,
                    boxSizing: 'border-box',
                    border: '1px solid',
                    borderColor: 'divider',
                    borderRadius: '4px',
                    fontSize: '1.15rem',
                    lineHeight: 1,
                    flexShrink: 0,
                    '& .MuiSvgIcon-root': { fontSize: '1.15rem' },
                  }}
                  aria-label="Выбрать эмодзи статуса"
                >
                  {statusEmoji || <EmojiIcon fontSize="small" />}
                </IconButton>
              </Tooltip>
              <TextField
                fullWidth
                label="Статус"
                variant="outlined"
                size="small"
                value={statusText}
                onChange={(e) => setStatusText(e.target.value.slice(0, 160))}
                disabled={isLoading}
                helperText={`${statusText.length}/160 — текст статуса; эмодзи выбирается кнопкой слева`}
                placeholder="Например: на связи / в фокусе / оффлайн"
                sx={{
                  '& .MuiOutlinedInput-root': {
                    height: 40,
                    boxSizing: 'border-box',
                  },
                }}
              />
            </Box>
          </Box>

          <Popover
            open={Boolean(emojiAnchor)}
            anchorEl={emojiAnchor}
            onClose={() => setEmojiAnchor(null)}
            anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
            transformOrigin={{ vertical: 'top', horizontal: 'left' }}
            slotProps={{
              paper: {
                sx: { maxWidth: 360 },
              },
            }}
          >
            <Box sx={{ p: 1.5, width: 340 }}>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 1 }}>
                <Typography variant="caption" color="text.secondary">
                  Эмодзи статуса ({STATUS_EMOJI_OPTIONS.length})
                </Typography>
                {statusEmoji ? (
                  <Button size="small" onClick={() => { setStatusEmoji(''); setEmojiAnchor(null); }}>
                    Убрать
                  </Button>
                ) : null}
              </Box>
              <Box
                sx={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(8, 1fr)',
                  gap: 0.25,
                  maxHeight: 280,
                  overflowY: 'auto',
                  pr: 0.5,
                }}
              >
                {STATUS_EMOJI_OPTIONS.map((emoji, idx) => (
                  <IconButton
                    key={`${emoji}-${idx}`}
                    size="small"
                    onClick={() => pickEmoji(emoji)}
                    sx={{
                      fontSize: '1.2rem',
                      borderRadius: 1,
                      width: 36,
                      height: 36,
                      bgcolor: statusEmoji === emoji ? 'action.selected' : 'transparent',
                    }}
                  >
                    {emoji}
                  </IconButton>
                ))}
              </Box>
            </Box>
          </Popover>

          <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
            <PersonIcon sx={{ color: 'text.secondary' }} />
            <TextField
              fullWidth
              label="Имя пользователя"
              variant="outlined"
              size="small"
              value={user.username}
              disabled
              helperText="Имя пользователя не может быть изменено"
            />
          </Box>
        </Box>

        <Box sx={{ mt: 3, display: 'flex', gap: 2 }}>
          <Button
            variant="contained"
            startIcon={isLoading ? <CircularProgress size={20} /> : <SaveIcon />}
            onClick={() => void handleSave()}
            disabled={isLoading || avatarBusy}
          >
            Сохранить изменения
          </Button>
        </Box>
      </Paper>
    </Box>
  );
}
