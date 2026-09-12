import React, { useMemo, useRef, useState } from 'react';
import {
  Box,
  Typography,
  IconButton,
  Tooltip,
  FormControl,
  InputLabel,
  OutlinedInput,
  InputAdornment,
  Popover,
  Switch,
  Chip,
  FormControlLabel,
} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import HelpOutlineIcon from '@mui/icons-material/HelpOutline';
import HubIcon from '@mui/icons-material/Hub';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import AgentIcon from '../../icons/AgentIcon';
import type { SxProps, Theme } from '@mui/material/styles';
import {
  AGENT_CONSTRUCTOR_OUTLINED_INPUT_SX,
  SIDEBAR_HIDE_SCROLLBAR_SX,
  getDropdownChevronSx,
  getDropdownItemSx,
  getDropdownItemStateSx,
  getDropdownPopoverPaperSx,
} from '../../constants/menuStyles';

export interface SubagentConfig {
  enabled: boolean;
  allow_self: boolean;
  agent_ids: number[];
}

export interface SubagentAgentOption {
  id: number;
  name: string;
}

interface AgentSubagentsEditorProps {
  currentAgentId: number | 'new';
  config: SubagentConfig;
  onChange: (config: SubagentConfig) => void;
  agents: SubagentAgentOption[];
  readOnly?: boolean;
  maxSubagents?: number;
  panelChrome: {
    fgSubtle: string;
    fgMuted: string;
    hoverBg: string;
    isLight?: boolean;
  };
  categoryFieldSx?: SxProps<Theme>;
}

const DEFAULT_MAX_SUBAGENTS = 10;

