import React from 'react';
import {
  Box,
  List,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Typography,
} from '@mui/material';
import DashboardOutlinedIcon from '@mui/icons-material/DashboardOutlined';
import PeopleOutlineIcon from '@mui/icons-material/PeopleOutline';
import PluginNavIcon from '@mui/icons-material/Extension';
import SkillsNavIcon from '../../icons/SkillsIcon';
import KnowledgeBaseIcon from '../../icons/KnowledgeBaseIcon';
import McpNavIcon from '../../icons/McpIcon';
import { SidebarRailAgentIcon } from '../../constants/sidebarRailIcons';
import { getAdminNavItemSx } from './adminSurfaces';

/** Разделы левого меню: одна сводка + справочники. */
export type AdminHubSection =
  | 'dashboard'
  | 'users'
  | 'agents'
  | 'skills'
  | 'knowledge-bases'
  | 'plugins'
  | 'mcp';

/** Тип сущности для дашборда использования. */
export type AdminUsageEntityType =
  | 'agent'
  | 'skill'
  | 'mcp'
  | 'knowledge_base'
  | 'plugin';

/**
 * Drill-down внутри сводки: клик по карточке события открывает
 * соответствующий дашборд без отдельного пункта в меню.
 */
export type AdminDashboardView =
  | 'overview'
  | 'sessions'
  | AdminUsageEntityType;

type NavItem = {
  id: AdminHubSection;
  label: string;
  icon: React.ReactNode;
};

type NavGroup = {
  title: string;
  items: NavItem[];
};

export const ADMIN_NAV_GROUPS: NavGroup[] = [
  {
    title: 'Обзор',
    items: [
      { id: 'dashboard', label: 'Дашборд', icon: <DashboardOutlinedIcon fontSize="small" /> },
    ],
  },
  {
    title: 'Справочники',
    items: [
      { id: 'users', label: 'Пользователи', icon: <PeopleOutlineIcon fontSize="small" /> },
      { id: 'agents', label: 'Агенты', icon: <SidebarRailAgentIcon sx={{ fontSize: 20 }} /> },
      { id: 'skills', label: 'Skills', icon: <SkillsNavIcon sx={{ fontSize: 20 }} /> },
      {
        id: 'knowledge-bases',
        label: 'Базы знаний',
        icon: <KnowledgeBaseIcon sx={{ fontSize: 20 }} />,
      },
      { id: 'plugins', label: 'Плагины', icon: <PluginNavIcon sx={{ fontSize: 20 }} /> },
      { id: 'mcp', label: 'MCP', icon: <McpNavIcon sx={{ fontSize: 20 }} /> },
    ],
  },
];

export const ADMIN_HUB_SECTIONS: NavItem[] = ADMIN_NAV_GROUPS.flatMap((g) => g.items);

const SECTION_IDS = new Set<string>(ADMIN_HUB_SECTIONS.map((s) => s.id));

const USAGE_VIEWS = new Set<string>([
  'agent',
  'skill',
  'mcp',
  'knowledge_base',
  'plugin',
]);

/** Старые URL `section=dash-*` / `sessions` → view на сводке. */
const LEGACY_SECTION_TO_VIEW: Record<string, AdminDashboardView> = {
  sessions: 'sessions',
  'dash-agents': 'agent',
  'dash-skills': 'skill',
  'dash-mcp': 'mcp',
  'dash-knowledge-bases': 'knowledge_base',
  'dash-plugins': 'plugin',
  usage: 'agent',
  'usage-agents': 'agent',
  'usage-skills': 'skill',
  'usage-mcp': 'mcp',
  'usage-kb': 'knowledge_base',
  'usage-knowledge-bases': 'knowledge_base',
  'usage-plugins': 'plugin',
};

export function parseAdminHubSection(raw: string | null | undefined): AdminHubSection {
  const v = String(raw || '').trim().toLowerCase();
  if (LEGACY_SECTION_TO_VIEW[v]) return 'dashboard';
  if (v === 'knowledge_bases' || v === 'kb' || v === 'knowledge') return 'knowledge-bases';
  if (SECTION_IDS.has(v)) return v as AdminHubSection;
  return 'dashboard';
}

export function parseAdminDashboardView(
  viewRaw: string | null | undefined,
  sectionRaw?: string | null,
): AdminDashboardView {
  const view = String(viewRaw || '').trim().toLowerCase();
  if (view === 'sessions' || view === 'overview') return view;
  if (USAGE_VIEWS.has(view)) return view as AdminUsageEntityType;

  const legacy = String(sectionRaw || '').trim().toLowerCase();
  if (LEGACY_SECTION_TO_VIEW[legacy]) return LEGACY_SECTION_TO_VIEW[legacy];
  return 'overview';
}

export function isUsageEntityType(view: AdminDashboardView): view is AdminUsageEntityType {
  return USAGE_VIEWS.has(view);
}

/** @deprecated используйте parseAdminHubSection */
export function parseAdminHubTab(raw: string | null | undefined): AdminHubSection {
  return parseAdminHubSection(raw);
}

export type AdminHubTab = AdminHubSection;

interface AdminSideNavProps {
  value: AdminHubSection;
  onChange: (section: AdminHubSection) => void;
  isDarkMode?: boolean;
}

/** Левое меню: дашборд + справочники. */
export default function AdminSideNav({
  value,
  onChange,
  isDarkMode = true,
}: AdminSideNavProps) {
  const border = isDarkMode ? '1px solid rgba(255,255,255,0.08)' : '1px solid rgba(0,0,0,0.08)';
  const muted = isDarkMode ? 'rgba(255,255,255,0.45)' : 'rgba(0,0,0,0.45)';

  return (
    <Box
      sx={{
        width: { xs: '100%', md: 240 },
        flexShrink: 0,
        borderRight: { xs: 'none', md: border },
        borderBottom: { xs: border, md: 'none' },
        pr: { md: 1 },
        pb: { xs: 1.5, md: 0 },
        mb: { xs: 2, md: 0 },
        overflow: 'auto',
        maxHeight: { md: '100%' },
      }}
    >
      {ADMIN_NAV_GROUPS.map((group) => (
        <Box key={group.title} sx={{ mb: 1.75 }}>
          <Typography
            variant="caption"
            sx={{
              display: 'block',
              px: 1.5,
              mb: 0.75,
              color: muted,
              textTransform: 'uppercase',
              letterSpacing: '0.06em',
              fontSize: '0.68rem',
              fontWeight: 600,
            }}
          >
            {group.title}
          </Typography>
          <List disablePadding dense>
            {group.items.map((section) => {
              const active = value === section.id;
              return (
                <ListItemButton
                  key={section.id}
                  selected={active}
                  onClick={() => onChange(section.id)}
                  sx={getAdminNavItemSx(isDarkMode, active)}
                >
                  <ListItemIcon sx={{ minWidth: 32, color: 'inherit' }}>
                    {section.icon}
                  </ListItemIcon>
                  <ListItemText
                    primary={section.label}
                    primaryTypographyProps={{
                      sx: { fontSize: '0.82rem', fontWeight: active ? 600 : 400 },
                    }}
                  />
                </ListItemButton>
              );
            })}
          </List>
        </Box>
      ))}
    </Box>
  );
}
