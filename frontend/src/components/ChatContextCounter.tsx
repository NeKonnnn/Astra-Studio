import React from 'react';
import { Box, Tooltip, Typography } from '@mui/material';
import { alpha, useTheme } from '@mui/material/styles';
import type { ChatContextUsage } from '../utils/contextTokens';
import { formatContextTokenCount, formatContextTokenLimit } from '../utils/contextTokens';

export interface ChatContextCounterProps {
  usage: ChatContextUsage;
  isDarkMode?: boolean;
  modelLabel?: string | null;
}

function usageColor(percent: number, isDarkMode: boolean): string {
  if (percent >= 90) return isDarkMode ? '#ff8a80' : '#d32f2f';
  if (percent >= 70) return isDarkMode ? '#ffb74d' : '#ed6c02';
  return isDarkMode ? 'rgba(255,255,255,0.45)' : 'rgba(0,0,0,0.45)';
}

export default function ChatContextCounter({
  usage,
  isDarkMode = false,
  modelLabel,
}: ChatContextCounterProps) {
  const theme = useTheme();
  const color = usageColor(usage.percent, isDarkMode);

  const tooltip = [
    modelLabel ? `Модель: ${modelLabel}` : null,
    `История: ${usage.messageTokens.toLocaleString('ru-RU')} ток.`,
    usage.draftTokens > 0 ? `Черновик: ${usage.draftTokens.toLocaleString('ru-RU')} ток.` : null,
    usage.attachmentTokens > 0
      ? `Вложения: ${usage.attachmentTokens.toLocaleString('ru-RU')} ток.`
      : null,
    `Лимит: ${usage.maxTokens.toLocaleString('ru-RU')} ток. (min окна модели и «Размер контекста» в настройках)`,
    'Оценка приблизительная (~4 символа на токен).',
  ]
    .filter(Boolean)
    .join('\n');

  return (
    <Tooltip title={<span style={{ whiteSpace: 'pre-line' }}>{tooltip}</span>}>
      <Box
        sx={{
          display: 'inline-flex',
          alignItems: 'center',
          userSelect: 'none',
          px: 0.5,
          py: 0.25,
          borderRadius: 1,
          cursor: 'default',
          '&:hover': {
            bgcolor: alpha(theme.palette.text.primary, isDarkMode ? 0.06 : 0.04),
          },
        }}
      >
        <Typography
          component="span"
          variant="caption"
          sx={{
            color,
            fontSize: '0.72rem',
            lineHeight: 1.2,
            letterSpacing: '0.01em',
            whiteSpace: 'nowrap',
          }}
        >
          Контекст {formatContextTokenCount(usage.currentTokens)} /{' '}
          {formatContextTokenLimit(usage.maxTokens)} ({usage.percent}%)
        </Typography>
      </Box>
    </Tooltip>
  );
}
