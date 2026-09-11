import React, { useEffect, useState } from 'react';
import { Box, Button, CircularProgress, IconButton, Typography } from '@mui/material';
import { Close as CloseIcon } from '@mui/icons-material';
import { useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { usePluginRuns } from '../contexts/PluginRunContext';
import { formatDuration } from '../plugins/verdict';
import { STATUS_TOAST_AUTO_DISMISS_MS } from '../constants/statusToast';

const GALLERY_PATH = '/gallery?tab=plugins';

/**
 * Плашка «аудит плагина выполняется / готов» — видна из чата и других разделов,
 * чтобы закрытое модальное окно не выглядело как потерянный запуск.
 *
 * Готовый (зелёный/красный) результат живёт как оранжевая RAG-плашка:
 * несколько секунд или до крестика. На логине и без сессии не показывается.
 */
export default function PluginRunBanner() {
  const { isAuthenticated } = useAuth();
  const { runningPlugins, runs, elapsedSec } = usePluginRuns();
  const navigate = useNavigate();
  const location = useLocation();
  const [dismissedKey, setDismissedKey] = useState<string | null>(null);

  const running = runningPlugins[0] || null;
  const finished = Object.values(runs)
    .filter((r) => r.status !== 'running' && r.finishedAtMs)
    .sort((a, b) => (b.finishedAtMs || 0) - (a.finishedAtMs || 0))[0];

  // Старый результат из localStorage не должен вечно висеть зелёной плашкой.
  const finishedIsFresh =
    Boolean(finished?.finishedAtMs) &&
    Date.now() - (finished!.finishedAtMs as number) < STATUS_TOAST_AUTO_DISMISS_MS;

  const active = running || (finishedIsFresh ? finished : null) || null;
  const activeKey = active
    ? `${active.pluginId}:${active.status}:${active.finishedAtMs || active.startedAtMs}`
    : null;

  const hiddenRoute =
    location.pathname.startsWith(GALLERY_PATH) ||
    location.pathname.startsWith('/login') ||
    location.pathname.startsWith('/share');

  const visible =
    isAuthenticated &&
    !hiddenRoute &&
    Boolean(active && activeKey && dismissedKey !== activeKey);

  // Как у RagReindexStatusBanner: готовый результат сам уходит через N секунд.
  useEffect(() => {
    if (!visible || !activeKey || running) return undefined;
    const timer = window.setTimeout(() => {
      setDismissedKey(activeKey);
    }, STATUS_TOAST_AUTO_DISMISS_MS);
    return () => window.clearTimeout(timer);
  }, [visible, activeKey, running]);

  if (!visible || !active || !activeKey) return null;

  const isError = active.status === 'error';
  const color = running ? 'info.main' : isError ? 'error.main' : 'success.main';
  const bg = running
    ? 'rgba(2, 136, 209, 0.12)'
    : isError
      ? 'rgba(211, 47, 47, 0.12)'
      : 'rgba(46, 125, 50, 0.12)';
  const text = running
    ? `${active.pluginName}: аудит выполняется — ${formatDuration(elapsedSec(active.pluginId))}`
    : isError
      ? `${active.pluginName}: аудит не завершён`
      : `${active.pluginName}: аудит готов`;

  return (
    <Box
      role="status"
      sx={{
        position: 'fixed',
        // Ниже RagReindexStatusBanner (top: 12), чтобы плашки не перекрывались.
        top: 60,
        left: '50%',
        transform: 'translateX(-50%)',
        zIndex: (theme) => theme.zIndex.snackbar + 1,
        maxWidth: 'min(720px, calc(100vw - 24px))',
        px: 2,
        py: 1,
        borderRadius: 1,
        border: '2px solid',
        borderColor: color,
        bgcolor: bg,
        boxShadow: '0 4px 24px rgba(0,0,0,0.18)',
      }}
    >
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        {running && <CircularProgress size={16} sx={{ color }} />}
        <Typography variant="body2" sx={{ fontWeight: 600, color, flex: 1 }}>
          {text}
        </Typography>
        <Button size="small" onClick={() => navigate(GALLERY_PATH)} sx={{ color }}>
          {running ? 'Открыть' : 'Показать результат'}
        </Button>
        <IconButton
          size="small"
          aria-label="Скрыть уведомление о запуске плагина"
          onClick={() => setDismissedKey(activeKey)}
          sx={{ color }}
        >
          <CloseIcon fontSize="small" />
        </IconButton>
      </Box>
    </Box>
  );
}
