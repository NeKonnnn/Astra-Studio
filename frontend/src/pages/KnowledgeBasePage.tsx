import React, { useState, useRef, useEffect, useCallback } from 'react';
import {
  Box,
  Paper,
  Typography,
  Button,
  IconButton,
  Chip,
  Alert,
  Snackbar,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  List,
  ListItem,
  ListItemIcon,
  ListItemText,
  LinearProgress,
  Tooltip,
  Divider,
  CircularProgress,
  alpha,
} from '@mui/material';
import {
  CloudUpload as UploadIcon,
  Description as DocumentIcon,
  PictureAsPdf as PdfIcon,
  TableChart as ExcelIcon,
  TextSnippet as TxtIcon,
  Delete as DeleteIcon,
  AutoStories as KbIcon,
  CheckCircle as CheckIcon,
  ErrorOutline as ErrorIcon,
  Refresh as RefreshIcon,
  Article as ArticleIcon,
  Image as ImageIcon,
} from '@mui/icons-material';
import { getApiUrl, API_ENDPOINTS, getAuthFetchHeaders } from '../config/api';
import { useAppActions } from '../contexts/AppContext';
import {
  formatRagApiDetail,
  parseRagDuplicateUploadMessage,
} from '../utils/ragPendingUpload';

interface KbDocument {
  id: number;
  filename: string;
  created_at: string | null;
  size: number | null;
  file_type: string | null;
}

interface KnowledgeBasePageProps {
  isDarkMode: boolean;
}

function formatFileSize(bytes: number | null): string {
  if (!bytes) return '—';
  if (bytes < 1024) return `${bytes} Б`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} КБ`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`;
}

