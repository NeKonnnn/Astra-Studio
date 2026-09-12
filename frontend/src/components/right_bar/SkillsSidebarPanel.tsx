/**
 * Панель Skills под кнопкой на правой боковой панели (как конструктор агента).
 * — выбор skill / новый skill
 * — редактирование и сохранение
 * — Поделиться / Опубликовать / Удалить / Import / Export
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Box,
  Button,
  Checkbox,
  CircularProgress,
  FormControlLabel,
  IconButton,
  Popover,
  TextField,
  Tooltip,
  Typography,
  useTheme,
} from '@mui/material';
import {
  Delete as DeleteIcon,
  ExpandMore as ExpandMoreIcon,
  FileDownload as ExportIcon,
  FileUpload as ImportIcon,
  HelpOutline as HelpOutlineIcon,
  Public as PublicIcon,
  PublicOff as PublicOffIcon,
  Save as SaveIcon,
  Search as SearchIcon,
  Share as ShareIcon,
} from '@mui/icons-material';
import type { SxProps, Theme } from '@mui/material/styles';
import { useAuth } from '../../contexts/AuthContext';
import { useAppActions, useAppContext } from '../../contexts/AppContext';
import { getApiUrl, API_ENDPOINTS } from '../../config/api';
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
} from '../../constants/menuStyles';
import {
  getSidebarPanelBackground,
  getSidebarPanelChrome,
} from '../../constants/sidebarPanelColor';
import {
  getActiveSkillIds,
  renameSkillSlugInAllChats,
  SKILL_SELECTION_CHANGED_EVENT,
  toggleActiveSkill,
} from '../../utils/skillSelectionStorage';
import {
  exportSkillsJson,
  formatSkillsApiDetail,
  importSkillFile,
  notifySkillsChanged,
  slugifySkillName,
} from '../../utils/skillsImportExport';
import ShareSkillDialog from '../ShareSkillDialog';
import SkillFilesEditor from '../skills/SkillFilesEditor';
import {
  ASTRA_OPEN_SKILLS_SIDEBAR,
  ASTRA_OPEN_SKILLS_SIDEBAR_ID_KEY,
} from '../../constants/hotkeys';

/** Если skill был активен в чатах под старым slug — обновить упоминание. */
function renameActiveSkillSlug(oldSlug: string, newSlug: string, name?: string): void {
  renameSkillSlugInAllChats(oldSlug, newSlug, name);
}

/** Подпись поля + «?» со всплывающей подсказкой (как «Стратегия поиска» в RAG). */
function FieldWithHelp({
  children,
  help,
  ariaLabel,
}: {
  children: React.ReactNode;
  help: React.ReactNode;
  ariaLabel: string;
}) {
  return (
    <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 0.5, width: '100%' }}>
      <Box sx={{ flex: 1, minWidth: 0 }}>{children}</Box>
      <Tooltip title={help} arrow placement="top">
        <IconButton
          size="small"
          aria-label={ariaLabel}
          sx={{
            mt: 0.75,
            p: 0.35,
            color: 'inherit',
            opacity: 0.45,
            '&:hover': { opacity: 0.75, bgcolor: 'transparent' },
          }}
        >
          <HelpOutlineIcon sx={{ fontSize: 16 }} />
        </IconButton>
      </Tooltip>
    </Box>
  );
}

interface SkillRow {
  id: number;
  slug: string;
  name: string;
  display_title?: string | null;
  description?: string | null;
  content?: string;
  is_active: boolean;
  is_public?: boolean;
  user_invocable?: boolean;
  disable_model_invocation?: boolean;
  always_apply?: boolean;
  allowed_tools?: string[];
  category?: string | null;
  author_id: string;
  write_access?: boolean;
  my_permission?: 'owner' | 'editor' | 'viewer' | string | null;
  meta?: { tags?: string[] };
}

const emptyForm = {
  slug: '',
  name: '',
  display_title: '',
  description: '',
  content: '',
  is_active: true,
  is_public: false,
  user_invocable: true,
  disable_model_invocation: false,
  always_apply: false,
  allowed_tools: '',
  category: '',
  tags: '',
};

interface SkillsSidebarPanelProps {
  isOpen?: boolean;
}

