import React, { useMemo } from 'react';
import { Box, Button, Typography } from '@mui/material';
import { PlayArrow as RunIcon } from '@mui/icons-material';
import { useCommittedContent } from '../../hooks/useCommittedContent';
import {
  buildCssSandboxSrcDoc,
  buildJsSandboxSrcDoc,
  buildPythonSandboxSrcDoc,
} from '../../utils/codeSandbox';

export type CodeSandboxKind = 'javascript' | 'css' | 'python';

interface Props {
  content: string;
  kind: CodeSandboxKind;
  isStreaming?: boolean;
  /** Автозапуск после стрима (по умолчанию true). */
  autoRun?: boolean;
}

function kindLabel(kind: CodeSandboxKind): string {
  if (kind === 'python') return 'Python';
  if (kind === 'css') return 'CSS';
  return 'JavaScript';
}

function buildSrcDoc(kind: CodeSandboxKind, code: string): string {
  if (kind === 'python') return buildPythonSandboxSrcDoc(code);
  if (kind === 'css') return buildCssSandboxSrcDoc(code);
  return buildJsSandboxSrcDoc(code);
}

/**
 * Песочница для JS / CSS / Python внутри артефакта.
 * На стриме iframe не монтируем (как HTML/React — Virtuoso #185).
 */
export default function ArtifactCodeSandboxPreview({
  content,
  kind,
  isStreaming = false,
  autoRun = true,
}: Props) {
  const committed = useCommittedContent(content || '', isStreaming, isStreaming ? 1200 : 500);
  const [runKey, setRunKey] = React.useState(0);
  const [armed, setArmed] = React.useState(autoRun);

  React.useEffect(() => {
    if (!isStreaming && autoRun) setArmed(true);
  }, [isStreaming, autoRun, committed]);

  const srcDoc = useMemo(() => {
    if (!armed || isStreaming) return '';
    if (!(committed || '').trim()) return '';
    return buildSrcDoc(kind, committed);
  }, [armed, committed, isStreaming, kind, runKey]);

  if (isStreaming) {
    return <Box sx={{ width: '100%', height: '100%', minHeight: 240, bgcolor: '#e8eaed' }} />;
  }

  if (!(committed || '').trim()) {
    return <Box sx={{ height: '100%', minHeight: 240, bgcolor: '#e8eaed' }} />;
  }

  if (!armed || !srcDoc) {
    return (
      <Box
        sx={{
          height: '100%',
          minHeight: 240,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 1.5,
          bgcolor: '#f8fafc',
          p: 2,
        }}
      >
        <Typography variant="body2" sx={{ color: '#64748b', textAlign: 'center' }}>
          Песочница {kindLabel(kind)}: нажмите «Запустить», чтобы выполнить код в браузере.
        </Typography>
        <Button
          size="small"
          variant="contained"
          startIcon={<RunIcon />}
          onClick={() => {
            setArmed(true);
            setRunKey((k) => k + 1);
          }}
        >
          Запустить
        </Button>
      </Box>
    );
  }

  return (
    <Box sx={{ position: 'relative', height: '100%', minHeight: 320 }}>
      <Box
        sx={{
          position: 'absolute',
          top: 8,
          right: 8,
          zIndex: 1,
        }}
      >
        <Button
          size="small"
          variant="outlined"
          startIcon={<RunIcon />}
          onClick={() => setRunKey((k) => k + 1)}
          sx={{ bgcolor: 'rgba(255,255,255,0.92)' }}
        >
          Перезапустить
        </Button>
      </Box>
      <Box
        component="iframe"
        key={runKey}
        title={`${kindLabel(kind)} sandbox`}
        sandbox="allow-scripts"
        srcDoc={srcDoc}
        sx={{
          width: '100%',
          height: '100%',
          minHeight: 320,
          border: 0,
          bgcolor: '#fff',
          display: 'block',
        }}
      />
    </Box>
  );
}
