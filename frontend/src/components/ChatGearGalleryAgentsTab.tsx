import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Box, Typography, CircularProgress, IconButton, Tooltip } from '@mui/material';
import {
  Check as CheckIcon,
  PersonOff as NoAgentIcon,
  DeleteOutline as RemoveIcon,
} from '@mui/icons-material';
import AgentIcon from '../icons/AgentIcon';
import { useAuth } from '../contexts/AuthContext';
import { useAppActions } from '../contexts/AppContext';
import { getApiUrl } from '../config/api';
import {
  getDropdownItemSx,
  MENU_ACTION_TEXT_SIZE,
  CHAT_GEAR_MENU_AGENT_LIST_MAX_HEIGHT_PX,
  CHAT_GEAR_SCROLL_AREA_NO_VISIBLE_SCROLLBAR_SX,
} from '../constants/menuStyles';
import type { Agent } from './AgentSelector';
import { getActiveAgentFromStorage } from './AgentSelector';
import { loadAgentModelOnly } from '../utils/applyAgentServer';
import { persistAgentMcpConfig } from '../utils/applyAgentMcp';
import { persistAgentArtifactsEnabled } from '../utils/agentArtifactsEnabled';
import { clearActiveAgent } from '../utils/clearActiveAgent';
import ChatGearAgentReindexHint from './ChatGearAgentReindexHint';
import { formatAuthorLabel } from '../utils/formatAuthorLabel';

const STORAGE_AGENT_ID = 'active_agent_id';
const STORAGE_AGENT_NAME = 'active_agent_name';
const STORAGE_AGENT_PROMPT = 'active_agent_prompt';

interface GalleryAgentMenuItemProps {
  agent: Agent;
  activeAgent: ReturnType<typeof getActiveAgentFromStorage>;
  isLoadingModel: boolean;
  loadingAgentId: number | null;
  isDarkMode: boolean;
  dropdownItemSx: ReturnType<typeof getDropdownItemSx>;
  mutedTextColor: string;
  iconColor: string;
  subtleColor: string;
  onSelect: (agent: Agent) => void;
  onRemove: (agent: Agent, e: React.MouseEvent) => void;
}

