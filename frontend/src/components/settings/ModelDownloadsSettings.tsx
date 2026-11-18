import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Collapse,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  Avatar,
  DialogTitle,
  IconButton,
  InputAdornment,
  LinearProgress,
  List,
  ListItem,
  ListItemAvatar,
  ListItemSecondaryAction,
  ListItemText,
  MenuItem,
  Stack,
  TextField,
  Tooltip,
  Typography,
  useTheme,
} from '@mui/material';
import CloudDownloadIcon from '@mui/icons-material/CloudDownload';
import DownloadIcon from '@mui/icons-material/Download';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import CancelIcon from '@mui/icons-material/Cancel';
import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircleOutline';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import ExpandLessIcon from '@mui/icons-material/ExpandLess';
import SearchIcon from '@mui/icons-material/Search';
import SmartToyOutlinedIcon from '@mui/icons-material/SmartToyOutlined';
import { authFetch, getApiUrl } from '../../config/api';
import { MODEL_SETTINGS_SECTION_TITLE_SX } from '../../constants/modelSettingsStyles';

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
  { id: 'llm_gguf', label: 'LLM (GGUF)' },
  { id: 'llm_hf', label: 'LLM (HF)' },
  { id: 'rag_embed', label: 'RAG embed' },
  { id: 'rag_rerank', label: 'RAG rerank' },
  { id: 'stt', label: 'STT' },
  { id: 'tts', label: 'TTS' },
  { id: 'diarization', label: 'Диаризация' },
  { id: 'comfyui', label: 'ComfyUI' },
  { id: 'ocr', label: 'OCR' },
  { id: 'omnivoice', label: 'OmniVoice' },
];

const PULL_TYPES = TYPE_OPTIONS.filter((t) => t.id !== 'all');

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

