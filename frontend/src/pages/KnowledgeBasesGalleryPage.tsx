import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Box,
  CircularProgress,
  Container,
  Pagination,
  Snackbar,
  Typography,
  useTheme,
} from '@mui/material';
import MenuBookOutlinedIcon from '@mui/icons-material/MenuBookOutlined';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { useAppActions, useAppContext } from '../contexts/AppContext';
import { authFetch, getApiUrl, API_ENDPOINTS } from '../config/api';
import {
  GalleryEntityCard,
  GallerySearchBookmarksBar,
} from '../components/galleryCards';
import type { GalleryCardItem } from '../components/galleryCards/types';
import { openKnowledgeBaseInSidebar } from '../utils/openKnowledgeBaseSidebarNav';
import {
  getActiveKnowledgeBaseRefs,
  setActiveKnowledgeBaseRefs,
} from '../utils/knowledgeBaseSelectionStorage';

interface KbGalleryItem {
  id: number;
  name: string;
  description?: string | null;
  document_count?: number;
  author_id: string;
  author_name?: string;
  author_full_name?: string | null;
  is_public?: boolean;
  views_count?: number;
  usage_count?: number;
  average_rating?: number;
  total_votes?: number;
  user_rating?: number | null;
  is_bookmarked?: boolean;
}

function toCardItem(kb: KbGalleryItem): GalleryCardItem {
  const docs = Number(kb.document_count) || 0;
  return {
    id: kb.id,
    title: kb.name,
    authorName: kb.author_full_name || kb.author_name || kb.author_id || '—',
    preview: (kb.description || '').trim() || 'Без описания',
    metaLine: docs === 1 ? '1 документ' : `Документов: ${docs}`,
    viewsCount: kb.views_count || 0,
    usageCount: kb.usage_count || 0,
    averageRating: kb.average_rating || 0,
    totalVotes: kb.total_votes || 0,
    userRating: kb.user_rating ?? null,
    isBookmarked: Boolean(kb.is_bookmarked),
  };
}

