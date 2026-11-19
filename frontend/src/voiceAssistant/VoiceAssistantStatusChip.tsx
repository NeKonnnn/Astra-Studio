import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Box,
  Chip,
  IconButton,
  Paper,
  Typography,
  Tooltip,
} from '@mui/material';
import MicIcon from '@mui/icons-material/Mic';
import MicNoneIcon from '@mui/icons-material/MicNone';
import RecordVoiceOverIcon from '@mui/icons-material/RecordVoiceOver';
import HeadsetMicIcon from '@mui/icons-material/HeadsetMic';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import ExpandLessIcon from '@mui/icons-material/ExpandLess';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import SettingsOutlinedIcon from '@mui/icons-material/SettingsOutlined';
import type { VoiceAssistantStatus } from './types';
import {
  VA_HISTORY_CHANGED,
  clearVaHistory,
  loadVaHistory,
  type VaHistoryEntry,
} from './conversationHistory';
import { ASTRA_OPEN_SETTINGS_SECTION } from '../constants/hotkeys';

const PHASE_LABEL: Record<VoiceAssistantStatus['phase'], string> = {
  idle: 'Выкл',
  listening_wake: 'Слушаю wake-word',
  capturing_command: 'Записываю команду',
  executing: 'Выполняю',
  speaking: 'Отвечаю',
};

const POS_KEY = 'astra_voice_assistant_widget_pos';
const MARGIN = 8;

type Pos = { x: number; y: number };

function loadPos(): Pos | null {
  try {
    const raw = localStorage.getItem(POS_KEY);
    if (!raw) return null;
    const p = JSON.parse(raw) as { x?: unknown; y?: unknown };
    const x = Number(p.x);
    const y = Number(p.y);
    if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
    return { x, y };
  } catch {
    return null;
  }
}

function savePos(pos: Pos): void {
  try {
    localStorage.setItem(POS_KEY, JSON.stringify(pos));
  } catch {
    /* ignore */
  }
}

function clampPos(x: number, y: number, w: number, h: number): Pos {
  const maxX = Math.max(MARGIN, window.innerWidth - w - MARGIN);
  const maxY = Math.max(MARGIN, window.innerHeight - h - MARGIN);
  return {
    x: Math.min(maxX, Math.max(MARGIN, x)),
    y: Math.min(maxY, Math.max(MARGIN, y)),
  };
}

function formatTime(at: number): string {
  try {
    return new Date(at).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  } catch {
    return '';
  }
}

type Props = {
  status: VoiceAssistantStatus;
  enabled: boolean;
};

