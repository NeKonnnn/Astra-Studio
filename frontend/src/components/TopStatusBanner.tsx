import React, { useEffect, useRef } from 'react';
import { Box, IconButton, Typography } from '@mui/material';
import { Close as CloseIcon } from '@mui/icons-material';
import { TOP_ERROR_BANNER_AUTO_DISMISS_MS } from './TopErrorBanner';

export type TopStatusSeverity = 'success' | 'error' | 'info' | 'warning';

const SEVERITY_SX: Record<
  TopStatusSeverity,
  { color: string; bgcolor: string; hoverBg: string }
> = {
  success: {
    color: 'success.main',
    bgcolor: 'rgba(46, 125, 50, 0.12)',
    hoverBg: 'rgba(46, 125, 50, 0.12)',
  },
  error: {
    color: 'error.main',
    bgcolor: 'rgba(211, 47, 47, 0.12)',
    hoverBg: 'rgba(211, 47, 47, 0.12)',
  },
  info: {
    color: 'info.main',
    bgcolor: 'rgba(2, 136, 209, 0.12)',
    hoverBg: 'rgba(2, 136, 209, 0.12)',
  },
  warning: {
    color: 'warning.main',
    bgcolor: 'rgba(237, 108, 2, 0.12)',
    hoverBg: 'rgba(237, 108, 2, 0.12)',
  },
};

interface TopStatusBannerProps {
  open: boolean;
  message: string;
  severity?: TopStatusSeverity;
  onClose: () => void;
  /** По умолчанию как у оранжевой RAG-плашки. */
  autoHideMs?: number;
  ariaLabel?: string;
  /** Смещение сверху: 12 — как RAG, 60 — ниже неё (как плашка плагина). */
  top?: number;
}

/**
 * Верхняя плашка по центру — тот же визуальный язык, что у RAG / PluginRunBanner.
 * Заменяет боковые MUI Snackbar для коротких статусных сообщений.
 */
export default function TopStatusBanner({
  open,
  message,
  severity = 'success',
  onClose,
  autoHideMs = TOP_ERROR_BANNER_AUTO_DISMISS_MS,
  ariaLabel = 'Закрыть уведомление',
  top = 12,
}: TopStatusBannerProps) {
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const style = SEVERITY_SX[severity];

  useEffect(() => {
    if (!open || !message.trim()) return undefined;
    const timer = window.setTimeout(() => onCloseRef.current(), autoHideMs);
    return () => window.clearTimeout(timer);
  }, [open, message, autoHideMs]);

  if (!open || !message.trim()) return null;

  return (
    <Box
      role="status"
      sx={{
        position: 'fixed',
        top,
        left: '50%',
        transform: 'translateX(-50%)',
        zIndex: (theme) => theme.zIndex.snackbar + 2,
        maxWidth: 'min(720px, calc(100vw - 24px))',
        px: 2,
        py: 1,
        borderRadius: 1,
        border: '2px solid',
        borderColor: style.color,
        bgcolor: style.bgcolor,
        boxShadow: '0 4px 24px rgba(0,0,0,0.18)',
      }}
    >
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <Typography variant="body2" sx={{ fontWeight: 600, color: style.color, flex: 1, textAlign: 'center' }}>
          {message}
        </Typography>
        <IconButton
          size="small"
          aria-label={ariaLabel}
          onClick={onClose}
          sx={{
            color: style.color,
            flexShrink: 0,
            '&:hover': { bgcolor: style.hoverBg },
          }}
        >
          <CloseIcon fontSize="small" />
        </IconButton>
      </Box>
    </Box>
  );
}