export default function AgentSubagentsEditor({
  currentAgentId,
  config,
  onChange,
  agents,
  readOnly = false,
  maxSubagents = DEFAULT_MAX_SUBAGENTS,
  panelChrome,
  categoryFieldSx,
}: AgentSubagentsEditorProps) {
  const [popoverAnchor, setPopoverAnchor] = useState<HTMLElement | null>(null);
  const triggerRef = useRef<HTMLDivElement>(null);
  const darkFields = !panelChrome.isLight;
  const dropdownItemSx = useMemo(() => getDropdownItemSx(darkFields), [darkFields]);

  const options = useMemo(() => {
    const exclude = typeof currentAgentId === 'number' ? currentAgentId : null;
    return agents.filter((a) => a.id !== exclude && !config.agent_ids.includes(a.id));
  }, [agents, currentAgentId, config.agent_ids]);

  const byId = useMemo(() => new Map(agents.map((a) => [a.id, a])), [agents]);

  const setEnabled = (enabled: boolean) => {
    onChange({ ...config, enabled });
  };

  const setAllowSelf = (allow_self: boolean) => {
    onChange({ ...config, enabled: true, allow_self });
  };

  const addAgent = (id: number) => {
    if (config.agent_ids.length >= maxSubagents || config.agent_ids.includes(id)) return;
    onChange({ ...config, enabled: true, agent_ids: [...config.agent_ids, id] });
    setPopoverAnchor(null);
  };

  const removeAgent = (id: number) => {
    onChange({ ...config, agent_ids: config.agent_ids.filter((x) => x !== id) });
  };

  const nothingToSpawn =
    config.enabled && !config.allow_self && config.agent_ids.length === 0;

  return (
    <Box sx={{ minWidth: 0, mt: 2 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 1 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, minWidth: 0 }}>
          <HubIcon sx={{ fontSize: 15, color: panelChrome.fgMuted }} />
          <Typography
            variant="caption"
            sx={{
              color: 'inherit',
              opacity: 0.65,
              fontWeight: 600,
              textTransform: 'uppercase',
              letterSpacing: '0.06em',
              fontSize: '0.7rem',
            }}
          >
            Субагенты
          </Typography>
          <Tooltip
            title="Изолированные дочерние запуски: модель вызывает tool subagent для подзадач. Подробный вывод инструментов остаётся у ребёнка, родителю возвращается итог."
            arrow
          >
            <HelpOutlineIcon sx={{ fontSize: 13, color: panelChrome.fgSubtle, cursor: 'help' }} />
          </Tooltip>
        </Box>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <Chip
            size="small"
            label={`${config.agent_ids.length} / ${maxSubagents}`}
            sx={{
              height: 18,
              fontSize: '0.62rem',
              color: panelChrome.fgMuted,
              bgcolor: 'rgba(255,255,255,0.06)',
              '& .MuiChip-label': { px: 0.75 },
            }}
          />
          <Switch
            size="small"
            checked={config.enabled}
            disabled={readOnly}
            onChange={(e) => setEnabled(e.target.checked)}
            inputProps={{ 'aria-label': 'Включить субагентов' }}
          />
        </Box>
      </Box>
      <Typography variant="caption" sx={{ color: panelChrome.fgSubtle, fontSize: '0.68rem', display: 'block', mt: 0.5 }}>
        Делегирование подзадач помощникам в отдельном контексте.
      </Typography>

      {config.enabled && (
        <Box sx={{ mt: 1, display: 'flex', flexDirection: 'column', gap: 1 }}>
          <FormControlLabel
            control={
              <Switch
                size="small"
                checked={config.allow_self}
                disabled={readOnly}
                onChange={(e) => setAllowSelf(e.target.checked)}
              />
            }
            label={
              <Typography variant="caption" sx={{ color: panelChrome.fgMuted, fontSize: '0.75rem' }}>
                Разрешить self-spawn (копия этого агента)
              </Typography>
            }
            sx={{ ml: 0, mr: 0 }}
          />

          {config.agent_ids.map((id) => {
            const agent = byId.get(id);
            return (
              <Box
                key={id}
                sx={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 0.75,
                  px: 0.5,
                  py: 0.25,
                  borderRadius: 1,
                  '&:hover': { bgcolor: panelChrome.hoverBg },
                }}
              >
                <AgentIcon sx={{ fontSize: 16, color: panelChrome.fgMuted }} />
                <Typography variant="caption" sx={{ flex: 1, color: panelChrome.fgMuted, fontSize: '0.78rem' }} noWrap>
                  {agent?.name || `Агент #${id}`}
                </Typography>
                {!readOnly && (
                  <IconButton
                    size="small"
                    aria-label={`Убрать субагента ${agent?.name || id}`}
                    onClick={() => removeAgent(id)}
                    sx={{ color: panelChrome.fgSubtle, p: 0.25 }}
                  >
                    <CloseIcon sx={{ fontSize: 14 }} />
                  </IconButton>
                )}
              </Box>
            );
          })}

          {!readOnly && config.agent_ids.length < maxSubagents && (
            <Box ref={triggerRef}>
              <FormControl fullWidth size="small" sx={categoryFieldSx}>
                <InputLabel shrink>Добавить субагента</InputLabel>
                <OutlinedInput
                  readOnly
                  value=""
                  placeholder="Выберите агента"
                  onClick={(e) => setPopoverAnchor(e.currentTarget)}
                  endAdornment={
                    <InputAdornment position="end">
                      <ExpandMoreIcon sx={getDropdownChevronSx(darkFields)} />
                    </InputAdornment>
                  }
                  sx={{ ...AGENT_CONSTRUCTOR_OUTLINED_INPUT_SX, cursor: 'pointer' }}
                  label="Добавить субагента"
                />
              </FormControl>
              <Popover
                open={Boolean(popoverAnchor)}
                anchorEl={popoverAnchor}
                onClose={() => setPopoverAnchor(null)}
                anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
                transformOrigin={{ vertical: 'top', horizontal: 'left' }}
                slotProps={{ paper: { sx: getDropdownPopoverPaperSx(popoverAnchor, darkFields) } }}
              >
                <Box sx={{ ...SIDEBAR_HIDE_SCROLLBAR_SX, maxHeight: 220, overflowY: 'auto', minWidth: 220, p: 0.5 }}>
                  {options.length === 0 ? (
                    <Typography variant="caption" sx={{ p: 1, display: 'block', opacity: 0.6 }}>
                      Нет доступных агентов
                    </Typography>
                  ) : (
                    options.map((agent) => (
                      <Box
                        key={agent.id}
                        onClick={() => addAgent(agent.id)}
                        sx={{
                          ...dropdownItemSx,
                          ...getDropdownItemStateSx(darkFields, false),
                        }}
                      >
                        <AgentIcon sx={{ fontSize: 14, opacity: 0.7 }} />
                        <span>{agent.name}</span>
                      </Box>
                    ))
                  )}
                </Box>
              </Popover>
            </Box>
          )}

          {nothingToSpawn && (
            <Typography variant="caption" sx={{ color: '#ff9800', fontSize: '0.72rem' }}>
              Включите self-spawn или добавьте хотя бы одного субагента.
            </Typography>
          )}
        </Box>
      )}
    </Box>
  );
}

export const EMPTY_SUBAGENT_CONFIG: SubagentConfig = {
  enabled: false,
  allow_self: true,
  agent_ids: [],
};

export const MAX_SUBAGENTS_UI = DEFAULT_MAX_SUBAGENTS;
