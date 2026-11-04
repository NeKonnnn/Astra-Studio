import React, { useEffect, useMemo, useState } from 'react';
import { Box, CircularProgress, Typography, useTheme } from '@mui/material';
import { authFetch, getApiUrl, API_ENDPOINTS } from '../../config/api';
import { fetchAllAgentsForAdmin } from '../../utils/fetchMergedUserAgents';
import { fetchPluginsResponse } from '../../plugins/api';
import { fetchMcpServers, fetchMcpStatus } from '../../mcp/api';
import type { McpPlatformStatus } from '../../mcp/types';
import type { AdminDashboardView, AdminHubSection } from './AdminHubTabs';
import { getAdminCardSx } from './adminSurfaces';
import {
  AdminAreaTrendCard,
  AdminDonutCard,
  calcSeriesTrendPct,
} from './AdminCharts';

interface AdminDashboardPanelProps {
  isDarkMode?: boolean;
  /** Открыть дашборд события (sessions / agent / skill / …). */
  onOpenView?: (view: AdminDashboardView) => void;
  /** Перейти в справочник (агенты, skills, …). */
  onOpenCatalog?: (section: Exclude<AdminHubSection, 'dashboard'>) => void;
}

type DashStats = {
  agentsTotal: number;
  agentsPublic: number;
  skillsTotal: number;
  skillsPublic: number;
  kbTotal: number;
  kbPublic: number;
  pluginsTotal: number;
  pluginsHealthy: number;
  mcpServers: number;
  mcpConnected: number;
};

type ActivitySummary = {
  active_users_24h: number;
  active_users_7d: number;
  logins_24h: number;
  logins_7d: number;
  logouts_24h: number;
  logouts_7d: number;
  unique_logins_7d: number;
  conversations_touched_24h: number;
  agents_created_7d: number;
  skills_created_7d: number;
  usage_7d?: Record<string, number>;
};

type ActivityPoint = {
  date: string;
  active_users: number;
  logins: number;
  logouts?: number;
  conversation_updates: number;
  agents_created: number;
  skills_created: number;
  usage_total?: number;
};

type TopAgent = {
  entity_id: string;
  entity_name: string;
  uses: number;
  unique_users: number;
};

const EMPTY_ACTIVITY: ActivitySummary = {
  active_users_24h: 0,
  active_users_7d: 0,
  logins_24h: 0,
  logins_7d: 0,
  logouts_24h: 0,
  logouts_7d: 0,
  unique_logins_7d: 0,
  conversations_touched_24h: 0,
  agents_created_7d: 0,
  skills_created_7d: 0,
  usage_7d: {},
};

function formatDayLabel(isoDate: string): string {
  const parts = String(isoDate || '').split('-');
  if (parts.length !== 3) return isoDate;
  return `${parts[2]}.${parts[1]}`;
}

