import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Checkbox,
  CircularProgress,
  Container,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  Pagination,
  Snackbar,
  Stack,
  TextField,
  Typography,
} from '@mui/material';
import SkillIcon from '../icons/SkillsIcon';
import { useNavigate, Navigate, useSearchParams } from 'react-router-dom';
import { useTheme } from '@mui/material/styles';
import { getApiUrl, API_ENDPOINTS } from '../config/api';
import { useAuth } from '../contexts/AuthContext';
import { useAppActions, useAppContext } from '../contexts/AppContext';
import { formatAuthorLabel } from '../utils/formatAuthorLabel';
import SkillFilesEditor from '../components/skills/SkillFilesEditor';
import {
  GalleryEntityCard,
  GallerySearchBookmarksBar,
  type GalleryCardItem,
} from '../components/galleryCards';
import {
  slugifySkillName,
  formatSkillsApiDetail,
  notifySkillsChanged,
} from '../utils/skillsImportExport';
import { openSkillInSidebar } from '../utils/openSkillSidebarNav';
import { toggleActiveSkill } from '../utils/skillSelectionStorage';

interface SkillItem {
  id: number;
  slug: string;
  name: string;
  display_title?: string | null;
  description?: string | null;
  content?: string;
  meta?: { tags?: string[] };
  is_active: boolean;
  is_public: boolean;
  user_invocable?: boolean;
  disable_model_invocation?: boolean;
  always_apply?: boolean;
  allowed_tools?: string[];
  category?: string | null;
  file_count?: number;
  author_id: string;
  author_name: string;
  author_full_name?: string | null;
  created_at: string;
  updated_at: string;
  write_access?: boolean;
  is_shared_with_me?: boolean;
  views_count?: number;
  usage_count?: number;
  average_rating?: number;
  total_votes?: number;
  user_rating?: number | null;
  is_bookmarked?: boolean;
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
  allowed_tools: '' as string,
  category: '',
  tags: '' as string,
};

function toCardItem(skill: SkillItem): GalleryCardItem {
  const title = skill.display_title || skill.name;
  const preview =
    skill.description?.trim() ||
    (skill.content && skill.content.length > 160
      ? `${skill.content.slice(0, 160)}…`
      : skill.content) ||
    `$${skill.slug}`;
  const metaParts = [
    skill.slug ? `$${skill.slug}` : null,
    skill.category,
    skill.file_count ? `${skill.file_count} файл(ов)` : null,
  ].filter(Boolean);
  return {
    id: skill.id,
    title,
    authorName: formatAuthorLabel(skill.author_name, skill.author_full_name, skill.author_id),
    preview,
    metaLine: metaParts.join(' · ') || undefined,
    viewsCount: skill.views_count,
    usageCount: skill.usage_count,
    averageRating: skill.average_rating,
    totalVotes: skill.total_votes,
    userRating: skill.user_rating,
    isBookmarked: skill.is_bookmarked,
  };
}

export default function SkillsPage() {
  return <Navigate to="/gallery?tab=skills" replace />;
}

