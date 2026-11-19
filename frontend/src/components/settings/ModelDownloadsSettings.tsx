import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Collapse,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  IconButton,
  InputAdornment,
  LinearProgress,
  List,
  ListItem,
  ListItemAvatar,
  ListItemSecondaryAction,
  ListItemText,
  Popover,
  Stack,
  TextField,
  Tooltip,
  Typography,
  useTheme,
} from '@mui/material';
import type { Theme } from '@mui/material/styles';
import CloudDownloadIcon from '@mui/icons-material/CloudDownload';
import DownloadIcon from '@mui/icons-material/Download';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import CancelIcon from '@mui/icons-material/Cancel';
import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircleOutline';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import ExpandLessIcon from '@mui/icons-material/ExpandLess';
import HelpOutlineIcon from '@mui/icons-material/HelpOutline';
import SearchIcon from '@mui/icons-material/Search';
import { authFetch, getApiUrl } from '../../config/api';
import {
  MODEL_SETTINGS_CARD_SX,
  MODEL_SETTINGS_HELP_ICON_BUTTON_SX,
  MODEL_SETTINGS_SECTION_TITLE_SX,
} from '../../constants/modelSettingsStyles';
import {
  DROPDOWN_TRIGGER_BG,
  DROPDOWN_TRIGGER_BG_HOVER,
  getDropdownChevronSx,
  getDropdownItemStateSx,
  getDropdownItemSx,
  getDropdownPopoverPaperSx,
  getDropdownTriggerButtonSx,
  getDropdownTriggerTextSx,
} from '../../constants/menuStyles';
import ModelHfAvatar from '../ModelHfAvatar';

async function readErrorDetail(res: Response, fallback: string): Promise<string> {
  const data = await res.json().catch(() => ({} as { detail?: string }));
  return (typeof data.detail === 'string' && data.detail) || fallback;
}

type CatalogModel = {
  id: string;
  type: string;
  display_name: string;
  description?: string;
  size_hint?: string;
  gated?: boolean;
  installed?: boolean;
  from_disk?: boolean;
  recommended?: boolean;
  has_provenance?: boolean;
  avatar_url?: string;
  local_name?: string;
  apply?: string;
  source?: { kind?: string; repo_id?: string; url?: string; filename?: string };
};

type DownloadJob = {
  id: string;
  status: string;
  model_type: string;
  display_name: string;
  progress_pct: number;
  bytes_done: number;
  bytes_total: number;
  speed_bps?: number;
  eta_seconds?: number | null;
  message: string;
  error?: string | null;
  catalog_id?: string | null;
};

const TYPE_OPTIONS: Array<{ id: string; label: string }> = [
  { id: 'all', label: 'Все типы' },
  { id: 'llm_gguf', label: 'LLM' },
  { id: 'rag_embed', label: 'RAG embed' },
  { id: 'rag_rerank', label: 'RAG rerank' },
  { id: 'stt', label: 'STT' },
  { id: 'tts', label: 'TTS' },
  { id: 'diarization', label: 'Диаризация' },
  { id: 'comfyui', label: 'ComfyUI' },
  { id: 'ocr', label: 'OCR' },
  { id: 'omnivoice', label: 'OmniVoice' },
];

/** Порядок секций «Рекомендованные» в UI. */
const TYPE_SECTION_ORDER: Array<{ id: string; label: string }> = [
  { id: 'llm', label: 'LLM' },
  { id: 'rag_embed', label: 'RAG (embed)' },
  { id: 'rag_rerank', label: 'RAG (rerank)' },
  { id: 'stt', label: 'STT' },
  { id: 'tts', label: 'TTS' },
  { id: 'diarization', label: 'Диаризация' },
  { id: 'ocr', label: 'OCR' },
  { id: 'comfyui', label: 'ComfyUI (изображения / видео)' },
  { id: 'omnivoice', label: 'OmniVoice' },
];

function sectionKeyForType(type: string): string {
  if (type === 'llm_gguf' || type === 'llm_hf') return 'llm';
  return type;
}

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