export function KnowledgeBasesGalleryContent({
  embedded = false,
}: {
  embedded?: boolean;
}) {
  const { token } = useAuth();
  const { showNotification } = useAppActions();
  const { state: appState } = useAppContext();
  const theme = useTheme();
  const isDarkMode = theme.palette.mode === 'dark';
  const navigate = useNavigate();

  const [items, setItems] = useState<KbGalleryItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(1);
  const [query, setQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const [showBookmarks, setShowBookmarks] = useState(false);
  const [usingId, setUsingId] = useState<number | null>(null);
  const [cloningId, setCloningId] = useState<number | null>(null);
  const [snackbar, setSnackbar] = useState<{
    open: boolean;
    message: string;
    severity: 'success' | 'error';
  }>({ open: false, message: '', severity: 'success' });

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
        url = `${getApiUrl(API_ENDPOINTS.KNOWLEDGE_BASES)}/my/bookmarks?${params}`;
      } else {
        const params = new URLSearchParams({
          page: String(page),
          limit: '12',
          view_option: 'public',
        });
        if (debouncedQuery) params.set('query', debouncedQuery);
        url = `${getApiUrl(API_ENDPOINTS.KNOWLEDGE_BASES)}/list?${params}`;
      }
      const resp = await authFetch(url, { headers: authHeaders() });
      if (!resp.ok) throw new Error('Не удалось загрузить базы знаний');
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

  const handleToggleBookmark = async (kb: KbGalleryItem) => {
    if (!token) {
      showNotification('error', 'Для закладок нужно войти в систему');
      return;
    }
    try {
      const method = kb.is_bookmarked ? 'DELETE' : 'POST';
      const resp = await authFetch(
        `${getApiUrl(API_ENDPOINTS.KNOWLEDGE_BASES)}/${kb.id}/bookmark`,
        { method, headers: authHeaders() },
      );
      if (!resp.ok) {
        const j = await resp.json().catch(() => ({}));
        throw new Error(
          typeof j.detail === 'string' ? j.detail : 'Не удалось изменить закладку',
        );
      }
      showNotification(
        'success',
        kb.is_bookmarked ? 'Удалено из закладок' : 'Добавлено в закладки',
      );
      if (showBookmarks && kb.is_bookmarked) {
        setItems((prev) => prev.filter((s) => s.id !== kb.id));
      } else {
        setItems((prev) =>
          prev.map((s) =>
            s.id === kb.id ? { ...s, is_bookmarked: !s.is_bookmarked } : s,
          ),
        );
      }
    } catch (e: unknown) {
      showNotification('error', e instanceof Error ? e.message : 'Ошибка закладки');
    }
  };

  const handleRate = async (kbId: number, rating: number) => {
    if (!token) {
      showNotification('error', 'Для оценки нужно войти в систему');
      return;
    }
    try {
      const resp = await authFetch(
        `${getApiUrl(API_ENDPOINTS.KNOWLEDGE_BASES)}/${kbId}/rate`,
        {
          method: 'POST',
          headers: authHeaders(),
          body: JSON.stringify({ rating }),
        },
      );
      if (!resp.ok) {
        const j = await resp.json().catch(() => ({}));
        throw new Error(
          typeof j.detail === 'string' ? j.detail : 'Не удалось сохранить оценку',
        );
      }
      showNotification('success', 'Оценка сохранена');
      await load();
    } catch (e: unknown) {
      showNotification('error', e instanceof Error ? e.message : 'Ошибка оценки');
    }
  };

  const handleOpen = (kb: KbGalleryItem) => {
    void authFetch(`${getApiUrl(API_ENDPOINTS.KNOWLEDGE_BASES)}/${kb.id}/view`, {
      method: 'POST',
      headers: authHeaders(),
    }).catch(() => undefined);
    openKnowledgeBaseInSidebar(kb.id, navigate);
  };

  const handleUse = async (kb: KbGalleryItem) => {
    if (!token) {
      showNotification('error', 'Для использования нужно войти в систему');
      return;
    }
    const chatId = appState.currentChatId;
    if (!chatId) {
      showNotification('error', 'Откройте чат, чтобы подключить базу знаний');
      return;
    }
    setUsingId(kb.id);
    try {
      await authFetch(`${getApiUrl(API_ENDPOINTS.KNOWLEDGE_BASES)}/${kb.id}/use`, {
        method: 'POST',
        headers: authHeaders(),
      }).catch(() => undefined);
      if (!kb.is_bookmarked) {
        await authFetch(`${getApiUrl(API_ENDPOINTS.KNOWLEDGE_BASES)}/${kb.id}/bookmark`, {
          method: 'POST',
          headers: authHeaders(),
        }).catch(() => undefined);
        setItems((prev) =>
          prev.map((s) => (s.id === kb.id ? { ...s, is_bookmarked: true } : s)),
        );
      }
      const current = getActiveKnowledgeBaseRefs(chatId);
      if (!current.some((r) => r.id === kb.id)) {
        setActiveKnowledgeBaseRefs(chatId, [...current, { id: kb.id, name: kb.name }]);
      }
      setSnackbar({
        open: true,
        message: `База «${kb.name}» добавлена в чат. Переходим…`,
        severity: 'success',
      });
      setTimeout(() => navigate('/'), 600);
    } finally {
      setUsingId(null);
    }
  };

  const handleClone = async (kb: KbGalleryItem) => {
    if (!token) {
      showNotification('error', 'Для клонирования нужно войти в систему');
      return;
    }
    if (
      !window.confirm(
        `Клонировать базу знаний «${kb.name}» со всеми документами и настройками РАГ? Оригинал не изменится.`,
      )
    ) {
      return;
    }
    setCloningId(kb.id);
    try {
      const resp = await authFetch(
        `${getApiUrl(API_ENDPOINTS.KNOWLEDGE_BASES)}/${kb.id}/duplicate`,
        { method: 'POST', headers: authHeaders() },
      );
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        throw new Error(
          typeof data.detail === 'string'
            ? data.detail
            : 'Не удалось клонировать базу знаний',
        );
      }
      const newId = Number(data.knowledge_base_id);
      const docs = Number(data.cloned_documents) || 0;
      showNotification(
        'success',
        docs > 0
          ? `База склонирована (документов: ${docs})`
          : 'База знаний склонирована',
      );
      if (Number.isFinite(newId) && newId > 0) {
        openKnowledgeBaseInSidebar(newId, navigate);
      }
    } catch (e: unknown) {
      showNotification(
        'error',
        e instanceof Error ? e.message : 'Ошибка клонирования базы знаний',
      );
    } finally {
      setCloningId(null);
    }
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
                <MenuBookOutlinedIcon color="primary" />
                <Typography variant="h4" fontWeight="bold">
                  Галерея баз знаний
                </Typography>
              </Box>
              <Typography variant="body2" color="text.secondary">
                «Использовать» — подключить к текущему чату · «Клонировать» — полная копия себе
              </Typography>
            </Box>
          </Container>
        </Box>
      )}
      {embedded && (
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5, textAlign: 'center' }}>
          «Использовать» — подключить к текущему чату · «Клонировать» — полная копия себе
        </Typography>
      )}

      <Box sx={{ py: embedded ? 0 : 2, pb: embedded ? 1.5 : undefined }}>
        <Container maxWidth="xl">
          <GallerySearchBookmarksBar
            searchQuery={query}
            onSearchChange={setQuery}
            searchPlaceholder="Поиск баз знаний…"
            showBookmarks={showBookmarks}
            onToggleBookmarks={() => setShowBookmarks((v) => !v)}
            bookmarksEnabled={Boolean(token)}
            allLabel="Все базы"
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
              ? 'В закладках пока нет баз знаний. Добавьте базу кнопкой-закладкой на карточке.'
              : 'Публичных баз знаний пока нет. Опубликуйте базу из панели справа.'}
          </Alert>
        ) : (
          <Box
            sx={{
              display: 'grid',
              gridTemplateColumns: { xs: '1fr', md: 'repeat(2, 1fr)', lg: 'repeat(3, 1fr)' },
              gap: 3,
            }}
          >
            {items.map((kb) => (
              <GalleryEntityCard
                key={kb.id}
                item={toCardItem(kb)}
                isDarkMode={isDarkMode}
                using={usingId === kb.id}
                cloning={cloningId === kb.id}
                useLabel="Использовать"
                cloneLabel="Клонировать"
                onOpen={() => handleOpen(kb)}
                onUse={() => void handleUse(kb)}
                onClone={() => void handleClone(kb)}
                onRate={(rating) => void handleRate(kb.id, rating)}
                onToggleBookmark={() => void handleToggleBookmark(kb)}
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

      <Snackbar
        open={snackbar.open}
        autoHideDuration={4000}
        onClose={() => setSnackbar((s) => ({ ...s, open: false }))}
        message={snackbar.message}
      />
    </Box>
  );
}

export default function KnowledgeBasesGalleryPage() {
  return <KnowledgeBasesGalleryContent />;
}
