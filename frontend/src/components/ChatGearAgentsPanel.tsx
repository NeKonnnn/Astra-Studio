import React, { useEffect, useMemo, useState } from 'react';
import {
  Box,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from '@mui/material';
import {
  Search as SearchIcon,
} from '@mui/icons-material';
import {
  MENU_ACTION_TEXT_SIZE,
} from '../constants/menuStyles';
import ChatGearMyAgentsTab from './ChatGearMyAgentsTab';
import ChatGearGalleryAgentsTab from './ChatGearGalleryAgentsTab';
import { getActiveAgentFromStorage } from './AgentSelector';
import { clearActiveAgent } from '../utils/clearActiveAgent';

interface ChatGearAgentsPanelProps {
  isDarkMode: boolean;
}

export default function ChatGearAgentsPanel({
  isDarkMode,
}: ChatGearAgentsPanelProps) {
  const [agentSearch, setAgentSearch] = useState('');
  const [agentsSubtab, setAgentsSubtab] = useState<'my' | 'gallery'>('my');
  const [activeAgent, setActiveAgent] = useState(() => getActiveAgentFromStorage());

  const text = isDarkMode ? 'rgba(255,255,255,0.95)' : 'rgba(0,0,0,0.9)';
  const border = isDarkMode ? 'rgba(255,255,255,0.12)' : 'rgba(0,0,0,0.12)';
  const placeholderColor = isDarkMode ? 'rgba(255,255,255,0.3)' : 'rgba(0,0,0,0.35)';
  const menuDividerBorder = isDarkMode ? 'rgba(255,255,255,0.12)' : 'rgba(0,0,0,0.08)';
  const subtleColor = isDarkMode ? 'rgba(255,255,255,0.35)' : 'rgba(0,0,0,0.45)';
  const muted = isDarkMode ? 'rgba(255,255,255,0.65)' : 'rgba(0,0,0,0.6)';

  useEffect(() => {
    const onAgentSelected = () => setActiveAgent(getActiveAgentFromStorage());
    window.addEventListener('agentSelected', onAgentSelected);
    return () => window.removeEventListener('agentSelected', onAgentSelected);
  }, []);

  const tabs = useMemo(
    () => (
      <ToggleButtonGroup
        exclusive
        value={agentsSubtab}
        onChange={(_, v: 'my' | 'gallery' | null) => {
          if (v) setAgentsSubtab(v);
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
        <ToggleButton value="my">Мои агенты</ToggleButton>
        <ToggleButton value="gallery">Агенты из галереи</ToggleButton>
      </ToggleButtonGroup>
    ),
    [agentsSubtab, border, menuDividerBorder],
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
            placeholder="Поиск агентов..."
            value={agentSearch}
            onChange={(e: React.ChangeEvent<HTMLInputElement>) => setAgentSearch(e.target.value)}
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

      {activeAgent ? (
        <Typography
          component="button"
          onClick={() => {
            clearActiveAgent();
            setActiveAgent(null);
          }}
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
          Сбросить выбор (1)
        </Typography>
      ) : null}

      {agentsSubtab === 'gallery' ? (
        <ChatGearGalleryAgentsTab
          isDarkMode={isDarkMode}
          searchQuery={agentSearch}
          visible={agentsSubtab === 'gallery'}
        />
      ) : (
        <ChatGearMyAgentsTab isDarkMode={isDarkMode} searchQuery={agentSearch} visible={agentsSubtab === 'my'} />
      )}
    </Box>
  );
}
