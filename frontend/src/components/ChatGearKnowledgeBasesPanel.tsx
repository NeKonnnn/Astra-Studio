import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Box,
  CircularProgress,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from '@mui/material';
import { Check as CheckIcon, Search as SearchIcon } from '@mui/icons-material';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { useAppContext } from '../contexts/AppContext';
import { getApiUrl, API_ENDPOINTS } from '../config/api';
import {
  getDropdownItemSx,
  MENU_ACTION_TEXT_SIZE,
  CHAT_GEAR_MENU_AGENT_LIST_MAX_HEIGHT_PX,
  CHAT_GEAR_SCROLL_AREA_NO_VISIBLE_SCROLLBAR_SX,
} from '../constants/menuStyles';
import { formatAuthorLabel } from '../utils/formatAuthorLabel';
import KnowledgeBaseIcon from '../icons/KnowledgeBaseIcon';
import {
  clearActiveKnowledgeBases,
  getActiveKnowledgeBaseIds,
  KB_SELECTION_CHANGED_EVENT,
  toggleActiveKnowledgeBase,
} from '../utils/knowledgeBaseSelectionStorage';

interface KbRow {
  id: number;
  name: string;
  description?: string | null;
  is_public?: boolean;
  document_count?: number;
  author_id: string;
  author_name?: string;
  author_full_name?: string | null;
}

interface ChatGearKnowledgeBasesPanelProps {
  isDarkMode: boolean;
  chatId?: string | null;
}

