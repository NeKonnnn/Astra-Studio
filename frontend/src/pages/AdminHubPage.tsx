import React, { useCallback, useMemo, useState } from 'react';
import {
  Box,
  Button,
  CircularProgress,
  Menu,
  MenuItem,
  Typography,
  useTheme,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import { Navigate, useNavigate, useSearchParams } from 'react-router-dom';
import AdminSideNav, {
  isUsageEntityType,
  parseAdminDashboardView,
  parseAdminHubSection,
  type AdminDashboardView,
  type AdminHubSection,
} from '../components/admin/AdminHubTabs';
import AdminDashboardPanel from '../components/admin/AdminDashboardPanel';
import AdminAgentsPanel from '../components/admin/AdminAgentsPanel';
import AdminSkillsPanel from '../components/admin/AdminSkillsPanel';
import AdminKnowledgeBasesPanel from '../components/admin/AdminKnowledgeBasesPanel';
import AdminPluginsPanel from '../components/admin/AdminPluginsPanel';
import AdminMcpPanel from '../components/admin/AdminMcpPanel';
import AdminUsagePanel from '../components/admin/AdminUsagePanel';
import AdminSessionsPanel from '../components/admin/AdminSessionsPanel';
import AdminStubPanel from '../components/admin/AdminStubPanel';
import { getWorkZoneBackgroundColor } from '../constants/workZoneBackground';
import { useWorkZoneBgMode } from '../hooks/useWorkZoneBgMode';
import { useAuth } from '../contexts/AuthContext';
import {
  downloadAdminExport,
  type AdminExportFormat,
} from '../utils/downloadAdminExport';

const DASHBOARD_VIEW_META: Record<
  Exclude<AdminDashboardView, 'overview'>,
  { title: string; subtitle: string }
> = {
  sessions: {
    title: 'Входы / выходы',
    subtitle: 'Логины и логауты: общая статистика и по пользователям',
  },
  agent: {
    title: 'Агенты — использование',
    subtitle: 'Сколько раз пользовались агентами, топ и кто каким агентом',
  },
  skill: {
    title: 'Skills — использование',
    subtitle: 'Использование skills по дням, топ и пользователи',
  },
  mcp: {
    title: 'MCP — использование',
    subtitle: 'Вызовы MCP-серверов/tools: частота и пользователи',
  },
  knowledge_base: {
    title: 'Базы знаний — использование',
    subtitle: 'Поиски и использования БЗ / RAG',
  },
  plugin: {
    title: 'Плагины — использование',
    subtitle: 'Вызовы HTTP-плагинов: топ и кто запускал',
  },
};

/**
 * Админ-панель: один дашборд (с drill-down по событиям) + справочники.
 */
export default function AdminHubPage() {
  const { user, isLoading } = useAuth();
  const theme = useTheme();
  const isDarkMode = theme.palette.mode === 'dark';
  const workZoneMode = useWorkZoneBgMode();
  const workZoneBgColor = getWorkZoneBackgroundColor(isDarkMode, workZoneMode);
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const [exportMenuAnchor, setExportMenuAnchor] = useState<null | HTMLElement>(null);

  const section = useMemo(() => {
    const raw = searchParams.get('section') || searchParams.get('tab');
    return parseAdminHubSection(raw);
  }, [searchParams]);

  const dashboardView = useMemo(() => {
    if (section !== 'dashboard') return 'overview' as const;
    return parseAdminDashboardView(
      searchParams.get('view'),
      searchParams.get('section') || searchParams.get('tab'),
    );
  }, [searchParams, section]);

  const setSection = useCallback(
    (next: AdminHubSection) => {
      const params = new URLSearchParams(searchParams);
      params.set('section', next);
      params.delete('tab');
      // Смена пункта меню всегда возвращает к обзору дашборда
      params.delete('view');
      setSearchParams(params, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  const setDashboardView = useCallback(
    (view: AdminDashboardView) => {
      const params = new URLSearchParams(searchParams);
      params.set('section', 'dashboard');
      params.delete('tab');
      if (view === 'overview') {
        params.delete('view');
      } else {
        params.set('view', view);
      }
      setSearchParams(params, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  const usageEntity = isUsageEntityType(dashboardView) ? dashboardView : undefined;
  const canExport = section === 'dashboard';

  const handleExport = useCallback(
    async (format: AdminExportFormat) => {
      setExportMenuAnchor(null);
      setExporting(true);
      setExportError(null);
      try {
        await downloadAdminExport({
          days: 14,
          entityType: usageEntity,
          format,
        });
      } catch (e) {
        setExportError(e instanceof Error ? e.message : 'Не удалось выгрузить статистику');
      } finally {
        setExporting(false);
      }
    },
    [usageEntity],
  );

  const textColor = isDarkMode ? '#fff' : '#111';
  const muted = isDarkMode ? 'rgba(255,255,255,0.55)' : 'rgba(0,0,0,0.55)';
  const shellBorder = isDarkMode ? '1px solid rgba(255,255,255,0.08)' : '1px solid rgba(0,0,0,0.08)';

  if (isLoading) {
    return null;
  }

  if (!user?.is_admin) {
    return <Navigate to="/" replace />;
  }

  const catalogTitles: Record<Exclude<AdminHubSection, 'dashboard'>, { title: string; subtitle: string }> = {
    users: {
      title: 'Пользователи',
      subtitle: 'Блокировка, роли и права доступа',
    },
    agents: {
      title: 'Агенты',
      subtitle: 'Справочник: все агенты пользователей платформы',
    },
    skills: {
      title: 'Skills',
      subtitle: 'Справочник: все skills пользователей',
    },
    'knowledge-bases': {
      title: 'Базы знаний',
      subtitle: 'Справочник: все базы знаний',
    },
    plugins: {
      title: 'Плагины',
      subtitle: 'Справочник: каталог плагинов и health',
    },
    mcp: {
      title: 'MCP',
      subtitle: 'Справочник: серверы MCP, статус и verify',
    },
  };

  let pageTitle = 'Дашборд';
  let pageSubtitle = 'Обзор платформы. Нажмите на событие, чтобы открыть его дашборд';
  if (section === 'dashboard' && dashboardView !== 'overview') {
    pageTitle = DASHBOARD_VIEW_META[dashboardView].title;
    pageSubtitle = DASHBOARD_VIEW_META[dashboardView].subtitle;
  } else if (section !== 'dashboard') {
    pageTitle = catalogTitles[section].title;
    pageSubtitle = catalogTitles[section].subtitle;
  }

  const railPadX = {
    pl: 'var(--left-sidebar-rail-overlap, 0px)',
    pr: 'var(--right-sidebar-rail-overlap, 0px)',
  } as const;

  return (
    <Box
      sx={{
        flexGrow: 1,
        height: '100%',
        overflow: 'hidden',
        backgroundColor: workZoneBgColor,
        color: textColor,
        display: 'flex',
        flexDirection: 'column',
        ...railPadX,
      }}
    >
      <Box
        sx={{
          px: { xs: 2, sm: 2.5 },
          pt: 2.5,
          pb: 1.5,
          display: 'flex',
          alignItems: 'flex-start',
          justifyContent: 'space-between',
          gap: 1,
          flexWrap: 'wrap',
          borderBottom: shellBorder,
        }}
      >
        <Box>
          <Typography variant="h5" fontWeight={700} sx={{ color: textColor }}>
            Администрирование
          </Typography>
          <Typography variant="body2" sx={{ color: muted, mt: 0.35 }}>
            {pageSubtitle}
          </Typography>
          {exportError ? (
            <Typography variant="caption" sx={{ color: 'error.main', display: 'block', mt: 0.5 }}>
              {exportError}
            </Typography>
          ) : null}
        </Box>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.25, mt: 0.5 }}>
          {canExport ? (
            <>
              <Button
                size="small"
                variant="outlined"
                disabled={exporting}
                endIcon={
                  exporting ? (
                    <CircularProgress size={14} color="inherit" />
                  ) : (
                    <ExpandMoreIcon sx={{ fontSize: 18 }} />
                  )
                }
                onClick={(e) => setExportMenuAnchor(e.currentTarget)}
                sx={{
                  textTransform: 'none',
                  borderColor: isDarkMode ? 'rgba(255,255,255,0.25)' : 'rgba(0,0,0,0.2)',
                  color: textColor,
                }}
              >
                {exporting ? 'Выгрузка…' : 'Выгрузить статистику'}
              </Button>
              <Menu
                anchorEl={exportMenuAnchor}
                open={Boolean(exportMenuAnchor)}
                onClose={() => setExportMenuAnchor(null)}
                anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
                transformOrigin={{ vertical: 'top', horizontal: 'right' }}
              >
                <MenuItem
                  disabled={exporting}
                  onClick={() => void handleExport('xlsx')}
                >
                  Excel (.xlsx)
                </MenuItem>
                <MenuItem
                  disabled={exporting}
                  onClick={() => void handleExport('csv')}
                >
                  CSV (.csv)
                </MenuItem>
                <MenuItem
                  disabled={exporting}
                  onClick={() => void handleExport('json')}
                >
                  JSON (.json)
                </MenuItem>
              </Menu>
            </>
          ) : null}
          <Typography
            component="button"
            onClick={() => navigate('/')}
            sx={{
              border: 'none',
              background: 'transparent',
              cursor: 'pointer',
              color: muted,
              fontSize: '0.85rem',
              '&:hover': { color: textColor },
            }}
          >
            К чату
          </Typography>
        </Box>
      </Box>

      <Box
        sx={{
          flex: 1,
          minHeight: 0,
          display: 'flex',
          flexDirection: { xs: 'column', md: 'row' },
          px: { xs: 1.5, sm: 2 },
          py: 2,
          gap: 2,
          overflow: 'hidden',
        }}
      >
        <AdminSideNav value={section} onChange={setSection} isDarkMode={isDarkMode} />

        <Box
          sx={{
            flex: 1,
            minWidth: 0,
            minHeight: 0,
            overflow: 'auto',
            display: 'flex',
            flexDirection: 'column',
          }}
        >
          {/* Заголовок секции только для дашборда/drill-down.
              В справочниках название уже есть в левом меню — второй "MCP"/"Агенты" не дублируем. */}
          {section === 'dashboard' ? (
            <Box
              sx={{
                display: 'flex',
                alignItems: 'center',
                gap: 1.5,
                mb: 1.5,
                flexWrap: 'wrap',
              }}
            >
              {dashboardView !== 'overview' ? (
                <Typography
                  component="button"
                  onClick={() => setDashboardView('overview')}
                  sx={{
                    border: 'none',
                    background: 'transparent',
                    cursor: 'pointer',
                    color: muted,
                    fontSize: '0.85rem',
                    p: 0,
                    '&:hover': { color: textColor },
                  }}
                >
                  ← К дашборду
                </Typography>
              ) : null}
              <Typography variant="h6" fontWeight={650}>
                {pageTitle}
              </Typography>
            </Box>
          ) : null}

          {section === 'dashboard' && dashboardView === 'overview' && (
            <AdminDashboardPanel
              isDarkMode={isDarkMode}
              onOpenView={setDashboardView}
              onOpenCatalog={(s) => setSection(s)}
            />
          )}
          {section === 'dashboard' && dashboardView === 'sessions' && (
            <AdminSessionsPanel isDarkMode={isDarkMode} />
          )}
          {section === 'dashboard' && usageEntity ? (
            <AdminUsagePanel isDarkMode={isDarkMode} entityType={usageEntity} />
          ) : null}

          {section === 'users' && (
            <AdminStubPanel
              isDarkMode={isDarkMode}
              title="Управление пользователями"
              description="Блокировка учётных записей и выдача прав (LDAP/Keycloak / локальные). Таблица и действия — на следующей итерации."
            />
          )}
          {section === 'agents' && <AdminAgentsPanel isDarkMode={isDarkMode} />}
          {section === 'skills' && <AdminSkillsPanel isDarkMode={isDarkMode} />}
          {section === 'knowledge-bases' && (
            <AdminKnowledgeBasesPanel isDarkMode={isDarkMode} />
          )}
          {section === 'plugins' && <AdminPluginsPanel isDarkMode={isDarkMode} />}
          {section === 'mcp' && <AdminMcpPanel isDarkMode={isDarkMode} />}
        </Box>
      </Box>
    </Box>
  );
}
