import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
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
  Divider,
  FormControlLabel,
  IconButton,
  LinearProgress,
  List,
  ListItemButton,
  ListItemText,
  Stack,
  Switch,
  Tab,
  Tabs,
  TextField,
  Typography,
  alpha,
  useTheme,
} from '@mui/material';
import {
  ArrowBack,
  DeleteOutline,
  FiberManualRecord,
  Pause,
  PersonOutline,
  PlayArrow,
  Refresh,
  RestoreFromTrash,
  SettingsOutlined,
  Stop,
  Search as SearchIcon,
} from '@mui/icons-material';
import { useAppActions } from '../contexts/AppContext';
import { dispatchInsertChatText } from '../components/right_bar/TranscriptionSidebarSection';
import * as api from './api';
import type { LiveSegment, LiveSessionCard, LiveSessionDetail } from './types';
import type { LiveSessionPrefs } from './api';
import { useDualTrackCapture } from './useDualTrackCapture';
import {
  getWorkZoneBackgroundColor,
  getWorkZoneCustomImage,
  isWorkZoneAnimatedMode,
} from '../constants/workZoneBackground';
import { useWorkZoneBgMode } from '../hooks/useWorkZoneBgMode';
import WorkZoneStarrySky from '../components/WorkZoneStarrySky';
import WorkZoneSnowfall from '../components/WorkZoneSnowfall';
import {
  getSidebarPanelBackground,
  isSidebarPanelLight,
} from '../constants/sidebarPanelColor';

/** Секция рабочей зоны без «карточки»: заголовок + поток контента. */
function WorkspaceSection({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <Box sx={{ pt: 2.5 }}>
      <Stack direction="row" spacing={1} alignItems="baseline" sx={{ mb: 1 }}>
        <Typography
          variant="overline"
          sx={{ letterSpacing: '0.08em', color: 'text.secondary', lineHeight: 1.2 }}
        >
          {title}
        </Typography>
        {hint}
      </Stack>
      {children}
    </Box>
  );
}

function mergeSegmentTexts(a: string, b: string): string {
  const at = (a || '').trim();
  const bt = (b || '').trim();
  if (!at) return bt;
  if (!bt) return at;
  if (bt.startsWith(at)) return bt;
  if (at.endsWith(bt)) return at;
  const aWords = at.split(/\s+/);
  const bWords = bt.split(/\s+/);
  let best = 0;
  const max = Math.min(aWords.length, bWords.length);
  for (let n = max; n >= 1; n -= 1) {
    if (
      aWords.slice(-n).join(' ').toLowerCase() ===
      bWords.slice(0, n).join(' ').toLowerCase()
    ) {
      best = n;
      break;
    }
  }
  if (best > 0) return [...aWords, ...bWords.slice(best)].join(' ');
  return `${at} ${bt}`;
}

/** Склеивает selected + live по id и схлопывает соседние куски одного трека (overlap STT). */
function buildDisplaySegments(
  selectedSegs: LiveSegment[] | undefined,
  liveSegs: LiveSegment[],
): LiveSegment[] {
  const byId = new Map<string, LiveSegment>();
  for (const s of selectedSegs || []) {
    if (s?.id) byId.set(s.id, s);
  }
  for (const s of liveSegs) {
    if (s?.id) byId.set(s.id, s);
  }
  const sorted = Array.from(byId.values()).sort(
    (a, b) => a.start_ms - b.start_ms || a.end_ms - b.end_ms,
  );
  const out: LiveSegment[] = [];
  for (const s of sorted) {
    const prev = out[out.length - 1];
    const sameTrack = prev && prev.track === s.track;
    const close = prev && s.start_ms - prev.end_ms < 1800;
    const sameSpeaker =
      prev &&
      (prev.speaker || '') === (s.speaker || '') &&
      Boolean(prev.speaker || s.speaker);
    if (prev && sameTrack && close && (sameSpeaker || (!prev.speaker && !s.speaker))) {
      prev.text = mergeSegmentTexts(prev.text, s.text);
      prev.end_ms = Math.max(prev.end_ms, s.end_ms);
      prev.is_echo = Boolean(prev.is_echo && s.is_echo);
    } else {
      out.push({ ...s });
    }
  }
  return out;
}

function normalizeSpeakerLabel(raw: string): string {
  let s = (raw || '').trim();
  while (/^SPEAKER_SPEAKER_/i.test(s)) {
    s = `SPEAKER_${s.slice('SPEAKER_SPEAKER_'.length)}`;
  }
  return s;
}

function collectSpeakerIds(segments: LiveSegment[]): string[] {
  const ids = new Set<string>();
  for (const s of segments) {
    const sp = normalizeSpeakerLabel(s.speaker || '');
    if (!sp) continue;
    if (sp === 'Вы' || sp === 'Собеседник') continue;
    ids.add(sp);
  }
  return Array.from(ids).sort();
}