/** Дашборд активности / сводка платформы. */
export default function AdminDashboardPanel({
  isDarkMode = true,
  onOpenView,
  onOpenCatalog,
}: AdminDashboardPanelProps) {
  const theme = useTheme();
  const [loading, setLoading] = useState(true);
  const [activityLoading, setActivityLoading] = useState(true);
  const [stats, setStats] = useState<DashStats>({
    agentsTotal: 0,
    agentsPublic: 0,
    skillsTotal: 0,
    skillsPublic: 0,
    kbTotal: 0,
    kbPublic: 0,
    pluginsTotal: 0,
    pluginsHealthy: 0,
    mcpServers: 0,
    mcpConnected: 0,
  });
  const [activity, setActivity] = useState<ActivitySummary>(EMPTY_ACTIVITY);
  const [series, setSeries] = useState<ActivityPoint[]>([]);
  const [topAgents, setTopAgents] = useState<TopAgent[]>([]);
  const [activityError, setActivityError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      setLoading(true);
      try {
        const [agents, skillsResp, kbResp, pluginsCatalog, mcpServers, mcpStatus] =
          await Promise.all([
            fetchAllAgentsForAdmin(),
            authFetch(getApiUrl('/api/skills/list?limit=1000&page=1')),
            authFetch(getApiUrl(`${API_ENDPOINTS.KNOWLEDGE_BASES}/list?limit=200&page=1`)),
            fetchPluginsResponse(true).catch(() => ({
              enabled: true,
              plugins: [] as Array<{ healthy?: boolean | null }>,
            })),
            fetchMcpServers().catch(() => []),
            fetchMcpStatus({ fetchTools: true }).catch(
              (): McpPlatformStatus => ({}),
            ),
          ]);

        let skillsTotal = 0;
        let skillsPublic = 0;
        if (skillsResp.ok) {
          const data = await skillsResp.json();
          const items = data.items || [];
          skillsTotal = Number(data.total) || items.length;
          skillsPublic = items.filter((s: { is_public?: boolean }) => s.is_public).length;
        }

        let kbTotal = 0;
        let kbPublic = 0;
        if (kbResp.ok) {
          const data = await kbResp.json();
          const items = data.items || [];
          kbTotal = Number(data.total) || items.length;
          kbPublic = items.filter((k: { is_public?: boolean }) => k.is_public).length;
        }

        const plugins = pluginsCatalog.plugins || [];
        const pluginsHealthy = plugins.filter((p) => p.healthy === true).length;
        const mcpStatusSafe: McpPlatformStatus = mcpStatus;

        if (cancelled) return;
        setStats({
          agentsTotal: agents.length,
          agentsPublic: agents.filter((a) => a.is_public).length,
          skillsTotal,
          skillsPublic,
          kbTotal,
          kbPublic,
          pluginsTotal: plugins.length,
          pluginsHealthy,
          mcpServers: mcpServers.length || Number(mcpStatusSafe.servers_total) || 0,
          mcpConnected:
            Number(mcpStatusSafe.servers_connected) ||
            (mcpStatusSafe.servers || []).filter((s) => s.connected).length ||
            0,
        });
      } catch {
        if (!cancelled) {
          setStats({
            agentsTotal: 0,
            agentsPublic: 0,
            skillsTotal: 0,
            skillsPublic: 0,
            kbTotal: 0,
            kbPublic: 0,
            pluginsTotal: 0,
            pluginsHealthy: 0,
            mcpServers: 0,
            mcpConnected: 0,
          });
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      setActivityLoading(true);
      setActivityError(null);
      try {
        const resp = await authFetch(getApiUrl(`${API_ENDPOINTS.ADMIN_ACTIVITY}?days=14`));
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        if (cancelled) return;
        setActivity({ ...EMPTY_ACTIVITY, ...(data.summary || {}) });
        setSeries(Array.isArray(data.series) ? data.series : []);
        setTopAgents(Array.isArray(data.top_agents_7d) ? data.top_agents_7d : []);
      } catch {
        if (!cancelled) {
          setActivity(EMPTY_ACTIVITY);
          setSeries([]);
          setTopAgents([]);
          setActivityError('Не удалось загрузить активность');
        }
      } finally {
        if (!cancelled) setActivityLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const activeAreaData = useMemo(
    () =>
      series.map((p) => ({
        label: formatDayLabel(p.date),
        value: Number(p.active_users) || 0,
      })),
    [series],
  );

  const activeTrendPct = useMemo(
    () => calcSeriesTrendPct(activeAreaData.map((p) => p.value)),
    [activeAreaData],
  );

  const usageDonut = useMemo(() => {
    const u = activity.usage_7d || {};
    return [
      { name: 'Агенты', value: Number(u.agent) || 0, color: theme.palette.primary.main },
      { name: 'Skills', value: Number(u.skill) || 0, color: theme.palette.info.main },
      { name: 'MCP', value: Number(u.mcp) || 0, color: theme.palette.success.light },
      {
        name: 'Базы знаний',
        value: Number(u.knowledge_base) || 0,
        color: theme.palette.warning.main,
      },
      { name: 'Плагины', value: Number(u.plugin) || 0, color: theme.palette.secondary.main },
    ];
  }, [activity.usage_7d, theme.palette]);

  const usageTotal7d = useMemo(
    () => usageDonut.reduce((acc, s) => acc + s.value, 0),
    [usageDonut],
  );

  const muted = isDarkMode ? 'rgba(255,255,255,0.55)' : 'rgba(0,0,0,0.55)';
  const cardSx = getAdminCardSx(isDarkMode);

  const Metric = ({
    label,
    value,
    hint,
    onClick,
  }: {
    label: string;
    value: string | number;
    hint?: string;
    onClick?: () => void;
  }) => (
    <Box
      component={onClick ? 'button' : 'div'}
      onClick={onClick}
      sx={{
        ...cardSx,
        textAlign: 'left',
        cursor: onClick ? 'pointer' : 'default',
        border: 'none',
        font: 'inherit',
        color: 'inherit',
        width: '100%',
      }}
    >
      <Typography variant="caption" sx={{ color: muted, display: 'block', mb: 0.75 }}>
        {label}
      </Typography>
      <Typography variant="h4" fontWeight={700} sx={{ lineHeight: 1.1 }}>
        {value}
      </Typography>
      {hint ? (
        <Typography variant="caption" sx={{ color: muted, mt: 0.75, display: 'block' }}>
          {hint}
        </Typography>
      ) : null}
    </Box>
  );

  const busy = loading || activityLoading;

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      {busy ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 2 }}>
          <CircularProgress size={28} />
        </Box>
      ) : null}

      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr', lg: 'repeat(3, 1fr)' },
          gap: 1.5,
        }}
      >
        <Metric
          label="Активные за 24ч"
          value={activityLoading ? '—' : activity.active_users_24h}
          hint={`Логинов: ${activity.logins_24h} · логаутов: ${activity.logouts_24h}`}
          onClick={onOpenView ? () => onOpenView('sessions') : undefined}
        />
        <Metric
          label="Активные за 7д"
          value={activityLoading ? '—' : activity.active_users_7d}
          hint={`Уник. логинов: ${activity.unique_logins_7d} · входов: ${activity.logins_7d}`}
          onClick={onOpenView ? () => onOpenView('sessions') : undefined}
        />
        <Metric
          label="Каталог: агенты"
          value={loading ? '—' : stats.agentsTotal}
          hint={`Публичных: ${stats.agentsPublic}`}
          onClick={onOpenCatalog ? () => onOpenCatalog('agents') : undefined}
        />
        <Metric
          label="Каталог: skills"
          value={loading ? '—' : stats.skillsTotal}
          hint={`Публичных: ${stats.skillsPublic}`}
          onClick={onOpenCatalog ? () => onOpenCatalog('skills') : undefined}
        />
        <Metric
          label="Каталог: БЗ / плагины / MCP"
          value={
            loading
              ? '—'
              : `${stats.kbTotal} / ${stats.pluginsTotal} / ${stats.mcpServers}`
          }
          hint="Справочники сущностей"
          onClick={onOpenCatalog ? () => onOpenCatalog('knowledge-bases') : undefined}
        />
      </Box>

      <Typography variant="subtitle2" fontWeight={600}>
        События
      </Typography>
      <Typography variant="body2" sx={{ color: muted, mt: -1 }}>
        Нажмите на карточку — откроется дашборд с деталями по этому типу событий.
      </Typography>

      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr', lg: 'repeat(3, 1fr)' },
          gap: 1.5,
        }}
      >
        {(
          [
            {
              view: 'sessions' as const,
              title: 'Входы / выходы',
              value: activityLoading
                ? '—'
                : `${activity.logins_7d} / ${activity.logouts_7d}`,
              hint: 'логины / логауты за 7д',
              color: theme.palette.success.main,
            },
            {
              view: 'agent' as const,
              title: 'Агенты',
              value: activityLoading ? '—' : activity.usage_7d?.agent ?? 0,
              hint: topAgents[0]
                ? `топ: ${topAgents[0].entity_name || topAgents[0].entity_id}`
                : 'использований за 7д',
              color: theme.palette.primary.main,
            },
            {
              view: 'skill' as const,
              title: 'Skills',
              value: activityLoading ? '—' : activity.usage_7d?.skill ?? 0,
              hint: 'использований за 7д',
              color: theme.palette.info.main,
            },
            {
              view: 'mcp' as const,
              title: 'MCP',
              value: activityLoading ? '—' : activity.usage_7d?.mcp ?? 0,
              hint: 'вызовов tools за 7д',
              color: theme.palette.success.light,
            },
            {
              view: 'knowledge_base' as const,
              title: 'Базы знаний',
              value: activityLoading ? '—' : activity.usage_7d?.knowledge_base ?? 0,
              hint: 'поисков / uses за 7д',
              color: theme.palette.warning.main,
            },
            {
              view: 'plugin' as const,
              title: 'Плагины',
              value: activityLoading ? '—' : activity.usage_7d?.plugin ?? 0,
              hint: 'вызовов за 7д',
              color: theme.palette.secondary.main,
            },
          ] as const
        ).map((card) => (
          <Box
            key={card.view}
            component="button"
            onClick={() => onOpenView?.(card.view)}
            sx={{
              ...cardSx,
              textAlign: 'left',
              cursor: onOpenView ? 'pointer' : 'default',
              border: 'none',
              font: 'inherit',
              color: 'inherit',
              width: '100%',
              transition: 'transform 0.12s ease, box-shadow 0.12s ease',
              '&:hover': onOpenView
                ? {
                    transform: 'translateY(-1px)',
                    boxShadow: isDarkMode
                      ? '0 4px 16px rgba(0,0,0,0.35)'
                      : '0 4px 14px rgba(0,0,0,0.08)',
                  }
                : undefined,
            }}
          >
            <Typography variant="caption" sx={{ color: muted, display: 'block', mb: 0.5 }}>
              {card.title}
            </Typography>
            <Typography variant="h4" fontWeight={700} sx={{ color: card.color, lineHeight: 1.1 }}>
              {card.value}
            </Typography>
            <Typography variant="caption" sx={{ color: muted, mt: 0.75, display: 'block' }}>
              {card.hint}
            </Typography>
          </Box>
        ))}
      </Box>

      {activityError ? (
        <Typography variant="body2" color="error">
          {activityError}
        </Typography>
      ) : null}

      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: { xs: '1fr', lg: '1.35fr 1fr' },
          gap: 1.5,
        }}
      >
        <Box sx={cardSx}>
          {activityLoading ? (
            <Box sx={{ display: 'flex', justifyContent: 'center', py: 8 }}>
              <CircularProgress size={24} />
            </Box>
          ) : (
            <AdminAreaTrendCard
              isDarkMode={isDarkMode}
              data={activeAreaData}
              title="Активные пользователи"
              subtitle="Тренд за выбранный период"
              kpi={activity.active_users_7d}
              kpiHint={`За 24ч: ${activity.active_users_24h} · чаты за сутки: ${activity.conversations_touched_24h}`}
              trendPct={activeTrendPct}
              color={theme.palette.success.main}
              valueLabel="Активные"
              height={250}
            />
          )}
        </Box>
        <Box sx={cardSx}>
          {activityLoading ? (
            <Box sx={{ display: 'flex', justifyContent: 'center', py: 8 }}>
              <CircularProgress size={24} />
            </Box>
          ) : (
            <AdminDonutCard
              isDarkMode={isDarkMode}
              title="Доля usage за 7д"
              subtitle="Агенты, skills, MCP, БЗ и плагины"
              data={usageDonut}
              centerValue={usageTotal7d}
              centerLabel="всего"
              height={220}
            />
          )}
        </Box>
      </Box>
    </Box>
  );
}
