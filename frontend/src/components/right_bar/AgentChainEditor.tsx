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
} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import HelpOutlineIcon from '@mui/icons-material/HelpOutline';
import LinkIcon from '@mui/icons-material/Link';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import AgentIcon from '../../icons/AgentIcon';
import type { SxProps, Theme } from '@mui/material/styles';
import { DEFAULT_MAX_CHAIN_AGENTS } from '../../constants/agentChain';
import {
  AGENT_CONSTRUCTOR_OUTLINED_INPUT_SX,
  SIDEBAR_HIDE_SCROLLBAR_SX,
  getDropdownChevronSx,
  getDropdownItemSx,
  getDropdownItemStateSx,
  getDropdownPopoverPaperSx,
} from '../../constants/menuStyles';

export interface ChainAgentOption {
  id: number;
  name: string;
}

interface AgentChainEditorProps {
  currentAgentId: number | 'new';
  currentAgentName: string;
  agentIds: number[];
  onChange: (ids: number[]) => void;
  hideSequential: boolean;
  onHideSequentialChange: (value: boolean) => void;
  agents: ChainAgentOption[];
  readOnly?: boolean;
  maxAgents?: number;
  panelChrome: {
    fgSubtle: string;
    fgMuted: string;
    hoverBg: string;
    isLight?: boolean;
  };
  categoryFieldSx?: SxProps<Theme>;
}

