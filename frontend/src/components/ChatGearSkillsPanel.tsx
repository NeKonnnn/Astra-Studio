import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Box,
  CircularProgress,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from '@mui/material';
import { Check as CheckIcon, Search as SearchIcon } from '@mui/icons-material';
import SkillIcon from '../icons/SkillsIcon';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { useAppContext } from '../contexts/AppContext';
import { authFetch, getApiUrl, API_ENDPOINTS } from '../config/api';
import {
  getDropdownItemSx,
  MENU_ACTION_TEXT_SIZE,
  CHAT_GEAR_SCROLL_AREA_NO_VISIBLE_SCROLLBAR_SX,
} from '../constants/menuStyles';
import {
  clearActiveSkills,
  getActiveSkillIds,
  SKILL_SELECTION_CHANGED_EVENT,
  toggleActiveSkill,
} from '../utils/skillSelectionStorage';
import { openSkillInSidebar } from '../utils/openSkillSidebarNav';
import ChatGearRowSettingsButton from './ChatGearRowSettingsButton';
import { formatAuthorLabel } from '../utils/formatAuthorLabel';

interface SkillRow {
  id: number;
  slug: string;
  name: string;
  display_title?: string | null;
  description?: string | null;
  is_active: boolean;
  user_invocable?: boolean;
  always_apply?: boolean;
  is_shared_with_me?: boolean;
  author_id?: string;
  author_name?: string;
  author_full_name?: string | null;
}

interface ChatGearSkillsPanelProps {
  isDarkMode: boolean;
  chatId?: string | null;
}

function isSelectableSkill(s: SkillRow): boolean {
  return s.is_active !== false && s.user_invocable !== false && !s.always_apply;
}

