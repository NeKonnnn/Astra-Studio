import React, { useEffect, useState } from 'react';
import { Box, CircularProgress, Paper, Typography } from '@mui/material';
import SkillIcon from '../../icons/SkillsIcon';
import { getApiUrl, API_ENDPOINTS } from '../../config/api';
import { useAuth } from '../../contexts/AuthContext';

export interface SkillSuggestion {
  id: number;
  slug: string;
  name: string;
  description?: string | null;
}

interface SkillMentionAutocompleteProps {
  query: string;
  open: boolean;
  anchorEl: HTMLElement | null;
  onSelect: (skill: SkillSuggestion) => void;
  isDarkMode?: boolean;
}

export default function SkillMentionAutocomplete({
  query,
  open,
  anchorEl,
  onSelect,
  isDarkMode,
}: SkillMentionAutocompleteProps) {
  const { token } = useAuth();
  const [items, setItems] = useState<SkillSuggestion[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedIdx, setSelectedIdx] = useState(0);

  useEffect(() => {
    if (!open || !token) {
      setItems([]);
      return;
    }
    let cancelled = false;
    const t = setTimeout(async () => {
      setLoading(true);
      try {
        const params = new URLSearchParams({ page: '1', limit: '20' });
        if (query.trim()) params.set('query', query.trim());
        const resp = await fetch(`${getApiUrl(API_ENDPOINTS.SKILLS)}/list?${params}`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!resp.ok) throw new Error('fail');
        const data = await resp.json();
        if (!cancelled) {
          setItems(
            (data.items || [])
              .filter(
                (s: SkillSuggestion & { is_active?: boolean; user_invocable?: boolean }) =>
                  s.is_active !== false && s.user_invocable !== false,
              )
              .map((s: SkillSuggestion) => ({
                id: s.id,
                slug: s.slug,
                name: s.name,
                description: s.description,
              })),
          );
          setSelectedIdx(0);
        }
      } catch {
        if (!cancelled) setItems([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, 180);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [open, query, token]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (!items.length) return;
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setSelectedIdx((i) => Math.min(i + 1, items.length - 1));
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        setSelectedIdx((i) => Math.max(i - 1, 0));
      } else if (e.key === 'Enter' || e.key === 'Tab') {
        e.preventDefault();
        e.stopPropagation();
        onSelect(items[selectedIdx]);
      }
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [open, items, selectedIdx, onSelect]);

  if (!open || !anchorEl) return null;

  const rect = anchorEl.getBoundingClientRect();
  const bg = isDarkMode ? '#2a2a2a' : '#fff';
  const text = isDarkMode ? 'rgba(255,255,255,0.9)' : 'rgba(0,0,0,0.87)';
  const muted = isDarkMode ? 'rgba(255,255,255,0.5)' : 'rgba(0,0,0,0.5)';
  const hover = isDarkMode ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.06)';

  return (
    <Paper
      elevation={8}
      sx={{
        position: 'fixed',
        left: rect.left,
        bottom: window.innerHeight - rect.top + 8,
        zIndex: 1400,
        width: Math.min(360, Math.max(260, rect.width)),
        maxHeight: 280,
        overflow: 'auto',
        bgcolor: bg,
        color: text,
        borderRadius: 2,
      }}
    >
      <Typography variant="caption" sx={{ px: 1.5, pt: 1, display: 'block', color: muted }}>
        Skills
      </Typography>
      {loading ? (
        <Box display="flex" justifyContent="center" py={2}>
          <CircularProgress size={22} />
        </Box>
      ) : items.length === 0 ? (
        <Typography variant="body2" sx={{ px: 1.5, py: 1.5, color: muted }}>
          Ничего не найдено
        </Typography>
      ) : (
        items.map((skill, idx) => (
          <Box
            key={skill.id}
            onMouseDown={(e) => {
              e.preventDefault();
              onSelect(skill);
            }}
            onMouseEnter={() => setSelectedIdx(idx)}
            sx={{
              px: 1.5,
              py: 1,
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: 1,
              bgcolor: idx === selectedIdx ? hover : 'transparent',
              '&:hover': { bgcolor: hover },
            }}
          >
            <SkillIcon sx={{ fontSize: 18, opacity: 0.7 }} />
            <Box sx={{ minWidth: 0 }}>
              <Typography variant="body2" noWrap>
                {skill.name}
              </Typography>
              <Typography variant="caption" sx={{ color: muted }} noWrap>
                ${skill.slug}
                {skill.description ? ` — ${skill.description}` : ''}
              </Typography>
            </Box>
          </Box>
        ))
      )}
    </Paper>
  );
}
