import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Container,
  Pagination,
  Typography,
} from '@mui/material';
import AgentIcon from '../icons/AgentIcon';
import { useTheme } from '@mui/material/styles';
import { useNavigate, Navigate } from 'react-router-dom';
import { authFetch, getApiUrl } from '../config/api';
import { useAuth } from '../contexts/AuthContext';
import { useAppActions } from '../contexts/AppContext';
import { loadAgentModelOnly } from '../utils/applyAgentServer';
import { persistAgentMcpConfig } from '../utils/applyAgentMcp';
import { persistAgentArtifactsEnabled } from '../utils/agentArtifactsEnabled';
import { openAgentInConstructor } from '../utils/openAgentConstructorNav';
import { cloneAgentDeep } from '../utils/cloneAgentDeep';
import {
  GalleryEntityCard,
  GallerySearchBookmarksBar,
  type GalleryCardItem,
} from '../components/galleryCards';
import { formatAuthorLabel } from '../utils/formatAuthorLabel';
import { useFeatureAccess } from '../hooks/useFeatureAccess';

interface GalleryAgent {
  id: number;
  name: string;
  description?: string;
  system_prompt: string;
  config?: Record<string, unknown>;
  tools?: string[];
  author_id: string;
  author_name: string;
  author_full_name?: string | null;
  is_public: boolean;
  usage_count: number;
  views_count: number;
  average_rating: number;
  total_votes: number;
  user_rating?: number | null;
  is_bookmarked?: boolean;
}

const STORAGE_AGENT_ID = 'active_agent_id';
const STORAGE_AGENT_NAME = 'active_agent_name';
const STORAGE_AGENT_PROMPT = 'active_agent_prompt';

function toCardItem(agent: GalleryAgent): GalleryCardItem {
  const preview =
    agent.description?.trim() ||
    (agent.system_prompt.length > 160
      ? `${agent.system_prompt.slice(0, 160)}…`
      : agent.system_prompt);
  return {
    id: agent.id,
    title: agent.name,
    authorName: formatAuthorLabel(agent.author_name, agent.author_full_name, agent.author_id),
    preview,
    viewsCount: agent.views_count,
    usageCount: agent.usage_count,
    averageRating: agent.average_rating,
    totalVotes: agent.total_votes,
    userRating: agent.user_rating,
    isBookmarked: agent.is_bookmarked,
  };
}

export default function AgentGalleryPage() {
  return <Navigate to="/gallery?tab=agents" replace />;
}

