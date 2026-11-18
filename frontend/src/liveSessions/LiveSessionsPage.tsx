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
  Paper,
  Stack,
  Switch,
  Tab,
  Tabs,
  TextField,
  Typography,
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

  return (
    <Box sx={{ p: { xs: 2, md: 3 }, maxWidth: 1400, mx: 'auto' }}>
      <Stack
        direction={{ xs: 'column', md: 'row' }}
        justifyContent="space-between"
        alignItems={{ xs: 'stretch', md: 'center' }}
        spacing={2}
        sx={{ mb: 2 }}
      >
        <Box>
          <Typography variant="h4" sx={{ fontWeight: 700, letterSpacing: '-0.02em' }}>
            Живые сессии
          </Typography>
          <Typography color="text.secondary" sx={{ mt: 0.5, maxWidth: 720 }}>
            Запись звонка без бота: микрофон + системный звук вкладки/экрана → живая стенограмма
            (WhisperX) → после стопа диаризация и бриф. Загрузка файлов/YouTube — в панели «Транскрибация».
          </Typography>
        </Box>
        <Stack direction="row" spacing={1} alignItems="center">
          <Button
            variant="outlined"
            startIcon={<ArrowBack />}
            onClick={() => {
              window.location.assign('/');
            }}
          >
            В чат
          </Button>
          <IconButton onClick={() => setPrefsOpen(true)} title="Настройки записи">
            <SettingsOutlined />
          </IconButton>
          <Button
            variant="contained"
            size="large"
            color="error"
            startIcon={<FiberManualRecord />}
            disabled={busy || recording}
            onClick={() => void beginCapture()}
          >
            Начать запись
          </Button>
        </Stack>
      </Stack>

      {!recording && guideStep < 3 && (
        <Alert severity="info" sx={{ mb: 2 }}>
          <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
            Как пользоваться
          </Typography>
          <ol style={{ margin: 0, paddingLeft: 18 }}>
            <li>Нажмите <b>Начать запись</b> (кнопка «Живая запись» в сайдбаре делает то же самое).</li>
            <li>Разрешите <b>микрофон</b>.</li>
            <li>
              В окне шаринга выберите <b>вкладку браузера</b> с звонком (Meet / Zoom Web / Teams Web)
              и галочку <b>«Также транслировать звук вкладки»</b> — либо <b>весь экран</b> с
              «Share system audio». Шаринг <b>окна</b> приложения (Zoom.exe и т.п.) звук не даёт —
              так устроен браузер.
            </li>
            <li>Говорите — справа появляются сегменты. По окончании — <b>Стоп и обработать</b>.</li>
          </ol>
        </Alert>
      )}

      {recording && (
        <Paper
          elevation={0}
          sx={{
            mb: 3,
            p: 2,
            border: '1px solid',
            borderColor: 'error.light',
            bgcolor: 'background.paper',
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
            <Alert severity="warning" sx={{ mt: 2 }}>
              Звук собеседника не захвачен (только микрофон). Причина чаще всего: выбран шаринг{' '}
              <b>окна</b> — браузер не отдаёт из него audio. Остановите запись и начните снова:
              шарьте <b>вкладку</b> звонка с галочкой звука или <b>весь экран</b> (Chrome/Edge на
              Windows).
            </Alert>
          )}
          {prefs.auto_stop_on_capture_end && (
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
              Auto-stop включён: если закрыть шаринг экрана/звука — запись завершится сама.
            </Typography>
          )}
        </Paper>
      )}

      <Stack direction={{ xs: 'column', md: 'row' }} spacing={2} alignItems="stretch">
        <Paper sx={{ width: { xs: '100%', md: 360 }, flexShrink: 0, p: 1.5 }}>
          <Tabs value={tab} onChange={(_, v) => setTab(v)} variant="fullWidth">
            <Tab label="Архив" />
            <Tab label="Корзина" />
          </Tabs>
          <TextField
            size="small"
            fullWidth
            placeholder="Поиск по брифу и стенограмме"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            InputProps={{ startAdornment: <SearchIcon fontSize="small" sx={{ mr: 1, opacity: 0.6 }} /> }}
            sx={{ my: 1.5 }}
          />
          {loading ? (
            <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
              <CircularProgress size={28} />
            </Box>
          ) : (
            <List dense sx={{ maxHeight: '60vh', overflow: 'auto' }}>
              {items.map((item) => (
                <ListItemButton
                  key={item.id}
                  selected={selected?.id === item.id}
                  onClick={() => void openSession(item.id)}
                >
                  <ListItemText
                    primary={item.title}
                    secondary={`${statusLabel(item.status)} · ${formatDuration(item.duration_ms)}`}
                    primaryTypographyProps={{ noWrap: true }}
                    secondaryTypographyProps={{ noWrap: true }}
                  />
                </ListItemButton>
              ))}
              {!items.length && (
                <Typography color="text.secondary" sx={{ p: 2 }}>
                  Пока пусто — нажмите «Начать запись».
                </Typography>
              )}
            </List>
          )}
        </Paper>

        <Paper sx={{ flex: 1, p: 2.5, minHeight: 420 }}>
          {!selected ? (
            <Box sx={{ py: 8, textAlign: 'center' }}>
              <Typography variant="h6">Выберите сессию или начните новую запись</Typography>
              <Typography color="text.secondary" sx={{ mt: 1 }}>
                После остановки AstraStudio прогонит аудио через STT/диаризацию, сохранит сегменты
                в PostgreSQL и соберёт бриф (для длинных встреч — сначала сводки частей).
              </Typography>
            </Box>
          ) : (
            <Stack spacing={2}>
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
                    disableUnderline: false,
                  }}
                />
                <Chip size="small" label={statusLabel(selected.status)} />
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

              <Stack direction="row" spacing={1} flexWrap="wrap">
                <Button
                  size="small"
                  startIcon={<PersonOutline />}
                  disabled={!speakerIds.length && selected.status === 'live'}
                  onClick={() => {
                    const next: Record<string, string> = { ...speakerNames };
                    for (const id of speakerIds) {
                      if (!(id in next)) next[id] = '';
                    }
                    // Live dual-track без диаризации — можно задать «Вы» / «Собеседник»
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
                  В чат: стенограмма
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
                  В чат: бриф
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

              <Divider />

              <Typography variant="subtitle2">Бриф</Typography>
              <Paper variant="outlined" sx={{ p: 2, whiteSpace: 'pre-wrap', minHeight: 120 }}>
                {selected.brief_status === 'writing' || selected.status === 'processing' ? (
                  <Stack direction="row" spacing={1} alignItems="center">
                    <CircularProgress size={18} />
                    <Typography color="text.secondary">Готовим бриф…</Typography>
                  </Stack>
                ) : selected.brief_markdown ? (
                  selected.brief_markdown
                ) : selected.status === 'live' || selected.status === 'paused' ? (
                  <Typography color="text.secondary">
                    Бриф появится после «Стоп и обработать» — саммари встречи (суть, решения, задачи).
                  </Typography>
                ) : selected.brief_status === 'failed' ? (
                  <Typography color="error">
                    Не удалось собрать бриф. Нажмите «Пересобрать бриф».
                  </Typography>
                ) : (
                  <Typography color="text.secondary">
                    Бриф пуст (мало текста в стенограмме или LLM не ответил). Попробуйте
                    «Пересобрать бриф».
                  </Typography>
                )}
              </Paper>

              <Typography variant="subtitle2">Мои заметки</Typography>
              <TextField
                multiline
                minRows={3}
                fullWidth
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
              />

              <Stack direction="row" spacing={1} alignItems="center">
                <Typography variant="subtitle2">Стенограмма</Typography>
                {recording && (
                  <Typography variant="caption" color="text.secondary">
                    обновляется по мере речи…
                  </Typography>
                )}
              </Stack>
              <Paper
                variant="outlined"
                sx={{
                  p: 2,
                  maxHeight: 420,
                  overflow: 'auto',
                  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
                  fontSize: 13,
                  whiteSpace: 'pre-wrap',
                }}
              >
                {transcriptText || (
                  <Typography color="text.secondary">
                    {recording
                      ? 'Ждём первые фрагменты речи…'
                      : 'Сегментов пока нет'}
                  </Typography>
                )}
                <div ref={transcriptEndRef} />
              </Paper>
            </Stack>
          )}
        </Paper>
      </Stack>

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
