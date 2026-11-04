import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Box,
  Button,
  Chip,
  CircularProgress,
  Collapse,
  IconButton,
  InputAdornment,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from '@mui/material';
import SearchIcon from '@mui/icons-material/Search';
import RefreshIcon from '@mui/icons-material/Refresh';
import BuildOutlinedIcon from '@mui/icons-material/BuildOutlined';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import {
  fetchMcpCredentialsMeta,
  fetchMcpServers,
  fetchMcpStatus,
  fetchMcpTools,
  verifyMcpServer,
} from '../../mcp/api';
import type {
  McpPlatformStatus,
  McpServerConfigPublic,
  McpServerStatus,
  McpToolInfo,
} from '../../mcp/types';
import { getAdminCardSx } from './adminSurfaces';

interface AdminMcpPanelProps {
  isDarkMode?: boolean;
}

type Row = {
  config: McpServerConfigPublic;
  status?: McpServerStatus;
  credentialsConfigured?: boolean;
};

/** Контроль MCP: список серверов, статус, verify, просмотр tools. */
export default function AdminMcpPanel({ isDarkMode = true }: AdminMcpPanelProps) {
  const [loading, setLoading] = useState(true);
  const [rows, setRows] = useState<Row[]>([]);
  const [platform, setPlatform] = useState<McpPlatformStatus | null>(null);
  const [search, setSearch] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [verifyingId, setVerifyingId] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [toolsByServer, setToolsByServer] = useState<Record<string, McpToolInfo[]>>({});
  const [toolsLoadingId, setToolsLoadingId] = useState<string | null>(null);
  const [toolsErrorByServer, setToolsErrorByServer] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [servers, status] = await Promise.all([
        fetchMcpServers(),
        fetchMcpStatus({ fetchTools: true }),
      ]);
      setPlatform(status);
      const statusById = new Map(
        (status.servers || []).map((s) => [s.id, s] as const),
      );
      const withCreds = await Promise.all(
        servers.map(async (cfg) => {
          let credentialsConfigured = false;
          try {
            const meta = await fetchMcpCredentialsMeta(cfg.id);
            credentialsConfigured = Boolean(meta.configured);
          } catch {
            /* ignore */
          }
          return {
            config: cfg,
            status: statusById.get(cfg.id),
            credentialsConfigured,
          } as Row;
        }),
      );
      setRows(withCreds);
    } catch (e) {
      setRows([]);
      setPlatform(null);
      setError(e instanceof Error ? e.message : 'Не удалось загрузить MCP');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const handleVerify = async (serverId: string) => {
    setVerifyingId(serverId);
    try {
      await verifyMcpServer(serverId);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : `Verify failed: ${serverId}`);
    } finally {
      setVerifyingId(null);
    }
  };

  const loadToolsForServer = useCallback(async (serverId: string) => {
    setToolsLoadingId(serverId);
    setToolsErrorByServer((prev) => {
      const next = { ...prev };
      delete next[serverId];
      return next;
    });
    try {
      const tools = await fetchMcpTools(serverId);
      setToolsByServer((prev) => ({ ...prev, [serverId]: tools }));
    } catch {
      setToolsErrorByServer((prev) => ({
        ...prev,
        [serverId]: 'Не удалось загрузить инструменты',
      }));
      setToolsByServer((prev) => ({ ...prev, [serverId]: [] }));
    } finally {
      setToolsLoadingId((cur) => (cur === serverId ? null : cur));
    }
  }, []);

  const toggleTools = useCallback(
    (serverId: string) => {
      setExpandedId((prev) => {
        const next = prev === serverId ? null : serverId;
        if (next) {
          void loadToolsForServer(next);
        }
        return next;
      });
    },
    [loadToolsForServer],
  );

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter((r) => {
      const hay = [r.config.id, r.config.display_name, r.config.transport, r.config.auth_type]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
      return hay.includes(q);
    });
  }, [rows, search]);

  const muted = isDarkMode ? 'rgba(255,255,255,0.55)' : 'rgba(0,0,0,0.55)';
  const text = isDarkMode ? 'rgba(255,255,255,0.92)' : 'rgba(0,0,0,0.88)';
  const border = isDarkMode ? 'rgba(255,255,255,0.1)' : 'rgba(0,0,0,0.1)';
  const cardSx = getAdminCardSx(isDarkMode);
  const headColor = isDarkMode ? 'rgba(255,255,255,0.45)' : 'rgba(0,0,0,0.45)';
  const colSpan = 9;

  const platformLabel =
    platform?.enabled === false
      ? 'Выключена'
      : platform?.initialized === false
        ? 'Не инициализирована'
        : 'Активна';

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5, minHeight: 0, flex: 1 }}>
      <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'center' }}>
        <TextField
          size="small"
          placeholder="Поиск серверов…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          sx={{ flex: '1 1 220px', minWidth: 180 }}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <SearchIcon fontSize="small" sx={{ opacity: 0.5 }} />
              </InputAdornment>
            ),
          }}
        />
        <Chip
          size="small"
          label={platformLabel}
          color={platformLabel === 'Активна' ? 'success' : 'default'}
          variant="outlined"
          sx={{ height: 28 }}
        />
        <Typography variant="body2" sx={{ color: muted }}>
          Серверов: {platform?.servers_connected ?? '—'}/
          {platform?.servers_total ?? platform?.total_servers ?? rows.length}
          {' · '}
          Tools: {platform?.tools ?? platform?.tools_total ?? '—'}
        </Typography>
        <Typography variant="body2" sx={{ color: muted, ml: 'auto' }}>
          {loading ? '…' : `${filtered.length} из ${rows.length}`}
        </Typography>
        <IconButton size="small" onClick={() => void load()} disabled={loading} title="Обновить">
          <RefreshIcon fontSize="small" />
        </IconButton>
      </Box>
      {platform?.message ? (
        <Typography variant="caption" sx={{ color: muted, mt: -0.5 }}>
          {platform.message}
        </Typography>
      ) : null}

      <Box sx={{ ...cardSx, p: 0, overflow: 'hidden', minHeight: 280 }}>
        {loading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}>
            <CircularProgress size={28} />
          </Box>
        ) : error ? (
          <Typography sx={{ p: 3, color: 'error.main' }}>{error}</Typography>
        ) : filtered.length === 0 ? (
          <Typography sx={{ p: 3, color: muted }}>MCP-серверы не найдены</Typography>
        ) : (
          <Box sx={{ overflow: 'auto', maxHeight: 'min(62vh, 640px)' }}>
            <Table size="small" stickyHeader>
              <TableHead>
                <TableRow>
                  {['ID', 'Имя', 'Transport', 'Enabled', 'Connected', 'Tools', 'Creds', '', ''].map(
                    (h, idx) => (
                      <TableCell
                        key={`${h}-${idx}`}
                        sx={{
                          bgcolor: isDarkMode ? 'rgba(30,30,34,0.95)' : 'rgba(250,250,250,0.95)',
                          color: headColor,
                          fontSize: '0.72rem',
                          fontWeight: 600,
                          borderBottom: isDarkMode
                            ? '1px solid rgba(255,255,255,0.08)'
                            : '1px solid rgba(0,0,0,0.08)',
                        }}
                      >
                        {h}
                      </TableCell>
                    ),
                  )}
                </TableRow>
              </TableHead>
              <TableBody>
                {filtered.map((r) => {
                  const connected = Boolean(r.status?.connected);
                  const toolsCount = r.status?.tools;
                  const expanded = expandedId === r.config.id;
                  const tools = toolsByServer[r.config.id];
                  const toolsLoading = toolsLoadingId === r.config.id;
                  const toolsError = toolsErrorByServer[r.config.id];
                  return (
                    <React.Fragment key={r.config.id}>
                      <TableRow hover selected={expanded}>
                        <TableCell sx={{ fontSize: '0.78rem', opacity: 0.75 }}>
                          {r.config.id}
                        </TableCell>
                        <TableCell>
                          <Typography variant="body2" fontWeight={600} noWrap>
                            {r.config.display_name || r.config.id}
                          </Typography>
                          {r.status?.error ? (
                            <Typography variant="caption" color="error" noWrap display="block">
                              {r.status.error}
                            </Typography>
                          ) : null}
                        </TableCell>
                        <TableCell sx={{ fontSize: '0.8rem', opacity: 0.75 }}>
                          {r.config.transport || '—'}
                        </TableCell>
                        <TableCell>
                          <Chip
                            size="small"
                            label={r.config.enabled ? 'Да' : 'Нет'}
                            color={r.config.enabled ? 'success' : 'default'}
                            variant="outlined"
                            sx={{ height: 22, fontSize: '0.7rem' }}
                          />
                        </TableCell>
                        <TableCell>
                          <Chip
                            size="small"
                            label={connected ? 'OK' : '—'}
                            color={connected ? 'success' : 'default'}
                            variant="outlined"
                            sx={{ height: 22, fontSize: '0.7rem' }}
                          />
                        </TableCell>
                        <TableCell sx={{ fontSize: '0.8rem' }}>
                          {typeof toolsCount === 'number' ? toolsCount : '—'}
                        </TableCell>
                        <TableCell>
                          <Chip
                            size="small"
                            label={r.credentialsConfigured ? 'Есть' : 'Нет'}
                            color={r.credentialsConfigured ? 'info' : 'default'}
                            variant="outlined"
                            sx={{ height: 22, fontSize: '0.7rem' }}
                          />
                        </TableCell>
                        <TableCell align="right" sx={{ width: 48, px: 0.5 }}>
                          <IconButton
                            size="small"
                            title={expanded ? 'Скрыть инструменты' : 'Показать инструменты'}
                            aria-label={expanded ? 'Скрыть инструменты' : 'Показать инструменты'}
                            aria-expanded={expanded}
                            onClick={() => toggleTools(r.config.id)}
                            color={expanded ? 'primary' : 'default'}
                          >
                            <BuildOutlinedIcon fontSize="small" />
                          </IconButton>
                        </TableCell>
                        <TableCell align="right">
                          <Button
                            size="small"
                            variant="outlined"
                            disabled={verifyingId === r.config.id}
                            onClick={() => void handleVerify(r.config.id)}
                            sx={{ textTransform: 'none', fontSize: '0.72rem' }}
                          >
                            {verifyingId === r.config.id ? '…' : 'Verify'}
                          </Button>
                        </TableCell>
                      </TableRow>
                      <TableRow>
                        <TableCell
                          colSpan={colSpan}
                          sx={{
                            py: 0,
                            borderBottom: expanded
                              ? isDarkMode
                                ? '1px solid rgba(255,255,255,0.08)'
                                : '1px solid rgba(0,0,0,0.08)'
                              : 'none',
                          }}
                        >
                          <Collapse in={expanded} timeout="auto" unmountOnExit>
                            <Box
                              sx={{
                                px: 1.5,
                                py: 1.25,
                                bgcolor: isDarkMode ? 'rgba(0,0,0,0.22)' : 'rgba(0,0,0,0.03)',
                              }}
                            >
                              <Box
                                sx={{
                                  display: 'flex',
                                  alignItems: 'center',
                                  gap: 0.75,
                                  mb: 1,
                                }}
                              >
                                <Typography
                                  sx={{
                                    fontSize: '0.72rem',
                                    fontWeight: 600,
                                    color: muted,
                                    textTransform: 'uppercase',
                                    letterSpacing: '0.04em',
                                  }}
                                >
                                  Инструменты
                                </Typography>
                                <ExpandMoreIcon
                                  sx={{
                                    fontSize: 18,
                                    color: muted,
                                    transform: 'rotate(180deg)',
                                  }}
                                />
                                {Array.isArray(tools) ? (
                                  <Chip
                                    size="small"
                                    label={`${tools.length}`}
                                    variant="outlined"
                                    sx={{ height: 20, fontSize: '0.65rem' }}
                                  />
                                ) : null}
                              </Box>

                              {toolsLoading ? (
                                <Box sx={{ display: 'flex', justifyContent: 'center', py: 2 }}>
                                  <CircularProgress size={22} />
                                </Box>
                              ) : toolsError ? (
                                <Typography variant="body2" color="error">
                                  {toolsError}
                                </Typography>
                              ) : !tools || tools.length === 0 ? (
                                <Typography variant="body2" sx={{ color: muted }}>
                                  Инструменты не найдены или сервер недоступен.
                                </Typography>
                              ) : (
                                <Box
                                  sx={{
                                    display: 'flex',
                                    flexDirection: 'column',
                                    gap: 0.75,
                                    maxHeight: 280,
                                    overflow: 'auto',
                                  }}
                                >
                                  {tools.map((tool) => (
                                    <Box
                                      key={tool.qualified_name || tool.name}
                                      sx={{
                                        border: `1px solid ${border}`,
                                        borderRadius: 1,
                                        px: 1,
                                        py: 0.85,
                                        bgcolor: isDarkMode
                                          ? 'rgba(255,255,255,0.03)'
                                          : 'rgba(255,255,255,0.7)',
                                      }}
                                    >
                                      <Typography
                                        variant="body2"
                                        fontWeight={600}
                                        sx={{ color: text }}
                                      >
                                        {tool.name}
                                      </Typography>
                                      {tool.qualified_name &&
                                      tool.qualified_name !== tool.name ? (
                                        <Typography
                                          variant="caption"
                                          sx={{
                                            color: muted,
                                            fontFamily: 'monospace',
                                            display: 'block',
                                          }}
                                          noWrap
                                        >
                                          {tool.qualified_name}
                                        </Typography>
                                      ) : null}
                                      {tool.description ? (
                                        <Typography
                                          variant="caption"
                                          sx={{ color: muted, display: 'block', mt: 0.25 }}
                                        >
                                          {tool.description}
                                        </Typography>
                                      ) : null}
                                    </Box>
                                  ))}
                                </Box>
                              )}
                            </Box>
                          </Collapse>
                        </TableCell>
                      </TableRow>
                    </React.Fragment>
                  );
                })}
              </TableBody>
            </Table>
          </Box>
        )}
      </Box>
      <Typography variant="caption" sx={{ color: muted }}>
        Иконка инструментов раскрывает список tools. Enabled задаётся конфигом (env/yaml);
        здесь — мониторинг, verify и credentials.
      </Typography>
    </Box>
  );
}
