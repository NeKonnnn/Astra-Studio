import React, { useState } from 'react';
import { Box, Collapse, Typography, Tooltip, useTheme } from '@mui/material';
import {
  Description as FileIcon,
  PictureAsPdf as PdfIcon,
  TableChart as ExcelIcon,
  TextSnippet as TxtIcon,
  Article as DocxIcon,
  KeyboardArrowDown as KeyboardArrowDownIcon,
} from '@mui/icons-material';

export type DocumentSearchTrace = {
  query: string;
  sourceFiles?: string[];
  hits?: Array<{
    file: string;
    anchor: string;
    relevance: number;
    content: string;
    chunkIndex: number;
    documentId: number;
    store: string;
  }>;
};

function getFileIconBg(filename: string): string {
  const ext = filename.split('.').pop()?.toLowerCase() || '';
  if (ext === 'pdf') return '#e53935';
  if (['docx', 'doc'].includes(ext)) return '#1976d2';
  if (['xlsx', 'xls', 'csv'].includes(ext)) return '#43a047';
  if (ext === 'txt') return '#607d8b';
  return '#5c6bc0';
}

function getFileTypeLabel(filename: string): string {
  const ext = filename.split('.').pop()?.toLowerCase() || '';
  if (ext === 'pdf') return 'PDF';
  if (['docx', 'doc'].includes(ext)) return 'Word';
  if (['xlsx', 'xls', 'csv'].includes(ext)) return 'Excel';
  if (ext === 'txt') return 'TXT';
  return 'File';
}

const fileIconSx = { fontSize: 14, color: 'white' };

function getFileIcon(filename: string) {
  const ext = filename.split('.').pop()?.toLowerCase() || '';
  if (ext === 'pdf') return <PdfIcon sx={fileIconSx} />;
  if (['xlsx', 'xls', 'csv'].includes(ext)) return <ExcelIcon sx={fileIconSx} />;
  if (ext === 'txt') return <TxtIcon sx={fileIconSx} />;
  if (['docx', 'doc'].includes(ext)) return <DocxIcon sx={fileIconSx} />;
  return <FileIcon sx={fileIconSx} />;
}

const CARD_H = 46;
const ICON_BOX = 26;