export default function ChatGearSkillsPanel({ isDarkMode, chatId }: ChatGearSkillsPanelProps) {
  const { token, user } = useAuth();
  const { state } = useAppContext();
  const effectiveChatId = chatId ?? state.currentChatId;
  const navigate = useNavigate();
  const [skills, setSkills] = useState<SkillRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [subtab, setSubtab] = useState<'my' | 'gallery'>('my');
  const [activeIds, setActiveIds] = useState<string[]>(() => getActiveSkillIds(effectiveChatId));

  const dropdownItemSx = useMemo(() => getDropdownItemSx(isDarkMode), [isDarkMode]);
  const muted = isDarkMode ? 'rgba(255,255,255,0.65)' : 'rgba(0,0,0,0.6)';
  const text = isDarkMode ? 'rgba(255,255,255,0.95)' : 'rgba(0,0,0,0.9)';
  const border = isDarkMode ? 'rgba(255,255,255,0.12)' : 'rgba(0,0,0,0.12)';
  const placeholderColor = isDarkMode ? 'rgba(255,255,255,0.3)' : 'rgba(0,0,0,0.35)';
  const menuDividerBorder = isDarkMode ? 'rgba(255,255,255,0.12)' : 'rgba(0,0,0,0.08)';
  const subtleColor = isDarkMode ? 'rgba(255,255,255,0.35)' : 'rgba(0,0,0,0.45)';
  const iconColor = isDarkMode ? 'rgba(255,255,255,0.7)' : 'rgba(0,0,0,0.6)';

  const loadSkills = useCallback(async () => {
    setLoading(true);
    try {
      const headers: HeadersInit = token ? { Authorization: `Bearer ${token}` } : {};
      if (subtab === 'gallery') {
        const resp = await authFetch(
          `${getApiUrl(API_ENDPOINTS.SKILLS)}/my/bookmarks?limit=100`,
          { headers },
        );
        if (!resp.ok) {
          setSkills([]);
          return;
        }
        const data = await resp.json();
        setSkills(((data.items || []) as SkillRow[]).filter(isSelectableSkill));
        return;
      }

      const [createdResp, sharedResp] = await Promise.all([
        authFetch(
          `${getApiUrl(API_ENDPOINTS.SKILLS)}/list?limit=100&view_option=created`,
          { headers },
        ),
        authFetch(
          `${getApiUrl(API_ENDPOINTS.SKILLS)}/list?limit=100&view_option=shared`,
          { headers },
        ),
      ]);
      const created = createdResp.ok
        ? (((await createdResp.json()).items || []) as SkillRow[])
        : [];
      const shared = sharedResp.ok
        ? (((await sharedResp.json()).items || []) as SkillRow[]).map((s) => ({
            ...s,
            is_shared_with_me: true,
          }))
        : [];
      const byId = new Map<number, SkillRow>();
      for (const s of created) byId.set(s.id, s);
      for (const s of shared) {
        if (!byId.has(s.id)) byId.set(s.id, s);
      }
      setSkills(Array.from(byId.values()).filter(isSelectableSkill));
    } catch {
      setSkills([]);
    } finally {
      setLoading(false);
    }
  }, [token, subtab]);

  useEffect(() => {
    void loadSkills();
  }, [loadSkills]);

  useEffect(() => {
    setActiveIds(getActiveSkillIds(effectiveChatId));
    const onChange = (e: Event) => {
      const detailChatId = (e as CustomEvent<{ chatId?: string }>).detail?.chatId;
      if (detailChatId && effectiveChatId && detailChatId !== effectiveChatId) return;
      setActiveIds(getActiveSkillIds(effectiveChatId));
    };
    window.addEventListener(SKILL_SELECTION_CHANGED_EVENT, onChange as EventListener);
    return () => window.removeEventListener(SKILL_SELECTION_CHANGED_EVENT, onChange as EventListener);
  }, [effectiveChatId]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    const uid = (user?.user_id || '').toLowerCase();
    let list = skills;
    if (subtab === 'my' && uid) {
      list = list.filter(
        (s) =>
          s.is_shared_with_me ||
          (s.author_id || '').toLowerCase() === uid ||
          !s.author_id,
      );
    }
    if (!q) return list;
    return list.filter((s) => {
      const title = (s.display_title || s.name || '').toLowerCase();
      const blob = [title, s.slug, s.description || ''].join(' ').toLowerCase();
      return blob.includes(q);
    });
  }, [skills, search, subtab, user?.user_id]);

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
        <ToggleButton value="my">Мои skills</ToggleButton>
        <ToggleButton value="gallery">Skills из галереи</ToggleButton>
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
            placeholder="Поиск skills…"
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
          onClick={() => clearActiveSkills(effectiveChatId)}
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
            <SkillIcon sx={{ fontSize: 28, opacity: 0.35, mb: 0.5 }} />
            <Typography sx={{ fontSize: '0.78rem', color: muted }}>
              {search.trim()
                ? 'Ничего не найдено'
                : subtab === 'gallery'
                  ? 'В галерее пока нет skills'
                  : 'Нет своих skills'}
            </Typography>
            {!search.trim() ? (
              <Typography
                component="button"
                onClick={() => navigate('/gallery?tab=skills')}
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
            ) : null}
          </Box>
        ) : (
          filtered.map((skill) => {
            const selected = activeIds.includes(skill.slug);
            const title = (skill.display_title || skill.name || skill.slug).trim();
            return (
              <Box
                key={skill.id}
                onClick={() => toggleActiveSkill(effectiveChatId, skill.slug, !selected, title)}
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
                <SkillIcon
                  sx={{
                    fontSize: 18,
                    color: selected ? 'primary.main' : iconColor,
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
                    {title}
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
                    ${skill.slug}
                    {' · '}
                    {skill.is_shared_with_me ? 'от ' : ''}
                    {formatAuthorLabel(skill.author_name, skill.author_full_name, skill.author_id) ||
                      (skill.is_shared_with_me ? 'коллеги' : 'автор неизвестен')}
                  </Typography>
                </Box>
                <ChatGearRowSettingsButton
                  title="Настройки skill"
                  color={muted}
                  onOpen={() => openSkillInSidebar(skill.id, navigate)}
                />
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
