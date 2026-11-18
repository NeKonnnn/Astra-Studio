import React, { useMemo, useState } from 'react';
import {
  Box,
  CircularProgress,
  Collapse,
  Link,
  Typography,
  keyframes,
} from '@mui/material';
import {
  CheckCircle as CheckIcon,
  ErrorOutline as ErrorIcon,
  Build as WrenchIcon,
  ExpandMore as ExpandMoreIcon,
  ExpandLess as ExpandLessIcon,
  AutoAwesome as SparklesIcon,
} from '@mui/icons-material';
import { formatGenerationDuration, useElapsedSeconds } from '../../hooks/useElapsedSeconds';
import type { McpToolCallRecord, McpToolExecution } from '../types';
import { downloadMcpFile } from '../api';
import { buildMcpToolTree, mergeMcpToolCalls } from '../utils/mergeToolCalls';
import { DocumentSearchPanel, type DocumentSearchTrace } from '../../components/DocumentSearchPanel';

/** Backend шлёт unix-секунды; Date.now() — миллисекунды. */
function toStartedAtMs(startedAt: number | undefined): number | null {
  if (startedAt == null || startedAt <= 0) return null;
  return startedAt < 1e12 ? startedAt * 1000 : startedAt;
}

function formatDurationMs(durationMs: number): string {
  return formatGenerationDuration(Math.max(0, durationMs / 1000));
}

const RESULT_PREVIEW_LIMIT = 10000;

const shimmerAnim = keyframes`
  0% { background-position: 200% center; }
  100% { background-position: -200% center; }
`;

interface McpToolCallsPanelProps {
  toolCalls: McpToolCallRecord[];
  serverLabels?: Record<string, string>;
  /** Если true — строим дерево по parent_call_id (субагенты). */
  nested?: boolean;
}

type Execution = McpToolExecution;

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

function toolDisplayName(execution: Execution): string {
  if (
    execution.tool === 'subagent' &&
    typeof execution.arguments?.agent_name === 'string' &&
    execution.arguments.agent_name.trim()
  ) {
    return execution.arguments.agent_name.trim();
  }
  if (execution.tool === 'skill') {
    const skill = execution.arguments?.skill;
    if (typeof skill === 'string' && skill.trim()) return `навык ${skill.trim()}`;
    return 'навык';
  }
  if (execution.tool === 'document_search') {
    return 'база знаний';
  }
  if (execution.tool === 'artifacts') {
    return 'артефакты';
  }
  if (String(execution.tool || '').startsWith('plugin:')) {
    const label = execution.arguments?.label;
    if (typeof label === 'string' && label.trim()) return label.trim();
    return execution.tool.replace(/^plugin:/, 'плагин ');
  }
  return execution.tool || execution.qualified_name || 'tool';
}

function summarizeToolNames(executions: Execution[]): string {
  const counts = new Map<string, number>();
  for (const ex of executions) {
    const name = toolDisplayName(ex);
    counts.set(name, (counts.get(name) || 0) + 1);
  }
  return Array.from(counts.entries())
    .map(([name, n]) => (n > 1 ? `${n}× ${name}` : name))
    .join(', ');
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

function CodeBlock({ children, maxHeight = 280 }: { children: string; maxHeight?: number }) {
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
        borderRadius: 1.5,
        bgcolor: (t) =>
          t.palette.mode === 'dark' ? 'rgba(0,0,0,0.28)' : 'rgba(0,0,0,0.04)',
        fontSize: '0.72rem',
        lineHeight: 1.45,
        fontFamily: 'Consolas, Monaco, "Courier New", monospace',
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
        maxHeight,
        overflow: 'auto',
      }}
    >
      {children}
    </Box>
  );
}

