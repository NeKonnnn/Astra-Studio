import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Box, Chip, CircularProgress, Typography } from '@mui/material';
import {
  MENU_ACTION_TEXT_SIZE,
  CHAT_GEAR_SCROLL_AREA_NO_VISIBLE_SCROLLBAR_SX,
  getDropdownItemSx,
} from '../constants/menuStyles';
import {
  INTEGRATIONS_CATALOG,
  type IntegrationCatalogItem,
  type IntegrationId,
} from '../integrations/catalog';
import { IntegrationIcon } from '../integrations/IntegrationIcons';
import { fetchIntegrationsConfig } from '../integrations/config';
import type { IntegrationsPublicConfig } from '../integrations/types';

interface ChatGearIntegrationsPanelProps {
  isDarkMode: boolean;
  onIntegrationClick?: (id: IntegrationId) => void;
}

export default function ChatGearIntegrationsPanel({
  isDarkMode,
  onIntegrationClick,
}: ChatGearIntegrationsPanelProps) {
  const [loading, setLoading] = useState(true);
  const [cfg, setCfg] = useState<IntegrationsPublicConfig | null>(null);

  const muted = isDarkMode ? 'rgba(255,255,255,0.65)' : 'rgba(0,0,0,0.6)';
  const text = isDarkMode ? 'rgba(255,255,255,0.95)' : 'rgba(0,0,0,0.9)';
  const dropdownItemSx = useMemo(() => getDropdownItemSx(isDarkMode), [isDarkMode]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setCfg(await fetchIntegrationsConfig(true));
    } catch {
      setCfg(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const isFeatureOn = (item: IntegrationCatalogItem): boolean =>
    Boolean(cfg?.features?.[item.featureKey]);

  const isConfigured = (item: IntegrationCatalogItem): boolean =>
    Boolean(cfg?.[item.configKey]?.configured);

  const isRunnable = (item: IntegrationCatalogItem): boolean =>
    item.status === 'available' && isFeatureOn(item) && isConfigured(item);

  const chipLabel = (item: IntegrationCatalogItem): string => {
    if (item.status === 'soon') return 'Скоро';
    if (!isFeatureOn(item)) return 'Выкл.';
    if (!isConfigured(item)) return 'Нужен CLIENT_ID';
    return 'Открыть';
  };

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0, overflow: 'hidden' }}>
      <Box sx={{ px: 1.25, pt: 1, pb: 0.5 }}>
        <Typography sx={{ fontSize: MENU_ACTION_TEXT_SIZE, fontWeight: 600, color: text }}>
          Интеграции
        </Typography>
        <Typography sx={{ fontSize: '0.72rem', color: muted, mt: 0.25 }}>
          Яндекс, Google, Microsoft, Notion, GitHub — подключение и вложения в чат.
        </Typography>
      </Box>

      {loading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}>
          <CircularProgress size={22} />
        </Box>
      ) : (
        <Box
          sx={{
            ...CHAT_GEAR_SCROLL_AREA_NO_VISIBLE_SCROLLBAR_SX,
            flex: 1,
            minHeight: 0,
            overflowY: 'auto',
            px: 0.5,
            pb: 1,
          }}
        >
          {INTEGRATIONS_CATALOG.map((item) => {
            const runnable = isRunnable(item);
            return (
              <Box
                key={item.id}
                onClick={() => runnable && onIntegrationClick?.(item.id)}
                sx={{
                  ...dropdownItemSx,
                  display: 'flex',
                  alignItems: 'center',
                  gap: 1,
                  opacity: runnable ? 1 : 0.72,
                  cursor: runnable ? 'pointer' : 'default',
                  color: text,
                }}
              >
                <IntegrationIcon id={item.id} size={32} />
                <Box sx={{ flex: 1, minWidth: 0 }}>
                  <Typography
                    sx={{
                      fontSize: MENU_ACTION_TEXT_SIZE,
                      fontWeight: 600,
                      whiteSpace: 'nowrap',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                    }}
                  >
                    {item.label}
                  </Typography>
                  <Typography
                    sx={{
                      fontSize: '0.68rem',
                      color: muted,
                      whiteSpace: 'nowrap',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                    }}
                  >
                    {item.description}
                  </Typography>
                </Box>
                <Chip
                  size="small"
                  label={chipLabel(item)}
                  color={runnable ? 'primary' : 'default'}
                  variant="outlined"
                  sx={{ height: 22, fontSize: '0.65rem' }}
                />
              </Box>
            );
          })}
        </Box>
      )}
    </Box>
  );
}
