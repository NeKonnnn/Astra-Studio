import React, { useMemo } from 'react';
import { Box } from '@mui/material';
import { useNavigate } from 'react-router-dom';
import { useAppActions, useAppContext } from '../contexts/AppContext';
import GenerationCompleteToast from './GenerationCompleteToast';

/**
 * Очередь плашек завершения генерации в чужом чате — справа сверху.
 */
export default function GenerationCompleteToastHost() {
  const { state } = useAppContext();
  const { setCurrentChat, removeGenerationCompleteToast } = useAppActions();
  const navigate = useNavigate();
  const visible = useMemo(
    () => state.generationCompleteToasts.slice(-3),
    [state.generationCompleteToasts],
  );

  if (visible.length === 0) return null;

  return (
    <Box
      sx={{
        position: 'fixed',
        top: 12,
        right: 12,
        zIndex: (theme) => theme.zIndex.snackbar + 5,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'flex-end',
        gap: 1,
        maxWidth: 'min(420px, calc(100vw - 24px))',
        pointerEvents: 'none',
        '& > *': { pointerEvents: 'auto' },
      }}
    >
      {visible.map((t) => (
        <GenerationCompleteToast
          key={t.id}
          title={t.title}
          preview={t.preview}
          onClose={() => removeGenerationCompleteToast(t.id)}
          onOpen={() => {
            removeGenerationCompleteToast(t.id);
            setCurrentChat(t.chatId);
            navigate('/');
          }}
        />
      ))}
    </Box>
  );
}
