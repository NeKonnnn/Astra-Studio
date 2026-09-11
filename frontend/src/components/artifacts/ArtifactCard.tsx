import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Box,
  CircularProgress,
  Collapse,
  IconButton,
  Popover,
  Tooltip,
  Typography,
  useTheme,
} from '@mui/material';
import {
  Code as CodeIcon,
  ExpandLess as ExpandLessIcon,
  OpenInNew as OpenInNewIcon,
  ContentCopy as CopyIcon,
  Check as CheckIcon,
  GetApp as DownloadIcon,
  Image as PngIcon,
  Photo as JpgIcon,
} from '@mui/icons-material';
import Editor from '@monaco-editor/react';
import {
  openArtifactViewer,
  sourceLabelForArtifactType,
} from '../../utils/artifactViewer';
import {
  exportArtifactPreviewAsJpg,
  exportArtifactPreviewAsPng,
} from '../../utils/artifactDownload';
import { isGpbPresentationHtml, openPresentationViewer } from '../../utils/presentationViewer';
import ArtifactPreview from './ArtifactPreview';
import type { ChatArtifact } from '../../types/artifacts';
import { artifactTypeLabel, guessCodeLanguage } from '../../utils/artifacts';
import {
  getDropdownPanelSx,
  getDropdownItemSx,
  MENU_ACTION_TEXT_SIZE,
} from '../../constants/menuStyles';
import { useInViewport } from '../../hooks/useInViewport';
import ThinkingShimmerText from '../chat/ThinkingShimmerText';

const DOWNLOAD_PANEL_W = 160;

interface Props {
  artifact: ChatArtifact;
  isStreaming?: boolean;
  autoOpen?: boolean;
}

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

/**
 * Viewer артефакта в чате — UX как InlinePresentationViewer:
 * превью + «Показать Mermaid/HTML/…» (Collapse снизу) + «Открыть в новой вкладке».
 */
