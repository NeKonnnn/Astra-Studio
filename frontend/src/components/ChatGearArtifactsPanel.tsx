import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Box, FormControlLabel, Switch, Tooltip, Typography } from '@mui/material';
import { HelpOutline as HelpIcon } from '@mui/icons-material';
import ArtifactsIcon from '../icons/ArtifactsIcon';
import {
  MENU_ACTION_TEXT_SIZE,
  CHAT_GEAR_SCROLL_AREA_NO_VISIBLE_SCROLLBAR_SX,
} from '../constants/menuStyles';
import {
  getChatArtifactsToolsState,
  setChatArtifactsToolMode,
  ARTIFACTS_SELECTION_CHANGED_EVENT,
  type ChatArtifactsToolsState,
} from '../utils/artifactsSelectionStorage';
import { AGENT_ARTIFACTS_CHANGED_EVENT } from '../utils/agentArtifactsEnabled';

interface ChatGearArtifactsPanelProps {
  isDarkMode: boolean;
  chatId?: string | null;
}

const OPTIONS: Array<{
  key: keyof ChatArtifactsToolsState;
  label: string;
  help: string;
}> = [
  {
    key: 'shadcn_enabled',
    label: 'Включить компоненты shadcn/ui',
    help: 'Модель может использовать shadcn/ui в React-артефактах. Нельзя включить вместе с режимом пользовательского промта.',
  },
  {
    key: 'user_prompt_mode',
    label: 'Режим пользовательского промта',
    help: 'При включении этого режима системный промт для создания артефактов по умолчанию не будет использован. Все инструкции для генерации артефактов должны задаваться вручную. Нельзя включить вместе с shadcn/ui.',
  },
];

export default function ChatGearArtifactsPanel({ isDarkMode, chatId }: ChatGearArtifactsPanelProps) {
  const [settings, setSettings] = useState<ChatArtifactsToolsState>(() =>
    getChatArtifactsToolsState(chatId),
  );

  const muted = isDarkMode ? 'rgba(255,255,255,0.65)' : 'rgba(0,0,0,0.6)';
  const text = isDarkMode ? 'rgba(255,255,255,0.95)' : 'rgba(0,0,0,0.9)';
  const border = isDarkMode ? 'rgba(255,255,255,0.12)' : 'rgba(0,0,0,0.12)';
  const helpColor = isDarkMode ? 'rgba(255,255,255,0.35)' : 'rgba(0,0,0,0.4)';

  const sync = useCallback(() => {
    setSettings(getChatArtifactsToolsState(chatId));
  }, [chatId]);

  useEffect(() => {
    sync();
    const onArtifacts = (e: Event) => {
      const detailChatId = (e as CustomEvent<{ chatId?: string }>).detail?.chatId;
      if (detailChatId && chatId && detailChatId !== chatId) return;
      sync();
    };
    window.addEventListener(ARTIFACTS_SELECTION_CHANGED_EVENT, onArtifacts as EventListener);
    window.addEventListener(AGENT_ARTIFACTS_CHANGED_EVENT, sync);
    window.addEventListener('agentSelected', sync as EventListener);
    window.addEventListener('storage', sync);
    return () => {
      window.removeEventListener(ARTIFACTS_SELECTION_CHANGED_EVENT, onArtifacts as EventListener);
      window.removeEventListener(AGENT_ARTIFACTS_CHANGED_EVENT, sync);
      window.removeEventListener('agentSelected', sync as EventListener);
      window.removeEventListener('storage', sync);
    };
  }, [chatId, sync]);

  const handleToggle = useCallback(
    (key: keyof ChatArtifactsToolsState, checked: boolean) => {
      if (!chatId) return;
      const next = setChatArtifactsToolMode(chatId, key, checked);
      setSettings(next);
    },
    [chatId],
  );

  const switchSx = useMemo(
    () => ({
      '& .MuiSwitch-switchBase.Mui-checked': { color: '#2196f3' },
      '& .MuiSwitch-switchBase.Mui-checked + .MuiSwitch-track': { bgcolor: 'rgba(33,150,243,0.5)' },
      '& .MuiSwitch-track': {
        bgcolor: isDarkMode ? 'rgba(255,255,255,0.2)' : 'rgba(0,0,0,0.2)',
      },
    }),
    [isDarkMode],
  );

  if (!chatId) {
    return (
      <Box sx={{ p: 1.5, maxWidth: 320 }}>
        <Typography variant="body2" sx={{ color: muted, fontSize: MENU_ACTION_TEXT_SIZE }}>
          Выберите или создайте чат, чтобы настроить артефакты для этого диалога.
        </Typography>
      </Box>
    );
  }

  return (
    <Box
      sx={{
        display: 'flex',
        flexDirection: 'column',
        minHeight: 0,
        height: '100%',
        overflow: 'hidden',
      }}
    >
      <Box
        sx={{
          px: 1.25,
          py: 1,
          borderBottom: `1px solid ${border}`,
          display: 'flex',
          alignItems: 'center',
          gap: 0.75,
          flexShrink: 0,
        }}
      >
        <ArtifactsIcon sx={{ fontSize: 18, color: muted }} />
        <Typography sx={{ fontSize: MENU_ACTION_TEXT_SIZE, fontWeight: 600, color: text }}>
          Артефакты
        </Typography>
      </Box>
      <Box
        sx={{
          flex: 1,
          minHeight: 0,
          overflowY: 'auto',
          px: 1.25,
          py: 1,
          ...CHAT_GEAR_SCROLL_AREA_NO_VISIBLE_SCROLLBAR_SX,
        }}
      >
        <Typography sx={{ fontSize: '0.75rem', color: muted, mb: 1, lineHeight: 1.45 }}>
          Режимы для текущего чата. Можно включить только один: shadcn/ui или свой промпт. Дефолты — из
          карточки активного агента.
        </Typography>
        {OPTIONS.map(({ key, label, help }) => {
          const otherKey: keyof ChatArtifactsToolsState =
            key === 'shadcn_enabled' ? 'user_prompt_mode' : 'shadcn_enabled';
          const blockedByOther = settings[otherKey];
          return (
            <Box
              key={key}
              sx={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: 1,
                py: 0.75,
                opacity: blockedByOther && !settings[key] ? 0.55 : 1,
              }}
            >
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, minWidth: 0, flex: 1 }}>
                <Typography
                  sx={{
                    fontSize: MENU_ACTION_TEXT_SIZE,
                    color: text,
                    lineHeight: 1.35,
                  }}
                >
                  {label}
                </Typography>
                <Tooltip title={help} arrow>
                  <HelpIcon sx={{ fontSize: 14, color: helpColor, flexShrink: 0 }} />
                </Tooltip>
              </Box>
              <FormControlLabel
                control={
                  <Switch
                    size="small"
                    checked={settings[key]}
                    disabled={blockedByOther && !settings[key]}
                    onChange={(e) => handleToggle(key, e.target.checked)}
                    sx={switchSx}
                  />
                }
                label=""
                sx={{ m: 0, flexShrink: 0 }}
              />
            </Box>
          );
        })}
      </Box>
    </Box>
  );
}
