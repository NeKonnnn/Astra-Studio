/**
 * Checkbox + multi-select баз знаний для модалок проекта.
 */

import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Box,
  Checkbox,
  FormControl,
  FormControlLabel,
  InputAdornment,
  InputLabel,
  OutlinedInput,
  Popover,
  Typography,
  useTheme,
} from '@mui/material';
import {
  CheckBox as CheckBoxIcon,
  CheckBoxOutlineBlank as CheckBoxBlankIcon,
  ExpandMore as ExpandMoreIcon,
} from '@mui/icons-material';
import { useAuth } from '../contexts/AuthContext';
import { getApiUrl, API_ENDPOINTS } from '../config/api';
import {
  getDropdownChevronSx,
  getDropdownItemStateSx,
  getDropdownItemSx,
  getDropdownPopoverPaperSx,
  SIDEBAR_HIDE_SCROLLBAR_SX,
} from '../constants/menuStyles';

interface KbOption {
  id: number;
  name: string;
  description?: string;
}

interface ProjectKnowledgeBasesFieldProps {
  enabled: boolean;
  selectedIds: number[];
  onEnabledChange: (enabled: boolean) => void;
  onSelectedIdsChange: (ids: number[]) => void;
  disabled?: boolean;
}

export default function ProjectKnowledgeBasesField({
  enabled,
  selectedIds,
  onEnabledChange,
  onSelectedIdsChange,
  disabled = false,
}: ProjectKnowledgeBasesFieldProps) {
  const theme = useTheme();
  const isDarkMode = theme.palette.mode === 'dark';
  const { token } = useAuth();
  const [options, setOptions] = useState<KbOption[]>([]);
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const triggerRef = useRef<HTMLDivElement>(null);

  const dropdownItemSx = useMemo(() => getDropdownItemSx(isDarkMode), [isDarkMode]);
  const dropdownChevronSx = useMemo(() => getDropdownChevronSx(isDarkMode), [isDarkMode]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const headers: HeadersInit = token ? { Authorization: `Bearer ${token}` } : {};
        const resp = await fetch(
          `${getApiUrl(API_ENDPOINTS.KNOWLEDGE_BASES)}/list?limit=100`,
          { headers },
        );
        if (!resp.ok || cancelled) return;
        const data = await resp.json();
        if (cancelled) return;
        setOptions(
          (data.items || []).map((k: { id: number; name: string; description?: string }) => ({
            id: Number(k.id),
            name: k.name,
            description: k.description,
          })),
        );
      } catch {
        if (!cancelled) setOptions([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token]);

  const label =
    selectedIds.length === 0
      ? ''
      : selectedIds.length === 1
        ? options.find((o) => o.id === selectedIds[0])?.name || String(selectedIds[0])
        : `Выбрано: ${selectedIds.length}`;

  return (
    <Box sx={{ mb: 2 }}>
      <FormControlLabel
        control={
          <Checkbox
            size="small"
            disabled={disabled}
            checked={enabled}
            onChange={(e) => {
              const next = e.target.checked;
              onEnabledChange(next);
              if (!next) onSelectedIdsChange([]);
            }}
          />
        }
        label={
          <Typography variant="body2">Прикрепить базы знаний</Typography>
        }
        sx={{ m: 0, mb: enabled ? 1 : 0 }}
      />
      {enabled && (
        <>
          <FormControl variant="outlined" fullWidth size="small">
            <InputLabel htmlFor="project-knowledge-bases">Базы знаний</InputLabel>
            <OutlinedInput
              ref={triggerRef}
              id="project-knowledge-bases"
              label="Базы знаний"
              value={label}
              readOnly
              placeholder="Выберите базы знаний"
              disabled={disabled}
              onClick={() => {
                if (disabled) return;
                setAnchor(triggerRef.current);
              }}
              endAdornment={
                <InputAdornment position="end">
                  <ExpandMoreIcon
                    sx={{
                      ...dropdownChevronSx,
                      transform: Boolean(anchor) ? 'rotate(180deg)' : 'none',
                    }}
                  />
                </InputAdornment>
              }
            />
          </FormControl>
          <Popover
            open={!disabled && Boolean(anchor)}
            anchorEl={anchor}
            onClose={() => setAnchor(null)}
            anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
            transformOrigin={{ vertical: 'top', horizontal: 'left' }}
            slotProps={{
              paper: { sx: getDropdownPopoverPaperSx(anchor, isDarkMode) },
            }}
          >
            <Box sx={{ py: 0.5, maxHeight: 220, overflowY: 'auto', ...SIDEBAR_HIDE_SCROLLBAR_SX }}>
              {options.length === 0 ? (
                <Box sx={{ px: 1.5, py: 1.5, fontSize: '0.78rem', opacity: 0.55, textAlign: 'center' }}>
                  Нет доступных баз знаний
                </Box>
              ) : (
                options.map((kb) => {
                  const selected = selectedIds.includes(kb.id);
                  return (
                    <Box
                      key={kb.id}
                      onClick={() => {
                        onSelectedIdsChange(
                          selected
                            ? selectedIds.filter((x) => x !== kb.id)
                            : [...selectedIds, kb.id],
                        );
                      }}
                      sx={{
                        ...dropdownItemSx,
                        display: 'flex',
                        alignItems: 'center',
                        gap: 0.75,
                        ...getDropdownItemStateSx(isDarkMode, selected),
                      }}
                    >
                      {selected ? (
                        <CheckBoxIcon sx={{ fontSize: 16, color: '#2196f3', flexShrink: 0 }} />
                      ) : (
                        <CheckBoxBlankIcon sx={{ fontSize: 16, opacity: 0.4, flexShrink: 0 }} />
                      )}
                      <Box sx={{ minWidth: 0 }}>
                        <Box component="span" sx={{ display: 'block' }}>
                          {kb.name}
                        </Box>
                        {kb.description ? (
                          <Box component="span" sx={{ display: 'block', opacity: 0.5, fontSize: '0.72rem' }}>
                            {kb.description}
                          </Box>
                        ) : null}
                      </Box>
                    </Box>
                  );
                })
              )}
            </Box>
          </Popover>
        </>
      )}
    </Box>
  );
}
