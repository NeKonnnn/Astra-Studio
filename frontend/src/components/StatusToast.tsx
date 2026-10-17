import React, { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
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
  STATUS_TOAST_STACK_ID,
  STATUS_TOAST_STACK_TOP_PX,
  STATUS_TOAST_STACK_Z_INDEX,
  STATUS_TOAST_TEXT,
  type StatusToastType,
} from '../constants/statusToast';

/**
 * Общий контейнер для всех `fixed` тостов: они выстраиваются столбиком сверху по центру.
 * Раньше каждый тост сам позиционировался `fixed` в одной точке, и несколько одновременных
 * (ошибка LLM + «соединение установлено» + перечанковка RAG) ложились друг на друга.
 */
export function getToastStack(): HTMLElement | null {
  if (typeof document === 'undefined') return null;
  let el = document.getElementById(STATUS_TOAST_STACK_ID);
  if (!el) {
    el = document.createElement('div');
    el.id = STATUS_TOAST_STACK_ID;
    Object.assign(el.style, {
      position: 'fixed',
      top: `${STATUS_TOAST_STACK_TOP_PX}px`,
      left: '50%',
      transform: 'translateX(-50%)',
      zIndex: String(STATUS_TOAST_STACK_Z_INDEX),
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      gap: '8px',
      maxWidth: 'calc(100vw - 24px)',
      // Контейнер не перехватывает клики; сами тосты включают pointer-events обратно.
      pointerEvents: 'none',
    });
    document.body.appendChild(el);
  }
  return el;
}

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
  /** Показывать в общем стеке тостов сверху по центру (несколько тостов не перекрываются) */
  fixed?: boolean;
  /** @deprecated позицию задаёт общий стек; параметр игнорируется */
  top?: number;
  /** @deprecated порядок задаёт общий стек; параметр игнорируется */
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

  const toast = (
    <Box
      role={type === 'error' || type === 'warning' ? 'alert' : 'status'}
      sx={{
        display: 'flex',
        alignItems: 'center',
        gap: 1.25,
        maxWidth: 'min(720px, calc(100vw - 24px))',
        px: 2,
        py: 1.5,
        borderRadius: '12px',
        bgcolor: STATUS_TOAST_BG,
        boxShadow: '0 8px 28px rgba(0,0,0,0.35)',
        ...(fixed ? { pointerEvents: 'auto' } : null),
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
            // Сенсорные экраны: область касания 44 px вместо 30 (без изменения размеров тоста).
            '@media (pointer: coarse)': {
              position: 'relative',
              '&::after': { content: '""', position: 'absolute', inset: -7 },
            },
            '&:hover': { color: STATUS_TOAST_TEXT, bgcolor: 'rgba(255,255,255,0.08)' },
          }}
        >
          <CloseIcon fontSize="small" />
        </IconButton>
      ) : null}
    </Box>
  );

  const stack = fixed ? getToastStack() : null;
  return stack ? createPortal(toast, stack) : toast;
}
