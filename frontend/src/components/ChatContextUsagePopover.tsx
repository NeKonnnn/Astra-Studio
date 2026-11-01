import React from 'react';
import {
  Box,
  CircularProgress,
  IconButton,
  Popover,
  Tooltip,
  Typography,
} from '@mui/material';
import { alpha, useTheme } from '@mui/material/styles';
import CloseIcon from '@mui/icons-material/Close';
import type { ChatContextUsageResult } from '../hooks/useChatContextUsage';
import {
  CONTEXT_SEGMENT_COLORS,
  CONTEXT_SEGMENT_ORDER,
  formatContextTokenCount,
  formatContextTokenLimit,
} from '../utils/contextTokens';

export interface ChatContextUsagePopoverProps {
  usage: ChatContextUsageResult;
  isDarkMode?: boolean;
  modelLabel?: string | null;
}

function usageColor(percent: number, isDarkMode: boolean): string {
  if (percent >= 100) return isDarkMode ? '#ff8a80' : '#d32f2f';
  if (percent >= 70) return isDarkMode ? '#ffb74d' : '#ed6c02';
  return isDarkMode ? 'rgba(255,255,255,0.45)' : 'rgba(0,0,0,0.45)';
}

function formatSegmentTokens(n: number): string {
  if (n >= 1000) {
    const k = n / 1000;
    const rounded = Math.round(k * 10) / 10;
    return `${Number.isInteger(rounded) ? rounded : rounded.toFixed(1).replace('.', ',')}K`;
  }
  return n.toLocaleString('ru-RU');
}

