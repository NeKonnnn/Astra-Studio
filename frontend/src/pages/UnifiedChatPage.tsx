import React, { useState, useRef, useEffect, useLayoutEffect, useCallback, useMemo } from 'react';
import { Virtuoso, type VirtuosoHandle } from 'react-virtuoso';
import { astra185Mark, astra185MarkRender } from '../utils/debugReact185';
import {
  Box,
  TextField,
  IconButton,
  Typography,
  Card,
  CardContent,
  Avatar,
  Chip,
  Tooltip,
  Alert,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  FormControl,
  InputLabel,
  OutlinedInput,
  InputAdornment,
  Slider,
  Popover,
  type PopoverActions,
  Collapse,
  Divider,
  Checkbox,
  FormControlLabel,
  Paper,
  CircularProgress,
} from '@mui/material';
import { useAuth } from '../contexts/AuthContext';
import {
  Send as SendIcon,
  Person as PersonIcon,
  ContentCopy as CopyIcon,
  Stop as StopIcon,
  Refresh as RefreshIcon,
  Mic as MicIcon,
  Close as CloseIcon,
  Upload as UploadIcon,
  Code as GearMenuCodingIcon,
  ImageOutlined as GearMenuImageGenIcon,
  ChevronLeft as ChevronLeftIcon,
  ChevronRight as ChevronRightIcon,
  Add as AddIcon,
  Share as ShareIcon,
  AutoStories as KbIcon,
  Check as CheckIcon,
  ExpandMore as ExpandMoreIcon,
  KeyboardArrowDown as KeyboardArrowDownIcon,
  Psychology as ThinkingModeIcon,
  Bolt as FastModeIcon,
  AutoAwesome as AutoModeIcon,
} from '@mui/icons-material';
import type { SxProps, Theme } from '@mui/material/styles';
import { useTheme } from '@mui/material/styles';
import { useAppContext, useAppActions, Message, MultiLLMResponseSlot } from '../contexts/AppContext';
import { useSocket } from '../contexts/SocketContext';
import { getApiUrl, getWsUrl, API_ENDPOINTS, getAuthFetchHeaders } from '../config/api';
import MessageRenderer from '../components/MessageRenderer';
import { DocumentSearchPanel } from '../components/DocumentSearchPanel';
import { useNavigate } from 'react-router-dom';
import ModelSelector from '../components/ModelSelector';
import MessageNavigationBar from '../components/MessageNavigationBar';
import { ASTRA_INSERT_CHAT_TEXT, ASTRA_INSERT_CHAT_TEXT_KEY } from '../components/right_bar';
import ShareConfirmDialog from '../components/ShareConfirmDialog';
import ChatInputBar, { InlineAttachment } from '../components/ChatInputBar';
import { prepareInlineImageFile, formatFileSize, dataUrlToFile, getClipboardImageFile } from '../utils/inlineImage';
import {
  buildOversizedInlineAttachMessage,
  buildUnsupportedInlineAttachMessage,
  isInlineAttachFileTooLarge,
  isInlineAttachSizeErrorMessage,
  INLINE_ATTACH_ACCEPT,
  INLINE_ATTACH_WAIT_BEFORE_SEND_MESSAGE,
} from '../utils/inlineAttachmentRules';
import TopErrorBanner from '../components/TopErrorBanner';
import { STATUS_TOAST_MESSAGES } from '../constants/statusToast';
import { logChatAttach, logChatAttachError } from '../utils/chatAttachDebug';
import InlineAttachmentsList from '../components/InlineAttachmentsList';
import InlineImageLightbox from '../components/InlineImageLightbox';
import ImageGenerationPlaceholder from '../components/ImageGenerationPlaceholder';
import ChatGearAgentsPanel from '../components/ChatGearAgentsPanel';
import { default as GearMenuAgentsIcon } from '../icons/AgentIcon';
import { default as GearMenuMcpIcon } from '../icons/McpIcon';
import { default as SkillsNavIcon } from '../icons/SkillsIcon';
import { default as GearMenuArtifactsIcon } from '../icons/ArtifactsIcon';
import ChatGearArtifactsPanel from '../components/ChatGearArtifactsPanel';
import ChatGearMcpPanel from '../components/ChatGearMcpPanel';
import ChatGearCodingPanel from '../components/ChatGearCodingPanel';
import ChatGearGenerationPanel from '../components/ChatGearGenerationPanel';
import {
  isAnyGenerationModeEnabled,
  isImageGenerationModeEnabled,
  isVideoGenerationModeEnabled,
} from '../imageGeneration/selectionStorage';
import ChatGearSkillsPanel from '../components/ChatGearSkillsPanel';
import VoiceChatDialog from '../components/VoiceChatDialog';
import AgentSelector from '../components/AgentSelector';
import ChatInputStatusCluster from '../components/ChatInputStatusCluster';
import ChatContextUsagePopover from '../components/ChatContextUsagePopover';
import MessageFeedbackBar from '../components/MessageFeedbackBar';
import MessageMoreActionsMenu from '../components/MessageMoreActionsMenu';
import { exportMessageContent } from '../utils/exportMessageContent';
import type { MessageExportFormat } from '../utils/exportMessageContent';
import type { MessageFeedback } from '../constants/messageFeedback';
import { useMyAgentSelection } from '../hooks/useChatInputAgentIndicators';
import { useRagReindexStatus } from '../hooks/useRagReindexStatus';
import {
  MEMORY_RAG_DISABLED_HINT,
  RAG_REINDEX_BLOCK_PLACEHOLDER,
} from '../utils/ragReindexBlock';
import { clearActiveAgent } from '../utils/clearActiveAgent';
import { clearActiveSkills } from '../utils/skillSelectionStorage';
import { disableChatArtifactsToolsForChat } from '../utils/artifactsSelectionStorage';
import { useActiveSkillIndicators } from '../hooks/useActiveSkillIndicators';
import { useChatInputArtifactsIndicator } from '../hooks/useChatInputArtifactsIndicator';
import { useChatContextUsage } from '../hooks/useChatContextUsage';
import { useChatInputMcpIndicators } from '../mcp/hooks/useChatInputMcpIndicators';
import { useMcpStreamingTools } from '../mcp/hooks/useMcpStreamingTools';
import McpToolCallsPanel from '../mcp/components/McpToolCallsPanel';
import { mergeMcpToolCalls } from '../mcp/utils/mergeToolCalls';
import McpLiveToolsIndicator from '../mcp/components/McpLiveToolsIndicator';
import ChatInputSuggestions from '../components/ChatInputSuggestions';
import MessageFollowUpSuggestions from '../components/MessageFollowUpSuggestions';
import { getChatInputSuggestions } from '../chat/getChatInputSuggestions';
import { loadFollowUpSettings } from '../chat/followUpSettings';
import { useFollowUpSuggestions } from '../hooks/useFollowUpSuggestions';
import { formatGenerationDuration, useElapsedSeconds } from '../hooks/useElapsedSeconds';
import { extractReasoningBlock as extractReasoningBlockShared } from '../utils/reasoningSplit';
import {
  estimateLibraryClusterWidthPx,
  getToolsButtonInsetSp,
} from '../components/chatInputLayout';
import { getWorkZoneBackgroundColor, getWorkZoneCustomImage, isWorkZoneAnimatedMode } from '../constants/workZoneBackground';
import { useWorkZoneBgMode } from '../hooks/useWorkZoneBgMode';
import WorkZoneStarrySky from '../components/WorkZoneStarrySky';
import WorkZoneSnowfall from '../components/WorkZoneSnowfall';
import {
  isKnowledgeRagEnabled,
  setKnowledgeRagEnabled,
  KNOWLEDGE_RAG_STORAGE_EVENT,
} from '../utils/knowledgeRagStorage';
import { estimateTokens } from '../utils/contextTokens';
import { ASTRA_TRIGGER_ATTACH } from '../constants/hotkeys';
import {
  getDropdownPanelSx,
  getDropdownItemSx,
  getFormFieldInputSx,
  DROPDOWN_CHEVRON_SX,
  DROPDOWN_PAPER_MARGIN_TOP,
  DROPDOWN_PAPER_MIN_WIDTH_PX,
  DROPDOWN_PAPER_DEFAULT_WIDTH_PX,
  DROPDOWN_ITEM_HOVER_BG_DARK,
  DROPDOWN_ITEM_HOVER_BG_LIGHT,
  MENU_ACTION_TEXT_SIZE,
  CHAT_GEAR_MENU_PANEL_WIDTH_PX,
  CHAT_GEAR_MENU_LEFT_RAIL_WIDTH_PX,
  CHAT_GEAR_MENU_EXPANDED_WIDTH_PX,
  CHAT_GEAR_MENU_AGENTS_RIGHT_MIN_PX,
  CHAT_GEAR_MENU_PANELS_GAP_PX,
  CHAT_GEAR_MENU_ANCHOR_VERTICAL_OFFSET,
  CHAT_GEAR_MENU_PAPER_MAX_HEIGHT,
  CHAT_GEAR_MENU_PAPER_MAX_HEIGHT_PX,
  getChatGearMenuPaperHeightPx,
  CHAT_GEAR_MENU_MARGIN_THRESHOLD_PX,
  CHAT_GEAR_SCROLL_AREA_NO_VISIBLE_SCROLLBAR_SX,
  SIDEBAR_HIDE_SCROLLBAR_SX,
} from '../constants/menuStyles';
import {
  isValidSelectedModelPath,
  LAST_SELECTED_MODEL_PATH_STORAGE_KEY,
  MODEL_THINKING_MODE_STORAGE_KEY,
  ModelThinkingMode,
} from '../utils/modelThinking';
import { applyAgentMcpToChat, applyStoredAgentMcpToChat } from '../utils/applyAgentMcp';
import AgentChainUpdate from '../components/chat/AgentChainUpdate';
import ThinkingShimmerText from '../components/chat/ThinkingShimmerText';
import { resolveChainRenderSegments } from '../constants/agentChain';

interface UnifiedChatPageProps {
  isDarkMode: boolean;
  sidebarOpen?: boolean;
  sidebarHidden?: boolean;
}

interface ModelWindow {
  id: string;
  selectedModel: string;
}

/** Иконка «сравнение моделей» для тёмной темы (светлый глиф на тёмной кнопке). */
const MULTI_LLM_COMPARE_ICON_DARK_THEME_DATA_URL =
  'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAACXBIWXMAAA7DAAAOwwHHb6hkAAAAGXRFWHRTb2Z0d2FyZQB3d3cuaW5rc2NhcGUub3Jnm+48GgAAAKhJREFUOI2tkTEOwkAQA22EQJSQf+QVNPwqBaKAt9BS8Qv+QEfPVUNzh6KwURIUN9btWV7vrjQHgBOQGEYCjpFBAnYjGlXAO/ogqNVAHWl/kvQYNEDTY/BNshiKHcH2S9JaklxcbTtHPmTdPvM98832o6XFtpdBg23mTefdj6k7aPNfO2gjGqHgOtol37Uaofuer4xQElwkPYHVgEeSdJ41SXcH8ySZgg8Dm7tpcrd/HwAAAABJRU5ErkJggg==';

/** Иконка для светлой темы (тёмный глиф на светлой кнопке). */
const MULTI_LLM_COMPARE_ICON_LIGHT_THEME_DATA_URL =
  'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAACXBIWXMAAAB2AAAAdgFOeyYIAAAAGXRFWHRTb2Z0d2FyZQB3d3cuaW5rc2NhcGUub3Jnm+48GgAAAJ5JREFUOI2tkksOwjAMRF8QEmIJ3INTsOFWCFXchi2r3qLn6L5ZhUVNlLbTph9GskZxrMnYMfwJL8ADIRMeKJSAB84zHroAjboIIne1ULUDJ0rgYaEEopPdiNUcauAA4BJVR2v5brmbcWn8AaqkNgBuL9RPxsfeeRJLZxB57QwiVAs/vJcIedqvySFdpE7bBfNWuQGeSmCLkw7WONmGL2KrPMuVWrUJAAAAAElFTkSuQmCC';

function MultiLlmModeToggleIcon({ isDarkMode }: { isDarkMode: boolean }): React.ReactElement {
  const src = isDarkMode
    ? MULTI_LLM_COMPARE_ICON_DARK_THEME_DATA_URL
    : MULTI_LLM_COMPARE_ICON_LIGHT_THEME_DATA_URL;
  return (
    <Box
      component="img"
      src={src}
      alt=""
      draggable={false}
      sx={{
        width: 16,
        height: 16,
        display: 'block',
        flexShrink: 0,
        objectFit: 'contain',
        pointerEvents: 'none',
      }}
    />
  );
}

/**
 * Значение для Select / POST multi-llm — всегда полный path ``<provider_id>/<model_id>``.
 * Бэкенд (split_model_path) понимает как новый формат, так и legacy ``llm-svc://host/model``.
 */
function availableModelSelectValue(m: { name: string; path: string }): string {
  return m.path || m.name;
}

/** Текст одного столбца multi-LLM как на экране (варианты перегенерации). */
function getMultiLlmColumnDisplayText(slot: MultiLLMResponseSlot): string {
  if (slot.alternativeResponses?.length && slot.currentResponseIndex !== undefined) {
    const i = slot.currentResponseIndex;
    if (i >= 0 && i < slot.alternativeResponses.length) {
      const t = slot.alternativeResponses[i];
      if (t !== undefined) return t;
    }
  }
  return slot.content;
}

/** Тело столбца multi-LLM для парсинга рассуждений (с trimEnd после завершения). */
function getMultiLlmColumnDisplayBody(response: MultiLLMResponseSlot): string {
  if (response.alternativeResponses?.length && response.currentResponseIndex !== undefined) {
    const ci = response.currentResponseIndex;
    if (ci >= 0 && ci < response.alternativeResponses.length) {
      const alt = response.alternativeResponses[ci];
      if (alt !== undefined) return response.isStreaming ? alt : alt.trimEnd();
    }
  }
  return response.isStreaming ? response.content : response.content.trimEnd();
}

function getAssistantInlineAttachments(message: Message): NonNullable<Message['inlineAttachments']> | undefined {
  const variants = message.inlineAttachmentVariants;
  if (variants?.length) {
    const idx = message.currentResponseIndex ?? variants.length - 1;
    const clamped = Math.max(0, Math.min(idx, variants.length - 1));
    const picked = variants[clamped];
    return picked?.length ? picked : undefined;
  }
  return message.inlineAttachments?.length ? message.inlineAttachments : undefined;
}

function extractReasoningBlock(
  rawText: string,
  isStreaming?: boolean,
): { visibleContent: string; reasoningContent: string | null; isThinkingStreaming: boolean } {
  // Единый парсер (теги + plaintext «Thinking Process» + orphan </think> у Qwen3.5).
  return extractReasoningBlockShared(rawText, isStreaming);
}

function getStreamingAssistantVisibleContent(message: Message): string {
  if (
    message.alternativeResponses &&
    message.alternativeResponses.length > 0 &&
    message.currentResponseIndex !== undefined
  ) {
    const ci = message.currentResponseIndex;
    if (ci >= 0 && ci < message.alternativeResponses.length) {
      const alt = message.alternativeResponses[ci];
      if (alt !== undefined) return alt;
    }
  }
  return message.content || '';
}

function ChatThinkingIndicator({
  isDarkMode,
  leftAlignMessages,
  widescreenMode,
  startedAtMs,
  agentName,
}: {
  isDarkMode: boolean;
  leftAlignMessages: boolean;
  widescreenMode: boolean;
  startedAtMs?: number | null;
  agentName?: string | null;
}) {
  const elapsedSec = useElapsedSeconds(startedAtMs, true);
  const timerLabel = formatGenerationDuration(elapsedSec ?? 0);

  return (
    <Box
      sx={{
        width: '100%',
        display: 'flex',
        flexDirection: 'column',
        alignItems: leftAlignMessages ? 'flex-start' : 'flex-start',
        mb: 1.5,
      }}
    >
      <Box
        sx={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'flex-start',
          maxWidth: widescreenMode ? '100%' : '75%',
          minWidth: '180px',
        }}
      >
        <Card
          sx={{
            backgroundColor: isDarkMode ? 'background.paper' : '#f8f9fa',
            color: isDarkMode ? 'text.primary' : '#333',
            boxShadow: isDarkMode
              ? '0 2px 8px rgba(0, 0, 0, 0.15)'
              : '0 2px 8px rgba(0, 0, 0, 0.1)',
            width: '100%',
          }}
        >
          <CardContent sx={{ p: 1.2, pb: 0.8 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', mb: 0.3 }}>
              <Avatar
                src="/astra.png"
                sx={{
                  width: 24,
                  height: 24,
                  mr: 1,
                  bgcolor: 'transparent',
                  position: 'relative',
                  '&::before': {
                    content: '""',
                    position: 'absolute',
                    top: '-2px',
                    left: '-2px',
                    right: '-2px',
                    bottom: '-2px',
                    borderRadius: '50%',
                    background: 'radial-gradient(circle, rgba(33, 150, 243, 0.3) 0%, transparent 70%)',
                    animation: 'thinking-glow 2s ease-in-out infinite',
                    '@keyframes thinking-glow': {
                      '0%, 100%': { opacity: 0.3, transform: 'scale(1)' },
                      '50%': { opacity: 0.8, transform: 'scale(1.3)' },
                    },
                  },
                  animation: 'thinking 2s ease-in-out infinite',
                }}
              />
              <Typography variant="caption" sx={{ opacity: 0.8, fontSize: '0.75rem', fontWeight: 500 }}>
                {agentName?.trim() || 'AstraChat'}
              </Typography>
              <Typography variant="caption" sx={{ ml: 'auto', opacity: 0.6, fontSize: '0.7rem' }}>
                {new Date().toLocaleTimeString('ru-RU', {
                  hour: '2-digit',
                  minute: '2-digit',
                })}
              </Typography>
            </Box>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, minHeight: '24px' }}>
              <ThinkingShimmerText isDarkMode={isDarkMode} fontSize="0.875rem" fontWeight={500}>
                {`думает...\u00A0${timerLabel}`}
              </ThinkingShimmerText>
            </Box>
          </CardContent>
        </Card>
      </Box>
    </Box>
  );
}

/** Живой таймер в чипе multi-LLM «Генерируется…». */
function MultiLlmGeneratingChip({ startedAtMs }: { startedAtMs?: number }) {
  const elapsed = useElapsedSeconds(startedAtMs, true);
  const label = `Генерируется… ${formatGenerationDuration(elapsed ?? 0)}`;
  return <Chip label={label} size="small" color="info" />;
}

/** Drag файлов из ОС; выделенный текст даёт text/plain без Files — не показываем зону загрузки */
function dataTransferHasFiles(dt: DataTransfer | null): boolean {
  if (!dt) return false;
  try {
    if (dt.items?.length) {
      for (let i = 0; i < dt.items.length; i++) {
        if (dt.items[i].kind === 'file') return true;
      }
    }
  } catch {
    /* ignore */
  }
  const types = dt.types;
  if (!types || types.length === 0) return false;
  const domTypes = types as unknown as { contains?: (s: string) => boolean };
  if (typeof domTypes.contains === 'function') {
    return domTypes.contains('Files');
  }
  return Array.from(types).includes('Files');
}

// ================================
// ИНТЕРФЕЙС ДАННЫХ ДЛЯ КАРТОЧКИ СООБЩЕНИЯ
// (callback-и передаются через ref, чтобы React.memo не реагировал на их пересоздание)
// ================================
interface MessageCardData {
  handleSendMessageFromRenderer: (prompt: string) => void;
  handleCopyMessage: (content: string) => void;
  handleEditClick: (message: Message) => void;
  handleRegenerate: (message: Message) => void;
  handleEditMultiLlmColumn: (message: Message, slotIndex: number) => void;
  handleRegenerateMultiLlmColumn: (message: Message, slotIndex: number) => void;
  handleMessageFeedback: (
    message: Message,
    payload: {
      rating: 'like' | 'dislike' | null;
      tags?: string[];
      comment?: string;
      multiLlmSlotIndex?: number;
    },
  ) => void | Promise<void>;
  synthesizeSpeech: (text: string) => void;
  handleEnterShareMode: () => void;
  handleBranchToNewChat: (message: Message, multiLlmSlotIndex?: number) => void;
  handleToggleMessage: (userMsgId: string, assistantMsgId: string) => void;
  handleFollowUpSelect: (content: string) => void;
  updateMessage: (
    chatId: string,
    messageId: string,
    content?: string,
    isStreaming?: boolean,
    multiLLMResponses?: MultiLLMResponseSlot[],
    alternativeResponses?: string[],
    currentResponseIndex?: number,
    documentSearch?: Message['documentSearch'],
    reasoningContent?: string,
    mcpToolCalls?: Message['mcpToolCalls'],
    inlineAttachments?: Message['inlineAttachments'],
    isImageGenerating?: boolean,
    inlineAttachmentVariants?: Message['inlineAttachmentVariants'],
    feedback?: MessageFeedback | null,
  ) => void;
  formatTimestamp: (ts: string) => string;
  currentChatId: string | undefined;
  messageRefs: React.MutableRefObject<(HTMLDivElement | null)[]>;
}

interface MessageCardProps {
  message: Message;
  index: number;
  /** Индекс в полном массиве messages — для навигации по вопросам. */
  navIndex: number;
  isPairStart: boolean;
  isSelected: boolean;
  nextMessageId: string | null;
  shareMode: boolean;
  isSpeaking: boolean;
  isDarkMode: boolean;
  /** Сообщение — последнее в чате (как history.currentId в Open WebUI). */
  isLastChatMessage: boolean;
  interfaceSettings: {
    userNoBorder: boolean;
    assistantNoBorder: boolean;
    leftAlignMessages: boolean;
    showUserName: boolean;
    followUpAutoGenerate: boolean;
    followUpShowScope: 'last' | 'all';
  };
  username: string | undefined;
  dataRef: React.MutableRefObject<MessageCardData>;
}

// ===========================
// КОМПОНЕНТ БЛОКА РАССУЖДЕНИЙ — дизайн в стиле Qwen Studio / ASTRA
// ===========================
interface ReasoningBlockProps {
  reasoningContent: string;
  isThinkingStreaming: boolean;
  isExpanded: boolean;
  onToggle: () => void;
  durationSec: number | null;
  /** Секунды с начала размышления (обновляется раз в секунду во время стриминга). */
  liveThinkingSec: number | null;
  isDarkMode: boolean;
}

const ReasoningBlock = React.memo(({
  reasoningContent,
  isThinkingStreaming,
  isExpanded,
  onToggle,
  durationSec,
  liveThinkingSec,
  isDarkMode,
}: ReasoningBlockProps) => {
  const pauseAutoScrollForInteraction = () => {
    window.dispatchEvent(new CustomEvent('astra_pause_chat_autoscroll'));
  };
  const isDark = isDarkMode;
  const titleColor = isDark ? 'rgba(255,255,255,0.9)' : 'rgba(0,0,0,0.85)';
  const contentColor = isDark ? 'rgba(255,255,255,0.82)' : 'rgba(0,0,0,0.78)';

  const headerLabel =
    isThinkingStreaming
      ? `Думает…\u00A0${liveThinkingSec ?? 0}\u00A0сек`
      : durationSec !== null
        ? `Думала\u00A0${durationSec}\u00A0сек`
        : 'Цепочка рассуждений';

  const subtleToggleSx = {
    display: 'flex',
    alignItems: 'center',
    gap: 0.5,
    userSelect: 'none' as const,
    cursor: 'pointer',
    width: 'fit-content',
    maxWidth: '100%',
    py: 0.25,
    transition: 'opacity 0.2s',
    '&:hover': { opacity: 0.75 },
  };

  return (
    <Box sx={{ mb: 0, pb: 1.5 }}>
      {/* Заголовок — как у «Выполнен поиск по документам» */}
      <Box
        onMouseDown={pauseAutoScrollForInteraction}
        onTouchStart={pauseAutoScrollForInteraction}
        onClick={() => {
          pauseAutoScrollForInteraction();
          onToggle();
        }}
        sx={subtleToggleSx}
        role="button"
        aria-expanded={isExpanded}
        aria-label={`${headerLabel}. ${isExpanded ? 'Свернуть' : 'Раскрыть'} блок рассуждений`}
      >
        {isThinkingStreaming ? (
          <ThinkingShimmerText isDarkMode={isDark} fontSize="0.9rem" fontWeight={400}>
            {headerLabel}
          </ThinkingShimmerText>
        ) : (
          <Typography variant="body2" sx={{ fontWeight: 400, fontSize: '0.9rem', lineHeight: 1.35, color: titleColor }}>
            {headerLabel}
          </Typography>
        )}

        <KeyboardArrowDownIcon
          fontSize="small"
          sx={{
            color: titleColor,
            flexShrink: 0,
            transform: isExpanded ? 'rotate(180deg)' : 'none',
            transition: 'transform 0.2s',
            opacity: isThinkingStreaming ? 0.9 : 1,
          }}
        />
      </Box>

      {/* Контент — карточка как у «Выполнен поиск по документам» */}
      <Collapse in={isExpanded} timeout={220}>
        <Box
          sx={{
            mt: 1,
            p: 2,
            borderRadius: 1,
            border: '1px solid',
            borderColor: 'divider',
            bgcolor: 'action.hover',
            position: 'relative',
            overflow: 'hidden',
            '&::before': isThinkingStreaming
              ? {
                  content: '""',
                  position: 'absolute',
                  top: 0,
                  left: 0,
                  right: 0,
                  height: '2px',
                  background: `linear-gradient(90deg, transparent, ${
                    isDark ? 'rgba(255,255,255,0.45)' : 'rgba(0,0,0,0.28)'
                  }, transparent)`,
                  '@keyframes reasoningScan': {
                    '0%': { transform: 'translateX(-100%)' },
                    '100%': { transform: 'translateX(100%)' },
                  },
                  animation: 'reasoningScan 1.8s linear infinite',
                }
              : {},
          }}
        >
          <Box
            sx={{
              fontSize: '0.82rem',
              lineHeight: 1.65,
              color: contentColor,
              '& p, & li, & span': { fontSize: 'inherit', lineHeight: 'inherit' },
              '& p:first-of-type': { mt: 0 },
              '& p:last-of-type': { mb: 0 },
            }}
          >
            <MessageRenderer
              content={reasoningContent}
              isStreaming={isThinkingStreaming}
            />
          </Box>
        </Box>
      </Collapse>
    </Box>
  );
});

/** Обычный запас viewport (слишком большой padding сам провоцировал пустой кадр). */
const VIRTUOSO_VIEWPORT_PADDING = { top: 200, bottom: 200 } as const;

const virtuosoMessagesEndRefHolder: {
  current: React.RefObject<HTMLDivElement | null> | null;
} = { current: null };

/** Footer Virtuoso: нижний спейсер (Grok-style) + якорь скролла. */
type VirtuosoListContext = {
  bottomSpacerPx: number;
};

