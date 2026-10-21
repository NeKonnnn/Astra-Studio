import React, { useEffect, useRef, useState } from 'react';
import { useRightRailOffsetPx } from '../hooks/useRightSidebarInsetCssVar';
import {
  Box,
  Fab,
  IconButton,
  Paper,
  TextField,
  Typography,
  CircularProgress,
  Collapse,
  Tooltip,
} from '@mui/material';
import {
  Close as CloseIcon,
  Send as SendIcon,
  SupportAgent as SupportAgentIcon,
} from '@mui/icons-material';
import ReactMarkdown from 'react-markdown';
import {
  findSupportArticle,
  formatArticleReply,
} from './knowledgeArticles';

/** Картинка помощника: положите файл сюда → public/static/support/assistant.png */
const ASSISTANT_IMAGE_SRC = '/static/support/assistant.png';

type ChatBubble = {
  id: string;
  role: 'user' | 'assistant';
  text: string;
};

const WELCOME =
  'Чем могу помочь? Опишите, что хотите сделать — подскажу, куда нажать и какие шаги пройти.';

/**
 * Прототип виджета поддержки (как чат на сайтах):
 * свёрнут в FAB справа внизу → раскрывается панель с аватаром, вопросом и ответом.
 * Ответы пока из локальной базы статей; позже — LLM + RAG.
 */
const HELP_FAB_GAP_PX = 16;