/** Разбор ввода: org/repo, hf://…, hf download …, hf.co/…, URL, catalog id */
function parsePullInput(raw: string): {
  repo_id?: string;
  url?: string;
  filename?: string;
  catalogHint?: string;
} {
  let s = raw.trim().replace(/^["']|["']$/g, '');
  if (!s) return {};

  // CLI: `hf download hf://org/repo/file.gguf` / `huggingface-cli download org/repo file.gguf`
  s = s
    .replace(/^huggingface-cli\s+download\s+/i, '')
    .replace(/^hf\s+download\s+/i, '')
    .trim();

  if (/^https?:\/\//i.test(s)) {
    try {
      const u = new URL(s);
      const parts = u.pathname.split('/').filter(Boolean);
      if (u.hostname.includes('huggingface.co') && parts.length >= 2) {
        const repo_id = `${parts[0]}/${parts[1]}`;
        const resolveIdx = parts.indexOf('resolve');
        const blobIdx = parts.indexOf('blob');
        const idx = resolveIdx >= 0 ? resolveIdx : blobIdx;
        const filename = idx >= 0 && parts[idx + 2] ? parts.slice(idx + 2).join('/') : undefined;
        return { repo_id, filename };
      }
    } catch {
      /* fallthrough */
    }
    return { url: s };
  }

  // hf://org/repo/file.gguf  или  hf:org/repo/file.gguf
  s = s.replace(/^hf:\/\//i, '').replace(/^hf:/i, '');
  let t = s
    .replace(/^hf\.co\//i, '')
    .replace(/^huggingface\.co\//i, '')
    .trim();

  // org/repo:quant
  const colon = t.indexOf(':');
  if (colon > 0 && !t.slice(0, colon).includes('/')) {
    // редкий случай — пропускаем
  } else if (colon > 0 && t.slice(0, colon).includes('/')) {
    const repo = t.slice(0, colon);
    const quant = t.slice(colon + 1);
    const segs = repo.split('/').filter(Boolean);
    if (segs.length >= 2) {
      return {
        repo_id: `${segs[0]}/${segs[1]}`,
        filename: quant.includes('.') ? quant : undefined,
        catalogHint: quant,
      };
    }
  }

  if (t.includes('/')) {
    const segs = t.split('/').filter(Boolean);
    if (segs.length >= 2) {
      const repo_id = `${segs[0]}/${segs[1]}`;
      const rest = segs.slice(2).join('/');
      // `hf download org/repo file.gguf` → иногда пробел вместо /
      if (!rest && /\s+\S+\.\w+$/.test(t)) {
        const [repoPart, filePart] = t.split(/\s+/, 2);
        const rs = repoPart.split('/').filter(Boolean);
        if (rs.length >= 2 && filePart) {
          return { repo_id: `${rs[0]}/${rs[1]}`, filename: filePart };
        }
      }
      if (rest) {
        return { repo_id, filename: rest };
      }
      return { repo_id };
    }
  }

  // `org/repo file.gguf` (пробел)
  const spaceParts = t.split(/\s+/).filter(Boolean);
  if (spaceParts.length >= 2 && spaceParts[0].includes('/')) {
    const segs = spaceParts[0].split('/').filter(Boolean);
    if (segs.length >= 2) {
      return { repo_id: `${segs[0]}/${segs[1]}`, filename: spaceParts[1] };
    }
  }

  return { catalogHint: t };
}

const avatarCache = new Map<string, string | null>();

function ModelAvatar({ model }: { model: CatalogModel }) {
  const [src, setSrc] = useState<string | undefined>(model.avatar_url);
  const repoId = model.source?.repo_id;

  useEffect(() => {
    setSrc(model.avatar_url);
  }, [model.avatar_url, model.id]);

  useEffect(() => {
    if (src || !repoId || model.source?.kind === 'local') return;
    if (avatarCache.has(repoId)) {
      const cached = avatarCache.get(repoId);
      if (cached) setSrc(cached);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const res = await authFetch(
          getApiUrl(`/api/model-downloads/avatar?repo_id=${encodeURIComponent(repoId)}`),
        );
        if (!res.ok) {
          avatarCache.set(repoId, null);
          return;
        }
        const data = await res.json();
        const url = typeof data.avatar_url === 'string' ? data.avatar_url : null;
        avatarCache.set(repoId, url);
        if (!cancelled && url) setSrc(url);
      } catch {
        avatarCache.set(repoId, null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [src, repoId, model.source?.kind]);

  return (
    <Avatar
      src={src}
      alt=""
      variant="rounded"
      sx={{ width: 36, height: 36, bgcolor: 'action.hover' }}
    >
      <SmartToyOutlinedIcon fontSize="small" />
    </Avatar>
  );
}

export default function ModelDownloadsSettings() {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';

  const [catalog, setCatalog] = useState<CatalogModel[]>([]);
  const [jobs, setJobs] = useState<DownloadJob[]>([]);
  const [typeFilter, setTypeFilter] = useState('all');
  const [search, setSearch] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const [pullQuery, setPullQuery] = useState('');
  const [pullType, setPullType] = useState('llm_gguf');
  const [pullBusy, setPullBusy] = useState(false);

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

  const pullModel = async () => {
    const raw = pullQuery.trim();
    if (!raw) return;
    setPullBusy(true);
    setError(null);
    setInfo(null);
    try {
      // 1) точное совпадение с catalog id / display / repo
      const lower = raw.toLowerCase();
      const hit = catalog.find(
        (m) =>
          m.id.toLowerCase() === lower
          || m.display_name.toLowerCase() === lower
          || (m.source?.repo_id || '').toLowerCase() === lower
          || (m.local_name || '').toLowerCase() === lower,
      );
      if (hit) {
        await startCatalog(hit.id);
        setPullQuery('');
        return;
      }

      const parsed = parsePullInput(raw);
      if (parsed.catalogHint && !parsed.repo_id && !parsed.url) {
        const soft = catalog.find(
          (m) =>
            m.id.toLowerCase().includes(parsed.catalogHint!.toLowerCase())
            || (m.local_name || '').toLowerCase().includes(parsed.catalogHint!.toLowerCase()),
        );
        if (soft) {
          await startCatalog(soft.id);
          setPullQuery('');
          return;
        }
      }

      const inferredType =
        (parsed.filename || '').toLowerCase().endsWith('.gguf')
        || (parsed.repo_id || '').toLowerCase().includes('gguf')
          ? 'llm_gguf'
          : pullType;

      const body: Record<string, unknown> = {
        type: inferredType,
        apply: true,
      };
      if (parsed.repo_id) body.repo_id = parsed.repo_id;
      if (parsed.url) body.url = parsed.url;
      if (parsed.filename) {
        body.filename = parsed.filename;
        body.local_name = parsed.filename.split('/').pop();
      }
      if (parsed.repo_id && !parsed.filename && pullType === 'llm_gguf') {
        // без filename — попробуем список файлов
        const filesRes = await authFetch(
          getApiUrl(`/api/model-downloads/hf-files?repo_id=${encodeURIComponent(parsed.repo_id)}`),
        );
        if (filesRes.ok) {
          const filesData = await filesRes.json();
          const gguf: string[] = Array.isArray(filesData.gguf) ? filesData.gguf : [];
          if (gguf.length === 1) {
            body.filename = gguf[0];
            body.local_name = gguf[0].split('/').pop();
          } else if (gguf.length > 1) {
            setError(
              `В репозитории несколько GGUF (${gguf.length}). Укажите файл, например: ${parsed.repo_id}/${gguf[0]}`,
            );
            return;
          }
        }
      }
      if (pullType === 'stt') body.stt_size = 'medium';
      if (pullType.startsWith('rag') && parsed.repo_id) {
        body.local_name = parsed.repo_id.split('/').pop();
      }

      if (!body.repo_id && !body.url) {
        setError('Не удалось разобрать модель. Пример: unsloth/Qwen3.5-4B-GGUF или id из каталога');
        return;
      }

      const res = await authFetch(getApiUrl('/api/model-downloads/start'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        setError(await readErrorDetail(res, `Ошибка ${res.status}`));
        return;
      }
      setInfo('Загрузка запущена');
      setPullQuery('');
      await loadJobs();
    } finally {
      setPullBusy(false);
    }
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
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2.5, maxWidth: 920 }}>
      <Box>
        <Typography variant="h6" sx={{ ...MODEL_SETTINGS_SECTION_TITLE_SX, display: 'flex', alignItems: 'center', gap: 1 }}>
          <CloudDownloadIcon fontSize="small" /> Manage Models
        </Typography>
        <Typography variant="body2" color="text.secondary">
          Скачайте модель из каталога или укажите Hugging Face repo — как Pull в Open WebUI.
        </Typography>
      </Box>

      {error && (
        <Alert severity="error" onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      {info && (
        <Alert severity="success" onClose={() => setInfo(null)}>
          {info}
        </Alert>
      )}
      {loading && <LinearProgress />}

      {/* Pull bar — главный паттерн Open WebUI */}
      <Box
        sx={{
          p: 2,
          borderRadius: 2,
          border,
          backgroundColor: panelBg,
        }}
      >
        <Typography variant="subtitle2" sx={{ mb: 1.25, fontWeight: 600 }}>
          Pull a model from Hugging Face
        </Typography>
        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} alignItems={{ sm: 'stretch' }}>
          <TextField
            select
            size="small"
            label="Тип"
            value={pullType}
            onChange={(e) => setPullType(String(e.target.value))}
            sx={{ minWidth: { sm: 150 } }}
          >
            {PULL_TYPES.map((t) => (
              <MenuItem key={t.id} value={t.id}>
                {t.label}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            size="small"
            fullWidth
            placeholder="hf://org/repo/file.gguf · org/repo · URL · id из каталога"
            value={pullQuery}
            onChange={(e) => setPullQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void pullModel();
            }}
            InputProps={{
              endAdornment: (
                <InputAdornment position="end">
                  <Tooltip title="Скачать">
                    <span>
                      <IconButton
                        color="primary"
                        edge="end"
                        disabled={!pullQuery.trim() || pullBusy}
                        onClick={() => void pullModel()}
                        aria-label="pull model"
                      >
                        {pullBusy ? <CircularProgress size={18} /> : <DownloadIcon />}
                      </IconButton>
                    </span>
                  </Tooltip>
                </InputAdornment>
              ),
            }}
          />
        </Stack>
        <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: 'block' }}>
          Примеры:{' '}
          <code>hf://unsloth/Qwen3.8-27B-GGUF/Qwen3.8-27B-UD-IQ4_XS.gguf</code>,{' '}
          <code>ai-forever/FRIDA</code>
        </Typography>
      </Box>

      {/* Active downloads */}
      {activeJobs.length > 0 && (
        <Box sx={{ borderRadius: 2, border, overflow: 'hidden' }}>
          <Box sx={{ px: 2, py: 1.25, backgroundColor: panelBg }}>
            <Typography variant="subtitle2" fontWeight={600}>
              Downloads
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

      {/* Catalog / installed list */}
      <Box sx={{ borderRadius: 2, border, overflow: 'hidden' }}>
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
          <Typography variant="subtitle2" fontWeight={600} sx={{ mr: 1 }}>
            Models
          </Typography>
          <TextField
            size="small"
            placeholder="Search models"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            sx={{ minWidth: 180, flex: 1 }}
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <SearchIcon fontSize="small" color="action" />
                </InputAdornment>
              ),
            }}
          />
          <TextField
            select
            size="small"
            value={typeFilter}
            onChange={(e) => setTypeFilter(String(e.target.value))}
            sx={{ minWidth: 140 }}
          >
            {TYPE_OPTIONS.map((t) => (
              <MenuItem key={t.id} value={t.id}>
                {t.label}
              </MenuItem>
            ))}
          </TextField>
        </Box>

        <List dense disablePadding>
          {filtered.map((m) => (
            <ListItem
              key={m.id}
              divider
              sx={{
                py: 1.25,
                '&:hover': { backgroundColor: theme.palette.action.hover },
              }}
            >
              <ListItemAvatar sx={{ minWidth: 48 }}>
                <ModelAvatar model={m} />
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
                        label="installed"
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
                  <Button size="small" variant="text" onClick={() => void applyInstalled(m.id)}>
                    Apply
                  </Button>
                )}
                {m.source?.kind !== 'local' && (
                  <Button
                    size="small"
                    variant={m.installed ? 'outlined' : 'contained'}
                    startIcon={<DownloadIcon />}
                    onClick={() => void startCatalog(m.id)}
                  >
                    {m.installed ? 'Re-pull' : 'Pull'}
                  </Button>
                )}
                {m.installed && (
                  <Tooltip title="Удалить с диска">
                    <IconButton
                      size="small"
                      color="error"
                      aria-label="Удалить модель"
                      onClick={() => setDeleteTarget(m)}
                    >
                      <DeleteOutlineIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                )}
              </Stack>
            </ListItem>
          ))}
          {!filtered.length && (
            <ListItem>
              <ListItemText
                primary={
                  <Typography variant="body2" color="text.secondary">
                    Ничего не найдено
                  </Typography>
                }
              />
            </ListItem>
          )}
        </List>
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
        <Box>
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

      {/* Advanced: HF token */}
      <Box sx={{ borderRadius: 2, border }}>
        <Button
          fullWidth
          onClick={() => setAdvancedOpen((v) => !v)}
          endIcon={advancedOpen ? <ExpandLessIcon /> : <ExpandMoreIcon />}
          sx={{ justifyContent: 'space-between', px: 2, py: 1.25, textTransform: 'none', color: 'text.primary' }}
        >
          <Typography variant="subtitle2" fontWeight={600}>
            Advanced · HF Token {hfConfigured ? `(${hfMasked})` : '(не задан)'}
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
                Save
              </Button>
              <Button variant="outlined" color="inherit" onClick={() => void clearToken()} disabled={!hfConfigured}>
                Clear
              </Button>
            </Stack>
          </Box>
        </Collapse>
      </Box>

      <Button variant="text" size="small" onClick={() => void refreshAll()} sx={{ alignSelf: 'flex-start' }}>
        Обновить список
      </Button>
    </Box>
  );
}
