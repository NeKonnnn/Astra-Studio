import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Stack,
  Typography,
} from '@mui/material';
import SystemUpdateAltIcon from '@mui/icons-material/SystemUpdateAlt';
import RefreshIcon from '@mui/icons-material/Refresh';
import GitHubIcon from '@mui/icons-material/GitHub';
import { authFetch, getApiUrl } from '../../config/api';

type CheckResult = {
  local_version?: string;
  remote_version?: string | null;
  latest_release?: string | null;
  commit_sha?: string | null;
  update_available?: boolean;
  enabled?: boolean;
  can_apply?: boolean;
  html_url?: string;
  image_tag?: string;
  repo?: string;
};

type StatusResult = {
  running?: boolean;
  phase?: string;
  message?: string;
  ok?: boolean;
  local_version?: string;
  enabled?: boolean;
  file_status?: { phase?: string; message?: string };
};

const ACTION_BUTTON_SX = {
  minWidth: 200,
  height: 40,
} as const;

export default function UpdatesSettings() {
  const [check, setCheck] = useState<CheckResult | null>(null);
  const [status, setStatus] = useState<StatusResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const loadCheck = useCallback(async () => {
    setError(null);
    const res = await authFetch(getApiUrl('/api/admin/updates/check'));
    if (!res.ok) {
      const detail = await res.text();
      throw new Error(detail || `HTTP ${res.status}`);
    }
    setCheck(await res.json());
  }, []);

  const loadStatus = useCallback(async () => {
    const res = await authFetch(getApiUrl('/api/admin/updates/status'));
    if (!res.ok) return;
    setStatus(await res.json());
  }, []);

  useEffect(() => {
    (async () => {
      try {
        setBusy(true);
        await Promise.all([loadCheck(), loadStatus()]);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    })();
  }, [loadCheck, loadStatus]);

  useEffect(() => {
    if (!status?.running) return undefined;
    const id = window.setInterval(() => {
      void loadStatus().catch(() => {
        // backend may restart mid-update
      });
    }, 3000);
    return () => window.clearInterval(id);
  }, [status?.running, loadStatus]);

  const onCheck = async () => {
    try {
      setBusy(true);
      setError(null);
      await loadCheck();
      await loadStatus();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const onApply = async () => {
    if (
      !window.confirm(
        'Обновить AstraStudio с GitHub/GHCR? Сервисы будут перезапущены, UI может на время пропасть.',
      )
    ) {
      return;
    }
    try {
      setBusy(true);
      setError(null);
      const res = await authFetch(getApiUrl('/api/admin/updates/apply'), { method: 'POST' });
      if (!res.ok) {
        const detail = await res.text();
        throw new Error(detail || `HTTP ${res.status}`);
      }
      setStatus(await res.json());
      // Soft reload later — backend container may restart
      window.setTimeout(() => {
        window.location.reload();
      }, 15000);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const running = Boolean(status?.running);
  const updateAvailable = Boolean(check?.update_available);
  const showLoader = busy || running;

  return (
    <Card sx={{ mb: 3 }}>
      <CardContent>
        <Typography variant="h6" gutterBottom sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <SystemUpdateAltIcon color="primary" />
          Обновления
        </Typography>

        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          Разработчик Константин Некрасов
        </Typography>

        {error && (
          <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
            {error}
          </Alert>
        )}

        <Stack spacing={1} sx={{ mb: 2 }}>
          <Typography variant="body2">
            Локальная версия: <strong>{check?.local_version || status?.local_version || '—'}</strong>
          </Typography>
          <Typography variant="body2">
            GitHub версия: <strong>{check?.remote_version || '—'}</strong>
            {check?.latest_release ? ` (release ${check.latest_release})` : ''}
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Тег образов: {check?.image_tag || 'latest'}
            {check?.commit_sha ? ` · commit ${check.commit_sha}` : ''}
          </Typography>
          <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
            {check?.enabled ? (
              <Chip size="small" color="success" label="Self-update включён" />
            ) : (
              <Chip size="small" color="warning" label="ASTRA_UPDATES_ENABLED=false" />
            )}
            {updateAvailable ? (
              <Chip size="small" color="info" label="Доступно обновление" />
            ) : (
              <Chip size="small" label="Актуально или версия неизвестна" />
            )}
            {check?.can_apply === false && (
              <Chip size="small" color="default" label="Apply из UI недоступен" />
            )}
          </Box>
          {running && (
            <Alert severity="info">
              Идёт обновление: {status?.phase} — {status?.message || status?.file_status?.message}
              . Страница перезагрузится автоматически.
            </Alert>
          )}
          {!running && status?.phase === 'failed' && (
            <Alert severity="error">{status?.message || 'Обновление не удалось'}</Alert>
          )}
          {!running && status?.phase === 'done' && (
            <Alert severity="success">{status?.message || 'Обновление завершено'}</Alert>
          )}
          {check?.enabled && !check?.can_apply && (
            <Alert severity="warning">
              Из UI применить нельзя (нет доступа к каталогу установки / docker.sock). На хосте:
              <Box component="code" sx={{ display: 'block', mt: 1 }}>
                cd ~/astrastudio && sh scripts/update.sh
              </Box>
            </Alert>
          )}
        </Stack>

        <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
          <Button
            variant="outlined"
            startIcon={
              showLoader ? (
                <CircularProgress size={18} thickness={2.4} color="inherit" />
              ) : (
                <RefreshIcon />
              )
            }
            onClick={onCheck}
            disabled={busy || running}
            sx={ACTION_BUTTON_SX}
          >
            Проверить обновления
          </Button>
          <Button
            variant="contained"
            startIcon={<SystemUpdateAltIcon />}
            onClick={onApply}
            disabled={busy || running || !check?.enabled || !check?.can_apply}
            sx={ACTION_BUTTON_SX}
          >
            Обновить сейчас
          </Button>
          {check?.html_url && (
            <Button
              href={check.html_url}
              target="_blank"
              rel="noreferrer"
              startIcon={<GitHubIcon />}
              sx={{ height: 40 }}
            >
              GitHub
            </Button>
          )}
        </Stack>
      </CardContent>
    </Card>
  );
}
