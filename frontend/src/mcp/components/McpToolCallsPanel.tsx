import React, { useMemo, useState } from 'react';
import {
  Box,
  CircularProgress,
  Collapse,
  IconButton,
  Link,
  Typography,
} from '@mui/material';
import {
  CheckCircleOutline as CheckIcon,
  ErrorOutline as ErrorIcon,
  ExpandMore as ExpandMoreIcon,
} from '@mui/icons-material';
import type { McpToolCallRecord } from '../types';
import { downloadMcpFile } from '../api';
import { mergeMcpToolCalls } from '../utils/mergeToolCalls';
import { DocumentSearchPanel, type DocumentSearchTrace } from '../../components/DocumentSearchPanel';

interface McpToolCallsPanelProps {
  toolCalls: McpToolCallRecord[];
  serverLabels?: Record<string, string>;
}

/** Длительность tool-вызова: минуты (и секунды), не сырые ms. */
function formatDuration(durationMs: number): string {
  const totalSec = Math.max(0, Math.round(durationMs / 1000));
  const mins = Math.floor(totalSec / 60);
  const secs = totalSec % 60;
  if (mins <= 0) {
    // меньше минуты — показываем как доли минуты
    const frac = Math.max(0.1, Math.round((durationMs / 60000) * 10) / 10);
    return `${frac.toLocaleString('ru-RU')} мин`;
  }
  if (secs === 0) return `${mins} мин`;
  return `${mins} мин ${secs} с`;
}

function formatJsonBlock(value: unknown): string {
  if (value == null) return '';
  if (typeof value === 'string') {
    const trimmed = value.trim();
    if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
      try {
        return JSON.stringify(JSON.parse(trimmed), null, 2);
      } catch {
        return value;
      }
    }
    return value;
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function McpDownloadLink({ url, label }: { url: string; label?: string }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleDownload = async (event: React.MouseEvent) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await downloadMcpFile(url, label);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось скачать файл');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Box>
      <Link
        component="button"
        type="button"
        onClick={handleDownload}
        disabled={busy}
        variant="caption"
        sx={{ wordBreak: 'break-all', textAlign: 'left' }}
      >
        {busy ? 'Скачивание…' : `Скачать: ${label || 'файл'}`}
      </Link>
      {error ? (
        <Typography variant="caption" color="error" sx={{ display: 'block', mt: 0.25 }}>
          {error}
        </Typography>
      ) : null}
    </Box>
  );
}

function statusLabel(
  status: 'running' | 'completed' | 'failed',
  tool: string,
  toolArgs?: Record<string, unknown>,
): string {
  const agentName =
    tool === 'subagent' && typeof toolArgs?.agent_name === 'string'
      ? toolArgs.agent_name.trim()
      : '';
  const toolLabel = agentName || tool;
  if (status === 'running') return `Выполняется: ${toolLabel}`;
  if (status === 'failed') return `Ошибка: ${toolLabel}`;
  return `Выполнено: ${toolLabel}`;
}

function CodeBlock({ children }: { children: string }) {
  if (!children.trim()) {
    return (
      <Typography variant="caption" color="text.secondary" sx={{ fontStyle: 'italic' }}>
        (пусто)
      </Typography>
    );
  }
  return (
    <Box
      component="pre"
      sx={{
        m: 0,
        p: 1.25,
        borderRadius: 1,
        bgcolor: 'action.hover',
        border: '1px solid',
        borderColor: 'divider',
        fontSize: '0.72rem',
        lineHeight: 1.45,
        fontFamily: 'Consolas, Monaco, "Courier New", monospace',
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
        maxHeight: 280,
        overflow: 'auto',
      }}
    >
      {children}
    </Box>
  );
}

