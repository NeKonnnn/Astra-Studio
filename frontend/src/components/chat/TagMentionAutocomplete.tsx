import React, { useEffect, useMemo, useState } from 'react';
import { Box, CircularProgress, Paper, Typography } from '@mui/material';
import SearchIcon from '@mui/icons-material/Search';
import LocalOfferOutlinedIcon from '@mui/icons-material/LocalOfferOutlined';
import { fetchAgentTags, type AgentTag } from '../../constants/AgentTags';
import {
  DROPDOWN_PAPER_MARGIN_TOP,
  SIDEBAR_HIDE_SCROLLBAR_SX,
  getDropdownItemSx,
  getDropdownItemStateSx,
  getDropdownPanelSx,
} from '../../constants/menuStyles';

export interface TagSuggestion {
  id: number;
  name: string;
}

interface TagMentionAutocompleteProps {
  query: string;
  open: boolean;
  anchorEl: HTMLElement | null;
  onSelect: (tag: TagSuggestion) => void;
  isDarkMode?: boolean;
}

export default function TagMentionAutocomplete({
  query,
  open,
  anchorEl,
  onSelect,
  isDarkMode = true,
}: TagMentionAutocompleteProps) {
  const [catalog, setCatalog] = useState<AgentTag[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedIdx, setSelectedIdx] = useState(0);
  const [searchQuery, setSearchQuery] = useState(query);

  const dark = Boolean(isDarkMode);
  const panelSx = useMemo(() => getDropdownPanelSx(dark), [dark]);
  const dropdownItemSx = useMemo(() => getDropdownItemSx(dark), [dark]);
  const muted = dark ? 'rgba(255,255,255,0.45)' : 'rgba(0,0,0,0.45)';
  const searchIconColor = dark ? 'rgba(255,255,255,0.35)' : 'rgba(0,0,0,0.4)';
  const searchPlaceholderColor = dark ? 'rgba(255,255,255,0.3)' : 'rgba(0,0,0,0.4)';
  const searchBorder = dark ? '1px solid rgba(255,255,255,0.07)' : '1px solid rgba(0,0,0,0.08)';

  useEffect(() => {
    if (open) setSearchQuery(query);
  }, [open, query]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    void fetchAgentTags()
      .then((list) => {
        if (!cancelled) setCatalog(list);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  const items = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    const filtered = q
      ? catalog.filter((t) => t.name.toLowerCase().includes(q))
      : catalog;
    return filtered.slice(0, 20).map((t) => ({ id: t.id, name: t.name }));
  }, [catalog, searchQuery]);

  useEffect(() => {
    setSelectedIdx(0);
  }, [items]);

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

  return (
    <Paper
      elevation={0}
      sx={{
        ...panelSx,
        position: 'fixed',
        left: rect.left,
        bottom: window.innerHeight - rect.top + 8,
        zIndex: 1400,
        width: Math.min(360, Math.max(260, rect.width)),
        mt: DROPDOWN_PAPER_MARGIN_TOP,
        overflow: 'hidden',
      }}
    >
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          px: 1.5,
          py: 0.9,
          gap: 1,
          borderBottom: searchBorder,
        }}
      >
        <SearchIcon sx={{ color: searchIconColor, fontSize: 16, flexShrink: 0 }} />
        <Box
          component="input"
          placeholder="Поиск тегов по имени"
          value={searchQuery}
          onChange={(e: React.ChangeEvent<HTMLInputElement>) => setSearchQuery(e.target.value)}
          onMouseDown={(e: React.MouseEvent) => e.stopPropagation()}
          sx={{
            flex: 1,
            bgcolor: 'transparent',
            border: 'none',
            outline: 'none',
            color: dark ? 'white' : 'rgba(0,0,0,0.87)',
            fontSize: '0.82rem',
            '&::placeholder': { color: searchPlaceholderColor },
          }}
        />
      </Box>

      {loading ? (
        <Box display="flex" justifyContent="center" py={2}>
          <CircularProgress size={18} sx={{ color: muted }} />
        </Box>
      ) : items.length === 0 ? (
        <Typography
          variant="body2"
          sx={{
            px: 1.5,
            py: 1.5,
            fontSize: '0.78rem',
            color: muted,
            textAlign: 'center',
          }}
        >
          {catalog.length === 0 ? 'Нет тегов' : 'Ничего не найдено'}
        </Typography>
      ) : (
        <Box sx={{ maxHeight: 260, overflowY: 'auto', py: 0.5, ...SIDEBAR_HIDE_SCROLLBAR_SX }}>
          {items.map((tag, idx) => {
            const selected = idx === selectedIdx;
            return (
              <Box
                key={tag.id}
                onMouseDown={(e) => {
                  e.preventDefault();
                  onSelect(tag);
                }}
                onMouseEnter={() => setSelectedIdx(idx)}
                sx={{
                  ...dropdownItemSx,
                  ...getDropdownItemStateSx(dark, selected),
                  display: 'flex',
                  alignItems: 'center',
                  gap: 1,
                  minWidth: 0,
                }}
              >
                <LocalOfferOutlinedIcon sx={{ fontSize: 16, opacity: 0.7, flexShrink: 0 }} />
                <Box sx={{ minWidth: 0, flex: 1 }}>
                  <Typography
                    variant="body2"
                    noWrap
                    sx={{
                      fontSize: 'inherit',
                      lineHeight: 'inherit',
                      fontWeight: selected ? 600 : 400,
                    }}
                  >
                    {tag.name}
                  </Typography>
                  <Typography variant="caption" sx={{ color: muted, fontSize: '0.68rem' }} noWrap>
                    #{tag.name}
                  </Typography>
                </Box>
              </Box>
            );
          })}
        </Box>
      )}
    </Paper>
  );
}
