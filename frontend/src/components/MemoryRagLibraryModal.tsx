import React, { useState, useRef, useEffect, useMemo } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Box,
  Typography,
  List,
  ListItem,
  ListItemIcon,
  ListItemText,
  IconButton,
  LinearProgress,
  Alert,
  Chip,
  Tooltip,
} from '@mui/material';
import {
  CloudUpload as UploadIcon,
  Delete as DeleteIcon,
  Description as DocumentIcon,
  PictureAsPdf as PdfIcon,
  TableChart as ExcelIcon,
  TextSnippet as TxtIcon,
  Article as ArticleIcon,
  Close as CloseIcon,
  LibraryBooks as LibraryIcon,
  Image as ImageIcon,
} from '@mui/icons-material';
import { getApiUrl, API_ENDPOINTS, getAuthFetchHeaders } from '../config/api';
import { useAppActions } from '../contexts/AppContext';
import { ragDocumentDisplayIndex } from '../utils/ragDocumentDisplayIndex';
import { filterByRagFilenameQuery } from '../utils/ragFilesSearch';
import RagFilesSearchField from './RagFilesSearchField';
import RagUploadingFileThumb from './RagUploadingFileThumb';
import {
  commitRagUploadUiUpdate,
  createRagPendingUploads,
  formatRagApiDetail,
  getRagFileTypeLabel,
  mapWithConcurrency,
  mergeRagDocumentsById,
  parseRagDuplicateUploadMessage,
  parseRagUploadDocumentId,
  ragWarningAlertSx,
  removeRagPendingUploads,
  RAG_UPLOAD_CONCURRENCY,
  type RagPendingUpload,
} from '../utils/ragPendingUpload';

export interface MemoryRagDoc {
  id: number;
  filename: string;
  created_at: string | null;
  size?: number | null;
  file_type?: string | null;
}

function formatFileSize(bytes: number | null | undefined): string {
  if (bytes == null || !bytes) return '—';
  if (bytes < 1024) return `${bytes} Б`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} КБ`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`;
}

function getFileIcon(filename: string) {
  const ext = filename.split('.').pop()?.toLowerCase() || '';
  if (ext === 'pdf') return <PdfIcon sx={{ color: '#e53935' }} />;
  if (['xlsx', 'xls', 'csv'].includes(ext)) return <ExcelIcon sx={{ color: '#43a047' }} />;
  if (ext === 'txt') return <TxtIcon sx={{ color: '#1e88e5' }} />;
  if (['docx', 'doc'].includes(ext)) return <ArticleIcon sx={{ color: '#1565c0' }} />;
  if(['jpg', 'jpeg', 'png', 'webp','bmp'].includes(ext)) return <ImageIcon sx={{ color: '#8e24aa' }} />;
  return <DocumentIcon sx={{ color: '#7b1fa2' }} />;
}

interface Props {
  open: boolean;
  onClose: () => void;
}