function ArgsKeyValue({ args }: { args: Record<string, unknown> }) {
  const entries = Object.entries(args);
  if (!entries.length) {
    return (
      <Typography variant="caption" color="text.secondary" sx={{ fontStyle: 'italic' }}>
        (без аргументов)
      </Typography>
    );
  }
  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.35, px: 0.5 }}>
      {entries.map(([key, value]) => (
        <Box key={key} sx={{ display: 'flex', gap: 1, alignItems: 'flex-start' }}>
          <Typography
            variant="caption"
            sx={{
              fontWeight: 600,
              color: 'text.secondary',
              flexShrink: 0,
              minWidth: 72,
            }}
          >
            {key}
          </Typography>
          <Typography
            variant="caption"
            sx={{ color: 'text.primary', wordBreak: 'break-word', lineHeight: 1.45 }}
          >
            {typeof value === 'object' && value !== null
              ? JSON.stringify(value)
              : String(value ?? '')}
          </Typography>
        </Box>
      ))}
    </Box>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <Typography
      variant="caption"
      sx={{
        display: 'block',
        mb: 0.75,
        px: 0.5,
        fontSize: '0.62rem',
        fontWeight: 600,
        letterSpacing: '0.08em',
        textTransform: 'uppercase',
        color: 'text.disabled',
      }}
    >
      {children}
    </Typography>
  );
}

function shimmerSx(enabled: boolean) {
  if (!enabled) return {};
  return {
    backgroundImage:
      'linear-gradient(90deg, rgba(158,158,158,0.55) 0%, rgba(245,245,245,0.95) 45%, rgba(158,158,158,0.55) 90%)',
    backgroundSize: '200% auto',
    backgroundClip: 'text',
    WebkitBackgroundClip: 'text',
    color: 'transparent',
    animation: `${shimmerAnim} 2.2s linear infinite`,
  };
}