export function SkillsGalleryContent({ embedded = false }: { embedded?: boolean }) {
  const { token, user } = useAuth();
  const { showNotification } = useAppActions();
  const { state: appState } = useAppContext();
  const theme = useTheme();
  const isDarkMode = theme.palette.mode === 'dark';
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  const [items, setItems] = useState<SkillItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(1);
  const [query, setQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const [showBookmarks, setShowBookmarks] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<SkillItem | null>(null);
  const [form, setForm] = useState(emptyForm);
  const [saving, setSaving] = useState(false);
  const [snackbar, setSnackbar] = useState<{ open: boolean; message: string; severity: 'success' | 'error' }>({
    open: false,
    message: '',
    severity: 'success',
  });
  const [usingId, setUsingId] = useState<number | null>(null);

  const authHeaders = useCallback((): HeadersInit => {
    const h: HeadersInit = { 'Content-Type': 'application/json' };
    if (token) h.Authorization = `Bearer ${token}`;
    return h;
  }, [token]);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedQuery(query.trim()), 400);
    return () => clearTimeout(t);
  }, [query]);

  useEffect(() => {
    setPage(1);
  }, [debouncedQuery, showBookmarks]);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    try {
      let url: string;
      if (showBookmarks) {
        const params = new URLSearchParams({ page: String(page), limit: '12' });
        url = `${getApiUrl(API_ENDPOINTS.SKILLS)}/my/bookmarks?${params}`;
      } else {
        const params = new URLSearchParams({
          page: String(page),
          limit: '12',
          view_option: 'public',
        });
        if (debouncedQuery) params.set('query', debouncedQuery);
        url = `${getApiUrl(API_ENDPOINTS.SKILLS)}/list?${params}`;
      }
      const resp = await fetch(url, { headers: authHeaders() });
      if (!resp.ok) throw new Error('Не удалось загрузить skills');
      const data = await resp.json();
      setItems(data.items || []);
      setPages(data.pages || 1);
    } catch (e) {
      setSnackbar({
        open: true,
        message: e instanceof Error ? e.message : 'Ошибка загрузки',
        severity: 'error',
      });
    } finally {
      setLoading(false);
    }
  }, [token, page, debouncedQuery, showBookmarks, authHeaders]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const onChanged = () => {
      void load();
    };
    window.addEventListener('astrachatSkillsChanged', onChanged);
    return () => window.removeEventListener('astrachatSkillsChanged', onChanged);
  }, [load]);

  useEffect(() => {
    try {
      const raw = sessionStorage.getItem('skills_md_import_draft');
      if (!raw) return;
      sessionStorage.removeItem('skills_md_import_draft');
      const draft = JSON.parse(raw) as {
        name?: string;
        slug?: string;
        description?: string;
        content?: string;
      };
      setEditing(null);
      setForm({
        ...emptyForm,
        name: draft.name || '',
        slug: draft.slug || '',
        description: draft.description || '',
        content: draft.content || '',
      });
      setFormOpen(true);
    } catch {
      /* ignore */
    }
  }, []);

  const openCreate = useCallback(() => {
    setEditing(null);
    setForm(emptyForm);
    setFormOpen(true);
  }, []);

  useEffect(() => {
    if (searchParams.get('create') !== '1') return;
    openCreate();
    const params = new URLSearchParams(searchParams);
    params.delete('create');
    setSearchParams(params, { replace: true });
  }, [searchParams, setSearchParams, openCreate]);

  const handleSave = async () => {
    if (!form.name.trim() || !form.content.trim()) {
      setSnackbar({ open: true, message: 'Укажите имя и содержимое', severity: 'error' });
      return;
    }
    setSaving(true);
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
      const url = editing
        ? `${getApiUrl(API_ENDPOINTS.SKILLS)}/${editing.id}`
        : `${getApiUrl(API_ENDPOINTS.SKILLS)}/create`;
      const resp = await fetch(url, {
        method: editing ? 'PUT' : 'POST',
        headers: authHeaders(),
        body: JSON.stringify(payload),
      });
      const j = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        throw new Error(formatSkillsApiDetail(j.detail, 'Не удалось сохранить'));
      }
      setFormOpen(false);
      setSnackbar({ open: true, message: editing ? 'Skill обновлён' : 'Skill создан', severity: 'success' });
      notifySkillsChanged();
      await load();
    } catch (e) {
      setSnackbar({
        open: true,
        message: e instanceof Error ? e.message : 'Ошибка',
        severity: 'error',
      });
    } finally {
      setSaving(false);
    }
  };

  const canWrite = (s: SkillItem) =>
    Boolean(s.write_access) ||
    (user?.user_id || '').toLowerCase() === (s.author_id || '').toLowerCase() ||
    Boolean(user?.is_admin);

  const handleToggleBookmark = async (skill: SkillItem) => {
    if (!token) {
      showNotification('error', 'Для закладок нужно войти в систему');
      return;
    }
    try {
      const method = skill.is_bookmarked ? 'DELETE' : 'POST';
      const resp = await fetch(`${getApiUrl(API_ENDPOINTS.SKILLS)}/${skill.id}/bookmark`, {
        method,
        headers: authHeaders(),
      });
      if (!resp.ok) {
        const j = await resp.json().catch(() => ({}));
        throw new Error(formatSkillsApiDetail(j.detail, 'Не удалось изменить закладку'));
      }
      showNotification(
        'success',
        skill.is_bookmarked ? 'Удалено из закладок' : 'Добавлено в закладки',
      );
      if (showBookmarks && skill.is_bookmarked) {
        setItems((prev) => prev.filter((s) => s.id !== skill.id));
      } else {
        setItems((prev) =>
          prev.map((s) =>
            s.id === skill.id ? { ...s, is_bookmarked: !s.is_bookmarked } : s,
          ),
        );
      }
    } catch (e: unknown) {
      showNotification('error', e instanceof Error ? e.message : 'Ошибка закладки');
    }
  };

  const handleRate = async (skillId: number, rating: number) => {
    if (!token) {
      showNotification('error', 'Для оценки нужно войти в систему');
      return;
    }
    try {
      const resp = await fetch(`${getApiUrl(API_ENDPOINTS.SKILLS)}/${skillId}/rate`, {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify({ rating }),
      });
      if (!resp.ok) {
        const j = await resp.json().catch(() => ({}));
        throw new Error(formatSkillsApiDetail(j.detail, 'Не удалось сохранить оценку'));
      }
      showNotification('success', 'Оценка сохранена');
      await load();
    } catch (e: unknown) {
      showNotification('error', e instanceof Error ? e.message : 'Ошибка оценки');
    }
  };

  const handleUse = async (skill: SkillItem) => {
    if (!token) {
      setSnackbar({ open: true, message: 'Для использования нужно войти в систему', severity: 'error' });
      return;
    }
    setUsingId(skill.id);
    try {
      await fetch(`${getApiUrl(API_ENDPOINTS.SKILLS)}/${skill.id}/use`, {
        method: 'POST',
        headers: authHeaders(),
      });
      if (!skill.is_bookmarked) {
        await fetch(`${getApiUrl(API_ENDPOINTS.SKILLS)}/${skill.id}/bookmark`, {
          method: 'POST',
          headers: authHeaders(),
        });
        setItems((prev) =>
          prev.map((s) => (s.id === skill.id ? { ...s, is_bookmarked: true } : s)),
        );
      }
      if (skill.user_invocable !== false && !skill.always_apply) {
        toggleActiveSkill(
          appState.currentChatId,
          skill.slug,
          true,
          skill.display_title || skill.name,
        );
      }
      setSnackbar({
        open: true,
        message: `Skill «${skill.display_title || skill.name}» добавлен в чат. Переходим…`,
        severity: 'success',
      });
      setTimeout(() => navigate('/'), 600);
    } finally {
      setUsingId(null);
    }
  };

  const handleOpen = (skill: SkillItem) => {
    void fetch(`${getApiUrl(API_ENDPOINTS.SKILLS)}/${skill.id}/view`, {
      method: 'POST',
      headers: authHeaders(),
    }).catch(() => undefined);
    openSkillInSidebar(skill.id, navigate);
  };

  return (
    <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      {!embedded && (
        <Box sx={{ py: 2 }}>
          <Container maxWidth="xl">
            <Box sx={{ textAlign: 'center' }}>
              <Box
                sx={{
                  display: 'flex',
                  flexDirection: 'row',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: 1.5,
                  mb: 0.5,
                  flexWrap: 'wrap',
                }}
              >
                <SkillIcon color="primary" />
                <Typography variant="h4" fontWeight="bold">
                  Галерея Skills
                </Typography>
                <Button size="small" onClick={() => navigate('/')}>
                  К чату
                </Button>
              </Box>
              <Typography variant="body2" color="text.secondary">
                Нажмите на карточку, чтобы открыть skill в панели справа · «Использовать» — добавить в чат
              </Typography>
            </Box>
          </Container>
        </Box>
      )}
      {embedded && (
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5, textAlign: 'center' }}>
          Нажмите на карточку, чтобы открыть skill в панели справа · «Использовать» — добавить в чат
        </Typography>
      )}

      <Box sx={{ py: embedded ? 0 : 2, pb: embedded ? 1.5 : undefined }}>
        <Container maxWidth="xl">
          <GallerySearchBookmarksBar
            searchQuery={query}
            onSearchChange={setQuery}
            searchPlaceholder="Поиск skills…"
            showBookmarks={showBookmarks}
            onToggleBookmarks={() => setShowBookmarks((v) => !v)}
            bookmarksEnabled={Boolean(token)}
            allLabel="Все skills"
            bookmarksLabel="Закладки"
          />
        </Container>
      </Box>

      <Container maxWidth="xl" sx={{ flex: 1, overflowY: 'auto', py: 3 }}>
        {loading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 8 }}>
            <CircularProgress />
          </Box>
        ) : items.length === 0 ? (
          <Alert severity="info">
            {showBookmarks
              ? 'В закладках пока нет skills. Добавьте skill кнопкой-закладкой на карточке.'
              : 'Публичных skills пока нет. Опубликуйте skill из панели Skills справа.'}
          </Alert>
        ) : (
          <Box
            sx={{
              display: 'grid',
              gridTemplateColumns: { xs: '1fr', md: 'repeat(2, 1fr)', lg: 'repeat(3, 1fr)' },
              gap: 3,
            }}
          >
            {items.map((skill) => (
              <GalleryEntityCard
                key={skill.id}
                item={toCardItem(skill)}
                isDarkMode={isDarkMode}
                using={usingId === skill.id}
                onOpen={() => handleOpen(skill)}
                onRate={(rating) => void handleRate(skill.id, rating)}
                onUse={() => void handleUse(skill)}
                onToggleBookmark={() => void handleToggleBookmark(skill)}
              />
            ))}
          </Box>
        )}

        {pages > 1 && (
          <Box sx={{ display: 'flex', justifyContent: 'center', mt: 4 }}>
            <Pagination
              page={page}
              count={pages}
              onChange={(_, p) => setPage(p)}
              color="primary"
            />
          </Box>
        )}
      </Container>

      <Dialog open={formOpen} onClose={() => setFormOpen(false)} fullWidth maxWidth="md">
        <DialogTitle>{editing ? 'Редактировать skill' : 'Новый skill'}</DialogTitle>
        <DialogContent>
          <Stack gap={2} mt={1}>
            <TextField
              label="Название"
              value={form.name}
              onChange={(e) => {
                const name = e.target.value;
                setForm((f) => ({
                  ...f,
                  name,
                  display_title: editing ? f.display_title : name,
                  slug: editing ? f.slug : slugifySkillName(name),
                }));
              }}
              fullWidth
            />
            <TextField
              label="Заголовок в интерфейсе"
              value={form.display_title}
              onChange={(e) => setForm((f) => ({ ...f, display_title: e.target.value }))}
              fullWidth
            />
            <TextField
              label="Служебное имя ($упоминание)"
              value={form.slug}
              onChange={(e) => setForm((f) => ({ ...f, slug: slugifySkillName(e.target.value) }))}
              helperText="В чате: $имя"
              fullWidth
            />
            <TextField
              label="Описание (когда использовать)"
              value={form.description}
              onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
              fullWidth
              multiline
              minRows={2}
            />
            <TextField
              label="Текст / SKILL.md (markdown)"
              value={form.content}
              onChange={(e) => setForm((f) => ({ ...f, content: e.target.value }))}
              fullWidth
              multiline
              minRows={10}
            />
            <TextField
              label="Категория"
              value={form.category}
              onChange={(e) => setForm((f) => ({ ...f, category: e.target.value }))}
              fullWidth
            />
            <TextField
              label="Разрешённые инструменты (через запятую)"
              value={form.allowed_tools}
              onChange={(e) => setForm((f) => ({ ...f, allowed_tools: e.target.value }))}
              fullWidth
            />
            <TextField
              label="Теги (через запятую)"
              value={form.tags}
              onChange={(e) => setForm((f) => ({ ...f, tags: e.target.value }))}
              fullWidth
            />
            <FormControlLabel
              control={
                <Checkbox
                  checked={form.is_public}
                  onChange={(e) => setForm((f) => ({ ...f, is_public: e.target.checked }))}
                />
              }
              label="Публичный (чтение для всех)"
            />
            <FormControlLabel
              control={
                <Checkbox
                  checked={form.user_invocable}
                  onChange={(e) => setForm((f) => ({ ...f, user_invocable: e.target.checked }))}
                />
              }
              label="Вызов пользователем ($ в чате)"
            />
            <FormControlLabel
              control={
                <Checkbox
                  checked={form.disable_model_invocation}
                  onChange={(e) => setForm((f) => ({ ...f, disable_model_invocation: e.target.checked }))}
                />
              }
              label="Запретить вызов моделью"
            />
            <FormControlLabel
              control={
                <Checkbox
                  checked={form.always_apply}
                  onChange={(e) => setForm((f) => ({ ...f, always_apply: e.target.checked }))}
                />
              }
              label="Всегда применять"
            />
            <FormControlLabel
              control={
                <Checkbox
                  checked={form.is_active}
                  onChange={(e) => setForm((f) => ({ ...f, is_active: e.target.checked }))}
                />
              }
              label="Активен"
            />
            {editing && (
              <SkillFilesEditor skillId={editing.id} token={token || ''} canWrite={canWrite(editing)} />
            )}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setFormOpen(false)}>Отмена</Button>
          <Button variant="contained" disabled={saving} onClick={() => void handleSave()}>
            Сохранить
          </Button>
        </DialogActions>
      </Dialog>

      <Snackbar
        open={snackbar.open}
        autoHideDuration={4000}
        onClose={() => setSnackbar((s) => ({ ...s, open: false }))}
      >
        <Alert severity={snackbar.severity} onClose={() => setSnackbar((s) => ({ ...s, open: false }))}>
          {snackbar.message}
        </Alert>
      </Snackbar>
    </Box>
  );
}
