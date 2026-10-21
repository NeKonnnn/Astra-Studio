import React from 'react';
import { Box, Chip } from '@mui/material';

export type GalleryHubTab = 'agents' | 'skills' | 'plugins' | 'knowledge-bases';

export const GALLERY_HUB_TABS: Array<{ id: GalleryHubTab; label: string }> = [
  { id: 'agents', label: 'Агенты' },
  { id: 'skills', label: 'Skills' },
  { id: 'plugins', label: 'Плагины' },
  { id: 'knowledge-bases', label: 'База знаний' },
];

export function parseGalleryHubTab(raw: string | null | undefined): GalleryHubTab {
  const v = String(raw || '').trim().toLowerCase();
  if (
    v === 'agents' ||
    v === 'skills' ||
    v === 'plugins' ||
    v === 'knowledge-bases' ||
    v === 'knowledge_bases' ||
    v === 'kb'
  ) {
    if (v === 'knowledge_bases' || v === 'kb') return 'knowledge-bases';
    return v as GalleryHubTab;
  }
  // legacy ?tab=prompts → агенты
  return 'agents';
}

interface GalleryHubTabsProps {
  value: GalleryHubTab;
  onChange: (tab: GalleryHubTab) => void;
  isDarkMode?: boolean;
}

export default function GalleryHubTabs({ value, onChange, isDarkMode = true }: GalleryHubTabsProps) {
  return (
    <Box
      sx={{
        display: 'flex',
        gap: 1,
        flexWrap: 'wrap',
        justifyContent: 'center',
        mb: 3,
      }}
    >
      {GALLERY_HUB_TABS.map((tab) => {
        const active = value === tab.id;
        return (
          <Chip
            key={tab.id}
            label={tab.label}
            onClick={() => onChange(tab.id)}
            color={active ? 'primary' : 'default'}
            variant={active ? 'filled' : 'outlined'}
            sx={{
              fontWeight: active ? 600 : 400,
              bgcolor: active
                ? undefined
                : isDarkMode
                  ? 'rgba(255,255,255,0.06)'
                  : 'rgba(0,0,0,0.04)',
              borderColor: isDarkMode ? 'rgba(255,255,255,0.18)' : 'rgba(0,0,0,0.16)',
              color: active ? undefined : isDarkMode ? 'rgba(255,255,255,0.85)' : 'rgba(0,0,0,0.75)',
              '&:hover': {
                bgcolor: active
                  ? undefined
                  : isDarkMode
                    ? 'rgba(255,255,255,0.1)'
                    : 'rgba(0,0,0,0.08)',
              },
            }}
          />
        );
      })}
    </Box>
  );
}
