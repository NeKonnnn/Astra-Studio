import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Box, Typography, CircularProgress, Link } from '@mui/material';
import {
  Search as SearchIcon,
  Check as CheckIcon,
  Block as NoSkillIcon,
} from '@mui/icons-material';
import SkillIcon from '../icons/SkillsIcon';
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
import {
  clearActiveSkills,
  getActiveSkillIds,
  SKILL_SELECTION_CHANGED_EVENT,
  toggleActiveSkill,
} from '../utils/skillSelectionStorage';
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
  author_name?: string;
  author_full_name?: string | null;
}

interface ChatGearSkillsPanelProps {
  isDarkMode: boolean;
  chatId?: string | null;
}

export default function ChatGearSkillsPanel({ isDarkMode, chatId }: ChatGearSkillsPanelProps) {
  const { token } = useAuth();
  const { state } = useAppContext();
  const effectiveChatId = chatId ?? state.currentChatId;
  const navigate = useNavigate();
  const [skills, setSkills] = useState<SkillRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [activeIds, setActiveIds] = useState<string[]>(() => getActiveSkillIds(effectiveChatId));

  const dropdownItemSx = useMemo(() => getDropdownItemSx(isDarkMode), [isDarkMode]);
  const muted = isDarkMode ? 'rgba(255,255,255,0.65)' : 'rgba(0,0,0,0.6)';
  const text = isDarkMode ? 'rgba(255,255,255,0.95)' : 'rgba(0,0,0,0.9)';
  const border = isDarkMode ? 'rgba(255,255,255,0.12)' : 'rgba(0,0,0,0.12)';
  const placeholderColor = isDarkMode ? 'rgba(255,255,255,0.3)' : 'rgba(0,0,0,0.35)';
  const iconColor = isDarkMode ? 'rgba(255,255,255,0.7)' : 'rgba(0,0,0,0.6)';
  const subtleColor = isDarkMode ? 'rgba(255,255,255,0.5)' : 'rgba(0,0,0,0.5)';
  const mutedTextColor = isDarkMode ? 'rgba(255,255,255,0.9)' : 'rgba(0,0,0,0.85)';

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
      const items = (data.items || []) as SkillRow[];
      setSkills(
        items.filter(
          (s) =>
            s.is_active !== false &&
            s.user_invocable !== false &&
            !s.always_apply,
        ),
      );
    } catch {
      setSkills([]);
    } finally {
      setLoading(false);
    }
  }, [token]);

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
    if (!q) return skills;
    return skills.filter((s) => {
      const title = (s.display_title || s.name || '').toLowerCase();
      const blob = [title, s.slug, s.description || ''].join(' ').toLowerCase();
      return blob.includes(q);
    });
  }, [skills, search]);

  const noneSelected = activeIds.length === 0;

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
      <Box
        sx={{
          flexShrink: 0,
          display: 'flex',
          alignItems: 'center',
          px: 1.5,
          py: 0.9,
          gap: 1,
          borderBottom: `1px solid ${border}`,
        }}
      >
        <SearchIcon sx={{ color: subtleColor, fontSize: 16, flexShrink: 0 }} />
        <Box
          component="input"
          placeholder="Поиск skills..."
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

      <Box
        sx={{
          height: `${CHAT_GEAR_MENU_AGENT_LIST_MAX_HEIGHT_PX}px`,
          maxHeight: `${CHAT_GEAR_MENU_AGENT_LIST_MAX_HEIGHT_PX}px`,
          flexShrink: 0,
          overflowX: 'hidden',
          overflowY: 'auto',
          px: 0.75,
          py: 1,
          boxSizing: 'border-box',
          ...CHAT_GEAR_SCROLL_AREA_NO_VISIBLE_SCROLLBAR_SX,
        }}
      >
        {loading && skills.length === 0 ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}>
            <CircularProgress size={28} />
          </Box>
        ) : (
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5 }}>
            <Box
              onClick={() => clearActiveSkills(effectiveChatId)}
              sx={{
                ...dropdownItemSx,
                display: 'flex',
                alignItems: 'center',
                gap: 1,
                color: noneSelected ? mutedTextColor : iconColor,
                fontWeight: noneSelected ? 600 : 400,
                bgcolor: noneSelected
                  ? isDarkMode
                    ? 'rgba(255,255,255,0.06)'
                    : 'rgba(0,0,0,0.04)'
                  : 'transparent',
                fontStyle: 'italic',
                borderRadius: 1,
                py: 0.75,
                px: 0.75,
              }}
            >
              <NoSkillIcon sx={{ fontSize: 18, color: subtleColor, flexShrink: 0 }} />
              <Typography sx={{ flex: 1, fontSize: MENU_ACTION_TEXT_SIZE }}>Без skills</Typography>
              {noneSelected && <CheckIcon sx={{ fontSize: 16, color: 'primary.main', flexShrink: 0 }} />}
            </Box>

            {filtered.map((skill) => {
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
                    color: selected ? mutedTextColor : iconColor,
                    fontWeight: selected ? 600 : 400,
                    bgcolor: selected
                      ? isDarkMode
                        ? 'rgba(255,255,255,0.06)'
                        : 'rgba(0,0,0,0.04)'
                      : 'transparent',
                    borderRadius: 1,
                    py: 0.75,
                    px: 0.75,
                  }}
                >
                  <SkillIcon
                    sx={{ fontSize: 18, color: selected ? 'primary.main' : iconColor, flexShrink: 0 }}
                  />
                  <Box sx={{ flex: 1, minWidth: 0 }}>
                    <Typography
                      sx={{
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                        fontSize: MENU_ACTION_TEXT_SIZE,
                      }}
                    >
                      {title}
                    </Typography>
                    <Typography
                      sx={{
                        fontSize: '0.65rem',
                        color: subtleColor,
                        lineHeight: 1.2,
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      ${skill.slug}
                      {skill.is_shared_with_me
                        ? ` · от ${formatAuthorLabel(skill.author_name, skill.author_full_name) || 'коллеги'}`
                        : ''}
                    </Typography>
                  </Box>
                  {selected ? (
                    <CheckIcon sx={{ fontSize: 16, color: 'primary.main', flexShrink: 0 }} />
                  ) : null}
                </Box>
              );
            })}

            {!loading && filtered.length === 0 && !search.trim() && (
              <Typography
                variant="body2"
                sx={{
                  color: subtleColor,
                  fontSize: MENU_ACTION_TEXT_SIZE,
                  px: 0.5,
                  py: 1,
                  textAlign: 'center',
                }}
              >
                Нет доступных skills
              </Typography>
            )}
            {!loading && search.trim() && filtered.length === 0 && (
              <Typography
                variant="body2"
                sx={{
                  color: subtleColor,
                  fontSize: MENU_ACTION_TEXT_SIZE,
                  px: 0.5,
                  py: 1,
                  textAlign: 'center',
                }}
              >
                Ничего не найдено
              </Typography>
            )}
          </Box>
        )}
      </Box>

      <Box sx={{ flexShrink: 0, px: 1.5, py: 1, borderTop: `1px solid ${border}` }}>
        <Link
          component="button"
          type="button"
          underline="hover"
          onClick={() => navigate('/gallery?tab=skills')}
          sx={{ fontSize: '0.75rem', color: muted }}
        >
          Управление skills →
        </Link>
      </Box>
    </Box>
  );
}
