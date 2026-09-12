import React from 'react';
import { Box, Typography } from '@mui/material';
import AgentIcon from '../../icons/AgentIcon';
import ThinkingShimmerText from './ThinkingShimmerText';
/** Заголовок шага цепочки — как ChainAgentUpdate в GPB_ASTRA. */
export default function AgentChainUpdate({
  name,
  isDarkMode,
  thinking,
}: {
  name: string;
  isDarkMode: boolean;
  thinking?: boolean;
}) {
  const title = name.trim() || 'Агент';
  return (
    <Box
      sx={{
        display: 'flex',
        alignItems: 'center',
        gap: 1,
        my: 1.25,
        position: 'relative',
        pl: 0.25,
      }}
    >
      <Box
        sx={{
          width: 22,
          height: 22,
          borderRadius: '50%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          bgcolor: isDarkMode ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.06)',
          flexShrink: 0,
        }}
      >
        <AgentIcon sx={{ fontSize: 14, opacity: 0.75 }} />
      </Box>
      <Typography variant="body2" sx={{ fontWeight: 600, fontSize: '0.88rem' }}>
        {title}
      </Typography>
      {thinking ? (
        <ThinkingShimmerText isDarkMode={isDarkMode} fontSize="0.75rem" fontWeight={500}>
          думает...
        </ThinkingShimmerText>
      ) : null}
    </Box>
  );
}
