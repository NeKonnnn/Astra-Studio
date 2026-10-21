import React, { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import {
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
  Button,
} from '@mui/material';
import {
  CloudUpload as UploadIcon,
  Delete as DeleteIcon,
  Description as DocumentIcon,
  PictureAsPdf as PdfIcon,
  TableChart as ExcelIcon,
  TextSnippet as TxtIcon,
  Article as ArticleIcon,
  Refresh as RefreshIcon,
  Image as ImageIcon,
} from '@mui/icons-material';
import { getApiUrl, API_ENDPOINTS, getAuthFetchHeaders } from '../config/api';
import {
  fetchRagEntityDefaults,
  resolveRagEmbeddingModelPath,
  resolveRagRerankerModelPath,
} from '../constants/ragEntityDefaults';
import { useRagEntityReadyMessage } from '../hooks/useRagEntityReadyMessage';
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

export interface ProjectRagDoc {
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
  if (ext === 'txt' || ext === 'md') return <TxtIcon sx={{ color: '#1e88e5' }} />;
  if (['docx', 'doc'].includes(ext)) return <ArticleIcon sx={{ color: '#1565c0' }} />;
  if(['jpg', 'jpeg', 'png', 'webp','bmp'].includes(ext)) return <ImageIcon sx={{ color: '#8e24aa' }} />;
  return <DocumentIcon sx={{ color: '#7b1fa2' }} />;
}

const ALLOWED = ['.pdf', '.docx', '.doc', '.docm', '.xlsx', '.xls', '.xlsm', '.ppt', '.pptx', '.txt', '.csv', '.md', '.log', '.rtf', '.jpg', '.jpeg', '.png', '.webp', '.bmp'];

export interface ProjectRagLibraryInlineProps {
  /** null — до первого resolve (новый проект) */
  projectId: string | null;
  /** Вызывается перед первой загрузкой, если projectId ещё null; должен вернуть id проекта */
  onResolveProjectId?: () => string | Promise<string>;
  /** Загружать список при монтировании / смене id */
  autoLoad?: boolean;
  /** Подпись под заголовком */
  subtitle?: string;
  dense?: boolean;
}

export default function ProjectRagLibraryInline({
  projectId,
  onResolveProjectId,
  autoLoad = true,
  subtitle = 'Файлы проекта хранятся в базе знаний проекта и доступны в чате через RAG',
  dense = false,
}: ProjectRagLibraryInlineProps) {
  const [documents, setDocuments] = useState<ProjectRagDoc[]>([]);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [pendingUploads, setPendingUploads] = useState<RagPendingUpload[]>([]);
  const [fileSearchQuery, setFileSearchQuery] = useState('');
  const [banner, setBanner] = useState<{
    message: string;
    severity: 'success' | 'error' | 'info' | 'warning';
  } | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [resolvedId, setResolvedId] = useState<string | null>(projectId);
  const [libraryKbId, setLibraryKbId] = useState<number | null>(null);

  useEffect(() => {
    setResolvedId(projectId);
    setFileSearchQuery('');
  }, [projectId]);

  const effectiveId = resolvedId ?? projectId;

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

  const { readyMessage: ragReadyMessage, clearReadyMessage: clearRagReadyMessage } =
    useRagEntityReadyMessage('project', effectiveId);

  const showModelGuard = useCallback((message: string) => {
    setBanner({ message, severity: 'warning' });
  }, []);

  const ensureLibraryKbId = useCallback(async (pid: string): Promise<number | null> => {
    if (libraryKbId != null) return libraryKbId;
    const url = getApiUrl(
      `${API_ENDPOINTS.KNOWLEDGE_BASES}/ensure-project-library?project_id=${encodeURIComponent(pid)}`,
    );
    const resp = await fetch(url, {
      method: 'POST',
      headers: getAuthFetchHeaders(),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(formatRagApiDetail(err.detail, `HTTP ${resp.status}`));
    }
    const data = (await resp.json()) as { id?: number };
    const id = typeof data.id === 'number' ? data.id : null;
    if (id != null) setLibraryKbId(id);
    return id;
  }, [libraryKbId]);

  /** Модели из настроек БЗ проекта (entity scope=kb). */
  const resolveProjectRagModelPaths = useCallback(
    async (
      kbId: number,
    ): Promise<{
      embeddingPath: string;
      rerankerPath: string;
      rerankingEnabled: boolean;
    }> => {
      try {
        const resp = await fetch(
          getApiUrl(`/api/rag/settings?scope=kb&knowledge_base_id=${encodeURIComponent(String(kbId))}`),
          { headers: getAuthFetchHeaders() },
        );
        if (!resp.ok) {
          const envDefaults = await fetchRagEntityDefaults('kb');
          return {
            embeddingPath: envDefaults.embeddingPath,
            rerankerPath: envDefaults.rerankerPath,
            rerankingEnabled: true,
          };
        }
        const data = (await resp.json()) as Record<string, unknown>;
        const envDefaults = await fetchRagEntityDefaults('kb');
        return {
          embeddingPath: resolveRagEmbeddingModelPath(
            String(data.rag_embedding_model_path || ''),
            envDefaults.embeddingPath,
          ),
          rerankerPath: resolveRagRerankerModelPath(
            String(data.rag_reranker_model_path || ''),
            envDefaults.rerankerPath,
          ),
          rerankingEnabled:
            typeof data.rag_reranking_enabled === 'boolean'
              ? data.rag_reranking_enabled
              : true,
        };
      } catch {
        const envDefaults = await fetchRagEntityDefaults('kb');
        return {
          embeddingPath: envDefaults.embeddingPath,
          rerankerPath: envDefaults.rerankerPath,
          rerankingEnabled: true,
        };
      }
    },
    [],
  );

  const fetchList = useCallback(async (pid: string): Promise<ProjectRagDoc[]> => {
    const kbId = await ensureLibraryKbId(pid);
    if (kbId == null) return [];
    const url = getApiUrl(API_ENDPOINTS.KB_DOCUMENTS_LIST);
    const resp = await fetch(url, { headers: getAuthFetchHeaders() });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    const rows: unknown[] = Array.isArray(data) ? data : data.documents || [];
    return rows
      .map((row) => {
        const r = row as Record<string, unknown>;
        const meta = (r.metadata && typeof r.metadata === 'object'
          ? (r.metadata as Record<string, unknown>)
          : {}) as Record<string, unknown>;
        const rowKb = meta.knowledge_base_id ?? r.knowledge_base_id;
        try {
          if (Number(rowKb) !== kbId) return null;
        } catch {
          return null;
        }
        return {
          id: Number(r.id),
          filename: String(r.filename || ''),
          created_at: (r.created_at as string | null) ?? null,
          size: (r.size as number | null) ?? null,
          file_type: (r.file_type as string | null) ?? null,
        } as ProjectRagDoc;
      })
      .filter((d): d is ProjectRagDoc => d != null && Number.isFinite(d.id));
  }, [ensureLibraryKbId]);

  const loadDocuments = useCallback(async () => {
    if (!effectiveId) {
      setDocuments([]);
      return;
    }
    setLoading(true);
    setBanner(null);
    try {
      setDocuments(await fetchList(effectiveId));
    } catch (e) {
      setBanner({
        message: `Не удалось загрузить список файлов: ${e instanceof Error ? e.message : String(e)}`,
        severity: 'error',
      });
    } finally {
      setLoading(false);
    }
  }, [effectiveId, fetchList]);

  useEffect(() => {
    if (!autoLoad) return;
    if (!effectiveId) {
      setDocuments([]);
      return;
    }
    let cancelled = false;
    setLoading(true);
    fetchList(effectiveId)
      .then((docs) => {
        if (!cancelled) setDocuments(docs);
      })
      .catch(() => {
        if (!cancelled) setDocuments([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [effectiveId, autoLoad, fetchList]);

  const ensureProjectId = async (): Promise<string | null> => {
    if (effectiveId) return effectiveId;
    if (!onResolveProjectId) {
      setBanner({
        message: 'Загрузка файлов для этого экрана недоступна (не передан обработчик создания проекта).',
        severity: 'error',
      });
      return null;
    }
    try {
      const id = await Promise.resolve(onResolveProjectId());
      setResolvedId(id);
      return id;
    } catch (e) {
      setBanner({
        message: `Не удалось подготовить проект: ${e instanceof Error ? e.message : String(e)}`,
        severity: 'error',
      });
      return null;
    }
  };

  const uploadFiles = async (files: FileList | File[]) => {
    // Снимок ДО любых await: FileList живой и обнуляется с value input.
    const list = Array.from(files || []);
    if (!list.length) return;

    const valid = list.filter((f) => {
      const ext = '.' + f.name.split('.').pop()?.toLowerCase();
      return ALLOWED.includes(ext);
    });
    if (!valid.length) {
      setBanner({
        message: 'Допустимы: PDF, DOC/DOCX/DOCM, XLS/XLSX/XLSM, PPT/PPTX, TXT, MD, LOG, CSV, RTF, JPG, JPEG, PNG, WEBP, BMP (OCR)',
        severity: 'error',
      });
      return;
    }

    const pendingEntries = createRagPendingUploads(valid);
    const dropPending = (ids: string[]) => {
      setPendingUploads((prev) => removeRagPendingUploads(prev, ids));
    };

    const pid = await ensureProjectId();
    if (!pid) {
      dropPending(pendingEntries.map((entry) => entry.clientId));
      return;
    }

    let kbId: number | null = null;
    try {
      kbId = await ensureLibraryKbId(pid);
    } catch (e) {
      dropPending(pendingEntries.map((entry) => entry.clientId));
      setBanner({
        message: `Не удалось подготовить БЗ проекта: ${e instanceof Error ? e.message : String(e)}`,
        severity: 'error',
      });
      return;
    }
    if (kbId == null) {
      dropPending(pendingEntries.map((entry) => entry.clientId));
      setBanner({ message: 'Не удалось подготовить БЗ проекта', severity: 'error' });
      return;
    }

    const { embeddingPath, rerankerPath, rerankingEnabled } =
      await resolveProjectRagModelPaths(kbId);
    if (!embeddingPath) {
      dropPending(pendingEntries.map((entry) => entry.clientId));
      showModelGuard(
        'Сначала выберите модель эмбеддингов в настройках РАГ базы знаний проекта',
      );
      return;
    }
    if (rerankingEnabled && !rerankerPath) {
      dropPending(pendingEntries.map((entry) => entry.clientId));
      showModelGuard(
        'Сначала выберите модель реранкера в настройках РАГ базы знаний проекта',
      );
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
        const url = getApiUrl(API_ENDPOINTS.KB_DOCUMENTS_UPLOAD);
        const resp = await fetch(url, { method: 'POST', headers: getAuthFetchHeaders(), body: fd });
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
          const msg = formatRagApiDetail(err.detail, resp.statusText);
          // Сообщения про модели — оранжевая табличка, остальное error.
          if (
            typeof msg === 'string' &&
            (msg.includes('эмбеддинг') || msg.includes('реранкер'))
          ) {
            showModelGuard(msg);
          } else {
            setBanner({ message: `${file.name}: ${msg}`, severity: 'error' });
          }
          stopRemaining = true;
          if (pendingId) {
            commitRagUploadUiUpdate(() => {
              setPendingUploads((prev) => removeRagPendingUploads(prev, [pendingId]));
            });
          }
          return;
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
            const fresh = await fetchList(pid);
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
        setDocuments(await fetchList(pid));
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
        message: `Загружено файлов: ${successCount}. Документы проиндексированы для RAG.`,
        severity: 'success',
      });
    } else if (lastDuplicateMsg) {
      setBanner({ message: lastDuplicateMsg, severity: 'warning' });
    }
    void fetchList(pid)
      .then((fresh) => {
        setDocuments((prev) => mergeRagDocumentsById(prev, fresh));
      })
      .catch(() => undefined);
  };

  const handleDelete = async (doc: ProjectRagDoc) => {
    if (!effectiveId) return;
    try {
      const url = getApiUrl(`${API_ENDPOINTS.KB_DOCUMENTS_DELETE}/${doc.id}`);
      const resp = await fetch(url, {
        method: 'DELETE',
        headers: getAuthFetchHeaders(),
      });
      if (!resp.ok) {
        // Текст из detail объясняет причину («хранилище недоступно»),
        // голый код ответа пользователю ничего не говорит.
        const detail = await resp.json().then(d => d?.detail).catch(() => null);
        throw new Error(detail || `HTTP ${resp.status}`);
      }
      setBanner({ message: `«${doc.filename}» удалён`, severity: 'success' });
      setDocuments(await fetchList(effectiveId));
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setBanner({ message: `Ошибка удаления: ${msg}`, severity: 'error' });
    }
  };

  const dropZone = (
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
        p: dense ? 2 : 3,
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
        accept=".pdf,.doc,.docx,.docm,.xls,.xlsx,.xlsm,.ppt,.pptx,.txt,.csv,.md,.log,.rtf,.jpg,.jpeg,.png,.webp,.bmp"
        onChange={(e) => {
          const list = e.target.files;
          if (!list?.length) return;
          const snapshot = Array.from(list);
          e.target.value = '';
          void uploadFiles(snapshot);
        }}
      />
      <UploadIcon sx={{ fontSize: dense ? 32 : 40, color: 'text.secondary', mb: 1 }} />
      <Typography variant="body2" gutterBottom>
        Перетащите файлы сюда или нажмите кнопку — загрузка сразу на сервер
      </Typography>
      <Button
        variant="contained"
        size={dense ? 'small' : 'medium'}
        startIcon={<UploadIcon />}
        disabled={uploading}
        onClick={() => fileInputRef.current?.click()}
      >
        Выбрать файлы
      </Button>
    </Box>
  );

  return (
    <Box>
      <Typography variant="subtitle2" fontWeight={600} gutterBottom>
        Файлы проекта (RAG)
      </Typography>
      <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 1 }}>
        {subtitle}
      </Typography>

      {banner && (
        <Alert
          severity={banner.severity}
          onClose={() => setBanner(null)}
          sx={banner.severity === 'warning' ? ragWarningAlertSx : { mb: 2 }}
        >
          {banner.message}
        </Alert>
      )}

      {ragReadyMessage && (
        <Alert
          severity="success"
          onClose={clearRagReadyMessage}
          sx={{
            mb: 2,
            bgcolor: 'rgba(76, 175, 80, 0.12)',
            color: 'success.main',
            border: '1px solid',
            borderColor: 'success.main',
            '& .MuiAlert-icon': { color: 'success.main' },
          }}
        >
          {ragReadyMessage}
        </Alert>
      )}

      {dropZone}

      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
        <Typography variant="subtitle2">
          Проиндексированные документы (
          {hasActiveFileSearch ? `${filteredTotal} из ${totalFiles}` : totalFiles})
        </Typography>
        <Button
          size="small"
          startIcon={<RefreshIcon />}
          onClick={() => loadDocuments()}
          disabled={loading || !effectiveId}
        >
          Обновить
        </Button>
      </Box>

      {totalFiles > 0 && (
        <RagFilesSearchField
          value={fileSearchQuery}
          onChange={setFileSearchQuery}
          totalCount={totalFiles}
          disabled={loading}
        />
      )}

      {!effectiveId ? (
        <Typography color="text.secondary" variant="body2">
          После выбора файлов будет создан черновик проекта (если его ещё нет), и документы сразу попадут в хранилище.
        </Typography>
      ) : loading ? (
        <LinearProgress />
      ) : documents.length === 0 && pendingUploads.length === 0 ? (
        <Typography color="text.secondary" variant="body2">
          Пока нет документов. Загрузите файлы выше — они станут доступны для поиска в рамках этого проекта.
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
    </Box>
  );
}
