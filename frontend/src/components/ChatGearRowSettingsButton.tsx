import React from 'react';
import { IconButton, Tooltip } from '@mui/material';
import { SettingsOutlined as SettingsIcon } from '@mui/icons-material';
import { ASTRA_CLOSE_CHAT_GEAR } from '../constants/hotkeys';

interface ChatGearRowSettingsButtonProps {
  title?: string;
  color?: string;
  onOpen: () => void;
}

/** Шестерёнка справа на строке инструментов: открыть настройки сущности и закрыть меню. */
export default function ChatGearRowSettingsButton({
  title = 'Настройки',
  color,
  onOpen,
}: ChatGearRowSettingsButtonProps): React.ReactElement {
  return (
    <Tooltip title={title}>
      <IconButton
        size="small"
        aria-label={title}
        onClick={(e) => {
          e.stopPropagation();
          onOpen();
          window.dispatchEvent(new CustomEvent(ASTRA_CLOSE_CHAT_GEAR));
        }}
        sx={{ p: 0.25, color, flexShrink: 0 }}
      >
        <SettingsIcon sx={{ fontSize: 16 }} />
      </IconButton>
    </Tooltip>
  );
}
