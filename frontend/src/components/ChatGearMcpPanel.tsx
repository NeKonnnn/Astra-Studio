import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Box,
  CircularProgress,
  Collapse,
  FormControlLabel,
  Switch,
  Typography,
  Chip,
} from '@mui/material';
import {
  Search as SearchIcon,
  ExpandMore as ExpandMoreIcon,
} from '@mui/icons-material';
import HubIcon from '../icons/McpIcon';
import {
  MENU_ACTION_TEXT_SIZE,
  CHAT_GEAR_SCROLL_AREA_NO_VISIBLE_SCROLLBAR_SX,
  getDropdownItemSx,
} from '../constants/menuStyles';
import { fetchMcpServers, fetchMcpStatus, fetchMcpTools } from '../mcp/api';
import { getMcpServerPlugin } from '../mcp/plugins/registry';
import { isMcpServerEnabledForChat } from '../mcp/selectionStorage';
import { useChatMcpSelection } from '../mcp/hooks/useChatMcpSelection';
import type { McpServerConfigPublic, McpServerStatus, McpToolInfo } from '../mcp/types';

interface ChatGearMcpPanelProps {
  isDarkMode: boolean;
  chatId: string | null | undefined;
}

export default function ChatGearMcpPanel({ isDarkMode, chatId }: ChatGearMcpPanelProps) {
  const { toggleServer } = useChatMcpSelection(chatId);
  const [servers, setServers] = useState<McpServerConfigPublic[]>([]);
  const [statusMap, setStatusMap] = useState<Map<string, McpServerStatus>>(new Map());
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [toolsByServer, setToolsByServer] = useState<Record<string, McpToolInfo[]>>({});
  const [toolsLoadingId, setToolsLoadingId] = useState<string | null>(null);
  const [toolsErrorByServer, setToolsErrorByServer] = useState<Record<string, string>>({});

  const muted = isDarkMode ? 'rgba(255,255,255,0.65)' : 'rgba(0,0,0,0.6)';
  const text = isDarkMode ? 'rgba(255,255,255,0.95)' : 'rgba(0,0,0,0.9)';
  const border = isDarkMode ? 'rgba(255,255,255,0.12)' : 'rgba(0,0,0,0.12)';
  const placeholderColor = isDarkMode ? 'rgba(255,255,255,0.3)' : 'rgba(0,0,0,0.35)';
  const dropdownItemSx = useMemo(() => getDropdownItemSx(isDarkMode), [isDarkMode]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [srv, st] = await Promise.all([fetchMcpServers(), fetchMcpStatus()]);
      setServers(srv.filter((s) => s.enabled));
      const map = new Map<string, McpServerStatus>();
      for (const s of st.servers || []) {
        map.set(s.id, s);
      }
      setStatusMap(map);
    } catch {
      setServers([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

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

  const toggleExpand = useCallback(
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
    if (!q) return servers;
    return servers.filter((s) => [s.id, s.display_name].join(' ').toLowerCase().includes(q));
  }, [servers, search]);

  if (!chatId) {
    return (
      <Box sx={{ p: 1.5, maxWidth: 320 }}>
        <Typography variant="body2" sx={{ color: muted, fontSize: MENU_ACTION_TEXT_SIZE }}>
          Выберите или создайте чат, чтобы включить MCP-серверы для этого диалога.
        </Typography>
      </Box>
    );
  }

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', minHeight: 0, flex: 1, height: '100%', overflow: 'hidden' }}>
      <Box sx={{ flexShrink: 0, px: 1.5, py: 0.9, display: 'flex', alignItems: 'center', gap: 1, borderBottom: `1px solid ${border}` }}>
        <SearchIcon sx={{ color: muted, fontSize: 16 }} />
        <Box
          component="input"
          placeholder="Поиск MCP..."
          value={search}
          onChange={(e: React.ChangeEvent<HTMLInputElement>) => setSearch(e.target.value)}
          sx={{
            flex: 1,
            bgcolor: 'transparent',
            border: 'none',
            outline: 'none',
            color: text,
            fontSize: MENU_ACTION_TEXT_SIZE,
            '&::placeholder': { color: placeholderColor },
          }}
        />
      </Box>

      <Box
        sx={{
          flex: 1,
          minHeight: 0,
          overflowY: 'auto',
          px: 0.75,
          py: 1,
          ...CHAT_GEAR_SCROLL_AREA_NO_VISIBLE_SCROLLBAR_SX,
        }}
      >
        {loading && servers.length === 0 ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}>
            <CircularProgress size={28} />
          </Box>
        ) : filtered.length === 0 ? (
          <Typography variant="body2" sx={{ color: muted, fontSize: MENU_ACTION_TEXT_SIZE, px: 0.5 }}>
            Нет доступных MCP-серверов.
          </Typography>
        ) : (
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
            {filtered.map((srv) => {
              const st = statusMap.get(srv.id);
              const enabled = isMcpServerEnabledForChat(chatId, srv.id);
              const Plugin = getMcpServerPlugin(srv.id);
              const expanded = expandedId === srv.id;
              const tools = toolsByServer[srv.id];
              const toolsLoading = toolsLoadingId === srv.id;
              const toolsError = toolsErrorByServer[srv.id];
              return (
                <Box
                  key={srv.id}
                  sx={{
                    border: `1px solid ${border}`,
                    borderRadius: 1,
                    overflow: 'hidden',
                    bgcolor: isDarkMode ? 'rgba(0,0,0,0.2)' : 'rgba(0,0,0,0.02)',
                  }}
                >
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, py: 0.65, px: 0.85 }}>
                    <HubIcon sx={{ fontSize: 20, color: enabled ? 'primary.main' : muted, flexShrink: 0 }} />
                    <Box
                      role="button"
                      tabIndex={0}
                      onClick={() => toggleExpand(srv.id)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault();
                          toggleExpand(srv.id);
                        }
                      }}
                      sx={{ flex: 1, minWidth: 0, display: 'flex', alignItems: 'center', gap: 0.5, cursor: 'pointer' }}
                    >
                      <Box sx={{ flex: 1, minWidth: 0 }}>
                        <Typography
                          sx={{
                            fontSize: MENU_ACTION_TEXT_SIZE,
                            fontWeight: 600,
                            color: text,
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            whiteSpace: 'nowrap',
                          }}
                        >
                          {srv.display_name || srv.id}
                        </Typography>
                        <Typography
                          sx={{
                            fontSize: '0.68rem',
                            color: muted,
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            whiteSpace: 'nowrap',
                            lineHeight: 1.3,
                          }}
                        >
                          Transport: {st?.transport || srv.transport || '—'}
                        </Typography>
                      </Box>
                      {st?.tools != null ? (
                        <Chip
                          label={`${st.tools}`}
                          size="small"
                          variant="outlined"
                          sx={{ height: 20, fontSize: '0.65rem', flexShrink: 0 }}
                        />
                      ) : null}
                      {st?.connected === false ? (
                        <Chip label="offline" size="small" color="error" variant="outlined" sx={{ height: 20, fontSize: '0.65rem' }} />
                      ) : st?.connected ? (
                        <Chip label="ok" size="small" color="success" variant="outlined" sx={{ height: 20, fontSize: '0.65rem' }} />
                      ) : null}
                      <ExpandMoreIcon
                        sx={{
                          fontSize: 20,
                          color: muted,
                          transform: expanded ? 'rotate(180deg)' : 'none',
                          transition: 'transform 0.2s',
                        }}
                      />
                    </Box>
                    <FormControlLabel
                      onClick={(e) => e.stopPropagation()}
                      control={
                        <Switch
                          size="small"
                          checked={enabled}
                          onChange={(_e, checked) => toggleServer(srv.id, checked)}
                          color="primary"
                        />
                      }
                      label=""
                      sx={{ m: 0 }}
                    />
                  </Box>
                  <Collapse in={expanded}>
                    <Box
                      sx={{
                        px: 1,
                        pb: 1,
                        pt: 0,
                        borderTop: `1px solid ${border}`,
                        bgcolor: isDarkMode ? 'rgba(0,0,0,0.12)' : 'rgba(0,0,0,0.03)',
                      }}
                    >
                      <Typography
                        sx={{
                          fontSize: '0.72rem',
                          fontWeight: 600,
                          color: muted,
                          textTransform: 'uppercase',
                          letterSpacing: '0.04em',
                          mb: 0.75,
                          mt: 1,
                        }}
                      >
                        Инструменты
                      </Typography>
                      {toolsLoading ? (
                        <Box sx={{ display: 'flex', justifyContent: 'center', py: 1.5 }}>
                          <CircularProgress size={20} />
                        </Box>
                      ) : toolsError ? (
                        <Typography sx={{ fontSize: MENU_ACTION_TEXT_SIZE, color: 'error.main', mb: 1 }}>
                          {toolsError}
                        </Typography>
                      ) : !tools || tools.length === 0 ? (
                        <Typography sx={{ fontSize: MENU_ACTION_TEXT_SIZE, color: muted, mb: 1 }}>
                          Инструменты не найдены или сервер недоступен.
                        </Typography>
                      ) : (
                        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.75, mb: Plugin ? 1.25 : 0 }}>
                          {tools.map((tool) => (
                            <Box
                              key={tool.qualified_name || tool.name}
                              sx={{
                                ...dropdownItemSx,
                                display: 'flex',
                                alignItems: 'flex-start',
                                gap: 1,
                                py: 0.75,
                                px: 0.75,
                                borderRadius: 1,
                                border: `1px solid ${border}`,
                                cursor: 'default',
                              }}
                            >
                              <Box sx={{ flex: 1, minWidth: 0 }}>
                                <Typography sx={{ fontSize: MENU_ACTION_TEXT_SIZE, fontWeight: 600, color: text }}>
                                  {tool.name}
                                </Typography>
                                {tool.qualified_name && tool.qualified_name !== tool.name ? (
                                  <Typography
                                    sx={{
                                      fontSize: '0.68rem',
                                      color: muted,
                                      fontFamily: 'monospace',
                                      overflow: 'hidden',
                                      textOverflow: 'ellipsis',
                                      whiteSpace: 'nowrap',
                                    }}
                                  >
                                    {tool.qualified_name}
                                  </Typography>
                                ) : null}
                                {tool.description ? (
                                  <Typography
                                    sx={{
                                      fontSize: '0.72rem',
                                      color: muted,
                                      lineHeight: 1.4,
                                      mt: 0.25,
                                    }}
                                  >
                                    {tool.description}
                                  </Typography>
                                ) : null}
                              </Box>
                            </Box>
                          ))}
                        </Box>
                      )}
                      {Plugin ? (
                        <Box sx={{ pt: 0.5 }}>
                          <Plugin
                            serverId={srv.id}
                            isDarkMode={isDarkMode}
                            compact
                            authMode={srv.auth_mode}
                          />
                        </Box>
                      ) : null}
                    </Box>
                  </Collapse>
                </Box>
              );
            })}
          </Box>
        )}
      </Box>
    </Box>
  );
}
