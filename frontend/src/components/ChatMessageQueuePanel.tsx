import React, { useEffect, useRef, useState } from 'react';
import {
  Box,
  IconButton,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import {
  Close as CloseIcon,
  DeleteOutline as ClearAllIcon,
  EditOutlined as EditIcon,
} from '@mui/icons-material';
import type { QueuedChatMessage } from '../hooks/useChatMessageQueue';

export type ChatMessageQueuePanelProps = {
  items: QueuedChatMessage[];
  isDarkMode?: boolean;
  maxWidth?: string | number;
  widescreen?: boolean;
  onUpdateText: (itemId: string, text: string) => void;
  onRemove: (itemId: string) => void;
  onClearAll: () => void;
};

function QueueRow({
  item,
  index,
  isDarkMode,
  onUpdateText,
  onRemove,
}: {
  item: QueuedChatMessage;
  index: number;
  isDarkMode?: boolean;
  onUpdateText: (itemId: string, text: string) => void;
  onRemove: (itemId: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(item.text);
  const inputRef = useRef<HTMLInputElement | HTMLTextAreaElement | null>(null);

  useEffect(() => {
    if (!editing) setDraft(item.text);
  }, [item.text, editing]);

  useEffect(() => {
    if (editing) {
      const t = window.setTimeout(() => {
        inputRef.current?.focus();
        inputRef.current?.select?.();
      }, 0);
      return () => window.clearTimeout(t);
    }
    return undefined;
  }, [editing]);

  const commit = () => {
    const next = draft.trim();
    if (!next) {
      setDraft(item.text);
      setEditing(false);
      return;
    }
    if (next !== item.text) onUpdateText(item.id, next);
    setEditing(false);
  };

  const border = isDarkMode ? 'rgba(255,255,255,0.12)' : 'rgba(0,0,0,0.1)';
  const surface = isDarkMode ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.04)';
  const hover = isDarkMode ? 'rgba(255,255,255,0.1)' : 'rgba(0,0,0,0.07)';
  const muted = isDarkMode ? 'rgba(255,255,255,0.55)' : 'rgba(0,0,0,0.5)';
  const textColor = isDarkMode ? 'rgba(255,255,255,0.92)' : 'rgba(0,0,0,0.87)';

  return (
    <Box
      data-testid={`chat-queue-item-${item.id}`}
      sx={{
        display: 'flex',
        alignItems: editing ? 'flex-start' : 'center',
        gap: 1,
        px: 1.25,
        py: editing ? 1 : 0.75,
        borderRadius: '12px',
        border: `1px solid ${border}`,
        bgcolor: surface,
        transition: 'background-color 120ms ease',
        '&:hover': { bgcolor: hover },
      }}
    >
      <Typography
        component="span"
        sx={{
          flexShrink: 0,
          minWidth: 22,
          height: 22,
          borderRadius: '7px',
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontSize: '0.7rem',
          fontWeight: 600,
          color: muted,
          bgcolor: isDarkMode ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.06)',
          mt: editing ? 0.35 : 0,
        }}
      >
        {index + 1}
      </Typography>

      <Box sx={{ flex: 1, minWidth: 0 }}>
        {editing ? (
          <TextField
            inputRef={inputRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={commit}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                commit();
              }
              if (e.key === 'Escape') {
                e.preventDefault();
                setDraft(item.text);
                setEditing(false);
              }
            }}
            multiline
            minRows={1}
            maxRows={4}
            fullWidth
            size="small"
            placeholder="Текст сообщения в очереди"
            sx={{
              '& .MuiOutlinedInput-root': {
                color: textColor,
                fontSize: '0.875rem',
                bgcolor: isDarkMode ? 'rgba(0,0,0,0.25)' : 'rgba(255,255,255,0.9)',
                '& fieldset': { borderColor: border },
                '&:hover fieldset': { borderColor: isDarkMode ? 'rgba(255,255,255,0.28)' : 'rgba(0,0,0,0.22)' },
                '&.Mui-focused fieldset': { borderColor: 'primary.main' },
              },
            }}
          />
        ) : (
          <Typography
            data-testid={`chat-queue-text-${item.id}`}
            onClick={() => setEditing(true)}
            sx={{
              color: textColor,
              fontSize: '0.875rem',
              lineHeight: 1.35,
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              cursor: 'text',
              userSelect: 'none',
            }}
            title="Нажмите, чтобы редактировать"
          >
            {item.text.trim() || (item.inlineAttachments.length ? 'Вложение' : 'Пустое сообщение')}
          </Typography>
        )}
        {!editing && item.inlineAttachments.length > 0 ? (
          <Typography sx={{ color: muted, fontSize: '0.72rem', mt: 0.25 }}>
            +{item.inlineAttachments.length}{' '}
            {item.inlineAttachments.length === 1 ? 'вложение' : 'влож.'}
          </Typography>
        ) : null}
      </Box>

      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.25, flexShrink: 0, mt: editing ? 0.15 : 0 }}>
        {!editing ? (
          <Tooltip title="Редактировать">
            <IconButton
              size="small"
              aria-label="Редактировать сообщение в очереди"
              onClick={() => setEditing(true)}
              sx={{ color: muted, '&:hover': { color: textColor } }}
            >
              <EditIcon sx={{ fontSize: 16 }} />
            </IconButton>
          </Tooltip>
        ) : null}
        <Tooltip title="Удалить из очереди">
          <IconButton
            size="small"
            aria-label="Удалить из очереди"
            data-testid={`chat-queue-remove-${item.id}`}
            onClick={() => onRemove(item.id)}
            sx={{ color: muted, '&:hover': { color: 'error.main' } }}
          >
            <CloseIcon sx={{ fontSize: 16 }} />
          </IconButton>
        </Tooltip>
      </Box>
    </Box>
  );
}

/**
 * Панель очереди над composer: список с редактированием и удалением.
 */
export default function ChatMessageQueuePanel({
  items,
  isDarkMode,
  maxWidth = '100%',
  widescreen,
  onUpdateText,
  onRemove,
  onClearAll,
}: ChatMessageQueuePanelProps) {
  if (!items.length) return null;

  const muted = isDarkMode ? 'rgba(255,255,255,0.55)' : 'rgba(0,0,0,0.5)';

  return (
    <Box
      data-testid="chat-message-queue"
      sx={{
        display: 'flex',
        flexDirection: 'column',
        gap: 0.75,
        maxWidth,
        width: '100%',
        mx: 'auto',
        mb: 1,
        px: widescreen ? 4 : 2,
        boxSizing: 'border-box',
      }}
    >
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 1,
          px: 0.25,
        }}
      >
        <Typography sx={{ color: muted, fontSize: '0.75rem', fontWeight: 600, letterSpacing: 0.2 }}>
          В очереди · {items.length}
        </Typography>
        {items.length > 1 ? (
          <Tooltip title="Очистить очередь">
            <IconButton
              size="small"
              aria-label="Очистить очередь"
              data-testid="chat-queue-clear"
              onClick={onClearAll}
              sx={{ color: muted, '&:hover': { color: 'error.main' } }}
            >
              <ClearAllIcon sx={{ fontSize: 18 }} />
            </IconButton>
          </Tooltip>
        ) : null}
      </Box>

      {items.map((item, index) => (
        <QueueRow
          key={item.id}
          item={item}
          index={index}
          isDarkMode={isDarkMode}
          onUpdateText={onUpdateText}
          onRemove={onRemove}
        />
      ))}
    </Box>
  );
}
