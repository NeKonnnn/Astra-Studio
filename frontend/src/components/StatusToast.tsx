import React, { useEffect, useRef } from 'react';
import { Box, IconButton, Typography } from '@mui/material';
import {
  CheckCircleOutline as SuccessIcon,
  WarningAmberOutlined as WarningIcon,
  HighlightOff as ErrorIcon,
  InfoOutlined as InfoIcon,
  Close as CloseIcon,
} from '@mui/icons-material';
import {
  STATUS_TOAST_AUTO_DISMISS_MS,
  STATUS_TOAST_BG,
  STATUS_TOAST_ICON_COLORS,
  STATUS_TOAST_TEXT,
  type StatusToastType,
} from '../constants/statusToast';

const VARIANT_ICONS: Record<StatusToastType, React.ElementType> = {
  success: SuccessIcon,
  warning: WarningIcon,
  error: ErrorIcon,
  info: InfoIcon,
};

export interface StatusToastProps {
  type: StatusToastType;
  message: string;
  onClose?: () => void;
  ariaLabel?: string;
  /** fixed top; если задан — тост позиционируется сверху по центру */
  fixed?: boolean;
  top?: number;
  zIndexOffset?: number;
  /** null — без автоскрытия; по умолчанию STATUS_TOAST_AUTO_DISMISS_MS при наличии onClose */
  autoDismissMs?: number | null;
}

/**
 * Тёмная рамка-уведомление: иконка (зелёная / оранжевая / красная) + произвольный текст.
 * Переиспользуется для копирования, перечанковки, ошибок и reconnect.
 */
export default function StatusToast({
  type,
  message,
  onClose,
  ariaLabel = 'Закрыть уведомление',
  fixed = false,
  top = 12,
  zIndexOffset = 0,
  autoDismissMs = STATUS_TOAST_AUTO_DISMISS_MS,
}: StatusToastProps) {
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const canDismiss = Boolean(onClose);

  useEffect(() => {
    if (!message.trim() || !canDismiss || autoDismissMs == null) return undefined;
    const timer = window.setTimeout(() => onCloseRef.current?.(), autoDismissMs);
    return () => window.clearTimeout(timer);
  }, [message, autoDismissMs, canDismiss]);

  if (!message.trim()) return null;

  const Icon = VARIANT_ICONS[type];
  const iconColor = STATUS_TOAST_ICON_COLORS[type];

  return (
    <Box
      role={type === 'error' || type === 'warning' ? 'alert' : 'status'}
      sx={{
        ...(fixed
          ? {
              position: 'fixed',
              top,
              left: '50%',
              transform: 'translateX(-50%)',
              zIndex: (theme) => theme.zIndex.snackbar + zIndexOffset,
            }
          : null),
        display: 'flex',
        alignItems: 'center',
        gap: 1.25,
        maxWidth: 'min(720px, calc(100vw - 24px))',
        px: 2,
        py: 1.5,
        borderRadius: '12px',
        bgcolor: STATUS_TOAST_BG,
        boxShadow: '0 8px 28px rgba(0,0,0,0.35)',
      }}
    >
      <Icon
        sx={{
          color: iconColor,
          fontSize: 22,
          flexShrink: 0,
        }}
      />
      <Typography
        variant="body2"
        sx={{
          color: STATUS_TOAST_TEXT,
          fontWeight: 500,
          lineHeight: 1.45,
          flex: 1,
          textAlign: 'left',
        }}
      >
        {message}
      </Typography>
      {onClose ? (
        <IconButton
          size="small"
          aria-label={ariaLabel}
          onClick={onClose}
          sx={{
            color: 'rgba(255,255,255,0.55)',
            flexShrink: 0,
            ml: 0.5,
            '&:hover': { color: STATUS_TOAST_TEXT, bgcolor: 'rgba(255,255,255,0.08)' },
          }}
        >
          <CloseIcon fontSize="small" />
        </IconButton>
      ) : null}
    </Box>
  );
}
