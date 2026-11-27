import React, { useEffect } from 'react';
import { Box, IconButton, Tooltip, Typography } from '@mui/material';
import { CloseFullscreen as CollapseIcon } from '@mui/icons-material';
import { useChatWorkspaceViewer } from '../../contexts/ChatWorkspaceViewerContext';
import ArtifactPreview from '../artifacts/ArtifactPreview';
import InlinePresentationViewer from '../InlinePresentationViewer';
import {
  LEFT_SIDEBAR_RAIL_OVERLAP_CSS_VAR,
  RIGHT_SIDEBAR_RAIL_OVERLAP_CSS_VAR,
} from '../../hooks/useRightSidebarInsetCssVar';

/** Поверх messages + input внутри `.fullscreen-chat`. */
export default function ChatWorkspaceViewerOverlay() {
  const { session, collapse } = useChatWorkspaceViewer();

  useEffect(() => {
    if (!session) return undefined;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        collapse();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [session, collapse]);

  if (!session) return null;

  return (
    <Box
      role="dialog"
      aria-modal="true"
      aria-label={session.title || 'Просмотр'}
      sx={{
        position: 'absolute',
        inset: 0,
        // Ниже LeftBar/RightBar (~1200): иначе оверлей перекрывает rail и нельзя
        // развернуть панели. Отступы ниже уводят chrome из-под overlap.
        zIndex: 30,
        display: 'flex',
        flexDirection: 'column',
        bgcolor: 'background.paper',
        borderLeft: '1px solid',
        borderRight: '1px solid',
        borderColor: 'divider',
        boxSizing: 'border-box',
        pl: `var(${LEFT_SIDEBAR_RAIL_OVERLAP_CSS_VAR}, 0px)`,
        pr: `var(${RIGHT_SIDEBAR_RAIL_OVERLAP_CSS_VAR}, 0px)`,
      }}
    >
      <Box
        sx={{
          flexShrink: 0,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 1,
          px: 1.5,
          py: 0.75,
          borderBottom: '1px solid',
          borderColor: 'divider',
          bgcolor: (t) =>
            t.palette.mode === 'dark' ? 'rgba(255,255,255,0.04)' : 'rgba(0,0,0,0.02)',
          minWidth: 0,
        }}
      >
        <Typography
          variant="caption"
          sx={{
            fontWeight: 600,
            letterSpacing: 0.02,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            minWidth: 0,
            flex: 1,
          }}
        >
          {session.title || 'Просмотр'}
        </Typography>
        <Tooltip title="Свернуть">
          <IconButton
            size="small"
            onClick={collapse}
            aria-label="Свернуть"
            sx={{ flexShrink: 0 }}
          >
            <CollapseIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      </Box>
      <Box
        sx={{
          flex: 1,
          minHeight: 0,
          position: 'relative',
          bgcolor: '#e8eaed',
          overflow: 'hidden',
        }}
      >
        {session.kind === 'artifact' ? (
          <Box sx={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }}>
            <ArtifactPreview artifact={session.artifact} />
          </Box>
        ) : (
          <InlinePresentationViewer html={session.html} embedded />
        )}
      </Box>
    </Box>
  );
}