export default function SkillsSidebarPanel({
  isOpen = true,
}: SkillsSidebarPanelProps) {
  const theme = useTheme();
  const isDarkMode = theme.palette.mode === 'dark';
  const { token, user } = useAuth();
  const { state: appState } = useAppContext();
  const currentChatId = appState.currentChatId;
  const { showNotification } = useAppActions();
  const importRef = useRef<HTMLInputElement>(null);

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
  /** Как «Инструкции» в конструкторе агента: без двойного padding у multiline. */
  const multilineFieldSx = useMemo(
    () =>
      [
        formFieldInputSx,
        {
          '& .MuiOutlinedInput-root.MuiInputBase-multiline': {
            padding: 0,
          },
          '& .MuiOutlinedInput-root.MuiInputBase-multiline .MuiOutlinedInput-input': {
            padding: `${AGENT_CONSTRUCTOR_FIELD_PADDING_Y_PX}px ${AGENT_CONSTRUCTOR_FIELD_PADDING_X_PX}px !important`,
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
      '& .MuiButton-startIcon': {
        marginRight: '8px',
        marginLeft: 0,
        color: 'inherit',
      },
      '& .MuiButton-startIcon .MuiSvgIcon-root': {
        fontSize: '0.9rem',
      },
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
  const checkboxSx = useMemo(
    () => ({
      color: panelChrome.fgSubtle,
      '&.Mui-checked': { color: '#2196f3' },
      p: 0.5,
    }),
    [panelChrome.fgSubtle],
  );
  const checkLabelSx = useMemo(
    () => ({
      ml: 0,
      '& .MuiFormControlLabel-label': {
        fontSize: '0.78rem',
        color: panelChrome.fgMuted,
      },
    }),
    [panelChrome.fgMuted],
  );

  const [skills, setSkills] = useState<SkillRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [popoverAnchor, setPopoverAnchor] = useState<HTMLElement | null>(null);
  const [selectedSkillId, setSelectedSkillId] = useState<number | 'new'>('new');
  const [detailMeta, setDetailMeta] = useState<Pick<
    SkillRow,
    'author_id' | 'write_access' | 'my_permission' | 'is_public'
  > | null>(null);
  const [form, setForm] = useState(emptyForm);
  /** Slug, с которым skill был загружен/сохранён (для обновления $упоминания в чате). */
  const persistedSlugRef = useRef<string>('');
  const [isSaving, setIsSaving] = useState(false);
  const [isPublishing, setIsPublishing] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [shareDialogOpen, setShareDialogOpen] = useState(false);
  const [busyImportExport, setBusyImportExport] = useState(false);
  const [activeIds, setActiveIds] = useState<string[]>(() => getActiveSkillIds(currentChatId));
  const [loadingDetail, setLoadingDetail] = useState(false);

  const authHeaders = useCallback((): HeadersInit => {
    const h: HeadersInit = { 'Content-Type': 'application/json' };
    if (token) h.Authorization = `Bearer ${token}`;
    return h;
  }, [token]);

  const loadSkills = useCallback(async () => {
    setLoading(true);
    try {
      const headers: HeadersInit = token ? { Authorization: `Bearer ${token}` } : {};
      const resp = await fetch(`${getApiUrl(API_ENDPOINTS.SKILLS)}/list?limit=100`, { headers });
      if (!resp.ok) {
        setSkills([]);
        return;
      }
      const data = await resp.json();
      setSkills((data.items || []) as SkillRow[]);
    } catch {
      setSkills([]);
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    if (isOpen) void loadSkills();
  }, [isOpen, loadSkills]);

  useEffect(() => {
    setActiveIds(getActiveSkillIds(currentChatId));
    const onChange = (e: Event) => {
      const detailChatId = (e as CustomEvent<{ chatId?: string }>).detail?.chatId;
      if (detailChatId && currentChatId && detailChatId !== currentChatId) return;
      setActiveIds(getActiveSkillIds(currentChatId));
    };
    window.addEventListener(SKILL_SELECTION_CHANGED_EVENT, onChange as EventListener);
    return () => window.removeEventListener(SKILL_SELECTION_CHANGED_EVENT, onChange as EventListener);
  }, [currentChatId]);

  const resetToNew = useCallback(() => {
    setSelectedSkillId('new');
    setDetailMeta(null);
    setForm(emptyForm);
    persistedSlugRef.current = '';
    setSaveSuccess(false);
    setSaveError(null);
  }, []);

  const loadSkillDetail = useCallback(
    async (id: number) => {
      setLoadingDetail(true);
      try {
        const resp = await fetch(`${getApiUrl(API_ENDPOINTS.SKILLS)}/${id}`, {
          headers: authHeaders(),
        });
        if (!resp.ok) throw new Error('Не удалось загрузить skill');
        const full: SkillRow = await resp.json();
        setSelectedSkillId(full.id);
        setDetailMeta({
          author_id: full.author_id,
          write_access: full.write_access,
          my_permission: full.my_permission,
          is_public: full.is_public,
        });
        persistedSlugRef.current = full.slug || '';
        setForm({
          slug: full.slug,
          name: full.name,
          display_title: full.display_title || full.name,
          description: full.description || '',
          content: full.content || '',
          is_active: full.is_active !== false,
          is_public: Boolean(full.is_public),
          user_invocable: full.user_invocable !== false,
          disable_model_invocation: Boolean(full.disable_model_invocation),
          always_apply: Boolean(full.always_apply),
          allowed_tools: (full.allowed_tools || []).join(', '),
          category: full.category || '',
          tags: (full.meta?.tags || []).join(', '),
        });
      } catch (e) {
        showNotification('error', e instanceof Error ? e.message : 'Ошибка загрузки');
        resetToNew();
      } finally {
        setLoadingDetail(false);
      }
    },
    [authHeaders, resetToNew, showNotification],
  );

  useEffect(() => {
    if (!isOpen) return;
    void (async () => {
      try {
        const raw = sessionStorage.getItem(ASTRA_OPEN_SKILLS_SIDEBAR_ID_KEY);
        if (!raw) return;
        sessionStorage.removeItem(ASTRA_OPEN_SKILLS_SIDEBAR_ID_KEY);
        const id = Number(raw);
        if (Number.isFinite(id) && id > 0) {
          await loadSkillDetail(id);
        }
      } catch {
        /* */
      }
    })();
  }, [isOpen, loadSkillDetail]);

  useEffect(() => {
    const onOpen = (e: Event) => {
      const detail = (e as CustomEvent<{ skillId?: number }>).detail;
      const id = detail?.skillId;
      if (typeof id === 'number' && Number.isFinite(id) && id > 0) {
        void loadSkillDetail(id);
      }
    };
    window.addEventListener(ASTRA_OPEN_SKILLS_SIDEBAR, onOpen);
    return () => window.removeEventListener(ASTRA_OPEN_SKILLS_SIDEBAR, onOpen);
  }, [loadSkillDetail]);

  const selectSkill = useCallback(
    (id: number | 'new') => {
      setPopoverAnchor(null);
      setSearchQuery('');
      setSaveSuccess(false);
      setSaveError(null);
      if (id === 'new') {
        resetToNew();
        return;
      }
      void loadSkillDetail(id);
    },
    [loadSkillDetail, resetToNew],
  );

  const filtered = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return skills;
    return skills.filter((s) => {
      const title = (s.display_title || s.name || '').toLowerCase();
      return [title, s.slug, s.description || ''].join(' ').toLowerCase().includes(q);
    });
  }, [skills, searchQuery]);

  const selectedSkill =
    selectedSkillId === 'new' ? null : skills.find((s) => s.id === selectedSkillId) || null;

  const userId = (user?.user_id || '').toLowerCase();
  const authorId = (detailMeta?.author_id || selectedSkill?.author_id || '').toLowerCase();
  const myPermission = detailMeta?.my_permission ?? selectedSkill?.my_permission;
  const writeAccess = detailMeta?.write_access ?? selectedSkill?.write_access;
  const selectedRole: 'owner' | 'editor' | 'viewer' =
    selectedSkillId === 'new'
      ? 'owner'
      : myPermission === 'editor' || myPermission === 'viewer'
        ? myPermission
        : authorId === userId || Boolean(user?.is_admin)
          ? 'owner'
          : writeAccess
            ? 'editor'
            : 'viewer';
  const isOwner = selectedRole === 'owner';
  const canEdit = selectedRole === 'owner' || selectedRole === 'editor';
  const readOnly = !canEdit;
  // Источник истины — форма (после load/save/publish), без OR со stale list-item
  const isPublic = Boolean(form.is_public);

  const selectedLabel =
    selectedSkillId === 'new'
      ? 'Новый skill'
      : selectedSkill?.display_title ||
        selectedSkill?.name ||
        form.display_title ||
        form.name ||
        'Skill';

  const handleSave = async () => {
    if (!form.name.trim() || !form.content.trim()) {
      const msg = 'Укажите название и текст skill (поле «Текст / SKILL.md»)';
      setSaveError(msg);
      setSaveSuccess(false);
      showNotification('error', msg);
      return;
    }
    if (readOnly) {
      const msg = 'Нет прав на сохранение этого skill';
      setSaveError(msg);
      showNotification('error', msg);
      return;
    }
    setIsSaving(true);
    setSaveError(null);
    setSaveSuccess(false);
    try {
      const rawSlug = form.slug.trim();
      const slug = /^[a-z0-9][a-z0-9._-]*$/i.test(rawSlug)
        ? rawSlug.toLowerCase()
        : slugifySkillName(form.name);
      const payload = {
        slug,
        name: form.name.trim(),
        display_title: (form.display_title.trim() || form.name.trim()).slice(0, 128),
        description: form.description.trim() || null,
        content: form.content,
        is_active: form.is_active,
        is_public: form.is_public,
        user_invocable: form.user_invocable,
        disable_model_invocation: form.disable_model_invocation,
        always_apply: form.always_apply,
        allowed_tools: form.allowed_tools
          .split(',')
          .map((t) => t.trim())
          .filter(Boolean),
        category: form.category.trim() || null,
        meta: {
          tags: form.tags
            .split(',')
            .map((t) => t.trim())
            .filter(Boolean),
        },
      };
      const isEdit = selectedSkillId !== 'new';
      const url = isEdit
        ? `${getApiUrl(API_ENDPOINTS.SKILLS)}/${selectedSkillId}`
        : `${getApiUrl(API_ENDPOINTS.SKILLS)}/create`;
      const resp = await fetch(url, {
        method: isEdit ? 'PUT' : 'POST',
        headers: authHeaders(),
        body: JSON.stringify(payload),
      });
      const j = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        throw new Error(
          formatSkillsApiDetail(j.detail, 'Не удалось сохранить skill'),
        );
      }
      const saved: SkillRow = j;
      if (!saved?.id) {
        throw new Error('Сервер не вернул сохранённый skill');
      }
      const previousSlug = persistedSlugRef.current;
      const nextSlug = saved.slug || slug;
      if (isEdit) {
        renameActiveSkillSlug(
          previousSlug,
          nextSlug,
          saved.display_title || saved.name || form.name,
        );
      }
      persistedSlugRef.current = nextSlug;
      setSelectedSkillId(saved.id);
      setDetailMeta({
        author_id: saved.author_id,
        write_access: saved.write_access ?? true,
        my_permission: saved.my_permission || 'owner',
        is_public: saved.is_public,
      });
      setForm((f) => ({
        ...f,
        slug: saved.slug || slug,
        name: saved.name || f.name,
        display_title: saved.display_title || f.display_title,
        description: saved.description || f.description,
        content: saved.content ?? f.content,
        is_public: Boolean(saved.is_public),
        is_active: saved.is_active !== false,
        user_invocable: saved.user_invocable !== false,
        disable_model_invocation: Boolean(saved.disable_model_invocation),
        always_apply: Boolean(saved.always_apply),
        allowed_tools: (saved.allowed_tools || []).join(', ') || f.allowed_tools,
        category: saved.category || f.category,
        tags: (saved.meta?.tags || []).join(', ') || f.tags,
      }));
      await loadSkills();
      notifySkillsChanged();
      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 4000);
      showNotification('success', isEdit ? 'Skill обновлён' : 'Skill создан');
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Ошибка сохранения';
      setSaveError(msg);
      setSaveSuccess(false);
      showNotification('error', msg);
    } finally {
      setIsSaving(false);
    }
  };

  const handleTogglePublish = async () => {
    if (selectedSkillId === 'new' || typeof selectedSkillId !== 'number' || !isOwner) return;
    const nextPublic = !form.is_public;
    setIsPublishing(true);
    // Optimistic UI — иначе OR со stale list оставлял кнопку «Снять с публикации»
    setForm((f) => ({ ...f, is_public: nextPublic }));
    setDetailMeta((m) => (m ? { ...m, is_public: nextPublic } : m));
    try {
      const resp = await fetch(`${getApiUrl(API_ENDPOINTS.SKILLS)}/${selectedSkillId}`, {
        method: 'PUT',
        headers: authHeaders(),
        body: JSON.stringify({ is_public: nextPublic }),
      });
      const j = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        setForm((f) => ({ ...f, is_public: !nextPublic }));
        setDetailMeta((m) => (m ? { ...m, is_public: !nextPublic } : m));
        throw new Error(
          formatSkillsApiDetail(j.detail, 'Не удалось изменить публикацию'),
        );
      }
      const saved: SkillRow = j;
      const finalPublic = Boolean(saved.is_public ?? nextPublic);
      setForm((f) => ({ ...f, is_public: finalPublic }));
      setDetailMeta((m) =>
        m
          ? { ...m, is_public: finalPublic }
          : {
              author_id: saved.author_id || '',
              write_access: saved.write_access,
              my_permission: saved.my_permission,
              is_public: finalPublic,
            },
      );
      setSkills((prev) =>
        prev.map((s) => (s.id === selectedSkillId ? { ...s, is_public: finalPublic } : s)),
      );
      notifySkillsChanged();
      showNotification(
        'success',
        finalPublic ? 'Skill опубликован в галерее' : 'Skill снят с публикации в галерее',
      );
    } catch (e) {
      showNotification(
        'error',
        e instanceof Error ? e.message : 'Не удалось изменить публикацию',
      );
    } finally {
      setIsPublishing(false);
    }
  };

  const handleDelete = async () => {
    if (selectedSkillId === 'new' || !isOwner) return;
    const title = form.name || selectedSkill?.name || 'skill';
    if (!window.confirm(`Удалить skill «${title}»?`)) return;
    try {
      const resp = await fetch(`${getApiUrl(API_ENDPOINTS.SKILLS)}/${selectedSkillId}`, {
        method: 'DELETE',
        headers: authHeaders(),
      });
      if (!resp.ok) throw new Error('Не удалось удалить');
      showNotification('success', 'Skill удалён');
      resetToNew();
      await loadSkills();
      notifySkillsChanged();
    } catch (e) {
      showNotification('error', e instanceof Error ? e.message : 'Ошибка удаления');
    }
  };

  const handleExport = async () => {
    setBusyImportExport(true);
    try {
      await exportSkillsJson(token);
      showNotification('success', 'Skills экспортированы');
    } catch (e) {
      showNotification('error', e instanceof Error ? e.message : 'Ошибка export');
    } finally {
      setBusyImportExport(false);
    }
  };

  const handleImportFile = async (file: File) => {
    setBusyImportExport(true);
    try {
      const result = await importSkillFile(file, token);
      if (result.kind === 'json') {
        showNotification('success', `Импорт завершён (${result.imported})`);
        await loadSkills();
      } else {
        setSelectedSkillId('new');
        setDetailMeta(null);
        setForm({
          ...emptyForm,
          name: result.name,
          slug: result.slug,
          description: result.description,
          content: result.content,
          display_title: result.name,
        });
        showNotification('info', 'Данные из MD подставлены в форму — сохраните skill');
      }
    } catch (e) {
      showNotification('error', e instanceof Error ? e.message : 'Ошибка import');
    } finally {
      setBusyImportExport(false);
    }
  };

  const chatActive =
    selectedSkillId !== 'new' && Boolean(form.slug) && activeIds.includes(form.slug);

  if (!isOpen) return null;

  return (
    <Box
      sx={{
        flex: 1,
        minHeight: 0,
        overflow: 'hidden',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      <input
        ref={importRef}
        type="file"
        accept=".json,.md,.txt"
        hidden
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) void handleImportFile(f);
          e.target.value = '';
        }}
      />

      <Box sx={{ px: 2, pt: 1.5, flexShrink: 0 }}>
        <Box onClick={(e) => setPopoverAnchor(e.currentTarget)} sx={formFieldTriggerSx}>
          <Typography sx={{ ...formFieldTriggerValueSx, fontWeight: 600 }}>
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
            paper: { sx: getDropdownPopoverPaperSx(popoverAnchor, darkFields) },
          }}
        >
          <Box
            sx={{
              display: 'flex',
              alignItems: 'center',
              px: 1.5,
              py: 0.9,
              gap: 1,
              borderBottom: darkFields
                ? '1px solid rgba(255,255,255,0.07)'
                : '1px solid rgba(0,0,0,0.08)',
            }}
          >
            <SearchIcon
              sx={{
                color: darkFields ? 'rgba(255,255,255,0.35)' : 'rgba(0,0,0,0.4)',
                fontSize: 16,
                flexShrink: 0,
              }}
            />
            <Box
              component="input"
              autoFocus
              placeholder="Поиск skills по имени"
              value={searchQuery}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) => setSearchQuery(e.target.value)}
              sx={{
                flex: 1,
                bgcolor: 'transparent',
                border: 'none',
                outline: 'none',
                color: darkFields ? 'white' : 'rgba(0,0,0,0.87)',
                fontSize: '0.82rem',
                '&::placeholder': {
                  color: darkFields ? 'rgba(255,255,255,0.3)' : 'rgba(0,0,0,0.4)',
                },
              }}
            />
          </Box>

          <Box sx={{ maxHeight: 260, overflowY: 'auto', py: 0.5, ...SIDEBAR_HIDE_SCROLLBAR_SX }}>
            <Box
              onClick={() => selectSkill('new')}
              sx={{
                ...dropdownItemSx,
                ...getDropdownItemStateSx(darkFields, selectedSkillId === 'new'),
                fontStyle: 'italic',
                opacity: selectedSkillId === 'new' ? 1 : 0.7,
              }}
            >
              + Новый skill
            </Box>

            {loading ? (
              <Box sx={{ display: 'flex', justifyContent: 'center', py: 2 }}>
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
              filtered.map((skill) => {
                const title = skill.display_title || skill.name || skill.slug;
                return (
                  <Box
                    key={skill.id}
                    onClick={() => selectSkill(skill.id)}
                    sx={{
                      ...dropdownItemSx,
                      ...getDropdownItemStateSx(darkFields, selectedSkillId === skill.id),
                    }}
                  >
                    <Box component="span" sx={{ display: 'block' }}>
                      {title}
                    </Box>
                    <Box
                      component="span"
                      sx={{ display: 'block', opacity: 0.5, fontSize: '0.72rem' }}
                    >
                      ${skill.slug}
                    </Box>
                  </Box>
                );
              })
            )}
          </Box>
        </Popover>
      </Box>

      {selectedSkillId !== 'new' && !isOwner && (
        <Box
          sx={{
            mx: 2,
            mt: 1,
            px: 1.25,
            py: 0.75,
            borderRadius: 1,
            border: '1px solid',
            borderColor: readOnly ? 'rgba(100,181,246,0.35)' : 'rgba(102,187,106,0.35)',
            bgcolor: readOnly ? 'rgba(100,181,246,0.08)' : 'rgba(102,187,106,0.08)',
            flexShrink: 0,
          }}
        >
          <Typography sx={{ fontSize: '0.72rem', opacity: 0.85 }}>
            {readOnly
              ? 'Общий skill · роль «Зритель» — только просмотр и использование, изменение недоступно.'
              : 'Общий skill · роль «Редактор» — можно изменять; удаление и шаринг доступны только владельцу.'}
          </Typography>
        </Box>
      )}

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
        {loadingDetail ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}>
            <CircularProgress size={22} />
          </Box>
        ) : (
          <>
            <Box>
              <TextField
                value={form.name}
                onChange={(e) => {
                  const name = e.target.value;
                  setForm((f) => ({
                    ...f,
                    name,
                    display_title: selectedSkillId === 'new' ? name : f.display_title,
                    slug: selectedSkillId === 'new' ? slugifySkillName(name) : f.slug,
                  }));
                }}
                label="Название"
                placeholder="Введите имя skill"
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
              value={form.display_title}
              onChange={(e) => setForm((f) => ({ ...f, display_title: e.target.value }))}
              label="Заголовок в интерфейсе"
              placeholder="Как skill будет называться в UI"
              variant="outlined"
              size="small"
              fullWidth
              disabled={readOnly}
              sx={formFieldInputSx}
              inputProps={{ maxLength: 128 }}
            />

            <FieldWithHelp
              ariaLabel="Справка: служебное имя"
              help={
                <Box sx={{ maxWidth: 280 }}>
                  <Typography variant="subtitle2" fontWeight={600} sx={{ mb: 0.5 }}>
                    Служебное имя
                  </Typography>
                  <Typography variant="body2" sx={{ opacity: 0.95 }}>
                    Технический идентификатор skill для вызова в чате через $имя (например $my-skill).
                    Только латиница, цифры и символы . _ -. Сохраняется вместе со skill.
                  </Typography>
                </Box>
              }
            >
              <TextField
                value={form.slug}
                onChange={(e) => setForm((f) => ({ ...f, slug: slugifySkillName(e.target.value) }))}
                label="Служебное имя ($упоминание)"
                placeholder="my-skill"
                variant="outlined"
                size="small"
                fullWidth
                disabled={readOnly}
                sx={formFieldInputSx}
              />
            </FieldWithHelp>

            <TextField
              value={form.description}
              onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
              label="Описание (когда использовать)"
              placeholder="Когда применять этот skill"
              variant="outlined"
              size="small"
              fullWidth
              multiline
              minRows={3}
              maxRows={8}
              disabled={readOnly}
              sx={multilineFieldSx}
            />

            <TextField
              value={form.content}
              onChange={(e) => setForm((f) => ({ ...f, content: e.target.value }))}
              label="Текст / SKILL.md (markdown)"
              placeholder="Основной текст инструкций skill"
              variant="outlined"
              size="small"
              fullWidth
              multiline
              minRows={3}
              maxRows={8}
              disabled={readOnly}
              sx={multilineFieldSx}
            />

            <TextField
              value={form.category}
              onChange={(e) => setForm((f) => ({ ...f, category: e.target.value }))}
              label="Категория"
              placeholder="Необязательно"
              variant="outlined"
              size="small"
              fullWidth
              disabled={readOnly}
              sx={formFieldInputSx}
            />

            <FieldWithHelp
              ariaLabel="Справка: разрешённые инструменты"
              help={
                <Box sx={{ maxWidth: 300 }}>
                  <Typography variant="subtitle2" fontWeight={600} sx={{ mb: 0.5 }}>
                    Разрешённые инструменты
                  </Typography>
                  <Typography variant="body2" sx={{ opacity: 0.95 }}>
                    ID MCP-серверов и инструментов через запятую. Они подмешиваются к skill при
                    ручном вызове ($имя) или когда включено «Всегда применять» — модель сможет
                    пользоваться только указанным набором.
                  </Typography>
                </Box>
              }
            >
              <TextField
                value={form.allowed_tools}
                onChange={(e) => setForm((f) => ({ ...f, allowed_tools: e.target.value }))}
                label="Разрешённые инструменты (через запятую)"
                placeholder="tool-a, tool-b"
                variant="outlined"
                size="small"
                fullWidth
                disabled={readOnly}
                sx={formFieldInputSx}
              />
            </FieldWithHelp>

            <TextField
              value={form.tags}
              onChange={(e) => setForm((f) => ({ ...f, tags: e.target.value }))}
              label="Теги (через запятую)"
              placeholder="tag1, tag2"
              variant="outlined"
              size="small"
              fullWidth
              disabled={readOnly}
              sx={formFieldInputSx}
            />

            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.25 }}>
              <FormControlLabel
                control={
                  <Checkbox
                    size="small"
                    checked={form.is_public}
                    disabled={readOnly}
                    onChange={(e) => setForm((f) => ({ ...f, is_public: e.target.checked }))}
                    sx={checkboxSx}
                  />
                }
                label="Публичный (чтение для всех)"
                sx={checkLabelSx}
              />
              <FormControlLabel
                control={
                  <Checkbox
                    size="small"
                    checked={form.user_invocable}
                    disabled={readOnly}
                    onChange={(e) => setForm((f) => ({ ...f, user_invocable: e.target.checked }))}
                    sx={checkboxSx}
                  />
                }
                label="Вызов пользователем ($ в чате)"
                sx={checkLabelSx}
              />
              <FormControlLabel
                control={
                  <Checkbox
                    size="small"
                    checked={form.disable_model_invocation}
                    disabled={readOnly}
                    onChange={(e) =>
                      setForm((f) => ({ ...f, disable_model_invocation: e.target.checked }))
                    }
                    sx={checkboxSx}
                  />
                }
                label="Запретить вызов моделью"
                sx={checkLabelSx}
              />
              <FormControlLabel
                control={
                  <Checkbox
                    size="small"
                    checked={form.always_apply}
                    disabled={readOnly}
                    onChange={(e) => setForm((f) => ({ ...f, always_apply: e.target.checked }))}
                    sx={checkboxSx}
                  />
                }
                label="Всегда применять"
                sx={checkLabelSx}
              />
              <FormControlLabel
                control={
                  <Checkbox
                    size="small"
                    checked={form.is_active}
                    disabled={readOnly}
                    onChange={(e) => setForm((f) => ({ ...f, is_active: e.target.checked }))}
                    sx={checkboxSx}
                  />
                }
                label="Активен"
                sx={checkLabelSx}
              />
              {selectedSkillId !== 'new' && form.user_invocable !== false && !form.always_apply && (
                <FormControlLabel
                  control={
                    <Checkbox
                      size="small"
                      checked={chatActive}
                      onChange={() =>
                        toggleActiveSkill(
                          currentChatId,
                          form.slug,
                          !chatActive,
                          form.display_title || form.name,
                        )
                      }
                      sx={checkboxSx}
                    />
                  }
                  label="Использовать в чате"
                  sx={checkLabelSx}
                />
              )}
            </Box>

            {selectedSkillId !== 'new' && typeof selectedSkillId === 'number' && token && (
              <SkillFilesEditor
                skillId={selectedSkillId}
                token={token}
                canWrite={!readOnly}
              />
            )}
          </>
        )}
      </Box>

      <Box
        sx={{
          flexShrink: 0,
          px: 2,
          pb: 2,
          pt: 0.5,
          display: 'flex',
          flexDirection: 'column',
          gap: 1,
        }}
      >
        {saveError && (
          <Typography
            variant="caption"
            sx={{ color: '#ef5350', fontSize: '0.72rem', textAlign: 'center' }}
          >
            {saveError}
          </Typography>
        )}
        {saveSuccess && (
          <Box sx={{ textAlign: 'center' }}>
            <Typography
              variant="caption"
              sx={{ color: '#66bb6a', fontSize: '0.72rem', display: 'block' }}
            >
              Skill успешно сохранён
            </Typography>
            <Typography
              variant="caption"
              sx={{
                color: panelChrome.fgSubtle,
                fontSize: '0.65rem',
                display: 'block',
                mt: 0.25,
              }}
            >
              Данные сохранены в базу приложения (PostgreSQL, таблица skills). Skill отображается в
              списке выше — выберите его для редактирования или отметьте «Использовать в чате».
            </Typography>
          </Box>
        )}

        <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', width: '100%' }}>
          <Box component="span" sx={{ flex: '1 1 140px', minWidth: 0, display: 'flex' }}>
            <Button
              size="small"
              fullWidth
              startIcon={<ImportIcon />}
              disabled={busyImportExport}
              onClick={() => importRef.current?.click()}
              sx={footerNeutralActionBtnSx}
            >
              Импорт
            </Button>
          </Box>
          <Box component="span" sx={{ flex: '1 1 140px', minWidth: 0, display: 'flex' }}>
            <Button
              size="small"
              fullWidth
              startIcon={
                busyImportExport ? (
                  <CircularProgress size={12} sx={{ color: 'inherit' }} />
                ) : (
                  <ExportIcon />
                )
              }
              disabled={busyImportExport}
              onClick={() => void handleExport()}
              sx={footerNeutralActionBtnSx}
            >
              Экспорт
            </Button>
          </Box>
        </Box>

        {selectedSkillId !== 'new' && isOwner && (
          <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', width: '100%' }}>
            <Box component="span" sx={{ flex: '1 1 140px', minWidth: 0, display: 'flex' }}>
              <Button
                size="small"
                fullWidth
                startIcon={<ShareIcon />}
                onClick={() => setShareDialogOpen(true)}
                sx={footerNeutralActionBtnSx}
              >
                Поделиться скиллом
              </Button>
            </Box>
            <Box component="span" sx={{ flex: '1 1 140px', minWidth: 0, display: 'flex' }}>
              <Button
                size="small"
                fullWidth
                disabled={isPublishing}
                startIcon={
                  isPublishing ? (
                    <CircularProgress size={12} sx={{ color: 'inherit' }} />
                  ) : isPublic ? (
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
                  color: isPublic ? '#2e7d32' : panelChrome.fgMuted,
                  border: `1px solid ${isPublic ? 'rgba(46,125,50,0.45)' : panelChrome.buttonBorder.replace('1px solid ', '')}`,
                  bgcolor: isPublic ? 'rgba(46,125,50,0.1)' : 'transparent',
                  '&:hover': {
                    bgcolor: isPublic ? 'rgba(46,125,50,0.16)' : panelChrome.hoverBg,
                    color: isPublic ? '#2e7d32' : panelChrome.fgMuted,
                  },
                  '&:disabled': { color: panelChrome.fgSubtle },
                }}
              >
                {isPublic ? 'Снять с публикации' : 'Опубликовать в галерее'}
              </Button>
            </Box>
            <Box component="span" sx={{ flex: '1 1 140px', minWidth: 0, display: 'flex' }}>
              <Button
                size="small"
                fullWidth
                startIcon={<DeleteIcon />}
                onClick={() => void handleDelete()}
                sx={footerDeleteActionBtnSx}
              >
                Удалить skill
              </Button>
            </Box>
          </Box>
        )}

        {canEdit ? (
          <Button
            fullWidth
            variant="contained"
            startIcon={
              isSaving ? (
                <CircularProgress size={14} sx={{ color: 'white' }} />
              ) : (
                <SaveIcon />
              )
            }
            onClick={() => void handleSave()}
            disabled={isSaving || loadingDetail}
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
              '& .MuiButton-startIcon .MuiSvgIcon-root': {
                fontSize: '0.9rem',
              },
              '&:hover': { bgcolor: '#388e3c' },
              '&:disabled': { bgcolor: 'rgba(46,125,50,0.4)', color: 'rgba(255,255,255,0.5)' },
            }}
          >
            {isSaving ? 'Сохраняю...' : 'Сохранить'}
          </Button>
        ) : null}
      </Box>

      {selectedSkillId !== 'new' && (
        <ShareSkillDialog
          open={shareDialogOpen}
          onClose={() => setShareDialogOpen(false)}
          skillId={typeof selectedSkillId === 'number' ? selectedSkillId : 0}
          skillName={form.name || selectedSkill?.name || 'Skill'}
          isDarkMode={isDarkMode}
        />
      )}
    </Box>
  );
}
