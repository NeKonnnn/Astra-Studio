import React, { useEffect, useMemo } from 'react';
import { Box } from '@mui/material';
import { useAppActions, useAppContext } from '../contexts/AppContext';
import {
  STATUS_TOAST_SHORT_DISMISS_MS,
  type StatusToastType,
} from '../constants/statusToast';
import StatusToast from './StatusToast';

const VALID_TYPES: StatusToastType[] = ['success', 'warning', 'error', 'info'];

function normalizeType(type: string): StatusToastType {
  return VALID_TYPES.includes(type as StatusToastType) ? (type as StatusToastType) : 'info';
}

/**
 * Рендерит очередь `state.notifications` из AppContext тёмными StatusToast.
 * Монтировать внутри AppProvider (например в App.tsx).
 */
export default function StatusToastHost() {
  const { state } = useAppContext();
  const { removeNotification } = useAppActions();
  const notifications = state.notifications;

  // Показываем последние несколько, чтобы не заслонять экран
  const visible = useMemo(() => notifications.slice(-4), [notifications]);

  useEffect(() => {
    if (notifications.length <= 8) return;
    // Подчищаем хвост очереди, если накопилось слишком много
    const stale = notifications.slice(0, notifications.length - 8);
    stale.forEach((n) => removeNotification(n.id));
  }, [notifications, removeNotification]);

  if (visible.length === 0) return null;

  return (
    <Box
      sx={{
        position: 'fixed',
        top: 12,
        left: '50%',
        transform: 'translateX(-50%)',
        zIndex: (theme) => theme.zIndex.snackbar + 4,
        display: 'flex',
        flexDirection: 'column',
        gap: 1,
        pointerEvents: 'none',
        '& > *': { pointerEvents: 'auto' },
      }}
    >
      {visible.map((n) => (
        <StatusToast
          key={n.id}
          type={normalizeType(n.type)}
          message={n.message}
          onClose={() => removeNotification(n.id)}
          autoDismissMs={STATUS_TOAST_SHORT_DISMISS_MS}
          ariaLabel="Закрыть уведомление"
        />
      ))}
    </Box>
  );
}