export function AgentGalleryContent({ embedded = false }: { embedded?: boolean }) {
  const { token } = useAuth();
  const { canUseAgents } = useFeatureAccess();
  const { showNotification } = useAppActions();
  const theme = useTheme();
  const isDarkMode = theme.palette.mode === 'dark';
  const navigate = useNavigate();

  const [agents, setAgents] = useState<GalleryAgent[]>([]);
  const [loading, setLoading] = useState(false);
  const [totalPages, setTotalPages] = useState(1);
  const [searchQuery, setSearchQuery] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [showBookmarks, setShowBookmarks] = useState(false);
  // BUG-FE-66: номер страницы привязан к текущим фильтрам; при смене поиска/закладок страница
  // становится 1 сразу в том же рендере, и запрос не уходит со старым page.
  const filtersKey = `${debouncedSearch}\u0000${showBookmarks ? 1 : 0}`;
  const [pageState, setPageState] = useState({ key: filtersKey, page: 1 });
  const page = pageState.key === filtersKey ? pageState.page : 1;
  const setPage = (next: number) => setPageState({ key: filtersKey, page: next });
  const [usingId, setUsingId] = useState<number | null>(null);
  const [cloningId, setCloningId] = useState<number | null>(null);
  // Актуальный запрос: предыдущий отменяется, а его устаревший ответ игнорируется.
  const requestSeqRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(searchQuery.trim()), 400);
    return () => clearTimeout(t);
  }, [searchQuery]);

  useEffect(
    () => () => {
      abortRef.current?.abort();
    },
    [],
  );

  const loadAgents = useCallback(async () => {
    if (!canUseAgents) {
      abortRef.current?.abort();
      requestSeqRef.current += 1;
      setAgents([]);
      setTotalPages(0);
      setLoading(false);
      return;
    }
    // Новый запрос отменяет предыдущий (смена поиска/страницы во время загрузки).
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const seq = ++requestSeqRef.current;
    const isStale = () => seq !== requestSeqRef.current;
    setLoading(true);
    try {
      const headers: HeadersInit = {};
      if (token) headers.Authorization = `Bearer ${token}`;

      let url: string;
      if (showBookmarks) {
        if (!token) {
          setAgents([]);
          setTotalPages(0);
          return;
        }
        const params = new URLSearchParams({ page: String(page), limit: '20' });
        url = `${getApiUrl('/api/agents/my/bookmarks')}?${params}`;
      } else {
        const params = new URLSearchParams({
          page: String(page),
          limit: '20',
          sort_by: 'rating',
          sort_order: 'desc',
        });
        if (debouncedSearch) params.set('search', debouncedSearch);
        url = `${getApiUrl('/api/agents/')}?${params}`;
      }

      const resp = await authFetch(url, { headers, signal: controller.signal });
      if (isStale()) return;
      if (!resp.ok) {
        setAgents([]);
        setTotalPages(0);
        return;
      }
      const data = await resp.json();
      if (isStale()) return;
      setAgents(data.agents || []);
      setTotalPages(data.pages || 1);
    } catch {
      if (isStale()) return;
      setAgents([]);
      setTotalPages(0);
    } finally {
      if (!isStale()) setLoading(false);
    }
  }, [page, debouncedSearch, token, showBookmarks, canUseAgents]);

  useEffect(() => {
    void loadAgents();
  }, [loadAgents]);

  const handleToggleBookmark = async (agent: GalleryAgent) => {
    if (!token) {
      showNotification('error', 'Для закладок нужно войти в систему');
      return;
    }
    try {
      const method = agent.is_bookmarked ? 'DELETE' : 'POST';
      const resp = await authFetch(getApiUrl(`/api/agents/${agent.id}/bookmark`), {
        method,
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || 'Не удалось изменить закладку');
      }
      showNotification(
        'success',
        agent.is_bookmarked ? 'Удалено из закладок' : 'Добавлено в закладки',
      );
      if (showBookmarks && agent.is_bookmarked) {
        setAgents((prev) => prev.filter((a) => a.id !== agent.id));
      } else {
        setAgents((prev) =>
          prev.map((a) =>
            a.id === agent.id ? { ...a, is_bookmarked: !a.is_bookmarked } : a,
          ),
        );
      }
    } catch (e: unknown) {
      showNotification('error', e instanceof Error ? e.message : 'Ошибка закладки');
    }
  };

  const handleRate = async (agentId: number, rating: number) => {
    if (!token) {
      showNotification('error', 'Для оценки нужно войти в систему');
      return;
    }
    try {
      const resp = await authFetch(getApiUrl(`/api/agents/${agentId}/rate`), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ rating }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || 'Не удалось сохранить оценку');
      }
      showNotification('success', 'Оценка сохранена');
      await loadAgents();
    } catch (e: unknown) {
      showNotification('error', e instanceof Error ? e.message : 'Ошибка оценки');
    }
  };

  const handleClone = async (agent: GalleryAgent) => {
    if (!token) {
      showNotification('error', 'Для клонирования агента нужно войти в систему');
      return;
    }
    if (
      !window.confirm(
        `Клонировать агента «${agent.name}» со всеми настройками, файлами и полными копиями агентов из цепочки/субагентов? Оригиналы не изменятся.`,
      )
    ) {
      return;
    }
    setCloningId(agent.id);
    try {
      // Тот же deep-clone API, что и в конструкторе: родитель + цепочка + субагенты.
      const result = await cloneAgentDeep(agent.id, token);
      showNotification(
        'success',
        result.clonedAgents > 1
          ? `Склонировано агентов: ${result.clonedAgents} (родитель + цепочка/субагенты)`
          : 'Агент полностью склонирован',
      );
      openAgentInConstructor(result.agentId, navigate, {
        relatedAgentIds: result.clonedAgentIds,
      });
    } catch (e: unknown) {
      showNotification('error', e instanceof Error ? e.message : 'Ошибка клонирования агента');
    } finally {
      setCloningId(null);
    }
  };

  const handleUse = async (agent: GalleryAgent) => {
    if (!token) {
      showNotification('error', 'Для использования агента нужно войти в систему');
      return;
    }
    setUsingId(agent.id);
    try {
      const headers: HeadersInit = { Authorization: `Bearer ${token}` };

      if (!agent.is_bookmarked) {
        await authFetch(getApiUrl(`/api/agents/${agent.id}/bookmark`), {
          method: 'POST',
          headers,
        });
        setAgents((prev) =>
          prev.map((a) => (a.id === agent.id ? { ...a, is_bookmarked: true } : a)),
        );
      }

      await authFetch(getApiUrl(`/api/agents/${agent.id}/use`), {
        method: 'POST',
        headers,
      });

      let full = agent;
      try {
        const detailResp = await authFetch(getApiUrl(`/api/agents/${agent.id}`), { headers });
        if (detailResp.ok) {
          full = { ...agent, ...(await detailResp.json()) };
        }
      } catch {
        /* */
      }

      const cfg = (full.config || {}) as Record<string, unknown>;
      const modelPath = String(cfg.model || cfg.model_path || '')
        .trim()
        .replace(/^1lm-svc:\/\//i, 'llm-svc://')
        .replace(/\s+/g, '');

      const applied = await loadAgentModelOnly(token, modelPath || null);

      localStorage.setItem(STORAGE_AGENT_ID, String(full.id));
      localStorage.setItem(STORAGE_AGENT_NAME, full.name);
      localStorage.setItem(STORAGE_AGENT_PROMPT, full.system_prompt || '');
      persistAgentMcpConfig(cfg);
      persistAgentArtifactsEnabled(cfg);
      window.dispatchEvent(new CustomEvent('agentSelected', { detail: full }));

      if (!applied.ok) {
        showNotification(
          'warning',
          `Агент «${full.name}» добавлен в «Агенты из галереи», но настройки не применились: ${applied.message}`,
        );
      } else if ('pending' in applied && applied.pending) {
        showNotification(
          'info',
          `Агент «${full.name}» добавлен. Модель подтянется при первом сообщении. Переходим…`,
        );
      } else {
        showNotification(
          'success',
          `Агент «${full.name}» добавлен. Промпт и настройки применятся в чате. Переходим…`,
        );
      }

      setTimeout(() => navigate('/'), 800);
    } catch (e: unknown) {
      showNotification('error', e instanceof Error ? e.message : 'Ошибка использования агента');
    } finally {
      setUsingId(null);
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
                <AgentIcon color="primary" />
                <Typography variant="h4" fontWeight="bold">
                  Галерея агентов
                </Typography>
                <Button size="small" onClick={() => navigate('/')}>
                  К чату
                </Button>
              </Box>
              <Typography variant="body2" color="text.secondary">
                Нажмите на карточку, чтобы открыть в конструкторе · «Использовать» — в чат · «Клонировать» — своя копия
              </Typography>
            </Box>
          </Container>
        </Box>
      )}
      {embedded && (
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5, textAlign: 'center' }}>
          Нажмите на карточку, чтобы открыть в конструкторе · «Использовать» — в чат · «Клонировать» — своя копия
        </Typography>
      )}

      <Box sx={{ py: embedded ? 0 : 2, pb: embedded ? 1.5 : undefined }}>
        <Container maxWidth="xl">
          <GallerySearchBookmarksBar
            searchQuery={searchQuery}
            onSearchChange={setSearchQuery}
            searchPlaceholder="Поиск агентов…"
            showBookmarks={showBookmarks}
            onToggleBookmarks={() => setShowBookmarks((v) => !v)}
            bookmarksEnabled={Boolean(token)}
            allLabel="Все агенты"
            bookmarksLabel="Закладки"
          />
        </Container>
      </Box>

      <Container maxWidth="xl" sx={{ flex: 1, overflowY: 'auto', py: 3 }}>
        {loading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 8 }}>
            <CircularProgress />
          </Box>
        ) : agents.length === 0 ? (
          <Alert severity="info">
            {!canUseAgents
              ? 'Агенты недоступны для вашей роли.'
              : showBookmarks
              ? 'В закладках пока нет агентов. Добавьте агента кнопкой-закладкой на карточке.'
              : 'Публичных агентов пока нет. Опубликуйте агента из конструктора кнопкой «Опубликовать в галерее».'}
          </Alert>
        ) : (
          <Box
            sx={{
              display: 'grid',
              gridTemplateColumns: { xs: '1fr', md: 'repeat(2, 1fr)', lg: 'repeat(3, 1fr)' },
              gap: 3,
            }}
          >
            {agents.map((agent) => (
              <GalleryEntityCard
                key={agent.id}
                item={toCardItem(agent)}
                isDarkMode={isDarkMode}
                using={usingId === agent.id}
                cloning={cloningId === agent.id}
                cloneLabel="Клонировать"
                onOpen={() => openAgentInConstructor(agent.id, navigate)}
                onRate={(rating) => void handleRate(agent.id, rating)}
                onUse={() => void handleUse(agent)}
                onClone={() => void handleClone(agent)}
                onToggleBookmark={() => void handleToggleBookmark(agent)}
              />
            ))}
          </Box>
        )}

        {totalPages > 1 && (
          <Box sx={{ display: 'flex', justifyContent: 'center', mt: 4 }}>
            <Pagination
              count={totalPages}
              page={page}
              onChange={(_, p) => setPage(p)}
              color="primary"
            />
          </Box>
        )}
      </Container>
    </Box>
  );
}