export default function ArtifactCard({ artifact, isStreaming = false }: Props) {
  const theme = useTheme();
  const isDarkMode = theme.palette.mode === 'dark';
  const [showSource, setShowSource] = useState(false);
  const [localContent, setLocalContent] = useState(artifact.content || '');
  const [copied, setCopied] = useState(false);
  const [downloadAnchorEl, setDownloadAnchorEl] = useState<null | HTMLElement>(null);
  const downloadWindowSx = useMemo(
    () => ({ ...getDropdownPanelSx(isDarkMode) }) as Record<string, unknown>,
    [isDarkMode],
  );
  const downloadItemSx = useMemo(() => getDropdownItemSx(isDarkMode), [isDarkMode]);
  const downloadTextColor = isDarkMode ? 'rgba(255,255,255,0.85)' : 'rgba(0,0,0,0.85)';
  const previewRootRef = React.useRef<HTMLDivElement>(null);
  const { ref: cardViewportRef, inView } = useInViewport<HTMLDivElement>(
    '280px 0px',
    Boolean(isStreaming && !artifact.closed),
  );
  const editorPath = useMemo(
    () => `artifact://${artifact.id || artifact.identifier || 'src'}.${extensionForType(artifact.type)}`,
    [artifact.id, artifact.identifier, artifact.type],
  );

  useEffect(() => {
    if (isStreaming) return;
    const next = artifact.content || '';
    setLocalContent((prev) => (prev === next ? prev : next));
  }, [artifact.id, artifact.content, isStreaming]);

  const language = useMemo(() => guessCodeLanguage(artifact.type), [artifact.type]);
  const pending = isStreaming && !artifact.closed;
  const sourceKind = sourceLabelForArtifactType(artifact.type);
  const previewContent = isStreaming ? (artifact.content || '') : localContent;

  const displayArtifact = useMemo(
    () => ({
      ...artifact,
      content: previewContent,
    }),
    [artifact, previewContent],
  );

  const statusLabel = (() => {
    const kind = artifactTypeLabel(artifact.type);
    if (pending) return `${artifact.title || kind} · генерация…`;
    return artifact.title || kind;
  })();

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(localContent || '');
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      /* ignore */
    }
  }, [localContent]);

  const handleDownload = useCallback(() => {
    const safe = (artifact.identifier || 'artifact').replace(/[^\w.-]+/g, '_');
    downloadText(`${safe}.${extensionForType(artifact.type)}`, localContent || '');
  }, [artifact.identifier, artifact.type, localContent]);

  const downloadMenuOpen = Boolean(downloadAnchorEl);
  const safeArtifactBase = useMemo(
    () => (artifact.identifier || artifact.title || 'artifact').replace(/[^\w.-]+/g, '_'),
    [artifact.identifier, artifact.title],
  );

  const handleExportPng = useCallback(() => {
    setDownloadAnchorEl(null);
    void exportArtifactPreviewAsPng({
      previewRoot: previewRootRef.current,
      fileNameBase: safeArtifactBase,
    }).catch((err) => {
      const message = err instanceof Error ? err.message : 'Не удалось скачать PNG.';
      window.alert(message);
    });
  }, [safeArtifactBase]);

  const handleExportJpg = useCallback(() => {
    setDownloadAnchorEl(null);
    void exportArtifactPreviewAsJpg({
      previewRoot: previewRootRef.current,
      fileNameBase: safeArtifactBase,
    }).catch((err) => {
      const message = err instanceof Error ? err.message : 'Не удалось скачать JPG.';
      window.alert(message);
    });
  }, [safeArtifactBase]);

  const handleOpenExternal = useCallback(() => {
    void (async () => {
      try {
        // GPB-презентации открываем своим viewer'ом (слайды / PPTX), не generic HTML.
        if (isGpbPresentationHtml(localContent)) {
          openPresentationViewer(localContent);
          return;
        }
        await openArtifactViewer({
          type: artifact.type,
          content: localContent,
          title: artifact.title || artifactTypeLabel(artifact.type),
          previewRoot: previewRootRef.current,
        });
      } catch (e) {
        console.error('Failed to open artifact viewer:', e);
      }
    })();
  }, [artifact.type, artifact.title, localContent]);

  const codeLineCount = Math.max(1, (localContent || '').split('\n').length);
  const editorHeight = Math.min(480, Math.max(200, Math.min(codeLineCount, 22) * 22 + 18));

  return (
    <Box
      ref={cardViewportRef}
      sx={{
        my: 2,
        borderRadius: 2,
        overflow: 'hidden',
        border: '1px solid',
        borderColor: 'divider',
        bgcolor: 'background.paper',
      }}
    >
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 1,
          px: 1.5,
          py: 0.75,
          borderBottom: '1px solid',
          borderColor: 'divider',
          bgcolor: (t) => (t.palette.mode === 'dark' ? 'rgba(255,255,255,0.04)' : 'rgba(0,0,0,0.02)'),
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, minWidth: 0 }}>
          {pending ? (
            <Box
              sx={{
                display: 'inline-flex',
                flexShrink: 0,
                animation: 'thinking 2s ease-in-out infinite',
              }}
            >
              <CircularProgress size={14} thickness={5} sx={{ color: 'primary.main' }} />
            </Box>
          ) : null}
          {pending ? (
            <ThinkingShimmerText
              isDarkMode={isDarkMode}
              fontSize="0.75rem"
              fontWeight={600}
              sx={{
                letterSpacing: 0.02,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                minWidth: 0,
                color: 'text.primary',
              }}
            >
              {statusLabel}
            </ThinkingShimmerText>
          ) : (
            <Typography
              variant="caption"
              sx={{
                fontWeight: 600,
                letterSpacing: 0.02,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                color: 'text.primary',
              }}
            >
              {statusLabel}
            </Typography>
          )}
        </Box>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.25 }}>
          {!pending ? (
            <>
              <Tooltip title="Скачать артефакт">
                <IconButton size="small" onClick={(e) => setDownloadAnchorEl(e.currentTarget)}>
                  <DownloadIcon fontSize="small" />
                </IconButton>
              </Tooltip>
              <Popover
                open={downloadMenuOpen}
                anchorEl={downloadAnchorEl}
                onClose={() => setDownloadAnchorEl(null)}
                anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
                transformOrigin={{ vertical: 'top', horizontal: 'left' }}
                slotProps={{
                  paper: {
                    sx: {
                      mt: 0.75,
                      p: 0,
                      overflow: 'visible',
                      background: 'transparent !important',
                      backgroundColor: 'transparent !important',
                      boxShadow: 'none !important',
                      backdropFilter: 'none',
                      border: 'none',
                    },
                  },
                }}
              >
                <Box
                  sx={{
                    ...downloadWindowSx,
                    width: DOWNLOAD_PANEL_W,
                    display: 'flex',
                    flexDirection: 'column',
                  }}
                >
                  <Box sx={{ py: 0.5, px: 0.5 }}>
                    <Box
                      onClick={handleExportPng}
                      sx={{
                        ...downloadItemSx,
                        display: 'flex',
                        alignItems: 'center',
                        gap: 1,
                        color: downloadTextColor,
                      }}
                    >
                      <PngIcon sx={{ fontSize: 18, color: '#2e7d32', flexShrink: 0 }} />
                      <Typography
                        sx={{
                          flex: 1,
                          minWidth: 0,
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                          fontSize: MENU_ACTION_TEXT_SIZE,
                        }}
                      >
                        PNG
                      </Typography>
                    </Box>
                    <Box
                      onClick={handleExportJpg}
                      sx={{
                        ...downloadItemSx,
                        display: 'flex',
                        alignItems: 'center',
                        gap: 1,
                        color: downloadTextColor,
                      }}
                    >
                      <JpgIcon sx={{ fontSize: 18, color: '#ed6c02', flexShrink: 0 }} />
                      <Typography
                        sx={{
                          flex: 1,
                          minWidth: 0,
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                          fontSize: MENU_ACTION_TEXT_SIZE,
                        }}
                      >
                        JPG
                      </Typography>
                    </Box>
                  </Box>
                </Box>
              </Popover>
            </>
          ) : null}
          {!pending ? (
            <Tooltip title={showSource ? `Скрыть ${sourceKind}` : `Показать ${sourceKind}`}>
              <IconButton size="small" onClick={() => setShowSource((v) => !v)}>
                {showSource ? <ExpandLessIcon fontSize="small" /> : <CodeIcon fontSize="small" />}
              </IconButton>
            </Tooltip>
          ) : null}
          {!pending ? (
            <Tooltip title="Открыть в новой вкладке">
              <IconButton size="small" onClick={handleOpenExternal}>
                <OpenInNewIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          ) : null}
        </Box>
      </Box>

      <Box
        sx={{
          width: '100%',
          aspectRatio: `${297} / ${167}`,
          maxHeight: 'min(88vh, 880px)',
          minHeight: 320,
          position: 'relative',
          // Светлый фон как у презентаций: подписи Mermaid/SVG (тёмный текст) остаются читаемыми
          // и в тёмной теме приложения.
          bgcolor: '#e8eaed',
          pb: '48px',
          overflow: 'hidden',
          // Жёстко гасим наследование color из пузыря сообщения (contrastText / theme).
          color: '#111827 !important',
          '& .MuiTypography-root': { color: '#111827 !important' },
        }}
      >
        <Box
          ref={previewRootRef}
          sx={{
            position: 'absolute',
            inset: 0,
            width: '100%',
            height: '100%',
            overflow: 'auto',
            color: '#111827 !important',
          }}
        >
          {inView || pending ? (
            <ArtifactPreview artifact={displayArtifact} isStreaming={isStreaming} />
          ) : (
            <Box sx={{ height: '100%', minHeight: 320, bgcolor: '#e8eaed' }} />
          )}
        </Box>
      </Box>

      {!pending ? (
        <Collapse in={showSource} unmountOnExit timeout={180}>
          <Box sx={{ borderTop: '1px solid', borderColor: 'divider' }}>
            <Box
              sx={{
                backgroundColor: '#1e1e1e',
                position: 'relative',
                overflow: 'hidden',
              }}
            >
              <Box
                sx={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  px: 2,
                  py: 1,
                  backgroundColor: '#2d2d30',
                  borderBottom: '1px solid #3e3e42',
                }}
              >
                <Typography
                  variant="caption"
                  sx={{
                    color: '#cccccc',
                    fontFamily: 'monospace',
                    textTransform: 'uppercase',
                    fontSize: '0.75rem',
                    fontWeight: 'bold',
                  }}
                >
                  {language}
                </Typography>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.25 }}>
                  <Tooltip title={copied ? '✓ Скопировано!' : 'Копировать код'}>
                    <IconButton
                      size="small"
                      onClick={handleCopy}
                      sx={{
                        color: '#cccccc',
                        '&:hover': { backgroundColor: 'rgba(255,255,255,0.1)', color: '#4ec9b0' },
                      }}
                    >
                      {copied ? (
                        <CheckIcon fontSize="small" sx={{ color: '#4ec9b0' }} />
                      ) : (
                        <CopyIcon fontSize="small" />
                      )}
                    </IconButton>
                  </Tooltip>
                  <Tooltip title="Скачать файл">
                    <IconButton
                      size="small"
                      onClick={handleDownload}
                      sx={{
                        color: '#cccccc',
                        '&:hover': { backgroundColor: 'rgba(255,255,255,0.1)', color: '#4ec9b0' },
                      }}
                    >
                      <DownloadIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                </Box>
              </Box>
              <Editor
                height={editorHeight}
                language={language}
                value={localContent}
                path={editorPath}
                theme="vs-dark"
                onMount={(editor) => {
                  requestAnimationFrame(() => {
                    editor.layout();
                    requestAnimationFrame(() => editor.layout());
                  });
                }}
                options={{
                  readOnly: false,
                  minimap: { enabled: false },
                  fontSize: 13,
                  wordWrap: 'on',
                  scrollBeyondLastLine: false,
                  automaticLayout: true,
                }}
                onChange={(value) => setLocalContent(value ?? '')}
              />
            </Box>
          </Box>
        </Collapse>
      ) : null}
    </Box>
  );
}