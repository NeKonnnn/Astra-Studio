import React, { useCallback, useEffect, useState } from 'react';
import { Box, Chip, CircularProgress, IconButton, Typography } from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import { fetchIntegrationsConfig } from '../../integrations/config';
import type { IntegrationsPublicConfig } from '../../integrations/types';
import { INTEGRATIONS_CATALOG } from '../../integrations/catalog';
import { IntegrationIcon } from '../../integrations/IntegrationIcons';
import { getAdminCardSx } from './adminSurfaces';

interface AdminIntegrationsPanelProps {
  isDarkMode?: boolean;
}

export default function AdminIntegrationsPanel({ isDarkMode = true }: AdminIntegrationsPanelProps) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [config, setConfig] = useState<IntegrationsPublicConfig | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setConfig(await fetchIntegrationsConfig(true));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Ошибка загрузки');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <Typography variant="h6" sx={{ flex: 1 }}>
          Интеграции
        </Typography>
        <IconButton size="small" onClick={() => void load()} disabled={loading}>
          {loading ? <CircularProgress size={18} /> : <RefreshIcon fontSize="small" />}
        </IconButton>
      </Box>
      <Typography variant="body2" color="text.secondary">
        Client ID / секреты задаются через ENV. В чате: Инструменты → Интеграции.
      </Typography>
      {error && (
        <Typography color="error" variant="body2">
          {error}
        </Typography>
      )}
      <Box sx={{ display: 'grid', gap: 1.5 }}>
        {INTEGRATIONS_CATALOG.map((item) => {
          const enabled = Boolean(config?.features?.[item.featureKey]);
          const cfg = config?.[item.configKey];
          return (
            <Box key={item.id} sx={{ ...getAdminCardSx(isDarkMode), p: 1.5, display: 'flex', gap: 1.25, alignItems: 'center' }}>
              <IntegrationIcon id={item.id} />
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <Typography variant="subtitle2">{item.label}</Typography>
                <Typography variant="caption" color="text.secondary" noWrap>
                  {cfg?.client_id ? `client_id: ${cfg.client_id}` : 'client_id не задан'}
                </Typography>
              </Box>
              <Chip
                size="small"
                label={enabled ? (cfg?.configured ? 'OK' : 'Нет CLIENT_ID') : 'Выкл'}
                color={enabled && cfg?.configured ? 'success' : 'default'}
                variant="outlined"
              />
            </Box>
          );
        })}
      </Box>
    </Box>
  );
}