async function fetchDocumentList(): Promise<MemoryRagDoc[]> {
  const { ensurePersonalLibraryKbId } = await import('../utils/personalLibraryKb');
  const kbId = await ensurePersonalLibraryKbId();
  if (kbId == null) return [];
  const resp = await fetch(getApiUrl(API_ENDPOINTS.KB_DOCUMENTS_LIST), {
    headers: getAuthFetchHeaders(),
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const data = await resp.json();
  const rows: unknown[] = Array.isArray(data) ? data : data.documents || [];
  return rows
    .map((row) => {
      const r = row as Record<string, unknown>;
      const meta = (r.metadata && typeof r.metadata === 'object'
        ? (r.metadata as Record<string, unknown>)
        : {}) as Record<string, unknown>;
      try {
        if (Number(meta.knowledge_base_id ?? r.knowledge_base_id) !== kbId) return null;
      } catch {
        return null;
      }
      return {
        id: Number(r.id),
        filename: String(r.filename || ''),
        created_at: (r.created_at as string | null) ?? null,
        size: (r.size as number | null) ?? null,
        file_type: (r.file_type as string | null) ?? null,
      } as MemoryRagDoc;
    })
    .filter((d): d is MemoryRagDoc => d != null && Number.isFinite(d.id));
}

export default function MemoryRagLibraryModal({ open, onClose }: Props) {
  const [documents, setDocuments] = useState<MemoryRagDoc[]>([]);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [pendingUploads, setPendingUploads] = useState<RagPendingUpload[]>([]);
  const [fileSearchQuery, setFileSearchQuery] = useState('');
  const [banner, setBanner] = useState<{
    message: string;
    severity: 'success' | 'error' | 'info' | 'warning';
  } | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const { showNotification } = useAppActions();
  const notifyRef = useRef(showNotification);
  notifyRef.current = showNotification;

  const filteredPendingUploads = useMemo(
    () => filterByRagFilenameQuery(pendingUploads, fileSearchQuery),
    [pendingUploads, fileSearchQuery],
  );
  const filteredDocuments = useMemo(
    () => filterByRagFilenameQuery(documents, fileSearchQuery),
    [documents, fileSearchQuery],
  );
  const totalFiles = documents.length + pendingUploads.length;
  const filteredTotal = filteredDocuments.length + filteredPendingUploads.length;
  const hasActiveFileSearch = fileSearchQuery.trim().length > 0;

  useEffect(() => {
    if (!open) {
      setBanner(null);
      setFileSearchQuery('');
      return;
    }
    let cancelled = false;
    setLoading(true);
    (async () => {
      try {
        const docs = await fetchDocumentList();
        if (!cancelled) setDocuments(docs);
      } catch (e) {
        if (!cancelled) notifyRef.current('error', `Не удалось загрузить список: ${e}`);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open]);

  const loadDocuments = async () => {
    setLoading(true);
    try {
      setDocuments(await fetchDocumentList());
    } catch (e) {
      notifyRef.current('error', `Не удалось загрузить список: ${e}`);
    } finally {
      setLoading(false);
    }
  };

  const allowed = ['.pdf', '.docx', '.doc', '.docm', '.xlsx', '.xls', '.xlsm', '.ppt', '.pptx', '.txt', '.csv', '.md', '.log', '.rtf', '.jpg', '.jpeg', '.png', '.webp', '.bmp'];

  const uploadFiles = async (files: FileList) => {
    if (!files.length) return;
    const valid = Array.from(files).filter((f) => {
      const ext = '.' + f.name.split('.').pop()?.toLowerCase();
      return allowed.includes(ext);
    });
    if (!valid.length) {
      setBanner({
        message: 'Допустимы: PDF, DOC/DOCX/DOCM, XLS/XLSX/XLSM, PPT/PPTX, TXT, MD, CSV, LOG, RTF, JPG, JPEG, PNG, WEBP, BMP (OCR)',
        severity: 'error',
      });
      return;
    }

    const pendingEntries = createRagPendingUploads(valid);
    const dropPending = (ids: string[]) => {
      setPendingUploads((prev) => removeRagPendingUploads(prev, ids));
    };

    const { ensurePersonalLibraryKbId } = await import('../utils/personalLibraryKb');
    const kbId = await ensurePersonalLibraryKbId();
    if (kbId == null) {
      dropPending(pendingEntries.map((entry) => entry.clientId));
      setBanner({
        message: 'Не удалось подготовить личную базу знаний',
        severity: 'error',
      });
      return;
    }

    setUploading(true);
    setBanner(null);
    setPendingUploads((prev) => [...prev, ...pendingEntries]);

    let stopRemaining = false;
    let successCount = 0;
    let duplicateCount = 0;
    let lastDuplicateMsg: string | null = null;

    await mapWithConcurrency(valid, RAG_UPLOAD_CONCURRENCY, async (file, index) => {
      if (stopRemaining) {
        const pendingId = pendingEntries[index]?.clientId;
        if (pendingId) {
          commitRagUploadUiUpdate(() => {
            setPendingUploads((prev) => removeRagPendingUploads(prev, [pendingId]));
          });
        }
        return;
      }

      const pendingId = pendingEntries[index]?.clientId;
      try {
        const fd = new FormData();
        fd.append('file', file);
        fd.append('knowledge_base_id', String(kbId));
        const resp = await fetch(getApiUrl(API_ENDPOINTS.KB_DOCUMENTS_UPLOAD), {
          method: 'POST',
          headers: getAuthFetchHeaders(),
          body: fd,
        });
        if (!resp.ok) {
          const err = await resp.json().catch(() => ({ detail: resp.statusText }));
          const duplicateMsg = parseRagDuplicateUploadMessage(resp.status, err, file.name);
          if (duplicateMsg) {
            duplicateCount += 1;
            lastDuplicateMsg = duplicateMsg;
            setBanner({ message: duplicateMsg, severity: 'warning' });
            if (pendingId) {
              commitRagUploadUiUpdate(() => {
                setPendingUploads((prev) => removeRagPendingUploads(prev, [pendingId]));
              });
            }
            return;
          }
          throw new Error(formatRagApiDetail(err.detail, resp.statusText));
        }
        const data = (await resp.json().catch(() => ({}))) as Record<string, unknown>;
        const docId = parseRagUploadDocumentId(data);
        successCount += 1;
        commitRagUploadUiUpdate(() => {
          if (docId != null) {
            setDocuments((prev) => {
              if (prev.some((d) => d.id === docId)) return prev;
              return [
                {
                  id: docId,
                  filename: file.name,
                  size: file.size,
                  file_type: getRagFileTypeLabel(file.name),
                  created_at: new Date().toISOString(),
                },
                ...prev,
              ];
            });
          }
          if (pendingId) {
            setPendingUploads((prev) => removeRagPendingUploads(prev, [pendingId]));
          }
        });
        if (docId == null) {
          try {
            const fresh = await fetchDocumentList();
            commitRagUploadUiUpdate(() => {
              setDocuments((prev) => mergeRagDocumentsById(prev, fresh));
            });
          } catch {
            /* ignore */
          }
        }
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : String(e);
        setBanner({ message: `${file.name}: ${msg}`, severity: 'error' });
        stopRemaining = true;
        if (pendingId) {
          commitRagUploadUiUpdate(() => {
            setPendingUploads((prev) => removeRagPendingUploads(prev, [pendingId]));
          });
        }
      }
    });

    setUploading(false);
    if (stopRemaining) {
      try {
        setDocuments(await fetchDocumentList());
      } catch {
        /* ignore */
      }
      commitRagUploadUiUpdate(() => {
        setPendingUploads([]);
      });
      return;
    }
    if (successCount > 0 && duplicateCount > 0) {
      setBanner({
        message: `Загружено: ${successCount}. Пропущено дубликатов: ${duplicateCount}.`,
        severity: 'warning',
      });
    } else if (successCount > 0) {
      setBanner({
        message: `Загружено файлов: ${successCount}. Исходники сохранены в хранилище, текст проиндексирован в PostgreSQL (pgvector).`,
        severity: 'success',
      });
    } else if (lastDuplicateMsg) {
      setBanner({ message: lastDuplicateMsg, severity: 'warning' });
    }
    void fetchDocumentList()
      .then((fresh) => {
        setDocuments((prev) => mergeRagDocumentsById(prev, fresh));
      })
      .catch(() => undefined);
  };

  const handleDelete = async (doc: MemoryRagDoc) => {
    try {
      const url = `${getApiUrl(API_ENDPOINTS.KB_DOCUMENTS_DELETE)}/${doc.id}`;
      const resp = await fetch(url, { method: 'DELETE' });
      if (!resp.ok) {
        // Текст из detail объясняет причину («хранилище недоступно»),
        // голый код ответа пользователю ничего не говорит.
        const detail = await resp.json().then(d => d?.detail).catch(() => null);
        throw new Error(detail || `HTTP ${resp.status}`);
      }
      setBanner({ message: `«${doc.filename}» удалён`, severity: 'success' });
      setDocuments(await fetchDocumentList());
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setBanner({ message: `Ошибка удаления: ${msg}`, severity: 'error' });
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="md"
      fullWidth
      scroll="paper"
      disableScrollLock
      disableRestoreFocus
      slotProps={{
        root: {
          sx: { zIndex: 2000 },
        },
      }}
    >
      <DialogTitle sx={{ display: 'flex', alignItems: 'center', gap: 1, pr: 6 }}>
        <LibraryIcon color="primary" />
        <Box flex={1}>
          <Typography variant="h6" component="span">
            Общая библиотека документов
          </Typography>
          <Typography variant="caption" color="text.secondary" display="block">
            Общие файлы для любого чата. Не путать с документами проекта или документами агента.
          </Typography>
        </Box>
        <IconButton aria-label="закрыть" onClick={onClose} sx={{ position: 'absolute', right: 8, top: 8 }}>
          <CloseIcon />
        </IconButton>
      </DialogTitle>
      <DialogContent dividers>
        {banner && (
          <Alert
            severity={banner.severity}
            onClose={() => setBanner(null)}
            sx={banner.severity === 'warning' ? ragWarningAlertSx : { mb: 2 }}
          >
            {banner.message}
          </Alert>
        )}
        <Box
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault();
            if (e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files);
          }}
          sx={{
            border: '2px dashed',
            borderColor: 'divider',
            borderRadius: 2,
            p: 3,
            textAlign: 'center',
            mb: 2,
            bgcolor: 'action.hover',
          }}
        >
          <input
            ref={fileInputRef}
            type="file"
            multiple
            hidden
            accept=".pdf,.doc,.docx,.docm,.xls,.xlsx,.xlsm,.ppt,.pptx,.txt,.md,.log,.csv,.rtf,.jpg,.jpeg,.png,.webp,.bmp"
            onChange={(e) => {
              if (e.target.files?.length) uploadFiles(e.target.files);
              e.target.value = '';
            }}
          />
          <UploadIcon sx={{ fontSize: 40, color: 'text.secondary', mb: 1 }} />
          <Typography variant="body2" gutterBottom>
            Перетащите файлы сюда или нажмите кнопку
          </Typography>
          <Button
            variant="contained"
            startIcon={<UploadIcon />}
            disabled={uploading}
            onClick={() => fileInputRef.current?.click()}
          >
            Выбрать файлы
          </Button>
        </Box>

        <Typography variant="subtitle2" gutterBottom>
          Проиндексированные документы (
          {hasActiveFileSearch ? `${filteredTotal} из ${totalFiles}` : totalFiles})
        </Typography>
        {totalFiles > 0 && (
          <RagFilesSearchField
            value={fileSearchQuery}
            onChange={setFileSearchQuery}
            totalCount={totalFiles}
            disabled={loading}
          />
        )}
        {loading ? (
          <LinearProgress />
        ) : documents.length === 0 && pendingUploads.length === 0 ? (
          <Typography color="text.secondary" variant="body2">
            Пока нет документов. После загрузки включите «Учитывать в ответах чата» здесь в настройках или кнопку
            «Общий RAG» в чате.
          </Typography>
        ) : filteredTotal === 0 ? (
          <Typography color="text.secondary" variant="body2">
            Ничего не найдено по запросу «{fileSearchQuery.trim()}».
          </Typography>
        ) : (
          <List dense disablePadding>
            {filteredPendingUploads.map((pending) => (
              <ListItem
                key={pending.clientId}
                sx={{ borderBottom: 1, borderColor: 'divider' }}
              >
                <ListItemIcon sx={{ minWidth: 40 }}>
                  <RagUploadingFileThumb filename={pending.filename} size={28} />
                </ListItemIcon>
                <ListItemText
                  primary={pending.filename}
                  secondary={getRagFileTypeLabel(pending.filename)}
                />
              </ListItem>
            ))}
            {filteredDocuments.map((doc) => (
              <ListItem
                key={doc.id}
                secondaryAction={
                  <Tooltip title="Удалить из БД и MinIO">
                    <IconButton edge="end" onClick={() => handleDelete(doc)} color="error" size="small">
                      <DeleteIcon />
                    </IconButton>
                  </Tooltip>
                }
                sx={{ borderBottom: 1, borderColor: 'divider' }}
              >
                <ListItemIcon sx={{ minWidth: 40 }}>{getFileIcon(doc.filename)}</ListItemIcon>
                <ListItemText
                  primary={doc.filename}
                  secondary={
                    <>
                      {formatFileSize(doc.size ?? null)}
                      {doc.created_at && (
                        <>
                          {' · '}
                          {new Date(doc.created_at).toLocaleString('ru-RU')}
                        </>
                      )}
                    </>
                  }
                />
                <Chip
                  size="small"
                  label={`#${ragDocumentDisplayIndex(documents, doc.id)}`}
                  sx={{ mr: 4 }}
                />
              </ListItem>
            ))}
          </List>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={loadDocuments} disabled={loading}>
          Обновить список
        </Button>
        <Button onClick={onClose} variant="contained">
          Закрыть
        </Button>
      </DialogActions>
    </Dialog>
  );
}