function VirtuosoMessagesEndAnchor({ context }: { context?: VirtuosoListContext }) {
  const spacer = context?.bottomSpacerPx ?? 0;
  return (
    <>
      {spacer > 0 ? (
        <div aria-hidden style={{ height: spacer, flex: '0 0 auto', width: '100%' }} />
      ) : null}
      <div ref={virtuosoMessagesEndRefHolder.current ?? undefined} style={{ height: 1 }} />
    </>
  );
}

const VIRTUOSO_LIST_COMPONENTS = { Footer: VirtuosoMessagesEndAnchor };

const MessageCardComponent = ({
  message, index, navIndex, isPairStart, isSelected, nextMessageId,
  shareMode, isSpeaking, isDarkMode, isLastChatMessage, interfaceSettings, username, dataRef,
}: MessageCardProps): React.ReactElement => {
  const isUser = message.role === 'user';
  const [isHovered, setIsHovered] = useState(false);
  const [hoveredMultiLlmCol, setHoveredMultiLlmCol] = useState<number | null>(null);
  const [reasoningExpanded, setReasoningExpanded] = useState(false);
  const [chainReasoningExpanded, setChainReasoningExpanded] = useState<Record<number, boolean>>({});
  const [multiReasoningExpanded, setMultiReasoningExpanded] = useState<Record<number, boolean>>({});
  const [lightboxSrc, setLightboxSrc] = useState<{ src: string; name: string } | null>(null);
  const thinkingStartRef = useRef<number | null>(null);
  const [thinkingDurationSec, setThinkingDurationSec] = useState<number | null>(null);
  const [liveThinkingSec, setLiveThinkingSec] = useState<number | null>(null);
  const prevThinkingStreamingRef = useRef(false);

  const multiPrevThinkingStreamingRef = useRef<Record<number, boolean>>({});
  const multiThinkingStartRef = useRef<Record<number, number | null>>({});
  const [multiThinkingDurationSec, setMultiThinkingDurationSec] = useState<Record<number, number | null>>({});
  const [multiLiveThinkingSec, setMultiLiveThinkingSec] = useState<Record<number, number | null>>({});

  // Вычисляем тело сообщения и парсим reasoning на уровне компонента (не внутри JSX)
  const visibleBody = useMemo(() => {
    if (
      message.alternativeResponses &&
      message.alternativeResponses.length > 0 &&
      message.currentResponseIndex !== undefined
    ) {
      const ci = message.currentResponseIndex;
      if (ci >= 0 && ci < message.alternativeResponses.length) {
        const alt = message.alternativeResponses[ci];
        if (alt !== undefined)
          return message.isStreaming ? alt : alt.trimEnd();
      }
    }
    return message.isStreaming ? message.content : message.content.trimEnd();
  }, [message.content, message.isStreaming, message.alternativeResponses, message.currentResponseIndex]);

  const parsedMessage = useMemo(
    () => extractReasoningBlock(visibleBody, message.isStreaming),
    [visibleBody, message.isStreaming],
  );
  const chainSegments = useMemo(
    () =>
      isUser
        ? null
        : resolveChainRenderSegments(
            visibleBody,
            message.chainSteps,
            message.chainCurrentAgent,
            message.isStreaming,
            message.hideSequentialOutputs,
          ),
    [isUser, visibleBody, message.chainSteps, message.chainCurrentAgent, message.isStreaming, message.hideSequentialOutputs],
  );
  const assistantInlineAttachments = useMemo(
    () => (isUser ? undefined : getAssistantInlineAttachments(message)),
    [isUser, message],
  );
  const showImageGenerationPlaceholder = Boolean(
    !isUser &&
      (message.isImageGenerating || message.isVideoGenerating) &&
      message.isStreaming &&
      !assistantInlineAttachments?.length,
  );
  const isReasoningStreaming = useMemo(() => {
    if (message.isImageGenerating || message.isVideoGenerating) return false;
    // Пока сообщение стримится и есть блок рассуждений — держим «Думает» над ответом.
    // Не ждём пустого visibleContent: иначе после первых токенов ответа заголовок
    // пропадает/меняется, и кажется, что «Думает» оказалось «под» ответом.
    return Boolean(
      parsedMessage.isThinkingStreaming ||
        (message.isStreaming && Boolean(parsedMessage.reasoningContent)),
    );
  }, [
    message.isImageGenerating,
    message.isVideoGenerating,
    parsedMessage.isThinkingStreaming,
    parsedMessage.reasoningContent,
    message.isStreaming,
  ]);

  // Отслеживаем длительность рассуждения
  useEffect(() => {
    const isNowThinking = isReasoningStreaming;
    const wasThinking = prevThinkingStreamingRef.current;
    if (isNowThinking && !wasThinking) {
      thinkingStartRef.current = Date.now();
      setThinkingDurationSec(null);
      setLiveThinkingSec(0);
    } else if (!isNowThinking && wasThinking && thinkingStartRef.current) {
      const secs = Math.round((Date.now() - thinkingStartRef.current) / 1000);
      setThinkingDurationSec(secs > 0 ? secs : 1);
      thinkingStartRef.current = null;
    }
    prevThinkingStreamingRef.current = isNowThinking;
  }, [isReasoningStreaming]);

  useEffect(() => {
    if (!isReasoningStreaming) {
      setLiveThinkingSec(null);
      return;
    }
    const tick = () => {
      if (thinkingStartRef.current) {
        setLiveThinkingSec(Math.max(0, Math.floor((Date.now() - thinkingStartRef.current) / 1000)));
      }
    };
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, [isReasoningStreaming]);

  useEffect(() => {
    if (!message.multiLLMResponses?.length) return;
    message.multiLLMResponses.forEach((response, respIndex) => {
      const displayBody = getMultiLlmColumnDisplayBody(response);
      const parsed = extractReasoningBlock(displayBody, response.isStreaming);
      const isThinkingStreaming = Boolean(
        parsed.isThinkingStreaming ||
          (response.isStreaming &&
            Boolean(parsed.reasoningContent) &&
            parsed.visibleContent.trim().length === 0),
      );
      const was = multiPrevThinkingStreamingRef.current[respIndex] ?? false;
      if (isThinkingStreaming && !was) {
        multiThinkingStartRef.current[respIndex] = Date.now();
        setMultiThinkingDurationSec((p: Record<number, number | null>) => ({ ...p, [respIndex]: null }));
        setMultiLiveThinkingSec((p) => ({ ...p, [respIndex]: 0 }));
      } else if (!isThinkingStreaming && was && multiThinkingStartRef.current[respIndex]) {
        const secs = Math.round((Date.now() - multiThinkingStartRef.current[respIndex]!) / 1000);
        setMultiThinkingDurationSec((p: Record<number, number | null>) => ({ ...p, [respIndex]: secs > 0 ? secs : 1 }));
        multiThinkingStartRef.current[respIndex] = null;
      }
      multiPrevThinkingStreamingRef.current[respIndex] = isThinkingStreaming;
    });
  }, [message]);

  useEffect(() => {
    if (!message.multiLLMResponses?.length) return;
    const thinkingIndices: number[] = [];
    message.multiLLMResponses.forEach((response, respIndex) => {
      const parsed = extractReasoningBlock(getMultiLlmColumnDisplayBody(response), response.isStreaming);
      const isThinkingStreaming = Boolean(
        parsed.isThinkingStreaming ||
          (response.isStreaming &&
            Boolean(parsed.reasoningContent) &&
            parsed.visibleContent.trim().length === 0),
      );
      if (isThinkingStreaming) thinkingIndices.push(respIndex);
    });
    if (thinkingIndices.length === 0) {
      setMultiLiveThinkingSec({});
      return;
    }
    const tick = () => {
      setMultiLiveThinkingSec((prev: Record<number, number | null>) => {
        const next = { ...prev };
        for (const respIndex of thinkingIndices) {
          const start = multiThinkingStartRef.current[respIndex];
          if (start) next[respIndex] = Math.max(0, Math.floor((Date.now() - start) / 1000));
        }
        return next;
      });
    };
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, [message]);

  // Сбрасываем таймер при старте новой генерации этого сообщения
  useEffect(() => {
    if (message.isStreaming) {
      setThinkingDurationSec(null);
      setLiveThinkingSec(null);
      thinkingStartRef.current = null;
      prevThinkingStreamingRef.current = false;
      setMultiThinkingDurationSec({});
      setMultiLiveThinkingSec({});
      multiThinkingStartRef.current = {};
      multiPrevThinkingStreamingRef.current = {};
    }
  }, [message.id]); // eslint-disable-line react-hooks/exhaustive-deps

  // Автораскрытие блока рассуждений — только ОДИН РАЗ при старте рассуждения.
  // Ref-флаг гарантирует, что после ручного сворачивания последующие чанки
  // блок НЕ переоткрывают.
  const hasAutoExpandedRef = useRef(false);
  useEffect(() => {
    if (message.isStreaming && message.content.includes('<think>') && !hasAutoExpandedRef.current) {
      hasAutoExpandedRef.current = true;
      setReasoningExpanded(true);
    }
    // При завершении генерации сбрасываем флаг для следующей генерации
    if (!message.isStreaming) {
      hasAutoExpandedRef.current = false;
    }
  }, [message.isStreaming, message.content]);

  const hideOuterActionBar = !isUser && (message.multiLLMResponses?.length ?? 0) > 0;
  const showFollowUpSuggestions =
    !isUser &&
    !shareMode &&
    interfaceSettings.followUpAutoGenerate &&
    !message.multiLLMResponses?.length &&
    Boolean(message.followUpSuggestions?.length) &&
    (interfaceSettings.followUpShowScope === 'all' || isLastChatMessage);

  const followUpBlock = showFollowUpSuggestions ? (
    <MessageFollowUpSuggestions
      suggestions={message.followUpSuggestions}
      disabled={Boolean(message.isStreaming)}
      isDarkMode={isDarkMode}
      onSelect={(content) => dataRef.current.handleFollowUpSelect(content)}
    />
  ) : null;
  const multiLlmActionIconSx = {
    opacity: 0.7,
    p: 0.5,
    borderRadius: '6px',
    minWidth: '28px',
    width: '28px',
    height: '28px',
    '&:hover': { opacity: 1, '& .MuiSvgIcon-root': { color: 'primary.main' } },
    '&:hover:not(:disabled)': { opacity: 1, '& .MuiSvgIcon-root': { color: 'primary.main' } },
    '& .MuiSvgIcon-root': { fontSize: '18px !important', width: '18px !important', height: '18px !important' },
  } as const;
  const shouldShowBorder = isUser
    ? !interfaceSettings.userNoBorder
    : !interfaceSettings.assistantNoBorder;

  const liveGenerationSec = useElapsedSeconds(
    message.generationStartedAtMs,
    Boolean(!isUser && message.isStreaming && !message.generationDurationSec),
  );
  const generationLabelSec =
    !isUser && !message.isStreaming && message.generationDurationSec
      ? message.generationDurationSec
      : liveGenerationSec;
  const generationLabel =
    generationLabelSec !== null && generationLabelSec > 0
      ? formatGenerationDuration(generationLabelSec)
      : null;

  const messageContent = (
    <>
      <Box sx={{ display: 'flex', alignItems: 'center', mb: 0.3 }}>
        <Avatar
          sx={{ width: 24, height: 24, mr: 1, bgcolor: isUser ? 'primary.dark' : 'transparent' }}
          src={isUser ? undefined : '/astra.png'}
        >
          {isUser ? <PersonIcon /> : null}
        </Avatar>
        <Typography variant="caption" sx={{ opacity: 0.8, fontSize: '0.75rem', fontWeight: 500 }}>
          {isUser
            ? (interfaceSettings.showUserName && username ? username : 'Вы')
            : (message.chainCurrentAgent?.agentName
              || message.chainSteps?.[message.chainSteps.length - 1]?.agentName
              || 'AstraChat')}
        </Typography>
        {!isUser && generationLabel ? (
          message.isStreaming ? (
            <ThinkingShimmerText
              isDarkMode={isDarkMode}
              fontSize="0.7rem"
              fontWeight={500}
              sx={{ ml: 1, whiteSpace: 'nowrap' }}
            >
              {`думает ${generationLabel}`}
            </ThinkingShimmerText>
          ) : (
            <Typography
              variant="caption"
              sx={{
                ml: 1,
                opacity: 0.65,
                fontSize: '0.7rem',
                fontWeight: 500,
                color: 'text.secondary',
                whiteSpace: 'nowrap',
              }}
            >
              {`думал ${generationLabel}`}
            </Typography>
          )
        ) : null}
        <Typography variant="caption" sx={{ ml: 'auto', opacity: 0.6, fontSize: '0.7rem' }}>
          {dataRef.current.formatTimestamp(message.timestamp)}
        </Typography>
      </Box>

      <Box sx={{ width: '100%' }}>
        {!isUser && message.documentSearch && (
          <DocumentSearchPanel trace={message.documentSearch} />
        )}
        {!isUser && message.mcpToolCalls && message.mcpToolCalls.length > 0 && !message.multiLLMResponses?.length && (
          <McpToolCallsPanel toolCalls={message.mcpToolCalls} />
        )}

        {showImageGenerationPlaceholder ? (
          <Box sx={{ mb: parsedMessage.visibleContent.trim() ? 1 : 0 }}>
            <ImageGenerationPlaceholder />
          </Box>
        ) : null}

        {!isUser && assistantInlineAttachments && assistantInlineAttachments.length > 0 && (
          <InlineAttachmentsList
            files={assistantInlineAttachments.map((a) => ({
              name: a.name,
              contentType: a.contentType,
              imageSrc: a.contentType === 'image' ? a.preview : undefined,
              videoSrc: a.contentType === 'video' ? a.preview : undefined,
              size: a.size,
            }))}
            isDarkMode={isDarkMode}
            variant="message"
            onImageExpand={(resolvedSrc, name) => setLightboxSrc({ src: resolvedSrc, name })}
            sx={{ mb: 1 }}
          />
        )}

        {/* Inline-вложения пользователя — тот же вид, что при прикреплении через «+» */}
        {isUser && message.inlineAttachments && message.inlineAttachments.length > 0 && (
          <InlineAttachmentsList
            files={message.inlineAttachments.map((a) => ({
              name: a.name,
              contentType: a.contentType,
              imageSrc: a.contentType === 'image' ? a.preview : undefined,
              videoSrc: a.contentType === 'video' ? a.preview : undefined,
              size: a.size,
            }))}
            isDarkMode={isDarkMode}
            variant="message"
            onImageExpand={(resolvedSrc, name) => setLightboxSrc({ src: resolvedSrc, name })}
            sx={{ mb: 1 }}
          />
        )}

        {message.multiLLMResponses && message.multiLLMResponses.length > 0 ? (
          <Box
            sx={{
              display: 'grid',
              gridTemplateColumns: {
                xs: '1fr',
                sm: `repeat(${Math.min(message.multiLLMResponses.length, 4)}, minmax(0, 1fr))`,
              },
              gap: 1.5,
            }}
          >
            {message.multiLLMResponses.map((response, respIndex) => {
              const displayBody = getMultiLlmColumnDisplayBody(response);
              const parsedResponse = extractReasoningBlock(displayBody, response.isStreaming);
              const isResponseReasoningStreaming = Boolean(
                parsedResponse.isThinkingStreaming ||
                  (response.isStreaming && Boolean(parsedResponse.reasoningContent)),
              );
              return (
                <Card
                  key={`${response.model}-${respIndex}`}
                  onMouseEnter={() => setHoveredMultiLlmCol(respIndex)}
                  onMouseLeave={() => setHoveredMultiLlmCol((h) => (h === respIndex ? null : h))}
                  sx={{
                    border: '1px solid',
                    borderColor: response.error ? 'error.main' : 'divider',
                    bgcolor: response.error ? 'error.light' : 'background.paper',
                    display: 'flex',
                    flexDirection: 'column',
                  }}
                >
                  <CardContent sx={{ flex: 1, display: 'flex', flexDirection: 'column', pb: 1 }}>
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1, flexWrap: 'wrap' }}>
                      <Typography variant="caption" fontWeight="bold" color={response.error ? 'error' : 'primary'}>
                        {response.model}
                      </Typography>
                      {response.isStreaming && (
                        <MultiLlmGeneratingChip startedAtMs={response.generationStartedAtMs} />
                      )}
                      {!response.isStreaming &&
                        !response.error &&
                        response.generationDurationSec != null &&
                        response.generationDurationSec > 0 && (
                          <Typography variant="caption" sx={{ opacity: 0.65, fontSize: '0.7rem' }}>
                            думал {formatGenerationDuration(response.generationDurationSec)}
                          </Typography>
                        )}
                      {response.error && <Chip label="Ошибка" size="small" color="error" />}
                    </Box>
                    {response.error ? (
                      <Alert severity="error" sx={{ mt: 1 }}>
                        <Typography variant="body2">{response.content}</Typography>
                      </Alert>
                    ) : (
                      <>
                        {parsedResponse.reasoningContent ? (
                          <ReasoningBlock
                            reasoningContent={parsedResponse.reasoningContent}
                            isThinkingStreaming={isResponseReasoningStreaming}
                            isExpanded={Boolean(multiReasoningExpanded[respIndex])}
                            onToggle={() =>
                              setMultiReasoningExpanded((prev) => ({ ...prev, [respIndex]: !prev[respIndex] }))
                            }
                            durationSec={multiThinkingDurationSec[respIndex] ?? null}
                            liveThinkingSec={multiLiveThinkingSec[respIndex] ?? null}
                            isDarkMode={isDarkMode}
                          />
                        ) : null}
                        <MessageRenderer
                          content={parsedResponse.visibleContent}
                          isStreaming={response.isStreaming && !isResponseReasoningStreaming}
                          onSendMessage={dataRef.current.handleSendMessageFromRenderer}
                          messageId={`${message.id || index}-m${respIndex}`}
                        />
                        {message.mcpToolCalls?.some((t) => !t.model || t.model === response.model) ? (
                          <McpToolCallsPanel
                            toolCalls={message.mcpToolCalls.filter(
                              (t) => !t.model || t.model === response.model,
                            )}
                          />
                        ) : null}
                      </>
                    )}
                  </CardContent>
                  {!isUser && !response.error ? (
                    <Box
                      sx={{
                        display: 'flex',
                        justifyContent: 'center',
                        alignItems: 'center',
                        flexWrap: 'wrap',
                        gap: 0.5,
                        px: 1,
                        pb: 1,
                        pt: 0,
                        minHeight: 28,
                        opacity: hoveredMultiLlmCol === respIndex ? 1 : 0,
                        visibility: hoveredMultiLlmCol === respIndex ? 'visible' : 'hidden',
                      }}
                    >
                      {response.alternativeResponses && response.alternativeResponses.length > 1 ? (
                        <>
                          <Tooltip title="Предыдущий вариант">
                            <span>
                              <IconButton
                                size="small"
                                onClick={() => {
                                  const ci = response.currentResponseIndex ?? 0;
                                  if (ci <= 0 || !dataRef.current.currentChatId || !message.multiLLMResponses) return;
                                  const ni = ci - 1;
                                  const nextText = response.alternativeResponses![ni] ?? '';
                                  const newCols = message.multiLLMResponses.map((r, i) =>
                                    i === respIndex
                                      ? { ...r, currentResponseIndex: ni, content: nextText }
                                      : r,
                                  );
                                  dataRef.current.updateMessage(
                                    dataRef.current.currentChatId,
                                    message.id,
                                    undefined,
                                    false,
                                    newCols,
                                  );
                                }}
                                disabled={(response.currentResponseIndex ?? 0) === 0}
                                sx={multiLlmActionIconSx}
                              >
                                <ChevronLeftIcon fontSize="small" />
                              </IconButton>
                            </span>
                          </Tooltip>
                          <Typography
                            variant="caption"
                            sx={{ opacity: 0.7, fontSize: '0.7rem', minWidth: '35px', textAlign: 'center' }}
                          >
                            {((response.currentResponseIndex ?? 0) + 1)}/{response.alternativeResponses.length}
                          </Typography>
                          <Tooltip title="Следующий вариант">
                            <span>
                              <IconButton
                                size="small"
                                onClick={() => {
                                  const ci = response.currentResponseIndex ?? 0;
                                  const al = response.alternativeResponses!;
                                  if (
                                    ci >= al.length - 1 ||
                                    !dataRef.current.currentChatId ||
                                    !message.multiLLMResponses
                                  )
                                    return;
                                  const ni = ci + 1;
                                  const nextText = al[ni] ?? '';
                                  const newCols = message.multiLLMResponses.map((r, i) =>
                                    i === respIndex
                                      ? { ...r, currentResponseIndex: ni, content: nextText }
                                      : r,
                                  );
                                  dataRef.current.updateMessage(
                                    dataRef.current.currentChatId,
                                    message.id,
                                    undefined,
                                    false,
                                    newCols,
                                  );
                                }}
                                disabled={
                                  (response.currentResponseIndex ?? 0) >= response.alternativeResponses.length - 1
                                }
                                sx={multiLlmActionIconSx}
                              >
                                <ChevronRightIcon fontSize="small" />
                              </IconButton>
                            </span>
                          </Tooltip>
                          <Box sx={{ width: '1px', height: '16px', bgcolor: 'divider', mx: 0.5 }} />
                        </>
                      ) : null}
                      <Tooltip title="Копировать">
                        <IconButton
                          size="small"
                          onClick={() =>
                            dataRef.current.handleCopyMessage(getMultiLlmColumnDisplayText(response))
                          }
                          sx={multiLlmActionIconSx}
                        >
                          <CopyIcon />
                        </IconButton>
                      </Tooltip>
                      {!response.error && !response.isStreaming ? (
                        <MessageFeedbackBar
                          feedback={response.feedback}
                          disabled={Boolean(message.isStreaming)}
                          isDarkMode={isDarkMode}
                          compact
                          onLike={() =>
                            dataRef.current.handleMessageFeedback(message, {
                              rating: 'like',
                              multiLlmSlotIndex: respIndex,
                            })
                          }
                          onDislikeSubmit={({ tags, comment }: { tags: string[]; comment: string }) =>
                            dataRef.current.handleMessageFeedback(message, {
                              rating: 'dislike',
                              tags,
                              comment,
                              multiLlmSlotIndex: respIndex,
                            })
                          }
                          onClear={() =>
                            dataRef.current.handleMessageFeedback(message, {
                              rating: null,
                              multiLlmSlotIndex: respIndex,
                            })
                          }
                        />
                      ) : null}
                      {!shareMode ? (
                        <Tooltip title="Поделиться">
                          <IconButton
                            size="small"
                            onClick={() => dataRef.current.handleEnterShareMode()}
                            sx={multiLlmActionIconSx}
                          >
                            <ShareIcon />
                          </IconButton>
                        </Tooltip>
                      ) : null}
                      <Tooltip title="Перегенерировать">
                        <span>
                          <IconButton
                            size="small"
                            onClick={() => dataRef.current.handleRegenerateMultiLlmColumn(message, respIndex)}
                            disabled={Boolean(response.isStreaming)}
                            sx={multiLlmActionIconSx}
                          >
                            <RefreshIcon />
                          </IconButton>
                        </span>
                      </Tooltip>
                      <MessageMoreActionsMenu
                        isDarkMode={isDarkMode}
                        compact
                        isSpeaking={isSpeaking}
                        showBranch={!shareMode}
                        branchDisabled={Boolean(response.isStreaming)}
                        showExport
                        exportDisabled={Boolean(response.isStreaming) || !getMultiLlmColumnDisplayText(response)?.trim()}
                        onEdit={() => dataRef.current.handleEditMultiLlmColumn(message, respIndex)}
                        onReadAloud={() =>
                          dataRef.current.synthesizeSpeech(getMultiLlmColumnDisplayText(response))
                        }
                        onBranch={() => dataRef.current.handleBranchToNewChat(message, respIndex)}
                        onExport={(format: MessageExportFormat) => {
                          exportMessageContent(
                            getMultiLlmColumnDisplayText(response),
                            format,
                            `ответ_${(response.model || 'llm').replace(/[\\/:*?"<>|]+/g, '_').slice(0, 40)}`,
                          );
                        }}
                        iconSx={multiLlmActionIconSx}
                      />
                    </Box>
                  ) : null}
                </Card>
              );
            })}
          </Box>
        ) : chainSegments ? (
          <>
            {chainSegments.map((seg, segIdx) => {
              const parsedSeg = extractReasoningBlock(seg.content, Boolean(seg.running && message.isStreaming));
              const segReasoning = (parsedSeg.reasoningContent || seg.reasoning || '').trim();
              const segReasoningStreaming = Boolean(
                parsedSeg.isThinkingStreaming ||
                  (seg.running && message.isStreaming && parsedSeg.reasoningContent),
              );
              const segExpanded =
                chainReasoningExpanded[segIdx] ?? (segReasoningStreaming || Boolean(segReasoning));
              const waiting = Boolean(seg.running && !parsedSeg.visibleContent.trim() && !segReasoning);
              return (
                <Box key={`${seg.agentId}-${seg.agentName}-${segIdx}`}>
                  <AgentChainUpdate
                    name={seg.agentName}
                    isDarkMode={isDarkMode}
                    thinking={waiting}
                  />
                  {seg.documentSearch ? (
                    <DocumentSearchPanel trace={seg.documentSearch} />
                  ) : null}
                  {segReasoning ? (
                    <ReasoningBlock
                      reasoningContent={segReasoning}
                      isThinkingStreaming={segReasoningStreaming}
                      isExpanded={segExpanded}
                      onToggle={() =>
                        setChainReasoningExpanded((p) => ({ ...p, [segIdx]: !segExpanded }))
                      }
                      durationSec={seg.running ? thinkingDurationSec : null}
                      liveThinkingSec={seg.running ? liveThinkingSec : null}
                      isDarkMode={isDarkMode}
                    />
                  ) : null}
                  {parsedSeg.visibleContent.trim() ? (
                    <MessageRenderer
                      content={parsedSeg.visibleContent}
                      isStreaming={Boolean(seg.running && message.isStreaming && !segReasoningStreaming)}
                      onSendMessage={dataRef.current.handleSendMessageFromRenderer}
                      messageId={`${message.id || `msg-${index}`}-chain-${segIdx}`}
                    />
                  ) : null}
                </Box>
              );
            })}
          </>
        ) : (
          <>
            {parsedMessage.reasoningContent ? (
              <ReasoningBlock
                reasoningContent={parsedMessage.reasoningContent}
                isThinkingStreaming={isReasoningStreaming}
                isExpanded={reasoningExpanded}
                onToggle={() => setReasoningExpanded((p) => !p)}
                durationSec={thinkingDurationSec}
                liveThinkingSec={liveThinkingSec}
                isDarkMode={isDarkMode}
              />
            ) : null}
            {!showImageGenerationPlaceholder || parsedMessage.visibleContent.trim() ? (
              <MessageRenderer
                content={parsedMessage.visibleContent}
                isStreaming={message.isStreaming && !isReasoningStreaming}
                onSendMessage={dataRef.current.handleSendMessageFromRenderer}
                messageId={message.id || `msg-${index}`}
              />
            ) : null}
          </>
        )}
      </Box>
    </>
  );

  return (
    <Box
      ref={(el: HTMLDivElement | null) => { dataRef.current.messageRefs.current[navIndex] = el; }}
      data-message-index={navIndex}
      sx={{ display: 'flex', flexDirection: 'row', alignItems: 'flex-start', mb: 1.5, width: '100%', minWidth: 0, maxWidth: '100%' }}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      {shareMode && isPairStart && (
        <Checkbox
          checked={isSelected}
          onChange={() => dataRef.current.handleToggleMessage(message.id, nextMessageId!)}
          sx={{ mt: 1, mr: 1, p: 0.5 }}
        />
      )}

      <Box
        sx={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: interfaceSettings.leftAlignMessages ? 'flex-start' : (isUser ? 'flex-end' : 'flex-start'),
          flex: 1,
          minWidth: 0,
          maxWidth: '100%',
        }}
      >
        {shouldShowBorder ? (
          <Card
            className="message-bubble"
            data-theme={isDarkMode ? 'dark' : 'light'}
            sx={{
              maxWidth: interfaceSettings.leftAlignMessages ? '100%' : (isUser ? '75%' : '100%'),
              minWidth: isUser ? '180px' : 0,
              width: interfaceSettings.leftAlignMessages ? '100%' : (isUser ? undefined : '100%'),
              overflowX: 'hidden',
              backgroundColor: isUser ? 'primary.main' : isDarkMode ? 'background.paper' : '#f8f9fa',
              color: isUser ? 'primary.contrastText' : isDarkMode ? 'text.primary' : '#333',
              boxShadow: isDarkMode ? '0 2px 8px rgba(0,0,0,0.15)' : '0 2px 8px rgba(0,0,0,0.1)',
            }}
          >
            <CardContent sx={{ p: 1.2, '&:last-child': { pb: 1.2 }, minWidth: 0, overflowX: 'hidden' }}>
              {messageContent}
              {followUpBlock}
            </CardContent>
          </Card>
        ) : (
          <Box sx={{ width: '100%', minWidth: 0, maxWidth: '100%', overflowX: 'hidden', p: 1.2 }}>
            {messageContent}
            {followUpBlock}
          </Box>
        )}

        {/* Кнопки действий снизу карточки (для multi-LLM — только под каждой колонкой) */}
        {!hideOuterActionBar && (
        <Box sx={{
          display: 'flex', justifyContent: 'center', alignItems: 'center', gap: 0.5,
          mt: 1, minHeight: 28,
          opacity: isHovered ? 1 : 0,
          visibility: isHovered ? 'visible' : 'hidden',
        }}>
          {/* Навигация по вариантам ответов */}
          {!isUser && message.alternativeResponses && message.alternativeResponses.length > 1 && (
            <>
              <Tooltip title="Предыдущий вариант">
                <span>
                  <IconButton
                    size="small"
                    onClick={() => {
                      const ci = message.currentResponseIndex ?? 0;
                      if (ci > 0) {
                        const ni = ci - 1;
                        const nextAttachments = message.inlineAttachmentVariants?.[ni];
                        dataRef.current.updateMessage(
                          dataRef.current.currentChatId!, message.id,
                          message.alternativeResponses![ni],
                          undefined, undefined, message.alternativeResponses, ni,
                          undefined, undefined, undefined,
                          nextAttachments?.length ? nextAttachments : undefined,
                        );
                      }
                    }}
                    disabled={(message.currentResponseIndex ?? 0) === 0}
                    sx={{ opacity: 0.7, p: 0.5, borderRadius: '6px', minWidth: '28px', width: '28px', height: '28px',
                      '&:hover:not(:disabled)': { opacity: 1, '& .MuiSvgIcon-root': { color: 'primary.main' } } }}
                  >
                    <ChevronLeftIcon fontSize="small" />
                  </IconButton>
                </span>
              </Tooltip>
              <Typography variant="caption" sx={{ opacity: 0.7, fontSize: '0.7rem', minWidth: '35px', textAlign: 'center' }}>
                {((message.currentResponseIndex ?? 0) + 1)}/{message.alternativeResponses.length}
              </Typography>
              <Tooltip title="Следующий вариант">
                <span>
                  <IconButton
                    size="small"
                    onClick={() => {
                      const ci = message.currentResponseIndex ?? 0;
                      if (ci < message.alternativeResponses!.length - 1) {
                        const ni = ci + 1;
                        const nextAttachments = message.inlineAttachmentVariants?.[ni];
                        dataRef.current.updateMessage(
                          dataRef.current.currentChatId!, message.id,
                          message.alternativeResponses![ni],
                          undefined, undefined, message.alternativeResponses, ni,
                          undefined, undefined, undefined,
                          nextAttachments?.length ? nextAttachments : undefined,
                        );
                      }
                    }}
                    disabled={(message.currentResponseIndex ?? 0) >= message.alternativeResponses!.length - 1}
                    sx={{ opacity: 0.7, p: 0.5, borderRadius: '6px', minWidth: '28px', width: '28px', height: '28px',
                      '&:hover:not(:disabled)': { opacity: 1, '& .MuiSvgIcon-root': { color: 'primary.main' } } }}
                  >
                    <ChevronRightIcon fontSize="small" />
                  </IconButton>
                </span>
              </Tooltip>
              <Box sx={{ width: '1px', height: '16px', bgcolor: 'divider', mx: 0.5 }} />
            </>
          )}

          <Tooltip title="Копировать">
            <IconButton
              size="small"
              onClick={() => {
                if (message.multiLLMResponses && message.multiLLMResponses.length > 0) {
                  dataRef.current.handleCopyMessage(
                    message.multiLLMResponses.map(r => `[${r.model}]\n${r.content}`).join('\n\n---\n\n')
                  );
                } else {
                  dataRef.current.handleCopyMessage(message.content);
                }
              }}
              className="message-copy-button"
              data-theme={isDarkMode ? 'dark' : 'light'}
              sx={{ opacity: 0.7, p: 0.5, borderRadius: '6px', minWidth: '28px', width: '28px', height: '28px',
                '&:hover': { opacity: 1, '& .MuiSvgIcon-root': { color: 'primary.main' } },
                '& .MuiSvgIcon-root': { fontSize: '18px !important', width: '18px !important', height: '18px !important' } }}
            >
              <CopyIcon />
            </IconButton>
          </Tooltip>

          {!isUser && !message.isStreaming && (
            <MessageFeedbackBar
              feedback={message.feedback}
              disabled={Boolean(message.isStreaming)}
              isDarkMode={isDarkMode}
              onLike={() =>
                dataRef.current.handleMessageFeedback(message, { rating: 'like' })
              }
              onDislikeSubmit={({ tags, comment }: { tags: string[]; comment: string }) =>
                dataRef.current.handleMessageFeedback(message, {
                  rating: 'dislike',
                  tags,
                  comment,
                })
              }
              onClear={() =>
                dataRef.current.handleMessageFeedback(message, { rating: null })
              }
            />
          )}

          {!isUser && !shareMode && (
            <Tooltip title="Поделиться">
              <IconButton
                size="small"
                onClick={() => dataRef.current.handleEnterShareMode()}
                className="message-share-button"
                data-theme={isDarkMode ? 'dark' : 'light'}
                sx={{ opacity: 0.7, p: 0.5, borderRadius: '6px', minWidth: '28px', width: '28px', height: '28px',
                  '&:hover': { opacity: 1, '& .MuiSvgIcon-root': { color: 'primary.main' } },
                  '& .MuiSvgIcon-root': { fontSize: '18px !important', width: '18px !important', height: '18px !important' } }}
              >
                <ShareIcon />
              </IconButton>
            </Tooltip>
          )}

          {!isUser && (
            <Tooltip title="Перегенерировать">
              <IconButton
                size="small"
                onClick={() => dataRef.current.handleRegenerate(message)}
                className="message-regenerate-button"
                data-theme={isDarkMode ? 'dark' : 'light'}
                sx={{ opacity: 0.7, p: 0.5, borderRadius: '6px', minWidth: '28px', width: '28px', height: '28px',
                  '&:hover': { opacity: 1, '& .MuiSvgIcon-root': { color: 'primary.main' } },
                  '& .MuiSvgIcon-root': { fontSize: '18px !important', width: '18px !important', height: '18px !important' } }}
              >
                <RefreshIcon />
              </IconButton>
            </Tooltip>
          )}

          <MessageMoreActionsMenu
            isDarkMode={isDarkMode}
            isSpeaking={isSpeaking}
            showBranch={!isUser && !shareMode}
            branchDisabled={Boolean(message.isStreaming)}
            showExport={!isUser}
            exportDisabled={Boolean(message.isStreaming) || !(
              (message.alternativeResponses && message.currentResponseIndex !== undefined
                ? message.alternativeResponses[message.currentResponseIndex]
                : message.multiLLMResponses?.length
                  ? message.multiLLMResponses.filter((r) => !r.error).map((r) => r.content).join('')
                  : message.content) || ''
            ).trim()}
            onEdit={() => dataRef.current.handleEditClick(message)}
            onReadAloud={() => {
              let textToSpeak = message.content;
              if (!isUser && message.alternativeResponses && message.alternativeResponses.length > 0 && message.currentResponseIndex !== undefined) {
                const ci = message.currentResponseIndex;
                if (ci >= 0 && ci < message.alternativeResponses.length) textToSpeak = message.alternativeResponses[ci];
              }
              if (!isUser && message.multiLLMResponses && message.multiLLMResponses.length > 0) {
                textToSpeak = message.multiLLMResponses.filter(r => !r.error).map(r => r.content).join(' ');
              }
              dataRef.current.synthesizeSpeech(textToSpeak);
            }}
            onBranch={() => dataRef.current.handleBranchToNewChat(message)}
            onExport={!isUser ? (format: MessageExportFormat) => {
              let textToExport = message.content;
              if (message.alternativeResponses && message.alternativeResponses.length > 0 && message.currentResponseIndex !== undefined) {
                const ci = message.currentResponseIndex;
                if (ci >= 0 && ci < message.alternativeResponses.length) {
                  textToExport = message.alternativeResponses[ci];
                }
              }
              if (message.multiLLMResponses && message.multiLLMResponses.length > 0) {
                textToExport = message.multiLLMResponses
                  .filter((r) => !r.error)
                  .map((r) => `[${r.model}]\n${r.content}`)
                  .join('\n\n---\n\n');
              }
              exportMessageContent(textToExport, format, 'ответ');
            } : undefined}
          />
        </Box>
        )}
      </Box>

      {/* Лайтбокс для просмотра прикреплённых изображений */}
      <InlineImageLightbox
        open={Boolean(lightboxSrc)}
        src={lightboxSrc?.src || ''}
        name={lightboxSrc?.name || 'image'}
        onClose={() => setLightboxSrc(null)}
      />
    </Box>
  );
};

