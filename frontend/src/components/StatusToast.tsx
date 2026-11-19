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
  /** fixed top; если задан — тост встаёт в общий стек сверху по центру */
  fixed?: boolean;
  /** @deprecated для fixed используется STATUS_TOAST_STACK_TOP_PX */
  top?: number;
  /** @deprecated для fixed используется STATUS_TOAST_STACK_Z_INDEX */
  zIndexOffset?: number;
  /** null — без автоскрытия; по умолчанию STATUS_TOAST_AUTO_DISMISS_MS при наличии onClose */
  autoDismissMs?: number | null;
}

function ensureToastStack(): HTMLElement {
  let stack = document.getElementById(STATUS_TOAST_STACK_ID);
  if (stack) return stack;

  stack = document.createElement('div');
  stack.id = STATUS_TOAST_STACK_ID;
  stack.style.position = 'fixed';
  stack.style.top = `${STATUS_TOAST_STACK_TOP_PX}px`;
  stack.style.left = '50%';
  stack.style.transform = 'translateX(-50%)';
  stack.style.zIndex = String(STATUS_TOAST_STACK_Z_INDEX);
  stack.style.display = 'flex';
  stack.style.flexDirection = 'column';
  stack.style.gap = '8px';
  stack.style.pointerEvents = 'none';
  stack.style.maxWidth = 'calc(100vw - 24px)';
  stack.style.width = 'max-content';
  document.body.appendChild(stack);
  return stack;
}

function releaseToastStackIfEmpty(): void {
  const stack = document.getElementById(STATUS_TOAST_STACK_ID);
  if (stack && stack.childElementCount === 0) {
    stack.remove();
  }
}

/**
 * Тёмная рамка-уведомление: иконка (зелёная / оранжевая / красная) + произвольный текст.
 * Переиспользуется для копирования, перечанковки, ошибок и reconnect.
 * fixed-тосты собираются в один столбик (#STATUS_TOAST_STACK_ID), чтобы не накладываться.
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
  const stackHostRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!message.trim() || !canDismiss || autoDismissMs == null) return undefined;
    const timer = window.setTimeout(() => onCloseRef.current?.(), autoDismissMs);
    return () => window.clearTimeout(timer);
  }, [message, autoDismissMs, canDismiss]);

  useEffect(() => {
    if (!fixed) return undefined;
    stackHostRef.current = ensureToastStack();
    return () => {
      // После unmount портала дождаться снятия DOM-узла, затем убрать пустой стек
      window.requestAnimationFrame(() => releaseToastStackIfEmpty());
    };
  }, [fixed]);

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
        pointerEvents: 'auto',
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

  if (fixed && typeof document !== 'undefined') {
    const stack = stackHostRef.current ?? ensureToastStack();
    return createPortal(toast, stack);
  }

  return toast;
}
