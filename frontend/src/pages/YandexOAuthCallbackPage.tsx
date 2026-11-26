import React, { useEffect, useState } from 'react';
import { Box, CircularProgress, Typography } from '@mui/material';
import {
  parseOAuthHash,
  YANDEX_OAUTH_MESSAGE_TYPE,
} from '../integrations/yandex-disk/oauth';
import { INTEGRATION_OAUTH_MESSAGE } from '../integrations/shared/oauthPopup';

/**
 * Callback для implicit OAuth Яндекса (`response_type=token`).
 * Токен приходит в hash; передаём его opener через postMessage и закрываем окно.
 */
export default function YandexOAuthCallbackPage() {
  const [status, setStatus] = useState('Завершение авторизации…');

  useEffect(() => {
    const { accessToken, expiresIn, error } = parseOAuthHash(window.location.hash);

    if (window.opener && !window.opener.closed) {
      const payload = {
        accessToken,
        expiresIn,
        error: error || (!accessToken ? 'access_token отсутствует' : null),
      };
      window.opener.postMessage(
        { type: YANDEX_OAUTH_MESSAGE_TYPE, ...payload },
        window.location.origin,
      );
      window.opener.postMessage(
        { type: INTEGRATION_OAUTH_MESSAGE, ...payload },
        window.location.origin,
      );
    }

    if (error || !accessToken) {
      setStatus(error || 'Не удалось получить токен Яндекса');
      return;
    }

    setStatus('Готово. Можно закрыть окно.');
    const t = window.setTimeout(() => {
      try {
        window.close();
      } catch {
        /* ignore */
      }
    }, 400);
    return () => window.clearTimeout(t);
  }, []);

  return (
    <Box
      sx={{
        minHeight: '100vh',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 2,
        p: 3,
      }}
    >
      <CircularProgress size={28} />
      <Typography variant="body2" color="text.secondary" align="center">
        {status}
      </Typography>
    </Box>
  );
}