// Мемоизируем: ре-рендер только когда меняется сам message, shareMode, isSelected, isSpeaking или настройки
const MessageCard = React.memo(MessageCardComponent, (prev, next) =>
  prev.message === next.message &&
  prev.navIndex === next.navIndex &&
  prev.shareMode === next.shareMode &&
  prev.isSelected === next.isSelected &&
  prev.isSpeaking === next.isSpeaking &&
  prev.isDarkMode === next.isDarkMode &&
  prev.isLastChatMessage === next.isLastChatMessage &&
  prev.interfaceSettings === next.interfaceSettings,
);

// ================================

export default function UnifiedChatPage({
  isDarkMode,
  sidebarOpen = true,
  sidebarHidden = false,
}: UnifiedChatPageProps) {
  const navigate = useNavigate();
  const theme = useTheme();

  const workZoneMode = useWorkZoneBgMode();
  const workZoneAnimated = isWorkZoneAnimatedMode(workZoneMode);
  const workZoneBgColor = getWorkZoneBackgroundColor(isDarkMode, workZoneMode);
  const workZoneCustomImage = getWorkZoneCustomImage();

  // Режим расположения выбора модели: 'settings' | 'workspace' | 'workspace_agent'
  type ModelSelectorMode = 'settings' | 'workspace' | 'workspace_agent';
  const readModelSelectorMode = (): ModelSelectorMode => {
    const saved = localStorage.getItem('model_selector_mode');
    if (saved === 'settings' || saved === 'workspace' || saved === 'workspace_agent') return saved;
    const oldBool = localStorage.getItem('show_model_selector_in_settings');
    return oldBool === 'true' ? 'settings' : 'workspace_agent';
  };
  const [modelSelectorMode, setModelSelectorMode] = useState<ModelSelectorMode>(readModelSelectorMode);

  // Состояние для панели с диалогами (навигация по сообщениям)
  const [showDialoguesPanel, setShowDialoguesPanel] = useState(() => {
    const saved = localStorage.getItem('show_dialogues_panel');
    return saved !== null ? saved === 'true' : true;
  });
  
  // Слушаем изменения настроек
  useEffect(() => {
    const handleSettingsChange = () => {
      setModelSelectorMode(readModelSelectorMode());
      const savedPanel = localStorage.getItem('show_dialogues_panel');
      setShowDialoguesPanel(savedPanel !== null ? savedPanel === 'true' : true);
    };
    
    window.addEventListener('interfaceSettingsChanged', handleSettingsChange);
    return () => window.removeEventListener('interfaceSettingsChanged', handleSettingsChange);
  }, []);

  // Состояние для текстового чата
  const [inputMessage, setInputMessage] = useState('');
  
  // Состояние для редактирования сообщений
  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const [editingMessage, setEditingMessage] = useState<Message | null>(null);
  const [editingMultiLlmSlotIndex, setEditingMultiLlmSlotIndex] = useState<number | null>(null);
  const [editText, setEditText] = useState('');
  
  const [showVoiceDialog, setShowVoiceDialog] = useState(false);

  
  // Состояние для документов
  const [isDragging, setIsDragging] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadingFile, setUploadingFile] = useState<{ name: string; size: number; previewUrl?: string } | null>(null);
  const [attachErrorBanner, setAttachErrorBanner] = useState<string | null>(null);
  const [modelErrorBanner, setModelErrorBanner] = useState<string | null>(null);
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const [query, setQuery] = useState('');
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const [isQuerying, setIsQuerying] = useState(false);
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const [queryResponse, setQueryResponse] = useState('');
  const [showDocumentDialog, setShowDocumentDialog] = useState(false);
  const [anchorEl, setAnchorEl] = useState<null | HTMLElement>(null);
  /** Раскрытый подпункт меню «Инструменты» (колонка справа, как в LeChat). */
  const [gearToolsPanel, setGearToolsPanel] = useState<
    'main' | 'agents' | 'artifacts' | 'skills' | 'mcp' | 'coding' | 'generation' | 'model-mode'
  >('main');
  const gearSubPanelOpen =
    gearToolsPanel === 'agents'
    || gearToolsPanel === 'artifacts'
    || gearToolsPanel === 'skills'
    || gearToolsPanel === 'model-mode'
    || gearToolsPanel === 'mcp'
    || gearToolsPanel === 'coding'
    || gearToolsPanel === 'generation';
  const [modelThinkingMode, setModelThinkingMode] = useState<ModelThinkingMode>(() => {
    const saved = (localStorage.getItem(MODEL_THINKING_MODE_STORAGE_KEY) || 'fast') as ModelThinkingMode;
    return saved === 'auto' || saved === 'thinking' || saved === 'fast' ? saved : 'fast';
  });
  const gearToolsPopoverActionRef = useRef<PopoverActions | null>(null);
  /** Якорь меню «Инструменты» — верх всей пилюли ввода (кнопка виджетов съезжает при многострочном тексте). */
  const chatInputToolsAnchorRef = useRef<HTMLDivElement>(null);
  /** Ширина меню «Инструменты» = ширина пилюли ввода (две колонки на всю длину поля). */
  const [gearToolsMenuWidthPx, setGearToolsMenuWidthPx] = useState<number | null>(null);
  /** Фиксированная высота бумаги Popover: левая колонка сразу «как с агентами», без роста вниз при раскрытии. */
  const [gearToolsPaperHeightPx, setGearToolsPaperHeightPx] = useState<number | null>(null);

  /** Геометрия меню от пилюли ввода + пересчёт позиции Popover. */
  useLayoutEffect(() => {
    if (!anchorEl) return;
    const shell = chatInputToolsAnchorRef.current;
    if (shell) {
      const rect = shell.getBoundingClientRect();
      setGearToolsMenuWidthPx(Math.round(rect.width));
      setGearToolsPaperHeightPx(getChatGearMenuPaperHeightPx(rect.top));
    }
    gearToolsPopoverActionRef.current?.updatePosition();
  }, [anchorEl, gearToolsPanel]);

  useEffect(() => {
    if (!anchorEl) return;
    const shell = chatInputToolsAnchorRef.current;
    if (!shell || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(() => {
      const rect = shell.getBoundingClientRect();
      setGearToolsMenuWidthPx(Math.round(rect.width));
      setGearToolsPaperHeightPx(getChatGearMenuPaperHeightPx(rect.top));
      queueMicrotask(() => gearToolsPopoverActionRef.current?.updatePosition());
    });
    ro.observe(shell);
    return () => ro.disconnect();
  }, [anchorEl]);

  // База знаний в ответах LLM (страница KB + библиотека из настроек)
  const [useKbRag, setUseKbRag] = useState(() => isKnowledgeRagEnabled());

  useEffect(() => {
    const onRag = () => setUseKbRag(isKnowledgeRagEnabled());
    window.addEventListener(KNOWLEDGE_RAG_STORAGE_EVENT, onRag);
    return () => window.removeEventListener(KNOWLEDGE_RAG_STORAGE_EVENT, onRag);
  }, []);

  const toggleKbRag = useCallback(() => {
    setUseKbRag((prev) => {
      const next = !prev;
      setKnowledgeRagEnabled(next);
      return next;
    });
  }, []);

  useEffect(() => {
    localStorage.setItem(MODEL_THINKING_MODE_STORAGE_KEY, modelThinkingMode);
  }, [modelThinkingMode]);

  const myAgentSelection = useMyAgentSelection();
  const {
    shouldBlockRagSend: shouldBlockRagSendForChat,
    blockMessage: ragReindexBlockMessage,
    status: ragReindexStatus,
    memoryRagEnabled,
  } = useRagReindexStatus();

  // Состояние для режима "Поделиться"
  const [shareMode, setShareMode] = useState(false);
  const [selectedMessages, setSelectedMessages] = useState<Set<string>>(new Set());
  const [isCreatingShareLink, setIsCreatingShareLink] = useState(false);
  const [shareDialogOpen, setShareDialogOpen] = useState(false);
  
  // Refs
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const applyInsert = (text: string) => {
      if (!text) return;
      setInputMessage(text);
      inputRef.current?.focus();
    };
    const onInsert = (event: Event) => {
      const detail = (event as CustomEvent<{ text?: string }>).detail;
      applyInsert(typeof detail?.text === 'string' ? detail.text : '');
    };
    window.addEventListener(ASTRA_INSERT_CHAT_TEXT, onInsert);
    try {
      const pending = sessionStorage.getItem(ASTRA_INSERT_CHAT_TEXT_KEY);
      if (pending) {
        sessionStorage.removeItem(ASTRA_INSERT_CHAT_TEXT_KEY);
        applyInsert(pending);
      }
    } catch {
      /* ignore */
    }
    return () => window.removeEventListener(ASTRA_INSERT_CHAT_TEXT, onInsert);
  }, []);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const [inlineAttachments, setInlineAttachments] = useState<InlineAttachment[]>([]);
  const messageRefs = useRef<(HTMLDivElement | null)[]>([]);
  // Флаг: пользователь находится у нижнего края → автоскролл разрешён
  const isAtBottomRef = useRef(true);
  /** Реактивно: пользователь не у низа ленты → показать кнопку «вниз». */
  const [isAwayFromBottom, setIsAwayFromBottom] = useState(false);
  // Флаг: мы сами инициировали прокрутку (чтобы не ловить её в scroll-listener)
  const isProgrammaticScrollRef = useRef(false);
  // Временная пауза автоскролла при взаимодействии с UI (например, сворачивание reasoning)
  const autoScrollPauseUntilRef = useRef(0);

  const syncAwayFromBottom = useCallback((away: boolean) => {
    isAtBottomRef.current = !away;
    setIsAwayFromBottom((prev) => (prev === away ? prev : away));
  }, []);

  useEffect(() => {
    const onAttach = () => fileInputRef.current?.click();
    window.addEventListener(ASTRA_TRIGGER_ATTACH, onAttach);
    return () => window.removeEventListener(ASTRA_TRIGGER_ATTACH, onAttach);
  }, []);

  useEffect(() => {
    const onPauseAutoScroll = () => {
      autoScrollPauseUntilRef.current = Date.now() + 5000;
      syncAwayFromBottom(true);
    };
    window.addEventListener('astra_pause_chat_autoscroll', onPauseAutoScroll);
    return () => window.removeEventListener('astra_pause_chat_autoscroll', onPauseAutoScroll);
  }, [syncAwayFromBottom]);

  // Ref со всеми callback-ами для MessageCard (обновляется перед каждым рендером)
  const messageCardDataRef = useRef<MessageCardData>({} as MessageCardData);

  // Context и Socket
  const { user, token } = useAuth();
  const { state } = useAppContext();
  const { 
    clearMessages, 
    showNotification, 
    setSpeaking, 
    setRecording, 
    updateMessage, 
    patchMessageFields,
    getCurrentMessages, 
    getCurrentChat,
    createChat,
    setCurrentChat,
    updateChatTitle,
    getProjectById,
    setLoading,
    branchChatAtMessage,
  } = useAppActions();
  const { sendMessage, regenerateResponse, regenerateMultiLlmSlot, isConnected, isConnecting, stopGeneration } =
    useSocket();

  // Получаем текущий чат и сообщения
  const currentChat = getCurrentChat();
  const messages = getCurrentMessages();
  const messagesHydrating = Boolean(currentChat?.messagesUnloaded);

  astra185MarkRender({
    chatId: currentChat?.id ?? null,
    messagesLen: messages.length,
    loadingIds: state.loadingChatIds.length,
    hydrating: messagesHydrating,
  });
  const showNewChatWelcome =
    state.isInitialized && messages.length === 0 && !messagesHydrating;
  /** Спиннер только если реально нечего показать (переключение на выгруженный чат). */
  const showChatHistoryLoading = messagesHydrating && messages.length === 0;
  /** Не мелькаем спиннером при быстрой подгрузке с API/кэша. */
  const [showChatHistoryLoadingDelayed, setShowChatHistoryLoadingDelayed] = useState(false);
  useEffect(() => {
    if (!showChatHistoryLoading) {
      setShowChatHistoryLoadingDelayed(false);
      return;
    }
    const timer = window.setTimeout(() => setShowChatHistoryLoadingDelayed(true), 350);
    return () => window.clearTimeout(timer);
  }, [showChatHistoryLoading, currentChat?.id]);
  const project = currentChat?.projectId ? getProjectById(currentChat.projectId) : null;
  const virtuosoRef = useRef<VirtuosoHandle | null>(null);
  const scrollerScrollHandlerRef = useRef<(() => void) | null>(null);
  const showPlainListRef = useRef(false);
  const virtuosoScrollerElRef = useRef<HTMLDivElement | null>(null);

  const bindMessagesContainer = useCallback((el: HTMLDivElement | null) => {
    const prev = messagesContainerRef.current;
    if (prev && scrollerScrollHandlerRef.current) {
      prev.removeEventListener('scroll', scrollerScrollHandlerRef.current);
      scrollerScrollHandlerRef.current = null;
    }
    messagesContainerRef.current = el;
    if (!el) return;
    const handleScroll = () => {
      if (isProgrammaticScrollRef.current) return;
      const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
      syncAwayFromBottom(distanceFromBottom >= 120);
    };
    scrollerScrollHandlerRef.current = handleScroll;
    el.addEventListener('scroll', handleScroll, { passive: true });
  }, [syncAwayFromBottom]);

  const setVirtuosoScrollerRef = useCallback((ref: HTMLElement | Window | null) => {
    const el = ref instanceof HTMLElement ? (ref as HTMLDivElement) : null;
    virtuosoScrollerElRef.current = el;
    if (!showPlainListRef.current) {
      bindMessagesContainer(el);
    }
  }, [bindMessagesContainer]);

  const ragSendBlocked = useMemo(
    () => shouldBlockRagSendForChat({ libraryEnabled: useKbRag }),
    [shouldBlockRagSendForChat, useKbRag],
  );

  const memoryReindexing = Boolean(ragReindexStatus?.memory?.reindexing);
  // Библиотека выключена на сервере (RAG_MEMORY_ENABLED=0) - тумблер не должен нажиматься
  const libraryDisabled = memoryReindexing || !memoryRagEnabled;
  const libraryDisabledHint = !memoryRagEnabled
    ? MEMORY_RAG_DISABLED_HINT
    : ragReindexBlockMessage || RAG_REINDEX_BLOCK_PLACEHOLDER;

  const hasActiveChatStreaming = useMemo(
    () =>
      messages.some(
        (m) =>
          m.isStreaming || (m.multiLLMResponses?.some((r) => r.isStreaming) ?? false),
      ),
    [messages],
  );
  const currentChatLoading = useMemo(
    () => (currentChat ? state.loadingChatIds.includes(currentChat.id) : false),
    [currentChat, state.loadingChatIds],
  );

  const visibleMessages = useMemo(() => {
    return messages.filter((message) => {
      const isMediaGenStreamingPlaceholder =
        message.role === 'assistant' &&
        (message.isImageGenerating || message.isVideoGenerating) &&
        message.isStreaming &&
        !message.inlineAttachments?.length &&
        !message.content.trim();
      if (isMediaGenStreamingPlaceholder) return false;

      const isEmptyAssistantPlaceholder =
        message.role === 'assistant' &&
        !message.content.trim() &&
        !message.mcpToolCalls?.length &&
        !message.multiLLMResponses?.length &&
        !message.documentSearch &&
        !message.isImageGenerating &&
        !message.isVideoGenerating &&
        !message.inlineAttachments?.length &&
        !message.chainCurrentAgent &&
        !message.chainSteps?.length;
      if (!isEmptyAssistantPlaceholder) return true;
      const parsedAssistant = extractReasoningBlock(message.content || '', message.isStreaming);
      if (currentChatLoading && !parsedAssistant?.reasoningContent?.trim()) {
        return false;
      }
      return true;
    });
  }, [messages, currentChatLoading]);

  /**
   * Virtuoso с полным списком. Grok-style: новый вопрос у верха экрана (спейсер в Footer),
   * «Думает»/ответ ниже, ручной скролл свободный. Без plain/handoff/#185-петель.
   */
  const streamingActive = hasActiveChatStreaming || currentChatLoading;

  const prevStreamingDbgRef = useRef(streamingActive);
  if (prevStreamingDbgRef.current !== streamingActive) {
    astra185Mark(
      'stream:toggle',
      `${prevStreamingDbgRef.current}→${streamingActive} hasStreaming=${hasActiveChatStreaming} loading=${currentChatLoading}`,
    );
    prevStreamingDbgRef.current = streamingActive;
  }
  const prevChatDbgRef = useRef(currentChat?.id);
  if (prevChatDbgRef.current !== currentChat?.id) {
    astra185Mark('chat:switch', `${prevChatDbgRef.current}→${currentChat?.id}`);
    prevChatDbgRef.current = currentChat?.id;
  }

  const wantVirtuosoList = useMemo(() => {
    return visibleMessages.length > 0;
  }, [visibleMessages.length]);

  const virtuosoData = visibleMessages;

  const showVirtuosoList = wantVirtuosoList;
  const showPlainList = false;
  showPlainListRef.current = showPlainList;
  const shouldUseVirtuosoList = wantVirtuosoList;
  const virtuosoViewportPadding = VIRTUOSO_VIEWPORT_PADDING;
  const virtuosoFollowOutput = false as const;

  // === Grok-style: новый вопрос у верха экрана, ответ печатается вниз, скролл свободный ===
  const grokTurnActiveRef = useRef(false);
  const grokAnchorVisibleIndexRef = useRef<number | null>(null);
  const prevTurnActiveForGrokRef = useRef(false);
  const [grokBottomSpacerPx, setGrokBottomSpacerPx] = useState(0);

  const lastUserVisibleIndex = useMemo(() => {
    for (let i = visibleMessages.length - 1; i >= 0; i -= 1) {
      if (visibleMessages[i].role === 'user') return i;
    }
    return -1;
  }, [visibleMessages]);

  const scrollVisibleIndexToTop = useCallback((visibleIndex: number) => {
    if (visibleIndex < 0 || !virtuosoRef.current) return;
    isProgrammaticScrollRef.current = true;
    isAtBottomRef.current = false;
    virtuosoRef.current.scrollToIndex({
      index: visibleIndex,
      align: 'start',
      behavior: 'auto',
    });
    window.setTimeout(() => {
      isProgrammaticScrollRef.current = false;
    }, 80);
  }, []);

  useEffect(() => {
    grokTurnActiveRef.current = false;
    grokAnchorVisibleIndexRef.current = null;
    setGrokBottomSpacerPx(0);
  }, [currentChat?.id]);

  // Старт хода → вопрос к верху (как в Grok).
  useEffect(() => {
    const was = prevTurnActiveForGrokRef.current;
    prevTurnActiveForGrokRef.current = streamingActive;
    if (streamingActive && !was) {
      grokTurnActiveRef.current = true;
      grokAnchorVisibleIndexRef.current = lastUserVisibleIndex;
      isAtBottomRef.current = false;
      const vh = messagesContainerRef.current?.clientHeight ?? 0;
      if (vh > 0) {
        setGrokBottomSpacerPx((prev) => Math.max(prev, Math.round(vh - 140)));
      }
      requestAnimationFrame(() => scrollVisibleIndexToTop(lastUserVisibleIndex));
      window.setTimeout(() => scrollVisibleIndexToTop(lastUserVisibleIndex), 60);
      window.setTimeout(() => scrollVisibleIndexToTop(lastUserVisibleIndex), 180);
    } else if (!streamingActive && was) {
      grokTurnActiveRef.current = false;
      // Не оставляем высокий «воздух» под ответом — иначе между текстом и пилюлей пустая пропасть.
      setGrokBottomSpacerPx(0);
    }
  }, [streamingActive, lastUserVisibleIndex, scrollVisibleIndexToTop]);

  // Спейсер снизу: держит вопрос у верха, пока ответ короткий (в Footer, без remount карточек).
  useLayoutEffect(() => {
    if (!streamingActive) return;
    const container = messagesContainerRef.current;
    const visibleIndex = grokAnchorVisibleIndexRef.current ?? lastUserVisibleIndex;
    if (!container || visibleIndex < 0) return;

    const anchorMsg = visibleMessages[visibleIndex];
    if (!anchorMsg) return;
    const navIndex = messages.findIndex((m) => m.id === anchorMsg.id);
    const qEl =
      (navIndex >= 0 ? messageRefs.current[navIndex] : null) ||
      (document.querySelector(
        `[data-message-index="${navIndex >= 0 ? navIndex : visibleIndex}"]`,
      ) as HTMLElement | null);

    const lastMsg = visibleMessages[visibleMessages.length - 1];
    const lastNav = lastMsg ? messages.findIndex((m) => m.id === lastMsg.id) : -1;
    const lastEl =
      (lastNav >= 0 ? messageRefs.current[lastNav] : null) ||
      (document.querySelector(
        `[data-message-index="${lastNav >= 0 ? lastNav : visibleMessages.length - 1}"]`,
      ) as HTMLElement | null);

    if (!qEl || !lastEl) return;
    const turnHeight = lastEl.getBoundingClientRect().bottom - qEl.getBoundingClientRect().top;
    const next = Math.max(0, Math.round(container.clientHeight - turnHeight - 24));
    setGrokBottomSpacerPx((prev) => (Math.abs(prev - next) > 8 ? next : prev));
  }, [streamingActive, messages, visibleMessages, lastUserVisibleIndex]);

  useLayoutEffect(() => {
    if (virtuosoScrollerElRef.current) {
      bindMessagesContainer(virtuosoScrollerElRef.current);
    }
  }, [bindMessagesContainer]);

  const virtuosoInitialLocation = useMemo(
    () => ({
      index: Math.max(0, visibleMessages.length - 1),
      align: 'end' as const,
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [currentChat?.id],
  );

  // Якорь к низу при открытии/перезагрузке чата.
  // initialTopMostItemIndex Virtuoso ненадёжен, когда реальные карточки выше defaultItemHeight,
  // поэтому доводим scrollToIndex несколько раз, пока высоты не измерятся.
  const initialAnchorChatRef = useRef<string | null>(null);
  const initialAnchorDoneRef = useRef(false);
  useEffect(() => {
    const chatId = currentChat?.id ?? null;
    if (initialAnchorChatRef.current !== chatId) {
      initialAnchorChatRef.current = chatId;
      initialAnchorDoneRef.current = false;
    }
    if (!chatId) return;
    if (visibleMessages.length === 0) return;
    if (initialAnchorDoneRef.current) return;
    // Живой ход (Grok): не тянем к низу при появлении первых сообщений.
    if (streamingActive || grokTurnActiveRef.current) {
      initialAnchorDoneRef.current = true;
      return;
    }

    isAtBottomRef.current = true;
    let cancelled = false;
    const lastIndex = Math.max(0, visibleMessages.length - 1);
    const anchor = () => {
      if (cancelled) return;
      if (grokTurnActiveRef.current) return;
      // Пользователь сам проскроллил вверх — не мешаем.
      if (!isAtBottomRef.current) return;
      isProgrammaticScrollRef.current = true;
      if (shouldUseVirtuosoList && virtuosoRef.current) {
        virtuosoRef.current.scrollToIndex({ index: lastIndex, align: 'end', behavior: 'auto' });
      } else if (messagesContainerRef.current) {
        messagesContainerRef.current.scrollTop = messagesContainerRef.current.scrollHeight;
      }
      window.setTimeout(() => {
        isProgrammaticScrollRef.current = false;
      }, 30);
    };
    anchor();
    const raf = requestAnimationFrame(anchor);
    const timers = [60, 180, 360, 700].map((d) => window.setTimeout(anchor, d));
    // После окна доводки помечаем чат «заякорен», дальше работает обычный автоскролл.
    const doneTimer = window.setTimeout(() => {
      initialAnchorDoneRef.current = true;
    }, 750);
    return () => {
      cancelled = true;
      cancelAnimationFrame(raf);
      timers.forEach((t) => window.clearTimeout(t));
      window.clearTimeout(doneTimer);
    };
  }, [currentChat?.id, visibleMessages.length, shouldUseVirtuosoList, streamingActive]);

  const hasRunningMcpTools = useMemo(() => {
    if (!currentChatLoading) return false;
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      const message = messages[i];
      if (message.role !== 'assistant') continue;
      if (!message.mcpToolCalls?.length) return false;
      return mergeMcpToolCalls(message.mcpToolCalls).some((exec) => exec.status === 'running');
    }
    return false;
  }, [messages, currentChatLoading]);

  const lastStreamingAssistant = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      const message = messages[i];
      if (message.role !== 'assistant') continue;
      if (message.isStreaming || (message.multiLLMResponses?.some((r) => r.isStreaming) ?? false)) {
        return message;
      }
      break;
    }
    return null;
  }, [messages]);

  const chainThinkingName = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      const message = messages[i];
      if (message.role !== 'assistant') continue;
      const name = message.chainCurrentAgent?.agentName?.trim();
      if (name) return name;
      break;
    }
    return null;
  }, [messages]);

  const dropdownPanelSx = getDropdownPanelSx(isDarkMode);
  const dropdownItemSx = useMemo(() => getDropdownItemSx(isDarkMode), [isDarkMode]);

  /** Как поле «Модель» / «Категория» в AgentConstructorPanel: outlined без синей обводки при «фокусе». */
  const multiLlmFormFieldInputSx = useMemo(() => getFormFieldInputSx(isDarkMode), [isDarkMode]);
  const multiLlmModelFieldSx = useMemo(
    () =>
      [
        multiLlmFormFieldInputSx,
        {
          '& .MuiOutlinedInput-root': { cursor: 'pointer' },
          '& .MuiOutlinedInput-root.Mui-focused fieldset': {
            borderColor: isDarkMode ? 'rgba(255,255,255,0.23)' : 'rgba(0,0,0,0.23)',
            borderWidth: '1px',
          },
          '& .MuiOutlinedInput-root:hover fieldset': {
            borderColor: isDarkMode ? 'rgba(255,255,255,0.4)' : 'rgba(0,0,0,0.4)',
          },
          '& .MuiOutlinedInput-root.Mui-focused:hover fieldset': {
            borderColor: isDarkMode ? 'rgba(255,255,255,0.4)' : 'rgba(0,0,0,0.4)',
          },
          '& .MuiInputLabel-root.Mui-focused': {
            color: isDarkMode ? 'rgba(255,255,255,0.7)' : 'rgba(0,0,0,0.6)',
          },
          '& .MuiFormLabel-asterisk': { color: '#f44336' },
        },
      ] as SxProps<Theme>,
    [multiLlmFormFieldInputSx, isDarkMode],
  );

  /** Выровнять по высоте с триггером AgentSelector (py 0.75 + строка ~0.82rem) — компактнее 32px. */
  const multiLlmModeToggleIconButtonSx = useMemo(
    () => ({
      flexShrink: 0,
      boxSizing: 'border-box' as const,
      width: 30,
      height: 30,
      minWidth: 30,
      minHeight: 30,
      p: 0,
      m: 0,
      borderRadius: '10px',
      display: 'inline-flex',
      alignItems: 'center',
      justifyContent: 'center',
      overflow: 'visible',
      lineHeight: 0,
      bgcolor: isDarkMode ? 'rgba(0,0,0,0.25)' : 'rgba(255,255,255,0.9)',
      border: isDarkMode ? '1px solid rgba(255,255,255,0.15)' : '1px solid rgba(0,0,0,0.12)',
      color: isDarkMode ? 'white' : 'text.primary',
      transition: 'background 0.15s, border-color 0.15s',
      '&:hover': {
        bgcolor: isDarkMode ? 'rgba(0,0,0,0.35)' : 'rgba(255,255,255,1)',
        borderColor: isDarkMode ? 'rgba(255,255,255,0.3)' : 'rgba(0,0,0,0.2)',
      },
    }),
    [isDarkMode],
  );

  // Сбрасываем поле ввода при переключении между чатами, чтобы черновик не "дублировался"
  useEffect(() => {
    setInputMessage('');
  }, [state.currentChatId]);

  // MCP из карточки активного агента → выбор серверов для текущего чата
  useEffect(() => {
    applyStoredAgentMcpToChat(state.currentChatId);
  }, [state.currentChatId]);

  useEffect(() => {
    const onAgentSelected = (e: Event) => {
      const detail = (e as CustomEvent<{ config?: Record<string, unknown> } | null>).detail;
      if (detail === null) return;
      if (detail?.config) {
        applyAgentMcpToChat(state.currentChatId, detail.config);
      } else {
        applyStoredAgentMcpToChat(state.currentChatId);
      }
    };
    window.addEventListener('agentSelected', onAgentSelected);
    return () => window.removeEventListener('agentSelected', onAgentSelected);
  }, [state.currentChatId]);

  const [imageGenModeOn, setImageGenModeOn] = useState(() => isImageGenerationModeEnabled(currentChat?.id));
  const [videoGenModeOn, setVideoGenModeOn] = useState(() => isVideoGenerationModeEnabled(currentChat?.id));
  const generationModeOn = imageGenModeOn || videoGenModeOn;

  useEffect(() => {
    setImageGenModeOn(isImageGenerationModeEnabled(currentChat?.id));
    setVideoGenModeOn(isVideoGenerationModeEnabled(currentChat?.id));
  }, [currentChat?.id]);

  useEffect(() => {
    const sync = () => {
      setImageGenModeOn(isImageGenerationModeEnabled(currentChat?.id));
      setVideoGenModeOn(isVideoGenerationModeEnabled(currentChat?.id));
    };
    window.addEventListener('astrachatGenerationModeChanged', sync);
    window.addEventListener('astrachatImageGenModeChanged', sync);
    window.addEventListener('astrachatVideoGenModeChanged', sync);
    return () => {
      window.removeEventListener('astrachatGenerationModeChanged', sync);
      window.removeEventListener('astrachatImageGenModeChanged', sync);
      window.removeEventListener('astrachatVideoGenModeChanged', sync);
    };
  }, [currentChat?.id]);

  const ensureModelSelectedForSend = useCallback((message?: string): boolean => {
    if (isAnyGenerationModeEnabled(currentChat?.id)) {
      return Boolean((message || '').trim());
    }
    const rawAgentId = localStorage.getItem('active_agent_id');
    const parsedAgentId = rawAgentId ? parseInt(rawAgentId, 10) : NaN;
    if (Number.isFinite(parsedAgentId)) {
      return true;
    }
    const modelPath = localStorage.getItem(LAST_SELECTED_MODEL_PATH_STORAGE_KEY);
    if (isValidSelectedModelPath(modelPath)) {
      return true;
    }
    setModelErrorBanner('Модель не выбрана! Пожалуйста, выберите модель');
    return false;
  }, [currentChat?.id]);

  // Стабильный обработчик для MessageRenderer (НЕ меняется при ререндерах!)
  const handleSendMessageFromRendererRef = useRef<((prompt: string) => void) | null>(null);
  const clearFollowUpSuggestionsRef = useRef<() => void>(() => {});
  
  // Обновляем ref при изменении зависимостей, но НЕ создаем новую функцию
  useEffect(() => {
    handleSendMessageFromRendererRef.current = (prompt: string) => {
      if (!ensureModelSelectedForSend(prompt)) {
        return;
      }
      if (currentChat && isConnected && !currentChatLoading) {
        clearFollowUpSuggestionsRef.current();
        sendMessage(prompt, currentChat.id);
      }
    };
  }, [currentChat, isConnected, currentChatLoading, sendMessage, ensureModelSelectedForSend]);
  
  // Создаем стабильную функцию ОДИН РАЗ (никогда не меняется!)
  const handleSendMessageFromRenderer = useCallback((prompt: string) => {
    handleSendMessageFromRendererRef.current?.(prompt);
  }, []); // ← Пустой массив! Функция НЕ пересоздается!
  
  // Состояние для режима multi-llm
  const activeMcpServers = useChatInputMcpIndicators(currentChat?.id);
  const artifactsIndicator = useChatInputArtifactsIndicator(currentChat?.id);
  const activeSkills = useActiveSkillIndicators(currentChat?.id);
  const { activeMcpTools } = useMcpStreamingTools();

  const mcpInputSuggestions = useMemo(() => {
    if (!activeMcpTools.length) return null;
    return <McpLiveToolsIndicator tools={activeMcpTools} />;
  }, [activeMcpTools]);

  const enabledMcpServerIds = useMemo(
    () => activeMcpServers.map((s) => s.id),
    [activeMcpServers],
  );

  const chatInputSuggestionsCatalog = useMemo(
    () => getChatInputSuggestions(enabledMcpServerIds, useKbRag),
    [enabledMcpServerIds, useKbRag],
  );

  const [availableModels, setAvailableModels] = useState<
    Array<{
      name: string;
      path: string;
      display_name?: string;
      size_mb?: number;
      provider_id?: string;
      provider_kind?: string;
      provider_default?: boolean;
    }>
  >([]);
  const [modelWindows, setModelWindows] = useState<ModelWindow[]>([{ id: '1', selectedModel: '' }]);
  const [multiLlmModelMenu, setMultiLlmModelMenu] = useState<{ windowId: string; anchorEl: HTMLElement } | null>(null);
  const [isMultiLlmMode, setIsMultiLlmMode] = useState<boolean>(
    () => localStorage.getItem('model_comparison_enabled') === 'true',
  );
  const multiLlmHasSelection = useMemo(
    () => modelWindows.some((w) => Boolean(w.selectedModel)),
    [modelWindows],
  );
  const multiLlmInputBlocked = isMultiLlmMode && !multiLlmHasSelection;
  const multiLlmSelectedPaths = useMemo(
    () => modelWindows.map((w) => w.selectedModel).filter(Boolean),
    [modelWindows],
  );
  const chatContextUsage = useChatContextUsage({
    messages,
    draftText: inputMessage,
    inlineAttachments,
    availableModels,
    configuredContextSize: state.modelSettings.context_size,
    loadedModelCtx: state.currentModel?.n_ctx,
    isMultiLlmMode,
    multiLlmModelPaths: multiLlmSelectedPaths,
    chatId: state.currentChatId,
    useKbRag,
    projectInstructions: project?.instructions ?? null,
  });
  const chatContextModelLabel = useMemo(() => {
    if (isMultiLlmMode && multiLlmSelectedPaths.length > 0) {
      if (multiLlmSelectedPaths.length === 1) {
        const row = availableModels.find((m) => m.path === multiLlmSelectedPaths[0]);
        return row?.display_name || row?.name || multiLlmSelectedPaths[0];
      }
      return `${multiLlmSelectedPaths.length} модели (мин. лимит)`;
    }
    const path = localStorage.getItem(LAST_SELECTED_MODEL_PATH_STORAGE_KEY) || '';
    const row = availableModels.find((m) => m.path === path);
    return row?.display_name || row?.name || (path ? path.split('/').pop() : null);
  }, [isMultiLlmMode, multiLlmSelectedPaths, availableModels]);
  const chatContextCounter = useMemo(
    () => (
      <ChatContextUsagePopover
        usage={chatContextUsage}
        isDarkMode={isDarkMode}
        modelLabel={chatContextModelLabel}
      />
    ),
    [chatContextUsage, isDarkMode, chatContextModelLabel],
  );
  const chatAwaitingTokens = useMemo(() => {
    if (!currentChatLoading || hasRunningMcpTools) return false;
    if (!lastStreamingAssistant) return true;
    // Генерация картинки/видео: в пузыре уже ImageGenerationPlaceholder — не дублируем «думает...»
    if (lastStreamingAssistant.isImageGenerating || lastStreamingAssistant.isVideoGenerating) return false;
    // Смотрим и content, и активный alternativeResponses[index]
    // (при перегенерации UI читает пустой слот, content тоже обнулён).
    const visible = getStreamingAssistantVisibleContent(lastStreamingAssistant);
    const parsed = extractReasoningBlock(visible || lastStreamingAssistant.content || '', true);
    if (parsed.reasoningContent?.trim()) return false;
    if (parsed.visibleContent.trim()) return false;
    return true;
  }, [currentChatLoading, hasRunningMcpTools, lastStreamingAssistant]);

  const chatAwaitingStartedAtMs = useMemo(() => {
    if (!chatAwaitingTokens) return null;
    if (lastStreamingAssistant?.generationStartedAtMs) {
      return lastStreamingAssistant.generationStartedAtMs;
    }
    // Плейсхолдер ассистента с isStreaming:false (ждём первый токен).
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      const m = messages[i];
      if (m.role === 'assistant' && m.generationStartedAtMs) return m.generationStartedAtMs;
      if (m.role === 'user') break;
    }
    return null;
  }, [chatAwaitingTokens, lastStreamingAssistant, messages]);

  const suggestionsDisabled =
    currentChatLoading || hasActiveChatStreaming || multiLlmInputBlocked || chatAwaitingTokens;

  const renderChatInputSuggestions = (maxWidth: string | number) => {
    if (!interfaceSettings.followUpAutoGenerate) return null;
    return (
    <ChatInputSuggestions
      suggestions={chatInputSuggestionsCatalog}
      inputValue={inputMessage}
      disabled={suggestionsDisabled}
      isDarkMode={isDarkMode}
      maxWidth={maxWidth}
      contentInset={suggestionsContentInset}
      onSelect={(text) => {
        setInputMessage(text);
        inputRef.current?.focus();
      }}
    />
    );
  };

  /** Плейсхолдер поля ввода: без dev-текста про порт 8000; при активной генерации — обычная подсказка (кнопка стоп и так видна). */
  const chatMainPlaceholder = useMemo(() => {
    if (ragSendBlocked) {
      return ragReindexBlockMessage || RAG_REINDEX_BLOCK_PLACEHOLDER;
    }
    if (!isConnected) {
      if (isConnecting) return 'Подключение к серверу...';
      return 'Нет соединения с сервером';
    }
    if (isMultiLlmMode && !multiLlmHasSelection) {
      return 'Выберите модели для сравнения (до 4, хотя бы одну)';
    }
    if (lastStreamingAssistant?.isVideoGenerating) return 'Генерация видео…';
    if (lastStreamingAssistant?.isImageGenerating) return 'Генерация изображения…';
    if (videoGenModeOn) return 'Опишите видео — текст сообщения станет промптом';
    if (imageGenModeOn) return 'Опишите изображение — текст сообщения станет промптом';
    if (chatAwaitingTokens) {
      return chainThinkingName ? `${chainThinkingName} думает...` : 'astrachat думает...';
    }
    return 'Чем я могу помочь вам сегодня?';
  }, [
    ragSendBlocked,
    ragReindexBlockMessage,
    isConnected,
    isConnecting,
    isMultiLlmMode,
    multiLlmHasSelection,
    chatAwaitingTokens,
    lastStreamingAssistant?.isImageGenerating,
    lastStreamingAssistant?.isVideoGenerating,
    imageGenModeOn,
    videoGenModeOn,
    chainThinkingName,
  ]);
  const socketBlocksChatInput = !isConnected && !isConnecting && !token;
  const chatSendDisabled =
    (!inputMessage.trim() && inlineAttachments.length === 0 && !isUploading)
    || socketBlocksChatInput
    || multiLlmInputBlocked
    || chatAwaitingTokens
    || ragSendBlocked;
  const prevAgentModeRef = useRef<string | undefined>(undefined);
  const skipNextMultiLlmChatResetRef = useRef(false);
  const lastMultiLlmPostedKeyRef = useRef<string>('');

  const handleOpenMcpGearPanel = useCallback(() => {
    const shell = chatInputToolsAnchorRef.current;
    if (shell) {
      setGearToolsPanel('mcp');
      setAnchorEl(shell);
      const rect = shell.getBoundingClientRect();
      setGearToolsMenuWidthPx(Math.round(rect.width));
      setGearToolsPaperHeightPx(getChatGearMenuPaperHeightPx(rect.top));
    }
  }, []);

  const handleOpenGenerationGearPanel = useCallback(() => {
    const shell = chatInputToolsAnchorRef.current;
    if (shell) {
      setGearToolsPanel('generation');
      setAnchorEl(shell);
      const rect = shell.getBoundingClientRect();
      setGearToolsMenuWidthPx(Math.round(rect.width));
      setGearToolsPaperHeightPx(getChatGearMenuPaperHeightPx(rect.top));
    }
  }, []);

  const handleClearMyAgent = useCallback(() => {
    clearActiveAgent();
    showNotification('info', 'Агент снят');
  }, [showNotification]);

  const handleClearSkills = useCallback(() => {
    clearActiveSkills(currentChat?.id);
    showNotification('info', 'Skills отключены');
  }, [showNotification, currentChat?.id]);

  const handleClearArtifactsTools = useCallback(() => {
    if (!currentChat?.id) return;
    disableChatArtifactsToolsForChat(currentChat.id);
    showNotification('info', 'Режим артефактов отключён');
  }, [currentChat?.id, showNotification]);

  const libraryInputBadge = useMemo(
    () => (
      <ChatInputStatusCluster
        isDarkMode={isDarkMode}
        libraryActive={useKbRag}
        onLibraryToggle={toggleKbRag}
        generationImageActive={imageGenModeOn}
        generationVideoActive={videoGenModeOn}
        onGenerationClick={handleOpenGenerationGearPanel}
        myAgentName={myAgentSelection?.name ?? null}
        onAgentToggle={myAgentSelection?.name ? handleClearMyAgent : undefined}
        activeSkills={activeSkills}
        onSkillsToggle={activeSkills.length ? handleClearSkills : undefined}
        activeMcpServers={activeMcpServers}
        onMcpClick={handleOpenMcpGearPanel}
        artifactsActive={artifactsIndicator.active}
        artifactsTooltip={artifactsIndicator.tooltip}
        onArtifactsToggle={artifactsIndicator.active ? handleClearArtifactsTools : undefined}
      />
    ),
    [
      isDarkMode,
      useKbRag,
      toggleKbRag,
      imageGenModeOn,
      videoGenModeOn,
      handleOpenGenerationGearPanel,
      myAgentSelection?.name,
      handleClearMyAgent,
      activeSkills,
      handleClearSkills,
      activeMcpServers,
      handleOpenMcpGearPanel,
      artifactsIndicator.active,
      artifactsIndicator.tooltip,
      handleClearArtifactsTools,
    ],
  );

  const handleToggleMultiLlmMode = useCallback(async () => {
    if (currentChatLoading || hasActiveChatStreaming) {
      showNotification('warning', 'Дождитесь окончания генерации перед сменой режима');
      return;
    }
    const next = !isMultiLlmMode;
    setIsMultiLlmMode(next);
    localStorage.setItem('model_comparison_enabled', next ? 'true' : 'false');
    if (!next) {
      lastMultiLlmPostedKeyRef.current = '';
    }
    showNotification('info', next ? 'Режим: сравнение моделей' : 'Режим: обычный чат');
  }, [isMultiLlmMode, showNotification, currentChatLoading, hasActiveChatStreaming]);

  /** Кнопка multi-LLM в поле ввода, когда селектор модели/агента спрятан в настройках. */
  const multiLlmSettingsExtraAction = useMemo(
    () =>
      modelSelectorMode === 'settings' ? (
        <Tooltip title="Сравнение моделей">
          <span>
            <IconButton
              onClick={() => {
                void handleToggleMultiLlmMode();
              }}
              disabled={
                currentChatLoading ||
                hasActiveChatStreaming
              }
              sx={multiLlmModeToggleIconButtonSx}
            >
              <MultiLlmModeToggleIcon isDarkMode={isDarkMode} />
            </IconButton>
          </span>
        </Tooltip>
      ) : null,
    [
      modelSelectorMode,
      handleToggleMultiLlmMode,
      isConnected,
      currentChatLoading,
      hasActiveChatStreaming,
      isDarkMode,
      multiLlmModeToggleIconButtonSx,
    ],
  );

  // Убираем автоматическое создание чатов - чаты создаются только по кнопке

  // Загружаем настройки интерфейса
  const [interfaceSettings, setInterfaceSettings] = useState(() => {
    const savedAutoTitle = localStorage.getItem('auto_generate_titles');
    const savedLargeTextAsFile = localStorage.getItem('large_text_as_file');
    const savedUserNoBorder = localStorage.getItem('user_no_border');
    const savedAssistantNoBorder = localStorage.getItem('assistant_no_border');
    const savedLeftAlignMessages = localStorage.getItem('left_align_messages');
    const savedWidescreenMode = localStorage.getItem('widescreen_mode');
    const savedShowUserName = localStorage.getItem('show_user_name');
    const savedEnableNotification = localStorage.getItem('enable_notification');
    const savedChatInputStyle = localStorage.getItem('chat_input_style');
    const savedChatAutoscrollStreaming = localStorage.getItem('chat_autoscroll_streaming');
    const followUpSettings = loadFollowUpSettings();
    return {
      autoGenerateTitles: savedAutoTitle !== null ? savedAutoTitle === 'true' : true,
      largeTextAsFile: savedLargeTextAsFile !== null ? savedLargeTextAsFile === 'true' : false,
      userNoBorder: savedUserNoBorder !== null ? savedUserNoBorder === 'true' : false,
      assistantNoBorder: savedAssistantNoBorder !== null ? savedAssistantNoBorder === 'true' : false,
      leftAlignMessages: savedLeftAlignMessages !== null ? savedLeftAlignMessages === 'true' : false,
      widescreenMode: savedWidescreenMode !== null ? savedWidescreenMode === 'true' : false,
      showUserName: savedShowUserName !== null ? savedShowUserName === 'true' : false,
      enableNotification: savedEnableNotification !== null ? savedEnableNotification === 'true' : false,
      autoScrollWhileStreaming:
        savedChatAutoscrollStreaming !== null ? savedChatAutoscrollStreaming === 'true' : false,
      chatInputStyle: (savedChatInputStyle as 'compact' | 'classic') || 'compact',
      followUpAutoGenerate: followUpSettings.followUpAutoGenerate,
      followUpShowScope: followUpSettings.followUpShowScope,
      followUpClickAction: followUpSettings.followUpClickAction,
    };
  });

  const handleFollowUpSelectRef = useRef<((content: string) => void) | null>(null);
  useEffect(() => {
    handleFollowUpSelectRef.current = (content: string) => {
      if (interfaceSettings.followUpClickAction === 'send') {
        handleSendMessageFromRendererRef.current?.(content);
        return;
      }
      setInputMessage(content);
      inputRef.current?.focus();
    };
  }, [interfaceSettings.followUpClickAction]);

  const handleFollowUpSelect = useCallback((content: string) => {
    handleFollowUpSelectRef.current?.(content);
  }, []);

  useFollowUpSuggestions({
    chatId: currentChat?.id,
    messages,
    enabled: interfaceSettings.followUpAutoGenerate,
    showScope: interfaceSettings.followUpShowScope,
    patchMessageFields,
  });

  const clearFollowUpSuggestions = useCallback(() => {
    if (!currentChat?.id) return;
    messages.forEach((m) => {
      if (m.role === 'assistant' && m.followUpSuggestions?.length) {
        patchMessageFields(currentChat.id, m.id, { followUpSuggestions: undefined });
      }
    });
  }, [currentChat?.id, messages, patchMessageFields]);

  useEffect(() => {
    clearFollowUpSuggestionsRef.current = clearFollowUpSuggestions;
  }, [clearFollowUpSuggestions]);

  const suggestionsContentInset = useMemo(() => {
    const mcpLabel =
      activeMcpServers.length === 1
        ? activeMcpServers[0].display_name
        : activeMcpServers.length > 1
          ? `${activeMcpServers.length} MCP`
          : '';
    const skillsLabel =
      activeSkills.length === 1
        ? (activeSkills[0].name || activeSkills[0].slug || 'Skill').trim()
        : activeSkills.length > 1
          ? `${activeSkills.length} Skills`
          : '';
    const clusterWidth = estimateLibraryClusterWidthPx(
      useKbRag,
      Boolean(myAgentSelection?.name),
      activeMcpServers.length > 0,
      mcpLabel,
      activeSkills.length > 0,
      skillsLabel,
      artifactsIndicator.active,
    );
    return getToolsButtonInsetSp(interfaceSettings.chatInputStyle, clusterWidth);
  }, [
    useKbRag,
    myAgentSelection?.name,
    activeMcpServers,
    activeSkills,
    artifactsIndicator.active,
    interfaceSettings.chatInputStyle,
  ]);

  // Слушаем изменения настроек интерфейса в localStorage
  useEffect(() => {
    const handleStorageChange = () => {
      const savedAutoTitle = localStorage.getItem('auto_generate_titles');
      const savedLargeTextAsFile = localStorage.getItem('large_text_as_file');
      const savedUserNoBorder = localStorage.getItem('user_no_border');
      const savedAssistantNoBorder = localStorage.getItem('assistant_no_border');
      const savedLeftAlignMessages = localStorage.getItem('left_align_messages');
      const savedWidescreenMode = localStorage.getItem('widescreen_mode');
      const savedShowUserName = localStorage.getItem('show_user_name');
      const savedEnableNotification = localStorage.getItem('enable_notification');
      const savedChatInputStyle = localStorage.getItem('chat_input_style');
      const savedChatAutoscrollStreaming = localStorage.getItem('chat_autoscroll_streaming');
      const followUpSettings = loadFollowUpSettings();
      setInterfaceSettings({
        autoGenerateTitles: savedAutoTitle !== null ? savedAutoTitle === 'true' : true,
        largeTextAsFile: savedLargeTextAsFile !== null ? savedLargeTextAsFile === 'true' : false,
        userNoBorder: savedUserNoBorder !== null ? savedUserNoBorder === 'true' : false,
        assistantNoBorder: savedAssistantNoBorder !== null ? savedAssistantNoBorder === 'true' : false,
        leftAlignMessages: savedLeftAlignMessages !== null ? savedLeftAlignMessages === 'true' : false,
        widescreenMode: savedWidescreenMode !== null ? savedWidescreenMode === 'true' : false,
        showUserName: savedShowUserName !== null ? savedShowUserName === 'true' : false,
        enableNotification: savedEnableNotification !== null ? savedEnableNotification === 'true' : false,
        autoScrollWhileStreaming:
          savedChatAutoscrollStreaming !== null ? savedChatAutoscrollStreaming === 'true' : false,
        chatInputStyle: (savedChatInputStyle as 'compact' | 'classic') || 'compact',
        followUpAutoGenerate: followUpSettings.followUpAutoGenerate,
        followUpShowScope: followUpSettings.followUpShowScope,
        followUpClickAction: followUpSettings.followUpClickAction,
      });
    };

    window.addEventListener('storage', handleStorageChange);
    // Также проверяем изменения в том же окне через кастомное событие
    window.addEventListener('interfaceSettingsChanged', handleStorageChange);

    return () => {
      window.removeEventListener('storage', handleStorageChange);
      window.removeEventListener('interfaceSettingsChanged', handleStorageChange);
    };
  }, []);

  // Автоматически обновляем название чата на основе первого сообщения пользователя
  useEffect(() => {
    if (currentChat && messages.length === 1 && interfaceSettings.autoGenerateTitles) {
      const firstMessage = messages[0];
      if (firstMessage.role === 'user' && currentChat.title === 'Новый чат') {
        const title = firstMessage.content.length > 50 
          ? firstMessage.content.substring(0, 50) + '...'
          : firstMessage.content;
        updateChatTitle(currentChat.id, title);
      }
    }
  }, [currentChat, messages, updateChatTitle, interfaceSettings.autoGenerateTitles]);

  // Убираем автоматическую остановку генерации при смене чата
  // Генерация должна происходить в том чате, где был задан вопрос

  // Состояние для кнопки "Прочесть вслух"
  const [isSpeaking, setIsSpeaking] = useState(false);
  const currentAudioRef = useRef<HTMLAudioElement | null>(null);
  const [voiceSettingsTTS] = useState(() => ({
    voice_id: localStorage.getItem('voice_id') || 'ru',
    speech_rate: parseFloat(localStorage.getItem('speech_rate') || '1.0'),
    voice_speaker: localStorage.getItem('voice_speaker') || 'baya',
  }));

  // Новый вопрос пользователя → Grok (не «к низу»). Иначе при росте ленты — «у дна».
  const prevMessagesLengthRef = useRef(0);
  useEffect(() => {
    const len = messages.length;
    if (len > prevMessagesLengthRef.current) {
      const last = messages[len - 1];
      if (last?.role === 'user' || grokTurnActiveRef.current) {
        syncAwayFromBottom(true);
      } else {
        syncAwayFromBottom(false);
      }
    }
    prevMessagesLengthRef.current = len;
  }, [messages, syncAwayFromBottom]);

  // Автоскролл к последнему сообщению — только когда пользователь у дна.
  // Не реагируем на follow-up подсказки, чтобы поле ввода не «прыгало».
  const autoscrollTrigger = useMemo(
    () =>
      messages
        .map((m) => {
          const streaming = m.isStreaming ? 1 : 0;
          const len = (m.content || '').length;
          const multi =
            m.multiLLMResponses
              ?.map((r) => `${r.isStreaming ? 1 : 0}:${(r.content || '').length}`)
              .join(',') ?? '';
          return `${m.id}|${streaming}|${len}|${multi}`;
        })
        .join(';;'),
    [messages],
  );

  useEffect(() => {
    const el = messagesContainerRef.current;
    if (!el || messages.length === 0) {
      setIsAwayFromBottom(false);
      return;
    }
    if (isProgrammaticScrollRef.current) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    syncAwayFromBottom(distanceFromBottom >= 120);
  }, [autoscrollTrigger, messages.length, syncAwayFromBottom]);

  const scrollChatToBottom = useCallback(() => {
    const lastIndex = Math.max(0, visibleMessages.length - 1);
    syncAwayFromBottom(false);
    isProgrammaticScrollRef.current = true;
    if (shouldUseVirtuosoList && virtuosoRef.current) {
      virtuosoRef.current.scrollToIndex({
        index: lastIndex,
        align: 'end',
        behavior: 'smooth',
      });
    }
    const container = messagesContainerRef.current;
    if (container) {
      container.scrollTo({ top: container.scrollHeight, behavior: 'smooth' });
    }
    window.setTimeout(() => {
      isProgrammaticScrollRef.current = false;
      const el = messagesContainerRef.current;
      if (!el) return;
      const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
      syncAwayFromBottom(distanceFromBottom >= 120);
    }, 700);
  }, [visibleMessages.length, shouldUseVirtuosoList, syncAwayFromBottom]);

  const showScrollToBottomButton = isAwayFromBottom && messages.length > 0 && !showNewChatWelcome;
  const showScrollToBottomLoader =
    showScrollToBottomButton &&
    streamingActive &&
    !interfaceSettings.autoScrollWhileStreaming;

  useEffect(() => {
    // Grok-ход: вопрос у верха — не тянем ленту к низу.
    if (grokTurnActiveRef.current) return;
    if (!interfaceSettings.autoScrollWhileStreaming) return;
    if (Date.now() < autoScrollPauseUntilRef.current) return;
    if (!isAtBottomRef.current) return;
    const container = messagesContainerRef.current;
    if (!container) return;
    isProgrammaticScrollRef.current = true;
    container.scrollTo({ top: container.scrollHeight, behavior: 'smooth' });
    const timer = setTimeout(() => { isProgrammaticScrollRef.current = false; }, 600);
    return () => clearTimeout(timer);
  }, [autoscrollTrigger, interfaceSettings.autoScrollWhileStreaming]);

  // Автоматический фокус на поле ввода при загрузке
  useEffect(() => {
    const timer = setTimeout(() => {
      if (inputRef.current) {
        inputRef.current.focus();
      }
    }, 300);
    
    return () => clearTimeout(timer);
  }, []);

  // Автоматический фокус на поле ввода при переключении чатов
  useEffect(() => {
    if (currentChat?.id) {
      const timer = setTimeout(() => {
        if (inputRef.current) {
          inputRef.current.focus();
        }
      }, 200);
      
      return () => clearTimeout(timer);
    }
  }, [currentChat?.id]);

  // Функция для воспроизведения звукового оповещения
  const playNotificationSound = useCallback(() => {
    if (!interfaceSettings.enableNotification) return;
    
    try {
      // Создаем простой звуковой сигнал через Web Audio API
      const audioContext = new (window.AudioContext || (window as any).webkitAudioContext)();
      const oscillator = audioContext.createOscillator();
      const gainNode = audioContext.createGain();
      
      oscillator.connect(gainNode);
      gainNode.connect(audioContext.destination);
      
      oscillator.frequency.value = 800; // Частота в Гц
      oscillator.type = 'sine';
      
      gainNode.gain.setValueAtTime(0.3, audioContext.currentTime);
      gainNode.gain.exponentialRampToValueAtTime(0.01, audioContext.currentTime + 0.3);
      
      oscillator.start(audioContext.currentTime);
      oscillator.stop(audioContext.currentTime + 0.3);
    } catch (error) {
    }
  }, [interfaceSettings.enableNotification]);

  // Отслеживаем завершение генерации сообщений для воспроизведения звука и фокуса в поле ввода
  const prevStreamingRef = useRef<boolean>(false);
  const prevChatBusyRef = useRef(false);
  const chatInputBusy = currentChatLoading || hasActiveChatStreaming;
  useEffect(() => {
    const isCurrentlyStreaming = hasActiveChatStreaming;

    if (prevStreamingRef.current && !isCurrentlyStreaming) {
      playNotificationSound();
    }

    prevStreamingRef.current = isCurrentlyStreaming;
  }, [hasActiveChatStreaming, playNotificationSound]);

  useEffect(() => {
    if (prevChatBusyRef.current && !chatInputBusy) {
      queueMicrotask(() => inputRef.current?.focus());
    }
    prevChatBusyRef.current = chatInputBusy;
  }, [chatInputBusy]);

  // Фокус на поле ввода при загрузке
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  useEffect(() => {
    const loadAvailableModelsOnce = async () => {
      try {
        const response = await fetch(`${getApiUrl('/api/models/available')}`);
        if (response.ok) {
          const data = await response.json();
          const newModels = data.models || [];
          setAvailableModels((prev) => {
            if (JSON.stringify(prev) !== JSON.stringify(newModels)) {
              return newModels;
            }
            return prev;
          });
        }
      } catch {
        /* ignore */
      }
    };

    void loadAvailableModelsOnce();
  }, []);

  useEffect(() => {
    if (skipNextMultiLlmChatResetRef.current) {
      skipNextMultiLlmChatResetRef.current = false;
      return;
    }
    setModelWindows((prev) => (prev.length === 0 ? [{ id: '1', selectedModel: '' }] : prev));
  }, [state.currentChatId]);

  useEffect(() => {
    const prev = prevAgentModeRef.current;
    if (prev === 'multi-llm' && !isMultiLlmMode) {
      setModelWindows([{ id: '1', selectedModel: '' }]);
    }
    prevAgentModeRef.current = isMultiLlmMode ? 'multi-llm' : 'default';
  }, [isMultiLlmMode]);

  // ================================
  // ФУНКЦИИ ТЕКСТОВОГО ЧАТА
  // ================================

  // ================================
  // ФУНКЦИИ ДЛЯ РЕЖИМА MULTI-LLM
  // ================================
  
  const addModelWindow = (): void => {
    if (modelWindows.length >= 4) {
      showNotification('warning', 'Можно добавить максимум 4 модели');
      return;
    }
    const newId = String(modelWindows.length + 1);
    setModelWindows([...modelWindows, { id: newId, selectedModel: '' }]);
  };

  const removeModelWindow = (id: string): void => {
    if (modelWindows.length <= 1) {
      void handleToggleMultiLlmMode();
      return;
    }
    setModelWindows((prev) => prev.filter((w) => w.id !== id));
  };

  const updateModelWindow = (id: string, updates: Partial<ModelWindow>): void => {
    setModelWindows((prev) => prev.map((w) => (w.id === id ? { ...w, ...updates } : w)));
  };

  const getSelectedModels = (): string[] => {
    return modelWindows.map(w => w.selectedModel).filter(m => m !== '');
  };

  const handleModelSelect = (windowId: string, modelName: string): void => {
    const selectedModels = getSelectedModels();
    
    // Проверяем, не выбрана ли эта модель в другом окне
    if (selectedModels.includes(modelName) && modelWindows.find(w => w.id === windowId)?.selectedModel !== modelName) {
      showNotification('error', 'Эта модель уже выбрана в другом окне');
      return;
    }
    
    updateModelWindow(windowId, { selectedModel: modelName });
  };

  useEffect(() => {
    if (hasActiveChatStreaming) setMultiLlmModelMenu(null);
  }, [hasActiveChatStreaming]);

  useEffect(() => {
    setMultiLlmModelMenu((prev) =>
      prev && !modelWindows.some((w) => w.id === prev.windowId) ? null : prev,
    );
  }, [modelWindows]);

  const handleSendMessageMultiLLM = async (): Promise<void> => {
    if (isUploading) {
      setAttachErrorBanner(INLINE_ATTACH_WAIT_BEFORE_SEND_MESSAGE);
      return;
    }

    if (!inputMessage.trim() || (!isConnected && !isConnecting && !token)) {
      return;
    }

    if (currentChatLoading || hasActiveChatStreaming) {
      return;
    }

    const selectedModels = getSelectedModels();
    if (selectedModels.length === 0) {
      setModelErrorBanner('Модель не выбрана! Пожалуйста, выберите модель');
      return;
    }

    let chatId = currentChat?.id;
    if (!chatId) {
      chatId = createChat('Новый чат');
      skipNextMultiLlmChatResetRef.current = true;
      setCurrentChat(chatId);
    }

    setLoading(true);

    const modelsKey = [...selectedModels].sort().join('\u0001');

    try {
      if (lastMultiLlmPostedKeyRef.current !== modelsKey) {
        const response = await fetch(`${getApiUrl('/api/model-comparison/models')}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ models: selectedModels }),
        });

        if (!response.ok) {
          throw new Error('Не удалось установить модели');
        }
        lastMultiLlmPostedKeyRef.current = modelsKey;
      }

      const inlinePayload = inlineAttachments.length
        ? {
            inline_context: inlineAttachments.filter(f => f.contentType === 'text').map(f => `[${f.name}]\n${f.content}`).join('\n\n') || undefined,
            inline_images: inlineAttachments.filter(f => f.contentType === 'image').map(f => f.content),
            attachments_meta: inlineAttachments.map(f => ({
              name: f.name,
              contentType: f.contentType as 'text' | 'image',
              preview: f.contentType === 'image' ? f.content : undefined,
              minio_object: f.minioObject,
              minio_bucket: f.minioBucket,
              ...(typeof f.size === 'number' && f.size > 0 ? { size: f.size } : {}),
              ...(typeof f.tokenEstimate === 'number' && f.tokenEstimate > 0 ? { tokenEstimate: f.tokenEstimate } : {}),
            })),
          }
        : undefined;
      sendMessage(inputMessage.trim(), chatId, true, undefined, true, inlinePayload);
      setInlineAttachments([]);

      setInputMessage('');

      setTimeout(() => {
        inputRef.current?.focus();
      }, 10);
    } catch {
      setLoading(false);
      showNotification('error', 'Ошибка отправки сообщения');
    }
  };

  const handleSendMessage = (): void => {
    if (isUploading) {
      setAttachErrorBanner(INLINE_ATTACH_WAIT_BEFORE_SEND_MESSAGE);
      return;
    }

    // Если режим multi-llm, используем специальную функцию
    if (isMultiLlmMode) {
      handleSendMessageMultiLLM();
      return;
    }

    if ((!inputMessage.trim() && inlineAttachments.length === 0) || currentChatLoading) {
      return;
    }
    if (!ensureModelSelectedForSend(inputMessage.trim())) {
      return;
    }
    if (!isConnected && !isConnecting) {
      if (!token) {
        showNotification('error', 'Нет соединения с сервером. Попробуйте переподключиться.');
        return;
      }
      // Токен есть, сокет ещё догоняет — sendMessage поставит отправку в очередь после connect
    }
    
    // Автоматически создаем новый чат, если его нет
    if (!currentChat) {
      const newChatId = createChat('Новый чат');
      setCurrentChat(newChatId);
      const messageText = inputMessage.trim();
      setInputMessage('');
      const inlinePayloadNew = inlineAttachments.length
        ? {
            inline_context: inlineAttachments.filter(f => f.contentType === 'text').map(f => `[${f.name}]\n${f.content}`).join('\n\n') || undefined,
            inline_images: inlineAttachments.filter(f => f.contentType === 'image').map(f => f.content),
            attachments_meta: inlineAttachments.map(f => ({
              name: f.name,
              contentType: f.contentType as 'text' | 'image',
              preview: f.contentType === 'image' ? f.content : undefined,
              minio_object: f.minioObject,
              minio_bucket: f.minioBucket,
              ...(typeof f.size === 'number' && f.size > 0 ? { size: f.size } : {}),
              ...(typeof f.tokenEstimate === 'number' && f.tokenEstimate > 0 ? { tokenEstimate: f.tokenEstimate } : {}),
            })),
          }
        : undefined;
      setTimeout(() => {
        clearFollowUpSuggestions();
        sendMessage(messageText, newChatId, true, undefined, undefined, inlinePayloadNew);
        setInlineAttachments([]);
        inputRef.current?.focus();
      }, 50);
      return;
    }

    const inlinePayload = inlineAttachments.length
      ? {
          inline_context: inlineAttachments.filter(f => f.contentType === 'text').map(f => `[${f.name}]\n${f.content}`).join('\n\n') || undefined,
          inline_images: inlineAttachments.filter(f => f.contentType === 'image').map(f => f.content),
          attachments_meta: inlineAttachments.map(f => ({
            name: f.name,
            contentType: f.contentType as 'text' | 'image',
            preview: f.contentType === 'image' ? f.content : undefined,
            minio_object: f.minioObject,
            minio_bucket: f.minioBucket,
            ...(typeof f.size === 'number' && f.size > 0 ? { size: f.size } : {}),
            ...(typeof f.tokenEstimate === 'number' && f.tokenEstimate > 0 ? { tokenEstimate: f.tokenEstimate } : {}),
          })),
        }
      : undefined;
    clearFollowUpSuggestions();
    sendMessage(inputMessage.trim(), currentChat.id, true, undefined, undefined, inlinePayload);
    setInlineAttachments([]);
    setInputMessage('');
    
    // Возвращаем фокус на поле ввода
    setTimeout(() => {
      inputRef.current?.focus();
    }, 10);
  };

  const handleKeyPress = (event: React.KeyboardEvent): void => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      handleSendMessage();
    }
  };

  // Вставка из буфера: скриншоты (Ctrl+V) и большой текст как файл
  const handlePaste = async (event: React.ClipboardEvent<HTMLDivElement>): Promise<void> => {
    const clipboardImage = getClipboardImageFile(event.clipboardData);
    if (clipboardImage) {
      event.preventDefault();
      if (isUploading) {
        showNotification('warning', 'Дождитесь окончания загрузки');
        return;
      }
      if (multiLlmInputBlocked || chatAwaitingTokens) {
        showNotification('warning', 'Сейчас нельзя прикрепить файл');
        return;
      }
      await handleMessageAttach(clipboardImage);
      return;
    }

    if (!interfaceSettings.largeTextAsFile) {
      return;
    }

    const pastedText = event.clipboardData.getData('text');
    
    // Определяем, что считается "большим текстом" (например, больше 1000 символов)
    const LARGE_TEXT_THRESHOLD = 1000;
    
    if (pastedText.length > LARGE_TEXT_THRESHOLD) {
      event.preventDefault(); // Предотвращаем стандартную вставку
      
      try {
        // Создаем текстовый файл из вставленного текста
        const blob = new Blob([pastedText], { type: 'text/plain' });
        const fileName = `pasted_text_${Date.now()}.txt`;
        const file = new File([blob], fileName, { type: 'text/plain' });
        
        await handleMessageAttach(file);
        
        // Очищаем поле ввода
        setInputMessage('');
        
        showNotification('success', 'Большой текст вставлен как файл');
      } catch (error) {
        
        showNotification('error', 'Ошибка при создании файла из вставленного текста');
        // В случае ошибки разрешаем стандартную вставку
      }
    }
  };

  const handleCopyMessage = async (content: string): Promise<void> => {
    try {
      const { markdownToRichHtml, markdownToPlainText } = await import('../utils/exportMessageContent');
      const plain = markdownToPlainText(content);
      const body = markdownToRichHtml(content);
      const html = `<!DOCTYPE html><html><body><!--StartFragment-->${body}<!--EndFragment--></body></html>`;
      if (typeof ClipboardItem !== 'undefined' && navigator.clipboard?.write) {
        await navigator.clipboard.write([
          new ClipboardItem({
            'text/plain': new Blob([plain], { type: 'text/plain' }),
            'text/html': new Blob([html], { type: 'text/html' }),
          }),
        ]);
      } else {
        await navigator.clipboard.writeText(plain);
      }
      showNotification('success', STATUS_TOAST_MESSAGES.COPY_SUCCESS);
    } catch (error) {
      try {
        await navigator.clipboard.writeText(content);
        showNotification('success', STATUS_TOAST_MESSAGES.COPY_SUCCESS);
      } catch {
        showNotification('error', STATUS_TOAST_MESSAGES.COPY_FAILED);
      }
    }
  };

  // Функция для перегенерации ответа LLM
  const handleEditMultiLlmColumn = useCallback((message: Message, slotIndex: number): void => {
    const slot = message.multiLLMResponses?.[slotIndex];
    if (!slot) return;
    setEditingMessage(message);
    setEditingMultiLlmSlotIndex(slotIndex);
    setEditText(getMultiLlmColumnDisplayText(slot));
    setEditDialogOpen(true);
  }, []);

  const handleRegenerateMultiLlmColumn = useCallback(
    (message: Message, slotIndex: number): void => {
      if (!currentChat || (!isConnected && !isConnecting)) {
        showNotification('error', 'Нет соединения с сервером');
        return;
      }
      const col = message.multiLLMResponses?.[slotIndex];
      if (!col || col.error) return;
      if (col.isStreaming) {
        showNotification('warning', 'Дождитесь окончания генерации этой модели');
        return;
      }
      const messageIndex = messages.findIndex((m) => m.id === message.id);
      let userMessage: Message | null = null;
      for (let i = messageIndex - 1; i >= 0; i--) {
        if (messages[i].role === 'user') {
          userMessage = messages[i];
          break;
        }
      }
      if (!userMessage) {
        showNotification('error', 'Не найдено предыдущее сообщение пользователя');
        return;
      }
      const displayText = getMultiLlmColumnDisplayText(col);
      let alts =
        col.alternativeResponses && col.alternativeResponses.length > 0
          ? [...col.alternativeResponses]
          : [displayText];
      const ci = col.currentResponseIndex ?? 0;
      if (ci < alts.length) alts[ci] = displayText;
      const newIndex = alts.length;
      alts = [...alts, ''];

      const newCols = message.multiLLMResponses!.map((r, i) =>
        i === slotIndex
          ? {
              ...r,
              alternativeResponses: alts,
              currentResponseIndex: newIndex,
              isStreaming: true,
              content: '',
            }
          : { ...r, isStreaming: false },
      );
      updateMessage(currentChat.id, message.id, undefined, true, newCols);
      regenerateMultiLlmSlot(userMessage.content, message.id, currentChat.id, col.model);
    },
    [currentChat, isConnected, messages, regenerateMultiLlmSlot, showNotification, updateMessage],
  );

  const handleRegenerate = (message: Message, customUserMessage?: string): void => {
    if (!currentChat || (!isConnected && !isConnecting)) {
      showNotification('error', 'Нет соединения с сервером');
      return;
    }

    if (message.multiLLMResponses && message.multiLLMResponses.length > 0) {
      showNotification('info', 'Используйте «Перегенерировать» под нужной моделью');
      return;
    }

    // Находим индекс текущего сообщения
    const messageIndex = messages.findIndex(m => m.id === message.id);
    if (messageIndex === -1) {
      showNotification('error', 'Сообщение не найдено');
      return;
    }

    // Ищем предыдущее сообщение пользователя
    let userMessage: Message | null = null;
    for (let i = messageIndex - 1; i >= 0; i--) {
      if (messages[i].role === 'user') {
        userMessage = messages[i];
        break;
      }
    }

    if (!userMessage) {
      showNotification('error', 'Не найдено предыдущее сообщение пользователя');
      return;
    }
    
    // Используем customUserMessage если передан, иначе берем из userMessage
    const userMessageContent = customUserMessage || userMessage.content;

    // Сохраняем текущий ответ в альтернативные ответы
    const currentContent = message.content;
    let existingAlternatives = message.alternativeResponses || [];
    const currentIndex = message.currentResponseIndex ?? 0;
    
    // Если альтернативных ответов еще нет, инициализируем массив с текущим ответом
    if (existingAlternatives.length === 0) {
      existingAlternatives = [currentContent];
    } else {
      // Обновляем текущий вариант в альтернативных ответах, если он изменился
      const updated = [...existingAlternatives];
      if (currentIndex < updated.length) {
        // Обновляем текущий вариант
        updated[currentIndex] = currentContent;
      } else {
        // Если индекс выходит за границы, добавляем текущий ответ
        updated.push(currentContent);
      }
      existingAlternatives = updated;
    }
    
    // Устанавливаем новый индекс для нового ответа (будет последним)
    const newIndex = existingAlternatives.length;
    
    // Добавляем пустое место для нового ответа (будет заполнено при генерации)
    const updatedAlternatives = [...existingAlternatives, ''];
    
    // Обнуляем content: иначе при перегенерации может мелькать старый ответ,
    // а UI для нового варианта берёт alternativeResponses[newIndex] (пусто → thinking).
    updateMessage(
      currentChat.id,
      message.id,
      '',
      true, // isStreaming - начинаем стриминг
      undefined, // multiLLMResponses
      updatedAlternatives,
      newIndex // Новый индекс для нового ответа
    );

    // Вызываем перегенерацию без создания нового сообщения пользователя
    // Передаем updatedAlternatives и newIndex для сохранения в SocketContext ref
    regenerateResponse(userMessageContent, message.id, currentChat.id, updatedAlternatives, newIndex);
  };

  // Функция для открытия диалога редактирования
  const handleEditClick = (message: Message): void => {
    setEditingMessage(message);
    setEditingMultiLlmSlotIndex(null);
    setEditText(message.content);
    setEditDialogOpen(true);
  };

  const handleMessageFeedback = useCallback(
    async (
      message: Message,
      payload: {
        rating: 'like' | 'dislike' | null;
        tags?: string[];
        comment?: string;
        multiLlmSlotIndex?: number;
      },
    ): Promise<void> => {
      if (!currentChat?.id || message.role !== 'assistant') return;

      const authToken = token || localStorage.getItem('auth_token') || localStorage.getItem('token');
      if (!authToken) {
        showNotification('error', 'Нужна авторизация, чтобы отправить отзыв');
        return;
      }

      const body: Record<string, unknown> = {
        rating: payload.rating,
        tags: payload.rating === 'dislike' ? payload.tags || [] : [],
        comment: payload.rating === 'dislike' ? payload.comment || '' : '',
      };
      if (typeof payload.multiLlmSlotIndex === 'number') {
        body.multi_llm_slot_index = payload.multiLlmSlotIndex;
      }

      try {
        const response = await fetch(
          getApiUrl(`${API_ENDPOINTS.MESSAGE_FEEDBACK}/${currentChat.id}/${message.id}/feedback`),
          {
            method: 'PUT',
            headers: {
              Authorization: `Bearer ${authToken}`,
              'Content-Type': 'application/json',
            },
            body: JSON.stringify(body),
          },
        );

        if (!response.ok) {
          let detail = 'Не удалось сохранить отзыв';
          try {
            const errData = await response.json();
            if (errData?.detail) detail = String(errData.detail);
          } catch {
            /* ignore */
          }
          showNotification('error', detail);
          return;
        }

        const resData = await response.json();
        const saved = resData?.feedback as
          | { rating?: string; tags?: string[]; comment?: string; updated_at?: string }
          | null
          | undefined;

        const nextFeedback: MessageFeedback | null =
          saved && (saved.rating === 'like' || saved.rating === 'dislike')
            ? {
                rating: saved.rating,
                tags: Array.isArray(saved.tags) ? saved.tags.map(String) : [],
                comment: typeof saved.comment === 'string' ? saved.comment : undefined,
                updatedAt: typeof saved.updated_at === 'string' ? saved.updated_at : undefined,
              }
            : null;

        if (typeof payload.multiLlmSlotIndex === 'number' && message.multiLLMResponses) {
          const idx = payload.multiLlmSlotIndex;
          const newCols = message.multiLLMResponses.map((slot, i) =>
            i === idx ? { ...slot, feedback: nextFeedback } : slot,
          );
          updateMessage(currentChat.id, message.id, undefined, false, newCols);
        } else {
          updateMessage(
            currentChat.id,
            message.id,
            undefined,
            undefined,
            undefined,
            undefined,
            undefined,
            undefined,
            undefined,
            undefined,
            undefined,
            undefined,
            undefined,
            nextFeedback,
          );
        }

        if (payload.rating === 'like') {
          showNotification('success', 'Спасибо! Отметили как хороший ответ');
        } else if (payload.rating === 'dislike') {
          showNotification('success', 'Спасибо за отзыв — учтём в следующих ответах');
        }
      } catch {
        showNotification('error', 'Не удалось сохранить отзыв');
      }
    },
    [currentChat?.id, token, showNotification, updateMessage],
  );

  // Функция для сохранения отредактированного сообщения
  const handleSaveEdit = async (): Promise<void> => {
    if (!editingMessage || !currentChat || !editText.trim()) {
      return;
    }

    const trimmedContent = editText.trim();

    if (editingMultiLlmSlotIndex !== null && editingMessage.multiLLMResponses) {
      const idx = editingMultiLlmSlotIndex;
      const next = editingMessage.multiLLMResponses.map((r, i) =>
        i === idx
          ? {
              ...r,
              content: trimmedContent,
              alternativeResponses: undefined,
              currentResponseIndex: undefined,
            }
          : r,
      );
      updateMessage(currentChat.id, editingMessage.id, undefined, false, next);
      showNotification('success', 'Ответ модели обновлён');
      setEditDialogOpen(false);
      setEditingMessage(null);
      setEditingMultiLlmSlotIndex(null);
      setEditText('');
      return;
    }
    
    // Обновляем сообщение в локальном состоянии
    updateMessage(currentChat.id, editingMessage.id, trimmedContent);
    
    // Сохраняем в MongoDB через API
    try {
      const response = await fetch(
        `${getApiUrl(API_ENDPOINTS.UPDATE_MESSAGE)}/${currentChat.id}/${editingMessage.id}`,
        {
          method: 'PUT',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ 
            content: trimmedContent,
            old_content: editingMessage.content  // Передаем старое содержимое для поиска
          }),
        }
      );

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({ detail: 'Ошибка сервера' }));
        throw new Error(errorData.detail || 'Ошибка при сохранении сообщения');
      }
      
      showNotification('success', 'Сообщение обновлено и сохранено в базе данных');
    } catch (error) {
      
      showNotification('warning', 'Сообщение обновлено локально, но не сохранено в базе данных');
    }
    
    setEditDialogOpen(false);
    setEditingMessage(null);
    setEditText('');
  };

  // Функция для сохранения и отправки на повторную генерацию (только для сообщений пользователя)
  const handleSaveAndSend = async (): Promise<void> => {
    if (!editingMessage || !currentChat || !editText.trim() || (!isConnected && !isConnecting)) {
      if (!isConnected && !isConnecting) {
        showNotification('error', 'Нет соединения с сервером');
      }
      return;
    }

    const trimmedContent = editText.trim();
    
    // Обновляем сообщение пользователя в локальном состоянии
    updateMessage(currentChat.id, editingMessage.id, trimmedContent);
    
    // Сохраняем в MongoDB через API
    try {
      const response = await fetch(
        `${getApiUrl(API_ENDPOINTS.UPDATE_MESSAGE)}/${currentChat.id}/${editingMessage.id}`,
        {
          method: 'PUT',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ 
            content: trimmedContent,
            old_content: editingMessage.content  // Передаем старое содержимое для поиска
          }),
        }
      );

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({ detail: 'Ошибка сервера' }));
        throw new Error(errorData.detail || 'Ошибка при сохранении сообщения');
      }
    } catch (error) {
      
      showNotification('warning', 'Сообщение обновлено локально, но не сохранено в базе данных');
    }
    
    // Находим следующее сообщение LLM после этого сообщения пользователя
    const messageIndex = messages.findIndex(m => m.id === editingMessage.id);
    if (messageIndex !== -1) {
      // Ищем следующее сообщение LLM
      for (let i = messageIndex + 1; i < messages.length; i++) {
        if (messages[i].role === 'assistant') {
          // Найдено сообщение LLM - перегенерируем его с обновленным текстом пользователя
          handleRegenerate(messages[i], trimmedContent);
          break;
        }
      }
    }
    
    setEditDialogOpen(false);
    setEditingMessage(null);
    setEditText('');
    showNotification('success', 'Сообщение обновлено и отправлено на перегенерацию');
  };

  // Функция для отмены редактирования
  const handleCancelEdit = (): void => {
    setEditDialogOpen(false);
    setEditingMessage(null);
    setEditingMultiLlmSlotIndex(null);
    setEditText('');
  };

  const formatTimestamp = (timestamp: string): string => {
    const d = new Date(timestamp);
    if (Number.isNaN(d.getTime())) return '';
    const date = d.toLocaleDateString('ru-RU', {
      day: '2-digit',
      month: '2-digit',
      year: 'numeric',
    });
    const time = d.toLocaleTimeString('ru-RU', {
      hour: '2-digit',
      minute: '2-digit',
    });
    return `${date} ${time}`;
  };

  // Функция для определения приветствия по времени суток (Московское время)
  const getGreeting = (): string => {
    const now = new Date();
    const moscowTime = new Date(now.toLocaleString("en-US", {timeZone: "Europe/Moscow"}));
    const hour = moscowTime.getHours();
    
    // Определяем имя пользователя для приветствия
    const userName = user?.full_name || user?.username || "";
    const nameToShow = userName ? `, ${userName}` : "";
    
    if (hour >= 5 && hour < 12) {
      return `Доброе утро${nameToShow}`;
    } else if (hour >= 12 && hour < 18) {
      return `Добрый день${nameToShow}`;
    } else if (hour >= 18 && hour < 22) {
      return `Добрый вечер${nameToShow}`;
    } else {
      return `Доброй ночи${nameToShow}`;
    }
  };

  // ================================
  // TTS ДЛЯ КНОПКИ "ПРОЧЕСТЬ ВСЛУХ"
  // ================================

  const synthesizeSpeech = async (text: string) => {
    if (!text.trim()) return;
    if (currentAudioRef.current) {
      currentAudioRef.current.pause(); currentAudioRef.current.src = ''; currentAudioRef.current = null;
    }
    setIsSpeaking(true);
    try {
      const response = await fetch(getApiUrl(API_ENDPOINTS.VOICE_SYNTHESIZE), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          text,
          voice_id: voiceSettingsTTS.voice_id,
          voice_speaker: voiceSettingsTTS.voice_speaker,
          speech_rate: voiceSettingsTTS.speech_rate,
        }),
      });
      if (response.ok) {
        const audioBlob = await response.blob();
        const audioUrl = URL.createObjectURL(audioBlob);
        const audio = new Audio(audioUrl);
        currentAudioRef.current = audio;
        audio.onended = () => {
          setIsSpeaking(false); URL.revokeObjectURL(audioUrl); currentAudioRef.current = null;
        };
        audio.onerror = () => {
          setIsSpeaking(false); showNotification('error', 'Ошибка воспроизведения речи');
          URL.revokeObjectURL(audioUrl); currentAudioRef.current = null;
        };
        await audio.play();
      } else {
        setIsSpeaking(false);
        showNotification('error', 'Ошибка синтеза речи');
      }
    } catch {
      setIsSpeaking(false);
      showNotification('error', 'Ошибка синтеза речи');
    }
  };

  // ================================
  // ФУНКЦИИ РАБОТЫ С ДОКУМЕНТАМИ
  // ================================

  /** Прикрепить файл: MinIO + содержимое для модели (без RAG/эмбеддингов) */
  const handleMessageAttach = async (file: File): Promise<void> => {
    logChatAttach('attach-start', {
      name: file.name,
      type: file.type,
      size: file.size,
      sizeHuman: formatFileSize(file.size),
      lastModified: file.lastModified,
    });

    if (isInlineAttachFileTooLarge(file)) {
      logChatAttach('attach-rejected-size', {
        name: file.name,
        size: file.size,
        sizeHuman: formatFileSize(file.size),
      });
      setAttachErrorBanner(buildOversizedInlineAttachMessage(file.name, file.size));
      if (fileInputRef.current) fileInputRef.current.value = '';
      return;
    }

    setUploadingFile({ name: file.name, size: file.size });
    setIsUploading(true);

    const isImage = /\.(jpe?g|png|webp|gif)$/i.test(file.name)
      || ['image/jpeg', 'image/jpg', 'image/png', 'image/webp', 'image/gif'].includes(file.type);

    logChatAttach('attach-validated', { name: file.name, isImage });

    let previewUrl: string | undefined;
    if (isImage) {
      previewUrl = URL.createObjectURL(file);
      setUploadingFile({ name: file.name, size: file.size, previewUrl });
    }
    let compressedNotice: string | null = null;
    const uploadUrl = getApiUrl(API_ENDPOINTS.DOCUMENTS_ATTACH);
    try {
      let fileToUpload = file;
      if (isImage) {
        const prepared = await prepareInlineImageFile(file);
        fileToUpload = dataUrlToFile(prepared.dataUrl, prepared.filename);
        logChatAttach('attach-upload-file-ready', {
          originalName: file.name,
          uploadName: fileToUpload.name,
          uploadType: fileToUpload.type,
          uploadSize: fileToUpload.size,
          uploadSizeHuman: formatFileSize(fileToUpload.size),
          wasCompressed: prepared.wasCompressed,
        });
        if (prepared.wasCompressed) {
          compressedNotice = ` (сжато: ${formatFileSize(prepared.originalSize)} → ${formatFileSize(prepared.compressedSize)})`;
        }
      } else {
        logChatAttach('attach-upload-file-ready', {
          originalName: file.name,
          uploadName: fileToUpload.name,
          uploadType: fileToUpload.type,
          uploadSize: fileToUpload.size,
          uploadSizeHuman: formatFileSize(fileToUpload.size),
          wasCompressed: false,
        });
      }

      const formData = new FormData();
      formData.append('file', fileToUpload);
      const uploadStartedAt = performance.now();
      logChatAttach('attach-fetch-start', { url: uploadUrl, uploadSize: fileToUpload.size });

      const response = await fetch(uploadUrl, {
        method: 'POST',
        headers: getAuthFetchHeaders(),
        body: formData,
      });

      const fetchMs = Math.round(performance.now() - uploadStartedAt);
      const responseContentType = response.headers.get('content-type') || '';
      const responseContentLength = response.headers.get('content-length');

      logChatAttach('attach-fetch-response', {
        status: response.status,
        ok: response.ok,
        statusText: response.statusText,
        fetchMs,
        responseContentType,
        responseContentLength,
      });

      if (response.ok) {
        const parseStartedAt = performance.now();
        let result: {
          type: 'text' | 'image';
          content: string;
          filename: string;
          minio_object?: string;
          minio_bucket?: string;
          warning?: string;
        };
        try {
          result = await response.json();
        } catch (parseError) {
          logChatAttachError('attach-json-parse-failed', parseError, {
            status: response.status,
            responseContentType,
            fetchMs,
          });
          showNotification('error', 'Сервер вернул некорректный ответ при прикреплении файла');
          return;
        }

        logChatAttach('attach-success', {
          filename: result.filename,
          type: result.type,
          contentChars: result.content?.length ?? 0,
          minioObject: result.minio_object ?? null,
          minioBucket: result.minio_bucket ?? null,
          warning: result.warning ?? null,
          parseMs: Math.round(performance.now() - parseStartedAt),
        });

        setInlineAttachments(prev => [
          ...prev,
          {
            name: result.filename || file.name,
            contentType: result.type,
            content: result.content,
            size: file.size,
            tokenEstimate:
              result.type === 'image'
                ? 512
                : estimateTokens(result.content || ''),
            ...(result.minio_object ? { minioObject: result.minio_object } : {}),
            ...(result.minio_bucket ? { minioBucket: result.minio_bucket } : {}),
          },
        ]);
        if (result.warning) {
          showNotification('warning', result.warning);
        }
        showNotification('success', `"${file.name}" прикреплён${compressedNotice || ''}`);
        setShowDocumentDialog(false);
      } else {
        let errBody: unknown = null;
        let detail = 'Ошибка при прикреплении файла';
        const responseText = await response.text().catch(() => '');
        if (responseText) {
          try {
            errBody = JSON.parse(responseText);
            detail = (errBody as { detail?: string }).detail || detail;
          } catch {
            errBody = { rawTextPreview: responseText.slice(0, 500) };
            detail = responseText.slice(0, 200) || detail;
          }
        }

        logChatAttach('attach-http-error', {
          status: response.status,
          detail,
          errBody,
          fetchMs,
          responseTextLength: responseText.length,
        });

        if (response.status === 413) {
          setAttachErrorBanner(buildOversizedInlineAttachMessage(file.name, file.size));
        } else if (response.status === 400 && isInlineAttachSizeErrorMessage(detail)) {
          setAttachErrorBanner(buildOversizedInlineAttachMessage(file.name, file.size));
        } else if (response.status === 400 && /не поддержива|unsupported|формат/i.test(detail)) {
          setAttachErrorBanner(buildUnsupportedInlineAttachMessage(file.name));
        } else {
          showNotification('error', detail);
        }
      }
    } catch (error) {
      logChatAttachError('attach-unhandled-error', error, {
        name: file.name,
        size: file.size,
        url: uploadUrl,
      });
      showNotification('error', 'Не удалось прикрепить файл');
    } finally {
      logChatAttach('attach-finished', { name: file.name });
      if (previewUrl) {
        URL.revokeObjectURL(previewUrl);
      }
      setIsUploading(false);
      setUploadingFile(null);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const handleDragOver = (e: React.DragEvent): void => {
    if (!dataTransferHasFiles(e.dataTransfer)) {
      return;
    }
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
    setIsDragging(true);
  };

  const handleDragLeave = (e: React.DragEvent): void => {
    if (!dataTransferHasFiles(e.dataTransfer)) {
      return;
    }
    e.preventDefault();
    setIsDragging(false);
  };

  const handleDrop = (e: React.DragEvent): void => {
    setIsDragging(false);
    const files = Array.from(e.dataTransfer.files);
    if (files.length === 0) {
      return;
    }
    e.preventDefault();
    handleMessageAttach(files[0]);
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>): void => {
    const files = e.target.files;
    if (files && files.length > 0) {
      handleMessageAttach(files[0]);
    }
  };

  const handleMenuOpen = (event: React.MouseEvent<HTMLElement>): void => {
    setGearToolsPanel('main');
    setAnchorEl(chatInputToolsAnchorRef.current ?? event.currentTarget);
    const shell = chatInputToolsAnchorRef.current;
    if (shell) {
      const rect = shell.getBoundingClientRect();
      setGearToolsMenuWidthPx(Math.round(rect.width));
      setGearToolsPaperHeightPx(getChatGearMenuPaperHeightPx(rect.top));
    } else {
      setGearToolsMenuWidthPx(null);
      setGearToolsPaperHeightPx(null);
    }
  };

  const handleMenuClose = (): void => {
    setGearToolsPanel('main');
    setAnchorEl(null);
    setGearToolsMenuWidthPx(null);
    setGearToolsPaperHeightPx(null);
  };

  const handleStopGeneration = (): void => {
    stopGeneration();
    showNotification('info', 'Генерация остановлена');
  };

  // ================================
  // ФУНКЦИИ НАВИГАЦИИ ПО СООБЩЕНИЯМ
  // ================================

  const scrollToMessage = useCallback((originalIndex: number) => {
    const target = messages[originalIndex];
    const visIndex = target
      ? visibleMessages.findIndex((m) => m.id === target.id)
      : originalIndex;
    if (visIndex < 0) return;

    isProgrammaticScrollRef.current = true;
    isAtBottomRef.current = false;

    const snapIntoView = () => {
      const el =
        messageRefs.current[originalIndex] ||
        (document.querySelector(`[data-message-index="${originalIndex}"]`) as HTMLElement | null);
      el?.scrollIntoView({ behavior: 'auto', block: 'start' });
    };

    // smooth + align:center на высоких карточках (презентации) часто no-op:
    // Virtuoso ещё не измерил item (defaultItemHeight=160).
    if (shouldUseVirtuosoList && virtuosoRef.current) {
      virtuosoRef.current.scrollToIndex({
        index: visIndex,
        align: 'start',
        behavior: 'auto',
      });
      requestAnimationFrame(snapIntoView);
      window.setTimeout(snapIntoView, 60);
      window.setTimeout(snapIntoView, 220);
    } else {
      snapIntoView();
    }
    window.setTimeout(() => { isProgrammaticScrollRef.current = false; }, 800);
  }, [messages, visibleMessages, shouldUseVirtuosoList]);

  // ================================
  // ФУНКЦИИ ДЛЯ РЕЖИМА "ПОДЕЛИТЬСЯ"
  // ================================

  const handleEnterShareMode = () => {
    setShareMode(true);
    setSelectedMessages(new Set());
  };

  const handleBranchToNewChat = useCallback(
    async (message: Message, multiLlmSlotIndex?: number): Promise<void> => {
      if (!currentChat?.id) return;
      if (message.role !== 'assistant') return;
      if (message.isStreaming) {
        showNotification('info', 'Дождитесь окончания генерации ответа');
        return;
      }
      if (
        multiLlmSlotIndex !== undefined &&
        message.multiLLMResponses?.[multiLlmSlotIndex]?.isStreaming
      ) {
        showNotification('info', 'Дождитесь окончания генерации ответа');
        return;
      }

      const newChatId = await branchChatAtMessage(currentChat.id, message.id, { multiLlmSlotIndex });
      if (newChatId) {
        showNotification('success', 'Создана ветка в новом чате');
        navigate('/');
      } else {
        showNotification('error', 'Не удалось создать ветку');
      }
    },
    [branchChatAtMessage, currentChat?.id, navigate, showNotification],
  );

  const handleExitShareMode = () => {
    setShareMode(false);
    setSelectedMessages(new Set());
  };

  const handleToggleMessage = (userMsgId: string, assistantMsgId: string) => {
    const newSelected = new Set(selectedMessages);
    
    if (newSelected.has(userMsgId) && newSelected.has(assistantMsgId)) {
      // Если оба выбраны, снимаем выбор
      newSelected.delete(userMsgId);
      newSelected.delete(assistantMsgId);
    } else {
      // Выбираем оба
      newSelected.add(userMsgId);
      newSelected.add(assistantMsgId);
    }
    
    setSelectedMessages(newSelected);
  };

  const handleSelectAll = () => {
    // Получаем все пары вопрос-ответ
    const allPairs: string[] = [];
    for (let i = 0; i < messages.length - 1; i++) {
      if (messages[i].role === 'user' && messages[i + 1].role === 'assistant') {
        allPairs.push(messages[i].id, messages[i + 1].id);
      }
    }
    
    if (selectedMessages.size === allPairs.length) {
      // Если все выбраны, снимаем выбор
      setSelectedMessages(new Set());
    } else {
      // Выбираем все
      setSelectedMessages(new Set(allPairs));
    }
  };

  const handleCreateShareLink = () => {
    if (selectedMessages.size === 0) {
      showNotification('error', 'Выберите хотя бы одно сообщение');
      return;
    }
    // Открываем диалог подтверждения
    setShareDialogOpen(true);
  };

  const createShareLinkConfirmed = async (): Promise<string> => {
    try {
      // Фильтруем выбранные сообщения в правильном порядке
      const selectedMessagesArray = messages.filter(msg => selectedMessages.has(msg.id));

      // Получаем токен для авторизации
      const token = localStorage.getItem('auth_token') || localStorage.getItem('token');
      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
      };
      if (token) {
        headers['Authorization'] = `Bearer ${token}`;
      }

      const response = await fetch(getApiUrl('/api/share/create'), {
        method: 'POST',
        headers,
        credentials: 'include',
        body: JSON.stringify({
          messages: selectedMessagesArray,
        }),
      });

      if (!response.ok) {
        throw new Error('Ошибка создания публичной ссылки');
      }

      const data = await response.json();
      const fullUrl = `${window.location.origin}/share/${data.share_id}`;
      
      return fullUrl;
    } catch (err) {
      showNotification('error', err instanceof Error ? err.message : 'Произошла ошибка');
      throw err;
    }
  };

  const handleCloseShareDialog = () => {
    setShareDialogOpen(false);
    // Выходим из режима выбора после закрытия диалога
    handleExitShareMode();
  };

  // ================================
  // (MessageCard определён на уровне модуля, выше UnifiedChatPage)
  // ================================

  // NOTE: MessageCard теперь определён на уровне модуля (вне UnifiedChatPage).
  // Это предотвращает пересоздание типа компонента при каждом рендере родителя
  // (что вызывало полный unmount/remount Monaco Editor при каждом нажатии клавиши).


  // ================================
  // ДИАЛОГИ
  // ================================

  const DocumentDialog = (): React.ReactElement => (
    <Dialog
      open={showDocumentDialog}
      onClose={() => setShowDocumentDialog(false)}
      maxWidth="md"
      fullWidth
      TransitionComponent={undefined}
      transitionDuration={0}
    >
      <DialogTitle>Загрузка документов</DialogTitle>
      <DialogContent>
        <Box
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          sx={{
            border: '2px dashed',
            borderColor: isDragging ? 'primary.main' : 'divider',
            borderRadius: 2,
            p: 4,
            textAlign: 'center',
            bgcolor: isDragging ? 'action.hover' : 'background.paper',
            cursor: 'pointer',
          }}
          onClick={() => fileInputRef.current?.click()}
        >
          <UploadIcon sx={{ fontSize: 48, color: 'text.secondary', mb: 2 }} />
          <Typography variant="h6" sx={{ mb: 1 }}>
            Перетащите файл сюда или нажмите для выбора
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Прикрепление к сообщению: файл сохраняется в MinIO и передаётся в модель (без RAG). PDF, Word, Excel, TXT, изображения до 50MB
          </Typography>
          <input
            ref={fileInputRef}
            type="file"
            accept={INLINE_ATTACH_ACCEPT}
            onChange={handleFileSelect}
            style={{ display: 'none' }}
          />
        </Box>
        

      </DialogContent>
      <DialogActions>
        <Button onClick={() => setShowDocumentDialog(false)}>
          Закрыть
        </Button>
      </DialogActions>
    </Dialog>
  );

  // ================================
  // ОСНОВНОЙ РЕНДЕР
  // ================================

  // Обновляем dataRef перед каждым рендером, чтобы MessageCard всегда видел актуальные callback-и
  // (MessageCard мемоизирован и не ре-рендерится при изменении inputMessage,
  //  но его onClick-обработчики через dataRef.current всегда получают свежие функции)
  messageCardDataRef.current = {
    handleSendMessageFromRenderer,
    handleFollowUpSelect,
    handleCopyMessage,
    handleEditClick,
    handleRegenerate,
    handleEditMultiLlmColumn,
    handleRegenerateMultiLlmColumn,
    handleMessageFeedback,
    synthesizeSpeech,
    handleEnterShareMode,
    handleBranchToNewChat,
    handleToggleMessage,
    updateMessage,
    formatTimestamp,
    currentChatId: currentChat?.id,
    messageRefs,
  };

  virtuosoMessagesEndRefHolder.current = messagesEndRef;

  const virtuosoContext = useMemo<VirtuosoListContext>(
    () => ({ bottomSpacerPx: grokBottomSpacerPx }),
    [grokBottomSpacerPx],
  );

  const renderMessageAtIndex = (
    index: number,
    message: (typeof visibleMessages)[number],
    list: typeof visibleMessages = visibleMessages,
  ) => {
    const isUserMsg = message.role === 'user';
    const isPairStart =
      isUserMsg &&
      index < list.length - 1 &&
      list[index + 1].role === 'assistant';
    const isSelected =
      isPairStart &&
      selectedMessages.has(message.id) &&
      selectedMessages.has(list[index + 1].id);
    const nextMessageId = isPairStart ? list[index + 1].id : null;
    const navIndex = messages.findIndex((m) => m.id === message.id);
    return (
      <MessageCard
        key={message.id || String(index)}
        message={message}
        index={index}
        navIndex={navIndex >= 0 ? navIndex : index}
        isPairStart={isPairStart}
        isSelected={isSelected}
        nextMessageId={nextMessageId}
        shareMode={shareMode}
        isSpeaking={isSpeaking}
        isDarkMode={isDarkMode}
        isLastChatMessage={index === list.length - 1}
        interfaceSettings={interfaceSettings}
        username={user?.username}
        dataRef={messageCardDataRef}
      />
    );
  };


  const renderMultiLlmModelToolbar = (): React.ReactNode => {
    if (!isMultiLlmMode) return null;
    const barMaxWidth = interfaceSettings.widescreenMode ? '100%' : messages.length === 0 ? '800px' : '1000px';
    const barPx =
      interfaceSettings.chatInputStyle === 'classic' ? 0 : interfaceSettings.widescreenMode ? 4 : 2;

    const displayModelLabel = (val: string): string => {
      if (!val) return '';
      const row = availableModels.find((x) => availableModelSelectValue(x) === val);
      if (row) return row.display_name || row.name;
      // Fallback: отрезаем префикс провайдера / legacy llm-svc://host/.
      const cleaned = val.startsWith('llm-svc://') ? val.slice('llm-svc://'.length) : val;
      return cleaned.split('/').pop() ?? val;
    };

    const menuWindowId = multiLlmModelMenu?.windowId ?? null;
    const menuAnchor = multiLlmModelMenu?.anchorEl ?? null;
    const menuOpen = Boolean(menuAnchor);
    const menuWin = menuWindowId ? modelWindows.find((w) => w.id === menuWindowId) : undefined;
    const selectedRowBg = isDarkMode ? DROPDOWN_ITEM_HOVER_BG_DARK : DROPDOWN_ITEM_HOVER_BG_LIGHT;
    const menuItemMuted = isDarkMode ? 'rgba(255,255,255,0.9)' : 'rgba(0,0,0,0.87)';

    return (
      <Box
        sx={{
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          gap: 1,
          width: '100%',
          maxWidth: barMaxWidth,
          mx: 'auto',
          px: barPx,
          mb: 1,
          position: 'relative',
          zIndex: workZoneAnimated ? 2 : undefined,
        }}
      >
        {modelWindows.map((window) => (
          <Box
            key={window.id}
            sx={{
              display: 'flex',
              alignItems: 'center',
              gap: 0.5,
              flex: '1 1 160px',
              minWidth: { xs: '100%', sm: 160 },
              maxWidth: { sm: 280 },
            }}
          >
            <FormControl
              variant="outlined"
              fullWidth
              size="small"
              required
              sx={multiLlmModelFieldSx}
              disabled={availableModels.length === 0 || hasActiveChatStreaming}
            >
              <InputLabel htmlFor={`multi-llm-model-${window.id}`}>Модель</InputLabel>
              <OutlinedInput
                id={`multi-llm-model-${window.id}`}
                label="Модель"
                value={displayModelLabel(window.selectedModel)}
                readOnly
                placeholder="Выберите модель"
                onClick={(e) => {
                  if (availableModels.length === 0 || hasActiveChatStreaming) return;
                  const root = (e.currentTarget as HTMLElement).closest('.MuiOutlinedInput-root') as HTMLElement | null;
                  if (!root) return;
                  setMultiLlmModelMenu((prev) =>
                    prev?.windowId === window.id && prev.anchorEl === root ? null : { windowId: window.id, anchorEl: root },
                  );
                }}
                endAdornment={
                  <InputAdornment position="end">
                    <ExpandMoreIcon
                      sx={{
                        ...DROPDOWN_CHEVRON_SX,
                        transform:
                          multiLlmModelMenu?.windowId === window.id && Boolean(multiLlmModelMenu?.anchorEl)
                            ? 'rotate(180deg)'
                            : 'none',
                      }}
                    />
                  </InputAdornment>
                }
              />
            </FormControl>
            <Tooltip title={modelWindows.length > 1 ? 'Убрать колонку' : 'Выйти из режима сравнения'}>
              <span>
                <IconButton
                  size="small"
                  onClick={() => removeModelWindow(window.id)}
                  color="error"
                  sx={{ flexShrink: 0 }}
                  disabled={hasActiveChatStreaming || currentChatLoading}
                >
                  <CloseIcon fontSize="small" />
                </IconButton>
              </span>
            </Tooltip>
          </Box>
        ))}
        <Popover
          open={menuOpen}
          anchorEl={menuAnchor}
          onClose={() => setMultiLlmModelMenu(null)}
          anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
          transformOrigin={{ vertical: 'top', horizontal: 'left' }}
          slotProps={{
            paper: {
              sx: {
                ...dropdownPanelSx,
                mt: DROPDOWN_PAPER_MARGIN_TOP,
                minWidth: DROPDOWN_PAPER_MIN_WIDTH_PX,
                width: menuAnchor
                  ? `${menuAnchor.getBoundingClientRect().width}px`
                  : DROPDOWN_PAPER_DEFAULT_WIDTH_PX,
              },
            },
          }}
        >
          <Box sx={{ py: 0.5, maxHeight: 320, overflow: 'auto', ...SIDEBAR_HIDE_SCROLLBAR_SX }}>
            {availableModels.length === 0 ? (
              <Typography
                sx={{
                  px: 1.5,
                  py: 1,
                  fontSize: MENU_ACTION_TEXT_SIZE,
                  color: isDarkMode ? 'rgba(255,255,255,0.45)' : 'text.secondary',
                }}
              >
                Загрузка моделей...
              </Typography>
            ) : (
              <>
                <Box
                  onClick={() => {
                    if (menuWindowId) {
                      handleModelSelect(menuWindowId, '');
                      setMultiLlmModelMenu(null);
                    }
                  }}
                  sx={{
                    ...dropdownItemSx,
                    color: !menuWin?.selectedModel ? (isDarkMode ? '#fff' : 'rgba(0,0,0,0.95)') : menuItemMuted,
                    fontWeight: !menuWin?.selectedModel ? 600 : 400,
                    bgcolor: !menuWin?.selectedModel ? selectedRowBg : 'transparent',
                  }}
                >
                  Не выбрано
                </Box>
                {availableModels
                  .filter((m) => {
                    const k = availableModelSelectValue(m);
                    const isSelectedElsewhere = modelWindows.some(
                      (w) => w.id !== menuWindowId && w.selectedModel === k,
                    );
                    return !isSelectedElsewhere || menuWin?.selectedModel === k;
                  })
                  .map((model) => {
                    const k = availableModelSelectValue(model);
                    const sel = menuWin?.selectedModel === k;
                    const hint = model.provider_id && !model.provider_default
                      ? `${model.provider_id}${model.provider_kind ? ` · ${model.provider_kind}` : ''}`
                      : '';
                    return (
                      <Box
                        key={k}
                        onClick={() => {
                          if (menuWindowId) {
                            handleModelSelect(menuWindowId, k);
                            setMultiLlmModelMenu(null);
                          }
                        }}
                        sx={{
                          ...dropdownItemSx,
                          display: 'flex',
                          flexDirection: 'column',
                          alignItems: 'flex-start',
                          color: sel ? (isDarkMode ? '#fff' : 'rgba(0,0,0,0.95)') : menuItemMuted,
                          fontWeight: sel ? 600 : 400,
                          bgcolor: sel ? selectedRowBg : 'transparent',
                        }}
                      >
                        <Box sx={{ fontSize: MENU_ACTION_TEXT_SIZE, width: '100%' }}>
                          {model.display_name || model.name}
                        </Box>
                        {hint ? (
                          <Box
                            sx={{
                              fontSize: '0.72rem',
                              color: isDarkMode ? 'rgba(255,255,255,0.45)' : 'rgba(0,0,0,0.5)',
                            }}
                          >
                            {hint}
                          </Box>
                        ) : null}
                      </Box>
                    );
                  })}
              </>
            )}
          </Box>
        </Popover>
        {modelWindows.length < 4 ? (
          <Tooltip title="Добавить модель (максимум 4)">
            <span>
              <IconButton
                onClick={addModelWindow}
                sx={{
                  flexShrink: 0,
                  width: 40,
                  height: 40,
                  borderRadius: '10px',
                  color: 'primary.main',
                  bgcolor: isDarkMode ? 'rgba(0,0,0,0.25)' : 'rgba(255,255,255,0.9)',
                  border: isDarkMode ? '1px solid rgba(255,255,255,0.15)' : '1px solid rgba(0,0,0,0.12)',
                  '&:hover': {
                    bgcolor: isDarkMode ? 'rgba(0,0,0,0.35)' : 'rgba(255,255,255,1)',
                  },
                }}
                disableRipple
                disabled={hasActiveChatStreaming}
              >
                <AddIcon sx={{ fontSize: '1.2rem' }} />
              </IconButton>
            </span>
          </Tooltip>
        ) : null}
      </Box>
    );
  };

  return (
    <Box sx={{ display: 'flex', height: '100%', minHeight: 0, minWidth: 0, overflow: 'hidden' }}>
      {modelErrorBanner ? (
        <TopErrorBanner
          message={modelErrorBanner}
          onClose={() => setModelErrorBanner(null)}
          ariaLabel="Закрыть уведомление о выборе модели"
        />
      ) : attachErrorBanner ? (
        <TopErrorBanner
          message={attachErrorBanner}
          onClose={() => setAttachErrorBanner(null)}
          ariaLabel="Закрыть уведомление о неподдерживаемом файле"
        />
      ) : null}
      {/* Основной контент */}
      <Box 
        className="fullscreen-chat" 
        sx={{ 
          flexGrow: 1,
          minHeight: 0,
          minWidth: 0,
          maxWidth: '100%',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
          pt: 8,
          backgroundColor: workZoneBgColor,
          ...(workZoneMode === 'custom' && workZoneCustomImage
            ? {
                backgroundImage: `url("${workZoneCustomImage}")`,
                backgroundSize: 'cover',
                backgroundPosition: 'center',
                backgroundRepeat: 'no-repeat',
              }
            : {}),
          color: isDarkMode ? 'white' : '#333',
          position: 'relative',
        }}
      >
      {workZoneMode === 'starry' ? <WorkZoneStarrySky isDarkMode={isDarkMode} /> : null}
      {workZoneMode === 'snowfall' ? <WorkZoneSnowfall isDarkMode={isDarkMode} /> : null}
      {/* Заголовок с информацией о проекте и модели */}
      {currentChat && project && (
        <Box sx={{ 
          position: 'absolute',
          top: 16,
          left: sidebarHidden ? 16 : sidebarOpen ? 16 : 80,
          zIndex: 1200,
          transition: 'left 0.3s ease',
          display: 'flex',
          alignItems: 'center',
          gap: 1,
        }}>
          <Typography
            variant="body1"
            sx={{
              fontWeight: 500,
              color: isDarkMode ? 'white' : '#333',
              cursor: 'pointer',
              '&:hover': {
                opacity: 0.8,
              },
              fontSize: '0.95rem',
            }}
            onClick={() => navigate(`/project/${project.id}`)}
          >
            {project.name}
          </Typography>
          <Typography
            variant="body1"
            sx={{
              fontWeight: 400,
              color: isDarkMode ? 'rgba(255,255,255,0.5)' : 'rgba(0,0,0,0.5)',
              fontSize: '0.95rem',
            }}
          >
            /
          </Typography>
          {modelSelectorMode === 'workspace' && (
            <ModelSelector 
              isDarkMode={isDarkMode}
              onModelSelect={(modelPath) => {
                
              }}
            />
          )}
          {modelSelectorMode === 'workspace_agent' && (
            <AgentSelector
              isDarkMode={isDarkMode}
              triggerMaxWidth={180}
              onModelSelect={() => {}}
            />
          )}
          {(modelSelectorMode === 'workspace' || modelSelectorMode === 'workspace_agent') && (
            <Tooltip title="Сравнение моделей">
              <span>
                <IconButton
                  onClick={() => {
                    void handleToggleMultiLlmMode();
                  }}
                  disabled={
                    currentChatLoading ||
                    hasActiveChatStreaming
                  }
                  sx={multiLlmModeToggleIconButtonSx}
                >
                  <MultiLlmModeToggleIcon isDarkMode={isDarkMode} />
                </IconButton>
              </span>
            </Tooltip>
          )}
        </Box>
      )}
      
      {/* Селектор моделей - на одном уровне с кнопкой сворачивания боковой панели */}
      {/* Когда панель развернута - ближе к панели, когда закрыта - дальше от узкой полоски */}
      {(!currentChat || !project) && (
        <Box sx={{ 
          position: 'absolute',
          top: 16,
          left: sidebarHidden ? 16 : sidebarOpen ? 16 : 80,
          zIndex: 1200,
          transition: 'left 0.3s ease',
          display: 'flex',
          alignItems: 'center',
          gap: 0.75,
        }}>
          {modelSelectorMode === 'workspace' && (
            <ModelSelector 
              isDarkMode={isDarkMode}
              onModelSelect={(modelPath) => {
                
              }}
            />
          )}
          {modelSelectorMode === 'workspace_agent' && (
            <AgentSelector
              isDarkMode={isDarkMode}
              triggerMaxWidth={180}
              onModelSelect={() => {}}
            />
          )}
          {(modelSelectorMode === 'workspace' || modelSelectorMode === 'workspace_agent') && (
            <Tooltip title="Сравнение моделей">
              <span>
                <IconButton
                  onClick={() => {
                    void handleToggleMultiLlmMode();
                  }}
                  disabled={
                    currentChatLoading ||
                    hasActiveChatStreaming
                  }
                  sx={multiLlmModeToggleIconButtonSx}
                >
                  <MultiLlmModeToggleIcon isDarkMode={isDarkMode} />
                </IconButton>
              </span>
            </Tooltip>
          )}
        </Box>
      )}


      {/* Область сообщений */}
      <Box
        className="chat-messages-area"
                 sx={{
           border: isDragging ? '2px dashed' : 'none',
           borderColor: isDragging ? 'primary.main' : 'transparent',
           bgcolor: isDragging ? 'action.hover' : 'transparent',
           position: 'relative',
           zIndex: workZoneAnimated ? 1 : undefined,
           ...(showNewChatWelcome
             ? {
                 flex: '0 0 auto',
                 minHeight: 0,
                 height: 0,
                 overflow: 'hidden',
                 p: 0,
               }
             : {
                 flex: 1,
                 minHeight: 0,
                 overflow: 'hidden',
                 overflowX: 'hidden',
                 overscrollBehavior: 'none',
                 justifyContent: 'flex-start',
                 pt: 2,
                 pl: 2,
                 pr: 'calc(16px + var(--right-sidebar-rail-overlap, 0px))',
                 pb: 0,
               }),
           display: 'flex',
           flexDirection: 'column',
           alignItems: 'stretch',
           // Селектор моделей в правом верхнем углу
           '&::before': {
             content: '""',
             position: 'absolute',
             top: 16,
             right: 16,
             zIndex: 10,
           },
          ...SIDEBAR_HIDE_SCROLLBAR_SX,
         }}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        onWheel={(e) => {
          // Wheel над пустыми полями рабочей зоны (не над scroller Virtuoso) — крутим ленту.
          const scroller = virtuosoScrollerElRef.current;
          if (!scroller) return;
          if (scroller.contains(e.target as Node)) return;
          const t = e.target as HTMLElement | null;
          if (t?.closest?.('textarea, input, [contenteditable="true"], .MuiPopover-root, .MuiModal-root')) {
            return;
          }
          scroller.scrollTop += e.deltaY;
        }}
      >
          {showChatHistoryLoadingDelayed ? (
            <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 1.5, py: 6, width: '100%' }}>
              <CircularProgress size={28} />
              <Typography variant="body2" color="text.secondary">
                Загрузка переписки…
              </Typography>
            </Box>
          ) : messages.length === 0 ? (
            null
          ) : (
            <Box
              sx={{
                width: '100%',
                flex: 1,
                minHeight: 0,
                minWidth: 0,
                maxWidth: '100%',
                alignSelf: 'stretch',
                display: 'flex',
                flexDirection: 'column',
                overflow: 'hidden',
              }}
            >
              {/* Virtuoso на всю ширину рабочей зоны — wheel работает везде, не только над баблами.
                  Контент сообщений центрируется внутри item (maxWidth 1000px). */}
              {showVirtuosoList ? (
                <Box sx={{ flex: 1, minHeight: 0, minWidth: 0, maxWidth: '100%', width: '100%', display: 'flex', flexDirection: 'column', pt: 4, pb: 0, overflow: 'hidden' }}>
                  <Virtuoso
                    key={currentChat?.id || 'chat'}
                    ref={virtuosoRef}
                    style={{ height: '100%', width: '100%' }}
                    scrollerRef={setVirtuosoScrollerRef}
                    data={virtuosoData}
                    defaultItemHeight={160}
                    increaseViewportBy={virtuosoViewportPadding}
                    initialTopMostItemIndex={virtuosoInitialLocation}
                    followOutput={virtuosoFollowOutput}
                    computeItemKey={(index, message) => message.id || String(index)}
                    components={VIRTUOSO_LIST_COMPONENTS}
                    context={virtuosoContext}
                    itemContent={(index, message) => (
                      <Box
                        sx={{
                          width: '100%',
                          maxWidth: interfaceSettings.widescreenMode ? '100%' : '1000px',
                          minWidth: 0,
                          mx: 'auto',
                          px: interfaceSettings.widescreenMode ? 4 : 2,
                          boxSizing: 'border-box',
                          overflowX: 'hidden',
                        }}
                      >
                        {renderMessageAtIndex(index, message, virtuosoData)}
                        {/* «Думает» — сразу после последнего сообщения (место ответа), обычный item списка:
                            при скролле вверх уезжает из вьюпорта вместе с лентой. */}
                        {chatAwaitingTokens && index === virtuosoData.length - 1 ? (
                          <ChatThinkingIndicator
                            isDarkMode={isDarkMode}
                            leftAlignMessages={interfaceSettings.leftAlignMessages}
                            widescreenMode={interfaceSettings.widescreenMode}
                            startedAtMs={chatAwaitingStartedAtMs}
                            agentName={chainThinkingName}
                          />
                        ) : null}
                      </Box>
                    )}
                  />
                </Box>
              ) : null}
            </Box>
          )}
          
          {/* Подсказка о перетаскивании в области сообщений */}
          {isDragging && (
            <Box
              sx={{
                position: 'absolute',
                top: '50%',
                left: '50%',
                transform: 'translate(-50%, -50%)',
                bgcolor: 'rgba(33, 150, 243, 0.9)',
                backdropFilter: 'blur(10px)',
                color: 'white',
                p: 3,
                borderRadius: 2,
                zIndex: 1000,
                textAlign: 'center',
                boxShadow: '0 8px 32px rgba(0, 0, 0, 0.3)',
                border: '1px solid rgba(255, 255, 255, 0.2)',
              }}
            >
              <UploadIcon sx={{ fontSize: 48, mb: 2 }} />
              <Typography variant="h6">
                Отпустите файл для загрузки
              </Typography>
            </Box>
          )}
        </Box>


                 {/* Поле ввода */}
         <Box
           className="chat-input-area"
           data-theme={isDarkMode ? 'dark' : 'light'}
                       sx={{
              flexShrink: 0,
              position: 'relative',
              zIndex: workZoneAnimated ? 2 : undefined,
              borderColor: isDragging ? 'primary.main' : 'divider',
              bgcolor: isDragging ? 'action.hover' : 'transparent',
              ...(!showNewChatWelcome && {
                pt: 0,
                pb: 2,
                // Те же боковые отступы, что у chat-messages-area — иначе пилюля и карточки разъезжаются по ширине
                pl: 2,
                pr: 'calc(16px + var(--right-sidebar-rail-overlap, 0px))',
              }),
              ...(showNewChatWelcome && {
                flex: 1,
                minHeight: 0,
                display: 'flex',
                flexDirection: 'column',
              }),
            }}
           onDragOver={handleDragOver}
           onDragLeave={handleDragLeave}
           onDrop={handleDrop}
         >
          
                     {showNewChatWelcome ? (
                       <Box
                         sx={{
                           flex: 1,
                           minHeight: 0,
                           width: '100%',
                           display: 'flex',
                           flexDirection: 'column',
                           justifyContent: 'center',
                           alignItems: 'center',
                           boxSizing: 'border-box',
                           /* верхний отступ рабочей зоны (pt у fullscreen-chat) смещает математический центр вниз — чуть поднимаем блок */
                           transform: 'translateY(calc(-1 * clamp(20px, 4vh, 72px)))',
                         }}
                       >
                         <Box
                           sx={{
                             textAlign: 'center',
                             mb: 2,
                             maxWidth: interfaceSettings.widescreenMode ? '100%' : '1000px',
                             mx: 'auto',
                             px: interfaceSettings.widescreenMode ? 4 : 2,
                             width: '100%',
                           }}
                         >
                           <Typography
                             variant="h4"
                             sx={{
                               color: isDarkMode ? 'white' : '#333',
                               fontWeight: 600,
                               mb: 1,
                             }}
                           >
                             {getGreeting()}
                           </Typography>
                         </Box>
                         {renderMultiLlmModelToolbar()}
                         <ChatInputBar
                           toolsMenuAnchorRef={chatInputToolsAnchorRef}
                           value={inputMessage}
                           onChange={setInputMessage}
                           onKeyPress={handleKeyPress}
                           onPaste={(e) => handlePaste(e as React.ClipboardEvent<HTMLDivElement>)}
                           placeholder={chatMainPlaceholder}
                          inputDisabled={socketBlocksChatInput || multiLlmInputBlocked || chatAwaitingTokens}
                              inputRef={inputRef}
                              isDarkMode={isDarkMode}
                              solidWorkZoneBackground={workZoneAnimated}
                           styleVariant={interfaceSettings.chatInputStyle}
                           containerSx={{
                             mt: 0,
                             p: interfaceSettings.chatInputStyle === 'classic' ? 0 : 1.5,
                             borderRadius: interfaceSettings.chatInputStyle === 'classic' ? '28px' : '28px',
                             maxWidth: interfaceSettings.widescreenMode ? '100%' : '800px',
                             width: '100%',
                             mx: 'auto',
                             px: interfaceSettings.chatInputStyle === 'classic' ? 0 : (interfaceSettings.widescreenMode ? 4 : 2),
                           }}
                           fileInputRef={fileInputRef}
                           onAttachClick={() => fileInputRef.current?.click()}
                           onFileSelect={(files) => { if (files?.length) handleMessageAttach(files[0]); }}
                           isUploading={isUploading}
                           uploadingFile={uploadingFile}
                           attachDisabled={isUploading || multiLlmInputBlocked || chatAwaitingTokens}
                              inlineFiles={inlineAttachments}
                              onInlineFileRemove={(idx) => setInlineAttachments(prev => prev.filter((_, i) => i !== idx))}
                              onSettingsClick={handleMenuOpen}
                             settingsDisabled={multiLlmInputBlocked || chatAwaitingTokens}
                              showStopButton={currentChatLoading || hasActiveChatStreaming}
                              onStopClick={handleStopGeneration}
                              onSendClick={handleSendMessage}
                             sendDisabled={chatSendDisabled}
                              onVoiceClick={() => setShowVoiceDialog(true)}
                             voiceDisabled={multiLlmInputBlocked || chatAwaitingTokens}
                           voiceTooltip="Голосовой ввод"
                           libraryBadge={libraryInputBadge}
                           inputSuggestions={mcpInputSuggestions}
                           extraActions={multiLlmSettingsExtraAction}
                           contextCounter={chatContextCounter}
                         />
                         {renderChatInputSuggestions(
                           interfaceSettings.widescreenMode ? '100%' : '800px',
                         )}
                       </Box>
                     ) : null}

                     {/* Поле ввода снизу (есть сообщения или идёт загрузка истории) */}
           {!showNewChatWelcome && (messages.length > 0 || showChatHistoryLoadingDelayed) ? (
           <>
             {renderMultiLlmModelToolbar()}
             {/* Та же колонка, что у карточек сообщений: maxWidth 1000 + px — пилюля не шире ленты */}
             <Box
               sx={{
                 position: 'relative',
                 width: '100%',
                 maxWidth: interfaceSettings.widescreenMode ? '100%' : '1000px',
                 mx: 'auto',
                 px: interfaceSettings.widescreenMode ? 4 : 2,
                 mt: 0,
                 boxSizing: 'border-box',
               }}
             >
               {showScrollToBottomButton ? (
                 <Box
                   sx={{
                     position: 'absolute',
                     left: '50%',
                     bottom: 'calc(100% + 10px)',
                     transform: 'translateX(-50%)',
                     zIndex: 30,
                     lineHeight: 0,
                   }}
                 >
                   <IconButton
                     onClick={scrollChatToBottom}
                     aria-label="Прокрутить к концу чата"
                     title="К концу чата"
                     sx={{
                       width: 40,
                       height: 40,
                       bgcolor: isDarkMode ? 'rgba(45, 48, 55, 0.92)' : 'rgba(255, 255, 255, 0.95)',
                       color: isDarkMode ? 'rgba(255,255,255,0.88)' : 'rgba(0,0,0,0.7)',
                       border: '1px solid',
                       borderColor: isDarkMode ? 'rgba(255,255,255,0.12)' : 'rgba(0,0,0,0.12)',
                       boxShadow: isDarkMode
                         ? '0 4px 16px rgba(0,0,0,0.45)'
                         : '0 4px 16px rgba(0,0,0,0.12)',
                       backdropFilter: 'blur(8px)',
                       transform: 'none',
                       '&:hover': {
                         bgcolor: isDarkMode ? 'rgba(55, 58, 66, 0.98)' : 'rgba(245, 245, 245, 0.98)',
                         transform: 'none',
                       },
                       '&:active': {
                         transform: 'none',
                       },
                       '&.Mui-focusVisible': {
                         transform: 'none',
                       },
                     }}
                   >
                     <Box
                       sx={{
                         position: 'relative',
                         width: 24,
                         height: 24,
                         display: 'flex',
                         alignItems: 'center',
                         justifyContent: 'center',
                       }}
                     >
                       {showScrollToBottomLoader && (
                         <CircularProgress
                           size={28}
                           thickness={2.4}
                           sx={{
                             position: 'absolute',
                             color: isDarkMode ? 'rgba(255,255,255,0.55)' : 'rgba(0,0,0,0.35)',
                           }}
                         />
                       )}
                       <KeyboardArrowDownIcon sx={{ fontSize: 22 }} />
                     </Box>
                   </IconButton>
                 </Box>
               ) : null}
             <ChatInputBar
               toolsMenuAnchorRef={chatInputToolsAnchorRef}
               value={inputMessage}
               onChange={setInputMessage}
               onKeyPress={handleKeyPress}
               onPaste={(e) => handlePaste(e as React.ClipboardEvent<HTMLDivElement>)}
               placeholder={chatMainPlaceholder}
                          inputDisabled={socketBlocksChatInput || multiLlmInputBlocked || chatAwaitingTokens}
                              inputRef={inputRef}
                              isDarkMode={isDarkMode}
                              solidWorkZoneBackground={workZoneAnimated}
               styleVariant={interfaceSettings.chatInputStyle}
               containerSx={{
                 mt: 0,
                 p: interfaceSettings.chatInputStyle === 'classic' ? 0 : 1.5,
                 borderRadius: '28px',
                 maxWidth: '100%',
                 width: '100%',
                 mx: 'auto',
                 px: interfaceSettings.chatInputStyle === 'classic' ? 0 : (interfaceSettings.widescreenMode ? 4 : 2),
               }}
               fileInputRef={fileInputRef}
               onAttachClick={() => fileInputRef.current?.click()}
               onFileSelect={(files) => { if (files?.length) handleMessageAttach(files[0]); }}
               isUploading={isUploading}
               uploadingFile={uploadingFile}
                           attachDisabled={isUploading || multiLlmInputBlocked || chatAwaitingTokens}
                              inlineFiles={inlineAttachments}
                              onInlineFileRemove={(idx) => setInlineAttachments(prev => prev.filter((_, i) => i !== idx))}
                              onSettingsClick={handleMenuOpen}
                             settingsDisabled={multiLlmInputBlocked || chatAwaitingTokens}
                              showStopButton={currentChatLoading || hasActiveChatStreaming}
                              onStopClick={handleStopGeneration}
                              onSendClick={handleSendMessage}
                             sendDisabled={chatSendDisabled}
                              onVoiceClick={() => setShowVoiceDialog(true)}
                             voiceDisabled={multiLlmInputBlocked || chatAwaitingTokens}
               voiceTooltip="Голосовой ввод"
               libraryBadge={libraryInputBadge}
               inputSuggestions={mcpInputSuggestions}
               extraActions={multiLlmSettingsExtraAction}
               contextCounter={chatContextCounter}
             />
             </Box>
           </>
           ) : null}

             {/* Диалоги */}
       <VoiceChatDialog
         open={showVoiceDialog}
         onClose={() => setShowVoiceDialog(false)}
       />
       <DocumentDialog />

       {/* Инструменты: колонка как в LeChat — «Агенты» + правая панель с вкладками; БЗ и очистка в левой колонке */}
       <Popover
         open={Boolean(anchorEl)}
         anchorEl={anchorEl}
         action={gearToolsPopoverActionRef}
         onClose={handleMenuClose}
         anchorOrigin={{ vertical: CHAT_GEAR_MENU_ANCHOR_VERTICAL_OFFSET, horizontal: 'left' }}
         transformOrigin={{ vertical: 'bottom', horizontal: 'left' }}
         marginThreshold={CHAT_GEAR_MENU_MARGIN_THRESHOLD_PX}
         slotProps={{
           paper: {
             sx: {
               mt: 0,
               mb: 0,
               p: 0,
               overflowX: 'hidden',
               background: 'transparent !important',
               backgroundColor: 'transparent !important',
               boxShadow: 'none !important',
               border: 'none',
               ...(gearToolsPaperHeightPx != null
                 ? {
                     minHeight: `${gearToolsPaperHeightPx}px`,
                     maxHeight: `${gearToolsPaperHeightPx}px`,
                     height: `${gearToolsPaperHeightPx}px`,
                     overflowY:
                       gearToolsPaperHeightPx < CHAT_GEAR_MENU_PAPER_MAX_HEIGHT_PX ? 'auto' : 'hidden',
                   }
                 : { maxHeight: CHAT_GEAR_MENU_PAPER_MAX_HEIGHT, overflowY: 'auto' }),
                ...((gearToolsPanel === 'agents' || gearToolsPanel === 'artifacts' || gearToolsPanel === 'skills' || gearToolsPanel === 'model-mode' || gearToolsPanel === 'mcp' || gearToolsPanel === 'coding' || gearToolsPanel === 'generation')
                ? CHAT_GEAR_SCROLL_AREA_NO_VISIBLE_SCROLLBAR_SX
                : {}),
             },
           },
         }}
       >
         <Box
           sx={{
             display: 'flex',
             flexDirection: 'row',
             alignItems: 'stretch',
            gap: gearToolsPanel === 'agents' || gearToolsPanel === 'artifacts' || gearToolsPanel === 'skills' || gearToolsPanel === 'model-mode' || gearToolsPanel === 'mcp' || gearToolsPanel === 'coding' || gearToolsPanel === 'generation' ? `${CHAT_GEAR_MENU_PANELS_GAP_PX}px` : 0,
             width:
             (gearToolsPanel === 'agents' || gearToolsPanel === 'artifacts' || gearToolsPanel === 'skills' || gearToolsPanel === 'model-mode' || gearToolsPanel === 'mcp' || gearToolsPanel === 'coding' || gearToolsPanel === 'generation') && gearToolsMenuWidthPx != null
                 ? `${gearToolsMenuWidthPx}px`
                : gearToolsPanel === 'agents' || gearToolsPanel === 'artifacts' || gearToolsPanel === 'skills' || gearToolsPanel === 'model-mode' || gearToolsPanel === 'mcp' || gearToolsPanel === 'coding' || gearToolsPanel === 'generation'
                   ? CHAT_GEAR_MENU_EXPANDED_WIDTH_PX
                   : CHAT_GEAR_MENU_PANEL_WIDTH_PX,
             maxWidth:
             (gearToolsPanel === 'agents' || gearToolsPanel === 'artifacts' || gearToolsPanel === 'skills' || gearToolsPanel === 'model-mode' || gearToolsPanel === 'mcp' || gearToolsPanel === 'coding' || gearToolsPanel === 'generation') && gearToolsMenuWidthPx != null
                 ? `${gearToolsMenuWidthPx}px`
                 : 'min(96vw, 580px)',
             minHeight: gearToolsPaperHeightPx != null ? `${gearToolsPaperHeightPx}px` : undefined,
             height: gearToolsPaperHeightPx != null ? `${gearToolsPaperHeightPx}px` : undefined,
             maxHeight: gearToolsPaperHeightPx != null ? `${gearToolsPaperHeightPx}px` : 'inherit',
             boxSizing: 'border-box',
             overflow: 'hidden',
           }}
         >
           <Box
             sx={{
               ...dropdownPanelSx,
               width:
               gearToolsPanel === 'agents' || gearToolsPanel === 'artifacts' || gearToolsPanel === 'skills' || gearToolsPanel === 'model-mode' || gearToolsPanel === 'mcp' || gearToolsPanel === 'coding' || gearToolsPanel === 'generation'
                  ? CHAT_GEAR_MENU_LEFT_RAIL_WIDTH_PX
                  : '100%',
               flexShrink: 0,
               boxSizing: 'border-box',
               py: 0.5,
               px: 0.5,
               display: 'flex',
               flexDirection: 'column',
               justifyContent: 'flex-start',
               alignSelf: 'stretch',
               minHeight: 0,
               height: '100%',
             }}
           >
             <Box
               onClick={() => setGearToolsPanel((p) => (p === 'agents' ? 'main' : 'agents'))}
               sx={{
                 ...dropdownItemSx,
                 display: 'flex',
                 alignItems: 'center',
                 gap: 1,
                 color: isDarkMode ? 'white' : '#333',
                 bgcolor:
                   gearToolsPanel === 'agents'
                     ? isDarkMode
                       ? DROPDOWN_ITEM_HOVER_BG_DARK
                       : DROPDOWN_ITEM_HOVER_BG_LIGHT
                     : 'transparent',
               }}
             >
               <GearMenuAgentsIcon
                 sx={{ fontSize: 18, color: isDarkMode ? 'rgba(255,255,255,0.7)' : 'rgba(0,0,0,0.6)', flexShrink: 0 }}
               />
               <Typography sx={{ flex: 1, minWidth: 0, fontSize: MENU_ACTION_TEXT_SIZE, whiteSpace: 'nowrap' }}>
                 Агенты
               </Typography>
               <ChevronRightIcon
                 sx={{
                   ...DROPDOWN_CHEVRON_SX,
                   flexShrink: 0,
                   transform: gearToolsPanel === 'agents' ? 'rotate(90deg)' : 'none',
                 }}
               />
             </Box>
             <Box
               onClick={() => setGearToolsPanel((p) => (p === 'artifacts' ? 'main' : 'artifacts'))}
               sx={{
                 ...dropdownItemSx,
                 display: 'flex',
                 alignItems: 'center',
                 gap: 1,
                 color: isDarkMode ? 'white' : '#333',
                 bgcolor:
                   gearToolsPanel === 'artifacts'
                     ? isDarkMode
                       ? DROPDOWN_ITEM_HOVER_BG_DARK
                       : DROPDOWN_ITEM_HOVER_BG_LIGHT
                     : 'transparent',
               }}
             >
               <GearMenuArtifactsIcon
                 sx={{ fontSize: 18, color: isDarkMode ? 'rgba(255,255,255,0.7)' : 'rgba(0,0,0,0.6)', flexShrink: 0 }}
               />
               <Typography sx={{ flex: 1, minWidth: 0, fontSize: MENU_ACTION_TEXT_SIZE, whiteSpace: 'nowrap' }}>
                 Артефакты
               </Typography>
               <ChevronRightIcon
                 sx={{
                   ...DROPDOWN_CHEVRON_SX,
                   flexShrink: 0,
                   transform: gearToolsPanel === 'artifacts' ? 'rotate(90deg)' : 'none',
                 }}
               />
             </Box>
                        <Box
              onClick={() => setGearToolsPanel((p) => (p === 'skills' ? 'main' : 'skills'))}
              sx={{
                ...dropdownItemSx,
                display: 'flex',
                alignItems: 'center',
                gap: 1,
                color: isDarkMode ? 'white' : '#333',
                bgcolor:
                  gearToolsPanel === 'skills'
                    ? isDarkMode
                      ? DROPDOWN_ITEM_HOVER_BG_DARK
                      : DROPDOWN_ITEM_HOVER_BG_LIGHT
                    : 'transparent',
              }}
            >
              <SkillsNavIcon
                sx={{ fontSize: 18, color: isDarkMode ? 'rgba(255,255,255,0.7)' : 'rgba(0,0,0,0.6)', flexShrink: 0 }}
              />
              <Typography sx={{ flex: 1, minWidth: 0, fontSize: MENU_ACTION_TEXT_SIZE, whiteSpace: 'nowrap' }}>
                Skills
              </Typography>
              <ChevronRightIcon
                sx={{
                  ...DROPDOWN_CHEVRON_SX,
                  flexShrink: 0,
                  transform: gearToolsPanel === 'skills' ? 'rotate(90deg)' : 'none',
                }}
              />
            </Box>
<Box
              onClick={() => setGearToolsPanel((p) => (p === 'mcp' ? 'main' : 'mcp'))}
              sx={{
                ...dropdownItemSx,
                display: 'flex',
                alignItems: 'center',
                gap: 1,
                color: isDarkMode ? 'white' : '#333',
                bgcolor:
                  gearToolsPanel === 'mcp'
                    ? isDarkMode
                      ? DROPDOWN_ITEM_HOVER_BG_DARK
                      : DROPDOWN_ITEM_HOVER_BG_LIGHT
                    : 'transparent',
              }}
            >
              <GearMenuMcpIcon
                sx={{ fontSize: 18, color: isDarkMode ? 'rgba(255,255,255,0.7)' : 'rgba(0,0,0,0.6)', flexShrink: 0 }}
              />
              <Typography sx={{ flex: 1, minWidth: 0, fontSize: MENU_ACTION_TEXT_SIZE, whiteSpace: 'nowrap' }}>
                MCP
              </Typography>
              <ChevronRightIcon
                sx={{
                  ...DROPDOWN_CHEVRON_SX,
                  flexShrink: 0,
                  transform: gearToolsPanel === 'mcp' ? 'rotate(90deg)' : 'none',
                }}
              />
            </Box>
            <Box
              onClick={() => {
                setGearToolsPanel((p) => (p === 'coding' ? 'main' : 'coding'));
              }}
              sx={{
                ...dropdownItemSx,
                display: 'flex',
                alignItems: 'center',
                gap: 1,
                color: isDarkMode ? 'white' : '#333',
                bgcolor:
                  gearToolsPanel === 'coding'
                    ? isDarkMode
                      ? DROPDOWN_ITEM_HOVER_BG_DARK
                      : DROPDOWN_ITEM_HOVER_BG_LIGHT
                    : 'transparent',
              }}
            >
              <GearMenuCodingIcon
                sx={{ fontSize: 18, color: isDarkMode ? 'rgba(255,255,255,0.7)' : 'rgba(0,0,0,0.6)', flexShrink: 0 }}
              />
              <Typography sx={{ flex: 1, minWidth: 0, fontSize: MENU_ACTION_TEXT_SIZE, whiteSpace: 'nowrap' }}>
                Coding
              </Typography>
              <ChevronRightIcon
                sx={{
                  ...DROPDOWN_CHEVRON_SX,
                  flexShrink: 0,
                  transform: gearToolsPanel === 'coding' ? 'rotate(90deg)' : 'none',
                }}
              />
            </Box>
            <Box
              onClick={() => setGearToolsPanel((p) => (p === 'generation' ? 'main' : 'generation'))}
              sx={{
                ...dropdownItemSx,
                display: 'flex',
                alignItems: 'center',
                gap: 1,
                color: isDarkMode ? 'white' : '#333',
                bgcolor:
                  gearToolsPanel === 'generation' || generationModeOn
                    ? isDarkMode
                      ? DROPDOWN_ITEM_HOVER_BG_DARK
                      : DROPDOWN_ITEM_HOVER_BG_LIGHT
                    : 'transparent',
              }}
            >
              <GearMenuImageGenIcon
                sx={{ fontSize: 18, color: isDarkMode ? 'rgba(255,255,255,0.7)' : 'rgba(0,0,0,0.6)', flexShrink: 0 }}
              />
              <Typography sx={{ flex: 1, minWidth: 0, fontSize: MENU_ACTION_TEXT_SIZE, whiteSpace: 'nowrap' }}>
                Режим генерации
              </Typography>
              <ChevronRightIcon
                sx={{
                  ...DROPDOWN_CHEVRON_SX,
                  flexShrink: 0,
                  transform: gearToolsPanel === 'generation' ? 'rotate(90deg)' : 'none',
                }}
              />
            </Box>
            <Box
              onClick={() => setGearToolsPanel((p) => (p === 'model-mode' ? 'main' : 'model-mode'))}
              sx={{
                ...dropdownItemSx,
                display: 'flex',
                alignItems: 'center',
                gap: 1,
                color: isDarkMode ? 'white' : '#333',
                bgcolor:
                  gearToolsPanel === 'model-mode'
                    ? isDarkMode
                      ? DROPDOWN_ITEM_HOVER_BG_DARK
                      : DROPDOWN_ITEM_HOVER_BG_LIGHT
                    : 'transparent',
              }}
            >
              <ThinkingModeIcon
                sx={{ fontSize: 18, color: isDarkMode ? 'rgba(255,255,255,0.7)' : 'rgba(0,0,0,0.6)', flexShrink: 0 }}
              />
              <Typography sx={{ flex: 1, minWidth: 0, fontSize: MENU_ACTION_TEXT_SIZE, whiteSpace: 'nowrap' }}>
                Режим модели
              </Typography>
              <ChevronRightIcon
                sx={{
                  ...DROPDOWN_CHEVRON_SX,
                  flexShrink: 0,
                  transform: gearToolsPanel === 'model-mode' ? 'rotate(90deg)' : 'none',
                }}
              />
            </Box>
             <Tooltip
               title={libraryDisabled ? libraryDisabledHint : ''}
               disableHoverListener={!libraryDisabled}
             >
               <span style={{ display: 'block' }}>
                 <Box
                   onClick={() => {
                     if (libraryDisabled) return;
                     toggleKbRag();
                     handleMenuClose();
                   }}
                   sx={{
                     ...dropdownItemSx,
                     display: 'flex',
                     alignItems: 'center',
                     gap: 1,
                     color: isDarkMode ? 'white' : '#333',
                     opacity: libraryDisabled ? 0.5 : 1,
                     cursor: libraryDisabled ? 'not-allowed' : 'pointer',
                   }}
                 >
                   <KbIcon sx={{ fontSize: 18, color: isDarkMode ? 'rgba(255,255,255,0.7)' : 'rgba(0,0,0,0.6)', flexShrink: 0 }} />
                   <Typography sx={{ flex: 1, minWidth: 0, fontSize: MENU_ACTION_TEXT_SIZE, whiteSpace: 'nowrap' }}>
                     {useKbRag ? 'Отключить общий RAG' : 'Общий RAG'}
                   </Typography>
                   {useKbRag ? (
                     <CheckIcon sx={{ fontSize: 16, color: 'primary.main', flexShrink: 0 }} />
                   ) : null}
                 </Box>
               </span>
             </Tooltip>
           </Box>
          {gearToolsPanel === 'agents' || gearToolsPanel === 'artifacts' || gearToolsPanel === 'skills' || gearToolsPanel === 'model-mode' || gearToolsPanel === 'mcp' || gearToolsPanel === 'coding' || gearToolsPanel === 'generation' ? (
             <Box
               sx={{
                 ...dropdownPanelSx,
                 flex: 1,
                 minWidth: `${CHAT_GEAR_MENU_AGENTS_RIGHT_MIN_PX}px`,
                 minHeight: 0,
                 height: '100%',
                 display: 'flex',
                 flexDirection: 'column',
                 overflow: 'hidden',
               }}
             >
              {gearToolsPanel === 'agents' ? (
                <ChatGearAgentsPanel isDarkMode={isDarkMode} />
              ) : gearToolsPanel === 'artifacts' ? (
                <ChatGearArtifactsPanel isDarkMode={isDarkMode} chatId={currentChat?.id} />
              ) : gearToolsPanel === 'skills' ? (
                <ChatGearSkillsPanel isDarkMode={isDarkMode} chatId={currentChat?.id} />
              ) : gearToolsPanel === 'mcp' ? (
                <ChatGearMcpPanel isDarkMode={isDarkMode} chatId={currentChat?.id} />
              ) : gearToolsPanel === 'coding' ? (
                <ChatGearCodingPanel
                  isDarkMode={isDarkMode}
                  chatId={currentChat?.id}
                  projectId={currentChat?.projectId}
                />
              ) : gearToolsPanel === 'generation' ? (
                <ChatGearGenerationPanel isDarkMode={isDarkMode} chatId={currentChat?.id} />
              ) : (
                <Box sx={{ p: 1, display: 'flex', flexDirection: 'column', gap: 0.5, overflowY: 'auto' }}>
                  {([
                    { id: 'auto', label: 'Автоматический', icon: <AutoModeIcon sx={{ fontSize: 16 }} /> },
                    { id: 'thinking', label: 'Мышление', icon: <ThinkingModeIcon sx={{ fontSize: 16 }} /> },
                    { id: 'fast', label: 'Быстрый', icon: <FastModeIcon sx={{ fontSize: 16 }} /> },
                  ] as const).map((mode) => (
                    <Box
                      key={mode.id}
                      onClick={() => {
                        setModelThinkingMode(mode.id);
                        showNotification('info', `Режим модели: ${mode.label}`);
                      }}
                      sx={{
                        ...dropdownItemSx,
                        display: 'flex',
                        alignItems: 'center',
                        gap: 1,
                        color: isDarkMode ? 'white' : '#333',
                        bgcolor:
                          modelThinkingMode === mode.id
                            ? isDarkMode
                              ? DROPDOWN_ITEM_HOVER_BG_DARK
                              : DROPDOWN_ITEM_HOVER_BG_LIGHT
                            : 'transparent',
                      }}
                    >
                      <Box sx={{ display: 'inline-flex', opacity: 0.9 }}>{mode.icon}</Box>
                      <Typography sx={{ flex: 1, minWidth: 0, fontSize: MENU_ACTION_TEXT_SIZE }}>
                        {mode.label}
                      </Typography>
                      {modelThinkingMode === mode.id ? (
                        <CheckIcon sx={{ fontSize: 16, color: 'primary.main', flexShrink: 0 }} />
                      ) : null}
                    </Box>
                  ))}
                </Box>
              )}
             </Box>
           ) : null}
         </Box>
       </Popover>

       {/* Диалог редактирования сообщения */}
       <Dialog
         open={editDialogOpen}
         onClose={handleCancelEdit}
         maxWidth="md"
         fullWidth
         PaperProps={{
           sx: {
             bgcolor: 'background.paper',
             borderRadius: 2,
           }
         }}
       >
        <DialogTitle>
          {editingMultiLlmSlotIndex !== null && editingMessage?.multiLLMResponses?.[editingMultiLlmSlotIndex]
            ? `Редактировать ответ (${editingMessage.multiLLMResponses[editingMultiLlmSlotIndex]!.model})`
            : 'Редактировать сообщение'}
        </DialogTitle>
        <DialogContent>
          <TextField
            autoFocus
            margin="dense"
            label="Текст сообщения"
            fullWidth
            multiline
            rows={6}
            variant="outlined"
            value={editText}
            onChange={(e) => setEditText(e.target.value)}
            sx={{ mt: 1 }}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={handleCancelEdit}>
            Отменить
          </Button>
          {editingMessage?.role === 'user' ? (
            // Кнопки для сообщений пользователя
            <>
              <Button onClick={handleSaveEdit} variant="outlined" color="primary">
                Сохранить
              </Button>
              <Button onClick={handleSaveAndSend} variant="contained" color="primary">
                Сохранить и отправить
              </Button>
            </>
          ) : (
            // Кнопки для сообщений LLM
            <Button onClick={handleSaveEdit} variant="contained" color="primary">
              Сохранить
            </Button>
          )}
        </DialogActions>
       </Dialog>

      </Box>

      {/* Нижняя панель в режиме "Поделиться" */}
      {shareMode && (
        <Paper
          sx={{
            position: 'fixed',
            bottom: 0,
            left: sidebarHidden ? 0 : sidebarOpen ? 240 : 64,
            right: 0,
            zIndex: 1200,
            borderRadius: 0,
            boxShadow: '0 -4px 12px rgba(0, 0, 0, 0.1)',
            backgroundColor: isDarkMode ? '#2d2d2d' : '#ffffff',
            transition: 'left 0.3s ease',
          }}
        >
          <Box
            sx={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              px: 3,
              py: 2,
            }}
          >
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
              <FormControlLabel
                control={
                  <Checkbox
                    checked={selectedMessages.size > 0 && (() => {
                      let totalPairs = 0;
                      for (let i = 0; i < messages.length - 1; i++) {
                        if (messages[i].role === 'user' && messages[i + 1].role === 'assistant') {
                          totalPairs++;
                        }
                      }
                      return selectedMessages.size === totalPairs * 2;
                    })()}
                    onChange={handleSelectAll}
                  />
                }
                label="Выбрать все"
              />
              <Typography variant="body2" color="text.secondary">
                Выбрано пар: {selectedMessages.size / 2}
              </Typography>
            </Box>

            <Box sx={{ display: 'flex', gap: 1 }}>
              <Button
                variant="outlined"
                onClick={handleExitShareMode}
                disabled={isCreatingShareLink}
              >
                Отмена
              </Button>
              <Button
                variant="contained"
                onClick={handleCreateShareLink}
                disabled={selectedMessages.size === 0}
              >
                Создать публичную ссылку
              </Button>
            </Box>
          </Box>
        </Paper>
      )}

      {/* Навигационная панель для сообщений (панель с диалогами) */}
      {messages.length > 0 && showDialoguesPanel && (
        <MessageNavigationBar
          messages={messages}
          isDarkMode={isDarkMode}
          onNavigate={scrollToMessage}
        />
      )}

      {/* Диалог подтверждения создания публичной ссылки */}
      <ShareConfirmDialog
        open={shareDialogOpen}
        onClose={handleCloseShareDialog}
        onConfirm={createShareLinkConfirmed}
        isDarkMode={isDarkMode}
        selectedCount={selectedMessages.size}
      />
      </Box>
    </Box>
  );
}