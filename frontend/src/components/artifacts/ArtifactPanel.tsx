import React, { useCallback, useMemo, useState } from 'react';
import {
  Box,
  Drawer,
  IconButton,
  Tab,
  Tabs,
  Tooltip,
  Typography,
} from '@mui/material';
import {
  Close as CloseIcon,
  ContentCopy as CopyIcon,
  GetApp as DownloadIcon,
  Refresh as RefreshIcon,
} from '@mui/icons-material';
import Editor from '@monaco-editor/react';
import { useArtifactContext } from '../../contexts/ArtifactContext';
import { useAppActions } from '../../contexts/AppContext';
import { artifactTypeLabel, guessCodeLanguage } from '../../utils/artifacts';
import { STATUS_TOAST_MESSAGES } from '../../constants/statusToast';
import ArtifactPreview from './ArtifactPreview';

const PANEL_WIDTH = 480;

function downloadText(filename: string, content: string) {
  const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function extensionForType(type: string): string {
  const lang = guessCodeLanguage(type);
  if (lang === 'html') return 'html';
  if (lang === 'xml') return 'svg';
  if (lang === 'markdown') return 'md';
  if (lang === 'mermaid') return 'mmd';
  if (lang === 'tsx') return 'tsx';
  return 'txt';
}

export default function ArtifactPanel() {
  const { current, isOpen, tab, setTab, closeArtifact, updateArtifact } = useArtifactContext();
  const { showNotification } = useAppActions();
  const [previewKey, setPreviewKey] = useState(0);

  const language = useMemo(
    () => (current ? guessCodeLanguage(current.type) : 'plaintext'),
    [current],
  );

  const handleCopy = useCallback(async () => {
    if (!current) return;
    try {
      await navigator.clipboard.writeText(current.content || '');
      showNotification('success', STATUS_TOAST_MESSAGES.COPY_SUCCESS_SHORT);
    } catch {
      showNotification('error', STATUS_TOAST_MESSAGES.COPY_FAILED);
    }
  }, [current, showNotification]);

  const handleDownload = useCallback(() => {
    if (!current) return;
    const safe = (current.identifier || 'artifact').replace(/[^\w.-]+/g, '_');
    downloadText(`${safe}.${extensionForType(current.type)}`, current.content || '');
  }, [current]);

  if (!current) {
    return null;
  }

  return (
    <>
      <Drawer
        anchor="right"
        open={isOpen}
        onClose={closeArtifact}
        variant="temporary"
        sx={{
          zIndex: (t) => t.zIndex.modal,
          '& .MuiDrawer-paper': {
            width: { xs: '100%', sm: PANEL_WIDTH },
            boxSizing: 'border-box',
            top: { xs: 0, sm: 64 },
            height: { xs: '100%', sm: 'calc(100% - 64px)' },
            borderLeft: '1px solid',
            borderColor: 'divider',
          },
        }}
      >
        <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
          <Box
            sx={{
              px: 1.5,
              py: 1,
              display: 'flex',
              alignItems: 'center',
              gap: 1,
              borderBottom: '1px solid',
              borderColor: 'divider',
            }}
          >
            <Box sx={{ minWidth: 0, flex: 1 }}>
              <Typography variant="subtitle1" noWrap sx={{ fontWeight: 600, lineHeight: 1.2 }}>
                {current.title}
              </Typography>
              <Typography variant="caption" color="text.secondary">
                {artifactTypeLabel(current.type)}
                {!current.closed ? ' · пишется…' : ''}
              </Typography>
            </Box>
            <Tooltip title="Обновить предпросмотр">
              <IconButton size="small" onClick={() => setPreviewKey((k) => k + 1)}>
                <RefreshIcon fontSize="small" />
              </IconButton>
            </Tooltip>
            <Tooltip title="Копировать">
              <IconButton size="small" onClick={handleCopy}>
                <CopyIcon fontSize="small" />
              </IconButton>
            </Tooltip>
            <Tooltip title="Скачать">
              <IconButton size="small" onClick={handleDownload}>
                <DownloadIcon fontSize="small" />
              </IconButton>
            </Tooltip>
            <IconButton size="small" onClick={closeArtifact} aria-label="Закрыть">
              <CloseIcon fontSize="small" />
            </IconButton>
          </Box>

          <Tabs
            value={tab}
            onChange={(_, v) => setTab(v)}
            variant="fullWidth"
            sx={{ minHeight: 40, borderBottom: 1, borderColor: 'divider' }}
          >
            <Tab value="preview" label="Просмотр" sx={{ minHeight: 40, py: 0 }} />
            <Tab value="code" label="Код" sx={{ minHeight: 40, py: 0 }} />
          </Tabs>

          <Box sx={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
            {tab === 'preview' ? (
              <Box key={previewKey} sx={{ height: '100%', overflow: 'auto' }}>
                <ArtifactPreview artifact={current} isStreaming={!current.closed} />
              </Box>
            ) : (
              <Editor
                height="100%"
                language={language}
                value={current.content || ''}
                theme="vs-dark"
                options={{
                  readOnly: false,
                  minimap: { enabled: false },
                  fontSize: 13,
                  wordWrap: 'on',
                  scrollBeyondLastLine: false,
                }}
                onChange={(value) => {
                  updateArtifact({
                    ...current,
                    content: value ?? '',
                  });
                }}
              />
            )}
          </Box>
        </Box>
      </Drawer>
    </>
  );
}
