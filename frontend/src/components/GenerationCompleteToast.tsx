import React, { useEffect, useRef } from 'react';
import { Box, IconButton, Typography } from '@mui/material';
import { Close as CloseIcon } from '@mui/icons-material';
import {
  STATUS_TOAST_BG,
  STATUS_TOAST_TEXT,
} from '../constants/statusToast';
import { GENERATION_COMPLETE_TOAST_DISMISS_MS } from '../utils/generationCompleteToast';

export interface GenerationCompleteToastProps {
  title: string;
  preview: string;
  onOpen: () => void;
  onClose: () => void;
  autoDismissMs?: number | null;
}

/**
 * Плашка «ответ готов» в другом чате: логотип + название + превью, клик открывает чат.
 */
export default function GenerationCompleteToast({
  title,
  preview,
  onOpen,
  onClose,
  autoDismissMs = GENERATION_COMPLETE_TOAST_DISMISS_MS,
}: GenerationCompleteToastProps) {
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    if (autoDismissMs == null) return undefined;
    const timer = window.setTimeout(() => onCloseRef.current(), autoDismissMs);
    return () => window.clearTimeout(timer);
  }, [autoDismissMs, title, preview]);

  return (
    <Box
      role="status"
      onClick={onOpen}
      sx={{
        display: 'flex',
        alignItems: 'center',
        gap: 1.25,
        maxWidth: 'min(420px, calc(100vw - 24px))',
        minWidth: 280,
        px: 1.5,
        py: 1.1,
        borderRadius: '999px',
        bgcolor: STATUS_TOAST_BG,
        boxShadow: '0 8px 28px rgba(0,0,0,0.35)',
        cursor: 'pointer',
        userSelect: 'none',
        '&:hover': { filter: 'brightness(1.06)' },
      }}
    >
      <Box
        component="img"
        src={`${process.env.PUBLIC_URL || ''}/astra.png`}
        alt=""
        sx={{
          width: 36,
          height: 36,
          borderRadius: '50%',
          flexShrink: 0,
          objectFit: 'cover',
          bgcolor: '#1e88e5',
        }}
      />
      <Box sx={{ flex: 1, minWidth: 0, pr: 0.5 }}>
        <Typography
          variant="body2"
          sx={{
            color: STATUS_TOAST_TEXT,
            fontWeight: 700,
            fontSize: '0.875rem',
            lineHeight: 1.25,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {title}
        </Typography>
        <Typography
          variant="caption"
          sx={{
            color: 'rgba(255,255,255,0.82)',
            fontWeight: 400,
            fontSize: '0.75rem',
            lineHeight: 1.3,
            display: '-webkit-box',
            WebkitLineClamp: 1,
            WebkitBoxOrient: 'vertical',
            overflow: 'hidden',
          }}
        >
          {preview}
        </Typography>
      </Box>
      <IconButton
        size="small"
        aria-label="Закрыть уведомление"
        onClick={(e) => {
          e.stopPropagation();
          onClose();
        }}
        sx={{
          color: 'rgba(255,255,255,0.55)',
          flexShrink: 0,
          '&:hover': { color: STATUS_TOAST_TEXT, bgcolor: 'rgba(255,255,255,0.08)' },
        }}
      >
        <CloseIcon fontSize="small" />
      </IconButton>
    </Box>
  );
}
