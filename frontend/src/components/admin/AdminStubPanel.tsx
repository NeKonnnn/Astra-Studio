import React from 'react';
import { Box, Paper, Typography } from '@mui/material';

interface AdminStubPanelProps {
  title: string;
  description: string;
  isDarkMode?: boolean;
}

/** Заглушка вкладки админ-панели до подключения API. */
export default function AdminStubPanel({
  title,
  description,
  isDarkMode = true,
}: AdminStubPanelProps) {
  const border = isDarkMode ? '1px solid rgba(255,255,255,0.1)' : '1px solid rgba(0,0,0,0.1)';
  const muted = isDarkMode ? 'rgba(255,255,255,0.55)' : 'rgba(0,0,0,0.55)';

  return (
    <Paper
      elevation={0}
      sx={{
        p: 3,
        border,
        borderRadius: 2,
        bgcolor: isDarkMode ? 'rgba(255,255,255,0.03)' : 'rgba(0,0,0,0.02)',
      }}
    >
      <Typography variant="h6" fontWeight={600} sx={{ mb: 1 }}>
        {title}
      </Typography>
      <Typography variant="body2" sx={{ color: muted, mb: 2, maxWidth: 640 }}>
        {description}
      </Typography>
      <Box
        sx={{
          py: 4,
          textAlign: 'center',
          borderRadius: 1,
          border: isDarkMode ? '1px dashed rgba(255,255,255,0.15)' : '1px dashed rgba(0,0,0,0.15)',
          color: muted,
          fontSize: '0.85rem',
        }}
      >
        Раздел в разработке — API и таблицы появятся на следующих итерациях
      </Box>
    </Paper>
  );
}
