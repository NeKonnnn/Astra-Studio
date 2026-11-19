import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  LinearProgress,
  Stack,
  TextField,
  Typography,
} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import CloudDownloadIcon from '@mui/icons-material/CloudDownload';
import CancelIcon from '@mui/icons-material/Cancel';
import { authFetch, getApiUrl } from '../config/api';
import { startModelPull, type CatalogPullHit } from '../utils/modelPull';

type DownloadJob = {
  id: string;
  status: string;
  display_name?: string;
  progress_pct?: number;
  bytes_done?: number;
  bytes_total?: number;
  speed_bps?: number;
  eta_seconds?: number | null;
  message?: string;
  error?: string | null;
};

const EXAMPLES = [
  'hf://unsloth/Qwen3.5-4B-GGUF/Qwen3.5-4B-UD-Q8_K_XL.gguf',
  'unsloth/Qwen3.8-27B-GGUF/Qwen3.8-27B-UD-IQ4_XS.gguf',
];

function formatBytes(n: number): string {
  if (!n || n <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(i === 0 ? 0 : 2)} ${units[i]}`;
}

type Props = {
  open: boolean;
  onClose: () => void;
  isDarkMode?: boolean;
  onStarted?: (jobId?: string) => void;
  onCompleted?: () => void;
};

export default function AddModelFromHfDialog({
  open,
  onClose,
  isDarkMode = true,
  onStarted,
  onCompleted,
}: Props) {
  const [query, setQuery] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [job, setJob] = useState<DownloadJob | null>(null);
  const [catalog, setCatalog] = useState<CatalogPullHit[]>([]);

  useEffect(() => {
    if (!open) return;
    setError(null);
    let cancelled = false;
    void (async () => {
      try {
        const res = await authFetch(getApiUrl('/api/model-downloads/catalog'));
        if (!res.ok || cancelled) return;
        const data = await res.json();
        if (!cancelled && Array.isArray(data.models)) setCatalog(data.models);
      } catch {
        /* catalog optional for custom HF pull */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open]);

  useEffect(() => {
    if (!open || !jobId) return;
    let cancelled = false;
    let finished = false;
    const tick = async () => {
      try {
        const res = await authFetch(getApiUrl(`/api/model-downloads/${encodeURIComponent(jobId)}`));
        if (!res.ok || cancelled) return;
        const data = (await res.json()) as DownloadJob;
        if (cancelled) return;
        setJob(data);
        if (!finished && ['completed', 'completed_needs_restart'].includes(data.status)) {
          finished = true;
          onCompleted?.();
        }
      } catch {
        /* ignore */
      }
    };
    void tick();
    const t = window.setInterval(() => void tick(), 800);
    return () => {
      cancelled = true;
      window.clearInterval(t);
    };
  }, [open, jobId, onCompleted]);

  const handleClose = () => {
    if (busy) return;
    setQuery('');
    setJobId(null);
    setJob(null);
    setError(null);
    onClose();
  };

  const handlePull = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await startModelPull({
        query,
        modelType: 'llm_gguf',
        catalog,
      });
      if (!result.ok) {
        setError(result.error);
        return;
      }
      setJobId(result.jobId || null);
      onStarted?.(result.jobId);
      if (!result.jobId) {
        onCompleted?.();
      }
    } finally {
      setBusy(false);
    }
  }, [query, catalog, onStarted, onCompleted]);

  const cancelJob = async () => {
    if (!jobId) return;
    await authFetch(getApiUrl(`/api/model-downloads/${encodeURIComponent(jobId)}/cancel`), {
      method: 'POST',
    });
  };

  const active = job && ['queued', 'running'].includes(job.status);
  const done = job && ['completed', 'completed_needs_restart'].includes(job.status);
  const failed = job && ['failed', 'cancelled'].includes(job.status);
  const progress =
    job && (job.progress_pct || 0) > 0
      ? Math.min(100, Math.max(0, job.progress_pct || 0))
      : job && (job.bytes_total || 0) > 0
        ? Math.min(100, Math.max(0, ((job.bytes_done || 0) / (job.bytes_total || 1)) * 100))
        : 0;

  const panelBg = isDarkMode ? 'rgba(255,255,255,0.04)' : 'rgba(0,0,0,0.03)';
  const border = isDarkMode ? '1px solid rgba(255,255,255,0.12)' : '1px solid rgba(0,0,0,0.12)';

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      maxWidth="sm"
      fullWidth
      PaperProps={{
        sx: {
          borderRadius: 2.5,
          bgcolor: isDarkMode ? '#1e1e1e' : '#fff',
        },
      }}
    >
      <DialogTitle sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', pr: 1 }}>
        <Stack direction="row" spacing={1} alignItems="center">
          <CloudDownloadIcon color="primary" />
          <Typography variant="h6" component="span" fontWeight={600}>
            Добавить модель
          </Typography>
        </Stack>
        <IconButton size="small" onClick={handleClose} disabled={busy} aria-label="Закрыть">
          <CloseIcon fontSize="small" />
        </IconButton>
      </DialogTitle>

      <DialogContent sx={{ pt: 1 }}>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          Вставьте Hugging Face repo или прямую ссылку на GGUF.
        </Typography>

        {error && (
          <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
            {error}
          </Alert>
        )}

        <TextField
          autoFocus
          fullWidth
          size="small"
          label="Ссылка или id"
          placeholder="hf://org/repo/file.gguf · org/repo/file.gguf"
          value={query}
          disabled={Boolean(active)}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && query.trim() && !busy && !active) void handlePull();
          }}
          sx={{
            mb: 1.5,
            '& .MuiOutlinedInput-root': { borderRadius: '10px' },
          }}
        />

        <Stack direction="row" flexWrap="wrap" gap={0.75} sx={{ mb: 2 }}>
          {EXAMPLES.map((ex) => (
            <Chip
              key={ex}
              size="small"
              variant="outlined"
              label={ex.split('/').slice(-1)[0]}
              onClick={() => !active && setQuery(ex)}
              sx={{ maxWidth: '100%' }}
            />
          ))}
        </Stack>

        {(active || done || failed) && job && (
          <Box sx={{ p: 1.5, borderRadius: 2, border, bgcolor: panelBg }}>
            <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.75 }}>
              <Typography variant="body2" fontWeight={600} noWrap sx={{ flex: 1 }}>
                {job.display_name || 'Загрузка'}
              </Typography>
              <Chip
                size="small"
                label={job.status}
                color={done ? 'success' : failed ? 'error' : 'info'}
                variant="outlined"
              />
            </Stack>
            <Typography variant="caption" color="text.secondary" component="div" sx={{ mb: 0.75 }}>
              {job.bytes_total
                ? `${formatBytes(job.bytes_done || 0)} / ${formatBytes(job.bytes_total)}`
                : job.message || '…'}
              {job.speed_bps ? ` · ${formatBytes(job.speed_bps)}/s` : ''}
            </Typography>
            <LinearProgress
              variant={progress > 0 ? 'determinate' : 'indeterminate'}
              value={progress}
              sx={{ height: 8, borderRadius: 1 }}
            />
            {failed && job.error && (
              <Typography variant="caption" color="error" sx={{ mt: 0.75, display: 'block' }}>
                {job.error}
              </Typography>
            )}
            {active && (
              <Button
                size="small"
                color="inherit"
                startIcon={<CancelIcon />}
                onClick={() => void cancelJob()}
                sx={{ mt: 1, textTransform: 'none' }}
              >
                Отменить
              </Button>
            )}
          </Box>
        )}
      </DialogContent>

      <DialogActions sx={{ px: 3, pb: 2.5, gap: 1 }}>
        <Button onClick={handleClose} disabled={busy} sx={{ textTransform: 'none' }}>
          {done ? 'Закрыть' : 'Отмена'}
        </Button>
        {!done && (
          <Button
            variant="contained"
            disabled={!query.trim() || busy || Boolean(active)}
            startIcon={busy ? <CircularProgress size={16} color="inherit" /> : <CloudDownloadIcon />}
            onClick={() => void handlePull()}
            sx={{ textTransform: 'none', borderRadius: '10px' }}
          >
            Скачать
          </Button>
        )}
      </DialogActions>
    </Dialog>
  );
}
