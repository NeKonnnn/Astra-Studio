import React, { useEffect, useMemo, useState } from 'react';
import {
  Box,
  CircularProgress,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
  useTheme,
} from '@mui/material';
import { authFetch, getApiUrl, API_ENDPOINTS } from '../../config/api';
import type { AdminUsageEntityType } from './AdminHubTabs';
import { getAdminCardSx } from './adminSurfaces';
import {
  AdminAreaTrendCard,
  AdminDonutCard,
  calcSeriesTrendPct,
} from './AdminCharts';

interface AdminUsagePanelProps {
  isDarkMode?: boolean;
  /** Один дашборд = один тип события/сущности. */
  entityType: AdminUsageEntityType;
  title?: string;
}

type TopRow = {
  entity_type: string;
  entity_id: string;
  entity_name: string;
  uses: number;
  unique_users: number;
};

type UserEntityRow = {
  user_id: string;
  entity_type: string;
  entity_id: string;
  entity_name: string;
  uses: number;
  last_used_at?: string | null;
};

const LABELS: Record<AdminUsageEntityType, string> = {
  agent: 'Агенты',
  skill: 'Skills',
  mcp: 'MCP',
  knowledge_base: 'Базы знаний',
  plugin: 'Плагины',
};

function formatDay(iso: string): string {
  const p = String(iso || '').split('-');
  return p.length === 3 ? `${p[2]}.${p[1]}` : iso;
}

