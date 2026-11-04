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
import { getAdminCardSx } from './adminSurfaces';
import {
  AdminAreaTrendCard,
  AdminDonutCard,
  calcSeriesTrendPct,
} from './AdminCharts';

interface AdminSessionsPanelProps {
  isDarkMode?: boolean;
}

type AuthSummary = {
  logins_24h: number;
  logouts_24h: number;
  logins_7d: number;
  logouts_7d: number;
  unique_logins_7d: number;
};

type AuthPoint = { date: string; logins: number; logouts: number };
type AuthUserRow = {
  user_id: string;
  logins: number;
  logouts: number;
  last_login_at?: string | null;
  last_logout_at?: string | null;
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

/** Логины / логауты: общая стата + по пользователям. */
export default function AdminSessionsPanel({ isDarkMode = true }: AdminSessionsPanelProps) {
  const theme = useTheme();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [summary, setSummary] = useState<AuthSummary>({
    logins_24h: 0,
    logouts_24h: 0,
    logins_7d: 0,
    logouts_7d: 0,
    unique_logins_7d: 0,
  });
  const [series, setSeries] = useState<AuthPoint[]>([]);
  const [byUser, setByUser] = useState<AuthUserRow[]>([]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      setLoading(true);
      setError(null);
      try {
        const resp = await authFetch(
          getApiUrl(`${API_ENDPOINTS.ADMIN_AUTH_STATS}?days=14&limit=100`),
        );
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        if (cancelled) return;
        setSummary({
          logins_24h: 0,
          logouts_24h: 0,
          logins_7d: 0,
          logouts_7d: 0,
          unique_logins_7d: 0,
          ...(data.summary || {}),
        });
        setSeries(Array.isArray(data.series) ? data.series : []);
        setByUser(Array.isArray(data.by_user) ? data.by_user : []);
      } catch {
        if (!cancelled) setError('Не удалось загрузить статистику сессий');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loginAreaData = useMemo(
    () => series.map((p) => ({ label: formatDay(p.date), value: Number(p.logins) || 0 })),
    [series],
  );

  const loginTrendPct = useMemo(
    () => calcSeriesTrendPct(loginAreaData.map((p) => p.value)),
    [loginAreaData],
  );

  const authDonut = useMemo(
    () => [
      { name: 'Логины', value: summary.logins_7d, color: theme.palette.success.main },
      { name: 'Логауты', value: summary.logouts_7d, color: theme.palette.warning.main },
    ],
    [summary.logins_7d, summary.logouts_7d, theme.palette.success.main, theme.palette.warning.main],
  );

  const muted = isDarkMode ? 'rgba(255,255,255,0.55)' : 'rgba(0,0,0,0.55)';
  const cardSx = getAdminCardSx(isDarkMode);
  const headColor = isDarkMode ? 'rgba(255,255,255,0.45)' : 'rgba(0,0,0,0.45)';

  const Metric = ({ label, value, hint }: { label: string; value: number | string; hint?: string }) => (
    <Box sx={cardSx}>
      <Typography variant="caption" sx={{ color: muted, display: 'block', mb: 0.75 }}>
        {label}
      </Typography>
      <Typography variant="h4" fontWeight={700}>
        {loading ? '—' : value}
      </Typography>
      {hint ? (
        <Typography variant="caption" sx={{ color: muted, mt: 0.5, display: 'block' }}>
          {hint}
        </Typography>
      ) : null}
    </Box>
  );

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
      {loading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 2 }}>
          <CircularProgress size={28} />
        </Box>
      ) : null}
      {error ? (
        <Typography color="error">{error}</Typography>
      ) : null}

      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr', md: 'repeat(5, 1fr)' },
          gap: 1.5,
        }}
      >
        <Metric label="Логины 24ч" value={summary.logins_24h} />
        <Metric label="Логауты 24ч" value={summary.logouts_24h} />
        <Metric label="Логины 7д" value={summary.logins_7d} />
        <Metric label="Логауты 7д" value={summary.logouts_7d} />
        <Metric
          label="Уник. пользователи 7д"
          value={summary.unique_logins_7d}
          hint="По успешным входам"
        />
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
            data={loginAreaData}
            title="Логины по дням"
            subtitle="Тренд успешных входов"
            kpi={loading ? '—' : summary.logins_7d}
            kpiHint={`Логауты за 7д: ${summary.logouts_7d}`}
            trendPct={loginTrendPct}
            color={theme.palette.success.main}
            valueLabel="Логины"
            height={230}
          />
        </Box>
        <Box sx={cardSx}>
          <AdminDonutCard
            isDarkMode={isDarkMode}
            title="Логины / логауты за 7д"
            subtitle="Доля событий сессий"
            data={authDonut}
            centerValue={summary.logins_7d + summary.logouts_7d}
            centerLabel="событий"
            height={210}
          />
        </Box>
      </Box>

      <Box sx={{ ...cardSx, p: 0, overflow: 'hidden' }}>
        <Typography variant="subtitle2" fontWeight={600} sx={{ px: 2, pt: 1.5, pb: 1 }}>
          По пользователям
        </Typography>
        {byUser.length === 0 && !loading ? (
          <Typography sx={{ p: 2, color: muted }}>Пока нет событий входа/выхода</Typography>
        ) : (
          <Box sx={{ overflow: 'auto', maxHeight: 420 }}>
            <Table size="small" stickyHeader>
              <TableHead>
                <TableRow>
                  {['Пользователь', 'Логины', 'Логауты', 'Последний вход', 'Последний выход'].map((h) => (
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
                  <TableRow key={row.user_id} hover>
                    <TableCell sx={{ fontSize: '0.82rem', fontWeight: 600 }}>{row.user_id}</TableCell>
                    <TableCell>{row.logins}</TableCell>
                    <TableCell>{row.logouts}</TableCell>
                    <TableCell sx={{ fontSize: '0.78rem', opacity: 0.8 }}>{formatTs(row.last_login_at)}</TableCell>
                    <TableCell sx={{ fontSize: '0.78rem', opacity: 0.8 }}>{formatTs(row.last_logout_at)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Box>
        )}
      </Box>
    </Box>
  );
}