function GalleryAgentMenuItem({
  agent,
  activeAgent,
  isLoadingModel,
  loadingAgentId,
  isDarkMode,
  dropdownItemSx,
  mutedTextColor,
  iconColor,
  subtleColor,
  onSelect,
  onRemove,
}: GalleryAgentMenuItemProps) {
  return (
    <Box
      onClick={() => {
        if (!isLoadingModel) onSelect(agent);
      }}
      sx={{
        ...dropdownItemSx,
        display: 'flex',
        alignItems: 'center',
        gap: 1,
        color: activeAgent?.id === agent.id ? mutedTextColor : iconColor,
        fontWeight: activeAgent?.id === agent.id ? 600 : 400,
        bgcolor:
          activeAgent?.id === agent.id
            ? isDarkMode
              ? 'rgba(255,255,255,0.06)'
              : 'rgba(0,0,0,0.04)'
            : 'transparent',
        borderRadius: 1,
        py: 0.75,
        px: 0.75,
        opacity: isLoadingModel ? 0.6 : 1,
        pointerEvents: isLoadingModel ? 'none' : 'auto',
        cursor: 'pointer',
      }}
    >
      <AgentIcon sx={{ fontSize: 18, color: iconColor, flexShrink: 0 }} />
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography
          sx={{
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            fontSize: MENU_ACTION_TEXT_SIZE,
          }}
        >
          {agent.name}
        </Typography>
        <Typography
          sx={{
            fontSize: '0.65rem',
            color: subtleColor,
            lineHeight: 1.2,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          от {formatAuthorLabel(agent.author_name, agent.author_full_name, agent.author_id) || 'автора'}
        </Typography>
      </Box>
      <ChatGearAgentReindexHint agentId={agent.id} />
      <Tooltip title="Убрать из галереи">
        <IconButton
          size="small"
          onClick={(e) => onRemove(agent, e)}
          sx={{ p: 0.25, color: subtleColor, flexShrink: 0 }}
        >
          <RemoveIcon sx={{ fontSize: 16 }} />
        </IconButton>
      </Tooltip>
      {loadingAgentId === agent.id ? (
        <CircularProgress size={14} sx={{ flexShrink: 0, color: 'primary.main' }} />
      ) : activeAgent?.id === agent.id ? (
        <CheckIcon sx={{ fontSize: 16, color: 'primary.main', flexShrink: 0 }} />
      ) : null}
    </Box>
  );
}

interface ChatGearGalleryAgentsTabProps {
  isDarkMode: boolean;
  searchQuery: string;
  visible: boolean;
}

export default function ChatGearGalleryAgentsTab({
  isDarkMode,
  searchQuery,
  visible,
}: ChatGearGalleryAgentsTabProps) {
  const { token } = useAuth();
  const { showNotification } = useAppActions();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loadingAgents, setLoadingAgents] = useState(false);
  const [activeAgent, setActiveAgent] = useState(() => getActiveAgentFromStorage());
  const [isLoadingModel, setIsLoadingModel] = useState(false);
  const [loadingAgentId, setLoadingAgentId] = useState<number | null>(null);

  const dropdownItemSx = useMemo(() => getDropdownItemSx(isDarkMode), [isDarkMode]);
  const mutedTextColor = isDarkMode ? 'rgba(255,255,255,0.9)' : 'rgba(0,0,0,0.85)';
  const iconColor = isDarkMode ? 'rgba(255,255,255,0.7)' : 'rgba(0,0,0,0.6)';
  const subtleColor = isDarkMode ? 'rgba(255,255,255,0.5)' : 'rgba(0,0,0,0.5)';

  const loadAgents = useCallback(async () => {
    if (!token) {
      setAgents([]);
      return;
    }
    setLoadingAgents(true);
    try {
      const resp = await fetch(getApiUrl('/api/agents/my/bookmarks?limit=100'), {
        headers: { Authorization: `Bearer ${token}` },
      });
      const data = resp.ok ? await resp.json() : { agents: [] };
      setAgents((data.agents || []) as Agent[]);
    } catch {
      setAgents([]);
    } finally {
      setLoadingAgents(false);
    }
  }, [token]);

  useEffect(() => {
    if (!visible) return;
    void loadAgents();
  }, [visible, loadAgents]);

  useEffect(() => {
    const onAgentSelected = () => setActiveAgent(getActiveAgentFromStorage());
    window.addEventListener('agentSelected', onAgentSelected);
    return () => window.removeEventListener('agentSelected', onAgentSelected);
  }, []);

  const handleClearAgent = useCallback(() => {
    clearActiveAgent();
    setActiveAgent(null);
    showNotification('info', 'Агент снят');
  }, [showNotification]);

  const handleRemoveFromGallery = useCallback(
    async (agent: Agent, e: React.MouseEvent) => {
      e.stopPropagation();
      if (!token) return;
      try {
        const resp = await fetch(getApiUrl(`/api/agents/${agent.id}/bookmark`), {
          method: 'DELETE',
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!resp.ok) throw new Error('fail');
        setAgents((prev) => prev.filter((a) => a.id !== agent.id));
        if (activeAgent?.id === agent.id) handleClearAgent();
        showNotification('success', `«${agent.name}» убран из «Агенты из галереи»`);
      } catch {
        showNotification('error', 'Не удалось убрать агента');
      }
    },
    [token, activeAgent, handleClearAgent, showNotification],
  );

  const handleSelectAgent = useCallback(
    async (agent: Agent) => {
      let full: Agent = { ...agent };
      if (token) {
        try {
          const r = await fetch(getApiUrl(`/api/agents/${agent.id}`), {
            headers: { Authorization: `Bearer ${token}` },
          });
          if (r.ok) {
            const j = await r.json();
            full = {
              ...agent,
              ...j,
              system_prompt: j.system_prompt ?? agent.system_prompt,
              config: (j.config as Record<string, unknown>) ?? agent.config,
            };
          }
        } catch {
          /* */
        }
      }

      const cfg = (full.config || {}) as Record<string, unknown>;
      const modelPath = String(cfg.model_path || cfg.model || '')
        .trim()
        .replace(/^1lm-svc:\/\//i, 'llm-svc://')
        .replace(/\s+/g, '');

      const persistLocal = () => {
        localStorage.setItem(STORAGE_AGENT_ID, String(full.id));
        localStorage.setItem(STORAGE_AGENT_NAME, full.name);
        localStorage.setItem(STORAGE_AGENT_PROMPT, full.system_prompt || '');
        persistAgentMcpConfig(cfg);
        persistAgentArtifactsEnabled(cfg);
        setActiveAgent({ id: full.id, name: full.name, system_prompt: full.system_prompt || '' });
        window.dispatchEvent(new CustomEvent('agentSelected', { detail: full }));
      };

      if (!token) {
        persistLocal();
        showNotification('info', 'Войдите в аккаунт, чтобы на сервер применились модель и настройки агента');
        return;
      }

      setIsLoadingModel(true);
      setLoadingAgentId(full.id);
      showNotification(
        'info',
        modelPath ? `Загрузка модели агента «${full.name}»…` : `Применение настроек агента «${full.name}»…`,
      );
      try {
        const applied = await loadAgentModelOnly(token, modelPath || null);
        persistLocal();
        if (!applied.ok) {
          showNotification('warning', `Агент «${full.name}» выбран, но модель не загрузилась: ${applied.message}`);
          return;
        }
        if ('pending' in applied && applied.pending) {
          showNotification(
            'info',
            `Агент «${full.name}» выбран. Модель подтянется при первом сообщении.`,
          );
          return;
        }
        showNotification(
          'success',
          modelPath
            ? `Агент «${full.name}»: модель загружена, промпт и настройки применятся в чате`
            : `Агент «${full.name}» активирован — промпт и настройки из карточки агента`,
        );
      } catch (e: unknown) {
        persistLocal();
        const msg = e instanceof Error ? e.message : String(e);
        showNotification('warning', `Агент «${full.name}» выбран, но модель не загрузилась: ${msg}`);
      } finally {
        setIsLoadingModel(false);
        setLoadingAgentId(null);
      }
    },
    [token, showNotification],
  );

  const filteredAgents = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return agents;
    return agents.filter((a) => a.name.toLowerCase().includes(q));
  }, [agents, searchQuery]);

  return (
    <Box
      sx={{
        height: `${CHAT_GEAR_MENU_AGENT_LIST_MAX_HEIGHT_PX}px`,
        maxHeight: `${CHAT_GEAR_MENU_AGENT_LIST_MAX_HEIGHT_PX}px`,
        flexShrink: 0,
        overflowX: 'hidden',
        overflowY: 'auto',
        px: 0.75,
        py: 1,
        boxSizing: 'border-box',
        ...CHAT_GEAR_SCROLL_AREA_NO_VISIBLE_SCROLLBAR_SX,
      }}
    >
      {loadingAgents && agents.length === 0 ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}>
          <CircularProgress size={28} />
        </Box>
      ) : (
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5 }}>
          <Box
            onClick={() => {
              if (!isLoadingModel) handleClearAgent();
            }}
            sx={{
              ...dropdownItemSx,
              display: 'flex',
              alignItems: 'center',
              gap: 1,
              color: !activeAgent ? mutedTextColor : iconColor,
              fontWeight: !activeAgent ? 600 : 400,
              bgcolor: !activeAgent ? (isDarkMode ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.04)') : 'transparent',
              fontStyle: 'italic',
              borderRadius: 1,
              py: 0.75,
              px: 0.75,
              opacity: isLoadingModel ? 0.6 : 1,
              pointerEvents: isLoadingModel ? 'none' : 'auto',
            }}
          >
            <NoAgentIcon sx={{ fontSize: 18, color: subtleColor, flexShrink: 0 }} />
            <Typography sx={{ flex: 1, fontSize: MENU_ACTION_TEXT_SIZE }}>Без агента</Typography>
            {!activeAgent && <CheckIcon sx={{ fontSize: 16, color: 'primary.main', flexShrink: 0 }} />}
          </Box>
          {filteredAgents.map((agent) => (
            <GalleryAgentMenuItem
              key={agent.id}
              agent={agent}
              activeAgent={activeAgent}
              isLoadingModel={isLoadingModel}
              loadingAgentId={loadingAgentId}
              isDarkMode={isDarkMode}
              dropdownItemSx={dropdownItemSx}
              mutedTextColor={mutedTextColor}
              iconColor={iconColor}
              subtleColor={subtleColor}
              onSelect={(a) => void handleSelectAgent(a)}
              onRemove={(a, e) => void handleRemoveFromGallery(a, e)}
            />
          ))}
          {!loadingAgents && filteredAgents.length === 0 && !searchQuery.trim() && (
            <Typography
              variant="body2"
              sx={{ color: subtleColor, fontSize: MENU_ACTION_TEXT_SIZE, px: 0.5, py: 1, textAlign: 'center' }}
            >
              Нет агентов из галереи. Нажмите «Использовать» в галерее агентов.
            </Typography>
          )}
          {!loadingAgents && searchQuery.trim() && filteredAgents.length === 0 && (
            <Typography
              variant="body2"
              sx={{ color: subtleColor, fontSize: MENU_ACTION_TEXT_SIZE, px: 0.5, py: 1, textAlign: 'center' }}
            >
              Ничего не найдено
            </Typography>
          )}
        </Box>
      )}
    </Box>
  );
}