export default function ChatGearKnowledgeBasesPanel({
  isDarkMode,
  chatId,
}: ChatGearKnowledgeBasesPanelProps) {
  const { token, user } = useAuth();
  const { state } = useAppContext();
  const effectiveChatId = chatId ?? state.currentChatId;
  const navigate = useNavigate();
  const [items, setItems] = useState<KbRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [subtab, setSubtab] = useState<'my' | 'gallery'>('my');
  const [activeIds, setActiveIds] = useState<number[]>(() =>
    getActiveKnowledgeBaseIds(effectiveChatId),
  );

  const dropdownItemSx = useMemo(() => getDropdownItemSx(isDarkMode), [isDarkMode]);
  const muted = isDarkMode ? 'rgba(255,255,255,0.65)' : 'rgba(0,0,0,0.6)';
  const text = isDarkMode ? 'rgba(255,255,255,0.95)' : 'rgba(0,0,0,0.9)';
  const border = isDarkMode ? 'rgba(255,255,255,0.12)' : 'rgba(0,0,0,0.12)';
  const placeholderColor = isDarkMode ? 'rgba(255,255,255,0.3)' : 'rgba(0,0,0,0.35)';
  const menuDividerBorder = isDarkMode ? 'rgba(255,255,255,0.12)' : 'rgba(0,0,0,0.08)';
  const subtleColor = isDarkMode ? 'rgba(255,255,255,0.35)' : 'rgba(0,0,0,0.45)';

  const loadItems = useCallback(async () => {
    setLoading(true);
    try {
      const headers: HeadersInit = token ? { Authorization: `Bearer ${token}` } : {};
      const view = subtab === 'gallery' ? 'public' : 'created';
      const resp = await fetch(
        `${getApiUrl(API_ENDPOINTS.KNOWLEDGE_BASES)}/list?limit=100&view_option=${view}`,
        { headers },
      );
      if (!resp.ok) {
        setItems([]);
        return;
      }
      const data = await resp.json();
      setItems((data.items || []) as KbRow[]);
    } catch {
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, [token, subtab]);

  useEffect(() => {
    void loadItems();
  }, [loadItems]);

  useEffect(() => {
    setActiveIds(getActiveKnowledgeBaseIds(effectiveChatId));
    const onChange = (e: Event) => {
      const detailChatId = (e as CustomEvent<{ chatId?: string }>).detail?.chatId;
      if (detailChatId && effectiveChatId && detailChatId !== effectiveChatId) return;
      setActiveIds(getActiveKnowledgeBaseIds(effectiveChatId));
    };
    window.addEventListener(KB_SELECTION_CHANGED_EVENT, onChange);
    return () => window.removeEventListener(KB_SELECTION_CHANGED_EVENT, onChange);
  }, [effectiveChatId]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    const uid = (user?.user_id || '').toLowerCase();
    let list = items;
    if (subtab === 'my' && uid) {
      list = list.filter((k) => (k.author_id || '').toLowerCase() === uid);
    }
    if (!q) return list;
    return list.filter(
      (k) =>
        k.name.toLowerCase().includes(q) ||
        (k.description || '').toLowerCase().includes(q),
    );
  }, [items, search, subtab, user?.user_id]);

  const tabs = useMemo(
    () => (
      <ToggleButtonGroup
        exclusive
        value={subtab}
        onChange={(_, v: 'my' | 'gallery' | null) => {
          if (v) setSubtab(v);
        }}
        fullWidth
        size="small"
        sx={{
          flexShrink: 0,
          px: 1.25,
          py: 0.5,
          gap: 0.5,
          borderBottom: `1px solid ${menuDividerBorder}`,
          '& .MuiToggleButtonGroup-grouped': {
            border: `1px solid ${border}`,
            borderRadius: '8px !important',
            flex: 1,
            py: 0.4,
            textTransform: 'none',
            fontSize: '0.7rem',
            fontWeight: 500,
            lineHeight: 1.2,
          },
        }}
      >
        <ToggleButton value="my">Мои базы знаний</ToggleButton>
        <ToggleButton value="gallery">Базы знаний из галереи</ToggleButton>
      </ToggleButtonGroup>
    ),
    [subtab, border, menuDividerBorder],
  );

  return (
    <Box
      sx={{
        display: 'flex',
        flexDirection: 'column',
        minHeight: 0,
        flex: 1,
        height: '100%',
        maxHeight: '100%',
        overflow: 'hidden',
      }}
    >
      <Box sx={{ flexShrink: 0 }}>
        <Box
          sx={{
            display: 'flex',
            alignItems: 'center',
            px: 1.5,
            py: 0.9,
            gap: 1,
            borderBottom: `1px solid ${menuDividerBorder}`,
          }}
        >
          <SearchIcon sx={{ color: subtleColor, fontSize: 16, flexShrink: 0 }} />
          <Box
            component="input"
            placeholder="Поиск баз знаний…"
            value={search}
            onChange={(e: React.ChangeEvent<HTMLInputElement>) => setSearch(e.target.value)}
            sx={{
              flex: 1,
              minWidth: 0,
              bgcolor: 'transparent',
              border: 'none',
              outline: 'none',
              color: text,
              fontSize: MENU_ACTION_TEXT_SIZE,
              '&::placeholder': { color: placeholderColor },
            }}
          />
        </Box>
        {tabs}
      </Box>

      {activeIds.length > 0 && (
        <Typography
          component="button"
          onClick={() => clearActiveKnowledgeBases(effectiveChatId)}
          sx={{
            border: 'none',
            background: 'transparent',
            cursor: 'pointer',
            color: muted,
            fontSize: '0.72rem',
            textAlign: 'left',
            px: 1.5,
            pt: 0.75,
            pb: 0,
            flexShrink: 0,
            '&:hover': { color: text },
          }}
        >
          Сбросить выбор ({activeIds.length})
        </Typography>
      )}

      <Box
        sx={{
          flex: 1,
          minHeight: 0,
          maxHeight: CHAT_GEAR_MENU_AGENT_LIST_MAX_HEIGHT_PX,
          overflowY: 'auto',
          ...CHAT_GEAR_SCROLL_AREA_NO_VISIBLE_SCROLLBAR_SX,
          px: 0.5,
          py: 0.5,
        }}
      >
        {loading ? (
          <Box sx={{ py: 2, display: 'flex', justifyContent: 'center' }}>
            <CircularProgress size={18} />
          </Box>
        ) : filtered.length === 0 ? (
          <Box sx={{ py: 2, textAlign: 'center' }}>
            <KnowledgeBaseIcon sx={{ fontSize: 28, opacity: 0.35, mb: 0.5 }} />
            <Typography sx={{ fontSize: '0.78rem', color: muted }}>
              {subtab === 'gallery'
                ? 'В галерее пока нет баз знаний'
                : 'Нет своих баз знаний'}
            </Typography>
            <Typography
              component="button"
              onClick={() => navigate('/gallery?tab=knowledge-bases')}
              sx={{
                mt: 0.75,
                border: 'none',
                background: 'transparent',
                cursor: 'pointer',
                color: '#2196f3',
                fontSize: '0.75rem',
              }}
            >
              Открыть галерею
            </Typography>
          </Box>
        ) : (
          filtered.map((kb) => {
            const selected = activeIds.includes(kb.id);
            return (
              <Box
                key={kb.id}
                onClick={() => toggleActiveKnowledgeBase(effectiveChatId, kb.id, kb.name)}
                sx={{
                  ...dropdownItemSx,
                  display: 'flex',
                  alignItems: 'center',
                  gap: 1,
                  color: selected ? muted : text,
                  fontWeight: selected ? 600 : 400,
                  bgcolor: selected
                    ? isDarkMode
                      ? 'rgba(255,255,255,0.06)'
                      : 'rgba(0,0,0,0.04)'
                    : 'transparent',
                  borderRadius: 1,
                  py: 0.75,
                  px: 0.75,
                  cursor: 'pointer',
                }}
              >
                <KnowledgeBaseIcon
                  sx={{
                    fontSize: 18,
                    color: selected ? 'primary.main' : muted,
                    flexShrink: 0,
                    opacity: selected ? 1 : 0.7,
                  }}
                />
                <Box sx={{ flex: 1, minWidth: 0 }}>
                  <Typography
                    sx={{
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                      fontSize: MENU_ACTION_TEXT_SIZE,
                      color: text,
                    }}
                  >
                    {kb.name}
                  </Typography>
                  <Typography
                    sx={{
                      fontSize: '0.65rem',
                      color: muted,
                      lineHeight: 1.2,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {(kb.document_count || 0) > 0
                      ? `${kb.document_count} док.`
                      : 'без документов'}
                    {' · '}
                    {formatAuthorLabel(kb.author_name, kb.author_full_name, kb.author_id)}
                  </Typography>
                </Box>
                {selected ? (
                  <CheckIcon sx={{ fontSize: 16, color: 'primary.main', flexShrink: 0 }} />
                ) : null}
              </Box>
            );
          })
        )}
      </Box>
    </Box>
  );
}