function formatEta(seconds?: number | null): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return '';
  const s = Math.round(seconds);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  if (m < 60) return `${m}m ${String(r).padStart(2, '0')}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${String(m % 60).padStart(2, '0')}m`;
}

function formatJobProgress(j: DownloadJob): string {
  const parts: string[] = [];
  if (j.bytes_total > 0) {
    parts.push(`${formatBytes(j.bytes_done)} / ${formatBytes(j.bytes_total)}`);
    const left = Math.max(0, j.bytes_total - j.bytes_done);
    if (left > 0) parts.push(`осталось ${formatBytes(left)}`);
  } else if (j.bytes_done > 0) {
    parts.push(formatBytes(j.bytes_done));
  }
  if (j.speed_bps && j.speed_bps > 0) {
    parts.push(`${formatBytes(j.speed_bps)}/s`);
  }
  const eta = formatEta(j.eta_seconds);
  if (eta) parts.push(`~${eta}`);
  if (j.progress_pct > 0) parts.push(`${j.progress_pct.toFixed(1)}%`);
  return parts.join(' · ');
}

function statusColor(status: string): 'default' | 'success' | 'warning' | 'error' | 'info' {
  if (status === 'completed') return 'success';
  if (status === 'completed_needs_restart') return 'warning';
  if (status === 'failed' || status === 'cancelled') return 'error';
  if (status === 'running' || status === 'queued') return 'info';
  return 'default';
}

function ModelCatalogRow({
  model: m,
  theme,
  onApply,
  onDownload,
  onDelete,
}: {
  model: CatalogModel;
  theme: Theme;
  onApply: (id: string) => void;
  onDownload: (id: string) => void;
  onDelete: (m: CatalogModel) => void;
}) {
  return (
    <ListItem
      divider
      sx={{
        py: 1.25,
        '&:hover': { backgroundColor: theme.palette.action.hover },
      }}
    >
      <ListItemAvatar sx={{ minWidth: 48 }}>
        <ModelHfAvatar
          avatarUrl={m.avatar_url}
          repoId={m.source?.kind !== 'local' ? m.source?.repo_id : undefined}
          modelKey={m.local_name || m.source?.filename || m.display_name}
          size={36}
          iconFontSize={22}
          rounded
        />
      </ListItemAvatar>
      <ListItemText
        primary={
          <Stack direction="row" spacing={0.75} alignItems="center" flexWrap="wrap" useFlexGap>
            <Typography variant="body2" fontWeight={600}>
              {m.display_name}
            </Typography>
            {m.installed && (
              <Chip
                size="small"
                icon={<CheckCircleOutlineIcon />}
                label="установлена"
                color="success"
                variant="outlined"
              />
            )}
            {m.from_disk && m.source?.kind === 'local' && (
              <Chip size="small" label="local" color="info" variant="outlined" />
            )}
            {m.from_disk && m.source?.kind !== 'local' && (
              <Chip size="small" label="pulled" color="info" variant="outlined" />
            )}
            {m.gated && <Chip size="small" label="gated" color="warning" variant="outlined" />}
            <Chip size="small" label={m.type} variant="outlined" />
            {m.size_hint && (
              <Typography variant="caption" color="text.secondary">
                {m.size_hint}
              </Typography>
            )}
          </Stack>
        }
        secondary={
          <Typography variant="caption" color="text.secondary" component="span">
            {m.source?.kind === 'local'
              ? (m.source.filename || m.local_name || 'local disk')
              : m.source?.kind === 'http'
                ? m.source.url
                : m.source?.repo_id}
            {m.source?.kind !== 'local' && m.source?.filename ? ` · ${m.source.filename}` : ''}
            {m.description ? ` — ${m.description}` : ''}
          </Typography>
        }
      />
      <Stack direction="row" spacing={0.5} sx={{ ml: 1, flexShrink: 0 }} alignItems="center">
        {m.installed && m.apply && m.apply !== 'none' && (
          <Button size="small" variant="text" onClick={() => void onApply(m.id)}>
            Apply
          </Button>
        )}
        {m.source?.kind !== 'local' && (
          <Button
            size="small"
            variant={m.installed ? 'outlined' : 'contained'}
            startIcon={<DownloadIcon />}
            onClick={() => void onDownload(m.id)}
          >
            {m.installed ? 'Re-pull' : 'Скачать'}
          </Button>
        )}
        {m.installed && (
          <Tooltip title="Удалить с диска">
            <IconButton
              size="small"
              color="error"
              aria-label="Удалить модель"
              onClick={() => onDelete(m)}
            >
              <DeleteOutlineIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        )}
      </Stack>
    </ListItem>
  );
}