function McpToolCallCard({
  execution,
  serverLabel,
  defaultOpen,
}: {
  execution: ReturnType<typeof mergeMcpToolCalls>[number];
  serverLabel: string;
  defaultOpen: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const inputText = formatJsonBlock(execution.arguments ?? {});
  const resultText = formatJsonBlock(execution.result ?? execution.result_preview ?? execution.error ?? '');

  return (
    <Box
      sx={{
        border: '1px solid',
        borderColor: execution.status === 'failed' ? 'error.light' : 'divider',
        borderRadius: 1.5,
        overflow: 'hidden',
        bgcolor: 'background.paper',
      }}
    >
      <Box
        role="button"
        tabIndex={0}
        onClick={() => setOpen((v) => !v)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            setOpen((v) => !v);
          }
        }}
        sx={{
          display: 'flex',
          alignItems: 'center',
          gap: 1,
          px: 1.25,
          py: 0.85,
          cursor: 'pointer',
          userSelect: 'none',
          '&:hover': { bgcolor: 'action.hover' },
        }}
      >
        {execution.status === 'running' ? (
          <CircularProgress size={16} sx={{ flexShrink: 0 }} />
        ) : execution.status === 'failed' ? (
          <ErrorIcon sx={{ fontSize: 18, color: 'error.main', flexShrink: 0 }} />
        ) : (
          <CheckIcon sx={{ fontSize: 18, color: 'success.main', flexShrink: 0 }} />
        )}
        <Typography variant="body2" sx={{ flex: 1, fontWeight: 500 }}>
          {statusLabel(execution.status, execution.tool, execution.arguments)}
        </Typography>
        {execution.duration_ms != null ? (
          <Typography variant="caption" color="text.secondary">
            {formatDuration(execution.duration_ms)}
          </Typography>
        ) : null}
        <IconButton
          size="small"
          aria-label={open ? 'Свернуть' : 'Развернуть'}
          sx={{ transform: open ? 'rotate(180deg)' : 'none', transition: 'transform 0.2s' }}
        >
          <ExpandMoreIcon fontSize="small" />
        </IconButton>
      </Box>

      <Collapse in={open}>
        <Box sx={{ px: 1.25, pb: 1.25, display: 'flex', flexDirection: 'column', gap: 1.25 }}>
          {execution.document_search &&
          (execution.document_search.hits?.length || execution.document_search.sourceFiles?.length) ? (
            <DocumentSearchPanel trace={execution.document_search as DocumentSearchTrace} />
          ) : null}

          <Box>
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>
              AstraChat отправил эту информацию {serverLabel}
            </Typography>
            <CodeBlock>{inputText}</CodeBlock>
          </Box>

          {execution.status !== 'running' ? (
            <Box>
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>
                Результат
              </Typography>
              <CodeBlock>{resultText}</CodeBlock>
            </Box>
          ) : null}

          {execution.download_urls?.length ? (
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.35 }}>
              {execution.download_urls.map((file) => (
                <McpDownloadLink
                  key={`${file.url}-${file.label || 'file'}`}
                  url={file.url}
                  label={file.label}
                />
              ))}
            </Box>
          ) : null}
        </Box>
      </Collapse>
    </Box>
  );
}

export default function McpToolCallsPanel({ toolCalls, serverLabels }: McpToolCallsPanelProps) {
  const executions = useMemo(() => mergeMcpToolCalls(toolCalls), [toolCalls]);
  const [open, setOpen] = useState(true);

  if (!executions.length) return null;

  const runningCount = executions.filter((x) => x.status === 'running').length;
  const completedCount = executions.filter((x) => x.status === 'completed').length;

  return (
    <Box sx={{ mt: 1.25, display: 'flex', flexDirection: 'column', gap: 1 }}>
      <Box
        role="button"
        tabIndex={0}
        onClick={() => setOpen((v) => !v)}
        sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5, cursor: 'pointer', width: 'fit-content' }}
      >
        <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600 }}>
          MCP-инструменты ({completedCount}/{executions.length}
          {runningCount > 0 ? `, выполняется ${runningCount}` : ''})
        </Typography>
        <IconButton size="small" sx={{ transform: open ? 'rotate(180deg)' : 'none' }}>
          <ExpandMoreIcon fontSize="small" />
        </IconButton>
      </Box>

      <Collapse in={open}>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
          {executions.map((execution, index) => (
            <McpToolCallCard
              key={execution.call_id}
              execution={execution}
              serverLabel={serverLabels?.[execution.server_id] || execution.server_id}
              defaultOpen={execution.status === 'running' || index === executions.length - 1}
            />
          ))}
        </Box>
      </Collapse>
    </Box>
  );
}