function McpToolCallRow({
  execution,
  serverLabel,
  defaultOpen,
  serverLabels,
}: {
  execution: Execution;
  serverLabel: string;
  defaultOpen: boolean;
  serverLabels?: Record<string, string>;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const [expandedResult, setExpandedResult] = useState(false);
  const name = toolDisplayName(execution);
  const isRunning =
    execution.status === 'running' ||
    Boolean(execution.children?.some((c) => c.status === 'running'));
  const isFailed = execution.status === 'failed';
  const isDone = execution.status === 'completed' || isFailed;
  const childExecutions = execution.children || [];

  const argsObj =
    execution.arguments &&
    typeof execution.arguments === 'object' &&
    !Array.isArray(execution.arguments)
      ? execution.arguments
      : null;

  const resultRaw = execution.result ?? execution.result_preview ?? execution.error ?? '';
  const resultText = formatJsonBlock(resultRaw);
  const resultTruncated =
    !expandedResult && typeof resultRaw === 'string' && resultRaw.length > RESULT_PREVIEW_LIMIT
      ? resultRaw.slice(0, RESULT_PREVIEW_LIMIT)
      : null;

  const liveSec = useElapsedSeconds(toStartedAtMs(execution.started_at), isRunning);
  const durationLabel =
    isRunning && liveSec != null
      ? formatGenerationDuration(liveSec)
      : execution.duration_ms != null
        ? formatDurationMs(execution.duration_ms)
        : null;

  const titleNode = isRunning ? (
    <>
      Выполняется <Box component="strong" sx={{ fontWeight: 700 }}>{name}</Box>…
    </>
  ) : isFailed ? (
    <>
      Ошибка <Box component="strong" sx={{ fontWeight: 700 }}>{name}</Box>
    </>
  ) : (
    <>
      Результат <Box component="strong" sx={{ fontWeight: 700 }}>{name}</Box>
    </>
  );

  return (
    <Box sx={{ width: '100%' }}>
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
          width: 'fit-content',
          maxWidth: '100%',
          cursor: 'pointer',
          userSelect: 'none',
          color: 'text.secondary',
          py: 0.35,
          '&:hover': { color: 'text.primary' },
        }}
      >
        {isRunning ? (
          <CircularProgress size={14} sx={{ flexShrink: 0 }} />
        ) : isFailed ? (
          <ErrorIcon sx={{ fontSize: 16, color: 'error.main', flexShrink: 0 }} />
        ) : isDone ? (
          <CheckIcon sx={{ fontSize: 16, color: 'success.main', flexShrink: 0 }} />
        ) : (
          <WrenchIcon sx={{ fontSize: 14, color: 'text.disabled', flexShrink: 0 }} />
        )}

        <Typography
          variant="body2"
          sx={{
            flex: 1,
            minWidth: 0,
            fontSize: '0.84rem',
            lineHeight: 1.35,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            ...shimmerSx(isRunning),
          }}
        >
          {titleNode}
        </Typography>

        {durationLabel ? (
          <Typography
            variant="caption"
            sx={{
              color: 'text.disabled',
              fontVariantNumeric: 'tabular-nums',
              whiteSpace: 'nowrap',
              flexShrink: 0,
            }}
          >
            {durationLabel}
          </Typography>
        ) : null}

        {open ? (
          <ExpandLessIcon sx={{ fontSize: 16, flexShrink: 0, opacity: 0.7 }} />
        ) : (
          <ExpandMoreIcon sx={{ fontSize: 16, flexShrink: 0, opacity: 0.7 }} />
        )}
      </Box>

      <Collapse in={open} timeout={220}>
        <Box
          sx={{
            mt: 0.75,
            mb: 0.5,
            p: 1.5,
            borderRadius: 2,
            border: '1px solid',
            borderColor: isFailed ? 'error.light' : 'divider',
            bgcolor: (t) =>
              t.palette.mode === 'dark' ? 'rgba(255,255,255,0.02)' : 'rgba(0,0,0,0.015)',
            display: 'flex',
            flexDirection: 'column',
            gap: 1.5,
          }}
        >
          {execution.document_search &&
          (execution.document_search.hits?.length ||
            execution.document_search.sourceFiles?.length) ? (
            <DocumentSearchPanel trace={execution.document_search as DocumentSearchTrace} />
          ) : null}

          <Box>
            <SectionLabel>Input</SectionLabel>
            {serverLabel ? (
              <Typography
                variant="caption"
                color="text.disabled"
                sx={{ display: 'block', mb: 0.75, px: 0.5 }}
              >
                → {serverLabel}
              </Typography>
            ) : null}
            {argsObj ? (
              <ArgsKeyValue args={argsObj} />
            ) : (
              <CodeBlock>{formatJsonBlock(execution.arguments ?? {})}</CodeBlock>
            )}
          </Box>

          {isDone ? (
            <Box>
              <SectionLabel>{isFailed ? 'Error' : 'Output'}</SectionLabel>
              {resultTruncated != null ? (
                <>
                  <CodeBlock maxHeight={360}>{resultTruncated}</CodeBlock>
                  <Typography
                    component="button"
                    variant="caption"
                    onClick={(e) => {
                      e.stopPropagation();
                      setExpandedResult(true);
                    }}
                    sx={{
                      mt: 0.75,
                      border: 'none',
                      background: 'transparent',
                      cursor: 'pointer',
                      color: 'text.secondary',
                      p: 0,
                      '&:hover': { color: 'text.primary' },
                    }}
                  >
                    Показать всё ({resultRaw.length.toLocaleString('ru-RU')} символов)
                  </Typography>
                </>
              ) : (
                <CodeBlock>{resultText}</CodeBlock>
              )}
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

          {childExecutions.length > 0 ? (
            <Box>
              <SectionLabel>Вложено</SectionLabel>
              <Box sx={{ pl: 0.5, display: 'flex', flexDirection: 'column', gap: 0.35 }}>
                {childExecutions.map((child, idx) => (
                  <McpToolCallRow
                    key={child.call_id}
                    execution={child}
                    serverLabel={serverLabels?.[child.server_id] || child.server_id}
                    serverLabels={serverLabels}
                    defaultOpen={
                      child.status === 'running' ||
                      Boolean(child.children?.length) ||
                      idx === childExecutions.length - 1
                    }
                  />
                ))}
              </Box>
            </Box>
          ) : null}
        </Box>
      </Collapse>
    </Box>
  );
}

/** Панель MCP/native/subagent в стиле Open WebUI: компактные строки + группа Exploring/Explored. */
export default function McpToolCallsPanel({
  toolCalls,
  serverLabels,
  nested = true,
}: McpToolCallsPanelProps) {
  const executions = useMemo(
    () => (nested ? buildMcpToolTree(toolCalls) : mergeMcpToolCalls(toolCalls)),
    [toolCalls, nested],
  );
  const [groupOpen, setGroupOpen] = useState(true);

  if (!executions.length) return null;

  const runningCount = executions.filter(
    (x) => x.status === 'running' || x.children?.some((c) => c.status === 'running'),
  ).length;
  const hasPending = runningCount > 0;
  const onlySubagents = executions.every((x) => x.tool === 'subagent');
  const exploringLabel = onlySubagents
    ? hasPending
      ? 'Исследуем'
      : 'Исследовано'
    : hasPending
      ? 'Выполняется'
      : 'Выполнено';
  const namesSummary = summarizeToolNames(executions);
  const useGroupChrome = executions.length > 1;

  const runningOnly = runningCount === 1;

  const rows = (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.35 }}>
      {executions.map((execution, index) => (
        <McpToolCallRow
          key={execution.call_id}
          execution={execution}
          serverLabel={serverLabels?.[execution.server_id] || execution.server_id}
          serverLabels={serverLabels}
          // Батч pending: строки свёрнуты, видно группу «Выполняется a, b…».
          // Одиночный running / последний одиночный — раскрыт.
          defaultOpen={
            execution.status === 'running' || Boolean(execution.children?.length)
              ? !useGroupChrome || runningOnly || Boolean(execution.children?.length)
              : !useGroupChrome && index === executions.length - 1
          }
        />
      ))}
    </Box>
  );

  if (!useGroupChrome) {
    return <Box sx={{ mt: 1, mb: 0.5 }}>{rows}</Box>;
  }

  return (
    <Box sx={{ mt: 1, mb: 0.5, display: 'flex', flexDirection: 'column', gap: 0.5 }}>
      <Box
        role="button"
        tabIndex={0}
        onClick={() => setGroupOpen((v) => !v)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            setGroupOpen((v) => !v);
          }
        }}
        sx={{
          display: 'flex',
          alignItems: 'center',
          gap: 1,
          width: 'fit-content',
          maxWidth: '100%',
          cursor: 'pointer',
          color: 'text.secondary',
          '&:hover': { color: 'text.primary' },
        }}
      >
        {hasPending ? (
          <CircularProgress size={14} sx={{ flexShrink: 0 }} />
        ) : (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.35, flexShrink: 0 }}>
            <CheckIcon sx={{ fontSize: 16, color: 'success.main' }} />
            <SparklesIcon sx={{ fontSize: 14, opacity: 0.55 }} />
          </Box>
        )}
        <Box sx={{ minWidth: 0 }}>
          <Typography
            variant="body2"
            sx={{
              fontSize: '0.84rem',
              fontWeight: 600,
              lineHeight: 1.3,
              ...shimmerSx(hasPending),
            }}
          >
            {exploringLabel}
          </Typography>
          <Typography
            variant="caption"
            color="text.disabled"
            sx={{
              display: 'block',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              maxWidth: { xs: 260, sm: 420 },
            }}
          >
            {namesSummary}
            {runningCount > 0 ? ` · активно ${runningCount}` : ''}
          </Typography>
        </Box>
        {groupOpen ? (
          <ExpandLessIcon sx={{ fontSize: 16, flexShrink: 0, opacity: 0.7 }} />
        ) : (
          <ExpandMoreIcon sx={{ fontSize: 16, flexShrink: 0, opacity: 0.7 }} />
        )}
      </Box>

      <Collapse in={groupOpen} timeout={220}>
        <Box sx={{ pl: { xs: 0.5, sm: 1.25 }, pt: 0.35 }}>{rows}</Box>
      </Collapse>
    </Box>
  );
}
