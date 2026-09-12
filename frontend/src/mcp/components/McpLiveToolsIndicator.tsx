import React from 'react';
import { Box, Chip, CircularProgress, Typography } from '@mui/material';
import HubIcon from '../../icons/McpIcon';
import type { McpToolCallRecord } from '../types';

interface McpLiveToolsIndicatorProps {
  tools: McpToolCallRecord[];
}

/** Live-индикатор вызова MCP tools во время генерации. */
export default function McpLiveToolsIndicator({ tools }: McpLiveToolsIndicatorProps) {
  if (!tools.length) return null;

  return (
    <Box sx={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 0.75, mb: 1, px: 0.5 }}>
      <CircularProgress size={14} sx={{ flexShrink: 0 }} />
      <HubIcon sx={{ fontSize: 16, color: 'primary.main' }} />
      <Typography variant="caption" color="primary" sx={{ fontWeight: 600 }}>
        MCP:
      </Typography>
      {tools.map((t) => (
        <Chip
          key={`${t.qualified_name}-${t.model || ''}`}
          size="small"
          label={t.model ? `${t.tool} (${t.model})` : t.tool}
          color="primary"
          variant="outlined"
        />
      ))}
    </Box>
  );
}