/** Панель «база знаний»: файлы только из фактических хитов retrieval; sourceFiles — запасной вариант без hits */
export function DocumentSearchPanel({ trace }: { trace: DocumentSearchTrace }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';
  const [sourcesOpen, setSourcesOpen] = useState(true);
  const [searchOpen, setSearchOpen] = useState(false);

  const hits = trace.hits ?? [];
  const sourceFiles = trace.sourceFiles ?? [];
  const fromHits = Array.from(
    new Set(hits.map((h) => h.file).filter(Boolean))
  ).sort();
  const files =
    fromHits.length > 0 ? fromHits : [...sourceFiles].sort();

  const subtleToggleSx = {
    display: 'flex',
    alignItems: 'center',
    gap: 0.5,
    userSelect: 'none' as const,
    cursor: 'pointer',
    width: 'fit-content',
    maxWidth: '100%',
    py: 0.25,
    transition: 'opacity 0.2s',
    '&:hover': { opacity: 0.75 },
  };

  const titleColor = isDark ? 'rgba(255,255,255,0.9)' : 'rgba(0,0,0,0.85)';
  const cardBg = isDark ? '#2a2d3a' : theme.palette.grey[100];
  const cardBorder = isDark ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.08)';
  const nameColor = isDark ? '#fff' : theme.palette.text.primary;
  const typeColor = isDark ? 'rgba(255,255,255,0.45)' : theme.palette.text.secondary;

  return (
    <Box sx={{ mb: 2, width: '100%' }}>
      {/* 1. Исходные документы — как выбор модели: без рамки, сворачивается */}
      <Box
        onClick={() => setSourcesOpen((v) => !v)}
        sx={subtleToggleSx}
        role="button"
        aria-expanded={sourcesOpen}
        aria-label={sourcesOpen ? 'Свернуть список документов' : 'Показать список документов'}
      >
        <Typography
          variant="body2"
          sx={{ color: titleColor, fontWeight: 400, fontSize: '0.9rem' }}
        >
          Исходные документы
          {files.length > 0 ? ` (${files.length})` : ''}
        </Typography>
        <KeyboardArrowDownIcon
          fontSize="small"
          sx={{
            color: titleColor,
            transform: sourcesOpen ? 'rotate(180deg)' : 'none',
            transition: 'transform 0.2s',
          }}
        />
      </Box>

      <Collapse in={sourcesOpen}>
        <Box
          sx={{
            mt: 1,
            mb: 1.5,
            display: 'grid',
            gridTemplateColumns: 'repeat(3, minmax(0, 1fr))',
            gap: 0.5,
            minWidth: 0,
            width: '100%',
          }}
        >
          {files.length === 0 ? (
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ gridColumn: '1 / -1' }}
            >
              Нет совпадений по файлам
            </Typography>
          ) : (
            files.map((fn) => (
              <Tooltip key={fn} title={fn} placement="top" arrow>
                <Box
                  sx={{
                    height: CARD_H,
                    minHeight: CARD_H,
                    maxHeight: CARD_H,
                    boxSizing: 'border-box',
                    position: 'relative',
                    borderRadius: 1,
                    bgcolor: cardBg,
                    border: `1px solid ${cardBorder}`,
                    px: 0.5,
                    py: 0.35,
                    minWidth: 0,
                    display: 'flex',
                    alignItems: 'center',
                    overflow: 'hidden',
                  }}
                >
                  <Box
                    sx={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 0.45,
                      minWidth: 0,
                      width: '100%',
                    }}
                  >
                    <Box
                      sx={{
                        width: ICON_BOX,
                        height: ICON_BOX,
                        minWidth: ICON_BOX,
                        borderRadius: 0.75,
                        bgcolor: getFileIconBg(fn),
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        flexShrink: 0,
                      }}
                    >
                      {getFileIcon(fn)}
                    </Box>
                    <Box sx={{ minWidth: 0, flex: 1, overflow: 'hidden' }}>
                      <Typography
                        variant="caption"
                        noWrap
                        component="div"
                        sx={{
                          color: nameColor,
                          fontSize: '0.6rem',
                          fontWeight: 500,
                          lineHeight: 1.3,
                        }}
                      >
                        {fn}
                      </Typography>
                      <Typography
                        variant="caption"
                        sx={{
                          color: typeColor,
                          fontSize: '0.55rem',
                          lineHeight: 1.2,
                          display: 'block',
                        }}
                      >
                        {getFileTypeLabel(fn)}
                      </Typography>
                    </Box>
                  </Box>
                </Box>
              </Tooltip>
            ))
          )}
        </Box>
      </Collapse>

      {/* 3. Детали поиска — без контейнера, как выбор модели */}
      <Box
        onClick={() => setSearchOpen((v) => !v)}
        sx={{ ...subtleToggleSx, mt: 0.25 }}
        role="button"
        aria-expanded={searchOpen}
      >
        <Typography
          variant="body2"
          sx={{ color: titleColor, fontWeight: 400, fontSize: '0.9rem' }}
        >
          Выполнен поиск по документам
        </Typography>
        <KeyboardArrowDownIcon
          fontSize="small"
          sx={{
            color: titleColor,
            transform: searchOpen ? 'rotate(180deg)' : 'none',
            transition: 'transform 0.2s',
          }}
        />
      </Box>

      <Collapse in={searchOpen}>
        <Box
          sx={{
            mt: 1,
            p: 2,
            borderRadius: 1,
            border: '1px solid',
            borderColor: 'divider',
            bgcolor: isDark ? 'action.hover' : 'action.hover',
          }}
        >
          <Typography variant="body2" sx={{ mb: 1 }}>
            AstraChat использовал поиск по документам со следующим запросом:
          </Typography>
          <Box
            component="pre"
            sx={{
              m: 0,
              p: 1.5,
              borderRadius: 1,
              fontSize: '0.8rem',
              overflow: 'auto',
              bgcolor: 'background.default',
              border: '1px solid',
              borderColor: 'divider',
              fontFamily: 'ui-monospace, monospace',
            }}
          >
            {JSON.stringify({ query: trace.query }, null, 2)}
          </Box>

          <Typography variant="subtitle2" sx={{ mt: 2, mb: 1, fontWeight: 600 }}>
            Результат
          </Typography>
          {hits.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              Фрагменты не найдены.
            </Typography>
          ) : (
            hits.map((h, i) => (
              <Box
                key={`${h.file}-${h.chunkIndex}-${i}`}
                sx={{
                  mb: i < hits.length - 1 ? 2 : 0,
                  pb: i < hits.length - 1 ? 2 : 0,
                  borderBottom:
                    i < hits.length - 1 ? '1px solid' : 'none',
                  borderColor: 'divider',
                }}
              >
                <Typography variant="caption" component="div" sx={{ display: 'block' }}>
                  <strong>File</strong>: {h.file}
                </Typography>
                <Typography variant="caption" component="div" sx={{ display: 'block' }}>
                  <strong>Anchor</strong>: {h.anchor}
                </Typography>
                <Typography variant="caption" component="div" sx={{ display: 'block' }}>
                  <strong>Relevance</strong>: {(() => {
                    const r = Number(h.relevance) || 0;
                    const pct = r > 0 && r <= 1 ? Math.round(r * 100) : Math.round(r);
                    return `${Math.max(1, Math.min(100, pct || 1))}%`;
                  })()}
                  {h.store ? ` (${h.store})` : ''}
                </Typography>
                <Typography
                  variant="caption"
                  component="div"
                  sx={{ display: 'block', mt: 0.5 }}
                >
                  <strong>Content</strong>:
                </Typography>
                <Typography
                  variant="body2"
                  sx={{ whiteSpace: 'pre-wrap', pl: 0.5, mt: 0.25, opacity: 0.95 }}
                >
                  {h.content}
                </Typography>
              </Box>
            ))
          )}
        </Box>
      </Collapse>
    </Box>
  );
}