function formatDate(iso: string | null): string {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString('ru-RU', {
      day: '2-digit',
      month: '2-digit',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return iso;
  }
}

function getFileIcon(filename: string, fileType: string | null) {
  const ext = filename.split('.').pop()?.toLowerCase() || '';
  if (ext === 'pdf') return <PdfIcon sx={{ color: '#e53935' }} />;
  if (['xlsx', 'xls', 'csv'].includes(ext)) return <ExcelIcon sx={{ color: '#43a047' }} />;
  if (ext === 'txt') return <TxtIcon sx={{ color: '#1e88e5' }} />;
  if (['docx', 'doc'].includes(ext)) return <ArticleIcon sx={{ color: '#1565c0' }} />;
  if(['jpg', 'jpeg', 'png', 'webp','bmp'].includes(ext)) return <ImageIcon sx={{ color: '#8e24aa' }} />;
  return <DocumentIcon sx={{ color: '#7b1fa2' }} />;
}

function getFileTypeChip(filename: string) {
  const ext = filename.split('.').pop()?.toLowerCase() || '';
  const map: Record<string, { label: string; color: 'error' | 'success' | 'primary' | 'secondary' | 'default' }> = {
    pdf: { label: 'PDF', color: 'error' },
    xlsx: { label: 'Excel', color: 'success' },
    xls: { label: 'Excel', color: 'success' },
    docx: { label: 'Word', color: 'primary' },
    doc: { label: 'Word', color: 'primary' },
    txt: { label: 'TXT', color: 'default' },
    csv: { label: 'CSV', color: 'secondary' },
    jpg: { label: 'Image (OCR)', color: 'secondary' },
    jpeg: { label: 'Image (OCR)', color: 'secondary' },
    png: { label: 'Image (OCR)', color: 'secondary' },
    webp: { label: 'Image (OCR)', color: 'secondary' },
    bmp: { label: 'Image (OCR)', color: 'secondary' },
  };
  const info = map[ext] || { label: ext.toUpperCase(), color: 'default' as const };
  return <Chip size="small" label={info.label} color={info.color} sx={{ fontSize: '0.7rem', height: 20 }} />;
}

export default function KnowledgeBasePage({ isDarkMode }: KnowledgeBasePageProps) {
  const [documents, setDocuments] = useState<KbDocument[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [docToDelete, setDocToDelete] = useState<KbDocument | null>(null);
  const [snackbar, setSnackbar] = useState<{ open: boolean; message: string; severity: 'success' | 'error' | 'info' | 'warning' }>({
    open: false, message: '', severity: 'info',
  });
  const [uploadProgress, setUploadProgress] = useState<string[]>([]);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const { showNotification } = useAppActions();

  const loadDocuments = useCallback(async () => {
    setIsLoading(true);
    try {
      const url = getApiUrl(API_ENDPOINTS.KB_DOCUMENTS_LIST);
      const resp = await fetch(url);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      setDocuments(data.documents || data || []);
    } catch (e) {
      showNotification('error', `Ошибка загрузки Базы Знаний: ${e}`);
    } finally {
      setIsLoading(false);
    }
  }, [showNotification]);

  useEffect(() => {
    loadDocuments();
  }, [loadDocuments]);

  const handleUploadFiles = async (files: FileList) => {
    if (!files.length) return;
    const allowed = ['.pdf', '.docx', '.doc', '.xlsx', '.xls', '.ppt', '.pptx', '.txt', '.csv', '.jpg', '.jpeg', '.png', '.webp', '.bmp'];
    const validFiles = Array.from(files).filter(f => {
      const ext = '.' + f.name.split('.').pop()?.toLowerCase();
      return allowed.includes(ext);
    });

    if (!validFiles.length) {
      setSnackbar({ open: true, message: 'Неподдерживаемый формат. Разрешены: PDF, DOC/DOCX, XLS/XLSX, PPT/PPTX, TXT, CSV, JPG, JPEG, PNG, WEBP, BMP (OCR)', severity: 'error' });
      return;
    }

    setIsUploading(true);
    setUploadProgress([]);
    const errors: string[] = [];
    const warnings: string[] = [];
    let successCount = 0;

    for (const file of validFiles) {
      setUploadProgress(prev => [...prev, `Загружаю: ${file.name}...`]);
      try {
        const formData = new FormData();
        formData.append('file', file);
        const url = getApiUrl(API_ENDPOINTS.KB_DOCUMENTS_UPLOAD);
        const resp = await fetch(url, {
          method: 'POST',
          headers: getAuthFetchHeaders(),
          body: formData,
        });
        if (!resp.ok) {
          const err = await resp.json().catch(() => ({ detail: resp.statusText }));
          const duplicateMsg = parseRagDuplicateUploadMessage(resp.status, err, file.name);
          if (duplicateMsg) {
            warnings.push(duplicateMsg);
            setUploadProgress(prev =>
              prev.map(p => p === `Загружаю: ${file.name}...` ? `⚠ ${file.name}: уже в базе` : p)
            );
            continue;
          }
          throw new Error(formatRagApiDetail(err.detail, resp.statusText));
        }
        successCount += 1;
        setUploadProgress(prev =>
          prev.map(p => p === `Загружаю: ${file.name}...` ? `✓ ${file.name}` : p)
        );
      } catch (e: any) {
        errors.push(`${file.name}: ${e.message}`);
        setUploadProgress(prev =>
          prev.map(p => p === `Загружаю: ${file.name}...` ? `✗ ${file.name}: ${e.message}` : p)
        );
      }
    }

    setIsUploading(false);
    if (errors.length) {
      setSnackbar({ open: true, message: `Ошибки: ${errors.join('; ')}`, severity: 'error' });
    } else if (warnings.length && successCount === 0) {
      setSnackbar({ open: true, message: warnings[0], severity: 'warning' });
    } else if (warnings.length) {
      setSnackbar({
        open: true,
        message: `Загружено: ${successCount}. Пропущено дубликатов: ${warnings.length}`,
        severity: 'warning',
      });
    } else {
      setSnackbar({ open: true, message: `Успешно загружено ${successCount} документ(ов)`, severity: 'success' });
    }
    await loadDocuments();
    setTimeout(() => setUploadProgress([]), 3000);
  };

  const handleDelete = async () => {
    if (!docToDelete) return;
    try {
      const url = `${getApiUrl(API_ENDPOINTS.KB_DOCUMENTS_DELETE)}/${docToDelete.id}`;
      const resp = await fetch(url, { method: 'DELETE' });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      setSnackbar({ open: true, message: `Документ «${docToDelete.filename}» удалён из Базы Знаний`, severity: 'success' });
      setDeleteDialogOpen(false);
      setDocToDelete(null);
      await loadDocuments();
    } catch (e) {
      setSnackbar({ open: true, message: `Ошибка удаления: ${e}`, severity: 'error' });
    }
  };

  const handleDragOver = (e: React.DragEvent) => { e.preventDefault(); setIsDragging(true); };
  const handleDragLeave = () => setIsDragging(false);
  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    if (e.dataTransfer.files.length) handleUploadFiles(e.dataTransfer.files);
  };

  const bgColor = isDarkMode ? 'rgba(255,255,255,0.03)' : 'rgba(0,0,0,0.02)';
  const borderColor = isDarkMode ? 'rgba(255,255,255,0.1)' : 'rgba(0,0,0,0.1)';

  return (
    <Box sx={{ height: '100%', overflow: 'auto', p: { xs: 2, sm: 3 }, bgcolor: bgColor }}>
      <Box sx={{ maxWidth: 900, mx: 'auto' }}>

        {/* Заголовок */}
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 3 }}>
          <KbIcon sx={{ fontSize: 32, color: 'primary.main' }} />
          <Box>
            <Typography variant="h5" fontWeight={700}>
              База Знаний
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Постоянное хранилище документов для RAG-поиска
            </Typography>
          </Box>
          <Box sx={{ ml: 'auto' }}>
            <Tooltip title="Обновить список">
              <IconButton onClick={loadDocuments} disabled={isLoading} size="small">
                <RefreshIcon />
              </IconButton>
            </Tooltip>
          </Box>
        </Box>

        {/* Зона загрузки */}
        <Paper
          elevation={0}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          onClick={() => !isUploading && fileInputRef.current?.click()}
          sx={{
            border: `2px dashed ${isDragging ? '#2196f3' : borderColor}`,
            borderRadius: 3,
            p: 4,
            mb: 3,
            textAlign: 'center',
            cursor: isUploading ? 'default' : 'pointer',
            bgcolor: isDragging
              ? alpha('#2196f3', 0.06)
              : isDarkMode ? 'rgba(255,255,255,0.03)' : 'rgba(0,0,0,0.02)',
            transition: 'all 0.2s',
            '&:hover': isUploading ? {} : {
              borderColor: 'primary.main',
              bgcolor: alpha('#2196f3', 0.04),
            },
          }}
        >
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept=".pdf,.docx,.doc,.xlsx,.xls,.ppt,.pptx,.txt,.csv,.jpg,.jpeg,.png,.webp,.bmp"
            style={{ display: 'none' }}
            onChange={e => { if (e.target.files) handleUploadFiles(e.target.files); e.target.value = ''; }}
          />
          {isUploading ? (
            <Box>
              <CircularProgress size={36} sx={{ mb: 1 }} />
              <Typography variant="body1" fontWeight={500} gutterBottom>
                Индексирую документы...
              </Typography>
              <List dense sx={{ maxWidth: 400, mx: 'auto', mt: 1 }}>
                {uploadProgress.map((msg, i) => (
                  <ListItem key={i} sx={{ py: 0.25 }}>
                    <ListItemIcon sx={{ minWidth: 28 }}>
                      {msg.startsWith('✓') ? (
                        <CheckIcon sx={{ fontSize: 16, color: 'success.main' }} />
                      ) : msg.startsWith('✗') ? (
                        <ErrorIcon sx={{ fontSize: 16, color: 'error.main' }} />
                      ) : (
                        <CircularProgress size={14} />
                      )}
                    </ListItemIcon>
                    <ListItemText
                      primary={msg}
                      primaryTypographyProps={{ variant: 'caption', noWrap: true }}
                    />
                  </ListItem>
                ))}
              </List>
            </Box>
          ) : (
            <Box>
              <UploadIcon sx={{ fontSize: 48, color: isDragging ? 'primary.main' : 'text.disabled', mb: 1 }} />
              <Typography variant="body1" fontWeight={500} gutterBottom>
                {isDragging ? 'Отпустите файлы для загрузки' : 'Перетащите файлы или нажмите для выбора'}
              </Typography>
              <Typography variant="caption" color="text.secondary">
                Поддерживаются: PDF, DOC/DOCX, XLS/XLSX, PPT/PPTX, TXT, CSV, JPG, JPEG, PNG, WEBP, BMP
              </Typography>
            </Box>
          )}
        </Paper>

        {/* Прогресс-бар */}
        {isUploading && <LinearProgress sx={{ mb: 2, borderRadius: 1 }} />}

        {/* Список документов */}
        <Paper
          elevation={0}
          sx={{
            border: `1px solid ${borderColor}`,
            borderRadius: 3,
            overflow: 'hidden',
            bgcolor: isDarkMode ? 'rgba(255,255,255,0.03)' : 'white',
          }}
        >
          <Box sx={{ px: 2.5, py: 2, display: 'flex', alignItems: 'center', gap: 1, borderBottom: `1px solid ${borderColor}` }}>
            <KbIcon sx={{ fontSize: 18, color: 'primary.main' }} />
            <Typography variant="subtitle1" fontWeight={600}>
              Документы в Базе Знаний
            </Typography>
            <Chip
              size="small"
              label={documents.length}
              color={documents.length > 0 ? 'primary' : 'default'}
              sx={{ ml: 'auto', fontWeight: 600 }}
            />
          </Box>

          {isLoading ? (
            <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
              <CircularProgress size={28} />
            </Box>
          ) : documents.length === 0 ? (
            <Box sx={{ textAlign: 'center', py: 6, px: 3 }}>
              <KbIcon sx={{ fontSize: 48, color: 'text.disabled', mb: 1.5 }} />
              <Typography variant="body1" color="text.secondary" gutterBottom>
                База Знаний пуста
              </Typography>
              <Typography variant="caption" color="text.disabled">
                Загрузите документы выше — они будут постоянно доступны для RAG-поиска
              </Typography>
            </Box>
          ) : (
            <List disablePadding>
              {documents.map((doc, idx) => (
                <React.Fragment key={doc.id}>
                  {idx > 0 && <Divider />}
                  <ListItem
                    sx={{
                      px: 2.5,
                      py: 1.5,
                      '&:hover': { bgcolor: isDarkMode ? 'rgba(255,255,255,0.04)' : 'rgba(0,0,0,0.03)' },
                    }}
                    secondaryAction={
                      <Tooltip title="Удалить из Базы Знаний">
                        <IconButton
                          size="small"
                          edge="end"
                          onClick={() => { setDocToDelete(doc); setDeleteDialogOpen(true); }}
                          sx={{ color: 'text.secondary', '&:hover': { color: 'error.main' } }}
                        >
                          <DeleteIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    }
                  >
                    <ListItemIcon sx={{ minWidth: 40 }}>
                      {getFileIcon(doc.filename, doc.file_type)}
                    </ListItemIcon>
                    <ListItemText
                      primary={
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
                          <Typography
                            variant="body2"
                            fontWeight={500}
                            sx={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: { xs: 160, sm: 300, md: 440 } }}
                            title={doc.filename}
                          >
                            {doc.filename}
                          </Typography>
                          {getFileTypeChip(doc.filename)}
                        </Box>
                      }
                      secondary={
                        <Typography variant="caption" color="text.secondary">
                          {formatDate(doc.created_at)} · {formatFileSize(doc.size)}
                        </Typography>
                      }
                    />
                  </ListItem>
                </React.Fragment>
              ))}
            </List>
          )}
        </Paper>

        {/* Подсказка */}
        {documents.length > 0 && (
          <Alert
            severity="info"
            sx={{ mt: 2, borderRadius: 2 }}
            icon={<KbIcon />}
          >
            <Typography variant="body2">
              Включите <b>«База Знаний»</b> в строке ввода чата — и ответы будут дополняться
              информацией из этих документов при каждом запросе.
            </Typography>
          </Alert>
        )}
      </Box>

      {/* Диалог подтверждения удаления */}
      <Dialog open={deleteDialogOpen} onClose={() => setDeleteDialogOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <DeleteIcon color="error" />
          Удалить из Базы Знаний?
        </DialogTitle>
        <DialogContent>
          <Typography variant="body2" color="text.secondary">
            Документ <b>«{docToDelete?.filename}»</b> будет удалён из Базы Знаний
            вместе со всеми векторными индексами. Это действие необратимо.
          </Typography>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2.5 }}>
          <Button onClick={() => setDeleteDialogOpen(false)} variant="outlined">Отмена</Button>
          <Button onClick={handleDelete} variant="contained" color="error" startIcon={<DeleteIcon />}>
            Удалить
          </Button>
        </DialogActions>
      </Dialog>

      {/* Уведомления */}
      <Snackbar
        open={snackbar.open}
        autoHideDuration={4000}
        onClose={() => setSnackbar(s => ({ ...s, open: false }))}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      >
        <Alert
          onClose={() => setSnackbar(s => ({ ...s, open: false }))}
          severity={snackbar.severity}
          sx={{ borderRadius: 2 }}
        >
          {snackbar.message}
        </Alert>
      </Snackbar>
    </Box>
  );
}