export default function VoiceAssistantStatusChip({ status, enabled }: Props) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const historyEndRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<{
    startX: number;
    startY: number;
    origX: number;
    origY: number;
    moved: boolean;
  } | null>(null);

  const [pos, setPos] = useState<Pos | null>(() => loadPos());
  const [dragging, setDragging] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(true);
  const [history, setHistory] = useState<VaHistoryEntry[]>(() => loadVaHistory());

  const color =
    status.phase === 'capturing_command'
      ? 'warning'
      : status.phase === 'executing' || status.phase === 'speaking'
        ? 'success'
        : 'primary';

  const icon =
    status.phase === 'capturing_command' || status.phase === 'speaking' ? (
      <RecordVoiceOverIcon fontSize="small" />
    ) : status.phase === 'listening_wake' ? (
      <MicIcon fontSize="small" />
    ) : (
      <MicNoneIcon fontSize="small" />
    );

  const transcript = status.lastAnswer || status.lastHeard || status.lastCommand || '';

  useEffect(() => {
    const onHist = () => setHistory(loadVaHistory());
    window.addEventListener(VA_HISTORY_CHANGED, onHist);
    return () => window.removeEventListener(VA_HISTORY_CHANGED, onHist);
  }, []);

  useEffect(() => {
    if (historyOpen) {
      historyEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [history, historyOpen, status.lastAnswer]);

  const ensureInViewport = useCallback(() => {
    const el = rootRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    setPos((prev) => {
      const base =
        prev ??
        ({
          x: window.innerWidth - rect.width - 16,
          y: window.innerHeight - rect.height - 16,
        } as Pos);
      const next = clampPos(base.x, base.y, rect.width, rect.height);
      if (!prev || next.x !== prev.x || next.y !== prev.y) {
        savePos(next);
        return next;
      }
      return prev;
    });
  }, []);

  useEffect(() => {
    if (!enabled) return undefined;
    const t = window.setTimeout(ensureInViewport, 0);
    const onResize = () => ensureInViewport();
    window.addEventListener('resize', onResize);
    return () => {
      window.clearTimeout(t);
      window.removeEventListener('resize', onResize);
    };
  }, [enabled, ensureInViewport, transcript, status.phase, historyOpen, history.length]);

  useEffect(() => {
    if (!dragging) return undefined;
    const onMove = (e: MouseEvent) => {
      const drag = dragRef.current;
      const el = rootRef.current;
      if (!drag || !el) return;
      const dx = e.clientX - drag.startX;
      const dy = e.clientY - drag.startY;
      if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
      const rect = el.getBoundingClientRect();
      setPos(clampPos(drag.origX + dx, drag.origY + dy, rect.width, rect.height));
    };
    const onUp = () => {
      dragRef.current = null;
      setDragging(false);
      setPos((current) => {
        if (current) savePos(current);
        return current;
      });
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
  }, [dragging]);

  const onMouseDown = (e: React.MouseEvent) => {
    if (e.button !== 0) return;
    // Не начинаем drag с кнопок/скролла истории
    const target = e.target as HTMLElement;
    if (target.closest('[data-va-no-drag]')) return;
    const el = rootRef.current;
    if (!el) return;
    e.preventDefault();
    const rect = el.getBoundingClientRect();
    const current = pos ?? { x: rect.left, y: rect.top };
    if (!pos) setPos({ x: rect.left, y: rect.top });
    dragRef.current = {
      startX: e.clientX,
      startY: e.clientY,
      origX: current.x,
      origY: current.y,
      moved: false,
    };
    setDragging(true);
  };

  if (!enabled) return null;

  const positionSx =
    pos != null
      ? { left: pos.x, top: pos.y, right: 'auto', bottom: 'auto' }
      : { right: 16, bottom: 16, left: 'auto', top: 'auto' };

  return (
    <Box
      ref={rootRef}
      onMouseDown={onMouseDown}
      sx={{
        position: 'fixed',
        ...positionSx,
        zIndex: (t) => t.zIndex.snackbar + 2,
        width: 360,
        maxWidth: 'calc(100vw - 24px)',
        cursor: dragging ? 'grabbing' : 'grab',
        userSelect: 'none',
        touchAction: 'none',
      }}
      title="Перетащите виджет мышкой"
    >
      <Paper
        elevation={dragging ? 10 : 6}
        sx={{
          position: 'relative',
          pl: 5.5,
          pr: 1.25,
          py: 1.25,
          bgcolor: 'background.paper',
          borderRadius: 3,
        }}
      >
        <Box
          sx={{
            position: 'absolute',
            left: -6,
            bottom: -6,
            width: 52,
            height: 52,
            borderRadius: '50%',
            bgcolor: 'primary.main',
            color: 'primary.contrastText',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            boxShadow: 3,
            pointerEvents: 'none',
          }}
        >
          <HeadsetMicIcon />
        </Box>

        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mb: 1 }}>
          <Chip icon={icon} label={PHASE_LABEL[status.phase]} color={color} size="small" />
          <Box sx={{ flex: 1 }} />
          <Tooltip title="Настройки голосового помощника">
            <IconButton
              data-va-no-drag
              size="small"
              onClick={(e) => {
                e.stopPropagation();
                window.dispatchEvent(
                  new CustomEvent(ASTRA_OPEN_SETTINGS_SECTION, {
                    detail: { section: 'voiceAssistant' },
                  }),
                );
              }}
            >
              <SettingsOutlinedIcon fontSize="small" />
            </IconButton>
          </Tooltip>
          <Tooltip title={historyOpen ? 'Свернуть историю' : 'История'}>
            <IconButton
              data-va-no-drag
              size="small"
              onClick={(e) => {
                e.stopPropagation();
                setHistoryOpen((v) => !v);
              }}
            >
              {historyOpen ? <ExpandLessIcon fontSize="small" /> : <ExpandMoreIcon fontSize="small" />}
            </IconButton>
          </Tooltip>
          <Tooltip title="Очистить историю">
            <IconButton
              data-va-no-drag
              size="small"
              onClick={(e) => {
                e.stopPropagation();
                clearVaHistory();
              }}
            >
              <DeleteOutlineIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        </Box>

        {historyOpen && (
          <Box
            data-va-no-drag
            sx={{
              maxHeight: 220,
              overflowY: 'auto',
              mb: 1,
              pr: 0.5,
              cursor: 'default',
              borderRadius: 1,
              bgcolor: (t) => (t.palette.mode === 'dark' ? 'rgba(255,255,255,0.04)' : 'rgba(0,0,0,0.03)'),
              p: 1,
            }}
            onMouseDown={(e) => e.stopPropagation()}
          >
            {history.length === 0 ? (
              <Typography variant="caption" color="text.secondary">
                История пуста. Скажите wake-word и команду или вопрос.
              </Typography>
            ) : (
              history.slice(-40).map((entry) => (
                <Box key={entry.id} sx={{ mb: 0.75 }}>
                  <Typography
                    variant="caption"
                    sx={{
                      fontWeight: 600,
                      color:
                        entry.role === 'user'
                          ? 'primary.main'
                          : entry.role === 'system'
                            ? 'text.secondary'
                            : 'success.main',
                    }}
                  >
                    {entry.role === 'user' ? 'Вы' : entry.role === 'assistant' ? 'Астра' : 'Система'}
                    {' · '}
                    {formatTime(entry.at)}
                  </Typography>
                  <Typography variant="body2" sx={{ wordBreak: 'break-word', lineHeight: 1.35 }}>
                    {entry.text}
                  </Typography>
                </Box>
              ))
            )}
            <div ref={historyEndRef} />
          </Box>
        )}

        {transcript ? (
          <Typography variant="body2" sx={{ wordBreak: 'break-word', pointerEvents: 'none' }}>
            {transcript}
          </Typography>
        ) : (
          <Typography variant="caption" color="text.secondary" display="block" sx={{ pointerEvents: 'none' }}>
            Скажите wake-word · ответ озвучивается голосом браузера (auto)
          </Typography>
        )}
        {(status.engine !== 'none' || status.ttsEngine) && (
          <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 0.5, pointerEvents: 'none' }}>
            STT: {status.engine}
            {status.ttsEngine ? ` · TTS: ${status.ttsEngine}` : ''}
            {status.sileroBlocked ? ' · Silero blocked' : ''}
            {status.lastError ? ` · ${status.lastError}` : ''}
          </Typography>
        )}
      </Paper>
    </Box>
  );
}
