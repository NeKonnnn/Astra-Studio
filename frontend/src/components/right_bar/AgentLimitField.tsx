import React from 'react';
import { Box, TextField, Tooltip, Typography } from '@mui/material';
import HelpOutlineIcon from '@mui/icons-material/HelpOutline';
import type { SxProps, Theme } from '@mui/material/styles';

interface AgentLimitFieldProps {
  label: string;
  tooltip: string;
  value: number | '';
  onChange: (value: number | '') => void;
  defaultLimit: number;
  maxLimit: number;
  readOnly?: boolean;
  panelChrome: {
    fgSubtle: string;
    fgMuted: string;
  };
  categoryFieldSx?: SxProps<Theme>;
}

export default function AgentLimitField({
  label,
  tooltip,
  value,
  onChange,
  defaultLimit,
  maxLimit,
  readOnly = false,
  panelChrome,
  categoryFieldSx,
}: AgentLimitFieldProps) {
  return (
    <Box sx={{ minWidth: 0, mt: 1 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mb: 0.75 }}>
        <Typography
          variant="caption"
          sx={{
            color: 'inherit',
            opacity: 0.65,
            fontWeight: 600,
            textTransform: 'uppercase',
            letterSpacing: '0.06em',
            fontSize: '0.7rem',
          }}
        >
          {label}
        </Typography>
        <Tooltip title={tooltip} arrow>
          <HelpOutlineIcon sx={{ fontSize: 13, color: panelChrome.fgSubtle, cursor: 'help' }} />
        </Tooltip>
      </Box>
      <TextField
        fullWidth
        size="small"
        type="number"
        disabled={readOnly}
        value={value}
        placeholder={`По умолчанию: ${defaultLimit}`}
        onChange={(e) => {
          const raw = e.target.value;
          if (raw === '') {
            onChange('');
            return;
          }
          const n = Number(raw);
          if (!Number.isFinite(n)) return;
          onChange(Math.max(1, Math.min(Math.trunc(n), maxLimit)));
        }}
        inputProps={{ min: 1, max: maxLimit, step: 1 }}
        sx={categoryFieldSx}
      />
    </Box>
  );
}