export default function ModelDownloadsSettings() {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';
  const dropdownTriggerSx = useMemo(() => getDropdownTriggerButtonSx(isDark), [isDark]);
  const dropdownTriggerTextSx = useMemo(() => getDropdownTriggerTextSx(isDark), [isDark]);
  const dropdownChevronSx = useMemo(() => getDropdownChevronSx(isDark), [isDark]);
  const dropdownItemSx = useMemo(() => getDropdownItemSx(isDark), [isDark]);

  const [catalog, setCatalog] = useState<CatalogModel[]>([]);
  const [jobs, setJobs] = useState<DownloadJob[]>([]);
  const [typeFilter, setTypeFilter] = useState('all');
  const [typeFilterAnchor, setTypeFilterAnchor] = useState<HTMLElement | null>(null);
  const [search, setSearch] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const [hfMasked, setHfMasked] = useState('');
  const [hfConfigured, setHfConfigured] = useState(false);
  const [hfTokenInput, setHfTokenInput] = useState('');
  const [advancedOpen, setAdvancedOpen] = useState(false);

  const [deleteTarget, setDeleteTarget] = useState<CatalogModel | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);

  const loadCatalog = useCallback(async () => {
    const res = await authFetch(getApiUrl('/api/model-downloads/catalog'));
    if (!res.ok) throw new Error(await readErrorDetail(res, `Ошибка ${res.status}`));
    const data = await res.json();
    setCatalog(Array.isArray(data.models) ? data.models : []);
  }, []);

  const loadJobs = useCallback(async () => {
    const res = await authFetch(getApiUrl('/api/model-downloads'));
    if (!res.ok) return;
    const data = await res.json();
    setJobs(Array.isArray(data.jobs) ? data.jobs : []);
  }, []);

  const loadToken = useCallback(async () => {
    const res = await authFetch(getApiUrl('/api/model-downloads/hf-token'));
    if (!res.ok) return;
    const data = await res.json();
    setHfConfigured(Boolean(data.configured));
    setHfMasked(data.masked || '');
  }, []);

  const refreshAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      await Promise.all([loadCatalog(), loadJobs(), loadToken()]);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [loadCatalog, loadJobs, loadToken]);

  useEffect(() => {
    void refreshAll();
  }, [refreshAll]);

  useEffect(() => {
    const hasActive = jobs.some((j) => j.status === 'queued' || j.status === 'running');
    if (!hasActive) return undefined;
    const t = window.setInterval(() => {
      void loadJobs();
    }, 1500);
    return () => window.clearInterval(t);
  }, [jobs, loadJobs]);

  const activeJobs = useMemo(
    () => jobs.filter((j) => j.status === 'queued' || j.status === 'running'),
    [jobs],
  );
  const recentJobs = useMemo(
    () => jobs.filter((j) => j.status !== 'queued' && j.status !== 'running').slice(0, 6),
    [jobs],
  );

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return catalog.filter((m) => {
      if (typeFilter === 'llm_gguf') {
        if (m.type !== 'llm_gguf' && m.type !== 'llm_hf') return false;
      } else if (typeFilter !== 'all' && m.type !== typeFilter) {
        return false;
      }
      if (!q) return true;
      const hay = `${m.display_name} ${m.id} ${m.description || ''} ${m.source?.repo_id || ''}`.toLowerCase();
      return hay.includes(q);
    });
  }, [catalog, typeFilter, search]);

  const groupedSections = useMemo(() => {
    const bySection = new Map<string, CatalogModel[]>();
    for (const m of filtered) {
      const key = sectionKeyForType(m.type);
      const list = bySection.get(key) || [];
      list.push(m);
      bySection.set(key, list);
    }
    const sections: Array<{ id: string; label: string; recommended: CatalogModel[]; other: CatalogModel[] }> = [];
    for (const meta of TYPE_SECTION_ORDER) {
      const items = bySection.get(meta.id) || [];
      if (!items.length) continue;
      sections.push({
        id: meta.id,
        label: meta.label,
        recommended: items.filter((m) => m.recommended !== false && !m.from_disk),
        other: items.filter((m) => m.recommended === false || Boolean(m.from_disk)),
      });
    }
    // типы вне известного порядка
    Array.from(bySection.entries()).forEach(([key, items]) => {
      if (TYPE_SECTION_ORDER.some((s) => s.id === key)) return;
      sections.push({
        id: key,
        label: key,
        recommended: items.filter((m) => m.recommended !== false && !m.from_disk),
        other: items.filter((m) => m.recommended === false || Boolean(m.from_disk)),
      });
    });
    return sections;
  }, [filtered]);

  const startCatalog = async (id: string) => {
    setError(null);
    setInfo(null);
    const res = await authFetch(getApiUrl('/api/model-downloads/start'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ catalog_id: id, apply: true }),
    });
    if (!res.ok) {
      setError(await readErrorDetail(res, `Ошибка ${res.status}`));
      return;
    }
    setInfo('Загрузка запущена');
    await loadJobs();
  };

  const applyInstalled = async (id: string) => {
    setError(null);
    const res = await authFetch(
      getApiUrl(`/api/model-downloads/apply/${encodeURIComponent(id)}`),
      { method: 'POST' },
    );
    if (!res.ok) {
      setError(await readErrorDetail(res, `Ошибка ${res.status}`));
      return;
    }
    setInfo('Модель применена');
    window.dispatchEvent(new CustomEvent('astra-models-changed'));
  };

  const confirmDeleteInstalled = async () => {
    if (!deleteTarget) return;
    setDeleteBusy(true);
    setError(null);
    try {
      const res = await authFetch(
        getApiUrl(`/api/model-downloads/catalog/${encodeURIComponent(deleteTarget.id)}`),
        { method: 'DELETE' },
      );
      if (!res.ok) {
        setError(await readErrorDetail(res, `Ошибка ${res.status}`));
        return;
      }
      const data = await res.json().catch(() => ({} as { message?: string; deleted?: string[] }));
      const n = Array.isArray(data.deleted) ? data.deleted.length : 0;
      setInfo(data.message || `Удалено файлов: ${n}`);
      setDeleteTarget(null);
      await loadCatalog();
      window.dispatchEvent(new CustomEvent('astra-models-changed'));
    } finally {
      setDeleteBusy(false);
    }
  };

  const cancelJob = async (id: string) => {
    await authFetch(
      getApiUrl(`/api/model-downloads/${encodeURIComponent(id)}/cancel`),
      { method: 'POST' },
    );
    await loadJobs();
  };

  const saveToken = async () => {
    const res = await authFetch(getApiUrl('/api/model-downloads/hf-token'), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: hfTokenInput }),
    });
    if (!res.ok) {
      setError(await readErrorDetail(res, 'Не удалось сохранить токен'));
      return;
    }
    setHfTokenInput('');
    setInfo('HF token сохранён');
    await loadToken();
  };

  const clearToken = async () => {
    await authFetch(getApiUrl('/api/model-downloads/hf-token'), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: '' }),
    });
    await loadToken();
    setInfo('HF token удалён');
  };

  const panelBg = isDark ? 'rgba(255,255,255,0.03)' : 'rgba(0,0,0,0.02)';
  const border = `1px solid ${theme.palette.divider}`;

  return (
    <Card sx={MODEL_SETTINGS_CARD_SX}>
      <CardContent>
        <Typography variant="h6" gutterBottom sx={MODEL_SETTINGS_SECTION_TITLE_SX}>
          <CloudDownloadIcon color="primary" />
          Управление моделями
          <Tooltip
            title="Рекомендованные модели по типам: Скачать / Re-pull / Apply / удаление. Новую LLM с Hugging Face — в чате: Модели → Добавить модель"
            arrow
          >
            <IconButton size="small" sx={MODEL_SETTINGS_HELP_ICON_BUTTON_SX}>
              <HelpOutlineIcon fontSize="small" color="action" />
            </IconButton>
          </Tooltip>
        </Typography>

        {error && (
          <Alert severity="error" onClose={() => setError(null)} sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}
        {info && (
          <Alert severity="success" onClose={() => setInfo(null)} sx={{ mb: 2 }}>
            {info}
          </Alert>
        )}
        {loading && (
          <Box sx={{ display: 'flex', justifyContent: 'center', mb: 2 }}>
            <CircularProgress size={28} thickness={2.4} />
          </Box>
        )}

      {activeJobs.length > 0 && (
        <Box sx={{ mb: 2, borderRadius: 1, border, overflow: 'hidden' }}>
          <Box sx={{ px: 2, py: 1.25, backgroundColor: panelBg }}>
            <Typography variant="subtitle1" fontWeight={600}>
              Загрузки
            </Typography>
          </Box>
          <List dense disablePadding>
            {activeJobs.map((j) => (
              <ListItem key={j.id} divider sx={{ py: 1.5, alignItems: 'flex-start' }}>
                <ListItemText
                  primary={
                    <Stack direction="row" spacing={1} alignItems="center">
                      <Typography variant="body2" fontWeight={600}>
                        {j.display_name}
                      </Typography>
                      <Chip size="small" label={j.status} color={statusColor(j.status)} />
                    </Stack>
                  }
                  secondary={
                    <Box sx={{ mt: 0.75 }}>
                      <Typography variant="body2" component="div" sx={{ fontVariantNumeric: 'tabular-nums' }}>
                        {formatJobProgress(j) || 'Подключение…'}
                      </Typography>
                      <Typography variant="caption" color="text.secondary" component="div" noWrap>
                        {j.message}
                      </Typography>
                      <LinearProgress
                        variant={
                          j.bytes_total > 0 || j.progress_pct > 0
                            ? 'determinate'
                            : 'indeterminate'
                        }
                        value={
                          j.progress_pct > 0
                            ? Math.min(100, Math.max(0, j.progress_pct))
                            : j.bytes_total > 0
                              ? Math.min(100, Math.max(0, (j.bytes_done / j.bytes_total) * 100))
                              : 0
                        }
                        sx={{ mt: 0.75, borderRadius: 1, height: 8 }}
                      />
                    </Box>
                  }
                  secondaryTypographyProps={{ component: 'div' }}
                />
                <ListItemSecondaryAction>
                  <IconButton edge="end" size="small" onClick={() => void cancelJob(j.id)} aria-label="cancel">
                    <CancelIcon fontSize="small" />
                  </IconButton>
                </ListItemSecondaryAction>
              </ListItem>
            ))}
          </List>
        </Box>
      )}

      <Box sx={{ mb: 2, borderRadius: 1, border, overflow: 'hidden' }}>
        <Box
          sx={{
            px: 2,
            py: 1.5,
            backgroundColor: panelBg,
            display: 'flex',
            flexWrap: 'wrap',
            gap: 1,
            alignItems: 'center',
          }}
        >
          <Typography variant="subtitle1" fontWeight={600} sx={{ mr: 1 }}>
            Модели
          </Typography>
          <TextField
            size="small"
            placeholder="Поиск моделей"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            sx={{
              flex: 1,
              minWidth: 140,
              '& .MuiOutlinedInput-root': {
                borderRadius: '10px',
                bgcolor: isDark ? DROPDOWN_TRIGGER_BG : 'rgba(0,0,0,0.04)',
                transition: 'background 0.15s, border-color 0.15s',
                '& fieldset': {
                  borderColor: isDark ? 'rgba(255,255,255,0.15)' : 'rgba(0,0,0,0.12)',
                },
                '&:hover': {
                  bgcolor: isDark ? DROPDOWN_TRIGGER_BG_HOVER : 'rgba(0,0,0,0.06)',
                  '& fieldset': {
                    borderColor: isDark ? 'rgba(255,255,255,0.15)' : 'rgba(0,0,0,0.12)',
                  },
                },
                '&.Mui-focused': {
                  bgcolor: isDark ? DROPDOWN_TRIGGER_BG_HOVER : 'rgba(0,0,0,0.06)',
                  '& fieldset': {
                    borderColor: isDark ? 'rgba(255,255,255,0.28)' : 'rgba(0,0,0,0.24)',
                    borderWidth: '1px',
                  },
                },
              },
              '& .MuiOutlinedInput-input': {
                py: 1,
                px: 0,
                fontSize: '0.875rem',
                fontWeight: 500,
                color: isDark ? '#fff' : 'rgba(0,0,0,0.87)',
              },
            }}
            InputProps={{
              startAdornment: (
                <InputAdornment position="start" sx={{ mr: 0.75 }}>
                  <SearchIcon fontSize="small" sx={{ color: isDark ? 'rgba(255,255,255,0.55)' : 'action.active' }} />
                </InputAdornment>
              ),
            }}
          />
          <Box sx={{ width: 140, flexShrink: 0 }}>
            <Box
              onClick={(e) => setTypeFilterAnchor(e.currentTarget)}
              sx={{ ...dropdownTriggerSx, width: '100%', boxSizing: 'border-box' }}
            >
              <Typography sx={{ ...dropdownTriggerTextSx, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {TYPE_OPTIONS.find((t) => t.id === typeFilter)?.label || 'Все типы'}
              </Typography>
              <ExpandMoreIcon
                sx={{
                  ...dropdownChevronSx,
                  transform: typeFilterAnchor ? 'rotate(180deg)' : 'none',
                  flexShrink: 0,
                }}
              />
            </Box>
            <Popover
              open={Boolean(typeFilterAnchor)}
              anchorEl={typeFilterAnchor}
              onClose={() => setTypeFilterAnchor(null)}
              anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
              transformOrigin={{ vertical: 'top', horizontal: 'left' }}
              slotProps={{
                paper: {
                  sx: {
                    ...getDropdownPopoverPaperSx(typeFilterAnchor, isDark),
                    // совпадает с шириной кнопки (не раздувать minWidth=180)
                    width: typeFilterAnchor
                      ? `${typeFilterAnchor.getBoundingClientRect().width}px`
                      : 140,
                    minWidth: typeFilterAnchor
                      ? `${typeFilterAnchor.getBoundingClientRect().width}px`
                      : 140,
                    maxWidth: typeFilterAnchor
                      ? `${typeFilterAnchor.getBoundingClientRect().width}px`
                      : 140,
                  },
                },
              }}
            >
              <Box sx={{ py: 0.5 }}>
                {TYPE_OPTIONS.map((t) => (
                  <Box
                    key={t.id}
                    onClick={() => {
                      setTypeFilter(t.id);
                      setTypeFilterAnchor(null);
                    }}
                    sx={{
                      ...dropdownItemSx,
                      ...getDropdownItemStateSx(isDark, typeFilter === t.id),
                      whiteSpace: 'nowrap',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                    }}
                  >
                    {t.label}
                  </Box>
                ))}
              </Box>
            </Popover>
          </Box>
        </Box>

        {!filtered.length ? (
          <Box sx={{ px: 2, py: 2 }}>
            <Typography variant="body2" color="text.secondary">
              Ничего не найдено
            </Typography>
          </Box>
        ) : (
          groupedSections.map((section) => (
            <Box key={section.id}>
              {section.recommended.length > 0 && (
                <>
                  <Box
                    sx={{
                      px: 2,
                      py: 1,
                      backgroundColor: isDark ? 'rgba(255,255,255,0.04)' : 'rgba(0,0,0,0.03)',
                      borderTop: border,
                    }}
                  >
                    <Typography variant="subtitle2" fontWeight={700}>
                      Рекомендованные · {section.label}
                    </Typography>
                  </Box>
                  <List dense disablePadding>
                    {section.recommended.map((m) => (
                      <ModelCatalogRow
                        key={m.id}
                        model={m}
                        theme={theme}
                        onApply={applyInstalled}
                        onDownload={startCatalog}
                        onDelete={setDeleteTarget}
                      />
                    ))}
                  </List>
                </>
              )}
              {section.other.length > 0 && (
                <>
                  <Box
                    sx={{
                      px: 2,
                      py: 1,
                      backgroundColor: isDark ? 'rgba(255,255,255,0.04)' : 'rgba(0,0,0,0.03)',
                      borderTop: border,
                    }}
                  >
                    <Typography variant="subtitle2" fontWeight={700}>
                      На диске · {section.label}
                    </Typography>
                  </Box>
                  <List dense disablePadding>
                    {section.other.map((m) => (
                      <ModelCatalogRow
                        key={m.id}
                        model={m}
                        theme={theme}
                        onApply={applyInstalled}
                        onDownload={startCatalog}
                        onDelete={setDeleteTarget}
                      />
                    ))}
                  </List>
                </>
              )}
            </Box>
          ))
        )}
      </Box>

      <Dialog
        open={Boolean(deleteTarget)}
        onClose={() => {
          if (!deleteBusy) setDeleteTarget(null);
        }}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>Удалить модель?</DialogTitle>
        <DialogContent>
          <DialogContentText>
            Будут безвозвратно удалены веса и связанные файлы с диска
            {deleteTarget ? (
              <>
                : <strong>{deleteTarget.display_name}</strong>
                {deleteTarget.local_name || deleteTarget.source?.filename
                  ? ` (${deleteTarget.local_name || deleteTarget.source?.filename})`
                  : ''}
              </>
            ) : null}
            . Запись в курируемом каталоге останется — можно скачать снова.
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleteTarget(null)} disabled={deleteBusy}>
            Отмена
          </Button>
          <Button
            color="error"
            variant="contained"
            startIcon={deleteBusy ? <CircularProgress size={14} color="inherit" /> : <DeleteOutlineIcon />}
            disabled={deleteBusy}
            onClick={() => void confirmDeleteInstalled()}
          >
            Удалить с диска
          </Button>
        </DialogActions>
      </Dialog>

      {recentJobs.length > 0 && (
        <Box sx={{ mb: 2 }}>
          <Typography variant="caption" color="text.secondary" sx={{ mb: 0.5, display: 'block' }}>
            Недавние задачи
          </Typography>
          <Stack direction="row" flexWrap="wrap" gap={0.75}>
            {recentJobs.map((j) => (
              <Chip
                key={j.id}
                size="small"
                label={`${j.display_name}: ${j.status}`}
                color={statusColor(j.status)}
                variant="outlined"
              />
            ))}
          </Stack>
        </Box>
      )}

      <Box sx={{ mb: 1, borderRadius: 1, border }}>
        <Button
          fullWidth
          onClick={() => setAdvancedOpen((v) => !v)}
          endIcon={advancedOpen ? <ExpandLessIcon /> : <ExpandMoreIcon />}
          sx={{ justifyContent: 'space-between', px: 2, py: 1.25, textTransform: 'none', color: 'text.primary' }}
        >
          <Typography variant="subtitle1" fontWeight={600}>
            Дополнительно · HF Token {hfConfigured ? `(${hfMasked})` : '(не задан)'}
          </Typography>
        </Button>
        <Collapse in={advancedOpen}>
          <Box sx={{ px: 2, pb: 2 }}>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
              Нужен для gated-моделей (pyannote). Ручная укладка весов в папки по-прежнему работает.
            </Typography>
            <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
              <TextField
                size="small"
                fullWidth
                type="password"
                label="Hugging Face access token"
                value={hfTokenInput}
                onChange={(e) => setHfTokenInput(e.target.value)}
                autoComplete="off"
              />
              <Button variant="contained" onClick={() => void saveToken()} disabled={!hfTokenInput.trim()}>
                Сохранить
              </Button>
              <Button variant="outlined" color="inherit" onClick={() => void clearToken()} disabled={!hfConfigured}>
                Очистить
              </Button>
            </Stack>
          </Box>
        </Collapse>
      </Box>

      <Button variant="outlined" size="small" onClick={() => void refreshAll()}>
        Обновить список
      </Button>
      </CardContent>
    </Card>
  );
}