function formatTs(iso?: string | null): string {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

/** Дашборд использования одного типа сущностей. */
export default function AdminUsagePanel({
  isDarkMode = true,
  entityType,
  title,
}: AdminUsagePanelProps) {
  const theme = useTheme();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [total7d, setTotal7d] = useState(0);
  const [series, setSeries] = useState<Array<{ date: string; uses: number }>>([]);
  const [top, setTop] = useState<TopRow[]>([]);
  const [byUser, setByUser] = useState<UserEntityRow[]>([]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      setLoading(true);
      setError(null);
      try {
        const qs = new URLSearchParams({
          days: '14',
          limit: '100',
          entity_type: entityType,
        });
        const resp = await authFetch(getApiUrl(`${API_ENDPOINTS.ADMIN_USAGE}?${qs}`));
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        if (cancelled) return;
        const summary = data.summary_7d || {};
        setTotal7d(Number(summary[entityType] || 0));
        setSeries(
          (Array.isArray(data.series) ? data.series : []).map(
            (p: { date?: string; uses?: number }) => ({
              date: String(p.date || ''),
              uses: Number(p.uses || 0),
            }),
          ),
        );
        setTop(Array.isArray(data.top) ? data.top : []);
        setByUser(Array.isArray(data.by_user) ? data.by_user : []);
      } catch {
        if (!cancelled) setError('Не удалось загрузить статистику использования');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [entityType]);

  const areaData = useMemo(
    () => series.map((p) => ({ label: formatDay(p.date), value: Number(p.uses) || 0 })),
    [series],
  );

  const usesTrendPct = useMemo(
    () => calcSeriesTrendPct(areaData.map((p) => p.value)),
    [areaData],
  );

  const topDonut = useMemo(() => {
    const TOP_N = 5;
    const sorted = [...top].sort((a, b) => b.uses - a.uses);
    const head = sorted.slice(0, TOP_N);
    const rest = sorted.slice(TOP_N);
    const otherUses = rest.reduce((acc, r) => acc + (Number(r.uses) || 0), 0);
    const slices = head.map((r) => ({
      name: r.entity_name || r.entity_id,
      value: Number(r.uses) || 0,
    }));
    if (otherUses > 0) slices.push({ name: 'Прочие', value: otherUses });
    return slices;
  }, [top]);

  const uniqueUsers = useMemo(() => {
    const set = new Set(byUser.map((r) => r.user_id));
    return set.size;
  }, [byUser]);

  const muted = isDarkMode ? 'rgba(255,255,255,0.55)' : 'rgba(0,0,0,0.55)';
  const cardSx = getAdminCardSx(isDarkMode);
  const headColor = isDarkMode ? 'rgba(255,255,255,0.45)' : 'rgba(0,0,0,0.45)';
  const label = title || LABELS[entityType];

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
      {loading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 2 }}>
          <CircularProgress size={28} />
        </Box>
      ) : null}
      {error ? <Typography color="error">{error}</Typography> : null}

      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr 1fr' },
          gap: 1.5,
        }}
      >
        <Box sx={cardSx}>
          <Typography variant="caption" sx={{ color: muted, display: 'block', mb: 0.5 }}>
            Использований за 7д
          </Typography>
          <Typography variant="h4" fontWeight={700}>
            {loading ? '—' : total7d}
          </Typography>
        </Box>
        <Box sx={cardSx}>
          <Typography variant="caption" sx={{ color: muted, display: 'block', mb: 0.5 }}>
            Уникальных сущностей (топ)
          </Typography>
          <Typography variant="h4" fontWeight={700}>
            {loading ? '—' : top.length}
          </Typography>
        </Box>
        <Box sx={cardSx}>
          <Typography variant="caption" sx={{ color: muted, display: 'block', mb: 0.5 }}>
            Пользователей с событиями
          </Typography>
          <Typography variant="h4" fontWeight={700}>
            {loading ? '—' : uniqueUsers}
          </Typography>
        </Box>
      </Box>

      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: { xs: '1fr', lg: '1.35fr 1fr' },
          gap: 1.5,
        }}
      >
        <Box sx={cardSx}>
          <AdminAreaTrendCard
            isDarkMode={isDarkMode}
            data={areaData}
            title={`${label}: использования по дням`}
            subtitle="Тренд событий за период"
            kpi={loading ? '—' : total7d}
            kpiHint="Сумма за 7 дней"
            trendPct={usesTrendPct}
            color={theme.palette.success.main}
            valueLabel="Использования"
            height={230}
          />
        </Box>
        <Box sx={cardSx}>
          <AdminDonutCard
            isDarkMode={isDarkMode}
            title="Топ сущностей"
            subtitle="Доля uses (топ‑5 + прочие)"
            data={topDonut}
            centerValue={topDonut.reduce((a, s) => a + s.value, 0) || total7d}
            centerLabel="uses"
            height={210}
          />
        </Box>
      </Box>

      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' },
          gap: 1.5,
        }}
      >
        <Box sx={{ ...cardSx, p: 0, overflow: 'hidden' }}>
          <Typography variant="subtitle2" fontWeight={600} sx={{ px: 2, pt: 1.5, pb: 1 }}>
            Чем пользуются чаще всего
          </Typography>
          {top.length === 0 && !loading ? (
            <Typography sx={{ p: 2, color: muted }}>Пока нет событий</Typography>
          ) : (
            <Box sx={{ overflow: 'auto', maxHeight: 380 }}>
              <Table size="small" stickyHeader>
                <TableHead>
                  <TableRow>
                    {['Имя / ID', 'Uses', 'Users'].map((h) => (
                      <TableCell
                        key={h}
                        sx={{
                          bgcolor: isDarkMode ? 'rgba(30,30,34,0.95)' : 'rgba(250,250,250,0.95)',
                          color: headColor,
                          fontSize: '0.72rem',
                          fontWeight: 600,
                        }}
                      >
                        {h}
                      </TableCell>
                    ))}
                  </TableRow>
                </TableHead>
                <TableBody>
                  {top.map((row) => (
                    <TableRow key={row.entity_id} hover>
                      <TableCell>
                        <Typography variant="body2" fontWeight={600} noWrap sx={{ maxWidth: 240 }}>
                          {row.entity_name || row.entity_id}
                        </Typography>
                        <Typography variant="caption" sx={{ color: muted }}>
                          {row.entity_id}
                        </Typography>
                      </TableCell>
                      <TableCell>{row.uses}</TableCell>
                      <TableCell>{row.unique_users}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Box>
          )}
        </Box>

        <Box sx={{ ...cardSx, p: 0, overflow: 'hidden' }}>
          <Typography variant="subtitle2" fontWeight={600} sx={{ px: 2, pt: 1.5, pb: 1 }}>
            Какой пользователь чем пользовался
          </Typography>
          {byUser.length === 0 && !loading ? (
            <Typography sx={{ p: 2, color: muted }}>Нет связок пользователь ↔ сущность</Typography>
          ) : (
            <Box sx={{ overflow: 'auto', maxHeight: 380 }}>
              <Table size="small" stickyHeader>
                <TableHead>
                  <TableRow>
                    {['Пользователь', 'Сущность', 'Uses', 'Последний раз'].map((h) => (
                      <TableCell
                        key={h}
                        sx={{
                          bgcolor: isDarkMode ? 'rgba(30,30,34,0.95)' : 'rgba(250,250,250,0.95)',
                          color: headColor,
                          fontSize: '0.72rem',
                          fontWeight: 600,
                        }}
                      >
                        {h}
                      </TableCell>
                    ))}
                  </TableRow>
                </TableHead>
                <TableBody>
                  {byUser.map((row) => (
                    <TableRow key={`${row.user_id}:${row.entity_id}`} hover>
                      <TableCell sx={{ fontSize: '0.8rem', fontWeight: 600 }}>
                        {row.user_id}
                      </TableCell>
                      <TableCell>
                        <Typography variant="body2" noWrap sx={{ maxWidth: 180 }}>
                          {row.entity_name || row.entity_id}
                        </Typography>
                      </TableCell>
                      <TableCell>{row.uses}</TableCell>
                      <TableCell sx={{ fontSize: '0.75rem', opacity: 0.8 }}>
                        {formatTs(row.last_used_at)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Box>
          )}
        </Box>
      </Box>
    </Box>
  );
}
