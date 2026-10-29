/**
 * Панель «Базы знаний» на правой боковой панели.
 * — выбор / создание БЗ
 * — загрузка файлов и каталога
 * — настройки РАГ
 * — публикация в галерею (как Skills)
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Box,
  Button,
  CircularProgress,
  IconButton,
  Popover,
  TextField,
  Tooltip,
  Typography,
  useTheme,
} from '@mui/material';
import {
  ArrowBack as ArrowBackIcon,
  Article as DocxIcon,
  Close as CloseIcon,
  Delete as DeleteIcon,
  Description as FileIcon,
  ExpandMore as ExpandMoreIcon,
  Folder as FolderIcon,
  FolderOpen as FolderOpenIcon,
  Image as ImageIcon,
  PictureAsPdf as PdfIcon,
  Public as PublicIcon,
  PublicOff as PublicOffIcon,
  Save as SaveIcon,
  Share as ShareIcon,
  Search as SearchIcon,
  Settings as SettingsIcon,
  TableChart as ExcelIcon,
  TextSnippet as TxtIcon,
  UploadFile as UploadFileIcon,
} from '@mui/icons-material';
import ShareKnowledgeBaseDialog from '../ShareKnowledgeBaseDialog';
import RagFilesSearchField from '../RagFilesSearchField';
import RagUploadingFileThumb from '../RagUploadingFileThumb';
import type { SxProps, Theme } from '@mui/material/styles';
import { useAuth } from '../../contexts/AuthContext';
import { useAppActions } from '../../contexts/AppContext';
import { useFeatureAccess } from '../../hooks/useFeatureAccess';
import { authFetch, getApiUrl, API_ENDPOINTS } from '../../config/api';
import {
  ASTRA_OPEN_KNOWLEDGE_BASES_SIDEBAR,
  ASTRA_OPEN_KNOWLEDGE_BASES_SIDEBAR_ID_KEY,
} from '../../constants/hotkeys';
import {
  getDropdownChevronSx,
  getDropdownItemSx,
  getDropdownItemStateSx,
  getDropdownPopoverPaperSx,
  getFormFieldInputSx,
  getFormFieldTriggerSx,
  getFormFieldTriggerValueSx,
  SIDEBAR_HIDE_SCROLLBAR_SX,
  AGENT_CONSTRUCTOR_FIELD_PADDING_X_PX,
  AGENT_CONSTRUCTOR_FIELD_PADDING_Y_PX,
  AGENT_CONSTRUCTOR_MULTILINE_HIDE_SCROLLBAR_SX,
} from '../../constants/menuStyles';
import {
  getSidebarPanelBackground,
  getSidebarPanelChrome,
} from '../../constants/sidebarPanelColor';
import RAGSettings from '../settings/RAGSettings';
import { saveEntityRagSettings, type EntityRagDraft } from '../../utils/entityRagSettings';
import {
  basenameOf,
  filterKbDocsByQuery,
  getDocRelativePath,
  joinPath,
  listKbFolderEntries,
  normalizeRelativePath,
} from '../../utils/kbFolderBrowser';
import { filterByRagFilenameQuery } from '../../utils/ragFilesSearch';
import {
  RAG_UPLOAD_CONCURRENCY,
  commitRagUploadUiUpdate,
  createRagPendingUploads,
  mapWithConcurrency,
  removeRagPendingUploads,
  type RagPendingUpload,
} from '../../utils/ragPendingUpload';

function getFileIconBg(filename: string): string {
  const ext = filename.split('.').pop()?.toLowerCase() || '';
  if (ext === 'pdf') return '#e53935';
  if (['docx', 'doc'].includes(ext)) return '#1976d2';
  if (['xlsx', 'xls', 'csv'].includes(ext)) return '#43a047';
  if (ext === 'txt') return '#607d8b';
  if (['jpg', 'jpeg', 'png', 'webp', 'bmp'].includes(ext)) return '#8e24aa';
  return '#5c6bc0';
}

function getFileTypeLabel(filename: string): string {
  const ext = filename.split('.').pop()?.toLowerCase() || '';
  if (ext === 'pdf') return 'PDF';
  if (['docx', 'doc'].includes(ext)) return 'Word';
  if (['xlsx', 'xls', 'csv'].includes(ext)) return 'Excel';
  if (ext === 'txt') return 'TXT';
  if (['jpg', 'jpeg', 'png', 'webp', 'bmp'].includes(ext)) return 'Image';
  return 'File';
}

const fileIconSx = { fontSize: 18, color: 'white' };

function getFileIcon(filename: string) {
  const ext = filename.split('.').pop()?.toLowerCase() || '';
  if (ext === 'pdf') return <PdfIcon sx={fileIconSx} />;
  if (['xlsx', 'xls', 'csv'].includes(ext)) return <ExcelIcon sx={fileIconSx} />;
  if (ext === 'txt') return <TxtIcon sx={fileIconSx} />;
  if (['docx', 'doc'].includes(ext)) return <DocxIcon sx={fileIconSx} />;
  if (['jpg', 'jpeg', 'png', 'webp', 'bmp'].includes(ext)) return <ImageIcon sx={fileIconSx} />;
  return <FileIcon sx={fileIconSx} />;
}

interface KbRow {
  id: number;
  name: string;
  description?: string | null;
  is_public?: boolean;
  document_count?: number;
  author_id: string;
  write_access?: boolean;
  my_permission?: 'owner' | 'editor' | 'viewer' | string | null;
}

interface KbDoc {
  id: number;
  filename: string;
  created_at?: string | null;
  size?: number | string | null;
  metadata?: {
    knowledge_base_id?: number;
    agent_id?: number;
    relative_path?: string;
  };
}

const emptyForm = {
  name: '',
  description: '',
  is_public: false,
};

interface KnowledgeBasesSidebarPanelProps {
  isOpen?: boolean;
}

export default function KnowledgeBasesSidebarPanel({
  isOpen = true,
}: KnowledgeBasesSidebarPanelProps) {
  const theme = useTheme();
  const isDarkMode = theme.palette.mode === 'dark';
  const { token, user } = useAuth();
  const { canCreateAgents, canPublishToLibrary } = useFeatureAccess();
  const { showNotification } = useAppActions();

  const [sidebarColorTick, setSidebarColorTick] = useState(0);
  const panelBg = useMemo(
    () => getSidebarPanelBackground(isDarkMode),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [isDarkMode, sidebarColorTick],
  );
  const panelChrome = useMemo(() => getSidebarPanelChrome(panelBg), [panelBg]);
  useEffect(() => {
    const onColorChanged = () => setSidebarColorTick((n) => n + 1);
    window.addEventListener('sidebarColorChanged', onColorChanged);
    return () => window.removeEventListener('sidebarColorChanged', onColorChanged);
  }, []);

  const darkFields = !panelChrome.isLight;
  const dropdownItemSx = useMemo(() => getDropdownItemSx(darkFields), [darkFields]);
  const dropdownChevronSx = useMemo(() => getDropdownChevronSx(darkFields), [darkFields]);
  const formFieldTriggerSx = useMemo(() => getFormFieldTriggerSx(darkFields), [darkFields]);
  const formFieldTriggerValueSx = useMemo(
    () => getFormFieldTriggerValueSx(darkFields),
    [darkFields],
  );
  const formFieldInputSx = useMemo(() => getFormFieldInputSx(darkFields), [darkFields]);
  const nameFieldSx = useMemo(
    () =>
      [formFieldInputSx, { '& .MuiFormLabel-asterisk': { color: '#f44336' } }] as SxProps<Theme>,
    [formFieldInputSx],
  );
  const multilineFieldSx = useMemo(
    () =>
      [
        formFieldInputSx,
        {
          '& .MuiOutlinedInput-root.MuiInputBase-multiline': { padding: 0 },
          '& .MuiOutlinedInput-root.MuiInputBase-multiline .MuiOutlinedInput-input': {
            padding: `${AGENT_CONSTRUCTOR_FIELD_PADDING_Y_PX}px ${AGENT_CONSTRUCTOR_FIELD_PADDING_X_PX}px !important`,
            ...AGENT_CONSTRUCTOR_MULTILINE_HIDE_SCROLLBAR_SX,
          },
        },
      ] as SxProps<Theme>,
    [formFieldInputSx],
  );
  const footerActionBtnSx = useMemo(
    () => ({
      textTransform: 'none' as const,
      fontSize: '0.72rem',
      fontWeight: 600,
      py: 0.55,
      px: 1.25,
      whiteSpace: 'nowrap' as const,
      minHeight: 30,
      flex: '1 1 140px',
      justifyContent: 'flex-start',
      textAlign: 'left' as const,
      '& .MuiButton-startIcon': { marginRight: '8px', marginLeft: 0, color: 'inherit' },
      '& .MuiButton-startIcon .MuiSvgIcon-root': { fontSize: '0.9rem' },
    }),
    [],
  );
  const footerNeutralActionBtnSx = useMemo(
    () => ({
      ...footerActionBtnSx,
      color: panelChrome.fgMuted,
      border: panelChrome.buttonBorder,
      bgcolor: 'transparent',
      '&:hover': { bgcolor: panelChrome.hoverBg, color: panelChrome.fgMuted },
    }),
    [footerActionBtnSx, panelChrome],
  );
  const footerDeleteActionBtnSx = useMemo(
    () => ({
      ...footerActionBtnSx,
      color: '#ef5350',
      border: '1px solid rgba(239,83,80,0.45)',
      bgcolor: 'rgba(239,83,80,0.08)',
      '&:hover': {
        bgcolor: 'rgba(239,83,80,0.16)',
        color: '#ef5350',
        borderColor: 'rgba(239,83,80,0.55)',
      },
    }),
    [footerActionBtnSx],
  );

  const fileInputRef = useRef<HTMLInputElement>(null);
  const dirInputRef = useRef<HTMLInputElement>(null);

  const [items, setItems] = useState<KbRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [popoverAnchor, setPopoverAnchor] = useState<HTMLElement | null>(null);
  const [selectedId, setSelectedId] = useState<number | 'new'>('new');
  const [form, setForm] = useState(emptyForm);
  const [detailMeta, setDetailMeta] = useState<Pick<
    KbRow,
    'author_id' | 'write_access' | 'my_permission' | 'is_public'
  > | null>(null);
  const [docs, setDocs] = useState<KbDoc[]>([]);
  const [currentFolderPath, setCurrentFolderPath] = useState('');
  const [fileSearchQuery, setFileSearchQuery] = useState('');
  const [isSaving, setIsSaving] = useState(false);
  const [isPublishing, setIsPublishing] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [pendingUploads, setPendingUploads] = useState<RagPendingUpload[]>([]);
  const [shareDialogOpen, setShareDialogOpen] = useState(false);
  const [showRagSettings, setShowRagSettings] = useState(false);
  const [ragDraft, setRagDraft] = useState<EntityRagDraft | null>(null);

  const fileCardSx = useMemo(
    () => ({
      position: 'relative' as const,
      borderRadius: 1,
      bgcolor: darkFields ? '#2a2d3a' : 'rgba(0,0,0,0.04)',
      border: darkFields ? '1px solid rgba(255,255,255,0.08)' : '1px solid rgba(0,0,0,0.12)',
      p: 0.5,
      minWidth: 0,
      display: 'flex',
      flexDirection: 'column' as const,
      justifyContent: 'center',
    }),
    [darkFields],
  );
  const fileNameColor = darkFields ? '#fff' : 'rgba(0,0,0,0.87)';
  const fileTypeColor = darkFields ? 'rgba(255,255,255,0.45)' : 'rgba(0,0,0,0.55)';
  const fileMutedColor = darkFields ? 'rgba(255,255,255,0.45)' : 'rgba(0,0,0,0.45)';
  const folderRowHover = darkFields ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.04)';

  const hasActiveFileSearch = fileSearchQuery.trim().length > 0;
  const folderListing = useMemo(
    () => listKbFolderEntries(docs, currentFolderPath),
    [docs, currentFolderPath],
  );
  const searchMatchedDocs = useMemo(
    () => filterKbDocsByQuery(docs, fileSearchQuery),
    [docs, fileSearchQuery],
  );
  const visibleFiles = hasActiveFileSearch ? searchMatchedDocs : folderListing.files;
  const visibleFolders = hasActiveFileSearch ? [] : folderListing.folders;
  const visiblePendingUploads = useMemo(
    () => filterByRagFilenameQuery(pendingUploads, fileSearchQuery),
    [pendingUploads, fileSearchQuery],
  );
  const totalFiles = docs.length + pendingUploads.length;
  const pathSegments = useMemo(() => {
    const n = normalizeRelativePath(currentFolderPath);
    return n ? n.split('/') : [];
  }, [currentFolderPath]);

  const authHeaders = useCallback((): HeadersInit => {
    const h: HeadersInit = { 'Content-Type': 'application/json' };
    if (token) h.Authorization = `Bearer ${token}`;
    return h;
  }, [token]);

  const isOwner =
    selectedId === 'new' ||
    (detailMeta?.author_id || '').toLowerCase() === (user?.user_id || '').toLowerCase() ||
    detailMeta?.my_permission === 'owner';
  const canWrite = selectedId === 'new' || Boolean(detailMeta?.write_access) || isOwner;
  const canPublish = isOwner && canPublishToLibrary;
  const readOnly = selectedId !== 'new' && !canWrite;

  const loadList = useCallback(async () => {
    setLoading(true);
    try {
      const headers: HeadersInit = token ? { Authorization: `Bearer ${token}` } : {};
      const pageSize = 200;
      const all: KbRow[] = [];
      let page = 1;
      let pages = 1;
      while (page <= pages) {
        const resp = await authFetch(
          `${getApiUrl(API_ENDPOINTS.KNOWLEDGE_BASES)}/list?limit=${pageSize}&page=${page}`,
          { headers },
        );
        if (!resp.ok) break;
        const data = await resp.json();
        const batch = (data.items || []) as KbRow[];
        all.push(...batch);
        pages = Math.max(1, Number(data.pages) || 1);
        if (batch.length === 0) break;
        page += 1;
      }
      setItems(all);
    } catch {
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, [token]);

  const loadDocs = useCallback(
    async (kbId: number) => {
      try {
        const headers: HeadersInit = token ? { Authorization: `Bearer ${token}` } : {};
        const resp = await authFetch(getApiUrl(API_ENDPOINTS.KB_DOCUMENTS_LIST), { headers });
        if (!resp.ok) {
          setDocs([]);
          return;
        }
        const data = await resp.json();
        const all = (data.documents || data || []) as KbDoc[];
        setDocs(
          all.filter((d) => Number(d?.metadata?.knowledge_base_id) === Number(kbId)),
        );
      } catch {
        setDocs([]);
      }
    },
    [token],
  );

  useEffect(() => {
    if (isOpen) void loadList();
  }, [isOpen, loadList]);

  useEffect(() => {
    if (typeof selectedId === 'number') {
      void loadDocs(selectedId);
    } else {
      setDocs([]);
    }
  }, [selectedId, loadDocs]);

  const filtered = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return items;
    return items.filter(
      (it) =>
        it.name.toLowerCase().includes(q) ||
        (it.description || '').toLowerCase().includes(q),
    );
  }, [items, searchQuery]);

  const selectedLabel = useMemo(() => {
    if (selectedId === 'new') return 'Новая база знаний';
    return items.find((x) => x.id === selectedId)?.name || `База #${selectedId}`;
  }, [items, selectedId]);

  const resetNew = () => {
    setSelectedId('new');
    setForm(emptyForm);
    setDetailMeta(null);
    setRagDraft(null);
    setShowRagSettings(false);
    setDocs([]);
    setCurrentFolderPath('');
    setFileSearchQuery('');
  };

  const openExisting = useCallback((row: KbRow) => {
    setSelectedId(row.id);
    setForm({
      name: row.name || '',
      description: row.description || '',
      is_public: Boolean(row.is_public),
    });
    setDetailMeta({
      author_id: row.author_id,
      write_access: row.write_access,
      my_permission: row.my_permission,
      is_public: row.is_public,
    });
    setShowRagSettings(false);
    setPopoverAnchor(null);
    setCurrentFolderPath('');
    setFileSearchQuery('');
  }, []);

  const loadKbById = useCallback(
    async (kbId: number) => {
      try {
        const resp = await authFetch(`${getApiUrl(API_ENDPOINTS.KNOWLEDGE_BASES)}/${kbId}`, {
          headers: authHeaders(),
        });
        if (!resp.ok) {
          showNotification('error', 'Не удалось открыть базу знаний');
          return;
        }
        const row = (await resp.json()) as KbRow;
        openExisting(row);
        setItems((prev) => (prev.some((x) => x.id === row.id) ? prev : [row, ...prev]));
      } catch (e) {
        showNotification('error', e instanceof Error ? e.message : 'Ошибка загрузки');
      }
    },
    [authHeaders, openExisting, showNotification],
  );

  useEffect(() => {
    if (!isOpen) return;
    void (async () => {
      try {
        const raw = sessionStorage.getItem(ASTRA_OPEN_KNOWLEDGE_BASES_SIDEBAR_ID_KEY);
        if (!raw) return;
        sessionStorage.removeItem(ASTRA_OPEN_KNOWLEDGE_BASES_SIDEBAR_ID_KEY);
        const id = Number(raw);
        if (Number.isFinite(id) && id > 0) {
          await loadKbById(id);
        }
      } catch {
        /* */
      }
    })();
  }, [isOpen, loadKbById]);

  useEffect(() => {
    const onOpen = (e: Event) => {
      const detail = (e as CustomEvent<{ kbId?: number }>).detail;
      const id = detail?.kbId;
      if (typeof id === 'number' && Number.isFinite(id) && id > 0) {
        void loadKbById(id);
      }
    };
    window.addEventListener(ASTRA_OPEN_KNOWLEDGE_BASES_SIDEBAR, onOpen);
    return () => window.removeEventListener(ASTRA_OPEN_KNOWLEDGE_BASES_SIDEBAR, onOpen);
  }, [loadKbById]);

  const handleSave = async () => {
    const name = form.name.trim();
    if (!name) {
      showNotification('warning', 'Укажите название базы знаний');
      return;
    }
    if (!canCreateAgents && selectedId === 'new') {
      showNotification('error', 'Недостаточно прав для создания базы знаний');
      return;
    }
    setIsSaving(true);
    try {
      if (selectedId === 'new') {
        const resp = await authFetch(`${getApiUrl(API_ENDPOINTS.KNOWLEDGE_BASES)}/create`, {
          method: 'POST',
          headers: authHeaders(),
          body: JSON.stringify({
            name,
            description: form.description.trim() || null,
            is_public: false,
          }),
        });
        if (!resp.ok) {
          const t = await resp.text().catch(() => '');
          throw new Error(t || `Ошибка создания: ${resp.status}`);
        }
        const created = (await resp.json()) as KbRow;
        if (ragDraft) {
          await saveEntityRagSettings({
            scope: 'kb',
            entityId: created.id,
            entityName: created.name,
            instructions: form.description || '',
            draft: ragDraft,
          });
        }
        showNotification('success', 'База знаний создана');
        await loadList();
        openExisting(created);
      } else {
        const resp = await authFetch(`${getApiUrl(API_ENDPOINTS.KNOWLEDGE_BASES)}/${selectedId}`, {
          method: 'PUT',
          headers: authHeaders(),
          body: JSON.stringify({
            name,
            description: form.description.trim() || null,
          }),
        });
        if (!resp.ok) {
          const t = await resp.text().catch(() => '');
          throw new Error(t || `Ошибка сохранения: ${resp.status}`);
        }
        if (ragDraft) {
          await saveEntityRagSettings({
            scope: 'kb',
            entityId: selectedId,
            entityName: name,
            instructions: form.description || '',
            draft: ragDraft,
          });
        }
        showNotification('success', 'База знаний сохранена');
        await loadList();
      }
    } catch (e) {
      showNotification('error', e instanceof Error ? e.message : 'Ошибка сохранения');
    } finally {
      setIsSaving(false);
    }
  };

  const handleTogglePublish = async () => {
    if (typeof selectedId !== 'number' || !canPublish) return;
    setIsPublishing(true);
    try {
      const next = !Boolean(detailMeta?.is_public || form.is_public);
      const resp = await authFetch(`${getApiUrl(API_ENDPOINTS.KNOWLEDGE_BASES)}/${selectedId}`, {
        method: 'PUT',
        headers: authHeaders(),
        body: JSON.stringify({ is_public: next }),
      });
      if (!resp.ok) {
        const t = await resp.text().catch(() => '');
        throw new Error(t || `Ошибка публикации: ${resp.status}`);
      }
      setForm((f) => ({ ...f, is_public: next }));
      setDetailMeta((m) => (m ? { ...m, is_public: next } : m));
      showNotification('success', next ? 'Опубликовано в галерее' : 'Снято с публикации');
      await loadList();
    } catch (e) {
      showNotification('error', e instanceof Error ? e.message : 'Ошибка публикации');
    } finally {
      setIsPublishing(false);
    }
  };

  const handleDelete = async () => {
    if (typeof selectedId !== 'number' || !isOwner) return;
    if (
      !window.confirm(
        'Удалить базу знаний вместе со всеми документами и файлами в хранилище? Это действие нельзя отменить.',
      )
    ) {
      return;
    }
    try {
      const resp = await authFetch(`${getApiUrl(API_ENDPOINTS.KNOWLEDGE_BASES)}/${selectedId}`, {
        method: 'DELETE',
        headers: authHeaders(),
      });
      if (!resp.ok) {
        const t = await resp.text().catch(() => '');
        throw new Error(t || `Ошибка удаления: ${resp.status}`);
      }
      showNotification('success', 'База знаний удалена');
      resetNew();
      await loadList();
    } catch (e) {
      showNotification('error', e instanceof Error ? e.message : 'Ошибка удаления');
    }
  };

  const uploadFiles = async (files: FileList | File[]) => {
    if (typeof selectedId !== 'number') {
      showNotification('warning', 'Сначала создайте и сохраните базу знаний');
      return;
    }
    // FileList — live: обнулять value input до конца async нельзя.
    const list = Array.from(files || []);
    if (!list.length) return;
    const pendingEntries = createRagPendingUploads(list);
    setPendingUploads((prev) => [...prev, ...pendingEntries]);
    setIsUploading(true);
    let ok = 0;
    try {
      await mapWithConcurrency(list, RAG_UPLOAD_CONCURRENCY, async (file, index) => {
        const pendingId = pendingEntries[index]?.clientId;
        const fd = new FormData();
        fd.append('file', file);
        fd.append('knowledge_base_id', String(selectedId));
        const rel =
          (file as File & { webkitRelativePath?: string }).webkitRelativePath ||
          file.name;
        if (rel) fd.append('relative_path', normalizeRelativePath(rel));
        const headers: HeadersInit = {};
        if (token) headers.Authorization = `Bearer ${token}`;
        try {
          const resp = await authFetch(getApiUrl(API_ENDPOINTS.KB_DOCUMENTS_UPLOAD), {
            method: 'POST',
            headers,
            body: fd,
          });
          if (resp.ok) {
            ok += 1;
          } else {
            const t = await resp.text().catch(() => '');
            showNotification('error', t || `Не удалось загрузить ${file.name}`);
          }
        } catch (e) {
          showNotification(
            'error',
            e instanceof Error ? e.message : `Ошибка загрузки ${file.name}`,
          );
        } finally {
          if (pendingId) {
            commitRagUploadUiUpdate(() => {
              setPendingUploads((prev) => removeRagPendingUploads(prev, [pendingId]));
            });
          }
        }
      });
      if (ok) {
        showNotification(
          'success',
          ok === 1 ? 'Файл загружен' : `Загружено файлов: ${ok}`,
        );
        setCurrentFolderPath('');
        setFileSearchQuery('');
        await loadDocs(selectedId);
        await loadList();
      }
    } finally {
      setIsUploading(false);
      setPendingUploads([]);
      if (fileInputRef.current) fileInputRef.current.value = '';
      if (dirInputRef.current) dirInputRef.current.value = '';
    }
  };

  const handleDeleteDoc = async (docId: number) => {
    if (typeof selectedId !== 'number' || readOnly) return;
    try {
      const headers: HeadersInit = token ? { Authorization: `Bearer ${token}` } : {};
      const resp = await authFetch(
        `${getApiUrl(API_ENDPOINTS.KB_DOCUMENTS_DELETE)}/${docId}`,
        { method: 'DELETE', headers },
      );
      if (!resp.ok) {
        const t = await resp.text().catch(() => '');
        throw new Error(t || `Ошибка удаления: ${resp.status}`);
      }
      setDocs((prev) => prev.filter((d) => d.id !== docId));
      await loadList();
    } catch (e) {
      showNotification('error', e instanceof Error ? e.message : 'Ошибка удаления файла');
    }
  };

  if (showRagSettings && typeof selectedId === 'number') {
    return (
      <RAGSettings
        lockedScope="kb"
        entityId={selectedId}
        entityName={form.name || selectedLabel}
        variant="panel"
        draft
        draftValue={ragDraft}
        onDraftChange={(d) => {
          setRagDraft(d);
          setShowRagSettings(false);
          showNotification('info', 'Настройки РАГ сохранятся вместе с базой знаний');
        }}
        onClose={() => setShowRagSettings(false)}
        panelTitle="Настройки РАГ для базы знаний"
        entityInstructionsPrompt={form.description}
        readOnly={readOnly}
        isDarkMode={isDarkMode}
      />
    );
  }

  return (
    <Box
      sx={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        minHeight: 0,
        color: panelChrome.fg,
        ...SIDEBAR_HIDE_SCROLLBAR_SX,
      }}
    >
      <Box sx={{ px: 2, pt: 1.5, flexShrink: 0 }}>
        <Box onClick={(e) => setPopoverAnchor(e.currentTarget)} sx={formFieldTriggerSx}>
          <Typography sx={{ ...formFieldTriggerValueSx, fontWeight: 600 }} noWrap>
            {selectedLabel}
          </Typography>
          <ExpandMoreIcon
            sx={{
              ...dropdownChevronSx,
              transform: Boolean(popoverAnchor) ? 'rotate(180deg)' : 'none',
            }}
          />
        </Box>
        <Popover
          open={Boolean(popoverAnchor)}
          anchorEl={popoverAnchor}
          onClose={() => {
            setPopoverAnchor(null);
            setSearchQuery('');
          }}
          anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
          transformOrigin={{ vertical: 'top', horizontal: 'left' }}
          slotProps={{
            paper: {
              sx: {
                ...getDropdownPopoverPaperSx(popoverAnchor, darkFields),
                maxHeight: 360,
              },
            },
          }}
        >
          <Box sx={{ p: 1 }}>
            <TextField
              size="small"
              fullWidth
              placeholder="Поиск…"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              InputProps={{
                startAdornment: <SearchIcon sx={{ fontSize: 16, mr: 0.75, opacity: 0.5 }} />,
              }}
              sx={formFieldInputSx}
            />
          </Box>
          <Box sx={{ maxHeight: 280, overflowY: 'auto', py: 0.5, ...SIDEBAR_HIDE_SCROLLBAR_SX }}>
            {canCreateAgents && (
              <Box
                onClick={() => {
                  resetNew();
                  setPopoverAnchor(null);
                }}
                sx={{
                  ...dropdownItemSx,
                  ...getDropdownItemStateSx(darkFields, selectedId === 'new'),
                  fontStyle: 'italic',
                  opacity: selectedId === 'new' ? 1 : 0.7,
                }}
              >
                + Новая база знаний
              </Box>
            )}
            {loading ? (
              <Box sx={{ py: 2, display: 'flex', justifyContent: 'center' }}>
                <CircularProgress size={18} />
              </Box>
            ) : filtered.length === 0 ? (
              <Box
                sx={{
                  px: 1.5,
                  py: 1.5,
                  fontSize: '0.78rem',
                  color: darkFields ? 'rgba(255,255,255,0.35)' : 'rgba(0,0,0,0.45)',
                  textAlign: 'center',
                }}
              >
                Не найдено
              </Box>
            ) : (
              filtered.map((row) => (
                <Box
                  key={row.id}
                  onClick={() => openExisting(row)}
                  sx={{
                    ...dropdownItemSx,
                    ...getDropdownItemStateSx(darkFields, selectedId === row.id),
                  }}
                >
                  <Box component="span" sx={{ display: 'block', fontSize: '0.82rem' }}>
                    {row.name}
                  </Box>
                  <Box
                    component="span"
                    sx={{ display: 'block', opacity: 0.5, fontSize: '0.72rem' }}
                  >
                    {row.document_count || 0} док.
                  </Box>
                </Box>
              ))
            )}
          </Box>
        </Popover>
      </Box>

      <Box
        sx={{
          flex: 1,
          minHeight: 0,
          overflowY: 'auto',
          px: 2,
          py: 1.5,
          display: 'flex',
          flexDirection: 'column',
          gap: 1.25,
          ...SIDEBAR_HIDE_SCROLLBAR_SX,
        }}
      >
        <Box>
          <TextField
            value={form.name}
            onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
            label="Название"
            placeholder="Введите название базы знаний"
            variant="outlined"
            size="small"
            fullWidth
            required
            disabled={readOnly}
            sx={nameFieldSx}
            inputProps={{ maxLength: 255 }}
          />
        </Box>
        <TextField
          value={form.description}
          onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
          label="Описание"
          placeholder="Кратко опишите содержимое базы знаний"
          variant="outlined"
          size="small"
          fullWidth
          multiline
          minRows={3}
          maxRows={8}
          disabled={readOnly}
          sx={multilineFieldSx}
        />

        {typeof selectedId === 'number' && (
          <>
            <Typography sx={{ fontSize: '0.78rem', fontWeight: 600, mb: 0.75, opacity: 0.85 }}>
              Документы ({docs.length})
            </Typography>
            <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', mb: 1 }}>
              <Button
                size="small"
                startIcon={<UploadFileIcon />}
                disabled={readOnly || isUploading}
                onClick={() => fileInputRef.current?.click()}
                sx={footerNeutralActionBtnSx}
              >
                Загрузить файлы
              </Button>
              <Button
                size="small"
                startIcon={<FolderOpenIcon />}
                disabled={readOnly || isUploading}
                onClick={() => dirInputRef.current?.click()}
                sx={footerNeutralActionBtnSx}
              >
                Загрузить каталог
              </Button>
              <Button
                size="small"
                startIcon={<SettingsIcon />}
                disabled={isUploading}
                onClick={() => setShowRagSettings(true)}
                sx={footerNeutralActionBtnSx}
              >
                Настройки РАГ
              </Button>
            </Box>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              hidden
              onChange={(e) => {
                if (e.target.files?.length) void uploadFiles(e.target.files);
              }}
            />
            <input
              ref={(el) => {
                dirInputRef.current = el;
                if (el) {
                  el.setAttribute('webkitdirectory', '');
                  el.setAttribute('directory', '');
                }
              }}
              type="file"
              multiple
              hidden
              onChange={(e) => {
                if (e.target.files?.length) void uploadFiles(e.target.files);
              }}
            />
            {totalFiles > 0 && (
              <Box sx={{ mb: 1, minWidth: 0, width: '100%' }}>
                <RagFilesSearchField
                  value={fileSearchQuery}
                  onChange={setFileSearchQuery}
                  totalCount={totalFiles}
                  variant="sidebar"
                  size="small"
                />
                {hasActiveFileSearch && (
                  <Typography
                    sx={{
                      color: fileMutedColor,
                      fontSize: '0.65rem',
                      display: 'block',
                      mt: 0.5,
                      mb: 0.25,
                    }}
                  >
                    {visiblePendingUploads.length + visibleFiles.length} из {totalFiles}
                  </Typography>
                )}
              </Box>
            )}

            {!hasActiveFileSearch && pathSegments.length > 0 && (
              <Box
                sx={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 0.5,
                  mb: 0.75,
                  minWidth: 0,
                }}
              >
                <IconButton
                  size="small"
                  onClick={() => {
                    const next = pathSegments.slice(0, -1).join('/');
                    setCurrentFolderPath(next);
                  }}
                  sx={{ color: fileMutedColor, p: 0.35 }}
                  aria-label="На уровень выше"
                >
                  <ArrowBackIcon sx={{ fontSize: 16 }} />
                </IconButton>
                <Typography
                  noWrap
                  sx={{ fontSize: '0.78rem', fontWeight: 600, color: fileNameColor, minWidth: 0 }}
                  title={currentFolderPath}
                >
                  {pathSegments[pathSegments.length - 1]}
                </Typography>
              </Box>
            )}

            {visibleFolders.length > 0 && (
              <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.25, mb: 0.75 }}>
                {visibleFolders.map((folderName) => (
                  <Box
                    key={folderName}
                    onClick={() => setCurrentFolderPath(joinPath(currentFolderPath, folderName))}
                    sx={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 1,
                      px: 0.75,
                      py: 0.65,
                      borderRadius: 1,
                      cursor: 'pointer',
                      '&:hover': { bgcolor: folderRowHover },
                    }}
                  >
                    <FolderIcon sx={{ fontSize: 20, color: fileMutedColor, flexShrink: 0 }} />
                    <Typography
                      noWrap
                      sx={{ fontSize: '0.8rem', color: fileNameColor, fontWeight: 500, minWidth: 0 }}
                    >
                      {folderName}
                    </Typography>
                  </Box>
                ))}
              </Box>
            )}

            {visiblePendingUploads.length > 0 || visibleFiles.length > 0 ? (
              <Box
                sx={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(2, minmax(0, 1fr))',
                  gap: 0.75,
                  minWidth: 0,
                  width: '100%',
                }}
              >
                {visiblePendingUploads.map((pending) => (
                  <Box key={pending.clientId} sx={fileCardSx}>
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, minWidth: 0, pr: 2 }}>
                      <RagUploadingFileThumb
                        filename={pending.filename}
                        size={32}
                        isDarkMode={isDarkMode}
                      />
                      <Box sx={{ minWidth: 0, flex: 1 }}>
                        <Typography
                          variant="caption"
                          noWrap
                          component="span"
                          sx={{
                            color: fileNameColor,
                            fontSize: '0.68rem',
                            display: 'block',
                            fontWeight: 500,
                            lineHeight: 1.3,
                          }}
                          title={pending.filename}
                        >
                          {pending.filename}
                        </Typography>
                        <Typography
                          variant="caption"
                          sx={{ color: fileTypeColor, fontSize: '0.62rem', lineHeight: 1.2 }}
                        >
                          {getFileTypeLabel(pending.filename)}
                        </Typography>
                      </Box>
                    </Box>
                  </Box>
                ))}
                {visibleFiles.map((doc) => {
                  const displayName = basenameOf(getDocRelativePath(doc)) || doc.filename;
                  return (
                    <Box key={doc.id} sx={fileCardSx}>
                      {!readOnly && (
                        <IconButton
                          size="small"
                          onClick={() => void handleDeleteDoc(doc.id)}
                          sx={{
                            position: 'absolute',
                            top: 3,
                            right: 3,
                            p: 0.2,
                            color: fileMutedColor,
                            '&:hover': { color: '#ef5350', bgcolor: 'rgba(239,83,80,0.12)' },
                          }}
                        >
                          <CloseIcon sx={{ fontSize: 11 }} />
                        </IconButton>
                      )}
                      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, minWidth: 0, pr: 2 }}>
                        <Box
                          sx={{
                            width: 32,
                            height: 32,
                            borderRadius: 1,
                            bgcolor: getFileIconBg(displayName),
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            flexShrink: 0,
                          }}
                        >
                          {getFileIcon(displayName)}
                        </Box>
                        <Box sx={{ minWidth: 0, flex: 1 }}>
                          <Tooltip title={getDocRelativePath(doc) || displayName} placement="top">
                            <Typography
                              variant="caption"
                              noWrap
                              component="span"
                              sx={{
                                color: fileNameColor,
                                fontSize: '0.68rem',
                                display: 'block',
                                fontWeight: 500,
                                lineHeight: 1.3,
                              }}
                            >
                              {displayName}
                            </Typography>
                          </Tooltip>
                          <Typography
                            variant="caption"
                            sx={{ color: fileTypeColor, fontSize: '0.62rem', lineHeight: 1.2 }}
                          >
                            {getFileTypeLabel(displayName)}
                          </Typography>
                        </Box>
                      </Box>
                    </Box>
                  );
                })}
              </Box>
            ) : docs.length === 0 && pendingUploads.length === 0 ? (
              <Typography sx={{ fontSize: '0.75rem', opacity: 0.55, py: 1 }}>
                Пока нет документов
              </Typography>
            ) : hasActiveFileSearch ? (
              <Typography sx={{ fontSize: '0.72rem', color: fileMutedColor, py: 0.5 }}>
                Ничего не найдено
              </Typography>
            ) : visibleFolders.length === 0 ? (
              <Typography sx={{ fontSize: '0.72rem', color: fileMutedColor, py: 0.5 }}>
                Папка пуста
              </Typography>
            ) : null}          </>
        )}
      </Box>

      <Box sx={{ px: 2, py: 1.5, display: 'flex', flexDirection: 'column', gap: 1 }}>
        {typeof selectedId === 'number' && (isOwner || canPublish) && (
          <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', width: '100%' }}>
            {isOwner && (
              <Box component="span" sx={{ flex: '1 1 140px', minWidth: 0, display: 'flex' }}>
                <Button
                  size="small"
                  fullWidth
                  startIcon={<ShareIcon />}
                  onClick={() => setShareDialogOpen(true)}
                  sx={footerNeutralActionBtnSx}
                >
                  Поделиться
                </Button>
              </Box>
            )}
            {canPublish && (
              <Box component="span" sx={{ flex: '1 1 140px', minWidth: 0, display: 'flex' }}>
                <Button
                  size="small"
                  fullWidth
                  disabled={isPublishing}
                  startIcon={
                    isPublishing ? (
                      <CircularProgress size={12} sx={{ color: 'inherit' }} />
                    ) : form.is_public ? (
                      <PublicOffIcon />
                    ) : (
                      <PublicIcon />
                    )
                  }
                  onClick={() => void handleTogglePublish()}
                  sx={{
                    ...footerActionBtnSx,
                    flex: '1 1 auto',
                    width: '100%',
                    color: form.is_public ? '#2e7d32' : panelChrome.fgMuted,
                    border: `1px solid ${
                      form.is_public
                        ? 'rgba(46,125,50,0.45)'
                        : panelChrome.buttonBorder.replace('1px solid ', '')
                    }`,
                    bgcolor: form.is_public ? 'rgba(46,125,50,0.1)' : 'transparent',
                    '&:hover': {
                      bgcolor: form.is_public ? 'rgba(46,125,50,0.16)' : panelChrome.hoverBg,
                      color: form.is_public ? '#2e7d32' : panelChrome.fgMuted,
                    },
                    '&:disabled': { color: panelChrome.fgSubtle },
                  }}
                >
                  {form.is_public ? 'Снять с галереи' : 'Опубликовать в галерее'}
                </Button>
              </Box>
            )}
            {isOwner && (
              <Box component="span" sx={{ flex: '1 1 140px', minWidth: 0, display: 'flex' }}>
                <Button
                  size="small"
                  fullWidth
                  startIcon={<DeleteIcon />}
                  onClick={() => void handleDelete()}
                  data-sidebar-danger
                  sx={footerDeleteActionBtnSx}
                >
                  Удалить
                </Button>
              </Box>
            )}
          </Box>
        )}
        <Button
          fullWidth
          variant="contained"
          startIcon={
            isSaving ? <CircularProgress size={14} sx={{ color: 'white' }} /> : <SaveIcon />
          }
          disabled={isSaving || readOnly || (!canCreateAgents && selectedId === 'new')}
          onClick={() => void handleSave()}
          sx={{
            bgcolor: '#2e7d32',
            textTransform: 'none',
            fontWeight: 600,
            fontSize: '0.82rem',
            py: 0.9,
            justifyContent: 'flex-start',
            textAlign: 'left',
            '& .MuiButton-startIcon': {
              marginRight: '8px',
              marginLeft: 0,
              color: 'inherit',
            },
            '&:hover': { bgcolor: '#388e3c' },
            '&:disabled': {
              bgcolor: 'rgba(46,125,50,0.4)',
              color: 'rgba(255,255,255,0.5)',
            },
          }}
        >
          {isSaving ? 'Сохраняю...' : 'Сохранить'}
        </Button>
      </Box>

      {typeof selectedId === 'number' && (
        <ShareKnowledgeBaseDialog
          open={shareDialogOpen}
          onClose={() => setShareDialogOpen(false)}
          kbId={selectedId}
          kbName={form.name || selectedLabel}
          isDarkMode={isDarkMode}
        />
      )}
    </Box>
  );
}
