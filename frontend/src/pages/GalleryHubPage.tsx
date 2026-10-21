import React, { useCallback, useMemo } from 'react';
import { Box, Typography, useTheme } from '@mui/material';
import { useNavigate, useSearchParams } from 'react-router-dom';
import GalleryHubTabs, {
  parseGalleryHubTab,
  type GalleryHubTab,
} from '../components/gallery/GalleryHubTabs';
import { AgentGalleryContent } from './AgentGalleryPage';
import { PluginGalleryContent } from './PluginGalleryPage';
import { SkillsGalleryContent } from './SkillsPage';
import { KnowledgeBasesGalleryContent } from './KnowledgeBasesGalleryPage';
import { getWorkZoneBackgroundColor } from '../constants/workZoneBackground';
import { useWorkZoneBgMode } from '../hooks/useWorkZoneBgMode';

export default function GalleryHubPage() {
  const theme = useTheme();
  const isDarkMode = theme.palette.mode === 'dark';
  const workZoneMode = useWorkZoneBgMode();
  const workZoneBgColor = getWorkZoneBackgroundColor(isDarkMode, workZoneMode);
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  const tab = useMemo(
    () => parseGalleryHubTab(searchParams.get('tab')),
    [searchParams],
  );

  const setTab = useCallback(
    (next: GalleryHubTab) => {
      const params = new URLSearchParams(searchParams);
      params.set('tab', next);
      // create=1 только для skills из сайдбара — сбрасываем при смене вкладки
      params.delete('create');
      setSearchParams(params, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  const textColor = isDarkMode ? '#fff' : '#111';
  const muted = isDarkMode ? 'rgba(255,255,255,0.55)' : 'rgba(0,0,0,0.55)';

  return (
    <Box
      sx={{
        flexGrow: 1,
        height: '100%',
        overflow: 'auto',
        pt: 4,
        px: { xs: 2, sm: 3 },
        pb: 3,
        backgroundColor: workZoneBgColor,
        color: textColor,
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      <Box sx={{ maxWidth: 1200, mx: 'auto', width: '100%', display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 2, flexWrap: 'wrap', gap: 1 }}>
          <Typography variant="h5" fontWeight={700} sx={{ color: textColor }}>
            Галерея
          </Typography>
          <Typography
            component="button"
            onClick={() => navigate('/')}
            sx={{
              border: 'none',
              background: 'transparent',
              cursor: 'pointer',
              color: muted,
              fontSize: '0.85rem',
              '&:hover': { color: textColor },
            }}
          >
            К чату
          </Typography>
        </Box>

        <GalleryHubTabs value={tab} onChange={setTab} isDarkMode={isDarkMode} />

        <Box sx={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
          {tab === 'agents' && <AgentGalleryContent embedded />}
          {tab === 'skills' && <SkillsGalleryContent embedded />}
          {tab === 'plugins' && <PluginGalleryContent embedded />}
          {tab === 'knowledge-bases' && <KnowledgeBasesGalleryContent embedded />}
        </Box>
      </Box>
    </Box>
  );
}