/** Сайдбар шлёт это, если уже на /live-sessions — чтобы стартнуть запись без remount. */
export const ASTRA_LIVE_SESSION_START = 'astrachat:live-session-start';

function formatDuration(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(s / 60);
  const sec = s % 60;
  const h = Math.floor(m / 60);
  if (h > 0) return `${h}:${String(m % 60).padStart(2, '0')}:${String(sec).padStart(2, '0')}`;
  return `${m}:${String(sec).padStart(2, '0')}`;
}

function statusLabel(status: string): string {
  switch (status) {
    case 'live':
      return 'Идёт запись';
    case 'paused':
      return 'Пауза';
    case 'processing':
      return 'Обработка';
    case 'ready':
      return 'Готово';
    case 'archived':
      return 'В корзине';
    default:
      return 'Черновик';
  }
}

function LevelBar({ value, label }: { value: number; label: string }) {
  const pct = Math.min(100, Math.round(value * 220));
  return (
    <Box sx={{ minWidth: 120 }}>
      <Typography variant="caption" color="text.secondary">
        {label}
      </Typography>
      <LinearProgress
        variant="determinate"
        value={pct}
        sx={{ height: 8, borderRadius: 1, bgcolor: 'action.hover' }}
      />
    </Box>
  );
}

const DEFAULT_PREFS: LiveSessionPrefs = {
  echo_detect: true,
  auto_stop_on_capture_end: true,
  auto_stop_on_remote_silence_sec: 0,
  jargon: [],
  show_echo_segments: false,
};

