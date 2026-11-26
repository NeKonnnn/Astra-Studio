import React, { useEffect, useState } from 'react';
import { Box, CircularProgress, Typography } from '@mui/material';
import { INTEGRATION_OAUTH_MESSAGE, parseOAuthHash } from '../integrations/shared/oauthPopup';
import { YANDEX_OAUTH_MESSAGE_TYPE } from '../integrations/yandex-disk/oauth';

/**
 * Универсальный OAuth callback:
 * - implicit: #access_token=...
 * - code: ?code=...
 * Совместим и со старым Yandex callback message type.
 */
export default function IntegrationOAuthCallbackPage() {
  const [status, setStatus] = useState('Завершение авторизации…');

  useEffect(() => {
    const hash = parseOAuthHash(window.location.hash);
    const params = new URLSearchParams(window.location.search);
    const code = params.get('code');
    const error = hash.error || params.get('error') || params.get('error_description');

    const accessToken = hash.accessToken;
    const payload = {
      accessToken,
      code,
      expiresIn: hash.expiresIn,
      error: error || (!accessToken && !code ? 'token/code отсутствует' : null),
    };

    if (window.opener && !window.opener.closed) {
      window.opener.postMessage(
        { type: INTEGRATION_OAUTH_MESSAGE, ...payload },
        window.location.origin,
      );
      // backward-compat для Яндекс.Диска
      window.opener.postMessage(
        { type: YANDEX_OAUTH_MESSAGE_TYPE, ...payload },
        window.location.origin,
      );
    }

    if (payload.error) {
      setStatus(String(payload.error));
      return;
    }
    setStatus('Готово. Можно закрыть окно.');
    const t = window.setTimeout(() => {
      try {
        window.close();
      } catch {
        /* ignore */
      }
    }, 350);
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