export default function SupportAssistantWidget() {
  const rightRailPx = useRightRailOffsetPx();
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [imageOk, setImageOk] = useState(true);
  const [messages, setMessages] = useState<ChatBubble[]>([
    { id: 'welcome', role: 'assistant', text: WELCOME },
  ]);
  const listRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const t = window.setTimeout(() => inputRef.current?.focus(), 180);
    return () => window.clearTimeout(t);
  }, [open]);

  useEffect(() => {
    if (!open || !listRef.current) return;
    listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [messages, open, busy]);

  const send = async () => {
    const q = input.trim();
    if (!q || busy) return;

    setInput('');
    setMessages((prev) => [
      ...prev,
      { id: `u-${Date.now()}`, role: 'user', text: q },
    ]);
    setBusy(true);

    // Имитация задержки «модели» в прототипе
    await new Promise((r) => setTimeout(r, 450));
    const article = findSupportArticle(q);
    const reply = formatArticleReply(article);

    setMessages((prev) => [
      ...prev,
      { id: `a-${Date.now()}`, role: 'assistant', text: reply },
    ]);
    setBusy(false);
  };

  return (
    <Box
      sx={{
        position: 'fixed',
        right: rightRailPx + HELP_FAB_GAP_PX,
        bottom: 20,
        zIndex: 1400,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'flex-end',
        gap: 1.25,
        pointerEvents: 'none',
        '& > *': { pointerEvents: 'auto' },
      }}
    >
      <Collapse in={open} timeout={220} unmountOnExit>
        <Paper
          elevation={8}
          sx={{
            width: { xs: 'min(100vw - 32px, 360px)', sm: 360 },
            maxHeight: 'min(70vh, 560px)',
            display: 'flex',
            flexDirection: 'column',
            overflow: 'hidden',
            borderRadius: 3,
            border: (t) => `1px solid ${t.palette.divider}`,
          }}
        >
          {/* Шапка: картинка + заголовок */}
          <Box
            sx={{
              px: 2,
              pt: 1.75,
              pb: 1.5,
              display: 'flex',
              alignItems: 'flex-start',
              gap: 1.5,
              bgcolor: (t) =>
                t.palette.mode === 'dark' ? 'rgba(33,150,243,0.12)' : 'rgba(33,150,243,0.08)',
              borderBottom: (t) => `1px solid ${t.palette.divider}`,
            }}
          >
            <Box
              sx={{
                width: 72,
                height: 72,
                borderRadius: 2,
                overflow: 'hidden',
                flexShrink: 0,
                bgcolor: 'background.paper',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                border: (t) => `1px solid ${t.palette.divider}`,
              }}
            >
              {imageOk ? (
                <Box
                  component="img"
                  src={ASSISTANT_IMAGE_SRC}
                  alt="Помощник"
                  onError={() => setImageOk(false)}
                  sx={{ width: '100%', height: '100%', objectFit: 'cover' }}
                />
              ) : (
                <SupportAgentIcon color="primary" sx={{ fontSize: 40 }} />
              )}
            </Box>
            <Box sx={{ flex: 1, minWidth: 0, pt: 0.5 }}>
              <Typography variant="subtitle1" fontWeight={600} lineHeight={1.25}>
                оператор: Астра
              </Typography>
              <Typography variant="caption" color="text.secondary">
                Подскажу шаги по интерфейсу
              </Typography>
            </Box>
            <IconButton
              size="small"
              aria-label="Свернуть"
              onClick={() => setOpen(false)}
              sx={{ mt: -0.5, mr: -0.5 }}
            >
              <CloseIcon fontSize="small" />
            </IconButton>
          </Box>

          {/* Переписка */}
          <Box
            ref={listRef}
            sx={{
              flex: 1,
              overflowY: 'auto',
              px: 1.75,
              py: 1.5,
              display: 'flex',
              flexDirection: 'column',
              gap: 1.25,
              minHeight: 180,
            }}
          >
            {messages.map((m) => (
              <Box
                key={m.id}
                sx={{
                  alignSelf: m.role === 'user' ? 'flex-end' : 'flex-start',
                  maxWidth: '92%',
                  px: 1.25,
                  py: 0.9,
                  borderRadius: 2,
                  bgcolor: (t) =>
                    m.role === 'user'
                      ? t.palette.primary.main
                      : t.palette.mode === 'dark'
                        ? 'rgba(255,255,255,0.06)'
                        : 'rgba(0,0,0,0.04)',
                  color: m.role === 'user' ? 'primary.contrastText' : 'text.primary',
                  '& p': { m: 0, fontSize: '0.875rem', lineHeight: 1.45 },
                  '& strong': { fontWeight: 600 },
                  '& ul, & ol': { m: 0, pl: 2 },
                  '& li': { fontSize: '0.875rem', mb: 0.35 },
                  '& img': {
                    display: 'block',
                    maxWidth: '100%',
                    height: 'auto',
                    borderRadius: 1,
                    mt: 0.75,
                    mb: 0.5,
                    border: (t) => `1px solid ${t.palette.divider}`,
                  },
                }}
              >
                {m.role === 'assistant' ? (
                  <ReactMarkdown>{m.text}</ReactMarkdown>
                ) : (
                  <Typography variant="body2">{m.text}</Typography>
                )}
              </Box>
            ))}
            {busy && (
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, pl: 0.5 }}>
                <CircularProgress size={16} />
                <Typography variant="caption" color="text.secondary">
                  Ищу инструкцию…
                </Typography>
              </Box>
            )}
          </Box>

          {/* Ввод */}
          <Box
            sx={{
              px: 1.5,
              py: 1.25,
              borderTop: (t) => `1px solid ${t.palette.divider}`,
              display: 'flex',
              gap: 0.75,
              alignItems: 'flex-end',
            }}
          >
            <TextField
              inputRef={inputRef}
              fullWidth
              size="small"
              multiline
              maxRows={3}
              placeholder="Например: как открыть настройки"
              value={input}
              disabled={busy}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  void send();
                }
              }}
            />
            <IconButton
              color="primary"
              aria-label="Отправить"
              disabled={busy || !input.trim()}
              onClick={() => void send()}
            >
              <SendIcon />
            </IconButton>
          </Box>
        </Paper>
      </Collapse>

      <Tooltip title={open ? 'Свернуть помощь' : 'Помощь'} placement="left">
        <Fab
          color="primary"
          aria-label={open ? 'Свернуть помощь' : 'Открыть помощь'}
          onClick={() => setOpen((v) => !v)}
          size="medium"
          sx={{
            boxShadow: 4,
            width: 56,
            height: 56,
          }}
        >
          {open ? <CloseIcon /> : <SupportAgentIcon />}
        </Fab>
      </Tooltip>
    </Box>
  );
}