export default function LiveSessionsPage() {
  const { showNotification } = useAppActions();
  const notifyRef = useRef(showNotification);
  notifyRef.current = showNotification;
  const [searchParams, setSearchParams] = useSearchParams();
  const [tab, setTab] = useState(0);
  const [items, setItems] = useState<LiveSessionCard[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<LiveSessionDetail | null>(null);
  const [titleDraft, setTitleDraft] = useState('');
  const [liveSegments, setLiveSegments] = useState<LiveSegment[]>([]);
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [prefsOpen, setPrefsOpen] = useState(false);
  const [prefs, setPrefs] = useState<LiveSessionPrefs>(DEFAULT_PREFS);
  const [jargonText, setJargonText] = useState('');
  const [guideStep, setGuideStep] = useState(0);
  const [speakerNames, setSpeakerNames] = useState<Record<string, string>>({});
  const [speakerEditorOpen, setSpeakerEditorOpen] = useState(false);
  const autoStartedRef = useRef(false);
  const finishingRef = useRef(false);
  const startingRef = useRef(false);
  const selectedIdRef = useRef<string | null>(null);
  const transcriptEndRef = useRef<HTMLDivElement | null>(null);
  const captureStartRef = useRef<(sessionId: string) => Promise<void>>(async () => undefined);
  const captureStopRef = useRef<() => Promise<{
    localBlob: Blob | null;
    remoteBlob: Blob | null;
    mixedBlob: Blob | null;
    durationMs: number;
  }>>(async () => ({
    localBlob: null,
    remoteBlob: null,
    mixedBlob: null,
    durationMs: 0,
  }));
  const prefsRef = useRef(prefs);
  prefsRef.current = prefs;

  const loadList = useCallback(async () => {
    setLoading(true);
    try {
      const list = await api.listSessions({
        q: query.trim() || undefined,
        trashed: tab === 1,
        limit: 80,
      });
      setItems(list);
    } catch (e) {
      notifyRef.current('error', e instanceof Error ? e.message : 'Не удалось загрузить сессии');
    } finally {
      setLoading(false);
    }
  }, [query, tab]);

  useEffect(() => {
    void loadList();
  }, [loadList]);

  useEffect(() => {
    void api.getPrefs().then((p) => {
      setPrefs({ ...DEFAULT_PREFS, ...p });
      setJargonText((p.jargon || []).join(', '));
    }).catch(() => undefined);
  }, []);

  const finishCapture = useCallback(async () => {
    if (!selectedIdRef.current || finishingRef.current) return;
    finishingRef.current = true;
    setBusy(true);
    setGuideStep(3);
    try {
      const stopped = await captureStopRef.current();
      notifyRef.current('info', 'Финализация: WhisperX + диаризация + бриф…');
      const ready = await api.finalizeSession({
        sessionId: selectedIdRef.current,
        localBlob: stopped.localBlob,
        remoteBlob: stopped.remoteBlob,
        mixedBlob: stopped.mixedBlob,
        durationMs: stopped.durationMs,
        generateBrief: true,
      });
      setSelected(ready);
      setTitleDraft(ready.title || '');
      selectedIdRef.current = ready.id;
      setLiveSegments(ready.segments || []);
      setGuideStep(4);
      notifyRef.current('success', 'Сессия готова: стенограмма и бриф сохранены');
      await loadList();
    } catch (e) {
      notifyRef.current('error', e instanceof Error ? e.message : 'Ошибка финализации');
    } finally {
      setBusy(false);
      finishingRef.current = false;
    }
  }, [loadList]);

  const finishCaptureRef = useRef(finishCapture);
  finishCaptureRef.current = finishCapture;

  const upsertSegment = useCallback((seg: LiveSegment) => {
    setLiveSegments((prev) => {
      const idx = prev.findIndex((x) => x.id === seg.id);
      if (idx >= 0) {
        const next = prev.slice();
        next[idx] = seg;
        return next;
      }
      return [...prev, seg];
    });
    setSelected((s) => {
      if (!s) return s;
      const segs = s.segments || [];
      const idx = segs.findIndex((x) => x.id === seg.id);
      const nextSegs =
        idx >= 0
          ? segs.map((x, i) => (i === idx ? seg : x))
          : [...segs, seg];
      return { ...s, segments: nextSegs };
    });
  }, []);

  const capture = useDualTrackCapture({
    jargon: prefs.jargon,
    autoStopOnCaptureEnd: prefs.auto_stop_on_capture_end,
    remoteSilenceSec: prefs.auto_stop_on_remote_silence_sec || 0,
    onSegment: (seg) => upsertSegment(seg),
    onError: (msg) => notifyRef.current('error', msg),
    onWarn: (msg) => notifyRef.current('warning', msg),
    onAutoStop: (reason) => {
      notifyRef.current(
        'info',
        reason === 'remote_silence'
          ? 'Тишина на системном звуке — останавливаем запись'
          : 'Шаринг экрана/звука завершён — останавливаем запись',
      );
      void finishCaptureRef.current();
    },
  });

  captureStopRef.current = capture.stop;
  captureStartRef.current = capture.start;

  const recording = capture.phase === 'live' || capture.phase === 'paused';

  // Пока идёт запись — подтягиваем сегменты с сервера (на случай гонок upload/UI)
  useEffect(() => {
    if (!recording || !selectedIdRef.current) return undefined;
    let cancelled = false;
    const tick = async () => {
      const id = selectedIdRef.current;
      if (!id || cancelled) return;
      try {
        const detail = await api.getSession(id);
        if (cancelled || selectedIdRef.current !== id) return;
        const segs = detail.segments || [];
        setLiveSegments((prev) => {
          const byId = new Map(prev.map((s) => [s.id, s]));
          for (const s of segs) byId.set(s.id, s);
          return Array.from(byId.values()).sort((a, b) => a.start_ms - b.start_ms);
        });
        setSelected((s) =>
          s && s.id === id
            ? {
                ...s,
                status: detail.status,
                segments: segs.length >= (s.segments?.length || 0) ? segs : s.segments,
              }
            : s,
        );
      } catch {
        /* ignore poll errors */
      }
    };
    const timer = window.setInterval(() => void tick(), 2500);
    void tick();
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [recording]);

  const beginCapture = useCallback(async () => {
    if (startingRef.current || finishingRef.current) return;
    if (capture.phase === 'live' || capture.phase === 'paused' || capture.phase === 'requesting') {
      return;
    }
    startingRef.current = true;
    setBusy(true);
    setGuideStep(1);
    try {
      const created = await api.createSession({
        title: 'Новая сессия',
        language: 'auto',
        source_app: 'browser',
      });
      await api.startSession(created.id);
      setSelected(created);
      setTitleDraft(created.title || 'Новая сессия');
      selectedIdRef.current = created.id;
      setLiveSegments([]);
      setGuideStep(2);
      notifyRef.current(
        'info',
        'Шаг 1/2: разрешите микрофон. Шаг 2/2: выберите экран или вкладку Zoom/Meet/Teams со звуком.',
      );
      await captureStartRef.current(created.id);
      setGuideStep(2);
      notifyRef.current('success', 'Запись идёт. Говорите — стенограмма появится справа.');
      await loadList();
    } catch (e) {
      setGuideStep(0);
      notifyRef.current('error', e instanceof Error ? e.message : 'Не удалось начать запись');
    } finally {
      setBusy(false);
      startingRef.current = false;
    }
  }, [capture.phase, loadList]);

  const beginCaptureRef = useRef(beginCapture);
  beginCaptureRef.current = beginCapture;

  // Кнопка «Живая запись» из сайдбара → /live-sessions?start=1
  useEffect(() => {
    if (searchParams.get('start') !== '1') return;
    if (autoStartedRef.current) return;
    autoStartedRef.current = true;
    setSearchParams({}, { replace: true });
    void beginCaptureRef.current();
  }, [searchParams, setSearchParams]);

  // Повторный клик «Живая запись», когда уже на этой странице
  useEffect(() => {
    const onStart = () => {
      autoStartedRef.current = true;
      void beginCaptureRef.current();
    };
    window.addEventListener(ASTRA_LIVE_SESSION_START, onStart);
    return () => window.removeEventListener(ASTRA_LIVE_SESSION_START, onStart);
  }, []);

  const openSession = async (id: string) => {
    try {
      const detail = await api.getSession(id);
      setSelected(detail);
      setTitleDraft(detail.title || '');
      selectedIdRef.current = detail.id;
      setLiveSegments(detail.segments || []);
      setGuideStep(detail.status === 'ready' ? 4 : 0);
    } catch (e) {
      notifyRef.current('error', e instanceof Error ? e.message : 'Сессия не найдена');
    }
  };

  const togglePause = async () => {
    if (!selected) return;
    try {
      if (capture.phase === 'live') {
        capture.pause();
        await api.pauseSession(selected.id);
      } else if (capture.phase === 'paused') {
        capture.resume();
        await api.resumeSession(selected.id);
      }
    } catch (e) {
      notifyRef.current('error', e instanceof Error ? e.message : 'Ошибка паузы');
    }
  };

  const savePrefs = async () => {
    try {
      const next = await api.savePrefs({
        echo_detect: prefs.echo_detect,
        auto_stop_on_capture_end: prefs.auto_stop_on_capture_end,
        auto_stop_on_remote_silence_sec: prefs.auto_stop_on_remote_silence_sec,
        show_echo_segments: prefs.show_echo_segments,
        jargon: jargonText
          .split(/[,;]/)
          .map((x) => x.trim())
          .filter(Boolean),
      });
      setPrefs(next);
      setJargonText((next.jargon || []).join(', '));
      setPrefsOpen(false);
      notifyRef.current('success', 'Настройки живых сессий сохранены');
    } catch (e) {
      notifyRef.current('error', e instanceof Error ? e.message : 'Не удалось сохранить настройки');
    }
  };

  const displaySegments = useMemo(
    () => buildDisplaySegments(selected?.segments, liveSegments),
    [liveSegments, selected?.segments],
  );

  const transcriptText = useMemo(() => {
    const segs = displaySegments.filter((s) => {
      if (!s.text?.trim()) return false;
      if (s.is_echo && !prefs.show_echo_segments) return false;
      return true;
    });
    return segs
      .map((s) => {
        const rawWho = normalizeSpeakerLabel(
          s.speaker || (s.track === 'local' ? 'Вы' : 'Собеседник'),
        );
        const who =
          speakerNames[rawWho]?.trim() ||
          speakerNames[s.speaker || '']?.trim() ||
          rawWho;
        const echo = s.is_echo ? ' [эхо]' : '';
        return `[${formatDuration(s.start_ms)}] ${who}${echo}: ${s.text}`;
      })
      .join('\n');
  }, [displaySegments, prefs.show_echo_segments, speakerNames]);

  const speakerIds = useMemo(
    () => collectSpeakerIds(displaySegments),
    [displaySegments],
  );

  useEffect(() => {
    transcriptEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [transcriptText, recording]);

  // Подтянуть сохранённую карту имён при открытии сессии
  useEffect(() => {
    const map = (selected?.metadata as { speaker_map?: Record<string, string> } | undefined)
      ?.speaker_map;
    if (map && typeof map === 'object') {
      setSpeakerNames((prev) => ({ ...map, ...prev }));
    }
  }, [selected?.id]);

  const theme = useTheme();
  const isDarkMode = theme.palette.mode === 'dark';
  const borderSubtle = alpha(theme.palette.divider, theme.palette.mode === 'dark' ? 0.5 : 1);
  const workZoneMode = useWorkZoneBgMode();
  const workZoneAnimated = isWorkZoneAnimatedMode(workZoneMode);
  const workZoneBgColor = getWorkZoneBackgroundColor(isDarkMode, workZoneMode);
  const workZoneCustomImage = getWorkZoneCustomImage();
  const sessionsPanelBg = getSidebarPanelBackground(isDarkMode);
  const sessionsPanelText = isSidebarPanelLight(sessionsPanelBg)
    ? 'rgba(0,0,0,0.88)'
    : 'rgba(255,255,255,0.92)';

  return (
    <Box
      sx={{
        display: 'flex',
        flexDirection: { xs: 'column', md: 'row' },
        flex: 1,
        minHeight: 0,
        height: '100%',
        overflow: 'hidden',
        // Не залезать под свёрнутый rail левой/правой панели (main margin ±64px).
        pl: 'var(--left-sidebar-rail-overlap, 0px)',
        pr: 'var(--right-sidebar-rail-overlap, 0px)',
        backgroundColor: workZoneBgColor,
        ...(workZoneMode === 'custom' && workZoneCustomImage
          ? {
              backgroundImage: `url("${workZoneCustomImage}")`,
              backgroundSize: 'cover',
              backgroundPosition: 'center',
              backgroundRepeat: 'no-repeat',
            }
          : {}),
        position: 'relative',
        color: isDarkMode ? 'white' : '#333',
      }}
    >
      {workZoneMode === 'starry' ? <WorkZoneStarrySky isDarkMode={isDarkMode} /> : null}
      {workZoneMode === 'snowfall' ? <WorkZoneSnowfall isDarkMode={isDarkMode} /> : null}

      {/* Список сессий — как колонка чатов, без Paper */}
      <Box
        sx={{
          width: { xs: '100%', md: 300 },
          minWidth: { md: 280 },
          flexShrink: 0,
          display: 'flex',
          flexDirection: 'column',
          borderRight: { md: `1px solid ${borderSubtle}` },
          borderBottom: { xs: `1px solid ${borderSubtle}`, md: 'none' },
          bgcolor: sessionsPanelBg,
          color: sessionsPanelText,
          maxHeight: { xs: 280, md: 'none' },
          zIndex: 1,
          overflow: 'hidden',
        }}
      >
        <Box sx={{ px: 1.5, pt: 1.5, pb: 0.5, minWidth: 0 }}>
          <Stack direction="row" alignItems="center" spacing={0.5} sx={{ mb: 1, minWidth: 0 }}>
            <Button
              size="small"
              startIcon={<ArrowBack fontSize="small" />}
              onClick={() => window.location.assign('/')}
              sx={{
                color: 'inherit',
                flexShrink: 0,
                textTransform: 'none',
                minWidth: 0,
                px: 0.75,
                opacity: 0.85,
                '&:hover': { opacity: 1, bgcolor: alpha(theme.palette.common.white, 0.08) },
              }}
            >
              К чату
            </Button>
            <Typography
              variant="subtitle1"
              sx={{ fontWeight: 600, flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
            >
              Живые сессии
            </Typography>
            <IconButton
              size="small"
              onClick={() => setPrefsOpen(true)}
              title="Настройки"
              sx={{ color: 'inherit', flexShrink: 0 }}
            >
              <SettingsOutlined fontSize="small" />
            </IconButton>
          </Stack>
          <Tabs
            value={tab}
            onChange={(_, v) => setTab(v)}
            variant="fullWidth"
            sx={{
              minHeight: 36,
              minWidth: 0,
              '& .MuiTab-root': {
                minHeight: 36,
                py: 0.5,
                px: 0.5,
                fontSize: '0.8rem',
                minWidth: 0,
              },
            }}
          >
            <Tab label="Архив" />
            <Tab label="Корзина" />
          </Tabs>
          <TextField
            size="small"
            fullWidth
            placeholder="Поиск…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            InputProps={{
              startAdornment: <SearchIcon fontSize="small" sx={{ mr: 1, opacity: 0.5 }} />,
            }}
            sx={{
              mt: 1,
              '& .MuiOutlinedInput-root': {
                bgcolor: alpha(theme.palette.common.white, isSidebarPanelLight(sessionsPanelBg) ? 0.55 : 0.08),
                color: 'inherit',
                '& fieldset': { borderColor: 'transparent' },
                '&:hover fieldset': { borderColor: borderSubtle },
              },
            }}
          />
        </Box>
        <Box sx={{ flex: 1, overflow: 'auto', py: 0.5, minWidth: 0 }}>
          {loading ? (
            <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
              <CircularProgress size={24} />
            </Box>
          ) : (
            <List dense disablePadding sx={{ minWidth: 0 }}>
              {items.map((item) => (
                <ListItemButton
                  key={item.id}
                  selected={selected?.id === item.id}
                  onClick={() => void openSession(item.id)}
                  sx={{ minWidth: 0, alignItems: 'flex-start' }}
                >
                  <ListItemText
                    primary={item.title}
                    secondary={`${statusLabel(item.status)} · ${formatDuration(item.duration_ms)}`}
                    primaryTypographyProps={{
                      noWrap: true,
                      fontSize: '0.9rem',
                      sx: { overflow: 'hidden', textOverflow: 'ellipsis' },
                    }}
                    secondaryTypographyProps={{
                      noWrap: true,
                      fontSize: '0.75rem',
                      sx: { overflow: 'hidden', textOverflow: 'ellipsis' },
                    }}
                    sx={{ my: 0, minWidth: 0 }}
                  />
                </ListItemButton>
              ))}
              {!items.length && (
                <Typography sx={{ px: 2.5, py: 2, fontSize: '0.85rem', opacity: 0.7 }}>
                  Пока пусто — нажмите «Начать запись».
                </Typography>
              )}
            </List>
          )}
        </Box>
        <Box sx={{ p: 1.5, borderTop: `1px solid ${borderSubtle}` }}>
          <Button
            fullWidth
            variant="contained"
            color="error"
            startIcon={<FiberManualRecord />}
            disabled={busy || recording}
            onClick={() => void beginCapture()}
          >
            Начать запись
          </Button>
        </Box>
      </Box>

      {/* Рабочая зона — тот же фон, что у чатов */}
      <Box
        sx={{
          flex: 1,
          minWidth: 0,
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
          position: 'relative',
          zIndex: workZoneAnimated ? 1 : 0,
          backgroundColor: workZoneAnimated || workZoneMode === 'custom' ? 'transparent' : workZoneBgColor,
        }}
      >
        {recording && (
          <Box
            sx={{
              px: { xs: 2, md: 3 },
              py: 1.5,
              borderBottom: `1px solid ${alpha(theme.palette.error.main, 0.35)}`,
              bgcolor: alpha(theme.palette.error.main, theme.palette.mode === 'dark' ? 0.08 : 0.04),
            }}
          >
            <Stack
              direction={{ xs: 'column', sm: 'row' }}
              spacing={2}
              alignItems={{ xs: 'stretch', sm: 'center' }}
            >
              <Chip
                color="error"
                label={capture.phase === 'paused' ? 'Пауза' : 'REC'}
                size="small"
              />
              <Typography sx={{ fontVariantNumeric: 'tabular-nums', minWidth: 72 }}>
                {formatDuration(capture.elapsedMs)}
              </Typography>
              <LevelBar value={capture.levels.local} label="Микрофон" />
              <LevelBar
                value={capture.levels.remote}
                label={capture.hasRemote ? 'Системный звук' : 'Нет remote'}
              />
              <Box sx={{ flex: 1 }} />
              <Button
                startIcon={capture.phase === 'paused' ? <PlayArrow /> : <Pause />}
                onClick={() => void togglePause()}
                disabled={busy}
              >
                {capture.phase === 'paused' ? 'Продолжить' : 'Пауза'}
              </Button>
              <Button
                variant="contained"
                color="inherit"
                startIcon={<Stop />}
                onClick={() => void finishCapture()}
                disabled={busy}
              >
                Стоп и обработать
              </Button>
            </Stack>
            {!capture.hasRemote && (
              <Typography variant="caption" color="warning.main" sx={{ display: 'block', mt: 1 }}>
                Звук собеседника не захвачен — шарьте вкладку звонка с галочкой звука или весь экран
                (не окно Zoom/Teams).
              </Typography>
            )}
          </Box>
        )}

        <Box sx={{ flex: 1, overflow: 'auto', px: { xs: 2, md: 4 }, py: { xs: 2, md: 3 } }}>
          {!recording && guideStep < 3 && !selected && (
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{ mb: 3, maxWidth: 560, lineHeight: 1.55 }}
            >
              Запись звонка без бота: микрофон + звук вкладки/экрана → стенограмма → бриф.
              Нажмите «Начать запись», разрешите микрофон и шарьте вкладку собеседника со звуком
              (Chrome/Edge). Файлы и YouTube — в панели «Транскрибация».
            </Typography>
          )}

          {!selected ? (
            <Box sx={{ py: { xs: 4, md: 10 }, maxWidth: 480 }}>
              <Typography variant="h5" sx={{ fontWeight: 600, letterSpacing: '-0.02em' }}>
                Выберите сессию или начните новую
              </Typography>
              <Typography color="text.secondary" sx={{ mt: 1.25, lineHeight: 1.6 }}>
                После стопа — WhisperX, диаризация и бриф. Для длинных встреч сначала сводки частей.
              </Typography>
            </Box>
          ) : (
            <Box sx={{ maxWidth: 860 }}>
              <Stack direction="row" spacing={1} alignItems="center">
                <TextField
                  fullWidth
                  variant="standard"
                  value={titleDraft}
                  onChange={(e) => setTitleDraft(e.target.value)}
                  onBlur={() => {
                    const next = titleDraft.trim() || 'Без названия';
                    setTitleDraft(next);
                    setSelected((s) => (s ? { ...s, title: next } : s));
                    void api
                      .updateSession(selected.id, { title: next })
                      .catch(() => undefined);
                  }}
                  InputProps={{
                    sx: { fontSize: 22, fontWeight: 600 },
                    disableUnderline: true,
                  }}
                />
                <Chip size="small" variant="outlined" label={statusLabel(selected.status)} />
                <IconButton onClick={() => void openSession(selected.id)} title="Обновить">
                  <Refresh />
                </IconButton>
                {tab === 0 ? (
                  <IconButton
                    onClick={async () => {
                      await api.trashSession(selected.id);
                      setSelected(null);
                      selectedIdRef.current = null;
                      await loadList();
                    }}
                    title="В корзину"
                  >
                    <DeleteOutline />
                  </IconButton>
                ) : (
                  <>
                    <IconButton
                      onClick={async () => {
                        await api.restoreSession(selected.id);
                        setSelected(null);
                        await loadList();
                      }}
                      title="Восстановить"
                    >
                      <RestoreFromTrash />
                    </IconButton>
                    <IconButton onClick={() => setConfirmDelete(true)} title="Удалить навсегда">
                      <DeleteOutline color="error" />
                    </IconButton>
                  </>
                )}
              </Stack>

              <Stack direction="row" spacing={0.5} flexWrap="wrap" sx={{ mt: 1, mb: 0.5 }}>
                <Button
                  size="small"
                  startIcon={<PersonOutline />}
                  disabled={!speakerIds.length && selected.status === 'live'}
                  onClick={() => {
                    const next: Record<string, string> = { ...speakerNames };
                    for (const id of speakerIds) {
                      if (!(id in next)) next[id] = '';
                    }
                    if (!speakerIds.length) {
                      next['Вы'] = next['Вы'] || '';
                      next['Собеседник'] = next['Собеседник'] || '';
                    }
                    setSpeakerNames(next);
                    setSpeakerEditorOpen(true);
                  }}
                >
                  Имена спикеров
                </Button>
                <Button
                  size="small"
                  disabled={!transcriptText}
                  onClick={() => {
                    dispatchInsertChatText(transcriptText);
                    notifyRef.current('success', 'Стенограмма вставлена в чат');
                    window.location.assign('/');
                  }}
                >
                  К чату: стенограмма
                </Button>
                <Button
                  size="small"
                  disabled={!selected.brief_markdown}
                  onClick={() => {
                    dispatchInsertChatText(selected.brief_markdown);
                    notifyRef.current('success', 'Бриф вставлен в чат');
                    window.location.assign('/');
                  }}
                >
                  К чату: бриф
                </Button>
                <Button
                  size="small"
                  disabled={busy || selected.status === 'live'}
                  onClick={async () => {
                    setBusy(true);
                    try {
                      const next = await api.regenerateBrief(selected.id);
                      setSelected(next);
                      setTitleDraft(next.title || titleDraft);
                      notifyRef.current('success', 'Бриф обновлён');
                    } catch (e) {
                      notifyRef.current(
                        'error',
                        e instanceof Error ? e.message : 'Не удалось обновить бриф',
                      );
                    } finally {
                      setBusy(false);
                    }
                  }}
                >
                  Пересобрать бриф
                </Button>
              </Stack>

              <Divider sx={{ borderColor: borderSubtle, my: 1 }} />

              <WorkspaceSection title="Бриф">
                <Box sx={{ whiteSpace: 'pre-wrap', lineHeight: 1.65, minHeight: 72 }}>
                  {selected.brief_status === 'writing' || selected.status === 'processing' ? (
                    <Stack direction="row" spacing={1} alignItems="center">
                      <CircularProgress size={18} />
                      <Typography color="text.secondary">Готовим бриф…</Typography>
                    </Stack>
                  ) : selected.brief_markdown ? (
                    <Typography component="div" sx={{ whiteSpace: 'pre-wrap' }}>
                      {selected.brief_markdown}
                    </Typography>
                  ) : selected.status === 'live' || selected.status === 'paused' ? (
                    <Typography color="text.secondary">
                      Появится после «Стоп и обработать».
                    </Typography>
                  ) : selected.brief_status === 'failed' ? (
                    <Typography color="error">
                      Не удалось собрать бриф. Нажмите «Пересобрать бриф».
                    </Typography>
                  ) : (
                    <Typography color="text.secondary">
                      Пусто — мало стенограммы или LLM не ответил. Попробуйте «Пересобрать бриф».
                    </Typography>
                  )}
                </Box>
              </WorkspaceSection>

              <WorkspaceSection title="Заметки">
                <TextField
                  multiline
                  minRows={2}
                  fullWidth
                  variant="standard"
                  value={selected.user_notes || ''}
                  onChange={(e) =>
                    setSelected((s) => (s ? { ...s, user_notes: e.target.value } : s))
                  }
                  onBlur={() => {
                    void api
                      .updateSession(selected.id, { user_notes: selected.user_notes })
                      .catch(() => undefined);
                  }}
                  placeholder="Личные пометки к сессии"
                  InputProps={{ disableUnderline: true }}
                  sx={{
                    px: 0,
                    py: 0.5,
                    borderRadius: 1,
                    bgcolor: alpha(theme.palette.action.hover, 0.35),
                    '& .MuiInputBase-root': { px: 1.5, py: 1 },
                  }}
                />
              </WorkspaceSection>

              <WorkspaceSection
                title="Стенограмма"
                hint={
                  recording ? (
                    <Typography variant="caption" color="text.secondary">
                      обновляется по мере речи…
                    </Typography>
                  ) : undefined
                }
              >
                <Box
                  sx={{
                    maxHeight: 'min(52vh, 520px)',
                    overflow: 'auto',
                    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
                    fontSize: 13,
                    lineHeight: 1.55,
                    whiteSpace: 'pre-wrap',
                    color: 'text.primary',
                    pb: 2,
                  }}
                >
                  {transcriptText || (
                    <Typography color="text.secondary" sx={{ fontFamily: 'inherit' }}>
                      {recording ? 'Ждём первые фрагменты речи…' : 'Сегментов пока нет'}
                    </Typography>
                  )}
                  <div ref={transcriptEndRef} />
                </Box>
              </WorkspaceSection>
            </Box>
          )}
        </Box>
      </Box>

      <Dialog open={prefsOpen} onClose={() => setPrefsOpen(false)} fullWidth maxWidth="sm">
        <DialogTitle>Настройки живых сессий</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ pt: 1 }}>
            <FormControlLabel
              control={
                <Switch
                  checked={prefs.echo_detect}
                  onChange={(_, v) => setPrefs((p) => ({ ...p, echo_detect: v }))}
                />
              }
              label="Echo-detect (прятать повтор системного звука в микрофоне)"
            />
            <FormControlLabel
              control={
                <Switch
                  checked={prefs.show_echo_segments}
                  onChange={(_, v) => setPrefs((p) => ({ ...p, show_echo_segments: v }))}
                />
              }
              label="Показывать помеченные эхо-сегменты в стенограмме"
            />
            <FormControlLabel
              control={
                <Switch
                  checked={prefs.auto_stop_on_capture_end}
                  onChange={(_, v) => setPrefs((p) => ({ ...p, auto_stop_on_capture_end: v }))}
                />
              }
              label="Auto-stop: завершать запись при окончании шаринга экрана/звука"
            />
            <TextField
              label="Auto-stop по тишине remote (сек, 0 = выкл.)"
              type="number"
              size="small"
              value={prefs.auto_stop_on_remote_silence_sec}
              onChange={(e) =>
                setPrefs((p) => ({
                  ...p,
                  auto_stop_on_remote_silence_sec: Math.max(0, Number(e.target.value) || 0),
                }))
              }
            />
            <TextField
              label="Jargon / словарь для STT"
              helperText="Термины через запятую: AstraStudio, pgvector, WhisperX…"
              fullWidth
              multiline
              minRows={2}
              value={jargonText}
              onChange={(e) => setJargonText(e.target.value)}
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setPrefsOpen(false)}>Отмена</Button>
          <Button variant="contained" onClick={() => void savePrefs()}>
            Сохранить
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog
        open={speakerEditorOpen}
        onClose={() => setSpeakerEditorOpen(false)}
        fullWidth
        maxWidth="sm"
      >
        <DialogTitle>Имена спикеров</DialogTitle>
        <DialogContent>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2, pt: 1 }}>
            Задайте понятные имена вместо технических лейблов вроде SPEAKER_00.
            После диаризации (стоп и обработка) здесь появятся все найденные спикеры.
          </Typography>
          <Stack spacing={1.5}>
            {Object.keys(speakerNames).length === 0 ? (
              <Alert severity="info">
                Спикеры появятся после распознавания. Нажмите «Стоп и обработать» или
                дождитесь первых сегментов.
              </Alert>
            ) : (
              Object.keys(speakerNames).map((id) => (
                <TextField
                  key={id}
                  size="small"
                  fullWidth
                  label={id}
                  placeholder={`Имя для ${id}`}
                  value={speakerNames[id] || ''}
                  onChange={(e) =>
                    setSpeakerNames((prev) => ({ ...prev, [id]: e.target.value }))
                  }
                />
              ))
            )}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setSpeakerEditorOpen(false)}>Отмена</Button>
          <Button
            variant="contained"
            disabled={!selected || busy}
            onClick={async () => {
              if (!selected) return;
              setBusy(true);
              try {
                const mapping: Record<string, string> = {};
                for (const [k, v] of Object.entries(speakerNames)) {
                  if (v.trim()) mapping[k] = v.trim();
                }
                if (Object.keys(mapping).length) {
                  const next = await api.renameSpeakers(selected.id, mapping);
                  setSelected(next);
                  setLiveSegments(next.segments || []);
                  setSpeakerNames({
                    ...((next.metadata as { speaker_map?: Record<string, string> })
                      ?.speaker_map || mapping),
                  });
                }
                setSpeakerEditorOpen(false);
                notifyRef.current('success', 'Имена спикеров сохранены');
              } catch (e) {
                notifyRef.current(
                  'error',
                  e instanceof Error ? e.message : 'Не удалось сохранить имена',
                );
              } finally {
                setBusy(false);
              }
            }}
          >
            Применить
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog open={confirmDelete} onClose={() => setConfirmDelete(false)}>
        <DialogTitle>Удалить сессию навсегда?</DialogTitle>
        <DialogContent>
          Аудио в хранилище и сегменты в PostgreSQL будут удалены без возможности восстановления.
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirmDelete(false)}>Отмена</Button>
          <Button
            color="error"
            onClick={async () => {
              if (!selected) return;
              await api.deleteSession(selected.id);
              setConfirmDelete(false);
              setSelected(null);
              selectedIdRef.current = null;
              await loadList();
            }}
          >
            Удалить
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