export default function ChatContextUsagePopover({
  usage,
  isDarkMode = false,
  modelLabel,
}: ChatContextUsagePopoverProps) {
  const theme = useTheme();
  const [anchorEl, setAnchorEl] = React.useState<HTMLElement | null>(null);
  const open = Boolean(anchorEl);
  const color = usageColor(usage.percent, isDarkMode);

  const segmentById = React.useMemo(() => {
    const map = new Map((usage.segments ?? []).map((s) => [s.id, s]));
    return map;
  }, [usage.segments]);

  const displaySegments = CONTEXT_SEGMENT_ORDER.map((id) => segmentById.get(id)).filter(
    (seg): seg is NonNullable<typeof seg> => Boolean(seg),
  );

  const barSegments = displaySegments.filter((s) => s.tokens > 0);
  const totalForBar = Math.max(usage.currentTokens, 1);

  const handleOpen = (e: React.MouseEvent<HTMLElement>) => {
    setAnchorEl(e.currentTarget);
    usage.refreshOverhead();
  };

  const summaryTooltip = [
    modelLabel ? `Модель: ${modelLabel}` : null,
    `Контекст: ${formatContextTokenCount(usage.currentTokens)} / ${formatContextTokenLimit(usage.maxTokens)} Токены (${usage.percent}%)`,
    'Нажмите для детализации',
    'Оценка ~4 символа на токен',
  ]
    .filter(Boolean)
    .join(' · ');

  const ringBg = isDarkMode ? 'rgba(255,255,255,0.12)' : 'rgba(0,0,0,0.08)';

  return (
    <>
      <Tooltip title={summaryTooltip}>
        <Box
          component="button"
          type="button"
          onClick={handleOpen}
          aria-label={`Использование контекста ${usage.percent}%`}
          sx={{
            position: 'relative',
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            width: 36,
            height: 36,
            flexShrink: 0,
            p: 0,
            border: 'none',
            borderRadius: '50%',
            bgcolor: 'transparent',
            cursor: 'pointer',
            '&:hover': {
              bgcolor: alpha(theme.palette.text.primary, isDarkMode ? 0.08 : 0.06),
            },
          }}
        >
          <CircularProgress
            variant="determinate"
            value={100}
            size={36}
            thickness={3.5}
            sx={{
              position: 'absolute',
              color: ringBg,
            }}
          />
          <CircularProgress
            variant="determinate"
            value={Math.min(100, usage.percent)}
            size={36}
            thickness={3.5}
            sx={{
              position: 'absolute',
              zIndex: 1,
              color,
              '& .MuiCircularProgress-circle': {
                strokeLinecap: 'round',
              },
            }}
          />
          <Typography
            component="span"
            variant="caption"
            sx={{
              position: 'relative',
              zIndex: 1,
              color,
              fontSize: usage.percent >= 100 ? '0.55rem' : '0.62rem',
              fontWeight: 700,
              lineHeight: 1,
              letterSpacing: '-0.02em',
            }}
          >
            {usage.percent}%
          </Typography>
        </Box>
      </Tooltip>

      <Popover
        open={open}
        anchorEl={anchorEl}
        onClose={() => setAnchorEl(null)}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
        transformOrigin={{ vertical: 'bottom', horizontal: 'right' }}
        slotProps={{
          paper: {
            sx: {
              width: 340,
              maxWidth: '92vw',
              p: 2,
              borderRadius: 2,
              bgcolor: isDarkMode ? '#2a2a2a' : '#fff',
              border: isDarkMode ? '1px solid rgba(255,255,255,0.12)' : '1px solid rgba(0,0,0,0.08)',
            },
          },
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
          <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
            Использование контекста
          </Typography>
          <IconButton size="small" onClick={() => setAnchorEl(null)} aria-label="Закрыть">
            <CloseIcon fontSize="small" />
          </IconButton>
        </Box>

        <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.75 }}>
          <Typography variant="caption" color="text.secondary">
            {usage.percent}% заполнено
          </Typography>
          <Typography variant="caption" color="text.secondary">
            ~{formatContextTokenCount(usage.currentTokens)} / {formatContextTokenLimit(usage.maxTokens)} Токены
          </Typography>
        </Box>

        <Box
          sx={{
            display: 'flex',
            height: 8,
            borderRadius: 1,
            overflow: 'hidden',
            bgcolor: isDarkMode ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.06)',
            mb: 1.5,
          }}
        >
          {barSegments.map((seg) => {
            const widthPct = Math.max(0.5, (seg.tokens / totalForBar) * 100);
            const segColor = CONTEXT_SEGMENT_COLORS[seg.id] ?? theme.palette.primary.main;
            return (
              <Box
                key={seg.id}
                title={`${seg.label}: ${seg.tokens}`}
                sx={{
                  width: `${widthPct}%`,
                  bgcolor: seg.active === false ? alpha(segColor, 0.35) : segColor,
                  minWidth: seg.tokens > 0 ? 2 : 0,
                }}
              />
            );
          })}
        </Box>

        {usage.overheadLoading ? (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, py: 1 }}>
            <CircularProgress size={16} />
            <Typography variant="body2" color="text.secondary">
              Загрузка системных сегментов…
            </Typography>
          </Box>
        ) : null}

        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.75 }}>
          {displaySegments.map((seg) => {
            const segColor = CONTEXT_SEGMENT_COLORS[seg.id] ?? theme.palette.primary.main;
            return (
              <Box
                key={seg.id}
                sx={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  gap: 1,
                  opacity: seg.active === false ? 0.55 : 1,
                }}
              >
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, minWidth: 0 }}>
                  <Box
                    sx={{
                      width: 10,
                      height: 10,
                      borderRadius: 0.5,
                      flexShrink: 0,
                      bgcolor: segColor,
                    }}
                  />
                  <Typography variant="body2" noWrap sx={{ fontSize: '0.82rem' }}>
                    {seg.label}
                    {seg.active === false ? ' (не активен)' : ''}
                  </Typography>
                </Box>
                <Typography variant="body2" sx={{ fontWeight: 600, flexShrink: 0, fontSize: '0.82rem' }}>
                  {formatSegmentTokens(seg.tokens)}
                </Typography>
              </Box>
            );
          })}
        </Box>

        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1.5, lineHeight: 1.4 }}>
          {usage.isOverLimit ? (
            <Box
              component="span"
              sx={{
                color: usage.percent >= 100
                  ? isDarkMode
                    ? '#ff8a80'
                    : '#d32f2f'
                  : isDarkMode
                    ? '#ffb74d'
                    : '#ed6c02',
              }}
            >
              Оценка превышает окно модели — отправка не блокируется; при ошибке LLM уменьшите файл или max_tokens.
            </Box>
          ) : (
            <>
              Память — все завершённые сообщения диалога. Текущее — только черновик в поле ввода
              (после ответа сбрасывается). Генерация — ответ, пока идёт стрим. Скиллы/агент —
              скрытый промпт запроса. Оценка ~4 символа на токен.
            </>
          )}
        </Typography>
      </Popover>
    </>
  );
}