export default function AgentChainEditor({
  currentAgentId,
  currentAgentName,
  agentIds,
  onChange,
  hideSequential,
  onHideSequentialChange,
  agents,
  readOnly = false,
  maxAgents = DEFAULT_MAX_CHAIN_AGENTS,
  panelChrome,
  categoryFieldSx,
}: AgentChainEditorProps) {
  const [addAnchor, setAddAnchor] = useState<HTMLElement | null>(null);
  const [replaceIndex, setReplaceIndex] = useState<number | null>(null);
  const addTriggerRef = useRef<HTMLDivElement>(null);
  const currentId = typeof currentAgentId === 'number' ? currentAgentId : null;
  const darkFields = panelChrome.isLight !== true;
  const dropdownItemSx = useMemo(() => getDropdownItemSx(darkFields), [darkFields]);
  const dropdownChevronSx = useMemo(() => getDropdownChevronSx(darkFields), [darkFields]);

  const byId = useMemo(() => {
    const map = new Map<number, ChainAgentOption>();
    for (const a of agents) map.set(a.id, a);
    return map;
  }, [agents]);

  const selected = useMemo(() => new Set(agentIds), [agentIds]);

  const addOptions = useMemo(
    () =>
      agents.filter(
        (a) => a.id !== currentId && !selected.has(a.id),
      ),
    [agents, currentId, selected],
  );

  const canAdd = !readOnly && agentIds.length < maxAgents && addOptions.length > 0;

  const removeAt = (index: number) => {
    if (readOnly) return;
    onChange(agentIds.filter((_, i) => i !== index));
  };

  const replaceAt = (index: number, id: number) => {
    if (readOnly) return;
    if (id === currentId || (selected.has(id) && agentIds[index] !== id)) return;
    const next = [...agentIds];
    next[index] = id;
    onChange(next);
  };

  const addAgent = (id: number) => {
    if (!canAdd) return;
    if (id === currentId || selected.has(id)) return;
    onChange([...agentIds, id]);
    setAddAnchor(null);
  };

  const openReplace = (event: React.MouseEvent<HTMLElement>, index: number) => {
    if (readOnly) return;
    setReplaceIndex(index);
    setAddAnchor(event.currentTarget);
  };

  const optionsForPopover =
    replaceIndex === null
      ? addOptions
      : agents.filter(
          (a) =>
            a.id !== currentId &&
            (a.id === agentIds[replaceIndex] || !selected.has(a.id)),
        );

  return (
    <Box sx={{ minWidth: 0 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 1 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, minWidth: 0 }}>
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
            Цепочка агентов
          </Typography>
          <Tooltip
            title="Фиксированная последовательность: каждый следующий агент видит выводы предыдущих (Mixture-of-Agents). LLM не выбирает, кому передать управление."
            arrow
          >
            <HelpOutlineIcon sx={{ fontSize: 13, color: panelChrome.fgSubtle, cursor: 'help' }} />
          </Tooltip>
        </Box>
        <Chip
          size="small"
          label={`${agentIds.length} / ${maxAgents}`}
          sx={{
            height: 18,
            fontSize: '0.62rem',
            color: panelChrome.fgMuted,
            bgcolor: 'rgba(255,255,255,0.06)',
            '& .MuiChip-label': { px: 0.75 },
          }}
        />
      </Box>
      <Typography variant="caption" sx={{ color: panelChrome.fgSubtle, fontSize: '0.68rem', display: 'block', mt: 0.5 }}>
        Текущий агент выполняется первым, затем остальные по порядку.
      </Typography>

      <Box sx={{ mt: 1, display: 'flex', flexDirection: 'column', gap: 0.5 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, px: 0.5, py: 0.5 }}>
          <AgentIcon sx={{ fontSize: 16, color: panelChrome.fgMuted }} />
          <Typography variant="caption" sx={{ color: panelChrome.fgMuted, fontSize: '0.78rem', fontWeight: 600 }} noWrap>
            {currentAgentName.trim() || 'Этот агент'}
          </Typography>
        </Box>

        {agentIds.map((id, idx) => {
          const agent = byId.get(id);
          return (
            <React.Fragment key={`${id}-${idx}`}>
              <Box sx={{ display: 'flex', justifyContent: 'center', py: 0.15 }}>
                <LinkIcon sx={{ fontSize: 14, color: panelChrome.fgSubtle, transform: 'rotate(90deg)' }} />
              </Box>
              <Box
                sx={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 0.5,
                  border: '1px solid rgba(255,255,255,0.1)',
                  borderRadius: 1,
                  px: 0.75,
                  py: 0.25,
                }}
              >
                <AgentIcon sx={{ fontSize: 15, color: panelChrome.fgSubtle, flexShrink: 0 }} />
                <Typography
                  component="button"
                  type="button"
                  onClick={(e: React.MouseEvent<HTMLElement>) => openReplace(e, idx)}
                  disabled={readOnly}
                  title={agent?.name || `#${id}`}
                  sx={{
                    flex: 1,
                    minWidth: 0,
                    border: 0,
                    background: 'none',
                    color: panelChrome.fgMuted,
                    fontSize: '0.78rem',
                    textAlign: 'left',
                    cursor: readOnly ? 'default' : 'pointer',
                    p: 0,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {agent?.name || `Агент #${id}`}
                </Typography>
                {!readOnly && (
                  <IconButton
                    size="small"
                    onClick={() => removeAt(idx)}
                    aria-label={`Убрать ${agent?.name || 'агента'} из цепочки`}
                    sx={{ color: panelChrome.fgSubtle, p: 0.25 }}
                  >
                    <CloseIcon sx={{ fontSize: 14 }} />
                  </IconButton>
                )}
              </Box>
            </React.Fragment>
          );
        })}

        {canAdd && (
          <>
            <Box sx={{ display: 'flex', justifyContent: 'center', py: 0.15 }}>
              <LinkIcon sx={{ fontSize: 14, color: panelChrome.fgSubtle, transform: 'rotate(90deg)' }} />
            </Box>
            <FormControl variant="outlined" fullWidth size="small" sx={categoryFieldSx}>
              <InputLabel htmlFor="agent-constructor-chain-add">Добавить агента</InputLabel>
              <OutlinedInput
                ref={addTriggerRef}
                id="agent-constructor-chain-add"
                label="Добавить агента"
                readOnly
                value=""
                placeholder="Выберите агента"
                onClick={() => {
                  if (addTriggerRef.current) {
                    setReplaceIndex(null);
                    setAddAnchor(addTriggerRef.current);
                  }
                }}
                sx={AGENT_CONSTRUCTOR_OUTLINED_INPUT_SX}
                endAdornment={
                  <InputAdornment position="end">
                    <ExpandMoreIcon
                      sx={{ ...dropdownChevronSx, transform: Boolean(addAnchor) ? 'rotate(180deg)' : 'none' }}
                    />
                  </InputAdornment>
                }
              />
            </FormControl>
          </>
        )}

        {agentIds.length >= maxAgents && (
          <Typography variant="caption" sx={{ color: panelChrome.fgSubtle, fontSize: '0.65rem', fontStyle: 'italic', textAlign: 'center' }}>
            Максимум {maxAgents} агентов в цепочке
          </Typography>
        )}
      </Box>

      <Box sx={{ mt: 1.25, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, minWidth: 0, pr: 1 }}>
          <Typography variant="caption" sx={{ color: panelChrome.fgMuted, fontSize: '0.78rem' }}>
            Скрывать промежуточные ответы
          </Typography>
          <Tooltip title="В чат попадёт только ответ последнего агента. Промежуточные шаги идут в фоне." arrow>
            <HelpOutlineIcon sx={{ fontSize: 12, color: panelChrome.fgSubtle, cursor: 'help' }} />
          </Tooltip>
        </Box>
        <Switch
          checked={hideSequential}
          disabled={readOnly}
          onChange={(e: React.ChangeEvent<HTMLInputElement>) => onHideSequentialChange(e.target.checked)}
          size="small"
          sx={{
            '& .MuiSwitch-switchBase.Mui-checked': { color: '#2196f3' },
            '& .MuiSwitch-switchBase.Mui-checked + .MuiSwitch-track': { bgcolor: 'rgba(33,150,243,0.5)' },
            '& .MuiSwitch-track': { bgcolor: 'rgba(255,255,255,0.2)' },
          }}
        />
      </Box>

      <Popover
        open={Boolean(addAnchor)}
        anchorEl={addAnchor}
        onClose={() => {
          setAddAnchor(null);
          setReplaceIndex(null);
        }}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
        transformOrigin={{ vertical: 'top', horizontal: 'left' }}
        slotProps={{
          paper: { sx: getDropdownPopoverPaperSx(addAnchor, darkFields) },
        }}
      >
        <Box sx={{ py: 0.5, maxHeight: 280, overflowY: 'auto', ...SIDEBAR_HIDE_SCROLLBAR_SX }}>
          {optionsForPopover.length === 0 ? (
            <Box
              sx={{
                px: 1.5,
                py: 1.5,
                fontSize: '0.78rem',
                color: darkFields ? 'rgba(255,255,255,0.35)' : 'rgba(0,0,0,0.45)',
                textAlign: 'center',
              }}
            >
              Нет доступных агентов
            </Box>
          ) : (
            optionsForPopover.map((a: ChainAgentOption) => (
              <Box
                key={a.id}
                onClick={() => {
                  if (replaceIndex === null) addAgent(a.id);
                  else {
                    replaceAt(replaceIndex, a.id);
                    setAddAnchor(null);
                    setReplaceIndex(null);
                  }
                }}
                sx={{
                  ...dropdownItemSx,
                  ...getDropdownItemStateSx(
                    darkFields,
                    replaceIndex !== null && agentIds[replaceIndex] === a.id,
                  ),
                }}
              >
                {a.name}
              </Box>
            ))
          )}
        </Box>
      </Popover>
    </Box>
  );
}
